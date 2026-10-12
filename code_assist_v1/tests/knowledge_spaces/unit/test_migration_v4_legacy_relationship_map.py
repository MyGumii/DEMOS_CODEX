from __future__ import annotations

import sqlite3

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.errors import KnowledgeCoreMigrationError
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    Migration,
    MigrationCatalog,
    MigrationStorageLayout,
    SCHEMA_V1_MIGRATION,
    SCHEMA_V2_MIGRATION,
    SCHEMA_V3_MIGRATION,
    SCHEMA_V4_MIGRATION,
    SingleMigrator,
    V003_EXPECTED_SCHEMA_SHA256,
    V004_EXPECTED_SCHEMA_SHA256,
    schema_sha256,
)
from code_assist_v1.knowledge_core.migrations.v004_legacy_migration_relationship_map import (
    LEGACY_MIGRATION_RELATIONSHIP_MAP_IMMUTABLE_CODE,
    LEGACY_MIGRATION_RELATIONSHIP_MAP_IMMUTABLE_TRIGGERS,
    LEGACY_MIGRATION_RELATIONSHIP_MAP_INDEXES,
    LEGACY_MIGRATION_RELATIONSHIP_MAP_TABLE,
    V004_POSTCONDITION_QUERIES,
    V004_STATEMENTS,
)


pytestmark = pytest.mark.unit


_V3_CATALOG = MigrationCatalog(
    (SCHEMA_V1_MIGRATION, SCHEMA_V2_MIGRATION, SCHEMA_V3_MIGRATION)
)
_V4_CATALOG = MigrationCatalog(
    (
        SCHEMA_V1_MIGRATION,
        SCHEMA_V2_MIGRATION,
        SCHEMA_V3_MIGRATION,
        SCHEMA_V4_MIGRATION,
    )
)
_RUN_ID = "00000000-0000-4000-8000-000000000100"
_SOURCE_MAPPING_ID = "00000000-0000-4000-8000-000000000201"
_ASSET_MAPPING_ID = "00000000-0000-4000-8000-000000000202"
_RELATIONSHIP_MAPPING_ID = "00000000-0000-4000-8000-000000000301"
_RELATIONSHIP_ID = "rel_0123456789abcdef01234567"
_SOURCE_RECORD_ID = "rec_0123456789abcdef01234567"
_ASSET_RECORD_ID = "rec_89abcdef0123456701234567"
_SPACE_ID = "00000000-0000-4000-8000-000000000401"
_SOURCE_ARTIFACT_ID = "00000000-0000-4000-8000-000000000501"
_ASSET_ARTIFACT_ID = "00000000-0000-4000-8000-000000000502"
_SOURCE_ITEM_ID = "00000000-0000-4000-8000-000000000601"
_SOURCE_REVISION_ID = "00000000-0000-4000-8000-000000000701"
_TARGET_ASSET_BLOB_ID = "00000000-0000-4000-8000-000000000801"
_EMBEDDED_ASSET_BLOB_ID = "00000000-0000-4000-8000-000000000802"
_SECOND_EMBEDDED_ASSET_BLOB_ID = "00000000-0000-4000-8000-000000000803"
_SOURCE_RAW_BLOB_ID = "00000000-0000-4000-8000-000000000804"

_RELATIONSHIP_COLUMNS = (
    "id",
    "migration_run_id",
    "relationship_id",
    "relationship_sha256",
    "relationship_type",
    "from_inventory_record_id",
    "to_inventory_record_id",
    "from_mapping_id",
    "to_mapping_id",
    "resolved_owner_user_id",
    "target_space_id",
    "source_artifact_id",
    "source_item_id",
    "source_revision_id",
    "target_asset_artifact_id",
    "target_asset_blob_id",
    "embedded_asset_blob_id",
    "embedded_ordinal",
    "embedded_logical_name_sha256",
    "status",
    "details_json",
    "created_at",
    "updated_at",
)


def _layout(isolated_knowledge_layout) -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=isolated_knowledge_layout.root,
        database=isolated_knowledge_layout.core_db_path,
        blob_root=isolated_knowledge_layout.blobs_dir,
        projection_root=isolated_knowledge_layout.projections_dir,
    )


def _factory(isolated_knowledge_layout, catalog=_V4_CATALOG):
    return ConnectionFactory._for_isolated_tests(
        isolated_knowledge_layout.core_db_path,
        catalog,
    )


