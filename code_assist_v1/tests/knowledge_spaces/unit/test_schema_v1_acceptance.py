from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import threading

import pytest

from code_assist_v1.knowledge_core.migrations.v001_catalog import V001_STATEMENTS
from code_assist_v1.knowledge_core.migrations.v001_invariants import (
    POSTCONDITION_DEFINITIONS,
)
from code_assist_v1.knowledge_core.migrations.v001_seed import RESPONSIBILITY_ROOT_ID


def _uuid(number: int) -> str:
    return f"00000000-0000-0000-0000-{number:012d}"


def _connect(database: str | Path = ":memory:") -> sqlite3.Connection:
    connection = sqlite3.connect(database, timeout=10.0)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA recursive_triggers=ON")
    for statement in V001_STATEMENTS:
        connection.execute(statement)
    connection.commit()
    return connection


def _insert_user(
    connection: sqlite3.Connection,
    user_id: str,
    *,
    role: str = "user",
    part_id: str | None = None,
) -> None:
    connection.execute(
        """INSERT INTO app_user(
          id,display_name,password_hash,system_role,account_status,current_part_id,
          scope_epoch,created_at,updated_at,disabled_at,row_version
        ) VALUES (?,?,'hash',?,'active',?,1,1,1,NULL,1)""",
        (user_id, user_id, role, part_id),
    )


def _insert_space(
    connection: sqlite3.Connection,
    space_id: str,
    user_id: str,
) -> None:
    connection.execute(
        """INSERT INTO knowledge_space(
          id,space_kind,owner_user_id,owner_org_unit_id,status,write_enabled,
          generation,created_at,updated_at,row_version
        ) VALUES (?,'personal',?,NULL,'active',1,0,1,1,1)""",
        (space_id, user_id),
    )


