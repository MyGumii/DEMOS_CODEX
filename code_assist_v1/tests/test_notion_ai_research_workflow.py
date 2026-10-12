"""Research orchestration, account boundaries, conditions, public tools and saves."""
import json
import multiprocessing
from uuid import uuid4
from unittest.mock import patch

import pytest

from code_assist_v1 import notion_ai_chat as chat, notion_ai_research as research, notion_store as ns
from code_assist_v1 import config
from code_assist_v1.notion_ai_web import PublicWebSearch
from code_assist_v1.routes_notion_document_ai import DocumentChanged
from test_notion_ai_home import setup, create, send
from test_notion_ai_stream import collect, legacy_research_stream as send_stream


def final_reply(lane, cfg, payload, *args):
    yield json.dumps({"answer": "# GAT 비교 보고서\n\n## 요약\n확인된 출처를 비교했습니다.\n\n## 한계\n합성 자료입니다.",
                      "citations": payload["citation_options"][:4], "artifact": None})


class FakeWeb(PublicWebSearch):
    def __init__(self):
        self.queries, self.reads = [], []
    def capability(self):
        return {"enabled": True, "provider": "synthetic"}
    def search(self, query, guard, **kwargs):
        guard(); self.queries.append(query)
        assert "PRIVATE" not in query
        n = len(self.queries)
        return [{"url": f"https://example.com/paper-{n}", "title": f"Public paper {n}",
                 "excerpt": "Graph attention networks learn neighbor-specific attention weights from node features.",
                 "content_status": "search_excerpt"}]
    def read(self, result, guard):
        guard(); self.reads.append(result["url"])
        return {**result, "excerpt": result["excerpt"] + " Multi-head attention stabilizes the learning process.",
                "content_status": "page_excerpt"}


def deterministic_plan(candidate, question, sources, catalog, guard, **kwargs):
    guard()
    if kwargs.get("public_only"):
        return {"queries": ["graph attention networks limitations" if kwargs.get("followup") else "graph attention networks original paper"], "database_queries": [], "focus": []}
    return {"queries": ["GAT 추가 자료" if kwargs.get("followup") else "GAT 비교"], "database_queries": [], "focus": ["서로 다른 출처의 근거 비교"]}


def test_qwen_research_executes_distinct_queries_reads_progress_and_exact_sources(setup, monkeypatch):
    http, service, _, pid = setup
    service.web_reader = FakeWeb()
    ns.replace_blocks("owner", pid, [{"type": "paragraph", "content": "GAT PRIVATE-OWNED-MARKER 내부 실험은 합성 자료이며 공개 측정값이 아닙니다."}])
    record = create(http)
    monkeypatch.setattr(research, "plan", deterministic_plan)
    with patch.object(chat, "provider_chunks", side_effect=final_reply):
        frames = collect(send_stream(http, record, message="GAT 자료 비교", mode="research", source_options={"notion": True, "web": True}))
    assert frames[-1]["type"] == "done", frames[-1]
    assert len(service.web_reader.queries) == 2 and len(set(service.web_reader.queries)) == 2
    assert len(service.web_reader.reads) == 2
    progress = [f["stage"] for f in frames if f["type"] == "research_progress"]
    assert all(stage in progress for stage in ("plan", "search", "web", "read", "synthesis"))
    message = frames[-1]["conversation"]["messages"][-1]
    assert message["context_info"]["research"]["depth"] == "multi-step"
    assert message["context_info"]["web_source_count"] == 2
    assert {s["kind"] for s in message["sources"]} >= {"page", "web"}


