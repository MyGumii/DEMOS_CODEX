"""Guarded NP-2C 1315 -> 1316 lifecycle-subtree schema migration.

Only the exact frozen 1315 catalog is accepted.  This additive migration
installs default-off duplicate/Trash/restore evidence, canonical lifecycle
labels, exact service-context guards, and deep-health-visible schema objects.
It executes no lifecycle command, opens no legacy shard, and enables no route,
worker, resolver, or runtime integration.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_store as store
from code_assist_v1 import notion_workspace_structural_finalize_migration as schema_1315


SOURCE_VERSION = 2026081315
SOURCE_NAME = "notion_workspace_v2_np2c_structural_finalize_authority"
SOURCE_CHECKSUM = (
    "sha256:a57f73b41a834c42edcdfbacbbf2aa1fc30dd34366d98d7c40a42337b4f0e932"
)
SOURCE_ACTUAL_SCHEMA_SHA256 = (
    "sha256:33eaed5b16671104d53443c7cd4b09b3471eabbce805161608a114e4528f5d82"
)
TARGET_VERSION = 2026081316
TARGET_NAME = "notion_workspace_v2_np2c_lifecycle_subtree_guards"
TARGET_CHECKSUM = (
    "sha256:04db7799402ea563228c6be4b016907c0f51ae4ba376fb176bb2a15ca588d81c"
)
TARGET_ACTUAL_SCHEMA_SHA256 = (
    "sha256:cb375ac9e36d42fb3896eabf11f32d6b35a131f5075c1512a51add3da6d32981"
)

SOURCE_SQL = store._SCHEMA_1315_SQL
TARGET_SQL = store._SCHEMA_1316_SQL
NEW_TABLES = frozenset({
    "workspace_page_lifecycle_subtree_manifests",
    "workspace_page_lifecycle_subtree_effects",
    "workspace_page_lifecycle_object_mappings",
})
NEW_INDEXES = frozenset({
    "idx_workspace_page_lifecycle_subtree_effects_command",
    "idx_workspace_page_lifecycle_object_mappings_command",
})
NEW_TRIGGERS = frozenset({
    "trg_workspace_page_lifecycle_subtree_command_insert",
    "trg_workspace_page_lifecycle_subtree_command_update",
    "trg_workspace_page_lifecycle_subtree_manifest_insert",
    "trg_workspace_page_lifecycle_subtree_manifest_update",
    "trg_workspace_page_lifecycle_subtree_manifest_delete",
    "trg_workspace_page_lifecycle_subtree_effect_insert",
    "trg_workspace_page_lifecycle_subtree_effect_update",
    "trg_workspace_page_lifecycle_subtree_effect_delete",
    "trg_workspace_page_lifecycle_object_mapping_insert",
    "trg_workspace_page_lifecycle_object_mapping_update",
    "trg_workspace_page_lifecycle_object_mapping_delete",
    "trg_page_source_lifecycle_subtree_duplicate_insert",
    "trg_page_source_lifecycle_subtree_transition",
    "trg_workspace_page_tree_node_canonical_lifecycle_insert",
    "trg_workspace_page_tree_node_canonical_lifecycle_update",
})
REPLACED_OBJECTS = frozenset({
    "trg_page_source_lifecycle_update",
    "trg_workspace_operation_lifecycle_namespace_insert",
    "workspace_page_lifecycle_commands",
    "workspace_page_tree_nodes",
})

if (
    schema_1315.TARGET_VERSION,
    schema_1315.TARGET_NAME,
    schema_1315.TARGET_CHECKSUM,
    schema_1315.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 1315 lifecycle-subtree source contract drifted")
if (
    store._SCHEMA_1315_VERSION,
    store._SCHEMA_1315_NAME,
    store._SCHEMA_1315_CHECKSUM,
    store._SCHEMA_1315_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen store 1315 contract drifted")
if (
    store._SCHEMA_1316_VERSION,
    store._SCHEMA_1316_NAME,
    store._SCHEMA_1316_CHECKSUM,
    store._SCHEMA_1316_ACTUAL_SCHEMA_SHA256,
) != (
    TARGET_VERSION,
    TARGET_NAME,
    TARGET_CHECKSUM,
    TARGET_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen store 1316 lifecycle-subtree target contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 1315 lifecycle-subtree source DDL drifted")
if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
    raise RuntimeError("frozen 1316 lifecycle-subtree target DDL drifted")

SOURCE_OBJECTS, _source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = migration_core._schema_contract(TARGET_SQL)
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1315 lifecycle-subtree source objects drifted")
if _target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1316 lifecycle-subtree target objects drifted")
_target_by_name = {item["name"]: item for item in TARGET_OBJECTS}
if not (NEW_TABLES | NEW_INDEXES | NEW_TRIGGERS | REPLACED_OBJECTS).issubset(
    _target_by_name
):
    raise RuntimeError("frozen 1316 lifecycle-subtree objects are incomplete")

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
    store._PAGE_LIFECYCLE_SUBTREE_SCHEMA_SQL,
    "code_assist.notion_workspace_lifecycle_subtree_migration.v1",
    (
        "refuse_live_prepared_or_source_applied_lifecycle_commands",
        "refuse_ambiguous_historical_duplicate_trash_restore_or_purge_commands",
        "refuse_ambiguous_historical_nonactive_tree_rows",
        "add_canonical_action_and_tree_lifecycle_state_without_fabrication",
        "add_immutable_subtree_manifest_effect_and_object_mapping_evidence",
        "add_exact_service_context_and_lifecycle_transition_guards",
        "add_deep_health_visible_required_objects",
        "preserve_bindings_tree_structural_evidence_and_ledger_history",
        "replace_migration_marker",
    ),
    "1315",
    "1316",
)


def _assert_migratable_lifecycle_state(
    catalog_path: os.PathLike[str] | str,
) -> None:
    path = Path(catalog_path).resolve(strict=True)
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        if con.execute(
            "SELECT 1 FROM workspace_page_lifecycle_commands "
            "WHERE status IN ('prepared','source_applied') LIMIT 1"
        ).fetchone():
            raise MigrationRefused(
                "1315 catalog contains a live page lifecycle command"
            )
        if con.execute(
            "SELECT 1 FROM workspace_page_lifecycle_commands "
            "WHERE action IN ('duplicate','archive','restore','purge') LIMIT 1"
        ).fetchone():
            raise MigrationRefused(
                "1315 catalog contains ambiguous historical lifecycle evidence"
            )
        if con.execute(
            "SELECT 1 FROM workspace_page_tree_nodes "
            "WHERE lifecycle_state<>'active' LIMIT 1"
        ).fetchone():
            raise MigrationRefused(
                "1315 catalog contains an ambiguous non-active page tree row"
            )
    except MigrationRefused:
        raise
    except sqlite3.Error:
        raise MigrationRefused(
            "1315 catalog lifecycle state could not be verified"
        ) from None
    finally:
        con.close()


def build_plan(
    catalog_path: os.PathLike[str] | str,
    user_data_root: os.PathLike[str] | str,
) -> dict[str, Any]:
    plan = migration_core.build_plan(catalog_path, user_data_root, _contract=_CONTRACT)
    if plan["state"] == "migration_required":
        _assert_migratable_lifecycle_state(catalog_path)
    return plan


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
    preflight = migration_core.build_plan(
        catalog_path, user_data_root, _contract=_CONTRACT
    )
    if preflight["state"] == "migration_required":
        _assert_migratable_lifecycle_state(catalog_path)
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
        message = str(exc)
        if "live_command_count=0" in message:
            raise MigrationRefused(
                "1315 catalog contains a live page lifecycle command"
            ) from None
        if "ambiguous_command_count=0" in message:
            raise MigrationRefused(
                "1315 catalog contains ambiguous historical lifecycle evidence"
            ) from None
        if "ambiguous_nonactive_tree_count=0" in message:
            raise MigrationRefused(
                "1315 catalog contains an ambiguous non-active page tree row"
            ) from None
        raise


__all__ = [
    "MigrationRefused",
    "NEW_INDEXES",
    "NEW_TABLES",
    "NEW_TRIGGERS",
    "REPLACED_OBJECTS",
    "SOURCE_VERSION",
    "TARGET_VERSION",
    "apply_plan",
    "build_plan",
]
