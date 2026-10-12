"""Public search and verified citations across output modes, without private leakage."""
import json
from unittest.mock import patch

import pytest

from code_assist_v1 import notion_ai_chat as chat, notion_ai_research as research
from code_assist_v1 import notion_ai_chat_artifacts as artifacts
from code_assist_v1.routes_notion_document_ai import DocumentChanged
from test_notion_ai_home import setup, create, ARTIFACTS
from test_notion_ai_stream import collect, send_stream, legacy_research_stream
from test_notion_ai_research_workflow import FakeWeb, deterministic_plan


PUBLIC = {"source_id": "W1", "kind": "web", "title": "Graph Attention Networks",
    "url": "https://example.com/gat", "excerpt": "Graph attention networks learn neighbor-specific attention weights from node features."}
PRIVATE = {"source_id": "P1", "kind": "page", "title": "PRIVATE memo",
    "url": "/page/private", "excerpt": "PRIVATE-ONLY experimental details must not leave the account."}


def chunks(text):
    yield text


@pytest.mark.parametrize("mode", ["chat", "slides", "spreadsheet", "research", "visualization"])
def test_all_modes_web_on_plan_public_query_use_evidence_and_save_citations(setup, monkeypatch, mode):
    http, service, _, pid = setup
    service.web_reader = FakeWeb()
    calls = []
    def plan(*args, **kwargs):
        calls.append(kwargs)
        return deterministic_plan(*args, **kwargs)
    monkeypatch.setattr(research, "plan", plan)
    monkeypatch.setattr(research, "select_public_results", lambda candidate, question, results, guard, **kwargs: results)
    record = create(http, page_ids=[pid])
    def reply(lane, cfg, payload, *args):
        web_ids = {s["source_id"] for s in payload["references"] if s["kind"] == "web"}
        assert web_ids
        if mode == "chat":
            option = next(c for c in payload["numbered_citation_options"] if c["source_id"] in web_ids)
            yield "이웃의 중요도를 학습합니다."
            yield f'[{option["number"]}]'
        else:
            chosen = [next(c for c in payload["citation_options"] if c["source_id"] in web_ids)]
            if mode == "research":
                chosen.append(next(c for c in payload["citation_options"] if c["source_id"] not in web_ids))
            yield json.dumps({"answer": "웹 원문에서 이웃별 가중치를 확인했습니다.[1]",
                "citations": chosen, "artifact": ARTIFACTS.get(mode)})
    sender = legacy_research_stream if mode == "research" else send_stream
    with patch.object(chat, "provider_chunks", side_effect=reply):
        frames = collect(sender(http, record, message="Graph Attention Network에 대해 설명해봐.",
            mode=mode, source_options={"notion": True, "web": True}))
    assert frames[-1]["type"] == "done", frames[-1]
    message = frames[-1]["conversation"]["messages"][-1]
    assert all("에 대해 설명" not in query for query in service.web_reader.queries)
    assert any(call.get("public_only") and call["task_mode"] == mode for call in calls)
    assert message["context_info"]["web_search_details"]["status"] == "completed"
    assert message["citations"][0]["url"].startswith("https://")
    if mode == "chat":
        assert [f["text"] for f in frames if f["type"] == "delta"] == ["이웃의 중요도를 학습합니다.", "[1]"]


@pytest.mark.parametrize("mode", ["chat", "slides", "spreadsheet", "research", "visualization"])
def test_web_off_never_plans_public_queries_or_calls_search(setup, monkeypatch, mode):
    http, service, _, pid = setup
    service.web_reader = FakeWeb()
    def plan(*args, **kwargs):
        assert not kwargs.get("public_only")
        return deterministic_plan(*args, **kwargs)
    monkeypatch.setattr(research, "plan", plan)
    record = create(http, page_ids=[pid])
    def reply(lane, cfg, payload, *args):
        assert not any(s["kind"] == "web" for s in payload["references"])
        if mode == "chat":
            yield "내 자료 내용입니다."
        else:
            yield json.dumps({"answer": "내 자료 내용", "citations": payload.get("citation_options", [])[:1],
                "artifact": ARTIFACTS.get(mode)})
    sender = legacy_research_stream if mode == "research" else send_stream
    with patch.object(chat, "provider_chunks", side_effect=reply):
        frames = collect(sender(http, record, mode=mode, source_options={"notion": True, "web": False}))
    assert frames[-1]["type"] == "done", frames[-1]
    assert service.web_reader.queries == service.web_reader.reads == []


def test_web_on_but_unnecessary_does_not_search_or_require_citations(setup, monkeypatch):
    http, service, _, _ = setup
    service.web_reader = FakeWeb()
    monkeypatch.setattr(research, "plan", lambda *a, **k: {"queries": [], "database_queries": [], "focus": []})
    with patch.object(chat, "provider_chunks", side_effect=lambda *a: chunks("안녕하세요!")):
        frames = collect(send_stream(http, create(http), message="안녕", source_options={"notion": False, "web": True}))
    assert frames[-1]["type"] == "done", frames[-1]
    assert service.web_reader.queries == []
    assert frames[-1]["conversation"]["messages"][-1]["context_info"]["web_search_details"]["status"] == "not_needed"


