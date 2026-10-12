"""Connection-scoped organization persistence primitives.

This module deliberately contains SQL, physical-domain validation, row mapping,
and compare-and-swap operations only.  It does not authorize administrators,
own transactions, choose replacement leaders, append audit/outbox records, or
provision knowledge spaces.  P1.5 services compose these primitives inside one
managed transaction; P1.6 remains the sole owner of space provisioning.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3
from typing import Literal, NoReturn
import unicodedata

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError
from ..migrations.v001_seed import (
    RESPONSIBILITY_ROOT_ID,
    RESPONSIBILITY_ROOT_NAME,
    RESPONSIBILITY_ROOT_NAME_KEY,
)
from .spaces import (
    SpaceNotFoundError,
    SpaceRecord,
    SpaceRepository,
    SpaceRepositoryError,
    SpaceStateConflictError,
    SpaceVersionConflictError,
)


OrgUnitKind = Literal["responsibility", "team", "part"]
OrgUnitStatus = Literal["active", "inactive"]
PartSpaceStatus = Literal["active", "frozen", "inactive"]
OrgSpaceKind = Literal["responsibility", "team", "part"]

_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_NONTERMINAL_JOB_STATES = ("queued", "claimed", "running", "retry")

_ORG_COLUMNS = """
id,kind,parent_id,name,name_key,status,created_at,updated_at,created_by,updated_by,
row_version
""".strip()

_SPACE_REPOSITORY = SpaceRepository()


class OrganizationRepositoryError(KnowledgeCoreDatabaseError):
    """Base error for canonical organization persistence."""

    code = "ORGANIZATION_REPOSITORY_ERROR"


class OrgUnitNotFoundError(OrganizationRepositoryError):
    code = "ORG_UNIT_NOT_FOUND"


class OrgUnitVersionConflictError(OrganizationRepositoryError):
    code = "ORG_UNIT_VERSION_CONFLICT"


class OrganizationStateConflictError(OrganizationRepositoryError):
    code = "ORGANIZATION_STATE_CONFLICT"


class MembershipNotFoundError(OrganizationRepositoryError):
    code = "MEMBERSHIP_NOT_FOUND"


class LeaderAssignmentNotFoundError(OrganizationRepositoryError):
    code = "LEADER_ASSIGNMENT_NOT_FOUND"


class PartSpaceNotFoundError(OrganizationRepositoryError):
    code = "PART_SPACE_NOT_FOUND"


class PartSpaceVersionConflictError(OrganizationRepositoryError):
    code = "PART_SPACE_VERSION_CONFLICT"


class UserOrganizationNotFoundError(OrganizationRepositoryError):
    code = "USER_ORGANIZATION_NOT_FOUND"


class UserOrganizationVersionConflictError(OrganizationRepositoryError):
    code = "USER_ORGANIZATION_VERSION_CONFLICT"


@dataclass(frozen=True, slots=True)
class OrgUnitRecord:
    id: str
    kind: OrgUnitKind
    parent_id: str | None
    name: str
    name_key: str
    status: OrgUnitStatus
    created_at_ms: int
    updated_at_ms: int
    created_by: str | None
    updated_by: str | None
    row_version: int


@dataclass(frozen=True, slots=True)
class OrgTreeRow:
    unit: OrgUnitRecord
    depth: int
    path_ids: tuple[str, ...]
    path_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OrgUnitImpactCounts:
    unit_id: str
    active_direct_child_count: int
    active_descendant_count: int
    current_member_count: int
    open_leader_count: int
    direct_owned_space_status: str
    descendant_owned_space_count: int
    owned_space_count: int
    active_space_count: int
    frozen_space_count: int
    inactive_space_count: int
    write_enabled_space_count: int
    knowledge_item_count: int
    active_knowledge_item_count: int
    inactive_knowledge_item_count: int
    merged_knowledge_item_count: int
    purged_tombstone_item_count: int

    @property
    def owned_space_state(self) -> str:
        """Return the direct unit-space state used by an admin preview.

        A unit owns at most one direct space.  Missing space is a valid P1.5
        state; provisioning belongs to P1.6.  Higher-level impact counters may
        additionally include independently owned descendant spaces.
        """

        return self.direct_owned_space_status

    def as_dict(self) -> dict[str, object]:
        return {
            "active_direct_child_count": self.active_direct_child_count,
            "active_descendant_count": self.active_descendant_count,
            "active_knowledge_item_count": self.active_knowledge_item_count,
            "active_space_count": self.active_space_count,
            "current_member_count": self.current_member_count,
            "descendant_owned_space_count": self.descendant_owned_space_count,
            "direct_owned_space_status": self.direct_owned_space_status,
            "frozen_space_count": self.frozen_space_count,
            "inactive_knowledge_item_count": self.inactive_knowledge_item_count,
            "inactive_space_count": self.inactive_space_count,
            "knowledge_item_count": self.knowledge_item_count,
            "merged_knowledge_item_count": self.merged_knowledge_item_count,
            "open_leader_count": self.open_leader_count,
            "owned_space_count": self.owned_space_count,
            "owned_space_state": self.owned_space_state,
            "purged_tombstone_item_count": self.purged_tombstone_item_count,
            "unit_id": self.unit_id,
            "write_enabled_space_count": self.write_enabled_space_count,
        }


@dataclass(frozen=True, slots=True)
class MembershipRecord:
    id: str
    user_id: str
    part_id: str
    valid_from_ms: int
    valid_to_ms: int | None
    assigned_by: str
    reason: str


@dataclass(frozen=True, slots=True)
class LeaderAssignmentRecord:
    id: str
    user_id: str
    part_id: str
    valid_from_ms: int
    valid_to_ms: int | None
    assigned_by: str
    reason: str


@dataclass(frozen=True, slots=True)
class UserOrganizationState:
    user_id: str
    account_status: Literal["active", "disabled"]
    current_part_id: str | None
    scope_epoch: int
    updated_at_ms: int
    row_version: int
    current_membership_id: str | None
    membership_part_id: str | None


@dataclass(frozen=True, slots=True)
class PartSpaceState:
    space_id: str
    part_id: str
    status: PartSpaceStatus
    write_enabled: bool
    generation: int
    updated_at_ms: int
    row_version: int
    space_kind: OrgSpaceKind = "part"

    @property
    def owner_org_unit_id(self) -> str:
        """Return the generic owner while preserving the P1.5 DTO surface."""

        return self.part_id


@dataclass(frozen=True, slots=True)
class AuthorityRevocationCounts:
    revoked_lease_count: int = 0
    invalidated_job_count: int = 0
    cancel_requested_job_count: int = 0

    def combine(
        self, other: "AuthorityRevocationCounts"
    ) -> "AuthorityRevocationCounts":
        if not isinstance(other, AuthorityRevocationCounts):
            raise TypeError("Authority revocation counts can combine only together.")
        return AuthorityRevocationCounts(
            revoked_lease_count=(
                self.revoked_lease_count + other.revoked_lease_count
            ),
            invalidated_job_count=(
                self.invalidated_job_count + other.invalidated_job_count
            ),
            cancel_requested_job_count=(
                self.cancel_requested_job_count
                + other.cancel_requested_job_count
            ),
        )


def _require_connection(connection: ManagedConnection) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("OrganizationRepository requires a ManagedConnection.")
    return connection


def _require_write_transaction(connection: ManagedConnection) -> None:
    _require_connection(connection)
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Organization repository writes require an existing managed transaction."
        )


def _canonical_uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _optional_uuid(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _canonical_uuid(value, field=field)


def _valid_user_id(value: object, *, field: str = "user_id") -> str:
    if not isinstance(value, str) or _USER_ID_RE.fullmatch(value) is None:
        raise ValueError(
            f"{field} must use the canonical lowercase login format."
        )
    return value


def _required_text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")
    normalized = value.strip()
    if not 1 <= len(normalized) <= maximum:
        raise ValueError(
            f"{field} must contain 1 through {maximum} non-blank characters."
        )
    return normalized


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _utc_ms(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative UTC millisecond integer.")
    return value


def _unit_kind(value: object) -> OrgUnitKind:
    if value not in ("responsibility", "team", "part"):
        raise ValueError("kind must be responsibility, team, or part.")
    return value  # type: ignore[return-value]


def _unit_status(value: object) -> OrgUnitStatus:
    if value not in ("active", "inactive"):
        raise ValueError("status must be active or inactive.")
    return value  # type: ignore[return-value]


def _space_status(value: object) -> PartSpaceStatus:
    if value not in ("active", "frozen", "inactive"):
        raise ValueError("status must be active, frozen, or inactive.")
    return value  # type: ignore[return-value]


def normalize_org_name(value: object) -> tuple[str, str]:
    """Return display text and the physical NFKC+casefold sibling key."""

    name = _required_text(value, field="name", maximum=120)
    name_key = unicodedata.normalize("NFKC", name).casefold().strip()
    if not 1 <= len(name_key) <= 160:
        raise ValueError("The normalized organization name exceeds 160 characters.")
    return name, name_key


def _row_to_unit(row: object) -> OrgUnitRecord:
    try:
        return OrgUnitRecord(
            id=str(row["id"]),  # type: ignore[index]
            kind=row["kind"],  # type: ignore[index,arg-type]
            parent_id=row["parent_id"],  # type: ignore[index,arg-type]
            name=str(row["name"]),  # type: ignore[index]
            name_key=str(row["name_key"]),  # type: ignore[index]
            status=row["status"],  # type: ignore[index,arg-type]
            created_at_ms=int(row["created_at"]),  # type: ignore[index]
            updated_at_ms=int(row["updated_at"]),  # type: ignore[index]
            created_by=row["created_by"],  # type: ignore[index,arg-type]
            updated_by=row["updated_by"],  # type: ignore[index,arg-type]
            row_version=int(row["row_version"]),  # type: ignore[index]
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise OrganizationRepositoryError(
            "Canonical organization-unit row mapping failed."
        ) from exc


def _row_to_membership(row: object) -> MembershipRecord:
    try:
        return MembershipRecord(
            id=str(row["id"]),  # type: ignore[index]
            user_id=str(row["user_id"]),  # type: ignore[index]
            part_id=str(row["part_id"]),  # type: ignore[index]
            valid_from_ms=int(row["valid_from"]),  # type: ignore[index]
            valid_to_ms=row["valid_to"],  # type: ignore[index,arg-type]
            assigned_by=str(row["assigned_by"]),  # type: ignore[index]
            reason=str(row["reason"]),  # type: ignore[index]
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise OrganizationRepositoryError(
            "Canonical membership row mapping failed."
        ) from exc


def _row_to_leader(row: object) -> LeaderAssignmentRecord:
    try:
        return LeaderAssignmentRecord(
            id=str(row["id"]),  # type: ignore[index]
            user_id=str(row["user_id"]),  # type: ignore[index]
            part_id=str(row["org_unit_id"]),  # type: ignore[index]
            valid_from_ms=int(row["valid_from"]),  # type: ignore[index]
            valid_to_ms=row["valid_to"],  # type: ignore[index,arg-type]
            assigned_by=str(row["assigned_by"]),  # type: ignore[index]
            reason=str(row["reason"]),  # type: ignore[index]
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise OrganizationRepositoryError(
            "Canonical leader-assignment row mapping failed."
        ) from exc


def _row_to_user_state(row: object) -> UserOrganizationState:
    try:
        return UserOrganizationState(
            user_id=str(row["id"]),  # type: ignore[index]
            account_status=row["account_status"],  # type: ignore[index,arg-type]
            current_part_id=row["current_part_id"],  # type: ignore[index,arg-type]
            scope_epoch=int(row["scope_epoch"]),  # type: ignore[index]
            updated_at_ms=int(row["updated_at"]),  # type: ignore[index]
            row_version=int(row["row_version"]),  # type: ignore[index]
            current_membership_id=row["membership_id"],  # type: ignore[index,arg-type]
            membership_part_id=row["membership_part_id"],  # type: ignore[index,arg-type]
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise OrganizationRepositoryError(
            "Canonical user-organization row mapping failed."
        ) from exc


def _space_record_to_part_state(space: SpaceRecord) -> PartSpaceState:
    """Adapt the P1.6 canonical record to the frozen P1.5 DTO surface."""

    if space.space_kind == "personal" or space.owner_org_unit_id is None:
        raise OrganizationRepositoryError(
            "Canonical organization-space row mapping failed."
        )
    return PartSpaceState(
        space_id=space.id,
        part_id=space.owner_org_unit_id,
        status=space.status,
        write_enabled=space.write_enabled,
        generation=space.generation,
        updated_at_ms=space.updated_at_ms,
        row_version=space.row_version,
        space_kind=space.space_kind,
    )


def _raise_organization_space_error(error: SpaceRepositoryError) -> NoReturn:
    """Preserve the P1.5 repository exception boundary over SpaceRepository."""

    if isinstance(error, SpaceNotFoundError):
        raise PartSpaceNotFoundError(
            "The organization space does not exist."
        ) from error
    if isinstance(error, SpaceVersionConflictError):
        raise PartSpaceVersionConflictError(
            "The organization-space row version changed before the update."
        ) from error
    if isinstance(error, SpaceStateConflictError):
        raise OrganizationStateConflictError(
            "The requested organization-space state is not currently valid."
        ) from error
    raise OrganizationRepositoryError(
        "Canonical organization-space persistence failed."
    ) from error


def _translate_integrity(exc: sqlite3.IntegrityError) -> None:
    message = str(exc)
    if any(
        marker in message
        for marker in (
            "ORG_PARENT_INVALID",
            "ORG_CYCLE",
            "ORG_UNIT_IN_USE",
            "MEMBERSHIP_INVALID",
            "LEADER_WRITE_GUARD",
            "HIGHER_ROLE_WRITE_GUARD",
            "HISTORY_IMMUTABLE",
            "SPACE_OWNER_INVALID",
            "SPACE_KIND_DISABLED",
            "USER_PART_INVALID",
            "SCOPE_EPOCH_INVALID",
            "UNIQUE constraint failed",
        )
    ):
        raise OrganizationStateConflictError(
            "The organization mutation violates canonical state constraints."
        ) from exc
    raise exc


class OrganizationRepository:
    """SQL primitives over units, memberships, leaders, and part authority."""

    __slots__ = ()

    def get_unit(
        self, connection: ManagedConnection, unit_id: str
    ) -> OrgUnitRecord | None:
        connection = _require_connection(connection)
        unit_id = _canonical_uuid(unit_id, field="unit_id")
        row = connection.execute(
            f"SELECT {_ORG_COLUMNS} FROM org_unit WHERE id=?", (unit_id,)
        ).fetchone()
        return None if row is None else _row_to_unit(row)

    def require_unit(
        self, connection: ManagedConnection, unit_id: str
    ) -> OrgUnitRecord:
        unit = self.get_unit(connection, unit_id)
        if unit is None:
            raise OrgUnitNotFoundError("The organization unit does not exist.")
        return unit

    def list_units(
        self,
        connection: ManagedConnection,
        *,
        kind: OrgUnitKind | None = None,
        status: OrgUnitStatus | None = None,
        parent_id: str | None = None,
        direct_children_only: bool = False,
    ) -> tuple[OrgUnitRecord, ...]:
        connection = _require_connection(connection)
        clauses: list[str] = []
        parameters: list[object] = []
        if kind is not None:
            clauses.append("kind=?")
            parameters.append(_unit_kind(kind))
        if status is not None:
            clauses.append("status=?")
            parameters.append(_unit_status(status))
        if direct_children_only:
            parent_id = _optional_uuid(parent_id, field="parent_id")
            clauses.append("parent_id IS ?")
            parameters.append(parent_id)
        elif parent_id is not None:
            raise ValueError(
                "parent_id requires direct_children_only=True to avoid ambiguity."
            )
        where = "" if not clauses else " WHERE " + " AND ".join(clauses)
        rows = connection.execute(
            f"SELECT {_ORG_COLUMNS} FROM org_unit{where} "
            "ORDER BY kind,parent_id,name_key,id",
            tuple(parameters),
        ).fetchall()
        return tuple(_row_to_unit(row) for row in rows)

    def list_subtree(
        self,
        connection: ManagedConnection,
        *,
        root_id: str = RESPONSIBILITY_ROOT_ID,
        include_inactive: bool = True,
    ) -> tuple[OrgTreeRow, ...]:
        connection = _require_connection(connection)
        root_id = _canonical_uuid(root_id, field="root_id")
        self.require_unit(connection, root_id)
        if not isinstance(include_inactive, bool):
            raise ValueError("include_inactive must be a Boolean.")
        rows = connection.execute(
            f"""WITH RECURSIVE subtree(id,depth) AS (
                   SELECT id,0 FROM org_unit WHERE id=?
                   UNION ALL
                   SELECT child.id,subtree.depth+1
                   FROM org_unit AS child
                   JOIN subtree ON child.parent_id=subtree.id
                 )
                 SELECT org_unit.id,org_unit.kind,org_unit.parent_id,
                        org_unit.name,org_unit.name_key,org_unit.status,
                        org_unit.created_at,org_unit.updated_at,
                        org_unit.created_by,org_unit.updated_by,
                        org_unit.row_version,subtree.depth AS tree_depth
                 FROM subtree JOIN org_unit ON org_unit.id=subtree.id
                 {"" if include_inactive else "WHERE org_unit.status='active'"}
                 ORDER BY subtree.depth,org_unit.kind,org_unit.name_key,org_unit.id""",
            (root_id,),
        ).fetchall()
        all_rows = connection.execute(
            f"SELECT {_ORG_COLUMNS} FROM org_unit"
        ).fetchall()
        mapped_units = tuple(_row_to_unit(row) for row in all_rows)
        units = {unit.id: unit for unit in mapped_units}
        result: list[OrgTreeRow] = []
        for row in rows:
            unit = _row_to_unit(row)
            reversed_path: list[OrgUnitRecord] = []
            cursor: OrgUnitRecord | None = unit
            seen: set[str] = set()
            while cursor is not None:
                if cursor.id in seen:
                    raise OrganizationRepositoryError(
                        "A cycle was found while mapping the organization tree."
                    )
                seen.add(cursor.id)
                reversed_path.append(cursor)
                if cursor.id == root_id:
                    break
                cursor = None if cursor.parent_id is None else units.get(cursor.parent_id)
            if not reversed_path or reversed_path[-1].id != root_id:
                raise OrganizationRepositoryError(
                    "An organization subtree row has no path to its requested root."
                )
            path = tuple(reversed(reversed_path))
            result.append(
                OrgTreeRow(
                    unit=unit,
                    depth=int(row["tree_depth"]),
                    path_ids=tuple(value.id for value in path),
                    path_names=tuple(value.name for value in path),
                )
            )
        return tuple(result)

    def create_unit(
        self,
        connection: ManagedConnection,
        *,
        unit_id: str,
        kind: OrgUnitKind,
        parent_id: str | None,
        name: str,
        actor_user_id: str,
        now_ms: int,
    ) -> OrgUnitRecord:
        _require_write_transaction(connection)
        unit_id = _canonical_uuid(unit_id, field="unit_id")
        kind = _unit_kind(kind)
        parent_id = _optional_uuid(parent_id, field="parent_id")
        name, name_key = normalize_org_name(name)
        actor_user_id = _valid_user_id(actor_user_id, field="actor_user_id")
        now_ms = _utc_ms(now_ms, field="now_ms")
        try:
            connection.execute(
                """INSERT INTO org_unit(
                     id,kind,parent_id,name,name_key,status,created_at,updated_at,
                     created_by,updated_by,row_version
                   ) VALUES (?,?,?,?,?,'active',?,?,?,?,1)""",
                (
                    unit_id,
                    kind,
                    parent_id,
                    name,
                    name_key,
                    now_ms,
                    now_ms,
                    actor_user_id,
                    actor_user_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            _translate_integrity(exc)
        return self.require_unit(connection, unit_id)

    def rename_unit(
        self,
        connection: ManagedConnection,
        *,
        unit_id: str,
        expected_row_version: int,
        name: str,
        actor_user_id: str,
        updated_at_ms: int,
    ) -> OrgUnitRecord:
        _require_write_transaction(connection)
        unit_id = _canonical_uuid(unit_id, field="unit_id")
        expected_row_version = _positive_int(
            expected_row_version, field="expected_row_version"
        )
        name, name_key = normalize_org_name(name)
        actor_user_id = _valid_user_id(actor_user_id, field="actor_user_id")
        updated_at_ms = _utc_ms(updated_at_ms, field="updated_at_ms")
        current = self._require_unit_version(
            connection, unit_id, expected_row_version
        )
        if current.name == name and current.name_key == name_key:
            return current
        try:
            cursor = connection.execute(
                """UPDATE org_unit
                   SET name=?,name_key=?,updated_at=?,updated_by=?,
                       row_version=row_version+1
                   WHERE id=? AND row_version=?""",
                (
                    name,
                    name_key,
                    updated_at_ms,
                    actor_user_id,
                    unit_id,
                    expected_row_version,
                ),
            )
        except sqlite3.IntegrityError as exc:
            _translate_integrity(exc)
        if cursor.rowcount != 1:
            self._raise_unit_missing_or_version(connection, unit_id)
        return self.require_unit(connection, unit_id)

    def set_unit_status(
        self,
        connection: ManagedConnection,
        *,
        unit_id: str,
        expected_row_version: int,
        status: OrgUnitStatus,
        actor_user_id: str,
        updated_at_ms: int,
    ) -> OrgUnitRecord:
        _require_write_transaction(connection)
        unit_id = _canonical_uuid(unit_id, field="unit_id")
        expected_row_version = _positive_int(
            expected_row_version, field="expected_row_version"
        )
        status = _unit_status(status)
        actor_user_id = _valid_user_id(actor_user_id, field="actor_user_id")
        updated_at_ms = _utc_ms(updated_at_ms, field="updated_at_ms")
        current = self._require_unit_version(
            connection, unit_id, expected_row_version
        )
        if current.status == status:
            return current
        try:
            cursor = connection.execute(
                """UPDATE org_unit
                   SET status=?,updated_at=?,updated_by=?,row_version=row_version+1
                   WHERE id=? AND row_version=?""",
                (
                    status,
                    updated_at_ms,
                    actor_user_id,
                    unit_id,
                    expected_row_version,
                ),
            )
        except sqlite3.IntegrityError as exc:
            _translate_integrity(exc)
        if cursor.rowcount != 1:
            self._raise_unit_missing_or_version(connection, unit_id)
        return self.require_unit(connection, unit_id)

    def set_unit_parent(
        self,
        connection: ManagedConnection,
        *,
        unit_id: str,
        expected_row_version: int,
        parent_id: str | None,
        actor_user_id: str,
        updated_at_ms: int,
    ) -> OrgUnitRecord:
        _require_write_transaction(connection)
        unit_id = _canonical_uuid(unit_id, field="unit_id")
        expected_row_version = _positive_int(
            expected_row_version, field="expected_row_version"
        )
        parent_id = _optional_uuid(parent_id, field="parent_id")
        actor_user_id = _valid_user_id(actor_user_id, field="actor_user_id")
        updated_at_ms = _utc_ms(updated_at_ms, field="updated_at_ms")
        current = self._require_unit_version(
            connection, unit_id, expected_row_version
        )
        if current.parent_id == parent_id:
            return current
        try:
            cursor = connection.execute(
                """UPDATE org_unit
                   SET parent_id=?,updated_at=?,updated_by=?,
                       row_version=row_version+1
                   WHERE id=? AND row_version=?""",
                (
                    parent_id,
                    updated_at_ms,
                    actor_user_id,
                    unit_id,
                    expected_row_version,
                ),
            )
        except sqlite3.IntegrityError as exc:
            _translate_integrity(exc)
        if cursor.rowcount != 1:
            self._raise_unit_missing_or_version(connection, unit_id)
        return self.require_unit(connection, unit_id)

    def touch_unit(
        self,
        connection: ManagedConnection,
        *,
        unit_id: str,
        expected_row_version: int,
        actor_user_id: str,
        updated_at_ms: int,
    ) -> OrgUnitRecord:
        """Advance the unit aggregate fence after a leader-affecting command."""

        _require_write_transaction(connection)
        unit_id = _canonical_uuid(unit_id, field="unit_id")
        expected_row_version = _positive_int(
            expected_row_version, field="expected_row_version"
        )
        actor_user_id = _valid_user_id(actor_user_id, field="actor_user_id")
        updated_at_ms = _utc_ms(updated_at_ms, field="updated_at_ms")
        cursor = connection.execute(
            """UPDATE org_unit
               SET updated_at=?,updated_by=?,row_version=row_version+1
               WHERE id=? AND row_version=?""",
            (updated_at_ms, actor_user_id, unit_id, expected_row_version),
        )
        if cursor.rowcount != 1:
            self._raise_unit_missing_or_version(connection, unit_id)
        return self.require_unit(connection, unit_id)

    def get_unit_impact(
        self, connection: ManagedConnection, unit_id: str
    ) -> OrgUnitImpactCounts:
        connection = _require_connection(connection)
        unit_id = _canonical_uuid(unit_id, field="unit_id")
        self.require_unit(connection, unit_id)
        row = connection.execute(
            """WITH RECURSIVE scoped_units(id,depth) AS (
                   SELECT id,0 FROM org_unit WHERE id=?
                   UNION ALL
                   SELECT child.id,scoped_units.depth+1
                   FROM org_unit AS child
                   JOIN scoped_units ON child.parent_id=scoped_units.id
                 ), scoped_parts(id) AS (
                   SELECT unit.id FROM org_unit AS unit
                   JOIN scoped_units ON scoped_units.id=unit.id
                   WHERE unit.kind='part'
                 ), scoped_spaces(id,status,write_enabled) AS (
                   SELECT space.id,space.status,space.write_enabled
                   FROM knowledge_space AS space
                   JOIN scoped_units ON scoped_units.id=space.owner_org_unit_id
                 )
                 SELECT
                   (SELECT count(*) FROM org_unit
                    WHERE parent_id=? AND status='active') AS direct_children,
                   (SELECT count(*) FROM scoped_units
                    JOIN org_unit USING(id)
                    WHERE depth>0 AND status='active') AS descendants,
                   (SELECT count(*) FROM org_membership
                    WHERE valid_to IS NULL AND part_id IN
                      (SELECT id FROM scoped_parts)) AS members,
                   (SELECT count(*) FROM org_role_assignment
                    WHERE valid_to IS NULL AND role='part_leader' AND org_unit_id IN
                      (SELECT id FROM scoped_parts)) AS leaders,
                   coalesce((SELECT status FROM knowledge_space
                    WHERE owner_org_unit_id=?),'not_created') AS direct_space_status,
                   (SELECT count(*) FROM knowledge_space AS descendant_space
                    JOIN scoped_units AS descendant_unit
                      ON descendant_unit.id=descendant_space.owner_org_unit_id
                    WHERE descendant_unit.depth>0) AS descendant_spaces,
                   (SELECT count(*) FROM scoped_spaces) AS spaces,
                   (SELECT count(*) FROM scoped_spaces
                    WHERE status='active') AS active_spaces,
                   (SELECT count(*) FROM scoped_spaces
                    WHERE status='frozen') AS frozen_spaces,
                   (SELECT count(*) FROM scoped_spaces
                    WHERE status='inactive') AS inactive_spaces,
                   (SELECT count(*) FROM scoped_spaces
                    WHERE write_enabled=1) AS write_spaces,
                   (SELECT count(*) FROM knowledge_item
                    WHERE space_id IN (SELECT id FROM scoped_spaces)) AS items,
                   (SELECT count(*) FROM knowledge_item
                    WHERE space_id IN (SELECT id FROM scoped_spaces)
                      AND lifecycle_status='active') AS active_items,
                   (SELECT count(*) FROM knowledge_item
                    WHERE space_id IN (SELECT id FROM scoped_spaces)
                      AND lifecycle_status='inactive') AS inactive_items,
                   (SELECT count(*) FROM knowledge_item
                    WHERE space_id IN (SELECT id FROM scoped_spaces)
                      AND lifecycle_status='merged') AS merged_items,
                   (SELECT count(*) FROM knowledge_item
                    WHERE space_id IN (SELECT id FROM scoped_spaces)
                      AND lifecycle_status='purged_tombstone') AS purged_items""",
            (unit_id, unit_id, unit_id),
        ).fetchone()
        if row is None:
            raise OrganizationRepositoryError(
                "Organization impact counts are unavailable."
            )
        return OrgUnitImpactCounts(
            unit_id=unit_id,
            active_direct_child_count=int(row["direct_children"]),
            active_descendant_count=int(row["descendants"]),
            current_member_count=int(row["members"]),
            open_leader_count=int(row["leaders"]),
            direct_owned_space_status=str(row["direct_space_status"]),
            descendant_owned_space_count=int(row["descendant_spaces"]),
            owned_space_count=int(row["spaces"]),
            active_space_count=int(row["active_spaces"]),
            frozen_space_count=int(row["frozen_spaces"]),
            inactive_space_count=int(row["inactive_spaces"]),
            write_enabled_space_count=int(row["write_spaces"]),
            knowledge_item_count=int(row["items"]),
            active_knowledge_item_count=int(row["active_items"]),
            inactive_knowledge_item_count=int(row["inactive_items"]),
            merged_knowledge_item_count=int(row["merged_items"]),
            purged_tombstone_item_count=int(row["purged_items"]),
        )

    def list_current_memberships(
        self,
        connection: ManagedConnection,
        *,
        part_id: str | None = None,
    ) -> tuple[MembershipRecord, ...]:
        connection = _require_connection(connection)
        if part_id is None:
            rows = connection.execute(
                """SELECT id,user_id,part_id,valid_from,valid_to,assigned_by,reason
                   FROM org_membership WHERE valid_to IS NULL
                   ORDER BY part_id,user_id,id"""
            ).fetchall()
        else:
            part_id = _canonical_uuid(part_id, field="part_id")
            rows = connection.execute(
                """SELECT id,user_id,part_id,valid_from,valid_to,assigned_by,reason
                   FROM org_membership
                   WHERE valid_to IS NULL AND part_id=? ORDER BY user_id,id""",
                (part_id,),
            ).fetchall()
        return tuple(_row_to_membership(row) for row in rows)

    def get_current_membership(
        self, connection: ManagedConnection, user_id: str
    ) -> MembershipRecord | None:
        connection = _require_connection(connection)
        user_id = _valid_user_id(user_id)
        row = connection.execute(
            """SELECT id,user_id,part_id,valid_from,valid_to,assigned_by,reason
               FROM org_membership WHERE user_id=? AND valid_to IS NULL""",
            (user_id,),
        ).fetchone()
        return None if row is None else _row_to_membership(row)

    def open_membership(
        self,
        connection: ManagedConnection,
        *,
        membership_id: str,
        user_id: str,
        part_id: str,
        assigned_by: str,
        reason: str,
        valid_from_ms: int,
        expected_current_membership_id: str | None = None,
    ) -> MembershipRecord:
        _require_write_transaction(connection)
        membership_id = _canonical_uuid(membership_id, field="membership_id")
        user_id = _valid_user_id(user_id)
        part_id = _canonical_uuid(part_id, field="part_id")
        assigned_by = _valid_user_id(assigned_by, field="assigned_by")
        reason = _required_text(reason, field="reason", maximum=1000)
        valid_from_ms = _utc_ms(valid_from_ms, field="valid_from_ms")
        expected_current_membership_id = _optional_uuid(
            expected_current_membership_id,
            field="expected_current_membership_id",
        )
        current = self.get_current_membership(connection, user_id)
        actual_id = None if current is None else current.id
        if actual_id != expected_current_membership_id:
            raise OrganizationStateConflictError(
                "The current membership changed before the open operation."
            )
        if current is not None:
            raise OrganizationStateConflictError(
                "A new membership requires no existing open membership."
            )
        try:
            connection.execute(
                """INSERT INTO org_membership(
                     id,user_id,part_id,valid_from,valid_to,assigned_by,reason
                   ) VALUES (?,?,?,?,NULL,?,?)""",
                (
                    membership_id,
                    user_id,
                    part_id,
                    valid_from_ms,
                    assigned_by,
                    reason,
                ),
            )
        except sqlite3.IntegrityError as exc:
            _translate_integrity(exc)
        opened = self.get_current_membership(connection, user_id)
        if opened is None or opened.id != membership_id:
            raise OrganizationRepositoryError(
                "The opened membership cannot be read back."
            )
        return opened

    def close_current_membership(
        self,
        connection: ManagedConnection,
        *,
        user_id: str,
        expected_membership_id: str,
        closed_at_ms: int,
    ) -> MembershipRecord:
        _require_write_transaction(connection)
        user_id = _valid_user_id(user_id)
        expected_membership_id = _canonical_uuid(
            expected_membership_id, field="expected_membership_id"
        )
        closed_at_ms = _utc_ms(closed_at_ms, field="closed_at_ms")
        current = self.get_current_membership(connection, user_id)
        if current is None:
            raise MembershipNotFoundError("The open membership does not exist.")
        if current.id != expected_membership_id:
            raise OrganizationStateConflictError(
                "The current membership changed before it could be closed."
            )
        if closed_at_ms <= current.valid_from_ms:
            raise ValueError("closed_at_ms must follow the membership start.")
        try:
            cursor = connection.execute(
                """UPDATE org_membership SET valid_to=?
                   WHERE id=? AND user_id=? AND valid_to IS NULL""",
                (closed_at_ms, expected_membership_id, user_id),
            )
        except sqlite3.IntegrityError as exc:
            _translate_integrity(exc)
        if cursor.rowcount != 1:
            raise OrganizationStateConflictError(
                "The current membership changed before it could be closed."
            )
        row = connection.execute(
            """SELECT id,user_id,part_id,valid_from,valid_to,assigned_by,reason
               FROM org_membership WHERE id=?""",
            (expected_membership_id,),
        ).fetchone()
        if row is None:
            raise MembershipNotFoundError("The closed membership disappeared.")
        return _row_to_membership(row)

    def get_user_organization_state(
        self, connection: ManagedConnection, user_id: str
    ) -> UserOrganizationState | None:
        connection = _require_connection(connection)
        user_id = _valid_user_id(user_id)
        row = connection.execute(
            """SELECT user.id,user.account_status,user.current_part_id,
                      user.scope_epoch,user.updated_at,user.row_version,
                      membership.id AS membership_id,
                      membership.part_id AS membership_part_id
               FROM app_user AS user
               LEFT JOIN org_membership AS membership
                 ON membership.user_id=user.id AND membership.valid_to IS NULL
               WHERE user.id=?""",
            (user_id,),
        ).fetchone()
        return None if row is None else _row_to_user_state(row)

    def compare_and_swap_user_part(
        self,
        connection: ManagedConnection,
        *,
        user_id: str,
        expected_current_part_id: str | None,
        target_part_id: str | None,
        expected_scope_epoch: int,
        expected_row_version: int,
        updated_at_ms: int,
    ) -> UserOrganizationState:
        _require_write_transaction(connection)
        user_id = _valid_user_id(user_id)
        expected_current_part_id = _optional_uuid(
            expected_current_part_id, field="expected_current_part_id"
        )
        target_part_id = _optional_uuid(target_part_id, field="target_part_id")
        expected_scope_epoch = _positive_int(
            expected_scope_epoch, field="expected_scope_epoch"
        )
        expected_row_version = _positive_int(
            expected_row_version, field="expected_row_version"
        )
        updated_at_ms = _utc_ms(updated_at_ms, field="updated_at_ms")
        current = self.get_user_organization_state(connection, user_id)
        if current is None:
            raise UserOrganizationNotFoundError(
                "The canonical user does not exist."
            )
        if (
            current.current_part_id != expected_current_part_id
            or current.scope_epoch != expected_scope_epoch
            or current.row_version != expected_row_version
        ):
            raise UserOrganizationVersionConflictError(
                "The user's organization scope changed before the update."
            )
        if target_part_id == expected_current_part_id:
            return current
        try:
            cursor = connection.execute(
                """UPDATE app_user
                   SET current_part_id=?,scope_epoch=scope_epoch+1,updated_at=?,
                       row_version=row_version+1
                   WHERE id=? AND current_part_id IS ? AND scope_epoch=?
                     AND row_version=?""",
                (
                    target_part_id,
                    updated_at_ms,
                    user_id,
                    expected_current_part_id,
                    expected_scope_epoch,
                    expected_row_version,
                ),
            )
        except sqlite3.IntegrityError as exc:
            _translate_integrity(exc)
        if cursor.rowcount != 1:
            raise UserOrganizationVersionConflictError(
                "The user's organization scope changed before the update."
            )
        updated = self.get_user_organization_state(connection, user_id)
        if updated is None:
            raise UserOrganizationNotFoundError(
                "The canonical user disappeared after the scope update."
            )
        return updated

    def list_active_leaders(
        self,
        connection: ManagedConnection,
        *,
        part_id: str | None = None,
    ) -> tuple[LeaderAssignmentRecord, ...]:
        connection = _require_connection(connection)
        if part_id is None:
            rows = connection.execute(
                """SELECT id,user_id,org_unit_id,valid_from,valid_to,assigned_by,reason
                   FROM org_role_assignment
                   WHERE role='part_leader' AND valid_to IS NULL
                   ORDER BY org_unit_id,user_id,id"""
            ).fetchall()
        else:
            part_id = _canonical_uuid(part_id, field="part_id")
            rows = connection.execute(
                """SELECT id,user_id,org_unit_id,valid_from,valid_to,assigned_by,reason
                   FROM org_role_assignment
                   WHERE role='part_leader' AND valid_to IS NULL AND org_unit_id=?
                   ORDER BY user_id,id""",
                (part_id,),
            ).fetchall()
        return tuple(_row_to_leader(row) for row in rows)

    def get_active_part_leader(
        self, connection: ManagedConnection, part_id: str
    ) -> LeaderAssignmentRecord | None:
        leaders = self.list_active_leaders(connection, part_id=part_id)
        if not leaders:
            return None
        if len(leaders) != 1:
            raise OrganizationRepositoryError(
                "A part has more than one active leader assignment."
            )
        return leaders[0]

    def open_part_leader(
        self,
        connection: ManagedConnection,
        *,
        assignment_id: str,
        user_id: str,
        part_id: str,
        assigned_by: str,
        reason: str,
        valid_from_ms: int,
        expected_current_assignment_id: str | None = None,
    ) -> LeaderAssignmentRecord:
        _require_write_transaction(connection)
        assignment_id = _canonical_uuid(assignment_id, field="assignment_id")
        user_id = _valid_user_id(user_id)
        part_id = _canonical_uuid(part_id, field="part_id")
        assigned_by = _valid_user_id(assigned_by, field="assigned_by")
        reason = _required_text(reason, field="reason", maximum=1000)
        valid_from_ms = _utc_ms(valid_from_ms, field="valid_from_ms")
        expected_current_assignment_id = _optional_uuid(
            expected_current_assignment_id,
            field="expected_current_assignment_id",
        )
        current = self.get_active_part_leader(connection, part_id)
        actual_id = None if current is None else current.id
        if actual_id != expected_current_assignment_id:
            raise OrganizationStateConflictError(
                "The active leader changed before the assignment was opened."
            )
        if current is not None:
            raise OrganizationStateConflictError(
                "A new leader requires no existing open leader assignment."
            )
        try:
            connection.execute(
                """INSERT INTO org_role_assignment(
                     id,user_id,org_unit_id,role,valid_from,valid_to,assigned_by,reason
                   ) VALUES (?,?,?,'part_leader',?,NULL,?,?)""",
                (
                    assignment_id,
                    user_id,
                    part_id,
                    valid_from_ms,
                    assigned_by,
                    reason,
                ),
            )
        except sqlite3.IntegrityError as exc:
            _translate_integrity(exc)
        opened = self.get_active_part_leader(connection, part_id)
        if opened is None or opened.id != assignment_id:
            raise OrganizationRepositoryError(
                "The opened leader assignment cannot be read back."
            )
        return opened

    def close_part_leader(
        self,
        connection: ManagedConnection,
        *,
        part_id: str,
        expected_assignment_id: str,
        closed_at_ms: int,
    ) -> LeaderAssignmentRecord:
        _require_write_transaction(connection)
        part_id = _canonical_uuid(part_id, field="part_id")
        expected_assignment_id = _canonical_uuid(
            expected_assignment_id, field="expected_assignment_id"
        )
        closed_at_ms = _utc_ms(closed_at_ms, field="closed_at_ms")
        current = self.get_active_part_leader(connection, part_id)
        if current is None:
            raise LeaderAssignmentNotFoundError(
                "The active leader assignment does not exist."
            )
        if current.id != expected_assignment_id:
            raise OrganizationStateConflictError(
                "The active leader changed before it could be closed."
            )
        if closed_at_ms <= current.valid_from_ms:
            raise ValueError("closed_at_ms must follow the leader assignment start.")
        try:
            cursor = connection.execute(
                """UPDATE org_role_assignment SET valid_to=?
                   WHERE id=? AND org_unit_id=? AND valid_to IS NULL""",
                (closed_at_ms, expected_assignment_id, part_id),
            )
        except sqlite3.IntegrityError as exc:
            _translate_integrity(exc)
        if cursor.rowcount != 1:
            raise OrganizationStateConflictError(
                "The active leader changed before it could be closed."
            )
        row = connection.execute(
            """SELECT id,user_id,org_unit_id,valid_from,valid_to,assigned_by,reason
               FROM org_role_assignment WHERE id=?""",
            (expected_assignment_id,),
        ).fetchone()
        if row is None:
            raise LeaderAssignmentNotFoundError(
                "The closed leader assignment disappeared."
            )
        return _row_to_leader(row)

    def get_part_space_state(
        self, connection: ManagedConnection, part_id: str
    ) -> PartSpaceState | None:
        return self.get_org_unit_space_state(
            connection, org_unit_id=part_id, expected_kind="part"
        )

    def get_org_unit_space_state(
        self,
        connection: ManagedConnection,
        *,
        org_unit_id: str,
        expected_kind: OrgSpaceKind | None = None,
    ) -> PartSpaceState | None:
        """Read an optional current or future organization-owned space.

        P1.6 provisions only part spaces, but the frozen schema intentionally
        accepts disabled team/responsibility spaces.  Organization previews
        must therefore account for such rows instead of silently ignoring them.
        """

        connection = _require_connection(connection)
        org_unit_id = _canonical_uuid(org_unit_id, field="org_unit_id")
        if expected_kind is not None and expected_kind not in (
            "responsibility",
            "team",
            "part",
        ):
            raise ValueError("expected_kind is not a supported organization kind.")
        try:
            space = _SPACE_REPOSITORY.get_org(connection, org_unit_id)
        except SpaceRepositoryError as error:
            _raise_organization_space_error(error)
        if space is None:
            return None
        result = _space_record_to_part_state(space)
        if expected_kind is not None and result.space_kind != expected_kind:
            raise OrganizationStateConflictError(
                "The organization space kind does not match its owner unit."
            )
        return result

    def set_part_space_state(
        self,
        connection: ManagedConnection,
        *,
        part_id: str,
        expected_row_version: int,
        status: PartSpaceStatus,
        write_enabled: bool,
        updated_at_ms: int,
    ) -> PartSpaceState:
        _require_write_transaction(connection)
        part_id = _canonical_uuid(part_id, field="part_id")
        expected_row_version = _positive_int(
            expected_row_version, field="expected_row_version"
        )
        status = _space_status(status)
        if not isinstance(write_enabled, bool):
            raise ValueError("write_enabled must be a Boolean.")
        if write_enabled and status != "active":
            raise ValueError("Only an active part space can enable writes.")
        updated_at_ms = _utc_ms(updated_at_ms, field="updated_at_ms")
        current = self.get_part_space_state(connection, part_id)
        if current is None:
            raise PartSpaceNotFoundError(
                "The part space is not provisioned yet."
            )
        if current.row_version != expected_row_version:
            raise PartSpaceVersionConflictError(
                "The part-space row version changed before the update."
            )
        try:
            updated = _SPACE_REPOSITORY.update_state(
                connection,
                space_id=current.space_id,
                expected_row_version=expected_row_version,
                status=status,
                write_enabled=write_enabled,
                updated_at_ms=updated_at_ms,
            )
        except SpaceRepositoryError as error:
            _raise_organization_space_error(error)
        result = _space_record_to_part_state(updated)
        if result.space_kind != "part" or result.part_id != part_id:
            raise OrganizationStateConflictError(
                "The part-space identity changed during its state update."
            )
        return result

    def freeze_part_space(
        self,
        connection: ManagedConnection,
        *,
        part_id: str,
        expected_row_version: int | None,
        updated_at_ms: int,
    ) -> PartSpaceState | None:
        """Freeze an existing part space; missing means valid ``not_created``."""

        _require_write_transaction(connection)
        part_id = _canonical_uuid(part_id, field="part_id")
        updated_at_ms = _utc_ms(updated_at_ms, field="updated_at_ms")
        current = self.get_part_space_state(connection, part_id)
        if current is None:
            if expected_row_version is not None:
                _positive_int(expected_row_version, field="expected_row_version")
            return None
        if expected_row_version is None:
            raise ValueError("An existing part space requires expected_row_version.")
        expected_row_version = _positive_int(
            expected_row_version, field="expected_row_version"
        )
        return self.set_part_space_state(
            connection,
            part_id=part_id,
            expected_row_version=expected_row_version,
            status="frozen",
            write_enabled=False,
            updated_at_ms=updated_at_ms,
        )

    def freeze_org_unit_space(
        self,
        connection: ManagedConnection,
        *,
        org_unit_id: str,
        expected_kind: OrgSpaceKind,
        expected_row_version: int | None,
        updated_at_ms: int,
    ) -> PartSpaceState | None:
        """Freeze an optional organization-owned space without provisioning it."""

        _require_write_transaction(connection)
        org_unit_id = _canonical_uuid(org_unit_id, field="org_unit_id")
        if expected_kind not in ("responsibility", "team", "part"):
            raise ValueError("expected_kind is not a supported organization kind.")
        updated_at_ms = _utc_ms(updated_at_ms, field="updated_at_ms")
        current = self.get_org_unit_space_state(
            connection,
            org_unit_id=org_unit_id,
            expected_kind=expected_kind,
        )
        if current is None:
            if expected_row_version is not None:
                _positive_int(expected_row_version, field="expected_row_version")
            return None
        if expected_row_version is None:
            raise ValueError(
                "An existing organization space requires expected_row_version."
            )
        expected_row_version = _positive_int(
            expected_row_version, field="expected_row_version"
        )
        if current.row_version != expected_row_version:
            raise PartSpaceVersionConflictError(
                "The organization-space row version changed before the update."
            )
        try:
            updated = _SPACE_REPOSITORY.update_state(
                connection,
                space_id=current.space_id,
                expected_row_version=expected_row_version,
                status="frozen",
                write_enabled=False,
                updated_at_ms=updated_at_ms,
            )
        except SpaceRepositoryError as error:
            _raise_organization_space_error(error)
        result = _space_record_to_part_state(updated)
        if result.space_kind != expected_kind or result.part_id != org_unit_id:
            raise OrganizationStateConflictError(
                "The organization-space identity changed during its state update."
            )
        return result

    def revoke_user_part_leases(
        self,
        connection: ManagedConnection,
        *,
        user_id: str,
        part_id: str,
    ) -> AuthorityRevocationCounts:
        _require_write_transaction(connection)
        user_id = _valid_user_id(user_id)
        part_id = _canonical_uuid(part_id, field="part_id")
        try:
            part_space = _SPACE_REPOSITORY.get_part(connection, part_id)
        except SpaceRepositoryError as error:
            _raise_organization_space_error(error)
        if part_space is None:
            return AuthorityRevocationCounts()
        cursor = connection.execute(
            """UPDATE item_lease
               SET holder_user_id=NULL,editor_session_id=NULL,token_hmac=NULL,
                   hmac_key_id=NULL,fencing_generation=fencing_generation+1,
                   base_item_row_version=NULL,base_revision_id=NULL,
                   acquired_at=NULL,heartbeat_at=NULL,expires_at=NULL,
                   absolute_expires_at=NULL
               WHERE holder_user_id=? AND item_id IN (
                 SELECT item.id FROM knowledge_item AS item WHERE item.space_id=?
               )""",
            (user_id, part_space.id),
        )
        return AuthorityRevocationCounts(
            revoked_lease_count=max(0, cursor.rowcount)
        )

    def invalidate_user_jobs_for_scope_epoch(
        self,
        connection: ManagedConnection,
        *,
        user_id: str,
        invalidated_scope_epoch: int,
        updated_at_ms: int,
    ) -> AuthorityRevocationCounts:
        """Count old-epoch jobs and request cancellation where supported.

        Authorization snapshot/epoch columns are immutable.  Scope invalidation
        therefore comes from the user CAS that advances the canonical epoch;
        this primitive only reports affected non-terminal rows and marks the
        cancellable subset for cooperative cancellation.
        """

        _require_write_transaction(connection)
        user_id = _valid_user_id(user_id)
        invalidated_scope_epoch = _positive_int(
            invalidated_scope_epoch, field="invalidated_scope_epoch"
        )
        updated_at_ms = _utc_ms(updated_at_ms, field="updated_at_ms")
        placeholders = ",".join("?" for _ in _NONTERMINAL_JOB_STATES)
        parameters = (
            user_id,
            invalidated_scope_epoch,
            *_NONTERMINAL_JOB_STATES,
        )
        row = connection.execute(
            f"""SELECT count(*) FROM durable_job
                 WHERE actor_user_id=? AND authorization_scope_epoch=?
                   AND state IN ({placeholders})""",
            parameters,
        ).fetchone()
        invalidated = 0 if row is None else int(row[0])
        cursor = connection.execute(
            f"""UPDATE durable_job
                 SET cancel_requested=1,updated_at=?
                 WHERE actor_user_id=? AND authorization_scope_epoch=?
                   AND state IN ({placeholders}) AND cancellable=1
                   AND cancel_requested=0""",
            (
                updated_at_ms,
                user_id,
                invalidated_scope_epoch,
                *_NONTERMINAL_JOB_STATES,
            ),
        )
        return AuthorityRevocationCounts(
            invalidated_job_count=invalidated,
            cancel_requested_job_count=max(0, cursor.rowcount),
        )

    def _require_unit_version(
        self,
        connection: ManagedConnection,
        unit_id: str,
        expected_row_version: int,
    ) -> OrgUnitRecord:
        unit = self.require_unit(connection, unit_id)
        if unit.row_version != expected_row_version:
            raise OrgUnitVersionConflictError(
                "The organization-unit row version changed before the update."
            )
        return unit

    def _raise_unit_missing_or_version(
        self, connection: ManagedConnection, unit_id: str
    ) -> None:
        if self.get_unit(connection, unit_id) is None:
            raise OrgUnitNotFoundError("The organization unit does not exist.")
        raise OrgUnitVersionConflictError(
            "The organization-unit row version changed before the update."
        )


__all__ = [
    "AuthorityRevocationCounts",
    "LeaderAssignmentNotFoundError",
    "LeaderAssignmentRecord",
    "MembershipNotFoundError",
    "MembershipRecord",
    "OrgTreeRow",
    "OrgUnitImpactCounts",
    "OrgUnitKind",
    "OrgSpaceKind",
    "OrgUnitNotFoundError",
    "OrgUnitRecord",
    "OrgUnitStatus",
    "OrgUnitVersionConflictError",
    "OrganizationRepository",
    "OrganizationRepositoryError",
    "OrganizationStateConflictError",
    "PartSpaceNotFoundError",
    "PartSpaceState",
    "PartSpaceStatus",
    "PartSpaceVersionConflictError",
    "RESPONSIBILITY_ROOT_ID",
    "RESPONSIBILITY_ROOT_NAME",
    "RESPONSIBILITY_ROOT_NAME_KEY",
    "UserOrganizationNotFoundError",
    "UserOrganizationState",
    "UserOrganizationVersionConflictError",
    "normalize_org_name",
]
