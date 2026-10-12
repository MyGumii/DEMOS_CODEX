from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
from threading import Barrier
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import (
    ConnectionFactory,
    ManagedConnection,
    TransactionMode,
    transaction,
)
from code_assist_v1.knowledge_core.domain import (
    ExplicitReferenceAvailability,
    FeatureFlags,
)
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.migrations.v001_invariants import (
    POSTCONDITION_DEFINITIONS,
)
from code_assist_v1.knowledge_core.repositories.artifacts import StagedVerifiedBlob
from code_assist_v1.knowledge_core.repositories.management_reads import (
    ManagementReadRepository,
)
from code_assist_v1.knowledge_core.repositories.semantic_graph_contracts import (
    ExplicitReferenceRecord,
    SemanticGraphContractRepository,
)
from code_assist_v1.knowledge_core.repositories.organization import (
    OrganizationRepository,
    RESPONSIBILITY_ROOT_ID,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)
from code_assist_v1.knowledge_core.scope import ScopeResolver, SessionPrincipal
from code_assist_v1.knowledge_core.services.editing import (
    AcquireLeaseCommand,
    FencedLeaseService,
    LeaseHmacKeyRing,
)
from code_assist_v1.knowledge_core.services.lifecycle import (
    DeactivateItemCommand,
    ItemLifecycleService,
    LifecycleFailpointTriggered,
    LifecycleIdempotencyError,
    LifecycleLeaseHeldError,
    LifecycleScopeStaleError,
    LifecycleServiceError,
    LifecycleServiceIntegrityError,
    RestoreItemCommand,
)
from code_assist_v1.knowledge_core.services.management_reads import (
    CanonicalManagementReadService,
    ItemManagementReadRequest,
    ItemRevisionCompareRequest,
    ItemRevisionReadRequest,
    ManagementReadBodyUnavailableError,
    SpaceManagementPageRequest,
)
from code_assist_v1.knowledge_core.services.purge import (
    ItemPurgeService,
    PurgeAuthorizationError,
    PurgeEligiblePageCommand,
    PurgeFailpointTriggered,
    PurgeItemCommand,
    PurgePreviewCommand,
    PurgeServiceError,
    PurgeValidationError,
)
from code_assist_v1.knowledge_core.services.publication import (
    CreateManualPreviewCommand,
    PublicationPreviewService,
    PublicationService,
    PublishPreviewCommand,
)
from code_assist_v1.knowledge_core.services.retrieval import (
    CanonicalRetrievalService,
    SpaceRetrievalRequest,
)
from code_assist_v1.knowledge_core.services.spaces import space_id_for_owner
from code_assist_v1.tests.knowledge_spaces.unit.test_migration_v10_semantic_graph_contracts import (
    _audit_outbox,
)


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("dfbcf57a-8289-43bf-abbb-8efbe567cfe7")
_KEY = bytes(range(32))


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _token(label: str) -> str:
    value = hashlib.sha256(label.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class _BlobLease:
    def close(self) -> None:
        return None


@dataclass(frozen=True)
class _Observation:
    blob: StagedVerifiedBlob
    lease: _BlobLease


class _Stager:
    def stage_and_verify(
        self,
        content: bytes,
        *,
        expected_sha256: str,
        media_type: str,
        effective_at_ms: int,
        normalized_content_sha256: str | None,
        normalizer_version: str | None,
    ) -> _Observation:
        del normalized_content_sha256, normalizer_version
        assert hashlib.sha256(content).hexdigest() == expected_sha256
        return _Observation(
            StagedVerifiedBlob(
                sha256=expected_sha256,
                size_bytes=len(content),
                media_type=media_type,
                relative_path=(
                    f"{expected_sha256[:2]}/{expected_sha256[2:4]}/{expected_sha256}"
                ),
                created_at_ms=effective_at_ms,
                verified_at_ms=effective_at_ms,
            ),
            _BlobLease(),
        )


def _flags() -> FeatureFlags:
    return FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        personal_writes=True,
        part_reads=True,
        part_writes=True,
    )


@dataclass(frozen=True)
class _Fixture:
    factory: ConnectionFactory
    space_id: str
    item_id: str
    revision_id: str
    epochs: dict[str, int]

    def principal(self, user_id: str) -> SessionPrincipal:
        return SessionPrincipal(user_id, self.epochs[user_id])


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p5.3-lifecycle-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _publish(
    factory: ConnectionFactory,
    resolver: ScopeResolver,
    *,
    epoch: int,
    label: str,
    markdown: str,
) -> tuple[str, str]:
    preview = PublicationPreviewService(factory, resolver, _Stager()).create_manual_preview(
        CreateManualPreviewCommand(
            request_id=_uuid(f"preview-request:{label}"),
            operation_id=_uuid(f"preview-operation:{label}"),
            actor_user_id="editor",
            actor_scope_epoch=epoch,
            markdown=markdown,
            request_meta={},
        )
    )
    published = PublicationService(factory, resolver).publish(
        PublishPreviewCommand(
            request_id=_uuid(f"publish-request:{label}"),
            operation_id=_uuid(f"publish-operation:{label}"),
            actor_user_id="editor",
            actor_scope_epoch=epoch,
            preview_id=preview.preview_id,
            source_snapshot_sha256=preview.source_snapshot_sha256,
            target_mode="part",
            request_meta={},
        )
    )
    target = published.targets[0]
    return str(target["item_id"]), str(target["resolved_revision_id"])


def _seed(layout) -> _Fixture:
    factory = _factory(layout)
    users = UserRepository()
    organization = OrganizationRepository()
    spaces = SpaceRepository()
    team_id = _uuid("team")
    part_id = _uuid("part")
    space_id = _uuid("part-space")
    epochs: dict[str, int] = {}
    connection = factory.open()
    try:
        with transaction(connection):
            for user_id, role in (
                ("editor", "user"),
                ("member", "user"),
                ("leader", "user"),
            ):
                users.import_legacy(
                    connection,
                    LegacyUserRecord(
                        id=user_id,
                        display_name=user_id.title(),
                        password_hash=f"scrypt:p5.3:{user_id}",
                        system_role=role,
                        account_status="active",
                        created_at_ms=100,
                        updated_at_ms=100,
                    ),
                )
                personal = spaces.ensure_personal(
                    connection,
                    space_id=space_id_for_owner("personal", user_id),
                    user_id=user_id,
                    created_at_ms=101,
                )
                spaces.update_state(
                    connection,
                    space_id=personal.space.id,
                    expected_row_version=personal.space.row_version,
                    status="active",
                    write_enabled=True,
                    updated_at_ms=102,
                )
            organization.create_unit(
                connection,
                unit_id=team_id,
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="Lifecycle Team",
                actor_user_id="leader",
                now_ms=110,
            )
            organization.create_unit(
                connection,
                unit_id=part_id,
                kind="part",
                parent_id=team_id,
                name="Lifecycle Part",
                actor_user_id="leader",
                now_ms=111,
            )
            for index, user_id in enumerate(("editor", "member", "leader"), 1):
                organization.open_membership(
                    connection,
                    membership_id=_uuid(f"membership:{user_id}"),
                    user_id=user_id,
                    part_id=part_id,
                    assigned_by="leader",
                    reason="P5.3 fixture",
                    valid_from_ms=120 + index,
                )
                before = users.require(connection, user_id)
                after = organization.compare_and_swap_user_part(
                    connection,
                    user_id=user_id,
                    expected_current_part_id=None,
                    target_part_id=part_id,
                    expected_scope_epoch=before.scope_epoch,
                    expected_row_version=before.row_version,
                    updated_at_ms=130 + index,
                )
                epochs[user_id] = after.scope_epoch
            organization.open_part_leader(
                connection,
                assignment_id=_uuid("leader-assignment"),
                user_id="leader",
                part_id=part_id,
                assigned_by="leader",
                reason="P5.3 fixture",
                valid_from_ms=140,
            )
            ensured = spaces.ensure_part(
                connection,
                space_id=space_id,
                part_id=part_id,
                created_at_ms=150,
            )
            spaces.update_state(
                connection,
                space_id=space_id,
                expected_row_version=ensured.space.row_version,
                status="active",
                write_enabled=True,
                updated_at_ms=151,
            )
    finally:
        connection.close()
    resolver = ScopeResolver(_flags())
    item_id, revision_id = _publish(
        factory,
        resolver,
        epoch=epochs["editor"],
        label="primary",
        markdown="# Lifecycle primary\nbody\n",
    )
    fixture = _Fixture(factory, space_id, item_id, revision_id, epochs)
    _make_published(fixture, label="primary")
    return fixture