def _insert_item(
    connection: sqlite3.Connection,
    *,
    user_id: str,
    space_id: str,
    item_id: str,
    revision_id: str,
    with_body: bool = False,
) -> None:
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
        ) VALUES (?,?,1,NULL,NULL,'Title',?,?,4,'v1','{}','initial','initial',?,1)""",
        (revision_id, item_id, "a" * 64, "a" * 64, user_id),
    )
    if with_body:
        connection.execute(
            """INSERT INTO knowledge_revision_body(
              revision_id,item_id,content_md,content_blob_hash,content_size_bytes,
              content_sha256,created_at
            ) VALUES (?,?,'body',NULL,4,?,1)""",
            (revision_id, item_id, "a" * 64),
        )
    connection.commit()


def _insert_edge(
    connection: sqlite3.Connection,
    *,
    edge_id: str,
    space_id: str,
    source_item_id: str,
    target_item_id: str,
    user_id: str,
    source_revision_id: str | None = None,
    target_revision_id: str | None = None,
    status: str = "active",
) -> None:
    connection.execute(
        """INSERT INTO knowledge_edge(
          id,space_id,source_item_id,target_item_id,edge_type,edge_status,origin,
          source_evidence_revision_id,target_evidence_revision_id,
          approval_finding_id,evidence_json,needs_revalidation,created_by,updated_by,
          created_at,updated_at,row_version
        ) VALUES (?,?,?,?,'related',?,'manual',?,?,NULL,'{}',0,?,?,1,1,1)""",
        (
            edge_id,
            space_id,
            source_item_id,
            target_item_id,
            status,
            source_revision_id,
            target_revision_id,
            user_id,
            user_id,
        ),
    )


@pytest.mark.unit
@pytest.mark.scenario("SCHEMA-001")
def test_schema_001_org_membership_leader_and_owner_combinations() -> None:
    connection = _connect()
    admin = "admin"
    team = _uuid(101)
    part_a = _uuid(102)
    part_b = _uuid(103)
    try:
        _insert_user(connection, admin, role="admin")
        for unit_id, kind, parent, name in (
            (team, "team", RESPONSIBILITY_ROOT_ID, "Team"),
            (part_a, "part", team, "Part A"),
            (part_b, "part", team, "Part B"),
        ):
            connection.execute(
                """INSERT INTO org_unit(
                  id,kind,parent_id,name,name_key,status,created_at,updated_at,
                  created_by,updated_by,row_version
                ) VALUES (?,?,?,?,?,'active',1,1,?,?,1)""",
                (unit_id, kind, parent, name, name.lower(), admin, admin),
            )

        with pytest.raises(sqlite3.IntegrityError, match="ORG_PARENT_INVALID"):
            connection.execute(
                """INSERT INTO org_unit(
                  id,kind,parent_id,name,name_key,status,created_at,updated_at,
                  created_by,updated_by,row_version
                ) VALUES (?,'team',?,'Bad','bad','active',1,1,?,?,1)""",
                (_uuid(104), part_a, admin, admin),
            )

        leader = "leader"
        _insert_user(connection, leader, part_id=part_a)
        connection.execute(
            """INSERT INTO org_membership(
              id,user_id,part_id,valid_from,valid_to,assigned_by,reason
            ) VALUES (?,?,?,1,NULL,?,'assigned')""",
            (_uuid(105), leader, part_a, admin),
        )
        connection.execute(
            """INSERT INTO org_role_assignment(
              id,user_id,org_unit_id,role,valid_from,valid_to,assigned_by,reason
            ) VALUES (?,?,?,'part_leader',1,NULL,?,'leader')""",
            (_uuid(106), leader, part_a, admin),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO org_membership(
                  id,user_id,part_id,valid_from,valid_to,assigned_by,reason
                ) VALUES (?,?,?,1,NULL,?,'second')""",
                (_uuid(107), leader, part_b, admin),
            )
        with pytest.raises(sqlite3.IntegrityError, match="MEMBERSHIP_INVALID"):
            connection.execute(
                """INSERT INTO org_role_assignment(
                  id,user_id,org_unit_id,role,valid_from,valid_to,assigned_by,reason
                ) VALUES (?,?,?,'part_leader',1,NULL,?,'wrong part')""",
                (_uuid(108), leader, part_b, admin),
            )
        with pytest.raises(sqlite3.IntegrityError, match="SPACE_OWNER_INVALID"):
            connection.execute(
                """INSERT INTO knowledge_space(
                  id,space_kind,owner_user_id,owner_org_unit_id,status,write_enabled,
                  generation,created_at,updated_at,row_version
                ) VALUES (?,'part',NULL,?,'active',0,0,1,1,1)""",
                (_uuid(109), team),
            )
        with pytest.raises(sqlite3.IntegrityError, match="SPACE_OWNER_INVALID"):
            connection.execute(
                """INSERT INTO knowledge_space(
                  id,space_kind,owner_user_id,owner_org_unit_id,status,write_enabled,
                  generation,created_at,updated_at,row_version
                ) VALUES (?,'part',NULL,?,'active',1,0,1,1,1)""",
                (_uuid(110), part_b),
            )
    finally:
        connection.close()


