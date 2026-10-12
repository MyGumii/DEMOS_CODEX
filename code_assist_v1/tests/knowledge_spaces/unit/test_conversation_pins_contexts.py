from __future__ import annotations

import hashlib
from dataclasses import fields
from pathlib import Path
import sqlite3
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.artifacts import (
    ArtifactRepository,
    LegacyArtifactBundle,
    LegacyExtractionSpec,
    PreparedMarkdownBody,
    StagedVerifiedBlob,
    legacy_artifact_ids_for_record,
)
from code_assist_v1.knowledge_core.repositories.audit import AuditRepository
from code_assist_v1.knowledge_core.repositories.conversations import (
    ConversationCreate,
    ConversationPinCreate,
    ConversationRepository,
    ConversationScopeError,
)
from code_assist_v1.knowledge_core.repositories.items import (
    ItemRepository,
    LegacyMarkdownImportBundle,
    legacy_item_ids_for_record,
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
from code_assist_v1.knowledge_core.services.conversations import (
    AddConversationContextCommand,
    AddConversationPinCommand,
    ConversationContextResolution,
    ConversationContextResolveRequest,
    ConversationIdempotencyError,
    ConversationService,
    ConversationServiceScopeError,
    CreateConversationCommand,
    RemoveConversationContextCommand,
    RemoveConversationPinCommand,
)
from code_assist_v1.test_safety import create_isolated_test_layout


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("8f7598be-d687-4793-9a0e-a1cfcff6fd16")
_PLAN = "a" * 64


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _record(number: int) -> str:
    return f"rec_{number:024x}"


def _factory(tmp_path: Path) -> ConnectionFactory:
    layout = create_isolated_test_layout(tmp_path, user_id="fixture-user")
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path,
        CANONICAL_MIGRATION_CATALOG,
    )
    SingleMigrator(
        factory,
        app_version="p3.2-conversation-attachment-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _user(
    user_id: str, *, current_part_id: str | None = None
) -> LegacyUserRecord:
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.title(),
        password_hash=f"scrypt:p3.2:{user_id}",
        system_role="admin" if user_id == "admin" else "user",
        account_status="active",
        current_part_id=current_part_id,
        scope_epoch=1,
        created_at_ms=100,
        updated_at_ms=100,
    )


def _artifact_bundle(number: int, *, owner: str) -> LegacyArtifactBundle:
    record_id = _record(number)
    content = f"# source {number}\n".encode("utf-8")
    raw_sha = hashlib.sha256(content).hexdigest()
    ids = legacy_artifact_ids_for_record(_PLAN, record_id)
    return LegacyArtifactBundle(
        plan_sha256=_PLAN,
        inventory_record_id=record_id,
        ids=ids,
        owner_user_id=owner,
        origin_key=f"p2v1/root/{number:064x}/physical_file/0",
        raw_blob=StagedVerifiedBlob(
            sha256=raw_sha,
            size_bytes=len(content),
            media_type="text/markdown",
            relative_path=f"{raw_sha[:2]}/{raw_sha[2:4]}/{raw_sha}",
            created_at_ms=200 + number,
            verified_at_ms=201 + number,
        ),
        logical_name=f"source-{number}.md",
        original_filename=f"source-{number}.md",
        source_owner_snapshot_json='{"schema_version":1}',
        metadata_json='{"schema_version":1}',
        created_at_ms=300 + number,
        extraction=LegacyExtractionSpec(
            normalized_content_sha256=raw_sha,
            normalizer_version="markdown_lf_v1",
            extractor_name="p3.2-fixture",
            extractor_version="v1",
            warnings_json="[]",
            body=PreparedMarkdownBody(
                content_md=content.decode("utf-8"),
                content_blob_hash=None,
                content_size_bytes=len(content),
                content_sha256=raw_sha,
            ),
            created_at_ms=400 + number,
        ),
    )


