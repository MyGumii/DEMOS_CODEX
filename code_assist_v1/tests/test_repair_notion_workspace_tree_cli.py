from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from code_assist_v1 import app_notion
from code_assist_v1 import config
from code_assist_v1 import notion_store as ns
from code_assist_v1.test_safety import assert_isolated_test_path
from code_assist_v1.tools import repair_notion_workspace_tree as repair_cli


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CLI_MODULE = "code_assist_v1.tools.repair_notion_workspace_tree"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _workspace_with_issues(isolated_data_root: Path, user_id: str) -> Path:
    user_data_root = assert_isolated_test_path(
        Path(config.USER_DATA_DIR), test_root=isolated_data_root
    )
    db_path = assert_isolated_test_path(
        user_data_root / user_id / "notion" / "notion.db",
        test_root=isolated_data_root,
    )
    created = ns.bootstrap_workspace(user_id)
    assert created["created"] is True
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA journal_mode=DELETE")
        rows = (
            ("p_root", None, "Root", 1),
            ("p_self", "p_self", "Self", 2),
            ("p_dangling", "p_missing", "Dangling", 3),
            ("p_cycle_a", "p_cycle_b", "Cycle A", 4),
            ("p_cycle_b", "p_cycle_a", "Cycle B", 5),
        )
        con.executemany(
            """
            INSERT INTO pages(
              id, owner_user_id, parent_id, title, icon, cover, settings_json,
              position, archived, archived_at, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (page_id, user_id, parent_id, title, "", "", "{}", position, 0, None, 1, 1)
                for page_id, parent_id, title, position in rows
            ],
        )
        con.commit()
    finally:
        con.close()
    assert repair_cli.inspect_tree(db_path)["issue_count"] == 3
    return db_path


def _database_state(db_path: Path) -> dict[str, Any]:
    stat = db_path.stat()
    con = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    try:
        schema = tuple(
            tuple(row)
            for row in con.execute(
                """
                SELECT type, name, tbl_name, COALESCE(sql, '')
                FROM sqlite_master ORDER BY type, name
                """
            ).fetchall()
        )
    finally:
        con.close()
    return {
        "sha256": _sha256(db_path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "schema": schema,
        "entries": tuple(sorted(path.name for path in db_path.parent.iterdir())),
    }


def _run_cli(isolated_data_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    assert_isolated_test_path(Path(config.USER_DATA_DIR), test_root=isolated_data_root)
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    assert Path(environment["CODE_ASSIST_DATA_ROOT"]).resolve() == isolated_data_root
    assert Path(environment["CODE_ASSIST_TEST_DATA_ROOT"]).resolve() == isolated_data_root
    return subprocess.run(
        [sys.executable, "-m", CLI_MODULE, *args],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def _report(stdout: str) -> dict[str, Any]:
    start = stdout.find("{")
    assert start >= 0, stdout
    return json.loads(stdout[start:])


@pytest.mark.regression
def test_np_1c_dry_run_reports_issues_without_any_database_change(
    isolated_data_root: Path,
) -> None:
    user_id = "np1c-dry-run"
    db_path = _workspace_with_issues(isolated_data_root, user_id)
    before = _database_state(db_path)

    completed = _run_cli(
        isolated_data_root,
        "--user-id", user_id,
        "--expected-db-path", str(db_path.resolve()),
    )

    assert completed.returncode == 0, completed.stderr
    report = _report(completed.stdout)
    assert report["mode"] == "dry-run"
    assert report["would_change"] is True
    assert report["before"]["database_sha256"] == before["sha256"]
    assert report["before"]["tree"] == {
        "active_page_count": 5,
        "affected_page_count": 4,
        "cycle_count": 1,
        "cycle_page_count": 2,
        "dangling_parent_count": 1,
        "healthy": False,
        "issue_count": 3,
        "self_parent_count": 1,
    }
    assert report["backup"]["created"] is False
    assert _database_state(db_path) == before
    assert not Path(f"{db_path}-wal").exists()
    assert not Path(f"{db_path}-shm").exists()


@pytest.mark.regression
def test_np_1c_missing_database_and_wrong_confirmation_block_apply(
    isolated_data_root: Path,
) -> None:
    missing_user = "np1c-missing"
    missing_path = Path(config.USER_DATA_DIR) / missing_user / "notion" / "notion.db"
    missing = _run_cli(
        isolated_data_root,
        "--user-id", missing_user,
        "--expected-db-path", str(missing_path.resolve()),
    )
    assert missing.returncode == 2
    assert "workspace database does not exist" in missing.stderr
    assert not missing_path.parent.exists()

    user_id = "np1c-confirm-guard"
    db_path = _workspace_with_issues(isolated_data_root, user_id)
    before = _database_state(db_path)
    blocked = _run_cli(
        isolated_data_root,
        "--user-id", user_id,
        "--expected-db-path", str(db_path.resolve()),
        "--apply",
        "--confirm", "wrong-confirmation",
    )
    assert blocked.returncode == 2
    assert "--confirm must exactly equal" in blocked.stderr
    assert _database_state(db_path) == before
    assert not list(db_path.parent.glob("*.tree-repair.*.bak"))


@pytest.mark.regression
def test_np_1c_explicit_apply_creates_verified_backup_and_repairs_temp_db(
    isolated_data_root: Path,
) -> None:
    user_id = "np1c-explicit-apply"
    db_path = _workspace_with_issues(isolated_data_root, user_id)
    before_sha = _sha256(db_path)

    completed = _run_cli(
        isolated_data_root,
        "--user-id", user_id,
        "--expected-db-path", str(db_path.resolve()),
        "--apply",
        "--confirm", repair_cli.required_confirmation(user_id),
    )

    assert completed.returncode == 0, completed.stderr
    report = _report(completed.stdout)
    backup_path = assert_isolated_test_path(
        Path(report["backup"]["path"]), test_root=isolated_data_root
    )
    assert backup_path.parent == db_path.parent
    assert backup_path.is_file()
    assert report["backup"]["quick_check"] == "ok"
    assert report["backup"]["sha256"] == _sha256(backup_path)
    assert report["backup"]["tree_snapshot"]["issue_count"] == 3
    assert report["before"]["database_sha256"] == before_sha
    assert report["after"]["tree"]["healthy"] is True
    assert report["after"]["tree"]["issue_count"] == 0
    assert report["after"]["database_sha256"] == _sha256(db_path)
    assert report["changed"] is True
    assert repair_cli.inspect_tree(backup_path)["issue_count"] == 3
    assert repair_cli.inspect_tree(db_path)["issue_count"] == 0


@pytest.mark.unit
def test_np_1c_backup_reservation_never_overwrites_existing_file(
    monkeypatch: pytest.MonkeyPatch,
    isolated_data_root: Path,
) -> None:
    db_path = assert_isolated_test_path(
        isolated_data_root / "backup-guard" / "notion.db",
        test_root=isolated_data_root,
    )
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"source")

    class FixedDateTime:
        @classmethod
        def now(cls, tz: timezone) -> datetime:
            return datetime(2026, 8, 13, 1, 2, 3, 456789, tzinfo=timezone.utc)

    monkeypatch.setattr(repair_cli, "datetime", FixedDateTime)
    monkeypatch.setattr(
        repair_cli.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="abcdef0123456789abcdef0123456789"),
    )
    expected = db_path.with_name(
        "notion.db.tree-repair.20260813T010203456789Z.0123456789ab.abcdef01.bak"
    )
    expected.write_bytes(b"keep-me")

    with pytest.raises(FileExistsError):
        repair_cli._reserve_backup_path(db_path, "0123456789abcdef")

    assert expected.read_bytes() == b"keep-me"


@pytest.mark.regression
def test_np_1c_create_app_never_invokes_workspace_tree_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        ns,
        "repair_workspace_tree",
        lambda user_id: calls.append(str(user_id)),
    )

    app = app_notion.create_app()

    assert app is not None
    assert calls == []
