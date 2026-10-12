"""Guarded, forward-only NP-2A catalog schema migration.

This module is deliberately dependency-free at import time: callers inject an
exact catalog path and user-data root.  Only the reconstructed canonical 1303
variant is accepted; unknown 1303 catalogs require restore/manual audit.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable

from code_assist_v1 import notion_workspace_store as store

SOURCE_VERSION = 2026081303
SOURCE_NAME = "notion_workspace_v2_np2a_p1_closeout"
TARGET_VERSION = 2026081304
TARGET_NAME = "notion_workspace_v2_np2a_locator_projection"
_PROJECTION_TABLE = "workspace_page_source_projections"
TARGET_CHECKSUM = "sha256:05b69bd7fc72994e1f71e30e4fdbd85f66009def3aa061b34dd77f7a5f648b4d"
TARGET_ACTUAL_SCHEMA_SHA256 = (
    "sha256:2e65a41d6f75f1976c4d3c554a3e6a536a66275443ee91715b8af915dbc09ffe"
)

# 1305 added only these reviewed objects.  Reconstructing 1304 from the
# canonical DDL is accepted only when the resulting byte-for-byte SQL and
# sqlite_master contracts still match the frozen 1304 fingerprints below.
# This keeps the old 1303 -> 1304 migrator usable without pretending that the
# current runtime schema itself is still 1304.
_OUTBOX_DELIVERY_OBJECTS = frozenset({
    "workspace_outbox_deliveries",
    "idx_workspace_outbox_delivery_claim",
    "idx_workspace_outbox_delivery_parent_claim",
    "idx_workspace_outbox_delivery_live_token",
    "trg_workspace_outbox_immutable_update",
    "trg_workspace_outbox_immutable_delete",
    "trg_outbox_delivery_parent_allowlist_insert",
    "trg_outbox_delivery_initial_state_insert",
    "trg_outbox_delivery_identity_immutable",
    "trg_outbox_delivery_transition_guard",
    "trg_outbox_delivery_transition_semantics",
    "trg_outbox_delivery_immutable_delete",
})
_CREATE_OBJECT_RE = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?(?:TABLE|TRIGGER|INDEX)\s+"
    r"IF\s+NOT\s+EXISTS\s+(\w+)",
    re.IGNORECASE,
)


def _strip_schema_objects(sql: str, names: frozenset[str]) -> str:
    """Remove an exact reviewed set of complete CREATE statements."""
    output: list[str] = []
    found: set[str] = set()
    statement = ""
    for line in sql.splitlines(keepends=True):
        statement += line
        if not sqlite3.complete_statement(statement):
            continue
        match = _CREATE_OBJECT_RE.search(statement)
        if match is None:
            raise RuntimeError("canonical schema contains an unsupported statement")
        name = match.group(1)
        if name in names:
            if name in found:
                raise RuntimeError(f"duplicate canonical schema object: {name}")
            found.add(name)
        else:
            output.append(statement)
        statement = ""
    if statement.strip():
        raise RuntimeError("canonical schema ended with an incomplete statement")
    if found != set(names):
        missing = sorted(set(names) - found)
        raise RuntimeError(f"canonical schema is missing frozen objects: {missing}")
    return "".join(output)


TARGET_SQL = _strip_schema_objects(store._SCHEMA_SQL, _OUTBOX_DELIVERY_OBJECTS)
# Keep the original private spelling as a compatibility alias for local
# tooling written against the first version of this explicit migrator.
_TARGET_SQL = TARGET_SQL
if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
    raise RuntimeError("frozen 1304 migration target DDL drifted")


def _source_sql() -> str:
    """Reconstruct the single reviewed 1303 source variant from canonical DDL."""
    sql = _TARGET_SQL
    start = sql.index(f"CREATE TABLE IF NOT EXISTS {_PROJECTION_TABLE}")
    end = sql.index("CREATE TABLE IF NOT EXISTS workspace_operations")
    sql = sql[:start] + sql[end:]
    for name in (
        "idx_workspace_page_sources_generation",
        "idx_workspace_file_sources_page",
        "idx_workspace_public_sources_page",
    ):
        marker = (
            f"CREATE UNIQUE INDEX IF NOT EXISTS {name}"
            if name == "idx_workspace_page_sources_generation"
            else f"CREATE INDEX IF NOT EXISTS {name}"
        )
        start = sql.index(marker)
        end = sql.index(";", start) + 1
        if sql[end:end + 1] == "\n":
            end += 1
        sql = sql[:start] + sql[end:]
    return sql


SOURCE_SQL = _source_sql()
# Frozen contract for the one reconstructed, reviewed 1303 variant.  Changing
# later target DDL must never silently broaden the accepted source schema.
SOURCE_CHECKSUM = "sha256:63fd212da9c354ca9f7c7851670d6c49cd80d7ee97b9e75fd3fb24452d41d435"
SOURCE_ACTUAL_SCHEMA_SHA256 = (
    "sha256:396e8537eb907870be22622979236dc05ec5ebfd3b16f21c11537746ced16fd1"
)
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 1303 migration source DDL drifted")


class MigrationRefused(RuntimeError):
    """Raised when a catalog or invocation fails a migration guard."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _resolved_target(catalog_path: os.PathLike[str] | str,
                     user_data_root: os.PathLike[str] | str) -> tuple[Path, Path]:
    raw_catalog = os.fspath(catalog_path).strip()
    raw_root = os.fspath(user_data_root).strip()
    if not raw_catalog or not raw_root:
        raise MigrationRefused("catalog_path and user_data_root are required")
    catalog_lexical = Path(os.path.abspath(raw_catalog))
    root_lexical = Path(os.path.abspath(raw_root))
    expected_lexical = root_lexical / ".notion-workspace-v2" / "workspace.db"
    if os.path.normcase(str(catalog_lexical)) != os.path.normcase(str(expected_lexical)):
        raise MigrationRefused(
            "catalog_path must exactly equal user_data_root/.notion-workspace-v2/workspace.db"
        )
    try:
        root = root_lexical.resolve(strict=True)
        catalog = catalog_lexical.resolve(strict=True)
        expected = (root / ".notion-workspace-v2" / "workspace.db").resolve(strict=True)
    except OSError as exc:
        raise MigrationRefused("exact catalog_path or user_data_root does not exist") from exc
    if not root.is_dir() or not catalog.is_file() or catalog != expected:
        raise MigrationRefused("catalog_path is not the exact catalog under user_data_root")
    if (os.path.normcase(str(root)) != os.path.normcase(str(root_lexical)) or
            os.path.normcase(str(catalog)) != os.path.normcase(str(catalog_lexical))):
        raise MigrationRefused("symlinked user_data_root or catalog_path is not allowed")
    if catalog.stat().st_nlink != 1:
        raise MigrationRefused("hard-linked catalog_path is not allowed")
    return catalog, root


