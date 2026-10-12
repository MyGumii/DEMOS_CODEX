"""Guarded, forward-only NP-2C 1311 -> 1312 lifecycle-kernel migration.

Only the exact frozen 1311 workspace catalog is accepted.  This migration
replaces the lifecycle transition guard and installs two-way operation-ID
namespace exclusion.  It adds no command API, source mutation, route, worker,
resolver, watermark acknowledgement, or live rollout.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from typing import Any, Callable

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_page_tree_migration as schema_1311
from code_assist_v1 import notion_workspace_store as store


SOURCE_VERSION = 2026081311
SOURCE_NAME = "notion_workspace_v2_np2c_page_tree_foundation"
SOURCE_CHECKSUM = (
    "sha256:bb88d9c7e7937d0e29086d20ebd0c7a567658678daca0dd31c931634431cf723"
)
SOURCE_ACTUAL_SCHEMA_SHA256 = (
    "sha256:7ef7fcee1e78a641388de16e55937ad73fd549d97aa33de7221c215d19da9517"
)
TARGET_VERSION = 2026081312
TARGET_NAME = "notion_workspace_v2_np2c_lifecycle_kernel_guards"
TARGET_CHECKSUM = (
    "sha256:effcfb238406228b79ad780e39c0577de4ac851f881af520c92b627b98b529f6"
)
TARGET_ACTUAL_SCHEMA_SHA256 = (
    "sha256:49e388386f2a90ea23fe8861554c50274b127de2ad7aa171e78bb4e9ded074e1"
)

SOURCE_SQL = store._SCHEMA_1311_SQL
TARGET_SQL = store._SCHEMA_1312_SQL
LIFECYCLE_KERNEL_OBJECTS = frozenset({
    "trg_workspace_page_lifecycle_command_transition",
    "trg_workspace_page_lifecycle_command_operation_namespace_insert",
    "trg_workspace_operation_lifecycle_namespace_insert",
})

if (
    schema_1311.TARGET_VERSION,
    schema_1311.TARGET_NAME,
    schema_1311.TARGET_CHECKSUM,
    schema_1311.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 1311 lifecycle-kernel source contract drifted")
if (
    store._SCHEMA_1311_VERSION,
    store._SCHEMA_1311_NAME,
    store._SCHEMA_1311_CHECKSUM,
    store._SCHEMA_1311_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen store 1311 contract drifted")
if (
    store._SCHEMA_1312_VERSION,
    store._SCHEMA_1312_NAME,
    store._SCHEMA_1312_CHECKSUM,
    store._SCHEMA_1312_ACTUAL_SCHEMA_SHA256,
) != (
    TARGET_VERSION,
    TARGET_NAME,
    TARGET_CHECKSUM,
    TARGET_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("runtime 1312 lifecycle-kernel target contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 1311 lifecycle-kernel source DDL drifted")
if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
    raise RuntimeError("frozen 1312 lifecycle-kernel target DDL drifted")

SOURCE_OBJECTS, _computed_source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _computed_target_actual = migration_core._schema_contract(TARGET_SQL)
if _computed_source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1311 lifecycle-kernel source objects drifted")
if _computed_target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1312 lifecycle-kernel target objects drifted")
if not LIFECYCLE_KERNEL_OBJECTS.issubset(
    {item["name"] for item in TARGET_OBJECTS}
):
    raise RuntimeError("frozen 1312 lifecycle-kernel objects are incomplete")

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
    store._LIFECYCLE_KERNEL_SCHEMA_SQL,
    "code_assist.notion_workspace_lifecycle_kernel_migration.v1",
    (
        "replace_lifecycle_transition_with_exact_lease_and_fencing_guards",
        "exclude_prepared_commands_from_workspace_operation_namespace",
        "allow_only_exact_finalized_operation_commitment",
        "refuse_untrusted_pre_kernel_lifecycle_commands",
        "preserve_page_tree_bindings_navigation_and_terminal_free_catalog",
        "replace_migration_marker",
    ),
    "1311",
    "1312",
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
            "np2c_lifecycle_kernel_migration_guard" in str(exc)
            or "existing_command_count=0" in str(exc)
        ):
            raise MigrationRefused(
                "1311 catalog contains untrusted pre-kernel lifecycle commands"
            ) from None
        raise


__all__ = [
    "LIFECYCLE_KERNEL_OBJECTS",
    "MigrationRefused",
    "SOURCE_VERSION",
    "TARGET_VERSION",
    "apply_plan",
    "build_plan",
]