@pytest.mark.unit
@pytest.mark.scenario("SCHEMA-002")
def test_schema_002_composite_foreign_keys_reject_cross_space_references() -> None:
    connection = _connect()
    user_a, user_b = "user-a", "user-b"
    space_a, space_b = _uuid(201), _uuid(202)
    item_a, revision_a = _uuid(203), _uuid(204)
    item_a2, revision_a2 = _uuid(205), _uuid(206)
    item_b, revision_b = _uuid(207), _uuid(208)
    conversation = _uuid(209)
    try:
        for user_id, space_id in ((user_a, space_a), (user_b, space_b)):
            _insert_user(connection, user_id)
            _insert_space(connection, space_id, user_id)
        connection.commit()
        _insert_item(
            connection,
            user_id=user_a,
            space_id=space_a,
            item_id=item_a,
            revision_id=revision_a,
        )
        _insert_item(
            connection,
            user_id=user_a,
            space_id=space_a,
            item_id=item_a2,
            revision_id=revision_a2,
        )
        _insert_item(
            connection,
            user_id=user_b,
            space_id=space_b,
            item_id=item_b,
            revision_id=revision_b,
        )
        connection.execute(
            """INSERT INTO conversation(
              id,owner_user_id,space_id,captured_scope_epoch,title,status,
              metadata_json,created_at,updated_at,row_version
            ) VALUES (?,?,?,1,'','active','{}',1,1,1)""",
            (conversation, user_a, space_a),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO conversation_pin(
                  conversation_id,space_id,item_id,revision_id,created_by,created_at
                ) VALUES (?,?,?,?,?,1)""",
                (conversation, space_a, item_b, revision_b, user_a),
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_edge(
                connection,
                edge_id=_uuid(210),
                space_id=space_a,
                source_item_id=item_a,
                target_item_id=item_b,
                user_id=user_a,
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_edge(
                connection,
                edge_id=_uuid(211),
                space_id=space_a,
                source_item_id=item_a,
                target_item_id=item_a2,
                source_revision_id=revision_a2,
                target_revision_id=revision_a,
                user_id=user_a,
            )

        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO knowledge_item(
                  id,space_id,lineage_group_id,lifecycle_status,curation_status,
                  current_revision_id,last_curation_transition_id,
                  latest_deactivation_episode_id,redirect_item_id,created_by,updated_by,
                  created_at,updated_at,row_version
                ) VALUES (?,?,NULL,'active','provisional',?,NULL,NULL,NULL,?,?,1,1,1)""",
                (_uuid(212), space_a, revision_b, user_a, user_a),
            )
            connection.commit()
        connection.rollback()
    finally:
        connection.close()


