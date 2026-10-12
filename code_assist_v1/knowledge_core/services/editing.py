"""Persistent fenced edit-lease service.

P5.1 and P5.2 deliberately expose no route and enable no rollout flag.  The
services own each short SQLite transaction, re-resolve canonical scope at the
write boundary, persist only keyed token digests, and compose lease and
immutable-edit changes with idempotency, audit, and outbox rows.
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
from typing import Literal
from uuid import UUID, uuid5

from ..db import ConnectionFactory, ManagedConnection, db_now_ms, transaction
from ..errors import KnowledgeCoreError, KnowledgeCoreTransactionError
from ..repositories.audit import AuditEventDraft, AuditRepository
from ..repositories.editing import (
    ItemEditConflictError,
    ItemEditDraft,
    ItemEditIntegrityError,
    ItemEditNotFoundError,
    ItemEditRepository,
    ItemEditWriteResult,
)
from ..repositories.jobs import (
    JobEffectConflictError,
    JobEnqueueDraft,
    JobRepository,
    JobRepositoryError,
    JobStateConflictError,
    canonical_job_input_sha256,
)
from ..repositories.leases import (
    LeaseHeldRepositoryError,
    LeaseIntegrityError,
    LeaseItemNotFoundError,
    LeaseLostRepositoryError,
    LeaseRecord,
    LeaseRepository,
    LeaseRevisionConflictRepositoryError,
    LeaseStateConflictRepositoryError,
    LeaseValidationProof,
)
from ..repositories.outbox import OutboxEventDraft, OutboxRepository
from ..repositories.users import (
    OperationIdempotencyConflictError,
    OperationInProgressError,
    UserNotFoundError,
    UserRecord,
    UserRepository,
)
from ..scope import (
    Capability,
    ScopeAuthenticationRequiredError,
    ScopeForbiddenError,
    ScopeIntegrityError,
    ScopeNotFoundError,
    ResolvedScope,
    ScopeResolver,
    ScopeStaleError,
    ScopeValidationError,
    SessionPrincipal,
)


DEFAULT_HEARTBEAT_INTERVAL_MS = 30_000
DEFAULT_STALE_AFTER_MS = 5 * 60_000
DEFAULT_ABSOLUTE_AFTER_MS = 2 * 60 * 60_000

_TOKEN_CONTEXT = b"knowledge-lease-v1\x00"
_LEASE_ID_NAMESPACE = UUID("a8561e13-6662-43a8-9d61-e67986a37444")
_DEDUP_NAMESPACE = UUID("f10d6e5d-bf94-453a-a469-71a51120a1f0")
_AUDIT_NAMESPACE = UUID("8f34eb88-e94c-4a30-8477-df1ca82a1d94")
_EDIT_REVISION_NAMESPACE = UUID("c7943de0-10c9-4f21-9288-68e45a094216")
_EDIT_TRANSITION_NAMESPACE = UUID("aec2dcc4-ae1a-40b4-b10e-a91eb15d120e")
_EDIT_JOB_NAMESPACE = UUID("4f4db688-d266-46a6-973b-9497d334800a")
_EDIT_JOB_DEDUP_NAMESPACE = UUID("86c94b50-621a-40c2-8176-61074b0bbccc")
_EDIT_RULE_VERSION = "curation-rescan-v1"
_MAX_EDIT_MARKDOWN_CHARACTERS = 2_000_000
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$", re.ASCII)
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$", re.ASCII)
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


class LeaseServiceError(KnowledgeCoreError):
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


class LeaseValidationError(LeaseServiceError):
    code = "VALIDATION_ERROR"
    http_status = 400


class LeaseAuthenticationError(LeaseServiceError):
    code = "AUTHENTICATION_REQUIRED"
    http_status = 401


class LeaseAuthorizationError(LeaseServiceError):
    code = "FORBIDDEN"
    http_status = 403


class LeaseNotFoundError(LeaseServiceError):
    code = "NOT_FOUND"
    http_status = 404


class LeaseScopeStaleError(LeaseServiceError):
    code = "SCOPE_STALE"


class LeaseHeldError(LeaseServiceError):
    code = "LEASE_HELD"
    http_status = 423


class LeaseLostError(LeaseServiceError):
    code = "LEASE_LOST"
    http_status = 423


class LeaseRevisionConflictError(LeaseServiceError):
    code = "REVISION_CONFLICT"


class LeaseIdempotencyError(LeaseServiceError):
    code = "IDEMPOTENCY_CONFLICT"


class LeaseOperationInProgressError(LeaseServiceError):
    retryable = True


class LeaseServiceIntegrityError(LeaseServiceError):
    code = "INTERNAL_ERROR"
    http_status = 500


class LeaseFailpointTriggered(LeaseServiceError):
    code = "LEASE_FAILPOINT_TRIGGERED"
    http_status = 500
    retryable = True


class ItemEditServiceIntegrityError(LeaseServiceIntegrityError):
    """A canonical edit effect or rescan binding failed closed."""


@dataclass(frozen=True, slots=True)
class AcquireLeaseCommand:
    principal: SessionPrincipal
    space_id: str
    item_id: str
    editor_session_id: str
    operation_id: str
    request_id: str
    raw_token: str = field(repr=False)
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class HeartbeatLeaseCommand:
    principal: SessionPrincipal
    space_id: str
    item_id: str
    editor_session_id: str
    lease_id: str
    fencing_generation: int
    request_id: str
    raw_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ReleaseLeaseCommand:
    principal: SessionPrincipal
    space_id: str
    item_id: str
    editor_session_id: str
    lease_id: str
    fencing_generation: int
    operation_id: str
    request_id: str
    raw_token: str = field(repr=False)
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class ForceReleaseLeaseCommand:
    principal: SessionPrincipal
    space_id: str
    item_id: str
    operation_id: str
    request_id: str
    reason: str
    confirmation: str
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class EditFenceCheck:
    principal: SessionPrincipal
    space_id: str
    item_id: str
    editor_session_id: str
    lease_id: str
    fencing_generation: int
    expected_item_row_version: int
    expected_revision_id: str
    raw_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SaveItemCommand:
    principal: SessionPrincipal
    space_id: str
    item_id: str
    editor_session_id: str
    lease_id: str
    fencing_generation: int
    expected_item_row_version: int
    expected_revision_id: str
    operation_id: str
    request_id: str
    markdown: str = field(repr=False)
    reason: str
    raw_token: str = field(repr=False)
    request_meta: Mapping[str, object] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class _PreparedMarkdownEdit:
    markdown: str = field(repr=False)
    raw_sha256: str
    normalized_sha256: str
    size_bytes: int
    reason: str


@dataclass(frozen=True, slots=True)
class LeaseAcquireResult:
    request_id: str
    operation_id: str
    lease_id: str
    fencing_generation: int
    base_revision_id: str
    expires_at: str
    absolute_expires_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "absolute_expires_at": self.absolute_expires_at,
            "base_revision_id": self.base_revision_id,
            "expires_at": self.expires_at,
            "fencing_generation": self.fencing_generation,
            "lease_id": self.lease_id,
            "operation_id": self.operation_id,
            "request_id": self.request_id,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "LeaseAcquireResult":
        if set(value) != {
            "absolute_expires_at",
            "base_revision_id",
            "expires_at",
            "fencing_generation",
            "lease_id",
            "operation_id",
            "request_id",
        }:
            raise ValueError("Stored lease-acquire result shape is invalid.")
        return cls(
            request_id=_uuid(value["request_id"], field_name="request_id"),
            operation_id=_uuid(value["operation_id"], field_name="operation_id"),
            lease_id=_uuid(value["lease_id"], field_name="lease_id"),
            fencing_generation=_positive_int(
                value["fencing_generation"], field_name="fencing_generation"
            ),
            base_revision_id=_uuid(
                value["base_revision_id"], field_name="base_revision_id"
            ),
            expires_at=_utc_iso(value["expires_at"], field_name="expires_at"),
            absolute_expires_at=_utc_iso(
                value["absolute_expires_at"], field_name="absolute_expires_at"
            ),
        )


@dataclass(frozen=True, slots=True)
class HeartbeatLeaseResult:
    request_id: str
    lease_id: str
    fencing_generation: int
    expires_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "expires_at": self.expires_at,
            "fencing_generation": self.fencing_generation,
            "lease_id": self.lease_id,
            "request_id": self.request_id,
        }


@dataclass(frozen=True, slots=True)
class LeaseMutationResult:
    request_id: str
    operation_id: str
    status: Literal["released", "force_released"]
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
    def from_mapping(cls, value: Mapping[str, object]) -> "LeaseMutationResult":
        if set(value) != {
            "operation_id",
            "request_id",
            "resource_id",
            "row_version",
            "status",
        }:
            raise ValueError("Stored lease mutation result shape is invalid.")
        status = value["status"]
        if status not in ("released", "force_released"):
            raise ValueError("Stored lease mutation status is invalid.")
        return cls(
            request_id=_uuid(value["request_id"], field_name="request_id"),
            operation_id=_uuid(value["operation_id"], field_name="operation_id"),
            status=status,
            resource_id=_uuid(value["resource_id"], field_name="resource_id"),
            row_version=_positive_int(value["row_version"], field_name="row_version"),
        )


@dataclass(frozen=True, slots=True)
class ItemEditResult:
    request_id: str
    operation_id: str
    item_id: str
    revision_id: str
    row_version: int
    status: Literal["updated"] = "updated"

    def as_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "operation_id": self.operation_id,
            "request_id": self.request_id,
            "revision_id": self.revision_id,
            "row_version": self.row_version,
            "status": self.status,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ItemEditResult":
        if set(value) != {
            "item_id",
            "operation_id",
            "request_id",
            "revision_id",
            "row_version",
            "status",
        } or value.get("status") != "updated":
            raise ValueError("Stored item-edit result shape is invalid.")
        return cls(
            request_id=_uuid(value["request_id"], field_name="request_id"),
            operation_id=_uuid(value["operation_id"], field_name="operation_id"),
            item_id=_uuid(value["item_id"], field_name="item_id"),
            revision_id=_uuid(value["revision_id"], field_name="revision_id"),
            row_version=_positive_int(value["row_version"], field_name="row_version"),
        )


class LeaseHmacKeyRing:
    """Immutable in-memory view of externally protected lease HMAC keys."""

    __slots__ = ("__active_key_id", "__keys")

    def __init__(self, *, active_key_id: str, keys: Mapping[str, bytes]) -> None:
        if not isinstance(active_key_id, str) or _KEY_ID_RE.fullmatch(active_key_id) is None:
            raise ValueError("active_key_id must use the canonical key ID format.")
        if not isinstance(keys, Mapping) or not keys:
            raise ValueError("Lease HMAC keys must be a non-empty mapping.")
        prepared: dict[str, bytes] = {}
        for key_id, key in keys.items():
            if not isinstance(key_id, str) or _KEY_ID_RE.fullmatch(key_id) is None:
                raise ValueError("Every lease HMAC key ID must be canonical.")
            if not isinstance(key, bytes) or len(key) != 32:
                raise ValueError("Every lease HMAC key must contain exactly 32 bytes.")
            prepared[key_id] = bytes(key)
        if active_key_id not in prepared:
            raise ValueError("The active lease HMAC key is unavailable.")
        self.__active_key_id = active_key_id
        self.__keys = tuple(sorted(prepared.items()))

    @property
    def active_key_id(self) -> str:
        return self.__active_key_id

    def digest(self, *, key_id: str, item_id: str, raw_token: bytes) -> bytes:
        item_id = _uuid(item_id, field_name="item_id")
        if not isinstance(raw_token, bytes) or len(raw_token) != 32:
            raise ValueError("raw_token must contain exactly 32 bytes.")
        key = next((value for name, value in self.__keys if name == key_id), None)
        if key is None:
            raise LeaseServiceIntegrityError(
                "The verification key for a held lease is unavailable."
            )
        return hmac.digest(
            key,
            _TOKEN_CONTEXT + item_id.encode("utf-8") + b"\x00" + raw_token,
            "sha256",
        )

    def active_digest(self, *, item_id: str, raw_token: bytes) -> tuple[str, bytes]:
        return self.__active_key_id, self.digest(
            key_id=self.__active_key_id, item_id=item_id, raw_token=raw_token
        )

    def candidate_digests(
        self, *, item_id: str, raw_token: bytes
    ) -> tuple[tuple[str, bytes], ...]:
        order = (self.__active_key_id,) + tuple(
            name for name, _ in self.__keys if name != self.__active_key_id
        )
        return tuple(
            (name, self.digest(key_id=name, item_id=item_id, raw_token=raw_token))
            for name in order
        )


def _uuid(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a canonical lowercase UUID.")
    return value


def _user_id(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _USER_ID_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a canonical user ID.")
    return value


def _positive_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer.")
    return value


def _utc_iso(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a UTC timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a UTC timestamp.") from exc
    if parsed.tzinfo != timezone.utc:
        raise ValueError(f"{field_name} must use the UTC Z timezone.")
    canonical = parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if canonical != value:
        raise ValueError(f"{field_name} must be canonical UTC milliseconds.")
    return value


def _iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _decode_token(value: object) -> bytes:
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        raise LeaseValidationError(
            "The lease token must be 43 unpadded base64url characters."
        )
    try:
        raw = base64.b64decode(value + "=", altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise LeaseValidationError("The lease token encoding is invalid.") from exc
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value:
        raise LeaseValidationError("The lease token must decode to exactly 32 bytes.")
    return raw


def _prepare_markdown_edit(markdown: object, reason: object) -> _PreparedMarkdownEdit:
    if (
        not isinstance(markdown, str)
        or not 1 <= len(markdown) <= _MAX_EDIT_MARKDOWN_CHARACTERS
        or "\x00" in markdown
    ):
        raise LeaseValidationError(
            "Markdown must contain 1..2000000 non-NUL characters."
        )
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000:
        raise LeaseValidationError("An edit reason is required.")
    try:
        raw = markdown.encode("utf-8")
        normalized = markdown.replace("\r\n", "\n").replace("\r", "\n").encode(
            "utf-8"
        )
    except UnicodeEncodeError as exc:
        raise LeaseValidationError("Markdown must be valid Unicode.") from exc
    return _PreparedMarkdownEdit(
        markdown=markdown,
        raw_sha256=sha256(raw).hexdigest(),
        normalized_sha256=sha256(normalized).hexdigest(),
        size_bytes=len(raw),
        reason=reason.strip(),
    )


def _canonical_json(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ValueError("The lease command must encode as finite JSON.") from exc


def _safe_request_meta(
    value: object, *, secret_values: tuple[str, ...] = ()
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("request_meta must be a mapping.")
    safe: dict[str, object] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            raise ValueError("request_meta keys must be strings.")
        folded = key.casefold()
        if any(fragment in folded for fragment in _SENSITIVE_META_FRAGMENTS):
            continue
        if key not in _REQUEST_META_ALLOWLIST:
            continue
        if raw is None:
            safe[key] = None
        elif isinstance(raw, str) and len(raw) <= 1000:
            if any(secret and secret in raw for secret in secret_values):
                continue
            safe[key] = raw
        else:
            raise ValueError("Allowed request metadata must be bounded text or null.")
    return safe


def _derived_uuid(namespace: UUID, *parts: str) -> str:
    return str(uuid5(namespace, "\x1f".join(parts)))


def lease_id_for(item_id: str, fencing_generation: int) -> str:
    return _derived_uuid(
        _LEASE_ID_NAMESPACE,
        _uuid(item_id, field_name="item_id"),
        str(_positive_int(fencing_generation, field_name="fencing_generation")),
    )


def _draft_recovery(
    *, current_revision_id: str | None = None, current_row_version: int | None = None
) -> dict[str, object]:
    value: dict[str, object] = {
        "actions": ["view_current", "copy_draft", "reacquire_and_compare"],
        "force_overwrite_allowed": False,
        "preserve_local_draft": True,
    }
    if current_revision_id is not None:
        value["current_revision_id"] = current_revision_id
    if current_row_version is not None:
        value["current_row_version"] = current_row_version
    return value


class FencedLeaseService:
    """Transaction-owning acquire/heartbeat/release/force-release service."""

    __slots__ = (
        "_absolute_after_ms",
        "_audit",
        "_factory",
        "_failpoint",
        "_keys",
        "_leases",
        "_outbox",
        "_scope",
        "_stale_after_ms",
        "_users",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        key_ring: LeaseHmacKeyRing,
        scope_resolver: ScopeResolver,
        stale_after_ms: int = DEFAULT_STALE_AFTER_MS,
        absolute_after_ms: int = DEFAULT_ABSOLUTE_AFTER_MS,
        leases: LeaseRepository | None = None,
        users: UserRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("FencedLeaseService requires a ConnectionFactory.")
        if not isinstance(key_ring, LeaseHmacKeyRing):
            raise TypeError("FencedLeaseService requires a LeaseHmacKeyRing.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("FencedLeaseService requires a ScopeResolver.")
        stale_after_ms = _positive_int(stale_after_ms, field_name="stale_after_ms")
        absolute_after_ms = _positive_int(
            absolute_after_ms, field_name="absolute_after_ms"
        )
        if stale_after_ms > absolute_after_ms:
            raise ValueError("stale_after_ms cannot exceed absolute_after_ms.")
        self._factory = factory
        self._keys = key_ring
        self._scope = scope_resolver
        self._stale_after_ms = stale_after_ms
        self._absolute_after_ms = absolute_after_ms
        self._leases = leases or LeaseRepository()
        self._users = users or UserRepository()
        self._audit = audit or AuditRepository()
        self._outbox = outbox or OutboxRepository()
        self._failpoint = failpoint

    def _hit(self, name: str) -> None:
        if self._failpoint is not None:
            self._failpoint(name)

    def _resolve_scope(
        self,
        connection: ManagedConnection,
        *,
        principal: SessionPrincipal,
        space_id: str,
        capability: Capability,
    ) -> tuple[UserRecord, ResolvedScope]:
        try:
            resolved = self._scope.resolve_in_transaction(
                connection, principal, space_id, capability
            )
        except ScopeAuthenticationRequiredError as exc:
            raise LeaseAuthenticationError(str(exc)) from exc
        except ScopeStaleError as exc:
            raise LeaseScopeStaleError(str(exc)) from exc
        except ScopeNotFoundError as exc:
            raise LeaseNotFoundError(str(exc)) from exc
        except ScopeForbiddenError as exc:
            raise LeaseAuthorizationError(str(exc)) from exc
        except ScopeValidationError as exc:
            raise LeaseValidationError(str(exc)) from exc
        except ScopeIntegrityError as exc:
            raise LeaseServiceIntegrityError(str(exc)) from exc
        actor = self._users.require(connection, resolved.actor_user_id)
        if actor.account_status != "active" or actor.scope_epoch != resolved.scope_epoch:
            raise LeaseScopeStaleError("The canonical actor scope changed.")
        return actor, resolved

    def _resolve(
        self,
        connection: ManagedConnection,
        *,
        principal: SessionPrincipal,
        space_id: str,
        capability: Capability,
    ) -> UserRecord:
        return self._resolve_scope(
            connection,
            principal=principal,
            space_id=space_id,
            capability=capability,
        )[0]

    @staticmethod
    def _validate_common(
        *,
        principal: SessionPrincipal,
        space_id: str,
        item_id: str,
        editor_session_id: str | None = None,
        operation_id: str | None = None,
        request_id: str | None = None,
        lease_id: str | None = None,
        fencing_generation: int | None = None,
    ) -> None:
        if not isinstance(principal, SessionPrincipal):
            raise TypeError("Lease commands require a SessionPrincipal.")
        _user_id(principal.user_id, field_name="principal.user_id")
        _positive_int(
            principal.captured_scope_epoch,
            field_name="principal.captured_scope_epoch",
        )
        _uuid(space_id, field_name="space_id")
        _uuid(item_id, field_name="item_id")
        if editor_session_id is not None:
            _uuid(editor_session_id, field_name="editor_session_id")
        if operation_id is not None:
            _uuid(operation_id, field_name="operation_id")
        if request_id is not None:
            _uuid(request_id, field_name="request_id")
        if lease_id is not None:
            _uuid(lease_id, field_name="lease_id")
        if fencing_generation is not None:
            _positive_int(fencing_generation, field_name="fencing_generation")

    def _request_digest(
        self,
        *,
        operation_type: str,
        principal: SessionPrincipal,
        space_id: str,
        item_id: str,
        editor_session_id: str | None = None,
        lease_id: str | None = None,
        fencing_generation: int | None = None,
        key_id: str | None = None,
        token_hmac: bytes | None = None,
        reason: str | None = None,
        extra: Mapping[str, object] | None = None,
    ) -> str:
        payload: dict[str, object] = {
            "actor_scope_epoch": principal.captured_scope_epoch,
            "actor_user_id": principal.user_id,
            "item_id": item_id,
            "operation_type": operation_type,
            "schema_version": 1,
            "space_id": space_id,
        }
        if editor_session_id is not None:
            payload["editor_session_id"] = editor_session_id
        if lease_id is not None:
            payload["lease_id"] = lease_id
        if fencing_generation is not None:
            payload["fencing_generation"] = fencing_generation
        if key_id is not None:
            payload["hmac_key_id"] = key_id
        if token_hmac is not None:
            payload["token_hmac_sha256"] = token_hmac.hex()
        if reason is not None:
            payload["reason"] = reason
        if extra is not None:
            payload["command"] = dict(extra)
        return sha256(_canonical_json(payload)).hexdigest()

    def _operation_token_binding(
        self,
        connection: ManagedConnection,
        *,
        operation_type: str,
        operation_id: str,
        principal: SessionPrincipal,
        space_id: str,
        item_id: str,
        editor_session_id: str,
        lease_id: str | None,
        fencing_generation: int | None,
        raw_token: bytes,
        extra: Mapping[str, object] | None = None,
    ) -> tuple[str, str, bytes, str]:
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE, principal.user_id, operation_type, operation_id
        )
        existing = self._users.get_operation(connection, dedup_id)
        candidates = list(
            self._keys.candidate_digests(item_id=item_id, raw_token=raw_token)
        )
        for key_id, token_hmac in candidates:
            request_hash = self._request_digest(
                operation_type=operation_type,
                principal=principal,
                space_id=space_id,
                item_id=item_id,
                editor_session_id=editor_session_id,
                lease_id=lease_id,
                fencing_generation=fencing_generation,
                key_id=key_id,
                token_hmac=token_hmac,
                extra=extra,
            )
            if existing is not None and existing.request_sha256 == request_hash:
                return dedup_id, key_id, token_hmac, request_hash
        # A first release after key rotation must verify with the key captured
        # by the held lease.  A terminal replay has no holder fields, so the
        # loop above instead finds the original request hash across all keys.
        try:
            held = self._leases.require_item(
                connection, item_id=item_id, space_id=space_id
            ).lease
        except LeaseItemNotFoundError:
            held = None
        if held is not None and held.held and held.hmac_key_id is not None:
            candidates.sort(key=lambda pair: pair[0] != held.hmac_key_id)
        key_id, token_hmac = candidates[0]
        request_hash = self._request_digest(
            operation_type=operation_type,
            principal=principal,
            space_id=space_id,
            item_id=item_id,
            editor_session_id=editor_session_id,
            lease_id=lease_id,
            fencing_generation=fencing_generation,
            key_id=key_id,
            token_hmac=token_hmac,
            extra=extra,
        )
        return dedup_id, key_id, token_hmac, request_hash

    def _begin(
        self,
        connection: ManagedConnection,
        *,
        actor: UserRecord,
        operation_type: str,
        operation_id: str,
        request_hash: str,
        dedup_id: str,
        now_ms: int,
    ):
        try:
            return self._users.begin_operation(
                connection,
                actor_user_id=actor.id,
                operation_type=operation_type,
                operation_id=operation_id,
                request_sha256=request_hash,
                dedup_id=dedup_id,
                now_ms=now_ms,
            )
        except OperationIdempotencyConflictError as exc:
            raise LeaseIdempotencyError(str(exc)) from exc

    @staticmethod
    def _replay_error(value: Mapping[str, object]) -> LeaseServiceError:
        code = value.get("code")
        message = value.get("message")
        details = value.get("details")
        if not isinstance(message, str) or not isinstance(details, Mapping):
            raise LeaseIdempotencyError("The stored lease error is invalid.")
        types: dict[object, type[LeaseServiceError]] = {
            "LEASE_HELD": LeaseHeldError,
            "LEASE_LOST": LeaseLostError,
            "REVISION_CONFLICT": LeaseRevisionConflictError,
            "NOT_FOUND": LeaseNotFoundError,
            "STATE_CONFLICT": LeaseServiceError,
        }
        error_type = types.get(code)
        if error_type is None:
            raise LeaseIdempotencyError("The stored lease error code is invalid.")
        return error_type(message, details=details)

    @staticmethod
    def _validate_acquire_replay(
        result: LeaseAcquireResult, command: AcquireLeaseCommand
    ) -> LeaseAcquireResult:
        if (
            result.operation_id != command.operation_id
            or result.lease_id
            != lease_id_for(command.item_id, result.fencing_generation)
            or result.expires_at > result.absolute_expires_at
        ):
            raise LeaseIdempotencyError(
                "The stored lease acquisition is not bound to this command."
            )
        return replace(result, request_id=command.request_id)

    @staticmethod
    def _validate_mutation_replay(
        result: LeaseMutationResult,
        *,
        operation_id: str,
        request_id: str,
        item_id: str,
        status: Literal["released", "force_released"],
    ) -> LeaseMutationResult:
        if (
            result.operation_id != operation_id
            or result.resource_id != item_id
            or result.status != status
        ):
            raise LeaseIdempotencyError(
                "The stored lease mutation is not bound to this command."
            )
        return replace(result, request_id=request_id)

    def _fail_final(
        self,
        connection: ManagedConnection,
        *,
        dedup_id: str,
        error: LeaseServiceError,
        now_ms: int,
    ) -> None:
        try:
            self._users.fail_operation_final(
                connection,
                dedup_id=dedup_id,
                error=error.as_error(),
                http_status=error.http_status,
                completed_at_ms=now_ms,
            )
        except (OperationIdempotencyConflictError, OperationInProgressError) as exc:
            raise LeaseIdempotencyError(str(exc)) from exc

    def _commit_result(
        self,
        connection: ManagedConnection,
        *,
        dedup_id: str,
        result: Mapping[str, object],
        http_status: int,
        now_ms: int,
    ) -> None:
        try:
            self._users.commit_operation(
                connection,
                dedup_id=dedup_id,
                result=result,
                http_status=http_status,
                completed_at_ms=now_ms,
            )
        except (OperationIdempotencyConflictError, OperationInProgressError) as exc:
            raise LeaseIdempotencyError(str(exc)) from exc

    @staticmethod
    def _held_error(exc: LeaseHeldRepositoryError) -> LeaseHeldError:
        lease = exc.lease
        details: dict[str, object] = {
            "fencing_generation": lease.fencing_generation,
            "holder_user_id": lease.holder_user_id,
        }
        if lease.heartbeat_at_ms is not None:
            details["heartbeat_at"] = _iso_from_ms(lease.heartbeat_at_ms)
        if lease.expires_at_ms is not None:
            details["expires_at"] = _iso_from_ms(lease.expires_at_ms)
        return LeaseHeldError(str(exc), details=details)

    @staticmethod
    def _map_repository_error(exc: LeaseRepositoryErrorTypes) -> LeaseServiceError:
        if isinstance(exc, LeaseHeldRepositoryError):
            return FencedLeaseService._held_error(exc)
        if isinstance(exc, LeaseItemNotFoundError):
            return LeaseNotFoundError(str(exc))
        if isinstance(exc, LeaseLostRepositoryError):
            return LeaseLostError(
                str(exc), details={"draft_recovery": _draft_recovery()}
            )
        if isinstance(exc, LeaseRevisionConflictRepositoryError):
            return LeaseRevisionConflictError(
                str(exc),
                details={
                    "current_revision_id": exc.current_revision_id,
                    "current_row_version": exc.current_row_version,
                    "draft_recovery": _draft_recovery(
                        current_revision_id=exc.current_revision_id,
                        current_row_version=exc.current_row_version,
                    ),
                },
            )
        if isinstance(exc, LeaseStateConflictRepositoryError):
            return LeaseServiceError(str(exc))
        return LeaseServiceIntegrityError(str(exc))

    def _append_event(
        self,
        connection: ManagedConnection,
        *,
        actor: UserRecord,
        operation_id: str,
        request_id: str,
        dedup_id: str,
        action: str,
        outcome: str,
        space_id: str,
        item_id: str,
        reason: str | None,
        details: Mapping[str, object],
        request_meta: Mapping[str, object],
        generation: int,
        now_ms: int,
    ) -> None:
        event_uuid = _derived_uuid(_AUDIT_NAMESPACE, dedup_id, "audit")
        safe_details = dict(details)
        safe_details["schema_version"] = 1
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=event_uuid,
                occurred_at_ms=now_ms,
                request_id=request_id,
                operation_id=operation_id,
                action=action,
                outcome=outcome,
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=space_id,
                item_id=item_id,
                reason=reason,
                details=safe_details,
                request_meta=request_meta,
            ),
        )
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"knowledge-lease:{action}:{dedup_id}",
                space_id=space_id,
                aggregate_type="knowledge_item",
                aggregate_id=item_id,
                aggregate_version=generation,
                event_type=action,
                payload=safe_details,
                available_at_ms=now_ms,
            ),
        )

    def acquire(self, command: AcquireLeaseCommand) -> LeaseAcquireResult:
        self._validate_common(
            principal=command.principal,
            space_id=command.space_id,
            item_id=command.item_id,
            editor_session_id=command.editor_session_id,
            operation_id=command.operation_id,
            request_id=command.request_id,
        )
        raw_token = _decode_token(command.raw_token)
        safe_meta = _safe_request_meta(
            command.request_meta, secret_values=(command.raw_token,)
        )
        result: LeaseAcquireResult | None = None
        final_error: LeaseServiceError | None = None
        connection = self._factory.open()
        try:
            with transaction(connection):
                actor = self._resolve(
                    connection,
                    principal=command.principal,
                    space_id=command.space_id,
                    capability=Capability.EDIT,
                )
                now_ms = db_now_ms(connection)
                dedup_id, key_id, token_hmac, request_hash = (
                    self._operation_token_binding(
                        connection,
                        operation_type="knowledge_lease.acquire",
                        operation_id=command.operation_id,
                        principal=command.principal,
                        space_id=command.space_id,
                        item_id=command.item_id,
                        editor_session_id=command.editor_session_id,
                        lease_id=None,
                        fencing_generation=None,
                        raw_token=raw_token,
                    )
                )
                begun = self._begin(
                    connection,
                    actor=actor,
                    operation_type="knowledge_lease.acquire",
                    operation_id=command.operation_id,
                    request_hash=request_hash,
                    dedup_id=dedup_id,
                    now_ms=now_ms,
                )
                if not begun.inserted:
                    if begun.operation.status == "committed":
                        stored = begun.operation.result
                        if stored is None:
                            raise LeaseIdempotencyError(
                                "The committed lease acquisition has no result."
                            )
                        result = self._validate_acquire_replay(
                            LeaseAcquireResult.from_mapping(stored), command
                        )
                    elif begun.operation.status == "failed_final":
                        stored = begun.operation.error
                        if stored is None:
                            raise LeaseIdempotencyError(
                                "The failed lease acquisition has no error."
                            )
                        final_error = self._replay_error(stored)
                    else:
                        raise LeaseOperationInProgressError(
                            "The lease acquisition is already in progress."
                        )
                else:
                    try:
                        written = self._leases.acquire(
                            connection,
                            item_id=command.item_id,
                            space_id=command.space_id,
                            actor_user_id=actor.id,
                            editor_session_id=command.editor_session_id,
                            token_hmac=token_hmac,
                            hmac_key_id=key_id,
                            now_ms=now_ms,
                            stale_after_ms=self._stale_after_ms,
                            absolute_after_ms=self._absolute_after_ms,
                        )
                    except (
                        LeaseHeldRepositoryError,
                        LeaseItemNotFoundError,
                        LeaseStateConflictRepositoryError,
                    ) as exc:
                        final_error = self._map_repository_error(exc)
                        self._fail_final(
                            connection,
                            dedup_id=dedup_id,
                            error=final_error,
                            now_ms=now_ms,
                        )
                    except LeaseIntegrityError as exc:
                        raise LeaseServiceIntegrityError(str(exc)) from exc
                    else:
                        self._hit("lease.after_generation_increment")
                        lease = written.item.lease
                        assert lease.base_revision_id is not None
                        assert lease.expires_at_ms is not None
                        assert lease.absolute_expires_at_ms is not None
                        result = LeaseAcquireResult(
                            request_id=command.request_id,
                            operation_id=command.operation_id,
                            lease_id=lease_id_for(
                                command.item_id, lease.fencing_generation
                            ),
                            fencing_generation=lease.fencing_generation,
                            base_revision_id=lease.base_revision_id,
                            expires_at=_iso_from_ms(lease.expires_at_ms),
                            absolute_expires_at=_iso_from_ms(
                                lease.absolute_expires_at_ms
                            ),
                        )
                        self._hit("lease.after_holder_write")
                        self._append_event(
                            connection,
                            actor=actor,
                            operation_id=command.operation_id,
                            request_id=command.request_id,
                            dedup_id=dedup_id,
                            action="knowledge_lease.acquired",
                            outcome=written.outcome,
                            space_id=command.space_id,
                            item_id=command.item_id,
                            reason=None,
                            details={
                                "base_item_row_version": lease.base_item_row_version,
                                "base_revision_id": lease.base_revision_id,
                                "fencing_generation": lease.fencing_generation,
                                "lease_id": result.lease_id,
                                "outcome": written.outcome,
                            },
                            request_meta=safe_meta,
                            generation=lease.fencing_generation,
                            now_ms=now_ms,
                        )
                        self._commit_result(
                            connection,
                            dedup_id=dedup_id,
                            result=result.as_dict(),
                            http_status=201,
                            now_ms=now_ms,
                        )
                self._hit("lease.before_commit")
        finally:
            connection.close()
        if final_error is not None:
            raise final_error
        if result is None:
            raise LeaseServiceIntegrityError(
                "The lease acquisition produced no terminal result."
            )
        return result

    def heartbeat(self, command: HeartbeatLeaseCommand) -> HeartbeatLeaseResult:
        self._validate_common(
            principal=command.principal,
            space_id=command.space_id,
            item_id=command.item_id,
            editor_session_id=command.editor_session_id,
            request_id=command.request_id,
            lease_id=command.lease_id,
            fencing_generation=command.fencing_generation,
        )
        raw_token = _decode_token(command.raw_token)
        connection = self._factory.open()
        try:
            with transaction(connection):
                actor = self._resolve(
                    connection,
                    principal=command.principal,
                    space_id=command.space_id,
                    capability=Capability.EDIT,
                )
                now_ms = db_now_ms(connection)
                item = self._leases.require_item(
                    connection, item_id=command.item_id, space_id=command.space_id
                )
                if (
                    not item.lease.held
                    or command.lease_id
                    != lease_id_for(command.item_id, item.lease.fencing_generation)
                    or item.lease.hmac_key_id is None
                ):
                    raise LeaseLostError(
                        "The lease identity is no longer current.",
                        details={"draft_recovery": _draft_recovery()},
                    )
                token_hmac = self._keys.digest(
                    key_id=item.lease.hmac_key_id,
                    item_id=command.item_id,
                    raw_token=raw_token,
                )
                try:
                    current = self._leases.heartbeat(
                        connection,
                        item_id=command.item_id,
                        space_id=command.space_id,
                        actor_user_id=actor.id,
                        editor_session_id=command.editor_session_id,
                        token_hmac=token_hmac,
                        fencing_generation=command.fencing_generation,
                        now_ms=now_ms,
                        stale_after_ms=self._stale_after_ms,
                    )
                except (LeaseLostRepositoryError, LeaseItemNotFoundError) as exc:
                    raise self._map_repository_error(exc) from exc
                assert current.lease.expires_at_ms is not None
                return HeartbeatLeaseResult(
                    request_id=command.request_id,
                    lease_id=command.lease_id,
                    fencing_generation=command.fencing_generation,
                    expires_at=_iso_from_ms(current.lease.expires_at_ms),
                )
        finally:
            connection.close()

    def release(self, command: ReleaseLeaseCommand) -> LeaseMutationResult:
        self._validate_common(
            principal=command.principal,
            space_id=command.space_id,
            item_id=command.item_id,
            editor_session_id=command.editor_session_id,
            operation_id=command.operation_id,
            request_id=command.request_id,
            lease_id=command.lease_id,
            fencing_generation=command.fencing_generation,
        )
        raw_token = _decode_token(command.raw_token)
        safe_meta = _safe_request_meta(
            command.request_meta, secret_values=(command.raw_token,)
        )
        result: LeaseMutationResult | None = None
        final_error: LeaseServiceError | None = None
        connection = self._factory.open()
        try:
            with transaction(connection):
                actor = self._resolve(
                    connection,
                    principal=command.principal,
                    space_id=command.space_id,
                    capability=Capability.EDIT,
                )
                now_ms = db_now_ms(connection)
                dedup_id, _key_id, token_hmac, request_hash = (
                    self._operation_token_binding(
                        connection,
                        operation_type="knowledge_lease.release",
                        operation_id=command.operation_id,
                        principal=command.principal,
                        space_id=command.space_id,
                        item_id=command.item_id,
                        editor_session_id=command.editor_session_id,
                        lease_id=command.lease_id,
                        fencing_generation=command.fencing_generation,
                        raw_token=raw_token,
                    )
                )
                begun = self._begin(
                    connection,
                    actor=actor,
                    operation_type="knowledge_lease.release",
                    operation_id=command.operation_id,
                    request_hash=request_hash,
                    dedup_id=dedup_id,
                    now_ms=now_ms,
                )
                if not begun.inserted:
                    if begun.operation.status == "committed":
                        stored = begun.operation.result
                        if stored is None:
                            raise LeaseIdempotencyError(
                                "The committed lease release has no result."
                            )
                        result = self._validate_mutation_replay(
                            LeaseMutationResult.from_mapping(stored),
                            operation_id=command.operation_id,
                            request_id=command.request_id,
                            item_id=command.item_id,
                            status="released",
                        )
                    elif begun.operation.status == "failed_final":
                        stored = begun.operation.error
                        if stored is None:
                            raise LeaseIdempotencyError(
                                "The failed lease release has no error."
                            )
                        final_error = self._replay_error(stored)
                    else:
                        raise LeaseOperationInProgressError(
                            "The lease release is already in progress."
                        )
                else:
                    if command.lease_id != lease_id_for(
                        command.item_id, command.fencing_generation
                    ):
                        final_error = LeaseLostError(
                            "The lease identity does not match the fence.",
                            details={"draft_recovery": _draft_recovery()},
                        )
                    else:
                        try:
                            written = self._leases.release(
                                connection,
                                item_id=command.item_id,
                                space_id=command.space_id,
                                actor_user_id=actor.id,
                                editor_session_id=command.editor_session_id,
                                token_hmac=token_hmac,
                                fencing_generation=command.fencing_generation,
                                now_ms=now_ms,
                            )
                        except (
                            LeaseLostRepositoryError,
                            LeaseItemNotFoundError,
                            LeaseStateConflictRepositoryError,
                        ) as exc:
                            final_error = self._map_repository_error(exc)
                        except LeaseIntegrityError as exc:
                            raise LeaseServiceIntegrityError(str(exc)) from exc
                        else:
                            result = LeaseMutationResult(
                                request_id=command.request_id,
                                operation_id=command.operation_id,
                                status="released",
                                resource_id=command.item_id,
                                row_version=written.item.row_version,
                            )
                            self._append_event(
                                connection,
                                actor=actor,
                                operation_id=command.operation_id,
                                request_id=command.request_id,
                                dedup_id=dedup_id,
                                action="knowledge_lease.released",
                                outcome="released",
                                space_id=command.space_id,
                                item_id=command.item_id,
                                reason=None,
                                details={
                                    "fencing_generation": command.fencing_generation,
                                    "lease_id": command.lease_id,
                                },
                                request_meta=safe_meta,
                                generation=command.fencing_generation,
                                now_ms=now_ms,
                            )
                    if final_error is not None:
                        self._fail_final(
                            connection,
                            dedup_id=dedup_id,
                            error=final_error,
                            now_ms=now_ms,
                        )
                    else:
                        assert result is not None
                        self._commit_result(
                            connection,
                            dedup_id=dedup_id,
                            result=result.as_dict(),
                            http_status=200,
                            now_ms=now_ms,
                        )
                self._hit("lease.before_commit")
        finally:
            connection.close()
        if final_error is not None:
            raise final_error
        if result is None:
            raise LeaseServiceIntegrityError(
                "The lease release produced no terminal result."
            )
        return result

    def force_release(
        self, command: ForceReleaseLeaseCommand
    ) -> LeaseMutationResult:
        self._validate_common(
            principal=command.principal,
            space_id=command.space_id,
            item_id=command.item_id,
            operation_id=command.operation_id,
            request_id=command.request_id,
        )
        if not isinstance(command.reason, str) or not 1 <= len(command.reason.strip()) <= 2000:
            raise LeaseValidationError("A force-release reason is required.")
        if command.confirmation != "FORCE_RELEASE":
            raise LeaseValidationError("Force-release confirmation is invalid.")
        reason = command.reason.strip()
        safe_meta = _safe_request_meta(command.request_meta)
        request_hash = self._request_digest(
            operation_type="knowledge_lease.force_release",
            principal=command.principal,
            space_id=command.space_id,
            item_id=command.item_id,
            reason=reason,
            extra={"confirmation": command.confirmation},
        )
        dedup_id = _derived_uuid(
            _DEDUP_NAMESPACE,
            command.principal.user_id,
            "knowledge_lease.force_release",
            command.operation_id,
        )
        result: LeaseMutationResult | None = None
        final_error: LeaseServiceError | None = None
        connection = self._factory.open()
        try:
            with transaction(connection):
                authorization_mode = "ordinary"
                try:
                    actor = self._resolve(
                        connection,
                        principal=command.principal,
                        space_id=command.space_id,
                        capability=Capability.FORCE_RELEASE,
                    )
                except (LeaseAuthorizationError, LeaseNotFoundError):
                    try:
                        actor = self._users.require(
                            connection, command.principal.user_id
                        )
                    except UserNotFoundError as exc:
                        raise LeaseAuthenticationError(
                            "The authenticated user is unavailable."
                        ) from exc
                    if (
                        actor.account_status != "active"
                        or actor.scope_epoch
                        != command.principal.captured_scope_epoch
                    ):
                        raise LeaseScopeStaleError(
                            "The canonical actor scope changed."
                        )
                    if actor.system_role != "admin":
                        raise LeaseAuthorizationError(
                            "Force release requires native authority or an administrator."
                        )
                    space = connection.execute(
                        "SELECT 1 FROM knowledge_space WHERE id=?",
                        (command.space_id,),
                    ).fetchone()
                    if space is None:
                        raise LeaseNotFoundError(
                            "The requested knowledge space was not found."
                        )
                    authorization_mode = "break_glass"
                now_ms = db_now_ms(connection)
                begun = self._begin(
                    connection,
                    actor=actor,
                    operation_type="knowledge_lease.force_release",
                    operation_id=command.operation_id,
                    request_hash=request_hash,
                    dedup_id=dedup_id,
                    now_ms=now_ms,
                )
                if not begun.inserted:
                    if begun.operation.status == "committed":
                        stored = begun.operation.result
                        if stored is None:
                            raise LeaseIdempotencyError(
                                "The committed force release has no result."
                            )
                        result = self._validate_mutation_replay(
                            LeaseMutationResult.from_mapping(stored),
                            operation_id=command.operation_id,
                            request_id=command.request_id,
                            item_id=command.item_id,
                            status="force_released",
                        )
                    elif begun.operation.status == "failed_final":
                        stored = begun.operation.error
                        if stored is None:
                            raise LeaseIdempotencyError(
                                "The failed force release has no error."
                            )
                        final_error = self._replay_error(stored)
                    else:
                        raise LeaseOperationInProgressError(
                            "The force release is already in progress."
                        )
                else:
                    try:
                        written = self._leases.force_release(
                            connection,
                            item_id=command.item_id,
                            space_id=command.space_id,
                        )
                    except (
                        LeaseItemNotFoundError,
                        LeaseStateConflictRepositoryError,
                    ) as exc:
                        final_error = self._map_repository_error(exc)
                        self._fail_final(
                            connection,
                            dedup_id=dedup_id,
                            error=final_error,
                            now_ms=now_ms,
                        )
                    except LeaseIntegrityError as exc:
                        raise LeaseServiceIntegrityError(str(exc)) from exc
                    else:
                        self._hit("lease.force_release_after_generation")
                        new_generation = written.item.lease.fencing_generation
                        result = LeaseMutationResult(
                            request_id=command.request_id,
                            operation_id=command.operation_id,
                            status="force_released",
                            resource_id=command.item_id,
                            row_version=written.item.row_version,
                        )
                        self._append_event(
                            connection,
                            actor=actor,
                            operation_id=command.operation_id,
                            request_id=command.request_id,
                            dedup_id=dedup_id,
                            action="knowledge_lease.force_released",
                            outcome="force_released",
                            space_id=command.space_id,
                            item_id=command.item_id,
                            reason=reason,
                            details={
                                "authorization_mode": authorization_mode,
                                "new_fencing_generation": new_generation,
                                "previous_fencing_generation": written.released.fencing_generation,
                                "previous_holder_user_id": written.released.holder_user_id,
                            },
                            request_meta=safe_meta,
                            generation=new_generation,
                            now_ms=now_ms,
                        )
                        self._commit_result(
                            connection,
                            dedup_id=dedup_id,
                            result=result.as_dict(),
                            http_status=200,
                            now_ms=now_ms,
                        )
                self._hit("lease.before_commit")
        finally:
            connection.close()
        if final_error is not None:
            raise final_error
        if result is None:
            raise LeaseServiceIntegrityError(
                "The force release produced no terminal result."
            )
        return result

    def validate_edit_fence_in_transaction(
        self,
        connection: ManagedConnection,
        check: EditFenceCheck,
    ) -> LeaseValidationProof:
        """Validate a future P5.2 edit inside that edit's write transaction."""

        if not isinstance(connection, ManagedConnection) or not connection.in_transaction:
            raise KnowledgeCoreTransactionError(
                "Edit fence validation requires the caller's managed transaction."
            )
        self._validate_common(
            principal=check.principal,
            space_id=check.space_id,
            item_id=check.item_id,
            editor_session_id=check.editor_session_id,
            lease_id=check.lease_id,
            fencing_generation=check.fencing_generation,
        )
        _positive_int(
            check.expected_item_row_version,
            field_name="expected_item_row_version",
        )
        _uuid(check.expected_revision_id, field_name="expected_revision_id")
        raw_token = _decode_token(check.raw_token)
        actor = self._resolve(
            connection,
            principal=check.principal,
            space_id=check.space_id,
            capability=Capability.EDIT,
        )
        now_ms = db_now_ms(connection)
        try:
            item = self._leases.require_item(
                connection, item_id=check.item_id, space_id=check.space_id
            )
        except LeaseItemNotFoundError as exc:
            raise LeaseNotFoundError(str(exc)) from exc
        if (
            not item.lease.held
            or item.lease.hmac_key_id is None
            or check.lease_id
            != lease_id_for(check.item_id, item.lease.fencing_generation)
        ):
            raise LeaseLostError(
                "The edit lease is no longer current.",
                details={"draft_recovery": _draft_recovery()},
            )
        token_hmac = self._keys.digest(
            key_id=item.lease.hmac_key_id,
            item_id=check.item_id,
            raw_token=raw_token,
        )
        try:
            return self._leases.validate_holder(
                connection,
                item_id=check.item_id,
                space_id=check.space_id,
                actor_user_id=actor.id,
                editor_session_id=check.editor_session_id,
                token_hmac=token_hmac,
                fencing_generation=check.fencing_generation,
                now_ms=now_ms,
                expected_item_row_version=check.expected_item_row_version,
                expected_revision_id=check.expected_revision_id,
            )
        except (
            LeaseLostRepositoryError,
            LeaseRevisionConflictRepositoryError,
            LeaseItemNotFoundError,
            LeaseIntegrityError,
        ) as exc:
            raise self._map_repository_error(exc) from exc