def _item_bundle(
    source: LegacyArtifactBundle, *, owner: str, space_id: str
) -> LegacyMarkdownImportBundle:
    assert source.extraction is not None
    return LegacyMarkdownImportBundle(
        plan_sha256=source.plan_sha256,
        inventory_record_id=source.inventory_record_id,
        ids=legacy_item_ids_for_record(
            source.plan_sha256, source.inventory_record_id
        ),
        owner_user_id=owner,
        personal_space_id=space_id,
        artifact_id=source.ids.artifact_id,
        extraction_snapshot_id=source.ids.extraction_snapshot_id,
        title=f"Imported {source.inventory_record_id}",
        raw_content_sha256=source.raw_blob.sha256,
        normalized_content_sha256=source.extraction.normalized_content_sha256,
        content_size_bytes=source.raw_blob.size_bytes,
        normalizer_version=source.extraction.normalizer_version,
        metadata_json='{"migration":true}',
        change_reason="p3.2 fixture",
        body=source.extraction.body,
        created_at_ms=500 + int(source.inventory_record_id[-4:], 16),
    )


def _publish_item(
    connection,
    *,
    owner: str,
    space_id: str,
    item_id: str,
    revision_id: str,
    number: int,
) -> None:
    finding_id = _uuid(f"release-finding:{number}")
    dedup_id = _uuid(f"release-dedup:{number}")
    operation_id = _uuid(f"release-operation:{number}")
    transition_id = _uuid(f"release-transition:{number}")
    connection.execute(
        """INSERT INTO operation_dedup(
             id,actor_user_id,operation_type,operation_id,request_sha256,status,
             http_status,result_json,error_json,created_at,completed_at,last_seen_at
           ) VALUES (?,?,'item_curation',?,?,'in_progress',NULL,NULL,NULL,600,NULL,600)""",
        (dedup_id, owner, operation_id, "b" * 64),
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
             'space_editor','agent_hidden','publish',?,?,NULL,NULL,'p3.2','v1','r1',
             NULL,NULL,NULL,'{}','{}',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,
             'user',?,610,610,1)""",
        (finding_id, space_id, item_id, revision_id, owner),
    )
    connection.execute(
        """UPDATE curation_finding SET status='approved',reviewed_by=?,reviewed_at=620,
             review_reason='approved',updated_at=620,row_version=2 WHERE id=?""",
        (owner, finding_id),
    )
    connection.execute(
        """UPDATE curation_finding SET status='applied',applied_operation_id=?,
             applied_dedup_id=?,applied_by=?,applied_at=630,updated_at=630,
             row_version=3 WHERE id=?""",
        (operation_id, dedup_id, owner, finding_id),
    )
    connection.execute(
        """INSERT INTO item_curation_transition(
             id,item_id,space_id,transition_kind,from_status,to_status,
             base_revision_id,result_revision_id,finding_id,rescan_job_id,
             decision_job_id,dedup_id,operation_id,actor_kind,actor_user_id,
             result_item_row_version,reason,created_at
           ) VALUES (?,?,?,'finding_decision','provisional','published',?,?,?,NULL,
             NULL,?,?,'user',?,2,'publish',630)""",
        (
            transition_id,
            item_id,
            space_id,
            revision_id,
            revision_id,
            finding_id,
            dedup_id,
            operation_id,
            owner,
        ),
    )
    connection.execute(
        """UPDATE knowledge_item SET curation_status='published',
             last_curation_transition_id=?,updated_by=?,updated_at=630,row_version=2
             WHERE id=?""",
        (transition_id, owner, item_id),
    )
    body = connection.execute(
        """SELECT r.title,b.content_md FROM knowledge_revision AS r
             JOIN knowledge_revision_body AS b ON b.revision_id=r.id
             WHERE r.id=? AND r.item_id=?""",
        (revision_id, item_id),
    ).fetchone()
    assert body is not None and body[1] is not None
    connection.execute(
        """INSERT INTO search_document(
             item_id,space_id,revision_id,title,content_md,updated_at
           ) VALUES (?,?,?,?,?,630)""",
        (item_id, space_id, revision_id, body[0], body[1]),
    )


def _seed(factory: ConnectionFactory) -> dict[str, object]:
    result: dict[str, object] = {
        "member_space": _uuid("member-space"),
        "other_space": _uuid("other-space"),
    }
    connection = factory.open()
    try:
        with transaction(connection):
            users = UserRepository()
            spaces = SpaceRepository()
            artifacts = ArtifactRepository()
            items = ItemRepository()
            for user_id, key in (
                ("member01", "member_space"),
                ("other01", "other_space"),
            ):
                users.import_legacy(connection, _user(user_id))
                spaces.ensure_personal(
                    connection,
                    space_id=str(result[key]),
                    user_id=user_id,
                    created_at_ms=150,
                )
            member_source = _artifact_bundle(1, owner="member01")
            other_source = _artifact_bundle(2, owner="other01")
            artifacts.put_legacy_bundle(connection, member_source)
            artifacts.put_legacy_bundle(connection, other_source)
            member_item = items.put_legacy_markdown_import(
                connection,
                _item_bundle(
                    member_source,
                    owner="member01",
                    space_id=str(result["member_space"]),
                ),
            )
            other_item = items.put_legacy_markdown_import(
                connection,
                _item_bundle(
                    other_source,
                    owner="other01",
                    space_id=str(result["other_space"]),
                ),
            )
            _publish_item(
                connection,
                owner="member01",
                space_id=str(result["member_space"]),
                item_id=member_item.item_id,
                revision_id=member_item.revision_id,
                number=1,
            )
            _publish_item(
                connection,
                owner="other01",
                space_id=str(result["other_space"]),
                item_id=other_item.item_id,
                revision_id=other_item.revision_id,
                number=2,
            )
            result.update(
                {
                    "artifact_id": member_source.ids.artifact_id,
                    "artifact_sha": member_source.raw_blob.sha256,
                    "item_id": member_item.item_id,
                    "revision_id": member_item.revision_id,
                    "other_item_id": other_item.item_id,
                    "other_revision_id": other_item.revision_id,
                }
            )
    finally:
        connection.close()
    return result


def _seed_part(factory: ConnectionFactory) -> dict[str, str]:
    values = {
        "team": _uuid("part-team"),
        "part": _uuid("part-unit"),
        "part_space": _uuid("part-space"),
        "personal_space": _uuid("part-member-personal-space"),
        "membership": _uuid("part-membership"),
        "item_id": _uuid("part-item"),
        "revision_id": _uuid("part-revision"),
    }
    connection = factory.open()
    try:
        with transaction(connection):
            users = UserRepository()
            organization = OrganizationRepository()
            spaces = SpaceRepository()
            users.import_legacy(connection, _user("admin"))
            organization.create_unit(
                connection,
                unit_id=values["team"],
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="P3.2 Team",
                actor_user_id="admin",
                now_ms=110,
            )
            organization.create_unit(
                connection,
                unit_id=values["part"],
                kind="part",
                parent_id=values["team"],
                name="P3.2 Part",
                actor_user_id="admin",
                now_ms=120,
            )
            users.import_legacy(
                connection,
                _user("member01", current_part_id=values["part"]),
            )
            organization.open_membership(
                connection,
                membership_id=values["membership"],
                user_id="member01",
                part_id=values["part"],
                assigned_by="admin",
                reason="P3.2 fixture",
                valid_from_ms=130,
            )
            spaces.ensure_personal(
                connection,
                space_id=values["personal_space"],
                user_id="member01",
                created_at_ms=140,
            )
            spaces.ensure_part(
                connection,
                space_id=values["part_space"],
                part_id=values["part"],
                created_at_ms=141,
            )
            body = "# shared part source\n"
            body_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
            connection.execute(
                """INSERT INTO knowledge_item(
                     id,space_id,lineage_group_id,lifecycle_status,curation_status,
                     current_revision_id,last_curation_transition_id,
                     latest_deactivation_episode_id,redirect_item_id,created_by,
                     updated_by,created_at,updated_at,row_version
                   ) VALUES (?,?,NULL,'active','provisional',?,NULL,NULL,NULL,
                     'member01','member01',200,200,1)""",
                (values["item_id"], values["part_space"], values["revision_id"]),
            )
            connection.execute(
                """INSERT INTO knowledge_revision(
                     id,item_id,revision_no,parent_revision_id,base_revision_id,title,
                     raw_content_sha256,normalized_content_sha256,content_size_bytes,
                     normalizer_version,metadata_json,change_kind,change_reason,
                     created_by,created_at
                   ) VALUES (?,?,1,NULL,NULL,'Shared source',?,?,?,
                     'markdown_lf_v1','{}','initial','fixture','member01',200)""",
                (
                    values["revision_id"],
                    values["item_id"],
                    body_sha,
                    body_sha,
                    len(body.encode("utf-8")),
                ),
            )
            connection.execute(
                """INSERT INTO knowledge_revision_body(
                     revision_id,item_id,content_md,content_blob_hash,
                     content_size_bytes,content_sha256,created_at
                   ) VALUES (?,?,?,NULL,?,?,200)""",
                (
                    values["revision_id"],
                    values["item_id"],
                    body,
                    len(body.encode("utf-8")),
                    body_sha,
                ),
            )
            _publish_item(
                connection,
                owner="member01",
                space_id=values["part_space"],
                item_id=values["item_id"],
                revision_id=values["revision_id"],
                number=3,
            )
    finally:
        connection.close()
    return values


def _conversation(factory: ConnectionFactory, ids: dict[str, object]) -> str:
    repository = ConversationRepository()
    conversation_id = _uuid("conversation")
    connection = factory.open()
    try:
        with transaction(connection):
            repository.create(
                connection,
                ConversationCreate(
                    conversation_id=conversation_id,
                    owner_user_id="member01",
                    space_id=str(ids["member_space"]),
                    captured_scope_epoch=1,
                    title="P3.2",
                    metadata_json='{"source":"runtime"}',
                    created_at_ms=700,
                ),
            )
    finally:
        connection.close()
    return conversation_id


def _service_conversation(factory: ConnectionFactory, ids: dict[str, object]) -> str:
    result = ConversationService(factory).create(
        CreateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("create-operation"),
            request_id=_uuid("create-request"),
            space_id=str(ids["member_space"]),
            title="P3.2 service",
        )
    )
    return result.conversation.id


class _Resolver:
    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        space_id: str,
        source_artifact_id: str | None = None,
        snapshot_sha256: str | None = None,
    ) -> None:
        self.factory = factory
        self.space_id = space_id
        self.source_artifact_id = source_artifact_id
        self.snapshot_sha256 = snapshot_sha256
        self.calls: list[ConversationContextResolveRequest] = []

    def resolve(
        self, request: ConversationContextResolveRequest
    ) -> ConversationContextResolution:
        probe = self.factory.open()
        try:
            assert probe.in_transaction is False
        finally:
            probe.close()
        self.calls.append(request)
        return ConversationContextResolution(
            space_id=self.space_id,
            binding_type=request.binding_type,
            binding_mode=request.binding_mode,
            external_key=request.external_key,
            source_artifact_id=self.source_artifact_id,
            snapshot_sha256=self.snapshot_sha256,
            display_name="Explicit fixture context",
        )


class _FailingAudit(AuditRepository):
    def append(self, connection, draft):  # type: ignore[override]
        raise RuntimeError("fixture audit failure")


def _count(connection, table: str, where: str = "1") -> int:
    row = connection.execute(f"SELECT count(*) FROM {table} WHERE {where}").fetchone()
    assert row is not None
    return int(row[0])


def test_repository_pin_is_fixed_to_visible_current_revision_and_same_space(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    conversation_id = _conversation(factory, ids)
    repository = ConversationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            created = repository.put_pin(
                connection,
                ConversationPinCreate(
                    conversation_id=conversation_id,
                    owner_user_id="member01",
                    item_id=str(ids["item_id"]),
                    revision_id=str(ids["revision_id"]),
                    created_at_ms=800,
                ),
            )
            assert created.inserted is True
            assert repository.put_pin(
                connection,
                ConversationPinCreate(
                    conversation_id=conversation_id,
                    owner_user_id="member01",
                    item_id=str(ids["item_id"]),
                    revision_id=str(ids["revision_id"]),
                    created_at_ms=801,
                ),
            ).inserted is False
            with pytest.raises(ConversationScopeError):
                repository.put_pin(
                    connection,
                    ConversationPinCreate(
                        conversation_id=conversation_id,
                        owner_user_id="member01",
                        item_id=str(ids["other_item_id"]),
                        revision_id=str(ids["other_revision_id"]),
                        created_at_ms=802,
                    ),
                )
            assert repository.list_pins(
                connection,
                conversation_id=conversation_id,
                owner_user_id="member01",
            ) == (created.pin,)
            assert repository.remove_pin(
                connection,
                conversation_id=conversation_id,
                owner_user_id="member01",
                item_id=str(ids["item_id"]),
            ).removed is True
            assert repository.remove_pin(
                connection,
                conversation_id=conversation_id,
                owner_user_id="member01",
                item_id=str(ids["item_id"]),
            ).removed is False
    finally:
        connection.close()


def test_service_pin_add_remove_replay_and_natural_no_change_are_durable(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    conversation_id = _service_conversation(factory, ids)
    service = ConversationService(factory)
    first = AddConversationPinCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=_uuid("pin-add-1"),
        request_id=_uuid("pin-add-request-1"),
        conversation_id=conversation_id,
        item_id=str(ids["item_id"]),
        revision_id=str(ids["revision_id"]),
    )
    added = service.add_pin(first)
    assert added.outcome == "added"
    assert service.add_pin(first) == added
    second = service.add_pin(
        AddConversationPinCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("pin-add-2"),
            request_id=_uuid("pin-add-request-2"),
            conversation_id=conversation_id,
            item_id=str(ids["item_id"]),
            revision_id=str(ids["revision_id"]),
        )
    )
    assert second.outcome == "no_change" and second.pin == added.pin
    assert service.list_pins(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation_id,
    ) == (added.pin,)
    remove = RemoveConversationPinCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=_uuid("pin-remove-1"),
        request_id=_uuid("pin-remove-request-1"),
        conversation_id=conversation_id,
        item_id=str(ids["item_id"]),
    )
    removed = service.remove_pin(remove)
    assert removed.outcome == "removed"
    assert service.remove_pin(remove) == removed
    absent = service.remove_pin(
        RemoveConversationPinCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("pin-remove-2"),
            request_id=_uuid("pin-remove-request-2"),
            conversation_id=conversation_id,
            item_id=str(ids["item_id"]),
        )
    )
    assert absent.outcome == "no_change" and absent.pin is None
    connection = factory.open()
    try:
        assert _count(connection, "conversation_pin") == 0
        assert _count(connection, "audit_event", "action LIKE 'conversation.pin.%'") == 4
        assert _count(connection, "outbox_event", "event_type LIKE 'conversation.pin.%'") == 2
    finally:
        connection.close()


def test_explicit_live_context_uses_outside_transaction_resolver_and_replays_without_it(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    conversation_id = _service_conversation(factory, ids)
    resolver = _Resolver(factory, space_id=str(ids["member_space"]))
    service = ConversationService(factory, context_resolver=resolver)
    assert service.list_contexts(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation_id,
    ) == ()
    command = AddConversationContextCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=_uuid("context-add-1"),
        request_id=_uuid("context-add-request-1"),
        conversation_id=conversation_id,
        binding_type="project_context",
        binding_mode="live",
        external_key="project:explicit-1",
    )
    added = service.add_context(command)
    assert added.outcome == "added"
    assert added.binding.status == "active"
    assert len(resolver.calls) == 1
    # A committed retry uses the server checkpoint and current DB scope. It does
    # not require the external source to remain reachable.
    assert ConversationService(factory).add_context(command) == added
    assert len(resolver.calls) == 1
    duplicate = service.add_context(
        AddConversationContextCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("context-add-2"),
            request_id=_uuid("context-add-request-2"),
            conversation_id=conversation_id,
            binding_type="project_context",
            binding_mode="live",
            external_key="project:explicit-1",
        )
    )
    assert duplicate.outcome == "no_change"
    assert duplicate.binding.id == added.binding.id
    assert service.list_contexts(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation_id,
    ) == (added.binding,)
    remove = RemoveConversationContextCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=_uuid("context-remove-1"),
        request_id=_uuid("context-remove-request-1"),
        conversation_id=conversation_id,
        binding_id=added.binding.id,
    )
    detached = service.remove_context(remove)
    assert detached.outcome == "detached"
    assert detached.binding.status == "detached"
    assert service.remove_context(remove) == detached
    redundant_remove = service.remove_context(
        RemoveConversationContextCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("context-remove-2"),
            request_id=_uuid("context-remove-request-2"),
            conversation_id=conversation_id,
            binding_id=added.binding.id,
        )
    )
    assert redundant_remove.outcome == "no_change"
    assert redundant_remove.binding == detached.binding
    assert service.list_contexts(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation_id,
    ) == ()
    assert service.list_contexts(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation_id,
        include_detached=True,
    ) == (detached.binding,)
    reattached = service.add_context(
        AddConversationContextCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("context-reattach"),
            request_id=_uuid("context-reattach-request"),
            conversation_id=conversation_id,
            binding_type="project_context",
            binding_mode="live",
            external_key="project:explicit-1",
        )
    )
    assert reattached.outcome == "added"
    assert reattached.binding.id != detached.binding.id


def test_snapshot_context_rechecks_scope_and_canonical_artifact_hash(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    conversation_id = _service_conversation(factory, ids)
    wrong_scope = ConversationService(
        factory,
        context_resolver=_Resolver(factory, space_id=str(ids["other_space"])),
    )
    command = AddConversationContextCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=_uuid("snapshot-wrong-scope"),
        request_id=_uuid("snapshot-wrong-scope-request"),
        conversation_id=conversation_id,
        binding_type="workspace_file",
        binding_mode="snapshot",
        external_key="workspace:file-1",
    )
    with pytest.raises(ConversationServiceScopeError):
        wrong_scope.add_context(command)
    wrong_hash = ConversationService(
        factory,
        context_resolver=_Resolver(
            factory,
            space_id=str(ids["member_space"]),
            source_artifact_id=str(ids["artifact_id"]),
            snapshot_sha256="f" * 64,
        ),
    )
    with pytest.raises(ConversationIdempotencyError):
        wrong_hash.add_context(
            AddConversationContextCommand(
                actor_user_id="member01",
                actor_scope_epoch=1,
                operation_id=_uuid("snapshot-wrong-hash"),
                request_id=_uuid("snapshot-wrong-hash-request"),
                conversation_id=conversation_id,
                binding_type="workspace_file",
                binding_mode="snapshot",
                external_key="workspace:file-1",
            )
        )
    valid = ConversationService(
        factory,
        context_resolver=_Resolver(
            factory,
            space_id=str(ids["member_space"]),
            source_artifact_id=str(ids["artifact_id"]),
            snapshot_sha256=str(ids["artifact_sha"]),
        ),
    ).add_context(
        AddConversationContextCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("snapshot-valid"),
            request_id=_uuid("snapshot-valid-request"),
            conversation_id=conversation_id,
            binding_type="workspace_file",
            binding_mode="snapshot",
            external_key="workspace:file-1",
        )
    )
    assert valid.binding.source_artifact_id == ids["artifact_id"]
    assert valid.binding.snapshot_sha256 == ids["artifact_sha"]
    connection = factory.open()
    try:
        assert _count(connection, "conversation_context_binding") == 1
        assert _count(connection, "operation_dedup", "operation_type='conversation.context.add'") == 1
    finally:
        connection.close()


def test_notion_and_source_artifact_context_are_explicit_server_resolutions(
    tmp_path: Path,
) -> None:
    assert {field.name for field in fields(AddConversationContextCommand)} == {
        "actor_user_id",
        "actor_scope_epoch",
        "operation_id",
        "request_id",
        "conversation_id",
        "binding_type",
        "binding_mode",
        "external_key",
        "request_meta",
    }
    factory = _factory(tmp_path)
    ids = _seed(factory)
    conversation_id = _service_conversation(factory, ids)
    notion = ConversationService(
        factory,
        context_resolver=_Resolver(factory, space_id=str(ids["member_space"])),
    ).add_context(
        AddConversationContextCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("notion-live"),
            request_id=_uuid("notion-live-request"),
            conversation_id=conversation_id,
            binding_type="notion_page",
            binding_mode="live",
            external_key="notion:page-1",
        )
    )
    source = ConversationService(
        factory,
        context_resolver=_Resolver(
            factory,
            space_id=str(ids["member_space"]),
            source_artifact_id=str(ids["artifact_id"]),
            snapshot_sha256=str(ids["artifact_sha"]),
        ),
    ).add_context(
        AddConversationContextCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("source-artifact-snapshot"),
            request_id=_uuid("source-artifact-snapshot-request"),
            conversation_id=conversation_id,
            binding_type="source_artifact",
            binding_mode="snapshot",
            external_key="artifact:source-1",
        )
    )
    assert (notion.binding.binding_type, notion.binding.binding_mode) == (
        "notion_page",
        "live",
    )
    assert (source.binding.binding_type, source.binding.binding_mode) == (
        "source_artifact",
        "snapshot",
    )


def test_attachment_audit_failure_rolls_back_domain_dedup_and_outbox(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    conversation_id = _service_conversation(factory, ids)
    with pytest.raises(RuntimeError, match="fixture audit failure"):
        ConversationService(factory, audit=_FailingAudit()).add_pin(
            AddConversationPinCommand(
                actor_user_id="member01",
                actor_scope_epoch=1,
                operation_id=_uuid("pin-failing-audit"),
                request_id=_uuid("pin-failing-audit-request"),
                conversation_id=conversation_id,
                item_id=str(ids["item_id"]),
                revision_id=str(ids["revision_id"]),
            )
        )
    connection = factory.open()
    try:
        assert _count(connection, "conversation_pin") == 0
        assert _count(connection, "operation_dedup", "operation_type='conversation.pin.add'") == 0
        assert _count(connection, "audit_event", "action='conversation.pin.add'") == 0
        assert _count(connection, "outbox_event", "event_type='conversation.pin.added'") == 0
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="fixture audit failure"):
        ConversationService(
            factory,
            audit=_FailingAudit(),
            context_resolver=_Resolver(
                factory, space_id=str(ids["member_space"])
            ),
        ).add_context(
            AddConversationContextCommand(
                actor_user_id="member01",
                actor_scope_epoch=1,
                operation_id=_uuid("context-failing-audit"),
                request_id=_uuid("context-failing-audit-request"),
                conversation_id=conversation_id,
                binding_type="project_context",
                binding_mode="live",
                external_key="project:failing-audit",
            )
        )
    connection = factory.open()
    try:
        assert _count(connection, "conversation_context_binding") == 0
        assert _count(
            connection,
            "operation_dedup",
            "operation_type='conversation.context.add'",
        ) == 0
        assert _count(
            connection, "audit_event", "action='conversation.context.add'"
        ) == 0
        assert _count(
            connection, "outbox_event", "event_type='conversation.context.added'"
        ) == 0
    finally:
        connection.close()


def test_foreign_actor_cannot_list_or_mutate_conversation_attachments(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    conversation_id = _service_conversation(factory, ids)
    service = ConversationService(factory)
    with pytest.raises(ConversationServiceScopeError):
        service.list_pins(
            actor_user_id="other01",
            actor_scope_epoch=1,
            conversation_id=conversation_id,
        )
    with pytest.raises(ConversationServiceScopeError):
        service.remove_pin(
            RemoveConversationPinCommand(
                actor_user_id="other01",
                actor_scope_epoch=1,
                operation_id=_uuid("foreign-pin-remove"),
                request_id=_uuid("foreign-pin-remove-request"),
                conversation_id=conversation_id,
                item_id=str(ids["item_id"]),
            )
        )


def test_part_conversation_has_no_private_default_and_accepts_only_explicit_part_scope(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed_part(factory)
    conversation_id = ConversationService(factory).create(
        CreateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("part-conversation-create"),
            request_id=_uuid("part-conversation-create-request"),
            space_id=ids["part_space"],
            title="Part conversation",
        )
    ).conversation.id
    service = ConversationService(factory)
    assert service.list_pins(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation_id,
    ) == ()
    assert service.list_contexts(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation_id,
    ) == ()
    pinned = service.add_pin(
        AddConversationPinCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("part-pin-add"),
            request_id=_uuid("part-pin-add-request"),
            conversation_id=conversation_id,
            item_id=ids["item_id"],
            revision_id=ids["revision_id"],
        )
    )
    assert pinned.space_id == ids["part_space"]
    with pytest.raises(ConversationServiceScopeError):
        ConversationService(
            factory,
            context_resolver=_Resolver(
                factory, space_id=ids["personal_space"]
            ),
        ).add_context(
            AddConversationContextCommand(
                actor_user_id="member01",
                actor_scope_epoch=1,
                operation_id=_uuid("part-private-context"),
                request_id=_uuid("part-private-context-request"),
                conversation_id=conversation_id,
                binding_type="project_context",
                binding_mode="live",
                external_key="project:private-default",
            )
        )
    explicit = ConversationService(
        factory,
        context_resolver=_Resolver(factory, space_id=ids["part_space"]),
    ).add_context(
        AddConversationContextCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("part-explicit-context"),
            request_id=_uuid("part-explicit-context-request"),
            conversation_id=conversation_id,
            binding_type="project_context",
            binding_mode="live",
            external_key="project:part-explicit",
        )
    )
    assert explicit.space_id == ids["part_space"]
    assert explicit.binding.external_key == "project:part-explicit"


@pytest.mark.scenario("CONV-013")
def test_conv_013_pin_and_explicit_context_cannot_move_across_spaces(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    conversation_id = _service_conversation(factory, ids)
    pin = ConversationService(factory).add_pin(
        AddConversationPinCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("conv-013-pin"),
            request_id=_uuid("conv-013-pin-request"),
            conversation_id=conversation_id,
            item_id=str(ids["item_id"]),
            revision_id=str(ids["revision_id"]),
        )
    )
    binding = ConversationService(
        factory,
        context_resolver=_Resolver(factory, space_id=str(ids["member_space"])),
    ).add_context(
        AddConversationContextCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("conv-013-context"),
            request_id=_uuid("conv-013-context-request"),
            conversation_id=conversation_id,
            binding_type="project_context",
            binding_mode="live",
            external_key="project:conv-013",
        )
    )
    other_conversation = _uuid("other-conversation")
    connection = factory.open()
    try:
        with transaction(connection):
            ConversationRepository().create(
                connection,
                ConversationCreate(
                    conversation_id=other_conversation,
                    owner_user_id="other01",
                    space_id=str(ids["other_space"]),
                    captured_scope_epoch=1,
                    title="Other",
                    metadata_json='{"source":"runtime"}',
                    created_at_ms=900,
                ),
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """UPDATE conversation_pin SET space_id=?
                       WHERE conversation_id=? AND item_id=?""",
                    (
                        ids["other_space"],
                        conversation_id,
                        pin.item_id,
                    ),
                )
            with pytest.raises(
                sqlite3.IntegrityError, match="CONVERSATION_CONTEXT_INVALID"
            ):
                connection.execute(
                    """UPDATE conversation_context_binding
                       SET conversation_id=?,row_version=row_version+1 WHERE id=?""",
                    (other_conversation, binding.binding.id),
                )
    finally:
        connection.close()