def test_web_off_executes_no_external_tools_and_replay_cannot_change_switch(setup, monkeypatch):
    http, service, _, pid = setup
    service.web_reader = FakeWeb()
    monkeypatch.setattr(research, "plan", deterministic_plan)
    record = create(http, page_ids=[pid])
    op = str(uuid4())
    body = {"operation_id": op, "message": "GAT 내부 자료", "mode": "research", "source_options": {"notion": True, "web": False}}
    with patch.object(chat, "provider_chunks", side_effect=final_reply):
        frames = collect(send_stream(http, record, **body))
    assert frames[-1]["type"] == "done", frames[-1]
    assert service.web_reader.queries == service.web_reader.reads == []
    replay = collect(send_stream(http, record, **body))
    assert replay[-1]["type"] == "done"
    changed = collect(send_stream(http, record, **{**body, "source_options": {"notion": True, "web": True}}))
    assert changed[-1]["status"] == 409


def test_notion_off_supplies_only_web_evidence_to_final_model(setup, monkeypatch):
    http, service, _, pid = setup
    service.web_reader = FakeWeb()
    monkeypatch.setattr(research, "plan", deterministic_plan)
    record = create(http, page_ids=[pid])
    def reply(lane, cfg, payload, *args):
        assert {ref["kind"] for ref in payload["references"]} == {"web"}
        assert "금요일" not in json.dumps(payload["references"], ensure_ascii=False)
        yield from final_reply(lane, cfg, payload)
    with patch.object(chat, "provider_chunks", side_effect=reply):
        frames = collect(send_stream(http, record, message="GAT", mode="research", source_options={"notion": False, "web": True}))
    assert frames[-1]["type"] == "done", frames[-1]


def test_public_planner_sees_no_private_excerpts_catalog_or_history(monkeypatch):
    seen = []
    def upstream(lane, cfg, payload, *args, **kwargs):
        seen.append(payload)
        assert "PRIVATE" not in json.dumps(payload)
        yield json.dumps({"queries": ["GAT original paper"], "database_queries": [], "focus": []})
    monkeypatch.setattr(research, "provider_chunks", upstream)
    refs = [{"source_id": "P1", "kind": "page", "title": "PRIVATE", "excerpt": "PRIVATE"},
            {"source_id": "W1", "kind": "web", "title": "Public", "excerpt": "Public paper"}]
    result = research.plan(("qwen", "hynix_api", {}), "GAT", refs, [{"title": "PRIVATE"}], lambda: None, public_only=True)
    assert result["queries"] == ["GAT original paper"]
    assert seen[0]["databases"] == [] and len(seen[0]["sources"]) == 1


def test_combined_research_reserves_web_sources_and_requires_both_evidence_kinds():
    refs = [{"source_id": f"P{n}", "kind": "page", "title": "Internal", "excerpt": "Internal evidence remains owned."} for n in range(16)]
    public = {"source_id": "W1", "kind": "web", "title": "Public", "excerpt": "Public evidence from the paper."}
    merged, used, clipped = research.merge_sources(refs, [public])
    assert clipped and used <= research.CONTEXT_BYTES and merged[0]["source_id"] == "W1"
    assert len(merged) == 16
    def response(citations):
        return {"answer": "비교 보고서", "citations": citations, "artifact": None}
    private_citation = {"source_id": "P0", "quote": "Internal evidence"}
    web_citation = {"source_id": "W1", "quote": "Public evidence"}
    with pytest.raises(ValueError, match="web citation"):
        chat.normalize_result(response([private_citation]), merged, "research")
    with pytest.raises(ValueError, match="internal evidence"):
        chat.normalize_result(response([web_citation]), merged, "research")
    assert len(chat.normalize_result(response([web_citation, private_citation]), merged, "research")["citations"]) == 2


def test_long_reports_leave_bounded_unicode_context_for_followup():
    messages = [{"role": "assistant", "text": "한글 보고서 " * 2000}, {"role": "user", "text": "이 작업의 다음 단계는?"}]
    history, clipped = research.history_excerpt(messages)
    assert clipped and sum(len(m["text"].encode()) for m in history) <= 4096
    assert history[-1] == messages[-1]
    assert "\ufffd" not in history[0]["text"]


