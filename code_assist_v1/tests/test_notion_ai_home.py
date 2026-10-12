"""Isolated AI home service, permissions, persistence, artifacts and process fences."""
import json
import multiprocessing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import xml.etree.ElementTree as ET

import pytest
from flask import Flask

from code_assist_v1 import auth, config, internal_llm, notion_store as ns
from code_assist_v1 import routes_notion_ai_chat as routes
from code_assist_v1 import notion_ai_chat_artifacts as artifacts
from code_assist_v1.notion_ai_chat import ChatService
from code_assist_v1.notion_ai_chat_store import ChatStore, ChatConflict
from code_assist_v1.notion_collaboration import iam_runtime

SECRET = "synthetic-ai-home-test-secret-32-chars"
MODELS = [("gemma4", "hynix_api", {"name": "Gemma4"}), ("qwen3.6", "hynix_api", {"name": "Qwen3.6"})]
ARTIFACTS = {
    "slides": {"kind": "slides", "title": "주간 발표", "slides": [{"title": "일정", "bullets": ["금요일 제출", "<script>not executable</script>"]}]},
    "spreadsheet": {"kind": "spreadsheet", "title": "계획표", "columns": ["이름", "값"], "rows": [["=HYPERLINK(\"bad\")", 3], ["일본어 日本語", -2]]},
    "visualization": {"kind": "visualization", "title": "성과", "labels": ["증가", "감소"], "values": [3, -2], "unit": "건"},
}


class FakeReader:
    revoked = False
    version = "v1"
    def authorize(self, uid, space):
        if self.revoked or uid != "owner" or space != "wiki1":
            raise PermissionError("Wiki denied")
    def scopes(self, uid, cursor=None):
        self.authorize(uid, "wiki1")
        return {"items": [{"space_id": "wiki1", "display_name": "내 Wiki"}], "next_cursor": None}
    def retrieve(self, uid, space, query):
        self.authorize(uid, space)
        return {"mode": "synthetic", "truncated": False, "items": [{"id": "doc1", "title": "일정 지식", "space_id": space, "revision_id": self.version, "excerpt": "금요일에 보고서를 제출합니다."}]}
    def revalidate(self, uid, space, snapshot):
        self.authorize(uid, space)
        if any(item["revision_id"] != self.version for item in snapshot["items"]):
            raise PermissionError("Wiki revision changed")


def model_reply(_task, payload, **kwargs):
    sources = payload["references"]
    citations = [{"source_id": sources[0]["source_id"], "quote": sources[0]["excerpt"][:20]}] if sources else []
    return json.dumps({"answer": "금요일 제출 계획입니다.", "citations": citations, "artifact": ARTIFACTS.get(payload["mode"])}), {}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = str(tmp_path / "isolated")
    monkeypatch.setattr(config, "USER_DATA_DIR", root)
    monkeypatch.setattr(ns, "USER_DATA_DIR", root)
    monkeypatch.setattr(routes, "current_user_id", lambda: "owner")
    monkeypatch.setattr(auth, "current_user_id", lambda: routes.current_user_id())
    monkeypatch.setattr(routes.providers, "text_models", lambda: MODELS)
    monkeypatch.setattr(iam_runtime, "runtime_requested", lambda: False)
    app = Flask(__name__); app.config.update(TESTING=True, SECRET_KEY=SECRET)
    routes.register_notion_ai_chat_routes(app)
    reader = FakeReader(); service = ChatService(app, reader); app.extensions[routes.SERVICE_KEY] = service
    pid = ns.create_page("owner", title="내 문서")["page"]["id"]
    ns.replace_blocks("owner", pid, [{"type": "paragraph", "content": "금요일 보고서 제출", "props": {"host_private_comparison": "NEVER SEND"}}])
    return app.test_client(), service, reader, pid


def create(http, **fields):
    response = http.post("/api/notion/ai-home/conversations", json={"operation_id": str(uuid4()), "page_ids": [], "knowledge_space_id": "", "workspace_id": "", **fields})
    assert response.status_code == 200, response.json
    return response.json


