from __future__ import annotations

import hashlib
import json
from dataclasses import fields, replace
import inspect
from pathlib import Path
import re
import shutil
import subprocess
import threading
from uuid import UUID, uuid5

import pytest
from flask import Flask
from werkzeug.serving import make_server

from code_assist_v1.knowledge_core.canonical_routes import (
    AgentTurnCompletion,
    CompactSummaryCompletion,
    register_canonical_routes,
)
from code_assist_v1.knowledge_core.db import ConnectionFactory, TransactionMode, transaction
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.conversations import (
    ConversationCreate,
    ConversationRepository,
)
from code_assist_v1.knowledge_core.repositories.audit import AuditRepository
from code_assist_v1.knowledge_core.repositories.organization import (
    OrganizationRepository,
    RESPONSIBILITY_ROOT_ID,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.scope import ScopeNotFoundError, ScopeResolver
from code_assist_v1.knowledge_core.services.retrieval import (
    CanonicalRetrievalService,
    ConversationRetrievalRequest,
    RetrievalScopeStaleError,
    RetrievalStateConflictError,
    SpaceRetrievalRequest,
)
from code_assist_v1.knowledge_core.services.management_reads import (
    CanonicalManagementReadService,
)
from code_assist_v1.knowledge_core.services.agent_context import (
    AgentBoundContextResolution,
    AgentContextIntegrityError,
    AgentContextResolverUnavailableError,
    AgentContextScopeStaleError,
    AgentContextStateConflictError,
    AgentTurnContextRequest,
    AgentTurnContextService,
)
from code_assist_v1.knowledge_core.services.scope_selection import ScopeSelectionService
from code_assist_v1.knowledge_core.services.conversations import (
    AcceptConversationTurnCommand,
    AddConversationContextCommand,
    AddConversationPinCommand,
    ConversationContextResolution,
    ConversationService,
)
from code_assist_v1.knowledge_core.repositories.retrieval import RetrievalQueryError
from code_assist_v1.knowledge_core.legacy import read_compatibility as read_compatibility_module
from code_assist_v1.knowledge_core.legacy.read_compatibility import (
    LegacyFindingReadRequest,
    LegacyGraphReadRequest,
    LegacyKnowledgeReadAdapter,
    LegacyPersonalReadRequest,
    LegacyReadIntegrityError,
    LegacyReadScopeError,
    LegacyReadStateConflictError,
    LegacySearchReadRequest,
)
from code_assist_v1.test_safety import create_isolated_test_layout


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("2cf9d3f9-184d-4bba-94df-4f0f8fdbbf69")


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(tmp_path: Path) -> ConnectionFactory:
    layout = create_isolated_test_layout(tmp_path, user_id="fixture-user")
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p3.3-canonical-retrieval-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _database_row_counts(factory: ConnectionFactory) -> dict[str, int]:
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            names = tuple(
                str(row["name"])
                for row in connection.execute(
                    """SELECT name FROM sqlite_schema
                         WHERE type='table' AND name NOT LIKE 'sqlite_%'
                         ORDER BY name"""
                ).fetchall()
            )
            result: dict[str, int] = {}
            for name in names:
                quoted = name.replace('"', '""')
                result[name] = int(
                    connection.execute(
                        f'SELECT count(*) AS count FROM "{quoted}"'
                    ).fetchone()["count"]
                )
            return result
    finally:
        connection.close()


def _user(user_id: str, *, role: str = "user") -> LegacyUserRecord:
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.title(),
        password_hash=f"scrypt:p3.3:{user_id}",
        system_role=role,  # type: ignore[arg-type]
        account_status="active",
        created_at_ms=100,
        updated_at_ms=100,
    )


def _flags() -> FeatureFlags:
    return FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        personal_writes=False,
        part_reads=True,
        part_writes=False,
        projections=False,
        higher_spaces=False,
    )


def _insert_item(
    connection,
    *,
    label: str,
    space_id: str,
    actor: str,
    title: str,
    body: str,
    published: bool,
    stale_search_row: bool = False,
    metadata_json: str = "{}",
    blob_backed: bool = False,
) -> tuple[str, str]:
    item_id = _uuid(f"item:{label}")
    revision_id = _uuid(f"revision:{label}")
    encoded = body.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    connection.execute(
        """INSERT INTO knowledge_item(
             id,space_id,lineage_group_id,lifecycle_status,curation_status,
             current_revision_id,last_curation_transition_id,
             latest_deactivation_episode_id,redirect_item_id,created_by,
             updated_by,created_at,updated_at,row_version
           ) VALUES (?,?,NULL,'active','provisional',?,NULL,NULL,NULL,?,?,200,200,1)""",
        (item_id, space_id, revision_id, actor, actor),
    )
    connection.execute(
        """INSERT INTO knowledge_revision(
             id,item_id,revision_no,parent_revision_id,base_revision_id,title,
             raw_content_sha256,normalized_content_sha256,content_size_bytes,
             normalizer_version,metadata_json,change_kind,change_reason,
             created_by,created_at
           ) VALUES (?,?,1,NULL,NULL,?,?,?,?, 'markdown_lf_v1',?,'initial',
                     'P3.3 fixture',?,200)""",
        (
            revision_id,
            item_id,
            title,
            digest,
            digest,
            len(encoded),
            metadata_json,
            actor,
        ),
    )
    if blob_backed:
        connection.execute(
            """INSERT INTO blob_object(
                 sha256,size_bytes,media_type,relative_path,verification_status,
                 created_at,verified_at
               ) VALUES (?,?,'application/octet-stream',?,'verified',200,200)
               ON CONFLICT(sha256) DO NOTHING""",
            (digest, len(encoded), f"{digest[:2]}/{digest[2:4]}/{digest}"),
        )
    connection.execute(
        """INSERT INTO knowledge_revision_body(
             revision_id,item_id,content_md,content_blob_hash,
             content_size_bytes,content_sha256,created_at
           ) VALUES (?,?,?,?,?,?,200)""",
        (revision_id, item_id, None if blob_backed else body,
         digest if blob_backed else None, len(encoded), digest),
    )
    if published:
        _publish(connection, label, item_id, revision_id, space_id, actor, title, body)
    elif stale_search_row:
        # Deliberately inconsistent stale projection evidence proves the read query
        # rechecks canonical lifecycle/curation rather than trusting FTS membership.
        connection.execute(
            """INSERT INTO search_document(
                 item_id,space_id,revision_id,title,content_md,updated_at
               ) VALUES (?,?,?,?,?,300)""",
            (item_id, space_id, revision_id, title, body),
        )
    return item_id, revision_id


