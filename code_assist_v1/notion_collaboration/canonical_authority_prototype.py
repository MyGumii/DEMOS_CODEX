"""Unversioned, disposable A0A-P2 collaboration-authority prototype.

This module is deliberately isolated from the product stores and migrations.
It accepts only a marker-bound directory below the operating-system temporary
root, creates no service or port, and never imports runtime configuration.  Its
purpose is to execute the A0A-P1 atomicity, idempotency, epoch, replay, and
send-time delivery-fence contracts before a production schema is allocated.
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
import tempfile
import time
from typing import Any, Callable, Iterator, Mapping
import uuid


PROTOTYPE_CONTRACT = "code_assist.notion.a0a_p2.disposable_authority.v1"
MARKER_NAME = ".a0a-p2-disposable-authority.json"
DATABASE_NAME = "a0a-p2-authority.sqlite3"
FORBIDDEN_COMPONENTS = {"knowledge", "user_data", ".runtime", ".notion-workspace-v2"}
ROLE_LEVEL = {"view": 1, "comment": 2, "edit": 3, "owner": 4}
FAULT_POINTS = {
    None,
    "after_domain",
    "after_event",
    "after_audit",
    "after_outbox",
    "after_operation",
}
_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{1,159}$")
_OPERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,199}$")


class PrototypeBoundaryError(ValueError):
    """The requested path is outside the disposable prototype boundary."""


class AuthorityUnavailable(RuntimeError):
    """The disposable canonical authority cannot be read or written."""


class NotFoundOrDenied(PermissionError):
    """Non-oracle absent/unauthorized response."""


class EpochConflict(RuntimeError):
    def __init__(self, workspace_epoch: int, page_epoch: int | None = None) -> None:
        self.workspace_epoch = int(workspace_epoch)
        self.page_epoch = None if page_epoch is None else int(page_epoch)
        super().__init__("stale access epoch")


class RevisionConflict(RuntimeError):
    def __init__(self, current_revision: int) -> None:
        self.current_revision = int(current_revision)
        super().__init__("stale document revision")


class OperationConflict(RuntimeError):
    """An operation ID was reused with a different canonical request hash."""


class InjectedFailure(RuntimeError):
    """Test-only transaction fault."""


@dataclass(frozen=True)
class AccessBinding:
    subject_id: str
    workspace_id: str
    page_id: str
    role: str
    workspace_epoch: int
    page_epoch: int
    page_sequence: int


@dataclass(frozen=True)
class QueuedFrame:
    subject_id: str
    workspace_id: str
    page_id: str
    workspace_epoch: int
    page_epoch: int
    page_sequence: int
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class DeliveryResult:
    delivered: bool
    reason: str
    payload: Mapping[str, Any] | None
    page_sequence: int | None

    def descriptor(self) -> dict[str, Any]:
        return {
            "delivered": self.delivered,
            "reason": self.reason,
            "payload": None if self.payload is None else dict(self.payload),
            "page_sequence": self.page_sequence,
        }


_SCHEMA_SQL = """
CREATE TABLE prototype_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE workspaces (
  id TEXT PRIMARY KEY,
  owner_subject_id TEXT NOT NULL,
  access_epoch INTEGER NOT NULL CHECK(access_epoch >= 1),
  row_version INTEGER NOT NULL CHECK(row_version >= 1),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE workspace_principals (
  workspace_id TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  principal_kind TEXT NOT NULL CHECK(principal_kind IN ('member','guest')),
  role TEXT NOT NULL CHECK(role IN ('owner','edit','comment','view')),
  active INTEGER NOT NULL CHECK(active IN (0,1)),
  row_version INTEGER NOT NULL CHECK(row_version >= 1),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(workspace_id, subject_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE pages (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  title TEXT NOT NULL,
  content_json TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  page_sequence INTEGER NOT NULL CHECK(page_sequence >= 1),
  access_epoch INTEGER NOT NULL CHECK(access_epoch >= 1),
  row_version INTEGER NOT NULL CHECK(row_version >= 1),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT
);

CREATE TABLE page_grants (
  page_id TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('edit','comment','view')),
  active INTEGER NOT NULL CHECK(active IN (0,1)),
  row_version INTEGER NOT NULL CHECK(row_version >= 1),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(page_id, subject_id),
  FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE operation_dedup (
  actor_subject_id TEXT NOT NULL,
  operation_type TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  request_sha256 TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(actor_subject_id, operation_type, operation_id)
) WITHOUT ROWID;

CREATE TABLE durable_events (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  page_id TEXT,
  page_sequence INTEGER,
  event_type TEXT NOT NULL,
  actor_subject_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  workspace_epoch INTEGER NOT NULL,
  page_epoch INTEGER,
  payload_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE RESTRICT,
  UNIQUE(page_id, page_sequence)
);

CREATE TABLE audit_events (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  page_id TEXT,
  action TEXT NOT NULL,
  actor_subject_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE RESTRICT
);

CREATE TABLE outbox_events (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  page_id TEXT,
  event_type TEXT NOT NULL,
  actor_subject_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','delivered')),
  created_at INTEGER NOT NULL,
  delivered_at INTEGER,
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE RESTRICT
);

CREATE INDEX idx_pages_workspace ON pages(workspace_id, id);
CREATE INDEX idx_principals_subject ON workspace_principals(subject_id, active);
CREATE INDEX idx_page_grants_subject ON page_grants(subject_id, active);
CREATE INDEX idx_events_page_sequence ON durable_events(page_id, page_sequence);
CREATE INDEX idx_outbox_pending ON outbox_events(status, created_at, id);
"""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _request_sha256(actor: str, operation_type: str, request: Mapping[str, Any]) -> str:
    return _sha256(
        _canonical_json(
            {"actor_subject_id": actor, "operation_type": operation_type, "request": request}
        )
    )


def _identifier(value: str, *, field: str) -> str:
    item = str(value or "").strip().lower()
    if not _ID_RE.fullmatch(item):
        raise ValueError(f"valid {field} required")
    return item


def _operation_id(value: str) -> str:
    item = str(value or "").strip()
    if not _OPERATION_RE.fullmatch(item):
        raise ValueError("valid operation_id required")
    return item


def _role(value: str, *, allow_owner: bool = False) -> str:
    role = str(value or "").strip().lower()
    allowed = {"view", "comment", "edit"} | ({"owner"} if allow_owner else set())
    if role not in allowed:
        raise ValueError("valid role required")
    return role


def _expected_positive(value: int, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"valid {field} required")
    return value


def _fault(value: str | None) -> str | None:
    if value not in FAULT_POINTS:
        raise ValueError("unknown fault point")
    return value


def _trip(selected: str | None, point: str) -> None:
    if selected == point:
        raise InjectedFailure(point)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_disposable_root(root: Path) -> Path:
    candidate = root.resolve(strict=False)
    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    if candidate == temp_root or not _is_within(candidate, temp_root):
        raise PrototypeBoundaryError("prototype root must be a child of the OS temporary root")
    if any(part.lower() in FORBIDDEN_COMPONENTS for part in candidate.parts):
        raise PrototypeBoundaryError("prototype root resembles a protected runtime path")
    current = candidate
    while _is_within(current, temp_root) and current != temp_root:
        if current.exists() and current.is_symlink():
            raise PrototypeBoundaryError("symlinked prototype roots are forbidden")
        current = current.parent
    return candidate


def initialize_disposable_authority(root: str | os.PathLike[str]) -> "DisposableAuthority":
    """Create one empty marker-bound prototype below the OS temporary root."""

    candidate = _validate_disposable_root(Path(root))
    candidate.mkdir(parents=True, exist_ok=False)
    marker_path = candidate / MARKER_NAME
    marker = {
        "contract": PROTOTYPE_CONTRACT,
        "disposable": True,
        "production_schema_version": None,
        "created_at": int(time.time()),
        "nonce": uuid.uuid4().hex,
    }
    with marker_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(marker, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
    database_path = candidate / DATABASE_NAME
    con = sqlite3.connect(database_path)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        con.executescript(_SCHEMA_SQL)
        con.executemany(
            "INSERT INTO prototype_meta(key,value) VALUES(?,?)",
            (
                ("contract", PROTOTYPE_CONTRACT),
                ("disposable", "true"),
                ("schema_mode", "unversioned"),
                ("production_schema_version", "null"),
            ),
        )
        con.commit()
    except Exception:
        con.rollback()
        con.close()
        raise
    finally:
        try:
            con.close()
        except Exception:
            pass
    return DisposableAuthority.open(candidate)


class DisposableAuthority:
    """Service-layer prototype with one SQLite transaction per mutation."""

    def __init__(self, root: Path, database_path: Path) -> None:
        self.root = root
        self.database_path = database_path

    @classmethod
    def open(cls, root: str | os.PathLike[str]) -> "DisposableAuthority":
        candidate = _validate_disposable_root(Path(root))
        marker_path = candidate / MARKER_NAME
        database_path = candidate / DATABASE_NAME
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise PrototypeBoundaryError("valid disposable marker required") from exc
        if (
            not isinstance(marker, dict)
            or marker.get("contract") != PROTOTYPE_CONTRACT
            or marker.get("disposable") is not True
            or marker.get("production_schema_version") is not None
        ):
            raise PrototypeBoundaryError("disposable marker contract mismatch")
        if not database_path.is_file() or database_path.is_symlink():
            raise PrototypeBoundaryError("prototype database is unavailable")
        authority = cls(candidate, database_path)
        integrity = authority.integrity()
        if integrity["contract"] != PROTOTYPE_CONTRACT or integrity["schema_mode"] != "unversioned":
            raise PrototypeBoundaryError("prototype metadata mismatch")
        return authority

    def _connect(self, *, query_only: bool) -> sqlite3.Connection:
        if not self.database_path.is_file():
            raise AuthorityUnavailable("prototype database is unavailable")
        try:
            if query_only:
                uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
                con = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
            else:
                con = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=5000")
            if query_only:
                con.execute("PRAGMA query_only=ON")
            return con
        except sqlite3.Error as exc:
            raise AuthorityUnavailable("prototype database is unavailable") from exc

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

    @contextmanager
    def _reader(self) -> Iterator[sqlite3.Connection]:
        con = self._connect(query_only=True)
        try:
            yield con
        finally:
            con.close()

    @staticmethod
    def _workspace(con: sqlite3.Connection, workspace_id: str) -> sqlite3.Row:
        row = con.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
        if row is None:
            raise NotFoundOrDenied("not found or permission denied")
        return row

    @staticmethod
    def _page(con: sqlite3.Connection, page_id: str) -> sqlite3.Row:
        row = con.execute("SELECT * FROM pages WHERE id=?", (page_id,)).fetchone()
        if row is None:
            raise NotFoundOrDenied("not found or permission denied")
        return row

    @classmethod
    def _workspace_role(
        cls, con: sqlite3.Connection, workspace_id: str, subject_id: str
    ) -> str | None:
        row = con.execute(
            "SELECT role FROM workspace_principals WHERE workspace_id=? AND subject_id=? AND active=1",
            (workspace_id, subject_id),
        ).fetchone()
        return None if row is None else str(row["role"])

    @classmethod
    def _access(
        cls,
        con: sqlite3.Connection,
        subject_id: str,
        page_id: str,
        *,
        minimum_role: str,
        expected_workspace_epoch: int | None = None,
        expected_page_epoch: int | None = None,
    ) -> tuple[sqlite3.Row, sqlite3.Row, str]:
        page = cls._page(con, page_id)
        workspace = cls._workspace(con, str(page["workspace_id"]))
        workspace_role = cls._workspace_role(con, str(workspace["id"]), subject_id)
        grant = con.execute(
            "SELECT role FROM page_grants WHERE page_id=? AND subject_id=? AND active=1",
            (page_id, subject_id),
        ).fetchone()
        roles = [item for item in (workspace_role, None if grant is None else str(grant["role"])) if item]
        effective = max(roles, key=lambda item: ROLE_LEVEL[item]) if roles else None
        if effective is None or ROLE_LEVEL[effective] < ROLE_LEVEL[minimum_role]:
            raise NotFoundOrDenied("not found or permission denied")
        workspace_epoch = int(workspace["access_epoch"])
        page_epoch = int(page["access_epoch"])
        if (
            expected_workspace_epoch is not None
            and _expected_positive(expected_workspace_epoch, field="workspace_epoch") != workspace_epoch
        ) or (
            expected_page_epoch is not None
            and _expected_positive(expected_page_epoch, field="page_epoch") != page_epoch
        ):
            raise EpochConflict(workspace_epoch, page_epoch)
        return workspace, page, effective

    @staticmethod
    def _owner(con: sqlite3.Connection, actor: str, workspace_id: str) -> sqlite3.Row:
        workspace = DisposableAuthority._workspace(con, workspace_id)
        role = DisposableAuthority._workspace_role(con, workspace_id, actor)
        if role != "owner" or str(workspace["owner_subject_id"]) != actor:
            raise NotFoundOrDenied("not found or permission denied")
        return workspace

    @staticmethod
    def _replay(
        con: sqlite3.Connection,
        *,
        actor: str,
        operation_type: str,
        operation_id: str,
        request_sha256: str,
        authorize: Callable[[], None],
    ) -> dict[str, Any] | None:
        row = con.execute(
            """
            SELECT request_sha256,result_json FROM operation_dedup
            WHERE actor_subject_id=? AND operation_type=? AND operation_id=?
            """,
            (actor, operation_type, operation_id),
        ).fetchone()
        if row is None:
            return None
        if str(row["request_sha256"]) != request_sha256:
            raise OperationConflict("operation_id reused with a different request")
        authorize()
        value = json.loads(str(row["result_json"]))
        if not isinstance(value, dict):
            raise AuthorityUnavailable("stored operation result is invalid")
        return value

    @staticmethod
    def _finish(
        con: sqlite3.Connection,
        *,
        actor: str,
        operation_type: str,
        operation_id: str,
        request_sha256: str,
        workspace_id: str,
        page_id: str | None,
        workspace_epoch: int,
        page_epoch: int | None,
        page_sequence: int | None,
        event_type: str,
        event_payload: Mapping[str, Any],
        result: dict[str, Any],
        fault: str | None,
    ) -> dict[str, Any]:
        selected = _fault(fault)
        _trip(selected, "after_domain")
        now = int(time.time())
        event_id = "evt_" + uuid.uuid4().hex
        audit_id = "aud_" + uuid.uuid4().hex
        outbox_id = "out_" + uuid.uuid4().hex
        payload_json = _canonical_json(event_payload)
        con.execute(
            """
            INSERT INTO durable_events(
              id,workspace_id,page_id,page_sequence,event_type,actor_subject_id,
              operation_id,workspace_epoch,page_epoch,payload_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event_id,
                workspace_id,
                page_id,
                page_sequence,
                event_type,
                actor,
                operation_id,
                workspace_epoch,
                page_epoch,
                payload_json,
                now,
            ),
        )
        _trip(selected, "after_event")
        con.execute(
            """
            INSERT INTO audit_events(
              id,workspace_id,page_id,action,actor_subject_id,operation_id,payload_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (audit_id, workspace_id, page_id, operation_type, actor, operation_id, payload_json, now),
        )
        _trip(selected, "after_audit")
        con.execute(
            """
            INSERT INTO outbox_events(
              id,workspace_id,page_id,event_type,actor_subject_id,operation_id,
              payload_json,status,created_at,delivered_at
            ) VALUES(?,?,?,?,?,?,?,'pending',?,NULL)
            """,
            (outbox_id, workspace_id, page_id, event_type, actor, operation_id, payload_json, now),
        )
        _trip(selected, "after_outbox")
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
            """
            INSERT INTO operation_dedup(
              actor_subject_id,operation_type,operation_id,request_sha256,result_json,created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (actor, operation_type, operation_id, request_sha256, _canonical_json(stable), now),
        )
        _trip(selected, "after_operation")
        return stable

    def create_workspace(
        self,
        actor_subject_id: str,
        workspace_id: str,
        *,
        operation_id: str,
        fault: str | None = None,
    ) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        workspace = _identifier(workspace_id, field="workspace_id")
        op_id = _operation_id(operation_id)
        operation_type = "workspace.created"
        request = {"workspace_id": workspace}
        request_hash = _request_sha256(actor, operation_type, request)
        with self._transaction() as con:
            replay = self._replay(
                con,
                actor=actor,
                operation_type=operation_type,
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=lambda: self._owner(con, actor, workspace),
            )
            if replay is not None:
                return replay
            if con.execute("SELECT 1 FROM workspaces WHERE id=?", (workspace,)).fetchone():
                raise ValueError("workspace already exists")
            now = int(time.time())
            con.execute(
                "INSERT INTO workspaces(id,owner_subject_id,access_epoch,row_version,created_at,updated_at) VALUES(?,?,1,1,?,?)",
                (workspace, actor, now, now),
            )
            con.execute(
                """
                INSERT INTO workspace_principals(
                  workspace_id,subject_id,principal_kind,role,active,row_version,created_at,updated_at
                ) VALUES(?,?,'member','owner',1,1,?,?)
                """,
                (workspace, actor, now, now),
            )
            return self._finish(
                con,
                actor=actor,
                operation_type=operation_type,
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=workspace,
                page_id=None,
                workspace_epoch=1,
                page_epoch=None,
                page_sequence=None,
                event_type=operation_type,
                event_payload={"workspace_id": workspace, "workspace_epoch": 1},
                result={"workspace_id": workspace, "workspace_epoch": 1, "row_version": 1},
                fault=fault,
            )

    def add_member(
        self,
        actor_subject_id: str,
        workspace_id: str,
        target_subject_id: str,
        role: str,
        *,
        expected_workspace_epoch: int,
        operation_id: str,
        fault: str | None = None,
    ) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        workspace = _identifier(workspace_id, field="workspace_id")
        target = _identifier(target_subject_id, field="target_subject_id")
        member_role = _role(role)
        op_id = _operation_id(operation_id)
        expected_epoch = _expected_positive(expected_workspace_epoch, field="workspace_epoch")
        operation_type = "workspace.member_set"
        request = {
            "workspace_id": workspace,
            "target_subject_id": target,
            "role": member_role,
            "expected_workspace_epoch": expected_epoch,
        }
        request_hash = _request_sha256(actor, operation_type, request)
        with self._transaction() as con:
            replay = self._replay(
                con,
                actor=actor,
                operation_type=operation_type,
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=lambda: self._owner(con, actor, workspace),
            )
            if replay is not None:
                return replay
            row = self._owner(con, actor, workspace)
            if int(row["access_epoch"]) != expected_epoch:
                raise EpochConflict(int(row["access_epoch"]))
            if target == actor:
                raise ValueError("owner role is immutable in this prototype")
            now = int(time.time())
            existing = con.execute(
                "SELECT row_version FROM workspace_principals WHERE workspace_id=? AND subject_id=?",
                (workspace, target),
            ).fetchone()
            next_version = 1 if existing is None else int(existing["row_version"]) + 1
            con.execute(
                """
                INSERT INTO workspace_principals(
                  workspace_id,subject_id,principal_kind,role,active,row_version,created_at,updated_at
                ) VALUES(?,?,'member',?,1,?,?,?)
                ON CONFLICT(workspace_id,subject_id) DO UPDATE SET
                  principal_kind='member',role=excluded.role,active=1,
                  row_version=excluded.row_version,updated_at=excluded.updated_at
                """,
                (workspace, target, member_role, next_version, now, now),
            )
            next_epoch = expected_epoch + 1
            con.execute(
                "UPDATE workspaces SET access_epoch=?,row_version=row_version+1,updated_at=? WHERE id=?",
                (next_epoch, now, workspace),
            )
            payload = {
                "target_subject_id": target,
                "role": member_role,
                "workspace_epoch": next_epoch,
            }
            return self._finish(
                con,
                actor=actor,
                operation_type=operation_type,
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=workspace,
                page_id=None,
                workspace_epoch=next_epoch,
                page_epoch=None,
                page_sequence=None,
                event_type=operation_type,
                event_payload=payload,
                result={"workspace_id": workspace, **payload},
                fault=fault,
            )

    def create_page(
        self,
        actor_subject_id: str,
        workspace_id: str,
        page_id: str,
        title: str,
        content: Mapping[str, Any],
        *,
        expected_workspace_epoch: int,
        operation_id: str,
        fault: str | None = None,
    ) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        workspace = _identifier(workspace_id, field="workspace_id")
        page = _identifier(page_id, field="page_id")
        page_title = str(title or "").strip()
        if not page_title or len(page_title) > 500:
            raise ValueError("valid title required")
        if not isinstance(content, Mapping):
            raise ValueError("content object required")
        content_json = _canonical_json(dict(content))
        expected_epoch = _expected_positive(expected_workspace_epoch, field="workspace_epoch")
        op_id = _operation_id(operation_id)
        operation_type = "page.created"
        request = {
            "workspace_id": workspace,
            "page_id": page,
            "title": page_title,
            "content": dict(content),
            "expected_workspace_epoch": expected_epoch,
        }
        request_hash = _request_sha256(actor, operation_type, request)
        with self._transaction() as con:
            def authorize() -> None:
                workspace_row = self._workspace(con, workspace)
                role = self._workspace_role(con, workspace, actor)
                if role is None or ROLE_LEVEL[role] < ROLE_LEVEL["edit"]:
                    raise NotFoundOrDenied("not found or permission denied")
                if int(workspace_row["access_epoch"]) != expected_epoch:
                    raise EpochConflict(int(workspace_row["access_epoch"]))

            replay = self._replay(
                con,
                actor=actor,
                operation_type=operation_type,
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=authorize,
            )
            if replay is not None:
                return replay
            authorize()
            if con.execute("SELECT 1 FROM pages WHERE id=?", (page,)).fetchone():
                raise ValueError("page already exists")
            now = int(time.time())
            content_hash = _sha256(content_json)
            con.execute(
                """
                INSERT INTO pages(
                  id,workspace_id,title,content_json,content_sha256,revision,
                  page_sequence,access_epoch,row_version,created_at,updated_at
                ) VALUES(?,?,?,?,?,1,1,1,1,?,?)
                """,
                (page, workspace, page_title, content_json, content_hash, now, now),
            )
            return self._finish(
                con,
                actor=actor,
                operation_type=operation_type,
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=workspace,
                page_id=page,
                workspace_epoch=expected_epoch,
                page_epoch=1,
                page_sequence=1,
                event_type=operation_type,
                event_payload={"page_id": page, "revision": 1, "content_sha256": content_hash},
                result={
                    "workspace_id": workspace,
                    "page_id": page,
                    "revision": 1,
                    "page_sequence": 1,
                    "workspace_epoch": expected_epoch,
                    "page_epoch": 1,
                    "content_sha256": content_hash,
                },
                fault=fault,
            )

    def grant_page_access(
        self,
        actor_subject_id: str,
        page_id: str,
        target_subject_id: str,
        role: str,
        *,
        expected_workspace_epoch: int,
        expected_page_epoch: int,
        operation_id: str,
        fault: str | None = None,
    ) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        page = _identifier(page_id, field="page_id")
        target = _identifier(target_subject_id, field="target_subject_id")
        grant_role = _role(role)
        workspace_epoch = _expected_positive(expected_workspace_epoch, field="workspace_epoch")
        page_epoch = _expected_positive(expected_page_epoch, field="page_epoch")
        op_id = _operation_id(operation_id)
        operation_type = "page.access_granted"
        request = {
            "page_id": page,
            "target_subject_id": target,
            "role": grant_role,
            "expected_workspace_epoch": workspace_epoch,
            "expected_page_epoch": page_epoch,
        }
        request_hash = _request_sha256(actor, operation_type, request)
        with self._transaction() as con:
            def authorize_replay() -> None:
                self._access(
                    con,
                    actor,
                    page,
                    minimum_role="edit",
                )

            replay = self._replay(
                con,
                actor=actor,
                operation_type=operation_type,
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=authorize_replay,
            )
            if replay is not None:
                return replay
            workspace_row, page_row, _ = self._access(
                con,
                actor,
                page,
                minimum_role="edit",
                expected_workspace_epoch=workspace_epoch,
                expected_page_epoch=page_epoch,
            )
            now = int(time.time())
            existing = con.execute(
                "SELECT row_version FROM page_grants WHERE page_id=? AND subject_id=?",
                (page, target),
            ).fetchone()
            next_version = 1 if existing is None else int(existing["row_version"]) + 1
            con.execute(
                """
                INSERT INTO page_grants(page_id,subject_id,role,active,row_version,created_at,updated_at)
                VALUES(?,?,?,1,?,?,?)
                ON CONFLICT(page_id,subject_id) DO UPDATE SET
                  role=excluded.role,active=1,row_version=excluded.row_version,updated_at=excluded.updated_at
                """,
                (page, target, grant_role, next_version, now, now),
            )
            next_page_epoch = page_epoch + 1
            next_sequence = int(page_row["page_sequence"]) + 1
            con.execute(
                """
                UPDATE pages SET access_epoch=?,page_sequence=?,row_version=row_version+1,updated_at=?
                WHERE id=?
                """,
                (next_page_epoch, next_sequence, now, page),
            )
            payload = {
                "page_id": page,
                "target_subject_id": target,
                "role": grant_role,
                "page_epoch": next_page_epoch,
            }
            return self._finish(
                con,
                actor=actor,
                operation_type=operation_type,
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=str(workspace_row["id"]),
                page_id=page,
                workspace_epoch=workspace_epoch,
                page_epoch=next_page_epoch,
                page_sequence=next_sequence,
                event_type=operation_type,
                event_payload=payload,
                result={
                    "workspace_id": str(workspace_row["id"]),
                    "workspace_epoch": workspace_epoch,
                    "page_sequence": next_sequence,
                    **payload,
                },
                fault=fault,
            )

    def save_page(
        self,
        actor_subject_id: str,
        page_id: str,
        content: Mapping[str, Any],
        *,
        expected_revision: int,
        expected_workspace_epoch: int,
        expected_page_epoch: int,
        operation_id: str,
        fault: str | None = None,
    ) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        page = _identifier(page_id, field="page_id")
        if not isinstance(content, Mapping):
            raise ValueError("content object required")
        expected_rev = _expected_positive(expected_revision, field="revision")
        workspace_epoch = _expected_positive(expected_workspace_epoch, field="workspace_epoch")
        page_epoch = _expected_positive(expected_page_epoch, field="page_epoch")
        op_id = _operation_id(operation_id)
        operation_type = "page.saved"
        content_value = dict(content)
        content_json = _canonical_json(content_value)
        content_hash = _sha256(content_json)
        request = {
            "page_id": page,
            "content": content_value,
            "expected_revision": expected_rev,
            "expected_workspace_epoch": workspace_epoch,
            "expected_page_epoch": page_epoch,
        }
        request_hash = _request_sha256(actor, operation_type, request)
        with self._transaction() as con:
            def authorize() -> None:
                self._access(
                    con,
                    actor,
                    page,
                    minimum_role="edit",
                    expected_workspace_epoch=workspace_epoch,
                    expected_page_epoch=page_epoch,
                )

            replay = self._replay(
                con,
                actor=actor,
                operation_type=operation_type,
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=authorize,
            )
            if replay is not None:
                return replay
            workspace_row, page_row, _ = self._access(
                con,
                actor,
                page,
                minimum_role="edit",
                expected_workspace_epoch=workspace_epoch,
                expected_page_epoch=page_epoch,
            )
            if int(page_row["revision"]) != expected_rev:
                raise RevisionConflict(int(page_row["revision"]))
            now = int(time.time())
            next_revision = expected_rev + 1
            next_sequence = int(page_row["page_sequence"]) + 1
            con.execute(
                """
                UPDATE pages SET content_json=?,content_sha256=?,revision=?,page_sequence=?,
                  row_version=row_version+1,updated_at=? WHERE id=?
                """,
                (content_json, content_hash, next_revision, next_sequence, now, page),
            )
            payload = {
                "page_id": page,
                "revision": next_revision,
                "page_sequence": next_sequence,
                "content_sha256": content_hash,
            }
            return self._finish(
                con,
                actor=actor,
                operation_type=operation_type,
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=str(workspace_row["id"]),
                page_id=page,
                workspace_epoch=workspace_epoch,
                page_epoch=page_epoch,
                page_sequence=next_sequence,
                event_type=operation_type,
                event_payload=payload,
                result={
                    "workspace_id": str(workspace_row["id"]),
                    "workspace_epoch": workspace_epoch,
                    "page_epoch": page_epoch,
                    **payload,
                },
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
        expected_epoch = _expected_positive(expected_workspace_epoch, field="workspace_epoch")
        op_id = _operation_id(operation_id)
        operation_type = "workspace.subject_revoked"
        request = {
            "workspace_id": workspace,
            "target_subject_id": target,
            "expected_workspace_epoch": expected_epoch,
        }
        request_hash = _request_sha256(actor, operation_type, request)
        with self._transaction() as con:
            replay = self._replay(
                con,
                actor=actor,
                operation_type=operation_type,
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=lambda: self._owner(con, actor, workspace),
            )
            if replay is not None:
                return replay
            workspace_row = self._owner(con, actor, workspace)
            if int(workspace_row["access_epoch"]) != expected_epoch:
                raise EpochConflict(int(workspace_row["access_epoch"]))
            if target == actor:
                raise ValueError("prototype owner cannot revoke itself")
            principal = con.execute(
                "SELECT active FROM workspace_principals WHERE workspace_id=? AND subject_id=?",
                (workspace, target),
            ).fetchone()
            grants = con.execute(
                """
                SELECT p.id,p.access_epoch,p.page_sequence FROM pages p
                JOIN page_grants g ON g.page_id=p.id
                WHERE p.workspace_id=? AND g.subject_id=? AND g.active=1
                ORDER BY p.id
                """,
                (workspace, target),
            ).fetchall()
            if (principal is None or int(principal["active"]) == 0) and not grants:
                raise NotFoundOrDenied("not found or permission denied")
            now = int(time.time())
            con.execute(
                """
                UPDATE workspace_principals SET active=0,row_version=row_version+1,updated_at=?
                WHERE workspace_id=? AND subject_id=? AND active=1
                """,
                (now, workspace, target),
            )
            affected_page_epochs: dict[str, int] = {}
            for page_row in grants:
                page_id = str(page_row["id"])
                next_page_epoch = int(page_row["access_epoch"]) + 1
                affected_page_epochs[page_id] = next_page_epoch
                con.execute(
                    """
                    UPDATE page_grants SET active=0,row_version=row_version+1,updated_at=?
                    WHERE page_id=? AND subject_id=? AND active=1
                    """,
                    (now, page_id, target),
                )
                con.execute(
                    """
                    UPDATE pages SET access_epoch=?,row_version=row_version+1,updated_at=? WHERE id=?
                    """,
                    (next_page_epoch, now, page_id),
                )
            next_workspace_epoch = expected_epoch + 1
            con.execute(
                "UPDATE workspaces SET access_epoch=?,row_version=row_version+1,updated_at=? WHERE id=?",
                (next_workspace_epoch, now, workspace),
            )
            payload = {
                "target_subject_id": target,
                "workspace_epoch": next_workspace_epoch,
                "affected_page_epochs": affected_page_epochs,
                "gateway_fence": "authoritative_send_time_epoch_check",
            }
            return self._finish(
                con,
                actor=actor,
                operation_type=operation_type,
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=workspace,
                page_id=None,
                workspace_epoch=next_workspace_epoch,
                page_epoch=None,
                page_sequence=None,
                event_type="permission.revoked",
                event_payload=payload,
                result={
                    "workspace_id": workspace,
                    **payload,
                    "state": "revoked",
                    "http_status": 200,
                    "gateway_notification": "durable_outbox_pending",
                },
                fault=fault,
            )

    def read_page(
        self,
        subject_id: str,
        page_id: str,
        *,
        expected_workspace_epoch: int,
        expected_page_epoch: int,
        minimum_role: str = "view",
    ) -> dict[str, Any]:
        subject = _identifier(subject_id, field="subject_id")
        page = _identifier(page_id, field="page_id")
        role = _role(minimum_role, allow_owner=True)
        with self._reader() as con:
            workspace_row, page_row, effective = self._access(
                con,
                subject,
                page,
                minimum_role=role,
                expected_workspace_epoch=expected_workspace_epoch,
                expected_page_epoch=expected_page_epoch,
            )
            content = json.loads(str(page_row["content_json"]))
            return {
                "workspace_id": str(workspace_row["id"]),
                "page_id": page,
                "role": effective,
                "title": str(page_row["title"]),
                "content": content,
                "content_sha256": str(page_row["content_sha256"]),
                "revision": int(page_row["revision"]),
                "page_sequence": int(page_row["page_sequence"]),
                "workspace_epoch": int(workspace_row["access_epoch"]),
                "page_epoch": int(page_row["access_epoch"]),
            }

    def binding(self, subject_id: str, page_id: str, *, minimum_role: str = "view") -> AccessBinding:
        subject = _identifier(subject_id, field="subject_id")
        page = _identifier(page_id, field="page_id")
        role = _role(minimum_role, allow_owner=True)
        with self._reader() as con:
            workspace_row, page_row, effective = self._access(
                con, subject, page, minimum_role=role
            )
            return AccessBinding(
                subject_id=subject,
                workspace_id=str(workspace_row["id"]),
                page_id=page,
                role=effective,
                workspace_epoch=int(workspace_row["access_epoch"]),
                page_epoch=int(page_row["access_epoch"]),
                page_sequence=int(page_row["page_sequence"]),
            )

    def authorize_frame_send(self, frame: QueuedFrame) -> DeliveryResult:
        """Perform the selected authoritative check immediately before send."""

        try:
            binding = self.binding(frame.subject_id, frame.page_id)
        except (NotFoundOrDenied, AuthorityUnavailable, PrototypeBoundaryError):
            return DeliveryResult(False, "denied_or_authority_unavailable", None, None)
        if (
            binding.workspace_id != frame.workspace_id
            or binding.workspace_epoch != frame.workspace_epoch
            or binding.page_epoch != frame.page_epoch
        ):
            return DeliveryResult(False, "stale_epoch", None, None)
        return DeliveryResult(True, "current_epoch", dict(frame.payload), frame.page_sequence)

    def counts(self) -> dict[str, int]:
        names = (
            "workspaces",
            "workspace_principals",
            "pages",
            "page_grants",
            "operation_dedup",
            "durable_events",
            "audit_events",
            "outbox_events",
        )
        with self._reader() as con:
            return {name: int(con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) for name in names}

    def operation_result(self, actor: str, operation_type: str, operation_id: str) -> dict[str, Any] | None:
        actor_id = _identifier(actor, field="actor_subject_id")
        op_id = _operation_id(operation_id)
        with self._reader() as con:
            row = con.execute(
                """
                SELECT result_json FROM operation_dedup
                WHERE actor_subject_id=? AND operation_type=? AND operation_id=?
                """,
                (actor_id, operation_type, op_id),
            ).fetchone()
            if row is None:
                return None
            value = json.loads(str(row["result_json"]))
            return value if isinstance(value, dict) else None

    def event_rows(self, *, operation_id: str | None = None) -> list[dict[str, Any]]:
        with self._reader() as con:
            if operation_id is None:
                rows = con.execute("SELECT * FROM durable_events ORDER BY created_at,id").fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM durable_events WHERE operation_id=? ORDER BY created_at,id",
                    (_operation_id(operation_id),),
                ).fetchall()
            return [dict(row) for row in rows]

    def outbox_rows(self, *, operation_id: str | None = None) -> list[dict[str, Any]]:
        with self._reader() as con:
            if operation_id is None:
                rows = con.execute("SELECT * FROM outbox_events ORDER BY created_at,id").fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM outbox_events WHERE operation_id=? ORDER BY created_at,id",
                    (_operation_id(operation_id),),
                ).fetchall()
            return [dict(row) for row in rows]

    def integrity(self) -> dict[str, Any]:
        with self._reader() as con:
            quick = str(con.execute("PRAGMA quick_check").fetchone()[0])
            foreign_key_errors = len(con.execute("PRAGMA foreign_key_check").fetchall())
            metadata = {
                str(row["key"]): str(row["value"])
                for row in con.execute("SELECT key,value FROM prototype_meta").fetchall()
            }
            migration_tables = int(
                con.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND lower(name) LIKE '%migration%'"
                ).fetchone()[0]
            )
            return {
                "quick_check": quick,
                "foreign_key_error_count": foreign_key_errors,
                "contract": metadata.get("contract"),
                "disposable": metadata.get("disposable") == "true",
                "schema_mode": metadata.get("schema_mode"),
                "production_schema_version": metadata.get("production_schema_version"),
                "migration_table_count": migration_tables,
            }


def queue_frame(
    authority: DisposableAuthority,
    subject_id: str,
    page_id: str,
    payload: Mapping[str, Any],
) -> QueuedFrame:
    if not isinstance(payload, Mapping):
        raise ValueError("payload object required")
    binding = authority.binding(subject_id, page_id)
    return QueuedFrame(
        subject_id=binding.subject_id,
        workspace_id=binding.workspace_id,
        page_id=binding.page_id,
        workspace_epoch=binding.workspace_epoch,
        page_epoch=binding.page_epoch,
        page_sequence=binding.page_sequence,
        payload=dict(payload),
    )


__all__ = [
    "AccessBinding",
    "AuthorityUnavailable",
    "DATABASE_NAME",
    "DeliveryResult",
    "DisposableAuthority",
    "EpochConflict",
    "InjectedFailure",
    "MARKER_NAME",
    "NotFoundOrDenied",
    "OperationConflict",
    "PROTOTYPE_CONTRACT",
    "PrototypeBoundaryError",
    "QueuedFrame",
    "RevisionConflict",
    "initialize_disposable_authority",
    "queue_frame",
]
