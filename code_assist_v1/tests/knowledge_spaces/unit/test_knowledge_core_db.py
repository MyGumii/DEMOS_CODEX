from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from code_assist_v1.knowledge_core.db import (
    ConnectionFactory,
    ManagedConnection,
    TransactionMode,
    _probe_fts5,
    _probe_json,
    db_now_ms,
    transaction,
)
from code_assist_v1.knowledge_core.errors import (
    KnowledgeCoreDatabasePathError,
    KnowledgeCoreMigrationError,
    KnowledgeCorePragmaMismatchError,
    KnowledgeCoreRuntimeMismatchError,
    KnowledgeCoreSchemaMismatchError,
    KnowledgeCoreTransactionError,
)
from code_assist_v1.knowledge_core.migrations import (
    Migration,
    MigrationCatalog,
    MigrationStorageLayout,
    SingleMigrator,
    build_cumulative_schema_sha256,
)
from code_assist_v1.knowledge_core.runtime import (
    APPROVED_SQLITE_SOURCE_ID,
    APPROVED_SQLITE_VERSION,
    EXPECTED_CONNECTION_PRAGMAS,
)
from code_assist_v1.tools.runtime_preflight import (
    APPROVED_SQLITE_SOURCE_ID as PREFLIGHT_SOURCE_ID,
    APPROVED_SQLITE_VERSION as PREFLIGHT_VERSION,
    EXPECTED_PRAGMAS as PREFLIGHT_PRAGMAS,
)
from code_assist_v1.tools import windows_volume
from code_assist_v1.test_safety import UnsafeTestDataPathError


_LEDGER_SQL = """
CREATE TABLE schema_migration(
    version INTEGER PRIMARY KEY CHECK(version >= 1),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
    checksum_sha256 TEXT NOT NULL CHECK(
        length(checksum_sha256)=64
        AND checksum_sha256=lower(checksum_sha256)
        AND checksum_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    applied_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(applied_at >= 0),
    app_version TEXT NOT NULL CHECK(length(app_version) BETWEEN 1 AND 80),
    sqlite_version TEXT NOT NULL,
    sqlite_source_id TEXT NOT NULL
) STRICT
"""

_PARENT_SQL = "CREATE TABLE parent(id INTEGER PRIMARY KEY, value TEXT NOT NULL) STRICT"
_CHILD_SQL = """
CREATE TABLE child(
    id INTEGER PRIMARY KEY,
    parent_id INTEGER NOT NULL REFERENCES parent(id) ON DELETE RESTRICT
) STRICT
"""


def _expected_schema_sha256(statements: tuple[str, ...]) -> str:
    return build_cumulative_schema_sha256(
        (statements,),
        require_approved_runtime=False,
    )[0]


def _v1(
    *,
    extra_statement: str | None = None,
    postcondition: str | None = None,
    expected_schema_sha256: str | None = None,
) -> Migration:
    statements = [_LEDGER_SQL, _PARENT_SQL, _CHILD_SQL]
    if extra_statement is not None:
        statements.append(extra_statement)
    normalized = tuple(statement.strip() for statement in statements)
    return Migration(
        1,
        "fixture_core",
        normalized,
        expected_schema_sha256 or _expected_schema_sha256(normalized),
        () if postcondition is None else (postcondition,),
    )


def _catalog(
    *,
    extra_statement: str | None = None,
    postcondition: str | None = None,
    expected_schema_sha256: str | None = None,
) -> MigrationCatalog:
    return MigrationCatalog(
        (
            _v1(
                extra_statement=extra_statement,
                postcondition=postcondition,
                expected_schema_sha256=expected_schema_sha256,
            ),
        )
    )


def _factory(path: Path, catalog: MigrationCatalog | None = None) -> ConnectionFactory:
    return ConnectionFactory._for_isolated_tests(path, catalog or _catalog())


def _storage_layout(path: Path) -> MigrationStorageLayout:
    data_root = path.parent.parent
    return MigrationStorageLayout(
        data_root=data_root,
        database=path,
        blob_root=data_root / "blobs",
        projection_root=data_root / "projections",
    )


def _migrator(factory: ConnectionFactory, *, app_version: str) -> SingleMigrator:
    return SingleMigrator(
        factory,
        app_version=app_version,
        storage_layout=_storage_layout(factory.database_path),
    )


def _migrate(path: Path, catalog: MigrationCatalog | None = None):
    factory = _factory(path, catalog)
    result = _migrator(factory, app_version="p1.2-test").run()
    return factory, result


@pytest.mark.unit
def test_p1_2_runtime_contract_is_shared_with_preflight() -> None:
    assert APPROVED_SQLITE_VERSION == PREFLIGHT_VERSION == "3.53.4"
    assert APPROVED_SQLITE_SOURCE_ID == PREFLIGHT_SOURCE_ID
    assert EXPECTED_CONNECTION_PRAGMAS == PREFLIGHT_PRAGMAS

    statements = tuple(statement.strip() for statement in (_LEDGER_SQL, _PARENT_SQL))
    generated = build_cumulative_schema_sha256(
        (statements,),
        require_approved_runtime=False,
    )
    assert generated == (_expected_schema_sha256(statements),)

    memory = sqlite3.connect(":memory:")
    try:
        source_id = memory.execute("SELECT sqlite_source_id()").fetchone()[0]
    finally:
        memory.close()
    if (
        sqlite3.sqlite_version != APPROVED_SQLITE_VERSION
        or source_id != APPROVED_SQLITE_SOURCE_ID
    ):
        with pytest.raises(KnowledgeCoreRuntimeMismatchError):
            build_cumulative_schema_sha256((statements,))


