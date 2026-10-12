"""Canonical v13 direct organization-home affiliation primitives."""

from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3
from typing import Literal

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError


HomeKind = Literal["part", "team", "responsibility"]
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)


class OrgAffiliationRepositoryError(KnowledgeCoreDatabaseError):
    code = "ORG_AFFILIATION_REPOSITORY_ERROR"


class OrgAffiliationStateConflictError(OrgAffiliationRepositoryError):
    code = "ORG_AFFILIATION_STATE_CONFLICT"


@dataclass(frozen=True, slots=True)
class OrgHomeAffiliationRecord:
    id: str
    user_id: str
    org_unit_id: str
    home_kind: HomeKind
    valid_from_ms: int
    valid_to_ms: int | None
    assigned_by_user_id: str
    reason: str
    row_version: int


_COLUMNS = """id,user_id,org_unit_id,home_kind,valid_from,valid_to,
assigned_by,reason,row_version"""


def _connection(value: object, *, write: bool = False) -> ManagedConnection:
    if not isinstance(value, ManagedConnection):
        raise TypeError("OrgAffiliationRepository requires a ManagedConnection.")
    if write and not value.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Organization-affiliation writes require an active transaction."
        )
    return value


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _user(value: object, field: str) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical user ID.")
    return value


def _time(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer.")
    return value


def _positive(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _reason(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not 1 <= len(value) <= 1000:
        raise ValueError("reason must be bounded trimmed text.")
    return value


def _record(row: object) -> OrgHomeAffiliationRecord:
    try:
        kind = row["home_kind"]  # type: ignore[index]
        if kind not in ("part", "team", "responsibility"):
            raise ValueError("invalid home kind")
        valid_from = _time(row["valid_from"], "valid_from")  # type: ignore[index]
        raw_valid_to = row["valid_to"]  # type: ignore[index]
        valid_to = None if raw_valid_to is None else _time(raw_valid_to, "valid_to")
        row_version = _positive(row["row_version"], "row_version")  # type: ignore[index]
        if (valid_to is None and row_version != 1) or (
            valid_to is not None and (valid_to <= valid_from or row_version != 2)
        ):
            raise ValueError("invalid affiliation lifecycle")
        return OrgHomeAffiliationRecord(
            id=_uuid(row["id"], "id"),  # type: ignore[index]
            user_id=_user(row["user_id"], "user_id"),  # type: ignore[index]
            org_unit_id=_uuid(row["org_unit_id"], "org_unit_id"),  # type: ignore[index]
            home_kind=kind,
            valid_from_ms=valid_from,
            valid_to_ms=valid_to,
            assigned_by_user_id=_user(row["assigned_by"], "assigned_by"),  # type: ignore[index]
            reason=_reason(row["reason"]),  # type: ignore[index]
            row_version=row_version,
        )
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise OrgAffiliationRepositoryError(
            "Canonical organization-affiliation row mapping failed."
        ) from exc


class OrgAffiliationRepository:
    __slots__ = ()

    def get_current(
        self, connection: ManagedConnection, user_id: str
    ) -> OrgHomeAffiliationRecord | None:
        connection = _connection(connection)
        user_id = _user(user_id, "user_id")
        rows = connection.execute(
            f"""SELECT {_COLUMNS} FROM org_home_affiliation
                WHERE user_id=? AND valid_to IS NULL ORDER BY id LIMIT 2""",
            (user_id,),
        ).fetchall()
        if len(rows) > 1:
            raise OrgAffiliationRepositoryError(
                "A user has multiple open organization affiliations."
            )
        return None if not rows else _record(rows[0])

    def list_current(
        self, connection: ManagedConnection
    ) -> tuple[OrgHomeAffiliationRecord, ...]:
        connection = _connection(connection)
        rows = connection.execute(
            f"""SELECT {_COLUMNS} FROM org_home_affiliation
                WHERE valid_to IS NULL ORDER BY home_kind,org_unit_id,user_id,id"""
        ).fetchall()
        return tuple(_record(row) for row in rows)

    def open_direct(
        self,
        connection: ManagedConnection,
        *,
        affiliation_id: str,
        user_id: str,
        org_unit_id: str,
        home_kind: Literal["team", "responsibility"],
        assigned_by_user_id: str,
        reason: str,
        valid_from_ms: int,
    ) -> OrgHomeAffiliationRecord:
        connection = _connection(connection, write=True)
        affiliation_id = _uuid(affiliation_id, "affiliation_id")
        user_id = _user(user_id, "user_id")
        org_unit_id = _uuid(org_unit_id, "org_unit_id")
        assigned_by_user_id = _user(assigned_by_user_id, "assigned_by_user_id")
        reason = _reason(reason)
        valid_from_ms = _time(valid_from_ms, "valid_from_ms")
        if home_kind not in ("team", "responsibility"):
            raise ValueError("A direct affiliation must target Team or Responsibility.")
        if self.get_current(connection, user_id) is not None:
            raise OrgAffiliationStateConflictError(
                "The user already has an active organization affiliation."
            )
        try:
            connection.execute(
                """INSERT INTO org_home_affiliation(
                     id,user_id,org_unit_id,home_kind,valid_from,valid_to,
                     assigned_by,reason,row_version
                   ) VALUES (?,?,?,?,?,NULL,?,?,1)""",
                (
                    affiliation_id,
                    user_id,
                    org_unit_id,
                    home_kind,
                    valid_from_ms,
                    assigned_by_user_id,
                    reason,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise OrgAffiliationStateConflictError(
                "The direct organization affiliation conflicts with current state."
            ) from exc
        opened = self.get_current(connection, user_id)
        if opened is None or opened.id != affiliation_id:
            raise OrgAffiliationRepositoryError(
                "The opened organization affiliation disappeared."
            )
        return opened

    def close_direct(
        self,
        connection: ManagedConnection,
        *,
        affiliation_id: str,
        user_id: str,
        closed_at_ms: int,
    ) -> OrgHomeAffiliationRecord:
        connection = _connection(connection, write=True)
        affiliation_id = _uuid(affiliation_id, "affiliation_id")
        user_id = _user(user_id, "user_id")
        current = self.get_current(connection, user_id)
        if current is None or current.id != affiliation_id or current.home_kind == "part":
            raise OrgAffiliationStateConflictError(
                "The direct organization affiliation changed before close."
            )
        closed_at_ms = _time(closed_at_ms, "closed_at_ms")
        if closed_at_ms <= current.valid_from_ms:
            raise ValueError("closed_at_ms must follow the affiliation start.")
        try:
            cursor = connection.execute(
                """UPDATE org_home_affiliation
                      SET valid_to=?,row_version=2
                    WHERE id=? AND user_id=? AND valid_to IS NULL AND row_version=1""",
                (closed_at_ms, affiliation_id, user_id),
            )
        except sqlite3.IntegrityError as exc:
            raise OrgAffiliationStateConflictError(
                "The direct organization affiliation cannot be closed."
            ) from exc
        if cursor.rowcount != 1:
            raise OrgAffiliationStateConflictError(
                "The direct organization affiliation changed before close."
            )
        row = connection.execute(
            f"SELECT {_COLUMNS} FROM org_home_affiliation WHERE id=?",
            (affiliation_id,),
        ).fetchone()
        if row is None:
            raise OrgAffiliationRepositoryError(
                "The closed organization affiliation disappeared."
            )
        return _record(row)

    def bump_direct_scope(
        self,
        connection: ManagedConnection,
        *,
        user_id: str,
        expected_scope_epoch: int,
        expected_row_version: int,
        updated_at_ms: int,
    ) -> tuple[int, int]:
        connection = _connection(connection, write=True)
        user_id = _user(user_id, "user_id")
        expected_scope_epoch = _positive(expected_scope_epoch, "expected_scope_epoch")
        expected_row_version = _positive(expected_row_version, "expected_row_version")
        updated_at_ms = _time(updated_at_ms, "updated_at_ms")
        try:
            cursor = connection.execute(
                """UPDATE app_user
                      SET scope_epoch=scope_epoch+1,updated_at=?,row_version=row_version+1
                    WHERE id=? AND account_status='active' AND current_part_id IS NULL
                      AND scope_epoch=? AND row_version=?""",
                (updated_at_ms, user_id, expected_scope_epoch, expected_row_version),
            )
        except sqlite3.IntegrityError as exc:
            raise OrgAffiliationStateConflictError(
                "The direct-affiliation scope fence cannot advance."
            ) from exc
        if cursor.rowcount != 1:
            raise OrgAffiliationStateConflictError(
                "The direct-affiliation scope fence changed before update."
            )
        row = connection.execute(
            "SELECT scope_epoch,row_version FROM app_user WHERE id=?",
            (user_id,),
        ).fetchone()
        if row is None:
            raise OrgAffiliationRepositoryError(
                "The direct-affiliation user disappeared."
            )
        return (_positive(row[0], "scope_epoch"), _positive(row[1], "row_version"))


__all__ = [
    "HomeKind",
    "OrgAffiliationRepository",
    "OrgAffiliationRepositoryError",
    "OrgAffiliationStateConflictError",
    "OrgHomeAffiliationRecord",
]
