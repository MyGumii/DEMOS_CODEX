from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
from dataclasses import asdict
from pathlib import Path
import hashlib
import threading
import time

import pytest

from code_assist_v1.notion_collaboration.q0_validation import (
    FROZEN_PRODUCT_DECISION_SHA256,
    Q0_REPORT_CONTRACT,
    REQUIRED_FAULT_POINT_IDS,
    REQUIRED_OBSERVABILITY_SIGNAL_IDS,
    REQUIRED_SECURITY_CONTROL_IDS,
    load_frozen_scale_contract,
    target_realm_attestation,
    validate_q0_report,
    write_q0_report_create_only,
)
from code_assist_v1.notion_collaboration.runtime_gateway import (
    ConnectionCapacityExceeded,
)
from code_assist_v1.tests.notion_collaboration.q0_support import (
    PAGE_ID,
    build_q0_environment,
    upload_one,
)


def _metric(snapshot: dict, section: str, name: str) -> dict:
    rows = [item for item in snapshot[section] if item["name"] == name]
    return max(rows, key=lambda item: item.get("count", 0))


@pytest.mark.unit
def test_q0_frozen_scale_and_local_realm_cannot_be_promoted_as_target_evidence(
    tmp_path: Path,
) -> None:
    scale = load_frozen_scale_contract()
    assert asdict(scale) == {
        "registered_users": 100,
        "simultaneous_connections": 30,
        "simultaneous_editors_per_page": 10,
        "pages_per_workspace": 10_000,
        "blocks_per_page": 5_000,
        "rows_per_database": 100_000,
        "sustained_operations_per_second": 30,
        "burst_operations_per_second": 100,
        "attachment_concurrency": 10,
        "minimum_free_bytes": 21_474_836_480,
        "minimum_free_percent": 20,
    }
    realm = target_realm_attestation({})
    report = {
        "contract": Q0_REPORT_CONTRACT,
        "product_decision_sha256": FROZEN_PRODUCT_DECISION_SHA256,
        "scale_contract": asdict(scale),
        "realm": realm,
        "observed_denominators": {
            "registered_users": 100,
            "simultaneous_connections": 30,
            "simultaneous_editors_per_page": 10,
            "sustained_operations_per_second": 30.0,
            "burst_operations_per_second": 100.0,
            "attachment_concurrency": 10,
        },
        "performance": {
            "ack_latency_ms": {
                "count": 100,
                "minimum": 1.0,
                "p50": 2.0,
                "p95": 3.0,
                "p99": 4.0,
                "maximum": 5.0,
            },
            "sqlite_busy_count": 0,
            "queue_lag_ms": 0.0,
            "projection_lag_ms": 0.0,
            "memory_peak_bytes": 1,
            "compaction_age_seconds": 0,
        },
        "reliability": {
            "acknowledged_operation_loss_count": 0,
            "convergence_mismatch_count": 0,
            "post_revoke_protected_delivery_count": 0,
            "unauthorized_upload_read_count": 0,
        },
        "fault_point_ids": sorted(REQUIRED_FAULT_POINT_IDS),
        "security_control_ids": sorted(REQUIRED_SECURITY_CONTROL_IDS),
        "observability_signal_ids": sorted(REQUIRED_OBSERVABILITY_SIGNAL_IDS),
    }
    validated = validate_q0_report(report, require_target_realm=False)
    assert validated["denominator_floor_passed"] is True
    assert validated["rate_floor_passed"] is True
    assert validated["realm_attestation_passed"] is False
    assert validated["target_gate_eligible"] is False
    with pytest.raises(ValueError, match="not eligible"):
        validate_q0_report(report, require_target_realm=True)

    target_report = copy.deepcopy(report)
    target_report["realm"] = target_realm_attestation(
        {
            "CODE_ASSIST_Q0_TARGET_LOAD": "1",
            "CODE_ASSIST_Q0_RUN_ID": "q0-unit-attestation",
            "CODE_ASSIST_Q0_SYNTHETIC_ROOT_SHA256": "sha256:" + "a" * 64,
        }
    )
    assert validate_q0_report(
        target_report, require_target_realm=True
    )["target_gate_eligible"] is True
    destination = tmp_path / "report.json"
    written_sha256 = write_q0_report_create_only(destination, target_report)
    assert written_sha256.startswith("sha256:")
    with pytest.raises(ValueError, match="already exists"):
        write_q0_report_create_only(destination, target_report)


