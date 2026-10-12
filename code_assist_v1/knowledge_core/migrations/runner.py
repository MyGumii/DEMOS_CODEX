"""One explicit, SQLite-serialized deployment migrator and schema validator."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import sqlite3

from ..db import (
    ConnectionFactory,
    ManagedConnection,
    TransactionMode,
    _MIGRATION_OPEN_CAPABILITY,
    _internal_sql,
    _ledger_write_sql,
    _probe_fts5,
    _read_only_sql,
    _set_user_version,
    _verify_connection_file,
    transaction,
)
from ..errors import (
    KnowledgeCoreDatabaseError,
    KnowledgeCoreFreshBootstrapRequiredError,
    KnowledgeCoreMigrationError,
    KnowledgeCoreSchemaMismatchError,
)
from ..runtime import (
    APPROVED_SQLITE_SOURCE_ID,
    APPROVED_SQLITE_VERSION,
    FAILPOINT_EXACT_NAME,
    FAILPOINT_PREFIX,
)
from .fingerprint import EMPTY_SCHEMA_SHA256, schema_sha256
from .model import MigrationCatalog
from .storage_preflight import MigrationStorageLayout


_APP_VERSION_PATTERN = re.compile(r"[^\x00\r\n]{1,80}\Z")


@dataclass(frozen=True, slots=True)
class MigrationResult:
    applied_versions: tuple[int, ...]
    catalog_checksum_sha256: str
    schema_version: int
    runtime_override_for_tests: bool


def _user_version(connection: ManagedConnection) -> int:
    with _internal_sql(connection):
        row = connection.execute("PRAGMA user_version").fetchone()
    if row is None or len(row) != 1 or not isinstance(row[0], int) or row[0] < 0:
        raise KnowledgeCoreSchemaMismatchError(
            "SQLite user_version is absent or invalid."
        )
    return row[0]


def _user_schema_objects(connection: ManagedConnection) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]


def _ledger_exists(connection: ManagedConnection) -> bool:
    row = connection.execute(
        "SELECT count(*) FROM sqlite_schema "
        "WHERE type='table' AND name='schema_migration'"
    ).fetchone()
    return row is not None and tuple(row) == (1,)


def _validate_physical_schema(
    connection: ManagedConnection,
    catalog: MigrationCatalog,
    version: int,
) -> None:
    expected = (
        EMPTY_SCHEMA_SHA256
        if version == 0
        else catalog.migration(version).expected_schema_sha256
    )
    try:
        observed = schema_sha256(connection)
    except (sqlite3.Error, KnowledgeCoreMigrationError) as exc:
        raise KnowledgeCoreSchemaMismatchError(
            "The physical SQLite schema could not be verified."
        ) from exc
    if observed != expected:
        raise KnowledgeCoreSchemaMismatchError(
            "The physical SQLite schema differs from the numbered migration catalog."
        )


def _applied_prefix(
    connection: ManagedConnection,
    catalog: MigrationCatalog,
    *,
    require_complete: bool,
) -> int:
    user_version = _user_version(connection)
    if not _ledger_exists(connection):
        if user_version != 0 or _user_schema_objects(connection):
            raise KnowledgeCoreSchemaMismatchError(
                "A non-empty database is missing the migration ledger."
            )
        if require_complete and catalog.latest_version != 0:
            raise KnowledgeCoreSchemaMismatchError(
                "The canonical schema has not been migrated."
            )
        _validate_physical_schema(connection, catalog, 0)
        return 0

    try:
        rows = connection.execute(
            "SELECT version,name,checksum_sha256,applied_at,app_version,"
            "sqlite_version,sqlite_source_id "
            "FROM schema_migration ORDER BY version"
        ).fetchall()
    except sqlite3.Error as exc:
        raise KnowledgeCoreSchemaMismatchError(
            "The schema migration ledger shape is invalid."
        ) from exc
    if not rows:
        raise KnowledgeCoreSchemaMismatchError(
            "The schema migration ledger cannot be present and empty."
        )
    versions = [row[0] for row in rows]
    if versions != list(range(1, len(rows) + 1)):
        raise KnowledgeCoreSchemaMismatchError(
            "Applied migration versions are not a contiguous prefix."
        )
    if len(rows) > catalog.latest_version:
        raise KnowledgeCoreSchemaMismatchError(
            "The database schema is newer than this application build."
        )
    for row in rows:
        version = row[0]
        expected = catalog.migration(version)
        if (
            row[1] != expected.name
            or row[2] != expected.checksum_sha256
            or not isinstance(row[3], int)
            or row[3] < 0
            or not isinstance(row[4], str)
            or not row[4].strip()
            or len(row[4]) > 80
            or row[5] != APPROVED_SQLITE_VERSION
            or row[6] != APPROVED_SQLITE_SOURCE_ID
        ):
            raise KnowledgeCoreSchemaMismatchError(
                "An applied migration identity or runtime record does not match."
            )
    applied_version = len(rows)
    if user_version != applied_version:
        raise KnowledgeCoreSchemaMismatchError(
            "SQLite user_version differs from the migration ledger."
        )
    if require_complete and applied_version != catalog.latest_version:
        raise KnowledgeCoreSchemaMismatchError(
            "The database schema is older than this application build."
        )
    _validate_physical_schema(connection, catalog, applied_version)
    return applied_version


def _foreign_key_check(connection: ManagedConnection) -> None:
    try:
        with _internal_sql(connection):
            violation = connection.execute("PRAGMA foreign_key_check").fetchone()
    except sqlite3.Error as exc:
        raise KnowledgeCoreMigrationError(
            "Foreign-key verification could not run."
        ) from exc
    if violation is not None:
        raise KnowledgeCoreMigrationError(
            "Foreign-key verification found a canonical schema violation."
        )


def _set_migration_rebuild_mode(
    connection: ManagedConnection,
    *,
    enabled: bool,
) -> None:
    """Enable/disable the narrow parent-table rebuild connection mode.

    SQLite requires FK enforcement off and legacy rename semantics while an
    existing parent table is replaced under same-name child references and
    cross-table triggers. Integrity is checked before commit and again after an
    ordinary FK-on reopen.
    """

    if connection.in_transaction:
        raise KnowledgeCoreMigrationError(
            "Migration foreign-key mode cannot change inside a transaction."
        )
    expected = 1 if enabled else 0
    try:
        with _internal_sql(connection):
            connection.execute(
                "PRAGMA foreign_keys = ON" if enabled else "PRAGMA foreign_keys = OFF"
            )
            connection.execute(
                "PRAGMA legacy_alter_table = OFF"
                if enabled
                else "PRAGMA legacy_alter_table = ON"
            )
            foreign_key_row = connection.execute("PRAGMA foreign_keys").fetchone()
            legacy_alter_row = connection.execute(
                "PRAGMA legacy_alter_table"
            ).fetchone()
    except sqlite3.Error as exc:
        raise KnowledgeCoreMigrationError(
            "Migration foreign-key mode could not be configured."
        ) from exc
    if (
        foreign_key_row is None
        or tuple(foreign_key_row) != (expected,)
        or legacy_alter_row is None
        or tuple(legacy_alter_row) != ((0 if enabled else 1),)
    ):
        raise KnowledgeCoreMigrationError(
            "Migration foreign-key mode did not persist."
        )


def _postconditions(
    connection: ManagedConnection,
    catalog: MigrationCatalog,
    *,
    excluded_queries: frozenset[str] = frozenset(),
) -> None:
    for migration in catalog.migrations:
        for postcondition_index, query in enumerate(
            migration.postcondition_queries, start=1
        ):
            if query in excluded_queries:
                continue
            try:
                with _read_only_sql(connection):
                    violation = connection.execute(query).fetchone()
            except sqlite3.Error as exc:
                raise KnowledgeCoreMigrationError(
                    "A migration postcondition was not a read-only query."
                ) from exc
            if violation is not None:
                label = violation[0] if len(violation) else "unlabelled"
                raise KnowledgeCoreMigrationError(
                    f"Migration v{migration.version} postcondition "
                    f"{postcondition_index} failed: {label}."
                )


def _probe_persistent_fts(connection: ManagedConnection) -> None:
    definition = connection.execute(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name='knowledge_fts'"
    ).fetchone()
    if definition is None:
        return
    if (
        len(definition) != 1
        or not isinstance(definition[0], str)
        or "using fts5" not in definition[0].casefold()
    ):
        raise KnowledgeCoreMigrationError(
            "The persistent Knowledge Core FTS object is not an FTS5 table."
        )

    probe_rowid = -9223372036854775807
    token = "knowledgecorev1ftsruntimeprobe"
    try:
        connection.execute(
            "INSERT INTO knowledge_fts(rowid,title,content_md) VALUES (?,?,?)",
            (probe_rowid, token, token),
        )
        matched = connection.execute(
            "SELECT count(*) FROM knowledge_fts "
            "WHERE knowledge_fts MATCH ? AND rowid=?",
            (token, probe_rowid),
        ).fetchone()
        if matched is None or tuple(matched) != (1,):
            raise KnowledgeCoreMigrationError(
                "The persistent Knowledge Core FTS probe could not find its row."
            )
        connection.execute(
            "INSERT INTO knowledge_fts(knowledge_fts,rowid,title,content_md) "
            "VALUES ('delete',?,?,?)",
            (probe_rowid, token, token),
        )
        residue = connection.execute(
            "SELECT count(*) FROM knowledge_fts "
            "WHERE knowledge_fts MATCH ? AND rowid=?",
            (token, probe_rowid),
        ).fetchone()
        if residue is None or tuple(residue) != (0,):
            raise KnowledgeCoreMigrationError(
                "The persistent Knowledge Core FTS probe left index residue."
            )
    except KnowledgeCoreMigrationError:
        raise
    except sqlite3.Error as exc:
        raise KnowledgeCoreMigrationError(
            "The persistent Knowledge Core FTS insert/query/delete probe failed."
        ) from exc


def validate_schema_state(
    connection: ManagedConnection,
    catalog: MigrationCatalog,
) -> None:
    """Fail closed unless the ledger exactly matches the complete code catalog."""

    try:
        _applied_prefix(connection, catalog, require_complete=True)
    except KnowledgeCoreSchemaMismatchError:
        raise
    except (KnowledgeCoreMigrationError, sqlite3.Error) as exc:
        raise KnowledgeCoreSchemaMismatchError(
            "The canonical schema state could not be verified."
        ) from exc


def verify_canonical_integrity(
    connection: ManagedConnection,
    catalog: MigrationCatalog,
) -> None:
    """Run the complete non-mutating schema/FK/postcondition integrity gate."""

    validate_schema_state(connection, catalog)
    try:
        with _internal_sql(connection):
            quick = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        raise KnowledgeCoreMigrationError(
            "Canonical SQLite quick-check could not run."
        ) from exc
    if quick is None or tuple(quick) != ("ok",):
        raise KnowledgeCoreMigrationError(
            "Canonical SQLite quick-check found a database violation."
        )
    _foreign_key_check(connection)
    _postconditions(connection, catalog)


def verify_canonical_integrity_before_projection_rebuild(
    connection: ManagedConnection,
    catalog: MigrationCatalog,
) -> None:
    """Verify canonical state while permitting only a stale search projection."""

    from .v001_invariants import POSTCONDITION_DEFINITIONS

    rebuildable = frozenset(
        query
        for name, query in POSTCONDITION_DEFINITIONS
        if name == "search_visibility_consistency"
    )
    if len(rebuildable) != 1:  # pragma: no cover - frozen catalog assertion
        raise KnowledgeCoreMigrationError(
            "The rebuildable projection invariant is not frozen exactly once."
        )
    validate_schema_state(connection, catalog)
    try:
        with _internal_sql(connection):
            quick = connection.execute("PRAGMA quick_check").fetchone()
    except sqlite3.Error as exc:
        raise KnowledgeCoreMigrationError(
            "Canonical SQLite quick-check could not run."
        ) from exc
    if quick is None or tuple(quick) != ("ok",):
        raise KnowledgeCoreMigrationError(
            "Canonical SQLite quick-check found a database violation."
        )
    _foreign_key_check(connection)
    _postconditions(connection, catalog, excluded_queries=rebuildable)


class SingleMigrator:
    """The only schema-changing entry point; never invoked by ordinary opens."""

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        app_version: str,
        storage_layout: MigrationStorageLayout,
        require_fresh_bootstrap: bool = False,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise KnowledgeCoreMigrationError(
                "SingleMigrator requires the canonical ConnectionFactory."
            )
        if (
            not isinstance(app_version, str)
            or not _APP_VERSION_PATTERN.fullmatch(app_version)
            or not app_version.strip()
        ):
            raise KnowledgeCoreMigrationError(
                "Migrator app_version must be non-empty text of at most 80 characters."
            )
        if factory.migration_catalog.latest_version < 1:
            raise KnowledgeCoreMigrationError(
                "The deployment migrator requires a non-empty migration catalog."
            )
        if not isinstance(storage_layout, MigrationStorageLayout):
            raise KnowledgeCoreMigrationError(
                "SingleMigrator requires an explicit MigrationStorageLayout."
            )
        if not isinstance(require_fresh_bootstrap, bool):
            raise KnowledgeCoreMigrationError(
                "require_fresh_bootstrap must be a Boolean."
            )
        self._factory = factory
        self._app_version = app_version
        self._storage_layout = storage_layout
        self._require_fresh_bootstrap = require_fresh_bootstrap

    def run(self) -> MigrationResult:
        if any(
            name == FAILPOINT_EXACT_NAME or name.startswith(FAILPOINT_PREFIX)
            for name in os.environ
        ):
            raise KnowledgeCoreMigrationError(
                "Failpoint configuration is forbidden during deployment migration."
            )
        connection = self._factory._open_for_migration(
            _MIGRATION_OPEN_CAPABILITY,
            self._storage_layout,
        )
        runtime_override_for_tests = connection.test_runtime_override
        applied_versions: list[int] = []
        foreign_keys_disabled = False
        try:
            preflight_applied = _applied_prefix(
                connection,
                self._factory.migration_catalog,
                require_complete=False,
            )
            pending_preflight = self._factory.migration_catalog.migrations[
                preflight_applied:
            ]
            foreign_keys_disabled = any(
                migration.requires_foreign_keys_off
                for migration in pending_preflight
            )
            if foreign_keys_disabled:
                _set_migration_rebuild_mode(connection, enabled=False)
            with transaction(connection, TransactionMode.EXCLUSIVE):
                applied = _applied_prefix(
                    connection,
                    self._factory.migration_catalog,
                    require_complete=False,
                )
                if applied != preflight_applied:
                    raise KnowledgeCoreMigrationError(
                        "The migration prefix changed before the exclusive transaction."
                    )
                if self._require_fresh_bootstrap and applied != 0:
                    raise KnowledgeCoreFreshBootstrapRequiredError(
                        "The fresh-only deployment target already has an applied schema."
                    )
                for migration in self._factory.migration_catalog.migrations[applied:]:
                    try:
                        for statement in migration.statements:
                            # SQLite implements ALTER TABLE by running an
                            # internal ``PRAGMA quick_check(table)``. Consumer
                            # PRAGMAs remain default-denied, so only the explicit
                            # deployment migrator opens the existing internal-SQL
                            # capability for this reviewed statement. Ledger
                            # rename/drop protections remain independently
                            # enforced by the authorizer.
                            if statement.lstrip().upper().startswith("ALTER TABLE"):
                                with _internal_sql(connection):
                                    connection.execute(statement)
                            else:
                                connection.execute(statement)
                        _validate_physical_schema(
                            connection,
                            self._factory.migration_catalog,
                            migration.version,
                        )
                        with _ledger_write_sql(connection):
                            connection.execute(
                                "INSERT INTO schema_migration("
                                "version,name,checksum_sha256,app_version,"
                                "sqlite_version,sqlite_source_id"
                                ") VALUES (?,?,?,?,?,?)",
                                (
                                    migration.version,
                                    migration.name,
                                    migration.checksum_sha256,
                                    self._app_version,
                                    APPROVED_SQLITE_VERSION,
                                    APPROVED_SQLITE_SOURCE_ID,
                                ),
                            )
                        _set_user_version(connection, migration.version)
                    except sqlite3.Error as exc:
                        raise KnowledgeCoreMigrationError(
                            "A numbered migration statement failed."
                        ) from exc
                    applied_versions.append(migration.version)

                _foreign_key_check(connection)
                _postconditions(connection, self._factory.migration_catalog)
                _probe_persistent_fts(connection)
                _probe_fts5(connection)
                _verify_connection_file(connection)
                _applied_prefix(
                    connection,
                    self._factory.migration_catalog,
                    require_complete=True,
                )
        except KnowledgeCoreDatabaseError:
            raise
        except Exception as exc:
            raise KnowledgeCoreMigrationError(
                "The deployment migration transaction failed."
            ) from exc
        finally:
            if foreign_keys_disabled and not connection.in_transaction:
                _set_migration_rebuild_mode(connection, enabled=True)
            connection.close()

        # Reopen through the ordinary factory. This proves that no implicit
        # migrator-only setting is required to consume the committed schema.
        reopened = self._factory.open()
        try:
            try:
                with _internal_sql(reopened):
                    quick = reopened.execute("PRAGMA quick_check").fetchone()
            except sqlite3.Error as exc:
                raise KnowledgeCoreMigrationError(
                    "Post-migration quick_check could not run."
                ) from exc
            if quick is None or tuple(quick) != ("ok",):
                raise KnowledgeCoreMigrationError(
                    "Post-migration quick_check did not return ok."
                )
            _foreign_key_check(reopened)
        finally:
            reopened.close()

        return MigrationResult(
            applied_versions=tuple(applied_versions),
            catalog_checksum_sha256=self._factory.migration_catalog.checksum_sha256,
            schema_version=self._factory.migration_catalog.latest_version,
            runtime_override_for_tests=runtime_override_for_tests,
        )


__all__ = [
    "MigrationResult",
    "SingleMigrator",
    "validate_schema_state",
]
