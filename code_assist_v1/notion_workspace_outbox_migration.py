"""Guarded, forward-only NP-2A 1304 -> 1305 outbox migration.

The source and target are exact reviewed schema variants.  The migrator never
repairs an unknown catalog, discovers user data, or runs on request paths.
Callers must inject the exact catalog/root paths and an independently retained
SQLite backup.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable

from code_assist_v1 import notion_workspace_schema_migration as schema_1304
from code_assist_v1 import notion_workspace_store as store


SOURCE_VERSION = 2026081304
SOURCE_NAME = "notion_workspace_v2_np2a_locator_projection"
SOURCE_CHECKSUM = "sha256:05b69bd7fc72994e1f71e30e4fdbd85f66009def3aa061b34dd77f7a5f648b4d"
SOURCE_ACTUAL_SCHEMA_SHA256 = (
    "sha256:2e65a41d6f75f1976c4d3c554a3e6a536a66275443ee91715b8af915dbc09ffe"
)
TARGET_VERSION = 2026081305
TARGET_NAME = "notion_workspace_v2_np2a_outbox_delivery"
TARGET_CHECKSUM = "sha256:81970eaca013f37409ab30fb6bd065f6cb218e23a7ce9a42cddefc8aa08e9848"
TARGET_ACTUAL_SCHEMA_SHA256 = (
    "sha256:18286b0f5796f26706e502aeec776971a1be7f5c5dd3dcf1a90326cad9f8e0a2"
)

OUTBOX_DELIVERY_OBJECTS = schema_1304._OUTBOX_DELIVERY_OBJECTS
SOURCE_SQL = schema_1304.TARGET_SQL
TARGET_SQL = store._SCHEMA_SQL

if (schema_1304.TARGET_VERSION, schema_1304.TARGET_NAME,
        schema_1304.TARGET_CHECKSUM,
        schema_1304.TARGET_ACTUAL_SCHEMA_SHA256) != (
            SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM,
            SOURCE_ACTUAL_SCHEMA_SHA256):
    raise RuntimeError("frozen 1304 outbox migration source contract drifted")
# ``store._SCHEMA_SQL`` is deliberately retained as the frozen 1305 DDL even
# after the runtime advances.  The old forward migrator must remain usable
# without pretending that the current request-time schema is still 1305.
if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
    raise RuntimeError("frozen 1305 outbox migration target DDL drifted")
if schema_1304._strip_schema_objects(TARGET_SQL, OUTBOX_DELIVERY_OBJECTS) != SOURCE_SQL:
    raise RuntimeError("1305 schema is not the reviewed additive extension of 1304")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 1304 outbox migration source DDL drifted")


MigrationRefused = schema_1304.MigrationRefused
_canonical = schema_1304._canonical
_sha256_bytes = schema_1304._sha256_bytes
_resolved_target = schema_1304._resolved_target
_connect_ro = schema_1304._connect_ro
_require_checkpointed = schema_1304._require_checkpointed
_require_rollback_journal_header = schema_1304._require_rollback_journal_header
_file_identity = schema_1304._file_identity
_objects = schema_1304._objects
actual_schema_sha256 = schema_1304.actual_schema_sha256
_database_fingerprint = schema_1304._database_fingerprint


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
    raise RuntimeError("frozen 1304 outbox migration source object contract drifted")
if _computed_target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1305 outbox migration target object contract drifted")


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


def _require_immutable_parent_state(
    con: sqlite3.Connection, *, target: bool,
) -> None:
    try:
        mutated = con.execute(
            "SELECT 1 FROM workspace_outbox WHERE delivered_at IS NOT NULL LIMIT 1"
        ).fetchone()
    except sqlite3.Error as exc:
        label = "target" if target else "source"
        raise MigrationRefused(f"unsupported_{label}_schema: outbox unavailable") from exc
    if mutated is not None:
        label = "target" if target else "source"
        raise MigrationRefused(
            f"unsupported_{label}_data: legacy outbox parent was mutated"
        )


def _inspect(con: sqlite3.Connection) -> dict[str, Any]:
    marker = _marker(con)
    actual = actual_schema_sha256(con)
    objects = _objects(con)
    if int(marker["version"]) == TARGET_VERSION:
        if (str(marker["name"]), str(marker["checksum"]),
                str(marker["actual_schema_sha256"]), actual, objects) != (
                    TARGET_NAME, TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256,
                    TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS):
            raise MigrationRefused("unsupported_target_schema: 1305 marker/object mismatch")
        _require_immutable_parent_state(con, target=True)
        return {
            "state": "already_current",
            "marker": marker,
            "actual_schema_sha256": actual,
        }
    expected = (
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM,
        SOURCE_ACTUAL_SCHEMA_SHA256, SOURCE_ACTUAL_SCHEMA_SHA256, SOURCE_OBJECTS,
    )
    observed = (
        int(marker["version"]), str(marker["name"]), str(marker["checksum"]),
        str(marker["actual_schema_sha256"]), actual, objects,
    )
    if observed != expected:
        raise MigrationRefused("unsupported_source_schema: exact 1304 contract mismatch")
    _require_immutable_parent_state(con, target=False)
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
) -> dict[str, Any]:
    payload = {
        "format": "code_assist.notion_workspace_outbox_migration.v1",
        "catalog_path": str(catalog),
        "user_data_root": str(root),
        "source_version": SOURCE_VERSION,
        "target_version": TARGET_VERSION,
        "source_checksum": SOURCE_CHECKSUM,
        "target_checksum": TARGET_CHECKSUM,
        "source_actual_schema_sha256": inspection["actual_schema_sha256"],
        "source_database_fingerprint": database_fingerprint,
        "state": inspection["state"],
        "operations": [] if inspection["state"] == "already_current" else [
            "install_outbox_delivery_schema", "replace_migration_marker",
        ],
        "backup_policy": (
            "operator-provided exact SQLite snapshot; verified before apply"
        ),
        "source_compatibility": (
            "only the frozen canonical 1304 variant with immutable outbox "
            "parent state is supported; other variants require restore or manual audit"
        ),
    }
    return {
        **payload,
        "plan_checksum": _sha256_bytes(_canonical(payload).encode("utf-8")),
    }


def build_plan(
    catalog_path: os.PathLike[str] | str,
    user_data_root: os.PathLike[str] | str,
) -> dict[str, Any]:
    catalog, root = _resolved_target(catalog_path, user_data_root)
    _require_rollback_journal_header(catalog)
    _require_checkpointed(catalog)
    con = _connect_ro(catalog)
    try:
        inspection = _inspect(con)
        database_fingerprint = _database_fingerprint(con)
    finally:
        con.close()
    _require_checkpointed(catalog)
    _require_rollback_journal_header(catalog)
    return _plan_report(catalog, root, inspection, database_fingerprint)


def apply_plan(
    catalog_path: os.PathLike[str] | str,
    user_data_root: os.PathLike[str] | str,
    *,
    exact_plan_checksum: str,
    backup_confirmed: bool,
    catalog_quiesced_confirmed: bool,
    backup_path: os.PathLike[str] | str,
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if not backup_confirmed or not catalog_quiesced_confirmed:
        raise MigrationRefused(
            "apply requires backup-confirmed and catalog-quiesced-confirmed"
        )
    if not exact_plan_checksum:
        raise MigrationRefused("apply requires the exact dry-run plan checksum")
    plan = build_plan(catalog_path, user_data_root)
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
        raise MigrationRefused(
            "verified backup_path must be a distinct existing file"
        )
    if os.path.normcase(str(backup)) != os.path.normcase(str(backup_lexical)):
        raise MigrationRefused("symlinked backup_path is not allowed")
    if backup.stat().st_nlink != 1:
        raise MigrationRefused("hard-linked backup_path is not allowed")
    backup_identity = _file_identity(backup)

    _require_rollback_journal_header(catalog)
    _require_rollback_journal_header(backup, label="backup")
    _require_checkpointed(catalog)
    _require_checkpointed(backup, label="backup")
    source = _connect_ro(catalog)
    copy = _connect_ro(backup)
    try:
        try:
            backup_inspection = _inspect(copy)
        except MigrationRefused as exc:
            raise MigrationRefused(
                "backup does not contain the exact source schema"
            ) from exc
        if backup_inspection["state"] != "migration_required":
            raise MigrationRefused("backup does not contain the exact source schema")
        if (source.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or
                copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok"):
            raise MigrationRefused("source or backup failed SQLite integrity_check")
        if (source.execute("PRAGMA foreign_key_check").fetchone() is not None or
                copy.execute("PRAGMA foreign_key_check").fetchone() is not None):
            raise MigrationRefused("source or backup failed SQLite foreign_key_check")
        source_fingerprint = _database_fingerprint(source)
        if source_fingerprint != plan["source_database_fingerprint"]:
            raise MigrationRefused("source changed after planning")
        if source_fingerprint != _database_fingerprint(copy):
            raise MigrationRefused("backup logical snapshot does not match source")
    finally:
        source.close()
        copy.close()
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

    con = sqlite3.connect(catalog)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.execute("BEGIN IMMEDIATE")
        if _inspect(con)["state"] != "migration_required":
            raise MigrationRefused("source changed after planning")
        if _database_fingerprint(con) != plan["source_database_fingerprint"]:
            raise MigrationRefused("catalog data changed after backup verification")
        _install_target(con)
        if fault_hook:
            fault_hook("after_schema")
        con.execute(
            "DELETE FROM workspace_schema_migrations WHERE version=?",
            (SOURCE_VERSION,),
        )
        con.execute(
            "INSERT INTO workspace_schema_migrations("
            "version,name,checksum,actual_schema_sha256,applied_at) "
            "VALUES(?,?,?,?,strftime('%s','now'))",
            (TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM,
             TARGET_ACTUAL_SCHEMA_SHA256),
        )
        if _inspect(con)["state"] != "already_current":
            raise MigrationRefused("target schema postcondition failed")
        if con.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise MigrationRefused("target foreign key postcondition failed")
        if fault_hook:
            fault_hook("before_commit")
        _reverify_backup(
            catalog,
            backup,
            plan["source_database_fingerprint"],
            backup_identity,
        )
        committed_report = _plan_report(
            catalog,
            root,
            {
                "state": "already_current",
                "actual_schema_sha256": TARGET_ACTUAL_SCHEMA_SHA256,
            },
            _database_fingerprint(con),
        )
        commit_recovered = schema_1304._commit_with_outcome_convergence(
            con,
            catalog=catalog,
            root=root,
            inspect=_inspect,
            expected_source_fingerprint=plan["source_database_fingerprint"],
            expected_target_fingerprint=committed_report[
                "source_database_fingerprint"
            ],
        )
    except Exception:
        try:
            transaction_active = con.in_transaction
        except sqlite3.Error:
            transaction_active = False
        if transaction_active:
            con.rollback()
        raise
    finally:
        con.close()
    return {
        **committed_report,
        "applied": True,
        "applied_plan_checksum": exact_plan_checksum,
        "backup_verification": backup_verification,
        "commit_recovered": commit_recovered,
    }


def _reverify_backup(
    catalog: Path,
    backup: Path,
    expected_fingerprint: str,
    expected_identity: tuple[str, int, int, int, int, int],
) -> None:
    """Recheck the retained source backup at the last safe pre-commit point."""
    if (not backup.is_file() or os.path.samefile(backup, catalog) or
            backup.stat().st_nlink != 1 or
            _file_identity(backup) != expected_identity):
        raise MigrationRefused(
            "verified backup changed before commit: identity mismatch"
        )
    _require_checkpointed(backup, label="backup")
    _require_rollback_journal_header(backup, label="backup")
    con = _connect_ro(backup)
    try:
        try:
            inspection = _inspect(con)
            integrity_ok = str(
                con.execute("PRAGMA integrity_check").fetchone()[0]
            ) == "ok"
            foreign_keys_ok = (
                con.execute("PRAGMA foreign_key_check").fetchone() is None
            )
            fingerprint = _database_fingerprint(con)
        except (sqlite3.Error, MigrationRefused) as exc:
            raise MigrationRefused("verified backup changed before commit") from exc
        if (inspection["state"] != "migration_required" or not integrity_ok or
                not foreign_keys_ok or fingerprint != expected_fingerprint):
            raise MigrationRefused("verified backup changed before commit")
    finally:
        con.close()
    _require_checkpointed(backup, label="backup")


def _install_target(con: sqlite3.Connection) -> None:
    # executescript commits an active Python transaction, so execute complete
    # canonical statements individually inside the explicit transaction.
    statement = ""
    for line in TARGET_SQL.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            if statement.strip():
                con.execute(statement)
            statement = ""
    if statement.strip():
        raise MigrationRefused("canonical target DDL ended with an incomplete statement")
