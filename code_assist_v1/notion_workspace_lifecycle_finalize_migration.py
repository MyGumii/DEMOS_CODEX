"""Guarded NP-2C 1312 -> 1313 rename-catalog-finalizer migration.

Only the exact frozen 1312 catalog is accepted.  The migration adds the
workspace-version CAS column and lifecycle-only page-source transition guard.
It never opens a legacy shard, runs a command, registers a worker/route, or
acknowledges a source watermark.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from typing import Any, Callable

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_lifecycle_kernel_migration as schema_1312
from code_assist_v1 import notion_workspace_store as store


SOURCE_VERSION = 2026081312
SOURCE_NAME = "notion_workspace_v2_np2c_lifecycle_kernel_guards"
SOURCE_CHECKSUM = (
    "sha256:effcfb238406228b79ad780e39c0577de4ac851f881af520c92b627b98b529f6"
)
SOURCE_ACTUAL_SCHEMA_SHA256 = (
    "sha256:49e388386f2a90ea23fe8861554c50274b127de2ad7aa171e78bb4e9ded074e1"
)
TARGET_VERSION = 2026081313
TARGET_NAME = "notion_workspace_v2_np2c_rename_catalog_finalize"
TARGET_CHECKSUM = (
    "sha256:6be5b47275cf0dbe0b6b244c3f71672662f8ce648d1efa446208a94488da1637"
)
TARGET_ACTUAL_SCHEMA_SHA256 = (
    "sha256:e971256344e262c59990901b3a6816ecac7be760a31c286f68c5e03d4d491595"
)

SOURCE_SQL = store._SCHEMA_1312_SQL
TARGET_SQL = store._SCHEMA_1313_SQL
LIFECYCLE_FINALIZE_OBJECTS = frozenset({
    "trg_page_source_lifecycle_update",
    "trg_workspace_page_lifecycle_expected_workspace_insert",
    "trg_workspace_page_lifecycle_expected_workspace_update",
})

if (
    schema_1312.TARGET_VERSION,
    schema_1312.TARGET_NAME,
    schema_1312.TARGET_CHECKSUM,
    schema_1312.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 1312 lifecycle-finalizer source contract drifted")
if (
    store._SCHEMA_1312_VERSION,
    store._SCHEMA_1312_NAME,
    store._SCHEMA_1312_CHECKSUM,
    store._SCHEMA_1312_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen store 1312 contract drifted")
if (
    store._SCHEMA_1313_VERSION,
    store._SCHEMA_1313_NAME,
    store._SCHEMA_1313_CHECKSUM,
    store._SCHEMA_1313_ACTUAL_SCHEMA_SHA256,
) != (
    TARGET_VERSION,
    TARGET_NAME,
    TARGET_CHECKSUM,
    TARGET_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen store 1313 lifecycle-finalizer target drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 1312 lifecycle-finalizer source DDL drifted")
if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
    raise RuntimeError("frozen 1313 lifecycle-finalizer target DDL drifted")

SOURCE_OBJECTS, _source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = migration_core._schema_contract(TARGET_SQL)
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1312 lifecycle-finalizer source objects drifted")
if _target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1313 lifecycle-finalizer target objects drifted")
if not LIFECYCLE_FINALIZE_OBJECTS.issubset(
    {item["name"] for item in TARGET_OBJECTS}
):
    raise RuntimeError("frozen 1313 lifecycle-finalizer objects are incomplete")

MigrationRefused = migration_core.MigrationRefused
_CONTRACT = migration_core._MigrationContract(
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
    store._PAGE_LIFECYCLE_FINALIZE_SCHEMA_SQL,
    "code_assist.notion_workspace_lifecycle_finalize_migration.v1",
    (
        "refuse_pre_finalizer_lifecycle_command_rows",
        "add_immutable_expected_workspace_version_cas",
        "add_lifecycle_only_active_binding_refresh_guard",
        "preserve_workspace_tree_navigation_healing_and_incarnation_history",
        "replace_migration_marker",
    ),
    "1312",
    "1313",
)


def build_plan(
    catalog_path: os.PathLike[str] | str,
    user_data_root: os.PathLike[str] | str,
) -> dict[str, Any]:
    return migration_core.build_plan(catalog_path, user_data_root, _contract=_CONTRACT)


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
    try:
        return migration_core.apply_plan(
            catalog_path,
            user_data_root,
            exact_plan_checksum=exact_plan_checksum,
            backup_confirmed=backup_confirmed,
            catalog_quiesced_confirmed=catalog_quiesced_confirmed,
            backup_path=backup_path,
            fault_hook=fault_hook,
            _contract=_CONTRACT,
        )
    except sqlite3.IntegrityError as exc:
        if (
            "np2c_lifecycle_finalize_migration_guard" in str(exc)
            or "existing_command_count=0" in str(exc)
        ):
            raise MigrationRefused(
                "1312 catalog contains pre-finalizer lifecycle commands"
            ) from None
        raise


__all__ = [
    "LIFECYCLE_FINALIZE_OBJECTS",
    "MigrationRefused",
    "SOURCE_VERSION",
    "TARGET_VERSION",
    "apply_plan",
    "build_plan",
]
