"""Guarded exact legacy incarnation-v2 -> change-consumer-v3 migration.

This operator-only module adds durable, non-destructive pass/work/operation
coordination to one explicitly selected owner shard.  It reuses the reviewed
copy-on-write backup and handle-relative replacement path from the v2
migrator.  No request/startup path imports this module and no watermark is
deleted or acknowledged by the migration.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from typing import Any, Callable

from code_assist_v1 import notion_legacy_change_migration as v1
from code_assist_v1 import notion_legacy_incarnation_migration as v2
from code_assist_v1.notion_legacy_change_consumer_contract import (
    EXPECTED_OBJECT_HASH,
    MIGRATION_CHECKSUM,
    MIGRATION_NAME,
    MIGRATION_VERSION,
    inspect_legacy_change_consumer_contract,
    install_schema_for_migration,
)
from code_assist_v1.notion_legacy_incarnation_contract import (
    EXPECTED_OBJECT_HASH as V2_EXPECTED_OBJECT_HASH,
    MIGRATION_CHECKSUM as V2_MIGRATION_CHECKSUM,
    MIGRATION_NAME as V2_MIGRATION_NAME,
    MIGRATION_VERSION as V2_MIGRATION_VERSION,
)


PLAN_VERSION = "notion.legacy-change-consumer.plan.v3"


class LegacyChangeConsumerMigrationRefused(
    v2.LegacyIncarnationMigrationRefused
):
    """Sanitized fail-closed v2 -> v3 migration refusal."""


def _canonical_json(value: Any) -> str:
    return v1._canonical_json(value)


def _sha256_text(value: str) -> str:
    return v1._sha256_text(value)


def _assert_frozen_contract() -> None:
    if (
        V2_MIGRATION_VERSION,
        V2_MIGRATION_NAME,
        V2_MIGRATION_CHECKSUM,
        V2_EXPECTED_OBJECT_HASH,
    ) != (
        v2.MIGRATION_VERSION,
        v2.MIGRATION_NAME,
        v2._MIGRATION_CHECKSUM,
        v2._EXPECTED_OBJECT_HASH,
    ):
        raise RuntimeError("frozen legacy v2 source contract drifted")
    if (
        MIGRATION_VERSION,
        MIGRATION_NAME,
        MIGRATION_CHECKSUM,
        EXPECTED_OBJECT_HASH,
    ) != (2026081303, "notion_legacy_change_consumer_v3",
          "sha256:9fc07c846c9e3e1aae98b3a8a8f0507a37fcf06a9ed203e4a5d650c4f3f71495",
          "sha256:69a96d7b7c2affd2c0ea83ce80064853d8e12ee8d3f0fbcf27fae3a14f429e6b"):
        raise RuntimeError("frozen legacy v3 target contract drifted")


_assert_frozen_contract()


def resolve_paths(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    expected_source_db_path: str | os.PathLike[str],
) -> dict[str, Path]:
    try:
        return v2.resolve_paths(
            user_data_root=user_data_root,
            source_owner_id=source_owner_id,
            expected_source_db_path=expected_source_db_path,
        )
    except v2.LegacyIncarnationMigrationRefused as exc:
        raise LegacyChangeConsumerMigrationRefused(str(exc)) from exc


def _inspect(con: sqlite3.Connection, *, verify_integrity: bool = True) -> dict[str, Any]:
    try:
        result = inspect_legacy_change_consumer_contract(
            con, verify_integrity=verify_integrity
        )
        markers = {
            int(row[0])
            for row in con.execute(
                "SELECT version FROM notion_legacy_schema_migrations"
            ).fetchall()
        }
        expected = (
            {v1.MIGRATION_VERSION, V2_MIGRATION_VERSION, MIGRATION_VERSION}
            if result["installed"]
            else {v1.MIGRATION_VERSION, V2_MIGRATION_VERSION}
        )
        if markers != expected:
            raise LegacyChangeConsumerMigrationRefused(
                "legacy change-consumer migration lineage is not exact"
            )
        return result
    except LegacyChangeConsumerMigrationRefused:
        raise
    except Exception as exc:
        raise LegacyChangeConsumerMigrationRefused(
            "legacy change-consumer schema is invalid"
        ) from exc


def _plan_body(
    paths: dict[str, Path], inspection: dict[str, Any], fingerprint: str
) -> dict[str, Any]:
    return {
        "plan_version": PLAN_VERSION,
        "source_migration_version": V2_MIGRATION_VERSION,
        "migration_version": MIGRATION_VERSION,
        "migration_name": MIGRATION_NAME,
        "migration_checksum": MIGRATION_CHECKSUM,
        "expected_object_hash": EXPECTED_OBJECT_HASH,
        "user_data_root": os.fspath(paths["root"]),
        "source_owner_id": paths["owner"].name,
        "source_db_path": os.fspath(paths["source"]),
        "source_database_fingerprint": fingerprint,
        "action": "already_current" if inspection["installed"] else "migrate_v2_to_v3",
        "quiescence_required": True,
        "backup_required": True,
        "destructive_ack": False,
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
            paths["source"],
            root=paths["root"],
            expected_resolved=paths["source"],
            read_only=True,
            expected_identity=identity,
        )
        try:
            inspection = _inspect(con)
            fingerprint = v1._logical_fingerprint(con)
        finally:
            con.close()
        v1._assert_rollback_journal(paths["source"])
    except LegacyChangeConsumerMigrationRefused:
        raise
    except Exception as exc:
        raise LegacyChangeConsumerMigrationRefused(
            "legacy change-consumer source is unavailable"
        ) from exc
    body = _plan_body(paths, inspection, fingerprint)
    return {**body, "plan_checksum": _sha256_text(_canonical_json(body))}


def _validate_plan(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict) or plan.get("plan_version") != PLAN_VERSION:
        raise LegacyChangeConsumerMigrationRefused("valid migration plan required")
    body = {key: value for key, value in plan.items() if key != "plan_checksum"}
    if str(plan.get("plan_checksum") or "") != _sha256_text(_canonical_json(body)):
        raise LegacyChangeConsumerMigrationRefused("migration plan checksum mismatch")


def _build_migrated_image(
    source: sqlite3.Connection,
    *,
    expected_source_fingerprint: str,
    fault_injector: Callable[[str], None] | None,
) -> tuple[bytes, str]:
    target = sqlite3.connect(":memory:")
    target.row_factory = sqlite3.Row
    target.execute("PRAGMA foreign_keys=ON")
    target.execute("PRAGMA temp_store=MEMORY")
    try:
        source.backup(target)
        if v1._logical_fingerprint(target) != expected_source_fingerprint:
            raise LegacyChangeConsumerMigrationRefused(
                "source changed before migration"
            )
        if _inspect(target)["installed"]:
            raise LegacyChangeConsumerMigrationRefused(
                "source schema changed before migration"
            )
        target.execute("BEGIN IMMEDIATE")
        install_schema_for_migration(target)
        now = int(target.execute("SELECT CAST(strftime('%s','now') AS INTEGER)").fetchone()[0])
        target.execute(
            """
            INSERT INTO notion_legacy_schema_migrations(
              version,name,checksum,actual_schema_sha256,applied_at
            ) VALUES(?,?,?,?,?)
            """,
            (MIGRATION_VERSION, MIGRATION_NAME, MIGRATION_CHECKSUM,
             EXPECTED_OBJECT_HASH, now),
        )
        if fault_injector:
            fault_injector("after_schema")
        target.commit()
        if not _inspect(target)["installed"]:
            raise LegacyChangeConsumerMigrationRefused(
                "migration postcondition failed"
            )
        final_fingerprint = v1._logical_fingerprint(target)
        image = target.serialize()
        if not image:
            raise LegacyChangeConsumerMigrationRefused("migration image is empty")
        return image, final_fingerprint
    except Exception:
        if target.in_transaction:
            target.rollback()
        raise
    finally:
        target.close()


def apply_plan(
    plan: dict[str, Any],
    *,
    confirm_checksum: str,
    source_quiesced_confirmed: bool,
    backup_confirmed: bool,
    expected_backup_path: str | os.PathLike[str],
    fault_injector: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    return v2._apply_forward_plan(
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
        refusal_type=LegacyChangeConsumerMigrationRefused,
        failure_message="legacy change-consumer migration failed",
    )


__all__ = [
    "LegacyChangeConsumerMigrationRefused",
    "MIGRATION_NAME",
    "MIGRATION_VERSION",
    "PLAN_VERSION",
    "apply_plan",
    "build_plan",
    "resolve_paths",
]
