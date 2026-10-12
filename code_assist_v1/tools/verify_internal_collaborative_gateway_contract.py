"""Fail-closed verifier for the T0A-P2 transport-only gateway contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any, Iterable, Sequence

from code_assist_v1.tools.internal_collaborative_validation import (
    DEFAULT_CATALOG,
    DEFAULT_LADDER,
    repository_path,
    sha256_file,
)
from code_assist_v1.tools.verify_notion_parity_catalog import (
    JsonSchemaValidator,
    load_json,
)


REPORT_SCHEMA_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = PROJECT_ROOT / "docs" / "notion_parity" / "core_free_web_ui"
DEFAULT_GATEWAY = CONTRACT_ROOT / "internal_collaborative_gateway_contract.v1.json"
DEFAULT_GATEWAY_SCHEMA = (
    CONTRACT_ROOT / "internal_collaborative_gateway_contract.v1.schema.json"
)
EXPECTED_AUTHORITY_PATHS = (
    "code_assist_v1/AGENTS.md",
    "code_assist_v1/docs/notion_parity/core_free_web_ui/INTERNAL_COLLABORATIVE_NOTION_ULTRA_EXECUTION_PLAN_V3.md",
    "code_assist_v1/docs/notion_parity/core_free_web_ui/INTERNAL_COLLABORATIVE_NOTION_XHIGH_IMPLEMENTATION_HANDOFF_V2.md",
    "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_protocol_contract.v1.json",
    "code_assist_v1/notion_collaboration/protocol_contract.py",
    "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-t0a-p1-protocol-contract-20260823.json",
)
EXPECTED_NEGATIVES = (
    "ORIGIN-MISMATCH",
    "TICKET-EXPIRED",
    "TICKET-REUSED",
    "EPOCH-STALE-ON-CONNECT",
    "EPOCH-STALE-BEFORE-SUBMIT",
    "EPOCH-STALE-BEFORE-DRAIN",
    "SLOW-CONSUMER-BACKPRESSURE",
    "RECONNECT-SUPERSEDES-STALE",
    "RECONNECT-STORM-THROTTLED",
    "AUTHORITY-UNAVAILABLE-FAIL-CLOSED",
    "CONNECTION-CAPACITY-BOUNDED",
)
EXPECTED_DELIVERABLES = {
    "transport_only_gateway_prototype",
    "origin_expiry_reuse_epoch_and_stale_client_negative_tests",
    "backpressure_reconnect_storm_and_gateway_unavailable_contract",
}
EXPECTED_TEST = "code_assist_v1/tests/notion_collaboration/test_backpressure.py"
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
DYNAMIC_AGENTS_PATH = "code_assist_v1/AGENTS.md"
FROZEN_AGENTS_SHA256 = "sha256:916383ca3674842d7dd2f9ef77af989b3b7dfe38e4c3f745072c6f9655d47549"


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _ordered(errors: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    unique = {(item["code"], item["subject"], item["message"]): item for item in errors}
    return [unique[key] for key in sorted(unique)]


def _load(path: Path, label: str, errors: list[dict[str, str]]) -> Any | None:
    try:
        return load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(_error(f"{label}_READ_FAILED", str(path), f"strict JSON read failed: {exc}"))
        return None


def _validate_authority_inputs(
    gateway: dict[str, Any], errors: list[dict[str, str]]
) -> None:
    inputs = gateway.get("authority_inputs", [])
    paths = tuple(item.get("path") for item in inputs if isinstance(item, dict))
    if paths != EXPECTED_AUTHORITY_PATHS or len(set(paths)) != len(paths):
        errors.append(_error("AUTHORITY_DENOMINATOR_DRIFT", "gateway.authority_inputs", repr(paths)))
    for index, item in enumerate(inputs if isinstance(inputs, list) else []):
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        digest = item.get("sha256")
        subject = f"gateway.authority_inputs[{index}]"
        resolved = repository_path(path) if isinstance(path, str) else None
        if resolved is None or not resolved.is_file():
            errors.append(_error("AUTHORITY_FILE_MISSING", subject, str(path)))
        elif not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            errors.append(_error("AUTHORITY_HASH_INVALID", subject, str(digest)))
        elif path == DYNAMIC_AGENTS_PATH and digest == FROZEN_AGENTS_SHA256:
            try:
                current_agents = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                current_agents = ""
            if not current_agents.startswith("# Shared Knowledge Agent implementation instructions"):
                errors.append(_error("DYNAMIC_AGENTS_INVALID", subject, str(path)))
        elif digest != sha256_file(resolved):
            errors.append(_error("AUTHORITY_HASH_DRIFT", subject, str(path)))


def _validate_gateway(gateway: dict[str, Any], errors: list[dict[str, str]]) -> None:
    _validate_authority_inputs(gateway, errors)
    boundary = gateway.get("transport_only_boundary", {})
    expected_does_not_own = {
        "authentication_cookie",
        "workspace_or_page_IAM",
        "canonical_page_or_block_mutation",
        "durable_event_or_outbox_storage",
        "CRDT_semantics_or_compaction",
        "Second_Brain_publication",
    }
    if set(boundary.get("does_not_own", [])) != expected_does_not_own:
        errors.append(_error("TRANSPORT_OWNERSHIP_DRIFT", "gateway.transport_only_boundary.does_not_own", repr(boundary.get("does_not_own"))))
    if (
        boundary.get("flask_cookie_is_transport_authorization") is not False
        or boundary.get("mutation_ack_source")
        != "canonical_sequence_ledger_after_durable_commit"
    ):
        errors.append(_error("TRANSPORT_AUTHORITY_DRIFT", "gateway.transport_only_boundary", repr(boundary)))

    admission = gateway.get("admission_contract", {})
    required_false = (
        "ticket_reuse_allowed",
        "expired_ticket_allowed",
        "wrong_origin_allowed",
        "stale_epoch_allowed",
        "client_visible_rejection_contains_protected_binding",
    )
    if (
        any(admission.get(key) is not False for key in required_false)
        or admission.get("maximum_active_connections_default") != 1024
        or len(admission.get("steps", [])) != 6
    ):
        errors.append(_error("ADMISSION_BOUNDARY_DRIFT", "gateway.admission_contract", repr(admission)))

    queue = gateway.get("queue_and_backpressure_contract", {})
    expected_queue = {
        "maximum_durable_frames_default": 64,
        "maximum_durable_payload_bytes_default": 4_194_304,
        "durable_overflow_action": "close_slow_session_purge_queue_require_replay",
        "silent_durable_drop_allowed": False,
        "acknowledged_operation_rollback_on_broadcast_failure": False,
        "queue_payload_counting": "exact_payload_bytes",
        "drain_order": "strictly_ascending_document_sequence",
        "send_time_epoch_revalidation": True,
        "stale_or_unavailable_drain_action": "purge_queue_close_session_return_zero_payload",
        "replay_batch_limit_inherited_from_T0A_P1": 256,
    }
    if any(queue.get(key) != value for key, value in expected_queue.items()):
        errors.append(_error("BACKPRESSURE_CONTRACT_DRIFT", "gateway.queue_and_backpressure_contract", repr(queue)))

    reconnect = gateway.get("reconnect_contract", {})
    expected_reconnect = {
        "attempt_key": ["subject_id", "client_id"],
        "fixed_window_ms_default": 10_000,
        "maximum_attempts_per_window_default": 5,
        "excess_attempt_action": "reject_before_ticket_consumption_without_content",
        "same_binding_reconnect_action": "new_connection_supersedes_old_and_purges_old_queue",
        "operation_and_client_sequence_state_is_transport_owned": False,
        "storm_never_increases_connection_capacity": True,
    }
    if any(reconnect.get(key) != value for key, value in expected_reconnect.items()):
        errors.append(_error("RECONNECT_CONTRACT_DRIFT", "gateway.reconnect_contract", repr(reconnect)))

    unavailable = gateway.get("gateway_unavailable_contract", {})
    required_unavailable_values = {
        "authority_resolver_unavailable_on_admission": "reject_without_ticket_consumption_or_content",
        "authority_resolver_unavailable_before_mutation": "reject_without_canonical_commit_or_content",
        "authority_resolver_unavailable_after_commit_before_broadcast": "keep_durable_commit_close_or_fence_sessions_and_require_replay",
        "authority_resolver_unavailable_before_drain": "purge_queue_close_session_return_zero_payload",
        "gateway_process_unavailable_during_revoke": "canonical_revoke_may_commit_with_durable_fence_outbox_but_no_protected_send_is_permitted",
        "availability_never_substitutes_for_authorization": True,
    }
    if any(unavailable.get(key) != value for key, value in required_unavailable_values.items()):
        errors.append(_error("UNAVAILABLE_CONTRACT_DRIFT", "gateway.gateway_unavailable_contract", repr(unavailable)))

    negatives = tuple(gateway.get("negative_scenarios", []))
    if negatives != EXPECTED_NEGATIVES:
        errors.append(_error("NEGATIVE_DENOMINATOR_DRIFT", "gateway.negative_scenarios", repr(negatives)))
    prototype = gateway.get("prototype_boundary", {})
    if prototype.get("implementation") != "standard_library_in_memory_transport_state_machine":
        errors.append(_error("PROTOTYPE_BOUNDARY_DRIFT", "gateway.prototype_boundary.implementation", str(prototype.get("implementation"))))
    forbidden_true = (
        "real_websocket_or_sse_listener_started",
        "os_port_bound",
        "dependency_downloaded_or_installed",
        "production_lockfile_modified",
        "production_runtime_imported",
        "schema_migration_allocated",
        "live_data_identity_or_browser_profile_accessed",
        "product_route_or_store_modified",
        "selected_gateway_language_or_crdt_runtime_bound",
    )
    if any(prototype.get(key) is not False for key in forbidden_true):
        errors.append(_error("PROTOTYPE_FALSE_PRODUCTION", "gateway.prototype_boundary", repr(prototype)))

    claims = gateway.get("claims", {})
    for key in (
        "transport_only_gateway_state_machine_executable",
        "origin_expiry_reuse_epoch_and_stale_client_negatives_executable",
        "backpressure_reconnect_storm_and_unavailable_contract_executable",
    ):
        if claims.get(key) is not True:
            errors.append(_error("CONTRACT_FALSE_NEGATIVE", f"gateway.claims.{key}", str(claims.get(key))))
    for key in (
        "actual_network_gateway_implemented",
        "actual_durable_event_store_implemented",
        "selected_runtime_bound",
        "production_runtime_implemented",
        "product_complete",
    ):
        if claims.get(key) is not False:
            errors.append(_error("CONTRACT_FALSE_GREEN", f"gateway.claims.{key}", str(claims.get(key))))


def _validate_catalog_ladder(
    catalog: dict[str, Any], ladder: dict[str, Any], errors: list[dict[str, str]]
) -> None:
    packet = next(
        (
            item
            for item in catalog.get("packets", [])
            if isinstance(item, dict) and item.get("id") == "T0A-P2"
        ),
        None,
    )
    if packet is None:
        errors.append(_error("PACKET_MISSING", "packet:T0A-P2", "required packet missing"))
    else:
        if set(packet.get("deliverables", [])) != EXPECTED_DELIVERABLES:
            errors.append(_error("PACKET_DELIVERABLE_DRIFT", "packet:T0A-P2", repr(packet.get("deliverables"))))
        if (
            packet.get("prerequisites") != ["T0A-P1"]
            or packet.get("approval") is not None
            or packet.get("validation_ids") != ["L3-CRDT-PROTOCOL-CORPUS"]
            or packet.get("status") not in {"planned", "gate_passed"}
        ):
            errors.append(_error("PACKET_BOUNDARY_DRIFT", "packet:T0A-P2", repr(packet)))

    lane = next(
        (
            lane
            for rung in ladder.get("rungs", [])
            if isinstance(rung, dict)
            for lane in rung.get("lanes", [])
            if isinstance(lane, dict) and lane.get("id") == "L3-CRDT-PROTOCOL-CORPUS"
        ),
        None,
    )
    if lane is None:
        errors.append(_error("L3_LANE_MISSING", "lane:L3-CRDT-PROTOCOL-CORPUS", "required lane missing"))
    elif (
        EXPECTED_TEST not in lane.get("argv", [])
        or EXPECTED_TEST not in lane.get("required_deliverables", [])
        or lane.get("state") != "planned_required"
    ):
        errors.append(_error("L3_BACKPRESSURE_TEST_DRIFT", "lane:L3-CRDT-PROTOCOL-CORPUS", EXPECTED_TEST))


def verify_gateway_contract(
    *,
    gateway_path: Path = DEFAULT_GATEWAY,
    gateway_schema_path: Path = DEFAULT_GATEWAY_SCHEMA,
    catalog_path: Path = DEFAULT_CATALOG,
    ladder_path: Path = DEFAULT_LADDER,
) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    gateway = _load(Path(gateway_path), "GATEWAY", errors)
    schema = _load(Path(gateway_schema_path), "GATEWAY_SCHEMA", errors)
    catalog = _load(Path(catalog_path), "CATALOG", errors)
    ladder = _load(Path(ladder_path), "LADDER", errors)
    if isinstance(gateway, dict) and isinstance(schema, dict):
        for item in JsonSchemaValidator(schema).validate(gateway):
            errors.append(_error(f"GATEWAY_SCHEMA_{item['code']}", item["subject"], item["message"]))
        _validate_gateway(gateway, errors)
    if isinstance(catalog, dict) and isinstance(ladder, dict):
        _validate_catalog_ladder(catalog, ladder, errors)
    ordered = _ordered(errors)
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "contract": "code_assist.notion.internal_collaborative_gateway_contract.audit.v1",
        "status": "pass" if not ordered else "fail",
        "error_count": len(ordered),
        "errors": ordered,
        "gateway_sha256": sha256_file(Path(gateway_path)) if Path(gateway_path).is_file() else None,
        "summary": {
            "packet_id": gateway.get("packet_id") if isinstance(gateway, dict) else None,
            "negative_scenario_count": len(gateway.get("negative_scenarios", [])) if isinstance(gateway, dict) else 0,
            "maximum_durable_frames_default": gateway.get("queue_and_backpressure_contract", {}).get("maximum_durable_frames_default") if isinstance(gateway, dict) else None,
            "maximum_attempts_per_window_default": gateway.get("reconnect_contract", {}).get("maximum_attempts_per_window_default") if isinstance(gateway, dict) else None,
            "actual_network_gateway_implemented": gateway.get("claims", {}).get("actual_network_gateway_implemented") if isinstance(gateway, dict) else None,
            "product_complete": gateway.get("claims", {}).get("product_complete") if isinstance(gateway, dict) else None,
        },
    }
    return report, 0 if not ordered else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", type=Path, default=DEFAULT_GATEWAY)
    parser.add_argument("--gateway-schema", type=Path, default=DEFAULT_GATEWAY_SCHEMA)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--ladder", type=Path, default=DEFAULT_LADDER)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report, exit_code = verify_gateway_contract(
        gateway_path=args.gateway,
        gateway_schema_path=args.gateway_schema,
        catalog_path=args.catalog,
        ladder_path=args.ladder,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