def _make_published(
    fixture: _Fixture,
    *,
    label: str,
    item_id: str | None = None,
    revision_id: str | None = None,
    search_content_md: str | None = None,
) -> None:
    item_id = item_id or fixture.item_id
    revision_id = revision_id or fixture.revision_id
    finding_id = _uuid(f"publish-finding:{label}")
    dedup_id = _uuid(f"publish-dedup:{label}")
    operation_id = _uuid(f"publish-operation:{label}")
    transition_id = _uuid(f"publish-transition:{label}")
    connection = fixture.factory.open()
    try:
        with transaction(connection):
            revision = connection.execute(
                """SELECT r.title,b.content_md
                     FROM knowledge_revision AS r
                     JOIN knowledge_revision_body AS b
                       ON b.revision_id=r.id AND b.item_id=r.item_id
                    WHERE r.id=? AND r.item_id=?""",
                (revision_id, item_id),
            ).fetchone()
            assert revision is not None
            content_md = revision[1] if revision[1] is not None else search_content_md
            assert isinstance(content_md, str)
            connection.execute(
                """INSERT INTO operation_dedup(
                     id,actor_user_id,operation_type,operation_id,request_sha256,status,
                     http_status,result_json,error_json,created_at,completed_at,last_seen_at
                   ) VALUES (?,'editor','item_curation',?,?,'in_progress',
                     NULL,NULL,NULL,300,NULL,300)""",
                (dedup_id, operation_id, "a" * 64),
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
                     'space_editor','agent_hidden','publish',?,?,NULL,NULL,'p5.3','v1','r1',
                     NULL,NULL,NULL,'{}','{}',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
                     'user','editor',310,310,1)""",
                (finding_id, fixture.space_id, item_id, revision_id),
            )
            connection.execute(
                """UPDATE curation_finding
                      SET status='approved',reviewed_by='editor',reviewed_at=320,
                          review_reason='approved',updated_at=320,row_version=2
                    WHERE id=?""",
                (finding_id,),
            )
            connection.execute(
                """UPDATE curation_finding
                      SET status='applied',applied_operation_id=?,applied_dedup_id=?,
                          applied_by='editor',applied_at=330,updated_at=330,row_version=3
                    WHERE id=?""",
                (operation_id, dedup_id, finding_id),
            )
            connection.execute(
                """INSERT INTO item_curation_transition(
                     id,item_id,space_id,transition_kind,from_status,to_status,
                     base_revision_id,result_revision_id,finding_id,rescan_job_id,
                     decision_job_id,dedup_id,operation_id,actor_kind,actor_user_id,
                     result_item_row_version,reason,created_at
                   ) VALUES (?,?,?,'finding_decision','provisional','published',?,?,?,NULL,
                     NULL,?,?,'user','editor',2,'publish',330)""",
                (
                    transition_id,
                    item_id,
                    fixture.space_id,
                    revision_id,
                    revision_id,
                    finding_id,
                    dedup_id,
                    operation_id,
                ),
            )
            connection.execute(
                """UPDATE knowledge_item
                      SET curation_status='published',last_curation_transition_id=?,
                          updated_by='editor',updated_at=330,row_version=2
                    WHERE id=?""",
                (transition_id, item_id),
            )
            connection.execute(
                """INSERT INTO search_document(
                     item_id,space_id,revision_id,title,content_md,updated_at
                   ) VALUES (?,?,?,?,?,330)""",
                (item_id, fixture.space_id, revision_id, revision[0], content_md),
            )
    finally:
        connection.close()


def _service(fixture: _Fixture, *, failpoint=None, content_loader=None) -> ItemLifecycleService:
    return ItemLifecycleService(
        fixture.factory,
        ScopeResolver(_flags()),
        failpoint=failpoint,
        content_loader=content_loader,
    )


def _seed_explicit_reference_to_primary(fixture: _Fixture, *, label: str) -> str:
    resolver = ScopeResolver(_flags())
    source_item_id, source_revision_id = _publish(
        fixture.factory,
        resolver,
        epoch=fixture.epochs["editor"],
        label=f"reference-source:{label}",
        markdown=(
            "# Reference source\n"
            f"[[item:{fixture.item_id}]]\n"
        ),
    )
    _make_published(
        fixture,
        label=f"reference-source:{label}",
        item_id=source_item_id,
        revision_id=source_revision_id,
    )
    reference_id = _uuid(f"explicit-reference:{label}")
    connection = fixture.factory.open()
    try:
        with transaction(connection):
            audit_id, outbox_id, operation_id = _audit_outbox(
                connection,
                label=f"lifecycle-reference:{label}",
                actor="editor",
                space_id=fixture.space_id,
                item_id=source_item_id,
                revision_id=source_revision_id,
                timestamp=400,
            )
            SemanticGraphContractRepository().append_reference(
                connection,
                ExplicitReferenceRecord(
                    id=reference_id,
                    source_space_id=fixture.space_id,
                    source_item_id=source_item_id,
                    source_revision_id=source_revision_id,
                    ordinal=1,
                    syntax_version="item-ref-v1",
                    target_space_id=fixture.space_id,
                    target_item_id=fixture.item_id,
                    target_revision_id=fixture.revision_id,
                    closure_proof_sha256="a" * 64,
                    actor_user_id="editor",
                    operation_id=operation_id,
                    effect_key=f"lifecycle-reference:{source_revision_id}:1",
                    audit_event_id=audit_id,
                    outbox_event_id=outbox_id,
                    availability_status=ExplicitReferenceAvailability.AVAILABLE,
                    unavailable_reason=None,
                    created_at=400,
                    updated_at=400,
                    row_version=1,
                ),
            )
    finally:
        connection.close()
    return reference_id


def _lease_service(fixture: _Fixture) -> FencedLeaseService:
    return FencedLeaseService(
        fixture.factory,
        key_ring=LeaseHmacKeyRing(active_key_id="lease-v1", keys={"lease-v1": _KEY}),
        scope_resolver=ScopeResolver(_flags()),
    )


def _purge_service(fixture: _Fixture, *, failpoint=None) -> ItemPurgeService:
    return ItemPurgeService(
        fixture.factory,
        ScopeResolver(_flags()),
        preview_hmac_key=_KEY,
        failpoint=failpoint,
    )


def _add_admin(fixture: _Fixture) -> None:
    connection = fixture.factory.open()
    try:
        with transaction(connection):
            UserRepository().import_legacy(
                connection,
                LegacyUserRecord(
                    id="admin",
                    display_name="Administrator",
                    password_hash="scrypt:p5.6:admin",
                    system_role="admin",
                    account_status="active",
                    created_at_ms=100,
                    updated_at_ms=100,
                ),
            )
            fixture.epochs["admin"] = UserRepository().require(
                connection, "admin"
            ).scope_epoch
    finally:
        connection.close()


def _make_purge_eligible(fixture: _Fixture, *, label: str = "purge") -> int:
    episode_id = _uuid(f"episode:{label}")
    connection = fixture.factory.open()
    try:
        with transaction(connection):
            now_ms = int(
                connection.execute(
                    "SELECT CAST(unixepoch('subsec') * 1000 AS INTEGER)"
                ).fetchone()[0]
            )
            deactivated_at = now_ms - 7_776_000_001
            connection.execute(
                "DELETE FROM search_document WHERE item_id=?", (fixture.item_id,)
            )
            connection.execute(
                """INSERT INTO item_deactivation_episode(
                     id,item_id,episode_no,deactivated_by,deactivated_at,reason,
                     purge_eligible_at,restored_by,restored_at,purged_by,purged_at
                   ) VALUES (?,?,1,'editor',?,'P5.6 eligible fixture',?,NULL,NULL,NULL,NULL)""",
                (
                    episode_id,
                    fixture.item_id,
                    deactivated_at,
                    deactivated_at + 7_776_000_000,
                ),
            )
            connection.execute(
                """UPDATE knowledge_item SET lifecycle_status='inactive',
                          latest_deactivation_episode_id=?,updated_by='editor',
                          updated_at=?,row_version=row_version+1 WHERE id=?""",
                (episode_id, deactivated_at, fixture.item_id),
            )
            return now_ms
    finally:
        connection.close()


def _purge_preview(fixture: _Fixture, *, label: str = "purge"):
    return _purge_service(fixture).preview(
        PurgePreviewCommand(
            principal=fixture.principal("admin"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            request_id=_uuid(f"preview-request:{label}"),
        )
    )


def _deactivate_command(
    fixture: _Fixture,
    *,
    actor: str = "editor",
    label: str = "deactivate",
    row_version: int = 2,
    reason: str = "Retire outdated guidance",
) -> DeactivateItemCommand:
    return DeactivateItemCommand(
        principal=fixture.principal(actor),
        space_id=fixture.space_id,
        item_id=fixture.item_id,
        expected_item_row_version=row_version,
        operation_id=_uuid(f"operation:{label}"),
        request_id=_uuid(f"request:{label}"),
        reason=reason,
        request_meta={"method": "POST", "path": "/items/item/deactivate"},
    )


def _restore_command(
    fixture: _Fixture,
    *,
    actor: str = "member",
    label: str = "restore",
    row_version: int = 3,
) -> RestoreItemCommand:
    return RestoreItemCommand(
        principal=fixture.principal(actor),
        space_id=fixture.space_id,
        item_id=fixture.item_id,
        expected_item_row_version=row_version,
        operation_id=_uuid(f"operation:{label}"),
        request_id=_uuid(f"request:{label}"),
        request_meta={"method": "POST", "path": "/items/item/restore"},
    )


def _state(fixture: _Fixture) -> dict[str, object]:
    connection = fixture.factory.open(read_only=True)
    try:
        tables = (
            "item_deactivation_episode",
            "knowledge_revision",
            "knowledge_revision_body",
            "knowledge_edge",
            "item_relation",
            "search_document",
            "audit_event",
            "outbox_event",
            "operation_dedup",
            "durable_job",
        )
        return {
            "counts": {
                table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in tables
            },
            "item": tuple(
                connection.execute(
                    """SELECT lifecycle_status,curation_status,current_revision_id,
                              latest_deactivation_episode_id,updated_by,row_version
                         FROM knowledge_item WHERE id=?""",
                    (fixture.item_id,),
                ).fetchone()
            ),
            "lease": tuple(
                connection.execute(
                    """SELECT holder_user_id,editor_session_id,token_hmac,hmac_key_id,
                              fencing_generation,base_item_row_version,base_revision_id,
                              acquired_at,heartbeat_at,expires_at,absolute_expires_at
                         FROM item_lease WHERE item_id=?""",
                    (fixture.item_id,),
                ).fetchone()
            ),
            "edges": tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM knowledge_edge ORDER BY id"
                ).fetchall()
            ),
            "relations": tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM item_relation ORDER BY id"
                ).fetchall()
            ),
            "space": tuple(
                connection.execute(
                    "SELECT generation,row_version FROM knowledge_space WHERE id=?",
                    (fixture.space_id,),
                ).fetchone()
            ),
        }
    finally:
        connection.close()


def _assert_postconditions(factory: ConnectionFactory) -> None:
    connection = factory.open(read_only=True)
    try:
        for name, sql in POSTCONDITION_DEFINITIONS:
            assert connection.execute(sql).fetchone() is None, name
    finally:
        connection.close()


class _LifecycleSqlFailpoint(RuntimeError):
    pass


def _is_mutating_sql(sql: str) -> bool:
    normalized = " ".join(sql.upper().split())
    if "TEMP." in normalized or "__KNOWLEDGE_CORE_FTS_PROBE" in normalized:
        return False
    return any(
        token in normalized
        for token in (
            "INSERT INTO ",
            "INSERT OR ",
            "UPDATE ",
            "DELETE FROM ",
            "REPLACE INTO ",
        )
    )


@pytest.mark.scenario("LIFE-001")
@pytest.mark.scenario("LIFE-003")
@pytest.mark.scenario("TIME-003")
def test_member_deactivation_records_episode_hides_item_and_replays(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    service = _service(fixture)
    command = _deactivate_command(fixture, actor="member")

    before = _state(fixture)
    result = service.deactivate(command)
    replay = service.deactivate(
        replace(command, request_id=_uuid("request:deactivate-replay"))
    )

    assert replay == replace(result, request_id=_uuid("request:deactivate-replay"))
    assert result.deactivated_by == "Member"
    assert result.row_version == 3
    deactivated_ms = int(
        datetime.fromisoformat(result.deactivated_at.replace("Z", "+00:00")).timestamp()
        * 1000
    )
    eligible_ms = int(
        datetime.fromisoformat(result.purge_eligible_at.replace("Z", "+00:00")).timestamp()
        * 1000
    )
    assert eligible_ms - deactivated_ms == 7_776_000_000
    assert result.edge_counts == {
        "active_incoming": 0,
        "active_outgoing": 0,
        "inactive_incoming": 0,
        "inactive_outgoing": 0,
        "total": 0,
    }
    assert result.reference_counts == {
        "active_incoming": 0,
        "active_outgoing": 0,
        "conversation_pins": 0,
        "inactive_incoming": 0,
        "inactive_outgoing": 0,
        "total": 0,
    }
    after = _state(fixture)
    assert after["counts"]["item_deactivation_episode"] == 1
    assert after["counts"]["knowledge_revision"] == before["counts"]["knowledge_revision"]
    assert after["counts"]["knowledge_revision_body"] == before["counts"]["knowledge_revision_body"]
    assert after["counts"]["search_document"] == 0
    assert after["counts"]["audit_event"] == before["counts"]["audit_event"] + 1
    assert after["counts"]["outbox_event"] == before["counts"]["outbox_event"] + 1
    assert after["item"][0] == "inactive"
    assert after["item"][2] == fixture.revision_id

    connection = fixture.factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            episode = connection.execute(
                """SELECT id,item_id,episode_no,deactivated_by,deactivated_at,
                          reason,purge_eligible_at,restored_by,restored_at,
                          purged_by,purged_at
                     FROM item_deactivation_episode WHERE item_id=?""",
                (fixture.item_id,),
            ).fetchone()
            assert episode is not None
            episode_id = str(episode[0])
            assert tuple(episode[1:]) == (
                fixture.item_id,
                1,
                "member",
                deactivated_ms,
                command.reason,
                eligible_ms,
                None,
                None,
                None,
                None,
            )
            audit = connection.execute(
                """SELECT occurred_at,request_id,operation_id,action,outcome,
                          actor_kind,actor_user_id,actor_display_name,actor_role_snapshot,
                          space_id,item_id,base_revision_id,new_revision_id,reason,
                          details_json,request_meta_json
                     FROM audit_event WHERE action='knowledge_item.deactivate'"""
            ).fetchone()
            assert audit is not None
            assert tuple(audit[:14]) == (
                deactivated_ms,
                command.request_id,
                command.operation_id,
                "knowledge_item.deactivate",
                "deactivated",
                "user",
                "member",
                "Member",
                "user",
                fixture.space_id,
                fixture.item_id,
                fixture.revision_id,
                fixture.revision_id,
                command.reason,
            )
            details = {
                "edge_counts": result.edge_counts,
                "episode_id": episode_id,
                "episode_no": 1,
                "own_or_expired_lease_released": False,
                "purge_eligible_at": result.purge_eligible_at,
                "reference_counts": result.reference_counts,
                "removed_search_document_count": 1,
                "revision_id": fixture.revision_id,
                "schema_version": 1,
                "space_generation": after["space"][0],
            }
            assert json.loads(audit[14]) == details
            assert json.loads(audit[15]) == command.request_meta
            outbox = connection.execute(
                """SELECT logical_key,space_id,aggregate_type,aggregate_id,
                          aggregate_version,event_type,payload_json,state,available_at,
                          claim_owner,claim_expires_at,attempt_count,last_error_json
                     FROM outbox_event WHERE event_type='knowledge_item.deactivated'"""
            ).fetchone()
            assert outbox is not None
            assert str(outbox[0]).startswith("knowledge-item:deactivated:")
            assert tuple(outbox[1:6]) == (
                fixture.space_id,
                "knowledge_item",
                fixture.item_id,
                3,
                "knowledge_item.deactivated",
            )
            assert json.loads(outbox[6]) == {"item_id": fixture.item_id, **details}
            assert tuple(outbox[7:]) == (
                "pending",
                deactivated_ms,
                None,
                None,
                0,
                None,
            )
            assert ManagementReadRepository().list_items(
                connection, space_id=fixture.space_id
            ) == ()
        result = CanonicalRetrievalService(
            fixture.factory,
            scope_resolver=ScopeResolver(_flags()),
        ).retrieve_for_space(
            SpaceRetrievalRequest(
                actor_user_id="member",
                actor_scope_epoch=fixture.epochs["member"],
                space_id=fixture.space_id,
                query="Lifecycle",
            )
        )
        assert result.hits == ()
    finally:
        connection.close()


@pytest.mark.scenario("REF-005")
def test_target_deactivation_atomically_marks_stable_reference_unavailable(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    reference_id = _seed_explicit_reference_to_primary(
        fixture, label="target-inactive"
    )
    service = _service(fixture)
    command = _deactivate_command(
        fixture, actor="member", label="reference-target-inactive"
    )

    result = service.deactivate(command)
    replay = service.deactivate(
        replace(command, request_id=_uuid("request:reference-target-replay"))
    )
    assert replay == replace(
        result, request_id=_uuid("request:reference-target-replay")
    )

    connection = fixture.factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            reference = SemanticGraphContractRepository().get_reference(
                connection, reference_id
            )
            assert reference is not None
            assert (
                reference.availability_status
                is ExplicitReferenceAvailability.UNAVAILABLE
            )
            assert reference.unavailable_reason == "target_inactive"
            assert reference.target_revision_id == fixture.revision_id
            assert reference.row_version == 2
            item = connection.execute(
                "SELECT lifecycle_status FROM knowledge_item WHERE id=?",
                (fixture.item_id,),
            ).fetchone()
            assert item is not None and item[0] == "inactive"
    finally:
        connection.close()


@pytest.mark.scenario("REF-005")
@pytest.mark.scenario("UPLOAD-011")
def test_reference_unavailability_and_target_deactivation_roll_back_together(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    reference_id = _seed_explicit_reference_to_primary(
        fixture, label="target-inactive-rollback"
    )

    def failpoint(name: str) -> None:
        if name == "lifecycle.after_reference_availability":
            raise LifecycleFailpointTriggered(name)

    with pytest.raises(
        LifecycleFailpointTriggered, match="after_reference_availability"
    ):
        _service(fixture, failpoint=failpoint).deactivate(
            _deactivate_command(
                fixture, actor="member", label="reference-target-rollback"
            )
        )

    connection = fixture.factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            reference = SemanticGraphContractRepository().get_reference(
                connection, reference_id
            )
            assert reference is not None
            assert (
                reference.availability_status
                is ExplicitReferenceAvailability.AVAILABLE
            )
            assert reference.unavailable_reason is None
            assert reference.row_version == 1
            item = connection.execute(
                "SELECT lifecycle_status FROM knowledge_item WHERE id=?",
                (fixture.item_id,),
            ).fetchone()
            assert item is not None and item[0] == "active"
    finally:
        connection.close()


@pytest.mark.scenario("LIFE-001")
@pytest.mark.scenario("LIFE-003")
@pytest.mark.scenario("PROJ-001")
def test_p54_inactive_detail_exposes_actor_reason_ages_impacts_and_sanitized_activity(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    command = _deactivate_command(
        fixture,
        actor="member",
        label="p54-inactive",
        reason="Superseded operating guidance",
    )
    lifecycle = _service(fixture)
    mutation = lifecycle.deactivate(command)
    reader = CanonicalManagementReadService(
        fixture.factory,
        scope_resolver=ScopeResolver(_flags()),
    )
    request = ItemManagementReadRequest(
        actor_user_id="member",
        actor_scope_epoch=fixture.epochs["member"],
        space_id=fixture.space_id,
        item_id=fixture.item_id,
    )
    detail = reader.get_item(request)
    assert detail.record.lifecycle_status == "inactive"
    assert detail.record.current_revision_id == fixture.revision_id
    assert detail.markdown == "# Lifecycle primary\nbody\n"
    assert detail.capabilities == ("read", "restore")
    assert detail.record.lease is None
    assert detail.inactive_duration_seconds is not None
    assert detail.inactive_duration_seconds >= 0
    assert detail.content_age_seconds >= detail.inactive_duration_seconds
    assert detail.review_age_seconds is not None
    assert detail.review_age_seconds >= detail.inactive_duration_seconds
    deactivation = detail.record.deactivation
    assert deactivation is not None
    assert deactivation.deactivated_by_user_id == "member"
    assert deactivation.deactivated_by_display_name == "Member"
    assert deactivation.reason == command.reason
    assert deactivation.purge_eligible_at_ms - deactivation.deactivated_at_ms == 7_776_000_000
    assert int(
        datetime.fromisoformat(
            mutation.deactivated_at.replace("Z", "+00:00")
        ).timestamp()
        * 1000
    ) == deactivation.deactivated_at_ms

    hidden = reader.list_items(
        SpaceManagementPageRequest(
            actor_user_id="member",
            actor_scope_epoch=fixture.epochs["member"],
            space_id=fixture.space_id,
            limit=50,
        )
    )
    assert fixture.item_id not in {item.id for item in hidden.items}
    activity = reader.list_activity(
        SpaceManagementPageRequest(
            actor_user_id="member",
            actor_scope_epoch=fixture.epochs["member"],
            space_id=fixture.space_id,
            limit=50,
        )
    )
    event = next(
        row for row in activity.items if row.action == "knowledge_item.deactivate"
    )
    assert event.actor_kind == "user"
    assert event.actor_user_id == "member"
    assert event.actor_display_name == "Member"
    assert event.reason == command.reason
    assert event.item_id == fixture.item_id
    assert event.base_revision_id == fixture.revision_id
    assert event.new_revision_id == fixture.revision_id
    _assert_postconditions(fixture.factory)


@pytest.mark.scenario("LIFE-002")
def test_foreign_live_lease_is_not_silently_released(isolated_knowledge_layout) -> None:
    fixture = _seed(isolated_knowledge_layout)
    leases = _lease_service(fixture)
    acquired = leases.acquire(
        AcquireLeaseCommand(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            editor_session_id=_uuid("foreign-session"),
            operation_id=_uuid("foreign-acquire"),
            request_id=_uuid("foreign-acquire-request"),
            raw_token=_token("foreign"),
            request_meta={},
        )
    )
    before = _state(fixture)
    command = _deactivate_command(fixture, actor="member", label="foreign-held")

    with pytest.raises(LifecycleLeaseHeldError) as raised:
        _service(fixture).deactivate(command)
    assert raised.value.code == "LEASE_HELD"
    after = _state(fixture)
    assert after["item"] == before["item"]
    assert after["lease"] == before["lease"]
    assert after["lease"][0] == "editor"
    assert after["lease"][4] == acquired.fencing_generation
    assert after["counts"]["item_deactivation_episode"] == 0
    assert after["counts"]["audit_event"] == before["counts"]["audit_event"]
    assert after["counts"]["outbox_event"] == before["counts"]["outbox_event"]
    assert after["counts"]["operation_dedup"] == before["counts"]["operation_dedup"] + 1


def test_expired_foreign_lease_is_fenced_before_deactivation(
    isolated_knowledge_layout,
    monkeypatch,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    acquired = _lease_service(fixture).acquire(
        AcquireLeaseCommand(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            editor_session_id=_uuid("expired-foreign-session"),
            operation_id=_uuid("expired-foreign-acquire"),
            request_id=_uuid("expired-foreign-acquire-request"),
            raw_token=_token("expired-foreign"),
            request_meta={},
        )
    )
    before = _state(fixture)
    expires_at_ms = int(before["lease"][9])
    import code_assist_v1.knowledge_core.services.lifecycle as lifecycle_module

    monkeypatch.setattr(lifecycle_module, "db_now_ms", lambda _connection: expires_at_ms)
    result = _service(fixture).deactivate(
        _deactivate_command(fixture, actor="member", label="expired-foreign")
    )

    after = _state(fixture)
    assert result.row_version == 3
    assert after["item"][0] == "inactive"
    assert after["lease"][0] is None
    assert after["lease"][4] == acquired.fencing_generation + 1
    assert all(value is None for value in after["lease"][5:])
    connection = fixture.factory.open(read_only=True)
    try:
        details_json = connection.execute(
            """SELECT details_json FROM audit_event
                 WHERE action='knowledge_item.deactivate' AND operation_id=?""",
            (_uuid("operation:expired-foreign"),),
        ).fetchone()[0]
        assert json.loads(details_json)["own_or_expired_lease_released"] is True
    finally:
        connection.close()
    _assert_postconditions(fixture.factory)


def test_actor_owned_lease_is_fenced_and_cleared_atomically(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    leases = _lease_service(fixture)
    acquired = leases.acquire(
        AcquireLeaseCommand(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            editor_session_id=_uuid("own-session"),
            operation_id=_uuid("own-acquire"),
            request_id=_uuid("own-acquire-request"),
            raw_token=_token("own"),
            request_meta={},
        )
    )

    _service(fixture).deactivate(_deactivate_command(fixture, actor="editor"))
    after = _state(fixture)
    assert after["lease"][0] is None
    assert after["lease"][4] == acquired.fencing_generation + 1
    assert all(value is None for value in after["lease"][5:])


@pytest.mark.scenario("TIME-003")
def test_restore_closes_latest_episode_and_emits_exact_audit_outbox(
    isolated_knowledge_layout,
    monkeypatch,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    import code_assist_v1.knowledge_core.services.lifecycle as lifecycle_module

    deactivated_at_ms = 1_800_000_000_000
    restored_at_ms = deactivated_at_ms + 123_456
    times = iter((deactivated_at_ms, restored_at_ms))
    monkeypatch.setattr(lifecycle_module, "db_now_ms", lambda _connection: next(times))
    service = _service(fixture)
    service.deactivate(
        _deactivate_command(fixture, actor="editor", label="restore-exact")
    )
    before_restore = _state(fixture)
    command = _restore_command(fixture, actor="member", label="restore-exact")
    result = service.restore(command)
    replay = service.restore(
        replace(command, request_id=_uuid("request:restore-exact-replay"))
    )

    assert replay == replace(
        result, request_id=_uuid("request:restore-exact-replay")
    )
    assert result.item_id == fixture.item_id
    assert result.revision_id == fixture.revision_id
    assert result.row_version == 4
    assert result.inactive_duration_seconds == 123
    assert result.review_job_queued is False
    after = _state(fixture)
    assert after["item"][0] == "active"
    assert after["item"][2] == fixture.revision_id
    assert after["counts"]["knowledge_revision"] == before_restore["counts"]["knowledge_revision"]
    assert after["counts"]["knowledge_revision_body"] == before_restore["counts"]["knowledge_revision_body"]
    assert after["counts"]["durable_job"] == before_restore["counts"]["durable_job"]
    assert after["counts"]["search_document"] == 1
    assert after["counts"]["audit_event"] == before_restore["counts"]["audit_event"] + 1
    assert after["counts"]["outbox_event"] == before_restore["counts"]["outbox_event"] + 1

    connection = fixture.factory.open(read_only=True)
    try:
        episode = connection.execute(
            """SELECT id,episode_no,deactivated_at,restored_by,restored_at,
                      purged_by,purged_at
                 FROM item_deactivation_episode WHERE item_id=?""",
            (fixture.item_id,),
        ).fetchone()
        assert episode is not None
        assert tuple(episode[1:]) == (
            1,
            deactivated_at_ms,
            "member",
            restored_at_ms,
            None,
            None,
        )
        details = {
            "episode_id": str(episode[0]),
            "episode_no": 1,
            "inactive_duration_seconds": 123,
            "restored_search_document_count": 1,
            "review_job_queued": False,
            "revision_id": fixture.revision_id,
            "schema_version": 1,
            "space_generation": after["space"][0],
        }
        audit = connection.execute(
            """SELECT occurred_at,request_id,operation_id,action,outcome,
                      actor_kind,actor_user_id,actor_display_name,actor_role_snapshot,
                      space_id,item_id,base_revision_id,new_revision_id,reason,
                      details_json,request_meta_json
                 FROM audit_event WHERE action='knowledge_item.restore'"""
        ).fetchone()
        assert audit is not None
        assert tuple(audit[:14]) == (
            restored_at_ms,
            command.request_id,
            command.operation_id,
            "knowledge_item.restore",
            "restored",
            "user",
            "member",
            "Member",
            "user",
            fixture.space_id,
            fixture.item_id,
            fixture.revision_id,
            fixture.revision_id,
            None,
        )
        assert json.loads(audit[14]) == details
        assert json.loads(audit[15]) == command.request_meta
        outbox = connection.execute(
            """SELECT logical_key,space_id,aggregate_type,aggregate_id,
                      aggregate_version,event_type,payload_json,state,available_at,
                      claim_owner,claim_expires_at,attempt_count,last_error_json
                 FROM outbox_event WHERE event_type='knowledge_item.restored'"""
        ).fetchone()
        assert outbox is not None
        assert str(outbox[0]).startswith("knowledge-item:restored:")
        assert tuple(outbox[1:6]) == (
            fixture.space_id,
            "knowledge_item",
            fixture.item_id,
            4,
            "knowledge_item.restored",
        )
        assert json.loads(outbox[6]) == {"item_id": fixture.item_id, **details}
        assert tuple(outbox[7:]) == (
            "pending",
            restored_at_ms,
            None,
            None,
            0,
            None,
        )
    finally:
        connection.close()
    _assert_postconditions(fixture.factory)


def _seed_graph_and_relation(fixture: _Fixture) -> tuple[str, str, str]:
    second_item, second_revision = _publish(
        fixture.factory,
        ScopeResolver(_flags()),
        epoch=fixture.epochs["editor"],
        label="second",
        markdown="# Lifecycle second\nbody\n",
    )
    _make_published(
        fixture,
        label="second",
        item_id=second_item,
        revision_id=second_revision,
    )
    active_edge = _uuid("active-edge")
    inactive_edge = _uuid("inactive-edge")
    relation_id = _uuid("relation")
    connection = fixture.factory.open()
    try:
        with transaction(connection):
            for edge_id, status in (
                (active_edge, "active"),
                (inactive_edge, "inactive"),
            ):
                connection.execute(
                    """INSERT INTO knowledge_edge(
                         id,space_id,source_item_id,target_item_id,edge_type,
                         edge_status,origin,source_evidence_revision_id,
                         target_evidence_revision_id,approval_finding_id,evidence_json,
                         needs_revalidation,created_by,updated_by,created_at,updated_at,
                         row_version
                       ) VALUES (?,?,?,?,? ,?,'manual',NULL,NULL,NULL,'{}',0,
                         'editor','editor',400,400,1)""",
                    (
                        edge_id,
                        fixture.space_id,
                        fixture.item_id,
                        second_item,
                        "related",
                        status,
                    ),
                )
            connection.execute(
                """INSERT INTO item_relation(
                     id,source_item_id,source_revision_id,target_item_id,
                     target_revision_id,relation_type,relation_status,evidence_json,
                     created_by,updated_by,created_at,updated_at,row_version
                   ) VALUES (?,?,?,?,?,'promoted_from','active','{}',
                     'editor','editor',401,401,1)""",
                (
                    relation_id,
                    fixture.item_id,
                    fixture.revision_id,
                    second_item,
                    second_revision,
                ),
            )
    finally:
        connection.close()
    return second_item, active_edge, inactive_edge


@pytest.mark.scenario("LIFE-004")
@pytest.mark.scenario("LIFE-005")
def test_deactivate_restore_preserves_edge_rows_and_only_active_edge_reappears(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    _second_item, active_edge, inactive_edge = _seed_graph_and_relation(fixture)
    before = _state(fixture)
    service = _service(fixture)

    deactivated = service.deactivate(_deactivate_command(fixture))
    assert deactivated.edge_counts == {
        "active_incoming": 0,
        "active_outgoing": 1,
        "inactive_incoming": 0,
        "inactive_outgoing": 1,
        "total": 2,
    }
    assert deactivated.reference_counts["active_outgoing"] == 1
    inactive = _state(fixture)
    assert inactive["edges"] == before["edges"]
    assert inactive["relations"] == before["relations"]

    connection = fixture.factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            graph = ManagementReadRepository().graph(
                connection, space_id=fixture.space_id
            )
        assert {edge.id for edge in graph.edges} == set()
    finally:
        connection.close()

    restored = service.restore(_restore_command(fixture))
    assert restored.revision_id == fixture.revision_id
    assert restored.row_version == 4
    assert restored.review_job_queued is False
    after = _state(fixture)
    assert after["edges"] == before["edges"]
    assert after["relations"] == before["relations"]
    assert after["item"][0] == "active"
    assert after["item"][2] == fixture.revision_id

    connection = fixture.factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            graph = ManagementReadRepository().graph(
                connection, space_id=fixture.space_id
            )
        assert {edge.id for edge in graph.edges} == {active_edge}
        assert inactive_edge not in {edge.id for edge in graph.edges}
    finally:
        connection.close()
    _assert_postconditions(fixture.factory)


@pytest.mark.scenario("LIFE-006")
def test_redeactivation_creates_a_new_episode_and_eligibility(
    isolated_knowledge_layout,
    monkeypatch,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    import code_assist_v1.knowledge_core.services.lifecycle as lifecycle_module

    times = iter((1_800_000_000_000, 1_800_086_400_000, 1_800_172_800_000))
    monkeypatch.setattr(lifecycle_module, "db_now_ms", lambda _connection: next(times))
    service = _service(fixture)
    first = service.deactivate(_deactivate_command(fixture, label="first"))
    service.restore(_restore_command(fixture, label="first", row_version=3))
    second = service.deactivate(
        _deactivate_command(fixture, label="second", row_version=4)
    )

    assert second.purge_eligible_at > first.purge_eligible_at
    connection = fixture.factory.open(read_only=True)
    try:
        rows = connection.execute(
            """SELECT episode_no,deactivated_at,purge_eligible_at,
                      restored_by,restored_at,purged_by,purged_at
                 FROM item_deactivation_episode WHERE item_id=?
                 ORDER BY episode_no""",
            (fixture.item_id,),
        ).fetchall()
        assert len(rows) == 2
        assert tuple(rows[0]) == (
            1,
            1_800_000_000_000,
            1_807_776_000_000,
            "member",
            1_800_086_400_000,
            None,
            None,
        )
        assert tuple(rows[1]) == (
            2,
            1_800_172_800_000,
            1_807_948_800_000,
            None,
            None,
            None,
            None,
        )
    finally:
        connection.close()


def test_stale_version_and_changed_idempotent_intent_fail_closed(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    service = _service(fixture)
    stale = _deactivate_command(fixture, label="stale", row_version=1)
    before = _state(fixture)
    with pytest.raises(LifecycleServiceError) as raised:
        service.deactivate(stale)
    assert raised.value.details["current_row_version"] == 2
    after_stale = _state(fixture)
    assert after_stale["item"] == before["item"]
    assert after_stale["counts"]["item_deactivation_episode"] == 0

    command = _deactivate_command(fixture, label="exact")
    service.deactivate(command)
    with pytest.raises(LifecycleIdempotencyError):
        service.deactivate(replace(command, reason="Different intent"))


def test_membership_move_rejects_captured_scope_before_lifecycle_effect(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    command = _deactivate_command(fixture, actor="member", label="moved-scope")
    connection = fixture.factory.open()
    try:
        with transaction(connection):
            organization = OrganizationRepository()
            users = UserRepository()
            membership = organization.get_current_membership(connection, "member")
            assert membership is not None
            organization.close_current_membership(
                connection,
                user_id="member",
                expected_membership_id=membership.id,
                closed_at_ms=999,
            )
            actor = users.require(connection, "member")
            organization.compare_and_swap_user_part(
                connection,
                user_id="member",
                expected_current_part_id=membership.part_id,
                target_part_id=None,
                expected_scope_epoch=actor.scope_epoch,
                expected_row_version=actor.row_version,
                updated_at_ms=999,
            )
    finally:
        connection.close()

    before = _state(fixture)
    with pytest.raises(LifecycleScopeStaleError) as raised:
        _service(fixture).deactivate(command)
    assert raised.value.code == "SCOPE_STALE"
    assert _state(fixture) == before
    _assert_postconditions(fixture.factory)


def test_two_service_instances_commit_exactly_one_deactivation(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    before = _state(fixture)
    barrier = Barrier(2)

    def invoke(label: str):
        barrier.wait(timeout=20)
        try:
            return _service(fixture).deactivate(
                _deactivate_command(fixture, actor="member", label=label)
            )
        except LifecycleServiceError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(invoke, ("race-a", "race-b")))
    successes = tuple(
        value for value in outcomes if not isinstance(value, LifecycleServiceError)
    )
    failures = tuple(
        value for value in outcomes if isinstance(value, LifecycleServiceError)
    )
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].code == "STATE_CONFLICT"
    state = _state(fixture)
    assert state["counts"]["item_deactivation_episode"] == 1
    assert state["counts"]["audit_event"] == before["counts"]["audit_event"] + 1
    assert state["counts"]["outbox_event"] == before["counts"]["outbox_event"] + 1
    assert state["item"][0] == "inactive"
    _assert_postconditions(fixture.factory)


def test_two_service_instances_commit_exactly_one_restore(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    _service(fixture).deactivate(_deactivate_command(fixture))
    before = _state(fixture)
    barrier = Barrier(2)

    def invoke(label: str):
        barrier.wait(timeout=20)
        try:
            return _service(fixture).restore(_restore_command(fixture, label=label))
        except LifecycleServiceError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(invoke, ("restore-race-a", "restore-race-b")))
    successes = tuple(
        value for value in outcomes if not isinstance(value, LifecycleServiceError)
    )
    failures = tuple(
        value for value in outcomes if isinstance(value, LifecycleServiceError)
    )
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].code == "STATE_CONFLICT"
    state = _state(fixture)
    assert state["counts"]["item_deactivation_episode"] == 1
    assert state["counts"]["audit_event"] == before["counts"]["audit_event"] + 1
    assert state["counts"]["outbox_event"] == before["counts"]["outbox_event"] + 1
    assert state["item"][0] == "active"
    assert state["item"][2] == fixture.revision_id
    _assert_postconditions(fixture.factory)


@pytest.mark.parametrize(
    "name",
    (
        "lifecycle.after_lease_clear",
        "lifecycle.after_episode",
        "lifecycle.after_item_state",
        "lifecycle.after_search_visibility",
        "lifecycle.after_audit",
        "lifecycle.after_outbox",
        "lifecycle.before_commit",
    ),
)
def test_deactivation_failpoints_roll_back_every_effect(
    isolated_knowledge_layout,
    name: str,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    before = _state(fixture)

    def failpoint(observed: str) -> None:
        if observed == name:
            raise LifecycleFailpointTriggered(name)

    with pytest.raises(LifecycleFailpointTriggered):
        _service(fixture, failpoint=failpoint).deactivate(
            _deactivate_command(fixture, label=f"fail:{name}")
        )
    assert _state(fixture) == before
    _assert_postconditions(fixture.factory)


@pytest.mark.parametrize(
    "name",
    (
        "lifecycle.after_episode",
        "lifecycle.after_item_state",
        "lifecycle.after_search_visibility",
        "lifecycle.after_audit",
        "lifecycle.after_outbox",
        "lifecycle.before_commit",
    ),
)
def test_restore_failpoints_roll_back_every_effect(
    isolated_knowledge_layout,
    name: str,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    _service(fixture).deactivate(_deactivate_command(fixture))
    before = _state(fixture)

    def failpoint(observed: str) -> None:
        if observed == name:
            raise LifecycleFailpointTriggered(name)

    with pytest.raises(LifecycleFailpointTriggered):
        _service(fixture, failpoint=failpoint).restore(
            _restore_command(fixture, label=f"fail:{name}")
        )
    assert _state(fixture) == before
    _assert_postconditions(fixture.factory)


@pytest.mark.parametrize("fail_at", tuple(range(1, 10)))
def test_deactivation_rolls_back_after_every_observed_sql_write_boundary(
    isolated_knowledge_layout,
    monkeypatch,
    fail_at: int,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    lease_service = _lease_service(fixture)
    lease_service.acquire(
        AcquireLeaseCommand(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            editor_session_id=_uuid(f"sql-session:{fail_at}"),
            operation_id=_uuid(f"sql-acquire:{fail_at}"),
            request_id=_uuid(f"sql-acquire-request:{fail_at}"),
            raw_token=_token(f"sql-token:{fail_at}"),
            request_meta={},
        )
    )
    before = _state(fixture)
    original_execute = ManagedConnection.execute
    observed = 0

    def wrapped_execute(self, sql, parameters=()):
        nonlocal observed
        cursor = original_execute(self, sql, parameters)
        if _is_mutating_sql(sql):
            observed += 1
            if observed == fail_at:
                raise _LifecycleSqlFailpoint(f"write-{fail_at}")
        return cursor

    monkeypatch.setattr(ManagedConnection, "execute", wrapped_execute)
    with pytest.raises(_LifecycleSqlFailpoint):
        _service(fixture).deactivate(
            _deactivate_command(fixture, label=f"sql:{fail_at}")
        )
    assert observed == fail_at
    assert _state(fixture) == before
    _assert_postconditions(fixture.factory)


@pytest.mark.parametrize("fail_at", tuple(range(1, 9)))
def test_restore_rolls_back_after_every_observed_sql_write_boundary(
    isolated_knowledge_layout,
    monkeypatch,
    fail_at: int,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    _service(fixture).deactivate(_deactivate_command(fixture))
    before = _state(fixture)
    original_execute = ManagedConnection.execute
    observed = 0

    def wrapped_execute(self, sql, parameters=()):
        nonlocal observed
        cursor = original_execute(self, sql, parameters)
        if _is_mutating_sql(sql):
            observed += 1
            if observed == fail_at:
                raise _LifecycleSqlFailpoint(f"write-{fail_at}")
        return cursor

    monkeypatch.setattr(ManagedConnection, "execute", wrapped_execute)
    with pytest.raises(_LifecycleSqlFailpoint):
        _service(fixture).restore(
            _restore_command(fixture, label=f"sql:{fail_at}")
        )
    assert observed == fail_at
    assert _state(fixture) == before
    _assert_postconditions(fixture.factory)


def test_lifecycle_sql_write_boundary_count_is_frozen(
    isolated_knowledge_layout,
    monkeypatch,
    capsys,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    _lease_service(fixture).acquire(
        AcquireLeaseCommand(
            principal=fixture.principal("editor"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            editor_session_id=_uuid("boundary-session"),
            operation_id=_uuid("boundary-acquire"),
            request_id=_uuid("boundary-acquire-request"),
            raw_token=_token("boundary"),
            request_meta={},
        )
    )
    original_execute = ManagedConnection.execute
    observed = 0

    def wrapped_execute(self, sql, parameters=()):
        nonlocal observed
        cursor = original_execute(self, sql, parameters)
        if _is_mutating_sql(sql):
            observed += 1
        return cursor

    monkeypatch.setattr(ManagedConnection, "execute", wrapped_execute)
    _service(fixture).deactivate(
        _deactivate_command(fixture, actor="editor", label="boundary")
    )
    # The outbox append also advances the durable v1 and v12 footer-v2
    # projection desired-generation markers in the same transaction.
    assert observed == 11
    observed = 0
    _service(fixture).restore(
        _restore_command(fixture, actor="editor", label="boundary")
    )
    assert observed == 10
    print("item_lifecycle_sql_write_boundaries=deactivate:11,restore:10")
    assert "deactivate:11,restore:10" in capsys.readouterr().out


def _seed_blob_backed_published_item(fixture: _Fixture) -> tuple[str, str, bytes]:
    item_id = _uuid("blob-item")
    revision_id = _uuid("blob-revision")
    body = b"# Blob lifecycle\nbody\n"
    digest = hashlib.sha256(body).hexdigest()
    connection = fixture.factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO blob_object(
                     sha256,size_bytes,media_type,relative_path,
                     verification_status,created_at,verified_at
                   ) VALUES (?,?,'text/markdown',?,'verified',500,500)""",
                (digest, len(body), f"{digest[:2]}/{digest[2:4]}/{digest}"),
            )
            connection.execute(
                """INSERT INTO knowledge_item(
                     id,space_id,lineage_group_id,lifecycle_status,curation_status,
                     current_revision_id,last_curation_transition_id,
                     latest_deactivation_episode_id,redirect_item_id,created_by,
                     updated_by,created_at,updated_at,row_version
                     ) VALUES (?,?,NULL,'active','provisional',?,NULL,NULL,NULL,
                     'editor','editor',500,500,1)""",
                (item_id, fixture.space_id, revision_id),
            )
            connection.execute(
                """INSERT INTO knowledge_revision(
                     id,item_id,revision_no,parent_revision_id,base_revision_id,title,
                     raw_content_sha256,normalized_content_sha256,content_size_bytes,
                     normalizer_version,metadata_json,change_kind,change_reason,
                     created_by,created_at
                   ) VALUES (?,?,1,NULL,NULL,'Blob lifecycle',?,?,?,
                     'markdown_lf_v1','{}','initial','blob fixture','editor',500)""",
                (revision_id, item_id, digest, digest, len(body)),
            )
            connection.execute(
                """INSERT INTO knowledge_revision_body(
                     revision_id,item_id,content_md,content_blob_hash,
                     content_size_bytes,content_sha256,created_at
                   ) VALUES (?,?,NULL,?,?,?,500)""",
                (revision_id, item_id, digest, len(body), digest),
            )
    finally:
        connection.close()
    _make_published(
        fixture,
        label="blob",
        item_id=item_id,
        revision_id=revision_id,
        search_content_md=body.decode("utf-8"),
    )
    return item_id, revision_id, body


