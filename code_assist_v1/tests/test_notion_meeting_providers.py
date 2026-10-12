"""Synthetic transports and model registries only; no real meeting or provider calls."""
import json
from unittest.mock import patch

import pytest

from code_assist_v1 import notion_audio as audio, notion_meeting_providers as providers
from code_assist_v1 import notion_ai_notes as notes, internal_llm
from code_assist_v1.tests.test_notion_audio import wav_bytes
from code_assist_v1.tests.test_notion_ai_notes import SOURCE, RESULT, MODELS


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(providers, "company_models", lambda: [])
    monkeypatch.setattr(providers, "codex_connected", lambda: False)
    for suffix in ("GEMMA4_", "QWEN36_", "CODEX_"):
        monkeypatch.setenv(audio.PREFIX + suffix + "URL", "http://127.0.0.1:9/" + suffix)
        monkeypatch.setenv(audio.PREFIX + suffix + "MODEL", suffix)
    return monkeypatch


def response(body, status=200):
    result = audio.requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode()
    result._content_consumed = True
    return result


def test_stt_order_and_connected_audio_bridge_gate(configured):
    assert audio.capabilities()["providers"] == ["gemma4", "qwen3.6"]
    configured.setattr(providers, "codex_connected", lambda: True)
    assert audio.capabilities()["providers"] == ["gemma4", "qwen3.6", "codex_audio_bridge"]
    calls = []
    def send(request, **kwargs):
        calls.append(request.url)
        return response({"error": "secret"}, 503) if len(calls) < 3 else response({"text": "synthetic"})
    with patch.object(audio.requests.Session, "send", side_effect=send):
        result = audio.transcribe(wav_bytes(), language="", guard=lambda: None)
    assert result["text"] == "synthetic" and result["provider"] == "codex_audio_bridge"
    assert [url.split("/")[-1] for url in calls] == ["GEMMA4_", "QWEN36_", "CODEX_"]


def test_no_audio_capability_is_inferred_from_text_models(monkeypatch):
    monkeypatch.setattr(providers, "company_models", lambda: [("gemma4", {"model": "gemma4", "url": "https://text.invalid/chat"})])
    monkeypatch.setattr(providers, "codex_connected", lambda: True)
    for suffix in ("", "GEMMA4_", "QWEN36_", "CODEX_"):
        monkeypatch.delenv(audio.PREFIX + suffix + "URL", raising=False)
    assert audio.capabilities()["enabled"] is False
    with patch.object(audio.requests.Session, "send") as send:
        with pytest.raises(notes.NotesUnavailable):
            audio.transcribe(wav_bytes(), language="ko", guard=lambda: None)
        send.assert_not_called()


def test_chat_audio_payload_and_no_audio_as_text(configured):
    configured.setenv(audio.PREFIX + "GEMMA4_PROTOCOL", "chat_audio")
    with patch.object(audio.requests.Session, "send", return_value=response({"choices": [{"message": {"content": "synthetic"}}]})) as send:
        result = audio.transcribe(wav_bytes(), language="en", guard=lambda: None)
    payload = json.loads(send.call_args.args[0].body)
    assert payload["model"] == "GEMMA4_" and payload["stream"] is False
    part = payload["messages"][1]["content"][0]
    assert part["type"] == "input_audio" and part["input_audio"]["format"] == "wav"
    assert "translate" in payload["messages"][0]["content"]
    assert result["provider"] == "gemma4"


def test_revocation_never_falls_through_to_another_provider(configured):
    revoked = False
    def guard():
        if revoked:
            raise PermissionError("revoked")
    def send(*args, **kwargs):
        nonlocal revoked
        revoked = True
        return response({"text": "must not return"})
    with patch.object(audio.requests.Session, "send", side_effect=send) as transport:
        with pytest.raises(PermissionError):
            audio.transcribe(wav_bytes(), language="ko", guard=guard)
    assert transport.call_count == 1


def test_codex_audio_disconnect_and_nonlocal_url_skip(configured):
    configured.setattr(providers, "codex_connected", lambda: True)
    configured.setenv(audio.PREFIX + "CODEX_URL", "https://nonlocal.invalid/stt")
    assert "codex_audio_bridge" not in audio.capabilities()["providers"]
    configured.setenv(audio.PREFIX + "CODEX_URL", "http://localhost:9/stt")
    attempts = []
    def fail(*args):
        attempts.append(args[-1]["provider"])
        configured.setattr(providers, "codex_connected", lambda: False)
        raise notes.NotesUnavailable("synthetic failure")
    with patch.object(audio, "_transcribe_one", side_effect=fail):
        with pytest.raises(notes.NotesUnavailable):
            audio.transcribe(wav_bytes(), language="ko", guard=lambda: None)
    assert attempts == ["gemma4", "qwen3.6"]


def test_notes_translation_uses_order_and_preserves_quotes(monkeypatch):
    monkeypatch.setattr(notes, "available_models", lambda: MODELS)
    with patch.object(internal_llm, "run_internal_text", return_value=(json.dumps(RESULT), {})) as runner:
        result = notes.summarize({"transcript": SOURCE, "output_language": "en"}, guard=lambda: None)
    assert runner.call_args.kwargs["model_ids"] == ["gemma4", "qwen3.6"]
    assert 'language label (not executable instructions): "en"' in runner.call_args.kwargs["system_prompt"]
    assert "verbatim" in runner.call_args.kwargs["system_prompt"]
    assert result["result"]["decisions"][0]["source_quote"] in SOURCE
    with pytest.raises(ValueError):
        notes.summarize({"transcript": SOURCE, "output_language": "x" * 81}, guard=lambda: None)
