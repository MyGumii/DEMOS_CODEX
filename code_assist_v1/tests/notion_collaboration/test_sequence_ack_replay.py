from __future__ import annotations

from dataclasses import replace

import pytest

from code_assist_v1.notion_collaboration.protocol_contract import (
    AcknowledgementConflict,
    EnvelopeRejected,
    InMemorySequenceLedger,
    InjectedProtocolFailure,
    MAX_REPLAY_BATCH,
    OperationConflict,
    ProtocolEnvelope,
    SequenceGap,
    StaleEpoch,
    TicketAuthority,
    TicketGrant,
    session_binding_sha256,
)


ORIGIN = "http://127.0.0.1:10112"


def _grant(
    *,
    subject_id: str = "subject-owner",
    client_id: str = "client-browser-a",
    session_secret: str = "session-owner-a",
) -> TicketGrant:
    authority = TicketAuthority()
    binding = session_binding_sha256(session_secret)
    issued = authority.issue(
        csrf_valid=True,
        subject_id=subject_id,
        workspace_id="workspace-alpha",
        page_id="page-shared",
        client_id=client_id,
        session_binding_sha256=binding,
        origin=ORIGIN,
        capabilities=("comment", "presence", "read", "write"),
        workspace_epoch=4,
        page_epoch=7,
        now_ms=1_000,
        ttl_ms=10_000,
    )
    return authority.consume(
        issued.token,
        origin=ORIGIN,
        subject_id=subject_id,
        workspace_id="workspace-alpha",
        page_id="page-shared",
        client_id=client_id,
        session_binding_sha256=binding,
        current_workspace_epoch=4,
        current_page_epoch=7,
        now_ms=1_001,
    )


def _envelope(
    grant: TicketGrant,
    *,
    operation_id: str,
    client_sequence: int,
    acknowledgement_sequence: int,
    payload: bytes,
) -> ProtocolEnvelope:
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


def _accept(
    ledger: InMemorySequenceLedger,
    envelope: ProtocolEnvelope,
    grant: TicketGrant,
    *,
    fault: str | None = None,
):
    return ledger.accept(
        envelope,
        grant,
        current_workspace_epoch=4,
        current_page_epoch=7,
        fault=fault,
    )


@pytest.mark.integration
def test_t0a_p1_document_sequence_is_monotonic_and_ack_follows_durable_event() -> None:
    ledger = InMemorySequenceLedger()
    grant = _grant()
    first = _accept(
        ledger,
        _envelope(
            grant,
            operation_id="op-sequence-first-0001",
            client_sequence=1,
            acknowledgement_sequence=0,
            payload=b'{"insert":"A"}',
        ),
        grant,
    )
    second = _accept(
        ledger,
        _envelope(
            grant,
            operation_id="op-sequence-second-0002",
            client_sequence=2,
            acknowledgement_sequence=first.document_sequence,
            payload=b'{"insert":"B"}',
        ),
        grant,
    )
    assert (first.document_sequence, second.document_sequence) == (1, 2)
    assert first.durable is second.durable is True
    assert ledger.high_water("workspace-alpha", "page-shared") == 2
    assert ledger.event_count("workspace-alpha", "page-shared") == 2
    assert ledger.next_client_sequence(grant) == 3


@pytest.mark.integration
def test_t0a_p1_exact_operation_retry_returns_original_ack_without_duplicate_event() -> None:
    ledger = InMemorySequenceLedger()
    grant = _grant()
    envelope = _envelope(
        grant,
        operation_id="op-deduplicate-exact-0001",
        client_sequence=1,
        acknowledgement_sequence=0,
        payload=b'{"insert":"once"}',
    )
    first = _accept(ledger, envelope, grant)
    replay = _accept(ledger, envelope, grant)
    assert replay == first
    assert ledger.event_count("workspace-alpha", "page-shared") == 1
    assert ledger.high_water("workspace-alpha", "page-shared") == 1


@pytest.mark.integration
def test_t0a_p1_operation_id_reuse_with_different_request_conflicts() -> None:
    ledger = InMemorySequenceLedger()
    grant = _grant()
    original = _envelope(
        grant,
        operation_id="op-deduplicate-conflict-0001",
        client_sequence=1,
        acknowledgement_sequence=0,
        payload=b'{"insert":"one"}',
    )
    _accept(ledger, original, grant)
    changed = _envelope(
        grant,
        operation_id=original.operation_id,
        client_sequence=1,
        acknowledgement_sequence=0,
        payload=b'{"insert":"different"}',
    )
    with pytest.raises(OperationConflict):
        _accept(ledger, changed, grant)
    assert ledger.event_count("workspace-alpha", "page-shared") == 1


