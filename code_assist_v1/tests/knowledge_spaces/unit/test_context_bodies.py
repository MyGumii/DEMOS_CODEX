from __future__ import annotations

from dataclasses import replace
import hashlib
from uuid import uuid4

import pytest

from code_assist_v1.knowledge_core.db import TransactionMode, transaction
from code_assist_v1.knowledge_core.agent_execution import _turn_payload
from code_assist_v1.knowledge_core.repositories.context_bodies import (
    ContextBodyMetadata, ContextBodyRepository, ContextBodyUnavailableError,
)
from code_assist_v1.knowledge_core.repositories.hybrid_retrieval import HybridRetrievalRepository
from code_assist_v1.knowledge_core.scope import ScopeResolver, SessionPrincipal
from code_assist_v1.knowledge_core.services.lifecycle import DeactivateItemCommand, ItemLifecycleService
from code_assist_v1.knowledge_core.services.agent_context import AgentTurnContextService
from code_assist_v1.knowledge_core.services.agent_context import AgentMandatoryContext, AgentTurnContextRequest
from code_assist_v1.knowledge_core.repositories.conversations import ConversationRepository
from code_assist_v1.knowledge_core.repositories.personal_wikis import PersonalWikiProfileRepository
from code_assist_v1.knowledge_core.services.conversations import (
    AcceptConversationTurnCommand, CompleteConversationTurnCommand,
    ConversationService, CreateConversationCommand,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_canonical_retrieval import (
    _factory, _seed, _insert_item, _flags, _accepted_turn, _context_request, _add_pin,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_aggregate_graph_service import (
    _factory as _org_factory, _seed as _org_seed, _flags as _org_flags,
    _epoch as _org_epoch, _uuid as _org_uuid,
)

pytestmark = [pytest.mark.unit, pytest.mark.scenario("RANK-009", "RANK-012")]


@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize("blob_backed", [False, True])
def test_materialized_context_contains_whole_late_body_but_query_cache_keeps_preview(tmp_path, pinned, blob_backed):
    factory = _factory(tmp_path)
    ids = _seed(factory)
    fact = "복구 승인 조건은 0.75% 미만이며 센서 교체 시 예외로 처리한다."
    body = "# scopealpha\n\n" + "일반 운영 배경을 기록한다.\n" * 800 + fact
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            item, revision = _insert_item(
                connection, label="late-canonical-body", space_id=ids["part-space-a"],
                actor="admin", title="scopealpha 운영 기준", body=body, published=True,
                blob_backed=blob_backed,
            )
    finally:
        connection.close()
    if pinned:
        _add_pin(factory, {**ids, "part-hit": item, "part-hit-revision": revision})
    bundle = AgentTurnContextService(
        factory, scope_resolver=ScopeResolver(_flags()), retrieval=HybridRetrievalRepository(),
        content_loader=lambda digest, size: body.encode(),
    ).compose(_context_request(ids, _accepted_turn(factory, ids)))
    actual = next(row for row in bundle.ranked_contexts if row.item_id == item)
    cached = next(row for row in bundle.retrieval.ranked_documents if row.item_id == item)
    assert fact not in cached.excerpt_md
    assert actual.excerpt_md == body
    assert actual.revision_id == revision
    assert not actual.truncated
    assert actual.source_bytes == len(body.encode()) > 4_096
    payload = _turn_payload(bundle)
    model_row = next(row for row in payload["retrieval_candidates"] if row["item_id"] == item)
    if pinned:
        assert "excerpt_md" not in model_row
        assert model_row["mandatory_context_identity"] == item
    else:
        assert model_row["excerpt_md"] == body
    assert model_row["body_coverage"]["complete"] is True
    assert model_row["body_coverage"]["character_ranges"] == [[0, len(body)]]
    if pinned:
        assert fact not in bundle.pins[0].excerpt_md
        assert bundle.mandatory_contexts[0].content_md == body
        assert not bundle.mandatory_contexts[0].truncated
        assert actual.body_in_mandatory == item
        assert bundle.context_budget.excerpt_bytes == (
            sum(len(row.content_md.encode()) for row in bundle.mandatory_contexts)
            + sum(len(row.excerpt_md.encode()) for row in bundle.ranked_contexts
                  if row.body_in_mandatory is None)
        )


@pytest.mark.parametrize("failure", ["foreign", "revision", "source", "inactive", "unpublished"])
def test_lazy_metadata_rejects_forged_or_stale_selection(tmp_path, failure):
    factory = _factory(tmp_path)
    ids = _seed(factory)
    connection = factory.open()
    repository = ContextBodyRepository()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            item, revision = _insert_item(
                connection, label="lazy-body-denied", space_id=ids["part-space-a"],
                actor="admin", title="scopealpha", body="Private late fact",
                published=failure != "unpublished",
            )
            if failure == "inactive":
                connection.execute("UPDATE knowledge_space SET write_enabled=1, row_version=row_version+1 WHERE id=?",
                                   (ids["part-space-a"],))
        if failure == "inactive":
            ItemLifecycleService(factory, ScopeResolver(replace(_flags(), part_writes=True))).deactivate(
                DeactivateItemCommand(
                    principal=SessionPrincipal("admin", int(ids["admin-epoch"])),
                    space_id=ids["part-space-a"], item_id=item, expected_item_row_version=2,
                    operation_id=str(uuid4()), request_id=str(uuid4()), reason="Inactive body regression",
                )
            )
        with transaction(connection, TransactionMode.DEFERRED):
            with pytest.raises(ContextBodyUnavailableError):
                repository.metadata(
                    connection,
                    identities=((item, str(uuid4()) if failure == "revision" else revision,
                                 ids["personal-admin"] if failure == "source" else ids["part-space-a"]),),
                    source_space_ids=(ids["personal-admin"] if failure == "foreign" else ids["part-space-a"],),
                    overlays=(),
                )
    finally:
        connection.close()


def test_body_hash_is_checked_after_lazy_materialization(tmp_path):
    factory = _factory(tmp_path)
    ids = _seed(factory)
    repository = ContextBodyRepository()
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            rows = repository.metadata(
                connection, identities=((ids["part-hit"], ids["part-hit-revision"], ids["part-space-a"]),),
                source_space_ids=(ids["part-space-a"],), overlays=(),
            )
            record = next(iter(rows.values()))
            assert repository.read(connection, record)
            with pytest.raises(ContextBodyUnavailableError):
                repository.read(connection, replace(record, content_sha256="0" * 64))
    finally:
        connection.close()


@pytest.mark.parametrize("level", ["personal", "part", "team", "responsibility"])
@pytest.mark.parametrize("blob_backed", [False, True])
@pytest.mark.scenario("CONV-012")
def test_bound_four_level_context_reads_full_native_and_inherited_bodies_without_private_leak(
    isolated_knowledge_layout, level, blob_backed,
):
    factory = _org_factory(isolated_knowledge_layout)
    ids = _org_seed(factory)
    personal = _org_uuid("space:personal:partmember")
    foreign = _org_uuid("space:personal:a2member")
    specs = {
        "personal": (personal, "partmember"),
        "foreign": (foreign, "a2member"),
        "part": (ids["part_space_a"], "partmember"),
        "sibling": (ids["part_space_a2"], "a2member"),
        "otherteam": (ids["part_space_b"], "bmember"),
        "team": (ids["team_space_a"], "teamlead"),
        "responsibility": (ids["responsibility_space"], "executive"),
    }
    expected = {
        "personal": {"personal"}, "part": {"part"},
        "team": {"part", "sibling", "team"},
        "responsibility": {"part", "sibling", "otherteam", "team", "responsibility"},
    }[level]
    items = {}
    blob_contents = {}
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            profiles = PersonalWikiProfileRepository()
            if profiles.get_by_space_id(connection, personal) is None:
                profiles.insert_initial_profile(
                    connection, space_id=personal, owner_user_id="partmember",
                    display_name="기본", is_default=True, created_at_ms=4_000,
                )
            for label, (space, author) in specs.items():
                body = f"# scopealpha {label}\n\n" + "정비 기록과 변경 이력.\n" * 260
                body += f"\n마지막 근거 {label}: 허용 오차 0.75%, 센서 교체는 예외."
                item, revision = _insert_item(
                    connection, label=f"four-level-long-{label}", space_id=space,
                    actor=author, title=f"scopealpha {label} 기준", body=body, published=True,
                    blob_backed=blob_backed,
                )
                items[label] = (item, revision, body)
                blob_contents[hashlib.sha256(body.encode()).hexdigest()] = body.encode()
    finally:
        connection.close()
    space, actor = specs[level]
    epoch = _org_epoch(factory, actor)
    flags = replace(_org_flags(), multi_wiki_schema=True, multi_wiki_reads=True,
                    conversation_wiki_binding=True, personal_writes=True, part_writes=True)
    scopes = ScopeResolver(flags)
    from code_assist_v1.knowledge_core.services.source_artifacts import (
        SourceArtifactService, ItemSourceArtifactDownloadRequest,
    )
    from code_assist_v1.knowledge_core.services.management_reads import CanonicalManagementReadService
    from code_assist_v1.knowledge_core.services.wiki_files import WikiFilesService
    from code_assist_v1.knowledge_core.agent_execution import CanonicalAgentTurnExecutor
    from code_assist_v1.tests.knowledge_spaces.unit.test_workspace_features import Stager
    from code_assist_v1.knowledge_core.repositories.organization import OrganizationRepository

    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            organizations = OrganizationRepository()
            for unit, member in ((ids["part_a"], "partmember"), (ids["part_a2"], "a2member"),
                                 (ids["part_b"], "bmember")):
                organizations.open_part_leader(
                    connection, assignment_id=str(uuid4()), user_id=member,
                    part_id=unit, assigned_by="admin", reason="Citation fixture writer",
                    valid_from_ms=4_000,
                )
            for source_space, _ in specs.values():
                connection.execute("UPDATE knowledge_space SET write_enabled=1,row_version=row_version+1 WHERE id=?",
                                   (source_space,))
    finally:
        connection.close()
    source_service = SourceArtifactService(factory, scope_resolver=scopes, stager=Stager())
    files = WikiFilesService(source_service, CanonicalManagementReadService(factory, scope_resolver=scopes))
    originals = {}
    for label, (source_space, author) in specs.items():
        item, revision, body = items[label]
        content = f"원본 {label}\n{body}".encode()
        result = files.attach(
            SessionPrincipal(author, _org_epoch(factory, author)), source_space, item,
            filename="동일한 이름.txt", content=content, operation_id=str(uuid4()),
            expected_revision_id=revision,
        )
        originals[label] = (result["artifact_id"], content)
    conversations_repo = ConversationRepository(allow_higher_spaces=True, conversation_wiki_binding=True)
    conversations = ConversationService(factory, conversations=conversations_repo, scope_resolver=scopes)
    conversation = conversations.create(CreateConversationCommand(
        actor_user_id=actor, actor_scope_epoch=epoch, operation_id=str(uuid4()),
        request_id=str(uuid4()), space_id=space,
    )).conversation
    turn = conversations.accept_turn(AcceptConversationTurnCommand(
        actor_user_id=actor, actor_scope_epoch=epoch, operation_id=str(uuid4()),
        request_id=str(uuid4()), conversation_id=conversation.id, user_content={"text": "scopealpha"},
    ))
    context_service = AgentTurnContextService(
        factory, scope_resolver=scopes, conversations=conversations_repo,
        retrieval=HybridRetrievalRepository(),
        content_loader=lambda digest, size: blob_contents[digest],
    )
    context_request = AgentTurnContextRequest(
        actor_user_id=actor, actor_scope_epoch=epoch, conversation_id=conversation.id, turn_id=turn.turn_id,
    )
    with context_service.reading_session(context_request) as reading:
        bundle = reading.context
        pages = []
        while True:
            page = reading.reader.read_next(max_bytes=8_193)
            pages.append(page)
            if page.selection_exhausted:
                break
        reading.reader.revalidate()
        reconstructed = {}
        provenance = {}
        for page in pages:
            for chunk in page.chunks:
                key = (chunk.source.metadata.item_id, chunk.source.metadata.revision_id)
                reconstructed[key] = reconstructed.get(key, "") + chunk.content_md
                provenance[key] = chunk.source_artifacts
        for label, (item, revision, body) in items.items():
            if label in expected:
                assert reconstructed[item, revision] == body
                assert [row.artifact_id for row in provenance[item, revision]] == [originals[label][0]]
            else:
                assert (item, revision) not in reconstructed
    assert reading.reader.closed
    by_item = {row.item_id: row for row in bundle.ranked_contexts}
    assert bundle.selected_view.kind == level
    assert bundle.conversation_binding_sha256
    for label, (item, revision, body) in items.items():
        if label not in expected:
            assert item not in by_item
            continue
        assert by_item[item].revision_id == revision
        assert by_item[item].excerpt_md == body
        assert len(body.encode()) > 4_096
        assert not by_item[item].truncated
    assert bundle.context_budget.excerpt_bytes <= 98_304

    # Controlled runner isolates source delivery/rendering from model quality.
    def runner(_task, payload, **kwargs):
        from code_assist_v1.knowledge_core.answer_input import restore_source_documents
        assert "source_documents" not in payload
        supplied = restore_source_documents(payload)["source_documents"]
        expected_items = {items[label][0] for label in expected}
        assert {row["item_id"] for row in supplied} == set(by_item)
        assert expected_items <= set(by_item)
        assert all(row["source_space_id"] in {specs[label][0] for label in expected} for row in supplied)
        assert all("download_url" not in artifact for row in supplied for artifact in row["artifacts"])
        # Existing authorized graph fixtures can also be supplied. Cite only
        # the new facts, so supplied-but-unused rows cannot become cited rows.
        handles = [row["citation_handle"] for row in supplied if row["item_id"] in expected_items]
        answer = "## 작성자별 기준\n\n서로 다른 문서의 조건을 확인합니다.\n\n근거 문서: "
        answer += ", ".join(f"[[wiki:{handle}]]" for handle in handles)
        answer += "\n\n---\n\n## 전체 요약\n각 출처의 조건을 함께 검토합니다."
        assert kwargs["validate_output"](answer) == ""
        return answer, {"model_kind": "hynix_api", "model_id": "compatible-fixture"}

    completion = CanonicalAgentTurnExecutor(runner=runner, provider_order=("hynix_api",)).complete(bundle)
    text = completion.assistant_content["text"]
    cited = completion.final_response["citation_trace"]["sources"]
    assert {row["item_id"] for row in cited} == {items[label][0] for label in expected}
    complete_command = CompleteConversationTurnCommand(
        actor_user_id=actor, actor_scope_epoch=epoch,
        operation_id=turn.operation_id, request_id=turn.request_id,
        conversation_id=conversation.id, assistant_content=completion.assistant_content,
        final_response=completion.final_response,
    )
    completed = conversations.complete_turn(complete_command)
    assert conversations.complete_turn(complete_command) == completed
    assert completed.state == "completed"
    stored = conversations.list_messages(
        actor_user_id=actor, actor_scope_epoch=epoch, conversation_id=conversation.id,
    )
    assert [row.role for row in stored] == ["user", "assistant"]
    assert stored[-1].content["content"] == completion.assistant_content
    assert "[[wiki:" not in stored[-1].content_json
    replay, events = conversations.reconnect_turn(
        actor_user_id=actor, actor_scope_epoch=epoch, conversation_id=conversation.id,
        operation_id=turn.operation_id,
    )
    assert replay.final_response == completion.final_response
    from code_assist_v1.knowledge_core.canonical_routes import _sse_chunks
    wire = _sse_chunks(replay, events)
    resumed, suffix = conversations.reconnect_turn(
        actor_user_id=actor, actor_scope_epoch=epoch, conversation_id=conversation.id,
        operation_id=turn.operation_id, after_event_sequence_no=events[0].sequence_no,
    )
    assert _sse_chunks(resumed, suffix) == wire[1:]
    assert "[[wiki:" not in "".join(wire)
    assert any(event.event_type == "completed" for event in events)
    for label in expected:
        item, revision, body = items[label]
        artifact_id, original_content = originals[label]
        assert (f"/api/knowledge/v1/spaces/{space}/items/{item}"
                f"/source-artifacts/{artifact_id}/download") in text
        source = next(row for row in cited if row["item_id"] == item)
        assert source["source_space_id"] == specs[label][0]
        assert source["revision_id"] == revision
        assert source["artifacts"][0]["content_sha256"] == hashlib.sha256(original_content).hexdigest()
        downloaded = source_service.download(ItemSourceArtifactDownloadRequest(
            SessionPrincipal(actor, epoch), space, item, artifact_id,
        ))
        assert downloaded.content == original_content


def test_corpus_overflow_discloses_omissions_in_actual_payload(tmp_path):
    factory = _factory(tmp_path)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            for index in range(40):
                body = f"# scopealpha 검증 {index}\n\n" + ("운영" * 700) + f"\n꼬리 {index}"
                _insert_item(connection, label=f"overflow-{index}", space_id=ids["part-space-a"],
                             actor="admin", title=f"scopealpha {index}", body=body, published=True)
    finally:
        connection.close()
    context_service = AgentTurnContextService(
        factory, scope_resolver=ScopeResolver(_flags()), retrieval=HybridRetrievalRepository(),
    )
    with context_service.reading_session(_context_request(ids, _accepted_turn(factory, ids))) as reading:
        bundle = reading.context
        all_bodies = {}
        while True:
            page = reading.reader.read_next()
            for chunk in page.chunks:
                key = (chunk.source.metadata.item_id, chunk.source.metadata.revision_id)
                all_bodies[key] = all_bodies.get(key, "") + chunk.content_md
            if page.selection_exhausted:
                break
        selected = (*bundle.retrieval.ranked_documents, *bundle.retrieval.supplemental_documents)
        assert len(all_bodies) == len(selected) > 40
        assert page.completed_source_count == len(all_bodies)
        assert sum(len(body.encode()) for body in all_bodies.values()) == page.selected_source_bytes
        connection = factory.open(read_only=True)
        try:
            for (item, revision), body in all_bodies.items():
                assert body == connection.execute(
                    "SELECT content_md FROM knowledge_revision_body WHERE item_id=? AND revision_id=?",
                    (item, revision),
                ).fetchone()[0]
        finally:
            connection.close()
    payload = _turn_payload(bundle)
    budget = bundle.context_budget
    assert budget.body_source_bytes > 98_304
    assert budget.body_omitted_count > 0
    assert budget.body_eligible_count == len(bundle.ranked_contexts) + budget.body_omitted_count
    assert budget.excerpt_bytes == sum(len(row.excerpt_md.encode()) for row in bundle.ranked_contexts)
    assert budget.excerpt_bytes <= 98_304
    assert payload["wiki_evidence_status"]["coverage_truncated"] is True
    assert payload["context_budget"]["body_omitted_count"] == budget.body_omitted_count


@pytest.mark.parametrize("remaining", [1, 2])
def test_empty_mandatory_unicode_fragment_is_counted_as_omitted(tmp_path, remaining):
    factory = _factory(tmp_path)
    ids = _seed(factory)
    service = AgentTurnContextService(
        factory, scope_resolver=ScopeResolver(_flags()), retrieval=HybridRetrievalRepository(),
    )
    retrieval = service.compose(_context_request(ids, _accepted_turn(factory, ids))).retrieval
    retrieval = replace(retrieval, ranked_documents=(), supplemental_documents=())
    bodies = ("x" * (98_304 - remaining), "한글", "a")
    candidates = []
    metadata = {}
    text_by_item = {}
    for body in bodies:
        item, revision = str(uuid4()), str(uuid4())
        digest = hashlib.sha256(body.encode()).hexdigest()
        candidates.append(AgentMandatoryContext(
            kind="stable_reference", identity=str(uuid4()), title="Mandatory source",
            content_md="", content_sha256=digest,
            target_item_id=item, target_revision_id=revision,
        ))
        metadata[item, revision] = ContextBodyMetadata(
            item, revision, ids["part-space-a"], len(body.encode()), digest,
        )
        text_by_item[item] = body
    mandatory, ranked, disclosure = service._bounded_hybrid_context(
        retrieval=retrieval, candidates=tuple(candidates), body_metadata=metadata,
        body_loader=lambda record: text_by_item[record.item_id],
    )
    assert len(mandatory) == 1
    assert ranked == ()
    assert disclosure.mandatory_selected_count == 1
    assert disclosure.mandatory_omitted_count == 2
    assert disclosure.byte_truncated_count == 0
    assert disclosure.excerpt_bytes == 98_304 - remaining


@pytest.mark.parametrize("expansion_stage", ["service", "repository"])
def test_expanded_search_terms_reach_overflow_passage_selection_without_rewriting_user_query(tmp_path, expansion_stage):
    factory = _factory(tmp_path)
    ids = _seed(factory)
    query = "비상 상황에서 복구는 어떤 순서로 진행해야 하나요?"
    fact = "Recovery protocol: isolate the failed controller, restore the verified snapshot, then validate telemetry."
    body = "# Continuity handbook\n\n" + "일반적인 운영 배경과 근무 일정.\n" * 4_000
    body += "\n" + fact + "\n\n" + "평상시 점검 준비와 기록 관리.\n" * 4_000
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            item, _ = _insert_item(connection, label="expanded-overflow-evidence",
                                   space_id=ids["part-space-a"], actor="admin",
                                   title="Continuity handbook", body=body, published=True)
    finally:
        connection.close()

    class Expander:
        def expand(self, received):
            assert received == query
            return "recovery protocol controller snapshot telemetry"

    turn = ConversationService(factory).accept_turn(AcceptConversationTurnCommand(
        actor_user_id="admin", actor_scope_epoch=int(ids["admin-epoch"]),
        operation_id=str(uuid4()), request_id=str(uuid4()),
        conversation_id=ids["conversation-part-a"], user_content={"text": query},
    ))
    bundle = AgentTurnContextService(
        factory, scope_resolver=ScopeResolver(_flags()), retrieval=HybridRetrievalRepository(
            query_transformer=Expander().expand if expansion_stage == "repository" else None,
        ),
        query_expander=Expander() if expansion_stage == "service" else None,
    ).compose(_context_request(ids, turn))
    actual = next(row for row in bundle.ranked_contexts if row.item_id == item)
    assert bundle.normalized_query == query
    assert bundle.retrieval.effective_search_query == "recovery protocol controller snapshot telemetry"
    assert actual.truncated and actual.source_bytes > 98_304
    assert fact in actual.excerpt_md
    assert fact in _turn_payload(bundle)["retrieval_candidates"][0]["excerpt_md"]
    assert bundle.context_budget.excerpt_bytes <= 98_304
