"""Route-unregistered NP-2C legacy create/move/reorder source adapter.

The adapter owns no path discovery and opens no database.  It accepts one
caller-owned, exact file-backed legacy-v3 SQLite connection, authenticates its
frozen contracts once, and records action-specific immutable receipts in the
existing v3 operation ledger.  A later offline orchestrator owns sentinel and
catalog finalization; request/startup paths must not import this module.
"""
from __future__ import annotations

from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable, Iterable

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
from code_assist_v1.notion_legacy_page_structure_contract import (
    CREATE_ACTION,
    MOVE_ACTION,
    REORDER_ACTION,
    LegacyPageCreatePayload,
    LegacyPageStructureContractError,
    LegacyPageStructurePrestate,
    LegacyPageStructureReceipt,
    MAX_AGGREGATE_BYTES,
    MAX_EFFECT_PAGE_RECEIPTS,
    MAX_PAGE_BLOCKS,
    MAX_PAGE_BYTES,
    MAX_PAGE_FILES,
    MAX_PARENT_BLOCKS,
    MAX_RESULT_JSON_BYTES,
    MAX_SIBLING_NODES,
    MAX_SUBTREE_NODES,
    MAX_TREE_DEPTH,
    SOURCE_CONSUMER,
    build_structure_receipt,
    canonical_json,
    derive_create_page_id,
    derive_structure_source_operation_id,
    legacy_page_structure_request_sha256,
    normalize_create_payload,
    sha256_payload,
    validate_structure_prestate,
    validate_structure_receipt,
)


MAX_PERMISSION_HOPS = 256
MAX_HISTORY_ROWS = 100
MAX_SHARE_ROWS = 5_000

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")
_PAGE_ID_RE = re.compile(r"^p_[0-9a-f]{32}$")
_EDIT_ROLES = frozenset({"owner", "edit"})


class LegacyPageStructureOperationConflict(Exception):
    """A deterministic source operation ID was reused for another request."""


class LegacyPageStructureSourceConflict(Exception):
    """The exact source prestate, identity, permission, or generation is stale."""


class LegacyPageStructureBoundsExceeded(Exception):
    """A structural effect exceeds one of the frozen source bounds."""


class LegacyPageStructureManualAuditRequired(Exception):
    """The adapter cannot prove the durable commit outcome."""


FaultHook = Callable[[str], None]


