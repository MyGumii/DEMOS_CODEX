"""Canonical SQLite connection factory and explicit transaction ownership.

Ordinary opens are existing-file-only, verify WAL and the migration catalog, and
never create or migrate schema. Only ``SingleMigrator`` uses the private
migration open. The production runtime allowlist cannot be replaced by caller
input; the only current-runtime bypass is guarded by the P0.2 isolated-test
path contract and is always reported on the connection and migration result.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import sqlite3
import stat
import threading
from typing import TYPE_CHECKING, Iterator

from .errors import (
    KnowledgeCoreDatabaseError,
    KnowledgeCoreDatabasePathError,
    KnowledgeCoreMigrationError,
    KnowledgeCorePragmaMismatchError,
    KnowledgeCoreRuntimeMismatchError,
    KnowledgeCoreTransactionError,
)
from .runtime import (
    APPROVED_SQLITE_SOURCE_ID,
    APPROVED_SQLITE_VERSION,
    DB_NOW_MS_SQL,
    EXPECTED_CONNECTION_PRAGMAS,
)

if TYPE_CHECKING:
    from .migrations.model import MigrationCatalog


_MIGRATION_OPEN_CAPABILITY = object()
_CONNECTION_INTERNAL_CAPABILITY = object()
_TRANSACTION_CAPABILITY = object()

_DML_WRITE_ACTIONS = {
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
}
_SCHEMA_WRITE_ACTIONS = {
    value
    for value in (
        getattr(sqlite3, "SQLITE_ALTER_TABLE", None),
        getattr(sqlite3, "SQLITE_ANALYZE", None),
        getattr(sqlite3, "SQLITE_CREATE_INDEX", None),
        getattr(sqlite3, "SQLITE_CREATE_TABLE", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_INDEX", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_TABLE", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_CREATE_TEMP_VIEW", None),
        getattr(sqlite3, "SQLITE_CREATE_TRIGGER", None),
        getattr(sqlite3, "SQLITE_CREATE_VIEW", None),
        getattr(sqlite3, "SQLITE_CREATE_VTABLE", None),
        getattr(sqlite3, "SQLITE_DROP_INDEX", None),
        getattr(sqlite3, "SQLITE_DROP_TABLE", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_INDEX", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_TABLE", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_DROP_TEMP_VIEW", None),
        getattr(sqlite3, "SQLITE_DROP_TRIGGER", None),
        getattr(sqlite3, "SQLITE_DROP_VIEW", None),
        getattr(sqlite3, "SQLITE_DROP_VTABLE", None),
        getattr(sqlite3, "SQLITE_REINDEX", None),
    )
    if value is not None
}
class TransactionMode(str, Enum):
    DEFERRED = "DEFERRED"
    IMMEDIATE = "IMMEDIATE"
    EXCLUSIVE = "EXCLUSIVE"


@dataclass(frozen=True, slots=True)
class SQLiteRuntimeFacts:
    version: str
    source_id: str
    fts5_compile_option: bool

    @property
    def exact_allowlist_match(self) -> bool:
        return (
            self.version == APPROVED_SQLITE_VERSION
            and self.source_id == APPROVED_SQLITE_SOURCE_ID
        )


@dataclass(frozen=True, slots=True)
class SQLiteHealthFacts:
    """Secret-free live facts from one fully validated read-only connection."""

    compile_options: tuple[str, ...]
    foreign_key_violation_count: int
    fts5_executable: bool
    pragmas: dict[str, int | str]
    quick_check_ok: bool
    runtime: SQLiteRuntimeFacts
    schema_version: int
    test_runtime_override: bool


_DATABASE_METRICS_LOCK = threading.Lock()
_DATABASE_EXECUTE_COUNT = 0
_DATABASE_BUSY_COUNT = 0
_DATABASE_LOCKED_COUNT = 0


def _record_database_execute(error: BaseException | None = None) -> None:
    global _DATABASE_EXECUTE_COUNT, _DATABASE_BUSY_COUNT, _DATABASE_LOCKED_COUNT
    with _DATABASE_METRICS_LOCK:
        _DATABASE_EXECUTE_COUNT += 1
        if not isinstance(error, sqlite3.Error):
            return
        code = getattr(error, "sqlite_errorcode", None)
        if isinstance(code, int):
            primary = code & 0xFF
            if primary == sqlite3.SQLITE_BUSY:
                _DATABASE_BUSY_COUNT += 1
            elif primary == sqlite3.SQLITE_LOCKED:
                _DATABASE_LOCKED_COUNT += 1


def database_runtime_metrics() -> dict[str, int]:
    """Return process-lifetime DB counters without exposing statements or data."""

    with _DATABASE_METRICS_LOCK:
        return {
            "application_retry_count": 0,
            "execute_count": _DATABASE_EXECUTE_COUNT,
            "sqlite_busy_count": _DATABASE_BUSY_COUNT,
            "sqlite_locked_count": _DATABASE_LOCKED_COUNT,
        }


@dataclass(frozen=True, slots=True)
class SQLiteOnlineBackupResult:
    """Observed facts from one completed online SQLite backup."""

    destination_path: Path
    destination_identity: tuple[int, int]
    file_size_bytes: int
    page_count: int
    page_size: int
    user_version: int
    audit_high_water: int
    quick_check: str
    foreign_key_violation_count: int


class ManagedCursor:
    """Result cursor that never exposes its raw sqlite3 connection."""

    __slots__ = ("__cursor",)

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self.__cursor = cursor

    @property
    def description(self) -> object:
        return self.__cursor.description

    @property
    def lastrowid(self) -> int | None:
        return self.__cursor.lastrowid

    @property
    def rowcount(self) -> int:
        return self.__cursor.rowcount

    def fetchone(self) -> sqlite3.Row | tuple[object, ...] | None:
        return self.__cursor.fetchone()

    def fetchmany(self, size: int | None = None) -> list[sqlite3.Row]:
        return self.__cursor.fetchmany() if size is None else self.__cursor.fetchmany(size)

    def fetchall(self) -> list[sqlite3.Row]:
        return self.__cursor.fetchall()

    def close(self) -> None:
        self.__cursor.close()

    def __iter__(self) -> "ManagedCursor":
        return self

    def __next__(self) -> sqlite3.Row:
        return next(self.__cursor)


class ManagedConnection:
    """Opaque connection boundary; the raw sqlite3 handle is never returned."""

    __slots__ = (
        "__database_identity",
        "__database_path",
        "__internal_sql_active",
        "__ledger_write_active",
        "__manager_controls_transaction",
        "__manager_transaction_active",
        "__raw",
        "__read_only_sql_active",
        "__runtime_facts",
        "__schema_writes_allowed",
        "__test_runtime_override",
    )

    def __init__(self, raw: sqlite3.Connection) -> None:
        if not isinstance(raw, sqlite3.Connection):
            raise KnowledgeCoreDatabaseError("ManagedConnection requires SQLite.")
        self.__raw = raw
        self.__database_identity: tuple[int, int] | None = None
        self.__database_path: Path | None = None
        self.__internal_sql_active = False
        self.__ledger_write_active = False
        self.__manager_controls_transaction = False
        self.__manager_transaction_active = False
        self.__read_only_sql_active = False
        self.__runtime_facts: SQLiteRuntimeFacts | None = None
        self.__schema_writes_allowed = False
        self.__test_runtime_override = False
        self.__install_authorizer()

    def __install_authorizer(self) -> None:
        def authorize(
            action: int,
            argument_one: str | None,
            argument_two: str | None,
            database_name: str | None,
            trigger_name: str | None,
        ) -> int:
            transaction_actions = {sqlite3.SQLITE_TRANSACTION}
            savepoint_action = getattr(sqlite3, "SQLITE_SAVEPOINT", None)
            if savepoint_action is not None:
                transaction_actions.add(savepoint_action)
            if action in transaction_actions and not self.__manager_controls_transaction:
                return sqlite3.SQLITE_DENY
            if self.__read_only_sql_active and (
                action in _DML_WRITE_ACTIONS
                or action in _SCHEMA_WRITE_ACTIONS
                or action
                in {
                    getattr(sqlite3, "SQLITE_ATTACH", -1),
                    getattr(sqlite3, "SQLITE_DETACH", -1),
                    sqlite3.SQLITE_PRAGMA,
                }
            ):
                return sqlite3.SQLITE_DENY
            write_allowed = (
                self.__manager_transaction_active or self.__internal_sql_active
            )
            if action in _DML_WRITE_ACTIONS and not write_allowed:
                return sqlite3.SQLITE_DENY
            ledger_name = "schema_migration"
            main_database = str(database_name or "main").casefold() == "main"
            if (
                action in _DML_WRITE_ACTIONS
                and isinstance(argument_one, str)
                and argument_one.casefold() == ledger_name
                and main_database
                and not (
                    action == sqlite3.SQLITE_INSERT
                    and self.__ledger_write_active
                    and trigger_name is None
                )
            ):
                return sqlite3.SQLITE_DENY
            if (
                action == getattr(sqlite3, "SQLITE_ALTER_TABLE", -1)
                and isinstance(argument_two, str)
                and argument_two.casefold() == ledger_name
                and str(argument_one or "main").casefold() == "main"
            ):
                return sqlite3.SQLITE_DENY
            if (
                action
                in {
                    getattr(sqlite3, "SQLITE_DROP_TABLE", -1),
                    getattr(sqlite3, "SQLITE_DROP_VTABLE", -1),
                }
                and isinstance(argument_one, str)
                and argument_one.casefold() == ledger_name
                and main_database
            ):
                return sqlite3.SQLITE_DENY
            if (
                action
                in {
                    getattr(sqlite3, "SQLITE_CREATE_TRIGGER", -1),
                    getattr(sqlite3, "SQLITE_CREATE_TEMP_TRIGGER", -1),
                }
                and isinstance(argument_two, str)
                and argument_two.casefold() == ledger_name
            ):
                return sqlite3.SQLITE_DENY
            if action in _SCHEMA_WRITE_ACTIONS and not (
                self.__internal_sql_active
                or (
                    self.__manager_transaction_active
                    and self.__schema_writes_allowed
                )
            ):
                return sqlite3.SQLITE_DENY
            if action in {
                getattr(sqlite3, "SQLITE_ATTACH", -1),
                getattr(sqlite3, "SQLITE_DETACH", -1),
            }:
                return sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_PRAGMA and not self.__internal_sql_active:
                # FTS5 issues a read-only data_version pragma internally while
                # preparing MATCH queries. Keep every assignment/effect pragma
                # denied and expose only this immutable observation.
                if not (
                    isinstance(argument_one, str)
                    and argument_one.casefold() == "data_version"
                    and argument_two is None
                ):
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        self.__raw.set_authorizer(authorize)

    @property
    def database_identity(self) -> tuple[int, int] | None:
        return self.__database_identity

    @property
    def database_path(self) -> Path | None:
        return self.__database_path

    @property
    def in_transaction(self) -> bool:
        return self.__raw.in_transaction

    @property
    def isolation_level(self) -> str | None:
        return self.__raw.isolation_level

    @property
    def runtime_facts(self) -> SQLiteRuntimeFacts | None:
        return self.__runtime_facts

    @property
    def test_runtime_override(self) -> bool:
        return self.__test_runtime_override

    @property
    def total_changes(self) -> int:
        return self.__raw.total_changes

    def __reconcile_failed_statement_transaction(self) -> None:
        """Drop managed write authority after an SQLite-initiated rollback."""

        if not self.__manager_transaction_active:
            return
        try:
            raw_transaction_active = self.__raw.in_transaction
        except sqlite3.ProgrammingError:
            raw_transaction_active = False
        if not raw_transaction_active:
            self.__manager_transaction_active = False

    def execute(self, sql: str, parameters: object = ()) -> ManagedCursor:
        try:
            cursor = ManagedCursor(self.__raw.execute(sql, parameters))
        except BaseException as exc:
            _record_database_execute(exc)
            self.__reconcile_failed_statement_transaction()
            raise
        _record_database_execute()
        return cursor

    def executemany(self, sql: str, parameters: object) -> ManagedCursor:
        try:
            cursor = ManagedCursor(self.__raw.executemany(sql, parameters))
        except BaseException as exc:
            _record_database_execute(exc)
            self.__reconcile_failed_statement_transaction()
            raise
        _record_database_execute()
        return cursor

    def commit(self) -> None:
        raise KnowledgeCoreTransactionError(
            "Commit is owned by the Knowledge Core transaction boundary."
        )

    def rollback(self) -> None:
        raise KnowledgeCoreTransactionError(
            "Rollback is owned by the Knowledge Core transaction boundary."
        )

    def executescript(self, sql_script: str, /) -> ManagedCursor:
        del sql_script
        raise KnowledgeCoreTransactionError(
            "executescript() is forbidden because it performs implicit commits."
        )

    def set_authorizer(self, authorizer_callback: object) -> None:
        del authorizer_callback
        raise KnowledgeCoreTransactionError(
            "The Knowledge Core SQLite authorizer cannot be replaced."
        )

    def setconfig(self, op: int, enable: bool = True) -> None:
        del op, enable
        raise KnowledgeCoreTransactionError(
            "SQLite connection safety configuration is immutable."
        )

    def enable_load_extension(self, enabled: bool) -> None:
        del enabled
        raise KnowledgeCoreTransactionError(
            "SQLite extension loading is disabled for canonical connections."
        )

    def load_extension(self, path: str, *, entrypoint: str | None = None) -> None:
        del path, entrypoint
        raise KnowledgeCoreTransactionError(
            "SQLite extension loading is disabled for canonical connections."
        )

    def deserialize(self, data: bytes, /, *, name: str = "main") -> None:
        del data, name
        raise KnowledgeCoreTransactionError(
            "SQLite deserialize is disabled for canonical connections."
        )

    def blobopen(
        self,
        table: str,
        column: str,
        row: int,
        /,
        *,
        readonly: bool = False,
        name: str = "main",
    ) -> None:
        del table, column, row, readonly, name
        raise KnowledgeCoreTransactionError(
            "Incremental blob handles are disabled for canonical connections."
        )

    def close(self) -> None:
        if (
            self.__manager_transaction_active
            and not self.__manager_controls_transaction
        ):
            raise KnowledgeCoreTransactionError(
                "A managed connection cannot be closed inside its transaction."
            )
        self.__raw.close()

    def contract_snapshot(self) -> dict[str, object]:
        """Return a diagnostic snapshot without exposing raw PRAGMA SQL."""

        pragmas = {
            name: _read_single_pragma(self, name)
            for name in (*EXPECTED_CONNECTION_PRAGMAS, "query_only", "user_version")
        }
        option = getattr(sqlite3, "SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE", None)
        no_checkpoint = option is not None and bool(self.__raw.getconfig(option))
        return {"no_checkpoint_on_close": no_checkpoint, "pragmas": pragmas}

    def _configure_no_checkpoint_on_close(self, capability: object) -> None:
        if capability is not _CONNECTION_INTERNAL_CAPABILITY:
            raise KnowledgeCoreTransactionError("Invalid SQLite internal capability.")
        option = getattr(sqlite3, "SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE", None)
        if option is None:
            raise KnowledgeCoreRuntimeMismatchError(
                "SQLite lacks SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE."
            )
        self.__raw.setconfig(option, True)
        if not self.__raw.getconfig(option):
            raise KnowledgeCorePragmaMismatchError(
                "SQLite no-checkpoint-on-close enforcement failed."
            )

    def _bind_database(
        self,
        capability: object,
        path: Path,
        identity: tuple[int, int],
    ) -> None:
        if capability is not _CONNECTION_INTERNAL_CAPABILITY:
            raise KnowledgeCoreTransactionError("Invalid SQLite internal capability.")
        self.__database_path = path
        self.__database_identity = identity

    def _bind_runtime(
        self,
        capability: object,
        facts: SQLiteRuntimeFacts,
        override_used: bool,
    ) -> None:
        if capability is not _CONNECTION_INTERNAL_CAPABILITY:
            raise KnowledgeCoreTransactionError("Invalid SQLite internal capability.")
        self.__runtime_facts = facts
        self.__test_runtime_override = override_used

    def _set_internal_sql(self, capability: object, active: bool) -> bool:
        if capability is not _CONNECTION_INTERNAL_CAPABILITY:
            raise KnowledgeCoreTransactionError("Invalid SQLite internal capability.")
        previous = self.__internal_sql_active
        self.__internal_sql_active = active
        return previous

    def _set_read_only_sql(self, capability: object, active: bool) -> bool:
        if capability is not _CONNECTION_INTERNAL_CAPABILITY:
            raise KnowledgeCoreTransactionError("Invalid SQLite internal capability.")
        previous = self.__read_only_sql_active
        self.__read_only_sql_active = active
        return previous

    def _set_ledger_write(self, capability: object, active: bool) -> bool:
        if capability is not _CONNECTION_INTERNAL_CAPABILITY:
            raise KnowledgeCoreTransactionError("Invalid SQLite internal capability.")
        previous = self.__ledger_write_active
        self.__ledger_write_active = active
        return previous

    def _allow_schema_writes(self, capability: object) -> None:
        if capability is not _MIGRATION_OPEN_CAPABILITY:
            raise KnowledgeCoreTransactionError("Invalid migration capability.")
        self.__schema_writes_allowed = True

    def _begin(self, capability: object, mode: TransactionMode) -> None:
        if capability is not _TRANSACTION_CAPABILITY:
            raise KnowledgeCoreTransactionError("Invalid transaction capability.")
        self.__manager_controls_transaction = True
        try:
            self.__raw.execute(f"BEGIN {mode.value}")
            self.__manager_transaction_active = True
        finally:
            self.__manager_controls_transaction = False

    def _commit(self, capability: object) -> None:
        if capability is not _TRANSACTION_CAPABILITY:
            raise KnowledgeCoreTransactionError("Invalid transaction capability.")
        self.__manager_controls_transaction = True
        try:
            self.__raw.commit()
        finally:
            self.__manager_transaction_active = False
            self.__manager_controls_transaction = False

    def _rollback(self, capability: object) -> None:
        if capability is not _TRANSACTION_CAPABILITY:
            raise KnowledgeCoreTransactionError("Invalid transaction capability.")
        self.__manager_controls_transaction = True
        try:
            self.__raw.rollback()
        finally:
            self.__manager_transaction_active = False
            self.__manager_controls_transaction = False

    def _backup_to(
        self,
        capability: object,
        destination: sqlite3.Connection,
    ) -> None:
        if capability is not _CONNECTION_INTERNAL_CAPABILITY:
            raise KnowledgeCoreTransactionError("Invalid SQLite internal capability.")
        if not isinstance(destination, sqlite3.Connection):
            raise KnowledgeCoreDatabaseError(
                "The SQLite backup destination is invalid."
            )
        self.__raw.backup(destination, pages=256, sleep=0.05)


@contextmanager
def _internal_sql(connection: ManagedConnection) -> Iterator[None]:
    previous = connection._set_internal_sql(_CONNECTION_INTERNAL_CAPABILITY, True)
    try:
        yield
    finally:
        connection._set_internal_sql(_CONNECTION_INTERNAL_CAPABILITY, previous)


@contextmanager
def _read_only_sql(connection: ManagedConnection) -> Iterator[None]:
    previous = connection._set_read_only_sql(_CONNECTION_INTERNAL_CAPABILITY, True)
    try:
        yield
    finally:
        connection._set_read_only_sql(_CONNECTION_INTERNAL_CAPABILITY, previous)


@contextmanager
def _ledger_write_sql(connection: ManagedConnection) -> Iterator[None]:
    previous = connection._set_ledger_write(_CONNECTION_INTERNAL_CAPABILITY, True)
    try:
        yield
    finally:
        connection._set_ledger_write(_CONNECTION_INTERNAL_CAPABILITY, previous)


def _absolute_database_path(database_path: str | os.PathLike[str]) -> Path:
    path = Path(database_path)
    if not path.is_absolute():
        raise KnowledgeCoreDatabasePathError(
            "The canonical database path must be absolute."
        )
    return path.resolve(strict=False)


def _sqlite_uri(path: Path, mode: str) -> str:
    return f"{path.as_uri()}?mode={mode}"


def _read_single_pragma(connection: ManagedConnection, name: str) -> int | str:
    with _internal_sql(connection):
        row = connection.execute(f"PRAGMA {name}").fetchone()
    if row is None or len(row) != 1:
        raise KnowledgeCorePragmaMismatchError(
            "SQLite did not return a required pragma value."
        )
    value = row[0]
    if name == "journal_mode" and isinstance(value, str):
        return value.lower()
    if not isinstance(value, (int, str)):
        raise KnowledgeCorePragmaMismatchError(
            "SQLite returned an invalid pragma value type."
        )
    return value


def _apply_connection_pragmas(connection: ManagedConnection) -> None:
    statements = (
        "PRAGMA foreign_keys = ON",
        "PRAGMA busy_timeout = 10000",
        "PRAGMA synchronous = FULL",
        "PRAGMA trusted_schema = OFF",
        "PRAGMA recursive_triggers = ON",
        "PRAGMA temp_store = MEMORY",
        "PRAGMA wal_autocheckpoint = 0",
    )
    with _internal_sql(connection):
        for statement in statements:
            connection.execute(statement)
    for name, expected in EXPECTED_CONNECTION_PRAGMAS.items():
        if name == "journal_mode":
            continue
        if _read_single_pragma(connection, name) != expected:
            raise KnowledgeCorePragmaMismatchError(
                "A required SQLite connection pragma did not persist."
            )


def _detect_runtime(connection: ManagedConnection) -> SQLiteRuntimeFacts:
    row = connection.execute(
        "SELECT sqlite_version(), sqlite_source_id(), "
        "sqlite_compileoption_used('ENABLE_FTS5')"
    ).fetchone()
    if row is None or len(row) != 3:
        raise KnowledgeCoreRuntimeMismatchError(
            "SQLite runtime identity could not be observed."
        )
    facts = SQLiteRuntimeFacts(
        version=str(row[0]),
        source_id=str(row[1]),
        fts5_compile_option=row[2] == 1,
    )
    if not facts.fts5_compile_option:
        raise KnowledgeCoreRuntimeMismatchError(
            "SQLite was not compiled with ENABLE_FTS5."
        )
    return facts


def _probe_fts5(connection: ManagedConnection) -> None:
    table_name = "__knowledge_core_fts_probe"
    table = f"temp.{table_name}"
    with _internal_sql(connection):
        try:
            connection.execute(f"CREATE VIRTUAL TABLE {table} USING fts5(content)")
            connection.execute(
                f"INSERT INTO {table}(content) VALUES (?)", ("probetoken",)
            )
            row = connection.execute(
                f"SELECT count(*) FROM {table} WHERE {table_name} MATCH ?",
                ("probetoken",),
            ).fetchone()
            if row is None or tuple(row) != (1,):
                raise KnowledgeCoreRuntimeMismatchError(
                    "SQLite FTS5 executable probe returned an invalid result."
                )
        except sqlite3.Error as exc:
            raise KnowledgeCoreRuntimeMismatchError(
                "SQLite FTS5 executable probe failed."
            ) from exc
        finally:
            try:
                connection.execute(f"DROP TABLE IF EXISTS {table}")
            except sqlite3.Error as exc:
                raise KnowledgeCoreRuntimeMismatchError(
                    "SQLite FTS5 probe cleanup failed."
                ) from exc
        residue = connection.execute(
            "SELECT count(*) FROM sqlite_temp_schema WHERE name = ?",
            (table_name,),
        ).fetchone()
        if residue is None or tuple(residue) != (0,):
            raise KnowledgeCoreRuntimeMismatchError(
                "SQLite FTS5 probe left temporary schema residue."
            )


def _probe_json(connection: ManagedConnection) -> None:
    try:
        row = connection.execute(
            "SELECT json_valid(?),json_type(?),json_type(?),json_valid(?)",
            ('{"schema_version":1}', '{"schema_version":1}', "[]", "{"),
        ).fetchone()
    except sqlite3.Error as exc:
        raise KnowledgeCoreRuntimeMismatchError(
            "SQLite JSON runtime probe failed."
        ) from exc
    if row is None or tuple(row) != (1, "object", "array", 0):
        raise KnowledgeCoreRuntimeMismatchError(
            "SQLite JSON runtime probe returned an unexpected result."
        )


def _verify_database_path(connection: ManagedConnection, expected_path: Path) -> None:
    with _internal_sql(connection):
        rows = connection.execute("PRAGMA database_list").fetchall()
    main_paths = [row[2] for row in rows if len(row) >= 3 and row[1] == "main"]
    if len(main_paths) != 1 or not main_paths[0]:
        raise KnowledgeCoreDatabasePathError(
            "SQLite did not expose one canonical main database path."
        )
    actual = Path(str(main_paths[0])).resolve(strict=False)
    if os.path.normcase(os.fspath(actual)) != os.path.normcase(os.fspath(expected_path)):
        raise KnowledgeCoreDatabasePathError(
            "SQLite opened a database path different from the requested path."
        )


def _database_file_identity(path: Path) -> tuple[int, int]:
    try:
        facts = path.stat(follow_symlinks=False)
    except (OSError, ValueError) as exc:
        raise KnowledgeCoreDatabasePathError(
            "The canonical SQLite database file cannot be inspected."
        ) from exc
    if not stat.S_ISREG(facts.st_mode):
        raise KnowledgeCoreDatabasePathError(
            "The canonical SQLite database path is not a regular file."
        )
    if facts.st_nlink != 1:
        raise KnowledgeCoreDatabasePathError(
            "The canonical SQLite database must have exactly one hard link."
        )
    return int(facts.st_dev), int(facts.st_ino)


def _verify_connection_file(connection: ManagedConnection) -> None:
    if connection.database_path is None or connection.database_identity is None:
        raise KnowledgeCoreDatabasePathError(
            "The managed connection has no verified database file identity."
        )
    if _database_file_identity(connection.database_path) != connection.database_identity:
        raise KnowledgeCoreDatabasePathError(
            "The canonical SQLite database file identity changed while open."
        )


def _set_user_version(connection: ManagedConnection, version: int) -> None:
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise KnowledgeCoreMigrationError("SQLite user_version must be non-negative.")
    with _internal_sql(connection):
        connection.execute(f"PRAGMA user_version = {version}")


def db_now_ms(connection: ManagedConnection) -> int:
    row = connection.execute(f"SELECT {DB_NOW_MS_SQL}").fetchone()
    if row is None or not isinstance(row[0], int) or row[0] < 0:
        raise KnowledgeCoreDatabaseError("SQLite database time is unavailable.")
    return row[0]


@contextmanager
def transaction(
    connection: ManagedConnection,
    mode: TransactionMode = TransactionMode.IMMEDIATE,
) -> Iterator[ManagedConnection]:
    """Own exactly one explicit transaction and rollback on every BaseException."""

    if not isinstance(connection, ManagedConnection):
        raise KnowledgeCoreTransactionError(
            "Knowledge Core transactions require a managed connection."
        )
    try:
        normalized_mode = TransactionMode(mode)
    except (TypeError, ValueError) as exc:
        raise KnowledgeCoreTransactionError(
            "The requested transaction mode is unsupported."
        ) from exc
    _verify_connection_file(connection)
    try:
        already_active = connection.in_transaction
    except sqlite3.ProgrammingError as exc:
        raise KnowledgeCoreTransactionError(
            "A closed connection cannot own a transaction."
        ) from exc
    if already_active:
        raise KnowledgeCoreTransactionError("Nested transactions are not supported.")
    try:
        connection._begin(_TRANSACTION_CAPABILITY, normalized_mode)
        yield connection
        if not connection.in_transaction:
            raise KnowledgeCoreTransactionError(
                "The managed transaction ended outside its owner."
            )
        _verify_connection_file(connection)
        connection._commit(_TRANSACTION_CAPABILITY)
    except BaseException as original:
        try:
            # SQLite may already have rolled back the raw transaction, for
            # example after an ``INSERT OR ROLLBACK`` constraint failure or a
            # fatal I/O error. The managed authority flag must still be
            # cleared before this connection can be reused or closed.
            connection._rollback(_TRANSACTION_CAPABILITY)
        except BaseException as rollback_error:
            if hasattr(original, "add_note"):
                original.add_note(
                    "Knowledge Core also failed to roll back the managed transaction: "
                    f"{type(rollback_error).__name__}."
                )
        raise


def online_backup_to_existing_file(
    connection: ManagedConnection,
    destination_path: str | os.PathLike[str],
    *,
    expected_destination_identity: tuple[int, int],
) -> SQLiteOnlineBackupResult:
    """Back up one verified canonical connection into a pre-created file.

    The P7.4 filesystem boundary must create the empty destination relative to
    a pinned staging-directory handle and retain a handle that denies delete
    sharing until this call returns.  Requiring the observed file identity here
    prevents a pathname winner or replacement from receiving canonical bytes.
    The online SQLite API, not a live ``.db`` file copy, captures WAL writes.
    """

    if not isinstance(connection, ManagedConnection):
        raise TypeError("online backup requires a ManagedConnection.")
    if connection.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Online backup cannot run inside an application transaction."
        )
    if (
        not isinstance(expected_destination_identity, tuple)
        or len(expected_destination_identity) != 2
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in expected_destination_identity
        )
    ):
        raise KnowledgeCoreDatabasePathError(
            "The backup destination identity is invalid."
        )
    destination = _absolute_database_path(destination_path)
    if connection.database_path is None:
        raise KnowledgeCoreDatabasePathError(
            "The source connection has no canonical path binding."
        )
    if os.path.normcase(os.fspath(destination)) == os.path.normcase(
        os.fspath(connection.database_path)
    ):
        raise KnowledgeCoreDatabasePathError(
            "The online backup destination cannot be the canonical database."
        )
    parent = destination.parent
    try:
        parent_before = parent.stat(follow_symlinks=False)
    except (OSError, ValueError) as exc:
        raise KnowledgeCoreDatabasePathError(
            "The backup destination parent cannot be inspected."
        ) from exc
    if not stat.S_ISDIR(parent_before.st_mode):
        raise KnowledgeCoreDatabasePathError(
            "The backup destination parent is not a directory."
        )
    attributes = int(getattr(parent_before, "st_file_attributes", 0))
    if attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
        raise KnowledgeCoreDatabasePathError(
            "The backup destination parent cannot be reparse-backed."
        )
    destination_identity = _database_file_identity(destination)
    if destination_identity != expected_destination_identity:
        raise KnowledgeCoreDatabasePathError(
            "The backup destination changed before SQLite opened it."
        )
    destination_facts = destination.stat(follow_symlinks=False)
    destination_attributes = int(
        getattr(destination_facts, "st_file_attributes", 0)
    )
    if destination_attributes & int(
        getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        raise KnowledgeCoreDatabasePathError(
            "The backup destination cannot be reparse-backed."
        )
    _verify_connection_file(connection)
    raw: sqlite3.Connection | None = None
    try:
        raw = sqlite3.connect(
            _sqlite_uri(destination, "rw"),
            uri=True,
            timeout=10.0,
            isolation_level=None,
            check_same_thread=True,
            cached_statements=0,
        )
        raw.row_factory = sqlite3.Row
        database_rows = raw.execute("PRAGMA database_list").fetchall()
        main_paths = [row[2] for row in database_rows if row[1] == "main"]
        if len(main_paths) != 1 or not main_paths[0]:
            raise KnowledgeCoreDatabasePathError(
                "SQLite did not bind the backup destination path."
            )
        opened = Path(str(main_paths[0])).resolve(strict=False)
        if os.path.normcase(os.fspath(opened)) != os.path.normcase(
            os.fspath(destination)
        ):
            raise KnowledgeCoreDatabasePathError(
                "SQLite opened a different backup destination."
            )
        if _database_file_identity(destination) != expected_destination_identity:
            raise KnowledgeCoreDatabasePathError(
                "The backup destination changed while SQLite opened it."
            )
        connection._backup_to(_CONNECTION_INTERNAL_CAPABILITY, raw)
        returned = raw.execute("PRAGMA journal_mode=DELETE").fetchone()
        if returned is None or str(returned[0]).casefold() != "delete":
            raise KnowledgeCorePragmaMismatchError(
                "The completed backup did not enter standalone journal mode."
            )
        raw.execute("PRAGMA foreign_keys=ON")
        quick_rows = raw.execute("PRAGMA quick_check").fetchall()
        quick_check = (
            "ok"
            if len(quick_rows) == 1 and tuple(quick_rows[0]) == ("ok",)
            else "failed"
        )
        foreign_key_violations = len(raw.execute("PRAGMA foreign_key_check").fetchall())
        page_count = int(raw.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(raw.execute("PRAGMA page_size").fetchone()[0])
        user_version = int(raw.execute("PRAGMA user_version").fetchone()[0])
        audit_row = raw.execute(
            "SELECT coalesce(max(id),0) FROM audit_event"
        ).fetchone()
        if audit_row is None:
            raise KnowledgeCoreDatabaseError(
                "The backup audit high-water mark is unavailable."
            )
        audit_high_water = int(audit_row[0])
        raw.close()
        raw = None
    except KnowledgeCoreDatabaseError:
        raise
    except sqlite3.Error as exc:
        raise KnowledgeCoreDatabaseError("The online SQLite backup failed.") from exc
    finally:
        if raw is not None:
            raw.close()
    _verify_connection_file(connection)
    if _database_file_identity(destination) != expected_destination_identity:
        raise KnowledgeCoreDatabasePathError(
            "The backup destination changed while the snapshot was written."
        )
    parent_after = parent.stat(follow_symlinks=False)
    if (int(parent_after.st_dev), int(parent_after.st_ino)) != (
        int(parent_before.st_dev),
        int(parent_before.st_ino),
    ):
        raise KnowledgeCoreDatabasePathError(
            "The backup destination parent changed during the snapshot."
        )
    final_facts = destination.stat(follow_symlinks=False)
    if quick_check != "ok" or foreign_key_violations != 0:
        raise KnowledgeCoreDatabaseError(
            "The online SQLite backup failed its integrity checks."
        )
    return SQLiteOnlineBackupResult(
        destination_path=destination,
        destination_identity=expected_destination_identity,
        file_size_bytes=int(final_facts.st_size),
        page_count=page_count,
        page_size=page_size,
        user_version=user_version,
        audit_high_water=audit_high_water,
        quick_check=quick_check,
        foreign_key_violation_count=foreign_key_violations,
    )


class ConnectionFactory:
    """Explicit-path factory; ordinary opens never create or migrate a database."""

    __slots__ = ("__database_path", "__migration_catalog", "__test_runtime_override")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del cls, kwargs
        raise TypeError("ConnectionFactory runtime policy cannot be subclassed.")

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        migration_catalog: "MigrationCatalog",
    ) -> None:
        from .migrations.model import MigrationCatalog

        if not isinstance(migration_catalog, MigrationCatalog):
            raise KnowledgeCoreMigrationError(
                "ConnectionFactory requires an immutable migration catalog."
            )
        if migration_catalog.latest_version < 1:
            raise KnowledgeCoreMigrationError(
                "ConnectionFactory requires a non-empty migration catalog."
            )
        object.__setattr__(
            self,
            "_ConnectionFactory__database_path",
            _absolute_database_path(database_path),
        )
        object.__setattr__(
            self,
            "_ConnectionFactory__migration_catalog",
            migration_catalog,
        )
        object.__setattr__(
            self,
            "_ConnectionFactory__test_runtime_override",
            False,
        )

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("ConnectionFactory configuration is immutable.")

    @property
    def database_path(self) -> Path:
        return self.__database_path

    @property
    def migration_catalog(self) -> "MigrationCatalog":
        return self.__migration_catalog

    @classmethod
    def _for_isolated_tests(
        cls,
        database_path: str | os.PathLike[str],
        migration_catalog: "MigrationCatalog",
    ) -> "ConnectionFactory":
        """Build a current-runtime factory only under the P0.2 isolation gate."""

        if cls is not ConnectionFactory or os.environ.get("CODE_ASSIST_TEST_MODE") != "1":
            raise KnowledgeCoreRuntimeMismatchError(
                "The SQLite runtime override is restricted to isolated tests."
            )
        from ..test_safety import assert_isolated_test_path

        guarded = assert_isolated_test_path(database_path)
        factory = cls(guarded, migration_catalog)
        object.__setattr__(
            factory,
            "_ConnectionFactory__test_runtime_override",
            True,
        )
        return factory

    def _runtime_preflight(self) -> None:
        raw = sqlite3.connect(
            ":memory:",
            isolation_level=None,
            check_same_thread=True,
            cached_statements=0,
        )
        raw.row_factory = sqlite3.Row
        connection = ManagedConnection(raw)
        try:
            connection._configure_no_checkpoint_on_close(
                _CONNECTION_INTERNAL_CAPABILITY
            )
            facts = _detect_runtime(connection)
            self._runtime_override_used(facts)
            _probe_fts5(connection)
            _probe_json(connection)
        finally:
            connection.close()

    def _runtime_override_used(self, facts: SQLiteRuntimeFacts) -> bool:
        if self.__test_runtime_override:
            if os.environ.get("CODE_ASSIST_TEST_MODE") != "1":
                raise KnowledgeCoreRuntimeMismatchError(
                    "The isolated-test SQLite runtime gate is no longer active."
                )
            from ..test_safety import assert_isolated_test_path

            guarded = assert_isolated_test_path(self.database_path)
            if guarded != self.database_path:
                raise KnowledgeCoreRuntimeMismatchError(
                    "The isolated-test SQLite path changed after factory construction."
                )
        if facts.exact_allowlist_match:
            return False
        if self.__test_runtime_override:
            return True
        raise KnowledgeCoreRuntimeMismatchError(
            "The SQLite version/source ID is not the approved production build."
        )

    def _connect(
        self,
        *,
        mode: str,
        expected_identity: tuple[int, int] | None,
        migration_capability: object | None = None,
    ) -> ManagedConnection:
        if mode not in {"ro", "rw", "rwc"}:
            raise KnowledgeCoreDatabasePathError("Unsupported SQLite open mode.")
        if (mode == "rwc") != (migration_capability is _MIGRATION_OPEN_CAPABILITY):
            raise KnowledgeCoreMigrationError(
                "Create-capable SQLite mode requires the migrator capability."
            )
        try:
            raw = sqlite3.connect(
                _sqlite_uri(self.database_path, mode),
                uri=True,
                timeout=10.0,
                isolation_level=None,
                check_same_thread=True,
                cached_statements=0,
            )
        except sqlite3.Error as exc:
            raise KnowledgeCoreDatabasePathError(
                "The canonical SQLite database could not be opened."
            ) from exc
        raw.row_factory = sqlite3.Row
        connection = ManagedConnection(raw)
        try:
            connection._configure_no_checkpoint_on_close(
                _CONNECTION_INTERNAL_CAPABILITY
            )
            _apply_connection_pragmas(connection)
            facts = _detect_runtime(connection)
            override_used = self._runtime_override_used(facts)
            _probe_fts5(connection)
            _probe_json(connection)
            _verify_database_path(connection, self.database_path)
            identity = _database_file_identity(self.database_path)
            if expected_identity is not None and identity != expected_identity:
                raise KnowledgeCoreDatabasePathError(
                    "The canonical SQLite database changed while it was being opened."
                )
            connection._bind_database(
                _CONNECTION_INTERNAL_CAPABILITY,
                self.database_path,
                identity,
            )
            connection._bind_runtime(
                _CONNECTION_INTERNAL_CAPABILITY,
                facts,
                override_used,
            )
            return connection
        except KnowledgeCoreDatabaseError:
            connection.close()
            raise
        except sqlite3.Error as exc:
            connection.close()
            raise KnowledgeCoreDatabaseError(
                "SQLite connection validation failed."
            ) from exc
        except BaseException:
            connection.close()
            raise

    def open(self, *, read_only: bool = False) -> ManagedConnection:
        """Open and validate an existing canonical DB without changing schema."""

        if not self.database_path.is_file():
            raise KnowledgeCoreDatabasePathError(
                "Ordinary connections require an existing database file."
            )
        identity = _database_file_identity(self.database_path)
        connection = self._connect(
            mode="ro" if read_only else "rw",
            expected_identity=identity,
        )
        try:
            if _read_single_pragma(connection, "journal_mode") != "wal":
                raise KnowledgeCorePragmaMismatchError(
                    "Ordinary connections require an existing WAL database."
                )
            from .migrations.runner import validate_schema_state

            validate_schema_state(connection, self.migration_catalog)
            if read_only:
                with _internal_sql(connection):
                    connection.execute("PRAGMA query_only = ON")
                if _read_single_pragma(connection, "query_only") != 1:
                    raise KnowledgeCorePragmaMismatchError(
                        "Read-only connection query_only enforcement failed."
                    )
            _verify_connection_file(connection)
            return connection
        except BaseException:
            connection.close()
            raise

    def _open_for_migration(
        self,
        capability: object,
        storage_layout: object | None = None,
    ) -> ManagedConnection:
        """Create/open only for the explicit SingleMigrator."""

        if capability is not _MIGRATION_OPEN_CAPABILITY:
            raise KnowledgeCoreMigrationError(
                "Only the explicit deployment migrator may open schema-write mode."
            )

        parent = self.database_path.parent
        if not parent.is_dir():
            raise KnowledgeCoreDatabasePathError(
                "The migration database parent must already exist."
            )
        if self.database_path.exists() and not self.database_path.is_file():
            raise KnowledgeCoreDatabasePathError(
                "The migration database path is not a regular file."
            )
        expected_identity = (
            _database_file_identity(self.database_path)
            if self.database_path.exists()
            else None
        )
        from .migrations.storage_preflight import (
            MigrationStorageLayout,
            verify_migration_storage_layout,
        )

        if not isinstance(storage_layout, MigrationStorageLayout):
            raise KnowledgeCoreDatabasePathError(
                "The deployment migrator requires an explicit storage layout."
            )
        if (
            os.path.normcase(os.fspath(storage_layout.database.resolve(strict=False)))
            != os.path.normcase(os.fspath(self.database_path))
        ):
            raise KnowledgeCoreDatabasePathError(
                "The migration layout database differs from the connection factory target."
            )

        storage_boundary = verify_migration_storage_layout(
            storage_layout,
            require_database=self.database_path.exists(),
        )
        self._runtime_preflight()
        connection = self._connect(
            mode="rwc",
            expected_identity=expected_identity,
            migration_capability=_MIGRATION_OPEN_CAPABILITY,
        )
        try:
            opened_boundary = verify_migration_storage_layout(
                storage_layout,
                require_database=True,
                expected=storage_boundary,
            )
            if opened_boundary.database_file_identity != connection.database_identity:
                raise KnowledgeCoreDatabasePathError(
                    "The migration storage identity differs from the opened database."
                )
            connection._allow_schema_writes(_MIGRATION_OPEN_CAPABILITY)
            with _internal_sql(connection):
                returned = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            journal_mode = (
                str(returned[0]).lower() if returned and len(returned) == 1 else ""
            )
            if journal_mode != "wal":
                raise KnowledgeCorePragmaMismatchError(
                    "The deployment migrator could not establish WAL mode."
                )
            if _read_single_pragma(connection, "journal_mode") != "wal":
                raise KnowledgeCorePragmaMismatchError(
                    "The deployment migrator could not verify WAL mode."
                )
            _verify_connection_file(connection)
            return connection
        except KnowledgeCoreDatabaseError:
            connection.close()
            raise
        except sqlite3.Error as exc:
            connection.close()
            raise KnowledgeCoreMigrationError(
                "SQLite migration connection setup failed."
            ) from exc
        except BaseException:
            connection.close()
            raise


def passive_wal_checkpoint(factory: ConnectionFactory) -> tuple[int, int, int]:
    """Run the sole online checkpoint mode allowed during normal service.

    Ordinary connections deny every effectful PRAGMA.  This narrow module-local
    capability exposes only ``wal_checkpoint(PASSIVE)`` so a deployment cannot
    smuggle ``TRUNCATE`` or another maintenance operation into request/worker
    code.  The coordinator owns no surrounding transaction.
    """

    if not isinstance(factory, ConnectionFactory):
        raise TypeError("factory must be a ConnectionFactory.")
    connection = factory.open()
    try:
        _verify_connection_file(connection)
        with _internal_sql(connection):
            row = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        _verify_connection_file(connection)
        if (
            row is None
            or len(row) != 3
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in row
            )
            or any(int(value) < 0 for value in row)
        ):
            raise KnowledgeCoreDatabaseError(
                "SQLite returned an invalid PASSIVE checkpoint result."
            )
        return int(row[0]), int(row[1]), int(row[2])
    except KnowledgeCoreDatabaseError:
        raise
    except sqlite3.Error as exc:
        raise KnowledgeCoreDatabaseError(
            "The PASSIVE WAL checkpoint failed."
        ) from exc
    finally:
        connection.close()


def observe_sqlite_health(factory: ConnectionFactory) -> SQLiteHealthFacts:
    """Observe live SQLite readiness through a validated, query-only connection.

    This is the only production-health escape hatch for diagnostic pragmas.  It
    returns counts and allowlisted runtime facts only; database paths, row
    values, and integrity error contents never cross the boundary.
    """

    if not isinstance(factory, ConnectionFactory):
        raise TypeError("factory must be a ConnectionFactory.")
    connection = factory.open(read_only=True)
    try:
        _verify_connection_file(connection)
        runtime = connection.runtime_facts
        if runtime is None:
            raise KnowledgeCoreRuntimeMismatchError(
                "The SQLite runtime facts are unavailable."
            )
        snapshot = connection.contract_snapshot()
        pragmas = snapshot.get("pragmas")
        if not isinstance(pragmas, dict):
            raise KnowledgeCorePragmaMismatchError(
                "The SQLite pragma snapshot is unavailable."
            )
        schema_version = pragmas.get("user_version")
        if isinstance(schema_version, bool) or not isinstance(schema_version, int):
            raise KnowledgeCorePragmaMismatchError(
                "The SQLite schema version is unavailable."
            )
        with _internal_sql(connection):
            compile_options = tuple(
                sorted(
                    str(row[0])
                    for row in connection.execute("PRAGMA compile_options")
                    if len(row) == 1
                )
            )
            quick_rows = connection.execute("PRAGMA quick_check").fetchall()
            foreign_key_count = sum(
                1 for _row in connection.execute("PRAGMA foreign_key_check")
            )
        _verify_connection_file(connection)
        return SQLiteHealthFacts(
            compile_options=compile_options,
            foreign_key_violation_count=foreign_key_count,
            fts5_executable=True,
            pragmas={
                str(name): value
                for name, value in pragmas.items()
                if name in {*EXPECTED_CONNECTION_PRAGMAS, "query_only", "user_version"}
                and isinstance(value, (int, str))
                and not isinstance(value, bool)
            },
            quick_check_ok=(
                len(quick_rows) == 1 and tuple(quick_rows[0]) == ("ok",)
            ),
            runtime=runtime,
            schema_version=schema_version,
            test_runtime_override=connection.test_runtime_override,
        )
    except KnowledgeCoreDatabaseError:
        raise
    except sqlite3.Error as exc:
        raise KnowledgeCoreDatabaseError(
            "The SQLite health observation failed."
        ) from exc
    finally:
        connection.close()


__all__ = [
    "ConnectionFactory",
    "ManagedConnection",
    "SQLiteHealthFacts",
    "SQLiteRuntimeFacts",
    "SQLiteOnlineBackupResult",
    "TransactionMode",
    "database_runtime_metrics",
    "db_now_ms",
    "observe_sqlite_health",
    "online_backup_to_existing_file",
    "passive_wal_checkpoint",
    "transaction",
]
