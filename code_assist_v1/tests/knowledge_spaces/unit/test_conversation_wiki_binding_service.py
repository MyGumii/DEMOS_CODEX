from __future__ import annotations

from uuid import UUID, uuid5

import pytest
from flask import Flask

from code_assist_v1.knowledge_core import HostKind, register_knowledge_core
from code_assist_v1.knowledge_core.agent_runtime import (
    compose_canonical_agent_runtime,
)
from code_assist_v1.knowledge_core.db import transaction
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.migrations import CANONICAL_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.repositories.conversations import (
    ConversationRepository,
)
from code_assist_v1.knowledge_core.repositories.personal_wikis import (
    PersonalWikiProfileRepository,
)
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.services.agent_context import (
    AgentContextCorpusClosureStaleError,
    AgentContextWikiArchivedHistoryOnlyError,
    AgentTurnContextRequest,
    AgentTurnContextService,
)
from code_assist_v1.knowledge_core.services.conversations import (
    AcceptConversationTurnCommand,
    AddConversationPinCommand,
    CompactConversationCommand,
    CompleteConversationTurnCommand,
    ConversationCorpusClosureStaleError,
    ConversationService,
    ConversationServiceScopeError,
    ConversationSessionScopeStaleError,
    ConversationWikiArchivedHistoryOnlyError,
    CreateConversationCommand,
    RemoveConversationPinCommand,
    UpdateConversationCommand,
)
from code_assist_v1.knowledge_core.services.personal_wikis import (
    ArchivePersonalWikiCommand,
    CreatePersonalWikiCommand,
    PersonalWikiLifecycleService,
    RestorePersonalWikiCommand,
)
from code_assist_v1.knowledge_core.services.scope_selection import (
    ScopeSelectionRequest,
    ScopeSelectionService,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_migration_v13_direct_org_affiliation import (
    _migrate as _migrate_catalog,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_conversation_pins_contexts import (
    _publish_item,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_personal_wiki_lifecycle_service import (
    _seed,
    _seed_wiki_identity_fixture,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_higher_authority_service import (
    _assign_leader as _assign_higher_leader,
    _epoch as _higher_epoch,
    _factory as _higher_factory,
    _role_command as _higher_role_command,
    _seed as _seed_higher,
    _service as _higher_service,
)


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("60738b93-c29d-46df-9498-ff36f731cbe7")
_CURSOR_SECRET = b"sbe-2.2-conversation-wiki-cursor-v1"


def _migrate_current(value):
    return _migrate_catalog(value, CANONICAL_MIGRATION_CATALOG)


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _stack(factory):
    flags = FeatureFlags(
        core_enabled=True,
        multi_wiki_schema=True,
        multi_wiki_reads=True,
        conversation_wiki_binding=True,
        personal_reads=True,
    )
    scopes = ScopeResolver(flags)
    repository = ConversationRepository(conversation_wiki_binding=True)
    conversations = ConversationService(
        factory,
        conversations=repository,
        scope_resolver=scopes,
    )
    return (
        conversations,
        ScopeSelectionService(
            factory,
            scope_resolver=scopes,
            conversations=repository,
        ),
        AgentTurnContextService(
            factory,
            scope_resolver=scopes,
            conversations=repository,
        ),
        repository,
    )


def _personal_service(factory) -> PersonalWikiLifecycleService:
    return PersonalWikiLifecycleService(factory, cursor_secret=_CURSOR_SECRET)


def _new_wiki(factory, *, label: str = "Projects"):
    return _personal_service(factory).create(
        CreatePersonalWikiCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid(f"wiki:create:{label}"),
            request_id=_uuid(f"wiki:create:{label}:request"),
            display_name=label,
            expected_row_version=1,
        )
    )


def _new_conversation(service, *, label: str, space_id: str):
    return service.create(
        CreateConversationCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid(f"conversation:{label}"),
            request_id=_uuid(f"conversation:{label}:request"),
            space_id=space_id,
            title=label,
        )
    ).conversation


def _accept(service, *, conversation_id: str, label: str):
    operation_id = _uuid(f"turn:{label}")
    request_id = _uuid(f"turn:{label}:request")
    result = service.accept_turn(
        AcceptConversationTurnCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=operation_id,
            request_id=request_id,
            conversation_id=conversation_id,
            user_content={"text": f"question {label}"},
        )
    )
    return result, operation_id, request_id


def _complete(
    service,
    *,
    conversation_id: str,
    operation_id: str,
    request_id: str,
    label: str,
):
    return service.complete_turn(
        CompleteConversationTurnCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=operation_id,
            request_id=request_id,
            conversation_id=conversation_id,
            assistant_content={"text": f"answer {label}"},
            final_response={"citations": [f"citation:{label}"], "finish_reason": "stop"},
        )
    )


