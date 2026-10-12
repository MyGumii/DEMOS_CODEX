from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from flask import Flask

from code_assist_v1 import internal_llm, notion_ai_notes as notes, notion_store as ns
from code_assist_v1 import routes_notion_ai_notes as routes


SOURCE = "민수: 금요일까지 보고서를 제출하겠습니다.\n지연: 출시일은 월요일로 결정했습니다."
RESULT = {
    "summary": "보고서 제출과 출시 일정을 정했습니다.",
    "decisions": [{"text": "월요일 출시", "source_quote": "출시일은 월요일로 결정했습니다."}],
    "action_items": [{"task": "보고서 제출", "owner": "민수", "due": "금요일", "source_quote": "민수: 금요일까지 보고서를 제출하겠습니다."}],
}
MODELS = [{"id": "gemma4", "name": "Gemma4", "provider": "hynix_api"}, {"id": "qwen3.6", "name": "Qwen3.6", "provider": "hynix_api"}]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ns, "USER_DATA_DIR", str(tmp_path / "isolated"))
    monkeypatch.setattr(routes, "current_user_id", lambda: "owner")
    monkeypatch.setattr(notes, "available_models", lambda: MODELS)
    from code_assist_v1.notion_collaboration import iam_runtime
    monkeypatch.setattr(iam_runtime, "runtime_requested", lambda: False)
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic")
    routes.register_notion_ai_notes_routes(app)
    ns.create_page("other", title="unrelated private page")
    page_id = ns.create_page("owner", title="meeting")["page"]["id"]
    yield app.test_client(), page_id


def _request(client, page_id, **fields):
    return client.post("/api/notion/ai-notes/summarize", json={"page_id": page_id, "transcript": SOURCE, **fields})


@pytest.mark.parametrize("model_id", ["", "gemma4", "qwen3.6"])
def test_draft_selects_registered_model_without_writing_page(client, model_id):
    http, page_id = client
    before = ns.get_page("owner", page_id)
    with patch.object(internal_llm, "run_internal_text", return_value=(json.dumps(RESULT), {"model_id": model_id, "model_used": "test"})) as runner:
        response = _request(http, page_id, model_id=model_id)
    assert response.status_code == 200
    assert response.get_json()["result"] == RESULT
    assert response.headers["Cache-Control"] == "no-store"
    assert ns.get_page("owner", page_id) == before
    assert runner.call_args.kwargs["model_ids"] == ([model_id] if model_id else ["gemma4", "qwen3.6"])
    assert runner.call_args.kwargs["provider_order"] == ["hynix_api", "codex"]
    assert runner.call_args.kwargs["preserve_codex_input"] is True
    assert runner.call_args.args[1]["transcript"] == SOURCE
    assert "unrelated" not in str(runner.call_args)
    assert runner.call_args.kwargs["validate_output"](json.dumps(RESULT)) == ""
    assert response.get_json()["summary_phase"] == "final"


def test_provisional_summary_instruction_and_invalid_phase(client):
    http, page_id = client
    with patch.object(internal_llm, "run_internal_text", return_value=(json.dumps(RESULT), {})) as runner:
        response = _request(http, page_id, summary_phase="provisional")
        assert response.status_code == 200
        assert response.get_json()["summary_phase"] == "provisional"
        assert "provisional summary" in runner.call_args.kwargs["system_prompt"]
        assert "later corrections" in runner.call_args.kwargs["system_prompt"]
        runner.reset_mock()
        assert _request(http, page_id, summary_phase="arbitrary").status_code == 400
        runner.assert_not_called()


@pytest.mark.parametrize("bad", [
    {"model_id": "client-arbitrary-model"}, {"transcript": 123}, {"transcript": "x" * 80001},
    {"transcript": "", "notes": ""}, {"notes": "x" * 12001}, {"instructions": "x" * 2001},
])
def test_invalid_input_does_not_call_model(client, bad):
    http, page_id = client
    with patch.object(internal_llm, "run_internal_text") as runner:
        assert _request(http, page_id, **bad).status_code == 400
        runner.assert_not_called()


@pytest.mark.parametrize("role,status", [("view", 403), ("comment", 403), ("edit", 200)])
def test_page_share_permissions(client, monkeypatch, role, status):
    http, page_id = client
    ns.share_page("owner", page_id, "collaborator", role=role)
    monkeypatch.setattr(routes, "current_user_id", lambda: "collaborator")
    with patch.object(internal_llm, "run_internal_text", return_value=(json.dumps(RESULT), {})) as runner:
        assert _request(http, page_id).status_code == status
        assert runner.call_count == (1 if status == 200 else 0)


def test_locked_and_private_pages_do_not_send_data(client, monkeypatch):
    http, page_id = client
    ns.update_page("owner", page_id, {"settings": {"locked": True}})
    with patch.object(internal_llm, "run_internal_text") as runner:
        assert _request(http, page_id).status_code == 403
        monkeypatch.setattr(routes, "current_user_id", lambda: "stranger")
        assert _request(http, page_id).status_code == 404
        runner.assert_not_called()


def test_access_revocation_during_generation_discards_result(client, monkeypatch):
    http, page_id = client
    ns.share_page("owner", page_id, "collaborator", role="edit")
    monkeypatch.setattr(routes, "current_user_id", lambda: "collaborator")

    def revoke(*args, **kwargs):
        ns.revoke_share("owner", page_id, "collaborator")
        return json.dumps(RESULT), {}

    with patch.object(internal_llm, "run_internal_text", side_effect=revoke):
        response = _request(http, page_id)
    assert response.status_code == 404
    assert "보고서" not in response.get_data(as_text=True)


