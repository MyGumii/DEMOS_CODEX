from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_store as store_module
from code_assist_v1 import notion_workspace_structural_effect_migration as migration_1314
from code_assist_v1 import notion_workspace_structural_finalize_migration as migration
from code_assist_v1.tools import migrate_notion_workspace_structural_finalize as cli


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


def test_structural_finalize_frozen_contracts_are_exact() -> None:
    assert migration.SOURCE_VERSION == migration_1314.TARGET_VERSION == 2026081314
    assert migration.SOURCE_SQL == migration_1314.TARGET_SQL
    assert migration.TARGET_VERSION == store_module._SCHEMA_1315_VERSION == 2026081315
    assert migration.TARGET_SQL == store_module._SCHEMA_1315_SQL
    assert migration.TARGET_CHECKSUM == store_module._SCHEMA_1315_CHECKSUM
    assert (
        migration.TARGET_ACTUAL_SCHEMA_SHA256
        == store_module._SCHEMA_1315_ACTUAL_SCHEMA_SHA256
    )
    assert (
        "sha256:" + hashlib.sha256(migration.TARGET_SQL.encode()).hexdigest()
        == migration.TARGET_CHECKSUM
    )
    assert migration.REPLACED_OBJECTS.issubset(
        {item["name"] for item in migration.TARGET_OBJECTS}
    )


def test_structural_finalize_dry_run_apply_cli_and_fresh_schema_are_exact(
    tmp_path: Path,
) -> None:
    root, db = _source_catalog(tmp_path)
    first = migration.build_plan(db, root)
    second = migration.build_plan(db, root)
    report, code = cli.run(["--catalog-path", str(db), "--user-data-root", str(root)])
    assert first == second
    assert first["state"] == "migration_required"
    assert report["mode"] == "dry-run"
    assert code == 0
    result = _apply(root, db, _backup(db, root / "workspace.before-1315.bak"))
    assert result["applied"] is True
    assert migration.build_plan(db, root)["state"] == "already_current"

    migrated_con = sqlite3.connect(db)
    fresh_con = sqlite3.connect(":memory:")
    try:
        fresh_con.executescript(migration.TARGET_SQL)
        query = (
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
            "ORDER BY type,name"
        )
        assert migrated_con.execute(query).fetchall() == fresh_con.execute(query).fetchall()
    finally:
        fresh_con.close()
        migrated_con.close()


@pytest.mark.parametrize("phase", ["after_schema", "before_commit"])
def test_structural_finalize_fault_rolls_back_exact_1314_source(
    tmp_path: Path, phase: str
) -> None:
    root, db = _source_catalog(tmp_path)
    before = db.read_bytes()

    def fault(current: str) -> None:
        if current == phase:
            raise RuntimeError("injected 1315 migration fault")

    with pytest.raises(RuntimeError, match="injected 1315 migration fault"):
        _apply(
            root,
            db,
            _backup(db, root / f"workspace.{phase}.bak"),
            fault_hook=fault,
        )
    assert db.read_bytes() == before
    assert migration.build_plan(db, root)["state"] == "migration_required"


def _order_trigger_sql() -> str:
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(migration.TARGET_SQL)
        return str(
            con.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND name='trg_workspace_page_tree_node_order_update'"
            ).fetchone()[0]
        )
    finally:
        con.close()


