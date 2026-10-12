from __future__ import annotations

import sqlite3

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory, transaction
from code_assist_v1.knowledge_core.errors import KnowledgeCoreMigrationError
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationCatalog,
    MigrationStorageLayout,
    SCHEMA_V1_MIGRATION,
    SCHEMA_V2_MIGRATION,
    SCHEMA_V3_MIGRATION,
    SingleMigrator,
    V002_EXPECTED_SCHEMA_SHA256,
    V003_EXPECTED_SCHEMA_SHA256,
    schema_sha256,
)
from code_assist_v1.knowledge_core.migrations.v003_legacy_migration_map_durability import (
    LEGACY_MIGRATION_MAP_IMMUTABLE_CODE,
    LEGACY_MIGRATION_MAP_IMMUTABLE_TRIGGERS,
    LEGACY_MIGRATION_MAP_LOCATOR_INDEX,
)


pytestmark = pytest.mark.unit


_V2_CATALOG = MigrationCatalog((SCHEMA_V1_MIGRATION, SCHEMA_V2_MIGRATION))
_V3_CATALOG = MigrationCatalog(
    (SCHEMA_V1_MIGRATION, SCHEMA_V2_MIGRATION, SCHEMA_V3_MIGRATION)
)


def _layout(isolated_knowledge_layout) -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=isolated_knowledge_layout.root,
        database=isolated_knowledge_layout.core_db_path,
        blob_root=isolated_knowledge_layout.blobs_dir,
        projection_root=isolated_knowledge_layout.projections_dir,
    )


def _factory(isolated_knowledge_layout, catalog=_V3_CATALOG):
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


def _insert_mapping(
    connection,
    *,
    mapping_id: str = "00000000-0000-4000-8000-000000000001",
    migration_run_id: str = "00000000-0000-4000-8000-000000000101",
    legacy_kind: str = "wiki_source",
    normalized_path: str = "p2v1/root/path/physical_file/1",
    source_sha256: str = "a" * 64,
) -> None:
    connection.execute(
        """INSERT INTO legacy_migration_map(
             id,migration_run_id,legacy_kind,normalized_path,source_sha256,
             resolved_owner_user_id,artifact_id,item_id,revision_id,edge_id,
             status,details_json,created_at,updated_at
           ) VALUES (?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,'imported','{}',100,100)""",
        (
            mapping_id,
            migration_run_id,
            legacy_kind,
            normalized_path,
            source_sha256,
        ),
    )


def _schema_object_names(connection, object_type: str) -> tuple[str, ...]:
    return tuple(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type=? ORDER BY name",
            (object_type,),
        ).fetchall()
    )


def test_fresh_bootstrap_applies_physical_schema_v3(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    result = _migrator(
        factory,
        isolated_knowledge_layout,
        app_version="p2.3-v3-bootstrap",
    ).run()

    assert result.applied_versions == (1, 2, 3)
    assert result.schema_version == 3
    assert _V3_CATALOG.migrations == (
        SCHEMA_V1_MIGRATION,
        SCHEMA_V2_MIGRATION,
        SCHEMA_V3_MIGRATION,
    )

    connection = factory.open()
    try:
        assert schema_sha256(connection) == V003_EXPECTED_SCHEMA_SHA256
        assert connection.contract_snapshot()["pragmas"]["user_version"] == 3
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT version,name FROM schema_migration ORDER BY version"
            ).fetchall()
        ] == [
            (1, "canonical_schema_v1"),
            (2, "account_auth_policy_v2"),
            (3, "legacy_migration_map_durability_v3"),
        ]
        assert LEGACY_MIGRATION_MAP_LOCATOR_INDEX in _schema_object_names(
            connection, "index"
        )
        assert set(LEGACY_MIGRATION_MAP_IMMUTABLE_TRIGGERS).issubset(
            _schema_object_names(connection, "trigger")
        )
    finally:
        connection.close()


def test_existing_empty_v2_database_upgrades_to_v3(
    isolated_knowledge_layout,
) -> None:
    v2_factory = _factory(isolated_knowledge_layout, _V2_CATALOG)
    first = _migrator(
        v2_factory,
        isolated_knowledge_layout,
        app_version="p2.3-v2-fixture",
    ).run()
    assert first.applied_versions == (1, 2)
    assert first.schema_version == 2

    v3_factory = _factory(isolated_knowledge_layout)
    upgraded = _migrator(
        v3_factory,
        isolated_knowledge_layout,
        app_version="p2.3-v3-upgrade",
    ).run()
    assert upgraded.applied_versions == (3,)
    assert upgraded.schema_version == 3

    connection = v3_factory.open()
    try:
        assert schema_sha256(connection) == V003_EXPECTED_SCHEMA_SHA256
        assert connection.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM sqlite_schema "
            "WHERE name='legacy_migration_map_v3_preflight'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_v3_mapping_rows_reject_update_and_delete_with_stable_abort_code(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _migrator(
        factory,
        isolated_knowledge_layout,
        app_version="p2.3-v3-immutability",
    ).run()
    connection = factory.open()
    try:
        with transaction(connection):
            _insert_mapping(connection)

        with pytest.raises(
            sqlite3.IntegrityError,
            match=LEGACY_MIGRATION_MAP_IMMUTABLE_CODE,
        ):
            with transaction(connection):
                connection.execute(
                    "UPDATE legacy_migration_map SET updated_at=updated_at+1"
                )
        with pytest.raises(
            sqlite3.IntegrityError,
            match=LEGACY_MIGRATION_MAP_IMMUTABLE_CODE,
        ):
            with transaction(connection):
                connection.execute("DELETE FROM legacy_migration_map")

        assert tuple(
            connection.execute(
                "SELECT count(*) FROM legacy_migration_map"
            ).fetchone()
        ) == (1,)
    finally:
        connection.close()


