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
    V011_EXPECTED_SCHEMA_SHA256,
    V012_EXPECTED_SCHEMA_SHA256,
    schema_sha256,
    verify_canonical_integrity,
)
from code_assist_v1.knowledge_core.migrations.v012_temporal_projection_footer import (
    V012_POSTCONDITION_QUERIES,
    V012_STATEMENTS,
)
from code_assist_v1.test_safety import create_isolated_test_layout


pytestmark = pytest.mark.unit
_V11_CATALOG = MigrationCatalog(CANONICAL_MIGRATION_CATALOG.migrations[:11])
_V12_CATALOG = MigrationCatalog(CANONICAL_MIGRATION_CATALOG.migrations[:12])


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
        app_version="v12-temporal-migration-test",
        storage_layout=_layout(value),
    ).run()
    return factory, result


@pytest.mark.scenario("TIME-001")
def test_v11_upgrade_matches_pristine_v12_and_is_idempotent(
    tmp_path, isolated_knowledge_layout
) -> None:
    v11_factory, initial = _migrate(isolated_knowledge_layout, _V11_CATALOG)
    assert initial.applied_versions == tuple(range(1, 12))
    connection = v11_factory.open(read_only=True)
    try:
        assert schema_sha256(connection) == V011_EXPECTED_SCHEMA_SHA256
    finally:
        connection.close()

    upgraded, migrated = _migrate(
        isolated_knowledge_layout, _V12_CATALOG
    )
    assert migrated.applied_versions == (12,)
    connection = upgraded.open(read_only=True)
    try:
        assert schema_sha256(connection) == V012_EXPECTED_SCHEMA_SHA256
        assert "temporal_generation" in connection.execute(
            "SELECT sql FROM sqlite_schema "
            "WHERE type='table' AND name='knowledge_space'"
        ).fetchone()[0]
        assertion_sql = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' "
            "AND name='revision_temporal_assertion'"
        ).fetchone()[0]
        assert "substr(date_value,9,2)" in assertion_sql
        assert "%400=0" in assertion_sql
        assert connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE type='table' AND name IN "
            "('revision_temporal_assertion','revision_temporal_head',"
            "'revision_verification_attestation','revision_verification_head',"
            "'projection_manifest_v2_ledger')"
        ).fetchone()[0] == 5
        verify_canonical_integrity(connection, _V12_CATALOG)
    finally:
        connection.close()

    _same, no_op = _migrate(
        isolated_knowledge_layout, _V12_CATALOG
    )
    assert no_op.applied_versions == ()

    pristine = create_isolated_test_layout(
        tmp_path / "v12-pristine", user_id="v12"
    )
    pristine_factory, pristine_result = _migrate(
        pristine, _V12_CATALOG
    )
    assert pristine_result.applied_versions == tuple(range(1, 13))
    pristine_connection = pristine_factory.open(read_only=True)
    try:
        assert schema_sha256(pristine_connection) == V012_EXPECTED_SCHEMA_SHA256
    finally:
        pristine_connection.close()


@pytest.mark.scenario("TIME-001")
def test_v12_failure_rolls_back_to_exact_v11_schema(tmp_path) -> None:
    layout = create_isolated_test_layout(
        tmp_path / "v12-rollback", user_id="v12"
    )
    v11_factory, _ = _migrate(layout, _V11_CATALOG)
    failing = Migration(
        version=12,
        name="temporal_provenance_v12_failure_probe",
        statements=(*V012_STATEMENTS, "SELECT no_such_v12_function()"),
        expected_schema_sha256=V012_EXPECTED_SCHEMA_SHA256,
        postcondition_queries=V012_POSTCONDITION_QUERIES,
    )
    catalog = MigrationCatalog((*_V11_CATALOG.migrations, failing))
    factory = ConnectionFactory._for_isolated_tests(layout.core_db_path, catalog)
    with pytest.raises(KnowledgeCoreMigrationError):
        SingleMigrator(
            factory,
            app_version="v12-rollback-test",
            storage_layout=_layout(layout),
        ).run()
    connection = v11_factory.open(read_only=True)
    try:
        assert schema_sha256(connection) == V011_EXPECTED_SCHEMA_SHA256
        assert connection.execute(
            "SELECT max(version) FROM schema_migration"
        ).fetchone()[0] == 11
        assert "temporal_generation" not in connection.execute(
            "SELECT sql FROM sqlite_schema "
            "WHERE type='table' AND name='knowledge_space'"
        ).fetchone()[0]
        assert connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name LIKE 'revision_temporal_%'"
        ).fetchone()[0] == 0
    finally:
        connection.close()
