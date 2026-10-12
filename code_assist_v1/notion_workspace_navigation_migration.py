"""Guarded, forward-only NP-2B 1309 -> 1310 navigation migration.

Only the exact frozen 1309 workspace catalog is accepted.  The migration adds
empty per-member navigation state, sidebar-section preferences, and stable page
favorites.  It never fabricates user preferences, enables routes/UI, opens a
legacy source shard, or runs from request/startup paths.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_incarnation_migration as schema_1309
from code_assist_v1 import notion_workspace_store as store


SOURCE_VERSION = 2026081309
SOURCE_NAME = "notion_workspace_v2_np2a_incarnation_attestations"
SOURCE_CHECKSUM = (
    "sha256:ecc2a8b40e8d114015e05b1772701dd97bb99ac513db9fd041626408f5f127ec"
)
SOURCE_ACTUAL_SCHEMA_SHA256 = (
    "sha256:d79ecc5b79faacf3ca4db288e7978b8192df91b8c3f9e1d5a99f0d859e2e06b1"
)
TARGET_VERSION = 2026081310
TARGET_NAME = "notion_workspace_v2_np2b_navigation_state"
TARGET_CHECKSUM = (
    "sha256:72058812be6f0b92db756d5648d2cd9a0741d4ad4f4f9c62377d128ca0b581b3"
)
TARGET_ACTUAL_SCHEMA_SHA256 = (
    "sha256:d8935af134fa12cdd5f0f803e15cba798dece1831cfeeee7673e0dc0ef7d7b33"
)

SOURCE_SQL = store._SCHEMA_1309_SQL
TARGET_SQL = store._SCHEMA_1310_SQL
NAVIGATION_OBJECTS = frozenset({
    "workspace_navigation_states",
    "workspace_sidebar_section_preferences",
    "workspace_page_favorites",
    "idx_workspace_page_favorites_subject_order",
    "trg_workspace_navigation_state_initial",
    "trg_workspace_navigation_state_duplicate_insert",
    "trg_workspace_navigation_state_transition",
    "trg_workspace_navigation_state_immutable_delete",
    "trg_workspace_sidebar_section_insert_guard",
    "trg_workspace_sidebar_section_duplicate_insert",
    "trg_workspace_sidebar_section_immutable_update",
    "trg_workspace_sidebar_section_delete_guard",
    "trg_workspace_page_favorite_insert_guard",
    "trg_workspace_page_favorite_limit_insert",
    "trg_workspace_page_favorite_duplicate_insert",
    "trg_workspace_page_favorite_immutable_update",
    "trg_workspace_page_favorite_delete_guard",
})

if (
    schema_1309.TARGET_VERSION,
    schema_1309.TARGET_NAME,
    schema_1309.TARGET_CHECKSUM,
    schema_1309.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 1309 navigation migration source contract drifted")
if (
    store._SCHEMA_1309_VERSION,
    store._SCHEMA_1309_NAME,
    store._SCHEMA_1309_CHECKSUM,
    store._SCHEMA_1309_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen store 1309 contract drifted")
if (
    store._SCHEMA_1310_VERSION,
    store._SCHEMA_1310_NAME,
    store._SCHEMA_1310_CHECKSUM,
    store._SCHEMA_1310_ACTUAL_SCHEMA_SHA256,
) != (
    TARGET_VERSION,
    TARGET_NAME,
    TARGET_CHECKSUM,
    TARGET_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 1310 navigation target contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 1309 navigation source DDL drifted")
if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
    raise RuntimeError("frozen 1310 navigation target DDL drifted")

SOURCE_OBJECTS, _computed_source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _computed_target_actual = migration_core._schema_contract(TARGET_SQL)
if _computed_source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1309 navigation source object contract drifted")
if _computed_target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1310 navigation target object contract drifted")
if not NAVIGATION_OBJECTS.issubset({item["name"] for item in TARGET_OBJECTS}):
    raise RuntimeError("frozen 1310 navigation objects are incomplete")

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
    store._NAVIGATION_SCHEMA_SQL,
    "code_assist.notion_workspace_navigation_migration.v1",
    (
        "install_member_navigation_state",
        "install_sidebar_section_preferences",
        "install_generation_stable_page_favorites",
        "install_service_context_guards",
        "preserve_existing_rows_without_fabricated_preferences",
        "replace_migration_marker",
    ),
    "1309",
    "1310",
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
    "NAVIGATION_OBJECTS",
    "SOURCE_VERSION",
    "TARGET_VERSION",
    "apply_plan",
    "build_plan",
]