def _publish(
    connection,
    label: str,
    item_id: str,
    revision_id: str,
    space_id: str,
    actor: str,
    title: str,
    body: str,
) -> None:
    item = connection.execute(
        "SELECT row_version,updated_at FROM knowledge_item WHERE id=?", (item_id,)
    ).fetchone()
    result_row_version = int(item["row_version"]) + 1
    updated_at = max(330, int(item["updated_at"]) + 1)
    finding_id = _uuid(f"finding:{label}")
    dedup_id = _uuid(f"dedup:{label}")
    operation_id = _uuid(f"operation:{label}")
    transition_id = _uuid(f"transition:{label}")
    connection.execute(
        """INSERT INTO operation_dedup(
             id,actor_user_id,operation_type,operation_id,request_sha256,status,
             http_status,result_json,error_json,created_at,completed_at,last_seen_at
           ) VALUES (?,?,'item_curation',?,?,'in_progress',NULL,NULL,NULL,300,NULL,300)""",
        (dedup_id, actor, operation_id, "a" * 64),
    )
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
             'space_editor','agent_hidden','publish',?,?,NULL,NULL,'p3.3','v1','r1',
             NULL,NULL,NULL,'{}','{}',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
             'user',?,310,310,1)""",
        (finding_id, space_id, item_id, revision_id, actor),
    )
    connection.execute(
        """UPDATE curation_finding SET status='approved',reviewed_by=?,reviewed_at=320,
             review_reason='approved',updated_at=320,row_version=2 WHERE id=?""",
        (actor, finding_id),
    )
    connection.execute(
        """UPDATE curation_finding SET status='applied',applied_operation_id=?,
             applied_dedup_id=?,applied_by=?,applied_at=330,updated_at=330,
             row_version=3 WHERE id=?""",
        (operation_id, dedup_id, actor, finding_id),
    )
    connection.execute(
        """INSERT INTO item_curation_transition(
             id,item_id,space_id,transition_kind,from_status,to_status,
             base_revision_id,result_revision_id,finding_id,rescan_job_id,
             decision_job_id,dedup_id,operation_id,actor_kind,actor_user_id,
             result_item_row_version,reason,created_at
           ) VALUES (?,?,?,'finding_decision','provisional','published',?,?,?,NULL,
             NULL,?,?,'user',?,?,'publish',?)""",
        (
            transition_id,
            item_id,
            space_id,
            revision_id,
            revision_id,
            finding_id,
            dedup_id,
            operation_id,
            actor,
            result_row_version,
            updated_at,
        ),
    )
    connection.execute(
        """UPDATE knowledge_item SET curation_status='published',
             last_curation_transition_id=?,updated_by=?,updated_at=?,row_version=?
             WHERE id=?""",
        (transition_id, actor, updated_at, result_row_version, item_id),
    )
    connection.execute(
        """INSERT INTO search_document(
             item_id,space_id,revision_id,title,content_md,updated_at
           ) VALUES (?,?,?,?,?,330)""",
        (item_id, space_id, revision_id, title, body),
    )


def _audit(connection, *, label: str, space_id: str, item_id: str, actor: str) -> None:
    connection.execute(
        """INSERT INTO audit_event(
             event_uuid,occurred_at,request_id,operation_id,action,outcome,
             actor_kind,actor_user_id,actor_display_name,actor_role_snapshot,
             space_id,item_id,base_revision_id,new_revision_id,reason,
             details_json,request_meta_json,schema_version
           ) VALUES (?,500,?,NULL,'item.fixture','recorded','user',?,?,?, ?,?,
                     NULL,NULL,NULL,'{}','{}',1)""",
        (
            _uuid(f"audit:{label}"),
            _uuid(f"request:{label}"),
            actor,
            actor.title(),
            "admin" if actor == "admin" else "user",
            space_id,
            item_id,
        ),
    )


def _seed(factory: ConnectionFactory) -> dict[str, str]:
    ids = {
        name: _uuid(name)
        for name in (
            "team-a",
            "team-b",
            "part-a",
            "part-b",
            "part-space-a",
            "part-space-b",
            "personal-admin",
            "personal-other",
            "conversation-part-a",
            "conversation-personal",
        )
    }
    connection = factory.open()
    try:
        with transaction(connection):
            users = UserRepository()
            org = OrganizationRepository()
            spaces = SpaceRepository()
            users.import_legacy_many(connection, (_user("admin", role="admin"), _user("other")))
            for team, name in (("team-a", "Team A"), ("team-b", "Team B")):
                org.create_unit(
                    connection,
                    unit_id=ids[team],
                    kind="team",
                    parent_id=RESPONSIBILITY_ROOT_ID,
                    name=name,
                    actor_user_id="admin",
                    now_ms=110,
                )
            for part, team, name in (
                ("part-a", "team-a", "Part A"),
                ("part-b", "team-b", "Part B"),
            ):
                org.create_unit(
                    connection,
                    unit_id=ids[part],
                    kind="part",
                    parent_id=ids[team],
                    name=name,
                    actor_user_id="admin",
                    now_ms=120,
                )
            for user_id, part in (("admin", "part-a"), ("other", "part-b")):
                org.open_membership(
                    connection,
                    membership_id=_uuid(f"membership:{user_id}:initial"),
                    user_id=user_id,
                    part_id=ids[part],
                    assigned_by="admin",
                    reason="P3.3 fixture",
                    valid_from_ms=130,
                )
                state = org.get_user_organization_state(connection, user_id)
                assert state is not None
                org.compare_and_swap_user_part(
                    connection,
                    user_id=user_id,
                    expected_current_part_id=None,
                    target_part_id=ids[part],
                    expected_scope_epoch=state.scope_epoch,
                    expected_row_version=state.row_version,
                    updated_at_ms=130,
                )
            org.open_part_leader(
                connection,
                assignment_id=_uuid("leader:part-a"),
                user_id="admin",
                part_id=ids["part-a"],
                assigned_by="admin",
                reason="P3.3 fixture",
                valid_from_ms=140,
            )
            org.open_part_leader(
                connection,
                assignment_id=_uuid("leader:part-b"),
                user_id="other",
                part_id=ids["part-b"],
                assigned_by="admin",
                reason="P3.3 fixture",
                valid_from_ms=140,
            )
            spaces.ensure_personal(
                connection,
                space_id=ids["personal-admin"],
                user_id="admin",
                created_at_ms=150,
            )
            spaces.ensure_personal(
                connection,
                space_id=ids["personal-other"],
                user_id="other",
                created_at_ms=150,
            )
            spaces.ensure_part(
                connection,
                space_id=ids["part-space-a"],
                part_id=ids["part-a"],
                created_at_ms=150,
            )
            spaces.ensure_part(
                connection,
                space_id=ids["part-space-b"],
                part_id=ids["part-b"],
                created_at_ms=150,
            )
            ids["part-hit"], ids["part-hit-revision"] = _insert_item(
                connection,
                label="part-hit",
                space_id=ids["part-space-a"],
                actor="admin",
                title="Scope alpha shared",
                body="# scopealpha shared source\n",
                published=True,
            )
            ids["part-neighbor"], ids["part-neighbor-revision"] = _insert_item(
                connection,
                label="part-neighbor",
                space_id=ids["part-space-a"],
                actor="admin",
                title="Semantic neighbor",
                body="# neighborbeta supporting source\n",
                published=True,
            )
            ids["part-provisional"], _ = _insert_item(
                connection,
                label="part-provisional",
                space_id=ids["part-space-a"],
                actor="admin",
                title="Scope alpha provisional leak",
                body="# scopealpha provisional leak\n",
                published=False,
                stale_search_row=True,
            )
            ids["foreign-hit"], _ = _insert_item(
                connection,
                label="foreign-hit",
                space_id=ids["part-space-b"],
                actor="other",
                title="Scope alpha foreign",
                body="# scopealpha foreign canary\n",
                published=True,
            )
            ids["personal-hit"], ids["personal-hit-revision"] = _insert_item(
                connection,
                label="personal-hit",
                space_id=ids["personal-admin"],
                actor="admin",
                title="Scope alpha private",
                body="# scopealpha private canary\n",
                published=True,
            )
            connection.execute(
                """INSERT INTO knowledge_edge(
                     id,space_id,source_item_id,target_item_id,edge_type,edge_status,
                     origin,source_evidence_revision_id,target_evidence_revision_id,
                     approval_finding_id,evidence_json,needs_revalidation,created_by,
                     updated_by,created_at,updated_at,row_version
                   ) VALUES (?, ?, ?, ?, 'supports','active','manual',NULL,NULL,NULL,
                             '{}',0,'admin','admin',450,450,1)""",
                (
                    _uuid("edge:part-hit-neighbor"),
                    ids["part-space-a"],
                    ids["part-hit"],
                    ids["part-neighbor"],
                ),
            )
            _audit(connection, label="hit", space_id=ids["part-space-a"], item_id=ids["part-hit"], actor="admin")
            _audit(connection, label="neighbor", space_id=ids["part-space-a"], item_id=ids["part-neighbor"], actor="admin")
            _audit(connection, label="foreign", space_id=ids["part-space-b"], item_id=ids["foreign-hit"], actor="other")
            connection.execute(
                """UPDATE knowledge_space SET generation=3,updated_at=490,
                     row_version=row_version+1 WHERE id=?""",
                (ids["part-space-a"],),
            )
            connection.execute(
                """INSERT INTO projection_state(
                     projection_name,space_id,desired_generation,built_generation,
                     checksum_sha256,status,last_error_json,updated_at,built_at,row_version
                   ) VALUES ('markdown',?,3,1,NULL,'building',NULL,490,NULL,1)""",
                (ids["part-space-a"],),
            )
            epoch = users.require(connection, "admin").scope_epoch
            ids["admin-epoch"] = str(epoch)
            conversations = ConversationRepository()
            conversations.create(
                connection,
                ConversationCreate(
                    conversation_id=ids["conversation-part-a"],
                    owner_user_id="admin",
                    space_id=ids["part-space-a"],
                    captured_scope_epoch=epoch,
                    title="Part retrieval",
                    metadata_json="{}",
                    created_at_ms=600,
                ),
            )
            conversations.create(
                connection,
                ConversationCreate(
                    conversation_id=ids["conversation-personal"],
                    owner_user_id="admin",
                    space_id=ids["personal-admin"],
                    captured_scope_epoch=epoch,
                    title="Personal retrieval",
                    metadata_json="{}",
                    created_at_ms=600,
                ),
            )
    finally:
        connection.close()
    return ids


def _service(factory: ConnectionFactory) -> CanonicalRetrievalService:
    return CanonicalRetrievalService(factory, scope_resolver=ScopeResolver(_flags()))


@pytest.mark.scenario("CONV-001")
@pytest.mark.scenario("CONV-004")
@pytest.mark.scenario("CONV-009")
@pytest.mark.scenario("SPACE-006")
@pytest.mark.scenario("CUR-003")
@pytest.mark.scenario("PROJ-005")
def test_conversation_fts_is_literal_current_visible_and_exactly_one_space(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    result = _service(factory).retrieve_for_conversation(
        ConversationRetrievalRequest(
            actor_user_id="admin",
            actor_scope_epoch=int(ids["admin-epoch"]),
            conversation_id=ids["conversation-part-a"],
            query='scopealpha) OR foreign* "',
        )
    )
    assert result.conversation_id == ids["conversation-part-a"]
    assert result.space_id == ids["part-space-a"]
    assert [hit.item_id for hit in result.hits] == [ids["part-hit"]]
    assert all(hit.space_id == ids["part-space-a"] for hit in result.hits)
    assert ids["part-provisional"] not in {hit.item_id for hit in result.hits}
    assert ids["foreign-hit"] not in {hit.item_id for hit in result.hits}
    assert ids["personal-hit"] not in {hit.item_id for hit in result.hits}
    assert result.retrieval_source == "canonical_fts"
    assert result.trust_boundary == "untrusted_reference"
    assert result.hits[0].trust_boundary == "untrusted_reference"
    assert result.projection.status == "building"
    assert result.projection.canonical_generation == 3
    assert result.projection.built_generation == 1
    assert result.projection.canonical_fallback_used is True


def test_semantic_neighborhood_and_recent_events_are_relevant_bounded_and_same_space(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    result = _service(factory).retrieve_for_conversation(
        ConversationRetrievalRequest(
            actor_user_id="admin",
            actor_scope_epoch=int(ids["admin-epoch"]),
            conversation_id=ids["conversation-part-a"],
            query="scopealpha",
            neighbor_limit=1,
            event_limit=2,
        )
    )
    assert len(result.semantic_neighbors) == 1
    neighbor = result.semantic_neighbors[0]
    assert neighbor.seed_item_id == ids["part-hit"]
    assert neighbor.neighbor_item_id == ids["part-neighbor"]
    assert neighbor.direction == "outgoing"
    assert neighbor.edge_type == "supports"
    assert neighbor.trust_boundary == "untrusted_reference"
    assert {event.item_id for event in result.recent_events} == {
        ids["part-hit"],
        ids["part-neighbor"],
    }
    assert ids["foreign-hit"] not in {event.item_id for event in result.recent_events}


def test_management_search_authorizes_one_requested_scope_and_never_admin_all_spaces(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    service = _service(factory)
    personal = service.retrieve_for_space(
        SpaceRetrievalRequest(
            actor_user_id="admin",
            actor_scope_epoch=int(ids["admin-epoch"]),
            space_id=ids["personal-admin"],
            query="scopealpha",
        )
    )
    assert personal.conversation_id is None
    assert [hit.item_id for hit in personal.hits] == [ids["personal-hit"]]
    assert personal.projection.status == "missing"
    assert personal.projection.canonical_fallback_used is True
    connection = factory.open()
    try:
        other_epoch = UserRepository().require(connection, "other").scope_epoch
    finally:
        connection.close()
    with pytest.raises(ScopeNotFoundError):
        service.retrieve_for_space(
            SpaceRetrievalRequest(
                actor_user_id="other",
                actor_scope_epoch=other_epoch,
                space_id=ids["part-space-a"],
                query="scopealpha",
            )
        )


@pytest.mark.scenario("CONV-005")
def test_part_move_and_archive_fail_closed_before_retrieval(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    service = _service(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            conversation = ConversationRepository().require_owned(
                connection,
                conversation_id=ids["conversation-personal"],
                owner_user_id="admin",
            )
            ConversationRepository().update_lifecycle(
                connection,
                conversation_id=conversation.id,
                owner_user_id="admin",
                expected_row_version=conversation.row_version,
                updated_at_ms=700,
                status="archived",
            )
        with pytest.raises(RetrievalStateConflictError):
            service.retrieve_for_conversation(
                ConversationRetrievalRequest(
                    actor_user_id="admin",
                    actor_scope_epoch=int(ids["admin-epoch"]),
                    conversation_id=ids["conversation-personal"],
                    query="scopealpha",
                )
            )
        with transaction(connection):
            org = OrganizationRepository()
            current = org.get_current_membership(connection, "admin")
            state = org.get_user_organization_state(connection, "admin")
            assert current is not None and state is not None
            leader = org.get_active_part_leader(connection, ids["part-a"])
            assert leader is not None and leader.user_id == "admin"
            org.close_part_leader(
                connection,
                part_id=ids["part-a"],
                expected_assignment_id=leader.id,
                closed_at_ms=800,
            )
            org.close_current_membership(
                connection,
                user_id="admin",
                expected_membership_id=current.id,
                closed_at_ms=800,
            )
            org.open_membership(
                connection,
                membership_id=_uuid("membership:admin:moved"),
                user_id="admin",
                part_id=ids["part-b"],
                assigned_by="admin",
                reason="P3.3 stale-scope proof",
                valid_from_ms=800,
            )
            org.compare_and_swap_user_part(
                connection,
                user_id="admin",
                expected_current_part_id=ids["part-a"],
                target_part_id=ids["part-b"],
                expected_scope_epoch=state.scope_epoch,
                expected_row_version=state.row_version,
                updated_at_ms=800,
            )
    finally:
        connection.close()
    with pytest.raises(RetrievalScopeStaleError):
        service.retrieve_for_conversation(
            ConversationRetrievalRequest(
                actor_user_id="admin",
                actor_scope_epoch=int(ids["admin-epoch"]),
                conversation_id=ids["conversation-part-a"],
                query="scopealpha",
            )
        )


def test_query_limits_and_controls_fail_before_fts_execution(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    service = _service(factory)
    base = dict(
        actor_user_id="admin",
        actor_scope_epoch=int(ids["admin-epoch"]),
        space_id=ids["part-space-a"],
    )
    with pytest.raises(RetrievalQueryError):
        service.retrieve_for_space(SpaceRetrievalRequest(**base, query="alpha\x00beta"))
    with pytest.raises(RetrievalQueryError):
        service.retrieve_for_space(
            SpaceRetrievalRequest(**base, query="scopealpha", top_k=21)
        )
    with pytest.raises(RetrievalQueryError):
        service.retrieve_for_space(SpaceRetrievalRequest(**base, query="***"))


def test_stale_fts_body_is_not_returned_as_canonical_content(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """UPDATE search_document SET content_md='scopealpha tampered projection',
                     updated_at=900 WHERE item_id=?""",
                (ids["part-hit"],),
            )
    finally:
        connection.close()
    result = _service(factory).retrieve_for_space(
        SpaceRetrievalRequest(
            actor_user_id="admin",
            actor_scope_epoch=int(ids["admin-epoch"]),
            space_id=ids["part-space-a"],
            query="scopealpha",
        )
    )
    assert result.hits == ()
    assert result.semantic_neighbors == ()
    assert result.recent_events == ()


class _BindingCreationResolver:
    def __init__(
        self,
        space_id: str,
        *,
        source_artifact_id: str | None = None,
        snapshot_sha256: str | None = None,
        space_by_reference: dict[str, str] | None = None,
    ) -> None:
        self.space_id = space_id
        self.source_artifact_id = source_artifact_id
        self.snapshot_sha256 = snapshot_sha256
        self.space_by_reference = dict(space_by_reference or {})

    def resolve(self, request):
        return ConversationContextResolution(
            space_id=self.space_by_reference.get(request.external_key, self.space_id),
            binding_type=request.binding_type,
            binding_mode=request.binding_mode,
            external_key=request.external_key,
            source_artifact_id=self.source_artifact_id,
            snapshot_sha256=self.snapshot_sha256,
            display_name="Explicit part project",
        )


class _TurnBindingResolver:
    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        content: str = "# explicit project context\n",
        availability: str = "available",
        reason: str | None = None,
        mutate=None,
        wrong_space: str | None = None,
        wrong_hash: bool = False,
    ) -> None:
        self.factory = factory
        self.content = content
        self.availability = availability
        self.reason = reason
        self.mutate = mutate
        self.wrong_space = wrong_space
        self.wrong_hash = wrong_hash
        self.calls = []

    def resolve(self, request):
        probe = self.factory.open()
        try:
            assert probe.in_transaction is False
        finally:
            probe.close()
        self.calls.append(request)
        if self.mutate is not None:
            mutate, self.mutate = self.mutate, None
            mutate()
        if self.availability == "unavailable":
            return AgentBoundContextResolution(
                conversation_id=request.conversation_id,
                space_id=request.space_id,
                binding_id=request.binding_id,
                binding_row_version=request.binding_row_version,
                binding_type=request.binding_type,
                binding_mode=request.binding_mode,
                external_key=request.external_key,
                availability="unavailable",
                content_md=None,
                content_sha256=None,
                evidence_sha256=None,
                unavailable_reason=self.reason,
            )
        digest = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        return AgentBoundContextResolution(
            conversation_id=request.conversation_id,
            space_id=self.wrong_space or request.space_id,
            binding_id=request.binding_id,
            binding_row_version=request.binding_row_version,
            binding_type=request.binding_type,
            binding_mode=request.binding_mode,
            external_key=request.external_key,
            availability="available",
            content_md=self.content,
            content_sha256="f" * 64 if self.wrong_hash else digest,
            evidence_sha256=digest,
            unavailable_reason=None,
        )


def _accepted_turn(factory: ConnectionFactory, ids: dict[str, str]):
    return ConversationService(factory).accept_turn(
        AcceptConversationTurnCommand(
            actor_user_id="admin",
            actor_scope_epoch=int(ids["admin-epoch"]),
            operation_id=_uuid("p3.4-turn-operation"),
            request_id=_uuid("p3.4-turn-request"),
            conversation_id=ids["conversation-part-a"],
            user_content={"text": "scopealpha"},
        )
    )


def _add_live_binding(factory: ConnectionFactory, ids: dict[str, str]):
    return ConversationService(
        factory,
        context_resolver=_BindingCreationResolver(ids["part-space-a"]),
    ).add_context(
        AddConversationContextCommand(
            actor_user_id="admin",
            actor_scope_epoch=int(ids["admin-epoch"]),
            operation_id=_uuid("p3.4-context-operation"),
            request_id=_uuid("p3.4-context-request"),
            conversation_id=ids["conversation-part-a"],
            binding_type="project_context",
            binding_mode="live",
            external_key="project:explicit-part",
        )
    ).binding


def _add_snapshot_binding(
    factory: ConnectionFactory,
    ids: dict[str, str],
    *,
    content: str,
):
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    artifact_id = _uuid("p3.4-snapshot-artifact")
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO source_artifact(
                     id,origin_type,origin_key,origin_revision_key,raw_sha256,
                     raw_size_bytes,media_type,original_filename,
                     body_retention_status,body_retention_reason,body_purged_at,
                     orphan_cleanup_eligible_at,publisher_user_id,
                     source_owner_snapshot_json,metadata_json,
                     metadata_schema_version,created_at
                   ) VALUES (?,'manual','p3.4:snapshot','v1',?,?,
                             'text/markdown','snapshot.md','present',NULL,NULL,
                             2592000700,'admin','{}','{}',1,700)""",
                (artifact_id, digest, len(content.encode("utf-8"))),
            )
    finally:
        connection.close()
    binding = ConversationService(
        factory,
        context_resolver=_BindingCreationResolver(
            ids["part-space-a"],
            source_artifact_id=artifact_id,
            snapshot_sha256=digest,
        ),
    ).add_context(
        AddConversationContextCommand(
            actor_user_id="admin",
            actor_scope_epoch=int(ids["admin-epoch"]),
            operation_id=_uuid("p3.4-snapshot-context-operation"),
            request_id=_uuid("p3.4-snapshot-context-request"),
            conversation_id=ids["conversation-part-a"],
            binding_type="workspace_file",
            binding_mode="snapshot",
            external_key="workspace:explicit-snapshot",
        )
    ).binding
    return binding, digest


