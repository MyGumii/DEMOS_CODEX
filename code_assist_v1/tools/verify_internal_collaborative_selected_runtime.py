"""Verify the checked-in T0A-P3 selected runtime topology and pure contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from code_assist_v1.notion_collaboration.selected_runtime_contract import (
    COMPACTION_REQUEST_CONTRACT,
    COMPACTION_RESPONSE_CONTRACT,
    HEALTH_CONTRACT,
    LIFECYCLE_STATES,
    SelectedRuntimeContractError,
    runtime_binding_sha256,
    validate_checked_in_binding,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADR = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_gateway_topology_adr.v1.json"
)


def verify() -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    try:
        binding = validate_checked_in_binding()
    except (OSError, SelectedRuntimeContractError) as exc:
        binding = None
        errors.append({"code": "RUNTIME_BINDING_INVALID", "subject": "binding", "message": str(exc)})
    try:
        adr = json.loads(ADR.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        adr = {}
        errors.append({"code": "ADR_INVALID", "subject": "adr", "message": str(exc)})
    if adr.get("contract") != "code_assist.notion.internal_collaborative_gateway_topology_adr.v1":
        errors.append({"code": "ADR_CONTRACT_DRIFT", "subject": "$.contract", "message": "unexpected contract"})
    selected = adr.get("selected_runtime", {})
    if selected.get("candidate_id") != "PY_OPAQUE_NODE_COMPACTOR" or selected.get("gateway_payload_semantics") != "opaque_bytes":
        errors.append({"code": "SELECTED_RUNTIME_DRIFT", "subject": "$.selected_runtime", "message": "winner or opaque boundary changed"})
    topology = adr.get("process_topology", {})
    gateway = topology.get("python_gateway", {})
    compactor = topology.get("node_compactor", {})
    if "authorization_policy" not in gateway.get("does_not_own", []) or "network_listener" not in compactor.get("does_not_own", []):
        errors.append({"code": "AUTHORITY_BOUNDARY_DRIFT", "subject": "$.process_topology", "message": "gateway or compactor gained forbidden authority"})
    epoch = adr.get("ticket_epoch_ack_binding", {})
    if epoch.get("canonical_commit_before_ACK") is not True or epoch.get("gateway_or_compactor_may_allocate_document_sequence") is not False:
        errors.append({"code": "ACK_AUTHORITY_DRIFT", "subject": "$.ticket_epoch_ack_binding", "message": "ACK or sequence boundary changed"})
    compaction = adr.get("compaction_protocol", {})
    if compaction.get("request_contract") != COMPACTION_REQUEST_CONTRACT or compaction.get("response_contract") != COMPACTION_RESPONSE_CONTRACT:
        errors.append({"code": "COMPACTION_CONTRACT_DRIFT", "subject": "$.compaction_protocol", "message": "protocol name changed"})
    lifecycle = adr.get("lifecycle_contract", {})
    if lifecycle.get("states") != list(LIFECYCLE_STATES) or lifecycle.get("start_or_stop_executed_by_this_packet") is not False:
        errors.append({"code": "LIFECYCLE_DRIFT", "subject": "$.lifecycle_contract", "message": "state or execution boundary changed"})
    health = adr.get("health_contract", {})
    if health.get("contract") != HEALTH_CONTRACT or health.get("health_response_fields", [None])[0] != "contract":
        errors.append({"code": "HEALTH_CONTRACT_DRIFT", "subject": "$.health_contract", "message": "health denominator changed"})
    rollback = adr.get("rollback_and_kill_switch_contract", {})
    if rollback.get("aggregate_flag_dominates") is not True or rollback.get("rejected_runtime_fallback_allowed") is not False:
        errors.append({"code": "ROLLBACK_DRIFT", "subject": "$.rollback_and_kill_switch_contract", "message": "kill switch or fallback changed"})
    prototype = adr.get("prototype_boundary", {})
    forbidden_true = (
        "actual_gateway_listener_started",
        "actual_compactor_process_started",
        "port_bound",
        "dependency_downloaded_or_installed",
        "global_runtime_lock_modified",
        "schema_or_migration_allocated",
        "identity_workspace_browser_or_live_data_accessed",
        "production_feature_flag_changed",
    )
    if any(prototype.get(key) is not False for key in forbidden_true):
        errors.append({"code": "PROTOTYPE_BOUNDARY_DRIFT", "subject": "$.prototype_boundary", "message": "T0A-P3 performed a forbidden effect"})
    expected_binding = adr.get("runtime_binding", {}).get("expected_sha256")
    if expected_binding != runtime_binding_sha256():
        errors.append({"code": "RUNTIME_BINDING_HASH_DRIFT", "subject": "$.runtime_binding.expected_sha256", "message": "binding hash differs"})
    return {
        "contract": "code_assist.notion.internal_collaborative_selected_runtime_verification.v1",
        "status": "pass" if not errors else "fail",
        "error_count": len(errors),
        "errors": sorted(errors, key=lambda item: (item["code"], item["subject"])),
        "runtime_binding_sha256": binding["runtime_binding_sha256"] if binding else None,
        "claims": {
            "topology_contract_complete": not errors,
            "actual_gateway_transport_implemented": False,
            "actual_compactor_service_implemented": False,
            "runtime_enabled": False,
            "product_complete": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report = verify()
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=None if args.compact else 2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
