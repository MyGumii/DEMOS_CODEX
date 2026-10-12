"""Fail-closed verifier for the T0B-P2 runtime gateway contract."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any, Sequence

from code_assist_v1.notion_collaboration.gateway_service import (
    ASGI_SERVICE_CONTRACT,
    EXPECTED_RUNTIME_VERSIONS,
    FORBIDDEN_OPERATOR_PORTS,
)
from code_assist_v1.notion_collaboration.runtime_gateway import (
    ALLOWED_DERIVED_FRAME_KINDS,
    DEFAULT_MAX_CONNECTIONS,
    DEFAULT_MAX_QUEUE_BYTES,
    DEFAULT_MAX_QUEUE_FRAMES,
    DEFAULT_RECONNECT_ATTEMPTS,
    DEFAULT_RECONNECT_WINDOW_MS,
    REQUIRED_COUNTERS,
    REQUIRED_GAUGES,
    REQUIRED_HISTOGRAMS,
    RUNTIME_GATEWAY_CONTRACT,
    RuntimeGateway,
)
from code_assist_v1.notion_collaboration.selected_runtime_contract import HEALTH_CONTRACT


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
DEFAULT_CONTRACT = PROJECT_ROOT / "docs" / "notion_parity" / "core_free_web_ui" / "internal_collaborative_runtime_gateway.v1.json"
SUCCESSOR_CONTRACT = PROJECT_ROOT / "docs" / "notion_parity" / "core_free_web_ui" / "internal_collaborative_cross_process_gateway.v1.json"
DYNAMIC_AGENTS_PATH = "code_assist_v1/AGENTS.md"
FROZEN_AGENTS_SHA256 = "sha256:916383ca3674842d7dd2f9ef77af989b3b7dfe38e4c3f745072c6f9655d47549"
SUCCESSOR_OWNED_GATEWAY_PATH = "code_assist_v1/notion_collaboration/gateway_service.py"


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _error(errors: list[dict[str, str]], code: str, subject: str, message: str) -> None:
    errors.append({"code": code, "subject": subject, "message": message})


def _successor_owns_current_artifact(relative: str, target: Path) -> bool:
    if relative != SUCCESSOR_OWNED_GATEWAY_PATH:
        return False
    try:
        successor = json.loads(SUCCESSOR_CONTRACT.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return False
    rows = successor.get("source_manifest", []) if isinstance(successor, dict) else []
    binding = next(
        (item for item in rows if isinstance(item, dict) and item.get("path") == relative),
        None,
    )
    return bool(
        successor.get("packet_id") == "T0B-P3"
        and successor.get("status") == "gate_passed"
        and successor.get("claims", {}).get("T0B_P3_gate_passed") is True
        and isinstance(binding, dict)
        and binding.get("sha256") == _sha256_file(target)
    )


def verify(contract_path: Path = DEFAULT_CONTRACT) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        contract = {}
        _error(errors, "CONTRACT_READ_FAILED", "contract", str(exc))
    if not isinstance(contract, dict):
        contract = {}
        _error(errors, "CONTRACT_TYPE", "contract", "contract must be an object")
    if (
        contract.get("schema_version") != 1
        or contract.get("contract") != "code_assist.notion.internal_collaborative_runtime_gateway.v1"
        or contract.get("packet_id") != "T0B-P2"
    ):
        _error(errors, "CONTRACT_IDENTITY_DRIFT", "contract", "T0B-P2 identity is not exact")

    inputs = contract.get("authority_inputs", [])
    paths: list[str] = []
    for index, item in enumerate(inputs if isinstance(inputs, list) else []):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            _error(errors, "AUTHORITY_INPUT_INVALID", f"authority_inputs[{index}]", "invalid input")
            continue
        relative = item["path"]
        target = (REPOSITORY_ROOT / relative).resolve(strict=False)
        try:
            target.relative_to(REPOSITORY_ROOT.resolve())
        except ValueError:
            _error(errors, "AUTHORITY_INPUT_ESCAPE", relative, "path escapes repository")
            continue
        if relative in paths:
            _error(errors, "AUTHORITY_INPUT_DUPLICATE", relative, "duplicate input")
        paths.append(relative)
        if not target.is_file():
            _error(errors, "AUTHORITY_INPUT_HASH", relative, "authority input drift")
        elif relative == DYNAMIC_AGENTS_PATH and item.get("sha256") == FROZEN_AGENTS_SHA256:
            try:
                current_agents = target.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                current_agents = ""
            if not current_agents.startswith("# Shared Knowledge Agent implementation instructions"):
                _error(errors, "DYNAMIC_AGENTS_INVALID", relative, "current dynamic instructions are invalid")
        elif _successor_owns_current_artifact(relative, target):
            pass
        elif item.get("sha256") != _sha256_file(target):
            _error(errors, "AUTHORITY_INPUT_HASH", relative, "authority input drift")

    kernel = contract.get("gateway_kernel", {})
    expected_kernel = {
        "canonical_authority_owns_ticket_consumption": True,
        "canonical_authority_owns_document_sequence": True,
        "canonical_commit_precedes_ack_and_broadcast": True,
        "gateway_payload_semantics": "opaque_Yjs_v1_update_bytes",
        "gateway_direct_canonical_table_writes": False,
        "same_binding_reconnect_fences_previous_session": True,
        "maximum_connections": DEFAULT_MAX_CONNECTIONS,
        "maximum_queue_frames_per_session": DEFAULT_MAX_QUEUE_FRAMES,
        "maximum_queue_bytes_per_session": DEFAULT_MAX_QUEUE_BYTES,
        "reconnect_window_ms": DEFAULT_RECONNECT_WINDOW_MS,
        "maximum_reconnect_attempts_per_window": DEFAULT_RECONNECT_ATTEMPTS,
        "backpressure_action": "retain_durable_commit_purge_queue_fence_slow_session_require_replay",
    }
    if not isinstance(kernel, dict) or any(kernel.get(key) != value for key, value in expected_kernel.items()):
        _error(errors, "GATEWAY_KERNEL_DRIFT", "gateway_kernel", "kernel boundary changed")
    parameters = inspect.signature(RuntimeGateway).parameters
    if parameters["aggregate_enabled"].default is not False or parameters["realtime_enabled"].default is not False:
        _error(errors, "DEFAULT_ON", "RuntimeGateway", "feature defaults must remain false")

    authorization = contract.get("authorization_and_redaction", {})
    expected_authorization = {
        "canonical_binding_checked_before_inbound_command": True,
        "canonical_binding_checked_by_durable_commit": True,
        "canonical_binding_checked_before_each_protected_frame_send": True,
        "replay_revalidates_before_payload_read": True,
        "stale_epoch_or_revoked_delivery_bytes": 0,
        "authority_unavailable_delivery_bytes": 0,
        "queued_derived_frames_purged_on_fence": True,
        "stable_non_oracle_errors": True,
    }
    if (
        not isinstance(authorization, dict)
        or any(authorization.get(key) != value for key, value in expected_authorization.items())
        or set(authorization.get("protected_derived_frame_kinds", [])) != ALLOWED_DERIVED_FRAME_KINDS
    ):
        _error(errors, "AUTHORIZATION_REDACTION_DRIFT", "authorization_and_redaction", "send fence weakened")

    metrics = contract.get("health_and_metrics", {})
    if (
        metrics.get("health_contract") != HEALTH_CONTRACT
        or metrics.get("default_aggregate_enabled") is not False
        or metrics.get("default_realtime_enabled") is not False
        or metrics.get("raw_workspace_or_page_identifiers_exported") is not False
        or metrics.get("raw_ticket_payload_title_member_or_content_exported") is not False
        or set(metrics.get("identifier_dimensions", [])) != {"workspace_id_hash", "page_id_hash"}
        or set(metrics.get("required_counters", [])) != REQUIRED_COUNTERS
        or set(metrics.get("required_gauges", [])) != REQUIRED_GAUGES
        or set(metrics.get("required_histograms", [])) != REQUIRED_HISTOGRAMS
    ):
        _error(errors, "HEALTH_METRICS_DRIFT", "health_and_metrics", "health or metrics boundary changed")

    service = contract.get("service_and_lifecycle", {})
    if (
        service.get("service_contract") != ASGI_SERVICE_CONTRACT
        or service.get("selected_gateway_runtime") != "CPython_3.14.3_uvicorn_0.52.4_websockets_17.0.1"
        or service.get("selected_runtime_import_is_lazy_and_exact_version_checked") is not True
        or service.get("owned_lifecycle_probe_process_count") != 1
        or service.get("owned_lifecycle_probe_listener") != "loopback_OS_assigned_port_zero"
        or set(service.get("forbidden_operator_ports", [])) != FORBIDDEN_OPERATOR_PORTS
        or service.get("exact_PID_and_launch_marker_verified") is not True
        or service.get("exact_owned_process_port_and_runtime_root_cleanup_verified") is not True
        or service.get("lifecycle_probe_transports_protected_data") is not False
        or EXPECTED_RUNTIME_VERSIONS != {"uvicorn": "0.52.4", "websockets": "17.0.1"}
    ):
        _error(errors, "SERVICE_LIFECYCLE_DRIFT", "service_and_lifecycle", "service boundary changed")

    deferred = contract.get("deferred_cross_process_boundary", {})
    if deferred.get("closure_owner") != "T0B-P3" or any(
        deferred.get(key) is not False
        for key in (
            "selected_python_runtime_installed_or_enabled_by_packet",
            "selected_Node_Yjs_runtime_installed_or_enabled_by_packet",
            "actual_selected_WebSocket_listener_started_by_gate",
            "actual_Node_compactor_started_by_gate",
            "actual_Yjs_merge_or_convergence_claimed",
            "actual_browser_collaboration_claimed",
        )
    ):
        _error(errors, "DEFERRED_BOUNDARY_FALSE_GREEN", "deferred_cross_process_boundary", "T0B-P3 work claimed early")

    validation = contract.get("validation", {})
    if (
        validation.get("new_test_count") != 9
        or validation.get("failed") != 0
        or validation.get("skipped") != 0
        or validation.get("xfailed") != 0
        or validation.get("database_roots") != "pytest_temporary_only"
        or validation.get("listener_ports") != "pytest_OS_assigned_loopback_only"
        or validation.get("owned_process_cleanup_complete") is not True
    ):
        _error(errors, "VALIDATION_DRIFT", "validation", "test evidence boundary changed")

    claims = contract.get("claims", {})
    required_true = (
        "durable_runtime_gateway_kernel_implemented",
        "ASGI_gateway_service_adapter_implemented",
        "send_time_epoch_fence_and_derived_frame_redaction_implemented",
        "bounded_backpressure_health_metrics_and_owned_lifecycle_implemented",
        "T0B_P2_gate_passed",
    )
    required_false = (
        "RT_001_closed",
        "runtime_or_feature_enabled",
        "development_pair_changed",
        "production_target_changed",
        "product_complete",
    )
    if any(claims.get(key) is not True for key in required_true) or any(claims.get(key) is not False for key in required_false):
        _error(errors, "CLAIM_DRIFT", "claims", "packet or product claim drift")

    unique = {(item["code"], item["subject"], item["message"]): item for item in errors}
    ordered = [unique[key] for key in sorted(unique)]
    report = {
        "report_schema_version": 1,
        "contract": RUNTIME_GATEWAY_CONTRACT + ".verification",
        "status": "pass" if not ordered else "fail",
        "error_count": len(ordered),
        "errors": ordered,
        "summary": {
            "packet_id": "T0B-P2",
            "new_test_count": validation.get("new_test_count"),
            "selected_runtime_versions": EXPECTED_RUNTIME_VERSIONS,
            "protected_derived_frame_kinds": sorted(ALLOWED_DERIVED_FRAME_KINDS),
            "required_metric_count": len(REQUIRED_COUNTERS | REQUIRED_GAUGES | REQUIRED_HISTOGRAMS),
            "RT_001_closed": claims.get("RT_001_closed"),
            "product_complete": claims.get("product_complete"),
        },
    }
    return report, 0 if not ordered else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify(args.contract)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
