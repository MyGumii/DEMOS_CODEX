from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_lifecycle_subtree_finalize_migration as migration
from code_assist_v1 import notion_workspace_lifecycle_subtree_migration as migration_1316
from code_assist_v1 import notion_workspace_store as store_module
from code_assist_v1.tools import (
    migrate_notion_workspace_lifecycle_subtree_finalize as cli,
)


def _source_catalog(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "user-data"
    db = root / ".notion-workspace-v2" / "workspace.db"
    db.parent.mkdir(parents=True)
    con = store_module.WorkspaceStore._configure(
        sqlite3.connect(db), query_only=False, healing_clock=lambda: 100
    )
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


def test_subtree_finalize_frozen_contracts_are_exact() -> None:
    assert migration.SOURCE_VERSION == migration_1316.TARGET_VERSION == 2026081316
    assert migration.SOURCE_SQL == migration_1316.TARGET_SQL
    assert migration.TARGET_VERSION == store_module.SCHEMA_VERSION == 2026081317
    assert migration.TARGET_SQL == store_module._SCHEMA_SQL + store_module._HEALING_SCHEMA_SQL
    assert migration.TARGET_CHECKSUM == store_module._SCHEMA_CHECKSUM
    assert migration.TARGET_ACTUAL_SCHEMA_SHA256 == store_module._SCHEMA_ACTUAL_SCHEMA_SHA256
    assert (
        "sha256:" + hashlib.sha256(migration.TARGET_SQL.encode()).hexdigest()
        == migration.TARGET_CHECKSUM
    )
    assert len(migration.SOURCE_OBJECTS) == len(migration.TARGET_OBJECTS) == 179


def test_subtree_finalize_apply_matches_fresh_runtime(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    plan = migration.build_plan(db, root)
    assert plan["state"] == "migration_required"
    result = migration.apply_plan(
        db,
        root,
        exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=_backup(db, root / "workspace.before-1317.bak"),
    )
    assert result["applied"] is True

    migrated = store_module.WorkspaceStore(db)
    health = migrated.verify_deep_health()
    assert health["healthy"] is True
    assert health["schema_version"] == 2026081317

    con = migrated._read_connection()
    try:
        effect_sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='workspace_page_lifecycle_subtree_effects'"
        ).fetchone()[0]
        assert "transitioned IN (0,1)" in effect_sql
        assert str(store_module.MAX_PAGE_LIFECYCLE_SUBTREE_EFFECTS) in effect_sql
    finally:
        con.close()


def test_subtree_finalize_cli_dry_run_is_deterministic(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    first = migration.build_plan(db, root)
    second = migration.build_plan(db, root)
    report, code = cli.run(
        ["--catalog-path", str(db), "--user-data-root", str(root)]
    )
    assert first == second
    assert report == {"mode": "dry-run", **first}
    assert code == 0


def test_subtree_finalize_requires_quiescence_and_exact_backup(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.before-1317.bak")
    with pytest.raises(migration.MigrationRefused):
        migration.apply_plan(
            db,
            root,
            exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=False,
            backup_path=backup,
        )