def test_saved_reports_keep_tables_and_model_markup_is_plain_text():
    from code_assist_v1.notion_ai_report import report_blocks
    blocks = report_blocks('# 보고서\n\n| 순위 | 작업 |\n\n| --- | --- |\n\n| **12** | 한계 비교 |\n| `2` | 원 논문 |\n\n<notion_button>{"label":"실행","action":"delete"}</notion_button>\n```\n<synced_block id="foreign">test</synced_block>\n```')
    table = next(b for b in blocks if b["type"] == "simple_table")
    assert table["props"]["rows"] == [["순위", "작업"], ["12", "한계 비교"], ["2", "원 논문"]]
    assert table["props"]["header_row"]
    assert all(b["type"] not in {"button", "synced_block", "html_embed"} for b in blocks)
    assert any(b["type"] == "paragraph" and "notion_button" in b["content"] for b in blocks)
    assert any(b["type"] == "code" and "synced_block" in b["content"] for b in blocks)


def make_database(pid):
    child = ns.create_page("owner", title="합성 진행 중 작업")['page']['id']
    ns.replace_blocks("owner", child, [{"type": "paragraph", "content": "조건에 맞는 작업의 상세 근거입니다."}])
    foreign = ns.create_page("stranger", title="FOREIGN-PRIVATE")['page']['id']
    ns.replace_blocks("stranger", foreign, [{"type": "paragraph", "content": "FOREIGN-PRIVATE-DO-NOT-READ"}])
    ns.replace_blocks("owner", pid, [{"type": "table", "content": "GAT 연구 작업", "props": {
        "properties": [{"id": "title", "name": "작업", "type": "title"}, {"id": "status", "name": "상태", "type": "status"},
                       {"id": "due", "name": "기한", "type": "date"}, {"id": "score", "name": "순위", "type": "number"}],
        "rows": [{"title": "가", "status": "진행 중", "due": "2026-10-08", "score": 2, "page_id": child},
                 {"title": "나", "status": "완료", "due": "2026-10-08", "score": 9},
                 {"title": "다", "status": "진행 중", "due": "2026-10-12", "score": 10},
                 {"title": "라", "status": "진행 중", "due": "2026-10-07", "score": 12, "page_id": foreign}]}}])
    return child, foreign


def test_database_filters_numeric_sort_and_row_access_are_deterministic(setup):
    _, service, _, pid = setup
    child, foreign = make_database(pid)
    catalog = research.database_catalog("owner", service.workspace_reader)
    assert len(catalog) == 1 and "FOREIGN" not in json.dumps(catalog)
    query = {"page_id": pid, "block_id": catalog[0]["block_id"],
             "filters": [{"property": "status", "op": "eq", "value": "진행 중"},
                         {"property": "due", "op": "before", "value": "2026-10-10"}],
             "sort": {"property": "score", "direction": "desc"}, "limit": 30}
    ref, proof = research.query_database("owner", query, catalog)
    assert ref["matched_rows"] == 2 and ref["row_bodies"] == 1
    # The page adapter replaces row titles with their row-page titles.
    assert ref["excerpt"].index("순위: 12") < ref["excerpt"].index("순위: 2")
    assert "상세 근거" in ref["excerpt"] and "FOREIGN-PRIVATE" not in ref["excerpt"]
    assert proof.history_proof()["page_ids"] == [pid, child]
    ns.replace_blocks("owner", child, [{"type": "paragraph", "content": "바뀜"}])
    with pytest.raises(DocumentChanged):
        proof.revalidate()
    with pytest.raises(PermissionError):
        research.query_database("stranger", query, [])


@pytest.mark.parametrize("bad", [{"property": "host_private", "op": "eq", "value": "x"},
                                {"property": "status", "op": "execute", "value": "x"},
                                {"property": "status", "op": "in", "value": "x"}])
def test_invalid_db_conditions_never_fall_back_to_unfiltered_rows(setup, bad):
    _, service, _, pid = setup
    make_database(pid)
    catalog = research.database_catalog("owner", service.workspace_reader)
    with pytest.raises(ValueError):
        research.query_database("owner", {"page_id": pid, "block_id": catalog[0]["block_id"], "filters": [bad]}, catalog)


