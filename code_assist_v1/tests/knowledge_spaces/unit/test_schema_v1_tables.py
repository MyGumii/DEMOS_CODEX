from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3

import pytest

from code_assist_v1.knowledge_core.migrations.v001_indexes import (
    INDEX_DEFINITIONS,
    INDEX_NAMES,
    INDEX_STATEMENTS,
)
from code_assist_v1.knowledge_core.migrations.v001_seed import (
    RESPONSIBILITY_ROOT_ID,
    RESPONSIBILITY_ROOT_NAME,
    RESPONSIBILITY_ROOT_NAME_KEY,
    SEED_NAMES,
    SEED_STATEMENTS,
)
from code_assist_v1.knowledge_core.migrations.v001_tables import (
    TABLE_DEFINITIONS,
    TABLE_NAMES,
    TABLE_STATEMENTS,
)


EXPECTED_COLUMNS = {
    "schema_migration": ("version", "name", "checksum_sha256", "applied_at", "app_version", "sqlite_version", "sqlite_source_id"),
    "org_unit": ("id", "kind", "parent_id", "name", "name_key", "status", "created_at", "updated_at", "created_by", "updated_by", "row_version"),
    "app_user": ("id", "display_name", "password_hash", "system_role", "account_status", "current_part_id", "scope_epoch", "created_at", "updated_at", "disabled_at", "row_version"),
    "org_membership": ("id", "user_id", "part_id", "valid_from", "valid_to", "assigned_by", "reason"),
    "org_role_assignment": ("id", "user_id", "org_unit_id", "role", "valid_from", "valid_to", "assigned_by", "reason"),
    "knowledge_space": ("id", "space_kind", "owner_user_id", "owner_org_unit_id", "status", "write_enabled", "generation", "created_at", "updated_at", "row_version"),
    "conversation": ("id", "owner_user_id", "space_id", "captured_scope_epoch", "title", "status", "metadata_json", "created_at", "updated_at", "row_version"),
    "conversation_message": ("id", "conversation_id", "sequence_no", "role", "content_json", "created_at"),
    "conversation_turn": ("id", "conversation_id", "actor_user_id", "dedup_id", "operation_id", "request_sha256", "state", "user_message_id", "assistant_message_id", "final_response_json", "error_json", "created_at", "updated_at", "completed_at", "row_version"),
    "conversation_turn_event": ("turn_id", "conversation_id", "sequence_no", "event_type", "payload_json", "created_at"),
    "conversation_context_binding": ("id", "conversation_id", "binding_type", "binding_mode", "external_key", "source_artifact_id", "snapshot_sha256", "display_metadata_json", "status", "status_reason", "created_by", "created_at", "status_changed_at", "detached_at", "row_version"),
    "conversation_summary": ("id", "conversation_id", "covered_through_sequence_no", "summary_md", "summary_sha256", "model_id", "prompt_version", "summarizer_version", "status", "created_by_user_id", "created_at", "invalidated_at", "invalidation_reason", "row_version"),
    "conversation_pin": ("conversation_id", "space_id", "item_id", "revision_id", "created_by", "created_at"),
    "blob_object": ("sha256", "size_bytes", "media_type", "relative_path", "verification_status", "created_at", "verified_at"),
    "source_artifact": ("id", "origin_type", "origin_key", "origin_revision_key", "raw_sha256", "raw_size_bytes", "media_type", "original_filename", "body_retention_status", "body_retention_reason", "body_purged_at", "orphan_cleanup_eligible_at", "publisher_user_id", "source_owner_snapshot_json", "metadata_json", "metadata_schema_version", "created_at"),
    "artifact_blob": ("id", "artifact_id", "content_sha256", "size_bytes", "role", "logical_name", "media_type", "ordinal", "body_retention_status", "created_at"),
    "artifact_blob_body": ("artifact_blob_id", "content_sha256", "created_at"),
    "extraction_snapshot": ("id", "artifact_id", "status", "extracted_sha256", "normalized_content_sha256", "extracted_size_bytes", "normalizer_version", "extractor_name", "extractor_version", "warnings_json", "body_retention_status", "created_at"),
    "extraction_snapshot_body": ("snapshot_id", "artifact_id", "content_md", "content_blob_hash", "content_size_bytes", "content_sha256", "created_at"),
    "publication_preview": ("id", "actor_user_id", "artifact_id", "extraction_snapshot_id", "durable_job_id", "source_snapshot_sha256", "captured_part_id", "captured_scope_epoch", "available_targets_json", "rule_version", "deterministic_findings_json", "ready_result_json", "result_schema_version", "status", "error_json", "expires_at", "created_at", "updated_at", "completed_at", "row_version"),
    "lineage_group": ("id", "initial_artifact_id", "base_artifact_id", "base_extraction_snapshot_id", "base_content_sha256", "sync_policy", "created_by", "created_at"),
    "lineage_member": ("lineage_group_id", "item_id", "initial_revision_id", "derived_from_item_id", "derived_from_revision_id", "member_role", "created_by", "created_at"),
    "operation_dedup": ("id", "actor_user_id", "operation_type", "operation_id", "request_sha256", "status", "http_status", "result_json", "error_json", "created_at", "completed_at", "last_seen_at"),
    "publication_operation": ("id", "dedup_id", "actor_user_id", "operation_id", "request_sha256", "artifact_id", "extraction_snapshot_id", "target_mode", "lineage_group_id", "created_at"),
    "publication_target": ("publication_operation_id", "target_space_id", "outcome", "item_id", "resolved_revision_id", "created_revision_id", "curation_finding_id", "response_json", "created_at"),
    "knowledge_item": ("id", "space_id", "lineage_group_id", "lifecycle_status", "curation_status", "current_revision_id", "last_curation_transition_id", "latest_deactivation_episode_id", "redirect_item_id", "created_by", "updated_by", "created_at", "updated_at", "row_version"),
    "item_curation_transition": ("id", "item_id", "space_id", "transition_kind", "from_status", "to_status", "base_revision_id", "result_revision_id", "finding_id", "rescan_job_id", "decision_job_id", "dedup_id", "operation_id", "actor_kind", "actor_user_id", "result_item_row_version", "reason", "created_at"),
    "knowledge_revision": ("id", "item_id", "revision_no", "parent_revision_id", "base_revision_id", "title", "raw_content_sha256", "normalized_content_sha256", "content_size_bytes", "normalizer_version", "metadata_json", "change_kind", "change_reason", "created_by", "created_at"),
    "knowledge_revision_body": ("revision_id", "item_id", "content_md", "content_blob_hash", "content_size_bytes", "content_sha256", "created_at"),
    "item_source": ("id", "item_id", "revision_id", "artifact_id", "extraction_snapshot_id", "role", "created_by", "created_at"),
    "item_relation": ("id", "source_item_id", "source_revision_id", "target_item_id", "target_revision_id", "relation_type", "relation_status", "evidence_json", "created_by", "updated_by", "created_at", "updated_at", "row_version"),
    "item_deactivation_episode": ("id", "item_id", "episode_no", "deactivated_by", "deactivated_at", "reason", "purge_eligible_at", "restored_by", "restored_at", "purged_by", "purged_at"),
    "knowledge_edge": ("id", "space_id", "source_item_id", "target_item_id", "edge_type", "edge_status", "origin", "source_evidence_revision_id", "target_evidence_revision_id", "approval_finding_id", "evidence_json", "needs_revalidation", "created_by", "updated_by", "created_at", "updated_at", "row_version"),
    "curation_finding": ("id", "space_id", "finding_kind", "risk_tier", "severity", "status", "decision_mode", "required_reviewer", "visibility_effect", "proposed_action", "source_item_id", "source_revision_id", "target_item_id", "target_revision_id", "detector_name", "detector_version", "rule_version", "model_id", "prompt_version", "confidence", "evidence_json", "proposed_action_json", "reviewed_by", "reviewed_at", "review_reason", "applied_operation_id", "applied_dedup_id", "applied_job_id", "applied_by", "applied_at", "last_application_error_json", "created_actor_kind", "created_by", "created_at", "updated_at", "row_version"),
    "change_proposal": ("id", "finding_id", "finding_input_row_version", "proposal_kind", "proposal_series_id", "proposal_version", "supersedes_proposal_id", "lineage_group_id", "base_item_id", "base_revision_id", "source_item_id", "source_revision_id", "target_item_id", "target_revision_id", "source_artifact_id", "extraction_snapshot_id", "proposed_content_md", "proposed_content_blob_hash", "proposed_content_size_bytes", "proposed_content_sha256", "preview_sha256", "state", "applied_operation_id", "applied_dedup_id", "last_application_error_json", "created_by", "updated_by", "created_at", "updated_at", "row_version"),
    "merge_event": ("id", "dedup_id", "space_id", "source_item_id", "source_revision_id", "target_item_id", "target_revision_id", "result_revision_id", "preview_sha256", "relation_id", "actor_user_id", "created_at"),
    "item_lease": ("item_id", "holder_user_id", "editor_session_id", "token_hmac", "hmac_key_id", "fencing_generation", "base_item_row_version", "base_revision_id", "acquired_at", "heartbeat_at", "expires_at", "absolute_expires_at"),
    "audit_event": ("id", "event_uuid", "occurred_at", "request_id", "operation_id", "action", "outcome", "actor_kind", "actor_user_id", "actor_display_name", "actor_role_snapshot", "space_id", "item_id", "base_revision_id", "new_revision_id", "reason", "details_json", "request_meta_json", "schema_version"),
    "outbox_event": ("id", "logical_key", "space_id", "aggregate_type", "aggregate_id", "aggregate_version", "event_type", "payload_json", "payload_schema_version", "state", "available_at", "claim_owner", "claim_expires_at", "attempt_count", "last_error_json", "created_at", "completed_at"),
    "durable_job": ("id", "dedup_id", "operation_id", "actor_kind", "actor_user_id", "space_id", "job_type", "handler_version", "effect_key", "input_ref_json", "input_sha256", "expected_state_json", "authorization_snapshot_json", "authorization_snapshot_sha256", "authorization_scope_epoch", "authorization_part_id", "authorization_capabilities_json", "state", "claim_owner", "claim_expires_at", "attempt_count", "max_attempts", "cancellable", "cancel_requested", "progress_permille", "result_json", "error_json", "available_at", "created_at", "updated_at", "completed_at"),
    "projection_state": ("projection_name", "space_id", "desired_generation", "built_generation", "checksum_sha256", "status", "last_error_json", "updated_at", "built_at", "row_version"),
    "backup_run": ("id", "state", "snapshot_db_relative_path", "snapshot_db_sha256", "manifest_sha256", "snapshot_audit_high_water", "schema_version", "sqlite_source_id", "error_json", "created_at", "completed_at", "row_version"),
    "backup_blob": ("backup_run_id", "blob_sha256", "size_bytes", "status", "copied_at", "verified_at", "error_json"),
    "blob_gc_candidate": ("blob_sha256", "unreferenced_since", "delete_after", "state", "claim_owner", "claim_expires_at", "attempt_count", "last_error_json", "updated_at"),
    "legacy_migration_map": ("id", "migration_run_id", "legacy_kind", "normalized_path", "source_sha256", "resolved_owner_user_id", "artifact_id", "item_id", "revision_id", "edge_id", "status", "details_json", "created_at", "updated_at"),
    "search_document": ("rowid", "item_id", "space_id", "revision_id", "title", "content_md", "updated_at"),
}

