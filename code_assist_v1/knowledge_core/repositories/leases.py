"""Persistent fenced edit-lease SQL boundary.

The repository owns no connection or transaction.  Every mutation requires a
caller-owned managed write transaction and uses compare-and-swap SQL against
the never-deleted ``item_lease`` row.  Raw lease tokens never cross this
boundary; callers provide only a 32-byte keyed digest.
"""

from __future__ import annotations

from dataclasses import dataclass
from hmac import compare_digest
import re
from typing import Literal

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_MAX_SQLITE_INTEGER = (1 << 63) - 1

LeaseAcquireOutcome = Literal["acquired", "reclaimed_expired"]


class LeaseRepositoryError(KnowledgeCoreDatabaseError):
    code = "LEASE_REPOSITORY_ERROR"


class LeaseItemNotFoundError(LeaseRepositoryError):
    code = "LEASE_ITEM_NOT_FOUND"


class LeaseHeldRepositoryError(LeaseRepositoryError):
    code = "LEASE_HELD"

    def __init__(self, message: str, *, lease: "LeaseRecord") -> None:
        super().__init__(message)
        self.lease = lease


class LeaseLostRepositoryError(LeaseRepositoryError):
    code = "LEASE_LOST"


class LeaseRevisionConflictRepositoryError(LeaseRepositoryError):
    code = "REVISION_CONFLICT"

    def __init__(
        self,
        message: str,
        *,
        current_revision_id: str,
        current_row_version: int,
    ) -> None:
        super().__init__(message)
        self.current_revision_id = current_revision_id
        self.current_row_version = current_row_version


class LeaseStateConflictRepositoryError(LeaseRepositoryError):
    code = "STATE_CONFLICT"


class LeaseIntegrityError(LeaseRepositoryError):
    code = "LEASE_INTEGRITY_ERROR"


@dataclass(frozen=True, slots=True)
class LeaseRecord:
    item_id: str
    holder_user_id: str | None
    editor_session_id: str | None
    token_hmac: bytes | None
    hmac_key_id: str | None
    fencing_generation: int
    base_item_row_version: int | None
    base_revision_id: str | None
    acquired_at_ms: int | None
    heartbeat_at_ms: int | None
    expires_at_ms: int | None
    absolute_expires_at_ms: int | None

    @property
    def held(self) -> bool:
        return self.holder_user_id is not None

    def expired(self, now_ms: int) -> bool:
        now_ms = _utc_ms(now_ms, field_name="now_ms")
        return bool(
            self.held
            and (
                self.expires_at_ms is None
                or self.absolute_expires_at_ms is None
                or now_ms >= self.expires_at_ms
                or now_ms >= self.absolute_expires_at_ms
            )
        )


@dataclass(frozen=True, slots=True)
class LeasableItemRecord:
    item_id: str
    space_id: str
    lifecycle_status: str
    current_revision_id: str
    row_version: int
    lease: LeaseRecord


@dataclass(frozen=True, slots=True)
class LeaseAcquireWriteResult:
    item: LeasableItemRecord
    outcome: LeaseAcquireOutcome


@dataclass(frozen=True, slots=True)
class LeaseReleaseWriteResult:
    item: LeasableItemRecord
    released: LeaseRecord


@dataclass(frozen=True, slots=True)
class LeaseValidationProof:
    item_id: str
    space_id: str
    holder_user_id: str
    editor_session_id: str
    fencing_generation: int
    base_item_row_version: int
    base_revision_id: str
    current_item_row_version: int
    current_revision_id: str
    expires_at_ms: int
    absolute_expires_at_ms: int


_LEASE_COLUMNS = (
    "item_id,holder_user_id,editor_session_id,token_hmac,hmac_key_id,"
    "fencing_generation,base_item_row_version,base_revision_id,acquired_at,"
    "heartbeat_at,expires_at,absolute_expires_at"
)


def _connection(value: object) -> ManagedConnection:
    if not isinstance(value, ManagedConnection):
        raise TypeError("LeaseRepository requires a ManagedConnection.")
    return value


def _write_transaction(value: object) -> ManagedConnection:
    connection = _connection(value)
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Lease mutation requires an existing managed transaction."
        )
    return connection


