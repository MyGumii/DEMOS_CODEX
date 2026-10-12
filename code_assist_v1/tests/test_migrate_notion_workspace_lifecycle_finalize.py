from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_lifecycle_finalize_migration as migration
from code_assist_v1 import notion_workspace_lifecycle_kernel_migration as migration_1312
from code_assist_v1 import notion_workspace_store as store_module
from code_assist_v1.tools import migrate_notion_workspace_lifecycle_finalize as cli


def _source_catalog(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "user-data"
    db = root / ".notion-workspace-v2" / "workspace.db"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
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


def test_lifecycle_finalize_frozen_contracts_are_exact() -> None:
    assert migration.SOURCE_VERSION == migration_1312.TARGET_VERSION == 2026081312
    assert migration.SOURCE_SQL == migration_1312.TARGET_SQL
    assert migration.TARGET_VERSION == store_module._SCHEMA_1313_VERSION == 2026081313
    assert migration.TARGET_SQL == store_module._SCHEMA_1313_SQL
    assert migration.TARGET_CHECKSUM == store_module._SCHEMA_1313_CHECKSUM
    assert (
        migration.TARGET_ACTUAL_SCHEMA_SHA256
        == store_module._SCHEMA_1313_ACTUAL_SCHEMA_SHA256
    )
    assert (
        "sha256:" + hashlib.sha256(migration.TARGET_SQL.encode()).hexdigest()
        == migration.TARGET_CHECKSUM
    )
    assert migration.LIFECYCLE_FINALIZE_OBJECTS.issubset(
        {item["name"] for item in migration.TARGET_OBJECTS}
    )


def test_lifecycle_finalize_dry_run_apply_and_cli_are_explicit(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    first = migration.build_plan(db, root)
    second = migration.build_plan(db, root)
    report, code = cli.run(["--catalog-path", str(db), "--user-data-root", str(root)])
    assert first == second
    assert first["state"] == "migration_required"
    assert report["mode"] == "dry-run"
    assert code == 0
    result = _apply(root, db, _backup(db, root / "workspace.before-1313.bak"))
    assert result["applied"] is True
    assert migration.build_plan(db, root)["state"] == "already_current"
    con = sqlite3.connect(db)
    try:
        assert con.execute(
            "SELECT version,name,checksum,actual_schema_sha256 "
            "FROM workspace_schema_migrations"
        ).fetchone() == (
            migration.TARGET_VERSION,
            migration.TARGET_NAME,
            migration.TARGET_CHECKSUM,
            migration.TARGET_ACTUAL_SCHEMA_SHA256,
        )
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()


def test_lifecycle_finalize_refuses_every_existing_1312_command(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    clock = [100]
    con = store_module.WorkspaceStore._configure(
        sqlite3.connect(db), query_only=False, healing_clock=lambda: clock[0]
    )
    try:
        con.execute(
            "INSERT INTO workspaces VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("ws_test", "Test", "test", None, "owner", 1, 1, 1, 100, 100),
        )
        con.execute(
            "INSERT INTO workspace_memberships VALUES(?,?,?,?,?,?,?,?)",
            ("ws_test", "owner", "owner", 1, "owner", 100, 100, None),
        )
        con.create_function("workspace_page_tree_now", 0, lambda: 100)
        con.create_function("workspace_page_tree_actor", 0, lambda: "owner")
        con.create_function("workspace_page_tree_operation_id", 0, lambda: "rename-1")
        con.create_function("workspace_page_tree_action", 0, lambda: "rename")
        con.execute(
            """
            INSERT INTO workspace_page_lifecycle_commands(
              id,workspace_id,actor_subject_id,operation_id,action,status,
              source_owner_subject_id,source_page_id,expected_tree_version,
              expected_binding_version,expected_incarnation_generation,
              request_sha256,created_at,updated_at
            ) VALUES(?,?,?,?,?,'prepared',?,?,?,?,?,?,?,?)
            """,
            (
                "plc_" + "1" * 32,
                "ws_test",
                "owner",
                "rename-1",
                "rename",
                "owner",
                "page-1",
                0,
                0,
                0,
                "sha256:" + "a" * 64,
                100,
                100,
            ),
        )
        con.commit()
    finally:
        con.close()
    before = db.read_bytes()
    with pytest.raises(
        migration.MigrationRefused,
        match="pre-finalizer lifecycle commands",
    ):
        _apply(root, db, _backup(db, root / "workspace.command.bak"))
    assert db.read_bytes() == before


@pytest.mark.parametrize("phase", ["after_schema", "before_commit"])
def test_lifecycle_finalize_fault_rolls_back_exact_1312_source(
    tmp_path: Path, phase: str
) -> None:
    root, db = _source_catalog(tmp_path)
    before = db.read_bytes()

    def fault(current: str) -> None:
        if current == phase:
            raise RuntimeError("injected 1313 migration fault")

    with pytest.raises(RuntimeError, match="injected 1313 migration fault"):
        _apply(root, db, _backup(db, root / f"workspace.{phase}.bak"), fault_hook=fault)
    assert db.read_bytes() == before
    assert migration.build_plan(db, root)["state"] == "migration_required"


def test_unconfigured_raw_writer_cannot_use_lifecycle_binding_refresh(
    tmp_path: Path,
) -> None:
    db = tmp_path / "workspace.db"
    store = store_module.WorkspaceStore(db, trusted_clock=lambda: 100)
    store.bootstrap()
    con = store._write_connection()
    try:
        assert tuple(con.execute(
            "SELECT workspace_page_lifecycle_finalize_command_id(), "
            "workspace_page_lifecycle_finalize_source_receipt_sha256()"
        ).fetchone()) == ("", "")
        trigger = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='trg_page_source_lifecycle_update'"
        ).fetchone()[0]
        assert "workspace_page_lifecycle_finalize_command_id()" in trigger
        assert "workspace_healing_apply_run_id()" in trigger
    finally:
        con.close()