def _add_pin(factory: ConnectionFactory, ids: dict[str, str]):
    return ConversationService(factory).add_pin(
        AddConversationPinCommand(
            actor_user_id="admin",
            actor_scope_epoch=int(ids["admin-epoch"]),
            operation_id=_uuid("p3.4-pin-operation"),
            request_id=_uuid("p3.4-pin-request"),
            conversation_id=ids["conversation-part-a"],
            item_id=ids["part-hit"],
            revision_id=ids["part-hit-revision"],
        )
    ).pin


def _context_request(ids: dict[str, str], turn) -> AgentTurnContextRequest:
    return AgentTurnContextRequest(
        actor_user_id="admin",
        actor_scope_epoch=int(ids["admin-epoch"]),
        conversation_id=ids["conversation-part-a"],
        turn_id=turn.turn_id,
    )


@pytest.mark.scenario("CONV-004")
@pytest.mark.scenario("CONV-007")
@pytest.mark.scenario("CONV-008")
@pytest.mark.scenario("CONV-009")
@pytest.mark.scenario("CONV-010")
def test_agent_turn_context_is_one_conversation_derived_call_with_no_implicit_private_inputs(
    tmp_path: Path,
) -> None:
    assert {field.name for field in fields(AgentTurnContextRequest)} == {
        "actor_user_id",
        "actor_scope_epoch",
        "conversation_id",
        "turn_id",
        "top_k",
        "neighbor_limit",
            "event_limit",
            "pin_limit",
            "as_of_ms",
        }
    factory = _factory(tmp_path)
    ids = _seed(factory)
    turn = _accepted_turn(factory, ids)
    _add_pin(factory, ids)
    binding = _add_live_binding(factory, ids)
    resolver = _TurnBindingResolver(factory)
    bundle = AgentTurnContextService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
        binding_resolver=resolver,
    ).compose(_context_request(ids, turn))
    assert bundle.space_id == ids["part-space-a"]
    assert bundle.turn_id == turn.turn_id
    assert bundle.operation_id == turn.operation_id
    assert bundle.retrieval_call_count == 1
    assert bundle.legacy_knowledge_queried is False
    assert bundle.legacy_second_brain_queried is False
    assert bundle.private_memory_injected is False
    assert bundle.default_project_context_injected is False
    assert [hit.item_id for hit in bundle.retrieval.hits] == [ids["part-hit"]]
    assert ids["personal-hit"] not in {hit.item_id for hit in bundle.retrieval.hits}
    assert ids["foreign-hit"] not in {hit.item_id for hit in bundle.retrieval.hits}
    assert len(bundle.pins) == 1 and bundle.pins[0].availability == "available"
    assert bundle.pins[0].item_id == ids["part-hit"]
    assert len(bundle.explicit_contexts) == 1
    assert bundle.explicit_contexts[0].binding_id == binding.id
    assert bundle.explicit_contexts[0].availability == "available"
    assert bundle.explicit_contexts[0].trust_boundary == "untrusted_reference"
    assert len(resolver.calls) == 1


