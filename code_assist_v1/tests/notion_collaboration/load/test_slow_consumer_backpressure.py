from __future__ import annotations

from pathlib import Path
import time

import pytest

from code_assist_v1.notion_collaboration.q0_validation import nearest_rank_distribution
from code_assist_v1.tests.notion_collaboration.q0_support import (
    PAGE_ID,
    build_q0_environment,
)


def _counter(snapshot: dict, name: str) -> int:
    return sum(
        int(item["value"])
        for item in snapshot["counters"]
        if item["name"] == name
    )


@pytest.mark.integration
@pytest.mark.concurrency
@pytest.mark.load
@pytest.mark.scenario("PERF-001")
def test_q0_slow_consumer_is_fenced_after_commit_and_replays_every_ack(
    tmp_path: Path,
) -> None:
    environment = build_q0_environment(
        tmp_path / "slow-consumer",
        registered_users=3,
        max_queue_frames=4,
        max_queue_bytes=4_096,
    )
    writer = environment.connect(
        environment.subjects[1],
        client_id="backpressure-writer",
        binding_secret="backpressure-writer-binding",
        now_ms=1_000,
    )
    slow = environment.connect(
        environment.subjects[2],
        client_id="backpressure-slow",
        binding_secret="backpressure-slow-binding",
        now_ms=2_000,
    )
    base_sequence = environment.gateway.authority.binding(
        environment.subjects[1], PAGE_ID, minimum_role="edit"
    ).document_sequence
    commit_latencies: list[float] = []
    first_queued_at = time.perf_counter()
    acknowledgements = []
    for sequence in range(1, 21):
        started = time.perf_counter()
        receipt = environment.gateway.submit_payload(
            writer.connection_id,
            operation_id=f"op-q0-backpressure-{sequence:04d}",
            client_sequence=sequence,
            acknowledgement_sequence=0,
            payload=f"durable-{sequence:04d}".encode(),
        )
        commit_latencies.append((time.perf_counter() - started) * 1_000)
        acknowledgements.append(receipt.acknowledgement)
        assert receipt.acknowledgement.durable is True
        assert len(environment.gateway.drain(writer.connection_id, max_frames=4)) == 1
        if sequence < 5:
            assert receipt.broadcast.backpressured_sessions == 0
        elif sequence == 5:
            assert receipt.broadcast.backpressured_sessions == 1

    assert [item.document_sequence for item in acknowledgements] == list(
        range(base_sequence + 1, base_sequence + 21)
    )
    assert environment.gateway.session_status(slow.connection_id) == {
        "connection_id": slow.connection_id,
        "active": False,
        "close_reason": "backpressure_exceeded",
        "queued_frames": 0,
        "queued_payload_bytes": 0,
        "last_queued_document_sequence": None,
    }
    assert environment.transport.counts()["workspace_collaboration_crdt_updates"] == 20

    replay = environment.connect(
        environment.subjects[2],
        client_id="backpressure-replay",
        binding_secret="backpressure-replay-binding",
        now_ms=20_000,
    )
    after = base_sequence
    replayed = []
    while len(replayed) < 20:
        receipt = environment.gateway.replay_to_queue(
            replay.connection_id,
            after_document_sequence=after,
            limit=4,
        )
        frames = environment.gateway.drain(replay.connection_id, max_frames=4)
        assert 1 <= len(frames) <= 4
        replayed.extend(frames)
        after = int(frames[-1].document_sequence or 0)
        if not receipt.has_more:
            break
    queue_lag_ms = (time.perf_counter() - first_queued_at) * 1_000
    assert [frame.document_sequence for frame in replayed] == [
        item.document_sequence for item in acknowledgements
    ]
    assert [frame.operation_id for frame in replayed] == [
        item.operation_id for item in acknowledgements
    ]
    assert nearest_rank_distribution(commit_latencies)["count"] == 20
    assert queue_lag_ms > 0

    snapshot = environment.gateway.metrics_snapshot()
    assert _counter(snapshot, "queue_overflow_disconnect_total") == 1
    assert next(
        item["value"]
        for item in snapshot["gauges"]
        if item["name"] == "outbound_queue_frames"
    ) == 0
    environment.gateway.stop()
