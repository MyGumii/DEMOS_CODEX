"""Real isolated context composition: many small bodies must not hide I/O cost."""
from __future__ import annotations

from dataclasses import replace
import json
from time import perf_counter
import tracemalloc

import pytest

from code_assist_v1.knowledge_core.agent_execution import CanonicalAgentTurnExecutor, _turn_payload
from code_assist_v1.knowledge_core.answer_citations import prepare_citations
from code_assist_v1.knowledge_core.answer_input import (
    answer_messages, encode_input, restore_source_documents,
)
from code_assist_v1.knowledge_core.db import (
    ConnectionFactory, ManagedConnection, TransactionMode, transaction,
)
from code_assist_v1.knowledge_core.repositories.hybrid_retrieval import HybridRetrievalRepository
from code_assist_v1.knowledge_core.repositories.context_bodies import (
    ContextBodyMetadata, ContextBodyUnavailableError,
)
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.services.agent_context import AgentTurnContextService
from code_assist_v1.tests.knowledge_spaces.unit.test_canonical_retrieval import (
    _accepted_turn, _context_request, _factory, _flags, _insert_item, _seed,
)

pytestmark = [pytest.mark.unit, pytest.mark.scenario("RANK-009", "RANK-012")]


@pytest.mark.parametrize("count", (8, 128, 1024))
def test_many_small_bodies_preserve_complete_evidence_and_measure_io(
    tmp_path, monkeypatch, record_property, count,
):
    factory = _factory(tmp_path)
    ids = _seed(factory)
    expected = {}
    connection = factory.open()
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            for index in range(count):
                # Keep each body unique: retrieval deliberately collapses exact
                # duplicate bodies, which would not measure many-body loading.
                body = f"가{index}"
                item, revision = _insert_item(
                    connection, label=f"tiny-scale-{index}", space_id=ids["part-space-a"],
                    actor="admin", title=f"scopealpha {index}", body=body, published=True,
                )
                expected[item] = (revision, body)
    finally:
        connection.close()
    request = _context_request(ids, _accepted_turn(factory, ids))
    service = AgentTurnContextService(
        factory, scope_resolver=ScopeResolver(_flags()), retrieval=HybridRetrievalRepository(),
    )
    counters = {"database_opens": 0, "statements": 0, "inline_body_reads": 0}
    original_open = ConnectionFactory.open
    original_execute = ManagedConnection.execute

    def counted_open(owner, *args, **kwargs):
        if owner is factory:
            counters["database_opens"] += 1
        return original_open(owner, *args, **kwargs)

    def counted_execute(owner, sql, parameters=()):
        if owner.database_path == factory.database_path:
            counters["statements"] += 1
            if " ".join(sql.split()).startswith("SELECT content_md FROM knowledge_revision_body"):
                counters["inline_body_reads"] += 1
        return original_execute(owner, sql, parameters)

    monkeypatch.setattr(ConnectionFactory, "open", counted_open)
    monkeypatch.setattr(ManagedConnection, "execute", counted_execute)
    tracemalloc.start()
    started = perf_counter()
    try:
        bundle = service.compose(request)
        payload = _turn_payload(bundle)
        seconds = perf_counter() - started
        _, python_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    actual = {row.item_id: row for row in bundle.ranked_contexts}
    assert expected.keys() <= actual.keys()
    for item, (revision, body) in expected.items():
        assert actual[item].revision_id == revision
        assert actual[item].excerpt_md == body
        assert not actual[item].truncated
    assert ids["foreign-hit"] not in actual
    assert ids["personal-hit"] not in actual
    assert counters["inline_body_reads"] == len(bundle.ranked_contexts)
    # Before this optimization, 8/128 new bodies caused 15/135 opens. Context
    # authority snapshots remain, but inline bodies share one local handle.
    assert counters["database_opens"] == 6
    assert bundle.context_budget.body_omitted_count == 0
    assert bundle.context_budget.excerpt_bytes <= 98_304
    # Inspect what the executor really gives its provider boundary, not merely
    # _turn_payload before citation preparation and provenance factoring.
    model_calls = []

    def model_runner(task, model_payload, **kwargs):
        model_calls.append((model_payload, kwargs["messages"]))
        assert json.loads(kwargs["messages"][1]["content"]) == model_payload
        return "검증용 응답: 실제 모델 품질 판정이 아님.", {}

    completion = CanonicalAgentTurnExecutor(runner=model_runner).complete(bundle)
    model_payload, messages = model_calls[0]
    before_factoring, _ = prepare_citations(payload, turn_id=bundle.turn_id,
                                           selected_view_space_id=bundle.space_id)
    assert restore_source_documents(model_payload) == before_factoring
    assert model_payload["retrieval_candidates"] == before_factoring["retrieval_candidates"]
    assert len(model_calls) == 1
    assert completion.final_response["model_input_trace"]["messages_bytes"] == len(encode_input(messages))
    before_messages = answer_messages(before_factoring, system=messages[0]["content"])
    if count >= 128:
        assert len(encode_input(messages)) < len(encode_input(before_messages))
    measurements = dict(
        **counters, fixture_documents=count, delivered_documents=len(actual),
        seconds=round(seconds, 6), python_traced_peak_bytes=python_peak,
        payload_bytes=len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
        excerpt_bytes=bundle.context_budget.excerpt_bytes,
        model_payload_before_bytes=len(encode_input(before_factoring)),
        model_payload_after_bytes=len(encode_input(model_payload)),
        logical_messages_before_bytes=len(encode_input(before_messages)),
        logical_messages_after_bytes=len(encode_input(messages)),
    )
    record_property("measurements", json.dumps(measurements, sort_keys=True))
    # Latency/tracemalloc are diagnostic, not target-server acceptance thresholds.