def _migrator(factory, isolated_knowledge_layout, *, app_version: str):
    return SingleMigrator(
        factory,
        app_version=app_version,
        storage_layout=_layout(isolated_knowledge_layout),
    )


def _mapping_values() -> tuple[tuple[object, ...], tuple[object, ...]]:
    return (
        (
            _SOURCE_MAPPING_ID,
            _RUN_ID,
            "knowledge.wiki_sources",
            "p2v1/root/source/physical_file/0",
            "a" * 64,
            "owner",
            _SOURCE_ARTIFACT_ID,
            _SOURCE_ITEM_ID,
            _SOURCE_REVISION_ID,
        ),
        (
            _ASSET_MAPPING_ID,
            _RUN_ID,
            "knowledge.wiki_assets",
            "p2v1/root/asset/physical_file/0",
            "b" * 64,
            "owner",
            _ASSET_ARTIFACT_ID,
            None,
            None,
        ),
    )


def _seed_dependencies(connection) -> None:
    connection.execute(
        """INSERT INTO app_user(
             id,display_name,password_hash,system_role,account_status,
             current_part_id,scope_epoch,created_at,updated_at,disabled_at,
             row_version,must_change_password,password_changed_at
           ) VALUES ('owner','Owner','hash','user','active',NULL,1,100,100,
             NULL,1,0,100)"""
    )
    connection.execute(
        """INSERT INTO knowledge_space(
             id,space_kind,owner_user_id,owner_org_unit_id,status,write_enabled,
             generation,created_at,updated_at,row_version
           ) VALUES (?,'personal','owner',NULL,'active',0,0,100,100,1)""",
        (_SPACE_ID,),
    )
    for sha256, media_type in (("a" * 64, "text/markdown"), ("b" * 64, "image/png")):
        connection.execute(
            """INSERT INTO blob_object(
                 sha256,size_bytes,media_type,relative_path,verification_status,
                 created_at,verified_at
               ) VALUES (?,1,?,?, 'verified',100,100)""",
            (sha256, media_type, f"{sha256[:2]}/{sha256[2:4]}/{sha256}"),
        )
    connection.execute(
        """INSERT INTO source_artifact(
             id,origin_type,origin_key,origin_revision_key,raw_sha256,
             raw_size_bytes,media_type,original_filename,
             body_retention_status,body_retention_reason,body_purged_at,
             orphan_cleanup_eligible_at,publisher_user_id,
             source_owner_snapshot_json,metadata_json,metadata_schema_version,
             created_at
           ) VALUES (?,'legacy','source',NULL,?,1,'text/markdown','source.md',
             'present',NULL,NULL,2592000100,'owner','{}','{}',1,100)""",
        (_SOURCE_ARTIFACT_ID, "a" * 64),
    )
    connection.execute(
        """INSERT INTO source_artifact(
             id,origin_type,origin_key,origin_revision_key,raw_sha256,
             raw_size_bytes,media_type,original_filename,
             body_retention_status,body_retention_reason,body_purged_at,
             orphan_cleanup_eligible_at,publisher_user_id,
             source_owner_snapshot_json,metadata_json,metadata_schema_version,
             created_at
           ) VALUES (?,'legacy','asset',NULL,?,1,'image/png','asset.png',
             'present',NULL,NULL,2592000100,'owner','{}','{}',1,100)""",
        (_ASSET_ARTIFACT_ID, "b" * 64),
    )
    connection.execute(
        """INSERT INTO artifact_blob(
             id,artifact_id,content_sha256,size_bytes,role,logical_name,
             media_type,ordinal,body_retention_status,created_at
           ) VALUES (?,?,?,1,'raw','source.md','text/markdown',0,'present',100)""",
        (_SOURCE_RAW_BLOB_ID, _SOURCE_ARTIFACT_ID, "a" * 64),
    )
    connection.execute(
        """INSERT INTO artifact_blob(
             id,artifact_id,content_sha256,size_bytes,role,logical_name,
             media_type,ordinal,body_retention_status,created_at
           ) VALUES (?,?,?,1,'raw','asset.png','image/png',0,'present',100)""",
        (_TARGET_ASSET_BLOB_ID, _ASSET_ARTIFACT_ID, "b" * 64),
    )
    connection.execute(
        """INSERT INTO artifact_blob(
             id,artifact_id,content_sha256,size_bytes,role,logical_name,
             media_type,ordinal,body_retention_status,created_at
           ) VALUES (?,?,?,1,'embedded_asset','asset.png','image/png',0,
             'present',100)""",
        (_EMBEDDED_ASSET_BLOB_ID, _SOURCE_ARTIFACT_ID, "b" * 64),
    )
    for artifact_blob_id, sha256 in (
        (_SOURCE_RAW_BLOB_ID, "a" * 64),
        (_TARGET_ASSET_BLOB_ID, "b" * 64),
        (_EMBEDDED_ASSET_BLOB_ID, "b" * 64),
    ):
        connection.execute(
            """INSERT INTO artifact_blob_body(
                 artifact_blob_id,content_sha256,created_at
               ) VALUES (?,?,100)""",
            (artifact_blob_id, sha256),
        )
    connection.execute(
        """INSERT INTO knowledge_item(
             id,space_id,lineage_group_id,lifecycle_status,curation_status,
             current_revision_id,last_curation_transition_id,
             latest_deactivation_episode_id,redirect_item_id,created_by,
             updated_by,created_at,updated_at,row_version
           ) VALUES (?, ?, NULL,'active','provisional',?,NULL,NULL,NULL,
             'owner','owner',100,100,1)""",
        (_SOURCE_ITEM_ID, _SPACE_ID, _SOURCE_REVISION_ID),
    )
    connection.execute(
        """INSERT INTO knowledge_revision(
             id,item_id,revision_no,parent_revision_id,base_revision_id,title,
             raw_content_sha256,normalized_content_sha256,content_size_bytes,
             normalizer_version,metadata_json,change_kind,change_reason,
             created_by,created_at
           ) VALUES (?,?,1,NULL,NULL,'Source',?,?,1,'markdown_lf_v1','{}',
             'import','legacy import','owner',100)""",
        (_SOURCE_REVISION_ID, _SOURCE_ITEM_ID, "a" * 64, "a" * 64),
    )
    connection.execute(
        """INSERT INTO knowledge_revision_body(
             revision_id,item_id,content_md,content_blob_hash,
             content_size_bytes,content_sha256,created_at
           ) VALUES (?,?,'x',NULL,1,?,100)""",
        (_SOURCE_REVISION_ID, _SOURCE_ITEM_ID, "a" * 64),
    )
    for values in _mapping_values():
        connection.execute(
            """INSERT INTO legacy_migration_map(
                 id,migration_run_id,legacy_kind,normalized_path,source_sha256,
                 resolved_owner_user_id,artifact_id,item_id,revision_id,edge_id,
                 status,details_json,created_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,NULL,'imported','{}',100,100)""",
            values,
        )


