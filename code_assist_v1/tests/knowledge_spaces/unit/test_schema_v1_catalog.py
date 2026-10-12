from __future__ import annotations

import sqlite3

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory
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
    SCHEMA_V6_MIGRATION,
    SCHEMA_V7_MIGRATION,
    SCHEMA_V8_MIGRATION,
    SCHEMA_V9_MIGRATION,
    SCHEMA_V10_MIGRATION,
    SCHEMA_V11_MIGRATION,
    SCHEMA_V12_MIGRATION,
    SCHEMA_V13_MIGRATION,
    SingleMigrator,
    V001_EXPECTED_SCHEMA_SHA256,
    V002_EXPECTED_SCHEMA_SHA256,
    V003_EXPECTED_SCHEMA_SHA256,
    V004_EXPECTED_SCHEMA_SHA256,
    V005_EXPECTED_SCHEMA_SHA256,
    V006_EXPECTED_SCHEMA_SHA256,
    V007_EXPECTED_SCHEMA_SHA256,
    V008_EXPECTED_SCHEMA_SHA256,
    V009_EXPECTED_SCHEMA_SHA256,
    V010_EXPECTED_SCHEMA_SHA256,
    V011_EXPECTED_SCHEMA_SHA256,
    V012_EXPECTED_SCHEMA_SHA256,
    V013_EXPECTED_SCHEMA_SHA256,
    build_cumulative_schema_sha256,
    schema_sha256,
)
from code_assist_v1.knowledge_core.migrations.v001_fts import FTS_TRIGGER_NAMES
from code_assist_v1.knowledge_core.migrations.v001_indexes import INDEX_NAMES
from code_assist_v1.knowledge_core.migrations.v001_invariants import (
    POSTCONDITION_DEFINITIONS,
    POSTCONDITION_NAMES,
)
from code_assist_v1.knowledge_core.migrations.v001_seed import (
    RESPONSIBILITY_ROOT_ID,
    RESPONSIBILITY_ROOT_NAME,
)
from code_assist_v1.knowledge_core.migrations.v001_tables import TABLE_NAMES
from code_assist_v1.knowledge_core.migrations.v001_triggers import TRIGGER_NAMES


pytestmark = [pytest.mark.unit, pytest.mark.scenario("SCHEMA-008")]


def _layout(isolated_knowledge_layout) -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=isolated_knowledge_layout.root,
        database=isolated_knowledge_layout.core_db_path,
        blob_root=isolated_knowledge_layout.blobs_dir,
        projection_root=isolated_knowledge_layout.projections_dir,
    )


def _factory(isolated_knowledge_layout, catalog=CANONICAL_MIGRATION_CATALOG):
    return ConnectionFactory._for_isolated_tests(
        isolated_knowledge_layout.core_db_path,
        catalog,
    )


