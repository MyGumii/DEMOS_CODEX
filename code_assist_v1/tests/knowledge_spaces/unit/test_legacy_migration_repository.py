from __future__ import annotations

from dataclasses import replace
import hashlib
import inspect
import json
import sqlite3
from uuid import UUID, uuid5

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.errors import KnowledgeCoreTransactionError
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
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
)
from code_assist_v1.knowledge_core.repositories.migration import (
    LegacyMigrationConflictError,
    LegacyMigrationDetails,
    LegacyMigrationIntegrityError,
    LegacyMigrationKey,
    LegacyMigrationRepository,
    LegacyMigrationTarget,
    VerifiedBlobEvidence,
    legacy_user_account_projection_sha256,
    migration_child_bindings_sha256,
    migration_mapping_id_for_record,
    migration_run_id_for_plan,
    migration_source_locator,
)
from code_assist_v1.knowledge_core.repositories.artifacts import CAS_BLOB_MEDIA_TYPE
from code_assist_v1.knowledge_core.repositories.organization import (
    OrganizationRepository,
    RESPONSIBILITY_ROOT_ID,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import (
    LegacyUserRecord,
    UserRecord,
    UserRepository,
)


pytestmark = pytest.mark.unit

_V7_CATALOG = MigrationCatalog(
    (
        SCHEMA_V1_MIGRATION,
        SCHEMA_V2_MIGRATION,
        SCHEMA_V3_MIGRATION,
        SCHEMA_V4_MIGRATION,
        SCHEMA_V5_MIGRATION,
        SCHEMA_V6_MIGRATION,
        SCHEMA_V7_MIGRATION,
    )
)

_FIXTURE_NAMESPACE = UUID("9c48a77d-76e3-45fd-8e22-7e9ea30e1e86")
_PLAN_A = "a" * 64
_PLAN_B = "b" * 64
_MANIFEST_SHA = "c" * 64
_ROOT_BINDING_SHA = "d" * 64
_RECORD_SHA = "e" * 64
_ROOT_ID = "legacy-root"
_USER_CATEGORY = "identity.users_registry"
_ITEM_CATEGORY = "knowledge.wiki_sources"
_NORMALIZED_SHA = hashlib.sha256(b"body").hexdigest()
_RECORD_1 = "rec_" + "0" * 23 + "1"
_USERS_CONTAINER_RECORD_ID = "rec_" + "0" * 21 + "100"


def _uuid(label: str) -> str:
    return str(uuid5(_FIXTURE_NAMESPACE, label))


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _factory(layout, catalog=CANONICAL_MIGRATION_CATALOG) -> ConnectionFactory:
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path,
        catalog,
    )
    SingleMigrator(
        factory,
        app_version="p2.3-legacy-mapping-test",
        storage_layout=MigrationStorageLayout(
            data_root=layout.root,
            database=layout.core_db_path,
            blob_root=layout.blobs_dir,
            projection_root=layout.projections_dir,
        ),
    ).run()
    return factory


def _verified_blob_evidence(target: LegacyMigrationTarget) -> VerifiedBlobEvidence:
    details = target.details
    assert details.source_record_sha256 is not None
    assert details.source_size_bytes is not None
    assert details.source_media_type is not None
    return VerifiedBlobEvidence(
        raw_sha256=details.source_record_sha256,
        size_bytes=details.source_size_bytes,
        media_type=details.source_media_type,
        normalized_content_sha256=details.normalized_content_sha256,
        normalizer_version=details.normalizer_version,
    )


class _RepositoryWithFixtureEvidence(LegacyMigrationRepository):
    """Keep non-filesystem repository fixtures focused on DB target closure."""

    def put_imported(self, connection, *, target, verified_blob_evidence=None, **kwargs):
        if verified_blob_evidence is None and target.details.import_mode != "user_account":
            verified_blob_evidence = _verified_blob_evidence(target)
        return super().put_imported(
            connection,
            target=target,
            verified_blob_evidence=verified_blob_evidence,
            **kwargs,
        )

    def verify_target_integrity(
        self, connection, record, *, verified_blob_evidence=None
    ):
        if (
            verified_blob_evidence is None
            and record.details.import_mode != "user_account"
        ):
            verified_blob_evidence = VerifiedBlobEvidence(
                raw_sha256=record.key.source_sha256,
                size_bytes=record.details.source_size_bytes,
                media_type=record.details.source_media_type,
                normalized_content_sha256=(
                    record.details.normalized_content_sha256
                ),
                normalizer_version=record.details.normalizer_version,
            )
        return super().verify_target_integrity(
            connection,
            record,
            verified_blob_evidence=verified_blob_evidence,
        )


def _repository() -> LegacyMigrationRepository:
    return _RepositoryWithFixtureEvidence()


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


def _seed_personal_owners(
    factory: ConnectionFactory,
    *user_ids: str,
) -> dict[str, str]:
    spaces: dict[str, str] = {}
    connection = factory.open()
    try:
        with transaction(connection):
            for index, user_id in enumerate(user_ids, start=1):
                UserRepository().import_legacy(connection, _legacy_user(user_id))
                space_id = _uuid(f"personal-space:{user_id}")
                SpaceRepository().ensure_personal(
                    connection,
                    space_id=space_id,
                    user_id=user_id,
                    created_at_ms=200 + index,
                )
                spaces[user_id] = space_id
    finally:
        connection.close()
    return spaces


def _seed_assigned_personal_owner(
    factory: ConnectionFactory,
    *,
    owner: str = "owner",
) -> tuple[LegacyUserRecord, str, str, str, str]:
    organization = OrganizationRepository()
    users = UserRepository()
    team_id = _uuid("account-evidence-team")
    part_id = _uuid("account-evidence-part")
    membership_id = _uuid("account-evidence-membership")
    leader_assignment_id = _uuid("account-evidence-leader")
    space_id = _uuid(f"personal-space:{owner}")
    legacy = replace(_legacy_user(owner), current_part_id=part_id)
    connection = factory.open()
    try:
        with transaction(connection):
            users.import_legacy(connection, _legacy_user("admin"))
            organization.create_unit(
                connection,
                unit_id=team_id,
                kind="team",
                parent_id=RESPONSIBILITY_ROOT_ID,
                name="Account Evidence Team",
                actor_user_id="admin",
                now_ms=120,
            )
            organization.create_unit(
                connection,
                unit_id=part_id,
                kind="part",
                parent_id=team_id,
                name="Account Evidence Part",
                actor_user_id="admin",
                now_ms=121,
            )
            users.import_legacy(connection, legacy)
            SpaceRepository().ensure_personal(
                connection,
                space_id=space_id,
                user_id=owner,
                created_at_ms=200,
            )
            organization.open_membership(
                connection,
                membership_id=membership_id,
                user_id=owner,
                part_id=part_id,
                assigned_by="admin",
                reason="legacy import",
                valid_from_ms=201,
            )
            organization.open_part_leader(
                connection,
                assignment_id=leader_assignment_id,
                user_id=owner,
                part_id=part_id,
                assigned_by="admin",
                reason="legacy import",
                valid_from_ms=202,
            )
    finally:
        connection.close()
    return legacy, space_id, part_id, membership_id, leader_assignment_id


def _record_id(number: int) -> str:
    return f"rec_{number:024x}"


def _user_details(
    *,
    owner: str,
    space_id: str,
    plan_sha256: str = _PLAN_A,
    record_id: str = _RECORD_1,
    path_key: str = "users.json#0",
    source_ordinal: int = 1,
    source_sha256: str = "1" * 64,
    result_disposition: str = "created",
    account_projection_sha256: str | None = None,
    assigned_part_id: str | None = None,
    membership_id: str | None = None,
    leader_assignment_id: str | None = None,
    parent_inventory_record_id: str = _USERS_CONTAINER_RECORD_ID,
) -> LegacyMigrationDetails:
    return LegacyMigrationDetails(
        plan_sha256=plan_sha256,
        inventory_manifest_sha256=_MANIFEST_SHA,
        root_binding_sha256=_ROOT_BINDING_SHA,
        inventory_record_id=record_id,
        inventory_record_sha256=_RECORD_SHA,
        root_id=_ROOT_ID,
        path_key_sha256=_sha(path_key),
        category=_USER_CATEGORY,
        record_kind="logical_record",
        source_ordinal=source_ordinal,
        import_mode="user_account",
        result_disposition=result_disposition,  # type: ignore[arg-type]
        target_space_id=space_id,
        source_record_sha256=source_sha256,
        account_projection_schema="legacy_user_import_v1",
        account_projection_sha256=(
            account_projection_sha256
            if account_projection_sha256 is not None
            else legacy_user_account_projection_sha256(_legacy_user(owner))
        ),
        assigned_part_id=assigned_part_id,
        membership_id=membership_id,
        leader_assignment_id=leader_assignment_id,
        parent_inventory_record_id=parent_inventory_record_id,
    )


def _user_mapping(
    *,
    owner: str,
    space_id: str,
    plan_sha256: str = _PLAN_A,
    record_id: str = _RECORD_1,
    path_key: str = "users.json#0",
    source_sha256: str = "1" * 64,
    source_ordinal: int = 1,
    result_disposition: str = "created",
    account_projection_sha256: str | None = None,
    assigned_part_id: str | None = None,
    membership_id: str | None = None,
    leader_assignment_id: str | None = None,
    parent_inventory_record_id: str = _USERS_CONTAINER_RECORD_ID,
) -> tuple[str, str, LegacyMigrationKey, LegacyMigrationTarget]:
    details = _user_details(
        owner=owner,
        space_id=space_id,
        plan_sha256=plan_sha256,
        record_id=record_id,
        path_key=path_key,
        source_ordinal=source_ordinal,
        source_sha256=source_sha256,
        result_disposition=result_disposition,
        account_projection_sha256=account_projection_sha256,
        assigned_part_id=assigned_part_id,
        membership_id=membership_id,
        leader_assignment_id=leader_assignment_id,
        parent_inventory_record_id=parent_inventory_record_id,
    )
    key = LegacyMigrationKey(
        legacy_kind=details.category,
        normalized_path=migration_source_locator(
            root_id=details.root_id,
            path_key=path_key,
            record_kind=details.record_kind,
            source_ordinal=details.source_ordinal,
        ),
        source_sha256=source_sha256,
    )
    target = LegacyMigrationTarget(
        resolved_owner_user_id=owner,
        artifact_id=None,
        item_id=None,
        revision_id=None,
        edge_id=None,
        details=details,
        source_path_key=path_key,
    )
    return (
        migration_mapping_id_for_record(plan_sha256, record_id),
        migration_run_id_for_plan(plan_sha256),
        key,
        target,
    )


