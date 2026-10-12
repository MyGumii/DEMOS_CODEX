from __future__ import annotations

from dataclasses import replace
import hashlib
import inspect
import json
import sqlite3
from pathlib import Path
from uuid import UUID, uuid5

import pytest
from flask import Blueprint, Flask

from code_assist_v1.knowledge_core import (
    CanonicalRouteBindings,
    HostKind,
    ServiceRegistry,
    register_knowledge_core,
)
from code_assist_v1.knowledge_core.canonical_routes import (
    AgentTurnCompletion,
    CompactSummaryCompletion,
    register_canonical_routes,
)
from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.conversations import (
    ConversationCreate,
    ConversationRepository,
    ConversationScopeError,
    ConversationVersionConflictError,
    LegacyConversation,
    LegacyConversationMessage,
    LegacyConversationSummary,
    legacy_conversation_id,
    legacy_conversation_message_id,
    legacy_conversation_summary_id,
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
from code_assist_v1.knowledge_core.repositories.audit import AuditRepository
from code_assist_v1.knowledge_core.legacy.session_compatibility import (
    LegacyJsonSessionAdapter,
    LegacySessionNotFoundError,
    LegacySessionScopeError,
)
from code_assist_v1.knowledge_core.services.conversations import (
    AcceptConversationTurnCommand,
    CompactConversationCommand,
    CompleteConversationTurnCommand,
    ConversationIdempotencyError,
    ConversationService,
    ConversationServiceScopeError,
    ConversationSessionScopeStaleError,
    CreateConversationCommand,
    TerminateConversationTurnCommand,
    UpdateConversationCommand,
    conversation_id_for_create,
    conversation_summary_id,
    conversation_turn_assistant_message_id,
)
from code_assist_v1.knowledge_core.services import scope_selection as scope_selection_module
from code_assist_v1.knowledge_core.services.scope_selection import (
    ScopeSelectionNotFoundError,
    ScopeSelectionRequest,
    ScopeSelectionScopeError,
    ScopeSelectionService,
)
from code_assist_v1.knowledge_core.services.retrieval import CanonicalRetrievalService
from code_assist_v1.knowledge_core.services.management_reads import (
    CanonicalManagementReadService,
)
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.test_safety import create_isolated_test_layout


pytestmark = pytest.mark.unit
_NAMESPACE = UUID("c8361355-fac4-4db5-b73c-66f360499d7a")


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _factory(tmp_path: Path) -> ConnectionFactory:
    layout = create_isolated_test_layout(tmp_path, user_id="fixture-user")
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path,
        CANONICAL_MIGRATION_CATALOG,
    )
    SingleMigrator(
        factory,
        app_version="p3.1-conversation-repository-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _user(user_id: str, *, current_part_id: str | None = None) -> LegacyUserRecord:
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.title(),
        password_hash=f"scrypt:p3.1:{user_id}",
        system_role="admin" if user_id == "admin" else "user",
        account_status="active",
        current_part_id=current_part_id,
        scope_epoch=1,
        created_at_ms=100,
        updated_at_ms=100,
    )


def _seed(factory: ConnectionFactory) -> dict[str, str]:
    values = {
        "team": _uuid("team"),
        "part_a": _uuid("part-a"),
        "part_b": _uuid("part-b"),
        "personal_space": _uuid("personal-space"),
        "part_space": _uuid("part-space"),
        "membership_a": _uuid("membership-a"),
        "membership_b": _uuid("membership-b"),
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
                name="Runtime Team",
                actor_user_id="admin",
                now_ms=110,
            )
            for index, key in enumerate(("part_a", "part_b")):
                organization.create_unit(
                    connection,
                    unit_id=values[key],
                    kind="part",
                    parent_id=values["team"],
                    name=f"Runtime Part {index}",
                    actor_user_id="admin",
                    now_ms=120 + index,
                )
            users.import_legacy(
                connection,
                _user("member01", current_part_id=values["part_a"]),
            )
            organization.open_membership(
                connection,
                membership_id=values["membership_a"],
                user_id="member01",
                part_id=values["part_a"],
                assigned_by="admin",
                reason="P3 fixture",
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
                part_id=values["part_a"],
                created_at_ms=141,
            )
    finally:
        connection.close()
    return values


def _create(
    repository: ConversationRepository,
    connection,
    *,
    label: str,
    space_id: str,
    now_ms: int,
):
    return repository.create(
        connection,
        ConversationCreate(
            conversation_id=_uuid(label),
            owner_user_id="member01",
            space_id=space_id,
            captured_scope_epoch=1,
            title=label,
            metadata_json=json.dumps(
                {"source": "runtime"},
                sort_keys=True,
                separators=(",", ":"),
            ),
            created_at_ms=now_ms,
        ),
    )


def test_conv_011_create_list_rename_archive_and_scope_are_exact(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    repository = ConversationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            personal = _create(
                repository,
                connection,
                label="personal-conversation",
                space_id=ids["personal_space"],
                now_ms=200,
            )
            part = _create(
                repository,
                connection,
                label="part-conversation",
                space_id=ids["part_space"],
                now_ms=201,
            )
            assert personal.inserted is True
            assert part.inserted is True
            assert _create(
                repository,
                connection,
                label="personal-conversation",
                space_id=ids["personal_space"],
                now_ms=200,
            ).inserted is False

            renamed = repository.update_lifecycle(
                connection,
                conversation_id=personal.conversation.id,
                owner_user_id="member01",
                expected_row_version=1,
                updated_at_ms=210,
                title="Renamed personal",
            )
            assert renamed.changed is True
            assert renamed.conversation.row_version == 2
            archived = repository.update_lifecycle(
                connection,
                conversation_id=renamed.conversation.id,
                owner_user_id="member01",
                expected_row_version=2,
                updated_at_ms=220,
                status="archived",
            )
            assert archived.conversation.status == "archived"
            assert archived.conversation.space_id == ids["personal_space"]
            assert archived.conversation.captured_scope_epoch == 1

            entries = repository.list_for_owner(
                connection,
                owner_user_id="member01",
                limit=2,
            )
            assert [row.conversation.id for row in entries] == [
                archived.conversation.id,
                part.conversation.id,
            ]
            assert all(row.accessible for row in entries)
            assert repository.list_for_owner(
                connection,
                owner_user_id="member01",
                before_updated_at_ms=part.conversation.updated_at_ms,
                before_conversation_id=part.conversation.id,
            ) == ()

            with pytest.raises(ConversationVersionConflictError):
                repository.update_lifecycle(
                    connection,
                    conversation_id=archived.conversation.id,
                    owner_user_id="member01",
                    expected_row_version=2,
                    updated_at_ms=230,
                    title="Stale write",
                )

            with pytest.raises(sqlite3.IntegrityError, match="CONVERSATION_SCOPE_IMMUTABLE"):
                connection.execute(
                    """UPDATE conversation SET space_id=?,row_version=row_version+1
                       WHERE id=?""",
                    (ids["part_space"], archived.conversation.id),
                )
    finally:
        connection.close()


def test_conv_011_part_scope_becomes_inaccessible_without_moving_conversation(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    repository = ConversationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            personal = _create(
                repository,
                connection,
                label="personal-scope",
                space_id=ids["personal_space"],
                now_ms=200,
            ).conversation
            part = _create(
                repository,
                connection,
                label="part-scope",
                space_id=ids["part_space"],
                now_ms=201,
            ).conversation

        with transaction(connection):
            organization = OrganizationRepository()
            current = organization.close_current_membership(
                connection,
                user_id="member01",
                expected_membership_id=ids["membership_a"],
                closed_at_ms=300,
            )
            assert current.part_id == ids["part_a"]
            organization.open_membership(
                connection,
                membership_id=ids["membership_b"],
                user_id="member01",
                part_id=ids["part_b"],
                assigned_by="admin",
                reason="P3 scope move",
                valid_from_ms=301,
            )
            state = organization.compare_and_swap_user_part(
                connection,
                user_id="member01",
                expected_current_part_id=ids["part_a"],
                target_part_id=ids["part_b"],
                expected_scope_epoch=1,
                expected_row_version=1,
                updated_at_ms=302,
            )
            assert state.scope_epoch == 2

        entries = repository.list_for_owner(
            connection,
            owner_user_id="member01",
        )
        by_id = {entry.conversation.id: entry for entry in entries}
        assert by_id[personal.id].access_status == "accessible"
        assert by_id[part.id].access_status == "stale_part_scope"
        assert by_id[part.id].conversation.space_id == ids["part_space"]
        assert by_id[part.id].conversation.captured_scope_epoch == 1
        with pytest.raises(ConversationScopeError):
            repository.list_messages(
                connection,
                conversation_id=part.id,
                owner_user_id="member01",
            )
        assert repository.list_messages(
            connection,
            conversation_id=personal.id,
            owner_user_id="member01",
        ) == ()
    finally:
        connection.close()


def _table_count(factory: ConnectionFactory, table: str) -> int:
    connection = factory.open()
    try:
        row = connection.execute(f'SELECT count(*) AS n FROM "{table}"').fetchone()
        assert row is not None
        return int(row["n"])
    finally:
        connection.close()


def test_conv_011_service_create_update_and_exact_replay_are_atomic(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    service = ConversationService(factory)
    create_operation = _uuid("service-create-operation")
    first_request = _uuid("service-create-request")
    command = CreateConversationCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=create_operation,
        request_id=first_request,
        space_id=ids["personal_space"],
        title="Service conversation",
        metadata={"client": "test"},
        request_meta={"correlation_id": "conv-011"},
    )

    first = service.create(command)
    replay = service.create(
        replace(command, request_id=_uuid("service-create-retry-request"))
    )
    assert first == replay
    assert first.request_id == first_request
    assert first.outcome == "created"
    assert first.conversation.id == conversation_id_for_create(
        "member01", create_operation
    )
    assert service.get(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=first.conversation.id,
    ) == first.conversation
    assert _table_count(factory, "conversation") == 1
    assert _table_count(factory, "operation_dedup") == 1
    assert _table_count(factory, "audit_event") == 1
    assert _table_count(factory, "outbox_event") == 1

    with pytest.raises(ConversationIdempotencyError):
        service.create(
            replace(command, title="Changed request")
        )
    assert _table_count(factory, "conversation") == 1

    update = service.update(
        UpdateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("service-update-operation"),
            request_id=_uuid("service-update-request"),
            conversation_id=first.conversation.id,
            expected_row_version=1,
            title="Updated title",
            status="archived",
        )
    )
    update_replay = service.update(
        UpdateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("service-update-operation"),
            request_id=_uuid("service-update-retry-request"),
            conversation_id=first.conversation.id,
            expected_row_version=1,
            title="Updated title",
            status="archived",
        )
    )
    assert update == update_replay
    assert update.outcome == "updated"
    assert update.conversation.row_version == 2
    assert update.conversation.status == "archived"
    assert _table_count(factory, "operation_dedup") == 2
    assert _table_count(factory, "audit_event") == 2
    assert _table_count(factory, "outbox_event") == 2


