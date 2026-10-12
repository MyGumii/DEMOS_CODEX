from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time

import pytest

from code_assist_v1.notion_collaboration.q0_validation import (
    load_frozen_scale_contract,
    nearest_rank_distribution,
    target_realm_attestation,
)
from code_assist_v1.notion_collaboration.selected_runtime_contract import (
    COMPACTION_RESPONSE_CONTRACT,
    runtime_binding_sha256,
)
from code_assist_v1.tests.notion_collaboration.q0_support import (
    ORIGIN,
    PAGE_ID,
    build_q0_environment,
)


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


@pytest.mark.integration
@pytest.mark.load
@pytest.mark.fault
@pytest.mark.scenario("PERF-001")
@pytest.mark.scenario("OPS-005")
def test_q0_sustained_soak_survives_restarts_compacts_and_converges(
    tmp_path: Path,
) -> None:
    scale = load_frozen_scale_contract()
    realm = target_realm_attestation()
    target_run = realm["target_realm_attested"] is True
    if target_run:
        soak_seconds = int(os.environ.get("CODE_ASSIST_Q0_SOAK_SECONDS", "60"))
        assert soak_seconds >= 60
        operation_count = max(120, scale.sustained_operations_per_second * soak_seconds)
        scheduled_rate = scale.sustained_operations_per_second + 2
    else:
        soak_seconds = 0
        operation_count = 120
        scheduled_rate = 0

    environment = build_q0_environment(
        tmp_path / "long-soak",
        registered_users=4,
        max_queue_frames=256,
    )
    identities = environment.subjects[1:4]
    clients = [f"soak-client-{index}" for index in range(3)]
    bindings = [f"soak-binding-{index}" for index in range(3)]
    sessions = [
        environment.connect(
            subject,
            client_id=clients[index],
            binding_secret=bindings[index],
            now_ms=1_000 + index,
        )
        for index, subject in enumerate(identities)
    ]
    base_sequence = environment.gateway.authority.binding(
        identities[0], PAGE_ID, minimum_role="edit"
    ).document_sequence
    client_sequences = [0, 0, 0]
    latencies: list[float] = []
    memory_samples: list[int] = []
    acknowledged_operations: list[str] = []
    run_started = time.perf_counter()

    for index in range(operation_count):
        if target_run:
            deadline = run_started + index / scheduled_rate
            remaining = deadline - time.perf_counter()
            if remaining > 0:
                time.sleep(remaining)
        editor = index % 3
        client_sequences[editor] += 1
        operation_id = f"op-q0-soak-{index:06d}"
        started = time.perf_counter()
        receipt = environment.gateway.submit_payload(
            sessions[editor].connection_id,
            operation_id=operation_id,
            client_sequence=client_sequences[editor],
            acknowledgement_sequence=0,
            payload=f"soak-update-{index:06d}".encode(),
        )
        latencies.append((time.perf_counter() - started) * 1_000)
        acknowledged_operations.append(receipt.acknowledgement.operation_id)
        for session in sessions:
            frames = environment.gateway.drain(session.connection_id, max_frames=8)
            assert len(frames) == 1
            assert frames[0].operation_id == operation_id

        if (index + 1) % 40 == 0 and index + 1 < operation_count:
            memory_snapshot = environment.gateway.metrics_snapshot()
            memory_samples.append(
                int(
                    next(
                        item["value"]
                        for item in memory_snapshot["gauges"]
                        if item["name"] == "process_RSS_bytes"
                    )
                )
            )
            environment.gateway.stop()
            health = environment.gateway.start(compactor_handshake_matches=True)
            assert health["ready_for_admission"] is True
            sessions = [
                environment.connect(
                    subject,
                    client_id=clients[client_index],
                    binding_secret=bindings[client_index],
                    now_ms=30_000 + index * 100 + client_index,
                )
                for client_index, subject in enumerate(identities)
            ]

    elapsed = time.perf_counter() - run_started
    assert len(acknowledged_operations) == operation_count
    assert len(set(acknowledged_operations)) == operation_count
    assert environment.transport.counts()["workspace_collaboration_crdt_updates"] == operation_count
    assert environment.gateway.authority.binding(
        identities[0], PAGE_ID, minimum_role="edit"
    ).document_sequence == base_sequence + operation_count
    latency = nearest_rank_distribution(latencies)
    assert latency["minimum"] <= latency["p50"] <= latency["p95"] <= latency["p99"] <= latency["maximum"]
    sustained_rate = operation_count / max(elapsed, 1e-9)
    if target_run:
        assert sustained_rate >= scale.sustained_operations_per_second

    ticket, binding = environment.issue_ticket(
        identities[0],
        client_id="soak-compactor",
        binding_secret="soak-compactor-binding",
        now_ms=900_000,
    )
    grant = environment.transport.consume_ticket(
        ticket.token,
        origin=ORIGIN,
        subject_id=identities[0],
        workspace_id=environment.workspace_id,
        document_id=PAGE_ID,
        client_id="soak-compactor",
        session_binding_sha256=binding,
        now_ms=900_001,
    )
    work = environment.transport.prepare_compaction(grant)
    snapshot_bytes = b"q0-soak-compacted-snapshot"
    state_vector = b"q0-soak-state-vector"
    response = {
        "contract": COMPACTION_RESPONSE_CONTRACT,
        "request_id": work.request["request_id"],
        "request_sha256": work.request["request_sha256"],
        "base_high_water_sequence": work.request["base_high_water_sequence"],
        "target_high_water_sequence": work.request["target_high_water_sequence"],
        "applied_update_count": len(work.ordered_updates),
        "snapshot_bytes": len(snapshot_bytes),
        "snapshot_sha256": _digest(snapshot_bytes),
        "state_vector_bytes": len(state_vector),
        "state_vector_sha256": _digest(state_vector),
        "runtime_binding_sha256": runtime_binding_sha256(),
    }
    compact_started = time.perf_counter()
    environment.transport.commit_compaction(
        grant,
        operation_id="op-q0-soak-compaction-0001",
        work=work,
        response=response,
        snapshot=snapshot_bytes,
        state_vector=state_vector,
    )
    environment.gateway.record_compaction_result(
        result="committed",
        duration_ms=(time.perf_counter() - compact_started) * 1_000,
        snapshot_bytes=len(snapshot_bytes),
    )
    final_metrics = environment.gateway.metrics_snapshot()
    memory_samples.append(
        int(
            next(
                item["value"]
                for item in final_metrics["gauges"]
                if item["name"] == "process_RSS_bytes"
            )
        )
    )
    assert min(memory_samples) > 0
    gauges = {item["name"]: item["value"] for item in final_metrics["gauges"]}
    assert gauges["durable_event_tail_length"] == 0
    assert gauges["compaction_lag_sequences"] == 0
    assert gauges["oldest_uncompacted_event_age_seconds"] == 0
    counters = {
        (item["name"], item["labels"].get("result_code")): item["value"]
        for item in final_metrics["counters"]
    }
    assert counters[("gateway_restart_total", None)] >= 2
    assert counters[("compaction_request_total_by_result", "committed")] == 1
    environment.gateway.stop()
