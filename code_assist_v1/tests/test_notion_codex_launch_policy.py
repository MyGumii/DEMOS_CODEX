"""Launch argument, capability and fallback order; no real model calls."""
from unittest.mock import patch

import pytest

from code_assist_v1 import internal_llm, notion_meeting_providers as providers
from code_assist_v1 import notion_ai_notes as notes, notion_audio
from code_assist_v1 import codex_bridge
from code_assist_v1.model_routing import apply_codex_launch_policy
from code_assist_v1.scripts.run_notion_local_stt_test import app_command
from code_assist_v1.tests.test_notion_ai_notes import client


def test_launcher_forwards_only_explicit_codex_option():
    assert "--codex" not in app_command("synthetic-python")
    assert app_command("synthetic-python", codex=True)[-1] == "--codex"
    assert app_command("synthetic-python", codex=False) == ["synthetic-python", "-u", "-m", "code_assist_v1.app_notion", "--port", "10322"]


@pytest.mark.parametrize("enabled,authenticated,expected", [(False, True, False), (True, False, False), (True, True, True)])
def test_real_launch_gate_precedes_cli_auth_probe(monkeypatch, enabled, authenticated, expected):
    monkeypatch.setenv("CODE_ASSIST_ENABLE_CODEX_BRIDGE", "1")
    apply_codex_launch_policy(enabled)
    with patch.object(codex_bridge, "_resolve_codex_bin", return_value="synthetic-codex") as resolve, patch.object(codex_bridge, "_codex_cli_authenticated", return_value=authenticated):
        assert providers.codex_connected() is expected
        assert resolve.call_count == int(enabled)


@pytest.mark.parametrize("enabled", [False, True])
def test_capabilities_use_auto_and_argument_gate_even_with_local_stt(client, monkeypatch, enabled):
    http, _page_id = client
    monkeypatch.setenv("CODE_ASSIST_ENABLE_CODEX_BRIDGE", "1" if enabled else "0")
    monkeypatch.setattr(codex_bridge, "_resolve_codex_bin", lambda: "synthetic-codex")
    monkeypatch.setattr(codex_bridge, "_codex_cli_authenticated", lambda _binary: True)
    def configs(lane, model_ids=None):
        if lane == "hynix_api":
            return [("qwen3.6", {"model": "qwen3.6"}), ("gemma4", {"model": "gemma4"})]
        return [("codex-local", {"model": "codex-cli"})]
    monkeypatch.setattr(internal_llm, "_candidate_configs", configs)
    monkeypatch.setattr(notes, "available_models", lambda: [{"id": mid, "name": mid, "provider": lane} for mid, lane, _cfg in providers.text_models()])
    monkeypatch.setattr(notion_audio, "capabilities", lambda: {"enabled": True, "local_only": True})
    response = http.get("/api/notion/ai-notes/capabilities")
    assert response.status_code == 200
    assert [model["id"] for model in response.json["models"]] == ["gemma4", "qwen3.6"] + (["codex-local"] if enabled else [])
    assert response.json["translation"]["default_model_id"] == ""
    assert response.json["codex_bridge"] == {"enabled": enabled, "connected": enabled}


@pytest.mark.parametrize("enabled", [False, True])
def test_summary_direct_codex_still_requires_real_launch_gate(client, monkeypatch, enabled):
    http, page_id = client
    monkeypatch.setenv("CODE_ASSIST_ENABLE_CODEX_BRIDGE", "1")
    apply_codex_launch_policy(enabled)
    monkeypatch.setattr(codex_bridge, "_resolve_codex_bin", lambda: "synthetic-codex")
    monkeypatch.setattr(codex_bridge, "_codex_cli_authenticated", lambda _binary: True)
    monkeypatch.setattr(internal_llm, "_candidate_configs", lambda lane, model_ids=None:
                        [("codex-local", {"model": "codex-cli"})] if lane == "codex" else [("gemma4", {"model": "gemma4"})])
    monkeypatch.setattr(notes, "available_models", lambda: [{"id": mid, "name": mid, "provider": lane} for mid, lane, _cfg in providers.text_models()])
    with patch.object(internal_llm, "run_internal_text", return_value=('{"summary":"Synthetic summary","decisions":[],"action_items":[]}', {"model_id": "codex-local"})) as runner:
        response = http.post("/api/notion/ai-notes/summarize", json={"page_id": page_id, "transcript": "Synthetic source.", "model_id": "codex-local"})
    assert response.status_code == (200 if enabled else 400)
    if enabled:
        assert runner.call_args.kwargs["model_ids"] == ["codex-local"]
    else:
        runner.assert_not_called()