@pytest.mark.scenario("CONV-001")
def test_first_turn_sets_empty_conversation_title_without_overwriting_explicit_title(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    service = ConversationService(factory)

    automatic = service.create(
        CreateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("automatic-title:create"),
            request_id=_uuid("automatic-title:create:request"),
            space_id=ids["personal_space"],
            title="",
        )
    ).conversation
    turn_command = AcceptConversationTurnCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=_uuid("automatic-title:turn"),
        request_id=_uuid("automatic-title:turn:request"),
        conversation_id=automatic.id,
        user_content={"text": "  첫 질문을   세션 이름으로\n표시해 주세요.  "},
    )
    service.accept_turn(turn_command)
    titled = service.get(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=automatic.id,
    )
    assert titled.title == "첫 질문을 세션 이름으로 표시해 주세요."
    assert titled.row_version == automatic.row_version + 1
    service.accept_turn(
        replace(turn_command, request_id=_uuid("automatic-title:turn:replay-request"))
    )
    assert service.get(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=automatic.id,
    ) == titled

    explicit = service.create(
        CreateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("explicit-title:create"),
            request_id=_uuid("explicit-title:create:request"),
            space_id=ids["personal_space"],
            title="사용자가 정한 제목",
        )
    ).conversation
    service.accept_turn(
        AcceptConversationTurnCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("explicit-title:turn"),
            request_id=_uuid("explicit-title:turn:request"),
            conversation_id=explicit.id,
            user_content={"text": "이 질문으로 덮어쓰면 안 됩니다."},
        )
    )
    preserved = service.get(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=explicit.id,
    )
    assert preserved.title == "사용자가 정한 제목"
    assert preserved.row_version == explicit.row_version


class _FailingAudit(AuditRepository):
    def append(self, connection, draft):
        raise RuntimeError("injected audit failure")


def test_conv_011_service_owns_rollback_and_revalidates_session_scope(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    failing = ConversationService(factory, audit=_FailingAudit())
    command = CreateConversationCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=_uuid("rollback-operation"),
        request_id=_uuid("rollback-request"),
        space_id=ids["personal_space"],
    )
    with pytest.raises(RuntimeError, match="injected audit failure"):
        failing.create(command)
    assert _table_count(factory, "conversation") == 0
    assert _table_count(factory, "operation_dedup") == 0
    assert _table_count(factory, "audit_event") == 0
    assert _table_count(factory, "outbox_event") == 0

    service = ConversationService(factory)
    part = service.create(
        CreateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("part-service-operation"),
            request_id=_uuid("part-service-request"),
            space_id=ids["part_space"],
        )
    ).conversation
    connection = factory.open()
    try:
        with transaction(connection):
            organization = OrganizationRepository()
            organization.close_current_membership(
                connection,
                user_id="member01",
                expected_membership_id=ids["membership_a"],
                closed_at_ms=300,
            )
            organization.open_membership(
                connection,
                membership_id=ids["membership_b"],
                user_id="member01",
                part_id=ids["part_b"],
                assigned_by="admin",
                reason="P3 service scope move",
                valid_from_ms=301,
            )
            organization.compare_and_swap_user_part(
                connection,
                user_id="member01",
                expected_current_part_id=ids["part_a"],
                target_part_id=ids["part_b"],
                expected_scope_epoch=1,
                expected_row_version=1,
                updated_at_ms=302,
            )
    finally:
        connection.close()

    with pytest.raises(ConversationSessionScopeStaleError):
        service.list_for_owner(actor_user_id="member01", actor_scope_epoch=1)
    entries = service.list_for_owner(actor_user_id="member01", actor_scope_epoch=2)
    assert len(entries) == 1
    assert entries[0].conversation.id == part.id
    assert entries[0].access_status == "stale_part_scope"
    with pytest.raises(ConversationServiceScopeError):
        service.list_messages(
            actor_user_id="member01",
            actor_scope_epoch=2,
            conversation_id=part.id,
        )


