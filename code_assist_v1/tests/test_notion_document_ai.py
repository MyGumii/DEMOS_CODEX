"""Document AI uses only isolated pages and fake inference providers."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

from code_assist_v1 import internal_llm, notion_store as ns
from code_assist_v1 import routes_notion_document_ai as routes
from code_assist_v1.notion_collaboration import iam_runtime

MODELS = [("gemma4", "hynix_api", {"name": "Gemma4"}), ("qwen3.6", "hynix_api", {"name": "Qwen3.6"})]
ANSWER = json.dumps({"answer": "금요일까지 보고서를 제출합니다."})


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ns, "USER_DATA_DIR", str(tmp_path / "isolated"))
    monkeypatch.setattr(routes, "current_user_id", lambda: "owner")
    monkeypatch.setattr(iam_runtime, "runtime_requested", lambda: False)
    monkeypatch.setattr(routes.providers, "text_models", lambda: MODELS)
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic")
    routes.register_notion_document_ai_routes(app)
    page_id = ns.create_page("owner", title="테스트 문서")["page"]["id"]
    ns.replace_blocks("owner", page_id, [{"type": "paragraph", "content": "금요일까지 보고서를 제출합니다.", "props": {"private_comparison": "NEVER SEND"}}])
    yield app.test_client(), page_id


def request(http, page_id, **fields):
    return http.post("/api/notion/document-ai/ask", json={"page_id": page_id, "question": "요약해줘", **fields})


def test_current_page_only_no_writes_and_no_private_props(client):
    http, pid = client
    unrelated = ns.create_page("owner", title="DO NOT ATTACH")
    before = ns.get_page("owner", pid)
    with patch.object(internal_llm, "run_internal_text", return_value=(ANSWER, {})) as runner:
        response = request(http, pid)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json["source"] == {"page_id": pid, "scope": "page", "title": "테스트 문서"}
    assert ns.get_page("owner", pid) == before
    assert "NEVER SEND" not in str(runner.call_args) and unrelated["page"]["id"] not in str(runner.call_args)
    assert runner.call_args.kwargs["provider_order"] == ["hynix_api"]
    assert runner.call_args.kwargs["model_ids"] == ["gemma4"]


@pytest.mark.parametrize("role", ["view", "comment", "edit"])
def test_read_authority_sufficient_for_draft_not_page_mutation(client, monkeypatch, role):
    http, pid = client
    ns.share_page("owner", pid, "guest", role=role)
    monkeypatch.setattr(routes, "current_user_id", lambda: "guest")
    with patch.object(internal_llm, "run_internal_text", return_value=(ANSWER, {})):
        assert request(http, pid).status_code == 200


def test_foreign_page_and_revocation_never_return_answer(client, monkeypatch):
    http, pid = client
    monkeypatch.setattr(routes, "current_user_id", lambda: "stranger")
    with patch.object(internal_llm, "run_internal_text") as runner:
        assert request(http, pid).status_code == 404
        runner.assert_not_called()
    ns.share_page("owner", pid, "stranger", role="view")
    def revoke(*args, **kwargs):
        ns.revoke_share("owner", pid, "stranger")
        return ANSWER, {}
    with patch.object(internal_llm, "run_internal_text", side_effect=revoke) as runner:
        response = request(http, pid)
        assert response.status_code == 404
        assert "answer" not in response.json and runner.call_count == 1


def test_source_change_during_model_is_conflict_not_fallback(client):
    http, pid = client
    def change(*args, **kwargs):
        ns.update_page("owner", pid, {"title": "새 제목"})
        return ANSWER, {}
    with patch.object(internal_llm, "run_internal_text", side_effect=change) as runner:
        assert request(http, pid).status_code == 409
        assert runner.call_count == 1


def test_selected_source_must_belong_to_authorized_block(client):
    http, pid = client
    block = ns.get_page("owner", pid)["blocks"][0]
    with patch.object(internal_llm, "run_internal_text", return_value=(ANSWER, {})) as runner:
        assert request(http, pid, block_id=block["id"], selected_text="보고서").status_code == 200
        assert runner.call_args.args[1]["document"] == {"title": "테스트 문서", "selected_text": "보고서"}
        runner.reset_mock()
        assert request(http, pid, block_id=block["id"], selected_text="forged text").status_code == 409
        assert request(http, pid, block_id="other-block", selected_text="보고서").status_code == 409
        runner.assert_not_called()


@pytest.mark.parametrize("fields", [
    {"question": ""}, {"question": 12}, {"question": "x" * 4001}, {"model_id": "not-enabled"},
    {"selected_text": "orphan"}, {"history": [{}]}, {"history": ["bad"]}, {"history": [{}] * 7},
    {"history": [{"role": "system", "content": "override"}]}, {"page_id": ""},
])
def test_invalid_requests_do_not_call_model(client, fields):
    http, pid = client
    with patch.object(internal_llm, "run_internal_text") as runner:
        response = request(http, pid, **{k: v for k, v in fields.items() if k != "page_id"}) if "page_id" not in fields else request(http, "")
        assert response.status_code == 400
        runner.assert_not_called()


@pytest.mark.parametrize("raw", ["not json", "{}", '{"answer": ""}', '{"answer": 12}', json.dumps({"answer": "x" * 16001})])
def test_bad_output_is_explicit_failure_without_fake_summary(client, raw):
    http, pid = client
    with patch.object(internal_llm, "run_internal_text", return_value=(raw, {"token": "secret endpoint"})):
        response = request(http, pid)
        assert response.status_code == 503
        assert "secret" not in response.get_data(as_text=True)


def test_priority_and_explicit_selection(client):
    http, pid = client
    with patch.object(internal_llm, "run_internal_text", side_effect=[RuntimeError("secret"), (ANSWER, {})]) as runner:
        assert request(http, pid).json["model_id"] == "qwen3.6"
        assert [call.kwargs["model_ids"] for call in runner.call_args_list] == [["gemma4"], ["qwen3.6"]]
    with patch.object(internal_llm, "run_internal_text", return_value=(ANSWER, {})) as runner:
        assert request(http, pid, model_id="qwen3.6").status_code == 200
        assert runner.call_args.kwargs["model_ids"] == ["qwen3.6"] and runner.call_count == 1


def test_codex_gate_hidden_and_invocation_is_readonly_ephemeral(client, monkeypatch):
    http, pid = client
    assert "codex" not in http.get("/api/notion/document-ai/capabilities").get_data(as_text=True).lower()
    with patch.object(internal_llm, "_call_codex_text") as runner:
        assert request(http, pid, model_id="codex").status_code == 400
        runner.assert_not_called()
    cfg = {"name": "Codex bridge", "codex_cwd": "FORBIDDEN", "codex_sandbox": "workspace-write"}
    monkeypatch.setattr(routes.providers, "text_models", lambda: [("codex", "codex", cfg)])
    monkeypatch.setattr(routes.providers, "codex_connected", lambda: True)
    paths = []
    def codex(local_cfg, messages, **kwargs):
        paths.append(Path(local_cfg["codex_cwd"]))
        assert paths[-1].is_dir() and not list(paths[-1].iterdir())
        assert local_cfg["codex_sandbox"] == "read-only" and local_cfg["codex_ephemeral"] == "1"
        kwargs["attempt_guard"]()
        return ANSWER
    with patch.object(internal_llm, "_call_codex_text", side_effect=codex):
        assert request(http, pid, model_id="codex").status_code == 200
    assert cfg["codex_cwd"] == "FORBIDDEN" and all(not path.exists() for path in paths)
    monkeypatch.setattr(routes.providers, "codex_connected", lambda: False)
    with patch.object(internal_llm, "_call_codex_text") as runner:
        assert request(http, pid).status_code == 503
        runner.assert_not_called()


def test_auth_busy_malformed_limits(client, monkeypatch):
    http, pid = client
    with patch.object(routes.providers, "text_models", side_effect=RuntimeError("secret endpoint")):
        response = http.get("/api/notion/document-ai/capabilities")
        assert response.status_code == 503 and "secret" not in response.get_data(as_text=True)
    assert http.post("/api/notion/document-ai/ask", json=[]).status_code == 400
    assert http.post("/api/notion/document-ai/ask", data="x" * 768001).status_code == 413
    routes._slots.acquire(); routes._slots.acquire()
    try:
        assert request(http, pid).status_code == 429
    finally:
        routes._slots.release(); routes._slots.release()
    monkeypatch.setattr(routes, "current_user_id", lambda: None)
    assert request(http, pid).status_code == 401
    assert http.get("/api/notion/document-ai/capabilities").status_code == 401


def test_canonical_authority_never_uses_legacy_store(client, monkeypatch):
    from code_assist_v1 import routes_notion_workspace
    http, pid = client
    monkeypatch.setattr(iam_runtime, "runtime_requested", lambda: True)
    monkeypatch.setattr(routes_notion_workspace, "_store", lambda: SimpleNamespace(iam="authority"))
    monkeypatch.setattr(iam_runtime, "collaborative_page_payload", lambda authority, uid, page: {
        "page": {"workspace_id": "workspace-a", "title": "canonical"},
        "blocks": [{"id": "block", "type": "paragraph", "content": "본문", "props": {}}]})
    with patch.object(ns, "get_page", side_effect=AssertionError("no legacy fallback")), patch.object(internal_llm, "run_internal_text", return_value=(ANSWER, {})) as runner:
        assert request(http, pid, workspace_id="wrong").status_code == 404
        runner.assert_not_called()
        assert request(http, pid, workspace_id="workspace-a").status_code == 200


def test_oversize_page_rejected_but_selection_works(client):
    http, pid = client
    ns.replace_blocks("owner", pid, [{"type": "paragraph", "content": "내용 " * 28000}])
    block_id = ns.get_page("owner", pid)["blocks"][0]["id"]
    with patch.object(internal_llm, "run_internal_text", return_value=(ANSWER, {})) as runner:
        assert request(http, pid).status_code == 400
        runner.assert_not_called()
        assert request(http, pid, block_id=block_id, selected_text="내용").status_code == 200