def _name(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _NAME_RE.fullmatch(value) is None:
        raise ValueError(f"valid {field} required")
    return value


def _page_id(value: object, *, allow_empty: bool = False) -> str:
    if allow_empty and value == "":
        return ""
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
        raise LegacyPageStructureContractError(
            "legacy page structure text is invalid"
        ) from exc


def _json_object(value: object, *, field: str) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LegacyPageStructureContractError(f"legacy {field} is invalid") from exc
    if not isinstance(parsed, dict):
        raise LegacyPageStructureContractError(f"legacy {field} is invalid")
    return parsed


def _deterministic_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(
        canonical_json([str(part) for part in parts]).encode("utf-8")
    ).hexdigest()
    return prefix + digest[:32]


class LegacyPageStructureSourceAdapter:
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
            raise LegacyPageStructureContractError(
                "legacy source foreign keys are unavailable"
            )
        self._owner = _name(source_owner_id, field="source_owner_id")
        raw_path = Path(expected_source_db_path)
        if not raw_path.is_absolute() or _is_link(raw_path):
            raise LegacyPageStructureContractError(
                "exact source database path required"
            )
        try:
            self._path = raw_path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise LegacyPageStructureContractError(
                "legacy structure source is unavailable"
            ) from exc
        if not self._path.is_file():
            raise LegacyPageStructureContractError(
                "exact source database path required"
            )
        self._clock = trusted_clock or (lambda: int(time.time()))
        self._write_context = 0
        self._now_value = 0
        self._identity = self._connection_identity()
        if str(self._con.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal":
            raise LegacyPageStructureContractError(
                "legacy structure source must be checkpointed"
            )
        if any(
            Path(str(self._path) + suffix).exists()
            for suffix in ("-wal", "-shm", "-journal")
        ):
            raise LegacyPageStructureContractError(
                "legacy structure source sidecar is present"
            )
        inspected = inspect_legacy_change_consumer_contract(
            self._con, verify_integrity=True
        )
        if not inspected.get("installed"):
            raise LegacyPageStructureContractError(
                "legacy source v3 migration is required"
            )
        self._assert_atomic_revision_capability()
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
                raise LegacyPageStructureContractError(
                    "exact source database path required"
                )
            stat = self._path.stat()
            if stat.st_nlink != 1:
                raise LegacyPageStructureContractError(
                    "legacy structure source must have one link"
                )
            return (
                sqlite_source_identity(int(stat.st_dev)),
                sqlite_source_identity(int(stat.st_ino)),
                int(stat.st_nlink),
            )
        except LegacyPageStructureContractError:
            raise
        except (OSError, StopIteration, TypeError, ValueError) as exc:
            raise LegacyPageStructureContractError(
                "legacy structure source is unavailable"
            ) from exc

    def _assert_identity(self) -> tuple[int, int, int]:
        current = self._connection_identity()
        if current != self._identity:
            raise LegacyPageStructureSourceConflict(
                "legacy structure source identity changed"
            )
        return current

    def _now(self) -> int:
        value = self._clock()
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= MAX_TIMESTAMP
        ):
            raise LegacyPageStructureContractError(
                "trusted source clock is invalid"
            )
        return value

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
        functions = {
            (str(row[0]), int(row[1]))
            for row in self._con.execute(
                "SELECT name,narg FROM pragma_function_list WHERE name IN "
                "('notion_new_incarnation_id','notion_incarnation_write_context')"
            ).fetchall()
        }
        if (
            "revision" not in pages
            or not required_versions.issubset(versions)
            or ("notion_new_incarnation_id", 0) not in functions
            or ("notion_incarnation_write_context", 0) not in functions
        ):
            raise LegacyPageStructureContractError(
                "atomic source structure capability is required"
            )

    def _state(self) -> tuple[int, int]:
        row = self._con.execute(
            "SELECT source_epoch,full_sweep_generation "
            "FROM notion_legacy_change_state WHERE singleton=1"
        ).fetchone()
        if not row:
            raise LegacyPageStructureContractError(
                "legacy source state is unavailable"
            )
        return int(row["source_epoch"]), int(row["full_sweep_generation"])

    def _page_evidence(self, page_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        page = self._con.execute(
            "SELECT * FROM pages WHERE id=? AND archived=0", (page_id,)
        ).fetchone()
        if not page or str(page["owner_user_id"]) != self._owner:
            raise LegacyPageStructureSourceConflict(
                "legacy structure source prestate changed"
            )
        watermark = self._con.execute(
            "SELECT change_generation,change_mask,source_exists "
            "FROM notion_page_change_watermarks WHERE page_id=?", (page_id,)
        ).fetchone()
        incarnation = self._con.execute(
            "SELECT incarnation_generation,incarnation_id,retired_at "
            "FROM notion_page_incarnation_history WHERE page_id=? "
            "ORDER BY incarnation_generation DESC LIMIT 1", (page_id,)
        ).fetchone()
        if (
            not watermark
            or not incarnation
            or int(watermark["source_exists"]) != 1
            or incarnation["retired_at"] is not None
        ):
            raise LegacyPageStructureSourceConflict(
                "legacy structure source prestate changed"
            )
        source_epoch, full_sweep = self._state()
        identity = self._assert_identity()
        evidence = {
            "source_device": identity[0],
            "source_inode": identity[1],
            "source_link_count": identity[2],
            "source_epoch": source_epoch,
            "full_sweep_generation": full_sweep,
            "source_owner_subject_id": self._owner,
            "target_page_id": page_id,
            "target_incarnation_id": str(incarnation["incarnation_id"]),
            "target_incarnation_generation": int(incarnation["incarnation_generation"]),
            "target_revision": int(page["revision"]),
            "target_updated_at": int(page["updated_at"]),
            "target_change_generation": int(watermark["change_generation"]),
            "target_change_mask": int(watermark["change_mask"]),
        }
        evidence["target_incarnation_receipt_sha256"] = (
            legacy_incarnation_receipt_sha256(
                source_owner_id=self._owner,
                page_id=page_id,
                incarnation_id=evidence["target_incarnation_id"],
                incarnation_generation=evidence["target_incarnation_generation"],
                incarnation_retired=False,
                change_generation=evidence["target_change_generation"],
                change_mask=evidence["target_change_mask"],
                source_exists=True,
                source_active=True,
                source_epoch=source_epoch,
                full_sweep_generation=full_sweep,
                source_device=identity[0],
                source_inode=identity[1],
                source_link_count=identity[2],
            )
        )
        return evidence, dict(page)

    def _bounded_rows(
        self, sql: str, params: tuple[Any, ...], *, maximum: int, reason: str
    ) -> list[sqlite3.Row]:
        rows = self._con.execute(sql, (*params, maximum + 1)).fetchall()
        if len(rows) > maximum:
            raise LegacyPageStructureBoundsExceeded(reason)
        return rows

    def _page_descriptor(self, page_id: str) -> tuple[dict[str, Any], int]:
        evidence, page = self._page_evidence(page_id)
        blocks = self._bounded_rows(
            "SELECT id,position,block_type,content,props_json,created_at,updated_at "
            "FROM blocks WHERE page_id=? ORDER BY position,id LIMIT ?",
            (page_id,),
            maximum=MAX_PAGE_BLOCKS,
            reason="legacy page block bound exceeded",
        )
        files = self._bounded_rows(
            "SELECT id,uploader_user_id,original_name,stored_name,rel_path,mime_type,"
            "size,created_at FROM files WHERE page_id=? ORDER BY id LIMIT ?",
            (page_id,),
            maximum=MAX_PAGE_FILES,
            reason="legacy page file bound exceeded",
        )
        shares = self._bounded_rows(
            "SELECT id,shared_user_id,role,created_at,updated_at,revoked_at "
            "FROM page_shares WHERE page_id=? ORDER BY id LIMIT ?",
            (page_id,),
            maximum=MAX_SHARE_ROWS,
            reason="legacy page share bound exceeded",
        )
        public = self._con.execute(
            "SELECT allow_duplicate,created_at,updated_at,revoked_at "
            "FROM public_links WHERE page_id=?", (page_id,)
        ).fetchall()
        if len(public) > 1:
            raise LegacyPageStructureContractError(
                "legacy public-link state is invalid"
            )
        descriptor = {
            "page": {
                key: page.get(key)
                for key in (
                    "id", "owner_user_id", "parent_id", "title", "icon", "cover",
                    "settings_json", "position", "archived", "archived_at", "revision",
                    "created_at", "updated_at",
                )
            },
            "evidence": evidence,
            "blocks": [dict(row) for row in blocks],
            "files": [dict(row) for row in files],
            "shares": [dict(row) for row in shares],
            "public_link": dict(public[0]) if public else None,
        }
        encoded_size = _utf8_len(canonical_json(descriptor))
        if encoded_size > MAX_PAGE_BYTES:
            raise LegacyPageStructureBoundsExceeded(
                "legacy page evidence byte bound exceeded"
            )
        return descriptor, encoded_size

    def _manifest(self, page_ids: Iterable[str]) -> tuple[str, int]:
        unique = sorted({str(value) for value in page_ids if str(value)})
        if len(unique) > MAX_EFFECT_PAGE_RECEIPTS:
            raise LegacyPageStructureBoundsExceeded(
                "legacy structural effect page bound exceeded"
            )
        rows: list[dict[str, str]] = []
        aggregate_bytes = 0
        for page_id in unique:
            descriptor, byte_count = self._page_descriptor(page_id)
            aggregate_bytes += byte_count
            if aggregate_bytes > MAX_AGGREGATE_BYTES:
                raise LegacyPageStructureBoundsExceeded(
                    "legacy structural effect byte bound exceeded"
                )
            rows.append({"page_id": page_id, "page_sha256": sha256_payload(descriptor)})
        return sha256_payload({"pages": rows}), len(unique)

    def _sibling_ids(self, parent_page_id: str) -> list[str]:
        if parent_page_id:
            sql = (
                "SELECT id FROM pages WHERE archived=0 AND parent_id=? "
                "ORDER BY position,updated_at DESC,id LIMIT ?"
            )
            params: tuple[Any, ...] = (parent_page_id,)
        else:
            sql = (
                "SELECT id FROM pages WHERE archived=0 AND parent_id IS NULL "
                "ORDER BY position,updated_at DESC,id LIMIT ?"
            )
            params = ()
        return [
            str(row["id"])
            for row in self._bounded_rows(
                sql,
                params,
                maximum=MAX_SIBLING_NODES,
                reason="legacy sibling scope bound exceeded",
            )
        ]

    def _descendant_ids(self, page_id: str) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        queue: deque[tuple[str, int]] = deque([(page_id, 0)])
        while queue:
            current, depth = queue.popleft()
            if current in seen:
                raise LegacyPageStructureContractError(
                    "legacy page tree contains a cycle"
                )
            if depth > MAX_TREE_DEPTH:
                raise LegacyPageStructureBoundsExceeded(
                    "legacy page tree depth bound exceeded"
                )
            seen.add(current)
            output.append(current)
            if len(output) > MAX_SUBTREE_NODES:
                raise LegacyPageStructureBoundsExceeded(
                    "legacy page subtree bound exceeded"
                )
            children = self._con.execute(
                "SELECT id FROM pages WHERE parent_id=? AND archived=0 "
                "ORDER BY position,updated_at DESC,id LIMIT ?",
                (current, MAX_SUBTREE_NODES + 1),
            ).fetchall()
            for row in children:
                queue.append((str(row["id"]), depth + 1))
        return output

    def _parent_blocks(self, parent_page_id: str) -> list[dict[str, Any]]:
        if not parent_page_id:
            return []
        rows = self._bounded_rows(
            "SELECT id,position,block_type,content,props_json,created_at,updated_at "
            "FROM blocks WHERE page_id=? ORDER BY position,id LIMIT ?",
            (parent_page_id,),
            maximum=MAX_PARENT_BLOCKS,
            reason="legacy parent block bound exceeded",
        )
        output: list[dict[str, Any]] = []
        for row in rows:
            raw = dict(row)
            raw["props"] = _json_object(
                raw.pop("props_json", "{}"), field="block props"
            )
            output.append(raw)
        if _utf8_len(canonical_json(output)) > MAX_PAGE_BYTES:
            raise LegacyPageStructureBoundsExceeded(
                "legacy parent block byte bound exceeded"
            )
        return output

    @staticmethod
    def _child_reference_ids(blocks: Iterable[dict[str, Any]]) -> list[str]:
        output: list[str] = []
        for block in blocks:
            props = block.get("props") if isinstance(block.get("props"), dict) else {}
            if str(block.get("block_type") or block.get("type") or "") == "child_page":
                page_id = str(props.get("page_id") or "")
                if page_id:
                    output.append(page_id)
            rows = props.get("rows")
            if isinstance(rows, list):
                output.extend(
                    str(row.get("page_id") or "")
                    for row in rows
                    if isinstance(row, dict) and str(row.get("page_id") or "")
                )
        return output

    def _structure_snapshot(
        self,
        *,
        target_page_id: str,
        old_parent_page_id: str,
        new_parent_page_id: str,
    ) -> dict[str, Any]:
        target = self._con.execute(
            "SELECT parent_id FROM pages WHERE id=? AND archived=0",
            (target_page_id,),
        ).fetchone()
        if not target:
            raise LegacyPageStructureSourceConflict(
                "legacy structure source prestate changed"
            )
        old_scope = self._sibling_ids(old_parent_page_id)
        new_scope = (
            list(old_scope)
            if new_parent_page_id == old_parent_page_id
            else self._sibling_ids(new_parent_page_id)
        )
        old_blocks = self._parent_blocks(old_parent_page_id)
        new_blocks = (
            old_blocks
            if new_parent_page_id == old_parent_page_id
            else self._parent_blocks(new_parent_page_id)
        )
        return {
            "target_parent_page_id": str(target["parent_id"] or ""),
            "old_scope_page_ids": old_scope,
            "new_scope_page_ids": new_scope,
            "old_parent_child_references": self._child_reference_ids(old_blocks),
            "new_parent_child_references": self._child_reference_ids(new_blocks),
        }

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
            raise LegacyPageStructureBoundsExceeded(
                "legacy page ancestry exceeds the adapter bound"
            )
        raise PermissionError("edit permission required")

    def _assert_unlocked(self, page_id: str) -> None:
        if not page_id:
            return
        row = self._con.execute(
            "SELECT owner_user_id,settings_json FROM pages "
            "WHERE id=? AND archived=0", (page_id,)
        ).fetchone()
        if not row or str(row["owner_user_id"]) != self._owner:
            raise LegacyPageStructureSourceConflict(
                "legacy structure source prestate changed"
            )
        settings = _json_object(row["settings_json"], field="page settings")
        if settings.get("locked"):
            raise PermissionError("parent page is locked")

    def _capture_create_prestate(
        self, *, target_page_id: str, new_parent_page_id: str
    ) -> LegacyPageStructurePrestate:
        identity = self._assert_identity()
        if self._con.execute(
            "SELECT 1 FROM pages WHERE id=?", (target_page_id,)
        ).fetchone() or self._con.execute(
            "SELECT 1 FROM notion_page_incarnation_history WHERE page_id=? LIMIT 1",
            (target_page_id,),
        ).fetchone():
            raise LegacyPageStructureSourceConflict(
                "legacy create target is no longer absent"
            )
        if new_parent_page_id:
            parent = self._con.execute(
                "SELECT owner_user_id FROM pages WHERE id=? AND archived=0",
                (new_parent_page_id,),
            ).fetchone()
            if not parent or str(parent["owner_user_id"]) != self._owner:
                raise LegacyPageStructureSourceConflict(
                    "legacy create parent changed"
                )
        siblings = self._sibling_ids(new_parent_page_id)
        manifest_pages = set(siblings)
        if new_parent_page_id:
            manifest_pages.add(new_parent_page_id)
        manifest, _count = self._manifest(manifest_pages)
        source_epoch, full_sweep = self._state()
        return validate_structure_prestate(
            {
                "action": CREATE_ACTION,
                "source_device": identity[0],
                "source_inode": identity[1],
                "source_link_count": identity[2],
                "source_epoch": source_epoch,
                "full_sweep_generation": full_sweep,
                "source_owner_subject_id": self._owner,
                "target_page_id": target_page_id,
                "target_exists": False,
                "target_incarnation_id": "",
                "target_incarnation_generation": 0,
                "target_revision": 0,
                "target_updated_at": 0,
                "target_change_generation": 0,
                "target_change_mask": 0,
                "target_incarnation_receipt_sha256": "",
                "old_parent_page_id": "",
                "new_parent_page_id": new_parent_page_id,
                "relative_page_id": "",
                "relative_position": "",
                "target_dense_ordinal": 0,
                "subtree_count": 0,
                "old_scope_count": 0,
                "new_scope_count": len(siblings),
                "pre_manifest_sha256": manifest,
            }
        )

    def capture_create_prestate(
        self,
        *,
        workspace_id: str,
        mutation_actor_subject_id: str,
        caller_operation_id: str,
        parent_page_id: str = "",
        source_quiesced_confirmed: bool,
    ) -> LegacyPageStructurePrestate:
        if source_quiesced_confirmed is not True:
            raise ValueError("legacy page create requires source quiescence")
        if self._con.in_transaction:
            raise ValueError("idle SQLite connection required")
        target_page_id = derive_create_page_id(
            workspace_id=workspace_id,
            source_owner_subject_id=self._owner,
            mutation_actor_subject_id=mutation_actor_subject_id,
            caller_operation_id=caller_operation_id,
        )
        parent = _page_id(parent_page_id, allow_empty=True)
        inspect_legacy_change_consumer_contract(self._con, verify_integrity=False)
        self._con.execute("BEGIN")
        try:
            result = self._capture_create_prestate(
                target_page_id=target_page_id,
                new_parent_page_id=parent,
            )
            self._con.rollback()
            return result
        except BaseException:
            if self._con.in_transaction:
                self._con.rollback()
            raise

    def _capture_move_prestate(
        self,
        *,
        target_page_id: str,
        requested_parent_page_id: str,
        relative_page_id: str,
        relative_position: str,
    ) -> LegacyPageStructurePrestate:
        evidence, page = self._page_evidence(target_page_id)
        old_parent = str(page.get("parent_id") or "")
        new_parent = requested_parent_page_id
        if relative_page_id:
            relative = self._con.execute(
                "SELECT parent_id,owner_user_id FROM pages "
                "WHERE id=? AND archived=0", (relative_page_id,)
            ).fetchone()
            if not relative or str(relative["owner_user_id"]) != self._owner:
                raise LegacyPageStructureSourceConflict(
                    "legacy relative page changed"
                )
            new_parent = str(relative["parent_id"] or "")
        if target_page_id in {new_parent, relative_page_id}:
            raise ValueError("page cannot be moved onto itself")
        descendants = self._descendant_ids(target_page_id)
        if new_parent in set(descendants):
            raise ValueError("page cannot be moved under its descendant")
        if new_parent:
            parent = self._con.execute(
                "SELECT owner_user_id FROM pages WHERE id=? AND archived=0",
                (new_parent,),
            ).fetchone()
            if not parent or str(parent["owner_user_id"]) != self._owner:
                raise LegacyPageStructureSourceConflict(
                    "legacy destination parent changed"
                )
        old_scope = self._sibling_ids(old_parent)
        new_scope = old_scope if new_parent == old_parent else self._sibling_ids(new_parent)
        if target_page_id not in old_scope:
            raise LegacyPageStructureContractError(
                "legacy target is absent from its sibling scope"
            )
        if relative_page_id and relative_page_id not in new_scope:
            raise LegacyPageStructureSourceConflict(
                "legacy relative page changed"
            )
        manifest_pages = set(descendants) | set(old_scope) | set(new_scope)
        manifest_pages.update(value for value in (old_parent, new_parent) if value)
        manifest, _count = self._manifest(manifest_pages)
        action = MOVE_ACTION if old_parent != new_parent else REORDER_ACTION
        return validate_structure_prestate(
            {
                "action": action,
                **evidence,
                "target_exists": True,
                "old_parent_page_id": old_parent,
                "new_parent_page_id": new_parent,
                "relative_page_id": relative_page_id,
                "relative_position": relative_position,
                "target_dense_ordinal": old_scope.index(target_page_id) + 1,
                "subtree_count": len(descendants),
                "old_scope_count": len(old_scope),
                "new_scope_count": len(new_scope),
                "pre_manifest_sha256": manifest,
            }
        )

    def capture_move_prestate(
        self,
        *,
        page_id: str,
        parent_page_id: str = "",
        before_page_id: str = "",
        after_page_id: str = "",
        source_quiesced_confirmed: bool,
    ) -> LegacyPageStructurePrestate:
        if source_quiesced_confirmed is not True:
            raise ValueError("legacy page move requires source quiescence")
        if before_page_id and after_page_id:
            raise ValueError("use either before_page_id or after_page_id")
        if self._con.in_transaction:
            raise ValueError("idle SQLite connection required")
        target = _page_id(page_id)
        parent = _page_id(parent_page_id, allow_empty=True)
        before = _page_id(before_page_id, allow_empty=True)
        after = _page_id(after_page_id, allow_empty=True)
        relative = before or after
        position = "before" if before else ("after" if after else "")
        inspect_legacy_change_consumer_contract(self._con, verify_integrity=False)
        self._con.execute("BEGIN")
        try:
            result = self._capture_move_prestate(
                target_page_id=target,
                requested_parent_page_id=parent,
                relative_page_id=relative,
                relative_position=position,
            )
            self._con.rollback()
            return result
        except BaseException:
            if self._con.in_transaction:
                self._con.rollback()
            raise

    def _bounded_version_blocks(self, page_id: str) -> str:
        rows = self._bounded_rows(
            "SELECT id,block_type,content,props_json,position FROM blocks "
            "WHERE page_id=? ORDER BY position,id LIMIT ?",
            (page_id,),
            maximum=MAX_PAGE_BLOCKS,
            reason="legacy page version block bound exceeded",
        )
        blocks: list[dict[str, Any]] = []
        byte_count = 0
        for row in rows:
            values = dict(row)
            props = _json_object(values.get("props_json"), field="block props")
            block = {
                "id": str(values.get("id") or ""),
                "type": str(values.get("block_type") or "paragraph"),
                "content": str(values.get("content") or ""),
                "props": props,
                "position": int(values.get("position") or 0),
            }
            byte_count += _utf8_len(canonical_json(block))
            if byte_count > MAX_PAGE_BYTES:
                raise LegacyPageStructureBoundsExceeded(
                    "legacy page version byte bound exceeded"
                )
            blocks.append(block)
        result = canonical_json(blocks)
        if _utf8_len(result) > MAX_PAGE_BYTES:
            raise LegacyPageStructureBoundsExceeded(
                "legacy page version byte bound exceeded"
            )
        return result

    def _record_page_version(
        self, *, page_id: str, now: int, source_operation_id: str, phase: str
    ) -> str:
        page = self._con.execute(
            "SELECT * FROM pages WHERE id=?", (page_id,)
        ).fetchone()
        if not page:
            raise LegacyPageStructureSourceConflict(
                "legacy page version target changed"
            )
        data = dict(page)
        blocks_json = self._bounded_version_blocks(page_id)
        signature = (
            str(data.get("title") or "Untitled"),
            str(data.get("icon") or ""),
            str(data.get("cover") or ""),
            str(data.get("settings_json") or "{}"),
            blocks_json,
        )
        previous = self._con.execute(
            "SELECT title,icon,cover,settings_json,blocks_json FROM page_versions "
            "WHERE page_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (page_id,),
        ).fetchone()
        if previous and signature == tuple(
            str(previous[key] or "")
            for key in ("title", "icon", "cover", "settings_json", "blocks_json")
        ):
            return ""
        previous_size = self._con.execute(
            "SELECT length(CAST(blocks_json AS BLOB)) FROM page_versions "
            "WHERE page_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
            (page_id,),
        ).fetchone()
        if previous_size and int(previous_size[0] or 0) > MAX_PAGE_BYTES:
            raise LegacyPageStructureBoundsExceeded(
                "legacy page version history bound exceeded"
            )
        version_id = _deterministic_id(
            "v_", source_operation_id, page_id, phase, now
        )
        self._con.execute(
            "INSERT INTO page_versions(id,page_id,owner_user_id,title,icon,cover,"
            "settings_json,blocks_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (version_id, page_id, self._owner, *signature, now),
        )
        stale = self._con.execute(
            "SELECT id FROM page_versions WHERE page_id=? "
            "ORDER BY created_at DESC,rowid DESC LIMIT -1 OFFSET ?",
            (page_id, MAX_HISTORY_ROWS),
        ).fetchall()
        if stale:
            self._con.executemany(
                "DELETE FROM page_versions WHERE id=?",
                [(row["id"],) for row in stale],
            )
        return version_id

    def _replace_blocks(
        self,
        *,
        page_id: str,
        blocks: Iterable[dict[str, Any]],
        now: int,
        source_operation_id: str,
        phase: str,
    ) -> None:
        prepared = list(blocks)
        if len(prepared) > MAX_PARENT_BLOCKS and phase != "create-page":
            raise LegacyPageStructureBoundsExceeded(
                "legacy parent block bound exceeded"
            )
        if len(prepared) > MAX_PAGE_BLOCKS:
            raise LegacyPageStructureBoundsExceeded(
                "legacy page block bound exceeded"
            )
        aggregate = 0
        normalized: list[tuple[str, str, dict[str, Any]]] = []
        for raw in prepared:
            if not isinstance(raw, dict):
                raise LegacyPageStructureContractError(
                    "legacy block evidence is invalid"
                )
            block_type = str(raw.get("type") or raw.get("block_type") or "paragraph")[:64]
            content = str(raw.get("content") or "")
            props = raw.get("props") if isinstance(raw.get("props"), dict) else _json_object(
                raw.get("props_json"), field="block props"
            )
            detached = json.loads(canonical_json(props))
            aggregate += _utf8_len(block_type) + _utf8_len(content) + _utf8_len(canonical_json(detached))
            if aggregate > MAX_PAGE_BYTES:
                raise LegacyPageStructureBoundsExceeded(
                    "legacy page block byte bound exceeded"
                )
            normalized.append((block_type, content, detached))
        self._con.execute("DELETE FROM blocks WHERE page_id=?", (page_id,))
        for index, (block_type, content, props) in enumerate(normalized, start=1):
            block_id = _deterministic_id(
                "b_", source_operation_id, page_id, phase, index
            )
            self._con.execute(
                "INSERT INTO blocks(id,page_id,owner_user_id,position,block_type,"
                "content,props_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    block_id,
                    page_id,
                    self._owner,
                    index,
                    block_type,
                    content,
                    canonical_json(props),
                    now,
                    now,
                ),
            )
        self._con.execute(
            "UPDATE pages SET updated_at=? WHERE id=?", (now, page_id)
        )

    @staticmethod
    def _remove_child_references(
        blocks: list[dict[str, Any]], child_page_id: str
    ) -> tuple[list[dict[str, Any]], bool]:
        output: list[dict[str, Any]] = []
        changed = False
        for original in blocks:
            block = dict(original)
            props = dict(block.get("props") or {})
            if (
                str(block.get("block_type") or block.get("type") or "") == "child_page"
                and str(props.get("page_id") or "") == child_page_id
            ):
                changed = True
                continue
            rows = props.get("rows")
            if isinstance(rows, list):
                filtered = [
                    row
                    for row in rows
                    if not (
                        isinstance(row, dict)
                        and str(row.get("page_id") or "") == child_page_id
                    )
                ]
                if len(filtered) != len(rows):
                    props["rows"] = filtered
                    changed = True
            block["props"] = props
            output.append(block)
        return output, changed

    @staticmethod
    def _position_child_reference(
        blocks: list[dict[str, Any]],
        page: dict[str, Any],
        *,
        before_page_id: str = "",
        after_page_id: str = "",
        create: bool = False,
    ) -> tuple[list[dict[str, Any]], bool]:
        page_id = str(page.get("id") or "")
        output = [dict(block) for block in blocks]
        current_index = next(
            (
                index
                for index, block in enumerate(output)
                if str(block.get("block_type") or block.get("type") or "") == "child_page"
                and str((block.get("props") or {}).get("page_id") or "") == page_id
            ),
            -1,
        )
        if current_index >= 0:
            reference = output.pop(current_index)
        elif create:
            reference = {
                "block_type": "child_page",
                "content": str(page.get("title") or "Untitled"),
                "props": {
                    "page_id": page_id,
                    "title": str(page.get("title") or "Untitled"),
                    "icon": str(page.get("icon") or ""),
                },
            }
        else:
            return blocks, False
        target_id = before_page_id or after_page_id
        target_index = next(
            (
                index
                for index, block in enumerate(output)
                if str(block.get("block_type") or block.get("type") or "") == "child_page"
                and str((block.get("props") or {}).get("page_id") or "") == target_id
            ),
            -1,
        )
        if target_index >= 0:
            insert_at = target_index + (1 if after_page_id else 0)
        elif current_index >= 0 and not create:
            insert_at = min(current_index, len(output))
        else:
            insert_at = len(output)
        output.insert(insert_at, reference)
        return output, bool(create or current_index != insert_at)

    def _bump_revisions(self, page_ids: Iterable[str], *, now: int) -> None:
        values = sorted({str(value) for value in page_ids if str(value)})
        self._con.executemany(
            "UPDATE pages SET revision=revision+1 WHERE id=?",
            [(value,) for value in values],
        )

    def _assert_clock_at_least(
        self, page_ids: Iterable[str], *, now: int
    ) -> None:
        values = sorted({str(value) for value in page_ids if str(value)})
        if not values:
            return
        maximum = 0
        for start in range(0, len(values), 500):
            chunk = values[start : start + 500]
            placeholders = ",".join("?" for _value in chunk)
            row = self._con.execute(
                f"SELECT COALESCE(MAX(updated_at),0) FROM pages "
                f"WHERE id IN ({placeholders})",
                tuple(chunk),
            ).fetchone()
            maximum = max(maximum, int(row[0] or 0))
        if now < maximum:
            raise LegacyPageStructureSourceConflict(
                "trusted source clock moved backwards"
            )

    def _replay(
        self,
        *,
        source_operation_id: str,
        source_request_sha256: str,
        expected_prestate: LegacyPageStructurePrestate,
    ) -> LegacyPageStructureReceipt | None:
        row = self._con.execute(
            "SELECT action,request_sha256,result_json FROM "
            "notion_legacy_change_consumer_operations "
            "WHERE consumer=? AND operation_id=?",
            (SOURCE_CONSUMER, source_operation_id),
        ).fetchone()
        if not row:
            return None
        if (
            str(row["action"]) != expected_prestate.action
            or str(row["request_sha256"]) != source_request_sha256
        ):
            raise LegacyPageStructureOperationConflict(source_operation_id)
        raw_json = str(row["result_json"])
        if _utf8_len(raw_json) > MAX_RESULT_JSON_BYTES:
            raise LegacyPageStructureContractError(
                "legacy page structure ledger is invalid"
            )
        try:
            result = json.loads(raw_json)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LegacyPageStructureContractError(
                "legacy page structure ledger is invalid"
            ) from exc
        if raw_json != canonical_json(result):
            raise LegacyPageStructureContractError(
                "legacy page structure ledger is invalid"
            )
        return validate_structure_receipt(
            result,
            expected_request_sha256=source_request_sha256,
            expected_prestate=expected_prestate,
        )

    def _insert_operation(
        self,
        *,
        source_operation_id: str,
        request_sha256: str,
        receipt: LegacyPageStructureReceipt,
        now: int,
    ) -> None:
        result_json = canonical_json(receipt.descriptor())
        if _utf8_len(result_json) > MAX_RESULT_JSON_BYTES:
            raise LegacyPageStructureBoundsExceeded(
                "legacy page structure receipt byte bound exceeded"
            )
        self._write_context = 1
        try:
            self._con.execute(
                "INSERT INTO notion_legacy_change_consumer_operations("
                "consumer,operation_id,action,request_sha256,result_json,created_at"
                ") VALUES(?,?,?,?,?,?)",
                (
                    SOURCE_CONSUMER,
                    source_operation_id,
                    receipt.action,
                    request_sha256,
                    result_json,
                    now,
                ),
            )
        finally:
            self._write_context = 0

    def _commit_or_converge(
        self,
        *,
        source_operation_id: str,
        request_sha256: str,
        expected_prestate: LegacyPageStructurePrestate,
    ) -> LegacyPageStructureReceipt | None:
        try:
            self._con.commit()
            return None
        except BaseException as exc:
            if self._con.in_transaction:
                self._con.rollback()
                raise
            recovered = self._replay(
                source_operation_id=source_operation_id,
                source_request_sha256=request_sha256,
                expected_prestate=expected_prestate,
            )
            if recovered is not None:
                return recovered
            raise LegacyPageStructureManualAuditRequired(
                "legacy page structure commit outcome is indeterminate"
            ) from exc

    def create(
        self,
        *,
        workspace_id: str,
        mutation_actor_subject_id: str,
        catalog_command_id: str,
        caller_operation_id: str,
        expected_prestate: LegacyPageStructurePrestate,
        create_payload: LegacyPageCreatePayload,
        source_quiesced_confirmed: bool,
        fault_hook: FaultHook | None = None,
    ) -> LegacyPageStructureReceipt:
        """Create one deterministic page and receipt it in the source transaction."""

        expected = validate_structure_prestate(expected_prestate)
        if expected.action != CREATE_ACTION:
            raise ValueError("legacy create prestate required")
        actor = _name(
            mutation_actor_subject_id, field="mutation_actor_subject_id"
        )
        target_page_id = derive_create_page_id(
            workspace_id=workspace_id,
            source_owner_subject_id=self._owner,
            mutation_actor_subject_id=actor,
            caller_operation_id=caller_operation_id,
        )
        if (
            expected.source_owner_subject_id != self._owner
            or expected.target_page_id != target_page_id
        ):
            raise LegacyPageStructureSourceConflict(
                "legacy create prestate is for another source"
            )
        if not isinstance(create_payload, LegacyPageCreatePayload):
            raise LegacyPageStructureContractError("create payload required")
        # Re-normalize the detached descriptor so callers cannot smuggle a
        # hand-built dataclass that bypasses payload bounds.
        payload = normalize_create_payload(**create_payload.descriptor())
        source_operation_id = derive_structure_source_operation_id(
            action=CREATE_ACTION,
            catalog_command_id=catalog_command_id,
            caller_operation_id=caller_operation_id,
        )
        request_sha256 = legacy_page_structure_request_sha256(
            mutation_actor_subject_id=actor,
            catalog_command_id=catalog_command_id,
            caller_operation_id=caller_operation_id,
            expected_prestate=expected,
            create_payload=payload,
        )

        # Exact replay is deliberately the first source query.  A lost
        # response must not depend on a renewed quiescence assertion.
        replay = self._replay(
            source_operation_id=source_operation_id,
            source_request_sha256=request_sha256,
            expected_prestate=expected,
        )
        if replay is not None:
            return replay
        if source_quiesced_confirmed is not True:
            raise ValueError("legacy page create requires source quiescence")
        if self._con.in_transaction:
            raise ValueError("idle SQLite connection required")

        inspect_legacy_change_consumer_contract(
            self._con, verify_integrity=False
        )
        now = self._now()
        self._now_value = now
        try:
            self._con.execute("BEGIN IMMEDIATE")
            replay = self._replay(
                source_operation_id=source_operation_id,
                source_request_sha256=request_sha256,
                expected_prestate=expected,
            )
            if replay is not None:
                self._con.rollback()
                return replay
            current = self._capture_create_prestate(
                target_page_id=target_page_id,
                new_parent_page_id=expected.new_parent_page_id,
            )
            if current != expected:
                raise LegacyPageStructureSourceConflict(
                    "legacy create source prestate changed"
                )
            if expected.new_parent_page_id:
                self._effective_edit_role(
                    actor=actor, page_id=expected.new_parent_page_id
                )
                self._assert_unlocked(expected.new_parent_page_id)
            elif actor != self._owner:
                raise PermissionError("owner permission required")

            siblings = self._sibling_ids(expected.new_parent_page_id)
            self._assert_clock_at_least(
                {*siblings, expected.new_parent_page_id}, now=now
            )
            position = len(siblings) + 1
            self._con.execute(
                "INSERT INTO pages(id,owner_user_id,parent_id,title,icon,cover,"
                "settings_json,position,archived,archived_at,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    target_page_id,
                    self._owner,
                    expected.new_parent_page_id or None,
                    payload.title,
                    payload.icon,
                    payload.cover,
                    canonical_json(payload.settings),
                    position,
                    0,
                    None,
                    now,
                    now,
                ),
            )
            self._replace_blocks(
                page_id=target_page_id,
                blocks=payload.blocks,
                now=now,
                source_operation_id=source_operation_id,
                phase="create-page",
            )
            self._record_page_version(
                page_id=target_page_id,
                now=now,
                source_operation_id=source_operation_id,
                phase="create-page",
            )
            if expected.new_parent_page_id and payload.add_to_parent_blocks:
                parent_blocks = self._parent_blocks(
                    expected.new_parent_page_id
                )
                page = {
                    "id": target_page_id,
                    "title": payload.title,
                    "icon": payload.icon,
                }
                parent_blocks, _changed = self._position_child_reference(
                    parent_blocks, page, create=True
                )
                self._replace_blocks(
                    page_id=expected.new_parent_page_id,
                    blocks=parent_blocks,
                    now=now,
                    source_operation_id=source_operation_id,
                    phase="create-parent",
                )
                self._bump_revisions(
                    {expected.new_parent_page_id}, now=now
                )
                self._record_page_version(
                    page_id=expected.new_parent_page_id,
                    now=now,
                    source_operation_id=source_operation_id,
                    phase="create-parent",
                )

            post_scope = self._sibling_ids(expected.new_parent_page_id)
            if target_page_id not in post_scope:
                raise LegacyPageStructureContractError(
                    "legacy create result is outside its sibling scope"
                )
            manifest_pages = set(post_scope)
            if expected.new_parent_page_id:
                manifest_pages.add(expected.new_parent_page_id)
            post_manifest, affected_count = self._manifest(manifest_pages)
            evidence, _page = self._page_evidence(target_page_id)
            receipt = build_structure_receipt(
                action=CREATE_ACTION,
                source_request_sha256=request_sha256,
                source_changed=True,
                structure_changed=True,
                poststate={
                    **evidence,
                    "old_parent_page_id": "",
                    "new_parent_page_id": expected.new_parent_page_id,
                    "target_dense_ordinal": post_scope.index(target_page_id) + 1,
                    "subtree_count": 1,
                    "old_scope_count": 0,
                    "new_scope_count": len(post_scope),
                    "affected_page_count": affected_count,
                    "post_manifest_sha256": post_manifest,
                },
            )
            if fault_hook is not None:
                fault_hook("before_operation")
            self._insert_operation(
                source_operation_id=source_operation_id,
                request_sha256=request_sha256,
                receipt=receipt,
                now=now,
            )
            if fault_hook is not None:
                fault_hook("before_commit")
            recovered = self._commit_or_converge(
                source_operation_id=source_operation_id,
                request_sha256=request_sha256,
                expected_prestate=expected,
            )
            result = recovered or receipt
            self._assert_identity()
            if fault_hook is not None:
                fault_hook("after_commit")
            return result
        except BaseException:
            if self._con.in_transaction:
                self._con.rollback()
            raise
        finally:
            self._write_context = 0
            self._now_value = 0

    def move_or_reorder(
        self,
        *,
        mutation_actor_subject_id: str,
        catalog_command_id: str,
        caller_operation_id: str,
        expected_prestate: LegacyPageStructurePrestate,
        source_quiesced_confirmed: bool,
        fault_hook: FaultHook | None = None,
    ) -> LegacyPageStructureReceipt:
        """Move/reorder one bounded subtree under an exact source prestate."""

        expected = validate_structure_prestate(expected_prestate)
        if expected.action not in {MOVE_ACTION, REORDER_ACTION}:
            raise ValueError("legacy move or reorder prestate required")
        actor = _name(
            mutation_actor_subject_id, field="mutation_actor_subject_id"
        )
        if expected.source_owner_subject_id != self._owner:
            raise LegacyPageStructureSourceConflict(
                "legacy move prestate is for another source"
            )
        source_operation_id = derive_structure_source_operation_id(
            action=expected.action,
            catalog_command_id=catalog_command_id,
            caller_operation_id=caller_operation_id,
        )
        request_sha256 = legacy_page_structure_request_sha256(
            mutation_actor_subject_id=actor,
            catalog_command_id=catalog_command_id,
            caller_operation_id=caller_operation_id,
            expected_prestate=expected,
        )

        replay = self._replay(
            source_operation_id=source_operation_id,
            source_request_sha256=request_sha256,
            expected_prestate=expected,
        )
        if replay is not None:
            return replay
        if source_quiesced_confirmed is not True:
            raise ValueError("legacy page move requires source quiescence")
        if self._con.in_transaction:
            raise ValueError("idle SQLite connection required")

        inspect_legacy_change_consumer_contract(
            self._con, verify_integrity=False
        )
        now = self._now()
        self._now_value = now
        try:
            self._con.execute("BEGIN IMMEDIATE")
            replay = self._replay(
                source_operation_id=source_operation_id,
                source_request_sha256=request_sha256,
                expected_prestate=expected,
            )
            if replay is not None:
                self._con.rollback()
                return replay
            current = self._capture_move_prestate(
                target_page_id=expected.target_page_id,
                requested_parent_page_id=expected.new_parent_page_id,
                relative_page_id=expected.relative_page_id,
                relative_position=expected.relative_position,
            )
            if current != expected:
                raise LegacyPageStructureSourceConflict(
                    "legacy move source prestate changed"
                )
            if actor != self._owner:
                raise PermissionError("owner permission required")
            if (
                expected.old_parent_page_id
                and expected.old_parent_page_id != expected.new_parent_page_id
            ):
                self._assert_unlocked(expected.old_parent_page_id)
            self._assert_unlocked(expected.new_parent_page_id)

            before = self._structure_snapshot(
                target_page_id=expected.target_page_id,
                old_parent_page_id=expected.old_parent_page_id,
                new_parent_page_id=expected.new_parent_page_id,
            )
            descendants = self._descendant_ids(expected.target_page_id)
            old_scope_before = self._sibling_ids(
                expected.old_parent_page_id
            )
            new_scope_before = (
                list(old_scope_before)
                if expected.new_parent_page_id == expected.old_parent_page_id
                else self._sibling_ids(expected.new_parent_page_id)
            )
            self._assert_clock_at_least(
                {
                    *descendants,
                    *old_scope_before,
                    *new_scope_before,
                    expected.old_parent_page_id,
                    expected.new_parent_page_id,
                },
                now=now,
            )
            destination = [
                page_id
                for page_id in new_scope_before
                if page_id != expected.target_page_id
            ]
            insert_at = len(destination)
            if expected.relative_page_id:
                if expected.relative_page_id not in destination:
                    raise LegacyPageStructureSourceConflict(
                        "legacy relative page changed"
                    )
                insert_at = destination.index(expected.relative_page_id)
                if expected.relative_position == "after":
                    insert_at += 1
            destination.insert(insert_at, expected.target_page_id)

            self._con.execute(
                "UPDATE pages SET parent_id=?,updated_at=? WHERE id=?",
                (
                    expected.new_parent_page_id or None,
                    now,
                    expected.target_page_id,
                ),
            )
            for position, page_id in enumerate(destination, start=1):
                self._con.execute(
                    "UPDATE pages SET position=? WHERE id=?",
                    (position, page_id),
                )
            changed_page_ids = {expected.target_page_id, *destination}
            target_page = self._con.execute(
                "SELECT id,title,icon FROM pages WHERE id=?",
                (expected.target_page_id,),
            ).fetchone()
            if not target_page:
                raise LegacyPageStructureSourceConflict(
                    "legacy move target changed"
                )
            target_descriptor = dict(target_page)

            if (
                expected.old_parent_page_id
                and expected.old_parent_page_id != expected.new_parent_page_id
            ):
                old_blocks, changed = self._remove_child_references(
                    self._parent_blocks(expected.old_parent_page_id),
                    expected.target_page_id,
                )
                if changed:
                    self._replace_blocks(
                        page_id=expected.old_parent_page_id,
                        blocks=old_blocks,
                        now=now,
                        source_operation_id=source_operation_id,
                        phase="move-old-parent",
                    )
                    changed_page_ids.add(expected.old_parent_page_id)
                    self._record_page_version(
                        page_id=expected.old_parent_page_id,
                        now=now,
                        source_operation_id=source_operation_id,
                        phase="move-old-parent",
                    )
            if (
                expected.new_parent_page_id
                and expected.old_parent_page_id != expected.new_parent_page_id
            ):
                new_blocks, _removed = self._remove_child_references(
                    self._parent_blocks(expected.new_parent_page_id),
                    expected.target_page_id,
                )
                new_blocks, changed = self._position_child_reference(
                    new_blocks,
                    target_descriptor,
                    before_page_id=(
                        expected.relative_page_id
                        if expected.relative_position == "before"
                        else ""
                    ),
                    after_page_id=(
                        expected.relative_page_id
                        if expected.relative_position == "after"
                        else ""
                    ),
                    create=True,
                )
                if changed:
                    self._replace_blocks(
                        page_id=expected.new_parent_page_id,
                        blocks=new_blocks,
                        now=now,
                        source_operation_id=source_operation_id,
                        phase="move-new-parent",
                    )
                    changed_page_ids.add(expected.new_parent_page_id)
                    self._record_page_version(
                        page_id=expected.new_parent_page_id,
                        now=now,
                        source_operation_id=source_operation_id,
                        phase="move-new-parent",
                    )
            elif expected.new_parent_page_id and expected.relative_page_id:
                parent_blocks, changed = self._position_child_reference(
                    self._parent_blocks(expected.new_parent_page_id),
                    target_descriptor,
                    before_page_id=(
                        expected.relative_page_id
                        if expected.relative_position == "before"
                        else ""
                    ),
                    after_page_id=(
                        expected.relative_page_id
                        if expected.relative_position == "after"
                        else ""
                    ),
                )
                if changed:
                    self._replace_blocks(
                        page_id=expected.new_parent_page_id,
                        blocks=parent_blocks,
                        now=now,
                        source_operation_id=source_operation_id,
                        phase="reorder-parent",
                    )
                    changed_page_ids.add(expected.new_parent_page_id)
                    self._record_page_version(
                        page_id=expected.new_parent_page_id,
                        now=now,
                        source_operation_id=source_operation_id,
                        phase="reorder-parent",
                    )

            self._bump_revisions(changed_page_ids, now=now)
            after = self._structure_snapshot(
                target_page_id=expected.target_page_id,
                old_parent_page_id=expected.old_parent_page_id,
                new_parent_page_id=expected.new_parent_page_id,
            )
            old_scope_after = self._sibling_ids(
                expected.old_parent_page_id
            )
            new_scope_after = (
                list(old_scope_after)
                if expected.new_parent_page_id == expected.old_parent_page_id
                else self._sibling_ids(expected.new_parent_page_id)
            )
            if expected.target_page_id not in new_scope_after:
                raise LegacyPageStructureContractError(
                    "legacy move result is outside its sibling scope"
                )
            descendants_after = self._descendant_ids(
                expected.target_page_id
            )
            manifest_pages = (
                set(descendants_after)
                | set(old_scope_after)
                | set(new_scope_after)
            )
            manifest_pages.update(
                value
                for value in (
                    expected.old_parent_page_id,
                    expected.new_parent_page_id,
                )
                if value
            )
            post_manifest, affected_count = self._manifest(manifest_pages)
            evidence, _page = self._page_evidence(expected.target_page_id)
            receipt = build_structure_receipt(
                action=expected.action,
                source_request_sha256=request_sha256,
                source_changed=True,
                structure_changed=before != after,
                poststate={
                    **evidence,
                    "old_parent_page_id": expected.old_parent_page_id,
                    "new_parent_page_id": expected.new_parent_page_id,
                    "target_dense_ordinal": new_scope_after.index(
                        expected.target_page_id
                    )
                    + 1,
                    "subtree_count": len(descendants_after),
                    "old_scope_count": len(old_scope_after),
                    "new_scope_count": len(new_scope_after),
                    "affected_page_count": affected_count,
                    "post_manifest_sha256": post_manifest,
                },
            )
            if fault_hook is not None:
                fault_hook("before_operation")
            self._insert_operation(
                source_operation_id=source_operation_id,
                request_sha256=request_sha256,
                receipt=receipt,
                now=now,
            )
            if fault_hook is not None:
                fault_hook("before_commit")
            recovered = self._commit_or_converge(
                source_operation_id=source_operation_id,
                request_sha256=request_sha256,
                expected_prestate=expected,
            )
            result = recovered or receipt
            self._assert_identity()
            if fault_hook is not None:
                fault_hook("after_commit")
            return result
        except BaseException:
            if self._con.in_transaction:
                self._con.rollback()
            raise
        finally:
            self._write_context = 0
            self._now_value = 0


__all__ = [
    "LegacyPageStructureBoundsExceeded",
    "LegacyPageStructureManualAuditRequired",
    "LegacyPageStructureOperationConflict",
    "LegacyPageStructureSourceAdapter",
    "LegacyPageStructureSourceConflict",
]
