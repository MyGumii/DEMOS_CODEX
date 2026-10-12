"""Connection-scoped v9 higher-space schema primitives.

Authorization, transaction ownership, audit/outbox construction, organization
mutation choreography, and routes belong to SBE-2.3.  This module only maps the
immutable storage DTOs and supplies bounded SQL primitives for that service.
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Literal

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError


HigherScopeRole = Literal["team_leader", "responsibility_executive"]
DistributionTargetKind = Literal["org_unit", "native_space"]
DistributionRevokeMode = Literal["ordinary", "break_glass"]

HIGHER_ROLE_HISTORY_MAX_PAGE_SIZE = 100
DISTRIBUTION_HISTORY_MAX_PAGE_SIZE = 100
DISTRIBUTION_CLOSURE_MAX_SIZE = 4096
ORG_MOVE_INVALIDATION_MAX_SIZE = 200


class HigherSpaceRepositoryError(KnowledgeCoreDatabaseError):
    code = "HIGHER_SPACE_REPOSITORY_ERROR"


class HigherRoleNotFoundError(HigherSpaceRepositoryError):
    code = "HIGHER_ROLE_NOT_FOUND"


class HigherRoleStateConflictError(HigherSpaceRepositoryError):
    code = "HIGHER_ROLE_STATE_CONFLICT"


class DistributionGrantNotFoundError(HigherSpaceRepositoryError):
    code = "DISTRIBUTION_GRANT_NOT_FOUND"


class DistributionGrantStateConflictError(HigherSpaceRepositoryError):
    code = "DISTRIBUTION_GRANT_STATE_CONFLICT"


@dataclass(frozen=True, slots=True)
class HigherScopeRoleAssignmentRecord:
    id: str
    org_unit_id: str
    user_id: str
    role: HigherScopeRole
    assigned_by_user_id: str
    assigned_at_ms: int
    ended_at_ms: int | None
    end_reason: str | None
    assignment_generation: int
    created_at_ms: int
    updated_at_ms: int
    row_version: int


@dataclass(frozen=True, slots=True)
class KnowledgeDistributionGrantRecord:
    id: str
    source_space_id: str
    source_item_id: str
    source_revision_id: str
    target_org_unit_id: str
    target_scope_kind: DistributionTargetKind
    target_scope_id: str
    grant_mode: Literal["read_only"]
    source_role_assignment_id: str
    created_by_user_id: str
    operation_id: str
    effect_key: str
    reason: str
    created_audit_event_id: int
    created_outbox_event_id: int
    created_at_ms: int
    revoked_at_ms: int | None
    revoked_by_user_id: str | None
    revoke_mode: DistributionRevokeMode | None
    revoke_role_assignment_id: str | None
    revoke_reason: str | None
    revoke_confirmation_sha256: str | None
    revoke_operation_id: str | None
    revoke_effect_key: str | None
    revoked_audit_event_id: int | None
    revoked_outbox_event_id: int | None
    updated_at_ms: int
    row_version: int


@dataclass(frozen=True, slots=True)
class HigherAuthorityInvalidationCounts:
    revoked_lease_count: int = 0
    invalidated_job_count: int = 0
    cancel_requested_job_count: int = 0


_ROLE_COLUMNS = """id,org_unit_id,user_id,role,assigned_by_user_id,
assigned_at,ended_at,end_reason,assignment_generation,created_at,updated_at,
row_version"""

_DISTRIBUTION_COLUMNS = """id,source_space_id,source_item_id,
source_revision_id,target_org_unit_id,target_scope_kind,target_scope_id,
grant_mode,source_role_assignment_id,created_by_user_id,operation_id,effect_key,
reason,created_audit_event_id,created_outbox_event_id,created_at,revoked_at,
revoked_by_user_id,revoke_mode,revoke_role_assignment_id,revoke_reason,
revoke_confirmation_sha256,revoke_operation_id,revoke_effect_key,
revoked_audit_event_id,revoked_outbox_event_id,updated_at,row_version"""


def _connection(value: object) -> ManagedConnection:
    if not isinstance(value, ManagedConnection):
        raise TypeError("HigherSpaceRepository requires a ManagedConnection.")
    return value


def _write_connection(value: object) -> ManagedConnection:
    connection = _connection(value)
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Higher-space writes require an existing managed transaction."
        )
    return connection


def _uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    parts = value.split("-")
    if (
        tuple(len(part) for part in parts) != (8, 4, 4, 4, 12)
        or value != value.lower()
        or any(character not in "0123456789abcdef-" for character in value)
    ):
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _user_id(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 64
        or value != value.strip().lower()
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789._-"
            for character in value
        )
    ):
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


def _bounded_text(value: object, *, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > maximum
    ):
        raise ValueError(f"{field} must be bounded trimmed text.")
    return value


def _sha256(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 value.")
    return value


def _value(row: object, key: str) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise HigherSpaceRepositoryError(
            "Canonical higher-space row mapping failed."
        ) from exc


def _role_record(row: object) -> HigherScopeRoleAssignmentRecord:
    try:
        role = _value(row, "role")
        if role not in ("team_leader", "responsibility_executive"):
            raise ValueError("invalid role")
        assigned_at = _non_negative(_value(row, "assigned_at"), field="assigned_at")
        ended_value = _value(row, "ended_at")
        ended_at = (
            None
            if ended_value is None
            else _non_negative(ended_value, field="ended_at")
        )
        end_reason_value = _value(row, "end_reason")
        end_reason = (
            None
            if end_reason_value is None
            else _bounded_text(end_reason_value, field="end_reason", maximum=1000)
        )
        created_at = _non_negative(_value(row, "created_at"), field="created_at")
        updated_at = _non_negative(_value(row, "updated_at"), field="updated_at")
        row_version = _positive(_value(row, "row_version"), field="row_version")
        if created_at != assigned_at or updated_at < created_at:
            raise ValueError("invalid role timestamps")
        if ended_at is None:
            if end_reason is not None or row_version != 1 or updated_at != assigned_at:
                raise ValueError("invalid open role tuple")
        elif (
            ended_at <= assigned_at
            or end_reason is None
            or row_version != 2
            or updated_at != ended_at
        ):
            raise ValueError("invalid closed role tuple")
        return HigherScopeRoleAssignmentRecord(
            id=_uuid(_value(row, "id"), field="id"),
            org_unit_id=_uuid(_value(row, "org_unit_id"), field="org_unit_id"),
            user_id=_user_id(_value(row, "user_id"), field="user_id"),
            role=role,  # type: ignore[arg-type]
            assigned_by_user_id=_user_id(
                _value(row, "assigned_by_user_id"), field="assigned_by_user_id"
            ),
            assigned_at_ms=assigned_at,
            ended_at_ms=ended_at,
            end_reason=end_reason,
            assignment_generation=_positive(
                _value(row, "assignment_generation"),
                field="assignment_generation",
            ),
            created_at_ms=created_at,
            updated_at_ms=updated_at,
            row_version=row_version,
        )
    except (TypeError, ValueError) as exc:
        raise HigherSpaceRepositoryError(
            "Canonical higher-role row is invalid."
        ) from exc


def _distribution_record(row: object) -> KnowledgeDistributionGrantRecord:
    try:
        target_kind = _value(row, "target_scope_kind")
        if target_kind not in ("org_unit", "native_space"):
            raise ValueError("invalid target kind")
        grant_mode = _value(row, "grant_mode")
        if grant_mode != "read_only":
            raise ValueError("invalid grant mode")
        revoke_mode = _value(row, "revoke_mode")
        if revoke_mode not in (None, "ordinary", "break_glass"):
            raise ValueError("invalid revoke mode")
        created_at = _non_negative(_value(row, "created_at"), field="created_at")
        revoked_raw = _value(row, "revoked_at")
        revoked_at = (
            None
            if revoked_raw is None
            else _non_negative(revoked_raw, field="revoked_at")
        )
        updated_at = _non_negative(_value(row, "updated_at"), field="updated_at")
        row_version = _positive(_value(row, "row_version"), field="row_version")
        if revoked_at is None:
            if revoke_mode is not None or row_version != 1 or updated_at != created_at:
                raise ValueError("invalid active grant tuple")
        elif revoked_at <= created_at or row_version != 2 or updated_at != revoked_at:
            raise ValueError("invalid revoked grant tuple")

        def optional_uuid(field: str) -> str | None:
            raw = _value(row, field)
            return None if raw is None else _uuid(raw, field=field)

        def optional_user(field: str) -> str | None:
            raw = _value(row, field)
            return None if raw is None else _user_id(raw, field=field)

        def optional_sha(field: str) -> str | None:
            raw = _value(row, field)
            return None if raw is None else _sha256(raw, field=field)

        def optional_text(field: str) -> str | None:
            raw = _value(row, field)
            return (
                None
                if raw is None
                else _bounded_text(raw, field=field, maximum=1000)
            )

        created_audit = _positive(
            _value(row, "created_audit_event_id"), field="created_audit_event_id"
        )
        revoked_audit_raw = _value(row, "revoked_audit_event_id")
        revoked_audit = (
            None
            if revoked_audit_raw is None
            else _positive(revoked_audit_raw, field="revoked_audit_event_id")
        )
        created_outbox = _positive(
            _value(row, "created_outbox_event_id"),
            field="created_outbox_event_id",
        )
        revoked_outbox_raw = _value(row, "revoked_outbox_event_id")
        revoked_outbox = (
            None
            if revoked_outbox_raw is None
            else _positive(revoked_outbox_raw, field="revoked_outbox_event_id")
        )
        return KnowledgeDistributionGrantRecord(
            id=_uuid(_value(row, "id"), field="id"),
            source_space_id=_uuid(
                _value(row, "source_space_id"), field="source_space_id"
            ),
            source_item_id=_uuid(
                _value(row, "source_item_id"), field="source_item_id"
            ),
            source_revision_id=_uuid(
                _value(row, "source_revision_id"), field="source_revision_id"
            ),
            target_org_unit_id=_uuid(
                _value(row, "target_org_unit_id"), field="target_org_unit_id"
            ),
            target_scope_kind=target_kind,  # type: ignore[arg-type]
            target_scope_id=_uuid(
                _value(row, "target_scope_id"), field="target_scope_id"
            ),
            grant_mode=grant_mode,  # type: ignore[arg-type]
            source_role_assignment_id=_uuid(
                _value(row, "source_role_assignment_id"),
                field="source_role_assignment_id",
            ),
            created_by_user_id=_user_id(
                _value(row, "created_by_user_id"), field="created_by_user_id"
            ),
            operation_id=_uuid(_value(row, "operation_id"), field="operation_id"),
            effect_key=_sha256(_value(row, "effect_key"), field="effect_key"),
            reason=_bounded_text(_value(row, "reason"), field="reason", maximum=1000),
            created_audit_event_id=created_audit,
            created_outbox_event_id=created_outbox,
            created_at_ms=created_at,
            revoked_at_ms=revoked_at,
            revoked_by_user_id=optional_user("revoked_by_user_id"),
            revoke_mode=revoke_mode,  # type: ignore[arg-type]
            revoke_role_assignment_id=optional_uuid("revoke_role_assignment_id"),
            revoke_reason=optional_text("revoke_reason"),
            revoke_confirmation_sha256=optional_sha("revoke_confirmation_sha256"),
            revoke_operation_id=optional_uuid("revoke_operation_id"),
            revoke_effect_key=optional_sha("revoke_effect_key"),
            revoked_audit_event_id=revoked_audit,
            revoked_outbox_event_id=revoked_outbox,
            updated_at_ms=updated_at,
            row_version=row_version,
        )
    except (TypeError, ValueError) as exc:
        raise HigherSpaceRepositoryError(
            "Canonical distribution-grant row is invalid."
        ) from exc


class HigherSpaceRepository:
    """Bounded primitives consumed later by the SBE-2.3 service."""

    def get_role_assignment(
        self, connection: ManagedConnection, assignment_id: str
    ) -> HigherScopeRoleAssignmentRecord | None:
        connection = _connection(connection)
        assignment_id = _uuid(assignment_id, field="assignment_id")
        row = connection.execute(
            f"SELECT {_ROLE_COLUMNS} FROM higher_scope_role_assignment WHERE id=?",
            (assignment_id,),
        ).fetchone()
        return None if row is None else _role_record(row)

    def get_open_role(
        self,
        connection: ManagedConnection,
        *,
        org_unit_id: str,
        role: HigherScopeRole,
    ) -> HigherScopeRoleAssignmentRecord | None:
        connection = _connection(connection)
        org_unit_id = _uuid(org_unit_id, field="org_unit_id")
        if role not in ("team_leader", "responsibility_executive"):
            raise ValueError("role is invalid.")
        rows = connection.execute(
            f"""SELECT {_ROLE_COLUMNS} FROM higher_scope_role_assignment
                 WHERE org_unit_id=? AND role=? AND ended_at IS NULL""",
            (org_unit_id, role),
        ).fetchall()
        if len(rows) > 1:
            raise HigherSpaceRepositoryError(
                "A higher scope has multiple open role episodes."
            )
        return None if not rows else _role_record(rows[0])

    def list_role_history(
        self,
        connection: ManagedConnection,
        *,
        org_unit_id: str,
        role: HigherScopeRole,
        limit: int = 50,
        before_generation: int | None = None,
    ) -> tuple[HigherScopeRoleAssignmentRecord, ...]:
        connection = _connection(connection)
        org_unit_id = _uuid(org_unit_id, field="org_unit_id")
        if role not in ("team_leader", "responsibility_executive"):
            raise ValueError("role is invalid.")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= HIGHER_ROLE_HISTORY_MAX_PAGE_SIZE
        ):
            raise ValueError("limit is outside the bounded role-history range.")
        if before_generation is not None:
            before_generation = _positive(
                before_generation, field="before_generation"
            )
        rows = connection.execute(
            f"""SELECT {_ROLE_COLUMNS} FROM higher_scope_role_assignment
                 WHERE org_unit_id=? AND role=?
                   AND (? IS NULL OR assignment_generation<?)
                 ORDER BY assignment_generation DESC,id LIMIT ?""",
            (org_unit_id, role, before_generation, before_generation, limit),
        ).fetchall()
        return tuple(_role_record(row) for row in rows)

    def invalidate_native_authority(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        role_assignment_id: str,
        updated_at_ms: int,
    ) -> HigherAuthorityInvalidationCounts:
        """Fence the native space after an old role episode loses authority.

        The v1 durable-job authorization snapshot cannot carry successor-only
        fields.  Every user job for the physical native space is therefore
        conservatively invalidated; commit-time ``AccessPlan`` fencing decides
        whether a non-cancellable claimant may still produce an effect.
        """

        connection = _write_connection(connection)
        space_id = _uuid(space_id, field="space_id")
        role_assignment_id = _uuid(
            role_assignment_id, field="role_assignment_id"
        )
        del role_assignment_id
        updated_at = _non_negative(updated_at_ms, field="updated_at_ms")
        leases = connection.execute(
            """UPDATE item_lease
                  SET holder_user_id=NULL,editor_session_id=NULL,token_hmac=NULL,
                      hmac_key_id=NULL,fencing_generation=fencing_generation+1,
                      base_item_row_version=NULL,base_revision_id=NULL,
                      acquired_at=NULL,heartbeat_at=NULL,expires_at=NULL,
                      absolute_expires_at=NULL
                WHERE holder_user_id IS NOT NULL AND item_id IN (
                  SELECT id FROM knowledge_item WHERE space_id=?
                )""",
            (space_id,),
        )
        row = connection.execute(
            """SELECT count(*) FROM durable_job
                WHERE space_id=? AND actor_kind='user'
                  AND state IN ('queued','claimed','running','retry')""",
            (space_id,),
        ).fetchone()
        invalidated = 0 if row is None else int(row[0])
        jobs = connection.execute(
            """UPDATE durable_job SET cancel_requested=1,updated_at=?
                WHERE space_id=? AND actor_kind='user'
                  AND state IN ('queued','claimed','running','retry')
                  AND cancellable=1 AND cancel_requested=0""",
            (updated_at, space_id),
        )
        return HigherAuthorityInvalidationCounts(
            revoked_lease_count=max(0, leases.rowcount),
            invalidated_job_count=invalidated,
            cancel_requested_job_count=max(0, jobs.rowcount),
        )

    def list_open_roles_blocking_org_move(
        self,
        connection: ManagedConnection,
        *,
        moved_org_unit_id: str,
        limit: int = ORG_MOVE_INVALIDATION_MAX_SIZE + 1,
    ) -> tuple[HigherScopeRoleAssignmentRecord, ...]:
        """Mirror the v9 move guard so preview fails before any domain write."""

        connection = _connection(connection)
        moved_org_unit_id = _uuid(
            moved_org_unit_id, field="moved_org_unit_id"
        )
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= ORG_MOVE_INVALIDATION_MAX_SIZE + 1
        ):
            raise ValueError("limit is outside the organization-move range.")
        rows = connection.execute(
            f"""SELECT {_ROLE_COLUMNS}
                  FROM higher_scope_role_assignment AS role_row
                 WHERE role_row.ended_at IS NULL AND (
                   role_row.org_unit_id=?
                   OR EXISTS (
                     SELECT 1
                       FROM org_unit AS moved
                       JOIN app_user AS assignee
                         ON assignee.id=role_row.user_id
                      WHERE moved.id=? AND moved.kind='part'
                        AND assignee.current_part_id=moved.id
                   )
                   OR EXISTS (
                     SELECT 1
                       FROM org_unit AS moved
                       JOIN org_unit AS member_part
                         ON member_part.parent_id=moved.id
                       JOIN app_user AS assignee
                         ON assignee.current_part_id=member_part.id
                        AND assignee.id=role_row.user_id
                      WHERE moved.id=? AND moved.kind='team'
                   )
                 )
                 ORDER BY role_row.org_unit_id,role_row.role,
                          role_row.assignment_generation,role_row.id
                 LIMIT ?""",
            (
                moved_org_unit_id,
                moved_org_unit_id,
                moved_org_unit_id,
                limit,
            ),
        ).fetchall()
        return tuple(_role_record(row) for row in rows)

    def list_distributions_invalid_after_org_move(
        self,
        connection: ManagedConnection,
        *,
        moved_org_unit_id: str,
        new_parent_org_unit_id: str,
        limit: int = ORG_MOVE_INVALIDATION_MAX_SIZE + 1,
    ) -> tuple[KnowledgeDistributionGrantRecord, ...]:
        """Find exact active overlays whose ancestry would be lost by a move."""

        connection = _connection(connection)
        moved_org_unit_id = _uuid(
            moved_org_unit_id, field="moved_org_unit_id"
        )
        new_parent_org_unit_id = _uuid(
            new_parent_org_unit_id, field="new_parent_org_unit_id"
        )
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= ORG_MOVE_INVALIDATION_MAX_SIZE + 1
        ):
            raise ValueError("limit is outside the organization-move range.")
        rows = connection.execute(
            """WITH RECURSIVE
                 moved_subtree(id) AS (
                   SELECT id FROM org_unit WHERE id=?
                   UNION ALL
                   SELECT child.id FROM org_unit AS child
                   JOIN moved_subtree AS parent ON child.parent_id=parent.id
                 ),
                 candidates(grant_id,target_org_unit_id) AS (
                   SELECT grant_row.id,grant_row.target_org_unit_id
                     FROM knowledge_distribution_grant AS grant_row
                    WHERE grant_row.revoked_at IS NULL
                      AND grant_row.target_org_unit_id IN (
                        SELECT id FROM moved_subtree
                      )
                 ),
                 hypothetical_ancestors(grant_id,id,parent_id,depth) AS (
                   SELECT candidate.grant_id,target.id,
                          CASE WHEN target.id=? THEN ? ELSE target.parent_id END,
                          0
                     FROM candidates AS candidate
                     JOIN org_unit AS target
                       ON target.id=candidate.target_org_unit_id
                   UNION ALL
                   SELECT ancestor.grant_id,parent.id,
                          CASE WHEN parent.id=? THEN ? ELSE parent.parent_id END,
                          ancestor.depth+1
                     FROM hypothetical_ancestors AS ancestor
                     JOIN org_unit AS parent ON parent.id=ancestor.parent_id
                    WHERE ancestor.depth<8
                 )
                 SELECT grant_row.id
                   FROM knowledge_distribution_grant AS grant_row
                   JOIN candidates AS candidate
                     ON candidate.grant_id=grant_row.id
                   JOIN knowledge_space AS source_space
                     ON source_space.id=grant_row.source_space_id
                  WHERE NOT EXISTS (
                    SELECT 1 FROM hypothetical_ancestors AS ancestor
                     WHERE ancestor.grant_id=grant_row.id
                       AND ancestor.id=source_space.owner_org_unit_id
                  )
                  ORDER BY grant_row.id LIMIT ?""",
            (
                moved_org_unit_id,
                moved_org_unit_id,
                new_parent_org_unit_id,
                moved_org_unit_id,
                new_parent_org_unit_id,
                limit,
            ),
        ).fetchall()
        result: list[KnowledgeDistributionGrantRecord] = []
        for row in rows:
            record = self.get_distribution(connection, str(row["id"]))
            if record is None:  # pragma: no cover - same-snapshot query
                raise HigherSpaceRepositoryError(
                    "An organization-move distribution disappeared."
                )
            result.append(record)
        return tuple(result)

    def next_assignment_generation(
        self,
        connection: ManagedConnection,
        *,
        org_unit_id: str,
        role: HigherScopeRole,
    ) -> int:
        connection = _connection(connection)
        org_unit_id = _uuid(org_unit_id, field="org_unit_id")
        if role not in ("team_leader", "responsibility_executive"):
            raise ValueError("role is invalid.")
        row = connection.execute(
            """SELECT coalesce(max(assignment_generation),0)+1
                 FROM higher_scope_role_assignment
                WHERE org_unit_id=? AND role=?""",
            (org_unit_id, role),
        ).fetchone()
        if row is None:
            raise HigherSpaceRepositoryError(
                "The next higher-role generation is unavailable."
            )
        return _positive(row[0], field="assignment_generation")

    def insert_open_role(
        self,
        connection: ManagedConnection,
        *,
        assignment_id: str,
        org_unit_id: str,
        user_id: str,
        role: HigherScopeRole,
        assigned_by_user_id: str,
        assignment_generation: int,
        assigned_at_ms: int,
    ) -> HigherScopeRoleAssignmentRecord:
        connection = _write_connection(connection)
        assignment_id = _uuid(assignment_id, field="assignment_id")
        org_unit_id = _uuid(org_unit_id, field="org_unit_id")
        user_id = _user_id(user_id, field="user_id")
        assigned_by_user_id = _user_id(
            assigned_by_user_id, field="assigned_by_user_id"
        )
        if role not in ("team_leader", "responsibility_executive"):
            raise ValueError("role is invalid.")
        generation = _positive(
            assignment_generation, field="assignment_generation"
        )
        assigned_at = _non_negative(assigned_at_ms, field="assigned_at_ms")
        try:
            connection.execute(
                """INSERT INTO higher_scope_role_assignment(
                     id,org_unit_id,user_id,role,assigned_by_user_id,assigned_at,
                     ended_at,end_reason,assignment_generation,created_at,
                     updated_at,row_version
                   ) VALUES (?,?,?,?,?,?,NULL,NULL,?,?,?,1)""",
                (
                    assignment_id,
                    org_unit_id,
                    user_id,
                    role,
                    assigned_by_user_id,
                    assigned_at,
                    generation,
                    assigned_at,
                    assigned_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise HigherRoleStateConflictError(
                "The higher-role episode could not be opened."
            ) from exc
        record = self.get_role_assignment(connection, assignment_id)
        if record is None:
            raise HigherSpaceRepositoryError(
                "The opened higher-role episode disappeared."
            )
        return record

    def close_role(
        self,
        connection: ManagedConnection,
        *,
        assignment_id: str,
        expected_generation: int,
        expected_row_version: int,
        ended_at_ms: int,
        end_reason: str,
    ) -> HigherScopeRoleAssignmentRecord:
        connection = _write_connection(connection)
        assignment_id = _uuid(assignment_id, field="assignment_id")
        generation = _positive(expected_generation, field="expected_generation")
        row_version = _positive(expected_row_version, field="expected_row_version")
        ended_at = _non_negative(ended_at_ms, field="ended_at_ms")
        reason = _bounded_text(end_reason, field="end_reason", maximum=1000)
        try:
            cursor = connection.execute(
                """UPDATE higher_scope_role_assignment
                      SET ended_at=?,end_reason=?,updated_at=?,row_version=row_version+1
                    WHERE id=? AND assignment_generation=? AND row_version=?
                      AND ended_at IS NULL""",
                (
                    ended_at,
                    reason,
                    ended_at,
                    assignment_id,
                    generation,
                    row_version,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise HigherRoleStateConflictError(
                "The higher-role episode could not be closed."
            ) from exc
        if cursor.rowcount != 1:
            if self.get_role_assignment(connection, assignment_id) is None:
                raise HigherRoleNotFoundError(
                    "The higher-role episode does not exist."
                )
            raise HigherRoleStateConflictError(
                "The higher-role episode changed before close."
            )
        record = self.get_role_assignment(connection, assignment_id)
        if record is None:
            raise HigherSpaceRepositoryError(
                "The closed higher-role episode disappeared."
            )
        return record

    def get_distribution(
        self, connection: ManagedConnection, grant_id: str
    ) -> KnowledgeDistributionGrantRecord | None:
        connection = _connection(connection)
        grant_id = _uuid(grant_id, field="grant_id")
        row = connection.execute(
            f"SELECT {_DISTRIBUTION_COLUMNS} FROM knowledge_distribution_grant WHERE id=?",
            (grant_id,),
        ).fetchone()
        return None if row is None else _distribution_record(row)

    def insert_distribution(
        self,
        connection: ManagedConnection,
        *,
        grant_id: str,
        source_space_id: str,
        source_item_id: str,
        source_revision_id: str,
        target_org_unit_id: str,
        target_scope_kind: DistributionTargetKind,
        target_scope_id: str,
        source_role_assignment_id: str,
        created_by_user_id: str,
        operation_id: str,
        effect_key: str,
        reason: str,
        created_audit_event_id: int,
        created_outbox_event_id: int,
        created_at_ms: int,
    ) -> KnowledgeDistributionGrantRecord:
        connection = _write_connection(connection)
        grant_id = _uuid(grant_id, field="grant_id")
        source_space_id = _uuid(source_space_id, field="source_space_id")
        source_item_id = _uuid(source_item_id, field="source_item_id")
        source_revision_id = _uuid(source_revision_id, field="source_revision_id")
        target_org_unit_id = _uuid(
            target_org_unit_id, field="target_org_unit_id"
        )
        if target_scope_kind not in ("org_unit", "native_space"):
            raise ValueError("target_scope_kind is invalid.")
        target_scope_id = _uuid(target_scope_id, field="target_scope_id")
        source_role_assignment_id = _uuid(
            source_role_assignment_id, field="source_role_assignment_id"
        )
        created_by_user_id = _user_id(
            created_by_user_id, field="created_by_user_id"
        )
        operation_id = _uuid(operation_id, field="operation_id")
        effect_key = _sha256(effect_key, field="effect_key")
        reason = _bounded_text(reason, field="reason", maximum=1000)
        audit_id = _positive(
            created_audit_event_id, field="created_audit_event_id"
        )
        outbox_id = _positive(
            created_outbox_event_id, field="created_outbox_event_id"
        )
        created_at = _non_negative(created_at_ms, field="created_at_ms")
        try:
            connection.execute(
                """INSERT INTO knowledge_distribution_grant(
                     id,source_space_id,source_item_id,source_revision_id,
                     target_org_unit_id,target_scope_kind,target_scope_id,
                     grant_mode,source_role_assignment_id,created_by_user_id,
                     operation_id,effect_key,reason,created_audit_event_id,
                     created_outbox_event_id,created_at,revoked_at,
                     revoked_by_user_id,revoke_mode,revoke_role_assignment_id,
                     revoke_reason,revoke_confirmation_sha256,revoke_operation_id,
                     revoke_effect_key,revoked_audit_event_id,
                     revoked_outbox_event_id,updated_at,row_version
                   ) VALUES (?,?,?,?,?,?,?,'read_only',?,?,?,?,?,?,?, ?,
                             NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,?,1)""",
                (
                    grant_id,
                    source_space_id,
                    source_item_id,
                    source_revision_id,
                    target_org_unit_id,
                    target_scope_kind,
                    target_scope_id,
                    source_role_assignment_id,
                    created_by_user_id,
                    operation_id,
                    effect_key,
                    reason,
                    audit_id,
                    outbox_id,
                    created_at,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DistributionGrantStateConflictError(
                "The distribution grant could not be created."
            ) from exc
        record = self.get_distribution(connection, grant_id)
        if record is None:
            raise HigherSpaceRepositoryError(
                "The created distribution grant disappeared."
            )
        return record

    def revoke_distribution(
        self,
        connection: ManagedConnection,
        *,
        grant_id: str,
        expected_row_version: int,
        revoked_by_user_id: str,
        revoke_mode: DistributionRevokeMode,
        revoke_role_assignment_id: str | None,
        revoke_reason: str,
        revoke_confirmation_sha256: str | None,
        revoke_operation_id: str,
        revoke_effect_key: str,
        revoked_audit_event_id: int,
        revoked_outbox_event_id: int,
        revoked_at_ms: int,
    ) -> KnowledgeDistributionGrantRecord:
        connection = _write_connection(connection)
        grant_id = _uuid(grant_id, field="grant_id")
        row_version = _positive(expected_row_version, field="expected_row_version")
        revoked_by_user_id = _user_id(
            revoked_by_user_id, field="revoked_by_user_id"
        )
        if revoke_mode not in ("ordinary", "break_glass"):
            raise ValueError("revoke_mode is invalid.")
        role_id = (
            None
            if revoke_role_assignment_id is None
            else _uuid(
                revoke_role_assignment_id, field="revoke_role_assignment_id"
            )
        )
        confirmation = (
            None
            if revoke_confirmation_sha256 is None
            else _sha256(
                revoke_confirmation_sha256,
                field="revoke_confirmation_sha256",
            )
        )
        if (revoke_mode == "ordinary") != (role_id is not None):
            raise ValueError("ordinary revoke requires one role assignment.")
        if (revoke_mode == "break_glass") != (confirmation is not None):
            raise ValueError("break-glass revoke requires confirmation evidence.")
        reason = _bounded_text(revoke_reason, field="revoke_reason", maximum=1000)
        operation_id = _uuid(revoke_operation_id, field="revoke_operation_id")
        effect_key = _sha256(revoke_effect_key, field="revoke_effect_key")
        audit_id = _positive(
            revoked_audit_event_id, field="revoked_audit_event_id"
        )
        outbox_id = _positive(
            revoked_outbox_event_id, field="revoked_outbox_event_id"
        )
        revoked_at = _non_negative(revoked_at_ms, field="revoked_at_ms")
        try:
            cursor = connection.execute(
                """UPDATE knowledge_distribution_grant
                      SET revoked_at=?,revoked_by_user_id=?,revoke_mode=?,
                          revoke_role_assignment_id=?,revoke_reason=?,
                          revoke_confirmation_sha256=?,revoke_operation_id=?,
                          revoke_effect_key=?,revoked_audit_event_id=?,
                          revoked_outbox_event_id=?,updated_at=?,
                          row_version=row_version+1
                    WHERE id=? AND revoked_at IS NULL AND row_version=?""",
                (
                    revoked_at,
                    revoked_by_user_id,
                    revoke_mode,
                    role_id,
                    reason,
                    confirmation,
                    operation_id,
                    effect_key,
                    audit_id,
                    outbox_id,
                    revoked_at,
                    grant_id,
                    row_version,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DistributionGrantStateConflictError(
                "The distribution grant could not be revoked."
            ) from exc
        if cursor.rowcount != 1:
            if self.get_distribution(connection, grant_id) is None:
                raise DistributionGrantNotFoundError(
                    "The distribution grant does not exist."
                )
            raise DistributionGrantStateConflictError(
                "The distribution grant changed before revoke."
            )
        record = self.get_distribution(connection, grant_id)
        if record is None:
            raise HigherSpaceRepositoryError(
                "The revoked distribution grant disappeared."
            )
        return record

    def list_distributions(
        self,
        connection: ManagedConnection,
        *,
        source_space_id: str,
        limit: int = 50,
        before_created_at_ms: int | None = None,
        before_id: str | None = None,
    ) -> tuple[KnowledgeDistributionGrantRecord, ...]:
        connection = _connection(connection)
        source_space_id = _uuid(source_space_id, field="source_space_id")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            # The service requests one bounded look-ahead row to determine
            # whether a 100-row public page has a successor cursor.
            or not 1 <= limit <= DISTRIBUTION_HISTORY_MAX_PAGE_SIZE + 1
        ):
            raise ValueError("limit is outside the bounded distribution range.")
        if (before_created_at_ms is None) != (before_id is None):
            raise ValueError("distribution cursor fields must be supplied together.")
        if before_created_at_ms is not None:
            before_created_at_ms = _non_negative(
                before_created_at_ms, field="before_created_at_ms"
            )
            before_id = _uuid(before_id, field="before_id")
        rows = connection.execute(
            f"""SELECT {_DISTRIBUTION_COLUMNS}
                  FROM knowledge_distribution_grant
                 WHERE source_space_id=?
                   AND (? IS NULL OR (created_at,id)<(?,?))
                 ORDER BY created_at DESC,id DESC LIMIT ?""",
            (
                source_space_id,
                before_created_at_ms,
                before_created_at_ms,
                before_id,
                limit,
            ),
        ).fetchall()
        return tuple(_distribution_record(row) for row in rows)

    def list_active_distributions_for_target(
        self,
        connection: ManagedConnection,
        *,
        target_org_unit_id: str,
        target_space_id: str,
        limit: int = DISTRIBUTION_CLOSURE_MAX_SIZE,
    ) -> tuple[KnowledgeDistributionGrantRecord, ...]:
        """Return exact-target overlays in one bounded, origin-preserving query."""

        connection = _connection(connection)
        target_org_unit_id = _uuid(
            target_org_unit_id, field="target_org_unit_id"
        )
        target_space_id = _uuid(target_space_id, field="target_space_id")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= DISTRIBUTION_CLOSURE_MAX_SIZE + 1
        ):
            raise ValueError("limit is outside the bounded closure range.")
        rows = connection.execute(
            f"""SELECT {_DISTRIBUTION_COLUMNS}
                  FROM knowledge_distribution_grant AS grant_row
                 WHERE target_org_unit_id=? AND revoked_at IS NULL
                   AND ((target_scope_kind='org_unit' AND target_scope_id=?)
                     OR (target_scope_kind='native_space' AND target_scope_id=?))
                   AND EXISTS (
                     SELECT 1
                       FROM knowledge_space AS source_space
                       JOIN knowledge_item AS source_item
                         ON source_item.space_id=source_space.id
                        AND source_item.id=grant_row.source_item_id
                      WHERE source_space.id=grant_row.source_space_id
                        AND source_space.status='active'
                        AND source_item.lifecycle_status='active'
                        AND source_item.current_revision_id=
                            grant_row.source_revision_id
                   )
                 ORDER BY source_space_id,source_item_id,id LIMIT ?""",
            (target_org_unit_id, target_org_unit_id, target_space_id, limit),
        ).fetchall()
        return tuple(_distribution_record(row) for row in rows)


__all__ = [
    "DISTRIBUTION_HISTORY_MAX_PAGE_SIZE",
    "DISTRIBUTION_CLOSURE_MAX_SIZE",
    "DistributionGrantNotFoundError",
    "DistributionGrantStateConflictError",
    "DistributionRevokeMode",
    "DistributionTargetKind",
    "HIGHER_ROLE_HISTORY_MAX_PAGE_SIZE",
    "ORG_MOVE_INVALIDATION_MAX_SIZE",
    "HigherRoleNotFoundError",
    "HigherRoleStateConflictError",
    "HigherScopeRole",
    "HigherAuthorityInvalidationCounts",
    "HigherScopeRoleAssignmentRecord",
    "HigherSpaceRepository",
    "HigherSpaceRepositoryError",
    "KnowledgeDistributionGrantRecord",
]
