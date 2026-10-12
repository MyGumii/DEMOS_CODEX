from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.notion_collaboration.protocol_contract import (
    InMemorySequenceLedger,
    ProtocolEnvelope,
    TicketAuthority,
    session_binding_sha256,
)
from code_assist_v1.notion_collaboration.transport_gateway_prototype import (
    ConnectionCapacityExceeded,
    EpochResolution,
    GatewayRejected,
    GatewayUnavailable,
    ReconnectThrottled,
    SessionClosed,
    TransportOnlyGateway,
)
from code_assist_v1.tools.verify_internal_collaborative_gateway_contract import (
    DEFAULT_GATEWAY,
    verify_gateway_contract,
)
from code_assist_v1.tools.verify_notion_parity_catalog import load_json


ORIGIN = "http://127.0.0.1:10112"


class MutableEpochResolver:
    def __init__(self) -> None:
        self.available = True
        self.bindings: dict[tuple[str, str, str], EpochResolution | None] = {}

    def allow(
        self,
        subject_id: str,
        *,
        workspace_epoch: int = 4,
        page_epoch: int = 7,
    ) -> None:
        self.bindings[(subject_id, "workspace-alpha", "page-shared")] = EpochResolution(
            workspace_epoch, page_epoch
        )

    def deny(self, subject_id: str) -> None:
        self.bindings[(subject_id, "workspace-alpha", "page-shared")] = None

    def __call__(
        self, subject_id: str, workspace_id: str, page_id: str
    ) -> EpochResolution | None:
        if not self.available:
            raise RuntimeError("synthetic authority outage")
        return self.bindings.get((subject_id, workspace_id, page_id))


def _gateway(
    *,
    max_queue_frames: int = 64,
    max_queue_bytes: int = 4_194_304,
    max_connections: int = 1_024,
    reconnect_window_ms: int = 10_000,
    reconnect_attempts: int = 5,
) -> tuple[TransportOnlyGateway, TicketAuthority, InMemorySequenceLedger, MutableEpochResolver]:
    tickets = TicketAuthority()
    ledger = InMemorySequenceLedger()
    resolver = MutableEpochResolver()
    gateway = TransportOnlyGateway(
        ticket_authority=tickets,
        sequence_ledger=ledger,
        epoch_resolver=resolver,
        max_queue_frames=max_queue_frames,
        max_queue_bytes=max_queue_bytes,
        max_connections=max_connections,
        reconnect_window_ms=reconnect_window_ms,
        reconnect_attempts=reconnect_attempts,
    )
    return gateway, tickets, ledger, resolver


def _issue(
    tickets: TicketAuthority,
    resolver: MutableEpochResolver,
    *,
    subject_id: str,
    client_id: str,
    session_secret: str,
    now_ms: int = 1_000,
    ttl_ms: int = 10_000,
    ticket_workspace_epoch: int | None = None,
    ticket_page_epoch: int | None = None,
):
    resolution = resolver(subject_id, "workspace-alpha", "page-shared")
    assert resolution is not None
    return tickets.issue(
        csrf_valid=True,
        subject_id=subject_id,
        workspace_id="workspace-alpha",
        page_id="page-shared",
        client_id=client_id,
        session_binding_sha256=session_binding_sha256(session_secret),
        origin=ORIGIN,
        capabilities=("comment", "presence", "read", "write"),
        workspace_epoch=(
            resolution.workspace_epoch
            if ticket_workspace_epoch is None
            else ticket_workspace_epoch
        ),
        page_epoch=(resolution.page_epoch if ticket_page_epoch is None else ticket_page_epoch),
        now_ms=now_ms,
        ttl_ms=ttl_ms,
    )


def _connect(
    gateway: TransportOnlyGateway,
    tickets: TicketAuthority,
    resolver: MutableEpochResolver,
    *,
    subject_id: str = "subject-owner",
    client_id: str = "client-browser-a",
    session_secret: str = "session-owner-a",
    now_ms: int = 1_001,
):
    issued = _issue(
        tickets,
        resolver,
        subject_id=subject_id,
        client_id=client_id,
        session_secret=session_secret,
        now_ms=now_ms - 1,
    )
    descriptor = gateway.connect(
        issued.token,
        origin=ORIGIN,
        subject_id=subject_id,
        workspace_id="workspace-alpha",
        page_id="page-shared",
        client_id=client_id,
        session_binding_sha256=session_binding_sha256(session_secret),
        now_ms=now_ms,
    )
    return issued, descriptor


