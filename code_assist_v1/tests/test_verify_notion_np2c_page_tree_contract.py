from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from code_assist_v1.tools.verify_notion_np2c_page_tree_contract import (
    load_contract,
    validate_contract,
    verify_contract,
)


def test_checked_in_np2c_contract_is_frozen_and_runtime_disabled() -> None:
    assert verify_contract() == {
        "valid": True,
        "error_count": 0,
        "errors": [],
        "status": "official_archive_source_contract_designed_source_v5_and_workspace_1318_blocked_default_off",
        "current_workspace_schema_version": 2026081317,
        "candidate_workspace_schema_version": 2026081318,
        "runtime_commands_enabled": False,
        "permanent_delete_enabled": False,
    }


def test_np2c_contract_rejects_unsafe_or_premature_changes() -> None:
    contract = load_contract()

    cases: list[tuple[str, dict]] = []
    for field in (
        "live_migration_applied",
        "runtime_commands_enabled",
        "permanent_delete_enabled",
        "resolver_cutover_enabled",
        "continuous_worker_enabled",
        "destructive_watermark_ack_enabled",
    ):
        changed = deepcopy(contract)
        changed["implementation"][field] = True
        cases.append((field, changed))

    for field in (
        "command_kernel_implemented",
        "source_effect_orchestration_implemented",
        "rename_source_adapter_contract_designed",
        "rename_source_adapter_implemented",
        "rename_catalog_finalize_contract_designed",
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
        changed = deepcopy(contract)
        changed["implementation"][field] = False
        cases.append((field, changed))

    for field in (
        "official_archive_unarchive_source_contract_implemented",
        "candidate_1318_schema_implemented",
        "official_archive_catalog_finalizer_implemented",
        "official_archive_rehearsal_implemented",
    ):
        changed = deepcopy(contract)
        changed["implementation"][field] = True
        cases.append((f"premature_{field}", changed))

    exactly_once = deepcopy(contract)
    exactly_once["authority"]["cross_database_exactly_once_claimed"] = True
    cases.append(("exactly_once", exactly_once))

    bare_parent = deepcopy(contract)
    bare_parent["canonical_identity"]["bare_page_id_parent_allowed"] = True
    cases.append(("bare_parent", bare_parent))

    reuse = deepcopy(contract)
    reuse["canonical_identity"]["recreated_id_inherits_prior_identity"] = True
    cases.append(("recreated_identity", reuse))

    fabricated = deepcopy(contract)
    fabricated["candidate_schema"]["fabricate_tree_rows_during_migration"] = True
    cases.append(("fabricated_tree", fabricated))

    cycle = deepcopy(contract)
    cycle["tree_invariants"]["cycles_allowed"] = True
    cases.append(("cycle", cycle))

    raw_token = deepcopy(contract)
    raw_token["command_protocol"]["lease_token_persisted_raw"] = True
    cases.append(("raw_lease", raw_token))

    silent_ancestor = deepcopy(contract)
    silent_ancestor["action_invariants"]["restore_silently_restores_ancestors"] = True
    cases.append(("silent_ancestor", silent_ancestor))

    source_open = deepcopy(contract)
    source_open["read_contract"]["structural_snapshot_source_database_open_count"] = 1
    cases.append(("structural_source_open", source_open))

    unbounded = deepcopy(contract)
    unbounded["bounds"]["synchronous_subtree_max_nodes"] = 1_000_000
    cases.append(("unbounded_subtree", unbounded))

    repeated_integrity = deepcopy(contract)
    repeated_integrity["bounds"][
        "maximum_full_integrity_checks_per_owner_per_new_effect"
    ] = 2
    cases.append(("repeated_owner_integrity", repeated_integrity))

    runtime_materializer = deepcopy(contract)
    runtime_materializer["offline_initial_materializer"]["runtime_registered"] = True
    cases.append(("runtime_materializer", runtime_materializer))

    late_replay = deepcopy(contract)
    late_replay["offline_initial_materializer"][
        "replay_precedes_sentinel_and_source_path_access"
    ] = False
    cases.append(("late_replay", late_replay))

    real_source = deepcopy(contract)
    real_source["command_protocol"][
        "real_or_live_legacy_source_mutation_performed"
    ] = True
    cases.append(("real_source_mutation", real_source))

    lifecycle_late_replay = deepcopy(contract)
    lifecycle_late_replay["command_protocol"][
        "exact_command_replay_precedes_quiescence_and_source_io"
    ] = False
    cases.append(("lifecycle_late_replay", lifecycle_late_replay))

    atomic_save_reuse = deepcopy(contract)
    atomic_save_reuse["rename_source_adapter_design"][
        "atomic_save_operation_table_reused"
    ] = True
    cases.append(("atomic_save_reuse", atomic_save_reuse))

    source_v4_collision = deepcopy(contract)
    source_v4_collision["rename_source_adapter_design"][
        "new_legacy_source_migration_required"
    ] = True
    cases.append(("source_v4_collision", source_v4_collision))

    raw_title = deepcopy(contract)
    raw_title["rename_source_adapter_design"]["raw_title_persisted_in_catalog"] = True
    cases.append(("raw_title", raw_title))

    no_source_replay = deepcopy(contract)
    no_source_replay["rename_source_adapter_design"][
        "source_operation_exact_replay_precedes_page_read"
    ] = False
    cases.append(("source_replay_late", no_source_replay))

    runtime_source_adapter = deepcopy(contract)
    runtime_source_adapter["rename_source_adapter_design"][
        "adapter_runtime_registered"
    ] = True
    cases.append(("runtime_source_adapter", runtime_source_adapter))

    source_adapter_live_data = deepcopy(contract)
    source_adapter_live_data["rename_source_adapter_design"][
        "adapter_live_source_accessed"
    ] = True
    cases.append(("source_adapter_live_data", source_adapter_live_data))

    unbounded_version = deepcopy(contract)
    unbounded_version["bounds"]["rename_page_version_max_bytes"] = 1 << 40
    cases.append(("unbounded_rename_version", unbounded_version))

    fake_healing_authority = deepcopy(contract)
    fake_healing_authority["rename_catalog_finalize_design"][
        "healing_run_context_reused_as_lifecycle_authority"
    ] = True
    cases.append(("fake_healing_authority", fake_healing_authority))

    premature_complete = deepcopy(contract)
    premature_complete["rename_catalog_finalize_design"][
        "tree_materialization_state_after_changed_rename"
    ] = "complete"
    cases.append(("premature_complete", premature_complete))

    no_full_sweep = deepcopy(contract)
    no_full_sweep["rename_catalog_finalize_design"][
        "full_sweep_required_after_changed_rename"
    ] = False
    cases.append(("no_full_sweep", no_full_sweep))

    late_catalog_replay = deepcopy(contract)
    late_catalog_replay["offline_rename_rehearsal"][
        "finalized_catalog_replay_precedes_sentinel_and_source_path_access"
    ] = False
    cases.append(("late_catalog_replay", late_catalog_replay))

    rehearsal_runtime = deepcopy(contract)
    rehearsal_runtime["offline_rename_rehearsal"]["runtime_registered"] = True
    cases.append(("rename_rehearsal_runtime", rehearsal_runtime))

    missing_1314 = deepcopy(contract)
    missing_1314["implementation"]["candidate_1314_schema_implemented"] = False
    cases.append(("missing_1314_implementation", missing_1314))

    missing_1315 = deepcopy(contract)
    missing_1315["implementation"]["candidate_1315_schema_implemented"] = False
    cases.append(("missing_1315_implementation", missing_1315))

    unsafe_finalize = deepcopy(contract)
    unsafe_finalize["structural_finalize_schema"][
        "root_parent_comparison_is_null_safe"
    ] = False
    cases.append(("unsafe_structural_finalize", unsafe_finalize))

    unguarded_structural_schema = deepcopy(contract)
    unguarded_structural_schema["structural_guard_schema"][
        "source_receipt_and_command_context_udfs_fail_closed"
    ] = False
    cases.append(("unguarded_structural_schema", unguarded_structural_schema))

    source_v4_reuse = deepcopy(contract)
    source_v4_reuse["create_move_reorder_design"][
        "new_legacy_source_migration_required"
    ] = True
    cases.append(("structural_source_v4_collision", source_v4_reuse))

    wrong_structural_adapter = deepcopy(contract)
    wrong_structural_adapter["create_move_reorder_design"][
        "source_adapter_module"
    ] = "code_assist_v1.notion_store"
    cases.append(("wrong_structural_adapter", wrong_structural_adapter))

    live_structural_adapter = deepcopy(contract)
    live_structural_adapter["create_move_reorder_design"][
        "source_adapter_live_source_accessed"
    ] = True
    cases.append(("live_structural_adapter", live_structural_adapter))

    untested_structural_adapter = deepcopy(contract)
    untested_structural_adapter["create_move_reorder_design"][
        "source_adapter_temporary_source_tests_passed"
    ] = 0
    cases.append(("untested_structural_adapter", untested_structural_adapter))

    rename_receipt_reuse = deepcopy(contract)
    rename_receipt_reuse["create_move_reorder_design"]["source_result_contracts"][
        "create"
    ] = "np2c_legacy_page_rename_receipt_v1"
    cases.append(("rename_receipt_reuse", rename_receipt_reuse))

    no_order_generation = deepcopy(contract)
    no_order_generation["create_move_reorder_design"][
        "catalog_finalize_uses_order_generation_to_avoid_transient_ordinal_collision"
    ] = False
    cases.append(("no_order_generation", no_order_generation))

    optimistic_delta = deepcopy(contract)
    optimistic_delta["create_move_reorder_design"][
        "fixed_generation_delta_assumed"
    ] = True
    cases.append(("fixed_generation_delta", optimistic_delta))

    late_structural_replay = deepcopy(contract)
    late_structural_replay["create_move_reorder_design"][
        "source_operation_exact_replay_precedes_page_path_and_quiescence"
    ] = False
    cases.append(("late_structural_replay", late_structural_replay))

    late_catalog_structural_replay = deepcopy(contract)
    late_catalog_structural_replay["create_move_reorder_design"][
        "catalog_finalized_replay_precedes_sentinel_path_and_source_io"
    ] = False
    cases.append(("late_catalog_structural_replay", late_catalog_structural_replay))

    no_generation_coalescing = deepcopy(contract)
    no_generation_coalescing["create_move_reorder_design"][
        "page_watermark_statements_coalesced_to_one_logical_generation_inside_source_transaction"
    ] = False
    cases.append(("no_generation_coalescing", no_generation_coalescing))

    cross_owner_move = deepcopy(contract)
    cross_owner_move["create_move_reorder_design"][
        "cross_owner_or_workspace_move_allowed"
    ] = True
    cases.append(("cross_owner_move", cross_owner_move))

    unbounded_structural = deepcopy(contract)
    unbounded_structural["bounds"][
        "structural_effect_max_aggregate_bytes"
    ] = 1 << 40
    cases.append(("unbounded_structural_bytes", unbounded_structural))

    premature_runtime = deepcopy(contract)
    premature_runtime["create_move_reorder_design"]["runtime_registered"] = True
    cases.append(("structural_runtime_registered", premature_runtime))

    archive_alias = deepcopy(contract)
    archive_alias["duplicate_trash_restore_design"][
        "official_archive_distinct_from_delete_or_trash"
    ] = False
    cases.append(("archive_aliased_to_trash", archive_alias))

    legacy_name_lie = deepcopy(contract)
    legacy_name_lie["duplicate_trash_restore_design"][
        "legacy_archive_page_is_http_delete_trash_compatibility"
    ] = False
    cases.append(("legacy_archive_name_lie", legacy_name_lie))

    lifecycle_source_v4_collision = deepcopy(contract)
    lifecycle_source_v4_collision["duplicate_trash_restore_design"][
        "new_source_schema_required_for_duplicate_trash_restore"
    ] = True
    cases.append(("lifecycle_source_v4_collision", lifecycle_source_v4_collision))

    missing_lifecycle_source_adapter = deepcopy(contract)
    missing_lifecycle_source_adapter["duplicate_trash_restore_design"][
        "source_adapter_implemented"
    ] = False
    cases.append(("missing_np2c4_source_adapter", missing_lifecycle_source_adapter))

    missing_archive_design = deepcopy(contract)
    missing_archive_design["implementation"][
        "official_archive_unarchive_contract_designed"
    ] = False
    cases.append(("missing_archive_design", missing_archive_design))

    archive_takes_v4 = deepcopy(contract)
    archive_takes_v4["official_archive_design"]["source_lineage"][
        "candidate_archive_source_version"
    ] = 2026081304
    cases.append(("archive_takes_h5e_v4", archive_takes_v4))

    archive_skips_v4 = deepcopy(contract)
    archive_skips_v4["official_archive_design"]["source_lineage"][
        "source_version_skip_allowed"
    ] = True
    cases.append(("archive_skips_v4", archive_skips_v4))

    archive_reuses_trash_bit = deepcopy(contract)
    archive_reuses_trash_bit["official_archive_design"]["legacy_compatibility"][
        "legacy_pages_archived_bit_reused_for_official_archive"
    ] = True
    cases.append(("archive_reuses_trash_bit", archive_reuses_trash_bit))

    archive_drops_locator = deepcopy(contract)
    archive_drops_locator["official_archive_design"]["catalog_contract"][
        "locator_projection_retained"
    ] = False
    cases.append(("archive_drops_locator", archive_drops_locator))

    archive_quarantines_binding = deepcopy(contract)
    archive_quarantines_binding["official_archive_design"]["catalog_contract"][
        "binding_state_while_archived"
    ] = "quarantined"
    cases.append(("archive_quarantines_binding", archive_quarantines_binding))

    archive_client_flag_authority = deepcopy(contract)
    archive_client_flag_authority["official_archive_design"]["authorization"][
        "client_flag_is_authority"
    ] = True
    cases.append(("archive_client_flag_authority", archive_client_flag_authority))

    archive_public_behavior_assumed = deepcopy(contract)
    archive_public_behavior_assumed["official_archive_design"]["visibility"][
        "public_or_published_link_behavior_assumed"
    ] = True
    cases.append(("archive_public_behavior_assumed", archive_public_behavior_assumed))

    premature_archive_source = deepcopy(contract)
    premature_archive_source["official_archive_design"]["source_state_contract"][
        "source_schema_implemented"
    ] = True
    cases.append(("premature_archive_source", premature_archive_source))

    guessed_nested_unarchive = deepcopy(contract)
    guessed_nested_unarchive["official_archive_design"][
        "nested_unarchive_semantics_frozen"
    ] = True
    cases.append(("guessed_nested_unarchive", guessed_nested_unarchive))

    public_duplicate = deepcopy(contract)
    public_duplicate["duplicate_trash_restore_design"]["public_duplicate_in_scope"] = True
    cases.append(("public_duplicate_scope_leak", public_duplicate))

    late_lifecycle_source_replay = deepcopy(contract)
    late_lifecycle_source_replay["duplicate_trash_restore_design"][
        "source_operation_exact_replay_precedes_quiescence_path_page_and_file_io"
    ] = False
    cases.append(("late_np2c4_source_replay", late_lifecycle_source_replay))

    lost_source_commit_resume = deepcopy(contract)
    lost_source_commit_resume["lifecycle_subtree_finalize_schema"][
        "source_commit_replay_precedes_precondition_recapture"
    ] = False
    cases.append(("lost_np2c4_source_commit_resume", lost_source_commit_resume))

    missing_catalog_fault_boundary = deepcopy(contract)
    missing_catalog_fault_boundary["lifecycle_subtree_finalize_schema"][
        "catalog_fault_boundaries_tested"
    ] = ["after_bindings", "after_tree"]
    cases.append(("missing_np2c4_catalog_fault_boundary", missing_catalog_fault_boundary))

    unverified_effect_bound = deepcopy(contract)
    unverified_effect_bound["duplicate_trash_restore_design"][
        "exact_11004_effect_bound_and_overflow_refusal_tested"
    ] = False
    cases.append(("unverified_np2c4_effect_bound", unverified_effect_bound))

    random_duplicate_ids = deepcopy(contract)
    random_duplicate_ids["duplicate_trash_restore_design"][
        "duplicate_page_block_file_mappings_domain_separated_and_replay_stable"
    ] = False
    cases.append(("random_duplicate_ids", random_duplicate_ids))

    visible_file_before_rows = deepcopy(contract)
    visible_file_before_rows["duplicate_trash_restore_design"][
        "duplicate_file_bytes_visible_before_source_rows"
    ] = True
    cases.append(("visible_file_before_rows", visible_file_before_rows))

    delete_unknown_bytes = deepcopy(contract)
    delete_unknown_bytes["duplicate_trash_restore_design"][
        "duplicate_unknown_file_outcome_auto_deleted"
    ] = True
    cases.append(("delete_unknown_duplicate_bytes", delete_unknown_bytes))

    broad_trash = deepcopy(contract)
    broad_trash["duplicate_trash_restore_design"][
        "trash_already_inactive_descendants_added_to_cohort"
    ] = True
    cases.append(("broad_trash_cohort", broad_trash))

    recreated_restore = deepcopy(contract)
    recreated_restore["duplicate_trash_restore_design"][
        "restore_recreated_page_id_accepted"
    ] = True
    cases.append(("recreated_restore", recreated_restore))

    invalid_restore_placement = deepcopy(contract)
    invalid_restore_placement["duplicate_trash_restore_design"][
        "restore_without_valid_placement_mutates_source"
    ] = True
    cases.append(("invalid_restore_placement", invalid_restore_placement))

    relabel_history = deepcopy(contract)
    relabel_history["duplicate_trash_restore_design"][
        "migration_silently_relabels_historical_archived_nodes"
    ] = True
    cases.append(("silent_archived_history_relabel", relabel_history))

    missing_1316 = deepcopy(contract)
    missing_1316["duplicate_trash_restore_design"]["candidate_1316_implemented"] = False
    cases.append(("missing_1316", missing_1316))

    missing_subtree_guard = deepcopy(contract)
    missing_subtree_guard["lifecycle_subtree_guard_schema"][
        "trash_and_restore_binding_transition_guards_added"
    ] = False
    cases.append(("missing_subtree_guard", missing_subtree_guard))

    missing_subtree_finalizer = deepcopy(contract)
    missing_subtree_finalizer["lifecycle_subtree_finalize_schema"][
        "duplicate_trash_restore_atomic_catalog_finalizer_added"
    ] = False
    cases.append(("missing_subtree_finalizer", missing_subtree_finalizer))

    unsafe_unchanged_effect = deepcopy(contract)
    unsafe_unchanged_effect["lifecycle_subtree_finalize_schema"][
        "unchanged_parent_and_sibling_effects_authorized"
    ] = False
    cases.append(("missing_unchanged_effect_authority", unsafe_unchanged_effect))

    late_subtree_replay = deepcopy(contract)
    late_subtree_replay["lifecycle_subtree_finalize_schema"][
        "finalized_replay_precedes_sentinel_path_quiescence_and_source_io"
    ] = False
    cases.append(("late_subtree_finalized_replay", late_subtree_replay))

    premature_subtree_finalizer = deepcopy(contract)
    premature_subtree_finalizer["lifecycle_subtree_guard_schema"][
        "catalog_finalizer_added"
    ] = True
    cases.append(("premature_subtree_finalizer", premature_subtree_finalizer))

    runtime_np2c4 = deepcopy(contract)
    runtime_np2c4["duplicate_trash_restore_design"]["runtime_registered"] = True
    cases.append(("runtime_np2c4", runtime_np2c4))

    unbounded_duplicate_bytes = deepcopy(contract)
    unbounded_duplicate_bytes["bounds"]["duplicate_aggregate_file_bytes_max"] = 1 << 40
    cases.append(("unbounded_duplicate_bytes", unbounded_duplicate_bytes))

    for name, changed in cases:
        assert validate_contract(changed)["valid"] is False, name


def test_np2c_contract_rejects_duplicate_keys_and_nonfinite_numbers(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"contract_version":1,"contract_version":1}', encoding="utf-8")
    report = verify_contract(duplicate)
    assert report["valid"] is False
    assert "duplicate JSON object key" in report["errors"][0]

    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"contract_version":NaN}', encoding="utf-8")
    report = verify_contract(nonfinite)
    assert report["valid"] is False
    assert "non-finite JSON number" in report["errors"][0]