def test_p54_blob_backed_history_loads_outside_snapshot_and_revalidates_exact_evidence(
    isolated_knowledge_layout,
) -> None:
    original = _seed(isolated_knowledge_layout)
    item_id, revision_id, body = _seed_blob_backed_published_item(original)
    fixture = replace(original, item_id=item_id, revision_id=revision_id)
    digest = hashlib.sha256(body).hexdigest()
    loads: list[tuple[str, int]] = []

    def loader(blob_sha256: str, size_bytes: int) -> bytes:
        loads.append((blob_sha256, size_bytes))
        return body

    reader = CanonicalManagementReadService(
        fixture.factory,
        scope_resolver=ScopeResolver(_flags()),
        content_loader=loader,
    )
    item = reader.get_item(
        ItemManagementReadRequest(
            actor_user_id="editor",
            actor_scope_epoch=fixture.epochs["editor"],
            space_id=fixture.space_id,
            item_id=item_id,
        )
    )
    assert item.markdown == body.decode("utf-8")
    assert loads == [(digest, len(body))]
    revision = reader.get_revision(
        ItemRevisionReadRequest(
            actor_user_id="editor",
            actor_scope_epoch=fixture.epochs["editor"],
            space_id=fixture.space_id,
            item_id=item_id,
            revision_id=revision_id,
        )
    )
    assert revision.markdown == body.decode("utf-8")
    comparison = reader.compare_revisions(
        ItemRevisionCompareRequest(
            actor_user_id="editor",
            actor_scope_epoch=fixture.epochs["editor"],
            space_id=fixture.space_id,
            item_id=item_id,
            from_revision_id=revision_id,
            to_revision_id=revision_id,
        )
    )
    assert comparison.hunks == ()
    assert all(observed == (digest, len(body)) for observed in loads)

    invalid = CanonicalManagementReadService(
        fixture.factory,
        scope_resolver=ScopeResolver(_flags()),
        content_loader=lambda _digest, _size: b"tampered",
    )
    with pytest.raises(ManagementReadBodyUnavailableError):
        invalid.get_revision(
            ItemRevisionReadRequest(
                actor_user_id="editor",
                actor_scope_epoch=fixture.epochs["editor"],
                space_id=fixture.space_id,
                item_id=item_id,
                revision_id=revision_id,
            )
        )

    changed = False

    def drifting_loader(_digest: str, _size: int) -> bytes:
        nonlocal changed
        if not changed:
            connection = fixture.factory.open()
            try:
                with transaction(connection):
                    connection.execute(
                        """UPDATE knowledge_item
                              SET updated_at=updated_at+1,row_version=row_version+1
                            WHERE id=?""",
                        (item_id,),
                    )
            finally:
                connection.close()
            changed = True
        return body

    drifting = CanonicalManagementReadService(
        fixture.factory,
        scope_resolver=ScopeResolver(_flags()),
        content_loader=drifting_loader,
    )
    refreshed = drifting.get_item(
        ItemManagementReadRequest(
            actor_user_id="editor",
            actor_scope_epoch=fixture.epochs["editor"],
            space_id=fixture.space_id,
            item_id=item_id,
        )
    )
    assert changed is True
    assert refreshed.markdown == body.decode("utf-8")
    assert refreshed.record.row_version == item.record.row_version + 1


