from __future__ import annotations

import hashlib
import json
import re
import sqlite3

import pytest

from code_assist_v1.knowledge_core.migrations.v001_indexes import INDEX_STATEMENTS
from code_assist_v1.knowledge_core.migrations.v001_tables import (
    TABLE_DEFINITIONS,
    TABLE_NAMES,
    TABLE_STATEMENTS,
)
from code_assist_v1.knowledge_core.migrations.v001_fts import (
    FTS_STATEMENTS,
    FTS_TABLE_SQL,
    FTS_TRIGGER_NAMES,
    FTS_TRIGGER_STATEMENTS,
)
from code_assist_v1.knowledge_core.migrations.v001_triggers import (
    TRIGGER_DEFINITIONS,
    TRIGGER_NAMES,
    TRIGGER_STATEMENTS,
)


pytestmark = [pytest.mark.unit, pytest.mark.scenario("SCHEMA-006")]

_TRIGGER_NAME_RE = re.compile(r"trg_[a-z][a-z0-9_]*_[a-z][a-z0-9_]*_(?:insert|update|delete)\Z")


def _sql_for(name: str) -> str:
    return dict(TRIGGER_DEFINITIONS)[name]


def _v1_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA recursive_triggers=ON")
    for statement in TABLE_STATEMENTS:
        connection.execute(statement)
    for statement in INDEX_STATEMENTS:
        connection.execute(statement)
    for statement in TRIGGER_STATEMENTS:
        connection.execute(statement)
    for statement in FTS_STATEMENTS:
        connection.execute(statement)
    return connection


def _seed_personal_subject(
    connection: sqlite3.Connection,
    *,
    item_suffix: int = 10,
    revision_suffix: int = 11,
) -> tuple[str, str, str, str]:
    user_id = "user01"
    space_id = "00000000-0000-0000-0000-000000000009"
    item_id = f"00000000-0000-0000-0000-{item_suffix:012d}"
    revision_id = f"00000000-0000-0000-0000-{revision_suffix:012d}"
    connection.execute(
        """INSERT OR IGNORE INTO app_user(
          id,display_name,password_hash,system_role,account_status,current_part_id,
          scope_epoch,created_at,updated_at,disabled_at,row_version
        ) VALUES (?,'User','hash','user','active',NULL,1,1,1,NULL,1)""",
        (user_id,),
    )
    connection.execute(
        """INSERT OR IGNORE INTO knowledge_space(
          id,space_kind,owner_user_id,owner_org_unit_id,status,write_enabled,
          generation,created_at,updated_at,row_version
        ) VALUES (?,'personal',?,NULL,'active',1,0,1,1,1)""",
        (space_id, user_id),
    )
    connection.execute(
        """INSERT INTO knowledge_item(
          id,space_id,lineage_group_id,lifecycle_status,curation_status,
          current_revision_id,last_curation_transition_id,
          latest_deactivation_episode_id,redirect_item_id,created_by,updated_by,
          created_at,updated_at,row_version
        ) VALUES (?,?,NULL,'active','provisional',?,NULL,NULL,NULL,?,?,1,1,1)""",
        (item_id, space_id, revision_id, user_id, user_id),
    )
    connection.execute(
        """INSERT INTO knowledge_revision(
          id,item_id,revision_no,parent_revision_id,base_revision_id,title,
          raw_content_sha256,normalized_content_sha256,content_size_bytes,
          normalizer_version,metadata_json,change_kind,change_reason,created_by,created_at
        ) VALUES (?,?,1,NULL,NULL,'Title',?,?,0,'v1','{}','initial','initial',?,1)""",
        (revision_id, item_id, "a" * 64, "a" * 64, user_id),
    )
    connection.commit()
    return user_id, space_id, item_id, revision_id


def test_schema_006_frozen_trigger_manifest_is_unique_named_and_complete() -> None:
    assert len(TRIGGER_NAMES) == 131
    assert len(FTS_TRIGGER_NAMES) == 3
    assert len(set((*TRIGGER_NAMES, *FTS_TRIGGER_NAMES))) == 134
    assert all(_TRIGGER_NAME_RE.fullmatch(name) for name in (*TRIGGER_NAMES, *FTS_TRIGGER_NAMES))
    assert TRIGGER_NAMES == tuple(name for name, _sql in TRIGGER_DEFINITIONS)
    assert TRIGGER_STATEMENTS == tuple(sql for _name, sql in TRIGGER_DEFINITIONS)

    # Compact frozen-order proof: a reordering or rename changes this review hash.
    manifest_hash = hashlib.sha256(
        "\n".join((*TRIGGER_NAMES, *FTS_TRIGGER_NAMES)).encode("ascii")
    ).hexdigest()
    assert manifest_hash == "02dd8f1b81c81e987da5847b35d709e226f26d2bdd538c8dff729bda1888221d"


