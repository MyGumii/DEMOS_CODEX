"""Append-only canonical audit repository.

Audit appends use the caller's already-open managed transaction.  The module
never opens a second connection, commits independently, writes JSONL, or catches
and discards an error; therefore a domain mutation and its audit row either
commit together or roll back together.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Literal, Mapping

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError


ActorKind = Literal["user", "system"]

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)

_AUDIT_COLUMNS = """
id,event_uuid,occurred_at,request_id,operation_id,action,outcome,actor_kind,
actor_user_id,actor_display_name,actor_role_snapshot,space_id,item_id,
base_revision_id,new_revision_id,reason,details_json,request_meta_json,
schema_version
""".strip()


class AuditRepositoryError(KnowledgeCoreDatabaseError):
    code = "AUDIT_REPOSITORY_ERROR"


class AuditConflictError(AuditRepositoryError):
    code = "AUDIT_EVENT_CONFLICT"


@dataclass(frozen=True, slots=True)
class AuditEventDraft:
    event_uuid: str
    occurred_at_ms: int
    request_id: str
    operation_id: str | None
    action: str
    outcome: str
    actor_kind: ActorKind
    actor_user_id: str | None
    actor_display_name: str
    actor_role_snapshot: str
    space_id: str | None = None
    item_id: str | None = None
    base_revision_id: str | None = None
    new_revision_id: str | None = None
    reason: str | None = None
    details: Mapping[str, object] = field(default_factory=dict)
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuditEventRecord:
    id: int
    event_uuid: str
    occurred_at_ms: int
    request_id: str
    operation_id: str | None
    action: str
    outcome: str
    actor_kind: ActorKind
    actor_user_id: str | None
    actor_display_name: str
    actor_role_snapshot: str
    space_id: str | None
    item_id: str | None
    base_revision_id: str | None
    new_revision_id: str | None
    reason: str | None
    details_json: str
    request_meta_json: str
    schema_version: int

    @property
    def details(self) -> dict[str, object]:
        value = json.loads(self.details_json)
        if not isinstance(value, dict):
            raise AuditRepositoryError("Stored audit details are not an object.")
        return value

    @property
    def request_meta(self) -> dict[str, object]:
        value = json.loads(self.request_meta_json)
        if not isinstance(value, dict):
            raise AuditRepositoryError("Stored audit request metadata is not an object.")
        return value


@dataclass(frozen=True, slots=True)
class _PreparedAudit:
    draft: AuditEventDraft
    details_json: str
    request_meta_json: str


def _require_connection(connection: ManagedConnection) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("AuditRepository requires a ManagedConnection.")
    return connection


def _require_write_transaction(connection: ManagedConnection) -> None:
    _require_connection(connection)
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Audit append requires an existing managed transaction."
        )


def _uuid(value: object, *, field_name: str, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a canonical lowercase UUID.")
    return value


def _required_text(value: object, *, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(
            f"{field_name} must contain 1..{maximum} non-blank characters."
        )
    return value.strip()


def _optional_text(value: object, *, field_name: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name=field_name, maximum=maximum)


def _utc_ms(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("occurred_at_ms must be a non-negative integer.")
    return value


def _canonical_object(value: object, *, field_name: str) -> str:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object mapping.")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON serializable.") from exc
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ValueError(f"{field_name} must encode a JSON object.")
    return encoded


def _prepare(draft: AuditEventDraft) -> _PreparedAudit:
    if not isinstance(draft, AuditEventDraft):
        raise TypeError("Audit append requires an AuditEventDraft.")
    event_uuid = _uuid(draft.event_uuid, field_name="event_uuid")
    request_id = _uuid(draft.request_id, field_name="request_id")
    operation_id = _uuid(
        draft.operation_id, field_name="operation_id", nullable=True
    )
    occurred_at = _utc_ms(draft.occurred_at_ms)
    action = _required_text(draft.action, field_name="action", maximum=120)
    outcome = _required_text(draft.outcome, field_name="outcome", maximum=80)
    if draft.actor_kind not in ("user", "system"):
        raise ValueError("actor_kind must be user or system.")
    actor_user_id = draft.actor_user_id
    if draft.actor_kind == "user":
        if not isinstance(actor_user_id, str) or _USER_ID_RE.fullmatch(actor_user_id) is None:
            raise ValueError("A user audit actor requires a canonical user ID.")
    elif actor_user_id is not None:
        raise ValueError("A system audit actor cannot have actor_user_id.")
    actor_display_name = _required_text(
        draft.actor_display_name, field_name="actor_display_name", maximum=200
    )
    actor_role_snapshot = _required_text(
        draft.actor_role_snapshot,
        field_name="actor_role_snapshot",
        maximum=200,
    )
    space_id = _uuid(draft.space_id, field_name="space_id", nullable=True)
    item_id = _uuid(draft.item_id, field_name="item_id", nullable=True)
    base_revision_id = _uuid(
        draft.base_revision_id, field_name="base_revision_id", nullable=True
    )
    new_revision_id = _uuid(
        draft.new_revision_id, field_name="new_revision_id", nullable=True
    )
    reason = _optional_text(draft.reason, field_name="reason", maximum=2000)
    normalized = AuditEventDraft(
        event_uuid=event_uuid,  # type: ignore[arg-type]
        occurred_at_ms=occurred_at,
        request_id=request_id,  # type: ignore[arg-type]
        operation_id=operation_id,
        action=action,
        outcome=outcome,
        actor_kind=draft.actor_kind,
        actor_user_id=actor_user_id,
        actor_display_name=actor_display_name,
        actor_role_snapshot=actor_role_snapshot,
        space_id=space_id,
        item_id=item_id,
        base_revision_id=base_revision_id,
        new_revision_id=new_revision_id,
        reason=reason,
        details=draft.details,
        request_meta=draft.request_meta,
    )
    return _PreparedAudit(
        draft=normalized,
        details_json=_canonical_object(draft.details, field_name="details"),
        request_meta_json=_canonical_object(
            draft.request_meta, field_name="request_meta"
        ),
    )


def _row_to_record(row: object) -> AuditEventRecord:
    try:
        return AuditEventRecord(
            id=int(row["id"]),  # type: ignore[index]
            event_uuid=str(row["event_uuid"]),  # type: ignore[index]
            occurred_at_ms=int(row["occurred_at"]),  # type: ignore[index]
            request_id=str(row["request_id"]),  # type: ignore[index]
            operation_id=row["operation_id"],  # type: ignore[index,arg-type]
            action=str(row["action"]),  # type: ignore[index]
            outcome=str(row["outcome"]),  # type: ignore[index]
            actor_kind=row["actor_kind"],  # type: ignore[index,arg-type]
            actor_user_id=row["actor_user_id"],  # type: ignore[index,arg-type]
            actor_display_name=str(row["actor_display_name"]),  # type: ignore[index]
            actor_role_snapshot=str(row["actor_role_snapshot"]),  # type: ignore[index]
            space_id=row["space_id"],  # type: ignore[index,arg-type]
            item_id=row["item_id"],  # type: ignore[index,arg-type]
            base_revision_id=row["base_revision_id"],  # type: ignore[index,arg-type]
            new_revision_id=row["new_revision_id"],  # type: ignore[index,arg-type]
            reason=row["reason"],  # type: ignore[index,arg-type]
            details_json=str(row["details_json"]),  # type: ignore[index]
            request_meta_json=str(row["request_meta_json"]),  # type: ignore[index]
            schema_version=int(row["schema_version"]),  # type: ignore[index]
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise AuditRepositoryError("Canonical audit row mapping failed.") from exc


def _prepared_identity(prepared: _PreparedAudit) -> tuple[object, ...]:
    draft = prepared.draft
    return (
        draft.event_uuid,
        draft.occurred_at_ms,
        draft.request_id,
        draft.operation_id,
        draft.action,
        draft.outcome,
        draft.actor_kind,
        draft.actor_user_id,
        draft.actor_display_name,
        draft.actor_role_snapshot,
        draft.space_id,
        draft.item_id,
        draft.base_revision_id,
        draft.new_revision_id,
        draft.reason,
        prepared.details_json,
        prepared.request_meta_json,
        1,
    )


def _record_identity(record: AuditEventRecord) -> tuple[object, ...]:
    return (
        record.event_uuid,
        record.occurred_at_ms,
        record.request_id,
        record.operation_id,
        record.action,
        record.outcome,
        record.actor_kind,
        record.actor_user_id,
        record.actor_display_name,
        record.actor_role_snapshot,
        record.space_id,
        record.item_id,
        record.base_revision_id,
        record.new_revision_id,
        record.reason,
        record.details_json,
        record.request_meta_json,
        record.schema_version,
    )


class AuditRepository:
    __slots__ = ()

    def append(
        self, connection: ManagedConnection, draft: AuditEventDraft
    ) -> AuditEventRecord:
        """Append once, or return the exact same immutable event on retry."""

        _require_write_transaction(connection)
        prepared = _prepare(draft)
        value = prepared.draft
        cursor = connection.execute(
            """INSERT INTO audit_event(
                 event_uuid,occurred_at,request_id,operation_id,action,outcome,
                 actor_kind,actor_user_id,actor_display_name,actor_role_snapshot,
                 space_id,item_id,base_revision_id,new_revision_id,reason,
                 details_json,request_meta_json,schema_version
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
               ON CONFLICT(event_uuid) DO NOTHING""",
            (
                value.event_uuid,
                value.occurred_at_ms,
                value.request_id,
                value.operation_id,
                value.action,
                value.outcome,
                value.actor_kind,
                value.actor_user_id,
                value.actor_display_name,
                value.actor_role_snapshot,
                value.space_id,
                value.item_id,
                value.base_revision_id,
                value.new_revision_id,
                value.reason,
                prepared.details_json,
                prepared.request_meta_json,
            ),
        )
        record = self.get_by_event_uuid(connection, value.event_uuid)
        if record is None:
            raise AuditRepositoryError("The inserted audit event cannot be read back.")
        if cursor.rowcount != 1 and _record_identity(record) != _prepared_identity(prepared):
            raise AuditConflictError(
                "The audit event UUID already identifies a different immutable event."
            )
        return record

    def get_by_event_uuid(
        self, connection: ManagedConnection, event_uuid: str
    ) -> AuditEventRecord | None:
        connection = _require_connection(connection)
        event_uuid = _uuid(event_uuid, field_name="event_uuid")  # type: ignore[assignment]
        row = connection.execute(
            f"SELECT {_AUDIT_COLUMNS} FROM audit_event WHERE event_uuid=?",
            (event_uuid,),
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def list_recent(
        self,
        connection: ManagedConnection,
        *,
        limit: int = 100,
        before_id: int | None = None,
        actor_user_id: str | None = None,
    ) -> tuple[AuditEventRecord, ...]:
        connection = _require_connection(connection)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer from 1 through 500.")
        clauses: list[str] = []
        parameters: list[object] = []
        if before_id is not None:
            if isinstance(before_id, bool) or not isinstance(before_id, int) or before_id < 1:
                raise ValueError("before_id must be a positive integer.")
            clauses.append("id<?")
            parameters.append(before_id)
        if actor_user_id is not None:
            if _USER_ID_RE.fullmatch(actor_user_id) is None:
                raise ValueError("actor_user_id must use the canonical login format.")
            clauses.append("actor_user_id=?")
            parameters.append(actor_user_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(limit)
        rows = connection.execute(
            f"SELECT {_AUDIT_COLUMNS} FROM audit_event{where} "
            "ORDER BY id DESC LIMIT ?",
            tuple(parameters),
        ).fetchall()
        return tuple(_row_to_record(row) for row in rows)


__all__ = [
    "ActorKind",
    "AuditConflictError",
    "AuditEventDraft",
    "AuditEventRecord",
    "AuditRepository",
    "AuditRepositoryError",
]