def send(http, record, **fields):
    return http.post(f'/api/notion/ai-home/conversations/{record["id"]}/messages', json={"operation_id": str(uuid4()), "expected_revision": record["revision"], "message": "계획을 정리해줘", "mode": "chat", **fields})


def legacy_research_send(http, record, **fields):
    """Retained v1 validator unit checks; v2 queue API is tested separately."""
    service = http.application.extensions[routes.SERVICE_KEY]
    data = {"operation_id": str(uuid4()), "expected_revision": record["revision"], "message": "계획을 정리해줘", "mode": "research", **fields}
    with http.application.test_request_context():
        result = service.ask("owner", record["id"], data)
    return http.application.response_class(json.dumps(result), content_type="application/json")


def test_general_chat_encrypted_persistent_scoped_replay(setup, monkeypatch):
    http, service, reader, pid = setup
    record = create(http)
    before = ns.get_page("owner", pid)
    op = str(uuid4())
    with patch.object(internal_llm, "run_internal_text", side_effect=model_reply) as runner:
        response = send(http, record, operation_id=op)
        assert response.status_code == 200, response.json
        assert len(response.json["messages"]) == 2
        assert runner.call_args.args[1]["references"] == []
        assert runner.call_args.kwargs["model_ids"] == ["gemma4"]
        assert send(http, record, operation_id=op).status_code == 200
        assert runner.call_count == 1
        assert send(http, record, operation_id=op, message="different").status_code == 409
    assert ns.get_page("owner", pid) == before
    path = service.store("owner").path
    assert "계획".encode() not in path.read_bytes() and "금요일".encode() not in path.read_bytes()
    fresh = ChatStore(path, SECRET, "owner").get(record["id"])
    assert fresh["revision"] == 1 and len(fresh["messages"]) == 2
    assert http.get("/api/notion/ai-home/conversations").json["items"][0]["id"] == record["id"]
    monkeypatch.setattr(routes, "current_user_id", lambda: "stranger")
    assert http.get(f'/api/notion/ai-home/conversations/{record["id"]}').status_code == 404
    assert http.get("/api/notion/ai-home/conversations").json["items"] == []
    assert not service.store("stranger").path.exists()


def test_selected_pages_and_wiki_citations_no_private_comparison(setup):
    http, service, reader, pid = setup
    record = create(http, page_ids=[pid], knowledge_space_id="wiki1")
    with patch.object(internal_llm, "run_internal_text", side_effect=model_reply) as runner:
        response = legacy_research_send(http, record, mode="research")
    assert response.status_code == 200, response.json
    assert "NEVER SEND" not in str(runner.call_args)
    answer = response.json["messages"][-1]
    assert answer["citations"] and answer["context_info"]["source_count"] == 2
    assert answer["context_info"]["web_search"] is False
    assert "knowledge_snapshot" not in answer
    assert "knowledge_snapshot" in service.store("owner").get(record["id"])["messages"][-1]
    reader.revoked = True
    assert http.get(f'/api/notion/ai-home/conversations/{record["id"]}').status_code == 403
    assert http.get("/api/notion/ai-home/conversations").json["items"] == []
    assert http.get(f'/api/notion/ai-home/conversations/{record["id"]}/messages/{answer["operation_id"]}/download/md').status_code == 403


def test_organization_closure_is_saved_privately_for_historical_revalidation(setup, monkeypatch):
    http, service, reader, pid = setup
    retrieve = reader.retrieve
    monkeypatch.setattr(reader, "retrieve", lambda *args: {**retrieve(*args), "corpus_closure_sha256": "closure-v1"})
    record = create(http, knowledge_space_id="wiki1")
    with patch.object(internal_llm, "run_internal_text", side_effect=model_reply):
        response = send(http, record)
    assert response.status_code == 200
    assert "knowledge_snapshot" not in response.json["messages"][-1]
    snapshot = service.store("owner").get(record["id"])["messages"][-1]["knowledge_snapshot"]
    assert snapshot["corpus_closure_sha256"] == "closure-v1"
    def changed(*args):
        assert args[-1]["corpus_closure_sha256"] == "closure-v1"
        raise PermissionError("scope changed")
    monkeypatch.setattr(reader, "revalidate", changed)
    assert http.get(f'/api/notion/ai-home/conversations/{record["id"]}').status_code == 403
    assert http.get("/api/notion/ai-home/conversations").json["items"] == []


