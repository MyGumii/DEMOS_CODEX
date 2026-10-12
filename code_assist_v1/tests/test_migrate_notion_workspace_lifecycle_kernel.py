from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_lifecycle_kernel_migration as migration
from code_assist_v1 import notion_workspace_page_tree_migration as migration_1311
from code_assist_v1 import notion_workspace_store as store_module
from code_assist_v1.tools import migrate_notion_workspace_lifecycle_kernel as cli


TOKEN_A = "sha256:" + "a" * 64
TOKEN_B = "sha256:" + "b" * 64
RECEIPT = "sha256:" + "c" * 64
REQUEST = "sha256:" + "d" * 64


def _configured(path: Path | str, clock: list[int]) -> sqlite3.Connection:
    return store_module.WorkspaceStore._configure(
        sqlite3.connect(path), query_only=False, healing_clock=lambda: clock[0]
    )


def _context(
    con: sqlite3.Connection,
    clock: list[int],
    *,
    operation_id: str,
    action: str = "move",
) -> None:
    con.create_function("workspace_page_tree_now", 0, lambda: clock[0])
    con.create_function("workspace_page_tree_actor", 0, lambda: "owner")
    con.create_function("workspace_page_tree_operation_id", 0, lambda: operation_id)
    con.create_function("workspace_page_tree_action", 0, lambda: action)


def _seed_workspace(con: sqlite3.Connection) -> None:
    con.execute(
        "INSERT INTO workspaces VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("ws_test", "Test", "test", None, "owner", 1, 1, 1, 100, 100),
    )
    con.execute(
        "INSERT INTO workspace_memberships VALUES(?,?,?,?,?,?,?,?)",
        ("ws_test", "owner", "owner", 1, "owner", 100, 100, None),
    )


def _insert_command(
    con: sqlite3.Connection,
    clock: list[int],
    *,
    operation_id: str = "move-page-1",
    command_id: str = "plc_" + "1" * 32,
) -> None:
    _context(con, clock, operation_id=operation_id)
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
            command_id,
            "ws_test",
            "owner",
            operation_id,
            "move",
            "prepared",
            "owner",
            "page-1",
            0,
            0,
            0,
            REQUEST,
            clock[0],
            clock[0],
        ),
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


def _raw_runtime() -> tuple[sqlite3.Connection, list[int]]:
    clock = [100]
    con = _configured(":memory:", clock)
    con.executescript(migration.TARGET_SQL)
    _seed_workspace(con)
    con.commit()
    return con, clock


def _claim(
    con: sqlite3.Connection,
    clock: list[int],
    *,
    operation_id: str = "move-page-1",
    worker: str = "worker.1",
    token: str = TOKEN_A,
    expires_at: int = 150,
    fence: int = 1,
) -> None:
    _context(con, clock, operation_id=operation_id)
    con.execute(
        """
        UPDATE workspace_page_lifecycle_commands
        SET lease_worker=?,lease_token_sha256=?,lease_expires_at=?,
            fencing_generation=?,row_version=row_version+1,updated_at=?
        WHERE actor_subject_id='owner' AND operation_id=?
        """,
        (worker, token, expires_at, fence, clock[0], operation_id),
    )


def test_lifecycle_kernel_migration_frozen_contracts_are_exact() -> None:
    assert migration.SOURCE_VERSION == migration_1311.TARGET_VERSION == 2026081311
    assert migration.SOURCE_SQL == migration_1311.TARGET_SQL
    assert migration.TARGET_VERSION == store_module._SCHEMA_1312_VERSION == 2026081312
    assert migration.TARGET_SQL == store_module._SCHEMA_1312_SQL
    assert migration.TARGET_CHECKSUM == store_module._SCHEMA_1312_CHECKSUM
    assert migration.TARGET_ACTUAL_SCHEMA_SHA256 == store_module._SCHEMA_1312_ACTUAL_SCHEMA_SHA256
    assert "sha256:" + hashlib.sha256(migration.TARGET_SQL.encode()).hexdigest() == migration.TARGET_CHECKSUM
    assert migration.LIFECYCLE_KERNEL_OBJECTS.issubset(
        {item["name"] for item in migration.TARGET_OBJECTS}
    )


