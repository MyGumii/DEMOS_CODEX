"""Exact OpenAI-compatible request bytes, not a mocked authoring transport."""
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from code_assist_v1 import internal_llm


@pytest.mark.parametrize("content", [
    "한글 문서" * 8_000, "🙂" * 6_144, "👩‍🔬e\u0301" * 2_000,
    '\\"\r\n\x01' * 4_000,
], ids=["korean", "emoji", "zwj-combining", "escaped"])
def test_exact_transport_body_is_measured_hashed_and_sent_without_reserialization(monkeypatch, content):
    sent = []
    def post(url, **kwargs):
        assert "json" not in kwargs
        assert kwargs["headers"]["Content-Type"] == "application/json"
        sent.append(kwargs["data"])
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"content": "ok"}}]})
    monkeypatch.setattr(internal_llm.req, "post", post)
    monkeypatch.setattr(internal_llm, "resolve_api_token", lambda cfg: (None, None))
    messages = [{"role": "user", "content": content}]
    cfg = {"url": "https://example.invalid/chat/completions", "model": "compatible-모델🙂", "tls_verify": True}
    receipt = {}
    internal_llm._call_api_text(cfg, messages, 8192, 0.1,
                                request_budget_bytes=2_000_000, request_receipt=receipt)
    body = sent[0]
    assert json.loads(body)["messages"] == messages
    assert receipt == {"serializer": "chat-completions-utf8-v2", "model": cfg["model"], "body_bytes": len(body),
                       "body_sha256": sha256(body).hexdigest()}
    for difference in (0, 1):
        internal_llm._call_api_text(cfg, messages, 8192, 0.1,
                                    request_budget_bytes=len(body) + difference)
        assert sent[-1] == body
    before = len(sent)
    with pytest.raises(ValueError, match="transport-byte"):
        internal_llm._call_api_text(cfg, messages, 8192, 0.1, request_budget_bytes=len(body) - 1)
    assert len(sent) == before


def test_existing_api_callers_keep_their_payload_behavior(monkeypatch):
    sent = []
    monkeypatch.setattr(internal_llm, "resolve_api_token", lambda cfg: (None, None))
    def post(url, **kwargs):
        sent.append(kwargs)
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"content": "legacy"}}]})
    monkeypatch.setattr(internal_llm.req, "post", post)
    assert internal_llm._call_api_text({"url": "https://example.invalid", "model": "m"}, [], 10, 0.1) == "legacy"
    assert "json" in sent[0] and "data" not in sent[0]


def test_runner_fallback_has_an_explicit_attempt_limit(monkeypatch):
    attempts = []
    monkeypatch.setattr(internal_llm, "_candidate_configs", lambda *a, **kw:
                        [(str(i), {"model": str(i)}) for i in range(20)])
    def api(*args, **kwargs):
        attempts.append(kwargs)
        raise TimeoutError("fixture")
    monkeypatch.setattr(internal_llm, "_call_api_text", api)
    result, metadata = internal_llm.run_internal_text("bounded", {}, provider_order=("hynix_api",),
                                                     request_budget_bytes=65_536, max_attempts=2)
    assert result is None and len(attempts) == 2
    assert all(row["request_budget_bytes"] == 65_536 for row in attempts)


def test_authoring_uses_real_common_adapter_and_retains_its_exact_transport_receipt(monkeypatch):
    from code_assist_v1.knowledge_core.wiki_authoring import (
        ExtractionSource, AuthoringModelPolicy, plan_authoring, WikiChunkAuthor, assemble_wiki,
    )
    source_text = '한글🙂 "역슬래시\\" 조건\n' * 2_000
    source = ExtractionSource(str(uuid4()), sha256(source_text.encode()).hexdigest(), source_text)
    plan = plan_authoring(source, model_policy=AuthoringModelPolicy(model_ids=("fixture",)))
    monkeypatch.setattr(internal_llm, "_candidate_configs", lambda *a, **kw:
        [("fixture", {"url": "https://example.invalid", "model": "company-alias"})])
    monkeypatch.setattr(internal_llm, "resolve_api_token", lambda cfg: (None, None))
    sent = []
    def post(url, **kwargs):
        sent.append(kwargs["data"])
        envelope = json.loads(kwargs["data"])
        payload = json.loads(envelope["messages"][1]["content"])
        response = json.dumps({"schema_version": 1, "chunk_id": payload["chunk_id"],
            "sections": [{"heading": "범위", "claims": [{"text": "원문 조건을 보존한다.",
                         "quotes": [payload["source"]]}]}], "warnings": []}, ensure_ascii=False)
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"content": response}}]})
    monkeypatch.setattr(internal_llm.req, "post", post)
    author = WikiChunkAuthor(runner=internal_llm.run_internal_text)
    results = [author.author(plan, chunk.ordinal) for chunk in plan.chunks]
    wiki = assemble_wiki(plan, results)
    manifest = json.loads(wiki.manifest_json)
    assert len(sent) == len(manifest["chunks"])
    for body, chunk in zip(sent, manifest["chunks"], strict=True):
        assert len(body) <= plan.model_policy.request_budget_bytes
        assert chunk["request_transport"]["body_sha256"] == sha256(body).hexdigest()
        assert chunk["request_transport"]["body_bytes"] == len(body)
        assert chunk["request_transport"]["model"] == "company-alias"
        assert chunk["model"]["model_kind"] == "hynix_api"
        assert chunk["model"]["model_id"] == "fixture"


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_attempt_budget_fails_before_provider_discovery(value, monkeypatch):
    monkeypatch.setattr(internal_llm, "_candidate_configs", lambda *a, **kw: pytest.fail("no discovery"))
    with pytest.raises(ValueError):
        internal_llm.run_internal_text("bounded", {}, max_attempts=value)