def _connect_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def _sidecars(path: Path) -> tuple[Path, ...]:
    return tuple(Path(f"{path}{suffix}") for suffix in ("-wal", "-shm", "-journal"))


def _require_checkpointed(path: Path, *, label: str = "catalog") -> None:
    if any(sidecar.exists() for sidecar in _sidecars(path)):
        raise MigrationRefused(
            f"{label}_not_checkpointed: SQLite sidecars must be absent before migration"
        )


def _require_rollback_journal_header(path: Path, *, label: str = "catalog") -> None:
    """Reject persistent WAL mode without opening SQLite and creating ``-shm``."""
    try:
        with path.open("rb") as source:
            header = source.read(20)
    except OSError as exc:
        raise MigrationRefused(f"{label}_header_unavailable") from exc
    if (len(header) < 20 or header[:16] != b"SQLite format 3\x00" or
            header[18:20] != b"\x01\x01"):
        raise MigrationRefused(
            f"{label}_journal_mode_unsupported: checkpointed rollback-journal SQLite required"
        )


def _file_identity(path: Path) -> tuple[str, int, int, int, int, int]:
    resolved = path.resolve(strict=True)
    stat = resolved.stat()
    return (
        os.path.normcase(str(resolved)),
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_nlink),
        int(stat.st_size),
        int(stat.st_mtime_ns),
    )


def _objects(con: sqlite3.Connection) -> list[dict[str, str]]:
    rows = con.execute(
        "SELECT type,name,tbl_name,COALESCE(sql,'') sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name,tbl_name"
    ).fetchall()
    return [{"type": str(r["type"]), "name": str(r["name"]),
             "table": str(r["tbl_name"]),
             "sql": " ".join(str(r["sql"] or "").split())} for r in rows]


def actual_schema_sha256(con: sqlite3.Connection) -> str:
    return _sha256_bytes(_canonical(_objects(con)).encode("utf-8"))


def _expected_source_objects() -> tuple[list[dict[str, str]], str]:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    try:
        con.executescript(SOURCE_SQL)
        objects = _objects(con)
        return objects, actual_schema_sha256(con)
    finally:
        con.close()


