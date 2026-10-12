"""Execute the real adapter; intercept only HTTP send or the CLI child process."""
from dataclasses import replace
from hashlib import sha256
import json
import threading

import pytest
import requests

from code_assist_v1 import codex_bridge, internal_llm
from code_assist_v1.knowledge_core.agent_execution import CanonicalAgentTurnExecutor
from code_assist_v1.knowledge_core.answer_input import AnswerInputError, validated_api_receipt
from code_assist_v1.tests.knowledge_spaces.unit.test_canonical_agent_execution import _context


pytestmark = [pytest.mark.unit, pytest.mark.scenario("RANK-009", "RANK-014", "RANK-017")]


def _long_context():
    context = _context()
    # Late and middle facts matter; a head/tail-preserving transport still fails.
    body = '시작 조건 "예외"\\\n' + "가나다 조건\n" * 7000
    body += "중간의 필수 조건: 17영업일 이내, 단 휴일은 제외한다.\n"
    body += "다른 세부 조건\n" * 7000 + "마지막 조건: 2027-03-31 이후에는 변경한다."
    hit = replace(context.retrieval.hits[0], excerpt_md=body)
    return replace(context, retrieval=replace(context.retrieval, hits=(hit,))), body


def test_complete_answer_crosses_real_requests_serialization_without_losing_korean_or_metadata(monkeypatch):
    context, body = _long_context()
    captured = []
    monkeypatch.setattr(internal_llm, "_candidate_configs", lambda *args, **kwargs: [
        ("fixture", {"url": "https://example.invalid/chat/completions", "model": "사내-별칭",
                     "tls_verify": True}),
    ])
    monkeypatch.setattr(internal_llm, "resolve_api_token", lambda cfg: (None, None))

    def send(session, request, **kwargs):
        assert isinstance(request, requests.PreparedRequest)
        captured.append(request)
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"choices": [{"message": {"content": "검증용 답변"}}]}).encode()
        response.headers["Content-Type"] = "application/json"
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    completion = CanonicalAgentTurnExecutor(provider_order=("hynix_api",), model_ids=("fixture",)).complete(context)
    assert len(captured) == 1
    request = captured[0]
    assert isinstance(request.body, bytes)
    envelope = json.loads(request.body)
    payload = json.loads(envelope["messages"][1]["content"])
    assert payload["retrieval_candidates"][0]["excerpt_md"] == body
    assert payload["recent_messages"][0]["text"] == context.recent_messages[0].text
    assert envelope["max_tokens"] == 3072 and envelope["model"] == "사내-별칭"
    receipt = completion.final_response["model_input_trace"]["api_request_transport"]
    assert receipt == {"serializer": "chat-completions-utf8-v2", "model": "사내-별칭",
                       "body_bytes": len(request.body), "body_sha256": sha256(request.body).hexdigest()}
    assert int(request.headers["Content-Length"]) == len(request.body)
    assert completion.final_response["model_input_trace"]["input_tokens"] is None


class _CapturedStdin:
    def __init__(self):
        self.value = None
        self.closed = threading.Event()

    def write(self, value):
        self.value = value

    def close(self):
        self.closed.set()


class _CompletedProcess:
    def __init__(self):
        self.stdin = _CapturedStdin()
        self.stdout = [json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": "검증용 답변",
        }}) + "\n"]
        self.stderr = []

    def wait(self, timeout=None):
        assert self.stdin.closed.wait(timeout or 2)
        return 0

    def poll(self):
        return 0