def _relationship_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": _RELATIONSHIP_MAPPING_ID,
        "migration_run_id": _RUN_ID,
        "relationship_id": _RELATIONSHIP_ID,
        "relationship_sha256": "c" * 64,
        "relationship_type": "asset_reference",
        "from_inventory_record_id": _SOURCE_RECORD_ID,
        "to_inventory_record_id": _ASSET_RECORD_ID,
        "from_mapping_id": _SOURCE_MAPPING_ID,
        "to_mapping_id": _ASSET_MAPPING_ID,
        "resolved_owner_user_id": "owner",
        "target_space_id": _SPACE_ID,
        "source_artifact_id": _SOURCE_ARTIFACT_ID,
        "source_item_id": _SOURCE_ITEM_ID,
        "source_revision_id": _SOURCE_REVISION_ID,
        "target_asset_artifact_id": _ASSET_ARTIFACT_ID,
        "target_asset_blob_id": _TARGET_ASSET_BLOB_ID,
        "embedded_asset_blob_id": _EMBEDDED_ASSET_BLOB_ID,
        "embedded_ordinal": 0,
        "embedded_logical_name_sha256": "d" * 64,
        "status": "imported",
        "details_json": "{}",
        "created_at": 200,
        "updated_at": 200,
    }
    values.update(overrides)
    return values


def _insert_relationship(connection, **overrides: object) -> None:
    values = _relationship_values(**overrides)
    placeholders = ",".join("?" for _ in _RELATIONSHIP_COLUMNS)
    connection.execute(
        f"INSERT INTO {LEGACY_MIGRATION_RELATIONSHIP_MAP_TABLE}"
        f"({','.join(_RELATIONSHIP_COLUMNS)}) VALUES ({placeholders})",
        tuple(values[column] for column in _RELATIONSHIP_COLUMNS),
    )