def test_blob_backed_restore_uses_verified_external_bytes_and_replay_needs_no_loader(
    isolated_knowledge_layout,
) -> None:
    original = _seed(isolated_knowledge_layout)
    item_id, revision_id, body = _seed_blob_backed_published_item(original)
    fixture = replace(original, item_id=item_id, revision_id=revision_id)
    loads: list[tuple[str, int]] = []

    def loader(digest: str, size: int) -> bytes:
        loads.append((digest, size))
        return body

    service = _service(fixture, content_loader=loader)
    service.deactivate(_deactivate_command(fixture, label="blob", row_version=2))
    command = _restore_command(fixture, label="blob", row_version=3)
    result = service.restore(command)
    assert result.revision_id == revision_id
    assert loads == [(hashlib.sha256(body).hexdigest(), len(body))]

    replay = _service(fixture).restore(
        replace(command, request_id=_uuid("request:blob-replay"))
    )
    assert replay == replace(result, request_id=_uuid("request:blob-replay"))
    assert _state(fixture)["counts"]["search_document"] == 2
    _assert_postconditions(fixture.factory)


def test_blob_backed_restore_rejects_wrong_observation_without_db_mutation(
    isolated_knowledge_layout,
) -> None:
    original = _seed(isolated_knowledge_layout)
    item_id, revision_id, _body = _seed_blob_backed_published_item(original)
    fixture = replace(original, item_id=item_id, revision_id=revision_id)
    _service(fixture).deactivate(
        _deactivate_command(fixture, label="blob-bad", row_version=2)
    )
    before = _state(fixture)
    with pytest.raises(LifecycleServiceIntegrityError):
        _service(fixture, content_loader=lambda _digest, _size: b"wrong").restore(
            _restore_command(fixture, label="blob-bad", row_version=3)
        )
    assert _state(fixture) == before


