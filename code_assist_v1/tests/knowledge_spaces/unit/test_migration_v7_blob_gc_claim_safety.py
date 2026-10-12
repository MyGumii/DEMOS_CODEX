from __future__ import annotations

import hashlib
import sqlite3
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.migrations import (
    MigrationCatalog,
    MigrationStorageLayout,
    SCHEMA_V1_MIGRATION,
    SCHEMA_V2_MIGRATION,
    SCHEMA_V3_MIGRATION,
    SCHEMA_V4_MIGRATION,
    SCHEMA_V5_MIGRATION,
    SCHEMA_V6_MIGRATION,
    SCHEMA_V7_MIGRATION,
    SingleMigrator,
    V007_EXPECTED_SCHEMA_SHA256,
    schema_sha256,
)
from code_assist_v1.knowledge_core.repositories.blob_gc import BlobGcRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)


pytestmark = [pytest.mark.unit, pytest.mark.scenario("LIFE-009")]

_NAMESPACE = UUID("5a04af4f-d54b-4cbb-b1a7-44983ef42fb4")
_V6_CATALOG = MigrationCatalog(
    (
        SCHEMA_V1_MIGRATION,
        SCHEMA_V2_MIGRATION,
        SCHEMA_V3_MIGRATION,
        SCHEMA_V4_MIGRATION,
        SCHEMA_V5_MIGRATION,
        SCHEMA_V6_MIGRATION,
    )
)
_V7_CATALOG = MigrationCatalog((*_V6_CATALOG.migrations, SCHEMA_V7_MIGRATION))


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _layout(value) -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=value.root,
        database=value.core_db_path,
        blob_root=value.blobs_dir,
        projection_root=value.projections_dir,
    )


def _factory(value, catalog=_V7_CATALOG) -> ConnectionFactory:
    return ConnectionFactory._for_isolated_tests(value.core_db_path, catalog)


def _migrate(value, catalog=_V7_CATALOG):
    factory = _factory(value, catalog)
    result = SingleMigrator(
        factory,
        app_version="p5.6-blob-gc-v7-test",
        storage_layout=_layout(value),
    ).run()
    return factory, result


def _seed_blob(connection, label: str) -> tuple[str, bytes]:
    content = ("blob-gc:" + label).encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    connection.execute(
        """INSERT INTO blob_object(
             sha256,size_bytes,media_type,relative_path,verification_status,
             created_at,verified_at
           ) VALUES (?,?,?,?,'verified',10,11)""",
        (digest, len(content), "application/octet-stream", f"{digest[:2]}/{digest[2:4]}/{digest}"),
    )
    return digest, content


def _seed_artifact_without_body(connection, digest: str, size: int, label: str) -> str:
    UserRepository().import_legacy(
        connection,
        LegacyUserRecord(
            id="owner",
            display_name="Owner",
            password_hash="p5.6-owner-hash",
            system_role="admin",
            account_status="active",
            created_at_ms=10,
            updated_at_ms=10,
        ),
    )
    artifact_id = _uuid("artifact:" + label)
    manifest_id = _uuid("manifest:" + label)
    connection.execute(
        """INSERT INTO source_artifact(
             id,origin_type,origin_key,origin_revision_key,raw_sha256,
             raw_size_bytes,media_type,original_filename,body_retention_status,
             body_retention_reason,body_purged_at,orphan_cleanup_eligible_at,
             publisher_user_id,source_owner_snapshot_json,metadata_json,
             metadata_schema_version,created_at
           ) VALUES (?,'legacy',?,?,?,?,'application/octet-stream',?,'present',
                     NULL,NULL,2592000100,'owner','{}','{}',1,100)""",
        (artifact_id, "legacy:" + label, "revision:" + label, digest, size, label),
    )
    connection.execute(
        """INSERT INTO artifact_blob(
             id,artifact_id,content_sha256,size_bytes,role,logical_name,
             media_type,ordinal,body_retention_status,created_at
           ) VALUES (?,?,?,?,'raw',?,'application/octet-stream',0,'present',100)""",
        (manifest_id, artifact_id, digest, size, label),
    )
    return manifest_id


def _seed_candidate(connection, digest: str) -> None:
    connection.execute(
        """INSERT INTO blob_gc_candidate(
             blob_sha256,unreferenced_since,delete_after,state,claim_owner,
             claim_expires_at,attempt_count,last_error_json,updated_at
           ) VALUES (?,0,86400000,'quarantined',NULL,NULL,0,NULL,86400000)""",
        (digest,),
    )


