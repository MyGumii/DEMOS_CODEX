"""Verify the O0A-P1 coordinated backup and restore design contract.

The verifier is deliberately read-only.  It binds the ADR to the current SBE
instruction set, the existing P7.4 implementation and L7 baseline, and the
internal-collaboration packet catalog.  It never creates a backup, opens a
runtime data root, restores data, imports the web application, or starts a
service.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from code_assist_v1.tools.internal_collaborative_validation import (
    DEFAULT_CATALOG,
    DEFAULT_LADDER,
    PROJECT_ROOT,
    repository_path,
    sha256_file,
)
from code_assist_v1.tools.verify_notion_parity_catalog import (
    JsonSchemaValidator,
    load_json,
)


REPORT_SCHEMA_VERSION = 1
CONTRACT_ROOT = PROJECT_ROOT / "docs" / "notion_parity" / "core_free_web_ui"
DEFAULT_ADR = CONTRACT_ROOT / "internal_collaborative_backup_adr.v1.json"
DEFAULT_SCHEMA = CONTRACT_ROOT / "internal_collaborative_backup_adr.v1.schema.json"

EXPECTED_PLANNING_PATHS = (
    "code_assist_v1/docs/notion_parity/core_free_web_ui/INTERNAL_COLLABORATIVE_NOTION_ULTRA_EXECUTION_PLAN_V3.md",
    "code_assist_v1/docs/notion_parity/core_free_web_ui/INTERNAL_COLLABORATIVE_NOTION_XHIGH_IMPLEMENTATION_HANDOFF_V2.md",
)
EXPECTED_SBE_PATHS = (
    "code_assist_v1/docs/knowledge_spaces/README.md",
    "code_assist_v1/docs/knowledge_spaces/second_brain_evolution/README.md",
    "code_assist_v1/docs/knowledge_spaces/second_brain_evolution/PRODUCT_CONTRACT.md",
    "code_assist_v1/docs/knowledge_spaces/second_brain_evolution/FORWARD_SCHEMA.md",
    "code_assist_v1/docs/knowledge_spaces/second_brain_evolution/EXECUTION_PLAN.md",
    "code_assist_v1/docs/knowledge_spaces/second_brain_evolution/ACCEPTANCE.md",
    "code_assist_v1/docs/knowledge_spaces/DECISIONS.md",
    "code_assist_v1/docs/knowledge_spaces/ARCHITECTURE.md",
    "code_assist_v1/docs/knowledge_spaces/SCHEMA_V1.md",
    "code_assist_v1/docs/knowledge_spaces/API_UI_CONTRACT.md",
    "code_assist_v1/docs/knowledge_spaces/CURRENT_SYSTEM_INVENTORY.md",
    "code_assist_v1/docs/knowledge_spaces/MIGRATION_ROLLOUT.md",
    "code_assist_v1/docs/knowledge_spaces/TEST_OPERATIONS.md",
    "code_assist_v1/docs/knowledge_spaces/DEVELOPMENT_BASELINE.md",
    "code_assist_v1/docs/knowledge_spaces/IMPLEMENTATION_BACKLOG.md",
    "code_assist_v1/docs/knowledge_spaces/TRACEABILITY.md",
    "code_assist_v1/docs/knowledge_spaces/GOAL_CONTRACT.json",
    "code_assist_v1/docs/knowledge_spaces/STATUS.md",
)
EXPECTED_P7_IMPLEMENTATION_PATHS = (
    "code_assist_v1/knowledge_core/backup_manifest.py",
    "code_assist_v1/knowledge_core/services/backup.py",
    "code_assist_v1/knowledge_core/services/restore.py",
)
EXPECTED_L7_TESTS = (
    "code_assist_v1/tests/knowledge_spaces/unit/test_backup_service.py",
    "code_assist_v1/tests/knowledge_spaces/unit/test_backup_retention.py",
    "code_assist_v1/tests/knowledge_spaces/unit/test_backup_repository.py",
    "code_assist_v1/tests/knowledge_spaces/unit/test_backup_manifest.py",
    "code_assist_v1/tests/knowledge_spaces/unit/test_online_sqlite_backup.py",
    "code_assist_v1/tests/knowledge_spaces/integration/test_backup_restore_cli.py",
    "code_assist_v1/tests/knowledge_spaces/integration/test_backup_multiprocess.py",
    "code_assist_v1/tests/knowledge_spaces/integration/test_restore_drill.py",
)
EXPECTED_FUTURE_O0A_P2_TESTS = (
    "code_assist_v1/tests/notion_collaboration/test_backup_high_water.py",
    "code_assist_v1/tests/notion_collaboration/test_restore_new_root.py",
    "code_assist_v1/tests/notion_collaboration/test_restore_replay_integrity.py",
)
EXPECTED_DELIVERABLES = {
    "write_barrier_backup_generation_and_restore_ADR",
    "separate_notion_and_sbe_database_authority_manifest_contract",
    "event_snapshot_compaction_high_water_contract",
    "mandatory_and_rebuildable_component_inventory",
    "secret_rotation_and_forced_reauthentication_restore_contract",
}
EXPECTED_MANDATORY_IDS = (
    "notion_canonical_database",
    "sbe_canonical_database",
    "shared_identity_registry",
    "notion_immutable_blobs",
    "sbe_immutable_blobs",
    "collaboration_event_log",
    "collaboration_snapshots_and_checkpoints",
    "legacy_cutover_sources",
    "authority_schema_and_config_markers",
    "encrypted_key_metadata",
    "audit_outbox_and_publication_high_water",
)
EXPECTED_REBUILDABLE_IDS = (
    "notion_compatibility_projections",
    "sbe_markdown_index_log_graph_and_backlinks",
    "fts_vector_and_search_caches",
    "derived_inbox_and_notification_views",
    "generated_previews_and_thumbnails",
    "materialized_collaboration_client_views",
)
EXPECTED_NEVER_RESTORE_IDS = (
    "sessions_csrf_tokens_tickets_and_bearer_tokens",
    "session_signing_secrets_cookie_keys_and_plaintext_encryption_keys",
    "ephemeral_presence_typing_and_cursor_state",
    "gateway_connections_queues_and_backpressure_counters",
    "browser_service_worker_and_local_caches",
    "temporary_staging_partial_generations_and_unverified_exports",
)
EXPECTED_MANIFEST_TOP_LEVEL_FIELDS = (
    "format_id",
    "generation_id",
    "state",
    "created_at",
    "completed_at",
    "write_barrier_id",
    "coordinator_config_sha256",
    "component_inventory_sha256",
    "publication_boundary",
    "authority_records",
    "component_records",
    "manifest_sha256",
)
EXPECTED_AUTHORITY_RECORD_FIELDS = (
    "authority_domain",
    "snapshot_id",
    "snapshot_sha256",
    "snapshot_size_bytes",
    "schema_version",
    "schema_lineage_sha256",
    "blob_manifest_sha256",
    "high_water",
    "quick_check",
    "foreign_key_violation_count",
    "logical_inventory_sha256",
    "verification_state",
)
EXPECTED_HIGH_WATER_FIELDS = (
    "audit_id",
    "outbox_id",
    "document_sequence",
    "event_sequence",
    "snapshot_event_sequence",
    "publication_revision",
)
EXPECTED_COMPONENT_RECORD_FIELDS = (
    "component_id",
    "authority_domain",
    "content_sha256",
    "size_bytes",
    "verification_state",
)
EXPECTED_BARRIER_STATES = (
    "declare_generation_intent",
    "acquire_per_authority_backup_and_blob_gc_holds",
    "stop_accepting_new_mutations",
    "drain_inflight_mutations_to_configured_deadline",
    "capture_per_authority_high_waters",
    "snapshot_each_authority_independently",
    "copy_and_verify_mandatory_non_rebuildable_components",
    "revalidate_no_mutation_crossed_the_recorded_boundaries",
    "finalize_self_hashed_verified_generation_after_all_checks",
    "release_holds_and_resume_mutations",
)
EXPECTED_RESTORE_STEPS = (
    "verify_generation_manifest_self_hash_state_and_complete_component_denominator",
    "verify_each_authority_snapshot_hash_schema_lineage_quick_check_and_foreign_keys",
    "verify_logical_inventory_blob_references_and_event_snapshot_continuity",
    "restore_notion_second_brain_and_identity_authorities_independently_into_new_root",
    "discard_all_restored_sessions_tokens_tickets_transport_state_and_secret_material",
    "provision_new_session_signing_secret_and_rotate_relevant_key_versions",
    "force_reauthentication_for_every_subject",
    "rebuild_every_projection_and_cache_from_canonical_authorities",
    "verify_logical_counts_high_waters_replay_and_projection_zero_mismatch",
    "emit_restore_receipt_without_enabling_traffic_or_production_mutations",
)
EXPECTED_INTEGRITY_CHECKS = (
    "manifest_hashes",
    "schema_and_migration_lineage",
    "sqlite_quick_check",
    "foreign_key_check",
    "logical_inventory_counts_and_hashes",
    "immutable_blob_manifest_and_references",
    "audit_and_outbox_high_waters",
    "event_sequence_continuity",
    "snapshot_replay_equivalence",
    "projection_zero_canonical_mismatch",
)
EXPECTED_COMPACTION_FENCES = (
    "snapshot_is_verified_and_self_hashed",
    "snapshot_covers_an_exact_event_high_water",
    "coordinated_verified_backup_generation_contains_the_snapshot_and_required_replay_tail",
    "replay_from_snapshot_to_current_head_has_zero_gap_duplicate_or_state_mismatch",
    "configured_minimum_event_and_page_history_retention_is_satisfied",
    "no_active_recovery_or_backup_hold_requires_the_candidate_events",
)
EXPECTED_G0_U_FIELDS = {
    "rpo",
    "rto",
    "write_drain_timeout",
    "backup_retention",
    "page_history_retention",
    "external_destination",
    "restore_secret_policy",
    "external_key_owner",
}


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _ordered(errors: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    unique = {(item["code"], item["subject"], item["message"]): item for item in errors}
    return [unique[key] for key in sorted(unique)]


def _load(path: Path, label: str, errors: list[dict[str, str]]) -> Any | None:
    try:
        return load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(_error(f"{label}_READ_FAILED", str(path), str(exc)))
        return None


def _expect(
    condition: bool,
    errors: list[dict[str, str]],
    code: str,
    subject: str,
    message: str,
) -> None:
    if not condition:
        errors.append(_error(code, subject, message))


def _validate_hash_refs(
    refs: Any,
    *,
    expected_paths: Sequence[str],
    subject: str,
    errors: list[dict[str, str]],
) -> None:
    if not isinstance(refs, list):
        errors.append(_error("HASH_REFS_INVALID", subject, "hash references must be an array"))
        return
    paths = tuple(item.get("path") for item in refs if isinstance(item, dict))
    _expect(
        paths == tuple(expected_paths),
        errors,
        "AUTHORITY_PATH_SET_DRIFT",
        subject,
        "authority paths or required order changed",
    )
    for index, item in enumerate(refs):
        item_subject = f"{subject}[{index}]"
        if not isinstance(item, dict):
            errors.append(_error("HASH_REF_INVALID", item_subject, "hash reference must be an object"))
            continue
        path = item.get("path")
        resolved = repository_path(path) if isinstance(path, str) else None
        if resolved is None or not resolved.is_file():
            errors.append(_error("AUTHORITY_PATH_INVALID", item_subject + ".path", str(path)))
            continue
        actual = sha256_file(resolved)
        _expect(
            item.get("sha256") == actual,
            errors,
            "AUTHORITY_HASH_DRIFT",
            item_subject + ".sha256",
            f"expected {item.get('sha256')}, observed {actual}",
        )


def _validate_authority_inputs(adr: dict[str, Any], errors: list[dict[str, str]]) -> None:
    inputs = adr.get("authority_inputs", {})
    if not isinstance(inputs, dict):
        return
    _validate_hash_refs(
        [inputs.get("agents_md")],
        expected_paths=("code_assist_v1/AGENTS.md",),
        subject="$.authority_inputs.agents_md",
        errors=errors,
    )
    _validate_hash_refs(
        inputs.get("planning_documents"),
        expected_paths=EXPECTED_PLANNING_PATHS,
        subject="$.authority_inputs.planning_documents",
        errors=errors,
    )
    _validate_hash_refs(
        inputs.get("p7_4_implementation_documents"),
        expected_paths=EXPECTED_P7_IMPLEMENTATION_PATHS,
        subject="$.authority_inputs.p7_4_implementation_documents",
        errors=errors,
    )

    manifest = inputs.get("sbe_instruction_manifest")
    if not isinstance(manifest, list):
        errors.append(_error("SBE_MANIFEST_INVALID", "$.authority_inputs.sbe_instruction_manifest", "expected an array"))
    else:
        paths = tuple(
            item[0]
            for item in manifest
            if isinstance(item, list) and len(item) == 2
        )
        _expect(
            paths == EXPECTED_SBE_PATHS,
            errors,
            "SBE_MANIFEST_ORDER_DRIFT",
            "$.authority_inputs.sbe_instruction_manifest",
            "the AGENTS.md 18-document order must remain exact",
        )
        for index, item in enumerate(manifest):
            subject = f"$.authority_inputs.sbe_instruction_manifest[{index}]"
            if not isinstance(item, list) or len(item) != 2:
                errors.append(_error("SBE_MANIFEST_ENTRY_INVALID", subject, "expected [path, sha256]"))
                continue
            relative, expected_hash = item
            resolved = repository_path(relative) if isinstance(relative, str) else None
            if resolved is None or not resolved.is_file():
                errors.append(_error("SBE_PATH_INVALID", subject + "[0]", str(relative)))
                continue
            actual = sha256_file(resolved)
            _expect(
                expected_hash == actual,
                errors,
                "SBE_HASH_DRIFT",
                subject + "[1]",
                f"expected {expected_hash}, observed {actual}",
            )

    lineage = repository_path(
        "code_assist_v1/docs/knowledge_spaces/second_brain_evolution/FORWARD_SCHEMA.md"
    )
    if lineage is not None and lineage.is_file():
        _expect(
            inputs.get("current_schema_lineage_sha256") == sha256_file(lineage),
            errors,
            "SCHEMA_LINEAGE_DRIFT",
            "$.authority_inputs.current_schema_lineage_sha256",
            "the ADR must bind the current SBE forward schema",
        )
    audit = inputs.get("sbe_contract_audit", {})
    _expect(
        isinstance(audit, dict)
        and audit.get("status") == "pass"
        and audit.get("input_sha256") == "sha256:284d70c452fa1b742212d12286d5d3c7da9b7e9a2014d9b29163df2a805c9cf0"
        and audit.get("goal_count") == 20
        and audit.get("mandatory_scenario_count") == 284
        and audit.get("error_count") == 0,
        errors,
        "SBE_AUDIT_BINDING_DRIFT",
        "$.authority_inputs.sbe_contract_audit",
        "the fresh SBE report-schema-v2 pass must remain exact",
    )


def _ids(rows: Any) -> tuple[Any, ...]:
    if not isinstance(rows, list):
        return ()
    return tuple(item.get("id") for item in rows if isinstance(item, dict))


def _validate_inventory_and_authorities(adr: dict[str, Any], errors: list[dict[str, str]]) -> None:
    current = adr.get("current_state", {})
    p7 = current.get("p7_4_baseline", {}) if isinstance(current, dict) else {}
    _expect(
        isinstance(p7, dict)
        and p7.get("status") == "verified_existing_disabled_synthetic_baseline"
        and p7.get("retention_generations") == {"hourly": 96, "daily": 30, "monthly": 12},
        errors,
        "P7_BASELINE_FALSE_GREEN",
        "$.current_state.p7_4_baseline",
        "P7.4 must remain an existing disabled/synthetic baseline with its exact retention facts",
    )
    for field in (
        "collaborative_backup_runtime_implemented",
        "backup_or_restore_executed_by_this_adr",
        "live_root_or_database_accessed",
    ):
        _expect(
            isinstance(current, dict) and current.get(field) is False,
            errors,
            "CURRENT_STATE_FALSE_GREEN",
            f"$.current_state.{field}",
            f"{field} must remain false",
        )

    boundary = adr.get("authority_boundary", {})
    if isinstance(boundary, dict):
        for field in (
            "notion_and_sbe_database_authorities_separate",
            "notion_and_sbe_schema_lineages_separate",
            "identity_snapshot_includes_password_hashes_and_stable_subject_ids",
        ):
            _expect(boundary.get(field) is True, errors, "AUTHORITY_BOUNDARY_WEAKENED", f"$.authority_boundary.{field}", f"{field} must remain true")
        for field in (
            "cross_database_atomic_transaction_claimed",
            "projection_as_sole_truth_allowed",
            "identity_snapshot_includes_live_sessions_tokens_or_secret_material",
        ):
            _expect(boundary.get(field) is False, errors, "AUTHORITY_FALSE_CLAIM", f"$.authority_boundary.{field}", f"{field} must remain false")

    inventory = adr.get("component_inventory", {})
    mandatory = _ids(inventory.get("mandatory_non_rebuildable")) if isinstance(inventory, dict) else ()
    rebuildable = _ids(inventory.get("rebuildable")) if isinstance(inventory, dict) else ()
    never = _ids(inventory.get("never_restore")) if isinstance(inventory, dict) else ()
    _expect(mandatory == EXPECTED_MANDATORY_IDS, errors, "MANDATORY_COMPONENT_DENOMINATOR_DRIFT", "$.component_inventory.mandatory_non_rebuildable", repr(mandatory))
    _expect(rebuildable == EXPECTED_REBUILDABLE_IDS, errors, "REBUILDABLE_COMPONENT_DENOMINATOR_DRIFT", "$.component_inventory.rebuildable", repr(rebuildable))
    _expect(never == EXPECTED_NEVER_RESTORE_IDS, errors, "NEVER_RESTORE_DENOMINATOR_DRIFT", "$.component_inventory.never_restore", repr(never))
    all_ids = mandatory + rebuildable + never
    _expect(len(all_ids) == len(set(all_ids)) == 23, errors, "COMPONENT_CLASSIFICATION_OVERLAP", "$.component_inventory", "all 23 component IDs must be unique and classified exactly once")


def _validate_generation_restore_and_compaction(adr: dict[str, Any], errors: list[dict[str, str]]) -> None:
    manifest = adr.get("coordinated_generation_manifest_contract", {})
    expected_manifest = {
        "format_id": "internal-collaborative-coordinated-generation-v1",
        "manifest_is_self_hashed": True,
        "allowed_states": ["preparing", "incomplete", "verified"],
        "required_authority_records": ["notion", "second_brain", "identity"],
        "required_top_level_fields": list(EXPECTED_MANIFEST_TOP_LEVEL_FIELDS),
        "required_authority_record_fields": list(EXPECTED_AUTHORITY_RECORD_FIELDS),
        "required_high_water_fields": list(EXPECTED_HIGH_WATER_FIELDS),
        "required_component_record_fields": list(EXPECTED_COMPONENT_RECORD_FIELDS),
        "secret_material_allowed": False,
        "each_authority_snapshot_independently_verified": True,
        "cross_database_transaction_claimed": False,
        "verified_requires_all_mandatory_components": True,
        "incomplete_generation_is_restorable": False,
    }
    if not isinstance(manifest, dict) or any(manifest.get(key) != value for key, value in expected_manifest.items()):
        errors.append(_error("GENERATION_MANIFEST_CONTRACT_DRIFT", "$.coordinated_generation_manifest_contract", repr(manifest)))

    barrier = adr.get("write_barrier_contract", {})
    _expect(
        isinstance(barrier, dict) and tuple(barrier.get("ordered_states", [])) == EXPECTED_BARRIER_STATES,
        errors,
        "WRITE_BARRIER_ORDER_DRIFT",
        "$.write_barrier_contract.ordered_states",
        "the ten-state barrier order must remain exact",
    )
    for field in ("write_crossing_boundary_allowed", "false_completion_allowed"):
        _expect(isinstance(barrier, dict) and barrier.get(field) is False, errors, "WRITE_BARRIER_FALSE_GREEN", f"$.write_barrier_contract.{field}", f"{field} must remain false")
    _expect(
        isinstance(barrier, dict) and barrier.get("drain_timeout_source") == "pending_G0_U_recovery_decision",
        errors,
        "WRITE_DRAIN_DECISION_BYPASS",
        "$.write_barrier_contract.drain_timeout_source",
        "O0A-P1 cannot freeze the production write-drain timeout",
    )

    restore = adr.get("restore_contract", {})
    _expect(isinstance(restore, dict) and tuple(restore.get("ordered_steps", [])) == EXPECTED_RESTORE_STEPS, errors, "RESTORE_ORDER_DRIFT", "$.restore_contract.ordered_steps", "the restore order must remain exact")
    _expect(isinstance(restore, dict) and tuple(restore.get("required_integrity_checks", [])) == EXPECTED_INTEGRITY_CHECKS, errors, "RESTORE_INTEGRITY_DENOMINATOR_DRIFT", "$.restore_contract.required_integrity_checks", "all ten restore checks are mandatory")
    expected_restore = {
        "target": "new_empty_runner_owned_root_only",
        "existing_or_live_root_overwrite_allowed": False,
        "outbound_integrations_enabled_during_restore": False,
        "mutation_traffic_enabled_during_restore": False,
        "session_policy": "rotate_session_signing_secret_and_force_reauthentication",
        "copied_session_registry_may_revive_sessions": False,
        "traffic_activation_requires_separate_approval_and_later_gate": True,
        "restore_failure_claims_success": False,
    }
    if not isinstance(restore, dict) or any(restore.get(key) != value for key, value in expected_restore.items()):
        errors.append(_error("RESTORE_SAFETY_BOUNDARY_DRIFT", "$.restore_contract", repr(restore)))

    compaction = adr.get("event_snapshot_compaction_contract", {})
    _expect(isinstance(compaction, dict) and tuple(compaction.get("event_deletion_allowed_only_after", [])) == EXPECTED_COMPACTION_FENCES, errors, "COMPACTION_FENCE_DENOMINATOR_DRIFT", "$.event_snapshot_compaction_contract.event_deletion_allowed_only_after", "all six compaction fences are required")
    expected_compaction = {
        "event_high_water_monotonic_no_reuse": True,
        "compaction_below_unverified_snapshot_allowed": False,
        "compaction_invalidates_immutable_sbe_publication_revision": False,
        "page_revision_auto_delete_allowed": False,
        "page_history_compaction_policy_source": "pending_G0_U_recovery_decision",
    }
    if not isinstance(compaction, dict) or any(compaction.get(key) != value for key, value in expected_compaction.items()):
        errors.append(_error("COMPACTION_SAFETY_BOUNDARY_DRIFT", "$.event_snapshot_compaction_contract", repr(compaction)))


def _validate_decisions_and_claims(adr: dict[str, Any], errors: list[dict[str, str]]) -> None:
    decisions = adr.get("recovery_decision_boundary", {})
    _expect(
        isinstance(decisions, dict)
        and decisions.get("decision_id") == "G0-U"
        and decisions.get("status") == "pending"
        and set(decisions.get("must_freeze_before_production_rehearsal", [])) == EXPECTED_G0_U_FIELDS,
        errors,
        "G0_U_DECISION_BOUNDARY_DRIFT",
        "$.recovery_decision_boundary",
        "all eight production recovery choices remain pending G0-U",
    )
    _expect(
        isinstance(decisions, dict)
        and decisions.get("existing_sbe_baseline_objectives_not_product_freeze") == {"rpo_minutes": 60, "rto_minutes": 240}
        and decisions.get("provisional_engineering_targets_not_product_freeze") == {"rpo_minutes": 15, "rto_minutes": 60, "write_drain_timeout_seconds": 60}
        and decisions.get("retention_destination_and_key_owner_selected") is False
        and decisions.get("production_recovery_contract_frozen_by_O0A_P1") is False,
        errors,
        "RECOVERY_TARGET_FALSE_FREEZE",
        "$.recovery_decision_boundary",
        "baseline observations and provisional targets must not masquerade as the production contract",
    )

    claims = adr.get("claims", {})
    for field in (
        "coordinated_backup_design_contract_accepted",
        "component_denominator_frozen_for_O0A_P2",
        "existing_p7_4_baseline_reused_read_only",
    ):
        _expect(isinstance(claims, dict) and claims.get(field) is True, errors, "CONTRACT_FALSE_NEGATIVE", f"$.claims.{field}", f"{field} must remain true")
    for field in (
        "collaborative_backup_runtime_implemented",
        "coordinated_backup_executed",
        "new_root_collaborative_restore_executed",
        "target_restore_executed",
        "production_recovery_contract_frozen",
        "sbe_successor_backup_format_modified",
        "production_schema_route_store_runtime_or_data_root_modified",
        "product_complete",
    ):
        _expect(isinstance(claims, dict) and claims.get(field) is False, errors, "CONTRACT_FALSE_GREEN", f"$.claims.{field}", f"{field} must remain false")


def _validate_catalog_and_lane(
    adr: dict[str, Any],
    *,
    catalog_path: Path,
    ladder_path: Path,
    errors: list[dict[str, str]],
) -> None:
    catalog = _load(catalog_path, "CATALOG", errors)
    ladder = _load(ladder_path, "LADDER", errors)
    if isinstance(catalog, dict):
        packet = next((item for item in catalog.get("packets", []) if isinstance(item, dict) and item.get("id") == "O0A-P1"), None)
        if packet is None:
            errors.append(_error("PACKET_MISSING", "packet:O0A-P1", "required packet missing"))
        else:
            _expect(set(packet.get("deliverables", [])) == EXPECTED_DELIVERABLES, errors, "PACKET_DELIVERABLE_DRIFT", "packet:O0A-P1", repr(packet.get("deliverables")))
            _expect(
                packet.get("prerequisites") == ["A0A-P1", "E0-P1"]
                and packet.get("external_prerequisites") == ["XG-SBE-DYNAMIC-BASELINE"]
                and packet.get("sbe_preflight_required") is True
                and packet.get("approval") is None
                and packet.get("validation_ids") == ["L7-KNOWLEDGE-BACKUP-BASELINE"]
                and packet.get("status") in {"planned", "gate_passed"},
                errors,
                "PACKET_BOUNDARY_DRIFT",
                "packet:O0A-P1",
                repr(packet),
            )

    lane = None
    if isinstance(ladder, dict):
        lane = next(
            (
                item
                for rung in ladder.get("rungs", [])
                if isinstance(rung, dict)
                for item in rung.get("lanes", [])
                if isinstance(item, dict) and item.get("id") == "L7-KNOWLEDGE-BACKUP-BASELINE"
            ),
            None,
        )
    if lane is None:
        errors.append(_error("L7_BASELINE_MISSING", "lane:L7-KNOWLEDGE-BACKUP-BASELINE", "required lane missing"))
    else:
        lane_tests = tuple(item for item in lane.get("argv", []) if isinstance(item, str) and item.endswith(".py"))
        _expect(lane.get("state") == "existing_baseline" and lane_tests == EXPECTED_L7_TESTS, errors, "L7_BASELINE_TEST_DRIFT", "lane:L7-KNOWLEDGE-BACKUP-BASELINE", repr(lane_tests))

    validation = adr.get("validation_contract", {})
    tests = validation.get("existing_baseline_tests") if isinstance(validation, dict) else None
    _validate_hash_refs(tests, expected_paths=EXPECTED_L7_TESTS, subject="$.validation_contract.existing_baseline_tests", errors=errors)
    _expect(
        isinstance(validation, dict)
        and tuple(validation.get("future_collaborative_restore_tests_owned_by_O0A_P2", [])) == EXPECTED_FUTURE_O0A_P2_TESTS
        and validation.get("sbe_baseline_scenarios") == ["OPS-001", "OPS-002", "OPS-003", "OPS-004", "OPS-005", "OPS-006"]
        and validation.get("sbe_successor_restore_scenarios") == ["SBEOPS-010", "SBEOPS-018"]
        and validation.get("target_restore_required_for_product_closure") is True,
        errors,
        "VALIDATION_BOUNDARY_DRIFT",
        "$.validation_contract",
        "O0A-P1 baseline and future O0A-P2/target denominators changed",
    )


def verify_backup_adr(
    *,
    adr_path: Path = DEFAULT_ADR,
    schema_path: Path = DEFAULT_SCHEMA,
    catalog_path: Path = DEFAULT_CATALOG,
    ladder_path: Path = DEFAULT_LADDER,
) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    adr = _load(Path(adr_path), "ADR", errors)
    schema = _load(Path(schema_path), "SCHEMA", errors)
    if isinstance(adr, dict) and isinstance(schema, dict):
        for item in JsonSchemaValidator(schema).validate(adr):
            errors.append(_error("ADR_SCHEMA_" + item["code"], item["subject"], item["message"]))
        _validate_authority_inputs(adr, errors)
        _validate_inventory_and_authorities(adr, errors)
        _validate_generation_restore_and_compaction(adr, errors)
        _validate_decisions_and_claims(adr, errors)
        _validate_catalog_and_lane(
            adr,
            catalog_path=Path(catalog_path),
            ladder_path=Path(ladder_path),
            errors=errors,
        )
    ordered = _ordered(errors)
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "contract": "code_assist.notion.internal_collaborative_backup_adr.audit.v1",
        "status": "pass" if not ordered else "fail",
        "error_count": len(ordered),
        "errors": ordered,
        "adr_sha256": sha256_file(Path(adr_path)) if Path(adr_path).is_file() else None,
        "schema_sha256": sha256_file(Path(schema_path)) if Path(schema_path).is_file() else None,
        "summary": {
            "packet_id": adr.get("packet_id") if isinstance(adr, dict) else None,
            "sbe_manifest_count": len(adr.get("authority_inputs", {}).get("sbe_instruction_manifest", [])) if isinstance(adr, dict) else 0,
            "mandatory_component_count": len(adr.get("component_inventory", {}).get("mandatory_non_rebuildable", [])) if isinstance(adr, dict) else 0,
            "rebuildable_component_count": len(adr.get("component_inventory", {}).get("rebuildable", [])) if isinstance(adr, dict) else 0,
            "never_restore_component_count": len(adr.get("component_inventory", {}).get("never_restore", [])) if isinstance(adr, dict) else 0,
            "production_recovery_contract_frozen": adr.get("claims", {}).get("production_recovery_contract_frozen") if isinstance(adr, dict) else None,
            "runtime_implemented": adr.get("claims", {}).get("collaborative_backup_runtime_implemented") if isinstance(adr, dict) else None,
            "product_complete": adr.get("claims", {}).get("product_complete") if isinstance(adr, dict) else None,
        },
    }
    return report, 0 if not ordered else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adr", type=Path, default=DEFAULT_ADR)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--ladder", type=Path, default=DEFAULT_LADDER)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report, exit_code = verify_backup_adr(
        adr_path=args.adr,
        schema_path=args.schema,
        catalog_path=args.catalog,
        ladder_path=args.ladder,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