def test_schema_008_frozen_v1_and_forward_catalog_order_and_fingerprints() -> None:
    assert CANONICAL_MIGRATION_CATALOG.migrations == (
        SCHEMA_V1_MIGRATION,
        SCHEMA_V2_MIGRATION,
        SCHEMA_V3_MIGRATION,
        SCHEMA_V4_MIGRATION,
        SCHEMA_V5_MIGRATION,
        SCHEMA_V6_MIGRATION,
        SCHEMA_V7_MIGRATION,
        SCHEMA_V8_MIGRATION,
        SCHEMA_V9_MIGRATION,
        SCHEMA_V10_MIGRATION,
        SCHEMA_V11_MIGRATION,
        SCHEMA_V12_MIGRATION,
        SCHEMA_V13_MIGRATION,
    )
    assert SCHEMA_V1_MIGRATION.version == 1
    assert SCHEMA_V1_MIGRATION.name == "canonical_schema_v1"
    assert SCHEMA_V1_MIGRATION.checksum_sha256 == (
        "4a1dcdd7617c5c8f6d67b0ae82450dbc990259a118e4cd675918770b8696b290"
    )
    assert SCHEMA_V2_MIGRATION.checksum_sha256 == (
        "bd814f33d0c0ec619acb877727bb4446047ed3c41eb81954a4d26b2dfa42bd4d"
    )
    assert SCHEMA_V3_MIGRATION.checksum_sha256 == (
        "d645a86064b4ae886a156c8ccb8eca3a2e5c45bcf58ce7eefa08a1c49af0fe43"
    )
    assert SCHEMA_V4_MIGRATION.checksum_sha256 == (
        "1557507d1d86390bac2fa4267217850bd2459df58aa1b702938b3ea4fad9f242"
    )
    assert SCHEMA_V5_MIGRATION.checksum_sha256 == (
        "feb79ac03f3882a5f78de1355c7c4ad2202e2c13da5260f4a6a1457995b86832"
    )
    assert SCHEMA_V6_MIGRATION.checksum_sha256 == (
        "1b5790ac762ac81cd120907d367fb791e0f8d3f0d58000fa4d4fe7d9bbf1e60c"
    )
    assert SCHEMA_V7_MIGRATION.checksum_sha256 == (
        "4f2b5f54b89bf0e3faf73aeb1096609c802a0543498b2e69619f071f9d86c440"
    )
    assert SCHEMA_V8_MIGRATION.checksum_sha256 == (
        "3f88b2a47c5fceb375535ac41de2332fc394b673a0dea88b63f145d1f0310ccb"
    )
    assert SCHEMA_V9_MIGRATION.checksum_sha256 == (
        "1ca3f06c06ad6155bc6a1cb860f36503d302dc3ea02cacc6b1f30a75d495291b"
    )
    assert SCHEMA_V10_MIGRATION.checksum_sha256 == (
        "b433ec346f55c5fd6634001f48a28a6cba1947cc2a6967b6b36fea25d6b92943"
    )
    assert SCHEMA_V11_MIGRATION.checksum_sha256 == (
        "d584077e9e774129998b154ddc0b056c2db621ee07ea418185352b1d4b494238"
    )
    assert SCHEMA_V12_MIGRATION.checksum_sha256 == (
        "35defab930534550dd622dfd18bac0edf11711e4fb57bb5ad799a97b0c1769e9"
    )
    assert SCHEMA_V13_MIGRATION.checksum_sha256 == (
        "e5a35fecf7005c240b0b48002c354c10446c28025b85af6b1eef37ba4092d711"
    )
    assert CANONICAL_MIGRATION_CATALOG.checksum_sha256 == (
        "04fa286a9e011310e01951a76c103b964b6696281b8bf370d5fa05b247c97668"
    )
    assert len(TABLE_NAMES) == len(set(TABLE_NAMES)) == 46
    assert len(INDEX_NAMES) == len(set(INDEX_NAMES)) == 120
    assert len(TRIGGER_NAMES) == len(set(TRIGGER_NAMES)) == 131
    assert len(FTS_TRIGGER_NAMES) == len(set(FTS_TRIGGER_NAMES)) == 3
    assert len(set((*TRIGGER_NAMES, *FTS_TRIGGER_NAMES))) == 134
    assert len(POSTCONDITION_NAMES) == len(set(POSTCONDITION_NAMES)) == 16

    observed = build_cumulative_schema_sha256(
        (SCHEMA_V1_MIGRATION.statements,),
        require_approved_runtime=False,
    )
    assert observed == (V001_EXPECTED_SCHEMA_SHA256,)
    assert SCHEMA_V1_MIGRATION.expected_schema_sha256 == (
        "694fa29e2e184c8ab546e7fb603cf014bc1eb58ded639504548f9fa3f920761e"
    )
    cumulative = build_cumulative_schema_sha256(
        (
            SCHEMA_V1_MIGRATION.statements,
            SCHEMA_V2_MIGRATION.statements,
            SCHEMA_V3_MIGRATION.statements,
            SCHEMA_V4_MIGRATION.statements,
            SCHEMA_V5_MIGRATION.statements,
            SCHEMA_V6_MIGRATION.statements,
            SCHEMA_V7_MIGRATION.statements,
            SCHEMA_V8_MIGRATION.statements,
            SCHEMA_V9_MIGRATION.statements,
            SCHEMA_V10_MIGRATION.statements,
            SCHEMA_V11_MIGRATION.statements,
            SCHEMA_V12_MIGRATION.statements,
            SCHEMA_V13_MIGRATION.statements,
        ),
        require_approved_runtime=False,
        requires_foreign_keys_off_versions=frozenset({9, 10, 11}),
    )
    assert cumulative == (
        V001_EXPECTED_SCHEMA_SHA256,
        V002_EXPECTED_SCHEMA_SHA256,
        V003_EXPECTED_SCHEMA_SHA256,
        V004_EXPECTED_SCHEMA_SHA256,
        V005_EXPECTED_SCHEMA_SHA256,
        V006_EXPECTED_SCHEMA_SHA256,
        V007_EXPECTED_SCHEMA_SHA256,
        V008_EXPECTED_SCHEMA_SHA256,
        V009_EXPECTED_SCHEMA_SHA256,
        V010_EXPECTED_SCHEMA_SHA256,
        V011_EXPECTED_SCHEMA_SHA256,
        V012_EXPECTED_SCHEMA_SHA256,
        V013_EXPECTED_SCHEMA_SHA256,
    )


