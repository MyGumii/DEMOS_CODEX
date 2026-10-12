"""Durable, bounded collaboration gateway kernel for T0B-P2.

The kernel owns connection state and opaque outbound frames.  Canonical
authorization, tickets, document sequences, updates, and replay remain owned by
``DurableTransportAuthority``.  Every protected frame is re-authorized against
the canonical database immediately before it leaves the queue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import ctypes
import hashlib
import json
import math
import os
import secrets
import threading
import time
from typing import Any, Iterable, Mapping
import uuid

from .canonical_authority import (
    AuthorityUnavailable,
    EpochConflict,
    NotFoundOrDenied,
)
from .durable_transport import (
    DurableTransportAuthority,
    YJS_UPDATE_ENCODING,
)
from .protocol_contract import (
    Acknowledgement,
    DurableProtocolEvent,
    EnvelopeRejected,
    ProtocolEnvelope,
    StaleEpoch,
    TicketGrant,
    TicketRejected,
)
from .selected_runtime_contract import (
    build_health_report,
    runtime_binding_sha256,
    validate_checked_in_binding,
)


RUNTIME_GATEWAY_CONTRACT = "code_assist.notion.t0b_p2.runtime_gateway.v1"
METRICS_CONTRACT = "code_assist.notion.t0b_p2.runtime_metrics.v1"
DEFAULT_MAX_QUEUE_FRAMES = 64
DEFAULT_MAX_QUEUE_BYTES = 4_194_304
DEFAULT_MAX_CONNECTIONS = 1_024
DEFAULT_RECONNECT_WINDOW_MS = 10_000
DEFAULT_RECONNECT_ATTEMPTS = 5
DEFAULT_PRESENCE_TTL_MS = 15_000
MIN_PRESENCE_TTL_MS = 5_000
MAX_PRESENCE_TTL_MS = 60_000
MAX_PRESENCE_PAYLOAD_BYTES = 4_096
ALLOWED_DERIVED_FRAME_KINDS = frozenset({"cache", "inbox", "search"})
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
REQUIRED_COUNTERS = frozenset(
    {
        "active_connections",
        "authority_unavailable_total",
        "compaction_request_total_by_result",
        "compactor_restart_total",
        "durable_ACK_total",
        "epoch_fence_drop_total",
        "gateway_restart_total",
        "inbound_commands_total_by_result",
        "queue_overflow_disconnect_total",
        "replay_events_total",
        "ticket_admission_total_by_result",
    }
)
REQUIRED_GAUGES = frozenset(
    {
        "compaction_lag_sequences",
        "durable_event_tail_length",
        "oldest_uncompacted_event_age_seconds",
        "outbound_queue_bytes",
        "outbound_queue_frames",
        "process_RSS_bytes",
    }
)
REQUIRED_HISTOGRAMS = frozenset(
    {
        "ACK_latency_ms",
        "command_commit_latency_ms",
        "compaction_duration_ms",
        "replay_latency_ms",
        "restart_to_ready_ms",
        "send_time_epoch_check_latency_ms",
        "snapshot_bytes",
    }
)


class GatewayRejected(PermissionError):
    def __init__(self) -> None:
        super().__init__("collaboration gateway rejected")


class GatewayUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("collaboration gateway unavailable")


class ReconnectThrottled(RuntimeError):
    def __init__(self) -> None:
        super().__init__("collaboration reconnect throttled")


class ConnectionCapacityExceeded(RuntimeError):
    def __init__(self) -> None:
        super().__init__("collaboration connection capacity exceeded")


class SessionClosed(RuntimeError):
    def __init__(self) -> None:
        super().__init__("collaboration session closed")


def _positive(value: int, *, field: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"valid {field} required")
    return value


def _nonnegative(value: int, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"valid {field} required")
    return value


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _nearest_rank(values: list[float], percent: int) -> float | None:
    if not values:
        return None
    index = max(0, math.ceil(len(values) * percent / 100) - 1)
    return values[index]


def _process_rss_bytes() -> int:
    """Return current RSS using only the standard library."""

    if os.name == "nt":
        class _ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
        if ctypes.windll.psapi.GetProcessMemoryInfo(  # type: ignore[attr-defined]
            handle, ctypes.byref(counters), counters.cb
        ):
            return max(1, int(counters.WorkingSetSize))
    try:
        import resource

        observed = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return max(1, observed if os.uname().sysname == "Darwin" else observed * 1024)
    except (AttributeError, ImportError, OSError):
        return 1


@dataclass(frozen=True, slots=True)
class GatewaySessionDescriptor:
    connection_id: str
    protocol_version: int
    workspace_epoch: int
    page_epoch: int
    capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BroadcastResult:
    considered_sessions: int
    enqueued_sessions: int
    duplicate_sessions: int
    fenced_sessions: int
    backpressured_sessions: int


@dataclass(frozen=True, slots=True)
class SubmitReceipt:
    acknowledgement: Acknowledgement
    broadcast: BroadcastResult


@dataclass(frozen=True, slots=True)
class ReplayQueueReceipt:
    high_water_sequence: int
    snapshot_enqueued: bool
    event_frames_enqueued: int
    duplicate_frames: int
    has_more: bool
    next_document_sequence: int = 0


@dataclass(frozen=True, slots=True)
class LiveReplayQueueReceipt:
    next_document_sequence: int
    high_water_sequence: int
    event_frames_enqueued: int
    duplicate_frames: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class PresenceReceipt:
    expires_at_ms: int
    considered_sessions: int
    enqueued_sessions: int
    fenced_sessions: int
    backpressured_sessions: int


@dataclass(frozen=True, slots=True)
class OutboundFrame:
    kind: str
    workspace_id: str
    page_id: str
    workspace_epoch: int
    page_epoch: int
    payload_encoding: str
    payload_sha256: str
    payload: bytes = field(repr=False)
    document_sequence: int | None = None
    operation_id: str | None = None

    def public_document(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "workspace_epoch": self.workspace_epoch,
            "page_epoch": self.page_epoch,
            "payload_encoding": self.payload_encoding,
            "payload_sha256": self.payload_sha256,
            "payload": self.payload,
            "document_sequence": self.document_sequence,
            "operation_id": self.operation_id,
        }


@dataclass(slots=True)
class _Session:
    connection_id: str
    grant: TicketGrant
    active: bool = True
    close_reason: str | None = None
    queue: list[OutboundFrame] = field(default_factory=list)
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


@dataclass(frozen=True, slots=True)
class _PresenceRecord:
    connection_id: str
    subject_id: str
    workspace_id: str
    page_id: str
    workspace_epoch: int
    page_epoch: int
    client_id: str
    expires_at_ms: int
    payload: bytes


class RuntimeMetrics:
    """Small in-process registry whose exported dimensions are redacted."""

    def __init__(self, *, hash_salt: bytes | None = None) -> None:
        salt = secrets.token_bytes(32) if hash_salt is None else bytes(hash_salt)
        if len(salt) < 16:
            raise ValueError("metrics hash salt must contain at least 16 bytes")
        self._salt = salt
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {
            (name, ()): 0 for name in REQUIRED_COUNTERS
        }
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], int | float] = {
            (name, ()): 0 for name in REQUIRED_GAUGES
        }
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = {
            (name, ()): [] for name in REQUIRED_HISTOGRAMS
        }

    def _hash_identifier(self, value: str) -> str:
        return "sha256:" + hashlib.sha256(self._salt + value.encode("utf-8")).hexdigest()

    def _labels(
        self,
        *,
        workspace_id: str | None = None,
        page_id: str | None = None,
        result_code: str | None = None,
        message_type: str | None = None,
    ) -> tuple[tuple[str, str], ...]:
        labels: dict[str, str] = {}
        if workspace_id is not None:
            labels["workspace_id_hash"] = self._hash_identifier(workspace_id)
        if page_id is not None:
            labels["page_id_hash"] = self._hash_identifier(page_id)
        if result_code is not None:
            labels["result_code"] = str(result_code)
        if message_type is not None:
            labels["message_type"] = str(message_type)
        return tuple(sorted(labels.items()))

    def increment(self, name: str, amount: int = 1, **dimensions: str | None) -> None:
        if name not in REQUIRED_COUNTERS or type(amount) is not int or amount < 0:
            raise ValueError("invalid counter update")
        key = (name, self._labels(**dimensions))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + amount

    def set_counter(self, name: str, value: int, **dimensions: str | None) -> None:
        if name != "active_connections" or type(value) is not int or value < 0:
            raise ValueError("invalid current-value counter update")
        key = (name, self._labels(**dimensions))
        with self._lock:
            self._counters[key] = value

    def gauge(self, name: str, value: int | float, **dimensions: str | None) -> None:
        if name not in REQUIRED_GAUGES or isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("invalid gauge update")
        key = (name, self._labels(**dimensions))
        with self._lock:
            self._gauges[key] = value

    def observe(self, name: str, value: int | float, **dimensions: str | None) -> None:
        if name not in REQUIRED_HISTOGRAMS or isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError("invalid histogram observation")
        key = (name, self._labels(**dimensions))
        with self._lock:
            self._histograms.setdefault(key, []).append(float(value))

    def snapshot(self) -> dict[str, Any]:
        self.gauge("process_RSS_bytes", _process_rss_bytes())
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            histograms = {key: tuple(values) for key, values in self._histograms.items()}

        def rows(values: Mapping[tuple[str, tuple[tuple[str, str], ...]], Any]) -> list[dict[str, Any]]:
            return [
                {"name": name, "labels": dict(labels), "value": value}
                for (name, labels), value in sorted(values.items())
            ]

        histogram_rows = []
        for (name, labels), values in sorted(histograms.items()):
            ordered = sorted(values)
            histogram_rows.append(
                {
                    "name": name,
                    "labels": dict(labels),
                    "count": len(ordered),
                    "minimum": None if not ordered else ordered[0],
                    "p50": _nearest_rank(ordered, 50),
                    "p95": _nearest_rank(ordered, 95),
                    "p99": _nearest_rank(ordered, 99),
                    "maximum": None if not ordered else ordered[-1],
                    "sum": sum(ordered),
                }
            )
        return {
            "contract": METRICS_CONTRACT,
            "runtime_binding_sha256": runtime_binding_sha256(),
            "counters": rows(counters),
            "gauges": rows(gauges),
            "histograms": histogram_rows,
        }


class RuntimeGateway:
    """Thread-safe gateway over the durable canonical transport authority."""

    def __init__(
        self,
        *,
        transport: DurableTransportAuthority,
        aggregate_enabled: bool = False,
        realtime_enabled: bool = False,
        max_queue_frames: int = DEFAULT_MAX_QUEUE_FRAMES,
        max_queue_bytes: int = DEFAULT_MAX_QUEUE_BYTES,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        reconnect_window_ms: int = DEFAULT_RECONNECT_WINDOW_MS,
        reconnect_attempts: int = DEFAULT_RECONNECT_ATTEMPTS,
        metrics: RuntimeMetrics | None = None,
    ) -> None:
        self.transport = transport
        self.authority = transport.canonical_authority()
        self.aggregate_enabled = aggregate_enabled is True
        self.realtime_enabled = realtime_enabled is True
        self.max_queue_frames = _positive(max_queue_frames, field="max_queue_frames")
        self.max_queue_bytes = _positive(max_queue_bytes, field="max_queue_bytes")
        self.max_connections = _positive(max_connections, field="max_connections")
        self.reconnect_window_ms = _positive(reconnect_window_ms, field="reconnect_window_ms")
        self.reconnect_attempts = _positive(reconnect_attempts, field="reconnect_attempts")
        self.metrics = RuntimeMetrics() if metrics is None else metrics
        self._lock = threading.RLock()
        # SQLite already serializes durable writers.  This lock additionally
        # preserves document-sequence order between commit and in-memory
        # broadcast when several ASGI tasks submit concurrently.
        self._submit_lock = threading.Lock()
        self._sessions: dict[str, _Session] = {}
        self._active_bindings: dict[tuple[str, str, str, str], str] = {}
        self._attempt_windows: dict[tuple[str, str], tuple[int, int]] = {}
        self._broadcasted_events: dict[tuple[str, str, int], str] = {}
        self._presence: dict[str, _PresenceRecord] = {}
        self._state = "STOPPED"
        self._authority_available = False
        self._schema_compatible = False
        self._binding_matches = False
        self._ownership_attestation_matches = False
        self._compactor_ready = False
        self._previously_ready_session = False
        self._started_at = 0.0
        self._set_queue_gauges_locked()

    def start(
        self,
        *,
        authority_available: bool = True,
        schema_compatible: bool = True,
        ownership_attestation_matches: bool = True,
        compactor_handshake_matches: bool,
    ) -> dict[str, Any]:
        with self._lock:
            if self._state not in {"STOPPED", "FAILED"}:
                raise GatewayUnavailable()
            restarting = self._previously_ready_session
            self._state = "STARTING"
            self._started_at = time.perf_counter()
        try:
            validate_checked_in_binding()
            binding_matches = True
        except Exception:
            binding_matches = False
        with self._lock:
            self._authority_available = authority_available is True
            self._schema_compatible = schema_compatible is True
            self._binding_matches = binding_matches
            self._ownership_attestation_matches = ownership_attestation_matches is True
            self._compactor_ready = compactor_handshake_matches is True
            self._state = "READY" if all(
                (
                    self._authority_available,
                    self._schema_compatible,
                    self._binding_matches,
                    self._ownership_attestation_matches,
                )
            ) else "FAILED"
            report = self.health()
        if report["ready_for_admission"]:
            self.metrics.observe("restart_to_ready_ms", (time.perf_counter() - self._started_at) * 1000)
            if restarting:
                self.metrics.increment("gateway_restart_total")
        return report

    def health(self) -> dict[str, Any]:
        with self._lock:
            queue_pressure = self._queue_pressure_locked()
            return build_health_report(
                state=self._state,
                aggregate_enabled=self.aggregate_enabled,
                realtime_enabled=self.realtime_enabled,
                authority_available=self._authority_available,
                schema_compatible=self._schema_compatible,
                runtime_binding_matches=self._binding_matches,
                ownership_attestation_matches=self._ownership_attestation_matches,
                compactor_ready=self._compactor_ready,
                queue_pressure_state=queue_pressure,
                previously_ready_session=self._previously_ready_session,
            )

    def metrics_snapshot(self, *, now_seconds: int | None = None) -> dict[str, Any]:
        """Refresh store/resource gauges and return one redacted metric snapshot."""

        observation = self.transport.operational_observation(now_seconds=now_seconds)
        self.metrics.gauge("durable_event_tail_length", observation["durable_event_tail_length"])
        self.metrics.gauge("compaction_lag_sequences", observation["compaction_lag_sequences"])
        self.metrics.gauge(
            "oldest_uncompacted_event_age_seconds",
            observation["oldest_uncompacted_event_age_seconds"],
        )
        return self.metrics.snapshot()

    def record_compactor_restart(self) -> None:
        self.metrics.increment("compactor_restart_total")

    def record_compaction_result(
        self,
        *,
        result: str,
        duration_ms: int | float,
        snapshot_bytes: int,
    ) -> None:
        normalized = str(result or "").strip().lower()
        if normalized not in {"committed", "conflict", "failed"}:
            raise ValueError("invalid compaction result")
        duration = float(duration_ms)
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("invalid compaction duration")
        size = _nonnegative(snapshot_bytes, field="snapshot_bytes")
        self.metrics.increment(
            "compaction_request_total_by_result", result_code=normalized
        )
        if normalized == "committed":
            self.metrics.observe("compaction_duration_ms", duration)
            self.metrics.observe("snapshot_bytes", size)

    def set_compactor_ready(self, ready: bool) -> dict[str, Any]:
        with self._lock:
            self._compactor_ready = ready is True
            return self.health()

    def _require_admission(self) -> None:
        if not self.health()["ready_for_admission"]:
            raise GatewayUnavailable()

    def _require_realtime(self) -> None:
        if not self.health()["ready_for_realtime"]:
            raise GatewayUnavailable()

    def _record_attempt(self, subject_id: str, client_id: str, now_ms: int) -> None:
        now = _nonnegative(now_ms, field="now_ms")
        key = (str(subject_id or ""), str(client_id or ""))
        if not all(key):
            raise GatewayRejected()
        window = now // self.reconnect_window_ms
        with self._lock:
            prior_window, prior_count = self._attempt_windows.get(key, (window, 0))
            count = prior_count + 1 if prior_window == window else 1
            self._attempt_windows[key] = (window, count)
        if count > self.reconnect_attempts:
            self.metrics.increment(
                "ticket_admission_total_by_result",
                result_code="reconnect_throttled",
            )
            raise ReconnectThrottled()

    def _active_count_locked(self) -> int:
        return sum(session.active for session in self._sessions.values())

    def _set_queue_gauges_locked(self) -> None:
        self.metrics.gauge(
            "outbound_queue_frames",
            sum(len(session.queue) for session in self._sessions.values() if session.active),
        )
        self.metrics.gauge(
            "outbound_queue_bytes",
            sum(session.queued_payload_bytes for session in self._sessions.values() if session.active),
        )

    def _queue_pressure_locked(self) -> str:
        active = [session for session in self._sessions.values() if session.active]
        frames = max((len(session.queue) for session in active), default=0)
        payload_bytes = max((session.queued_payload_bytes for session in active), default=0)
        # A queue exactly at its admitted bound must remain drainable.  The
        # enqueue attempt that would exceed either bound fences that one slow
        # consumer; it never publishes an over-bound queue.
        if frames > self.max_queue_frames or payload_bytes > self.max_queue_bytes:
            return "overflow"
        if frames >= max(1, self.max_queue_frames * 3 // 4) or payload_bytes >= max(1, self.max_queue_bytes * 3 // 4):
            return "warning"
        return "normal"

    def _fence_locked(self, session: _Session, reason: str) -> None:
        if reason not in ALLOWED_CLOSE_REASONS:
            raise ValueError("unknown close reason")
        if not session.active:
            return
        session.active = False
        session.close_reason = reason
        session.queue.clear()
        session.queued_payload_bytes = 0
        if self._active_bindings.get(session.binding_key) == session.connection_id:
            self._active_bindings.pop(session.binding_key, None)
        self._set_queue_gauges_locked()
        self.metrics.set_counter("active_connections", self._active_count_locked())

    def _fence(self, connection_id: str, reason: str) -> None:
        with self._lock:
            session = self._sessions.get(str(connection_id or ""))
            if session is not None:
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
        self._require_admission()
        self._record_attempt(subject_id, client_id, now_ms)
        provisional_key = (str(subject_id), str(page_id), str(client_id), str(session_binding_sha256))
        with self._lock:
            existing_id = self._active_bindings.get(provisional_key)
            if existing_id is None and self._active_count_locked() >= self.max_connections:
                self.metrics.increment("ticket_admission_total_by_result", result_code="capacity")
                raise ConnectionCapacityExceeded()
        try:
            grant = self.transport.consume_ticket(
                token,
                origin=origin,
                subject_id=subject_id,
                workspace_id=workspace_id,
                document_id=page_id,
                client_id=client_id,
                session_binding_sha256=session_binding_sha256,
                now_ms=now_ms,
            )
        except (TicketRejected, NotFoundOrDenied, EpochConflict, StaleEpoch, ValueError):
            self.metrics.increment("ticket_admission_total_by_result", result_code="rejected")
            raise GatewayRejected() from None
        except AuthorityUnavailable:
            self.metrics.increment("ticket_admission_total_by_result", result_code="authority_unavailable")
            self.metrics.increment("authority_unavailable_total")
            raise GatewayUnavailable() from None
        session = _Session("conn_" + uuid.uuid4().hex, grant)
        with self._lock:
            if not self.health()["ready_for_admission"]:
                raise GatewayUnavailable()
            existing_id = self._active_bindings.get(session.binding_key)
            if existing_id is None and self._active_count_locked() >= self.max_connections:
                raise ConnectionCapacityExceeded()
            if existing_id is not None:
                existing = self._sessions.get(existing_id)
                if existing is not None:
                    self._fence_locked(existing, "superseded_by_reconnect")
            self._sessions[session.connection_id] = session
            self._active_bindings[session.binding_key] = session.connection_id
            self._previously_ready_session = True
            self._set_queue_gauges_locked()
            self.metrics.set_counter("active_connections", self._active_count_locked())
        self.metrics.increment(
            "ticket_admission_total_by_result",
            result_code="accepted",
            workspace_id=workspace_id,
            page_id=page_id,
        )
        claims = grant.claims
        return GatewaySessionDescriptor(
            session.connection_id,
            claims.protocol_version,
            claims.workspace_epoch,
            claims.page_epoch,
            claims.capabilities,
        )

    def _active_session(self, connection_id: str) -> _Session:
        self._require_realtime()
        with self._lock:
            session = self._sessions.get(str(connection_id or ""))
            if session is None or not session.active:
                raise SessionClosed()
            return session

    def _authorize_session(self, session: _Session, *, minimum_role: str = "view") -> None:
        claims = session.grant.claims
        started = time.perf_counter()
        try:
            binding = self.authority.binding(claims.subject_id, claims.page_id, minimum_role=minimum_role)
            if (
                binding.workspace_id != claims.workspace_id
                or binding.workspace_epoch != claims.workspace_epoch
                or binding.document_epoch != claims.page_epoch
            ):
                raise EpochConflict(binding.workspace_epoch, binding.document_epoch)
        except AuthorityUnavailable:
            self._fence(session.connection_id, "authority_unavailable")
            self.metrics.increment("authority_unavailable_total")
            raise GatewayUnavailable() from None
        except (NotFoundOrDenied, EpochConflict, StaleEpoch):
            self._fence(session.connection_id, "stale_epoch")
            self.metrics.increment("epoch_fence_drop_total")
            raise StaleEpoch() from None
        finally:
            self.metrics.observe(
                "send_time_epoch_check_latency_ms",
                (time.perf_counter() - started) * 1000,
                workspace_id=claims.workspace_id,
                page_id=claims.page_id,
            )

    @staticmethod
    def _event_frame(event: DurableProtocolEvent) -> OutboundFrame:
        return OutboundFrame(
            "crdt.update",
            event.workspace_id,
            event.page_id,
            event.workspace_epoch,
            event.page_epoch,
            event.payload_encoding,
            event.payload_sha256,
            event.payload,
            event.document_sequence,
            event.operation_id,
        )

    @staticmethod
    def _replay_event_frame(
        session: _Session, event: DurableProtocolEvent
    ) -> OutboundFrame:
        """Bind historical payloads to the already revalidated reader epoch."""

        claims = session.grant.claims
        return OutboundFrame(
            "crdt.update",
            claims.workspace_id,
            claims.page_id,
            claims.workspace_epoch,
            claims.page_epoch,
            event.payload_encoding,
            event.payload_sha256,
            event.payload,
            event.document_sequence,
            event.operation_id,
        )

    def submit_payload(
        self,
        connection_id: str,
        *,
        operation_id: str,
        client_sequence: int,
        acknowledgement_sequence: int,
        payload: bytes,
        capabilities: Iterable[str] = ("write",),
        payload_encoding: str = YJS_UPDATE_ENCODING,
    ) -> SubmitReceipt:
        session = self._active_session(connection_id)
        envelope = ProtocolEnvelope.create(
            session.grant,
            message_type="crdt.update",
            operation_id=operation_id,
            client_sequence=client_sequence,
            acknowledgement_sequence=acknowledgement_sequence,
            payload=payload,
            capabilities=capabilities,
            payload_encoding=payload_encoding,
        )
        return self.submit(connection_id, envelope)

    def submit(self, connection_id: str, envelope: ProtocolEnvelope) -> SubmitReceipt:
        with self._submit_lock:
            return self._submit_serialized(connection_id, envelope)

    def _submit_serialized(
        self, connection_id: str, envelope: ProtocolEnvelope
    ) -> SubmitReceipt:
        session = self._active_session(connection_id)
        self._authorize_session(session, minimum_role="edit")
        started = time.perf_counter()
        try:
            acknowledgement = self.transport.accept_update(envelope, session.grant)
        except (NotFoundOrDenied, EpochConflict, StaleEpoch):
            self._fence(connection_id, "stale_epoch")
            self.metrics.increment("epoch_fence_drop_total")
            self.metrics.increment("inbound_commands_total_by_result", result_code="fenced", message_type=envelope.message_type)
            raise StaleEpoch() from None
        except AuthorityUnavailable:
            self._fence(connection_id, "authority_unavailable")
            self.metrics.increment("authority_unavailable_total")
            self.metrics.increment("inbound_commands_total_by_result", result_code="authority_unavailable", message_type=envelope.message_type)
            raise GatewayUnavailable() from None
        except EnvelopeRejected:
            self._fence(connection_id, "protocol_violation")
            self.metrics.increment("inbound_commands_total_by_result", result_code="rejected", message_type=envelope.message_type)
            raise
        elapsed = (time.perf_counter() - started) * 1000
        self.metrics.observe("command_commit_latency_ms", elapsed, workspace_id=envelope.workspace_id, page_id=envelope.page_id)
        self.metrics.observe("ACK_latency_ms", elapsed, workspace_id=envelope.workspace_id, page_id=envelope.page_id)
        self.metrics.increment("durable_ACK_total", workspace_id=envelope.workspace_id, page_id=envelope.page_id)
        self.metrics.increment("inbound_commands_total_by_result", result_code="committed", message_type=envelope.message_type)
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
        event_key = (event.workspace_id, event.page_id, event.document_sequence)
        with self._lock:
            prior = self._broadcasted_events.get(event_key)
            if prior is None:
                self._broadcasted_events[event_key] = acknowledgement.event_sha256
                should_broadcast = True
            elif prior == acknowledgement.event_sha256:
                should_broadcast = False
            else:
                self._fence_locked(session, "protocol_violation")
                raise EnvelopeRejected("durable event identity conflict")
        broadcast = self._broadcast(event) if should_broadcast else BroadcastResult(0, 0, 1, 0, 0)
        return SubmitReceipt(acknowledgement, broadcast)

    def _enqueue(self, connection_id: str, frame: OutboundFrame) -> str:
        try:
            session = self._active_session(connection_id)
            self._authorize_session(session)
        except (GatewayUnavailable, SessionClosed, StaleEpoch):
            return "fenced"
        claims = session.grant.claims
        if (
            not session.grant.allows("read")
            or frame.workspace_id != claims.workspace_id
            or frame.page_id != claims.page_id
            or frame.workspace_epoch != claims.workspace_epoch
            or frame.page_epoch != claims.page_epoch
        ):
            self._fence(connection_id, "stale_epoch")
            return "fenced"
        with self._lock:
            current = self._sessions.get(connection_id)
            if current is None or not current.active:
                return "fenced"
            if frame.kind.startswith("presence."):
                replaced = [
                    item
                    for item in current.queue
                    if item.kind.startswith("presence.")
                    and item.operation_id == frame.operation_id
                ]
                if replaced:
                    current.queue = [item for item in current.queue if item not in replaced]
                    current.queued_payload_bytes -= sum(len(item.payload) for item in replaced)
            for queued in current.queue:
                if (
                    queued.kind == frame.kind
                    and queued.document_sequence == frame.document_sequence
                    and queued.operation_id == frame.operation_id
                    and queued.payload_sha256 == frame.payload_sha256
                ):
                    return "duplicate"
            if frame.document_sequence is not None:
                durable_sequences = [item.document_sequence for item in current.queue if item.document_sequence is not None]
                if durable_sequences and frame.document_sequence < durable_sequences[-1]:
                    self._fence_locked(current, "protocol_violation")
                    return "fenced"
            if len(current.queue) + 1 > self.max_queue_frames or current.queued_payload_bytes + len(frame.payload) > self.max_queue_bytes:
                self._fence_locked(current, "backpressure_exceeded")
                self.metrics.increment("queue_overflow_disconnect_total", workspace_id=frame.workspace_id, page_id=frame.page_id)
                return "backpressured"
            current.queue.append(frame)
            current.queued_payload_bytes += len(frame.payload)
            self._set_queue_gauges_locked()
            return "enqueued"

    def _broadcast(self, event: DurableProtocolEvent) -> BroadcastResult:
        with self._lock:
            targets = [
                item.connection_id
                for item in self._sessions.values()
                if item.active
                and item.grant.claims.workspace_id == event.workspace_id
                and item.grant.claims.page_id == event.page_id
            ]
        outcomes = [self._enqueue(connection_id, self._event_frame(event)) for connection_id in targets]
        return BroadcastResult(
            len(targets),
            outcomes.count("enqueued"),
            outcomes.count("duplicate"),
            outcomes.count("fenced"),
            outcomes.count("backpressured"),
        )

    def replay_to_queue(self, connection_id: str, *, after_document_sequence: int, limit: int) -> ReplayQueueReceipt:
        session = self._active_session(connection_id)
        self._authorize_session(session)
        started = time.perf_counter()
        try:
            batch = self.transport.replay(
                session.grant,
                after_document_sequence=_nonnegative(after_document_sequence, field="after_document_sequence"),
                limit=_positive(limit, field="limit"),
            )
        except (NotFoundOrDenied, EpochConflict, StaleEpoch):
            self._fence(connection_id, "stale_epoch")
            self.metrics.increment("epoch_fence_drop_total")
            raise StaleEpoch() from None
        except AuthorityUnavailable:
            self._fence(connection_id, "authority_unavailable")
            self.metrics.increment("authority_unavailable_total")
            raise GatewayUnavailable() from None
        outcomes: list[str] = []
        snapshot_enqueued = False
        if batch.snapshot is not None:
            snapshot = batch.snapshot
            outcome = self._enqueue(
                connection_id,
                OutboundFrame(
                    "crdt.snapshot",
                    session.grant.claims.workspace_id,
                    session.grant.claims.page_id,
                    session.grant.claims.workspace_epoch,
                    session.grant.claims.page_epoch,
                    "application/vnd.yjs.snapshot",
                    snapshot.snapshot_sha256,
                    snapshot.snapshot,
                    snapshot.through_document_sequence,
                    snapshot.snapshot_id,
                ),
            )
            outcomes.append(outcome)
            snapshot_enqueued = outcome == "enqueued"
        for event in batch.events:
            outcomes.append(
                self._enqueue(connection_id, self._replay_event_frame(session, event))
            )
        if any(outcome in {"backpressured", "fenced"} for outcome in outcomes):
            raise SessionClosed()
        self.metrics.increment("replay_events_total", len(batch.events), workspace_id=session.grant.claims.workspace_id, page_id=session.grant.claims.page_id)
        self.metrics.observe("replay_latency_ms", (time.perf_counter() - started) * 1000, workspace_id=session.grant.claims.workspace_id, page_id=session.grant.claims.page_id)
        return ReplayQueueReceipt(
            batch.high_water_sequence,
            snapshot_enqueued,
            sum(outcome == "enqueued" for outcome in outcomes) - int(snapshot_enqueued),
            outcomes.count("duplicate"),
            batch.has_more,
            batch.events[-1].document_sequence if batch.has_more else batch.high_water_sequence,
        )

    def replay_live_to_queue(
        self,
        connection_id: str,
        *,
        after_document_sequence: int,
        limit: int,
    ) -> LiveReplayQueueReceipt:
        """Queue committed comment-domain signals; never synthesize live state."""

        session = self._active_session(connection_id)
        self._authorize_session(session)
        claims = session.grant.claims
        replay = getattr(self.authority, "replay_domain_events", None)
        if replay is None:
            raise GatewayUnavailable()
        started = time.perf_counter()
        try:
            batch = replay(
                claims.subject_id,
                claims.page_id,
                after_document_sequence=_nonnegative(
                    after_document_sequence, field="after_document_sequence"
                ),
                limit=_positive(limit, field="limit"),
            )
        except (NotFoundOrDenied, EpochConflict, StaleEpoch):
            self._fence(connection_id, "stale_epoch")
            self.metrics.increment("epoch_fence_drop_total")
            raise StaleEpoch() from None
        except AuthorityUnavailable:
            self._fence(connection_id, "authority_unavailable")
            self.metrics.increment("authority_unavailable_total")
            raise GatewayUnavailable() from None
        outcomes: list[str] = []
        for event in batch.events:
            raw = json.dumps(
                event.public_document(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            outcomes.append(
                self._enqueue(
                    connection_id,
                    OutboundFrame(
                        event.event_type,
                        claims.workspace_id,
                        claims.page_id,
                        claims.workspace_epoch,
                        claims.page_epoch,
                        "application/json",
                        _sha256(raw),
                        raw,
                        event.document_sequence,
                        event.operation_id,
                    ),
                )
            )
        if any(outcome in {"backpressured", "fenced"} for outcome in outcomes):
            raise SessionClosed()
        self.metrics.increment(
            "replay_events_total",
            len(batch.events),
            workspace_id=claims.workspace_id,
            page_id=claims.page_id,
        )
        self.metrics.observe(
            "replay_latency_ms",
            (time.perf_counter() - started) * 1000,
            workspace_id=claims.workspace_id,
            page_id=claims.page_id,
        )
        return LiveReplayQueueReceipt(
            batch.next_document_sequence,
            batch.high_water_sequence,
            outcomes.count("enqueued"),
            outcomes.count("duplicate"),
            batch.has_more,
        )

    @staticmethod
    def _presence_payload(
        session: _Session, payload: Mapping[str, Any], *, state: str
    ) -> bytes:
        unknown = set(payload) - {
            "cursor_block_id",
            "selection_start",
            "selection_end",
            "state",
        }
        if unknown:
            raise ValueError("presence payload contains unknown fields")
        cursor = payload.get("cursor_block_id")
        if cursor is not None:
            cursor = str(cursor).strip()
            if not cursor or len(cursor) > 160:
                raise ValueError("presence cursor block is invalid")
        selection: dict[str, int] = {}
        for name in ("selection_start", "selection_end"):
            value = payload.get(name)
            if value is not None:
                if type(value) is not int or value < 0 or value > 10_000_000:
                    raise ValueError("presence selection is invalid")
                selection[name] = value
        requested_state = str(payload.get("state") or state).strip().lower()
        if requested_state not in {"active", "idle", "left"}:
            raise ValueError("presence state is invalid")
        claims = session.grant.claims
        document = {
            "subject_id": claims.subject_id,
            "client_id": claims.client_id,
            "state": requested_state,
            **({"cursor_block_id": cursor} if cursor is not None else {}),
            **selection,
        }
        raw = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(raw) > MAX_PRESENCE_PAYLOAD_BYTES:
            raise ValueError("presence payload is too large")
        return raw

    def update_presence(
        self,
        connection_id: str,
        *,
        payload: Mapping[str, Any],
        now_ms: int,
        ttl_ms: int = DEFAULT_PRESENCE_TTL_MS,
    ) -> PresenceReceipt:
        session = self._active_session(connection_id)
        if not session.grant.allows("presence"):
            raise GatewayRejected()
        self._authorize_session(session)
        now = _nonnegative(now_ms, field="now_ms")
        ttl = _positive(ttl_ms, field="ttl_ms")
        if ttl < MIN_PRESENCE_TTL_MS or ttl > MAX_PRESENCE_TTL_MS:
            raise ValueError("presence ttl is outside bounds")
        raw = self._presence_payload(session, payload, state="active")
        claims = session.grant.claims
        record = _PresenceRecord(
            connection_id,
            claims.subject_id,
            claims.workspace_id,
            claims.page_id,
            claims.workspace_epoch,
            claims.page_epoch,
            claims.client_id,
            now + ttl,
            raw,
        )
        with self._lock:
            self._presence[connection_id] = record
            targets = [
                item.connection_id
                for item in self._sessions.values()
                if item.active
                and item.grant.claims.workspace_id == claims.workspace_id
                and item.grant.claims.page_id == claims.page_id
            ]
        frame = OutboundFrame(
            "presence.update",
            claims.workspace_id,
            claims.page_id,
            claims.workspace_epoch,
            claims.page_epoch,
            "application/json",
            _sha256(raw),
            raw,
            None,
            claims.subject_id,
        )
        outcomes = [self._enqueue(target, frame) for target in targets]
        return PresenceReceipt(
            record.expires_at_ms,
            len(targets),
            outcomes.count("enqueued"),
            outcomes.count("fenced"),
            outcomes.count("backpressured"),
        )

    def _remove_presence(self, connection_id: str) -> int:
        with self._lock:
            record = self._presence.pop(connection_id, None)
            if record is None:
                return 0
            source = self._sessions.get(connection_id)
            if source is None:
                return 0
            targets = [
                item.connection_id
                for item in self._sessions.values()
                if item.active
                and item.connection_id != connection_id
                and item.grant.claims.workspace_id == record.workspace_id
                and item.grant.claims.page_id == record.page_id
            ]
        raw = self._presence_payload(source, {}, state="left")
        frame = OutboundFrame(
            "presence.leave",
            record.workspace_id,
            record.page_id,
            record.workspace_epoch,
            record.page_epoch,
            "application/json",
            _sha256(raw),
            raw,
            None,
            record.subject_id,
        )
        return sum(self._enqueue(target, frame) == "enqueued" for target in targets)

    def expire_presence(self, *, now_ms: int) -> int:
        now = _nonnegative(now_ms, field="now_ms")
        with self._lock:
            expired = [
                connection_id
                for connection_id, record in self._presence.items()
                if record.expires_at_ms <= now
            ]
        for connection_id in expired:
            self._remove_presence(connection_id)
        return len(expired)

    def queue_derived_frame(self, connection_id: str, *, kind: str, payload: bytes) -> str:
        normalized = str(kind or "").strip().lower()
        if normalized not in ALLOWED_DERIVED_FRAME_KINDS:
            raise ValueError("unknown derived frame kind")
        session = self._active_session(connection_id)
        self._authorize_session(session)
        claims = session.grant.claims
        raw = bytes(payload)
        return self._enqueue(
            connection_id,
            OutboundFrame(
                normalized,
                claims.workspace_id,
                claims.page_id,
                claims.workspace_epoch,
                claims.page_epoch,
                "application/json",
                _sha256(raw),
                raw,
            ),
        )

    def drain(self, connection_id: str, *, max_frames: int) -> tuple[OutboundFrame, ...]:
        limit = _positive(max_frames, field="max_frames")
        self.expire_presence(now_ms=int(time.time() * 1000))
        session = self._active_session(connection_id)
        with self._lock:
            current = self._sessions.get(connection_id)
            if current is None or not current.active:
                raise SessionClosed()
            selected = tuple(current.queue[:limit])
        for _frame in selected:
            self._authorize_session(session)
        with self._lock:
            current = self._sessions.get(connection_id)
            if current is None or not current.active or tuple(current.queue[: len(selected)]) != selected:
                if current is not None:
                    self._fence_locked(current, "protocol_violation")
                raise SessionClosed()
            del current.queue[: len(selected)]
            current.queued_payload_bytes -= sum(len(frame.payload) for frame in selected)
            self._set_queue_gauges_locked()
        return selected

    def invalidate_stale_sessions(self) -> int:
        with self._lock:
            active = [session for session in self._sessions.values() if session.active]
        fenced = 0
        for session in active:
            try:
                self._authorize_session(session)
            except (GatewayUnavailable, StaleEpoch):
                fenced += 1
        return fenced

    def disconnect(self, connection_id: str) -> None:
        self._remove_presence(connection_id)
        self._fence(connection_id, "client_disconnect")

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._state == "STOPPED":
                return self.health()
            self._state = "DRAINING"
            for session in self._sessions.values():
                self._fence_locked(session, "gateway_stopped")
            self._presence.clear()
            self._state = "STOPPED"
            self._authority_available = False
            self._compactor_ready = False
            return self.health()

    def session_status(self, connection_id: str) -> dict[str, Any]:
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
                "last_queued_document_sequence": next(
                    (frame.document_sequence for frame in reversed(session.queue) if frame.document_sequence is not None),
                    None,
                ),
            }

    def active_connection_count(self) -> int:
        with self._lock:
            return self._active_count_locked()


__all__ = [
    "ALLOWED_DERIVED_FRAME_KINDS",
    "BroadcastResult",
    "ConnectionCapacityExceeded",
    "GatewayRejected",
    "GatewaySessionDescriptor",
    "GatewayUnavailable",
    "METRICS_CONTRACT",
    "LiveReplayQueueReceipt",
    "OutboundFrame",
    "PresenceReceipt",
    "RUNTIME_GATEWAY_CONTRACT",
    "ReconnectThrottled",
    "ReplayQueueReceipt",
    "RuntimeGateway",
    "RuntimeMetrics",
    "SessionClosed",
    "SubmitReceipt",
]