def test_schema_006_manifest_covers_every_required_trigger_family() -> None:
    required_fragments = {
        "_row_version_update",
        "trg_org_unit_parent_insert",
        "trg_org_unit_parent_update",
        "trg_org_unit_inactivate_guard_update",
        "trg_app_user_part_update",
        "trg_org_membership_history_update",
        "trg_org_role_assignment_leader_write_guard_update",
        "trg_knowledge_space_owner_update",
        "trg_conversation_scope_update",
        "trg_conversation_turn_validity_update",
        "trg_conversation_context_binding_validity_update",
        "trg_knowledge_revision_history_update",
        "trg_knowledge_revision_body_purge_delete",
        "trg_source_artifact_validity_insert",
        "trg_source_artifact_retention_update",
        "trg_extraction_snapshot_validity_insert",
        "trg_publication_preview_validity_update",
        "trg_knowledge_item_lifecycle_update",
        "trg_knowledge_item_curation_update",
        "trg_curation_finding_policy_update",
        "trg_change_proposal_state_update",
        "trg_knowledge_item_lease_seed_insert",
        "trg_outbox_event_state_update",
        "trg_durable_job_state_update",
        "trg_backup_run_state_update",
        "trg_blob_gc_candidate_state_update",
    }
    names = set(TRIGGER_NAMES)
    assert all(
        fragment in names or any(fragment in name for name in names)
        for fragment in required_fragments
    )

    sql = "\n".join(TRIGGER_STATEMENTS)
    for stable_error in (
        "ROW_VERSION_CONFLICT",
        "ORG_PARENT_INVALID",
        "ORG_CYCLE",
        "ORG_UNIT_IN_USE",
        "USER_PART_INVALID",
        "SCOPE_EPOCH_INVALID",
        "MEMBERSHIP_INVALID",
        "LEADER_WRITE_GUARD",
        "SPACE_OWNER_INVALID",
        "SPACE_KIND_DISABLED",
        "CONVERSATION_SCOPE_IMMUTABLE",
        "CONVERSATION_TURN_INVALID",
        "CONVERSATION_CONTEXT_INVALID",
        "HISTORY_IMMUTABLE",
        "ARTIFACT_BODY_RETENTION_INVALID",
        "PUBLICATION_PREVIEW_INVALID",
        "LINEAGE_MEMBER_INVALID",
        "ITEM_STATE_INVALID",
        "ITEM_CURATION_INVALID",
        "REVISION_BODY_IMMUTABLE",
        "CURATION_POLICY_INVALID",
        "CHANGE_PROPOSAL_INVALID",
        "OUTBOX_STATE_INVALID",
        "JOB_STATE_INVALID",
        "MAINTENANCE_STATE_INVALID",
    ):
        assert f"RAISE(ABORT, '{stable_error}')" in sql


def test_schema_006_every_row_version_table_has_the_exact_increment_guard() -> None:
    versioned_tables = tuple(
        name
        for name, statement in TABLE_DEFINITIONS
        if re.search(r"\n\s*row_version INTEGER", statement)
    )
    assert versioned_tables == (
        "org_unit",
        "app_user",
        "knowledge_space",
        "conversation",
        "conversation_turn",
        "conversation_context_binding",
        "conversation_summary",
        "publication_preview",
        "knowledge_item",
        "item_relation",
        "knowledge_edge",
        "curation_finding",
        "change_proposal",
        "projection_state",
        "backup_run",
    )
    for table in versioned_tables:
        sql = _sql_for(f"trg_{table}_row_version_update")
        assert "NEW.row_version <> OLD.row_version + 1" in sql
        assert "RAISE(ABORT, 'ROW_VERSION_CONFLICT')" in sql


def test_schema_006_trigger_sql_has_no_application_defined_function_dependency() -> None:
    sql = "\n".join(TRIGGER_STATEMENTS)
    assert "CREATE FUNCTION" not in sql.upper()
    assert "load_extension" not in sql.lower()
    assert "pragma_" not in sql.lower()
    assert "unixepoch('subsec')" in sql
    assert "json_each(" in sql
    assert "json_array_length(" in sql


def test_schema_006_all_trigger_ddl_compiles_against_all_v1_tables() -> None:
    assert len(TABLE_NAMES) == 46
    connection = _v1_connection()
    try:
        observed = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='trigger' ORDER BY rowid"
            )
        )
        assert observed == (*TRIGGER_NAMES, *FTS_TRIGGER_NAMES)
    finally:
        connection.close()


