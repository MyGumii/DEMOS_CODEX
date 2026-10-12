"""Route-unregistered NP-2C legacy rename source adapter.

The adapter consumes one caller-owned, exact file-backed SQLite connection.
It authenticates the frozen legacy-v3 schema, performs no path discovery, and
never opens or migrates a source database.  A later offline orchestrator owns
sentinel/path/quiescence policy; request/startup paths must not import this
module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable
import uuid

from code_assist_v1.notion_legacy_change_consumer_contract import (
    CONSUMER_NOW_UDF,
    CONSUMER_WRITE_CONTEXT_UDF,
    MAX_TIMESTAMP,
    inspect_legacy_change_consumer_contract,
)
from code_assist_v1.notion_legacy_changes import (
    legacy_incarnation_receipt_sha256,
    sqlite_source_identity,
)
from code_assist_v1.notion_legacy_page_lifecycle_contract import (
    LegacyPageRenameContractError,
    LegacyPageRenamePrestate,
    LegacyPageRenameReceipt,
    RENAME_ACTION,
    SOURCE_CONSUMER,
    build_rename_receipt,
    derive_rename_source_operation_id,
    legacy_page_rename_request_sha256,
    normalize_legacy_page_title,
    validate_rename_prestate,
    validate_rename_receipt,
)


MAX_PERMISSION_HOPS = 256
MAX_PAGE_VERSION_BLOCKS = 10_000
MAX_PAGE_VERSION_BYTES = 16 * 1024 * 1024

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")
_EDIT_ROLES = frozenset({"owner", "edit"})


class LegacyPageRenameOperationConflict(Exception):
    """The deterministic source operation ID was reused for another request."""


class LegacyPageRenameSourceConflict(Exception):
    """The exact source prestate, permission, or generation is stale."""


class LegacyPageRenameBoundsExceeded(Exception):
    """A source page cannot be versioned inside the frozen adapter bounds."""


class LegacyPageRenameManualAuditRequired(Exception):
    """The adapter cannot prove the durable commit outcome."""


FaultHook = Callable[[str], None]


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError) as exc:
        raise LegacyPageRenameContractError("legacy page rename evidence is invalid") from exc


def _name(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _NAME_RE.fullmatch(value) is None:
        raise ValueError(f"valid {field} required")
    return value


def _page_id(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 160:
        raise ValueError("valid page_id required")
    return value


def _is_link(path: Path) -> bool:
    junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or bool(junction())


def _utf8_len(value: object) -> int:
    try:
        return len(str(value or "").encode("utf-8"))
    except UnicodeError as exc:
        raise LegacyPageRenameContractError("legacy page snapshot text is invalid") from exc


class LegacyPageRenameSourceAdapter:
    """One authenticated v3 source connection and one exact owner shard."""

    def __init__(
        self,
        con: sqlite3.Connection,
        *,
        source_owner_id: str,
        expected_source_db_path: str | os.PathLike[str],
        trusted_clock: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(con, sqlite3.Connection) or con.in_transaction:
            raise ValueError("idle SQLite connection required")
        self._con = con
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA foreign_keys=ON")
        if int(self._con.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise LegacyPageRenameContractError("legacy source foreign keys are unavailable")
        self._owner = _name(source_owner_id, field="source_owner_id")
        raw_path = Path(expected_source_db_path)
        if not raw_path.is_absolute() or _is_link(raw_path):
            raise LegacyPageRenameContractError("exact source database path required")
        try:
            self._path = raw_path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise LegacyPageRenameContractError("legacy rename source is unavailable") from exc
        if not self._path.is_file():
            raise LegacyPageRenameContractError("exact source database path required")
        self._clock = trusted_clock or (lambda: int(time.time()))
        self._write_context = 0
        self._now_value = 0
        self._identity = self._connection_identity()
        if str(self._con.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal":
            raise LegacyPageRenameContractError("legacy rename source must be checkpointed")
        if any(Path(str(self._path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise LegacyPageRenameContractError("legacy rename source sidecar is present")
        inspected = inspect_legacy_change_consumer_contract(
            self._con, verify_integrity=True
        )
        if not inspected.get("installed"):
            raise LegacyPageRenameContractError("legacy source v3 migration is required")
        self._con.create_function(
            CONSUMER_WRITE_CONTEXT_UDF,
            0,
            lambda: self._write_context,
            deterministic=False,
        )
        self._con.create_function(
            CONSUMER_NOW_UDF,
            0,
            lambda: self._now_value,
            deterministic=False,
        )

    def _connection_identity(self) -> tuple[int, int, int]:
        try:
            rows = self._con.execute("PRAGMA database_list").fetchall()
            main = next(row for row in rows if str(row[1]) == "main")
            connected = Path(str(main[2])).resolve(strict=True)
            if connected != self._path:
                raise LegacyPageRenameContractError("exact source database path required")
            stat = self._path.stat()
            if stat.st_nlink != 1:
                raise LegacyPageRenameContractError("legacy rename source must have one link")
            return (
                sqlite_source_identity(int(stat.st_dev)),
                sqlite_source_identity(int(stat.st_ino)),
                int(stat.st_nlink),
            )
        except LegacyPageRenameContractError:
            raise
        except (OSError, StopIteration, TypeError, ValueError) as exc:
            raise LegacyPageRenameContractError("legacy rename source is unavailable") from exc

    def _assert_identity(self) -> tuple[int, int, int]:
        current = self._connection_identity()
        if current != self._identity:
            raise LegacyPageRenameSourceConflict("legacy rename source identity changed")
        return current

    def _now(self) -> int:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_TIMESTAMP:
            raise LegacyPageRenameContractError("trusted source clock is invalid")
        return value

    def _read_current(
        self, *, page_id: str
    ) -> tuple[LegacyPageRenamePrestate, dict[str, Any]]:
        identity = self._assert_identity()
        page = self._con.execute(
            "SELECT * FROM pages WHERE id=? AND archived=0", (page_id,)
        ).fetchone()
        if not page or str(page["owner_user_id"]) != self._owner:
            raise LegacyPageRenameSourceConflict("legacy rename source prestate changed")
        page_data = dict(page)
        revision = page_data.get("revision")
        updated_at = page_data.get("updated_at")
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
            or isinstance(updated_at, bool)
            or not isinstance(updated_at, int)
            or updated_at < 0
        ):
            raise LegacyPageRenameContractError("atomic source revision capability is required")
        state = self._con.execute(
            "SELECT source_epoch,full_sweep_generation "
            "FROM notion_legacy_change_state WHERE singleton=1"
        ).fetchone()
        watermark = self._con.execute(
            "SELECT change_generation,change_mask,source_exists "
            "FROM notion_page_change_watermarks WHERE page_id=?",
            (page_id,),
        ).fetchone()
        incarnation = self._con.execute(
            "SELECT incarnation_generation,incarnation_id,retired_at "
            "FROM notion_page_incarnation_history WHERE page_id=? "
            "ORDER BY incarnation_generation DESC LIMIT 1",
            (page_id,),
        ).fetchone()
        if not state or not watermark or not incarnation or incarnation["retired_at"] is not None:
            raise LegacyPageRenameSourceConflict("legacy rename source prestate changed")
        if watermark["source_exists"] != 1:
            raise LegacyPageRenameSourceConflict("legacy rename source prestate changed")
        raw = {
            "source_device": identity[0],
            "source_inode": identity[1],
            "source_link_count": identity[2],
            "source_epoch": state["source_epoch"],
            "full_sweep_generation": state["full_sweep_generation"],
            "source_owner_subject_id": self._owner,
            "source_page_id": page_id,
            "incarnation_id": incarnation["incarnation_id"],
            "incarnation_generation": incarnation["incarnation_generation"],
            "revision": revision,
            "updated_at": updated_at,
            "change_generation": watermark["change_generation"],
            "change_mask": watermark["change_mask"],
            "source_exists": True,
            "source_active": True,
        }
        raw["incarnation_receipt_sha256"] = legacy_incarnation_receipt_sha256(
            source_owner_id=self._owner,
            page_id=page_id,
            incarnation_id=str(raw["incarnation_id"]),
            incarnation_generation=int(raw["incarnation_generation"]),
            incarnation_retired=False,
            change_generation=int(raw["change_generation"]),
            change_mask=int(raw["change_mask"]),
            source_exists=True,
            source_active=True,
            source_epoch=int(raw["source_epoch"]),
            full_sweep_generation=int(raw["full_sweep_generation"]),
            source_device=identity[0],
            source_inode=identity[1],
            source_link_count=identity[2],
        )
        return validate_rename_prestate(raw), page_data

    def capture_prestate(
        self, *, page_id: str, source_quiesced_confirmed: bool
    ) -> LegacyPageRenamePrestate:
        if source_quiesced_confirmed is not True:
            raise ValueError("legacy page rename requires source quiescence")
        exact_page_id = _page_id(page_id)
        if self._con.in_transaction:
            raise ValueError("idle SQLite connection required")
        inspect_legacy_change_consumer_contract(self._con, verify_integrity=False)
        self._con.execute("BEGIN")
        try:
            prestate, _page = self._read_current(page_id=exact_page_id)
            self._con.rollback()
            return prestate
        except Exception:
            self._con.rollback()
            raise

    def _effective_edit_role(self, *, actor: str, page_id: str) -> str:
        if actor == self._owner:
            return "owner"
        current = page_id
        visited: set[str] = set()
        for _hop in range(MAX_PERMISSION_HOPS):
            if not current or current in visited:
                break
            visited.add(current)
            row = self._con.execute(
                "SELECT p.parent_id,s.role FROM pages p "
                "LEFT JOIN page_shares s ON s.page_id=p.id "
                "AND s.shared_user_id=? AND s.revoked_at IS NULL "
                "WHERE p.id=? AND p.archived=0",
                (actor, current),
            ).fetchone()
            if not row:
                break
            role = str(row["role"] or "")
            if role:
                if role not in _EDIT_ROLES:
                    raise PermissionError("edit permission required")
                return role
            current = str(row["parent_id"] or "")
        if current and current not in visited:
            raise LegacyPageRenameBoundsExceeded("legacy page ancestry exceeds the adapter bound")
        raise PermissionError("edit permission required")

    @staticmethod
    def _settings(value: object) -> dict[str, Any]:
        try:
            parsed = json.loads(str(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LegacyPageRenameContractError("legacy page settings are invalid") from exc
        if not isinstance(parsed, dict):
            raise LegacyPageRenameContractError("legacy page settings are invalid")
        return parsed

    def _assert_atomic_revision_capability(self) -> None:
        pages = {
            str(row[1])
            for row in self._con.execute("PRAGMA table_info(pages)").fetchall()
        }
        versions = {
            str(row[1])
            for row in self._con.execute("PRAGMA table_info(page_versions)").fetchall()
        }
        required_versions = {
            "id",
            "page_id",
            "owner_user_id",
            "title",
            "icon",
            "cover",
            "settings_json",
            "blocks_json",
            "created_at",
        }
        if "revision" not in pages or not required_versions.issubset(versions):
            raise LegacyPageRenameContractError(
                "atomic source revision capability is required"
            )

    def _bounded_version_blocks(self, page: dict[str, Any]) -> str:
        rows = self._con.execute(
            "SELECT id,block_type,content,props_json,position "
            "FROM blocks WHERE page_id=? ORDER BY position LIMIT ?",
            (str(page["id"]), MAX_PAGE_VERSION_BLOCKS + 1),
        ).fetchall()
        if len(rows) > MAX_PAGE_VERSION_BLOCKS:
            raise LegacyPageRenameBoundsExceeded("legacy page version block bound exceeded")
        byte_count = sum(
            _utf8_len(page.get(field))
            for field in ("title", "icon", "cover", "settings_json")
        )
        blocks: list[dict[str, Any]] = []
        for row in rows:
            values = dict(row)
            byte_count += sum(
                _utf8_len(values.get(field))
                for field in ("id", "block_type", "content", "props_json")
            )
            if byte_count > MAX_PAGE_VERSION_BYTES:
                raise LegacyPageRenameBoundsExceeded("legacy page version byte bound exceeded")
            try:
                props = json.loads(str(values.get("props_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                props = {}
            if not isinstance(props, dict):
                props = {}
            position = values.get("position")
            if isinstance(position, bool) or not isinstance(position, int):
                raise LegacyPageRenameContractError("legacy page blocks are invalid")
            blocks.append(
                {
                    "id": str(values.get("id") or ""),
                    "type": str(values.get("block_type") or "paragraph"),
                    "content": str(values.get("content") or ""),
                    "props": props,
                    "position": position,
                }
            )
        try:
            blocks_json = json.dumps(blocks, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError, UnicodeError) as exc:
            raise LegacyPageRenameContractError("legacy page blocks are invalid") from exc
        if _utf8_len(blocks_json) > MAX_PAGE_VERSION_BYTES:
            raise LegacyPageRenameBoundsExceeded("legacy page version byte bound exceeded")
        previous_size = self._con.execute(
            "SELECT length(CAST(blocks_json AS BLOB)) FROM page_versions WHERE page_id=? "
            "ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (str(page["id"]),),
        ).fetchone()
        if previous_size and int(previous_size[0] or 0) > MAX_PAGE_VERSION_BYTES:
            raise LegacyPageRenameBoundsExceeded("legacy page version history bound exceeded")
        return blocks_json

    def _record_page_version(
        self, *, page: dict[str, Any], blocks_json: str, now: int
    ) -> None:
        signature = (
            str(page.get("title") or "Untitled"),
            str(page.get("icon") or ""),
            str(page.get("cover") or ""),
            str(page.get("settings_json") or "{}"),
            blocks_json,
        )
        previous = self._con.execute(
            "SELECT title,icon,cover,settings_json,blocks_json FROM page_versions "
            "WHERE page_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (str(page["id"]),),
        ).fetchone()
        if previous and signature == tuple(
            str(previous[key] or "")
            for key in ("title", "icon", "cover", "settings_json", "blocks_json")
        ):
            raise LegacyPageRenameContractError("legacy page version snapshot did not advance")
        self._con.execute(
            "INSERT INTO page_versions(id,page_id,owner_user_id,title,icon,cover,"
            "settings_json,blocks_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "v_" + uuid.uuid4().hex,
                str(page["id"]),
                self._owner,
                *signature,
                now,
            ),
        )
        stale = self._con.execute(
            "SELECT id FROM page_versions WHERE page_id=? "
            "ORDER BY created_at DESC,rowid DESC LIMIT -1 OFFSET 100",
            (str(page["id"]),),
        ).fetchall()
        if stale:
            self._con.executemany(
                "DELETE FROM page_versions WHERE id=?", [(row["id"],) for row in stale]
            )

    def _replay(
        self,
        *,
        source_operation_id: str,
        source_request_sha256: str,
        expected_prestate: LegacyPageRenamePrestate,
    ) -> LegacyPageRenameReceipt | None:
        row = self._con.execute(
            "SELECT action,request_sha256,result_json "
            "FROM notion_legacy_change_consumer_operations "
            "WHERE consumer=? AND operation_id=?",
            (SOURCE_CONSUMER, source_operation_id),
        ).fetchone()
        if not row:
            return None
        if str(row["action"]) != RENAME_ACTION or str(row["request_sha256"]) != source_request_sha256:
            raise LegacyPageRenameOperationConflict(source_operation_id)
        try:
            result = json.loads(str(row["result_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LegacyPageRenameContractError("legacy page rename ledger is invalid") from exc
        if str(row["result_json"]) != _canonical_json(result):
            raise LegacyPageRenameContractError("legacy page rename ledger is invalid")
        return validate_rename_receipt(
            result,
            expected_request_sha256=source_request_sha256,
            expected_prestate=expected_prestate,
        )

    def rename(
        self,
        *,
        mutation_actor_subject_id: str,
        requested_title: object,
        catalog_command_id: str,
        caller_operation_id: str,
        expected_prestate: LegacyPageRenamePrestate,
        source_quiesced_confirmed: bool,
        fault_hook: FaultHook | None = None,
    ) -> LegacyPageRenameReceipt:
        actor = _name(mutation_actor_subject_id, field="mutation_actor_subject_id")
        expected = validate_rename_prestate(expected_prestate)
        if expected.source_owner_subject_id != self._owner:
            raise LegacyPageRenameSourceConflict("legacy rename source prestate changed")
        normalized_title = normalize_legacy_page_title(requested_title)
        source_operation_id = derive_rename_source_operation_id(
            catalog_command_id=catalog_command_id,
            caller_operation_id=caller_operation_id,
        )
        request_sha256 = legacy_page_rename_request_sha256(
            mutation_actor_subject_id=actor,
            normalized_title=normalized_title,
            catalog_command_id=catalog_command_id,
            caller_operation_id=caller_operation_id,
            expected_prestate=expected,
        )
        if self._con.in_transaction:
            raise ValueError("idle SQLite connection required")

        # Response-loss replay is intentionally the first source query made by
        # this command.  It never reads the page, permissions, blocks, or title.
        replay = self._replay(
            source_operation_id=source_operation_id,
            source_request_sha256=request_sha256,
            expected_prestate=expected,
        )
        if replay is not None:
            return replay
        if source_quiesced_confirmed is not True:
            raise ValueError("legacy page rename requires source quiescence")
        inspect_legacy_change_consumer_contract(self._con, verify_integrity=False)
        now = self._now()
        self._now_value = now
        self._con.execute("BEGIN IMMEDIATE")
        try:
            replay = self._replay(
                source_operation_id=source_operation_id,
                source_request_sha256=request_sha256,
                expected_prestate=expected,
            )
            if replay is not None:
                self._con.rollback()
                return replay
            self._assert_atomic_revision_capability()
            current, page = self._read_current(page_id=expected.source_page_id)
            if current != expected:
                raise LegacyPageRenameSourceConflict("legacy rename source prestate changed")
            self._effective_edit_role(actor=actor, page_id=expected.source_page_id)
            settings = self._settings(page.get("settings_json"))
            if settings.get("locked"):
                raise PermissionError("page is locked")
            if now < expected.updated_at:
                raise LegacyPageRenameContractError("trusted source clock moved backwards")
            changed = normalized_title != str(page.get("title") or "Untitled")
            if changed:
                blocks_json = self._bounded_version_blocks(page)
                if fault_hook is not None:
                    fault_hook("before_page_update")
                updated = self._con.execute(
                    "UPDATE pages SET title=?,updated_at=?,revision=revision+1 "
                    "WHERE id=? AND archived=0 AND revision=?",
                    (normalized_title, now, expected.source_page_id, expected.revision),
                )
                if updated.rowcount != 1:
                    raise LegacyPageRenameSourceConflict("legacy rename source prestate changed")
                if fault_hook is not None:
                    fault_hook("after_page_update")
                changed_page = dict(
                    self._con.execute(
                        "SELECT * FROM pages WHERE id=?", (expected.source_page_id,)
                    ).fetchone()
                )
                self._record_page_version(page=changed_page, blocks_json=blocks_json, now=now)
                if fault_hook is not None:
                    fault_hook("after_page_version")
            poststate, _post_page = self._read_current(
                page_id=expected.source_page_id
            )
            receipt = build_rename_receipt(
                changed=changed,
                source_request_sha256=request_sha256,
                poststate=poststate,
            )
            receipt = validate_rename_receipt(
                receipt,
                expected_request_sha256=request_sha256,
                expected_prestate=expected,
            )
            if fault_hook is not None:
                fault_hook("before_operation_insert")
            self._write_context = 1
            try:
                self._con.execute(
                    "INSERT INTO notion_legacy_change_consumer_operations("
                    "consumer,operation_id,action,request_sha256,result_json,created_at"
                    ") VALUES(?,?,?,?,?,?)",
                    (
                        SOURCE_CONSUMER,
                        source_operation_id,
                        RENAME_ACTION,
                        request_sha256,
                        _canonical_json(receipt.descriptor()),
                        now,
                    ),
                )
            finally:
                self._write_context = 0
            if fault_hook is not None:
                fault_hook("after_operation_insert")
                fault_hook("before_commit")
            try:
                self._con.commit()
            except BaseException as exc:
                if self._con.in_transaction:
                    self._con.rollback()
                    raise
                recovered = self._replay(
                    source_operation_id=source_operation_id,
                    source_request_sha256=request_sha256,
                    expected_prestate=expected,
                )
                if recovered is not None:
                    return recovered
                raise LegacyPageRenameManualAuditRequired(
                    "legacy page rename commit outcome is indeterminate"
                ) from exc
            self._assert_identity()
            if fault_hook is not None:
                fault_hook("after_commit")
            return receipt
        except BaseException:
            self._write_context = 0
            if self._con.in_transaction:
                self._con.rollback()
            raise


__all__ = [
    "LegacyPageRenameBoundsExceeded",
    "LegacyPageRenameManualAuditRequired",
    "LegacyPageRenameOperationConflict",
    "LegacyPageRenameSourceAdapter",
    "LegacyPageRenameSourceConflict",
    "MAX_PAGE_VERSION_BLOCKS",
    "MAX_PAGE_VERSION_BYTES",
]
