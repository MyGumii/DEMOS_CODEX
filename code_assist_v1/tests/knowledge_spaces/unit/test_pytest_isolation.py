from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

import pytest

from code_assist_v1 import config
from code_assist_v1.test_safety import (
    UnsafeTestDataPathError,
    assert_isolated_test_path,
)


@pytest.mark.unit
def test_effective_config_and_all_standard_fixtures_are_session_isolated(
    isolated_data_root: Path,
    isolated_knowledge_layout,
    isolated_sqlite_db: Path,
    isolated_blob_dir: Path,
    isolated_projection_dir: Path,
    isolated_notion_dir: Path,
) -> None:
    root = isolated_data_root.resolve()
    assert Path(os.environ["CODE_ASSIST_TEST_DATA_ROOT"]).resolve() == root
    assert Path(os.environ["CODE_ASSIST_DATA_ROOT"]).resolve() == root
    assert Path(config.DATA_ROOT or "").resolve() == root

    for candidate in (
        config.RUNTIME_DIR,
        config.KNOWLEDGE_DIR,
        config.SESSIONS_DIR,
        config.WORKSPACE_DIR,
        config.USER_DATA_DIR,
        config.AUTH_USERS_FILE,
        config.AUTH_SECRET_FILE,
        config.TOKEN_FILE,
        isolated_knowledge_layout.root,
        isolated_blob_dir,
        isolated_projection_dir,
        isolated_notion_dir,
        isolated_sqlite_db,
    ):
        assert assert_isolated_test_path(candidate, test_root=root)

    connection = sqlite3.connect(isolated_sqlite_db)
    try:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
    finally:
        connection.close()


@pytest.mark.unit
def test_guard_rejects_real_repository_and_live_roots(
    isolated_data_root: Path,
) -> None:
    package_root = Path(__file__).resolve().parents[3]
    repository_root = package_root.parent
    candidates = (
        repository_root,
        package_root,
        package_root / ".runtime",
        package_root / "user_data",
        package_root / "sessions",
        package_root / "workspace",
        package_root / "knowledge",
        repository_root / "knowledge",
    )
    for candidate in candidates:
        with pytest.raises(UnsafeTestDataPathError):
            assert_isolated_test_path(candidate, test_root=isolated_data_root)


@pytest.mark.unit
def test_session_guard_rejects_environment_or_temp_escape(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import conftest as isolation_plugin

    with monkeypatch.context() as scoped:
        scoped.setenv("OPENAI_API_KEY", "must-not-leak")
        with pytest.raises(pytest.UsageError, match="OPENAI_API_KEY"):
            isolation_plugin._assert_effective_isolation(isolated_data_root)

    with monkeypatch.context() as scoped:
        scoped.setenv("CODE_ASSIST_JOB_WORKERS", "9")
        with pytest.raises(pytest.UsageError, match="CODE_ASSIST_JOB_WORKERS"):
            isolation_plugin._assert_effective_isolation(isolated_data_root)

    with monkeypatch.context() as scoped:
        scoped.setenv("TEMP", os.fspath(Path(__file__).resolve().parents[3]))
        with pytest.raises(pytest.UsageError, match="TEMP"):
            isolation_plugin._assert_effective_isolation(isolated_data_root)

    with monkeypatch.context() as scoped:
        scoped.setattr(tempfile, "tempdir", os.fspath(Path(__file__).resolve().parents[3]))
        with pytest.raises(pytest.UsageError, match="tempfile.tempdir"):
            isolation_plugin._assert_effective_isolation(isolated_data_root)


@pytest.mark.unit
def test_bootstrap_rejects_live_inherited_temp_before_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import conftest as isolation_plugin

    live_package_root = Path(__file__).resolve().parents[3]
    with monkeypatch.context() as scoped:
        scoped.setenv("CODE_ASSIST_TEST_MODE", "1")
        scoped.setenv("TEMP", os.fspath(live_package_root))
        scoped.setenv("TMP", os.fspath(live_package_root))
        scoped.setenv("TMPDIR", os.fspath(live_package_root))
        scoped.setattr(tempfile, "tempdir", None)
        with pytest.raises(UnsafeTestDataPathError):
            isolation_plugin._trusted_temp_parent()


@pytest.mark.unit
def test_actual_symlink_or_windows_junction_cannot_escape_guarded_root(
    tmp_path: Path,
) -> None:
    guarded_root = tmp_path / "guarded"
    outside = tmp_path / "outside"
    junction = guarded_root / "escape"
    guarded_root.mkdir()
    outside.mkdir()

    if sys.platform == "win32":
        completed = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout
    else:
        junction.symlink_to(outside, target_is_directory=True)

    try:
        with pytest.raises(UnsafeTestDataPathError):
            assert_isolated_test_path(junction / "child", test_root=guarded_root)
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)


@pytest.mark.fault
def test_fault_marker_runs_only_with_a_guarded_root(isolated_data_root: Path) -> None:
    assert assert_isolated_test_path(isolated_data_root) == isolated_data_root