def test_lifecycle_results_and_storage_never_contain_request_secrets(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    sentinel = "P5_3_SECRET_SENTINEL_39e6d48c"
    command = replace(
        _deactivate_command(fixture),
        request_meta={
            "method": "POST",
            "path": "/items/item/deactivate",
            "authorization": sentinel,
            "user_agent": sentinel,
        },
    )
    result = _service(fixture).deactivate(command)
    assert sentinel not in repr(command)
    assert sentinel not in json.dumps(result.as_dict(), sort_keys=True)
    connection = fixture.factory.open(read_only=True)
    try:
        for table in ("operation_dedup", "audit_event", "outbox_event"):
            rows = connection.execute(f"SELECT * FROM {table}").fetchall()
            assert sentinel not in repr([tuple(row) for row in rows])
    finally:
        connection.close()


def _purge_command(
    fixture: _Fixture,
    preview,
    *,
    actor: str = "admin",
    label: str = "purge",
    row_version: int = 3,
) -> PurgeItemCommand:
    return PurgeItemCommand(
        principal=fixture.principal(actor),
        space_id=fixture.space_id,
        item_id=fixture.item_id,
        expected_item_row_version=row_version,
        operation_id=_uuid(f"purge-operation:{label}"),
        request_id=_uuid(f"purge-request:{label}"),
        preview_token=preview.preview_token,
        confirmation=preview.confirmation_text,
        request_meta={"method": "POST", "path": "/items/item/purge"},
    )


@pytest.mark.scenario("LIFE-009")
def test_admin_purge_queue_is_bounded_cursor_stable_and_includes_time_eligible_rows(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    _add_admin(fixture)
    _make_purge_eligible(fixture, label="queue-first")
    second_item, second_revision = _publish(
        fixture.factory,
        ScopeResolver(_flags()),
        epoch=fixture.epochs["editor"],
        label="purge-queue-second",
        markdown="# Second purge candidate\n",
    )
    second = replace(
        fixture,
        item_id=second_item,
        revision_id=second_revision,
    )
    _make_purge_eligible(second, label="queue-second")
    service = _purge_service(fixture)

    first = service.list_eligible(
        PurgeEligiblePageCommand(
            principal=fixture.principal("admin"),
            request_id=_uuid("purge-queue:first"),
            limit=1,
        )
    )
    assert first.has_more is True
    assert first.next_cursor is not None
    assert len(first.items) == 1
    assert set(first.items[0]) == {
        "blocking_reference_count",
        "deactivated_at",
        "item_id",
        "purge_eligible_at",
        "space_id",
        "title",
    }

    final = service.list_eligible(
        PurgeEligiblePageCommand(
            principal=fixture.principal("admin"),
            request_id=_uuid("purge-queue:second"),
            cursor=first.next_cursor,
            limit=1,
        )
    )
    assert final.has_more is False
    assert final.next_cursor is None
    assert len(final.items) == 1
    assert {first.items[0]["item_id"], final.items[0]["item_id"]} == {
        fixture.item_id,
        second.item_id,
    }
    assert all(
        row["blocking_reference_count"] == 0
        for row in (*first.items, *final.items)
    )

    tampered = first.next_cursor[:-1] + (
        "A" if first.next_cursor[-1] != "A" else "B"
    )
    with pytest.raises(PurgeValidationError):
        service.list_eligible(
            PurgeEligiblePageCommand(
                principal=fixture.principal("admin"),
                request_id=_uuid("purge-queue:tampered"),
                cursor=tampered,
                limit=1,
            )
        )
    with pytest.raises(PurgeAuthorizationError):
        service.list_eligible(
            PurgeEligiblePageCommand(
                principal=fixture.principal("editor"),
                request_id=_uuid("purge-queue:nonadmin"),
            )
        )


@pytest.mark.scenario("LIFE-009")
def test_admin_can_purge_an_eligible_item_from_a_frozen_space(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    _add_admin(fixture)
    _make_purge_eligible(fixture, label="frozen-space")
    connection = fixture.factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """UPDATE knowledge_space SET status='frozen',write_enabled=0,
                          updated_at=updated_at+1,row_version=row_version+1
                     WHERE id=?""",
                (fixture.space_id,),
            )
    finally:
        connection.close()

    service = _purge_service(fixture)
    preview = service.preview(
        PurgePreviewCommand(
            principal=fixture.principal("admin"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            request_id=_uuid("purge-frozen:preview"),
        )
    )
    assert preview.eligible is True
    result = service.purge(
        _purge_command(fixture, preview, label="frozen-space")
    )
    assert result.status == "purged_tombstone"


@pytest.mark.scenario("LIFE-007")
def test_purge_preview_never_schedules_and_rejects_before_ninety_days(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    _add_admin(fixture)
    _service(fixture).deactivate(_deactivate_command(fixture, label="purge-too-early"))
    preview = _purge_preview(fixture, label="too-early")

    assert preview.eligible is False
    assert preview.blocking_references == ()
    assert preview.confirmation_text == f"PURGE {fixture.item_id}"
    before = _state(fixture)
    with pytest.raises(PurgeServiceError):
        _purge_service(fixture).purge(
            _purge_command(fixture, preview, label="too-early", row_version=3)
        )
    after = _state(fixture)
    assert after["item"] == before["item"]
    assert after["counts"]["knowledge_revision_body"] == before["counts"][
        "knowledge_revision_body"
    ]
    assert after["counts"]["durable_job"] == before["counts"]["durable_job"]


@pytest.mark.scenario("LIFE-008")
def test_only_active_admin_can_preview_or_execute_purge(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    _add_admin(fixture)
    _make_purge_eligible(fixture)
    with pytest.raises(PurgeAuthorizationError):
        _purge_service(fixture).preview(
            PurgePreviewCommand(
                principal=fixture.principal("editor"),
                space_id=fixture.space_id,
                item_id=fixture.item_id,
                request_id=_uuid("preview-request:non-admin"),
            )
        )
    preview = _purge_preview(fixture)
    with pytest.raises(PurgeAuthorizationError):
        _purge_service(fixture).purge(
            _purge_command(fixture, preview, actor="editor", label="non-admin")
        )


@pytest.mark.scenario("LIFE-009")
@pytest.mark.scenario("LIFE-010")
def test_eligible_admin_purge_retains_metadata_and_enqueues_only_blob_gc(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    _add_admin(fixture)
    _make_purge_eligible(fixture)
    service = _purge_service(fixture)
    preview = service.preview(
        PurgePreviewCommand(
            fixture.principal("admin"),
            fixture.space_id,
            fixture.item_id,
            _uuid("preview-request:eligible"),
        )
    )
    assert preview.eligible is True
    assert "revision_metadata" in preview.retained_categories
    assert "revision_bodies" in preview.deleted_categories
    before = _state(fixture)
    command = _purge_command(fixture, preview, label="eligible")
    result = service.purge(command)
    replay = service.purge(replace(command, request_id=_uuid("purge-request:eligible-replay")))

    assert replay == replace(result, request_id=_uuid("purge-request:eligible-replay"))
    assert result.status == "purged_tombstone"
    assert result.row_version == 4
    connection = fixture.factory.open(read_only=True)
    try:
        item = connection.execute(
            """SELECT lifecycle_status,current_revision_id,row_version
                 FROM knowledge_item WHERE id=?""",
            (fixture.item_id,),
        ).fetchone()
        assert tuple(item) == ("purged_tombstone", None, 4)
        assert connection.execute(
            "SELECT count(*) FROM knowledge_revision WHERE item_id=?",
            (fixture.item_id,),
        ).fetchone()[0] >= 1
        assert connection.execute(
            "SELECT count(*) FROM knowledge_revision_body WHERE item_id=?",
            (fixture.item_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM item_source WHERE item_id=?",
            (fixture.item_id,),
        ).fetchone()[0] >= 1
        assert connection.execute(
            "SELECT count(*) FROM search_document WHERE item_id=?",
            (fixture.item_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT purged_by,purged_at FROM item_deactivation_episode WHERE item_id=?",
            (fixture.item_id,),
        ).fetchone()[0] == "admin"
        assert connection.execute(
            "SELECT count(*) FROM durable_job"
        ).fetchone()[0] == before["counts"]["durable_job"]
        assert connection.execute(
            "SELECT count(*) FROM blob_gc_candidate"
        ).fetchone()[0] == preview.sizes["candidate_blob_count"]
    finally:
        connection.close()
    _assert_postconditions(fixture.factory)


@pytest.mark.scenario("LIFE-009")
def test_purge_preview_is_invalidated_by_a_new_active_reference(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    _add_admin(fixture)
    _make_purge_eligible(fixture)
    preview = _purge_preview(fixture, label="stale-reference")
    other_item, other_revision = _publish(
        fixture.factory,
        ScopeResolver(_flags()),
        epoch=fixture.epochs["editor"],
        label="purge-reference-target",
        markdown="# Target\nbody\n",
    )
    connection = fixture.factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO item_relation(
                     id,source_item_id,source_revision_id,target_item_id,
                     target_revision_id,relation_type,relation_status,evidence_json,
                     created_by,updated_by,created_at,updated_at,row_version
                   ) VALUES (?,?,?,?,?,'supersedes','active','{}','editor','editor',1,1,1)""",
                (
                    _uuid("purge-active-reference"),
                    fixture.item_id,
                    fixture.revision_id,
                    other_item,
                    other_revision,
                ),
            )
    finally:
        connection.close()
    with pytest.raises(PurgeServiceError) as raised:
        _purge_service(fixture).purge(
            _purge_command(fixture, preview, label="stale-reference")
        )
    assert raised.value.details["blocking_references"][0]["kind"] == "item_relation"
    assert _state(fixture)["item"][0] == "inactive"


@pytest.mark.scenario("LIFE-009")
def test_purge_tombstones_item_but_retains_a_shared_source_artifact_body(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    _add_admin(fixture)
    other_item, other_revision = _publish(
        fixture.factory,
        ScopeResolver(_flags()),
        epoch=fixture.epochs["editor"],
        label="purge-shared-artifact-item",
        markdown="# Independent survivor\nbody\n",
    )
    connection = fixture.factory.open()
    try:
        with transaction(connection):
            source = connection.execute(
                """SELECT artifact_id,extraction_snapshot_id FROM item_source
                     WHERE item_id=? ORDER BY created_at,id LIMIT 1""",
                (fixture.item_id,),
            ).fetchone()
            assert source is not None
            connection.execute(
                """INSERT INTO item_source(
                     id,item_id,revision_id,artifact_id,extraction_snapshot_id,
                     role,created_by,created_at
                   ) VALUES (?,?,?,?,?,'evidence','editor',500)""",
                (
                    _uuid("purge-shared-source"),
                    other_item,
                    other_revision,
                    source[0],
                    source[1],
                ),
            )
    finally:
        connection.close()
    _make_purge_eligible(fixture, label="shared-artifact")
    preview = _purge_preview(fixture, label="shared-artifact")
    assert preview.eligible is True
    artifact_blockers = [
        value
        for value in preview.blocking_references
        if value["scope"] == "artifact_body"
    ]
    assert any(
        value["kind"] == "surviving_item"
        and value["reference_id"] == other_item
        for value in artifact_blockers
    )
    assert "referenced_artifact_bodies" in preview.retained_categories
    _purge_service(fixture).purge(
        _purge_command(fixture, preview, label="shared-artifact")
    )
    connection = fixture.factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT lifecycle_status FROM knowledge_item WHERE id=?",
            (fixture.item_id,),
        ).fetchone()[0] == "purged_tombstone"
        retained = connection.execute(
            """SELECT a.body_retention_status,
                      (SELECT count(*) FROM artifact_blob_body bb
                        JOIN artifact_blob b ON b.id=bb.artifact_blob_id
                       WHERE b.artifact_id=a.id)
                 FROM source_artifact a JOIN item_source s ON s.artifact_id=a.id
                WHERE s.item_id=? ORDER BY s.created_at,s.id LIMIT 1""",
            (fixture.item_id,),
        ).fetchone()
        assert tuple(retained) == ("present", 1)
    finally:
        connection.close()
    _assert_postconditions(fixture.factory)


@pytest.mark.scenario("LIFE-009")
def test_purge_token_and_confirmation_are_strict_and_never_persisted(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    _add_admin(fixture)
    _make_purge_eligible(fixture, label="token")
    preview = _purge_preview(fixture, label="token")
    service = _purge_service(fixture)
    command = _purge_command(fixture, preview, label="token")
    with pytest.raises(PurgeServiceError):
        service.purge(replace(command, confirmation="purge"))
    with pytest.raises(PurgeServiceError):
        service.purge(
            replace(
                command,
                preview_token=preview.preview_token[:-1]
                + ("A" if preview.preview_token[-1] != "A" else "B"),
            )
        )
    assert preview.preview_token not in repr(preview)
    assert preview.preview_token not in repr(command)
    connection = fixture.factory.open(read_only=True)
    try:
        for table in ("operation_dedup", "audit_event", "outbox_event"):
            rows = connection.execute(f"SELECT * FROM {table}").fetchall()
            assert preview.preview_token not in repr([tuple(row) for row in rows])
    finally:
        connection.close()


@pytest.mark.scenario("LIFE-009")
@pytest.mark.parametrize(
    "name",
    (
        "lifecycle.purge_after_episode",
        "lifecycle.purge_after_tombstone",
        "lifecycle.purge_after_fts_remove",
        "lifecycle.purge_after_body_delete",
        "lifecycle.purge_after_gc_enqueue",
        "lifecycle.purge_after_audit",
        "lifecycle.purge_after_outbox",
        "lifecycle.purge_before_commit",
    ),
)
def test_purge_failpoints_roll_back_every_domain_effect(
    isolated_knowledge_layout,
    name: str,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    _add_admin(fixture)
    _make_purge_eligible(fixture, label=name)
    preview = _purge_preview(fixture, label=name)
    before = _state(fixture)

    def failpoint(observed: str) -> None:
        if observed == name:
            raise PurgeFailpointTriggered(name)

    with pytest.raises(PurgeFailpointTriggered):
        _purge_service(fixture, failpoint=failpoint).purge(
            _purge_command(fixture, preview, label=name)
        )
    assert _state(fixture) == before