def test_schema_006_row_version_audit_and_outbox_guards_execute() -> None:
    connection = _v1_connection()
    root_id = "00000000-0000-0000-0000-000000000001"
    event_id = "00000000-0000-0000-0000-000000000002"
    request_id = "00000000-0000-0000-0000-000000000003"
    try:
        connection.execute(
            """INSERT INTO org_unit(
              id,kind,parent_id,name,name_key,status,created_at,updated_at,
              created_by,updated_by,row_version
            ) VALUES (?,'responsibility',NULL,'Root','root','active',1,1,NULL,NULL,1)""",
            (root_id,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="ROW_VERSION_CONFLICT"):
            connection.execute(
                "UPDATE org_unit SET name='Changed',updated_at=2 WHERE id=?",
                (root_id,),
            )
        connection.execute(
            "UPDATE org_unit SET name='Changed',updated_at=2,row_version=2 WHERE id=?",
            (root_id,),
        )

        connection.execute(
            """INSERT INTO audit_event(
              event_uuid,occurred_at,request_id,operation_id,action,outcome,
              actor_kind,actor_user_id,actor_display_name,actor_role_snapshot,
              details_json,request_meta_json,schema_version
            ) VALUES (?,1,?,NULL,'probe','ok','system',NULL,'system','system','{}','{}',1)""",
            (event_id, request_id),
        )
        with pytest.raises(sqlite3.IntegrityError, match="HISTORY_IMMUTABLE"):
            connection.execute("UPDATE audit_event SET outcome='changed' WHERE id=1")
        with pytest.raises(sqlite3.IntegrityError, match="HISTORY_IMMUTABLE"):
            connection.execute("DELETE FROM audit_event WHERE id=1")

        connection.execute(
            """INSERT INTO outbox_event(
              logical_key,space_id,aggregate_type,aggregate_id,aggregate_version,
              event_type,payload_json,payload_schema_version,state,available_at,
              attempt_count,created_at
            ) VALUES ('one',NULL,'item','one',1,'changed','{}',1,'pending',1,0,1)"""
        )
        with pytest.raises(sqlite3.IntegrityError, match="OUTBOX_STATE_INVALID"):
            connection.execute(
                "UPDATE outbox_event SET state='applied',completed_at=2 WHERE id=1"
            )
        connection.execute(
            """UPDATE outbox_event
               SET state='processing',claim_owner='worker',claim_expires_at=10,
                   attempt_count=1 WHERE id=1"""
        )
        connection.execute(
            "UPDATE outbox_event SET claim_expires_at=20 WHERE id=1"
        )
        connection.execute(
            """UPDATE outbox_event
               SET state='applied',claim_owner=NULL,claim_expires_at=NULL,
                   completed_at=3 WHERE id=1"""
        )
    finally:
        connection.close()


def test_schema_006_finding_cannot_be_born_reviewed_or_t2_applied() -> None:
    connection = _v1_connection()
    user_id, space_id, source_item, source_revision = _seed_personal_subject(connection)
    _user, _space, target_item, target_revision = _seed_personal_subject(
        connection, item_suffix=12, revision_suffix=13
    )
    dedup_id = "00000000-0000-0000-0000-000000000014"
    operation_id = "00000000-0000-0000-0000-000000000015"
    try:
        connection.execute(
            """INSERT INTO operation_dedup(
              id,actor_user_id,operation_type,operation_id,request_sha256,status,
              http_status,result_json,error_json,created_at,completed_at,last_seen_at
            ) VALUES (?,?,'semantic_edge',?,?,'in_progress',NULL,NULL,NULL,1,NULL,1)""",
            (dedup_id, user_id, operation_id, "b" * 64),
        )
        with pytest.raises(sqlite3.IntegrityError, match="CURATION_POLICY_INVALID"):
            connection.execute(
                """INSERT INTO curation_finding(
                  id,space_id,finding_kind,risk_tier,severity,status,decision_mode,
                  required_reviewer,visibility_effect,proposed_action,
                  source_item_id,source_revision_id,target_item_id,target_revision_id,
                  detector_name,detector_version,rule_version,model_id,prompt_version,
                  confidence,evidence_json,proposed_action_json,reviewed_by,reviewed_at,
                  review_reason,applied_operation_id,applied_dedup_id,applied_job_id,
                  applied_by,applied_at,last_application_error_json,created_actor_kind,
                  created_by,created_at,updated_at,row_version
                ) VALUES (?,?, 'semantic_edge','T2','info','applied','human',
                  'space_editor','none','relation',?,?,?,?,
                  'detector','v1','r1',NULL,NULL,NULL,'{}','{}',NULL,NULL,NULL,
                  ?,?,NULL,?,2,NULL,'user',?,1,1,1)""",
                (
                    "00000000-0000-0000-0000-000000000016",
                    space_id,
                    source_item,
                    source_revision,
                    target_item,
                    target_revision,
                    operation_id,
                    dedup_id,
                    user_id,
                    user_id,
                ),
            )

        pending_id = "00000000-0000-0000-0000-000000000017"
        connection.execute(
            """INSERT INTO curation_finding(
              id,space_id,finding_kind,risk_tier,severity,status,decision_mode,
              required_reviewer,visibility_effect,proposed_action,
              source_item_id,source_revision_id,target_item_id,target_revision_id,
              detector_name,detector_version,rule_version,model_id,prompt_version,
              confidence,evidence_json,proposed_action_json,reviewed_by,reviewed_at,
              review_reason,applied_operation_id,applied_dedup_id,applied_job_id,
              applied_by,applied_at,last_application_error_json,created_actor_kind,
              created_by,created_at,updated_at,row_version
            ) VALUES (?,?, 'semantic_edge','T2','info','pending','human',
              'space_editor','none','relation',?,?,?,?,
              'detector','v1','r1',NULL,NULL,NULL,'{}','{}',NULL,NULL,NULL,
              NULL,NULL,NULL,NULL,NULL,NULL,'user',?,1,1,1)""",
            (
                pending_id,
                space_id,
                source_item,
                source_revision,
                target_item,
                target_revision,
                user_id,
            ),
        )
        with pytest.raises(sqlite3.IntegrityError, match="CURATION_POLICY_INVALID"):
            connection.execute(
                """INSERT INTO knowledge_edge(
                  id,space_id,source_item_id,target_item_id,edge_type,edge_status,
                  origin,source_evidence_revision_id,target_evidence_revision_id,
                  approval_finding_id,evidence_json,needs_revalidation,created_by,
                  updated_by,created_at,updated_at,row_version
                ) VALUES (?,?,?,?,'related','active','semantic_approved',?,?,?,
                  '{}',0,?,?,1,1,1)""",
                (
                    "00000000-0000-0000-0000-000000000018",
                    space_id,
                    source_item,
                    target_item,
                    source_revision,
                    target_revision,
                    pending_id,
                    user_id,
                    user_id,
                ),
            )
    finally:
        connection.close()


def test_schema_006_conversation_turn_and_summary_cannot_be_born_terminal() -> None:
    connection = _v1_connection()
    user_id, space_id, _item_id, _revision_id = _seed_personal_subject(connection)
    conversation_id = "00000000-0000-0000-0000-000000000100"
    user_message_id = "00000000-0000-0000-0000-000000000101"
    assistant_message_id = "00000000-0000-0000-0000-000000000102"
    dedup_id = "00000000-0000-0000-0000-000000000103"
    operation_id = "00000000-0000-0000-0000-000000000104"
    turn_id = "00000000-0000-0000-0000-000000000105"
    try:
        connection.execute(
            """INSERT INTO conversation(
              id,owner_user_id,space_id,captured_scope_epoch,title,status,
              metadata_json,created_at,updated_at,row_version
            ) VALUES (?,?,?,1,'','active','{}',1,1,1)""",
            (conversation_id, user_id, space_id),
        )
        connection.execute(
            """INSERT INTO conversation_message(
              id,conversation_id,sequence_no,role,content_json,created_at
            ) VALUES (?,?,1,'user','{}',1)""",
            (user_message_id, conversation_id),
        )
        connection.execute(
            """INSERT INTO conversation_message(
              id,conversation_id,sequence_no,role,content_json,created_at
            ) VALUES (?,?,2,'assistant','{}',2)""",
            (assistant_message_id, conversation_id),
        )
        connection.execute(
            """INSERT INTO operation_dedup(
              id,actor_user_id,operation_type,operation_id,request_sha256,status,
              http_status,result_json,error_json,created_at,completed_at,last_seen_at
            ) VALUES (?,?,'conversation_turn',?,?,'in_progress',NULL,NULL,NULL,1,NULL,1)""",
            (dedup_id, user_id, operation_id, "a" * 64),
        )

        with pytest.raises(sqlite3.IntegrityError, match="CONVERSATION_TURN_INVALID"):
            connection.execute(
                """INSERT INTO conversation_turn(
                  id,conversation_id,actor_user_id,dedup_id,operation_id,
                  request_sha256,state,user_message_id,assistant_message_id,
                  final_response_json,error_json,created_at,updated_at,completed_at,
                  row_version
                ) VALUES (?,?,?,?,?,?,'completed',?,?,'{}',NULL,1,1,1,1)""",
                (
                    turn_id,
                    conversation_id,
                    user_id,
                    dedup_id,
                    operation_id,
                    "a" * 64,
                    user_message_id,
                    assistant_message_id,
                ),
            )

        connection.execute(
            """INSERT INTO conversation_turn(
              id,conversation_id,actor_user_id,dedup_id,operation_id,
              request_sha256,state,user_message_id,assistant_message_id,
              final_response_json,error_json,created_at,updated_at,completed_at,
              row_version
            ) VALUES (?,?,?,?,?,?,'accepted',?,NULL,NULL,NULL,1,1,NULL,1)""",
            (
                turn_id,
                conversation_id,
                user_id,
                dedup_id,
                operation_id,
                "a" * 64,
                user_message_id,
            ),
        )
        connection.execute(
            "UPDATE conversation_turn SET state='running',updated_at=2,row_version=2 WHERE id=?",
            (turn_id,),
        )
        connection.execute(
            """UPDATE conversation_turn
               SET state='completed',assistant_message_id=?,final_response_json='{}',
                   completed_at=3,updated_at=3,row_version=3 WHERE id=?""",
            (assistant_message_id, turn_id),
        )
        with pytest.raises(sqlite3.IntegrityError, match="CONVERSATION_TURN_INVALID"):
            connection.execute(
                """UPDATE conversation_turn SET final_response_json='{"changed":true}',
                   updated_at=4,row_version=4 WHERE id=?""",
                (turn_id,),
            )

        with pytest.raises(sqlite3.IntegrityError, match="CONVERSATION_CONTEXT_INVALID"):
            connection.execute(
                """INSERT INTO conversation_summary(
                  id,conversation_id,covered_through_sequence_no,summary_md,
                  summary_sha256,model_id,prompt_version,summarizer_version,status,
                  created_by_user_id,created_at,invalidated_at,invalidation_reason,
                  row_version
                ) VALUES (?,?,1,'summary',?,'model','p1','s1','invalidated',?,1,2,
                  'born terminal',1)""",
                (
                    "00000000-0000-0000-0000-000000000106",
                    conversation_id,
                    "b" * 64,
                    user_id,
                ),
            )
    finally:
        connection.close()


def test_schema_006_detached_context_binding_is_terminal() -> None:
    connection = _v1_connection()
    user_id, space_id, _item_id, _revision_id = _seed_personal_subject(connection)
    conversation_id = "00000000-0000-0000-0000-000000000110"
    binding_id = "00000000-0000-0000-0000-000000000111"
    try:
        connection.execute(
            """INSERT INTO conversation(
              id,owner_user_id,space_id,captured_scope_epoch,title,status,
              metadata_json,created_at,updated_at,row_version
            ) VALUES (?,?,?,1,'','active','{}',1,1,1)""",
            (conversation_id, user_id, space_id),
        )
        connection.execute(
            """INSERT INTO conversation_context_binding(
              id,conversation_id,binding_type,binding_mode,external_key,
              source_artifact_id,snapshot_sha256,display_metadata_json,status,
              status_reason,created_by,created_at,status_changed_at,detached_at,
              row_version
            ) VALUES (?,?,'workspace_file','live','file',NULL,NULL,'{}','active',
              NULL,?,1,1,NULL,1)""",
            (binding_id, conversation_id, user_id),
        )
        connection.execute(
            """UPDATE conversation_context_binding
               SET status='unavailable',status_reason='offline',status_changed_at=2,
                   row_version=2 WHERE id=?""",
            (binding_id,),
        )
        connection.execute(
            """UPDATE conversation_context_binding
               SET status_reason='still offline',status_changed_at=3,row_version=3
               WHERE id=?""",
            (binding_id,),
        )
        connection.execute(
            """UPDATE conversation_context_binding
               SET status='detached',detached_at=4,status_changed_at=4,row_version=4
               WHERE id=?""",
            (binding_id,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="CONVERSATION_CONTEXT_INVALID"):
            connection.execute(
                """UPDATE conversation_context_binding
                   SET status_reason='mutated terminal',status_changed_at=5,
                       detached_at=5,row_version=5 WHERE id=?""",
                (binding_id,),
            )
    finally:
        connection.close()


def test_schema_006_deactivation_episode_is_inserted_open() -> None:
    connection = _v1_connection()
    user_id, _space_id, item_id, _revision_id = _seed_personal_subject(connection)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="ITEM_STATE_INVALID"):
            connection.execute(
                """INSERT INTO item_deactivation_episode(
                  id,item_id,episode_no,deactivated_by,deactivated_at,reason,
                  purge_eligible_at,restored_by,restored_at,purged_by,purged_at
                ) VALUES (?,?,1,?,1,'reason',7776000001,?,2,NULL,NULL)""",
                (
                    "00000000-0000-0000-0000-000000000120",
                    item_id,
                    user_id,
                    user_id,
                ),
            )
    finally:
        connection.close()


def test_schema_006_artifact_and_ready_extraction_are_born_present() -> None:
    connection = _v1_connection()
    user_id, _space_id, _item_id, _revision_id = _seed_personal_subject(connection)
    artifact_id = "00000000-0000-0000-0000-000000000121"
    snapshot_id = "00000000-0000-0000-0000-000000000122"
    try:
        with pytest.raises(sqlite3.IntegrityError, match="ARTIFACT_BODY_RETENTION_INVALID"):
            connection.execute(
                """INSERT INTO source_artifact(
                  id,origin_type,origin_key,origin_revision_key,raw_sha256,raw_size_bytes,
                  media_type,original_filename,body_retention_status,
                  body_retention_reason,body_purged_at,orphan_cleanup_eligible_at,
                  publisher_user_id,source_owner_snapshot_json,metadata_json,
                  metadata_schema_version,created_at
                ) VALUES (?,'manual',NULL,NULL,?,0,'text/markdown',NULL,'purged',
                  'orphan_ingestion_cleanup',2,2592000001,?,'{}','{}',1,1)""",
                (artifact_id, "c" * 64, user_id),
            )

        connection.execute(
            """INSERT INTO source_artifact(
              id,origin_type,origin_key,origin_revision_key,raw_sha256,raw_size_bytes,
              media_type,original_filename,body_retention_status,
              body_retention_reason,body_purged_at,orphan_cleanup_eligible_at,
              publisher_user_id,source_owner_snapshot_json,metadata_json,
              metadata_schema_version,created_at
            ) VALUES (?,'manual',NULL,NULL,?,0,'text/markdown',NULL,'present',
              NULL,NULL,2592000001,?,'{}','{}',1,1)""",
            (artifact_id, "c" * 64, user_id),
        )
        with pytest.raises(sqlite3.IntegrityError, match="ARTIFACT_BODY_RETENTION_INVALID"):
            connection.execute(
                """INSERT INTO extraction_snapshot(
                  id,artifact_id,status,extracted_sha256,normalized_content_sha256,
                  extracted_size_bytes,normalizer_version,extractor_name,
                  extractor_version,warnings_json,body_retention_status,created_at
                ) VALUES (?,?,'ready',?,?,0,'n1','extractor','v1','[]','purged',1)""",
                (snapshot_id, artifact_id, "d" * 64, "e" * 64),
            )
    finally:
        connection.close()


def test_schema_006_artifact_aggregate_purges_only_after_child_manifests() -> None:
    connection = _v1_connection()
    user_id, _space_id, _item_id, _revision_id = _seed_personal_subject(connection)
    artifact_id = "00000000-0000-0000-0000-000000000123"
    blob_id = "00000000-0000-0000-0000-000000000124"
    try:
        connection.execute(
            """INSERT INTO source_artifact(
              id,origin_type,origin_key,origin_revision_key,raw_sha256,raw_size_bytes,
              media_type,original_filename,body_retention_status,
              body_retention_reason,body_purged_at,orphan_cleanup_eligible_at,
              publisher_user_id,source_owner_snapshot_json,metadata_json,
              metadata_schema_version,created_at
            ) VALUES (?,'manual',NULL,NULL,?,0,'text/markdown',NULL,'present',
              NULL,NULL,2592000001,?,'{}','{}',1,1)""",
            (artifact_id, "c" * 64, user_id),
        )
        connection.execute(
            """INSERT INTO artifact_blob(
              id,artifact_id,content_sha256,size_bytes,role,logical_name,
              media_type,ordinal,body_retention_status,created_at
            ) VALUES (?,?,?,0,'raw','raw.md','text/markdown',0,'present',1)""",
            (blob_id, artifact_id, "c" * 64),
        )
        with pytest.raises(sqlite3.IntegrityError, match="ARTIFACT_BODY_RETENTION_INVALID"):
            connection.execute(
                """UPDATE source_artifact SET body_retention_status='purged',
                   body_retention_reason='orphan_ingestion_cleanup',body_purged_at=2
                   WHERE id=?""",
                (artifact_id,),
            )
        connection.execute(
            "UPDATE artifact_blob SET body_retention_status='purged' WHERE id=?",
            (blob_id,),
        )
        connection.execute(
            """UPDATE source_artifact SET body_retention_status='purged',
               body_retention_reason='orphan_ingestion_cleanup',body_purged_at=2
               WHERE id=?""",
            (artifact_id,),
        )
        assert connection.execute(
            "SELECT body_retention_status FROM source_artifact WHERE id=?",
            (artifact_id,),
        ).fetchone() == ("purged",)
    finally:
        connection.close()


def test_schema_006_explicit_edge_state_can_be_reactivated() -> None:
    connection = _v1_connection()
    user_id, space_id, source_item, _source_revision = _seed_personal_subject(connection)
    _user, _space, target_item, _target_revision = _seed_personal_subject(
        connection, item_suffix=130, revision_suffix=131
    )
    edge_id = "00000000-0000-0000-0000-000000000132"
    try:
        connection.execute(
            """INSERT INTO knowledge_edge(
              id,space_id,source_item_id,target_item_id,edge_type,edge_status,
              origin,source_evidence_revision_id,target_evidence_revision_id,
              approval_finding_id,evidence_json,needs_revalidation,created_by,
              updated_by,created_at,updated_at,row_version
            ) VALUES (?,?,?,?,'related','inactive','manual',NULL,NULL,NULL,'{}',0,
              ?,?,1,1,1)""",
            (edge_id, space_id, source_item, target_item, user_id, user_id),
        )
        connection.execute(
            """UPDATE knowledge_edge SET edge_status='active',updated_at=2,
               updated_by=?,row_version=2 WHERE id=?""",
            (user_id, edge_id),
        )
        assert connection.execute(
            "SELECT edge_status,row_version FROM knowledge_edge WHERE id=?", (edge_id,)
        ).fetchone() == ("active", 2)
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("lifecycle", "transition_id"),
    (
        ("inactive", None),
        ("active", "00000000-0000-0000-0000-000000000099"),
    ),
)
def test_schema_006_item_insert_rejects_noninitial_shape(
    lifecycle: str,
    transition_id: str | None,
) -> None:
    connection = _v1_connection()
    user_id, space_id, _item, _revision = _seed_personal_subject(connection)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="ITEM_STATE_INVALID"):
            connection.execute(
                """INSERT INTO knowledge_item(
                  id,space_id,lineage_group_id,lifecycle_status,curation_status,
                  current_revision_id,last_curation_transition_id,
                  latest_deactivation_episode_id,redirect_item_id,created_by,updated_by,
                  created_at,updated_at,row_version
                ) VALUES (?,?,NULL,?,'provisional',?, ?,NULL,NULL,?,?,2,2,1)""",
                (
                    "00000000-0000-0000-0000-000000000020",
                    space_id,
                    lifecycle,
                    "00000000-0000-0000-0000-000000000021",
                    transition_id,
                    user_id,
                    user_id,
                ),
            )
    finally:
        connection.close()


