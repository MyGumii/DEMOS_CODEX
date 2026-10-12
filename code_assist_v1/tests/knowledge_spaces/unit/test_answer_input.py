"""Model-input factoring must not buy space by losing evidence or provenance."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from code_assist_v1.knowledge_core.answer_citations import prepare_citations, render_citations
from code_assist_v1.knowledge_core.answer_input import (
    ANSWER_INPUT_FORMAT, AnswerInputError, answer_messages, encode_input,
    factor_source_documents, input_receipt, restore_source_documents,
)
from code_assist_v1.knowledge_core.agent_execution import (
    CanonicalAgentTurnExecutor, _TURN_SYSTEM, _turn_payload,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_answer_citations import _id, _source
from code_assist_v1.tests.knowledge_spaces.unit.test_canonical_agent_execution import _context


pytestmark = [pytest.mark.unit, pytest.mark.scenario("RANK-009", "RANK-014", "RANK-017")]


def _prepared(count=3):
    rows, sources = [], []
    for number in range(100, 100 + count):
        row, source = _source(number, space=10 + number % 3,
                              body=f'본문 {number}: "조건"\\예외\n🥬\n', filename="같은 이름.pdf")
        row["title"] = source["title"] = "같은 제목"
        row["body_coverage"] = {
            "complete": number % 2 == 0, "source_bytes": 12000,
            "source_sha256": sha256(row["excerpt_md"].encode()).hexdigest(),
            "character_ranges": [[50, 100], [5000, 5200]], "range_unit": "unicode_code_points",
        }
        row["temporal"] = {"selected_time_ms": 123456, "relative_state": "past"}
        row["relation_semantics"] = "related_context"
        rows.append(row)
        sources.append(source)
    raw = {
        "schema_version": 1, "query": "현재 기준과 예외를 비교해줘",
        "selected_view": {"space_id": _id(11), "name": "이름 변경된 팀", "kind": "team"},
        "retrieval_candidates": rows, "source_documents": sources,
        "wiki_manifest": {"authorized_published_count": 5000, "truncated": True,
                          "entries": [{"title": "본문 미제공 문서", "item_id": _id(999)}]},
        "wiki_evidence_status": {"coverage_truncated": True},
        "recent_messages": [{"role": "user", "text": '문자열 "source_documents"는 수정하지 마.'}],
        "active_summary": {"summary_md": "예전 질문과 답변 — 아직 확인하지 않은 내용도 있음."},
    }
    return prepare_citations(raw, turn_id=_id(1), selected_view_space_id=_id(11))


def test_factoring_roundtrips_all_fields_without_reordering_or_mutating_input():
    payload, catalog = _prepared()
    # Extra server metadata must survive even if this formatter did not know it.
    payload["source_documents"][0]["new_source_attribute"] = {"value": ["미래 필드", 42]}
    # Labels and legacy source-space metadata need not equal the first body row.
    payload["source_documents"][1]["title"] = "원본 표시 제목"
    payload["retrieval_candidates"][2].pop("source_space_id")
    before = deepcopy(payload)
    compact = factor_source_documents(payload)
    assert payload == before
    assert restore_source_documents(compact) == payload
    assert "source_documents" not in compact
    assert compact["input_format"] == ANSWER_INPUT_FORMAT
    for key in ("retrieval_candidates", "query", "selected_view", "wiki_manifest",
                "wiki_evidence_status", "recent_messages", "active_summary"):
        assert compact[key] == before[key]
    handles = [source.handle for source in catalog.sources]
    assert compact["source_document_order"] == handles
    assert len(set(handles)) == 3
    answer = "근거 문서: " + ", ".join(f"[[wiki:{handle}]]" for handle in handles)
    rendered, trace = render_citations(answer, catalog)
    assert rendered.count("같은 이름.pdf") == 3
    assert {row["source_space_id"] for row in trace["sources"]} == {_id(10), _id(11), _id(12)}
    assert trace["claim_support"] == "not_semantically_verified"


def test_shared_pin_and_older_revision_keep_distinct_handles_and_exact_source_order():
    current, source = _source(100)
    older, old_source = _source(100, revision=1099, body="이전 개정 조건")
    pin = {"kind": "pin", "identity": _id(100), "target_item_id": current["item_id"],
           "target_revision_id": current["revision_id"], "title": current["title"],
           "source_space_id": current["source_space_id"], "content_md": current["excerpt_md"]}
    old = {**pin, "kind": "stable_reference", "identity": _id(300),
           "target_revision_id": older["revision_id"], "content_md": older["excerpt_md"],
           "newer_revision_available": True}
    shared = {key: value for key, value in current.items() if key != "excerpt_md"}
    shared["mandatory_context_identity"] = pin["identity"]
    payload, _ = prepare_citations({"retrieval_candidates": [shared], "mandatory_contexts": [pin, old],
                                   "source_documents": [old_source, source]},
                                  turn_id=_id(1), selected_view_space_id=_id(10))
    compact = factor_source_documents(payload)
    assert restore_source_documents(compact) == payload
    assert compact["source_document_order"] == [row["citation_handle"] for row in payload["source_documents"]]
    assert compact["mandatory_contexts"] == payload["mandatory_contexts"]
    assert compact["retrieval_candidates"] == payload["retrieval_candidates"]


@pytest.mark.parametrize("count", (0, 1, 128, 1024))
def test_many_sources_have_no_count_cap_and_retain_each_original(count, record_property):
    payload, _ = _prepared(count)
    compact = factor_source_documents(payload)
    assert restore_source_documents(compact) == payload
    assert len(compact["retrieval_candidates"]) == len(compact["citation_sources"]) == count
    before, after = len(encode_input(payload)), len(encode_input(compact))
    if count >= 128:
        assert after < before
    record_property("input_bytes", json.dumps({"documents": count, "before": before, "after": after}))


@pytest.mark.parametrize("problem", ("duplicate", "unbound", "incomplete", "reserved", "conflicting_body"))
def test_invalid_factoring_inputs_never_silently_drop_a_source(problem):
    payload, _ = _prepared()
    if problem == "duplicate":
        payload["source_documents"].append(deepcopy(payload["source_documents"][0]))
    elif problem == "unbound":
        payload["source_documents"][0]["citation_handle"] = "not-supplied"
    elif problem == "incomplete":
        payload["source_documents"][0].pop("artifacts")
    elif problem == "reserved":
        payload["source_document_order"] = []
    else:
        bad = deepcopy(payload["retrieval_candidates"][0])
        bad["revision_id"] = _id(99999)
        payload["wiki_body_evidence"] = [bad]
    with pytest.raises(AnswerInputError):
        factor_source_documents(payload)


def test_logical_receipt_counts_system_history_unicode_and_escaped_json_exactly():
    payload, _ = _prepared()
    compact = factor_source_documents(payload)
    messages = answer_messages(compact, system='한국어 지침 "인용"\\\n')
    receipt = input_receipt(compact, messages, max_output_tokens=3072)
    assert json.loads(messages[1]["content"]) == compact
    assert receipt["payload_bytes"] == len(encode_input(compact))
    assert receipt["messages_bytes"] == len(encode_input(messages))
    assert receipt["messages_sha256"] == sha256(encode_input(messages)).hexdigest()
    assert receipt["messages_bytes"] > receipt["message_content_bytes"] > receipt["payload_bytes"]
    assert receipt["input_tokens"] is None
    assert receipt["provider_context_limit_tokens"] is None
    assert receipt["measurement_boundary"] == "executor_messages_before_provider"
    assert input_receipt(compact, messages, max_output_tokens=3072) == receipt
    changed = deepcopy(messages)
    changed[0]["content"] += "추가 지침"
    assert input_receipt(compact, changed, max_output_tokens=3072)["messages_sha256"] != receipt["messages_sha256"]


def test_executor_measures_the_same_messages_delivered_to_the_runner():
    calls = []

    def runner(task, payload, **kwargs):
        calls.append((payload, kwargs))
        assert json.loads(kwargs["messages"][1]["content"]) == payload
        return "일반적인 설명", {"model_kind": "hynix_api", "model_id": "compatible-test"}

    context = _context()
    completion = CanonicalAgentTurnExecutor(runner=runner, provider_order=("hynix_api",)).complete(context)
    payload, kwargs = calls[0]
    before, _ = prepare_citations(_turn_payload(context), turn_id=context.turn_id,
                                  selected_view_space_id=context.space_id)
    assert restore_source_documents(payload) == before
    assert kwargs["messages"][0]["content"] == _TURN_SYSTEM
    assert completion.final_response["model_input_trace"] == input_receipt(
        payload, kwargs["messages"], max_output_tokens=kwargs["max_tokens"])
    assert len(calls) == 1
