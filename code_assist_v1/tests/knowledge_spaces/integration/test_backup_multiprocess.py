from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
import sqlite3
import time

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.services.backup import (
    BackupService,
    BackupServiceConfiguration,
)


pytestmark = pytest.mark.integration


def _layout_from(values: tuple[str, str, str, str]) -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=Path(values[0]),
        database=Path(values[1]),
        blob_root=Path(values[2]),
        projection_root=Path(values[3]),
    )


def _configuration() -> BackupServiceConfiguration:
    return BackupServiceConfiguration(
        app_version="0.1.0",
        build_revision="a" * 64,
        configuration_version="test-config-v1",
        lease_hmac_key_set_version="test-lease-keys-v1",
    )


def _crash_backup(
    layout_values: tuple[str, str, str, str],
    backup_root: str,
    run_id: str,
    boundary: str,
) -> None:
    layout = _layout_from(layout_values)
    factory = ConnectionFactory._for_isolated_tests(
        layout.database, CANONICAL_MIGRATION_CATALOG
    )

    def crash(name: str) -> None:
        if name == boundary:
            os._exit(73)

    BackupService(
        factory,
        layout,
        backup_root=Path(backup_root),
        configuration=_configuration(),
        failpoint=crash,
    ).create(backup_run_id=run_id)


def _writer(
    layout_values: tuple[str, str, str, str],
    ready: object,
    count: int,
) -> None:
    from code_assist_v1.knowledge_core.db import transaction

    layout = _layout_from(layout_values)
    factory = ConnectionFactory._for_isolated_tests(
        layout.database, CANONICAL_MIGRATION_CATALOG
    )
    connection = factory.open()
    try:
        ready.set()  # type: ignore[attr-defined]
        for index in range(count):
            with transaction(connection):
                user_id = f"writer-{index:03d}"
                connection.execute(
                    """INSERT INTO app_user(
                         id,display_name,password_hash,system_role,account_status,
                         current_part_id,scope_epoch,must_change_password,
                         password_changed_at,created_at,updated_at,disabled_at,row_version
                       ) VALUES (?,?,'hash','user','active',NULL,1,0,1,1,1,NULL,1)""",
                    (user_id, user_id),
                )
            time.sleep(0.01)
    finally:
        connection.close()


def _factory(value) -> tuple[ConnectionFactory, MigrationStorageLayout]:
    layout = MigrationStorageLayout(
        data_root=value.root,
        database=value.core_db_path,
        blob_root=value.blobs_dir,
        projection_root=value.projections_dir,
    )
    factory = ConnectionFactory._for_isolated_tests(
        value.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p7.4-backup-process-test",
        storage_layout=layout,
    ).run()
    return factory, layout


def _values(layout: MigrationStorageLayout) -> tuple[str, str, str, str]:
    return (
        str(layout.data_root),
        str(layout.database),
        str(layout.blob_root),
        str(layout.projection_root),
    )


def _backup_root(value, label: str) -> Path:
    root = value.root.parent / label
    root.mkdir()
    return root


@pytest.mark.scenario("OPS-006")
@pytest.mark.parametrize(
    "boundary",
    (
        "backup.after_hold_acquire",
        "backup.after_database_snapshot",
        "backup.after_blob_manifest",
        "backup.before_finalize_rename",
    ),
)
def test_hard_crash_never_false_completes_and_same_run_recovers(
    isolated_knowledge_layout,
    boundary: str,
) -> None:
    factory, layout = _factory(isolated_knowledge_layout)
    root = _backup_root(
        isolated_knowledge_layout,
        "backup-crash-" + boundary.rsplit(".", 1)[-1],
    )
    run_id = {
        "backup.after_hold_acquire": "00000000-0000-4000-8000-000000007431",
        "backup.after_database_snapshot": "00000000-0000-4000-8000-000000007432",
        "backup.after_blob_manifest": "00000000-0000-4000-8000-000000007433",
        "backup.before_finalize_rename": "00000000-0000-4000-8000-000000007434",
    }[boundary]
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_crash_backup,
        args=(_values(layout), str(root), run_id, boundary),
    )
    process.start()
    process.join(60)
    assert process.exitcode == 73
    assert not (root / f"backup-{run_id}").exists()

    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT state FROM backup_run WHERE id=?", (run_id,)
        ).fetchone()[0] in {"preparing", "copying"}
    finally:
        connection.close()

    result = BackupService(
        factory,
        layout,
        backup_root=root,
        configuration=_configuration(),
    ).create(backup_run_id=run_id)
    assert result.state == "verified"
    assert result.replayed is True
    assert (root / f"backup-{run_id}" / "manifest.json").is_file()


@pytest.mark.scenario("OPS-001")
def test_online_backup_during_real_process_writes_is_integral(
    isolated_knowledge_layout,
) -> None:
    factory, layout = _factory(isolated_knowledge_layout)
    root = _backup_root(isolated_knowledge_layout, "backup-under-write")
    run_id = "00000000-0000-4000-8000-000000007435"
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(target=_writer, args=(_values(layout), ready, 40))
    process.start()
    assert ready.wait(20)

    result = BackupService(
        factory,
        layout,
        backup_root=root,
        configuration=_configuration(),
    ).create(backup_run_id=run_id)
    process.join(60)
    assert process.exitcode == 0
    assert result.state == "verified"

    snapshot = root / f"backup-{run_id}" / "database" / "knowledge-core.sqlite"
    restored = sqlite3.connect(f"{snapshot.as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        assert restored.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert restored.execute("PRAGMA foreign_key_check").fetchall() == []
        snapshot_users = restored.execute(
            "SELECT count(*) FROM app_user WHERE id LIKE 'writer-%'"
        ).fetchone()[0]
        assert 1 <= snapshot_users <= 40
    finally:
        restored.close()
    live = factory.open(read_only=True)
    try:
        assert live.execute(
            "SELECT count(*) FROM app_user WHERE id LIKE 'writer-%'"
        ).fetchone()[0] == 40
    finally:
        live.close()
