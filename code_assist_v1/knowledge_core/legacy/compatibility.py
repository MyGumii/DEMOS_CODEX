"""Compatibility adapters for the legacy Flask cookie-session account shape.

The canonical database remains authoritative.  This module deliberately has no
Flask or configuration imports: callers pass the signed-session mapping and an
already opened canonical connection.  A session is valid only while its
captured ``scope_epoch`` exactly matches an active account, which makes account
disable/re-enable and later membership moves revoke old cookies immediately.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass
import re
import time
from typing import Any, Callable

from werkzeug.security import check_password_hash

from ..auth_security import issue_csrf_token
from ..db import ManagedConnection
from ..repositories.users import (
    LegacyUserRecord,
    UserHardDeleteForbiddenError,
    UserRecord,
    UserRepository,
)


SESSION_KEY = "code_assist_user"
DEFAULT_INITIAL_PASSWORD = "user1234"
_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class LegacyCompatibilityError(ValueError):
    """A legacy account/session payload cannot be represented safely."""


@dataclass(frozen=True, slots=True)
class SessionResolution:
    """Result of resolving one legacy-compatible signed cookie session."""

    user: UserRecord | None
    public_user: dict[str, Any] | None
    stale: bool


def _safe_user_id(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if _USER_ID_RE.fullmatch(normalized) else ""


def _legacy_timestamp_ms(value: object, *, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return fallback
    if timestamp < 0:
        return fallback
    # Existing users.json stores Unix seconds.  Accept already-migrated
    # millisecond fixtures without multiplying them a second time.
    return timestamp * 1000 if timestamp < 100_000_000_000 else timestamp


def legacy_user_records(
    payload: Mapping[str, Any],
    *,
    now_ms: int | None = None,
) -> tuple[LegacyUserRecord, ...]:
    """Normalize the existing ``{"users": {...}}`` document for import.

    The pre-role compatibility rule is preserved: when no explicit admin
    exists, the first valid account in source order is imported as admin.
    Password hashes are carried through byte-for-byte and never returned by
    public/session adapters.
    """

    users = payload.get("users")
    if not isinstance(users, Mapping):
        raise LegacyCompatibilityError("Legacy user payload must contain an object.")
    observed_now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if observed_now < 0:
        raise LegacyCompatibilityError("now_ms must be non-negative.")

    valid_entries: list[tuple[str, Mapping[str, Any]]] = []
    explicit_admin = False
    for source_id, raw in users.items():
        if not isinstance(raw, Mapping):
            raise LegacyCompatibilityError("Every legacy user must be an object.")
        user_id = _safe_user_id(raw.get("id") or source_id)
        if not user_id:
            raise LegacyCompatibilityError("Legacy user ID is invalid.")
        if user_id != _safe_user_id(source_id):
            raise LegacyCompatibilityError("Legacy user key and ID disagree.")
        role = str(raw.get("role") or "").strip().lower()
        explicit_admin = explicit_admin or role == "admin"
        valid_entries.append((user_id, raw))

    records: list[LegacyUserRecord] = []
    for index, (user_id, raw) in enumerate(valid_entries):
        raw_role = str(raw.get("role") or "").strip().lower()
        role = raw_role if raw_role in {"admin", "user"} else (
            "admin" if not explicit_admin and index == 0 else "user"
        )
        password_hash = str(raw.get("password_hash") or "").strip()
        if not password_hash:
            raise LegacyCompatibilityError("Legacy password hash is required.")
        created_at = _legacy_timestamp_ms(
            raw.get("created_at"), fallback=observed_now
        )
        updated_at = _legacy_timestamp_ms(
            raw.get("updated_at"), fallback=created_at
        )
        if updated_at < created_at:
            raise LegacyCompatibilityError(
                "Legacy updated_at cannot precede created_at."
            )
        disabled = bool(raw.get("disabled"))
        disabled_at = None
        if disabled:
            disabled_at = _legacy_timestamp_ms(
                raw.get("disabled_at"), fallback=updated_at
            )
        must_change_password = bool(raw.get("must_change_password"))
        raw_password_changed_at = raw.get("password_changed_at")
        password_changed_at_ms = (
            None
            if raw_password_changed_at is None
            else _legacy_timestamp_ms(raw_password_changed_at, fallback=updated_at)
        )
        # Preserve auth.verify_user's legacy lazy-upgrade rule during the
        # offline import so an old default-password account cannot bypass the
        # mandatory password change merely because the JSON predates policy
        # fields.  The canonical row receives a deterministic state up front;
        # login never needs to mutate authentication metadata.
        if (
            not must_change_password
            and not raw_password_changed_at
            and check_password_hash(password_hash, DEFAULT_INITIAL_PASSWORD)
        ):
            must_change_password = True
            password_changed_at_ms = None
        records.append(
            LegacyUserRecord(
                id=user_id,
                display_name=str(raw.get("display_name") or user_id).strip()
                or user_id,
                password_hash=password_hash,
                system_role=role,
                account_status="disabled" if disabled else "active",
                current_part_id=None,
                scope_epoch=1,
                created_at_ms=created_at,
                updated_at_ms=updated_at,
                disabled_at_ms=disabled_at,
                must_change_password=must_change_password,
                password_changed_at_ms=password_changed_at_ms,
            )
        )
    return tuple(records)


class LegacyAccountAdapter:
    """Preserve legacy public/login/session shapes over canonical users."""

    __slots__ = ("_clock", "_users")

    def __init__(
        self,
        users: UserRepository | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._users = users or UserRepository()
        self._clock = clock

    @staticmethod
    def public_user(user: UserRecord) -> dict[str, Any]:
        return user.as_legacy_public_user()

    def verify_credentials(
        self,
        connection: ManagedConnection,
        *,
        user_id: str,
        password: str,
    ) -> dict[str, Any] | None:
        normalized = _safe_user_id(user_id)
        if not normalized:
            return None
        user = self._users.get(connection, normalized)
        if user is None or user.account_status != "active":
            return None
        if not check_password_hash(user.password_hash, str(password or "")):
            return None
        return self.public_user(user)

    def authenticate_and_login(
        self,
        connection: ManagedConnection,
        session: MutableMapping[str, Any],
        *,
        user_id: str,
        password: str,
    ) -> dict[str, Any] | None:
        """Verify and capture the exact canonical epoch in one DB snapshot.

        A later disable still invalidates the signed cookie because every use
        resolves its captured epoch against the canonical row.  The credential
        hash never enters the returned public/session mapping.
        """

        normalized = _safe_user_id(user_id)
        if not normalized:
            return None
        user = self._users.get(connection, normalized)
        if user is None or user.account_status != "active":
            return None
        if not check_password_hash(user.password_hash, str(password or "")):
            return None
        self.login(session, user)
        return self.public_user(user)

    def login(
        self,
        session: MutableMapping[str, Any],
        user: UserRecord,
    ) -> dict[str, Any]:
        if user.account_status != "active":
            raise LegacyCompatibilityError("Disabled accounts cannot log in.")
        session.clear()
        session_value = {
            "id": user.id,
            "display_name": user.display_name,
            "role": user.system_role,
            "is_admin": user.is_admin,
            "must_change_password": user.must_change_password,
            "scope_epoch": user.scope_epoch,
            "login_at": int(self._clock()),
        }
        session[SESSION_KEY] = session_value
        # Flask's SecureCookieSession exposes ``permanent``; plain mappings used
        # by framework-neutral tests do not.  Preserve the existing permanent
        # login behavior when that attribute is available.
        if hasattr(session, "permanent"):
            setattr(session, "permanent", True)
        issue_csrf_token(session)
        return dict(session_value)

    @staticmethod
    def logout(session: MutableMapping[str, Any]) -> None:
        session.clear()

    def resolve_session(
        self,
        connection: ManagedConnection,
        session: Mapping[str, Any],
    ) -> SessionResolution:
        value = session.get(SESSION_KEY)
        if not isinstance(value, Mapping):
            return SessionResolution(user=None, public_user=None, stale=False)
        user_id = _safe_user_id(value.get("id"))
        captured_epoch = value.get("scope_epoch")
        if (
            not user_id
            or isinstance(captured_epoch, bool)
            or not isinstance(captured_epoch, int)
            or captured_epoch < 1
        ):
            return SessionResolution(user=None, public_user=None, stale=True)
        user = self._users.get(connection, user_id)
        if (
            user is None
            or user.account_status != "active"
            or user.scope_epoch != captured_epoch
        ):
            return SessionResolution(user=None, public_user=None, stale=True)
        return SessionResolution(
            user=user,
            public_user=self.public_user(user),
            stale=False,
        )

    def reject_hard_delete(
        self,
        connection: ManagedConnection,
        user_id: str,
    ) -> None:
        """Map every legacy destructive-delete path to disable-first policy."""

        try:
            self._users.hard_delete(connection, user_id)
        except UserHardDeleteForbiddenError:
            raise
        raise UserHardDeleteForbiddenError(
            "Canonical users cannot be hard-deleted; disable the account instead."
        )


__all__ = [
    "DEFAULT_INITIAL_PASSWORD",
    "LegacyAccountAdapter",
    "LegacyCompatibilityError",
    "SESSION_KEY",
    "SessionResolution",
    "legacy_user_records",
]