def test_schema_001_to_013_canonical_migration_bootstrap_reopen_and_retry(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    migrator = SingleMigrator(
        factory,
        app_version="p1.3-test",
        storage_layout=_layout(isolated_knowledge_layout),
    )
    first = migrator.run()
    assert first.applied_versions == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13)
    assert first.schema_version == 13
    assert first.runtime_override_for_tests is True

    connection = factory.open()
    try:
        assert schema_sha256(connection) == V013_EXPECTED_SCHEMA_SHA256
        assert connection.execute(
            "SELECT count(*) FROM sqlite_schema "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0] == 73
        assert connection.execute(
            "SELECT count(*) FROM sqlite_schema "
            "WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0] == 187
        assert connection.execute(
            "SELECT count(*) FROM sqlite_schema "
            "WHERE type='trigger' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0] == 214
        columns = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name='app_user'"
        ).fetchone()[0]
        assert "must_change_password" in columns
        assert "password_changed_at" in columns
        seed_rows = connection.execute(
            "SELECT id,name,name_key,kind,status FROM org_unit"
        ).fetchall()
        assert [tuple(row) for row in seed_rows] == [
            (
                RESPONSIBILITY_ROOT_ID,
                RESPONSIBILITY_ROOT_NAME,
                RESPONSIBILITY_ROOT_NAME,
                "responsibility",
                "active",
            )
        ]
        for name, query in POSTCONDITION_DEFINITIONS:
            assert connection.execute(query).fetchone() is None, name
    finally:
        connection.close()

    second = migrator.run()
    assert second.applied_versions == ()
    assert second.catalog_checksum_sha256 == first.catalog_checksum_sha256


def test_schema_008_canonical_postcondition_failure_rolls_back_every_object(
    isolated_knowledge_layout,
) -> None:
    failing = Migration(
        version=1,
        name="canonical_schema_v1_postcondition_probe",
        statements=SCHEMA_V1_MIGRATION.statements,
        expected_schema_sha256=V001_EXPECTED_SCHEMA_SHA256,
        postcondition_queries=("SELECT 'forced invariant failure'",),
    )
    factory = _factory(isolated_knowledge_layout, MigrationCatalog((failing,)))
    with pytest.raises(KnowledgeCoreMigrationError):
        SingleMigrator(
            factory,
            app_version="p1.3-test",
            storage_layout=_layout(isolated_knowledge_layout),
        ).run()

    raw = sqlite3.connect(isolated_knowledge_layout.core_db_path)
    try:
        assert raw.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchone() == (0,)
        assert raw.execute("PRAGMA user_version").fetchone() == (0,)
    finally:
        raw.close()


def test_schema_008_canonical_statement_failure_rolls_back_seed_and_schema(
    isolated_knowledge_layout,
) -> None:
    failing = Migration(
        version=1,
        name="canonical_schema_v1_statement_probe",
        statements=(*SCHEMA_V1_MIGRATION.statements, "SELECT no_such_function()"),
        expected_schema_sha256=V001_EXPECTED_SCHEMA_SHA256,
    )
    factory = _factory(isolated_knowledge_layout, MigrationCatalog((failing,)))
    with pytest.raises(KnowledgeCoreMigrationError):
        SingleMigrator(
            factory,
            app_version="p1.3-test",
            storage_layout=_layout(isolated_knowledge_layout),
        ).run()

    raw = sqlite3.connect(isolated_knowledge_layout.core_db_path)
    try:
        assert raw.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchone() == (0,)
        assert raw.execute("PRAGMA user_version").fetchone() == (0,)
    finally:
        raw.close()


@pytest.mark.parametrize(
    "environment_name",
    ("CODE_ASSIST_FAILPOINTS", "CODE_ASSIST_FAILPOINT_schema_v1"),
)
def test_schema_008_migrator_rejects_failpoints_before_database_creation(
    isolated_knowledge_layout,
    monkeypatch,
    environment_name: str,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    monkeypatch.setenv(environment_name, "secret-value-must-not-be-reported")
    with pytest.raises(KnowledgeCoreMigrationError, match="Failpoint configuration"):
        SingleMigrator(
            factory,
            app_version="p1.3-test",
            storage_layout=_layout(isolated_knowledge_layout),
        ).run()
    assert not isolated_knowledge_layout.core_db_path.exists()