@pytest.mark.integration
def test_t0a_p1_client_sequence_gap_repeat_and_reuse_fail_closed() -> None:
    ledger = InMemorySequenceLedger()
    grant = _grant()
    gap = _envelope(
        grant,
        operation_id="op-client-gap-0003",
        client_sequence=3,
        acknowledgement_sequence=0,
        payload=b"gap",
    )
    with pytest.raises(SequenceGap) as missing:
        _accept(ledger, gap, grant)
    assert missing.value.expected_sequence == 1

    first = _envelope(
        grant,
        operation_id="op-client-sequence-0001",
        client_sequence=1,
        acknowledgement_sequence=0,
        payload=b"first",
    )
    _accept(ledger, first, grant)
    reused = _envelope(
        grant,
        operation_id="op-client-sequence-other-0002",
        client_sequence=1,
        acknowledgement_sequence=0,
        payload=b"reuse",
    )
    with pytest.raises(SequenceGap) as repeated:
        _accept(ledger, reused, grant)
    assert repeated.value.expected_sequence == 2


@pytest.mark.integration
def test_t0a_p1_client_ack_cannot_exceed_or_move_behind_durable_high_water() -> None:
    ledger = InMemorySequenceLedger()
    grant = _grant()
    impossible = _envelope(
        grant,
        operation_id="op-ack-future-0001",
        client_sequence=1,
        acknowledgement_sequence=1,
        payload=b"future",
    )
    with pytest.raises(AcknowledgementConflict):
        _accept(ledger, impossible, grant)

    first_ack = _accept(
        ledger,
        replace(impossible, acknowledgement_sequence=0),
        grant,
    )
    second_ack = _accept(
        ledger,
        _envelope(
            grant,
            operation_id="op-ack-current-0002",
            client_sequence=2,
            acknowledgement_sequence=first_ack.document_sequence,
            payload=b"current",
        ),
        grant,
    )
    stale_ack = _envelope(
        grant,
        operation_id="op-ack-backwards-0003",
        client_sequence=3,
        acknowledgement_sequence=0,
        payload=b"backwards",
    )
    with pytest.raises(AcknowledgementConflict):
        _accept(ledger, stale_ack, grant)
    assert second_ack.document_sequence == 2
    assert ledger.event_count("workspace-alpha", "page-shared") == 2


@pytest.mark.fault
def test_t0a_p1_precommit_failure_emits_no_ack_event_or_sequence_and_can_retry() -> None:
    ledger = InMemorySequenceLedger()
    grant = _grant()
    envelope = _envelope(
        grant,
        operation_id="op-fault-before-commit-0001",
        client_sequence=1,
        acknowledgement_sequence=0,
        payload=b"atomic",
    )
    with pytest.raises(InjectedProtocolFailure, match="before_durable_commit"):
        _accept(ledger, envelope, grant, fault="before_durable_commit")
    assert ledger.event_count("workspace-alpha", "page-shared") == 0
    assert ledger.high_water("workspace-alpha", "page-shared") == 0
    assert ledger.next_client_sequence(grant) == 1
    recovered = _accept(ledger, envelope, grant)
    assert recovered.document_sequence == 1


@pytest.mark.fault
def test_t0a_p1_lost_ack_after_durable_commit_replays_original_ack_exactly_once() -> None:
    ledger = InMemorySequenceLedger()
    grant = _grant()
    envelope = _envelope(
        grant,
        operation_id="op-fault-lost-ack-0001",
        client_sequence=1,
        acknowledgement_sequence=0,
        payload=b"committed",
    )
    with pytest.raises(InjectedProtocolFailure, match="after_durable_commit_before_ack"):
        _accept(ledger, envelope, grant, fault="after_durable_commit_before_ack")
    assert ledger.event_count("workspace-alpha", "page-shared") == 1
    assert ledger.high_water("workspace-alpha", "page-shared") == 1
    recovered = _accept(ledger, envelope, grant)
    assert recovered.document_sequence == 1
    assert recovered.operation_id == envelope.operation_id
    assert ledger.event_count("workspace-alpha", "page-shared") == 1


