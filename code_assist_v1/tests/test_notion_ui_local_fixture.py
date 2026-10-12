from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from code_assist_v1.test_safety import UnsafeTestDataPathError
from code_assist_v1.notion_core_ui_pilot import (
    PILOT_ENABLE_ENV,
    PILOT_KILL_SWITCH_ENV,
    PILOT_SUBJECTS_ENV,
    PILOT_WORKSPACES_ENV,
)
from code_assist_v1.tools.notion_parity.ui_local_fixture import (
    build_fixture,
    inspect_runtime_paths,
    isolated_environment,
    main,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _fixture(tmp_path: Path):
    return build_fixture(
        data_root=tmp_path / "ui1-local",
        repository_root=PROJECT_ROOT,
        port=_free_port(),
    )


@pytest.mark.unit
def test_fixture_descriptor_is_identifier_and_path_free(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    encoded = json.dumps(fixture.descriptor(), sort_keys=True)

    assert fixture.descriptor() == {
        "contract": "notion_core_free_ui_local_fixture_v1",
        "profile_id": "core_free_owner_chrome_desktop_ko_kr",
        "origin_alias": "LOCAL-UI1",
        "surface": "notion",
        "isolated_data_root": True,
        "synthetic_only": True,
        "test_mode": True,
        "live_local_data_accessed": False,
        "token_available": False,
    }
    assert str(fixture.root) not in encoded
    assert str(PROJECT_ROOT) not in encoded


@pytest.mark.unit
def test_environment_is_complete_and_removes_credentials(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    environment = isolated_environment(
        fixture,
        base_environ={
            "PATH": os.environ.get("PATH", ""),
            "OPENAI_API_KEY": "must-not-survive",
            "CODEX_TOKEN": "must-not-survive",
            PILOT_ENABLE_ENV: "1",
            PILOT_KILL_SWITCH_ENV: "0",
            PILOT_SUBJECTS_ENV: "unexpected-user",
            PILOT_WORKSPACES_ENV: "ws_unexpected",
        },
    )

    assert "OPENAI_API_KEY" not in environment
    assert "CODEX_TOKEN" not in environment
    assert environment["CODE_ASSIST_DATA_ROOT"] == os.fspath(fixture.root)
    assert environment["CODE_ASSIST_TEST_DATA_ROOT"] == os.fspath(fixture.root)
    assert environment["CODE_ASSIST_ALLOWED_ORIGINS"] == (
        f"{fixture.origin},http://localhost:{fixture.port}"
    )
    assert environment["CODE_ASSIST_NOTION_PORT"] == str(fixture.port)
    for key in ("TEMP", "TMP", "TMPDIR"):
        assert environment[key] == os.fspath(fixture.root)
    for key in (
        PILOT_ENABLE_ENV,
        PILOT_KILL_SWITCH_ENV,
        PILOT_SUBJECTS_ENV,
        PILOT_WORKSPACES_ENV,
    ):
        assert key not in environment


@pytest.mark.unit
def test_navigation_tree_seed_is_explicit_isolated_and_mutually_exclusive(
    tmp_path: Path,
) -> None:
    fixture = build_fixture(
        data_root=tmp_path / "ui-tree-local",
        repository_root=PROJECT_ROOT,
        port=_free_port(),
        seed_navigation_tree=True,
    )
    environment = isolated_environment(fixture, base_environ={})

    assert fixture.seed_navigation_tree is True
    assert fixture.seed_navigation_workspaces is False
    assert environment["CODE_ASSIST_NP2B_NAVIGATION_UI"] == "1"
    assert environment[PILOT_ENABLE_ENV] == "1"
    assert environment[PILOT_SUBJECTS_ENV] == "ui1-owner"
    assert PILOT_KILL_SWITCH_ENV not in environment
    assert PILOT_WORKSPACES_ENV not in environment
    with pytest.raises(ValueError, match="mutually exclusive"):
        build_fixture(
            data_root=tmp_path / "invalid-tree-local",
            repository_root=PROJECT_ROOT,
            port=_free_port(),
            seed_navigation_workspaces=True,
            seed_navigation_tree=True,
        )


@pytest.mark.unit
def test_fixture_rejects_workspace_overlap_and_invalid_ports() -> None:
    with pytest.raises(UnsafeTestDataPathError):
        build_fixture(
            data_root=PROJECT_ROOT,
            repository_root=PROJECT_ROOT,
            port=14126,
        )
    with pytest.raises(ValueError, match="port"):
        build_fixture(
            data_root=Path(os.environ["CODE_ASSIST_TEST_DATA_ROOT"]),
            repository_root=PROJECT_ROOT,
            port=True,
        )


@pytest.mark.unit
def test_runtime_path_proof_is_exact_and_token_free(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    report = inspect_runtime_paths(fixture)

    assert json.loads(
        (fixture.root / ".runtime" / "api-config.json").read_text(encoding="utf-8")
    ) == {}
    assert report == {
        "contract": "notion_core_free_ui_local_path_proof_v1",
        "isolated": True,
        "path_keys": [
            "AUTH_SECRET_FILE",
            "AUTH_USERS_FILE",
            "DATA_ROOT",
            "KNOWLEDGE_DIR",
            "RUNTIME_DIR",
            "SESSIONS_DIR",
            "TOKEN_FILE",
            "USER_DATA_DIR",
            "WORKSPACE_DIR",
        ],
        "token_available": False,
    }


@pytest.mark.unit
def test_cli_check_requires_explicit_test_confirmation(tmp_path: Path) -> None:
    with pytest.raises(UnsafeTestDataPathError, match="test-mode-confirmed"):
        main(
            [
                "check",
                "--data-root",
                os.fspath(tmp_path / "ui1-local"),
                "--port",
                str(_free_port()),
            ]
        )


@pytest.mark.integration
def test_cli_serves_health_only_from_isolated_runtime(tmp_path: Path) -> None:
    root = tmp_path / "ui1-browser-runtime"
    port = _free_port()
    command = [
        sys.executable,
        "-m",
        "code_assist_v1.tools.notion_parity.ui_local_fixture",
        "serve",
        "--data-root",
        os.fspath(root),
        "--port",
        str(port),
        "--repository-root",
        os.fspath(PROJECT_ROOT),
        "--test-mode-confirmed",
    ]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.fspath(PROJECT_ROOT)
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=(
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            if sys.platform == "win32"
            else 0
        ),
    )
    health = None
    try:
        for _ in range(120):
            if process.poll() is not None:
                break
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/healthz",
                    timeout=1,
                ) as response:
                    health = json.load(response)
                break
            except Exception:
                time.sleep(0.25)
        assert health == {
            "ai_fallback": "local_extractive",
            "ai_provider": "hynix_gemma4",
            "ai_provider_order": ["company_api", "local_gguf"],
            "mode": "standalone",
            "service": "notion_workspace",
            "status": "ok",
        }
        assert process.poll() is None
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


@pytest.mark.unit
def test_fixture_module_import_has_no_app_or_filesystem_side_effects(
    tmp_path: Path,
) -> None:
    script = r'''
import json
from pathlib import Path
import sys

root = Path.cwd()
before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
import code_assist_v1.tools.notion_parity.ui_local_fixture  # noqa: F401
after = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
forbidden = sorted(
    name for name in sys.modules
    if name in {
        "code_assist_v1.app_code",
        "code_assist_v1.auth",
        "code_assist_v1.config",
        "code_assist_v1.data_paths",
        "code_assist_v1.knowledge_core",
    }
    or name.startswith("code_assist_v1.routes_")
    or name.startswith("code_assist_v1.notion_workspace")
)
print(json.dumps({"changed": before != after, "forbidden": forbidden}, sort_keys=True))
'''
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.fspath(PROJECT_ROOT)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"changed": False, "forbidden": []}
