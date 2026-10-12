from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest

from code_assist_v1.knowledge_core.db import (
    TransactionMode,
    database_runtime_metrics,
    transaction,
)
from code_assist_v1.knowledge_core.agent_runtime import compose_canonical_agent_runtime
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.agent_execution import (
    CanonicalAgentTurnExecutor,
    _turn_payload,
)
from code_assist_v1.knowledge_core.repositories.hybrid_retrieval import (
    DIRECT_CAP,
    FINAL_CAP,
    HybridRetrievalRepository,
    PRE_RANK_CAP,
    WIKI_MANIFEST_CAP,
)
from code_assist_v1.knowledge_core.repositories.retrieval import (
    RankedDocumentIdentity,
    RetrievalQueryError,
)
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.services.retrieval import (
    CanonicalRetrievalService,
    SpaceRetrievalRequest,
)
from code_assist_v1.knowledge_core.services.agent_context import AgentTurnContextService
from code_assist_v1.knowledge_core.services.conversations import (
    AcceptConversationTurnCommand,
    CompleteConversationTurnCommand,
    ConversationService,
)
from code_assist_v1.knowledge_core.services.personal_wiki_search import (
    PersonalWikiManagementSearchRequest,
    PersonalWikiManagementSearchService,
)
from code_assist_v1.knowledge_core.services.query_expansion import (
    MultilingualQueryExpansionAdapter,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_canonical_retrieval import (
    _factory,
    _flags,
    _insert_item,
    _seed,
    _accepted_turn,
    _add_live_binding,
    _add_pin,
    _context_request,
    _TurnBindingResolver,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_aggregate_graph_service import (
    _command as _aggregate_command,
    _decorate_spaces as _decorate_aggregate_spaces,
    _epoch as _aggregate_epoch,
    _factory as _aggregate_factory,
    _flags as _aggregate_flags,
    _seed as _aggregate_seed,
)
from code_assist_v1.knowledge_core.services.aggregate_graph import AggregateGraphService
from code_assist_v1.test_safety import create_isolated_test_layout


pytestmark = pytest.mark.unit


def _service(factory):
    return CanonicalRetrievalService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
        retrieval=HybridRetrievalRepository(),
    )


def _request(ids, *, query="scopealpha"):
    return SpaceRetrievalRequest(
        actor_user_id="admin",
        actor_scope_epoch=int(ids["admin-epoch"]),
        space_id=ids["part-space-a"],
        query=query,
        top_k=20,
    )


class _StaticQueryExpander:
    def __init__(self, expanded: str, *, intent: str = "topic") -> None:
        self.expanded = expanded
        self.intent = intent
        self.calls: list[str] = []

    def expand(self, query: str) -> str:
        self.calls.append(query)
        return f"{query} {self.expanded}"

    def intent_for(self, query: str) -> str:
        return self.intent


@pytest.mark.scenario("RANK-004", "RANK-014", "RANK-017")
def test_natural_korean_query_expands_before_one_bounded_fts_retrieval(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            target_item, _ = _insert_item(
                connection,
                label="natural-language-cross-lingual-target",
                space_id=ids["part-space-a"],
                actor="admin",
                title="Continuity operations handbook",
                body=(
                    "# Continuity operations\n\n"
                    "The emergency recovery protocol isolates the failed controller, "
                    "restores the latest verified snapshot, and validates conveyor telemetry.\n"
                ),
                published=True,
            )
            unrelated_item, _ = _insert_item(
                connection,
                label="natural-language-cross-lingual-negative",
                space_id=ids["part-space-a"],
                actor="admin",
                title="Cafeteria seasonal menu",
                body="# Cafeteria\n\nLunch fruit and allergy labeling schedule.\n",
                published=True,
            )
            connection.execute(
                """UPDATE knowledge_space SET generation=generation+1,
                          updated_at=updated_at+1,row_version=row_version+1
                    WHERE id=?""",
                (ids["part-space-a"],),
            )
    finally:
        connection.close()
    expander = _StaticQueryExpander("recovery protocol")
    result = CanonicalRetrievalService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
        retrieval=HybridRetrievalRepository(
            query_transformer=lambda _query: "temporal-fallback-must-not-replace-expansion"
        ),
        query_expander=expander,
    ).retrieve_for_space(
        _request(ids, query="비상 상황에서 복구는 어떤 순서로 진행해야 해?")
    )
    assert expander.calls == ["비상 상황에서 복구는 어떤 순서로 진행해야 해?"]
    assert result.normalized_query == "비상 상황에서 복구는 어떤 순서로 진행해야 해?"
    assert target_item in {row.item_id for row in result.ranked_documents}
    assert unrelated_item not in {row.item_id for row in result.ranked_documents}
    assert result.hybrid_explain is not None
    assert result.hybrid_explain.direct_candidate_count == 1


@pytest.mark.scenario("RANK-004", "RANK-008", "RANK-014")
def test_generic_wiki_overview_supplies_representative_real_bodies(
    tmp_path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    expected: set[str] = set()
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            for label, title, body in (
                (
                    "overview-continuity",
                    "Emergency continuity operations",
                    "# Continuity\n\nController recovery sequence and snapshot validation.\n",
                ),
                (
                    "overview-conveyor",
                    "Conveyor telemetry diagnostics",
                    "# Diagnostics\n\nMotor current and vibration anomaly triage.\n",
                ),
                (
                    "overview-cafeteria",
                    "Cafeteria allergy schedule",
                    "# Cafeteria\n\nSeasonal menu allergen labeling.\n",
                ),
            ):
                item_id, _ = _insert_item(
                    connection,
                    label=label,
                    space_id=ids["part-space-a"],
                    actor="admin",
                    title=title,
                    body=body,
                    published=True,
                )
                expected.add(item_id)
            connection.execute(
                """UPDATE knowledge_space SET generation=generation+1,
                          updated_at=updated_at+1,row_version=row_version+1
                    WHERE id=?""",
                (ids["part-space-a"],),
            )
    finally:
        connection.close()
    result = CanonicalRetrievalService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
        retrieval=HybridRetrievalRepository(),
        query_expander=_StaticQueryExpander("corpus-plan", intent="overview"),
    ).retrieve_for_space(
        _request(ids, query="이 위키에는 어떤 정보가 들어 있어?")
    )
    returned = {row.item_id for row in result.supplemental_documents}
    assert expected <= returned
    assert result.ranked_documents == ()
    assert result.query_intent == "overview"
    assert result.hybrid_explain is not None
    assert result.hybrid_explain.direct_candidate_count == 0
    assert all(row.excerpt_md.strip() for row in result.supplemental_documents)
    assert all(
        row.selection_reason == "representative"
        for row in result.supplemental_documents
    )
    assert result.wiki_manifest is not None
    assert len(result.supplemental_documents) == (
        result.wiki_manifest.authorized_published_count
    )
    assert ids["foreign-hit"] not in returned
    assert ids["personal-hit"] not in returned


@pytest.mark.scenario("RANK-004", "RANK-008", "RANK-014")
def test_renamed_selected_part_and_generic_nickname_supply_bodies_without_examples(
    tmp_path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            connection.execute(
                """UPDATE org_unit
                      SET name='달빛물류파트',updated_at=updated_at+1,
                          row_version=row_version+1
                    WHERE id=?""",
                (ids["part-a"],),
            )
            expected_item, _ = _insert_item(
                connection,
                label="renamed-part-overview-body",
                space_id=ids["part-space-a"],
                actor="admin",
                title="Cryogenic conveyor recovery",
                body="# Recovery\n\nLUNAR-RECOVERY-4821 isolates the failed carrier.\n",
                published=True,
            )
            connection.execute(
                """UPDATE knowledge_space SET generation=generation+1,
                          updated_at=updated_at+1,row_version=row_version+1
                    WHERE id=?""",
                (ids["part-space-a"],),
            )
    finally:
        connection.close()

    repository = HybridRetrievalRepository()
    overview = CanonicalRetrievalService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
        retrieval=repository,
    ).retrieve_for_space(
        _request(ids, query="물류파트에는 어떤 지식이 있어?")
    )
    returned = {
        row.item_id: row.excerpt_md for row in overview.supplemental_documents
    }
    assert expected_item in returned
    assert "LUNAR-RECOVERY-4821" in returned[expected_item]
    assert ids["foreign-hit"] not in returned
    assert ids["personal-hit"] not in returned

    topic = CanonicalRetrievalService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
        retrieval=HybridRetrievalRepository(),
    ).retrieve_for_space(
        _request(ids, query="물류파트의 scopealpha 내용을 알려줘")
    )
    assert ids["part-hit"] in {row.item_id for row in topic.ranked_documents}
    assert expected_item not in {row.item_id for row in topic.ranked_documents}


