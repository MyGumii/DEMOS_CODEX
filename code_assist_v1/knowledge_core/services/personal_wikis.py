"""Owner-only, bounded personal-Wiki collection and lifecycle service.

The v8 profile table is canonical.  This service owns authentication/scope
revalidation, signed owner/filter-bound cursors, transaction boundaries,
operation idempotency, audit, and durable outbox events.  It deliberately owns
no conversation binding, content search, projection rendering, scheduler, or
semantic restore-event consumer.
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

from ..db import (
    ConnectionFactory,
    ManagedConnection,
    TransactionMode,
    db_now_ms,
    transaction,
)
from ..errors import KnowledgeCoreError
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.personal_wikis import (
    PERSONAL_WIKI_ARCHIVE_REASON_MAX_LENGTH,
    PERSONAL_WIKI_MAX_PAGE_SIZE,
    PersonalWikiDefaultConflictError,
    PersonalWikiLifecycleStatus,
    PersonalWikiNameConflictError,
    PersonalWikiNotFoundError,
    PersonalWikiProfileRecord,
    PersonalWikiProfileRepository,
    PersonalWikiStateError,
    PersonalWikiVersionConflictError,
    normalize_personal_wiki_name,
    normalize_personal_wiki_query,
)
from ..repositories.users import (
    OperationIdempotencyConflictError,
    OperationInProgressError,
    UserNotFoundError,
    UserRecord,
    UserRepository,
)


_DEDUP_NAMESPACE = UUID("4fd86c4b-e01d-47da-8747-988386361e52")
_SPACE_NAMESPACE = UUID("937b4d99-67be-46b4-aa8a-ffdc61abb190")
_AUDIT_NAMESPACE = UUID("1c84d7b4-a4da-4abd-88f0-9fab9cbc47b6")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
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
_CURSOR_VERSION = 1
_CURSOR_MAX_LENGTH = 4096
_OUTBOX_EVENT_TYPES = {
    "archive": "personal_wiki.archived",
    "create": "personal_wiki.created",
    "rename": "personal_wiki.renamed",
    "restore": "personal_wiki.restored",
    "set_default": "personal_wiki.default_changed",
}


class PersonalWikiServiceError(KnowledgeCoreError):
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


class PersonalWikiAuthenticationError(PersonalWikiServiceError):
    code = "AUTHENTICATION_REQUIRED"
    http_status = 401


class PersonalWikiScopeStaleError(PersonalWikiServiceError):
    code = "SCOPE_STALE"


class PersonalWikiConcealedError(PersonalWikiServiceError):
    code = "NOT_FOUND"
    http_status = 404


class PersonalWikiServiceNameConflictError(PersonalWikiServiceError):
    code = "WIKI_NAME_CONFLICT"


class PersonalWikiServiceDefaultRequiredError(PersonalWikiServiceError):
    code = "WIKI_DEFAULT_REQUIRED"


class PersonalWikiServiceVersionError(PersonalWikiServiceError):
    code = "REVISION_CONFLICT"


class PersonalWikiServiceIdempotencyError(PersonalWikiServiceError):
    code = "IDEMPOTENCY_CONFLICT"


class PersonalWikiServiceOperationInProgressError(PersonalWikiServiceError):
    retryable = True


class PersonalWikiCursorError(PersonalWikiServiceError):
    code = "VALIDATION_ERROR"
    http_status = 400


class PersonalWikiFailpointTriggered(PersonalWikiServiceError):
    code = "PERSONAL_WIKI_FAILPOINT_TRIGGERED"
    http_status = 500
    retryable = True


@dataclass(frozen=True, slots=True)
class ListPersonalWikisQuery:
    actor_user_id: str
    actor_scope_epoch: int
    request_id: str
    lifecycle_status: PersonalWikiLifecycleStatus = "active"
    query: str | None = None
    cursor: str | None = None
    limit: int = 50


@dataclass(frozen=True, slots=True)
class PersonalWikiPageResult:
    request_id: str
    items: tuple[PersonalWikiProfileRecord, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class CreatePersonalWikiCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    display_name: str
    expected_row_version: int
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RenamePersonalWikiCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    space_id: str
    display_name: str
    expected_row_version: int
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SetDefaultPersonalWikiCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    space_id: str
    expected_row_version: int
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ArchivePersonalWikiCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    space_id: str
    expected_row_version: int
    reason: str | None = None
    request_meta: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RestorePersonalWikiCommand:
    actor_user_id: str
    actor_scope_epoch: int
    operation_id: str
    request_id: str
    space_id: str
    expected_row_version: int
    make_default: bool = False
    request_meta: Mapping[str, object] = field(default_factory=dict)


PersonalWikiMutationAction = Literal[
    "create", "rename", "set_default", "archive", "restore"
]


@dataclass(frozen=True, slots=True)
class PersonalWikiMutationResult:
    request_id: str
    operation_id: str
    action: PersonalWikiMutationAction
    status: str
    resource_id: str
    row_version: int
    lifecycle_generation: int

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "lifecycle_generation": self.lifecycle_generation,
            "operation_id": self.operation_id,
            "request_id": self.request_id,
            "resource_id": self.resource_id,
            "row_version": self.row_version,
            "status": self.status,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "PersonalWikiMutationResult":
        action = value.get("action")
        if action not in {"create", "rename", "set_default", "archive", "restore"}:
            raise ValueError("stored personal-Wiki action is invalid")
        status = value.get("status")
        if not isinstance(status, str) or not status or len(status) > 80:
            raise ValueError("stored personal-Wiki status is invalid")
        return cls(
            request_id=_uuid(value.get("request_id"), field="request_id"),
            operation_id=_uuid(value.get("operation_id"), field="operation_id"),
            action=action,  # type: ignore[arg-type]
            status=status,
            resource_id=_uuid(value.get("resource_id"), field="resource_id"),
            row_version=_positive(value.get("row_version"), field="row_version"),
            lifecycle_generation=_positive(
                value.get("lifecycle_generation"), field="lifecycle_generation"
            ),
        )


@dataclass(frozen=True, slots=True)
class _AppliedMutation:
    profile: PersonalWikiProfileRecord
    status: str
    details: Mapping[str, object]


def _uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _user_id(value: object, *, field: str = "actor_user_id") -> str:
    if not isinstance(value, str) or _USER_ID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical user ID.")
    return value


def _positive(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _canonical_json(value: Mapping[str, object]) -> str:
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Personal-Wiki metadata must be finite JSON.") from exc
    if not isinstance(json.loads(encoded), dict):
        raise ValueError("Personal-Wiki metadata must encode an object.")
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
        if raw_value is None:
            result[key] = None
        else:
            text = str(raw_value)
            if len(text) > 500:
                raise ValueError("Audit request metadata values are too long.")
            result[key] = text
    _canonical_json(result)
    return result


def _derived_uuid(namespace: UUID, *parts: str) -> str:
    return str(uuid5(namespace, "\x1f".join(parts)))


def _semantic_hash(payload: Mapping[str, object]) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _archive_reason(value: object) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > PERSONAL_WIKI_ARCHIVE_REASON_MAX_LENGTH
    ):
        raise ValueError("reason must be null or bounded nonblank trimmed text.")
    return value


class PersonalWikiLifecycleService:
    """SBE-2.1 owner profile read/mutation boundary."""

    __slots__ = (
        "_audit",
        "_cursor_secret",
        "_factory",
        "_failpoint",
        "_new_wiki_write_enabled",
        "_outbox",
        "_profiles",
        "_users",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        cursor_secret: bytes,
        users: UserRepository | None = None,
        profiles: PersonalWikiProfileRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        failpoint: Callable[[str], None] | None = None,
        new_wiki_write_enabled: bool = False,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("PersonalWikiLifecycleService requires a ConnectionFactory.")
        if not isinstance(cursor_secret, bytes) or len(cursor_secret) < 32:
            raise ValueError("cursor_secret must contain at least 32 bytes.")
        if not isinstance(new_wiki_write_enabled, bool):
            raise TypeError("new_wiki_write_enabled must be a Boolean.")
        self._factory = factory
        self._cursor_secret = bytes(cursor_secret)
        self._users = users or UserRepository()
        self._profiles = profiles or PersonalWikiProfileRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()
        self._failpoint = failpoint
        self._new_wiki_write_enabled = new_wiki_write_enabled

    def _hit(self, name: str) -> None:
        if self._failpoint is None:
            return
        try:
            self._failpoint(name)
        except PersonalWikiFailpointTriggered:
            raise
        except Exception as exc:
            raise PersonalWikiFailpointTriggered(
                f"Personal-Wiki failpoint triggered at {name}."
            ) from exc

    def _active_actor(
        self,
        connection: ManagedConnection,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
    ) -> UserRecord:
        actor_user_id = _user_id(actor_user_id)
        actor_scope_epoch = _positive(actor_scope_epoch, field="actor_scope_epoch")
        try:
            actor = self._users.require(connection, actor_user_id)
        except UserNotFoundError as exc:
            raise PersonalWikiAuthenticationError(
                "A valid active account session is required."
            ) from exc
        if actor.account_status != "active":
            raise PersonalWikiAuthenticationError(
                "A valid active account session is required."
            )
        if actor.scope_epoch != actor_scope_epoch:
            raise PersonalWikiScopeStaleError(
                "The account session scope is stale.",
                details={
                    "current_scope_epoch": actor.scope_epoch,
                    "expected_scope_epoch": actor_scope_epoch,
                },
            )
        return actor

    def _cursor_signature(self, raw: bytes) -> str:
        return hmac.new(self._cursor_secret, raw, sha256).hexdigest()

    def _encode_cursor(
        self,
        *,
        owner_user_id: str,
        lifecycle_status: PersonalWikiLifecycleStatus,
        query_key: str | None,
        last: PersonalWikiProfileRecord,
    ) -> str:
        payload: dict[str, object] = {
            "lifecycle_status": lifecycle_status,
            "owner_user_id": owner_user_id,
            "query_key": query_key,
            "row_version": last.row_version,
            "space_id": last.space_id,
            "version": _CURSOR_VERSION,
        }
        if lifecycle_status == "active":
            payload["name_key"] = last.name_key
        else:
            if last.archived_at_ms is None:
                raise PersonalWikiCursorError("The archived cursor source is invalid.")
            payload["archived_at_ms"] = last.archived_at_ms
        raw = _canonical_json(payload).encode("utf-8")
        body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        return f"{body}.{self._cursor_signature(raw)}"

    def _decode_cursor(
        self,
        value: str,
        *,
        owner_user_id: str,
        lifecycle_status: PersonalWikiLifecycleStatus,
        query_key: str | None,
    ) -> dict[str, object]:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > _CURSOR_MAX_LENGTH
            or value.count(".") != 1
        ):
            raise PersonalWikiCursorError("The personal-Wiki cursor is invalid.")
        body, signature = value.split(".", 1)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", body) or not re.fullmatch(
            r"[0-9a-f]{64}", signature
        ):
            raise PersonalWikiCursorError("The personal-Wiki cursor is invalid.")
        try:
            padded = body + "=" * (-len(body) % 4)
            raw = base64.b64decode(padded, altchars=b"-_", validate=True)
            decoded = json.loads(raw.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise PersonalWikiCursorError("The personal-Wiki cursor is invalid.") from exc
        if not hmac.compare_digest(signature, self._cursor_signature(raw)):
            raise PersonalWikiCursorError("The personal-Wiki cursor is invalid.")
        if not isinstance(decoded, dict) or _canonical_json(decoded).encode("utf-8") != raw:
            raise PersonalWikiCursorError("The personal-Wiki cursor is noncanonical.")
        common = {
            "lifecycle_status",
            "owner_user_id",
            "query_key",
            "row_version",
            "space_id",
            "version",
        }
        expected_keys = common | (
            {"name_key"} if lifecycle_status == "active" else {"archived_at_ms"}
        )
        if set(decoded) != expected_keys:
            raise PersonalWikiCursorError("The personal-Wiki cursor shape is invalid.")
        if (
            decoded.get("version") != _CURSOR_VERSION
            or decoded.get("owner_user_id") != owner_user_id
            or decoded.get("lifecycle_status") != lifecycle_status
            or decoded.get("query_key") != query_key
        ):
            raise PersonalWikiCursorError(
                "The personal-Wiki cursor does not match this owner or filter."
            )
        try:
            _uuid(decoded.get("space_id"), field="cursor.space_id")
            _positive(decoded.get("row_version"), field="cursor.row_version")
            if lifecycle_status == "active":
                name_key = decoded.get("name_key")
                if not isinstance(name_key, str) or not name_key:
                    raise ValueError("cursor.name_key is invalid")
            else:
                archived = decoded.get("archived_at_ms")
                if isinstance(archived, bool) or not isinstance(archived, int) or archived < 0:
                    raise ValueError("cursor.archived_at_ms is invalid")
        except ValueError as exc:
            raise PersonalWikiCursorError("The personal-Wiki cursor is invalid.") from exc
        return decoded

    def _require_live_cursor_anchor(
        self,
        connection: ManagedConnection,
        *,
        owner_user_id: str,
        lifecycle_status: PersonalWikiLifecycleStatus,
        query_key: str | None,
        cursor_values: Mapping[str, object],
    ) -> None:
        """Reject a signed cursor whose ordering anchor changed after issuance."""

        try:
            profile = self._profiles.require_owned(
                connection,
                owner_user_id=owner_user_id,
                space_id=str(cursor_values["space_id"]),
            )
        except (PersonalWikiNotFoundError, KeyError) as exc:
            raise PersonalWikiCursorError(
                "The personal-Wiki cursor anchor is stale."
            ) from exc
        if (
            profile.lifecycle_status != lifecycle_status
            or profile.row_version != cursor_values.get("row_version")
            or (query_key is not None and not profile.name_key.startswith(query_key))
        ):
            raise PersonalWikiCursorError(
                "The personal-Wiki cursor anchor is stale."
            )
        if lifecycle_status == "active":
            if profile.name_key != cursor_values.get("name_key"):
                raise PersonalWikiCursorError(
                    "The personal-Wiki cursor anchor is stale."
                )
        elif profile.archived_at_ms != cursor_values.get("archived_at_ms"):
            raise PersonalWikiCursorError(
                "The personal-Wiki cursor anchor is stale."
            )

    def list_profiles(self, query: ListPersonalWikisQuery) -> PersonalWikiPageResult:
        if not isinstance(query, ListPersonalWikisQuery):
            raise TypeError("query must be ListPersonalWikisQuery.")
        actor_user_id = _user_id(query.actor_user_id)
        actor_scope_epoch = _positive(query.actor_scope_epoch, field="actor_scope_epoch")
        request_id = _uuid(query.request_id, field="request_id")
        if query.lifecycle_status not in ("active", "archived"):
            raise ValueError("lifecycle_status must be active or archived.")
        if (
            isinstance(query.limit, bool)
            or not isinstance(query.limit, int)
            or not 1 <= query.limit <= PERSONAL_WIKI_MAX_PAGE_SIZE
        ):
            raise ValueError("limit must be between 1 and 100.")
        query_key = normalize_personal_wiki_query(query.query)
        cursor_values: dict[str, object] | None = None
        if query.cursor is not None:
            cursor_values = self._decode_cursor(
                query.cursor,
                owner_user_id=actor_user_id,
                lifecycle_status=query.lifecycle_status,
                query_key=query_key,
            )

        connection = self._factory.open()
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                self._active_actor(
                    connection,
                    actor_user_id=actor_user_id,
                    actor_scope_epoch=actor_scope_epoch,
                )
                if cursor_values is not None:
                    self._require_live_cursor_anchor(
                        connection,
                        owner_user_id=actor_user_id,
                        lifecycle_status=query.lifecycle_status,
                        query_key=query_key,
                        cursor_values=cursor_values,
                    )
                page = self._profiles.list_page_by_owner(
                    connection,
                    owner_user_id=actor_user_id,
                    lifecycle_status=query.lifecycle_status,
                    query=query.query,
                    after_name_key=(
                        None
                        if cursor_values is None or query.lifecycle_status != "active"
                        else str(cursor_values["name_key"])
                    ),
                    after_space_id=(
                        None if cursor_values is None else str(cursor_values["space_id"])
                    ),
                    before_archived_at_ms=(
                        None
                        if cursor_values is None or query.lifecycle_status != "archived"
                        else int(cursor_values["archived_at_ms"])
                    ),
                    limit=query.limit,
                )
                next_cursor = None
                if page.has_more:
                    if not page.items:
                        raise PersonalWikiCursorError(
                            "The personal-Wiki page cannot advance its cursor."
                        )
                    next_cursor = self._encode_cursor(
                        owner_user_id=actor_user_id,
                        lifecycle_status=query.lifecycle_status,
                        query_key=query_key,
                        last=page.items[-1],
                    )
        finally:
            connection.close()
        return PersonalWikiPageResult(
            request_id=request_id,
            items=page.items,
            next_cursor=next_cursor,
        )

    def create(self, command: CreatePersonalWikiCommand) -> PersonalWikiMutationResult:
        if not isinstance(command, CreatePersonalWikiCommand):
            raise TypeError("command must be CreatePersonalWikiCommand.")
        display_name, _ = normalize_personal_wiki_name(command.display_name)
        expected = _positive(command.expected_row_version, field="expected_row_version")
        actor = _user_id(command.actor_user_id)
        operation_id = _uuid(command.operation_id, field="operation_id")
        space_id = _derived_uuid(_SPACE_NAMESPACE, actor, operation_id)

        def apply(connection: ManagedConnection, _actor: UserRecord, now_ms: int) -> _AppliedMutation:
            profile = self._translate_repository(
                lambda: self._profiles.insert_new_wiki(
                    connection,
                    space_id=space_id,
                    owner_user_id=actor,
                    display_name=display_name,
                    expected_default_row_version=expected,
                    created_at_ms=now_ms,
                    write_enabled=self._new_wiki_write_enabled,
                )
            )
            return _AppliedMutation(
                profile=profile,
                status="created",
                details={"display_name": profile.display_name},
            )

        return self._execute(
            command=command,
            action="create",
            operation_type="personal_wiki.create",
            request_payload={
                "display_name": display_name,
                "expected_row_version": expected,
            },
            reason=None,
            apply=apply,
            http_status=201,
        )

    def rename(self, command: RenamePersonalWikiCommand) -> PersonalWikiMutationResult:
        if not isinstance(command, RenamePersonalWikiCommand):
            raise TypeError("command must be RenamePersonalWikiCommand.")
        space_id = _uuid(command.space_id, field="space_id")
        display_name, _ = normalize_personal_wiki_name(command.display_name)
        expected = _positive(command.expected_row_version, field="expected_row_version")

        def apply(connection: ManagedConnection, actor: UserRecord, now_ms: int) -> _AppliedMutation:
            before = self._owned(connection, actor.id, space_id)
            profile = self._translate_repository(
                lambda: self._profiles.rename(
                    connection,
                    owner_user_id=actor.id,
                    space_id=space_id,
                    display_name=display_name,
                    expected_row_version=expected,
                    updated_at_ms=now_ms,
                )
            )
            changed = profile.row_version != before.row_version
            return _AppliedMutation(
                profile=profile,
                status="renamed" if changed else "no_change",
                details={
                    "new_display_name": profile.display_name,
                    "old_display_name": before.display_name,
                },
            )

        return self._execute(
            command=command,
            action="rename",
            operation_type="personal_wiki.rename",
            request_payload={
                "display_name": display_name,
                "expected_row_version": expected,
                "space_id": space_id,
            },
            reason=None,
            apply=apply,
        )

    def set_default(
        self, command: SetDefaultPersonalWikiCommand
    ) -> PersonalWikiMutationResult:
        if not isinstance(command, SetDefaultPersonalWikiCommand):
            raise TypeError("command must be SetDefaultPersonalWikiCommand.")
        space_id = _uuid(command.space_id, field="space_id")
        expected = _positive(command.expected_row_version, field="expected_row_version")

        def apply(connection: ManagedConnection, actor: UserRecord, now_ms: int) -> _AppliedMutation:
            before = self._owned(connection, actor.id, space_id)
            previous = self._profiles.require_exact_default(
                connection, owner_user_id=actor.id
            )
            profile = self._translate_repository(
                lambda: self._profiles.set_default(
                    connection,
                    owner_user_id=actor.id,
                    space_id=space_id,
                    expected_row_version=expected,
                    updated_at_ms=now_ms,
                )
            )
            return _AppliedMutation(
                profile=profile,
                status="no_change" if before.is_default else "default_set",
                details={"previous_default_space_id": previous.space_id},
            )

        return self._execute(
            command=command,
            action="set_default",
            operation_type="personal_wiki.set_default",
            request_payload={"expected_row_version": expected, "space_id": space_id},
            reason=None,
            apply=apply,
        )

    def archive(self, command: ArchivePersonalWikiCommand) -> PersonalWikiMutationResult:
        if not isinstance(command, ArchivePersonalWikiCommand):
            raise TypeError("command must be ArchivePersonalWikiCommand.")
        space_id = _uuid(command.space_id, field="space_id")
        expected = _positive(command.expected_row_version, field="expected_row_version")
        reason = _archive_reason(command.reason)

        def apply(connection: ManagedConnection, actor: UserRecord, now_ms: int) -> _AppliedMutation:
            before = self._owned(connection, actor.id, space_id)
            profile = self._translate_repository(
                lambda: self._profiles.archive(
                    connection,
                    owner_user_id=actor.id,
                    space_id=space_id,
                    expected_row_version=expected,
                    archived_at_ms=now_ms,
                    reason=reason,
                )
            )
            return _AppliedMutation(
                profile=profile,
                status="archived",
                details={"previous_lifecycle_generation": before.lifecycle_generation},
            )

        return self._execute(
            command=command,
            action="archive",
            operation_type="personal_wiki.archive",
            request_payload={
                "expected_row_version": expected,
                "reason": reason,
                "space_id": space_id,
            },
            reason=reason,
            apply=apply,
        )

    def restore(self, command: RestorePersonalWikiCommand) -> PersonalWikiMutationResult:
        if not isinstance(command, RestorePersonalWikiCommand):
            raise TypeError("command must be RestorePersonalWikiCommand.")
        space_id = _uuid(command.space_id, field="space_id")
        expected = _positive(command.expected_row_version, field="expected_row_version")
        if not isinstance(command.make_default, bool):
            raise ValueError("make_default must be a Boolean.")

        def apply(connection: ManagedConnection, actor: UserRecord, now_ms: int) -> _AppliedMutation:
            before = self._owned(connection, actor.id, space_id)
            previous = self._profiles.require_exact_default(
                connection, owner_user_id=actor.id
            )
            profile = self._translate_repository(
                lambda: self._profiles.restore(
                    connection,
                    owner_user_id=actor.id,
                    space_id=space_id,
                    expected_row_version=expected,
                    restored_at_ms=now_ms,
                    make_default=command.make_default,
                )
            )
            return _AppliedMutation(
                profile=profile,
                status="restored",
                details={
                    "make_default": command.make_default,
                    "previous_default_space_id": previous.space_id,
                    "previous_lifecycle_generation": before.lifecycle_generation,
                },
            )

        return self._execute(
            command=command,
            action="restore",
            operation_type="personal_wiki.restore",
            request_payload={
                "expected_row_version": expected,
                "make_default": command.make_default,
                "space_id": space_id,
            },
            reason=None,
            apply=apply,
        )

    def _owned(
        self, connection: ManagedConnection, owner_user_id: str, space_id: str
    ) -> PersonalWikiProfileRecord:
        try:
            return self._profiles.require_owned(
                connection, owner_user_id=owner_user_id, space_id=space_id
            )
        except PersonalWikiNotFoundError as exc:
            raise PersonalWikiConcealedError(
                "The requested personal Wiki was not found."
            ) from exc

    @staticmethod
    def _translate_repository(
        function: Callable[[], PersonalWikiProfileRecord],
    ) -> PersonalWikiProfileRecord:
        try:
            return function()
        except PersonalWikiNotFoundError as exc:
            raise PersonalWikiConcealedError(
                "The requested personal Wiki was not found."
            ) from exc
        except PersonalWikiNameConflictError as exc:
            raise PersonalWikiServiceNameConflictError(str(exc)) from exc
        except PersonalWikiDefaultConflictError as exc:
            raise PersonalWikiServiceDefaultRequiredError(str(exc)) from exc
        except PersonalWikiVersionConflictError as exc:
            raise PersonalWikiServiceVersionError(str(exc)) from exc
        except PersonalWikiStateError as exc:
            raise PersonalWikiServiceError(str(exc)) from exc

    def _execute(
        self,
        *,
        command: object,
        action: PersonalWikiMutationAction,
        operation_type: str,
        request_payload: Mapping[str, object],
        reason: str | None,
        apply: Callable[[ManagedConnection, UserRecord, int], _AppliedMutation],
        http_status: int = 200,
    ) -> PersonalWikiMutationResult:
        actor_user_id = _user_id(getattr(command, "actor_user_id", None))
        actor_scope_epoch = _positive(
            getattr(command, "actor_scope_epoch", None), field="actor_scope_epoch"
        )
        operation_id = _uuid(getattr(command, "operation_id", None), field="operation_id")
        request_id = _uuid(getattr(command, "request_id", None), field="request_id")
        request_meta = _safe_request_meta(getattr(command, "request_meta", None))
        semantic_payload = {
            "action": action,
            "actor_scope_epoch": actor_scope_epoch,
            "actor_user_id": actor_user_id,
            "operation_type": operation_type,
            "request": dict(request_payload),
            "schema_version": 1,
        }
        request_hash = _semantic_hash(semantic_payload)
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE, actor_user_id, operation_type, operation_id
        )
        audit_uuid = _derived_uuid(_AUDIT_NAMESPACE, dedup_id, "mutation")

        result: PersonalWikiMutationResult | None = None
        connection = self._factory.open()
        try:
            with transaction(connection):
                actor = self._active_actor(
                    connection,
                    actor_user_id=actor_user_id,
                    actor_scope_epoch=actor_scope_epoch,
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
                    raise PersonalWikiServiceIdempotencyError(str(exc)) from exc
                if not begun.inserted:
                    if begun.operation.status == "committed":
                        stored = begun.operation.result
                        if not isinstance(stored, Mapping):
                            raise PersonalWikiServiceIdempotencyError(
                                "The committed Wiki operation has no valid result."
                            )
                        try:
                            replay = PersonalWikiMutationResult.from_mapping(stored)
                        except ValueError as exc:
                            raise PersonalWikiServiceIdempotencyError(
                                "The committed Wiki operation result is invalid."
                            ) from exc
                        if replay.operation_id != operation_id or replay.action != action:
                            raise PersonalWikiServiceIdempotencyError(
                                "The committed Wiki operation identity is invalid."
                            )
                        result = replace(replay, request_id=request_id)
                    else:
                        raise PersonalWikiServiceOperationInProgressError(
                            "The personal-Wiki operation is already in progress."
                        )
                else:
                    applied = apply(connection, actor, now_ms)
                    self._hit("personal_wiki.after_domain")
                    profile = applied.profile
                    details = {
                        "action": action,
                        "is_default": profile.is_default,
                        "lifecycle_generation": profile.lifecycle_generation,
                        "lifecycle_status": profile.lifecycle_status,
                        "profile_row_version": profile.row_version,
                        "schema_version": 1,
                        **dict(applied.details),
                    }
                    audit = self._audit.append(
                        connection,
                        AuditEventDraft(
                            event_uuid=audit_uuid,
                            occurred_at_ms=now_ms,
                            request_id=request_id,
                            operation_id=operation_id,
                            action=operation_type,
                            outcome=applied.status,
                            actor_kind="user",
                            actor_user_id=actor.id,
                            actor_display_name=actor.display_name,
                            actor_role_snapshot=actor.system_role,
                            space_id=profile.space_id,
                            reason=reason,
                            details=details,
                            request_meta=request_meta,
                        ),
                    )
                    self._hit("personal_wiki.after_audit")
                    self._outbox.append(
                        connection,
                        OutboxEventDraft(
                            logical_key=f"personal-wiki:{dedup_id}:{action}:v1",
                            space_id=profile.space_id,
                            aggregate_type="personal_wiki",
                            aggregate_id=profile.space_id,
                            aggregate_version=profile.row_version,
                            event_type=_OUTBOX_EVENT_TYPES[action],
                            payload={
                                "action": action,
                                "audit_event_id": audit.id,
                                "is_default": profile.is_default,
                                "lifecycle_generation": profile.lifecycle_generation,
                                "lifecycle_status": profile.lifecycle_status,
                                "operation_id": operation_id,
                                "owner_user_id": profile.owner_user_id,
                                "profile_row_version": profile.row_version,
                                "schema_version": 1,
                                "space_id": profile.space_id,
                                **dict(applied.details),
                            },
                            available_at_ms=now_ms,
                        ),
                    )
                    self._hit("personal_wiki.after_outbox")
                    result = PersonalWikiMutationResult(
                        request_id=request_id,
                        operation_id=operation_id,
                        action=action,
                        status=applied.status,
                        resource_id=profile.space_id,
                        row_version=profile.row_version,
                        lifecycle_generation=profile.lifecycle_generation,
                    )
                    try:
                        self._users.commit_operation(
                            connection,
                            dedup_id=dedup_id,
                            result=result.as_dict(),
                            http_status=http_status,
                            completed_at_ms=now_ms,
                        )
                    except (
                        OperationIdempotencyConflictError,
                        OperationInProgressError,
                    ) as exc:
                        raise PersonalWikiServiceIdempotencyError(str(exc)) from exc
                    self._hit("personal_wiki.before_commit")
        finally:
            connection.close()
        if result is None:  # pragma: no cover - every transaction branch is terminal
            raise PersonalWikiServiceError(
                "The personal-Wiki operation produced no terminal result."
            )
        return result


__all__ = [
    "ArchivePersonalWikiCommand",
    "CreatePersonalWikiCommand",
    "ListPersonalWikisQuery",
    "PersonalWikiAuthenticationError",
    "PersonalWikiConcealedError",
    "PersonalWikiCursorError",
    "PersonalWikiFailpointTriggered",
    "PersonalWikiLifecycleService",
    "PersonalWikiMutationResult",
    "PersonalWikiPageResult",
    "PersonalWikiScopeStaleError",
    "PersonalWikiServiceDefaultRequiredError",
    "PersonalWikiServiceError",
    "PersonalWikiServiceIdempotencyError",
    "PersonalWikiServiceNameConflictError",
    "PersonalWikiServiceOperationInProgressError",
    "PersonalWikiServiceVersionError",
    "RenamePersonalWikiCommand",
    "RestorePersonalWikiCommand",
    "SetDefaultPersonalWikiCommand",
]
