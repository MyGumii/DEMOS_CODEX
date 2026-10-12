from __future__ import annotations

import copy

import pytest

from code_assist_v1.notion_collaboration.selected_runtime_contract import (
    COMPACTION_REQUEST_CONTRACT,
    COMPACTION_RESPONSE_CONTRACT,
    HEALTH_CONTRACT,
    SelectedRuntimeContractError,
    build_compaction_request,
    build_health_report,
    build_startup_attestation,
    runtime_binding_sha256,
    validate_checked_in_binding,
    validate_compaction_response,
)


EXPECTED_BINDING = "sha256:99b8e01015bbf92c385493bebbc08b6600add4b43afd2ab6e1746a6bb8e74057"


@pytest.mark.unit
def test_t0a_p3_checked_in_runtime_binding_is_exact_and_stable() -> None:
    report = validate_checked_in_binding()
    assert report["runtime_binding_sha256"] == EXPECTED_BINDING
    assert runtime_binding_sha256() == EXPECTED_BINDING
    assert report["descriptor"]["candidate_id"] == "PY_OPAQUE_NODE_COMPACTOR"
    assert report["descriptor"]["wire_format"] == "Yjs_v1_update_bytes"
    assert len(report["authority_hashes"]) == 6


@pytest.mark.unit
def test_t0a_p3_startup_attestation_binds_owned_pids_port_and_initial_not_ready() -> None:
    attestation = build_startup_attestation(
        deployment_generation_id="generation-1",
        gateway_instance_id="gateway-1",
        schema_compatibility="compatible",
        gateway_pid=101,
        compactor_pid=102,
        listener_port=49152,
    )
    assert attestation["runtime_binding_sha256"] == EXPECTED_BINDING
    assert attestation["listener_owner"] == "python_gateway"
    assert attestation["compactor_listener_allowed"] is False
    assert attestation["initial_state"] == "NOT_READY"
    with pytest.raises(SelectedRuntimeContractError, match="PID or port"):
        build_startup_attestation(
            deployment_generation_id="generation-1",
            gateway_instance_id="gateway-1",
            schema_compatibility="compatible",
            gateway_pid=0,
            compactor_pid=102,
            listener_port=49152,
        )


@pytest.mark.unit
def test_t0a_p3_health_is_fail_closed_and_compactor_degradation_admits_no_new_session() -> None:
    ready = build_health_report(
        state="READY",
        aggregate_enabled=True,
        realtime_enabled=True,
        authority_available=True,
        schema_compatible=True,
        runtime_binding_matches=True,
        ownership_attestation_matches=True,
        compactor_ready=True,
        queue_pressure_state="normal",
    )
    assert ready["contract"] == HEALTH_CONTRACT
    assert ready["ready_for_admission"] is True
    assert ready["ready_for_realtime"] is True
    assert ready["ready_for_compaction"] is True

    degraded = build_health_report(
        state="READY",
        aggregate_enabled=True,
        realtime_enabled=True,
        authority_available=True,
        schema_compatible=True,
        runtime_binding_matches=True,
        ownership_attestation_matches=True,
        compactor_ready=False,
        queue_pressure_state="warning",
        previously_ready_session=True,
    )
    assert degraded["ready_for_admission"] is False
    assert degraded["ready_for_realtime"] is True
    assert degraded["ready_for_compaction"] is False

    stale = build_health_report(
        state="READY",
        aggregate_enabled=True,
        realtime_enabled=True,
        authority_available=False,
        schema_compatible=True,
        runtime_binding_matches=True,
        ownership_attestation_matches=True,
        compactor_ready=True,
        queue_pressure_state="normal",
    )
    assert stale["ready_for_admission"] is False
    assert stale["ready_for_realtime"] is False
    assert stale["authority_available"] is False


@pytest.mark.unit
def test_t0a_p3_compaction_request_binds_contiguous_high_water_epochs_and_update_hashes() -> None:
    hashes = ["sha256:" + character * 64 for character in ("1", "2", "3")]
    request = build_compaction_request(
        request_id="request-1",
        document_id="document-1",
        workspace_epoch=4,
        page_epoch=7,
        base_snapshot_id="snapshot-10",
        base_high_water_sequence=10,
        target_high_water_sequence=13,
        ordered_update_bytes=8192,
        ordered_update_sha256=hashes,
    )
    assert request["contract"] == COMPACTION_REQUEST_CONTRACT
    assert request["ordered_update_count"] == 3
    assert request["ordered_update_sha256"] == hashes
    assert request["runtime_binding_sha256"] == EXPECTED_BINDING
    assert request["request_sha256"].startswith("sha256:")
    with pytest.raises(SelectedRuntimeContractError, match="contiguous"):
        build_compaction_request(
            request_id="request-1",
            document_id="document-1",
            workspace_epoch=4,
            page_epoch=7,
            base_snapshot_id="snapshot-10",
            base_high_water_sequence=10,
            target_high_water_sequence=14,
            ordered_update_bytes=8192,
            ordered_update_sha256=hashes,
        )


@pytest.mark.unit
def test_t0a_p3_compaction_response_rejects_binding_or_count_tamper() -> None:
    request = build_compaction_request(
        request_id="request-2",
        document_id="document-2",
        workspace_epoch=2,
        page_epoch=3,
        base_snapshot_id=None,
        base_high_water_sequence=0,
        target_high_water_sequence=1,
        ordered_update_bytes=100,
        ordered_update_sha256=["sha256:" + "a" * 64],
    )
    response = {
        "contract": COMPACTION_RESPONSE_CONTRACT,
        "request_id": request["request_id"],
        "request_sha256": request["request_sha256"],
        "base_high_water_sequence": 0,
        "target_high_water_sequence": 1,
        "applied_update_count": 1,
        "snapshot_bytes": 80,
        "snapshot_sha256": "sha256:" + "b" * 64,
        "state_vector_bytes": 12,
        "state_vector_sha256": "sha256:" + "c" * 64,
        "runtime_binding_sha256": EXPECTED_BINDING,
    }
    assert validate_compaction_response(response, request=request) == response
    tampered = copy.deepcopy(response)
    tampered["applied_update_count"] = 2
    with pytest.raises(SelectedRuntimeContractError, match="binding drift"):
        validate_compaction_response(tampered, request=request)
