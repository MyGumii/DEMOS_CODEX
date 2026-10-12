from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.data_paths import (
    DataRootConfigurationError,
    resolve_runtime_data_paths,
)


@pytest.mark.unit
def test_default_layout_preserves_integrated_and_standalone_paths(tmp_path: Path) -> None:
    package = tmp_path / "repo" / "code_assist_v1"
    repository = package.parent

    integrated = resolve_runtime_data_paths(
        package_dir=str(package),
        repository_root=str(repository),
        standalone=False,
        environ={},
    )
    standalone = resolve_runtime_data_paths(
        package_dir=str(package),
        repository_root=str(repository),
        standalone=True,
        environ={},
    )

    assert integrated.knowledge_dir == str(repository / "knowledge")
    assert standalone.knowledge_dir == str(package / "knowledge")
    assert integrated.user_data_dir == str(package / "user_data")
    assert integrated.data_root is None
    assert not integrated.override_active


@pytest.mark.unit
def test_explicit_root_contains_every_mutable_path(tmp_path: Path) -> None:
    root = tmp_path / "isolated"
    paths = resolve_runtime_data_paths(
        package_dir=str(tmp_path / "package"),
        repository_root=str(tmp_path),
        standalone=False,
        environ={"CODE_ASSIST_DATA_ROOT": str(root)},
    )

    assert paths.override_active
    assert paths.data_root == str(root.resolve())
    for value in (
        paths.runtime_dir,
        paths.knowledge_dir,
        paths.sessions_dir,
        paths.workspace_dir,
        paths.user_data_dir,
    ):
        assert Path(value).resolve().is_relative_to(root.resolve())


@pytest.mark.unit
@pytest.mark.parametrize("value", ["relative", ".", "folder/data"])
def test_explicit_root_rejects_relative_paths(tmp_path: Path, value: str) -> None:
    with pytest.raises(DataRootConfigurationError, match="absolute"):
        resolve_runtime_data_paths(
            package_dir=str(tmp_path / "package"),
            repository_root=str(tmp_path),
            standalone=False,
            environ={"CODE_ASSIST_DATA_ROOT": value},
        )


@pytest.mark.unit
def test_explicit_root_rejects_filesystem_root(tmp_path: Path) -> None:
    anchor = Path(tmp_path.anchor)
    with pytest.raises(DataRootConfigurationError, match="filesystem root"):
        resolve_runtime_data_paths(
            package_dir=str(tmp_path / "package"),
            repository_root=str(tmp_path),
            standalone=False,
            environ={"CODE_ASSIST_DATA_ROOT": str(anchor)},
        )


@pytest.mark.unit
def test_explicit_root_rejects_existing_file(tmp_path: Path) -> None:
    file_path = tmp_path / "not-a-directory"
    file_path.write_text("fixture", encoding="utf-8")
    with pytest.raises(DataRootConfigurationError, match="directory path"):
        resolve_runtime_data_paths(
            package_dir=str(tmp_path / "package"),
            repository_root=str(tmp_path),
            standalone=False,
            environ={"CODE_ASSIST_DATA_ROOT": str(file_path)},
        )


@pytest.mark.integration
def test_config_and_outlook_imports_bind_to_override_before_store_import(
    tmp_path: Path,
) -> None:
    isolated = tmp_path / "subprocess-data"
    token_file = isolated / ".runtime" / "test-token.txt"
    api_config_file = isolated / ".runtime" / "api-config.json"
    api_config_file.parent.mkdir(parents=True, exist_ok=True)
    api_config_file.write_text("{}\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "CODE_ASSIST_DATA_ROOT": str(isolated),
            "CODE_ASSIST_STANDALONE": "1",
            "CODE_ASSIST_TOKEN_FILE": str(token_file),
            "CODE_ASSIST_API_CONFIG": str(api_config_file),
            "CODE_ASSIST_MS_TOKEN_KEY_FILE": str(
                isolated / ".runtime" / "ms-token.key"
            ),
            "CODE_ASSIST_MS_CLIENT_SECRET_FILE": str(
                isolated / ".runtime" / "ms-client-secret.txt"
            ),
            "CODE_ASSIST_CODEX_CWD": str(isolated / "workspace"),
            "CODE_ASSIST_CODEX_BIN": str(isolated / "bin" / "disabled.exe"),
            "CODE_ASSIST_ENABLE_CODEX_BRIDGE": "0",
            "CODE_ASSIST_AUTOLOAD_GGUF": "0",
        }
    )
    script = """
import json
from code_assist_v1 import (
  auth, codex_bridge, config, job_manager, knowledge_store, models,
  notion_store, outlook_graph, outlook_integration, routes_notion,
  routes_sessions, routes_workspace, second_brain_store, skill_filter,
)
print(json.dumps({
  'override': config.DATA_ROOT_OVERRIDE_ACTIVE,
  'data_root': config.DATA_ROOT,
  'runtime': config.RUNTIME_DIR,
  'knowledge': config.KNOWLEDGE_DIR,
  'sessions': config.SESSIONS_DIR,
  'workspace': config.WORKSPACE_DIR,
  'user_data': config.USER_DATA_DIR,
  'users': config.AUTH_USERS_FILE,
  'secret': config.AUTH_SECRET_FILE,
  'token': config.TOKEN_FILE,
  'outlook_key': outlook_graph.TOKEN_KEY_FILE,
  'outlook_effective_key': outlook_graph._key_file_path(),
  'auth_audit': auth.AUTH_AUDIT_FILE,
  'job_user_data': job_manager.USER_DATA_DIR,
  'legacy_knowledge': knowledge_store.KNOWLEDGE_DIR,
  'second_brain_knowledge': second_brain_store.KNOWLEDGE_DIR,
  'notion_user_data': notion_store.USER_DATA_DIR,
  'outlook_user_data': outlook_integration.USER_DATA_DIR,
  'notion_route_user_data': routes_notion.USER_DATA_DIR,
  'session_fallback': routes_sessions.SESSIONS_DIR,
  'workspace_route': routes_workspace.WORKSPACE_DIR,
  'skill_user_data': skill_filter.USER_DATA_DIR,
  'codex_workspace': codex_bridge.WORKSPACE_DIR,
  'codex_effective_cwd': codex_bridge.codex_model_config()['codex_cwd'],
  'api_config': models.API_CONFIG_PATH,
}, sort_keys=True))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    # Importing the existing model registry emits one compatibility diagnostic;
    # the isolated-path probe is the final JSON line by contract.
    report = json.loads(completed.stdout.splitlines()[-1])
    resolved = isolated.resolve()

    assert report["override"] is True
    assert Path(report["data_root"]).resolve() == resolved
    for key in (
        "runtime",
        "knowledge",
        "sessions",
        "workspace",
        "user_data",
        "users",
        "secret",
        "token",
        "outlook_key",
        "outlook_effective_key",
        "auth_audit",
        "job_user_data",
        "legacy_knowledge",
        "second_brain_knowledge",
        "notion_user_data",
        "outlook_user_data",
        "notion_route_user_data",
        "session_fallback",
        "workspace_route",
        "skill_user_data",
        "codex_workspace",
        "codex_effective_cwd",
        "api_config",
    ):
        assert Path(report[key]).resolve().is_relative_to(resolved), key
    for key in ("runtime", "knowledge", "sessions", "workspace", "user_data"):
        assert Path(report[key]).is_dir(), key
