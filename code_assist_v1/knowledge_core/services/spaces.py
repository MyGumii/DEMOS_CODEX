"""Atomic phase-1 personal/part knowledge-space provisioning.

This service is intentionally framework-neutral and unregistered.  It creates
only missing personal spaces and spaces owned by currently active parts.  It
does not resolve request scope, expose a space to a user, enable knowledge
traffic, enable writes, create higher-level spaces, or touch projections.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
import re
from typing import Literal
from uuid import UUID, uuid5

from ..db import ConnectionFactory, ManagedConnection, db_now_ms, transaction
from ..errors import KnowledgeCoreError
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.organization import OrganizationRepository
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.spaces import (
    EnsureSpaceResult,
    SpaceIdentityConflictError,
    SpaceOwnerNotFoundError,
    SpaceOwnerStateError,
    SpaceProvisioningReport,
    SpaceRecord,
    SpaceRepository,
    SpaceRepositoryError,
)
from ..repositories.users import (
    OperationIdempotencyConflictError,
    OperationInProgressError,
    UserNotFoundError,
    UserRecord,
    UserRepository,
)


_SPACE_NAMESPACE = UUID("7bcce88a-c11c-4f64-b2af-dc9a9c7dfe44")
_DEDUP_NAMESPACE = UUID("67c8ea56-f96a-442e-91d5-917e76697992")
_AUDIT_NAMESPACE = UUID("68a8f4a3-355f-48fc-b2fc-0b9005fa3316")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
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


class SpaceProvisioningServiceError(KnowledgeCoreError):
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


class SpaceProvisioningAuthorizationError(SpaceProvisioningServiceError):
    code = "FORBIDDEN"
    http_status = 403


class SpaceProvisioningScopeStaleError(SpaceProvisioningServiceError):
    code = "SCOPE_STALE"


class SpaceProvisioningIdempotencyError(SpaceProvisioningServiceError):
    code = "IDEMPOTENCY_CONFLICT"


class SpaceProvisioningInProgressError(SpaceProvisioningServiceError):
    retryable = True

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> None:
        merged = {"state": "operation_in_progress"}
        merged.update(dict(details or {}))
        super().__init__(message, details=merged)


class SpaceProvisioningInvariantError(SpaceProvisioningServiceError):
    code = "STATE_CONFLICT"


class SpaceProvisioningFailpointTriggered(SpaceProvisioningServiceError):
    code = "SPACE_PROVISIONING_FAILPOINT_TRIGGERED"
    http_status = 500
    retryable = True


OwnerKind = Literal["personal", "part"]
ProvisionOutcome = Literal["created", "existing"]


@dataclass(frozen=True, slots=True)
class ProvisionPhase1SpacesCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    reason: str
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SpaceOwnerOutcome:
    owner_kind: OwnerKind
    owner_id: str
    space_id: str
    outcome: ProvisionOutcome

    def as_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "owner_id": self.owner_id,
            "owner_kind": self.owner_kind,
            "space_id": self.space_id,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "SpaceOwnerOutcome":
        owner_kind = value.get("owner_kind")
        outcome = value.get("outcome")
        owner_id = value.get("owner_id")
        space_id = value.get("space_id")
        if owner_kind not in ("personal", "part"):
            raise ValueError("stored owner kind is invalid")
        if outcome not in ("created", "existing"):
            raise ValueError("stored provisioning outcome is invalid")
        if owner_kind == "personal":
            owner_id = _valid_user_id(owner_id, field_name="owner_id")
        else:
            owner_id = _canonical_uuid(owner_id, field_name="owner_id")
        _canonical_uuid(space_id, field_name="space_id")
        return cls(
            owner_kind=owner_kind,
            owner_id=owner_id,
            space_id=space_id,
            outcome=outcome,
        )


@dataclass(frozen=True, slots=True)
class Phase1SpaceProvisioningResult:
    request_id: str
    operation_id: str
    status: Literal["created", "no_change"]
    created_personal_count: int
    existing_personal_count: int
    created_part_count: int
    existing_part_count: int
    total_user_count: int
    personal_space_count: int
    active_part_count: int
    active_part_space_count: int
    total_part_space_count: int
    higher_space_count: int
    outcomes: tuple[SpaceOwnerOutcome, ...]

    @property
    def created_count(self) -> int:
        return self.created_personal_count + self.created_part_count

    def as_dict(self) -> dict[str, object]:
        return {
            "active_part_count": self.active_part_count,
            "active_part_space_count": self.active_part_space_count,
            "created_part_count": self.created_part_count,
            "created_personal_count": self.created_personal_count,
            "existing_part_count": self.existing_part_count,
            "existing_personal_count": self.existing_personal_count,
            "higher_space_count": self.higher_space_count,
            "operation_id": self.operation_id,
            "outcomes": [value.as_dict() for value in self.outcomes],
            "personal_space_count": self.personal_space_count,
            "request_id": self.request_id,
            "status": self.status,
            "total_part_space_count": self.total_part_space_count,
            "total_user_count": self.total_user_count,
        }

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, object]
    ) -> "Phase1SpaceProvisioningResult":
        try:
            raw_outcomes = value["outcomes"]
            if not isinstance(raw_outcomes, list):
                raise ValueError("outcomes must be a list")
            outcomes = tuple(
                SpaceOwnerOutcome.from_mapping(item)
                for item in raw_outcomes
                if isinstance(item, Mapping)
            )
            if len(outcomes) != len(raw_outcomes):
                raise ValueError("outcome entry is invalid")
            status = value["status"]
            if status not in ("created", "no_change"):
                raise ValueError("status is invalid")
            result = cls(
                request_id=_canonical_uuid(
                    value["request_id"], field_name="request_id"
                ),
                operation_id=_canonical_uuid(
                    value["operation_id"], field_name="operation_id"
                ),
                status=status,
                created_personal_count=_nonnegative_int(
                    value["created_personal_count"], "created_personal_count"
                ),
                existing_personal_count=_nonnegative_int(
                    value["existing_personal_count"], "existing_personal_count"
                ),
                created_part_count=_nonnegative_int(
                    value["created_part_count"], "created_part_count"
                ),
                existing_part_count=_nonnegative_int(
                    value["existing_part_count"], "existing_part_count"
                ),
                total_user_count=_nonnegative_int(
                    value["total_user_count"], "total_user_count"
                ),
                personal_space_count=_nonnegative_int(
                    value["personal_space_count"], "personal_space_count"
                ),
                active_part_count=_nonnegative_int(
                    value["active_part_count"], "active_part_count"
                ),
                active_part_space_count=_nonnegative_int(
                    value["active_part_space_count"], "active_part_space_count"
                ),
                total_part_space_count=_nonnegative_int(
                    value["total_part_space_count"], "total_part_space_count"
                ),
                higher_space_count=_nonnegative_int(
                    value["higher_space_count"], "higher_space_count"
                ),
                outcomes=outcomes,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SpaceProvisioningIdempotencyError(
                "The stored space-provisioning result is invalid."
            ) from exc
        if result.status == "created" and result.created_count == 0:
            raise SpaceProvisioningIdempotencyError(
                "The stored space-provisioning result is inconsistent."
            )
        if result.status == "no_change" and result.created_count != 0:
            raise SpaceProvisioningIdempotencyError(
                "The stored space-provisioning result is inconsistent."
            )
        personal = tuple(
            outcome for outcome in result.outcomes if outcome.owner_kind == "personal"
        )
        parts = tuple(
            outcome for outcome in result.outcomes if outcome.owner_kind == "part"
        )
        expected_order = (
            *sorted(personal, key=lambda outcome: outcome.owner_id),
            *sorted(parts, key=lambda outcome: outcome.owner_id),
        )
        if result.outcomes != expected_order:
            raise SpaceProvisioningIdempotencyError(
                "The stored provisioning owner order is invalid."
            )
        if (
            len({(outcome.owner_kind, outcome.owner_id) for outcome in result.outcomes})
            != len(result.outcomes)
            or len({outcome.space_id for outcome in result.outcomes})
            != len(result.outcomes)
            or len(personal) != result.total_user_count
            or len(parts) != result.active_part_count
            or sum(outcome.outcome == "created" for outcome in personal)
            != result.created_personal_count
            or sum(outcome.outcome == "existing" for outcome in personal)
            != result.existing_personal_count
            or sum(outcome.outcome == "created" for outcome in parts)
            != result.created_part_count
            or sum(outcome.outcome == "existing" for outcome in parts)
            != result.existing_part_count
            or result.personal_space_count != result.total_user_count
            or result.active_part_space_count != result.active_part_count
        ):
            raise SpaceProvisioningIdempotencyError(
                "The stored provisioning counts are inconsistent."
            )
        return result


def _canonical_uuid(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a canonical lowercase UUID.")
    return value


def _valid_user_id(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _USER_ID_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a canonical user ID.")
    return value


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer.")
    return value


def _nonnegative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer.")
    return value


def _canonical_json(value: Mapping[str, object]) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Space provisioning metadata must be finite JSON.") from exc
    if not isinstance(json.loads(encoded), dict):
        raise ValueError("Space provisioning metadata must be a JSON object.")
    return encoded


def _safe_request_meta(value: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip().lower()
        if any(fragment in key for fragment in _SENSITIVE_META_FRAGMENTS):
            raise ValueError("Sensitive request metadata cannot enter audit storage.")
        if key not in _REQUEST_META_ALLOWLIST:
            continue
        if raw_value is None:
            safe[key] = None
        else:
            text = str(raw_value)
            if len(text) > 500:
                raise ValueError("Audit request metadata values are too long.")
            safe[key] = text
    _canonical_json(safe)
    return safe


def _derived_uuid(namespace: UUID, *parts: str) -> str:
    return str(uuid5(namespace, "\x1f".join(parts)))


def space_id_for_owner(owner_kind: OwnerKind, owner_id: str) -> str:
    """Return the stable server-owned identity for one phase-1 owner."""

    if owner_kind == "personal":
        owner_id = _valid_user_id(owner_id, field_name="owner_id")
    elif owner_kind == "part":
        owner_id = _canonical_uuid(owner_id, field_name="owner_id")
    else:
        raise ValueError("owner_kind must be personal or part.")
    return _derived_uuid(_SPACE_NAMESPACE, owner_kind, owner_id)


def _semantic_hash(command: ProvisionPhase1SpacesCommand) -> str:
    return sha256(
        _canonical_json(
            {
                "actor_scope_epoch": command.actor_scope_epoch,
                "actor_user_id": command.actor_user_id,
                "operation_type": "knowledge_space.provision_phase1",
                "reason": command.reason,
                "schema_version": 1,
            }
        ).encode("ascii")
    ).hexdigest()


def _created_audit_uuid(dedup_id: str, space_id: str) -> str:
    return _derived_uuid(_AUDIT_NAMESPACE, dedup_id, "space", space_id)


class SpaceProvisioningService:
    """Administrator-only reconciliation of phase-1 owner/space lower bounds."""

    __slots__ = (
        "_audit",
        "_factory",
        "_failpoint",
        "_organization",
        "_outbox",
        "_spaces",
        "_users",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        users: UserRepository | None = None,
        organization: OrganizationRepository | None = None,
        spaces: SpaceRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("SpaceProvisioningService requires a ConnectionFactory.")
        self._factory = factory
        self._users = users or UserRepository()
        self._organization = organization or OrganizationRepository()
        self._spaces = spaces or SpaceRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()
        self._failpoint = failpoint

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    def _active_admin(
        self, connection: ManagedConnection, actor_user_id: str
    ) -> UserRecord:
        try:
            actor = self._users.require(connection, actor_user_id)
        except UserNotFoundError as exc:
            raise SpaceProvisioningAuthorizationError(
                "An active system administrator must provision spaces."
            ) from exc
        if actor.account_status != "active" or not actor.is_admin:
            raise SpaceProvisioningAuthorizationError(
                "An active system administrator must provision spaces."
            )
        return actor

    @staticmethod
    def _check_epoch(actor: UserRecord, expected: int) -> None:
        _positive_int(expected, "actor_scope_epoch")
        if actor.scope_epoch != expected:
            raise SpaceProvisioningScopeStaleError(
                "The administrator session scope is stale.",
                details={
                    "current_scope_epoch": actor.scope_epoch,
                    "expected_scope_epoch": expected,
                },
            )

    def provision_phase1_spaces(
        self, command: ProvisionPhase1SpacesCommand
    ) -> Phase1SpaceProvisioningResult:
        _valid_user_id(command.actor_user_id, field_name="actor_user_id")
        _positive_int(command.actor_scope_epoch, "actor_scope_epoch")
        _canonical_uuid(command.operation_id, field_name="operation_id")
        _canonical_uuid(command.request_id, field_name="request_id")
        if not isinstance(command.reason, str) or not 1 <= len(command.reason.strip()) <= 1000:
            raise ValueError("reason must contain 1 through 1000 characters.")
        reason = command.reason.strip()
        safe_meta = _safe_request_meta(command.request_meta)
        request_hash = _semantic_hash(replace(command, reason=reason))
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE,
            command.actor_user_id,
            "knowledge_space.provision_phase1",
            command.operation_id,
        )
        command_audit_uuid = _derived_uuid(_AUDIT_NAMESPACE, dedup_id, "command")
        result: Phase1SpaceProvisioningResult | None = None
        final_error: SpaceProvisioningServiceError | None = None

        connection = self._factory.open()
        try:
            with transaction(connection):
                actor = self._active_admin(connection, command.actor_user_id)
                now_ms = db_now_ms(connection)
                try:
                    begun = self._users.begin_operation(
                        connection,
                        actor_user_id=actor.id,
                        operation_type="knowledge_space.provision_phase1",
                        operation_id=command.operation_id,
                        request_sha256=request_hash,
                        dedup_id=dedup_id,
                        now_ms=now_ms,
                    )
                except OperationIdempotencyConflictError as exc:
                    raise SpaceProvisioningIdempotencyError(str(exc)) from exc

                if not begun.inserted:
                    if begun.operation.status == "committed":
                        stored = begun.operation.result
                        if stored is None:
                            raise SpaceProvisioningIdempotencyError(
                                "The committed provisioning operation has no result."
                            )
                        result = self._validate_replay_result(
                            connection,
                            result=Phase1SpaceProvisioningResult.from_mapping(stored),
                            operation_id=command.operation_id,
                            request_id=command.request_id,
                        )
                    elif begun.operation.status == "failed_final":
                        stored_error = begun.operation.error
                        if not isinstance(stored_error, Mapping):
                            raise SpaceProvisioningIdempotencyError(
                                "The failed provisioning operation has no error."
                            )
                        code = stored_error.get("code")
                        message = stored_error.get("message")
                        details = stored_error.get("details")
                        if code != "SCOPE_STALE" or not isinstance(message, str) or not isinstance(details, Mapping):
                            raise SpaceProvisioningIdempotencyError(
                                "The failed provisioning operation error is invalid."
                            )
                        final_error = SpaceProvisioningScopeStaleError(
                            message, details=details
                        )
                    else:
                        raise SpaceProvisioningInProgressError(
                            "The provisioning operation is already in progress."
                        )
                else:
                    try:
                        self._check_epoch(actor, command.actor_scope_epoch)
                    except SpaceProvisioningScopeStaleError as exc:
                        final_error = exc
                        self._audit.append(
                            connection,
                            AuditEventDraft(
                                event_uuid=command_audit_uuid,
                                occurred_at_ms=now_ms,
                                request_id=command.request_id,
                                operation_id=command.operation_id,
                                action="knowledge_space.provision_phase1",
                                outcome="rejected",
                                actor_kind="user",
                                actor_user_id=actor.id,
                                actor_display_name=actor.display_name,
                                actor_role_snapshot=actor.system_role,
                                reason=reason,
                                details={"error": exc.as_error(), "schema_version": 1},
                                request_meta=safe_meta,
                            ),
                        )
                        self._users.fail_operation_final(
                            connection,
                            dedup_id=dedup_id,
                            error=exc.as_error(),
                            http_status=exc.http_status,
                            completed_at_ms=now_ms,
                        )
                    else:
                        result = self._apply(
                            connection=connection,
                            command=command,
                            actor=actor,
                            dedup_id=dedup_id,
                            command_audit_uuid=command_audit_uuid,
                            now_ms=now_ms,
                            reason=reason,
                            request_meta=safe_meta,
                        )
                        try:
                            self._users.commit_operation(
                                connection,
                                dedup_id=dedup_id,
                                result=result.as_dict(),
                                http_status=200,
                                completed_at_ms=now_ms,
                            )
                        except (
                            OperationIdempotencyConflictError,
                            OperationInProgressError,
                        ) as exc:
                            raise SpaceProvisioningIdempotencyError(str(exc)) from exc
                        self._hit("space_provisioning.before_commit")
        finally:
            connection.close()
        if final_error is not None:
            raise final_error
        if result is None:  # pragma: no cover - every path above is terminal
            raise SpaceProvisioningServiceError(
                "The provisioning command produced no terminal result."
            )
        return result

    def _validate_replay_result(
        self,
        connection: ManagedConnection,
        *,
        result: Phase1SpaceProvisioningResult,
        operation_id: str,
        request_id: str,
    ) -> Phase1SpaceProvisioningResult:
        """Bind a durable result back to the canonical owner rows.

        Provisioning derives IDs only for missing owners.  A valid space that
        predates this service keeps its stable server-generated ID, so replay
        validation must compare the stored outcome with the canonical row
        instead of assuming every existing row uses this service's UUID5.
        """

        if result.operation_id != operation_id:
            raise SpaceProvisioningIdempotencyError(
                "The stored provisioning operation identity is invalid."
            )
        for outcome in result.outcomes:
            if outcome.owner_kind == "personal":
                current = self._spaces.get_personal(connection, outcome.owner_id)
            else:
                current = self._spaces.get_part(connection, outcome.owner_id)
            if current is None or current.id != outcome.space_id:
                raise SpaceProvisioningIdempotencyError(
                    "The stored provisioning owner identity is invalid."
                )
            if (
                outcome.outcome == "created"
                and outcome.space_id
                != space_id_for_owner(outcome.owner_kind, outcome.owner_id)
            ):
                raise SpaceProvisioningIdempotencyError(
                    "The stored created-space identity is invalid."
                )
        return replace(result, request_id=request_id)

    def _apply(
        self,
        *,
        connection: ManagedConnection,
        command: ProvisionPhase1SpacesCommand,
        actor: UserRecord,
        dedup_id: str,
        command_audit_uuid: str,
        now_ms: int,
        reason: str,
        request_meta: Mapping[str, object],
    ) -> Phase1SpaceProvisioningResult:
        users = tuple(sorted(self._users.list_all(connection), key=lambda user: user.id))
        parts = tuple(
            sorted(
                self._organization.list_units(
                    connection, kind="part", status="active"
                ),
                key=lambda part: part.id,
            )
        )
        ensured: list[tuple[EnsureSpaceResult, OwnerKind, str]] = []
        first_created_hit = False
        for user in users:
            value = self._spaces.ensure_personal(
                connection,
                space_id=space_id_for_owner("personal", user.id),
                user_id=user.id,
                created_at_ms=now_ms,
            )
            ensured.append((value, "personal", user.id))
            if value.created and not first_created_hit:
                first_created_hit = True
                self._hit("space_provisioning.after_first_insert")
        for part in parts:
            value = self._spaces.ensure_part(
                connection,
                space_id=space_id_for_owner("part", part.id),
                part_id=part.id,
                created_at_ms=now_ms,
            )
            ensured.append((value, "part", part.id))
            if value.created and not first_created_hit:
                first_created_hit = True
                self._hit("space_provisioning.after_first_insert")
        self._hit("space_provisioning.after_domain")

        report = self._spaces.provisioning_report(connection)
        self._require_complete_report(report, users=len(users), active_parts=len(parts))
        outcomes = tuple(
            SpaceOwnerOutcome(
                owner_kind=kind,
                owner_id=owner_id,
                space_id=value.space.id,
                outcome="created" if value.created else "existing",
            )
            for value, kind, owner_id in ensured
        )
        created = [(value.space, kind, owner_id) for value, kind, owner_id in ensured if value.created]

        for space, owner_kind, owner_id in created:
            self._append_space_events(
                connection=connection,
                command=command,
                actor=actor,
                dedup_id=dedup_id,
                now_ms=now_ms,
                reason=reason,
                request_meta=request_meta,
                space=space,
                owner_kind=owner_kind,
                owner_id=owner_id,
            )
        status: Literal["created", "no_change"] = "created" if created else "no_change"
        created_personal = sum(kind == "personal" for _, kind, _ in created)
        created_part = sum(kind == "part" for _, kind, _ in created)
        result = Phase1SpaceProvisioningResult(
            request_id=command.request_id,
            operation_id=command.operation_id,
            status=status,
            created_personal_count=created_personal,
            existing_personal_count=len(users) - created_personal,
            created_part_count=created_part,
            existing_part_count=len(parts) - created_part,
            total_user_count=report.total_user_count,
            personal_space_count=report.personal_space_count,
            active_part_count=report.active_part_count,
            active_part_space_count=report.active_part_space_count,
            total_part_space_count=report.total_part_space_count,
            higher_space_count=report.higher_space_count,
            outcomes=outcomes,
        )
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=command_audit_uuid,
                occurred_at_ms=now_ms,
                request_id=command.request_id,
                operation_id=command.operation_id,
                action="knowledge_space.provision_phase1",
                outcome=status,
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                reason=reason,
                details={
                    "active_part_count": result.active_part_count,
                    "created_part_count": result.created_part_count,
                    "created_personal_count": result.created_personal_count,
                    "schema_version": 1,
                    "total_user_count": result.total_user_count,
                },
                request_meta=request_meta,
            ),
        )
        self._hit("space_provisioning.after_audit")
        if created:
            self._hit("space_provisioning.after_outbox")
        return result

    @staticmethod
    def _require_complete_report(
        report: SpaceProvisioningReport, *, users: int, active_parts: int
    ) -> None:
        if (
            not report.complete
            or report.total_user_count != users
            or report.personal_space_count != users
            or report.active_part_count != active_parts
            or report.active_part_space_count != active_parts
        ):
            raise SpaceProvisioningInvariantError(
                "The phase-1 knowledge-space lower-bound postcondition failed.",
                details={
                    "active_part_count": report.active_part_count,
                    "active_part_space_count": report.active_part_space_count,
                    "missing_part_count": report.missing_part_count,
                    "missing_personal_count": report.missing_personal_count,
                    "personal_space_count": report.personal_space_count,
                    "total_user_count": report.total_user_count,
                },
            )

    def _append_space_events(
        self,
        *,
        connection: ManagedConnection,
        command: ProvisionPhase1SpacesCommand,
        actor: UserRecord,
        dedup_id: str,
        now_ms: int,
        reason: str,
        request_meta: Mapping[str, object],
        space: SpaceRecord,
        owner_kind: OwnerKind,
        owner_id: str,
    ) -> None:
        details = {
            "generation": space.generation,
            "owner_id": owner_id,
            "owner_kind": owner_kind,
            "row_version": space.row_version,
            "schema_version": 1,
            "status": space.status,
            "write_enabled": space.write_enabled,
        }
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_created_audit_uuid(dedup_id, space.id),
                occurred_at_ms=now_ms,
                request_id=command.request_id,
                operation_id=command.operation_id,
                action="knowledge_space.provisioned",
                outcome="created",
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=space.id,
                reason=reason,
                details=details,
                request_meta=request_meta,
            ),
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"knowledge-space:{space.id}:provisioned:v1",
                space_id=space.id,
                aggregate_type="knowledge_space",
                aggregate_id=space.id,
                aggregate_version=space.row_version,
                event_type="knowledge_space.provisioned",
                payload={
                    "operation_id": command.operation_id,
                    "owner_id": owner_id,
                    "owner_kind": owner_kind,
                    "schema_version": 1,
                    "space_id": space.id,
                },
                available_at_ms=now_ms,
            ),
        )


__all__ = [
    "Phase1SpaceProvisioningResult",
    "ProvisionPhase1SpacesCommand",
    "SpaceOwnerOutcome",
    "SpaceProvisioningAuthorizationError",
    "SpaceProvisioningFailpointTriggered",
    "SpaceProvisioningIdempotencyError",
    "SpaceProvisioningInProgressError",
    "SpaceProvisioningInvariantError",
    "SpaceProvisioningScopeStaleError",
    "SpaceProvisioningService",
    "SpaceProvisioningServiceError",
    "space_id_for_owner",
]
