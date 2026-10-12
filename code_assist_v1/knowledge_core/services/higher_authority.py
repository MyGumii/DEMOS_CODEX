"""SBE-2.3 higher-role and downward-distribution authority service.

The service owns administrator role choreography, exact role-episode fencing,
bounded distribution management, operation idempotency, audit, and outbox.
It delegates selected-view/native capability decisions to the canonical
``ScopeResolver`` and never accepts a client-computed closure or role claim.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from hashlib import sha256
import hmac
import json
import re
from typing import Literal
from uuid import UUID, uuid5

from ..db import ConnectionFactory, ManagedConnection, TransactionMode, db_now_ms, transaction
from ..errors import KnowledgeCoreError
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.affiliations import (
    OrgAffiliationRepository,
    OrgAffiliationStateConflictError,
)
from ..repositories.higher_spaces import (
    DISTRIBUTION_HISTORY_MAX_PAGE_SIZE,
    DistributionGrantNotFoundError,
    DistributionGrantStateConflictError,
    DistributionTargetKind,
    HigherRoleNotFoundError,
    HigherRoleStateConflictError,
    HigherScopeRole,
    HigherScopeRoleAssignmentRecord,
    HigherSpaceRepository,
    KnowledgeDistributionGrantRecord,
)
from ..repositories.organization import OrganizationRepository
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.spaces import SpaceRepository, SpaceRepositoryError, SpaceRecord
from ..repositories.users import (
    OperationIdempotencyConflictError,
    OperationInProgressError,
    UserNotFoundError,
    UserRecord,
    UserRepository,
)
from ..scope import Capability, ScopeResolutionError, ScopeResolver, SessionPrincipal


_DEDUP_NAMESPACE = UUID("01b8a8ec-01b6-40dd-833e-53aa30cff9db")
_ROLE_NAMESPACE = UUID("5339e66b-22c2-48eb-82bd-5fe395e7cb8e")
_GRANT_NAMESPACE = UUID("5feadf1d-ed74-414f-8827-a21763ad4d59")
_AUDIT_NAMESPACE = UUID("2e946699-28fb-4972-9e27-07b3395b1675")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_CURSOR_MAX_LENGTH = 4096
_CURSOR_VERSION = 1
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


class HigherAuthorityServiceError(KnowledgeCoreError):
    code = "STATE_CONFLICT"
    http_status = 409
    retryable = False

    def __init__(
        self, message: str, *, details: Mapping[str, object] | None = None
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


class HigherAuthorityAuthenticationError(HigherAuthorityServiceError):
    code = "AUTHENTICATION_REQUIRED"
    http_status = 401


class HigherAuthorityScopeStaleError(HigherAuthorityServiceError):
    code = "SCOPE_STALE"


class HigherAuthorityForbiddenError(HigherAuthorityServiceError):
    code = "FORBIDDEN"
    http_status = 403


class HigherAuthorityNativeWriteDisabledError(HigherAuthorityForbiddenError):
    """The requested native write level is not enabled by rollout policy."""

    code = "NATIVE_WRITE_DISABLED"


class HigherAuthorityConcealedError(HigherAuthorityServiceError):
    code = "NOT_FOUND"
    http_status = 404


class HigherAuthorityVersionError(HigherAuthorityServiceError):
    code = "REVISION_CONFLICT"


class HigherAuthorityRoleEpisodeStaleError(HigherAuthorityVersionError):
    code = "ROLE_EPISODE_STALE"


class HigherAuthorityRoleConflictError(HigherAuthorityServiceError):
    code = "STATE_CONFLICT"


class HigherAuthorityDistributionConflictError(HigherAuthorityServiceError):
    code = "DISTRIBUTION_STATE_CONFLICT"


class HigherAuthorityIdempotencyError(HigherAuthorityServiceError):
    code = "IDEMPOTENCY_CONFLICT"


class HigherAuthorityOperationInProgressError(HigherAuthorityServiceError):
    retryable = True


class HigherAuthorityCursorError(HigherAuthorityServiceError):
    code = "VALIDATION_ERROR"
    http_status = 400


class HigherAuthorityFailpointTriggered(HigherAuthorityServiceError):
    code = "HIGHER_AUTHORITY_FAILPOINT_TRIGGERED"
    http_status = 500
    retryable = True


@dataclass(frozen=True, slots=True)
class SetHigherRoleCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    org_unit_id: str
    role: HigherScopeRole
    assignee_user_id: str | None
    expected_current_assignment_id: str | None
    expected_current_assignment_generation: int | None
    expected_current_assignment_row_version: int | None
    expected_space_row_version: int
    enable_writes: bool
    reason: str
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HigherRoleMutationResult:
    request_id: str
    operation_id: str
    status: Literal["assigned", "replaced", "updated", "ended"]
    org_unit_id: str
    space_id: str
    assignment_id: str | None
    assignment_generation: int | None
    assignment_row_version: int | None
    space_row_version: int
    space_generation: int
    write_enabled: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "assignment_generation": self.assignment_generation,
            "assignment_id": self.assignment_id,
            "assignment_row_version": self.assignment_row_version,
            "operation_id": self.operation_id,
            "org_unit_id": self.org_unit_id,
            "request_id": self.request_id,
            "space_generation": self.space_generation,
            "space_id": self.space_id,
            "space_row_version": self.space_row_version,
            "status": self.status,
            "write_enabled": self.write_enabled,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "HigherRoleMutationResult":
        status = value.get("status")
        if status not in {"assigned", "replaced", "updated", "ended"}:
            raise ValueError("stored higher-role status is invalid")
        assignment_id = value.get("assignment_id")
        assignment_generation = value.get("assignment_generation")
        assignment_row_version = value.get("assignment_row_version")
        if assignment_id is None:
            if assignment_generation is not None or assignment_row_version is not None:
                raise ValueError("stored closed higher-role tuple is invalid")
        else:
            assignment_id = _uuid(assignment_id, field="assignment_id")
            assignment_generation = _positive(
                assignment_generation, field="assignment_generation"
            )
            assignment_row_version = _positive(
                assignment_row_version, field="assignment_row_version"
            )
        write_enabled = value.get("write_enabled")
        if not isinstance(write_enabled, bool):
            raise ValueError("stored write_enabled is invalid")
        return cls(
            request_id=_uuid(value.get("request_id"), field="request_id"),
            operation_id=_uuid(value.get("operation_id"), field="operation_id"),
            status=status,  # type: ignore[arg-type]
            org_unit_id=_uuid(value.get("org_unit_id"), field="org_unit_id"),
            space_id=_uuid(value.get("space_id"), field="space_id"),
            assignment_id=assignment_id,
            assignment_generation=assignment_generation,  # type: ignore[arg-type]
            assignment_row_version=assignment_row_version,  # type: ignore[arg-type]
            space_row_version=_positive(
                value.get("space_row_version"), field="space_row_version"
            ),
            space_generation=_non_negative(
                value.get("space_generation"), field="space_generation"
            ),
            write_enabled=write_enabled,
        )


@dataclass(frozen=True, slots=True)
class ListDistributionsQuery:
    actor_user_id: str
    actor_scope_epoch: int
    request_id: str
    source_space_id: str
    cursor: str | None = None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class DistributionPageResult:
    request_id: str
    items: tuple[KnowledgeDistributionGrantRecord, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ReadHigherRoleQuery:
    actor_user_id: str
    actor_scope_epoch: int
    request_id: str
    org_unit_id: str
    role: HigherScopeRole


@dataclass(frozen=True, slots=True)
class HigherRoleStatusResult:
    request_id: str
    org_unit_id: str
    role: HigherScopeRole
    space_id: str
    space_row_version: int
    space_generation: int
    write_enabled: bool
    assignment_id: str | None
    assignment_generation: int | None
    assignment_row_version: int | None
    assignee_user_id: str | None
    assignee_display_name: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "assignee_display_name": self.assignee_display_name,
            "assignee_user_id": self.assignee_user_id,
            "assignment_generation": self.assignment_generation,
            "assignment_id": self.assignment_id,
            "assignment_row_version": self.assignment_row_version,
            "org_unit_id": self.org_unit_id,
            "request_id": self.request_id,
            "role": self.role,
            "space_generation": self.space_generation,
            "space_id": self.space_id,
            "space_row_version": self.space_row_version,
            "write_enabled": self.write_enabled,
        }


@dataclass(frozen=True, slots=True)
class ListDistributionTargetsQuery:
    actor_user_id: str
    actor_scope_epoch: int
    request_id: str
    source_space_id: str


@dataclass(frozen=True, slots=True)
class DistributionTargetOption:
    org_unit_id: str
    kind: str
    name: str
    org_path: tuple[str, ...]
    target_scope_kind: Literal["org_unit"] = "org_unit"

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "name": self.name,
            "org_path": list(self.org_path),
            "org_unit_id": self.org_unit_id,
            "target_scope_id": self.org_unit_id,
            "target_scope_kind": self.target_scope_kind,
        }


@dataclass(frozen=True, slots=True)
class DistributionTargetListResult:
    request_id: str
    source_space_id: str
    items: tuple[DistributionTargetOption, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "items": [item.as_dict() for item in self.items],
            "request_id": self.request_id,
            "source_space_id": self.source_space_id,
        }


@dataclass(frozen=True, slots=True)
class CreateDistributionCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    source_space_id: str
    source_item_id: str
    source_revision_id: str
    target_org_unit_id: str
    target_scope_kind: DistributionTargetKind
    target_scope_id: str
    reason: str
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RevokeDistributionCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    source_space_id: str
    grant_id: str
    expected_row_version: int
    reason: str
    break_glass: bool = False
    confirmation: str | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DistributionMutationResult:
    request_id: str
    operation_id: str
    status: Literal["created", "revoked"]
    grant_id: str
    source_space_id: str
    row_version: int
    revoked: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "grant_id": self.grant_id,
            "operation_id": self.operation_id,
            "request_id": self.request_id,
            "revoked": self.revoked,
            "row_version": self.row_version,
            "source_space_id": self.source_space_id,
            "status": self.status,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "DistributionMutationResult":
        status = value.get("status")
        if status not in {"created", "revoked"}:
            raise ValueError("stored distribution status is invalid")
        revoked = value.get("revoked")
        if not isinstance(revoked, bool) or revoked != (status == "revoked"):
            raise ValueError("stored distribution lifecycle is invalid")
        return cls(
            request_id=_uuid(value.get("request_id"), field="request_id"),
            operation_id=_uuid(value.get("operation_id"), field="operation_id"),
            status=status,  # type: ignore[arg-type]
            grant_id=_uuid(value.get("grant_id"), field="grant_id"),
            source_space_id=_uuid(
                value.get("source_space_id"), field="source_space_id"
            ),
            row_version=_positive(value.get("row_version"), field="row_version"),
            revoked=revoked,
        )


def _uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _user(value: object, *, field: str = "actor_user_id") -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical user ID.")
    return value


def _positive(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _non_negative(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer.")
    return value


def _reason(value: object) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > 1000
    ):
        raise ValueError("reason must be bounded nonblank trimmed text.")
    return value


def _canonical_json(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if not isinstance(json.loads(encoded), dict):
        raise ValueError("metadata must encode an object")
    return encoded


def _safe_request_meta(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("request_meta must be a mapping.")
    result: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip().lower()
        if any(fragment in key for fragment in _SENSITIVE_META_FRAGMENTS):
            raise ValueError("Sensitive request metadata cannot enter audit storage.")
        if key not in _REQUEST_META_ALLOWLIST:
            continue
        if raw_value is not None and len(str(raw_value)) > 500:
            raise ValueError("Audit request metadata values are too long.")
        result[key] = None if raw_value is None else str(raw_value)
    _canonical_json(result)
    return result


def _derived_uuid(namespace: UUID, *parts: str) -> str:
    return str(uuid5(namespace, "\x1f".join(parts)))


def _semantic_hash(value: Mapping[str, object]) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class HigherAuthorityService:
    """Transaction-owning SBE-2.3 role and distribution boundary."""

    __slots__ = (
        "_audit",
        "_affiliations",
        "_cursor_secret",
        "_factory",
        "_failpoint",
        "_higher",
        "_organizations",
        "_outbox",
        "_scope",
        "_spaces",
        "_users",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        scope_resolver: ScopeResolver,
        *,
        cursor_secret: bytes,
        users: UserRepository | None = None,
        organizations: OrganizationRepository | None = None,
        spaces: SpaceRepository | None = None,
        higher: HigherSpaceRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        affiliations: OrgAffiliationRepository | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("HigherAuthorityService requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("HigherAuthorityService requires a ScopeResolver.")
        if not isinstance(cursor_secret, bytes) or len(cursor_secret) < 32:
            raise ValueError("cursor_secret must contain at least 32 bytes.")
        if failpoint is not None and not callable(failpoint):
            raise TypeError("failpoint must be callable or None.")
        self._factory = factory
        self._scope = scope_resolver
        self._cursor_secret = bytes(cursor_secret)
        self._users = users or UserRepository()
        self._affiliations = affiliations or OrgAffiliationRepository()
        self._organizations = organizations or OrganizationRepository()
        self._spaces = spaces or SpaceRepository()
        self._higher = higher or HigherSpaceRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()
        self._failpoint = failpoint

    def _hit(self, name: str) -> None:
        if self._failpoint is None:
            return
        try:
            self._failpoint(name)
        except HigherAuthorityFailpointTriggered:
            raise
        except Exception as exc:
            raise HigherAuthorityFailpointTriggered(
                f"Higher-authority failpoint triggered at {name}."
            ) from exc

    def _active_actor(
        self,
        connection: ManagedConnection,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
    ) -> UserRecord:
        actor_user_id = _user(actor_user_id)
        actor_scope_epoch = _positive(
            actor_scope_epoch, field="actor_scope_epoch"
        )
        try:
            actor = self._users.require(connection, actor_user_id)
        except UserNotFoundError as exc:
            raise HigherAuthorityAuthenticationError(
                "A valid active account session is required."
            ) from exc
        if actor.account_status != "active":
            raise HigherAuthorityAuthenticationError(
                "A valid active account session is required."
            )
        if actor.scope_epoch != actor_scope_epoch:
            raise HigherAuthorityScopeStaleError(
                "The account session scope is stale.",
                details={
                    "current_scope_epoch": actor.scope_epoch,
                    "expected_scope_epoch": actor_scope_epoch,
                },
            )
        return actor

    def _active_admin(
        self,
        connection: ManagedConnection,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
    ) -> UserRecord:
        actor = self._active_actor(
            connection,
            actor_user_id=actor_user_id,
            actor_scope_epoch=actor_scope_epoch,
        )
        if not actor.is_admin:
            raise HigherAuthorityForbiddenError(
                "Administrator authority is required for role management."
            )
        return actor

    @staticmethod
    def _expected_current_role(
        command: SetHigherRoleCommand,
        current: HigherScopeRoleAssignmentRecord | None,
    ) -> None:
        expected_id = command.expected_current_assignment_id
        expected_generation = command.expected_current_assignment_generation
        expected_version = command.expected_current_assignment_row_version
        if current is None:
            if any(
                value is not None
                for value in (expected_id, expected_generation, expected_version)
            ):
                raise HigherAuthorityRoleEpisodeStaleError(
                    "The expected higher-role episode no longer exists."
                )
            return
        if (
            expected_id is None
            or expected_generation is None
            or expected_version is None
        ):
            raise HigherAuthorityRoleEpisodeStaleError(
                "The current higher-role episode requires an exact fence."
            )
        if (
            _uuid(expected_id, field="expected_current_assignment_id") != current.id
            or _positive(
                expected_generation,
                field="expected_current_assignment_generation",
            )
            != current.assignment_generation
            or _positive(
                expected_version,
                field="expected_current_assignment_row_version",
            )
            != current.row_version
        ):
            raise HigherAuthorityRoleEpisodeStaleError(
                "The higher-role episode changed before the command."
            )

    def _set_space_write_state(
        self,
        connection: ManagedConnection,
        *,
        space: SpaceRecord,
        expected_row_version: int,
        enabled: bool,
        now_ms: int,
    ) -> SpaceRecord:
        try:
            return self._spaces.update_state(
                connection,
                space_id=space.id,
                expected_row_version=expected_row_version,
                status="active",
                write_enabled=enabled,
                updated_at_ms=now_ms,
            )
        except SpaceRepositoryError as exc:
            raise HigherAuthorityRoleEpisodeStaleError(
                "The higher native-space state changed before the command."
            ) from exc

    def _require_direct_assignee(
        self,
        connection: ManagedConnection,
        *,
        user_id: str,
    ) -> UserRecord:
        try:
            user = self._users.require(connection, user_id)
        except UserNotFoundError as exc:
            raise HigherAuthorityRoleConflictError(
                "The requested higher-role assignee does not exist."
            ) from exc
        if user.account_status != "active":
            raise HigherAuthorityRoleConflictError(
                "The requested higher-role assignee is not active."
            )
        if user.current_part_id is not None:
            raise HigherAuthorityRoleConflictError(
                "Unassign the user from their Part before assigning a direct Team or Responsibility role."
            )
        if self._affiliations.get_current(connection, user.id) is not None:
            raise HigherAuthorityRoleConflictError(
                "The requested assignee already has a direct organization affiliation."
            )
        return user

    def _open_direct_affiliation(
        self,
        connection: ManagedConnection,
        *,
        assignment_id: str,
        assignee: UserRecord,
        actor: UserRecord,
        org_unit_id: str,
        role: HigherScopeRole,
        reason: str,
        now_ms: int,
    ) -> None:
        try:
            self._affiliations.open_direct(
                connection,
                affiliation_id=assignment_id,
                user_id=assignee.id,
                org_unit_id=org_unit_id,
                home_kind=("team" if role == "team_leader" else "responsibility"),
                assigned_by_user_id=actor.id,
                reason=reason,
                valid_from_ms=now_ms,
            )
            self._affiliations.bump_direct_scope(
                connection,
                user_id=assignee.id,
                expected_scope_epoch=assignee.scope_epoch,
                expected_row_version=assignee.row_version,
                updated_at_ms=max(now_ms, assignee.updated_at_ms),
            )
        except OrgAffiliationStateConflictError as exc:
            raise HigherAuthorityRoleConflictError(
                "The direct organization affiliation changed before assignment."
            ) from exc

    def _close_direct_affiliation(
        self,
        connection: ManagedConnection,
        *,
        assignment: HigherScopeRoleAssignmentRecord,
        closed_at_ms: int,
    ) -> None:
        user = self._users.require(connection, assignment.user_id)
        affiliation = self._affiliations.get_current(connection, assignment.user_id)
        if (
            affiliation is None
            or affiliation.id != assignment.id
            or affiliation.org_unit_id != assignment.org_unit_id
            or affiliation.home_kind
            != ("team" if assignment.role == "team_leader" else "responsibility")
        ):
            raise HigherAuthorityRoleConflictError(
                "The direct role and organization affiliation disagree."
            )
        try:
            self._affiliations.close_direct(
                connection,
                affiliation_id=affiliation.id,
                user_id=user.id,
                closed_at_ms=max(closed_at_ms, affiliation.valid_from_ms + 1),
            )
            self._affiliations.bump_direct_scope(
                connection,
                user_id=user.id,
                expected_scope_epoch=user.scope_epoch,
                expected_row_version=user.row_version,
                updated_at_ms=max(closed_at_ms, user.updated_at_ms),
            )
        except OrgAffiliationStateConflictError as exc:
            raise HigherAuthorityRoleConflictError(
                "The direct organization affiliation changed before role closure."
            ) from exc

    def set_role(self, command: SetHigherRoleCommand) -> HigherRoleMutationResult:
        if not isinstance(command, SetHigherRoleCommand):
            raise TypeError("command must be SetHigherRoleCommand.")
        actor_id = _user(command.actor_user_id)
        epoch = _positive(command.actor_scope_epoch, field="actor_scope_epoch")
        operation_id = _uuid(command.operation_id, field="operation_id")
        request_id = _uuid(command.request_id, field="request_id")
        org_unit_id = _uuid(command.org_unit_id, field="org_unit_id")
        if command.role not in ("team_leader", "responsibility_executive"):
            raise ValueError("role is invalid.")
        role = command.role
        assignee = (
            None
            if command.assignee_user_id is None
            else _user(command.assignee_user_id, field="assignee_user_id")
        )
        expected_space_version = _positive(
            command.expected_space_row_version,
            field="expected_space_row_version",
        )
        if not isinstance(command.enable_writes, bool):
            raise ValueError("enable_writes must be a Boolean.")
        if assignee is None and command.enable_writes:
            raise ValueError("An ended role cannot leave native writes enabled.")
        native_write_flag = (
            self._scope.flags.team_writes
            if role == "team_leader"
            else self._scope.flags.responsibility_writes
        )
        if command.enable_writes and not native_write_flag:
            raise HigherAuthorityNativeWriteDisabledError(
                "The requested higher native write level is disabled by rollout policy.",
                details={"role": role},
            )
        reason = _reason(command.reason)
        request_meta = _safe_request_meta(command.request_meta)
        operation_type = f"higher_role.{role}.set"
        request_payload = {
            "assignee_user_id": assignee,
            "enable_writes": command.enable_writes,
            "expected_current_assignment_generation": (
                command.expected_current_assignment_generation
            ),
            "expected_current_assignment_id": command.expected_current_assignment_id,
            "expected_current_assignment_row_version": (
                command.expected_current_assignment_row_version
            ),
            "expected_space_row_version": expected_space_version,
            "org_unit_id": org_unit_id,
            "reason": reason,
            "role": role,
        }
        request_hash = _semantic_hash(
            {
                "actor_scope_epoch": epoch,
                "actor_user_id": actor_id,
                "operation_type": operation_type,
                "request": request_payload,
                "schema_version": 1,
            }
        )
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE, actor_id, operation_type, operation_id
        )
        assignment_id = _derived_uuid(
            _ROLE_NAMESPACE, org_unit_id, role, operation_id
        )
        result: HigherRoleMutationResult | None = None
        connection = self._factory.open()
        try:
            with transaction(connection):
                actor = self._active_admin(
                    connection,
                    actor_user_id=actor_id,
                    actor_scope_epoch=epoch,
                )
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
                    raise HigherAuthorityIdempotencyError(str(exc)) from exc
                if not begun.inserted:
                    if begun.operation.status != "committed":
                        raise HigherAuthorityOperationInProgressError(
                            "The higher-role operation is already in progress."
                        )
                    stored = begun.operation.result
                    if not isinstance(stored, Mapping):
                        raise HigherAuthorityIdempotencyError(
                            "The committed higher-role result is invalid."
                        )
                    try:
                        replay = HigherRoleMutationResult.from_mapping(stored)
                    except ValueError as exc:
                        raise HigherAuthorityIdempotencyError(
                            "The committed higher-role result is invalid."
                        ) from exc
                    result = replace(replay, request_id=request_id)
                else:
                    unit = self._organizations.get_unit(connection, org_unit_id)
                    expected_kind = (
                        "team" if role == "team_leader" else "responsibility"
                    )
                    if (
                        unit is None
                        or unit.kind != expected_kind
                        or unit.status != "active"
                    ):
                        raise HigherAuthorityConcealedError(
                            "The requested organization unit was not found."
                        )
                    space = self._spaces.get_org(connection, org_unit_id)
                    if (
                        space is None
                        or space.space_kind != expected_kind
                        or space.status != "active"
                    ):
                        raise HigherAuthorityRoleConflictError(
                            "The organization unit has no exact active native space."
                        )
                    if space.row_version != expected_space_version:
                        raise HigherAuthorityRoleEpisodeStaleError(
                            "The higher native-space state changed before the command."
                        )
                    current = self._higher.get_open_role(
                        connection, org_unit_id=org_unit_id, role=role
                    )
                    self._expected_current_role(command, current)
                    new_assignee = None
                    if assignee is not None and (
                        current is None or current.user_id != assignee
                    ):
                        new_assignee = self._require_direct_assignee(
                            connection, user_id=assignee
                        )
                    old_role_id = None if current is None else current.id
                    old_generation = (
                        None if current is None else current.assignment_generation
                    )
                    invalidation = None
                    try:
                        if assignee is None:
                            if current is None:
                                raise HigherAuthorityRoleConflictError(
                                    "No open higher-role episode can be ended."
                                )
                            if space.write_enabled:
                                space = self._set_space_write_state(
                                    connection,
                                    space=space,
                                    expected_row_version=space.row_version,
                                    enabled=False,
                                    now_ms=now_ms,
                                )
                            invalidation = self._higher.invalidate_native_authority(
                                connection,
                                space_id=space.id,
                                role_assignment_id=current.id,
                                updated_at_ms=now_ms,
                            )
                            closed_at = max(now_ms, current.assigned_at_ms + 1)
                            self._higher.close_role(
                                connection,
                                assignment_id=current.id,
                                expected_generation=current.assignment_generation,
                                expected_row_version=current.row_version,
                                ended_at_ms=closed_at,
                                end_reason=reason,
                            )
                            self._close_direct_affiliation(
                                connection,
                                assignment=current,
                                closed_at_ms=closed_at,
                            )
                            active_role = None
                            status: Literal[
                                "assigned", "replaced", "updated", "ended"
                            ] = "ended"
                        elif current is not None and current.user_id == assignee:
                            disabling = space.write_enabled and not command.enable_writes
                            space = self._set_space_write_state(
                                connection,
                                space=space,
                                expected_row_version=space.row_version,
                                enabled=command.enable_writes,
                                now_ms=now_ms,
                            )
                            if disabling:
                                invalidation = self._higher.invalidate_native_authority(
                                    connection,
                                    space_id=space.id,
                                    role_assignment_id=current.id,
                                    updated_at_ms=now_ms,
                                )
                            active_role = current
                            status = "updated"
                        else:
                            replacing = current is not None
                            if current is not None:
                                if space.write_enabled:
                                    space = self._set_space_write_state(
                                        connection,
                                        space=space,
                                        expected_row_version=space.row_version,
                                        enabled=False,
                                        now_ms=now_ms,
                                    )
                                invalidation = self._higher.invalidate_native_authority(
                                    connection,
                                    space_id=space.id,
                                    role_assignment_id=current.id,
                                    updated_at_ms=now_ms,
                                )
                                closed_at = max(now_ms, current.assigned_at_ms + 1)
                                self._higher.close_role(
                                    connection,
                                    assignment_id=current.id,
                                    expected_generation=current.assignment_generation,
                                    expected_row_version=current.row_version,
                                    ended_at_ms=closed_at,
                                    end_reason=reason,
                                )
                                self._close_direct_affiliation(
                                    connection,
                                    assignment=current,
                                    closed_at_ms=closed_at,
                                )
                            generation = self._higher.next_assignment_generation(
                                connection, org_unit_id=org_unit_id, role=role
                            )
                            if new_assignee is None:
                                raise HigherAuthorityRoleConflictError(
                                    "The direct higher-role assignee is unavailable."
                                )
                            self._open_direct_affiliation(
                                connection,
                                assignment_id=assignment_id,
                                assignee=new_assignee,
                                actor=actor,
                                org_unit_id=org_unit_id,
                                role=role,
                                reason=reason,
                                now_ms=now_ms,
                            )
                            active_role = self._higher.insert_open_role(
                                connection,
                                assignment_id=assignment_id,
                                org_unit_id=org_unit_id,
                                user_id=assignee,
                                role=role,
                                assigned_by_user_id=actor.id,
                                assignment_generation=generation,
                                assigned_at_ms=now_ms,
                            )
                            space = self._set_space_write_state(
                                connection,
                                space=space,
                                expected_row_version=space.row_version,
                                enabled=command.enable_writes,
                                now_ms=now_ms,
                            )
                            status = "replaced" if replacing else "assigned"
                    except (HigherRoleNotFoundError, HigherRoleStateConflictError) as exc:
                        raise HigherAuthorityRoleConflictError(str(exc)) from exc

                    self._hit("higher_role.after_domain")
                    details = {
                        "new_assignment_generation": (
                            None
                            if active_role is None
                            else active_role.assignment_generation
                        ),
                        "new_assignment_id": (
                            None if active_role is None else active_role.id
                        ),
                        "old_assignment_generation": old_generation,
                        "old_assignment_id": old_role_id,
                        "cancel_requested_job_count": (
                            0
                            if invalidation is None
                            else invalidation.cancel_requested_job_count
                        ),
                        "invalidated_job_count": (
                            0
                            if invalidation is None
                            else invalidation.invalidated_job_count
                        ),
                        "org_unit_id": org_unit_id,
                        "role": role,
                        "schema_version": 1,
                        "space_generation": space.generation,
                        "space_row_version": space.row_version,
                        "revoked_lease_count": (
                            0
                            if invalidation is None
                            else invalidation.revoked_lease_count
                        ),
                        "write_enabled": space.write_enabled,
                    }
                    audit = self._audit.append(
                        connection,
                        AuditEventDraft(
                            event_uuid=_derived_uuid(
                                _AUDIT_NAMESPACE, dedup_id, "role"
                            ),
                            occurred_at_ms=now_ms,
                            request_id=request_id,
                            operation_id=operation_id,
                            action=operation_type,
                            outcome=status,
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
                    self._hit("higher_role.after_audit")
                    self._outbox.append(
                        connection,
                        OutboxEventDraft(
                            logical_key=f"higher-role:{dedup_id}:v1",
                            space_id=space.id,
                            aggregate_type="higher_scope_role",
                            aggregate_id=org_unit_id,
                            aggregate_version=(
                                max(1, space.generation)
                                if active_role is None
                                else active_role.assignment_generation
                            ),
                            event_type="higher_role.changed",
                            payload={
                                **details,
                                "audit_event_id": audit.id,
                                "operation_id": operation_id,
                                "status": status,
                            },
                            available_at_ms=now_ms,
                        ),
                    )
                    self._hit("higher_role.after_outbox")
                    result = HigherRoleMutationResult(
                        request_id=request_id,
                        operation_id=operation_id,
                        status=status,
                        org_unit_id=org_unit_id,
                        space_id=space.id,
                        assignment_id=(
                            None if active_role is None else active_role.id
                        ),
                        assignment_generation=(
                            None
                            if active_role is None
                            else active_role.assignment_generation
                        ),
                        assignment_row_version=(
                            None if active_role is None else active_role.row_version
                        ),
                        space_row_version=space.row_version,
                        space_generation=space.generation,
                        write_enabled=space.write_enabled,
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
                        raise HigherAuthorityIdempotencyError(str(exc)) from exc
                    self._hit("higher_role.before_commit")
        finally:
            connection.close()
        if result is None:
            raise HigherAuthorityServiceError(
                "The higher-role operation produced no terminal result."
            )
        return result

    def _cursor_signature(self, raw: bytes) -> str:
        return hmac.new(self._cursor_secret, raw, sha256).hexdigest()

    def _encode_distribution_cursor(
        self,
        *,
        actor_user_id: str,
        source_space_id: str,
        last: KnowledgeDistributionGrantRecord,
    ) -> str:
        payload = {
            "actor_user_id": actor_user_id,
            "created_at_ms": last.created_at_ms,
            "grant_id": last.id,
            "row_version": last.row_version,
            "source_space_id": source_space_id,
            "version": _CURSOR_VERSION,
        }
        raw = _canonical_json(payload).encode("utf-8")
        body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        return f"{body}.{self._cursor_signature(raw)}"

    def _decode_distribution_cursor(
        self,
        value: str,
        *,
        actor_user_id: str,
        source_space_id: str,
    ) -> dict[str, object]:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > _CURSOR_MAX_LENGTH
            or value.count(".") != 1
        ):
            raise HigherAuthorityCursorError("The distribution cursor is invalid.")
        body, signature = value.split(".", 1)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", body) or not re.fullmatch(
            r"[0-9a-f]{64}", signature
        ):
            raise HigherAuthorityCursorError("The distribution cursor is invalid.")
        try:
            raw = base64.b64decode(
                body + "=" * (-len(body) % 4), altchars=b"-_", validate=True
            )
            decoded = json.loads(raw.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise HigherAuthorityCursorError(
                "The distribution cursor is invalid."
            ) from exc
        if not hmac.compare_digest(signature, self._cursor_signature(raw)):
            raise HigherAuthorityCursorError("The distribution cursor is invalid.")
        expected_keys = {
            "actor_user_id",
            "created_at_ms",
            "grant_id",
            "row_version",
            "source_space_id",
            "version",
        }
        if (
            not isinstance(decoded, dict)
            or set(decoded) != expected_keys
            or _canonical_json(decoded).encode("utf-8") != raw
            or decoded.get("version") != _CURSOR_VERSION
            or decoded.get("actor_user_id") != actor_user_id
            or decoded.get("source_space_id") != source_space_id
        ):
            raise HigherAuthorityCursorError(
                "The distribution cursor does not match this actor or source."
            )
        try:
            _uuid(decoded.get("grant_id"), field="cursor.grant_id")
            _non_negative(
                decoded.get("created_at_ms"), field="cursor.created_at_ms"
            )
            _positive(decoded.get("row_version"), field="cursor.row_version")
        except ValueError as exc:
            raise HigherAuthorityCursorError(
                "The distribution cursor is invalid."
            ) from exc
        return decoded

    def _distribution_manager(
        self,
        connection: ManagedConnection,
        *,
        actor: UserRecord,
        source_space_id: str,
    ) -> tuple[SpaceRecord, HigherScopeRoleAssignmentRecord | None]:
        source = self._spaces.get_by_id(connection, source_space_id)
        if (
            source is None
            or source.status != "active"
            or source.space_kind not in {"team", "responsibility"}
            or source.owner_org_unit_id is None
        ):
            raise HigherAuthorityConcealedError(
                "The requested higher native space was not found."
            )
        role: HigherScopeRole = (
            "team_leader"
            if source.space_kind == "team"
            else "responsibility_executive"
        )
        current = self._higher.get_open_role(
            connection, org_unit_id=source.owner_org_unit_id, role=role
        )
        if actor.is_admin:
            return source, current
        try:
            self._scope.resolve_in_transaction(
                connection,
                SessionPrincipal(
                    user_id=actor.id,
                    captured_scope_epoch=actor.scope_epoch,
                ),
                source.id,
                Capability.READ,
            )
        except ScopeResolutionError as exc:
            raise HigherAuthorityConcealedError(
                "The requested higher native space was not found."
            ) from exc
        if current is None or current.user_id != actor.id:
            raise HigherAuthorityForbiddenError(
                "The current source-native role episode is required."
            )
        return source, current

    def read_role(self, query: ReadHigherRoleQuery) -> HigherRoleStatusResult:
        if not isinstance(query, ReadHigherRoleQuery):
            raise TypeError("query must be ReadHigherRoleQuery.")
        actor_id = _user(query.actor_user_id)
        epoch = _positive(query.actor_scope_epoch, field="actor_scope_epoch")
        request_id = _uuid(query.request_id, field="request_id")
        org_unit_id = _uuid(query.org_unit_id, field="org_unit_id")
        role = query.role
        if role not in {"team_leader", "responsibility_executive"}:
            raise ValueError("role is invalid.")
        expected_kind = "team" if role == "team_leader" else "responsibility"
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._active_admin(
                    connection,
                    actor_user_id=actor_id,
                    actor_scope_epoch=epoch,
                )
                unit = self._organizations.get_unit(connection, org_unit_id)
                space = self._spaces.get_org(connection, org_unit_id)
                if (
                    unit is None
                    or unit.kind != expected_kind
                    or unit.status != "active"
                    or space is None
                    or space.space_kind != expected_kind
                    or space.status != "active"
                ):
                    raise HigherAuthorityConcealedError(
                        "The requested organization unit was not found."
                    )
                assignment = self._higher.get_open_role(
                    connection,
                    org_unit_id=org_unit_id,
                    role=role,
                )
                assignee = (
                    None
                    if assignment is None
                    else self._users.require(connection, assignment.user_id)
                )
                return HigherRoleStatusResult(
                    request_id=request_id,
                    org_unit_id=org_unit_id,
                    role=role,
                    space_id=space.id,
                    space_row_version=space.row_version,
                    space_generation=space.generation,
                    write_enabled=space.write_enabled,
                    assignment_id=None if assignment is None else assignment.id,
                    assignment_generation=(
                        None
                        if assignment is None
                        else assignment.assignment_generation
                    ),
                    assignment_row_version=(
                        None if assignment is None else assignment.row_version
                    ),
                    assignee_user_id=(
                        None if assignment is None else assignment.user_id
                    ),
                    assignee_display_name=(
                        None if assignee is None else assignee.display_name
                    ),
                )
        finally:
            connection.close()

    def list_distribution_targets(
        self, query: ListDistributionTargetsQuery
    ) -> DistributionTargetListResult:
        if not isinstance(query, ListDistributionTargetsQuery):
            raise TypeError("query must be ListDistributionTargetsQuery.")
        actor_id = _user(query.actor_user_id)
        epoch = _positive(query.actor_scope_epoch, field="actor_scope_epoch")
        request_id = _uuid(query.request_id, field="request_id")
        source_space_id = _uuid(query.source_space_id, field="source_space_id")
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                actor = self._active_actor(
                    connection,
                    actor_user_id=actor_id,
                    actor_scope_epoch=epoch,
                )
                source, _role = self._scope_for_distribution_mutation(
                    connection,
                    actor=actor,
                    source_space_id=source_space_id,
                    capability=Capability.DISTRIBUTION_GRANT,
                )
                if source.owner_org_unit_id is None:
                    raise HigherAuthorityServiceError(
                        "The higher native space has no organization owner."
                    )
                subtree = self._organizations.list_subtree(
                    connection,
                    root_id=source.owner_org_unit_id,
                    include_inactive=False,
                )
                descendants = tuple(row for row in subtree if row.depth > 0)
                if len(descendants) > 4096:
                    raise HigherAuthorityServiceError(
                        "The distribution target list exceeds the bounded closure."
                    )
                items = tuple(
                    DistributionTargetOption(
                        org_unit_id=row.unit.id,
                        kind=row.unit.kind,
                        name=row.unit.name,
                        org_path=row.path_names,
                    )
                    for row in descendants
                    if row.unit.kind in {"team", "part"}
                )
                return DistributionTargetListResult(
                    request_id=request_id,
                    source_space_id=source_space_id,
                    items=items,
                )
        finally:
            connection.close()

    def list_distributions(
        self, query: ListDistributionsQuery
    ) -> DistributionPageResult:
        if not isinstance(query, ListDistributionsQuery):
            raise TypeError("query must be ListDistributionsQuery.")
        actor_id = _user(query.actor_user_id)
        epoch = _positive(query.actor_scope_epoch, field="actor_scope_epoch")
        request_id = _uuid(query.request_id, field="request_id")
        source_space_id = _uuid(query.source_space_id, field="source_space_id")
        if (
            isinstance(query.limit, bool)
            or not isinstance(query.limit, int)
            or not 1 <= query.limit <= DISTRIBUTION_HISTORY_MAX_PAGE_SIZE
        ):
            raise ValueError("limit must be between 1 and 100.")
        cursor = None
        if query.cursor is not None:
            cursor = self._decode_distribution_cursor(
                query.cursor,
                actor_user_id=actor_id,
                source_space_id=source_space_id,
            )
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                actor = self._active_actor(
                    connection,
                    actor_user_id=actor_id,
                    actor_scope_epoch=epoch,
                )
                self._distribution_manager(
                    connection, actor=actor, source_space_id=source_space_id
                )
                if cursor is not None:
                    anchor = self._higher.get_distribution(
                        connection, str(cursor["grant_id"])
                    )
                    if (
                        anchor is None
                        or anchor.source_space_id != source_space_id
                        or anchor.created_at_ms != cursor["created_at_ms"]
                        or anchor.row_version != cursor["row_version"]
                    ):
                        raise HigherAuthorityCursorError(
                            "The distribution cursor anchor is stale."
                        )
                rows = self._higher.list_distributions(
                    connection,
                    source_space_id=source_space_id,
                    limit=query.limit + 1,
                    before_created_at_ms=(
                        None if cursor is None else int(cursor["created_at_ms"])
                    ),
                    before_id=(
                        None if cursor is None else str(cursor["grant_id"])
                    ),
                )
                has_more = len(rows) > query.limit
                items = rows[: query.limit]
                next_cursor = (
                    self._encode_distribution_cursor(
                        actor_user_id=actor_id,
                        source_space_id=source_space_id,
                        last=items[-1],
                    )
                    if has_more and items
                    else None
                )
        finally:
            connection.close()
        return DistributionPageResult(
            request_id=request_id, items=items, next_cursor=next_cursor
        )

    def _scope_for_distribution_mutation(
        self,
        connection: ManagedConnection,
        *,
        actor: UserRecord,
        source_space_id: str,
        capability: Capability,
    ) -> tuple[SpaceRecord, HigherScopeRoleAssignmentRecord]:
        try:
            resolved = self._scope.resolve_in_transaction(
                connection,
                SessionPrincipal(
                    user_id=actor.id,
                    captured_scope_epoch=actor.scope_epoch,
                ),
                source_space_id,
                capability,
            )
        except ScopeResolutionError as exc:
            if getattr(exc, "code", None) == "NOT_FOUND":
                raise HigherAuthorityConcealedError(
                    "The requested higher native space was not found."
                ) from exc
            raise HigherAuthorityForbiddenError(
                "Current native authority rejects the distribution mutation."
            ) from exc
        plan = resolved.access_plan
        if (
            plan is None
            or plan.native_mutation_space_id != source_space_id
            or plan.role_assignment_id is None
            or plan.role_assignment_generation is None
        ):
            raise HigherAuthorityForbiddenError(
                "The exact source-native role episode is required."
            )
        source = self._spaces.require_by_id(connection, source_space_id)
        role = self._higher.get_role_assignment(
            connection, plan.role_assignment_id
        )
        if (
            role is None
            or role.ended_at_ms is not None
            or role.user_id != actor.id
            or role.assignment_generation != plan.role_assignment_generation
        ):
            raise HigherAuthorityForbiddenError(
                "The source-native role episode changed before mutation."
            )
        return source, role

    def create_distribution(
        self, command: CreateDistributionCommand
    ) -> DistributionMutationResult:
        if not isinstance(command, CreateDistributionCommand):
            raise TypeError("command must be CreateDistributionCommand.")
        actor_id = _user(command.actor_user_id)
        epoch = _positive(command.actor_scope_epoch, field="actor_scope_epoch")
        operation_id = _uuid(command.operation_id, field="operation_id")
        request_id = _uuid(command.request_id, field="request_id")
        source_space_id = _uuid(command.source_space_id, field="source_space_id")
        source_item_id = _uuid(command.source_item_id, field="source_item_id")
        source_revision_id = _uuid(
            command.source_revision_id, field="source_revision_id"
        )
        target_org_unit_id = _uuid(
            command.target_org_unit_id, field="target_org_unit_id"
        )
        if command.target_scope_kind not in {"org_unit", "native_space"}:
            raise ValueError("target_scope_kind is invalid.")
        target_scope_kind = command.target_scope_kind
        target_scope_id = _uuid(command.target_scope_id, field="target_scope_id")
        reason = _reason(command.reason)
        request_meta = _safe_request_meta(command.request_meta)
        operation_type = "knowledge_distribution.create"
        request_payload = {
            "reason": reason,
            "source_item_id": source_item_id,
            "source_revision_id": source_revision_id,
            "source_space_id": source_space_id,
            "target_org_unit_id": target_org_unit_id,
            "target_scope_id": target_scope_id,
            "target_scope_kind": target_scope_kind,
        }
        request_hash = _semantic_hash(
            {
                "actor_scope_epoch": epoch,
                "actor_user_id": actor_id,
                "operation_type": operation_type,
                "request": request_payload,
                "schema_version": 1,
            }
        )
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE, actor_id, operation_type, operation_id
        )
        grant_id = _derived_uuid(
            _GRANT_NAMESPACE, source_space_id, actor_id, operation_id
        )
        effect_key = _semantic_hash(
            {
                "grant_id": grant_id,
                "operation_id": operation_id,
                "schema_version": 1,
            }
        )
        result: DistributionMutationResult | None = None
        connection = self._factory.open()
        try:
            with transaction(connection):
                actor = self._active_actor(
                    connection,
                    actor_user_id=actor_id,
                    actor_scope_epoch=epoch,
                )
                now_ms = db_now_ms(connection)
                result = self._begin_distribution_operation(
                    connection,
                    actor=actor,
                    operation_type=operation_type,
                    operation_id=operation_id,
                    request_id=request_id,
                    request_hash=request_hash,
                    dedup_id=dedup_id,
                    now_ms=now_ms,
                    expected_status="created",
                )
                if result is None:
                    source, role = self._scope_for_distribution_mutation(
                        connection,
                        actor=actor,
                        source_space_id=source_space_id,
                        capability=Capability.DISTRIBUTION_GRANT,
                    )
                    details = {
                        **request_payload,
                        "grant_id": grant_id,
                        "role_assignment_generation": role.assignment_generation,
                        "role_assignment_id": role.id,
                        "schema_version": 1,
                    }
                    audit = self._audit.append(
                        connection,
                        AuditEventDraft(
                            event_uuid=_derived_uuid(
                                _AUDIT_NAMESPACE, dedup_id, "grant"
                            ),
                            occurred_at_ms=now_ms,
                            request_id=request_id,
                            operation_id=operation_id,
                            action=operation_type,
                            outcome="created",
                            actor_kind="user",
                            actor_user_id=actor.id,
                            actor_display_name=actor.display_name,
                            actor_role_snapshot=actor.system_role,
                            space_id=source.id,
                            item_id=source_item_id,
                            new_revision_id=source_revision_id,
                            reason=reason,
                            details=details,
                            request_meta=request_meta,
                        ),
                    )
                    outbox = self._outbox.append(
                        connection,
                        OutboxEventDraft(
                            logical_key=f"distribution:{dedup_id}:create:v1",
                            space_id=source.id,
                            aggregate_type="knowledge_distribution_grant",
                            aggregate_id=grant_id,
                            aggregate_version=1,
                            event_type="knowledge_distribution.created",
                            payload={
                                **details,
                                "audit_event_id": audit.id,
                                "operation_id": operation_id,
                            },
                            available_at_ms=now_ms,
                        ),
                    )
                    self._hit("distribution_create.after_evidence")
                    try:
                        grant = self._higher.insert_distribution(
                            connection,
                            grant_id=grant_id,
                            source_space_id=source_space_id,
                            source_item_id=source_item_id,
                            source_revision_id=source_revision_id,
                            target_org_unit_id=target_org_unit_id,
                            target_scope_kind=target_scope_kind,
                            target_scope_id=target_scope_id,
                            source_role_assignment_id=role.id,
                            created_by_user_id=actor.id,
                            operation_id=operation_id,
                            effect_key=effect_key,
                            reason=reason,
                            created_audit_event_id=audit.id,
                            created_outbox_event_id=outbox.id,
                            created_at_ms=now_ms,
                        )
                    except DistributionGrantStateConflictError as exc:
                        raise HigherAuthorityDistributionConflictError(
                            "The distribution grant conflicts with current canonical state."
                        ) from exc
                    self._hit("distribution_create.after_domain")
                    result = DistributionMutationResult(
                        request_id=request_id,
                        operation_id=operation_id,
                        status="created",
                        grant_id=grant.id,
                        source_space_id=grant.source_space_id,
                        row_version=grant.row_version,
                        revoked=False,
                    )
                    self._commit_distribution_operation(
                        connection, dedup_id=dedup_id, result=result, now_ms=now_ms
                    )
                    self._hit("distribution_create.before_commit")
        finally:
            connection.close()
        if result is None:
            raise HigherAuthorityServiceError(
                "The distribution create produced no terminal result."
            )
        return result

    def _begin_distribution_operation(
        self,
        connection: ManagedConnection,
        *,
        actor: UserRecord,
        operation_type: str,
        operation_id: str,
        request_id: str,
        request_hash: str,
        dedup_id: str,
        now_ms: int,
        expected_status: Literal["created", "revoked"],
    ) -> DistributionMutationResult | None:
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
            raise HigherAuthorityIdempotencyError(str(exc)) from exc
        if begun.inserted:
            return None
        if begun.operation.status != "committed":
            raise HigherAuthorityOperationInProgressError(
                "The distribution operation is already in progress."
            )
        stored = begun.operation.result
        if not isinstance(stored, Mapping):
            raise HigherAuthorityIdempotencyError(
                "The committed distribution result is invalid."
            )
        try:
            replay = DistributionMutationResult.from_mapping(stored)
        except ValueError as exc:
            raise HigherAuthorityIdempotencyError(
                "The committed distribution result is invalid."
            ) from exc
        if replay.operation_id != operation_id or replay.status != expected_status:
            raise HigherAuthorityIdempotencyError(
                "The committed distribution identity is invalid."
            )
        return replace(replay, request_id=request_id)

    def _commit_distribution_operation(
        self,
        connection: ManagedConnection,
        *,
        dedup_id: str,
        result: DistributionMutationResult,
        now_ms: int,
    ) -> None:
        try:
            self._users.commit_operation(
                connection,
                dedup_id=dedup_id,
                result=result.as_dict(),
                http_status=201 if result.status == "created" else 200,
                completed_at_ms=now_ms,
            )
        except (OperationIdempotencyConflictError, OperationInProgressError) as exc:
            raise HigherAuthorityIdempotencyError(str(exc)) from exc

    def revoke_distribution(
        self, command: RevokeDistributionCommand
    ) -> DistributionMutationResult:
        if not isinstance(command, RevokeDistributionCommand):
            raise TypeError("command must be RevokeDistributionCommand.")
        actor_id = _user(command.actor_user_id)
        epoch = _positive(command.actor_scope_epoch, field="actor_scope_epoch")
        operation_id = _uuid(command.operation_id, field="operation_id")
        request_id = _uuid(command.request_id, field="request_id")
        source_space_id = _uuid(command.source_space_id, field="source_space_id")
        grant_id = _uuid(command.grant_id, field="grant_id")
        expected_version = _positive(
            command.expected_row_version, field="expected_row_version"
        )
        reason = _reason(command.reason)
        if not isinstance(command.break_glass, bool):
            raise ValueError("break_glass must be a Boolean.")
        expected_confirmation = f"REVOKE DISTRIBUTION {grant_id}"
        if command.break_glass:
            if command.confirmation != expected_confirmation:
                raise HigherAuthorityForbiddenError(
                    "Break-glass revoke requires the exact confirmation phrase."
                )
            confirmation_sha = sha256(
                expected_confirmation.encode("utf-8")
            ).hexdigest()
        else:
            if command.confirmation is not None:
                raise ValueError("confirmation is valid only for break-glass revoke.")
            confirmation_sha = None
        request_meta = _safe_request_meta(command.request_meta)
        operation_type = "knowledge_distribution.revoke"
        request_payload = {
            "break_glass": command.break_glass,
            "confirmation_sha256": confirmation_sha,
            "expected_row_version": expected_version,
            "grant_id": grant_id,
            "reason": reason,
            "source_space_id": source_space_id,
        }
        request_hash = _semantic_hash(
            {
                "actor_scope_epoch": epoch,
                "actor_user_id": actor_id,
                "operation_type": operation_type,
                "request": request_payload,
                "schema_version": 1,
            }
        )
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE, actor_id, operation_type, operation_id
        )
        effect_key = _semantic_hash(
            {
                "grant_id": grant_id,
                "operation_id": operation_id,
                "revoke": True,
                "schema_version": 1,
            }
        )
        result: DistributionMutationResult | None = None
        connection = self._factory.open()
        try:
            with transaction(connection):
                actor = self._active_actor(
                    connection,
                    actor_user_id=actor_id,
                    actor_scope_epoch=epoch,
                )
                now_ms = db_now_ms(connection)
                result = self._begin_distribution_operation(
                    connection,
                    actor=actor,
                    operation_type=operation_type,
                    operation_id=operation_id,
                    request_id=request_id,
                    request_hash=request_hash,
                    dedup_id=dedup_id,
                    now_ms=now_ms,
                    expected_status="revoked",
                )
                if result is None:
                    grant = self._higher.get_distribution(connection, grant_id)
                    if grant is None or grant.source_space_id != source_space_id:
                        raise HigherAuthorityConcealedError(
                            "The requested distribution grant was not found."
                        )
                    if grant.row_version != expected_version or grant.revoked_at_ms is not None:
                        raise HigherAuthorityDistributionConflictError(
                            "The distribution grant changed before revoke."
                        )
                    role: HigherScopeRoleAssignmentRecord | None
                    mode: Literal["ordinary", "break_glass"]
                    if command.break_glass:
                        if not actor.is_admin:
                            raise HigherAuthorityForbiddenError(
                                "Administrator authority is required for break-glass revoke."
                            )
                        source, _ = self._distribution_manager(
                            connection,
                            actor=actor,
                            source_space_id=source_space_id,
                        )
                        role = None
                        mode = "break_glass"
                    else:
                        source, role = self._scope_for_distribution_mutation(
                            connection,
                            actor=actor,
                            source_space_id=source_space_id,
                            capability=Capability.DISTRIBUTION_REVOKE,
                        )
                        mode = "ordinary"
                    details = {
                        **request_payload,
                        "revoke_mode": mode,
                        "role_assignment_generation": (
                            None if role is None else role.assignment_generation
                        ),
                        "role_assignment_id": None if role is None else role.id,
                        "schema_version": 1,
                    }
                    audit = self._audit.append(
                        connection,
                        AuditEventDraft(
                            event_uuid=_derived_uuid(
                                _AUDIT_NAMESPACE, dedup_id, "revoke"
                            ),
                            occurred_at_ms=now_ms,
                            request_id=request_id,
                            operation_id=operation_id,
                            action=operation_type,
                            outcome="revoked",
                            actor_kind="user",
                            actor_user_id=actor.id,
                            actor_display_name=actor.display_name,
                            actor_role_snapshot=actor.system_role,
                            space_id=source.id,
                            item_id=grant.source_item_id,
                            base_revision_id=grant.source_revision_id,
                            reason=reason,
                            details=details,
                            request_meta=request_meta,
                        ),
                    )
                    outbox = self._outbox.append(
                        connection,
                        OutboxEventDraft(
                            logical_key=f"distribution:{dedup_id}:revoke:v1",
                            space_id=source.id,
                            aggregate_type="knowledge_distribution_grant",
                            aggregate_id=grant.id,
                            aggregate_version=expected_version + 1,
                            event_type="knowledge_distribution.revoked",
                            payload={
                                **details,
                                "audit_event_id": audit.id,
                                "operation_id": operation_id,
                            },
                            available_at_ms=now_ms,
                        ),
                    )
                    self._hit("distribution_revoke.after_evidence")
                    try:
                        revoked = self._higher.revoke_distribution(
                            connection,
                            grant_id=grant.id,
                            expected_row_version=expected_version,
                            revoked_by_user_id=actor.id,
                            revoke_mode=mode,
                            revoke_role_assignment_id=(
                                None if role is None else role.id
                            ),
                            revoke_reason=reason,
                            revoke_confirmation_sha256=confirmation_sha,
                            revoke_operation_id=operation_id,
                            revoke_effect_key=effect_key,
                            revoked_audit_event_id=audit.id,
                            revoked_outbox_event_id=outbox.id,
                            revoked_at_ms=max(now_ms, grant.created_at_ms + 1),
                        )
                    except DistributionGrantNotFoundError as exc:
                        raise HigherAuthorityConcealedError(
                            "The requested distribution grant was not found."
                        ) from exc
                    except DistributionGrantStateConflictError as exc:
                        raise HigherAuthorityDistributionConflictError(
                            "The distribution grant conflicts with current canonical state."
                        ) from exc
                    self._hit("distribution_revoke.after_domain")
                    result = DistributionMutationResult(
                        request_id=request_id,
                        operation_id=operation_id,
                        status="revoked",
                        grant_id=revoked.id,
                        source_space_id=revoked.source_space_id,
                        row_version=revoked.row_version,
                        revoked=True,
                    )
                    self._commit_distribution_operation(
                        connection, dedup_id=dedup_id, result=result, now_ms=now_ms
                    )
                    self._hit("distribution_revoke.before_commit")
        finally:
            connection.close()
        if result is None:
            raise HigherAuthorityServiceError(
                "The distribution revoke produced no terminal result."
            )
        return result


__all__ = [
    "CreateDistributionCommand",
    "DistributionMutationResult",
    "DistributionPageResult",
    "DistributionTargetListResult",
    "DistributionTargetOption",
    "HigherAuthorityAuthenticationError",
    "HigherAuthorityConcealedError",
    "HigherAuthorityCursorError",
    "HigherAuthorityDistributionConflictError",
    "HigherAuthorityFailpointTriggered",
    "HigherAuthorityForbiddenError",
    "HigherAuthorityIdempotencyError",
    "HigherAuthorityNativeWriteDisabledError",
    "HigherAuthorityOperationInProgressError",
    "HigherAuthorityRoleConflictError",
    "HigherAuthorityRoleEpisodeStaleError",
    "HigherAuthorityScopeStaleError",
    "HigherAuthorityService",
    "HigherAuthorityServiceError",
    "HigherAuthorityVersionError",
    "HigherRoleMutationResult",
    "HigherRoleStatusResult",
    "ListDistributionTargetsQuery",
    "ListDistributionsQuery",
    "ReadHigherRoleQuery",
    "RevokeDistributionCommand",
    "SetHigherRoleCommand",
]