def test_v3_rejects_second_effect_for_same_locator_with_different_kind_and_hash(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout)
    _migrator(
        factory,
        isolated_knowledge_layout,
        app_version="p2.3-v3-locator",
    ).run()
    connection = factory.open()
    try:
        with transaction(connection):
            _insert_mapping(connection)
        with pytest.raises(
            sqlite3.IntegrityError,
            match=r"UNIQUE constraint failed: legacy_migration_map\.normalized_path",
        ):
            with transaction(connection):
                _insert_mapping(
                    connection,
                    mapping_id="00000000-0000-4000-8000-000000000002",
                    migration_run_id="00000000-0000-4000-8000-000000000102",
                    legacy_kind="hidden_page",
                    source_sha256="b" * 64,
                )
        assert tuple(
            connection.execute(
                "SELECT count(*) FROM legacy_migration_map"
            ).fetchone()
        ) == (1,)
    finally:
        connection.close()


def test_v3_refuses_v2_database_with_duplicate_stable_locators(
    isolated_knowledge_layout,
) -> None:
    v2_factory = _factory(isolated_knowledge_layout, _V2_CATALOG)
    _migrator(
        v2_factory,
        isolated_knowledge_layout,
        app_version="p2.3-v2-duplicate-fixture",
    ).run()
    connection = v2_factory.open()
    try:
        with transaction(connection):
            _insert_mapping(connection)
            _insert_mapping(
                connection,
                mapping_id="00000000-0000-4000-8000-000000000002",
                migration_run_id="00000000-0000-4000-8000-000000000102",
                legacy_kind="hidden_page",
                source_sha256="b" * 64,
            )
    finally:
        connection.close()

    v3_factory = _factory(isolated_knowledge_layout)
    with pytest.raises(KnowledgeCoreMigrationError) as caught:
        _migrator(
            v3_factory,
            isolated_knowledge_layout,
            app_version="p2.3-v3-duplicate-rejection",
        ).run()

    causes: list[BaseException] = []
    cause: BaseException | None = caught.value
    while cause is not None:
        causes.append(cause)
        cause = cause.__cause__
    assert any(
        isinstance(value, sqlite3.IntegrityError)
        and "legacy_migration_map.normalized_path" in str(value)
        for value in causes
    )

    preserved = v2_factory.open()
    try:
        assert schema_sha256(preserved) == V002_EXPECTED_SCHEMA_SHA256
        assert preserved.contract_snapshot()["pragmas"]["user_version"] == 2
        assert tuple(
            preserved.execute(
                "SELECT count(*) FROM legacy_migration_map"
            ).fetchone()
        ) == (2,)
        assert LEGACY_MIGRATION_MAP_LOCATOR_INDEX not in _schema_object_names(
            preserved, "index"
        )
        assert not set(LEGACY_MIGRATION_MAP_IMMUTABLE_TRIGGERS).intersection(
            _schema_object_names(preserved, "trigger")
        )
        assert tuple(
            preserved.execute(
                "SELECT group_concat(version, ',') "
                "FROM schema_migration ORDER BY version"
            ).fetchone()
        ) == ("1,2",)
    finally:
        preserved.close()


def test_v3_refuses_any_preexisting_v2_mapping_row(
    isolated_knowledge_layout,
) -> None:
    v2_factory = _factory(isolated_knowledge_layout, _V2_CATALOG)
    _migrator(
        v2_factory,
        isolated_knowledge_layout,
        app_version="p2.3-v2-nonempty-fixture",
    ).run()
    connection = v2_factory.open()
    try:
        with transaction(connection):
            _insert_mapping(connection)
    finally:
        connection.close()

    v3_factory = _factory(isolated_knowledge_layout)
    with pytest.raises(KnowledgeCoreMigrationError) as caught:
        _migrator(
            v3_factory,
            isolated_knowledge_layout,
            app_version="p2.3-v3-nonempty-rejection",
        ).run()

    causes: list[BaseException] = []
    cause: BaseException | None = caught.value
    while cause is not None:
        causes.append(cause)
        cause = cause.__cause__
    assert any(
        isinstance(value, sqlite3.IntegrityError)
        and "LEGACY_MIGRATION_MAP_EMPTY_BEFORE_V3" in str(value)
        for value in causes
    )

    preserved = v2_factory.open()
    try:
        assert schema_sha256(preserved) == V002_EXPECTED_SCHEMA_SHA256
        assert preserved.contract_snapshot()["pragmas"]["user_version"] == 2
        assert preserved.execute(
            "SELECT count(*) FROM legacy_migration_map"
        ).fetchone()[0] == 1
        assert preserved.execute(
            "SELECT count(*) FROM sqlite_schema "
            "WHERE name='legacy_migration_map_v3_preflight'"
        ).fetchone()[0] == 0
    finally:
        preserved.close()


def test_v3_migrator_rerun_is_idempotent(isolated_knowledge_layout) -> None:
    factory = _factory(isolated_knowledge_layout)
    migrator = _migrator(
        factory,
        isolated_knowledge_layout,
        app_version="p2.3-v3-idempotency",
    )
    first = migrator.run()
    second = migrator.run()

    assert first.applied_versions == (1, 2, 3)
    assert second.applied_versions == ()
    assert second.schema_version == 3
    assert second.catalog_checksum_sha256 == first.catalog_checksum_sha256