def test_anonymous_and_malformed_and_busy(client, monkeypatch):
    http, page_id = client
    assert http.post("/api/notion/ai-notes/summarize", json=[]).status_code == 400
    routes._slots.acquire()
    routes._slots.acquire()
    try:
        assert _request(http, page_id).status_code == 429
    finally:
        routes._slots.release()
        routes._slots.release()
    monkeypatch.setattr(routes, "current_user_id", lambda: None)
    assert _request(http, page_id).status_code == 401
    assert http.get("/api/notion/ai-notes/capabilities").status_code == 401


@pytest.mark.parametrize("output", [None, "not JSON", json.dumps({**RESULT, "decisions": [{"text": "invented", "source_quote": "not in source"}]})])
def test_provider_failure_and_bad_citations_are_not_fake_summaries(client, output):
    http, page_id = client
    with patch.object(internal_llm, "run_internal_text", return_value=(output, {"error": "secret token https://internal.endpoint"})):
        response = _request(http, page_id)
    assert response.status_code == 503
    assert "secret" not in response.get_data(as_text=True)
    assert "result" not in response.get_json()


def test_block_persistence_and_export(client):
    _, page_id = client
    ns.replace_blocks("owner", page_id, [{"type": "ai_notes", "content": "## AI 노트\n" + SOURCE, "props": {"transcript": SOURCE, "result": RESULT}}])
    block = ns.get_page("owner", page_id)["blocks"][0]
    assert block["type"] == "ai_notes"
    assert block["props"]["result"] == RESULT
    assert SOURCE in ns.export_page_markdown("owner", page_id)["markdown"]


def test_model_capabilities_respect_launch_policy(monkeypatch):
    registry = {
        "gemma4": {"model": "gemma-4-31B-it", "name": "Gemma4"},
        "qwen3.6": {"model": "qwen3.6", "name": "Qwen3.6"},
        "disabled": {"enabled": False},
        "codex-local": {"runtime": "codex"},
    }
    monkeypatch.setattr(internal_llm, "MODEL_REGISTRY", registry)
    monkeypatch.setattr(internal_llm, "ENV_CONFIG", {})
    monkeypatch.setattr(internal_llm, "_candidate_model_ids", lambda: list(registry))
    monkeypatch.setattr(internal_llm, "codex_internal_enabled", lambda: False)
    from code_assist_v1 import codex_bridge
    monkeypatch.setattr(codex_bridge, "_resolve_codex_bin", lambda: "synthetic-codex")
    monkeypatch.setattr(codex_bridge, "_codex_cli_authenticated", lambda binary: True)
    assert [item["id"] for item in notes.available_models()] == ["gemma4", "qwen3.6"]
    monkeypatch.setattr(internal_llm, "codex_internal_enabled", lambda: True)
    assert [item["id"] for item in notes.available_models()] == ["gemma4", "qwen3.6", "codex-local"]
    monkeypatch.setattr(codex_bridge, "_codex_cli_authenticated", lambda binary: False)
    assert [item["id"] for item in notes.available_models()] == ["gemma4", "qwen3.6"]


@pytest.mark.parametrize("model_id", ["gemma4", "qwen3.6"])
def test_real_loopback_chat_completion_transport(client, monkeypatch, model_id):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"choices": [{"message": {"content": json.dumps(RESULT)}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    registry = {model_id: {"model": model_id + "-served", "name": model_id, "url": f"http://127.0.0.1:{server.server_port}/v1/chat/completions"}}
    monkeypatch.setattr(internal_llm, "MODEL_REGISTRY", registry)
    monkeypatch.setattr(internal_llm, "ENV_CONFIG", {})
    monkeypatch.setattr(internal_llm, "_candidate_model_ids", lambda: [model_id])
    monkeypatch.setattr(internal_llm, "codex_internal_enabled", lambda: False)
    monkeypatch.setattr(internal_llm, "resolve_api_token", lambda cfg: ("", "test"))
    try:
        http, page_id = client
        response = _request(http, page_id, model_id=model_id)
        assert response.status_code == 200
        assert response.get_json()["model_id"] == model_id
        assert len(received) == 1
        assert received[0]["model"] == model_id + "-served"
        assert json.loads(received[0]["messages"][1]["content"])["payload"]["transcript"] == SOURCE
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_collaborative_authority_does_not_fall_back_to_legacy(client, monkeypatch):
    from types import SimpleNamespace
    from code_assist_v1.notion_collaboration import iam_runtime
    from code_assist_v1 import routes_notion_workspace

    http, page_id = client
    monkeypatch.setattr(iam_runtime, "runtime_requested", lambda: True)
    monkeypatch.setattr(routes_notion_workspace, "_store", lambda: SimpleNamespace(iam="canonical-authority"))
    calls = []

    def payload(authority, user, page):
        calls.append((authority, user, page))
        return {"page": {"can_edit": True, "workspace_id": "workspace-a", "settings": {}}}

    monkeypatch.setattr(iam_runtime, "collaborative_page_payload", payload)
    with patch.object(ns, "get_page", side_effect=AssertionError("legacy access forbidden")), patch.object(internal_llm, "run_internal_text", return_value=(json.dumps(RESULT), {})) as runner:
        assert _request(http, page_id, workspace_id="workspace-b").status_code == 404
        runner.assert_not_called()
        assert _request(http, page_id, workspace_id="workspace-a").status_code == 200
        assert len(calls) >= 4


def test_unknown_owner_is_rejected():
    result = json.loads(json.dumps(RESULT))
    result["action_items"][0]["owner"] = "unmentioned person"
    with pytest.raises(ValueError, match="owner/date"):
        notes.normalize_result(result, SOURCE)
