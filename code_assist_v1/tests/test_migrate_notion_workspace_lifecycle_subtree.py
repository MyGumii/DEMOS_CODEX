from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_lifecycle_subtree_migration as migration
from code_assist_v1 import notion_workspace_store as store_module
from code_assist_v1 import notion_workspace_structural_finalize_migration as migration_1315
from code_assist_v1.tools import migrate_notion_workspace_lifecycle_subtree as cli


REQUEST = "sha256:" + "d" * 64
TOKEN = "sha256:" + "a" * 64


def _configured(path: Path | str, clock: list[int]) -> sqlite3.Connection:
    return store_module.WorkspaceStore._configure(
        sqlite3.connect(path), query_only=False, healing_clock=lambda: clock[0]
    )


def _context(
    con: sqlite3.Connection,
    clock: list[int],
    *,
    operation_id: str,
    action: str,
    command_id: str = "",
) -> None:
    con.create_function("workspace_page_tree_now", 0, lambda: clock[0])
    con.create_function("workspace_page_tree_actor", 0, lambda: "owner")
    con.create_function("workspace_page_tree_operation_id", 0, lambda: operation_id)
    con.create_function("workspace_page_tree_action", 0, lambda: action)
    con.create_function(
        "workspace_page_lifecycle_subtree_command_id", 0, lambda: command_id
    )


def _seed_workspace(con: sqlite3.Connection) -> None:
    con.execute(
        "INSERT INTO workspaces VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("ws_test", "Test", "test", None, "owner", 1, 1, 1, 100, 100),
    )
    con.execute(
        "INSERT INTO workspace_memberships VALUES(?,?,?,?,?,?,?,?)",
        ("ws_test", "owner", "owner", 1, "owner", 100, 100, None),
    )


