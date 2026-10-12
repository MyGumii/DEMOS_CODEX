from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_healing_apply_migration as migration
from code_assist_v1 import notion_workspace_healing_migration as migration_1306
from code_assist_v1 import notion_workspace_store as store_module
from code_assist_v1.tools import migrate_notion_workspace_healing_apply as cli


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


def _metadata(path: Path):
    stat = path.stat()
    return (
        stat.st_size,
        stat.st_mtime_ns,
        hashlib.sha256(path.read_bytes()).hexdigest(),
        tuple(sorted(item.name for item in path.parent.iterdir())),
    )


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


def _seed_locator_apply_run(con: sqlite3.Connection) -> None:
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
          0,0,0,0,0,0,0,0,0,0,0,0,'worker.one',?,120,1,1,NULL,100,100,NULL,NULL
        )
        """,
        ("sha256:" + "a" * 64,),
    )


def _seed_fence_and_ledger(con: sqlite3.Connection) -> None:
    con.commit()
    con.execute("BEGIN")
    # This deliberately precedes its operation parent: the FK is deferred so
    # a normal _mutate handler can insert the operation later in the same tx.
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
    con.commit()


def _seed_active_page(con: sqlite3.Connection) -> None:
    con.execute(
        """
        INSERT INTO workspace_page_sources(
          workspace_id,source_owner_subject_id,source_page_id,teamspace_id,
          placement,state,quarantined_at,source_revision,source_updated_at,
          source_sha256,bound_by_subject_id,row_version,created_at,updated_at
        ) VALUES('ws_test','owner','page-1',NULL,'private','pending',NULL,
                 1,100,?,'owner',1,100,100)
        """,
        ("sha256:" + "1" * 64,),
    )
    con.execute(
        "UPDATE workspace_page_sources SET state='active' "
        "WHERE workspace_id='ws_test' AND source_page_id='page-1'"
    )
    con.commit()


def test_frozen_contracts_are_exact_and_1306_remains_independent() -> None:
    assert migration.SOURCE_SQL == migration_1306.TARGET_SQL
    assert migration.SOURCE_VERSION == 2026081306
    assert migration.TARGET_VERSION == 2026081307
    assert store_module.SCHEMA_VERSION == 2026081317
    assert migration.SOURCE_CHECKSUM == (
        "sha256:6f5b23e4a276fd25aa96b1e427e8f4dce55e307fb059095baa46d6178ed13f52"
    )
    assert migration.SOURCE_ACTUAL_SCHEMA_SHA256 == (
        "sha256:abaadac598bea97af44260bdbbf31a9ca883b65e20dbefaabacd1c5be18ec025"
    )
    assert migration.TARGET_SQL == (
        store_module._SCHEMA_SQL + store_module._HEALING_SCHEMA_1307_SQL
    )
    assert migration.TARGET_CHECKSUM == (
        "sha256:161491ac8f1a75444777049d1436a1874bccc15848377e9c5a6343b66a0a275f"
    )
    assert migration.TARGET_ACTUAL_SCHEMA_SHA256 == (
        "sha256:04ff6c79fa7d8e3c51823128a9defaca9253f88b0bb73209475349f94609f825"
    )


def test_dry_run_and_cli_are_read_only(tmp_path) -> None:
    root, db = _source_catalog(tmp_path)
    before = _metadata(db)
    first = migration.build_plan(db, root)
    second = migration.build_plan(db, root)
    report, code = cli.run(
        ["--catalog-path", str(db), "--user-data-root", str(root)]
    )
    assert first == second
    assert report["mode"] == "dry-run"
    assert report["plan_checksum"] == first["plan_checksum"]
    assert code == 0
    assert _metadata(db) == before


def test_apply_matches_frozen_1307_and_newer_runtime_fails_closed(tmp_path) -> None:
    root, db = _source_catalog(tmp_path)
    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.before-1307.bak")
    result = migration.apply_plan(
        db,
        root,
        exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup,
    )
    assert result["applied"] is True
    assert result["commit_recovered"] is False
    runtime = store_module.WorkspaceStore(db)
    assert runtime.capabilities() == {"workspace_v2": False, "schema_version": None}
    with pytest.raises(store_module.WorkspaceSubstrateUnavailable):
        runtime.verify_deep_health()
    assert migration.build_plan(db, root)["state"] == "already_current"


def test_migration_preserves_existing_run_as_shadow(tmp_path) -> None:
    root, db = _source_catalog(tmp_path)
    con = sqlite3.connect(db)
    con.create_function("workspace_healing_now", 0, lambda: 100)
    try:
        _seed_workspace(con)
        con.execute(
            """
            INSERT INTO workspace_healing_runs(
              id,workspace_id,started_by_subject_id,status,
              cursor_owner_subject_id,cursor_page_id,has_more,
              scanned_count,visited_count,match_count,safe_miss_count,stale_count,
              superseded_count,ambiguous_count,permission_mismatch_count,
              unsafe_path_count,catalog_unavailable_count,lease_worker,
              lease_token_sha256,lease_expires_at,fencing_generation,row_version,
              failure_code,started_at,updated_at,completed_at,failed_at
            ) VALUES('wheal_old','ws_test','owner','running',NULL,NULL,1,
                     0,0,0,0,0,0,0,0,0,0,'worker.one',?,120,1,1,NULL,
                     100,100,NULL,NULL)
            """,
            ("sha256:" + "a" * 64,),
        )
        con.commit()
    finally:
        con.close()
    plan = migration.build_plan(db, root)
    backup = _backup(db, root / "workspace.before-1307.bak")
    migration.apply_plan(
        db,
        root,
        exact_plan_checksum=plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=backup,
    )
    con = sqlite3.connect(db)
    try:
        row = con.execute(
            "SELECT healing_mode,oversize_count,source_missing_count "
            "FROM workspace_healing_runs WHERE id='wheal_old'"
        ).fetchone()
    finally:
        con.close()
    assert row == ("shadow", 0, 0)


def test_source_fence_requires_live_apply_run_and_is_immutable(tmp_path) -> None:
    store = store_module.WorkspaceStore(
        store_module.catalog_path_for_user_data_root(tmp_path),
        trusted_clock=lambda: 100,
    )
    store.bootstrap()
    con = _configured(Path(store.db_path))
    try:
        _seed_workspace(con)
        _seed_locator_apply_run(con)
        _seed_fence_and_ledger(con)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            con.execute(
                "UPDATE workspace_healing_source_fences SET source_epoch=4"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            con.execute("DELETE FROM workspace_healing_source_fences")
    finally:
        con.close()


def test_page_proof_refresh_requires_exact_service_run_id(tmp_path) -> None:
    store = store_module.WorkspaceStore(
        store_module.catalog_path_for_user_data_root(tmp_path),
        trusted_clock=lambda: 100,
    )
    store.bootstrap()
    setup = _configured(Path(store.db_path))
    try:
        _seed_workspace(setup)
        _seed_locator_apply_run(setup)
        _seed_fence_and_ledger(setup)
        _seed_active_page(setup)
        with pytest.raises(sqlite3.IntegrityError, match="lifecycle"):
            setup.execute(
                "UPDATE workspace_page_sources SET source_revision=2,"
                "source_sha256=?,row_version=2,updated_at=101 "
                "WHERE workspace_id='ws_test' AND source_page_id='page-1'",
                ("sha256:" + "2" * 64,),
            )
        setup.rollback()
        setup.create_function(
            "workspace_healing_apply_run_id", 0, lambda: "wheal_wrong"
        )
        with pytest.raises(sqlite3.IntegrityError, match="lifecycle"):
            setup.execute(
                "UPDATE workspace_page_sources SET source_revision=2,"
                "source_sha256=?,row_version=2,updated_at=101 "
                "WHERE workspace_id='ws_test' AND source_page_id='page-1'",
                ("sha256:" + "2" * 64,),
            )
        setup.rollback()
        setup.create_function(
            "workspace_healing_apply_run_id", 0, lambda: "wheal_apply"
        )
        setup.execute(
            "UPDATE workspace_page_sources SET source_revision=2,"
            "source_sha256=?,row_version=2,updated_at=101 "
            "WHERE workspace_id='ws_test' AND source_page_id='page-1'",
            ("sha256:" + "2" * 64,),
        )
        setup.commit()
        row = setup.execute(
            "SELECT state,row_version,source_revision FROM workspace_page_sources"
        ).fetchone()
        assert tuple(row) == ("active", 2, 2)
        with pytest.raises(sqlite3.IntegrityError, match="lifecycle"):
            setup.execute(
                "UPDATE workspace_page_sources SET row_version=3,updated_at=102 "
                "WHERE workspace_id='ws_test' AND source_page_id='page-1'"
            )
        setup.rollback()
        setup.create_function(
            "workspace_healing_apply_source_generation", 0, lambda: 7
        )
        setup.execute(
            "UPDATE workspace_page_sources SET state='quarantined',"
            "quarantined_at=102,quarantine_reason='source_archived',"
            "quarantine_source_generation=7,row_version=3,updated_at=102 "
            "WHERE workspace_id='ws_test' AND source_page_id='page-1'"
        )
        setup.commit()
        setup.create_function(
            "workspace_healing_apply_source_generation", 0, lambda: 8
        )
        with pytest.raises(sqlite3.IntegrityError, match="lifecycle"):
            setup.execute(
                "UPDATE workspace_page_sources SET state='active',quarantined_at=NULL,"
                "quarantine_reason=NULL,quarantine_source_generation=NULL,"
                "row_version=4,updated_at=103 "
                "WHERE workspace_id='ws_test' AND source_page_id='page-1'"
            )
        setup.rollback()
        restored = setup.execute(
            "SELECT state,row_version,source_revision,quarantined_at "
            "FROM workspace_page_sources"
        ).fetchone()
        assert tuple(restored) == ("quarantined", 3, 2, 102)
    finally:
        setup.close()


def test_unregistered_raw_writer_fails_closed(tmp_path) -> None:
    store = store_module.WorkspaceStore(
        store_module.catalog_path_for_user_data_root(tmp_path),
        trusted_clock=lambda: 100,
    )
    store.bootstrap()
    setup = _configured(Path(store.db_path))
    try:
        _seed_workspace(setup)
        _seed_locator_apply_run(setup)
        _seed_fence_and_ledger(setup)
        _seed_active_page(setup)
    finally:
        setup.close()
    raw = sqlite3.connect(store.db_path)
    raw.execute("PRAGMA foreign_keys=ON")
    raw.create_function("workspace_healing_now", 0, lambda: 100)
    try:
        with pytest.raises(sqlite3.OperationalError, match="no such function"):
            raw.execute(
                "UPDATE workspace_page_sources SET source_revision=2,"
                "source_sha256=?,row_version=2,updated_at=101 "
                "WHERE workspace_id='ws_test' AND source_page_id='page-1'",
                ("sha256:" + "2" * 64,),
            )
        raw.rollback()
    finally:
        raw.close()
