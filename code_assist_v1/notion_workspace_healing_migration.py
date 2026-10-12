"""Guarded, forward-only NP-2A 1305 -> 1306 healing-run migration.

Only the exact frozen 1305 catalog is accepted.  Dry-run is read-only; apply
requires the approved plan checksum, writer quiescence, and a separately
retained exact SQLite backup.  No application or request path imports this
module.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable, NamedTuple
import uuid

from code_assist_v1 import notion_legacy_change_migration as secure_fs
from code_assist_v1 import notion_workspace_outbox_migration as schema_1305
from code_assist_v1 import notion_workspace_store as store


SOURCE_VERSION = 2026081305
SOURCE_NAME = "notion_workspace_v2_np2a_outbox_delivery"
SOURCE_CHECKSUM = "sha256:81970eaca013f37409ab30fb6bd065f6cb218e23a7ce9a42cddefc8aa08e9848"
SOURCE_ACTUAL_SCHEMA_SHA256 = (
    "sha256:18286b0f5796f26706e502aeec776971a1be7f5c5dd3dcf1a90326cad9f8e0a2"
)
TARGET_VERSION = 2026081306
TARGET_NAME = "notion_workspace_v2_np2a_healing_runs"
TARGET_CHECKSUM = (
    "sha256:6f5b23e4a276fd25aa96b1e427e8f4dce55e307fb059095baa46d6178ed13f52"
)
TARGET_ACTUAL_SCHEMA_SHA256 = (
    "sha256:abaadac598bea97af44260bdbbf31a9ca883b65e20dbefaabacd1c5be18ec025"
)

HEALING_RUN_OBJECTS = frozenset({
    "workspace_healing_runs",
    "idx_workspace_healing_runs_workspace_status",
    "idx_workspace_healing_runs_one_running",
    "trg_workspace_healing_run_initial_state",
    "trg_workspace_healing_run_identity_immutable",
    "trg_workspace_healing_run_transition_guard",
    "trg_workspace_healing_run_immutable_delete",
})
SOURCE_SQL = schema_1305.TARGET_SQL
TARGET_SQL = store._SCHEMA_SQL + store._HEALING_SCHEMA_1306_SQL

if (
    schema_1305.TARGET_VERSION,
    schema_1305.TARGET_NAME,
    schema_1305.TARGET_CHECKSUM,
    schema_1305.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 1305 healing migration source contract drifted")
if schema_1305.schema_1304._strip_schema_objects(
    TARGET_SQL, HEALING_RUN_OBJECTS
) != SOURCE_SQL:
    raise RuntimeError("1306 schema is not the reviewed additive extension of 1305")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 1305 healing migration source DDL drifted")


MigrationRefused = schema_1305.MigrationRefused
_canonical = schema_1305._canonical
_sha256_bytes = schema_1305._sha256_bytes
_resolved_target = schema_1305._resolved_target
_connect_ro = schema_1305._connect_ro
_require_checkpointed = schema_1305._require_checkpointed
_require_rollback_journal_header = schema_1305._require_rollback_journal_header
_file_identity = schema_1305._file_identity
_objects = schema_1305._objects
actual_schema_sha256 = schema_1305.actual_schema_sha256
_database_fingerprint = schema_1305._database_fingerprint


def _connect_immutable_ro(path: Path) -> sqlite3.Connection:
    """Open one pre-validated, quiesced image without consulting sidecars."""
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def _schema_contract(sql: str) -> tuple[list[dict[str, str]], str]:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    try:
        con.executescript(sql)
        return _objects(con), actual_schema_sha256(con)
    finally:
        con.close()


SOURCE_OBJECTS, _computed_source_actual = _schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _computed_target_actual = _schema_contract(TARGET_SQL)
if _computed_source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1305 healing migration source object contract drifted")
if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
    raise RuntimeError("frozen 1306 healing migration target DDL drifted")
if _computed_target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1306 healing migration target object contract drifted")


class _MigrationContract(NamedTuple):
    source_version: int
    source_name: str
    source_checksum: str
    source_actual_schema_sha256: str
    source_objects: list[dict[str, str]]
    target_version: int
    target_name: str
    target_checksum: str
    target_actual_schema_sha256: str
    target_objects: list[dict[str, str]]
    extension_sql: str
    plan_format: str
    operations: tuple[str, ...]
    source_label: str
    target_label: str


_CURRENT_CONTRACT = _MigrationContract(
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
    SOURCE_OBJECTS,
    TARGET_VERSION,
    TARGET_NAME,
    TARGET_CHECKSUM,
    TARGET_ACTUAL_SCHEMA_SHA256,
    TARGET_OBJECTS,
    store._HEALING_SCHEMA_1306_SQL,
    "code_assist.notion_workspace_healing_migration.v1",
    ("install_healing_run_schema", "replace_migration_marker"),
    "1305",
    "1306",
)


def _stable_file_identity(path: Path) -> tuple[str, int, int, int]:
    """Return the path/inode identity that must survive content mutations."""
    resolved = path.resolve(strict=True)
    stat = resolved.stat()
    return (
        os.path.normcase(str(resolved)),
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_nlink),
    )


def _fd_stable_identity(fd: int, path: Path) -> tuple[str, int, int, int]:
    stat = os.fstat(fd)
    return (
        os.path.normcase(str(path.resolve(strict=True))),
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_nlink),
    )


def _assert_catalog_identity(
    catalog: Path,
    root: Path,
    expected: tuple[str, int, int, int],
    *,
    pin_fd: int | None = None,
) -> None:
    """Re-resolve the exact target and prove its pathname still names the pin."""
    try:
        current_catalog, current_root = _resolved_target(catalog, root)
        current = _stable_file_identity(current_catalog)
        pinned = _fd_stable_identity(pin_fd, catalog) if pin_fd is not None else current
    except (OSError, MigrationRefused) as exc:
        raise MigrationRefused("catalog identity changed during migration") from exc
    if current_root != root or current_catalog != catalog or current != expected or pinned != expected:
        raise MigrationRefused("catalog identity changed during migration")


def _open_pin(path: Path) -> int:
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        create_file = ctypes.windll.kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            os.fspath(path),
            0x0080,  # FILE_READ_ATTRIBUTES; SQLite owns the data handle.
            0x00000001 | 0x00000002,  # share read/write, never delete/rename.
            None,
            3,  # OPEN_EXISTING
            0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle in (None, 0, invalid):
            raise MigrationRefused("migration file identity could not be pinned")
        try:
            return msvcrt.open_osfhandle(int(handle), os.O_RDONLY)
        except Exception as exc:
            ctypes.windll.kernel32.CloseHandle(handle)
            raise MigrationRefused(
                "migration file identity could not be pinned"
            ) from exc
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(path, flags)
    except OSError as exc:
        raise MigrationRefused("migration file identity could not be pinned") from exc


def _marker(con: sqlite3.Connection) -> dict[str, Any]:
    try:
        rows = con.execute(
            "SELECT version,name,checksum,actual_schema_sha256,applied_at "
            "FROM workspace_schema_migrations ORDER BY version"
        ).fetchall()
    except sqlite3.Error as exc:
        raise MigrationRefused(
            "unsupported_source_schema: migration marker unavailable"
        ) from exc
    if len(rows) != 1:
        raise MigrationRefused("unsupported_source_schema: expected exactly one marker")
    return dict(rows[0])


def _inspect(
    con: sqlite3.Connection,
    contract: _MigrationContract = _CURRENT_CONTRACT,
) -> dict[str, Any]:
    marker = _marker(con)
    actual = actual_schema_sha256(con)
    objects = _objects(con)
    if int(marker["version"]) == contract.target_version:
        if (
            str(marker["name"]),
            str(marker["checksum"]),
            str(marker["actual_schema_sha256"]),
            actual,
            objects,
        ) != (
            contract.target_name,
            contract.target_checksum,
            contract.target_actual_schema_sha256,
            contract.target_actual_schema_sha256,
            contract.target_objects,
        ):
            raise MigrationRefused(
                f"unsupported_target_schema: {contract.target_label} marker/object mismatch"
            )
        schema_1305._require_immutable_parent_state(con, target=True)
        return {
            "state": "already_current",
            "marker": marker,
            "actual_schema_sha256": actual,
        }
    observed = (
        int(marker["version"]),
        str(marker["name"]),
        str(marker["checksum"]),
        str(marker["actual_schema_sha256"]),
        actual,
        objects,
    )
    expected = (
        contract.source_version,
        contract.source_name,
        contract.source_checksum,
        contract.source_actual_schema_sha256,
        contract.source_actual_schema_sha256,
        contract.source_objects,
    )
    if observed != expected:
        raise MigrationRefused(
            f"unsupported_source_schema: exact {contract.source_label} contract mismatch"
        )
    schema_1305._require_immutable_parent_state(con, target=False)
    return {
        "state": "migration_required",
        "marker": marker,
        "actual_schema_sha256": actual,
    }


def _plan_report(
    catalog: Path,
    root: Path,
    inspection: dict[str, Any],
    database_fingerprint: str,
    catalog_identity: tuple[str, int, int, int],
    contract: _MigrationContract = _CURRENT_CONTRACT,
) -> dict[str, Any]:
    payload = {
        "format": contract.plan_format,
        "catalog_path": str(catalog),
        "user_data_root": str(root),
        "source_version": contract.source_version,
        "target_version": contract.target_version,
        "source_checksum": contract.source_checksum,
        "target_checksum": contract.target_checksum,
        "source_actual_schema_sha256": inspection["actual_schema_sha256"],
        "source_database_fingerprint": database_fingerprint,
        "catalog_file_identity": {
            "device": catalog_identity[1],
            "inode": catalog_identity[2],
            "link_count": catalog_identity[3],
        },
        "state": inspection["state"],
        "operations": []
        if inspection["state"] == "already_current"
        else list(contract.operations),
        "backup_policy": (
            "operator-provided exact SQLite snapshot; verified before apply"
        ),
        "source_compatibility": (
            f"only the frozen canonical {contract.source_label} variant is supported; other "
            "variants require restore or manual audit"
        ),
    }
    return {
        **payload,
        "plan_checksum": _sha256_bytes(_canonical(payload).encode("utf-8")),
    }


def build_plan(
    catalog_path: os.PathLike[str] | str,
    user_data_root: os.PathLike[str] | str,
    *,
    _contract: _MigrationContract = _CURRENT_CONTRACT,
) -> dict[str, Any]:
    catalog, root = _resolved_target(catalog_path, user_data_root)
    catalog_identity = _stable_file_identity(catalog)
    catalog_pin: int | None = _open_pin(catalog)
    try:
        _require_rollback_journal_header(catalog)
        _require_checkpointed(catalog)
        _assert_catalog_identity(catalog, root, catalog_identity, pin_fd=catalog_pin)
        con = _connect_ro(catalog)
        try:
            _assert_catalog_identity(catalog, root, catalog_identity, pin_fd=catalog_pin)
            inspection = _inspect(con, _contract)
            database_fingerprint = _database_fingerprint(con)
        finally:
            con.close()
        _assert_catalog_identity(catalog, root, catalog_identity, pin_fd=catalog_pin)
    finally:
        os.close(catalog_pin)
    _require_checkpointed(catalog)
    _require_rollback_journal_header(catalog)
    return _plan_report(
        catalog, root, inspection, database_fingerprint, catalog_identity, _contract
    )


def _reverify_backup(
    catalog: Path,
    backup: Path,
    expected_fingerprint: str,
    expected_identity: tuple[str, int, int, int, int, int],
    *,
    pin_fd: int | None = None,
    guard: sqlite3.Connection | None = None,
    contract: _MigrationContract = _CURRENT_CONTRACT,
) -> None:
    try:
        identity_matches = _file_identity(backup) == expected_identity
        if pin_fd is not None:
            pin_stat = os.fstat(pin_fd)
            identity_matches = identity_matches and (
                int(pin_stat.st_dev),
                int(pin_stat.st_ino),
                int(pin_stat.st_nlink),
                int(pin_stat.st_size),
                int(pin_stat.st_mtime_ns),
            ) == expected_identity[1:]
        distinct = not os.path.samefile(backup, catalog)
    except OSError as exc:
        raise MigrationRefused(
            "verified backup changed before commit: identity mismatch"
        ) from exc
    if not backup.is_file() or not distinct or not identity_matches:
        raise MigrationRefused(
            "verified backup changed before commit: identity mismatch"
        )
    _require_checkpointed(backup, label="backup")
    _require_rollback_journal_header(backup, label="backup")
    con = _connect_immutable_ro(backup)
    try:
        try:
            inspection = _inspect(con, contract)
            integrity_ok = str(
                con.execute("PRAGMA integrity_check").fetchone()[0]
            ) == "ok"
            foreign_keys_ok = con.execute("PRAGMA foreign_key_check").fetchone() is None
            fingerprint = _database_fingerprint(con)
        except (sqlite3.Error, MigrationRefused) as exc:
            raise MigrationRefused("verified backup changed before commit") from exc
        if (
            inspection["state"] != "migration_required"
            or not integrity_ok
            or not foreign_keys_ok
            or fingerprint != expected_fingerprint
        ):
            raise MigrationRefused("verified backup changed before commit")
    finally:
        con.close()
    if guard is not None:
        try:
            guard_inspection = _inspect(guard, contract)
            guard_fingerprint = _database_fingerprint(guard)
        except (sqlite3.Error, MigrationRefused) as exc:
            raise MigrationRefused("verified backup changed before commit") from exc
        if (
            guard_inspection["state"] != "migration_required"
            or guard_fingerprint != expected_fingerprint
        ):
            raise MigrationRefused("verified backup changed before commit")
    _require_checkpointed(backup, label="backup")


def _expected_catalog_identity(plan: dict[str, Any], catalog: Path) -> tuple[str, int, int, int]:
    raw = plan.get("catalog_file_identity")
    if not isinstance(raw, dict):
        raise MigrationRefused("dry-run plan is missing catalog file identity")
    try:
        return (
            os.path.normcase(str(catalog)),
            int(raw["device"]),
            int(raw["inode"]),
            int(raw["link_count"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MigrationRefused("dry-run plan has invalid catalog file identity") from exc


def _install_extension(
    con: sqlite3.Connection,
    contract: _MigrationContract = _CURRENT_CONTRACT,
) -> None:
    statement = ""
    for line in contract.extension_sql.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            if statement.strip():
                con.execute(statement)
            statement = ""
    if statement.strip():
        raise MigrationRefused("healing target DDL ended with an incomplete statement")


def _build_target_image(
    source: sqlite3.Connection,
    *,
    expected_source_fingerprint: str,
    fault_hook: Callable[[str], None] | None,
    contract: _MigrationContract = _CURRENT_CONTRACT,
) -> tuple[bytes, str]:
    """Build and validate 1306 wholly in memory; never write the source path."""
    target = sqlite3.connect(":memory:")
    target.row_factory = sqlite3.Row
    target.execute("PRAGMA foreign_keys=ON")
    target.execute("PRAGMA temp_store=MEMORY")
    try:
        source.backup(target)
        if (
            _inspect(target, contract)["state"] != "migration_required"
            or _database_fingerprint(target) != expected_source_fingerprint
        ):
            raise MigrationRefused("source changed after planning")
        target.execute("BEGIN IMMEDIATE")
        _install_extension(target, contract)
        if fault_hook:
            fault_hook("after_schema")
        target.execute(
            "DELETE FROM workspace_schema_migrations WHERE version=?",
            (contract.source_version,),
        )
        target.execute(
            "INSERT INTO workspace_schema_migrations("
            "version,name,checksum,actual_schema_sha256,applied_at) "
            "VALUES(?,?,?,?,strftime('%s','now'))",
            (
                contract.target_version,
                contract.target_name,
                contract.target_checksum,
                contract.target_actual_schema_sha256,
            ),
        )
        if _inspect(target, contract)["state"] != "already_current":
            raise MigrationRefused("target schema postcondition failed")
        if target.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise MigrationRefused("target foreign key postcondition failed")
        if fault_hook:
            fault_hook("before_commit")
        target.commit()
        target_fingerprint = _database_fingerprint(target)
        image = target.serialize()
        if not image:
            raise MigrationRefused("target migration image is empty")
        return image, target_fingerprint
    except Exception:
        if target.in_transaction:
            target.rollback()
        raise
    finally:
        target.close()


def apply_plan(
    catalog_path: os.PathLike[str] | str,
    user_data_root: os.PathLike[str] | str,
    *,
    exact_plan_checksum: str,
    backup_confirmed: bool,
    catalog_quiesced_confirmed: bool,
    backup_path: os.PathLike[str] | str,
    fault_hook: Callable[[str], None] | None = None,
    _contract: _MigrationContract = _CURRENT_CONTRACT,
) -> dict[str, Any]:
    if os.name != "nt":
        raise MigrationRefused(
            "secure healing migration apply is unavailable on this platform"
        )
    if not backup_confirmed or not catalog_quiesced_confirmed:
        raise MigrationRefused(
            "apply requires backup-confirmed and catalog-quiesced-confirmed"
        )
    if not exact_plan_checksum:
        raise MigrationRefused("apply requires the exact dry-run plan checksum")
    plan = build_plan(catalog_path, user_data_root, _contract=_contract)
    if exact_plan_checksum != plan["plan_checksum"]:
        raise MigrationRefused("exact plan checksum mismatch")
    if plan["state"] == "already_current":
        return {
            **plan,
            "applied": False,
            "reason": "already_current",
            "commit_recovered": False,
        }

    catalog, root = _resolved_target(catalog_path, user_data_root)
    catalog_identity = _expected_catalog_identity(plan, catalog)
    _assert_catalog_identity(catalog, root, catalog_identity)
    try:
        backup_lexical = Path(os.path.abspath(os.fspath(backup_path)))
        backup = backup_lexical.resolve(strict=True)
    except OSError as exc:
        raise MigrationRefused("verified backup_path does not exist") from exc
    try:
        backup.relative_to(root)
    except ValueError as exc:
        raise MigrationRefused("backup_path is outside user_data_root") from exc
    if backup == catalog or os.path.samefile(backup, catalog) or not backup.is_file():
        raise MigrationRefused("verified backup_path must be a distinct existing file")
    if os.path.normcase(str(backup)) != os.path.normcase(str(backup_lexical)):
        raise MigrationRefused("symlinked backup_path is not allowed")
    if backup.stat().st_nlink != 1:
        raise MigrationRefused("hard-linked backup_path is not allowed")
    backup_identity = _file_identity(backup)

    _require_rollback_journal_header(catalog)
    _require_rollback_journal_header(backup, label="backup")
    _require_checkpointed(catalog)
    _require_checkpointed(backup, label="backup")
    source = _connect_immutable_ro(catalog)
    copy = _connect_immutable_ro(backup)
    try:
        _assert_catalog_identity(catalog, root, catalog_identity)
        try:
            backup_inspection = _inspect(copy, _contract)
        except MigrationRefused as exc:
            raise MigrationRefused(
                "backup does not contain the exact source schema"
            ) from exc
        if backup_inspection["state"] != "migration_required":
            raise MigrationRefused("backup does not contain the exact source schema")
        if (
            source.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
        ):
            raise MigrationRefused("source or backup failed SQLite integrity_check")
        if (
            source.execute("PRAGMA foreign_key_check").fetchone() is not None
            or copy.execute("PRAGMA foreign_key_check").fetchone() is not None
        ):
            raise MigrationRefused("source or backup failed SQLite foreign_key_check")
        source_fingerprint = _database_fingerprint(source)
        if source_fingerprint != plan["source_database_fingerprint"]:
            raise MigrationRefused("source changed after planning")
        if source_fingerprint != _database_fingerprint(copy):
            raise MigrationRefused("backup logical snapshot does not match source")
    finally:
        source.close()
        copy.close()
    _assert_catalog_identity(catalog, root, catalog_identity)
    _require_checkpointed(catalog)
    _require_checkpointed(backup, label="backup")
    _require_rollback_journal_header(catalog)
    _require_rollback_journal_header(backup, label="backup")
    backup_verification = {
        "path": str(backup),
        "integrity_check": "ok",
        "foreign_key_check": "ok",
        "logical_snapshot_match": True,
    }

    catalog_pin = _open_pin(catalog)
    try:
        backup_pin = _open_pin(backup)
    except BaseException:
        os.close(catalog_pin)
        raise
    backup_guard: sqlite3.Connection | None = None
    directory_chain: list[int] = []
    source_security_handle: int | None = None
    backup_security_handle: int | None = None
    stage_handle: int | None = None
    stage_location = "absent"
    commit_recovered = False
    committed_report: dict[str, Any] | None = None
    cleanup_error: BaseException | None = None
    try:
        _assert_catalog_identity(catalog, root, catalog_identity, pin_fd=catalog_pin)
        backup_guard = _connect_immutable_ro(backup)
        backup_guard.execute("BEGIN")
        _reverify_backup(
            catalog,
            backup,
            plan["source_database_fingerprint"],
            backup_identity,
            pin_fd=backup_pin,
            guard=backup_guard,
            contract=_contract,
        )

        # The reviewed 1306 image is built in memory from a read-only source.
        # No pathname-based SQLite writer is ever opened against the catalog.
        directory_chain = secure_fs._pinned_directory_chain(root, catalog.parent)
        source_security_handle = secure_fs._pin_windows_path(
            catalog,
            directory=False,
            allow_delete=True,
            share_write=False,
        )
        source_identity = secure_fs._file_identity(catalog)
        source_security_metadata = secure_fs._windows_security_metadata(
            source_security_handle
        )
        if (
            source_security_metadata[2][1] != source_identity[1]
            or source_security_metadata[2][2] != source_identity[2]
        ):
            raise MigrationRefused("catalog security identity changed")
        backup_security_handle = secure_fs._pin_windows_path(
            backup,
            directory=False,
            allow_delete=False,
            share_write=False,
        )
        backup_security_metadata = secure_fs._windows_security_metadata(
            backup_security_handle
        )
        if (
            backup_security_metadata[:2] != source_security_metadata[:2]
            or backup_security_metadata[2][1] != backup_identity[2]
            or backup_security_metadata[2][2] != backup_identity[3]
        ):
            raise MigrationRefused(
                "verified backup security metadata does not match source"
            )
        if fault_hook:
            fault_hook("before_writer_open")
        secure_fs._assert_exact_file(
            catalog,
            root=root,
            expected_resolved=catalog,
            expected_identity=source_identity,
            content_identity=False,
        )
        _require_checkpointed(catalog)
        _require_checkpointed(backup, label="backup")
        source = _connect_immutable_ro(catalog)
        try:
            if (
                _inspect(source, _contract)["state"] != "migration_required"
                or _database_fingerprint(source)
                != plan["source_database_fingerprint"]
            ):
                raise MigrationRefused("source changed after planning")
            image, target_fingerprint = _build_target_image(
                source,
                expected_source_fingerprint=plan["source_database_fingerprint"],
                fault_hook=fault_hook,
                contract=_contract,
            )
        finally:
            source.close()
        # Keep the no-delete pin through the source snapshot, then let the
        # source-security handle + pinned parent carry the replace fence.
        os.close(catalog_pin)
        catalog_pin = None
        if fault_hook:
            fault_hook("after_writer_open")

        stage_path = catalog.with_name(
            f".{catalog.name}.healing-{uuid.uuid4().hex}.stage"
        )
        stage_handle = secure_fs._create_pinned_windows_file(
            stage_path,
            parent_handle=directory_chain[-1],
            exclusive=True,
        )
        stage_location = "stage"
        secure_fs._write_flush_windows_handle(stage_handle, image)
        stage_security_metadata = secure_fs._windows_security_metadata(stage_handle)
        if stage_security_metadata[:2] != source_security_metadata[:2]:
            raise MigrationRefused(
                "catalog security metadata is not safely replaceable"
            )
        expected_image_sha256 = "sha256:" + hashlib.sha256(image).hexdigest()
        if secure_fs._sha256_windows_handle(stage_handle) != expected_image_sha256:
            raise MigrationRefused("target migration image changed")

        secure_fs._assert_exact_file(
            catalog,
            root=root,
            expected_resolved=catalog,
            expected_identity=source_identity,
            content_identity=False,
        )
        _reverify_backup(
            catalog,
            backup,
            plan["source_database_fingerprint"],
            backup_identity,
            pin_fd=backup_pin,
            guard=backup_guard,
            contract=_contract,
        )
        if secure_fs._windows_security_metadata(
            backup_security_handle
        ) != backup_security_metadata:
            raise MigrationRefused("verified backup security metadata changed")
        _require_checkpointed(catalog)
        _require_checkpointed(backup, label="backup")
        if fault_hook:
            fault_hook("after_backup_verify")
        secure_fs._assert_exact_file(
            catalog,
            root=root,
            expected_resolved=catalog,
            expected_identity=source_identity,
            content_identity=False,
        )
        _reverify_backup(
            catalog,
            backup,
            plan["source_database_fingerprint"],
            backup_identity,
            pin_fd=backup_pin,
            guard=backup_guard,
            contract=_contract,
        )
        if fault_hook:
            fault_hook("after_final_backup_verify")
        secure_fs._assert_exact_file(
            catalog,
            root=root,
            expected_resolved=catalog,
            expected_identity=source_identity,
            content_identity=False,
        )
        _reverify_backup(
            catalog,
            backup,
            plan["source_database_fingerprint"],
            backup_identity,
            pin_fd=backup_pin,
            guard=backup_guard,
            contract=_contract,
        )
        if secure_fs._windows_security_metadata(
            source_security_handle
        ) != source_security_metadata:
            raise MigrationRefused("catalog security metadata changed before replacement")
        if secure_fs._windows_security_metadata(
            backup_security_handle
        ) != backup_security_metadata:
            raise MigrationRefused("verified backup security metadata changed")
        _require_checkpointed(catalog)
        _require_checkpointed(backup, label="backup")
        verify_source = _connect_immutable_ro(catalog)
        try:
            if (
                _inspect(verify_source, _contract)["state"] != "migration_required"
                or _database_fingerprint(verify_source)
                != plan["source_database_fingerprint"]
            ):
                raise MigrationRefused("source changed before replacement")
        finally:
            verify_source.close()

        stage_location = "unknown"
        try:
            secure_fs._replace_windows_handle_relative(
                stage_handle,
                parent_handle=directory_chain[-1],
                target_name=catalog.name,
            )
            stage_location = "target"
        except secure_fs.LegacyChangeMigrationRefused as replace_error:
            try:
                converged = (
                    os.path.normcase(
                        os.path.abspath(
                            os.fspath(secure_fs._windows_final_path(stage_handle))
                        )
                    )
                    == os.path.normcase(os.path.abspath(os.fspath(catalog)))
                    and secure_fs._sha256_windows_handle(stage_handle)
                    == expected_image_sha256
                )
            except Exception:
                converged = False
            if not converged:
                raise replace_error
            stage_location = "target"
            commit_recovered = True

        secure_fs._flush_windows_handle(stage_handle)
        if fault_hook:
            try:
                fault_hook("after_commit")
            except Exception:
                # The replacement is already durable.  Exact postconditions,
                # not an observer exception, determine the reported outcome.
                pass
        try:
            _require_checkpointed(catalog)
            _require_checkpointed(backup, label="backup")
        except MigrationRefused as exc:
            raise MigrationRefused(
                "replacement sidecar state is unsafe; manual audit required"
            ) from exc
        replacement_metadata = secure_fs._windows_security_metadata(stage_handle)
        if replacement_metadata != stage_security_metadata:
            raise MigrationRefused(
                "replacement security metadata changed; manual audit required"
            )
        if secure_fs._sha256_windows_handle(stage_handle) != expected_image_sha256:
            raise MigrationRefused(
                "replacement content changed; restore from verified backup"
            )
        final_handle_path = os.path.normcase(
            os.path.abspath(os.fspath(secure_fs._windows_final_path(stage_handle)))
        )
        if final_handle_path != os.path.normcase(os.path.abspath(os.fspath(catalog))):
            raise MigrationRefused(
                "replacement path changed; restore from verified backup"
            )

        _reverify_backup(
            catalog,
            backup,
            plan["source_database_fingerprint"],
            backup_identity,
            pin_fd=backup_pin,
            guard=backup_guard,
            contract=_contract,
        )
        if secure_fs._windows_security_metadata(
            backup_security_handle
        ) != backup_security_metadata:
            raise MigrationRefused("verified backup security metadata changed")
        # The same handle proved the durable replacement.  Release its
        # exclusive share before reopening the final path under the still
        # pinned directory chain and rebinding identity/security metadata.
        secure_fs._close_path_pins([stage_handle])
        stage_handle = None
        stage_location = "target"
        final_identity = _stable_file_identity(catalog)
        final_security_handle = secure_fs._pin_windows_path(
            catalog,
            directory=False,
            allow_delete=False,
            share_write=False,
        )
        verify_final: sqlite3.Connection | None = None
        try:
            final_metadata = secure_fs._windows_security_metadata(
                final_security_handle
            )
            if (
                final_metadata != replacement_metadata
                or final_metadata[2][1] != final_identity[2]
                or final_metadata[2][2] != final_identity[3]
            ):
                raise MigrationRefused(
                    "replacement security metadata changed; restore from verified backup"
                )
            try:
                final_raw_sha256 = "sha256:" + hashlib.sha256(
                    catalog.read_bytes()
                ).hexdigest()
            except OSError as exc:
                raise MigrationRefused(
                    "replacement content could not be verified; restore from verified backup"
                ) from exc
            if final_raw_sha256 != expected_image_sha256:
                raise MigrationRefused(
                    "replacement content changed; restore from verified backup"
                )
            _require_checkpointed(catalog)
            _require_checkpointed(backup, label="backup")
            verify_final = _connect_immutable_ro(catalog)
            if (
                _inspect(verify_final, _contract)["state"] != "already_current"
                or _database_fingerprint(verify_final) != target_fingerprint
            ):
                raise MigrationRefused(
                    "replacement postcondition failed; restore from verified backup"
                )
            if secure_fs._windows_security_metadata(
                backup_security_handle
            ) != backup_security_metadata:
                raise MigrationRefused(
                    "verified backup security metadata changed"
                )
            _require_checkpointed(catalog)
            _require_checkpointed(backup, label="backup")
        finally:
            try:
                if verify_final is not None:
                    verify_final.close()
            finally:
                secure_fs._close_path_pins([final_security_handle])
        committed_report = _plan_report(
            catalog,
            root,
            {
                "state": "already_current",
                "actual_schema_sha256": _contract.target_actual_schema_sha256,
            },
            target_fingerprint,
            final_identity[:4],
            _contract,
        )
    except Exception as exc:
        if stage_location == "unknown":
            raise MigrationRefused(
                "replacement_outcome_indeterminate: manual audit required"
            ) from None
        if stage_location == "target":
            raise MigrationRefused(
                "post_replacement_verification_failed: manual audit required"
            ) from None
        if isinstance(exc, secure_fs.LegacyChangeMigrationRefused):
            raise MigrationRefused(str(exc)) from exc
        raise
    finally:
        if stage_handle is not None:
            if stage_location == "stage":
                try:
                    secure_fs._dispose_windows_handle_on_close(stage_handle)
                except BaseException as exc:
                    cleanup_error = exc
            # Unknown means the native rename outcome could not be proven.
            # Never mark that handle delete-on-close: it may already name the
            # live catalog and the retained operator backup is the recovery.
            secure_fs._close_path_pins([stage_handle])
        if source_security_handle is not None:
            secure_fs._close_path_pins([source_security_handle])
        if backup_security_handle is not None:
            secure_fs._close_path_pins([backup_security_handle])
        secure_fs._close_path_pins(directory_chain)
        try:
            if backup_guard is not None:
                backup_guard.close()
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
        try:
            os.close(backup_pin)
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
        if catalog_pin is not None:
            try:
                os.close(catalog_pin)
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            raise cleanup_error
    assert committed_report is not None
    return {
        **committed_report,
        "applied": True,
        "applied_plan_checksum": exact_plan_checksum,
        "backup_verification": backup_verification,
        "commit_recovered": commit_recovered,
    }


__all__ = [
    "HEALING_RUN_OBJECTS",
    "MigrationRefused",
    "SOURCE_VERSION",
    "TARGET_VERSION",
    "apply_plan",
    "build_plan",
]