def _envelope(
    issued,
    *,
    operation_id: str,
    client_sequence: int,
    acknowledgement_sequence: int,
    payload: bytes,
) -> ProtocolEnvelope:
    from code_assist_v1.notion_collaboration.protocol_contract import TicketGrant

    # A gateway session owns the consumed grant.  Tests reconstruct only the
    # immutable public binding to build a client envelope; no token is reused.
    grant = TicketGrant(claims=issued.claims, consumed_at_ms=issued.claims.issued_at_ms + 1)
    return ProtocolEnvelope.create(
        grant,
        message_type="typed.command",
        operation_id=operation_id,
        client_sequence=client_sequence,
        acknowledgement_sequence=acknowledgement_sequence,
        payload=payload,
        capabilities=("write",),
        payload_encoding="application/json",
    )


def _codes(report: dict[str, object]) -> set[str]:
    return {item["code"] for item in report["errors"]}  # type: ignore[index]


@pytest.mark.unit
def test_t0a_p2_checked_in_gateway_backpressure_and_unavailable_contract_is_current() -> None:
    report, exit_code = verify_gateway_contract()
    assert exit_code == 0, report
    assert report["status"] == "pass"
    assert report["summary"] == {
        "packet_id": "T0A-P2",
        "negative_scenario_count": 11,
        "maximum_durable_frames_default": 64,
        "maximum_attempts_per_window_default": 5,
        "actual_network_gateway_implemented": False,
        "product_complete": False,
    }


@pytest.mark.integration
def test_t0a_p2_transport_submits_after_authority_check_and_broadcasts_committed_event() -> None:
    gateway, tickets, ledger, resolver = _gateway()
    resolver.allow("subject-owner")
    resolver.allow("subject-member")
    owner_ticket, owner = _connect(gateway, tickets, resolver)
    _, member = _connect(
        gateway,
        tickets,
        resolver,
        subject_id="subject-member",
        client_id="client-browser-b",
        session_secret="session-member-b",
    )
    envelope = _envelope(
        owner_ticket,
        operation_id="op-gateway-submit-0001",
        client_sequence=1,
        acknowledgement_sequence=0,
        payload=b'{"insert":"committed"}',
    )
    receipt = gateway.submit(owner.connection_id, envelope)
    assert receipt.acknowledgement.document_sequence == 1
    assert receipt.broadcast.enqueued_sessions == 2
    assert ledger.event_count("workspace-alpha", "page-shared") == 1
    delivered = gateway.drain(member.connection_id, max_frames=1)
    assert len(delivered) == 1
    assert delivered[0].payload == b'{"insert":"committed"}'


@pytest.mark.integration
def test_t0a_p2_exact_submit_retry_does_not_rebroadcast_durable_event() -> None:
    gateway, tickets, ledger, resolver = _gateway()
    resolver.allow("subject-owner")
    issued, session = _connect(gateway, tickets, resolver)
    envelope = _envelope(
        issued,
        operation_id="op-gateway-dedup-0001",
        client_sequence=1,
        acknowledgement_sequence=0,
        payload=b"once",
    )
    first = gateway.submit(session.connection_id, envelope)
    assert len(gateway.drain(session.connection_id, max_frames=1)) == 1
    replay = gateway.submit(session.connection_id, envelope)
    assert replay.acknowledgement == first.acknowledgement
    assert replay.broadcast.considered_sessions == 0
    assert replay.broadcast.duplicate_sessions == 1
    assert gateway.drain(session.connection_id, max_frames=1) == ()
    assert ledger.event_count("workspace-alpha", "page-shared") == 1


