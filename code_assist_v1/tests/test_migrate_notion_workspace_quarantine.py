from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_healing_apply_migration as migration_1307
from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_quarantine_migration as migration
from code_assist_v1 import notion_workspace_store as store_module
from code_assist_v1.tools import migrate_notion_workspace_quarantine as cli


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


def _schema_contract(path: Path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        return migration_core._objects(con), migration_core.actual_schema_sha256(con)
    finally:
        con.close()


def _configured(path: Path, *, now: int = 100) -> sqlite3.Connection:
    return store_module.WorkspaceStore._configure(
        sqlite3.connect(path),
        query_only=False,
        healing_clock=lambda: now,
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


def _seed_page(
    con: sqlite3.Connection,
    page_id: str,
    *,
    state: str = "active",
) -> None:
    con.execute(
        """
        INSERT INTO workspace_page_sources(
          workspace_id,source_owner_subject_id,source_page_id,teamspace_id,
          placement,state,quarantined_at,source_revision,source_updated_at,
          source_sha256,bound_by_subject_id,row_version,created_at,updated_at
        ) VALUES('ws_test','owner',?,NULL,'private','pending',NULL,
                 1,100,?,'owner',1,100,100)
        """,
        (page_id, "sha256:" + hashlib.sha256(page_id.encode()).hexdigest()),
    )
    if state in {"active", "quarantined"}:
        con.execute(
            "UPDATE workspace_page_sources SET state='active' "
            "WHERE workspace_id='ws_test' AND source_page_id=?",
            (page_id,),
        )
    if state == "quarantined":
        con.execute(
            "UPDATE workspace_page_sources SET state='quarantined',"
            "quarantined_at=101,row_version=2,updated_at=101 "
            "WHERE workspace_id='ws_test' AND source_page_id=?",
            (page_id,),
        )


def _seed_live_apply_run_and_fence(con: sqlite3.Connection) -> None:
    con.execute(
        """
        INSERT INTO workspace_healing_runs(
          id,workspace_id,started_by_subject_id,healing_mode,status,
          cursor_owner_subject_id,cursor_page_id,has_more,
          scanned_count,visited_count,match_count,safe_miss_count,stale_count,
          superseded_count,ambiguous_count,permission_mismatch_count,
          unsafe_path_count,catalog_unavailable_count,oversize_count,
          source_missing_count,lease_worker,lease_token_sha256,lease_expires_at,
          fencing_generation,row_version,failure_code,started_at,updated_at,
          completed_at,failed_at
        ) VALUES(
          'wheal_apply','ws_test','owner','locator_apply','running',NULL,NULL,1,
          0,0,0,0,0,0,0,0,0,0,0,0,'worker.one',?,120,1,1,NULL,
          100,100,NULL,NULL
        )
        """,
        ("sha256:" + "a" * 64,),
    )
    con.execute(
        """
        INSERT INTO workspace_operations(
          actor_subject_id,operation_id,action,request_hash,workspace_id,
          required_capability,authorization_strength,
          authorization_context_json,result_json,created_at
        ) VALUES('owner','register-fence','workspace.healing_source_fenced',
                 'sha256:test','ws_test','workspace.admin',40,
                 '{"workspace_id":"ws_test"}','{}',100)
        """
    )
    con.execute(
        """
        INSERT INTO workspace_healing_source_fences(
          workspace_id,run_id,source_owner_subject_id,source_epoch,
          full_sweep_generation,source_device,source_inode,source_link_count,
          registered_by_subject_id,operation_id,created_at
        ) VALUES('ws_test','wheal_apply','owner',3,7,-456,-789,1,
                 'owner','register-fence',100)
        """
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


def test_frozen_contracts_and_import_chain_are_exact() -> None:
    assert migration.SOURCE_SQL == migration_1307.TARGET_SQL
    assert migration.SOURCE_VERSION == 2026081307
    assert migration.SOURCE_NAME == "notion_workspace_v2_np2a_healing_apply_fences"
    assert migration.SOURCE_CHECKSUM == (
        "sha256:161491ac8f1a75444777049d1436a1874bccc15848377e9c5a6343b66a0a275f"
    )
    assert migration.SOURCE_ACTUAL_SCHEMA_SHA256 == (
        "sha256:04ff6c79fa7d8e3c51823128a9defaca9253f88b0bb73209475349f94609f825"
    )
    assert migration.TARGET_SQL == store_module._SCHEMA_1308_SQL
    assert migration.TARGET_VERSION == 2026081308
    assert migration.TARGET_NAME == "notion_workspace_v2_np2a_quarantine_reasons"
    assert migration.TARGET_CHECKSUM == (
        "sha256:30e8b20db620f743f47a2af0f31ab78213ed35e4adbf8d5b9e4f406c6ed6ccd7"
    )
    assert migration.TARGET_ACTUAL_SCHEMA_SHA256 == (
        "sha256:72659383f302480d79c816cafba2fe491bc8c0fed43d3e29f9baccd51208fbd9"
    )
    assert (
        "sha256:" + hashlib.sha256(migration.TARGET_SQL.encode("utf-8")).hexdigest()
        == migration.TARGET_CHECKSUM
    )


def test_dry_run_and_cli_are_deterministic_and_read_only(tmp_path: Path) -> None:
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


def test_migrated_catalog_matches_fresh_1308_object_contract(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    backup = _backup(db, root / "workspace.before-1308.bak")
    result = _apply(root, db, backup)

    # Build the reviewed frozen 1308 image directly.  A fresh WorkspaceStore
    # now bootstraps the later runtime schema and must not be used as a moving
    # alias for this historical migration target.
    fresh_db = tmp_path / "fresh-1308.db"
    fresh_con = _configured(fresh_db)
    try:
        fresh_con.executescript(migration.TARGET_SQL)
        fresh_con.execute(
            "INSERT INTO workspace_schema_migrations VALUES(?,?,?,?,?)",
            (
                migration.TARGET_VERSION,
                migration.TARGET_NAME,
                migration.TARGET_CHECKSUM,
                migration.TARGET_ACTUAL_SCHEMA_SHA256,
                100,
            ),
        )
        fresh_con.commit()
    finally:
        fresh_con.close()

    expected = (migration.TARGET_OBJECTS, migration.TARGET_ACTUAL_SCHEMA_SHA256)
    assert result["applied"] is True
    assert result["commit_recovered"] is False
    assert _schema_contract(db) == expected
    assert _schema_contract(fresh_db) == expected
    assert migration.build_plan(db, root)["state"] == "already_current"
    assert migration.build_plan(db, root)["state"] == "already_current"


def test_existing_quarantine_is_backfilled_and_other_states_stay_null(
    tmp_path: Path,
) -> None:
    root, db = _source_catalog(tmp_path)
    con = _configured(db)
    try:
        _seed_workspace(con)
        _seed_page(con, "page-pending", state="pending")
        _seed_page(con, "page-active", state="active")
        _seed_page(con, "page-quarantined", state="quarantined")
        con.commit()
    finally:
        con.close()
    backup = _backup(db, root / "workspace.before-1308.bak")
    _apply(root, db, backup)

    con = sqlite3.connect(db)
    try:
        rows = con.execute(
            "SELECT source_page_id,state,quarantine_reason,"
            "quarantine_source_generation FROM workspace_page_sources "
            "ORDER BY source_page_id"
        ).fetchall()
    finally:
        con.close()
    assert rows == [
        ("page-active", "active", None, None),
        ("page-pending", "pending", None, None),
        ("page-quarantined", "quarantined", "legacy_unknown", 1),
    ]


def test_apply_requires_quiescence_confirmation_even_with_live_run(
    tmp_path: Path,
) -> None:
    root, db = _source_catalog(tmp_path)
    con = _configured(db)
    try:
        _seed_workspace(con)
        _seed_live_apply_run_and_fence(con)
        con.commit()
    finally:
        con.close()
    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.before-1308.bak")
    before = _metadata(db)

    with pytest.raises(migration.MigrationRefused, match="quiesced"):
        migration.apply_plan(
            db,
            root,
            exact_plan_checksum=plan["plan_checksum"],
            backup_confirmed=True,
            catalog_quiesced_confirmed=False,
            backup_path=backup,
        )
    assert _metadata(db) == before

    _apply(root, db, backup)
    con = sqlite3.connect(db)
    try:
        row = con.execute(
            "SELECT healing_mode,status,fencing_generation,row_version "
            "FROM workspace_healing_runs WHERE id='wheal_apply'"
        ).fetchone()
    finally:
        con.close()
    assert row == ("locator_apply", "running", 1, 1)


@pytest.mark.parametrize("fault_point", ["after_schema", "before_commit"])
def test_fault_rolls_back_schema_backfill_and_marker(
    tmp_path: Path, fault_point: str
) -> None:
    root, db = _source_catalog(tmp_path)
    con = _configured(db)
    try:
        _seed_workspace(con)
        _seed_page(con, "page-quarantined", state="quarantined")
        con.commit()
    finally:
        con.close()
    backup = _backup(db, root / "workspace.before-1308.bak")
    before = _metadata(db)

    def fault(point: str) -> None:
        if point == fault_point:
            raise RuntimeError("injected quarantine migration fault")

    with pytest.raises(RuntimeError, match="injected"):
        _apply(root, db, backup, fault_hook=fault)
    assert _metadata(db) == before
    assert migration.build_plan(db, root)["state"] == "migration_required"
    con = sqlite3.connect(db)
    try:
        columns = {
            row[1] for row in con.execute("PRAGMA table_info(workspace_page_sources)")
        }
        marker = con.execute(
            "SELECT version,name FROM workspace_schema_migrations"
        ).fetchone()
    finally:
        con.close()
    assert "quarantine_reason" not in columns
    assert "quarantine_source_generation" not in columns
    assert marker == (migration.SOURCE_VERSION, migration.SOURCE_NAME)


def test_apply_rejects_unverified_or_nonmatching_backup(tmp_path: Path) -> None:
    root, db = _source_catalog(tmp_path)
    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.before-1308.bak")
    common = {
        "catalog_path": db,
        "user_data_root": root,
        "exact_plan_checksum": plan["plan_checksum"],
        "catalog_quiesced_confirmed": True,
    }
    with pytest.raises(migration.MigrationRefused, match="backup-confirmed"):
        migration.apply_plan(
            **common, backup_confirmed=False, backup_path=backup
        )

    wrong_backup = root / "wrong.bak"
    sqlite3.connect(wrong_backup).close()
    with pytest.raises(migration.MigrationRefused, match="backup"):
        migration.apply_plan(
            **common, backup_confirmed=True, backup_path=wrong_backup
        )
    assert migration.build_plan(db, root)["state"] == "migration_required"


def test_source_quarantine_raw_writes_require_udf_run_and_owner_fence(
    tmp_path: Path,
) -> None:
    store = store_module.WorkspaceStore(
        store_module.catalog_path_for_user_data_root(tmp_path),
        trusted_clock=lambda: 100,
    )
    store.bootstrap()
    setup = _configured(Path(store.db_path))
    try:
        _seed_workspace(setup)
        _seed_page(setup, "page-1")
        setup.commit()
    finally:
        setup.close()

    update = (
        "UPDATE workspace_page_sources SET state='quarantined',"
        "quarantined_at=101,quarantine_reason='source_archived',"
        "quarantine_source_generation=7,row_version=2,updated_at=101 "
        "WHERE workspace_id='ws_test' AND source_page_id='page-1'"
    )
    raw = sqlite3.connect(store.db_path)
    raw.execute("PRAGMA foreign_keys=ON")
    raw.create_function("workspace_healing_now", 0, lambda: 100)
    try:
        with pytest.raises(sqlite3.OperationalError, match="no such function"):
            raw.execute(update)
        raw.rollback()
    finally:
        raw.close()

    guarded = _configured(Path(store.db_path))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="lifecycle"):
            guarded.execute(update)
        guarded.rollback()
        guarded.create_function(
            "workspace_healing_apply_run_id", 0, lambda: "wheal_apply"
        )
        guarded.create_function(
            "workspace_healing_apply_source_generation", 0, lambda: 7
        )
        with pytest.raises(sqlite3.IntegrityError, match="lifecycle"):
            guarded.execute(update)
        guarded.rollback()

        _seed_live_apply_run_and_fence(guarded)
        guarded.execute(update)
        guarded.commit()
    finally:
        guarded.close()


def test_archive_restore_is_blocked_and_delete_requires_fenced_new_generation(
    tmp_path: Path,
) -> None:
    store = store_module.WorkspaceStore(
        store_module.catalog_path_for_user_data_root(tmp_path),
        trusted_clock=lambda: 100,
    )
    store.bootstrap()
    con = _configured(Path(store.db_path))
    generation = {"value": 7}
    try:
        _seed_workspace(con)
        _seed_page(con, "page-restore")
        _seed_page(con, "page-delete")
        _seed_live_apply_run_and_fence(con)
        con.create_function(
            "workspace_healing_apply_run_id", 0, lambda: "wheal_apply"
        )
        con.create_function(
            "workspace_healing_apply_source_generation",
            0,
            lambda: generation["value"],
        )
        con.execute(
            "UPDATE workspace_page_sources SET state='quarantined',"
            "quarantined_at=101,quarantine_reason='source_archived',"
            "quarantine_source_generation=7,row_version=2,updated_at=101 "
            "WHERE source_page_id='page-restore'"
        )
        con.execute(
            "UPDATE workspace_page_sources SET state='quarantined',"
            "quarantined_at=101,quarantine_reason='source_archived',"
            "quarantine_source_generation=7,row_version=2,updated_at=101 "
            "WHERE source_page_id='page-delete'"
        )
        con.commit()

        restore = (
            "UPDATE workspace_page_sources SET state='active',"
            "quarantined_at=NULL,quarantine_reason=NULL,"
            "quarantine_source_generation=NULL,row_version=3,updated_at=102 "
            "WHERE source_page_id='page-restore'"
        )
        archive_to_delete = (
            "UPDATE workspace_page_sources SET quarantine_reason='source_deleted',"
            "quarantine_source_generation=8,row_version=3,updated_at=102 "
            "WHERE source_page_id='page-delete'"
        )
        for invalid_generation in (7, 9):
            generation["value"] = invalid_generation
            with pytest.raises(sqlite3.IntegrityError, match="lifecycle"):
                con.execute(restore)
            con.rollback()
            with pytest.raises(sqlite3.IntegrityError, match="lifecycle"):
                con.execute(archive_to_delete)
            con.rollback()

        generation["value"] = 8
        with pytest.raises(sqlite3.IntegrityError, match="lifecycle"):
            con.execute(restore)
        con.rollback()
        con.execute(archive_to_delete)
        con.commit()

        generation["value"] = 9
        with pytest.raises(sqlite3.IntegrityError, match="lifecycle"):
            con.execute(
                "UPDATE workspace_page_sources SET state='active',"
                "quarantined_at=NULL,quarantine_reason=NULL,"
                "quarantine_source_generation=NULL,row_version=4,updated_at=103 "
                "WHERE source_page_id='page-delete'"
            )
        con.rollback()
        rows = con.execute(
            "SELECT source_page_id,state,quarantine_reason,"
            "quarantine_source_generation,row_version "
            "FROM workspace_page_sources ORDER BY source_page_id"
        ).fetchall()
    finally:
        con.close()
    assert [tuple(row) for row in rows] == [
        ("page-delete", "quarantined", "source_deleted", 8, 3),
        ("page-restore", "quarantined", "source_archived", 7, 2),
    ]