def test_revoked_page_stale_page_and_wiki_do_not_commit(setup, monkeypatch):
    http, service, reader, pid = setup
    ns.share_page("owner", pid, "guest", role="view")
    monkeypatch.setattr(routes, "current_user_id", lambda: "guest")
    record = create(http, page_ids=[pid])
    def revoke(*args, **kwargs):
        ns.revoke_share("owner", pid, "guest")
        return model_reply(*args, **kwargs)
    with patch.object(internal_llm, "run_internal_text", side_effect=revoke) as runner:
        assert send(http, record).status_code == 404
        assert runner.call_count == 1
    assert service.store("guest").get(record["id"])["messages"] == []
    monkeypatch.setattr(routes, "current_user_id", lambda: "owner")
    record = create(http, page_ids=[pid])
    def change(*args, **kwargs):
        ns.update_page("owner", pid, {"title": "새 제목"}); return model_reply(*args, **kwargs)
    with patch.object(internal_llm, "run_internal_text", side_effect=change):
        assert send(http, record).status_code == 409
    assert service.store("owner").get(record["id"])["messages"] == []
    record = create(http, knowledge_space_id="wiki1")
    def change_wiki(*args, **kwargs):
        reader.version = "v2"; return model_reply(*args, **kwargs)
    with patch.object(internal_llm, "run_internal_text", side_effect=change_wiki):
        assert send(http, record).status_code == 403


@pytest.mark.parametrize("mode,kind,mime", [("slides", "html", "text/html"), ("spreadsheet", "csv", "text/csv"), ("visualization", "svg", "image/svg+xml"), ("chat", "md", "text/markdown")])
def test_artifact_generation_and_authorized_download(setup, mode, kind, mime):
    http, service, reader, pid = setup
    record = create(http)
    with patch.object(internal_llm, "run_internal_text", side_effect=model_reply):
        response = send(http, record, mode=mode)
    assert response.status_code == 200, response.json
    answer = response.json["messages"][-1]
    response = http.get(f'/api/notion/ai-home/conversations/{record["id"]}/messages/{answer["operation_id"]}/download/{kind}')
    assert response.status_code == 200 and mime in response.content_type
    assert "attachment" in response.headers["Content-Disposition"]
    assert response.headers["Cache-Control"] == "no-store"
    body = response.get_data(as_text=True)
    if kind == "html":
        assert "&lt;script&gt;" in body and "<script>" not in body and "Content-Security-Policy" in body
    if kind == "csv":
        assert "'=HYPERLINK" in body and ",-2" in body
    if kind == "svg":
        ET.fromstring(body)
        assert "-2" in body and "nan" not in body.lower()


def test_archive_restore_does_not_delete_data_and_source_change_starts_new_chat(setup):
    http, service, reader, pid = setup
    record = create(http)
    with patch.object(internal_llm, "run_internal_text", side_effect=model_reply):
        record = send(http, record).json
    path = f'/api/notion/ai-home/conversations/{record["id"]}/archive'
    op = str(uuid4())
    assert http.post(path, json={"operation_id": op, "archived": True}).status_code == 200
    assert http.post(path, json={"operation_id": op, "archived": True}).status_code == 200
    assert http.get("/api/notion/ai-home/conversations").json["items"] == []
    assert http.get("/api/notion/ai-home/conversations?archived=1").json["items"][0]["id"] == record["id"]
    assert len(service.store("owner").get(record["id"])["messages"]) == 2
    assert http.post(path, json={"operation_id": str(uuid4()), "archived": False}).status_code == 200
    newer = create(http, page_ids=[pid])
    assert newer["id"] != record["id"] and newer["messages"] == []
    assert record["scope"]["page_ids"] == []