@pytest.mark.parametrize("failure", ["wrong_origin", "expired", "reused", "stale_epoch"])
@pytest.mark.integration
def test_t0a_p2_origin_expiry_reuse_and_stale_epoch_admission_fail_non_oracle(
    failure: str,
) -> None:
    gateway, tickets, _, resolver = _gateway(reconnect_attempts=20)
    resolver.allow("subject-owner")
    issued = _issue(
        tickets,
        resolver,
        subject_id="subject-owner",
        client_id="client-browser-a",
        session_secret="session-owner-a",
        ttl_ms=10 if failure == "expired" else 10_000,
    )
    if failure == "stale_epoch":
        resolver.allow("subject-owner", workspace_epoch=5, page_epoch=8)
    values = {
        "origin": "https://attacker.example" if failure == "wrong_origin" else ORIGIN,
        "subject_id": "subject-owner",
        "workspace_id": "workspace-alpha",
        "page_id": "page-shared",
        "client_id": "client-browser-a",
        "session_binding_sha256": session_binding_sha256("session-owner-a"),
        "now_ms": 1_011 if failure == "expired" else 1_001,
    }
    if failure == "reused":
        gateway.connect(issued.token, **values)
    with pytest.raises(GatewayRejected) as rejected:
        gateway.connect(issued.token, **values)
    assert str(rejected.value) == "collaboration gateway rejected"


@pytest.mark.fault
def test_t0a_p2_slow_consumer_is_closed_and_must_bounded_replay_without_silent_drop() -> None:
    gateway, tickets, ledger, resolver = _gateway(max_queue_frames=2, max_queue_bytes=64)
    resolver.allow("subject-owner")
    resolver.allow("subject-slow")
    owner_ticket, owner = _connect(gateway, tickets, resolver)
    _, slow = _connect(
        gateway,
        tickets,
        resolver,
        subject_id="subject-slow",
        client_id="client-browser-slow",
        session_secret="session-slow",
    )

    acknowledgement = 0
    third_receipt = None
    for sequence in range(1, 4):
        receipt = gateway.submit(
            owner.connection_id,
            _envelope(
                owner_ticket,
                operation_id=f"op-backpressure-{sequence:04d}",
                client_sequence=sequence,
                acknowledgement_sequence=acknowledgement,
                payload=f"event-{sequence}".encode(),
            ),
        )
        acknowledgement = receipt.acknowledgement.document_sequence
        gateway.drain(owner.connection_id, max_frames=1)
        third_receipt = receipt

    assert third_receipt is not None
    assert third_receipt.broadcast.backpressured_sessions == 1
    assert ledger.event_count("workspace-alpha", "page-shared") == 3
    assert gateway.session_status(slow.connection_id) == {
        "connection_id": slow.connection_id,
        "active": False,
        "close_reason": "backpressure_exceeded",
        "queued_frames": 0,
        "queued_payload_bytes": 0,
        "last_queued_document_sequence": None,
    }
    with pytest.raises(SessionClosed):
        gateway.drain(slow.connection_id, max_frames=1)

    _, reconnected = _connect(
        gateway,
        tickets,
        resolver,
        subject_id="subject-slow",
        client_id="client-browser-slow",
        session_secret="session-slow",
        now_ms=2_001,
    )
    first_batch = gateway.replay_to_queue(
        reconnected.connection_id, after_document_sequence=0, limit=2
    )
    assert first_batch.enqueued_frames == 2
    assert [event.document_sequence for event in gateway.drain(reconnected.connection_id, max_frames=2)] == [1, 2]
    tail = gateway.replay_to_queue(
        reconnected.connection_id, after_document_sequence=2, limit=2
    )
    assert tail.enqueued_frames == 1
    assert [event.document_sequence for event in gateway.drain(reconnected.connection_id, max_frames=2)] == [3]


@pytest.mark.integration
def test_t0a_p2_same_binding_reconnect_supersedes_and_purges_stale_session() -> None:
    gateway, tickets, _, resolver = _gateway()
    resolver.allow("subject-owner")
    issued, first = _connect(gateway, tickets, resolver)
    gateway.submit(
        first.connection_id,
        _envelope(
            issued,
            operation_id="op-reconnect-queue-0001",
            client_sequence=1,
            acknowledgement_sequence=0,
            payload=b"protected",
        ),
    )
    assert gateway.session_status(first.connection_id)["queued_frames"] == 1
    _, second = _connect(gateway, tickets, resolver, now_ms=2_001)
    assert second.connection_id != first.connection_id
    assert gateway.active_connection_count() == 1
    assert gateway.session_status(first.connection_id)["close_reason"] == "superseded_by_reconnect"
    assert gateway.session_status(first.connection_id)["queued_frames"] == 0
    with pytest.raises(SessionClosed):
        gateway.drain(first.connection_id, max_frames=1)


