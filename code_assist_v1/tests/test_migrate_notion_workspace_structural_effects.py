from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_lifecycle_finalize_migration as migration_1313
from code_assist_v1 import notion_workspace_store as store_module
from code_assist_v1 import notion_workspace_structural_effect_migration as migration
from code_assist_v1.tools import migrate_notion_workspace_structural_effects as cli


SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64


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


def _seed_workspace(db: Path) -> None:
    con = store_module.WorkspaceStore._configure(
        sqlite3.connect(db), query_only=False, healing_clock=lambda: 100
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
        con.commit()
    finally:
        con.close()


def _manifest_values(command_id: str = "plc_" + "1" * 32) -> tuple[object, ...]:
    return (
        command_id,
        "ws_test",
        "create",
        "owner",
        "p_" + "2" * 32,
        SHA_A,
        SHA_B,
        SHA_C,
        1,
        1,
        1,
        2,
        1,
        1,
        1,
        "inc_" + "3" * 32,
        1,
        1,
        31,
        SHA_A,
        None,
        None,
        None,
        None,
        1,
        1,
        0,
        1,
        1,
        1,
        100,
    )


_MANIFEST_INSERT = """
INSERT INTO workspace_page_lifecycle_structural_manifests(
  command_id,workspace_id,action,source_owner_subject_id,target_source_page_id,
  source_request_sha256,source_operation_receipt_sha256,post_manifest_sha256,
  source_changed,structure_changed,source_device,source_inode,source_link_count,
  source_epoch,full_sweep_generation,target_incarnation_id,
  target_incarnation_generation,target_change_generation,target_change_mask,
  target_source_receipt_sha256,old_parent_source_owner_subject_id,
  old_parent_source_page_id,new_parent_source_owner_subject_id,
  new_parent_source_page_id,target_dense_ordinal,subtree_count,old_scope_count,
  new_scope_count,affected_page_count,order_generation,created_at
) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


def test_structural_effect_frozen_contracts_are_exact() -> None:
    assert migration.SOURCE_VERSION == migration_1313.TARGET_VERSION == 2026081313
    assert migration.SOURCE_SQL == migration_1313.TARGET_SQL
    assert migration.TARGET_VERSION == store_module._SCHEMA_1314_VERSION == 2026081314
    assert migration.TARGET_SQL == store_module._SCHEMA_1314_SQL
    assert migration.TARGET_CHECKSUM == store_module._SCHEMA_1314_CHECKSUM
    assert (
        migration.TARGET_ACTUAL_SCHEMA_SHA256
        == store_module._SCHEMA_1314_ACTUAL_SCHEMA_SHA256
    )
    assert (
        "sha256:" + hashlib.sha256(migration.TARGET_SQL.encode()).hexdigest()
        == migration.TARGET_CHECKSUM
    )
    assert migration.STRUCTURAL_EFFECT_OBJECTS.issubset(
        {item["name"] for item in migration.TARGET_OBJECTS}
    )


def test_structural_effect_dry_run_apply_cli_and_fresh_schema_are_exact(
    tmp_path: Path,
) -> None:
    root, db = _source_catalog(tmp_path)
    _seed_workspace(db)
    read_con = sqlite3.connect(db)
    try:
        before_workspace = read_con.execute(
            "SELECT id,name,row_version,security_epoch FROM workspaces"
        ).fetchone()
    finally:
        read_con.close()
    first = migration.build_plan(db, root)
    second = migration.build_plan(db, root)
    report, code = cli.run(["--catalog-path", str(db), "--user-data-root", str(root)])
    assert first == second
    assert first["state"] == "migration_required"
    assert report["mode"] == "dry-run"
    assert code == 0
    result = _apply(root, db, _backup(db, root / "workspace.before-1314.bak"))
    assert result["applied"] is True
    assert migration.build_plan(db, root)["state"] == "already_current"
    con = sqlite3.connect(db)
    try:
        assert con.execute(
            "SELECT id,name,row_version,security_epoch FROM workspaces"
        ).fetchone() == before_workspace
        column = next(
            row for row in con.execute("PRAGMA table_info(workspace_page_tree_nodes)")
            if row[1] == "order_generation"
        )
        assert column[3] == 1
        assert str(column[4]) == "1"
    finally:
        con.close()
    con = sqlite3.connect(db)
    try:
        assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert tuple(con.execute(
            "SELECT version,name,checksum,actual_schema_sha256 "
            "FROM workspace_schema_migrations"
        ).fetchone()) == (
            migration.TARGET_VERSION,
            migration.TARGET_NAME,
            migration.TARGET_CHECKSUM,
            migration.TARGET_ACTUAL_SCHEMA_SHA256,
        )
    finally:
        con.close()


def test_structural_effect_refuses_live_1313_command_during_dry_run(
    tmp_path: Path,
) -> None:
    root, db = _source_catalog(tmp_path)
    _seed_workspace(db)
    con = store_module.WorkspaceStore._configure(
        sqlite3.connect(db), query_only=False, healing_clock=lambda: 100
    )
    try:
        con.create_function("workspace_page_tree_actor", 0, lambda: "owner")
        con.create_function("workspace_page_tree_operation_id", 0, lambda: "rename-1")
        con.create_function("workspace_page_tree_action", 0, lambda: "rename")
        con.execute(
            """
            INSERT INTO workspace_page_lifecycle_commands(
              id,workspace_id,actor_subject_id,operation_id,action,status,
              source_owner_subject_id,source_page_id,expected_tree_version,
              expected_binding_version,expected_incarnation_generation,
              request_sha256,created_at,updated_at,expected_workspace_version,
              expected_workspace_security_epoch,expected_tree_state_version
            ) VALUES(?,?,?,?,?,'prepared',?,?,?,?,?,?,?,?,?,?,?)
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
                SHA_A,
                100,
                100,
                1,
                1,
                1,
            ),
        )
        con.commit()
    finally:
        con.close()
    with pytest.raises(migration.MigrationRefused, match="live page lifecycle command"):
        migration.build_plan(db, root)


