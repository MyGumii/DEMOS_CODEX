from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_incarnation_migration as migration_1309
from code_assist_v1 import notion_workspace_navigation_migration as migration
from code_assist_v1 import notion_workspace_store as store_module
from code_assist_v1.tools import migrate_notion_workspace_navigation as cli


def _configured(path: Path, *, now: int = 100) -> sqlite3.Connection:
    return store_module.WorkspaceStore._configure(
        sqlite3.connect(path), query_only=False, healing_clock=lambda: now
    )


def _source_catalog(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "user-data"
    db = root / ".notion-workspace-v2" / "workspace.db"
    db.parent.mkdir(parents=True)
    con = _configured(db)
    try:
        con.executescript(migration.SOURCE_SQL)
        con.execute(
            "INSERT INTO workspace_schema_migrations VALUES(?,?,?,?,?)",
            (
                migration.SOURCE_VERSION,
                migration.SOURCE_NAME,
                migration.SOURCE_CHECKSUM,
                migration.SOURCE_ACTUAL_SCHEMA_SHA256,
                100,
            ),
        )
        con.execute(
            "INSERT INTO workspaces VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("ws_test", "Test", "test", None, "owner", 1, 1, 1, 100, 100),
        )
        con.execute(
            "INSERT INTO workspace_memberships VALUES(?,?,?,?,?,?,?,?)",
            ("ws_test", "owner", "owner", 1, "owner", 100, 100, None),
        )
        con.commit()
    finally:
        con.close()
    return root, db


def _backup(source: Path, destination: Path) -> Path:
    source_con = sqlite3.connect(source)
    destination_con = sqlite3.connect(destination)
    try:
        source_con.backup(destination_con)
        destination_con.commit()
    finally:
        destination_con.close()
        source_con.close()
    return destination


def _metadata(path: Path):
    stat = path.stat()
    return (
        stat.st_size,
        stat.st_mtime_ns,
        hashlib.sha256(path.read_bytes()).hexdigest(),
        tuple(sorted(item.name for item in path.parent.iterdir())),
    )


def _apply(root: Path, db: Path, backup: Path, *, fault_hook=None):
    plan = migration.build_plan(db, root)
    return migration.apply_plan(
        db,
        root,
        exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup,
        fault_hook=fault_hook,
    )


def test_navigation_migration_frozen_contracts_are_exact() -> None:
    assert migration.SOURCE_VERSION == migration_1309.TARGET_VERSION == 2026081309
    assert migration.SOURCE_SQL == migration_1309.TARGET_SQL
    assert migration.TARGET_VERSION == store_module._SCHEMA_1310_VERSION == 2026081310
    assert migration.TARGET_SQL == store_module._SCHEMA_1310_SQL
    assert migration.TARGET_CHECKSUM == store_module._SCHEMA_1310_CHECKSUM
    assert (
        migration.TARGET_ACTUAL_SCHEMA_SHA256
        == store_module._SCHEMA_1310_ACTUAL_SCHEMA_SHA256
    )
    assert (
        "sha256:" + hashlib.sha256(migration.TARGET_SQL.encode()).hexdigest()
        == migration.TARGET_CHECKSUM
    )
    assert migration.NAVIGATION_OBJECTS.issubset(
        {item["name"] for item in migration.TARGET_OBJECTS}
    )


def test_navigation_migration_dry_run_and_cli_are_deterministic_read_only(
    tmp_path: Path,
) -> None:
    root, db = _source_catalog(tmp_path)
    before = _metadata(db)
    first = migration.build_plan(db, root)
    second = migration.build_plan(db, root)
    report, code = cli.run(
        ["--catalog-path", str(db), "--user-data-root", str(root)]
    )
    assert first == second
    assert first["state"] == "migration_required"
    assert report["mode"] == "dry-run"
    assert report["plan_checksum"] == first["plan_checksum"]
    assert code == 0
    assert _metadata(db) == before


def test_navigation_migration_preserves_catalog_and_fabricates_no_state(
    tmp_path: Path,
) -> None:
    root, db = _source_catalog(tmp_path)
    _apply(root, db, _backup(db, root / "workspace.before-1310.bak"))

    store = store_module.WorkspaceStore(db, trusted_clock=lambda: 100)
    assert store.capabilities() == {"workspace_v2": False, "schema_version": None}
    with pytest.raises(store_module.WorkspaceSubstrateUnavailable):
        store.verify_deep_health()
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0] == 1
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_navigation_states"
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_sidebar_section_preferences"
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_favorites"
        ).fetchone()[0] == 0
    finally:
        con.close()
    assert migration.build_plan(db, root)["state"] == "already_current"


@pytest.mark.parametrize("phase", ["after_schema", "before_commit"])
def test_navigation_migration_fault_rolls_back_exact_source(
    tmp_path: Path, phase: str
) -> None:
    root, db = _source_catalog(tmp_path)
    before = _metadata(db)
    backup = _backup(db, root / f"workspace.{phase}.bak")

    def fault(current: str) -> None:
        if current == phase:
            raise RuntimeError(f"fault:{phase}")

    with pytest.raises(RuntimeError, match=f"fault:{phase}"):
        _apply(root, db, backup, fault_hook=fault)
    assert _metadata(db) == before
    assert migration.build_plan(db, root)["state"] == "migration_required"


def test_navigation_migration_requires_explicit_quiescence_and_backup(
    tmp_path: Path,
) -> None:
    root, db = _source_catalog(tmp_path)
    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.guard.bak")
    with pytest.raises(migration.MigrationRefused):
        migration.apply_plan(
            db,
            root,
            exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=False,
            backup_path=backup,
        )
    assert migration.build_plan(db, root)["state"] == "migration_required"
