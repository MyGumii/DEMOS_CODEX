from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.notion_collaboration.selected_runtime_contract import (
    COMPACTION_REQUEST_CONTRACT,
    COMPACTION_RESPONSE_CONTRACT,
    LIFECYCLE_STATES,
    runtime_binding_sha256,
)
from code_assist_v1.tools.verify_internal_collaborative_selected_runtime import ADR, verify


PACKAGE_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_t0a_p3_topology_verifier_passes_with_incomplete_product_truth() -> None:
    report = verify()
    assert report["status"] == "pass"
    assert report["error_count"] == 0
    assert report["runtime_binding_sha256"] == runtime_binding_sha256()
    assert report["claims"] == {
        "topology_contract_complete": True,
        "actual_gateway_transport_implemented": False,
        "actual_compactor_service_implemented": False,
        "runtime_enabled": False,
        "product_complete": False,
    }


@pytest.mark.unit
def test_t0a_p3_topology_keeps_IAM_ACK_and_compaction_commit_in_canonical_python_authority() -> None:
    adr = json.loads(ADR.read_text(encoding="utf-8"))
    topology = adr["process_topology"]
    assert "authorization_policy" in topology["python_gateway"]["does_not_own"]
    assert "Yjs_update_interpretation" in topology["python_gateway"]["does_not_own"]
    assert "canonical_database_access" in topology["node_compactor"]["does_not_own"]
    assert "network_listener" in topology["node_compactor"]["does_not_own"]
    assert adr["ticket_epoch_ack_binding"]["canonical_commit_before_ACK"] is True
    assert adr["ticket_epoch_ack_binding"]["gateway_or_compactor_may_allocate_document_sequence"] is False
    assert adr["compaction_protocol"]["request_contract"] == COMPACTION_REQUEST_CONTRACT
    assert adr["compaction_protocol"]["response_contract"] == COMPACTION_RESPONSE_CONTRACT


@pytest.mark.unit
def test_t0a_p3_lifecycle_health_rollback_and_observability_denominators_are_complete() -> None:
    adr = json.loads(ADR.read_text(encoding="utf-8"))
    assert adr["lifecycle_contract"]["states"] == list(LIFECYCLE_STATES)
    assert len(adr["lifecycle_contract"]["start_order"]) == 8
    assert len(adr["lifecycle_contract"]["stop_order"]) == 6
    assert len(adr["health_contract"]["readiness_required_checks"]) == 8
    assert adr["rollback_and_kill_switch_contract"]["aggregate_flag_dominates"] is True
    assert adr["rollback_and_kill_switch_contract"]["rejected_runtime_fallback_allowed"] is False
    observability = adr["observability_contract"]
    assert len(observability["required_counters"]) == 11
    assert len(observability["required_gauges"]) == 6
    assert len(observability["required_histograms"]) == 7
    assert "raw_CRDT_update" in observability["forbidden_log_fields"]
    assert "any_acknowledged_operation_loss" in observability["alert_conditions"]


@pytest.mark.unit
def test_t0a_p3_packet_starts_no_runtime_and_claims_no_service_completion() -> None:
    adr = json.loads(ADR.read_text(encoding="utf-8"))
    boundary = adr["prototype_boundary"]
    assert boundary["pure_contract_state_machine_only"] is True
    assert all(
        boundary[key] is False
        for key in (
            "actual_gateway_listener_started",
            "actual_compactor_process_started",
            "port_bound",
            "dependency_downloaded_or_installed",
            "global_runtime_lock_modified",
            "schema_or_migration_allocated",
            "identity_workspace_browser_or_live_data_accessed",
            "production_feature_flag_changed",
        )
    )
    assert adr["claims"]["actual_gateway_transport_implemented"] is False
    assert adr["claims"]["actual_compactor_service_implemented"] is False
    assert adr["claims"]["product_complete"] is False


@pytest.mark.unit
def test_t0a_p3_verifier_cli_is_clean() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-E",
            "-m",
            "code_assist_v1.tools.verify_internal_collaborative_selected_runtime",
            "--compact",
        ],
        cwd=PACKAGE_ROOT.parent,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["status"] == "pass"
