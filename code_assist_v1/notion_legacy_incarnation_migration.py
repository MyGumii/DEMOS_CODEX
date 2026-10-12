"""Guarded exact v1 -> v2 legacy page-incarnation migration.

This module is operator-invoked only.  It reuses the already reviewed secure
path, backup, and handle-relative replacement primitives from the v1 migrator,
but independently authenticates the frozen v1 source and v2 target contracts.
It is never imported by request handling or by the side-effect-free reader.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable
import uuid

from code_assist_v1 import notion_legacy_change_migration as v1
from code_assist_v1.notion_legacy_change_contract import (
    EXPECTED_OBJECT_HASH as V1_EXPECTED_OBJECT_HASH,
    MIGRATION_CHECKSUM as V1_MIGRATION_CHECKSUM,
    MIGRATION_NAME as V1_MIGRATION_NAME,
    MIGRATION_VERSION as V1_MIGRATION_VERSION,
    MAX_GENERATION,
)
from code_assist_v1.notion_legacy_incarnation_contract import (
    INCARNATION_UDF_NAME,
    INCARNATION_WRITE_CONTEXT_UDF_NAME,
    inspect_legacy_incarnation_contract,
    new_legacy_page_incarnation_id,
)


PLAN_VERSION = "notion.legacy-page-incarnations.plan.v2"
MIGRATION_VERSION = 2026081302
MIGRATION_NAME = "notion_legacy_page_incarnations_v2"


class LegacyIncarnationMigrationRefused(Exception):
    """Sanitized fail-closed migration refusal."""


def _context_guard() -> str:
    return (
        f"typeof({INCARNATION_WRITE_CONTEXT_UDF_NAME}())<>'integer' "
        f"OR {INCARNATION_WRITE_CONTEXT_UDF_NAME}() IS NOT 1"
    )


_NOW = "CAST(strftime('%s','now') AS INTEGER)"
_DDL_STATEMENTS: tuple[str, ...] = (
    f"""
    CREATE TABLE notion_page_incarnation_history(
      page_id TEXT NOT NULL CHECK(
        typeof(page_id)='text' AND length(page_id) BETWEEN 1 AND 160
      ),
      incarnation_generation INTEGER NOT NULL CHECK(
        typeof(incarnation_generation)='integer' AND
        incarnation_generation BETWEEN 1 AND {MAX_GENERATION}
      ),
      incarnation_id TEXT NOT NULL UNIQUE CHECK(
        typeof(incarnation_id)='text' AND length(incarnation_id)=36 AND
        substr(incarnation_id,1,4)='inc_' AND
        substr(incarnation_id,5) NOT GLOB '*[^0-9a-f]*'
      ),
      created_at INTEGER NOT NULL CHECK(
        typeof(created_at)='integer' AND created_at>=0
      ),
      retired_at INTEGER CHECK(
        retired_at IS NULL OR (
          typeof(retired_at)='integer' AND retired_at>=created_at
        )
      ),
      PRIMARY KEY(page_id,incarnation_generation)
    ) WITHOUT ROWID
    """.strip(),
    "CREATE UNIQUE INDEX idx_notion_page_incarnation_history_active "
    "ON notion_page_incarnation_history(page_id) WHERE retired_at IS NULL",
    v1._trigger(
        "trg_notion_incarnation_history_insert_guard",
        "BEFORE INSERT",
        "notion_page_incarnation_history",
        "SELECT RAISE(ABORT,'legacy incarnation writer unavailable')",
        _context_guard(),
    ),
    v1._trigger(
        "trg_notion_incarnation_history_update_guard",
        "BEFORE UPDATE",
        "notion_page_incarnation_history",
        "SELECT RAISE(ABORT,'legacy incarnation history is immutable')",
        _context_guard()
        + " OR OLD.page_id IS NOT NEW.page_id"
        + " OR OLD.incarnation_generation IS NOT NEW.incarnation_generation"
        + " OR OLD.incarnation_id IS NOT NEW.incarnation_id"
        + " OR OLD.created_at IS NOT NEW.created_at"
        + " OR OLD.retired_at IS NOT NULL"
        + " OR NEW.retired_at IS NULL",
    ),
    v1._trigger(
        "trg_notion_incarnation_history_delete_guard",
        "BEFORE DELETE",
        "notion_page_incarnation_history",
        "SELECT RAISE(ABORT,'legacy incarnation history is immutable')",
    ),
    v1._trigger(
        "trg_notion_incarnation_pages_insert",
        "AFTER INSERT",
        "pages",
        f"""
        SELECT CASE WHEN {_context_guard()}
          THEN RAISE(ABORT,'legacy incarnation writer unavailable') END;
        UPDATE notion_page_incarnation_history
        SET retired_at=MAX(created_at,{_NOW})
        WHERE page_id=NEW.id AND retired_at IS NULL;
        SELECT CASE WHEN COALESCE((
          SELECT MAX(incarnation_generation)
          FROM notion_page_incarnation_history WHERE page_id=NEW.id
        ),0)>={MAX_GENERATION}
          THEN RAISE(ABORT,'legacy incarnation generation exhausted') END;
        INSERT INTO notion_page_incarnation_history(
          page_id,incarnation_generation,incarnation_id,created_at,retired_at
        ) SELECT NEW.id,
          COALESCE(MAX(incarnation_generation),0)+1,
          {INCARNATION_UDF_NAME}(),{_NOW},NULL
          FROM notion_page_incarnation_history WHERE page_id=NEW.id
        """.strip(),
    ),
    v1._trigger(
        "trg_notion_incarnation_pages_id_update",
        "AFTER UPDATE OF id",
        "pages",
        f"""
        SELECT CASE WHEN {_context_guard()}
          THEN RAISE(ABORT,'legacy incarnation writer unavailable') END;
        UPDATE notion_page_incarnation_history
        SET retired_at=MAX(created_at,{_NOW})
        WHERE page_id IN (OLD.id,NEW.id) AND retired_at IS NULL;
        SELECT CASE WHEN COALESCE((
          SELECT MAX(incarnation_generation)
          FROM notion_page_incarnation_history WHERE page_id=NEW.id
        ),0)>={MAX_GENERATION}
          THEN RAISE(ABORT,'legacy incarnation generation exhausted') END;
        INSERT INTO notion_page_incarnation_history(
          page_id,incarnation_generation,incarnation_id,created_at,retired_at
        ) SELECT NEW.id,
          COALESCE(MAX(incarnation_generation),0)+1,
          {INCARNATION_UDF_NAME}(),{_NOW},NULL
          FROM notion_page_incarnation_history WHERE page_id=NEW.id
        """.strip(),
        "OLD.id IS NOT NEW.id",
    ),
    v1._trigger(
        "trg_notion_incarnation_pages_delete",
        "AFTER DELETE",
        "pages",
        f"""
        SELECT CASE WHEN {_context_guard()}
          THEN RAISE(ABORT,'legacy incarnation writer unavailable') END;
        UPDATE notion_page_incarnation_history
        SET retired_at=MAX(created_at,{_NOW})
        WHERE page_id=OLD.id AND retired_at IS NULL
        """.strip(),
    ),
)

_MIGRATION_CHECKSUM = (
    "sha256:db029078d58d1220fb382a29840cf236149c5191b1c0c83fb26291fe1d8e7903"
)
_EXPECTED_OBJECT_HASH = (
    "sha256:207c56ae66ae10d0cd0e32753993572df0cbe4add779cb9f93379a459b73f42d"
)

_EXPECTED_OBJECTS = {
    "notion_page_incarnation_history",
    "idx_notion_page_incarnation_history_active",
    "trg_notion_incarnation_history_insert_guard",
    "trg_notion_incarnation_history_update_guard",
    "trg_notion_incarnation_history_delete_guard",
    "trg_notion_incarnation_pages_insert",
    "trg_notion_incarnation_pages_id_update",
    "trg_notion_incarnation_pages_delete",
}


def _canonical_json(value: Any) -> str:
    return v1._canonical_json(value)


def _sha256_text(value: str) -> str:
    return v1._sha256_text(value)


def _object_hash(con: sqlite3.Connection) -> str:
    placeholders = ",".join("?" for _ in _EXPECTED_OBJECTS)
    rows = con.execute(
        f"SELECT type,name,sql FROM sqlite_master WHERE name IN ({placeholders}) "
        "ORDER BY type,name",
        tuple(sorted(_EXPECTED_OBJECTS)),
    ).fetchall()
    return _sha256_text(_canonical_json([tuple(row) for row in rows]))


def _expected_object_hash() -> str:
    con = sqlite3.connect(":memory:")
    con.create_function(INCARNATION_WRITE_CONTEXT_UDF_NAME, 0, lambda: 1)
    con.create_function(INCARNATION_UDF_NAME, 0, new_legacy_page_incarnation_id)
    try:
        con.executescript("CREATE TABLE pages(id TEXT PRIMARY KEY);")
        for statement in _DDL_STATEMENTS:
            con.execute(statement)
        return _object_hash(con)
    finally:
        con.close()


def _assert_frozen_contract() -> None:
    if (
        V1_MIGRATION_VERSION,
        V1_MIGRATION_NAME,
        V1_MIGRATION_CHECKSUM,
        V1_EXPECTED_OBJECT_HASH,
    ) != (
        v1.MIGRATION_VERSION,
        v1.MIGRATION_NAME,
        v1._MIGRATION_CHECKSUM,
        v1._EXPECTED_OBJECT_HASH,
    ):
        raise RuntimeError("frozen legacy v1 source contract drifted")
    if _sha256_text("\n-- statement --\n".join(_DDL_STATEMENTS)) != _MIGRATION_CHECKSUM:
        raise RuntimeError("frozen legacy incarnation v2 DDL drifted")
    if _expected_object_hash() != _EXPECTED_OBJECT_HASH:
        raise RuntimeError("frozen legacy incarnation v2 object contract drifted")


_assert_frozen_contract()


def resolve_paths(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    expected_source_db_path: str | os.PathLike[str],
) -> dict[str, Path]:
    try:
        return v1.resolve_paths(
            user_data_root=user_data_root,
            source_owner_id=source_owner_id,
            expected_source_db_path=expected_source_db_path,
        )
    except v1.LegacyChangeMigrationRefused as exc:
        raise LegacyIncarnationMigrationRefused(str(exc)) from exc


def _inspect(con: sqlite3.Connection, *, verify_integrity: bool = True) -> dict[str, Any]:
    try:
        result = inspect_legacy_incarnation_contract(
            con, verify_integrity=verify_integrity
        )
        markers = {
            int(row[0])
            for row in con.execute(
                "SELECT version FROM notion_legacy_schema_migrations"
            ).fetchall()
        }
        expected = (
            {V1_MIGRATION_VERSION, MIGRATION_VERSION}
            if result["installed"]
            else {V1_MIGRATION_VERSION}
        )
        if markers != expected:
            raise LegacyIncarnationMigrationRefused(
                "legacy incarnation migration lineage is not exact"
            )
        return result
    except LegacyIncarnationMigrationRefused:
        raise
    except Exception as exc:
        raise LegacyIncarnationMigrationRefused(
            "legacy incarnation schema is invalid"
        ) from exc


def _plan_body(
    paths: dict[str, Path], inspection: dict[str, Any], fingerprint: str
) -> dict[str, Any]:
    return {
        "plan_version": PLAN_VERSION,
        "source_migration_version": V1_MIGRATION_VERSION,
        "migration_version": MIGRATION_VERSION,
        "migration_name": MIGRATION_NAME,
        "migration_checksum": _MIGRATION_CHECKSUM,
        "expected_object_hash": _EXPECTED_OBJECT_HASH,
        "user_data_root": os.fspath(paths["root"]),
        "source_owner_id": paths["owner"].name,
        "source_db_path": os.fspath(paths["source"]),
        "source_database_fingerprint": fingerprint,
        "action": "already_current" if inspection["installed"] else "migrate_v1_to_v2",
        "quiescence_required": True,
        "backup_required": True,
    }


def build_plan(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    expected_source_db_path: str | os.PathLike[str],
) -> dict[str, Any]:
    paths = resolve_paths(
        user_data_root=user_data_root,
        source_owner_id=source_owner_id,
        expected_source_db_path=expected_source_db_path,
    )
    try:
        v1._assert_rollback_journal(paths["source"])
        identity = v1._file_identity(paths["source"])
        con = v1._open_exact(
            paths["source"], root=paths["root"],
            expected_resolved=paths["source"], read_only=True,
            expected_identity=identity,
        )
        try:
            inspection = _inspect(con)
            fingerprint = v1._logical_fingerprint(con)
        finally:
            con.close()
        v1._assert_rollback_journal(paths["source"])
    except LegacyIncarnationMigrationRefused:
        raise
    except Exception as exc:
        raise LegacyIncarnationMigrationRefused(
            "legacy incarnation source is unavailable"
        ) from exc
    body = _plan_body(paths, inspection, fingerprint)
    return {**body, "plan_checksum": _sha256_text(_canonical_json(body))}


def _validate_plan(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict) or plan.get("plan_version") != PLAN_VERSION:
        raise LegacyIncarnationMigrationRefused("valid migration plan required")
    body = {key: value for key, value in plan.items() if key != "plan_checksum"}
    if str(plan.get("plan_checksum") or "") != _sha256_text(_canonical_json(body)):
        raise LegacyIncarnationMigrationRefused("migration plan checksum mismatch")


def _build_migrated_image(
    source: sqlite3.Connection,
    *,
    expected_source_fingerprint: str,
    fault_injector: Callable[[str], None] | None,
    incarnation_allocator: Callable[[], str] = new_legacy_page_incarnation_id,
) -> tuple[bytes, str]:
    target = sqlite3.connect(":memory:")
    target.row_factory = sqlite3.Row
    target.execute("PRAGMA foreign_keys=ON")
    target.execute("PRAGMA temp_store=MEMORY")
    target.create_function(INCARNATION_WRITE_CONTEXT_UDF_NAME, 0, lambda: 1)
    target.create_function(INCARNATION_UDF_NAME, 0, incarnation_allocator)
    try:
        source.backup(target)
        if v1._logical_fingerprint(target) != expected_source_fingerprint:
            raise LegacyIncarnationMigrationRefused("source changed before migration")
        if _inspect(target)["installed"]:
            raise LegacyIncarnationMigrationRefused(
                "source schema changed before migration"
            )
        target.execute("BEGIN IMMEDIATE")
        for statement in _DDL_STATEMENTS:
            target.execute(statement)
        now = int(target.execute(f"SELECT {_NOW}").fetchone()[0])
        target.execute(
            f"""
            INSERT INTO notion_page_incarnation_history(
              page_id,incarnation_generation,incarnation_id,created_at,retired_at
            )
            SELECT w.page_id,1,{INCARNATION_UDF_NAME}(),?,
                   CASE WHEN p.id IS NULL THEN ? ELSE NULL END
            FROM notion_page_change_watermarks w
            LEFT JOIN pages p ON p.id=w.page_id
            ORDER BY w.page_id
            """,
            (now, now),
        )
        target.execute(
            """
            INSERT INTO notion_legacy_schema_migrations(
              version,name,checksum,actual_schema_sha256,applied_at
            ) VALUES(?,?,?,?,?)
            """,
            (
                MIGRATION_VERSION,
                MIGRATION_NAME,
                _MIGRATION_CHECKSUM,
                _EXPECTED_OBJECT_HASH,
                now,
            ),
        )
        if fault_injector:
            fault_injector("after_schema")
        target.commit()
        if not _inspect(target)["installed"]:
            raise LegacyIncarnationMigrationRefused("migration postcondition failed")
        final_fingerprint = v1._logical_fingerprint(target)
        image = target.serialize()
        if not image:
            raise LegacyIncarnationMigrationRefused("migration image is empty")
        return image, final_fingerprint
    except Exception:
        if target.in_transaction:
            target.rollback()
        raise
    finally:
        target.close()


def _apply_forward_plan(
    plan: dict[str, Any],
    *,
    confirm_checksum: str,
    source_quiesced_confirmed: bool,
    backup_confirmed: bool,
    expected_backup_path: str | os.PathLike[str],
    fault_injector: Callable[[str], None] | None = None,
    validate_plan_fn: Callable[[dict[str, Any]], None],
    build_plan_fn: Callable[..., dict[str, Any]],
    resolve_paths_fn: Callable[..., dict[str, Path]],
    build_migrated_image_fn: Callable[..., tuple[bytes, str]],
    inspect_fn: Callable[..., dict[str, Any]],
    migration_version: int,
    refusal_type: type[Exception],
    failure_message: str,
) -> dict[str, Any]:
    """Shared secure image-replacement path for a frozen additive successor."""
    # Keep the reviewed refusal/convergence body identical for every frozen
    # successor while allowing each public wrapper to expose its own error type.
    LegacyIncarnationMigrationRefused = refusal_type
    validate_plan_fn(plan)
    if source_quiesced_confirmed is not True:
        raise LegacyIncarnationMigrationRefused("apply requires source quiescence")
    if backup_confirmed is not True:
        raise LegacyIncarnationMigrationRefused("apply requires backup confirmation")
    if confirm_checksum != plan["plan_checksum"]:
        raise LegacyIncarnationMigrationRefused(
            "confirm_checksum does not match the plan"
        )
    fresh = build_plan_fn(
        user_data_root=plan["user_data_root"],
        source_owner_id=plan["source_owner_id"],
        expected_source_db_path=plan["source_db_path"],
    )
    if fresh != plan:
        raise LegacyIncarnationMigrationRefused(
            "source changed after the approved dry-run"
        )
    if plan["action"] == "already_current":
        return {
            "status": "already_current",
            "migration_version": migration_version,
            "plan_checksum": plan["plan_checksum"],
            "commit_recovered": False,
        }

    paths = resolve_paths_fn(
        user_data_root=plan["user_data_root"],
        source_owner_id=plan["source_owner_id"],
        expected_source_db_path=plan["source_db_path"],
    )
    backup_guard: dict[str, Any] | None = None
    try:
        source_identity = v1._file_identity(paths["source"])
        backup_path = Path(expected_backup_path)
        backup, backup_guard = v1._backup(
            paths["source"], backup_path, paths["root"],
            expected_source_identity=source_identity,
            expected_fingerprint=plan["source_database_fingerprint"],
        )
        try:
            if fault_injector:
                fault_injector("after_backup")
            if v1._file_identity(paths["source"]) != source_identity:
                raise LegacyIncarnationMigrationRefused(
                    "source database identity changed"
                )
            v1._assert_rollback_journal(paths["source"])
            v1._revalidate_backup(
                backup, backup_guard, root=paths["root"],
                expected_fingerprint=plan["source_database_fingerprint"],
            )
        except BaseException:
            v1._close_backup_guard(backup_guard)
            raise

        directory_chain: list[int] = []
        stage_handle: int | None = None
        source_security_handle: int | None = None
        stage_location = "absent"
        commit_recovered = False
        final_fingerprint = ""
        replacement_metadata: tuple[Any, ...] | None = None
        try:
            directory_chain = v1._pinned_directory_chain(
                paths["root"], paths["source"].parent
            )
            v1._assert_rollback_journal(paths["source"])
            source_security_handle = v1._pin_windows_path(
                paths["source"], directory=False, allow_delete=True,
                share_write=False,
            )
            source_security_metadata = v1._windows_security_metadata(
                source_security_handle
            )
            if (
                source_security_metadata[2][1] != source_identity[1]
                or source_security_metadata[2][2] != source_identity[2]
            ):
                raise LegacyIncarnationMigrationRefused(
                    "source security metadata identity mismatch"
                )
            if source_security_metadata != backup_guard["source_security_metadata"]:
                try:
                    v1._dispose_windows_handle_on_close(int(backup_guard["handle"]))
                finally:
                    v1._close_backup_guard(backup_guard)
                raise LegacyIncarnationMigrationRefused(
                    "source security metadata changed after backup"
                )
            source = v1._open_exact(
                paths["source"], root=paths["root"],
                expected_resolved=paths["source"], read_only=True,
                expected_identity=source_identity,
            )
            try:
                image, final_fingerprint = build_migrated_image_fn(
                    source,
                    expected_source_fingerprint=plan["source_database_fingerprint"],
                    fault_injector=fault_injector,
                )
            finally:
                source.close()

            stage_path = paths["source"].with_name(
                f".{paths['source'].name}.legacy-incarnation-{uuid.uuid4().hex}.stage"
            )
            stage_handle = v1._create_pinned_windows_file(
                stage_path, parent_handle=directory_chain[-1], exclusive=True
            )
            stage_location = "stage"
            v1._write_flush_windows_handle(stage_handle, image)
            stage_security_metadata = v1._windows_security_metadata(stage_handle)
            if stage_security_metadata[:2] != source_security_metadata[:2]:
                raise LegacyIncarnationMigrationRefused(
                    "source security metadata is not safely replaceable"
                )
            if fault_injector:
                fault_injector("before_commit")
            v1._revalidate_backup(
                backup, backup_guard, root=paths["root"],
                expected_fingerprint=plan["source_database_fingerprint"],
            )
            v1._assert_exact_file(
                paths["source"], root=paths["root"],
                expected_resolved=paths["source"],
                expected_identity=source_identity, content_identity=False,
            )
            if v1._windows_security_metadata(source_security_handle) != source_security_metadata:
                v1._invalidate_backup_guard(backup_guard)
                raise LegacyIncarnationMigrationRefused(
                    "source security metadata changed before replacement"
                )
            if v1._windows_security_metadata(stage_handle) != stage_security_metadata:
                raise LegacyIncarnationMigrationRefused(
                    "staging security metadata changed before replacement"
                )
            verify_source = v1._open_exact(
                paths["source"], root=paths["root"],
                expected_resolved=paths["source"], read_only=True,
                expected_identity=source_identity,
            )
            try:
                inspected = inspect_fn(verify_source)
                if (
                    inspected["installed"]
                    or v1._logical_fingerprint(verify_source)
                    != plan["source_database_fingerprint"]
                ):
                    raise LegacyIncarnationMigrationRefused(
                        "source changed before replacement"
                    )
            finally:
                verify_source.close()

            stage_location = "unknown"
            try:
                v1._replace_windows_handle_relative(
                    stage_handle, parent_handle=directory_chain[-1],
                    target_name=paths["source"].name,
                )
                stage_location = "target"
            except v1.LegacyChangeMigrationRefused as replace_error:
                try:
                    converged = (
                        os.path.normcase(os.path.abspath(os.fspath(
                            v1._windows_final_path(stage_handle)
                        )))
                        == os.path.normcase(os.path.abspath(os.fspath(paths["source"])))
                        and v1._sha256_windows_handle(stage_handle)
                        == "sha256:" + hashlib.sha256(image).hexdigest()
                    )
                except Exception:
                    converged = False
                if not converged:
                    raise replace_error
                stage_location = "target"
                commit_recovered = True
            if v1._windows_security_metadata(stage_handle)[:2] != source_security_metadata[:2]:
                raise LegacyIncarnationMigrationRefused(
                    "replacement security metadata changed; manual audit required"
                )
            v1._flush_windows_handle(stage_handle)
            if fault_injector:
                fault_injector("after_replacement")
            replacement_metadata = v1._windows_security_metadata(stage_handle)
            if replacement_metadata != stage_security_metadata:
                raise LegacyIncarnationMigrationRefused(
                    "replacement security metadata changed; manual audit required"
                )
            if v1._sha256_windows_handle(stage_handle) != (
                "sha256:" + hashlib.sha256(image).hexdigest()
            ):
                raise LegacyIncarnationMigrationRefused(
                    "replacement content changed; restore from verified backup"
                )
            if os.path.normcase(os.path.abspath(os.fspath(
                v1._windows_final_path(stage_handle)
            ))) != os.path.normcase(os.path.abspath(os.fspath(paths["source"]))):
                raise LegacyIncarnationMigrationRefused(
                    "replacement path changed; restore from verified backup"
                )
        except BaseException:
            v1._close_backup_guard(backup_guard)
            if stage_location == "unknown":
                raise LegacyIncarnationMigrationRefused(
                    "replacement_outcome_indeterminate: manual audit required"
                ) from None
            raise
        finally:
            cleanup_error: BaseException | None = None
            if stage_handle is not None:
                if stage_location == "stage":
                    try:
                        v1._dispose_windows_handle_on_close(stage_handle)
                    except v1.LegacyChangeMigrationRefused as exc:
                        cleanup_error = exc
                v1._close_path_pins([stage_handle])
            if source_security_handle is not None:
                v1._close_path_pins([source_security_handle])
            v1._close_path_pins(directory_chain)
            if cleanup_error is not None:
                raise cleanup_error

        try:
            v1._revalidate_backup(
                backup, backup_guard, root=paths["root"],
                expected_fingerprint=plan["source_database_fingerprint"],
                verify_source_metadata=False,
                invalidate_on_security_change=False,
            )
            v1._assert_rollback_journal(paths["source"])
            final_identity = v1._file_identity(paths["source"])
            final_security_handle: int | None = None
            verify_final: sqlite3.Connection | None = None
            try:
                final_security_handle = v1._pin_windows_path(
                    paths["source"], directory=False, allow_delete=False,
                    share_write=False,
                )
                verify_final = v1._open_exact(
                    paths["source"], root=paths["root"],
                    expected_resolved=paths["source"], read_only=True,
                    expected_identity=final_identity,
                )
                final_metadata = v1._windows_security_metadata(final_security_handle)
                assert replacement_metadata is not None
                if (
                    final_metadata[:2] != replacement_metadata[:2]
                    or final_metadata[2][1] != replacement_metadata[2][1]
                    or final_metadata[2][2] != replacement_metadata[2][2]
                    or final_metadata[2][1] != final_identity[1]
                    or final_metadata[2][2] != final_identity[2]
                ):
                    raise LegacyIncarnationMigrationRefused(
                        "replacement security metadata changed; restore from verified backup"
                    )
                if (
                    not inspect_fn(verify_final)["installed"]
                    or v1._logical_fingerprint(verify_final) != final_fingerprint
                ):
                    raise LegacyIncarnationMigrationRefused(
                        "replacement postcondition failed; restore from verified backup"
                    )
            finally:
                if verify_final is not None:
                    verify_final.close()
                if final_security_handle is not None:
                    v1._close_path_pins([final_security_handle])
        finally:
            v1._close_backup_guard(backup_guard)
        return {
            "status": "applied",
            "migration_version": migration_version,
            "plan_checksum": plan["plan_checksum"],
            "source_database_fingerprint": final_fingerprint,
            "backup": backup,
            "commit_recovered": commit_recovered,
        }
    except LegacyIncarnationMigrationRefused:
        raise
    except v1.LegacyChangeMigrationRefused as exc:
        raise LegacyIncarnationMigrationRefused(str(exc)) from exc
    except Exception as exc:
        raise LegacyIncarnationMigrationRefused(failure_message) from exc
    finally:
        # Idempotent outer closure also covers cleanup exceptions raised from
        # the staging finally block before the normal postcondition path.
        v1._close_backup_guard(backup_guard)


def apply_plan(
    plan: dict[str, Any],
    *,
    confirm_checksum: str,
    source_quiesced_confirmed: bool,
    backup_confirmed: bool,
    expected_backup_path: str | os.PathLike[str],
    fault_injector: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Apply one unchanged, approved v1 -> v2 dry-run."""
    return _apply_forward_plan(
        plan,
        confirm_checksum=confirm_checksum,
        source_quiesced_confirmed=source_quiesced_confirmed,
        backup_confirmed=backup_confirmed,
        expected_backup_path=expected_backup_path,
        fault_injector=fault_injector,
        validate_plan_fn=_validate_plan,
        build_plan_fn=build_plan,
        resolve_paths_fn=resolve_paths,
        build_migrated_image_fn=_build_migrated_image,
        inspect_fn=_inspect,
        migration_version=MIGRATION_VERSION,
        refusal_type=LegacyIncarnationMigrationRefused,
        failure_message="legacy incarnation migration failed",
    )


__all__ = [
    "LegacyIncarnationMigrationRefused",
    "MIGRATION_NAME",
    "MIGRATION_VERSION",
    "PLAN_VERSION",
    "apply_plan",
    "build_plan",
    "resolve_paths",
]