def _seed_second_embedded_blob(connection) -> None:
    connection.execute(
        """INSERT INTO artifact_blob(
             id,artifact_id,content_sha256,size_bytes,role,logical_name,
             media_type,ordinal,body_retention_status,created_at
           ) VALUES (?,?,?,1,'embedded_asset','asset-two.png','image/png',1,
             'present',100)""",
        (_SECOND_EMBEDDED_ASSET_BLOB_ID, _SOURCE_ARTIFACT_ID, "b" * 64),
    )
    connection.execute(
        """INSERT INTO artifact_blob_body(
             artifact_blob_id,content_sha256,created_at
           ) VALUES (?,?,100)""",
        (_SECOND_EMBEDDED_ASSET_BLOB_ID, "b" * 64),
    )


def _schema_object_names(connection, object_type: str) -> tuple[str, ...]:
    return tuple(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type=? ORDER BY name",
            (object_type,),
        ).fetchall()
    )


def test_fresh_bootstrap_applies_physical_schema_v4(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    result = _migrator(
        factory,
        isolated_knowledge_layout,
        app_version="p2.3-v4-bootstrap",
    ).run()

    assert result.applied_versions == (1, 2, 3, 4)
    assert result.schema_version == 4
    assert _V4_CATALOG.migrations == (
        SCHEMA_V1_MIGRATION,
        SCHEMA_V2_MIGRATION,
        SCHEMA_V3_MIGRATION,
        SCHEMA_V4_MIGRATION,
    )

    connection = factory.open()
    try:
        assert schema_sha256(connection) == V004_EXPECTED_SCHEMA_SHA256
        assert connection.contract_snapshot()["pragmas"]["user_version"] == 4
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT version,name FROM schema_migration ORDER BY version"
            ).fetchall()
        ] == [
            (1, "canonical_schema_v1"),
            (2, "account_auth_policy_v2"),
            (3, "legacy_migration_map_durability_v3"),
            (4, "legacy_migration_relationship_map_v4"),
        ]
        assert LEGACY_MIGRATION_RELATIONSHIP_MAP_TABLE in _schema_object_names(
            connection, "table"
        )
        assert connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?",
            (LEGACY_MIGRATION_RELATIONSHIP_MAP_TABLE,),
        ).fetchone()[0] == V004_STATEMENTS[3]
        assert set(LEGACY_MIGRATION_RELATIONSHIP_MAP_INDEXES).issubset(
            _schema_object_names(connection, "index")
        )
        assert set(
            LEGACY_MIGRATION_RELATIONSHIP_MAP_IMMUTABLE_TRIGGERS
        ).issubset(_schema_object_names(connection, "trigger"))
    finally:
        connection.close()


def test_v4_preflight_rejects_a_preexisting_relationship_table_atomically() -> None:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA recursive_triggers=ON")
        connection.execute("BEGIN EXCLUSIVE")
        for migration in (
            SCHEMA_V1_MIGRATION,
            SCHEMA_V2_MIGRATION,
            SCHEMA_V3_MIGRATION,
        ):
            for statement in migration.statements:
                connection.execute(statement)
        connection.commit()
        connection.execute(
            "CREATE TABLE legacy_migration_relationship_map(marker INTEGER) STRICT"
        )

        connection.execute("BEGIN EXCLUSIVE")
        with pytest.raises(
            sqlite3.IntegrityError,
            match="LEGACY_MIGRATION_RELATIONSHIP_MAP_ABSENT_BEFORE_V4",
        ):
            for statement in V004_STATEMENTS:
                connection.execute(statement)
        connection.rollback()

        assert connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' "
            "AND name='legacy_migration_relationship_map'"
        ).fetchone() == (
            "CREATE TABLE legacy_migration_relationship_map(marker INTEGER) STRICT",
        )
        assert connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE "
            "name='legacy_migration_relationship_map_v4_preflight'"
        ).fetchone() == (0,)
        assert not set(LEGACY_MIGRATION_RELATIONSHIP_MAP_INDEXES).intersection(
            name for name, in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='index'"
            )
        )
    finally:
        connection.close()