@pytest.mark.scenario("RANK-016", "RANK-017")
def test_multilingual_query_expansion_is_cached_and_fails_to_original_query() -> None:
    calls: list[str] = []
    prompts: list[str] = []

    def runner(task, payload, **kwargs):
        calls.append(task)
        prompts.append(kwargs["system_prompt"])
        assert payload["query"] == "복구 절차를 설명해줘"
        return (
            '{"schema_version":2,"intent":"topic",'
            '"search_terms":["recovery","restore protocol"]}',
            {"model_kind": "codex", "model_id": "codex-local"},
        )

    adapter = MultilingualQueryExpansionAdapter(
        runner=runner,
        provider_order=("codex",),
        model_ids=("codex-local",),
    )
    first = adapter.expand("복구 절차를 설명해줘")
    second = adapter.expand("복구 절차를 설명해줘")
    assert first == second
    assert "recovery" in first and "restore" in first and "protocol" in first
    assert adapter.intent_for("복구 절차를 설명해줘") == "topic"
    assert calls == ["canonical_multilingual_query_expansion"]
    assert "복구" not in prompts[0]
    assert "달빛물류파트" not in prompts[0]

    malformed = MultilingualQueryExpansionAdapter(
        runner=lambda *args, **kwargs: ("not-json", {}),
    )
    assert malformed.expand("복구 절차를 설명해줘") == "복구 절차를 설명해줘"
    assert malformed.intent_for("복구 절차를 설명해줘") == "unknown"


@pytest.mark.scenario("RANK-014", "RANK-017")
def test_agent_query_plan_uses_model_intent_without_selected_view_phrases(tmp_path) -> None:
    factory = _factory(tmp_path)
    expander = _StaticQueryExpander("bounded-plan", intent="overview")
    service = AgentTurnContextService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
        query_expander=expander,
    )
    query = "달빛물류파트에는 어떤 지식이 있어?"
    search_query, intent = service._query_plan(query)
    assert search_query == f"{query} bounded-plan"
    assert intent == "overview"
    assert expander.calls == [query]


@pytest.mark.scenario("RANK-004", "RANK-008", "RANK-017")
def test_followup_reuses_only_reauthorized_prior_turn_body_identities(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    conversation_service = ConversationService(factory)
    first_operation = str(uuid4())
    first_request = str(uuid4())
    first_turn = conversation_service.accept_turn(
        AcceptConversationTurnCommand(
            actor_user_id="admin",
            actor_scope_epoch=int(ids["admin-epoch"]),
            operation_id=first_operation,
            request_id=first_request,
            conversation_id=ids["conversation-part-a"],
            user_content={"text": "scopealpha"},
        )
    )
    first_bundle = AgentTurnContextService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
        retrieval=HybridRetrievalRepository(),
        query_expander=_StaticQueryExpander("scopealpha", intent="topic"),
    ).compose(_context_request(ids, first_turn))
    first_identities = {
        (row.item_id, row.revision_id, row.source_space_id)
        for row in first_bundle.ranked_contexts
    }
    assert first_identities
    completion = CanonicalAgentTurnExecutor(
        runner=lambda *args, **kwargs: (
            "The selected Wiki evidence was used.",
            {"model_id": "test-model", "model_kind": "codex"},
        )
    ).complete(first_bundle)
    assert "retrieval_trace" in completion.final_response
    conversation_service.complete_turn(
        CompleteConversationTurnCommand(
            actor_user_id="admin",
            actor_scope_epoch=int(ids["admin-epoch"]),
            operation_id=first_operation,
            request_id=first_request,
            conversation_id=ids["conversation-part-a"],
            assistant_content=completion.assistant_content,
            final_response=completion.final_response,
        )
    )

    second_operation = str(uuid4())
    second_turn = conversation_service.accept_turn(
        AcceptConversationTurnCommand(
            actor_user_id="admin",
            actor_scope_epoch=int(ids["admin-epoch"]),
            operation_id=second_operation,
            request_id=str(uuid4()),
            conversation_id=ids["conversation-part-a"],
            user_content={"text": "그 근거를 이어서 설명해줘"},
        )
    )
    second_bundle = AgentTurnContextService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
        retrieval=HybridRetrievalRepository(),
        query_expander=_StaticQueryExpander("followup", intent="continuation"),
    ).compose(_context_request(ids, second_turn))
    continued = {
        (row.item_id, row.revision_id, row.source_space_id)
        for row in second_bundle.retrieval.supplemental_documents
        if row.selection_reason == "continuation"
    }
    assert second_bundle.retrieval.query_intent == "continuation"
    assert second_bundle.retrieval.ranked_documents == ()
    assert continued == first_identities
    assert all(
        row.item_id not in {ids["foreign-hit"], ids["personal-hit"]}
        for row in second_bundle.retrieval.supplemental_documents
    )
    payload = _turn_payload(second_bundle)
    assert payload["retrieval_candidates"] == []
    assert {
        (row["item_id"], row["revision_id"], row["source_space_id"])
        for row in payload["wiki_body_evidence"]
        if row["selection_reason"] == "continuation"
    } == first_identities
    assert payload["wiki_evidence_status"]["continuation_body_excerpt_count"] == len(
        first_identities
    )


