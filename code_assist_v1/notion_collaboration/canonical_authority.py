"""Versioned canonical Notion collaboration authority for A0B-P1.

This service opens only an exact ``2026082401`` workspace catalog.  Every
mutation uses one ``BEGIN IMMEDIATE`` transaction and commits the domain
change, immutable operation result, durable event, audit row, and outbox row
together.  It never scans account directories and has no legacy-shard write
path.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable, Iterator, Mapping, Sequence

from code_assist_v1 import notion_workspace_collaboration_migration as migration
from code_assist_v1 import notion_workspace_healing_migration as migration_core


AUTHORITY_CONTRACT = "code_assist.notion.canonical_collaboration_authority.v1"
ROLE_LEVEL = {"view": 1, "comment": 2, "edit": 3, "owner": 4}
WORKSPACE_EDIT_ROLES = {"member", "membership_admin", "owner"}
WORKSPACE_ADMIN_ROLES = {"membership_admin", "owner"}
FAULT_POINTS = {
    None,
    "after_domain",
    "after_event",
    "after_audit",
    "after_outbox",
    "after_operation",
}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$", re.ASCII)
_OPERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,199}$", re.ASCII)
_GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$", re.ASCII)


class AuthorityUnavailable(RuntimeError):
    """The exact canonical catalog cannot be used."""


class NotFoundOrDenied(PermissionError):
    """Shared non-oracle response for absent and unauthorized content."""


class EpochConflict(RuntimeError):
    def __init__(self, workspace_epoch: int, document_epoch: int | None = None) -> None:
        self.workspace_epoch = int(workspace_epoch)
        self.document_epoch = None if document_epoch is None else int(document_epoch)
        super().__init__("stale access epoch")


class RevisionConflict(RuntimeError):
    def __init__(self, current_revision: int) -> None:
        self.current_revision = int(current_revision)
        super().__init__("stale document revision")


class OperationConflict(RuntimeError):
    """An operation ID was reused with a different canonical request."""


class InjectedFailure(RuntimeError):
    """Test-only failure used to prove transaction rollback."""


@dataclass(frozen=True, slots=True)
class AccessBinding:
    subject_id: str
    workspace_id: str
    document_id: str
    role: str
    workspace_epoch: int
    document_epoch: int
    document_sequence: int


@dataclass(frozen=True, slots=True)
class QueuedFrame:
    subject_id: str
    workspace_id: str
    document_id: str
    workspace_epoch: int
    document_epoch: int
    document_sequence: int
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    delivered: bool
    reason: str
    payload: Mapping[str, Any] | None
    document_sequence: int | None

    def descriptor(self) -> dict[str, Any]:
        return {
            "delivered": self.delivered,
            "reason": self.reason,
            "payload": None if self.payload is None else dict(self.payload),
            "document_sequence": self.document_sequence,
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:32]}"


def _identifier(value: str | None, *, field: str) -> str:
    item = str(value or "").strip()
    if _ID_RE.fullmatch(item) is None:
        raise ValueError(f"valid {field} required")
    return item


def _operation_id(value: str | None) -> str:
    item = str(value or "").strip()
    if _OPERATION_RE.fullmatch(item) is None:
        raise ValueError("valid operation_id required")
    return item


def _positive(value: int, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"valid {field} required")
    return value


def _role(value: str, *, owner: bool = False) -> str:
    item = str(value or "").strip().lower()
    allowed = {"view", "comment", "edit"} | ({"owner"} if owner else set())
    if item not in allowed:
        raise ValueError("valid role required")
    return item


def _request_sha256(actor: str, action: str, request: Mapping[str, Any]) -> str:
    return _sha256(
        _canonical_json(
            {"actor_subject_id": actor, "action": action, "request": dict(request)}
        )
    )


def _trip(selected: str | None, point: str) -> None:
    if selected not in FAULT_POINTS:
        raise ValueError("unknown fault point")
    if selected == point:
        raise InjectedFailure(point)


def _clean_blocks(blocks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(blocks, (str, bytes)) or not isinstance(blocks, Sequence):
        raise ValueError("blocks array required")
    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    for position, raw in enumerate(blocks):
        if not isinstance(raw, Mapping):
            raise ValueError("block object required")
        block_id = _identifier(str(raw.get("id", "")), field="block_id")
        if block_id in ids:
            raise ValueError("duplicate block_id")
        ids.add(block_id)
        block_type = str(raw.get("type", raw.get("block_type", "text"))).strip()
        if not block_type or len(block_type) > 80:
            raise ValueError("valid block_type required")
        props = raw.get("props", raw.get("props_json", {}))
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except (ValueError, json.JSONDecodeError) as exc:
                raise ValueError("valid block props required") from exc
        if not isinstance(props, Mapping):
            raise ValueError("block props object required")
        result.append(
            {
                "id": block_id,
                "position": position,
                "type": block_type,
                "content": str(raw.get("content", "")),
                "props": dict(props),
            }
        )
    return result


def _content_descriptor(
    *, title: str, icon: str, cover: str, settings: Mapping[str, Any], blocks: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    return {
        "title": title,
        "icon": icon,
        "cover": cover,
        "settings": dict(settings),
        "blocks": [dict(block) for block in blocks],
    }


class CanonicalAuthority:
    """Service-layer facade over one exact collaboration workspace catalog."""

    def __init__(self, catalog_path: Path, user_data_root: Path) -> None:
        self.catalog_path = catalog_path
        self.user_data_root = user_data_root

    @classmethod
    def open(
        cls,
        catalog_path: str | os.PathLike[str],
        user_data_root: str | os.PathLike[str],
    ) -> "CanonicalAuthority":
        try:
            catalog, root = migration_core._resolved_target(catalog_path, user_data_root)
            con = sqlite3.connect(f"{catalog.as_uri()}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            try:
                con.execute("PRAGMA query_only=ON")
                inspection = migration_core._inspect(con, migration._contract())
                if inspection["state"] != "already_current":
                    raise AuthorityUnavailable("collaboration schema is not current")
            finally:
                con.close()
        except AuthorityUnavailable:
            raise
        except (OSError, sqlite3.Error, migration.MigrationRefused, RuntimeError) as exc:
            raise AuthorityUnavailable("canonical collaboration authority is unavailable") from exc
        return cls(catalog, root)

    def _connect(self, *, query_only: bool) -> sqlite3.Connection:
        try:
            if query_only:
                con = sqlite3.connect(
                    f"{self.catalog_path.as_uri()}?mode=ro",
                    uri=True,
                    timeout=5.0,
                    isolation_level=None,
                )
            else:
                con = sqlite3.connect(
                    self.catalog_path, timeout=5.0, isolation_level=None
                )
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=5000")
            if query_only:
                con.execute("PRAGMA query_only=ON")
            marker = con.execute(
                "SELECT name,checksum,actual_schema_sha256 "
                "FROM workspace_schema_migrations WHERE version=?",
                (migration.TARGET_VERSION,),
            ).fetchone()
            if marker is None or tuple(marker) != (
                migration.TARGET_NAME,
                migration.TARGET_CHECKSUM,
                migration.TARGET_ACTUAL_SCHEMA_SHA256,
            ):
                con.close()
                raise AuthorityUnavailable("collaboration schema marker mismatch")
            return con
        except AuthorityUnavailable:
            raise
        except sqlite3.Error as exc:
            raise AuthorityUnavailable("canonical collaboration authority is unavailable") from exc

    @contextmanager
    def _reader(self) -> Iterator[sqlite3.Connection]:
        con = self._connect(query_only=True)
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        con = self._connect(query_only=False)
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def _workspace(con: sqlite3.Connection, workspace_id: str) -> sqlite3.Row:
        row = con.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
        if row is None:
            raise NotFoundOrDenied("not found or permission denied")
        return row

    @staticmethod
    def _document(con: sqlite3.Connection, document_id: str) -> sqlite3.Row:
        row = con.execute(
            "SELECT * FROM workspace_collaboration_documents WHERE id=?",
            (document_id,),
        ).fetchone()
        if row is None:
            raise NotFoundOrDenied("not found or permission denied")
        return row

    @staticmethod
    def _workspace_role(
        con: sqlite3.Connection, workspace_id: str, subject_id: str
    ) -> str | None:
        row = con.execute(
            "SELECT role FROM workspace_memberships "
            "WHERE workspace_id=? AND subject_id=? AND revoked_at IS NULL",
            (workspace_id, subject_id),
        ).fetchone()
        return None if row is None else str(row["role"])

    @classmethod
    def _effective_role(
        cls,
        con: sqlite3.Connection,
        workspace_id: str,
        document_id: str,
        subject_id: str,
    ) -> str | None:
        workspace_role = cls._workspace_role(con, workspace_id, subject_id)
        roles: list[str] = []
        if workspace_role == "owner":
            roles.append("owner")
        elif workspace_role in WORKSPACE_EDIT_ROLES:
            roles.append("edit")
        grant = con.execute(
            "SELECT role FROM workspace_collaboration_page_grants "
            "WHERE document_id=? AND subject_id=? AND active=1",
            (document_id, subject_id),
        ).fetchone()
        if grant is not None:
            roles.append(str(grant["role"]))
        return max(roles, key=ROLE_LEVEL.__getitem__) if roles else None

    @classmethod
    def _access(
        cls,
        con: sqlite3.Connection,
        subject_id: str,
        document_id: str,
        *,
        minimum_role: str,
        expected_workspace_epoch: int | None = None,
        expected_document_epoch: int | None = None,
    ) -> tuple[sqlite3.Row, sqlite3.Row, str]:
        document = cls._document(con, document_id)
        workspace = cls._workspace(con, str(document["workspace_id"]))
        effective = cls._effective_role(
            con, str(workspace["id"]), document_id, subject_id
        )
        if effective is None or ROLE_LEVEL[effective] < ROLE_LEVEL[minimum_role]:
            raise NotFoundOrDenied("not found or permission denied")
        workspace_epoch = int(workspace["security_epoch"])
        document_epoch = int(document["access_epoch"])
        if (
            expected_workspace_epoch is not None
            and _positive(expected_workspace_epoch, field="workspace_epoch")
            != workspace_epoch
        ) or (
            expected_document_epoch is not None
            and _positive(expected_document_epoch, field="document_epoch")
            != document_epoch
        ):
            raise EpochConflict(workspace_epoch, document_epoch)
        return workspace, document, effective

    @classmethod
    def _require_admin(
        cls, con: sqlite3.Connection, actor: str, workspace_id: str
    ) -> sqlite3.Row:
        workspace = cls._workspace(con, workspace_id)
        if cls._workspace_role(con, workspace_id, actor) not in WORKSPACE_ADMIN_ROLES:
            raise NotFoundOrDenied("not found or permission denied")
        return workspace

    @staticmethod
    def _replay(
        con: sqlite3.Connection,
        *,
        actor: str,
        action: str,
        operation_id: str,
        request_sha256: str,
        authorize: Callable[[], None],
    ) -> dict[str, Any] | None:
        row = con.execute(
            "SELECT request_sha256,result_json FROM workspace_collaboration_operations "
            "WHERE actor_subject_id=? AND action=? AND operation_id=?",
            (actor, action, operation_id),
        ).fetchone()
        if row is None:
            return None
        if str(row["request_sha256"]) != request_sha256:
            raise OperationConflict("operation_id reused with a different request")
        authorize()
        try:
            value = json.loads(str(row["result_json"]))
        except (ValueError, json.JSONDecodeError) as exc:
            raise AuthorityUnavailable("stored operation result is invalid") from exc
        if not isinstance(value, dict):
            raise AuthorityUnavailable("stored operation result is invalid")
        return value

    @staticmethod
    def _finish(
        con: sqlite3.Connection,
        *,
        actor: str,
        action: str,
        operation_id: str,
        request_sha256: str,
        workspace_id: str,
        document_id: str | None,
        workspace_epoch: int,
        document_epoch: int | None,
        document_sequence: int | None,
        event_type: str,
        event_payload: Mapping[str, Any],
        result: Mapping[str, Any],
        fault: str | None,
        runtime_generation_id: str | None = None,
    ) -> dict[str, Any]:
        _trip(fault, "after_domain")
        now = int(time.time())
        event_id = _stable_id("cevt", actor, action, operation_id)
        audit_id = _stable_id("caud", actor, action, operation_id)
        outbox_id = _stable_id("cout", actor, action, operation_id)
        payload_json = _canonical_json(dict(event_payload))
        con.execute(
            "INSERT INTO workspace_collaboration_events("
            "id,workspace_id,document_id,document_sequence,event_type,actor_subject_id,"
            "operation_id,workspace_epoch,document_epoch,payload_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                workspace_id,
                document_id,
                document_sequence,
                event_type,
                actor,
                operation_id,
                workspace_epoch,
                document_epoch,
                payload_json,
                now,
            ),
        )
        _trip(fault, "after_event")
        con.execute(
            "INSERT INTO workspace_collaboration_audit_events("
            "id,workspace_id,document_id,action,actor_subject_id,operation_id,payload_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                audit_id,
                workspace_id,
                document_id,
                action,
                actor,
                operation_id,
                payload_json,
                now,
            ),
        )
        _trip(fault, "after_audit")
        con.execute(
            "INSERT INTO workspace_collaboration_outbox("
            "id,workspace_id,document_id,event_type,actor_subject_id,operation_id,"
            "workspace_epoch,document_epoch,payload_json,created_at,delivered_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
            (
                outbox_id,
                workspace_id,
                document_id,
                event_type,
                actor,
                operation_id,
                workspace_epoch,
                document_epoch,
                payload_json,
                now,
            ),
        )
        _trip(fault, "after_outbox")
        stable = dict(result)
        stable.update(
            {
                "event_id": event_id,
                "audit_id": audit_id,
                "outbox_id": outbox_id,
                "operation_id": operation_id,
                "request_sha256": request_sha256,
            }
        )
        con.execute(
            "INSERT INTO workspace_collaboration_operations("
            "actor_subject_id,action,operation_id,request_sha256,workspace_id,document_id,"
            "result_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                actor,
                action,
                operation_id,
                request_sha256,
                workspace_id,
                document_id,
                _canonical_json(stable),
                now,
            ),
        )
        if runtime_generation_id is not None:
            generation = str(runtime_generation_id)
            if _GENERATION_RE.fullmatch(generation) is None:
                raise ValueError("valid runtime_generation_id required")
            con.execute(
                "INSERT INTO workspace_collaboration_cutovers("
                "realm,deployment_generation_id,workspace_id,activated_at,"
                "runtime_write_count,first_runtime_write_at,last_runtime_write_at) "
                "VALUES('development',?,?,?,1,?,?) "
                "ON CONFLICT(realm,deployment_generation_id,workspace_id) DO UPDATE SET "
                "runtime_write_count=runtime_write_count+1,"
                "first_runtime_write_at=COALESCE(first_runtime_write_at,excluded.first_runtime_write_at),"
                "last_runtime_write_at=excluded.last_runtime_write_at",
                (generation, workspace_id, now, now, now),
            )
        _trip(fault, "after_operation")
        return stable

    @staticmethod
    def _insert_blocks(
        con: sqlite3.Connection,
        document_id: str,
        blocks: Sequence[Mapping[str, Any]],
        *,
        now: int,
    ) -> None:
        con.executemany(
            "INSERT INTO workspace_collaboration_blocks("
            "id,document_id,position,block_type,content,props_json,row_version,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,1,?,?)",
            [
                (
                    str(block["id"]),
                    document_id,
                    int(block["position"]),
                    str(block["type"]),
                    str(block["content"]),
                    _canonical_json(block["props"]),
                    now,
                    now,
                )
                for block in blocks
            ],
        )

    @staticmethod
    def _snapshot_on_connection(
        con: sqlite3.Connection, document: sqlite3.Row
    ) -> dict[str, Any]:
        blocks = [
            {
                "id": str(row["id"]),
                "position": int(row["position"]),
                "type": str(row["block_type"]),
                "content": str(row["content"]),
                "props": json.loads(str(row["props_json"])),
            }
            for row in con.execute(
                "SELECT * FROM workspace_collaboration_blocks "
                "WHERE document_id=? ORDER BY position,id",
                (str(document["id"]),),
            ).fetchall()
        ]
        return _content_descriptor(
            title=str(document["title"]),
            icon=str(document["icon"]),
            cover=str(document["cover"]),
            settings=json.loads(str(document["settings_json"])),
            blocks=blocks,
        )

    @staticmethod
    def _record_document_creation_successor_state(
        con: sqlite3.Connection,
        *,
        workspace_id: str,
        document_id: str,
        content_sha256: str,
        actor_subject_id: str,
        operation_id: str,
        created_at: int,
    ) -> None:
        """Successor-schema hook committed atomically with document creation."""

    def create_document(
        self,
        actor_subject_id: str,
        workspace_id: str,
        document_id: str,
        *,
        title: str,
        blocks: Sequence[Mapping[str, Any]],
        expected_workspace_epoch: int,
        operation_id: str,
        icon: str = "",
        cover: str = "",
        settings: Mapping[str, Any] | None = None,
        parent_document_id: str | None = None,
        fault: str | None = None,
        runtime_generation_id: str | None = None,
    ) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        workspace = _identifier(workspace_id, field="workspace_id")
        document = _identifier(document_id, field="document_id")
        parent = None if parent_document_id is None else _identifier(parent_document_id, field="parent_document_id")
        clean_title = str(title).strip()
        if not clean_title or len(clean_title) > 1000:
            raise ValueError("valid title required")
        clean_settings = dict(settings or {})
        clean_blocks = _clean_blocks(blocks)
        epoch = _positive(expected_workspace_epoch, field="workspace_epoch")
        op_id = _operation_id(operation_id)
        action = "collaboration.document_created"
        request = {
            "workspace_id": workspace,
            "document_id": document,
            "parent_document_id": parent,
            "title": clean_title,
            "icon": str(icon),
            "cover": str(cover),
            "settings": clean_settings,
            "blocks": clean_blocks,
            "expected_workspace_epoch": epoch,
        }
        request_hash = _request_sha256(actor, action, request)
        with self._transaction() as con:
            replay = self._replay(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=lambda: self._require_create_access(con, actor, workspace, epoch),
            )
            if replay is not None:
                return replay
            workspace_row = self._require_create_access(con, actor, workspace, epoch)
            if con.execute(
                "SELECT 1 FROM workspace_collaboration_documents WHERE id=?", (document,)
            ).fetchone():
                raise ValueError("document already exists")
            if parent is not None:
                parent_row = self._document(con, parent)
                if str(parent_row["workspace_id"]) != workspace:
                    raise ValueError("parent workspace mismatch")
            now = int(time.time())
            descriptor = _content_descriptor(
                title=clean_title,
                icon=str(icon),
                cover=str(cover),
                settings=clean_settings,
                blocks=clean_blocks,
            )
            content_hash = _sha256(_canonical_json(descriptor))
            con.execute(
                "INSERT INTO workspace_collaboration_documents("
                "id,workspace_id,legacy_owner_subject_id,parent_document_id,title,icon,cover,"
                "settings_json,position,archived,archived_at,revision,document_sequence,"
                "access_epoch,row_version,content_sha256,imported_snapshot_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,0,0,NULL,1,1,1,1,?,NULL,?,?)",
                (
                    document,
                    workspace,
                    actor,
                    parent,
                    clean_title,
                    str(icon),
                    str(cover),
                    _canonical_json(clean_settings),
                    content_hash,
                    now,
                    now,
                ),
            )
            self._insert_blocks(con, document, clean_blocks, now=now)
            con.execute(
                "INSERT INTO workspace_collaboration_revisions("
                "document_id,revision,document_sequence,content_sha256,snapshot_json,"
                "actor_subject_id,operation_id,created_at) VALUES(?,1,1,?,?,?,?,?)",
                (document, content_hash, _canonical_json(descriptor), actor, op_id, now),
            )
            self._record_document_creation_successor_state(
                con,
                workspace_id=workspace,
                document_id=document,
                content_sha256=content_hash,
                actor_subject_id=actor,
                operation_id=op_id,
                created_at=now,
            )
            result = {
                "workspace_id": workspace,
                "document_id": document,
                "revision": 1,
                "document_sequence": 1,
                "workspace_epoch": int(workspace_row["security_epoch"]),
                "document_epoch": 1,
                "content_sha256": content_hash,
            }
            return self._finish(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=workspace,
                document_id=document,
                workspace_epoch=epoch,
                document_epoch=1,
                document_sequence=1,
                event_type="document.created",
                event_payload=result,
                result=result,
                fault=fault,
                runtime_generation_id=runtime_generation_id,
            )

    @classmethod
    def _require_create_access(
        cls, con: sqlite3.Connection, actor: str, workspace_id: str, expected_epoch: int
    ) -> sqlite3.Row:
        workspace = cls._workspace(con, workspace_id)
        if int(workspace["security_epoch"]) != expected_epoch:
            raise EpochConflict(int(workspace["security_epoch"]))
        if cls._workspace_role(con, workspace_id, actor) not in WORKSPACE_EDIT_ROLES:
            raise NotFoundOrDenied("not found or permission denied")
        return workspace

    def save_document(
        self,
        actor_subject_id: str,
        document_id: str,
        *,
        title: str,
        blocks: Sequence[Mapping[str, Any]],
        expected_revision: int,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        icon: str = "",
        cover: str = "",
        settings: Mapping[str, Any] | None = None,
        fault: str | None = None,
        runtime_generation_id: str | None = None,
    ) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        document = _identifier(document_id, field="document_id")
        clean_title = str(title).strip()
        if not clean_title or len(clean_title) > 1000:
            raise ValueError("valid title required")
        clean_blocks = _clean_blocks(blocks)
        clean_settings = dict(settings or {})
        revision = _positive(expected_revision, field="revision")
        workspace_epoch = _positive(expected_workspace_epoch, field="workspace_epoch")
        document_epoch = _positive(expected_document_epoch, field="document_epoch")
        op_id = _operation_id(operation_id)
        action = "collaboration.document_saved"
        request = {
            "document_id": document,
            "title": clean_title,
            "icon": str(icon),
            "cover": str(cover),
            "settings": clean_settings,
            "blocks": clean_blocks,
            "expected_revision": revision,
            "expected_workspace_epoch": workspace_epoch,
            "expected_document_epoch": document_epoch,
        }
        request_hash = _request_sha256(actor, action, request)
        with self._transaction() as con:
            def authorize() -> None:
                self._access(
                    con,
                    actor,
                    document,
                    minimum_role="edit",
                    expected_workspace_epoch=workspace_epoch,
                    expected_document_epoch=document_epoch,
                )

            replay = self._replay(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=authorize,
            )
            if replay is not None:
                return replay
            workspace_row, document_row, _ = self._access(
                con,
                actor,
                document,
                minimum_role="edit",
                expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=document_epoch,
            )
            if int(document_row["revision"]) != revision:
                raise RevisionConflict(int(document_row["revision"]))
            next_revision = revision + 1
            next_sequence = int(document_row["document_sequence"]) + 1
            now = int(time.time())
            descriptor = _content_descriptor(
                title=clean_title,
                icon=str(icon),
                cover=str(cover),
                settings=clean_settings,
                blocks=clean_blocks,
            )
            content_hash = _sha256(_canonical_json(descriptor))
            con.execute(
                "UPDATE workspace_collaboration_documents SET "
                "title=?,icon=?,cover=?,settings_json=?,revision=?,document_sequence=?,"
                "row_version=row_version+1,content_sha256=?,updated_at=? WHERE id=?",
                (
                    clean_title,
                    str(icon),
                    str(cover),
                    _canonical_json(clean_settings),
                    next_revision,
                    next_sequence,
                    content_hash,
                    now,
                    document,
                ),
            )
            con.execute(
                "DELETE FROM workspace_collaboration_blocks WHERE document_id=?",
                (document,),
            )
            self._insert_blocks(con, document, clean_blocks, now=now)
            con.execute(
                "INSERT INTO workspace_collaboration_revisions("
                "document_id,revision,document_sequence,content_sha256,snapshot_json,"
                "actor_subject_id,operation_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    document,
                    next_revision,
                    next_sequence,
                    content_hash,
                    _canonical_json(descriptor),
                    actor,
                    op_id,
                    now,
                ),
            )
            result = {
                "workspace_id": str(workspace_row["id"]),
                "document_id": document,
                "revision": next_revision,
                "document_sequence": next_sequence,
                "workspace_epoch": workspace_epoch,
                "document_epoch": document_epoch,
                "content_sha256": content_hash,
            }
            return self._finish(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=str(workspace_row["id"]),
                document_id=document,
                workspace_epoch=workspace_epoch,
                document_epoch=document_epoch,
                document_sequence=next_sequence,
                event_type="document.saved",
                event_payload=result,
                result=result,
                fault=fault,
                runtime_generation_id=runtime_generation_id,
            )

    def add_comment(
        self,
        actor_subject_id: str,
        document_id: str,
        comment_id: str,
        *,
        content: str,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        block_id: str | None = None,
        parent_comment_id: str | None = None,
        anchor: Mapping[str, Any] | None = None,
        fault: str | None = None,
        runtime_generation_id: str | None = None,
    ) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        document = _identifier(document_id, field="document_id")
        comment = _identifier(comment_id, field="comment_id")
        block = None if block_id is None else _identifier(block_id, field="block_id")
        parent = None if parent_comment_id is None else _identifier(parent_comment_id, field="parent_comment_id")
        text = str(content).strip()
        if not text:
            raise ValueError("comment content required")
        workspace_epoch = _positive(expected_workspace_epoch, field="workspace_epoch")
        document_epoch = _positive(expected_document_epoch, field="document_epoch")
        op_id = _operation_id(operation_id)
        action = "collaboration.comment_added"
        request = {
            "document_id": document,
            "comment_id": comment,
            "block_id": block,
            "parent_comment_id": parent,
            "content": text,
            "anchor": dict(anchor or {}),
            "expected_workspace_epoch": workspace_epoch,
            "expected_document_epoch": document_epoch,
        }
        request_hash = _request_sha256(actor, action, request)
        with self._transaction() as con:
            def authorize() -> None:
                self._access(
                    con,
                    actor,
                    document,
                    minimum_role="comment",
                    expected_workspace_epoch=workspace_epoch,
                    expected_document_epoch=document_epoch,
                )

            replay = self._replay(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=authorize,
            )
            if replay is not None:
                return replay
            workspace_row, document_row, _ = self._access(
                con,
                actor,
                document,
                minimum_role="comment",
                expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=document_epoch,
            )
            now = int(time.time())
            next_sequence = int(document_row["document_sequence"]) + 1
            con.execute(
                "INSERT INTO workspace_collaboration_comments("
                "id,document_id,block_id,author_subject_id,content,anchor_json,"
                "parent_comment_id,reactions_json,resolved,row_version,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,'{}',0,1,?,?)",
                (comment, document, block, actor, text, _canonical_json(dict(anchor or {})), parent, now, now),
            )
            con.execute(
                "UPDATE workspace_collaboration_documents SET document_sequence=?,"
                "row_version=row_version+1,updated_at=? WHERE id=?",
                (next_sequence, now, document),
            )
            result = {
                "workspace_id": str(workspace_row["id"]),
                "document_id": document,
                "comment_id": comment,
                "document_sequence": next_sequence,
                "workspace_epoch": workspace_epoch,
                "document_epoch": document_epoch,
            }
            return self._finish(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=str(workspace_row["id"]),
                document_id=document,
                workspace_epoch=workspace_epoch,
                document_epoch=document_epoch,
                document_sequence=next_sequence,
                event_type="comment.added",
                event_payload={**result, "author_subject_id": actor},
                result=result,
                fault=fault,
                runtime_generation_id=runtime_generation_id,
            )

    def grant_page_access(
        self,
        actor_subject_id: str,
        document_id: str,
        target_subject_id: str,
        role: str,
        *,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        fault: str | None = None,
    ) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        document = _identifier(document_id, field="document_id")
        target = _identifier(target_subject_id, field="target_subject_id")
        grant_role = _role(role)
        workspace_epoch = _positive(expected_workspace_epoch, field="workspace_epoch")
        document_epoch = _positive(expected_document_epoch, field="document_epoch")
        op_id = _operation_id(operation_id)
        action = "collaboration.page_grant_set"
        request = {
            "document_id": document,
            "target_subject_id": target,
            "role": grant_role,
            "expected_workspace_epoch": workspace_epoch,
            "expected_document_epoch": document_epoch,
        }
        request_hash = _request_sha256(actor, action, request)
        with self._transaction() as con:
            document_row = self._document(con, document)
            workspace_id = str(document_row["workspace_id"])
            replay = self._replay(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=lambda: self._require_admin(con, actor, workspace_id),
            )
            if replay is not None:
                return replay
            workspace_row = self._require_admin(con, actor, workspace_id)
            if int(workspace_row["security_epoch"]) != workspace_epoch or int(document_row["access_epoch"]) != document_epoch:
                raise EpochConflict(int(workspace_row["security_epoch"]), int(document_row["access_epoch"]))
            now = int(time.time())
            if self._workspace_role(con, workspace_id, target) is None:
                existing_guest = con.execute(
                    "SELECT guest_epoch FROM workspace_guests WHERE workspace_id=? AND subject_id=?",
                    (workspace_id, target),
                ).fetchone()
                guest_epoch = 1 if existing_guest is None else int(existing_guest["guest_epoch"]) + 1
                con.execute(
                    "INSERT INTO workspace_guests(workspace_id,subject_id,guest_epoch,"
                    "invited_by_subject_id,created_at,updated_at,revoked_at) VALUES(?,?,?,?,?,?,NULL) "
                    "ON CONFLICT(workspace_id,subject_id) DO UPDATE SET "
                    "guest_epoch=excluded.guest_epoch,invited_by_subject_id=excluded.invited_by_subject_id,"
                    "updated_at=excluded.updated_at,revoked_at=NULL",
                    (workspace_id, target, guest_epoch, actor, now, now),
                )
            previous = con.execute(
                "SELECT grant_epoch FROM workspace_collaboration_page_grants "
                "WHERE document_id=? AND subject_id=?",
                (document, target),
            ).fetchone()
            grant_epoch = 1 if previous is None else int(previous["grant_epoch"]) + 1
            next_document_epoch = document_epoch + 1
            con.execute(
                "INSERT INTO workspace_collaboration_page_grants("
                "document_id,subject_id,role,active,grant_epoch,granted_by_subject_id,"
                "created_at,updated_at,revoked_at) VALUES(?,?,?,1,?,?,?, ?,NULL) "
                "ON CONFLICT(document_id,subject_id) DO UPDATE SET role=excluded.role,active=1,"
                "grant_epoch=excluded.grant_epoch,granted_by_subject_id=excluded.granted_by_subject_id,"
                "updated_at=excluded.updated_at,revoked_at=NULL",
                (document, target, grant_role, grant_epoch, actor, now, now),
            )
            con.execute(
                "UPDATE workspace_collaboration_documents SET access_epoch=?,"
                "row_version=row_version+1,updated_at=? WHERE id=?",
                (next_document_epoch, now, document),
            )
            result = {
                "workspace_id": workspace_id,
                "document_id": document,
                "target_subject_id": target,
                "role": grant_role,
                "workspace_epoch": workspace_epoch,
                "document_epoch": next_document_epoch,
                "grant_epoch": grant_epoch,
            }
            return self._finish(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=workspace_id,
                document_id=document,
                workspace_epoch=workspace_epoch,
                document_epoch=next_document_epoch,
                document_sequence=None,
                event_type="permission.granted",
                event_payload=result,
                result=result,
                fault=fault,
            )

    def revoke_subject(
        self,
        actor_subject_id: str,
        workspace_id: str,
        target_subject_id: str,
        *,
        expected_workspace_epoch: int,
        operation_id: str,
        fault: str | None = None,
    ) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        workspace = _identifier(workspace_id, field="workspace_id")
        target = _identifier(target_subject_id, field="target_subject_id")
        expected_epoch = _positive(expected_workspace_epoch, field="workspace_epoch")
        op_id = _operation_id(operation_id)
        action = "collaboration.subject_revoked"
        request = {
            "workspace_id": workspace,
            "target_subject_id": target,
            "expected_workspace_epoch": expected_epoch,
        }
        request_hash = _request_sha256(actor, action, request)
        with self._transaction() as con:
            replay = self._replay(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=lambda: self._require_admin(con, actor, workspace),
            )
            if replay is not None:
                return replay
            workspace_row = self._require_admin(con, actor, workspace)
            if int(workspace_row["security_epoch"]) != expected_epoch:
                raise EpochConflict(int(workspace_row["security_epoch"]))
            if target == str(workspace_row["owner_subject_id"]):
                raise ValueError("workspace owner cannot be revoked")
            membership = con.execute(
                "SELECT role FROM workspace_memberships WHERE workspace_id=? AND subject_id=? "
                "AND revoked_at IS NULL",
                (workspace, target),
            ).fetchone()
            guest = con.execute(
                "SELECT 1 FROM workspace_guests WHERE workspace_id=? AND subject_id=? "
                "AND revoked_at IS NULL",
                (workspace, target),
            ).fetchone()
            grants = con.execute(
                "SELECT d.id,d.access_epoch FROM workspace_collaboration_documents d "
                "JOIN workspace_collaboration_page_grants g ON g.document_id=d.id "
                "WHERE d.workspace_id=? AND g.subject_id=? AND g.active=1 ORDER BY d.id",
                (workspace, target),
            ).fetchall()
            if membership is None and guest is None and not grants:
                raise NotFoundOrDenied("not found or permission denied")
            if membership is not None and str(membership["role"]) == "owner":
                raise ValueError("workspace owner cannot be revoked")
            now = int(time.time())
            affected: dict[str, int] = {}
            for page in grants:
                next_epoch = int(page["access_epoch"]) + 1
                affected[str(page["id"])] = next_epoch
                con.execute(
                    "UPDATE workspace_collaboration_page_grants SET active=0,"
                    "grant_epoch=grant_epoch+1,updated_at=?,revoked_at=? "
                    "WHERE document_id=? AND subject_id=? AND active=1",
                    (now, now, str(page["id"]), target),
                )
                con.execute(
                    "UPDATE workspace_collaboration_documents SET access_epoch=?,"
                    "row_version=row_version+1,updated_at=? WHERE id=?",
                    (next_epoch, now, str(page["id"])),
                )
            con.execute(
                "UPDATE workspace_guests SET revoked_at=?,updated_at=?,guest_epoch=guest_epoch+1 "
                "WHERE workspace_id=? AND subject_id=? AND revoked_at IS NULL",
                (now, now, workspace, target),
            )
            con.execute(
                "UPDATE workspace_memberships SET revoked_at=?,updated_at=?,"
                "membership_epoch=membership_epoch+1 WHERE workspace_id=? AND subject_id=? "
                "AND revoked_at IS NULL",
                (now, now, workspace, target),
            )
            next_workspace_epoch = expected_epoch + 1
            con.execute(
                "UPDATE workspaces SET security_epoch=?,row_version=row_version+1,updated_at=? "
                "WHERE id=?",
                (next_workspace_epoch, now, workspace),
            )
            result = {
                "workspace_id": workspace,
                "target_subject_id": target,
                "workspace_epoch": next_workspace_epoch,
                "affected_document_epochs": affected,
                "state": "revoked",
                "http_status": 200,
                "gateway_notification": "durable_outbox_pending",
                "gateway_fence": "authoritative_send_time_epoch_check",
            }
            return self._finish(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=workspace,
                document_id=None,
                workspace_epoch=next_workspace_epoch,
                document_epoch=None,
                document_sequence=None,
                event_type="permission.revoked",
                event_payload=result,
                result=result,
                fault=fault,
            )

    def read_document(
        self,
        subject_id: str,
        document_id: str,
        *,
        expected_workspace_epoch: int | None = None,
        expected_document_epoch: int | None = None,
    ) -> dict[str, Any]:
        subject = _identifier(subject_id, field="subject_id")
        document = _identifier(document_id, field="document_id")
        with self._reader() as con:
            workspace_row, document_row, role = self._access(
                con,
                subject,
                document,
                minimum_role="view",
                expected_workspace_epoch=expected_workspace_epoch,
                expected_document_epoch=expected_document_epoch,
            )
            snapshot = self._snapshot_on_connection(con, document_row)
            comments = [
                {
                    "id": str(row["id"]),
                    "block_id": row["block_id"],
                    "author_subject_id": str(row["author_subject_id"]),
                    "content": str(row["content"]),
                    "anchor": json.loads(str(row["anchor_json"])),
                    "parent_comment_id": row["parent_comment_id"],
                    "reactions": json.loads(str(row["reactions_json"])),
                    "resolved": bool(row["resolved"]),
                    "created_at": int(row["created_at"]),
                    "updated_at": int(row["updated_at"]),
                }
                for row in con.execute(
                    "SELECT * FROM workspace_collaboration_comments "
                    "WHERE document_id=? ORDER BY created_at,id",
                    (document,),
                ).fetchall()
            ]
            return {
                "contract": AUTHORITY_CONTRACT,
                "workspace_id": str(workspace_row["id"]),
                "document_id": document,
                "role": role,
                "workspace_epoch": int(workspace_row["security_epoch"]),
                "document_epoch": int(document_row["access_epoch"]),
                "revision": int(document_row["revision"]),
                "document_sequence": int(document_row["document_sequence"]),
                "content_sha256": str(document_row["content_sha256"]),
                "parent_document_id": document_row["parent_document_id"],
                "position": int(document_row["position"]),
                "archived": bool(document_row["archived"]),
                "created_at": int(document_row["created_at"]),
                "updated_at": int(document_row["updated_at"]),
                **snapshot,
                "comments": comments,
            }

    def binding(
        self, subject_id: str, document_id: str, *, minimum_role: str = "view"
    ) -> AccessBinding:
        subject = _identifier(subject_id, field="subject_id")
        document = _identifier(document_id, field="document_id")
        required = _role(minimum_role)
        with self._reader() as con:
            workspace, page, effective = self._access(
                con, subject, document, minimum_role=required
            )
            return AccessBinding(
                subject,
                str(workspace["id"]),
                document,
                effective,
                int(workspace["security_epoch"]),
                int(page["access_epoch"]),
                int(page["document_sequence"]),
            )

    def authorize_frame_send(self, frame: QueuedFrame) -> DeliveryResult:
        try:
            binding = self.binding(frame.subject_id, frame.document_id)
            if (
                binding.workspace_id != frame.workspace_id
                or binding.workspace_epoch != frame.workspace_epoch
                or binding.document_epoch != frame.document_epoch
            ):
                raise NotFoundOrDenied("not found or permission denied")
            return DeliveryResult(
                True,
                "authorized_current_epoch",
                dict(frame.payload),
                frame.document_sequence,
            )
        except (AuthorityUnavailable, NotFoundOrDenied, sqlite3.Error):
            return DeliveryResult(False, "denied_or_authority_unavailable", None, None)

    def operation_result(
        self, actor_subject_id: str, action: str, operation_id: str
    ) -> dict[str, Any] | None:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        op_id = _operation_id(operation_id)
        with self._reader() as con:
            row = con.execute(
                "SELECT result_json FROM workspace_collaboration_operations "
                "WHERE actor_subject_id=? AND action=? AND operation_id=?",
                (actor, str(action), op_id),
            ).fetchone()
            return None if row is None else json.loads(str(row["result_json"]))

    def counts(self) -> dict[str, int]:
        names = (
            "workspace_collaboration_documents",
            "workspace_collaboration_blocks",
            "workspace_collaboration_comments",
            "workspace_collaboration_page_grants",
            "workspace_collaboration_databases",
            "workspace_collaboration_database_rows",
            "workspace_collaboration_database_cells",
            "workspace_collaboration_revisions",
            "workspace_collaboration_operations",
            "workspace_collaboration_events",
            "workspace_collaboration_audit_events",
            "workspace_collaboration_outbox",
            "workspace_collaboration_imports",
            "workspace_collaboration_cutovers",
        )
        with self._reader() as con:
            return {
                name: int(con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
                for name in names
            }

    def rows(self, table: str, *, operation_id: str | None = None) -> list[dict[str, Any]]:
        allowed = {
            "workspace_collaboration_events",
            "workspace_collaboration_audit_events",
            "workspace_collaboration_outbox",
            "workspace_collaboration_operations",
        }
        if table not in allowed:
            raise ValueError("unsupported evidence table")
        query = f'SELECT * FROM "{table}"'
        parameters: tuple[str, ...] = ()
        if operation_id is not None:
            query += " WHERE operation_id=?"
            parameters = (_operation_id(operation_id),)
        query += " ORDER BY created_at"
        with self._reader() as con:
            return [dict(row) for row in con.execute(query, parameters).fetchall()]

    def cutover_state(self, generation_id: str, workspace_id: str) -> dict[str, Any]:
        generation = str(generation_id)
        if _GENERATION_RE.fullmatch(generation) is None:
            raise ValueError("valid generation_id required")
        workspace = _identifier(workspace_id, field="workspace_id")
        with self._reader() as con:
            row = con.execute(
                "SELECT * FROM workspace_collaboration_cutovers "
                "WHERE realm='development' AND deployment_generation_id=? AND workspace_id=?",
                (generation, workspace),
            ).fetchone()
        count = 0 if row is None else int(row["runtime_write_count"])
        return {
            "realm": "development",
            "deployment_generation_id": generation,
            "workspace_id": workspace,
            "runtime_write_count": count,
            "rollback_mode": (
                "disable_candidate_and_return_to_unchanged_legacy"
                if count == 0
                else "roll_forward_or_restore_candidate_never_overlay_from_legacy"
            ),
            "schema_downgrade_required": False,
        }

    def integrity(self) -> dict[str, Any]:
        with self._reader() as con:
            quick = str(con.execute("PRAGMA quick_check").fetchone()[0])
            foreign_keys = len(con.execute("PRAGMA foreign_key_check").fetchall())
            marker = con.execute(
                "SELECT version,name,checksum,actual_schema_sha256 "
                "FROM workspace_schema_migrations"
            ).fetchall()
            return {
                "contract": AUTHORITY_CONTRACT,
                "quick_check": quick,
                "foreign_key_error_count": foreign_keys,
                "schema_version": int(marker[0]["version"]) if len(marker) == 1 else None,
                "schema_marker_count": len(marker),
                "request_time_directory_scan_count": 0,
                "legacy_source_database_open_count": 0,
                "dual_write_count": 0,
            }


def queue_frame(
    authority: CanonicalAuthority,
    subject_id: str,
    document_id: str,
    payload: Mapping[str, Any],
) -> QueuedFrame:
    binding = authority.binding(subject_id, document_id)
    return QueuedFrame(
        binding.subject_id,
        binding.workspace_id,
        binding.document_id,
        binding.workspace_epoch,
        binding.document_epoch,
        binding.document_sequence,
        dict(payload),
    )


def execute_command_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """Small argv/JSON-safe process boundary used by restart rehearsals."""

    if not isinstance(document, Mapping):
        raise ValueError("command object required")
    authority = CanonicalAuthority.open(
        str(document.get("catalog_path", "")), str(document.get("user_data_root", ""))
    )
    command = str(document.get("command", ""))
    arguments = document.get("arguments")
    if not isinstance(arguments, Mapping):
        raise ValueError("command arguments object required")
    if command == "save_document":
        return authority.save_document(**dict(arguments))
    if command == "read_document":
        return authority.read_document(**dict(arguments))
    if command == "revoke_subject":
        return authority.revoke_subject(**dict(arguments))
    if command == "integrity":
        return authority.integrity()
    raise ValueError("unsupported command")


__all__ = [
    "AUTHORITY_CONTRACT",
    "AccessBinding",
    "AuthorityUnavailable",
    "CanonicalAuthority",
    "DeliveryResult",
    "EpochConflict",
    "InjectedFailure",
    "NotFoundOrDenied",
    "OperationConflict",
    "QueuedFrame",
    "RevisionConflict",
    "execute_command_document",
    "queue_frame",
]