def test_complete_answer_reaches_codex_stdin_without_hidden_60000_character_cutoff(tmp_path, monkeypatch):
    context, body = _long_context()
    process = _CompletedProcess()
    cfg = {"codex_cwd": str(tmp_path), "codex_sandbox": "read-only", "model": "codex-cli"}
    monkeypatch.setattr(internal_llm, "_candidate_configs", lambda *args, **kwargs: [("codex-local", cfg)])
    monkeypatch.setattr(codex_bridge, "_resolve_codex_bin", lambda: "codex")
    monkeypatch.setattr(codex_bridge, "_codex_cli_authenticated", lambda value: True)
    monkeypatch.setenv("CODE_ASSIST_CODEX_MAX_PROMPT_CHARS", "60000")
    captured_args = []

    def popen(args, **kwargs):
        captured_args.append(args)
        return process

    monkeypatch.setattr(codex_bridge.subprocess, "Popen", popen)
    result = CanonicalAgentTurnExecutor(provider_order=("codex",)).complete(context)
    prompt = process.stdin.value
    assert len(prompt) > 60000
    assert "context truncated for Codex CLI prompt budget" not in prompt
    payload = json.loads(prompt.split("\n## USER\n", 1)[1])
    assert payload["retrieval_candidates"][0]["excerpt_md"] == body
    assert payload["wiki_manifest"]["authorized_published_count"] == 1
    assert result.assistant_content == {"text": "검증용 답변"}
    assert "api_request_transport" not in result.final_response["model_input_trace"]
    assert "features.shell_tool=false" not in str(captured_args)
    assert 'web_search="disabled"' not in str(captured_args)


def test_legacy_bridge_limit_is_unchanged_but_complete_mode_retains_all_text(monkeypatch):
    monkeypatch.setenv("CODE_ASSIST_CODEX_MAX_PROMPT_CHARS", "60000")
    text = "a" * 45000 + "MIDDLE_MUST_SURVIVE" + "b" * 45000
    legacy = codex_bridge.build_codex_prompt([{"role": "user", "content": text}])
    complete = codex_bridge.build_codex_prompt([{"role": "user", "content": text}], preserve_input=True)
    assert "context truncated" in legacy and "MIDDLE_MUST_SURVIVE" not in legacy
    assert text in complete and "context truncated" not in complete


@pytest.mark.parametrize("value", [0, "true", None, 1])
def test_invalid_complete_input_policy_fails_before_provider_discovery(value, monkeypatch):
    monkeypatch.setattr(internal_llm, "_candidate_configs", lambda *args, **kwargs: pytest.fail("no discovery"))
    with pytest.raises(ValueError):
        internal_llm.run_internal_text("fixture", {}, preserve_codex_input=value)


@pytest.mark.parametrize("changes", [{"body_bytes": True}, {"body_sha256": "x" * 64},
                                    {"model": "x\nsecret"}, {"serializer": "unknown"}])
def test_invalid_transport_claim_cannot_be_persisted_as_a_receipt(changes):
    with pytest.raises(AnswerInputError):
        validated_api_receipt({"serializer": "chat-completions-utf8-v2", "model": "m",
                               "body_bytes": 10, "body_sha256": "a" * 64, **changes})


@pytest.mark.parametrize("status", [200, 500])
def test_actual_http_send_return_is_discarded_when_authority_changes(monkeypatch, status):
    changed = False
    requests_sent = []

    class ScopeChanged(RuntimeError):
        code = "SCOPE_STALE"

    failure = ScopeChanged("fixture authority changed during HTTP")

    def guard():
        if changed:
            raise failure

    monkeypatch.setattr(internal_llm, "_candidate_configs", lambda *args, **kwargs: [
        (model, {"url": "https://example.invalid/chat/completions", "model": model})
        for model in ("fixture", "fallback")
    ])
    monkeypatch.setattr(internal_llm, "resolve_api_token", lambda cfg: (None, None))

    def send(session, request, **kwargs):
        nonlocal changed
        requests_sent.append(request)
        assert isinstance(request, requests.PreparedRequest)
        envelope = json.loads(request.body)
        assert "attempt_guard" not in envelope
        assert "attempt_guard" not in json.loads(envelope["messages"][1]["content"])
        changed = True
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps({"choices": [{"message": {"content": "discard this answer"}}]}).encode()
        response.headers["Content-Type"] = "application/json"
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    with pytest.raises(ScopeChanged) as raised:
        CanonicalAgentTurnExecutor(provider_order=("hynix_api",)).complete(_context(), attempt_guard=guard)
    assert raised.value is failure
    assert len(requests_sent) == 1