class ImmutableItemEditService(FencedLeaseService):
    """P5.2 immutable-save boundary built on the persistent lease contract."""

    __slots__ = ("_edits", "_jobs")

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        key_ring: LeaseHmacKeyRing,
        scope_resolver: ScopeResolver,
        stale_after_ms: int = DEFAULT_STALE_AFTER_MS,
        absolute_after_ms: int = DEFAULT_ABSOLUTE_AFTER_MS,
        leases: LeaseRepository | None = None,
        users: UserRepository | None = None,
        audit: AuditRepository | None = None,
        outbox: OutboxRepository | None = None,
        edits: ItemEditRepository | None = None,
        jobs: JobRepository | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(
            factory,
            key_ring=key_ring,
            scope_resolver=scope_resolver,
            stale_after_ms=stale_after_ms,
            absolute_after_ms=absolute_after_ms,
            leases=leases,
            users=users,
            audit=audit,
            outbox=outbox,
            failpoint=failpoint,
        )
        self._edits = edits or ItemEditRepository()
        self._jobs = jobs or JobRepository()

    @staticmethod
    def _validate_edit_replay(
        result: ItemEditResult,
        command: SaveItemCommand,
        *,
        expected_revision_id: str,
    ) -> ItemEditResult:
        if (
            result.operation_id != command.operation_id
            or result.item_id != command.item_id
            or result.revision_id != expected_revision_id
            or result.row_version != command.expected_item_row_version + 1
        ):
            raise LeaseIdempotencyError(
                "The stored item-edit result is not bound to this command."
            )
        return replace(result, request_id=command.request_id)

    def _append_edit_event(
        self,
        connection: ManagedConnection,
        *,
        actor: UserRecord,
        command: SaveItemCommand,
        dedup_id: str,
        written: ItemEditWriteResult,
        request_meta: Mapping[str, object],
        now_ms: int,
    ) -> None:
        details: dict[str, object] = {
            "fencing_generation": command.fencing_generation,
            "lease_id": command.lease_id,
            "obsoleted_finding_count": written.obsoleted_finding_count,
            "obsoleted_proposal_count": written.obsoleted_proposal_count,
            "removed_search_document_count": written.removed_search_document_count,
            "rescan_job_id": written.rescan_job_id,
            "revalidation_edge_count": written.revalidation_edge_count,
            "revision_no": written.revision_no,
            "schema_version": 1,
            "space_generation": written.space_generation,
            "transition_id": written.transition_id,
        }
        self._audit.append(
            connection,
            AuditEventDraft(
                event_uuid=_derived_uuid(_AUDIT_NAMESPACE, dedup_id, "item-edit"),
                occurred_at_ms=now_ms,
                request_id=command.request_id,
                operation_id=command.operation_id,
                action="knowledge_item.edit",
                outcome="updated",
                actor_kind="user",
                actor_user_id=actor.id,
                actor_display_name=actor.display_name,
                actor_role_snapshot=actor.system_role,
                space_id=command.space_id,
                item_id=command.item_id,
                base_revision_id=written.base_revision_id,
                new_revision_id=written.revision_id,
                reason=command.reason.strip(),
                details=details,
                request_meta=request_meta,
            ),
        )
        self._hit("edit.after_audit")
        self._outbox.append(
            connection,
            OutboxEventDraft(
                logical_key=f"knowledge-item:edited:{dedup_id}",
                space_id=command.space_id,
                aggregate_type="knowledge_item",
                aggregate_id=command.item_id,
                aggregate_version=written.item_row_version,
                event_type="knowledge_item.edited",
                payload={
                    "base_revision_id": written.base_revision_id,
                    "item_id": command.item_id,
                    "new_revision_id": written.revision_id,
                    **details,
                },
                available_at_ms=now_ms,
            ),
        )
        self._hit("edit.after_outbox")

    def save(self, command: SaveItemCommand) -> ItemEditResult:
        if not isinstance(command, SaveItemCommand):
            raise TypeError("save requires a SaveItemCommand.")
        self._validate_common(
            principal=command.principal,
            space_id=command.space_id,
            item_id=command.item_id,
            editor_session_id=command.editor_session_id,
            operation_id=command.operation_id,
            request_id=command.request_id,
            lease_id=command.lease_id,
            fencing_generation=command.fencing_generation,
        )
        expected_row_version = _positive_int(
            command.expected_item_row_version,
            field_name="expected_item_row_version",
        )
        expected_revision_id = _uuid(
            command.expected_revision_id, field_name="expected_revision_id"
        )
        prepared = _prepare_markdown_edit(command.markdown, command.reason)
        raw_token = _decode_token(command.raw_token)
        safe_meta = _safe_request_meta(
            command.request_meta, secret_values=(command.raw_token,)
        )
        revision_id = _derived_uuid(
            _EDIT_REVISION_NAMESPACE,
            command.principal.user_id,
            command.operation_id,
        )
        transition_id = _derived_uuid(
            _EDIT_TRANSITION_NAMESPACE,
            command.principal.user_id,
            command.operation_id,
        )
        rescan_job_id = _derived_uuid(
            _EDIT_JOB_NAMESPACE,
            command.principal.user_id,
            command.operation_id,
        )
        job_dedup_id = _derived_uuid(
            _EDIT_JOB_DEDUP_NAMESPACE,
            command.principal.user_id,
            command.operation_id,
        )
        command_binding = {
            "content_size_bytes": prepared.size_bytes,
            "expected_item_row_version": expected_row_version,
            "expected_revision_id": expected_revision_id,
            "normalizer_version": "markdown_lf_v1",
            "normalized_content_sha256": prepared.normalized_sha256,
            "raw_content_sha256": prepared.raw_sha256,
            "reason": prepared.reason,
            "schema_version": 1,
        }
        result: ItemEditResult | None = None
        final_error: LeaseServiceError | None = None
        connection = self._factory.open()
        try:
            try:
                with transaction(connection):
                    actor, resolved = self._resolve_scope(
                        connection,
                        principal=command.principal,
                        space_id=command.space_id,
                        capability=Capability.EDIT,
                    )
                    now_ms = db_now_ms(connection)
                    dedup_id, _key_id, token_hmac, request_hash = (
                        self._operation_token_binding(
                            connection,
                            operation_type="knowledge_item.edit",
                            operation_id=command.operation_id,
                            principal=command.principal,
                            space_id=command.space_id,
                            item_id=command.item_id,
                            editor_session_id=command.editor_session_id,
                            lease_id=command.lease_id,
                            fencing_generation=command.fencing_generation,
                            raw_token=raw_token,
                            extra=command_binding,
                        )
                    )
                    begun = self._begin(
                        connection,
                        actor=actor,
                        operation_type="knowledge_item.edit",
                        operation_id=command.operation_id,
                        request_hash=request_hash,
                        dedup_id=dedup_id,
                        now_ms=now_ms,
                    )
                    if not begun.inserted:
                        if begun.operation.status == "committed":
                            stored = begun.operation.result
                            if stored is None:
                                raise LeaseIdempotencyError(
                                    "The committed item edit has no result."
                                )
                            result = self._validate_edit_replay(
                                ItemEditResult.from_mapping(stored),
                                command,
                                expected_revision_id=revision_id,
                            )
                        elif begun.operation.status == "failed_final":
                            stored = begun.operation.error
                            if stored is None:
                                raise LeaseIdempotencyError(
                                    "The failed item edit has no stored error."
                                )
                            final_error = self._replay_error(stored)
                        else:
                            raise LeaseOperationInProgressError(
                                "The item edit is already in progress."
                            )
                    else:
                        try:
                            proof = self.validate_edit_fence_in_transaction(
                                connection,
                                EditFenceCheck(
                                    principal=command.principal,
                                    space_id=command.space_id,
                                    item_id=command.item_id,
                                    editor_session_id=command.editor_session_id,
                                    lease_id=command.lease_id,
                                    fencing_generation=command.fencing_generation,
                                    expected_item_row_version=expected_row_version,
                                    expected_revision_id=expected_revision_id,
                                    raw_token=command.raw_token,
                                ),
                            )
                        except (
                            LeaseLostError,
                            LeaseRevisionConflictError,
                            LeaseNotFoundError,
                        ) as exc:
                            final_error = exc
                            self._fail_final(
                                connection,
                                dedup_id=dedup_id,
                                error=final_error,
                                now_ms=now_ms,
                            )
                        else:
                            if (
                                proof.base_revision_id != expected_revision_id
                                or proof.base_item_row_version != expected_row_version
                            ):
                                raise ItemEditServiceIntegrityError(
                                    "The lease proof is not bound to the edit base."
                                )
                            job_input = {
                                "item_id": command.item_id,
                                "revision_id": revision_id,
                                "rule_version": _EDIT_RULE_VERSION,
                            }
                            job_input_sha = canonical_job_input_sha256(job_input)
                            job_begun = self._users.begin_operation(
                                connection,
                                actor_user_id=actor.id,
                                operation_type="job:curation.rescan",
                                operation_id=command.operation_id,
                                request_sha256=job_input_sha,
                                dedup_id=job_dedup_id,
                                now_ms=now_ms,
                            )
                            if not job_begun.inserted:
                                raise LeaseIdempotencyError(
                                    "The rescan enqueue identity is already in use."
                                )
                            capabilities = tuple(
                                sorted(
                                    capability.value
                                    for capability in resolved.effective_capabilities
                                )
                            )
                            plan = resolved.access_plan
                            authorization_fence = (
                                None
                                if plan is None
                                else {
                                    "fence_mode": "mutation_authority",
                                    "corpus_closure_generation": plan.corpus_closure.generation,
                                    "corpus_closure_sha256": plan.corpus_closure.sha256,
                                    "organization_snapshot_version": str(plan.corpus_closure.organization_snapshot_version),
                                    "native_mutation_space_id": plan.native_mutation_space_id,
                                    "role_assignment_generation": plan.role_assignment_generation,
                                    "role_assignment_id": plan.role_assignment_id,
                                }
                            )
                            job = self._jobs.enqueue(
                                connection,
                                JobEnqueueDraft(
                                    job_id=rescan_job_id,
                                    dedup_id=job_dedup_id,
                                    operation_id=command.operation_id,
                                    actor_kind="user",
                                    actor_user_id=actor.id,
                                    space_id=command.space_id,
                                    job_type="curation.rescan",
                                    handler_version="v1",
                                    effect_key=(
                                        f"curation-rescan:{command.item_id}:"
                                        f"{revision_id}"
                                    ),
                                    input_ref=job_input,
                                    expected_state={
                                        "_authorization_fence": authorization_fence,
                                        "base_revision_id": expected_revision_id,
                                        "content_size_bytes": prepared.size_bytes,
                                        "expected_item_row_version": expected_row_version,
                                        "item_id": command.item_id,
                                        "normalizer_version": "markdown_lf_v1",
                                        "normalized_content_sha256": (
                                            prepared.normalized_sha256
                                        ),
                                        "raw_content_sha256": prepared.raw_sha256,
                                        "revision_id": revision_id,
                                        "schema_version": 1,
                                    },
                                    authorization_snapshot={
                                        "account_status": "active",
                                        "actor_kind": "user",
                                        "actor_user_id": actor.id,
                                        "capabilities": list(capabilities),
                                        "current_part_id": actor.current_part_id,
                                        "schema_version": 1,
                                        "scope_epoch": actor.scope_epoch,
                                        "space_id": command.space_id,
                                        "system_role": actor.system_role,
                                    },
                                    authorization_scope_epoch=actor.scope_epoch,
                                    authorization_part_id=actor.current_part_id,
                                    authorization_capabilities=capabilities,
                                    max_attempts=3,
                                    cancellable=False,
                                    available_at_ms=now_ms,
                                ),
                            ).job
                            self._hit("edit.after_rescan_enqueue")
                            written = self._edits.apply(
                                connection,
                                ItemEditDraft(
                                    item_id=command.item_id,
                                    space_id=command.space_id,
                                    actor_user_id=actor.id,
                                    operation_id=command.operation_id,
                                    dedup_id=dedup_id,
                                    revision_id=revision_id,
                                    transition_id=transition_id,
                                    rescan_job_id=job.id,
                                    rescan_job_dedup_id=job_dedup_id,
                                    expected_item_row_version=expected_row_version,
                                    expected_revision_id=expected_revision_id,
                                    content_md=prepared.markdown,
                                    raw_content_sha256=prepared.raw_sha256,
                                    normalized_content_sha256=(
                                        prepared.normalized_sha256
                                    ),
                                    content_size_bytes=prepared.size_bytes,
                                    normalizer_version="markdown_lf_v1",
                                    metadata={
                                        "fencing_generation": (
                                            command.fencing_generation
                                        ),
                                        "operation_id": command.operation_id,
                                        "schema_version": 1,
                                    },
                                    reason=prepared.reason,
                                    created_at_ms=now_ms,
                                ),
                                failpoint=self._hit,
                            )
                            self._hit("edit.after_item_effect")
                            try:
                                released = self._leases.release(
                                    connection,
                                    item_id=command.item_id,
                                    space_id=command.space_id,
                                    actor_user_id=actor.id,
                                    editor_session_id=command.editor_session_id,
                                    token_hmac=token_hmac,
                                    fencing_generation=command.fencing_generation,
                                    now_ms=now_ms,
                                )
                            except (
                                LeaseLostRepositoryError,
                                LeaseItemNotFoundError,
                                LeaseIntegrityError,
                            ) as exc:
                                raise ItemEditServiceIntegrityError(
                                    "The edit lease could not be released atomically."
                                ) from exc
                            if (
                                released.item.current_revision_id != revision_id
                                or released.item.row_version
                                != written.item_row_version
                            ):
                                raise ItemEditServiceIntegrityError(
                                    "The released lease does not cover the edited item."
                                )
                            self._hit("edit.after_lease_release")
                            self._append_edit_event(
                                connection,
                                actor=actor,
                                command=command,
                                dedup_id=dedup_id,
                                written=written,
                                request_meta=safe_meta,
                                now_ms=now_ms,
                            )
                            result = ItemEditResult(
                                request_id=command.request_id,
                                operation_id=command.operation_id,
                                item_id=command.item_id,
                                revision_id=revision_id,
                                row_version=written.item_row_version,
                            )
                            self._users.commit_operation(
                                connection,
                                dedup_id=job_dedup_id,
                                result={
                                    "job_id": job.id,
                                    "operation_id": command.operation_id,
                                    "revision_id": revision_id,
                                    "status": "queued",
                                },
                                http_status=202,
                                completed_at_ms=now_ms,
                            )
                            self._commit_result(
                                connection,
                                dedup_id=dedup_id,
                                result=result.as_dict(),
                                http_status=200,
                                now_ms=now_ms,
                            )
                            self._hit("edit.before_commit")
            except ItemEditNotFoundError as exc:
                raise LeaseNotFoundError(str(exc)) from exc
            except ItemEditConflictError as exc:
                raise LeaseRevisionConflictError(
                    str(exc),
                    details={
                        "current_revision_id": exc.current_revision_id,
                        "current_row_version": exc.current_row_version,
                        "draft_recovery": _draft_recovery(
                            current_revision_id=exc.current_revision_id,
                            current_row_version=exc.current_row_version,
                        ),
                    },
                ) from exc
            except (
                ItemEditIntegrityError,
                JobEffectConflictError,
                JobStateConflictError,
                JobRepositoryError,
            ) as exc:
                raise ItemEditServiceIntegrityError(str(exc)) from exc
        finally:
            connection.close()
        if final_error is not None:
            raise final_error
        if result is None:
            raise ItemEditServiceIntegrityError(
                "The immutable item edit produced no terminal result."
            )
        return result