@pytest.mark.unit
def test_p1_2_migration_and_catalog_checksums_are_deterministic() -> None:
    first = _catalog()
    second = _catalog()
    changed = _catalog(extra_statement="CREATE TABLE extra(id INTEGER) STRICT")
    assert first == second
    assert len(first.checksum_sha256) == 64
    assert first.checksum_sha256 == second.checksum_sha256
    assert first.migrations[0].checksum_sha256 == second.migrations[0].checksum_sha256
    assert changed.checksum_sha256 != first.checksum_sha256
    assert changed.migrations[0].checksum_sha256 != first.migrations[0].checksum_sha256
    rebuild = Migration(
        1,
        "fixture_core",
        first.migrations[0].statements,
        first.migrations[0].expected_schema_sha256,
        first.migrations[0].postcondition_queries,
        requires_foreign_keys_off=True,
    )
    assert rebuild.requires_foreign_keys_off is True
    assert rebuild.checksum_sha256 != first.migrations[0].checksum_sha256


@pytest.mark.unit
@pytest.mark.parametrize(
    "migration",
    (
        lambda: Migration(0, "bad", ("SELECT 1",), "0" * 64),
        lambda: Migration(1, "Bad-Name", ("SELECT 1",), "0" * 64),
        lambda: Migration(1, "empty", (), "0" * 64),
        lambda: Migration(1, "transaction", ("BEGIN IMMEDIATE",), "0" * 64),
        lambda: Migration(1, "script", ("PRAGMA journal_mode=WAL",), "0" * 64),
        lambda: Migration(1, "pragma", ("PRAGMA writable_schema=ON",), "0" * 64),
        lambda: Migration(
            1, "comment_bypass", ("-- comment\nBEGIN IMMEDIATE",), "0" * 64
        ),
        lambda: Migration(1, "bom_bypass", ("\ufeffPRAGMA application_id=1",), "0" * 64),
        lambda: Migration(
            1,
            "postcondition",
            ("SELECT 1",),
            "0" * 64,
            ("DELETE FROM x",),
        ),
        lambda: Migration(
            1,
            "invalid_rebuild_mode",
            ("SELECT 1",),
            "0" * 64,
            requires_foreign_keys_off="yes",  # type: ignore[arg-type]
        ),
    ),
)
def test_p1_2_invalid_migration_definitions_fail_before_io(migration) -> None:
    with pytest.raises(KnowledgeCoreMigrationError):
        migration()


@pytest.mark.unit
def test_p1_2_catalog_requires_contiguous_order() -> None:
    first = _v1()
    third_sql = "CREATE TABLE third(id INTEGER) STRICT"
    third = Migration(
        3,
        "third",
        (third_sql,),
        _expected_schema_sha256((*first.statements, third_sql)),
    )
    with pytest.raises(KnowledgeCoreMigrationError):
        MigrationCatalog((first, third))


@pytest.mark.unit
def test_p1_2_migrator_requires_nonblank_bounded_app_version(
    isolated_knowledge_layout,
) -> None:
    factory = _factory(isolated_knowledge_layout.core_db_path)
    with pytest.raises(KnowledgeCoreMigrationError):
        SingleMigrator(  # type: ignore[arg-type]
            None,
            app_version="p1.2-test",
            storage_layout=_storage_layout(isolated_knowledge_layout.core_db_path),
        )
    with pytest.raises(TypeError):
        SingleMigrator(factory, app_version="p1.2-test")
    for invalid in ("", "   ", "line\nbreak", "x" * 81):
        with pytest.raises(KnowledgeCoreMigrationError):
            _migrator(factory, app_version=invalid)


@pytest.mark.unit
def test_p1_2_test_override_is_confined_to_exact_isolated_root(
    monkeypatch: pytest.MonkeyPatch,
    isolated_data_root: Path,
) -> None:
    inside = isolated_data_root / "core" / "inside.sqlite"
    inside.parent.mkdir(exist_ok=True)
    (isolated_data_root / "blobs").mkdir(exist_ok=True)
    (isolated_data_root / "projections").mkdir(exist_ok=True)
    guarded_factory = _factory(inside)
    assert guarded_factory.database_path == inside.resolve(strict=False)
    with pytest.raises(AttributeError):
        guarded_factory._test_runtime_override = True  # type: ignore[attr-defined]

    monkeypatch.setenv("CODE_ASSIST_TEST_MODE", "0")
    with pytest.raises(KnowledgeCoreRuntimeMismatchError):
        _migrator(guarded_factory, app_version="p1.2-test").run()
    assert not inside.exists()
    with pytest.raises(KnowledgeCoreRuntimeMismatchError):
        _factory(inside)
    monkeypatch.setenv("CODE_ASSIST_TEST_MODE", "1")

    outside = isolated_data_root.parent / "outside.sqlite"
    with pytest.raises(UnsafeTestDataPathError):
        _factory(outside)

    with pytest.raises(TypeError):
        class UnsafeRuntimeFactory(ConnectionFactory):
            pass