def _put_user(
    repository: LegacyMigrationRepository,
    connection,
    *,
    owner: str,
    space_id: str,
    now_ms: int,
    plan_sha256: str = _PLAN_A,
    record_id: str = _RECORD_1,
    path_key: str = "users.json#0",
    source_sha256: str = "1" * 64,
    source_ordinal: int = 1,
    result_disposition: str = "created",
):
    mapping_id, run_id, key, target = _user_mapping(
        owner=owner,
        space_id=space_id,
        plan_sha256=plan_sha256,
        record_id=record_id,
        path_key=path_key,
        source_sha256=source_sha256,
        source_ordinal=source_ordinal,
        result_disposition=result_disposition,
    )
    return repository.put_imported(
        connection,
        mapping_id=mapping_id,
        migration_run_id=run_id,
        source_path_key=path_key,
        key=key,
        target=target,
        now_ms=now_ms,
    )


def _put_user_child(
    repository: LegacyMigrationRepository,
    connection,
    *,
    owner: str,
    space_id: str,
    record_id: str,
    source_ordinal: int,
    source_sha256: str,
    plan_sha256: str = _PLAN_A,
    parent_inventory_record_id: str = _USERS_CONTAINER_RECORD_ID,
):
    path_key = f"users.json#{source_ordinal - 1}"
    mapping_id, run_id, key, target = _user_mapping(
        owner=owner,
        space_id=space_id,
        plan_sha256=plan_sha256,
        record_id=record_id,
        path_key=path_key,
        source_sha256=source_sha256,
        source_ordinal=source_ordinal,
        parent_inventory_record_id=parent_inventory_record_id,
    )
    return repository.put_imported(
        connection,
        mapping_id=mapping_id,
        migration_run_id=run_id,
        key=key,
        target=target,
        now_ms=400 + source_ordinal,
    )


def _source_container_target(
    *,
    child_bindings: tuple[tuple[str, str, str], ...],
    plan_sha256: str = _PLAN_A,
    record_id: str = _USERS_CONTAINER_RECORD_ID,
    path_key: str = "users.json",
    source_sha256: str = hashlib.sha256(b"users-container").hexdigest(),
    planned_import_child_count: int | None = None,
    imported_child_bindings_sha256: str | None = None,
) -> LegacyMigrationTarget:
    details = LegacyMigrationDetails(
        plan_sha256=plan_sha256,
        inventory_manifest_sha256=_MANIFEST_SHA,
        root_binding_sha256=_ROOT_BINDING_SHA,
        inventory_record_id=record_id,
        inventory_record_sha256=_sha("users-container-inventory-record"),
        root_id=_ROOT_ID,
        path_key_sha256=_sha(path_key),
        category=_USER_CATEGORY,
        record_kind="physical_file",
        source_ordinal=0,
        import_mode="source_container",
        result_disposition="dependency_closed",
        source_size_bytes=len(b"users-container"),
        source_media_type="application/json",
        source_record_sha256=source_sha256,
        parent_inventory_record_id=None,
        planned_import_child_count=(
            len(child_bindings)
            if planned_import_child_count is None
            else planned_import_child_count
        ),
        imported_child_bindings_sha256=(
            migration_child_bindings_sha256(child_bindings)
            if imported_child_bindings_sha256 is None
            else imported_child_bindings_sha256
        ),
    )
    return LegacyMigrationTarget(
        resolved_owner_user_id=None,
        artifact_id=None,
        item_id=None,
        revision_id=None,
        edge_id=None,
        details=details,
        source_path_key=path_key,
    )


def _container_evidence(target: LegacyMigrationTarget) -> VerifiedBlobEvidence:
    assert target.details.source_record_sha256 is not None
    assert target.details.source_size_bytes is not None
    assert target.details.source_media_type is not None
    return VerifiedBlobEvidence(
        raw_sha256=target.details.source_record_sha256,
        size_bytes=target.details.source_size_bytes,
        media_type=target.details.source_media_type,
        normalized_content_sha256=None,
        normalizer_version=None,
    )


def _put_container(
    repository: LegacyMigrationRepository,
    connection,
    target: LegacyMigrationTarget,
    *,
    now_ms: int = 500,
):
    mapping_id, run_id, key = _mapping_for_target(target)
    return repository.put_imported(
        connection,
        mapping_id=mapping_id,
        migration_run_id=run_id,
        key=key,
        target=target,
        verified_blob_evidence=_container_evidence(target),
        now_ms=now_ms,
    )


def _item_details(
    *,
    space_id: str,
    artifact_blob_id: str,
    extraction_snapshot_id: str,
    item_source_id: str,
    plan_sha256: str = _PLAN_A,
    record_id: str = _record_id(20),
    category: str = _ITEM_CATEGORY,
    path_key: str = "wiki/sources/imported.md",
    blob_sha256: str = "2" * 64,
    result_disposition: str = "created",
) -> LegacyMigrationDetails:
    return LegacyMigrationDetails(
        plan_sha256=plan_sha256,
        inventory_manifest_sha256=_MANIFEST_SHA,
        root_binding_sha256=_ROOT_BINDING_SHA,
        inventory_record_id=record_id,
        inventory_record_sha256=_RECORD_SHA,
        root_id=_ROOT_ID,
        path_key_sha256=_sha(path_key),
        category=category,
        record_kind="physical_file",
        source_ordinal=0,
        import_mode="personal_item",
        result_disposition=result_disposition,  # type: ignore[arg-type]
        target_space_id=space_id,
        blob_sha256=blob_sha256,
        artifact_blob_id=artifact_blob_id,
        extraction_snapshot_id=extraction_snapshot_id,
        item_source_id=item_source_id,
        normalized_content_sha256=_NORMALIZED_SHA,
        normalizer_version="markdown_lf_v1",
        source_size_bytes=4,
        source_media_type="text/markdown",
        source_record_sha256=blob_sha256,
    )


def _item_target(
    *,
    owner: str,
    space_id: str,
    artifact_id: str | None = None,
    artifact_blob_id: str | None = None,
    extraction_snapshot_id: str | None = None,
    item_id: str | None = None,
    revision_id: str | None = None,
    item_source_id: str | None = None,
    **detail_overrides: object,
) -> LegacyMigrationTarget:
    artifact_id = artifact_id or _uuid("artifact:item")
    artifact_blob_id = artifact_blob_id or _uuid("artifact-blob:item")
    extraction_snapshot_id = extraction_snapshot_id or _uuid("extraction:item")
    item_id = item_id or _uuid("item:item")
    revision_id = revision_id or _uuid("revision:mapped")
    item_source_id = item_source_id or _uuid("item-source:item")
    details = _item_details(
        space_id=space_id,
        artifact_blob_id=artifact_blob_id,
        extraction_snapshot_id=extraction_snapshot_id,
        item_source_id=item_source_id,
        **detail_overrides,  # type: ignore[arg-type]
    )
    return LegacyMigrationTarget(
        resolved_owner_user_id=owner,
        artifact_id=artifact_id,
        item_id=item_id,
        revision_id=revision_id,
        edge_id=None,
        details=details,
        source_path_key=str(detail_overrides.get("path_key", "wiki/sources/imported.md")),
    )


def _mapping_for_target(
    target: LegacyMigrationTarget,
) -> tuple[str, str, LegacyMigrationKey]:
    path_key = target.source_path_key
    assert path_key is not None
    details = target.details
    return (
        migration_mapping_id_for_record(
            details.plan_sha256, details.inventory_record_id
        ),
        migration_run_id_for_plan(details.plan_sha256),
        LegacyMigrationKey(
            legacy_kind=details.category,
            normalized_path=migration_source_locator(
                root_id=details.root_id,
                path_key=path_key,
                record_kind=details.record_kind,
                source_ordinal=details.source_ordinal,
            ),
            source_sha256=details.source_record_sha256 or "",
        ),
    )


def _seed_personal_asset(
    connection,
    *,
    owner: str,
    space_id: str,
) -> LegacyMigrationTarget:
    artifact_id = _uuid("artifact:asset")
    artifact_blob_id = _uuid("artifact-blob:asset")
    path_key = "wiki/assets/image.png"
    raw_sha256 = hashlib.sha256(b"asset-body").hexdigest()
    size_bytes = len(b"asset-body")
    media_type = "image/png"
    record_id = _record_id(21)
    locator = migration_source_locator(
        root_id=_ROOT_ID,
        path_key=path_key,
        record_kind="physical_file",
        source_ordinal=0,
    )

    connection.execute(
        """INSERT INTO blob_object(
             sha256,size_bytes,media_type,relative_path,verification_status,
             created_at,verified_at
           ) VALUES (?,?,?,?, 'verified',300,300)""",
        (
            raw_sha256,
            size_bytes,
            CAS_BLOB_MEDIA_TYPE,
            f"{raw_sha256[:2]}/{raw_sha256[2:4]}/{raw_sha256}",
        ),
    )
    connection.execute(
        """INSERT INTO source_artifact(
             id,origin_type,origin_key,origin_revision_key,raw_sha256,
             raw_size_bytes,media_type,original_filename,
             body_retention_status,body_retention_reason,body_purged_at,
             orphan_cleanup_eligible_at,publisher_user_id,
             source_owner_snapshot_json,metadata_json,metadata_schema_version,
             created_at
           ) VALUES (?,'legacy',?,?,?,?,?,'image.png','present',NULL,NULL,
             2592000300,?,'{}','{}',1,300)""",
        (artifact_id, locator, record_id, raw_sha256, size_bytes, media_type, owner),
    )
    connection.execute(
        """INSERT INTO artifact_blob(
             id,artifact_id,content_sha256,size_bytes,role,logical_name,
             media_type,ordinal,body_retention_status,created_at
           ) VALUES (?,?,?,?,'raw','image.png',?,0,'present',300)""",
        (artifact_blob_id, artifact_id, raw_sha256, size_bytes, media_type),
    )
    connection.execute(
        """INSERT INTO artifact_blob_body(
             artifact_blob_id,content_sha256,created_at
           ) VALUES (?,?,300)""",
        (artifact_blob_id, raw_sha256),
    )
    details = LegacyMigrationDetails(
        plan_sha256=_PLAN_A,
        inventory_manifest_sha256=_MANIFEST_SHA,
        root_binding_sha256=_ROOT_BINDING_SHA,
        inventory_record_id=record_id,
        inventory_record_sha256=_RECORD_SHA,
        root_id=_ROOT_ID,
        path_key_sha256=_sha(path_key),
        category="knowledge.wiki_assets",
        record_kind="physical_file",
        source_ordinal=0,
        import_mode="personal_asset",
        result_disposition="created",
        target_space_id=space_id,
        blob_sha256=raw_sha256,
        artifact_blob_id=artifact_blob_id,
        source_size_bytes=size_bytes,
        source_media_type=media_type,
        source_record_sha256=raw_sha256,
    )
    return LegacyMigrationTarget(
        resolved_owner_user_id=owner,
        artifact_id=artifact_id,
        item_id=None,
        revision_id=None,
        edge_id=None,
        details=details,
        source_path_key=path_key,
    )