def _archive(factory, *, space_id: str, row_version: int, label: str):
    return _personal_service(factory).archive(
        ArchivePersonalWikiCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid(f"wiki:archive:{label}"),
            request_id=_uuid(f"wiki:archive:{label}:request"),
            space_id=space_id,
            expected_row_version=row_version,
            reason="SBE-2.2 contract test",
        )
    )


@pytest.mark.scenario("WIKI-013", "WIKI-014")
def test_exact_authorized_wiki_is_bound_and_saved_session_overrides_client_state(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate_current(isolated_knowledge_layout)
    ids = _seed(factory, ("owner", "user"), ("other", "user"))
    wiki = _new_wiki(factory, label="Project Atlas")
    conversations, selector, _, repository = _stack(factory)

    conversation = _new_conversation(
        conversations,
        label="atlas-session",
        space_id=wiki.resource_id,
    )
    turn, _, _ = _accept(
        conversations,
        conversation_id=conversation.id,
        label="atlas-first",
    )
    connection = factory.open(read_only=True)
    try:
        events = repository.list_turn_events(
            connection,
            turn_id=turn.turn_id,
            conversation_id=conversation.id,
            limit=1,
        )
    finally:
        connection.close()
    accepted = events[0].payload
    assert accepted["conversation_binding"]["selected_view_space_id"] == wiki.resource_id
    assert accepted["conversation_binding"]["selected_view_kind"] == "personal"
    assert accepted["conversation_binding_sha256"]

    snapshot = selector.read(
        ScopeSelectionRequest(
            actor_user_id="owner",
            actor_scope_epoch=1,
            selected_space_id=ids["owner"],
            conversation_id=conversation.id,
        )
    )
    assert snapshot.selected_space_id == wiki.resource_id
    assert sum(option.selected for option in snapshot.spaces) == 1
    selected = next(option for option in snapshot.spaces if option.selected)
    assert selected.name == "Project Atlas"
    assert snapshot.conversation_badge is not None
    assert snapshot.conversation_badge.space_id == wiki.resource_id

    with pytest.raises(ConversationServiceScopeError):
        _new_conversation(
            conversations,
            label="forged-foreign",
            space_id=ids["other"],
        )

    _complete(
        conversations,
        conversation_id=conversation.id,
        operation_id=turn.operation_id,
        request_id=turn.request_id,
        label="atlas-first",
    )
    archived = _archive(
        factory,
        space_id=wiki.resource_id,
        row_version=wiki.row_version,
        label="atlas",
    )
    assert archived.lifecycle_generation == wiki.lifecycle_generation + 1
    with pytest.raises(ConversationWikiArchivedHistoryOnlyError):
        _new_conversation(
            conversations,
            label="archived-normal-mode",
            space_id=wiki.resource_id,
        )
    assert _new_conversation(
        conversations,
        label="atlas-session",
        space_id=wiki.resource_id,
    ).id == conversation.id
    assert _accept(
        conversations,
        conversation_id=conversation.id,
        label="atlas-first",
    )[0].state == "completed"
    with pytest.raises(ConversationWikiArchivedHistoryOnlyError):
        conversations.update(
            UpdateConversationCommand(
                actor_user_id="owner",
                actor_scope_epoch=1,
                operation_id=_uuid("archived:update"),
                request_id=_uuid("archived:update:request"),
                conversation_id=conversation.id,
                expected_row_version=conversation.row_version,
                title="must not update",
            )
        )
    with pytest.raises(ConversationWikiArchivedHistoryOnlyError):
        conversations.compact(
            CompactConversationCommand(
                actor_user_id="owner",
                actor_scope_epoch=1,
                operation_id=_uuid("archived:compact"),
                request_id=_uuid("archived:compact:request"),
                conversation_id=conversation.id,
                covered_through_sequence_no=2,
                summary_md="must not compact",
                model_id="local-test-model",
                prompt_version="sbe-2.2",
                summarizer_version="sbe-2.2",
            )
        )


@pytest.mark.scenario("WIKI-015")
def test_switching_wiki_creates_isolated_conversation_state_and_cache_identity(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate_current(isolated_knowledge_layout)
    ids = _seed(factory, ("owner", "user"))
    wiki = _new_wiki(factory, label="Engineering")
    _seed_wiki_identity_fixture(
        factory,
        space_id=wiki.resource_id,
        label="conversation-binding",
    )
    conversations, _, agent_context, repository = _stack(factory)
    old = _new_conversation(
        conversations,
        label="engineering-session",
        space_id=wiki.resource_id,
    )
    old_turn, old_operation, old_request = _accept(
        conversations,
        conversation_id=old.id,
        label="engineering",
    )
    _complete(
        conversations,
        conversation_id=old.id,
        operation_id=old_operation,
        request_id=old_request,
        label="engineering",
    )
    conversations.compact(
        CompactConversationCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid("compact:engineering"),
            request_id=_uuid("compact:engineering:request"),
            conversation_id=old.id,
            covered_through_sequence_no=2,
            summary_md="Engineering conversation summary",
            model_id="local-test-model",
            prompt_version="sbe-2.2",
            summarizer_version="sbe-2.2",
        )
    )
    connection = factory.open(read_only=True)
    try:
        item_id, revision_id = connection.execute(
            """SELECT i.id,i.current_revision_id FROM knowledge_item AS i
               WHERE i.space_id=? ORDER BY i.id LIMIT 1""",
            (wiki.resource_id,),
        ).fetchone()
    finally:
        connection.close()
    connection = factory.open()
    try:
        with transaction(connection):
            _publish_item(
                connection,
                owner="owner",
                space_id=wiki.resource_id,
                item_id=item_id,
                revision_id=revision_id,
                number=922,
            )
    finally:
        connection.close()
    conversations.add_pin(
        AddConversationPinCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid("pin:engineering"),
            request_id=_uuid("pin:engineering:request"),
            conversation_id=old.id,
            item_id=item_id,
            revision_id=revision_id,
        )
    )

    new = _new_conversation(
        conversations,
        label="default-session",
        space_id=ids["owner"],
    )
    new_turn, _, _ = _accept(
        conversations,
        conversation_id=new.id,
        label="default",
    )
    bundle = agent_context.compose(
        AgentTurnContextRequest(
            actor_user_id="owner",
            actor_scope_epoch=1,
            conversation_id=new.id,
            turn_id=new_turn.turn_id,
            neighbor_limit=0,
            event_limit=0,
        )
    )

    assert old.id != new.id and old.space_id != new.space_id
    assert len(conversations.list_messages(
        actor_user_id="owner", actor_scope_epoch=1, conversation_id=old.id
    )) == 2
    assert len(conversations.list_messages(
        actor_user_id="owner", actor_scope_epoch=1, conversation_id=new.id
    )) == 1
    assert conversations.get_active_summary(
        actor_user_id="owner", actor_scope_epoch=1, conversation_id=old.id
    ) is not None
    assert conversations.get_active_summary(
        actor_user_id="owner", actor_scope_epoch=1, conversation_id=new.id
    ) is None
    assert len(conversations.list_pins(
        actor_user_id="owner", actor_scope_epoch=1, conversation_id=old.id
    )) == 1
    assert conversations.list_pins(
        actor_user_id="owner", actor_scope_epoch=1, conversation_id=new.id
    ) == ()

    connection = factory.open(read_only=True)
    try:
        old_event = repository.list_turn_events(
            connection,
            turn_id=old_turn.turn_id,
            conversation_id=old.id,
            limit=1,
        )[0]
        new_event = repository.list_turn_events(
            connection,
            turn_id=new_turn.turn_id,
            conversation_id=new.id,
            limit=1,
        )[0]
    finally:
        connection.close()
    assert (
        old_event.payload["conversation_binding_sha256"]
        != new_event.payload["conversation_binding_sha256"]
    )
    assert (
        bundle.conversation_binding_sha256
        == new_event.payload["conversation_binding_sha256"]
    )
    assert bundle.selected_view_space_id == new.space_id
    assert bundle.corpus_closure_sha256 == (
        new_event.payload["conversation_binding"]["corpus_closure_sha256"]
    )
    _archive(
        factory,
        space_id=wiki.resource_id,
        row_version=wiki.row_version,
        label="engineering-history",
    )
    assert len(conversations.list_messages(
        actor_user_id="owner", actor_scope_epoch=1, conversation_id=old.id
    )) == 2
    assert conversations.get_active_summary(
        actor_user_id="owner", actor_scope_epoch=1, conversation_id=old.id
    ) is not None
    assert len(conversations.list_pins(
        actor_user_id="owner", actor_scope_epoch=1, conversation_id=old.id
    )) == 1
    with pytest.raises(ConversationWikiArchivedHistoryOnlyError):
        conversations.add_pin(
            AddConversationPinCommand(
                actor_user_id="owner",
                actor_scope_epoch=1,
                operation_id=_uuid("pin:engineering:archived"),
                request_id=_uuid("pin:engineering:archived:request"),
                conversation_id=old.id,
                item_id=item_id,
                revision_id=revision_id,
            )
        )
    with pytest.raises(ConversationWikiArchivedHistoryOnlyError):
        conversations.remove_pin(
            RemoveConversationPinCommand(
                actor_user_id="owner",
                actor_scope_epoch=1,
                operation_id=_uuid("pin:engineering:remove-archived"),
                request_id=_uuid("pin:engineering:remove-archived:request"),
                conversation_id=old.id,
                item_id=item_id,
            )
        )