def test_hidden_codex_and_priority_explicit_selection(setup, monkeypatch):
    http, service, reader, pid = setup
    assert "codex" not in http.get("/api/notion/ai-home/capabilities").get_data(as_text=True).lower()
    record = create(http)
    with patch.object(internal_llm, "run_internal_text", side_effect=[RuntimeError("secret"), model_reply("", {"references": [], "mode": "chat"})]) as runner:
        response = send(http, record)
    assert response.json["messages"][-1]["model_id"] == "qwen3.6"
    assert [call.kwargs["model_ids"] for call in runner.call_args_list] == [["gemma4"], ["qwen3.6"]]
    with patch.object(internal_llm, "run_internal_text") as runner:
        assert send(http, response.json, model_id="codex").status_code == 400
        runner.assert_not_called()
    cfg = {"name": "Codex bridge", "codex_cwd": "forbidden"}
    monkeypatch.setattr(routes.providers, "text_models", lambda: [("codex", "codex", cfg)])
    monkeypatch.setattr(routes.providers, "codex_connected", lambda: True)
    paths = []
    def codex(local_cfg, messages, **kwargs):
        paths.append(Path(local_cfg["codex_cwd"]))
        assert paths[-1].is_dir() and local_cfg["codex_sandbox"] == "read-only" and local_cfg["codex_ephemeral"] == "1"
        kwargs["attempt_guard"]()
        return json.dumps({"answer": "합성 응답", "citations": [], "artifact": None})
    with patch.object(internal_llm, "_call_codex_text", side_effect=codex):
        assert send(http, response.json, model_id="codex").status_code == 200
    assert cfg["codex_cwd"] == "forbidden" and all(not path.exists() for path in paths)


@pytest.mark.parametrize("fields", [{"message": ""}, {"message": "x" * 12001}, {"mode": "shell"}, {"expected_revision": -1}, {"expected_revision": True}, {"operation_id": "bad"}])
def test_invalid_input_no_model(setup, fields):
    http, service, reader, pid = setup
    record = create(http)
    with patch.object(internal_llm, "run_internal_text") as runner:
        assert send(http, record, **fields).status_code == 400
        runner.assert_not_called()


def test_bad_citation_and_bad_artifact_do_not_store_fake_answer(setup):
    http, service, reader, pid = setup
    record = create(http, page_ids=[pid])
    for bad in [{"answer": "unsupported", "citations": [{"source_id": "fake", "quote": "fake"}], "artifact": None},
                {"answer": "unsupported", "citations": [], "artifact": {"kind": "script", "code": "arbitrary"}}]:
        with patch.object(internal_llm, "run_internal_text", return_value=(json.dumps(bad), {})):
            assert send(http, record).status_code == 503
    assert service.store("owner").get(record["id"])["messages"] == []


def test_budget_and_auth_busy_limits(setup, monkeypatch):
    http, service, reader, pid = setup
    ns.replace_blocks("owner", pid, [{"type": "paragraph", "content": "한글자료 " * 13000}])
    sources, pages, knowledge, info = service.context("owner", {"page_ids": [pid], "workspace_id": "", "knowledge_space_id": "wiki1"}, "자료")
    assert info["excerpt_bytes"] <= 98304 and info["truncated"]
    assert len(sources) == 2
    record = create(http)
    assert send(http, record, mode="research").status_code == 200  # Accepted as a durable job.
    routes._slots.acquire(); routes._slots.acquire()
    try:
        assert send(http, record).status_code == 429
    finally:
        routes._slots.release(); routes._slots.release()
    assert http.post("/api/notion/ai-home/conversations", json=[]).status_code == 400
    assert http.post("/api/notion/ai-home/conversations", data="x" * 96001).status_code == 413
    monkeypatch.setattr(routes, "current_user_id", lambda: None)
    assert http.get("/api/notion/ai-home/capabilities").status_code == 401


def test_disabled_account_during_general_chat_blocks_fallback_and_commit(setup, monkeypatch):
    http, service, reader, pid = setup
    record = create(http)
    def disable(*args, **kwargs):
        monkeypatch.setattr(auth, "current_user_id", lambda: None)
        return model_reply(*args, **kwargs)
    with patch.object(internal_llm, "run_internal_text", side_effect=disable) as runner:
        assert send(http, record).status_code == 403
        assert runner.call_count == 1
    saved = service.store("owner").get(record["id"])
    assert saved["messages"] == [] and saved["interrupted"] and not saved["pending"]