def test_conv_012_turn_accept_reconnect_and_completion_are_exact_once(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    service = ConversationService(factory)
    conversation = service.create(
        CreateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("turn-parent-operation"),
            request_id=_uuid("turn-parent-request"),
            space_id=ids["personal_space"],
        )
    ).conversation
    operation_id = _uuid("turn-operation")
    request_id = _uuid("turn-request")
    accepted_command = AcceptConversationTurnCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=operation_id,
        request_id=request_id,
        conversation_id=conversation.id,
        user_content={"text": "hello"},
        request_meta={"correlation_id": "conv-012"},
    )

    accepted = service.accept_turn(accepted_command)
    accepted_retry = service.accept_turn(
        replace(accepted_command, request_id=_uuid("turn-retry-request"))
    )
    assert accepted == accepted_retry
    assert accepted.state == "accepted"
    assert accepted.assistant_message_id is None
    assert accepted.event_cursor == 1
    assert _table_count(factory, "conversation_message") == 1
    assert _table_count(factory, "conversation_turn") == 1
    assert _table_count(factory, "conversation_turn_event") == 1

    reconnected, events = service.reconnect_turn(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation.id,
        operation_id=operation_id,
    )
    assert reconnected == accepted
    assert [event.event_type for event in events] == ["accepted"]
    assert service.reconnect_turn(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation.id,
        operation_id=operation_id,
        after_event_sequence_no=1,
    )[1] == ()

    with pytest.raises(ConversationIdempotencyError):
        service.accept_turn(
            replace(accepted_command, user_content={"text": "different"})
        )
    assert _table_count(factory, "conversation_message") == 1

    completed_command = CompleteConversationTurnCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=operation_id,
        request_id=request_id,
        conversation_id=conversation.id,
        assistant_content={"text": "hello back"},
        final_response={"finish_reason": "stop"},
        request_meta={"correlation_id": "conv-012"},
    )
    completed = service.complete_turn(completed_command)
    completed_retry = service.complete_turn(completed_command)
    assert completed == completed_retry
    assert completed.state == "completed"
    assert completed.assistant_message_id == conversation_turn_assistant_message_id(
        conversation.id, operation_id
    )
    assert completed.final_response == {"finish_reason": "stop"}
    assert completed.event_cursor == 2
    assert _table_count(factory, "conversation_message") == 2
    assert _table_count(factory, "conversation_turn") == 1
    assert _table_count(factory, "conversation_turn_event") == 2
    assert _table_count(factory, "operation_dedup") == 2
    assert _table_count(factory, "audit_event") == 3
    assert _table_count(factory, "outbox_event") == 3

    after_completion = service.accept_turn(
        replace(accepted_command, request_id=_uuid("turn-lost-response-retry"))
    )
    assert after_completion == completed
    messages = service.list_messages(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation.id,
    )
    assert [(row.sequence_no, row.role) for row in messages] == [
        (1, "user"),
        (2, "assistant"),
    ]


class _FailOnCompletionAudit(AuditRepository):
    def append(self, connection, draft):
        if draft.action == "conversation.turn.complete":
            raise RuntimeError("injected completion audit failure")
        return super().append(connection, draft)


def test_conv_012_completion_failure_rolls_back_to_reconnectable_accept(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    normal = ConversationService(factory)
    conversation = normal.create(
        CreateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("rollback-turn-parent"),
            request_id=_uuid("rollback-turn-parent-request"),
            space_id=ids["personal_space"],
        )
    ).conversation
    accepted = AcceptConversationTurnCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=_uuid("rollback-turn-operation"),
        request_id=_uuid("rollback-turn-request"),
        conversation_id=conversation.id,
        user_content={"text": "persist me once"},
    )
    normal.accept_turn(accepted)
    completion = CompleteConversationTurnCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=accepted.operation_id,
        request_id=accepted.request_id,
        conversation_id=conversation.id,
        assistant_content={"text": "one final answer"},
        final_response={"finish_reason": "stop"},
    )
    with pytest.raises(RuntimeError, match="injected completion audit failure"):
        ConversationService(factory, audit=_FailOnCompletionAudit()).complete_turn(
            completion
        )
    state, events = normal.reconnect_turn(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation.id,
        operation_id=accepted.operation_id,
    )
    assert state.state == "accepted"
    assert [event.event_type for event in events] == ["accepted"]
    assert _table_count(factory, "conversation_message") == 1

    completed = normal.complete_turn(completion)
    assert completed.state == "completed"
    assert _table_count(factory, "conversation_message") == 2
    assert _table_count(factory, "conversation_turn_event") == 2


def test_conv_012_failed_and_cancelled_turns_are_terminal_exact_once(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    service = ConversationService(factory)
    conversation = service.create(
        CreateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("terminal-turn-parent"),
            request_id=_uuid("terminal-turn-parent-request"),
            space_id=ids["personal_space"],
        )
    ).conversation

    failed_accept = AcceptConversationTurnCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=_uuid("failed-turn-operation"),
        request_id=_uuid("failed-turn-request"),
        conversation_id=conversation.id,
        user_content={"text": "fail safely"},
    )
    service.accept_turn(failed_accept)
    failed_command = TerminateConversationTurnCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=failed_accept.operation_id,
        request_id=failed_accept.request_id,
        conversation_id=conversation.id,
        terminal_state="failed",
        error_code="MODEL_RESPONSE_UNAVAILABLE",
        retryable=True,
        request_meta={"correlation_id": "conv-012-failed"},
    )
    failed = service.terminate_turn(failed_command)
    assert service.terminate_turn(failed_command) == failed
    assert service.accept_turn(failed_accept) == failed
    assert service.complete_turn(
        CompleteConversationTurnCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=failed_accept.operation_id,
            request_id=failed_accept.request_id,
            conversation_id=conversation.id,
            assistant_content={"text": "must not be stored"},
            final_response={"finish_reason": "stop"},
        )
    ) == failed
    assert failed.state == "failed"
    assert failed.assistant_message_id is None
    assert failed.final_response is None
    assert failed.error == {
        "code": "MODEL_RESPONSE_UNAVAILABLE",
        "retryable": True,
        "schema_version": 1,
    }
    assert failed.event_cursor == 2
    reconnected, failed_events = service.reconnect_turn(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation.id,
        operation_id=failed_accept.operation_id,
        after_event_sequence_no=1,
    )
    assert reconnected == failed
    assert [event.event_type for event in failed_events] == ["failed"]
    assert failed_events[0].payload == {
        "error": failed.error,
        "schema_version": 1,
        "state": "failed",
    }

    with pytest.raises(ValueError, match="stable sanitized turn error"):
        service.terminate_turn(
            replace(failed_command, error_code="raw model exception: secret")
        )

    cancelled_accept = AcceptConversationTurnCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=_uuid("cancelled-turn-operation"),
        request_id=_uuid("cancelled-turn-request"),
        conversation_id=conversation.id,
        user_content={"text": "cancel safely"},
    )
    service.accept_turn(cancelled_accept)
    cancelled_command = TerminateConversationTurnCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=cancelled_accept.operation_id,
        request_id=cancelled_accept.request_id,
        conversation_id=conversation.id,
        terminal_state="cancelled",
    )
    cancelled = service.terminate_turn(cancelled_command)
    assert service.terminate_turn(cancelled_command) == cancelled
    assert cancelled.state == "cancelled"
    assert cancelled.assistant_message_id is None
    assert cancelled.final_response is None
    assert cancelled.error is None
    assert cancelled.event_cursor == 2
    _, cancelled_events = service.reconnect_turn(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation.id,
        operation_id=cancelled_accept.operation_id,
        after_event_sequence_no=1,
    )
    assert [event.event_type for event in cancelled_events] == ["cancelled"]
    assert cancelled_events[0].payload == {
        "schema_version": 1,
        "state": "cancelled",
    }
    with pytest.raises(ValueError, match="cannot carry an error"):
        service.terminate_turn(
            replace(cancelled_command, error_code="CANCEL_ERROR")
        )

    assert _table_count(factory, "conversation_message") == 2
    assert _table_count(factory, "conversation_turn") == 2
    assert _table_count(factory, "conversation_turn_event") == 4
    assert _table_count(factory, "operation_dedup") == 3
    assert _table_count(factory, "audit_event") == 5
    assert _table_count(factory, "outbox_event") == 5


class _FailOnTerminationAudit(AuditRepository):
    def append(self, connection, draft):
        if draft.action == "conversation.turn.failed":
            raise RuntimeError("injected termination audit failure")
        return super().append(connection, draft)


def test_conv_012_terminal_audit_failure_rolls_back_to_reconnectable_accept(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    normal = ConversationService(factory)
    conversation = normal.create(
        CreateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("terminal-rollback-parent"),
            request_id=_uuid("terminal-rollback-parent-request"),
            space_id=ids["personal_space"],
        )
    ).conversation
    accepted = AcceptConversationTurnCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=_uuid("terminal-rollback-operation"),
        request_id=_uuid("terminal-rollback-request"),
        conversation_id=conversation.id,
        user_content={"text": "keep the accepted checkpoint"},
    )
    normal.accept_turn(accepted)
    termination = TerminateConversationTurnCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=accepted.operation_id,
        request_id=accepted.request_id,
        conversation_id=conversation.id,
        terminal_state="failed",
        error_code="MODEL_RESPONSE_UNAVAILABLE",
        retryable=True,
    )
    with pytest.raises(RuntimeError, match="injected termination audit failure"):
        ConversationService(
            factory, audit=_FailOnTerminationAudit()
        ).terminate_turn(termination)

    state, events = normal.reconnect_turn(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation.id,
        operation_id=accepted.operation_id,
    )
    assert state.state == "accepted"
    assert state.error is None
    assert [event.event_type for event in events] == ["accepted"]
    assert _table_count(factory, "conversation_message") == 1
    assert _table_count(factory, "conversation_turn_event") == 1

    terminal = normal.terminate_turn(termination)
    assert terminal.state == "failed"
    assert terminal.error is not None
    assert _table_count(factory, "conversation_message") == 1
    assert _table_count(factory, "conversation_turn_event") == 2