@pytest.mark.integration
def test_t0a_p2_stale_epoch_before_submit_or_drain_closes_and_returns_zero_payload() -> None:
    gateway, tickets, ledger, resolver = _gateway()
    resolver.allow("subject-owner")
    issued, session = _connect(gateway, tickets, resolver)
    gateway.submit(
        session.connection_id,
        _envelope(
            issued,
            operation_id="op-epoch-queued-0001",
            client_sequence=1,
            acknowledgement_sequence=0,
            payload=b"must-not-deliver",
        ),
    )
    resolver.allow("subject-owner", workspace_epoch=5, page_epoch=8)
    from code_assist_v1.notion_collaboration.protocol_contract import StaleEpoch

    with pytest.raises(StaleEpoch):
        gateway.drain(session.connection_id, max_frames=1)
    assert gateway.session_status(session.connection_id)["queued_frames"] == 0
    assert gateway.session_status(session.connection_id)["close_reason"] == "stale_epoch"
    assert ledger.event_count("workspace-alpha", "page-shared") == 1

    resolver.allow("subject-owner")
    next_ticket, next_session = _connect(gateway, tickets, resolver, now_ms=2_001)
    resolver.allow("subject-owner", workspace_epoch=5, page_epoch=8)
    with pytest.raises(StaleEpoch):
        gateway.submit(
            next_session.connection_id,
            _envelope(
                next_ticket,
                operation_id="op-epoch-stale-submit-0002",
                client_sequence=2,
                acknowledgement_sequence=1,
                payload=b"must-not-commit",
            ),
        )
    assert ledger.event_count("workspace-alpha", "page-shared") == 1


@pytest.mark.fault
def test_t0a_p2_authority_unavailable_never_consumes_admission_ticket_or_drains_payload() -> None:
    gateway, tickets, ledger, resolver = _gateway()
    resolver.allow("subject-owner")
    pending = _issue(
        tickets,
        resolver,
        subject_id="subject-owner",
        client_id="client-pending",
        session_secret="session-pending",
    )
    resolver.available = False
    with pytest.raises(GatewayUnavailable):
        gateway.connect(
            pending.token,
            origin=ORIGIN,
            subject_id="subject-owner",
            workspace_id="workspace-alpha",
            page_id="page-shared",
            client_id="client-pending",
            session_binding_sha256=session_binding_sha256("session-pending"),
            now_ms=1_001,
        )
    resolver.available = True
    pending_session = gateway.connect(
        pending.token,
        origin=ORIGIN,
        subject_id="subject-owner",
        workspace_id="workspace-alpha",
        page_id="page-shared",
        client_id="client-pending",
        session_binding_sha256=session_binding_sha256("session-pending"),
        now_ms=1_002,
    )
    pending_grant_ticket = pending
    gateway.submit(
        pending_session.connection_id,
        _envelope(
            pending_grant_ticket,
            operation_id="op-unavailable-queued-0001",
            client_sequence=1,
            acknowledgement_sequence=0,
            payload=b"protected",
        ),
    )
    resolver.available = False
    with pytest.raises(GatewayUnavailable):
        gateway.drain(pending_session.connection_id, max_frames=1)
    assert gateway.session_status(pending_session.connection_id)["queued_frames"] == 0
    assert gateway.session_status(pending_session.connection_id)["close_reason"] == "authority_unavailable"
    assert ledger.event_count("workspace-alpha", "page-shared") == 1


@pytest.mark.integration
def test_t0a_p2_reconnect_storm_is_bounded_before_ticket_consumption_and_recovers_next_window() -> None:
    gateway, tickets, _, resolver = _gateway(
        reconnect_window_ms=100, reconnect_attempts=2
    )
    resolver.allow("subject-owner")
    issued = _issue(
        tickets,
        resolver,
        subject_id="subject-owner",
        client_id="client-storm",
        session_secret="session-storm",
        now_ms=1_000,
        ttl_ms=5_000,
    )
    values = {
        "origin": ORIGIN,
        "subject_id": "subject-owner",
        "workspace_id": "workspace-alpha",
        "page_id": "page-shared",
        "client_id": "client-storm",
        "session_binding_sha256": session_binding_sha256("session-storm"),
    }
    for now_ms in (1_001, 1_002):
        with pytest.raises(GatewayRejected):
            gateway.connect("x" * 43, now_ms=now_ms, **values)
    with pytest.raises(ReconnectThrottled):
        gateway.connect(issued.token, now_ms=1_003, **values)
    connected = gateway.connect(issued.token, now_ms=1_101, **values)
    assert connected.connection_id.startswith("conn_")
    assert gateway.active_connection_count() == 1