def test_v6_upgrades_to_v7_and_fresh_catalog_has_exact_guard_set(
    isolated_knowledge_layout,
) -> None:
    _factory_v6, first = _migrate(isolated_knowledge_layout, _V6_CATALOG)
    assert first.applied_versions == (1, 2, 3, 4, 5, 6)

    factory, second = _migrate(isolated_knowledge_layout)
    assert second.applied_versions == (7,)
    assert second.schema_version == 7
    connection = factory.open()
    try:
        assert schema_sha256(connection) == V007_EXPECTED_SCHEMA_SHA256
        rows = connection.execute(
            """SELECT name FROM sqlite_schema WHERE type='trigger' AND name IN (
                 'trg_artifact_blob_body_gc_fence_insert',
                 'trg_extraction_snapshot_body_gc_fence_insert',
                 'trg_knowledge_revision_body_gc_fence_insert',
                 'trg_change_proposal_gc_fence_insert',
                 'trg_blob_gc_candidate_reference_insert',
                 'trg_blob_gc_candidate_claim_guard',
                 'trg_blob_gc_candidate_finalize_delete',
                 'trg_backup_run_gc_claim_insert'
               ) ORDER BY name"""
        ).fetchall()
        assert len(rows) == 8
    finally:
        connection.close()


def test_claimed_candidate_fences_new_reference_and_new_backup(
    isolated_knowledge_layout,
) -> None:
    factory, _result = _migrate(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            digest, content = _seed_blob(connection, "claimed")
            manifest_id = _seed_artifact_without_body(
                connection, digest, len(content), "claimed.bin"
            )
            _seed_candidate(connection, digest)
        with transaction(connection):
            claim = BlobGcRepository().claim_next(
                connection, claim_owner="gc-worker-1", now_ms=100_000_000
            )
            assert claim is not None
        with pytest.raises(sqlite3.IntegrityError, match="BLOB_GC_REFERENCE_FENCED"):
            with transaction(connection):
                connection.execute(
                    "INSERT INTO artifact_blob_body(artifact_blob_id,content_sha256,created_at) VALUES (?,?,?)",
                    (manifest_id, digest, 101),
                )
        with pytest.raises(sqlite3.IntegrityError, match="BLOB_GC_BACKUP_RACE"):
            with transaction(connection):
                connection.execute(
                    """INSERT INTO backup_run(
                         id,state,snapshot_audit_high_water,schema_version,
                         sqlite_source_id,created_at,row_version
                       ) VALUES (?,'preparing',0,1,
                         '2026-07-24 19:02:57 bf7c7f30031888f4e796e429ab3978879485813aaca6f641c7b33e4e09459bcc',
                         100,1)""",
                    (_uuid("backup:claimed"),),
                )
    finally:
        connection.close()


def test_candidate_insert_rejects_existing_reference_and_quarantine_reference_cancels(
    isolated_knowledge_layout,
) -> None:
    factory, _result = _migrate(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            digest, content = _seed_blob(connection, "referenced")
            manifest_id = _seed_artifact_without_body(
                connection, digest, len(content), "referenced.bin"
            )
            connection.execute(
                "INSERT INTO artifact_blob_body(artifact_blob_id,content_sha256,created_at) VALUES (?,?,?)",
                (manifest_id, digest, 101),
            )
        with pytest.raises(sqlite3.IntegrityError, match="BLOB_GC_REFERENCE_PRESENT"):
            with transaction(connection):
                _seed_candidate(connection, digest)

        with transaction(connection):
            other_digest, other_content = _seed_blob(connection, "late-reference")
            other_manifest = _seed_artifact_without_body(
                connection, other_digest, len(other_content), "late-reference.bin"
            )
            _seed_candidate(connection, other_digest)
            connection.execute(
                "INSERT INTO artifact_blob_body(artifact_blob_id,content_sha256,created_at) VALUES (?,?,?)",
                (other_manifest, other_digest, 101),
            )
        with transaction(connection):
            assert (
                BlobGcRepository().claim_next(
                    connection, claim_owner="gc-worker-2", now_ms=100_000_000
                )
                is None
            )
        assert connection.execute(
            "SELECT count(*) FROM blob_gc_candidate WHERE blob_sha256=?",
            (other_digest,),
        ).fetchone()[0] == 0
    finally:
        connection.close()
