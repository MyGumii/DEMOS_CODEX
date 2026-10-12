"""Account lifecycle orchestration and the later organization-service boundary.

P1.4 implements only account import/disable/re-enable policy here.  SQL remains
inside repositories; this service owns authorization, idempotency, leader-safe
ordering, one transaction, audit coupling, and failpoint boundaries.  P1.5
extends the same boundary with organization administration commands.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
import hmac
import json
from typing import Any, Literal
from uuid import UUID, uuid5
import zlib

from ..db import (
    ConnectionFactory,
    ManagedConnection,
    TransactionMode,
    db_now_ms,
    transaction,
)
from ..errors import KnowledgeCoreError
from ..domain import FeatureFlags
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.affiliations import OrgAffiliationRepository
from ..repositories.higher_spaces import (
    ORG_MOVE_INVALIDATION_MAX_SIZE,
    DistributionGrantStateConflictError,
    HigherScopeRoleAssignmentRecord,
    HigherSpaceRepository,
    KnowledgeDistributionGrantRecord,
)
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.spaces import SpaceRepository, SpaceRepositoryError
from ..repositories.organization import (
    AuthorityRevocationCounts,
    LeaderAssignmentNotFoundError as OrgRepoLeaderNotFoundError,
    LeaderAssignmentRecord,
    MembershipNotFoundError as OrgRepoMembershipNotFoundError,
    MembershipRecord,
    OrgTreeRow,
    OrgUnitImpactCounts,
    OrgUnitKind,
    OrgUnitNotFoundError as OrgRepoUnitNotFoundError,
    OrgUnitRecord,
    OrgUnitStatus,
    OrgUnitVersionConflictError as OrgRepoUnitVersionConflictError,
    OrganizationRepository,
    OrganizationStateConflictError as OrgRepoStateConflictError,
    PartSpaceNotFoundError as OrgRepoPartSpaceNotFoundError,
    PartSpaceState,
    PartSpaceVersionConflictError as OrgRepoPartSpaceVersionConflictError,
    RESPONSIBILITY_ROOT_ID,
    UserOrganizationState,
    UserOrganizationNotFoundError as OrgRepoUserNotFoundError,
    UserOrganizationVersionConflictError as OrgRepoUserVersionConflictError,
    normalize_org_name,
)
from ..repositories.users import (
    ActiveLeaderAssignment,
    AccountStatusTransition,
    LegacyUserImportResult,
    LegacyUserRecord,
    OpenMembershipRecord,
    OperationIdempotencyConflictError,
    OperationInProgressError,
    PartSpaceWriteState,
    UserRecord,
    UserNotFoundError,
    UserRepository,
    UserVersionConflictError,
)


_DEDUP_NAMESPACE = UUID("83b1e3ec-d1af-49a6-ae5c-099c75bf9ca7")
_AUDIT_NAMESPACE = UUID("adc15d66-88c7-48ae-b13c-56b2e3d039e0")
_ROLE_NAMESPACE = UUID("fbff3f5c-fd2d-42ea-8f87-08e64dc482ab")
_ORG_UNIT_NAMESPACE = UUID("8f970299-193a-49a7-b3f8-9c09d97aec70")
_MEMBERSHIP_NAMESPACE = UUID("8daf8da2-4aa2-4b38-8f57-3b95bc2bbd97")
_ORG_ROLE_NAMESPACE = UUID("7e25fbd2-bb72-462f-9f70-99396f60bacf")
_ORG_PREVIEW_TTL_MS = 300_000


class AccountServiceError(KnowledgeCoreError):
    code = "ACCOUNT_COMMAND_FAILED"
    http_status = 500
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


class AccountAuthorizationError(AccountServiceError):
    code = "FORBIDDEN"
    http_status = 403


class AccountStateConflictError(AccountServiceError):
    code = "STATE_CONFLICT"
    http_status = 409


class AccountRevisionConflictError(AccountServiceError):
    code = "REVISION_CONFLICT"
    http_status = 409


class AccountNotFoundError(AccountServiceError):
    code = "NOT_FOUND"
    http_status = 404


class AccountIdempotencyError(AccountServiceError):
    code = "IDEMPOTENCY_CONFLICT"
    http_status = 409


class AccountOperationInProgressError(AccountServiceError):
    code = "STATE_CONFLICT"
    http_status = 409
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


class AccountScopeStaleError(AccountServiceError):
    code = "SCOPE_STALE"
    http_status = 409


class AccountFailpointTriggered(AccountServiceError):
    """Test-only injected failure proving transaction rollback."""

    code = "ACCOUNT_FAILPOINT_TRIGGERED"


@dataclass(frozen=True, slots=True)
class _StatusChangePlan:
    membership: OpenMembershipRecord | None
    leader: ActiveLeaderAssignment | None
    part_space: PartSpaceWriteState | None
    replacement: UserRecord | None
    part_unit: OrgUnitRecord | None
    changed_at_ms: int
    no_op: bool


@dataclass(frozen=True, slots=True)
class AccountLifecycleCommand:
    actor_user_id: str
    target_user_id: str
    expected_row_version: int
    operation_id: str
    request_id: str
    reason: str
    replacement_leader_user_id: str | None = None
    actor_scope_epoch: int | None = None
    expected_current_leader_assignment_id: str | None = None
    expected_part_row_version: int | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AccountLifecycleResult:
    operation_id: str
    target_user_id: str
    account_status: Literal["active", "disabled"]
    scope_epoch: int
    row_version: int
    changed: bool
    previous_part_id: str | None
    closed_membership: bool
    leader_action: Literal["none", "replaced", "write_disabled"]
    replacement_leader_user_id: str | None
    part_write_enabled_after: bool | None
    revoked_lease_count: int
    invalidated_job_count: int
    cancel_requested_job_count: int
    audit_event_uuid: str

    def as_dict(self) -> dict[str, object]:
        return {
            "account_status": self.account_status,
            "audit_event_uuid": self.audit_event_uuid,
            "cancel_requested_job_count": self.cancel_requested_job_count,
            "changed": self.changed,
            "closed_membership": self.closed_membership,
            "invalidated_job_count": self.invalidated_job_count,
            "leader_action": self.leader_action,
            "operation_id": self.operation_id,
            "part_write_enabled_after": self.part_write_enabled_after,
            "previous_part_id": self.previous_part_id,
            "replacement_leader_user_id": self.replacement_leader_user_id,
            "revoked_lease_count": self.revoked_lease_count,
            "row_version": self.row_version,
            "scope_epoch": self.scope_epoch,
            "target_user_id": self.target_user_id,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "AccountLifecycleResult":
        try:
            status = str(value["account_status"])
            action = str(value["leader_action"])
            if status not in {"active", "disabled"}:
                raise ValueError("invalid account status")
            if action not in {"none", "replaced", "write_disabled"}:
                raise ValueError("invalid leader action")
            return cls(
                operation_id=str(value["operation_id"]),
                target_user_id=str(value["target_user_id"]),
                account_status=status,  # type: ignore[arg-type]
                scope_epoch=int(value["scope_epoch"]),
                row_version=int(value["row_version"]),
                changed=bool(value["changed"]),
                previous_part_id=(
                    None
                    if value.get("previous_part_id") is None
                    else str(value["previous_part_id"])
                ),
                closed_membership=bool(value["closed_membership"]),
                leader_action=action,  # type: ignore[arg-type]
                replacement_leader_user_id=(
                    None
                    if value.get("replacement_leader_user_id") is None
                    else str(value["replacement_leader_user_id"])
                ),
                part_write_enabled_after=(
                    None
                    if value.get("part_write_enabled_after") is None
                    else bool(value["part_write_enabled_after"])
                ),
                revoked_lease_count=int(value["revoked_lease_count"]),
                invalidated_job_count=int(value["invalidated_job_count"]),
                cancel_requested_job_count=int(
                    value["cancel_requested_job_count"]
                ),
                audit_event_uuid=str(value["audit_event_uuid"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AccountIdempotencyError(
                "The stored account operation result is invalid."
            ) from exc


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
        raise ValueError("Account command metadata must be finite JSON.") from exc
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):  # pragma: no cover - Mapping guarantees it
        raise ValueError("Account command metadata must be a JSON object.")
    return encoded


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


def _safe_request_meta(value: Mapping[str, object]) -> dict[str, object]:
    """Persist a small forensic allowlist and reject credential-shaped keys."""

    safe: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key).strip().lower()
        if any(fragment in key for fragment in _SENSITIVE_META_FRAGMENTS):
            raise ValueError("Sensitive request metadata cannot enter audit storage.")
        if key not in _REQUEST_META_ALLOWLIST:
            continue
        if raw_value is None:
            safe[key] = None
            continue
        text = str(raw_value)
        if len(text) > 500:
            raise ValueError("Audit request metadata values are too long.")
        safe[key] = text
    _canonical_json(safe)
    return safe


def _request_sha256(
    command: AccountLifecycleCommand,
    target_status: Literal["active", "disabled"],
) -> str:
    value: dict[str, object] = {
        "actor_user_id": command.actor_user_id,
        "actor_scope_epoch": command.actor_scope_epoch,
        "expected_current_leader_assignment_id": (
            command.expected_current_leader_assignment_id
        ),
        "expected_part_row_version": command.expected_part_row_version,
        "expected_row_version": command.expected_row_version,
        "reason": command.reason,
        "replacement_leader_user_id": command.replacement_leader_user_id,
        "schema_version": 1,
        "target_status": target_status,
        "target_user_id": command.target_user_id,
    }
    return sha256(_canonical_json(value).encode("ascii")).hexdigest()


def _derived_uuid(namespace: UUID, *parts: str) -> str:
    return str(uuid5(namespace, "\x1f".join(parts)))


_FINAL_ERROR_TYPES: dict[str, type[AccountServiceError]] = {
    AccountAuthorizationError.code: AccountAuthorizationError,
    AccountNotFoundError.code: AccountNotFoundError,
    AccountRevisionConflictError.code: AccountRevisionConflictError,
    AccountStateConflictError.code: AccountStateConflictError,
    AccountScopeStaleError.code: AccountScopeStaleError,
}


def _normalize_final_error(error: BaseException) -> AccountServiceError:
    if isinstance(error, AccountServiceError):
        if error.code in _FINAL_ERROR_TYPES:
            return error
    if isinstance(error, UserVersionConflictError):
        return AccountRevisionConflictError(str(error))
    if isinstance(error, UserNotFoundError):
        return AccountNotFoundError(str(error))
    raise TypeError("Only stable non-retryable account errors may become terminal.")


def _stored_final_error(value: Mapping[str, object] | None) -> AccountServiceError:
    if not isinstance(value, Mapping):
        raise AccountIdempotencyError(
            "The failed-final account operation has no valid stored error."
        )
    code = str(value.get("code") or "")
    message = str(value.get("message") or "")
    retryable = value.get("retryable")
    details = value.get("details")
    error_type = _FINAL_ERROR_TYPES.get(code)
    if (
        error_type is None
        or not message
        or retryable is not False
        or not isinstance(details, Mapping)
    ):
        raise AccountIdempotencyError(
            "The failed-final account operation error is invalid."
        )
    return error_type(message, details=details)


class OrganizationServiceError(KnowledgeCoreError):
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


class OrganizationAuthorizationError(OrganizationServiceError):
    code = "FORBIDDEN"
    http_status = 403


class OrganizationScopeStaleError(OrganizationServiceError):
    code = "SCOPE_STALE"


class OrganizationRevisionConflictError(OrganizationServiceError):
    code = "REVISION_CONFLICT"


class OrganizationStateConflictError(OrganizationServiceError):
    code = "STATE_CONFLICT"


class OrganizationNotFoundError(OrganizationServiceError):
    code = "NOT_FOUND"
    http_status = 404


class OrganizationIdempotencyError(OrganizationServiceError):
    code = "IDEMPOTENCY_CONFLICT"


class OrganizationOperationInProgressError(OrganizationServiceError):
    code = "STATE_CONFLICT"
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


class OrganizationPreviewError(OrganizationServiceError):
    code = "STATE_CONFLICT"


class OrganizationValidationError(OrganizationServiceError):
    code = "VALIDATION_ERROR"
    http_status = 400


class OrganizationFailpointTriggered(OrganizationServiceError):
    """Test-only transient error; callers may safely retry the same operation."""

    code = "STATE_CONFLICT"
    retryable = True


@dataclass(frozen=True, slots=True)
class CreateOrgUnitCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    parent_id: str
    kind: Literal["team", "part"]
    name: str
    reason: str | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RenameOrgUnitCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    unit_id: str
    expected_row_version: int
    name: str
    reason: str | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OrgChangePreviewCommand:
    actor_user_id: str
    actor_scope_epoch: int
    request_id: str
    unit_id: str
    expected_row_version: int
    change: Literal["move", "inactivate"]
    new_parent_id: str | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OrgMoveResolution:
    retain_memberships: bool = True
    retain_leader: bool = True
    retain_space: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "retain_leader": self.retain_leader,
            "retain_memberships": self.retain_memberships,
            "retain_space": self.retain_space,
        }


@dataclass(frozen=True, slots=True)
class OrgMemberResolution:
    user_id: str
    expected_user_row_version: int
    target_part_id: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "expected_user_row_version": self.expected_user_row_version,
            "target_part_id": self.target_part_id,
            "user_id": self.user_id,
        }


@dataclass(frozen=True, slots=True)
class OrgInactivationResolution:
    member_resolutions: tuple[OrgMemberResolution, ...]
    expected_current_leader_assignment_id: str | None
    leader_action: Literal["close"] = "close"
    expected_space_row_version: int | None = None
    space_action: Literal["freeze_if_present"] = "freeze_if_present"

    def as_dict(self) -> dict[str, object]:
        return {
            "expected_current_leader_assignment_id": (
                self.expected_current_leader_assignment_id
            ),
            "expected_space_row_version": self.expected_space_row_version,
            "leader_action": self.leader_action,
            "member_resolutions": [
                value.as_dict() for value in self.member_resolutions
            ],
            "space_action": self.space_action,
        }


@dataclass(frozen=True, slots=True)
class MoveOrgUnitCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    unit_id: str
    expected_row_version: int
    preview_token: str
    new_parent_id: str
    resolution: OrgMoveResolution
    reason: str | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InactivateOrgUnitCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    unit_id: str
    expected_row_version: int
    preview_token: str
    resolution: OrgInactivationResolution
    reason: str
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SetMembershipCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    target_user_id: str
    expected_user_row_version: int
    part_id: str | None
    expected_current_leader_assignment_id: str | None
    replacement_leader_user_id: str | None
    reason: str
    expected_part_row_version: int | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SetLeaderCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    part_id: str
    leader_user_id: str
    expected_part_row_version: int
    expected_current_leader_assignment_id: str | None
    reason: str
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OrganizationMutationResult:
    request_id: str
    operation_id: str
    status: str
    resource_id: str | None
    row_version: int | None

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
    ) -> "OrganizationMutationResult":
        try:
            row_version = value.get("row_version")
            return cls(
                request_id=str(value["request_id"]),
                operation_id=str(value["operation_id"]),
                status=str(value["status"]),
                resource_id=(
                    None
                    if value.get("resource_id") is None
                    else str(value["resource_id"])
                ),
                row_version=(
                    None if row_version is None else int(row_version)
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OrganizationIdempotencyError(
                "The stored organization-operation result is invalid."
            ) from exc


def _utc_iso(utc_ms: int) -> str:
    return datetime.fromtimestamp(utc_ms / 1000, tz=UTC).isoformat().replace(
        "+00:00", "Z"
    )


def _positive_version(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise OrganizationValidationError(
            f"{field_name} must be a positive integer."
        )
    return value


@dataclass(frozen=True, slots=True)
class OrgChangePreviewResult:
    request_id: str
    unit_id: str
    allowed: bool
    blockers: tuple[Mapping[str, object], ...]
    member_impact: Mapping[str, object]
    leader_impact: Mapping[str, object]
    space_impact: Mapping[str, object]
    preview_token: str
    expires_at_ms: int
    impact_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "blockers": [dict(value) for value in self.blockers],
            "expires_at": _utc_iso(self.expires_at_ms),
            "leader_impact": dict(self.leader_impact),
            "member_impact": dict(self.member_impact),
            "preview_token": self.preview_token,
            "request_id": self.request_id,
            "space_impact": dict(self.space_impact),
            "unit_id": self.unit_id,
        }


@dataclass(frozen=True, slots=True)
class CanonicalOrgUser:
    id: str
    display_name: str
    system_role: Literal["admin", "user"]
    account_status: Literal["active", "disabled"]
    current_part_id: str | None
    current_membership_id: str | None
    current_affiliation_id: str | None
    home_org_unit_id: str | None
    home_org_unit_kind: Literal["part", "team", "responsibility"] | None
    scope_epoch: int
    row_version: int

    def as_dict(self) -> dict[str, object]:
        return {
            "account_status": self.account_status,
            "current_membership_id": self.current_membership_id,
            "current_affiliation_id": self.current_affiliation_id,
            "current_part_id": self.current_part_id,
            "display_name": self.display_name,
            "id": self.id,
            "home_org_unit_id": self.home_org_unit_id,
            "home_org_unit_kind": self.home_org_unit_kind,
            "row_version": self.row_version,
            "scope_epoch": self.scope_epoch,
            "system_role": self.system_role,
        }


@dataclass(frozen=True, slots=True)
class OrganizationTreeResult:
    request_id: str
    root: Mapping[str, object]
    users: tuple[CanonicalOrgUser, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "root": dict(self.root),
            "users": [user.as_dict() for user in self.users],
        }


_ORG_FINAL_ERROR_TYPES: dict[str, type[OrganizationServiceError]] = {
    "FORBIDDEN": OrganizationAuthorizationError,
    "SCOPE_STALE": OrganizationScopeStaleError,
    "REVISION_CONFLICT": OrganizationRevisionConflictError,
    "STATE_CONFLICT": OrganizationStateConflictError,
    "NOT_FOUND": OrganizationNotFoundError,
    "VALIDATION_ERROR": OrganizationValidationError,
}


def _stored_org_final_error(
    value: Mapping[str, object] | None,
) -> OrganizationServiceError:
    if not isinstance(value, Mapping):
        raise OrganizationIdempotencyError(
            "The failed-final organization operation has no valid stored error."
        )
    code = str(value.get("code") or "")
    message = str(value.get("message") or "")
    retryable = value.get("retryable")
    details = value.get("details")
    error_type = _ORG_FINAL_ERROR_TYPES.get(code)
    if (
        error_type is None
        or not message
        or retryable is not False
        or not isinstance(details, Mapping)
    ):
        raise OrganizationIdempotencyError(
            "The failed-final organization operation error is invalid."
        )
    return error_type(message, details=details)


def _org_reason(value: object, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 1000:
        raise ValueError("Organization reason must be 1 through 1000 characters.")
    return value.strip()


def _org_semantic_hash(
    operation_type: str,
    command: object,
) -> str:
    if isinstance(command, CreateOrgUnitCommand):
        payload: dict[str, object] = {
            "actor_scope_epoch": command.actor_scope_epoch,
            "actor_user_id": command.actor_user_id,
            "kind": command.kind,
            "name": command.name,
            "parent_id": command.parent_id,
            "reason": command.reason,
        }
    elif isinstance(command, RenameOrgUnitCommand):
        payload = {
            "actor_scope_epoch": command.actor_scope_epoch,
            "actor_user_id": command.actor_user_id,
            "expected_row_version": command.expected_row_version,
            "name": command.name,
            "reason": command.reason,
            "unit_id": command.unit_id,
        }
    elif isinstance(command, MoveOrgUnitCommand):
        payload = {
            "actor_scope_epoch": command.actor_scope_epoch,
            "actor_user_id": command.actor_user_id,
            "expected_row_version": command.expected_row_version,
            "new_parent_id": command.new_parent_id,
            "preview_token": command.preview_token,
            "reason": command.reason,
            "resolution": command.resolution.as_dict(),
            "unit_id": command.unit_id,
        }
    elif isinstance(command, InactivateOrgUnitCommand):
        payload = {
            "actor_scope_epoch": command.actor_scope_epoch,
            "actor_user_id": command.actor_user_id,
            "expected_row_version": command.expected_row_version,
            "preview_token": command.preview_token,
            "reason": command.reason,
            "resolution": command.resolution.as_dict(),
            "unit_id": command.unit_id,
        }
    elif isinstance(command, SetMembershipCommand):
        payload = {
            "actor_scope_epoch": command.actor_scope_epoch,
            "actor_user_id": command.actor_user_id,
            "expected_current_leader_assignment_id": (
                command.expected_current_leader_assignment_id
            ),
            "expected_part_row_version": command.expected_part_row_version,
            "expected_user_row_version": command.expected_user_row_version,
            "part_id": command.part_id,
            "reason": command.reason,
            "replacement_leader_user_id": command.replacement_leader_user_id,
            "target_user_id": command.target_user_id,
        }
    elif isinstance(command, SetLeaderCommand):
        payload = {
            "actor_scope_epoch": command.actor_scope_epoch,
            "actor_user_id": command.actor_user_id,
            "expected_current_leader_assignment_id": (
                command.expected_current_leader_assignment_id
            ),
            "expected_part_row_version": command.expected_part_row_version,
            "leader_user_id": command.leader_user_id,
            "part_id": command.part_id,
            "reason": command.reason,
        }
    else:  # pragma: no cover - internal caller contract
        raise TypeError("Unsupported organization command type.")
    payload["operation_type"] = operation_type
    payload["schema_version"] = 1
    return sha256(_canonical_json(payload).encode("ascii")).hexdigest()


def _preview_payload_bytes(payload: Mapping[str, object]) -> bytes:
    return _canonical_json(payload).encode("ascii")


def _encode_preview_token(
    key: bytes,
    payload: Mapping[str, object],
) -> str:
    body = base64.urlsafe_b64encode(
        zlib.compress(_preview_payload_bytes(payload), level=9)
    ).rstrip(b"=")
    signature = hmac.digest(key, b"org-preview-v1\0" + body, "sha256")
    return "v1." + body.decode("ascii") + "." + base64.urlsafe_b64encode(
        signature
    ).rstrip(b"=").decode("ascii")


def _decode_preview_token(key: bytes, token: object) -> dict[str, object]:
    if not isinstance(token, str) or not 32 <= len(token) <= 4096:
        raise OrganizationPreviewError("The organization preview token is invalid.")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        raise OrganizationPreviewError("The organization preview token is invalid.")
    try:
        body = parts[1].encode("ascii")
        signature_text = parts[2].encode("ascii")
        signature = base64.b64decode(
            signature_text + b"=" * (-len(signature_text) % 4),
            altchars=b"-_",
            validate=True,
        )
        compressed = base64.b64decode(
            body + b"=" * (-len(body) % 4),
            altchars=b"-_",
            validate=True,
        )
        if (
            base64.urlsafe_b64encode(compressed).rstrip(b"=") != body
            or base64.urlsafe_b64encode(signature).rstrip(b"=")
            != signature_text
        ):
            raise ValueError("non-canonical base64")
    except (ValueError, UnicodeEncodeError) as exc:
        raise OrganizationPreviewError(
            "The organization preview token is invalid."
        ) from exc
    expected = hmac.digest(key, b"org-preview-v1\0" + body, "sha256")
    if not hmac.compare_digest(signature, expected):
        raise OrganizationPreviewError("The organization preview token is invalid.")
    try:
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(compressed, 65_537)
        if (
            len(raw) > 65_536
            or decompressor.unconsumed_tail
            or decompressor.unused_data
            or not decompressor.eof
        ):
            raise ValueError("preview payload too large")
        tail = decompressor.flush(65_537 - len(raw))
        raw += tail
        if len(raw) > 65_536 or decompressor.unused_data:
            raise ValueError("preview payload too large or has trailing data")
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError, zlib.error) as exc:
        raise OrganizationPreviewError(
            "The organization preview token is invalid."
        ) from exc
    if not isinstance(value, dict):
        raise OrganizationPreviewError("The organization preview token is invalid.")
    return value


class AccountService:
    """Atomic disable/re-enable commands over one canonical database."""

    __slots__ = (
        "_audit",
        "_affiliations",
        "_factory",
        "_failpoint",
        "_organization",
        "_outbox",
        "_users",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        users: UserRepository | None = None,
        organization: OrganizationRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("AccountService requires a ConnectionFactory.")
        self._factory = factory
        self._users = users or UserRepository()
        self._organization = organization or OrganizationRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()
        self._failpoint = failpoint

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    def import_legacy_users(
        self,
        records: Sequence[LegacyUserRecord],
    ) -> tuple[LegacyUserImportResult, ...]:
        """Idempotently import normalized users in one all-or-none transaction."""

        connection = self._factory.open()
        try:
            with transaction(connection):
                return self._users.import_legacy_many(connection, tuple(records))
        finally:
            connection.close()

    def disable_account(
        self, command: AccountLifecycleCommand
    ) -> AccountLifecycleResult:
        return self._set_status(command, "disabled")

    def reenable_account(
        self, command: AccountLifecycleCommand
    ) -> AccountLifecycleResult:
        if command.replacement_leader_user_id is not None:
            raise ValueError("Leader replacement is valid only while disabling.")
        return self._set_status(command, "active")

    def _authorize_admin(
        self,
        connection: ManagedConnection,
        actor_user_id: str,
        expected_scope_epoch: int | None = None,
    ) -> UserRecord:
        actor = self._users.require(connection, actor_user_id)
        if actor.account_status != "active" or not actor.is_admin:
            raise AccountAuthorizationError(
                "An active system administrator must perform this command."
            )
        if expected_scope_epoch is not None:
            if (
                isinstance(expected_scope_epoch, bool)
                or not isinstance(expected_scope_epoch, int)
                or expected_scope_epoch < 1
            ):
                raise ValueError("actor_scope_epoch must be a positive integer.")
            if actor.scope_epoch != expected_scope_epoch:
                raise AccountScopeStaleError(
                    "The administrator session scope is stale.",
                    details={
                        "current_scope_epoch": actor.scope_epoch,
                        "expected_scope_epoch": expected_scope_epoch,
                    },
                )
        return actor

    def _set_status(
        self,
        command: AccountLifecycleCommand,
        target_status: Literal["active", "disabled"],
    ) -> AccountLifecycleResult:
        if (
            not isinstance(command.reason, str)
            or not 1 <= len(command.reason.strip()) <= 2000
        ):
            raise ValueError("Account lifecycle reason must be 1 through 2000 characters.")
        safe_request_meta = _safe_request_meta(command.request_meta)
        operation_type = f"account.{target_status}"
        request_hash = _request_sha256(command, target_status)
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE,
            command.actor_user_id,
            operation_type,
            command.operation_id,
        )
        audit_event_uuid = _derived_uuid(_AUDIT_NAMESPACE, dedup_id)
        result: AccountLifecycleResult | None = None
        final_error: AccountServiceError | None = None

        connection = self._factory.open()
        try:
            with transaction(connection):
                actor = self._authorize_admin(
                    connection,
                    command.actor_user_id,
                    command.actor_scope_epoch,
                )
                now_ms = db_now_ms(connection)
                try:
                    begun = self._users.begin_operation(
                        connection,
                        actor_user_id=actor.id,
                        operation_type=operation_type,
                        operation_id=command.operation_id,
                        request_sha256=request_hash,
                        dedup_id=dedup_id,
                        now_ms=now_ms,
                    )
                except OperationIdempotencyConflictError as exc:
                    raise AccountIdempotencyError(str(exc)) from exc
                if not begun.inserted:
                    if begun.operation.status == "committed":
                        stored_result = begun.operation.result
                        if stored_result is None:
                            raise AccountIdempotencyError(
                                "The committed account operation has no result."
                            )
                        result = AccountLifecycleResult.from_mapping(stored_result)
                    elif begun.operation.status == "failed_final":
                        final_error = _stored_final_error(begun.operation.error)
                    else:
                        raise AccountOperationInProgressError(
                            "The account operation is already in progress."
                        )
                else:
                    target: UserRecord | None = None
                    try:
                        target = self._users.require(
                            connection, command.target_user_id
                        )
                        if target_status == "disabled" and actor.id == target.id:
                            raise AccountAuthorizationError(
                                "An administrator cannot disable their own active session.",
                                details={"target_user_id": target.id},
                            )
                        if (
                            target_status == "disabled"
                            and target.is_admin
                            and self._users.active_admin_count(
                                connection, excluding_user_id=target.id
                            ) < 1
                        ):
                            raise AccountStateConflictError(
                                "At least one active administrator must remain.",
                                details={"target_user_id": target.id},
                            )
                        plan = self._plan_status_change(
                            connection=connection,
                            target=target,
                            target_status=target_status,
                            command=command,
                            now_ms=now_ms,
                        )
                    except (
                        AccountAuthorizationError,
                        AccountStateConflictError,
                        AccountRevisionConflictError,
                        UserNotFoundError,
                        UserVersionConflictError,
                    ) as error:
                        final_error = _normalize_final_error(error)
                        self._record_final_failure(
                            connection=connection,
                            actor=actor,
                            target=target,
                            command=command,
                            operation_type=operation_type,
                            dedup_id=dedup_id,
                            audit_event_uuid=audit_event_uuid,
                            error=final_error,
                            now_ms=now_ms,
                            request_meta=safe_request_meta,
                        )
                    else:
                        result = self._apply_status_change(
                            connection=connection,
                            actor=actor,
                            target=target,
                            target_status=target_status,
                            command=command,
                            now_ms=now_ms,
                            audit_event_uuid=audit_event_uuid,
                            plan=plan,
                        )
                        self._audit.append(
                            connection,
                            AuditEventDraft(
                                event_uuid=audit_event_uuid,
                                occurred_at_ms=now_ms,
                                request_id=command.request_id,
                                operation_id=command.operation_id,
                                action=operation_type,
                                outcome="changed" if result.changed else "noop",
                                actor_kind="user",
                                actor_user_id=actor.id,
                                actor_display_name=actor.display_name,
                                actor_role_snapshot=actor.system_role,
                                reason=command.reason,
                                details={
                                    "account_status": result.account_status,
                                    "cancel_requested_job_count": result.cancel_requested_job_count,
                                    "closed_membership": result.closed_membership,
                                    "invalidated_job_count": result.invalidated_job_count,
                                    "leader_action": result.leader_action,
                                    "part_write_enabled_after": result.part_write_enabled_after,
                                    "previous_part_id": result.previous_part_id,
                                    "replacement_leader_user_id": result.replacement_leader_user_id,
                                    "revoked_lease_count": result.revoked_lease_count,
                                    "row_version": result.row_version,
                                    "schema_version": 1,
                                    "scope_epoch": result.scope_epoch,
                                    "target_user_id": result.target_user_id,
                                },
                                request_meta=safe_request_meta,
                            ),
                        )
                        self._hit("account.after_audit")
                        self._outbox.append(
                            connection,
                            OutboxEventDraft(
                                logical_key=f"account:{dedup_id}",
                                space_id=None,
                                aggregate_type="app_user",
                                aggregate_id=result.target_user_id,
                                aggregate_version=result.row_version,
                                event_type=f"account.{result.account_status}",
                                payload={
                                    "account_status": result.account_status,
                                    "operation_id": result.operation_id,
                                    "row_version": result.row_version,
                                    "schema_version": 1,
                                    "scope_epoch": result.scope_epoch,
                                    "target_user_id": result.target_user_id,
                                },
                                available_at_ms=now_ms,
                            ),
                        )
                        self._hit("account.after_outbox")
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
                            raise AccountIdempotencyError(str(exc)) from exc
                        self._hit("account.before_commit")
        finally:
            connection.close()

        if final_error is not None:
            raise final_error
        if result is None:  # pragma: no cover - every legal branch sets one
            raise AccountServiceError("The account command produced no terminal result.")
        return result

    def _record_final_failure(
        self,
        *,
        connection: ManagedConnection,
        actor: UserRecord,
        target: UserRecord | None,
        command: AccountLifecycleCommand,
        operation_type: str,
        dedup_id: str,
        audit_event_uuid: str,
        error: AccountServiceError,
        now_ms: int,
        request_meta: Mapping[str, object],
    ) -> None:
        error_payload = error.as_error()
        target_version = 0 if target is None else target.row_version
        details = {
            "error": error_payload,
            "row_version": target_version,
            "schema_version": 1,
            "target_user_id": command.target_user_id,
        }
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=audit_event_uuid,
                occurred_at_ms=now_ms,
                request_id=command.request_id,
                operation_id=command.operation_id,
                action=operation_type,
                outcome="rejected",
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                reason=command.reason,
                details=details,
                request_meta=request_meta,
            ),
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"account:{dedup_id}",
                space_id=None,
                aggregate_type="app_user",
                aggregate_id=command.target_user_id,
                aggregate_version=target_version,
                event_type="account.command_rejected",
                payload={
                    "error": error_payload,
                    "operation_id": command.operation_id,
                    "schema_version": 1,
                    "target_user_id": command.target_user_id,
                },
                available_at_ms=now_ms,
            ),
        )
        try:
            self._users.fail_operation_final(
                connection,
                dedup_id=dedup_id,
                error=error_payload,
                http_status=error.http_status,
                completed_at_ms=now_ms,
            )
        except (OperationIdempotencyConflictError, OperationInProgressError) as exc:
            raise AccountIdempotencyError(str(exc)) from exc

    def _plan_status_change(
        self,
        *,
        connection: ManagedConnection,
        target: UserRecord,
        target_status: Literal["active", "disabled"],
        command: AccountLifecycleCommand,
        now_ms: int,
    ) -> _StatusChangePlan:
        """Finish every expected terminal validation before the first write."""

        if target.row_version != command.expected_row_version:
            raise AccountRevisionConflictError(
                "The canonical user row version changed before the account command.",
                details={
                    "current_row_version": target.row_version,
                    "expected_row_version": command.expected_row_version,
                    "target_user_id": target.id,
                },
            )
        membership = self._users.get_open_membership(connection, target.id)
        leaders = self._users.list_active_leader_assignments(connection, target.id)
        if target.account_status == target_status:
            if command.replacement_leader_user_id is not None:
                raise AccountStateConflictError(
                    "A replacement can be supplied only for a real leader disable.",
                    details={"target_user_id": target.id},
                )
            return _StatusChangePlan(
                membership=membership,
                leader=None,
                part_space=None,
                replacement=None,
                part_unit=None,
                changed_at_ms=now_ms,
                no_op=True,
            )
        if len(leaders) > 1:
            raise AccountStateConflictError(
                "The account has multiple active leader assignments.",
                details={"target_user_id": target.id},
            )
        if target.current_part_id is None and membership is not None:
            raise AccountStateConflictError(
                "The account membership and current part disagree.",
                details={"target_user_id": target.id},
            )
        if target.current_part_id is not None and (
            membership is None or membership.part_id != target.current_part_id
        ):
            raise AccountStateConflictError(
                "The account membership and current part disagree.",
                details={"target_user_id": target.id},
            )
        leader = leaders[0] if leaders else None
        if leader is not None and leader.part_id != target.current_part_id:
            raise AccountStateConflictError(
                "The account leader assignment and current part disagree.",
                details={"target_user_id": target.id},
            )
        strict_leader_cas = (
            command.expected_current_leader_assignment_id is not None
            or command.expected_part_row_version is not None
        )
        current_part_leader = (
            None
            if target.current_part_id is None
            else self._users.get_active_part_leader(
                connection, target.current_part_id
            )
        )
        actual_current_assignment_id = (
            None if current_part_leader is None else current_part_leader.id
        )
        if strict_leader_cas and (
            command.expected_current_leader_assignment_id
            != actual_current_assignment_id
        ):
            raise AccountRevisionConflictError(
                "The part leader assignment changed before the account command.",
                details={
                    "current_leader_assignment_id": actual_current_assignment_id,
                    "expected_current_leader_assignment_id": (
                        command.expected_current_leader_assignment_id
                    ),
                    "part_id": target.current_part_id,
                },
            )
        if target_status == "active" and (membership is not None or leader is not None):
            raise AccountStateConflictError(
                "A disabled account retains unexpected organization authority.",
                details={"target_user_id": target.id},
            )

        changed_at_ms = now_ms
        if membership is not None:
            changed_at_ms = max(changed_at_ms, membership.valid_from_ms + 1)
        if leader is not None:
            changed_at_ms = max(changed_at_ms, leader.valid_from_ms + 1)

        part_space: PartSpaceWriteState | None = None
        replacement: UserRecord | None = None
        part_unit: OrgUnitRecord | None = None
        if target_status == "disabled" and leader is not None:
            part_unit = self._organization.require_unit(connection, leader.part_id)
            if strict_leader_cas and command.expected_part_row_version is None:
                raise AccountRevisionConflictError(
                    "A leader-affecting account command requires the part row version.",
                    details={"part_id": leader.part_id},
                )
            if (
                command.expected_part_row_version is not None
                and part_unit.row_version != command.expected_part_row_version
            ):
                raise AccountRevisionConflictError(
                    "The part aggregate changed before the account command.",
                    details={
                        "current_row_version": part_unit.row_version,
                        "expected_row_version": command.expected_part_row_version,
                        "part_id": leader.part_id,
                    },
                )
            part_space = self._users.get_part_space_write_state(
                connection, leader.part_id
            )
            replacement_id = command.replacement_leader_user_id
            if replacement_id is not None:
                if replacement_id == target.id:
                    raise AccountStateConflictError(
                        "The disabled leader cannot replace themselves.",
                        details={"target_user_id": target.id},
                    )
                replacement = self._users.require(connection, replacement_id)
                replacement_membership = self._users.get_open_membership(
                    connection, replacement.id
                )
                if (
                    replacement.account_status != "active"
                    or replacement.current_part_id != leader.part_id
                    or replacement_membership is None
                    or replacement_membership.part_id != leader.part_id
                ):
                    raise AccountStateConflictError(
                        "The replacement leader must be an active member of the same part.",
                        details={
                            "replacement_leader_user_id": replacement.id,
                            "target_user_id": target.id,
                        },
                    )
        elif command.replacement_leader_user_id is not None:
            raise AccountStateConflictError(
                "A replacement can be supplied only for an active part leader.",
                details={"target_user_id": target.id},
            )

        return _StatusChangePlan(
            membership=membership,
            leader=leader,
            part_space=part_space,
            replacement=replacement,
            part_unit=part_unit,
            changed_at_ms=changed_at_ms,
            no_op=False,
        )

    def _apply_status_change(
        self,
        *,
        connection: ManagedConnection,
        actor: UserRecord,
        target: UserRecord,
        target_status: Literal["active", "disabled"],
        command: AccountLifecycleCommand,
        now_ms: int,
        audit_event_uuid: str,
        plan: _StatusChangePlan,
    ) -> AccountLifecycleResult:
        membership = plan.membership
        leader = plan.leader
        if plan.no_op:
            transition_result = self._users.transition_account_status(
                connection,
                user_id=target.id,
                target_status=target_status,
                expected_row_version=command.expected_row_version,
                changed_at_ms=now_ms,
            )
            return AccountLifecycleResult(
                operation_id=command.operation_id,
                target_user_id=target.id,
                account_status=target.account_status,
                scope_epoch=target.scope_epoch,
                row_version=target.row_version,
                changed=False,
                previous_part_id=transition_result.previous_part_id,
                closed_membership=False,
                leader_action="none",
                replacement_leader_user_id=None,
                part_write_enabled_after=None,
                revoked_lease_count=0,
                invalidated_job_count=0,
                cancel_requested_job_count=0,
                audit_event_uuid=audit_event_uuid,
            )

        changed_at_ms = plan.changed_at_ms

        leader_action: Literal["none", "replaced", "write_disabled"] = "none"
        replacement_id: str | None = None
        part_write_enabled_after: bool | None = None
        part_space = plan.part_space
        prior_write_enabled = False

        if target_status == "disabled" and leader is not None:
            prior_write_enabled = bool(
                part_space is not None and part_space.write_enabled
            )
            if prior_write_enabled and part_space is not None:
                part_space = self._users.set_part_space_write_enabled(
                    connection,
                    part_id=leader.part_id,
                    enabled=False,
                    expected_row_version=part_space.row_version,
                    updated_at_ms=changed_at_ms,
                )

            replacement_id = None if plan.replacement is None else plan.replacement.id
            if replacement_id is not None:
                assert plan.replacement is not None

            self._users.close_leader_assignment(
                connection,
                assignment_id=leader.id,
                closed_at_ms=changed_at_ms,
            )
            if replacement_id is None:
                leader_action = "write_disabled"
            else:
                self._users.open_part_leader_assignment(
                    connection,
                    assignment_id=_derived_uuid(
                        _ROLE_NAMESPACE,
                        actor.id,
                        command.operation_id,
                        leader.part_id,
                        replacement_id,
                    ),
                    user_id=replacement_id,
                    part_id=leader.part_id,
                    assigned_by=actor.id,
                    reason=(
                        "Account disable leader replacement; "
                        f"operation_id={command.operation_id}"
                    ),
                    valid_from_ms=changed_at_ms,
                )
                leader_action = "replaced"

            assert plan.part_unit is not None
            self._organization.touch_unit(
                connection,
                unit_id=leader.part_id,
                expected_row_version=plan.part_unit.row_version,
                actor_user_id=actor.id,
                updated_at_ms=changed_at_ms,
            )

        closed_membership = False
        if target_status == "disabled" and membership is not None:
            self._users.close_open_membership(
                connection,
                user_id=target.id,
                closed_at_ms=changed_at_ms,
            )
            closed_membership = True

        try:
            transition_result: AccountStatusTransition = (
                self._users.transition_account_status(
                    connection,
                    user_id=target.id,
                    target_status=target_status,
                    expected_row_version=command.expected_row_version,
                    changed_at_ms=changed_at_ms,
                )
            )
        except UserVersionConflictError:
            raise
        self._hit("account.after_status")
        self._hit("account.after_scope_epoch")
        self._hit("account.after_session_revoke")

        if (
            target_status == "disabled"
            and leader_action == "replaced"
            and prior_write_enabled
            and part_space is not None
        ):
            part_space = self._users.set_part_space_write_enabled(
                connection,
                part_id=part_space.part_id,
                enabled=True,
                expected_row_version=part_space.row_version,
                updated_at_ms=changed_at_ms,
            )
        if part_space is not None:
            current_part_space = self._users.get_part_space_write_state(
                connection, part_space.part_id
            )
            part_write_enabled_after = (
                None
                if current_part_space is None
                else current_part_space.write_enabled
            )

        updated = transition_result.user
        return AccountLifecycleResult(
            operation_id=command.operation_id,
            target_user_id=updated.id,
            account_status=updated.account_status,
            scope_epoch=updated.scope_epoch,
            row_version=updated.row_version,
            changed=transition_result.changed,
            previous_part_id=transition_result.previous_part_id,
            closed_membership=closed_membership,
            leader_action=leader_action,
            replacement_leader_user_id=replacement_id,
            part_write_enabled_after=part_write_enabled_after,
            revoked_lease_count=transition_result.revoked_lease_count,
            invalidated_job_count=transition_result.invalidated_job_count,
            cancel_requested_job_count=transition_result.cancel_requested_job_count,
            audit_event_uuid=audit_event_uuid,
        )


@dataclass(frozen=True, slots=True)
class _OrganizationApplied:
    result: OrganizationMutationResult
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    event_type: str
    details: Mapping[str, object] = field(default_factory=dict)


class _OrganizationDomainRollback(Exception):
    """Carry a post-write failure across a mandatory transaction rollback."""

    __slots__ = ("error",)

    def __init__(self, error: BaseException) -> None:
        super().__init__(str(error))
        self.error = error


@dataclass(frozen=True, slots=True)
class _OrgPreviewSnapshot:
    unit: OrgUnitRecord
    new_parent: OrgUnitRecord | None
    subtree_ids: frozenset[str]
    members: tuple[tuple[MembershipRecord, UserOrganizationState], ...]
    leader: LeaderAssignmentRecord | None
    higher_roles: tuple[HigherScopeRoleAssignmentRecord, ...]
    invalid_distributions: tuple[KnowledgeDistributionGrantRecord, ...]
    subtree_spaces: tuple[PartSpaceState, ...]
    space: PartSpaceState | None
    impact: OrgUnitImpactCounts
    blockers: tuple[Mapping[str, object], ...]
    facts: Mapping[str, object]
    impact_sha256: str


def _normalize_org_final_error(error: BaseException) -> OrganizationServiceError:
    if isinstance(error, OrganizationServiceError):
        if not error.retryable and error.code in _ORG_FINAL_ERROR_TYPES:
            return error
        raise TypeError("Retryable organization errors cannot become failed-final.")
    if isinstance(
        error,
        (
            OrgRepoUnitVersionConflictError,
            OrgRepoPartSpaceVersionConflictError,
            OrgRepoUserVersionConflictError,
            UserVersionConflictError,
        ),
    ):
        return OrganizationRevisionConflictError(str(error))
    if isinstance(
        error,
        (
            OrgRepoUnitNotFoundError,
            OrgRepoPartSpaceNotFoundError,
            OrgRepoUserNotFoundError,
            OrgRepoMembershipNotFoundError,
            OrgRepoLeaderNotFoundError,
            UserNotFoundError,
        ),
    ):
        return OrganizationNotFoundError(str(error))
    if isinstance(error, OrgRepoStateConflictError):
        return OrganizationStateConflictError(str(error))
    raise TypeError("Only stable organization errors may become failed-final.")


class OrganizationService:
    """Administrator-only hierarchy, membership, and leadership commands."""

    __slots__ = (
        "_audit",
        "_affiliations",
        "_factory",
        "_failpoint",
        "_flags",
        "_higher",
        "_organization",
        "_outbox",
        "_preview_hmac_key",
        "_spaces",
        "_users",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        preview_hmac_key: bytes,
        users: UserRepository | None = None,
        organization: OrganizationRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        flags: FeatureFlags = FeatureFlags(),
        spaces: SpaceRepository | None = None,
        higher: HigherSpaceRepository | None = None,
        affiliations: OrgAffiliationRepository | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("OrganizationService requires a ConnectionFactory.")
        if (
            not isinstance(preview_hmac_key, bytes)
            or len(preview_hmac_key) < 32
        ):
            raise ValueError("preview_hmac_key must contain at least 32 bytes.")
        if not isinstance(flags, FeatureFlags):
            raise TypeError("flags must be a FeatureFlags snapshot.")
        self._factory = factory
        self._preview_hmac_key = bytes(preview_hmac_key)
        self._users = users or UserRepository()
        self._affiliations = affiliations or OrgAffiliationRepository()
        self._organization = organization or OrganizationRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()
        self._flags = flags
        self._spaces = spaces or SpaceRepository()
        self._higher = higher or HigherSpaceRepository()
        self._failpoint = failpoint

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    def _active_admin(
        self,
        connection: ManagedConnection,
        actor_user_id: str,
    ) -> UserRecord:
        try:
            actor = self._users.require(connection, actor_user_id)
        except UserNotFoundError as exc:
            raise OrganizationAuthorizationError(
                "An active system administrator must perform this command."
            ) from exc
        if actor.account_status != "active" or not actor.is_admin:
            raise OrganizationAuthorizationError(
                "An active system administrator must perform this command."
            )
        return actor

    @staticmethod
    def _require_actor_epoch(actor: UserRecord, expected_scope_epoch: int) -> None:
        if (
            isinstance(expected_scope_epoch, bool)
            or not isinstance(expected_scope_epoch, int)
            or expected_scope_epoch < 1
        ):
            raise OrganizationValidationError(
                "actor_scope_epoch must be a positive integer."
            )
        if actor.scope_epoch != expected_scope_epoch:
            raise OrganizationScopeStaleError(
                "The administrator session scope is stale.",
                details={
                    "current_scope_epoch": actor.scope_epoch,
                    "expected_scope_epoch": expected_scope_epoch,
                },
            )

    def _run_mutation(
        self,
        command: (
            CreateOrgUnitCommand
            | RenameOrgUnitCommand
            | MoveOrgUnitCommand
            | InactivateOrgUnitCommand
            | SetMembershipCommand
            | SetLeaderCommand
        ),
        *,
        operation_type: str,
        reason: str | None,
        apply: Callable[
            [
                ManagedConnection,
                UserRecord,
                int,
                str,
                Callable[[], None],
            ],
            _OrganizationApplied,
        ],
    ) -> OrganizationMutationResult:
        safe_meta = _safe_request_meta(command.request_meta)
        request_hash = _org_semantic_hash(operation_type, command)
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE,
            command.actor_user_id,
            operation_type,
            command.operation_id,
        )
        audit_event_uuid = _derived_uuid(_AUDIT_NAMESPACE, dedup_id)
        result: OrganizationMutationResult | None = None
        final_error: OrganizationServiceError | None = None
        connection = self._factory.open()
        try:
            with transaction(connection):
                actor = self._active_admin(connection, command.actor_user_id)
                now_ms = db_now_ms(connection)
                try:
                    begun = self._users.begin_operation(
                        connection,
                        actor_user_id=actor.id,
                        operation_type=operation_type,
                        operation_id=command.operation_id,
                        request_sha256=request_hash,
                        dedup_id=dedup_id,
                        now_ms=now_ms,
                    )
                except OperationIdempotencyConflictError as exc:
                    raise OrganizationIdempotencyError(str(exc)) from exc
                if not begun.inserted:
                    if begun.operation.status == "committed":
                        stored_result = begun.operation.result
                        if stored_result is None:
                            raise OrganizationIdempotencyError(
                                "The committed organization operation has no result."
                            )
                        result = replace(
                            OrganizationMutationResult.from_mapping(stored_result),
                            request_id=command.request_id,
                        )
                    elif begun.operation.status == "failed_final":
                        final_error = _stored_org_final_error(
                            begun.operation.error
                        )
                    else:
                        raise OrganizationOperationInProgressError(
                            "The organization operation is already in progress."
                        )
                else:
                    applied: _OrganizationApplied | None = None
                    domain_write_started = False

                    def mark_domain_write_started() -> None:
                        nonlocal domain_write_started
                        domain_write_started = True

                    try:
                        self._require_actor_epoch(
                            actor, command.actor_scope_epoch
                        )
                        applied = apply(
                            connection,
                            actor,
                            now_ms,
                            dedup_id,
                            mark_domain_write_started,
                        )
                    except (
                        OrganizationAuthorizationError,
                        OrganizationScopeStaleError,
                        OrganizationRevisionConflictError,
                        OrganizationStateConflictError,
                        OrganizationNotFoundError,
                        OrganizationPreviewError,
                        OrganizationValidationError,
                        OrgRepoUnitNotFoundError,
                        OrgRepoUnitVersionConflictError,
                        OrgRepoStateConflictError,
                        OrgRepoMembershipNotFoundError,
                        OrgRepoLeaderNotFoundError,
                        OrgRepoPartSpaceNotFoundError,
                        OrgRepoPartSpaceVersionConflictError,
                        OrgRepoUserNotFoundError,
                        OrgRepoUserVersionConflictError,
                        UserNotFoundError,
                        UserVersionConflictError,
                    ) as error:
                        if domain_write_started:
                            # A post-write repository/CAS error must roll the
                            # complete transaction back.  Committing a
                            # failed-final envelope here would also commit the
                            # earlier domain DML because managed connections do
                            # not expose application savepoints.
                            raise _OrganizationDomainRollback(error) from error
                        final_error = _normalize_org_final_error(error)
                        self._record_org_final_failure(
                            connection=connection,
                            actor=actor,
                            command=command,
                            operation_type=operation_type,
                            dedup_id=dedup_id,
                            audit_event_uuid=audit_event_uuid,
                            error=final_error,
                            now_ms=now_ms,
                            reason=reason,
                            request_meta=safe_meta,
                        )
                    else:
                        self._hit("organization.after_domain")
                        result = applied.result
                        details = {
                            "resource_id": result.resource_id,
                            "row_version": result.row_version,
                            "schema_version": 1,
                            "status": result.status,
                            **dict(applied.details),
                        }
                        self._audit.append(
                            connection,
                            AuditEventDraft(
                                event_uuid=audit_event_uuid,
                                occurred_at_ms=now_ms,
                                request_id=command.request_id,
                                operation_id=command.operation_id,
                                action=operation_type,
                                outcome=result.status,
                                actor_kind="user",
                                actor_user_id=actor.id,
                                actor_display_name=actor.display_name,
                                actor_role_snapshot=actor.system_role,
                                reason=reason,
                                details=details,
                                request_meta=safe_meta,
                            ),
                        )
                        self._hit("organization.after_audit")
                        outbox_payload: dict[str, object] = {
                            "operation_id": command.operation_id,
                            "resource_id": result.resource_id,
                            "row_version": result.row_version,
                            "schema_version": 1,
                            "status": result.status,
                        }
                        if applied.event_type == "organization.unit.moved":
                            outbox_payload["closure_invalidation"] = {
                                key: details[key]
                                for key in (
                                    "affected_job_space_ids",
                                    "affected_projection_space_ids",
                                    "cancel_requested_job_count",
                                    "invalidated_distribution_count",
                                    "invalidated_distribution_ids",
                                    "invalidated_job_count",
                                    "invalidated_projection_count",
                                    "new_parent_id",
                                    "old_parent_id",
                                )
                            }
                        self._outbox.append(
                            connection,
                            OutboxEventDraft(
                                logical_key=f"organization:{dedup_id}",
                                space_id=None,
                                aggregate_type=applied.aggregate_type,
                                aggregate_id=applied.aggregate_id,
                                aggregate_version=applied.aggregate_version,
                                event_type=applied.event_type,
                                payload=outbox_payload,
                                available_at_ms=now_ms,
                            ),
                        )
                        self._hit("organization.after_outbox")
                        try:
                            self._users.commit_operation(
                                connection,
                                dedup_id=dedup_id,
                                result=result.as_dict(),
                                http_status=(
                                    201
                                    if operation_type == "organization.unit.create"
                                    else 200
                                ),
                                completed_at_ms=now_ms,
                            )
                        except (
                            OperationIdempotencyConflictError,
                            OperationInProgressError,
                        ) as exc:
                            raise OrganizationIdempotencyError(str(exc)) from exc
                        self._hit("organization.before_commit")
        except _OrganizationDomainRollback as rollback:
            final_error = OrganizationFailpointTriggered(
                "The organization mutation was rolled back before commit.",
                details={
                    "cause": type(rollback.error).__name__,
                    "state": "operation_uncommitted",
                },
            )
        finally:
            connection.close()
        if final_error is not None:
            raise final_error
        if result is None:  # pragma: no cover - every legal path is terminal
            raise OrganizationServiceError(
                "The organization command produced no terminal result."
            )
        return result

    def _record_org_final_failure(
        self,
        *,
        connection: ManagedConnection,
        actor: UserRecord,
        command: object,
        operation_type: str,
        dedup_id: str,
        audit_event_uuid: str,
        error: OrganizationServiceError,
        now_ms: int,
        reason: str | None,
        request_meta: Mapping[str, object],
    ) -> None:
        operation_id = str(getattr(command, "operation_id"))
        request_id = str(getattr(command, "request_id"))
        target_id = (
            getattr(command, "unit_id", None)
            or getattr(command, "target_user_id", None)
            or getattr(command, "part_id", None)
            or "organization"
        )
        payload = error.as_error()
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=audit_event_uuid,
                occurred_at_ms=now_ms,
                request_id=request_id,
                operation_id=operation_id,
                action=operation_type,
                outcome="rejected",
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                reason=reason,
                details={
                    "error": payload,
                    "schema_version": 1,
                    "target_id": target_id,
                },
                request_meta=request_meta,
            ),
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"organization:{dedup_id}",
                space_id=None,
                aggregate_type="organization_command",
                aggregate_id=str(target_id),
                aggregate_version=0,
                event_type="organization.command_rejected",
                payload={
                    "error": payload,
                    "operation_id": operation_id,
                    "schema_version": 1,
                    "target_id": target_id,
                },
                available_at_ms=now_ms,
            ),
        )
        try:
            self._users.fail_operation_final(
                connection,
                dedup_id=dedup_id,
                error=payload,
                http_status=error.http_status,
                completed_at_ms=now_ms,
            )
        except (OperationIdempotencyConflictError, OperationInProgressError) as exc:
            raise OrganizationIdempotencyError(str(exc)) from exc

    @staticmethod
    def _blocker(
        code: str, message: str, resource_id: str | None
    ) -> Mapping[str, object]:
        return {
            "code": code,
            "message": message,
            "resource_id": resource_id,
        }

    @staticmethod
    def _preview_resolution_facts(
        snapshot: _OrgPreviewSnapshot,
    ) -> dict[str, object]:
        return {
            "expected_current_leader_assignment_id": (
                None if snapshot.leader is None else snapshot.leader.id
            ),
            "expected_member_versions": [
                {
                    "expected_user_row_version": user.row_version,
                    "user_id": membership.user_id,
                }
                for membership, user in snapshot.members
            ],
            "expected_space_row_version": (
                None if snapshot.space is None else snapshot.space.row_version
            ),
            "space_state": (
                "not_created" if snapshot.space is None else snapshot.space.status
            ),
        }

    def _build_change_snapshot(
        self,
        connection: ManagedConnection,
        *,
        unit_id: str,
        expected_row_version: int,
        change: Literal["move", "inactivate"],
        new_parent_id: str | None,
    ) -> _OrgPreviewSnapshot:
        if change not in ("move", "inactivate"):
            raise OrganizationValidationError(
                "change must be move or inactivate."
            )
        if (
            isinstance(expected_row_version, bool)
            or not isinstance(expected_row_version, int)
            or expected_row_version < 1
        ):
            raise OrganizationValidationError(
                "expected_row_version must be a positive integer."
            )
        unit = self._organization.require_unit(connection, unit_id)
        if unit.row_version != expected_row_version:
            raise OrganizationRevisionConflictError(
                "The organization-unit row version changed after preview.",
                details={
                    "current_row_version": unit.row_version,
                    "expected_row_version": expected_row_version,
                },
            )
        subtree = self._organization.list_subtree(
            connection, root_id=unit.id, include_inactive=True
        )
        subtree_ids = frozenset(row.unit.id for row in subtree)
        part_ids = frozenset(
            row.unit.id for row in subtree if row.unit.kind == "part"
        )
        memberships = tuple(
            membership
            for membership in self._organization.list_current_memberships(
                connection
            )
            if membership.part_id in part_ids
        )
        members: list[tuple[MembershipRecord, UserOrganizationState]] = []
        for membership in memberships:
            user = self._organization.get_user_organization_state(
                connection, membership.user_id
            )
            if (
                user is None
                or user.current_part_id != membership.part_id
                or user.current_membership_id != membership.id
                or user.membership_part_id != membership.part_id
            ):
                raise OrganizationStateConflictError(
                    "A current member and canonical user scope disagree."
                )
            members.append((membership, user))
        members.sort(key=lambda value: (value[0].user_id, value[0].id))
        all_leaders = tuple(
            leader
            for leader in self._organization.list_active_leaders(connection)
            if leader.part_id in part_ids
        )
        leader = (
            self._organization.get_active_part_leader(connection, unit.id)
            if unit.kind == "part"
            else None
        )
        spaces = tuple(
            owned_space
            for row in subtree
            if row.unit.kind in ("responsibility", "team", "part")
            and (
                owned_space := self._organization.get_org_unit_space_state(
                    connection,
                    org_unit_id=row.unit.id,
                    expected_kind=row.unit.kind,
                )
            )
            is not None
        )
        space = self._organization.get_org_unit_space_state(
            connection,
            org_unit_id=unit.id,
            expected_kind=unit.kind,
        )
        impact = self._organization.get_unit_impact(connection, unit.id)
        blockers: list[Mapping[str, object]] = []
        new_parent: OrgUnitRecord | None = None
        higher_roles: tuple[HigherScopeRoleAssignmentRecord, ...] = ()
        invalid_distributions: tuple[KnowledgeDistributionGrantRecord, ...] = ()
        sibling_facts: list[dict[str, object]] = []
        if unit.id == RESPONSIBILITY_ROOT_ID:
            blockers.append(
                self._blocker(
                    "IMMUTABLE_ROOT",
                    "The AMHS responsibility root is immutable.",
                    unit.id,
                )
            )
        if unit.status != "active":
            blockers.append(
                self._blocker(
                    "UNIT_INACTIVE",
                    "An inactive organization unit cannot be changed again.",
                    unit.id,
                )
            )
        if len(members) > 200:
            blockers.append(
                self._blocker(
                    "MEMBER_RESOLUTION_LIMIT",
                    "At most 200 current members can be resolved in one change.",
                    unit.id,
                )
            )
        if change == "move":
            if new_parent_id is None:
                raise OrganizationValidationError(
                    "new_parent_id is required for a move preview."
                )
            new_parent = self._organization.require_unit(
                connection, new_parent_id
            )
            expected_parent_kind = (
                "responsibility" if unit.kind == "team" else "team"
            )
            if (
                unit.kind not in ("team", "part")
                or new_parent.kind != expected_parent_kind
                or new_parent.status != "active"
            ):
                blockers.append(
                    self._blocker(
                        "INVALID_PARENT",
                        "The move destination is not an active valid parent.",
                        new_parent.id,
                    )
                )
            if new_parent.id in subtree_ids:
                blockers.append(
                    self._blocker(
                        "MOVE_CYCLE",
                        "The move destination is inside the unit subtree.",
                        new_parent.id,
                    )
                )
            if unit.parent_id == new_parent.id:
                blockers.append(
                    self._blocker(
                        "MOVE_UNCHANGED",
                        "The organization unit already has that parent.",
                        new_parent.id,
                    )
                )
            siblings = self._organization.list_units(
                connection,
                parent_id=new_parent.id,
                direct_children_only=True,
            )
            sibling_facts = [
                {
                    "id": sibling.id,
                    "kind": sibling.kind,
                    "name_key": sibling.name_key,
                    "row_version": sibling.row_version,
                    "status": sibling.status,
                }
                for sibling in siblings
            ]
            duplicate = next(
                (
                    sibling
                    for sibling in siblings
                    if sibling.id != unit.id
                    and sibling.kind == unit.kind
                    and sibling.status == "active"
                    and sibling.name_key == unit.name_key
                ),
                None,
            )
            if duplicate is not None:
                blockers.append(
                    self._blocker(
                        "DUPLICATE_SIBLING_NAME",
                        "The destination already has an active sibling with that name.",
                        duplicate.id,
                    )
                )
            higher_roles = self._higher.list_open_roles_blocking_org_move(
                connection,
                moved_org_unit_id=unit.id,
                limit=ORG_MOVE_INVALIDATION_MAX_SIZE + 1,
            )
            if higher_roles:
                blockers.append(
                    self._blocker(
                        "OPEN_HIGHER_ROLE",
                        "End or replace every affected higher role before moving this unit.",
                        higher_roles[0].id,
                    )
                )
            if (
                unit.kind in ("team", "part")
                and new_parent.kind
                == ("responsibility" if unit.kind == "team" else "team")
                and new_parent.status == "active"
            ):
                invalid_distributions = (
                    self._higher.list_distributions_invalid_after_org_move(
                        connection,
                        moved_org_unit_id=unit.id,
                        new_parent_org_unit_id=new_parent.id,
                        limit=ORG_MOVE_INVALIDATION_MAX_SIZE + 1,
                    )
                )
                if len(invalid_distributions) > ORG_MOVE_INVALIDATION_MAX_SIZE:
                    blockers.append(
                        self._blocker(
                            "DISTRIBUTION_INVALIDATION_LIMIT",
                            "Resolve distribution grants in bounded batches before this move.",
                            unit.id,
                        )
                    )
        elif new_parent_id is not None:
            raise OrganizationValidationError(
                "new_parent_id must be null for an inactivation preview."
            )
        if change == "inactivate" and impact.active_direct_child_count:
            blockers.append(
                self._blocker(
                    "ACTIVE_CHILDREN",
                    "Active direct children must be resolved before inactivation.",
                    unit.id,
                )
            )
        facts: dict[str, object] = {
            "change": change,
            "impact": impact.as_dict(),
            "leaders": [
                {
                    "assignment_id": value.id,
                    "part_id": value.part_id,
                    "user_id": value.user_id,
                    "valid_from_ms": value.valid_from_ms,
                }
                for value in all_leaders
            ],
            "higher_roles": [
                {
                    "assignment_generation": value.assignment_generation,
                    "assignment_id": value.id,
                    "org_unit_id": value.org_unit_id,
                    "role": value.role,
                    "row_version": value.row_version,
                    "user_id": value.user_id,
                }
                for value in higher_roles
            ],
            "invalid_distributions": [
                {
                    "grant_id": value.id,
                    "row_version": value.row_version,
                    "source_revision_id": value.source_revision_id,
                    "source_space_id": value.source_space_id,
                    "target_org_unit_id": value.target_org_unit_id,
                }
                for value in invalid_distributions
            ],
            "members": [
                {
                    "account_status": user.account_status,
                    "membership_id": membership.id,
                    "membership_part_id": membership.part_id,
                    "membership_valid_from_ms": membership.valid_from_ms,
                    "scope_epoch": user.scope_epoch,
                    "user_id": user.user_id,
                    "user_row_version": user.row_version,
                }
                for membership, user in members
            ],
            "new_parent": (
                None
                if new_parent is None
                else {
                    "id": new_parent.id,
                    "kind": new_parent.kind,
                    "parent_id": new_parent.parent_id,
                    "row_version": new_parent.row_version,
                    "status": new_parent.status,
                }
            ),
            "siblings": sibling_facts,
            "spaces": [
                {
                    "generation": value.generation,
                    "owner_org_unit_id": value.owner_org_unit_id,
                    "row_version": value.row_version,
                    "space_id": value.space_id,
                    "space_kind": value.space_kind,
                    "status": value.status,
                    "write_enabled": value.write_enabled,
                }
                for value in spaces
            ],
            "subtree": [
                {
                    "id": row.unit.id,
                    "kind": row.unit.kind,
                    "name_key": row.unit.name_key,
                    "parent_id": row.unit.parent_id,
                    "row_version": row.unit.row_version,
                    "status": row.unit.status,
                }
                for row in subtree
            ],
            "unit": {
                "id": unit.id,
                "kind": unit.kind,
                "parent_id": unit.parent_id,
                "row_version": unit.row_version,
                "status": unit.status,
            },
        }
        impact_sha256 = sha256(
            _canonical_json(facts).encode("ascii")
        ).hexdigest()
        return _OrgPreviewSnapshot(
            unit=unit,
            new_parent=new_parent,
            subtree_ids=subtree_ids,
            members=tuple(members),
            leader=leader,
            higher_roles=higher_roles,
            invalid_distributions=invalid_distributions,
            subtree_spaces=spaces,
            space=space,
            impact=impact,
            blockers=tuple(blockers),
            facts=facts,
            impact_sha256=impact_sha256,
        )

    def _issue_change_preview_token(
        self,
        *,
        actor: UserRecord,
        command: OrgChangePreviewCommand,
        snapshot: _OrgPreviewSnapshot,
        now_ms: int,
    ) -> tuple[str, int]:
        expires_at_ms = now_ms + _ORG_PREVIEW_TTL_MS
        resolution = self._preview_resolution_facts(snapshot)
        resolution_sha256 = sha256(
            _canonical_json(resolution).encode("ascii")
        ).hexdigest()
        payload: dict[str, object] = {
            "actor_scope_epoch": actor.scope_epoch,
            "actor_user_id": actor.id,
            "allowed": not snapshot.blockers,
            "change": command.change,
            "expected_current_leader_assignment_id": resolution[
                "expected_current_leader_assignment_id"
            ],
            "expected_member_versions": resolution[
                "expected_member_versions"
            ],
            "expected_resolution_sha256": resolution_sha256,
            "expected_space_row_version": resolution[
                "expected_space_row_version"
            ],
            "expires_at_ms": expires_at_ms,
            "impact_sha256": snapshot.impact_sha256,
            "issued_at_ms": now_ms,
            "new_parent_id": command.new_parent_id,
            "schema_version": 1,
            "space_state": resolution["space_state"],
            "unit_id": command.unit_id,
            "unit_row_version": command.expected_row_version,
        }
        token = _encode_preview_token(self._preview_hmac_key, payload)
        if len(token) > 4096:
            # The signed full impact hash still binds every member fact.  The
            # compact digest keeps the public token within its frozen maximum
            # for the contract's 200-member worst case.
            payload["expected_member_versions"] = None
            payload["expected_member_versions_sha256"] = sha256(
                _canonical_json(
                    {"values": resolution["expected_member_versions"]}
                ).encode("ascii")
            ).hexdigest()
            token = _encode_preview_token(self._preview_hmac_key, payload)
        if len(token) > 4096:  # pragma: no cover - fixed compact envelope
            raise OrganizationPreviewError(
                "The organization preview token exceeds its contract limit."
            )
        return token, expires_at_ms

    def _validate_change_preview_token(
        self,
        connection: ManagedConnection,
        *,
        actor: UserRecord,
        unit_id: str,
        expected_row_version: int,
        change: Literal["move", "inactivate"],
        new_parent_id: str | None,
        preview_token: str,
        now_ms: int,
    ) -> _OrgPreviewSnapshot:
        payload = _decode_preview_token(self._preview_hmac_key, preview_token)
        issued_at = payload.get("issued_at_ms")
        expires_at = payload.get("expires_at_ms")
        if (
            isinstance(issued_at, bool)
            or not isinstance(issued_at, int)
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, int)
            or expires_at - issued_at != _ORG_PREVIEW_TTL_MS
            or issued_at > now_ms
            or now_ms >= expires_at
        ):
            raise OrganizationPreviewError(
                "The organization preview token is expired or invalid."
            )
        expected_envelope = {
            "actor_scope_epoch": actor.scope_epoch,
            "actor_user_id": actor.id,
            "change": change,
            "new_parent_id": new_parent_id,
            "schema_version": 1,
            "unit_id": unit_id,
            "unit_row_version": expected_row_version,
        }
        if any(payload.get(key) != value for key, value in expected_envelope.items()):
            raise OrganizationPreviewError(
                "The organization preview token does not match this command."
            )
        snapshot = self._build_change_snapshot(
            connection,
            unit_id=unit_id,
            expected_row_version=expected_row_version,
            change=change,
            new_parent_id=new_parent_id,
        )
        resolution = self._preview_resolution_facts(snapshot)
        resolution_sha256 = sha256(
            _canonical_json(resolution).encode("ascii")
        ).hexdigest()
        member_versions_sha256 = sha256(
            _canonical_json(
                {"values": resolution["expected_member_versions"]}
            ).encode("ascii")
        ).hexdigest()
        member_versions_match = (
            payload.get("expected_member_versions")
            == resolution["expected_member_versions"]
            or (
                payload.get("expected_member_versions") is None
                and payload.get("expected_member_versions_sha256")
                == member_versions_sha256
            )
        )
        if (
            payload.get("allowed") is not True
            or snapshot.blockers
            or payload.get("impact_sha256") != snapshot.impact_sha256
            or payload.get("expected_resolution_sha256") != resolution_sha256
            or payload.get("expected_current_leader_assignment_id")
            != resolution["expected_current_leader_assignment_id"]
            or payload.get("expected_space_row_version")
            != resolution["expected_space_row_version"]
            or payload.get("space_state") != resolution["space_state"]
            or not member_versions_match
        ):
            raise OrganizationPreviewError(
                "Organization impact changed after the preview."
            )
        return snapshot

    def preview_change(
        self, command: OrgChangePreviewCommand
    ) -> OrgChangePreviewResult:
        _safe_request_meta(command.request_meta)
        connection = self._factory.open()
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                actor = self._active_admin(connection, command.actor_user_id)
                self._require_actor_epoch(actor, command.actor_scope_epoch)
                now_ms = db_now_ms(connection)
                snapshot = self._build_change_snapshot(
                    connection,
                    unit_id=command.unit_id,
                    expected_row_version=command.expected_row_version,
                    change=command.change,
                    new_parent_id=command.new_parent_id,
                )
                token, expires_at_ms = self._issue_change_preview_token(
                    actor=actor,
                    command=command,
                    snapshot=snapshot,
                    now_ms=now_ms,
                )
                resolution_facts = self._preview_resolution_facts(snapshot)
                current_member_count = sum(
                    1
                    for membership, _user in snapshot.members
                    if membership.part_id == snapshot.unit.id
                )
                direct_space = snapshot.space
                if direct_space is None:
                    space_status = (
                        "not_created"
                        if snapshot.unit.kind == "part"
                        else "future_only"
                    )
                    knowledge_item_count = snapshot.impact.knowledge_item_count
                elif direct_space.status == "frozen":
                    space_status = "frozen"
                    knowledge_item_count = snapshot.impact.knowledge_item_count
                elif direct_space.status == "active" and direct_space.write_enabled:
                    space_status = "active"
                    knowledge_item_count = snapshot.impact.knowledge_item_count
                else:
                    space_status = "write_disabled"
                    knowledge_item_count = snapshot.impact.knowledge_item_count
                return OrgChangePreviewResult(
                    request_id=command.request_id,
                    unit_id=command.unit_id,
                    allowed=not snapshot.blockers,
                    blockers=snapshot.blockers,
                    member_impact={
                        "current_member_count": current_member_count,
                        "descendant_member_count": (
                            len(snapshot.members) - current_member_count
                        ),
                        "member_ids": [
                            membership.user_id
                            for membership, _user in snapshot.members[:200]
                        ],
                        "members": [
                            dict(member)
                            for member in list(
                                resolution_facts["expected_member_versions"]
                            )[:200]
                        ],
                    },
                    leader_impact={
                        "leader_assignment_id": resolution_facts[
                            "expected_current_leader_assignment_id"
                        ],
                        "leader_user_id": (
                            None
                            if snapshot.leader is None
                            else snapshot.leader.user_id
                        ),
                        "will_close": bool(
                            command.change == "inactivate"
                            and snapshot.leader is not None
                        ),
                    },
                    space_impact={
                        "knowledge_item_count": knowledge_item_count,
                        "row_version": resolution_facts[
                            "expected_space_row_version"
                        ],
                        "space_id": (
                            None if direct_space is None else direct_space.space_id
                        ),
                        "space_status": space_status,
                        "write_enabled": bool(
                            direct_space is not None
                            and direct_space.write_enabled
                        ),
                    },
                    preview_token=token,
                    expires_at_ms=expires_at_ms,
                    impact_sha256=snapshot.impact_sha256,
                )
        except (
            OrgRepoUnitNotFoundError,
            OrgRepoUserNotFoundError,
        ) as error:
            raise OrganizationNotFoundError(str(error)) from error
        except OrgRepoUnitVersionConflictError as error:
            raise OrganizationRevisionConflictError(str(error)) from error
        finally:
            connection.close()

    def get_tree(
        self,
        actor_user_id: str,
        actor_scope_epoch: int,
        request_id: str,
    ) -> OrganizationTreeResult:
        connection = self._factory.open()
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                return self._get_tree_from_snapshot(
                    connection,
                    actor_user_id=actor_user_id,
                    actor_scope_epoch=actor_scope_epoch,
                    request_id=request_id,
                )
        finally:
            connection.close()

    def _get_tree_from_snapshot(
        self,
        connection: ManagedConnection,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
        request_id: str,
    ) -> OrganizationTreeResult:
            actor = self._active_admin(connection, actor_user_id)
            self._require_actor_epoch(actor, actor_scope_epoch)
            rows = self._organization.list_subtree(connection)
            memberships = self._organization.list_current_memberships(connection)
            affiliations = self._affiliations.list_current(connection)
            leaders = self._organization.list_active_leaders(connection)
            canonical_users = self._users.list_all(connection)
            membership_by_user: dict[str, MembershipRecord] = {}
            for membership in memberships:
                if membership.user_id in membership_by_user:
                    raise OrganizationStateConflictError(
                        "Canonical current membership state is inconsistent.",
                        details={"state": "current_membership_mismatch"},
                    )
                membership_by_user[membership.user_id] = membership
            affiliation_by_user = {value.user_id: value for value in affiliations}
            if len(affiliation_by_user) != len(affiliations):
                raise OrganizationStateConflictError(
                    "Canonical current affiliation state is inconsistent.",
                    details={"state": "current_affiliation_mismatch"},
                )
            rendered_users: list[CanonicalOrgUser] = []
            for user in canonical_users:
                membership = membership_by_user.pop(user.id, None)
                affiliation = affiliation_by_user.pop(user.id, None)
                membership_part_id = (
                    None if membership is None else membership.part_id
                )
                if (
                    (user.current_part_id is None) != (membership is None)
                    or user.current_part_id != membership_part_id
                    or (
                        user.current_part_id is not None
                        and (
                            affiliation is None
                            or affiliation.id != membership.id
                            or affiliation.home_kind != "part"
                            or affiliation.org_unit_id != user.current_part_id
                        )
                    )
                    or (
                        user.current_part_id is None
                        and affiliation is not None
                        and affiliation.home_kind == "part"
                    )
                    or (
                        user.account_status == "disabled"
                        and (user.current_part_id is not None or affiliation is not None)
                    )
                ):
                    raise OrganizationStateConflictError(
                        "Canonical current membership state is inconsistent.",
                        details={"state": "current_membership_mismatch"},
                    )
                rendered_users.append(
                    CanonicalOrgUser(
                        id=user.id,
                        display_name=user.display_name,
                        system_role=user.system_role,
                        account_status=user.account_status,
                        current_part_id=user.current_part_id,
                        current_membership_id=(
                            None if membership is None else membership.id
                        ),
                        current_affiliation_id=(
                            None if affiliation is None else affiliation.id
                        ),
                        home_org_unit_id=(
                            None if affiliation is None else affiliation.org_unit_id
                        ),
                        home_org_unit_kind=(
                            None if affiliation is None else affiliation.home_kind
                        ),
                        scope_epoch=user.scope_epoch,
                        row_version=user.row_version,
                    )
                )
            if membership_by_user:
                raise OrganizationStateConflictError(
                    "Canonical current membership state is inconsistent.",
                    details={"state": "current_membership_mismatch"},
                )
            if affiliation_by_user:
                raise OrganizationStateConflictError(
                    "Canonical current affiliation state is inconsistent.",
                    details={"state": "current_affiliation_mismatch"},
                )
            member_counts: dict[str, int] = {}
            for membership in memberships:
                member_counts[membership.part_id] = (
                    member_counts.get(membership.part_id, 0) + 1
                )
            leader_by_part = {leader.part_id: leader for leader in leaders}
            children: dict[str, list[str]] = {}
            by_id = {row.unit.id: row.unit for row in rows}
            for row in rows:
                if row.unit.parent_id is not None:
                    children.setdefault(row.unit.parent_id, []).append(row.unit.id)
            render_cache: dict[str, dict[str, object]] = {}

            def render(unit_id: str) -> dict[str, object]:
                cached = render_cache.get(unit_id)
                if cached is not None:
                    return cached
                unit = by_id[unit_id]
                child_ids = children.get(unit_id, [])
                rendered_children = [render(child) for child in child_ids]
                if unit.kind == "part":
                    member_count = member_counts.get(unit.id, 0)
                    leader = leader_by_part.get(unit.id)
                    leader_payload: dict[str, object] | None = None
                    if leader is not None:
                        leader_user = self._users.require(
                            connection, leader.user_id
                        )
                        leader_payload = {
                            "assignment_id": leader.id,
                            "display_name": leader_user.display_name,
                            "user_id": leader.user_id,
                            "valid_from": _utc_iso(leader.valid_from_ms),
                        }
                    space = self._organization.get_part_space_state(
                        connection, unit.id
                    )
                    if space is None:
                        space_status = "not_created"
                    elif space.status == "frozen":
                        space_status = "frozen"
                    elif space.status == "active" and space.write_enabled:
                        space_status = "active"
                    else:
                        space_status = "write_disabled"
                    warnings: list[str] = []
                    if leader is None:
                        warnings.append("leader_missing")
                    if space is None:
                        warnings.append("space_not_created")
                else:
                    member_count = sum(
                        int(rendered["member_count"])
                        for rendered in rendered_children
                    )
                    leader_payload = None
                    space_status = "future_only"
                    warnings = []
                if unit.status == "inactive":
                    warnings.append("unit_inactive")
                payload = {
                    "children": rendered_children,
                    "id": unit.id,
                    "kind": unit.kind,
                    "leader": leader_payload,
                    "member_count": member_count,
                    "name": unit.name,
                    "parent_id": unit.parent_id,
                    "row_version": unit.row_version,
                    "space_status": space_status,
                    "status": unit.status,
                    "warnings": warnings,
                }
                render_cache[unit_id] = payload
                return payload

            return OrganizationTreeResult(
                request_id=request_id,
                root=render(RESPONSIBILITY_ROOT_ID),
                users=tuple(rendered_users),
            )

    def create_unit(
        self, command: CreateOrgUnitCommand
    ) -> OrganizationMutationResult:
        reason = _org_reason(command.reason, required=False)
        if command.kind not in ("team", "part"):
            raise OrganizationValidationError(
                "Only team and part units can be created."
            )
        normalize_org_name(command.name)

        def apply(
            connection: ManagedConnection,
            actor: UserRecord,
            now_ms: int,
            dedup_id: str,
            mark_domain_write_started: Callable[[], None],
        ) -> _OrganizationApplied:
            parent = self._organization.require_unit(connection, command.parent_id)
            expected_parent_kind = (
                "responsibility" if command.kind == "team" else "team"
            )
            if parent.kind != expected_parent_kind or parent.status != "active":
                raise OrganizationStateConflictError(
                    "The requested organization parent is invalid."
                )
            _display_name, name_key = normalize_org_name(command.name)
            siblings = self._organization.list_units(
                connection,
                parent_id=parent.id,
                direct_children_only=True,
            )
            if any(
                sibling.kind == command.kind
                and sibling.status == "active"
                and sibling.name_key == name_key
                for sibling in siblings
            ):
                raise OrganizationStateConflictError(
                    "An active sibling already uses that organization name."
                )
            unit_id = _derived_uuid(_ORG_UNIT_NAMESPACE, dedup_id)
            mark_domain_write_started()
            unit = self._organization.create_unit(
                connection,
                unit_id=unit_id,
                kind=command.kind,
                parent_id=command.parent_id,
                name=command.name,
                actor_user_id=actor.id,
                now_ms=now_ms,
            )
            native_space_id: str | None = None
            if self._flags.higher_spaces:
                try:
                    if unit.kind == "part":
                        provisioned = self._spaces.ensure_part(
                            connection,
                            space_id=unit.id,
                            part_id=unit.id,
                            created_at_ms=now_ms,
                        )
                    else:
                        provisioned = self._spaces.ensure_higher(
                            connection,
                            space_id=unit.id,
                            org_unit_id=unit.id,
                            kind="team",
                            created_at_ms=now_ms,
                        )
                except SpaceRepositoryError as exc:
                    raise OrganizationStateConflictError(
                        "The organization native space could not be provisioned."
                    ) from exc
                native_space_id = provisioned.space.id
            self._hit("organization.unit.after_create")
            return _OrganizationApplied(
                result=OrganizationMutationResult(
                    request_id=command.request_id,
                    operation_id=command.operation_id,
                    status="created",
                    resource_id=unit.id,
                    row_version=unit.row_version,
                ),
                aggregate_type="org_unit",
                aggregate_id=unit.id,
                aggregate_version=unit.row_version,
                event_type="organization.unit.created",
                details={
                    "kind": unit.kind,
                    "name": unit.name,
                    "parent_id": unit.parent_id,
                    "space_id": native_space_id,
                    "space_status": (
                        "write_disabled"
                        if native_space_id is not None
                        else ("not_created" if unit.kind == "part" else "future_only")
                    ),
                },
            )

        return self._run_mutation(
            command,
            operation_type="organization.unit.create",
            reason=reason,
            apply=apply,
        )

    def rename_unit(
        self, command: RenameOrgUnitCommand
    ) -> OrganizationMutationResult:
        _positive_version(
            command.expected_row_version, field_name="expected_row_version"
        )
        reason = _org_reason(command.reason, required=False)
        normalize_org_name(command.name)

        def apply(
            connection: ManagedConnection,
            actor: UserRecord,
            now_ms: int,
            dedup_id: str,
            mark_domain_write_started: Callable[[], None],
        ) -> _OrganizationApplied:
            del dedup_id
            if command.unit_id == RESPONSIBILITY_ROOT_ID:
                raise OrganizationStateConflictError(
                    "The AMHS responsibility root is immutable."
                )
            before = self._organization.require_unit(
                connection, command.unit_id
            )
            if before.row_version != command.expected_row_version:
                raise OrganizationRevisionConflictError(
                    "The organization-unit row version changed before the rename.",
                    details={
                        "current_row_version": before.row_version,
                        "expected_row_version": command.expected_row_version,
                    },
                )
            _display_name, name_key = normalize_org_name(command.name)
            siblings = self._organization.list_units(
                connection,
                parent_id=before.parent_id,
                direct_children_only=True,
            )
            if any(
                sibling.id != before.id
                and sibling.kind == before.kind
                and sibling.status == "active"
                and sibling.name_key == name_key
                for sibling in siblings
            ):
                raise OrganizationStateConflictError(
                    "An active sibling already uses that organization name."
                )
            mark_domain_write_started()
            renamed = self._organization.rename_unit(
                connection,
                unit_id=command.unit_id,
                expected_row_version=command.expected_row_version,
                name=command.name,
                actor_user_id=actor.id,
                updated_at_ms=now_ms,
            )
            changed = renamed.row_version != before.row_version
            return _OrganizationApplied(
                result=OrganizationMutationResult(
                    request_id=command.request_id,
                    operation_id=command.operation_id,
                    status="renamed" if changed else "unchanged",
                    resource_id=renamed.id,
                    row_version=renamed.row_version,
                ),
                aggregate_type="org_unit",
                aggregate_id=renamed.id,
                aggregate_version=renamed.row_version,
                event_type=(
                    "organization.unit.renamed"
                    if changed
                    else "organization.unit.unchanged"
                ),
                details={"name": renamed.name},
            )

        return self._run_mutation(
            command,
            operation_type="organization.unit.rename",
            reason=reason,
            apply=apply,
        )

    def _move_affected_space_ids(
        self,
        connection: ManagedConnection,
        *,
        snapshot: _OrgPreviewSnapshot,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return exact job and aggregate-projection invalidation targets."""

        if snapshot.new_parent is None:  # pragma: no cover - move proof
            raise OrganizationPreviewError("The move destination is unavailable.")
        aggregate_org_ids: set[str] = set()

        def add_ancestor_chain(start_id: str | None) -> None:
            cursor_id = start_id
            visited: set[str] = set()
            while cursor_id is not None:
                if cursor_id in visited or len(visited) >= 8:
                    raise OrganizationStateConflictError(
                        "The organization ancestor chain is invalid."
                    )
                visited.add(cursor_id)
                unit = self._organization.require_unit(connection, cursor_id)
                if unit.kind in ("team", "responsibility"):
                    aggregate_org_ids.add(unit.id)
                cursor_id = unit.parent_id

        add_ancestor_chain(snapshot.unit.parent_id)
        add_ancestor_chain(snapshot.new_parent.id)
        if snapshot.unit.kind in ("team", "responsibility"):
            aggregate_org_ids.add(snapshot.unit.id)

        projection_space_ids: set[str] = set()
        for org_unit_id in sorted(aggregate_org_ids):
            state = self._organization.get_org_unit_space_state(
                connection,
                org_unit_id=org_unit_id,
            )
            if state is not None and state.status == "active":
                projection_space_ids.add(state.space_id)
        job_space_ids = {
            state.space_id
            for state in snapshot.subtree_spaces
            if state.status == "active"
        }
        job_space_ids.update(projection_space_ids)
        return tuple(sorted(job_space_ids)), tuple(sorted(projection_space_ids))

    @staticmethod
    def _invalidate_move_jobs(
        connection: ManagedConnection,
        *,
        space_ids: tuple[str, ...],
        now_ms: int,
    ) -> tuple[int, int]:
        invalidated = 0
        cancel_requested = 0
        for space_id in space_ids:
            row = connection.execute(
                """SELECT count(*) FROM durable_job
                    WHERE space_id=? AND actor_kind='user'
                      AND state IN ('queued','claimed','running','retry')""",
                (space_id,),
            ).fetchone()
            invalidated += 0 if row is None else int(row[0])
            cursor = connection.execute(
                """UPDATE durable_job SET cancel_requested=1,updated_at=?
                    WHERE space_id=? AND actor_kind='user'
                      AND state IN ('queued','claimed','running','retry')
                      AND cancellable=1 AND cancel_requested=0""",
                (now_ms, space_id),
            )
            cancel_requested += max(0, cursor.rowcount)
        return invalidated, cancel_requested

    @staticmethod
    def _invalidate_move_aggregate_vectors(
        connection: ManagedConnection,
        *,
        space_ids: tuple[str, ...],
        now_ms: int,
    ) -> int:
        from ..repositories.aggregate_graph import AggregateGraphRepository

        return len(
            AggregateGraphRepository().invalidate_generation_targets(
                connection,
                selected_space_ids=space_ids,
                now_ms=now_ms,
            )
        )

    def _revoke_move_distributions(
        self,
        connection: ManagedConnection,
        *,
        actor: UserRecord,
        command: MoveOrgUnitCommand,
        snapshot: _OrgPreviewSnapshot,
        moved: OrgUnitRecord,
        dedup_id: str,
        now_ms: int,
    ) -> tuple[str, ...]:
        grants = snapshot.invalid_distributions
        if not grants:
            return ()
        grant_ids = tuple(grant.id for grant in grants)
        cascade_reason = "Organization move invalidated the grant ancestry."
        confirmation_sha256 = sha256(
            (
                "organization-move\x1f"
                + command.operation_id
                + "\x1f"
                + snapshot.impact_sha256
            ).encode("ascii")
        ).hexdigest()
        details = {
            "grant_ids": list(grant_ids),
            "moved_org_unit_id": moved.id,
            "new_parent_id": moved.parent_id,
            "old_parent_id": snapshot.unit.parent_id,
            "preview_impact_sha256": snapshot.impact_sha256,
            "schema_version": 1,
        }
        audit = self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(
                    _AUDIT_NAMESPACE, dedup_id, "distribution-cascade"
                ),
                occurred_at_ms=now_ms,
                request_id=command.request_id,
                operation_id=command.operation_id,
                action="knowledge_distribution.org_move_invalidate",
                outcome="revoked",
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                reason=cascade_reason,
                details=details,
                request_meta=_safe_request_meta(command.request_meta),
            ),
        )
        outbox = self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=(
                    f"organization:{dedup_id}:distribution-invalidation:v1"
                ),
                space_id=None,
                aggregate_type="organization_move_distribution_invalidation",
                aggregate_id=moved.id,
                aggregate_version=moved.row_version,
                event_type="knowledge_distribution.invalidated_by_org_move",
                payload={
                    **details,
                    "audit_event_id": audit.id,
                    "operation_id": command.operation_id,
                },
                available_at_ms=now_ms,
            ),
        )
        for grant in grants:
            effect_key = sha256(
                _canonical_json(
                    {
                        "cause": "organization_move",
                        "grant_id": grant.id,
                        "operation_id": command.operation_id,
                        "schema_version": 1,
                    }
                ).encode("ascii")
            ).hexdigest()
            try:
                self._higher.revoke_distribution(
                    connection,
                    grant_id=grant.id,
                    expected_row_version=grant.row_version,
                    revoked_by_user_id=actor.id,
                    revoke_mode="break_glass",
                    revoke_role_assignment_id=None,
                    revoke_reason=cascade_reason,
                    revoke_confirmation_sha256=confirmation_sha256,
                    revoke_operation_id=command.operation_id,
                    revoke_effect_key=effect_key,
                    revoked_audit_event_id=audit.id,
                    revoked_outbox_event_id=outbox.id,
                    revoked_at_ms=max(now_ms, grant.created_at_ms + 1),
                )
            except DistributionGrantStateConflictError as exc:
                raise OrganizationStateConflictError(
                    "A distribution grant changed during organization move."
                ) from exc
        return grant_ids

    def move_unit(
        self, command: MoveOrgUnitCommand
    ) -> OrganizationMutationResult:
        _positive_version(
            command.expected_row_version, field_name="expected_row_version"
        )
        reason = _org_reason(command.reason, required=False)
        if not isinstance(command.resolution, OrgMoveResolution) or any(
            value is not True
            for value in (
                command.resolution.retain_memberships,
                command.resolution.retain_leader,
                command.resolution.retain_space,
            )
        ):
            raise OrganizationValidationError(
                "A move must explicitly retain memberships, leader, and space."
            )

        def apply(
            connection: ManagedConnection,
            actor: UserRecord,
            now_ms: int,
            dedup_id: str,
            mark_domain_write_started: Callable[[], None],
        ) -> _OrganizationApplied:
            snapshot = self._validate_change_preview_token(
                connection,
                actor=actor,
                unit_id=command.unit_id,
                expected_row_version=command.expected_row_version,
                change="move",
                new_parent_id=command.new_parent_id,
                preview_token=command.preview_token,
                now_ms=now_ms,
            )
            if snapshot.new_parent is None:  # pragma: no cover - validator proves it
                raise OrganizationPreviewError(
                    "The move preview has no destination parent."
                )
            job_space_ids, projection_space_ids = self._move_affected_space_ids(
                connection,
                snapshot=snapshot,
            )
            mark_domain_write_started()
            moved = self._organization.set_unit_parent(
                connection,
                unit_id=snapshot.unit.id,
                expected_row_version=snapshot.unit.row_version,
                parent_id=snapshot.new_parent.id,
                actor_user_id=actor.id,
                updated_at_ms=now_ms,
            )
            self._hit("organization.move.after_parent")
            revoked_distribution_ids = self._revoke_move_distributions(
                connection,
                actor=actor,
                command=command,
                snapshot=snapshot,
                moved=moved,
                dedup_id=dedup_id,
                now_ms=now_ms,
            )
            self._hit("organization.move.after_distribution_invalidation")
            invalidated_projection_count = (
                self._invalidate_move_aggregate_vectors(
                    connection,
                    space_ids=projection_space_ids,
                    now_ms=now_ms,
                )
            )
            invalidated_job_count, cancel_requested_job_count = (
                self._invalidate_move_jobs(
                    connection,
                    space_ids=job_space_ids,
                    now_ms=now_ms,
                )
            )
            self._hit("organization.move.after_closure_invalidation")
            return _OrganizationApplied(
                result=OrganizationMutationResult(
                    request_id=command.request_id,
                    operation_id=command.operation_id,
                    status="moved",
                    resource_id=moved.id,
                    row_version=moved.row_version,
                ),
                aggregate_type="org_unit",
                aggregate_id=moved.id,
                aggregate_version=moved.row_version,
                event_type="organization.unit.moved",
                details={
                    "impact_sha256": snapshot.impact_sha256,
                    "affected_job_space_ids": list(job_space_ids),
                    "affected_projection_space_ids": list(projection_space_ids),
                    "cancel_requested_job_count": cancel_requested_job_count,
                    "invalidated_distribution_count": len(
                        revoked_distribution_ids
                    ),
                    "invalidated_distribution_ids": list(
                        revoked_distribution_ids
                    ),
                    "invalidated_job_count": invalidated_job_count,
                    "invalidated_projection_count": (
                        invalidated_projection_count
                    ),
                    "new_parent_id": moved.parent_id,
                    "old_parent_id": snapshot.unit.parent_id,
                    "retain_leader": True,
                    "retain_memberships": True,
                    "retain_space": True,
                },
            )

        return self._run_mutation(
            command,
            operation_type="organization.unit.move",
            reason=reason,
            apply=apply,
        )

    def inactivate_unit(
        self, command: InactivateOrgUnitCommand
    ) -> OrganizationMutationResult:
        _positive_version(
            command.expected_row_version, field_name="expected_row_version"
        )
        reason = _org_reason(command.reason, required=True)
        assert reason is not None
        resolution = command.resolution
        if (
            not isinstance(resolution, OrgInactivationResolution)
            or resolution.leader_action != "close"
            or resolution.space_action != "freeze_if_present"
            or len(resolution.member_resolutions) > 200
        ):
            raise OrganizationValidationError(
                "The inactivation resolution is invalid."
            )
        if resolution.expected_space_row_version is not None:
            _positive_version(
                resolution.expected_space_row_version,
                field_name="expected_space_row_version",
            )
        for member_resolution in resolution.member_resolutions:
            if isinstance(member_resolution, OrgMemberResolution):
                _positive_version(
                    member_resolution.expected_user_row_version,
                    field_name="expected_user_row_version",
                )

        def apply(
            connection: ManagedConnection,
            actor: UserRecord,
            now_ms: int,
            dedup_id: str,
            mark_domain_write_started: Callable[[], None],
        ) -> _OrganizationApplied:
            snapshot = self._validate_change_preview_token(
                connection,
                actor=actor,
                unit_id=command.unit_id,
                expected_row_version=command.expected_row_version,
                change="inactivate",
                new_parent_id=None,
                preview_token=command.preview_token,
                now_ms=now_ms,
            )
            expected_leader_id = (
                None if snapshot.leader is None else snapshot.leader.id
            )
            if (
                resolution.expected_current_leader_assignment_id
                != expected_leader_id
            ):
                raise OrganizationRevisionConflictError(
                    "The inactivation leader resolution is stale.",
                    details={
                        "current_leader_assignment_id": expected_leader_id,
                        "expected_current_leader_assignment_id": (
                            resolution.expected_current_leader_assignment_id
                        ),
                    },
                )
            expected_space_version = (
                None if snapshot.space is None else snapshot.space.row_version
            )
            if resolution.expected_space_row_version != expected_space_version:
                raise OrganizationRevisionConflictError(
                    "The inactivation space resolution is stale.",
                    details={
                        "current_space_row_version": expected_space_version,
                        "expected_space_row_version": (
                            resolution.expected_space_row_version
                        ),
                    },
                )
            provided: dict[str, OrgMemberResolution] = {}
            for member_resolution in resolution.member_resolutions:
                if not isinstance(member_resolution, OrgMemberResolution):
                    raise OrganizationValidationError(
                        "Every member resolution must use the canonical shape."
                    )
                if member_resolution.user_id in provided:
                    raise OrganizationValidationError(
                        "Every current member must be resolved exactly once."
                    )
                provided[member_resolution.user_id] = member_resolution
            current_by_user = {
                membership.user_id: (membership, user)
                for membership, user in snapshot.members
            }
            if set(provided) != set(current_by_user):
                raise OrganizationStateConflictError(
                    "The inactivation resolution must enumerate every current member exactly."
                )
            destinations: dict[str, OrgUnitRecord | None] = {}
            for user_id, member_resolution in provided.items():
                membership, user = current_by_user[user_id]
                if user.row_version != member_resolution.expected_user_row_version:
                    raise OrganizationRevisionConflictError(
                        "A resolved member row version is stale.",
                        details={
                            "current_row_version": user.row_version,
                            "expected_row_version": (
                                member_resolution.expected_user_row_version
                            ),
                            "user_id": user_id,
                        },
                    )
                if (
                    membership.part_id != snapshot.unit.id
                    or user.current_part_id != snapshot.unit.id
                ):
                    raise OrganizationStateConflictError(
                        "Only direct members of the inactivated part can be resolved."
                    )
                destination: OrgUnitRecord | None = None
                if member_resolution.target_part_id is not None:
                    destination = self._organization.require_unit(
                        connection, member_resolution.target_part_id
                    )
                    if (
                        destination.kind != "part"
                        or destination.status != "active"
                        or destination.id in snapshot.subtree_ids
                    ):
                        raise OrganizationStateConflictError(
                            "A member destination must be an active part outside the inactivated subtree."
                        )
                destinations[user_id] = destination
            changed_at = now_ms
            if snapshot.leader is not None:
                changed_at = max(
                    changed_at, snapshot.leader.valid_from_ms + 1
                )
            for membership, _user in snapshot.members:
                changed_at = max(changed_at, membership.valid_from_ms + 1)
            mark_domain_write_started()
            frozen_space = self._organization.freeze_org_unit_space(
                connection,
                org_unit_id=snapshot.unit.id,
                expected_kind=snapshot.unit.kind,
                expected_row_version=resolution.expected_space_row_version,
                updated_at_ms=changed_at,
            )
            self._hit("organization.inactivate.after_space")
            working_unit = snapshot.unit
            if snapshot.leader is not None:
                self._organization.close_part_leader(
                    connection,
                    part_id=snapshot.unit.id,
                    expected_assignment_id=snapshot.leader.id,
                    closed_at_ms=changed_at,
                )
                working_unit = self._organization.touch_unit(
                    connection,
                    unit_id=snapshot.unit.id,
                    expected_row_version=working_unit.row_version,
                    actor_user_id=actor.id,
                    updated_at_ms=changed_at,
                )
            self._hit("organization.inactivate.after_leader")
            revocations = AuthorityRevocationCounts()
            updated_users: list[UserOrganizationState] = []
            for index, (membership, user) in enumerate(snapshot.members):
                destination = destinations[user.user_id]
                self._organization.close_current_membership(
                    connection,
                    user_id=user.user_id,
                    expected_membership_id=membership.id,
                    closed_at_ms=changed_at,
                )
                if destination is not None:
                    self._organization.open_membership(
                        connection,
                        membership_id=_derived_uuid(
                            _MEMBERSHIP_NAMESPACE,
                            dedup_id,
                            user.user_id,
                        ),
                        user_id=user.user_id,
                        part_id=destination.id,
                        assigned_by=actor.id,
                        reason=reason,
                        valid_from_ms=changed_at,
                        expected_current_membership_id=None,
                    )
                updated = self._organization.compare_and_swap_user_part(
                    connection,
                    user_id=user.user_id,
                    expected_current_part_id=snapshot.unit.id,
                    target_part_id=(
                        None if destination is None else destination.id
                    ),
                    expected_scope_epoch=user.scope_epoch,
                    expected_row_version=user.row_version,
                    updated_at_ms=changed_at,
                )
                updated_users.append(updated)
                revocations = revocations.combine(
                    self._organization.revoke_user_part_leases(
                        connection,
                        user_id=user.user_id,
                        part_id=snapshot.unit.id,
                    )
                )
                revocations = revocations.combine(
                    self._organization.invalidate_user_jobs_for_scope_epoch(
                        connection,
                        user_id=user.user_id,
                        invalidated_scope_epoch=user.scope_epoch,
                        updated_at_ms=changed_at,
                    )
                )
                self._hit("organization.inactivate.after_member")
                self._hit(f"organization.inactivate.after_member.{index}")
            inactive = self._organization.set_unit_status(
                connection,
                unit_id=working_unit.id,
                expected_row_version=working_unit.row_version,
                status="inactive",
                actor_user_id=actor.id,
                updated_at_ms=changed_at,
            )
            self._hit("organization.inactivate.after_status")
            if self._organization.list_current_memberships(
                connection, part_id=snapshot.unit.id
            ):
                raise OrganizationStateConflictError(
                    "Current memberships remain after part inactivation."
                )
            if (
                snapshot.unit.kind == "part"
                and self._organization.get_active_part_leader(
                    connection, snapshot.unit.id
                )
                is not None
            ):
                raise OrganizationStateConflictError(
                    "An active leader remains after part inactivation."
                )
            return _OrganizationApplied(
                result=OrganizationMutationResult(
                    request_id=command.request_id,
                    operation_id=command.operation_id,
                    status="inactivated",
                    resource_id=inactive.id,
                    row_version=inactive.row_version,
                ),
                aggregate_type="org_unit",
                aggregate_id=inactive.id,
                aggregate_version=inactive.row_version,
                event_type="organization.unit.inactivated",
                details={
                    "cancel_requested_job_count": (
                        revocations.cancel_requested_job_count
                    ),
                    "closed_leader_assignment_id": expected_leader_id,
                    "frozen_space_id": (
                        None if frozen_space is None else frozen_space.space_id
                    ),
                    "impact_sha256": snapshot.impact_sha256,
                    "invalidated_job_count": revocations.invalidated_job_count,
                    "member_resolutions": [
                        {
                            "new_part_id": updated.current_part_id,
                            "scope_epoch": updated.scope_epoch,
                            "user_id": updated.user_id,
                            "user_row_version": updated.row_version,
                        }
                        for updated in updated_users
                    ],
                    "revoked_lease_count": revocations.revoked_lease_count,
                    "space_state": (
                        "not_created"
                        if frozen_space is None
                        else frozen_space.status
                    ),
                },
            )

        return self._run_mutation(
            command,
            operation_type="organization.unit.inactivate",
            reason=reason,
            apply=apply,
        )

    def set_leader(
        self, command: SetLeaderCommand
    ) -> OrganizationMutationResult:
        _positive_version(
            command.expected_part_row_version,
            field_name="expected_part_row_version",
        )
        reason = _org_reason(command.reason, required=True)
        assert reason is not None

        def apply(
            connection: ManagedConnection,
            actor: UserRecord,
            now_ms: int,
            dedup_id: str,
            mark_domain_write_started: Callable[[], None],
        ) -> _OrganizationApplied:
            part = self._organization.require_unit(connection, command.part_id)
            if part.kind != "part" or part.status != "active":
                raise OrganizationStateConflictError(
                    "A leader can be assigned only to an active part."
                )
            if part.row_version != command.expected_part_row_version:
                raise OrganizationRevisionConflictError(
                    "The part aggregate changed before the leader command.",
                    details={
                        "current_row_version": part.row_version,
                        "expected_row_version": command.expected_part_row_version,
                    },
                )
            current = self._organization.get_active_part_leader(
                connection, part.id
            )
            actual_assignment_id = None if current is None else current.id
            if actual_assignment_id != command.expected_current_leader_assignment_id:
                raise OrganizationRevisionConflictError(
                    "The active leader assignment changed before the command.",
                    details={
                        "current_leader_assignment_id": actual_assignment_id,
                        "expected_current_leader_assignment_id": (
                            command.expected_current_leader_assignment_id
                        ),
                    },
                )
            leader_user = self._organization.get_user_organization_state(
                connection, command.leader_user_id
            )
            if (
                leader_user is None
                or leader_user.account_status != "active"
                or leader_user.current_part_id != part.id
                or leader_user.current_membership_id is None
                or leader_user.membership_part_id != part.id
            ):
                raise OrganizationStateConflictError(
                    "The part leader must be an active current member of that part."
                )
            if current is not None and current.user_id == command.leader_user_id:
                return _OrganizationApplied(
                    result=OrganizationMutationResult(
                        request_id=command.request_id,
                        operation_id=command.operation_id,
                        status="unchanged",
                        resource_id=current.id,
                        row_version=part.row_version,
                    ),
                    aggregate_type="org_unit",
                    aggregate_id=part.id,
                    aggregate_version=part.row_version,
                    event_type="organization.leader.unchanged",
                    details={"leader_user_id": current.user_id},
                )
            mark_domain_write_started()
            changed_at = now_ms
            if current is not None:
                changed_at = max(changed_at, current.valid_from_ms + 1)
            space = self._organization.get_part_space_state(connection, part.id)
            restore_writes = bool(space is not None and space.write_enabled)
            if restore_writes and space is not None:
                space = self._organization.set_part_space_state(
                    connection,
                    part_id=part.id,
                    expected_row_version=space.row_version,
                    status=space.status,
                    write_enabled=False,
                    updated_at_ms=changed_at,
                )
            if current is not None:
                self._organization.close_part_leader(
                    connection,
                    part_id=part.id,
                    expected_assignment_id=current.id,
                    closed_at_ms=changed_at,
                )
                self._hit("organization.leader.after_close")
            assignment = self._organization.open_part_leader(
                connection,
                assignment_id=_derived_uuid(_ORG_ROLE_NAMESPACE, dedup_id),
                user_id=command.leader_user_id,
                part_id=part.id,
                assigned_by=actor.id,
                reason=reason,
                valid_from_ms=changed_at,
                expected_current_assignment_id=None,
            )
            self._hit("organization.leader.after_open")
            touched = self._organization.touch_unit(
                connection,
                unit_id=part.id,
                expected_row_version=part.row_version,
                actor_user_id=actor.id,
                updated_at_ms=changed_at,
            )
            if restore_writes and space is not None:
                space = self._organization.set_part_space_state(
                    connection,
                    part_id=part.id,
                    expected_row_version=space.row_version,
                    status=space.status,
                    write_enabled=True,
                    updated_at_ms=changed_at,
                )
            return _OrganizationApplied(
                result=OrganizationMutationResult(
                    request_id=command.request_id,
                    operation_id=command.operation_id,
                    status="assigned" if current is None else "replaced",
                    resource_id=assignment.id,
                    row_version=touched.row_version,
                ),
                aggregate_type="org_unit",
                aggregate_id=part.id,
                aggregate_version=touched.row_version,
                event_type="organization.leader.changed",
                details={
                    "leader_assignment_id": assignment.id,
                    "leader_user_id": assignment.user_id,
                    "previous_leader_user_id": (
                        None if current is None else current.user_id
                    ),
                },
            )

        return self._run_mutation(
            command,
            operation_type="organization.leader.set",
            reason=reason,
            apply=apply,
        )

    def set_membership(
        self, command: SetMembershipCommand
    ) -> OrganizationMutationResult:
        _positive_version(
            command.expected_user_row_version,
            field_name="expected_user_row_version",
        )
        if command.expected_part_row_version is not None:
            _positive_version(
                command.expected_part_row_version,
                field_name="expected_part_row_version",
            )
        reason = _org_reason(command.reason, required=True)
        assert reason is not None

        def apply(
            connection: ManagedConnection,
            actor: UserRecord,
            now_ms: int,
            dedup_id: str,
            mark_domain_write_started: Callable[[], None],
        ) -> _OrganizationApplied:
            target = self._organization.get_user_organization_state(
                connection, command.target_user_id
            )
            if target is None:
                raise OrganizationNotFoundError(
                    "The canonical user does not exist."
                )
            if target.row_version != command.expected_user_row_version:
                raise OrganizationRevisionConflictError(
                    "The user's organization state changed before the command.",
                    details={
                        "current_row_version": target.row_version,
                        "expected_row_version": command.expected_user_row_version,
                    },
                )
            if target.account_status != "active":
                raise OrganizationStateConflictError(
                    "A disabled account cannot receive organization membership."
                )
            membership = self._organization.get_current_membership(
                connection, target.user_id
            )
            affiliation = self._affiliations.get_current(
                connection, target.user_id
            )
            if target.current_part_id is None:
                if membership is not None:
                    raise OrganizationStateConflictError(
                        "The user's current part and membership disagree."
                    )
                if affiliation is not None:
                    raise OrganizationStateConflictError(
                        "End the user's direct Team or Responsibility role before assigning a Part."
                    )
            elif membership is None or membership.part_id != target.current_part_id:
                raise OrganizationStateConflictError(
                    "The user's current part and membership disagree."
                )
            elif (
                affiliation is None
                or affiliation.id != membership.id
                or affiliation.home_kind != "part"
                or affiliation.org_unit_id != target.current_part_id
            ):
                raise OrganizationStateConflictError(
                    "The user's Part affiliation and membership disagree."
                )
            destination: OrgUnitRecord | None = None
            if command.part_id is not None:
                destination = self._organization.require_unit(
                    connection, command.part_id
                )
                if destination.kind != "part" or destination.status != "active":
                    raise OrganizationStateConflictError(
                        "Membership can target only an active part."
                    )
            current_leader = (
                None
                if target.current_part_id is None
                else self._organization.get_active_part_leader(
                    connection, target.current_part_id
                )
            )
            actual_leader_assignment_id = (
                None if current_leader is None else current_leader.id
            )
            if (
                actual_leader_assignment_id
                != command.expected_current_leader_assignment_id
            ):
                raise OrganizationRevisionConflictError(
                    "The current part leader assignment changed before the command.",
                    details={
                        "current_leader_assignment_id": actual_leader_assignment_id,
                        "expected_current_leader_assignment_id": (
                            command.expected_current_leader_assignment_id
                        ),
                    },
                )
            if command.part_id == target.current_part_id:
                if command.replacement_leader_user_id is not None:
                    raise OrganizationStateConflictError(
                        "Leader replacement requires a real membership move."
                    )
                return _OrganizationApplied(
                    result=OrganizationMutationResult(
                        request_id=command.request_id,
                        operation_id=command.operation_id,
                        status="unchanged",
                        resource_id=(None if membership is None else membership.id),
                        row_version=target.row_version,
                    ),
                    aggregate_type="app_user",
                    aggregate_id=target.user_id,
                    aggregate_version=target.row_version,
                    event_type="organization.membership.unchanged",
                    details={"part_id": target.current_part_id},
                )
            leader_owned = bool(
                current_leader is not None
                and current_leader.user_id == target.user_id
            )
            if not leader_owned and command.replacement_leader_user_id is not None:
                raise OrganizationStateConflictError(
                    "Only a moving current leader can have a replacement."
                )
            current_part: OrgUnitRecord | None = None
            if target.current_part_id is not None:
                current_part = self._organization.require_unit(
                    connection, target.current_part_id
                )
                if command.expected_part_row_version is not None and (
                    current_part.row_version != command.expected_part_row_version
                ):
                    raise OrganizationRevisionConflictError(
                        "The current part aggregate changed before the command.",
                        details={
                            "current_row_version": current_part.row_version,
                            "expected_row_version": command.expected_part_row_version,
                        },
                    )
            if leader_owned and command.expected_part_row_version is None:
                raise OrganizationRevisionConflictError(
                    "Moving the current leader requires expected_part_row_version."
                )
            replacement: UserOrganizationState | None = None
            if command.replacement_leader_user_id is not None:
                if command.replacement_leader_user_id == target.user_id:
                    raise OrganizationStateConflictError(
                        "The moving leader cannot replace themselves."
                    )
                replacement = self._organization.get_user_organization_state(
                    connection, command.replacement_leader_user_id
                )
                if (
                    replacement is None
                    or replacement.account_status != "active"
                    or replacement.current_part_id != target.current_part_id
                    or replacement.current_membership_id is None
                    or replacement.membership_part_id != target.current_part_id
                ):
                    raise OrganizationStateConflictError(
                        "The replacement leader must be an active member of the old part."
                    )
            mark_domain_write_started()
            changed_at = now_ms
            if membership is not None:
                changed_at = max(changed_at, membership.valid_from_ms + 1)
            if current_leader is not None:
                changed_at = max(changed_at, current_leader.valid_from_ms + 1)
            part_space: PartSpaceState | None = None
            prior_write_enabled = False
            replacement_assignment: LeaderAssignmentRecord | None = None
            touched_part: OrgUnitRecord | None = None
            if leader_owned:
                assert current_part is not None and current_leader is not None
                part_space = self._organization.get_part_space_state(
                    connection, current_part.id
                )
                prior_write_enabled = bool(
                    part_space is not None and part_space.write_enabled
                )
                if prior_write_enabled and part_space is not None:
                    part_space = self._organization.set_part_space_state(
                        connection,
                        part_id=current_part.id,
                        expected_row_version=part_space.row_version,
                        status=part_space.status,
                        write_enabled=False,
                        updated_at_ms=changed_at,
                    )
                self._organization.close_part_leader(
                    connection,
                    part_id=current_part.id,
                    expected_assignment_id=current_leader.id,
                    closed_at_ms=changed_at,
                )
                if replacement is not None:
                    replacement_assignment = self._organization.open_part_leader(
                        connection,
                        assignment_id=_derived_uuid(
                            _ORG_ROLE_NAMESPACE, dedup_id, "replacement"
                        ),
                        user_id=replacement.user_id,
                        part_id=current_part.id,
                        assigned_by=actor.id,
                        reason=reason,
                        valid_from_ms=changed_at,
                        expected_current_assignment_id=None,
                    )
                touched_part = self._organization.touch_unit(
                    connection,
                    unit_id=current_part.id,
                    expected_row_version=current_part.row_version,
                    actor_user_id=actor.id,
                    updated_at_ms=changed_at,
                )
                if (
                    replacement_assignment is not None
                    and prior_write_enabled
                    and part_space is not None
                ):
                    part_space = self._organization.set_part_space_state(
                        connection,
                        part_id=current_part.id,
                        expected_row_version=part_space.row_version,
                        status=part_space.status,
                        write_enabled=True,
                        updated_at_ms=changed_at,
                    )
            if membership is not None:
                self._organization.close_current_membership(
                    connection,
                    user_id=target.user_id,
                    expected_membership_id=membership.id,
                    closed_at_ms=changed_at,
                )
            self._hit("membership.after_old_close")
            new_membership: MembershipRecord | None = None
            if destination is not None:
                new_membership = self._organization.open_membership(
                    connection,
                    membership_id=_derived_uuid(
                        _MEMBERSHIP_NAMESPACE, dedup_id
                    ),
                    user_id=target.user_id,
                    part_id=destination.id,
                    assigned_by=actor.id,
                    reason=reason,
                    valid_from_ms=changed_at,
                    expected_current_membership_id=None,
                )
            self._hit("membership.after_new_insert")
            updated = self._organization.compare_and_swap_user_part(
                connection,
                user_id=target.user_id,
                expected_current_part_id=target.current_part_id,
                target_part_id=(None if destination is None else destination.id),
                expected_scope_epoch=target.scope_epoch,
                expected_row_version=target.row_version,
                updated_at_ms=changed_at,
            )
            self._hit("membership.after_scope_epoch")
            revocations = AuthorityRevocationCounts()
            if target.current_part_id is not None:
                revocations = revocations.combine(
                    self._organization.revoke_user_part_leases(
                        connection,
                        user_id=target.user_id,
                        part_id=target.current_part_id,
                    )
                )
            revocations = revocations.combine(
                self._organization.invalidate_user_jobs_for_scope_epoch(
                    connection,
                    user_id=target.user_id,
                    invalidated_scope_epoch=target.scope_epoch,
                    updated_at_ms=changed_at,
                )
            )
            self._hit("membership.after_lease_revoke")
            final_membership = self._organization.get_current_membership(
                connection, target.user_id
            )
            if destination is None:
                if (
                    updated.current_part_id is not None
                    or final_membership is not None
                ):
                    raise OrganizationStateConflictError(
                        "The unassigned membership postcondition failed."
                    )
                status = "unassigned"
            else:
                if (
                    updated.current_part_id != destination.id
                    or final_membership is None
                    or final_membership.part_id != destination.id
                ):
                    raise OrganizationStateConflictError(
                        "The assigned membership postcondition failed."
                    )
                status = "assigned" if target.current_part_id is None else "moved"
            return _OrganizationApplied(
                result=OrganizationMutationResult(
                    request_id=command.request_id,
                    operation_id=command.operation_id,
                    status=status,
                    resource_id=(
                        None if new_membership is None else new_membership.id
                    ),
                    row_version=updated.row_version,
                ),
                aggregate_type="app_user",
                aggregate_id=updated.user_id,
                aggregate_version=updated.row_version,
                event_type=f"organization.membership.{status}",
                details={
                    "cancel_requested_job_count": (
                        revocations.cancel_requested_job_count
                    ),
                    "invalidated_job_count": revocations.invalidated_job_count,
                    "new_part_id": updated.current_part_id,
                    "old_part_id": target.current_part_id,
                    "part_row_version": (
                        None if touched_part is None else touched_part.row_version
                    ),
                    "replacement_leader_assignment_id": (
                        None
                        if replacement_assignment is None
                        else replacement_assignment.id
                    ),
                    "revoked_lease_count": revocations.revoked_lease_count,
                    "scope_epoch": updated.scope_epoch,
                    "target_user_id": updated.user_id,
                },
            )

        return self._run_mutation(
            command,
            operation_type="organization.membership.set",
            reason=reason,
            apply=apply,
        )