@pytest.mark.integration
def test_t0a_p2_connection_capacity_rejects_before_consumption_then_allows_freed_slot() -> None:
    gateway, tickets, _, resolver = _gateway(max_connections=1)
    resolver.allow("subject-owner")
    resolver.allow("subject-member")
    _, owner = _connect(gateway, tickets, resolver)
    member_ticket = _issue(
        tickets,
        resolver,
        subject_id="subject-member",
        client_id="client-member",
        session_secret="session-member",
    )
    values = {
        "origin": ORIGIN,
        "subject_id": "subject-member",
        "workspace_id": "workspace-alpha",
        "page_id": "page-shared",
        "client_id": "client-member",
        "session_binding_sha256": session_binding_sha256("session-member"),
        "now_ms": 1_001,
    }
    with pytest.raises(ConnectionCapacityExceeded):
        gateway.connect(member_ticket.token, **values)
    gateway.disconnect(owner.connection_id)
    member = gateway.connect(member_ticket.token, **{**values, "now_ms": 1_002})
    assert gateway.active_connection_count() == 1
    assert member.connection_id.startswith("conn_")


@pytest.mark.fault
def test_t0a_p2_gateway_stop_purges_all_queues_and_fails_closed() -> None:
    gateway, tickets, _, resolver = _gateway()
    resolver.allow("subject-owner")
    issued, session = _connect(gateway, tickets, resolver)
    gateway.submit(
        session.connection_id,
        _envelope(
            issued,
            operation_id="op-gateway-stop-0001",
            client_sequence=1,
            acknowledgement_sequence=0,
            payload=b"protected",
        ),
    )
    gateway.stop()
    assert gateway.session_status(session.connection_id)["queued_frames"] == 0
    assert gateway.session_status(session.connection_id)["close_reason"] == "gateway_stopped"
    with pytest.raises(GatewayUnavailable):
        gateway.drain(session.connection_id, max_frames=1)


@pytest.mark.unit
def test_t0a_p2_gateway_verifier_rejects_backpressure_unavailable_and_false_green_drift(
    tmp_path: Path,
) -> None:
    gateway = copy.deepcopy(load_json(DEFAULT_GATEWAY))
    gateway["admission_contract"]["stale_epoch_allowed"] = True
    gateway["queue_and_backpressure_contract"]["silent_durable_drop_allowed"] = True
    gateway["reconnect_contract"]["maximum_attempts_per_window_default"] = 0
    gateway["gateway_unavailable_contract"]["availability_never_substitutes_for_authorization"] = False
    gateway["prototype_boundary"]["os_port_bound"] = True
    gateway["claims"]["actual_network_gateway_implemented"] = True
    path = tmp_path / "gateway-drift.json"
    path.write_text(json.dumps(gateway, ensure_ascii=False), encoding="utf-8")
    report, exit_code = verify_gateway_contract(gateway_path=path)
    assert exit_code == 1
    assert {
        "ADMISSION_BOUNDARY_DRIFT",
        "BACKPRESSURE_CONTRACT_DRIFT",
        "RECONNECT_CONTRACT_DRIFT",
        "UNAVAILABLE_CONTRACT_DRIFT",
        "PROTOTYPE_FALSE_PRODUCTION",
        "CONTRACT_FALSE_GREEN",
    }.issubset(_codes(report))
    assert any(code.startswith("GATEWAY_SCHEMA_") for code in _codes(report))


@pytest.mark.unit
def test_t0a_p2_gateway_verifier_cli_passes_without_binding_a_port() -> None:
    root = Path(__file__).resolve().parents[3]
    run = subprocess.run(
        [
            sys.executable,
            "-E",
            "-m",
            "code_assist_v1.tools.verify_internal_collaborative_gateway_contract",
            "--compact",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads(run.stdout)
    assert report["status"] == "pass"
    assert report["summary"]["actual_network_gateway_implemented"] is False
