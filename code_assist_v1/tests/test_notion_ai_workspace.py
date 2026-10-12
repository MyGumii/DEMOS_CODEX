"""Personal Notion auto-context: real isolated HTTP routes and owner boundaries."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from code_assist_v1 import auth, internal_llm, notion_store as ns
from code_assist_v1.notion_ai_workspace import CONTEXT_BYTES
from code_assist_v1.routes_notion_document_ai import DocumentChanged
from code_assist_v1.tests.test_notion_ai_home import setup, create, send, model_reply, legacy_research_send
from code_assist_v1 import routes_notion_ai_chat as routes


def seed(uid, title, body):
    pid = ns.create_page(uid, title=title)["page"]["id"]
    ns.replace_blocks(uid, pid, [{"type": "paragraph", "content": body,
                                 "props": {"host_private_comparison": "NEVER SEND"}}])
    return pid


@pytest.mark.parametrize("uid", ["owner", "admin", "member"])
def test_http_each_account_automatically_reads_own_notion_only(setup, monkeypatch, uid):
    http, service, _, _ = setup
    mine = seed(uid, uid + "-OWN-NOTION", "ONLY-" + uid + "-7319")
    foreign = seed("other", "FOREIGN-PRIVATE-NOTION", "FOREIGN-SECRET-9021")
    ns.share_page("other", foreign, uid, role="view")  # Sharing is not personal ownership.
    monkeypatch.setattr(routes, "current_user_id", lambda: uid)
    record = create(http, user_id="other")
    with patch.object(internal_llm, "run_internal_text", side_effect=model_reply) as model:
        response = send(http, record, message="지금 페이지 어떤 내용들이 있지?", user_id="other")
    assert response.status_code == 200, response.json
    payload = model.call_args.args[1]
    encoded = json.dumps(payload)
    assert uid + "-OWN-NOTION" in encoded and "ONLY-" + uid + "-7319" in encoded
    assert "FOREIGN" not in encoded and "NEVER SEND" not in encoded
    answer = response.json["messages"][-1]
    info = answer["context_info"]["notion_workspace"]
    assert info["page_count"] == (2 if uid == "owner" else 1)
    assert any(source["id"] == mine for source in answer["sources"])
    assert "notion_snapshot" not in answer
    assert service.store(uid).get(record["id"])["messages"][-1]["notion_snapshot"]["owner_user_id"] == uid
    assert http.get("/api/notion/ai-home/capabilities").json["notion"]["page_count"] == info["page_count"]


@pytest.mark.parametrize("question", [
    "지금 페이지 어떤 내용들이 있지?", "내 문서 목록을 보여줘", "어떤 자료들이 있어?",
    "무슨 내용들이 정리되어있어?",
    "여기에는 뭐가 저장돼 있어?", "저장해둔 내용 알려줘", "현재 내용이 뭐가 있지?",
    "What pages do I have?", "List my documents", "What's here?",
])
def test_page_inventory_questions_without_notion_name_receive_real_evidence(setup, question):
    http, _, _, pid = setup
    record = create(http)
    with patch.object(internal_llm, "run_internal_text", side_effect=model_reply) as model:
        response = send(http, record, message=question)
    assert response.status_code == 200, response.json
    payload = model.call_args.args[1]
    assert payload["context_info"]["source_count"] > 0
    assert payload["context_info"]["notion_connection"]["enabled"] is True
    assert payload["context_info"]["notion_workspace"]["page_count"] == 1
    assert any(s["id"] == pid for s in payload["references"])


@pytest.mark.parametrize("question", ["2 더하기 3은 몇인가요?", "HTML 페이지를 만드는 방법을 설명해줘"])
def test_general_chat_keeps_connection_status_separate_from_references(setup, question):
    http, _, _, _ = setup
    record = create(http)
    with patch.object(internal_llm, "run_internal_text", side_effect=model_reply) as model:
        response = send(http, record, message=question)
    assert response.status_code == 200, response.json
    info = model.call_args.args[1]["context_info"]
    assert info["source_count"] == 0
    assert info["notion_connection"] == {"enabled": True, "mode": "personal-owned"}
    assert model.call_args.args[1]["references"] == []
    assert "does NOT mean permission was denied" in model.call_args.kwargs["system_prompt"]


def test_empty_account_page_question_has_zero_pages_not_zero_permission(setup, monkeypatch):
    http, service, _, _ = setup
    monkeypatch.setattr(routes, "current_user_id", lambda: "new-member")
    record = create(http)
    with patch.object(internal_llm, "run_internal_text", side_effect=model_reply) as model:
        response = send(http, record, message="지금 페이지 어떤 내용들이 있지?")
    assert response.status_code == 200, response.json
    info = model.call_args.args[1]["context_info"]
    assert info["source_count"] == 1 and info["notion_workspace"]["page_count"] == 0
    assert info["notion_connection"]["enabled"] is True
    assert not (Path(ns.USER_DATA_DIR) / "new-member" / "notion").exists()


@pytest.mark.parametrize("mode", ["chat", "research"])
def test_invalid_citation_is_corrected_once_on_same_model_with_same_scope(setup, mode):
    http, service, _, _ = setup
    record = create(http)
    payloads = []
    def reply(task, payload, **kwargs):
        payloads.append(payload)
        if len(payloads) == 1:
            return json.dumps({"answer": "REJECTED-ANSWER", "citations": [{"source_id": "fake", "quote": "fake"}], "artifact": None}), {}
        return model_reply(task, payload, **kwargs)
    with patch.object(internal_llm, "run_internal_text", side_effect=reply) as model:
        sender = legacy_research_send if mode == "research" else send
        response = sender(http, record, message="지금 페이지 어떤 내용들이 있지?", mode=mode)
    assert response.status_code == 200, response.json
    assert model.call_count == 2
    assert [call.kwargs["model_ids"] for call in model.call_args_list] == [["gemma4"], ["gemma4"]]
    assert payloads[1]["references"] == payloads[0]["references"]
    assert payloads[1]["response_contract_feedback"]["allowed_source_ids"] == [s["source_id"] for s in payloads[0]["references"]]
    assert "REJECTED-ANSWER" not in json.dumps(payloads[1])
    saved = service.store("owner").get(record["id"])
    assert saved["revision"] == 1 and len(saved["messages"]) == 2
    assert "REJECTED-ANSWER" not in str(saved["messages"])


def test_invalid_reply_retry_still_rejects_bad_citations_and_is_bounded(setup):
    http, service, _, _ = setup
    record = create(http)
    bad = json.dumps({"answer": "unsupported", "citations": [{"source_id": "fake", "quote": "fake"}], "artifact": None})
    with patch.object(internal_llm, "run_internal_text", return_value=(bad, {})) as model:
        response = send(http, record, message="지금 페이지 어떤 내용들이 있지?", model_id="gemma4")
    assert response.status_code == 503
    assert model.call_count == 2
    assert service.store("owner").get(record["id"])["messages"] == []


def test_page_revocation_after_invalid_reply_prevents_correction_attempt(setup):
    http, service, _, pid = setup
    record = create(http)
    def revoke(*args, **kwargs):
        ns.archive_page("owner", pid)
        return json.dumps({"answer": "unsupported", "citations": [{"source_id": "fake", "quote": "fake"}], "artifact": None}), {}
    with patch.object(internal_llm, "run_internal_text", side_effect=revoke) as model:
        response = send(http, record, message="지금 페이지 어떤 내용들이 있지?")
    assert response.status_code == 409
    assert model.call_count == 1
    assert service.store("owner").get(record["id"])["messages"] == []


def test_relevant_query_and_followup_without_repeating_notion(setup):
    http, _, _, _ = setup
    pid = seed("owner", "Project Quartz", "Quartz deployment date: 2031-04-17")
    record = create(http)
    with patch.object(internal_llm, "run_internal_text", side_effect=model_reply) as model:
        response = send(http, record, message="Quartz deployment date?")
        assert response.status_code == 200, response.json
        assert any(s["id"] == pid for s in model.call_args.args[1]["references"])
        response = send(http, response.json, message="그 내용을 자세히 설명해줘")
        assert response.status_code == 200, response.json
        assert any(s["id"] == pid for s in model.call_args.args[1]["references"])


def test_explicit_wiki_or_page_scope_does_not_auto_expand(setup):
    http, _, _, pid = setup
    seed("owner", "UNSELECTED-PERSONAL", "UNSELECTED-PERSONAL-SECRET")
    for fields in [{"page_ids": [pid]}, {"knowledge_space_id": "wiki1"}]:
        record = create(http, **fields)
        with patch.object(internal_llm, "run_internal_text", side_effect=model_reply) as model:
            response = send(http, record, message="내 notion 내용 알려줘")
        assert response.status_code == 200, response.json
        assert "UNSELECTED" not in str(model.call_args.args[1])
        assert "notion_workspace" not in response.json["messages"][-1]["context_info"]


def test_archived_pages_and_empty_account_are_not_leaked_or_bootstrapped(setup):
    _, service, _, pid = setup
    ns.archive_page("owner", pid)
    reader = service.workspace_reader
    before = set(Path(ns.USER_DATA_DIR).rglob("*"))
    snapshot = reader.snapshot("empty-account", "내 노션 목록")
    assert snapshot.info["page_count"] == 0
    assert snapshot.info["body_page_count"] == 0
    snapshot.revalidate()
    assert set(Path(ns.USER_DATA_DIR).rglob("*")) == before
    assert reader.snapshot("owner", "내 노션 목록").info["page_count"] == 0


def test_auto_source_change_or_archive_aborts_without_committing_answer(setup):
    http, service, _, pid = setup
    record = create(http)
    def change(*args, **kwargs):
        ns.replace_blocks("owner", pid, [{"type": "paragraph", "content": "changed after read"}])
        return model_reply(*args, **kwargs)
    with patch.object(internal_llm, "run_internal_text", side_effect=change):
        response = send(http, record, message="내 notion 내용")
    assert response.status_code == 409, response.json
    assert service.store("owner").get(record["id"])["messages"] == []
    with patch.object(internal_llm, "run_internal_text", side_effect=model_reply):
        response = send(http, record, message="내 notion 내용")
    assert response.status_code == 200, response.json
    ns.archive_page("owner", pid)
    assert http.get(f'/api/notion/ai-home/conversations/{record["id"]}').status_code == 403


def test_inventory_changes_and_forged_actor_are_revalidated(setup, monkeypatch):
    _, service, _, _ = setup
    snapshot = service.workspace_reader.snapshot("owner", "내 notion 내용")
    seed("owner", "New Page", "new fact")
    with pytest.raises(DocumentChanged):
        snapshot.revalidate()
    for uid in ["../owner", "owner/other", "", None]:
        with pytest.raises(PermissionError):
            service.workspace_reader.snapshot(uid, "notion")
    with service.app.test_request_context():
        monkeypatch.setattr(auth, "current_user_id", lambda: "owner")
        with pytest.raises(PermissionError):
            service.workspace_reader.snapshot("admin", "notion")


def test_bounded_unicode_context_and_relevant_text_beyond_prefix(setup):
    _, service, _, _ = setup
    seed("owner", "Long memo", "가" * 30000 + " QUARTZ7319 means Friday launch. " + "나" * 30000)
    for i in range(70):
        seed("owner", f"Extra {i}", "brief")
    snapshot = service.workspace_reader.snapshot("owner", "notion QUARTZ7319")
    assert sum(len(source["excerpt"].encode()) for source in snapshot.references) <= CONTEXT_BYTES
    assert "QUARTZ7319 means Friday launch" in str(snapshot.references)
    assert snapshot.info["truncated"] and snapshot.info["body_coverage_partial"]
    assert snapshot.info["listed_page_count"] < snapshot.info["page_count"]


def test_inline_cells_included_private_props_and_synced_contents_excluded(setup):
    _, service, _, pid = setup
    ns.replace_blocks("owner", pid, [
        {"type": "simple_table", "content": "", "props": {"rows": [["Task", "Friday"]], "host_private_comparison": "NEVER SEND"}},
        {"type": "table", "content": "Deliveries", "props": {"properties": [{"id": "status", "name": "Status", "type": "select"}],
             "rows": [{"status": "Shipped", "internal": "NEVER SEND"}, {"status": "ARCHIVED SECRET", "archived": True}]}},
        {"type": "synced_block", "content": "FOREIGN-SYNC-SECRET", "props": {"sync_id": "missing-source"}},
    ])
    snapshot = service.workspace_reader.snapshot("owner", "내 notion 내용")
    text = str(snapshot.references)
    assert "Friday" in text and "Shipped" in text
    assert "NEVER SEND" not in text and "FOREIGN-SYNC" not in text and "ARCHIVED SECRET" not in text


def test_collaborative_runtime_never_uses_unbound_legacy_personal_evidence(setup, monkeypatch):
    from code_assist_v1.notion_collaboration import iam_runtime
    _, service, _, pid = setup
    monkeypatch.setattr(iam_runtime, "runtime_requested", lambda: True)
    assert service.workspace_reader.capability("owner")["enabled"] is False
    assert service.workspace_reader.snapshot("owner", "내 notion 내용") is None
    with pytest.raises(PermissionError):
        service.workspace_reader.authorize_history("owner", {"owner_user_id": "owner", "page_ids": [pid]})