class _P37CanaryExecutor:
    def __init__(self) -> None:
        self.bundles: list[object] = []

    def complete(self, bundle: object) -> AgentTurnCompletion:
        self.bundles.append(bundle)
        assert bundle.space_id
        assert bundle.retrieval_call_count == 1
        assert bundle.legacy_knowledge_queried is False
        assert bundle.legacy_second_brain_queried is False
        assert bundle.private_memory_injected is False
        assert bundle.default_project_context_injected is False
        return AgentTurnCompletion(
            assistant_content={"text": "part-only canonical answer"},
            final_response={
                "finish_reason": "stop",
                "retrieved_item_ids": [hit.item_id for hit in bundle.retrieval.hits],
            },
        )


class _P37CanarySummarizer:
    def summarize(self, **_: object) -> CompactSummaryCompletion:
        return CompactSummaryCompletion(
            summary_md="# canonical canary summary\n",
            model_id="test-model",
            prompt_version="p3.7-test",
            summarizer_version="p3.7-test",
        )


@pytest.mark.scenario("SPACE-006")
@pytest.mark.scenario("CONV-004")
@pytest.mark.scenario("CONV-005")
@pytest.mark.scenario("CONV-006")
@pytest.mark.scenario("CONV-007")
@pytest.mark.scenario("CONV-008")
@pytest.mark.scenario("CONV-009")
@pytest.mark.scenario("CONV-010")
@pytest.mark.scenario("CONV-012")
@pytest.mark.scenario("CONV-013")
def test_p37_api_agent_canary_never_crosses_space_or_queries_parallel_legacy(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    epoch = int(ids["admin-epoch"])
    principal = {"id": "admin", "scope_epoch": epoch}
    scopes = ScopeResolver(_flags())
    executor = _P37CanaryExecutor()
    conversations = ConversationService(
        factory,
        context_resolver=_BindingCreationResolver(
            ids["part-space-a"],
            space_by_reference={
                "project:cross-space-context": ids["personal-admin"],
            },
        ),
    )
    app = Flask("p37-api-agent-canary", static_folder=None)
    register_canonical_routes(
        app,
        scope_selection_service=ScopeSelectionService(
            factory,
            scope_resolver=scopes,
        ),
        conversation_service=conversations,
        retrieval_service=CanonicalRetrievalService(
            factory,
            scope_resolver=scopes,
        ),
        management_read_service=CanonicalManagementReadService(
            factory,
            scope_resolver=scopes,
        ),
        agent_context_service=AgentTurnContextService(
            factory,
            scope_resolver=scopes,
            binding_resolver=_TurnBindingResolver(factory),
        ),
        turn_executor=executor,
        conversation_summarizer=_P37CanarySummarizer(),
        principal_resolver=lambda: principal,
    )
    client = app.test_client()

    principal["id"] = "Admin"
    assert client.get("/api/knowledge/v1/spaces").status_code == 401
    principal["id"] = "admin"
    read_counts_before = _database_row_counts(factory)

    part_search = client.post(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/search",
        json={"query": "scopealpha", "limit": 10},
    )
    assert part_search.status_code == 200
    assert [item["item_id"] for item in part_search.json["items"]] == [
        ids["part-hit"]
    ]
    assert ids["personal-hit"] not in {
        item["item_id"] for item in part_search.json["items"]
    }
    assert ids["foreign-hit"] not in {
        item["item_id"] for item in part_search.json["items"]
    }

    personal_search = client.post(
        f"/api/knowledge/v1/spaces/{ids['personal-admin']}/search",
        json={"query": "scopealpha", "limit": 10},
    )
    assert personal_search.status_code == 200
    assert [item["item_id"] for item in personal_search.json["items"]] == [
        ids["personal-hit"]
    ]

    part_items = client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/items?limit=1"
    )
    assert part_items.status_code == 200
    assert part_items.json["has_more"] is True
    assert isinstance(part_items.json["next_cursor"], str)
    remaining_part_items = client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/items",
        query_string={"cursor": part_items.json["next_cursor"], "limit": 10},
    )
    assert remaining_part_items.status_code == 200
    part_item_ids = {
        item["id"]
        for page in (part_items, remaining_part_items)
        for item in page.json["items"]
    }
    assert part_item_ids == {ids["part-hit"], ids["part-neighbor"]}
    assert ids["personal-hit"] not in part_item_ids
    assert ids["foreign-hit"] not in part_item_ids
    personal_items = client.get(
        f"/api/knowledge/v1/spaces/{ids['personal-admin']}/items"
    )
    assert personal_items.status_code == 200
    assert [item["id"] for item in personal_items.json["items"]] == [
        ids["personal-hit"]
    ]

    item_detail = client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/items/{ids['part-hit']}"
    )
    assert item_detail.status_code == 200, item_detail.json
    assert item_detail.json["item"]["current_revision_id"] == ids["part-hit-revision"]
    assert item_detail.json["markdown"] == "# scopealpha shared source\n"
    assert item_detail.json["timeline"]["content_age_seconds"] >= 0
    assert item_detail.json["timeline"]["review_age_seconds"] >= 0
    assert item_detail.json["lifecycle"]["deactivation"] is None
    assert item_detail.json["lifecycle"]["edge_counts"]["active_outgoing"] == 1
    assert item_detail.json["lease"] is None
    assert "read" in item_detail.json["capabilities"]

    revisions = client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/items/{ids['part-hit']}/revisions"
    )
    assert revisions.status_code == 200, revisions.json
    assert revisions.json["has_more"] is False
    assert revisions.json["next_cursor"] is None
    assert [row["id"] for row in revisions.json["items"]] == [ids["part-hit-revision"]]
    revision = revisions.json["items"][0]
    assert revision["created_by"] == "admin"
    assert revision["created_by_display"] == "Admin"
    assert revision["reason"] == "P3.3 fixture"
    assert "markdown" not in revision

    revision_detail = client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/items/{ids['part-hit']}/revisions/{ids['part-hit-revision']}"
    )
    assert revision_detail.status_code == 200, revision_detail.json
    assert revision_detail.json["revision"]["markdown"] == "# scopealpha shared source\n"
    comparison = client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/items/{ids['part-hit']}/compare",
        query_string={"from": ids["part-hit-revision"], "to": ids["part-hit-revision"]},
    )
    assert comparison.status_code == 200, comparison.json
    assert comparison.json["hunks"] == []
    assert re.fullmatch(r"[0-9a-f]{64}", comparison.json["diff_sha256"])
    assert client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/items/{ids['part-hit']}/compare",
        query_string={"from": ids["part-hit-revision"]},
    ).status_code == 400
    assert client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/items/{ids['part-hit']}/compare",
        query_string={
            "from": ids["part-hit-revision"],
            "to": ids["part-hit-revision"],
            "user_id": "other",
        },
    ).status_code == 400
    assert client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/items/{ids['part-hit']}/revisions",
        query_string={"cursor": part_items.json["next_cursor"]},
    ).status_code == 400

    cross_space_item = client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/items/{ids['foreign-hit']}"
    )
    assert cross_space_item.status_code in {404, 409}

    graph = client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/graph"
    )
    assert graph.status_code == 200
    assert graph.json["space_id"] == ids["part-space-a"]
    assert graph.json["generation"] == 3
    assert {node["id"] for node in graph.json["nodes"]} == {
        ids["part-hit"],
        ids["part-neighbor"],
    }
    assert len(graph.json["edges"]) == 1
    assert {
        graph.json["edges"][0]["source_item_id"],
        graph.json["edges"][0]["target_item_id"],
    } == {ids["part-hit"], ids["part-neighbor"]}
    assert ids["personal-hit"] not in {
        node["id"] for node in graph.json["nodes"]
    }
    assert ids["foreign-hit"] not in {
        node["id"] for node in graph.json["nodes"]
    }

    projection = client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/projection-status"
    )
    assert projection.status_code == 200
    assert projection.json == {
        "built_at": None,
        "built_generation": 1,
        "checksum_sha256": None,
        "desired_generation": 3,
        "lag_generations": 2,
        "last_error": None,
        "projection_name": "markdown",
        "request_id": projection.json["request_id"],
        "space_id": ids["part-space-a"],
        "status": "building",
        "updated_at": "1970-01-01T00:00:00.490Z",
    }

    findings = client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/curation/findings?limit=1"
    )
    assert findings.status_code == 200
    assert findings.json["has_more"] is True
    remaining_findings = client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/curation/findings",
        query_string={"cursor": findings.json["next_cursor"], "limit": 10},
    )
    assert remaining_findings.status_code == 200
    finding_sources = {
        finding["source_item_id"]
        for page in (findings, remaining_findings)
        for finding in page.json["items"]
    }
    assert finding_sources == {ids["part-hit"], ids["part-neighbor"]}
    assert ids["personal-hit"] not in finding_sources
    assert ids["foreign-hit"] not in finding_sources
    wrong_cursor_kind = client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/curation/findings",
        query_string={"cursor": part_items.json["next_cursor"]},
    )
    assert wrong_cursor_kind.status_code == 400

    activity = client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/activity?limit=1"
    )
    assert activity.status_code == 200
    assert activity.json["has_more"] is True
    assert isinstance(activity.json["next_cursor"], str)
    remaining_activity = client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/activity",
        query_string={"cursor": activity.json["next_cursor"], "limit": 10},
    )
    assert remaining_activity.status_code == 200
    activity_items = [
        event
        for page in (activity, remaining_activity)
        for event in page.json["items"]
    ]
    assert {event["item_id"] for event in activity_items} == {
        ids["part-hit"],
        ids["part-neighbor"],
    }
    assert {event["space_id"] for event in activity_items} == {
        ids["part-space-a"]
    }
    assert all(
        set(event)
            == {
                "action", "actor", "base_revision_id", "capabilities",
                "event_uuid", "inherited", "item_id", "new_revision_id",
                "occurred_at", "origin", "outcome", "overlay_grant_id", "reason",
                "selected_view_space_id", "source_space_id", "space_id",
            }
        for event in activity_items
    )
    assert ids["foreign-hit"] not in {event["item_id"] for event in activity_items}
    assert client.get(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/activity",
        query_string={"cursor": part_items.json["next_cursor"]},
    ).status_code == 400
    assert _database_row_counts(factory) == read_counts_before

    cross_space_pin = client.post(
        f"/api/knowledge/v1/conversations/{ids['conversation-part-a']}/pins",
        json={
            "operation_id": _uuid("p3.7-cross-space-pin"),
            "item_id": ids["personal-hit"],
            "revision_id": ids["personal-hit-revision"],
        },
    )
    assert cross_space_pin.status_code in {404, 409}
    assert conversations.list_pins(
        actor_user_id="admin",
        actor_scope_epoch=epoch,
        conversation_id=ids["conversation-part-a"],
    ) == ()

    cross_space_context = client.post(
        f"/api/knowledge/v1/conversations/{ids['conversation-part-a']}/context-bindings",
        json={
            "binding_mode": "live",
            "operation_id": _uuid("p3.7-cross-space-context"),
            "source_kind": "project",
            "source_reference": "project:cross-space-context",
        },
    )
    assert cross_space_context.status_code in {404, 409}
    assert conversations.list_contexts(
        actor_user_id="admin",
        actor_scope_epoch=epoch,
        conversation_id=ids["conversation-part-a"],
    ) == ()

    pin_operation = _uuid("p3.7-part-pin")
    pin = client.post(
        f"/api/knowledge/v1/conversations/{ids['conversation-part-a']}/pins",
        json={
            "operation_id": pin_operation,
            "item_id": ids["part-hit"],
            "revision_id": ids["part-hit-revision"],
        },
        headers={"Idempotency-Key": pin_operation},
    )
    assert pin.status_code == 201
    assert pin.json["resource_id"] == ids["part-hit"]

    context_operation = _uuid("p3.7-part-context")
    context = client.post(
        f"/api/knowledge/v1/conversations/{ids['conversation-part-a']}/context-bindings",
        json={
            "binding_mode": "live",
            "operation_id": context_operation,
            "source_kind": "project",
            "source_reference": "project:explicit-part-context",
        },
        headers={"Idempotency-Key": context_operation},
    )
    assert context.status_code == 201
    binding_id = context.json["resource_id"]

    operation_id = _uuid("p3.7-api-agent-turn")
    response = client.post(
        f"/api/knowledge/v1/conversations/{ids['conversation-part-a']}/turns/stream",
        json={"message": "scopealpha", "operation_id": operation_id},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    assert "event: completed" in response.text
    assert len(executor.bundles) == 1
    bundle = executor.bundles[0]
    assert bundle.space_id == ids["part-space-a"]
    assert [hit.item_id for hit in bundle.retrieval.hits] == [ids["part-hit"]]
    assert ids["personal-hit"] not in {hit.item_id for hit in bundle.retrieval.hits}
    assert ids["foreign-hit"] not in {hit.item_id for hit in bundle.retrieval.hits}
    assert len(bundle.pins) == 1
    assert bundle.pins[0].item_id == ids["part-hit"]
    assert len(bundle.explicit_contexts) == 1
    assert bundle.explicit_contexts[0].binding_id == binding_id
    assert bundle.explicit_contexts[0].availability == "available"

    compact_operation = _uuid("p3.7-api-compact")
    compacted = client.post(
        f"/api/knowledge/v1/conversations/{ids['conversation-part-a']}/compact",
        json={
            "operation_id": compact_operation,
            "through_message_sequence": 2,
        },
        headers={"Idempotency-Key": compact_operation},
    )
    assert compacted.status_code == 202
    detail = client.get(
        f"/api/knowledge/v1/conversations/{ids['conversation-part-a']}"
    )
    assert detail.status_code == 200
    assert detail.json["summary_status"] == "ready"
    assert [pin["item_id"] for pin in detail.json["pins"]] == [ids["part-hit"]]
    assert [binding["id"] for binding in detail.json["context_bindings"]] == [
        binding_id
    ]

    replay = client.post(
        f"/api/knowledge/v1/conversations/{ids['conversation-part-a']}/turns/stream",
        json={"message": "scopealpha", "operation_id": operation_id},
        headers={"Accept": "text/event-stream"},
    )
    assert replay.status_code == 200
    assert len(executor.bundles) == 1
    messages = conversations.list_messages(
        actor_user_id="admin",
        actor_scope_epoch=epoch,
        conversation_id=ids["conversation-part-a"],
    )
    assert [message.role for message in messages] == ["user", "assistant"]

    remove_pin_operation = _uuid("p3.7-remove-part-pin")
    removed_pin = client.delete(
        f"/api/knowledge/v1/conversations/{ids['conversation-part-a']}/pins/{ids['part-hit']}",
        json={"operation_id": remove_pin_operation},
        headers={"Idempotency-Key": remove_pin_operation},
    )
    assert removed_pin.status_code == 200
    remove_context_operation = _uuid("p3.7-remove-part-context")
    removed_context = client.delete(
        f"/api/knowledge/v1/conversations/{ids['conversation-part-a']}/context-bindings/{binding_id}",
        json={"operation_id": remove_context_operation},
        headers={"Idempotency-Key": remove_context_operation},
    )
    assert removed_context.status_code == 200
    detached = client.get(
        f"/api/knowledge/v1/conversations/{ids['conversation-part-a']}"
    )
    assert detached.status_code == 200
    assert detached.json["pins"] == []
    assert detached.json["context_bindings"] == []

    connection = factory.open()
    try:
        other_epoch = UserRepository().require(connection, "other").scope_epoch
    finally:
        connection.close()
    principal.update({"id": "other", "scope_epoch": other_epoch})
    foreign_search = client.post(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/search",
        json={"query": "scopealpha"},
    )
    assert foreign_search.status_code == 404
    for path in (
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/items",
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/graph",
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/activity",
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/projection-status",
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/curation/findings",
    ):
        assert client.get(path).status_code == 404
    foreign_chat = client.post(
        f"/api/knowledge/v1/conversations/{ids['conversation-part-a']}/turns/stream",
        json={"message": "scopealpha", "operation_id": _uuid("p3.7-foreign-turn")},
        headers={"Accept": "text/event-stream"},
    )
    assert foreign_chat.status_code in {404, 409}
    assert len(executor.bundles) == 1


@pytest.mark.scenario("SPACE-006")
@pytest.mark.scenario("CONV-001")
@pytest.mark.scenario("CONV-004")
@pytest.mark.scenario("CONV-008")
@pytest.mark.scenario("CONV-012")
def test_p37_real_browser_transport_reaches_canonical_flask_and_agent_only(
    tmp_path: Path,
) -> None:
    node = shutil.which("node")
    assert node is not None, "P3.7 browser/API/Agent canary requires Node.js."
    factory = _factory(tmp_path)
    ids = _seed(factory)
    scopes = ScopeResolver(_flags())
    executor = _P37CanaryExecutor()
    conversations = ConversationService(
        factory,
        context_resolver=_BindingCreationResolver(ids["part-space-a"]),
    )
    app = Flask("p37-real-browser-api-agent", static_folder=None)
    register_canonical_routes(
        app,
        scope_selection_service=ScopeSelectionService(factory, scope_resolver=scopes),
        conversation_service=conversations,
        retrieval_service=CanonicalRetrievalService(factory, scope_resolver=scopes),
        management_read_service=CanonicalManagementReadService(
            factory, scope_resolver=scopes
        ),
        agent_context_service=AgentTurnContextService(
            factory,
            scope_resolver=scopes,
            binding_resolver=_TurnBindingResolver(factory),
        ),
        turn_executor=executor,
        conversation_summarizer=_P37CanarySummarizer(),
        principal_resolver=lambda: {
            "id": "admin",
            "scope_epoch": int(ids["admin-epoch"]),
        },
    )
    server = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    transport_path = (
        Path(__file__).resolve().parents[3] / "static" / "knowledge_core_transport.js"
    )
    operation_id = _uuid("p3.7-real-browser-turn")
    source = f"""
const assert=require('node:assert/strict');
const transportModule=require({json.dumps(str(transport_path))});
const base={json.dumps(f'http://127.0.0.1:{server.server_port}')};
const calls=[];
const transport=transportModule.createTransport({{
  fetchImpl:(path,init)=>{{calls.push(path);return fetch(base+path,init);}},
  operationIdFactory:()=>{json.dumps(operation_id)},
}});
(async()=>{{
  const snapshot=await transport.readSnapshot({{
    conversationId:{json.dumps(ids['conversation-part-a'])},
    selectedSpaceId:{json.dumps(ids['part-space-a'])},
  }});
  assert.equal(snapshot.selected_space_id,{json.dumps(ids['part-space-a'])});
  assert.equal(snapshot.conversation_badge.space_id,{json.dumps(ids['part-space-a'])});
  const activity=await transport.management({json.dumps(ids['part-space-a'])},'activity');
  assert.deepEqual(new Set(activity.items.map(value=>value.space_id)),new Set([{json.dumps(ids['part-space-a'])}]));
  const turn=await transport.streamTurn({{
    conversationId:{json.dumps(ids['conversation-part-a'])},
    message:'scopealpha',operationId:{json.dumps(operation_id)},
  }});
  assert.equal(turn.assistantText,'part-only canonical answer');
  let denied=null;
  try {{await transport.management({json.dumps(ids['part-space-b'])},'graph');}}
  catch(error) {{denied={{code:error.code,status:error.status}};}}
  assert.equal(denied.status,404);
  assert.ok(calls.every(path=>path.startsWith('/api/knowledge/v1/')));
  assert.ok(calls.every(path=>!path.includes('/api/code/')));
  console.log(JSON.stringify({{activity:activity.items.length,answer:turn.assistantText,calls:calls.length,denied}}));
}})().catch(error=>{{console.error(error);process.exit(1);}});
"""
    try:
        completed = subprocess.run(
            [node, "-e", source],
            cwd=Path(__file__).resolve().parents[3],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result == {
        "activity": 2,
        "answer": "part-only canonical answer",
        "calls": 6,
        "denied": {"code": "NOT_FOUND", "status": 404},
    }
    assert len(executor.bundles) == 1
    bundle = executor.bundles[0]
    assert bundle.space_id == ids["part-space-a"]
    assert [hit.item_id for hit in bundle.retrieval.hits] == [ids["part-hit"]]
    assert bundle.legacy_knowledge_queried is False
    assert bundle.legacy_second_brain_queried is False


def test_agent_context_module_has_no_legacy_parallel_or_implicit_store_dependency() -> None:
    from code_assist_v1.knowledge_core.services import agent_context

    source = inspect.getsource(agent_context)
    for forbidden in (
        "knowledge_store",
        "second_brain_store",
        "memory_store",
        "project_context_store",
        "notion_store",
        "pinned_thoughts",
        "workspace_files",
    ):
        assert forbidden not in source


def test_p37_management_reads_are_read_only_and_have_no_legacy_dependency() -> None:
    from code_assist_v1.knowledge_core.repositories import management_reads as repository
    from code_assist_v1.knowledge_core.services import management_reads as service

    repository_source = inspect.getsource(repository)
    service_source = inspect.getsource(service)
    assert "open(read_only=True)" in service_source
    for forbidden_sql in (" INSERT ", " UPDATE ", " DELETE ", " REPLACE "):
        assert forbidden_sql not in repository_source.upper()
    for forbidden_dependency in (
        "knowledge_store",
        "second_brain_store",
        "memory_store",
        "project_context_store",
        "notion_store",
        "projection_root",
    ):
        assert forbidden_dependency not in repository_source
        assert forbidden_dependency not in service_source


def test_live_access_loss_and_recovery_are_persisted_once_with_audit_and_outbox(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    turn = _accepted_turn(factory, ids)
    binding = _add_live_binding(factory, ids)
    unavailable = _TurnBindingResolver(
        factory, availability="unavailable", reason="access_revoked"
    )
    service = AgentTurnContextService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
        binding_resolver=unavailable,
    )
    first = service.compose(_context_request(ids, turn))
    assert first.explicit_contexts[0].availability == "unavailable"
    assert first.explicit_contexts[0].content_md is None
    second = service.compose(_context_request(ids, turn))
    assert second.explicit_contexts == first.explicit_contexts
    connection = factory.open()
    try:
        row = connection.execute(
            "SELECT status,status_reason,row_version FROM conversation_context_binding WHERE id=?",
            (binding.id,),
        ).fetchone()
        assert tuple(row) == ("unavailable", "access_revoked", 2)
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE action='conversation.context.availability'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE event_type='conversation.context.availability_changed'"
        ).fetchone()[0] == 1
    finally:
        connection.close()
    recovered = AgentTurnContextService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
        binding_resolver=_TurnBindingResolver(factory, content="# recovered\n"),
    ).compose(_context_request(ids, turn))
    assert recovered.explicit_contexts[0].availability == "available"
    connection = factory.open()
    try:
        row = connection.execute(
            "SELECT status,status_reason,row_version FROM conversation_context_binding WHERE id=?",
            (binding.id,),
        ).fetchone()
        assert tuple(row) == ("active", None, 3)
        assert connection.execute(
            "SELECT count(*) FROM audit_event WHERE action='conversation.context.availability'"
        ).fetchone()[0] == 2
    finally:
        connection.close()