@pytest.mark.parametrize(
    "status",
    ("extracting", "consumed", "expired", "failed", "cancelled"),
)
def test_schema_006_preview_cannot_be_inserted_in_noninitial_state(status: str) -> None:
    connection = _v1_connection()
    user_id, _space_id, _item, _revision = _seed_personal_subject(connection)
    artifact_id = "00000000-0000-0000-0000-000000000030"
    snapshot_id = "00000000-0000-0000-0000-000000000031"
    job_id = "00000000-0000-0000-0000-000000000032"
    try:
        connection.execute(
            """INSERT INTO source_artifact(
              id,origin_type,origin_key,origin_revision_key,raw_sha256,raw_size_bytes,
              media_type,original_filename,body_retention_status,
              body_retention_reason,body_purged_at,orphan_cleanup_eligible_at,
              publisher_user_id,source_owner_snapshot_json,metadata_json,
              metadata_schema_version,created_at
            ) VALUES (?,'manual',NULL,NULL,?,0,'text/markdown',NULL,'present',
              NULL,NULL,2592000001,?,'{}','{}',1,1)""",
            (artifact_id, "c" * 64, user_id),
        )
        ready_shaped = status in ("consumed", "expired", "cancelled")
        failed = status == "failed"
        with pytest.raises(sqlite3.IntegrityError, match="PUBLICATION_PREVIEW_INVALID"):
            connection.execute(
                """INSERT INTO publication_preview(
                  id,actor_user_id,artifact_id,extraction_snapshot_id,durable_job_id,
                  source_snapshot_sha256,captured_part_id,captured_scope_epoch,
                  available_targets_json,rule_version,deterministic_findings_json,
                  ready_result_json,result_schema_version,status,error_json,expires_at,
                  created_at,updated_at,completed_at,row_version
                ) VALUES (?,?,?,?,?,?,NULL,1,'["personal"]','r1',?,?,1,?,?,99,1,1,?,1)""",
                (
                    f"00000000-0000-0000-0000-{40 + len(status):012d}",
                    user_id,
                    artifact_id,
                    snapshot_id if ready_shaped else None,
                    None if ready_shaped else job_id,
                    "d" * 64 if ready_shaped else None,
                    "[]" if ready_shaped else None,
                    "{}" if ready_shaped else None,
                    status,
                    "{}" if failed else None,
                    2 if status not in ("extracting",) else None,
                ),
            )
    finally:
        connection.close()


