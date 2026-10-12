"""Guarded, forward-only NP-2A 1307 -> 1308 quarantine migration.

Only the exact frozen 1307 catalog is accepted.  Apply uses the reviewed
in-memory image builder and handle-relative replacement path shared by the
earlier workspace migrations; request paths never import or invoke this file.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from code_assist_v1 import notion_workspace_healing_apply_migration as schema_1307
from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_store as store


SOURCE_VERSION = 2026081307
SOURCE_NAME = "notion_workspace_v2_np2a_healing_apply_fences"
SOURCE_CHECKSUM = (
    "sha256:161491ac8f1a75444777049d1436a1874bccc15848377e9c5a6343b66a0a275f"
)
SOURCE_ACTUAL_SCHEMA_SHA256 = (
    "sha256:04ff6c79fa7d8e3c51823128a9defaca9253f88b0bb73209475349f94609f825"
)
TARGET_VERSION = 2026081308
TARGET_NAME = "notion_workspace_v2_np2a_quarantine_reasons"
TARGET_CHECKSUM = (
    "sha256:30e8b20db620f743f47a2af0f31ab78213ed35e4adbf8d5b9e4f406c6ed6ccd7"
)
TARGET_ACTUAL_SCHEMA_SHA256 = (
    "sha256:72659383f302480d79c816cafba2fe491bc8c0fed43d3e29f9baccd51208fbd9"
)

SOURCE_SQL = schema_1307.TARGET_SQL
TARGET_SQL = store._SCHEMA_1308_SQL
QUARANTINE_REASON_OBJECTS = frozenset({
    "trg_page_source_quarantine_metadata_insert",
    "trg_page_source_quarantine_metadata_update",
})

if (
    schema_1307.TARGET_VERSION,
    schema_1307.TARGET_NAME,
    schema_1307.TARGET_CHECKSUM,
    schema_1307.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 1307 quarantine migration source contract drifted")
if (
    2026081308,
    "notion_workspace_v2_np2a_quarantine_reasons",
    store._SCHEMA_1308_CHECKSUM,
) != (TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM):
    raise RuntimeError("frozen 1308 quarantine target contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 1307 quarantine source DDL drifted")
if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
    raise RuntimeError("frozen 1308 quarantine target DDL drifted")

SOURCE_OBJECTS, _computed_source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _computed_target_actual = migration_core._schema_contract(TARGET_SQL)
if _computed_source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1307 quarantine source object contract drifted")
if _computed_target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1308 quarantine target object contract drifted")

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
    store._QUARANTINE_SCHEMA_SQL,
    "code_assist.notion_workspace_quarantine_migration.v1",
    (
        "add_quarantine_reason",
        "add_quarantine_source_generation",
        "backfill_legacy_quarantine_metadata",
        "install_quarantine_lifecycle_guards",
        "replace_migration_marker",
    ),
    "1307",
    "1308",
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


__all__ = [
    "MigrationRefused",
    "QUARANTINE_REASON_OBJECTS",
    "SOURCE_VERSION",
    "TARGET_VERSION",
    "apply_plan",
    "build_plan",
]