@pytest.mark.scenario("RANK-003", "RANK-008")
def test_continuation_identity_is_only_a_hint_and_cannot_restore_unpublished_body(
    tmp_path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            unpublished_item, unpublished_revision = _insert_item(
                connection,
                label="unpublished-continuation-hint",
                space_id=ids["part-space-a"],
                actor="admin",
                title="Unpublished continuation source",
                body="# Unpublished source\n\nThis body must not return.\n",
                published=False,
                stale_search_row=True,
            )
            connection.execute(
                """UPDATE knowledge_space
                      SET generation=generation+1,updated_at=updated_at+1,
                          row_version=row_version+1
                    WHERE id=?""",
                (ids["part-space-a"],),
            )
        with transaction(connection, TransactionMode.DEFERRED):
            result = HybridRetrievalRepository().retrieve_closure(
                connection,
                selected_space_id=ids["part-space-a"],
                source_space_ids=(ids["part-space-a"],),
                overlays=(),
                corpus_closure_sha256="e" * 64,
                query="continue",
                retrieval_intent="continuation",
                continuation_documents=(
                    RankedDocumentIdentity(
                        item_id=unpublished_item,
                        revision_id=unpublished_revision,
                        source_space_id=ids["part-space-a"],
                    ),
                ),
            )
    finally:
        connection.close()
    assert unpublished_item not in {
        row.item_id for row in result.supplemental_documents
    }
    assert all(
        row.selection_reason == "representative"
        for row in result.supplemental_documents
    )


@pytest.mark.scenario("RANK-001", "RANK-005", "RANK-011", "RANK-012")
def test_sbe_5_1_exact_scope_one_hop_final_documents_and_cache(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    service = _service(factory)

    first = service.retrieve_for_space(_request(ids))
    assert first.hybrid_explain is not None
    assert first.hybrid_explain.pipeline_version == "canonical-hybrid-rerank-v4-shared-preview-budget"
    assert first.hybrid_explain.cache_hit is False
    assert first.hybrid_explain.direct_candidate_count == 1
    assert first.hybrid_explain.one_hop_candidate_count == 1
    assert [row.item_id for row in first.ranked_documents] == [
        ids["part-hit"],
        ids["part-neighbor"],
    ]
    assert first.ranked_documents[1].provenance == ("one_hop",)
    assert first.ranked_documents[1].relation_semantics == "supporting_context"
    forbidden = {ids["foreign-hit"], ids["personal-hit"], ids["part-provisional"]}
    assert forbidden.isdisjoint(row.item_id for row in first.ranked_documents)

    replay = service.retrieve_for_space(_request(ids))
    assert replay.hybrid_explain is not None
    assert replay.hybrid_explain.cache_hit is True
    assert replay.cache_identity_sha256 == first.cache_identity_sha256
    assert replay.ranked_documents == first.ranked_documents

    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            connection.execute(
                """UPDATE knowledge_space
                      SET generation=generation+1,updated_at=updated_at+1,
                          row_version=row_version+1
                    WHERE id=?""",
                (ids["part-space-a"],),
            )
    finally:
        connection.close()
    invalidated = service.retrieve_for_space(_request(ids))
    assert invalidated.hybrid_explain is not None
    assert invalidated.hybrid_explain.cache_hit is False
    assert invalidated.cache_identity_sha256 != first.cache_identity_sha256
    replay_after_content = service.retrieve_for_space(_request(ids))
    assert replay_after_content.hybrid_explain is not None
    assert replay_after_content.hybrid_explain.cache_hit is True
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            connection.execute(
                """UPDATE knowledge_space
                      SET temporal_generation=temporal_generation+1,
                          updated_at=updated_at+1,row_version=row_version+1
                    WHERE id=?""",
                (ids["part-space-a"],),
            )
    finally:
        connection.close()
    temporal_invalidated = service.retrieve_for_space(_request(ids))
    assert temporal_invalidated.hybrid_explain is not None
    assert temporal_invalidated.hybrid_explain.cache_hit is False
    assert temporal_invalidated.cache_identity_sha256 != invalidated.cache_identity_sha256


@pytest.mark.scenario("RANK-002")
def test_sbe_5_1_personal_wiki_exact_scope_has_no_org_dependency(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    result = _service(factory).retrieve_for_space(
        SpaceRetrievalRequest(
            actor_user_id="admin",
            actor_scope_epoch=int(ids["admin-epoch"]),
            space_id=ids["personal-admin"],
            query="scopealpha",
            top_k=20,
        )
    )
    assert result.hybrid_explain is not None
    assert [row.item_id for row in result.ranked_documents] == [ids["personal-hit"]]
    assert result.ranked_documents[0].source_space_id == ids["personal-admin"]
    assert result.ranked_documents[0].inherited is False


@pytest.mark.scenario("RANK-001", "RANK-003", "RANK-008")
def test_query_independent_manifest_is_bounded_authorized_and_not_rank_padding(
    tmp_path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    first = _service(factory).retrieve_for_space(
        _request(ids, query="oracular cartography")
    )
    second = _service(factory).retrieve_for_space(
        _request(ids, query="사과나무 천문 관측")
    )
    assert first.ranked_documents == ()
    assert second.ranked_documents == ()
    assert first.wiki_manifest is not None
    assert second.wiki_manifest is not None
    assert first.wiki_manifest == second.wiki_manifest
    returned = {row.item_id for row in first.wiki_manifest.entries}
    assert {ids["part-hit"], ids["part-neighbor"]} <= returned
    assert ids["foreign-hit"] not in returned
    assert ids["personal-hit"] not in returned
    assert ids["part-provisional"] not in returned
    assert first.wiki_manifest.selected_view_space_id == ids["part-space-a"]
    assert len(first.wiki_manifest.entries) <= WIKI_MANIFEST_CAP


@pytest.mark.scenario("RANK-003")
def test_sbe_5_1_responsibility_retrieval_expands_inherited_team_home_edge(
    isolated_knowledge_layout,
) -> None:
    factory = _aggregate_factory(isolated_knowledge_layout)
    ids = _aggregate_seed(factory)
    _decorate_aggregate_spaces(ids)
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            source_item, source_revision = _insert_item(
                connection,
                label="responsibility-search-source",
                space_id=ids["part_space_a"],
                actor="partmember",
                title="Scopeomega responsibility source",
                body="# scopeomega responsibility source\n",
                published=True,
            )
            target_item, target_revision = _insert_item(
                connection,
                label="responsibility-search-target",
                space_id=ids["part_space_a2"],
                actor="a2member",
                title="Inherited team graph target",
                body="# inherited team graph target\n",
                published=True,
            )
            for space_id in (ids["part_space_a"], ids["part_space_a2"]):
                connection.execute(
                    """UPDATE knowledge_space
                          SET generation=generation+1,updated_at=updated_at+1,
                              row_version=row_version+1 WHERE id=?""",
                    (space_id,),
                )
    finally:
        connection.close()
    ids.update(
        {
            "item_search-source": source_item,
            "revision_search-source": source_revision,
            "space_for_search-source": ids["part_space_a"],
            "item_search-target": target_item,
            "revision_search-target": target_revision,
            "space_for_search-target": ids["part_space_a2"],
        }
    )
    edge = AggregateGraphService(factory, ScopeResolver(_aggregate_flags())).create(
        _aggregate_command(
            factory,
            ids,
            actor="teamlead",
            source="search-source",
            target="search-target",
            label="responsibility-inherits-team-search-edge",
        )
    )
    assert edge.home_space_id == ids["team_space_a"]
    result = CanonicalRetrievalService(
        factory,
        scope_resolver=ScopeResolver(_aggregate_flags()),
        retrieval=HybridRetrievalRepository(),
    ).retrieve_for_space(
        SpaceRetrievalRequest(
            actor_user_id="executive",
            actor_scope_epoch=_aggregate_epoch(factory, "executive"),
            space_id=ids["responsibility_space"],
            query="scopeomega",
            top_k=20,
        )
    )
    inherited_target = next(
        row for row in result.ranked_documents if row.item_id == target_item
    )
    assert inherited_target.provenance == ("one_hop",)
    assert inherited_target.source_space_id == ids["part_space_a2"]
    assert inherited_target.origin == "inherited"
    assert inherited_target.inherited is True


@pytest.mark.scenario("RANK-004", "RANK-007", "RANK-008")
def test_all_selected_wiki_paths_remove_the_fixed_ten_document_cap(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            for index in range(50):
                _insert_item(
                    connection,
                    label=f"hybrid-cap-{index:02d}",
                    space_id=ids["part-space-a"],
                    actor="admin",
                    title=f"Scopealpha bounded candidate {index:02d}",
                    body=f"# scopealpha bounded candidate {index:02d}\n",
                    published=True,
                )
            connection.execute(
                """UPDATE knowledge_space
                      SET generation=generation+1,updated_at=updated_at+1,
                          row_version=row_version+1
                    WHERE id=?""",
                (ids["part-space-a"],),
            )
    finally:
        connection.close()

    result = _service(factory).retrieve_for_space(_request(ids))
    explain = result.hybrid_explain
    assert explain is not None
    assert explain.direct_candidate_count > DIRECT_CAP
    assert explain.pre_rank_candidate_count > PRE_RANK_CAP
    assert explain.direct_cap is None
    assert explain.pre_rank_cap is None
    assert explain.final_cap is None
    assert len(result.ranked_documents) > FINAL_CAP
    assert [row.rank for row in result.ranked_documents] == list(
        range(1, len(result.ranked_documents) + 1)
    )
    assert len({row.item_id for row in result.ranked_documents}) == len(
        result.ranked_documents
    )
    assert result.wiki_manifest is not None
    assert result.wiki_manifest.authorized_published_count >= 52
    assert len(result.wiki_manifest.entries) == WIKI_MANIFEST_CAP
    assert result.wiki_manifest.truncated is True


@pytest.mark.scenario("RANK-008", "RANK-009")
def test_agent_receives_more_than_ten_part_markdown_bodies_when_budget_allows(
    tmp_path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            for index in range(18):
                _insert_item(
                    connection,
                    label=f"agent-full-corpus-{index:02d}",
                    space_id=ids["part-space-a"],
                    actor="admin",
                    title=f"Scopealpha full corpus {index:02d}",
                    body=f"# scopealpha full corpus {index:02d}\n\nbody-{index:02d}\n",
                    published=True,
                )
            connection.execute(
                """UPDATE knowledge_space SET generation=generation+1,
                          updated_at=updated_at+1,row_version=row_version+1
                    WHERE id=?""",
                (ids["part-space-a"],),
            )
    finally:
        connection.close()
    turn = _accepted_turn(factory, ids)
    bundle = AgentTurnContextService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
        retrieval=HybridRetrievalRepository(),
    ).compose(_context_request(ids, turn))
    assert bundle.retrieval.hybrid_explain is not None
    assert bundle.retrieval.hybrid_explain.final_cap is None
    assert len(bundle.ranked_contexts) > FINAL_CAP
    assert bundle.context_budget is not None
    assert bundle.context_budget.excerpt_budget_bytes == 98_304
    assert bundle.context_budget.excerpt_bytes <= 98_304


@pytest.mark.scenario("RANK-003", "RANK-008")
def test_personal_team_and_responsibility_backends_share_full_corpus_policy(
    tmp_path,
) -> None:
    personal_factory = _factory(tmp_path / "personal")
    personal_ids = _seed(personal_factory)
    connection = personal_factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            for index in range(12):
                _insert_item(
                    connection,
                    label=f"personal-full-{index:02d}",
                    space_id=personal_ids["personal-admin"],
                    actor="admin",
                    title=f"Personalfulltopic document {index:02d}",
                    body=f"# personalfulltopic {index:02d}\n",
                    published=True,
                )
            connection.execute(
                """UPDATE knowledge_space SET generation=generation+1,
                          updated_at=updated_at+1,row_version=row_version+1
                    WHERE id=?""",
                (personal_ids["personal-admin"],),
            )
    finally:
        connection.close()
    personal = _service(personal_factory).retrieve_for_space(
        SpaceRetrievalRequest(
            actor_user_id="admin",
            actor_scope_epoch=int(personal_ids["admin-epoch"]),
            space_id=personal_ids["personal-admin"],
            query="personalfulltopic",
            top_k=100,
        )
    )
    assert personal.hybrid_explain is not None
    assert personal.hybrid_explain.final_cap is None
    assert len(personal.ranked_documents) == 12
    with pytest.raises(RetrievalQueryError, match="top_k"):
        _service(personal_factory).retrieve_for_space(
            SpaceRetrievalRequest(
                actor_user_id="admin",
                actor_scope_epoch=int(personal_ids["admin-epoch"]),
                space_id=personal_ids["personal-admin"],
                query="personalfulltopic",
                top_k=101,
            )
        )

    higher_factory = _aggregate_factory(
        create_isolated_test_layout(tmp_path / "higher", user_id="higher-fixture")
    )
    higher_ids = _aggregate_seed(higher_factory)
    connection = higher_factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            for kind, space_id, actor in (
                ("team", higher_ids["team_space_a"], "teamlead"),
                (
                    "responsibility",
                    higher_ids["responsibility_space"],
                    "executive",
                ),
            ):
                for index in range(12):
                    _insert_item(
                        connection,
                        label=f"{kind}-full-{index:02d}",
                        space_id=space_id,
                        actor=actor,
                        title=f"{kind}fulltopic document {index:02d}",
                        body=f"# {kind}fulltopic {index:02d}\n",
                        published=True,
                    )
                connection.execute(
                    """UPDATE knowledge_space SET generation=generation+1,
                              updated_at=updated_at+1,row_version=row_version+1
                        WHERE id=?""",
                    (space_id,),
                )
    finally:
        connection.close()
    service = CanonicalRetrievalService(
        higher_factory,
        scope_resolver=ScopeResolver(_aggregate_flags()),
        retrieval=HybridRetrievalRepository(),
    )
    for actor, space_id, query in (
        ("teamlead", higher_ids["team_space_a"], "teamfulltopic"),
        (
            "executive",
            higher_ids["responsibility_space"],
            "responsibilityfulltopic",
        ),
    ):
        result = service.retrieve_for_space(
            SpaceRetrievalRequest(
                actor_user_id=actor,
                actor_scope_epoch=_aggregate_epoch(higher_factory, actor),
                space_id=space_id,
                query=query,
                top_k=100,
            )
        )
        assert result.hybrid_explain is not None
        assert result.hybrid_explain.final_cap is None
        assert len(result.ranked_documents) == 12


@pytest.mark.scenario("RANK-004", "RANK-011")
def test_sbe_5_1_korean_fts_and_restarted_set_order_are_deterministic(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            korean_item, _ = _insert_item(
                connection,
                label="hybrid-korean-token",
                space_id=ids["part-space-a"],
                actor="admin",
                title="운영정책 변경 안내",
                body="# 운영정책 변경 안내\n",
                published=True,
            )
            connection.execute(
                """UPDATE knowledge_space
                      SET generation=generation+1,updated_at=updated_at+1,
                          row_version=row_version+1 WHERE id=?""",
                (ids["part-space-a"],),
            )
        with transaction(connection, TransactionMode.DEFERRED):
            first = HybridRetrievalRepository().retrieve_closure(
                connection,
                selected_space_id=ids["part-space-a"],
                source_space_ids=(ids["part-space-a"], ids["part-space-b"]),
                overlays=(),
                corpus_closure_sha256="c" * 64,
                query="운영정책",
            )
            restarted_reversed = HybridRetrievalRepository().retrieve_closure(
                connection,
                selected_space_id=ids["part-space-a"],
                source_space_ids=(ids["part-space-b"], ids["part-space-a"]),
                overlays=(),
                corpus_closure_sha256="c" * 64,
                query="운영정책",
            )
    finally:
        connection.close()
    assert [row.item_id for row in first.ranked_documents] == [korean_item]
    assert restarted_reversed.ranked_documents == first.ranked_documents


@pytest.mark.scenario("RANK-009")
def test_agent_mandatory_lane_is_four_and_full_corpus_is_byte_bounded(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    turn = _accepted_turn(factory, ids)
    _add_live_binding(factory, ids)
    _add_pin(factory, ids)
    bundle = AgentTurnContextService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
        binding_resolver=_TurnBindingResolver(factory, content="가" * 5_000),
        retrieval=HybridRetrievalRepository(),
    ).compose(_context_request(ids, turn))

    assert [row.kind for row in bundle.mandatory_contexts] == [
        "explicit_context",
        "pin",
    ]
    assert len(bundle.mandatory_contexts) <= 4
    assert bundle.mandatory_contexts[0].truncated is False
    assert bundle.mandatory_contexts[0].content_md == "가" * 5_000
    assert bundle.context_budget is not None
    assert bundle.context_budget.excerpt_budget_bytes == 98_304
    assert bundle.context_budget.excerpt_bytes <= 98_304
    assert len(bundle.ranked_contexts) == len(bundle.retrieval.ranked_documents)
    assert bundle.selected_view is not None
    assert bundle.selected_view.id == ids["part-space-a"]
    assert bundle.selected_view.kind == "part"
    assert bundle.selected_view.name == "Part A"
    payload = _turn_payload(bundle)
    assert payload["selected_view"] == {
        "space_id": ids["part-space-a"],
        "kind": "part",
        "name": "Part A",
        "org_path": ["AMHS담당", "Team A", "Part A"],
    }
    assert payload["pins"] == []
    assert payload["explicit_contexts"] == []
    assert len(payload["mandatory_contexts"]) == 2
    assert len(payload["retrieval_candidates"]) == len(bundle.ranked_contexts)


@pytest.mark.scenario("RANK-002")
def test_sbe_5_1_management_search_is_owner_active_only_and_cursor_bound(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            for index in range(12):
                _insert_item(
                    connection,
                    label=f"personal-management-{index:02d}",
                    space_id=ids["personal-admin"],
                    actor="admin",
                    title=f"Scopealpha personal result {index:02d}",
                    body=f"# scopealpha personal result {index:02d}\n",
                    published=True,
                )
            foreign_item, _ = _insert_item(
                connection,
                label="personal-management-foreign",
                space_id=ids["personal-other"],
                actor="other",
                title="Scopealpha foreign personal result",
                body="# scopealpha foreign personal result\n",
                published=True,
            )
    finally:
        connection.close()
    service = PersonalWikiManagementSearchService(
        factory, cursor_secret=b"sbe-5.1-management-search-cursor-key!!"
    )
    first = service.search(
        PersonalWikiManagementSearchRequest(
            actor_user_id="admin",
            actor_scope_epoch=int(ids["admin-epoch"]),
            request_id=str(uuid4()),
            query="scopealpha",
            limit=5,
        )
    )
    assert len(first.items) == 5
    assert first.next_cursor is not None
    assert {item.source_space_id for item in first.items} == {ids["personal-admin"]}
    assert foreign_item not in {item.item_id for item in first.items}
    second = service.search(
        PersonalWikiManagementSearchRequest(
            actor_user_id="admin",
            actor_scope_epoch=int(ids["admin-epoch"]),
            request_id=str(uuid4()),
            query="scopealpha",
            limit=5,
            cursor=first.next_cursor,
        )
    )
    assert not ({item.item_id for item in first.items} & {item.item_id for item in second.items})
    assert {item.source_space_id for item in second.items} == {ids["personal-admin"]}


@pytest.mark.scenario("RANK-005", "RANK-006", "RANK-010")
def test_sbe_5_1_two_hop_cycle_guard_and_contradiction_semantics(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            hop2_item, hop2_revision = _insert_item(
                connection,
                label="hybrid-hop-two-conflict",
                space_id=ids["part-space-a"],
                actor="admin",
                title="Conflicting operating rule",
                body="# conflicting operating rule\n",
                published=True,
            )
            connection.execute(
                """INSERT INTO knowledge_edge(
                     id,space_id,source_item_id,target_item_id,edge_type,edge_status,
                     origin,source_evidence_revision_id,target_evidence_revision_id,
                     approval_finding_id,evidence_json,needs_revalidation,created_by,
                     updated_by,created_at,updated_at,row_version
                   ) VALUES (?, ?, ?, ?, 'contradicts','active','manual',NULL,NULL,NULL,
                             '{}',0,'admin','admin',700,700,1)""",
                (str(uuid4()), ids["part-space-a"], ids["part-neighbor"], hop2_item),
            )
            connection.execute(
                """UPDATE knowledge_space
                      SET generation=generation+1,updated_at=updated_at+1,
                          row_version=row_version+1 WHERE id=?""",
                (ids["part-space-a"],),
            )
    finally:
        connection.close()
    result = _service(factory).retrieve_for_space(_request(ids))
    explain = result.hybrid_explain
    assert explain is not None and explain.two_hop_executed is True
    conflict = next(row for row in result.ranked_documents if row.item_id == hop2_item)
    assert conflict.revision_id == hop2_revision
    assert conflict.provenance == ("two_hop",)
    assert conflict.graph_hop == 2
    assert conflict.relation_type == "contradicts"
    assert conflict.relation_semantics == "conflicting_evidence"
    assert len({row.item_id for row in result.ranked_documents}) == len(
        result.ranked_documents
    )


@pytest.mark.scenario("RANK-007")
def test_sbe_5_1_exact_body_dedupe_retains_alternate_identity(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    duplicate_body = "# scopealpha shared source\n"
    duplicate_sha = hashlib.sha256(duplicate_body.encode("utf-8")).hexdigest()
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            duplicate_item, _ = _insert_item(
                connection,
                label="hybrid-exact-duplicate",
                space_id=ids["part-space-a"],
                actor="admin",
                title="Scopealpha exact duplicate",
                body=duplicate_body,
                published=True,
            )
            connection.execute(
                """UPDATE knowledge_space
                      SET generation=generation+1,updated_at=updated_at+1,
                          row_version=row_version+1 WHERE id=?""",
                (ids["part-space-a"],),
            )
    finally:
        connection.close()
    result = _service(factory).retrieve_for_space(_request(ids))
    matches = [
        row for row in result.ranked_documents
        if row.normalized_content_sha256 == duplicate_sha
    ]
    assert len(matches) == 1
    identities = {matches[0].item_id, *(alt.item_id for alt in matches[0].alternate_identities)}
    assert identities == {ids["part-hit"], duplicate_item}


@pytest.mark.scenario("RANK-013")
def test_sbe_5_1_multi_source_fairness_preserves_relevant_minority(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            for index in range(15):
                _insert_item(
                    connection,
                    label=f"fairness-majority-{index:02d}",
                    space_id=ids["part-space-a"],
                    actor="admin",
                    title=f"Scopealpha majority {index:02d}",
                    body=f"# scopealpha majority {index:02d}\n",
                    published=True,
                )
            connection.execute(
                """UPDATE knowledge_space SET generation=generation+1,
                          updated_at=updated_at+1,row_version=row_version+1
                    WHERE id=?""",
                (ids["part-space-a"],),
            )
        with transaction(connection, TransactionMode.DEFERRED):
            snapshot = HybridRetrievalRepository().retrieve_closure(
                connection,
                selected_space_id=ids["part-space-a"],
                source_space_ids=(ids["part-space-a"], ids["part-space-b"]),
                overlays=(),
                corpus_closure_sha256="a" * 64,
                query="scopealpha",
            )
    finally:
        connection.close()
    assert snapshot.hybrid_explain is not None
    assert snapshot.hybrid_explain.final_cap is None
    assert len(snapshot.ranked_documents) > FINAL_CAP
    assert ids["foreign-hit"] in {row.item_id for row in snapshot.ranked_documents}
    assert {row.source_space_id for row in snapshot.ranked_documents} == {
        ids["part-space-a"],
        ids["part-space-b"],
    }


@pytest.mark.scenario("REF-006")
@pytest.mark.parametrize("blob_backed", [False, True])
@pytest.mark.parametrize("historical", [False, True])
def test_sbe_5_1_stable_reference_is_nonrecursive_mandatory_context(tmp_path, monkeypatch, blob_backed, historical) -> None:
    from dataclasses import replace
    import hashlib
    from code_assist_v1.knowledge_core.scope import SessionPrincipal
    from code_assist_v1.knowledge_core.services.source_artifacts import (
        SourceArtifactService, ItemSourceArtifactDownloadRequest,
    )
    from code_assist_v1.knowledge_core.services.management_reads import CanonicalManagementReadService
    from code_assist_v1.knowledge_core.services.wiki_files import WikiFilesService
    from code_assist_v1.knowledge_core.agent_execution import CanonicalAgentTurnExecutor
    from code_assist_v1.tests.knowledge_spaces.unit.test_workspace_features import Stager

    factory = _factory(tmp_path)
    ids = _seed(factory)
    reference_id = str(uuid4())
    operation_id = str(uuid4())
    reference_body = "# Fixed source\n\n" + "명시적으로 참조된 기준 문서.\n" * 300 + "\n최종 조건: 0.75% 미만."
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            target_item, target_revision = _insert_item(
                connection, label="long-fixed-reference", space_id=ids["part-space-a"],
                actor="admin", title="Fixed source", body=reference_body, published=True,
                blob_backed=blob_backed,
            )
            ids["part-neighbor"] = target_item
            ids["part-neighbor-revision"] = target_revision
            audit = connection.execute(
                """SELECT id FROM audit_event
                    WHERE item_id=? ORDER BY id LIMIT 1""",
                (ids["part-hit"],),
            ).fetchone()
            assert audit is not None
            connection.execute(
                """INSERT INTO outbox_event(
                     logical_key,space_id,aggregate_type,aggregate_id,
                     aggregate_version,event_type,payload_json,
                     payload_schema_version,state,available_at,claim_owner,
                     claim_expires_at,attempt_count,last_error_json,created_at,
                     completed_at
                   ) VALUES (?,?,'knowledge_item',?,1,'reference.fixture','{}',1,
                             'pending',800,NULL,NULL,0,NULL,800,NULL)""",
                (f"reference-fixture:{reference_id}", ids["part-space-a"], ids["part-hit"]),
            )
            outbox_id = int(
                connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            )
            connection.execute(
                """INSERT INTO knowledge_explicit_reference(
                     id,source_space_id,source_item_id,source_revision_id,
                     ordinal,syntax_version,target_space_id,target_item_id,
                     target_revision_id,closure_proof_sha256,actor_user_id,
                     operation_id,effect_key,audit_event_id,outbox_event_id,
                     availability_status,unavailable_reason,created_at,updated_at,
                     row_version
                   ) VALUES (?,?,?,?,1,'item-ref-v1',?,?,?,?,?,?,?, ?,?,
                             'available',NULL,800,800,1)""",
                (
                    reference_id,
                    ids["part-space-a"],
                    ids["part-hit"],
                    ids["part-hit-revision"],
                    ids["part-space-a"],
                    ids["part-neighbor"],
                    ids["part-neighbor-revision"],
                    "b" * 64,
                    "admin",
                    operation_id,
                    f"explicit-reference:{reference_id}",
                    int(audit["id"]),
                    outbox_id,
                ),
            )
    finally:
        connection.close()
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            connection.execute(
                "UPDATE knowledge_space SET write_enabled=1,row_version=row_version+1 WHERE id=?",
                (ids["part-space-a"],),
            )
    finally:
        connection.close()
    scopes = ScopeResolver(replace(_flags(), part_writes=True))
    original_content = "고정 참조의 원본 파일. 최종 조건: 0.75% 미만.".encode()
    original_service = SourceArtifactService(factory, scope_resolver=scopes, stager=Stager())
    original_files = WikiFilesService(original_service, CanonicalManagementReadService(factory, scope_resolver=scopes))
    principal = SessionPrincipal("admin", int(ids["admin-epoch"]))
    attached_original = original_files.attach(
        principal, ids["part-space-a"], target_item,
        filename="fixed-reference.txt", content=original_content,
        operation_id=str(uuid4()), expected_revision_id=target_revision,
    )
    later_artifact = None
    if historical:
        import base64
        from code_assist_v1.knowledge_core.services.editing import (
            AcquireLeaseCommand, ImmutableItemEditService, LeaseHmacKeyRing, SaveItemCommand,
        )
        from code_assist_v1.tests.knowledge_spaces.unit.test_canonical_retrieval import _publish
        editor = ImmutableItemEditService(
            factory, scope_resolver=scopes,
            key_ring=LeaseHmacKeyRing(active_key_id="citation", keys={"citation": b"c" * 32}),
        )
        session = str(uuid4())
        token = base64.urlsafe_b64encode(b"c" * 32).decode().rstrip("=")
        lease = editor.acquire(AcquireLeaseCommand(
            principal=principal, space_id=ids["part-space-a"], item_id=target_item,
            editor_session_id=session, operation_id=str(uuid4()), request_id=str(uuid4()), raw_token=token,
        ))
        revised_body = "# Fixed source revised\n\nNew rule: 0.50% instead of 0.75%.\n"
        editor.save(SaveItemCommand(
            principal=principal, space_id=ids["part-space-a"], item_id=target_item,
            editor_session_id=session, lease_id=lease.lease_id, fencing_generation=lease.fencing_generation,
            expected_item_row_version=2, expected_revision_id=target_revision,
            operation_id=str(uuid4()), request_id=str(uuid4()),
            markdown=revised_body,
            reason="Historical citation must retain original R1 evidence", raw_token=token,
        ))
        connection = factory.open()
        try:
            with transaction(connection, TransactionMode.IMMEDIATE):
                current_revision = connection.execute(
                    "SELECT current_revision_id FROM knowledge_item WHERE id=?", (target_item,),
                ).fetchone()[0]
                # An edit deliberately makes the item provisional. Seed the normal
                # reviewed publication transition, retaining its real R1 history.
                _publish(
                    connection, "citation-fixed-r2", target_item, current_revision,
                    ids["part-space-a"], "admin", "Fixed source revised", revised_body,
                )
        finally:
            connection.close()
        later_artifact = original_files.attach(
            principal, ids["part-space-a"], target_item, filename="later-revision.txt",
            content=b"Original first attached to R2 only.", operation_id=str(uuid4()),
            expected_revision_id=current_revision,
        )["artifact_id"]
    original = AgentTurnContextService._hybrid_mandatory_candidates
    observed = []

    def inspect_candidates(service, connection, **kwargs):
        rows = original(service, connection, **kwargs)
        observed.extend(rows)
        assert all(row.content_md == "" for row in rows if row.kind == "stable_reference")
        return rows

    monkeypatch.setattr(AgentTurnContextService, "_hybrid_mandatory_candidates", inspect_candidates)
    turn = _accepted_turn(factory, ids)
    bundle = AgentTurnContextService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
        retrieval=HybridRetrievalRepository(),
        content_loader=lambda digest, size: reference_body.encode(),
    ).compose(_context_request(ids, turn))
    assert len(bundle.mandatory_contexts) == 1
    reference = bundle.mandatory_contexts[0]
    assert reference.kind == "stable_reference"
    assert reference.identity == reference_id
    assert reference.source_revision_id == ids["part-hit-revision"]
    assert reference.target_revision_id == ids["part-neighbor-revision"]
    assert reference.target_item_id == ids["part-neighbor"]
    assert reference.newer_revision_available is historical
    assert observed
    assert reference.content_md == reference_body
    assert reference.source_bytes == len(reference_body.encode()) > 4_096
    assert not reference.truncated
    assert reference.target_source_space_id == ids["part-space-a"]
    assert [row.artifact_id for row in reference.source_artifacts] == [attached_original["artifact_id"]]
    assert reference.source_artifacts[0].content_sha256 == hashlib.sha256(original_content).hexdigest()

    def citation_runner(_task, payload, **kwargs):
        supplied = next(row for row in payload["mandatory_contexts"] if row["identity"] == reference_id)
        assert supplied["content_md"] == reference_body
        assert "source_documents" not in payload
        assert "download_url" not in str(payload["citation_sources"])
        return f"## 기준\n\n최종 조건: 0.75% 미만.\n\n근거 문서: [[wiki:{supplied['citation_handle']}]]", {}

    completion = CanonicalAgentTurnExecutor(runner=citation_runner).complete(bundle)
    expected_url = (f"/api/knowledge/v1/spaces/{ids['part-space-a']}/items/{target_item}"
                    f"/source-artifacts/{attached_original['artifact_id']}/download")
    assert expected_url in completion.assistant_content["text"]
    cited = completion.final_response["citation_trace"]["sources"]
    assert len(cited) == 1 and cited[0]["revision_id"] == target_revision
    if later_artifact:
        assert later_artifact not in str(cited)
        assert "later-revision.txt" not in completion.assistant_content["text"]
    downloaded = original_service.download(ItemSourceArtifactDownloadRequest(
        principal, ids["part-space-a"], target_item, attached_original["artifact_id"],
    ))
    assert downloaded.content == original_content

    # A selected fixed reference must remain readable when a preceding pin
    # consumes the entire initial context. Keep R1 even after a legal R2 edit;
    # a later R2 original may not be attached to the historical R1 fragments.
    from code_assist_v1.tests.knowledge_spaces.unit.test_canonical_retrieval import _add_pin
    blocker_body = "# Pinned context\n\n" + "배경 기록\n" * 8_000
    assert len(blocker_body.encode()) > 98_304
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            blocker_item, blocker_revision = _insert_item(
                connection, label="reading-pin-before-fixed-reference",
                space_id=ids["part-space-a"], actor="admin", title="Pinned context",
                body=blocker_body, published=True,
            )
    finally:
        connection.close()
    _add_pin(factory, {**ids, "part-hit": blocker_item, "part-hit-revision": blocker_revision})
    reader_service = AgentTurnContextService(
        factory, scope_resolver=ScopeResolver(_flags()), retrieval=HybridRetrievalRepository(),
        content_loader=lambda digest, size: reference_body.encode(),
    )
    with reader_service.reading_session(_context_request(ids, turn)) as reading:
        assert reference_id not in {row.identity for row in reading.context.mandatory_contexts}
        chunks = []
        while True:
            page = reading.reader.read_next(max_bytes=16_384)
            chunks.extend(row for row in page.chunks
                          if (row.source.metadata.item_id, row.source.metadata.revision_id)
                          == (target_item, target_revision))
            if page.selection_exhausted:
                break
        assert "".join(row.content_md for row in chunks) == reference_body
        assert all("stable_reference" in row.source.selection_roles for row in chunks)
        assert all([artifact.artifact_id for artifact in row.source_artifacts]
                   == [attached_original["artifact_id"]] for row in chunks)
        assert later_artifact is None or later_artifact not in str(chunks)
        reading.reader.revalidate()
        # Simulate a persisted reference availability change during provider
        # I/O without changing target/current revision or closure generation.
        # The provider-return fence must inspect the reference, not only items.
        connection = factory.open()
        try:
            with transaction(connection, TransactionMode.IMMEDIATE):
                connection.execute(
                    """UPDATE knowledge_explicit_reference
                          SET availability_status='unavailable',unavailable_reason='permission_lost',
                              row_version=row_version+1,updated_at=updated_at+1 WHERE id=?""",
                    (reference_id,),
                )
        finally:
            connection.close()
        from code_assist_v1.knowledge_core.services.agent_context import AgentContextStateConflictError
        with pytest.raises(AgentContextStateConflictError):
            reading.reader.revalidate()
        assert reading.reader.closed


@pytest.mark.scenario("RANK-001", "RANK-002")
def test_sbe_5_1_runtime_shares_one_hybrid_repository_and_separates_management_search(
    tmp_path,
) -> None:
    factory = _factory(tmp_path)
    runtime = compose_canonical_agent_runtime(
        factory,
        flags=FeatureFlags(
            core_enabled=True,
            personal_reads=True,
            hybrid_rerank=True,
        ),
        principal_resolver=lambda: None,
    )
    retrieval_repository = runtime.services.retrieval._retrieval
    assert isinstance(retrieval_repository, HybridRetrievalRepository)
    assert runtime.services.agent_context._retrieval is retrieval_repository
    assert runtime.services.personal_wiki_search is None

    multi = compose_canonical_agent_runtime(
        factory,
        flags=FeatureFlags(
            core_enabled=True,
            multi_wiki_schema=True,
            multi_wiki_reads=True,
            personal_reads=True,
            hybrid_rerank=True,
        ),
        principal_resolver=lambda: None,
        personal_wiki_cursor_secret=b"sbe-5.1-runtime-personal-wiki-secret!!",
    )
    assert isinstance(
        multi.services.personal_wiki_search,
        PersonalWikiManagementSearchService,
    )


@pytest.mark.scenario("RANK-003", "RANK-004", "RANK-005", "RANK-006")
def test_sbe_5_1_sql_work_is_fixed_for_single_and_multi_source_closures(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    counts: list[int] = []
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            for index, sources in enumerate(
                (
                    (ids["part-space-a"],),
                    (ids["part-space-a"], ids["part-space-b"]),
                )
            ):
                before = database_runtime_metrics()["execute_count"]
                HybridRetrievalRepository().retrieve_closure(
                    connection,
                    selected_space_id=ids["part-space-a"],
                    source_space_ids=sources,
                    overlays=(),
                    corpus_closure_sha256=("c" if index == 0 else "d") * 64,
                    query="scopealpha",
                )
                counts.append(database_runtime_metrics()["execute_count"] - before)
    finally:
        connection.close()
    # One batch reads admitted graph bodies only after both-hop dedupe. The
    # extra statement is per retrieval, never per source, edge or document.
    assert counts == [10, 10]