EXPECTED_INDEX_NAMES = (
    "ux_org_unit_active_sibling", "ix_org_unit_parent", "ix_app_user_part_status", "ix_app_user_status",
    "ux_org_membership_current_user", "ix_org_membership_part_period", "ix_org_membership_user_history",
    "ux_org_role_active_unit_role", "ix_org_role_user", "ux_space_personal_owner", "ux_space_org_owner",
    "ix_space_kind_status", "ux_conversation_id_space", "ix_conversation_owner_time", "ix_conversation_space_time",
    "ux_message_id_conversation", "ux_message_sequence", "ix_message_conversation_time", "ux_turn_id_conversation",
    "ux_turn_conversation_operation", "ux_turn_dedup", "ix_turn_conversation_time", "ix_turn_state",
    "ix_turn_event_conversation", "ux_context_binding_attached", "ix_context_binding_conversation",
    "ux_conversation_summary_active", "ix_conversation_summary_history", "ix_pin_item", "ux_blob_relative_path",
    "ix_artifact_origin", "ix_artifact_raw_hash", "ix_artifact_retention", "ux_artifact_blob_identity",
    "ux_artifact_blob_id_hash", "ux_artifact_raw_role", "ix_artifact_blob_content_hash",
    "ix_artifact_blob_body_hash", "ux_extraction_id_artifact", "ix_extraction_artifact_time",
    "ix_extraction_body_blob", "ix_preview_actor_status_expiry", "ux_preview_durable_job",
    "ux_lineage_member_item", "ix_lineage_member_derived", "ux_operation_dedup_actor_type_id",
    "ix_operation_dedup_cleanup", "ux_publication_actor_operation", "ux_publication_dedup",
    "ix_publication_target_item", "ix_publication_target_finding", "ix_item_visible", "ix_item_lineage",
    "ix_item_redirect", "ix_item_deactivation", "ix_item_curation_transition", "ux_curation_transition_id_item",
    "ux_curation_transition_dedup", "ux_curation_transition_job", "ux_curation_transition_decision_job",
    "ux_curation_transition_item_version", "ix_curation_transition_item", "ux_revision_id_item",
    "ux_revision_number", "ix_revision_item_time", "ix_revision_body_blob", "ux_item_source_identity",
    "ix_item_source_artifact", "ix_item_source_revision", "ux_item_relation_active", "ix_item_relation_target",
    "ux_deactivation_id_item", "ux_deactivation_episode", "ux_deactivation_open", "ix_deactivation_eligibility",
    "ux_edge_active_identity", "ux_edge_approval_finding", "ix_edge_reverse", "ix_edge_revalidation",
    "ix_finding_review_queue", "ix_finding_source_revision", "ix_finding_target_revision",
    "ix_finding_ignored_pair", "ux_finding_apply_dedup", "ix_finding_apply_job", "ux_proposal_series_version",
    "ux_proposal_finding", "ux_proposal_superseded_once", "ux_proposal_apply_dedup",
    "ix_proposal_target_state", "ix_proposal_source", "ix_proposal_supersedes", "ux_merge_dedup",
    "ux_merge_relation", "ix_lease_expiry", "ix_lease_holder", "ix_audit_space_time", "ix_audit_item_time",
    "ix_audit_operation", "ux_audit_event_uuid", "ux_outbox_logical_key", "ix_outbox_available",
    "ix_outbox_claim_expiry", "ix_outbox_space", "ux_job_effect_key", "ix_job_available",
    "ix_job_claim_expiry", "ix_job_actor_time", "ix_job_space_state", "ix_job_handler_state", "ux_job_dedup",
    "ix_projection_status", "ix_backup_state_time", "ix_backup_blob_hash", "ix_blob_gc_available",
    "ix_blob_gc_claim_expiry", "ix_migration_run_status", "ux_migration_source_identity",
    "ux_search_document_item", "ix_search_document_space",
)


