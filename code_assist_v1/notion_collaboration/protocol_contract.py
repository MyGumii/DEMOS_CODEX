"""Disposable executable model for the T0A-P1 collaboration protocol.

This module is intentionally transport-, database-, and runtime-neutral.  It
starts no listener, reads no application configuration, and persists nothing.
It exists to make the ticket, envelope, sequence, acknowledgement, replay, and
epoch rules executable before T0A-P2 binds a transport and before any product
schema is allocated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
import secrets
import threading
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit
import uuid


PROTOCOL_CONTRACT = "code_assist.notion.t0a_p1.disposable_protocol.v1"
PROTOCOL_VERSION = 1
MAX_TICKET_TTL_MS = 30_000
MAX_PAYLOAD_BYTES = 1_048_576
MAX_REPLAY_BATCH = 256
ALLOWED_CAPABILITIES = frozenset({"comment", "presence", "read", "write"})
MESSAGE_CAPABILITIES = {
    "ack": "read",
    "comment.create": "comment",
    "crdt.update": "write",
    "presence.update": "presence",
    "replay.request": "read",
    "typed.command": "write",
}
DURABLE_MESSAGE_TYPES = frozenset({"comment.create", "crdt.update", "typed.command"})

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,159}$")
_OPERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,199}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")


class TicketRejected(PermissionError):
    """Stable non-oracle rejection for every ticket failure."""

    def __init__(self) -> None:
        super().__init__("collaboration ticket rejected")


class EnvelopeRejected(ValueError):
    """The versioned envelope is malformed or exceeds its grant."""


class StaleEpoch(PermissionError):
    """Admission or protected delivery used a non-current access epoch."""

    def __init__(self) -> None:
        super().__init__("stale collaboration authorization")


class OperationConflict(RuntimeError):
    """An operation ID was reused with different request bytes or binding."""


class SequenceGap(RuntimeError):
    """A client did not send exactly its next expected sequence."""

    def __init__(self, expected_sequence: int) -> None:
        self.expected_sequence = expected_sequence
        super().__init__(f"expected client sequence {expected_sequence}")


class AcknowledgementConflict(RuntimeError):
    """A client acknowledgement moved backwards or beyond durable state."""


class InjectedProtocolFailure(RuntimeError):
    """Test-only failure before or after the disposable durable boundary."""


def _identifier(value: str, *, field_name: str) -> str:
    item = str(value or "").strip()
    if not _ID_RE.fullmatch(item):
        raise ValueError(f"valid {field_name} required")
    return item


def _operation_id(value: str) -> str:
    item = str(value or "").strip()
    if not _OPERATION_RE.fullmatch(item):
        raise ValueError("valid operation_id required")
    return item


def _positive_int(value: int, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"valid {field_name} required")
    return value


def _nonnegative_int(value: int, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"valid {field_name} required")
    return value


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def session_binding_sha256(session_secret: str | bytes) -> str:
    """Return the only session material allowed in tickets and envelopes."""

    raw = session_secret.encode("utf-8") if isinstance(session_secret, str) else bytes(session_secret)
    if not raw:
        raise ValueError("non-empty session secret required")
    return _sha256_bytes(raw)


def _session_binding(value: str) -> str:
    item = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(item):
        raise ValueError("valid session_binding_sha256 required")
    return item


def _origin(value: str) -> str:
    item = str(value or "").strip()
    parsed = urlsplit(item)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("valid origin required")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("valid origin required") from exc
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = 443 if parsed.scheme == "https" else 80
    authority = host if port in {None, default_port} else f"{host}:{port}"
    return f"{parsed.scheme.lower()}://{authority}"


def _capabilities(values: Iterable[str]) -> tuple[str, ...]:
    try:
        items = tuple(sorted({str(value).strip().lower() for value in values}))
    except TypeError as exc:
        raise ValueError("valid capabilities required") from exc
    if not items or any(not item or item not in ALLOWED_CAPABILITIES for item in items):
        raise ValueError("valid capabilities required")
    return items


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class TicketClaims:
    protocol_version: int
    jti: str
    subject_id: str
    workspace_id: str
    page_id: str
    client_id: str
    session_binding_sha256: str
    origin: str
    capabilities: tuple[str, ...]
    workspace_epoch: int
    page_epoch: int
    issued_at_ms: int
    expires_at_ms: int


@dataclass(frozen=True)
class IssuedTicket:
    token: str = field(repr=False)
    claims: TicketClaims


@dataclass(frozen=True)
class TicketGrant:
    claims: TicketClaims
    consumed_at_ms: int
    temporary_unlock: Mapping[str, Any] | None = field(default=None, repr=False)

    def allows(self, capability: str) -> bool:
        return str(capability).strip().lower() in self.claims.capabilities


@dataclass
class _TicketRecord:
    claims: TicketClaims
    consumed_at_ms: int | None = None


class TicketAuthority:
    """Thread-safe one-time ticket model that stores no plaintext ticket."""

    def __init__(self) -> None:
        self._records: dict[str, _TicketRecord] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        *,
        csrf_valid: bool,
        subject_id: str,
        workspace_id: str,
        page_id: str,
        client_id: str,
        session_binding_sha256: str,
        origin: str,
        capabilities: Iterable[str],
        workspace_epoch: int,
        page_epoch: int,
        now_ms: int,
        ttl_ms: int = MAX_TICKET_TTL_MS,
    ) -> IssuedTicket:
        if csrf_valid is not True:
            raise TicketRejected()
        issued_at = _nonnegative_int(now_ms, field_name="now_ms")
        lifetime = _positive_int(ttl_ms, field_name="ttl_ms")
        if lifetime > MAX_TICKET_TTL_MS:
            raise ValueError("ticket ttl exceeds protocol maximum")
        claims = TicketClaims(
            protocol_version=PROTOCOL_VERSION,
            jti="jti_" + uuid.uuid4().hex,
            subject_id=_identifier(subject_id, field_name="subject_id"),
            workspace_id=_identifier(workspace_id, field_name="workspace_id"),
            page_id=_identifier(page_id, field_name="page_id"),
            client_id=_identifier(client_id, field_name="client_id"),
            session_binding_sha256=_session_binding(session_binding_sha256),
            origin=_origin(origin),
            capabilities=_capabilities(capabilities),
            workspace_epoch=_positive_int(workspace_epoch, field_name="workspace_epoch"),
            page_epoch=_positive_int(page_epoch, field_name="page_epoch"),
            issued_at_ms=issued_at,
            expires_at_ms=issued_at + lifetime,
        )
        token = secrets.token_urlsafe(32)
        digest = _sha256_bytes(token.encode("ascii"))
        with self._lock:
            if digest in self._records:  # Cryptographically implausible; fail closed.
                raise TicketRejected()
            self._records[digest] = _TicketRecord(claims=claims)
        return IssuedTicket(token=token, claims=claims)

    def consume(
        self,
        token: str,
        *,
        origin: str,
        subject_id: str,
        workspace_id: str,
        page_id: str,
        client_id: str,
        session_binding_sha256: str,
        current_workspace_epoch: int,
        current_page_epoch: int,
        now_ms: int,
    ) -> TicketGrant:
        try:
            candidate = str(token or "")
            if not _TOKEN_RE.fullmatch(candidate):
                raise ValueError
            digest = _sha256_bytes(candidate.encode("ascii"))
            presented = {
                "origin": _origin(origin),
                "subject_id": _identifier(subject_id, field_name="subject_id"),
                "workspace_id": _identifier(workspace_id, field_name="workspace_id"),
                "page_id": _identifier(page_id, field_name="page_id"),
                "client_id": _identifier(client_id, field_name="client_id"),
                "session_binding_sha256": _session_binding(session_binding_sha256),
                "workspace_epoch": _positive_int(
                    current_workspace_epoch, field_name="current_workspace_epoch"
                ),
                "page_epoch": _positive_int(current_page_epoch, field_name="current_page_epoch"),
                "now_ms": _nonnegative_int(now_ms, field_name="now_ms"),
            }
        except (TypeError, UnicodeError, ValueError):
            raise TicketRejected() from None

        with self._lock:
            record = self._records.get(digest)
            if record is None or record.consumed_at_ms is not None:
                raise TicketRejected()
            # A recognized ticket is burned before validation so wrong-origin or
            # stale-epoch probes cannot retry or distinguish stored state.
            record.consumed_at_ms = presented["now_ms"]
            claims = record.claims
            valid = (
                claims.protocol_version == PROTOCOL_VERSION
                and claims.issued_at_ms <= presented["now_ms"] < claims.expires_at_ms
                and claims.origin == presented["origin"]
                and claims.subject_id == presented["subject_id"]
                and claims.workspace_id == presented["workspace_id"]
                and claims.page_id == presented["page_id"]
                and claims.client_id == presented["client_id"]
                and claims.session_binding_sha256 == presented["session_binding_sha256"]
                and claims.workspace_epoch == presented["workspace_epoch"]
                and claims.page_epoch == presented["page_epoch"]
            )
            if not valid:
                raise TicketRejected()
            return TicketGrant(claims=claims, consumed_at_ms=presented["now_ms"])

    def debug_digest_snapshot(self) -> tuple[dict[str, Any], ...]:
        """Return test evidence that the store contains digests, never tokens."""

        with self._lock:
            return tuple(
                {
                    "ticket_digest": digest,
                    "jti": record.claims.jti,
                    "consumed_at_ms": record.consumed_at_ms,
                }
                for digest, record in sorted(self._records.items())
            )


@dataclass(frozen=True)
class ProtocolEnvelope:
    protocol_version: int
    message_type: str
    subject_id: str
    workspace_id: str
    page_id: str
    client_id: str
    session_binding_sha256: str
    capabilities: tuple[str, ...]
    workspace_epoch: int
    page_epoch: int
    operation_id: str
    client_sequence: int
    acknowledgement_sequence: int
    payload_encoding: str
    payload_length: int
    payload_sha256: str
    payload: bytes = field(repr=False)

    @classmethod
    def create(
        cls,
        grant: TicketGrant,
        *,
        message_type: str,
        operation_id: str,
        client_sequence: int,
        acknowledgement_sequence: int,
        payload: bytes,
        capabilities: Iterable[str],
        payload_encoding: str = "application/octet-stream",
    ) -> "ProtocolEnvelope":
        raw = bytes(payload)
        message = str(message_type or "").strip().lower()
        caps = _capabilities(capabilities)
        if message not in MESSAGE_CAPABILITIES:
            raise EnvelopeRejected("unknown message type")
        if MESSAGE_CAPABILITIES[message] not in caps:
            raise EnvelopeRejected("required capability missing")
        if not set(caps) <= set(grant.claims.capabilities):
            raise EnvelopeRejected("capability exceeds ticket grant")
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise EnvelopeRejected("payload exceeds protocol maximum")
        encoding = str(payload_encoding or "").strip().lower()
        if not encoding or len(encoding) > 128:
            raise EnvelopeRejected("valid payload encoding required")
        claims = grant.claims
        return cls(
            protocol_version=PROTOCOL_VERSION,
            message_type=message,
            subject_id=claims.subject_id,
            workspace_id=claims.workspace_id,
            page_id=claims.page_id,
            client_id=claims.client_id,
            session_binding_sha256=claims.session_binding_sha256,
            capabilities=caps,
            workspace_epoch=claims.workspace_epoch,
            page_epoch=claims.page_epoch,
            operation_id=_operation_id(operation_id),
            client_sequence=_positive_int(client_sequence, field_name="client_sequence"),
            acknowledgement_sequence=_nonnegative_int(
                acknowledgement_sequence, field_name="acknowledgement_sequence"
            ),
            payload_encoding=encoding,
            payload_length=len(raw),
            payload_sha256=_sha256_bytes(raw),
            payload=raw,
        )

    def validate(
        self,
        grant: TicketGrant,
        *,
        current_workspace_epoch: int,
        current_page_epoch: int,
    ) -> None:
        claims = grant.claims
        try:
            caps = _capabilities(self.capabilities)
            current_workspace = _positive_int(
                current_workspace_epoch, field_name="current_workspace_epoch"
            )
            current_page = _positive_int(current_page_epoch, field_name="current_page_epoch")
        except ValueError as exc:
            raise EnvelopeRejected("invalid envelope metadata") from exc
        if (
            self.protocol_version != PROTOCOL_VERSION
            or self.message_type not in MESSAGE_CAPABILITIES
            or self.subject_id != claims.subject_id
            or self.workspace_id != claims.workspace_id
            or self.page_id != claims.page_id
            or self.client_id != claims.client_id
            or self.session_binding_sha256 != claims.session_binding_sha256
            or caps != self.capabilities
            or not set(caps) <= set(claims.capabilities)
            or MESSAGE_CAPABILITIES[self.message_type] not in caps
            or self.workspace_epoch != claims.workspace_epoch
            or self.page_epoch != claims.page_epoch
        ):
            raise EnvelopeRejected("envelope grant binding mismatch")
        if self.workspace_epoch != current_workspace or self.page_epoch != current_page:
            raise StaleEpoch()
        if (
            self.client_sequence < 1
            or self.acknowledgement_sequence < 0
            or self.payload_length < 0
            or self.payload_length > MAX_PAYLOAD_BYTES
            or self.payload_length != len(self.payload)
            or self.payload_sha256 != _sha256_bytes(self.payload)
        ):
            raise EnvelopeRejected("envelope payload integrity mismatch")
        _operation_id(self.operation_id)

    def request_sha256(self) -> str:
        descriptor = {
            "protocol_version": self.protocol_version,
            "message_type": self.message_type,
            "subject_id": self.subject_id,
            "workspace_id": self.workspace_id,
            "page_id": self.page_id,
            "client_id": self.client_id,
            "session_binding_sha256": self.session_binding_sha256,
            "capabilities": list(self.capabilities),
            "workspace_epoch": self.workspace_epoch,
            "page_epoch": self.page_epoch,
            "operation_id": self.operation_id,
            "client_sequence": self.client_sequence,
            "acknowledgement_sequence": self.acknowledgement_sequence,
            "payload_encoding": self.payload_encoding,
            "payload_length": self.payload_length,
            "payload_sha256": self.payload_sha256,
        }
        return _sha256_bytes(_canonical_json(descriptor))


@dataclass(frozen=True)
class Acknowledgement:
    operation_id: str
    client_sequence: int
    document_sequence: int
    request_sha256: str
    event_sha256: str
    workspace_epoch: int
    page_epoch: int
    durable: bool = True


@dataclass(frozen=True)
class DurableProtocolEvent:
    workspace_id: str
    page_id: str
    document_sequence: int
    subject_id: str
    client_id: str
    operation_id: str
    client_sequence: int
    message_type: str
    payload_encoding: str
    payload_sha256: str
    payload: bytes = field(repr=False)
    workspace_epoch: int = 1
    page_epoch: int = 1


@dataclass(frozen=True)
class ReplayBatch:
    after_document_sequence: int
    high_water_sequence: int
    events: tuple[DurableProtocolEvent, ...]
    has_more: bool


class InMemorySequenceLedger:
    """Atomic in-memory model of durable ordering and idempotent ACK replay."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._high_water: dict[tuple[str, str], int] = {}
        self._events: dict[tuple[str, str], list[DurableProtocolEvent]] = {}
        self._next_client_sequence: dict[tuple[str, str, str, str], int] = {}
        self._last_client_ack: dict[tuple[str, str, str, str], int] = {}
        self._operations: dict[
            tuple[str, str, str], tuple[str, tuple[str, str, str, str], Acknowledgement]
        ] = {}

    @staticmethod
    def _client_key(envelope: ProtocolEnvelope) -> tuple[str, str, str, str]:
        return (
            envelope.subject_id,
            envelope.page_id,
            envelope.client_id,
            envelope.session_binding_sha256,
        )

    @staticmethod
    def _operation_key(envelope: ProtocolEnvelope) -> tuple[str, str, str]:
        return envelope.subject_id, envelope.page_id, envelope.operation_id

    @staticmethod
    def _page_key_from_claims(claims: TicketClaims) -> tuple[str, str]:
        return claims.workspace_id, claims.page_id

    @staticmethod
    def _validate_grant(
        grant: TicketGrant,
        *,
        current_workspace_epoch: int,
        current_page_epoch: int,
        required_capability: str,
    ) -> None:
        if (
            grant.claims.workspace_epoch
            != _positive_int(current_workspace_epoch, field_name="current_workspace_epoch")
            or grant.claims.page_epoch
            != _positive_int(current_page_epoch, field_name="current_page_epoch")
        ):
            raise StaleEpoch()
        if not grant.allows(required_capability):
            raise EnvelopeRejected("capability exceeds ticket grant")

    def accept(
        self,
        envelope: ProtocolEnvelope,
        grant: TicketGrant,
        *,
        current_workspace_epoch: int,
        current_page_epoch: int,
        fault: str | None = None,
    ) -> Acknowledgement:
        if fault not in {None, "before_durable_commit", "after_durable_commit_before_ack"}:
            raise ValueError("unknown protocol fault point")
        envelope.validate(
            grant,
            current_workspace_epoch=current_workspace_epoch,
            current_page_epoch=current_page_epoch,
        )
        if envelope.message_type not in DURABLE_MESSAGE_TYPES:
            raise EnvelopeRejected("message type is not a durable mutation")
        request_sha256 = envelope.request_sha256()
        operation_key = self._operation_key(envelope)
        client_key = self._client_key(envelope)
        page_key = (envelope.workspace_id, envelope.page_id)

        with self._lock:
            prior = self._operations.get(operation_key)
            if prior is not None:
                prior_hash, prior_client_key, prior_ack = prior
                if prior_hash != request_sha256 or prior_client_key != client_key:
                    raise OperationConflict("operation_id reused with a different request")
                return prior_ack

            expected = self._next_client_sequence.get(client_key, 1)
            if envelope.client_sequence != expected:
                raise SequenceGap(expected)
            high_water = self._high_water.get(page_key, 0)
            previous_ack = self._last_client_ack.get(client_key, 0)
            if (
                envelope.acknowledgement_sequence < previous_ack
                or envelope.acknowledgement_sequence > high_water
            ):
                raise AcknowledgementConflict("acknowledgement is outside durable bounds")

            document_sequence = high_water + 1
            event = DurableProtocolEvent(
                workspace_id=envelope.workspace_id,
                page_id=envelope.page_id,
                document_sequence=document_sequence,
                subject_id=envelope.subject_id,
                client_id=envelope.client_id,
                operation_id=envelope.operation_id,
                client_sequence=envelope.client_sequence,
                message_type=envelope.message_type,
                payload_encoding=envelope.payload_encoding,
                payload_sha256=envelope.payload_sha256,
                payload=envelope.payload,
                workspace_epoch=envelope.workspace_epoch,
                page_epoch=envelope.page_epoch,
            )
            event_descriptor = {
                "workspace_id": event.workspace_id,
                "page_id": event.page_id,
                "document_sequence": event.document_sequence,
                "subject_id": event.subject_id,
                "client_id": event.client_id,
                "operation_id": event.operation_id,
                "client_sequence": event.client_sequence,
                "message_type": event.message_type,
                "payload_encoding": event.payload_encoding,
                "payload_sha256": event.payload_sha256,
                "workspace_epoch": event.workspace_epoch,
                "page_epoch": event.page_epoch,
            }
            ack = Acknowledgement(
                operation_id=envelope.operation_id,
                client_sequence=envelope.client_sequence,
                document_sequence=document_sequence,
                request_sha256=request_sha256,
                event_sha256=_sha256_bytes(_canonical_json(event_descriptor)),
                workspace_epoch=envelope.workspace_epoch,
                page_epoch=envelope.page_epoch,
            )
            if fault == "before_durable_commit":
                raise InjectedProtocolFailure("before_durable_commit")

            # This assignment group is the disposable durable boundary.  No
            # acknowledgement can escape before every index is updated.
            self._events.setdefault(page_key, []).append(event)
            self._high_water[page_key] = document_sequence
            self._next_client_sequence[client_key] = expected + 1
            self._last_client_ack[client_key] = envelope.acknowledgement_sequence
            self._operations[operation_key] = (request_sha256, client_key, ack)
            if fault == "after_durable_commit_before_ack":
                raise InjectedProtocolFailure("after_durable_commit_before_ack")
            return ack

    def replay(
        self,
        grant: TicketGrant,
        *,
        after_document_sequence: int,
        limit: int,
        current_workspace_epoch: int,
        current_page_epoch: int,
    ) -> ReplayBatch:
        self._validate_grant(
            grant,
            current_workspace_epoch=current_workspace_epoch,
            current_page_epoch=current_page_epoch,
            required_capability="read",
        )
        after = _nonnegative_int(
            after_document_sequence, field_name="after_document_sequence"
        )
        batch_limit = _positive_int(limit, field_name="limit")
        if batch_limit > MAX_REPLAY_BATCH:
            raise ValueError("replay batch exceeds protocol maximum")
        page_key = self._page_key_from_claims(grant.claims)
        with self._lock:
            high_water = self._high_water.get(page_key, 0)
            if after > high_water:
                raise AcknowledgementConflict("replay cursor exceeds durable high-water mark")
            candidates = [
                event
                for event in self._events.get(page_key, [])
                if event.document_sequence > after
            ]
            selected = tuple(candidates[:batch_limit])
            for event in selected:
                self.authorize_send(
                    event,
                    grant,
                    current_workspace_epoch=current_workspace_epoch,
                    current_page_epoch=current_page_epoch,
                )
            return ReplayBatch(
                after_document_sequence=after,
                high_water_sequence=high_water,
                events=selected,
                has_more=len(candidates) > len(selected),
            )

    @staticmethod
    def authorize_send(
        event: DurableProtocolEvent,
        grant: TicketGrant,
        *,
        current_workspace_epoch: int,
        current_page_epoch: int,
    ) -> None:
        InMemorySequenceLedger._validate_grant(
            grant,
            current_workspace_epoch=current_workspace_epoch,
            current_page_epoch=current_page_epoch,
            required_capability="read",
        )
        claims = grant.claims
        if (
            event.workspace_id != claims.workspace_id
            or event.page_id != claims.page_id
            or event.workspace_epoch != current_workspace_epoch
            or event.page_epoch != current_page_epoch
        ):
            raise StaleEpoch()

    def high_water(self, workspace_id: str, page_id: str) -> int:
        key = (
            _identifier(workspace_id, field_name="workspace_id"),
            _identifier(page_id, field_name="page_id"),
        )
        with self._lock:
            return self._high_water.get(key, 0)

    def event_count(self, workspace_id: str, page_id: str) -> int:
        key = (
            _identifier(workspace_id, field_name="workspace_id"),
            _identifier(page_id, field_name="page_id"),
        )
        with self._lock:
            return len(self._events.get(key, []))

    def next_client_sequence(self, grant: TicketGrant) -> int:
        claims = grant.claims
        key = (
            claims.subject_id,
            claims.page_id,
            claims.client_id,
            claims.session_binding_sha256,
        )
        with self._lock:
            return self._next_client_sequence.get(key, 1)