def test_schema_006_job_dead_requires_exhaustion_and_claim_renewal_keeps_owner() -> None:
    connection = _v1_connection()
    job_id = "00000000-0000-0000-0000-000000000070"
    try:
        connection.execute(
            """INSERT INTO durable_job(
              id,dedup_id,operation_id,actor_kind,actor_user_id,space_id,job_type,
              handler_version,effect_key,input_ref_json,input_sha256,
              expected_state_json,authorization_snapshot_json,
              authorization_snapshot_sha256,authorization_scope_epoch,
              authorization_part_id,authorization_capabilities_json,state,
              claim_owner,claim_expires_at,attempt_count,max_attempts,cancellable,
              cancel_requested,progress_permille,result_json,error_json,available_at,
              created_at,updated_at,completed_at
            ) VALUES (?,NULL,NULL,'system',NULL,NULL,'probe','v1','probe:70','{}',?,
              '{}','{"schema_version":1,"actor_kind":"system"}',?,NULL,NULL,'[]',
              'queued',NULL,NULL,0,3,0,0,0,NULL,NULL,1,1,1,NULL)""",
            (job_id, "e" * 64, "f" * 64),
        )
        connection.execute(
            """UPDATE durable_job SET state='claimed',claim_owner='worker-a',
               claim_expires_at=10,attempt_count=1,updated_at=2 WHERE id=?""",
            (job_id,),
        )
        connection.execute(
            "UPDATE durable_job SET claim_expires_at=20,updated_at=3 WHERE id=?",
            (job_id,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="JOB_STATE_INVALID"):
            connection.execute(
                "UPDATE durable_job SET claim_owner='worker-b',updated_at=4 WHERE id=?",
                (job_id,),
            )
        connection.execute(
            "UPDATE durable_job SET state='running',updated_at=4 WHERE id=?",
            (job_id,),
        )
        connection.execute(
            """UPDATE durable_job SET state='retry',claim_owner=NULL,
               claim_expires_at=NULL,updated_at=5 WHERE id=?""",
            (job_id,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="JOB_STATE_INVALID"):
            connection.execute(
                """UPDATE durable_job SET state='dead',error_json='{}',completed_at=6,
                   updated_at=6 WHERE id=?""",
                (job_id,),
            )

        for attempt in (2, 3):
            connection.execute(
                """UPDATE durable_job SET state='claimed',claim_owner='worker-a',
                   claim_expires_at=?,attempt_count=?,updated_at=? WHERE id=?""",
                (20 + attempt, attempt, 6 + attempt * 3, job_id),
            )
            connection.execute(
                "UPDATE durable_job SET state='running',updated_at=? WHERE id=?",
                (7 + attempt * 3, job_id),
            )
            connection.execute(
                """UPDATE durable_job SET state='retry',claim_owner=NULL,
                   claim_expires_at=NULL,updated_at=? WHERE id=?""",
                (8 + attempt * 3, job_id),
            )
        connection.execute(
            """UPDATE durable_job SET state='dead',error_json='{}',completed_at=20,
               updated_at=20 WHERE id=?""",
            (job_id,),
        )
        assert connection.execute(
            "SELECT state,attempt_count,max_attempts FROM durable_job WHERE id=?",
            (job_id,),
        ).fetchone() == ("dead", 3, 3)
    finally:
        connection.close()


def test_schema_006_job_handler_input_and_authorization_snapshots_are_exact() -> None:
    connection = _v1_connection()
    user_id, space_id, item_id, revision_id = _seed_personal_subject(connection)

    statement = """INSERT INTO durable_job(
      id,dedup_id,operation_id,actor_kind,actor_user_id,space_id,job_type,
      handler_version,effect_key,input_ref_json,input_sha256,expected_state_json,
      authorization_snapshot_json,authorization_snapshot_sha256,
      authorization_scope_epoch,authorization_part_id,
      authorization_capabilities_json,state,claim_owner,claim_expires_at,
      attempt_count,max_attempts,cancellable,cancel_requested,progress_permille,
      result_json,error_json,available_at,created_at,updated_at,completed_at
    ) VALUES (
      :id,NULL,NULL,:actor_kind,:actor_user_id,:space_id,:job_type,
      :handler_version,:effect_key,:input_ref_json,:input_sha256,'{}',
      :authorization_snapshot_json,:authorization_snapshot_sha256,
      :authorization_scope_epoch,NULL,:authorization_capabilities_json,
      'queued',NULL,NULL,0,3,0,0,0,NULL,NULL,1,1,1,NULL
    )"""

    def insert_job(
        suffix: int,
        *,
        actor_kind: str = "system",
        job_type: str = "curation.rescan",
        handler_version: str = "v1",
        input_ref: dict[str, object] | None = None,
        snapshot: dict[str, object] | None = None,
        capabilities: list[str] | None = None,
    ) -> None:
        user_actor = actor_kind == "user"
        job_id = f"00000000-0000-0000-0000-{suffix:012d}"
        values = {
            "id": job_id,
            "actor_kind": actor_kind,
            "actor_user_id": user_id if user_actor else None,
            "space_id": space_id,
            "job_type": job_type,
            "handler_version": handler_version,
            "effect_key": f"job:{suffix}",
            "input_ref_json": json.dumps(
                input_ref
                if input_ref is not None
                else {
                    "item_id": item_id,
                    "revision_id": revision_id,
                    "rule_version": "r1",
                },
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            "input_sha256": "e" * 64,
            "authorization_snapshot_json": json.dumps(
                snapshot
                if snapshot is not None
                else (
                    {
                        "schema_version": 1,
                        "actor_kind": "user",
                        "actor_user_id": user_id,
                        "account_status": "active",
                        "system_role": "user",
                        "current_part_id": None,
                        "scope_epoch": 1,
                        "space_id": space_id,
                        "capabilities": capabilities or [],
                    }
                    if user_actor
                    else {"schema_version": 1, "actor_kind": "system"}
                ),
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            "authorization_snapshot_sha256": "f" * 64,
            "authorization_scope_epoch": 1 if user_actor else None,
            "authorization_capabilities_json": json.dumps(
                capabilities or [], ensure_ascii=True, separators=(",", ":")
            ),
        }
        connection.execute(statement, values)

    try:
        insert_job(80)
        insert_job(81, actor_kind="user", capabilities=["edit", "read"])

        invalid_cases = (
            {"handler_version": "v2"},
            {
                "input_ref": {
                    "item_id": item_id,
                    "revision_id": revision_id,
                    "rule_version": "r1",
                    "extra": True,
                }
            },
            {
                "input_ref": {
                    "item_id": "00000000-0000-0000-0000-0000000000-z",
                    "revision_id": revision_id,
                    "rule_version": "r1",
                }
            },
            {
                "snapshot": {
                    "schema_version": 1,
                    "actor_kind": "system",
                    "extra": True,
                }
            },
            {
                "actor_kind": "user",
                "capabilities": ["read", "edit"],
            },
            {
                "actor_kind": "user",
                "snapshot": {
                    "schema_version": 1,
                    "actor_kind": "user",
                    "actor_user_id": user_id,
                    "account_status": "active",
                    "system_role": "user",
                    "current_part_id": None,
                    "scope_epoch": 99,
                    "space_id": space_id,
                    "capabilities": [],
                },
            },
        )
        for offset, overrides in enumerate(invalid_cases, start=82):
            with pytest.raises(sqlite3.IntegrityError, match="JOB_STATE_INVALID"):
                insert_job(offset, **overrides)
    finally:
        connection.close()


def test_schema_003_purge_is_the_only_pointer_clear_exception_and_retains_transition() -> None:
    lifecycle = _sql_for("trg_knowledge_item_lifecycle_update")
    curation = _sql_for("trg_knowledge_item_curation_update")
    body_delete = _sql_for("trg_knowledge_revision_body_purge_delete")

    assert "OLD.lifecycle_status='inactive'" in lifecycle
    assert "NEW.lifecycle_status='purged_tombstone'" in lifecycle
    assert "NEW.current_revision_id IS NOT NULL" in lifecycle
    assert "NEW.last_curation_transition_id IS NOT OLD.last_curation_transition_id" in lifecycle
    assert "OLD.current_revision_id IS NOT NULL AND NEW.current_revision_id IS NULL" in curation
    assert "NEW.last_curation_transition_id IS OLD.last_curation_transition_id" in curation
    assert "a.account_status='active' AND a.system_role='admin'" in body_delete
    assert "e.purged_at>=e.purge_eligible_at" in body_delete


def test_schema_006_curation_application_has_exact_caller_or_system_proof() -> None:
    finding_insert = _sql_for("trg_curation_finding_policy_insert")
    finding_update = _sql_for("trg_curation_finding_policy_update")
    transition = _sql_for("trg_item_curation_transition_validity_insert")

    for sql in (finding_insert, finding_update):
        assert "NEW.applied_job_id IS NULL" in sql
        assert "NEW.applied_job_id IS NOT NULL" in sql
        assert "NEW.status='apply_failed'" in sql
        assert "NEW.last_application_error_json IS NOT NULL" in sql
    assert "f.applied_job_id=NEW.decision_job_id" in transition
    assert "NEW.decision_job_id IS NOT NULL" in transition


def test_schema_006_proposal_finding_subject_is_always_the_target_item() -> None:
    proposal = _sql_for("trg_change_proposal_validity_insert")
    assert "f.source_item_id=NEW.target_item_id" in proposal
    assert "f.source_revision_id=NEW.target_revision_id" in proposal
    assert "f.target_item_id IS NULL AND f.target_revision_id IS NULL" in proposal
    assert "NEW.proposal_kind='stable_source_update'" in proposal
    assert "NEW.proposal_kind='personal_to_part'" in proposal


def test_schema_005_fts_sql_is_exact_external_content_contract() -> None:
    assert FTS_TABLE_SQL == """CREATE VIRTUAL TABLE knowledge_fts USING fts5(
  title,
  content_md,
  content='search_document',
  content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);"""
    assert FTS_TRIGGER_NAMES == (
        "trg_search_document_fts_insert",
        "trg_search_document_fts_delete",
        "trg_search_document_fts_update",
    )
    assert FTS_STATEMENTS == (FTS_TABLE_SQL, *FTS_TRIGGER_STATEMENTS)


def test_schema_005_external_content_triggers_synchronize_insert_update_delete() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            """CREATE TABLE search_document(
              rowid INTEGER PRIMARY KEY AUTOINCREMENT,
              item_id TEXT NOT NULL UNIQUE,
              space_id TEXT NOT NULL,
              revision_id TEXT NOT NULL,
              title TEXT NOT NULL,
              content_md TEXT NOT NULL,
              updated_at INTEGER NOT NULL
            ) STRICT"""
        )
        for statement in FTS_STATEMENTS:
            connection.execute(statement)

        connection.execute(
            "INSERT INTO search_document(item_id,space_id,revision_id,title,content_md,updated_at) "
            "VALUES ('item','space','rev','alpha','first body',1)"
        )
        assert connection.execute(
            "SELECT rowid,title FROM knowledge_fts WHERE knowledge_fts MATCH 'alpha'"
        ).fetchall() == [(1, "alpha")]

        connection.execute(
            "UPDATE search_document SET title='beta',content_md='second body' WHERE rowid=1"
        )
        assert connection.execute(
            "SELECT count(*) FROM knowledge_fts WHERE knowledge_fts MATCH 'alpha'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT rowid,title FROM knowledge_fts WHERE knowledge_fts MATCH 'beta'"
        ).fetchall() == [(1, "beta")]

        connection.execute("DELETE FROM search_document WHERE rowid=1")
        assert connection.execute(
            "SELECT count(*) FROM knowledge_fts WHERE knowledge_fts MATCH 'beta'"
        ).fetchone() == (0,)
    finally:
        connection.close()
