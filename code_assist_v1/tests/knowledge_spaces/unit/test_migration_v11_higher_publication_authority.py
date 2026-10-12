from __future__ import annotations

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.errors import KnowledgeCoreMigrationError
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    Migration,
    MigrationCatalog,
    MigrationStorageLayout,
    SingleMigrator,
    V010_EXPECTED_SCHEMA_SHA256,
    V011_EXPECTED_SCHEMA_SHA256,
    schema_sha256,
    verify_canonical_integrity,
)
from code_assist_v1.knowledge_core.migrations.v011_higher_publication_authority import (
    V011_POSTCONDITION_QUERIES,
    V011_STATEMENTS,
)
from code_assist_v1.knowledge_core.scope import ScopeResolver
from code_assist_v1.knowledge_core.services.publication import PublicationService
from code_assist_v1.test_safety import create_isolated_test_layout
from code_assist_v1.tests.knowledge_spaces.unit.test_publication_service import (
    _flags,
    _preview,
    _publish_command,
    _seed_actor,
)


pytestmark = pytest.mark.unit
_V10_CATALOG = MigrationCatalog(CANONICAL_MIGRATION_CATALOG.migrations[:10])
_V11_CATALOG = MigrationCatalog(CANONICAL_MIGRATION_CATALOG.migrations[:11])


def _layout(value) -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=value.root,
        database=value.core_db_path,
        blob_root=value.blobs_dir,
        projection_root=value.projections_dir,
    )


def _migrate(value, catalog: MigrationCatalog):
    factory = ConnectionFactory._for_isolated_tests(value.core_db_path, catalog)
    result = SingleMigrator(
        factory,
        app_version="v11-higher-publication-migration-test",
        storage_layout=_layout(value),
    ).run()
    return factory, result


@pytest.mark.scenario("HSPACE-009")
@pytest.mark.scenario("HSPACE-011")
def test_v10_publication_is_preserved_v11_matches_pristine_and_replays(
    tmp_path, isolated_knowledge_layout
) -> None:
    v10_factory, initial = _migrate(isolated_knowledge_layout, _V10_CATALOG)
    assert initial.applied_versions == tuple(range(1, 11))
    _seed_actor(v10_factory)
    preview = _preview(
        v10_factory,
        label="v11-preserved",
        markdown="# Preserved v10 publication\n\nBody\n",
        epoch=1,
        part=False,
    )
    command = _publish_command(
        preview,
        label="v11-preserved-commit",
        epoch=1,
        target_mode="personal",
    )
    before_result = PublicationService(
        v10_factory, ScopeResolver(_flags())
    ).publish(command)
    connection = v10_factory.open(read_only=True)
    try:
        before = tuple(
            connection.execute(
                """SELECT id,dedup_id,actor_user_id,operation_id,request_sha256,
                          artifact_id,extraction_snapshot_id,target_mode,
                          lineage_group_id,created_at
                     FROM publication_operation"""
            ).fetchone()
        )
        assert schema_sha256(connection) == V010_EXPECTED_SCHEMA_SHA256
    finally:
        connection.close()

    upgraded, migrated = _migrate(
        isolated_knowledge_layout, _V11_CATALOG
    )
    assert migrated.applied_versions == (11,)
    connection = upgraded.open(read_only=True)
    try:
        after = tuple(
            connection.execute(
                """SELECT id,dedup_id,actor_user_id,operation_id,request_sha256,
                          artifact_id,extraction_snapshot_id,target_mode,
                          lineage_group_id,created_at
                     FROM publication_operation"""
            ).fetchone()
        )
        assert after == before
        assert connection.execute(
            "SELECT explicit_authority_id FROM publication_operation"
        ).fetchone()[0] is None
        assert schema_sha256(connection) == V011_EXPECTED_SCHEMA_SHA256
        verify_canonical_integrity(connection, _V11_CATALOG)
    finally:
        connection.close()
    replay = PublicationService(upgraded, ScopeResolver(_flags())).publish(command)
    assert replay == before_result
    _same, no_op = _migrate(
        isolated_knowledge_layout, _V11_CATALOG
    )
    assert no_op.applied_versions == ()

    pristine = create_isolated_test_layout(
        tmp_path / "v11-pristine", user_id="v11"
    )
    pristine_factory, pristine_result = _migrate(
        pristine, _V11_CATALOG
    )
    assert pristine_result.applied_versions == tuple(range(1, 12))
    pristine_connection = pristine_factory.open(read_only=True)
    try:
        assert schema_sha256(pristine_connection) == V011_EXPECTED_SCHEMA_SHA256
    finally:
        pristine_connection.close()


def test_v11_failure_rolls_back_to_exact_v10_schema(tmp_path) -> None:
    layout = create_isolated_test_layout(
        tmp_path / "v11-rollback", user_id="v11"
    )
    v10_factory, _ = _migrate(layout, _V10_CATALOG)
    failing = Migration(
        version=11,
        name="higher_publication_authority_v11_failure_probe",
        statements=(*V011_STATEMENTS, "SELECT no_such_v11_function()"),
        expected_schema_sha256=V011_EXPECTED_SCHEMA_SHA256,
        postcondition_queries=V011_POSTCONDITION_QUERIES,
        requires_foreign_keys_off=True,
    )
    catalog = MigrationCatalog((*_V10_CATALOG.migrations, failing))
    factory = ConnectionFactory._for_isolated_tests(layout.core_db_path, catalog)
    with pytest.raises(KnowledgeCoreMigrationError):
        SingleMigrator(
            factory,
            app_version="v11-rollback-test",
            storage_layout=_layout(layout),
        ).run()
    connection = v10_factory.open(read_only=True)
    try:
        assert schema_sha256(connection) == V010_EXPECTED_SCHEMA_SHA256
        assert connection.execute(
            "SELECT max(version) FROM schema_migration"
        ).fetchone()[0] == 10
        assert connection.execute(
            """SELECT count(*) FROM sqlite_schema
                 WHERE name LIKE 'higher_publication_%'"""
        ).fetchone()[0] == 0
        table_sql = connection.execute(
            """SELECT sql FROM sqlite_schema
                 WHERE type='table' AND name='publication_operation'"""
        ).fetchone()[0]
        assert "explicit_authority_id" not in table_sql
    finally:
        connection.close()
