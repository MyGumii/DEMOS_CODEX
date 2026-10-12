"""Fail-closed verifier for the T0A-P1 collaboration protocol contract."""

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
DEFAULT_PROTOCOL = CONTRACT_ROOT / "internal_collaborative_protocol_contract.v1.json"
DEFAULT_PROTOCOL_SCHEMA = (
    CONTRACT_ROOT / "internal_collaborative_protocol_contract.v1.schema.json"
)

EXPECTED_AUTHORITY_PATHS = (
    "code_assist_v1/AGENTS.md",
    "code_assist_v1/docs/notion_parity/core_free_web_ui/INTERNAL_COLLABORATIVE_NOTION_ULTRA_EXECUTION_PLAN_V3.md",
    "code_assist_v1/docs/notion_parity/core_free_web_ui/INTERNAL_COLLABORATIVE_NOTION_XHIGH_IMPLEMENTATION_HANDOFF_V2.md",
    "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_authority_adr.v1.json",
    "code_assist_v1/docs/notion_parity/core_free_web_ui/internal_collaborative_crdt_corpus.v1.json",
    "code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-e0-p1-candidate-corpus-20260823.json",
)
EXPECTED_THREATS = (
    "TICKET-CSRF",
    "TICKET-THEFT",
    "TICKET-REPLAY",
    "PAYLOAD-TAMPER",
    "OPERATION-DUPLICATE",
    "ORDER-GAP",
    "STALE-AUTHORIZATION",
)
EXPECTED_BINDINGS = (
    "jti",
    "subject_id",
    "workspace_id",
    "page_id",
    "client_id",
    "session_binding_sha256",
    "origin",
    "capabilities",
    "workspace_epoch",
    "page_epoch",
    "issued_at_ms",
    "expires_at_ms",
)
EXPECTED_CAPABILITIES = ("comment", "presence", "read", "write")
EXPECTED_MESSAGE_CAPABILITIES = {
    "ack": "read",
    "comment.create": "comment",
    "crdt.update": "write",
    "presence.update": "presence",
    "replay.request": "read",
    "typed.command": "write",
}
EXPECTED_ENVELOPE_FIELDS = (
    "protocol_version",
    "message_type",
    "subject_id",
    "workspace_id",
    "page_id",
    "client_id",
    "session_binding_sha256",
    "capabilities",
    "workspace_epoch",
    "page_epoch",
    "operation_id",
    "client_sequence",
    "acknowledgement_sequence",
    "payload_encoding",
    "payload_length",
    "payload_sha256",
    "payload",
)
EXPECTED_IDENTITIES = (
    "client_operation_id",
    "per_document_event_sequence",
    "crdt_update_generation",
    "checkpoint_snapshot_id",
    "document_revision",
    "second_brain_publication_revision",
)
EXPECTED_DELIVERABLES = {
    "one_time_ticket_threat_model_and_subject_workspace_page_client_binding",
    "versioned_envelope_payload_hash_and_capabilities",
    "operation_sequence_ack_replay_and_epoch_contract",
}
EXPECTED_TESTS = (
    "code_assist_v1/tests/notion_collaboration/test_ticket_protocol.py",
    "code_assist_v1/tests/notion_collaboration/test_sequence_ack_replay.py",
)
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


def _schema_validate(
    value: Any,
    schema: Any,
    errors: list[dict[str, str]],
) -> None:
    if not isinstance(schema, dict):
        return
    for item in JsonSchemaValidator(schema).validate(value):
        errors.append(
            _error(
                f"PROTOCOL_SCHEMA_{item['code']}",
                item["subject"],
                item["message"],
            )
        )


def _validate_authority_inputs(
    protocol: dict[str, Any], errors: list[dict[str, str]]
) -> None:
    inputs = protocol.get("authority_inputs", [])
    paths = tuple(
        item.get("path") for item in inputs if isinstance(item, dict)
    )
    if paths != EXPECTED_AUTHORITY_PATHS or len(set(paths)) != len(paths):
        errors.append(
            _error("AUTHORITY_DENOMINATOR_DRIFT", "protocol.authority_inputs", repr(paths))
        )
    for index, item in enumerate(inputs if isinstance(inputs, list) else []):
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        digest = item.get("sha256")
        subject = f"protocol.authority_inputs[{index}]"
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


