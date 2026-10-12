from __future__ import annotations

import base64
from dataclasses import dataclass, replace
import hashlib
import json
import sqlite3
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import (
    ConnectionFactory,
    ManagedConnection,
    transaction,
)
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.migrations.v001_invariants import (
    POSTCONDITION_DEFINITIONS,
)
from code_assist_v1.knowledge_core.repositories.artifacts import StagedVerifiedBlob
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
    ForceReleaseLeaseCommand,
    ImmutableItemEditService,
    LeaseFailpointTriggered,
    LeaseHmacKeyRing,
    LeaseIdempotencyError,
    LeaseLostError,
    LeaseRevisionConflictError,
    LeaseScopeStaleError,
    SaveItemCommand,
)
from code_assist_v1.knowledge_core.services.management_reads import (
    CanonicalManagementReadService,
    ItemManagementReadRequest,
    ItemRevisionCompareRequest,
    ItemRevisionPageRequest,
    ItemRevisionReadRequest,
)
from code_assist_v1.knowledge_core.services.publication import (
    CreateManualPreviewCommand,
    PublicationPreviewService,
    PublicationService,
    PublishPreviewCommand,
)
from code_assist_v1.knowledge_core.services.spaces import space_id_for_owner


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("7b0c421e-8dcb-4bf8-938d-d3f35365dac2")
_KEY = bytes(range(32))


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _token(label: str) -> str:
    raw = hashlib.sha256(label.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


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
            blob=StagedVerifiedBlob(
                sha256=expected_sha256,
                size_bytes=len(content),
                media_type=media_type,
                relative_path=(
                    f"{expected_sha256[:2]}/{expected_sha256[2:4]}/{expected_sha256}"
                ),
                created_at_ms=effective_at_ms,
                verified_at_ms=effective_at_ms,
            ),
            lease=_BlobLease(),
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
    part_id: str
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
        app_version="p5.2-item-edit-test",
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
    result = PublicationService(factory, resolver).publish(
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
    target = result.targets[0]
    return str(target["item_id"]), str(target["resolved_revision_id"])


def _seed(layout) -> _Fixture:
    factory = _factory(layout)
    users = UserRepository()
    organization = OrganizationRepository()
    spaces = SpaceRepository()
    team_id, part_id, space_id = _uuid("team"), _uuid("part"), _uuid("part-space")
    epochs: dict[str, int] = {}
    connection = factory.open()
    try:
        with transaction(connection):
            for user_id, role in (
                ("editor", "user"),
                ("member", "user"),
                ("leader", "user"),
                ("admin", "admin"),
            ):
                users.import_legacy(
                    connection,
                    LegacyUserRecord(
                        id=user_id,
                        display_name=user_id.title(),
                        password_hash=f"scrypt:p5.2:{user_id}",
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
                name="Edit Team",
                actor_user_id="leader",
                now_ms=110,
            )
            organization.create_unit(
                connection,
                unit_id=part_id,
                kind="part",
                parent_id=team_id,
                name="Edit Part",
                actor_user_id="leader",
                now_ms=111,
            )
            for index, user_id in enumerate(("editor", "member", "leader", "admin"), 1):
                organization.open_membership(
                    connection,
                    membership_id=_uuid(f"membership:{user_id}"),
                    user_id=user_id,
                    part_id=part_id,
                    assigned_by="leader",
                    reason="P5.2 fixture",
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
                reason="P5.2 fixture",
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
        markdown="# Initial\n",
    )
    return _Fixture(factory, part_id, space_id, item_id, revision_id, epochs)


def _service(fixture: _Fixture, *, stale_after_ms: int = 300_000, failpoint=None):
    return ImmutableItemEditService(
        fixture.factory,
        key_ring=LeaseHmacKeyRing(active_key_id="lease-v1", keys={"lease-v1": _KEY}),
        scope_resolver=ScopeResolver(_flags()),
        stale_after_ms=stale_after_ms,
        absolute_after_ms=7_200_000,
        failpoint=failpoint,
    )


def _acquire(fixture: _Fixture, *, service=None, label: str = "edit"):
    service = service or _service(fixture)
    token = _token(label)
    command = AcquireLeaseCommand(
        principal=fixture.principal("editor"),
        space_id=fixture.space_id,
        item_id=fixture.item_id,
        editor_session_id=_uuid(f"session:{label}"),
        operation_id=_uuid(f"acquire:{label}"),
        request_id=_uuid(f"acquire-request:{label}"),
        raw_token=token,
        request_meta={},
    )
    return service, command, service.acquire(command)


def _save_command(
    fixture: _Fixture,
    acquire_command: AcquireLeaseCommand,
    acquire_result,
    *,
    label: str = "edit",
    expected_revision_id: str | None = None,
    expected_row_version: int = 1,
) -> SaveItemCommand:
    return SaveItemCommand(
        principal=acquire_command.principal,
        space_id=fixture.space_id,
        item_id=fixture.item_id,
        editor_session_id=acquire_command.editor_session_id,
        lease_id=acquire_result.lease_id,
        fencing_generation=acquire_result.fencing_generation,
        expected_item_row_version=expected_row_version,
        expected_revision_id=expected_revision_id or fixture.revision_id,
        operation_id=_uuid(f"save:{label}"),
        request_id=_uuid(f"save-request:{label}"),
        markdown="# Edited\r\nbody\n",
        reason="Reviewed item edit",
        raw_token=acquire_command.raw_token,
        request_meta={"method": "PUT", "path": "/items/item"},
    )


def _make_published(fixture: _Fixture, *, label: str) -> None:
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
                (fixture.revision_id, fixture.item_id),
            ).fetchone()
            assert revision is not None and revision[1] is not None
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
                     'space_editor','agent_hidden','publish',?,?,NULL,NULL,'p5.2','v1','r1',
                     NULL,NULL,NULL,'{}','{}',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
                     'user','editor',310,310,1)""",
                (
                    finding_id,
                    fixture.space_id,
                    fixture.item_id,
                    fixture.revision_id,
                ),
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
                    fixture.item_id,
                    fixture.space_id,
                    fixture.revision_id,
                    fixture.revision_id,
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
                (transition_id, fixture.item_id),
            )
            connection.execute(
                """INSERT INTO search_document(
                     item_id,space_id,revision_id,title,content_md,updated_at
                   ) VALUES (?,?,?,?,?,330)""",
                (
                    fixture.item_id,
                    fixture.space_id,
                    fixture.revision_id,
                    revision[0],
                    revision[1],
                ),
            )
    finally:
        connection.close()


def _counts(factory: ConnectionFactory) -> dict[str, int]:
    tables = (
        "knowledge_revision",
        "knowledge_revision_body",
        "item_curation_transition",
        "durable_job",
        "operation_dedup",
        "audit_event",
        "outbox_event",
    )
    connection = factory.open(read_only=True)
    try:
        return {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in tables
        }
    finally:
        connection.close()


def _edit_state(factory: ConnectionFactory, fixture: _Fixture) -> dict[str, object]:
    tables = (
        "knowledge_revision",
        "knowledge_revision_body",
        "item_curation_transition",
        "durable_job",
        "operation_dedup",
        "audit_event",
        "outbox_event",
        "curation_finding",
        "change_proposal",
        "knowledge_edge",
        "search_document",
    )
    connection = factory.open(read_only=True)
    try:
        return {
            "counts": {
                table: int(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                )
                for table in tables
            },
            "edges": tuple(
                tuple(row)
                for row in connection.execute(
                    """SELECT id,edge_status,needs_revalidation,row_version
                         FROM knowledge_edge ORDER BY id"""
                ).fetchall()
            ),
            "findings": tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT id,status,row_version FROM curation_finding ORDER BY id"
                ).fetchall()
            ),
            "item": tuple(
                connection.execute(
                    """SELECT current_revision_id,curation_status,row_version
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
            "proposals": tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT id,state,row_version FROM change_proposal ORDER BY id"
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


class _ItemEditSqlFailpoint(RuntimeError):
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


@pytest.mark.scenario("LEASE-001")
@pytest.mark.scenario("REV-002")
def test_save_creates_one_immutable_revision_rescan_event_and_release(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    service, acquire_command, acquired = _acquire(fixture)
    before = _counts(fixture.factory)
    command = _save_command(fixture, acquire_command, acquired)

    result = service.save(command)
    replay = service.save(replace(command, request_id=_uuid("save-replay-request")))

    assert replay == replace(result, request_id=_uuid("save-replay-request"))
    assert result.status == "updated"
    assert result.row_version == 2
    after = _counts(fixture.factory)
    assert after["knowledge_revision"] == before["knowledge_revision"] + 1
    assert after["knowledge_revision_body"] == before["knowledge_revision_body"] + 1
    assert after["item_curation_transition"] == before["item_curation_transition"] + 1
    assert after["durable_job"] == before["durable_job"] + 1
    assert after["audit_event"] == before["audit_event"] + 1
    assert after["outbox_event"] == before["outbox_event"] + 1
    assert after["operation_dedup"] == before["operation_dedup"] + 2

    connection = fixture.factory.open(read_only=True)
    try:
        revision = connection.execute(
            """SELECT revision_no,parent_revision_id,base_revision_id,title,
                      raw_content_sha256,normalized_content_sha256,
                      content_size_bytes,normalizer_version,change_kind,
                      change_reason,created_by
                 FROM knowledge_revision WHERE id=?""",
            (result.revision_id,),
        ).fetchone()
        assert revision is not None
        assert tuple(revision[:4]) == (2, fixture.revision_id, fixture.revision_id, "Initial")
        raw = command.markdown.encode("utf-8")
        normalized = command.markdown.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
        assert revision[4] == hashlib.sha256(raw).hexdigest()
        assert revision[5] == hashlib.sha256(normalized).hexdigest()
        assert tuple(revision[6:]) == (
            len(raw),
            "markdown_lf_v1",
            "edit",
            command.reason,
            "editor",
        )
        body = connection.execute(
            "SELECT content_md,content_blob_hash,content_size_bytes,content_sha256 FROM knowledge_revision_body WHERE revision_id=?",
            (result.revision_id,),
        ).fetchone()
        assert body is not None
        assert tuple(body) == (
            command.markdown,
            None,
            len(raw),
            hashlib.sha256(raw).hexdigest(),
        )
        item = connection.execute(
            "SELECT current_revision_id,curation_status,row_version FROM knowledge_item WHERE id=?",
            (fixture.item_id,),
        ).fetchone()
        assert tuple(item) == (result.revision_id, "provisional", 2)
        lease = connection.execute(
            "SELECT holder_user_id,fencing_generation FROM item_lease WHERE item_id=?",
            (fixture.item_id,),
        ).fetchone()
        assert tuple(lease) == (None, acquired.fencing_generation)
        job = connection.execute(
            """SELECT id,state,input_ref_json FROM durable_job
                 WHERE job_type='curation.rescan'
                   AND json_extract(input_ref_json,'$.revision_id')=?""",
            (result.revision_id,),
        ).fetchone()
        assert job is not None and job[1] == "queued"
        assert json.loads(job[2]) == {
            "item_id": fixture.item_id,
            "revision_id": result.revision_id,
            "rule_version": "curation-rescan-v1",
        }
        audit = connection.execute(
            "SELECT action,base_revision_id,new_revision_id,details_json FROM audit_event WHERE action='knowledge_item.edit'"
        ).fetchone()
        assert audit is not None
        assert tuple(audit[:3]) == (
            "knowledge_item.edit",
            fixture.revision_id,
            result.revision_id,
        )
        assert json.loads(audit[3])["rescan_job_id"] == job[0]
    finally:
        connection.close()


@pytest.mark.scenario("REV-001")
@pytest.mark.scenario("REV-002")
def test_p54_activity_reader_exposes_exact_revision_history_body_and_safe_diff(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    editor, acquire_command, acquired = _acquire(fixture, label="p54-history")
    reader = CanonicalManagementReadService(
        fixture.factory,
        scope_resolver=ScopeResolver(_flags()),
    )
    before_save = reader.get_item(
        ItemManagementReadRequest(
            actor_user_id="editor",
            actor_scope_epoch=fixture.epochs["editor"],
            space_id=fixture.space_id,
            item_id=fixture.item_id,
        )
    )
    assert before_save.record.lease is not None
    assert before_save.record.lease.holder_user_id == "editor"
    assert before_save.record.lease.holder_display_name == "Editor"
    assert before_save.record.lease.fencing_generation == acquired.fencing_generation
    assert before_save.markdown == "# Initial\n"
    assert "edit" in before_save.capabilities
    assert before_save.content_age_seconds >= 0

    command = _save_command(
        fixture,
        acquire_command,
        acquired,
        label="p54-history",
    )
    saved = editor.save(command)
    read_counts = _counts(fixture.factory)
    page = reader.list_revisions(
        ItemRevisionPageRequest(
            actor_user_id="editor",
            actor_scope_epoch=fixture.epochs["editor"],
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            limit=50,
        )
    )
    assert page.has_more is False
    assert [revision.id for revision in page.items] == [
        saved.revision_id,
        fixture.revision_id,
    ]
    assert [revision.revision_no for revision in page.items] == [2, 1]
    assert page.items[0].created_by_user_id == "editor"
    assert page.items[0].created_by_display_name == "Editor"
    assert page.items[0].reason == "Reviewed item edit"
    assert page.items[0].parent_revision_id == fixture.revision_id
    assert page.items[0].body is not None
    first_page = reader.list_revisions(
        ItemRevisionPageRequest(
            actor_user_id="editor",
            actor_scope_epoch=fixture.epochs["editor"],
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            limit=1,
        )
    )
    assert first_page.has_more is True
    assert [row.id for row in first_page.items] == [saved.revision_id]
    second_page = reader.list_revisions(
        ItemRevisionPageRequest(
            actor_user_id="editor",
            actor_scope_epoch=fixture.epochs["editor"],
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            before_revision_no=first_page.items[-1].revision_no,
            before_revision_id=first_page.items[-1].id,
            limit=1,
        )
    )
    assert second_page.has_more is False
    assert [row.id for row in second_page.items] == [fixture.revision_id]

    old = reader.get_revision(
        ItemRevisionReadRequest(
            actor_user_id="editor",
            actor_scope_epoch=fixture.epochs["editor"],
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            revision_id=fixture.revision_id,
        )
    )
    current = reader.get_revision(
        ItemRevisionReadRequest(
            actor_user_id="editor",
            actor_scope_epoch=fixture.epochs["editor"],
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            revision_id=saved.revision_id,
        )
    )
    assert old.markdown == "# Initial\n"
    assert current.markdown == command.markdown

    compare_request = ItemRevisionCompareRequest(
        actor_user_id="editor",
        actor_scope_epoch=fixture.epochs["editor"],
        space_id=fixture.space_id,
        item_id=fixture.item_id,
        from_revision_id=fixture.revision_id,
        to_revision_id=saved.revision_id,
    )
    comparison = reader.compare_revisions(compare_request)
    assert comparison.diff_format == "unified-v1"
    assert len(comparison.diff_sha256) == 64
    assert comparison == reader.compare_revisions(compare_request)
    assert any(line.startswith("-") for hunk in comparison.hunks for line in hunk.lines)
    assert any(line.startswith("+") for hunk in comparison.hunks for line in hunk.lines)
    same = reader.compare_revisions(
        replace(
            compare_request,
            from_revision_id=saved.revision_id,
            to_revision_id=saved.revision_id,
        )
    )
    assert same.hunks == ()
    assert _counts(fixture.factory) == read_counts


def test_edit_of_published_item_removes_search_projection_atomically(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    _make_published(fixture, label="published-edit")
    service, acquire_command, acquired = _acquire(
        fixture, label="published-edit"
    )
    command = _save_command(
        fixture,
        acquire_command,
        acquired,
        label="published-edit",
        expected_row_version=2,
    )

    result = service.save(command)

    assert result.row_version == 3
    connection = fixture.factory.open(read_only=True)
    try:
        item = connection.execute(
            """SELECT current_revision_id,curation_status,row_version
                 FROM knowledge_item WHERE id=?""",
            (fixture.item_id,),
        ).fetchone()
        search_count = connection.execute(
            "SELECT count(*) FROM search_document WHERE item_id=?",
            (fixture.item_id,),
        ).fetchone()
        audit = connection.execute(
            """SELECT details_json FROM audit_event
                 WHERE action='knowledge_item.edit' AND item_id=?""",
            (fixture.item_id,),
        ).fetchone()
        assert tuple(item) == (result.revision_id, "provisional", 3)
        assert int(search_count[0]) == 0
        assert json.loads(audit[0])["removed_search_document_count"] == 1
    finally:
        connection.close()


@pytest.mark.scenario("REV-001")
def test_saved_revision_metadata_and_body_are_immutable(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    service, acquire_command, acquired = _acquire(fixture, label="immutable")
    result = service.save(
        _save_command(fixture, acquire_command, acquired, label="immutable")
    )
    connection = fixture.factory.open()
    try:
        before_revision = tuple(
            connection.execute(
                "SELECT * FROM knowledge_revision WHERE id=?",
                (result.revision_id,),
            ).fetchone()
        )
        before_body = tuple(
            connection.execute(
                "SELECT * FROM knowledge_revision_body WHERE revision_id=?",
                (result.revision_id,),
            ).fetchone()
        )
        for statement, parameters in (
            (
                "UPDATE knowledge_revision SET title='tampered' WHERE id=?",
                (result.revision_id,),
            ),
            (
                "DELETE FROM knowledge_revision WHERE id=?",
                (result.revision_id,),
            ),
            (
                "UPDATE knowledge_revision_body SET content_md='tampered' WHERE revision_id=?",
                (result.revision_id,),
            ),
            (
                "DELETE FROM knowledge_revision_body WHERE revision_id=?",
                (result.revision_id,),
            ),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                with transaction(connection):
                    connection.execute(statement, parameters)
        assert tuple(
            connection.execute(
                "SELECT * FROM knowledge_revision WHERE id=?",
                (result.revision_id,),
            ).fetchone()
        ) == before_revision
        assert tuple(
            connection.execute(
                "SELECT * FROM knowledge_revision_body WHERE revision_id=?",
                (result.revision_id,),
            ).fetchone()
        ) == before_body
    finally:
        connection.close()


@pytest.mark.scenario("LEASE-006")
@pytest.mark.scenario("LEASE-012")
def test_stale_expected_revision_preserves_draft_and_held_lease(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    service, acquire_command, acquired = _acquire(fixture, label="stale")
    before = _counts(fixture.factory)
    command = _save_command(
        fixture,
        acquire_command,
        acquired,
        label="stale",
        expected_revision_id=_uuid("stale-revision"),
    )
    with pytest.raises(LeaseRevisionConflictError) as captured:
        service.save(command)
    recovery = captured.value.details["draft_recovery"]
    assert recovery["preserve_local_draft"] is True
    assert recovery["force_overwrite_allowed"] is False
    assert recovery["actions"] == [
        "view_current",
        "copy_draft",
        "reacquire_and_compare",
    ]
    after = _counts(fixture.factory)
    assert after["knowledge_revision"] == before["knowledge_revision"]
    assert after["durable_job"] == before["durable_job"]
    assert after["operation_dedup"] == before["operation_dedup"] + 1
    connection = fixture.factory.open(read_only=True)
    try:
        lease = connection.execute(
            "SELECT holder_user_id,fencing_generation FROM item_lease WHERE item_id=?",
            (fixture.item_id,),
        ).fetchone()
        assert tuple(lease) == ("editor", acquired.fencing_generation)
    finally:
        connection.close()


@pytest.mark.scenario("LEASE-003")
def test_expired_lease_cannot_save(isolated_knowledge_layout) -> None:
    fixture = _seed(isolated_knowledge_layout)
    service, acquire_command, acquired = _acquire(
        fixture, service=_service(fixture, stale_after_ms=1), label="expired"
    )
    connection = fixture.factory.open()
    try:
        with transaction(connection):
            connection.execute(
                "UPDATE item_lease SET expires_at=heartbeat_at WHERE item_id=?",
                (fixture.item_id,),
            )
    finally:
        connection.close()
    with pytest.raises(LeaseLostError):
        service.save(_save_command(fixture, acquire_command, acquired, label="expired"))


@pytest.mark.scenario("LEASE-005")
def test_old_token_and_fence_after_force_release_can_never_save(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    service, acquire_command, acquired = _acquire(fixture, label="forced")
    service.force_release(
        ForceReleaseLeaseCommand(
            principal=fixture.principal("leader"),
            space_id=fixture.space_id,
            item_id=fixture.item_id,
            operation_id=_uuid("force-operation"),
            request_id=_uuid("force-request"),
            reason="Editor is unavailable",
            confirmation="FORCE_RELEASE",
        )
    )
    with pytest.raises(LeaseLostError):
        service.save(_save_command(fixture, acquire_command, acquired, label="forced"))


@pytest.mark.scenario("LEASE-010")
def test_membership_move_during_edit_rejects_later_save(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    service, acquire_command, acquired = _acquire(fixture, label="moved")
    connection = fixture.factory.open()
    try:
        with transaction(connection):
            organization = OrganizationRepository()
            users = UserRepository()
            membership = organization.get_current_membership(connection, "editor")
            assert membership is not None
            organization.close_current_membership(
                connection,
                user_id="editor",
                expected_membership_id=membership.id,
                closed_at_ms=999,
            )
            actor = users.require(connection, "editor")
            organization.compare_and_swap_user_part(
                connection,
                user_id="editor",
                expected_current_part_id=fixture.part_id,
                target_part_id=None,
                expected_scope_epoch=actor.scope_epoch,
                expected_row_version=actor.row_version,
                updated_at_ms=999,
            )
    finally:
        connection.close()
    with pytest.raises(LeaseScopeStaleError):
        service.save(_save_command(fixture, acquire_command, acquired, label="moved"))


@pytest.mark.parametrize(
    "failpoint",
    [
        "edit.after_rescan_enqueue",
        "edit.after_revision_insert",
        "edit.after_current_pointer_update",
        "edit.after_item_effect",
        "edit.after_lease_release",
        "edit.after_audit",
        "edit.after_outbox",
        "edit.before_commit",
    ],
)
def test_edit_failpoints_roll_back_revision_job_item_lease_audit_and_outbox(
    isolated_knowledge_layout,
    failpoint: str,
) -> None:
    fixture = _seed(isolated_knowledge_layout)

    def hit(name: str) -> None:
        if name == failpoint:
            raise LeaseFailpointTriggered(name)

    service = _service(fixture, failpoint=hit)
    service, acquire_command, acquired = _acquire(
        fixture, service=service, label=failpoint
    )
    before = _counts(fixture.factory)
    with pytest.raises(LeaseFailpointTriggered):
        service.save(
            _save_command(fixture, acquire_command, acquired, label=failpoint)
        )
    assert _counts(fixture.factory) == before
    connection = fixture.factory.open(read_only=True)
    try:
        item = connection.execute(
            "SELECT current_revision_id,row_version FROM knowledge_item WHERE id=?",
            (fixture.item_id,),
        ).fetchone()
        assert tuple(item) == (fixture.revision_id, 1)
        lease = connection.execute(
            "SELECT holder_user_id,fencing_generation FROM item_lease WHERE item_id=?",
            (fixture.item_id,),
        ).fetchone()
        assert tuple(lease) == ("editor", acquired.fencing_generation)
    finally:
        connection.close()


def test_edit_operation_id_binds_content_base_and_reason(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    service, acquire_command, acquired = _acquire(fixture, label="binding")
    command = _save_command(fixture, acquire_command, acquired, label="binding")
    service.save(command)
    after_success = _counts(fixture.factory)

    for changed in (
        replace(command, markdown="# Different\nbody\n"),
        replace(command, reason="A different reviewed reason"),
        replace(command, expected_item_row_version=2),
    ):
        with pytest.raises(LeaseIdempotencyError):
            service.save(changed)
        assert _counts(fixture.factory) == after_success


@pytest.mark.scenario("LEASE-011")
def test_item_save_never_echoes_or_persists_raw_token(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    service, acquire_command, acquired = _acquire(fixture, label="save-secret")
    command = _save_command(
        fixture, acquire_command, acquired, label="save-secret"
    )

    result = service.save(command)

    assert command.raw_token not in repr(command)
    assert command.markdown not in repr(command)
    assert command.raw_token not in json.dumps(result.as_dict(), sort_keys=True)
    for path in (
        isolated_knowledge_layout.core_db_path,
        isolated_knowledge_layout.core_db_path.with_name(
            isolated_knowledge_layout.core_db_path.name + "-wal"
        ),
        isolated_knowledge_layout.core_db_path.with_name(
            isolated_knowledge_layout.core_db_path.name + "-shm"
        ),
    ):
        if path.exists():
            assert command.raw_token.encode("ascii") not in path.read_bytes()


def test_every_item_edit_sql_write_boundary_rolls_back(
    isolated_knowledge_layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    service, acquire_command, acquired = _acquire(
        fixture, label="sql-failpoint"
    )
    command = _save_command(
        fixture, acquire_command, acquired, label="sql-failpoint"
    )
    baseline = _edit_state(fixture.factory, fixture)
    original_execute = ManagedConnection.execute
    original_executemany = ManagedConnection.executemany
    state: dict[str, object] = {
        "active": False,
        "fail_at": None,
        "seen": [],
        "stop_after_terminal": False,
    }

    def observe(sql: str) -> None:
        if not state["active"] or not _is_mutating_sql(sql):
            return
        seen = state["seen"]
        assert isinstance(seen, list)
        normalized = " ".join(sql.upper().split())
        seen.append(normalized)
        fail_at = state["fail_at"]
        if isinstance(fail_at, int) and len(seen) == fail_at:
            raise _ItemEditSqlFailpoint(f"item edit SQL write {fail_at}")
        if (
            state["stop_after_terminal"]
            and "UPDATE OPERATION_DEDUP" in normalized
            and "STATUS='COMMITTED'" in normalized
        ):
            raise _ItemEditSqlFailpoint("item edit terminal write discovery")

    def execute(connection, sql, parameters=()):
        cursor = original_execute(connection, sql, parameters)
        observe(sql)
        return cursor

    def executemany(connection, sql, parameters):
        cursor = original_executemany(connection, sql, parameters)
        observe(sql)
        return cursor

    monkeypatch.setattr(ManagedConnection, "execute", execute)
    monkeypatch.setattr(ManagedConnection, "executemany", executemany)

    state.update(active=True, fail_at=None, seen=[], stop_after_terminal=True)
    with pytest.raises(_ItemEditSqlFailpoint, match="terminal write discovery"):
        service.save(command)
    matrix = tuple(state["seen"])
    state["active"] = False
    print(f"item_edit_sql_write_boundaries={len(matrix)}")
    assert len(matrix) >= 13
    assert "UPDATE OPERATION_DEDUP" in matrix[-1]
    assert _edit_state(fixture.factory, fixture) == baseline
    _assert_postconditions(fixture.factory)

    for fail_at in range(1, len(matrix) + 1):
        state.update(
            active=True,
            fail_at=fail_at,
            seen=[],
            stop_after_terminal=False,
        )
        with pytest.raises(
            _ItemEditSqlFailpoint,
            match=rf"item edit SQL write {fail_at}",
        ):
            service.save(command)
        observed = tuple(state["seen"])
        state["active"] = False
        assert observed == matrix[:fail_at]
        assert _edit_state(fixture.factory, fixture) == baseline
        _assert_postconditions(fixture.factory)


def test_edit_obsoletes_fixed_revision_findings_and_revalidates_edges(
    isolated_knowledge_layout,
) -> None:
    fixture = _seed(isolated_knowledge_layout)
    target_item, target_revision = _publish(
        fixture.factory,
        ScopeResolver(_flags()),
        epoch=fixture.epochs["editor"],
        label="target",
        markdown="# Target\n",
    )
    finding_id, edge_id = _uuid("finding"), _uuid("edge")
    update_finding_id = _uuid("update-finding")
    proposal_id = _uuid("update-proposal")
    proposed_content = "# Proposed update\n"
    proposed_bytes = proposed_content.encode("utf-8")
    proposed_sha256 = hashlib.sha256(proposed_bytes).hexdigest()
    connection = fixture.factory.open()
    try:
        with transaction(connection):
            source = connection.execute(
                """SELECT artifact_id,extraction_snapshot_id
                     FROM item_source WHERE item_id=? AND revision_id=?""",
                (fixture.item_id, fixture.revision_id),
            ).fetchone()
            assert source is not None
            connection.execute(
                """INSERT INTO curation_finding(
                     id,space_id,finding_kind,risk_tier,severity,status,decision_mode,
                     required_reviewer,visibility_effect,proposed_action,
                     source_item_id,source_revision_id,target_item_id,target_revision_id,
                     detector_name,detector_version,rule_version,model_id,prompt_version,
                     confidence,evidence_json,proposed_action_json,reviewed_by,reviewed_at,
                     review_reason,applied_operation_id,applied_dedup_id,applied_job_id,
                     applied_by,applied_at,last_application_error_json,
                     created_actor_kind,created_by,created_at,updated_at,row_version
                   ) VALUES (?,?,'semantic_edge','T2','info','pending','human',
                     'space_editor','none','relation',?,?,?,?,'test','v1','r1',
                     NULL,NULL,NULL,'{}','{}',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
                     NULL,'user','editor',500,500,1)""",
                (
                    finding_id,
                    fixture.space_id,
                    fixture.item_id,
                    fixture.revision_id,
                    target_item,
                    target_revision,
                ),
            )
            connection.execute(
                """INSERT INTO curation_finding(
                     id,space_id,finding_kind,risk_tier,severity,status,decision_mode,
                     required_reviewer,visibility_effect,proposed_action,
                     source_item_id,source_revision_id,target_item_id,target_revision_id,
                     detector_name,detector_version,rule_version,model_id,prompt_version,
                     confidence,evidence_json,proposed_action_json,reviewed_by,reviewed_at,
                     review_reason,applied_operation_id,applied_dedup_id,applied_job_id,
                     applied_by,applied_at,last_application_error_json,
                     created_actor_kind,created_by,created_at,updated_at,row_version
                   ) VALUES (?,?,'update','T2','info','pending','human',
                     'space_editor','agent_hidden','update',?,?,NULL,NULL,'p5.2','v1',
                     'update-v1',NULL,NULL,NULL,'{}','{}',NULL,NULL,NULL,NULL,NULL,NULL,
                     NULL,NULL,NULL,'user','editor',500,500,1)""",
                (
                    update_finding_id,
                    fixture.space_id,
                    fixture.item_id,
                    fixture.revision_id,
                ),
            )
            connection.execute(
                """INSERT INTO change_proposal(
                     id,finding_id,finding_input_row_version,proposal_kind,
                     proposal_series_id,proposal_version,supersedes_proposal_id,
                     lineage_group_id,base_item_id,base_revision_id,source_item_id,
                     source_revision_id,target_item_id,target_revision_id,
                     source_artifact_id,extraction_snapshot_id,proposed_content_md,
                     proposed_content_blob_hash,proposed_content_size_bytes,
                     proposed_content_sha256,preview_sha256,state,applied_operation_id,
                     applied_dedup_id,last_application_error_json,created_by,updated_by,
                     created_at,updated_at,row_version
                   ) VALUES (?,?,1,'stable_source_update',?,1,NULL,NULL,?,?,NULL,NULL,
                     ?,?,?,?,?,NULL,?,?,?,'pending_review',NULL,NULL,NULL,
                     'editor','editor',510,510,1)""",
                (
                    proposal_id,
                    update_finding_id,
                    proposal_id,
                    fixture.item_id,
                    fixture.revision_id,
                    fixture.item_id,
                    fixture.revision_id,
                    source[0],
                    source[1],
                    proposed_content,
                    len(proposed_bytes),
                    proposed_sha256,
                    proposed_sha256,
                ),
            )
            connection.execute(
                """INSERT INTO knowledge_edge(
                     id,space_id,source_item_id,target_item_id,edge_type,edge_status,
                     origin,source_evidence_revision_id,target_evidence_revision_id,
                     approval_finding_id,evidence_json,needs_revalidation,created_by,
                     updated_by,created_at,updated_at,row_version
                   ) VALUES (?,?,?,?,'related','active','manual',?,?,NULL,'{}',0,
                     'editor','editor',500,500,1)""",
                (
                    edge_id,
                    fixture.space_id,
                    fixture.item_id,
                    target_item,
                    fixture.revision_id,
                    target_revision,
                ),
            )
    finally:
        connection.close()
    service, acquire_command, acquired = _acquire(fixture, label="revalidation")
    service.save(
        _save_command(fixture, acquire_command, acquired, label="revalidation")
    )
    connection = fixture.factory.open(read_only=True)
    try:
        finding = connection.execute(
            "SELECT status,row_version FROM curation_finding WHERE id=?",
            (finding_id,),
        ).fetchone()
        update_finding = connection.execute(
            "SELECT status,row_version FROM curation_finding WHERE id=?",
            (update_finding_id,),
        ).fetchone()
        proposal = connection.execute(
            "SELECT state,row_version FROM change_proposal WHERE id=?",
            (proposal_id,),
        ).fetchone()
        edge = connection.execute(
            "SELECT edge_status,needs_revalidation,row_version FROM knowledge_edge WHERE id=?",
            (edge_id,),
        ).fetchone()
        audit = connection.execute(
            "SELECT details_json FROM audit_event WHERE action='knowledge_item.edit'"
        ).fetchone()
        assert tuple(finding) == ("obsolete", 2)
        assert tuple(update_finding) == ("obsolete", 2)
        assert tuple(proposal) == ("obsolete", 2)
        assert tuple(edge) == ("inactive", 1, 2)
        details = json.loads(audit[0])
        assert details["obsoleted_finding_count"] == 2
        assert details["obsoleted_proposal_count"] == 1
        assert details["revalidation_edge_count"] == 1
    finally:
        connection.close()