@pytest.mark.unit
def test_p1_2_factory_construction_and_ordinary_open_never_create_database(
    isolated_knowledge_layout,
) -> None:
    path = isolated_knowledge_layout.core_db_path
    factory = _factory(path)
    assert not path.exists()
    with pytest.raises(KnowledgeCoreDatabasePathError):
        factory.open()
    assert not path.exists()
    with pytest.raises(KnowledgeCoreMigrationError):
        factory._open_for_migration(object())
    assert not path.exists()
    with pytest.raises(KnowledgeCoreDatabasePathError):
        ConnectionFactory(Path("relative.sqlite"), _catalog())
    with pytest.raises(KnowledgeCoreMigrationError):
        ConnectionFactory(path, None)  # type: ignore[arg-type]
    with pytest.raises(KnowledgeCoreMigrationError):
        ConnectionFactory(path, MigrationCatalog(()))


@pytest.mark.unit
def test_p1_2_migrator_requires_existing_parent_and_does_not_create_it(
    isolated_data_root: Path,
) -> None:
    path = isolated_data_root / "missing-parent" / "core.sqlite"
    factory = _factory(path)
    with pytest.raises(KnowledgeCoreDatabasePathError):
        _migrator(factory, app_version="p1.2-test").run()
    assert not path.parent.exists()
    assert not path.exists()


@pytest.mark.unit
def test_p1_2_single_migrator_sets_wal_records_ledger_and_is_idempotent(
    isolated_knowledge_layout,
) -> None:
    path = isolated_knowledge_layout.core_db_path
    factory, first = _migrate(path)
    second = _migrator(factory, app_version="p1.2-test").run()

    assert first.applied_versions == (1,)
    assert second.applied_versions == ()
    assert first.schema_version == second.schema_version == 1
    assert first.catalog_checksum_sha256 == second.catalog_checksum_sha256
    assert first.runtime_override_for_tests is True

    connection = factory.open()
    try:
        assert connection.isolation_level is None
        assert connection.in_transaction is False
        assert connection.test_runtime_override is True
        assert connection.runtime_facts is not None
        assert connection.runtime_facts.version == sqlite3.sqlite_version
        snapshot = connection.contract_snapshot()
        assert snapshot["no_checkpoint_on_close"] is True
        pragmas = snapshot["pragmas"]
        assert isinstance(pragmas, dict)
        assert {name: pragmas[name] for name in EXPECTED_CONNECTION_PRAGMAS} == (
            EXPECTED_CONNECTION_PRAGMAS
        )
        row = connection.execute(
            "SELECT version,name,checksum_sha256,app_version,sqlite_version,"
            "sqlite_source_id FROM schema_migration"
        ).fetchone()
        assert tuple(row) == (
            1,
            "fixture_core",
            factory.migration_catalog.migrations[0].checksum_sha256,
            "p1.2-test",
            APPROVED_SQLITE_VERSION,
            APPROVED_SQLITE_SOURCE_ID,
        )
        assert pragmas["user_version"] == 1
    finally:
        connection.close()


@pytest.mark.unit
def test_p1_2_unicode_and_space_database_path_round_trips(
    isolated_data_root: Path,
) -> None:
    parent = isolated_data_root / "P1.2 유니코드 space"
    parent.mkdir()
    (isolated_data_root / "blobs").mkdir(exist_ok=True)
    (isolated_data_root / "projections").mkdir(exist_ok=True)
    path = parent / "지식 core.sqlite"
    factory, result = _migrate(path)
    assert result.applied_versions == (1,)
    connection = factory.open()
    try:
        assert connection.database_path == path.resolve()
    finally:
        connection.close()


@pytest.mark.unit
def test_p1_2_read_only_connection_enforces_query_only(isolated_knowledge_layout) -> None:
    factory, _ = _migrate(isolated_knowledge_layout.core_db_path)
    connection = factory.open(read_only=True)
    try:
        assert connection.contract_snapshot()["pragmas"]["query_only"] == 1
        assert connection.execute("SELECT count(*) FROM parent").fetchone()[0] == 0
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("INSERT INTO parent(id,value) VALUES (1,'blocked')")
    finally:
        connection.close()