def test_conv_013_compact_summary_is_same_space_idempotent_and_versioned(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    service = ConversationService(factory)
    conversation = service.create(
        CreateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("summary-parent-operation"),
            request_id=_uuid("summary-parent-request"),
            space_id=ids["personal_space"],
        )
    ).conversation
    turn_operation = _uuid("summary-turn-operation")
    turn_request = _uuid("summary-turn-request")
    service.accept_turn(
        AcceptConversationTurnCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=turn_operation,
            request_id=turn_request,
            conversation_id=conversation.id,
            user_content={"text": "summarize this"},
        )
    )
    service.complete_turn(
        CompleteConversationTurnCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=turn_operation,
            request_id=turn_request,
            conversation_id=conversation.id,
            assistant_content={"text": "answer"},
            final_response={"finish_reason": "stop"},
        )
    )
    compact_operation = _uuid("summary-operation")
    command = CompactConversationCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=compact_operation,
        request_id=_uuid("summary-request"),
        conversation_id=conversation.id,
        covered_through_sequence_no=2,
        summary_md="First compact summary",
        model_id="summary-model",
        prompt_version="summary-prompt-v1",
        summarizer_version="summary-kernel-v1",
        request_meta={"correlation_id": "conv-013"},
    )

    first = service.compact(command)
    retry = service.compact(
        replace(
            command,
            request_id=_uuid("summary-retry-request"),
            summary_md="A regenerated value must not replace committed output",
        )
    )
    assert retry == first
    assert first.outcome == "created"
    assert first.space_id == ids["personal_space"]
    assert first.summary.id == conversation_summary_id(
        conversation.id, compact_operation
    )
    assert first.summary.status == "active"
    assert service.get_active_summary(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation.id,
    ) == first.summary
    with pytest.raises(ConversationIdempotencyError):
        service.compact(replace(command, covered_through_sequence_no=1))
    with pytest.raises(ConversationIdempotencyError):
        service.compact(
            replace(
                command,
                operation_id=_uuid("summary-missing-prefix-operation"),
                request_id=_uuid("summary-missing-prefix-request"),
                covered_through_sequence_no=3,
            )
        )
    assert _table_count(factory, "operation_dedup") == 3

    replacement = service.compact(
        replace(
            command,
            operation_id=_uuid("summary-replacement-operation"),
            request_id=_uuid("summary-replacement-request"),
            summary_md="Replacement compact summary",
        )
    )
    assert replacement.outcome == "replaced"
    history = service.list_summary_history(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation.id,
    )
    assert [row.id for row in history] == [replacement.summary.id, first.summary.id]
    assert [row.status for row in history] == ["active", "invalidated"]
    assert history[1].invalidation_reason == "superseded_by_compaction"
    assert history[1].row_version == 2
    assert _table_count(factory, "conversation_summary") == 2
    assert _table_count(factory, "operation_dedup") == 4
    assert _table_count(factory, "audit_event") == 5
    assert _table_count(factory, "outbox_event") == 5

    connection = factory.open()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="CONVERSATION_CONTEXT_INVALID"):
            with transaction(connection):
                connection.execute(
                    "UPDATE conversation_summary SET conversation_id=? WHERE id=?",
                    (_uuid("forged-summary-conversation"), replacement.summary.id),
                )
    finally:
        connection.close()
    assert service.get_active_summary(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation.id,
    ) == replacement.summary


class _FailOnCompactAudit(AuditRepository):
    def append(self, connection, draft):
        if draft.action == "conversation.compact":
            raise RuntimeError("injected compact audit failure")
        return super().append(connection, draft)


def test_conv_013_compact_replacement_rolls_back_without_losing_active_summary(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    normal = ConversationService(factory)
    conversation = normal.create(
        CreateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("compact-rollback-parent"),
            request_id=_uuid("compact-rollback-parent-request"),
            space_id=ids["personal_space"],
        )
    ).conversation
    turn_operation = _uuid("compact-rollback-turn")
    turn_request = _uuid("compact-rollback-turn-request")
    normal.accept_turn(
        AcceptConversationTurnCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=turn_operation,
            request_id=turn_request,
            conversation_id=conversation.id,
            user_content={"text": "one"},
        )
    )
    normal.complete_turn(
        CompleteConversationTurnCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=turn_operation,
            request_id=turn_request,
            conversation_id=conversation.id,
            assistant_content={"text": "two"},
            final_response={"finish_reason": "stop"},
        )
    )
    base = CompactConversationCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=_uuid("compact-base"),
        request_id=_uuid("compact-base-request"),
        conversation_id=conversation.id,
        covered_through_sequence_no=2,
        summary_md="Stable active summary",
        model_id="summary-model",
        prompt_version="prompt-v1",
        summarizer_version="summarizer-v1",
    )
    active = normal.compact(base).summary
    failing_command = replace(
        base,
        operation_id=_uuid("compact-failing-replacement"),
        request_id=_uuid("compact-failing-request"),
        summary_md="This replacement must roll back",
    )
    with pytest.raises(RuntimeError, match="injected compact audit failure"):
        ConversationService(factory, audit=_FailOnCompactAudit()).compact(
            failing_command
        )
    assert _table_count(factory, "conversation_summary") == 1
    assert normal.get_active_summary(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation.id,
    ) == active

    replacement = normal.compact(failing_command)
    assert replacement.outcome == "replaced"
    assert _table_count(factory, "conversation_summary") == 2


def test_p3_1_json_session_adapter_reads_only_canonical_runtime_state(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    service = ConversationService(factory)
    conversation = service.create(
        CreateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("compat-runtime-parent"),
            request_id=_uuid("compat-runtime-parent-request"),
            space_id=ids["personal_space"],
            title="Canonical session",
            metadata={"client": "legacy-ui"},
        )
    ).conversation
    operation_id = _uuid("compat-runtime-turn")
    request_id = _uuid("compat-runtime-request")
    service.accept_turn(
        AcceptConversationTurnCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=operation_id,
            request_id=request_id,
            conversation_id=conversation.id,
            user_content={"text": "canonical hello"},
        )
    )
    service.complete_turn(
        CompleteConversationTurnCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=operation_id,
            request_id=request_id,
            conversation_id=conversation.id,
            assistant_content={"text": "canonical reply"},
            final_response={"finish_reason": "stop"},
        )
    )
    service.compact(
        CompactConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("compat-runtime-compact"),
            request_id=_uuid("compat-runtime-compact-request"),
            conversation_id=conversation.id,
            covered_through_sequence_no=2,
            summary_md="Canonical compact summary",
            model_id="summary-model",
            prompt_version="prompt-v1",
            summarizer_version="summarizer-v1",
        )
    )

    adapter = LegacyJsonSessionAdapter(factory)
    assert adapter.reads_legacy_files is False
    assert adapter.writes_legacy_files is False
    assert adapter.migration_authority == "offline_p2_5_only"
    listed = adapter.list_sessions(
        actor_user_id="member01",
        actor_scope_epoch=1,
    )
    assert len(listed) == 1
    assert listed[0]["session_id"] == conversation.id
    assert listed[0]["space_id"] == ids["personal_space"]
    assert listed[0]["message_count"] == 2
    assert listed[0]["first_message"] == "canonical hello"

    first_page = adapter.load_session(
        actor_user_id="member01",
        actor_scope_epoch=1,
        session_reference=conversation.id,
        limit=1,
    )
    assert first_page["message_count"] == 2
    assert first_page["has_more"] is True
    assert first_page["next_message_sequence"] == 1
    assert first_page["compact_summary"] == "Canonical compact summary"
    assert first_page["metadata"] == {"client": "legacy-ui"}
    assert first_page["messages"] == [
        {"content": {"text": "canonical hello"}, "role": "user"}
    ]
    second_page = adapter.load_session(
        actor_user_id="member01",
        actor_scope_epoch=1,
        session_reference=conversation.id,
        after_message_sequence_no=1,
        limit=1,
    )
    assert second_page["has_more"] is False
    assert second_page["messages"] == [
        {"content": {"text": "canonical reply"}, "role": "assistant"}
    ]
    with pytest.raises(LegacySessionNotFoundError):
        adapter.load_session(
            actor_user_id="admin",
            actor_scope_epoch=1,
            session_reference=conversation.id,
        )
    with pytest.raises(LegacySessionScopeError):
        adapter.load_session(
            actor_user_id="member01",
            actor_scope_epoch=2,
            session_reference=conversation.id,
        )