def test_binding_change_during_external_resolution_discards_the_resolved_content(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    turn = _accepted_turn(factory, ids)
    binding = _add_live_binding(factory, ids)

    def detach() -> None:
        connection = factory.open()
        try:
            with transaction(connection):
                connection.execute(
                    """UPDATE conversation_context_binding
                       SET status='detached',status_reason='removed_by_owner',
                           status_changed_at=999,detached_at=999,row_version=row_version+1
                       WHERE id=?""",
                    (binding.id,),
                )
        finally:
            connection.close()

    resolver = _TurnBindingResolver(factory, mutate=detach)
    with pytest.raises(AgentContextStateConflictError):
        AgentTurnContextService(
            factory,
            scope_resolver=ScopeResolver(_flags()),
            binding_resolver=resolver,
        ).compose(_context_request(ids, turn))
    assert len(resolver.calls) == 1


def test_resolver_cannot_change_space_or_forge_content_hash(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    turn = _accepted_turn(factory, ids)
    _add_live_binding(factory, ids)
    for resolver in (
        _TurnBindingResolver(factory, wrong_space=ids["part-space-b"]),
        _TurnBindingResolver(factory, wrong_hash=True),
    ):
        with pytest.raises(AgentContextIntegrityError):
            AgentTurnContextService(
                factory,
                scope_resolver=ScopeResolver(_flags()),
                binding_resolver=resolver,
            ).compose(_context_request(ids, turn))


def test_pin_is_revalidated_against_the_current_revision_on_every_turn(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    turn = _accepted_turn(factory, ids)
    _add_pin(factory, ids)
    old_revision = _uuid("p3.4-old-pin-revision")
    body = "# prior pinned body\n"
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO knowledge_revision(
                     id,item_id,revision_no,parent_revision_id,base_revision_id,title,
                     raw_content_sha256,normalized_content_sha256,content_size_bytes,
                     normalizer_version,metadata_json,change_kind,change_reason,
                     created_by,created_at
                   ) VALUES (?,?,2,?,NULL,'Prior pin',?,?,?,'markdown_lf_v1','{}',
                             'edit','fixture','admin',700)""",
                (
                    old_revision,
                    ids["part-hit"],
                    ids["part-hit-revision"],
                    digest,
                    digest,
                    len(body.encode("utf-8")),
                ),
            )
            connection.execute(
                """INSERT INTO knowledge_revision_body(
                     revision_id,item_id,content_md,content_blob_hash,
                     content_size_bytes,content_sha256,created_at
                   ) VALUES (?,?,?,NULL,?,?,700)""",
                (old_revision, ids["part-hit"], body, len(body.encode("utf-8")), digest),
            )
            connection.execute(
                "UPDATE conversation_pin SET revision_id=? WHERE conversation_id=? AND item_id=?",
                (old_revision, ids["conversation-part-a"], ids["part-hit"]),
            )
    finally:
        connection.close()
    bundle = AgentTurnContextService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
    ).compose(_context_request(ids, turn))
    assert len(bundle.pins) == 1
    assert bundle.pins[0].availability == "unavailable"
    assert bundle.pins[0].unavailable_reason == "revision_not_current"
    assert bundle.pins[0].excerpt_md is None


def test_part_scope_change_before_composition_blocks_context_and_resolver(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    turn = _accepted_turn(factory, ids)
    _add_live_binding(factory, ids)
    resolver = _TurnBindingResolver(factory)
    stale_request = replace(
        _context_request(ids, turn),
        actor_scope_epoch=int(ids["admin-epoch"]) + 1,
    )
    with pytest.raises(AgentContextScopeStaleError):
        AgentTurnContextService(
            factory,
            scope_resolver=ScopeResolver(_flags()),
            binding_resolver=resolver,
        ).compose(stale_request)
    assert resolver.calls == []


def test_snapshot_binding_uses_the_immutable_hash_and_never_degrades_to_unavailable(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    turn = _accepted_turn(factory, ids)
    content = "# immutable workspace snapshot\n"
    binding, digest = _add_snapshot_binding(factory, ids, content=content)
    resolver = _TurnBindingResolver(factory, content=content)
    bundle = AgentTurnContextService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
        binding_resolver=resolver,
    ).compose(_context_request(ids, turn))
    context = bundle.explicit_contexts[0]
    assert context.binding_id == binding.id
    assert context.binding_mode == "snapshot"
    assert context.availability == "available"
    assert context.evidence_sha256 == digest
    connection = factory.open()
    try:
        assert tuple(
            connection.execute(
                "SELECT status,status_reason,row_version FROM conversation_context_binding WHERE id=?",
                (binding.id,),
            ).fetchone()
        ) == ("active", None, 1)
    finally:
        connection.close()
    with pytest.raises(AgentContextResolverUnavailableError):
        AgentTurnContextService(
            factory,
            scope_resolver=ScopeResolver(_flags()),
        ).compose(_context_request(ids, turn))


class _FailingAvailabilityAudit(AuditRepository):
    def append(self, connection, draft):  # type: ignore[override]
        if draft.action == "conversation.context.availability":
            raise RuntimeError("fixture availability audit failure")
        return super().append(connection, draft)


def test_live_availability_transition_rolls_back_with_audit_failure(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    turn = _accepted_turn(factory, ids)
    binding = _add_live_binding(factory, ids)
    with pytest.raises(RuntimeError, match="fixture availability audit failure"):
        AgentTurnContextService(
            factory,
            scope_resolver=ScopeResolver(_flags()),
            binding_resolver=_TurnBindingResolver(
                factory, availability="unavailable", reason="source_unavailable"
            ),
            audit=_FailingAvailabilityAudit(),
        ).compose(_context_request(ids, turn))
    connection = factory.open()
    try:
        assert tuple(
            connection.execute(
                "SELECT status,status_reason,row_version FROM conversation_context_binding WHERE id=?",
                (binding.id,),
            ).fetchone()
        ) == ("active", None, 1)
        assert connection.execute(
            "SELECT count(*) FROM outbox_event WHERE event_type='conversation.context.availability_changed'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def _compatibility_fixture(factory: ConnectionFactory) -> dict[str, str]:
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            (
                ids["personal-provisional"],
                ids["personal-provisional-revision"],
            ) = _insert_item(
                connection,
                label="personal-provisional",
                space_id=ids["personal-admin"],
                actor="admin",
                title="Compatibility draft",
                body="# compatdraft personal content\n",
                published=False,
                stale_search_row=True,
                metadata_json=(
                    '{"domain":"engineering","service":"api",'
                    '"topics":["compatibility"]}'
                ),
            )
            ids["other-personal"], ids["other-personal-revision"] = _insert_item(
                connection,
                label="other-personal",
                space_id=ids["personal-other"],
                actor="other",
                title="Foreign personal canary",
                body="# scopealpha foreign-personal-canary\n",
                published=True,
            )
            ids["personal-edge"] = _uuid("edge:personal-hit-provisional")
            connection.execute(
                """INSERT INTO knowledge_edge(
                     id,space_id,source_item_id,target_item_id,edge_type,edge_status,
                     origin,source_evidence_revision_id,target_evidence_revision_id,
                     approval_finding_id,evidence_json,needs_revalidation,created_by,
                     updated_by,created_at,updated_at,row_version
                   ) VALUES (?,?,?,?,'related','active','manual',NULL,NULL,NULL,
                             '{}',0,'admin','admin',700,700,1)""",
                (
                    ids["personal-edge"],
                    ids["personal-admin"],
                    ids["personal-hit"],
                    ids["personal-provisional"],
                ),
            )
            _audit(
                connection,
                label="personal-hit",
                space_id=ids["personal-admin"],
                item_id=ids["personal-hit"],
                actor="admin",
            )
            ids["personal-finding"] = _uuid("finding:personal-hit")
            ids["other-personal-finding"] = _uuid("finding:other-personal")
    finally:
        connection.close()
    return ids


def _compatibility_adapter(
    factory: ConnectionFactory,
    *,
    flags: FeatureFlags | None = None,
) -> LegacyKnowledgeReadAdapter:
    return LegacyKnowledgeReadAdapter(
        factory,
        scope_resolver=ScopeResolver(_flags() if flags is None else flags),
    )


def _compatibility_counts(factory: ConnectionFactory) -> tuple[int, ...]:
    connection = factory.open(read_only=True)
    try:
        return tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "knowledge_item",
                "knowledge_revision",
                "knowledge_edge",
                "curation_finding",
                "audit_event",
                "outbox_event",
                "search_document",
            )
        )
    finally:
        connection.close()


def test_p35_compatibility_adapter_has_no_legacy_store_or_write_dependency() -> None:
    source = inspect.getsource(read_compatibility_module)
    lowered = source.casefold()
    for forbidden in (
        "second_brain_store",
        "legacy_knowledge_store",
        "refresh_index",
        "refresh_cache",
        "pending_diffs",
        "pathlib",
        "read_text(",
        "read_bytes(",
        "write_text(",
        "write_bytes(",
    ):
        assert forbidden not in lowered
    assert re.search(
        r"\b(?:insert\s+into|update\s+\w+\s+set|delete\s+from|replace\s+into)\b",
        source,
        re.IGNORECASE,
    ) is None
    assert "self._factory.open(read_only=True)" in source


@pytest.mark.scenario("SPACE-006")
def test_legacy_uid_never_selects_a_foreign_personal_store_and_shapes_are_preserved(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _compatibility_fixture(factory)
    adapter = _compatibility_adapter(factory)
    forged = LegacyPersonalReadRequest(
        actor_user_id="admin",
        actor_scope_epoch=int(ids["admin-epoch"]),
        legacy_user_id="other",
        limit=500,
    )

    listing = adapter.list_knowledge(forged)
    listed_ids = {str(item["item_id"]) for item in listing["items"]}
    assert set(listing) == {"items", "user_id"}
    assert listing["user_id"] == "admin"
    assert listed_ids == {ids["personal-hit"], ids["personal-provisional"]}
    assert ids["other-personal"] not in listed_ids
    assert ids["part-hit"] not in listed_ids
    assert {"filename", "size", "mtime"} <= set(listing["items"][0])

    view = adapter.view_knowledge(forged, ids["personal-hit"])
    assert view is not None
    assert {"filename", "content", "path"} <= set(view)
    assert adapter.view_knowledge(forged, ids["other-personal"]) is None
    thoughts = adapter.list_thoughts(forged)
    assert {"items", "count", "user_id"} == set(thoughts)
    assert thoughts["count"] == 2
    thought = adapter.get_thought(forged, ids["personal-hit"])
    assert thought is not None and set(thought) == {"meta", "content"}
    assert adapter.get_thought(forged, ids["other-personal"]) is None


@pytest.mark.scenario("SPACE-006")
def test_compatibility_search_is_literal_personal_and_includes_management_states(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _compatibility_fixture(factory)
    adapter = _compatibility_adapter(factory)

    scoped = adapter.search_knowledge(
        LegacySearchReadRequest(
            actor_user_id="admin",
            actor_scope_epoch=int(ids["admin-epoch"]),
            legacy_user_id="other",
            query='scopealpha) OR foreign* "',
            max_results=20,
        )
    )
    assert set(scoped) == {"query", "results", "count", "user_id"}
    assert scoped["user_id"] == "admin"
    assert [row["item_id"] for row in scoped["results"]] == [ids["personal-hit"]]
    assert "foreign-personal-canary" not in str(scoped)

    provisional = adapter.search_knowledge(
        LegacySearchReadRequest(
            actor_user_id="admin",
            actor_scope_epoch=int(ids["admin-epoch"]),
            query="compatdraft",
        )
    )
    assert [row["item_id"] for row in provisional["results"]] == [
        ids["personal-provisional"]
    ]
    assert provisional["results"][0]["curation_status"] == "provisional"


@pytest.mark.scenario("SPACE-006")
def test_compatibility_graph_is_effective_personal_and_read_only(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _compatibility_fixture(factory)
    adapter = _compatibility_adapter(factory)
    before = _compatibility_counts(factory)
    request = LegacyGraphReadRequest(
        actor_user_id="admin",
        actor_scope_epoch=int(ids["admin-epoch"]),
        legacy_user_id="other",
    )

    graph = adapter.graph(request)
    node_ids = {node["data"]["id"] for node in graph["nodes"]}
    assert set(graph) == {"view", "nodes", "edges", "counts", "filters"}
    assert graph["view"] == "wiki"
    assert node_ids == {ids["personal-hit"], ids["personal-provisional"]}
    assert ids["other-personal"] not in node_ids
    assert [edge["data"]["id"] for edge in graph["edges"]] == [
        ids["personal-edge"]
    ]

    focused = adapter.graph(replace(request, focus=ids["personal-hit"], depth=0))
    assert [node["data"]["id"] for node in focused["nodes"]] == [
        ids["personal-hit"]
    ]
    assert focused["edges"] == []
    service_filtered = adapter.graph(replace(request, service="api"))
    assert [node["data"]["id"] for node in service_filtered["nodes"]] == [
        ids["personal-provisional"]
    ]
    assert _compatibility_counts(factory) == before


@pytest.mark.scenario("SPACE-006")
def test_compatibility_findings_read_only_from_canonical_personal_inbox(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _compatibility_fixture(factory)
    adapter = _compatibility_adapter(factory)
    request = LegacyFindingReadRequest(
        actor_user_id="admin",
        actor_scope_epoch=int(ids["admin-epoch"]),
        legacy_user_id="other",
        limit=1,
    )

    result = adapter.list_findings(request)
    assert set(result) == {"items", "count", "counts", "user_id"}
    assert result["user_id"] == "admin"
    assert result["count"] == 1
    assert result["items"][0]["id"] == ids["personal-finding"]
    assert result["items"][0]["status"] == "applied"
    assert result["items"][0]["content"] == ""
    assert result["items"][0]["proposed_content"] == ""
    assert adapter.get_finding(
        LegacyPersonalReadRequest(
            actor_user_id="admin",
            actor_scope_epoch=int(ids["admin-epoch"]),
            legacy_user_id="other",
        ),
        ids["other-personal-finding"],
    ) is None


def test_compatibility_limits_scope_epochs_and_flags_fail_closed(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    ids = _compatibility_fixture(factory)
    adapter = _compatibility_adapter(factory)
    one = LegacyPersonalReadRequest(
        actor_user_id="admin",
        actor_scope_epoch=int(ids["admin-epoch"]),
        limit=1,
    )
    assert len(adapter.list_knowledge(one)["items"]) == 1
    assert len(adapter.list_thoughts(one)["items"]) == 1
    assert adapter.system_document(one, "index") is not None
    with pytest.raises(ValueError, match="limit"):
        adapter.system_document(replace(one, limit=0), "index")
    with pytest.raises(LegacyReadScopeError):
        adapter.list_knowledge(
            replace(one, actor_scope_epoch=int(ids["admin-epoch"]) + 1)
        )
    disabled = _compatibility_adapter(
        factory,
        flags=replace(_flags(), personal_reads=False),
    )
    with pytest.raises(LegacyReadScopeError):
        disabled.list_knowledge(one)
    with pytest.raises(ValueError, match="service"):
        adapter.graph(
            LegacyGraphReadRequest(
                actor_user_id="admin",
                actor_scope_epoch=int(ids["admin-epoch"]),
                service="api\u202e",
            )
        )


def test_compatibility_current_body_and_metadata_evidence_fail_closed(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            ids["bad-metadata"], _ = _insert_item(
                connection,
                label="bad-metadata",
                space_id=ids["personal-admin"],
                actor="admin",
                title="Bad metadata",
                body="# bad metadata\n",
                published=False,
                metadata_json='{"service":{}}',
            )
    finally:
        connection.close()
    adapter = _compatibility_adapter(factory)
    request = LegacyPersonalReadRequest(
        actor_user_id="admin",
        actor_scope_epoch=int(ids["admin-epoch"]),
        limit=500,
    )
    with pytest.raises(LegacyReadIntegrityError, match="metadata service"):
        adapter.list_thoughts(request)

    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                "UPDATE search_document SET content_md='# forged\\n' WHERE item_id=?",
                (ids["personal-hit"],),
            )
    finally:
        connection.close()
    with pytest.raises(LegacyReadIntegrityError, match="search document"):
        adapter.view_knowledge(request, ids["personal-hit"])


def test_compatibility_system_documents_are_database_views_without_refresh(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _compatibility_fixture(factory)
    adapter = _compatibility_adapter(factory)
    request = LegacyPersonalReadRequest(
        actor_user_id="admin",
        actor_scope_epoch=int(ids["admin-epoch"]),
        legacy_user_id="other",
        limit=500,
    )
    before = _compatibility_counts(factory)
    index = adapter.system_document(request, "index")
    log = adapter.system_document(request, "log")
    assert index is not None and log is not None
    assert ids["personal-hit"] in index["content"]
    assert ids["other-personal"] not in index["content"]
    assert "item.fixture" in log["content"]
    assert adapter.system_document(request, "unknown") is None
    assert _compatibility_counts(factory) == before
