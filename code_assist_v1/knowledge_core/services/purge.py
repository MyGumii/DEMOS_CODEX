"""Administrator-only P5.6 permanent purge preview and command service.

Nothing in this module schedules a purge.  A signed, short-lived preview and an
exact human confirmation are required before the service opens the single
canonical write transaction that creates the tombstone and removes body links.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import re
from uuid import UUID, uuid5
import zlib

from ..db import ConnectionFactory, ManagedConnection, db_now_ms, transaction
from ..errors import KnowledgeCoreError
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.purge import (
    ItemPurgeRepository,
    PurgeDraft,
    PurgeIntegrityError,
    PurgeItemNotFoundError,
    PurgeSnapshot,
    PurgeStateConflictError,
    PurgeWriteResult,
)
from ..repositories.users import (
    OperationIdempotencyConflictError,
    OperationInProgressError,
    UserRecord,
    UserRepository,
    UserNotFoundError,
)
from ..scope import (
    Capability,
    ScopeAuthenticationRequiredError,
    ScopeForbiddenError,
    ScopeIntegrityError,
    ScopeNotFoundError,
    ScopeResolver,
    ScopeStaleError,
    ScopeValidationError,
    SessionPrincipal,
)


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_DEDUP_NAMESPACE = UUID("9fb13b8b-af1d-43d5-b183-256970827d12")
_AUDIT_NAMESPACE = UUID("3de795f1-57ba-45fd-babc-fd77af0ad4dc")
_PREVIEW_TTL_MS = 300_000
_TOKEN_DOMAIN = b"item-purge-preview-v1\0"
_QUEUE_CURSOR_DOMAIN = b"purge-eligible-cursor-v1\0"
_TOKEN_MAX_PAYLOAD = 16_384
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


class PurgeServiceError(KnowledgeCoreError):
    code = "STATE_CONFLICT"
    http_status = 409
    retryable = False

    def __init__(
        self, message: str, *, details: Mapping[str, object] | None = None
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


class PurgeValidationError(PurgeServiceError):
    code = "VALIDATION_ERROR"
    http_status = 400


class PurgeAuthenticationError(PurgeServiceError):
    code = "AUTHENTICATION_REQUIRED"
    http_status = 401


class PurgeAuthorizationError(PurgeServiceError):
    code = "FORBIDDEN"
    http_status = 403


class PurgeNotFoundError(PurgeServiceError):
    code = "NOT_FOUND"
    http_status = 404


class PurgeScopeStaleError(PurgeServiceError):
    code = "SCOPE_STALE"


class PurgeIdempotencyError(PurgeServiceError):
    code = "IDEMPOTENCY_CONFLICT"


class PurgeOperationInProgressError(PurgeServiceError):
    retryable = True


class PurgeServiceIntegrityError(PurgeServiceError):
    code = "INTERNAL_ERROR"
    http_status = 500


class PurgeFailpointTriggered(PurgeServiceError):
    code = "PURGE_FAILPOINT_TRIGGERED"
    http_status = 500
    retryable = True


@dataclass(frozen=True, slots=True)
class PurgePreviewCommand:
    principal: SessionPrincipal
    space_id: str
    item_id: str
    request_id: str


@dataclass(frozen=True, slots=True)
class PurgeEligiblePageCommand:
    principal: SessionPrincipal
    request_id: str
    cursor: str | None = field(default=None, repr=False)
    limit: int = 50


@dataclass(frozen=True, slots=True)
class PurgeEligiblePageResult:
    request_id: str
    items: tuple[Mapping[str, object], ...]
    next_cursor: str | None = field(repr=False)
    has_more: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "has_more": self.has_more,
            "items": [dict(value) for value in self.items],
            "next_cursor": self.next_cursor,
            "request_id": self.request_id,
        }


@dataclass(frozen=True, slots=True)
class PurgeItemCommand:
    principal: SessionPrincipal
    space_id: str
    item_id: str
    expected_item_row_version: int
    operation_id: str
    request_id: str
    preview_token: str = field(repr=False)
    confirmation: str
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class PurgePreviewResult:
    request_id: str
    item_id: str
    eligible: bool
    purge_eligible_at: str
    retained_categories: tuple[str, ...]
    deleted_categories: tuple[str, ...]
    sizes: Mapping[str, int]
    blocking_references: tuple[Mapping[str, object], ...]
    preview_token: str = field(repr=False)
    confirmation_text: str

    def as_dict(self) -> dict[str, object]:
        return {
            "blocking_references": [dict(value) for value in self.blocking_references],
            "confirmation_text": self.confirmation_text,
            "deleted_categories": list(self.deleted_categories),
            "eligible": self.eligible,
            "item_id": self.item_id,
            "preview_token": self.preview_token,
            "purge_eligible_at": self.purge_eligible_at,
            "request_id": self.request_id,
            "retained_categories": list(self.retained_categories),
            "sizes": dict(self.sizes),
        }


@dataclass(frozen=True, slots=True)
class PurgeItemResult:
    request_id: str
    operation_id: str
    status: str
    resource_id: str
    row_version: int

    def as_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "request_id": self.request_id,
            "resource_id": self.resource_id,
            "row_version": self.row_version,
            "status": self.status,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "PurgeItemResult":
        if set(value) != {"operation_id", "request_id", "resource_id", "row_version", "status"}:
            raise ValueError("Stored purge result shape is invalid.")
        return cls(
            request_id=_uuid(value["request_id"], field="request_id"),
            operation_id=_uuid(value["operation_id"], field="operation_id"),
            status=_text(value["status"], field="status", maximum=80),
            resource_id=_uuid(value["resource_id"], field="resource_id"),
            row_version=_positive(value["row_version"], field="row_version"),
        )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_ID_RE.fullmatch(value) is None:
        raise ValueError("principal.user_id must be a canonical user ID.")
    return value


def _positive(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise ValueError(f"{field} must be non-empty and at most {maximum} characters.")
    return value.strip()


def _iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _derived_uuid(namespace: UUID, *parts: str) -> str:
    return str(uuid5(namespace, "\0".join(parts)))


def _safe_request_meta(value: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key in sorted(set(value) & _REQUEST_META_ALLOWLIST):
        item = value[key]
        if isinstance(item, bool):
            result[key] = item
        elif isinstance(item, int) and not isinstance(item, bool):
            result[key] = item
        elif isinstance(item, str) and len(item) <= 1000:
            folded = item.casefold()
            if not any(fragment in folded for fragment in _SENSITIVE_META_FRAGMENTS):
                result[key] = item
    return result


def _encode_token(
    key: bytes,
    payload: Mapping[str, object],
    *,
    domain: bytes = _TOKEN_DOMAIN,
) -> str:
    body = base64.urlsafe_b64encode(zlib.compress(_canonical_json(payload), level=9)).rstrip(b"=")
    signature = hmac.digest(key, domain + body, "sha256")
    return "v1." + body.decode("ascii") + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")


def _decode_token(
    key: bytes,
    token: object,
    *,
    domain: bytes = _TOKEN_DOMAIN,
) -> dict[str, object]:
    if not isinstance(token, str) or not 32 <= len(token) <= 4096:
        raise PurgeValidationError("The purge preview token is invalid.")
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        raise PurgeValidationError("The purge preview token is invalid.")
    try:
        body = parts[1].encode("ascii")
        signature_text = parts[2].encode("ascii")
        signature = base64.b64decode(signature_text + b"=" * (-len(signature_text) % 4), altchars=b"-_", validate=True)
        compressed = base64.b64decode(body + b"=" * (-len(body) % 4), altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(compressed).rstrip(b"=") != body or base64.urlsafe_b64encode(signature).rstrip(b"=") != signature_text:
            raise ValueError("non-canonical base64")
    except (UnicodeEncodeError, ValueError) as exc:
        raise PurgeValidationError("The purge preview token is invalid.") from exc
    expected = hmac.digest(key, domain + body, "sha256")
    if not hmac.compare_digest(signature, expected):
        raise PurgeValidationError("The purge preview token is invalid.")
    try:
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(compressed, _TOKEN_MAX_PAYLOAD + 1)
        if len(raw) > _TOKEN_MAX_PAYLOAD or decompressor.unconsumed_tail or decompressor.unused_data or not decompressor.eof:
            raise ValueError("invalid compressed payload")
        raw += decompressor.flush(_TOKEN_MAX_PAYLOAD + 1 - len(raw))
        if len(raw) > _TOKEN_MAX_PAYLOAD or decompressor.unused_data:
            raise ValueError("invalid compressed payload")
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError, zlib.error) as exc:
        raise PurgeValidationError("The purge preview token is invalid.") from exc
    if not isinstance(value, dict):
        raise PurgeValidationError("The purge preview token is invalid.")
    return value


class ItemPurgeService:
    """Disabled-by-composition P5.6 manual purge boundary."""

    __slots__ = (
        "_audit",
        "_factory",
        "_failpoint",
        "_outbox",
        "_preview_hmac_key",
        "_purge",
        "_scope",
        "_users",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        scope_resolver: ScopeResolver,
        *,
        preview_hmac_key: bytes,
        purge: ItemPurgeRepository | None = None,
        users: UserRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("ItemPurgeService requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("ItemPurgeService requires a ScopeResolver.")
        if not isinstance(preview_hmac_key, bytes) or len(preview_hmac_key) < 32:
            raise TypeError("preview_hmac_key must contain at least 32 bytes.")
        self._factory = factory
        self._scope = scope_resolver
        self._preview_hmac_key = bytes(preview_hmac_key)
        self._purge = purge or ItemPurgeRepository()
        self._users = users or UserRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()
        self._failpoint = failpoint

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    @staticmethod
    def _validate_principal(principal: SessionPrincipal) -> None:
        if not isinstance(principal, SessionPrincipal):
            raise TypeError("Purge commands require a SessionPrincipal.")
        _user(principal.user_id)
        _positive(principal.captured_scope_epoch, field="principal.captured_scope_epoch")

    def _resolve_admin(
        self,
        connection: ManagedConnection,
        *,
        principal: SessionPrincipal,
        space_id: str | None,
    ) -> UserRecord:
        # Administrative data access is intentionally separate from ordinary
        # Agent/management retrieval scope.  ScopeResolver correctly refuses a
        # foreign personal/part space even for an admin, so purge authenticates
        # the canonical user snapshot and then verifies the explicit target
        # space without broadening ordinary retrieval capabilities.
        try:
            actor = self._users.require(connection, principal.user_id)
        except UserNotFoundError as exc:
            raise PurgeAuthenticationError("The authenticated user is unavailable.") from exc
        if actor.account_status != "active" or actor.scope_epoch != principal.captured_scope_epoch:
            raise PurgeScopeStaleError("The canonical actor scope changed.")
        if actor.system_role != "admin":
            raise PurgeAuthorizationError("Permanent purge requires an active administrator.")
        if space_id is not None:
            space = connection.execute(
                "SELECT status FROM knowledge_space WHERE id=?", (space_id,)
            ).fetchone()
            if space is None:
                raise PurgeNotFoundError("The requested knowledge space was not found.")
        return actor

    @staticmethod
    def _confirmation(item_id: str) -> str:
        return f"PURGE {item_id}"

    def _token_payload(
        self,
        *,
        actor: UserRecord,
        principal: SessionPrincipal,
        snapshot: PurgeSnapshot,
        issued_at_ms: int,
    ) -> dict[str, object]:
        return {
            "actor_scope_epoch": principal.captured_scope_epoch,
            "actor_user_id": actor.id,
            "confirmation": self._confirmation(snapshot.item_id),
            "eligible": snapshot.eligible,
            "expires_at_ms": issued_at_ms + _PREVIEW_TTL_MS,
            "issued_at_ms": issued_at_ms,
            "item_id": snapshot.item_id,
            "item_row_version": snapshot.row_version,
            "schema_version": 1,
            "space_id": snapshot.space_id,
            "state_sha256": snapshot.state_sha256,
        }

    @staticmethod
    def _error_details(snapshot: PurgeSnapshot | None) -> dict[str, object]:
        if snapshot is None:
            return {}
        return {
            "blocking_references": [value.as_dict() for value in snapshot.blockers],
            "eligible": snapshot.eligible,
            "purge_eligible_at": _iso_from_ms(snapshot.purge_eligible_at_ms),
            "row_version": snapshot.row_version,
        }

    def list_eligible(
        self, command: PurgeEligiblePageCommand
    ) -> PurgeEligiblePageResult:
        if not isinstance(command, PurgeEligiblePageCommand):
            raise TypeError("list_eligible requires a PurgeEligiblePageCommand.")
        self._validate_principal(command.principal)
        request_id = _uuid(command.request_id, field="request_id")
        limit = _positive(command.limit, field="limit")
        if limit > 100:
            raise PurgeValidationError("limit cannot exceed 100.")
        after_time: int | None = None
        after_item: str | None = None
        if command.cursor is not None:
            payload = _decode_token(
                self._preview_hmac_key,
                command.cursor,
                domain=_QUEUE_CURSOR_DOMAIN,
            )
            if set(payload) != {
                "after_item_id",
                "after_purge_eligible_at_ms",
                "schema_version",
                "scope",
            }:
                raise PurgeValidationError("The purge queue cursor is invalid.")
            if payload["schema_version"] != 1 or payload["scope"] != "purge_eligible":
                raise PurgeValidationError("The purge queue cursor is invalid.")
            after_item = _uuid(payload["after_item_id"], field="after_item_id")
            value = payload["after_purge_eligible_at_ms"]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PurgeValidationError("The purge queue cursor is invalid.")
            after_time = value
        connection = self._factory.open()
        try:
            with transaction(connection, mode="DEFERRED"):
                self._resolve_admin(
                    connection,
                    principal=command.principal,
                    space_id=None,
                )
                rows, has_more = self._purge.list_eligible(
                    connection,
                    now_ms=db_now_ms(connection),
                    after_purge_eligible_at_ms=after_time,
                    after_item_id=after_item,
                    limit=limit,
                )
        except PurgeIntegrityError as exc:
            raise PurgeServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()
        items = tuple(
            {
                "blocking_reference_count": row.blocking_reference_count,
                "deactivated_at": _iso_from_ms(row.deactivated_at_ms),
                "item_id": row.item_id,
                "purge_eligible_at": _iso_from_ms(row.purge_eligible_at_ms),
                "space_id": row.space_id,
                "title": row.title,
            }
            for row in rows
        )
        next_cursor = None
        if has_more:
            if not rows:
                raise PurgeServiceIntegrityError(
                    "The purge queue has_more cursor has no terminal row."
                )
            last = rows[-1]
            next_cursor = _encode_token(
                self._preview_hmac_key,
                {
                    "after_item_id": last.item_id,
                    "after_purge_eligible_at_ms": last.purge_eligible_at_ms,
                    "schema_version": 1,
                    "scope": "purge_eligible",
                },
                domain=_QUEUE_CURSOR_DOMAIN,
            )
        return PurgeEligiblePageResult(
            request_id=request_id,
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    def preview(self, command: PurgePreviewCommand) -> PurgePreviewResult:
        if not isinstance(command, PurgePreviewCommand):
            raise TypeError("preview requires a PurgePreviewCommand.")
        self._validate_principal(command.principal)
        space_id = _uuid(command.space_id, field="space_id")
        item_id = _uuid(command.item_id, field="item_id")
        request_id = _uuid(command.request_id, field="request_id")
        connection = self._factory.open()
        try:
            with transaction(connection, mode="DEFERRED"):
                actor = self._resolve_admin(
                    connection, principal=command.principal, space_id=space_id
                )
                now_ms = db_now_ms(connection)
                snapshot = self._purge.preview(
                    connection, item_id=item_id, space_id=space_id, now_ms=now_ms
                )
                payload = self._token_payload(
                    actor=actor,
                    principal=command.principal,
                    snapshot=snapshot,
                    issued_at_ms=now_ms,
                )
                token = _encode_token(self._preview_hmac_key, payload)
                if len(token) > 4096:
                    raise PurgeServiceIntegrityError("The purge preview token exceeds its contract limit.")
        except PurgeItemNotFoundError as exc:
            raise PurgeNotFoundError(str(exc)) from exc
        except PurgeStateConflictError as exc:
            raise PurgeServiceError(str(exc), details=self._error_details(exc.snapshot)) from exc
        except PurgeIntegrityError as exc:
            raise PurgeServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()
        return PurgePreviewResult(
            request_id=request_id,
            item_id=item_id,
            eligible=snapshot.eligible,
            purge_eligible_at=_iso_from_ms(snapshot.purge_eligible_at_ms),
            retained_categories=snapshot.retained_categories,
            deleted_categories=snapshot.deleted_categories,
            sizes=snapshot.sizes.as_dict(),
            blocking_references=tuple(value.as_dict() for value in snapshot.blockers),
            preview_token=token,
            confirmation_text=self._confirmation(item_id),
        )

    def _validated_token(
        self,
        *,
        command: PurgeItemCommand,
        actor: UserRecord,
        now_ms: int,
        enforce_fresh: bool = True,
    ) -> dict[str, object]:
        payload = _decode_token(self._preview_hmac_key, command.preview_token)
        expected_keys = {
            "actor_scope_epoch",
            "actor_user_id",
            "confirmation",
            "eligible",
            "expires_at_ms",
            "issued_at_ms",
            "item_id",
            "item_row_version",
            "schema_version",
            "space_id",
            "state_sha256",
        }
        if set(payload) != expected_keys:
            raise PurgeValidationError("The purge preview token is invalid.")
        issued = payload["issued_at_ms"]
        expires = payload["expires_at_ms"]
        invalid_time = (
            isinstance(issued, bool)
            or not isinstance(issued, int)
            or isinstance(expires, bool)
            or not isinstance(expires, int)
            or expires - issued != _PREVIEW_TTL_MS
            or issued > now_ms
            or now_ms >= expires
        )
        if enforce_fresh and invalid_time:
            raise PurgeServiceError("The purge preview token is expired.")
        expected = {
            "actor_scope_epoch": command.principal.captured_scope_epoch,
            "actor_user_id": actor.id,
            "confirmation": self._confirmation(command.item_id),
            "eligible": True,
            "item_id": command.item_id,
            "item_row_version": command.expected_item_row_version,
            "schema_version": 1,
            "space_id": command.space_id,
        }
        if any(payload[key] != value for key, value in expected.items()):
            raise PurgeServiceError("The purge preview no longer matches this command.")
        if command.confirmation != payload["confirmation"]:
            raise PurgeValidationError("The exact purge confirmation text is required.")
        if not isinstance(payload["state_sha256"], str) or _SHA_RE.fullmatch(payload["state_sha256"]) is None:
            raise PurgeValidationError("The purge preview state binding is invalid.")
        return payload

    def purge(self, command: PurgeItemCommand) -> PurgeItemResult:
        if not isinstance(command, PurgeItemCommand):
            raise TypeError("purge requires a PurgeItemCommand.")
        self._validate_principal(command.principal)
        space_id = _uuid(command.space_id, field="space_id")
        item_id = _uuid(command.item_id, field="item_id")
        expected_row = _positive(command.expected_item_row_version, field="expected_item_row_version")
        operation_id = _uuid(command.operation_id, field="operation_id")
        request_id = _uuid(command.request_id, field="request_id")
        _text(command.confirmation, field="confirmation", maximum=200)
        safe_meta = _safe_request_meta(command.request_meta)
        operation_type = "knowledge_item.purge"
        dedup_id = _derived_uuid(_DEDUP_NAMESPACE, command.principal.user_id, operation_type, operation_id)
        result: PurgeItemResult | None = None
        final_error: PurgeServiceError | None = None
        connection = self._factory.open()
        try:
            with transaction(connection):
                actor = self._resolve_admin(connection, principal=command.principal, space_id=space_id)
                now_ms = db_now_ms(connection)
                token = self._validated_token(
                    command=command,
                    actor=actor,
                    now_ms=now_ms,
                    enforce_fresh=False,
                )
                request_hash = sha256(
                    _canonical_json(
                        {
                            "actor_scope_epoch": command.principal.captured_scope_epoch,
                            "actor_user_id": actor.id,
                            "confirmation": command.confirmation,
                            "expected_item_row_version": expected_row,
                            "item_id": item_id,
                            "operation_type": operation_type,
                            "schema_version": 1,
                            "space_id": space_id,
                            "state_sha256": token["state_sha256"],
                        }
                    )
                ).hexdigest()
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
                    raise PurgeIdempotencyError(str(exc)) from exc
                if not begun.inserted:
                    if begun.operation.status == "committed":
                        if begun.operation.result is None:
                            raise PurgeIdempotencyError("Committed purge has no result.")
                        stored = PurgeItemResult.from_mapping(begun.operation.result)
                        if stored.operation_id != operation_id or stored.resource_id != item_id or stored.row_version != expected_row + 1:
                            raise PurgeIdempotencyError("Stored purge result is not bound to this command.")
                        result = replace(stored, request_id=request_id)
                    elif begun.operation.status == "failed_final":
                        stored = begun.operation.error
                        if not isinstance(stored, Mapping):
                            raise PurgeIdempotencyError("Failed purge has no stored error.")
                        message = stored.get("message")
                        details = stored.get("details")
                        if not isinstance(message, str) or not isinstance(details, Mapping):
                            raise PurgeIdempotencyError("Stored purge error is invalid.")
                        final_error = PurgeServiceError(message, details=details)
                    else:
                        raise PurgeOperationInProgressError("The purge is already in progress.")
                else:
                    self._validated_token(
                        command=command,
                        actor=actor,
                        now_ms=now_ms,
                        enforce_fresh=True,
                    )
                    try:
                        written = self._purge.purge(
                            connection,
                            PurgeDraft(
                                item_id=item_id,
                                space_id=space_id,
                                actor_user_id=actor.id,
                                expected_item_row_version=expected_row,
                                expected_state_sha256=str(token["state_sha256"]),
                                now_ms=now_ms,
                            ),
                            failpoint=self._hit,
                        )
                    except PurgeItemNotFoundError as exc:
                        final_error = PurgeNotFoundError(str(exc))
                    except PurgeStateConflictError as exc:
                        final_error = PurgeServiceError(str(exc), details=self._error_details(exc.snapshot))
                    if final_error is not None:
                        try:
                            self._users.fail_operation_final(
                                connection,
                                dedup_id=dedup_id,
                                error=final_error.as_error(),
                                http_status=final_error.http_status,
                                completed_at_ms=now_ms,
                            )
                        except (OperationIdempotencyConflictError, OperationInProgressError) as exc:
                            raise PurgeIdempotencyError(str(exc)) from exc
                    else:
                        self._append_events(
                            connection,
                            actor=actor,
                            command=command,
                            dedup_id=dedup_id,
                            written=written,
                            request_meta=safe_meta,
                            now_ms=now_ms,
                        )
                        result = PurgeItemResult(
                            request_id=request_id,
                            operation_id=operation_id,
                            status="purged_tombstone",
                            resource_id=item_id,
                            row_version=written.item_row_version,
                        )
                        try:
                            self._users.commit_operation(
                                connection,
                                dedup_id=dedup_id,
                                result=result.as_dict(),
                                http_status=200,
                                completed_at_ms=now_ms,
                            )
                        except (OperationIdempotencyConflictError, OperationInProgressError) as exc:
                            raise PurgeIdempotencyError(str(exc)) from exc
                        self._hit("lifecycle.purge_before_commit")
        except PurgeIntegrityError as exc:
            raise PurgeServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()
        if final_error is not None:
            raise final_error
        if result is None:
            raise PurgeServiceIntegrityError("The purge produced no terminal result.")
        return result

    def _append_events(
        self,
        connection: ManagedConnection,
        *,
        actor: UserRecord,
        command: PurgeItemCommand,
        dedup_id: str,
        written: PurgeWriteResult,
        request_meta: Mapping[str, object],
        now_ms: int,
    ) -> None:
        details: dict[str, object] = {
            "artifact_body_delete_count": written.artifact_body_delete_count,
            "episode_id": written.episode_id,
            "episode_no": written.episode_no,
            "extraction_body_delete_count": written.extraction_body_delete_count,
            "gc_candidate_count": written.gc_candidate_count,
            "retained_artifact_count": written.retained_artifact_count,
            "revision_body_delete_count": written.revision_body_delete_count,
            "revision_id": written.revision_id,
            "schema_version": 1,
            "space_generation": written.space_generation,
        }
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(_AUDIT_NAMESPACE, dedup_id, "purge"),
                occurred_at_ms=now_ms,
                request_id=command.request_id,
                operation_id=command.operation_id,
                action="knowledge_item.purge",
                outcome="purged",
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=command.space_id,
                item_id=command.item_id,
                base_revision_id=written.revision_id,
                new_revision_id=None,
                reason="Administrator-confirmed eligible body purge",
                details=details,
                request_meta=request_meta,
            ),
        )
        self._hit("lifecycle.purge_after_audit")
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"knowledge-item:purged:{dedup_id}",
                space_id=command.space_id,
                aggregate_type="knowledge_item",
                aggregate_id=command.item_id,
                aggregate_version=written.item_row_version,
                event_type="knowledge_item.purged",
                payload={"item_id": command.item_id, **details},
                available_at_ms=now_ms,
            ),
        )
        self._hit("lifecycle.purge_after_outbox")


__all__ = [
    "ItemPurgeService",
    "PurgeAuthenticationError",
    "PurgeAuthorizationError",
    "PurgeFailpointTriggered",
    "PurgeEligiblePageCommand",
    "PurgeEligiblePageResult",
    "PurgeIdempotencyError",
    "PurgeItemCommand",
    "PurgeItemResult",
    "PurgeNotFoundError",
    "PurgeOperationInProgressError",
    "PurgePreviewCommand",
    "PurgePreviewResult",
    "PurgeScopeStaleError",
    "PurgeServiceError",
    "PurgeServiceIntegrityError",
    "PurgeValidationError",
]
