from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid

import pytest

from code_assist_v1.knowledge_core.backup_manifest import backup_manifest_errors
from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.services.backup import (
    BackupService,
    BackupServiceConfiguration,
    BackupServiceError,
)


pytestmark = pytest.mark.unit


def _factory(value) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        value.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p7.4-backup-service-test",
        storage_layout=MigrationStorageLayout(
            data_root=value.root,
            database=value.core_db_path,
            blob_root=value.blobs_dir,
            projection_root=value.projections_dir,
        ),
    ).run()
    return factory


def _layout(value) -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=value.root,
        database=value.core_db_path,
        blob_root=value.blobs_dir,
        projection_root=value.projections_dir,
    )


def _backup_root(value) -> Path:
    root = value.root.parent / ("backup-root-" + uuid.uuid4().hex)
    root.mkdir()
    return root


def _configuration() -> BackupServiceConfiguration:
    return BackupServiceConfiguration(
        app_version="0.1.0",
        build_revision="a" * 64,
        configuration_version="test-config-v1",
        lease_hmac_key_set_version="test-lease-keys-v1",
    )


def _service(value, factory, backup_root, *, failpoint=None) -> BackupService:
    return BackupService(
        factory,
        _layout(value),
        backup_root=backup_root,
        configuration=_configuration(),
        failpoint=failpoint,
    )


