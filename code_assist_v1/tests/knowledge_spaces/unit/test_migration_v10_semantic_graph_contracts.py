from __future__ import annotations

import sqlite3

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.errors import KnowledgeCoreMigrationError
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    Migration,
    MigrationCatalog,
    SingleMigrator,
    V009_EXPECTED_SCHEMA_SHA256,
    V010_EXPECTED_SCHEMA_SHA256,
    schema_sha256,
    verify_canonical_integrity,
)
from code_assist_v1.knowledge_core.migrations.v010_semantic_graph_contracts import (
    V010_POSTCONDITION_QUERIES,
    V010_STATEMENTS,
)
from code_assist_v1.test_safety import create_isolated_test_layout
from code_assist_v1.tests.knowledge_spaces.unit.test_migration_v9_higher_native_spaces import (
    _V8_CATALOG,
    _V9_CATALOG,
    _layout,
    _migrate as _migrate_v9,
    _seed_v8_hierarchy,
    _uuid,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_schema_v1_acceptance import (
    _apply_release,
    _insert_human_release_finding,
)


pytestmark = pytest.mark.unit
_EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_V10_CATALOG = MigrationCatalog(CANONICAL_MIGRATION_CATALOG.migrations[:10])


def _migrate_current(value, catalog=_V10_CATALOG):
    factory = ConnectionFactory._for_isolated_tests(value.core_db_path, catalog)
    result = SingleMigrator(
        factory,
        app_version="sbe-1.3-v10-test",
        storage_layout=_layout(value),
    ).run()
    return factory, result


def _v9_hierarchy(value):
    v8_factory, _ = _migrate_v9(value, _V8_CATALOG)
    ids = _seed_v8_hierarchy(v8_factory)
    factory, _ = _migrate_v9(value)
    ids["root_space"] = ids["root"]
    ids["team2_space"] = ids["team2"]
    ids["part2_space"] = ids["part2"]
    return factory, ids


def _published_item(
    connection,
    *,
    label: str,
    space_id: str,
    actor: str,
    timestamp: int,
) -> tuple[str, str]:
    item_id = _uuid(f"v10:item:{label}")
    revision_id = _uuid(f"v10:revision:{label}")
    title = "Title"
    body = "body"
    body_sha = __import__("hashlib").sha256(body.encode("utf-8")).hexdigest()
    connection.execute(
        """INSERT INTO knowledge_item(
             id,space_id,lineage_group_id,lifecycle_status,curation_status,
             current_revision_id,last_curation_transition_id,
             latest_deactivation_episode_id,redirect_item_id,created_by,
             updated_by,created_at,updated_at,row_version
           ) VALUES (?,?,NULL,'active','provisional',?,NULL,NULL,NULL,?,?,?, ?,1)""",
        (item_id, space_id, revision_id, actor, actor, timestamp, timestamp),
    )
    connection.execute(
        """INSERT INTO knowledge_revision(
             id,item_id,revision_no,parent_revision_id,base_revision_id,title,
             raw_content_sha256,normalized_content_sha256,content_size_bytes,
             normalizer_version,metadata_json,change_kind,change_reason,
             created_by,created_at
           ) VALUES (?,?,1,NULL,NULL,?,?,?,?, 'markdown_lf_v1','{}','initial',
                     'v10 fixture',?,?)""",
        (
            revision_id,
            item_id,
            title,
            body_sha,
            body_sha,
            len(body.encode("utf-8")),
            actor,
            timestamp,
        ),
    )
    connection.execute(
        """INSERT INTO knowledge_revision_body(
             revision_id,item_id,content_md,content_blob_hash,
             content_size_bytes,content_sha256,created_at
           ) VALUES (?,?,?,NULL,?,?,?)""",
        (
            revision_id,
            item_id,
            body,
            len(body.encode("utf-8")),
            body_sha,
            timestamp,
        ),
    )
    finding_id = _uuid(f"v10:finding:{label}")
    transition_id = _uuid(f"v10:transition:{label}")
    dedup_id = _uuid(f"v10:dedup:{label}")
    operation_id = _uuid(f"v10:curation-operation:{label}")
    connection.execute(
        """INSERT INTO operation_dedup(
             id,actor_user_id,operation_type,operation_id,request_sha256,status,
             http_status,result_json,error_json,created_at,completed_at,last_seen_at
           ) VALUES (?,?,'item_curation',?,?,'in_progress',NULL,NULL,NULL,
                     1,NULL,1)""",
        (dedup_id, actor, operation_id, "f" * 64),
    )
    _insert_human_release_finding(
        connection,
        finding_id=finding_id,
        user_id=actor,
        space_id=space_id,
        item_id=item_id,
        revision_id=revision_id,
    )
    _apply_release(
        connection,
        user_id=actor,
        space_id=space_id,
        item_id=item_id,
        revision_id=revision_id,
        finding_id=finding_id,
        dedup_id=dedup_id,
        operation_id=operation_id,
        transition_id=transition_id,
    )
    return item_id, revision_id


def _audit_outbox(
    connection,
    *,
    label: str,
    actor: str,
    space_id: str,
    item_id: str | None,
    revision_id: str | None,
    timestamp: int,
) -> tuple[int, int, str]:
    operation_id = _uuid(f"v10:operation:{label}")
    audit_id = connection.execute(
        """INSERT INTO audit_event(
             event_uuid,occurred_at,request_id,operation_id,action,outcome,
             actor_kind,actor_user_id,actor_display_name,actor_role_snapshot,
             space_id,item_id,base_revision_id,new_revision_id,reason,
             details_json,request_meta_json,schema_version
           ) VALUES (?,?,?,?,?,'committed','user',?,?, 'user',?,?,NULL,?,
                     'v10 fixture','{}','{}',1)""",
        (
            _uuid(f"v10:audit:{label}"),
            timestamp,
            _uuid(f"v10:request:{label}"),
            operation_id,
            f"semantic.{label}",
            actor,
            actor.title(),
            space_id,
            item_id,
            revision_id,
        ),
    ).lastrowid
    outbox_id = connection.execute(
        """INSERT INTO outbox_event(
             logical_key,space_id,aggregate_type,aggregate_id,
             aggregate_version,event_type,payload_json,payload_schema_version,
             state,available_at,attempt_count,created_at
           ) VALUES (?,?,'semantic',?,1,?,'{}',1,'pending',?,0,?)""",
        (
            f"v10:{label}",
            space_id,
            _uuid(f"v10:aggregate:{label}"),
            f"semantic.{label}",
            timestamp,
            timestamp,
        ),
    ).lastrowid
    return int(audit_id), int(outbox_id), operation_id


def _system_job(
    connection,
    *,
    label: str,
    effect_key: str,
    space_id: str,
    timestamp: int,
) -> str:
    job_id = _uuid(f"v10:job:{label}")
    connection.execute(
        """INSERT INTO durable_job(
             id,dedup_id,operation_id,actor_kind,actor_user_id,space_id,
             job_type,handler_version,effect_key,input_ref_json,input_sha256,
             expected_state_json,authorization_snapshot_json,
             authorization_snapshot_sha256,authorization_scope_epoch,
             authorization_part_id,authorization_capabilities_json,state,
             claim_owner,claim_expires_at,attempt_count,max_attempts,cancellable,
             cancel_requested,progress_permille,result_json,error_json,
             available_at,created_at,updated_at,completed_at
           ) VALUES (?,NULL,NULL,'system',NULL,?,'semantic_edge.evaluate','v1',
                     ?,'{}',?,'{}','{"actor_kind":"system","schema_version":1}',
                     ?,NULL,NULL,'[]','queued',NULL,NULL,0,3,0,0,0,NULL,NULL,
                     ?,?,?,NULL)""",
        (job_id, space_id, effect_key, "a" * 64, "b" * 64, timestamp, timestamp, timestamp),
    )
    return job_id


def _system_edge_audit_outbox(
    connection,
    *,
    label: str,
    edge_id: str,
    space_id: str,
    source_item_id: str,
    source_revision_id: str,
    target_revision_id: str,
    timestamp: int,
) -> tuple[int, int]:
    audit_id = connection.execute(
        """INSERT INTO audit_event(
             event_uuid,occurred_at,request_id,operation_id,action,outcome,
             actor_kind,actor_user_id,actor_display_name,actor_role_snapshot,
             space_id,item_id,base_revision_id,new_revision_id,reason,
             details_json,request_meta_json,schema_version
           ) VALUES (?,?,?,NULL,'semantic.edge.activate','committed','system',
                     NULL,'Semantic Edge Worker','system',?,?,?,?,
                     'validated automatic edge','{}','{}',1)""",
        (
            _uuid(f"v10:edge-audit:{label}"),
            timestamp,
            _uuid(f"v10:edge-request:{label}"),
            space_id,
            source_item_id,
            source_revision_id,
            target_revision_id,
        ),
    ).lastrowid
    outbox_id = connection.execute(
        """INSERT INTO outbox_event(
             logical_key,space_id,aggregate_type,aggregate_id,
             aggregate_version,event_type,payload_json,payload_schema_version,
             state,available_at,attempt_count,created_at
           ) VALUES (?,?,'knowledge_edge',?,1,'semantic.edge.activated','{}',1,
                     'pending',?,0,?)""",
        (f"semantic-edge:{label}", space_id, edge_id, timestamp, timestamp),
    ).lastrowid
    return int(audit_id), int(outbox_id)


@pytest.mark.scenario("AGRAPH-001")
@pytest.mark.scenario("AGRAPH-002")
@pytest.mark.scenario("AGRAPH-004")
def test_v9_to_v10_preserves_local_edges_matches_pristine_replays_and_rolls_back(
    tmp_path, isolated_knowledge_layout
) -> None:
    v9_factory, ids = _v9_hierarchy(isolated_knowledge_layout)
    edge_id = _uuid("v10:legacy-edge")
    connection = v9_factory.open()
    try:
        with transaction(connection):
            first_item, first_revision = _published_item(
                connection,
                label="legacy-a",
                space_id=ids["part_space"],
                actor="leader",
                timestamp=500,
            )
            second_item, second_revision = _published_item(
                connection,
                label="legacy-b",
                space_id=ids["part_space"],
                actor="leader",
                timestamp=501,
            )
            connection.execute(
                """INSERT INTO knowledge_edge(
                     id,space_id,source_item_id,target_item_id,edge_type,
                     edge_status,origin,source_evidence_revision_id,
                     target_evidence_revision_id,approval_finding_id,
                     evidence_json,needs_revalidation,created_by,updated_by,
                     created_at,updated_at,row_version
                   ) VALUES (?, ?,?,?,'related','active','manual',?,?,NULL,
                             '{}',0,'leader','leader',502,502,1)""",
                (
                    edge_id,
                    ids["part_space"],
                    first_item,
                    second_item,
                    first_revision,
                    second_revision,
                ),
            )
            before = tuple(
                connection.execute(
                    """SELECT id,space_id,source_item_id,target_item_id,edge_type,
                              edge_status,origin,source_evidence_revision_id,
                              target_evidence_revision_id,approval_finding_id,
                              evidence_json,needs_revalidation,created_by,
                              updated_by,created_at,updated_at,row_version
                         FROM knowledge_edge WHERE id=?""",
                    (edge_id,),
                ).fetchone()
            )
    finally:
        connection.close()

    factory, first = _migrate_current(isolated_knowledge_layout)
    assert first.applied_versions == (10,)
    connection = factory.open()
    try:
        after = tuple(
            connection.execute(
                """SELECT id,space_id,source_item_id,target_item_id,edge_type,
                          edge_status,origin,source_evidence_revision_id,
                          target_evidence_revision_id,approval_finding_id,
                          evidence_json,needs_revalidation,created_by,
                          updated_by,created_at,updated_at,row_version
                     FROM knowledge_edge WHERE id=?""",
                (edge_id,),
            ).fetchone()
        )
        assert after == before
        assert tuple(
            connection.execute(
                """SELECT actor_kind,system_job_id,semantic_key,effect_key
                     FROM knowledge_edge WHERE id=?""",
                (edge_id,),
            ).fetchone()
        ) == ("user", None, None, None)
        assert schema_sha256(connection) == V010_EXPECTED_SCHEMA_SHA256
        verify_canonical_integrity(connection, _V10_CATALOG)
    finally:
        connection.close()
    _same, replay = _migrate_current(isolated_knowledge_layout)
    assert replay.applied_versions == ()

    pristine = create_isolated_test_layout(tmp_path / "v10-pristine", user_id="v10")
    pristine_factory, pristine_result = _migrate_current(pristine)
    assert pristine_result.applied_versions == tuple(range(1, 11))
    pristine_connection = pristine_factory.open()
    try:
        assert schema_sha256(pristine_connection) == V010_EXPECTED_SCHEMA_SHA256
    finally:
        pristine_connection.close()

    rollback = create_isolated_test_layout(tmp_path / "v10-rollback", user_id="v10")
    rollback_factory, _rollback_ids = _v9_hierarchy(rollback)
    failing_v10 = Migration(
        version=10,
        name="semantic_graph_contracts_v10_failure_probe",
        statements=(*V010_STATEMENTS, "SELECT no_such_v10_function()"),
        expected_schema_sha256=V010_EXPECTED_SCHEMA_SHA256,
        postcondition_queries=V010_POSTCONDITION_QUERIES,
        requires_foreign_keys_off=True,
    )
    failing_catalog = MigrationCatalog((*_V9_CATALOG.migrations, failing_v10))
    failing_factory = ConnectionFactory._for_isolated_tests(
        rollback.core_db_path, failing_catalog
    )
    with pytest.raises(KnowledgeCoreMigrationError):
        SingleMigrator(
            failing_factory,
            app_version="sbe-1.3-rollback-test",
            storage_layout=_layout(rollback),
        ).run()
    probe = rollback_factory.open()
    try:
        assert schema_sha256(probe) == V009_EXPECTED_SCHEMA_SHA256
        assert probe.execute(
            "SELECT max(version) FROM schema_migration"
        ).fetchone()[0] == 9
        assert probe.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name='semantic_relation_type'"
        ).fetchone()[0] == 0
        assert "semantic_auto" not in probe.execute(
            "SELECT sql FROM sqlite_schema WHERE name='knowledge_edge'"
        ).fetchone()[0]
    finally:
        probe.close()


