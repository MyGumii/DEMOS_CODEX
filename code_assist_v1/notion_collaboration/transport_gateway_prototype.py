"""Disposable T0A-P2 transport-only gateway state machine.

The model starts no listener and imports no WebSocket/ASGI runtime.  It owns
only connection lifecycle, bounded outbound queues, reconnect throttling, and
send-time fencing.  Ticket claims, durable mutation ordering, and replay remain
owned by the T0A-P1 executable contract and its injected authority resolver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Callable
import uuid

from code_assist_v1.notion_collaboration.protocol_contract import (
    Acknowledgement,
    DurableProtocolEvent,
    EnvelopeRejected,
    InMemorySequenceLedger,
    ProtocolEnvelope,
    ReplayBatch,
    StaleEpoch,
    TicketAuthority,
    TicketGrant,
    TicketRejected,
)


GATEWAY_CONTRACT = "code_assist.notion.t0a_p2.transport_gateway_prototype.v1"
DEFAULT_MAX_QUEUE_FRAMES = 64
DEFAULT_MAX_QUEUE_BYTES = 4_194_304
DEFAULT_MAX_CONNECTIONS = 1_024
DEFAULT_RECONNECT_WINDOW_MS = 10_000
DEFAULT_RECONNECT_ATTEMPTS = 5
ALLOWED_CLOSE_REASONS = frozenset(
    {
        "authority_unavailable",
        "backpressure_exceeded",
        "client_disconnect",
        "gateway_stopped",
        "protocol_violation",
        "stale_epoch",
        "superseded_by_reconnect",
    }
)


class GatewayRejected(PermissionError):
    """Stable non-oracle admission or session rejection."""

    def __init__(self) -> None:
        super().__init__("collaboration gateway rejected")


class GatewayUnavailable(RuntimeError):
    """The gateway or authoritative epoch resolver is unavailable."""

    def __init__(self) -> None:
        super().__init__("collaboration gateway unavailable")


class ReconnectThrottled(RuntimeError):
    """A reconnect key exceeded its bounded attempt window."""

    def __init__(self) -> None:
        super().__init__("collaboration reconnect throttled")


class ConnectionCapacityExceeded(RuntimeError):
    """The bounded connection table has no available slot."""

    def __init__(self) -> None:
        super().__init__("collaboration connection capacity exceeded")


class SessionClosed(RuntimeError):
    """A transport operation targeted a closed or unknown session."""

    def __init__(self) -> None:
        super().__init__("collaboration session closed")


@dataclass(frozen=True)
class EpochResolution:
    workspace_epoch: int
    page_epoch: int


EpochResolver = Callable[[str, str, str], EpochResolution | None]


@dataclass(frozen=True)
class GatewaySessionDescriptor:
    connection_id: str
    protocol_version: int
    workspace_epoch: int
    page_epoch: int
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class BroadcastResult:
    considered_sessions: int
    enqueued_sessions: int
    duplicate_sessions: int
    fenced_sessions: int
    backpressured_sessions: int


@dataclass(frozen=True)
class SubmitReceipt:
    acknowledgement: Acknowledgement
    broadcast: BroadcastResult


@dataclass(frozen=True)
class ReplayQueueReceipt:
    batch: ReplayBatch
    enqueued_frames: int
    duplicate_frames: int


@dataclass
class _Session:
    connection_id: str
    grant: TicketGrant
    active: bool = True
    close_reason: str | None = None
    queue: list[DurableProtocolEvent] = field(default_factory=list)
    queued_payload_bytes: int = 0

    @property
    def binding_key(self) -> tuple[str, str, str, str]:
        claims = self.grant.claims
        return (
            claims.subject_id,
            claims.page_id,
            claims.client_id,
            claims.session_binding_sha256,
        )


def _positive(value: int, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"valid {field_name} required")
    return value


def _nonnegative(value: int, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"valid {field_name} required")
    return value


class TransportOnlyGateway:
    """Thread-safe bounded transport model with no canonical domain writes."""

    def __init__(
        self,
        *,
        ticket_authority: TicketAuthority,
        sequence_ledger: InMemorySequenceLedger,
        epoch_resolver: EpochResolver,
        max_queue_frames: int = DEFAULT_MAX_QUEUE_FRAMES,
        max_queue_bytes: int = DEFAULT_MAX_QUEUE_BYTES,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        reconnect_window_ms: int = DEFAULT_RECONNECT_WINDOW_MS,
        reconnect_attempts: int = DEFAULT_RECONNECT_ATTEMPTS,
    ) -> None:
        self.ticket_authority = ticket_authority
        self.sequence_ledger = sequence_ledger
        self.epoch_resolver = epoch_resolver
        self.max_queue_frames = _positive(max_queue_frames, field_name="max_queue_frames")
        self.max_queue_bytes = _positive(max_queue_bytes, field_name="max_queue_bytes")
        self.max_connections = _positive(max_connections, field_name="max_connections")
        self.reconnect_window_ms = _positive(
            reconnect_window_ms, field_name="reconnect_window_ms"
        )
        self.reconnect_attempts = _positive(
            reconnect_attempts, field_name="reconnect_attempts"
        )
        self._lock = threading.Lock()
        self._sessions: dict[str, _Session] = {}
        self._active_bindings: dict[tuple[str, str, str, str], str] = {}
        self._attempt_windows: dict[tuple[str, str], tuple[int, int]] = {}
        self._broadcasted_events: dict[tuple[str, str, int], str] = {}
        self._running = True

    def _require_running(self) -> None:
        with self._lock:
            if not self._running:
                raise GatewayUnavailable()

    def _record_attempt(self, subject_id: str, client_id: str, now_ms: int) -> None:
        now = _nonnegative(now_ms, field_name="now_ms")
        key = (str(subject_id or ""), str(client_id or ""))
        if not all(key):
            raise GatewayRejected()
        window = now // self.reconnect_window_ms
        with self._lock:
            prior_window, prior_count = self._attempt_windows.get(key, (window, 0))
            count = prior_count + 1 if prior_window == window else 1
            self._attempt_windows[key] = (window, count)
            if count > self.reconnect_attempts:
                raise ReconnectThrottled()

    def _resolve(
        self, subject_id: str, workspace_id: str, page_id: str
    ) -> EpochResolution:
        try:
            value = self.epoch_resolver(subject_id, workspace_id, page_id)
        except Exception:
            raise GatewayUnavailable() from None
        if value is None:
            raise GatewayRejected()
        try:
            workspace_epoch = _positive(
                value.workspace_epoch, field_name="workspace_epoch"
            )
            page_epoch = _positive(value.page_epoch, field_name="page_epoch")
        except (AttributeError, TypeError, ValueError):
            raise GatewayUnavailable() from None
        return EpochResolution(workspace_epoch, page_epoch)

    def _active_count_locked(self) -> int:
        return sum(session.active for session in self._sessions.values())

    def _fence_locked(self, session: _Session, reason: str) -> None:
        if reason not in ALLOWED_CLOSE_REASONS:
            raise ValueError("unknown close reason")
        session.active = False
        session.close_reason = reason
        session.queue.clear()
        session.queued_payload_bytes = 0
        if self._active_bindings.get(session.binding_key) == session.connection_id:
            self._active_bindings.pop(session.binding_key, None)

    def _fence(self, connection_id: str, reason: str) -> None:
        with self._lock:
            session = self._sessions.get(connection_id)
            if session is not None and session.active:
                self._fence_locked(session, reason)

    def connect(
        self,
        token: str,
        *,
        origin: str,
        subject_id: str,
        workspace_id: str,
        page_id: str,
        client_id: str,
        session_binding_sha256: str,
        now_ms: int,
    ) -> GatewaySessionDescriptor:
        self._require_running()
        self._record_attempt(subject_id, client_id, now_ms)
        resolution = self._resolve(subject_id, workspace_id, page_id)

        # Capacity is checked before consuming a ticket.  A same-binding
        # reconnect replaces one active session and therefore needs no new slot.
        provisional_key = (
            str(subject_id),
            str(page_id),
            str(client_id),
            str(session_binding_sha256),
        )
        with self._lock:
            existing_id = self._active_bindings.get(provisional_key)
            if existing_id is None and self._active_count_locked() >= self.max_connections:
                raise ConnectionCapacityExceeded()

        try:
            grant = self.ticket_authority.consume(
                token,
                origin=origin,
                subject_id=subject_id,
                workspace_id=workspace_id,
                page_id=page_id,
                client_id=client_id,
                session_binding_sha256=session_binding_sha256,
                current_workspace_epoch=resolution.workspace_epoch,
                current_page_epoch=resolution.page_epoch,
                now_ms=now_ms,
            )
        except TicketRejected:
            raise GatewayRejected() from None

        session = _Session(connection_id="conn_" + uuid.uuid4().hex, grant=grant)
        with self._lock:
            if not self._running:
                raise GatewayUnavailable()
            existing_id = self._active_bindings.get(session.binding_key)
            if existing_id is None and self._active_count_locked() >= self.max_connections:
                raise ConnectionCapacityExceeded()
            if existing_id is not None:
                existing = self._sessions.get(existing_id)
                if existing is not None and existing.active:
                    self._fence_locked(existing, "superseded_by_reconnect")
            self._sessions[session.connection_id] = session
            self._active_bindings[session.binding_key] = session.connection_id
        claims = grant.claims
        return GatewaySessionDescriptor(
            connection_id=session.connection_id,
            protocol_version=claims.protocol_version,
            workspace_epoch=claims.workspace_epoch,
            page_epoch=claims.page_epoch,
            capabilities=claims.capabilities,
        )

    def _active_session(self, connection_id: str) -> _Session:
        self._require_running()
        with self._lock:
            session = self._sessions.get(str(connection_id or ""))
            if session is None or not session.active:
                raise SessionClosed()
            return session

    def _current_for_session(self, session: _Session) -> EpochResolution:
        claims = session.grant.claims
        try:
            resolution = self._resolve(
                claims.subject_id, claims.workspace_id, claims.page_id
            )
        except GatewayUnavailable:
            self._fence(session.connection_id, "authority_unavailable")
            raise
        except GatewayRejected:
            self._fence(session.connection_id, "stale_epoch")
            raise StaleEpoch() from None
        if (
            resolution.workspace_epoch != claims.workspace_epoch
            or resolution.page_epoch != claims.page_epoch
        ):
            self._fence(session.connection_id, "stale_epoch")
            raise StaleEpoch()
        return resolution

    def submit(
        self,
        connection_id: str,
        envelope: ProtocolEnvelope,
        *,
        fault: str | None = None,
    ) -> SubmitReceipt:
        session = self._active_session(connection_id)
        resolution = self._current_for_session(session)
        try:
            acknowledgement = self.sequence_ledger.accept(
                envelope,
                session.grant,
                current_workspace_epoch=resolution.workspace_epoch,
                current_page_epoch=resolution.page_epoch,
                fault=fault,
            )
        except StaleEpoch:
            self._fence(connection_id, "stale_epoch")
            raise
        except EnvelopeRejected:
            self._fence(connection_id, "protocol_violation")
            raise

        event = DurableProtocolEvent(
            workspace_id=envelope.workspace_id,
            page_id=envelope.page_id,
            document_sequence=acknowledgement.document_sequence,
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
        event_key = (
            event.workspace_id,
            event.page_id,
            event.document_sequence,
        )
        with self._lock:
            prior_event_sha256 = self._broadcasted_events.get(event_key)
            if prior_event_sha256 is None:
                self._broadcasted_events[event_key] = acknowledgement.event_sha256
                should_broadcast = True
            elif prior_event_sha256 == acknowledgement.event_sha256:
                should_broadcast = False
            else:
                self._fence_locked(session, "protocol_violation")
                raise EnvelopeRejected("durable event identity conflict")
        return SubmitReceipt(
            acknowledgement=acknowledgement,
            broadcast=(
                self._broadcast_durable(event)
                if should_broadcast
                else BroadcastResult(0, 0, 1, 0, 0)
            ),
        )

    def _enqueue_one(self, connection_id: str, event: DurableProtocolEvent) -> str:
        try:
            session = self._active_session(connection_id)
        except (GatewayUnavailable, SessionClosed):
            return "inactive"
        if not session.grant.allows("read"):
            return "not_subscribed"
        try:
            resolution = self._current_for_session(session)
            self.sequence_ledger.authorize_send(
                event,
                session.grant,
                current_workspace_epoch=resolution.workspace_epoch,
                current_page_epoch=resolution.page_epoch,
            )
        except GatewayUnavailable:
            return "fenced"
        except (GatewayRejected, StaleEpoch):
            self._fence(connection_id, "stale_epoch")
            return "fenced"

        with self._lock:
            current = self._sessions.get(connection_id)
            if current is None or not current.active:
                return "inactive"
            for queued in current.queue:
                if queued.document_sequence == event.document_sequence:
                    if (
                        queued.operation_id == event.operation_id
                        and queued.payload_sha256 == event.payload_sha256
                    ):
                        return "duplicate"
                    self._fence_locked(current, "protocol_violation")
                    return "fenced"
            if current.queue and event.document_sequence < current.queue[-1].document_sequence:
                self._fence_locked(current, "protocol_violation")
                return "fenced"
            projected_frames = len(current.queue) + 1
            projected_bytes = current.queued_payload_bytes + len(event.payload)
            if (
                projected_frames > self.max_queue_frames
                or projected_bytes > self.max_queue_bytes
            ):
                self._fence_locked(current, "backpressure_exceeded")
                return "backpressured"
            current.queue.append(event)
            current.queued_payload_bytes = projected_bytes
            return "enqueued"

    def _broadcast_durable(self, event: DurableProtocolEvent) -> BroadcastResult:
        self._require_running()
        with self._lock:
            targets = [
                session.connection_id
                for session in self._sessions.values()
                if session.active
                and session.grant.claims.workspace_id == event.workspace_id
                and session.grant.claims.page_id == event.page_id
            ]
        outcomes = [self._enqueue_one(connection_id, event) for connection_id in targets]
        return BroadcastResult(
            considered_sessions=len(targets),
            enqueued_sessions=outcomes.count("enqueued"),
            duplicate_sessions=outcomes.count("duplicate"),
            fenced_sessions=outcomes.count("fenced"),
            backpressured_sessions=outcomes.count("backpressured"),
        )

    def replay_to_queue(
        self,
        connection_id: str,
        *,
        after_document_sequence: int,
        limit: int,
    ) -> ReplayQueueReceipt:
        session = self._active_session(connection_id)
        resolution = self._current_for_session(session)
        batch = self.sequence_ledger.replay(
            session.grant,
            after_document_sequence=after_document_sequence,
            limit=limit,
            current_workspace_epoch=resolution.workspace_epoch,
            current_page_epoch=resolution.page_epoch,
        )
        outcomes = [self._enqueue_one(connection_id, event) for event in batch.events]
        if any(outcome in {"backpressured", "fenced", "inactive"} for outcome in outcomes):
            raise SessionClosed()
        return ReplayQueueReceipt(
            batch=batch,
            enqueued_frames=outcomes.count("enqueued"),
            duplicate_frames=outcomes.count("duplicate"),
        )

    def drain(
        self, connection_id: str, *, max_frames: int
    ) -> tuple[DurableProtocolEvent, ...]:
        limit = _positive(max_frames, field_name="max_frames")
        session = self._active_session(connection_id)
        with self._lock:
            current = self._sessions.get(connection_id)
            if current is None or not current.active:
                raise SessionClosed()
            selected = tuple(current.queue[:limit])
        try:
            for event in selected:
                # Resolve for each protected frame at the last modelled send
                # boundary; one check for a whole batch is insufficient.
                resolution = self._current_for_session(session)
                self.sequence_ledger.authorize_send(
                    event,
                    session.grant,
                    current_workspace_epoch=resolution.workspace_epoch,
                    current_page_epoch=resolution.page_epoch,
                )
        except StaleEpoch:
            self._fence(connection_id, "stale_epoch")
            raise
        with self._lock:
            current = self._sessions.get(connection_id)
            if current is None or not current.active:
                raise SessionClosed()
            if tuple(current.queue[: len(selected)]) != selected:
                self._fence_locked(current, "protocol_violation")
                raise SessionClosed()
            del current.queue[: len(selected)]
            current.queued_payload_bytes -= sum(len(event.payload) for event in selected)
            return selected

    def disconnect(self, connection_id: str) -> None:
        self._fence(str(connection_id or ""), "client_disconnect")

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
            for session in self._sessions.values():
                if session.active:
                    self._fence_locked(session, "gateway_stopped")

    def session_status(self, connection_id: str) -> dict[str, object]:
        with self._lock:
            session = self._sessions.get(str(connection_id or ""))
            if session is None:
                raise SessionClosed()
            return {
                "connection_id": session.connection_id,
                "active": session.active,
                "close_reason": session.close_reason,
                "queued_frames": len(session.queue),
                "queued_payload_bytes": session.queued_payload_bytes,
                "last_queued_document_sequence": (
                    session.queue[-1].document_sequence if session.queue else None
                ),
            }

    def active_connection_count(self) -> int:
        with self._lock:
            return self._active_count_locked()
