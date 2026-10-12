"""Guarded, forward-only NP-2C 1310 -> 1311 page-tree migration.

Only the exact frozen 1310 workspace catalog is accepted.  The migration adds
an unmaterialized tree state, empty canonical node storage, and an empty
service-fenced lifecycle command saga.  It never reads a legacy source shard,
fabricates a tree or command receipt, enables a route/worker/resolver, or runs
from request/startup paths.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_navigation_migration as schema_1310
from code_assist_v1 import notion_workspace_store as store


SOURCE_VERSION = 2026081310
SOURCE_NAME = "notion_workspace_v2_np2b_navigation_state"
SOURCE_CHECKSUM = (
    "sha256:72058812be6f0b92db756d5648d2cd9a0741d4ad4f4f9c62377d128ca0b581b3"
)
SOURCE_ACTUAL_SCHEMA_SHA256 = (
    "sha256:d8935af134fa12cdd5f0f803e15cba798dece1831cfeeee7673e0dc0ef7d7b33"
)
TARGET_VERSION = 2026081311
TARGET_NAME = "notion_workspace_v2_np2c_page_tree_foundation"
TARGET_CHECKSUM = (
    "sha256:bb88d9c7e7937d0e29086d20ebd0c7a567658678daca0dd31c931634431cf723"
)
TARGET_ACTUAL_SCHEMA_SHA256 = (
    "sha256:7ef7fcee1e78a641388de16e55937ad73fd549d97aa33de7221c215d19da9517"
)

SOURCE_SQL = store._SCHEMA_1310_SQL
TARGET_SQL = store._SCHEMA_1311_SQL
PAGE_TREE_OBJECTS = frozenset({
    "workspace_page_tree_states",
    "workspace_page_tree_nodes",
    "workspace_page_lifecycle_commands",
    "idx_workspace_page_tree_root_ordinal",
    "idx_workspace_page_tree_child_ordinal",
    "idx_workspace_page_tree_active_parent",
    "idx_workspace_page_lifecycle_commands_status",
    "idx_workspace_page_lifecycle_commands_live_target",
    "trg_workspace_page_tree_state_workspace_insert",
    "trg_workspace_page_tree_state_duplicate_insert",
    "trg_workspace_page_tree_state_transition",
    "trg_workspace_page_tree_state_immutable_delete",
    "trg_workspace_page_tree_node_service_insert",
    "trg_workspace_page_tree_node_binding_insert",
    "trg_workspace_page_tree_node_duplicate_insert",
    "trg_workspace_page_tree_node_transition",
    "trg_workspace_page_tree_node_immutable_delete",
    "trg_workspace_page_lifecycle_command_initial",
    "trg_workspace_page_lifecycle_command_duplicate_insert",
    "trg_workspace_page_lifecycle_command_transition",
    "trg_workspace_page_lifecycle_command_immutable_delete",
})

if (
    schema_1310.TARGET_VERSION,
    schema_1310.TARGET_NAME,
    schema_1310.TARGET_CHECKSUM,
    schema_1310.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 1310 page-tree migration source contract drifted")
if (
    store._SCHEMA_1310_VERSION,
    store._SCHEMA_1310_NAME,
    store._SCHEMA_1310_CHECKSUM,
    store._SCHEMA_1310_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen store 1310 contract drifted")
if (
    store._SCHEMA_1311_VERSION,
    store._SCHEMA_1311_NAME,
    store._SCHEMA_1311_CHECKSUM,
    store._SCHEMA_1311_ACTUAL_SCHEMA_SHA256,
) != (
    TARGET_VERSION,
    TARGET_NAME,
    TARGET_CHECKSUM,
    TARGET_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("runtime 1311 page-tree target contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 1310 page-tree source DDL drifted")
if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
    raise RuntimeError("frozen 1311 page-tree target DDL drifted")

SOURCE_OBJECTS, _computed_source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _computed_target_actual = migration_core._schema_contract(TARGET_SQL)
if _computed_source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1310 page-tree source object contract drifted")
if _computed_target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1311 page-tree target object contract drifted")
if not PAGE_TREE_OBJECTS.issubset({item["name"] for item in TARGET_OBJECTS}):
    raise RuntimeError("frozen 1311 page-tree objects are incomplete")

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
    store._PAGE_TREE_SCHEMA_SQL,
    "code_assist.notion_workspace_page_tree_migration.v1",
    (
        "install_unmaterialized_workspace_tree_state",
        "install_empty_generation_fenced_tree_nodes",
        "install_empty_service_fenced_lifecycle_command_saga",
        "preserve_existing_workspace_navigation_and_bindings",
        "fabricate_no_tree_nodes_or_lifecycle_receipts",
        "replace_migration_marker",
    ),
    "1310",
    "1311",
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
    "PAGE_TREE_OBJECTS",
    "SOURCE_VERSION",
    "TARGET_VERSION",
    "apply_plan",
    "build_plan",
]