def _reader_fixture(tmp_path):
    factory = _factory(tmp_path)
    ids = _seed(factory)
    connection = factory.open()
    records = []
    try:
        with transaction(connection, TransactionMode.IMMEDIATE):
            for blob_backed in (False, True):
                body = f"검증할 원문 {blob_backed}"
                item, revision = _insert_item(
                    connection, label=f"reader-{blob_backed}", space_id=ids["part-space-a"],
                    actor="admin", title="scopealpha", body=body, published=True,
                    blob_backed=blob_backed,
                )
                row = connection.execute(
                    "SELECT content_size_bytes,content_sha256,content_blob_hash "
                    "FROM knowledge_revision_body WHERE revision_id=?", (revision,),
                ).fetchone()
                records.append((ContextBodyMetadata(
                    item, revision, ids["part-space-a"], row["content_size_bytes"],
                    row["content_sha256"], row["content_blob_hash"],
                ), body))
    finally:
        connection.close()
    return factory, records


def _observe_reader_connections(factory, monkeypatch):
    opened, closed = [], []
    original_open, original_close = ConnectionFactory.open, ManagedConnection.close

    def open_connection(owner, *args, **kwargs):
        if owner is factory:
            assert kwargs["read_only"] is True
        connection = original_open(owner, *args, **kwargs)
        if owner is factory:
            opened.append(connection)
        return connection

    def close_connection(connection):
        if connection in opened:
            assert not connection.in_transaction
            closed.append(connection)
        return original_close(connection)

    monkeypatch.setattr(ConnectionFactory, "open", open_connection)
    monkeypatch.setattr(ManagedConnection, "close", close_connection)
    return opened, closed


@pytest.mark.parametrize("mode", (
    "empty", "blob_only", "mixed", "blob_first", "blob_failure", "inline_failure", "consumer_failure",
))
def test_reader_is_lazy_transaction_free_for_blob_and_closes_on_every_exit(tmp_path, monkeypatch, mode):
    factory, ((inline, inline_text), (blob, blob_text)) = _reader_fixture(tmp_path)
    opened, closed = _observe_reader_connections(factory, monkeypatch)

    def loader(digest, size):
        assert not any(connection.in_transaction for connection in opened)
        assert digest == blob.content_sha256
        assert size == len(blob_text.encode())
        if mode == "blob_failure":
            raise OSError("fixture CAS failure")
        return blob_text.encode()

    service = AgentTurnContextService(
        factory, scope_resolver=ScopeResolver(_flags()), retrieval=HybridRetrievalRepository(),
        content_loader=loader,
    )

    def consume():
        with service._context_body_reader() as read:
            assert not opened
            if mode == "empty":
                return
            if mode == "blob_first":
                assert read(blob) == blob_text
                assert not opened
            if mode != "blob_only":
                assert read(inline) == inline_text
                assert len(opened) == 1
                assert not opened[0].in_transaction
            assert read(blob) == blob_text
            if mode == "consumer_failure":
                raise ValueError("fixture passage-selection failure")
            if mode == "inline_failure":
                read(replace(inline, content_sha256="0" * 64))
            elif mode == "mixed":
                assert read(inline) == inline_text
                assert len(opened) == 1
                assert not opened[0].in_transaction

    if mode.endswith("failure"):
        with pytest.raises(ValueError if mode == "consumer_failure" else ContextBodyUnavailableError):
            consume()
    else:
        consume()
    assert len(opened) == (0 if mode in {"empty", "blob_only"} else 1)
    assert closed == opened


def test_same_service_interleaved_readers_do_not_share_connection_or_lifetime(tmp_path, monkeypatch):
    factory, ((inline, text), _) = _reader_fixture(tmp_path)
    opened, closed = _observe_reader_connections(factory, monkeypatch)
    service = AgentTurnContextService(
        factory, scope_resolver=ScopeResolver(_flags()), retrieval=HybridRetrievalRepository(),
    )
    with service._context_body_reader() as first:
        assert first(inline) == text
        with service._context_body_reader() as second:
            assert second(inline) == text
            assert len(opened) == 2
            assert opened[0] is not opened[1]
            assert not any(connection.in_transaction for connection in opened)
        assert closed == [opened[1]]
        assert first(inline) == text
    assert closed == [opened[1], opened[0]]