@pytest.mark.scenario("WIKI-016", "WIKI-017")
def test_archived_history_is_read_only_and_restore_rejects_pre_archive_turn_fence(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate_current(isolated_knowledge_layout)
    _seed(factory, ("owner", "user"))
    wiki = _new_wiki(factory, label="Lifecycle")
    conversations, selector, agent_context, _ = _stack(factory)
    conversation = _new_conversation(
        conversations,
        label="lifecycle-session",
        space_id=wiki.resource_id,
    )
    completed_turn, completed_operation, completed_request = _accept(
        conversations,
        conversation_id=conversation.id,
        label="before-archive-completed",
    )
    _complete(
        conversations,
        conversation_id=conversation.id,
        operation_id=completed_operation,
        request_id=completed_request,
        label="before-archive-completed",
    )
    pending, pending_operation, pending_request = _accept(
        conversations,
        conversation_id=conversation.id,
        label="before-archive-pending",
    )

    archived = _archive(
        factory,
        space_id=wiki.resource_id,
        row_version=wiki.row_version,
        label="lifecycle",
    )
    assert conversations.get(
        actor_user_id="owner",
        actor_scope_epoch=1,
        conversation_id=conversation.id,
    ).id == conversation.id
    assert len(conversations.list_messages(
        actor_user_id="owner",
        actor_scope_epoch=1,
        conversation_id=conversation.id,
    )) == 3
    archived_snapshot = selector.read(
        ScopeSelectionRequest("owner", 1, conversation_id=conversation.id)
    )
    assert archived_snapshot.selected_space_id == wiki.resource_id
    assert archived_snapshot.conversation_badge is not None
    assert archived_snapshot.conversation_badge.history_only is True
    assert next(
        option for option in archived_snapshot.spaces if option.selected
    ).history_only is True

    with pytest.raises(ConversationWikiArchivedHistoryOnlyError):
        _accept(
            conversations,
            conversation_id=conversation.id,
            label="while-archived",
        )
    with pytest.raises(AgentContextWikiArchivedHistoryOnlyError):
        agent_context.compose(
            AgentTurnContextRequest(
                actor_user_id="owner",
                actor_scope_epoch=1,
                conversation_id=conversation.id,
                turn_id=pending.turn_id,
            )
        )

    restored = _personal_service(factory).restore(
        RestorePersonalWikiCommand(
            actor_user_id="owner",
            actor_scope_epoch=1,
            operation_id=_uuid("wiki:restore:lifecycle"),
            request_id=_uuid("wiki:restore:lifecycle:request"),
            space_id=wiki.resource_id,
            expected_row_version=archived.row_version,
            make_default=False,
        )
    )
    assert restored.lifecycle_generation == archived.lifecycle_generation + 1
    with pytest.raises(ConversationCorpusClosureStaleError):
        _complete(
            conversations,
            conversation_id=conversation.id,
            operation_id=pending_operation,
            request_id=pending_request,
            label="stale-after-restore",
        )
    with pytest.raises(AgentContextCorpusClosureStaleError):
        agent_context.compose(
            AgentTurnContextRequest(
                actor_user_id="owner",
                actor_scope_epoch=1,
                conversation_id=conversation.id,
                turn_id=pending.turn_id,
            )
        )

    fresh, _, _ = _accept(
        conversations,
        conversation_id=conversation.id,
        label="after-explicit-restore",
    )
    assert fresh.conversation_id == conversation.id
    connection = factory.open(read_only=True)
    try:
        profile = PersonalWikiProfileRepository().require_owned(
            connection,
            owner_user_id="owner",
            space_id=wiki.resource_id,
        )
    finally:
        connection.close()
    assert profile.lifecycle_status == "active"
    assert profile.lifecycle_generation == restored.lifecycle_generation


@pytest.mark.scenario("WIKI-017")
def test_higher_role_episode_change_invalidates_scope_before_accepted_team_turn_commit(
    isolated_knowledge_layout,
) -> None:
    factory = _higher_factory(isolated_knowledge_layout)
    ids = _seed_higher(factory)
    authority = _higher_service(factory)
    assigned = _assign_higher_leader(factory, ids, authority)
    assert assigned.assignment_id is not None
    assert assigned.assignment_generation is not None
    assert assigned.assignment_row_version is not None

    flags = FeatureFlags(
        core_enabled=True,
        multi_wiki_schema=True,
        multi_wiki_reads=True,
        conversation_wiki_binding=True,
        personal_reads=True,
        part_reads=True,
        higher_spaces=True,
        team_writes=True,
        responsibility_writes=True,
    )
    scopes = ScopeResolver(flags)
    repository = ConversationRepository(
        allow_higher_spaces=True,
        conversation_wiki_binding=True,
    )
    conversations = ConversationService(
        factory,
        conversations=repository,
        scope_resolver=scopes,
    )
    epoch = _higher_epoch(factory, "leader")
    conversation = conversations.create(
        CreateConversationCommand(
            actor_user_id="leader",
            actor_scope_epoch=epoch,
            operation_id=_uuid("higher:conversation"),
            request_id=_uuid("higher:conversation:request"),
            space_id=ids["team_space_a"],
            title="Team closure fence",
        )
    ).conversation
    operation_id = _uuid("higher:turn")
    request_id = _uuid("higher:turn:request")
    conversations.accept_turn(
        AcceptConversationTurnCommand(
            actor_user_id="leader",
            actor_scope_epoch=epoch,
            operation_id=operation_id,
            request_id=request_id,
            conversation_id=conversation.id,
            user_content={"text": "team closure"},
        )
    )

    ended = authority.set_role(
        _higher_role_command(
            factory,
            ids,
            operation="role:end-for-conversation-fence",
            assignee=None,
            expected_assignment=assigned.assignment_id,
            expected_generation=assigned.assignment_generation,
            expected_assignment_version=assigned.assignment_row_version,
            expected_space_version=assigned.space_row_version,
            enable_writes=False,
        )
    )
    assert ended.status == "ended"
    with pytest.raises(ConversationSessionScopeStaleError):
        conversations.complete_turn(
            CompleteConversationTurnCommand(
                actor_user_id="leader",
                actor_scope_epoch=epoch,
                operation_id=operation_id,
                request_id=request_id,
                conversation_id=conversation.id,
                assistant_content={"text": "must not commit"},
                final_response={"finish_reason": "stop"},
            )
        )


@pytest.mark.scenario("WIKI-013", "WIKI-016")
def test_canonical_api_opens_archived_history_and_rejects_normal_mode_turn(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate_current(isolated_knowledge_layout)
    _seed(factory, ("owner", "user"))
    wiki = _new_wiki(factory, label="Archived API")
    flags = FeatureFlags(
        core_enabled=True,
        multi_wiki_schema=True,
        multi_wiki_reads=True,
        conversation_wiki_binding=True,
        personal_reads=True,
    )
    runtime = compose_canonical_agent_runtime(
        factory,
        flags=flags,
        principal_resolver=lambda: {"id": "owner", "scope_epoch": 1},
        personal_wiki_cursor_secret=_CURSOR_SECRET,
    )
    conversation = _new_conversation(
        runtime.services.conversations,
        label="archived-api-session",
        space_id=wiki.resource_id,
    )
    _archive(
        factory,
        space_id=wiki.resource_id,
        row_version=wiki.row_version,
        label="archived-api",
    )

    app = Flask("sbe-2.2-archived-api", static_folder=None)
    register_knowledge_core(
        app,
        HostKind.CODE,
        environ={
            "KNOWLEDGE_CORE_ENABLED": "1",
            "KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA": "1",
            "KNOWLEDGE_CORE_MULTI_WIKI_READS": "1",
            "KNOWLEDGE_CORE_CONVERSATION_WIKI_BINDING": "1",
            "KNOWLEDGE_CORE_READS": "1",
        },
        services=runtime.services,
        canonical_route_bindings=runtime.route_bindings,
    )
    client = app.test_client()
    detail = client.get(
        f"/api/knowledge/v1/conversations/{conversation.id}"
    )
    assert detail.status_code == 200, detail.json
    assert detail.json["conversation"]["history_only"] is True
    assert detail.json["conversation"]["access_status"] == "wiki_archived"
    assert (
        detail.json["conversation"]["selected_view_space_id"]
        == wiki.resource_id
    )

    operation_id = _uuid("archived-api:turn")
    rejected = client.post(
        f"/api/knowledge/v1/conversations/{conversation.id}/turns/stream",
        json={"message": "must be history only", "operation_id": operation_id},
        headers={"Idempotency-Key": operation_id},
    )
    assert rejected.status_code == 409
    assert rejected.json["error"]["code"] == "WIKI_ARCHIVED_HISTORY_ONLY"