def _seed_referenced_blob(factory: ConnectionFactory, value) -> tuple[str, bytes]:
    content = b"p7.4 immutable backup body\n"
    digest = hashlib.sha256(content).hexdigest()
    relative = f"{digest[:2]}/{digest[2:4]}/{digest}"
    target = value.blobs_dir / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO app_user(
                     id,display_name,password_hash,system_role,account_status,
                     current_part_id,scope_epoch,must_change_password,
                     password_changed_at,created_at,updated_at,disabled_at,row_version
                   ) VALUES ('backup-user','Backup User','hash','admin','active',
                     NULL,1,0,1,1,1,NULL,1)"""
            )
            connection.execute(
                """INSERT INTO blob_object(
                     sha256,size_bytes,media_type,relative_path,verification_status,
                     created_at,verified_at
                   ) VALUES (?,?,'text/markdown',?,'verified',1,1)""",
                (digest, len(content), relative),
            )
            connection.execute(
                """INSERT INTO source_artifact(
                     id,origin_type,origin_key,origin_revision_key,raw_sha256,
                     raw_size_bytes,media_type,original_filename,
                     body_retention_status,body_retention_reason,body_purged_at,
                     orphan_cleanup_eligible_at,publisher_user_id,
                     source_owner_snapshot_json,metadata_json,
                     metadata_schema_version,created_at
                   ) VALUES (
                     '00000000-0000-4000-8000-000000007410','legacy','backup',
                     'v1',?,?,'text/markdown','backup.md','present',NULL,NULL,
                     2592000001,'backup-user','{}','{}',1,1
                   )""",
                (digest, len(content)),
            )
            connection.execute(
                """INSERT INTO artifact_blob(
                     id,artifact_id,content_sha256,size_bytes,role,logical_name,
                     media_type,ordinal,body_retention_status,created_at
                   ) VALUES (
                     '00000000-0000-4000-8000-000000007411',
                     '00000000-0000-4000-8000-000000007410',?,?,
                     'raw','backup.md','text/markdown',0,'present',1
                   )""",
                (digest, len(content)),
            )
            connection.execute(
                """INSERT INTO artifact_blob_body(
                     artifact_blob_id,content_sha256,created_at
                   ) VALUES ('00000000-0000-4000-8000-000000007411',?,1)""",
                (digest,),
            )
    finally:
        connection.close()
    return digest, content


def test_online_backup_copies_referenced_blob_verifies_manifest_and_replays(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    digest, content = _seed_referenced_blob(factory, isolated_knowledge_layout)
    root = _backup_root(isolated_knowledge_layout)
    run_id = "00000000-0000-4000-8000-000000007420"
    service = _service(isolated_knowledge_layout, factory, root)

    result = service.create(backup_run_id=run_id)
    assert result.state == "verified"
    assert result.replayed is False
    assert result.blob_count == 1
    assert result.blob_bytes == len(content)

    final = root / f"backup-{run_id}"
    assert final.is_dir()
    assert not (root / f".backup-{run_id}.partial").exists()
    assert (final / "database" / "knowledge-core.sqlite").is_file()
    assert (final / "blobs" / digest[:2] / digest[2:4] / digest).read_bytes() == content
    manifest = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
    assert backup_manifest_errors(manifest) == ()
    assert manifest["manifest_sha256"] == result.manifest_sha256
    assert manifest["snapshot_db_sha256"] == result.snapshot_db_sha256
    assert manifest["blob_count"] == 1

    replay = service.create(backup_run_id=run_id)
    assert replay == type(replay)(
        backup_run_id=result.backup_run_id,
        state=result.state,
        completed_directory=result.completed_directory,
        snapshot_db_sha256=result.snapshot_db_sha256,
        manifest_sha256=result.manifest_sha256,
        snapshot_audit_high_water=result.snapshot_audit_high_water,
        blob_count=result.blob_count,
        blob_bytes=result.blob_bytes,
        replayed=True,
    )

    connection = factory.open(read_only=True)
    try:
        run = connection.execute(
            """SELECT state,snapshot_db_sha256,manifest_sha256,row_version
                 FROM backup_run WHERE id=?""",
            (run_id,),
        ).fetchone()
        assert tuple(run) == (
            "verified",
            result.snapshot_db_sha256,
            result.manifest_sha256,
            3,
        )
        assert connection.execute(
            "SELECT status FROM backup_blob WHERE backup_run_id=? AND blob_sha256=?",
            (run_id, digest),
        ).fetchone()[0] == "verified"
    finally:
        connection.close()


def test_empty_online_backup_is_valid_and_has_no_blob_directories_beyond_root(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    root = _backup_root(isolated_knowledge_layout)
    run_id = "00000000-0000-4000-8000-000000007421"
    result = _service(isolated_knowledge_layout, factory, root).create(
        backup_run_id=run_id
    )
    assert result.blob_count == 0
    assert result.blob_bytes == 0
    assert list((root / f"backup-{run_id}" / "blobs").iterdir()) == []


@pytest.mark.parametrize(
    "boundary",
    (
        "backup.after_hold_acquire",
        "backup.after_database_snapshot",
        "backup.after_blob_manifest",
        "backup.before_finalize_rename",
    ),
)
def test_backup_failpoints_never_record_or_publish_false_success(
    isolated_knowledge_layout,
    boundary: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _seed_referenced_blob(factory, isolated_knowledge_layout)
    root = _backup_root(isolated_knowledge_layout)
    run_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "p7.4:" + boundary))

    def stop(name: str) -> None:
        if name == boundary:
            raise RuntimeError("synthetic backup failpoint secret")

    with pytest.raises(BackupServiceError, match="did not complete") as caught:
        _service(
            isolated_knowledge_layout, factory, root, failpoint=stop
        ).create(backup_run_id=run_id)
    assert "synthetic backup failpoint secret" not in str(caught.value)
    assert not (root / f"backup-{run_id}").exists()

    connection = factory.open(read_only=True)
    try:
        row = connection.execute(
            "SELECT state,manifest_sha256,error_json FROM backup_run WHERE id=?",
            (run_id,),
        ).fetchone()
        assert row[0] == "failed"
        assert row[1] is None
        assert json.loads(row[2]) == {
            "code": (
                "BACKUP_SNAPSHOT_FAILED"
                if boundary
                in {"backup.after_hold_acquire", "backup.after_database_snapshot"}
                else "BACKUP_FINALIZATION_FAILED"
            ),
            "schema_version": 1,
        }
        assert connection.execute(
            "SELECT count(*) FROM backup_run WHERE state IN ('preparing','copying')"
        ).fetchone()[0] == 0
    finally:
        connection.close()
