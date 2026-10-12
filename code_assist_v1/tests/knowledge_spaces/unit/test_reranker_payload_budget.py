from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, uuid5

import pytest

from code_assist_v1.knowledge_core.evidence_passages import (
    allocate_fair_evidence_bytes, evidence_text_bytes, select_evidence_passages,
)
from code_assist_v1.knowledge_core.repositories.retrieval import RankedDocument
from code_assist_v1.knowledge_core.services import hybrid_reranking as ranking_module
from code_assist_v1.knowledge_core.services.hybrid_reranking import (
    LocalCrossEncoderAdapter, MODEL_INPUT_CAP_BYTES, _model_input,
)

pytestmark = [pytest.mark.unit, pytest.mark.scenario("RANK-015", "RANK-016", "RANK-017")]


def _doc(index: int, text: str, *, title: str = "검증 자료") -> RankedDocument:
    identity = lambda suffix: str(uuid5(NAMESPACE_URL, f"payload-budget:{index}:{suffix}"))
    return RankedDocument(
        item_id=identity("item"), revision_id=identity("revision"),
        source_space_id=identity("space"), selected_view_space_id=identity("view"),
        title=title, excerpt_md=text, score_ppm=500_000, rank=index + 1,
        provenance=("fts",), graph_hop=0, relation_type=None,
        relation_semantics="direct_match", origin="native", inherited=False,
        overlay_grant_id=None, normalized_content_sha256=sha256(text.encode()).hexdigest(),
    )


def _serialized(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _runner(calls):
    def runner(task, payload, **kwargs):
        calls.append(payload)
        assert len(_serialized(payload)) <= ranking_module.MODEL_INPUT_CAP_BYTES
        assert task == "canonical_local_cross_encoder"
        return json.dumps({"schema_version": 1, "grades": [
            {"item_id": row["item_id"], "grade": 2} for row in payload["candidates"]
        ]}), {"model_kind": "codex", "model_id": "budget-test-only"}
    return runner


@pytest.mark.parametrize("sizes,budget,expected", [
    ((1, 100, 100), 101, (1, 50, 50)),
    ((100, 1, 100), 102, (51, 1, 50)),
    ((0, 2, 4), 100, (0, 2, 4)),
    ((100, 100, 100), 2, (1, 1, 0)),
    ((), 15, ()),
])
def test_fair_allocation_saturates_short_sources(sizes, budget, expected):
    assert allocate_fair_evidence_bytes(sizes, budget) == expected


@pytest.mark.parametrize("budget", [0, 1, 2, 3, 4, 5, 7, 100, 4095, 4096, 10000])
def test_json_escaped_passages_keep_exact_unicode_ranges_and_bound(budget):
    text = ('한국어😀"\\\n\r\t\b\f\x01\x00와 e\u0301 조합\n' * 350
            + "복구예외는 마지막 구간에 존재한다.")
    result = select_evidence_passages(text, budget, query="복구", json_escaped=True)
    assert evidence_text_bytes(result.text, json_escaped=True) <= budget
    assert result.text == "\n\n".join(text[start:end] for start, end in result.ranges)
    assert result.source_bytes == len(text.encode())
    assert result.source_sha256 == sha256(text.encode()).hexdigest()
    assert json.loads(json.dumps(result.text, ensure_ascii=False)) == result.text


def test_whole_payload_fairly_shares_all_candidates_and_preserves_expanded_late_facts():
    query = "비상 상황에 어떻게 대응하지?"
    short = '간단한 자료: "표기"와 역슬래시\\.\n'
    late = "Recovery protocol: isolate controller and restore verified snapshot."
    text = '배경자료 😀 "인용" \\ \n\t\x01\n' * 300 + late
    docs = (_doc(0, short), *(_doc(i, text) for i in range(1, 25)))
    before = tuple(docs)
    payload, digest, size = _model_input(query, docs, evidence_query="recovery protocol controller snapshot")
    rows = payload["candidates"]
    assert size == len(_serialized(payload)) <= MODEL_INPUT_CAP_BYTES
    assert digest == sha256(_serialized(payload)).hexdigest()
    assert payload["query"] == query
    assert [row["item_id"] for row in rows] == [row.item_id for row in docs]
    assert rows[0]["excerpt_md"] == short
    assert not rows[0]["preview_shortened"]
    assert all(row["preview_shortened"] for row in rows[1:])
    assert all(late in row["excerpt_md"] for row in rows[1:])
    used = [evidence_text_bytes(row["excerpt_md"], json_escaped=True) for row in rows[1:]]
    assert max(used) - min(used) <= 6  # one escaped/code-point boundary
    assert docs == before
    assert _model_input(query, docs, evidence_query="recovery protocol controller snapshot")[1:] == (digest, size)
    calls = []
    adapter = LocalCrossEncoderAdapter(runner=_runner(calls))
    first = adapter.grade(query, docs, evidence_query="recovery protocol controller snapshot")
    assert first.applied and len(first.grades) == len(docs)
    assert adapter.grade(query, docs, evidence_query="recovery protocol controller snapshot") == first
    assert len(calls) == 1


@pytest.mark.parametrize("size", [131071, 131072, 131073])
def test_exact_wire_boundary_preserves_ids_and_never_calls_oversized_metadata(size):
    # Metadata alone cannot be clipped. Empty body makes the exact wire length
    # independent of passage clipping and exercises the adapter's <= boundary.
    doc = _doc(0, "", title="")
    base = _model_input("query", (doc,))[2]
    doc = replace(doc, title="x" * (size - base))
    payload, _, actual = _model_input("query", (doc,))
    assert actual == size
    assert len(payload["candidates"]) == 1
    calls = []
    outcome = LocalCrossEncoderAdapter(runner=_runner(calls)).grade("query", (doc,))
    assert outcome.applied == (size <= MODEL_INPUT_CAP_BYTES)
    assert len(calls) == int(size <= MODEL_INPUT_CAP_BYTES)
    if size > MODEL_INPUT_CAP_BYTES:
        assert outcome.reason == "input_bound"


def test_metadata_fits_but_tiny_allowance_cannot_supply_korean_evidence(monkeypatch):
    docs = (_doc(0, "한국어"), _doc(1, "😀"))
    empty = tuple(replace(row, excerpt_md="") for row in docs)
    base = _model_input("query", empty)[2]
    monkeypatch.setattr(ranking_module, "MODEL_INPUT_CAP_BYTES", base + 2)
    payload, _, size = _model_input("query", docs)
    assert size <= base + 2
    assert [row["item_id"] for row in payload["candidates"]] == [row.item_id for row in docs]
    calls = []
    result = LocalCrossEncoderAdapter(runner=_runner(calls)).grade("query", docs)
    assert not result.applied and result.reason == "input_bound" and not calls


def test_many_identities_are_retained_even_when_metadata_alone_exceeds_budget():
    docs = tuple(_doc(i, "자료", title='긴 제목 "\\\n' * 40) for i in range(500))
    payload, _, size = _model_input("query", docs)
    assert size > MODEL_INPUT_CAP_BYTES
    assert [row["item_id"] for row in payload["candidates"]] == [row.item_id for row in docs]
    calls = []
    result = LocalCrossEncoderAdapter(runner=_runner(calls)).grade("query", docs)
    assert not result.applied and result.reason == "input_bound" and not calls
