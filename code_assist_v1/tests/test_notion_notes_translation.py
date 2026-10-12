"""Translation boundary tests; all pages/users are isolated fixtures."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from code_assist_v1 import internal_llm, notion_store as ns
from code_assist_v1 import notion_notes_translation as translation
from code_assist_v1 import routes_notion_ai_notes as routes
from code_assist_v1.tests.test_notion_ai_notes import client


@pytest.fixture(autouse=True)
def models(monkeypatch):
    monkeypatch.setattr(translation.providers, "codex_connected", lambda: True)
    monkeypatch.setattr(translation.providers, "text_models", lambda: [
        ("gemma4", "hynix_api", {"name": "Gemma4"}),
        ("qwen3.6", "hynix_api", {"name": "Qwen3.6"}),
        ("codex-local", "codex", {"name": "Codex", "codex_cwd": "MUST_NOT_USE", "codex_sandbox": "workspace-write"}),
    ])


def request(http, page_id, **fields):
    return http.post("/api/notion/ai-notes/translate", json={
        "page_id": page_id, "text": "  [00:00] Hello.\n", "target_language": "한국어", **fields,
    })


def test_translates_exact_input_without_page_mutation(client):
    http, page_id = client
    before = ns.get_page("owner", page_id)
    with patch.object(internal_llm, "run_internal_text", return_value=(json.dumps({"translation": "[00:00] 안녕하세요."}), {})) as runner:
        reply = request(http, page_id, context="earlier sentence")
    assert reply.status_code == 200
    assert reply.headers["Cache-Control"] == "no-store"
    assert reply.json["text"] == "  [00:00] Hello.\n"
    assert reply.json["translation"] == "[00:00] 안녕하세요."
    assert len(reply.json["source_sha256"]) == 64
    assert ns.get_page("owner", page_id) == before
    assert runner.call_args.args[1] == {"text": "  [00:00] Hello.\n", "previous_context": "earlier sentence"}
    assert runner.call_args.kwargs["model_ids"] == ["gemma4"]
    assert "not a summary" in runner.call_args.kwargs["system_prompt"]


def test_fallback_codex_receives_text_in_ephemeral_readonly_directory(client):
    http, page_id = client
    directories = []

    def bridge(cfg, messages, **kwargs):
        directories.append(cfg["codex_cwd"])
        assert Path(cfg["codex_cwd"]).is_dir()
        assert cfg["codex_sandbox"] == "read-only" and cfg["codex_ephemeral"] == "1"
        assert cfg["codex_reasoning_effort"] == "low"
        assert kwargs["preserve_input"] is True
        assert callable(kwargs["attempt_guard"])
        assert json.loads(messages[1]["content"])["text"] == "  [00:00] Hello.\n"
        return json.dumps({"translation": "안녕하세요."})

    with patch.object(internal_llm, "run_internal_text", return_value=(None, {})) as api, patch.object(internal_llm, "_call_codex_text", side_effect=bridge):
        reply = request(http, page_id)
    assert reply.status_code == 200 and reply.json["provider"] == "codex"
    assert [call.kwargs["model_ids"] for call in api.call_args_list] == [["gemma4"], ["qwen3.6"]]
    assert all(not Path(directory).exists() for directory in directories)


def test_explicit_codex_calls_only_codex_and_rejects_unavailable_bridge(client, monkeypatch):
    http, page_id = client
    with patch.object(internal_llm, "run_internal_text") as api, patch.object(internal_llm, "_call_codex_text", return_value='{"translation":"직접 선택 번역"}') as bridge:
        response = request(http, page_id, model_id="codex-local")
        assert response.status_code == 200
        assert response.json["model_id"] == "codex-local"
        api.assert_not_called()
        assert bridge.call_count == 1
        assert bridge.call_args.args[0]["codex_sandbox"] == "read-only"
        assert bridge.call_args.args[0]["codex_reasoning_effort"] == "low"
        assert not Path(bridge.call_args.args[0]["codex_cwd"]).exists()
    with patch.object(internal_llm, "run_internal_text") as api, patch.object(internal_llm, "_call_codex_text", side_effect=RuntimeError("synthetic offline")):
        assert request(http, page_id, model_id="codex-local").status_code == 503
        api.assert_not_called()
    monkeypatch.setattr(translation.providers, "text_models", lambda: [])
    with patch.object(internal_llm, "_call_codex_text") as bridge:
        assert request(http, page_id, model_id="codex-local").status_code == 400
        assert request(http, page_id).status_code == 503
        bridge.assert_not_called()


def test_translation_low_effort_is_local_and_does_not_replace_model(client, monkeypatch):
    http, page_id = client
    original = {"name": "Synthetic", "codex_reasoning_effort": "xhigh", "codex_cli_model": "synthetic-existing-model"}
    monkeypatch.setattr(translation.providers, "text_models", lambda: [("codex-local", "codex", original)])
    with patch.object(internal_llm, "_call_codex_text", return_value='{"translation":"검증 번역"}') as bridge:
        assert request(http, page_id, model_id="codex-local").status_code == 200
    invocation = bridge.call_args.args[0]
    assert invocation["codex_reasoning_effort"] == "low"
    assert invocation["codex_cli_model"] == "synthetic-existing-model"
    assert original == {"name": "Synthetic", "codex_reasoning_effort": "xhigh", "codex_cli_model": "synthetic-existing-model"}


@pytest.mark.parametrize("model_id", ["", "codex-local"])
def test_disconnected_before_invocation_never_invokes_codex(client, monkeypatch, model_id):
    http, page_id = client
    monkeypatch.setattr(translation.providers, "codex_connected", lambda: False)
    with patch.object(internal_llm, "run_internal_text", return_value=(None, {})), patch.object(internal_llm, "_call_codex_text") as bridge:
        assert request(http, page_id, model_id=model_id).status_code == 503
        bridge.assert_not_called()


@pytest.mark.parametrize("fields", [
    {"text": ""}, {"text": "x" * 2001}, {"text": 2}, {"text": "x\0y"},
    {"target_language": ""}, {"target_language": "ko\nen"}, {"target_language": "x" * 81},
    {"context": "x" * 1201}, {"model_id": "arbitrary"},
])
def test_bad_input_never_calls_model(client, fields):
    http, page_id = client
    with patch.object(internal_llm, "run_internal_text") as api, patch.object(internal_llm, "_call_codex_text") as bridge:
        assert request(http, page_id, **fields).status_code == 400
        api.assert_not_called(); bridge.assert_not_called()


@pytest.mark.parametrize("role,status", [("view", 403), ("comment", 403), ("edit", 200)])
def test_translation_requires_page_edit(client, monkeypatch, role, status):
    http, page_id = client
    ns.share_page("owner", page_id, "collaborator", role=role)
    monkeypatch.setattr(routes, "current_user_id", lambda: "collaborator")
    with patch.object(internal_llm, "run_internal_text", return_value=('{"translation":"안녕"}', {})) as api:
        assert request(http, page_id).status_code == status
        assert api.call_count == (1 if status == 200 else 0)


def test_midcall_revocation_and_provider_errors_do_not_leak(client, monkeypatch):
    http, page_id = client
    ns.share_page("owner", page_id, "collaborator", role="edit")
    monkeypatch.setattr(routes, "current_user_id", lambda: "collaborator")
    def revoke(*args, **kwargs):
        ns.revoke_share("owner", page_id, "collaborator")
        raise RuntimeError("private upstream URL token secret")
    with patch.object(internal_llm, "run_internal_text", side_effect=revoke) as api, patch.object(internal_llm, "_call_codex_text") as bridge:
        reply = request(http, page_id)
        assert reply.status_code == 404
        assert api.call_count == 1
        bridge.assert_not_called()
        assert b"private upstream" not in reply.data


def test_invalid_output_safe_errors_auth_body_limit_and_slots(client, monkeypatch):
    http, page_id = client
    with patch.object(internal_llm, "run_internal_text", return_value=('{"summary":"wrong kind"}', {})), patch.object(internal_llm, "_call_codex_text", side_effect=RuntimeError("secret")):
        response = request(http, page_id)
        assert response.status_code == 503 and b"secret" not in response.data
    assert request(http, page_id, text="x" * 65000).status_code == 413
    routes._translation_slots.acquire(); routes._translation_slots.acquire()
    try:
        assert request(http, page_id).status_code == 429
    finally:
        routes._translation_slots.release(); routes._translation_slots.release()
    monkeypatch.setattr(routes, "current_user_id", lambda: "")
    assert request(http, page_id).status_code == 401


@pytest.mark.parametrize("pair", [["ko", "en"], ["ko", "ja"], ["en", "ja"]])
def test_pair_interprets_both_directions_in_one_call_with_exact_coverage(client, pair):
    http, page_id = client
    phrases = {"ko": "[00:00] 안녕하세요.\n", "en": "[00:04] Hello.\n", "ja": "[00:08] こんにちは。\n"}
    segments = [{"text": phrases[origin], "source_language": origin,
                 "target_language": pair[1 - pair.index(origin)], "translation": "합성 번역"}
                for origin in pair]
    source = "".join(segment["text"] for segment in segments)
    before = ns.get_page("owner", page_id)
    with patch.object(internal_llm, "run_internal_text", return_value=(json.dumps({"segments": segments}), {})) as runner:
        response = request(http, page_id, text=source, translation_mode="pair", language_pair=list(reversed(pair)))
    assert response.status_code == 200
    assert runner.call_count == 1
    assert response.json["language_pair"] == pair
    assert response.json["translation_mode"] == "pair"
    assert response.json["target_language"] == " ↔ ".join(translation.PAIR_LANGUAGES[code] for code in pair)
    for origin in pair:
        other = pair[1 - pair.index(origin)]
        assert f"[{translation.PAIR_LANGUAGES[origin]} → {translation.PAIR_LANGUAGES[other]}]" in response.json["translation"]
        assert phrases[origin] in response.json["translation"]
    assert "Concatenating every segment.text" in runner.call_args.kwargs["system_prompt"]
    assert ns.get_page("owner", page_id) == before


def test_pair_unknown_language_is_visible_without_invented_translation(client):
    http, page_id = client
    source = "... Bonjour."
    with patch.object(internal_llm, "run_internal_text", return_value=(json.dumps({"segments": [
        {"text": source, "source_language": "und", "target_language": "", "translation": ""},
    ]}), {})):
        response = request(http, page_id, text=source, translation_mode="pair", language_pair=["ko", "ja"])
    assert response.status_code == 200
    assert "언어 확인 필요" in response.json["translation"]
    assert response.json["translation"].endswith(source)


@pytest.mark.parametrize("fields", [
    {"translation_mode": "wrong"}, {"translation_mode": []},
    {"translation_mode": "single", "language_pair": ["ko", "en"]},
    *({"translation_mode": "pair", "language_pair": pair} for pair in
      (None, "ko,en", [], ["ko"], ["ko", "ko"], ["ko", "zh"], ["ko", "en", "ja"], ["ko", {}])),
])
def test_bad_pair_never_calls_provider(client, fields):
    http, page_id = client
    with patch.object(internal_llm, "run_internal_text") as runner, patch.object(internal_llm, "_call_codex_text") as bridge:
        assert request(http, page_id, **fields).status_code == 400
        runner.assert_not_called(); bridge.assert_not_called()


@pytest.mark.parametrize("changed", [
    {"text": "rewritten source"}, {"source_language": "ja"},
    {"target_language": "en"}, {"translation": ""}, {"source_language": {}},
    {"source_language": "und", "target_language": "", "translation": "invented"},
])
def test_pair_invalid_model_direction_or_coverage_is_not_returned(client, changed):
    http, page_id = client
    segment = {"text": "Hello.", "source_language": "en", "target_language": "ko", "translation": "안녕", **changed}
    with patch.object(internal_llm, "run_internal_text", return_value=(json.dumps({"segments": [segment]}), {})) as runner:
        response = request(http, page_id, text="Hello.", translation_mode="pair", language_pair=["ko", "en"], model_id="gemma4")
    assert response.status_code == 503
    assert runner.call_count == 1


def test_pair_codex_uses_existing_direct_selection_guard(client):
    http, page_id = client
    result = {"segments": [{"text": "Hello.", "source_language": "en", "target_language": "ja", "translation": "こんにちは。"}]}
    with patch.object(internal_llm, "run_internal_text") as runner, patch.object(internal_llm, "_call_codex_text", return_value=json.dumps(result)) as bridge:
        response = request(http, page_id, text="Hello.", translation_mode="pair", language_pair=["en", "ja"], model_id="codex-local")
    assert response.status_code == 200
    runner.assert_not_called()
    assert bridge.call_count == 1
    assert bridge.call_args.args[0]["codex_sandbox"] == "read-only"