def test_canonical_workspace_authority_rechecked_and_no_legacy_fallback(setup, monkeypatch):
    from types import SimpleNamespace
    from code_assist_v1 import routes_notion_workspace as workspace
    http, service, reader, pid = setup
    allowed = [{"id": "workspace1"}]
    monkeypatch.setattr(iam_runtime, "runtime_requested", lambda: True)
    monkeypatch.setattr(workspace, "_store", lambda: SimpleNamespace(list_workspaces=lambda uid: allowed if uid == "owner" else []))
    assert http.post("/api/notion/ai-home/conversations", json={"operation_id": str(uuid4()), "workspace_id": "workspace2"}).status_code == 404
    record = create(http, workspace_id="workspace1")
    def revoke(*args, **kwargs):
        allowed.clear(); return model_reply(*args, **kwargs)
    with patch.object(internal_llm, "run_internal_text", side_effect=revoke) as runner:
        assert send(http, record).status_code == 404
        assert runner.call_count == 1
    assert service.store("owner").get(record["id"])["messages"] == []
    assert http.get("/api/notion/ai-home/conversations?workspace_id=workspace1").json["items"] == []


@pytest.mark.parametrize("scope", [{"page_ids": "bad"}, {"page_ids": ["p"] * 2}, {"page_ids": [str(i) for i in range(5)]}, {"page_ids": [True]}, {"knowledge_space_id": "outside"}])
def test_invalid_or_unowned_scope_cannot_create_conversation(setup, scope):
    http, service, reader, pid = setup
    response = http.post("/api/notion/ai-home/conversations", json={"operation_id": str(uuid4()), **scope})
    assert response.status_code in {400, 403}
    assert not service.store("owner").path.exists()


def test_wrong_secret_or_owner_never_returns_plaintext(tmp_path):
    path = tmp_path / "chat.db"
    store = ChatStore(path, SECRET, "owner")
    record = store.create(str(uuid4()), {"page_ids": [], "knowledge_space_id": "", "workspace_id": ""})
    from cryptography.fernet import InvalidToken
    with pytest.raises(InvalidToken): ChatStore(path, SECRET, "other").get(record["id"])
    with pytest.raises(InvalidToken): ChatStore(path, SECRET + "changed", "owner").get(record["id"])
    assert store.get(record["id"])["messages"] == []


def _race_begin(path, cid, op, barrier, queue):
    store = ChatStore(path, SECRET, "owner")
    barrier.wait(timeout=10)
    try:
        work = store.begin(cid, op, {"message": "one call"}, 0)
        queue.put("claimed" if work.get("claim") else "replayed")
    except ChatConflict:
        queue.put("blocked")


def test_cross_process_single_claim_and_expired_job_requires_new_operation(tmp_path):
    store = ChatStore(tmp_path / "chat.db", SECRET, "owner")
    record = store.create(str(uuid4()), {"workspace_id": "", "page_ids": [], "knowledge_space_id": ""})
    context = multiprocessing.get_context("spawn"); barrier = context.Barrier(2); queue = context.Queue(); op = str(uuid4())
    processes = [context.Process(target=_race_begin, args=(str(store.path), record["id"], op, barrier, queue)) for _ in range(2)]
    for process in processes: process.start()
    try:
        results = sorted(queue.get(timeout=20) for _ in processes)
        assert results == ["blocked", "claimed"]
        for process in processes:
            process.join(10); assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive(): process.terminate(); process.join(5)
        queue.close()
    assert store.get(record["id"])["pending"]
    with patch("code_assist_v1.notion_ai_chat_store.time.time", return_value=10**12):
        assert store.get(record["id"])["interrupted"]
        with pytest.raises(ChatConflict): store.begin(record["id"], op, {"message": "one call"}, 0)
        assert store.begin(record["id"], str(uuid4()), {"message": "retry"}, 0)["claim"]