@pytest.mark.unit
def test_p1_2_ordinary_open_does_not_migrate_empty_wal_database(
    isolated_knowledge_layout,
) -> None:
    path = isolated_knowledge_layout.core_db_path
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
    finally:
        raw.close()

    with pytest.raises(KnowledgeCoreSchemaMismatchError):
        _factory(path).open()
    raw = sqlite3.connect(path)
    try:
        objects = raw.execute(
            "SELECT name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        assert objects == []
        assert raw.execute("PRAGMA user_version").fetchone() == (0,)
    finally:
        raw.close()


@pytest.mark.unit
def test_p1_2_ordinary_open_verifies_but_never_changes_journal_mode(
    isolated_knowledge_layout,
) -> None:
    path = isolated_knowledge_layout.core_db_path
    raw = sqlite3.connect(path)
    try:
        raw.execute("CREATE TABLE legacy_unknown(id INTEGER)")
        assert raw.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    finally:
        raw.close()

    with pytest.raises(KnowledgeCorePragmaMismatchError):
        _factory(path).open()
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
    finally:
        raw.close()


@pytest.mark.unit
def test_p1_2_transaction_commit_rollback_and_database_clock(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate(isolated_knowledge_layout.core_db_path)
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute("INSERT INTO parent(id,value) VALUES (1,'committed')")
            assert db_now_ms(connection) >= 0
        assert connection.execute("SELECT value FROM parent WHERE id=1").fetchone()[0] == "committed"

        with pytest.raises(RuntimeError):
            with transaction(connection, TransactionMode.IMMEDIATE):
                connection.execute("INSERT INTO parent(id,value) VALUES (2,'rolled-back')")
                raise RuntimeError("test rollback")
        assert connection.execute("SELECT count(*) FROM parent WHERE id=2").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.unit
def test_p1_2_cached_dml_cannot_reuse_transaction_authority(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate(isolated_knowledge_layout.core_db_path)
    connection = factory.open()
    statement = "INSERT INTO parent(id,value) VALUES (?,?)"
    try:
        with transaction(connection):
            connection.execute(statement, (20, "inside-managed-transaction"))

        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(statement, (21, "outside-managed-transaction"))

        rows = connection.execute(
            "SELECT id,value FROM parent WHERE id IN (20,21) ORDER BY id"
        ).fetchall()
        assert [tuple(row) for row in rows] == [(20, "inside-managed-transaction")]
    finally:
        connection.close()


@pytest.mark.unit
def test_p1_2_sqlite_auto_rollback_clears_authority_and_connection_remains_reusable(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate(isolated_knowledge_layout.core_db_path)
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                "INSERT INTO parent(id,value) VALUES (?,?)",
                (30, "conflict-sentinel"),
            )

        with pytest.raises(sqlite3.IntegrityError):
            with transaction(connection):
                connection.execute(
                    "INSERT OR ROLLBACK INTO parent(id,value) VALUES (?,?)",
                    (30, "must-auto-rollback"),
                )

        assert connection.in_transaction is False
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(
                "INSERT INTO parent(id,value) VALUES (?,?) /* outside-after-rollback */",
                (31, "must-be-denied"),
            )
        assert connection.execute(
            "SELECT count(*) FROM parent WHERE id=31"
        ).fetchone()[0] == 0

        with transaction(connection):
            connection.execute(
                "INSERT INTO parent(id,value) VALUES (?,?)",
                (32, "reused-after-auto-rollback"),
            )
        assert connection.execute(
            "SELECT value FROM parent WHERE id=32"
        ).fetchone()[0] == "reused-after-auto-rollback"
    finally:
        connection.close()


@pytest.mark.unit
def test_p1_2_caught_sqlite_auto_rollback_cannot_autocommit_inside_owner_block(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate(isolated_knowledge_layout.core_db_path)
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                "INSERT INTO parent(id,value) VALUES (?,?)",
                (40, "conflict-sentinel"),
            )

        with pytest.raises(KnowledgeCoreTransactionError):
            with transaction(connection):
                try:
                    connection.execute(
                        "INSERT OR ROLLBACK INTO parent(id,value) VALUES (?,?)",
                        (40, "must-auto-rollback"),
                    )
                except sqlite3.IntegrityError:
                    pass

                with pytest.raises(sqlite3.DatabaseError):
                    connection.execute(
                        "INSERT INTO parent(id,value) VALUES (?,?)",
                        (41, "must-not-autocommit"),
                    )

        assert connection.execute(
            "SELECT count(*) FROM parent WHERE id=41"
        ).fetchone()[0] == 0
        with transaction(connection):
            connection.execute(
                "INSERT INTO parent(id,value) VALUES (?,?)",
                (42, "reusable-after-caught-auto-rollback"),
            )
    finally:
        connection.close()


@pytest.mark.unit
def test_p1_2_caught_executemany_auto_rollback_cannot_retain_write_authority(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate(isolated_knowledge_layout.core_db_path)
    connection = factory.open()
    try:
        with transaction(connection):
            connection.execute(
                "INSERT INTO parent(id,value) VALUES (?,?)",
                (50, "conflict-sentinel"),
            )

        with pytest.raises(KnowledgeCoreTransactionError):
            with transaction(connection):
                try:
                    connection.executemany(
                        "INSERT OR ROLLBACK INTO parent(id,value) VALUES (?,?)",
                        ((51, "rolled-back-prefix"), (50, "must-conflict")),
                    )
                except sqlite3.IntegrityError:
                    pass

                with pytest.raises(sqlite3.DatabaseError):
                    connection.executemany(
                        "INSERT INTO parent(id,value) VALUES (?,?)",
                        ((52, "must-not-autocommit"),),
                    )

        assert connection.execute(
            "SELECT count(*) FROM parent WHERE id IN (51,52)"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.unit
def test_p1_2_transaction_boundary_blocks_manual_or_nested_control(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate(isolated_knowledge_layout.core_db_path)
    connection = factory.open()
    try:
        with pytest.raises(KnowledgeCoreTransactionError):
            connection.commit()
        with pytest.raises(KnowledgeCoreTransactionError):
            connection.rollback()
        with pytest.raises(KnowledgeCoreTransactionError):
            connection.executescript("INSERT INTO parent VALUES (1,'unsafe');")
        with pytest.raises(KnowledgeCoreTransactionError):
            with transaction(connection, "INVALID"):  # type: ignore[arg-type]
                pass

        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("INSERT INTO parent(id,value) VALUES (8,'autocommit')")
        assert connection.execute("SELECT count(*) FROM parent WHERE id=8").fetchone()[0] == 0

        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("PRAGMA user_version=9")
        assert connection.contract_snapshot()["pragmas"]["user_version"] == 1

        for unsafe_pragma in (
            "PRAGMA application_id=12345",
            "PRAGMA schema_version=23456",
            "PRAGMA journal_size_limit=4096",
            "PRAGMA read_uncommitted=ON",
            "PRAGMA optimize",
            "PRAGMA wal_checkpoint",
            "PRAGMA incremental_vacuum",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                connection.execute(unsafe_pragma)

        data_version = connection.execute("PRAGMA data_version").fetchone()
        assert data_version is not None and isinstance(data_version[0], int)
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("PRAGMA data_version=1")

        with pytest.raises(KnowledgeCoreTransactionError):
            connection.set_authorizer(None)
        if hasattr(sqlite3, "SQLITE_DBCONFIG_ENABLE_FKEY"):
            with pytest.raises(KnowledgeCoreTransactionError):
                connection.setconfig(sqlite3.SQLITE_DBCONFIG_ENABLE_FKEY, False)
        with pytest.raises(KnowledgeCoreTransactionError):
            connection.enable_load_extension(True)

        with pytest.raises(sqlite3.DatabaseError):
            with transaction(connection):
                connection.execute("CREATE TABLE route_owned_schema(id INTEGER)")
        assert connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name='route_owned_schema'"
        ).fetchone()[0] == 0

        with pytest.raises(KnowledgeCoreTransactionError):
            with transaction(connection):
                connection.execute("INSERT INTO parent(id,value) VALUES (3,'outer')")
                with transaction(connection):
                    pass
        assert connection.execute("SELECT count(*) FROM parent WHERE id=3").fetchone()[0] == 0

        with pytest.raises(sqlite3.DatabaseError):
            with transaction(connection):
                connection.execute("INSERT INTO parent(id,value) VALUES (4,'raw-commit')")
                connection.execute("COMMIT")
        assert connection.execute("SELECT count(*) FROM parent WHERE id=4").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.unit
def test_p1_2_ordinary_transaction_cannot_rewrite_migration_ledger(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate(isolated_knowledge_layout.core_db_path)
    connection = factory.open()
    try:
        original = tuple(
            connection.execute(
                "SELECT version,name,checksum_sha256,app_version FROM schema_migration"
            ).fetchone()
        )
        for statement in (
            "UPDATE schema_migration SET app_version='tampered' WHERE version=1",
            "DELETE FROM schema_migration WHERE version=1",
            "INSERT INTO schema_migration(version,name,checksum_sha256,app_version,"
            "sqlite_version,sqlite_source_id) VALUES "
            "(2,'tampered',lower(hex(randomblob(32))),'tampered','x','y')",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                with transaction(connection):
                    connection.execute(statement)
        observed = tuple(
            connection.execute(
                "SELECT version,name,checksum_sha256,app_version FROM schema_migration"
            ).fetchone()
        )
        assert observed == original
    finally:
        connection.close()


@pytest.mark.unit
def test_p1_2_opaque_wrapper_hides_every_raw_sqlite_mutation_channel(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate(isolated_knowledge_layout.core_db_path)
    connection = factory.open()
    source = sqlite3.connect(":memory:")
    try:
        assert not isinstance(connection, sqlite3.Connection)
        cursor = connection.execute("SELECT 1")
        with pytest.raises(AttributeError):
            getattr(cursor, "connection")
        with pytest.raises(TypeError):
            sqlite3.Connection.set_authorizer(connection, None)
        with pytest.raises(TypeError):
            sqlite3.Connection.commit(connection)
        if hasattr(sqlite3, "SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE"):
            with pytest.raises(TypeError):
                sqlite3.Connection.setconfig(
                    connection,
                    sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE,
                    False,
                )
        with pytest.raises(KnowledgeCoreTransactionError):
            connection.blobopen("parent", "value", 1, readonly=False)
        with pytest.raises(TypeError):
            source.backup(connection)
    finally:
        source.close()
        connection.close()


@pytest.mark.unit
@pytest.mark.parametrize("attempt", ("direct", "trigger", "rename"))
def test_p1_2_numbered_migration_cannot_rewrite_prior_ledger_history(
    isolated_knowledge_layout,
    attempt: str,
) -> None:
    path = isolated_knowledge_layout.core_db_path
    first_catalog = _catalog()
    first_factory, _ = _migrate(path, first_catalog)
    first = first_catalog.migrations[0]
    direct_sql = (
        "UPDATE schema_migration SET app_version='rewritten', applied_at=0 "
        "WHERE version=1"
    )
    trigger_sql = (
        "CREATE TRIGGER rewrite_prior_ledger AFTER INSERT ON schema_migration "
        "WHEN NEW.version=2 BEGIN UPDATE schema_migration "
        "SET app_version='rewritten' WHERE version=1; END"
    )
    rename_sql = (
        "ALTER TABLE schema_migration RENAME TO schema_history",
        "UPDATE schema_history SET app_version='rewritten', applied_at=0 "
        "WHERE version=1",
        "ALTER TABLE schema_history RENAME TO schema_migration",
    )
    statements = (
        (direct_sql,)
        if attempt == "direct"
        else (trigger_sql,)
        if attempt == "trigger"
        else rename_sql
    )
    expected_schema = (
        _expected_schema_sha256((*first.statements, trigger_sql))
        if attempt == "trigger"
        else first.expected_schema_sha256
    )
    malicious = Migration(
        2,
        "ledger_rewrite_attempt",
        statements,
        expected_schema,
    )
    expanded = MigrationCatalog((first, malicious))
    with pytest.raises(KnowledgeCoreMigrationError):
        _migrator(_factory(path, expanded), app_version="p1.2-test").run()

    connection = first_factory.open()
    try:
        rows = connection.execute(
            "SELECT version,app_version FROM schema_migration ORDER BY version"
        ).fetchall()
        assert [tuple(row) for row in rows] == [(1, "p1.2-test")]
    finally:
        connection.close()


@pytest.mark.unit
def test_p1_2_close_inside_transaction_is_blocked_and_original_baseexception_survives(
    isolated_knowledge_layout,
) -> None:
    path = isolated_knowledge_layout.core_db_path
    factory, _ = _migrate(path)
    guarded = factory.open()
    try:
        with pytest.raises(KnowledgeCoreTransactionError):
            with transaction(guarded):
                guarded.execute("INSERT INTO parent(id,value) VALUES (30,'rollback')")
                guarded.close()
        assert guarded.execute("SELECT count(*) FROM parent WHERE id=30").fetchone()[0] == 0
    finally:
        guarded.close()

    class Sentinel(BaseException):
        pass

    bypassed = factory.open()
    try:
        with pytest.raises(Sentinel):
            with transaction(bypassed):
                bypassed.execute("INSERT INTO parent(id,value) VALUES (31,'rollback')")
                with pytest.raises(TypeError):
                    sqlite3.Connection.close(bypassed)
                raise Sentinel("preserve me")
    finally:
        bypassed.close()
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("SELECT count(*) FROM parent WHERE id=31").fetchone() == (0,)
    finally:
        raw.close()


@pytest.mark.unit
def test_p1_2_every_ordinary_connection_enforces_foreign_keys(
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate(isolated_knowledge_layout.core_db_path)
    connection = factory.open()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            with transaction(connection):
                connection.execute("INSERT INTO child(id,parent_id) VALUES (1,999)")
        assert connection.execute("SELECT count(*) FROM child").fetchone()[0] == 0
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("PRAGMA foreign_key_check")
    finally:
        connection.close()


@pytest.mark.unit
def test_p1_2_failed_statement_rolls_back_schema_ledger_and_user_version(
    isolated_knowledge_layout,
) -> None:
    path = isolated_knowledge_layout.core_db_path
    catalog = _catalog(
        extra_statement="CREATE TABL invalid syntax",
        expected_schema_sha256="0" * 64,
    )
    with pytest.raises(KnowledgeCoreMigrationError):
        _migrator(_factory(path, catalog), app_version="p1.2-test").run()

    raw = sqlite3.connect(path)
    try:
        assert raw.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert raw.execute("PRAGMA user_version").fetchone() == (0,)
        assert raw.execute(
            "SELECT name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall() == []
    finally:
        raw.close()


@pytest.mark.unit
def test_p1_2_failed_postcondition_rolls_back_entire_migration(
    isolated_knowledge_layout,
) -> None:
    path = isolated_knowledge_layout.core_db_path
    catalog = _catalog(postcondition="SELECT 1")
    with pytest.raises(KnowledgeCoreMigrationError):
        _migrator(_factory(path, catalog), app_version="p1.2-test").run()
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone() == (0,)
        assert raw.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name='schema_migration'"
        ).fetchone() == (0,)
    finally:
        raw.close()


@pytest.mark.unit
def test_p1_2_mutating_with_postcondition_is_denied_and_fully_rolled_back(
    isolated_knowledge_layout,
) -> None:
    path = isolated_knowledge_layout.core_db_path
    catalog = _catalog(
        extra_statement="INSERT INTO parent(id,value) VALUES (99,'sentinel')",
        postcondition=(
            "WITH witness AS (SELECT 1) "
            "DELETE FROM parent WHERE id=99"
        ),
    )
    with pytest.raises(KnowledgeCoreMigrationError):
        _migrator(_factory(path, catalog), app_version="p1.2-test").run()
    raw = sqlite3.connect(path)
    try:
        assert raw.execute("PRAGMA user_version").fetchone() == (0,)
        assert raw.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        ).fetchone() == (0,)
    finally:
        raw.close()


@pytest.mark.unit
def test_p1_2_fts_probe_cleanup_failure_is_not_suppressed(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate(isolated_knowledge_layout.core_db_path)
    connection = factory.open()
    original_execute = ManagedConnection.execute

    def fail_probe_drop(self, sql: str, parameters: object = ()):
        if sql.startswith("DROP TABLE IF EXISTS temp.__knowledge_core_fts_probe"):
            raise sqlite3.OperationalError("injected cleanup failure")
        return original_execute(self, sql, parameters)

    monkeypatch.setattr(ManagedConnection, "execute", fail_probe_drop)
    try:
        with pytest.raises(KnowledgeCoreRuntimeMismatchError):
            _probe_fts5(connection)
    finally:
        connection.close()


@pytest.mark.unit
def test_p1_3_migrator_probes_persistent_external_content_fts(
    isolated_knowledge_layout,
) -> None:
    search_sql = """
CREATE TABLE search_document(
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content_md TEXT NOT NULL
) STRICT
""".strip()
    fts_sql = """
CREATE VIRTUAL TABLE knowledge_fts USING fts5(
    title,
    content_md,
    content='search_document',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
)
""".strip()
    statements = tuple(
        statement.strip()
        for statement in (_LEDGER_SQL, _PARENT_SQL, _CHILD_SQL, search_sql, fts_sql)
    )
    migration = Migration(
        1,
        "fixture_core_with_fts",
        statements,
        _expected_schema_sha256(statements),
    )
    catalog = MigrationCatalog((migration,))
    factory = _factory(isolated_knowledge_layout.core_db_path, catalog)

    result = _migrator(factory, app_version="p1.3-fts-test").run()
    assert result.applied_versions == (1,)
    connection = factory.open()
    try:
        assert connection.execute(
            "SELECT count(*) FROM knowledge_fts "
            "WHERE knowledge_fts MATCH 'knowledgecorev1ftsruntimeprobe'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.unit
def test_p1_3_json_runtime_probe_is_executable_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout,
) -> None:
    factory, _ = _migrate(isolated_knowledge_layout.core_db_path)
    connection = factory.open()
    original_execute = ManagedConnection.execute
    try:
        _probe_json(connection)

        def fail_json_probe(self, sql: str, parameters: object = ()):
            if sql.startswith("SELECT json_valid("):
                raise sqlite3.OperationalError("injected JSON probe failure")
            return original_execute(self, sql, parameters)

        monkeypatch.setattr(ManagedConnection, "execute", fail_json_probe)
        with pytest.raises(KnowledgeCoreRuntimeMismatchError):
            _probe_json(connection)
    finally:
        connection.close()


@pytest.mark.unit
@pytest.mark.scenario("SCHEMA-008")
def test_p1_3_migrator_rejects_storage_before_creating_target(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout,
) -> None:
    path = isolated_knowledge_layout.core_db_path

    def ineligible(_: object) -> dict[str, object]:
        return {
            "eligible": False,
            "exists": True,
            "reason_codes": ["filesystem_not_ntfs"],
            "reparse": {"components": [], "present": False},
            "requested_path": str(path.parent),
            "resolved_path": str(path.parent),
            "syntactic_unc": False,
            "volume": {
                "drive_type": "fixed",
                "drive_type_code": 3,
                "filesystem": "FAT32",
                "root": "C:\\",
            },
        }

    monkeypatch.setattr(windows_volume, "probe_path", ineligible)
    with pytest.raises(KnowledgeCoreDatabasePathError):
        _migrator(
            _factory(path, _catalog()),
            app_version="p1.3-storage-gate-test",
        ).run()
    assert not path.exists()


@pytest.mark.unit
def test_p1_2_checksum_drift_fails_startup_without_mutation(
    isolated_knowledge_layout,
) -> None:
    path = isolated_knowledge_layout.core_db_path
    factory, _ = _migrate(path)
    original = factory.migration_catalog.migrations[0].checksum_sha256
    drifted = _catalog(extra_statement="CREATE TABLE drifted(id INTEGER) STRICT")
    assert drifted.migrations[0].checksum_sha256 != original

    with pytest.raises(KnowledgeCoreSchemaMismatchError):
        _factory(path, drifted).open()
    connection = factory.open(read_only=True)
    try:
        assert connection.execute(
            "SELECT checksum_sha256 FROM schema_migration WHERE version=1"
        ).fetchone()[0] == original
        assert connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name='drifted'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.unit
def test_p1_2_physical_schema_tampering_fails_even_when_ledger_is_unchanged(
    isolated_knowledge_layout,
) -> None:
    path = isolated_knowledge_layout.core_db_path
    factory, _ = _migrate(path)
    raw = sqlite3.connect(path)
    try:
        raw.execute("DROP TABLE child")
    finally:
        raw.close()
    with pytest.raises(KnowledgeCoreSchemaMismatchError):
        factory.open()


@pytest.mark.unit
def test_p1_2_hard_link_alias_is_rejected_on_every_open(
    isolated_knowledge_layout,
) -> None:
    path = isolated_knowledge_layout.core_db_path
    factory, _ = _migrate(path)
    alias = path.with_name("knowledge-core-alias.db")
    os.link(path, alias)
    with pytest.raises(KnowledgeCoreDatabasePathError):
        factory.open()
    with pytest.raises(KnowledgeCoreDatabasePathError):
        _factory(alias).open()


@pytest.mark.unit
def test_p1_2_last_ordinary_close_does_not_checkpoint_wal(
    isolated_knowledge_layout,
) -> None:
    path = isolated_knowledge_layout.core_db_path
    factory, _ = _migrate(path)
    connection = factory.open()
    with transaction(connection):
        connection.execute("INSERT INTO parent(id,value) VALUES (71,'wal')")
    connection.close()

    wal_path = Path(f"{path}-wal")
    assert wal_path.is_file()
    assert wal_path.stat().st_size > 0

    maintenance = sqlite3.connect(path, isolation_level=None)
    try:
        checkpoint = maintenance.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        assert checkpoint is not None and checkpoint[0] == 0
    finally:
        maintenance.close()
    assert not wal_path.exists() or wal_path.stat().st_size == 0


@pytest.mark.unit
def test_p1_2_user_version_or_runtime_ledger_drift_fails_startup(
    isolated_knowledge_layout,
) -> None:
    path = isolated_knowledge_layout.core_db_path
    factory, _ = _migrate(path)
    raw = sqlite3.connect(path)
    try:
        raw.execute("PRAGMA user_version=7")
    finally:
        raw.close()
    with pytest.raises(KnowledgeCoreSchemaMismatchError):
        factory.open()

    raw = sqlite3.connect(path)
    try:
        raw.execute("PRAGMA user_version=1")
        raw.execute("UPDATE schema_migration SET sqlite_source_id='tampered'")
        raw.commit()
    finally:
        raw.close()
    with pytest.raises(KnowledgeCoreSchemaMismatchError):
        factory.open()


@pytest.mark.unit
def test_p1_2_pending_catalog_requires_migrator_then_reopens(
    isolated_knowledge_layout,
) -> None:
    path = isolated_knowledge_layout.core_db_path
    first_catalog = _catalog()
    _migrate(path, first_catalog)
    second_sql = "CREATE TABLE note(id INTEGER) STRICT"
    second = Migration(
        2,
        "fixture_extension",
        (second_sql,),
        _expected_schema_sha256((*first_catalog.migrations[0].statements, second_sql)),
    )
    expanded = MigrationCatalog((first_catalog.migrations[0], second))
    expanded_factory = _factory(path, expanded)

    with pytest.raises(KnowledgeCoreSchemaMismatchError):
        expanded_factory.open()
    result = _migrator(expanded_factory, app_version="p1.2-test").run()
    assert result.applied_versions == (2,)
    connection = expanded_factory.open()
    try:
        assert connection.contract_snapshot()["pragmas"]["user_version"] == 2
        assert connection.execute(
            "SELECT group_concat(version, ',') FROM schema_migration ORDER BY version"
        ).fetchone()[0] == "1,2"
        assert connection.execute(
            "SELECT count(*) FROM sqlite_schema WHERE name='note'"
        ).fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.unit
def test_p1_2_production_factory_rejects_unapproved_runtime_before_schema(
    isolated_knowledge_layout,
) -> None:
    memory = sqlite3.connect(":memory:")
    try:
        source_id = memory.execute("SELECT sqlite_source_id()").fetchone()[0]
    finally:
        memory.close()
    if (
        sqlite3.sqlite_version == APPROVED_SQLITE_VERSION
        and source_id == APPROVED_SQLITE_SOURCE_ID
    ):
        pytest.skip("This environment already has the exact approved SQLite build.")
    path = isolated_knowledge_layout.core_db_path
    raw = sqlite3.connect(path)
    raw.close()
    factory = ConnectionFactory(path, _catalog())
    with pytest.raises(KnowledgeCoreRuntimeMismatchError):
        factory.open()

    absent = path.with_name("production-migration-must-not-create.db")
    with pytest.raises(KnowledgeCoreRuntimeMismatchError):
        SingleMigrator(
            ConnectionFactory(absent, _catalog()),
            app_version="p1.2-test",
            storage_layout=_storage_layout(absent),
        ).run()
    assert not absent.exists()


@pytest.mark.unit
def test_p1_2_fresh_import_and_factory_construction_do_not_touch_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "never-created.sqlite"
    script = """
import json
import pathlib
import sys
from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations.model import Migration, MigrationCatalog
path = pathlib.Path(sys.argv[1])
catalog = MigrationCatalog((Migration(1, 'construction_probe', ('SELECT 1',), '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8eaa0b349bf00a6f2d0e9f2a'),))
ConnectionFactory(path, catalog)
print(json.dumps({'exists': path.exists(), 'sqlite_loaded': 'sqlite3' in sys.modules}))
"""
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script, os.fspath(database)],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == '{"exists": false, "sqlite_loaded": true}'
    assert not database.exists()
