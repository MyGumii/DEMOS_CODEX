from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_incarnation_migration as migration
from code_assist_v1 import notion_workspace_navigation_migration as migration_1310
from code_assist_v1 import notion_workspace_page_tree_migration as migration_1311
from code_assist_v1 import notion_workspace_lifecycle_kernel_migration as migration_1312
from code_assist_v1 import notion_workspace_lifecycle_finalize_migration as migration_1313
from code_assist_v1 import notion_workspace_structural_effect_migration as migration_1314
from code_assist_v1 import notion_workspace_structural_finalize_migration as migration_1315
from code_assist_v1 import notion_workspace_lifecycle_subtree_migration as migration_1316
from code_assist_v1 import (
    notion_workspace_lifecycle_subtree_finalize_migration as migration_1317,
)
from code_assist_v1 import notion_workspace_quarantine_migration as migration_1308
from code_assist_v1 import notion_workspace_store as store_module
from code_assist_v1.tools import migrate_notion_workspace_incarnation as cli


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


def _seed_workspace_and_binding(con: sqlite3.Connection, *, state: str) -> None:
    con.execute(
        "INSERT INTO workspaces VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("ws_test", "Test", "test", None, "owner", 1, 1, 1, 100, 100),
    )
    con.execute(
        "INSERT INTO workspace_memberships VALUES(?,?,?,?,?,?,?,?)",
        ("ws_test", "owner", "owner", 1, "owner", 100, 100, None),
    )
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
    if state == "active":
        con.execute(
            "UPDATE workspace_page_sources SET state='active' WHERE source_page_id='page-1'"
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


def test_frozen_contracts_and_runtime_hash_are_exact() -> None:
    assert migration.SOURCE_SQL == migration_1308.TARGET_SQL
    assert migration.SOURCE_VERSION == 2026081308
    assert migration.TARGET_VERSION == store_module._SCHEMA_1309_VERSION == 2026081309
    assert migration.TARGET_SQL == store_module._SCHEMA_1309_SQL
    assert migration.TARGET_CHECKSUM == store_module._SCHEMA_1309_CHECKSUM
    assert (
        migration.TARGET_ACTUAL_SCHEMA_SHA256
        == store_module._SCHEMA_1309_ACTUAL_SCHEMA_SHA256
    )
    assert (
        "sha256:" + hashlib.sha256(migration.TARGET_SQL.encode()).hexdigest()
        == migration.TARGET_CHECKSUM
    )
    assert migration.INCARNATION_ATTESTATION_OBJECTS.issubset(
        {item["name"] for item in migration.TARGET_OBJECTS}
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


@pytest.mark.parametrize("state", ["pending", "active"])
def test_migration_preserves_existing_binding_unattested_and_healthy(
    tmp_path: Path, state: str
) -> None:
    root, db = _source_catalog(tmp_path)
    con = _configured(db)
    try:
        _seed_workspace_and_binding(con, state=state)
        con.commit()
    finally:
        con.close()
    _apply(root, db, _backup(db, root / "workspace.before-1309.bak"))
    assert migration.build_plan(db, root)["state"] == "already_current"
    navigation_plan = migration_1310.build_plan(db, root)
    migration_1310.apply_plan(
        db,
        root,
        exact_plan_checksum=navigation_plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=_backup(db, root / "workspace.before-1310.bak"),
    )
    page_tree_plan = migration_1311.build_plan(db, root)
    migration_1311.apply_plan(
        db,
        root,
        exact_plan_checksum=page_tree_plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=_backup(db, root / "workspace.before-1311.bak"),
    )
    lifecycle_plan = migration_1312.build_plan(db, root)
    migration_1312.apply_plan(
        db,
        root,
        exact_plan_checksum=lifecycle_plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=_backup(db, root / "workspace.before-1312.bak"),
    )
    finalize_plan = migration_1313.build_plan(db, root)
    migration_1313.apply_plan(
        db,
        root,
        exact_plan_checksum=finalize_plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=_backup(db, root / "workspace.before-1313.bak"),
    )
    structural_plan = migration_1314.build_plan(db, root)
    migration_1314.apply_plan(
        db,
        root,
        exact_plan_checksum=structural_plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=_backup(db, root / "workspace.before-1314.bak"),
    )
    structural_finalize_plan = migration_1315.build_plan(db, root)
    migration_1315.apply_plan(
        db,
        root,
        exact_plan_checksum=structural_finalize_plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=_backup(db, root / "workspace.before-1315.bak"),
    )
    subtree_plan = migration_1316.build_plan(db, root)
    migration_1316.apply_plan(
        db,
        root,
        exact_plan_checksum=subtree_plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=_backup(db, root / "workspace.before-1316.bak"),
    )
    subtree_finalize_plan = migration_1317.build_plan(db, root)
    migration_1317.apply_plan(
        db,
        root,
        exact_plan_checksum=subtree_finalize_plan["plan_checksum"],
        backup_confirmed=True,
        catalog_quiesced_confirmed=True,
        backup_path=_backup(db, root / "workspace.before-1317.bak"),
    )

    store = store_module.WorkspaceStore(db, trusted_clock=lambda: 100)
    health = store.verify_deep_health()
    readiness = store.incarnation_attestation_readiness("owner", "ws_test")
    assert health["healthy"] is True
    assert health["incarnation_attestation_readiness"] == {
        key: value for key, value in readiness.items() if key != "workspace_id"
    }
    assert readiness["attestation_count"] == 0
    assert readiness["unattested_active_binding_count"] == (state == "active")
    assert readiness["ready_for_resolver_cutover"] is False
    with pytest.raises(migration_1310.MigrationRefused):
        migration_1310.build_plan(db, root)
    with pytest.raises(migration_1311.MigrationRefused):
        migration_1311.build_plan(db, root)
    with pytest.raises(migration_1312.MigrationRefused):
        migration_1312.build_plan(db, root)
    with pytest.raises(migration_1313.MigrationRefused):
        migration_1313.build_plan(db, root)
    with pytest.raises(migration_1314.MigrationRefused):
        migration_1314.build_plan(db, root)
    with pytest.raises(migration_1315.MigrationRefused):
        migration_1315.build_plan(db, root)
    with pytest.raises(migration_1316.MigrationRefused):
        migration_1316.build_plan(db, root)
    assert migration_1317.build_plan(db, root)["state"] == "already_current"


def test_raw_attestation_insert_requires_service_context_and_exact_visible_parent(
    tmp_path: Path,
) -> None:
    store = store_module.WorkspaceStore(
        store_module.catalog_path_for_user_data_root(tmp_path),
        trusted_clock=lambda: 100,
    )
    store.bootstrap()
    con = _configured(Path(store.db_path))
    try:
        _seed_workspace_and_binding(con, state="active")
        con.commit()
        receipt_sha256 = store_module.legacy_incarnation_receipt_sha256(
            source_owner_id="owner",
            page_id="page-1",
            incarnation_id="inc_" + "a" * 32,
            incarnation_generation=1,
            incarnation_retired=False,
            change_generation=6,
            change_mask=31,
            source_exists=True,
            source_active=True,
            source_epoch=2,
            full_sweep_generation=3,
            source_device=4,
            source_inode=5,
            source_link_count=1,
        )
        values = (
            "ws_test", "owner", "page-1", 1, "inc_" + "a" * 32, 1, 0,
            2, 3, 4, 5, 1, 6, 31, 1, 1,
            receipt_sha256,
            store_module.LEGACY_INCARNATION_RECEIPT_DOMAIN,
            "owner", "review-1", 100,
        )
        insert = """
            INSERT INTO workspace_page_source_incarnation_attestations(
              workspace_id,source_owner_subject_id,source_page_id,binding_version,
              source_incarnation_id,source_incarnation_generation,
              source_incarnation_retired,source_epoch,full_sweep_generation,
              source_device,source_inode,source_link_count,
              source_change_generation,source_change_mask,source_exists,source_active,
              source_receipt_sha256,source_receipt_domain,reviewed_by_subject_id,
              review_operation_id,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        with pytest.raises(sqlite3.IntegrityError, match="service context"):
            con.execute(insert, values)
        con.rollback()

        attestation = store_module.LegacyPageIncarnationAttestation(
            workspace_id="ws_test",
            owner_subject_id="owner",
            page_id="page-1",
            binding_version=1,
            incarnation_id="inc_" + "a" * 32,
            incarnation_generation=1,
            incarnation_retired=False,
            source_epoch=2,
            full_sweep_generation=3,
            source_device=4,
            source_inode=5,
            source_link_count=1,
            change_generation=6,
            change_mask=31,
            source_exists=True,
            source_active=True,
            receipt_sha256=receipt_sha256,
            reviewed_by_subject_id="owner",
            review_operation_id="review-1",
            created_at=100,
        )
        projection_sha256 = store_module._page_projection_sha256(
            workspace_id="ws_test",
            source_owner_subject_id="owner",
            source_page_id="page-1",
            binding_version=1,
            file_sources=(),
            public_sources=(),
        )
        projection = {
            "binding_version": 1,
            "projection_sha256": projection_sha256,
            "file_source_count": 0,
            "public_source_count": 0,
            "changed": True,
        }
        operation_result = {
            "workspace": {
                "id": "ws_test",
                "name": "Test",
                "icon": "",
                "owner_subject_id": "owner",
                "version": 1,
                "security_epoch": 1,
                "members_can_create_teamspaces": True,
                "created_at": 100,
                "updated_at": 100,
            },
            "binding": {
                "workspace_id": "ws_test",
                "source_owner_subject_id": "owner",
                "source_page_id": "page-1",
                "state": "active",
                "version": 1,
            },
            "attestation": attestation.descriptor(),
            "projection": projection,
        }
        result_json = json.dumps(
            operation_result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload_json = json.dumps(
            {
                "attestation": attestation.descriptor(),
                "projection": projection,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        target_id = "owner:page-1:1"
        authorization_json = json.dumps(
            {
                "source_owner_subject_id": "owner",
                "teamspace_id": "",
                "workspace_id": "ws_test",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        con.execute(
            """
            INSERT INTO workspace_operations VALUES(
              'owner','review-1','workspace.page_source_incarnation_attested',
              'sha256:request','ws_test','workspace.page_source.bind_admin',40,?,
              ?,100
            )
            """,
            (authorization_json, result_json),
        )
        con.execute(
            """
            INSERT INTO workspace_audit_events VALUES(
              'waud_review','ws_test','owner',
              'workspace.page_source_incarnation_attested',
              'legacy_page_source_incarnation',?,'review-1',?,100
            )
            """,
            (target_id, payload_json),
        )
        con.execute(
            """
            INSERT INTO workspace_outbox VALUES(
              'wout_review','ws_test','owner',
              'workspace.page_source_incarnation_attested',
              'legacy_page_source_incarnation',?,'review-1',?,100,NULL
            )
            """,
            (target_id, payload_json),
        )
        con.execute(
            "SELECT 1"
        )
        con.create_function("workspace_incarnation_attestation_actor", 0, lambda: "owner")
        con.create_function(
            "workspace_incarnation_attestation_operation_id", 0, lambda: "review-1"
        )
        con.execute(
            """
            INSERT INTO workspace_page_source_projections(
              workspace_id,source_owner_subject_id,source_page_id,
              binding_version,projection_sha256,file_source_count,
              public_source_count,projected_at
            ) VALUES('ws_test','owner','page-1',1,?,0,0,100)
            """,
            (projection_sha256,),
        )
        con.commit()
        for index, invalid in (
            (6, "not-an-integer"),
            (6, 0.5),
            (14, "not-an-integer"),
            (14, 0.5),
            (15, "not-an-integer"),
            (15, 0.5),
        ):
            invalid_values = list(values)
            invalid_values[index] = invalid
            with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
                con.execute(insert, tuple(invalid_values))
            con.rollback()
        con.execute(insert, values)
        con.commit()
        assert store.incarnation_attestation_readiness("owner", "ws_test") == {
            "workspace_id": "ws_test",
            "binding_count": 1,
            "active_binding_count": 1,
            "pending_binding_count": 0,
            "quarantined_binding_count": 0,
            "attestation_count": 1,
            "current_attestation_count": 1,
            "historical_attestation_count": 0,
            "attested_active_binding_count": 1,
            "unattested_active_binding_count": 0,
            "attested_pending_binding_count": 0,
            "unattested_pending_binding_count": 0,
            "ready_for_resolver_cutover": True,
        }
        assert store.verify_deep_health()["healthy"] is True

        with pytest.raises(sqlite3.IntegrityError, match="already exists"):
            con.execute(insert, values)
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="already exists"):
            con.execute(insert.replace("INSERT INTO", "INSERT OR REPLACE INTO"), values)
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            con.execute(
                "UPDATE workspace_page_source_incarnation_attestations "
                "SET created_at=101"
            )
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            con.execute("DELETE FROM workspace_page_source_incarnation_attestations")
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="audit ledger"):
            con.execute("UPDATE workspace_audit_events SET created_at=101")
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="audit ledger"):
            con.execute("DELETE FROM workspace_audit_events")
        con.rollback()

        con.execute(
            "UPDATE workspace_page_sources SET state='quarantined',quarantined_at=101,"
            "quarantine_reason='manual',quarantine_source_generation=2,"
            "row_version=2,updated_at=101 WHERE source_page_id='page-1'"
        )
        con.commit()
        quarantined_values = (*values[:3], 2, *values[4:])
        with pytest.raises(sqlite3.IntegrityError, match="visible candidate"):
            con.execute(insert, quarantined_values)
        con.rollback()
    finally:
        con.close()


@pytest.mark.parametrize("fault_point", ["after_schema", "before_commit"])
def test_fault_rolls_back_empty_attestation_schema_and_marker(
    tmp_path: Path, fault_point: str
) -> None:
    root, db = _source_catalog(tmp_path)
    backup = _backup(db, root / "workspace.before-1309.bak")
    before = _metadata(db)

    def fault(point: str) -> None:
        if point == fault_point:
            raise RuntimeError("injected incarnation migration fault")

    with pytest.raises(RuntimeError, match="injected"):
        _apply(root, db, backup, fault_hook=fault)
    assert _metadata(db) == before
    assert migration.build_plan(db, root)["state"] == "migration_required"


def test_attestation_receipt_digest_commits_nonce_generation_and_full_fence() -> None:
    base = dict(
        source_owner_id="owner",
        page_id="page-1",
        incarnation_id="inc_" + "a" * 32,
        incarnation_generation=1,
        incarnation_retired=False,
        change_generation=2,
        change_mask=31,
        source_exists=True,
        source_active=True,
        source_epoch=3,
        full_sweep_generation=4,
        source_device=5,
        source_inode=6,
        source_link_count=1,
    )
    original = store_module.legacy_incarnation_receipt_sha256(**base)
    for key in (
        "incarnation_generation", "source_epoch", "full_sweep_generation",
        "source_device", "source_inode", "source_link_count",
    ):
        changed = dict(base)
        changed[key] += 1
        assert store_module.legacy_incarnation_receipt_sha256(**changed) != original
