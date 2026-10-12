from __future__ import annotations

from dataclasses import replace
import hashlib
import json
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
    LegacyConversationConflictError,
    LegacyConversationMessage,
    LegacyConversationSummary,
    LegacyNotionContextBinding,
    legacy_conversation_id,
    legacy_conversation_message_id,
    legacy_conversation_summary_id,
    legacy_notion_context_binding_id,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("da89f421-9f9e-41a2-840d-351694b3af7e")
_PLAN = "a" * 64
_OWNER = "owner"
_OTHER = "other"


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _record(number: int) -> str:
    return f"rec_{number:024x}"


def _relationship(number: int) -> str:
    return f"rel_{number:024x}"


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path,
        CANONICAL_MIGRATION_CATALOG,
    )
    SingleMigrator(
        factory,
        app_version="p2.5-conversation-repository-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _seed_identity(connection, user_id: str, space_id: str) -> None:
    UserRepository().import_legacy(
        connection,
        LegacyUserRecord(
            id=user_id,
            display_name=user_id.title(),
            password_hash=f"p2.5:{user_id}",
            system_role="user",
            account_status="active",
            created_at_ms=10,
            updated_at_ms=10,
        ),
    )
    SpaceRepository().ensure_personal(
        connection,
        space_id=space_id,
        user_id=user_id,
        created_at_ms=20,
    )


def _conversation(
    *,
    number: int = 1,
    owner: str = _OWNER,
    space_id: str | None = None,
) -> LegacyConversation:
    record = _record(number)
    return LegacyConversation(
        plan_sha256=_PLAN,
        inventory_record_id=record,
        conversation_id=legacy_conversation_id(_PLAN, record),
        owner_user_id=owner,
        personal_space_id=space_id or _uuid(f"space:{owner}"),
        captured_scope_epoch=1,
        title="Legacy session",
        metadata_json=(
            '{"legacy":{"bindings":1,"session_id_sha256":"'
            + "b" * 64
            + '","uploads":2},"schema_version":1}'
        ),
        created_at_ms=100,
    )


def _message(
    conversation: LegacyConversation,
    *,
    number: int,
    sequence_no: int,
    role: str = "user",
) -> LegacyConversationMessage:
    record = _record(number)
    content = json.dumps(
        {"content": f"message-{sequence_no}", "role": role},
        sort_keys=True,
        separators=(",", ":"),
    )
    return LegacyConversationMessage(
        plan_sha256=_PLAN,
        inventory_record_id=record,
        message_id=legacy_conversation_message_id(_PLAN, record),
        conversation_id=conversation.conversation_id,
        owner_user_id=conversation.owner_user_id,
        personal_space_id=conversation.personal_space_id,
        sequence_no=sequence_no,
        role=role,  # type: ignore[arg-type]
        content_json=content,
        created_at_ms=100 + sequence_no,
    )


def _summary(
    conversation: LegacyConversation,
    *,
    number: int = 20,
    covered: int = 1,
) -> LegacyConversationSummary:
    record = _record(number)
    body = "Legacy compact summary"
    return LegacyConversationSummary(
        plan_sha256=_PLAN,
        inventory_record_id=record,
        summary_id=legacy_conversation_summary_id(_PLAN, record),
        conversation_id=conversation.conversation_id,
        owner_user_id=conversation.owner_user_id,
        personal_space_id=conversation.personal_space_id,
        covered_through_sequence_no=covered,
        summary_md=body,
        summary_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        created_at_ms=120,
    )


def _binding(
    conversation: LegacyConversation,
    *,
    number: int = 1,
    external_key: str = "page-legacy-1",
) -> LegacyNotionContextBinding:
    relationship = _relationship(number)
    return LegacyNotionContextBinding(
        plan_sha256=_PLAN,
        relationship_id=relationship,
        context_binding_id=legacy_notion_context_binding_id(_PLAN, relationship),
        conversation_id=conversation.conversation_id,
        owner_user_id=conversation.owner_user_id,
        personal_space_id=conversation.personal_space_id,
        external_page_key=external_key,
        display_metadata_json=(
            '{"notion_row_sha256":"'
            + "c" * 64
            + '","page_identity_sha256":"'
            + "d" * 64
            + '","schema_version":1}'
        ),
        created_at_ms=130,
    )


def _seed_conversation(factory: ConnectionFactory) -> LegacyConversation:
    value = _conversation()
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_identity(connection, _OWNER, value.personal_space_id)
            ConversationRepository().put_legacy_conversation(connection, value)
    finally:
        connection.close()
    return value


def _count(connection, table: str) -> int:
    return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def test_p25_conversation_ids_are_deterministic_and_source_bound() -> None:
    assert legacy_conversation_id(_PLAN, _record(1)) == legacy_conversation_id(
        _PLAN, _record(1)
    )
    assert legacy_conversation_id(_PLAN, _record(1)) != legacy_conversation_id(
        _PLAN, _record(2)
    )
    assert legacy_conversation_message_id(
        _PLAN, _record(1)
    ) != legacy_conversation_summary_id(_PLAN, _record(1))
    assert legacy_notion_context_binding_id(
        _PLAN, _relationship(1)
    ) != legacy_notion_context_binding_id("b" * 64, _relationship(1))


@pytest.mark.parametrize("kind", ["conversation", "message", "summary", "binding"])
def test_p25_conversation_writes_require_caller_owned_transaction(
    isolated_knowledge_layout,
    kind: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    conversation = _seed_conversation(factory)
    repository = ConversationRepository()
    connection = factory.open()
    try:
        if kind == "conversation":
            value = replace(conversation, inventory_record_id=_record(2))
            call = lambda: repository.put_legacy_conversation(connection, value)
        elif kind == "message":
            call = lambda: repository.put_legacy_message(
                connection, _message(conversation, number=2, sequence_no=1)
            )
        elif kind == "summary":
            call = lambda: repository.put_legacy_summary(
                connection, _summary(conversation)
            )
        else:
            call = lambda: repository.put_legacy_notion_binding(
                connection, _binding(conversation)
            )
        with pytest.raises(KnowledgeCoreTransactionError):
            call()
    finally:
        connection.close()


def test_legacy_conversation_inserts_archived_and_exactly_replays(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    value = _conversation()
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_identity(connection, _OWNER, value.personal_space_id)
            first = ConversationRepository().put_legacy_conversation(connection, value)
            second = ConversationRepository().put_legacy_conversation(connection, value)
        assert first.inserted is True
        assert second.inserted is False
        row = connection.execute(
            """SELECT owner_user_id,space_id,captured_scope_epoch,title,status,
                      metadata_json,created_at,updated_at,row_version
               FROM conversation WHERE id=?""",
            (value.conversation_id,),
        ).fetchone()
        assert tuple(row) == (
            _OWNER,
            value.personal_space_id,
            1,
            "Legacy session",
            "archived",
            value.metadata_json,
            100,
            100,
            1,
        )
        assert _count(connection, "conversation_turn") == 0
        assert _count(connection, "conversation_pin") == 0
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("conversation_id", _uuid("forged")),
        ("metadata_json", '{"z":1,"a":2}'),
        ("captured_scope_epoch", 2),
    ],
)
def test_legacy_conversation_rejects_unbound_identity_or_scope_before_insert(
    isolated_knowledge_layout,
    field: str,
    replacement: object,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    base = _conversation()
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_identity(connection, _OWNER, base.personal_space_id)
            with pytest.raises((ValueError, LegacyConversationConflictError)):
                ConversationRepository().put_legacy_conversation(
                    connection, replace(base, **{field: replacement})
                )
        assert _count(connection, "conversation") == 0
    finally:
        connection.close()


def test_messages_append_in_exact_sequence_and_replay(isolated_knowledge_layout) -> None:
    factory = _factory(isolated_knowledge_layout)
    conversation = _seed_conversation(factory)
    first = _message(conversation, number=2, sequence_no=1, role="user")
    second = _message(conversation, number=3, sequence_no=2, role="assistant")
    connection = factory.open()
    try:
        with transaction(connection):
            repository = ConversationRepository()
            assert repository.put_legacy_message(connection, first).inserted is True
            assert repository.put_legacy_message(connection, second).inserted is True
            assert repository.put_legacy_message(connection, first).inserted is False
            assert repository.put_legacy_message(connection, second).inserted is False
        rows = connection.execute(
            """SELECT id,sequence_no,role,content_json FROM conversation_message
               ORDER BY sequence_no"""
        ).fetchall()
        assert [(row["id"], row["sequence_no"], row["role"]) for row in rows] == [
            (first.message_id, 1, "user"),
            (second.message_id, 2, "assistant"),
        ]
        assert json.loads(rows[1]["content_json"])["content"] == "message-2"
    finally:
        connection.close()


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: replace(value, sequence_no=2),
        lambda value: replace(value, role="invented"),
        lambda value: replace(value, content_json='{"content":"x","role":"tool"}'),
        lambda value: replace(value, message_id=_uuid("wrong-message")),
    ],
)
def test_invalid_or_out_of_order_message_has_no_effect(
    isolated_knowledge_layout,
    mutator,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    conversation = _seed_conversation(factory)
    value = mutator(_message(conversation, number=2, sequence_no=1))
    connection = factory.open()
    try:
        with transaction(connection):
            with pytest.raises((ValueError, LegacyConversationConflictError)):
                ConversationRepository().put_legacy_message(connection, value)
        assert _count(connection, "conversation_message") == 0
    finally:
        connection.close()


def test_message_sequence_collision_fails_closed(isolated_knowledge_layout) -> None:
    factory = _factory(isolated_knowledge_layout)
    conversation = _seed_conversation(factory)
    first = _message(conversation, number=2, sequence_no=1)
    collision = _message(conversation, number=3, sequence_no=1, role="assistant")
    connection = factory.open()
    try:
        with transaction(connection):
            repository = ConversationRepository()
            repository.put_legacy_message(connection, first)
            with pytest.raises(LegacyConversationConflictError):
                repository.put_legacy_message(connection, collision)
        assert _count(connection, "conversation_message") == 1
    finally:
        connection.close()


def test_legacy_summary_requires_covered_message_and_exact_utf8_hash(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    conversation = _seed_conversation(factory)
    message = _message(conversation, number=2, sequence_no=1)
    summary = _summary(conversation)
    connection = factory.open()
    try:
        with transaction(connection):
            repository = ConversationRepository()
            with pytest.raises(LegacyConversationConflictError):
                repository.put_legacy_summary(connection, summary)
            with pytest.raises(ValueError):
                repository.put_legacy_summary(
                    connection, replace(summary, summary_sha256="f" * 64)
                )
            repository.put_legacy_message(connection, message)
            assert repository.put_legacy_summary(connection, summary).inserted is True
            assert repository.put_legacy_summary(connection, summary).inserted is False
        row = connection.execute(
            """SELECT covered_through_sequence_no,model_id,prompt_version,
                      summarizer_version,status,created_by_user_id
               FROM conversation_summary"""
        ).fetchone()
        assert tuple(row) == (1, "legacy-import", "p2.5-v1", "p2.5-v1", "active", None)
    finally:
        connection.close()


def test_second_active_summary_is_rejected(isolated_knowledge_layout) -> None:
    factory = _factory(isolated_knowledge_layout)
    conversation = _seed_conversation(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            repository = ConversationRepository()
            repository.put_legacy_message(
                connection, _message(conversation, number=2, sequence_no=1)
            )
            repository.put_legacy_summary(connection, _summary(conversation, number=20))
            with pytest.raises(LegacyConversationConflictError):
                repository.put_legacy_summary(
                    connection, _summary(conversation, number=21)
                )
        assert _count(connection, "conversation_summary") == 1
    finally:
        connection.close()


def test_reviewed_notion_binding_inserts_live_and_exactly_replays(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    conversation = _seed_conversation(factory)
    binding = _binding(conversation)
    connection = factory.open()
    try:
        with transaction(connection):
            repository = ConversationRepository()
            assert repository.put_legacy_notion_binding(connection, binding).inserted is True
            assert repository.put_legacy_notion_binding(connection, binding).inserted is False
        row = connection.execute(
            """SELECT binding_type,binding_mode,external_key,source_artifact_id,
                      snapshot_sha256,status,status_reason,created_by
               FROM conversation_context_binding"""
        ).fetchone()
        assert tuple(row) == (
            "notion_page",
            "live",
            "page-legacy-1",
            None,
            None,
            "active",
            None,
            _OWNER,
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: replace(value, context_binding_id=_uuid("wrong-binding")),
        lambda value: replace(value, external_page_key=" page-legacy-1 "),
        lambda value: replace(value, display_metadata_json='{"z":1,"a":2}'),
    ],
)
def test_invalid_notion_binding_has_no_effect(
    isolated_knowledge_layout,
    mutator,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    conversation = _seed_conversation(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            with pytest.raises(ValueError):
                ConversationRepository().put_legacy_notion_binding(
                    connection, mutator(_binding(conversation))
                )
        assert _count(connection, "conversation_context_binding") == 0
    finally:
        connection.close()


def test_notion_page_identity_collision_is_rejected(isolated_knowledge_layout) -> None:
    factory = _factory(isolated_knowledge_layout)
    conversation = _seed_conversation(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            repository = ConversationRepository()
            repository.put_legacy_notion_binding(connection, _binding(conversation, number=1))
            with pytest.raises(LegacyConversationConflictError):
                repository.put_legacy_notion_binding(
                    connection, _binding(conversation, number=2)
                )
        assert _count(connection, "conversation_context_binding") == 1
    finally:
        connection.close()


def test_child_effect_cannot_cross_owner_or_personal_space(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    conversation = _seed_conversation(factory)
    other_space = _uuid("space:other")
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_identity(connection, _OTHER, other_space)
            repository = ConversationRepository()
            message = _message(conversation, number=2, sequence_no=1)
            with pytest.raises(LegacyConversationConflictError):
                repository.put_legacy_message(
                    connection,
                    replace(
                        message,
                        owner_user_id=_OTHER,
                        personal_space_id=other_space,
                    ),
                )
            with pytest.raises(LegacyConversationConflictError):
                repository.put_legacy_notion_binding(
                    connection,
                    replace(
                        _binding(conversation),
                        owner_user_id=_OTHER,
                        personal_space_id=other_space,
                    ),
                )
        assert _count(connection, "conversation_message") == 0
        assert _count(connection, "conversation_context_binding") == 0
    finally:
        connection.close()


def test_exact_replay_detects_mutable_conversation_drift(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    conversation = _seed_conversation(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """UPDATE conversation SET title='changed',updated_at=101,
                           row_version=row_version+1 WHERE id=?""",
                (conversation.conversation_id,),
            )
            with pytest.raises(LegacyConversationConflictError):
                ConversationRepository().put_legacy_conversation(
                    connection, conversation
                )
        assert connection.execute(
            "SELECT title FROM conversation WHERE id=?", (conversation.conversation_id,)
        ).fetchone()[0] == "changed"
    finally:
        connection.close()