@pytest.mark.integration
def test_t0a_p1_replay_is_exclusive_bounded_ordered_and_reports_high_water() -> None:
    ledger = InMemorySequenceLedger()
    grant = _grant()
    acknowledgement = 0
    for sequence in range(1, 4):
        ack = _accept(
            ledger,
            _envelope(
                grant,
                operation_id=f"op-replay-batch-{sequence:04d}",
                client_sequence=sequence,
                acknowledgement_sequence=acknowledgement,
                payload=f"event-{sequence}".encode(),
            ),
            grant,
        )
        acknowledgement = ack.document_sequence

    first = ledger.replay(
        grant,
        after_document_sequence=0,
        limit=2,
        current_workspace_epoch=4,
        current_page_epoch=7,
    )
    assert [event.document_sequence for event in first.events] == [1, 2]
    assert first.high_water_sequence == 3
    assert first.has_more is True
    tail = ledger.replay(
        grant,
        after_document_sequence=2,
        limit=2,
        current_workspace_epoch=4,
        current_page_epoch=7,
    )
    assert [event.document_sequence for event in tail.events] == [3]
    assert tail.has_more is False
    with pytest.raises(ValueError, match="batch exceeds"):
        ledger.replay(
            grant,
            after_document_sequence=0,
            limit=MAX_REPLAY_BATCH + 1,
            current_workspace_epoch=4,
            current_page_epoch=7,
        )
    with pytest.raises(AcknowledgementConflict):
        ledger.replay(
            grant,
            after_document_sequence=4,
            limit=1,
            current_workspace_epoch=4,
            current_page_epoch=7,
        )


@pytest.mark.integration
def test_t0a_p1_old_epoch_write_replay_and_queued_send_deliver_zero_protected_payload() -> None:
    ledger = InMemorySequenceLedger()
    grant = _grant()
    envelope = _envelope(
        grant,
        operation_id="op-stale-epoch-0001",
        client_sequence=1,
        acknowledgement_sequence=0,
        payload=b"protected",
    )
    ack = _accept(ledger, envelope, grant)
    batch = ledger.replay(
        grant,
        after_document_sequence=0,
        limit=1,
        current_workspace_epoch=4,
        current_page_epoch=7,
    )
    assert ack.document_sequence == batch.events[0].document_sequence == 1

    with pytest.raises(StaleEpoch):
        ledger.accept(
            _envelope(
                grant,
                operation_id="op-stale-epoch-write-0002",
                client_sequence=2,
                acknowledgement_sequence=1,
                payload=b"must-not-commit",
            ),
            grant,
            current_workspace_epoch=5,
            current_page_epoch=8,
        )
    with pytest.raises(StaleEpoch):
        ledger.replay(
            grant,
            after_document_sequence=0,
            limit=1,
            current_workspace_epoch=5,
            current_page_epoch=8,
        )
    with pytest.raises(StaleEpoch):
        ledger.authorize_send(
            batch.events[0],
            grant,
            current_workspace_epoch=5,
            current_page_epoch=8,
        )
    assert ledger.event_count("workspace-alpha", "page-shared") == 1


@pytest.mark.integration
def test_t0a_p1_client_sequence_is_per_binding_while_document_sequence_is_shared() -> None:
    ledger = InMemorySequenceLedger()
    owner = _grant()
    member = _grant(
        subject_id="subject-member",
        client_id="client-browser-b",
        session_secret="session-member-b",
    )
    owner_ack = _accept(
        ledger,
        _envelope(
            owner,
            operation_id="op-owner-shared-page-0001",
            client_sequence=1,
            acknowledgement_sequence=0,
            payload=b"owner",
        ),
        owner,
    )
    member_ack = _accept(
        ledger,
        _envelope(
            member,
            operation_id="op-member-shared-page-0001",
            client_sequence=1,
            acknowledgement_sequence=owner_ack.document_sequence,
            payload=b"member",
        ),
        member,
    )
    assert owner_ack.document_sequence == 1
    assert member_ack.document_sequence == 2
    assert ledger.next_client_sequence(owner) == 2
    assert ledger.next_client_sequence(member) == 2


@pytest.mark.unit
def test_t0a_p1_non_durable_presence_cannot_enter_durable_sequence_ledger() -> None:
    ledger = InMemorySequenceLedger()
    grant = _grant()
    presence = ProtocolEnvelope.create(
        grant,
        message_type="presence.update",
        operation_id="op-presence-ephemeral-0001",
        client_sequence=1,
        acknowledgement_sequence=0,
        payload=b"online",
        capabilities=("presence",),
    )
    with pytest.raises(EnvelopeRejected, match="not a durable mutation"):
        _accept(ledger, presence, grant)
    assert ledger.event_count("workspace-alpha", "page-shared") == 0
