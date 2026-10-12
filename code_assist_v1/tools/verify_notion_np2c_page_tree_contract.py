"""Strict read-only verifier for the NP-2C page-tree/lifecycle design."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = (
    PROJECT_ROOT / "docs" / "notion_parity" / "np2c_page_tree_lifecycle_contract.json"
)


class DuplicateJsonKeyError(ValueError):
    pass


def _object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def load_contract(path: str | Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    value = json.loads(
        Path(path).read_text(encoding="utf-8"),
        object_pairs_hook=_object,
        parse_constant=lambda item: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {item}")
        ),
    )
    if not isinstance(value, dict):
        raise ValueError("NP-2C contract root must be an object")
    return value


def validate_contract(value: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    require(value.get("contract_version") == 19, "contract_version must be 19")
    require(
        value.get("name") == "np2c_canonical_page_tree_lifecycle",
        "invalid contract name",
    )
    require(
        value.get("status")
        == "official_archive_source_contract_designed_source_v5_and_workspace_1318_blocked_default_off",
        "status must remain official_archive_source_contract_designed_source_v5_and_workspace_1318_blocked_default_off",
    )
    require(
        value.get("reference_state")
        == "official_reference_only_live_characterization_pending",
        "live characterization must remain pending",
    )

    implementation = value.get("implementation")
    require(isinstance(implementation, dict), "implementation must be an object")
    if isinstance(implementation, dict):
        require(
            implementation.get("current_workspace_schema_version") == 2026081317,
            "current workspace schema must remain 2026081317",
        )
        require(
            implementation.get("candidate_workspace_schema_version") == 2026081318,
            "candidate workspace schema must be 2026081318",
        )
        require(implementation.get("schema_required") is True, "1312 schema must be required")
        for field in (
            "routes_implemented",
            "ui_implemented",
            "live_migration_applied",
            "runtime_commands_enabled",
            "permanent_delete_enabled",
            "resolver_cutover_enabled",
            "continuous_worker_enabled",
            "destructive_watermark_ack_enabled",
        ):
            require(implementation.get(field) is False, f"{field} must be false")
        for field in (
            "schema_required",
            "schema_implemented",
            "migration_implemented",
            "tree_materializer_implemented",
            "tree_snapshot_api_implemented",
            "lifecycle_kernel_guards_implemented",
            "command_kernel_implemented",
            "source_effect_orchestration_implemented",
            "rename_source_adapter_contract_designed",
            "rename_source_adapter_implemented",
            "rename_catalog_finalize_contract_designed",
            "candidate_1313_schema_required",
            "rename_catalog_finalize_implemented",
            "candidate_1313_schema_implemented",
            "rename_end_to_end_rehearsal_implemented",
            "create_move_reorder_contract_designed",
            "candidate_1314_schema_required",
            "candidate_1314_schema_implemented",
            "candidate_1315_schema_required",
            "candidate_1315_schema_implemented",
            "source_adapters_implemented",
            "create_move_reorder_source_adapters_implemented",
            "create_move_reorder_catalog_finalizers_implemented",
            "create_move_reorder_rehearsal_implemented",
            "duplicate_trash_restore_contract_designed",
            "duplicate_trash_restore_source_adapters_implemented",
            "candidate_1316_schema_required",
            "candidate_1316_schema_implemented",
            "candidate_1317_schema_required",
            "candidate_1317_schema_implemented",
            "duplicate_trash_restore_catalog_finalizers_implemented",
            "duplicate_trash_restore_rehearsal_implemented",
            "duplicate_trash_restore_independent_audit_completed",
            "official_archive_unarchive_contract_designed",
            "candidate_1318_schema_required",
            "legacy_v1_kernel_fail_before_source",
        ):
            require(implementation.get(field) is True, f"{field} must be true")
        for field in (
            "official_archive_unarchive_source_contract_implemented",
            "candidate_1318_schema_implemented",
            "official_archive_catalog_finalizer_implemented",
            "official_archive_rehearsal_implemented",
        ):
            require(implementation.get(field) is False, f"{field} must be false")
        require(
            implementation.get("tree_materializer_scope")
            == "offline_initial_unmaterialized_to_complete_only",
            "tree materializer scope is invalid",
        )
        require(
            implementation.get("command_kernel_scope")
            == "disabled_rename_protocol_with_injected_temp_source_effect",
            "command kernel scope is invalid",
        )

    gaps = set(value.get("current_gaps", []))
    for gap in (
        "create_id_derivation_at_future_runtime_boundary_is_not_implemented",
        "dependent_reference_full_sweep_refresh_is_not_implemented",
        "runtime_lifecycle_service_is_not_registered",
        "tree_refresh_after_initial_materialization_is_not_implemented",
        "lifecycle_subtree_cutover_evidence_is_not_complete",
        "official_archive_and_trash_are_not_distinct_in_the_legacy_source",
        "official_archive_live_characterization_matrix_is_incomplete",
        "h5e_source_v4_predecessor_is_not_implemented",
        "official_archive_catalog_authority_is_not_implemented",
        "catalog_and_source_mutations_are_not_one_transaction",
        "legacy_restore_implicitly_restores_ancestors",
        "legacy_permanent_delete_cleans_files_after_database_commit",
    ):
        require(gap in gaps, f"missing current gap: {gap}")

    authority = value.get("authority")
    require(isinstance(authority, dict), "authority must be an object")
    if isinstance(authority, dict):
        for field in (
            "legacy_source_remains_content_authority",
            "legacy_source_remains_physical_tree_authority_before_cutover",
            "catalog_tree_is_generation_fenced_projection",
        ):
            require(authority.get(field) is True, f"{field} must be true")
        require(
            authority.get("cross_database_exactly_once_claimed") is False,
            "cross-database exactly-once must not be claimed",
        )
        require(
            authority.get("delivery_semantics") == "at_least_once_state_idempotent",
            "delivery semantics must be at-least-once/state-idempotent",
        )
        require(
            authority.get("directory_scan_fallback_allowed") is False,
            "directory scans must remain forbidden",
        )
        require(
            authority.get("source_or_network_io_inside_catalog_write_transaction") is False,
            "catalog writer transactions must contain no source/network I/O",
        )

    identity = value.get("canonical_identity")
    require(isinstance(identity, dict), "canonical_identity must be an object")
    if isinstance(identity, dict):
        require(
            identity.get("page_locator")
            == ["workspace_id", "source_owner_subject_id", "source_page_id"],
            "page locator identity is invalid",
        )
        for field in (
            "parent_locator_uses_full_owner_page_identity",
            "binding_generation_required",
            "source_incarnation_id_required",
            "source_incarnation_generation_required",
            "full_source_fence_required",
            "page_change_generation_required",
            "workspace_tree_version_required",
        ):
            require(identity.get(field) is True, f"{field} must be true")
        require(identity.get("bare_page_id_parent_allowed") is False, "bare parent IDs forbidden")
        require(
            identity.get("recreated_id_inherits_prior_identity") is False,
            "recreated IDs must not inherit prior identity",
        )

    schema = value.get("candidate_schema")
    require(isinstance(schema, dict), "candidate_schema must be an object")
    if isinstance(schema, dict):
        require(schema.get("source_version") == 2026081310, "schema source must be 1310")
        require(schema.get("target_version") == 2026081311, "schema target must be 1311")
        require(
            schema.get("required_concerns")
            == [
                "workspace_page_tree_state",
                "workspace_page_tree_nodes",
                "workspace_page_lifecycle_command_saga",
            ],
            "candidate schema concerns are invalid",
        )
        require(
            schema.get("existing_workspaces_initial_materialization") == "unmaterialized",
            "existing workspaces must start unmaterialized",
        )
        for field in (
            "fabricate_tree_rows_during_migration",
            "fabricate_lifecycle_receipts_during_migration",
            "request_or_startup_migration_enabled",
        ):
            require(schema.get(field) is False, f"{field} must be false")
        require(
            schema.get("frozen_1310_contract_must_remain_importable") is True,
            "frozen 1310 must remain importable",
        )

    evidence = value.get("frozen_schema_evidence")
    require(isinstance(evidence, dict), "frozen_schema_evidence must be an object")
    if isinstance(evidence, dict):
        require(
            evidence.get("source_1310_checksum")
            == "sha256:72058812be6f0b92db756d5648d2cd9a0741d4ad4f4f9c62377d128ca0b581b3",
            "frozen 1310 checksum is invalid",
        )
        require(
            evidence.get("source_1310_actual_schema_sha256")
            == "sha256:d8935af134fa12cdd5f0f803e15cba798dece1831cfeeee7673e0dc0ef7d7b33",
            "frozen 1310 actual schema hash is invalid",
        )
        require(
            evidence.get("target_1311_checksum")
            == "sha256:bb88d9c7e7937d0e29086d20ebd0c7a567658678daca0dd31c931634431cf723",
            "frozen 1311 checksum is invalid",
        )
        require(
            evidence.get("target_1311_actual_schema_sha256")
            == "sha256:7ef7fcee1e78a641388de16e55937ad73fd549d97aa33de7221c215d19da9517",
            "frozen 1311 actual schema hash is invalid",
        )
        require(
            evidence.get("target_1311_schema_object_count") == 140,
            "1311 object count is invalid",
        )
        require(
            evidence.get("target_1312_checksum")
            == "sha256:effcfb238406228b79ad780e39c0577de4ac851f881af520c92b627b98b529f6",
            "frozen 1312 checksum is invalid",
        )
        require(
            evidence.get("target_1312_actual_schema_sha256")
            == "sha256:49e388386f2a90ea23fe8861554c50274b127de2ad7aa171e78bb4e9ded074e1",
            "frozen 1312 actual schema hash is invalid",
        )
        require(
            evidence.get("target_1312_schema_object_count") == 142,
            "1312 object count is invalid",
        )
        require(
            evidence.get("target_1313_checksum")
            == "sha256:6be5b47275cf0dbe0b6b244c3f71672662f8ce648d1efa446208a94488da1637",
            "frozen 1313 checksum is invalid",
        )
        require(
            evidence.get("target_1313_actual_schema_sha256")
            == "sha256:e971256344e262c59990901b3a6816ecac7be760a31c286f68c5e03d4d491595",
            "frozen 1313 actual schema hash is invalid",
        )
        require(
            evidence.get("target_1313_schema_object_count") == 144,
            "1313 object count is invalid",
        )
        require(
            evidence.get("target_1314_checksum")
            == "sha256:c131d83534326ab1dfa8bf4d007412e1fb9e08c628c04e36de55287b79835b82",
            "frozen 1314 checksum is invalid",
        )
        require(
            evidence.get("target_1314_actual_schema_sha256")
            == "sha256:2aad855cd6b9775850706f3c23c6ae3a494b908b4bce188791c0533a0e0acf14",
            "frozen 1314 actual schema hash is invalid",
        )
        require(
            evidence.get("target_1314_schema_object_count") == 159,
            "1314 object count is invalid",
        )
        require(
            evidence.get("target_1315_checksum")
            == "sha256:a57f73b41a834c42edcdfbacbbf2aa1fc30dd34366d98d7c40a42337b4f0e932",
            "frozen 1315 checksum is invalid",
        )
        require(
            evidence.get("target_1315_actual_schema_sha256")
            == "sha256:33eaed5b16671104d53443c7cd4b09b3471eabbce805161608a114e4528f5d82",
            "frozen 1315 actual schema hash is invalid",
        )
        require(
            evidence.get("target_1315_schema_object_count") == 159,
            "1315 object count is invalid",
        )
        require(
            evidence.get("target_1316_checksum")
            == "sha256:04db7799402ea563228c6be4b016907c0f51ae4ba376fb176bb2a15ca588d81c",
            "frozen 1316 checksum is invalid",
        )
        require(
            evidence.get("target_1316_actual_schema_sha256")
            == "sha256:cb375ac9e36d42fb3896eabf11f32d6b35a131f5075c1512a51add3da6d32981",
            "frozen 1316 actual schema hash is invalid",
        )
        require(
            evidence.get("target_1316_schema_object_count") == 179,
            "1316 object count is invalid",
        )
        require(
            evidence.get("target_1317_checksum")
            == "sha256:6cb890ccc026435715a1c9171b4d1fd0f74ae6896f63d3cdf66a27b6eec5b61b",
            "frozen 1317 checksum is invalid",
        )
        require(
            evidence.get("target_1317_actual_schema_sha256")
            == "sha256:650a031ff1f7d1794bde06d9f9479794d0c89fa2faba8e07762badfe3c500aad",
            "frozen 1317 actual schema hash is invalid",
        )
        require(
            evidence.get("target_1317_schema_object_count") == 179,
            "1317 object count is invalid",
        )

    structural_schema = value.get("structural_guard_schema")
    require(
        isinstance(structural_schema, dict),
        "structural_guard_schema must be an object",
    )
    if isinstance(structural_schema, dict):
        require(
            structural_schema.get("source_version") == 2026081313,
            "structural guard source must be 1313",
        )
        require(
            structural_schema.get("target_version") == 2026081314,
            "structural guard target must be 1314",
        )
        require(
            structural_schema.get("migration_module")
            == "code_assist_v1.notion_workspace_structural_effect_migration",
            "structural guard migration module is invalid",
        )
        require(
            structural_schema.get("migration_cli")
            == "code_assist_v1.tools.migrate_notion_workspace_structural_effects",
            "structural guard migration CLI is invalid",
        )
        for field in (
            "order_generation_added",
            "immutable_structural_manifest_added",
            "immutable_bounded_page_effects_added",
            "action_shape_guards_added",
            "source_receipt_and_command_context_udfs_fail_closed",
            "lifecycle_binding_refresh_and_create_guards_added",
            "generation_scoped_dense_ordinal_guards_added",
            "structural_operation_result_validator_added",
            "deep_health_structural_correlation_added",
            "migration_refuses_live_commands",
            "migration_refuses_invalid_materialized_tree",
        ):
            require(structural_schema.get(field) is True, f"{field} must be true")
        for field in (
            "migration_fabricates_source_receipts_or_commands",
            "catalog_finalizer_added",
            "request_or_startup_migration_enabled",
            "runtime_registered",
            "live_catalog_accessed",
        ):
            require(structural_schema.get(field) is False, f"{field} must be false")

    finalize_schema = value.get("structural_finalize_schema")
    require(
        isinstance(finalize_schema, dict),
        "structural_finalize_schema must be an object",
    )
    if isinstance(finalize_schema, dict):
        exact_finalize = {
            "source_version": 2026081314,
            "target_version": 2026081315,
            "migration_module": "code_assist_v1.notion_workspace_structural_finalize_migration",
            "migration_cli": "code_assist_v1.tools.migrate_notion_workspace_structural_finalize",
        }
        for field, expected in exact_finalize.items():
            require(finalize_schema.get(field) == expected, f"{field} is invalid")
        for field in (
            "root_parent_comparison_is_null_safe",
            "complete_effect_scope_authority_required",
            "source_mutated_structurally_unchanged_parent_retains_order_generation",
            "migration_refuses_live_commands",
            "catalog_finalizer_added",
            "exact_sentinel_rehearsal_added",
        ):
            require(finalize_schema.get(field) is True, f"{field} must be true")
        for field in (
            "migration_fabricates_source_receipts_or_commands",
            "request_or_startup_migration_enabled",
            "runtime_registered",
            "live_catalog_accessed",
        ):
            require(finalize_schema.get(field) is False, f"{field} must be false")

    subtree_schema = value.get("lifecycle_subtree_guard_schema")
    require(
        isinstance(subtree_schema, dict),
        "lifecycle_subtree_guard_schema must be an object",
    )
    if isinstance(subtree_schema, dict):
        exact_subtree = {
            "source_version": 2026081315,
            "target_version": 2026081316,
            "migration_module": "code_assist_v1.notion_workspace_lifecycle_subtree_migration",
            "migration_cli": "code_assist_v1.tools.migrate_notion_workspace_lifecycle_subtree",
        }
        for field, expected in exact_subtree.items():
            require(subtree_schema.get(field) == expected, f"{field} is invalid")
        require(
            subtree_schema.get("canonical_actions") == ["duplicate", "trash", "restore"],
            "canonical lifecycle actions are invalid",
        )
        require(
            subtree_schema.get("canonical_tree_states")
            == ["active", "archived", "trashed", "deleted"],
            "canonical lifecycle tree states are invalid",
        )
        for field in (
            "immutable_subtree_manifest_added",
            "immutable_bounded_page_effects_added",
            "immutable_object_mappings_added",
            "duplicate_pending_binding_create_guard_added",
            "trash_and_restore_binding_transition_guards_added",
            "canonical_tree_state_transition_guards_added",
            "source_receipt_generation_and_command_context_udfs_fail_closed",
            "operation_audit_outbox_result_correlation_added",
            "deep_health_subtree_evidence_correlation_added",
            "migration_refuses_live_commands",
            "migration_refuses_historical_lifecycle_commands",
            "migration_refuses_ambiguous_nonactive_tree_history",
        ):
            require(subtree_schema.get(field) is True, f"{field} must be true")
        for field in (
            "migration_fabricates_subtree_evidence_or_success",
            "catalog_finalizer_added",
            "exact_sentinel_rehearsal_added",
            "request_or_startup_migration_enabled",
            "runtime_registered",
            "live_source_or_catalog_accessed",
        ):
            require(subtree_schema.get(field) is False, f"{field} must be false")

    subtree_finalize_schema = value.get("lifecycle_subtree_finalize_schema")
    require(
        isinstance(subtree_finalize_schema, dict),
        "lifecycle_subtree_finalize_schema must be an object",
    )
    if isinstance(subtree_finalize_schema, dict):
        exact_subtree_finalize = {
            "source_version": 2026081316,
            "target_version": 2026081317,
            "migration_module": "code_assist_v1.notion_workspace_lifecycle_subtree_finalize_migration",
            "migration_cli": "code_assist_v1.tools.migrate_notion_workspace_lifecycle_subtree_finalize",
            "maximum_effect_rows": 11004,
            "transitioned_values": [0, 1],
        }
        for field, expected in exact_subtree_finalize.items():
            require(
                subtree_finalize_schema.get(field) == expected,
                f"{field} is invalid",
            )
        for field in (
            "frozen_1316_contract_preserved",
            "unchanged_parent_and_sibling_effects_authorized",
            "active_to_active_binding_refresh_authorized",
            "duplicate_trash_restore_atomic_catalog_finalizer_added",
            "binding_projection_attestation_tree_and_ledger_atomic",
            "exact_source_lineage_and_receipt_revalidated",
            "deep_health_finalized_evidence_correlation_added",
            "migration_refuses_live_commands_or_subtree_evidence",
            "exact_sentinel_rehearsal_added",
            "finalized_replay_precedes_sentinel_path_quiescence_and_source_io",
            "source_commit_replay_precedes_precondition_recapture",
            "source_receipt_ack_loss_resume_tested",
            "source_applied_catalog_fault_resume_tested",
            "source_and_catalog_effect_bound_parity_tested",
            "maximum_effect_overflow_refused_before_effect_parse",
            "aggregate_report_identifier_free",
            "aggregate_report_path_digest_and_lease_secret_free_tested",
        ):
            require(
                subtree_finalize_schema.get(field) is True,
                f"{field} must be true",
            )
        for field in (
            "migration_fabricates_subtree_evidence_or_success",
            "request_or_startup_migration_enabled",
            "runtime_registered",
            "live_source_or_catalog_accessed",
        ):
            require(
                subtree_finalize_schema.get(field) is False,
                f"{field} must be false",
            )
        require(
            subtree_finalize_schema.get("catalog_fault_boundaries_tested")
            == ["after_evidence", "after_bindings", "after_tree"],
            "catalog fault boundaries are invalid",
        )

    guard_schema = value.get("lifecycle_kernel_guard_schema")
    require(isinstance(guard_schema, dict), "lifecycle_kernel_guard_schema must be an object")
    if isinstance(guard_schema, dict):
        require(guard_schema.get("source_version") == 2026081311, "guard source must be 1311")
        require(guard_schema.get("target_version") == 2026081312, "guard target must be 1312")
        require(guard_schema.get("maximum_lease_seconds") == 300, "command lease bound is invalid")
        for field in (
            "exact_claim_reclaim_heartbeat_fencing",
            "live_lease_required_for_source_applied_and_terminal_transition",
            "command_and_workspace_operation_namespace_mutually_exclusive",
            "only_exact_finalized_operation_commitment_allowed",
            "pre_kernel_lifecycle_commands_refused",
        ):
            require(guard_schema.get(field) is True, f"{field} must be true")
        for field in (
            "adds_command_service_or_source_adapter",
            "request_or_startup_migration_enabled",
        ):
            require(guard_schema.get(field) is False, f"{field} must be false")

    source_adapter = value.get("rename_source_adapter_design")
    require(isinstance(source_adapter, dict), "rename source adapter design must be an object")
    if isinstance(source_adapter, dict):
        exact_values = {
            "design_status": "contract_tested_route_unregistered",
            "legacy_source_version": 2026081303,
            "source_operation_table": "notion_legacy_change_consumer_operations",
            "source_operation_consumer": "np2c.lifecycle",
            "source_operation_action": "legacy.page_lifecycle.rename",
            "source_operation_result_contract": "np2c_legacy_page_rename_receipt_v1",
            "changed_revision_delta": 1,
            "changed_change_generation_delta": 1,
            "changed_source_epoch_delta": 1,
            "changed_full_sweep_generation_delta": 1,
            "contract_module": "code_assist_v1.notion_legacy_page_lifecycle_contract",
            "adapter_module": "code_assist_v1.notion_legacy_page_lifecycle",
            "adapter_connection_scope": "caller_owned_exact_file_backed_v3_connection",
            "adapter_full_integrity_checks_per_instance": 1,
            "adapter_permission_ancestry_max_hops": 256,
            "adapter_page_version_max_blocks": 10000,
            "adapter_page_version_max_bytes": 16777216,
            "adapter_changed_update_statement_count": 1,
            "adapter_temporary_source_tests_passed": 6,
        }
        for field, expected in exact_values.items():
            require(source_adapter.get(field) == expected, f"{field} is invalid")
        for field in (
            "h5e_source_v4_reservation_preserved",
            "source_operation_row_immutable",
            "source_operation_commits_with_page_mutation",
            "source_operation_exact_replay_precedes_page_read",
            "source_operation_changed_request_conflicts",
            "source_operation_contract_requires_authenticated_v3",
            "source_atomic_revision_capability_required",
            "source_mutation_actor_separate_from_catalog_proof_actor",
            "source_mutation_actor_requires_current_edit_role",
            "catalog_actor_requires_current_workspace_admin",
            "normalized_title_committed_by_source_request_sha256",
            "catalog_request_commits_opaque_source_request_sha256",
            "changed_incarnation_preserved",
            "noop_revision_and_watermark_unchanged",
            "noop_still_writes_source_operation_receipt",
            "source_commit_response_loss_recognized_from_ledger",
            "ambiguous_source_commit_requires_manual_audit",
            "page_version_snapshot_semantics_preserved",
            "page_snapshot_bounds_rechecked_inside_writer",
            "adapter_requires_source_quiescence_for_new_effect",
            "adapter_replay_allowed_before_quiescence",
            "adapter_exact_path_and_file_identity_rechecked",
        ):
            require(source_adapter.get(field) is True, f"{field} must be true")
        for field in (
            "new_legacy_source_migration_required",
            "atomic_save_operation_table_reused",
            "raw_title_persisted_in_catalog",
            "raw_title_persisted_in_catalog_ledger_or_event",
            "source_or_catalog_cross_database_transaction_used",
            "adapter_runtime_registered",
            "adapter_live_source_accessed",
        ):
            require(source_adapter.get(field) is False, f"{field} must be false")
        require(
            source_adapter.get("atomic_save_rejection_reasons")
            == [
                "result_contains_full_page_and_blocks",
                "row_cascades_on_page_delete",
                "update_and_delete_immutability_guards_absent",
                "exact_object_contract_authentication_absent",
                "existing_legacy_shards_may_not_have_atomic_save_schema",
            ],
            "atomic-save rejection reasons are invalid",
        )
        expected_prestate = {
            "source_device", "source_inode", "source_link_count", "source_epoch",
            "full_sweep_generation", "source_owner_subject_id", "source_page_id",
            "incarnation_id", "incarnation_generation", "revision", "updated_at",
            "change_generation", "change_mask", "source_exists", "source_active",
            "incarnation_receipt_sha256",
        }
        require(
            set(source_adapter.get("expected_prestate_fields", [])) == expected_prestate,
            "rename expected pre-state is incomplete",
        )
        require(
            set(source_adapter.get("exact_poststate_fields", []))
            == expected_prestate | {"changed"},
            "rename exact post-state is incomplete",
        )

    catalog_finalize = value.get("rename_catalog_finalize_design")
    require(isinstance(catalog_finalize, dict), "rename catalog finalize design must be an object")
    if isinstance(catalog_finalize, dict):
        require(
            catalog_finalize.get("candidate_workspace_source_version") == 2026081312,
            "rename catalog source must be 1312",
        )
        require(
            catalog_finalize.get("candidate_workspace_target_version") == 2026081313,
            "rename catalog target must be 1313",
        )
        require(
            catalog_finalize.get("tree_materialization_state_after_changed_rename")
            == "partial_until_exact_owner_full_sweep",
            "changed rename must retain full-sweep debt",
        )
        for field in (
            "migration_refuses_existing_lifecycle_commands",
            "lifecycle_specific_active_binding_refresh_guard_required",
            "catalog_finalize_requires_exact_source_candidate_revalidation",
            "old_projection_deleted_before_binding_generation_update",
            "binding_proof_generation_incremented_once",
            "current_incarnation_attestation_inserted_for_new_binding_generation",
            "locator_projection_replaced_in_same_transaction",
            "tree_node_receipt_and_binding_generation_refreshed_in_same_transaction",
            "workspace_row_version_and_security_epoch_bumped_once_when_changed",
            "noop_does_not_bump_binding_tree_or_workspace_versions",
            "tree_state_row_version_bumped_when_changed",
            "final_operation_audit_outbox_atomic",
            "final_result_raw_title_free",
            "full_sweep_required_after_changed_rename",
            "dependent_reference_proofs_may_remain_stale_until_full_sweep",
            "candidate_1313_frozen_or_implemented",
            "legacy_v1_finalize_operation_blocked_by_1313_ddl",
            "expected_workspace_version_security_epoch_and_tree_state_frozen",
        ):
            require(catalog_finalize.get(field) is True, f"{field} must be true")
        for field in (
            "healing_run_context_reused_as_lifecycle_authority",
            "tree_version_bumped_for_rename",
            "resolver_cutover_allowed_while_partial",
            "runtime_registration_allowed",
        ):
            require(catalog_finalize.get(field) is False, f"{field} must be false")

    rename_rehearsal = value.get("offline_rename_rehearsal")
    require(isinstance(rename_rehearsal, dict), "offline rename rehearsal must be an object")
    if isinstance(rename_rehearsal, dict):
        exact_values = {
            "module": "code_assist_v1.notion_workspace_page_lifecycle_rehearsal",
            "report_contract": "np2c_1313_offline_page_lifecycle_rename_v1",
            "legacy_source_version": 2026081303,
            "workspace_catalog_version": 2026081313,
            "temporary_sentinel_tests_passed": 5,
        }
        for field, expected_value in exact_values.items():
            require(
                rename_rehearsal.get(field) == expected_value,
                f"{field} is invalid",
            )
        for field in (
            "requires_rehearsal_sentinel_for_new_effect",
            "requires_source_quiescence_for_new_effect",
            "requires_current_workspace_admin",
            "requires_owner_canonical_source_proof",
            "requires_complete_current_tree_prestate",
            "requires_v3_install_before_catalog_source_attestation",
            "finalized_catalog_replay_precedes_sentinel_and_source_path_access",
            "finalized_catalog_replay_revalidates_exact_source_request",
            "source_operation_replay_precedes_source_page_read",
            "source_receipt_and_full_candidate_revalidated_before_catalog_commit",
            "changed_and_noop_paths_tested",
            "source_commit_response_loss_tested",
            "catalog_commit_response_loss_tested",
            "catalog_transaction_rollback_after_source_commit_tested",
            "changed_rename_preserves_full_sweep_debt",
        ):
            require(rename_rehearsal.get(field) is True, f"{field} must be true")
        for field in (
            "changed_title_persisted_in_report_or_catalog_ledger",
            "report_contains_page_owner_path_digest_or_lease_secret",
            "cross_database_exactly_once_claimed",
            "runtime_registered",
            "routes_changed",
            "live_source_or_catalog_accessed",
        ):
            require(rename_rehearsal.get(field) is False, f"{field} must be false")

    structural_design = value.get("create_move_reorder_design")
    require(
        isinstance(structural_design, dict),
        "create_move_reorder_design must be an object",
    )
    if isinstance(structural_design, dict):
        exact_values = {
            "design_status": "source_adapters_1314_guards_1315_finalizer_and_rehearsal_contract_tested",
            "legacy_source_version": 2026081303,
            "source_operation_table": "notion_legacy_change_consumer_operations",
            "source_operation_consumer": "np2c.lifecycle",
            "create_proposed_page_id_format": "p_<32-lowercase-hex>",
            "create_existing_page_or_incarnation_collision_disposition": "conflict_without_write",
            "create_root_shard_authority": "initiating_source_owner",
            "create_child_shard_authority": "exact_parent_owner",
            "move_source_authority": "current_source_owner_only_until_np2d",
            "reorder_source_authority": "current_source_owner_only_until_np2d",
            "candidate_workspace_source_version": 2026081313,
            "candidate_workspace_target_version": 2026081314,
            "finalizer_workspace_source_version": 2026081314,
            "finalizer_workspace_target_version": 2026081315,
            "source_contract_module": "code_assist_v1.notion_legacy_page_structural",
            "source_adapter_module": "code_assist_v1.notion_legacy_page_structural",
            "source_adapter_connection_scope": "caller_owned_exact_file_backed_v3_connection",
            "source_adapter_full_integrity_checks_per_instance": 1,
            "source_adapter_temporary_source_tests_passed": 3,
        }
        for field, expected_value in exact_values.items():
            require(
                structural_design.get(field) == expected_value,
                f"{field} is invalid",
            )
        require(
            structural_design.get("source_actions")
            == {
                "create": "legacy.page_lifecycle.create",
                "move": "legacy.page_lifecycle.move",
                "reorder": "legacy.page_lifecycle.reorder",
            },
            "structural source action namespace is invalid",
        )
        require(
            structural_design.get("source_result_contracts")
            == {
                "create": "np2c_legacy_page_structural_receipt_v1",
                "move": "np2c_legacy_page_structural_receipt_v1",
                "reorder": "np2c_legacy_page_structural_receipt_v1",
            },
            "structural source result contracts are invalid",
        )
        for field in (
            "h5e_source_v4_reservation_preserved",
            "source_operation_exact_replay_precedes_page_path_and_quiescence",
            "source_operation_changed_request_conflicts",
            "source_result_commits_post_manifest_sha256",
            "create_page_id_is_caller_supplied_and_exact_request_committed",
            "create_parent_edit_and_lock_revalidated_inside_writer",
            "move_when_result_parent_differs",
            "reorder_when_result_parent_is_same",
            "same_parent_append_classified_as_reorder",
            "source_changed_and_structure_changed_recorded_separately",
            "legacy_same_position_write_not_optimized_before_differential",
            "before_and_after_mutually_exclusive",
            "relative_sibling_must_be_in_result_parent_scope",
            "source_prewrite_scope_audit_required",
            "source_adapter_import_and_filesystem_isolated",
            "candidate_1314_schema_required",
            "candidate_1314_schema_implemented",
            "candidate_1315_schema_required",
            "candidate_1315_schema_implemented",
            "frozen_1313_must_remain_importable",
            "migration_preserves_finalized_1313_rename_history",
            "migration_refuses_live_prepared_or_source_applied_commands",
            "catalog_finalize_revalidates_source_manifest_outside_writer",
            "catalog_finalize_rechecks_all_command_tree_binding_incarnation_and_scope_fences",
            "catalog_finalize_deletes_old_projections_before_binding_generation_updates",
            "catalog_finalize_updates_all_affected_scopes_in_one_transaction",
            "catalog_finalize_uses_order_generation_to_avoid_transient_ordinal_collision",
            "tree_version_bumped_once_only_when_structure_changed",
            "workspace_and_security_versions_bumped_once_when_source_changed",
            "catalog_finalize_operation_audit_outbox_atomic",
            "changed_effect_marks_tree_partial_and_full_sweep_required",
            "page_watermark_statements_coalesced_to_one_logical_generation_inside_source_transaction",
            "catalog_finalized_replay_precedes_sentinel_path_and_source_io",
            "aggregate_report_identifier_free",
        ):
            require(structural_design.get(field) is True, f"{field} must be true")
        for field in (
            "new_legacy_source_migration_required",
            "fixed_generation_delta_assumed",
            "source_and_catalog_cross_database_transaction_used",
            "create_proposed_page_id_replay_derived",
            "create_default_add_to_parent_blocks",
            "cross_owner_or_workspace_move_allowed",
            "migration_fabricates_source_receipts_or_commands",
            "watermark_acknowledged_or_deleted",
            "runtime_registered",
            "live_source_or_catalog_accessed",
            "source_adapter_live_source_accessed",
        ):
            require(structural_design.get(field) is False, f"{field} must be false")
        require(
            structural_design.get("source_post_manifest_components")
            == [
                "target_pre_and_post_parent_and_dense_ordinal",
                "ordered_old_sibling_scope",
                "ordered_new_sibling_scope",
                "complete_moved_subtree_and_depths",
                "all_source_mutated_page_receipts_and_proof_locator_digests",
                "old_and_new_parent_block_commitments",
                "full_source_fence",
                "source_changed_and_structure_changed",
                "bounded_counts_and_manifest_sha256",
            ],
            "structural post-manifest contract is incomplete",
        )
        required_1314_reasons = {
            "create_target_has_no_existing_binding_or_tree_node",
            "1313_request_normalizer_is_rename_only_and_requires_positive_generations",
            "1313_command_receipt_caps_affected_nodes_at_1000",
            "move_and_reorder_need_two_10000_node_sibling_scopes_and_per_page_evidence",
            "1313_binding_refresh_guard_authorizes_only_one_rename_target",
            "full_10000_node_scope_has_no_spare_positive_ordinal_for_unique_index_permutation",
        }
        require(
            set(structural_design.get("candidate_1314_reasons", []))
            == required_1314_reasons,
            "candidate 1314 justification is incomplete",
        )
        require(
            structural_design.get("candidate_1314_required_concerns")
            == [
                "immutable_action_specific_command_scope_and_page_effect_evidence",
                "tree_node_order_generation_and_generation_scoped_unique_ordinals",
                "create_move_reorder_action_shape_triggers",
                "lifecycle_only_create_binding_and_bounded_multi_page_proof_refresh_authority",
                "structural_finalize_result_and_exact_operation_audit_outbox_commitment",
                "deep_health_scope_manifest_order_binding_projection_and_ledger_validation",
            ],
            "candidate 1314 concerns are invalid",
        )
        require(
            structural_design.get("implementation_order")
            == [
                "NP2C-3e1-action-specific-legacy-v3-source-adapters",
                "NP2C-3e2-frozen-1313-to-1314-schema-and-guards",
                "NP2C-3e3-atomic-catalog-finalizers-and-sentinel-rehearsal",
            ],
            "structural implementation order is invalid",
        )

    lifecycle4 = value.get("duplicate_trash_restore_design")
    require(
        isinstance(lifecycle4, dict),
        "duplicate_trash_restore_design must be an object",
    )
    if isinstance(lifecycle4, dict):
        exact_values = {
            "design_status": "source_adapters_1316_guards_1317_source_commit_replay_and_adversarial_audit_contract_tested_default_off",
            "design_document": "docs/notion_parity/NP2C_DUPLICATE_TRASH_RESTORE_DESIGN.md",
            "reference_snapshot_date": "2026-08-14",
            "legacy_source_version": 2026081303,
            "source_operation_table": "notion_legacy_change_consumer_operations",
            "source_operation_consumer": "np2c.lifecycle",
            "duplicate_page_id_format": "p_<32-lowercase-hex>",
            "duplicate_external_reference_first_adapter_disposition": "refuse_without_write",
            "candidate_workspace_source_version": 2026081315,
            "candidate_workspace_target_version": 2026081316,
            "source_contract_module": "code_assist_v1.notion_legacy_page_duplicate_trash_restore_contract",
            "source_adapter_module": "code_assist_v1.notion_legacy_page_duplicate_trash_restore",
            "source_adapter_connection_scope": "caller_owned_exact_file_backed_v3_connection_and_exact_owner_workspace_root",
            "source_adapter_full_integrity_checks_per_instance": 1,
            "source_adapter_temporary_source_tests_passed": 18,
        }
        for field, expected in exact_values.items():
            require(lifecycle4.get(field) == expected, f"{field} is invalid")
        require(
            lifecycle4.get("canonical_legacy_compatibility_actions")
            == ["duplicate", "trash", "restore"],
            "legacy compatibility action vocabulary is invalid",
        )
        require(
            lifecycle4.get("official_archive_actions")
            == ["archive", "unarchive"],
            "official Archive action vocabulary is invalid",
        )
        require(
            lifecycle4.get("source_actions")
            == {
                "duplicate": "legacy.page_lifecycle.duplicate",
                "trash": "legacy.page_lifecycle.trash",
                "restore": "legacy.page_lifecycle.restore",
            },
            "NP2C-4 source action namespace is invalid",
        )
        require(
            lifecycle4.get("source_result_contracts")
            == {
                "duplicate": "np2c_legacy_page_duplicate_receipt_v1",
                "trash": "np2c_legacy_page_trash_receipt_v1",
                "restore": "np2c_legacy_page_restore_receipt_v1",
            },
            "NP2C-4 source result contracts are invalid",
        )
        for field in (
            "official_archive_distinct_from_delete_or_trash",
            "official_archive_plan_or_capability_gated",
            "legacy_archive_page_is_http_delete_trash_compatibility",
            "source_operation_exact_replay_precedes_quiescence_path_page_and_file_io",
            "source_operation_changed_request_conflicts",
            "h5e_source_v4_reservation_preserved",
            "new_source_schema_required_for_official_archive_unarchive",
            "official_archive_source_schema_version_assigned",
            "initial_offline_actor_must_be_source_owner_and_workspace_admin",
            "duplicate_same_owner_and_workspace_only",
            "duplicate_page_block_file_mappings_domain_separated_and_replay_stable",
            "duplicate_new_pages_receive_fresh_incarnations",
            "duplicate_existing_identity_or_byte_collision_refuses_without_substitution",
            "duplicate_internal_references_remapped",
            "duplicate_precommit_orphans_recoverable_from_prepared_command_and_mapping",
            "trash_exact_active_transition_cohort_only",
            "trash_preserves_file_bytes_content_history_and_incarnation",
            "trash_removes_active_locator_public_file_projections_atomically_in_catalog",
            "restore_references_exact_trash_command_and_cohort",
            "restore_requires_same_incarnation",
            "page_watermark_statements_coalesced_to_one_logical_generation_per_changed_page",
            "changed_effect_preserves_complete_tree_with_full_owner_receipt",
            "full_sweep_required_remains_watermark_cutover_debt",
            "candidate_1316_required",
            "candidate_1316_implemented",
            "candidate_1317_required",
            "candidate_1317_implemented",
            "migration_refuses_ambiguous_nonactive_history",
            "source_adapter_import_and_filesystem_isolated",
            "source_adapter_implemented",
            "workspace_schema_or_migration_implemented",
            "catalog_finalizer_implemented",
            "exact_sentinel_rehearsal_implemented",
            "source_commit_replay_before_precondition_implemented",
            "source_receipt_ack_loss_and_catalog_fault_resume_tested",
            "catalog_fault_boundaries_all_rollback_tested",
            "exact_11004_effect_bound_and_overflow_refusal_tested",
            "aggregate_report_redaction_tested",
        ):
            require(lifecycle4.get(field) is True, f"{field} must be true")
        for field in (
            "new_source_schema_required_for_duplicate_trash_restore",
            "public_duplicate_in_scope",
            "duplicate_shares_guests_public_links_comments_notifications_integrations_copied",
            "duplicate_file_bytes_visible_before_source_rows",
            "duplicate_unknown_file_outcome_auto_deleted",
            "trash_already_inactive_descendants_added_to_cohort",
            "restore_recreated_page_id_accepted",
            "restore_silently_restores_ancestors",
            "restore_unrelated_trashed_descendants",
            "restore_without_valid_placement_mutates_source",
            "migration_silently_relabels_historical_archived_nodes",
            "changed_effect_marks_tree_partial_and_full_sweep_required",
            "runtime_registered",
            "live_source_or_catalog_accessed",
            "permanent_delete_or_purge_implemented",
        ):
            require(lifecycle4.get(field) is False, f"{field} must be false")
        require(
            lifecycle4.get("official_archive_candidate_source_schema_version")
            == 2026081305,
            "official Archive candidate source version must be 2026081305",
        )
        require(
            lifecycle4.get("duplicate_file_protocol")
            == [
                "catalog_command_prepared",
                "deterministic_mapping_derived",
                "exact_source_path_size_and_sha256_verified",
                "contained_operation_staging_exclusive_create",
                "staged_bytes_flushed_and_reverified",
                "atomic_no_overwrite_final_publish_or_exact_existing_hash",
                "source_transaction_revalidates_and_commits_rows_plus_v3_receipt",
            ],
            "duplicate file convergence protocol is invalid",
        )
        require(
            lifecycle4.get("restore_placement_modes")
            == [
                "original_exact_active_parent_and_scope",
                "reviewed_fallback_root_or_exact_active_parent",
            ],
            "restore placement modes are invalid",
        )
        require(
            set(lifecycle4.get("candidate_1316_reasons", []))
            == {
                "1315_action_guards_allow_only_rename_create_move_reorder",
                "1315_structural_create_manifest_requires_single_page_subtree",
                "1315_effects_require_active_after_proof_and_projection",
                "1315_tree_lifecycle_cannot_distinguish_archive_from_trash",
                "1315_has_no_trash_cohort_original_placement_or_duplicate_file_mapping_commitment",
                "1315_has_no_lifecycle_multi_create_or_multi_page_trash_restore_authority",
            },
            "candidate 1316 justification is incomplete",
        )
        require(
            lifecycle4.get("candidate_1316_required_concerns")
            == [
                "immutable_duplicate_and_trash_restore_manifests_and_page_effects",
                "before_after_lifecycle_cohort_original_placement_mapping_and_file_commitments",
                "active_archived_trashed_deleted_tree_lifecycle_representation",
                "lifecycle_only_multi_create_active_to_trashed_and_exact_trashed_to_active_authority",
                "generation_scoped_dense_active_order_updates",
                "exact_result_operation_audit_outbox_and_deep_health_correlation",
                "fail_closed_service_context_and_raw_sql_guards",
            ],
            "candidate 1316 concerns are invalid",
        )
        require(
            lifecycle4.get("implementation_order")
            == [
                "NP2C-4a-duplicate-trash-restore-contract-design",
                "NP2C-4b-route-unregistered-source-v3-adapters-and-file-recovery",
                "NP2C-4c-frozen-1315-to-1316-lifecycle-subtree-schema-and-guards",
                "NP2C-4d-atomic-catalog-finalizers-and-exact-sentinel-rehearsal",
                "NP2C-4e-independent-audit-and-bounded-adversarial-rehearsal",
            ],
            "NP2C-4 implementation order is invalid",
        )

    archive = value.get("official_archive_design")
    require(isinstance(archive, dict), "official_archive_design must be an object")
    if isinstance(archive, dict):
        for field, expected in {
            "design_status": "official_reference_contract_frozen_implementation_blocked_default_off",
            "design_document": "docs/notion_parity/NP2C_OFFICIAL_ARCHIVE_DESIGN.md",
            "reference_snapshot_date": "2026-08-14",
        }.items():
            require(archive.get(field) == expected, f"official Archive {field} is invalid")
        require(
            archive.get("reference_sources")
            == [
                "https://www.notion.com/help/archive-pages",
                "https://www.notion.com/en-gb/help/duplicate-delete-and-restore-content",
                "https://www.notion.com/releases/2026-03-27",
                "https://developers.notion.com/reference/page",
                "https://developers.notion.com/reference/patch-page",
            ],
            "official Archive reference sources are invalid",
        )

        observations = archive.get("reference_observations")
        require(isinstance(observations, dict), "Archive observations must be an object")
        if isinstance(observations, dict):
            for field in (
                "archive_is_beta",
                "business_or_enterprise_plan_required",
                "edit_permission_can_archive_or_unarchive",
                "archive_is_distinct_from_trash",
                "archived_page_remains_accessible",
                "existing_workspace_links_continue_to_work",
                "parent_archive_affects_all_subpages",
                "default_search_hidden",
                "default_sidebar_hidden",
                "default_database_view_hidden",
                "explicit_archive_views_exist",
                "api_response_distinguishes_is_archived_from_in_trash",
            ):
                require(observations.get(field) is True, f"{field} must be true")
            for field in (
                "documented_update_body_supports_is_archived_mutation",
                "public_api_used_as_implementation_oracle",
            ):
                require(observations.get(field) is False, f"{field} must be false")

        compatibility = archive.get("legacy_compatibility")
        require(isinstance(compatibility, dict), "Archive compatibility must be an object")
        if isinstance(compatibility, dict):
            require(compatibility.get("legacy_archive_page_means_trash") is True, "legacy archive_page must remain Trash")
            require(compatibility.get("legacy_pages_archived_bit_reused_for_official_archive") is False, "legacy pages.archived must not represent official Archive")
            require(compatibility.get("historical_catalog_archive_action_reinterpreted") is False, "historical Trash action must not be reinterpreted")
            require(compatibility.get("historical_catalog_archive_action_canonical_meaning") == "trash", "historical archive action must remain canonical Trash")
            require(compatibility.get("future_storage_actions") == ["archive_content", "unarchive_content"], "future Archive storage actions are invalid")
            require(compatibility.get("future_canonical_actions") == ["archive", "unarchive"], "future Archive canonical actions are invalid")
            require(
                compatibility.get("future_source_actions")
                == {
                    "archive": "legacy.page_lifecycle.content_archive",
                    "unarchive": "legacy.page_lifecycle.content_unarchive",
                },
                "future Archive source actions are invalid",
            )
            require(
                compatibility.get("future_source_result_contracts")
                == {
                    "archive": "np2c_legacy_page_content_archive_receipt_v1",
                    "unarchive": "np2c_legacy_page_content_unarchive_receipt_v1",
                },
                "future Archive result contracts are invalid",
            )

        lineage = archive.get("source_lineage")
        require(isinstance(lineage, dict), "Archive source lineage must be an object")
        if isinstance(lineage, dict):
            require(lineage.get("current_source_schema_version") == 2026081303, "Archive source lineage must start at v3")
            require(lineage.get("h5e_reserved_predecessor_version") == 2026081304, "H5e v4 reservation must remain exact")
            require(lineage.get("candidate_archive_source_version") == 2026081305, "Archive source candidate must be v5")
            for field in (
                "candidate_version_is_reservation_only",
                "source_v5_requires_exact_frozen_v4",
            ):
                require(lineage.get(field) is True, f"{field} must be true")
            for field in (
                "h5e_predecessor_authorized",
                "h5e_predecessor_implemented",
                "source_version_skip_allowed",
                "source_v4_repurposed_for_archive",
                "request_or_startup_migration_allowed",
                "operational_source_migration_applied",
            ):
                require(lineage.get(field) is False, f"{field} must be false")

        source_state = archive.get("source_state_contract")
        require(isinstance(source_state, dict), "Archive source state contract must be an object")
        if isinstance(source_state, dict):
            require(source_state.get("candidate_state_table") == "notion_page_archive_states", "Archive state table is invalid")
            for field in (
                "separate_archive_state_table_required",
                "legacy_pages_archived_must_remain_zero_while_officially_archived",
                "exact_page_incarnation_required",
                "positive_monotonic_archive_generation_required",
                "archive_root_and_operation_provenance_required",
                "direct_and_inherited_provenance_required",
                "exact_changed_cohort_required",
                "content_blocks_files_versions_and_incarnation_preserved",
                "immutable_source_operation_receipt_same_transaction",
            ):
                require(source_state.get(field) is True, f"{field} must be true")
            for field in (
                "raw_sql_without_service_context_allowed",
                "ambiguous_nested_state_mutates_source",
                "source_schema_implemented",
                "source_adapter_implemented",
            ):
                require(source_state.get(field) is False, f"{field} must be false")

        catalog = archive.get("catalog_contract")
        require(isinstance(catalog, dict), "Archive catalog contract must be an object")
        if isinstance(catalog, dict):
            for field, expected in {
                "current_workspace_schema_version": 2026081317,
                "candidate_workspace_schema_version": 2026081318,
                "binding_state_while_archived": "active",
                "physical_tree_lifecycle_state_while_archived": "active",
                "canonical_tree_lifecycle_state_while_archived": "archived",
            }.items():
                require(catalog.get(field) == expected, f"Archive catalog {field} is invalid")
            for field in (
                "candidate_schema_required",
                "separate_archive_manifest_and_effect_authority_required",
                "locator_projection_retained",
                "official_archived_counter_required",
                "direct_locator_requires_current_iam",
                "dependent_reference_full_sweep_required",
            ):
                require(catalog.get(field) is True, f"{field} must be true")
            for field in (
                "candidate_schema_implemented",
                "frozen_1317_history_rewritten",
                "default_navigation_search_and_database_views_include_archived",
                "complete_full_owner_tree_marked_partial",
                "catalog_finalizer_implemented",
                "runtime_registered",
            ):
                require(catalog.get(field) is False, f"{field} must be false")

        archive_auth = archive.get("authorization")
        require(isinstance(archive_auth, dict), "Archive authorization must be an object")
        if isinstance(archive_auth, dict):
            for field in (
                "server_side_plan_entitlement_required",
                "current_edit_permission_required",
                "np2d_unified_capability_required_before_runtime",
                "first_offline_adapter_may_require_source_owner_and_workspace_admin",
            ):
                require(archive_auth.get(field) is True, f"{field} must be true")
            for field in ("client_flag_is_authority", "unknown_plan_or_permission_mutates_source"):
                require(archive_auth.get(field) is False, f"{field} must be false")

        visibility = archive.get("visibility")
        require(isinstance(visibility, dict), "Archive visibility must be an object")
        if isinstance(visibility, dict):
            for field in (
                "default_sidebar_hidden",
                "default_search_hidden",
                "default_database_view_hidden",
                "explicit_archive_filter_required",
                "direct_workspace_link_resolution_retained_subject_to_iam",
                "first_offline_adapter_refuses_public_locator_cohort",
            ):
                require(visibility.get(field) is True, f"{field} must be true")
            for field in ("archive_is_authorization_grant_or_revoke", "public_or_published_link_behavior_assumed"):
                require(visibility.get(field) is False, f"{field} must be false")

        archive_bounds = archive.get("bounds")
        require(isinstance(archive_bounds, dict), "Archive bounds must be an object")
        if isinstance(archive_bounds, dict):
            for field, expected in {
                "synchronous_subtree_max_nodes": 1000,
                "maximum_depth": 256,
                "maximum_siblings_per_affected_scope": 10000,
                "maximum_aggregate_source_bytes": 268435456,
                "maximum_full_integrity_checks_per_owner_per_new_effect": 1,
                "maximum_immutable_source_snapshots_per_new_effect": 2,
            }.items():
                require(archive_bounds.get(field) == expected, f"Archive bound {field} is invalid")
            for field in ("source_and_catalog_effect_counts_must_match", "overflow_refused_before_source_mutation"):
                require(archive_bounds.get(field) is True, f"{field} must be true")
            require(archive_bounds.get("source_or_network_io_inside_catalog_writer") is False, "Archive catalog writer must not perform external I/O")

        archive_redaction = archive.get("redaction")
        require(isinstance(archive_redaction, dict), "Archive redaction must be an object")
        if isinstance(archive_redaction, dict):
            for field in (
                "raw_title_or_content_persisted_in_result_audit_outbox",
                "page_or_owner_ids_in_aggregate_report",
                "raw_path_filename_public_token_nonce_digest_or_lease_in_report",
            ):
                require(archive_redaction.get(field) is False, f"{field} must be false")

        require(
            archive.get("live_characterization_required")
            == [
                "parent_archive_and_unarchive_with_independently_archived_descendants",
                "inherited_child_independent_unarchive",
                "create_move_duplicate_trash_restore_across_archived_boundary",
                "root_page_database_page_inline_database_and_data_source",
                "search_library_sidebar_database_and_content_search_visibility",
                "direct_link_backlink_relation_rollup_mention_comment_and_synced_block",
                "owner_editor_commenter_reader_guest_teamspace_and_admin_permissions",
                "public_and_published_link_behavior",
                "web_desktop_and_mobile_behavior",
                "response_loss_permission_loss_plan_loss_and_subtree_drift",
            ],
            "official Archive characterization matrix is invalid",
        )
        for field in (
            "nested_unarchive_semantics_frozen",
            "live_characterization_completed",
            "implementation_authorized",
            "runtime_enabled",
        ):
            require(archive.get(field) is False, f"{field} must be false")
        require(
            archive.get("implementation_order")
            == [
                "NP2C-5-official-reference-design-and-machine-contract",
                "NP2C-5a-approved-business-enterprise-live-characterization",
                "H5e-1-source-v4-schema-migrator-separately-authorized-ack-disabled",
                "NP2C-5b-source-v5-schema-migrator-and-route-unregistered-adapter",
                "NP2C-5c-workspace-1318-guards-and-atomic-finalizer",
                "NP2C-5d-exact-sentinel-offline-rehearsal",
                "NP2C-5e-independent-security-bounds-redaction-and-deep-health-audit",
                "NP2C-6-protected-routes-and-default-off-ui",
            ],
            "official Archive implementation order is invalid",
        )

    tree = value.get("tree_invariants")
    require(isinstance(tree, dict), "tree_invariants must be an object")
    if isinstance(tree, dict):
        require(tree.get("lifecycle_states") == ["active", "archived", "deleted"], "invalid lifecycle states")
        require(
            tree.get("materialization_states") == ["unmaterialized", "partial", "complete"],
            "invalid materialization states",
        )
        for field in (
            "root_parent_components_are_null",
            "dense_positive_sibling_ordinals",
            "unique_sibling_ordinals",
            "active_node_requires_current_active_binding",
            "deleted_tombstone_retained_for_replay",
            "tree_version_bumped_once_per_finalized_structure_mutation",
        ):
            require(tree.get(field) is True, f"{field} must be true")
        for field in (
            "cross_workspace_parent_allowed",
            "cross_owner_parent_allowed_in_current_model",
            "self_parent_allowed",
            "cycles_allowed",
            "archived_or_deleted_node_is_resolver_target",
            "navigation_preference_mutation_bumps_tree_version",
        ):
            require(tree.get(field) is False, f"{field} must be false")

    protocol = value.get("command_protocol")
    require(isinstance(protocol, dict), "command_protocol must be an object")
    if isinstance(protocol, dict):
        require(
            protocol.get("phases")
            == ["prepared", "source_applied", "finalized", "failed", "manual_audit"],
            "command phases are invalid",
        )
        for field in (
            "operation_id_required",
            "canonical_request_sha256_required",
            "current_actor_reauthorized_on_replay",
            "trusted_clock_required",
            "lease_token_digest_required",
            "fencing_generation_required",
            "raw_sql_transition_guards_required",
            "source_receipt_required_before_finalize",
            "source_post_state_recognition_required",
            "operation_audit_outbox_atomic_with_finalize",
            "exact_command_replay_precedes_quiescence_and_source_io",
            "source_receipt_revalidated_before_and_inside_finalize",
            "catalog_tree_binding_incarnation_rechecked_before_receipt_and_finalize",
            "same_token_sufficient_live_lease_ack_is_write_free",
            "finalized_commitment_deep_health_validated",
            "kernel_report_identifier_free",
            "tree_or_locator_projection_updated_by_kernel",
        ):
            require(protocol.get(field) is True, f"{field} must be true")
        for field in (
            "real_or_live_legacy_source_mutation_performed",
        ):
            require(protocol.get(field) is False, f"{field} must be false")
        require(
            protocol.get("temporary_v3_source_rename_mutation_tested") is True,
            "temporary v3 rename evidence is required",
        )
        require(
            protocol.get("changed_finalizer_temporary_catalog_tests_passed") == 6,
            "temporary changed-finalizer evidence is incomplete",
        )
        require(
            protocol.get("changed_finalizer_live_catalog_accessed") is False,
            "live catalog finalization is forbidden",
        )
        require(protocol.get("lease_token_persisted_raw") is False, "raw lease tokens forbidden")
        require(
            protocol.get("ambiguous_source_state_disposition") == "manual_audit",
            "ambiguous source state must require manual audit",
        )

    require(
        value.get("actions")
        == [
            "create", "rename", "move", "reorder", "duplicate", "trash",
            "restore", "archive", "unarchive", "purge",
        ],
        "action allowlist is invalid",
    )
    require(value.get("internal_actions") == ["materialize"], "internal action allowlist is invalid")

    action = value.get("action_invariants")
    require(isinstance(action, dict), "action_invariants must be an object")
    if isinstance(action, dict):
        for field in (
            "create_identity_replay_stable",
            "rename_payload_redacted_from_audit_outbox",
            "move_before_after_mutually_exclusive",
            "move_relative_sibling_same_result_parent",
            "move_updates_all_sibling_ordinals_atomically",
            "duplicate_subtree_bounded_before_write",
            "duplicate_internal_references_remapped",
            "trash_exact_transition_cohort_fenced",
            "restore_requires_same_incarnation",
            "archive_is_distinct_from_trash",
            "legacy_archive_page_means_trash_compatibility",
            "purge_requires_separate_approval",
        ):
            require(action.get(field) is True, f"{field} must be true")
        for field in (
            "duplicate_shares_or_public_links_by_default",
            "public_duplicate_in_np2c4_scope",
            "restore_silently_restores_ancestors",
            "official_archive_unarchive_implemented",
            "purge_runs_unbounded_in_request",
        ):
            require(action.get(field) is False, f"{field} must be false")

    authorization = value.get("authorization")
    require(isinstance(authorization, dict), "authorization must be an object")
    if isinstance(authorization, dict):
        for field in (
            "active_workspace_member_required",
            "source_authority_revalidated_each_phase",
            "permission_loss_prevents_finalize",
            "runtime_routes_blocked_until_unified_capability_or_narrow_review",
            "denial_is_non_enumerating",
        ):
            require(authorization.get(field) is True, f"{field} must be true")
        require(authorization.get("guest_mutation_allowed") is False, "guest mutation forbidden")

    bounds = value.get("bounds")
    require(isinstance(bounds, dict), "bounds must be an object")
    if isinstance(bounds, dict):
        require(bounds.get("request_json_max_bytes") == 65536, "request bound is invalid")
        require(bounds.get("synchronous_subtree_max_nodes") == 1000, "subtree bound is invalid")
        require(bounds.get("tree_max_depth") == 256, "tree depth bound is invalid")
        require(bounds.get("sibling_scope_max_nodes") == 10000, "sibling bound is invalid")
        require(bounds.get("maximum_command_leases_per_command") == 1, "lease bound is invalid")
        require(bounds.get("maximum_source_snapshots_per_new_effect") == 2, "snapshot bound is invalid")
        require(
            bounds.get("maximum_full_integrity_checks_per_owner_per_new_effect") == 1,
            "owner integrity bound is invalid",
        )
        require(bounds.get("legacy_directory_scan_count") == 0, "directory scans are forbidden")
        require(
            bounds.get("rename_permission_ancestry_max_hops") == 256,
            "rename permission bound is invalid",
        )
        require(
            bounds.get("rename_page_version_max_blocks") == 10000,
            "rename version block bound is invalid",
        )
        require(
            bounds.get("rename_page_version_max_bytes") == 16777216,
            "rename version byte bound is invalid",
        )
        require(
            bounds.get("structural_effect_max_page_receipts") == 21002,
            "structural page-receipt bound is invalid",
        )
        require(
            bounds.get("structural_effect_max_aggregate_bytes") == 268435456,
            "structural aggregate-byte bound is invalid",
        )
        require(
            bounds.get("structural_effect_result_json_max_bytes") == 16777216,
            "structural result-json bound is invalid",
        )
        require(
            bounds.get("structural_parent_block_max_count") == 5000,
            "structural parent-block bound is invalid",
        )
        require(
            bounds.get("duplicate_total_block_max_count") == 50000,
            "duplicate block bound is invalid",
        )
        require(
            bounds.get("duplicate_total_file_max_count") == 5000,
            "duplicate file-count bound is invalid",
        )
        require(
            bounds.get("duplicate_aggregate_file_bytes_max") == 268435456,
            "duplicate file-byte bound is invalid",
        )
        require(
            bounds.get("duplicate_aggregate_metadata_bytes_max") == 268435456,
            "duplicate metadata-byte bound is invalid",
        )
        require(
            bounds.get("duplicate_source_result_json_max_bytes") == 16777216,
            "duplicate source-result bound is invalid",
        )
        require(
            bounds.get("lifecycle_transition_cohort_max_nodes") == 1000,
            "lifecycle cohort bound is invalid",
        )

    materializer = value.get("offline_initial_materializer")
    require(isinstance(materializer, dict), "offline materializer must be an object")
    if isinstance(materializer, dict):
        for field in (
            "requires_rehearsal_sentinel",
            "requires_source_quiescence",
            "requires_current_workspace_admin",
            "requires_unmaterialized_tree_version_zero",
            "requires_zero_existing_tree_nodes",
            "requires_every_active_binding_currently_attested",
            "requires_every_source_page_active_and_nonretired",
            "source_owner_shard_full_integrity_first_snapshot_only",
            "second_snapshot_reuses_exact_integrity_attestation",
            "catalog_commit_includes_state_nodes_operation_audit_outbox",
            "replay_precedes_sentinel_and_source_path_access",
        ):
            require(materializer.get(field) is True, f"{field} must be true")
        for field in (
            "report_contains_page_or_owner_identifiers",
            "runtime_registered",
            "source_mutation_performed",
            "source_watermark_acknowledged",
        ):
            require(materializer.get(field) is False, f"{field} must be false")
        for field in (
            "hidden_or_unbound_parent_disposition",
            "cycle_disposition",
            "source_drift_disposition",
        ):
            require(
                materializer.get(field) == "refuse_without_write",
                f"{field} must refuse without write",
            )

    read = value.get("read_contract")
    require(isinstance(read, dict), "read_contract must be an object")
    if isinstance(read, dict):
        require(read.get("structural_snapshot_source_database_open_count") == 0, "structural reads must not open source DBs")
        for field in (
            "display_hydration_exact_owner_only",
            "one_tree_version_per_response",
        ):
            require(read.get(field) is True, f"{field} must be true")
        for field in (
            "hidden_parent_locator_returned",
            "hidden_ordinal_gap_returned",
            "archived_or_deleted_node_returned_in_active_tree",
            "content_resolver_switched",
        ):
            require(read.get(field) is False, f"{field} must be false")

    migration = value.get("migration_contract")
    require(isinstance(migration, dict), "migration_contract must be an object")
    if isinstance(migration, dict):
        for field in (
            "explicit_offline_quiesced_only",
            "exact_backup_required",
            "source_backup_identity_and_security_revalidation_required",
            "fresh_and_migrated_schema_equality_required",
            "preserve_existing_navigation_and_bindings",
            "partial_or_future_schema_refused",
            "ambiguous_commit_convergence_required",
            "pre_kernel_lifecycle_commands_refused",
        ):
            require(migration.get(field) is True, f"{field} must be true")
        require(migration.get("live_catalog_access_allowed") is False, "live catalog access forbidden")

    required_faults = set(value.get("required_fault_tests", []))
    for name in (
        "source_effect_committed_before_catalog_finalize_crash",
        "permission_revoked_between_phases",
        "source_incarnation_changed_between_phases",
        "catalog_finalize_response_loss_replay_before_source_path",
        "catalog_finalize_transaction_rollback_after_source_commit",
        "create_proposed_id_collision_without_source_write",
        "create_source_commit_response_loss_exact_replay",
        "move_or_reorder_action_class_mismatch",
        "move_full_10000_sibling_scope_order_generation_permutation",
        "structural_catalog_commit_response_loss_replay_before_source_path",
        "structural_effect_bounds_refuse_before_source_write",
        "move_cycle_and_cross_workspace",
        "duplicate_operation_request_conflict_before_file_io",
        "duplicate_deterministic_page_block_file_identity_collision",
        "duplicate_file_copy_partial_failure",
        "duplicate_process_loss_before_source_commit_reuses_or_cleans_exact_mapping",
        "duplicate_source_commit_response_loss_exact_replay",
        "trash_preexisting_inactive_descendant_excluded_from_cohort",
        "restore_identity_reuse",
        "restore_never_silently_restores_ancestors",
        "restore_unrelated_trashed_descendant_remains_trashed",
        "official_archive_not_aliased_to_legacy_trash",
        "candidate_1316_raw_sql_context_and_ambiguous_history_refusal",
        "lifecycle_subtree_transitioned_and_unchanged_effect_authority",
        "duplicate_catalog_binding_projection_attestation_tree_atomic_finalize",
        "trash_catalog_projection_removal_and_tree_transition_atomic_finalize",
        "restore_catalog_exact_cohort_atomic_finalize",
        "lifecycle_subtree_catalog_replay_before_sentinel_path_and_source_io",
        "lifecycle_subtree_catalog_rollback_after_source_commit",
        "lifecycle_subtree_source_receipt_ack_loss_replays_before_precondition",
        "lifecycle_subtree_source_applied_catalog_fault_resume",
        "lifecycle_subtree_catalog_fault_boundaries_all_rollback",
        "lifecycle_subtree_exact_11004_effect_bound_and_overflow_refusal",
        "lifecycle_subtree_report_identifier_path_digest_and_lease_redaction",
        "lifecycle_subtree_unconfigured_raw_writer_denied",
        "official_archive_source_version_does_not_reuse_or_skip_h5e_1304",
        "official_archive_keeps_binding_and_locator_projection_addressable",
        "official_archive_default_navigation_search_and_database_views_hidden",
        "official_archive_nested_unarchive_semantics_require_live_characterization",
        "official_archive_public_link_behavior_not_assumed",
        "official_archive_schema_runtime_and_live_migration_remain_disabled",
        "permanent_delete_disabled",
    ):
        require(name in required_faults, f"missing fault test: {name}")

    require(
        value.get("implementation_sequence")
        == [
            "NP2C-0-design-and-machine-contract",
            "NP2C-1-1311-tree-and-command-foundation",
            "NP2C-2a-offline-initial-materializer",
            "NP2C-2b-lifecycle-command-kernel",
            "NP2C-3a-rename-source-adapter-design-and-receipt-contract",
            "NP2C-3b-v3-ledger-backed-rename-source-adapter",
            "NP2C-3c-1313-atomic-catalog-rename-finalize",
            "NP2C-3d-offline-rename-response-loss-rehearsal",
            "NP2C-3e-create-move-reorder-contract-design",
            "NP2C-3e1-action-specific-legacy-v3-source-adapters",
            "NP2C-3e2-frozen-1313-to-1314-schema-and-guards",
            "NP2C-3e3-atomic-catalog-finalizers-and-sentinel-rehearsal",
            "NP2C-4a-duplicate-trash-restore-contract-design",
            "NP2C-4b-route-unregistered-source-v3-adapters-and-file-recovery",
            "NP2C-4c-frozen-1315-to-1316-lifecycle-subtree-schema-and-guards",
            "NP2C-4d-atomic-catalog-finalizers-and-exact-sentinel-rehearsal",
            "NP2C-4e-independent-audit-and-bounded-adversarial-rehearsal",
            "NP2C-5-official-archive-source-contract-design",
            "NP2C-6-protected-routes-and-default-off-ui",
            "NP2C-7-retention-and-purge-approval",
        ],
        "implementation sequence is invalid",
    )

    stops = set(value.get("stop_conditions", []))
    for condition in (
        "file_ownership_or_hash_drift",
        "protected_boundary_change_required",
        "frozen_1310_contract_mismatch",
        "source_schema_version_conflicts_with_h5e_lineage",
        "hidden_identity_or_secret_leak",
        "destructive_purge_required",
        "continuous_worker_or_resolver_cutover_required",
        "live_migration_or_feature_enable_required",
        "route_auth_second_brain_or_knowledge_regression",
    ):
        require(condition in stops, f"missing stop condition: {condition}")

    return {
        "valid": not errors,
        "error_count": len(errors),
        "errors": errors,
        "status": value.get("status"),
        "current_workspace_schema_version": (
            implementation.get("current_workspace_schema_version")
            if isinstance(implementation, dict)
            else None
        ),
        "candidate_workspace_schema_version": (
            implementation.get("candidate_workspace_schema_version")
            if isinstance(implementation, dict)
            else None
        ),
        "runtime_commands_enabled": (
            implementation.get("runtime_commands_enabled")
            if isinstance(implementation, dict)
            else None
        ),
        "permanent_delete_enabled": (
            implementation.get("permanent_delete_enabled")
            if isinstance(implementation, dict)
            else None
        ),
    }


def verify_contract(path: str | Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    try:
        return validate_contract(load_contract(path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "valid": False,
            "error_count": 1,
            "errors": [str(exc)],
            "status": None,
            "current_workspace_schema_version": None,
            "candidate_workspace_schema_version": None,
            "runtime_commands_enabled": None,
            "permanent_delete_enabled": None,
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    args = parser.parse_args(argv)
    report = verify_contract(args.contract)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