@pytest.mark.integration
@pytest.mark.concurrency
@pytest.mark.load
@pytest.mark.scenario("PERF-001")
def test_q0_exact_100_registered_30_connected_10_same_page_editors(
    tmp_path: Path,
) -> None:
    environment = build_q0_environment(
        tmp_path / "target-concurrency",
        registered_users=100,
        max_connections=30,
        max_queue_frames=4_096,
    )
    sessions = [
        environment.connect(
            subject,
            client_id=f"load-client-{index:03d}",
            binding_secret=f"load-binding-{index:03d}",
            now_ms=10_000 + index,
        )
        for index, subject in enumerate(environment.subjects[1:31], start=1)
    ]
    assert len(environment.subjects) == 100
    assert environment.gateway.active_connection_count() == 30

    capacity_ticket, capacity_binding = environment.issue_ticket(
        environment.subjects[31],
        client_id="load-client-capacity",
        binding_secret="load-binding-capacity",
        now_ms=20_000,
    )
    with pytest.raises(ConnectionCapacityExceeded):
        environment.gateway.connect(
            capacity_ticket.token,
            origin="http://127.0.0.1:49400",
            subject_id=environment.subjects[31],
            workspace_id=environment.workspace_id,
            page_id=PAGE_ID,
            client_id="load-client-capacity",
            session_binding_sha256=capacity_binding,
            now_ms=20_001,
        )
    capacity_row = next(
        item
        for item in environment.transport.ticket_rows()
        if item["jti"] == capacity_ticket.claims.jti
    )
    assert capacity_row["consumed_at_ms"] is None

    editors = sessions[:10]
    initial_sequence = environment.gateway.authority.binding(
        environment.subjects[1], PAGE_ID, minimum_role="edit"
    ).document_sequence

    def write_editor(editor_index: int) -> list[tuple[int, str]]:
        session = editors[editor_index]
        receipts: list[tuple[int, str]] = []
        for sequence in range(1, 11):
            started = time.perf_counter()
            receipt = environment.gateway.submit_payload(
                session.connection_id,
                operation_id=f"op-q0-burst-{editor_index:02d}-{sequence:04d}",
                client_sequence=sequence,
                acknowledgement_sequence=0,
                payload=f"editor-{editor_index:02d}-update-{sequence:04d}".encode(),
            )
            assert time.perf_counter() >= started
            assert receipt.acknowledgement.durable is True
            receipts.append(
                (
                    receipt.acknowledgement.document_sequence,
                    receipt.acknowledgement.event_sha256,
                )
            )
        return receipts

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=10) as pool:
        receipts = [item for group in pool.map(write_editor, range(10)) for item in group]
    elapsed = time.perf_counter() - started
    assert len(receipts) == 100
    assert sorted(item[0] for item in receipts) == list(
        range(initial_sequence + 1, initial_sequence + 101)
    )
    assert len({item[1] for item in receipts}) == 100

    converged: list[str] = []
    for session in sessions:
        frames = environment.gateway.drain(session.connection_id, max_frames=256)
        assert len(frames) == 100
        ordered = sorted(frames, key=lambda frame: int(frame.document_sequence or 0))
        digest = hashlib.sha256()
        for frame in ordered:
            digest.update(str(frame.document_sequence).encode())
            digest.update(frame.payload_sha256.encode())
        converged.append(digest.hexdigest())
    assert len(set(converged)) == 1

    snapshot = environment.gateway.metrics_snapshot()
    latency = _metric(snapshot, "histograms", "ACK_latency_ms")
    assert latency["count"] == 100
    assert latency["minimum"] <= latency["p50"] <= latency["p95"] <= latency["p99"] <= latency["maximum"]
    assert _metric(snapshot, "gauges", "process_RSS_bytes")["value"] > 0
    assert _metric(snapshot, "gauges", "durable_event_tail_length")["value"] == 100
    assert environment.transport.counts()["workspace_collaboration_crdt_updates"] == 100

    # This records a local observation only.  The target runner is responsible
    # for enforcing the 30/s sustained and 100/s burst rate floors.
    burst_rate = 100 / max(elapsed, 1e-9)
    assert burst_rate > 0
    if target_realm_attestation()["target_realm_attested"]:
        assert burst_rate >= load_frozen_scale_contract().burst_operations_per_second
    environment.gateway.stop()


@pytest.mark.integration
@pytest.mark.concurrency
@pytest.mark.load
def test_q0_ten_attachment_streams_commit_without_false_source_fence(
    tmp_path: Path,
) -> None:
    environment = build_q0_environment(
        tmp_path / "attachment-concurrency",
        registered_users=11,
        through_file_authority=True,
    )
    assert environment.files is not None
    authority = environment.files
    staged = threading.Barrier(10)

    def upload(index: int) -> dict:
        payload = (f"attachment-{index:02d}-".encode() * 4_096)[:65_536]
        return upload_one(
            authority,
            actor=environment.subjects[index + 1],
            payload=payload,
            operation_suffix=f"{index:04d}",
            before_finalize=staged.wait,
        )

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(upload, range(10)))
    assert len({item["file_id"] for item in results}) == 10
    assert all(item["state"] == "active" for item in results)
    listed = authority.list_files("owner", PAGE_ID)
    assert len(listed["files"]) == 10
    assert len({item["content_sha256"] for item in listed["files"]}) == 10
    environment.gateway.stop()
