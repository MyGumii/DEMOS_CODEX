from __future__ import annotations

from dataclasses import replace
import hashlib
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.errors import KnowledgeCoreTransactionError
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.artifacts import (
    ArtifactBundleConflictError,
    ArtifactRepository,
    LegacyArtifactBundle,
    LegacyEmbeddedAssetLink,
    LegacyExtractionSpec,
    PreparedMarkdownBody,
    StagedVerifiedBlob,
    legacy_artifact_ids_for_record,
    legacy_embedded_manifest_id_for_relationship,
)
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("c3b738ed-85e6-4d98-b968-22702f9b0c44")
_PLAN = "a" * 64
_OWNER = "owner"


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _record(number: int) -> str:
    return f"rec_{number:024x}"


def _relationship(number: int) -> str:
    return f"rel_{number:024x}"


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path,
        CANONICAL_MIGRATION_CATALOG,
    )
    SingleMigrator(
        factory,
        app_version="p2.3-artifact-repository-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _seed_owner(connection, owner: str = _OWNER) -> None:
    UserRepository().import_legacy(
        connection,
        LegacyUserRecord(
            id=owner,
            display_name=owner.title(),
            password_hash=f"scrypt:p2.3:{owner}",
            system_role="user",
            account_status="active",
            created_at_ms=100,
            updated_at_ms=100,
        ),
    )


def _blob(content: bytes, media_type: str, *, clock: int = 200) -> StagedVerifiedBlob:
    sha256 = hashlib.sha256(content).hexdigest()
    return StagedVerifiedBlob(
        sha256=sha256,
        size_bytes=len(content),
        media_type=media_type,
        relative_path=f"{sha256[:2]}/{sha256[2:4]}/{sha256}",
        created_at_ms=clock,
        verified_at_ms=clock + 1,
    )


def _bundle(
    number: int,
    *,
    owner: str = _OWNER,
    content: bytes = b"# legacy\n",
    markdown: bool = True,
    blob_body: bool = False,
) -> LegacyArtifactBundle:
    record_id = _record(number)
    raw = _blob(content, "text/markdown" if markdown else "image/png")
    ids = legacy_artifact_ids_for_record(_PLAN, record_id)
    extraction = None
    if markdown:
        body = PreparedMarkdownBody(
            content_md=None if blob_body else content.decode("utf-8"),
            content_blob_hash=raw.sha256 if blob_body else None,
            content_size_bytes=raw.size_bytes,
            content_sha256=raw.sha256,
        )
        extraction = LegacyExtractionSpec(
            normalized_content_sha256=hashlib.sha256(content).hexdigest(),
            normalizer_version="markdown_lf_v1",
            extractor_name="legacy-import",
            extractor_version="v1",
            warnings_json="[]",
            body=body,
            created_at_ms=301 + number,
        )
    return LegacyArtifactBundle(
        plan_sha256=_PLAN,
        inventory_record_id=record_id,
        ids=ids,
        owner_user_id=owner,
        origin_key=f"p2v1/legacy-root/{number:064x}/physical_file/0",
        raw_blob=raw,
        logical_name=f"source-{number}.md" if markdown else f"asset-{number}.png",
        original_filename=f"source-{number}.md" if markdown else f"asset-{number}.png",
        source_owner_snapshot_json='{"schema_version":1}',
        metadata_json='{"schema_version":1}',
        created_at_ms=300 + number,
        extraction=extraction,
    )


def _count(connection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def test_deterministic_artifact_and_relationship_ids_are_plan_bound() -> None:
    first = legacy_artifact_ids_for_record(_PLAN, _record(1))
    assert first == legacy_artifact_ids_for_record(_PLAN, _record(1))
    assert first != legacy_artifact_ids_for_record(_PLAN, _record(2))
    assert first != legacy_artifact_ids_for_record("b" * 64, _record(1))
    relationship_id = _relationship(1)
    assert legacy_embedded_manifest_id_for_relationship(
        _PLAN, relationship_id
    ) == legacy_embedded_manifest_id_for_relationship(_PLAN, relationship_id)
    assert legacy_embedded_manifest_id_for_relationship(
        _PLAN, relationship_id
    ) != legacy_embedded_manifest_id_for_relationship("b" * 64, relationship_id)


def test_artifact_bundle_requires_caller_owned_transaction(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
        with pytest.raises(KnowledgeCoreTransactionError):
            ArtifactRepository().put_legacy_bundle(connection, _bundle(1))
    finally:
        connection.close()


@pytest.mark.parametrize("blob_body", [False, True])
def test_markdown_artifact_bundle_inserts_and_exactly_replays(
    isolated_knowledge_layout,
    blob_body: bool,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = ArtifactRepository()
    bundle = _bundle(10 + int(blob_body), blob_body=blob_body)
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
            result = repository.put_legacy_bundle(connection, bundle)
            assert result.inserted
        with transaction(connection):
            replay = repository.put_legacy_bundle(connection, bundle)
            assert not replay.inserted
            assert _count(connection, "blob_object") == 1
            assert _count(connection, "source_artifact") == 1
            assert _count(connection, "artifact_blob") == 1
            assert _count(connection, "artifact_blob_body") == 1
            assert _count(connection, "extraction_snapshot") == 1
            assert _count(connection, "extraction_snapshot_body") == 1
    finally:
        connection.close()


def test_two_artifacts_share_one_exact_content_addressed_blob(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = ArtifactRepository()
    first = _bundle(20)
    second = _bundle(21)
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
            repository.put_legacy_bundle(connection, first)
            repository.put_legacy_bundle(connection, second)
            assert _count(connection, "blob_object") == 1
            assert _count(connection, "source_artifact") == 2
            assert _count(connection, "artifact_blob_body") == 2
    finally:
        connection.close()


def test_known_conflict_is_preflighted_before_writes_even_if_caught_and_committed(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = ArtifactRepository()
    bundle = _bundle(30)
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
            repository.put_legacy_bundle(connection, bundle)
        before_changes = connection.total_changes
        with transaction(connection):
            with pytest.raises(ArtifactBundleConflictError):
                repository.put_legacy_bundle(
                    connection,
                    replace(bundle, metadata_json='{"different":true}'),
                )
            unrelated = _blob(b"unrelated", "application/octet-stream", clock=900)
            connection.execute(
                """INSERT INTO blob_object(
                     sha256,size_bytes,media_type,relative_path,
                     verification_status,created_at,verified_at
                   ) VALUES (?,?,?,?,'verified',?,?)""",
                (
                    unrelated.sha256,
                    unrelated.size_bytes,
                    unrelated.media_type,
                    unrelated.relative_path,
                    unrelated.created_at_ms,
                    unrelated.verified_at_ms,
                ),
            )
        assert connection.total_changes == before_changes + 1
        with transaction(connection):
            assert _count(connection, "source_artifact") == 1
            assert _count(connection, "artifact_blob") == 1
            assert _count(connection, "extraction_snapshot") == 1
    finally:
        connection.close()


def test_partial_existing_artifact_fails_before_adding_more_bundle_rows(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = ArtifactRepository()
    bundle = _bundle(40)
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
            blob = bundle.raw_blob
            connection.execute(
                """INSERT INTO blob_object(
                     sha256,size_bytes,media_type,relative_path,
                     verification_status,created_at,verified_at
                   ) VALUES (?,?,?,?,'verified',?,?)""",
                (
                    blob.sha256,
                    blob.size_bytes,
                    blob.media_type,
                    blob.relative_path,
                    blob.created_at_ms,
                    blob.verified_at_ms,
                ),
            )
            connection.execute(
                """INSERT INTO source_artifact(
                     id,origin_type,origin_key,origin_revision_key,raw_sha256,
                     raw_size_bytes,media_type,original_filename,
                     body_retention_status,body_retention_reason,body_purged_at,
                     orphan_cleanup_eligible_at,publisher_user_id,
                     source_owner_snapshot_json,metadata_json,
                     metadata_schema_version,created_at
                   ) VALUES (?,'legacy',?,?,?,?,?,?,'present',NULL,NULL,?,?,?, ?,1,?)""",
                (
                    bundle.ids.artifact_id,
                    bundle.origin_key,
                    bundle.inventory_record_id,
                    blob.sha256,
                    blob.size_bytes,
                    blob.media_type,
                    bundle.original_filename,
                    bundle.created_at_ms + 2_592_000_000,
                    bundle.owner_user_id,
                    bundle.source_owner_snapshot_json,
                    bundle.metadata_json,
                    bundle.created_at_ms,
                ),
            )
        with transaction(connection):
            with pytest.raises(ArtifactBundleConflictError):
                repository.put_legacy_bundle(connection, bundle)
            connection.execute(
                "UPDATE app_user SET display_name='Owner 2',updated_at=500,row_version=2 WHERE id=?",
                (_OWNER,),
            )
        with transaction(connection):
            assert _count(connection, "artifact_blob") == 0
            assert _count(connection, "artifact_blob_body") == 0
            assert _count(connection, "extraction_snapshot") == 0
    finally:
        connection.close()


def test_embedded_asset_link_is_deterministic_exact_and_replayable(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = ArtifactRepository()
    source = _bundle(50)
    asset = _bundle(51, content=b"png-bytes", markdown=False)
    relationship_id = _relationship(50)
    name = "images/asset.png"
    link = LegacyEmbeddedAssetLink(
        plan_sha256=_PLAN,
        relationship_id=relationship_id,
        manifest_id=legacy_embedded_manifest_id_for_relationship(
            _PLAN, relationship_id
        ),
        source_artifact_id=source.ids.artifact_id,
        target_asset_artifact_id=asset.ids.artifact_id,
        target_asset_raw_manifest_id=asset.ids.raw_manifest_id,
        target_blob=asset.raw_blob,
        logical_name=name,
        logical_name_sha256=hashlib.sha256(name.encode("utf-8")).hexdigest(),
        ordinal=3,
        created_at_ms=800,
    )
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
            repository.put_legacy_bundle(connection, source)
            repository.put_legacy_bundle(connection, asset)
            inserted = repository.put_embedded_asset_link(connection, link)
            assert inserted.inserted
        with transaction(connection):
            replay = repository.put_embedded_asset_link(connection, link)
            assert not replay.inserted
            row = connection.execute(
                """SELECT role,ordinal,logical_name,content_sha256
                   FROM artifact_blob WHERE id=?""",
                (link.manifest_id,),
            ).fetchone()
            assert row is not None
            assert tuple(row) == ("embedded_asset", 3, name, asset.raw_blob.sha256)
    finally:
        connection.close()


def test_embedded_ordinal_conflict_is_preflighted_without_partial_link(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = ArtifactRepository()
    source = _bundle(60)
    first_asset = _bundle(61, content=b"asset-one", markdown=False)
    second_asset = _bundle(62, content=b"asset-two", markdown=False)

    def link(number: int, asset_bundle: LegacyArtifactBundle) -> LegacyEmbeddedAssetLink:
        relationship_id = _relationship(number)
        name = f"asset-{number}.png"
        return LegacyEmbeddedAssetLink(
            plan_sha256=_PLAN,
            relationship_id=relationship_id,
            manifest_id=legacy_embedded_manifest_id_for_relationship(
                _PLAN, relationship_id
            ),
            source_artifact_id=source.ids.artifact_id,
            target_asset_artifact_id=asset_bundle.ids.artifact_id,
            target_asset_raw_manifest_id=asset_bundle.ids.raw_manifest_id,
            target_blob=asset_bundle.raw_blob,
            logical_name=name,
            logical_name_sha256=hashlib.sha256(name.encode()).hexdigest(),
            ordinal=0,
            created_at_ms=900 + number,
        )

    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
            repository.put_legacy_bundle(connection, source)
            repository.put_legacy_bundle(connection, first_asset)
            repository.put_legacy_bundle(connection, second_asset)
            repository.put_embedded_asset_link(connection, link(1, first_asset))
        before = connection.total_changes
        with transaction(connection):
            with pytest.raises(ArtifactBundleConflictError):
                repository.put_embedded_asset_link(connection, link(2, second_asset))
            connection.execute(
                "UPDATE app_user SET display_name='Owner 2',updated_at=999,row_version=2 WHERE id=?",
                (_OWNER,),
            )
        assert connection.total_changes == before + 1
        with transaction(connection):
            row = connection.execute(
                """SELECT count(*) FROM artifact_blob
                   WHERE artifact_id=? AND role='embedded_asset'""",
                (source.ids.artifact_id,),
            ).fetchone()
            assert row is not None and row[0] == 1
    finally:
        connection.close()


def test_embedded_link_requires_existing_verified_target_blob_before_writes(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = ArtifactRepository()
    source = _bundle(70)
    missing_blob = _blob(b"not-in-database", "image/png", clock=1000)
    relationship_id = _relationship(70)
    name = "missing.png"
    link = LegacyEmbeddedAssetLink(
        plan_sha256=_PLAN,
        relationship_id=relationship_id,
        manifest_id=legacy_embedded_manifest_id_for_relationship(
            _PLAN, relationship_id
        ),
        source_artifact_id=source.ids.artifact_id,
        target_asset_artifact_id=legacy_artifact_ids_for_record(
            _PLAN, _record(71)
        ).artifact_id,
        target_asset_raw_manifest_id=legacy_artifact_ids_for_record(
            _PLAN, _record(71)
        ).raw_manifest_id,
        target_blob=missing_blob,
        logical_name=name,
        logical_name_sha256=hashlib.sha256(name.encode()).hexdigest(),
        ordinal=9,
        created_at_ms=1002,
    )
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner(connection)
            repository.put_legacy_bundle(connection, source)
        before = connection.total_changes
        with transaction(connection):
            with pytest.raises(ArtifactBundleConflictError):
                repository.put_embedded_asset_link(connection, link)
            connection.execute(
                "UPDATE app_user SET display_name='Owner 2',updated_at=1003,row_version=2 WHERE id=?",
                (_OWNER,),
            )
        assert connection.total_changes == before + 1
        with transaction(connection):
            assert connection.execute(
                "SELECT 1 FROM artifact_blob WHERE id=?", (link.manifest_id,)
            ).fetchone() is None
            assert connection.execute(
                "SELECT 1 FROM artifact_blob_body WHERE artifact_blob_id=?",
                (link.manifest_id,),
            ).fetchone() is None
    finally:
        connection.close()