LeaseRepositoryErrorTypes = (
    LeaseHeldRepositoryError
    | LeaseItemNotFoundError
    | LeaseLostRepositoryError
    | LeaseRevisionConflictRepositoryError
    | LeaseStateConflictRepositoryError
    | LeaseIntegrityError
)


__all__ = [
    "AcquireLeaseCommand",
    "DEFAULT_ABSOLUTE_AFTER_MS",
    "DEFAULT_HEARTBEAT_INTERVAL_MS",
    "DEFAULT_STALE_AFTER_MS",
    "EditFenceCheck",
    "FencedLeaseService",
    "ForceReleaseLeaseCommand",
    "HeartbeatLeaseCommand",
    "HeartbeatLeaseResult",
    "ImmutableItemEditService",
    "ItemEditResult",
    "ItemEditServiceIntegrityError",
    "LeaseAcquireResult",
    "LeaseAuthenticationError",
    "LeaseAuthorizationError",
    "LeaseFailpointTriggered",
    "LeaseHeldError",
    "LeaseHmacKeyRing",
    "LeaseIdempotencyError",
    "LeaseLostError",
    "LeaseMutationResult",
    "LeaseNotFoundError",
    "LeaseOperationInProgressError",
    "LeaseRevisionConflictError",
    "LeaseScopeStaleError",
    "LeaseServiceError",
    "LeaseServiceIntegrityError",
    "LeaseValidationError",
    "ReleaseLeaseCommand",
    "SaveItemCommand",
    "lease_id_for",
]
