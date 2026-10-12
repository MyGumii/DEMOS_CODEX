from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_navigation_migration as migration_1310
from code_assist_v1 import notion_workspace_page_tree_migration as migration
from code_assist_v1 import notion_workspace_store as store_module
from code_assist_v1.notion_workspace_store import LegacyPageSourceProof
from code_assist_v1.tools import migrate_notion_workspace_page_tree as cli


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


def test_page_tree_migration_frozen_contracts_are_exact() -> None:
    assert migration.SOURCE_VERSION == migration_1310.TARGET_VERSION == 2026081310
    assert migration.SOURCE_SQL == migration_1310.TARGET_SQL
    assert migration.TARGET_VERSION == store_module._SCHEMA_1311_VERSION == 2026081311
    assert migration.TARGET_SQL == store_module._SCHEMA_1311_SQL
    assert migration.TARGET_CHECKSUM == store_module._SCHEMA_1311_CHECKSUM
    assert (
        migration.TARGET_ACTUAL_SCHEMA_SHA256
        == store_module._SCHEMA_1311_ACTUAL_SCHEMA_SHA256
    )
    assert (
        "sha256:" + hashlib.sha256(migration.TARGET_SQL.encode()).hexdigest()
        == migration.TARGET_CHECKSUM
    )
    assert migration.PAGE_TREE_OBJECTS.issubset(
        {item["name"] for item in migration.TARGET_OBJECTS}
    )


def test_page_tree_migration_dry_run_is_deterministic_and_read_only(
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


def test_page_tree_migration_preserves_catalog_and_starts_unmaterialized(
    tmp_path: Path,
) -> None:
    root, db = _source_catalog(tmp_path)
    _apply(root, db, _backup(db, root / "workspace.before-1311.bak"))

    con = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    try:
        assert tuple(
            con.execute(
                "SELECT version,name,checksum,actual_schema_sha256 "
                "FROM workspace_schema_migrations"
            ).fetchone()
        ) == (
            migration.TARGET_VERSION,
            migration.TARGET_NAME,
            migration.TARGET_CHECKSUM,
            migration.TARGET_ACTUAL_SCHEMA_SHA256,
        )
        assert con.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0] == 1
        assert tuple(
            con.execute(
                "SELECT tree_version,materialization_state,active_node_count,"
                "archived_node_count,deleted_node_count,row_version "
                "FROM workspace_page_tree_states WHERE workspace_id='ws_test'"
            ).fetchone()
        ) == (0, "unmaterialized", 0, 0, 0, 1)
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_tree_nodes"
        ).fetchone()[0] == 0
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_lifecycle_commands"
        ).fetchone()[0] == 0
    finally:
        con.close()
    assert migration.build_plan(db, root)["state"] == "already_current"


def test_fresh_workspace_gets_unmaterialized_state_without_tree_or_command(
    tmp_path: Path,
) -> None:
    db = tmp_path / "fresh" / "workspace.db"
    store = store_module.WorkspaceStore(db, trusted_clock=lambda: 100)
    store.bootstrap()
    created = store.create_workspace(
        "owner", name="Fresh", icon="", operation_id="create_fresh_tree_workspace"
    )
    workspace_id = created["workspace"]["id"]
    report = store.page_tree_readiness("owner", workspace_id)
    assert report["materialization_state"] == "unmaterialized"
    assert report["ready_for_tree_reads"] is False
    assert report["active_node_count"] == 0
    assert store.verify_deep_health()["page_tree_counts"] == {
        "state_count": 1,
        "node_count": 0,
        "command_count": 0,
        "subtree_manifest_count": 0,
        "subtree_effect_count": 0,
        "subtree_mapping_count": 0,
        "complete_workspace_count": 0,
    }