def _schema() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    for statement in TABLE_STATEMENTS + INDEX_STATEMENTS:
        connection.execute(statement)
    return connection


@pytest.mark.unit
def test_schema_v1_table_and_column_manifest_is_exact() -> None:
    assert len(TABLE_DEFINITIONS) == len(TABLE_NAMES) == 46
    assert len(set(TABLE_NAMES)) == 46
    assert tuple(EXPECTED_COLUMNS) == TABLE_NAMES

    connection = _schema()
    try:
        observed = {
            table: tuple(row[1] for row in connection.execute(f"PRAGMA table_xinfo({table})"))
            for table in TABLE_NAMES
        }
        table_list = {
            row[1]: (row[4], row[5])
            for row in connection.execute("PRAGMA table_list")
            if row[1] in TABLE_NAMES
        }
        assert observed == EXPECTED_COLUMNS
        assert table_list == {name: (0, 1) for name in TABLE_NAMES}
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


@pytest.mark.unit
def test_schema_v1_explicit_index_manifest_is_exact() -> None:
    assert INDEX_NAMES == EXPECTED_INDEX_NAMES
    assert len(INDEX_DEFINITIONS) == len(INDEX_STATEMENTS) == 120
    assert len(set(INDEX_NAMES)) == 120
    assert "ix_finding_apply_job" in INDEX_NAMES

    connection = _schema()
    try:
        observed = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema "
                "WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
            )
        )
        assert observed == INDEX_NAMES
    finally:
        connection.close()