SOURCE_OBJECTS, _computed_source_actual = _expected_source_objects()
if _computed_source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1303 migration source object contract drifted")


def _marker(con: sqlite3.Connection) -> dict[str, Any]:
    try:
        rows = con.execute(
            "SELECT version,name,checksum,actual_schema_sha256,applied_at "
            "FROM workspace_schema_migrations ORDER BY version"
        ).fetchall()
    except sqlite3.Error as exc:
        raise MigrationRefused("unsupported_source_schema: migration marker unavailable") from exc
    if len(rows) != 1:
        raise MigrationRefused("unsupported_source_schema: expected exactly one marker")
    return dict(rows[0])


def _inspect(con: sqlite3.Connection) -> dict[str, Any]:
    marker = _marker(con)
    actual = actual_schema_sha256(con)
    objects = _objects(con)
    if int(marker["version"]) == TARGET_VERSION:
        if (str(marker["name"]), str(marker["checksum"]),
                str(marker["actual_schema_sha256"]), actual, objects) != (
            TARGET_NAME, TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256,
            TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS):
            raise MigrationRefused("unsupported_target_schema: 1304 marker/object mismatch")
        return {"state": "already_current", "marker": marker, "actual_schema_sha256": actual}
    expected = (SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM,
                SOURCE_ACTUAL_SCHEMA_SHA256, SOURCE_ACTUAL_SCHEMA_SHA256, SOURCE_OBJECTS)
    observed = (int(marker["version"]), str(marker["name"]), str(marker["checksum"]),
                str(marker["actual_schema_sha256"]), actual, objects)
    if observed != expected:
        raise MigrationRefused("unsupported_source_schema: exact 1303 contract mismatch")
    return {"state": "migration_required", "marker": marker, "actual_schema_sha256": actual}


def _target_contract() -> tuple[list[dict[str, str]], str]:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    try:
        con.executescript(_TARGET_SQL)
        return _objects(con), actual_schema_sha256(con)
    finally:
        con.close()


TARGET_OBJECTS, _computed_target_actual = _target_contract()
if _computed_target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1304 migration target object contract drifted")


def _plan_report(catalog: Path, root: Path, inspection: dict[str, Any],
                 database_fingerprint: str) -> dict[str, Any]:
    payload = {
        "format": "code_assist.notion_workspace_schema_migration.v1",
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
            "install_locator_projection_schema", "replace_migration_marker"
        ],
        "backup_policy": "operator-provided exact SQLite snapshot; verified before apply",
        "source_compatibility": (
            "only the frozen reconstructed canonical 1303 variant is supported; "
            "other 1303 checksums require restore or manual audit"
        ),
    }
    return {**payload, "plan_checksum": _sha256_bytes(_canonical(payload).encode("utf-8"))}


def build_plan(catalog_path: os.PathLike[str] | str,
               user_data_root: os.PathLike[str] | str) -> dict[str, Any]:
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


