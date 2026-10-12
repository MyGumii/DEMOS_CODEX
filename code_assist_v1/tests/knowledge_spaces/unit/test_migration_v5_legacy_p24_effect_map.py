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
    SCHEMA_V5_MIGRATION,
    SingleMigrator,
    V005_EXPECTED_SCHEMA_SHA256,
    schema_sha256,
)
from code_assist_v1.knowledge_core.migrations.v005_legacy_p24_effect_map import (
    LEGACY_MIGRATION_P24_RELATIONSHIP_IMMUTABLE_CODE,
    V005_STATEMENTS,
)
from code_assist_v1.knowledge_core.repositories.spaces import SpaceRepository
from code_assist_v1.knowledge_core.repositories.users import LegacyUserRecord, UserRepository


pytestmark = pytest.mark.unit

_V4_CATALOG = MigrationCatalog(
    (
        SCHEMA_V1_MIGRATION,
        SCHEMA_V2_MIGRATION,
        SCHEMA_V3_MIGRATION,
        SCHEMA_V4_MIGRATION,
    )
)
_V5_CATALOG = MigrationCatalog((*_V4_CATALOG.migrations, SCHEMA_V5_MIGRATION))
_RUN = "00000000-0000-4000-8000-000000000100"
_MAP_A = "00000000-0000-4000-8000-000000000201"
_MAP_B = "00000000-0000-4000-8000-000000000202"
_REL_MAP = "00000000-0000-4000-8000-000000000301"
_SPACE = "00000000-0000-4000-8000-000000000401"


def _layout(value) -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=value.root,
        database=value.core_db_path,
        blob_root=value.blobs_dir,
        projection_root=value.projections_dir,
    )


def _factory(value, catalog=_V5_CATALOG) -> ConnectionFactory:
    return ConnectionFactory._for_isolated_tests(value.core_db_path, catalog)


def _migrate(value, catalog=_V5_CATALOG):
    factory = _factory(value, catalog)
    result = SingleMigrator(
        factory,
        app_version="p2.4-v5-test",
        storage_layout=_layout(value),
    ).run()
    return factory, result


def _insert_mapping(connection, mapping_id: str, path: str, sha: str) -> None:
    connection.execute(
        """INSERT INTO legacy_migration_map(
             id,migration_run_id,legacy_kind,normalized_path,source_sha256,
             resolved_owner_user_id,artifact_id,item_id,revision_id,edge_id,
             status,details_json,created_at,updated_at
           ) VALUES (?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,'imported','{}',100,100)""",
        (mapping_id, _RUN, "knowledge.wiki_graph_nodes", path, sha),
    )


def _seed_owner_space(connection) -> None:
    UserRepository().import_legacy(
        connection,
        LegacyUserRecord(
            id="owner",
            display_name="Owner",
            password_hash="p2.4-hash",
            system_role="admin",
            account_status="active",
            created_at_ms=10,
            updated_at_ms=10,
        ),
    )
    SpaceRepository().ensure_personal(
        connection,
        space_id=_SPACE,
        user_id="owner",
        created_at_ms=20,
    )


def test_v5_fresh_bootstrap_has_exact_forward_objects(isolated_knowledge_layout) -> None:
    factory, result = _migrate(isolated_knowledge_layout)
    assert result.applied_versions == (1, 2, 3, 4, 5)
    assert result.schema_version == 5
    connection = factory.open()
    try:
        assert schema_sha256(connection) == V005_EXPECTED_SCHEMA_SHA256
        mapping_sql = connection.execute(
            """SELECT sql FROM sqlite_schema
               WHERE type='table' AND name='legacy_migration_map'"""
        ).fetchone()["sql"]
        assert "finding_id" in mapping_sql
        assert "audit_event_id" in mapping_sql
        assert connection.execute(
            """SELECT count(*) FROM sqlite_schema
               WHERE name IN (
                 'legacy_migration_p24_relationship_map',
                 'ux_migration_finding_target','ux_migration_audit_event_target',
                 'ux_migration_p24_relationship_identity',
                 'ix_migration_p24_relationship_run',
                 'ix_migration_p24_relationship_endpoints',
                 'ix_migration_p24_relationship_effect',
                 'trg_legacy_migration_p24_relationship_history_update',
                 'trg_legacy_migration_p24_relationship_history_delete'
               )"""
        ).fetchone()[0] == 9
    finally:
        connection.close()


