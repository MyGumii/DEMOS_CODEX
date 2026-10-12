from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from code_assist_v1 import config
from code_assist_v1 import notion_store as ns
from code_assist_v1.test_safety import assert_isolated_test_path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CLI_MODULE = "code_assist_v1.tools.migrate_notion_atomic_save"


def _legacy_database(isolated_data_root: Path, user_id: str) -> Path:
    user_data_root = assert_isolated_test_path(
        Path(config.USER_DATA_DIR),
        test_root=isolated_data_root,
    )
    db_path = assert_isolated_test_path(
        user_data_root / user_id / "notion" / "notion.db",
        test_root=isolated_data_root,
    )
    db_path.parent.mkdir(parents=True, exist_ok=False)
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute(
            """
            CREATE TABLE pages (
              id TEXT PRIMARY KEY,
              owner_user_id TEXT NOT NULL,
              parent_id TEXT,
              title TEXT NOT NULL,
              icon TEXT,
              cover TEXT,
              settings_json TEXT,
              position INTEGER NOT NULL DEFAULT 0,
              archived INTEGER NOT NULL DEFAULT 0,
              archived_at INTEGER,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            )
            """
        )
        con.executescript(
            """
            CREATE TABLE blocks (
              id TEXT PRIMARY KEY,
              page_id TEXT NOT NULL,
              owner_user_id TEXT NOT NULL,
              position INTEGER NOT NULL,
              block_type TEXT NOT NULL,
              content TEXT,
              props_json TEXT,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
            );

            CREATE TABLE files (
              id TEXT PRIMARY KEY,
              page_id TEXT NOT NULL,
              owner_user_id TEXT NOT NULL,
              uploader_user_id TEXT NOT NULL,
              original_name TEXT NOT NULL,
              stored_name TEXT NOT NULL,
              rel_path TEXT NOT NULL,
              mime_type TEXT,
              size INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
            );

            CREATE TABLE page_versions (
              id TEXT PRIMARY KEY,
              page_id TEXT NOT NULL,
              owner_user_id TEXT NOT NULL,
              title TEXT NOT NULL,
              icon TEXT,
              cover TEXT,
              settings_json TEXT,
              blocks_json TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              FOREIGN KEY(page_id) REFERENCES pages(id) ON DELETE CASCADE
            );
            """
        )
        con.execute(
            """
            INSERT INTO pages(
              id, owner_user_id, parent_id, title, icon, cover, settings_json,
              position, archived, archived_at, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("p_legacy", user_id, None, "Legacy", "", "", "{}", 1, 0, None, 1, 1),
        )
        con.commit()
    finally:
        con.close()
    assert db_path.is_file()
    return db_path


def _read_schema(db_path: Path) -> tuple[tuple[object, ...], ...]:
    con = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    try:
        objects = con.execute(
            """
            SELECT type, name, tbl_name, COALESCE(sql, '')
            FROM sqlite_master
            ORDER BY type, name
            """
        ).fetchall()
        page_columns = con.execute("PRAGMA table_info(pages)").fetchall()
        return tuple(tuple(row) for row in [*objects, *page_columns])
    finally:
        con.close()


def _file_state(path: Path) -> tuple[int, int, str]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


def _database_state(db_path: Path) -> dict[str, object]:
    sidecars = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            sidecars[candidate.name] = _file_state(candidate)
    return {
        "schema": _read_schema(db_path),
        "files": sidecars,
        "directory_entries": tuple(sorted(item.name for item in db_path.parent.iterdir())),
    }


def _run_cli(isolated_data_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    assert_isolated_test_path(Path(config.USER_DATA_DIR), test_root=isolated_data_root)
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    assert Path(environment["CODE_ASSIST_DATA_ROOT"]).resolve() == isolated_data_root.resolve()
    assert Path(environment["CODE_ASSIST_TEST_DATA_ROOT"]).resolve() == isolated_data_root.resolve()
    return subprocess.run(
        [sys.executable, "-m", CLI_MODULE, *args],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )


def _json_report(stdout: str) -> dict[str, object]:
    start = stdout.find("{")
    assert start >= 0, stdout
    return json.loads(stdout[start:])


def test_dry_run_is_strictly_read_only(
    isolated_data_root: Path,
) -> None:
    user_id = "notion-dry-run"
    db_path = _legacy_database(isolated_data_root, user_id)
    before = _database_state(db_path)

    completed = _run_cli(
        isolated_data_root,
        "--user-id",
        user_id,
        "--expected-db-path",
        str(db_path.resolve()),
    )

    assert completed.returncode == 0, completed.stderr
    report = _json_report(completed.stdout)
    assert report["mode"] == "dry-run"
    assert report["db_path"] == str(db_path.resolve())
    assert report["would_change"] is True
    assert report["before"]["atomic_save"] is False
    assert _database_state(db_path) == before
    assert not Path(f"{db_path}-wal").exists()
    assert not Path(f"{db_path}-shm").exists()


def test_path_mismatch_blocks_apply_without_touching_database(
    isolated_data_root: Path,
) -> None:
    user_id = "notion-path-guard"
    db_path = _legacy_database(isolated_data_root, user_id)
    wrong_path = assert_isolated_test_path(
        isolated_data_root / "wrong-target" / "notion.db",
        test_root=isolated_data_root,
    )
    before = _database_state(db_path)

    completed = _run_cli(
        isolated_data_root,
        "--user-id",
        user_id,
        "--expected-db-path",
        str(wrong_path),
        "--apply",
        "--backup-confirmed",
        "--confirm-checksum",
        ns.ATOMIC_SAVE_MIGRATION[2],
    )

    assert completed.returncode == 2
    assert "--expected-db-path does not match resolved target" in completed.stderr
    assert _database_state(db_path) == before


@pytest.mark.parametrize(
    ("extra_args", "expected_error"),
    [
        (
            ("--apply", "--confirm-checksum", ns.ATOMIC_SAVE_MIGRATION[2]),
            "--apply requires --backup-confirmed",
        ),
        (
            ("--apply", "--backup-confirmed"),
            "--confirm-checksum must exactly equal",
        ),
    ],
)
def test_apply_requires_backup_confirmation_and_checksum(
    isolated_data_root: Path,
    extra_args: tuple[str, ...],
    expected_error: str,
) -> None:
    discriminator = "backup" if "--backup-confirmed" not in extra_args else "checksum"
    user_id = f"notion-{discriminator}-guard"
    db_path = _legacy_database(isolated_data_root, user_id)
    before = _database_state(db_path)

    completed = _run_cli(
        isolated_data_root,
        "--user-id",
        user_id,
        "--expected-db-path",
        str(db_path.resolve()),
        *extra_args,
    )

    assert completed.returncode == 2
    assert expected_error in completed.stderr
    assert _database_state(db_path) == before


def test_explicit_apply_migrates_only_the_isolated_database(
    isolated_data_root: Path,
) -> None:
    user_id = "notion-explicit-apply"
    db_path = _legacy_database(isolated_data_root, user_id)
    assert_isolated_test_path(db_path, test_root=isolated_data_root)
    before = _database_state(db_path)

    completed = _run_cli(
        isolated_data_root,
        "--user-id",
        user_id,
        "--expected-db-path",
        str(db_path.resolve()),
        "--apply",
        "--backup-confirmed",
        "--confirm-checksum",
        ns.ATOMIC_SAVE_MIGRATION[2],
    )

    assert completed.returncode == 0, completed.stderr
    report = _json_report(completed.stdout)
    assert report["mode"] == "apply"
    assert report["before"]["atomic_save"] is False
    assert report["after"]["atomic_save"] is True
    assert report["result"] == {
        "atomic_save": True,
        "user_id": user_id,
        "version": ns.ATOMIC_SAVE_MIGRATION[0],
    }
    assert _database_state(db_path) != before

    con = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    try:
        page_columns = {str(row[1]) for row in con.execute("PRAGMA table_info(pages)")}
        tables = {
            str(row[0])
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        migration = con.execute(
            "SELECT name, checksum FROM notion_schema_migrations WHERE version=?",
            (ns.ATOMIC_SAVE_MIGRATION[0],),
        ).fetchone()
        revision = con.execute("SELECT revision FROM pages WHERE id='p_legacy'").fetchone()
    finally:
        con.close()
    assert "revision" in page_columns
    assert {"page_save_operations", "notion_schema_migrations"}.issubset(tables)
    assert migration == ns.ATOMIC_SAVE_MIGRATION[1:]
    assert revision == (1,)