def _validate_protocol(protocol: dict[str, Any], errors: list[dict[str, str]]) -> None:
    _validate_authority_inputs(protocol, errors)
    threat_ids = tuple(
        item.get("id")
        for item in protocol.get("threat_model", {}).get("threats", [])
        if isinstance(item, dict)
    )
    if threat_ids != EXPECTED_THREATS:
        errors.append(_error("THREAT_DENOMINATOR_DRIFT", "protocol.threat_model", repr(threat_ids)))
    rejection = str(protocol.get("threat_model", {}).get("non_oracle_rejection", ""))
    if not all(word in rejection for word in ("expired", "reused", "wrong-origin", "stale-epoch")):
        errors.append(_error("NON_ORACLE_RULE_WEAKENED", "protocol.threat_model.non_oracle_rejection", rejection))

    ticket = protocol.get("ticket_contract", {})
    if tuple(ticket.get("required_bindings", [])) != EXPECTED_BINDINGS:
        errors.append(_error("TICKET_BINDING_DRIFT", "protocol.ticket_contract.required_bindings", repr(ticket.get("required_bindings"))))
    if tuple(ticket.get("allowed_capabilities", [])) != EXPECTED_CAPABILITIES:
        errors.append(_error("CAPABILITY_DENOMINATOR_DRIFT", "protocol.ticket_contract.allowed_capabilities", repr(ticket.get("allowed_capabilities"))))
    required_ticket_values = {
        "protocol_version": 1,
        "entropy_bits_minimum": 256,
        "maximum_ttl_ms": 30000,
        "stored_secret_material": "sha256_digest_only",
        "recognized_failure_consumes_ticket": True,
        "flask_cookie_is_gateway_authorization": False,
        "plaintext_ticket_persisted": False,
        "consume_is_atomic": True,
    }
    if any(ticket.get(key) != value for key, value in required_ticket_values.items()):
        errors.append(_error("TICKET_SECURITY_DRIFT", "protocol.ticket_contract", repr(ticket)))

    envelope = protocol.get("envelope_contract", {})
    if tuple(envelope.get("required_fields", [])) != EXPECTED_ENVELOPE_FIELDS:
        errors.append(_error("ENVELOPE_FIELD_DRIFT", "protocol.envelope_contract.required_fields", repr(envelope.get("required_fields"))))
    if envelope.get("message_capabilities") != EXPECTED_MESSAGE_CAPABILITIES:
        errors.append(_error("MESSAGE_CAPABILITY_DRIFT", "protocol.envelope_contract.message_capabilities", repr(envelope.get("message_capabilities"))))
    if (
        envelope.get("protocol_version") != 1
        or envelope.get("payload_hash") != "sha256_of_exact_payload_bytes"
        or envelope.get("maximum_payload_bytes") != 1_048_576
        or envelope.get("raw_cookie_field_allowed") is not False
    ):
        errors.append(_error("ENVELOPE_SECURITY_DRIFT", "protocol.envelope_contract", repr(envelope)))

    sequence = protocol.get("sequence_ack_replay_contract", {})
    if tuple(sequence.get("identity_dimensions", [])) != EXPECTED_IDENTITIES:
        errors.append(_error("IDENTITY_DIMENSION_DRIFT", "protocol.sequence_ack_replay_contract.identity_dimensions", repr(sequence.get("identity_dimensions"))))
    if tuple(sequence.get("deduplication_key", [])) != (
        "subject_id",
        "page_id",
        "operation_id",
    ):
        errors.append(_error("DEDUP_KEY_DRIFT", "protocol.sequence_ack_replay_contract.deduplication_key", repr(sequence.get("deduplication_key"))))
    required_sequence_values = {
        "client_sequence_starts_at": 1,
        "document_sequence_starts_at": 1,
        "same_operation_different_request": "operation_conflict",
        "commit_before_ack": True,
        "replay_cursor": "exclusive_after_document_sequence",
        "replay_order": "strictly_ascending_document_sequence",
        "replay_batch_is_bounded": True,
        "send_time_epoch_revalidation": True,
    }
    if any(sequence.get(key) != value for key, value in required_sequence_values.items()):
        errors.append(_error("SEQUENCE_ACK_REPLAY_DRIFT", "protocol.sequence_ack_replay_contract", repr(sequence)))

    boundary = protocol.get("prototype_boundary", {})
    if boundary.get("implementation") != "standard_library_in_memory_disposable_contract_model":
        errors.append(_error("PROTOTYPE_BOUNDARY_DRIFT", "protocol.prototype_boundary.implementation", str(boundary.get("implementation"))))
    forbidden_true = (
        "network_listener_started",
        "dependency_downloaded_or_installed",
        "production_lockfile_modified",
        "production_runtime_imported",
        "schema_migration_allocated",
        "live_data_identity_or_browser_profile_accessed",
        "production_service_port_or_data_root_changed",
        "gateway_transport_implemented",
        "durable_database_implemented",
        "selected_crdt_runtime_bound",
    )
    if any(boundary.get(key) is not False for key in forbidden_true):
        errors.append(_error("PROTOTYPE_FALSE_PRODUCTION", "protocol.prototype_boundary", repr(boundary)))
    claims = protocol.get("claims", {})
    for key in (
        "ticket_threat_model_frozen",
        "ticket_binding_contract_executable",
        "versioned_envelope_contract_executable",
        "sequence_ack_replay_contract_executable",
    ):
        if claims.get(key) is not True:
            errors.append(_error("CONTRACT_FALSE_NEGATIVE", f"protocol.claims.{key}", str(claims.get(key))))
    for key in (
        "actual_gateway_transport_implemented",
        "actual_durable_event_store_implemented",
        "candidate_winner_selected",
        "production_runtime_implemented",
        "product_complete",
    ):
        if claims.get(key) is not False:
            errors.append(_error("CONTRACT_FALSE_GREEN", f"protocol.claims.{key}", str(claims.get(key))))