def test_nonempty_v4_record_map_upgrades_without_rewriting_rows(
    isolated_knowledge_layout,
) -> None:
    v4_factory, _ = _migrate(isolated_knowledge_layout, _V4_CATALOG)
    connection = v4_factory.open()
    try:
        with transaction(connection):
            _insert_mapping(connection, _MAP_A, "p2v1/a", "a" * 64)
        before = tuple(
            connection.execute(
                "SELECT * FROM legacy_migration_map WHERE id=?", (_MAP_A,)
            ).fetchone()
        )
    finally:
        connection.close()

    factory, result = _migrate(isolated_knowledge_layout)
    assert result.applied_versions == (5,)
    connection = factory.open()
    try:
        row = connection.execute(
            "SELECT * FROM legacy_migration_map WHERE id=?", (_MAP_A,)
        ).fetchone()
        assert tuple(row)[:-2] == before
        assert row["finding_id"] is None
        assert row["audit_event_id"] is None
    finally:
        connection.close()


def test_p24_relationship_checkpoint_is_insert_only_and_exactly_constrained(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate(isolated_knowledge_layout)
    connection = factory.open()
    try:
        with transaction(connection):
            _seed_owner_space(connection)
            _insert_mapping(connection, _MAP_A, "p2v1/a", "a" * 64)
            _insert_mapping(connection, _MAP_B, "p2v1/b", "b" * 64)
            connection.execute(
                """INSERT INTO legacy_migration_p24_relationship_map(
                     id,migration_run_id,relationship_id,relationship_sha256,
                     relationship_type,source_mapping_id,from_mapping_id,
                     to_mapping_id,resolved_owner_user_id,target_space_id,
                     effect_kind,edge_id,finding_id,item_id,status,details_json,
                     created_at,updated_at
                   ) VALUES (?,?,?,?, 'graph_node_source',NULL,?,?,?,?,'dependency',
                     NULL,NULL,NULL,'imported','{}',100,100)""",
                (
                    _REL_MAP,
                    _RUN,
                    "rel_0123456789abcdef01234567",
                    "c" * 64,
                    _MAP_A,
                    _MAP_B,
                    "owner",
                    _SPACE,
                ),
            )
        for statement in (
            "UPDATE legacy_migration_p24_relationship_map SET updated_at=101",
            "DELETE FROM legacy_migration_p24_relationship_map",
        ):
            with pytest.raises(sqlite3.IntegrityError, match=LEGACY_MIGRATION_P24_RELATIONSHIP_IMMUTABLE_CODE):
                with transaction(connection):
                    connection.execute(statement)
        with pytest.raises(sqlite3.IntegrityError):
            with transaction(connection):
                connection.execute(
                    """INSERT INTO legacy_migration_p24_relationship_map(
                         id,migration_run_id,relationship_id,relationship_sha256,
                         relationship_type,source_mapping_id,from_mapping_id,
                         to_mapping_id,resolved_owner_user_id,target_space_id,
                         effect_kind,status,details_json,created_at,updated_at
                       ) VALUES (?,?,?,?, 'requires',NULL,?,?,?,?,'dependency',
                         'imported','{}',100,100)""",
                    (
                        "00000000-0000-4000-8000-000000000302",
                        _RUN,
                        "rel_1123456789abcdef01234567",
                        "d" * 64,
                        _MAP_A,
                        _MAP_B,
                        "owner",
                        _SPACE,
                    ),
                )
    finally:
        connection.close()


def test_v5_rerun_is_idempotent(isolated_knowledge_layout) -> None:
    factory = _factory(isolated_knowledge_layout)
    migrator = SingleMigrator(
        factory,
        app_version="p2.4-v5-rerun",
        storage_layout=_layout(isolated_knowledge_layout),
    )
    first = migrator.run()
    second = migrator.run()
    assert first.applied_versions == (1, 2, 3, 4, 5)
    assert second.applied_versions == ()
    assert second.schema_version == 5


def test_v5_named_object_collision_rolls_back_column_additions(
    isolated_knowledge_layout,
) -> None:
    _, _ = _migrate(isolated_knowledge_layout, _V4_CATALOG)
    collision = Migration(
        version=5,
        name="legacy_p24_effect_map_collision_probe",
        statements=(
            "CREATE TABLE legacy_migration_p24_relationship_map(id TEXT)",
            *V005_STATEMENTS,
        ),
        expected_schema_sha256="0" * 64,
    )
    catalog = MigrationCatalog((*_V4_CATALOG.migrations, collision))
    factory = _factory(isolated_knowledge_layout, catalog)
    with pytest.raises(KnowledgeCoreMigrationError):
        SingleMigrator(
            factory,
            app_version="p2.4-v5-collision",
            storage_layout=_layout(isolated_knowledge_layout),
        ).run()

    raw = sqlite3.connect(isolated_knowledge_layout.core_db_path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone() == (4,)
        columns = {
            row[1]
            for row in raw.execute("PRAGMA table_info(legacy_migration_map)").fetchall()
        }
        assert "finding_id" not in columns
        assert "audit_event_id" not in columns
        assert raw.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name='ux_migration_finding_target'"
        ).fetchone() == (0,)
        assert raw.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name='legacy_migration_p24_relationship_map'"
        ).fetchone() == (0,)
    finally:
        raw.close()
