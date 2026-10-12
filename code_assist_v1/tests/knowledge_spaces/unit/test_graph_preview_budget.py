from __future__ import annotations

from dataclasses import replace
import ctypes
import gc
from hashlib import sha256
import json
import os
import threading
from time import perf_counter
import tracemalloc
from uuid import uuid4

import pytest

from code_assist_v1.knowledge_core.db import ManagedConnection, TransactionMode, transaction
from code_assist_v1.knowledge_core.agent_execution import _turn_payload
from code_assist_v1.knowledge_core.agent_runtime import compose_canonical_agent_runtime
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.repositories import hybrid_retrieval as graph_module
from code_assist_v1.knowledge_core.repositories.retrieval import RetrievalIntegrityError
from code_assist_v1.knowledge_core.services.hybrid_reranking import (
    HybridRankingService, LocalCrossEncoderAdapter,
)
from code_assist_v1.knowledge_core.services import hybrid_reranking as ranking_module
from code_assist_v1.knowledge_core.services.agent_context import AgentTurnContextService
from code_assist_v1.knowledge_core.services.conversations import (
    AcceptConversationTurnCommand, ConversationService,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_canonical_retrieval import (
    _factory, _insert_item, _seed, _flags, _TurnBindingResolver, _context_request,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_hybrid_reranking import _snapshot

pytestmark = [pytest.mark.unit, pytest.mark.scenario("RANK-005", "RANK-006", "RANK-015", "RANK-017")]


def _edge(connection, space, source, target):
    connection.execute("""INSERT INTO knowledge_edge(
        id,space_id,source_item_id,target_item_id,edge_type,edge_status,
        origin,source_evidence_revision_id,target_evidence_revision_id,
        approval_finding_id,evidence_json,needs_revalidation,created_by,
        updated_by,created_at,updated_at,row_version
        ) VALUES (?,?,?,?,'supports','active','manual',NULL,NULL,NULL,
                  '{}',0,'admin','admin',450,450,1)""",
        (str(uuid4()), space, source, target))


def _fixture(tmp_path, *, blob_backed):
    factory = _factory(tmp_path)
    ids = _seed(factory)
    text_a = "Administrative context.\n" * 900 + "복구절차는 센서를 격리하고 스냅샷을 검증한다."
    text_b = "Independent operating context.\n" * 900 + "복구예외는 구형 장치에 적용한다."
    records = {}
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            for label, title, text in (
                ("seed-a", "budgettoken A", "# budgettoken\nEntry A."),
                ("seed-b", "budgettoken B", "# budgettoken\nEntry B."),
                ("neighbor-a", "Operating notes A", text_a),
                ("duplicate-a", "Operating notes A copy", text_a),
                ("neighbor-b", "Operating notes B", text_b),
            ):
                item, revision = _insert_item(connection, label=f"preview-work-{label}",
                    space_id=ids["part-space-a"], actor="admin", title=title, body=text,
                    published=True, blob_backed=blob_backed)
                records[label] = (item, revision)
            for source, target in (
                ("seed-a", "neighbor-a"), ("seed-b", "neighbor-a"),
                ("seed-b", "duplicate-a"), ("seed-a", "seed-b"),
                ("neighbor-a", "neighbor-b"), ("duplicate-a", "neighbor-b"),
            ):
                _edge(connection, ids["part-space-a"], records[source][0], records[target][0])
            for label in ("foreign-a", "foreign-b"):
                records[label] = _insert_item(connection, label=f"preview-work-{label}",
                    space_id=ids["part-space-b"], actor="other", title="budgettoken foreign",
                    body=f"# budgettoken FOREIGN {label}\n", published=True, blob_backed=blob_backed)
            _edge(connection, ids["part-space-b"], records["foreign-a"][0], records["foreign-b"][0])
    finally:
        connection.close()
    return factory, ids, records, (text_a, text_b)


def _spy(monkeypatch):
    reads, passages = [], []
    execute = ManagedConnection.execute
    selector = graph_module.select_evidence_passages

    def traced(self, sql, *args, **kwargs):
        if "AS preview_source" in sql:
            reads.append(json.loads(args[0]["identities"]))
        # The graph discovery query must not select or compare complete bodies.
        if "WITH seeds(seed_id,seed_rank)" in sql:
            assert "d.content_md" not in sql and "body.content_md" not in sql
        return execute(self, sql, *args, **kwargs)

    def select(text, budget, **kwargs):
        passages.append((sha256(text.encode()).hexdigest(), len(text.encode())))
        return selector(text, budget, **kwargs)

    monkeypatch.setattr(ManagedConnection, "execute", traced)
    monkeypatch.setattr(graph_module, "select_evidence_passages", select)
    return reads, passages


@pytest.mark.parametrize("blob_backed", [False, True])
def test_graph_reads_once_after_both_hops_and_exact_body_dedupe(tmp_path, monkeypatch, blob_backed):
    factory, ids, records, texts = _fixture(tmp_path, blob_backed=blob_backed)
    reads, passages = _spy(monkeypatch)
    started = perf_counter()
    result = _snapshot(factory, ids, query="budgettoken 복구")
    elapsed = perf_counter() - started
    assert result.hybrid_explain.two_hop_executed
    assert result.graph_preview_work.source_bodies_read == 2
    assert result.graph_preview_work.source_bytes == sum(len(text.encode()) for text in texts)
    assert result.graph_preview_work.preview_reuses >= 4
    assert result.graph_preview_work.deferred_bodies == 0
    assert result.reranker_evidence_complete
    assert len(reads) == 1 and len(reads[0]) == 2
    assert len(passages) == 2
    assert {digest for digest, _ in passages} == {sha256(text.encode()).hexdigest() for text in texts}
    read_ids = {row["item_id"] for row in reads[0]}
    assert records["seed-a"][0] not in read_ids and records["seed-b"][0] not in read_ids
    assert records["foreign-a"][0] not in read_ids and records["foreign-b"][0] not in read_ids
    assert len(result.reranker_candidates) == 4
    assert any(row.alternate_identities for row in result.reranker_candidates)
    assert {row.graph_hop for row in result.reranker_candidates} == {0, 1, 2}
    print(f"graph_preview_read_ms={elapsed * 1000:.3f}; bodies=2; bytes={result.graph_preview_work.source_bytes}")


@pytest.mark.parametrize("blob_backed", [False, True])
@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_graph_budget_boundary_plans_all_identities_before_any_read(tmp_path, monkeypatch, blob_backed, delta):
    factory, ids, records, texts = _fixture(tmp_path, blob_backed=blob_backed)
    baseline = _snapshot(factory, ids, query="budgettoken 복구")
    total = sum(len(text.encode()) for text in texts)
    monkeypatch.setattr(graph_module, "_GRAPH_SOURCE_BUDGET_BYTES", total + delta)
    reads, passages = _spy(monkeypatch)
    result = _snapshot(factory, ids, query="budgettoken 복구")
    # Only previews may differ across work budgets, never ranking semantics.
    assert tuple(replace(row, excerpt_md="") for row in result.reranker_candidates) == tuple(
        replace(row, excerpt_md="") for row in baseline.reranker_candidates)
    if delta < 0:
        assert not result.reranker_evidence_complete
        assert result.graph_preview_work.source_bytes == 0
        assert result.graph_preview_work.source_bodies_read == 0
        assert result.graph_preview_work.deferred_bodies == 2
        assert not reads and not passages
    else:
        assert result.reranker_evidence_complete
        assert result.graph_preview_work.source_bytes == total
        assert len(reads) == 1 and len(passages) == 2


@pytest.mark.parametrize("temporal_enabled", [False, True])
def test_source_bound_skips_entire_grader_with_exact_temporal_fallback(tmp_path, monkeypatch, temporal_enabled):
    factory, ids, _, _ = _fixture(tmp_path, blob_backed=True)
    monkeypatch.setattr(graph_module, "_GRAPH_SOURCE_BUDGET_BYTES", 1)
    reads, passages = _spy(monkeypatch)
    snapshot = _snapshot(factory, ids, query="budgettoken 복구")
    assert not reads and not passages
    calls = []
    adapter = LocalCrossEncoderAdapter(runner=lambda *args, **kwargs: calls.append(args))
    kwargs = dict(temporal_ranking=temporal_enabled, clock_ms=lambda: 1_800_000_000_000)
    baseline = (
        HybridRankingService(factory, local_cross_encoder=False, **kwargs).process(
            snapshot, query="budgettoken 복구", as_of_ms=1_800_000_000_000)
        if temporal_enabled else snapshot
    )
    ranking = HybridRankingService(factory, local_cross_encoder=True, adapter=adapter, **kwargs)
    result = ranking.process(snapshot, query="budgettoken 복구", as_of_ms=1_800_000_000_000)
    assert result.ranking_envelope.reranker_status == "fallback"
    assert result.ranking_envelope.reranker_reason == "input_bound"
    assert result.ranked_documents == baseline.ranked_documents
    assert result.supplemental_documents == baseline.supplemental_documents
    assert not calls
    assert ranking.process(snapshot, query="budgettoken 복구", as_of_ms=1_800_000_000_000).ranking_envelope.cache_hit
    # An otherwise identical complete-evidence snapshot must not reuse fallback.
    assert not ranking.process(replace(snapshot, reranker_evidence_complete=True),
        query="budgettoken 복구", as_of_ms=1_800_000_000_000).ranking_envelope.cache_hit
    assert len(calls) == 1


def test_blob_search_projection_hash_is_verified_before_preview_delivery(tmp_path):
    factory, ids, records, _ = _fixture(tmp_path, blob_backed=True)
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            for label in ("neighbor-a", "duplicate-a", "neighbor-b"):
                connection.execute("UPDATE search_document SET content_md=? WHERE item_id=?",
                    ("Corrupted projection without evidence.", records[label][0]))
    finally:
        connection.close()
    with pytest.raises(RetrievalIntegrityError, match="canonical integrity"):
        _snapshot(factory, ids, query="budgettoken 복구")


@pytest.mark.parametrize("blob_backed", [False, True])
@pytest.mark.parametrize("prepare", [False, True])
def test_graph_work_fallback_does_not_remove_final_answer_bodies(tmp_path, monkeypatch, blob_backed, prepare):
    factory, ids, records, texts = _fixture(tmp_path, blob_backed=blob_backed)
    monkeypatch.setattr(graph_module, "_GRAPH_SOURCE_BUDGET_BYTES", 1)
    turn = ConversationService(factory).accept_turn(AcceptConversationTurnCommand(
        actor_user_id="admin", actor_scope_epoch=int(ids["admin-epoch"]),
        operation_id=str(uuid4()), request_id=str(uuid4()),
        conversation_id=ids["conversation-part-a"], user_content={"text": "budgettoken 복구"},
    ))
    calls, loads = [], []
    raw_by_hash = {sha256(text.encode()).hexdigest(): text.encode() for text in (
        *texts, "# budgettoken\nEntry A.", "# budgettoken\nEntry B.",
    )}

    def loader(digest, size):
        loads.append(digest)
        assert len(raw_by_hash[digest]) == size
        return raw_by_hash[digest]

    postprocessor = HybridRankingService(factory, local_cross_encoder=True, temporal_ranking=False,
        adapter=LocalCrossEncoderAdapter(runner=lambda *args, **kwargs: calls.append(args)))
    context = AgentTurnContextService(factory, scope_resolver=ScopeResolver(_flags()),
        binding_resolver=_TurnBindingResolver(factory), retrieval=graph_module.HybridRetrievalRepository(
            prepare_reranker_evidence=prepare),
        postprocessor=postprocessor if prepare else None, content_loader=loader if blob_backed else None,
    ).compose(_context_request(ids, turn))
    if prepare:
        assert context.retrieval.ranking_envelope.reranker_reason == "input_bound"
    else:
        assert context.retrieval.ranking_envelope is None
    assert context.retrieval.graph_preview_work.preparation_enabled == prepare
    assert context.retrieval.graph_preview_work.source_bodies_read == 0
    assert not calls
    payload = _turn_payload(context)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "복구절차는 센서를 격리하고 스냅샷을 검증한다." in serialized
    assert "복구예외는 구형 장치에 적용한다." in serialized
    assert all(records[label][0] not in serialized for label in ("foreign-a", "foreign-b"))
    assert all(any(row.excerpt_md == text for row in context.ranked_contexts) for text in texts)
    if blob_backed:
        assert len(loads) == len(set(loads)) == 4


def test_runtime_disabled_reranker_avoids_graph_body_preparation_and_payload_allocator(tmp_path, monkeypatch):
    factory, ids, _, _ = _fixture(tmp_path, blob_backed=True)
    enabled = _snapshot(factory, ids, query="budgettoken 복구")
    runtime = compose_canonical_agent_runtime(factory,
        flags=replace(_flags(), hybrid_rerank=True, local_cross_encoder=False, temporal_ranking=True),
        principal_resolver=lambda: None)
    repository = runtime.services.retrieval._retrieval
    reads, passages = _spy(monkeypatch)
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            snapshot = repository.retrieve_closure(connection, selected_space_id=ids["part-space-a"],
                source_space_ids=(ids["part-space-a"],), overlays=(), corpus_closure_sha256="d" * 64,
                query="budgettoken 복구")
    finally:
        connection.close()
    assert not reads and not passages
    assert not snapshot.graph_preview_work.preparation_enabled
    assert snapshot.graph_preview_work.source_bodies_read == 0
    assert snapshot.cache_identity_sha256 != enabled.cache_identity_sha256
    assert tuple(replace(row, excerpt_md="") for row in snapshot.reranker_candidates) == tuple(
        replace(row, excerpt_md="") for row in enabled.reranker_candidates)

    def forbidden(*args, **kwargs):
        raise AssertionError("Disabled grader must not allocate model previews")

    monkeypatch.setattr(ranking_module, "_model_input", forbidden)
    result = runtime.services.hybrid_ranking.process(snapshot, query="budgettoken 복구")
    assert result.ranking_envelope.reranker_status == "disabled"
    # Temporal-only processing adds typed metadata without changing this
    # non-temporal query's deterministic scores/order or prepared evidence.
    assert all(row.temporal_metadata is not None for row in result.ranked_documents)
    assert tuple(replace(row, temporal_metadata=None) for row in result.ranked_documents) == snapshot.ranked_documents


def _windows_rss():
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage",
            )
        ]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_process = kernel.GetCurrentProcess
    get_process.restype = ctypes.c_void_p
    get_memory = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
    get_memory.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    get_memory.restype = ctypes.c_int
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not get_memory(get_process(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.WorkingSetSize


@pytest.mark.skipif(os.name != "nt", reason="Target Windows work/RSS measurement")
@pytest.mark.parametrize("body_size", [1024 * 1024, 8 * 1024 * 1024, 8 * 1024 * 1024 + 1])
def test_real_graph_source_budget_and_windows_resource_measurement(tmp_path, body_size):
    factory = _factory(tmp_path)
    ids = _seed(factory)
    line = "Unrelated archival text with neutral background.\n"
    body = (line * (body_size // len(line) + 1))[:body_size]
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            seed, _ = _insert_item(connection, label="resource-seed", space_id=ids["part-space-a"],
                actor="admin", title="sizebudget", body="# sizebudget\nEntry.", published=True)
            neighbor, _ = _insert_item(connection, label="resource-neighbor", space_id=ids["part-space-a"],
                actor="admin", title="Archive", body=body, published=True, blob_backed=True)
            _edge(connection, ids["part-space-a"], seed, neighbor)
    finally:
        connection.close()
    del body
    gc.collect()
    initial_rss = _windows_rss()
    samples = [initial_rss]
    stop = threading.Event()

    def sample():
        while not stop.wait(0.005):
            samples.append(_windows_rss())

    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    tracemalloc.start()
    started = perf_counter()
    try:
        result = _snapshot(factory, ids, query="sizebudget")
        elapsed = perf_counter() - started
        _, python_peak = tracemalloc.get_traced_memory()
        samples.append(_windows_rss())
    finally:
        tracemalloc.stop()
        stop.set()
        monitor.join(timeout=2)
    admitted = body_size <= graph_module._GRAPH_SOURCE_BUDGET_BYTES
    assert result.graph_preview_work.source_bytes == (body_size if admitted else 0)
    assert result.graph_preview_work.source_bodies_read == int(admitted)
    assert result.reranker_evidence_complete == admitted
    assert {row.item_id for row in result.reranker_candidates} == {seed, neighbor}
    print(json.dumps({
        "source_bytes": body_size, "admitted": admitted,
        "retrieval_ms_with_tracemalloc": round(elapsed * 1000, 3),
        "python_peak_allocated_bytes": python_peak,
        "initial_process_rss_bytes": initial_rss,
        "sampled_process_peak_rss_bytes": max(samples),
        "sampled_rss_increase_bytes": max(samples) - initial_rss,
    }, sort_keys=True))