def _insert_aggregate(
    connection,
    *,
    label: str,
    home_space_id: str,
    home_org_unit_id: str,
    source_space_id: str,
    source_item_id: str,
    source_revision_id: str,
    target_space_id: str,
    target_item_id: str,
    target_revision_id: str,
    actor: str,
    audit_id: int,
    outbox_id: int,
    operation_id: str,
    semantic_key: str,
    edge_type: str = "related",
    direction: str = "symmetric",
) -> str:
    edge_id = _uuid(f"v10:aggregate:{label}")
    source_item_row_version = connection.execute(
        "SELECT row_version FROM knowledge_item WHERE id=?",
        (source_item_id,),
    ).fetchone()[0]
    target_item_row_version = connection.execute(
        "SELECT row_version FROM knowledge_item WHERE id=?",
        (target_item_id,),
    ).fetchone()[0]
    connection.execute(
        """INSERT INTO aggregate_knowledge_edge(
             id,semantic_key,home_space_id,home_org_unit_id,source_space_id,
             source_item_id,source_revision_id,target_space_id,target_item_id,
             target_revision_id,edge_type,direction,edge_status,origin,
             actor_kind,actor_user_id,system_job_id,operation_id,effect_key,
             detector_generation,model_provider,model_name,prompt_schema_version,
             scoring_version,confidence_ppm,source_evidence_span_sha256,
             target_evidence_span_sha256,source_evidence_location_json,
             target_evidence_location_json,closure_proof_sha256,
             source_item_row_version,target_item_row_version,audit_event_id,
             outbox_event_id,created_at,updated_at,deactivated_at,row_version
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'active','manual','user',?,NULL,
                     ?,?,NULL,NULL,NULL,NULL,NULL,NULL,?,?, '{}','{}',?,?,?,
                     ?,?,600,600,NULL,1)""",
        (
            edge_id,
            semantic_key,
            home_space_id,
            home_org_unit_id,
            source_space_id,
            source_item_id,
            source_revision_id,
            target_space_id,
            target_item_id,
            target_revision_id,
            edge_type,
            direction,
            actor,
            operation_id,
            f"aggregate:{label}",
            "c" * 64,
            "d" * 64,
            "e" * 64,
            source_item_row_version,
            target_item_row_version,
            audit_id,
            outbox_id,
        ),
    )
    return edge_id


