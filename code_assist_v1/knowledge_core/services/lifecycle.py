"""Transaction-owning P5.3 item deactivation and restore boundary.

The service is deliberately not route-registered and enables no rollout flag.
It re-resolves canonical scope at every write boundary, uses SQLite time, binds
each command to durable idempotency, and commits the lifecycle state, episode,
search visibility, audit, and outbox as one transaction.  Blob-backed Markdown
is loaded and hash-verified outside the write transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from uuid import UUID, uuid5

from ..db import (
    ConnectionFactory,
    ManagedConnection,
    TransactionMode,
    db_now_ms,
    transaction,
)
from ..errors import KnowledgeCoreError
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.leases import LeaseHeldRepositoryError, LeaseRecord
from ..repositories.lifecycle import (
    DeactivateDraft,
    DeactivateWriteResult,
    ItemLifecycleRepository,
    LifecycleIntegrityError,
    LifecycleItemNotFoundError,
    LifecycleStateConflictError,
    RestoreDraft,
    RestoreSearchEvidence,
    RestoreWriteResult,
    verify_restore_search_content,
)
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.semantic_graph_contracts import (
    SemanticGraphContractRepository,
    SemanticGraphContractRepositoryError,
)
from ..repositories.users import (
    OperationIdempotencyConflictError,
    OperationInProgressError,
    UserRecord,
    UserRepository,
)
from ..scope import (
    Capability,
    ScopeAuthenticationRequiredError,
    ScopeForbiddenError,
    ScopeIntegrityError,
    ScopeNotFoundError,
    ScopeResolver,
    ScopeStaleError,
    ScopeValidationError,
    SessionPrincipal,
)


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_DEDUP_NAMESPACE = UUID("af1e96cb-127f-4fea-8718-26c060dc5608")
_EPISODE_NAMESPACE = UUID("bd59bed7-4212-4b9a-9795-d0bdd170316a")
_AUDIT_NAMESPACE = UUID("ff172021-8cc7-42fa-b9ae-672d683b7bd5")
_REQUEST_META_ALLOWLIST = frozenset(
    {"remote_addr", "method", "path", "host", "user_agent"}
)
_SENSITIVE_META_FRAGMENTS = (
    "authorization",
    "cookie",
    "csrf",
    "lease",
    "password",
    "secret",
    "token",
)


class LifecycleServiceError(KnowledgeCoreError):
    code = "STATE_CONFLICT"
    http_status = 409
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.details = dict(details or {})

    def as_error(self) -> dict[str, object]:
        return {
            "code": self.code,
            "details": self.details,
            "message": str(self),
            "retryable": self.retryable,
        }


class LifecycleValidationError(LifecycleServiceError):
    code = "VALIDATION_ERROR"
    http_status = 400


class LifecycleAuthenticationError(LifecycleServiceError):
    code = "AUTHENTICATION_REQUIRED"
    http_status = 401


class LifecycleAuthorizationError(LifecycleServiceError):
    code = "FORBIDDEN"
    http_status = 403


class LifecycleNotFoundError(LifecycleServiceError):
    code = "NOT_FOUND"
    http_status = 404


class LifecycleScopeStaleError(LifecycleServiceError):
    code = "SCOPE_STALE"


class LifecycleLeaseHeldError(LifecycleServiceError):
    code = "LEASE_HELD"
    http_status = 423


class LifecycleIdempotencyError(LifecycleServiceError):
    code = "IDEMPOTENCY_CONFLICT"


class LifecycleOperationInProgressError(LifecycleServiceError):
    retryable = True


class LifecycleServiceIntegrityError(LifecycleServiceError):
    code = "INTERNAL_ERROR"
    http_status = 500


class LifecycleFailpointTriggered(LifecycleServiceError):
    code = "LIFECYCLE_FAILPOINT_TRIGGERED"
    http_status = 500
    retryable = True


@dataclass(frozen=True, slots=True)
class DeactivateItemCommand:
    principal: SessionPrincipal
    space_id: str
    item_id: str
    expected_item_row_version: int
    operation_id: str
    request_id: str
    reason: str
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class RestoreItemCommand:
    principal: SessionPrincipal
    space_id: str
    item_id: str
    expected_item_row_version: int
    operation_id: str
    request_id: str
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


def _count_map(value: object, *, keys: frozenset[str]) -> dict[str, int]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError("Stored lifecycle count shape is invalid.")
    result: dict[str, int] = {}
    for key in sorted(keys):
        count = value[key]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("Stored lifecycle count is invalid.")
        result[key] = count
    return result


@dataclass(frozen=True, slots=True)
class DeactivateItemResult:
    request_id: str
    operation_id: str
    item_id: str
    row_version: int
    deactivated_at: str
    deactivated_by: str
    purge_eligible_at: str
    edge_counts: Mapping[str, int]
    reference_counts: Mapping[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "deactivated_at": self.deactivated_at,
            "deactivated_by": self.deactivated_by,
            "edge_counts": dict(self.edge_counts),
            "item_id": self.item_id,
            "operation_id": self.operation_id,
            "purge_eligible_at": self.purge_eligible_at,
            "reference_counts": dict(self.reference_counts),
            "request_id": self.request_id,
            "row_version": self.row_version,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "DeactivateItemResult":
        if set(value) != {
            "deactivated_at",
            "deactivated_by",
            "edge_counts",
            "item_id",
            "operation_id",
            "purge_eligible_at",
            "reference_counts",
            "request_id",
            "row_version",
        }:
            raise ValueError("Stored deactivation result shape is invalid.")
        actor = value["deactivated_by"]
        if not isinstance(actor, str) or not 1 <= len(actor) <= 300:
            raise ValueError("Stored deactivation actor snapshot is invalid.")
        edge_counts = _count_map(
            value["edge_counts"],
            keys=frozenset(
                {
                    "active_incoming",
                    "active_outgoing",
                    "inactive_incoming",
                    "inactive_outgoing",
                    "total",
                }
            ),
        )
        reference_counts = _count_map(
            value["reference_counts"],
            keys=frozenset(
                {
                    "active_incoming",
                    "active_outgoing",
                    "conversation_pins",
                    "inactive_incoming",
                    "inactive_outgoing",
                    "total",
                }
            ),
        )
        if edge_counts["total"] != sum(
            edge_counts[key]
            for key in (
                "active_incoming",
                "active_outgoing",
                "inactive_incoming",
                "inactive_outgoing",
            )
        ):
            raise ValueError("Stored lifecycle edge counts do not reconcile.")
        if reference_counts["total"] != sum(
            reference_counts[key]
            for key in (
                "active_incoming",
                "active_outgoing",
                "conversation_pins",
                "inactive_incoming",
                "inactive_outgoing",
            )
        ):
            raise ValueError("Stored lifecycle reference counts do not reconcile.")
        deactivated_at = _utc_iso(
            value["deactivated_at"], field="deactivated_at"
        )
        purge_eligible_at = _utc_iso(
            value["purge_eligible_at"], field="purge_eligible_at"
        )
        deactivated_ms = int(
            datetime.fromisoformat(deactivated_at.replace("Z", "+00:00")).timestamp()
            * 1000
        )
        purge_ms = int(
            datetime.fromisoformat(
                purge_eligible_at.replace("Z", "+00:00")
            ).timestamp()
            * 1000
        )
        if purge_ms - deactivated_ms != 7_776_000_000:
            raise ValueError("Stored purge eligibility is not exactly 90 days.")
        return cls(
            request_id=_uuid(value["request_id"], field="request_id"),
            operation_id=_uuid(value["operation_id"], field="operation_id"),
            item_id=_uuid(value["item_id"], field="item_id"),
            row_version=_positive(value["row_version"], field="row_version"),
            deactivated_at=deactivated_at,
            deactivated_by=actor,
            purge_eligible_at=purge_eligible_at,
            edge_counts=edge_counts,
            reference_counts=reference_counts,
        )


@dataclass(frozen=True, slots=True)
class RestoreItemResult:
    request_id: str
    operation_id: str
    item_id: str
    revision_id: str
    row_version: int
    inactive_duration_seconds: int
    review_job_queued: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "inactive_duration_seconds": self.inactive_duration_seconds,
            "item_id": self.item_id,
            "operation_id": self.operation_id,
            "request_id": self.request_id,
            "review_job_queued": self.review_job_queued,
            "revision_id": self.revision_id,
            "row_version": self.row_version,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "RestoreItemResult":
        if set(value) != {
            "inactive_duration_seconds",
            "item_id",
            "operation_id",
            "request_id",
            "review_job_queued",
            "revision_id",
            "row_version",
        }:
            raise ValueError("Stored restore result shape is invalid.")
        duration = value["inactive_duration_seconds"]
        queued = value["review_job_queued"]
        if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
            raise ValueError("Stored inactive duration is invalid.")
        if queued is not False:
            raise ValueError("Stored review-job result is invalid.")
        return cls(
            request_id=_uuid(value["request_id"], field="request_id"),
            operation_id=_uuid(value["operation_id"], field="operation_id"),
            item_id=_uuid(value["item_id"], field="item_id"),
            revision_id=_uuid(value["revision_id"], field="revision_id"),
            row_version=_positive(value["row_version"], field="row_version"),
            inactive_duration_seconds=duration,
            review_job_queued=queued,
        )


def _uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_ID_RE.fullmatch(value) is None:
        raise ValueError("principal.user_id must be a canonical user ID.")
    return value


def _positive(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _reason(value: object) -> str:
    if not isinstance(value, str):
        raise LifecycleValidationError("A deactivation reason is required.")
    result = value.strip()
    if not 1 <= len(result) <= 2000:
        raise LifecycleValidationError(
            "A deactivation reason must contain 1..2000 characters."
        )
    return result


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _derived_uuid(namespace: UUID, *parts: object) -> str:
    return str(uuid5(namespace, "\x00".join(str(part) for part in parts)))


def _iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _utc_iso(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be canonical UTC milliseconds.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be canonical UTC milliseconds.") from exc
    if parsed.tzinfo != timezone.utc:
        raise ValueError(f"{field} must use UTC Z.")
    canonical = parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if canonical != value:
        raise ValueError(f"{field} must be canonical UTC milliseconds.")
    return value


def _safe_request_meta(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise LifecycleValidationError("request_meta must be an object.")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or key not in _REQUEST_META_ALLOWLIST:
            continue
        if any(fragment in key.casefold() for fragment in _SENSITIVE_META_FRAGMENTS):
            continue
        if isinstance(item, bool):
            result[key] = item
        elif isinstance(item, int) and not isinstance(item, bool):
            result[key] = item
        elif isinstance(item, str) and len(item) <= 1000:
            folded = item.casefold()
            if not any(fragment in folded for fragment in _SENSITIVE_META_FRAGMENTS):
                result[key] = item
    return result


class ItemLifecycleService:
    """Disabled P5.3 service for exact deactivate/restore episodes."""

    __slots__ = (
        "_audit",
        "_content_loader",
        "_factory",
        "_failpoint",
        "_lifecycle",
        "_outbox",
        "_semantic_graph",
        "_scope",
        "_users",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        scope_resolver: ScopeResolver,
        *,
        content_loader: Callable[[str, int], bytes | str] | None = None,
        lifecycle: ItemLifecycleRepository | None = None,
        users: UserRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        semantic_graph: SemanticGraphContractRepository | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("ItemLifecycleService requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("ItemLifecycleService requires a ScopeResolver.")
        if content_loader is not None and not callable(content_loader):
            raise TypeError("content_loader must be callable.")
        self._factory = factory
        self._scope = scope_resolver
        self._content_loader = content_loader
        self._lifecycle = lifecycle or ItemLifecycleRepository()
        self._users = users or UserRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()
        self._semantic_graph = semantic_graph or SemanticGraphContractRepository()
        self._failpoint = failpoint

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    @staticmethod
    def _validate_common(
        *,
        principal: SessionPrincipal,
        space_id: str,
        item_id: str,
        expected_item_row_version: int,
        operation_id: str,
        request_id: str,
    ) -> None:
        if not isinstance(principal, SessionPrincipal):
            raise TypeError("Lifecycle commands require a SessionPrincipal.")
        _user(principal.user_id)
        _positive(principal.captured_scope_epoch, field="principal.captured_scope_epoch")
        _uuid(space_id, field="space_id")
        _uuid(item_id, field="item_id")
        _positive(expected_item_row_version, field="expected_item_row_version")
        _uuid(operation_id, field="operation_id")
        _uuid(request_id, field="request_id")

    def _resolve(
        self,
        connection: ManagedConnection,
        *,
        principal: SessionPrincipal,
        space_id: str,
        capability: Capability,
    ) -> UserRecord:
        try:
            resolved = self._scope.resolve_in_transaction(
                connection, principal, space_id, capability
            )
        except ScopeAuthenticationRequiredError as exc:
            raise LifecycleAuthenticationError(str(exc)) from exc
        except ScopeStaleError as exc:
            raise LifecycleScopeStaleError(str(exc)) from exc
        except ScopeNotFoundError as exc:
            raise LifecycleNotFoundError(str(exc)) from exc
        except ScopeForbiddenError as exc:
            raise LifecycleAuthorizationError(str(exc)) from exc
        except ScopeValidationError as exc:
            raise LifecycleValidationError(str(exc)) from exc
        except ScopeIntegrityError as exc:
            raise LifecycleServiceIntegrityError(str(exc)) from exc
        actor = self._users.require(connection, resolved.actor_user_id)
        if actor.account_status != "active" or actor.scope_epoch != resolved.scope_epoch:
            raise LifecycleScopeStaleError("The canonical actor scope changed.")
        return actor

    @staticmethod
    def _request_hash(
        *,
        operation_type: str,
        principal: SessionPrincipal,
        space_id: str,
        item_id: str,
        expected_row_version: int,
        reason: str | None,
    ) -> str:
        value: dict[str, object] = {
            "actor_scope_epoch": principal.captured_scope_epoch,
            "actor_user_id": principal.user_id,
            "expected_item_row_version": expected_row_version,
            "item_id": item_id,
            "operation_type": operation_type,
            "schema_version": 1,
            "space_id": space_id,
        }
        if reason is not None:
            value["reason"] = reason
        return sha256(_canonical_json(value)).hexdigest()

    def _begin(
        self,
        connection: ManagedConnection,
        *,
        actor: UserRecord,
        operation_type: str,
        operation_id: str,
        request_hash: str,
        dedup_id: str,
        now_ms: int,
    ):
        try:
            return self._users.begin_operation(
                connection,
                actor_user_id=actor.id,
                operation_type=operation_type,
                operation_id=operation_id,
                request_sha256=request_hash,
                dedup_id=dedup_id,
                now_ms=now_ms,
            )
        except OperationIdempotencyConflictError as exc:
            raise LifecycleIdempotencyError(str(exc)) from exc

    def _commit(
        self,
        connection: ManagedConnection,
        *,
        dedup_id: str,
        result: Mapping[str, object],
        now_ms: int,
    ) -> None:
        try:
            self._users.commit_operation(
                connection,
                dedup_id=dedup_id,
                result=result,
                http_status=200,
                completed_at_ms=now_ms,
            )
        except (OperationIdempotencyConflictError, OperationInProgressError) as exc:
            raise LifecycleIdempotencyError(str(exc)) from exc

    def _fail_final(
        self,
        connection: ManagedConnection,
        *,
        dedup_id: str,
        error: LifecycleServiceError,
        now_ms: int,
    ) -> None:
        try:
            self._users.fail_operation_final(
                connection,
                dedup_id=dedup_id,
                error=error.as_error(),
                http_status=error.http_status,
                completed_at_ms=now_ms,
            )
        except (OperationIdempotencyConflictError, OperationInProgressError) as exc:
            raise LifecycleIdempotencyError(str(exc)) from exc

    @staticmethod
    def _replay_error(value: Mapping[str, object]) -> LifecycleServiceError:
        code = value.get("code")
        message = value.get("message")
        details = value.get("details")
        if not isinstance(message, str) or not isinstance(details, Mapping):
            raise LifecycleIdempotencyError("Stored lifecycle error is invalid.")
        error_types: dict[object, type[LifecycleServiceError]] = {
            "LEASE_HELD": LifecycleLeaseHeldError,
            "NOT_FOUND": LifecycleNotFoundError,
            "STATE_CONFLICT": LifecycleServiceError,
        }
        error_type = error_types.get(code)
        if error_type is None:
            raise LifecycleIdempotencyError("Stored lifecycle error code is invalid.")
        return error_type(message, details=details)

    @staticmethod
    def _held_error(exc: LeaseHeldRepositoryError) -> LifecycleLeaseHeldError:
        lease: LeaseRecord = exc.lease
        details: dict[str, object] = {
            "fencing_generation": lease.fencing_generation,
            "holder_user_id": lease.holder_user_id,
        }
        if lease.heartbeat_at_ms is not None:
            details["heartbeat_at"] = _iso_from_ms(lease.heartbeat_at_ms)
        if lease.expires_at_ms is not None:
            details["expires_at"] = _iso_from_ms(lease.expires_at_ms)
        return LifecycleLeaseHeldError(str(exc), details=details)

    @staticmethod
    def _state_error(exc: LifecycleStateConflictError) -> LifecycleServiceError:
        return LifecycleServiceError(
            str(exc),
            details={
                "current_revision_id": exc.current_revision_id,
                "current_row_version": exc.current_row_version,
                "current_status": exc.current_status,
            },
        )

    @staticmethod
    def _validate_deactivate_replay(
        result: DeactivateItemResult, command: DeactivateItemCommand
    ) -> DeactivateItemResult:
        if (
            result.operation_id != command.operation_id
            or result.item_id != command.item_id
            or result.row_version != command.expected_item_row_version + 1
        ):
            raise LifecycleIdempotencyError(
                "Stored deactivation result is not bound to this command."
            )
        return replace(result, request_id=command.request_id)

    @staticmethod
    def _validate_restore_replay(
        result: RestoreItemResult, command: RestoreItemCommand
    ) -> RestoreItemResult:
        if (
            result.operation_id != command.operation_id
            or result.item_id != command.item_id
            or result.row_version != command.expected_item_row_version + 1
        ):
            raise LifecycleIdempotencyError(
                "Stored restore result is not bound to this command."
            )
        return replace(result, request_id=command.request_id)

    def _append_deactivate_event(
        self,
        connection: ManagedConnection,
        *,
        actor: UserRecord,
        command: DeactivateItemCommand,
        dedup_id: str,
        written: DeactivateWriteResult,
        request_meta: Mapping[str, object],
        now_ms: int,
    ) -> None:
        details: dict[str, object] = {
            "edge_counts": written.edge_counts.as_dict(),
            "episode_id": written.episode_id,
            "episode_no": written.episode_no,
            "own_or_expired_lease_released": (
                written.own_or_expired_lease_released
            ),
            "purge_eligible_at": _iso_from_ms(written.purge_eligible_at_ms),
            "reference_counts": written.reference_counts.as_dict(),
            "removed_search_document_count": written.removed_search_document_count,
            "revision_id": written.revision_id,
            "schema_version": 1,
            "space_generation": written.space_generation,
        }
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(_AUDIT_NAMESPACE, dedup_id, "deactivate"),
                occurred_at_ms=now_ms,
                request_id=command.request_id,
                operation_id=command.operation_id,
                action="knowledge_item.deactivate",
                outcome="deactivated",
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=command.space_id,
                item_id=command.item_id,
                base_revision_id=written.revision_id,
                new_revision_id=written.revision_id,
                reason=command.reason.strip(),
                details=details,
                request_meta=request_meta,
            ),
        )
        self._hit("lifecycle.after_audit")
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"knowledge-item:deactivated:{dedup_id}",
                space_id=command.space_id,
                aggregate_type="knowledge_item",
                aggregate_id=command.item_id,
                aggregate_version=written.item_row_version,
                event_type="knowledge_item.deactivated",
                payload={"item_id": command.item_id, **details},
                available_at_ms=now_ms,
            ),
        )
        self._hit("lifecycle.after_outbox")

    def _append_restore_event(
        self,
        connection: ManagedConnection,
        *,
        actor: UserRecord,
        command: RestoreItemCommand,
        dedup_id: str,
        written: RestoreWriteResult,
        request_meta: Mapping[str, object],
        now_ms: int,
    ) -> None:
        details: dict[str, object] = {
            "episode_id": written.episode_id,
            "episode_no": written.episode_no,
            "inactive_duration_seconds": written.inactive_duration_seconds,
            "restored_search_document_count": (
                written.restored_search_document_count
            ),
            "review_job_queued": written.review_job_queued,
            "revision_id": written.revision_id,
            "schema_version": 1,
            "space_generation": written.space_generation,
        }
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(_AUDIT_NAMESPACE, dedup_id, "restore"),
                occurred_at_ms=now_ms,
                request_id=command.request_id,
                operation_id=command.operation_id,
                action="knowledge_item.restore",
                outcome="restored",
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=command.space_id,
                item_id=command.item_id,
                base_revision_id=written.revision_id,
                new_revision_id=written.revision_id,
                reason=None,
                details=details,
                request_meta=request_meta,
            ),
        )
        self._hit("lifecycle.after_audit")
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"knowledge-item:restored:{dedup_id}",
                space_id=command.space_id,
                aggregate_type="knowledge_item",
                aggregate_id=command.item_id,
                aggregate_version=written.item_row_version,
                event_type="knowledge_item.restored",
                payload={"item_id": command.item_id, **details},
                available_at_ms=now_ms,
            ),
        )
        self._hit("lifecycle.after_outbox")

    def deactivate(self, command: DeactivateItemCommand) -> DeactivateItemResult:
        if not isinstance(command, DeactivateItemCommand):
            raise TypeError("deactivate requires a DeactivateItemCommand.")
        self._validate_common(
            principal=command.principal,
            space_id=command.space_id,
            item_id=command.item_id,
            expected_item_row_version=command.expected_item_row_version,
            operation_id=command.operation_id,
            request_id=command.request_id,
        )
        reason = _reason(command.reason)
        safe_meta = _safe_request_meta(command.request_meta)
        operation_type = "knowledge_item.deactivate"
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE, command.principal.user_id, operation_type, command.operation_id
        )
        episode_id = _derived_uuid(_EPISODE_NAMESPACE, command.item_id, command.operation_id)
        request_hash = self._request_hash(
            operation_type=operation_type,
            principal=command.principal,
            space_id=command.space_id,
            item_id=command.item_id,
            expected_row_version=command.expected_item_row_version,
            reason=reason,
        )
        result: DeactivateItemResult | None = None
        final_error: LifecycleServiceError | None = None
        connection = self._factory.open()
        try:
            with transaction(connection):
                actor = self._resolve(
                    connection,
                    principal=command.principal,
                    space_id=command.space_id,
                    capability=Capability.DEACTIVATE,
                )
                now_ms = db_now_ms(connection)
                begun = self._begin(
                    connection,
                    actor=actor,
                    operation_type=operation_type,
                    operation_id=command.operation_id,
                    request_hash=request_hash,
                    dedup_id=dedup_id,
                    now_ms=now_ms,
                )
                if not begun.inserted:
                    if begun.operation.status == "committed":
                        stored = begun.operation.result
                        if stored is None:
                            raise LifecycleIdempotencyError(
                                "Committed deactivation has no result."
                            )
                        result = self._validate_deactivate_replay(
                            DeactivateItemResult.from_mapping(stored), command
                        )
                    elif begun.operation.status == "failed_final":
                        stored = begun.operation.error
                        if stored is None:
                            raise LifecycleIdempotencyError(
                                "Failed deactivation has no stored error."
                            )
                        final_error = self._replay_error(stored)
                    else:
                        raise LifecycleOperationInProgressError(
                            "The deactivation is already in progress."
                        )
                else:
                    try:
                        written = self._lifecycle.deactivate(
                            connection,
                            DeactivateDraft(
                                item_id=command.item_id,
                                space_id=command.space_id,
                                actor_user_id=actor.id,
                                episode_id=episode_id,
                                expected_item_row_version=(
                                    command.expected_item_row_version
                                ),
                                reason=reason,
                                now_ms=now_ms,
                            ),
                            failpoint=self._hit,
                        )
                    except LeaseHeldRepositoryError as exc:
                        final_error = self._held_error(exc)
                    except LifecycleItemNotFoundError as exc:
                        final_error = LifecycleNotFoundError(str(exc))
                    except LifecycleStateConflictError as exc:
                        final_error = self._state_error(exc)
                    if final_error is not None:
                        self._fail_final(
                            connection,
                            dedup_id=dedup_id,
                            error=final_error,
                            now_ms=now_ms,
                        )
                    else:
                        self._semantic_graph.mark_target_references_unavailable(
                            connection,
                            target_item_id=command.item_id,
                            reason="target_inactive",
                            now_ms=now_ms,
                        )
                        self._hit("lifecycle.after_reference_availability")
                        self._append_deactivate_event(
                            connection,
                            actor=actor,
                            command=replace(command, reason=reason),
                            dedup_id=dedup_id,
                            written=written,
                            request_meta=safe_meta,
                            now_ms=now_ms,
                        )
                        result = DeactivateItemResult(
                            request_id=command.request_id,
                            operation_id=command.operation_id,
                            item_id=command.item_id,
                            row_version=written.item_row_version,
                            deactivated_at=_iso_from_ms(written.deactivated_at_ms),
                            deactivated_by=actor.display_name,
                            purge_eligible_at=_iso_from_ms(
                                written.purge_eligible_at_ms
                            ),
                            edge_counts=written.edge_counts.as_dict(),
                            reference_counts=written.reference_counts.as_dict(),
                        )
                        self._commit(
                            connection,
                            dedup_id=dedup_id,
                            result=result.as_dict(),
                            now_ms=now_ms,
                        )
                        self._hit("lifecycle.before_commit")
        except (LifecycleIntegrityError, SemanticGraphContractRepositoryError) as exc:
            raise LifecycleServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()
        if final_error is not None:
            raise final_error
        if result is None:
            raise LifecycleServiceIntegrityError(
                "The deactivation produced no terminal result."
            )
        return result

    def restore(self, command: RestoreItemCommand) -> RestoreItemResult:
        if not isinstance(command, RestoreItemCommand):
            raise TypeError("restore requires a RestoreItemCommand.")
        self._validate_common(
            principal=command.principal,
            space_id=command.space_id,
            item_id=command.item_id,
            expected_item_row_version=command.expected_item_row_version,
            operation_id=command.operation_id,
            request_id=command.request_id,
        )
        safe_meta = _safe_request_meta(command.request_meta)
        operation_type = "knowledge_item.restore"
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE, command.principal.user_id, operation_type, command.operation_id
        )
        request_hash = self._request_hash(
            operation_type=operation_type,
            principal=command.principal,
            space_id=command.space_id,
            item_id=command.item_id,
            expected_row_version=command.expected_item_row_version,
            reason=None,
        )
        early = self._restore_preflight(
            command, dedup_id=dedup_id, request_hash=request_hash
        )
        if isinstance(early, RestoreItemResult):
            return early
        if isinstance(early, LifecycleServiceError):
            raise early
        evidence = early
        result: RestoreItemResult | None = None
        final_error: LifecycleServiceError | None = None
        connection = self._factory.open()
        try:
            with transaction(connection):
                actor = self._resolve(
                    connection,
                    principal=command.principal,
                    space_id=command.space_id,
                    capability=Capability.RESTORE,
                )
                now_ms = db_now_ms(connection)
                begun = self._begin(
                    connection,
                    actor=actor,
                    operation_type=operation_type,
                    operation_id=command.operation_id,
                    request_hash=request_hash,
                    dedup_id=dedup_id,
                    now_ms=now_ms,
                )
                if not begun.inserted:
                    if begun.operation.status == "committed":
                        stored = begun.operation.result
                        if stored is None:
                            raise LifecycleIdempotencyError(
                                "Committed restore has no result."
                            )
                        result = self._validate_restore_replay(
                            RestoreItemResult.from_mapping(stored), command
                        )
                    elif begun.operation.status == "failed_final":
                        stored = begun.operation.error
                        if stored is None:
                            raise LifecycleIdempotencyError(
                                "Failed restore has no stored error."
                            )
                        final_error = self._replay_error(stored)
                    else:
                        raise LifecycleOperationInProgressError(
                            "The restore is already in progress."
                        )
                else:
                    try:
                        written = self._lifecycle.restore(
                            connection,
                            RestoreDraft(
                                item_id=command.item_id,
                                space_id=command.space_id,
                                actor_user_id=actor.id,
                                expected_item_row_version=(
                                    command.expected_item_row_version
                                ),
                                now_ms=now_ms,
                                search_evidence=evidence,
                            ),
                            failpoint=self._hit,
                        )
                    except LifecycleItemNotFoundError as exc:
                        final_error = LifecycleNotFoundError(str(exc))
                    except LifecycleStateConflictError as exc:
                        final_error = self._state_error(exc)
                    if final_error is not None:
                        self._fail_final(
                            connection,
                            dedup_id=dedup_id,
                            error=final_error,
                            now_ms=now_ms,
                        )
                    else:
                        self._append_restore_event(
                            connection,
                            actor=actor,
                            command=command,
                            dedup_id=dedup_id,
                            written=written,
                            request_meta=safe_meta,
                            now_ms=now_ms,
                        )
                        result = RestoreItemResult(
                            request_id=command.request_id,
                            operation_id=command.operation_id,
                            item_id=command.item_id,
                            revision_id=written.revision_id,
                            row_version=written.item_row_version,
                            inactive_duration_seconds=(
                                written.inactive_duration_seconds
                            ),
                            review_job_queued=written.review_job_queued,
                        )
                        self._commit(
                            connection,
                            dedup_id=dedup_id,
                            result=result.as_dict(),
                            now_ms=now_ms,
                        )
                        self._hit("lifecycle.before_commit")
        except LifecycleIntegrityError as exc:
            raise LifecycleServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()
        if final_error is not None:
            raise final_error
        if result is None:
            raise LifecycleServiceIntegrityError(
                "The restore produced no terminal result."
            )
        return result

    def _restore_preflight(
        self,
        command: RestoreItemCommand,
        *,
        dedup_id: str,
        request_hash: str,
    ) -> RestoreItemResult | LifecycleServiceError | RestoreSearchEvidence | None:
        requirement = None
        connection = self._factory.open()
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._resolve(
                    connection,
                    principal=command.principal,
                    space_id=command.space_id,
                    capability=Capability.RESTORE,
                )
                existing = self._users.get_operation(connection, dedup_id)
                if existing is not None:
                    if existing.request_sha256 != request_hash:
                        raise LifecycleIdempotencyError(
                            "The operation ID is already bound to another restore."
                        )
                    if existing.status == "committed":
                        stored = existing.result
                        if stored is None:
                            raise LifecycleIdempotencyError(
                                "Committed restore has no result."
                            )
                        return self._validate_restore_replay(
                            RestoreItemResult.from_mapping(stored), command
                        )
                    if existing.status == "failed_final":
                        stored = existing.error
                        if stored is None:
                            raise LifecycleIdempotencyError(
                                "Failed restore has no stored error."
                            )
                        return self._replay_error(stored)
                    raise LifecycleOperationInProgressError(
                        "The restore is already in progress."
                    )
                try:
                    requirement = self._lifecycle.restore_search_requirement(
                        connection,
                        item_id=command.item_id,
                        space_id=command.space_id,
                        expected_item_row_version=command.expected_item_row_version,
                    )
                except LifecycleItemNotFoundError:
                    requirement = None
        except LifecycleIntegrityError as exc:
            raise LifecycleServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()
        if requirement is None:
            return None
        if requirement.content_md is not None:
            return verify_restore_search_content(requirement, requirement.content_md)
        if self._content_loader is None or requirement.content_blob_hash is None:
            raise LifecycleServiceIntegrityError(
                "Blob-backed restore requires an external verified content loader."
            )
        try:
            payload = self._content_loader(
                requirement.content_blob_hash, requirement.content_size_bytes
            )
        except Exception as exc:
            raise LifecycleServiceIntegrityError(
                "The restore content loader failed."
            ) from exc
        try:
            return verify_restore_search_content(requirement, payload)
        except LifecycleIntegrityError as exc:
            raise LifecycleServiceIntegrityError(str(exc)) from exc


__all__ = [
    "DeactivateItemCommand",
    "DeactivateItemResult",
    "ItemLifecycleService",
    "LifecycleAuthenticationError",
    "LifecycleAuthorizationError",
    "LifecycleFailpointTriggered",
    "LifecycleIdempotencyError",
    "LifecycleLeaseHeldError",
    "LifecycleNotFoundError",
    "LifecycleOperationInProgressError",
    "LifecycleScopeStaleError",
    "LifecycleServiceError",
    "LifecycleServiceIntegrityError",
    "LifecycleValidationError",
    "RestoreItemCommand",
    "RestoreItemResult",
]
