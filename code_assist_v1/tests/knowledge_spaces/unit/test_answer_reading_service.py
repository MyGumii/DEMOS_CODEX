"""Real isolated authority and lifecycle changes across evidence reading pages."""
from __future__ import annotations

import base64
from dataclasses import replace
from uuid import uuid4

import pytest

from code_assist_v1.knowledge_core.answer_reading import EvidenceReadingClosedError
from code_assist_v1.knowledge_core.db import ConnectionFactory, ManagedConnection, TransactionMode, transaction
from code_assist_v1.knowledge_core.repositories.hybrid_retrieval import HybridRetrievalRepository
from code_assist_v1.knowledge_core.scope import ScopeResolver, SessionPrincipal
from code_assist_v1.knowledge_core.services.agent_context import (
    AgentContextCorpusClosureStaleError, AgentContextScopeStaleError,
    AgentContextStateConflictError, AgentTurnContextService,
)
from code_assist_v1.knowledge_core.services.conversations import (
    ConversationService, TerminateConversationTurnCommand,
)
from code_assist_v1.knowledge_core.services.editing import (
    AcquireLeaseCommand, ImmutableItemEditService, LeaseHmacKeyRing, SaveItemCommand,
)
from code_assist_v1.knowledge_core.services.lifecycle import DeactivateItemCommand, ItemLifecycleService
from code_assist_v1.tests.knowledge_spaces.unit.test_canonical_retrieval import (
    _accepted_turn, _add_pin, _context_request, _flags,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_context_blob_bodies import _blob_fixture

pytestmark = [pytest.mark.unit, pytest.mark.scenario("CONV-012", "RANK-009", "RANK-012")]


@pytest.mark.parametrize("when", ["between_pages", "provider_return", "during_read"])
@pytest.mark.parametrize("change", ["epoch", "generation", "cancel", "revision", "deactivate"])
def test_original_reading_authority_is_rechecked_after_state_changes(tmp_path, change, when):
    factory, ids, item, revision, encoded = _blob_fixture(tmp_path)
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            connection.execute("UPDATE knowledge_space SET write_enabled=1,row_version=row_version+1 WHERE id=?",
                               (ids["part-space-a"],))
    finally:
        connection.close()
    scopes = ScopeResolver(replace(_flags(), part_writes=True))
    principal = SessionPrincipal("admin", int(ids["admin-epoch"]))
    _add_pin(factory, {**ids, "part-hit": item, "part-hit-revision": revision})
    turn = _accepted_turn(factory, ids)
    editor = ImmutableItemEditService(
        factory, scope_resolver=scopes,
        key_ring=LeaseHmacKeyRing(active_key_id="reader", keys={"reader": b"r" * 32}),
    )
    session_id = str(uuid4())
    token = base64.urlsafe_b64encode(b"r" * 32).decode().rstrip("=")
    lease = None
    if change == "revision":
        lease = editor.acquire(AcquireLeaseCommand(
            principal=principal, space_id=ids["part-space-a"], item_id=item,
            editor_session_id=session_id, operation_id=str(uuid4()), request_id=str(uuid4()), raw_token=token,
        ))

    def mutate():
        if change == "cancel":
            ConversationService(factory).terminate_turn(TerminateConversationTurnCommand(
                actor_user_id="admin", actor_scope_epoch=principal.captured_scope_epoch,
                operation_id=turn.operation_id, request_id=turn.request_id,
                conversation_id=ids["conversation-part-a"], terminal_state="cancelled",
            ))
        elif change == "revision":
            editor.save(SaveItemCommand(
                principal=principal, space_id=ids["part-space-a"], item_id=item,
                editor_session_id=session_id, lease_id=lease.lease_id,
                fencing_generation=lease.fencing_generation, expected_item_row_version=2,
                expected_revision_id=revision, operation_id=str(uuid4()), request_id=str(uuid4()),
                markdown="# scopealpha 수정\n\n이전 기준을 수정한 새 문서입니다.",
                reason="Reading cursor revision race", raw_token=token,
            ))
        elif change == "deactivate":
            ItemLifecycleService(factory, scopes).deactivate(DeactivateItemCommand(
                principal=principal, space_id=ids["part-space-a"], item_id=item,
                expected_item_row_version=2, operation_id=str(uuid4()), request_id=str(uuid4()),
                reason="Reading cursor lifecycle race",
            ))
        else:
            connection = factory.open()
            try:
                with transaction(connection, TransactionMode.IMMEDIATE):
                    if change == "epoch":
                        connection.execute("UPDATE app_user SET scope_epoch=scope_epoch+1,row_version=row_version+1 WHERE id='admin'")
                    else:
                        connection.execute("UPDATE knowledge_space SET generation=generation+1,row_version=row_version+1 WHERE id=?",
                                           (ids["part-space-a"],))
            finally:
                connection.close()

    armed = False

    def load(digest, size):
        if armed:
            mutate()
        return encoded

    service = AgentTurnContextService(factory, scope_resolver=scopes,
        retrieval=HybridRetrievalRepository(), content_loader=load)
    with service.reading_session(_context_request(ids, turn)) as session:
        if when != "during_read":
            page = session.reader.read_next(max_bytes=31)
            assert page.chunks[0].source.metadata.item_id == item
            assert session.reader._cached_body is not None
            session.reader.revalidate()  # The fence immediately before a provider call.
            mutate()
        else:
            armed = True
        with pytest.raises((AgentContextScopeStaleError, AgentContextStateConflictError)):
            if when == "provider_return":
                session.reader.revalidate()
            else:
                session.reader.read_next(max_bytes=31)
        assert session.reader.closed and session.reader._cached_body is None
        with pytest.raises(EvidenceReadingClosedError):
            session.reader.read_next()


def test_page_returns_without_database_handles_and_does_not_repeat_retrieval(tmp_path, monkeypatch):
    factory, ids, item, revision, encoded = _blob_fixture(tmp_path)
    _add_pin(factory, {**ids, "part-hit": item, "part-hit-revision": revision})
    turn = _accepted_turn(factory, ids)
    connections = set()
    retrieval_calls = []
    blob_calls = []
    original_open = ConnectionFactory.open
    original_close = ManagedConnection.close
    original_retrieve = HybridRetrievalRepository.retrieve_closure

    def opened(owner, *args, **kwargs):
        connection = original_open(owner, *args, **kwargs)
        if owner is factory:
            connections.add(id(connection))
        return connection

    def closed(connection):
        connections.discard(id(connection))
        return original_close(connection)

    def retrieve(owner, *args, **kwargs):
        retrieval_calls.append(True)
        return original_retrieve(owner, *args, **kwargs)

    def load(digest, size):
        # A lazy inline reader might have an idle connection, but never an
        # active snapshot; pin-first fixture starts with this CAS source.
        assert not connections
        blob_calls.append(digest)
        return encoded

    monkeypatch.setattr(ConnectionFactory, "open", opened)
    monkeypatch.setattr(ManagedConnection, "close", closed)
    monkeypatch.setattr(HybridRetrievalRepository, "retrieve_closure", retrieve)
    service = AgentTurnContextService(factory, scope_resolver=ScopeResolver(_flags()),
        retrieval=HybridRetrievalRepository(), content_loader=load)
    with service.reading_session(_context_request(ids, turn)) as session:
        initial_reads = len(blob_calls)
        assert not connections
        for _ in range(3):
            assert session.reader.read_next(max_bytes=512).chunks
            assert not connections
            session.reader.revalidate()
            assert not connections
        assert len(blob_calls) == initial_reads + 1
        assert len(retrieval_calls) == 1
    assert not connections and session.reader.closed


@pytest.mark.parametrize("level", ["personal", "part", "team", "responsibility"])
def test_accumulated_multi_author_bodies_exceed_one_page_without_scope_leak(isolated_knowledge_layout, level, monkeypatch):
    from code_assist_v1.tests.knowledge_spaces.unit import test_aggregate_graph_service as org_fixture
    from code_assist_v1.tests.knowledge_spaces.unit.test_canonical_retrieval import _insert_item
    from code_assist_v1.knowledge_core.repositories.organization import OrganizationRepository
    from code_assist_v1.knowledge_core.repositories.users import UserRepository
    from code_assist_v1.knowledge_core.repositories.personal_wikis import PersonalWikiProfileRepository
    from code_assist_v1.knowledge_core.repositories.conversations import ConversationRepository
    from code_assist_v1.knowledge_core.services.conversations import (
        CreateConversationCommand, AcceptConversationTurnCommand,
    )
    from code_assist_v1.knowledge_core.services.agent_context import AgentTurnContextRequest

    factory = org_fixture._factory(isolated_knowledge_layout)
    ids = org_fixture._seed(factory)
    personal = org_fixture._uuid("space:personal:partmember")
    foreign = org_fixture._uuid("space:personal:a2member")
    specs = {
        "personal": (personal, ("partmember",)),
        "foreign": (foreign, ("a2member",)),
        "part": (ids["part_space_a"], ("partmember", "partpeer")),
        "sibling": (ids["part_space_a2"], ("a2member",)),
        "otherteam": (ids["part_space_b"], ("bmember",)),
        "team": (ids["team_space_a"], ("teamlead",)),
        "responsibility": (ids["responsibility_space"], ("executive",)),
    }
    expected_labels = {
        "personal": {"personal"}, "part": {"part"},
        "team": {"part", "sibling", "team"},
        "responsibility": {"part", "sibling", "otherteam", "team", "responsibility"},
    }[level]
    expected = {}
    forbidden = set()
    peer_items = set()
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            UserRepository().import_legacy_many(connection, (org_fixture._user("partpeer"),))
            organizations = OrganizationRepository()
            state = organizations.get_user_organization_state(connection, "partpeer")
            organizations.open_membership(
                connection, membership_id=str(uuid4()), user_id="partpeer", part_id=ids["part_a"],
                assigned_by="admin", reason="Second author in the same Part", valid_from_ms=5_000,
            )
            organizations.compare_and_swap_user_part(
                connection, user_id="partpeer", expected_current_part_id=None,
                target_part_id=ids["part_a"], expected_scope_epoch=state.scope_epoch,
                expected_row_version=state.row_version, updated_at_ms=5_000,
            )
            profiles = PersonalWikiProfileRepository()
            if profiles.get_by_space_id(connection, personal) is None:
                profiles.insert_initial_profile(
                    connection, space_id=personal, owner_user_id="partmember", display_name="기본",
                    is_default=True, created_at_ms=5_000,
                )
            for label, (space, authors) in specs.items():
                for author in authors:
                    for index in range(10):
                        body = f"# scopealpha {label}/{author}/{index}\n\n"
                        body += "공정 조건과 실험 관찰 기록.\n" * 400
                        body += f"\n마지막 조건 {author}/{index}: 허용 오차 {index + 1}.75%, 교체 시 예외.\n"
                        item, revision = _insert_item(
                            connection, label=f"reader-many-{label}-{author}-{index}",
                            space_id=space, actor=author, title=f"scopealpha {label} {author} {index}",
                            body=body, published=True,
                        )
                        key = (item, revision)
                        if label in expected_labels:
                            expected[key] = body
                        else:
                            forbidden.add(key)
                        if author == "partpeer":
                            peer_items.add(key)
    finally:
        connection.close()
    assert sum(len(body.encode()) for body in expected.values()) > 98_304
    space, authors = specs[level]
    actor = authors[0]
    epoch = org_fixture._epoch(factory, actor)
    flags = replace(org_fixture._flags(), multi_wiki_schema=True, multi_wiki_reads=True,
                    conversation_wiki_binding=True)
    scopes = ScopeResolver(flags)
    repository = ConversationRepository(allow_higher_spaces=True, conversation_wiki_binding=True)
    conversations = ConversationService(factory, conversations=repository, scope_resolver=scopes)
    conversation = conversations.create(CreateConversationCommand(
        actor_user_id=actor, actor_scope_epoch=epoch, operation_id=str(uuid4()), request_id=str(uuid4()),
        space_id=space,
    )).conversation
    turn = conversations.accept_turn(AcceptConversationTurnCommand(
        actor_user_id=actor, actor_scope_epoch=epoch, operation_id=str(uuid4()), request_id=str(uuid4()),
        conversation_id=conversation.id, user_content={"text": "scopealpha"},
    ))
    service = AgentTurnContextService(factory, scope_resolver=scopes, conversations=repository,
                                      retrieval=HybridRetrievalRepository())
    with service.reading_session(AgentTurnContextRequest(
        actor_user_id=actor, actor_scope_epoch=epoch, conversation_id=conversation.id, turn_id=turn.turn_id,
    )) as session:
        assert session.context.context_budget.body_omitted_count > 0
        reconstructed = {}
        page_count = 0
        while True:
            page = session.reader.read_next()
            page_count += 1
            for chunk in page.chunks:
                key = (chunk.source.metadata.item_id, chunk.source.metadata.revision_id)
                assert key not in forbidden
                assert chunk.start_code_point == len(reconstructed.get(key, ""))
                reconstructed[key] = reconstructed.get(key, "") + chunk.content_md
            if page.selection_exhausted:
                break
        assert page_count > 1
        assert all(reconstructed[key] == body for key, body in expected.items())
        if level != "personal":
            assert peer_items <= reconstructed.keys()
        session.reader.revalidate()

        # Exercise the real common provider fallback and executor with this
        # original service-owned selection, not a hand-built callback fixture.
        from code_assist_v1 import internal_llm
        from code_assist_v1.knowledge_core.agent_execution import CanonicalAgentTurnExecutor
        import json
        import requests

        sends = []
        revoke = False
        monkeypatch.setattr(internal_llm, "_candidate_configs", lambda *a, **kw: [
            (model, {"model": model, "url": "https://example.invalid/chat/completions"})
            for model in ("fixture", "fallback")
        ])
        monkeypatch.setattr(internal_llm, "resolve_api_token", lambda _: (None, None))

        def send(http_session, request, **kwargs):
            assert isinstance(request, requests.PreparedRequest)
            envelope = json.loads(request.body)
            sends.append(envelope["model"])
            payload = json.loads(envelope["messages"][1]["content"])
            supplied = payload["retrieval_candidates"] + payload["wiki_body_evidence"]
            assert supplied
            assert all((row["item_id"], row["revision_id"]) not in forbidden for row in supplied)
            # This entry point still uses the initial budgeted body context.
            # Exhausting the cursor in this test must not change that claim.
            assert payload["context_budget"]["body_omitted_count"] > 0
            if revoke:
                connection = factory.open()
                try:
                    with transaction(connection, TransactionMode.IMMEDIATE):
                        connection.execute(
                            "UPDATE knowledge_space SET generation=generation+1,row_version=row_version+1 WHERE id=?",
                            (session.context.space_id,),
                        )
                finally:
                    connection.close()
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps({"choices": [{"message": {"content":
                "테스트 응답: 실제 LLM의 종합 답변 품질을 검증한 것은 아닙니다."}}]}).encode()
            response.headers["Content-Type"] = "application/json"
            return response

        monkeypatch.setattr(requests.Session, "send", send)
        executor = CanonicalAgentTurnExecutor(provider_order=("hynix_api",))
        result = executor.complete_reading(session)
        assert result.assistant_content["text"].startswith("테스트 응답")
        assert result.final_response["model_input_trace"]["api_request_transport"]["model"] == "fixture"
        revoke = True
        with pytest.raises(AgentContextCorpusClosureStaleError) as changed:
            executor.complete_reading(session)
        assert changed.value.code == "CORPUS_CLOSURE_STALE"
        assert sends == ["fixture", "fixture"]  # Never send to fallback after revocation.
        assert session.reader.closed
