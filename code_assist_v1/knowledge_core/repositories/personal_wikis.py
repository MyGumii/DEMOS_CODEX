"""Connection-scoped persistence primitives for v8 personal-Wiki profiles.

This module owns no authorization, transaction, route, audit, or outbox
workflow.  It validates the frozen storage DTO and supplies the bounded SQL
and compare-and-swap primitives that the lifecycle service composes inside one
managed transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
import unicodedata
from typing import Literal

from ..db import ManagedConnection
from ..errors import KnowledgeCoreDatabaseError, KnowledgeCoreTransactionError


PersonalWikiLifecycleStatus = Literal["active", "archived"]

DEFAULT_PERSONAL_WIKI_DISPLAY_NAME = "내 지식"
PERSONAL_WIKI_DISPLAY_NAME_MAX_LENGTH = 120
PERSONAL_WIKI_NAME_KEY_MAX_LENGTH = 160
PERSONAL_WIKI_ARCHIVE_REASON_MAX_LENGTH = 1000
PERSONAL_WIKI_MAX_PAGE_SIZE = 100

_REJECTED_UNICODE_CATEGORIES = frozenset({"Cc", "Cf", "Cn", "Co", "Cs"})


class PersonalWikiRepositoryError(KnowledgeCoreDatabaseError):
    code = "PERSONAL_WIKI_REPOSITORY_ERROR"


class PersonalWikiNotFoundError(PersonalWikiRepositoryError):
    code = "PERSONAL_WIKI_NOT_FOUND"


class PersonalWikiNameConflictError(PersonalWikiRepositoryError):
    code = "WIKI_NAME_CONFLICT"


class PersonalWikiDefaultConflictError(PersonalWikiRepositoryError):
    code = "WIKI_DEFAULT_REQUIRED"


class PersonalWikiStateError(PersonalWikiRepositoryError):
    code = "PERSONAL_WIKI_STATE_INVALID"


class PersonalWikiVersionConflictError(PersonalWikiRepositoryError):
    code = "PERSONAL_WIKI_VERSION_CONFLICT"


@dataclass(frozen=True, slots=True)
class PersonalWikiProfileRecord:
    space_id: str
    owner_user_id: str
    display_name: str
    name_key: str
    lifecycle_status: PersonalWikiLifecycleStatus
    lifecycle_generation: int
    is_default: bool
    archived_at_ms: int | None
    archived_by_user_id: str | None
    archive_reason: str | None
    created_at_ms: int
    updated_at_ms: int
    row_version: int


@dataclass(frozen=True, slots=True)
class PersonalWikiProfilePage:
    items: tuple[PersonalWikiProfileRecord, ...]
    has_more: bool


def normalize_personal_wiki_name(display_name: object) -> tuple[str, str]:
    """Return the preserved display spelling and deterministic NFKC+casefold key."""

    if not isinstance(display_name, str):
        raise ValueError("display_name must be text.")
    if display_name != display_name.strip() or not display_name:
        raise ValueError("display_name must be nonblank trimmed text.")
    if len(display_name) > PERSONAL_WIKI_DISPLAY_NAME_MAX_LENGTH:
        raise ValueError("display_name exceeds the canonical length bound.")
    if any(
        unicodedata.category(character) in _REJECTED_UNICODE_CATEGORIES
        for character in display_name
    ):
        raise ValueError("display_name contains a forbidden Unicode character.")

    name_key = unicodedata.normalize("NFKC", display_name).casefold()
    if (
        not name_key
        or name_key != name_key.strip()
        or len(name_key) > PERSONAL_WIKI_NAME_KEY_MAX_LENGTH
        or any(
            unicodedata.category(character) in _REJECTED_UNICODE_CATEGORIES
            for character in name_key
        )
    ):
        raise ValueError("display_name cannot produce a canonical name_key.")
    return display_name, name_key


def normalize_personal_wiki_query(query: object) -> str | None:
    """Normalize an optional bounded profile-name prefix without rendering it."""

    if query is None:
        return None
    if not isinstance(query, str) or query != query.strip() or not query:
        raise ValueError("query must be nonblank trimmed text or null.")
    if len(query) > PERSONAL_WIKI_DISPLAY_NAME_MAX_LENGTH or any(
        unicodedata.category(character) in _REJECTED_UNICODE_CATEGORIES
        for character in query
    ):
        raise ValueError("query contains an invalid character or exceeds its bound.")
    value = unicodedata.normalize("NFKC", query).casefold()
    if not value or len(value) > PERSONAL_WIKI_NAME_KEY_MAX_LENGTH:
        raise ValueError("query cannot produce a bounded canonical key.")
    return value


def _require_connection(connection: ManagedConnection) -> ManagedConnection:
    if not isinstance(connection, ManagedConnection):
        raise TypeError("PersonalWikiProfileRepository requires a ManagedConnection.")
    return connection


def _require_write_transaction(connection: ManagedConnection) -> ManagedConnection:
    connection = _require_connection(connection)
    if not connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Personal-Wiki profile writes require an existing managed transaction."
        )
    return connection


def _user_id(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 64
        or value != value.strip().lower()
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in value)
    ):
        raise ValueError(f"{field} is not a canonical user ID.")
    return value


def _space_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("space_id must be a canonical lowercase UUID.")
    parts = value.split("-")
    if (
        len(parts) != 5
        or tuple(len(part) for part in parts) != (8, 4, 4, 4, 12)
        or value != value.lower()
        or any(character not in "0123456789abcdef-" for character in value)
    ):
        raise ValueError("space_id must be a canonical lowercase UUID.")
    return value


def _non_negative_ms(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative UTC millisecond integer.")
    return value


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _row_value(row: object, key: str) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, TypeError, IndexError) as exc:
        raise PersonalWikiRepositoryError(
            "Canonical personal-Wiki profile row mapping failed."
        ) from exc


def _row_to_profile(row: object) -> PersonalWikiProfileRecord:
    try:
        space_id = _space_id(_row_value(row, "space_id"))
        owner_user_id = _user_id(
            _row_value(row, "owner_user_id"), field="owner_user_id"
        )
        display_name, expected_name_key = normalize_personal_wiki_name(
            _row_value(row, "display_name")
        )
        name_key = _row_value(row, "name_key")
        if not isinstance(name_key, str) or name_key != expected_name_key:
            raise ValueError("name_key does not match display_name")
        lifecycle_status = _row_value(row, "lifecycle_status")
        if lifecycle_status not in ("active", "archived"):
            raise ValueError("lifecycle_status is invalid")
        lifecycle_generation = _positive_int(
            _row_value(row, "lifecycle_generation"),
            field="lifecycle_generation",
        )
        stored_default = _row_value(row, "is_default")
        if type(stored_default) is not int or stored_default not in (0, 1):
            raise ValueError("is_default is not a canonical Boolean")

        archived_at_value = _row_value(row, "archived_at")
        archived_at_ms = (
            None
            if archived_at_value is None
            else _non_negative_ms(archived_at_value, field="archived_at")
        )
        archived_by_value = _row_value(row, "archived_by_user_id")
        archived_by_user_id = (
            None
            if archived_by_value is None
            else _user_id(archived_by_value, field="archived_by_user_id")
        )
        archive_reason = _row_value(row, "archive_reason")
        if archive_reason is not None and (
            not isinstance(archive_reason, str)
            or archive_reason != archive_reason.strip()
            or not archive_reason
            or len(archive_reason) > PERSONAL_WIKI_ARCHIVE_REASON_MAX_LENGTH
        ):
            raise ValueError("archive_reason is invalid")

        if lifecycle_status == "active":
            if any(value is not None for value in (archived_at_ms, archived_by_user_id, archive_reason)):
                raise ValueError("active profile carries archive metadata")
        elif bool(stored_default) or archived_at_ms is None or archived_by_user_id != owner_user_id:
            raise ValueError("archived profile tuple is invalid")

        created_at_ms = _non_negative_ms(_row_value(row, "created_at"), field="created_at")
        updated_at_ms = _non_negative_ms(_row_value(row, "updated_at"), field="updated_at")
        if updated_at_ms < created_at_ms:
            raise ValueError("updated_at precedes created_at")
        row_version = _positive_int(_row_value(row, "row_version"), field="row_version")
        return PersonalWikiProfileRecord(
            space_id=space_id,
            owner_user_id=owner_user_id,
            display_name=display_name,
            name_key=name_key,
            lifecycle_status=lifecycle_status,  # type: ignore[arg-type]
            lifecycle_generation=lifecycle_generation,
            is_default=bool(stored_default),
            archived_at_ms=archived_at_ms,
            archived_by_user_id=archived_by_user_id,
            archive_reason=archive_reason,
            created_at_ms=created_at_ms,
            updated_at_ms=updated_at_ms,
            row_version=row_version,
        )
    except (TypeError, ValueError) as exc:
        raise PersonalWikiRepositoryError(
            "Canonical personal-Wiki profile row mapping failed."
        ) from exc


_PROFILE_COLUMNS = """
space_id,owner_user_id,display_name,name_key,lifecycle_status,
lifecycle_generation,is_default,archived_at,archived_by_user_id,archive_reason,
created_at,updated_at,row_version
""".strip()


class PersonalWikiProfileRepository:
    """Strict v8 profile reads plus transaction-scoped lifecycle CAS primitives."""

    __slots__ = ()

    def get_by_space_id(
        self, connection: ManagedConnection, space_id: str
    ) -> PersonalWikiProfileRecord | None:
        connection = _require_connection(connection)
        space_id = _space_id(space_id)
        row = connection.execute(
            f"SELECT {_PROFILE_COLUMNS} FROM personal_wiki_profile WHERE space_id=?",
            (space_id,),
        ).fetchone()
        return None if row is None else _row_to_profile(row)

    def get_default(
        self, connection: ManagedConnection, owner_user_id: str
    ) -> PersonalWikiProfileRecord | None:
        connection = _require_connection(connection)
        owner_user_id = _user_id(owner_user_id, field="owner_user_id")
        rows = connection.execute(
            f"""SELECT {_PROFILE_COLUMNS} FROM personal_wiki_profile
                WHERE owner_user_id=? AND lifecycle_status='active'
                  AND is_default=1
                ORDER BY space_id LIMIT 2""",
            (owner_user_id,),
        ).fetchall()
        if len(rows) > 1:
            raise PersonalWikiStateError(
                "The owner has more than one active default personal Wiki."
            )
        return None if not rows else _row_to_profile(rows[0])

    def list_by_owner(
        self,
        connection: ManagedConnection,
        *,
        owner_user_id: str,
        lifecycle_status: PersonalWikiLifecycleStatus = "active",
        after_name_key: str | None = None,
        after_space_id: str | None = None,
        limit: int = 50,
    ) -> tuple[PersonalWikiProfileRecord, ...]:
        connection = _require_connection(connection)
        owner_user_id = _user_id(owner_user_id, field="owner_user_id")
        if lifecycle_status not in ("active", "archived"):
            raise ValueError("lifecycle_status must be active or archived.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= PERSONAL_WIKI_MAX_PAGE_SIZE:
            raise ValueError("limit must be between 1 and the bounded page maximum.")
        if (after_name_key is None) != (after_space_id is None):
            raise ValueError("Both cursor tuple members must be supplied together.")
        parameters: list[object] = [owner_user_id, lifecycle_status]
        cursor_clause = ""
        if after_name_key is not None and after_space_id is not None:
            if (
                not isinstance(after_name_key, str)
                or not after_name_key
                or after_name_key != after_name_key.strip()
                or len(after_name_key) > PERSONAL_WIKI_NAME_KEY_MAX_LENGTH
            ):
                raise ValueError("after_name_key is invalid.")
            cursor_clause = " AND (name_key,space_id)>(?,?)"
            parameters.extend((after_name_key, _space_id(after_space_id)))
        parameters.append(limit)
        rows = connection.execute(
            f"""SELECT {_PROFILE_COLUMNS} FROM personal_wiki_profile
                WHERE owner_user_id=? AND lifecycle_status=?{cursor_clause}
                ORDER BY name_key,space_id LIMIT ?""",
            tuple(parameters),
        ).fetchall()
        return tuple(_row_to_profile(row) for row in rows)

    def list_page_by_owner(
        self,
        connection: ManagedConnection,
        *,
        owner_user_id: str,
        lifecycle_status: PersonalWikiLifecycleStatus,
        query: str | None = None,
        after_name_key: str | None = None,
        after_space_id: str | None = None,
        before_archived_at_ms: int | None = None,
        limit: int = 50,
    ) -> PersonalWikiProfilePage:
        """Return at most ``limit`` rows and one bounded look-ahead bit.

        Active profiles use the stable ``(name_key, space_id)`` order. Archived
        management uses ``(archived_at DESC, space_id ASC)`` so it follows the
        dedicated v8 cursor index. The service, not the repository, owns opaque
        signed cursor encoding.
        """

        connection = _require_connection(connection)
        owner_user_id = _user_id(owner_user_id, field="owner_user_id")
        if lifecycle_status not in ("active", "archived"):
            raise ValueError("lifecycle_status must be active or archived.")
        query_key = normalize_personal_wiki_query(query)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= PERSONAL_WIKI_MAX_PAGE_SIZE
        ):
            raise ValueError("limit must be between 1 and the bounded page maximum.")

        clauses = ["owner_user_id=?", "lifecycle_status=?"]
        parameters: list[object] = [owner_user_id, lifecycle_status]
        if query_key is not None:
            # Prefix semantics keep the query owner/status/name-indexable and
            # avoid an unbounded contains scan across an unlimited collection.
            clauses.append("name_key>=? AND name_key<?")
            parameters.extend((query_key, query_key + "\U0010ffff"))

        if lifecycle_status == "active":
            if before_archived_at_ms is not None:
                raise ValueError("An active cursor cannot carry archived time.")
            if (after_name_key is None) != (after_space_id is None):
                raise ValueError("Both active cursor tuple members are required.")
            if after_name_key is not None and after_space_id is not None:
                if (
                    not isinstance(after_name_key, str)
                    or not after_name_key
                    or after_name_key != after_name_key.strip()
                    or len(after_name_key) > PERSONAL_WIKI_NAME_KEY_MAX_LENGTH
                ):
                    raise ValueError("after_name_key is invalid.")
                clauses.append("(name_key,space_id)>(?,?)")
                parameters.extend((after_name_key, _space_id(after_space_id)))
            order = "name_key ASC,space_id ASC"
        else:
            if after_name_key is not None:
                raise ValueError("An archived cursor cannot carry an active name key.")
            if (before_archived_at_ms is None) != (after_space_id is None):
                raise ValueError("Both archived cursor tuple members are required.")
            if before_archived_at_ms is not None and after_space_id is not None:
                archived_at = _non_negative_ms(
                    before_archived_at_ms, field="before_archived_at_ms"
                )
                clauses.append("(archived_at<? OR (archived_at=? AND space_id>?))")
                parameters.extend((archived_at, archived_at, _space_id(after_space_id)))
            order = "archived_at DESC,space_id ASC"

        parameters.append(limit + 1)
        rows = connection.execute(
            f"""SELECT {_PROFILE_COLUMNS} FROM personal_wiki_profile
                WHERE {' AND '.join(clauses)}
                ORDER BY {order} LIMIT ?""",
            tuple(parameters),
        ).fetchall()
        records = tuple(_row_to_profile(row) for row in rows)
        return PersonalWikiProfilePage(items=records[:limit], has_more=len(records) > limit)

    def require_owned(
        self,
        connection: ManagedConnection,
        *,
        owner_user_id: str,
        space_id: str,
    ) -> PersonalWikiProfileRecord:
        connection = _require_connection(connection)
        owner_user_id = _user_id(owner_user_id, field="owner_user_id")
        space_id = _space_id(space_id)
        row = connection.execute(
            f"""SELECT {_PROFILE_COLUMNS} FROM personal_wiki_profile
                WHERE owner_user_id=? AND space_id=?""",
            (owner_user_id, space_id),
        ).fetchone()
        if row is None:
            raise PersonalWikiNotFoundError("The personal Wiki is not available to this owner.")
        return _row_to_profile(row)

    def require_exact_default(
        self,
        connection: ManagedConnection,
        *,
        owner_user_id: str,
        expected_row_version: int | None = None,
    ) -> PersonalWikiProfileRecord:
        owner_user_id = _user_id(owner_user_id, field="owner_user_id")
        current = self.get_default(connection, owner_user_id)
        if current is None:
            raise PersonalWikiDefaultConflictError(
                "The owner must have exactly one active default Wiki."
            )
        if expected_row_version is not None:
            expected = _positive_int(expected_row_version, field="expected_row_version")
            if current.row_version != expected:
                raise PersonalWikiVersionConflictError(
                    "The default Wiki row version changed before the mutation."
                )
        return current

    def _require_exact_default_postcondition(
        self, connection: ManagedConnection, owner_user_id: str
    ) -> PersonalWikiProfileRecord:
        rows = connection.execute(
            f"""SELECT {_PROFILE_COLUMNS} FROM personal_wiki_profile
                WHERE owner_user_id=? AND lifecycle_status='active' AND is_default=1
                ORDER BY space_id LIMIT 2""",
            (owner_user_id,),
        ).fetchall()
        if len(rows) != 1:
            raise PersonalWikiDefaultConflictError(
                "A personal-Wiki mutation must preserve exactly one active default."
            )
        return _row_to_profile(rows[0])

    def insert_new_wiki(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        owner_user_id: str,
        display_name: str,
        expected_default_row_version: int,
        created_at_ms: int,
        write_enabled: bool = False,
    ) -> PersonalWikiProfileRecord:
        """Insert one additional stable personal space and non-default profile."""

        connection = _require_write_transaction(connection)
        space_id = _space_id(space_id)
        owner_user_id = _user_id(owner_user_id, field="owner_user_id")
        created_at_ms = _non_negative_ms(created_at_ms, field="created_at_ms")
        if not isinstance(write_enabled, bool):
            raise ValueError("write_enabled must be a Boolean.")
        self.require_exact_default(
            connection,
            owner_user_id=owner_user_id,
            expected_row_version=expected_default_row_version,
        )
        existing = self.get_by_space_id(connection, space_id)
        if existing is not None:
            raise PersonalWikiStateError(
                "The new personal-Wiki identity is already in use."
            )
        try:
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'personal',?,NULL,'active',?,0,?,?,1)""",
                (
                    space_id,
                    owner_user_id,
                    1 if write_enabled else 0,
                    created_at_ms,
                    created_at_ms,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise PersonalWikiStateError(
                "The new personal-Wiki space violates the canonical identity contract."
            ) from exc
        profile = self.insert_initial_profile(
            connection,
            space_id=space_id,
            owner_user_id=owner_user_id,
            display_name=display_name,
            is_default=False,
            created_at_ms=created_at_ms,
        )
        self._require_exact_default_postcondition(connection, owner_user_id)
        return profile

    def rename(
        self,
        connection: ManagedConnection,
        *,
        owner_user_id: str,
        space_id: str,
        display_name: str,
        expected_row_version: int,
        updated_at_ms: int,
    ) -> PersonalWikiProfileRecord:
        connection = _require_write_transaction(connection)
        current = self.require_owned(
            connection, owner_user_id=owner_user_id, space_id=space_id
        )
        expected = _positive_int(expected_row_version, field="expected_row_version")
        if current.row_version != expected:
            raise PersonalWikiVersionConflictError(
                "The personal-Wiki row version changed before rename."
            )
        display_name, name_key = normalize_personal_wiki_name(display_name)
        updated_at_ms = _non_negative_ms(updated_at_ms, field="updated_at_ms")
        if current.display_name == display_name and current.name_key == name_key:
            return current
        try:
            cursor = connection.execute(
                """UPDATE personal_wiki_profile
                   SET display_name=?,name_key=?,updated_at=?,row_version=row_version+1
                   WHERE space_id=? AND owner_user_id=? AND row_version=?""",
                (
                    display_name,
                    name_key,
                    updated_at_ms,
                    current.space_id,
                    current.owner_user_id,
                    expected,
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "owner_user_id, personal_wiki_profile.name_key" in str(exc):
                raise PersonalWikiNameConflictError(
                    "The owner already has a Wiki with this normalized name."
                ) from exc
            raise PersonalWikiStateError(
                "The personal-Wiki rename violates the storage contract."
            ) from exc
        if cursor.rowcount != 1:
            raise PersonalWikiVersionConflictError(
                "The personal-Wiki row version changed before rename."
            )
        return self.require_owned(
            connection, owner_user_id=current.owner_user_id, space_id=current.space_id
        )

    def set_default(
        self,
        connection: ManagedConnection,
        *,
        owner_user_id: str,
        space_id: str,
        expected_row_version: int,
        updated_at_ms: int,
    ) -> PersonalWikiProfileRecord:
        connection = _require_write_transaction(connection)
        current = self.require_owned(
            connection, owner_user_id=owner_user_id, space_id=space_id
        )
        expected = _positive_int(expected_row_version, field="expected_row_version")
        if current.row_version != expected:
            raise PersonalWikiVersionConflictError(
                "The personal-Wiki row version changed before default selection."
            )
        if current.lifecycle_status != "active":
            raise PersonalWikiStateError("An archived Wiki cannot become the default.")
        updated_at_ms = _non_negative_ms(updated_at_ms, field="updated_at_ms")
        if current.is_default:
            return current
        previous = self.require_exact_default(
            connection, owner_user_id=current.owner_user_id
        )
        cleared = connection.execute(
            """UPDATE personal_wiki_profile
               SET is_default=0,updated_at=?,row_version=row_version+1
               WHERE space_id=? AND owner_user_id=? AND is_default=1
                 AND lifecycle_status='active' AND row_version=?""",
            (
                updated_at_ms,
                previous.space_id,
                current.owner_user_id,
                previous.row_version,
            ),
        )
        if cleared.rowcount != 1:
            raise PersonalWikiVersionConflictError(
                "The current default changed before replacement."
            )
        selected = connection.execute(
            """UPDATE personal_wiki_profile
               SET is_default=1,updated_at=?,row_version=row_version+1
               WHERE space_id=? AND owner_user_id=? AND lifecycle_status='active'
                 AND is_default=0 AND row_version=?""",
            (
                updated_at_ms,
                current.space_id,
                current.owner_user_id,
                expected,
            ),
        )
        if selected.rowcount != 1:
            raise PersonalWikiVersionConflictError(
                "The selected Wiki changed before it became default."
            )
        default = self._require_exact_default_postcondition(
            connection, current.owner_user_id
        )
        if default.space_id != current.space_id:
            raise PersonalWikiDefaultConflictError(
                "The selected Wiki did not become the exact active default."
            )
        return default

    def archive(
        self,
        connection: ManagedConnection,
        *,
        owner_user_id: str,
        space_id: str,
        expected_row_version: int,
        archived_at_ms: int,
        reason: str | None,
    ) -> PersonalWikiProfileRecord:
        connection = _require_write_transaction(connection)
        current = self.require_owned(
            connection, owner_user_id=owner_user_id, space_id=space_id
        )
        expected = _positive_int(expected_row_version, field="expected_row_version")
        if current.row_version != expected:
            raise PersonalWikiVersionConflictError(
                "The personal-Wiki row version changed before archive."
            )
        if current.lifecycle_status != "active":
            raise PersonalWikiStateError("Only an active Wiki can be archived.")
        if current.is_default:
            raise PersonalWikiDefaultConflictError(
                "The current default Wiki cannot be archived."
            )
        active_count = connection.execute(
            """SELECT count(*) FROM personal_wiki_profile
               WHERE owner_user_id=? AND lifecycle_status='active'""",
            (current.owner_user_id,),
        ).fetchone()[0]
        if type(active_count) is not int or active_count <= 1:
            raise PersonalWikiDefaultConflictError(
                "The last active Wiki cannot be archived."
            )
        archived_at_ms = _non_negative_ms(archived_at_ms, field="archived_at_ms")
        if reason is not None and (
            not isinstance(reason, str)
            or reason != reason.strip()
            or not reason
            or len(reason) > PERSONAL_WIKI_ARCHIVE_REASON_MAX_LENGTH
        ):
            raise ValueError("reason must be null or bounded nonblank trimmed text.")
        cursor = connection.execute(
            """UPDATE personal_wiki_profile
               SET lifecycle_status='archived',lifecycle_generation=lifecycle_generation+1,
                   is_default=0,archived_at=?,archived_by_user_id=?,archive_reason=?,
                   updated_at=?,row_version=row_version+1
               WHERE space_id=? AND owner_user_id=? AND lifecycle_status='active'
                 AND is_default=0 AND row_version=?""",
            (
                archived_at_ms,
                current.owner_user_id,
                reason,
                archived_at_ms,
                current.space_id,
                current.owner_user_id,
                expected,
            ),
        )
        if cursor.rowcount != 1:
            raise PersonalWikiVersionConflictError(
                "The personal-Wiki state changed before archive."
            )
        self._require_exact_default_postcondition(connection, current.owner_user_id)
        return self.require_owned(
            connection, owner_user_id=current.owner_user_id, space_id=current.space_id
        )

    def restore(
        self,
        connection: ManagedConnection,
        *,
        owner_user_id: str,
        space_id: str,
        expected_row_version: int,
        restored_at_ms: int,
        make_default: bool,
    ) -> PersonalWikiProfileRecord:
        connection = _require_write_transaction(connection)
        current = self.require_owned(
            connection, owner_user_id=owner_user_id, space_id=space_id
        )
        expected = _positive_int(expected_row_version, field="expected_row_version")
        if current.row_version != expected:
            raise PersonalWikiVersionConflictError(
                "The personal-Wiki row version changed before restore."
            )
        if current.lifecycle_status != "archived":
            raise PersonalWikiStateError("Only an archived Wiki can be restored.")
        if not isinstance(make_default, bool):
            raise ValueError("make_default must be a Boolean.")
        restored_at_ms = _non_negative_ms(restored_at_ms, field="restored_at_ms")
        previous_default = self.require_exact_default(
            connection, owner_user_id=current.owner_user_id
        )
        if make_default:
            cleared = connection.execute(
                """UPDATE personal_wiki_profile
                   SET is_default=0,updated_at=?,row_version=row_version+1
                   WHERE space_id=? AND owner_user_id=? AND is_default=1
                     AND lifecycle_status='active' AND row_version=?""",
                (
                    restored_at_ms,
                    previous_default.space_id,
                    current.owner_user_id,
                    previous_default.row_version,
                ),
            )
            if cleared.rowcount != 1:
                raise PersonalWikiVersionConflictError(
                    "The current default changed before restore."
                )
        cursor = connection.execute(
            """UPDATE personal_wiki_profile
               SET lifecycle_status='active',lifecycle_generation=lifecycle_generation+1,
                   is_default=?,archived_at=NULL,archived_by_user_id=NULL,
                   archive_reason=NULL,updated_at=?,row_version=row_version+1
               WHERE space_id=? AND owner_user_id=? AND lifecycle_status='archived'
                 AND row_version=?""",
            (
                1 if make_default else 0,
                restored_at_ms,
                current.space_id,
                current.owner_user_id,
                expected,
            ),
        )
        if cursor.rowcount != 1:
            raise PersonalWikiVersionConflictError(
                "The personal-Wiki state changed before restore."
            )
        default = self._require_exact_default_postcondition(
            connection, current.owner_user_id
        )
        if make_default and default.space_id != current.space_id:
            raise PersonalWikiDefaultConflictError(
                "The restored Wiki did not become the exact active default."
            )
        return self.require_owned(
            connection, owner_user_id=current.owner_user_id, space_id=current.space_id
        )

    def insert_initial_profile(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        owner_user_id: str,
        display_name: str,
        is_default: bool,
        created_at_ms: int,
    ) -> PersonalWikiProfileRecord:
        connection = _require_write_transaction(connection)
        space_id = _space_id(space_id)
        owner_user_id = _user_id(owner_user_id, field="owner_user_id")
        display_name, name_key = normalize_personal_wiki_name(display_name)
        if not isinstance(is_default, bool):
            raise ValueError("is_default must be a Boolean.")
        created_at_ms = _non_negative_ms(created_at_ms, field="created_at_ms")
        try:
            connection.execute(
                """INSERT INTO personal_wiki_profile(
                     space_id,owner_user_id,display_name,name_key,lifecycle_status,
                     lifecycle_generation,is_default,archived_at,
                     archived_by_user_id,archive_reason,created_at,updated_at,row_version
                   ) VALUES (?,?,?,?,'active',1,?,NULL,NULL,NULL,?,?,1)""",
                (
                    space_id,
                    owner_user_id,
                    display_name,
                    name_key,
                    1 if is_default else 0,
                    created_at_ms,
                    created_at_ms,
                ),
            )
        except sqlite3.IntegrityError as exc:
            message = str(exc)
            if "owner_user_id, personal_wiki_profile.name_key" in message:
                raise PersonalWikiNameConflictError(
                    "The owner already has a Wiki with this normalized name."
                ) from exc
            if "personal_wiki_profile.owner_user_id" in message:
                raise PersonalWikiDefaultConflictError(
                    "The owner already has an active default Wiki."
                ) from exc
            raise PersonalWikiStateError(
                "The personal-Wiki profile violates the v8 storage contract."
            ) from exc

        default_count = connection.execute(
            """SELECT count(*) FROM personal_wiki_profile
               WHERE owner_user_id=? AND lifecycle_status='active' AND is_default=1""",
            (owner_user_id,),
        ).fetchone()[0]
        if default_count != 1:
            raise PersonalWikiDefaultConflictError(
                "A personal-Wiki mutation must preserve exactly one active default."
            )
        profile = self.get_by_space_id(connection, space_id)
        if profile is None:
            raise PersonalWikiRepositoryError(
                "The inserted personal-Wiki profile cannot be read back."
            )
        return profile


__all__ = [
    "DEFAULT_PERSONAL_WIKI_DISPLAY_NAME",
    "PERSONAL_WIKI_ARCHIVE_REASON_MAX_LENGTH",
    "PERSONAL_WIKI_DISPLAY_NAME_MAX_LENGTH",
    "PERSONAL_WIKI_MAX_PAGE_SIZE",
    "PERSONAL_WIKI_NAME_KEY_MAX_LENGTH",
    "PersonalWikiDefaultConflictError",
    "PersonalWikiLifecycleStatus",
    "PersonalWikiNameConflictError",
    "PersonalWikiNotFoundError",
    "PersonalWikiProfilePage",
    "PersonalWikiProfileRecord",
    "PersonalWikiProfileRepository",
    "PersonalWikiRepositoryError",
    "PersonalWikiStateError",
    "PersonalWikiVersionConflictError",
    "normalize_personal_wiki_name",
    "normalize_personal_wiki_query",
]
