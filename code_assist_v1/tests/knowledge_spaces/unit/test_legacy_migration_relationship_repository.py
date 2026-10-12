from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import sqlite3
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.errors import KnowledgeCoreTransactionError
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.knowledge_core.repositories.artifacts import CAS_BLOB_MEDIA_TYPE
from code_assist_v1.knowledge_core.repositories.migration import (
    LegacyMigrationConflictError,
    LegacyMigrationDetails,
    LegacyMigrationIntegrityError,
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
from code_assist_v1.knowledge_core.repositories.users import LegacyUserRecord, UserRepository


pytestmark = pytest.mark.unit

_NAMESPACE = UUID("41342961-81bf-45c7-b5ea-4622190204f6")
_PLAN_A = "a" * 64
_PLAN_B = "b" * 64
_MANIFEST_SHA = "c" * 64
_ROOT_BINDING_SHA = "d" * 64
_ROOT_ID = "legacy-root"
_OWNER = "owner"
_RELATIONSHIP_ID = "rel_" + "1" * 24
_RELATIONSHIP_SHA = hashlib.sha256(b"reviewed asset relationship").hexdigest()


def _uuid(label: str) -> str:
    return str(uuid5(_NAMESPACE, label))


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _record_id(number: int) -> str:
    return f"rec_{number:024x}"


def _relationship_id(number: int) -> str:
    return f"rel_{number:024x}"


def _factory(layout) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path,
        CANONICAL_MIGRATION_CATALOG,
    )
    SingleMigrator(
        factory,
        app_version="p2.3-relationship-repository-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _legacy_user(user_id: str) -> LegacyUserRecord:
    return LegacyUserRecord(
        id=user_id,
        display_name=user_id.title(),
        password_hash=f"scrypt:p2.3:{user_id}",
        system_role="user",
        account_status="active",
        created_at_ms=100,
        updated_at_ms=100,
    )


def _seed_owner(connection, *, owner: str = _OWNER) -> str:
    UserRepository().import_legacy(connection, _legacy_user(owner))
    space_id = _uuid(f"personal-space:{owner}")
    SpaceRepository().ensure_personal(
        connection,
        space_id=space_id,
        user_id=owner,
        created_at_ms=200,
    )
    return space_id


def _insert_verified_blob(
    connection,
    *,
    sha256: str,
    size_bytes: int,
    media_type: str,
    now_ms: int,
) -> None:
    connection.execute(
        """INSERT INTO blob_object(
             sha256,size_bytes,media_type,relative_path,verification_status,
             created_at,verified_at
           ) VALUES (?,?,?,?,'verified',?,?)""",
        (
            sha256,
            size_bytes,
            CAS_BLOB_MEDIA_TYPE,
            f"{sha256[:2]}/{sha256[2:4]}/{sha256}",
            now_ms,
            now_ms,
        ),
    )


def _insert_artifact(
    connection,
    *,
    artifact_id: str,
    artifact_blob_id: str,
    locator: str,
    inventory_record_id: str,
    raw_sha256: str,
    size_bytes: int,
    media_type: str,
    filename: str,
    owner: str,
    now_ms: int,
) -> None:
    connection.execute(
        """INSERT INTO source_artifact(
             id,origin_type,origin_key,origin_revision_key,raw_sha256,
             raw_size_bytes,media_type,original_filename,
             body_retention_status,body_retention_reason,body_purged_at,
             orphan_cleanup_eligible_at,publisher_user_id,
             source_owner_snapshot_json,metadata_json,metadata_schema_version,
             created_at
           ) VALUES (?,'legacy',?,?,?,?,?,?,'present',NULL,NULL,?,?,
             '{}','{}',1,?)""",
        (
            artifact_id,
            locator,
            inventory_record_id,
            raw_sha256,
            size_bytes,
            media_type,
            filename,
            now_ms + 2_592_000_000,
            owner,
            now_ms,
        ),
    )
    connection.execute(
        """INSERT INTO artifact_blob(
             id,artifact_id,content_sha256,size_bytes,role,logical_name,
             media_type,ordinal,body_retention_status,created_at
           ) VALUES (?,?,?,?,'raw',?,?,0,'present',?)""",
        (
            artifact_blob_id,
            artifact_id,
            raw_sha256,
            size_bytes,
            filename,
            media_type,
            now_ms,
        ),
    )
    connection.execute(
        """INSERT INTO artifact_blob_body(
             artifact_blob_id,content_sha256,created_at
           ) VALUES (?,?,?)""",
        (artifact_blob_id, raw_sha256, now_ms),
    )


@dataclass(frozen=True, slots=True)
class _Endpoints:
    owner: str
    space_id: str
    plan_sha256: str
    item_target: LegacyMigrationTarget
    asset_target: LegacyMigrationTarget
    item_mapping_id: str
    asset_mapping_id: str
    item_evidence: VerifiedBlobEvidence
    asset_evidence: VerifiedBlobEvidence
    embedded_asset_blob_id: str
    embedded_ordinal: int
    embedded_logical_name: str


def _seed_endpoints(
    connection,
    *,
    owner: str,
    space_id: str,
    label: str = "a",
    plan_sha256: str = _PLAN_A,
    record_number: int = 100,
    asset_inventory_manifest_sha256: str = _MANIFEST_SHA,
    asset_root_binding_sha256: str = _ROOT_BINDING_SHA,
) -> _Endpoints:
    records = LegacyMigrationRepository()
    item_record_id = _record_id(record_number)
    asset_record_id = _record_id(record_number + 1)
    item_path = f"wiki/sources/{label}.md"
    asset_path = f"wiki/assets/{label}.png"
    item_locator = migration_source_locator(
        root_id=_ROOT_ID,
        path_key=item_path,
        record_kind="physical_file",
        source_ordinal=0,
    )
    asset_locator = migration_source_locator(
        root_id=_ROOT_ID,
        path_key=asset_path,
        record_kind="physical_file",
        source_ordinal=0,
    )

    item_bytes = f"body-{label}".encode("utf-8")
    item_sha = hashlib.sha256(item_bytes).hexdigest()
    normalized_sha = hashlib.sha256(item_bytes).hexdigest()
    item_artifact_id = _uuid(f"item-artifact:{plan_sha256}:{label}")
    item_artifact_blob_id = _uuid(f"item-raw-blob:{plan_sha256}:{label}")
    item_id = _uuid(f"item:{plan_sha256}:{label}")
    revision_id = _uuid(f"revision:{plan_sha256}:{label}")
    extraction_id = _uuid(f"extraction:{plan_sha256}:{label}")
    item_source_id = _uuid(f"item-source:{plan_sha256}:{label}")
    _insert_verified_blob(
        connection,
        sha256=item_sha,
        size_bytes=len(item_bytes),
        media_type="text/markdown",
        now_ms=300,
    )
    _insert_artifact(
        connection,
        artifact_id=item_artifact_id,
        artifact_blob_id=item_artifact_blob_id,
        locator=item_locator,
        inventory_record_id=item_record_id,
        raw_sha256=item_sha,
        size_bytes=len(item_bytes),
        media_type="text/markdown",
        filename=f"{label}.md",
        owner=owner,
        now_ms=300,
    )
    connection.execute(
        """INSERT INTO extraction_snapshot(
             id,artifact_id,status,extracted_sha256,normalized_content_sha256,
             extracted_size_bytes,normalizer_version,extractor_name,
             extractor_version,warnings_json,body_retention_status,created_at
           ) VALUES (?,?,'ready',?,?,?,'markdown_lf_v1','legacy-import','v1',
             '[]','present',301)""",
        (extraction_id, item_artifact_id, item_sha, normalized_sha, len(item_bytes)),
    )
    connection.execute(
        """INSERT INTO extraction_snapshot_body(
             snapshot_id,artifact_id,content_md,content_blob_hash,
             content_size_bytes,content_sha256,created_at
           ) VALUES (?,?,?,NULL,?,?,301)""",
        (
            extraction_id,
            item_artifact_id,
            item_bytes.decode("utf-8"),
            len(item_bytes),
            item_sha,
        ),
    )
    connection.execute(
        """INSERT INTO knowledge_item(
             id,space_id,lineage_group_id,lifecycle_status,curation_status,
             current_revision_id,last_curation_transition_id,
             latest_deactivation_episode_id,redirect_item_id,created_by,
             updated_by,created_at,updated_at,row_version
           ) VALUES (?,?,NULL,'active','provisional',?,NULL,NULL,NULL,?,?,302,302,1)""",
        (item_id, space_id, revision_id, owner, owner),
    )
    connection.execute(
        """INSERT INTO knowledge_revision(
             id,item_id,revision_no,parent_revision_id,base_revision_id,title,
             raw_content_sha256,normalized_content_sha256,content_size_bytes,
             normalizer_version,metadata_json,change_kind,change_reason,
             created_by,created_at
           ) VALUES (?,?,1,NULL,NULL,?,?,?,?,?,'{}','import',
             'legacy migration',?,302)""",
        (
            revision_id,
            item_id,
            f"Imported {label}",
            item_sha,
            normalized_sha,
            len(item_bytes),
            "markdown_lf_v1",
            owner,
        ),
    )
    connection.execute(
        """INSERT INTO knowledge_revision_body(
             revision_id,item_id,content_md,content_blob_hash,
             content_size_bytes,content_sha256,created_at
           ) VALUES (?,?,?,NULL,?,?,302)""",
        (
            revision_id,
            item_id,
            item_bytes.decode("utf-8"),
            len(item_bytes),
            item_sha,
        ),
    )
    connection.execute(
        """INSERT INTO item_source(
             id,item_id,revision_id,artifact_id,extraction_snapshot_id,role,
             created_by,created_at
           ) VALUES (?,?,?,?,?,'migrated',?,302)""",
        (
            item_source_id,
            item_id,
            revision_id,
            item_artifact_id,
            extraction_id,
            owner,
        ),
    )
    item_details = LegacyMigrationDetails(
        plan_sha256=plan_sha256,
        inventory_manifest_sha256=_MANIFEST_SHA,
        root_binding_sha256=_ROOT_BINDING_SHA,
        inventory_record_id=item_record_id,
        inventory_record_sha256=_sha_text(f"item-record:{label}"),
        root_id=_ROOT_ID,
        path_key_sha256=_sha_text(item_path),
        category="knowledge.wiki_sources",
        record_kind="physical_file",
        source_ordinal=0,
        import_mode="personal_item",
        result_disposition="created",
        target_space_id=space_id,
        blob_sha256=item_sha,
        artifact_blob_id=item_artifact_blob_id,
        extraction_snapshot_id=extraction_id,
        item_source_id=item_source_id,
        normalized_content_sha256=normalized_sha,
        normalizer_version="markdown_lf_v1",
        source_size_bytes=len(item_bytes),
        source_media_type="text/markdown",
        source_record_sha256=item_sha,
    )
    item_target = LegacyMigrationTarget(
        resolved_owner_user_id=owner,
        artifact_id=item_artifact_id,
        item_id=item_id,
        revision_id=revision_id,
        edge_id=None,
        details=item_details,
        source_path_key=item_path,
    )
    item_mapping_id = migration_mapping_id_for_record(plan_sha256, item_record_id)
    item_evidence = VerifiedBlobEvidence(
        raw_sha256=item_sha,
        size_bytes=len(item_bytes),
        media_type="text/markdown",
        normalized_content_sha256=normalized_sha,
        normalizer_version="markdown_lf_v1",
    )
    records.put_imported(
        connection,
        mapping_id=item_mapping_id,
        migration_run_id=migration_run_id_for_plan(plan_sha256),
        key=LegacyMigrationKey(
            legacy_kind=item_details.category,
            normalized_path=item_locator,
            source_sha256=item_sha,
        ),
        target=item_target,
        verified_blob_evidence=item_evidence,
        now_ms=310,
    )

    asset_bytes = f"asset-{label}".encode("utf-8")
    asset_sha = hashlib.sha256(asset_bytes).hexdigest()
    asset_artifact_id = _uuid(f"asset-artifact:{plan_sha256}:{label}")
    asset_blob_id = _uuid(f"asset-raw-blob:{plan_sha256}:{label}")
    _insert_verified_blob(
        connection,
        sha256=asset_sha,
        size_bytes=len(asset_bytes),
        media_type="image/png",
        now_ms=320,
    )
    _insert_artifact(
        connection,
        artifact_id=asset_artifact_id,
        artifact_blob_id=asset_blob_id,
        locator=asset_locator,
        inventory_record_id=asset_record_id,
        raw_sha256=asset_sha,
        size_bytes=len(asset_bytes),
        media_type="image/png",
        filename=f"{label}.png",
        owner=owner,
        now_ms=320,
    )
    asset_details = LegacyMigrationDetails(
        plan_sha256=plan_sha256,
        inventory_manifest_sha256=asset_inventory_manifest_sha256,
        root_binding_sha256=asset_root_binding_sha256,
        inventory_record_id=asset_record_id,
        inventory_record_sha256=_sha_text(f"asset-record:{label}"),
        root_id=_ROOT_ID,
        path_key_sha256=_sha_text(asset_path),
        category="knowledge.wiki_assets",
        record_kind="physical_file",
        source_ordinal=0,
        import_mode="personal_asset",
        result_disposition="created",
        target_space_id=space_id,
        blob_sha256=asset_sha,
        artifact_blob_id=asset_blob_id,
        source_size_bytes=len(asset_bytes),
        source_media_type="image/png",
        source_record_sha256=asset_sha,
    )
    asset_target = LegacyMigrationTarget(
        resolved_owner_user_id=owner,
        artifact_id=asset_artifact_id,
        item_id=None,
        revision_id=None,
        edge_id=None,
        details=asset_details,
        source_path_key=asset_path,
    )
    asset_mapping_id = migration_mapping_id_for_record(plan_sha256, asset_record_id)
    asset_evidence = VerifiedBlobEvidence(
        raw_sha256=asset_sha,
        size_bytes=len(asset_bytes),
        media_type="image/png",
    )
    records.put_imported(
        connection,
        mapping_id=asset_mapping_id,
        migration_run_id=migration_run_id_for_plan(plan_sha256),
        key=LegacyMigrationKey(
            legacy_kind=asset_details.category,
            normalized_path=asset_locator,
            source_sha256=asset_sha,
        ),
        target=asset_target,
        verified_blob_evidence=asset_evidence,
        now_ms=330,
    )

    embedded_asset_blob_id = _uuid(f"embedded-asset:{plan_sha256}:{label}")
    embedded_ordinal = 1
    logical_name = f"images/{label}.png"
    connection.execute(
        """INSERT INTO artifact_blob(
             id,artifact_id,content_sha256,size_bytes,role,logical_name,
             media_type,ordinal,body_retention_status,created_at
           ) VALUES (?,?,?,?,'embedded_asset',?,?,?,'present',340)""",
        (
            embedded_asset_blob_id,
            item_artifact_id,
            asset_sha,
            len(asset_bytes),
            logical_name,
            "image/png",
            embedded_ordinal,
        ),
    )
    connection.execute(
        """INSERT INTO artifact_blob_body(
             artifact_blob_id,content_sha256,created_at
           ) VALUES (?,?,340)""",
        (embedded_asset_blob_id, asset_sha),
    )
    return _Endpoints(
        owner=owner,
        space_id=space_id,
        plan_sha256=plan_sha256,
        item_target=item_target,
        asset_target=asset_target,
        item_mapping_id=item_mapping_id,
        asset_mapping_id=asset_mapping_id,
        item_evidence=item_evidence,
        asset_evidence=asset_evidence,
        embedded_asset_blob_id=embedded_asset_blob_id,
        embedded_ordinal=embedded_ordinal,
        embedded_logical_name=logical_name,
    )


def _relationship_target(
    endpoints: _Endpoints,
    *,
    relationship_id: str = _RELATIONSHIP_ID,
    relationship_sha256: str = _RELATIONSHIP_SHA,
) -> LegacyMigrationRelationshipTarget:
    return LegacyMigrationRelationshipTarget(
        from_mapping_id=endpoints.item_mapping_id,
        to_mapping_id=endpoints.asset_mapping_id,
        resolved_owner_user_id=endpoints.owner,
        target_space_id=endpoints.space_id,
        source_artifact_id=str(endpoints.item_target.artifact_id),
        source_item_id=str(endpoints.item_target.item_id),
        source_revision_id=str(endpoints.item_target.revision_id),
        target_asset_artifact_id=str(endpoints.asset_target.artifact_id),
        target_asset_blob_id=str(endpoints.asset_target.details.artifact_blob_id),
        embedded_asset_blob_id=endpoints.embedded_asset_blob_id,
        embedded_ordinal=endpoints.embedded_ordinal,
        embedded_logical_name_sha256=_sha_text(endpoints.embedded_logical_name),
        details=LegacyMigrationRelationshipDetails(
            plan_sha256=endpoints.plan_sha256,
            inventory_manifest_sha256=_MANIFEST_SHA,
            root_binding_sha256=_ROOT_BINDING_SHA,
            relationship_id=relationship_id,
            inventory_relationship_sha256=relationship_sha256,
            from_inventory_record_id=endpoints.item_target.details.inventory_record_id,
            to_inventory_record_id=endpoints.asset_target.details.inventory_record_id,
        ),
    )


def _put_relationship(
    repository: LegacyMigrationRelationshipRepository,
    connection,
    endpoints: _Endpoints,
    *,
    target: LegacyMigrationRelationshipTarget | None = None,
    relationship_id: str = _RELATIONSHIP_ID,
    relationship_sha256: str = _RELATIONSHIP_SHA,
    now_ms: int = 500,
):
    target = target or _relationship_target(
        endpoints,
        relationship_id=relationship_id,
        relationship_sha256=relationship_sha256,
    )
    return repository.put_imported(
        connection,
        relationship_mapping_id=migration_relationship_mapping_id_for_plan(
            target.details.plan_sha256,
            relationship_id,
        ),
        migration_run_id=migration_run_id_for_plan(target.details.plan_sha256),
        relationship_id=relationship_id,
        relationship_sha256=relationship_sha256,
        target=target,
        source_verified_blob_evidence=endpoints.item_evidence,
        target_verified_blob_evidence=endpoints.asset_evidence,
        now_ms=now_ms,
    )


def _seed_fixture(factory: ConnectionFactory) -> _Endpoints:
    connection = factory.open()
    try:
        with transaction(connection):
            space_id = _seed_owner(connection)
            return _seed_endpoints(
                connection,
                owner=_OWNER,
                space_id=space_id,
            )
    finally:
        connection.close()


def test_relationship_mapping_id_and_insert_exact_replay_are_frozen(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    endpoints = _seed_fixture(factory)
    repository = LegacyMigrationRelationshipRepository()
    target = _relationship_target(endpoints)
    connection = factory.open()
    try:
        with pytest.raises(KnowledgeCoreTransactionError):
            _put_relationship(repository, connection, endpoints, target=target)

        with transaction(connection):
            first = _put_relationship(
                repository,
                connection,
                endpoints,
                target=target,
                now_ms=500,
            )
            replay = _put_relationship(
                repository,
                connection,
                endpoints,
                target=target,
                now_ms=999,
            )

        assert migration_run_id_for_plan(_PLAN_A) == (
            "c05a5a16-2408-5ae0-8a38-4fe0403c4fc4"
        )
        assert migration_relationship_mapping_id_for_plan(
            _PLAN_A, _RELATIONSHIP_ID
        ) == "10f3e62c-2591-5d31-b42d-a2b794bd5f84"
        assert first.inserted is True
        assert replay.inserted is False
        assert replay.record == first.record
        assert first.record.created_at_ms == first.record.updated_at_ms == 500
        assert first.record.relationship_sha256 == _RELATIONSHIP_SHA
        assert first.record.relationship_type == "asset_reference"
        assert first.record.status == "imported"
        expected_details = {
            "embedded_asset_blob_id": target.embedded_asset_blob_id,
            "embedded_logical_name_sha256": target.embedded_logical_name_sha256,
            "embedded_ordinal": target.embedded_ordinal,
            "execution_phase": "P2.3",
            "final_terminal_classification": "imported",
            "from_inventory_record_id": target.details.from_inventory_record_id,
            "from_mapping_id": target.from_mapping_id,
            "import_mode": "personal_asset",
            "inventory_manifest_sha256": _MANIFEST_SHA,
            "inventory_relationship_sha256": _RELATIONSHIP_SHA,
            "plan_sha256": _PLAN_A,
            "relationship_id": _RELATIONSHIP_ID,
            "relationship_type": "asset_reference",
            "resolved_owner_user_id": _OWNER,
            "root_binding_sha256": _ROOT_BINDING_SHA,
            "schema_version": 1,
            "source_artifact_id": target.source_artifact_id,
            "source_item_id": target.source_item_id,
            "source_revision_id": target.source_revision_id,
            "status": "imported",
            "target_asset_artifact_id": target.target_asset_artifact_id,
            "target_asset_blob_id": target.target_asset_blob_id,
            "target_space_id": target.target_space_id,
            "to_inventory_record_id": target.details.to_inventory_record_id,
            "to_mapping_id": target.to_mapping_id,
        }
        assert first.record.details_json == json.dumps(
            expected_details,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        assert repository.get_by_id(connection, first.record.id) == first.record
        assert repository.get_by_relationship_id(
            connection, _RELATIONSHIP_ID
        ) == first.record
        assert connection.execute(
            "SELECT count(*) FROM knowledge_edge"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize(
    "mutation",
    (
        "relationship_sha256",
        "from_mapping_id",
        "to_mapping_id",
        "owner",
        "space",
        "source_artifact",
        "source_item",
        "source_revision",
        "asset_artifact",
        "asset_blob",
        "ordinal",
        "logical_name",
    ),
)
def test_exact_replay_rejects_every_changed_relationship_binding(
    isolated_knowledge_layout,
    mutation: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    endpoints = _seed_fixture(factory)
    repository = LegacyMigrationRelationshipRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            _put_relationship(repository, connection, endpoints)

        target = _relationship_target(endpoints)
        relationship_sha256 = _RELATIONSHIP_SHA
        if mutation == "relationship_sha256":
            relationship_sha256 = "9" * 64
            target = replace(
                target,
                details=replace(
                    target.details,
                    inventory_relationship_sha256=relationship_sha256,
                ),
            )
        elif mutation == "owner":
            target = replace(target, resolved_owner_user_id="different-owner")
        elif mutation == "space":
            target = replace(target, target_space_id=_uuid("different-space"))
        elif mutation == "logical_name":
            target = replace(target, embedded_logical_name_sha256="8" * 64)
        elif mutation == "ordinal":
            target = replace(target, embedded_ordinal=target.embedded_ordinal + 1)
        else:
            field = {
                "from_mapping_id": "from_mapping_id",
                "to_mapping_id": "to_mapping_id",
                "source_artifact": "source_artifact_id",
                "source_item": "source_item_id",
                "source_revision": "source_revision_id",
                "asset_artifact": "target_asset_artifact_id",
                "asset_blob": "target_asset_blob_id",
            }[mutation]
            target = replace(target, **{field: _uuid(f"changed:{field}")})

        with pytest.raises(LegacyMigrationConflictError):
            with transaction(connection):
                _put_relationship(
                    repository,
                    connection,
                    endpoints,
                    target=target,
                    relationship_sha256=relationship_sha256,
                )
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_relationship_map"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_preinsert_requires_exact_same_plan_endpoint_mappings(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    endpoints = _seed_fixture(factory)
    repository = LegacyMigrationRelationshipRepository()
    target = _relationship_target(endpoints)
    target = replace(
        target,
        details=replace(target.details, plan_sha256=_PLAN_B),
    )
    connection = factory.open()
    try:
        with pytest.raises(LegacyMigrationIntegrityError):
            with transaction(connection):
                _put_relationship(repository, connection, endpoints, target=target)
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_relationship_map"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize("binding", ("inventory_manifest_sha256", "root_binding_sha256"))
def test_relationship_review_bindings_must_match_both_endpoint_mappings(
    isolated_knowledge_layout,
    binding: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    endpoints = _seed_fixture(factory)
    repository = LegacyMigrationRelationshipRepository()
    target = _relationship_target(endpoints)
    target = replace(
        target,
        details=replace(target.details, **{binding: "5" * 64}),
    )
    connection = factory.open()
    try:
        with pytest.raises(LegacyMigrationIntegrityError):
            with transaction(connection):
                _put_relationship(repository, connection, endpoints, target=target)
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_relationship_map"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize("binding", ("inventory_manifest_sha256", "root_binding_sha256"))
def test_relationship_review_bindings_cannot_match_only_one_endpoint(
    isolated_knowledge_layout,
    binding: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            space_id = _seed_owner(connection)
            endpoint_kwargs = {
                "asset_inventory_manifest_sha256": _MANIFEST_SHA,
                "asset_root_binding_sha256": _ROOT_BINDING_SHA,
            }
            endpoint_kwargs[
                "asset_inventory_manifest_sha256"
                if binding == "inventory_manifest_sha256"
                else "asset_root_binding_sha256"
            ] = "4" * 64
            endpoints = _seed_endpoints(
                connection,
                owner=_OWNER,
                space_id=space_id,
                **endpoint_kwargs,
            )
        with pytest.raises(LegacyMigrationIntegrityError):
            with transaction(connection):
                _put_relationship(
                    LegacyMigrationRelationshipRepository(),
                    connection,
                    endpoints,
                )
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_relationship_map"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_preinsert_requires_canonical_owner_space_and_exact_endpoint_targets(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    endpoints = _seed_fixture(factory)
    repository = LegacyMigrationRelationshipRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            other_space = _seed_owner(connection, owner="other")
        target = replace(
            _relationship_target(endpoints),
            resolved_owner_user_id="other",
            target_space_id=other_space,
        )
        with pytest.raises(LegacyMigrationIntegrityError):
            with transaction(connection):
                _put_relationship(repository, connection, endpoints, target=target)

        wrong_top_level = replace(
            _relationship_target(endpoints),
            source_artifact_id=str(endpoints.asset_target.artifact_id),
        )
        with pytest.raises(LegacyMigrationIntegrityError):
            with transaction(connection):
                _put_relationship(
                    repository,
                    connection,
                    endpoints,
                    target=wrong_top_level,
                )
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_relationship_map"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize("evidence_side", ("source", "target"))
def test_both_pretransaction_blob_observations_are_mandatory_and_exact(
    isolated_knowledge_layout,
    evidence_side: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    endpoints = _seed_fixture(factory)
    repository = LegacyMigrationRelationshipRepository()
    target = _relationship_target(endpoints)
    source_evidence = endpoints.item_evidence
    target_evidence = endpoints.asset_evidence
    if evidence_side == "source":
        source_evidence = replace(source_evidence, raw_sha256="7" * 64)
    else:
        target_evidence = replace(target_evidence, media_type="image/jpeg")
    connection = factory.open()
    try:
        with pytest.raises(LegacyMigrationIntegrityError):
            with transaction(connection):
                repository.put_imported(
                    connection,
                    relationship_mapping_id=migration_relationship_mapping_id_for_plan(
                        _PLAN_A, _RELATIONSHIP_ID
                    ),
                    migration_run_id=migration_run_id_for_plan(_PLAN_A),
                    relationship_id=_RELATIONSHIP_ID,
                    relationship_sha256=_RELATIONSHIP_SHA,
                    target=target,
                    source_verified_blob_evidence=source_evidence,
                    target_verified_blob_evidence=target_evidence,
                    now_ms=500,
                )
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_relationship_map"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize("mutation", ("ordinal", "logical_name"))
def test_embedded_asset_effect_binds_ordinal_and_logical_name_hash(
    isolated_knowledge_layout,
    mutation: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    endpoints = _seed_fixture(factory)
    target = _relationship_target(endpoints)
    if mutation == "ordinal":
        target = replace(target, embedded_ordinal=target.embedded_ordinal + 1)
    else:
        target = replace(target, embedded_logical_name_sha256="6" * 64)
    connection = factory.open()
    try:
        with pytest.raises(LegacyMigrationIntegrityError):
            with transaction(connection):
                _put_relationship(
                    LegacyMigrationRelationshipRepository(),
                    connection,
                    endpoints,
                    target=target,
                )
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_relationship_map"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_embedded_asset_requires_role_body_and_target_asset_content(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    endpoints = _seed_fixture(factory)
    alternate_id = _uuid("wrong-attachment-role")
    asset = endpoints.asset_target
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                """INSERT INTO artifact_blob(
                     id,artifact_id,content_sha256,size_bytes,role,logical_name,
                     media_type,ordinal,body_retention_status,created_at
                   ) VALUES (?,?,?,?,'attachment',?,?,2,'present',400)""",
                (
                    alternate_id,
                    endpoints.item_target.artifact_id,
                    asset.details.blob_sha256,
                    asset.details.source_size_bytes,
                    "wrong-role.png",
                    asset.details.source_media_type,
                ),
            )
            connection.execute(
                """INSERT INTO artifact_blob_body(
                     artifact_blob_id,content_sha256,created_at
                   ) VALUES (?,?,400)""",
                (alternate_id, asset.details.blob_sha256),
            )
        target = replace(
            _relationship_target(endpoints),
            embedded_asset_blob_id=alternate_id,
            embedded_ordinal=2,
            embedded_logical_name_sha256=_sha_text("wrong-role.png"),
        )
        with pytest.raises(LegacyMigrationIntegrityError):
            with transaction(connection):
                _put_relationship(
                    LegacyMigrationRelationshipRepository(),
                    connection,
                    endpoints,
                    target=target,
                )
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_relationship_map"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_caught_integrity_failure_leaves_no_checkpoint_and_outer_tx_usable(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    endpoints = _seed_fixture(factory)
    repository = LegacyMigrationRelationshipRepository()
    bad_target = replace(
        _relationship_target(endpoints),
        embedded_ordinal=endpoints.embedded_ordinal + 10,
    )
    connection = factory.open()
    try:
        with transaction(connection):
            with pytest.raises(LegacyMigrationIntegrityError):
                _put_relationship(
                    repository,
                    connection,
                    endpoints,
                    target=bad_target,
                )
            UserRepository().import_legacy(connection, _legacy_user("after-failure"))

        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_relationship_map"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT id FROM app_user WHERE id='after-failure'"
        ).fetchone()[0] == "after-failure"
    finally:
        connection.close()


def test_v4_relationship_history_rejects_update_and_delete(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    endpoints = _seed_fixture(factory)
    connection = factory.open()
    try:
        with transaction(connection):
            result = _put_relationship(
                LegacyMigrationRelationshipRepository(),
                connection,
                endpoints,
            )
        for statement in (
            "UPDATE legacy_migration_relationship_map SET updated_at=updated_at WHERE id=?",
            "DELETE FROM legacy_migration_relationship_map WHERE id=?",
        ):
            with pytest.raises(
                sqlite3.IntegrityError,
                match="LEGACY_MIGRATION_RELATIONSHIP_MAP_IMMUTABLE",
            ):
                with transaction(connection):
                    connection.execute(statement, (result.record.id,))
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_relationship_map"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_relationship_list_for_run_has_stable_relationship_id_order(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = LegacyMigrationRelationshipRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            space_id = _seed_owner(connection)
            first_endpoints = _seed_endpoints(
                connection,
                owner=_OWNER,
                space_id=space_id,
                label="order-a",
                record_number=200,
            )
            second_endpoints = _seed_endpoints(
                connection,
                owner=_OWNER,
                space_id=space_id,
                label="order-b",
                record_number=300,
            )
            for relationship_id, relationship_sha, endpoints in (
                (_relationship_id(2), _sha_text("relationship-2"), second_endpoints),
                (_relationship_id(1), _sha_text("relationship-1"), first_endpoints),
            ):
                _put_relationship(
                    repository,
                    connection,
                    endpoints,
                    relationship_id=relationship_id,
                    relationship_sha256=relationship_sha,
                )

        listed = repository.list_for_run(
            connection,
            migration_run_id_for_plan(_PLAN_A),
        )
        assert [record.relationship_id for record in listed] == [
            _relationship_id(1),
            _relationship_id(2),
        ]
        assert all(record.relationship_type == "asset_reference" for record in listed)
        assert connection.execute(
            "SELECT count(*) FROM knowledge_edge"
        ).fetchone()[0] == 0
    finally:
        connection.close()