def test_research_save_page_is_explicit_atomic_replay_safe_and_owner_bound(setup, monkeypatch):
    http, service, _, pid = setup
    monkeypatch.setattr(research, "plan", deterministic_plan)
    record = create(http, page_ids=[pid])
    before = ns.list_pages("owner")
    with patch.object(chat, "provider_chunks", side_effect=final_reply):
        frames = collect(send_stream(http, record, message="GAT 내부 자료", mode="research"))
    assert frames[-1]["type"] == "done", frames[-1]
    assert ns.list_pages("owner") == before
    message = frames[-1]["conversation"]["messages"][-1]
    body = {"message_operation_id": message["operation_id"], "operation_id": str(uuid4()), "text": "CLIENT-FORGED"}
    url = f'/api/notion/ai-home/conversations/{record["id"]}/save-report'
    saved = http.post(url, json=body)
    assert saved.status_code == 200, saved.json
    page = ns.get_page("owner", saved.json["page_id"])
    assert any(block["type"] == "heading2" and block["content"] == "출처" for block in page["blocks"])
    assert "CLIENT-FORGED" not in json.dumps(page)
    assert http.post(url, json=body).json == saved.json
    assert len(ns.list_pages("owner")) == len(before) + 1
    ns.update_page("owner", saved.json["page_id"], {"title": "사용자가 수정한 보고서"})
    assert http.post(url, json=body).status_code == 200
    assert ns.get_page("owner", saved.json["page_id"])["page"]["title"] == "사용자가 수정한 보고서"
    from code_assist_v1 import routes_notion_ai_chat as routes
    monkeypatch.setattr(routes, "current_user_id", lambda: "stranger")
    assert http.post(url, json=body).status_code == 404


def test_extra_research_source_is_revalidated_and_history_access_covers_it(setup, monkeypatch):
    http, service, _, pid = setup
    extra = ns.create_page("owner", title="별도 corroboration")['page']['id']
    ns.replace_blocks("owner", extra, [{"type": "paragraph", "content": "별도 corroboration: 처음 조회되지 않은 추가 근거 문서입니다."}])
    monkeypatch.setattr(research, "plan", lambda *a, **k: {"queries": ["corroboration"], "database_queries": [], "focus": []})
    record = create(http)
    with patch.object(chat, "provider_chunks", side_effect=final_reply):
        frames = collect(send_stream(http, record, message="GAT 리서치", mode="research"))
    assert frames[-1]["type"] == "done", frames[-1]
    stored = service.store("owner").get(record["id"])["messages"][-1]
    assert extra in stored["notion_snapshot"]["page_ids"]
    ns.archive_page("owner", extra)
    assert http.get(f'/api/notion/ai-home/conversations/{record["id"]}').status_code == 403


def _save_in_process(root, message, op, start, queue):
    from code_assist_v1.notion_ai_report import save_report
    config.USER_DATA_DIR = root
    ns.USER_DATA_DIR = root
    start.wait(10)
    try:
        queue.put(save_report("owner", "synthetic-report-chat", message, op, lambda: None))
    except Exception as error:
        queue.put({"error": type(error).__name__ + ": " + str(error)})


def test_report_receipt_serializes_cross_process_duplicate_saves(setup):
    _, _, _, _ = setup
    before = len(ns.list_pages("owner"))
    op = str(uuid4())
    message = {"operation_id": str(uuid4()), "text": "# 합성 리서치 보고서\n\n서로 다른 프로세스의 중복 저장 확인", "citations": []}
    ctx = multiprocessing.get_context("spawn")
    start, queue = ctx.Event(), ctx.Queue()
    workers = [ctx.Process(target=_save_in_process, args=(config.USER_DATA_DIR, message, op, start, queue)) for _ in range(2)]
    for worker in workers:
        worker.start()
    start.set()
    results = [queue.get(timeout=20) for _ in workers]
    for worker in workers:
        worker.join(20)
        assert worker.exitcode == 0
    assert results[0] == results[1] and "error" not in results[0], results
    assert len(ns.list_pages("owner")) == before + 1