def test_structural_finalize_order_generation_uses_exact_effect_roles() -> None:
    con = sqlite3.connect(":memory:")
    try:
        con.create_function("workspace_page_tree_action", 0, lambda: "move")
        con.create_function(
            "workspace_page_lifecycle_structural_command_id",
            0,
            lambda: "plc_" + "1" * 32,
        )
        con.create_function(
            "workspace_page_lifecycle_structural_source_receipt_sha256",
            0,
            lambda: "sha256:" + "a" * 64,
        )
        con.executescript(
            """
            CREATE TABLE workspace_page_tree_nodes(
              workspace_id TEXT,source_owner_subject_id TEXT,source_page_id TEXT,
              binding_version INTEGER,parent_source_owner_subject_id TEXT,
              parent_source_page_id TEXT,sibling_ordinal INTEGER,depth INTEGER,
              order_generation INTEGER
            );
            CREATE TABLE workspace_page_lifecycle_structural_manifests(
              command_id TEXT,workspace_id TEXT,action TEXT,
              source_operation_receipt_sha256 TEXT,structure_changed INTEGER,
              order_generation INTEGER
            );
            CREATE TABLE workspace_page_lifecycle_structural_effects(
              command_id TEXT,workspace_id TEXT,source_owner_subject_id TEXT,
              source_page_id TEXT,before_binding_version INTEGER,
              after_binding_version INTEGER,old_parent_source_page_id TEXT,
              new_parent_source_page_id TEXT,old_sibling_ordinal INTEGER,
              new_sibling_ordinal INTEGER,old_depth INTEGER,new_depth INTEGER,
              effect_roles INTEGER
            );
            """
        )
        con.execute(_order_trigger_sql())
        command_id = "plc_" + "1" * 32
        receipt = "sha256:" + "a" * 64
        con.execute(
            "INSERT INTO workspace_page_lifecycle_structural_manifests "
            "VALUES(?,?,?,?,?,?)",
            (command_id, "ws_test", "move", receipt, 1, 2),
        )
        con.executemany(
            "INSERT INTO workspace_page_lifecycle_structural_effects "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                (
                    command_id, "ws_test", "owner", "parent", 1, 2,
                    None, None, 1, 1, 0, 0,
                    store_module.PAGE_LIFECYCLE_EFFECT_NEW_PARENT,
                ),
                (
                    command_id, "ws_test", "owner", "target", 1, 1,
                    "parent", "parent", 1, 1, 1, 1,
                    store_module.PAGE_LIFECYCLE_EFFECT_TARGET
                    | store_module.PAGE_LIFECYCLE_EFFECT_OLD_SCOPE
                    | store_module.PAGE_LIFECYCLE_EFFECT_NEW_SCOPE
                    | store_module.PAGE_LIFECYCLE_EFFECT_SUBTREE,
                ),
            ),
        )
        con.executemany(
            "INSERT INTO workspace_page_tree_nodes VALUES(?,?,?,?,?,?,?,?,?)",
            (
                ("ws_test", "owner", "parent", 1, None, None, 1, 0, 1),
                ("ws_test", "owner", "target", 1, "owner", "parent", 1, 1, 1),
            ),
        )

        with pytest.raises(sqlite3.IntegrityError, match="order generation"):
            con.execute(
                "UPDATE workspace_page_tree_nodes SET binding_version=2,"
                "order_generation=2 WHERE source_page_id='parent'"
            )
        con.execute(
            "UPDATE workspace_page_tree_nodes SET binding_version=2 "
            "WHERE source_page_id='parent'"
        )

        with pytest.raises(sqlite3.IntegrityError, match="order generation"):
            con.execute(
                "UPDATE workspace_page_tree_nodes SET binding_version=1 "
                "WHERE source_page_id='target'"
            )
        con.execute(
            "UPDATE workspace_page_tree_nodes SET order_generation=2 "
            "WHERE source_page_id='target'"
        )
        assert con.execute(
            "SELECT source_page_id,binding_version,order_generation "
            "FROM workspace_page_tree_nodes ORDER BY source_page_id"
        ).fetchall() == [("parent", 2, 1), ("target", 1, 2)]
    finally:
        con.close()


def test_structural_finalize_replacement_trigger_is_narrow() -> None:
    source_con = sqlite3.connect(":memory:")
    target_con = sqlite3.connect(":memory:")
    try:
        source_con.executescript(migration.SOURCE_SQL)
        target_con.executescript(migration.TARGET_SQL)
        query = (
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL ORDER BY type,name"
        )
        source = {row[1]: row for row in source_con.execute(query)}
        target = {row[1]: row for row in target_con.execute(query)}
        assert set(source) == set(target)
        assert {
            name for name in source if source[name] != target[name]
        } == migration.REPLACED_OBJECTS
    finally:
        target_con.close()
        source_con.close()
