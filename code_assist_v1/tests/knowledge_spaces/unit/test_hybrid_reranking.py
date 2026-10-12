from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import UUID, uuid4, uuid5

import pytest
from flask import Flask

from code_assist_v1.knowledge_core.agent_execution import _turn_payload
from code_assist_v1.knowledge_core.agent_runtime import compose_canonical_agent_runtime
from code_assist_v1.knowledge_core.composition import register_knowledge_core
from code_assist_v1.knowledge_core.db import (
    TransactionMode,
    database_runtime_metrics,
    transaction,
)
from code_assist_v1.knowledge_core.domain import (
    FeatureFlags,
    TemporalAssertionKind,
    TemporalAssertionOrigin,
    HostKind,
)
from code_assist_v1.knowledge_core.repositories.hybrid_retrieval import (
    HybridRetrievalRepository,
)
from code_assist_v1.knowledge_core.scope import ScopeResolver, SessionPrincipal
from code_assist_v1.knowledge_core.services.agent_context import (
    AgentTurnContextService,
)
from code_assist_v1.knowledge_core.services.hybrid_reranking import (
    DETERMINISTIC_WEIGHT_PERCENT,
    HybridRankingService,
    LocalCrossEncoderAdapter,
    MODEL_WEIGHT_PERCENT,
    retrieval_query_text,
)
from code_assist_v1.knowledge_core.services.retrieval import (
    CanonicalRetrievalService,
    SpaceRetrievalRequest,
)
from code_assist_v1.knowledge_core.services.temporal import (
    TemporalAssertionCommand,
    TemporalService,
    VerificationAttestCommand,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_canonical_retrieval import (
    _TurnBindingResolver,
    _accepted_turn,
    _context_request,
    _factory,
    _flags,
    _insert_item,
    _seed,
)


pytestmark = pytest.mark.unit


def _write_flags() -> FeatureFlags:
    return FeatureFlags(
        core_enabled=True,
        personal_reads=True,
        personal_writes=True,
        part_reads=True,
        part_writes=True,
    )


@pytest.mark.scenario("RANK-015", "RANK-018")
def test_sbe_5_2_runtime_shares_post_transaction_ranking_service(tmp_path) -> None:
    factory = _factory(tmp_path)
    runtime = compose_canonical_agent_runtime(
        factory,
        flags=FeatureFlags(
            core_enabled=True,
            personal_reads=True,
            hybrid_rerank=True,
            local_cross_encoder=True,
            temporal_ranking=True,
        ),
        principal_resolver=lambda: None,
        model_runner=lambda *_args, **_kwargs: (None, {}),
    )
    ranking = runtime.services.hybrid_ranking
    assert isinstance(ranking, HybridRankingService)
    assert runtime.services.retrieval._postprocessor is ranking
    assert runtime.services.agent_context._postprocessor is ranking


@pytest.mark.scenario("RANK-018", "RANK-019", "TIME-020")
def test_sbe_5_2_search_api_exposes_ranked_temporal_envelope_additively(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    runtime = compose_canonical_agent_runtime(
        factory,
        flags=FeatureFlags(
            core_enabled=True,
            personal_reads=True,
            part_reads=True,
            hybrid_rerank=True,
            temporal_ranking=True,
        ),
        principal_resolver=lambda: {
            "id": "admin",
            "scope_epoch": int(ids["admin-epoch"]),
        },
    )
    app = Flask("sbe-5.2-ranked-search", static_folder=None)
    register_knowledge_core(
        app,
        HostKind.CODE,
        environ={
            "KNOWLEDGE_CORE_ENABLED": "1",
            "KNOWLEDGE_CORE_READS": "1",
            "KNOWLEDGE_CORE_PART_READS": "1",
            "KNOWLEDGE_CORE_HYBRID_RERANK": "1",
            "KNOWLEDGE_CORE_TEMPORAL_RANKING": "1",
        },
        services=runtime.services,
        canonical_route_bindings=runtime.route_bindings,
    )
    response = app.test_client().post(
        f"/api/knowledge/v1/spaces/{ids['part-space-a']}/search",
        json={"as_of_ms": 1_800_000_000_000, "query": "scopealpha latest"},
    )
    assert response.status_code == 200, response.json
    assert response.json["items"]
    assert response.json["ranked_documents"]
    assert response.json["ranking"]["pipeline_version"] == "hybrid-rerank-v4-shared-preview-budget"
    assert response.json["ranking"]["temporal_intent"] == "current"
    assert response.json["ranking"]["reranker_status"] == "disabled"
    assert response.json["ranked_documents"][0]["temporal"] is not None


def _snapshot(factory, ids, *, query: str = "scopealpha"):
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            return HybridRetrievalRepository().retrieve_closure(
                connection,
                selected_space_id=ids["part-space-a"],
                source_space_ids=(ids["part-space-a"],),
                overlays=(),
                corpus_closure_sha256="d" * 64,
                query=query,
            )
    finally:
        connection.close()


def _retrieval_service(factory, *, ranking=None):
    return CanonicalRetrievalService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
        retrieval=HybridRetrievalRepository(
            query_transformer=(retrieval_query_text if ranking is not None else None)
        ),
        postprocessor=ranking,
    )


@pytest.mark.scenario("RANK-004", "RANK-015", "RANK-017")
@pytest.mark.parametrize("blob_backed", [False, True])
def test_reranker_sees_late_expanded_query_match_not_only_document_prefix(tmp_path, blob_backed):
    factory = _factory(tmp_path)
    ids = _seed(factory)
    query = "비상시 복구 순서를 알려줘"
    late = "Recovery protocol: isolate the controller, restore the snapshot, then validate telemetry."
    body = "# Continuity handbook\n\n" + "Unrelated administrative background.\n" * 700 + late
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            target, _ = _insert_item(connection, label="late-expanded-reranker",
                space_id=ids["part-space-a"], actor="admin", title="Continuity handbook",
                body=body, published=True, blob_backed=blob_backed)
            distractor, _ = _insert_item(connection, label="prefix-distractor-reranker",
                space_id=ids["part-space-a"], actor="admin", title="Recovery terminology",
                body="# Recovery protocol glossary\n\nOnly a label list; no operating procedure.", published=True)
    finally:
        connection.close()
    captured = []

    class Expander:
        def expand(self, received):
            assert received == query
            return "recovery protocol controller snapshot telemetry"

    def runner(task, payload, **kwargs):
        captured.append(payload)
        by_id = {row["item_id"]: row for row in payload["candidates"]}
        assert payload["query"] == query
        assert late in by_id[target]["excerpt_md"]
        assert "no operating procedure" in by_id[distractor]["excerpt_md"]
        assert ids["foreign-hit"] not in by_id
        return json.dumps({"schema_version": 1, "grades": [
            {"item_id": row["item_id"], "grade": 4 if row["item_id"] == target else 0}
            for row in payload["candidates"]
        ]}), {"model_id": "capture-grader", "model_kind": "codex"}

    ranking = HybridRankingService(factory, local_cross_encoder=True, temporal_ranking=False,
                                   adapter=LocalCrossEncoderAdapter(runner=runner))
    result = CanonicalRetrievalService(factory, scope_resolver=ScopeResolver(_flags()),
        retrieval=HybridRetrievalRepository(), query_expander=Expander(), postprocessor=ranking,
    ).retrieve_for_space(_space_request(ids, query))
    assert len(captured) == 1
    assert result.ranking_envelope.reranker_status == "applied"
    assert result.ranked_documents[0].item_id == target
    assert result.ranked_documents[0].model_grade == 4


@pytest.mark.scenario("RANK-005", "RANK-006", "RANK-015", "RANK-017")
@pytest.mark.parametrize("blob_backed", [False, True])
def test_reranker_keeps_one_and_two_hop_neighbors_and_reads_late_inflected_evidence(tmp_path, blob_backed):
    factory = _factory(tmp_path)
    ids = _seed(factory)
    facts = (
        "복구절차는 센서를 격리한 다음 검증된 스냅샷을 적용해야 한다.",
        "복구예외는 구형 제어기이며 수동 검증 승인 후에만 재기동한다.",
    )
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            seed, _ = _insert_item(connection, label="graph-query-seed", space_id=ids["part-space-a"],
                actor="admin", title="복구", body="# 복구\nSee linked operating records.", published=True)
            neighbors = []
            for index, fact in enumerate(facts):
                body = "# Operating record\n\n" + "General background and administrative notes.\n" * 900 + fact
                item, _ = _insert_item(connection, label=f"graph-query-late-{index}",
                    space_id=ids["part-space-a"], actor="admin", title=f"Operating record {index}",
                    body=body, published=True, blob_backed=blob_backed)
                neighbors.append(item)
            for source, target in ((seed, neighbors[0]), (neighbors[0], neighbors[1])):
                connection.execute("""INSERT INTO knowledge_edge(
                    id,space_id,source_item_id,target_item_id,edge_type,edge_status,
                    origin,source_evidence_revision_id,target_evidence_revision_id,
                    approval_finding_id,evidence_json,needs_revalidation,created_by,
                    updated_by,created_at,updated_at,row_version
                    ) VALUES (?,?,?,?,'supports','active','manual',NULL,NULL,NULL,
                              '{}',0,'admin','admin',450,450,1)""",
                    (str(uuid4()), ids["part-space-a"], source, target))
    finally:
        connection.close()
    captured = []

    def runner(task, payload, **kwargs):
        captured.append(payload)
        by_id = {row["item_id"]: row for row in payload["candidates"]}
        assert set(by_id) == {seed, *neighbors}
        for item, fact in zip(neighbors, facts, strict=True):
            assert fact in by_id[item]["excerpt_md"]
            assert len(by_id[item]["excerpt_md"].encode()) <= 12_288
        return json.dumps({"schema_version": 1, "grades": [
            {"item_id": item, "grade": 4} for item in by_id
        ]}), {"model_id": "capture-grader", "model_kind": "codex"}

    baseline = _snapshot(factory, ids, query="복구")
    assert {row.item_id: row.graph_hop for row in baseline.reranker_candidates} == {
        seed: 0, neighbors[0]: 1, neighbors[1]: 2,
    }
    ranking = HybridRankingService(factory, local_cross_encoder=True, temporal_ranking=False,
                                   adapter=LocalCrossEncoderAdapter(runner=runner))
    result = ranking.process(baseline, query="복구")
    assert len(captured) == 1
    assert result.ranking_envelope.reranker_status == "applied"
    assert {row.item_id for row in result.ranked_documents} == {seed, *neighbors}
    fallback = HybridRankingService(factory, local_cross_encoder=True, temporal_ranking=False,
        adapter=LocalCrossEncoderAdapter(runner=None)).process(baseline, query="복구")
    assert fallback.ranked_documents == baseline.ranked_documents


def _space_request(ids, query: str, *, as_of_ms: int | None = None):
    return SpaceRetrievalRequest(
        actor_user_id="admin",
        actor_scope_epoch=int(ids["admin-epoch"]),
        space_id=ids["part-space-a"],
        query=query,
        top_k=20,
        as_of_ms=as_of_ms,
    )


def _add_direct_documents(factory, ids, *, label: str, count: int) -> tuple[str, ...]:
    created: list[str] = []
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            for index in range(count):
                item_id, _revision_id = _insert_item(
                    connection,
                    label=f"{label}-{index}",
                    space_id=ids["part-space-a"],
                    actor="admin",
                    title=f"Policy timeline {label} {index}",
                    body=f"# policy timeline {label} {index}\n",
                    published=True,
                )
                created.append(item_id)
            connection.execute(
                """UPDATE knowledge_space
                      SET generation=generation+1,updated_at=updated_at+1,
                          row_version=row_version+1 WHERE id=?""",
                (ids["part-space-a"],),
            )
    finally:
        connection.close()
    return tuple(created)


@pytest.mark.scenario("RANK-015", "RANK-017", "RANK-021")
def test_sbe_5_2_local_adapter_is_one_bounded_authorized_cached_call(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    _add_direct_documents(factory, ids, label="adapter", count=12)
    calls: list[dict[str, object]] = []

    def runner(_task, payload, **kwargs):
        calls.append({"payload": payload, "kwargs": kwargs})
        assert len(payload["candidates"]) <= 40
        assert kwargs["provider_order"] == ["gguf", "codex"]
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        assert "temporal_summary" not in serialized
        assert "information_effective" not in serialized
        grades = [
            {"item_id": row["item_id"], "grade": index % 5}
            for index, row in enumerate(payload["candidates"])
        ]
        return json.dumps({"schema_version": 1, "grades": grades}), {
            "model_id": "codex-bridge-local-reranker",
            "model_kind": "codex",
        }

    adapter = LocalCrossEncoderAdapter(
        runner=runner,
        model_revision="codex-bridge-local-reranker-r1",
    )
    ranking = HybridRankingService(
        factory,
        local_cross_encoder=True,
        temporal_ranking=True,
        adapter=adapter,
        clock_ms=lambda: 1_800_000_000_000,
    )
    service = _retrieval_service(factory, ranking=ranking)
    first = service.retrieve_for_space(_space_request(ids, "policy"))
    assert first.ranking_envelope is not None
    assert first.ranking_envelope.reranker_status == "applied"
    assert first.ranking_envelope.cache_hit is False
    assert first.hybrid_explain is not None
    assert first.hybrid_explain.final_cap is None
    assert len(first.ranked_documents) > 10
    allowed = {row["item_id"] for row in calls[0]["payload"]["candidates"]}
    assert {row.item_id for row in first.ranked_documents} <= allowed
    for row in first.ranked_documents:
        assert row.model_grade is not None
        expected = (
            DETERMINISTIC_WEIGHT_PERCENT * int(row.deterministic_score_ppm)
            + MODEL_WEIGHT_PERCENT * row.model_grade * 250_000
        ) // 100
        assert row.score_ppm == expected
    replay = service.retrieve_for_space(_space_request(ids, "policy"))
    assert len(calls) == 1
    assert replay.ranked_documents == first.ranked_documents
    assert replay.ranking_envelope is not None
    assert replay.ranking_envelope.cache_hit is True
    assert replay.ranking_envelope.input_sha256 == first.ranking_envelope.input_sha256


@pytest.mark.scenario("RANK-021")
def test_sbe_5_2_sql_and_model_work_is_constant_at_1_100_and_1000_wikis(
    tmp_path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    namespace = UUID("23dfb621-44fd-4d99-902f-a3d7e62420bd")
    extra_spaces = tuple(str(uuid5(namespace, f"wiki:{index}")) for index in range(999))
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            connection.executemany(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'personal','admin',NULL,'active',0,0,100,100,1)""",
                ((space_id,) for space_id in extra_spaces),
            )
    finally:
        connection.close()
    sql_counts: list[int] = []
    model_counts: list[int] = []
    for wiki_count in (1, 100, 1000):
        source_ids = (ids["part-space-a"], *extra_spaces[: wiki_count - 1])
        connection = factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                before = database_runtime_metrics()["execute_count"]
                snapshot = HybridRetrievalRepository().retrieve_closure(
                    connection,
                    selected_space_id=ids["part-space-a"],
                    source_space_ids=source_ids,
                    overlays=(),
                    corpus_closure_sha256=sha256(
                        f"closure:{wiki_count}".encode()
                    ).hexdigest(),
                    query="scopealpha",
                )
                sql_counts.append(
                    database_runtime_metrics()["execute_count"] - before
                )
        finally:
            connection.close()
        calls = [0]

        def runner(_task, payload, **_kwargs):
            calls[0] += 1
            return json.dumps(
                {
                    "schema_version": 1,
                    "grades": [
                        {"item_id": row["item_id"], "grade": 2}
                        for row in payload["candidates"]
                    ],
                }
            ), {"model_id": "bounded-local", "model_kind": "gguf"}

        ranking = HybridRankingService(
            factory,
            local_cross_encoder=True,
            temporal_ranking=False,
            adapter=LocalCrossEncoderAdapter(runner=runner),
            clock_ms=lambda: 1_800_000_000_000,
        )
        ranked = ranking.process(snapshot, query="scopealpha")
        assert ranked.hybrid_explain is not None
        assert ranked.hybrid_explain.final_cap is None
        assert ranked.ranked_documents
        model_counts.append(calls[0])
    assert len(set(sql_counts)) == 1
    assert model_counts == [1, 1, 1]


@pytest.mark.scenario("RANK-016", "RANK-017")
@pytest.mark.parametrize(
    ("runner", "reason"),
    (
        (lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()), "timeout"),
        (lambda *_args, **_kwargs: (_ for _ in ()).throw(MemoryError()), "oom"),
        (lambda *_args, **_kwargs: (None, {}), "model_unavailable"),
        (lambda *_args, **_kwargs: ("{}", {"model_id": "m", "model_kind": "codex"}), "malformed_output"),
        (
            lambda *_args, **_kwargs: (
                json.dumps(
                    {
                        "schema_version": 1,
                        "grades": [
                            {
                                "item_id": "00000000-0000-0000-0000-000000000000",
                                "grade": 4,
                            }
                        ],
                    }
                ),
                {"model_id": "m", "model_kind": "codex"},
            ),
            "injected_output",
        ),
        (lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError()), "provider_failure"),
    ),
)
def test_sbe_5_2_all_model_failures_return_exact_deterministic_core(
    tmp_path, runner, reason
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    baseline = _snapshot(factory, ids)
    ranking = HybridRankingService(
        factory,
        local_cross_encoder=True,
        temporal_ranking=False,
        adapter=LocalCrossEncoderAdapter(runner=runner),
        clock_ms=lambda: 1_800_000_000_000,
    )
    result = ranking.process(baseline, query="scopealpha", as_of_ms=1_800_000_000_000)
    assert result.ranked_documents == baseline.ranked_documents
    assert result.ranking_envelope is not None
    assert result.ranking_envelope.reranker_status == "fallback"
    assert result.ranking_envelope.reranker_reason == reason


@pytest.mark.scenario("RANK-016")
def test_sbe_5_2_agent_context_survives_malformed_local_model(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    turn = _accepted_turn(factory, ids)
    ranking = HybridRankingService(
        factory,
        local_cross_encoder=True,
        temporal_ranking=True,
        adapter=LocalCrossEncoderAdapter(
            runner=lambda *_args, **_kwargs: (
                "not-json",
                {"model_id": "codex-local", "model_kind": "codex"},
            )
        ),
        clock_ms=lambda: 1_800_000_000_000,
    )
    bundle = AgentTurnContextService(
        factory,
        scope_resolver=ScopeResolver(_flags()),
        binding_resolver=_TurnBindingResolver(factory),
        retrieval=HybridRetrievalRepository(),
        postprocessor=ranking,
    ).compose(_context_request(ids, turn))
    assert bundle.retrieval.ranking_envelope is not None
    assert bundle.retrieval.ranking_envelope.reranker_status == "fallback"
    assert bundle.retrieval.ranked_documents
    assert len(bundle.ranked_contexts) == len(bundle.retrieval.ranked_documents)
    payload = _turn_payload(bundle)
    assert payload["ranking"]["reranker_status"] == "fallback"
    assert all(row["temporal"] is not None for row in payload["retrieval_candidates"])


def _assert_effective(
    service: TemporalService,
    ids,
    *,
    item_id: str,
    revision_id: str,
    date_value: str,
    label: str,
) -> dict[str, object]:
    return service.assert_temporal(
        TemporalAssertionCommand(
            principal=SessionPrincipal("admin", int(ids["admin-epoch"])),
            space_id=ids["part-space-a"],
            item_id=item_id,
            expected_revision_id=revision_id,
            assertion_kind=TemporalAssertionKind.INFORMATION_EFFECTIVE,
            value={
                "precision": "date",
                "timezone": "Asia/Seoul",
                "value": date_value,
            },
            expected_head_event_id=None,
            expected_head_generation=0,
            origin=TemporalAssertionOrigin.USER,
            evidence_sha256=sha256(f"evidence:{label}".encode()).hexdigest(),
            evidence_reference=f"SBE-5.2 fixture {label}",
            operation_id=str(uuid4()),
            request_id=str(uuid4()),
        )
    )


def _enable_fixture_part_writes(factory, ids) -> None:
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            connection.execute(
                """UPDATE knowledge_space
                      SET write_enabled=1,updated_at=updated_at+1,
                          row_version=row_version+1 WHERE id=?""",
                (ids["part-space-a"],),
            )
    finally:
        connection.close()


def _temporal_documents(factory, ids):
    labels = ("old", "current", "future")
    dates = ("2020-01-01", "2026-08-24", "2026-08-26")
    values: dict[str, tuple[str, str]] = {}
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            for label in labels:
                values[label] = _insert_item(
                    connection,
                    label=f"temporal-rank-{label}",
                    space_id=ids["part-space-a"],
                    actor="admin",
                    title=f"Policy timeline {label}",
                    body=f"# policy timeline {label}\n",
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
    _enable_fixture_part_writes(factory, ids)
    temporal = TemporalService(factory, ScopeResolver(_write_flags()))
    assertions = {
        label: _assert_effective(
            temporal,
            ids,
            item_id=values[label][0],
            revision_id=values[label][1],
            date_value=date_value,
            label=label,
        )
        for label, date_value in zip(labels, dates, strict=True)
    }
    temporal.attest(
        VerificationAttestCommand(
            principal=SessionPrincipal("admin", int(ids["admin-epoch"])),
            space_id=ids["part-space-a"],
            item_id=values["current"][0],
            expected_revision_id=values["current"][1],
            expected_temporal_summary_sha256=str(
                assertions["current"]["temporal_summary_sha256"]
            ),
            expected_head_event_id=None,
            expected_head_generation=0,
            method="manual_review",
            evidence_sha256=sha256(b"verified current policy").hexdigest(),
            evidence_reference="SBE-5.2 verified fixture",
            note="verified current policy",
            subject_verified_at=None,
            operation_id=str(uuid4()),
            request_id=str(uuid4()),
        )
    )
    return values


def _utc_ms(year: int, month: int, day: int, hour: int = 0) -> int:
    return int(datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp() * 1000)


@pytest.mark.scenario("RANK-018", "RANK-019", "TIME-020")
def test_sbe_5_2_temporal_intent_precedence_neutrality_and_boundary_cache(
    tmp_path,
) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    values = _temporal_documents(factory, ids)
    clock = [_utc_ms(2026, 8, 25, 14)]
    ranking = HybridRankingService(
        factory,
        local_cross_encoder=False,
        temporal_ranking=True,
        clock_ms=lambda: clock[0],
    )
    service = _retrieval_service(factory, ranking=ranking)
    as_of = _utc_ms(2026, 8, 25, 12)
    deterministic = _snapshot(factory, ids, query="policy")
    neutral = service.retrieve_for_space(_space_request(ids, "policy", as_of_ms=as_of))
    neutral_again = service.retrieve_for_space(
        _space_request(ids, "policy", as_of_ms=as_of)
    )
    assert neutral.ranked_documents == neutral_again.ranked_documents
    assert [
        (row.item_id, row.revision_id, row.rank, row.score_ppm)
        for row in neutral.ranked_documents
    ] == [
        (row.item_id, row.revision_id, row.rank, row.score_ppm)
        for row in deterministic.ranked_documents
    ]
    assert neutral.ranking_envelope is not None
    assert neutral.ranking_envelope.temporal_intent == "none"
    current = service.retrieve_for_space(
        _space_request(ids, "policy latest", as_of_ms=as_of)
    )
    assert current.ranking_envelope is not None
    assert current.ranking_envelope.temporal_intent == "current"
    assert current.ranked_documents[0].item_id == values["current"][0]
    assert current.ranked_documents[0].temporal_metadata is not None
    assert current.ranked_documents[0].temporal_metadata.verification_current is True
    scheduled = service.retrieve_for_space(
        _space_request(ids, "policy scheduled", as_of_ms=as_of)
    )
    assert scheduled.ranked_documents[0].item_id == values["future"][0]
    historical = service.retrieve_for_space(
        _space_request(ids, "policy 2020", as_of_ms=as_of)
    )
    assert historical.ranked_documents[0].item_id == values["old"][0]

    implicit = service.retrieve_for_space(_space_request(ids, "policy scheduled"))
    replay = service.retrieve_for_space(_space_request(ids, "policy scheduled"))
    assert implicit.ranking_envelope is not None
    assert replay.ranking_envelope is not None and replay.ranking_envelope.cache_hit
    boundary = implicit.ranking_envelope.next_effective_boundary_ms
    assert boundary == _utc_ms(2026, 8, 25, 15)
    clock[0] = boundary + 1
    after = service.retrieve_for_space(_space_request(ids, "policy scheduled"))
    assert after.ranking_envelope is not None
    assert after.ranking_envelope.cache_hit is False
    assert after.ranking_envelope.as_of_ms == boundary + 1


@pytest.mark.scenario("TIME-021")
def test_sbe_5_2_temporal_provenance_never_changes_content_dedupe(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    duplicate_body = "# temporalduplicate shared body\n"
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            first = _insert_item(
                connection,
                label="temporal-duplicate-a",
                space_id=ids["part-space-a"],
                actor="admin",
                title="Temporalduplicate A",
                body=duplicate_body,
                published=True,
            )
            second = _insert_item(
                connection,
                label="temporal-duplicate-b",
                space_id=ids["part-space-a"],
                actor="admin",
                title="Temporalduplicate B",
                body=duplicate_body,
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
    _enable_fixture_part_writes(factory, ids)
    temporal = TemporalService(factory, ScopeResolver(_write_flags()))
    _assert_effective(
        temporal,
        ids,
        item_id=first[0],
        revision_id=first[1],
        date_value="2020-01-01",
        label="duplicate-old",
    )
    _assert_effective(
        temporal,
        ids,
        item_id=second[0],
        revision_id=second[1],
        date_value="2026-08-24",
        label="duplicate-new",
    )
    snapshot = _snapshot(factory, ids, query="temporalduplicate")
    matching = [
        row
        for row in snapshot.reranker_candidates
        if row.normalized_content_sha256 == sha256(duplicate_body.encode()).hexdigest()
    ]
    assert len(matching) == 1
    identities = {
        matching[0].item_id,
        *(row.item_id for row in matching[0].alternate_identities),
    }
    assert identities == {first[0], second[0]}


@pytest.mark.scenario("RANK-020", "TIME-022")
def test_sbe_5_2_newer_conflict_is_retained_without_any_mutation(tmp_path) -> None:
    factory = _factory(tmp_path)
    ids = _seed(factory)
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            conflict_item, conflict_revision = _insert_item(
                connection,
                label="newer-conflicting-temporal-fact",
                space_id=ids["part-space-a"],
                actor="admin",
                title="Newer conflicting temporal fact",
                body="# newer conflicting temporal fact\n",
                published=True,
            )
            connection.execute(
                """INSERT INTO knowledge_edge(
                     id,space_id,source_item_id,target_item_id,edge_type,edge_status,
                     origin,source_evidence_revision_id,target_evidence_revision_id,
                     approval_finding_id,evidence_json,needs_revalidation,created_by,
                     updated_by,created_at,updated_at,row_version
                   ) VALUES (?, ?, ?, ?, 'contradicts','active','manual',NULL,NULL,NULL,
                             '{}',0,'admin','admin',900,900,1)""",
                (
                    str(uuid4()),
                    ids["part-space-a"],
                    ids["part-hit"],
                    conflict_item,
                ),
            )
            connection.execute(
                """UPDATE knowledge_space SET generation=generation+1,
                          updated_at=updated_at+1,row_version=row_version+1
                    WHERE id=?""",
                (ids["part-space-a"],),
            )
    finally:
        connection.close()
    _enable_fixture_part_writes(factory, ids)
    temporal = TemporalService(factory, ScopeResolver(_write_flags()))
    _assert_effective(
        temporal,
        ids,
        item_id=conflict_item,
        revision_id=conflict_revision,
        date_value="2026-08-24",
        label="newer-conflict",
    )
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            before = {
                table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in (
                    "knowledge_item",
                    "knowledge_revision",
                    "knowledge_edge",
                    "audit_event",
                    "outbox_event",
                    "operation_dedup",
                )
            }
    finally:
        connection.close()
    ranking = HybridRankingService(
        factory,
        local_cross_encoder=False,
        temporal_ranking=True,
        clock_ms=lambda: _utc_ms(2026, 8, 25, 12),
    )
    result = _retrieval_service(factory, ranking=ranking).retrieve_for_space(
        _space_request(ids, "scopealpha latest", as_of_ms=_utc_ms(2026, 8, 25, 12))
    )
    assert {ids["part-hit"], conflict_item} <= {
        row.item_id for row in result.ranked_documents
    }
    conflict = next(
        row for row in result.ranked_documents if row.item_id == conflict_item
    )
    assert conflict.relation_semantics == "conflicting_evidence"
    assert conflict.temporal_metadata is not None
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            after = {
                table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in before
            }
    finally:
        connection.close()
    assert after == before