def test_p3_1_json_session_adapter_resolves_p25_hash_without_file_fallback(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    plan = "a" * 64
    parent_record = "rec_" + "1" * 24
    message_record = "rec_" + "2" * 24
    summary_record = "rec_" + "3" * 24
    legacy_session_id = "legacy-session-alpha"
    session_hash = hashlib.sha256(legacy_session_id.encode("utf-8")).hexdigest()
    conversation_id = legacy_conversation_id(plan, parent_record)
    metadata = json.dumps(
        {
            "legacy_p2_5": {
                "bindings": [],
                "child_evidence": [],
                "inventory_record_id": parent_record,
                "metadata": [
                    {
                        "metadata": {"source": "legacy"},
                        "skills_used": ["legacy-skill"],
                    }
                ],
                "plan_sha256": plan,
                "schema_version": 1,
                "session_core_metadata": [],
                "session_id_sha256": session_hash,
                "summaries": ["Imported compact summary"],
                "uploads": [{"name": "legacy.txt"}],
            },
            "schema_version": 1,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    summary_md = "Imported compact summary"
    connection = factory.open()
    try:
        with transaction(connection):
            repository = ConversationRepository()
            repository.put_legacy_conversation(
                connection,
                LegacyConversation(
                    plan_sha256=plan,
                    inventory_record_id=parent_record,
                    conversation_id=conversation_id,
                    owner_user_id="member01",
                    personal_space_id=ids["personal_space"],
                    captured_scope_epoch=1,
                    title="Imported session",
                    metadata_json=metadata,
                    created_at_ms=200,
                ),
            )
            repository.put_legacy_message(
                connection,
                LegacyConversationMessage(
                    plan_sha256=plan,
                    inventory_record_id=message_record,
                    message_id=legacy_conversation_message_id(
                        plan, message_record
                    ),
                    conversation_id=conversation_id,
                    owner_user_id="member01",
                    personal_space_id=ids["personal_space"],
                    sequence_no=1,
                    role="user",
                    content_json='{"content":"legacy hello","role":"user"}',
                    created_at_ms=201,
                ),
            )
            repository.put_legacy_summary(
                connection,
                LegacyConversationSummary(
                    plan_sha256=plan,
                    inventory_record_id=summary_record,
                    summary_id=legacy_conversation_summary_id(
                        plan, summary_record
                    ),
                    conversation_id=conversation_id,
                    owner_user_id="member01",
                    personal_space_id=ids["personal_space"],
                    covered_through_sequence_no=1,
                    summary_md=summary_md,
                    summary_sha256=hashlib.sha256(
                        summary_md.encode("utf-8")
                    ).hexdigest(),
                    created_at_ms=202,
                ),
            )
    finally:
        connection.close()

    adapter = LegacyJsonSessionAdapter(factory)
    assert adapter.resolve_conversation_id(
        actor_user_id="member01",
        actor_scope_epoch=1,
        session_reference=legacy_session_id,
    ) == conversation_id
    loaded = adapter.load_session(
        actor_user_id="member01",
        actor_scope_epoch=1,
        session_reference=legacy_session_id,
    )
    assert loaded["session_id"] == conversation_id
    assert loaded["legacy_session_id_sha256"] == session_hash
    assert loaded["messages"] == [{"content": "legacy hello", "role": "user"}]
    assert loaded["compact_summary"] == summary_md
    assert loaded["metadata"] == {"source": "legacy"}
    assert loaded["skills_used"] == ["legacy-skill"]
    assert loaded["uploaded_files"] == [{"name": "legacy.txt"}]
    with pytest.raises(LegacySessionNotFoundError):
        adapter.load_session(
            actor_user_id="member01",
            actor_scope_epoch=1,
            session_reference="missing-legacy-session",
        )


def test_p3_1_every_committed_replay_revalidates_current_space_access(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    service = ConversationService(factory)
    create_command = CreateConversationCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=_uuid("replay-scope-create"),
        request_id=_uuid("replay-scope-create-request"),
        space_id=ids["part_space"],
    )
    conversation = service.create(create_command).conversation
    turn_command = AcceptConversationTurnCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=_uuid("replay-scope-turn"),
        request_id=_uuid("replay-scope-turn-request"),
        conversation_id=conversation.id,
        user_content={"text": "part-scoped content"},
    )
    service.accept_turn(turn_command)
    completion_command = CompleteConversationTurnCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=turn_command.operation_id,
        request_id=turn_command.request_id,
        conversation_id=conversation.id,
        assistant_content={"text": "part-scoped answer"},
        final_response={"finish_reason": "stop"},
    )
    service.complete_turn(completion_command)
    compact_command = CompactConversationCommand(
        actor_user_id="member01",
        actor_scope_epoch=1,
        operation_id=_uuid("replay-scope-compact"),
        request_id=_uuid("replay-scope-compact-request"),
        conversation_id=conversation.id,
        covered_through_sequence_no=2,
        summary_md="Part-scoped compact summary",
        model_id="summary-model",
        prompt_version="prompt-v1",
        summarizer_version="summarizer-v1",
    )
    service.compact(compact_command)

    connection = factory.open()
    try:
        with transaction(connection):
            SpaceRepository().update_state(
                connection,
                space_id=ids["part_space"],
                expected_row_version=1,
                status="frozen",
                write_enabled=False,
                updated_at_ms=400,
            )
    finally:
        connection.close()

    for replay in (
        lambda: service.create(create_command),
        lambda: service.accept_turn(turn_command),
        lambda: service.complete_turn(completion_command),
        lambda: service.compact(compact_command),
    ):
        with pytest.raises(ConversationServiceScopeError):
            replay()
    with pytest.raises(ConversationServiceScopeError):
        service.get(
            actor_user_id="member01",
            actor_scope_epoch=1,
            conversation_id=conversation.id,
        )
    adapter = LegacyJsonSessionAdapter(factory)
    with pytest.raises(LegacySessionScopeError):
        adapter.load_session(
            actor_user_id="member01",
            actor_scope_epoch=1,
            session_reference=conversation.id,
        )
    entries = service.list_for_owner(
        actor_user_id="member01",
        actor_scope_epoch=1,
    )
    assert len(entries) == 1
    assert entries[0].conversation.space_id == ids["part_space"]
    assert entries[0].access_status == "space_inactive"


def _scope_selector(factory: ConnectionFactory) -> ScopeSelectionService:
    return ScopeSelectionService(
        factory,
        scope_resolver=ScopeResolver(
            FeatureFlags(
                core_enabled=True,
                personal_reads=True,
                personal_writes=False,
                part_reads=True,
                part_writes=False,
                projections=False,
                higher_spaces=False,
            )
        ),
    )


@pytest.mark.scenario("SPACE-004")
@pytest.mark.scenario("SPACE-005")
def test_p36_selector_separates_management_choice_from_immutable_chat_badge(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    conversation = ConversationService(factory).create(
        CreateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("p36-personal-conversation"),
            request_id=_uuid("p36-personal-request"),
            space_id=ids["personal_space"],
            title="Personal history",
        )
    ).conversation

    snapshot = _scope_selector(factory).read(
        ScopeSelectionRequest(
            actor_user_id="member01",
            actor_scope_epoch=1,
            selected_space_id=ids["part_space"],
            conversation_id=conversation.id,
        )
    )
    assert snapshot.actor_user_id == "member01"
    assert snapshot.scope_epoch == 1
    assert snapshot.selected_space_id == ids["part_space"]
    assert [option.kind for option in snapshot.spaces] == ["personal", "part"]
    assert [option.selected for option in snapshot.spaces] == [False, True]
    assert snapshot.spaces[0].name == "내 지식"
    assert snapshot.spaces[1].name == "Runtime Part 0"
    assert snapshot.spaces[1].org_path[-2:] == (
        "Runtime Team",
        "Runtime Part 0",
    )
    badge = snapshot.conversation_badge
    assert badge is not None
    assert badge.conversation_id == conversation.id
    assert badge.space_id == ids["personal_space"]
    assert badge.space_kind == "personal"
    assert badge.space_name == "내 지식"
    assert badge.access_status == "accessible"
    assert badge.accessible is True
    assert badge.immutable is True
    assert snapshot.as_dict()["conversation_badge"]["space_id"] == ids[
        "personal_space"
    ]

    with pytest.raises(ScopeSelectionNotFoundError):
        _scope_selector(factory).read(
            replace(
                ScopeSelectionRequest(
                    actor_user_id="member01",
                    actor_scope_epoch=1,
                ),
                selected_space_id=_uuid("foreign-space"),
            )
        )


@pytest.mark.scenario("CONV-002")
@pytest.mark.scenario("CONV-003")
def test_p36_switch_creates_a_new_conversation_and_preserves_old_history(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    service = ConversationService(factory)
    old = service.create(
        CreateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("p36-old-conversation"),
            request_id=_uuid("p36-old-request"),
            space_id=ids["personal_space"],
            title="Old personal history",
        )
    ).conversation
    turn_operation = _uuid("p36-old-turn")
    turn_request = _uuid("p36-old-turn-request")
    service.accept_turn(
        AcceptConversationTurnCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=turn_operation,
            request_id=turn_request,
            conversation_id=old.id,
            user_content={"text": "old personal message"},
        )
    )
    service.complete_turn(
        CompleteConversationTurnCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=turn_operation,
            request_id=turn_request,
            conversation_id=old.id,
            assistant_content={"text": "old personal answer"},
            final_response={"finish_reason": "stop"},
        )
    )

    new = service.create(
        CreateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("p36-new-part-conversation"),
            request_id=_uuid("p36-new-part-request"),
            space_id=ids["part_space"],
            title="New part conversation",
        )
    ).conversation
    assert new.id != old.id
    assert new.space_id == ids["part_space"]
    assert old.space_id == ids["personal_space"]
    assert [message.role for message in service.list_messages(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=old.id,
    )] == ["user", "assistant"]
    assert service.list_messages(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=new.id,
    ) == ()
    assert service.get(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=old.id,
    ) == old

    old_badge = _scope_selector(factory).read(
        ScopeSelectionRequest("member01", 1, conversation_id=old.id)
    ).conversation_badge
    new_badge = _scope_selector(factory).read(
        ScopeSelectionRequest("member01", 1, conversation_id=new.id)
    ).conversation_badge
    assert old_badge is not None and old_badge.space_id == ids["personal_space"]
    assert new_badge is not None and new_badge.space_id == ids["part_space"]
    assert old_badge.immutable and new_badge.immutable
    assert "space_id" not in {
        field.name for field in __import__("dataclasses").fields(UpdateConversationCommand)
    }