def _source_catalog(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "user-data"
    db = root / ".notion-workspace-v2" / "workspace.db"
    db.parent.mkdir(parents=True)
    clock = [100]
    con = _configured(db, clock)
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
        _seed_workspace(con)
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


def _insert_source_command(
    db: Path,
    *,
    action: str = "duplicate",
    terminal: bool = False,
) -> None:
    clock = [100]
    operation_id = f"{action}-existing"
    command_id = "plc_" + "1" * 32
    con = _configured(db, clock)
    try:
        _context(
            con,
            clock,
            operation_id=operation_id,
            action=action,
            command_id=command_id,
        )
        con.execute(
            """
            INSERT INTO workspace_page_lifecycle_commands(
              id,workspace_id,actor_subject_id,operation_id,action,status,
              source_owner_subject_id,source_page_id,expected_tree_version,
              expected_binding_version,expected_incarnation_generation,
              request_sha256,created_at,updated_at,expected_workspace_version,
              expected_workspace_security_epoch,expected_tree_state_version
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                command_id,
                "ws_test",
                "owner",
                operation_id,
                action,
                "prepared",
                "owner",
                "page-1",
                0,
                0,
                0,
                REQUEST,
                100,
                100,
                1,
                1,
                1,
            ),
        )
        if terminal:
            con.execute(
                """
                UPDATE workspace_page_lifecycle_commands
                SET lease_worker='worker.1',lease_token_sha256=?,
                    lease_expires_at=150,fencing_generation=1,
                    row_version=row_version+1,updated_at=100
                WHERE id=?
                """,
                (TOKEN, command_id),
            )
            con.execute(
                """
                UPDATE workspace_page_lifecycle_commands
                SET status='failed',failure_code='review_required',terminal_at=100,
                    lease_worker=NULL,lease_token_sha256=NULL,lease_expires_at=NULL,
                    row_version=row_version+1,updated_at=100
                WHERE id=?
                """,
                (command_id,),
            )
        con.commit()
    finally:
        con.close()


def test_lifecycle_subtree_frozen_contracts_are_exact() -> None:
    assert migration.SOURCE_VERSION == migration_1315.TARGET_VERSION == 2026081315
    assert migration.SOURCE_SQL == migration_1315.TARGET_SQL
    assert migration.TARGET_VERSION == store_module._SCHEMA_1316_VERSION == 2026081316
    assert migration.TARGET_SQL == store_module._SCHEMA_1316_SQL
    assert migration.TARGET_CHECKSUM == store_module._SCHEMA_1316_CHECKSUM
    assert (
        migration.TARGET_ACTUAL_SCHEMA_SHA256
        == store_module._SCHEMA_1316_ACTUAL_SCHEMA_SHA256
    )
    assert (
        "sha256:" + hashlib.sha256(migration.TARGET_SQL.encode()).hexdigest()
        == migration.TARGET_CHECKSUM
    )
    assert len(migration.SOURCE_OBJECTS) == 159
    assert len(migration.TARGET_OBJECTS) == 179


def test_lifecycle_subtree_dry_run_apply_and_fresh_schema_are_exact(
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

    result = _apply(root, db, _backup(db, root / "workspace.before-1316.bak"))
    assert result["applied"] is True
    assert migration.build_plan(db, root)["state"] == "already_current"

    migrated = sqlite3.connect(db)
    fresh_path = tmp_path / "fresh.db"
    fresh = sqlite3.connect(fresh_path)
    try:
        fresh.executescript(migration.TARGET_SQL)
        fresh.execute(
            "INSERT INTO workspace_schema_migrations VALUES(?,?,?,?,?)",
            (
                migration.TARGET_VERSION,
                migration.TARGET_NAME,
                migration.TARGET_CHECKSUM,
                migration.TARGET_ACTUAL_SCHEMA_SHA256,
                100,
            ),
        )
        fresh.commit()
        query = (
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
            "ORDER BY type,name"
        )
        assert migrated.execute(query).fetchall() == fresh.execute(query).fetchall()
        assert migrated.execute(
            "SELECT canonical_lifecycle_action "
            "FROM workspace_page_lifecycle_commands"
        ).fetchall() == []
        assert migrated.execute(
            "SELECT canonical_lifecycle_state FROM workspace_page_tree_nodes"
        ).fetchall() == []
    finally:
        fresh.close()
        migrated.close()


@pytest.mark.parametrize("phase", ["after_schema", "before_commit"])
def test_lifecycle_subtree_fault_rolls_back_exact_1315_source(
    tmp_path: Path, phase: str
) -> None:
    root, db = _source_catalog(tmp_path)
    before = db.read_bytes()

    def fault(current: str) -> None:
        if current == phase:
            raise RuntimeError("injected 1316 migration fault")

    with pytest.raises(RuntimeError, match="injected 1316 migration fault"):
        _apply(
            root,
            db,
            _backup(db, root / f"workspace.{phase}.bak"),
            fault_hook=fault,
        )
    assert db.read_bytes() == before
    assert migration.build_plan(db, root)["state"] == "migration_required"


def test_lifecycle_subtree_refuses_live_source_command(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    _insert_source_command(db)
    with pytest.raises(migration.MigrationRefused, match="live page lifecycle"):
        migration.build_plan(db, root)


def test_lifecycle_subtree_refuses_ambiguous_terminal_history(
    tmp_path: Path,
) -> None:
    root, db = _source_catalog(tmp_path)
    _insert_source_command(db, terminal=True)
    with pytest.raises(migration.MigrationRefused, match="ambiguous historical"):
        migration.build_plan(db, root)


def test_runtime_canonical_lifecycle_command_is_service_context_only(
    tmp_path: Path,
) -> None:
    db = tmp_path / "current.db"
    runtime = store_module.WorkspaceStore(db, trusted_clock=lambda: 100)
    runtime.bootstrap()
    created = runtime.create_workspace(
        "owner", name="Lifecycle", operation_id="create-lifecycle-workspace"
    )
    workspace_id = created["workspace"]["id"]
    con = runtime._write_connection()
    try:
        con.create_function("workspace_page_tree_actor", 0, lambda: "owner")
        con.create_function(
            "workspace_page_tree_operation_id", 0, lambda: "duplicate-new"
        )
        con.create_function("workspace_page_tree_action", 0, lambda: "duplicate")
        with pytest.raises(sqlite3.IntegrityError, match="canonical lifecycle"):
            con.execute(
                """
                INSERT INTO workspace_page_lifecycle_commands(
                  id,workspace_id,actor_subject_id,operation_id,action,status,
                  source_owner_subject_id,source_page_id,expected_tree_version,
                  expected_binding_version,expected_incarnation_generation,
                  request_sha256,created_at,updated_at,expected_workspace_version,
                  expected_workspace_security_epoch,expected_tree_state_version,
                  canonical_lifecycle_action
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "plc_" + "2" * 32,
                    workspace_id,
                    "owner",
                    "duplicate-new",
                    "duplicate",
                    "prepared",
                    "owner",
                    "page-1",
                    0,
                    0,
                    0,
                    REQUEST,
                    100,
                    100,
                    1,
                    1,
                    1,
                    "duplicate",
                ),
            )
    finally:
        con.close()


def test_runtime_subtree_evidence_is_exact_context_immutable_and_healthy(
    tmp_path: Path,
) -> None:
    db = tmp_path / "evidence.db"
    clock = [100]
    runtime = store_module.WorkspaceStore(db, trusted_clock=lambda: clock[0])
    runtime.bootstrap()
    created = runtime.create_workspace(
        "owner", name="Evidence", operation_id="create-evidence-workspace"
    )
    workspace_id = created["workspace"]["id"]
    command_id = "plc_" + "3" * 32
    operation_id = "duplicate-evidence"
    receipt = "sha256:" + "e" * 64
    source_receipt = "sha256:" + "1" * 64
    result_receipt = "sha256:" + "2" * 64
    page_sha = "sha256:" + "3" * 64
    con = runtime._write_connection()
    try:
        _context(
            con,
            clock,
            operation_id=operation_id,
            action="duplicate",
            command_id=command_id,
        )
        con.execute(
            """
            INSERT INTO workspace_page_lifecycle_commands(
              id,workspace_id,actor_subject_id,operation_id,action,status,
              source_owner_subject_id,source_page_id,expected_tree_version,
              expected_binding_version,expected_incarnation_generation,
              request_sha256,created_at,updated_at,expected_workspace_version,
              expected_workspace_security_epoch,expected_tree_state_version,
              canonical_lifecycle_action
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                command_id,
                workspace_id,
                "owner",
                operation_id,
                "duplicate",
                "prepared",
                "owner",
                "p_source",
                0,
                0,
                0,
                REQUEST,
                100,
                100,
                1,
                1,
                1,
                "duplicate",
            ),
        )
        con.execute(
            """
            UPDATE workspace_page_lifecycle_commands
            SET lease_worker='worker.1',lease_token_sha256=?,lease_expires_at=150,
                fencing_generation=1,row_version=row_version+1,updated_at=100
            WHERE id=?
            """,
            (TOKEN, command_id),
        )
        con.execute(
            """
            UPDATE workspace_page_lifecycle_commands
            SET status='source_applied',source_receipt_sha256=?,
                affected_node_count=1,file_work_count=0,
                row_version=row_version+1,updated_at=100
            WHERE id=?
            """,
            (receipt, command_id),
        )

        manifest_values = (
            command_id,
            workspace_id,
            "duplicate",
            "owner",
            "p_source",
            "p_result",
            REQUEST,
            receipt,
            "sha256:" + "4" * 64,
            "sha256:" + "5" * 64,
            1,
            2,
            1,
            1,
            1,
            "",
            "",
            1,
            1,
            0,
            0,
            1,
            100,
        )
        manifest_sql = """
            INSERT INTO workspace_page_lifecycle_subtree_manifests(
              command_id,workspace_id,canonical_action,source_owner_subject_id,
              root_source_page_id,result_root_source_page_id,source_request_sha256,
              source_operation_receipt_sha256,post_manifest_sha256,
              file_manifest_sha256,source_device,source_inode,source_link_count,
              source_epoch,full_sweep_generation,trash_source_operation_id,
              placement_mode,effect_count,mapping_count,file_mapping_count,
              aggregate_file_bytes,order_generation,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        with pytest.raises(sqlite3.IntegrityError, match="manifest context"):
            con.create_function(
                "workspace_page_lifecycle_subtree_source_receipt_sha256",
                0,
                lambda: "",
            )
            con.execute(manifest_sql, manifest_values)
        con.create_function(
            "workspace_page_lifecycle_subtree_source_receipt_sha256",
            0,
            lambda: receipt,
        )
        con.execute(manifest_sql, manifest_values)
        con.execute(
            """
            INSERT INTO workspace_page_lifecycle_subtree_effects(
              command_id,effect_ordinal,workspace_id,source_owner_subject_id,
              source_page_id,result_page_id,source_incarnation_id,
              source_incarnation_generation,source_revision,
              source_change_generation,result_incarnation_id,
              result_incarnation_generation,result_revision,
              result_change_generation,before_lifecycle,after_lifecycle,
              before_parent_source_page_id,after_parent_source_page_id,
              before_sibling_ordinal,after_sibling_ordinal,transitioned,
              before_binding_version,after_binding_version,
              before_source_revision,before_source_updated_at,before_page_sha256,
              after_source_revision,after_source_updated_at,after_page_sha256,
              before_source_receipt_sha256,after_source_receipt_sha256,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                command_id,
                1,
                workspace_id,
                "owner",
                "p_source",
                "p_result",
                "inc_" + "1" * 32,
                1,
                1,
                1,
                "inc_" + "2" * 32,
                1,
                1,
                1,
                "absent",
                "active",
                "",
                "",
                None,
                1,
                1,
                0,
                1,
                1,
                100,
                page_sha,
                1,
                100,
                page_sha,
                source_receipt,
                result_receipt,
                100,
            ),
        )
        con.execute(
            """
            INSERT INTO workspace_page_lifecycle_object_mappings(
              command_id,mapping_ordinal,mapping_kind,source_object_id,
              result_object_id,source_page_id,result_page_id,byte_size,
              content_sha256,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                command_id,
                1,
                "page",
                "p_source",
                "p_result",
                "p_source",
                "p_result",
                0,
                "",
                100,
            ),
        )
        con.execute(
            """
            INSERT INTO workspace_page_sources(
              workspace_id,source_owner_subject_id,source_page_id,teamspace_id,
              placement,state,quarantined_at,source_revision,source_updated_at,
              source_sha256,bound_by_subject_id,row_version,created_at,updated_at,
              quarantine_reason,quarantine_source_generation
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                workspace_id,
                "owner",
                "p_result",
                None,
                "private",
                "pending",
                None,
                1,
                100,
                page_sha,
                "owner",
                1,
                100,
                100,
                None,
                None,
            ),
        )
        con.create_function(
            "workspace_page_lifecycle_subtree_source_receipt_sha256",
            0,
            lambda: "sha256:" + "f" * 64,
        )
        with pytest.raises(sqlite3.IntegrityError, match="transition authority"):
            con.execute(
                "UPDATE workspace_page_sources SET state='active' "
                "WHERE workspace_id=? AND source_page_id='p_result'",
                (workspace_id,),
            )
        con.create_function(
            "workspace_page_lifecycle_subtree_source_receipt_sha256",
            0,
            lambda: receipt,
        )
        con.execute(
            "UPDATE workspace_page_sources SET state='active' "
            "WHERE workspace_id=? AND source_page_id='p_result'",
            (workspace_id,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            con.execute(
                "UPDATE workspace_page_lifecycle_object_mappings "
                "SET result_object_id='p_other' WHERE command_id=?",
                (command_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            con.execute(
                "DELETE FROM workspace_page_lifecycle_subtree_effects "
                "WHERE command_id=?",
                (command_id,),
            )
        con.commit()
    finally:
        con.close()

    health = runtime.deep_health()
    assert health["healthy"] is False
    assert "workspace_page_lifecycle_subtree_evidence_invalid" in health["issues"]
    assert health["page_tree_counts"] == {
        "state_count": 1,
        "node_count": 0,
        "command_count": 1,
        "subtree_manifest_count": 1,
        "subtree_effect_count": 1,
        "subtree_mapping_count": 1,
        "complete_workspace_count": 0,
    }


def test_lifecycle_subtree_modules_import_without_app_boundary_or_fs_effects(
    tmp_path: Path,
) -> None:
    script = r'''import json,sys
before=set(sys.modules)
import code_assist_v1.notion_workspace_lifecycle_subtree_migration
import code_assist_v1.tools.migrate_notion_workspace_lifecycle_subtree
blocked=("app_code","app_notion","auth","config","data_paths","knowledge_core",
         "routes_notion_bridge","routes_chat_stream","second_brain")
print(json.dumps(sorted(name for name in set(sys.modules)-before
                        if any(part in name.lower() for part in blocked))))
'''
    import subprocess
    import sys

    before = tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")))
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        check=True,
    )
    assert completed.stdout.strip() == "[]"
    assert tuple(sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))) == before