def apply_plan(catalog_path: os.PathLike[str] | str,
               user_data_root: os.PathLike[str] | str, *,
               exact_plan_checksum: str, backup_confirmed: bool,
               catalog_quiesced_confirmed: bool, backup_path: os.PathLike[str] | str,
               fault_hook: Callable[[str], None] | None = None) -> dict[str, Any]:
    if not backup_confirmed or not catalog_quiesced_confirmed:
        raise MigrationRefused("apply requires backup-confirmed and catalog-quiesced-confirmed")
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
    if (backup == catalog or os.path.samefile(backup, catalog) or
            not backup.is_file()):
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
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or copy.execute(
                "PRAGMA integrity_check").fetchone()[0] != "ok":
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
        source.close(); copy.close()
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
        _install_projection(con)
        if fault_hook:
            fault_hook("after_schema")
        con.execute("DELETE FROM workspace_schema_migrations WHERE version=?", (SOURCE_VERSION,))
        con.execute(
            "INSERT INTO workspace_schema_migrations(version,name,checksum,actual_schema_sha256,applied_at) "
            "VALUES(?,?,?,?,strftime('%s','now'))",
            (TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256),
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
            {"state": "already_current",
             "actual_schema_sha256": TARGET_ACTUAL_SCHEMA_SHA256},
            _database_fingerprint(con),
        )
        commit_recovered = _commit_with_outcome_convergence(
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


def _database_fingerprint(con: sqlite3.Connection) -> str:
    return store.logical_database_fingerprint_on_connection(con)


def _commit_with_outcome_convergence(
    con: sqlite3.Connection,
    *,
    catalog: Path,
    root: Path,
    inspect: Callable[[sqlite3.Connection], dict[str, Any]],
    expected_source_fingerprint: str,
    expected_target_fingerprint: str,
) -> bool:
    """Commit once and converge an exception to an exact durable outcome.

    A SQLite/driver wrapper is allowed to report an error after the underlying
    commit has made the target durable.  Re-running the migration in that case
    would make the caller observe a false failure.  Conversely, treating every
    post-transaction error as success could bless a replaced or partially
    modified catalog.  Therefore the exceptional path closes the writer and
    accepts only the exact reviewed source or target contract plus the exact
    precomputed logical fingerprint.

    The ordinary successful path performs no reopen and returns ``False``.
    ``True`` means an exception was recovered as the exact durable target.
    """
    try:
        con.commit()
        return False
    except Exception:
        # A live transaction means SQLite has not reported a durable commit.
        # Preserve the original failure after a best-effort rollback; callers
        # must never mistake a rollback failure for migration success.
        if con.in_transaction:
            try:
                con.rollback()
            except Exception:
                pass
            raise

        # The transaction ended despite the exception, so commit outcome is
        # ambiguous.  Close the writer before any read-only convergence check.
        try:
            con.close()
        except Exception:
            raise MigrationRefused(
                "commit_outcome_indeterminate: manual audit required"
            ) from None

        try:
            resolved_catalog, resolved_root = _resolved_target(catalog, root)
            if resolved_catalog != catalog or resolved_root != root:
                raise MigrationRefused("commit target identity changed")
            _require_checkpointed(resolved_catalog)
            _require_rollback_journal_header(resolved_catalog)
            recovered = _connect_ro(resolved_catalog)
            try:
                state = inspect(recovered)["state"]
                if str(recovered.execute(
                        "PRAGMA integrity_check"
                ).fetchone()[0]) != "ok":
                    raise MigrationRefused("commit target integrity mismatch")
                if recovered.execute(
                        "PRAGMA foreign_key_check"
                ).fetchone() is not None:
                    raise MigrationRefused("commit target foreign key mismatch")
                fingerprint = _database_fingerprint(recovered)
            finally:
                recovered.close()
            _require_checkpointed(resolved_catalog)
            _require_rollback_journal_header(resolved_catalog)
        except Exception:
            raise MigrationRefused(
                "commit_outcome_indeterminate: manual audit required"
            ) from None

        if (state == "already_current" and
                fingerprint == expected_target_fingerprint):
            return True
        if (state == "migration_required" and
                fingerprint == expected_source_fingerprint):
            raise
        raise MigrationRefused(
            "commit_outcome_indeterminate: manual audit required"
        ) from None


def _reverify_backup(
    catalog: Path,
    backup: Path,
    expected_fingerprint: str,
    expected_identity: tuple[str, int, int, int, int, int],
) -> None:
    """Recheck the operator backup at the last safe point before commit."""
    if (not backup.is_file() or os.path.samefile(backup, catalog) or
            backup.stat().st_nlink != 1 or
            _file_identity(backup) != expected_identity):
        raise MigrationRefused("verified backup changed before commit: identity mismatch")
    _require_checkpointed(backup, label="backup")
    _require_rollback_journal_header(backup, label="backup")
    con = _connect_ro(backup)
    try:
        try:
            inspection = _inspect(con)
            integrity_ok = str(con.execute("PRAGMA integrity_check").fetchone()[0]) == "ok"
            foreign_keys_ok = con.execute("PRAGMA foreign_key_check").fetchone() is None
            fingerprint = _database_fingerprint(con)
        except (sqlite3.Error, MigrationRefused) as exc:
            raise MigrationRefused("verified backup changed before commit") from exc
        if (inspection["state"] != "migration_required" or not integrity_ok or
                not foreign_keys_ok or fingerprint != expected_fingerprint):
            raise MigrationRefused("verified backup changed before commit")
    finally:
        con.close()
    _require_checkpointed(backup, label="backup")


def _install_projection(con: sqlite3.Connection) -> None:
    # Run canonical CREATE IF NOT EXISTS statements one-by-one.  ``executescript``
    # is intentionally avoided because it commits an active Python transaction.
    statement = ""
    for line in _TARGET_SQL.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            if statement.strip():
                con.execute(statement)
            statement = ""
    if statement.strip():
        raise MigrationRefused("canonical target DDL ended with an incomplete statement")