def test_structural_effect_refuses_invalid_1313_tree_state(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    _seed_workspace(db)
    con = store_module.WorkspaceStore._configure(
        sqlite3.connect(db), query_only=False, healing_clock=lambda: 101
    )
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        con.create_function("workspace_page_tree_actor", 0, lambda: "owner")
        con.create_function("workspace_page_tree_operation_id", 0, lambda: "corrupt")
        con.create_function("workspace_page_tree_action", 0, lambda: "materialize")
        con.execute(
            """
            UPDATE workspace_page_tree_states
            SET materialization_state='partial',active_node_count=1,
                row_version=row_version+1,updated_at=101,
                last_actor_subject_id='owner',last_operation_id='corrupt'
            WHERE workspace_id='ws_test'
            """
        )
        con.commit()
    finally:
        con.close()
    with pytest.raises(migration.MigrationRefused, match="page tree is not healthy"):
        migration.build_plan(db, root)


@pytest.mark.parametrize("phase", ["after_schema", "before_commit"])
def test_structural_effect_fault_rolls_back_exact_1313_source(
    tmp_path: Path, phase: str
) -> None:
    root, db = _source_catalog(tmp_path)
    before = db.read_bytes()

    def fault(current: str) -> None:
        if current == phase:
            raise RuntimeError("injected 1314 migration fault")

    with pytest.raises(RuntimeError, match="injected 1314 migration fault"):
        _apply(
            root,
            db,
            _backup(db, root / f"workspace.{phase}.bak"),
            fault_hook=fault,
        )
    assert db.read_bytes() == before
    assert migration.build_plan(db, root)["state"] == "migration_required"


def test_unconfigured_writer_cannot_create_structural_evidence(tmp_path: Path) -> None:
    db = tmp_path / "workspace.db"
    store = store_module.WorkspaceStore(db, trusted_clock=lambda: 100)
    store.bootstrap()
    con = store._write_connection()
    try:
        assert tuple(
            con.execute(
                "SELECT workspace_page_lifecycle_structural_command_id(),"
                "workspace_page_lifecycle_structural_source_receipt_sha256()"
            ).fetchone()
        ) == ("", "")
        with pytest.raises(sqlite3.IntegrityError, match="manifest context"):
            con.execute(_MANIFEST_INSERT, _manifest_values())
        trigger_sql = {
            row[0]: row[1]
            for row in con.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name IN ("
                "'trg_page_source_structural_create_insert',"
                "'trg_page_source_structural_create_activate',"
                "'trg_workspace_page_tree_node_order_update')"
            )
        }
        assert set(trigger_sql) == {
            "trg_page_source_structural_create_insert",
            "trg_page_source_structural_create_activate",
            "trg_workspace_page_tree_node_order_update",
        }
        assert all("structural" in sql or "order_generation" in sql for sql in trigger_sql.values())
    finally:
        con.rollback()
        con.close()


def test_deep_health_rejects_orphan_structural_manifest(tmp_path: Path) -> None:
    db = tmp_path / "workspace.db"
    store = store_module.WorkspaceStore(db, trusted_clock=lambda: 100)
    store.bootstrap()
    con = sqlite3.connect(db)
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        trigger_sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='trg_workspace_page_lifecycle_structural_manifest_insert'"
        ).fetchone()[0]
        con.execute("DROP TRIGGER trg_workspace_page_lifecycle_structural_manifest_insert")
        con.execute(_MANIFEST_INSERT, _manifest_values())
        con.execute(trigger_sql)
        con.commit()
    finally:
        con.close()
    health = store.deep_health()
    assert health["healthy"] is False
    assert "workspace_page_lifecycle_structural_manifest_invalid" in health["issues"]