@pytest.mark.scenario("AGRAPH-003")
def test_aggregate_edges_require_nonpersonal_endpoints_exact_lca_and_one_home(
    isolated_knowledge_layout,
) -> None:
    _v9, ids = _v9_hierarchy(isolated_knowledge_layout)
    factory, _ = _migrate_current(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            left_item, left_revision = _published_item(
                connection,
                label="aggregate-left",
                space_id=ids["part_space"],
                actor="leader",
                timestamp=560,
            )
            right_item, right_revision = _published_item(
                connection,
                label="aggregate-right",
                space_id=ids["part2_space"],
                actor="foreign",
                timestamp=561,
            )
            audit_id, outbox_id, operation_id = _audit_outbox(
                connection,
                label="aggregate-valid",
                actor="leader",
                space_id=ids["root_space"],
                item_id=left_item,
                revision_id=left_revision,
                timestamp=562,
            )
            edge_id = _insert_aggregate(
                connection,
                label="valid",
                home_space_id=ids["root_space"],
                home_org_unit_id=ids["root"],
                source_space_id=ids["part_space"],
                source_item_id=left_item,
                source_revision_id=left_revision,
                target_space_id=ids["part2_space"],
                target_item_id=right_item,
                target_revision_id=right_revision,
                actor="leader",
                audit_id=audit_id,
                outbox_id=outbox_id,
                operation_id=operation_id,
                semantic_key="1" * 64,
            )
            assert connection.execute(
                "SELECT home_space_id FROM aggregate_knowledge_edge WHERE id=?",
                (edge_id,),
            ).fetchone()[0] == ids["root_space"]

            with pytest.raises(sqlite3.IntegrityError):
                _insert_aggregate(
                    connection,
                    label="wrong-lca",
                    home_space_id=ids["team_space"],
                    home_org_unit_id=ids["team"],
                    source_space_id=ids["part_space"],
                    source_item_id=left_item,
                    source_revision_id=left_revision,
                    target_space_id=ids["part2_space"],
                    target_item_id=right_item,
                    target_revision_id=right_revision,
                    actor="leader",
                    audit_id=audit_id,
                    outbox_id=outbox_id,
                    operation_id=_uuid("v10:wrong-lca-operation"),
                    semantic_key="2" * 64,
                )
            with pytest.raises(sqlite3.IntegrityError):
                _insert_aggregate(
                    connection,
                    label="duplicate-home",
                    home_space_id=ids["root_space"],
                    home_org_unit_id=ids["root"],
                    source_space_id=ids["part_space"],
                    source_item_id=left_item,
                    source_revision_id=left_revision,
                    target_space_id=ids["part2_space"],
                    target_item_id=right_item,
                    target_revision_id=right_revision,
                    actor="leader",
                    audit_id=audit_id,
                    outbox_id=outbox_id,
                    operation_id=_uuid("v10:duplicate-home-operation"),
                    semantic_key="1" * 64,
                )

            personal_space = connection.execute(
                "SELECT id FROM knowledge_space WHERE owner_user_id='leader' AND space_kind='personal'"
            ).fetchone()[0]
            personal_item, personal_revision = _published_item(
                connection,
                label="aggregate-personal",
                space_id=personal_space,
                actor="leader",
                timestamp=563,
            )
            with pytest.raises(sqlite3.IntegrityError):
                _insert_aggregate(
                    connection,
                    label="personal",
                    home_space_id=ids["root_space"],
                    home_org_unit_id=ids["root"],
                    source_space_id=personal_space,
                    source_item_id=personal_item,
                    source_revision_id=personal_revision,
                    target_space_id=ids["part2_space"],
                    target_item_id=right_item,
                    target_revision_id=right_revision,
                    actor="leader",
                    audit_id=audit_id,
                    outbox_id=outbox_id,
                    operation_id=_uuid("v10:personal-operation"),
                    semantic_key="3" * 64,
                )
    finally:
        connection.close()


def _insert_auto_local_edge(
    connection,
    *,
    edge_id: str,
    space_id: str,
    source_item_id: str,
    source_revision_id: str,
    target_item_id: str,
    target_revision_id: str,
    job_id: str,
    effect_key: str,
    audit_id: int,
    outbox_id: int,
    confidence: int | None,
) -> None:
    connection.execute(
        """INSERT INTO knowledge_edge(
             id,space_id,source_item_id,target_item_id,edge_type,edge_status,
             origin,actor_kind,source_evidence_revision_id,
             target_evidence_revision_id,approval_finding_id,system_job_id,
             semantic_key,detector_generation,model_provider,model_name,
             prompt_schema_version,scoring_version,confidence_ppm,effect_key,
             semantic_evidence_sha256,source_evidence_span_sha256,
             target_evidence_span_sha256,source_evidence_location_json,
             target_evidence_location_json,audit_event_id,outbox_event_id,
             evidence_json,needs_revalidation,
             created_by,updated_by,created_at,updated_at,row_version
           ) VALUES (?,?,?,?,'supports','active','semantic_auto','system',?,?,
                     NULL,?, ?,1,'local','judge-v1','prompt-v1','score-v1',?,?,
                     ?,?,?,'{}','{}',?,?,'{}',0,NULL,NULL,700,700,1)""",
        (
            edge_id,
            space_id,
            source_item_id,
            target_item_id,
            source_revision_id,
            target_revision_id,
            job_id,
            "4" * 64,
            confidence,
            effect_key,
            "5" * 64,
            "6" * 64,
            "7" * 64,
            audit_id,
            outbox_id,
        ),
    )


@pytest.mark.scenario("AUTOEDGE-001")
@pytest.mark.scenario("AUTOEDGE-002")
def test_semantic_auto_local_edges_require_complete_system_job_and_evidence(
    isolated_knowledge_layout,
) -> None:
    _v9, ids = _v9_hierarchy(isolated_knowledge_layout)
    factory, _ = _migrate_current(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            source_item, source_revision = _published_item(
                connection,
                label="auto-source",
                space_id=ids["part_space"],
                actor="leader",
                timestamp=680,
            )
            target_item, target_revision = _published_item(
                connection,
                label="auto-target",
                space_id=ids["part_space"],
                actor="leader",
                timestamp=681,
            )
            effect_key = "semantic-auto:complete"
            job_id = _system_job(
                connection,
                label="auto-complete",
                effect_key=effect_key,
                space_id=ids["part_space"],
                timestamp=682,
            )
            edge_id = _uuid("v10:auto-edge:complete")
            audit_id, outbox_id = _system_edge_audit_outbox(
                connection,
                label="auto-complete",
                edge_id=edge_id,
                space_id=ids["part_space"],
                source_item_id=source_item,
                source_revision_id=source_revision,
                target_revision_id=target_revision,
                timestamp=683,
            )
            _insert_auto_local_edge(
                connection,
                edge_id=edge_id,
                space_id=ids["part_space"],
                source_item_id=source_item,
                source_revision_id=source_revision,
                target_item_id=target_item,
                target_revision_id=target_revision,
                job_id=job_id,
                effect_key=effect_key,
                audit_id=audit_id,
                outbox_id=outbox_id,
                confidence=900000,
            )
            with pytest.raises(sqlite3.IntegrityError):
                _insert_auto_local_edge(
                    connection,
                    edge_id=_uuid("v10:auto-edge:incomplete"),
                    space_id=ids["part_space"],
                    source_item_id=source_item,
                    source_revision_id=source_revision,
                    target_item_id=target_item,
                    target_revision_id=target_revision,
                    job_id=job_id,
                    effect_key="semantic-auto:incomplete",
                    audit_id=audit_id,
                    outbox_id=outbox_id,
                    confidence=None,
                )
        assert connection.execute(
            "SELECT count(*) FROM knowledge_edge WHERE origin='semantic_auto'"
        ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.scenario("AUTOEDGE-003")
def test_intent_seed_and_decision_ledgers_are_tagged_immutable_and_idempotent(
    isolated_knowledge_layout,
) -> None:
    _v9, ids = _v9_hierarchy(isolated_knowledge_layout)
    factory, _ = _migrate_current(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            source_item, source_revision = _published_item(
                connection,
                label="intent-source",
                space_id=ids["part_space"],
                actor="leader",
                timestamp=800,
            )
            body_sha = connection.execute(
                "SELECT normalized_content_sha256 FROM knowledge_revision WHERE id=?",
                (source_revision,),
            ).fetchone()[0]
            audit_id, outbox_id, _operation_id = _audit_outbox(
                connection,
                label="seed-run",
                actor="admin",
                space_id=ids["root_space"],
                item_id=source_item,
                revision_id=source_revision,
                timestamp=801,
            )
            seed_id = _uuid("v10:seed-run")
            connection.execute(
                """INSERT INTO semantic_edge_intent_seed_run(
                     id,effect_key,cause_kind,scope_kind,scope_id,
                     detector_generation,previous_detector_generation,
                     old_ancestor_space_id,new_ancestor_space_id,
                     organization_event_audit_id,detector_rollout_audit_id,
                     lifecycle_restore_audit_id,snapshot_domain_high_water,
                     snapshot_audit_high_water,cursor_updated_at,cursor_revision_id,
                     state,fence,scanned_count,intent_count,result_sha256,error_json,
                     created_audit_event_id,created_outbox_event_id,created_at,
                     updated_at,completed_at,row_version
                   ) VALUES (?,'seed:baseline','baseline_backfill','global',NULL,
                             1,NULL,NULL,NULL,NULL,NULL,NULL,100,?,0,NULL,
                             'pending',1,0,0,NULL,NULL,?,?,802,802,NULL,1)""",
                (seed_id, audit_id, audit_id, outbox_id),
            )
            connection.execute(
                """UPDATE semantic_edge_intent_seed_run
                      SET state='running',cursor_updated_at=800,
                          cursor_revision_id=?,updated_at=803,row_version=2
                    WHERE id=?""",
                (source_revision, seed_id),
            )
            part_generation, part_row_version = tuple(
                connection.execute(
                    """SELECT s.generation,o.row_version FROM knowledge_space s
                         JOIN org_unit o ON o.id=s.owner_org_unit_id WHERE s.id=?""",
                    (ids["part_space"],),
                ).fetchone()
            )
            intent_id = _uuid("v10:intent")
            intent_effect = "intent:baseline:part-local"
            connection.execute(
                """INSERT INTO semantic_edge_intent(
                     id,effect_key,source_space_id,source_item_id,
                     source_revision_id,source_body_sha256,target_kind,
                     target_space_id,target_org_unit_id,detector_generation,
                     source_revision_row_version,source_space_generation,
                     target_space_generation,organization_generation,
                     closure_proof_sha256,item_lifecycle_generation,
                     wiki_lifecycle_generation,latest_episode_id,
                     latest_episode_no,latest_episode_restored_at,provenance_kind,
                     publication_transition_id,publication_audit_event_id,
                     publication_outbox_event_id,seed_run_id,
                     seed_snapshot_high_water,seed_cursor_updated_at,
                     seed_cursor_revision_id,organization_event_audit_id,
                     detector_rollout_audit_id,lifecycle_restore_audit_id,created_at
                   ) VALUES (?,?,?,?,?,?,'local',?,?,1,1,?,?,?, ?,1,0,NULL,NULL,
                             NULL,'baseline_backfill',NULL,NULL,NULL,?,100,800,?,
                             NULL,NULL,NULL,804)""",
                (
                    intent_id,
                    intent_effect,
                    ids["part_space"],
                    source_item,
                    source_revision,
                    body_sha,
                    ids["part_space"],
                    ids["part"],
                    part_generation,
                    part_generation,
                    part_row_version,
                    "8" * 64,
                    seed_id,
                    source_revision,
                ),
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE semantic_edge_intent SET created_at=805 WHERE id=?",
                    (intent_id,),
                )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM semantic_edge_intent WHERE id=?", (intent_id,)
                )

            job_id = _system_job(
                connection,
                label="intent-decision",
                effect_key=intent_effect,
                space_id=ids["part_space"],
                timestamp=805,
            )
            connection.execute(
                """UPDATE durable_job SET state='claimed',claim_owner='worker',
                          claim_expires_at=900,attempt_count=1,updated_at=806
                    WHERE id=?""",
                (job_id,),
            )
            connection.execute(
                "UPDATE durable_job SET state='running',updated_at=807 WHERE id=?",
                (job_id,),
            )
            terminal_id = _uuid("v10:decision:terminal")
            connection.execute(
                """INSERT INTO semantic_edge_decision(
                     id,job_id,intent_id,record_kind,ordinal,outcome,
                     candidate_space_id,candidate_item_id,candidate_revision_id,
                     retrieval_version,retrieval_score_json,model_provider,
                     model_name,prompt_schema_version,response_sha256,edge_type,
                     direction,model_grade,source_evidence_span_sha256,
                     target_evidence_span_sha256,validation_result,
                     terminal_reason,candidate_count,candidate_ids_sha256,
                     local_edge_id,aggregate_edge_id,evidence_json,created_at
                   ) VALUES (?,?,?,'job_terminal',NULL,'no_candidate',NULL,NULL,
                             NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
                             NULL,NULL,'no eligible candidate',0,?,NULL,NULL,'{}',808)""",
                (terminal_id, job_id, intent_id, "9" * 64),
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO semantic_edge_decision(
                         id,job_id,intent_id,record_kind,ordinal,outcome,
                         terminal_reason,candidate_count,candidate_ids_sha256,
                         evidence_json,created_at
                       ) VALUES (?,?,?,'job_terminal',NULL,'stale','stale',0,?,
                                 '{}',809)""",
                    (_uuid("v10:decision:duplicate"), job_id, intent_id, "a" * 64),
                )
            connection.execute(
                """UPDATE durable_job SET state='succeeded',claim_owner=NULL,
                          claim_expires_at=NULL,result_json='{}',completed_at=809,
                          progress_permille=1000,updated_at=809 WHERE id=?""",
                (job_id,),
            )
            connection.execute(
                """UPDATE semantic_edge_intent_seed_run
                      SET state='completed',scanned_count=1,intent_count=1,
                          result_sha256=?,updated_at=810,completed_at=810,
                          row_version=3 WHERE id=?""",
                ("b" * 64, seed_id),
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """UPDATE semantic_edge_intent_seed_run
                          SET cursor_updated_at=0,updated_at=811,row_version=4
                        WHERE id=?""",
                    (seed_id,),
                )
        assert connection.execute(
            """SELECT count(*) FROM semantic_edge_decision
               WHERE job_id=? AND record_kind='job_terminal'""",
            (job_id,),
        ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.scenario("REF-001")
@pytest.mark.scenario("REF-002")
def test_explicit_references_are_revision_bound_and_do_not_mutate_graphs(
    isolated_knowledge_layout,
) -> None:
    _v9, ids = _v9_hierarchy(isolated_knowledge_layout)
    factory, _ = _migrate_current(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            source_item, source_revision = _published_item(
                connection,
                label="reference-source",
                space_id=ids["part_space"],
                actor="leader",
                timestamp=900,
            )
            target_item, target_revision = _published_item(
                connection,
                label="reference-target",
                space_id=ids["part_space"],
                actor="leader",
                timestamp=901,
            )
            foreign_item, foreign_revision = _published_item(
                connection,
                label="reference-foreign",
                space_id=ids["part2_space"],
                actor="foreign",
                timestamp=902,
            )
            audit_id, outbox_id, operation_id = _audit_outbox(
                connection,
                label="explicit-reference",
                actor="leader",
                space_id=ids["part_space"],
                item_id=source_item,
                revision_id=source_revision,
                timestamp=903,
            )
            graph_counts = tuple(
                connection.execute(
                    """SELECT (SELECT count(*) FROM knowledge_edge),
                              (SELECT count(*) FROM aggregate_knowledge_edge)"""
                ).fetchone()
            )
            reference_id = _uuid("v10:explicit-reference")
            connection.execute(
                """INSERT INTO knowledge_explicit_reference(
                     id,source_space_id,source_item_id,source_revision_id,ordinal,
                     syntax_version,target_space_id,target_item_id,
                     target_revision_id,closure_proof_sha256,actor_user_id,
                     operation_id,effect_key,audit_event_id,outbox_event_id,
                     availability_status,unavailable_reason,created_at,updated_at,
                     row_version
                   ) VALUES (?,?,?,?,1,'item-ref-v1',?,?,?,?,?,?,
                             'reference:effect',?,?,'available',NULL,904,904,1)""",
                (
                    reference_id,
                    ids["part_space"],
                    source_item,
                    source_revision,
                    ids["part_space"],
                    target_item,
                    target_revision,
                    "c" * 64,
                    "leader",
                    operation_id,
                    audit_id,
                    outbox_id,
                ),
            )
            assert tuple(
                connection.execute(
                    """SELECT (SELECT count(*) FROM knowledge_edge),
                              (SELECT count(*) FROM aggregate_knowledge_edge)"""
                ).fetchone()
            ) == graph_counts
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO knowledge_explicit_reference(
                         id,source_space_id,source_item_id,source_revision_id,
                         ordinal,syntax_version,target_space_id,target_item_id,
                         target_revision_id,closure_proof_sha256,actor_user_id,
                         operation_id,effect_key,audit_event_id,outbox_event_id,
                         availability_status,unavailable_reason,created_at,
                         updated_at,row_version
                       ) VALUES (?,?,?,?,2,'item-ref-v1',?,?,?,?,?,?,?, ?,?,
                                 'available',NULL,905,905,1)""",
                    (
                        _uuid("v10:explicit-reference:foreign"),
                        ids["part_space"],
                        source_item,
                        source_revision,
                        ids["part2_space"],
                        foreign_item,
                        foreign_revision,
                        "d" * 64,
                        "leader",
                        _uuid("v10:reference:foreign-operation"),
                        "reference:foreign",
                        audit_id,
                        outbox_id,
                    ),
                )
            connection.execute(
                """UPDATE knowledge_explicit_reference
                      SET availability_status='unavailable',
                          unavailable_reason='target_inactive',updated_at=906,
                          row_version=2 WHERE id=?""",
                (reference_id,),
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE knowledge_explicit_reference SET ordinal=2 WHERE id=?",
                    (reference_id,),
                )
        assert tuple(
            connection.execute(
                """SELECT syntax_version,target_revision_id,availability_status,
                          unavailable_reason FROM knowledge_explicit_reference
                   WHERE id=?""",
                (reference_id,),
            ).fetchone()
        ) == ("item-ref-v1", target_revision, "unavailable", "target_inactive")
    finally:
        connection.close()


@pytest.mark.scenario("AGRAPH-001")
@pytest.mark.scenario("AUTOEDGE-003")
def test_relation_suppression_and_generation_vector_state_is_closed(
    isolated_knowledge_layout,
) -> None:
    _v9, ids = _v9_hierarchy(isolated_knowledge_layout)
    factory, _ = _migrate_current(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """UPDATE semantic_relation_type SET direction_policy='directed'
                         WHERE relation_type='related'"""
                )

            source_item, source_revision = _published_item(
                connection,
                label="suppression-source",
                space_id=ids["part_space"],
                actor="leader",
                timestamp=1200,
            )
            target_item, target_revision = _published_item(
                connection,
                label="suppression-target",
                space_id=ids["part_space"],
                actor="leader",
                timestamp=1201,
            )
            effect_key = "semantic-auto:suppression"
            job_id = _system_job(
                connection,
                label="suppression",
                effect_key=effect_key,
                space_id=ids["part_space"],
                timestamp=1202,
            )
            edge_id = _uuid("v10:auto-edge:suppression")
            edge_audit, edge_outbox = _system_edge_audit_outbox(
                connection,
                label="suppression",
                edge_id=edge_id,
                space_id=ids["part_space"],
                source_item_id=source_item,
                source_revision_id=source_revision,
                target_revision_id=target_revision,
                timestamp=1203,
            )
            _insert_auto_local_edge(
                connection,
                edge_id=edge_id,
                space_id=ids["part_space"],
                source_item_id=source_item,
                source_revision_id=source_revision,
                target_item_id=target_item,
                target_revision_id=target_revision,
                job_id=job_id,
                effect_key=effect_key,
                audit_id=edge_audit,
                outbox_id=edge_outbox,
                confidence=950000,
            )
            audit_id, outbox_id, operation_id = _audit_outbox(
                connection,
                label="suppression-create",
                actor="leader",
                space_id=ids["part_space"],
                item_id=source_item,
                revision_id=source_revision,
                timestamp=1204,
            )
            suppression_id = _uuid("v10:suppression")
            connection.execute(
                """INSERT INTO semantic_edge_suppression(
                     id,semantic_key,scope_kind,scope_space_id,local_edge_id,
                     aggregate_edge_id,reason,detector_generation_floor,
                     detector_generation_ceiling,status,actor_user_id,
                     operation_id,effect_key,audit_event_id,outbox_event_id,
                     superseding_local_edge_id,superseding_aggregate_edge_id,
                     lifted_by_user_id,lifted_at,lift_audit_event_id,
                     lift_outbox_event_id,created_at,updated_at,row_version
                   ) VALUES (?,?,'local',?,?,NULL,'incorrect relation',1,1,
                             'active','leader',?,?,?, ?,NULL,NULL,NULL,NULL,NULL,
                             NULL,1205,1205,1)""",
                (
                    suppression_id,
                    "4" * 64,
                    ids["part_space"],
                    edge_id,
                    operation_id,
                    "suppression:create",
                    audit_id,
                    outbox_id,
                ),
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO semantic_edge_suppression(
                         id,semantic_key,scope_kind,scope_space_id,local_edge_id,
                         aggregate_edge_id,reason,detector_generation_floor,
                         detector_generation_ceiling,status,actor_user_id,
                         operation_id,effect_key,audit_event_id,outbox_event_id,
                         superseding_local_edge_id,superseding_aggregate_edge_id,
                         lifted_by_user_id,lifted_at,lift_audit_event_id,
                         lift_outbox_event_id,created_at,updated_at,row_version
                       ) VALUES (?,?,'local',?,?,NULL,'invalid initial state',1,1,
                                 'lifted','leader',?,?,?, ?,NULL,NULL,'leader',
                                 1206,?, ?,1205,1206,2)""",
                    (
                        _uuid("v10:suppression:invalid"),
                        "4" * 64,
                        ids["part_space"],
                        edge_id,
                        _uuid("v10:suppression:invalid-operation"),
                        "suppression:invalid",
                        audit_id,
                        outbox_id,
                        audit_id,
                        outbox_id,
                    ),
                )
            lift_audit, lift_outbox, _lift_operation = _audit_outbox(
                connection,
                label="suppression-lift",
                actor="leader",
                space_id=ids["part_space"],
                item_id=source_item,
                revision_id=source_revision,
                timestamp=1207,
            )
            connection.execute(
                """UPDATE semantic_edge_suppression
                      SET status='lifted',lifted_by_user_id='leader',lifted_at=1208,
                          lift_audit_event_id=?,lift_outbox_event_id=?,
                          updated_at=1208,row_version=2 WHERE id=?""",
                (lift_audit, lift_outbox, suppression_id),
            )

            organization_row_version = connection.execute(
                "SELECT row_version FROM org_unit WHERE id=?", (ids["root"],)
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO aggregate_generation_vector(
                     selected_space_id,source_vector_json,source_vector_sha256,
                     home_edge_generation,desired_generation,built_generation,
                     manifest_sha256,closure_proof_sha256,
                     organization_row_version,status,last_error_json,created_at,
                     updated_at,row_version
                   ) VALUES (?,'[]',?,0,1,0,NULL,?,?,'idle',NULL,1210,1210,1)""",
                (
                    ids["root_space"],
                    _EMPTY_SHA,
                    "5" * 64,
                    organization_row_version,
                ),
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """UPDATE aggregate_generation_vector
                          SET desired_generation=0,updated_at=1211,row_version=2
                        WHERE selected_space_id=?""",
                    (ids["root_space"],),
                )
            connection.execute(
                """UPDATE aggregate_generation_vector
                      SET built_generation=1,manifest_sha256=?,status='ready',
                          updated_at=1212,row_version=2
                    WHERE selected_space_id=?""",
                ("6" * 64, ids["root_space"]),
            )
        assert tuple(
            connection.execute(
                """SELECT status,row_version FROM semantic_edge_suppression
                     WHERE id=?""",
                (suppression_id,),
            ).fetchone()
        ) == ("lifted", 2)
        assert tuple(
            connection.execute(
                """SELECT status,built_generation,desired_generation
                     FROM aggregate_generation_vector WHERE selected_space_id=?""",
                (ids["root_space"],),
            ).fetchone()
        ) == ("ready", 1, 1)
    finally:
        connection.close()