def _seed_historical_personal_item(
    connection,
    *,
    owner: str,
    space_id: str,
    raw_sha256: str = hashlib.sha256(b"body").hexdigest(),
    mapped_change_kind: str = "import",
    result_disposition: str = "created",
) -> LegacyMigrationTarget:
    artifact_id = _uuid("artifact:item")
    artifact_blob_id = _uuid("artifact-blob:item")
    extraction_id = _uuid("extraction:item")
    item_id = _uuid("item:item")
    mapped_revision_id = _uuid("revision:mapped")
    current_revision_id = _uuid("revision:current")
    item_source_id = _uuid("item-source:item")
    path_key = "wiki/sources/imported.md"
    locator = migration_source_locator(
        root_id=_ROOT_ID,
        path_key=path_key,
        record_kind="physical_file",
        source_ordinal=0,
    )
    record_id = _record_id(20)
    current_raw_sha = "3" * 64
    current_normalized_sha = "4" * 64
    base_revision_id = (
        _uuid("revision:base") if mapped_change_kind == "edit" else None
    )
    mapped_revision_no = 2 if base_revision_id is not None else 1

    connection.execute(
        """INSERT INTO blob_object(
             sha256,size_bytes,media_type,relative_path,verification_status,
             created_at,verified_at
           ) VALUES (?,4,?,?,'verified',300,300)""",
        (
            raw_sha256,
            CAS_BLOB_MEDIA_TYPE,
            f"{raw_sha256[:2]}/{raw_sha256[2:4]}/{raw_sha256}",
        ),
    )
    connection.execute(
        """INSERT INTO source_artifact(
             id,origin_type,origin_key,origin_revision_key,raw_sha256,
             raw_size_bytes,media_type,original_filename,
             body_retention_status,body_retention_reason,body_purged_at,
             orphan_cleanup_eligible_at,publisher_user_id,
             source_owner_snapshot_json,metadata_json,metadata_schema_version,
             created_at
           ) VALUES (?,'legacy',?,?,?,4,'text/markdown',
             'imported.md','present',NULL,NULL,2592000300,?,'{}','{}',1,300)""",
        (artifact_id, locator, record_id, raw_sha256, owner),
    )
    connection.execute(
        """INSERT INTO artifact_blob(
             id,artifact_id,content_sha256,size_bytes,role,logical_name,
             media_type,ordinal,body_retention_status,created_at
           ) VALUES (?,?,?,4,'raw','imported.md','text/markdown',0,'present',300)""",
        (artifact_blob_id, artifact_id, raw_sha256),
    )
    connection.execute(
        """INSERT INTO artifact_blob_body(
             artifact_blob_id,content_sha256,created_at
           ) VALUES (?,?,300)""",
        (artifact_blob_id, raw_sha256),
    )
    connection.execute(
        """INSERT INTO extraction_snapshot(
             id,artifact_id,status,extracted_sha256,normalized_content_sha256,
             extracted_size_bytes,normalizer_version,extractor_name,
             extractor_version,warnings_json,body_retention_status,created_at
           ) VALUES (?,?,'ready',?,?,4,'markdown_lf_v1','legacy-import','v1',
             '[]','present',301)""",
        (extraction_id, artifact_id, raw_sha256, _NORMALIZED_SHA),
    )
    connection.execute(
        """INSERT INTO extraction_snapshot_body(
             snapshot_id,artifact_id,content_md,content_blob_hash,
             content_size_bytes,content_sha256,created_at
           ) VALUES (?,?,'body',NULL,4,?,301)""",
        (extraction_id, artifact_id, raw_sha256),
    )
    connection.execute(
        """INSERT INTO knowledge_item(
             id,space_id,lineage_group_id,lifecycle_status,curation_status,
             current_revision_id,last_curation_transition_id,
             latest_deactivation_episode_id,redirect_item_id,created_by,
             updated_by,created_at,updated_at,row_version
           ) VALUES (?,?,NULL,'active','provisional',?,NULL,NULL,NULL,?,?,302,302,1)""",
        (item_id, space_id, current_revision_id, owner, owner),
    )
    if base_revision_id is not None:
        base_body = "prior"
        base_raw_sha = hashlib.sha256(base_body.encode("utf-8")).hexdigest()
        connection.execute(
            """INSERT INTO knowledge_revision(
                 id,item_id,revision_no,parent_revision_id,base_revision_id,title,
                 raw_content_sha256,normalized_content_sha256,content_size_bytes,
                 normalizer_version,metadata_json,change_kind,change_reason,
                 created_by,created_at
               ) VALUES (?,?,1,NULL,NULL,'Prior',?,?,5,'markdown_lf_v1','{}',
                 'initial','initial revision',?,301)""",
            (base_revision_id, item_id, base_raw_sha, base_raw_sha, owner),
        )
        connection.execute(
            """INSERT INTO knowledge_revision_body(
                 revision_id,item_id,content_md,content_blob_hash,
                 content_size_bytes,content_sha256,created_at
               ) VALUES (?,?,'prior',NULL,5,?,301)""",
            (base_revision_id, item_id, base_raw_sha),
        )
    connection.execute(
        """INSERT INTO knowledge_revision(
             id,item_id,revision_no,parent_revision_id,base_revision_id,title,
             raw_content_sha256,normalized_content_sha256,content_size_bytes,
             normalizer_version,metadata_json,change_kind,change_reason,
             created_by,created_at
           ) VALUES (?,?,?,?,?,'Imported',?,?,4,'markdown_lf_v1','{}',
             ?,'legacy migration',?,302)""",
        (
            mapped_revision_id,
            item_id,
            mapped_revision_no,
            base_revision_id,
            base_revision_id,
            raw_sha256,
            _NORMALIZED_SHA,
            mapped_change_kind,
            owner,
        ),
    )
    connection.execute(
        """INSERT INTO knowledge_revision_body(
             revision_id,item_id,content_md,content_blob_hash,
             content_size_bytes,content_sha256,created_at
           ) VALUES (?,?,'body',NULL,4,?,302)""",
        (mapped_revision_id, item_id, raw_sha256),
    )
    connection.execute(
        """INSERT INTO knowledge_revision(
             id,item_id,revision_no,parent_revision_id,base_revision_id,title,
             raw_content_sha256,normalized_content_sha256,content_size_bytes,
             normalizer_version,metadata_json,change_kind,change_reason,
             created_by,created_at
           ) VALUES (?,?,?,?,?,'Edited later',?,?,5,'markdown_lf_v1','{}',
             'edit','later edit',?,350)""",
        (
            current_revision_id,
            item_id,
            mapped_revision_no + 1,
            mapped_revision_id,
            mapped_revision_id,
            current_raw_sha,
            current_normalized_sha,
            owner,
        ),
    )
    connection.execute(
        """INSERT INTO knowledge_revision_body(
             revision_id,item_id,content_md,content_blob_hash,
             content_size_bytes,content_sha256,created_at
           ) VALUES (?,?,'later',NULL,5,?,350)""",
        (current_revision_id, item_id, current_raw_sha),
    )
    connection.execute(
        """INSERT INTO item_source(
             id,item_id,revision_id,artifact_id,extraction_snapshot_id,role,
             created_by,created_at
           ) VALUES (?,?,?,?,?,'migrated',?,302)""",
        (
            item_source_id,
            item_id,
            mapped_revision_id,
            artifact_id,
            extraction_id,
            owner,
        ),
    )
    return _item_target(
        owner=owner,
        space_id=space_id,
        artifact_id=artifact_id,
        artifact_blob_id=artifact_blob_id,
        extraction_snapshot_id=extraction_id,
        item_id=item_id,
        revision_id=mapped_revision_id,
        item_source_id=item_source_id,
        record_id=record_id,
        path_key=path_key,
        blob_sha256=raw_sha256,
        result_disposition=result_disposition,
    )


def test_source_locator_has_only_stable_source_fields_and_distinguishes_them() -> None:
    signature = inspect.signature(migration_source_locator)
    assert tuple(signature.parameters) == (
        "root_id",
        "path_key",
        "record_kind",
        "source_ordinal",
    )
    assert "record_id" not in signature.parameters
    assert "category" not in signature.parameters
    assert "owner" not in signature.parameters

    path_key = "knowledge/alice/wiki/sources/one.md"
    path_digest = _sha(path_key)
    baseline = migration_source_locator(
        root_id="knowledge-root",
        path_key=path_key,
        record_kind="physical_file",
        source_ordinal=0,
    )
    assert baseline == f"p2v1/knowledge-root/{path_digest}/physical_file/0"
    variants = {
        baseline,
        migration_source_locator(
            root_id="other-root",
            path_key=path_key,
            record_kind="physical_file",
            source_ordinal=0,
        ),
        migration_source_locator(
            root_id="knowledge-root",
            path_key=path_key + ".bak",
            record_kind="physical_file",
            source_ordinal=0,
        ),
        migration_source_locator(
            root_id="knowledge-root",
            path_key=path_key,
            record_kind="logical_record",
            source_ordinal=1,
        ),
        migration_source_locator(
            root_id="knowledge-root",
            path_key=path_key,
            record_kind="logical_record",
            source_ordinal=2,
        ),
    }
    assert len(variants) == 5
    assert path_key not in baseline
    assert "rec_" not in baseline
    assert "alice" not in baseline
    assert "knowledge.wiki_sources" not in baseline

    for unsafe in (
        "Knowledge/Alice.md",
        "knowledge/cafe\u0301.md",
        "knowledge\\alice.md",
        "../knowledge/alice.md",
        "/knowledge/alice.md",
        "c:/knowledge/alice.md",
    ):
        with pytest.raises(ValueError, match="canonical relative inventory key"):
            migration_source_locator(
                root_id="knowledge-root",
                path_key=unsafe,
                record_kind="physical_file",
                source_ordinal=0,
            )


