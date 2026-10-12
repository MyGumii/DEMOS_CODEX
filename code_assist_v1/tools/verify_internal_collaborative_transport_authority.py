"""Fail-closed verifier for the T0B-P1 durable transport authority."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from code_assist_v1 import notion_workspace_realtime_transport_migration as migration
from code_assist_v1.notion_collaboration.durable_transport import (
    DURABLE_TRANSPORT_CONTRACT,
    YJS_UPDATE_ENCODING,
)
from code_assist_v1.notion_collaboration.selected_runtime_contract import (
    SELECTED_CANDIDATE_ID,
    WIRE_FORMAT,
    runtime_binding_sha256,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
DEFAULT_CONTRACT = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_transport_authority.v1.json"
)
DYNAMIC_AGENTS_PATH = "code_assist_v1/AGENTS.md"
FROZEN_AGENTS_SHA256 = "sha256:916383ca3674842d7dd2f9ef77af989b3b7dfe38e4c3f745072c6f9655d47549"


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _error(errors: list[dict[str, str]], code: str, subject: str, message: str) -> None:
    errors.append({"code": code, "subject": subject, "message": message})


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
        or contract.get("contract") != "code_assist.notion.internal_collaborative_transport_authority.v1"
        or contract.get("packet_id") != "T0B-P1"
    ):
        _error(errors, "CONTRACT_IDENTITY_DRIFT", "contract", "T0B-P1 identity is not exact")

    for index, item in enumerate(contract.get("authority_inputs", [])):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            _error(errors, "AUTHORITY_INPUT_INVALID", f"authority_inputs[{index}]", "invalid input")
            continue
        target = (REPOSITORY_ROOT / item["path"]).resolve(strict=False)
        try:
            target.relative_to(REPOSITORY_ROOT.resolve())
        except ValueError:
            _error(errors, "AUTHORITY_INPUT_ESCAPE", item["path"], "path escapes repository")
            continue
        if not target.is_file():
            _error(errors, "AUTHORITY_INPUT_HASH", item["path"], "authority input drift")
        elif item["path"] == DYNAMIC_AGENTS_PATH and item.get("sha256") == FROZEN_AGENTS_SHA256:
            try:
                current_agents = target.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                current_agents = ""
            if not current_agents.startswith("# Shared Knowledge Agent implementation instructions"):
                _error(errors, "DYNAMIC_AGENTS_INVALID", item["path"], "current dynamic instructions are invalid")
        elif item.get("sha256") != _sha256_file(target):
            _error(errors, "AUTHORITY_INPUT_HASH", item["path"], "authority input drift")

    lineage = contract.get("schema_lineage", {})
    expected_lineage = {
        "source_version": migration.SOURCE_VERSION,
        "source_name": migration.SOURCE_NAME,
        "source_checksum": migration.SOURCE_CHECKSUM,
        "source_actual_schema_sha256": migration.SOURCE_ACTUAL_SCHEMA_SHA256,
        "parallel_reserved_version": migration.PARALLEL_RESERVED_VERSION,
        "target_version": migration.TARGET_VERSION,
        "target_name": migration.TARGET_NAME,
        "target_checksum": migration.TARGET_CHECKSUM,
        "target_actual_schema_sha256": migration.TARGET_ACTUAL_SCHEMA_SHA256,
        "source_object_count": len(migration.SOURCE_OBJECTS),
        "target_object_count": len(migration.TARGET_OBJECTS),
    }
    if not isinstance(lineage, dict) or any(lineage.get(key) != value for key, value in expected_lineage.items()):
        _error(errors, "SCHEMA_LINEAGE_DRIFT", "schema_lineage", "migration binding drift")
    required_tables = {
        "workspace_collaboration_tickets",
        "workspace_collaboration_client_ack_state",
        "workspace_collaboration_crdt_updates",
        "workspace_collaboration_crdt_snapshots",
    }
    object_names = {item["name"] for item in migration.TARGET_OBJECTS}
    if set(lineage.get("required_additive_tables", [])) != required_tables or not required_tables <= object_names:
        _error(errors, "SCHEMA_TABLE_SET_DRIFT", "schema_lineage.required_additive_tables", "table set drift")
    if lineage.get("application_import_applies_migration") is not False or lineage.get("forward_only") is not True:
        _error(errors, "MIGRATION_BOUNDARY_DRIFT", "schema_lineage", "migration boundary weakened")

    ticket = contract.get("ticket_authority", {})
    ticket_expected = {
        "stored_secret_material": "sha256_digest_only",
        "plaintext_ticket_persisted": False,
        "csrf_required_before_issue": True,
        "current_canonical_role_and_epoch_required": True,
        "recognized_ticket_burned_before_accept_or_reject": True,
        "one_time_consumption": True,
        "maximum_ttl_ms": 30000,
        "non_oracle_rejection": "collaboration ticket rejected",
    }
    if not isinstance(ticket, dict) or any(ticket.get(key) != value for key, value in ticket_expected.items()):
        _error(errors, "TICKET_AUTHORITY_DRIFT", "ticket_authority", "ticket contract weakened")

    update = contract.get("durable_update_authority", {})
    if (
        update.get("selected_candidate_id") != SELECTED_CANDIDATE_ID
        or update.get("wire_format") != WIRE_FORMAT
        or update.get("payload_encoding") != YJS_UPDATE_ENCODING
        or update.get("maximum_payload_bytes") != 1_048_576
        or update.get("runtime_binding_sha256") != runtime_binding_sha256()
        or update.get("same_operation_same_request") != "original_ack"
        or update.get("same_operation_different_request") != "operation_conflict"
        or update.get("response_loss_retry_duplicate_effect_count") != 0
        or len(update.get("fault_points_full_rollback", [])) != 5
    ):
        _error(errors, "DURABLE_UPDATE_DRIFT", "durable_update_authority", "durable update contract drift")
    expected_order = [
        "current_authority_and_epoch_revalidation",
        "operation_replay_or_conflict_check",
        "client_sequence_and_ack_bound_check",
        "opaque_update_and_monotonic_state_write",
        "canonical_event_write",
        "canonical_audit_write",
        "canonical_outbox_write",
        "immutable_operation_result_write",
        "commit_before_ack",
    ]
    if update.get("canonical_transaction_order") != expected_order:
        _error(errors, "ATOMIC_ORDER_DRIFT", "durable_update_authority.canonical_transaction_order", "order drift")

    replay = contract.get("replay_and_compaction", {})
    replay_expected = {
        "replay_order": "strictly_ascending_document_sequence",
        "maximum_replay_batch": 256,
        "current_epoch_revalidated_before_payload_read": True,
        "compacted_cursor_returns_snapshot_then_tail": True,
        "snapshot_and_state_vector_hashes_verified_against_exact_bytes": True,
        "ordered_update_hashes_and_high_water_revalidated_before_snapshot_commit": True,
        "base_snapshot_change_fails_closed": True,
        "python_inspects_yjs_payload_semantics": False,
    }
    if not isinstance(replay, dict) or any(replay.get(key) != value for key, value in replay_expected.items()):
        _error(errors, "REPLAY_COMPACTION_DRIFT", "replay_and_compaction", "replay contract weakened")

    boundary = contract.get("prototype_boundary", {})
    if boundary.get("temporary_sqlite_catalogs_only_for_gate_evidence") is not True or any(
        boundary.get(key) is not False
        for key in (
            "network_listener_started",
            "service_or_port_started",
            "live_identity_imported",
            "live_or_existing_per_user_database_opened",
            "development_pair_changed",
            "capability_enabled",
            "production_target_changed",
        )
    ):
        _error(errors, "BOUNDARY_FALSE_GREEN", "prototype_boundary", "external boundary claim drift")
    claims = contract.get("claims", {})
    if (
        claims.get("durable_ticket_event_ack_replay_snapshot_and_epoch_store_implemented") is not True
        or claims.get("selected_crdt_payload_and_compaction_binding_implemented") is not True
        or claims.get("atomic_operation_event_audit_outbox_commit_implemented") is not True
        or claims.get("T0B_P1_gate_passed") is not True
        or claims.get("actual_gateway_transport_implemented") is not False
        or claims.get("runtime_or_feature_enabled") is not False
        or claims.get("product_complete") is not False
    ):
        _error(errors, "CLAIM_DRIFT", "claims", "packet/product claim drift")

    unique = {(item["code"], item["subject"], item["message"]): item for item in errors}
    final_errors = [unique[key] for key in sorted(unique)]
    report = {
        "report_schema_version": 1,
        "contract": DURABLE_TRANSPORT_CONTRACT + ".verification",
        "status": "pass" if not final_errors else "fail",
        "error_count": len(final_errors),
        "errors": final_errors,
        "summary": {
            "packet_id": "T0B-P1",
            "source_version": migration.SOURCE_VERSION,
            "target_version": migration.TARGET_VERSION,
            "target_object_count": len(migration.TARGET_OBJECTS),
            "runtime_binding_sha256": runtime_binding_sha256(),
            "new_test_count": contract.get("validation", {}).get("new_test_count"),
            "actual_gateway_transport_implemented": claims.get("actual_gateway_transport_implemented"),
            "product_complete": claims.get("product_complete"),
        },
    }
    return report, 0 if not final_errors else 1


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