def _validate_catalog_and_ladder(
    catalog: dict[str, Any], ladder: dict[str, Any], errors: list[dict[str, str]]
) -> None:
    packets = [item for item in catalog.get("packets", []) if isinstance(item, dict)]
    packet = next((item for item in packets if item.get("id") == "T0A-P1"), None)
    if packet is None:
        errors.append(_error("PACKET_MISSING", "packet:T0A-P1", "required packet missing"))
    else:
        if set(packet.get("deliverables", [])) != EXPECTED_DELIVERABLES:
            errors.append(_error("PACKET_DELIVERABLE_DRIFT", "packet:T0A-P1", repr(packet.get("deliverables"))))
        if (
            packet.get("prerequisites") != ["A0A-P1"]
            or packet.get("approval") is not None
            or packet.get("validation_ids") != ["L3-CRDT-PROTOCOL-CORPUS"]
            or packet.get("status") not in {"planned", "gate_passed"}
        ):
            errors.append(_error("PACKET_BOUNDARY_DRIFT", "packet:T0A-P1", repr(packet)))

    lanes = [
        lane
        for rung in ladder.get("rungs", [])
        if isinstance(rung, dict)
        for lane in rung.get("lanes", [])
        if isinstance(lane, dict)
    ]
    lane = next((item for item in lanes if item.get("id") == "L3-CRDT-PROTOCOL-CORPUS"), None)
    if lane is None:
        errors.append(_error("L3_LANE_MISSING", "lane:L3-CRDT-PROTOCOL-CORPUS", "required lane missing"))
        return
    argv = tuple(item for item in lane.get("argv", []) if isinstance(item, str))
    deliverables = tuple(lane.get("required_deliverables", []))
    if any(path not in argv or path not in deliverables for path in EXPECTED_TESTS):
        errors.append(_error("L3_PROTOCOL_TEST_DRIFT", "lane:L3-CRDT-PROTOCOL-CORPUS", repr(EXPECTED_TESTS)))


def verify_protocol_contract(
    *,
    protocol_path: Path = DEFAULT_PROTOCOL,
    protocol_schema_path: Path = DEFAULT_PROTOCOL_SCHEMA,
    catalog_path: Path = DEFAULT_CATALOG,
    ladder_path: Path = DEFAULT_LADDER,
) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    protocol = _load(Path(protocol_path), "PROTOCOL", errors)
    schema = _load(Path(protocol_schema_path), "PROTOCOL_SCHEMA", errors)
    catalog = _load(Path(catalog_path), "CATALOG", errors)
    ladder = _load(Path(ladder_path), "LADDER", errors)
    if isinstance(protocol, dict) and isinstance(schema, dict):
        _schema_validate(protocol, schema, errors)
        _validate_protocol(protocol, errors)
    if isinstance(catalog, dict) and isinstance(ladder, dict):
        _validate_catalog_and_ladder(catalog, ladder, errors)
    ordered = _ordered(errors)
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "contract": "code_assist.notion.internal_collaborative_protocol_contract.audit.v1",
        "status": "pass" if not ordered else "fail",
        "error_count": len(ordered),
        "errors": ordered,
        "protocol_sha256": sha256_file(Path(protocol_path)) if Path(protocol_path).is_file() else None,
        "summary": {
            "packet_id": protocol.get("packet_id") if isinstance(protocol, dict) else None,
            "threat_count": len(protocol.get("threat_model", {}).get("threats", [])) if isinstance(protocol, dict) else 0,
            "ticket_binding_count": len(protocol.get("ticket_contract", {}).get("required_bindings", [])) if isinstance(protocol, dict) else 0,
            "message_type_count": len(protocol.get("envelope_contract", {}).get("message_capabilities", {})) if isinstance(protocol, dict) else 0,
            "identity_dimension_count": len(protocol.get("sequence_ack_replay_contract", {}).get("identity_dimensions", [])) if isinstance(protocol, dict) else 0,
            "gateway_transport_implemented": protocol.get("claims", {}).get("actual_gateway_transport_implemented") if isinstance(protocol, dict) else None,
            "durable_event_store_implemented": protocol.get("claims", {}).get("actual_durable_event_store_implemented") if isinstance(protocol, dict) else None,
            "product_complete": protocol.get("claims", {}).get("product_complete") if isinstance(protocol, dict) else None,
        },
    }
    return report, 0 if not ordered else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--protocol-schema", type=Path, default=DEFAULT_PROTOCOL_SCHEMA)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--ladder", type=Path, default=DEFAULT_LADDER)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report, exit_code = verify_protocol_contract(
        protocol_path=args.protocol,
        protocol_schema_path=args.protocol_schema,
        catalog_path=args.catalog,
        ladder_path=args.ladder,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