def test_deterministic_run_and_mapping_ids_are_frozen_and_input_bound() -> None:
    assert migration_run_id_for_plan(_PLAN_A) == (
        "c05a5a16-2408-5ae0-8a38-4fe0403c4fc4"
    )
    assert migration_mapping_id_for_record(_PLAN_A, _RECORD_1) == (
        "29b72189-c97a-563b-b349-c850d467e1e4"
    )
    assert migration_run_id_for_plan(_PLAN_A) == migration_run_id_for_plan(_PLAN_A)
    assert migration_mapping_id_for_record(
        _PLAN_A, _RECORD_1
    ) == migration_mapping_id_for_record(_PLAN_A, _RECORD_1)
    assert migration_run_id_for_plan(_PLAN_A) != migration_run_id_for_plan(_PLAN_B)
    assert migration_mapping_id_for_record(
        _PLAN_A, _RECORD_1
    ) != migration_mapping_id_for_record(_PLAN_A, _record_id(2))
    assert migration_mapping_id_for_record(
        _PLAN_A, _RECORD_1
    ) != migration_mapping_id_for_record(_PLAN_B, _RECORD_1)


def test_put_imported_requires_a_caller_owned_transaction(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = _repository()
    mapping_id, run_id, key, target = _user_mapping(
        owner="owner", space_id=spaces["owner"]
    )
    connection = factory.open()
    try:
        with pytest.raises(KnowledgeCoreTransactionError):
            repository.put_imported(
                connection,
                mapping_id=mapping_id,
                migration_run_id=run_id,
                key=key,
                target=target,
                now_ms=500,
            )
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.scenario("MIG-004")
def test_mig_004_user_account_insert_and_exact_replay_are_stable(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = _repository()
    mapping_id, run_id, key, target = _user_mapping(
        owner="owner", space_id=spaces["owner"], result_disposition="existing_exact"
    )
    connection = factory.open()
    try:
        with transaction(connection):
            first = repository.put_imported(
                connection,
                mapping_id=mapping_id,
                migration_run_id=run_id,
                key=key,
                target=target,
                now_ms=500,
            )
        assert first.inserted is True
        assert first.record.created_at_ms == 500
        assert first.record.updated_at_ms == 500
        assert first.record.id == mapping_id
        assert first.record.migration_run_id == run_id
        assert first.record.key == key
        assert first.record.details == target.details
        assert json.loads(first.record.details_json) == target.details.to_mapping()
        assert repository.get_by_id(connection, mapping_id) == first.record
        assert repository.get_by_key(connection, key) == first.record
        assert repository.require_by_key(connection, key) == first.record

        with transaction(connection):
            retry = repository.put_imported(
                connection,
                mapping_id=mapping_id,
                migration_run_id=run_id,
                key=key,
                target=target,
                now_ms=999,
            )
        assert retry.inserted is False
        assert retry.record == first.record
        assert retry.record.created_at_ms == 500
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.parametrize(
    "change",
    (
        "mapping_id",
        "migration_run_id",
        "plan_sha256",
        "inventory_record_id",
        "inventory_record_sha256",
        "target_owner",
        "target_space",
    ),
)
def test_same_source_key_rejects_changed_review_or_target_binding(
    isolated_knowledge_layout,
    change: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner", "other")
    repository = _repository()
    mapping_id, run_id, key, target = _user_mapping(
        owner="owner", space_id=spaces["owner"]
    )
    connection = factory.open()
    try:
        with transaction(connection):
            repository.put_imported(
                connection,
                mapping_id=mapping_id,
                migration_run_id=run_id,
                key=key,
                target=target,
                now_ms=500,
            )

        changed_mapping_id = mapping_id
        changed_run_id = run_id
        changed_target = target
        if change == "mapping_id":
            changed_mapping_id = _uuid("conflicting-mapping-id")
        elif change == "migration_run_id":
            changed_run_id = _uuid("conflicting-run-id")
        elif change == "plan_sha256":
            changed_target = replace(
                target, details=replace(target.details, plan_sha256=_PLAN_B)
            )
        elif change == "inventory_record_id":
            changed_target = replace(
                target,
                details=replace(
                    target.details, inventory_record_id=_record_id(99)
                ),
            )
        elif change == "inventory_record_sha256":
            changed_target = replace(
                target,
                details=replace(
                    target.details, inventory_record_sha256="9" * 64
                ),
            )
        elif change == "target_owner":
            changed_target = replace(target, resolved_owner_user_id="other")
        elif change == "target_space":
            changed_target = replace(
                target,
                details=replace(
                    target.details, target_space_id=spaces["other"]
                ),
            )
        else:  # pragma: no cover - the parameter list is closed above.
            raise AssertionError(change)

        with pytest.raises(LegacyMigrationConflictError):
            with transaction(connection):
                repository.put_imported(
                    connection,
                    mapping_id=changed_mapping_id,
                    migration_run_id=changed_run_id,
                    key=key,
                    target=changed_target,
                    now_ms=600,
                )
        assert repository.require_by_key(connection, key).id == mapping_id
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("mapping_change", "expected_message"),
    (
        ("locator", "canonical reviewed path key"),
        ("category", "deterministic IDs"),
        ("mapping_id", "deterministic IDs"),
        ("run_id", "deterministic IDs"),
        ("plan", "deterministic IDs"),
        ("record", "deterministic IDs"),
    ),
)
def test_new_mapping_rejects_requested_key_and_deterministic_id_mismatches(
    isolated_knowledge_layout,
    mapping_change: str,
    expected_message: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = _repository()
    mapping_id, run_id, key, target = _user_mapping(
        owner="owner", space_id=spaces["owner"]
    )
    if mapping_change == "locator":
        key = replace(key, normalized_path=key.normalized_path + "-changed")
    elif mapping_change == "category":
        key = replace(key, legacy_kind="knowledge.wiki_sources")
    elif mapping_change == "mapping_id":
        mapping_id = _uuid("wrong-request-mapping")
    elif mapping_change == "run_id":
        run_id = _uuid("wrong-request-run")
    elif mapping_change == "plan":
        target = replace(
            target, details=replace(target.details, plan_sha256=_PLAN_B)
        )
    elif mapping_change == "record":
        target = replace(
            target,
            details=replace(target.details, inventory_record_id=_record_id(88)),
        )
    connection = factory.open()
    try:
        with pytest.raises(ValueError, match=expected_message):
            with transaction(connection):
                repository.put_imported(
                    connection,
                    mapping_id=mapping_id,
                    migration_run_id=run_id,
                    key=key,
                    target=target,
                    now_ms=500,
                )
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_locator_wide_collision_rejects_changed_source_sha(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = _repository()
    mapping_id, run_id, key, target = _user_mapping(
        owner="owner", space_id=spaces["owner"]
    )
    connection = factory.open()
    try:
        with transaction(connection):
            repository.put_imported(
                connection,
                mapping_id=mapping_id,
                migration_run_id=run_id,
                key=key,
                target=target,
                now_ms=500,
            )
        with pytest.raises(LegacyMigrationConflictError):
            with transaction(connection):
                repository.put_imported(
                    connection,
                    mapping_id=mapping_id,
                    migration_run_id=run_id,
                    key=replace(key, source_sha256="8" * 64),
                    target=target,
                    now_ms=600,
                )
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_same_mapping_id_cannot_bind_a_different_source_locator(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = _repository()
    mapping_id, run_id, key, target = _user_mapping(
        owner="owner", space_id=spaces["owner"]
    )
    connection = factory.open()
    try:
        with transaction(connection):
            repository.put_imported(
                connection,
                mapping_id=mapping_id,
                migration_run_id=run_id,
                key=key,
                target=target,
                now_ms=500,
            )
        changed_path = "users.json#different-locator"
        changed_details = replace(
            target.details,
            path_key_sha256=_sha(changed_path),
        )
        changed_key = replace(
            key,
            normalized_path=migration_source_locator(
                root_id=changed_details.root_id,
                path_key=changed_path,
                record_kind=changed_details.record_kind,
                source_ordinal=changed_details.source_ordinal,
            ),
        )
        with pytest.raises(LegacyMigrationConflictError):
            with transaction(connection):
                repository.put_imported(
                    connection,
                    mapping_id=mapping_id,
                    migration_run_id=run_id,
                    source_path_key=changed_path,
                    key=changed_key,
                    target=replace(target, details=changed_details),
                    now_ms=600,
                )
        assert repository.get_by_key(connection, changed_key) is None
    finally:
        connection.close()


def test_list_for_run_is_ordered_and_does_not_mix_plan_runs(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = _repository()
    connection = factory.open()
    try:
        specifications = (
            (_PLAN_A, _record_id(31), "users.json#z", "7" * 64, 1),
            (_PLAN_B, _record_id(32), "users.json#other-plan", "6" * 64, 1),
            (_PLAN_A, _record_id(33), "users.json#a", "5" * 64, 2),
        )
        inserted = []
        for index, (plan, record_id, path, source_sha, ordinal) in enumerate(
            specifications
        ):
            with transaction(connection):
                result = _put_user(
                    repository,
                    connection,
                    owner="owner",
                    space_id=spaces["owner"],
                    now_ms=700 + index,
                    plan_sha256=plan,
                    record_id=record_id,
                    path_key=path,
                    source_sha256=source_sha,
                    source_ordinal=ordinal,
                )
            inserted.append(result.record)

        plan_a_records = repository.list_for_run(
            connection, migration_run_id_for_plan(_PLAN_A)
        )
        expected = tuple(
            sorted(
                (record for record in inserted if record.details.plan_sha256 == _PLAN_A),
                key=lambda record: (
                    record.key.normalized_path,
                    record.key.source_sha256,
                    record.id,
                ),
            )
        )
        assert plan_a_records == expected
        assert all(record.details.plan_sha256 == _PLAN_A for record in plan_a_records)
        assert repository.list_for_run(
            connection, migration_run_id_for_plan(_PLAN_B)
        ) == (inserted[1],)
    finally:
        connection.close()


@pytest.mark.parametrize("tamper", ("details", "updated_at", "target"))
def test_stored_row_tampering_is_rejected_by_v3_history_guard(
    isolated_knowledge_layout,
    tamper: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = _repository()
    mapping_id, _, key, _ = _user_mapping(
        owner="owner", space_id=spaces["owner"]
    )
    connection = factory.open()
    try:
        with transaction(connection):
            _put_user(
                repository,
                connection,
                owner="owner",
                space_id=spaces["owner"],
                now_ms=500,
            )
        with pytest.raises(sqlite3.IntegrityError, match="LEGACY_MIGRATION_MAP_IMMUTABLE"):
            with transaction(connection):
                if tamper == "details":
                    connection.execute(
                        "UPDATE legacy_migration_map SET details_json=details_json||' '",
                    )
                elif tamper == "updated_at":
                    connection.execute(
                        "UPDATE legacy_migration_map SET updated_at=updated_at+1",
                    )
                else:
                    connection.execute(
                        "UPDATE legacy_migration_map SET artifact_id=?",
                        (_uuid("tamper-artifact"),),
                    )
        assert repository.get_by_id(connection, mapping_id) is not None
        assert repository.get_by_key(connection, key) is not None
    finally:
        connection.close()


def test_verify_target_integrity_rejects_fabricated_record_and_missing_targets(
    isolated_knowledge_layout,
) -> None:
    # Missing-target corruption is intentionally fabricated by deleting a
    # personal space. v8 forbids that through profile FK/delete guards, so this
    # historical verifier canary remains pinned to the frozen v7 catalog.
    factory = _factory(isolated_knowledge_layout, _V7_CATALOG)
    spaces = _seed_personal_owners(factory, "owner", "other")
    repository = _repository()
    connection = factory.open()
    try:
        with transaction(connection):
            inserted = _put_user(
                repository,
                connection,
                owner="owner",
                space_id=spaces["owner"],
                now_ms=500,
            )
        record = inserted.record

        with pytest.raises(LegacyMigrationIntegrityError, match="exact durable mapping"):
            repository.verify_target_integrity(
                connection,
                replace(record, resolved_owner_user_id="missing-user"),
            )

        with transaction(connection):
            connection.execute(
                "DELETE FROM knowledge_space WHERE id=?", (spaces["owner"],)
            )
        with pytest.raises(LegacyMigrationIntegrityError, match="personal space"):
            repository.verify_target_integrity(connection, record)

        with transaction(connection):
            connection.execute(
                """INSERT INTO knowledge_space(
                     id,space_kind,owner_user_id,owner_org_unit_id,status,
                     write_enabled,generation,created_at,updated_at,row_version
                   ) VALUES (?,'personal','owner',NULL,'active',0,0,700,700,1)""",
                (spaces["owner"],),
            )
            connection.execute(
                "DELETE FROM knowledge_space WHERE id=?", (spaces["other"],)
            )
            connection.execute(
                "DELETE FROM app_user WHERE id='other'"
            )
        assert repository.verify_target_integrity(connection, record) == record
    finally:
        connection.close()


def test_personal_item_mapping_accepts_exact_historical_revision_and_replay(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = _repository()
    connection = factory.open()
    try:
        with transaction(connection):
            target = _seed_historical_personal_item(
                connection,
                owner="owner",
                space_id=spaces["owner"],
            )
            details = target.details
            key = LegacyMigrationKey(
                legacy_kind=details.category,
                normalized_path=(
                    f"p2v1/{details.root_id}/{details.path_key_sha256}/"
                    f"{details.record_kind}/{details.source_ordinal}"
                ),
        source_sha256=details.blob_sha256 or "",
            )
            mapping_id = migration_mapping_id_for_record(
                details.plan_sha256, details.inventory_record_id
            )
            run_id = migration_run_id_for_plan(details.plan_sha256)
            first = repository.put_imported(
                connection,
                mapping_id=mapping_id,
                migration_run_id=run_id,
                key=key,
                target=target,
                now_ms=500,
            )
        assert first.inserted is True
        item = connection.execute(
            """SELECT lifecycle_status,curation_status,current_revision_id
               FROM knowledge_item WHERE id=?""",
            (target.item_id,),
        ).fetchone()
        assert tuple(item) == (
            "active",
            "provisional",
            _uuid("revision:current"),
        )
        assert first.record.revision_id == _uuid("revision:mapped")
        assert repository.verify_target_integrity(connection, first.record) == first.record

        with transaction(connection):
            replay = repository.put_imported(
                connection,
                mapping_id=mapping_id,
                migration_run_id=run_id,
                key=key,
                target=target,
                now_ms=999,
            )
        assert replay.inserted is False
        assert replay.record == first.record
    finally:
        connection.close()


def test_locator_wide_collision_rejects_changed_item_category(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = _repository()
    connection = factory.open()
    try:
        with transaction(connection):
            target = _seed_historical_personal_item(
                connection,
                owner="owner",
                space_id=spaces["owner"],
            )
            details = target.details
            key = LegacyMigrationKey(
                legacy_kind=details.category,
                normalized_path=(
                    f"p2v1/{details.root_id}/{details.path_key_sha256}/"
                    f"{details.record_kind}/{details.source_ordinal}"
                ),
        source_sha256=details.blob_sha256 or "",
            )
            mapping_id = migration_mapping_id_for_record(
                details.plan_sha256, details.inventory_record_id
            )
            run_id = migration_run_id_for_plan(details.plan_sha256)
            repository.put_imported(
                connection,
                mapping_id=mapping_id,
                migration_run_id=run_id,
                key=key,
                target=target,
                now_ms=500,
            )

        changed_details = replace(
            target.details,
            category="knowledge.legacy_raw",
        )
        changed_target = replace(target, details=changed_details)
        changed_key = replace(key, legacy_kind=changed_details.category)
        with pytest.raises(LegacyMigrationConflictError):
            with transaction(connection):
                repository.put_imported(
                    connection,
                    mapping_id=mapping_id,
                    migration_run_id=run_id,
                    key=changed_key,
                    target=changed_target,
                    now_ms=600,
                )
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_artifact",
        "missing_item",
        "missing_revision",
        "edge_target",
        "missing_blob_sha",
        "missing_artifact_blob",
        "missing_extraction",
        "missing_item_source",
        "missing_normalized_sha",
        "missing_normalizer",
        "existing_exact_disposition",
    ),
)
def test_personal_item_target_shape_rejects_missing_or_forbidden_fields(
    isolated_knowledge_layout,
    mutation: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = _repository()
    target = _item_target(owner="owner", space_id=spaces["owner"])
    if mutation == "missing_artifact":
        target = replace(target, artifact_id=None)
    elif mutation == "missing_item":
        target = replace(target, item_id=None)
    elif mutation == "missing_revision":
        target = replace(target, revision_id=None)
    elif mutation == "edge_target":
        target = replace(target, edge_id=_uuid("forbidden-edge"))
    elif mutation == "missing_blob_sha":
        target = replace(target, details=replace(target.details, blob_sha256=None))
    elif mutation == "missing_artifact_blob":
        target = replace(
            target, details=replace(target.details, artifact_blob_id=None)
        )
    elif mutation == "missing_extraction":
        target = replace(
            target, details=replace(target.details, extraction_snapshot_id=None)
        )
    elif mutation == "missing_item_source":
        target = replace(target, details=replace(target.details, item_source_id=None))
    elif mutation == "missing_normalized_sha":
        target = replace(
            target, details=replace(target.details, normalized_content_sha256=None)
        )
    elif mutation == "missing_normalizer":
        target = replace(
            target, details=replace(target.details, normalizer_version=None)
        )
    elif mutation == "existing_exact_disposition":
        target = replace(
            target,
            details=replace(target.details, result_disposition="existing_exact"),
        )
    details = target.details
    key = LegacyMigrationKey(
        legacy_kind=details.category,
        normalized_path=(
            f"p2v1/{details.root_id}/{details.path_key_sha256}/"
            f"{details.record_kind}/{details.source_ordinal}"
        ),
        source_sha256="2" * 64,
    )
    connection = factory.open()
    try:
        with pytest.raises(ValueError):
            with transaction(connection):
                repository.put_imported(
                    connection,
                    mapping_id=migration_mapping_id_for_record(
                        details.plan_sha256, details.inventory_record_id
                    ),
                    migration_run_id=migration_run_id_for_plan(details.plan_sha256),
                    key=key,
                    target=target,
                    now_ms=500,
                )
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_artifact",
        "item_target",
        "revision_target",
        "missing_blob_sha",
        "missing_artifact_blob",
        "extraction_evidence",
        "item_source_evidence",
        "normalized_evidence",
        "normalizer_evidence",
        "no_op_disposition",
    ),
)
def test_personal_asset_target_shape_rejects_missing_or_forbidden_fields(
    isolated_knowledge_layout,
    mutation: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = _repository()
    path_key = "wiki/assets/image.png"
    details = LegacyMigrationDetails(
        plan_sha256=_PLAN_A,
        inventory_manifest_sha256=_MANIFEST_SHA,
        root_binding_sha256=_ROOT_BINDING_SHA,
        inventory_record_id=_record_id(70),
        inventory_record_sha256=_RECORD_SHA,
        root_id=_ROOT_ID,
        path_key_sha256=_sha(path_key),
        category="knowledge.wiki_assets",
        record_kind="physical_file",
        source_ordinal=0,
        import_mode="personal_asset",
        result_disposition="created",
        target_space_id=spaces["owner"],
        blob_sha256="7" * 64,
        artifact_blob_id=_uuid("asset-blob"),
        source_size_bytes=4,
        source_media_type="image/png",
        source_record_sha256="7" * 64,
    )
    target = LegacyMigrationTarget(
        resolved_owner_user_id="owner",
        artifact_id=_uuid("asset-artifact"),
        item_id=None,
        revision_id=None,
        edge_id=None,
        details=details,
        source_path_key=path_key,
    )
    if mutation == "missing_artifact":
        target = replace(target, artifact_id=None)
    elif mutation == "item_target":
        target = replace(target, item_id=_uuid("forbidden-asset-item"))
    elif mutation == "revision_target":
        target = replace(target, revision_id=_uuid("forbidden-asset-revision"))
    elif mutation == "missing_blob_sha":
        target = replace(target, details=replace(details, blob_sha256=None))
    elif mutation == "missing_artifact_blob":
        target = replace(target, details=replace(details, artifact_blob_id=None))
    elif mutation == "extraction_evidence":
        target = replace(
            target,
            details=replace(details, extraction_snapshot_id=_uuid("asset-extraction")),
        )
    elif mutation == "item_source_evidence":
        target = replace(
            target,
            details=replace(details, item_source_id=_uuid("asset-item-source")),
        )
    elif mutation == "normalized_evidence":
        target = replace(
            target,
            details=replace(details, normalized_content_sha256=_NORMALIZED_SHA),
        )
    elif mutation == "normalizer_evidence":
        target = replace(
            target,
            details=replace(details, normalizer_version="legacy-md-v1"),
        )
    elif mutation == "no_op_disposition":
        target = replace(
            target,
            details=replace(details, result_disposition="no_op"),
        )
    key = LegacyMigrationKey(
        legacy_kind=details.category,
        normalized_path=migration_source_locator(
            root_id=details.root_id,
            path_key=path_key,
            record_kind=details.record_kind,
            source_ordinal=details.source_ordinal,
        ),
        source_sha256="7" * 64,
    )
    connection = factory.open()
    try:
        with pytest.raises(ValueError):
            with transaction(connection):
                repository.put_imported(
                    connection,
                    mapping_id=migration_mapping_id_for_record(
                        details.plan_sha256, details.inventory_record_id
                    ),
                    migration_run_id=migration_run_id_for_plan(details.plan_sha256),
                    key=key,
                    target=target,
                    now_ms=500,
                )
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize("mode", ("personal_asset", "personal_item"))
@pytest.mark.parametrize("evidence_change", ("missing", "raw_sha256", "size_bytes"))
def test_physical_import_requires_exact_pretransaction_evidence_before_mapping(
    isolated_knowledge_layout,
    mode: str,
    evidence_change: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = LegacyMigrationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            target = (
                _seed_personal_asset(
                    connection, owner="owner", space_id=spaces["owner"]
                )
                if mode == "personal_asset"
                else _seed_historical_personal_item(
                    connection, owner="owner", space_id=spaces["owner"]
                )
            )
            mapping_id, run_id, key = _mapping_for_target(target)
            evidence = _verified_blob_evidence(target)
            if evidence_change == "missing":
                evidence = None
            elif evidence_change == "raw_sha256":
                evidence = replace(evidence, raw_sha256="9" * 64)
            else:
                evidence = replace(evidence, size_bytes=evidence.size_bytes + 1)
            with pytest.raises(
                LegacyMigrationIntegrityError, match="pre-transaction"
            ):
                repository.put_imported(
                    connection,
                    mapping_id=mapping_id,
                    migration_run_id=run_id,
                    key=key,
                    target=target,
                    verified_blob_evidence=evidence,
                    now_ms=500,
                )
            UserRepository().import_legacy(connection, _legacy_user("unrelated"))

        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 0
        assert UserRepository().require(connection, "unrelated").id == "unrelated"
    finally:
        connection.close()


@pytest.mark.parametrize("mode", ("personal_asset", "personal_item"))
def test_physical_import_accepts_exact_pretransaction_blob_evidence(
    isolated_knowledge_layout,
    mode: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = LegacyMigrationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            target = (
                _seed_personal_asset(
                    connection, owner="owner", space_id=spaces["owner"]
                )
                if mode == "personal_asset"
                else _seed_historical_personal_item(
                    connection, owner="owner", space_id=spaces["owner"]
                )
            )
            mapping_id, run_id, key = _mapping_for_target(target)
            evidence = _verified_blob_evidence(target)
            result = repository.put_imported(
                connection,
                mapping_id=mapping_id,
                migration_run_id=run_id,
                key=key,
                target=target,
                verified_blob_evidence=evidence,
                now_ms=500,
            )

        assert result.inserted is True
        assert result.record.details.source_record_sha256 == evidence.raw_sha256
    finally:
        connection.close()


@pytest.mark.parametrize("mode", ("personal_asset", "personal_item"))
def test_pretransaction_evidence_binds_media_and_markdown_normalization(
    isolated_knowledge_layout,
    mode: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = LegacyMigrationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            target = (
                _seed_personal_asset(
                    connection, owner="owner", space_id=spaces["owner"]
                )
                if mode == "personal_asset"
                else _seed_historical_personal_item(
                    connection, owner="owner", space_id=spaces["owner"]
                )
            )
            mapping_id, run_id, key = _mapping_for_target(target)
            evidence = _verified_blob_evidence(target)
            evidence = (
                replace(evidence, media_type="application/octet-stream")
                if mode == "personal_asset"
                else replace(evidence, normalized_content_sha256="9" * 64)
            )
            with pytest.raises(LegacyMigrationIntegrityError, match="reviewed source"):
                repository.put_imported(
                    connection,
                    mapping_id=mapping_id,
                    migration_run_id=run_id,
                    key=key,
                    target=target,
                    verified_blob_evidence=evidence,
                    now_ms=500,
                )
            UserRepository().import_legacy(connection, _legacy_user("unrelated"))

        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 0
        assert UserRepository().require(connection, "unrelated").id == "unrelated"
    finally:
        connection.close()


def test_preinsert_target_integrity_failure_can_be_caught_without_poisoning_outer_commit(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = _repository()
    connection = factory.open()
    try:
        with transaction(connection):
            target = _seed_personal_asset(
                connection, owner="owner", space_id=spaces["owner"]
            )
            target = replace(target, artifact_id=_uuid("missing-target-artifact"))
            mapping_id, run_id, key = _mapping_for_target(target)
            with pytest.raises(LegacyMigrationIntegrityError):
                repository.put_imported(
                    connection,
                    mapping_id=mapping_id,
                    migration_run_id=run_id,
                    key=key,
                    target=target,
                    now_ms=500,
                )
            UserRepository().import_legacy(connection, _legacy_user("unrelated"))

        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 0
        assert UserRepository().require(connection, "unrelated").id == "unrelated"
    finally:
        connection.close()


@pytest.mark.parametrize(
    "source_path_key",
    (None, "Wiki/Sources/imported.md", "wiki\\sources\\imported.md"),
)
def test_put_imported_requires_the_canonical_reviewed_source_path_key(
    isolated_knowledge_layout,
    source_path_key: str | None,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = _repository()
    mapping_id, run_id, key, target = _user_mapping(
        owner="owner", space_id=spaces["owner"]
    )
    target = replace(target, source_path_key=None)
    connection = factory.open()
    try:
        with pytest.raises(ValueError):
            with transaction(connection):
                repository.put_imported(
                    connection,
                    mapping_id=mapping_id,
                    migration_run_id=run_id,
                    source_path_key=source_path_key,
                    key=key,
                    target=target,
                    now_ms=500,
                )
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_full_hash_locator_cannot_bypass_canonical_source_path_validation(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = _repository()
    mapping_id, run_id, key, target = _user_mapping(
        owner="owner", space_id=spaces["owner"]
    )
    target = replace(target, source_path_key=None)
    connection = factory.open()
    try:
        with pytest.raises(ValueError, match="canonical reviewed path key"):
            with transaction(connection):
                repository.put_imported(
                    connection,
                    mapping_id=mapping_id,
                    migration_run_id=run_id,
                    source_path_key=key.normalized_path,
                    key=key,
                    target=target,
                    now_ms=500,
                )
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_user_account_initial_projection_hash_is_frozen_and_field_complete() -> None:
    legacy = _legacy_user("owner")
    baseline = legacy_user_account_projection_sha256(legacy)
    assert baseline == "4fded1153dd1848c48a54a3c3377d12a13fe7d2e64e1a14d5e8f983810dc4410"

    canonical = UserRecord(
        id=legacy.id,
        display_name=legacy.display_name,
        password_hash=legacy.password_hash,
        system_role=legacy.system_role,
        account_status=legacy.account_status,
        current_part_id=legacy.current_part_id,
        scope_epoch=legacy.scope_epoch,
        must_change_password=legacy.must_change_password,
        password_changed_at_ms=legacy.password_changed_at_ms,
        created_at_ms=legacy.created_at_ms,
        updated_at_ms=legacy.updated_at_ms,
        disabled_at_ms=legacy.disabled_at_ms,
        row_version=1,
    )
    assert legacy_user_account_projection_sha256(canonical) == baseline

    variants = (
        replace(legacy, id="other"),
        replace(legacy, display_name="Other"),
        replace(legacy, password_hash="scrypt:p2.3:changed"),
        replace(legacy, system_role="admin"),
        replace(legacy, account_status="disabled"),
        replace(legacy, current_part_id=_uuid("projection-part")),
        replace(legacy, scope_epoch=2),
        replace(legacy, must_change_password=True),
        replace(legacy, password_changed_at_ms=101),
        replace(legacy, created_at_ms=99),
        replace(legacy, updated_at_ms=101),
        replace(legacy, disabled_at_ms=101),
        replace(canonical, row_version=2),
    )
    variant_hashes = {
        legacy_user_account_projection_sha256(variant) for variant in variants
    }
    assert baseline not in variant_hashes
    assert len(variant_hashes) == len(variants)


def test_user_account_projection_mismatch_never_creates_a_mapping(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = _repository()
    mapping_id, run_id, key, target = _user_mapping(
        owner="owner",
        space_id=spaces["owner"],
        account_projection_sha256="9" * 64,
    )
    connection = factory.open()
    try:
        with pytest.raises(
            LegacyMigrationIntegrityError, match="reviewed import projection"
        ):
            with transaction(connection):
                repository.put_imported(
                    connection,
                    mapping_id=mapping_id,
                    migration_run_id=run_id,
                    key=key,
                    target=target,
                    now_ms=500,
                )
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize(
    "evidence_mismatch",
    (
        "assigned_part",
        "membership_id",
        "leader_assignment_id",
        "unexpected_membership",
        "unexpected_leader",
        "missing_membership",
        "missing_leader",
    ),
)
def test_user_account_membership_and_leader_projection_mismatch_never_maps(
    isolated_knowledge_layout,
    evidence_mismatch: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    legacy, space_id, part_id, membership_id, leader_id = (
        _seed_assigned_personal_owner(factory)
    )
    projection_sha = legacy_user_account_projection_sha256(legacy)
    assigned_part_id: str | None = part_id
    expected_membership_id: str | None = membership_id
    expected_leader_id: str | None = leader_id
    organization = OrganizationRepository()
    connection = factory.open()
    try:
        if evidence_mismatch == "assigned_part":
            assigned_part_id = _uuid("wrong-evidence-part")
        elif evidence_mismatch == "membership_id":
            expected_membership_id = _uuid("wrong-evidence-membership")
        elif evidence_mismatch == "leader_assignment_id":
            expected_leader_id = _uuid("wrong-evidence-leader")
        elif evidence_mismatch == "unexpected_membership":
            assigned_part_id = expected_membership_id = expected_leader_id = None
        elif evidence_mismatch == "unexpected_leader":
            expected_leader_id = None
        elif evidence_mismatch == "missing_membership":
            with transaction(connection):
                organization.close_part_leader(
                    connection,
                    part_id=part_id,
                    expected_assignment_id=leader_id,
                    closed_at_ms=600,
                )
                organization.close_current_membership(
                    connection,
                    user_id="owner",
                    expected_membership_id=membership_id,
                    closed_at_ms=601,
                )
        elif evidence_mismatch == "missing_leader":
            with transaction(connection):
                organization.close_part_leader(
                    connection,
                    part_id=part_id,
                    expected_assignment_id=leader_id,
                    closed_at_ms=600,
                )

        mapping_id, run_id, key, target = _user_mapping(
            owner="owner",
            space_id=space_id,
            account_projection_sha256=projection_sha,
            assigned_part_id=assigned_part_id,
            membership_id=expected_membership_id,
            leader_assignment_id=expected_leader_id,
        )
        with pytest.raises(LegacyMigrationIntegrityError):
            with transaction(connection):
                repository = _repository()
                repository.put_imported(
                    connection,
                    mapping_id=mapping_id,
                    migration_run_id=run_id,
                    key=key,
                    target=target,
                    now_ms=500,
                )
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_user_account_mapping_keeps_initial_projection_valid_after_later_mutation(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    legacy, space_id, part_id, membership_id, leader_id = (
        _seed_assigned_personal_owner(factory)
    )
    repository = _repository()
    organization = OrganizationRepository()
    mapping_id, run_id, key, target = _user_mapping(
        owner="owner",
        space_id=space_id,
        account_projection_sha256=legacy_user_account_projection_sha256(legacy),
        assigned_part_id=part_id,
        membership_id=membership_id,
        leader_assignment_id=leader_id,
    )
    connection = factory.open()
    try:
        with transaction(connection):
            first = repository.put_imported(
                connection,
                mapping_id=mapping_id,
                migration_run_id=run_id,
                key=key,
                target=target,
                now_ms=500,
            )
        with transaction(connection):
            organization.close_part_leader(
                connection,
                part_id=part_id,
                expected_assignment_id=leader_id,
                closed_at_ms=600,
            )
            organization.close_current_membership(
                connection,
                user_id="owner",
                expected_membership_id=membership_id,
                closed_at_ms=601,
            )
            organization.compare_and_swap_user_part(
                connection,
                user_id="owner",
                expected_current_part_id=part_id,
                target_part_id=None,
                expected_scope_epoch=legacy.scope_epoch,
                expected_row_version=1,
                updated_at_ms=602,
            )
            UserRepository().update_profile(
                connection,
                user_id="owner",
                expected_row_version=2,
                display_name="Owner Renamed Later",
                system_role="admin",
                updated_at_ms=603,
            )

        assert repository.verify_target_integrity(connection, first.record) == first.record
        with transaction(connection):
            replay = repository.put_imported(
                connection,
                mapping_id=mapping_id,
                migration_run_id=run_id,
                key=key,
                target=target,
                now_ms=999,
            )
        assert replay.inserted is False
        assert replay.record == first.record
        current = UserRepository().require(connection, "owner")
        assert (current.display_name, current.system_role, current.current_part_id) == (
            "Owner Renamed Later",
            "admin",
            None,
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("result_disposition", "mapped_change_kind", "accepted"),
    (
        ("created", "import", True),
        ("created", "initial", False),
        ("created", "edit", False),
        ("provenance_added", "initial", True),
        ("provenance_added", "edit", True),
        ("no_op", "initial", True),
        ("no_op", "edit", True),
    ),
)
def test_item_result_disposition_binds_created_vs_reused_revision_kind(
    isolated_knowledge_layout,
    result_disposition: str,
    mapped_change_kind: str,
    accepted: bool,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = _repository()
    connection = factory.open()
    try:
        with transaction(connection):
            target = _seed_historical_personal_item(
                connection,
                owner="owner",
                space_id=spaces["owner"],
                mapped_change_kind=mapped_change_kind,
                result_disposition=result_disposition,
            )
            mapping_id, run_id, key = _mapping_for_target(target)
            if accepted:
                result = repository.put_imported(
                    connection,
                    mapping_id=mapping_id,
                    migration_run_id=run_id,
                    key=key,
                    target=target,
                    now_ms=500,
                )
                assert result.inserted is True
            else:
                with pytest.raises(
                    LegacyMigrationIntegrityError, match="personal item"
                ):
                    repository.put_imported(
                        connection,
                        mapping_id=mapping_id,
                        migration_run_id=run_id,
                        key=key,
                        target=target,
                        now_ms=500,
                    )

        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == (1 if accepted else 0)
    finally:
        connection.close()


def test_child_binding_digest_is_frozen_and_order_independent() -> None:
    bindings = (
        (_uuid("child:a"), _record_id(101), "1" * 64),
        (_uuid("child:b"), _record_id(102), "2" * 64),
    )
    assert migration_child_bindings_sha256(bindings) == (
        "7d894b8a440676468aaefb911b62a5ec26e6ddff5b94d5ba8f8032231d80333b"
    )
    assert migration_child_bindings_sha256(reversed(bindings)) == (
        migration_child_bindings_sha256(bindings)
    )


def test_empty_users_source_container_closes_with_the_empty_dependency_digest(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = LegacyMigrationRepository()
    target = _source_container_target(child_bindings=())
    connection = factory.open()
    try:
        with transaction(connection):
            result = _put_container(repository, connection, target)

        assert result.inserted is True
        assert result.record.details.planned_import_child_count == 0
        assert result.record.details.imported_child_bindings_sha256 == (
            migration_child_bindings_sha256(())
        )
        assert repository.verify_target_integrity(
            connection,
            result.record,
            verified_blob_evidence=_container_evidence(target),
        ) == result.record
    finally:
        connection.close()


def test_users_source_container_closes_exact_children_and_replays_stably(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "alice", "charlie")
    repository = LegacyMigrationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            # Ordinal 2 represents a reviewed quarantined logical record.  It
            # deliberately has no canonical mapping and is not a dependency.
            charlie = _put_user_child(
                repository,
                connection,
                owner="charlie",
                space_id=spaces["charlie"],
                record_id=_record_id(103),
                source_ordinal=3,
                source_sha256="3" * 64,
            )
            alice = _put_user_child(
                repository,
                connection,
                owner="alice",
                space_id=spaces["alice"],
                record_id=_record_id(101),
                source_ordinal=1,
                source_sha256="1" * 64,
            )
            bindings = tuple(
                (
                    child.record.id,
                    child.record.details.inventory_record_id,
                    child.record.key.source_sha256,
                )
                for child in (alice, charlie)
            )
            target = _source_container_target(
                # The closure result cannot depend on insertion or caller order.
                child_bindings=tuple(reversed(bindings))
            )
            first = _put_container(repository, connection, target)

        assert first.inserted is True
        assert first.record.resolved_owner_user_id is None
        assert (
            first.record.artifact_id,
            first.record.item_id,
            first.record.revision_id,
            first.record.edge_id,
        ) == (None, None, None, None)
        assert first.record.details.planned_import_child_count == 2
        assert first.record.details.imported_child_bindings_sha256 == (
            migration_child_bindings_sha256(bindings)
        )
        assert repository.verify_target_integrity(
            connection,
            first.record,
            verified_blob_evidence=_container_evidence(target),
        ) == first.record

        with transaction(connection):
            replay = _put_container(repository, connection, target, now_ms=999)
        assert replay.inserted is False
        assert replay.record == first.record
    finally:
        connection.close()


@pytest.mark.parametrize(
    "forbidden_claim",
    (
        "owner",
        "target_space",
        "artifact",
        "item",
        "revision",
        "edge",
        "blob_sha256",
        "artifact_blob",
        "extraction",
        "item_source",
        "normalized_content",
        "account_projection",
        "assignment",
        "parent",
    ),
)
def test_users_source_container_rejects_personal_or_content_claims(
    isolated_knowledge_layout,
    forbidden_claim: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = LegacyMigrationRepository()
    target = _source_container_target(child_bindings=())
    details = target.details
    if forbidden_claim == "owner":
        target = replace(target, resolved_owner_user_id="owner")
    elif forbidden_claim == "target_space":
        details = replace(details, target_space_id=_uuid("container-space"))
    elif forbidden_claim == "artifact":
        target = replace(target, artifact_id=_uuid("container-artifact"))
    elif forbidden_claim == "item":
        target = replace(target, item_id=_uuid("container-item"))
    elif forbidden_claim == "revision":
        target = replace(target, revision_id=_uuid("container-revision"))
    elif forbidden_claim == "edge":
        target = replace(target, edge_id=_uuid("container-edge"))
    elif forbidden_claim == "blob_sha256":
        details = replace(details, blob_sha256=details.source_record_sha256)
    elif forbidden_claim == "artifact_blob":
        details = replace(details, artifact_blob_id=_uuid("container-blob"))
    elif forbidden_claim == "extraction":
        details = replace(
            details, extraction_snapshot_id=_uuid("container-extraction")
        )
    elif forbidden_claim == "item_source":
        details = replace(details, item_source_id=_uuid("container-item-source"))
    elif forbidden_claim == "normalized_content":
        details = replace(
            details,
            normalized_content_sha256="9" * 64,
            normalizer_version="markdown_lf_v1",
        )
    elif forbidden_claim == "account_projection":
        details = replace(
            details,
            account_projection_schema="legacy_user_import_v1",
            account_projection_sha256="9" * 64,
        )
    elif forbidden_claim == "assignment":
        details = replace(
            details,
            assigned_part_id=_uuid("container-part"),
            membership_id=_uuid("container-membership"),
        )
    else:
        details = replace(
            details, parent_inventory_record_id=_record_id(999)
        )
    target = replace(target, details=details)
    mapping_id, run_id, key = _mapping_for_target(target)
    connection = factory.open()
    try:
        with pytest.raises(ValueError):
            with transaction(connection):
                repository.put_imported(
                    connection,
                    mapping_id=mapping_id,
                    migration_run_id=run_id,
                    key=key,
                    target=target,
                    verified_blob_evidence=_container_evidence(target),
                    now_ms=500,
                )
        assert repository.get_by_id(connection, mapping_id) is None
    finally:
        connection.close()


@pytest.mark.parametrize(
    "shape_change",
    (
        "category",
        "record_kind",
        "disposition",
        "child_count",
        "child_digest",
    ),
)
def test_users_source_container_requires_the_frozen_dependency_shape(
    isolated_knowledge_layout,
    shape_change: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = LegacyMigrationRepository()
    target = _source_container_target(child_bindings=())
    if shape_change == "category":
        target = replace(
            target,
            details=replace(target.details, category="knowledge.wiki_sources"),
        )
    elif shape_change == "record_kind":
        target = replace(
            target,
            details=replace(
                target.details, record_kind="logical_record", source_ordinal=1
            ),
        )
    elif shape_change == "disposition":
        target = replace(
            target,
            details=replace(target.details, result_disposition="created"),
        )
    elif shape_change == "child_count":
        target = replace(
            target,
            details=replace(target.details, planned_import_child_count=None),
        )
    else:
        target = replace(
            target,
            details=replace(target.details, imported_child_bindings_sha256=None),
        )
    mapping_id, run_id, key = _mapping_for_target(target)
    connection = factory.open()
    try:
        with pytest.raises(ValueError):
            with transaction(connection):
                repository.put_imported(
                    connection,
                    mapping_id=mapping_id,
                    migration_run_id=run_id,
                    key=key,
                    target=target,
                    verified_blob_evidence=_container_evidence(target),
                    now_ms=500,
                )
        assert repository.get_by_id(connection, mapping_id) is None
    finally:
        connection.close()


@pytest.mark.parametrize(
    "evidence_change",
    ("missing", "raw_sha256", "size_bytes", "media_type", "normalized", "normalizer"),
)
def test_users_source_container_requires_exact_pretransaction_file_evidence(
    isolated_knowledge_layout,
    evidence_change: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    repository = LegacyMigrationRepository()
    target = _source_container_target(child_bindings=())
    mapping_id, run_id, key = _mapping_for_target(target)
    evidence = _container_evidence(target)
    if evidence_change == "missing":
        evidence = None
    elif evidence_change == "raw_sha256":
        evidence = replace(evidence, raw_sha256="9" * 64)
    elif evidence_change == "size_bytes":
        evidence = replace(evidence, size_bytes=evidence.size_bytes + 1)
    elif evidence_change == "media_type":
        evidence = replace(evidence, media_type="application/octet-stream")
    elif evidence_change == "normalized":
        evidence = replace(evidence, normalized_content_sha256="9" * 64)
    else:
        evidence = replace(evidence, normalizer_version="markdown_lf_v1")
    connection = factory.open()
    try:
        with pytest.raises(LegacyMigrationIntegrityError, match="pre-transaction"):
            with transaction(connection):
                repository.put_imported(
                    connection,
                    mapping_id=mapping_id,
                    migration_run_id=run_id,
                    key=key,
                    target=target,
                    verified_blob_evidence=evidence,
                    now_ms=500,
                )
        assert repository.get_by_id(connection, mapping_id) is None
    finally:
        connection.close()


def test_logical_user_account_requires_a_bound_parent_container_record(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "owner")
    repository = LegacyMigrationRepository()
    mapping_id, run_id, key, target = _user_mapping(
        owner="owner",
        space_id=spaces["owner"],
    )
    target = replace(
        target,
        details=replace(target.details, parent_inventory_record_id=None),
    )
    connection = factory.open()
    try:
        with pytest.raises(ValueError, match="projection evidence"):
            with transaction(connection):
                repository.put_imported(
                    connection,
                    mapping_id=mapping_id,
                    migration_run_id=run_id,
                    key=key,
                    target=target,
                    now_ms=500,
                )
        assert repository.get_by_id(connection, mapping_id) is None
    finally:
        connection.close()


@pytest.mark.parametrize(
    "dependency_failure",
    ("missing", "extra", "wrong_plan", "wrong_parent", "count", "digest"),
)
def test_users_source_container_rejects_inexact_imported_child_closure_atomically(
    isolated_knowledge_layout,
    dependency_failure: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "alice", "bob", "unrelated")
    repository = LegacyMigrationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            first = _put_user_child(
                repository,
                connection,
                owner="alice",
                space_id=spaces["alice"],
                record_id=_record_id(101),
                source_ordinal=1,
                source_sha256="1" * 64,
            )
            first_binding = (
                first.record.id,
                first.record.details.inventory_record_id,
                first.record.key.source_sha256,
            )
            bindings = [first_binding]

            if dependency_failure == "missing":
                missing_id, _, _, missing_target = _user_mapping(
                    owner="bob",
                    space_id=spaces["bob"],
                    record_id=_record_id(102),
                    path_key="users.json#1",
                    source_ordinal=2,
                    source_sha256="2" * 64,
                )
                bindings.append(
                    (
                        missing_id,
                        missing_target.details.inventory_record_id,
                        missing_target.details.source_record_sha256 or "",
                    )
                )
            else:
                child_plan = _PLAN_B if dependency_failure == "wrong_plan" else _PLAN_A
                child_parent = (
                    _record_id(999)
                    if dependency_failure == "wrong_parent"
                    else _USERS_CONTAINER_RECORD_ID
                )
                second = _put_user_child(
                    repository,
                    connection,
                    owner="bob",
                    space_id=spaces["bob"],
                    record_id=_record_id(102),
                    source_ordinal=2,
                    source_sha256="2" * 64,
                    plan_sha256=child_plan,
                    parent_inventory_record_id=child_parent,
                )
                second_binding = (
                    second.record.id,
                    second.record.details.inventory_record_id,
                    second.record.key.source_sha256,
                )
                if dependency_failure != "extra":
                    bindings.append(second_binding)

            target = _source_container_target(
                child_bindings=tuple(bindings),
                planned_import_child_count=(
                    1 if dependency_failure == "count" else None
                ),
                imported_child_bindings_sha256=(
                    "9" * 64 if dependency_failure == "digest" else None
                ),
            )
            container_id, _, _ = _mapping_for_target(target)
            with pytest.raises(LegacyMigrationIntegrityError):
                _put_container(repository, connection, target)

            # A caught closure failure must not poison or checkpoint the outer
            # caller-owned identity transaction.
            UserRepository().update_profile(
                connection,
                user_id="unrelated",
                expected_row_version=1,
                display_name="Still Commit This",
                system_role="user",
                updated_at_ms=700,
            )

        assert repository.get_by_id(connection, container_id) is None
        assert UserRepository().require(connection, "unrelated").display_name == (
            "Still Commit This"
        )
    finally:
        connection.close()


def test_users_source_container_rejects_late_children_and_remains_valid(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    spaces = _seed_personal_owners(factory, "alice", "bob")
    repository = LegacyMigrationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            first_child = _put_user_child(
                repository,
                connection,
                owner="alice",
                space_id=spaces["alice"],
                record_id=_record_id(101),
                source_ordinal=1,
                source_sha256="1" * 64,
            )
            binding = (
                first_child.record.id,
                first_child.record.details.inventory_record_id,
                first_child.record.key.source_sha256,
            )
            target = _source_container_target(child_bindings=(binding,))
            container = _put_container(repository, connection, target)

        with pytest.raises(LegacyMigrationConflictError, match="dependency-closed"):
            with transaction(connection):
                _put_user_child(
                    repository,
                    connection,
                    owner="bob",
                    space_id=spaces["bob"],
                    record_id=_record_id(102),
                    source_ordinal=2,
                    source_sha256="2" * 64,
                )

        assert repository.verify_target_integrity(
            connection,
            container.record,
            verified_blob_evidence=_container_evidence(target),
        ) == container.record
        with transaction(connection):
            assert (
                _put_container(repository, connection, target, now_ms=999)
                .inserted
                is False
            )
    finally:
        connection.close()


def test_users_source_container_replay_rechecks_each_user_target(
    isolated_knowledge_layout,
) -> None:
    # See the companion missing-target canary above: v8 correctly prevents the
    # destructive fixture itself, while v7 still exercises replay validation.
    factory = _factory(isolated_knowledge_layout, _V7_CATALOG)
    spaces = _seed_personal_owners(factory, "alice")
    repository = LegacyMigrationRepository()
    connection = factory.open()
    try:
        with transaction(connection):
            child = _put_user_child(
                repository,
                connection,
                owner="alice",
                space_id=spaces["alice"],
                record_id=_record_id(101),
                source_ordinal=1,
                source_sha256="1" * 64,
            )
            binding = (
                child.record.id,
                child.record.details.inventory_record_id,
                child.record.key.source_sha256,
            )
            target = _source_container_target(child_bindings=(binding,))
            container = _put_container(repository, connection, target)

        with transaction(connection):
            connection.execute(
                "DELETE FROM knowledge_space WHERE id=?", (spaces["alice"],)
            )

        with pytest.raises(LegacyMigrationIntegrityError, match="personal space"):
            repository.verify_target_integrity(
                connection,
                container.record,
                verified_blob_evidence=_container_evidence(target),
            )
    finally:
        connection.close()
