"""Guarded, forward-only NP-2A 1306 -> 1307 healing-apply migration.

Only the exact frozen 1306 catalog is accepted.  The secure image builder and
Windows handle-relative replacement implementation are shared with the
reviewed 1305 -> 1306 migrator; this module supplies a distinct exact schema
contract and never runs from request paths.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from code_assist_v1 import notion_workspace_healing_migration as schema_1306
from code_assist_v1 import notion_workspace_store as store


SOURCE_VERSION = 2026081306
SOURCE_NAME = "notion_workspace_v2_np2a_healing_runs"
SOURCE_CHECKSUM = (
    "sha256:6f5b23e4a276fd25aa96b1e427e8f4dce55e307fb059095baa46d6178ed13f52"
)
SOURCE_ACTUAL_SCHEMA_SHA256 = (
    "sha256:abaadac598bea97af44260bdbbf31a9ca883b65e20dbefaabacd1c5be18ec025"
)
TARGET_VERSION = 2026081307
TARGET_NAME = "notion_workspace_v2_np2a_healing_apply_fences"
TARGET_CHECKSUM = (
    "sha256:161491ac8f1a75444777049d1436a1874bccc15848377e9c5a6343b66a0a275f"
)
TARGET_ACTUAL_SCHEMA_SHA256 = (
    "sha256:04ff6c79fa7d8e3c51823128a9defaca9253f88b0bb73209475349f94609f825"
)

SOURCE_SQL = schema_1306.TARGET_SQL
TARGET_SQL = store._SCHEMA_SQL + store._HEALING_SCHEMA_1307_SQL
HEALING_APPLY_OBJECTS = frozenset({
    "workspace_healing_source_fences",
    "idx_workspace_healing_source_fences_owner",
    "trg_workspace_healing_source_fence_initial_state",
    "trg_workspace_healing_source_fence_immutable_update",
    "trg_workspace_healing_source_fence_immutable_delete",
})

if (
    schema_1306.TARGET_VERSION,
    schema_1306.TARGET_NAME,
    schema_1306.TARGET_CHECKSUM,
    schema_1306.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 1306 healing-apply migration source contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 1306 healing-apply source DDL drifted")
if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
    raise RuntimeError("frozen 1307 healing-apply target DDL drifted")

SOURCE_OBJECTS, _computed_source_actual = schema_1306._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _computed_target_actual = schema_1306._schema_contract(TARGET_SQL)
if _computed_source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1306 healing-apply source object contract drifted")
if _computed_target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1307 healing-apply target object contract drifted")

MigrationRefused = schema_1306.MigrationRefused
_CONTRACT = schema_1306._MigrationContract(
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
    store._HEALING_APPLY_SCHEMA_SQL,
    "code_assist.notion_workspace_healing_apply_migration.v1",
    ("upgrade_healing_run_schema", "install_healing_source_fences",
     "install_healing_page_source_lifecycle", "replace_migration_marker"),
    "1306",
    "1307",
)


def build_plan(
    catalog_path: os.PathLike[str] | str,
    user_data_root: os.PathLike[str] | str,
) -> dict[str, Any]:
    return schema_1306.build_plan(
        catalog_path,
        user_data_root,
        _contract=_CONTRACT,
    )


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
    return schema_1306.apply_plan(
        catalog_path,
        user_data_root,
        exact_plan_checksum=exact_plan_checksum,
        backup_confirmed=backup_confirmed,
        catalog_quiesced_confirmed=catalog_quiesced_confirmed,
        backup_path=backup_path,
        fault_hook=fault_hook,
        _contract=_CONTRACT,
    )


__all__ = [
    "HEALING_APPLY_OBJECTS",
    "MigrationRefused",
    "SOURCE_VERSION",
    "TARGET_VERSION",
    "apply_plan",
    "build_plan",
]