@pytest.mark.unit
@pytest.mark.scenario("SCHEMA-003")
def test_schema_003_history_is_immutable_and_only_eligible_purge_removes_body() -> None:
    connection = _connect()
    admin = "admin"
    space_id, item_id, revision_id = _uuid(301), _uuid(302), _uuid(303)
    episode_id = _uuid(304)
    try:
        _insert_user(connection, admin, role="admin")
        _insert_space(connection, space_id, admin)
        connection.commit()
        _insert_item(
            connection,
            user_id=admin,
            space_id=space_id,
            item_id=item_id,
            revision_id=revision_id,
            with_body=True,
        )
        connection.execute(
            """INSERT INTO audit_event(
              event_uuid,occurred_at,request_id,operation_id,action,outcome,
              actor_kind,actor_user_id,actor_display_name,actor_role_snapshot,
              space_id,item_id,base_revision_id,new_revision_id,reason,
              details_json,request_meta_json,schema_version
            ) VALUES (?,1,?,NULL,'create','ok','user',?,'Admin','admin',?,?,?,?,'why',
              '{}','{}',1)""",
            (_uuid(305), _uuid(306), admin, space_id, item_id, revision_id, revision_id),
        )
        with pytest.raises(sqlite3.IntegrityError, match="REVISION_BODY_IMMUTABLE"):
            connection.execute(
                "UPDATE knowledge_revision SET title='changed' WHERE id=?",
                (revision_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="REVISION_BODY_IMMUTABLE"):
            connection.execute(
                "UPDATE knowledge_revision_body SET content_md='evil' WHERE revision_id=?",
                (revision_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="REVISION_BODY_IMMUTABLE"):
            connection.execute(
                "DELETE FROM knowledge_revision_body WHERE revision_id=?",
                (revision_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="HISTORY_IMMUTABLE"):
            connection.execute("UPDATE audit_event SET outcome='changed' WHERE event_uuid=?", (_uuid(305),))

        connection.execute(
            """INSERT INTO item_deactivation_episode(
              id,item_id,episode_no,deactivated_by,deactivated_at,reason,
              purge_eligible_at,restored_by,restored_at,purged_by,purged_at
            ) VALUES (?,?,1,?,1,'retired',7776000001,NULL,NULL,NULL,NULL)""",
            (episode_id, item_id, admin),
        )
        connection.execute(
            """UPDATE knowledge_item SET lifecycle_status='inactive',
               latest_deactivation_episode_id=?,updated_by=?,updated_at=2,row_version=2
               WHERE id=?""",
            (episode_id, admin, item_id),
        )
        connection.execute(
            """UPDATE item_deactivation_episode SET purged_by=?,purged_at=7776000001
               WHERE id=?""",
            (admin, episode_id),
        )
        connection.execute(
            """UPDATE knowledge_item SET lifecycle_status='purged_tombstone',
               current_revision_id=NULL,updated_by=?,updated_at=3,row_version=3
               WHERE id=?""",
            (admin, item_id),
        )
        connection.execute(
            "DELETE FROM knowledge_revision_body WHERE revision_id=?",
            (revision_id,),
        )
        assert connection.execute(
            "SELECT count(*) FROM knowledge_revision_body WHERE revision_id=?",
            (revision_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT title FROM knowledge_revision WHERE id=?", (revision_id,)
        ).fetchone() == ("Title",)
    finally:
        connection.close()


@pytest.mark.unit
@pytest.mark.concurrency
@pytest.mark.scenario("SCHEMA-004")
def test_schema_004_partial_uniqueness_and_lease_hold_under_writer_race(tmp_path: Path) -> None:
    database = tmp_path / "schema-race.sqlite"
    connection = _connect(database)
    user_id, space_id = "racer", _uuid(401)
    source_item, source_revision = _uuid(402), _uuid(403)
    target_item, target_revision = _uuid(404), _uuid(405)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        _insert_user(connection, user_id)
        _insert_space(connection, space_id, user_id)
        connection.commit()
        _insert_item(
            connection,
            user_id=user_id,
            space_id=space_id,
            item_id=source_item,
            revision_id=source_revision,
        )
        _insert_item(
            connection,
            user_id=user_id,
            space_id=space_id,
            item_id=target_item,
            revision_id=target_revision,
        )
        assert connection.execute(
            "SELECT item_id,fencing_generation FROM item_lease WHERE item_id=?",
            (source_item,),
        ).fetchone() == (source_item, 0)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO item_lease(item_id,fencing_generation) VALUES (?,0)",
                (source_item,),
            )

        connection.execute(
            """INSERT INTO item_deactivation_episode(
              id,item_id,episode_no,deactivated_by,deactivated_at,reason,
              purge_eligible_at,restored_by,restored_at,purged_by,purged_at
            ) VALUES (?,?,1,?,1,'one',7776000001,NULL,NULL,NULL,NULL)""",
            (_uuid(406), source_item, user_id),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO item_deactivation_episode(
                  id,item_id,episode_no,deactivated_by,deactivated_at,reason,
                  purge_eligible_at,restored_by,restored_at,purged_by,purged_at
                ) VALUES (?,?,2,?,2,'two',7776000002,NULL,NULL,NULL,NULL)""",
                (_uuid(407), source_item, user_id),
            )
        connection.commit()
    finally:
        connection.close()

    barrier = threading.Barrier(2)

    def competing_insert(edge_id: str) -> str:
        contender = sqlite3.connect(database, timeout=10.0)
        contender.execute("PRAGMA foreign_keys=ON")
        contender.execute("PRAGMA busy_timeout=10000")
        contender.execute("PRAGMA trusted_schema=OFF")
        contender.execute("PRAGMA recursive_triggers=ON")
        try:
            barrier.wait(timeout=5.0)
            contender.execute("BEGIN IMMEDIATE")
            _insert_edge(
                contender,
                edge_id=edge_id,
                space_id=space_id,
                source_item_id=source_item,
                target_item_id=target_item,
                user_id=user_id,
            )
            contender.commit()
            return "committed"
        except sqlite3.IntegrityError:
            contender.rollback()
            return "rejected"
        finally:
            contender.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(competing_insert, (_uuid(408), _uuid(409))))
    assert results == ["committed", "rejected"]


def _insert_human_release_finding(
    connection: sqlite3.Connection,
    *,
    finding_id: str,
    user_id: str,
    space_id: str,
    item_id: str,
    revision_id: str,
) -> None:
    connection.execute(
        """INSERT INTO curation_finding(
          id,space_id,finding_kind,risk_tier,severity,status,decision_mode,
          required_reviewer,visibility_effect,proposed_action,source_item_id,
          source_revision_id,target_item_id,target_revision_id,detector_name,
          detector_version,rule_version,model_id,prompt_version,confidence,
          evidence_json,proposed_action_json,reviewed_by,reviewed_at,review_reason,
          applied_operation_id,applied_dedup_id,applied_job_id,applied_by,
          applied_at,last_application_error_json,created_actor_kind,created_by,
          created_at,updated_at,row_version
        ) VALUES (?,?,'curation_release','T2','info','pending','human',
          'space_editor','agent_hidden','publish',?,?,NULL,NULL,'test','v1','r1',
          NULL,NULL,NULL,'{}','{}',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
          'user',?,1,1,1)""",
        (finding_id, space_id, item_id, revision_id, user_id),
    )
    connection.execute(
        """UPDATE curation_finding SET status='approved',reviewed_by=?,reviewed_at=2,
           review_reason='approved',updated_at=2,row_version=2 WHERE id=?""",
        (user_id, finding_id),
    )


def _apply_release(
    connection: sqlite3.Connection,
    *,
    user_id: str,
    space_id: str,
    item_id: str,
    revision_id: str,
    finding_id: str,
    dedup_id: str,
    operation_id: str,
    transition_id: str,
) -> None:
    connection.execute(
        """UPDATE curation_finding SET status='applied',applied_operation_id=?,
           applied_dedup_id=?,applied_by=?,applied_at=3,updated_at=3,row_version=3
           WHERE id=?""",
        (operation_id, dedup_id, user_id, finding_id),
    )
    connection.execute(
        """INSERT INTO item_curation_transition(
          id,item_id,space_id,transition_kind,from_status,to_status,
          base_revision_id,result_revision_id,finding_id,rescan_job_id,
          decision_job_id,dedup_id,operation_id,actor_kind,actor_user_id,
          result_item_row_version,reason,created_at
        ) VALUES (?,?,?,'finding_decision','provisional','published',?,?,?,NULL,
          NULL,?,?,'user',?,2,'publish',3)""",
        (
            transition_id,
            item_id,
            space_id,
            revision_id,
            revision_id,
            finding_id,
            dedup_id,
            operation_id,
            user_id,
        ),
    )
    connection.execute(
        """UPDATE knowledge_item SET curation_status='published',
           last_curation_transition_id=?,updated_by=?,updated_at=3,row_version=2
           WHERE id=?""",
        (transition_id, user_id, item_id),
    )
    connection.execute(
        """INSERT INTO search_document(
          item_id,space_id,revision_id,title,content_md,updated_at
        ) VALUES (?,?,?,'Title','body',3)""",
        (item_id, space_id, revision_id),
    )


@pytest.mark.unit
@pytest.mark.scenario("SCHEMA-005")
def test_schema_005_item_visibility_and_fts_commit_or_rollback_together() -> None:
    connection = _connect()
    user_id, space_id = "publisher", _uuid(501)
    item_id, revision_id, finding_id = _uuid(502), _uuid(503), _uuid(504)
    dedup_id, operation_id, transition_id = _uuid(505), _uuid(506), _uuid(507)
    visibility_query = dict(POSTCONDITION_DEFINITIONS)["search_visibility_consistency"]
    try:
        _insert_user(connection, user_id)
        _insert_space(connection, space_id, user_id)
        connection.commit()
        _insert_item(
            connection,
            user_id=user_id,
            space_id=space_id,
            item_id=item_id,
            revision_id=revision_id,
            with_body=True,
        )
        connection.execute(
            """INSERT INTO operation_dedup(
              id,actor_user_id,operation_type,operation_id,request_sha256,status,
              http_status,result_json,error_json,created_at,completed_at,last_seen_at
            ) VALUES (?,?,'item_curation',?,?,'in_progress',NULL,NULL,NULL,1,NULL,1)""",
            (dedup_id, user_id, operation_id, "b" * 64),
        )
        _insert_human_release_finding(
            connection,
            finding_id=finding_id,
            user_id=user_id,
            space_id=space_id,
            item_id=item_id,
            revision_id=revision_id,
        )
        connection.commit()

        connection.execute("BEGIN IMMEDIATE")
        _apply_release(
            connection,
            user_id=user_id,
            space_id=space_id,
            item_id=item_id,
            revision_id=revision_id,
            finding_id=finding_id,
            dedup_id=dedup_id,
            operation_id=operation_id,
            transition_id=transition_id,
        )
        assert connection.execute(visibility_query).fetchone() is None
        assert connection.execute(
            "SELECT count(*) FROM knowledge_fts WHERE knowledge_fts MATCH 'body'"
        ).fetchone() == (1,)
        connection.rollback()

        assert connection.execute(
            "SELECT curation_status,row_version FROM knowledge_item WHERE id=?",
            (item_id,),
        ).fetchone() == ("provisional", 1)
        assert connection.execute(
            "SELECT status,row_version FROM curation_finding WHERE id=?",
            (finding_id,),
        ).fetchone() == ("approved", 2)
        assert connection.execute("SELECT count(*) FROM search_document").fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM knowledge_fts WHERE knowledge_fts MATCH 'body'"
        ).fetchone() == (0,)

        connection.execute("BEGIN IMMEDIATE")
        _apply_release(
            connection,
            user_id=user_id,
            space_id=space_id,
            item_id=item_id,
            revision_id=revision_id,
            finding_id=finding_id,
            dedup_id=dedup_id,
            operation_id=operation_id,
            transition_id=transition_id,
        )
        connection.commit()
        assert connection.execute(visibility_query).fetchone() is None
        assert connection.execute(
            "SELECT curation_status FROM knowledge_item WHERE id=?", (item_id,)
        ).fetchone() == ("published",)
        assert connection.execute(
            "SELECT count(*) FROM knowledge_fts WHERE knowledge_fts MATCH 'body'"
        ).fetchone() == (1,)
    finally:
        connection.close()


@pytest.mark.unit
@pytest.mark.scenario("SCHEMA-007")
def test_schema_007_actor_references_restrict_delete_and_snapshots_survive_changes() -> None:
    connection = _connect()
    user_id = "audited"
    event_uuid = _uuid(701)
    try:
        _insert_user(connection, user_id)
        connection.execute(
            """INSERT INTO audit_event(
              event_uuid,occurred_at,request_id,operation_id,action,outcome,
              actor_kind,actor_user_id,actor_display_name,actor_role_snapshot,
              space_id,item_id,base_revision_id,new_revision_id,reason,
              details_json,request_meta_json,schema_version
            ) VALUES (?,1,?,NULL,'rename','ok','user',?,'Original Name','user',
              NULL,NULL,NULL,NULL,'reason','{}','{}',1)""",
            (event_uuid, _uuid(702), user_id),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM app_user WHERE id=?", (user_id,))
        connection.execute(
            """UPDATE app_user SET display_name='Renamed',updated_at=2,row_version=2
               WHERE id=?""",
            (user_id,),
        )
        connection.execute(
            """UPDATE app_user SET account_status='disabled',disabled_at=3,
               scope_epoch=2,updated_at=3,row_version=3 WHERE id=?""",
            (user_id,),
        )
        assert connection.execute(
            """SELECT actor_display_name,actor_role_snapshot
               FROM audit_event WHERE event_uuid=?""",
            (event_uuid,),
        ).fetchone() == ("Original Name", "user")
        with pytest.raises(sqlite3.IntegrityError, match="HISTORY_IMMUTABLE"):
            connection.execute(
                "UPDATE audit_event SET actor_display_name='Renamed' WHERE event_uuid=?",
                (event_uuid,),
            )
    finally:
        connection.close()
