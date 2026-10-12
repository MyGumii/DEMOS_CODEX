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
    ArtifactRepository,
    LegacyArtifactBundle,
    LegacyEmbeddedAssetLink,
    LegacyExtractionSpec,
    PreparedMarkdownBody,
    StagedVerifiedBlob,
    legacy_artifact_ids_for_record,
    legacy_embedded_manifest_id_for_relationship,
)
from code_assist_v1.knowledge_core.repositories.items import (
    ItemBundleConflictError,
    ItemRepository,
    LegacyMarkdownImportBundle,
    legacy_item_ids_for_record,
)
from code_assist_v1.knowledge_core.repositories.migration import (
    LegacyMigrationDetails,
    LegacyMigrationKey,
    LegacyMigrationRelationshipDetails,
    LegacyMigrationRelationshipRepository,
    LegacyMigrationRelationshipTarget,
    LegacyMigrationRepository,
    LegacyMigrationTarget,
    VerifiedBlobEvidence,
    migration_mapping_id_for_record,
    migration_relationship_mapping_id_for_plan,
    migration_run_id_for_plan,
    migration_source_locator,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRepository,
)


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("9bfa99e2-e389-4739-bdd1-41b641b4b05e")
_PLAN = "a" * 64


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _record(number: int) -> str:
    return f"rec_{number:024x}"


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path,
        CANONICAL_MIGRATION_CATALOG,
    )
    SingleMigrator(
        factory,
        app_version="p2.3-item-repository-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _seed_owner(connection, owner: str) -> str:
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
    space_id = _uuid(f"space:{owner}")
    SpaceRepository().ensure_personal(
        connection,
        space_id=space_id,
        user_id=owner,
        created_at_ms=150,
    )
    return space_id


def _normalize_markdown(content: bytes) -> bytes:
    return content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _artifact_bundle(
    number: int,
    *,
    owner: str,
    content: bytes = b"# exact legacy body\n",
    blob_body: bool = False,
) -> LegacyArtifactBundle:
    record_id = _record(number)
    raw_sha = hashlib.sha256(content).hexdigest()
    ids = legacy_artifact_ids_for_record(_PLAN, record_id)
    blob = StagedVerifiedBlob(
        sha256=raw_sha,
        size_bytes=len(content),
        media_type="text/markdown",
        relative_path=f"{raw_sha[:2]}/{raw_sha[2:4]}/{raw_sha}",
        created_at_ms=200,
        verified_at_ms=201,
    )
    body = PreparedMarkdownBody(
        content_md=None if blob_body else content.decode("utf-8"),
        content_blob_hash=raw_sha if blob_body else None,
        content_size_bytes=len(content),
        content_sha256=raw_sha,
    )
    return LegacyArtifactBundle(
        plan_sha256=_PLAN,
        inventory_record_id=record_id,
        ids=ids,
        owner_user_id=owner,
        origin_key=f"p2v1/legacy-root/{number:064x}/physical_file/0",
        raw_blob=blob,
        logical_name=f"source-{number}.md",
        original_filename=f"source-{number}.md",
        source_owner_snapshot_json='{"schema_version":1}',
        metadata_json='{"schema_version":1}',
        created_at_ms=300 + number,
        extraction=LegacyExtractionSpec(
            normalized_content_sha256=hashlib.sha256(
                _normalize_markdown(content)
            ).hexdigest(),
            normalizer_version="markdown_lf_v1",
            extractor_name="legacy-import",
            extractor_version="v1",
            warnings_json="[]",
            body=body,
            created_at_ms=400 + number,
        ),
    )


def _asset_bundle(number: int, *, owner: str) -> LegacyArtifactBundle:
    record_id = _record(number)
    content = f"asset-{number}".encode("ascii")
    raw_sha = hashlib.sha256(content).hexdigest()
    ids = legacy_artifact_ids_for_record(_PLAN, record_id)
    return LegacyArtifactBundle(
        plan_sha256=_PLAN,
        inventory_record_id=record_id,
        ids=ids,
        owner_user_id=owner,
        origin_key=f"p2v1/legacy-root/{number:064x}/physical_file/0",
        raw_blob=StagedVerifiedBlob(
            sha256=raw_sha,
            size_bytes=len(content),
            media_type="image/png",
            relative_path=f"{raw_sha[:2]}/{raw_sha[2:4]}/{raw_sha}",
            created_at_ms=200 + number,
            verified_at_ms=201 + number,
        ),
        logical_name=f"asset-{number}.png",
        original_filename=f"asset-{number}.png",
        source_owner_snapshot_json='{"schema_version":1}',
        metadata_json='{"schema_version":1}',
        created_at_ms=300 + number,
        extraction=None,
    )


def _item_bundle(
    source: LegacyArtifactBundle,
    *,
    owner: str,
    space_id: str,
    target_item_id: str | None = None,
    target_revision_id: str | None = None,
) -> LegacyMarkdownImportBundle:
    assert source.extraction is not None
    return LegacyMarkdownImportBundle(
        plan_sha256=source.plan_sha256,
        inventory_record_id=source.inventory_record_id,
        ids=legacy_item_ids_for_record(
            source.plan_sha256, source.inventory_record_id
        ),
        owner_user_id=owner,
        personal_space_id=space_id,
        artifact_id=source.ids.artifact_id,
        extraction_snapshot_id=source.ids.extraction_snapshot_id,
        title=f"Imported {source.inventory_record_id}",
        raw_content_sha256=source.raw_blob.sha256,
        normalized_content_sha256=source.extraction.normalized_content_sha256,
        content_size_bytes=source.raw_blob.size_bytes,
        normalizer_version=source.extraction.normalizer_version,
        metadata_json='{"migration":true}',
        change_reason="legacy migration",
        body=source.extraction.body,
        created_at_ms=600 + int(source.inventory_record_id[-4:], 16),
        target_item_id=target_item_id,
        target_revision_id=target_revision_id,
    )


def _count(connection, table: str) -> int:
    row = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def test_item_ids_are_deterministic_and_bound_to_plan_and_record() -> None:
    first = legacy_item_ids_for_record(_PLAN, _record(1))
    assert first == legacy_item_ids_for_record(_PLAN, _record(1))
    assert first != legacy_item_ids_for_record(_PLAN, _record(2))
    assert first != legacy_item_ids_for_record("b" * 64, _record(1))


def test_item_bundle_requires_caller_owned_transaction(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    source = _artifact_bundle(1, owner="owner")
    connection = factory.open()
    try:
        with transaction(connection):
            space_id = _seed_owner(connection, "owner")
            ArtifactRepository().put_legacy_bundle(connection, source)
        with pytest.raises(KnowledgeCoreTransactionError):
            ItemRepository().put_legacy_markdown_import(
                connection,
                _item_bundle(source, owner="owner", space_id=space_id),
            )
    finally:
        connection.close()


@pytest.mark.parametrize("blob_body", [False, True])
def test_created_markdown_item_satisfies_deferred_pointer_and_exact_replay(
    isolated_knowledge_layout,
    blob_body: bool,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    source = _artifact_bundle(10 + int(blob_body), owner="owner", blob_body=blob_body)
    artifacts = ArtifactRepository()
    items = ItemRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            space_id = _seed_owner(connection, "owner")
            artifacts.put_legacy_bundle(connection, source)
            bundle = _item_bundle(source, owner="owner", space_id=space_id)
            result = items.put_legacy_markdown_import(connection, bundle)
            assert result.disposition == "created"
            assert result.inserted_item and result.inserted_source
        # Committing the outer transaction proves the initially deferred
        # item->revision pointer was closed by the bundle.
        with transaction(connection):
            replay = items.put_legacy_markdown_import(connection, bundle)
            assert replay.disposition == "no_op"
            assert not replay.inserted_item and not replay.inserted_source
            assert _count(connection, "knowledge_item") == 1
            assert _count(connection, "knowledge_revision") == 1
            assert _count(connection, "knowledge_revision_body") == 1
            assert _count(connection, "item_source") == 1
            assert _count(connection, "item_lease") == 1
            assert _count(connection, "search_document") == 0
    finally:
        connection.close()


def test_second_distinct_exact_source_adds_provenance_then_replays_no_op(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    artifacts = ArtifactRepository()
    items = ItemRepository()
    first_source = _artifact_bundle(20, owner="owner")
    second_source = _artifact_bundle(21, owner="owner")
    connection = factory.open()
    try:
        with transaction(connection):
            space_id = _seed_owner(connection, "owner")
            artifacts.put_legacy_bundle(connection, first_source)
            artifacts.put_legacy_bundle(connection, second_source)
            first = items.put_legacy_markdown_import(
                connection,
                _item_bundle(first_source, owner="owner", space_id=space_id),
            )
            second_bundle = _item_bundle(
                second_source,
                owner="owner",
                space_id=space_id,
            )
            second = items.put_legacy_markdown_import(connection, second_bundle)
            assert second.disposition == "provenance_added"
            assert not second.inserted_item and second.inserted_source
        with transaction(connection):
            replay = items.put_legacy_markdown_import(connection, second_bundle)
            assert replay.disposition == "no_op"
            assert _count(connection, "knowledge_item") == 1
            assert _count(connection, "knowledge_revision") == 1
            assert _count(connection, "item_source") == 2
            assert _count(connection, "source_artifact") == 2
            rows = connection.execute(
                "SELECT artifact_id FROM item_source ORDER BY artifact_id"
            ).fetchall()
            assert {row[0] for row in rows} == {
                first_source.ids.artifact_id,
                second_source.ids.artifact_id,
            }
    finally:
        connection.close()


def test_same_exact_body_for_different_owners_remains_two_personal_items(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    artifacts = ArtifactRepository()
    items = ItemRepository()
    alice_source = _artifact_bundle(30, owner="alice")
    bob_source = _artifact_bundle(31, owner="bob")
    connection = factory.open()
    try:
        with transaction(connection):
            alice_space = _seed_owner(connection, "alice")
            bob_space = _seed_owner(connection, "bob")
            artifacts.put_legacy_bundle(connection, alice_source)
            artifacts.put_legacy_bundle(connection, bob_source)
            alice = items.put_legacy_markdown_import(
                connection,
                _item_bundle(alice_source, owner="alice", space_id=alice_space),
            )
            bob = items.put_legacy_markdown_import(
                connection,
                _item_bundle(bob_source, owner="bob", space_id=bob_space),
            )
            assert alice.item_id != bob.item_id
            assert alice.revision_id != bob.revision_id
            assert _count(connection, "knowledge_item") == 2
            assert _count(connection, "knowledge_revision") == 2
            assert _count(connection, "blob_object") == 1
    finally:
        connection.close()


def test_normalized_hash_alone_cannot_attach_provenance(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    artifacts = ArtifactRepository()
    items = ItemRepository()
    crlf_source = _artifact_bundle(40, owner="owner", content=b"same\r\n")
    lf_source = _artifact_bundle(41, owner="owner", content=b"same\n")
    assert crlf_source.extraction is not None and lf_source.extraction is not None
    assert (
        crlf_source.extraction.normalized_content_sha256
        == lf_source.extraction.normalized_content_sha256
    )
    assert crlf_source.raw_blob.sha256 != lf_source.raw_blob.sha256
    connection = factory.open()
    try:
        with transaction(connection):
            space_id = _seed_owner(connection, "owner")
            artifacts.put_legacy_bundle(connection, crlf_source)
            artifacts.put_legacy_bundle(connection, lf_source)
            first = items.put_legacy_markdown_import(
                connection,
                _item_bundle(crlf_source, owner="owner", space_id=space_id),
            )
            with pytest.raises(ItemBundleConflictError) as caught:
                items.put_legacy_markdown_import(
                    connection,
                    _item_bundle(
                        lf_source,
                        owner="owner",
                        space_id=space_id,
                        target_item_id=first.item_id,
                        target_revision_id=first.revision_id,
                    ),
                )
            assert "same" not in str(caught.value)
            second = items.put_legacy_markdown_import(
                connection,
                _item_bundle(lf_source, owner="owner", space_id=space_id),
            )
            assert second.disposition == "created"
            assert _count(connection, "knowledge_item") == 2
            assert _count(connection, "item_source") == 2
    finally:
        connection.close()


def test_cross_owner_target_conflict_is_preflighted_if_caught_and_committed(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    artifacts = ArtifactRepository()
    items = ItemRepository()
    alice_source = _artifact_bundle(50, owner="alice")
    bob_source = _artifact_bundle(51, owner="bob")
    connection = factory.open()
    try:
        with transaction(connection):
            alice_space = _seed_owner(connection, "alice")
            bob_space = _seed_owner(connection, "bob")
            artifacts.put_legacy_bundle(connection, alice_source)
            artifacts.put_legacy_bundle(connection, bob_source)
            alice = items.put_legacy_markdown_import(
                connection,
                _item_bundle(alice_source, owner="alice", space_id=alice_space),
            )
        before = connection.total_changes
        with transaction(connection):
            with pytest.raises(ItemBundleConflictError):
                items.put_legacy_markdown_import(
                    connection,
                    _item_bundle(
                        bob_source,
                        owner="bob",
                        space_id=bob_space,
                        target_item_id=alice.item_id,
                        target_revision_id=alice.revision_id,
                    ),
                )
            connection.execute(
                "UPDATE app_user SET display_name='Bob 2',updated_at=900,row_version=2 WHERE id='bob'"
            )
        assert connection.total_changes == before + 1
        with transaction(connection):
            assert _count(connection, "knowledge_item") == 1
            assert _count(connection, "knowledge_revision") == 1
            assert _count(connection, "item_source") == 1
            row = connection.execute(
                "SELECT count(*) FROM item_source WHERE artifact_id=?",
                (bob_source.ids.artifact_id,),
            ).fetchone()
            assert row is not None and row[0] == 0
    finally:
        connection.close()


def test_missing_selected_target_and_id_drift_fail_before_item_writes(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    source = _artifact_bundle(60, owner="owner")
    artifacts = ArtifactRepository()
    items = ItemRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            space_id = _seed_owner(connection, "owner")
            artifacts.put_legacy_bundle(connection, source)
            bundle = _item_bundle(source, owner="owner", space_id=space_id)
            with pytest.raises(ItemBundleConflictError):
                items.put_legacy_markdown_import(
                    connection,
                    replace(
                        bundle,
                        target_item_id=_uuid("missing-item"),
                        target_revision_id=_uuid("missing-revision"),
                    ),
                )
            with pytest.raises(ValueError):
                items.put_legacy_markdown_import(
                    connection,
                    replace(
                        bundle,
                        ids=replace(bundle.ids, item_source_id=_uuid("drift")),
                    ),
                )
            assert _count(connection, "knowledge_item") == 0
            assert _count(connection, "knowledge_revision") == 0
            assert _count(connection, "item_source") == 0
    finally:
        connection.close()


def test_bundles_satisfy_record_and_v4_relationship_target_verifiers(
    isolated_knowledge_layout,
) -> None:
    """Exercise the exact downstream P2.3 physical-effect contracts."""

    factory = _factory(isolated_knowledge_layout)
    artifacts = ArtifactRepository()
    items = ItemRepository()
    records = LegacyMigrationRepository()
    relationships = LegacyMigrationRelationshipRepository()
    source_path = "wiki/sources/with-asset.md"
    second_source_path = "wiki/sources/same-body.md"
    asset_path = "wiki/assets/picture.png"
    source = replace(
        _artifact_bundle(70, owner="owner"),
        origin_key=migration_source_locator(
            root_id="legacy-root",
            path_key=source_path,
            record_kind="physical_file",
            source_ordinal=0,
        ),
    )
    asset = replace(
        _asset_bundle(71, owner="owner"),
        origin_key=migration_source_locator(
            root_id="legacy-root",
            path_key=asset_path,
            record_kind="physical_file",
            source_ordinal=0,
        ),
    )
    second_source = replace(
        _artifact_bundle(72, owner="owner"),
        origin_key=migration_source_locator(
            root_id="legacy-root",
            path_key=second_source_path,
            record_kind="physical_file",
            source_ordinal=0,
        ),
    )
    relationship_id = "rel_" + "7" * 24
    relationship_sha = hashlib.sha256(b"reviewed relationship").hexdigest()
    logical_name = "images/picture.png"
    embedded_id = legacy_embedded_manifest_id_for_relationship(
        _PLAN, relationship_id
    )
    connection = factory.open()
    try:
        with transaction(connection):
            space_id = _seed_owner(connection, "owner")
            artifacts.put_legacy_bundle(connection, source)
            artifacts.put_legacy_bundle(connection, asset)
            artifacts.put_legacy_bundle(connection, second_source)
            imported = items.put_legacy_markdown_import(
                connection,
                _item_bundle(source, owner="owner", space_id=space_id),
            )
            provenance = items.put_legacy_markdown_import(
                connection,
                _item_bundle(
                    second_source,
                    owner="owner",
                    space_id=space_id,
                ),
            )
            assert provenance.disposition == "provenance_added"
            artifacts.put_embedded_asset_link(
                connection,
                LegacyEmbeddedAssetLink(
                    plan_sha256=_PLAN,
                    relationship_id=relationship_id,
                    manifest_id=embedded_id,
                    source_artifact_id=source.ids.artifact_id,
                    target_asset_artifact_id=asset.ids.artifact_id,
                    target_asset_raw_manifest_id=asset.ids.raw_manifest_id,
                    target_blob=asset.raw_blob,
                    logical_name=logical_name,
                    logical_name_sha256=hashlib.sha256(
                        logical_name.encode("utf-8")
                    ).hexdigest(),
                    ordinal=2,
                    created_at_ms=900,
                ),
            )
            assert source.extraction is not None
            source_details = LegacyMigrationDetails(
                plan_sha256=_PLAN,
                inventory_manifest_sha256="b" * 64,
                root_binding_sha256="c" * 64,
                inventory_record_id=source.inventory_record_id,
                inventory_record_sha256="d" * 64,
                root_id="legacy-root",
                path_key_sha256=hashlib.sha256(source_path.encode()).hexdigest(),
                category="knowledge.wiki_sources",
                record_kind="physical_file",
                source_ordinal=0,
                import_mode="personal_item",
                result_disposition="created",
                target_space_id=space_id,
                blob_sha256=source.raw_blob.sha256,
                artifact_blob_id=source.ids.raw_manifest_id,
                extraction_snapshot_id=source.ids.extraction_snapshot_id,
                item_source_id=imported.item_source_id,
                normalized_content_sha256=(
                    source.extraction.normalized_content_sha256
                ),
                normalizer_version="markdown_lf_v1",
                source_size_bytes=source.raw_blob.size_bytes,
                source_media_type=source.raw_blob.media_type,
                source_record_sha256=source.raw_blob.sha256,
            )
            source_target = LegacyMigrationTarget(
                resolved_owner_user_id="owner",
                artifact_id=source.ids.artifact_id,
                item_id=imported.item_id,
                revision_id=imported.revision_id,
                edge_id=None,
                details=source_details,
                source_path_key=source_path,
            )
            source_mapping_id = migration_mapping_id_for_record(
                _PLAN, source.inventory_record_id
            )
            records.put_imported(
                connection,
                mapping_id=source_mapping_id,
                migration_run_id=migration_run_id_for_plan(_PLAN),
                source_path_key=source_path,
                key=LegacyMigrationKey(
                    legacy_kind="knowledge.wiki_sources",
                    normalized_path=source.origin_key,
                    source_sha256=source.raw_blob.sha256,
                ),
                target=source_target,
                verified_blob_evidence=VerifiedBlobEvidence(
                    raw_sha256=source.raw_blob.sha256,
                    size_bytes=source.raw_blob.size_bytes,
                    media_type=source.raw_blob.media_type,
                    normalized_content_sha256=(
                        source.extraction.normalized_content_sha256
                    ),
                    normalizer_version="markdown_lf_v1",
                ),
                now_ms=950,
            )
            assert second_source.extraction is not None
            second_details = LegacyMigrationDetails(
                plan_sha256=_PLAN,
                inventory_manifest_sha256="b" * 64,
                root_binding_sha256="c" * 64,
                inventory_record_id=second_source.inventory_record_id,
                inventory_record_sha256="f" * 64,
                root_id="legacy-root",
                path_key_sha256=hashlib.sha256(
                    second_source_path.encode()
                ).hexdigest(),
                category="knowledge.wiki_sources",
                record_kind="physical_file",
                source_ordinal=0,
                import_mode="personal_item",
                result_disposition="provenance_added",
                target_space_id=space_id,
                blob_sha256=second_source.raw_blob.sha256,
                artifact_blob_id=second_source.ids.raw_manifest_id,
                extraction_snapshot_id=(
                    second_source.ids.extraction_snapshot_id
                ),
                item_source_id=provenance.item_source_id,
                normalized_content_sha256=(
                    second_source.extraction.normalized_content_sha256
                ),
                normalizer_version="markdown_lf_v1",
                source_size_bytes=second_source.raw_blob.size_bytes,
                source_media_type=second_source.raw_blob.media_type,
                source_record_sha256=second_source.raw_blob.sha256,
            )
            second_mapping_id = migration_mapping_id_for_record(
                _PLAN, second_source.inventory_record_id
            )
            records.put_imported(
                connection,
                mapping_id=second_mapping_id,
                migration_run_id=migration_run_id_for_plan(_PLAN),
                source_path_key=second_source_path,
                key=LegacyMigrationKey(
                    legacy_kind="knowledge.wiki_sources",
                    normalized_path=second_source.origin_key,
                    source_sha256=second_source.raw_blob.sha256,
                ),
                target=LegacyMigrationTarget(
                    resolved_owner_user_id="owner",
                    artifact_id=second_source.ids.artifact_id,
                    item_id=provenance.item_id,
                    revision_id=provenance.revision_id,
                    edge_id=None,
                    details=second_details,
                    source_path_key=second_source_path,
                ),
                verified_blob_evidence=VerifiedBlobEvidence(
                    raw_sha256=second_source.raw_blob.sha256,
                    size_bytes=second_source.raw_blob.size_bytes,
                    media_type=second_source.raw_blob.media_type,
                    normalized_content_sha256=(
                        second_source.extraction.normalized_content_sha256
                    ),
                    normalizer_version="markdown_lf_v1",
                ),
                now_ms=950,
            )
            asset_details = LegacyMigrationDetails(
                plan_sha256=_PLAN,
                inventory_manifest_sha256="b" * 64,
                root_binding_sha256="c" * 64,
                inventory_record_id=asset.inventory_record_id,
                inventory_record_sha256="e" * 64,
                root_id="legacy-root",
                path_key_sha256=hashlib.sha256(asset_path.encode()).hexdigest(),
                category="knowledge.wiki_assets",
                record_kind="physical_file",
                source_ordinal=0,
                import_mode="personal_asset",
                result_disposition="created",
                target_space_id=space_id,
                blob_sha256=asset.raw_blob.sha256,
                artifact_blob_id=asset.ids.raw_manifest_id,
                source_size_bytes=asset.raw_blob.size_bytes,
                source_media_type=asset.raw_blob.media_type,
                source_record_sha256=asset.raw_blob.sha256,
            )
            asset_target = LegacyMigrationTarget(
                resolved_owner_user_id="owner",
                artifact_id=asset.ids.artifact_id,
                item_id=None,
                revision_id=None,
                edge_id=None,
                details=asset_details,
                source_path_key=asset_path,
            )
            asset_mapping_id = migration_mapping_id_for_record(
                _PLAN, asset.inventory_record_id
            )
            records.put_imported(
                connection,
                mapping_id=asset_mapping_id,
                migration_run_id=migration_run_id_for_plan(_PLAN),
                source_path_key=asset_path,
                key=LegacyMigrationKey(
                    legacy_kind="knowledge.wiki_assets",
                    normalized_path=asset.origin_key,
                    source_sha256=asset.raw_blob.sha256,
                ),
                target=asset_target,
                verified_blob_evidence=VerifiedBlobEvidence(
                    raw_sha256=asset.raw_blob.sha256,
                    size_bytes=asset.raw_blob.size_bytes,
                    media_type=asset.raw_blob.media_type,
                ),
                now_ms=951,
            )
            relationship_target = LegacyMigrationRelationshipTarget(
                from_mapping_id=source_mapping_id,
                to_mapping_id=asset_mapping_id,
                resolved_owner_user_id="owner",
                target_space_id=space_id,
                source_artifact_id=source.ids.artifact_id,
                source_item_id=imported.item_id,
                source_revision_id=imported.revision_id,
                target_asset_artifact_id=asset.ids.artifact_id,
                target_asset_blob_id=asset.ids.raw_manifest_id,
                embedded_asset_blob_id=embedded_id,
                embedded_ordinal=2,
                embedded_logical_name_sha256=hashlib.sha256(
                    logical_name.encode("utf-8")
                ).hexdigest(),
                details=LegacyMigrationRelationshipDetails(
                    plan_sha256=_PLAN,
                    inventory_manifest_sha256="b" * 64,
                    root_binding_sha256="c" * 64,
                    relationship_id=relationship_id,
                    inventory_relationship_sha256=relationship_sha,
                    from_inventory_record_id=source.inventory_record_id,
                    to_inventory_record_id=asset.inventory_record_id,
                ),
            )
            relationship = relationships.put_imported(
                connection,
                relationship_mapping_id=(
                    migration_relationship_mapping_id_for_plan(
                        _PLAN, relationship_id
                    )
                ),
                migration_run_id=migration_run_id_for_plan(_PLAN),
                relationship_id=relationship_id,
                relationship_sha256=relationship_sha,
                target=relationship_target,
                source_verified_blob_evidence=VerifiedBlobEvidence(
                    raw_sha256=source.raw_blob.sha256,
                    size_bytes=source.raw_blob.size_bytes,
                    media_type=source.raw_blob.media_type,
                    normalized_content_sha256=(
                        source.extraction.normalized_content_sha256
                    ),
                    normalizer_version="markdown_lf_v1",
                ),
                target_verified_blob_evidence=VerifiedBlobEvidence(
                    raw_sha256=asset.raw_blob.sha256,
                    size_bytes=asset.raw_blob.size_bytes,
                    media_type=asset.raw_blob.media_type,
                ),
                now_ms=952,
            )
            assert relationship.inserted
    finally:
        connection.close()
