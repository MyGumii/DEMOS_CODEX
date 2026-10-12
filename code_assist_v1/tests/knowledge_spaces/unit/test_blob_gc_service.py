from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Literal

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.services.blob_gc import (
    BlobGcFilesystemError,
    BlobGcService,
    BlobGcStorageError,
    FilesystemBlobGcStorage,
)


pytestmark = [pytest.mark.unit, pytest.mark.scenario("LIFE-009")]


def _factory(value) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        value.core_db_path, CANONICAL_MIGRATION_CATALOG
    )
    SingleMigrator(
        factory,
        app_version="p5.6-blob-gc-service-test",
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


def _seed_candidate(factory: ConnectionFactory, label: str) -> tuple[str, bytes, str]:
    content = ("physical:" + label).encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    relative_path = f"{digest[:2]}/{digest[2:4]}/{digest}"
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO blob_object(
                     sha256,size_bytes,media_type,relative_path,verification_status,
                     created_at,verified_at
                   ) VALUES (?,?,'application/octet-stream',?,'verified',10,11)""",
                (digest, len(content), relative_path),
            )
            connection.execute(
                """INSERT INTO blob_gc_candidate(
                     blob_sha256,unreferenced_since,delete_after,state,claim_owner,
                     claim_expires_at,attempt_count,last_error_json,updated_at
                   ) VALUES (?,0,86400000,'quarantined',NULL,NULL,0,NULL,86400000)""",
                (digest,),
            )
    finally:
        connection.close()
    return digest, content, relative_path


@dataclass
class _Storage:
    factory: ConnectionFactory
    expected_content: bytes
    outcome: Literal["deleted", "missing_verified"] = "deleted"
    fail: bool = False
    calls: list[tuple[str, int, str]] = field(default_factory=list)

    def delete_verified(
        self,
        *,
        sha256: str,
        size_bytes: int,
        relative_path: str,
    ) -> Literal["deleted", "missing_verified"]:
        # The claim transaction must already be committed and closed before
        # filesystem I/O is delegated to this provider.
        connection = self.factory.open()
        try:
            row = connection.execute(
                "SELECT state,claim_owner FROM blob_gc_candidate WHERE blob_sha256=?",
                (sha256,),
            ).fetchone()
            assert row is not None and row[0] == "claimed" and row[1] is not None
        finally:
            connection.close()
        assert hashlib.sha256(self.expected_content).hexdigest() == sha256
        assert len(self.expected_content) == size_bytes
        assert relative_path == f"{sha256[:2]}/{sha256[2:4]}/{sha256}"
        self.calls.append((sha256, size_bytes, relative_path))
        if self.fail:
            raise OSError("synthetic-delete-failure")
        return self.outcome


@pytest.mark.parametrize("outcome", ["deleted", "missing_verified"])
def test_gc_claims_outside_io_and_finalizes_exact_verified_blob(
    isolated_knowledge_layout,
    outcome: Literal["deleted", "missing_verified"],
) -> None:
    factory = _factory(isolated_knowledge_layout)
    digest, content, _relative_path = _seed_candidate(factory, outcome)
    storage = _Storage(factory, content, outcome=outcome)

    result = BlobGcService(factory, storage).run_once(claim_owner="gc-worker")
    assert result.status == outcome
    assert result.blob_sha256 == digest
    assert result.attempt_count == 1
    assert len(storage.calls) == 1

    connection = factory.open()
    try:
        assert connection.execute(
            "SELECT count(*) FROM blob_gc_candidate WHERE blob_sha256=?", (digest,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM blob_object WHERE sha256=?", (digest,)
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_storage_failure_is_sanitized_and_retry_state_is_durable(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    digest, content, _relative_path = _seed_candidate(factory, "failure")
    storage = _Storage(factory, content, fail=True)

    with pytest.raises(BlobGcStorageError, match="Verified blob deletion failed") as caught:
        BlobGcService(factory, storage).run_once(claim_owner="gc-worker")
    assert "synthetic-delete-failure" not in str(caught.value)

    connection = factory.open()
    try:
        row = connection.execute(
            """SELECT state,claim_owner,claim_expires_at,attempt_count,last_error_json
                 FROM blob_gc_candidate WHERE blob_sha256=?""",
            (digest,),
        ).fetchone()
        assert tuple(row) == (
            "retry",
            None,
            None,
            1,
            '{"code":"BLOB_DELETE_FAILED","schema_version":1}',
        )
        assert connection.execute(
            "SELECT count(*) FROM blob_object WHERE sha256=?", (digest,)
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_active_backup_is_an_idle_hold_and_construction_never_schedules(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    digest, content, _relative_path = _seed_candidate(factory, "backup")
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO backup_run(
                     id,state,snapshot_audit_high_water,schema_version,
                     sqlite_source_id,created_at,row_version
                   ) VALUES ('00000000-0000-4000-8000-000000000777','preparing',0,1,
                     '2026-07-24 19:02:57 bf7c7f30031888f4e796e429ab3978879485813aaca6f641c7b33e4e09459bcc',
                     100,1)"""
            )
    finally:
        connection.close()

    storage = _Storage(factory, content)
    service = BlobGcService(factory, storage)
    assert storage.calls == []
    result = service.run_once(claim_owner="gc-worker")
    assert result.status == "idle"
    assert result.blob_sha256 is None
    assert storage.calls == []

    connection = factory.open()
    try:
        assert connection.execute(
            "SELECT state FROM blob_gc_candidate WHERE blob_sha256=?", (digest,)
        ).fetchone()[0] == "quarantined"
        assert connection.execute("SELECT count(*) FROM durable_job").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.skipif(os.name != "nt", reason="production blob GC is Windows-only")
def test_filesystem_provider_stream_verifies_and_deletes_exact_cas_object(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    digest, content, relative_path = _seed_candidate(factory, "filesystem")
    target = isolated_knowledge_layout.blobs_dir / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)

    result = BlobGcService(
        factory, FilesystemBlobGcStorage(_layout(isolated_knowledge_layout))
    ).run_once(claim_owner="gc-filesystem")
    assert result.status == "deleted"
    assert result.blob_sha256 == digest
    assert not target.exists()

    connection = factory.open()
    try:
        assert connection.execute(
            "SELECT count(*) FROM blob_object WHERE sha256=?", (digest,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM blob_gc_candidate WHERE blob_sha256=?", (digest,)
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.skipif(os.name != "nt", reason="production blob GC is Windows-only")
def test_filesystem_provider_rejects_wrong_bytes_and_multiple_links_without_delete(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    digest, content, relative_path = _seed_candidate(factory, "filesystem-invalid")
    target = isolated_knowledge_layout.blobs_dir / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"wrong")
    provider = FilesystemBlobGcStorage(_layout(isolated_knowledge_layout))
    with pytest.raises(BlobGcFilesystemError):
        provider.delete_verified(
            sha256=digest,
            size_bytes=len(content),
            relative_path=relative_path,
        )
    assert target.read_bytes() == b"wrong"

    target.write_bytes(content)
    sibling = target.parent / (digest + ".hardlink")
    os.link(target, sibling)
    try:
        with pytest.raises(BlobGcFilesystemError, match="multiply linked"):
            provider.delete_verified(
                sha256=digest,
                size_bytes=len(content),
                relative_path=relative_path,
            )
        assert target.read_bytes() == content
        assert sibling.read_bytes() == content
    finally:
        sibling.unlink()