@pytest.mark.parametrize("reason", ["irrelevant", "search_failed", "review_failed"])
def test_unusable_web_results_disclose_failure_and_never_become_evidence(setup, monkeypatch, reason):
    http, service, _, pid = setup
    service.web_reader = FakeWeb()
    monkeypatch.setattr(research, "plan", deterministic_plan)
    def select(*args, **kwargs):
        if reason == "review_failed":
            raise ValueError("invalid model result")
        return []
    monkeypatch.setattr(research, "select_public_results", select)
    if reason == "search_failed":
        monkeypatch.setattr(service.web_reader, "search", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    def reply(lane, cfg, payload, *args):
        assert not any(s["kind"] == "web" for s in payload["references"])
        assert payload["context_info"]["limitations"]
        yield "이번 검색에서는 웹 근거를 확보하지 못했습니다."
    with patch.object(chat, "provider_chunks", side_effect=reply):
        frames = collect(send_stream(http, create(http, page_ids=[pid]), source_options={"notion": True, "web": True}))
    assert frames[-1]["type"] == "done", frames[-1]
    message = frames[-1]["conversation"]["messages"][-1]
    assert message["context_info"]["web_source_count"] == 0
    assert message["context_info"]["web_search_details"]["status"] == ("failed" if reason == "search_failed" else "no_results")
    assert service.web_reader.reads == [] and message["citations"] == []


def test_public_relevance_check_receives_only_returned_public_titles_and_snippets(monkeypatch):
    results = [{"title": "Original GAT paper", "excerpt": PUBLIC["excerpt"], "url": PUBLIC["url"]},
        {"title": "Longest graph circuits", "excerpt": "Pure graph theory about circuits", "url": "https://example.com/circuit"}]
    def upstream(lane, cfg, payload, *args, **kwargs):
        assert "PRIVATE" not in json.dumps(payload)
        assert set(payload) == {"request", "search_query", "results"}
        assert "url" not in payload["results"][0]
        yield '{"indices":[0]}'
    monkeypatch.setattr(research, "provider_chunks", upstream)
    assert research.select_public_results(("qwen", "hynix_api", {}), "GAT", results, lambda: None) == results[:1]


@pytest.mark.parametrize("indices", [[99], [-1], [True], [0, 0], ["https://evil.example"]])
def test_relevance_model_cannot_invent_or_duplicate_candidates(monkeypatch, indices):
    monkeypatch.setattr(research, "provider_chunks", lambda *a, **k: chunks(json.dumps({"indices": indices})))
    with pytest.raises(ValueError):
        research.select_public_results(("qwen", "hynix_api", {}), "GAT", [PUBLIC], lambda: None)


def test_chat_numbered_citations_compact_repeated_numbers_and_ignore_code_math():
    refs = [PRIVATE, PUBLIC]
    number = next(c["number"] for c in artifacts.numbered_citation_options(refs) if c["source_id"] == "W1")
    answer = chat.normalize_answer(f"원문 근거[{number}]와 같은 근거[{number}]. `x[99]` $x[98]$", refs, "chat")
    assert answer["answer"] == "원문 근거[1]와 같은 근거[1]. `x[99]` $x[98]$"
    assert len(answer["citations"]) == 1 and answer["citations"][0]["url"] == PUBLIC["url"]
    with pytest.raises(ValueError, match="unsupported chat citation"):
        chat.normalize_answer("잘못된 인용[99]", refs, "chat")
    with pytest.raises(ValueError, match="web citation"):
        chat.normalize_answer("웹 인용 없이 답변", refs, "chat")


def test_web_citation_choices_use_body_evidence_not_titles_and_navigation():
    ref = {**PUBLIC, "excerpt": "Skip to main content\nGraph Attention Networks\n"
        "Authors: Public authors of a scientific paper\nAbstract: " + PUBLIC["excerpt"]}
    choices = artifacts.citation_options([ref])
    assert choices == [{"source_id": "W1", "quote": PUBLIC["excerpt"]}]
    assert choices[0]["quote"] in ref["excerpt"]
    assert artifacts.citation_options([{**PUBLIC, "excerpt": PUBLIC["title"]}]) == []


@pytest.mark.parametrize("query,expected", [
    ("Graph Attention Networks original paper title authors year", '"Graph Attention Networks"'),
    ("GATv2 original paper title authors year difference from GAT", '"GATv2"'),
    ('"GATv2" paper title year', '"GATv2"'),
    ('"Exact Publication Name" original paper', '"Exact Publication Name"'),
    ('"How Attentive are Graph Attention Networks?"', '"How Attentive are Graph Attention Networks?"'),
    ("graph learning benchmarks 2026", "graph learning benchmarks 2026"),
])
def test_public_query_preserves_subject_and_dates_removes_lookup_padding(query, expected):
    assert research.public_query(query) == expected


@pytest.mark.parametrize("mode", ["slides", "spreadsheet", "visualization"])
def test_artifact_grammar_allows_verified_web_citations_and_requires_them(mode):
    schema = artifacts.response_schema(mode, [PUBLIC])
    assert schema["properties"]["citations"]["minItems"] == 1
    assert artifacts.response_schema(mode, [PRIVATE])["properties"]["citations"]["maxItems"] == 0
    with pytest.raises(ValueError, match="web citation"):
        chat.normalize_result({"answer": "설명", "citations": [], "artifact": ARTIFACTS[mode]}, [PUBLIC], mode)


def test_revoked_context_during_public_relevance_still_aborts_not_falls_back(setup, monkeypatch):
    http, service, _, pid = setup
    service.web_reader = FakeWeb()
    monkeypatch.setattr(research, "plan", deterministic_plan)
    def select(candidate, question, results, guard, **kwargs):
        from code_assist_v1 import notion_store as ns
        ns.replace_blocks("owner", pid, [{"type": "paragraph", "content": "Changed during relevance"}])
        guard()
    monkeypatch.setattr(research, "select_public_results", select)
    frames = collect(send_stream(http, create(http, page_ids=[pid]), source_options={"notion": True, "web": True}))
    assert frames[-1]["type"] == "error" and frames[-1]["status"] == 409
