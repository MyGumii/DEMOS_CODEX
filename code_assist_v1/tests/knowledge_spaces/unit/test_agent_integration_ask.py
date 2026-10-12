"""AIK-030: committed ask answers across canonical, HTTP and Hermes MCP lanes."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
from uuid import uuid4

from flask import Flask
import pytest
from werkzeug.serving import make_server

from code_assist_v1.agent_integrations.bridge import KnowledgeBridge, stable_id
from code_assist_v1.agent_integrations.routes import bearer_auth_gate, register
from code_assist_v1.agent_integrations.service import IntegrationService
from code_assist_v1.agent_integrations.store import IntegrationStore
from code_assist_v1.knowledge_core.agent_runtime import compose_canonical_agent_runtime
from code_assist_v1.knowledge_core.domain import FeatureFlags
from code_assist_v1.knowledge_core.services.conversations import (
    AcceptConversationTurnCommand, CompleteConversationTurnCommand,
    CreateConversationCommand, TerminateConversationTurnCommand,
)
from code_assist_v1.tests.knowledge_spaces.unit.test_workspace_features import wiki

pytestmark = pytest.mark.unit
ANSWER = "## 검토 결과\n\n- 확인된 자료를 바탕으로 **검토**하세요."
QUESTION = "What is known?"
ASK = "/api/integrations/v1/knowledge/ask"


@pytest.fixture
def ask_env(wiki, tmp_path):
    factory, ids, source, management, _ = wiki
    calls = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        return ANSWER, {"model_kind": "fixture"}

    flags = FeatureFlags(core_enabled=True, personal_reads=True, personal_writes=True,
        multi_wiki_schema=True, multi_wiki_reads=True, multi_wiki_writes=True,
        conversation_wiki_binding=True)
    runtime = compose_canonical_agent_runtime(factory, flags=flags,
        principal_resolver=lambda: {"id": "member01", "scope_epoch": 1},
        personal_wiki_cursor_secret=b"a" * 32, model_runner=runner)
    services = replace(runtime.services, source_artifacts=source, management_reads=management)
    bridge = KnowledgeBridge(services, runtime.route_bindings)
    store = IntegrationStore(tmp_path / "ask-ledger.db", b"x" * 32)
    store.initialize()
    service = IntegrationService(store, bridge)
    app = Flask(__name__)
    app.secret_key = "x" * 32
    register(app, service, lambda: {"id": "member01"})

    @app.before_request
    def gate():
        handled, response = bearer_auth_gate(app)
        if handled:
            return response

    return SimpleNamespace(service=service, bridge=bridge, services=services,
        actor=bridge.actor("member01"), space=str(ids["member_space"]), calls=calls,
        app=app, client=app.test_client())


def grant(env, scopes=None):
    return env.service.create_connection("member01", dict(operation_id=str(uuid4()),
        name="Isolated ask fixture", read_space_id=env.space, write_space_id=env.space,
        scopes=scopes if scopes is not None else ["knowledge:read", "knowledge:ask"]))


def post(env, connection, operation, message=QUESTION):
    return env.client.post(ASK, headers={"Authorization": "Bearer " + connection["token"]},
        json={"operation_id": operation, "message": message})


def stored_turn(env, operation, *, state="completed"):
    """Pre-fix storage format: text only in assistant message, metadata separately."""
    common = dict(actor_user_id="member01", actor_scope_epoch=1, request_id=str(uuid4()))
    conversations = env.services.conversations
    cid = conversations.create(CreateConversationCommand(**common,
        operation_id=stable_id(operation + ":conversation"), space_id=env.space,
        title="외부 에이전트 질문")).conversation.id
    conversations.accept_turn(AcceptConversationTurnCommand(**common,
        operation_id=operation, conversation_id=cid, user_content={"text": QUESTION}))
    if state == "completed":
        conversations.complete_turn(CompleteConversationTurnCommand(**common,
            operation_id=operation, conversation_id=cid,
            assistant_content={"text": ANSWER}, final_response={"model_kind": "legacy-fixture"}))
    else:
        conversations.terminate_turn(TerminateConversationTurnCommand(**common,
            operation_id=operation, conversation_id=cid,
            terminal_state=state, error_code="FIXTURE_STOPPED" if state == "failed" else None,
            retryable=state == "failed"))
    return cid


def test_aik_030_http_answer_metadata_replay_and_grant_isolation(ask_env):
    env = ask_env
    connection, operation = grant(env), str(uuid4())
    first = post(env, connection, operation)
    assert first.status_code == 200, first.get_json()
    result = first.get_json()
    assert result["state"] == "completed"
    assert result["answer"] == ANSWER
    assert "citation_trace" in result["response"]
    assert "model_input_trace" in result["response"]
    assert "answer" not in result["response"]
    assert first.headers["Cache-Control"] == "private, no-store"
    assert len(env.calls) == 1
    # Reconstruct the adapter: replay must come from durable canonical messages.
    env.service.bridge = KnowledgeBridge(env.services, env.bridge.bindings)
    assert post(env, connection, operation).get_json() == result
    assert len(env.calls) == 1
    assert post(env, connection, operation, "Changed question").status_code == 409
    assert len(env.calls) == 1
    second = post(env, grant(env), operation).get_json()
    assert second["answer"] == ANSWER
    assert second["conversation_id"] != result["conversation_id"]
    assert len(env.calls) == 2
    assert post(env, grant(env, ["knowledge:read"]), str(uuid4())).status_code == 403
    env.service.store.revoke("member01", connection["id"], str(uuid4()), lambda: None)
    assert post(env, connection, operation).status_code == 401
    assert len(env.calls) == 2


def test_aik_030_legacy_answer_uses_its_exact_message_not_latest(ask_env):
    env, operation = ask_env, str(uuid4())
    cid = stored_turn(env, operation)
    common = dict(actor_user_id="member01", actor_scope_epoch=1, request_id=str(uuid4()),
        conversation_id=cid, operation_id=str(uuid4()))
    env.services.conversations.accept_turn(AcceptConversationTurnCommand(**common,
        user_content={"text": "A later question"}))
    env.services.conversations.complete_turn(CompleteConversationTurnCommand(**common,
        assistant_content={"text": "A later, unrelated answer"}, final_response={}))
    result = env.bridge.ask(env.actor, env.space, operation, QUESTION, lambda: None)
    assert result == dict(conversation_id=cid, state="completed", answer=ANSWER,
        response={"model_kind": "legacy-fixture"})
    assert not env.calls


@pytest.mark.parametrize("state", ["failed", "cancelled"])
def test_aik_030_terminal_turn_has_no_fabricated_answer(ask_env, state):
    env, operation = ask_env, str(uuid4())
    stored_turn(env, operation, state=state)
    result = env.bridge.ask(env.actor, env.space, operation, QUESTION, lambda: None)
    assert result["state"] == state
    assert result["answer"] is None
    if state == "failed":
        assert result["error"]["code"] == "FIXTURE_STOPPED"
    else:
        assert "error" not in result
    assert not env.calls


def test_aik_030_missing_body_is_unavailable_and_preserves_committed_turn(ask_env, monkeypatch):
    env, connection, operation = ask_env, grant(ask_env), str(uuid4())
    canonical_operation = stable_id(connection["id"] + ":" + operation)
    cid = stored_turn(env, canonical_operation)
    with monkeypatch.context() as patch:
        # Simulate an unavailable history read, never modify immutable messages.
        patch.setattr(type(env.services.conversations), "list_messages", lambda self, **kwargs: ())
        response = post(env, connection, operation)
        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "ANSWER_UNAVAILABLE"
    result = post(env, connection, operation).get_json()
    assert result["conversation_id"] == cid
    assert result["state"] == "completed" and result["answer"] == ANSWER
    assert not env.calls


def test_aik_030_revocation_during_answer_read_blocks_delivery(ask_env, monkeypatch):
    env, connection, operation = ask_env, grant(ask_env), str(uuid4())
    stored_turn(env, stable_id(connection["id"] + ":" + operation))
    original = env.services.conversations.list_messages

    def read_then_revoke(self, **kwargs):
        messages = original(**kwargs)
        env.service.store.revoke("member01", connection["id"], str(uuid4()), lambda: None)
        return messages

    monkeypatch.setattr(type(env.services.conversations), "list_messages", read_then_revoke)
    response = post(env, connection, operation)
    assert response.status_code == 403
    assert "answer" not in response.get_json()
    assert not env.calls


def test_aik_030_installed_hermes_mcp_receives_answer_and_exact_replay(ask_env, tmp_path):
    python = Path("D:/Hermes/hermes-agent/venv/Scripts/python.exe")
    if not python.is_file():
        pytest.skip("Hermes environment not installed on this host")
    env, connection = ask_env, grant(ask_env)
    server = make_server("127.0.0.1", 0, env.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    adapter = Path(__file__).resolve().parents[3] / "agent_integrations/hermes_mcp.py"
    process_env = {**os.environ, "SECOND_BRAIN_URL": f"http://127.0.0.1:{server.server_port}",
        "SECOND_BRAIN_TOKEN": connection["token"]}
    process_env.pop("SECOND_BRAIN_TOKEN_FILE", None)
    probe = tmp_path / "ask_mcp_probe.py"
    probe.write_text('''import asyncio, json, os, sys
from uuid import uuid4
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
async def main():
    async with stdio_client(StdioServerParameters(command=sys.executable,
            args=[sys.argv[1]], env=dict(os.environ))) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert "second_brain_ask" in [tool.name for tool in tools.tools]
            arguments = {"operation_id": str(uuid4()), "message": "What is known?"}
            responses = []
            for _ in range(2):
                result = await session.call_tool("second_brain_ask", arguments)
                assert not result.is_error, result
                responses.append(json.loads(next(c.text for c in result.content if c.type == "text")))
            assert responses[0] == responses[1]
            assert responses[0]["state"] == "completed"
            assert responses[0]["answer"] == json.loads(sys.argv[2])
            assert "citation_trace" in responses[0]["response"]
            print("Hermes MCP ask answer and exact replay: PASS")
asyncio.run(main())
''', encoding="utf-8")
    try:
        completed = subprocess.run([str(python), str(probe), str(adapter), json.dumps(ANSWER)],
            env=process_env, capture_output=True, text=True, timeout=45)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert len(env.calls) == 1
    finally:
        server.shutdown()
        thread.join(10)