__all__ = [
    "AccountAuthorizationError",
    "AccountFailpointTriggered",
    "AccountIdempotencyError",
    "AccountLifecycleCommand",
    "AccountLifecycleResult",
    "AccountOperationInProgressError",
    "AccountScopeStaleError",
    "AccountService",
    "AccountServiceError",
    "AccountStateConflictError",
    "CanonicalOrgUser",
    "CreateOrgUnitCommand",
    "InactivateOrgUnitCommand",
    "MoveOrgUnitCommand",
    "OrgChangePreviewCommand",
    "OrgChangePreviewResult",
    "OrgInactivationResolution",
    "OrgMemberResolution",
    "OrgMoveResolution",
    "OrganizationAuthorizationError",
    "OrganizationFailpointTriggered",
    "OrganizationIdempotencyError",
    "OrganizationMutationResult",
    "OrganizationNotFoundError",
    "OrganizationOperationInProgressError",
    "OrganizationPreviewError",
    "OrganizationRevisionConflictError",
    "OrganizationScopeStaleError",
    "OrganizationService",
    "OrganizationServiceError",
    "OrganizationStateConflictError",
    "OrganizationTreeResult",
    "OrganizationValidationError",
    "RenameOrgUnitCommand",
    "SetLeaderCommand",
    "SetMembershipCommand",
]
