"""Actor-authorized P7.1 durable job status and cancellation service.

The durable repository and worker kernel own enqueue, claim fencing, recovery,
and effect/result atomicity.  This service adds only the public actor boundary:
an authenticated user may read or idempotently request cancellation of their
own durable row.  It starts no worker and executes no handler.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from uuid import UUID, uuid5

from ..db import ConnectionFactory, TransactionMode, db_now_ms, transaction
from ..errors import KnowledgeCoreError
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.jobs import (
    DurableJobRecord,
    JobCancellationUnsupportedError,
    JobRepository,
    JobRepositoryError,
    JobStateConflictError,
)
from ..repositories.users import (
    OperationIdempotencyConflictError,
    UserNotFoundError,
    UserRecord,
    UserRepository,
)
from ..scope import SessionPrincipal


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_DEDUP_NAMESPACE = UUID("8886d921-e4ca-4cac-b1d7-e2abf5203736")
_AUDIT_NAMESPACE = UUID("d63801d8-0ab0-455b-bc6a-c6ee8d8fa350")
_REQUEST_META_ALLOWLIST = frozenset(
    {"remote_addr", "method", "path", "host", "user_agent"}
)
_SENSITIVE_META_FRAGMENTS = (
    "authorization",
    "cookie",
    "csrf",
    "password",
    "secret",
    "token",
)
_PUBLIC_STATUSES = frozenset(
    {
        "queued",
        "claimed",
        "running",
        "retry",
        "succeeded",
        "failed",
        "cancelled",
        "dead",
        "cancel_requested",
    }
)


class DurableJobServiceError(KnowledgeCoreError):
    code = "STATE_CONFLICT"
    http_status = 409
    retryable = False

    def __init__(
        self, message: str, *, details: Mapping[str, object] | None = None
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})


class DurableJobValidationError(DurableJobServiceError):
    code = "VALIDATION_ERROR"
    http_status = 400


class DurableJobNotFoundError(DurableJobServiceError):
    code = "NOT_FOUND"
    http_status = 404


class DurableJobScopeStaleError(DurableJobServiceError):
    code = "SCOPE_STALE"


class DurableJobIdempotencyError(DurableJobServiceError):
    code = "IDEMPOTENCY_CONFLICT"


class DurableJobOperationInProgressError(DurableJobServiceError):
    code = "STATE_CONFLICT"
    retryable = True


class DurableJobCancellationError(DurableJobServiceError):
    code = "STATE_CONFLICT"


class DurableJobServiceIntegrityError(DurableJobServiceError):
    code = "INTERNAL_ERROR"
    http_status = 500


@dataclass(frozen=True, slots=True)
class ReadDurableJobRequest:
    principal: SessionPrincipal
    job_id: str
    request_id: str


@dataclass(frozen=True, slots=True)
class CancelDurableJobCommand:
    principal: SessionPrincipal
    job_id: str
    operation_id: str
    request_id: str
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class DurableJobStatusResult:
    request_id: str
    job: DurableJobRecord


@dataclass(frozen=True, slots=True)
class CancelDurableJobResult:
    request_id: str
    operation_id: str
    resource_id: str
    status: str
    row_version: None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "request_id": self.request_id,
            "resource_id": self.resource_id,
            "row_version": self.row_version,
            "status": self.status,
        }

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, object]
    ) -> "CancelDurableJobResult":
        if set(value) != {
            "operation_id",
            "request_id",
            "resource_id",
            "row_version",
            "status",
        }:
            raise DurableJobServiceIntegrityError(
                "Stored durable job cancellation result is invalid."
            )
        operation_id = _uuid(value["operation_id"], "stored operation_id")
        request_id = _uuid(value["request_id"], "stored request_id")
        resource_id = _uuid(value["resource_id"], "stored resource_id")
        status = value["status"]
        if status not in _PUBLIC_STATUSES or value["row_version"] is not None:
            raise DurableJobServiceIntegrityError(
                "Stored durable job cancellation result is invalid."
            )
        return cls(
            request_id=request_id,
            operation_id=operation_id,
            resource_id=resource_id,
            status=status,
        )


def _uuid(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise DurableJobValidationError(
            f"{field_name} must be a canonical lowercase UUID."
        )
    return value


def _derived_uuid(namespace: UUID, *parts: str) -> str:
    return str(uuid5(namespace, "\x00".join(parts)))


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise DurableJobValidationError(
            "Durable job command input is invalid."
        ) from exc


def _safe_request_meta(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise DurableJobValidationError("request_meta must be an object.")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or key not in _REQUEST_META_ALLOWLIST:
            continue
        if isinstance(item, bool):
            result[key] = item
        elif isinstance(item, int) and not isinstance(item, bool):
            result[key] = item
        elif isinstance(item, str) and len(item) <= 1_000:
            folded = item.casefold()
            if not any(fragment in folded for fragment in _SENSITIVE_META_FRAGMENTS):
                result[key] = item
    return result


class DurableJobService:
    """Read/cancel actor-owned durable jobs without running worker code."""

    __slots__ = ("_audit", "_factory", "_failpoint", "_jobs", "_users")

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        jobs: JobRepository | None = None,
        users: UserRepository | None = None,
        audit: AuditRepository | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("DurableJobService requires a ConnectionFactory.")
        if failpoint is not None and not callable(failpoint):
            raise TypeError("failpoint must be callable or None.")
        self._factory = factory
        self._jobs = jobs or JobRepository()
        self._users = users or UserRepository()
        self._audit = audit or AuditRepository()
        self._failpoint = failpoint

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    def _active_actor(self, connection, principal: SessionPrincipal) -> UserRecord:
        if not isinstance(principal, SessionPrincipal):
            raise DurableJobValidationError(
                "A canonical session principal is required."
            )
        try:
            actor = self._users.require(connection, principal.user_id)
        except UserNotFoundError as exc:
            raise DurableJobScopeStaleError(
                "The durable job session is no longer current."
            ) from exc
        if (
            actor.account_status != "active"
            or actor.scope_epoch != principal.captured_scope_epoch
        ):
            raise DurableJobScopeStaleError(
                "The durable job session is no longer current."
            )
        return actor

    def _owned_job(
        self, connection, *, actor: UserRecord, job_id: str
    ) -> DurableJobRecord:
        try:
            job = self._jobs.get(connection, job_id)
        except JobRepositoryError as exc:
            raise DurableJobServiceIntegrityError(
                "Stored durable job state is invalid."
            ) from exc
        if (
            job is None
            or job.actor_kind != "user"
            or job.actor_user_id != actor.id
        ):
            raise DurableJobNotFoundError(
                "The requested durable job was not found."
            )
        return job

    def read(self, request: ReadDurableJobRequest) -> DurableJobStatusResult:
        if not isinstance(request, ReadDurableJobRequest):
            raise TypeError("read requires a ReadDurableJobRequest.")
        job_id = _uuid(request.job_id, "job_id")
        request_id = _uuid(request.request_id, "request_id")
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                actor = self._active_actor(connection, request.principal)
                job = self._owned_job(connection, actor=actor, job_id=job_id)
                return DurableJobStatusResult(request_id=request_id, job=job)
        finally:
            connection.close()

    def cancel(self, command: CancelDurableJobCommand) -> CancelDurableJobResult:
        if not isinstance(command, CancelDurableJobCommand):
            raise TypeError("cancel requires a CancelDurableJobCommand.")
        job_id = _uuid(command.job_id, "job_id")
        operation_id = _uuid(command.operation_id, "operation_id")
        request_id = _uuid(command.request_id, "request_id")
        if not isinstance(command.principal, SessionPrincipal):
            raise DurableJobValidationError(
                "A canonical session principal is required."
            )
        safe_meta = _safe_request_meta(command.request_meta)
        operation_type = "durable_job.cancel"
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE,
            command.principal.user_id,
            operation_type,
            operation_id,
        )
        request_hash = sha256(
            _canonical_bytes(
                {
                    "actor_scope_epoch": command.principal.captured_scope_epoch,
                    "actor_user_id": command.principal.user_id,
                    "job_id": job_id,
                    "operation_type": operation_type,
                    "schema_version": 1,
                }
            )
        ).hexdigest()
        connection = self._factory.open()
        try:
            with transaction(connection):
                actor = self._active_actor(connection, command.principal)
                self._owned_job(connection, actor=actor, job_id=job_id)
                now_ms = db_now_ms(connection)
                try:
                    begun = self._users.begin_operation(
                        connection,
                        actor_user_id=actor.id,
                        operation_type=operation_type,
                        operation_id=operation_id,
                        request_sha256=request_hash,
                        dedup_id=dedup_id,
                        now_ms=now_ms,
                    )
                except OperationIdempotencyConflictError as exc:
                    raise DurableJobIdempotencyError(str(exc)) from exc
                if not begun.inserted:
                    if begun.operation.status == "committed":
                        stored = begun.operation.result
                        if stored is None:
                            raise DurableJobServiceIntegrityError(
                                "Committed durable job cancellation has no result."
                            )
                        replay = CancelDurableJobResult.from_mapping(stored)
                        if (
                            replay.operation_id != operation_id
                            or replay.resource_id != job_id
                        ):
                            raise DurableJobServiceIntegrityError(
                                "Stored durable job cancellation is not bound to this command."
                            )
                        return CancelDurableJobResult(
                            request_id=request_id,
                            operation_id=replay.operation_id,
                            resource_id=replay.resource_id,
                            status=replay.status,
                        )
                    raise DurableJobOperationInProgressError(
                        "The durable job cancellation is already in progress."
                    )
                try:
                    cancelled = self._jobs.request_cancel(
                        connection,
                        job_id=job_id,
                        now_ms=now_ms,
                    )
                except JobCancellationUnsupportedError as exc:
                    raise DurableJobCancellationError(str(exc)) from exc
                except JobStateConflictError as exc:
                    raise DurableJobServiceError(str(exc)) from exc
                except JobRepositoryError as exc:
                    raise DurableJobServiceIntegrityError(
                        "Stored durable job state is invalid."
                    ) from exc
                self._hit("after_job_cancel")
                status = (
                    "cancel_requested"
                    if cancelled.job.cancel_requested
                    and cancelled.job.state not in {"cancelled"}
                    else cancelled.job.state
                )
                result = CancelDurableJobResult(
                    request_id=request_id,
                    operation_id=operation_id,
                    resource_id=cancelled.job.id,
                    status=status,
                )
                self._audit.append(
                    connection,
                    AuditEventDraft(
                        event_uuid=_derived_uuid(
                            _AUDIT_NAMESPACE, dedup_id, "cancel"
                        ),
                        occurred_at_ms=now_ms,
                        request_id=request_id,
                        operation_id=operation_id,
                        action="durable_job.cancel",
                        outcome=status,
                        actor_kind="user",
                        actor_user_id=actor.id,
                        actor_display_name=actor.display_name,
                        actor_role_snapshot=actor.system_role,
                        space_id=cancelled.job.space_id,
                        reason="User requested durable job cancellation.",
                        details={
                            "attempt_count": cancelled.job.attempt_count,
                            "cancel_requested": cancelled.job.cancel_requested,
                            "changed": cancelled.changed,
                            "handler_version": cancelled.job.handler_version,
                            "immediate": cancelled.immediate,
                            "job_id": cancelled.job.id,
                            "job_type": cancelled.job.job_type,
                            "state": cancelled.job.state,
                        },
                        request_meta=safe_meta,
                    ),
                )
                self._hit("after_audit")
                self._users.commit_operation(
                    connection,
                    dedup_id=dedup_id,
                    result=result.as_dict(),
                    http_status=200,
                    completed_at_ms=now_ms,
                )
                self._hit("after_operation_commit")
                return result
        finally:
            connection.close()


__all__ = [
    "CancelDurableJobCommand",
    "CancelDurableJobResult",
    "DurableJobCancellationError",
    "DurableJobIdempotencyError",
    "DurableJobNotFoundError",
    "DurableJobOperationInProgressError",
    "DurableJobScopeStaleError",
    "DurableJobService",
    "DurableJobServiceError",
    "DurableJobServiceIntegrityError",
    "DurableJobStatusResult",
    "DurableJobValidationError",
    "ReadDurableJobRequest",
]