def test_existing_nonempty_v3_database_upgrades_without_changing_record_mappings(
    isolated_knowledge_layout,
) -> None:
    v3_factory = _factory(isolated_knowledge_layout, _V3_CATALOG)
    _migrator(
        v3_factory,
        isolated_knowledge_layout,
        app_version="p2.3-v3-relationship-fixture",
    ).run()
    connection = v3_factory.open()
    try:
        with transaction(connection):
            _seed_dependencies(connection)
        before = [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM legacy_migration_map ORDER BY id"
            ).fetchall()
        ]
    finally:
        connection.close()

    v4_factory = _factory(isolated_knowledge_layout)
    upgraded = _migrator(
        v4_factory,
        isolated_knowledge_layout,
        app_version="p2.3-v4-upgrade",
    ).run()
    assert upgraded.applied_versions == (4,)
    assert upgraded.schema_version == 4

    connection = v4_factory.open()
    try:
        assert schema_sha256(connection) == V004_EXPECTED_SCHEMA_SHA256
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM legacy_migration_map ORDER BY id"
            ).fetchall()
        ] == before
        assert tuple(connection.execute(
            "SELECT count(*) FROM legacy_migration_relationship_map"
        ).fetchone()) == (0,)
    finally:
        connection.close()


def test_v4_relationship_rows_are_insert_only_with_stable_abort_code(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _migrator(
        factory,
        isolated_knowledge_layout,
        app_version="p2.3-v4-immutability",
    ).run()
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_dependencies(connection)
            _insert_relationship(connection)
            _seed_second_embedded_blob(connection)
        with pytest.raises(
            sqlite3.IntegrityError,
            match=LEGACY_MIGRATION_RELATIONSHIP_MAP_IMMUTABLE_CODE,
        ):
            with transaction(connection):
                connection.execute(
                    "UPDATE legacy_migration_relationship_map "
                    "SET updated_at=updated_at+1"
                )
        with pytest.raises(
            sqlite3.IntegrityError,
            match=LEGACY_MIGRATION_RELATIONSHIP_MAP_IMMUTABLE_CODE,
        ):
            with transaction(connection):
                connection.execute(
                    "DELETE FROM legacy_migration_relationship_map"
                )
        assert tuple(connection.execute(
            "SELECT count(*) FROM legacy_migration_relationship_map"
        ).fetchone()) == (1,)
    finally:
        connection.close()


def test_v4_rejects_duplicate_relationship_and_embedded_blob_identities(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _migrator(
        factory,
        isolated_knowledge_layout,
        app_version="p2.3-v4-uniqueness",
    ).run()
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_dependencies(connection)
            _insert_relationship(connection)
            _seed_second_embedded_blob(connection)
        with pytest.raises(
            sqlite3.IntegrityError,
            match=r"legacy_migration_relationship_map\.relationship_id",
        ):
            with transaction(connection):
                _insert_relationship(
                    connection,
                    id="00000000-0000-4000-8000-000000000302",
                    embedded_asset_blob_id=_SECOND_EMBEDDED_ASSET_BLOB_ID,
                    embedded_ordinal=1,
                )
        with pytest.raises(
            sqlite3.IntegrityError,
            match=r"legacy_migration_relationship_map\.embedded_asset_blob_id",
        ):
            with transaction(connection):
                _insert_relationship(
                    connection,
                    id="00000000-0000-4000-8000-000000000303",
                    relationship_id="rel_1123456789abcdef01234567",
                )
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("overrides", "error_pattern"),
    (
        ({"relationship_id": "rel_not_hex________________"}, "CHECK constraint"),
        ({"relationship_sha256": "A" * 64}, "CHECK constraint"),
        ({"relationship_type": "graph_edge"}, "CHECK constraint"),
        ({"from_inventory_record_id": "rec_not_hex________________"}, "CHECK constraint"),
        ({"to_inventory_record_id": _SOURCE_RECORD_ID}, "CHECK constraint"),
        ({"to_mapping_id": _SOURCE_MAPPING_ID}, "CHECK constraint"),
        ({"target_asset_blob_id": _EMBEDDED_ASSET_BLOB_ID}, "CHECK constraint"),
        ({"embedded_ordinal": -1}, "CHECK constraint"),
        ({"embedded_logical_name_sha256": "D" * 64}, "CHECK constraint"),
        ({"status": "failed"}, "CHECK constraint"),
        ({"details_json": "[]"}, "CHECK constraint"),
        ({"updated_at": 201}, "CHECK constraint"),
    ),
)
def test_v4_rejects_invalid_relationship_shapes(
    isolated_knowledge_layout,
    overrides: dict[str, object],
    error_pattern: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _migrator(
        factory,
        isolated_knowledge_layout,
        app_version="p2.3-v4-shape",
    ).run()
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_dependencies(connection)
        with pytest.raises(sqlite3.IntegrityError, match=error_pattern):
            with transaction(connection):
                _insert_relationship(connection, **overrides)
        assert tuple(connection.execute(
            "SELECT count(*) FROM legacy_migration_relationship_map"
        ).fetchone()) == (0,)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "overrides",
    (
        {"from_mapping_id": "00000000-0000-4000-8000-000000009001"},
        {"to_mapping_id": "00000000-0000-4000-8000-000000009002"},
        {"resolved_owner_user_id": "missing"},
        {"target_space_id": "00000000-0000-4000-8000-000000009003"},
        {"source_artifact_id": "00000000-0000-4000-8000-000000009004"},
        {"source_item_id": "00000000-0000-4000-8000-000000009005"},
        {"source_revision_id": "00000000-0000-4000-8000-000000009006"},
        {"target_asset_artifact_id": "00000000-0000-4000-8000-000000009007"},
        {"target_asset_blob_id": "00000000-0000-4000-8000-000000009008"},
        {"embedded_asset_blob_id": "00000000-0000-4000-8000-000000009009"},
    ),
)
def test_v4_rejects_missing_relationship_foreign_keys(
    isolated_knowledge_layout,
    overrides: dict[str, object],
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _migrator(
        factory,
        isolated_knowledge_layout,
        app_version="p2.3-v4-foreign-key",
    ).run()
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_dependencies(connection)
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint"):
            with transaction(connection):
                _insert_relationship(connection, **overrides)
        assert tuple(connection.execute(
            "SELECT count(*) FROM legacy_migration_relationship_map"
        ).fetchone()) == (0,)
    finally:
        connection.close()


def test_v4_migrator_rerun_is_idempotent(isolated_knowledge_layout) -> None:
    factory = _factory(isolated_knowledge_layout)
    migrator = _migrator(
        factory,
        isolated_knowledge_layout,
        app_version="p2.3-v4-idempotency",
    )
    first = migrator.run()
    second = migrator.run()

    assert first.applied_versions == (1, 2, 3, 4)
    assert second.applied_versions == ()
    assert second.schema_version == 4
    assert second.catalog_checksum_sha256 == first.catalog_checksum_sha256


def test_v4_postcondition_failure_rolls_back_schema_and_preserves_v3_rows(
    isolated_knowledge_layout,
) -> None:
    v3_factory = _factory(isolated_knowledge_layout, _V3_CATALOG)
    _migrator(
        v3_factory,
        isolated_knowledge_layout,
        app_version="p2.3-v3-rollback-fixture",
    ).run()
    connection = v3_factory.open()
    try:
        with transaction(connection):
            _seed_dependencies(connection)
    finally:
        connection.close()

    failing_v4 = Migration(
        version=4,
        name="legacy_migration_relationship_map_v4",
        statements=V004_STATEMENTS,
        expected_schema_sha256=V004_EXPECTED_SCHEMA_SHA256,
        postcondition_queries=(*V004_POSTCONDITION_QUERIES, "SELECT 'forced failure'"),
    )
    failing_catalog = MigrationCatalog(
        (
            SCHEMA_V1_MIGRATION,
            SCHEMA_V2_MIGRATION,
            SCHEMA_V3_MIGRATION,
            failing_v4,
        )
    )
    failing_factory = _factory(isolated_knowledge_layout, failing_catalog)
    with pytest.raises(KnowledgeCoreMigrationError):
        _migrator(
            failing_factory,
            isolated_knowledge_layout,
            app_version="p2.3-v4-rollback",
        ).run()

    preserved = v3_factory.open()
    try:
        assert schema_sha256(preserved) == V003_EXPECTED_SCHEMA_SHA256
        assert preserved.contract_snapshot()["pragmas"]["user_version"] == 3
        assert tuple(preserved.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()) == (2,)
        assert tuple(preserved.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name LIKE "
            "'legacy_migration_relationship_map%'"
        ).fetchone()) == (0,)
        assert tuple(preserved.execute(
            "SELECT group_concat(version, ',') FROM schema_migration "
            "ORDER BY version"
        ).fetchone()) == ("1,2,3",)
    finally:
        preserved.close()