@pytest.mark.unit
def test_schema_v1_uuid_domain_rejects_extra_hyphen() -> None:
    uuid_checks = re.compile(
        r"length\(replace\(([a-z_]+),'-',''\)\)=32\s+"
        r"AND replace\(\1,'-',''\) NOT GLOB '\*\[\^0-9a-f\]\*'"
    )
    assert sum(len(uuid_checks.findall(sql)) for sql in TABLE_STATEMENTS) == 169

    connection = _schema()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO org_unit(id,kind,name,name_key,status) VALUES (?,?,?,?,?)",
                (
                    "00000000-0000-0000-0000-00000000000-",
                    "responsibility",
                    "invalid",
                    "invalid",
                    "active",
                ),
            )
    finally:
        connection.close()


@pytest.mark.unit
def test_schema_v1_finding_job_column_fk_and_index_are_frozen() -> None:
    connection = _schema()
    try:
        columns = tuple(
            row[1] for row in connection.execute("PRAGMA table_xinfo(curation_finding)")
        )
        foreign_keys = tuple(connection.execute("PRAGMA foreign_key_list(curation_finding)"))
        apply_job_index_sql = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='index' AND name='ix_finding_apply_job'"
        ).fetchone()
        assert "applied_job_id" in columns
        assert any(row[2] == "durable_job" and row[3] == "applied_job_id" for row in foreign_keys)
        assert apply_job_index_sql == (
            "CREATE INDEX ix_finding_apply_job ON curation_finding(applied_job_id) "
            "WHERE applied_job_id IS NOT NULL",
        )
    finally:
        connection.close()