def test_lifecycle_kernel_dry_run_apply_and_cli_are_explicit(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    first = migration.build_plan(db, root)
    second = migration.build_plan(db, root)
    report, code = cli.run(["--catalog-path", str(db), "--user-data-root", str(root)])
    assert first == second
    assert first["state"] == "migration_required"
    assert report["mode"] == "dry-run"
    assert code == 0
    result = _apply(root, db, _backup(db, root / "workspace.before-1312.bak"))
    assert result["applied"] is True
    assert migration.build_plan(db, root)["state"] == "already_current"
    con = sqlite3.connect(db)
    try:
        marker = con.execute(
            "SELECT version,name,checksum,actual_schema_sha256 "
            "FROM workspace_schema_migrations"
        ).fetchone()
        assert marker == (
            migration.TARGET_VERSION,
            migration.TARGET_NAME,
            migration.TARGET_CHECKSUM,
            migration.TARGET_ACTUAL_SCHEMA_SHA256,
        )
        assert con.execute(
            "SELECT COUNT(*) FROM workspace_page_lifecycle_commands"
        ).fetchone()[0] == 0
    finally:
        con.close()


def test_lifecycle_kernel_migration_refuses_every_pre_kernel_command(
    tmp_path: Path,
) -> None:
    root, db = _source_catalog(tmp_path)
    clock = [100]
    con = _configured(db, clock)
    try:
        _insert_command(con, clock)
        con.commit()
    finally:
        con.close()
    before = db.read_bytes()
    with pytest.raises(
        migration.MigrationRefused,
        match="untrusted pre-kernel lifecycle commands",
    ):
        _apply(root, db, _backup(db, root / "workspace.command.bak"))
    assert db.read_bytes() == before
    assert migration.build_plan(db, root)["state"] == "migration_required"


@pytest.mark.parametrize("phase", ["after_schema", "before_commit"])
def test_lifecycle_kernel_fault_rolls_back_exact_source(
    tmp_path: Path, phase: str
) -> None:
    root, db = _source_catalog(tmp_path)
    before = db.read_bytes()

    def fault(current: str) -> None:
        if current == phase:
            raise RuntimeError(f"fault:{phase}")

    with pytest.raises(RuntimeError, match=f"fault:{phase}"):
        _apply(root, db, _backup(db, root / f"workspace.{phase}.bak"), fault_hook=fault)
    assert db.read_bytes() == before


@pytest.mark.parametrize(
    ("worker", "expires_at", "fence"),
    [("Worker", 150, 1), ("worker.1", 100, 1), ("worker.1", 401, 1), ("worker.1", 150, 2)],
)
def test_claim_rejects_bad_worker_expiry_and_fence(
    worker: str, expires_at: int, fence: int
) -> None:
    con, clock = _raw_runtime()
    try:
        _insert_command(con, clock)
        with pytest.raises(sqlite3.IntegrityError, match="lifecycle command transition"):
            _claim(con, clock, worker=worker, expires_at=expires_at, fence=fence)
        con.rollback()
    finally:
        con.close()


def test_claim_heartbeat_reclaim_source_apply_and_finalize_are_exact() -> None:
    con, clock = _raw_runtime()
    try:
        _insert_command(con, clock)
        con.commit()
        _claim(con, clock)
        con.commit()
        clock[0] = 101
        _context(con, clock, operation_id="move-page-1")
        con.execute(
            """
            UPDATE workspace_page_lifecycle_commands
            SET lease_expires_at=160,row_version=row_version+1,updated_at=101
            WHERE operation_id='move-page-1'
            """
        )
        con.commit()
        clock[0] = 120
        with pytest.raises(sqlite3.IntegrityError, match="lifecycle command transition"):
            _claim(con, clock, worker="worker.2", token=TOKEN_B, expires_at=170, fence=2)
        con.rollback()

        clock[0] = 160
        _claim(con, clock, worker="worker.2", token=TOKEN_B, expires_at=200, fence=2)
        con.commit()
        clock[0] = 161
        _context(con, clock, operation_id="move-page-1")
        con.execute(
            """
            UPDATE workspace_page_lifecycle_commands
            SET status='source_applied',source_receipt_sha256=?,affected_node_count=2,
                file_work_count=1,row_version=row_version+1,updated_at=161
            WHERE operation_id='move-page-1'
            """,
            (RECEIPT,),
        )
        con.commit()
        clock[0] = 162
        _context(con, clock, operation_id="move-page-1")
        con.execute(
            """
            UPDATE workspace_page_lifecycle_commands
            SET status='finalized',lease_worker=NULL,lease_token_sha256=NULL,
                lease_expires_at=NULL,terminal_at=162,row_version=row_version+1,
                updated_at=162
            WHERE operation_id='move-page-1'
            """
        )
        con.commit()
        row = con.execute(
            "SELECT status,fencing_generation,row_version,affected_node_count,file_work_count "
            "FROM workspace_page_lifecycle_commands WHERE operation_id='move-page-1'"
        ).fetchone()
        assert tuple(row) == ("finalized", 2, 6, 2, 1)
        with pytest.raises(sqlite3.IntegrityError, match="lifecycle command transition"):
            _context(con, clock, operation_id="move-page-1")
            con.execute(
                "UPDATE workspace_page_lifecycle_commands SET row_version=6 "
                "WHERE operation_id='move-page-1'"
            )
        con.rollback()
    finally:
        con.close()


def _insert_operation(
    con: sqlite3.Connection,
    *,
    operation_id: str,
    action: str,
) -> None:
    con.execute(
        """
        INSERT INTO workspace_operations(
          actor_subject_id,operation_id,action,request_hash,workspace_id,
          required_capability,authorization_strength,authorization_context_json,
          result_json,created_at
        ) VALUES('owner',?,?,?,'ws_test','workspace.admin',40,'{}','{}',100)
        """,
        (operation_id, action, REQUEST),
    )


def test_operation_namespace_is_two_way_and_only_exact_finalize_is_allowed() -> None:
    con, clock = _raw_runtime()
    try:
        _insert_operation(con, operation_id="occupied", action="workspace.other")
        con.commit()
        with pytest.raises(sqlite3.IntegrityError, match="namespace conflict"):
            _insert_command(con, clock, operation_id="occupied", command_id="plc_" + "2" * 32)
        con.rollback()

        _insert_command(con, clock)
        con.commit()
        with pytest.raises(sqlite3.IntegrityError, match="namespace conflict"):
            _insert_operation(con, operation_id="move-page-1", action="workspace.other")
        con.rollback()

        _claim(con, clock)
        clock[0] = 101
        _context(con, clock, operation_id="move-page-1")
        con.execute(
            "UPDATE workspace_page_lifecycle_commands SET status='source_applied',"
            "source_receipt_sha256=?,row_version=3,updated_at=101 "
            "WHERE operation_id='move-page-1'",
            (RECEIPT,),
        )
        clock[0] = 102
        _context(con, clock, operation_id="move-page-1")
        con.execute(
            "UPDATE workspace_page_lifecycle_commands SET status='finalized',"
            "lease_worker=NULL,lease_token_sha256=NULL,lease_expires_at=NULL,"
            "terminal_at=102,row_version=4,updated_at=102 "
            "WHERE operation_id='move-page-1'"
        )
        _insert_operation(
            con,
            operation_id="move-page-1",
            action=store_module.PAGE_LIFECYCLE_FINALIZED_ACTION,
        )
        assert con.execute(
            "SELECT action FROM workspace_operations WHERE operation_id='move-page-1'"
        ).fetchone()[0] == store_module.PAGE_LIFECYCLE_FINALIZED_ACTION
    finally:
        con.rollback()
        con.close()


def test_deep_health_detects_bypassed_lease_and_namespace_corruption(
    tmp_path: Path,
) -> None:
    db = tmp_path / "catalog" / "workspace.db"
    db.parent.mkdir(parents=True)
    clock = [100]
    con = _configured(db, clock)
    try:
        con.executescript(migration.TARGET_SQL)
        con.execute(
            "INSERT INTO workspace_schema_migrations VALUES(?,?,?,?,?)",
            (
                migration.TARGET_VERSION,
                migration.TARGET_NAME,
                migration.TARGET_CHECKSUM,
                migration.TARGET_ACTUAL_SCHEMA_SHA256,
                100,
            ),
        )
        _seed_workspace(con)
        _insert_command(con, clock)
        con.commit()

        transition_sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='trg_workspace_page_lifecycle_command_transition'"
        ).fetchone()[0]
        namespace_sql = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='trg_workspace_operation_lifecycle_namespace_insert'"
        ).fetchone()[0]
        con.execute("DROP TRIGGER trg_workspace_page_lifecycle_command_transition")
        con.execute("DROP TRIGGER trg_workspace_operation_lifecycle_namespace_insert")
        con.execute(
            """
            UPDATE workspace_page_lifecycle_commands
            SET lease_worker='Worker',lease_token_sha256=?,lease_expires_at=1000,
                row_version=2,updated_at=100
            WHERE operation_id='move-page-1'
            """,
            (TOKEN_A,),
        )
        _insert_operation(
            con,
            operation_id="move-page-1",
            action="workspace.other",
        )
        con.execute(transition_sql)
        con.execute(namespace_sql)
        con.commit()
    finally:
        con.close()

    con = sqlite3.connect(db)
    try:
        invalid = con.execute(
            """
            SELECT COUNT(*) FROM workspace_page_lifecycle_commands c
            WHERE typeof(c.lease_worker)<>'text'
               OR c.lease_worker NOT GLOB '[a-z][a-z0-9_.-]*'
               OR EXISTS (
                 SELECT 1 FROM workspace_operations op
                 WHERE op.actor_subject_id=c.actor_subject_id
                   AND op.operation_id=c.operation_id
                   AND NOT (
                     c.status='finalized'
                     AND op.workspace_id=c.workspace_id
                     AND op.action='workspace.page_lifecycle_finalized'
                   )
               )
            """
        ).fetchone()[0]
        assert invalid == 1
    finally:
        con.close()
