from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.services.backup import (
    BackupService,
    BackupServiceConfiguration,
)
from code_assist_v1.knowledge_core.services.restore import (
    RestoreDrillError,
    RestoreDrillService,
)
from code_assist_v1.knowledge_core.services import restore as restore_module


pytestmark = pytest.mark.integration


def _source(isolated_knowledge_layout):
    layout = MigrationStorageLayout(
        data_root=isolated_knowledge_layout.root,
        database=isolated_knowledge_layout.core_db_path,
        blob_root=isolated_knowledge_layout.blobs_dir,
        projection_root=isolated_knowledge_layout.projections_dir,
    )
    factory = ConnectionFactory._for_isolated_tests(
        layout.database, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p7.4-restore-drill-test",
        storage_layout=layout,
    ).run()
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO app_user(
                     id,display_name,password_hash,system_role,account_status,
                     current_part_id,scope_epoch,must_change_password,
                     password_changed_at,created_at,updated_at,disabled_at,row_version
                   ) VALUES (
                     'restore-admin','Restore Admin','hash','admin','active',
                     NULL,1,0,1,1,1,NULL,1
                   )"""
            )
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (
                     '00000000-0000-4000-8000-000000007450','personal',
                     'restore-admin',NULL,'active',0,0,1,1,1
                   )"""
            )
            connection.execute(
                """INSERT INTO personal_wiki_profile(
                     space_id,owner_user_id,display_name,name_key,is_default,
                     lifecycle_status,lifecycle_generation,created_at,
                     updated_at,row_version
                   ) VALUES (
                     '00000000-0000-4000-8000-000000007450',
                     'restore-admin','내 지식','내 지식',1,'active',1,1,1,1
                   )"""
            )
    finally:
        connection.close()
    return factory, layout


def _backup(factory, layout, isolated_knowledge_layout):
    backup_root = isolated_knowledge_layout.root.parent / (
        "backup-root-" + uuid.uuid4().hex
    )
    backup_root.mkdir()
    run_id = "00000000-0000-4000-8000-000000007451"
    service = BackupService(
        factory,
        layout,
        backup_root=backup_root,
        configuration=BackupServiceConfiguration(
            app_version="0.1.0",
            build_revision="a" * 64,
            configuration_version="restore-test-v1",
            lease_hmac_key_set_version="restore-key-set-v1",
        ),
    )
    result = service.create(backup_run_id=run_id)
    return backup_root, run_id, result


def _target(isolated_knowledge_layout) -> MigrationStorageLayout:
    root = isolated_knowledge_layout.root.parent / (
        "restore-target-" + uuid.uuid4().hex
    )
    database_parent = root / "database"
    blobs = root / "blobs"
    projections = root / "projections"
    for value in (root, database_parent, blobs, projections):
        value.mkdir()
    return MigrationStorageLayout(
        data_root=root,
        database=database_parent / "knowledge-core.sqlite",
        blob_root=blobs,
        projection_root=projections,
    )


@pytest.mark.scenario("OPS-002")
@pytest.mark.scenario("OPS-003")
def test_isolated_restore_meets_objectives_and_rebuilds_all_projections(
    isolated_knowledge_layout,
) -> None:
    factory, source_layout = _source(isolated_knowledge_layout)
    backup_root, run_id, created = _backup(
        factory, source_layout, isolated_knowledge_layout
    )
    target = _target(isolated_knowledge_layout)
    source_database_sha = __import__("hashlib").sha256(
        source_layout.database.read_bytes()
    ).hexdigest()

    result = RestoreDrillService(
        factory, source_layout, backup_root=backup_root
    ).restore(
        backup_run_id=run_id,
        target_layout=target,
        assumed_incident_at_ms=int(time.time() * 1000),
    )

    assert result.passed is True
    assert result.backup_run_id == run_id
    assert result.restored_snapshot_sha256 == created.snapshot_db_sha256
    assert result.rpo_minutes <= 60
    assert result.rto_seconds <= 240 * 60
    assert result.integrity_mismatch_count == 0
    assert result.projection_space_count == 2
    assert result.projection_entry_count > 0
    assert source_database_sha == __import__("hashlib").sha256(
        source_layout.database.read_bytes()
    ).hexdigest()
    assert (target.projection_root / "00000000-0000-4000-8000-000000007450" / "manifest.json").is_file()

    raw = sqlite3.connect(target.database)
    try:
        assert raw.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert raw.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert raw.execute("PRAGMA foreign_key_check").fetchall() == []
        assert raw.execute(
            "SELECT count(*) FROM backup_run WHERE state IN ('preparing','copying')"
        ).fetchone() == (0,)
        assert raw.execute(
            "SELECT error_json FROM backup_run WHERE id=?", (run_id,)
        ).fetchone() == (
            json.dumps(
                {
                    "code": "RESTORED_BACKUP_HOLD_CLOSED",
                    "schema_version": 1,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    finally:
        raw.close()


@pytest.mark.parametrize("target_name", ("manifest.json", "database/knowledge-core.sqlite"))
def test_restore_rejects_tampered_completed_backup_before_target_write(
    isolated_knowledge_layout,
    target_name: str,
) -> None:
    factory, source_layout = _source(isolated_knowledge_layout)
    backup_root, run_id, _created = _backup(
        factory, source_layout, isolated_knowledge_layout
    )
    target = _target(isolated_knowledge_layout)
    tampered = backup_root / f"backup-{run_id}" / target_name
    original = tampered.read_bytes()
    tampered.write_bytes(original + b"tampered")

    with pytest.raises(RestoreDrillError, match="did not complete"):
        RestoreDrillService(
            factory, source_layout, backup_root=backup_root
        ).restore(
            backup_run_id=run_id,
            target_layout=target,
            assumed_incident_at_ms=int(time.time() * 1000),
        )
    assert not target.database.exists()
    assert list(target.blob_root.iterdir()) == []
    assert list(target.projection_root.iterdir()) == []


def test_restore_target_remains_delete_pinned_through_projection_rebuild(
    isolated_knowledge_layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, source_layout = _source(isolated_knowledge_layout)
    backup_root, run_id, _created = _backup(
        factory, source_layout, isolated_knowledge_layout
    )
    target = _target(isolated_knowledge_layout)
    moved = target.projection_root.with_name("moved-projection-root")
    original = restore_module.FullProjectionMaterializer.rebuild_all
    blocked = False

    def prove_pin(self):
        nonlocal blocked
        try:
            os.replace(target.projection_root, moved)
        except OSError:
            blocked = True
        else:  # pragma: no cover - a product regression makes this branch fail
            os.replace(moved, target.projection_root)
        return original(self)

    monkeypatch.setattr(
        restore_module.FullProjectionMaterializer,
        "rebuild_all",
        prove_pin,
    )
    result = RestoreDrillService(
        factory, source_layout, backup_root=backup_root
    ).restore(
        backup_run_id=run_id,
        target_layout=target,
        assumed_incident_at_ms=int(time.time() * 1000),
    )
    assert result.passed is True
    assert blocked is True
    assert target.projection_root.is_dir()
    assert not moved.exists()
