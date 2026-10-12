from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.integration


_ENVIRONMENT_KEYS = (
    "CODE_ASSIST_ALLOWED_ORIGINS",
    "CODE_ASSIST_API_CONFIG",
    "CODE_ASSIST_AUTOLOAD_GGUF",
    "CODE_ASSIST_BROWSER_SECURITY_PRODUCTION",
    "CODE_ASSIST_CODEX_CWD",
    "CODE_ASSIST_CODEX_EPHEMERAL",
    "CODE_ASSIST_CODEX_SANDBOX",
    "CODE_ASSIST_DATA_ROOT",
    "CODE_ASSIST_ENABLE_CODEX_BRIDGE",
    "CODE_ASSIST_PORT",
    "CODE_ASSIST_PROCESS_ROLE",
    "CODE_ASSIST_STANDALONE",
    "CODE_ASSIST_TEST_DATA_ROOT",
    "CODE_ASSIST_TEST_MODE",
)


def test_isolated_local_agent_gate_uses_one_part_and_no_company_api(
    tmp_path: Path,
) -> None:
    root = tmp_path / "local-second-brain"
    source = f"""
import json
from pathlib import Path
from code_assist_v1.tools.local_second_brain_validation import (
    build_validation_app,
    initialize_fixture,
    run_functional_gate,
)

def fake_runner(*args, **kwargs):
    assert kwargs["provider_order"] == ["codex"]
    assert kwargs["model_ids"] == ["codex-local"]
    prompt = json.dumps(kwargs["messages"], ensure_ascii=False)
    assert "FOREIGN-CANARY-4821" not in prompt
    assert "PERSONAL-CANARY-2640" not in prompt
    assert "ORBIT-COPPER-7319" in prompt
    return "ORBIT-COPPER-7319", {{
        "fallback_errors": [],
        "model_id": "codex-local",
        "model_kind": "codex",
    }}

factory, marker, password = initialize_fixture(
    Path({os.fspath(root)!r}),
    port=10111,
    password="LocalSecondBrain-2026!",
)
assert password == "LocalSecondBrain-2026!"
assert marker["mutation_flags_enabled"] is False
assert marker["model_provider_order"] == ["codex"]
app = build_validation_app(factory, marker, model_runner=fake_runner)
result = run_functional_gate(app, password=password, port=10111)
print(json.dumps({{"marker": marker, "result": result}}, sort_keys=True))
"""
    environment = dict(os.environ)
    for name in _ENVIRONMENT_KEYS:
        environment.pop(name, None)
    completed = subprocess.run(
        [sys.executable, "-E", "-c", source],
        cwd=Path(__file__).resolve().parents[4],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["result"] == {
        "answer_sha256": "deed7b9a9aa1bd34f15babce787c3f5b7a6105971447879580e227bf10e2b693",
        "company_api_called": False,
        "conversation_id": payload["result"]["conversation_id"],
        "foreign_marker_absent": True,
        "model_provider": "codex",
        "part_marker_present": True,
        "part_space_id": payload["marker"]["fixture_ids"]["part-space-a"],
        "status": "pass",
    }
    assert Path(payload["marker"]["data_root"]).resolve() == root.resolve()
    assert payload["marker"]["database"].startswith(os.fspath(root.resolve()))


def test_cli_reopens_only_a_hash_bound_isolated_fixture(tmp_path: Path) -> None:
    root = tmp_path / "local-second-brain-cli"
    environment = dict(os.environ)
    for name in _ENVIRONMENT_KEYS:
        environment.pop(name, None)
    command = [
        sys.executable,
        "-E",
        "-m",
        "code_assist_v1.tools.local_second_brain_validation",
        "--data-root",
        os.fspath(root),
        "--mode",
        "initialize",
        "--password",
        "LocalSecondBrain-2026!",
        "--port",
        "10112",
    ]
    first = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[4],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    second = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[4],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    assert first.returncode == second.returncode == 0
    first_payload = json.loads(first.stdout.strip().splitlines()[-1])
    second_payload = json.loads(second.stdout.strip().splitlines()[-1])
    assert first_payload["fixture_sha256"] == second_payload["fixture_sha256"]
    assert first_payload["password"] == "LocalSecondBrain-2026!"
    assert second_payload["password"] is None
    assert first_payload["url"] == "http://127.0.0.1:10112/"
