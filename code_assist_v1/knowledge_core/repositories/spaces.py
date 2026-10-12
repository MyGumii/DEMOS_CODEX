"""Connection-scoped knowledge-space persistence primitives.

P1.6 provisions only personal spaces and spaces owned by active parts.  This
repository deliberately does not authorize a caller, open/commit transactions,
append audit/outbox rows, expose a space to traffic, or enable writes.  A later
service composes these primitives inside one managed transaction.

The ``ensure_*`` methods are get-or-create operations.  Their caller supplies a
stable candidate UUID for a missing row; a canonical row already owned by the
same user/part wins without being mutated.  Reusing the candidate UUID for a
different owner or encountering a mismatched owner/kind fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3
from typing import Literal

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError
from .personal_wikis import (
    DEFAULT_PERSONAL_WIKI_DISPLAY_NAME,
    PersonalWikiProfileRepository,
)


SpaceKind = Literal["personal", "part", "team", "responsibility"]
SpaceStatus = Literal["active", "frozen", "inactive"]

_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)

_SPACE_COLUMNS = """
id,space_kind,owner_user_id,owner_org_unit_id,status,write_enabled,generation,
created_at,updated_at,row_version
""".strip()

_QUALIFIED_SPACE_COLUMNS = """
s.id AS id,s.space_kind AS space_kind,s.owner_user_id AS owner_user_id,
s.owner_org_unit_id AS owner_org_unit_id,s.status AS status,
s.write_enabled AS write_enabled,s.generation AS generation,
s.created_at AS created_at,s.updated_at AS updated_at,s.row_version AS row_version
""".strip()


class SpaceRepositoryError(KnowledgeCoreDatabaseError):
    """Base error for canonical knowledge-space persistence."""

    code = "SPACE_REPOSITORY_ERROR"


class SpaceNotFoundError(SpaceRepositoryError):
    code = "SPACE_NOT_FOUND"


class SpaceOwnerNotFoundError(SpaceRepositoryError):
    code = "SPACE_OWNER_NOT_FOUND"


class SpaceOwnerStateError(SpaceRepositoryError):
    code = "SPACE_OWNER_STATE_INVALID"


class SpaceIdentityConflictError(SpaceRepositoryError):
    """A supplied ID or owner is already bound to an incompatible space."""

    code = "SPACE_IDENTITY_CONFLICT"


class SpaceVersionConflictError(SpaceRepositoryError):
    code = "SPACE_VERSION_CONFLICT"


class SpaceStateConflictError(SpaceRepositoryError):
    code = "SPACE_STATE_CONFLICT"


@dataclass(frozen=True, slots=True)
class SpaceRecord:
    id: str
    space_kind: SpaceKind
    owner_user_id: str | None
    owner_org_unit_id: str | None
    status: SpaceStatus
    write_enabled: bool
    generation: int
    created_at_ms: int
    updated_at_ms: int
    row_version: int


@dataclass(frozen=True, slots=True)
class EnsureSpaceResult:
    space: SpaceRecord
    created: bool


@dataclass(frozen=True, slots=True)
class SpaceProvisioningReport:
    total_user_count: int
    personal_space_count: int
    missing_personal_count: int
    active_part_count: int
    active_part_space_count: int
    total_part_space_count: int
    missing_part_count: int
    higher_space_count: int

    @property
    def complete(self) -> bool:
        return (
            self.missing_personal_count == 0
            and self.missing_part_count == 0
            and self.active_part_count == self.active_part_space_count
        )


def _require_connection(connection: ManagedConnection) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("SpaceRepository requires a ManagedConnection.")
    return connection


def _require_write_transaction(connection: ManagedConnection) -> ManagedConnection:
    connection = _require_connection(connection)
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Space repository writes require an existing managed transaction."
        )
    return connection


def _canonical_uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _valid_user_id(value: object, *, field: str = "user_id") -> str:
    if not isinstance(value, str) or _USER_ID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must use the canonical lowercase login format.")
    return value


def _utc_ms(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative UTC millisecond integer.")
    return value


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _space_status(value: object) -> SpaceStatus:
    if value not in ("active", "frozen", "inactive"):
        raise ValueError("status must be active, frozen, or inactive.")
    return value  # type: ignore[return-value]


def _optional_limit(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("limit must be a positive integer or null.")
    return value


def _row_value(row: object, key: str) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, TypeError, IndexError) as exc:
        raise SpaceRepositoryError(
            "Canonical knowledge-space row mapping failed."
        ) from exc


def _row_to_space(row: object) -> SpaceRecord:
    try:
        space_id = _canonical_uuid(_row_value(row, "id"), field="space_id")
        kind = _row_value(row, "space_kind")
        if kind not in ("personal", "part", "team", "responsibility"):
            raise ValueError("space_kind is invalid")
        status = _row_value(row, "status")
        if status not in ("active", "frozen", "inactive"):
            raise ValueError("space status is invalid")

        owner_user_value = _row_value(row, "owner_user_id")
        owner_org_value = _row_value(row, "owner_org_unit_id")
        owner_user_id = (
            None
            if owner_user_value is None
            else _valid_user_id(owner_user_value, field="owner_user_id")
        )
        owner_org_unit_id = (
            None
            if owner_org_value is None
            else _canonical_uuid(owner_org_value, field="owner_org_unit_id")
        )
        if kind == "personal":
            if owner_user_id is None or owner_org_unit_id is not None:
                raise ValueError("personal owner shape is invalid")
        elif owner_user_id is not None or owner_org_unit_id is None:
            raise ValueError("organization owner shape is invalid")

        stored_write_enabled = _row_value(row, "write_enabled")
        if type(stored_write_enabled) is not int or stored_write_enabled not in (0, 1):
            raise ValueError("write_enabled is not a canonical Boolean")
        generation = _row_value(row, "generation")
        created_at = _row_value(row, "created_at")
        updated_at = _row_value(row, "updated_at")
        row_version = _row_value(row, "row_version")
        if type(generation) is not int or generation < 0:
            raise ValueError("generation is invalid")
        if type(created_at) is not int or created_at < 0:
            raise ValueError("created_at is invalid")
        if type(updated_at) is not int or updated_at < 0:
            raise ValueError("updated_at is invalid")
        if type(row_version) is not int or row_version < 1:
            raise ValueError("row_version is invalid")
        return SpaceRecord(
            id=space_id,
            space_kind=kind,  # type: ignore[arg-type]
            owner_user_id=owner_user_id,
            owner_org_unit_id=owner_org_unit_id,
            status=status,  # type: ignore[arg-type]
            write_enabled=bool(stored_write_enabled),
            generation=generation,
            created_at_ms=created_at,
            updated_at_ms=updated_at,
            row_version=row_version,
        )
    except (TypeError, ValueError) as exc:
        raise SpaceRepositoryError(
            "Canonical knowledge-space row mapping failed."
        ) from exc


def _initial_shape(space: SpaceRecord) -> bool:
    return (
        space.status == "active"
        and not space.write_enabled
        and space.generation == 0
        and space.row_version == 1
        and space.created_at_ms == space.updated_at_ms
    )


def _translate_integrity(exc: sqlite3.IntegrityError) -> None:
    message = str(exc)
    if any(
        marker in message
        for marker in (
            "SPACE_OWNER_INVALID",
            "SPACE_KIND_DISABLED",
            "FOREIGN KEY constraint failed",
            "UNIQUE constraint failed",
        )
    ):
        raise SpaceIdentityConflictError(
            "The knowledge-space identity or owner is already bound incompatibly."
        ) from exc
    raise SpaceRepositoryError(
        "The knowledge-space insert violates a canonical constraint."
    ) from exc


class SpaceRepository:
    """SQL and row mapping for personal/part space provisioning."""

    __slots__ = ()

    def get_by_id(
        self, connection: ManagedConnection, space_id: str
    ) -> SpaceRecord | None:
        connection = _require_connection(connection)
        space_id = _canonical_uuid(space_id, field="space_id")
        row = connection.execute(
            f"SELECT {_SPACE_COLUMNS} FROM knowledge_space WHERE id=?",
            (space_id,),
        ).fetchone()
        return None if row is None else _row_to_space(row)

    def get_personal(
        self, connection: ManagedConnection, user_id: str
    ) -> SpaceRecord | None:
        connection = _require_connection(connection)
        user_id = _valid_user_id(user_id)
        profile_schema = connection.execute(
            """SELECT 1 FROM sqlite_schema
               WHERE type='table' AND name='personal_wiki_profile'"""
        ).fetchone()
        if profile_schema is not None:
            row = connection.execute(
                f"""SELECT {_QUALIFIED_SPACE_COLUMNS}
                    FROM knowledge_space s
                    JOIN personal_wiki_profile p ON p.space_id=s.id
                    WHERE s.space_kind='personal' AND s.owner_user_id=?
                      AND p.owner_user_id=? AND p.lifecycle_status='active'
                      AND p.is_default=1
                    ORDER BY s.id LIMIT 1""",
                (user_id, user_id),
            ).fetchone()
            if row is None and connection.execute(
                """SELECT 1 FROM knowledge_space
                   WHERE space_kind='personal' AND owner_user_id=? LIMIT 1""",
                (user_id,),
            ).fetchone() is not None:
                raise SpaceStateConflictError(
                    "The owner has personal spaces but no exact active default Wiki."
                )
            return None if row is None else _row_to_space(row)
        row = connection.execute(
            f"""SELECT {_SPACE_COLUMNS} FROM knowledge_space
                WHERE space_kind='personal' AND owner_user_id=?""",
            (user_id,),
        ).fetchone()
        return None if row is None else _row_to_space(row)

    def get_org(
        self, connection: ManagedConnection, org_unit_id: str
    ) -> SpaceRecord | None:
        connection = _require_connection(connection)
        org_unit_id = _canonical_uuid(org_unit_id, field="org_unit_id")
        row = connection.execute(
            f"""SELECT {_SPACE_COLUMNS} FROM knowledge_space
                WHERE space_kind<>'personal' AND owner_org_unit_id=?""",
            (org_unit_id,),
        ).fetchone()
        return None if row is None else _row_to_space(row)

    def get_part(
        self, connection: ManagedConnection, part_id: str
    ) -> SpaceRecord | None:
        connection = _require_connection(connection)
        part_id = _canonical_uuid(part_id, field="part_id")
        row = connection.execute(
            f"""SELECT {_SPACE_COLUMNS} FROM knowledge_space
                WHERE space_kind='part' AND owner_org_unit_id=?""",
            (part_id,),
        ).fetchone()
        return None if row is None else _row_to_space(row)

    def require_by_id(
        self, connection: ManagedConnection, space_id: str
    ) -> SpaceRecord:
        space = self.get_by_id(connection, space_id)
        if space is None:
            raise SpaceNotFoundError("The knowledge space does not exist.")
        return space

    def list_missing_personal_owner_ids(
        self,
        connection: ManagedConnection,
        *,
        limit: int | None = None,
    ) -> tuple[str, ...]:
        connection = _require_connection(connection)
        limit = _optional_limit(limit)
        sql = """SELECT user.id
                 FROM app_user AS user
                 WHERE NOT EXISTS (
                   SELECT 1 FROM knowledge_space AS space
                   WHERE space.space_kind='personal'
                     AND space.owner_user_id=user.id
                 )
                 ORDER BY user.id"""
        parameters: tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            parameters = (limit,)
        rows = connection.execute(sql, parameters).fetchall()
        return tuple(_valid_user_id(row[0]) for row in rows)

    def list_missing_part_owner_ids(
        self,
        connection: ManagedConnection,
        *,
        limit: int | None = None,
    ) -> tuple[str, ...]:
        connection = _require_connection(connection)
        limit = _optional_limit(limit)
        sql = """SELECT unit.id
                 FROM org_unit AS unit
                 WHERE unit.kind='part' AND unit.status='active'
                   AND NOT EXISTS (
                     SELECT 1 FROM knowledge_space AS space
                     WHERE space.owner_org_unit_id=unit.id
                   )
                 ORDER BY unit.id"""
        parameters: tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            parameters = (limit,)
        rows = connection.execute(sql, parameters).fetchall()
        return tuple(_canonical_uuid(row[0], field="part_id") for row in rows)

    def provisioning_report(
        self, connection: ManagedConnection
    ) -> SpaceProvisioningReport:
        connection = _require_connection(connection)
        row = connection.execute(
            """SELECT
                 (SELECT count(*) FROM app_user) AS total_user_count,
                 (SELECT count(*) FROM knowledge_space
                    WHERE space_kind='personal') AS personal_space_count,
                 (SELECT count(*) FROM app_user AS user
                    WHERE NOT EXISTS (
                      SELECT 1 FROM knowledge_space AS space
                      WHERE space.space_kind='personal'
                        AND space.owner_user_id=user.id
                    )) AS missing_personal_count,
                 (SELECT count(*) FROM org_unit
                    WHERE kind='part' AND status='active') AS active_part_count,
                 (SELECT count(*) FROM knowledge_space AS space
                    JOIN org_unit AS unit
                      ON unit.id=space.owner_org_unit_id
                   WHERE space.space_kind='part'
                     AND unit.kind='part' AND unit.status='active')
                    AS active_part_space_count,
                 (SELECT count(*) FROM knowledge_space
                    WHERE space_kind='part') AS total_part_space_count,
                 (SELECT count(*) FROM org_unit AS unit
                    WHERE unit.kind='part' AND unit.status='active'
                      AND NOT EXISTS (
                        SELECT 1 FROM knowledge_space AS space
                        WHERE space.owner_org_unit_id=unit.id
                      )) AS missing_part_count,
                 (SELECT count(*) FROM knowledge_space
                    WHERE space_kind IN ('team','responsibility'))
                    AS higher_space_count"""
        ).fetchone()
        if row is None:
            raise SpaceRepositoryError("The space provisioning report is unavailable.")
        try:
            values = tuple(int(row[index]) for index in range(8))
        except (TypeError, ValueError, IndexError) as exc:
            raise SpaceRepositoryError(
                "The space provisioning report cannot be mapped."
            ) from exc
        if any(value < 0 for value in values):
            raise SpaceRepositoryError("The space provisioning report is invalid.")
        return SpaceProvisioningReport(*values)

    def update_state(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        expected_row_version: int,
        status: SpaceStatus,
        write_enabled: bool,
        updated_at_ms: int,
    ) -> SpaceRecord:
        """Compare-and-swap lifecycle/write state without changing ownership.

        Schema triggers remain the final authority for leader/write and
        higher-space rules.  A semantic no-op still validates the supplied row
        version and returns the existing record without incrementing it.
        """

        connection = _require_write_transaction(connection)
        space_id = _canonical_uuid(space_id, field="space_id")
        expected_row_version = _positive_int(
            expected_row_version, field="expected_row_version"
        )
        status = _space_status(status)
        if not isinstance(write_enabled, bool):
            raise ValueError("write_enabled must be a Boolean.")
        if write_enabled and status != "active":
            raise ValueError("Only an active space can enable writes.")
        updated_at_ms = _utc_ms(updated_at_ms, field="updated_at_ms")

        current = self.require_by_id(connection, space_id)
        if current.row_version != expected_row_version:
            raise SpaceVersionConflictError(
                "The knowledge-space row version changed before the update."
            )
        if current.status == status and current.write_enabled == write_enabled:
            return current
        try:
            cursor = connection.execute(
                """UPDATE knowledge_space
                   SET status=?,write_enabled=?,updated_at=?,
                       row_version=row_version+1
                   WHERE id=? AND row_version=?""",
                (
                    status,
                    1 if write_enabled else 0,
                    updated_at_ms,
                    space_id,
                    expected_row_version,
                ),
            )
        except sqlite3.IntegrityError as exc:
            message = str(exc)
            if any(
                marker in message
                for marker in (
                    "SPACE_KIND_DISABLED",
                    "SPACE_OWNER_INVALID",
                    "LEADER_WRITE_GUARD",
                )
            ):
                raise SpaceStateConflictError(
                    "The requested knowledge-space state is not currently valid."
                ) from exc
            if "ROW_VERSION_CONFLICT" in message:
                raise SpaceVersionConflictError(
                    "The knowledge-space row version changed before the update."
                ) from exc
            raise SpaceRepositoryError(
                "The knowledge-space state update violates a canonical constraint."
            ) from exc
        if cursor.rowcount != 1:
            if self.get_by_id(connection, space_id) is None:
                raise SpaceNotFoundError("The knowledge space does not exist.")
            raise SpaceVersionConflictError(
                "The knowledge-space row version changed before the update."
            )
        return self.require_by_id(connection, space_id)

    def ensure_personal(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        user_id: str,
        created_at_ms: int,
    ) -> EnsureSpaceResult:
        connection = _require_write_transaction(connection)
        space_id = _canonical_uuid(space_id, field="space_id")
        user_id = _valid_user_id(user_id)
        created_at_ms = _utc_ms(created_at_ms, field="created_at_ms")

        candidate = self.get_by_id(connection, space_id)
        if candidate is not None:
            if not (
                candidate.space_kind == "personal"
                and candidate.owner_user_id == user_id
                and candidate.owner_org_unit_id is None
            ):
                raise SpaceIdentityConflictError(
                    "The supplied space ID is already bound to another owner or kind."
                )

        existing = self.get_personal(connection, user_id)
        if existing is not None:
            return EnsureSpaceResult(space=existing, created=False)
        if candidate is not None:
            raise SpaceStateConflictError(
                "The supplied personal space is not the owner's active default Wiki."
            )

        owner = connection.execute(
            "SELECT account_status FROM app_user WHERE id=?", (user_id,)
        ).fetchone()
        if owner is None:
            raise SpaceOwnerNotFoundError("The personal-space owner does not exist.")
        if owner[0] not in ("active", "disabled"):
            raise SpaceOwnerStateError("The personal-space owner state is invalid.")

        try:
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'personal',?,NULL,'active',0,0,?,?,1)""",
                (space_id, user_id, created_at_ms, created_at_ms),
            )
        except sqlite3.IntegrityError as exc:
            winner = self._resolve_personal_insert_conflict(
                connection, exc=exc, space_id=space_id, user_id=user_id
            )
            return EnsureSpaceResult(space=winner, created=False)
        inserted = self.get_by_id(connection, space_id)
        if inserted is None or inserted.owner_user_id != user_id:
            raise SpaceRepositoryError(
                "The provisioned personal space cannot be read back."
            )
        if not _initial_shape(inserted):
            raise SpaceRepositoryError(
                "The new personal space does not have the canonical initial shape."
            )
        profile_schema = connection.execute(
            """SELECT 1 FROM sqlite_schema
               WHERE type='table' AND name='personal_wiki_profile'"""
        ).fetchone()
        if profile_schema is not None:
            PersonalWikiProfileRepository().insert_initial_profile(
                connection,
                space_id=space_id,
                owner_user_id=user_id,
                display_name=DEFAULT_PERSONAL_WIKI_DISPLAY_NAME,
                is_default=True,
                created_at_ms=created_at_ms,
            )
        return EnsureSpaceResult(space=inserted, created=True)

    def ensure_part(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        part_id: str,
        created_at_ms: int,
    ) -> EnsureSpaceResult:
        connection = _require_write_transaction(connection)
        space_id = _canonical_uuid(space_id, field="space_id")
        part_id = _canonical_uuid(part_id, field="part_id")
        created_at_ms = _utc_ms(created_at_ms, field="created_at_ms")

        owner = connection.execute(
            "SELECT kind,status FROM org_unit WHERE id=?", (part_id,)
        ).fetchone()
        if owner is None:
            raise SpaceOwnerNotFoundError("The part-space owner does not exist.")
        if owner[0] != "part" or owner[1] != "active":
            raise SpaceOwnerStateError(
                "A part space can be provisioned only for an active part."
            )

        candidate = self.get_by_id(connection, space_id)
        if candidate is not None:
            if (
                candidate.space_kind == "part"
                and candidate.owner_org_unit_id == part_id
                and candidate.owner_user_id is None
            ):
                return EnsureSpaceResult(space=candidate, created=False)
            raise SpaceIdentityConflictError(
                "The supplied space ID is already bound to another owner or kind."
            )

        existing = self.get_org(connection, part_id)
        if existing is not None:
            if existing.space_kind != "part":
                raise SpaceIdentityConflictError(
                    "The organization owner is bound to another space kind."
                )
            return EnsureSpaceResult(space=existing, created=False)

        try:
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'part',NULL,?,'active',0,0,?,?,1)""",
                (space_id, part_id, created_at_ms, created_at_ms),
            )
        except sqlite3.IntegrityError as exc:
            winner = self._resolve_part_insert_conflict(
                connection, exc=exc, space_id=space_id, part_id=part_id
            )
            return EnsureSpaceResult(space=winner, created=False)
        inserted = self.get_by_id(connection, space_id)
        if inserted is None or inserted.owner_org_unit_id != part_id:
            raise SpaceRepositoryError("The provisioned part space cannot be read back.")
        if not _initial_shape(inserted):
            raise SpaceRepositoryError(
                "The new part space does not have the canonical initial shape."
            )
        return EnsureSpaceResult(space=inserted, created=True)

    def ensure_higher(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        org_unit_id: str,
        kind: Literal["team", "responsibility"],
        created_at_ms: int,
    ) -> EnsureSpaceResult:
        """Provision one exact write-disabled Team/Responsibility native space."""

        connection = _require_write_transaction(connection)
        space_id = _canonical_uuid(space_id, field="space_id")
        org_unit_id = _canonical_uuid(org_unit_id, field="org_unit_id")
        if kind not in {"team", "responsibility"}:
            raise ValueError("kind must be team or responsibility.")
        created_at_ms = _utc_ms(created_at_ms, field="created_at_ms")
        owner = connection.execute(
            "SELECT kind,status FROM org_unit WHERE id=?", (org_unit_id,)
        ).fetchone()
        if owner is None:
            raise SpaceOwnerNotFoundError("The higher-space owner does not exist.")
        if owner[0] != kind or owner[1] != "active":
            raise SpaceOwnerStateError(
                "A higher native space requires an exact active organization owner."
            )
        candidate = self.get_by_id(connection, space_id)
        if candidate is not None:
            if (
                candidate.space_kind == kind
                and candidate.owner_org_unit_id == org_unit_id
                and candidate.owner_user_id is None
            ):
                return EnsureSpaceResult(space=candidate, created=False)
            raise SpaceIdentityConflictError(
                "The supplied space ID is already bound to another owner or kind."
            )
        existing = self.get_org(connection, org_unit_id)
        if existing is not None:
            if existing.space_kind != kind:
                raise SpaceIdentityConflictError(
                    "The organization owner is bound to another space kind."
                )
            return EnsureSpaceResult(space=existing, created=False)
        try:
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,?,NULL,?,'active',0,0,?,?,1)""",
                (space_id, kind, org_unit_id, created_at_ms, created_at_ms),
            )
        except sqlite3.IntegrityError as exc:
            candidate = self.get_by_id(connection, space_id)
            existing = self.get_org(connection, org_unit_id)
            if (
                candidate is not None
                and candidate.space_kind == kind
                and candidate.owner_org_unit_id == org_unit_id
            ):
                return EnsureSpaceResult(space=candidate, created=False)
            if existing is not None and existing.space_kind == kind and candidate is None:
                return EnsureSpaceResult(space=existing, created=False)
            _translate_integrity(exc)
            raise AssertionError("Integrity translation must raise.")  # pragma: no cover
        inserted = self.get_by_id(connection, space_id)
        if inserted is None or inserted.owner_org_unit_id != org_unit_id:
            raise SpaceRepositoryError(
                "The provisioned higher native space cannot be read back."
            )
        if not _initial_shape(inserted):
            raise SpaceRepositoryError(
                "The new higher native space does not have the canonical initial shape."
            )
        return EnsureSpaceResult(space=inserted, created=True)

    def _resolve_personal_insert_conflict(
        self,
        connection: ManagedConnection,
        *,
        exc: sqlite3.IntegrityError,
        space_id: str,
        user_id: str,
    ) -> SpaceRecord:
        candidate = self.get_by_id(connection, space_id)
        existing = self.get_personal(connection, user_id)
        if (
            candidate is not None
            and candidate.space_kind == "personal"
            and candidate.owner_user_id == user_id
        ):
            return candidate
        if existing is not None and candidate is None:
            return existing
        _translate_integrity(exc)
        raise AssertionError("Integrity translation must raise.")  # pragma: no cover

    def _resolve_part_insert_conflict(
        self,
        connection: ManagedConnection,
        *,
        exc: sqlite3.IntegrityError,
        space_id: str,
        part_id: str,
    ) -> SpaceRecord:
        candidate = self.get_by_id(connection, space_id)
        existing = self.get_org(connection, part_id)
        if (
            candidate is not None
            and candidate.space_kind == "part"
            and candidate.owner_org_unit_id == part_id
        ):
            return candidate
        if (
            existing is not None
            and existing.space_kind == "part"
            and candidate is None
        ):
            return existing
        _translate_integrity(exc)
        raise AssertionError("Integrity translation must raise.")  # pragma: no cover


__all__ = [
    "EnsureSpaceResult",
    "SpaceIdentityConflictError",
    "SpaceKind",
    "SpaceNotFoundError",
    "SpaceOwnerNotFoundError",
    "SpaceOwnerStateError",
    "SpaceProvisioningReport",
    "SpaceRecord",
    "SpaceRepository",
    "SpaceRepositoryError",
    "SpaceStateConflictError",
    "SpaceStatus",
    "SpaceVersionConflictError",
]
