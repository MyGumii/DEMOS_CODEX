"""Guarded NP-2C 1313 -> 1314 structural-effect schema migration.

Only the exact frozen 1313 catalog is accepted.  The migration adds immutable
create/move/reorder manifests, bounded page effects, and generation-scoped
sibling ordinals.  It never executes a lifecycle command, opens a legacy
source shard, acknowledges a watermark, registers a route/worker, or enables
the resolver.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_lifecycle_finalize_migration as schema_1313
from code_assist_v1 import notion_workspace_store as store


SOURCE_VERSION = 2026081313
SOURCE_NAME = "notion_workspace_v2_np2c_rename_catalog_finalize"
SOURCE_CHECKSUM = (
    "sha256:6be5b47275cf0dbe0b6b244c3f71672662f8ce648d1efa446208a94488da1637"
)
SOURCE_ACTUAL_SCHEMA_SHA256 = (
    "sha256:e971256344e262c59990901b3a6816ecac7be760a31c286f68c5e03d4d491595"
)
TARGET_VERSION = 2026081314
TARGET_NAME = "notion_workspace_v2_np2c_structural_effect_guards"
TARGET_CHECKSUM = (
    "sha256:c131d83534326ab1dfa8bf4d007412e1fb9e08c628c04e36de55287b79835b82"
)
TARGET_ACTUAL_SCHEMA_SHA256 = (
    "sha256:2aad855cd6b9775850706f3c23c6ae3a494b908b4bce188791c0533a0e0acf14"
)

SOURCE_SQL = store._SCHEMA_1313_SQL
TARGET_SQL = store._SCHEMA_1314_SQL
STRUCTURAL_EFFECT_OBJECTS = frozenset({
    "workspace_page_lifecycle_structural_manifests",
    "workspace_page_lifecycle_structural_effects",
    "idx_workspace_page_lifecycle_structural_effects_command",
    "trg_workspace_page_lifecycle_structural_command_insert",
    "trg_workspace_page_lifecycle_structural_manifest_insert",
    "trg_workspace_page_lifecycle_structural_manifest_duplicate",
    "trg_workspace_page_lifecycle_structural_manifest_update",
    "trg_workspace_page_lifecycle_structural_manifest_delete",
    "trg_workspace_page_lifecycle_structural_effect_insert",
    "trg_workspace_page_lifecycle_structural_effect_update",
    "trg_workspace_page_lifecycle_structural_effect_delete",
    "trg_page_source_structural_create_insert",
    "trg_page_source_structural_create_activate",
    "trg_workspace_page_tree_node_order_insert",
    "trg_workspace_page_tree_node_order_update",
    "trg_page_source_lifecycle_update",
    "trg_workspace_operation_lifecycle_namespace_insert",
})

if (
    schema_1313.TARGET_VERSION,
    schema_1313.TARGET_NAME,
    schema_1313.TARGET_CHECKSUM,
    schema_1313.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 1313 structural-effect source contract drifted")
if (
    store._SCHEMA_1313_VERSION,
    store._SCHEMA_1313_NAME,
    store._SCHEMA_1313_CHECKSUM,
    store._SCHEMA_1313_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen store 1313 contract drifted")
if (
    store._SCHEMA_1314_VERSION,
    store._SCHEMA_1314_NAME,
    store._SCHEMA_1314_CHECKSUM,
    store._SCHEMA_1314_ACTUAL_SCHEMA_SHA256,
) != (
    TARGET_VERSION,
    TARGET_NAME,
    TARGET_CHECKSUM,
    TARGET_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen store 1314 structural-effect target contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 1313 structural-effect source DDL drifted")
if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
    raise RuntimeError("frozen 1314 structural-effect target DDL drifted")

SOURCE_OBJECTS, _source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = migration_core._schema_contract(TARGET_SQL)
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1313 structural-effect source objects drifted")
if _target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1314 structural-effect target objects drifted")
if not STRUCTURAL_EFFECT_OBJECTS.issubset({item["name"] for item in TARGET_OBJECTS}):
    raise RuntimeError("frozen 1314 structural-effect objects are incomplete")

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
    store._PAGE_LIFECYCLE_STRUCTURAL_SCHEMA_SQL,
    "code_assist.notion_workspace_structural_effect_migration.v1",
    (
        "refuse_live_prepared_or_source_applied_lifecycle_commands",
        "refuse_semantically_invalid_page_tree_state",
        "add_generation_scoped_dense_sibling_order",
        "add_immutable_action_specific_structural_manifest_and_effects",
        "add_lifecycle_only_create_and_multi_page_proof_guards",
        "preserve_finalized_rename_binding_projection_tree_and_ledger_history",
        "replace_migration_marker",
    ),
    "1313",
    "1314",
)


_SOURCE_TREE_INVALID_SQL = """
SELECT 1 FROM workspace_page_tree_nodes n
WHERE NOT EXISTS (
  SELECT 1 FROM workspace_page_sources ps
  WHERE ps.workspace_id=n.workspace_id
    AND ps.source_owner_subject_id=n.source_owner_subject_id
    AND ps.source_page_id=n.source_page_id
    AND ps.row_version=n.binding_version
    AND ((n.lifecycle_state='active' AND ps.state='active')
      OR (n.lifecycle_state='archived' AND ps.state='quarantined'
        AND ps.quarantine_reason='source_archived')
      OR (n.lifecycle_state='deleted' AND ps.state='quarantined'
        AND ps.quarantine_reason='source_deleted'))
)
   OR (n.lifecycle_state='active' AND NOT EXISTS (
     SELECT 1 FROM workspace_page_source_incarnation_attestations a
     WHERE a.workspace_id=n.workspace_id
       AND a.source_owner_subject_id=n.source_owner_subject_id
       AND a.source_page_id=n.source_page_id
       AND a.binding_version=n.binding_version
       AND a.source_incarnation_id=n.source_incarnation_id
       AND a.source_incarnation_generation=n.source_incarnation_generation
       AND a.source_change_generation=n.source_change_generation
       AND a.source_receipt_sha256=n.source_receipt_sha256
   ))
   OR (n.parent_resolution='resolved' AND NOT EXISTS (
     SELECT 1 FROM workspace_page_tree_nodes p
     WHERE p.workspace_id=n.workspace_id
       AND p.source_owner_subject_id=n.parent_source_owner_subject_id
       AND p.source_page_id=n.parent_source_page_id
       AND p.lifecycle_state=n.lifecycle_state AND p.depth+1=n.depth
   ))
   OR (n.lifecycle_state='active' AND n.parent_resolution='root'
     AND n.sibling_ordinal>(
       SELECT COUNT(*) FROM workspace_page_tree_nodes s
       WHERE s.workspace_id=n.workspace_id AND s.lifecycle_state='active'
         AND s.parent_resolution='root'))
   OR (n.lifecycle_state='active' AND n.parent_resolution='resolved'
     AND n.sibling_ordinal>(
       SELECT COUNT(*) FROM workspace_page_tree_nodes s
       WHERE s.workspace_id=n.workspace_id AND s.lifecycle_state='active'
         AND s.parent_resolution='resolved'
         AND s.parent_source_owner_subject_id=n.parent_source_owner_subject_id
         AND s.parent_source_page_id=n.parent_source_page_id))