@pytest.mark.unit
def test_schema_v1_seed_manifest_and_statement_are_exact() -> None:
    manifest_path = (
        Path(__file__).parents[3]
        / "knowledge_core"
        / "migrations"
        / "seed_manifest_v1.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest == {
        "format": "code_assist.knowledge_core.seed_manifest.v1",
        "schema_version": 1,
        "seeds": [
            {
                "seed_key": "org.responsibility_root",
                "table": "org_unit",
                "values": {
                    "created_by": None,
                    "id": "fb90d31e-9576-5823-84ef-241b7552f214",
                    "kind": "responsibility",
                    "name": "AMHS담당",
                    "name_key": "AMHS담당",
                    "parent_id": None,
                    "row_version": 1,
                    "status": "active",
                    "updated_by": None,
                },
            }
        ],
    }
    assert SEED_NAMES == ("org.responsibility_root",)
    assert RESPONSIBILITY_ROOT_ID == manifest["seeds"][0]["values"]["id"]
    assert RESPONSIBILITY_ROOT_NAME == RESPONSIBILITY_ROOT_NAME_KEY == "AMHS담당"

    connection = _schema()
    try:
        for statement in SEED_STATEMENTS:
            connection.execute(statement)
        root = connection.execute(
            "SELECT id,kind,parent_id,name,name_key,status,created_by,updated_by,row_version "
            "FROM org_unit"
        ).fetchone()
        assert root == (
            "fb90d31e-9576-5823-84ef-241b7552f214",
            "responsibility",
            None,
            "AMHS담당",
            "AMHS담당",
            "active",
            None,
            None,
            1,
        )
    finally:
        connection.close()