def test_page_tree_tables_fail_closed_without_service_context(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    _apply(root, db, _backup(db, root / "workspace.raw-guard.bak"))
    con = _configured(db, now=101)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="tree state transition"):
            con.execute(
                """
                UPDATE workspace_page_tree_states
                SET materialization_state='partial',tree_version=1,row_version=2,
                    last_actor_subject_id='owner',last_operation_id='forged',
                    updated_at=101
                WHERE workspace_id='ws_test'
                """
            )
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="lifecycle command insert"):
            con.execute(
                """
                INSERT INTO workspace_page_lifecycle_commands(
                  id,workspace_id,actor_subject_id,operation_id,action,status,
                  source_owner_subject_id,source_page_id,expected_tree_version,
                  expected_binding_version,expected_incarnation_generation,
                  request_sha256,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "plc_" + "1" * 32,
                    "ws_test",
                    "owner",
                    "forged",
                    "move",
                    "prepared",
                    "owner",
                    "p_test",
                    0,
                    0,
                    0,
                    "sha256:" + "1" * 64,
                    101,
                    101,
                ),
            )
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="tree state is immutable"):
            con.execute(
                "DELETE FROM workspace_page_tree_states WHERE workspace_id='ws_test'"
            )
    finally:
        con.rollback()
        con.close()


def test_page_tree_node_trigger_requires_materialization_operation_and_exact_parent() -> None:
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(migration.TARGET_SQL)
        sql = " ".join(
            con.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND name='trg_workspace_page_tree_node_binding_insert'"
            ).fetchone()[0].split()
        )
    finally:
        con.close()
    assert "s.materialization_state IN ('partial','complete')" in sql
    assert "s.last_operation_id=NEW.last_operation_id" in sql
    assert "p.lifecycle_state=NEW.lifecycle_state" in sql
    assert "p.depth+1=NEW.depth" in sql


def test_page_tree_readiness_recounts_nodes_and_fails_closed_on_fabricated_state(
    tmp_path: Path,
) -> None:
    store = store_module.WorkspaceStore(
        store_module.catalog_path_for_user_data_root(tmp_path),
        trusted_clock=lambda: 100,
    )
    workspace = store.create_workspace(
        "owner", name="Tree readiness", operation_id="create-tree-readiness"
    )["workspace"]
    workspace_id = workspace["id"]
    store.bind_page_source(
        "owner",
        workspace_id,
        LegacyPageSourceProof(
            actor_subject_id="owner",
            owner_subject_id="owner",
            page_id="page-1",
            revision=1,
            updated_at=100,
            content_sha256="sha256:" + "a" * 64,
        ),
        source_revalidator=lambda _proof: True,
        base_version=1,
        operation_id="bind-tree-readiness",
    )
    con = _configured(store.db_path, now=101)
    try:
        con.create_function("workspace_page_tree_actor", 0, lambda: "owner")
        con.create_function(
            "workspace_page_tree_operation_id", 0, lambda: "bind-tree-readiness"
        )
        con.create_function("workspace_page_tree_action", 0, lambda: "materialize")
        state_before = tuple(
            con.execute(
                "SELECT tree_version,row_version,created_at,updated_at "
                "FROM workspace_page_tree_states WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
        )
        assert state_before[:2] == (0, 1)
        materialized_at = int(state_before[3]) + 1
        con.create_function(
            "workspace_page_tree_now", 0, lambda: materialized_at
        )
        assert tuple(
            con.execute(
                "SELECT workspace_page_tree_now(),workspace_page_tree_actor(),"
                "workspace_page_tree_operation_id(),workspace_page_tree_action()"
            ).fetchone()
        ) == (materialized_at, "owner", "bind-tree-readiness", "materialize")
        con.execute(
            """
            UPDATE workspace_page_tree_states
            SET tree_version=1,materialization_state='complete',active_node_count=1,
                source_fence_set_sha256=?,row_version=2,
                last_actor_subject_id='owner',last_operation_id='bind-tree-readiness',
                updated_at=?
            WHERE workspace_id=?
            """,
            ("sha256:" + "b" * 64, materialized_at, workspace_id),
        )
        con.commit()
    finally:
        con.close()

    readiness = store.page_tree_readiness("owner", workspace_id)
    assert readiness["active_binding_count"] == 1
    assert readiness["active_node_count"] == 1
    assert readiness["ready_for_tree_reads"] is False
    health = store.deep_health()
    assert health["healthy"] is False
    assert "workspace_page_tree_state_invalid" in health["issues"]


@pytest.mark.parametrize("phase", ["after_schema", "before_commit"])
def test_page_tree_migration_fault_rolls_back_exact_source(
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


def test_page_tree_migration_requires_explicit_quiescence_and_backup(
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
