"""Canonical user persistence primitives.

The repository deliberately owns only SQL, validation, and row mapping.  It
does not decide who may administer an account, choose a replacement part
leader, create Flask sessions, or commit transactions.  Callers must execute
mutations inside :func:`knowledge_core.db.transaction` and append their audit
event through the same managed connection before that transaction commits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import sqlite3
from typing import Iterable, Literal, Mapping, NoReturn

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError
from .spaces import (
    SpaceNotFoundError,
    SpaceRecord,
    SpaceRepository,
    SpaceRepositoryError,
    SpaceStateConflictError,
    SpaceVersionConflictError,
)


SystemRole = Literal["admin", "user"]
AccountStatus = Literal["active", "disabled"]

_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_NONTERMINAL_JOB_STATES = ("queued", "claimed", "running", "retry")

_USER_COLUMNS = """
id, display_name, password_hash, system_role, account_status, current_part_id,
scope_epoch, must_change_password, password_changed_at, created_at, updated_at,
disabled_at, row_version
""".strip()

_OPERATION_COLUMNS = """
id,actor_user_id,operation_type,operation_id,request_sha256,status,http_status,
result_json,error_json,created_at,completed_at,last_seen_at
""".strip()

_SPACE_REPOSITORY = SpaceRepository()


class UserRepositoryError(KnowledgeCoreDatabaseError):
    """Base error for canonical user repository operations."""

    code = "USER_REPOSITORY_ERROR"


class UserNotFoundError(UserRepositoryError):
    code = "USER_NOT_FOUND"


class UserVersionConflictError(UserRepositoryError):
    code = "USER_VERSION_CONFLICT"


class UserImportConflictError(UserRepositoryError):
    code = "USER_IMPORT_CONFLICT"


class UserLeaderGuardError(UserRepositoryError):
    code = "USER_LEADER_WRITE_GUARD"


class UserMembershipGuardError(UserRepositoryError):
    code = "USER_MEMBERSHIP_GUARD"


class UserHardDeleteForbiddenError(UserRepositoryError):
    code = "USER_HARD_DELETE_FORBIDDEN"


class OperationIdempotencyConflictError(UserRepositoryError):
    code = "IDEMPOTENCY_CONFLICT"


class OperationInProgressError(UserRepositoryError):
    code = "OPERATION_IN_PROGRESS"


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: str
    display_name: str
    password_hash: str = field(repr=False)
    system_role: SystemRole
    account_status: AccountStatus
    current_part_id: str | None
    scope_epoch: int
    must_change_password: bool
    password_changed_at_ms: int | None
    created_at_ms: int
    updated_at_ms: int
    disabled_at_ms: int | None
    row_version: int

    @property
    def is_admin(self) -> bool:
        return self.system_role == "admin"

    @property
    def disabled(self) -> bool:
        return self.account_status == "disabled"

    def as_legacy_public_user(
        self,
    ) -> dict[str, object]:
        """Return the stable fields consumed by the existing cookie adapter."""

        return {
            "id": self.id,
            "display_name": self.display_name,
            "role": self.system_role,
            "is_admin": self.is_admin,
            "disabled": self.disabled,
            "must_change_password": self.must_change_password,
            "password_changed_at": self.password_changed_at_ms,
            "created_at": self.created_at_ms,
            "updated_at": self.updated_at_ms,
            "disabled_at": self.disabled_at_ms,
            "scope_epoch": self.scope_epoch,
            "row_version": self.row_version,
        }


@dataclass(frozen=True, slots=True)
class LegacyUserRecord:
    """Already-normalized input from the offline legacy migration planner."""

    id: str
    display_name: str
    password_hash: str = field(repr=False)
    system_role: SystemRole
    account_status: AccountStatus
    created_at_ms: int
    updated_at_ms: int
    disabled_at_ms: int | None = None
    current_part_id: str | None = None
    scope_epoch: int = 1
    must_change_password: bool = False
    password_changed_at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class LegacyUserImportResult:
    user: UserRecord
    inserted: bool


@dataclass(frozen=True, slots=True)
class AccountStatusTransition:
    user: UserRecord
    changed: bool
    previous_part_id: str | None
    invalidated_session_epoch: bool
    revoked_lease_count: int
    invalidated_job_count: int
    cancel_requested_job_count: int


@dataclass(frozen=True, slots=True)
class OperationDedupRecord:
    id: str
    actor_user_id: str
    operation_type: str
    operation_id: str
    request_sha256: str
    status: Literal["in_progress", "committed", "failed_final"]
    http_status: int | None
    result_json: str | None
    error_json: str | None
    created_at_ms: int
    completed_at_ms: int | None
    last_seen_at_ms: int

    @property
    def result(self) -> dict[str, object] | None:
        if self.result_json is None:
            return None
        value = json.loads(self.result_json)
        if not isinstance(value, dict):
            raise UserRepositoryError("Stored operation result is not an object.")
        return value

    @property
    def error(self) -> dict[str, object] | None:
        if self.error_json is None:
            return None
        value = json.loads(self.error_json)
        if not isinstance(value, dict):
            raise UserRepositoryError("Stored operation error is not an object.")
        return value


@dataclass(frozen=True, slots=True)
class OperationBeginResult:
    operation: OperationDedupRecord
    inserted: bool


@dataclass(frozen=True, slots=True)
class OpenMembershipRecord:
    id: str
    user_id: str
    part_id: str
    valid_from_ms: int
    assigned_by: str
    reason: str


@dataclass(frozen=True, slots=True)
class ActiveLeaderAssignment:
    id: str
    user_id: str
    part_id: str
    valid_from_ms: int
    assigned_by: str
    reason: str


@dataclass(frozen=True, slots=True)
class PartSpaceWriteState:
    space_id: str
    part_id: str
    write_enabled: bool
    row_version: int


def _space_record_to_part_write_state(space: SpaceRecord) -> PartSpaceWriteState:
    """Adapt the canonical space record to the frozen P1.4 DTO surface."""

    if space.space_kind != "part" or space.owner_org_unit_id is None:
        raise UserRepositoryError("Canonical part-space row mapping failed.")
    return PartSpaceWriteState(
        space_id=space.id,
        part_id=space.owner_org_unit_id,
        write_enabled=space.write_enabled,
        row_version=space.row_version,
    )


def _raise_user_space_error(error: SpaceRepositoryError) -> NoReturn:
    """Preserve P1.4 user-repository exceptions over SpaceRepository."""

    if isinstance(error, SpaceNotFoundError):
        raise UserNotFoundError("The canonical part space does not exist.") from error
    if isinstance(error, SpaceVersionConflictError):
        raise UserVersionConflictError(
            "The part-space row version changed before the write toggle."
        ) from error
    if isinstance(error, SpaceStateConflictError):
        raise UserLeaderGuardError(
            "The requested part-space write state is not currently valid."
        ) from error
    raise UserRepositoryError("Canonical part-space persistence failed.") from error


def _get_part_space_record(
    connection: ManagedConnection, part_id: str
) -> SpaceRecord | None:
    try:
        return _SPACE_REPOSITORY.get_part(connection, part_id)
    except SpaceRepositoryError as error:
        _raise_user_space_error(error)


def _require_connection(connection: ManagedConnection) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("UserRepository requires a ManagedConnection.")
    return connection


def _require_write_transaction(connection: ManagedConnection) -> None:
    _require_connection(connection)
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "User repository writes require an existing managed transaction."
        )


def _valid_user_id(value: object) -> str:
    if not isinstance(value, str) or _USER_ID_RE.fullmatch(value) is None:
        raise ValueError("user_id must use the canonical lowercase login format.")
    return value


def _required_text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"{field} must contain 1..{maximum} non-blank characters.")
    return value.strip()


def _utc_ms(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative UTC millisecond integer.")
    return value


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _canonical_uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest.")
    return value


def _canonical_json_object(value: object, *, field: str) -> str:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a JSON object mapping.")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be JSON serializable.") from exc
    if not isinstance(json.loads(encoded), dict):
        raise ValueError(f"{field} must encode a JSON object.")
    return encoded


def _normalize_import(record: LegacyUserRecord) -> LegacyUserRecord:
    if not isinstance(record, LegacyUserRecord):
        raise TypeError("legacy import requires a LegacyUserRecord.")
    user_id = _valid_user_id(record.id)
    display_name = _required_text(
        record.display_name, field="display_name", maximum=120
    )
    password_hash = _required_text(
        record.password_hash, field="password_hash", maximum=512
    )
    if record.system_role not in ("admin", "user"):
        raise ValueError("system_role must be admin or user.")
    if record.account_status not in ("active", "disabled"):
        raise ValueError("account_status must be active or disabled.")
    created_at = _utc_ms(record.created_at_ms, field="created_at_ms")
    updated_at = _utc_ms(record.updated_at_ms, field="updated_at_ms")
    if updated_at < created_at:
        raise ValueError("updated_at_ms cannot precede created_at_ms.")
    disabled_at = record.disabled_at_ms
    if record.account_status == "disabled":
        if disabled_at is None:
            raise ValueError("disabled legacy users require disabled_at_ms.")
        disabled_at = _utc_ms(disabled_at, field="disabled_at_ms")
        if disabled_at < created_at:
            raise ValueError("disabled_at_ms cannot precede created_at_ms.")
        if record.current_part_id is not None:
            raise ValueError("disabled legacy users must be explicitly unassigned.")
    elif disabled_at is not None:
        raise ValueError("active legacy users cannot have disabled_at_ms.")
    scope_epoch = _positive_int(record.scope_epoch, field="scope_epoch")
    if not isinstance(record.must_change_password, bool):
        raise ValueError("must_change_password must be a Boolean.")
    password_changed_at = record.password_changed_at_ms
    if password_changed_at is not None:
        password_changed_at = _utc_ms(
            password_changed_at, field="password_changed_at_ms"
        )
        if password_changed_at < created_at:
            raise ValueError("password_changed_at_ms cannot precede created_at_ms.")
    if record.must_change_password and password_changed_at is not None:
        raise ValueError(
            "A forced password-change account cannot have password_changed_at_ms."
        )
    # Part ownership is an explicit P1.5/P2 planner decision.  This repository
    # passes a supplied canonical part UUID to the schema trigger and never
    # infers it from a path, display name, or legacy content.
    current_part_id = record.current_part_id
    if current_part_id is not None:
        current_part_id = _canonical_uuid(
            current_part_id, field="current_part_id"
        )
    return LegacyUserRecord(
        id=user_id,
        display_name=display_name,
        password_hash=password_hash,
        system_role=record.system_role,
        account_status=record.account_status,
        created_at_ms=created_at,
        updated_at_ms=updated_at,
        disabled_at_ms=disabled_at,
        current_part_id=current_part_id,
        scope_epoch=scope_epoch,
        must_change_password=record.must_change_password,
        password_changed_at_ms=password_changed_at,
    )


def _row_to_user(row: object) -> UserRecord:
    if row is None:
        raise UserNotFoundError("The requested canonical user does not exist.")
    try:
        return UserRecord(
            id=str(row["id"]),  # type: ignore[index]
            display_name=str(row["display_name"]),  # type: ignore[index]
            password_hash=str(row["password_hash"]),  # type: ignore[index]
            system_role=row["system_role"],  # type: ignore[index,arg-type]
            account_status=row["account_status"],  # type: ignore[index,arg-type]
            current_part_id=row["current_part_id"],  # type: ignore[index,arg-type]
            scope_epoch=int(row["scope_epoch"]),  # type: ignore[index]
            must_change_password=bool(row["must_change_password"]),  # type: ignore[index]
            password_changed_at_ms=row["password_changed_at"],  # type: ignore[index,arg-type]
            created_at_ms=int(row["created_at"]),  # type: ignore[index]
            updated_at_ms=int(row["updated_at"]),  # type: ignore[index]
            disabled_at_ms=row["disabled_at"],  # type: ignore[index,arg-type]
            row_version=int(row["row_version"]),  # type: ignore[index]
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise UserRepositoryError("Canonical user row mapping failed.") from exc


def _row_to_operation(row: object) -> OperationDedupRecord:
    try:
        return OperationDedupRecord(
            id=str(row["id"]),  # type: ignore[index]
            actor_user_id=str(row["actor_user_id"]),  # type: ignore[index]
            operation_type=str(row["operation_type"]),  # type: ignore[index]
            operation_id=str(row["operation_id"]),  # type: ignore[index]
            request_sha256=str(row["request_sha256"]),  # type: ignore[index]
            status=row["status"],  # type: ignore[index,arg-type]
            http_status=row["http_status"],  # type: ignore[index,arg-type]
            result_json=row["result_json"],  # type: ignore[index,arg-type]
            error_json=row["error_json"],  # type: ignore[index,arg-type]
            created_at_ms=int(row["created_at"]),  # type: ignore[index]
            completed_at_ms=row["completed_at"],  # type: ignore[index,arg-type]
            last_seen_at_ms=int(row["last_seen_at"]),  # type: ignore[index]
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise UserRepositoryError("Operation idempotency row mapping failed.") from exc


def _import_identity(record: LegacyUserRecord) -> tuple[object, ...]:
    return (
        record.id,
        record.display_name,
        record.password_hash,
        record.system_role,
        record.account_status,
        record.current_part_id,
        record.scope_epoch,
        1 if record.must_change_password else 0,
        record.password_changed_at_ms,
        record.created_at_ms,
        record.updated_at_ms,
        record.disabled_at_ms,
        1,
    )


def _record_identity(record: UserRecord) -> tuple[object, ...]:
    return (
        record.id,
        record.display_name,
        record.password_hash,
        record.system_role,
        record.account_status,
        record.current_part_id,
        record.scope_epoch,
        1 if record.must_change_password else 0,
        record.password_changed_at_ms,
        record.created_at_ms,
        record.updated_at_ms,
        record.disabled_at_ms,
        record.row_version,
    )


class UserRepository:
    """Connection-scoped SQL primitives for ``app_user``."""

    __slots__ = ()

    def get(self, connection: ManagedConnection, user_id: str) -> UserRecord | None:
        connection = _require_connection(connection)
        user_id = _valid_user_id(user_id)
        row = connection.execute(
            f"SELECT {_USER_COLUMNS} FROM app_user WHERE id=?", (user_id,)
        ).fetchone()
        return None if row is None else _row_to_user(row)

    def require(self, connection: ManagedConnection, user_id: str) -> UserRecord:
        record = self.get(connection, user_id)
        if record is None:
            raise UserNotFoundError("The requested canonical user does not exist.")
        return record

    def count(self, connection: ManagedConnection) -> int:
        connection = _require_connection(connection)
        row = connection.execute("SELECT count(*) AS count FROM app_user").fetchone()
        if row is None:
            raise UserRepositoryError("Canonical user count is unavailable.")
        return int(row[0])

    def list_all(
        self,
        connection: ManagedConnection,
        *,
        account_status: AccountStatus | None = None,
    ) -> tuple[UserRecord, ...]:
        connection = _require_connection(connection)
        if account_status is None:
            rows = connection.execute(
                f"SELECT {_USER_COLUMNS} FROM app_user ORDER BY id"
            ).fetchall()
        else:
            if account_status not in ("active", "disabled"):
                raise ValueError("account_status must be active, disabled, or null.")
            rows = connection.execute(
                f"SELECT {_USER_COLUMNS} FROM app_user "
                "WHERE account_status=? ORDER BY id",
                (account_status,),
            ).fetchall()
        return tuple(_row_to_user(row) for row in rows)

    def active_admin_count(
        self,
        connection: ManagedConnection,
        *,
        excluding_user_id: str | None = None,
    ) -> int:
        connection = _require_connection(connection)
        if excluding_user_id is None:
            row = connection.execute(
                """SELECT count(*) FROM app_user
                   WHERE account_status='active' AND system_role='admin'"""
            ).fetchone()
        else:
            excluding_user_id = _valid_user_id(excluding_user_id)
            row = connection.execute(
                """SELECT count(*) FROM app_user
                   WHERE account_status='active' AND system_role='admin' AND id<>?""",
                (excluding_user_id,),
            ).fetchone()
        if row is None:
            raise UserRepositoryError("The active administrator count is unavailable.")
        return int(row[0])

    def get_open_membership(
        self, connection: ManagedConnection, user_id: str
    ) -> OpenMembershipRecord | None:
        connection = _require_connection(connection)
        user_id = _valid_user_id(user_id)
        row = connection.execute(
            """SELECT id,user_id,part_id,valid_from,assigned_by,reason
               FROM org_membership WHERE user_id=? AND valid_to IS NULL""",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return OpenMembershipRecord(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            part_id=str(row["part_id"]),
            valid_from_ms=int(row["valid_from"]),
            assigned_by=str(row["assigned_by"]),
            reason=str(row["reason"]),
        )

    def close_open_membership(
        self,
        connection: ManagedConnection,
        *,
        user_id: str,
        closed_at_ms: int,
    ) -> OpenMembershipRecord | None:
        _require_write_transaction(connection)
        user_id = _valid_user_id(user_id)
        closed_at_ms = _utc_ms(closed_at_ms, field="closed_at_ms")
        membership = self.get_open_membership(connection, user_id)
        if membership is None:
            return None
        if closed_at_ms <= membership.valid_from_ms:
            raise ValueError("closed_at_ms must follow the membership start.")
        cursor = connection.execute(
            """UPDATE org_membership SET valid_to=?
               WHERE id=? AND valid_to IS NULL""",
            (closed_at_ms, membership.id),
        )
        if cursor.rowcount != 1:
            raise UserVersionConflictError(
                "The open membership changed before it could be closed."
            )
        return membership

    def list_active_leader_assignments(
        self, connection: ManagedConnection, user_id: str
    ) -> tuple[ActiveLeaderAssignment, ...]:
        connection = _require_connection(connection)
        user_id = _valid_user_id(user_id)
        rows = connection.execute(
            """SELECT id,user_id,org_unit_id,valid_from,assigned_by,reason
               FROM org_role_assignment
               WHERE user_id=? AND role='part_leader' AND valid_to IS NULL
               ORDER BY org_unit_id,id""",
            (user_id,),
        ).fetchall()
        return tuple(
            ActiveLeaderAssignment(
                id=str(row["id"]),
                user_id=str(row["user_id"]),
                part_id=str(row["org_unit_id"]),
                valid_from_ms=int(row["valid_from"]),
                assigned_by=str(row["assigned_by"]),
                reason=str(row["reason"]),
            )
            for row in rows
        )

    def get_active_part_leader(
        self, connection: ManagedConnection, part_id: str
    ) -> ActiveLeaderAssignment | None:
        connection = _require_connection(connection)
        part_id = _canonical_uuid(part_id, field="part_id")
        row = connection.execute(
            """SELECT id,user_id,org_unit_id,valid_from,assigned_by,reason
               FROM org_role_assignment
               WHERE org_unit_id=? AND role='part_leader' AND valid_to IS NULL""",
            (part_id,),
        ).fetchone()
        if row is None:
            return None
        return ActiveLeaderAssignment(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            part_id=str(row["org_unit_id"]),
            valid_from_ms=int(row["valid_from"]),
            assigned_by=str(row["assigned_by"]),
            reason=str(row["reason"]),
        )

    def close_leader_assignment(
        self,
        connection: ManagedConnection,
        *,
        assignment_id: str,
        closed_at_ms: int,
    ) -> ActiveLeaderAssignment:
        _require_write_transaction(connection)
        assignment_id = _canonical_uuid(assignment_id, field="assignment_id")
        closed_at_ms = _utc_ms(closed_at_ms, field="closed_at_ms")
        row = connection.execute(
            """SELECT id,user_id,org_unit_id,valid_from,assigned_by,reason
               FROM org_role_assignment
               WHERE id=? AND role='part_leader' AND valid_to IS NULL""",
            (assignment_id,),
        ).fetchone()
        if row is None:
            raise UserNotFoundError("The active leader assignment does not exist.")
        assignment = ActiveLeaderAssignment(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            part_id=str(row["org_unit_id"]),
            valid_from_ms=int(row["valid_from"]),
            assigned_by=str(row["assigned_by"]),
            reason=str(row["reason"]),
        )
        if closed_at_ms <= assignment.valid_from_ms:
            raise ValueError("closed_at_ms must follow the leader assignment start.")
        cursor = connection.execute(
            """UPDATE org_role_assignment SET valid_to=?
               WHERE id=? AND valid_to IS NULL""",
            (closed_at_ms, assignment_id),
        )
        if cursor.rowcount != 1:
            raise UserVersionConflictError(
                "The leader assignment changed before it could be closed."
            )
        return assignment

    def open_part_leader_assignment(
        self,
        connection: ManagedConnection,
        *,
        assignment_id: str,
        user_id: str,
        part_id: str,
        assigned_by: str,
        reason: str,
        valid_from_ms: int,
    ) -> ActiveLeaderAssignment:
        _require_write_transaction(connection)
        assignment_id = _canonical_uuid(assignment_id, field="assignment_id")
        user_id = _valid_user_id(user_id)
        part_id = _canonical_uuid(part_id, field="part_id")
        assigned_by = _valid_user_id(assigned_by)
        reason = _required_text(reason, field="reason", maximum=1000)
        valid_from_ms = _utc_ms(valid_from_ms, field="valid_from_ms")
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
        return ActiveLeaderAssignment(
            id=assignment_id,
            user_id=user_id,
            part_id=part_id,
            valid_from_ms=valid_from_ms,
            assigned_by=assigned_by,
            reason=reason,
        )

    def get_part_space_write_state(
        self, connection: ManagedConnection, part_id: str
    ) -> PartSpaceWriteState | None:
        connection = _require_connection(connection)
        part_id = _canonical_uuid(part_id, field="part_id")
        space = _get_part_space_record(connection, part_id)
        if space is None:
            return None
        return _space_record_to_part_write_state(space)

    def set_part_space_write_enabled(
        self,
        connection: ManagedConnection,
        *,
        part_id: str,
        enabled: bool,
        expected_row_version: int,
        updated_at_ms: int,
    ) -> PartSpaceWriteState:
        _require_write_transaction(connection)
        part_id = _canonical_uuid(part_id, field="part_id")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a Boolean.")
        expected_row_version = _positive_int(
            expected_row_version, field="expected_row_version"
        )
        updated_at_ms = _utc_ms(updated_at_ms, field="updated_at_ms")
        current_space = _get_part_space_record(connection, part_id)
        if current_space is None:
            raise UserNotFoundError("The canonical part space does not exist.")
        current = _space_record_to_part_write_state(current_space)
        if enabled and current_space.status != "active":
            raise UserLeaderGuardError(
                "The requested part-space write state is not currently valid."
            )
        try:
            updated = _SPACE_REPOSITORY.update_state(
                connection,
                space_id=current.space_id,
                expected_row_version=expected_row_version,
                status=current_space.status,
                write_enabled=enabled,
                updated_at_ms=updated_at_ms,
            )
        except SpaceRepositoryError as error:
            _raise_user_space_error(error)
        result = _space_record_to_part_write_state(updated)
        if result.part_id != part_id:
            raise UserRepositoryError(
                "The part-space identity changed during its write toggle."
            )
        return result

    def import_legacy(
        self,
        connection: ManagedConnection,
        record: LegacyUserRecord,
    ) -> LegacyUserImportResult:
        """Insert one normalized legacy account or prove an exact prior import.

        A matching retry returns the existing row.  A reused login ID whose
        password hash, role, status, assignment, time, epoch, or row version no
        longer matches is never overwritten and is reported as a conflict.
        """

        _require_write_transaction(connection)
        normalized = _normalize_import(record)
        cursor = connection.execute(
            """INSERT INTO app_user(
                 id,display_name,password_hash,system_role,account_status,
                 current_part_id,scope_epoch,must_change_password,
                 password_changed_at,created_at,updated_at,disabled_at,row_version
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)
               ON CONFLICT(id) DO NOTHING""",
            (
                normalized.id,
                normalized.display_name,
                normalized.password_hash,
                normalized.system_role,
                normalized.account_status,
                normalized.current_part_id,
                normalized.scope_epoch,
                1 if normalized.must_change_password else 0,
                normalized.password_changed_at_ms,
                normalized.created_at_ms,
                normalized.updated_at_ms,
                normalized.disabled_at_ms,
            ),
        )
        inserted = cursor.rowcount == 1
        current = self.require(connection, normalized.id)
        if not inserted and _record_identity(current) != _import_identity(normalized):
            raise UserImportConflictError(
                "The legacy login ID already maps to different canonical user state."
            )
        return LegacyUserImportResult(user=current, inserted=inserted)

    def import_legacy_many(
        self,
        connection: ManagedConnection,
        records: Iterable[LegacyUserRecord],
    ) -> tuple[LegacyUserImportResult, ...]:
        _require_write_transaction(connection)
        return tuple(self.import_legacy(connection, record) for record in records)

    def begin_operation(
        self,
        connection: ManagedConnection,
        *,
        actor_user_id: str,
        operation_type: str,
        operation_id: str,
        request_sha256: str,
        dedup_id: str,
        now_ms: int,
    ) -> OperationBeginResult:
        """Reserve an actor-scoped mutation ID without applying domain policy."""

        _require_write_transaction(connection)
        actor_user_id = _valid_user_id(actor_user_id)
        operation_type = _required_text(
            operation_type, field="operation_type", maximum=80
        )
        operation_id = _canonical_uuid(operation_id, field="operation_id")
        request_sha256 = _sha256(request_sha256, field="request_sha256")
        dedup_id = _canonical_uuid(dedup_id, field="dedup_id")
        now_ms = _utc_ms(now_ms, field="now_ms")
        cursor = connection.execute(
            """INSERT INTO operation_dedup(
                 id,actor_user_id,operation_type,operation_id,request_sha256,
                 status,http_status,result_json,error_json,created_at,
                 completed_at,last_seen_at
               ) VALUES (?,?,?,?,?,'in_progress',NULL,NULL,NULL,?,NULL,?)
               ON CONFLICT DO NOTHING""",
            (
                dedup_id,
                actor_user_id,
                operation_type,
                operation_id,
                request_sha256,
                now_ms,
                now_ms,
            ),
        )
        inserted = cursor.rowcount == 1
        row = connection.execute(
            f"""SELECT {_OPERATION_COLUMNS} FROM operation_dedup
                WHERE actor_user_id=? AND operation_type=? AND operation_id=?""",
            (actor_user_id, operation_type, operation_id),
        ).fetchone()
        if row is None:
            # The primary dedup UUID was reused for a different actor/key.
            raise OperationIdempotencyConflictError(
                "The operation dedup identity is already bound to another command."
            )
        operation = _row_to_operation(row)
        if operation.id != dedup_id or operation.request_sha256 != request_sha256:
            raise OperationIdempotencyConflictError(
                "The operation ID was reused with different command input."
            )
        if not inserted and operation.status in ("committed", "failed_final"):
            # Terminal rows permit last_seen_at-only advancement.  In-progress
            # rows deliberately remain immutable until their terminal commit.
            seen_at = max(operation.last_seen_at_ms, now_ms)
            connection.execute(
                "UPDATE operation_dedup SET last_seen_at=? WHERE id=?",
                (seen_at, operation.id),
            )
            row = connection.execute(
                f"SELECT {_OPERATION_COLUMNS} FROM operation_dedup WHERE id=?",
                (operation.id,),
            ).fetchone()
            if row is None:
                raise UserRepositoryError("The operation row disappeared after retry.")
            operation = _row_to_operation(row)
        return OperationBeginResult(operation=operation, inserted=inserted)

    def get_operation(
        self, connection: ManagedConnection, dedup_id: str
    ) -> OperationDedupRecord | None:
        connection = _require_connection(connection)
        dedup_id = _canonical_uuid(dedup_id, field="dedup_id")
        row = connection.execute(
            f"SELECT {_OPERATION_COLUMNS} FROM operation_dedup WHERE id=?",
            (dedup_id,),
        ).fetchone()
        return None if row is None else _row_to_operation(row)

    def commit_operation(
        self,
        connection: ManagedConnection,
        *,
        dedup_id: str,
        result: Mapping[str, object],
        http_status: int,
        completed_at_ms: int,
    ) -> OperationDedupRecord:
        _require_write_transaction(connection)
        dedup_id = _canonical_uuid(dedup_id, field="dedup_id")
        result_json = _canonical_json_object(result, field="result")
        if (
            isinstance(http_status, bool)
            or not isinstance(http_status, int)
            or not 100 <= http_status <= 599
        ):
            raise ValueError("http_status must be an integer from 100 through 599.")
        completed_at_ms = _utc_ms(completed_at_ms, field="completed_at_ms")
        existing = self.get_operation(connection, dedup_id)
        if existing is None:
            raise UserNotFoundError("The operation idempotency row does not exist.")
        if existing.status == "committed":
            if (
                existing.http_status == http_status
                and existing.result_json == result_json
                and existing.completed_at_ms == completed_at_ms
            ):
                return existing
            raise OperationIdempotencyConflictError(
                "The committed operation result is immutable."
            )
        if existing.status != "in_progress":
            raise OperationIdempotencyConflictError(
                "A failed-final operation cannot be committed."
            )
        cursor = connection.execute(
            """UPDATE operation_dedup
               SET status='committed',http_status=?,result_json=?,error_json=NULL,
                   completed_at=?,last_seen_at=?
               WHERE id=? AND status='in_progress'""",
            (
                http_status,
                result_json,
                completed_at_ms,
                max(existing.last_seen_at_ms, completed_at_ms),
                dedup_id,
            ),
        )
        if cursor.rowcount != 1:
            raise OperationInProgressError(
                "The operation changed before its result could be committed."
            )
        committed = self.get_operation(connection, dedup_id)
        if committed is None:
            raise UserRepositoryError("The committed operation row cannot be read.")
        return committed

    def fail_operation_final(
        self,
        connection: ManagedConnection,
        *,
        dedup_id: str,
        error: Mapping[str, object],
        http_status: int,
        completed_at_ms: int,
    ) -> OperationDedupRecord:
        _require_write_transaction(connection)
        dedup_id = _canonical_uuid(dedup_id, field="dedup_id")
        error_json = _canonical_json_object(error, field="error")
        if (
            isinstance(http_status, bool)
            or not isinstance(http_status, int)
            or not 100 <= http_status <= 599
        ):
            raise ValueError("http_status must be an integer from 100 through 599.")
        completed_at_ms = _utc_ms(completed_at_ms, field="completed_at_ms")
        existing = self.get_operation(connection, dedup_id)
        if existing is None:
            raise UserNotFoundError("The operation idempotency row does not exist.")
        if existing.status == "failed_final":
            if (
                existing.http_status == http_status
                and existing.error_json == error_json
                and existing.completed_at_ms == completed_at_ms
            ):
                return existing
            raise OperationIdempotencyConflictError(
                "The failed-final operation error is immutable."
            )
        if existing.status != "in_progress":
            raise OperationIdempotencyConflictError(
                "A committed operation cannot become failed-final."
            )
        cursor = connection.execute(
            """UPDATE operation_dedup
               SET status='failed_final',http_status=?,result_json=NULL,error_json=?,
                   completed_at=?,last_seen_at=?
               WHERE id=? AND status='in_progress'""",
            (
                http_status,
                error_json,
                completed_at_ms,
                max(existing.last_seen_at_ms, completed_at_ms),
                dedup_id,
            ),
        )
        if cursor.rowcount != 1:
            raise OperationInProgressError(
                "The operation changed before its error could be committed."
            )
        failed = self.get_operation(connection, dedup_id)
        if failed is None:
            raise UserRepositoryError("The failed operation row cannot be read.")
        return failed

    def update_profile(
        self,
        connection: ManagedConnection,
        *,
        user_id: str,
        expected_row_version: int,
        display_name: str,
        system_role: SystemRole,
        updated_at_ms: int,
    ) -> UserRecord:
        _require_write_transaction(connection)
        user_id = _valid_user_id(user_id)
        expected_row_version = _positive_int(
            expected_row_version, field="expected_row_version"
        )
        display_name = _required_text(
            display_name, field="display_name", maximum=120
        )
        if system_role not in ("admin", "user"):
            raise ValueError("system_role must be admin or user.")
        updated_at_ms = _utc_ms(updated_at_ms, field="updated_at_ms")
        cursor = connection.execute(
            """UPDATE app_user
               SET display_name=?,system_role=?,updated_at=?,row_version=row_version+1
               WHERE id=? AND row_version=?""",
            (
                display_name,
                system_role,
                updated_at_ms,
                user_id,
                expected_row_version,
            ),
        )
        if cursor.rowcount != 1:
            self._raise_missing_or_version(connection, user_id)
        return self.require(connection, user_id)

    def update_password_hash(
        self,
        connection: ManagedConnection,
        *,
        user_id: str,
        expected_row_version: int,
        password_hash: str,
        password_change_kind: Literal["admin_reset", "self_change"],
        updated_at_ms: int,
    ) -> UserRecord:
        _require_write_transaction(connection)
        user_id = _valid_user_id(user_id)
        expected_row_version = _positive_int(
            expected_row_version, field="expected_row_version"
        )
        password_hash = _required_text(
            password_hash, field="password_hash", maximum=512
        )
        if password_change_kind not in ("admin_reset", "self_change"):
            raise ValueError(
                "password_change_kind must be admin_reset or self_change."
            )
        updated_at_ms = _utc_ms(updated_at_ms, field="updated_at_ms")
        must_change_password = password_change_kind == "admin_reset"
        password_changed_at_ms = (
            None if must_change_password else updated_at_ms
        )
        cursor = connection.execute(
            """UPDATE app_user
               SET password_hash=?,must_change_password=?,password_changed_at=?,
                   updated_at=?,row_version=row_version+1
               WHERE id=? AND row_version=?""",
            (
                password_hash,
                1 if must_change_password else 0,
                password_changed_at_ms,
                updated_at_ms,
                user_id,
                expected_row_version,
            ),
        )
        if cursor.rowcount != 1:
            self._raise_missing_or_version(connection, user_id)
        return self.require(connection, user_id)

    def transition_account_status(
        self,
        connection: ManagedConnection,
        *,
        user_id: str,
        target_status: AccountStatus,
        expected_row_version: int,
        changed_at_ms: int,
    ) -> AccountStatusTransition:
        """Apply the canonical disable/re-enable storage effects atomically.

        The caller owns the surrounding transaction, operation idempotency,
        administrator and last-admin policy, leader replacement/write-disable
        ordering, audit, outbox, and Flask session handling.  A real status
        transition increments both ``scope_epoch`` and ``row_version``.  Disable
        also clears ``current_part_id`` so re-enable cannot restore a former-part
        capability, clears every held lease with a new fence, and requests
        cancellation of cancellable non-terminal jobs.  Every non-terminal job
        is authorization-invalidated by the epoch change even when it is not
        cancellable.
        """

        _require_write_transaction(connection)
        user_id = _valid_user_id(user_id)
        if target_status not in ("active", "disabled"):
            raise ValueError("target_status must be active or disabled.")
        expected_row_version = _positive_int(
            expected_row_version, field="expected_row_version"
        )
        changed_at_ms = _utc_ms(changed_at_ms, field="changed_at_ms")
        before = self.require(connection, user_id)
        if before.account_status == target_status:
            return AccountStatusTransition(
                user=before,
                changed=False,
                previous_part_id=before.current_part_id,
                invalidated_session_epoch=False,
                revoked_lease_count=0,
                invalidated_job_count=0,
                cancel_requested_job_count=0,
            )
        if before.row_version != expected_row_version:
            raise UserVersionConflictError(
                "The canonical user row version changed before the account transition."
            )
        if target_status == "disabled":
            if self.list_active_leader_assignments(connection, user_id):
                raise UserLeaderGuardError(
                    "The leader role must be replaced or safely closed before this transition."
                )
            if self.get_open_membership(connection, user_id) is not None:
                raise UserMembershipGuardError(
                    "The open part membership must be closed before account disable."
                )
        elif self.get_open_membership(connection, user_id) is not None:
            raise UserMembershipGuardError(
                "A disabled account cannot be re-enabled with an open membership."
            )
        disabled_at = changed_at_ms if target_status == "disabled" else None
        resulting_part_id = None if target_status == "disabled" else before.current_part_id
        try:
            cursor = connection.execute(
                """UPDATE app_user
                   SET account_status=?,disabled_at=?,current_part_id=?,
                       scope_epoch=scope_epoch+1,updated_at=?,row_version=row_version+1
                   WHERE id=? AND row_version=? AND account_status<>?""",
                (
                    target_status,
                    disabled_at,
                    resulting_part_id,
                    changed_at_ms,
                    user_id,
                    expected_row_version,
                    target_status,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "LEADER_WRITE_GUARD" in str(exc):
                raise UserLeaderGuardError(
                    "The leader role must be replaced or safely closed before this transition."
                ) from exc
            raise
        if cursor.rowcount != 1:
            self._raise_missing_or_version(connection, user_id)

        revoked_lease_count = 0
        invalidated_job_count = 0
        cancel_requested_job_count = 0
        if target_status == "disabled":
            lease_cursor = connection.execute(
                """UPDATE item_lease
                   SET holder_user_id=NULL,editor_session_id=NULL,token_hmac=NULL,
                       hmac_key_id=NULL,fencing_generation=fencing_generation+1,
                       base_item_row_version=NULL,base_revision_id=NULL,
                       acquired_at=NULL,heartbeat_at=NULL,expires_at=NULL,
                       absolute_expires_at=NULL
                   WHERE holder_user_id=?""",
                (user_id,),
            )
            revoked_lease_count = max(0, lease_cursor.rowcount)
            placeholders = ",".join("?" for _ in _NONTERMINAL_JOB_STATES)
            job_count_row = connection.execute(
                f"""SELECT count(*) FROM durable_job
                    WHERE actor_user_id=? AND state IN ({placeholders})""",
                (user_id, *_NONTERMINAL_JOB_STATES),
            ).fetchone()
            invalidated_job_count = int(job_count_row[0]) if job_count_row else 0
            job_cursor = connection.execute(
                f"""UPDATE durable_job
                    SET cancel_requested=1,updated_at=?
                    WHERE actor_user_id=? AND state IN ({placeholders})
                      AND cancellable=1 AND cancel_requested=0""",
                (changed_at_ms, user_id, *_NONTERMINAL_JOB_STATES),
            )
            cancel_requested_job_count = max(0, job_cursor.rowcount)

        return AccountStatusTransition(
            user=self.require(connection, user_id),
            changed=True,
            previous_part_id=before.current_part_id,
            invalidated_session_epoch=True,
            revoked_lease_count=revoked_lease_count,
            invalidated_job_count=invalidated_job_count,
            cancel_requested_job_count=cancel_requested_job_count,
        )

    def session_epoch_is_current(
        self,
        connection: ManagedConnection,
        *,
        user_id: str,
        captured_scope_epoch: int,
    ) -> bool:
        connection = _require_connection(connection)
        user_id = _valid_user_id(user_id)
        captured_scope_epoch = _positive_int(
            captured_scope_epoch, field="captured_scope_epoch"
        )
        row = connection.execute(
            """SELECT 1 FROM app_user
               WHERE id=? AND account_status='active' AND scope_epoch=?""",
            (user_id, captured_scope_epoch),
        ).fetchone()
        return row is not None

    def hard_delete(self, connection: ManagedConnection, user_id: str) -> None:
        """Fail closed for every legacy account-deletion adapter.

        Account removal is disable-first.  This method intentionally never
        executes ``DELETE`` even when the user currently has no foreign-key
        references; retained audit/knowledge actors must remain addressable.
        """

        _require_connection(connection)
        user_id = _valid_user_id(user_id)
        self.require(connection, user_id)
        raise UserHardDeleteForbiddenError(
            "Canonical users cannot be hard-deleted; disable the account instead."
        )

    def _raise_missing_or_version(
        self, connection: ManagedConnection, user_id: str
    ) -> None:
        if self.get(connection, user_id) is None:
            raise UserNotFoundError("The requested canonical user does not exist.")
        raise UserVersionConflictError(
            "The canonical user row version changed before the update."
        )


__all__ = [
    "AccountStatus",
    "AccountStatusTransition",
    "ActiveLeaderAssignment",
    "LegacyUserImportResult",
    "LegacyUserRecord",
    "OpenMembershipRecord",
    "OperationBeginResult",
    "OperationDedupRecord",
    "OperationIdempotencyConflictError",
    "OperationInProgressError",
    "PartSpaceWriteState",
    "SystemRole",
    "UserHardDeleteForbiddenError",
    "UserImportConflictError",
    "UserLeaderGuardError",
    "UserMembershipGuardError",
    "UserNotFoundError",
    "UserRecord",
    "UserRepository",
    "UserRepositoryError",
    "UserVersionConflictError",
]