def _uuid(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a canonical lowercase UUID.")
    return value


def _user_id(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _USER_ID_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a canonical user ID.")
    return value


def _required_text(value: object, *, field_name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text.")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{field_name} must contain 1..{maximum} characters.")
    return normalized


def _positive_int(value: object, *, field_name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= _MAX_SQLITE_INTEGER
    ):
        raise ValueError(f"{field_name} must be a positive SQLite integer.")
    return value


def _utc_ms(value: object, *, field_name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_SQLITE_INTEGER
    ):
        raise ValueError(f"{field_name} must be a non-negative SQLite integer.")
    return value


def _digest(value: object) -> bytes:
    if not isinstance(value, bytes) or len(value) != 32:
        raise ValueError("token_hmac must contain exactly 32 bytes.")
    return value


def _row_lease(row: object) -> LeaseRecord:
    try:
        token = row["token_hmac"]  # type: ignore[index]
        record = LeaseRecord(
            item_id=str(row["item_id"]),  # type: ignore[index]
            holder_user_id=row["holder_user_id"],  # type: ignore[index,arg-type]
            editor_session_id=row["editor_session_id"],  # type: ignore[index,arg-type]
            token_hmac=None if token is None else bytes(token),
            hmac_key_id=row["hmac_key_id"],  # type: ignore[index,arg-type]
            fencing_generation=int(row["fencing_generation"]),  # type: ignore[index]
            base_item_row_version=(
                None
                if row["base_item_row_version"] is None  # type: ignore[index]
                else int(row["base_item_row_version"])  # type: ignore[index]
            ),
            base_revision_id=row["base_revision_id"],  # type: ignore[index,arg-type]
            acquired_at_ms=(
                None
                if row["acquired_at"] is None  # type: ignore[index]
                else int(row["acquired_at"])  # type: ignore[index]
            ),
            heartbeat_at_ms=(
                None
                if row["heartbeat_at"] is None  # type: ignore[index]
                else int(row["heartbeat_at"])  # type: ignore[index]
            ),
            expires_at_ms=(
                None
                if row["expires_at"] is None  # type: ignore[index]
                else int(row["expires_at"])  # type: ignore[index]
            ),
            absolute_expires_at_ms=(
                None
                if row["absolute_expires_at"] is None  # type: ignore[index]
                else int(row["absolute_expires_at"])  # type: ignore[index]
            ),
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LeaseIntegrityError("The canonical lease row could not be decoded.") from exc
    _validate_record(record)
    return record


def _validate_record(record: LeaseRecord) -> None:
    try:
        _uuid(record.item_id, field_name="item_id")
        _utc_ms(record.fencing_generation, field_name="fencing_generation")
    except ValueError as exc:
        raise LeaseIntegrityError("The canonical lease identity is invalid.") from exc
    values = (
        record.holder_user_id,
        record.editor_session_id,
        record.token_hmac,
        record.hmac_key_id,
        record.base_item_row_version,
        record.base_revision_id,
        record.acquired_at_ms,
        record.heartbeat_at_ms,
        record.expires_at_ms,
        record.absolute_expires_at_ms,
    )
    if not record.held:
        if any(value is not None for value in values):
            raise LeaseIntegrityError("A free canonical lease retains holder state.")
        return
    if any(value is None for value in values):
        raise LeaseIntegrityError("A held canonical lease is incomplete.")
    try:
        _user_id(record.holder_user_id, field_name="holder_user_id")
        _uuid(record.editor_session_id, field_name="editor_session_id")
        _digest(record.token_hmac)
        _required_text(record.hmac_key_id, field_name="hmac_key_id", maximum=80)
        _positive_int(record.base_item_row_version, field_name="base_item_row_version")
        _uuid(record.base_revision_id, field_name="base_revision_id")
        acquired = _utc_ms(record.acquired_at_ms, field_name="acquired_at_ms")
        heartbeat = _utc_ms(record.heartbeat_at_ms, field_name="heartbeat_at_ms")
        expires = _utc_ms(record.expires_at_ms, field_name="expires_at_ms")
        absolute = _utc_ms(
            record.absolute_expires_at_ms, field_name="absolute_expires_at_ms"
        )
    except ValueError as exc:
        raise LeaseIntegrityError("A held canonical lease has invalid fields.") from exc
    if not acquired <= heartbeat <= expires <= absolute:
        raise LeaseIntegrityError("The canonical lease time ordering is invalid.")


def _row_item(row: object) -> LeasableItemRecord:
    lease = _row_lease(row)
    try:
        item = LeasableItemRecord(
            item_id=lease.item_id,
            space_id=str(row["space_id"]),  # type: ignore[index]
            lifecycle_status=str(row["lifecycle_status"]),  # type: ignore[index]
            current_revision_id=str(row["current_revision_id"]),  # type: ignore[index]
            row_version=int(row["row_version"]),  # type: ignore[index]
            lease=lease,
        )
        _uuid(item.space_id, field_name="space_id")
        _uuid(item.current_revision_id, field_name="current_revision_id")
        _positive_int(item.row_version, field_name="row_version")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise LeaseIntegrityError("The leasable item row could not be decoded.") from exc
    return item


def _held_match(
    record: LeaseRecord,
    *,
    actor_user_id: str,
    editor_session_id: str,
    token_hmac: bytes,
    fencing_generation: int,
    now_ms: int,
) -> None:
    if (
        not record.held
        or record.expired(now_ms)
        or record.holder_user_id != actor_user_id
        or record.editor_session_id != editor_session_id
        or record.fencing_generation != fencing_generation
        or record.token_hmac is None
        or not compare_digest(record.token_hmac, token_hmac)
    ):
        raise LeaseLostRepositoryError(
            "The lease token, fence, session, holder, or expiry no longer matches."
        )


class LeaseRepository:
    """Connection-scoped compare-and-swap operations for ``item_lease``."""

    __slots__ = ()

    def get(self, connection: ManagedConnection, item_id: str) -> LeaseRecord | None:
        connection = _connection(connection)
        item_id = _uuid(item_id, field_name="item_id")
        row = connection.execute(
            f"SELECT {_LEASE_COLUMNS} FROM item_lease WHERE item_id=?", (item_id,)
        ).fetchone()
        return None if row is None else _row_lease(row)

    def require_item(
        self,
        connection: ManagedConnection,
        *,
        item_id: str,
        space_id: str,
    ) -> LeasableItemRecord:
        connection = _connection(connection)
        item_id = _uuid(item_id, field_name="item_id")
        space_id = _uuid(space_id, field_name="space_id")
        row = connection.execute(
            f"""SELECT i.id AS item_id,i.space_id,i.lifecycle_status,
                       i.current_revision_id,i.row_version,{','.join('l.' + c for c in _LEASE_COLUMNS.split(',') if c != 'item_id')}
                  FROM knowledge_item AS i
                  JOIN item_lease AS l ON l.item_id=i.id
                 WHERE i.id=? AND i.space_id=?""",
            (item_id, space_id),
        ).fetchone()
        if row is None:
            item = connection.execute(
                "SELECT space_id FROM knowledge_item WHERE id=?", (item_id,)
            ).fetchone()
            if item is not None and str(item[0]) == space_id:
                raise LeaseIntegrityError(
                    "The canonical item has no persistent fencing row."
                )
            raise LeaseItemNotFoundError("The requested item was not found in this space.")
        return _row_item(row)

    def acquire(
        self,
        connection: ManagedConnection,
        *,
        item_id: str,
        space_id: str,
        actor_user_id: str,
        editor_session_id: str,
        token_hmac: bytes,
        hmac_key_id: str,
        now_ms: int,
        stale_after_ms: int,
        absolute_after_ms: int,
    ) -> LeaseAcquireWriteResult:
        connection = _write_transaction(connection)
        actor_user_id = _user_id(actor_user_id, field_name="actor_user_id")
        editor_session_id = _uuid(
            editor_session_id, field_name="editor_session_id"
        )
        token_hmac = _digest(token_hmac)
        hmac_key_id = _required_text(
            hmac_key_id, field_name="hmac_key_id", maximum=80
        )
        now_ms = _utc_ms(now_ms, field_name="now_ms")
        stale_after_ms = _positive_int(stale_after_ms, field_name="stale_after_ms")
        absolute_after_ms = _positive_int(
            absolute_after_ms, field_name="absolute_after_ms"
        )
        if stale_after_ms > absolute_after_ms:
            raise ValueError("stale_after_ms cannot exceed absolute_after_ms.")
        if now_ms > _MAX_SQLITE_INTEGER - absolute_after_ms:
            raise LeaseIntegrityError("Lease expiry exceeds the SQLite integer range.")

        before = self.require_item(connection, item_id=item_id, space_id=space_id)
        if before.lifecycle_status != "active":
            raise LeaseStateConflictRepositoryError("Only an active item can be leased.")
        if before.lease.fencing_generation >= _MAX_SQLITE_INTEGER:
            raise LeaseIntegrityError("The lease fencing generation is exhausted.")
        if before.lease.held and not before.lease.expired(now_ms):
            raise LeaseHeldRepositoryError(
                "Another valid editor holds the item lease.", lease=before.lease
            )
        outcome: LeaseAcquireOutcome = (
            "reclaimed_expired" if before.lease.held else "acquired"
        )
        expires_at = now_ms + stale_after_ms
        absolute_expires_at = now_ms + absolute_after_ms
        cursor = connection.execute(
            """UPDATE item_lease
                  SET holder_user_id=?,editor_session_id=?,token_hmac=?,hmac_key_id=?,
                      fencing_generation=fencing_generation+1,
                      base_item_row_version=?,base_revision_id=?,acquired_at=?,
                      heartbeat_at=?,expires_at=?,absolute_expires_at=?
                WHERE item_id=? AND fencing_generation=?
                  AND (holder_user_id IS NULL OR expires_at<=? OR absolute_expires_at<=?)""",
            (
                actor_user_id,
                editor_session_id,
                token_hmac,
                hmac_key_id,
                before.row_version,
                before.current_revision_id,
                now_ms,
                now_ms,
                expires_at,
                absolute_expires_at,
                before.item_id,
                before.lease.fencing_generation,
                now_ms,
                now_ms,
            ),
        )
        if cursor.rowcount != 1:
            current = self.require_item(
                connection, item_id=before.item_id, space_id=before.space_id
            )
            if current.lease.held and not current.lease.expired(now_ms):
                raise LeaseHeldRepositoryError(
                    "Another valid editor acquired the item lease.",
                    lease=current.lease,
                )
            raise LeaseStateConflictRepositoryError(
                "The lease changed before acquisition could commit."
            )
        current = self.require_item(
            connection, item_id=before.item_id, space_id=before.space_id
        )
        if (
            current.lease.fencing_generation
            != before.lease.fencing_generation + 1
            or current.lease.holder_user_id != actor_user_id
            or current.lease.editor_session_id != editor_session_id
            or current.lease.token_hmac is None
            or not compare_digest(current.lease.token_hmac, token_hmac)
        ):
            raise LeaseIntegrityError("The acquired lease postcondition failed.")
        return LeaseAcquireWriteResult(item=current, outcome=outcome)

    def heartbeat(
        self,
        connection: ManagedConnection,
        *,
        item_id: str,
        space_id: str,
        actor_user_id: str,
        editor_session_id: str,
        token_hmac: bytes,
        fencing_generation: int,
        now_ms: int,
        stale_after_ms: int,
    ) -> LeasableItemRecord:
        connection = _write_transaction(connection)
        actor_user_id = _user_id(actor_user_id, field_name="actor_user_id")
        editor_session_id = _uuid(
            editor_session_id, field_name="editor_session_id"
        )
        token_hmac = _digest(token_hmac)
        fencing_generation = _positive_int(
            fencing_generation, field_name="fencing_generation"
        )
        now_ms = _utc_ms(now_ms, field_name="now_ms")
        stale_after_ms = _positive_int(stale_after_ms, field_name="stale_after_ms")
        item = self.require_item(connection, item_id=item_id, space_id=space_id)
        if item.lifecycle_status != "active":
            raise LeaseLostRepositoryError("The leased item is no longer active.")
        _held_match(
            item.lease,
            actor_user_id=actor_user_id,
            editor_session_id=editor_session_id,
            token_hmac=token_hmac,
            fencing_generation=fencing_generation,
            now_ms=now_ms,
        )
        assert item.lease.expires_at_ms is not None
        assert item.lease.absolute_expires_at_ms is not None
        if now_ms > _MAX_SQLITE_INTEGER - stale_after_ms:
            raise LeaseIntegrityError("Lease heartbeat exceeds the integer range.")
        expires_at = min(
            item.lease.absolute_expires_at_ms,
            max(item.lease.expires_at_ms, now_ms + stale_after_ms),
        )
        if expires_at <= now_ms:
            raise LeaseLostRepositoryError("The absolute lease lifetime expired.")
        cursor = connection.execute(
            """UPDATE item_lease SET heartbeat_at=?,expires_at=?
                WHERE item_id=? AND holder_user_id=? AND editor_session_id=?
                  AND token_hmac=? AND fencing_generation=?
                  AND expires_at>? AND absolute_expires_at>?""",
            (
                now_ms,
                expires_at,
                item.item_id,
                actor_user_id,
                editor_session_id,
                token_hmac,
                fencing_generation,
                now_ms,
                now_ms,
            ),
        )
        if cursor.rowcount != 1:
            raise LeaseLostRepositoryError(
                "The lease changed before heartbeat could commit."
            )
        current = self.require_item(
            connection, item_id=item.item_id, space_id=item.space_id
        )
        _held_match(
            current.lease,
            actor_user_id=actor_user_id,
            editor_session_id=editor_session_id,
            token_hmac=token_hmac,
            fencing_generation=fencing_generation,
            now_ms=now_ms,
        )
        return current

    def release(
        self,
        connection: ManagedConnection,
        *,
        item_id: str,
        space_id: str,
        actor_user_id: str,
        editor_session_id: str,
        token_hmac: bytes,
        fencing_generation: int,
        now_ms: int,
    ) -> LeaseReleaseWriteResult:
        connection = _write_transaction(connection)
        actor_user_id = _user_id(actor_user_id, field_name="actor_user_id")
        editor_session_id = _uuid(
            editor_session_id, field_name="editor_session_id"
        )
        token_hmac = _digest(token_hmac)
        fencing_generation = _positive_int(
            fencing_generation, field_name="fencing_generation"
        )
        now_ms = _utc_ms(now_ms, field_name="now_ms")
        item = self.require_item(connection, item_id=item_id, space_id=space_id)
        _held_match(
            item.lease,
            actor_user_id=actor_user_id,
            editor_session_id=editor_session_id,
            token_hmac=token_hmac,
            fencing_generation=fencing_generation,
            now_ms=now_ms,
        )
        cursor = connection.execute(
            """UPDATE item_lease SET
                      holder_user_id=NULL,editor_session_id=NULL,token_hmac=NULL,
                      hmac_key_id=NULL,base_item_row_version=NULL,
                      base_revision_id=NULL,acquired_at=NULL,heartbeat_at=NULL,
                      expires_at=NULL,absolute_expires_at=NULL
                WHERE item_id=? AND holder_user_id=? AND editor_session_id=?
                  AND token_hmac=? AND fencing_generation=?
                  AND expires_at>? AND absolute_expires_at>?""",
            (
                item.item_id,
                actor_user_id,
                editor_session_id,
                token_hmac,
                fencing_generation,
                now_ms,
                now_ms,
            ),
        )
        if cursor.rowcount != 1:
            raise LeaseLostRepositoryError(
                "The lease changed before release could commit."
            )
        current = self.require_item(
            connection, item_id=item.item_id, space_id=item.space_id
        )
        if current.lease.held or current.lease.fencing_generation != fencing_generation:
            raise LeaseIntegrityError("The owner-release postcondition failed.")
        return LeaseReleaseWriteResult(item=current, released=item.lease)

    def force_release(
        self,
        connection: ManagedConnection,
        *,
        item_id: str,
        space_id: str,
    ) -> LeaseReleaseWriteResult:
        connection = _write_transaction(connection)
        item = self.require_item(connection, item_id=item_id, space_id=space_id)
        if not item.lease.held:
            raise LeaseStateConflictRepositoryError("The item has no held lease.")
        if item.lease.fencing_generation >= _MAX_SQLITE_INTEGER:
            raise LeaseIntegrityError("The lease fencing generation is exhausted.")
        cursor = connection.execute(
            """UPDATE item_lease SET
                      holder_user_id=NULL,editor_session_id=NULL,token_hmac=NULL,
                      hmac_key_id=NULL,fencing_generation=fencing_generation+1,
                      base_item_row_version=NULL,base_revision_id=NULL,
                      acquired_at=NULL,heartbeat_at=NULL,expires_at=NULL,
                      absolute_expires_at=NULL
                WHERE item_id=? AND fencing_generation=? AND holder_user_id IS NOT NULL""",
            (item.item_id, item.lease.fencing_generation),
        )
        if cursor.rowcount != 1:
            raise LeaseStateConflictRepositoryError(
                "The lease changed before force release could commit."
            )
        current = self.require_item(
            connection, item_id=item.item_id, space_id=item.space_id
        )
        if (
            current.lease.held
            or current.lease.fencing_generation
            != item.lease.fencing_generation + 1
        ):
            raise LeaseIntegrityError("The force-release postcondition failed.")
        return LeaseReleaseWriteResult(item=current, released=item.lease)

    def clear_for_lifecycle(
        self,
        connection: ManagedConnection,
        *,
        item_id: str,
        space_id: str,
        actor_user_id: str,
        now_ms: int,
    ) -> LeaseReleaseWriteResult | None:
        """Clear only an actor-owned or expired lease for item deactivation.

        A live lease held by another user is an authorization-visible lock
        conflict, never an implicit force release.  An actor-owned lease and an
        expired lease are revoked by advancing the persistent fencing
        generation so that every previously issued token becomes stale before
        the item can become inactive.
        """

        connection = _write_transaction(connection)
        actor_user_id = _user_id(actor_user_id, field_name="actor_user_id")
        now_ms = _utc_ms(now_ms, field_name="now_ms")
        item = self.require_item(connection, item_id=item_id, space_id=space_id)
        if not item.lease.held:
            return None
        if (
            not item.lease.expired(now_ms)
            and item.lease.holder_user_id != actor_user_id
        ):
            raise LeaseHeldRepositoryError(
                "Another valid editor holds the item lease.", lease=item.lease
            )
        return self.force_release(
            connection,
            item_id=item.item_id,
            space_id=item.space_id,
        )

    def validate_holder(
        self,
        connection: ManagedConnection,
        *,
        item_id: str,
        space_id: str,
        actor_user_id: str,
        editor_session_id: str,
        token_hmac: bytes,
        fencing_generation: int,
        now_ms: int,
        expected_item_row_version: int | None = None,
        expected_revision_id: str | None = None,
    ) -> LeaseValidationProof:
        connection = _connection(connection)
        actor_user_id = _user_id(actor_user_id, field_name="actor_user_id")
        editor_session_id = _uuid(
            editor_session_id, field_name="editor_session_id"
        )
        token_hmac = _digest(token_hmac)
        fencing_generation = _positive_int(
            fencing_generation, field_name="fencing_generation"
        )
        now_ms = _utc_ms(now_ms, field_name="now_ms")
        if expected_item_row_version is not None:
            expected_item_row_version = _positive_int(
                expected_item_row_version, field_name="expected_item_row_version"
            )
        if expected_revision_id is not None:
            expected_revision_id = _uuid(
                expected_revision_id, field_name="expected_revision_id"
            )
        item = self.require_item(connection, item_id=item_id, space_id=space_id)
        if item.lifecycle_status != "active":
            raise LeaseLostRepositoryError("The leased item is no longer active.")
        _held_match(
            item.lease,
            actor_user_id=actor_user_id,
            editor_session_id=editor_session_id,
            token_hmac=token_hmac,
            fencing_generation=fencing_generation,
            now_ms=now_ms,
        )
        assert item.lease.base_item_row_version is not None
        assert item.lease.base_revision_id is not None
        assert item.lease.expires_at_ms is not None
        assert item.lease.absolute_expires_at_ms is not None
        if (
            item.row_version != item.lease.base_item_row_version
            or item.current_revision_id != item.lease.base_revision_id
            or (
                expected_item_row_version is not None
                and item.row_version != expected_item_row_version
            )
            or (
                expected_revision_id is not None
                and item.current_revision_id != expected_revision_id
            )
        ):
            raise LeaseRevisionConflictRepositoryError(
                "The item changed after the lease was acquired.",
                current_revision_id=item.current_revision_id,
                current_row_version=item.row_version,
            )
        return LeaseValidationProof(
            item_id=item.item_id,
            space_id=item.space_id,
            holder_user_id=actor_user_id,
            editor_session_id=editor_session_id,
            fencing_generation=fencing_generation,
            base_item_row_version=item.lease.base_item_row_version,
            base_revision_id=item.lease.base_revision_id,
            current_item_row_version=item.row_version,
            current_revision_id=item.current_revision_id,
            expires_at_ms=item.lease.expires_at_ms,
            absolute_expires_at_ms=item.lease.absolute_expires_at_ms,
        )


__all__ = [
    "LeaseAcquireOutcome",
    "LeaseAcquireWriteResult",
    "LeaseHeldRepositoryError",
    "LeaseIntegrityError",
    "LeaseItemNotFoundError",
    "LeaseLostRepositoryError",
    "LeaseRecord",
    "LeaseReleaseWriteResult",
    "LeaseRepository",
    "LeaseRepositoryError",
    "LeaseRevisionConflictRepositoryError",
    "LeaseStateConflictRepositoryError",
    "LeaseValidationProof",
    "LeasableItemRecord",
]
