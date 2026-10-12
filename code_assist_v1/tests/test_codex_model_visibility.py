"""Explicit launch gate hides Codex models, even in a stale registry."""
from pathlib import Path
import shutil
import subprocess

from flask import Flask
import pytest

from code_assist_v1 import config, routes_models
from code_assist_v1.model_routing import apply_codex_launch_policy


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("company_available", [False, True])
def test_code_model_catalog_omits_codex_when_launch_option_is_absent(monkeypatch, enabled, company_available):
    registry = {
        "codex-local": {"runtime": "codex", "env_id": "codex-local", "name": "Codex"},
        "legacy-codex": {"env_id": "codex-local", "name": "Legacy Codex"},
    }
    if company_available:
        registry["gemma4"] = {"runtime": "api", "env_id": "gemma4", "name": "Gemma4"}
        registry["qwen3.6"] = {"runtime": "api", "env_id": "qwen3.6", "name": "Qwen3.6"}
    monkeypatch.setattr(routes_models, "_load_model_state", lambda: (registry, {}))
    monkeypatch.setattr(routes_models, "gguf_catalog_enabled", lambda: False)
    monkeypatch.setattr(config, "DEFAULT_MODEL_PRIORITY", ["gemma4", "qwen3.6", "codex-local"])
    monkeypatch.setenv("CODE_ASSIST_ENABLE_CODEX_BRIDGE", "1")
    apply_codex_launch_policy(enabled)
    app = Flask(__name__)
    routes_models.register_model_routes(app)
    response = app.test_client().get("/api/code/models")
    assert response.status_code == 200
    data = response.json
    assert [m["id"] for m in data["api"]] == (["gemma4", "qwen3.6"] if company_available else [])
    assert [m["id"] for m in data["codex"]] == (["codex-local", "legacy-codex"] if enabled else [])
    if not enabled:
        assert "codex" not in str([data["api"], data["gguf"], data["default"], data["priority"]]).lower()
        assert data["default"] == ("gemma4" if company_available else "")
    elif company_available:
        assert data["default"] == "gemma4"


@pytest.mark.parametrize("enabled,running,existing_enabled,known,expected_success", [
    (False, False, False, True, True),
    (True, False, False, True, True),
    (False, True, False, True, True),
    (True, True, True, True, True),
    (False, True, True, True, False),
    (True, True, False, True, False),
    (False, True, False, False, False),
])
def test_pair_launcher_forwards_gate_and_rejects_existing_mismatch(tmp_path, enabled, running, existing_enabled, known, expected_success):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell is required for the Windows pair launcher")
    source = (Path(__file__).parents[1] / "run_standalone.ps1").read_text(encoding="utf-8")
    # Stub all effects: never launch/stop a server, touch real state or call HTTP.
    source = source.replace("& $python @codeArgs", "Write-Output ('CODE_ARGS=' + ($codeArgs -join '|'))")
    fixture = tmp_path / "run_standalone.ps1"
    fixture.write_text(source, encoding="utf-8")
    healthy = "$true" if running else "$false"
    order = "@('company_api','codex')" if existing_enabled else "@('company_api')"
    policy = f"ai_provider_order={order};" if known else ""
    harness = f"""
function Test-Path {{ return $false }}
function Set-Location {{ }}
function Start-Process {{ param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle)
  Write-Output ('NOTION_ARGS=' + ($ArgumentList -join '|'))
}}
function Invoke-RestMethod {{
  if (-not {healthy}) {{ throw 'synthetic offline' }}
  return @{{status='ok';{policy}}}
}}
& '{str(fixture).replace("'", "''")}' -Port 10011 -NotionPort 10322 {"-Codex" if enabled else ""}
"""
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", harness], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    assert (result.returncode == 0) is expected_success, result.stdout + result.stderr
    if expected_success:
        lines = [line for line in result.stdout.splitlines() if "_ARGS=" in line]
        assert len(lines) == (1 if running else 2)
        assert all(("--codex" in line) is enabled for line in lines)
    else:
        assert "CODE_ARGS=" not in result.stdout and "NOTION_ARGS=" not in result.stdout