LIMIT 1
"""

_SOURCE_TREE_STATE_INVALID_SQL = """
SELECT 1 FROM workspace_page_tree_states s
WHERE s.active_node_count<>(
    SELECT COUNT(*) FROM workspace_page_tree_nodes n
    WHERE n.workspace_id=s.workspace_id AND n.lifecycle_state='active')
   OR s.archived_node_count<>(
    SELECT COUNT(*) FROM workspace_page_tree_nodes n
    WHERE n.workspace_id=s.workspace_id AND n.lifecycle_state='archived')
   OR s.deleted_node_count<>(
    SELECT COUNT(*) FROM workspace_page_tree_nodes n
    WHERE n.workspace_id=s.workspace_id AND n.lifecycle_state='deleted')
   OR (s.materialization_state='unmaterialized' AND EXISTS (
    SELECT 1 FROM workspace_page_tree_nodes n WHERE n.workspace_id=s.workspace_id))
LIMIT 1
"""


def _assert_frozen_1313_tree_is_migratable(catalog_path: os.PathLike[str] | str) -> None:
    path = Path(catalog_path).resolve(strict=True)
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        if con.execute(
            "SELECT 1 FROM workspace_page_lifecycle_commands "
            "WHERE status IN ('prepared','source_applied') LIMIT 1"
        ).fetchone():
            raise MigrationRefused(
                "1313 catalog contains a live page lifecycle command"
            )
        if (
            con.execute(_SOURCE_TREE_INVALID_SQL).fetchone()
            or con.execute(_SOURCE_TREE_STATE_INVALID_SQL).fetchone()
        ):
            raise MigrationRefused("1313 catalog page tree is not healthy")
    except sqlite3.Error:
        raise MigrationRefused("1313 catalog page tree could not be verified") from None
    finally:
        con.close()


def build_plan(
    catalog_path: os.PathLike[str] | str,
    user_data_root: os.PathLike[str] | str,
) -> dict[str, Any]:
    plan = migration_core.build_plan(catalog_path, user_data_root, _contract=_CONTRACT)
    if plan["state"] == "migration_required":
        _assert_frozen_1313_tree_is_migratable(catalog_path)
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
    # Give callers the same preflight disposition as dry-run.  The migration
    # SQL repeats both checks inside the staged writer transaction.
    preflight = migration_core.build_plan(
        catalog_path, user_data_root, _contract=_CONTRACT
    )
    if preflight["state"] == "migration_required":
        _assert_frozen_1313_tree_is_migratable(catalog_path)
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
                "1313 catalog contains a live page lifecycle command"
            ) from None
        if "invalid_tree_count=0" in message:
            raise MigrationRefused("1313 catalog page tree is not healthy") from None
        raise


__all__ = [
    "MigrationRefused",
    "SOURCE_VERSION",
    "STRUCTURAL_EFFECT_OBJECTS",
    "TARGET_VERSION",
    "apply_plan",
    "build_plan",
]