@pytest.mark.scenario("CONV-003")
def test_p36_old_part_badge_survives_scope_move_but_access_fails_closed(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    conversation = ConversationService(factory).create(
        CreateConversationCommand(
            actor_user_id="member01",
            actor_scope_epoch=1,
            operation_id=_uuid("p36-old-part"),
            request_id=_uuid("p36-old-part-request"),
            space_id=ids["part_space"],
            title="Old part history",
        )
    ).conversation
    connection = factory.open()
    try:
        with transaction(connection):
            organization = OrganizationRepository()
            organization.close_current_membership(
                connection,
                user_id="member01",
                expected_membership_id=ids["membership_a"],
                closed_at_ms=300,
            )
            organization.open_membership(
                connection,
                membership_id=ids["membership_b"],
                user_id="member01",
                part_id=ids["part_b"],
                assigned_by="admin",
                reason="P3.6 scope move",
                valid_from_ms=301,
            )
            state = organization.compare_and_swap_user_part(
                connection,
                user_id="member01",
                expected_current_part_id=ids["part_a"],
                target_part_id=ids["part_b"],
                expected_scope_epoch=1,
                expected_row_version=1,
                updated_at_ms=302,
            )
            assert state.scope_epoch == 2
    finally:
        connection.close()

    with pytest.raises(ScopeSelectionScopeError):
        _scope_selector(factory).read(
            ScopeSelectionRequest("member01", 1, conversation_id=conversation.id)
        )
    snapshot = _scope_selector(factory).read(
        ScopeSelectionRequest("member01", 2, conversation_id=conversation.id)
    )
    assert snapshot.selected_space_id == ids["personal_space"]
    assert [option.kind for option in snapshot.spaces] == ["personal"]
    badge = snapshot.conversation_badge
    assert badge is not None
    assert badge.space_id == ids["part_space"]
    assert badge.space_name == "Runtime Part 0"
    assert badge.access_status == "stale_part_scope"
    assert badge.accessible is False
    assert badge.immutable is True
    with pytest.raises(ScopeSelectionNotFoundError):
        _scope_selector(factory).read(
            ScopeSelectionRequest(
                "member01",
                2,
                selected_space_id=ids["part_space"],
                conversation_id=conversation.id,
            )
        )


def test_p36_selector_is_read_only_bounded_and_rejects_foreign_conversation(
    tmp_path: Path,
) -> None:
    source = inspect.getsource(scope_selection_module)
    lowered = source.casefold()
    assert "self._factory.open(read_only=True)" in source
    assert "ConversationService" not in scope_selection_module.__dict__
    for forbidden in ("insert into", "delete from", "replace into"):
        assert forbidden not in lowered

    factory = _factory(tmp_path)
    ids = _seed(factory)
    before = tuple(
        _table_count(factory, table)
        for table in ("conversation", "audit_event", "outbox_event")
    )
    snapshot = _scope_selector(factory).read(
        ScopeSelectionRequest("member01", 1)
    )
    assert snapshot.selected_space_id == ids["personal_space"]
    assert len(snapshot.spaces) == 2
    assert tuple(
        _table_count(factory, table)
        for table in ("conversation", "audit_event", "outbox_event")
    ) == before
    with pytest.raises(ScopeSelectionNotFoundError):
        _scope_selector(factory).read(
            ScopeSelectionRequest(
                "member01",
                1,
                conversation_id=_uuid("foreign-conversation"),
            )
        )


class _P37AgentContext:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def compose(self, request: object) -> object:
        self.requests.append(request)
        return request


class _P37TurnExecutor:
    def __init__(self) -> None:
        self.contexts: list[object] = []
        self.fail = False

    def complete(self, context: object) -> AgentTurnCompletion:
        self.contexts.append(context)
        if self.fail:
            raise RuntimeError("injected canonical executor failure")
        return AgentTurnCompletion(
            assistant_content={"text": "canonical answer"},
            final_response={"finish_reason": "stop"},
        )


class _P37Summarizer:
    def summarize(self, **_: object) -> CompactSummaryCompletion:
        return CompactSummaryCompletion(
            summary_md="# canonical summary\n",
            model_id="test-model",
            prompt_version="p3.7-test",
            summarizer_version="p3.7-test",
        )


@pytest.mark.scenario("SPACE-004")
@pytest.mark.scenario("SPACE-005")
@pytest.mark.scenario("CONV-001")
@pytest.mark.scenario("CONV-002")
@pytest.mark.scenario("CONV-003")
@pytest.mark.scenario("CONV-004")
@pytest.mark.scenario("CONV-011")
@pytest.mark.scenario("CONV-012")
def test_p37_canonical_route_canary_uses_only_server_principal_and_immutable_scope(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    principal = {"id": "member01", "scope_epoch": 1}
    conversations = ConversationService(factory)
    selector = _scope_selector(factory)
    context = _P37AgentContext()
    executor = _P37TurnExecutor()
    app = Flask("p37-canonical-route-canary", static_folder=None)
    container = register_knowledge_core(
        app,
        HostKind.CODE,
        environ={
            "KNOWLEDGE_CORE_ENABLED": "1",
            "KNOWLEDGE_CORE_READS": "1",
            "KNOWLEDGE_CORE_PART_READS": "1",
        },
        services=ServiceRegistry(
            scope_selection=selector,
            conversations=conversations,
            retrieval=CanonicalRetrievalService(
                factory,
                scope_resolver=selector._scopes,
            ),
            management_reads=CanonicalManagementReadService(
                factory,
                scope_resolver=selector._scopes,
            ),
            agent_context=context,
        ),
        canonical_route_bindings=CanonicalRouteBindings(
            turn_executor=executor,
            conversation_summarizer=_P37Summarizer(),
            principal_resolver=lambda: principal,
        ),
    )
    assert container.registered_route_groups == ("canonical_knowledge_v1",)
    assert container.serves_traffic is True
    assert len(
        [
            rule
            for rule in app.url_map.iter_rules()
            if rule.rule.startswith("/api/knowledge/v1")
        ]
    ) == 21
    client = app.test_client()

    spaces = client.get("/api/knowledge/v1/spaces")
    assert spaces.status_code == 200
    assert [(item["id"], item["kind"]) for item in spaces.json["items"]] == [
        (ids["personal_space"], "personal"),
        (ids["part_space"], "part"),
    ]
    assert all("selected" not in item for item in spaces.json["items"])

    actor_override = client.post(
        "/api/knowledge/v1/conversations",
        json={
            "operation_id": _uuid("p37-actor-override"),
            "space_id": ids["part_space"],
            "user_id": "admin",
        },
    )
    assert actor_override.status_code == 400
    duplicate_json = client.post(
        "/api/knowledge/v1/conversations",
        data=(
            '{"operation_id":"' + _uuid("p37-duplicate-one")
            + '","operation_id":"' + _uuid("p37-duplicate-two")
            + '","space_id":"' + ids["part_space"] + '"}'
        ).encode("utf-8"),
        content_type="application/json",
    )
    assert duplicate_json.status_code == 400
    assert client.get("/api/knowledge/v1/conversations").json["items"] == []

    conflicting_alias = client.post(
        "/api/knowledge/v1/conversations",
        json={
            "operation_id": _uuid("p37-conflicting-body-operation"),
            "space_id": ids["part_space"],
        },
        headers={"Idempotency-Key": _uuid("p37-conflicting-header-operation")},
    )
    assert conflicting_alias.status_code == 409
    assert conflicting_alias.json["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert client.get("/api/knowledge/v1/conversations").json["items"] == []

    operation_id = _uuid("p37-create-part-conversation")
    created = client.post(
        "/api/knowledge/v1/conversations",
        json={
            "operation_id": operation_id,
            "space_id": ids["part_space"],
            "title": "Part route canary",
        },
        headers={"Idempotency-Key": operation_id},
    )
    assert created.status_code == 201
    conversation_id = created.json["resource_id"]
    stored = conversations.get(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation_id,
    )
    assert stored.space_id == ids["part_space"]
    assert stored.captured_scope_epoch == 1

    forged_update = client.patch(
        f"/api/knowledge/v1/conversations/{conversation_id}",
        json={
            "expected_row_version": stored.row_version,
            "operation_id": _uuid("p37-forged-update"),
            "space_id": ids["personal_space"],
            "title": "forged",
        },
    )
    assert forged_update.status_code == 400
    assert forged_update.json["error"]["code"] == "VALIDATION_ERROR"
    assert conversations.get(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation_id,
    ).space_id == ids["part_space"]

    forged_turn = client.post(
        f"/api/knowledge/v1/conversations/{conversation_id}/turns/stream",
        json={
            "message": "should be rejected",
            "operation_id": _uuid("p37-forged-turn"),
            "space_id": ids["personal_space"],
        },
    )
    assert forged_turn.status_code == 400
    assert conversations.list_messages(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation_id,
    ) == ()

    turn_operation = _uuid("p37-part-turn")
    turn = client.post(
        f"/api/knowledge/v1/conversations/{conversation_id}/turns/stream",
        json={"message": "part canary\nquery", "operation_id": turn_operation},
        headers={"Accept": "text/event-stream"},
    )
    assert turn.status_code == 200
    assert turn.mimetype == "text/event-stream"
    assert "event: accepted" in turn.text
    assert "event: completed" in turn.text
    assert len(context.requests) == 1
    assert len(executor.contexts) == 1
    context_request = context.requests[0]
    assert context_request.actor_user_id == "member01"
    assert context_request.actor_scope_epoch == 1
    assert context_request.conversation_id == conversation_id
    assert not hasattr(context_request, "space_id")
    messages = conversations.list_messages(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation_id,
    )
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].content == {"content": {"text": "part canary\nquery"}, "role": "user"}

    replay = client.post(
        f"/api/knowledge/v1/conversations/{conversation_id}/turns/stream",
        json={"message": "part canary\nquery", "operation_id": turn_operation},
        headers={"Accept": "text/event-stream"},
    )
    assert replay.status_code == 200
    assert "event: accepted" in replay.text
    assert "event: completed" in replay.text
    assert len(context.requests) == 1
    assert len(executor.contexts) == 1
    assert len(conversations.list_messages(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=conversation_id,
    )) == 2

    resumed = client.post(
        f"/api/knowledge/v1/conversations/{conversation_id}/turns/stream",
        json={
            "message": "part canary\nquery",
            "operation_id": turn_operation,
            "resume_after_event_sequence": 1,
        },
        headers={
            "Accept": "text/event-stream",
            "Idempotency-Key": turn_operation,
        },
    )
    assert resumed.status_code == 200
    assert "event: accepted" not in resumed.text
    assert "event: completed" in resumed.text
    assert len(executor.contexts) == 1

    listing = client.get("/api/knowledge/v1/conversations")
    assert listing.status_code == 200
    assert listing.json["items"][0]["space"]["id"] == ids["part_space"]
    assert listing.json["items"][0]["turn_count"] == 1
    assert listing.json["items"][0]["completed_turn_count"] == 1
    assert listing.json["items"][0]["latest_turn_state"] == "completed"
    detail = client.get(f"/api/knowledge/v1/conversations/{conversation_id}")
    assert detail.status_code == 200
    assert detail.json["conversation"]["space"]["id"] == ids["part_space"]
    assert [message["role"] for message in detail.json["messages"]] == [
        "user",
        "assistant",
    ]

    created_ids = {conversation_id}
    archived_conversation_id = None
    for index in range(2):
        page_operation = _uuid(f"p37-page-conversation-{index}")
        page_created = client.post(
            "/api/knowledge/v1/conversations",
            json={
                "operation_id": page_operation,
                "space_id": ids["part_space"],
                "title": f"Page canary {index}",
            },
            headers={"Idempotency-Key": page_operation},
        )
        assert page_created.status_code == 201
        page_conversation_id = page_created.json["resource_id"]
        created_ids.add(page_conversation_id)
        if index == 0:
            renamed = client.patch(
                f"/api/knowledge/v1/conversations/{page_conversation_id}",
                json={
                    "expected_row_version": page_created.json["row_version"],
                    "operation_id": _uuid("p37-page-conversation-rename"),
                    "title": "Renamed route canary",
                },
            )
            assert renamed.status_code == 200
            archived = client.patch(
                f"/api/knowledge/v1/conversations/{page_conversation_id}",
                json={
                    "archived": True,
                    "expected_row_version": renamed.json["row_version"],
                    "operation_id": _uuid("p37-page-conversation-archive"),
                },
            )
            assert archived.status_code == 200
            archived_conversation_id = page_conversation_id
    first_page = client.get("/api/knowledge/v1/conversations?limit=2")
    assert first_page.status_code == 200
    assert first_page.json["has_more"] is True
    assert isinstance(first_page.json["next_cursor"], str)
    second_page = client.get(
        "/api/knowledge/v1/conversations",
        query_string={"cursor": first_page.json["next_cursor"], "limit": 2},
    )
    assert second_page.status_code == 200
    assert second_page.json["has_more"] is False
    assert second_page.json["next_cursor"] is None
    paged_ids = {
        item["id"]
        for page in (first_page, second_page)
        for item in page.json["items"]
    }
    assert paged_ids == created_ids
    archived_summary = next(
        item
        for page in (first_page, second_page)
        for item in page.json["items"]
        if item["id"] == archived_conversation_id
    )
    assert archived_summary["archived"] is True
    assert archived_summary["title"] == "Renamed route canary"
    assert archived_summary["space"]["id"] == ids["part_space"]
    assert client.get(
        "/api/knowledge/v1/conversations?cursor=not-a-canonical-cursor"
    ).status_code == 400
    assert client.get(
        "/api/knowledge/v1/conversations?limit=1&limit=2"
    ).status_code == 400

    executor.fail = True
    failed_operation = _uuid("p37-failed-turn")
    failed = client.post(
        f"/api/knowledge/v1/conversations/{conversation_id}/turns/stream",
        json={"message": "fail closed", "operation_id": failed_operation},
        headers={
            "Accept": "text/event-stream",
            "Idempotency-Key": failed_operation,
        },
    )
    assert failed.status_code == 200
    assert failed.mimetype == "text/event-stream"
    assert "event: accepted" in failed.text
    assert "event: failed" in failed.text
    assert "injected canonical executor failure" not in failed.text
    executor_calls = len(executor.contexts)
    failed_replay = client.post(
        f"/api/knowledge/v1/conversations/{conversation_id}/turns/stream",
        json={"message": "fail closed", "operation_id": failed_operation},
        headers={"Accept": "text/event-stream"},
    )
    assert failed_replay.status_code == 200
    assert "event: failed" in failed_replay.text
    assert len(executor.contexts) == executor_calls
    failed_listing = client.get("/api/knowledge/v1/conversations")
    failed_summary = next(
        item
        for item in failed_listing.json["items"]
        if item["id"] == conversation_id
    )
    assert failed_summary["turn_count"] == 2
    assert failed_summary["completed_turn_count"] == 1
    assert failed_summary["latest_turn_state"] == "failed"

    switched_operation = _uuid("p37-new-personal-conversation")
    switched = client.post(
        "/api/knowledge/v1/conversations",
        json={
            "operation_id": switched_operation,
            "space_id": ids["personal_space"],
            "title": "New personal scope",
        },
        headers={"Idempotency-Key": switched_operation},
    )
    assert switched.status_code == 201
    assert switched.json["resource_id"] != conversation_id
    switched_record = conversations.get(
        actor_user_id="member01",
        actor_scope_epoch=1,
        conversation_id=switched.json["resource_id"],
    )
    assert switched_record.space_id == ids["personal_space"]
    preserved = client.get(f"/api/knowledge/v1/conversations/{conversation_id}")
    assert preserved.status_code == 200
    assert preserved.json["conversation"]["space"]["id"] == ids["part_space"]
    assert [message["role"] for message in preserved.json["messages"]] == [
        "user",
        "assistant",
        "user",
    ]

    connection = factory.open()
    try:
        with transaction(connection):
            organization = OrganizationRepository()
            organization.close_current_membership(
                connection,
                user_id="member01",
                expected_membership_id=ids["membership_a"],
                closed_at_ms=300,
            )
            organization.open_membership(
                connection,
                membership_id=ids["membership_b"],
                user_id="member01",
                part_id=ids["part_b"],
                assigned_by="admin",
                reason="P3.7 route scope move",
                valid_from_ms=301,
            )
            organization.compare_and_swap_user_part(
                connection,
                user_id="member01",
                expected_current_part_id=ids["part_a"],
                target_part_id=ids["part_b"],
                expected_scope_epoch=1,
                expected_row_version=1,
                updated_at_ms=302,
            )
    finally:
        connection.close()
    principal["scope_epoch"] = 2
    stale_detail = client.get(
        f"/api/knowledge/v1/conversations/{conversation_id}"
    )
    assert stale_detail.status_code == 409
    assert stale_detail.json["error"]["code"] == "SCOPE_STALE"
    stale_list = client.get("/api/knowledge/v1/conversations?limit=100")
    assert stale_list.status_code == 200, stale_list.json
    stale_badge = next(
        item for item in stale_list.json["items"] if item["id"] == conversation_id
    )
    assert stale_badge["space"]["id"] == ids["part_space"]
    assert stale_badge["accessible"] is False

    principal.update({"id": "admin", "scope_epoch": 1})
    foreign = client.get(f"/api/knowledge/v1/conversations/{conversation_id}")
    assert foreign.status_code in {404, 409}
    principal.clear()
    anonymous = client.get("/api/knowledge/v1/spaces")
    assert anonymous.status_code == 401
    assert anonymous.json["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_p37_route_registration_is_preflighted_and_never_partially_shadows(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)
    _seed(factory)
    selector = _scope_selector(factory)
    dependencies = {
        "scope_selection_service": selector,
        "conversation_service": ConversationService(factory),
        "retrieval_service": CanonicalRetrievalService(
            factory,
            scope_resolver=selector._scopes,
        ),
        "management_read_service": CanonicalManagementReadService(
            factory,
            scope_resolver=selector._scopes,
        ),
        "agent_context_service": _P37AgentContext(),
        "turn_executor": _P37TurnExecutor(),
        "conversation_summarizer": _P37Summarizer(),
        "principal_resolver": lambda: {"id": "member01", "scope_epoch": 1},
    }

    colliding = Flask("p37-variable-collision", static_folder=None)
    colliding.add_url_rule(
        "/api/knowledge/v1/conversations/<string:any_conversation>",
        endpoint="shadow_conversation",
        view_func=lambda any_conversation: any_conversation,
        methods=["GET"],
    )
    before = tuple(colliding.url_map.iter_rules())
    with pytest.raises(RuntimeError, match="canonical route already exists"):
        register_canonical_routes(colliding, **dependencies)
    assert tuple(colliding.url_map.iter_rules()) == before
    assert not getattr(
        colliding, "_knowledge_core_canonical_v1_registered", False
    )

    endpoint_collision = Flask("p37-endpoint-collision", static_folder=None)
    endpoint_collision.add_url_rule(
        "/unrelated",
        endpoint="knowledge_v1_spaces",
        view_func=lambda: "shadow",
    )
    endpoint_before = tuple(endpoint_collision.url_map.iter_rules())
    with pytest.raises(RuntimeError, match="endpoint already exists"):
        register_canonical_routes(endpoint_collision, **dependencies)
    assert tuple(endpoint_collision.url_map.iter_rules()) == endpoint_before

    served = Flask("p37-late-registration", static_folder=None)
    served.test_client().get("/")
    with pytest.raises(RuntimeError, match="before the first request"):
        register_canonical_routes(served, **dependencies)
    assert not [
        rule
        for rule in served.url_map.iter_rules()
        if rule.rule.startswith("/api/knowledge/v1")
    ]

    nonempty = Blueprint("p37_nonempty", __name__)
    nonempty.add_url_rule("/shadow", view_func=lambda: "shadow")
    with pytest.raises(RuntimeError, match="dedicated empty Blueprint"):
        register_canonical_routes(nonempty, **dependencies)

    blueprint = Blueprint("p37_canonical", __name__)
    registered = register_canonical_routes(blueprint, **dependencies)
    assert len(registered) == 21
    assert {
        "/api/knowledge/v1/spaces/<space_id>/items/<item_id>",
        "/api/knowledge/v1/spaces/<space_id>/items/<item_id>/revisions",
        "/api/knowledge/v1/spaces/<space_id>/items/<item_id>/revisions/<revision_id>",
        "/api/knowledge/v1/spaces/<space_id>/items/<item_id>/compare",
    }.issubset(set(registered))
    host = Flask("p37-blueprint-host", static_folder=None)
    host.add_url_rule(
        "/api/knowledge/v1/spaces",
        endpoint="host_shadow_spaces",
        view_func=lambda: "shadow",
        methods=["GET"],
    )
    host_before = tuple(host.url_map.iter_rules())
    with pytest.raises(RuntimeError, match="Blueprint host"):
        host.register_blueprint(blueprint)
    assert tuple(host.url_map.iter_rules()) == host_before
