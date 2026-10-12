from __future__ import annotations

import hashlib

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.backup import (
    BackupBlobSpec,
    BackupConflictError,
    BackupIntegrityError,
    BackupRepository,
)
from code_assist_v1.knowledge_core.repositories.blob_gc import BlobGcRepository


pytestmark = pytest.mark.unit

_RUN_ID = "00000000-0000-4000-8000-000000007400"
_OTHER_RUN_ID = "00000000-0000-4000-8000-000000007401"


def _factory(value) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        value.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p7.4-backup-repository-test",
        storage_layout=MigrationStorageLayout(
            data_root=value.root,
            database=value.core_db_path,
            blob_root=value.blobs_dir,
            projection_root=value.projections_dir,
        ),
    ).run()
    return factory


def _spec(label: str) -> BackupBlobSpec:
    content = label.encode("utf-8")
    return BackupBlobSpec(hashlib.sha256(content).hexdigest(), len(content))


def _begin(factory: ConnectionFactory, *, run_id: str = _RUN_ID):
    connection = factory.open()
    try:
        with transaction(connection):
            return BackupRepository().begin(
                connection, backup_run_id=run_id, created_at_ms=100
            )
    finally:
        connection.close()


def test_backup_run_installs_exact_holds_and_finalizes_only_after_all_blobs(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    first = _spec("first")
    second = _spec("second")
    assert _begin(factory).state == "preparing"

    connection = factory.open()
    repository = BackupRepository()
    try:
        with transaction(connection):
            copying = repository.begin_copying(
                connection,
                backup_run_id=_RUN_ID,
                snapshot_db_sha256="a" * 64,
                snapshot_audit_high_water=42,
                blobs=(second, first),
            )
            assert copying.state == "copying"
            assert copying.row_version == 2
            assert [row.blob_sha256 for row in repository.list_blobs(connection, _RUN_ID)] == sorted(
                (first.sha256, second.sha256)
            )

        with transaction(connection):
            repository.mark_blob_verified(
                connection,
                backup_run_id=_RUN_ID,
                blob_sha256=first.sha256,
                copied_at_ms=110,
                verified_at_ms=111,
            )
            with pytest.raises(BackupIntegrityError, match="Every referenced"):
                repository.mark_verified(
                    connection,
                    backup_run_id=_RUN_ID,
                    manifest_sha256="b" * 64,
                    completed_at_ms=120,
                )

        with transaction(connection):
            repository.mark_blob_verified(
                connection,
                backup_run_id=_RUN_ID,
                blob_sha256=second.sha256,
                copied_at_ms=112,
                verified_at_ms=113,
            )
            verified = repository.mark_verified(
                connection,
                backup_run_id=_RUN_ID,
                manifest_sha256="b" * 64,
                completed_at_ms=120,
            )
            assert verified.state == "verified"
            assert verified.snapshot_db_relative_path == "database/knowledge-core.sqlite"
            assert verified.snapshot_db_sha256 == "a" * 64
            assert verified.manifest_sha256 == "b" * 64
            assert verified.snapshot_audit_high_water == 42
            assert verified.row_version == 3

        latest = repository.latest_verified(connection)
        assert latest is not None and latest.id == _RUN_ID
    finally:
        connection.close()


def test_begin_and_copying_are_exact_replays_and_reject_drift(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    blob = _spec("one")
    _begin(factory)
    connection = factory.open()
    repository = BackupRepository()
    try:
        with transaction(connection):
            assert repository.begin(
                connection, backup_run_id=_RUN_ID, created_at_ms=100
            ).state == "preparing"
            with pytest.raises(BackupConflictError, match="creation time"):
                repository.begin(
                    connection, backup_run_id=_RUN_ID, created_at_ms=101
                )
        with transaction(connection):
            repository.begin_copying(
                connection,
                backup_run_id=_RUN_ID,
                snapshot_db_sha256="c" * 64,
                snapshot_audit_high_water=7,
                blobs=(blob,),
            )
        with transaction(connection):
            replay = repository.begin_copying(
                connection,
                backup_run_id=_RUN_ID,
                snapshot_db_sha256="c" * 64,
                snapshot_audit_high_water=7,
                blobs=(blob,),
            )
            assert replay.state == "copying"
            with pytest.raises(BackupConflictError, match="differs"):
                repository.begin_copying(
                    connection,
                    backup_run_id=_RUN_ID,
                    snapshot_db_sha256="d" * 64,
                    snapshot_audit_high_water=7,
                    blobs=(blob,),
                )
    finally:
        connection.close()


def test_begin_rejects_a_second_active_backup_until_the_first_is_terminal(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _begin(factory)
    connection = factory.open()
    repository = BackupRepository()
    try:
        with transaction(connection):
            with pytest.raises(BackupConflictError, match="global GC hold"):
                repository.begin(
                    connection,
                    backup_run_id=_OTHER_RUN_ID,
                    created_at_ms=101,
                )
        with transaction(connection):
            repository.mark_failed(
                connection,
                backup_run_id=_RUN_ID,
                error_code="BACKUP_CANCELLED",
                completed_at_ms=102,
            )
        with transaction(connection):
            assert repository.begin(
                connection,
                backup_run_id=_OTHER_RUN_ID,
                created_at_ms=103,
            ).state == "preparing"
    finally:
        connection.close()


def test_verified_blob_and_terminal_run_replay_are_exact(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    blob = _spec("one")
    _begin(factory)
    repository = BackupRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            repository.begin_copying(
                connection,
                backup_run_id=_RUN_ID,
                snapshot_db_sha256="e" * 64,
                snapshot_audit_high_water=0,
                blobs=(blob,),
            )
        with transaction(connection):
            repository.mark_blob_verified(
                connection,
                backup_run_id=_RUN_ID,
                blob_sha256=blob.sha256,
                copied_at_ms=101,
                verified_at_ms=102,
            )
        with transaction(connection):
            assert repository.mark_blob_verified(
                connection,
                backup_run_id=_RUN_ID,
                blob_sha256=blob.sha256,
                copied_at_ms=101,
                verified_at_ms=102,
            ).status == "verified"
            with pytest.raises(BackupConflictError, match="different completion"):
                repository.mark_blob_verified(
                    connection,
                    backup_run_id=_RUN_ID,
                    blob_sha256=blob.sha256,
                    copied_at_ms=101,
                    verified_at_ms=103,
                )
        with transaction(connection):
            repository.mark_verified(
                connection,
                backup_run_id=_RUN_ID,
                manifest_sha256="f" * 64,
                completed_at_ms=105,
            )
        with transaction(connection):
            assert repository.mark_verified(
                connection,
                backup_run_id=_RUN_ID,
                manifest_sha256="f" * 64,
                completed_at_ms=105,
            ).state == "verified"
            with pytest.raises(BackupConflictError, match="different completion"):
                repository.mark_verified(
                    connection,
                    backup_run_id=_RUN_ID,
                    manifest_sha256="0" * 64,
                    completed_at_ms=105,
                )
    finally:
        connection.close()


@pytest.mark.parametrize("copying", [False, True])
def test_failure_is_sanitized_terminal_and_releases_global_gc_hold(
    isolated_knowledge_layout,
    copying: bool,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    blob = _spec("held")
    _begin(factory)
    repository = BackupRepository()
    connection = factory.open()
    try:
        if copying:
            with transaction(connection):
                repository.begin_copying(
                    connection,
                    backup_run_id=_RUN_ID,
                    snapshot_db_sha256="1" * 64,
                    snapshot_audit_high_water=0,
                    blobs=(blob,),
                )
        with transaction(connection):
            failed = repository.mark_failed(
                connection,
                backup_run_id=_RUN_ID,
                error_code="BACKUP_IO_FAILED",
                completed_at_ms=120,
            )
            assert failed.state == "failed"
            assert failed.error_code == "BACKUP_IO_FAILED"
            assert failed.completed_at_ms == 120
            if copying:
                rows = repository.list_blobs(connection, _RUN_ID)
                assert len(rows) == 1
                assert rows[0].status == "failed"
                assert rows[0].error_code == "BACKUP_IO_FAILED"
        assert connection.execute(
            "SELECT count(*) FROM backup_run WHERE state IN ('preparing','copying')"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_preparing_run_blocks_gc_claim_and_claim_blocks_new_backup(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    content = b"gc-race"
    digest = hashlib.sha256(content).hexdigest()
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO blob_object(
                     sha256,size_bytes,media_type,relative_path,verification_status,
                     created_at,verified_at
                   ) VALUES (?,?,'application/octet-stream',?,'verified',1,2)""",
                (digest, len(content), f"{digest[:2]}/{digest[2:4]}/{digest}"),
            )
            connection.execute(
                """INSERT INTO blob_gc_candidate(
                     blob_sha256,unreferenced_since,delete_after,state,claim_owner,
                     claim_expires_at,attempt_count,last_error_json,updated_at
                   ) VALUES (?,0,86400000,'quarantined',NULL,NULL,0,NULL,86400000)""",
                (digest,),
            )
        with transaction(connection):
            BackupRepository().begin(
                connection, backup_run_id=_RUN_ID, created_at_ms=100
            )
        with transaction(connection):
            assert BlobGcRepository().claim_next(
                connection, claim_owner="gc", now_ms=86400001
            ) is None
        with transaction(connection):
            BackupRepository().mark_failed(
                connection,
                backup_run_id=_RUN_ID,
                error_code="BACKUP_CANCELLED",
                completed_at_ms=101,
            )
        with transaction(connection):
            claim = BlobGcRepository().claim_next(
                connection, claim_owner="gc", now_ms=86400001
            )
            assert claim is not None
        with transaction(connection):
            with pytest.raises(Exception, match="BLOB_GC_BACKUP_RACE"):
                BackupRepository().begin(
                    connection, backup_run_id=_OTHER_RUN_ID, created_at_ms=102
                )
    finally:
        connection.close()


def test_repository_mutations_require_caller_transaction_and_rollback_cleanly(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = BackupRepository()
    connection = factory.open()
    try:
        with pytest.raises(Exception, match="existing managed transaction"):
            repository.begin(
                connection, backup_run_id=_RUN_ID, created_at_ms=100
            )
        with pytest.raises(RuntimeError, match="rollback"):
            with transaction(connection):
                repository.begin(
                    connection, backup_run_id=_RUN_ID, created_at_ms=100
                )
                raise RuntimeError("rollback")
        assert repository.get(connection, _RUN_ID) is None
    finally:
        connection.close()


def test_empty_snapshot_can_complete_without_blob_rows(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _begin(factory)
    repository = BackupRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            repository.begin_copying(
                connection,
                backup_run_id=_RUN_ID,
                snapshot_db_sha256="2" * 64,
                snapshot_audit_high_water=0,
                blobs=(),
            )
        with transaction(connection):
            assert repository.mark_verified(
                connection,
                backup_run_id=_RUN_ID,
                manifest_sha256="3" * 64,
                completed_at_ms=101,
            ).state == "verified"
    finally:
        connection.close()
