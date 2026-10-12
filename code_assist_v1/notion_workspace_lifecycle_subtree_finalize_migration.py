"""Guarded NP-2C 1316 -> 1317 lifecycle-subtree finalizer authority.

The frozen 1316 catalog could record only transitioned subtree effects.  Real
duplicate/Trash/restore receipts also contain bounded active->active
parent/sibling effects.  This forward-only migration refuses every live
command or previously stored subtree row, rebuilds only those still-empty
evidence tables with the complete receipt bounds, and installs exact proof and
order transition authority.  It performs no source or catalog lifecycle
effect and is never imported by application/request paths.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_lifecycle_subtree_migration as schema_1316
from code_assist_v1 import notion_workspace_store as store


SOURCE_VERSION = 2026081316
SOURCE_NAME = "notion_workspace_v2_np2c_lifecycle_subtree_guards"
SOURCE_CHECKSUM = (
    "sha256:04db7799402ea563228c6be4b016907c0f51ae4ba376fb176bb2a15ca588d81c"
)
SOURCE_ACTUAL_SCHEMA_SHA256 = (
    "sha256:cb375ac9e36d42fb3896eabf11f32d6b35a131f5075c1512a51add3da6d32981"
)
TARGET_VERSION = 2026081317
TARGET_NAME = "notion_workspace_v2_np2c_lifecycle_subtree_finalize_authority"
TARGET_CHECKSUM = (
    "sha256:6cb890ccc026435715a1c9171b4d1fd0f74ae6896f63d3cdf66a27b6eec5b61b"
)
TARGET_ACTUAL_SCHEMA_SHA256 = (
    "sha256:650a031ff1f7d1794bde06d9f9479794d0c89fa2faba8e07762badfe3c500aad"
)

SOURCE_SQL = store._SCHEMA_1316_SQL
TARGET_SQL = store._SCHEMA_SQL + store._HEALING_SCHEMA_SQL
REPLACED_OBJECTS = frozenset({
    "workspace_page_lifecycle_subtree_manifests",
    "workspace_page_lifecycle_subtree_effects",
    "workspace_page_lifecycle_object_mappings",
    "idx_workspace_page_lifecycle_subtree_effects_command",
    "idx_workspace_page_lifecycle_object_mappings_command",
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
    "trg_page_source_lifecycle_update",
    "trg_page_source_lifecycle_subtree_duplicate_insert",
    "trg_page_source_lifecycle_subtree_transition",
    "trg_workspace_page_tree_node_canonical_lifecycle_insert",
    "trg_workspace_page_tree_node_canonical_lifecycle_update",
    "trg_workspace_page_tree_node_order_insert",
    "trg_workspace_page_tree_node_order_update",
    "trg_workspace_operation_lifecycle_namespace_insert",
})

if (
    schema_1316.TARGET_VERSION,
    schema_1316.TARGET_NAME,
    schema_1316.TARGET_CHECKSUM,
    schema_1316.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 1316 subtree-finalize source contract drifted")
if (
    store._SCHEMA_1316_VERSION,
    store._SCHEMA_1316_NAME,
    store._SCHEMA_1316_CHECKSUM,
    store._SCHEMA_1316_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen store 1316 contract drifted")
if (
    store.SCHEMA_VERSION,
    store.SCHEMA_MIGRATION_NAME,
    store._SCHEMA_CHECKSUM,
    store._SCHEMA_ACTUAL_SCHEMA_SHA256,
) != (
    TARGET_VERSION,
    TARGET_NAME,
    TARGET_CHECKSUM,
    TARGET_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("runtime 1317 subtree-finalize target contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 1316 subtree-finalize source DDL drifted")
if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
    raise RuntimeError("frozen 1317 subtree-finalize target DDL drifted")

SOURCE_OBJECTS, _source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = migration_core._schema_contract(TARGET_SQL)
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1316 subtree-finalize source objects drifted")
if _target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1317 subtree-finalize target objects drifted")
if not REPLACED_OBJECTS.issubset({item["name"] for item in TARGET_OBJECTS}):
    raise RuntimeError("frozen 1317 subtree-finalize objects are incomplete")

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
    store._PAGE_LIFECYCLE_SUBTREE_FINALIZE_SCHEMA_SQL,
    "code_assist.notion_workspace_lifecycle_subtree_finalize_migration.v1",
    (
        "refuse_live_page_lifecycle_commands",
        "refuse_any_preexisting_subtree_evidence",
        "rebuild_empty_subtree_evidence_for_full_bounded_receipts",
        "authorize_exact_active_to_active_source_proof_refresh",
        "authorize_exact_full_effect_order_generation",
        "preserve_all_non_subtree_catalog_state",
        "replace_migration_marker",
    ),
    "1316",
    "1317",
)


def _assert_empty_authority_state(catalog_path: os.PathLike[str] | str) -> None:
    path = Path(catalog_path).resolve(strict=True)
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        if con.execute(
            "SELECT 1 FROM workspace_page_lifecycle_commands "
            "WHERE status IN ('prepared','source_applied') LIMIT 1"
        ).fetchone():
            raise MigrationRefused("1316 catalog contains a live page lifecycle command")
        if any(
            con.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            for table in (
                "workspace_page_lifecycle_subtree_manifests",
                "workspace_page_lifecycle_subtree_effects",
                "workspace_page_lifecycle_object_mappings",
            )
        ):
            raise MigrationRefused("1316 catalog contains subtree evidence")
    except MigrationRefused:
        raise
    except sqlite3.Error:
        raise MigrationRefused(
            "1316 catalog subtree-finalize state could not be verified"
        ) from None
    finally:
        con.close()


def build_plan(
    catalog_path: os.PathLike[str] | str,
    user_data_root: os.PathLike[str] | str,
) -> dict[str, Any]:
    plan = migration_core.build_plan(catalog_path, user_data_root, _contract=_CONTRACT)
    if plan["state"] == "migration_required":
        _assert_empty_authority_state(catalog_path)
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
        _assert_empty_authority_state(catalog_path)
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
                "1316 catalog contains a live page lifecycle command"
            ) from None
        if "evidence_row_count=0" in message:
            raise MigrationRefused("1316 catalog contains subtree evidence") from None
        raise


__all__ = [
    "MigrationRefused",
    "REPLACED_OBJECTS",
    "SOURCE_VERSION",
    "TARGET_VERSION",
    "apply_plan",
    "build_plan",
]
