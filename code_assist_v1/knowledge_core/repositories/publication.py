"""Immutable publication-preview persistence and state transitions.

The repository owns no authorization policy, extraction, filesystem access,
job execution, transaction, commit, or rollback.  P4.2 services supply already
authorized actor/scope facts and coordinate durable-job state in the same
caller-owned transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import sqlite3
from typing import Literal, Mapping, Sequence
from uuid import UUID

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError


PreviewStatus = Literal[
    "queued", "extracting", "ready", "consumed", "expired", "cancelled", "failed"
]
TargetMode = Literal["personal", "part", "both", "explicit"]

PREVIEW_RESULT_SCHEMA_VERSION = 1
PREVIEW_TTL_MS = 3_600_000
_SQLITE_MAX = 9_223_372_036_854_775_807
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$", re.ASCII)
_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,79}$", re.ASCII)
_COLUMNS = """id,actor_user_id,artifact_id,extraction_snapshot_id,durable_job_id,
source_snapshot_sha256,captured_part_id,captured_scope_epoch,
available_targets_json,rule_version,deterministic_findings_json,
ready_result_json,result_schema_version,status,error_json,expires_at,
created_at,updated_at,completed_at,row_version"""


class PublicationPreviewRepositoryError(KnowledgeCoreDatabaseError):
    code = "PUBLICATION_PREVIEW_REPOSITORY_ERROR"


class PublicationPreviewNotFoundError(PublicationPreviewRepositoryError):
    code = "NOT_FOUND"


class PublicationPreviewConflictError(PublicationPreviewRepositoryError):
    code = "STATE_CONFLICT"


class PublicationPreviewIntegrityError(PublicationPreviewRepositoryError):
    code = "PUBLICATION_PREVIEW_INTEGRITY_ERROR"


@dataclass(frozen=True, slots=True)
class PreviewFinding:
    kind: str
    severity: Literal["blocker", "error", "warning", "info"]
    risk_tier: Literal["T0", "T1", "T2", "T3"]
    visibility_effect: Literal[
        "none", "badge", "agent_hidden", "publication_blocked"
    ]
    evidence_sha256: str

    def to_mapping(self) -> dict[str, object]:
        return {
            "evidence_sha256": self.evidence_sha256,
            "kind": self.kind,
            "risk_tier": self.risk_tier,
            "severity": self.severity,
            "visibility_effect": self.visibility_effect,
        }


@dataclass(frozen=True, slots=True)
class QueuedPreviewDraft:
    preview_id: str
    actor_user_id: str
    artifact_id: str
    durable_job_id: str
    captured_part_id: str | None
    captured_scope_epoch: int
    available_target_modes: tuple[TargetMode, ...]
    rule_version: str
    expires_at_ms: int
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class ReadyPreviewDraft:
    preview_id: str
    request_id: str
    operation_id: str
    actor_user_id: str
    artifact_id: str
    extraction_snapshot_id: str
    durable_job_id: str | None
    source_snapshot_sha256: str
    captured_part_id: str | None
    captured_scope_epoch: int
    available_target_modes: tuple[TargetMode, ...]
    rule_version: str
    title: str
    size_bytes: int
    warnings: tuple[str, ...]
    deterministic_findings: tuple[PreviewFinding, ...]
    expires_at_ms: int
    created_at_ms: int
    completed_at_ms: int


@dataclass(frozen=True, slots=True)
class PublicationPreviewRecord:
    preview_id: str
    actor_user_id: str
    artifact_id: str
    extraction_snapshot_id: str | None
    durable_job_id: str | None
    source_snapshot_sha256: str | None
    captured_part_id: str | None
    captured_scope_epoch: int
    available_targets_json: str
    rule_version: str
    deterministic_findings_json: str | None
    ready_result_json: str | None
    result_schema_version: int
    status: PreviewStatus
    error_json: str | None
    expires_at_ms: int
    created_at_ms: int
    updated_at_ms: int
    completed_at_ms: int | None
    row_version: int

    @property
    def available_target_modes(self) -> tuple[str, ...]:
        value = json.loads(self.available_targets_json)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise PublicationPreviewIntegrityError("Stored targets are invalid.")
        return tuple(value)

    @property
    def ready_result(self) -> dict[str, object] | None:
        return _object_or_none(self.ready_result_json, field="ready_result_json")

    @property
    def error(self) -> dict[str, object] | None:
        return _object_or_none(self.error_json, field="error_json")


def _connection(connection: object, *, write: bool) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("PublicationPreviewRepository requires a ManagedConnection.")
    if write and not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Publication preview writes require a managed transaction."
        )
    return connection


def _uuid(value: object, *, field: str) -> str:
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{field} must be a canonical UUID.") from None
    canonical = str(parsed)
    if not isinstance(value, str) or value != canonical:
        raise ValueError(f"{field} must be a canonical UUID.")
    return canonical


def _optional_uuid(value: object, *, field: str) -> str | None:
    return None if value is None else _uuid(value, field=field)


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("actor_user_id is invalid.")
    return value


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256.")
    return value


def _integer(value: object, *, field: str, minimum: int = 0) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= _SQLITE_MAX
    ):
        raise ValueError(f"{field} is outside the integer domain.")
    return value


def _text(value: object, *, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or "\x00" in value
        or any(ord(char) < 0x20 for char in value)
    ):
        raise ValueError(f"{field} is invalid.")
    return value


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _object_or_none(value: str | None, *, field: str) -> dict[str, object] | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise PublicationPreviewIntegrityError(f"Stored {field} is invalid.") from exc
    if not isinstance(parsed, dict) or _canonical(parsed) != value:
        raise PublicationPreviewIntegrityError(f"Stored {field} is invalid.")
    return parsed


def _stored_findings(value: object) -> tuple[PreviewFinding, ...]:
    if not isinstance(value, list):
        raise PublicationPreviewIntegrityError("Stored findings are invalid.")
    prepared: list[PreviewFinding] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "evidence_sha256",
            "kind",
            "risk_tier",
            "severity",
            "visibility_effect",
        }:
            raise PublicationPreviewIntegrityError("Stored findings are invalid.")
        prepared.append(
            PreviewFinding(
                kind=item["kind"],  # type: ignore[arg-type]
                severity=item["severity"],  # type: ignore[arg-type]
                risk_tier=item["risk_tier"],  # type: ignore[arg-type]
                visibility_effect=item["visibility_effect"],  # type: ignore[arg-type]
                evidence_sha256=item["evidence_sha256"],  # type: ignore[arg-type]
            )
        )
    try:
        normalized = _findings(tuple(prepared))
    except (TypeError, ValueError) as exc:
        raise PublicationPreviewIntegrityError("Stored findings are invalid.") from exc
    if [finding.to_mapping() for finding in normalized] != value:
        raise PublicationPreviewIntegrityError("Stored findings are invalid.")
    return normalized


def _utc_iso(value: int) -> str:
    value = _integer(value, field="utc_ms")
    return (
        datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _targets(value: object, *, part_id: str | None) -> tuple[TargetMode, ...]:
    if not isinstance(value, tuple):
        raise ValueError("available_target_modes must be a tuple.")
    if value == ("explicit",):
        return value
    allowed = ("personal", "part", "both")
    if (
        not value
        or any(item not in allowed for item in value)
        or len(set(value)) != len(value)
        or tuple(item for item in allowed if item in value) != value
        or "personal" not in value
        or (part_id is None and any(item in {"part", "both"} for item in value))
    ):
        raise ValueError("available_target_modes are invalid.")
    return value  # type: ignore[return-value]


def _warnings(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple) or len(value) > 100:
        raise ValueError("warnings must be a tuple of safe codes.")
    parsed = tuple(
        code
        for code in value
        if isinstance(code, str) and _CODE_RE.fullmatch(code) is not None
    )
    if len(parsed) != len(value) or parsed != tuple(sorted(set(parsed))):
        raise ValueError("warnings must be sorted unique safe codes.")
    return parsed


def _findings(value: object) -> tuple[PreviewFinding, ...]:
    if not isinstance(value, tuple) or len(value) > 100:
        raise ValueError("deterministic_findings must be a tuple.")
    prepared: list[PreviewFinding] = []
    for finding in value:
        if not isinstance(finding, PreviewFinding):
            raise TypeError("PreviewFinding values are required.")
        kind = _text(finding.kind, field="finding.kind", maximum=80)
        if _CODE_RE.fullmatch(kind) is None:
            raise ValueError("finding.kind must be a safe code.")
        if finding.severity not in {"blocker", "error", "warning", "info"}:
            raise ValueError("finding.severity is invalid.")
        if finding.risk_tier not in {"T0", "T1", "T2", "T3"}:
            raise ValueError("finding.risk_tier is invalid.")
        if finding.visibility_effect not in {
            "none",
            "badge",
            "agent_hidden",
            "publication_blocked",
        }:
            raise ValueError("finding.visibility_effect is invalid.")
        prepared.append(
            PreviewFinding(
                kind,
                finding.severity,
                finding.risk_tier,
                finding.visibility_effect,
                _sha(finding.evidence_sha256, field="finding.evidence_sha256"),
            )
        )
    ordered = tuple(
        sorted(
            prepared,
            key=lambda item: (
                item.kind,
                item.severity,
                item.risk_tier,
                item.visibility_effect,
                item.evidence_sha256,
            ),
        )
    )
    if tuple(prepared) != ordered or len(set(prepared)) != len(prepared):
        raise ValueError("deterministic_findings must be canonical and unique.")
    return ordered


def _expiry(created_at_ms: object, expires_at_ms: object) -> tuple[int, int]:
    created = _integer(created_at_ms, field="created_at_ms")
    expires = _integer(expires_at_ms, field="expires_at_ms")
    if expires - created != PREVIEW_TTL_MS:
        raise ValueError("Publication previews require the frozen one-hour TTL.")
    return created, expires


def _prepare_queued(value: object) -> QueuedPreviewDraft:
    if not isinstance(value, QueuedPreviewDraft):
        raise TypeError("A QueuedPreviewDraft is required.")
    part_id = _optional_uuid(value.captured_part_id, field="captured_part_id")
    created, expires = _expiry(value.created_at_ms, value.expires_at_ms)
    return QueuedPreviewDraft(
        _uuid(value.preview_id, field="preview_id"),
        _user(value.actor_user_id),
        _uuid(value.artifact_id, field="artifact_id"),
        _uuid(value.durable_job_id, field="durable_job_id"),
        part_id,
        _integer(value.captured_scope_epoch, field="captured_scope_epoch", minimum=1),
        _targets(value.available_target_modes, part_id=part_id),
        _text(value.rule_version, field="rule_version", maximum=120),
        expires,
        created,
    )


def _prepare_ready(value: object) -> ReadyPreviewDraft:
    if not isinstance(value, ReadyPreviewDraft):
        raise TypeError("A ReadyPreviewDraft is required.")
    part_id = _optional_uuid(value.captured_part_id, field="captured_part_id")
    created, expires = _expiry(value.created_at_ms, value.expires_at_ms)
    completed = _integer(value.completed_at_ms, field="completed_at_ms")
    job_id = _optional_uuid(value.durable_job_id, field="durable_job_id")
    if completed < created or (job_id is None and completed != created):
        raise ValueError("Preview completion time is invalid.")
    return ReadyPreviewDraft(
        _uuid(value.preview_id, field="preview_id"),
        _uuid(value.request_id, field="request_id"),
        _uuid(value.operation_id, field="operation_id"),
        _user(value.actor_user_id),
        _uuid(value.artifact_id, field="artifact_id"),
        _uuid(value.extraction_snapshot_id, field="extraction_snapshot_id"),
        job_id,
        _sha(value.source_snapshot_sha256, field="source_snapshot_sha256"),
        part_id,
        _integer(value.captured_scope_epoch, field="captured_scope_epoch", minimum=1),
        _targets(value.available_target_modes, part_id=part_id),
        _text(value.rule_version, field="rule_version", maximum=120),
        _text(value.title, field="title", maximum=500),
        _integer(value.size_bytes, field="size_bytes"),
        _warnings(value.warnings),
        _findings(value.deterministic_findings),
        expires,
        created,
        completed,
    )


def _ready_result(value: ReadyPreviewDraft) -> dict[str, object]:
    return {
        "available_target_modes": list(value.available_target_modes),
        "completed_at": _utc_iso(value.completed_at_ms),
        "created_at": _utc_iso(value.created_at_ms),
        "deterministic_findings": [
            finding.to_mapping() for finding in value.deterministic_findings
        ],
        "expires_at": _utc_iso(value.expires_at_ms),
        "job_id": value.durable_job_id,
        "operation_id": value.operation_id,
        "preview_id": value.preview_id,
        "ready_result_schema_version": PREVIEW_RESULT_SCHEMA_VERSION,
        "request_id": value.request_id,
        "size_bytes": value.size_bytes,
        "source_snapshot_sha256": value.source_snapshot_sha256,
        "status": "ready",
        "title": value.title,
        "updated_at": _utc_iso(value.completed_at_ms),
        "warnings": list(value.warnings),
    }


def _row_to_record(row: object) -> PublicationPreviewRecord:
    try:
        record = PublicationPreviewRecord(
            preview_id=row["id"],  # type: ignore[index,arg-type]
            actor_user_id=row["actor_user_id"],  # type: ignore[index,arg-type]
            artifact_id=row["artifact_id"],  # type: ignore[index,arg-type]
            extraction_snapshot_id=row["extraction_snapshot_id"],  # type: ignore[index,arg-type]
            durable_job_id=row["durable_job_id"],  # type: ignore[index,arg-type]
            source_snapshot_sha256=row["source_snapshot_sha256"],  # type: ignore[index,arg-type]
            captured_part_id=row["captured_part_id"],  # type: ignore[index,arg-type]
            captured_scope_epoch=row["captured_scope_epoch"],  # type: ignore[index,arg-type]
            available_targets_json=row["available_targets_json"],  # type: ignore[index,arg-type]
            rule_version=row["rule_version"],  # type: ignore[index,arg-type]
            deterministic_findings_json=row["deterministic_findings_json"],  # type: ignore[index,arg-type]
            ready_result_json=row["ready_result_json"],  # type: ignore[index,arg-type]
            result_schema_version=row["result_schema_version"],  # type: ignore[index,arg-type]
            status=row["status"],  # type: ignore[index,arg-type]
            error_json=row["error_json"],  # type: ignore[index,arg-type]
            expires_at_ms=row["expires_at"],  # type: ignore[index,arg-type]
            created_at_ms=row["created_at"],  # type: ignore[index,arg-type]
            updated_at_ms=row["updated_at"],  # type: ignore[index,arg-type]
            completed_at_ms=row["completed_at"],  # type: ignore[index,arg-type]
            row_version=row["row_version"],  # type: ignore[index,arg-type]
        )
    except (KeyError, TypeError, IndexError) as exc:
        raise PublicationPreviewIntegrityError("A preview row cannot be mapped.") from exc
    _validate_record(record)
    return record


def _validate_record(record: PublicationPreviewRecord) -> None:
    try:
        _validate_record_value(record)
    except PublicationPreviewIntegrityError:
        raise
    except (TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
        raise PublicationPreviewIntegrityError(
            "Stored publication preview integrity validation failed."
        ) from exc


def _validate_record_value(record: PublicationPreviewRecord) -> None:
    _uuid(record.preview_id, field="preview_id")
    _user(record.actor_user_id)
    _uuid(record.artifact_id, field="artifact_id")
    _optional_uuid(record.extraction_snapshot_id, field="extraction_snapshot_id")
    _optional_uuid(record.durable_job_id, field="durable_job_id")
    if record.source_snapshot_sha256 is not None:
        _sha(record.source_snapshot_sha256, field="source_snapshot_sha256")
    part_id = _optional_uuid(record.captured_part_id, field="captured_part_id")
    _integer(record.captured_scope_epoch, field="captured_scope_epoch", minimum=1)
    try:
        targets = json.loads(record.available_targets_json)
    except json.JSONDecodeError as exc:
        raise PublicationPreviewIntegrityError("Stored preview targets are invalid.") from exc
    if not isinstance(targets, list):
        raise PublicationPreviewIntegrityError("Stored preview targets are invalid.")
    _targets(tuple(targets), part_id=part_id)
    if _canonical(targets) != record.available_targets_json:
        raise PublicationPreviewIntegrityError("Stored preview targets are not canonical.")
    _text(record.rule_version, field="rule_version", maximum=120)
    if record.result_schema_version != PREVIEW_RESULT_SCHEMA_VERSION or record.status not in {
        "queued", "extracting", "ready", "consumed", "expired", "cancelled", "failed"
    }:
        raise PublicationPreviewIntegrityError("Stored preview state is invalid.")
    _integer(record.expires_at_ms, field="expires_at_ms")
    _integer(record.created_at_ms, field="created_at_ms")
    _integer(record.updated_at_ms, field="updated_at_ms")
    _integer(record.row_version, field="row_version", minimum=1)
    if record.expires_at_ms - record.created_at_ms != PREVIEW_TTL_MS:
        raise PublicationPreviewIntegrityError("Stored preview expiry is invalid.")
    if record.updated_at_ms < record.created_at_ms:
        raise PublicationPreviewIntegrityError("Stored preview chronology is invalid.")
    completed = record.completed_at_ms
    if completed is not None:
        _integer(completed, field="completed_at_ms")
        if completed < record.created_at_ms or record.updated_at_ms < completed:
            raise PublicationPreviewIntegrityError("Stored preview chronology is invalid.")

    findings_value: list[object] | None = None
    findings: tuple[PreviewFinding, ...] | None = None
    if record.deterministic_findings_json is not None:
        findings_value = json.loads(record.deterministic_findings_json)
        if _canonical(findings_value) != record.deterministic_findings_json:
            raise PublicationPreviewIntegrityError("Stored findings are invalid.")
        findings = _stored_findings(findings_value)

    ready = _object_or_none(record.ready_result_json, field="ready_result_json")
    error = _object_or_none(record.error_json, field="error_json")
    ready_backed = record.status in {"ready", "consumed", "expired"} or (
        record.status == "cancelled" and record.extraction_snapshot_id is not None
    )
    unready_cancelled = record.status == "cancelled" and not ready_backed

    if record.status in {"queued", "extracting"}:
        if any(
            value is not None
            for value in (
                record.extraction_snapshot_id,
                record.source_snapshot_sha256,
                findings_value,
                ready,
                error,
                completed,
            )
        ) or record.durable_job_id is None:
            raise PublicationPreviewIntegrityError("Stored unready preview shape is invalid.")
    elif record.status == "failed":
        if (
            record.durable_job_id is None
            or completed is None
            or error is None
            or any(
                value is not None
                for value in (
                    record.extraction_snapshot_id,
                    record.source_snapshot_sha256,
                    findings_value,
                    ready,
                )
            )
        ):
            raise PublicationPreviewIntegrityError("Stored failed preview shape is invalid.")
        if set(error) != {"code", "message"}:
            raise PublicationPreviewIntegrityError("Stored preview error is invalid.")
        code = _text(error["code"], field="error.code", maximum=80)
        _text(error["message"], field="error.message", maximum=500)
        if _ERROR_CODE_RE.fullmatch(code) is None:
            raise PublicationPreviewIntegrityError("Stored preview error is invalid.")
    elif unready_cancelled:
        if (
            record.durable_job_id is None
            or completed is None
            or any(
                value is not None
                for value in (
                    record.extraction_snapshot_id,
                    record.source_snapshot_sha256,
                    findings_value,
                    ready,
                    error,
                )
            )
        ):
            raise PublicationPreviewIntegrityError(
                "Stored cancelled preview shape is invalid."
            )
    elif ready_backed:
        if (
            record.extraction_snapshot_id is None
            or record.source_snapshot_sha256 is None
            or findings is None
            or ready is None
            or completed is None
            or error is not None
        ):
            raise PublicationPreviewIntegrityError("Stored ready preview shape is invalid.")
        required = {
            "available_target_modes",
            "completed_at",
            "created_at",
            "deterministic_findings",
            "expires_at",
            "job_id",
            "operation_id",
            "preview_id",
            "ready_result_schema_version",
            "request_id",
            "size_bytes",
            "source_snapshot_sha256",
            "status",
            "title",
            "updated_at",
            "warnings",
        }
        if set(ready) != required:
            raise PublicationPreviewIntegrityError("Stored ready result is invalid.")
        request_id = _uuid(ready["request_id"], field="request_id")
        operation_id = _uuid(ready["operation_id"], field="operation_id")
        title = _text(ready["title"], field="title", maximum=500)
        size = _integer(ready["size_bytes"], field="size_bytes")
        warning_value = ready["warnings"]
        if not isinstance(warning_value, list):
            raise PublicationPreviewIntegrityError("Stored ready warnings are invalid.")
        warnings = _warnings(tuple(warning_value))
        if not isinstance(ready["deterministic_findings"], list):
            raise PublicationPreviewIntegrityError("Stored ready findings are invalid.")
        ready_findings = _stored_findings(ready["deterministic_findings"])
        expected = _ready_result(
            ReadyPreviewDraft(
                preview_id=record.preview_id,
                request_id=request_id,
                operation_id=operation_id,
                actor_user_id=record.actor_user_id,
                artifact_id=record.artifact_id,
                extraction_snapshot_id=record.extraction_snapshot_id,
                durable_job_id=record.durable_job_id,
                source_snapshot_sha256=record.source_snapshot_sha256,
                captured_part_id=record.captured_part_id,
                captured_scope_epoch=record.captured_scope_epoch,
                available_target_modes=tuple(targets),  # type: ignore[arg-type]
                rule_version=record.rule_version,
                title=title,
                size_bytes=size,
                warnings=warnings,
                deterministic_findings=ready_findings,
                expires_at_ms=record.expires_at_ms,
                created_at_ms=record.created_at_ms,
                completed_at_ms=completed,
            )
        )
        if ready != expected or ready_findings != findings:
            raise PublicationPreviewIntegrityError("Stored ready result is inconsistent.")
        if record.status == "ready" and record.updated_at_ms != completed:
            raise PublicationPreviewIntegrityError("Stored ready chronology is invalid.")
        if record.status == "expired" and record.updated_at_ms < record.expires_at_ms:
            raise PublicationPreviewIntegrityError("Stored expiry chronology is invalid.")
    else:
        raise PublicationPreviewIntegrityError("Stored preview state is invalid.")


def _execute_transition(
    connection: ManagedConnection,
    sql: str,
    parameters: tuple[object, ...],
    *,
    message: str,
) -> None:
    try:
        cursor = connection.execute(sql, parameters)
    except sqlite3.IntegrityError as exc:
        raise PublicationPreviewConflictError(message) from exc
    if cursor.rowcount != 1:
        raise PublicationPreviewConflictError(message)


class PublicationPreviewRepository:
    __slots__ = ()

    def get(
        self, connection: ManagedConnection, preview_id: str
    ) -> PublicationPreviewRecord | None:
        connection = _connection(connection, write=False)
        preview_id = _uuid(preview_id, field="preview_id")
        row = connection.execute(
            f"SELECT {_COLUMNS} FROM publication_preview WHERE id=?", (preview_id,)
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def require_for_actor(
        self, connection: ManagedConnection, *, preview_id: str, actor_user_id: str
    ) -> PublicationPreviewRecord:
        record = self.get(connection, preview_id)
        actor = _user(actor_user_id)
        if record is None or record.actor_user_id != actor:
            raise PublicationPreviewNotFoundError("Publication preview was not found.")
        return record

    def insert_queued(
        self, connection: ManagedConnection, draft: QueuedPreviewDraft
    ) -> PublicationPreviewRecord:
        connection = _connection(connection, write=True)
        value = _prepare_queued(draft)
        existing = self.get(connection, value.preview_id)
        if existing is not None:
            expected = (
                value.actor_user_id,
                value.artifact_id,
                value.durable_job_id,
                value.captured_part_id,
                value.captured_scope_epoch,
                _canonical(list(value.available_target_modes)),
                value.rule_version,
                value.expires_at_ms,
                value.created_at_ms,
            )
            actual = (
                existing.actor_user_id,
                existing.artifact_id,
                existing.durable_job_id,
                existing.captured_part_id,
                existing.captured_scope_epoch,
                existing.available_targets_json,
                existing.rule_version,
                existing.expires_at_ms,
                existing.created_at_ms,
            )
            if existing.status != "queued" or actual != expected:
                raise PublicationPreviewConflictError(
                    "The preview ID is already bound incompatibly."
                )
            return existing
        try:
            connection.execute(
                """INSERT INTO publication_preview(
                     id,actor_user_id,artifact_id,extraction_snapshot_id,
                     durable_job_id,source_snapshot_sha256,captured_part_id,
                     captured_scope_epoch,available_targets_json,rule_version,
                     deterministic_findings_json,ready_result_json,
                     result_schema_version,status,error_json,expires_at,
                     created_at,updated_at,completed_at,row_version
                   ) VALUES (?,?,?,NULL,?,NULL,?,?,?, ?,NULL,NULL,1,'queued',
                     NULL,?,?,?,NULL,1)""",
                (
                    value.preview_id,
                    value.actor_user_id,
                    value.artifact_id,
                    value.durable_job_id,
                    value.captured_part_id,
                    value.captured_scope_epoch,
                    _canonical(list(value.available_target_modes)),
                    value.rule_version,
                    value.expires_at_ms,
                    value.created_at_ms,
                    value.created_at_ms,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PublicationPreviewConflictError(
                "The queued preview violates canonical state."
            ) from exc
        record = self.get(connection, value.preview_id)
        if record is None:
            raise PublicationPreviewIntegrityError("The queued preview was not stored.")
        return record

    def insert_ready(
        self, connection: ManagedConnection, draft: ReadyPreviewDraft
    ) -> PublicationPreviewRecord:
        connection = _connection(connection, write=True)
        value = _prepare_ready(draft)
        if value.durable_job_id is not None:
            raise ValueError("A synchronous ready preview cannot carry a durable job.")
        existing = self.get(connection, value.preview_id)
        if existing is not None:
            ready_json = _canonical(_ready_result(value))
            expected = (
                value.actor_user_id,
                value.artifact_id,
                value.extraction_snapshot_id,
                value.source_snapshot_sha256,
                value.captured_part_id,
                value.captured_scope_epoch,
                _canonical(list(value.available_target_modes)),
                value.rule_version,
                ready_json,
                value.expires_at_ms,
                value.created_at_ms,
                value.completed_at_ms,
            )
            actual = (
                existing.actor_user_id,
                existing.artifact_id,
                existing.extraction_snapshot_id,
                existing.source_snapshot_sha256,
                existing.captured_part_id,
                existing.captured_scope_epoch,
                existing.available_targets_json,
                existing.rule_version,
                existing.ready_result_json,
                existing.expires_at_ms,
                existing.created_at_ms,
                existing.completed_at_ms,
            )
            if existing.status == "ready" and existing.durable_job_id is None and actual == expected:
                return existing
            raise PublicationPreviewConflictError("The preview ID already exists.")
        return self._insert_ready(connection, value)

    def _insert_ready(
        self, connection: ManagedConnection, value: ReadyPreviewDraft
    ) -> PublicationPreviewRecord:
        findings_json = _canonical(
            [finding.to_mapping() for finding in value.deterministic_findings]
        )
        ready_json = _canonical(_ready_result(value))
        try:
            connection.execute(
                """INSERT INTO publication_preview(
                     id,actor_user_id,artifact_id,extraction_snapshot_id,
                     durable_job_id,source_snapshot_sha256,captured_part_id,
                     captured_scope_epoch,available_targets_json,rule_version,
                     deterministic_findings_json,ready_result_json,
                     result_schema_version,status,error_json,expires_at,
                     created_at,updated_at,completed_at,row_version
                   ) VALUES (?,?,?,?,?,?, ?,?,?,?, ?,?,1,'ready',NULL,?,?,?,?,1)""",
                (
                    value.preview_id,
                    value.actor_user_id,
                    value.artifact_id,
                    value.extraction_snapshot_id,
                    value.durable_job_id,
                    value.source_snapshot_sha256,
                    value.captured_part_id,
                    value.captured_scope_epoch,
                    _canonical(list(value.available_target_modes)),
                    value.rule_version,
                    findings_json,
                    ready_json,
                    value.expires_at_ms,
                    value.created_at_ms,
                    value.completed_at_ms,
                    value.completed_at_ms,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PublicationPreviewConflictError(
                "The ready preview violates canonical state."
            ) from exc
        record = self.get(connection, value.preview_id)
        if record is None or record.ready_result_json != ready_json:
            raise PublicationPreviewIntegrityError("The ready preview was not stored exactly.")
        return record

    def mark_extracting(
        self, connection: ManagedConnection, *, preview_id: str, updated_at_ms: int
    ) -> PublicationPreviewRecord:
        connection = _connection(connection, write=True)
        preview_id = _uuid(preview_id, field="preview_id")
        updated = _integer(updated_at_ms, field="updated_at_ms")
        current = self.get(connection, preview_id)
        if current is None:
            raise PublicationPreviewNotFoundError("Publication preview was not found.")
        if current.status == "extracting":
            return current
        if current.status != "queued" or updated < current.updated_at_ms:
            raise PublicationPreviewConflictError("Preview cannot begin extraction.")
        _execute_transition(
            connection,
            """UPDATE publication_preview
               SET status='extracting',updated_at=?,row_version=row_version+1
               WHERE id=? AND status='queued' AND row_version=?""",
            (updated, preview_id, current.row_version),
            message="Preview extraction state changed.",
        )
        result = self.get(connection, preview_id)
        if result is None or result.status != "extracting":
            raise PublicationPreviewConflictError("Preview extraction state changed.")
        return result

    def complete_ready(
        self, connection: ManagedConnection, draft: ReadyPreviewDraft
    ) -> PublicationPreviewRecord:
        connection = _connection(connection, write=True)
        value = _prepare_ready(draft)
        if value.durable_job_id is None:
            raise ValueError("An asynchronous completion requires its durable job.")
        current = self.get(connection, value.preview_id)
        if current is None:
            raise PublicationPreviewNotFoundError("Publication preview was not found.")
        if current.status == "ready":
            if current.ready_result_json == _canonical(_ready_result(value)):
                return current
            raise PublicationPreviewConflictError("The ready result is immutable.")
        expected = (
            value.actor_user_id,
            value.artifact_id,
            value.durable_job_id,
            value.captured_part_id,
            value.captured_scope_epoch,
            _canonical(list(value.available_target_modes)),
            value.rule_version,
            value.expires_at_ms,
            value.created_at_ms,
        )
        actual = (
            current.actor_user_id,
            current.artifact_id,
            current.durable_job_id,
            current.captured_part_id,
            current.captured_scope_epoch,
            current.available_targets_json,
            current.rule_version,
            current.expires_at_ms,
            current.created_at_ms,
        )
        if current.status != "extracting" or actual != expected:
            raise PublicationPreviewConflictError("Preview cannot complete with this result.")
        findings_json = _canonical(
            [finding.to_mapping() for finding in value.deterministic_findings]
        )
        ready_json = _canonical(_ready_result(value))
        try:
            cursor = connection.execute(
                """UPDATE publication_preview SET
                     extraction_snapshot_id=?,source_snapshot_sha256=?,
                     deterministic_findings_json=?,ready_result_json=?,
                     status='ready',error_json=NULL,updated_at=?,completed_at=?,
                     row_version=row_version+1
                   WHERE id=? AND status='extracting' AND row_version=?""",
                (
                    value.extraction_snapshot_id,
                    value.source_snapshot_sha256,
                    findings_json,
                    ready_json,
                    value.completed_at_ms,
                    value.completed_at_ms,
                    value.preview_id,
                    current.row_version,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PublicationPreviewConflictError(
                "The asynchronous ready result violates canonical state."
            ) from exc
        if cursor.rowcount != 1:
            raise PublicationPreviewConflictError("Preview completion lost its fence.")
        result = self.get(connection, value.preview_id)
        if result is None or result.ready_result_json != ready_json:
            raise PublicationPreviewIntegrityError("The ready result was not stored exactly.")
        return result

    def fail(
        self,
        connection: ManagedConnection,
        *,
        preview_id: str,
        error: Mapping[str, object],
        completed_at_ms: int,
    ) -> PublicationPreviewRecord:
        connection = _connection(connection, write=True)
        preview_id = _uuid(preview_id, field="preview_id")
        completed = _integer(completed_at_ms, field="completed_at_ms")
        if not isinstance(error, Mapping):
            raise TypeError("error must be a mapping.")
        error_json = _canonical(dict(error))
        parsed = json.loads(error_json)
        if set(parsed) != {"code", "message"}:
            raise ValueError("Preview errors require only code and message.")
        code = _text(parsed["code"], field="error.code", maximum=80)
        if _ERROR_CODE_RE.fullmatch(code) is None:
            raise ValueError("Preview error code must use the frozen safe vocabulary shape.")
        _text(parsed["message"], field="error.message", maximum=500)
        current = self.get(connection, preview_id)
        if current is None:
            raise PublicationPreviewNotFoundError("Publication preview was not found.")
        if current.status == "failed":
            if current.error_json == error_json and current.completed_at_ms == completed:
                return current
            raise PublicationPreviewConflictError("The preview failure is immutable.")
        if current.status not in {"queued", "extracting"} or completed < current.updated_at_ms:
            raise PublicationPreviewConflictError("Preview cannot fail from its current state.")
        _execute_transition(
            connection,
            """UPDATE publication_preview SET status='failed',error_json=?,
                 updated_at=?,completed_at=?,row_version=row_version+1
               WHERE id=? AND status=? AND row_version=?""",
            (
                error_json,
                completed,
                completed,
                preview_id,
                current.status,
                current.row_version,
            ),
            message="Preview failure lost its fence.",
        )
        result = self.get(connection, preview_id)
        if result is None or result.status != "failed":
            raise PublicationPreviewConflictError("Preview failure lost its fence.")
        return result

    def cancel(
        self, connection: ManagedConnection, *, preview_id: str, completed_at_ms: int
    ) -> PublicationPreviewRecord:
        connection = _connection(connection, write=True)
        preview_id = _uuid(preview_id, field="preview_id")
        completed = _integer(completed_at_ms, field="completed_at_ms")
        current = self.get(connection, preview_id)
        if current is None:
            raise PublicationPreviewNotFoundError("Publication preview was not found.")
        if current.status == "cancelled":
            return current
        if current.status not in {"queued", "extracting", "ready"} or completed < current.updated_at_ms:
            raise PublicationPreviewConflictError("Preview cannot be cancelled.")
        terminal_completed = (
            current.completed_at_ms if current.status == "ready" else completed
        )
        if terminal_completed is None:
            raise PublicationPreviewIntegrityError(
                "A ready preview has no immutable completion time."
            )
        _execute_transition(
            connection,
            """UPDATE publication_preview SET status='cancelled',error_json=NULL,
                 updated_at=?,completed_at=?,row_version=row_version+1
               WHERE id=? AND status=? AND row_version=?""",
            (
                completed,
                terminal_completed,
                preview_id,
                current.status,
                current.row_version,
            ),
            message="Preview cancellation lost its fence.",
        )
        result = self.get(connection, preview_id)
        if result is None or result.status != "cancelled":
            raise PublicationPreviewConflictError("Preview cancellation lost its fence.")
        return result

    def expire_ready(
        self, connection: ManagedConnection, *, preview_id: str, now_ms: int
    ) -> PublicationPreviewRecord:
        connection = _connection(connection, write=True)
        preview_id = _uuid(preview_id, field="preview_id")
        now = _integer(now_ms, field="now_ms")
        current = self.get(connection, preview_id)
        if current is None:
            raise PublicationPreviewNotFoundError("Publication preview was not found.")
        if current.status == "expired":
            return current
        if current.status != "ready" or now < current.expires_at_ms:
            raise PublicationPreviewConflictError("Preview is not eligible to expire.")
        _execute_transition(
            connection,
            """UPDATE publication_preview SET status='expired',updated_at=?,
                 row_version=row_version+1
               WHERE id=? AND status='ready' AND row_version=?""",
            (now, preview_id, current.row_version),
            message="Preview expiry lost its fence.",
        )
        result = self.get(connection, preview_id)
        if result is None or result.status != "expired":
            raise PublicationPreviewConflictError("Preview expiry lost its fence.")
        return result
