from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import sqlite3
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.errors import KnowledgeCoreTransactionError
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.conversations import (
    ConversationRepository,
    LegacyConversation,
    LegacyConversationMessage,
    LegacyConversationSummary,
    LegacyNotionContextBinding,
    legacy_conversation_id,
    legacy_conversation_message_id,
    legacy_conversation_summary_id,
    legacy_notion_context_binding_id,
)
from code_assist_v1.knowledge_core.repositories.artifacts import (
    ArtifactRepository,
    LegacyArtifactBundle,
    StagedVerifiedBlob,
    legacy_artifact_ids_for_record,
)
from code_assist_v1.knowledge_core.repositories.audit import (
    AuditEventDraft,
    AuditRepository,
)
from code_assist_v1.knowledge_core.repositories.migration import (
    migration_mapping_id_for_record,
    migration_source_locator,
)
from code_assist_v1.knowledge_core.repositories.migration_p25 import (
    LegacyP25JobDetails,
    LegacyP25JobTarget,
    LegacyP25MappingConflict,
    LegacyP25MappingIntegrityError,
    LegacyP25MappingRepository,
    LegacyP25NotionDetails,
    LegacyP25NotionTarget,
    LegacyP25RelationshipDetails,
    LegacyP25RelationshipRepository,
    LegacyP25RelationshipTarget,
    LegacyP25SessionDetails,
    LegacyP25SessionTarget,
    p25_child_bindings_sha256,
    p25_relationship_bindings_sha256,
    p25_relationship_mapping_id,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("c54c434c-71b9-47e0-beb6-f95c8c8b1fcb")
_PLAN = "a" * 64
_MANIFEST = "b" * 64
_ROOT_BINDING = "c" * 64
_OWNER = "owner"
_SPACE = str(uuid5(_NAMESPACE, "space"))
_SESSION_PARENT = "rec_" + "1" * 24
_MESSAGE_RECORD = "rec_" + "2" * 24
_METADATA_RECORD = "rec_" + "3" * 24
_SUMMARY_RECORD = "rec_" + "4" * 24
_NOTION_PARENT = "rec_" + "5" * 24
_NOTION_LINK = "rec_" + "6" * 24
_RELATIONSHIP = "rel_" + "7" * 24
_SESSION_PATH = "user_data/owner/sessions/alpha.json"
_NOTION_PATH = "user_data/owner/notion/notion.db"
_SESSION_ID_SHA = hashlib.sha256(b"alpha").hexdigest()
_PAGE_SHA = hashlib.sha256(b"page-1").hexdigest()
_NOTION_ROW_SHA = "e" * 64
_RELATIONSHIP_SHA = "f" * 64
_JOB_RECORD = "rec_" + "8" * 24
_JOB_PATH = "user_data/owner/jobs/job-1.json"


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path,
        CANONICAL_MIGRATION_CATALOG,
    )
    SingleMigrator(
        factory,
        app_version="p2.5-mapping-repository-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _evidence() -> tuple[list[dict[str, object]], dict[str, object]]:
    message = {"content": "hello", "role": "user"}
    metadata = {"metadata": {"source": "legacy"}, "skills_used": []}
    summary = "compact summary"
    children = [
        {
            "child_kind": "messages",
            "inventory_record_id": _MESSAGE_RECORD,
            "payload_sha256": _sha(_canonical(message)),
            "source_ordinal": 1,
        },
        {
            "child_kind": "metadata",
            "inventory_record_id": _METADATA_RECORD,
            "payload_sha256": _sha(_canonical(metadata)),
            "source_ordinal": 2,
        },
        {
            "child_kind": "summaries",
            "inventory_record_id": _SUMMARY_RECORD,
            "payload_sha256": _sha(_canonical(summary)),
            "source_ordinal": 3,
        },
    ]
    return children, {"message": message, "metadata": metadata, "summary": summary}


def _seed_targets(connection) -> dict[str, object]:
    UserRepository().import_legacy(
        connection,
        LegacyUserRecord(
            id=_OWNER,
            display_name="Owner",
            password_hash="p2.5-owner-hash",
            system_role="admin",
            account_status="active",
            created_at_ms=10,
            updated_at_ms=10,
        ),
    )
    SpaceRepository().ensure_personal(
        connection,
        space_id=_SPACE,
        user_id=_OWNER,
        created_at_ms=20,
    )
    child_evidence, payloads = _evidence()
    conversation_id = legacy_conversation_id(_PLAN, _SESSION_PARENT)
    conversation = LegacyConversation(
        plan_sha256=_PLAN,
        inventory_record_id=_SESSION_PARENT,
        conversation_id=conversation_id,
        owner_user_id=_OWNER,
        personal_space_id=_SPACE,
        captured_scope_epoch=1,
        title="Legacy alpha",
        metadata_json=_canonical(
            {
                "legacy_p2_5": {
                    "child_evidence": child_evidence,
                    "inventory_record_id": _SESSION_PARENT,
                    "plan_sha256": _PLAN,
                    "schema_version": 1,
                    "session_id_sha256": _SESSION_ID_SHA,
                },
                "schema_version": 1,
            }
        ),
        created_at_ms=100,
    )
    conversations = ConversationRepository()
    conversations.put_legacy_conversation(connection, conversation)
    message_id = legacy_conversation_message_id(_PLAN, _MESSAGE_RECORD)
    conversations.put_legacy_message(
        connection,
        LegacyConversationMessage(
            plan_sha256=_PLAN,
            inventory_record_id=_MESSAGE_RECORD,
            message_id=message_id,
            conversation_id=conversation_id,
            owner_user_id=_OWNER,
            personal_space_id=_SPACE,
            sequence_no=1,
            role="user",
            content_json=_canonical(payloads["message"]),
            created_at_ms=101,
        ),
    )
    summary_id = legacy_conversation_summary_id(_PLAN, _SUMMARY_RECORD)
    summary_body = str(payloads["summary"])
    conversations.put_legacy_summary(
        connection,
        LegacyConversationSummary(
            plan_sha256=_PLAN,
            inventory_record_id=_SUMMARY_RECORD,
            summary_id=summary_id,
            conversation_id=conversation_id,
            owner_user_id=_OWNER,
            personal_space_id=_SPACE,
            covered_through_sequence_no=1,
            summary_md=summary_body,
            summary_sha256=hashlib.sha256(summary_body.encode("utf-8")).hexdigest(),
            created_at_ms=102,
        ),
    )
    return {
        "conversation": conversation,
        "message_id": message_id,
        "summary_id": summary_id,
        "payloads": payloads,
    }


def _session_child(
    record_id: str,
    *,
    ordinal: int,
    kind: str,
    payload: object,
    conversation_id: str,
    message_id: str | None = None,
    summary_id: str | None = None,
) -> LegacyP25SessionTarget:
    result = (
        "message_created"
        if kind == "messages"
        else "summary_created"
        if kind == "summaries"
        else "preserved_in_metadata"
    )
    return LegacyP25SessionTarget(
        resolved_owner_user_id=_OWNER,
        conversation_id=conversation_id,
        message_id=message_id,
        summary_id=summary_id,
        source_path_key=_SESSION_PATH,
        details=LegacyP25SessionDetails(
            plan_sha256=_PLAN,
            inventory_manifest_sha256=_MANIFEST,
            root_binding_sha256=_ROOT_BINDING,
            inventory_record_id=record_id,
            inventory_record_sha256=_sha("record:" + record_id),
            parent_inventory_record_id=_SESSION_PARENT,
            root_id="user-data",
            path_key_sha256=_sha(_SESSION_PATH),
            category="conversation.user_sessions",
            record_kind="logical_record",
            source_ordinal=ordinal,
            import_mode="conversation_child",
            result_disposition=result,
            target_space_id=_SPACE,
            source_record_sha256=_sha("source:" + record_id),
            conversation_id=conversation_id,
            session_id_sha256=_SESSION_ID_SHA,
            child_kind=kind,  # type: ignore[arg-type]
            payload_sha256=_sha(_canonical(payload)),
            message_sequence_no=1 if kind == "messages" else None,
            planned_import_child_record_ids=(),
            imported_child_bindings_sha256=p25_child_bindings_sha256(()),
        ),
    )


def _session_targets(seed: dict[str, object]) -> tuple[LegacyP25SessionTarget, ...]:
    conversation = seed["conversation"]
    assert isinstance(conversation, LegacyConversation)
    payloads = seed["payloads"]
    assert isinstance(payloads, dict)
    return (
        _session_child(
            _MESSAGE_RECORD,
            ordinal=1,
            kind="messages",
            payload=payloads["message"],
            conversation_id=conversation.conversation_id,
            message_id=str(seed["message_id"]),
        ),
        _session_child(
            _METADATA_RECORD,
            ordinal=2,
            kind="metadata",
            payload=payloads["metadata"],
            conversation_id=conversation.conversation_id,
        ),
        _session_child(
            _SUMMARY_RECORD,
            ordinal=3,
            kind="summaries",
            payload=payloads["summary"],
            conversation_id=conversation.conversation_id,
            summary_id=str(seed["summary_id"]),
        ),
    )


def _session_parent(
    seed: dict[str, object],
    children: tuple[LegacyP25SessionTarget, ...],
) -> LegacyP25SessionTarget:
    conversation = seed["conversation"]
    assert isinstance(conversation, LegacyConversation)
    bindings = [
        (
            migration_mapping_id_for_record(_PLAN, child.details.inventory_record_id),
            child.details.inventory_record_id,
            child.details.source_record_sha256,
        )
        for child in children
    ]
    return LegacyP25SessionTarget(
        resolved_owner_user_id=_OWNER,
        conversation_id=conversation.conversation_id,
        message_id=None,
        summary_id=None,
        source_path_key=_SESSION_PATH,
        details=LegacyP25SessionDetails(
            plan_sha256=_PLAN,
            inventory_manifest_sha256=_MANIFEST,
            root_binding_sha256=_ROOT_BINDING,
            inventory_record_id=_SESSION_PARENT,
            inventory_record_sha256=_sha("record:" + _SESSION_PARENT),
            parent_inventory_record_id=None,
            root_id="user-data",
            path_key_sha256=_sha(_SESSION_PATH),
            category="conversation.user_sessions",
            record_kind="physical_file",
            source_ordinal=0,
            import_mode="source_container",
            result_disposition="dependency_closed",
            target_space_id=_SPACE,
            source_record_sha256=_sha("source:" + _SESSION_PARENT),
            conversation_id=conversation.conversation_id,
            session_id_sha256=_SESSION_ID_SHA,
            child_kind=None,
            payload_sha256=None,
            message_sequence_no=None,
            planned_import_child_record_ids=tuple(
                sorted(child.details.inventory_record_id for child in children)
            ),
            imported_child_bindings_sha256=p25_child_bindings_sha256(bindings),
        ),
    )


def _put_session_children(connection, seed: dict[str, object]):
    repository = LegacyP25MappingRepository()
    children = _session_targets(seed)
    records = [
        repository.put_session_imported(
            connection, target=child, now_ms=200 + index
        )[0]
        for index, child in enumerate(children)
    ]
    return children, records


def _notion_link(seed: dict[str, object]) -> tuple[LegacyP25NotionTarget, str]:
    conversation = seed["conversation"]
    assert isinstance(conversation, LegacyConversation)
    binding_id = legacy_notion_context_binding_id(_PLAN, _RELATIONSHIP)
    ConversationRepository().put_legacy_notion_binding(
        seed["connection"],  # type: ignore[arg-type]
        LegacyNotionContextBinding(
            plan_sha256=_PLAN,
            relationship_id=_RELATIONSHIP,
            context_binding_id=binding_id,
            conversation_id=conversation.conversation_id,
            owner_user_id=_OWNER,
            personal_space_id=_SPACE,
            external_page_key="page-1",
            display_metadata_json=_canonical(
                {
                    "legacy_p2_5": {
                        "notion_row_sha256": _NOTION_ROW_SHA,
                        "page_identity_sha256": _PAGE_SHA,
                        "relationship_id": _RELATIONSHIP,
                        "schema_version": 1,
                    }
                }
            ),
            created_at_ms=300,
        ),
    )
    target = LegacyP25NotionTarget(
        resolved_owner_user_id=_OWNER,
        conversation_id=conversation.conversation_id,
        context_binding_id=binding_id,
        source_path_key=_NOTION_PATH,
        details=LegacyP25NotionDetails(
            plan_sha256=_PLAN,
            inventory_manifest_sha256=_MANIFEST,
            root_binding_sha256=_ROOT_BINDING,
            inventory_record_id=_NOTION_LINK,
            inventory_record_sha256=_sha("record:" + _NOTION_LINK),
            parent_inventory_record_id=_NOTION_PARENT,
            root_id="user-data",
            path_key_sha256=_sha(_NOTION_PATH),
            record_kind="logical_record",
            source_ordinal=1,
            import_mode="notion_session_link",
            result_disposition="binding_created",
            target_space_id=_SPACE,
            source_record_sha256=_sha("source:" + _NOTION_LINK),
            conversation_id=conversation.conversation_id,
            relationship_id=_RELATIONSHIP,
            inventory_relationship_sha256=_RELATIONSHIP_SHA,
            page_identity_sha256=_PAGE_SHA,
            notion_row_sha256=_NOTION_ROW_SHA,
            planned_import_child_record_ids=(),
            planned_import_relationship_ids=(),
            imported_child_bindings_sha256=p25_child_bindings_sha256(()),
            imported_relationship_bindings_sha256=p25_relationship_bindings_sha256(()),
        ),
    )
    return target, binding_id


def _relationship_target(
    seed: dict[str, object],
    link_record_id: str,
    session_record_id: str,
    binding_id: str,
) -> LegacyP25RelationshipTarget:
    conversation = seed["conversation"]
    assert isinstance(conversation, LegacyConversation)
    return LegacyP25RelationshipTarget(
        source_mapping_id=migration_mapping_id_for_record(_PLAN, link_record_id),
        from_mapping_id=migration_mapping_id_for_record(_PLAN, session_record_id),
        conversation_id=conversation.conversation_id,
        context_binding_id=binding_id,
        resolved_owner_user_id=_OWNER,
        target_space_id=_SPACE,
        details=LegacyP25RelationshipDetails(
            plan_sha256=_PLAN,
            relationship_id=_RELATIONSHIP,
            inventory_manifest_sha256=_MANIFEST,
            root_binding_sha256=_ROOT_BINDING,
            inventory_relationship_sha256=_RELATIONSHIP_SHA,
            source_inventory_record_id=link_record_id,
            from_inventory_record_id=session_record_id,
            page_identity_sha256=_PAGE_SHA,
            notion_row_sha256=_NOTION_ROW_SHA,
        ),
    )


def _notion_parent(
    link: LegacyP25NotionTarget,
    relationship_mapping_id: str,
) -> LegacyP25NotionTarget:
    return LegacyP25NotionTarget(
        resolved_owner_user_id=_OWNER,
        conversation_id=None,
        context_binding_id=None,
        source_path_key=_NOTION_PATH,
        details=LegacyP25NotionDetails(
            plan_sha256=_PLAN,
            inventory_manifest_sha256=_MANIFEST,
            root_binding_sha256=_ROOT_BINDING,
            inventory_record_id=_NOTION_PARENT,
            inventory_record_sha256=_sha("record:" + _NOTION_PARENT),
            parent_inventory_record_id=None,
            root_id="user-data",
            path_key_sha256=_sha(_NOTION_PATH),
            record_kind="physical_file",
            source_ordinal=0,
            import_mode="source_container",
            result_disposition="dependency_closed",
            target_space_id=None,
            source_record_sha256=_sha("source:" + _NOTION_PARENT),
            conversation_id=None,
            relationship_id=None,
            inventory_relationship_sha256=None,
            page_identity_sha256=None,
            notion_row_sha256=None,
            planned_import_child_record_ids=(_NOTION_LINK,),
            planned_import_relationship_ids=(_RELATIONSHIP,),
            imported_child_bindings_sha256=p25_child_bindings_sha256(
                (
                    (
                        migration_mapping_id_for_record(_PLAN, _NOTION_LINK),
                        _NOTION_LINK,
                        link.details.source_record_sha256,
                    ),
                )
            ),
            imported_relationship_bindings_sha256=p25_relationship_bindings_sha256(
                ((relationship_mapping_id, _RELATIONSHIP, _RELATIONSHIP_SHA),)
            ),
        ),
    )


def _job_target(
    connection,
    *,
    source_status: str,
) -> LegacyP25JobTarget:
    raw = _canonical(
        {
            "job_id": "job-1",
            "kind": "legacy",
            "owner_user_id": _OWNER,
            "status": source_status,
        }
    ).encode("utf-8")
    raw_sha = hashlib.sha256(raw).hexdigest()
    mode = (
        "abandoned_legacy_job"
        if source_status in {"queued", "running", "unknown"}
        else "job_history"
    )
    disposition = (
        "abandoned_historical"
        if mode == "abandoned_legacy_job"
        else "terminal_history_recorded"
    )
    identity_sha = _sha("job-identity")
    evidence = {
        "authority": "historical_only",
        "category": "operations.jobs",
        "execution_policy": "never_replay",
        "inventory_record_id": _JOB_RECORD,
        "job_identity_sha256": identity_sha,
        "media_type": "application/json",
        "raw_sha256": raw_sha,
        "schema_version": 1,
        "size_bytes": len(raw),
        "source_status": source_status,
    }
    evidence_json = _canonical(evidence)
    artifact_ids = legacy_artifact_ids_for_record(_PLAN, _JOB_RECORD)
    locator = migration_source_locator(
        root_id="user-data",
        path_key=_JOB_PATH,
        record_kind="physical_file",
        source_ordinal=0,
    )
    ArtifactRepository().put_legacy_bundle(
        connection,
        LegacyArtifactBundle(
            plan_sha256=_PLAN,
            inventory_record_id=_JOB_RECORD,
            ids=artifact_ids,
            owner_user_id=_OWNER,
            origin_key=locator,
            raw_blob=StagedVerifiedBlob(
                sha256=raw_sha,
                size_bytes=len(raw),
                media_type="application/json",
                relative_path=f"{raw_sha[:2]}/{raw_sha[2:4]}/{raw_sha}",
                created_at_ms=400,
                verified_at_ms=401,
            ),
            logical_name="job-1.json",
            original_filename="job-1.json",
            source_owner_snapshot_json='{"schema_version":1}',
            metadata_json='{"authority":"historical_only","schema_version":1}',
            created_at_ms=400,
            extraction=None,
        ),
    )
    audit_uuid = str(uuid5(_NAMESPACE, f"job-audit:{source_status}"))
    event = AuditRepository().append(
        connection,
        AuditEventDraft(
            event_uuid=audit_uuid,
            occurred_at_ms=410,
            request_id=str(uuid5(_NAMESPACE, "job-request")),
            operation_id=str(uuid5(_NAMESPACE, "job-operation")),
            action=(
                "legacy_job.abandoned_history"
                if mode == "abandoned_legacy_job"
                else "legacy_job.terminal_history"
            ),
            outcome=disposition,
            actor_kind="system",
            actor_user_id=None,
            actor_display_name="P2.5 legacy migration",
            actor_role_snapshot="system",
            space_id=_SPACE,
            reason="reviewed P2.5 historical job; never replay",
            details=evidence,
            request_meta={"reviewer_id": "reviewer"},
        ),
    )
    details = LegacyP25JobDetails(
        plan_sha256=_PLAN,
        inventory_manifest_sha256=_MANIFEST,
        root_binding_sha256=_ROOT_BINDING,
        inventory_record_id=_JOB_RECORD,
        inventory_record_sha256=_sha("record:" + _JOB_RECORD),
        root_id="user-data",
        path_key_sha256=_sha(_JOB_PATH),
        source_record_sha256=raw_sha,
        target_space_id=_SPACE,
        import_mode=mode,  # type: ignore[arg-type]
        result_disposition=disposition,  # type: ignore[arg-type]
        source_status=source_status,  # type: ignore[arg-type]
        job_identity_sha256=identity_sha,
        job_evidence_sha256=hashlib.sha256(evidence_json.encode("utf-8")).hexdigest(),
    )
    return LegacyP25JobTarget(
        resolved_owner_user_id=_OWNER,
        artifact_id=artifact_ids.artifact_id,
        audit_event_id=event.id,
        audit_event_uuid=event.event_uuid,
        details=details,
        source_path_key=_JOB_PATH,
        job_evidence_json=evidence_json,
    )


def _count(connection, table: str) -> int:
    return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def test_p25_binding_digests_and_relationship_id_are_order_independent() -> None:
    first = (str(uuid5(_NAMESPACE, "m1")), _MESSAGE_RECORD, "1" * 64)
    second = (str(uuid5(_NAMESPACE, "m2")), _METADATA_RECORD, "2" * 64)
    assert p25_child_bindings_sha256((first, second)) == p25_child_bindings_sha256(
        (second, first)
    )
    assert p25_relationship_mapping_id(
        _PLAN, _RELATIONSHIP
    ) == p25_relationship_mapping_id(_PLAN, _RELATIONSHIP)
    assert p25_relationship_mapping_id(
        _PLAN, _RELATIONSHIP
    ) != p25_relationship_mapping_id("9" * 64, _RELATIONSHIP)


def test_session_children_and_container_insert_then_exactly_replay(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            seed = _seed_targets(connection)
            children, _ = _put_session_children(connection, seed)
            parent = _session_parent(seed, children)
            repository = LegacyP25MappingRepository()
            parent_record, inserted = repository.put_session_imported(
                connection, target=parent, now_ms=210
            )
            assert inserted is True
            for index, child in enumerate(children):
                assert repository.put_session_imported(
                    connection, target=child, now_ms=200 + index
                )[1] is False
            assert repository.put_session_imported(
                connection, target=parent, now_ms=210
            )[1] is False
        rows = connection.execute(
            """SELECT json_extract(details_json,'$.import_mode') AS mode,
                      conversation_id,message_id,summary_id
               FROM legacy_migration_map
               WHERE json_extract(details_json,'$.schema_version')=5
               ORDER BY mode,id"""
        ).fetchall()
        assert len(rows) == 4
        assert sum(row["message_id"] is not None for row in rows) == 1
        assert sum(row["summary_id"] is not None for row in rows) == 1
        assert all(row["conversation_id"] == parent.conversation_id for row in rows)
        assert parent_record.id == migration_mapping_id_for_record(
            _PLAN, _SESSION_PARENT
        )
    finally:
        connection.close()


def test_session_parent_refuses_incomplete_child_closure(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            seed = _seed_targets(connection)
            children = _session_targets(seed)
            LegacyP25MappingRepository().put_session_imported(
                connection, target=children[0], now_ms=200
            )
            with pytest.raises(LegacyP25MappingIntegrityError):
                LegacyP25MappingRepository().put_session_imported(
                    connection,
                    target=_session_parent(seed, children),
                    now_ms=210,
                )
        assert _count(connection, "legacy_migration_map") == 1
    finally:
        connection.close()


def test_closed_session_rejects_late_child_and_remains_valid(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            seed = _seed_targets(connection)
            children, _ = _put_session_children(connection, seed)
            parent = _session_parent(seed, children)
            repository = LegacyP25MappingRepository()
            repository.put_session_imported(connection, target=parent, now_ms=210)
            late = replace(
                children[1],
                details=replace(
                    children[1].details,
                    inventory_record_id="rec_" + "8" * 24,
                    inventory_record_sha256="8" * 64,
                    source_ordinal=4,
                    source_record_sha256="9" * 64,
                ),
            )
            with pytest.raises(LegacyP25MappingConflict):
                repository.put_session_imported(
                    connection, target=late, now_ms=211
                )
            assert repository.put_session_imported(
                connection, target=parent, now_ms=210
            )[1] is False
        assert _count(connection, "legacy_migration_map") == 4
    finally:
        connection.close()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda child: replace(child, message_id=str(uuid5(_NAMESPACE, "wrong"))),
        lambda child: replace(
            child,
            details=replace(child.details, payload_sha256="0" * 64),
        ),
        lambda child: replace(
            child,
            details=replace(child.details, message_sequence_no=2),
        ),
    ],
)
def test_session_message_checkpoint_requires_exact_canonical_effect(
    isolated_knowledge_layout,
    mutation,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            seed = _seed_targets(connection)
            child = mutation(_session_targets(seed)[0])
            with pytest.raises(LegacyP25MappingIntegrityError):
                LegacyP25MappingRepository().put_session_imported(
                    connection, target=child, now_ms=200
                )
        assert _count(connection, "legacy_migration_map") == 0
    finally:
        connection.close()


def test_notion_link_relationship_and_parent_close_exactly(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            seed = _seed_targets(connection)
            seed["connection"] = connection
            children, _ = _put_session_children(connection, seed)
            session_parent = _session_parent(seed, children)
            records = LegacyP25MappingRepository()
            records.put_session_imported(
                connection, target=session_parent, now_ms=210
            )
            link, binding_id = _notion_link(seed)
            assert records.put_notion_imported(
                connection, target=link, now_ms=310
            )[1] is True
            relation = _relationship_target(
                seed, _NOTION_LINK, _SESSION_PARENT, binding_id
            )
            relationship_record, inserted = LegacyP25RelationshipRepository().put_imported(
                connection, target=relation, now_ms=311
            )
            assert inserted is True
            parent = _notion_parent(link, relationship_record.id)
            assert records.put_notion_imported(
                connection, target=parent, now_ms=312
            )[1] is True
            assert records.put_notion_imported(
                connection, target=link, now_ms=310
            )[1] is False
            assert LegacyP25RelationshipRepository().put_imported(
                connection, target=relation, now_ms=311
            )[1] is False
            assert records.put_notion_imported(
                connection, target=parent, now_ms=312
            )[1] is False
        assert _count(connection, "legacy_migration_p25_relationship_map") == 1
        row = connection.execute(
            """SELECT relationship_type,conversation_id,context_binding_id,
                      page_identity_sha256,notion_row_sha256,status
               FROM legacy_migration_p25_relationship_map"""
        ).fetchone()
        assert tuple(row) == (
            "notion_session_page",
            session_parent.conversation_id,
            binding_id,
            _PAGE_SHA,
            _NOTION_ROW_SHA,
            "imported",
        )
    finally:
        connection.close()


def test_notion_parent_refuses_missing_relationship_checkpoint(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            seed = _seed_targets(connection)
            seed["connection"] = connection
            children, _ = _put_session_children(connection, seed)
            LegacyP25MappingRepository().put_session_imported(
                connection, target=_session_parent(seed, children), now_ms=210
            )
            link, _ = _notion_link(seed)
            LegacyP25MappingRepository().put_notion_imported(
                connection, target=link, now_ms=310
            )
            parent = _notion_parent(
                link, p25_relationship_mapping_id(_PLAN, _RELATIONSHIP)
            )
            with pytest.raises(LegacyP25MappingIntegrityError):
                LegacyP25MappingRepository().put_notion_imported(
                    connection, target=parent, now_ms=312
                )
        assert _count(connection, "legacy_migration_p25_relationship_map") == 0
    finally:
        connection.close()


@pytest.mark.parametrize(
    "field",
    ["inventory_manifest_sha256", "root_binding_sha256", "page_identity_sha256"],
)
def test_notion_relationship_rejects_endpoint_binding_drift(
    isolated_knowledge_layout,
    field: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            seed = _seed_targets(connection)
            seed["connection"] = connection
            children, _ = _put_session_children(connection, seed)
            LegacyP25MappingRepository().put_session_imported(
                connection, target=_session_parent(seed, children), now_ms=210
            )
            link, binding_id = _notion_link(seed)
            LegacyP25MappingRepository().put_notion_imported(
                connection, target=link, now_ms=310
            )
            relation = _relationship_target(
                seed, _NOTION_LINK, _SESSION_PARENT, binding_id
            )
            relation = replace(
                relation,
                details=replace(relation.details, **{field: "0" * 64}),
            )
            with pytest.raises(LegacyP25MappingIntegrityError):
                LegacyP25RelationshipRepository().put_imported(
                    connection, target=relation, now_ms=311
                )
        assert _count(connection, "legacy_migration_p25_relationship_map") == 0
    finally:
        connection.close()


def test_p25_mapping_requires_transaction_and_canonical_source_path(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            seed = _seed_targets(connection)
        child = _session_targets(seed)[0]
        with pytest.raises(KnowledgeCoreTransactionError):
            LegacyP25MappingRepository().put_session_imported(
                connection, target=child, now_ms=200
            )
        with transaction(connection):
            with pytest.raises(ValueError):
                LegacyP25MappingRepository().put_session_imported(
                    connection,
                    target=replace(child, source_path_key="../escape.json"),
                    now_ms=200,
                )
        assert _count(connection, "legacy_migration_map") == 0
    finally:
        connection.close()


def test_p25_relationship_checkpoint_is_database_immutable(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            seed = _seed_targets(connection)
            seed["connection"] = connection
            children, _ = _put_session_children(connection, seed)
            LegacyP25MappingRepository().put_session_imported(
                connection, target=_session_parent(seed, children), now_ms=210
            )
            link, binding_id = _notion_link(seed)
            LegacyP25MappingRepository().put_notion_imported(
                connection, target=link, now_ms=310
            )
            relation = _relationship_target(
                seed, _NOTION_LINK, _SESSION_PARENT, binding_id
            )
            LegacyP25RelationshipRepository().put_imported(
                connection, target=relation, now_ms=311
            )
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            with transaction(connection):
                connection.execute(
                    "UPDATE legacy_migration_p25_relationship_map SET updated_at=999"
                )
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            with transaction(connection):
                connection.execute("DELETE FROM legacy_migration_p25_relationship_map")
        assert _count(connection, "legacy_migration_p25_relationship_map") == 1
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("source_status", "mode", "outcome"),
    [
        ("succeeded", "job_history", "terminal_history_recorded"),
        ("failed", "job_history", "terminal_history_recorded"),
        ("queued", "abandoned_legacy_job", "abandoned_historical"),
        ("running", "abandoned_legacy_job", "abandoned_historical"),
        ("unknown", "abandoned_legacy_job", "abandoned_historical"),
    ],
)
def test_legacy_jobs_are_historical_artifacts_never_durable_jobs(
    isolated_knowledge_layout,
    source_status: str,
    mode: str,
    outcome: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_targets(connection)
            target = _job_target(connection, source_status=source_status)
            repository = LegacyP25MappingRepository()
            record, inserted = repository.put_job_imported(
                connection, target=target, now_ms=420
            )
            assert inserted is True
            assert repository.put_job_imported(
                connection, target=target, now_ms=420
            )[1] is False
        row = connection.execute(
            """SELECT artifact_id,audit_event_id,conversation_id,message_id,
                      json_extract(details_json,'$.import_mode') AS mode,
                      json_extract(details_json,'$.result_disposition') AS disposition
               FROM legacy_migration_map WHERE id=?""",
            (record.id,),
        ).fetchone()
        assert row["artifact_id"] == target.artifact_id
        assert row["audit_event_id"] == target.audit_event_id
        assert row["conversation_id"] is None
        assert row["message_id"] is None
        assert row["mode"] == mode
        assert row["disposition"] == outcome
        assert _count(connection, "durable_job") == 0
    finally:
        connection.close()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda target: replace(
            target,
            details=replace(target.details, source_status="running"),
        ),
        lambda target: replace(target, artifact_id=str(uuid5(_NAMESPACE, "wrong-artifact"))),
        lambda target: replace(target, audit_event_id=999),
        lambda target: replace(
            target,
            job_evidence_json=target.job_evidence_json.replace(
                '"never_replay"', '"execute"'
            ),
        ),
    ],
)
def test_legacy_job_checkpoint_rejects_policy_or_effect_drift(
    isolated_knowledge_layout,
    mutation,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_targets(connection)
            target = mutation(_job_target(connection, source_status="succeeded"))
            with pytest.raises((ValueError, LegacyP25MappingIntegrityError)):
                LegacyP25MappingRepository().put_job_imported(
                    connection, target=target, now_ms=420
                )
        assert _count(connection, "legacy_migration_map") == 0
        assert _count(connection, "durable_job") == 0
    finally:
        connection.close()
