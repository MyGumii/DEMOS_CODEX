"""Guarded NP-2C 1314 -> 1315 structural-finalizer authority migration.

Only the exact frozen 1314 catalog is accepted.  This migration replaces the
reviewed structural-finalizer authority triggers so a future offline finalizer
can advance complete sibling/subtree scopes, retain the generation of
source-mutated parents whose own structure is unchanged, and represent a root
target with a null parent exactly.  It executes no lifecycle command, opens no
legacy shard, and enables no runtime integration.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_store as store
from code_assist_v1 import notion_workspace_structural_effect_migration as schema_1314


SOURCE_VERSION = 2026081314
SOURCE_NAME = "notion_workspace_v2_np2c_structural_effect_guards"
SOURCE_CHECKSUM = (
    "sha256:c131d83534326ab1dfa8bf4d007412e1fb9e08c628c04e36de55287b79835b82"
)
SOURCE_ACTUAL_SCHEMA_SHA256 = (
    "sha256:2aad855cd6b9775850706f3c23c6ae3a494b908b4bce188791c0533a0e0acf14"
)
TARGET_VERSION = 2026081315
TARGET_NAME = "notion_workspace_v2_np2c_structural_finalize_authority"
TARGET_CHECKSUM = (
    "sha256:a57f73b41a834c42edcdfbacbbf2aa1fc30dd34366d98d7c40a42337b4f0e932"
)
TARGET_ACTUAL_SCHEMA_SHA256 = (
    "sha256:33eaed5b16671104d53443c7cd4b09b3471eabbce805161608a114e4528f5d82"
)

SOURCE_SQL = store._SCHEMA_1314_SQL
TARGET_SQL = store._SCHEMA_1315_SQL
REPLACED_OBJECTS = frozenset({
    "trg_workspace_page_tree_node_order_update",
    "trg_workspace_page_lifecycle_structural_manifest_insert",
    "trg_workspace_page_lifecycle_structural_effect_insert",
    "trg_workspace_operation_lifecycle_namespace_insert",
})

if (
    schema_1314.TARGET_VERSION,
    schema_1314.TARGET_NAME,
    schema_1314.TARGET_CHECKSUM,
    schema_1314.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 1314 structural-finalize source contract drifted")
if (
    store._SCHEMA_1314_VERSION,
    store._SCHEMA_1314_NAME,
    store._SCHEMA_1314_CHECKSUM,
    store._SCHEMA_1314_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen store 1314 contract drifted")
if (
    store._SCHEMA_1315_VERSION,
    store._SCHEMA_1315_NAME,
    store._SCHEMA_1315_CHECKSUM,
    store._SCHEMA_1315_ACTUAL_SCHEMA_SHA256,
) != (
    TARGET_VERSION,
    TARGET_NAME,
    TARGET_CHECKSUM,
    TARGET_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen store 1315 structural-finalize target drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 1314 structural-finalize source DDL drifted")
if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
    raise RuntimeError("frozen 1315 structural-finalize target DDL drifted")

SOURCE_OBJECTS, _source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = migration_core._schema_contract(TARGET_SQL)
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1314 structural-finalize source objects drifted")
if _target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1315 structural-finalize target objects drifted")
if REPLACED_OBJECTS != {
    item["name"]
    for item in TARGET_OBJECTS
    if item["name"] in REPLACED_OBJECTS
}:
    raise RuntimeError("frozen 1315 structural-finalize objects are incomplete")

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
    store._PAGE_LIFECYCLE_STRUCTURAL_FINALIZE_SCHEMA_SQL,
    "code_assist.notion_workspace_structural_finalize_migration.v1",
    (
        "refuse_live_prepared_or_source_applied_lifecycle_commands",
        "replace_order_generation_trigger_with_exact_effect_authority",
        "replace_structural_effect_trigger_with_null_safe_root_authority",
        "retain_generation_for_structurally_unchanged_parent_proof_refresh",
        "advance_generation_for_complete_sibling_and_subtree_scopes",
        "preserve_structural_manifests_effects_bindings_tree_and_ledger_history",
        "replace_migration_marker",
    ),
    "1314",
    "1315",
)


def _assert_no_live_lifecycle_command(
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
                "1314 catalog contains a live page lifecycle command"
            )
    except sqlite3.Error:
        raise MigrationRefused(
            "1314 catalog lifecycle state could not be verified"
        ) from None
    finally:
        con.close()


def build_plan(
    catalog_path: os.PathLike[str] | str,
    user_data_root: os.PathLike[str] | str,
) -> dict[str, Any]:
    plan = migration_core.build_plan(catalog_path, user_data_root, _contract=_CONTRACT)
    if plan["state"] == "migration_required":
        _assert_no_live_lifecycle_command(catalog_path)
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
        _assert_no_live_lifecycle_command(catalog_path)
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
        if "live_command_count=0" in str(exc):
            raise MigrationRefused(
                "1314 catalog contains a live page lifecycle command"
            ) from None
        raise


__all__ = [
    "MigrationRefused",
    "REPLACED_OBJECTS",
    "SOURCE_VERSION",
    "TARGET_VERSION",
    "apply_plan",
    "build_plan",
]
