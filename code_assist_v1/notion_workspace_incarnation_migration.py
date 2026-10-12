"""Guarded, forward-only NP-2A 1308 -> 1309 attestation migration.

Only the exact frozen 1308 catalog is accepted.  The migration adds an empty
source-incarnation attestation relation and its fail-closed guards; it never
manufactures evidence for existing bindings.  Apply is explicit, quiesced,
backup-gated, and unavailable from application request or startup paths.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_quarantine_migration as schema_1308
from code_assist_v1 import notion_workspace_store as store


SOURCE_VERSION = 2026081308
SOURCE_NAME = "notion_workspace_v2_np2a_quarantine_reasons"
SOURCE_CHECKSUM = (
    "sha256:30e8b20db620f743f47a2af0f31ab78213ed35e4adbf8d5b9e4f406c6ed6ccd7"
)
SOURCE_ACTUAL_SCHEMA_SHA256 = (
    "sha256:72659383f302480d79c816cafba2fe491bc8c0fed43d3e29f9baccd51208fbd9"
)
TARGET_VERSION = 2026081309
TARGET_NAME = "notion_workspace_v2_np2a_incarnation_attestations"
TARGET_CHECKSUM = (
    "sha256:ecc2a8b40e8d114015e05b1772701dd97bb99ac513db9fd041626408f5f127ec"
)
TARGET_ACTUAL_SCHEMA_SHA256 = (
    "sha256:d79ecc5b79faacf3ca4db288e7978b8192df91b8c3f9e1d5a99f0d859e2e06b1"
)

SOURCE_SQL = schema_1308.TARGET_SQL
TARGET_SQL = store._SCHEMA_1309_SQL
INCARNATION_ATTESTATION_OBJECTS = frozenset({
    "trg_workspace_audit_events_immutable_update",
    "trg_workspace_audit_events_immutable_delete",
    "workspace_page_source_incarnation_attestations",
    "idx_workspace_page_source_incarnation_attestations_incarnation",
    "trg_page_source_incarnation_attestation_requires_current_binding",
    "trg_page_source_incarnation_attestation_service_context",
    "trg_page_source_incarnation_attestation_duplicate_insert",
    "trg_page_source_incarnation_attestation_nonce_scope",
    "trg_page_source_incarnation_attestation_lineage",
    "trg_page_source_incarnation_attestation_immutable_update",
    "trg_page_source_incarnation_attestation_immutable_delete",
    "trg_page_source_incarnation_attestation_parent_delete",
})

if (
    schema_1308.TARGET_VERSION,
    schema_1308.TARGET_NAME,
    schema_1308.TARGET_CHECKSUM,
    schema_1308.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 1308 incarnation migration source contract drifted")
if (
    store._SCHEMA_1309_VERSION,
    store._SCHEMA_1309_NAME,
    store._SCHEMA_1309_CHECKSUM,
    store._SCHEMA_1309_ACTUAL_SCHEMA_SHA256,
) != (
    TARGET_VERSION,
    TARGET_NAME,
    TARGET_CHECKSUM,
    TARGET_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 1309 incarnation target contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 1308 incarnation source DDL drifted")
if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
    raise RuntimeError("frozen 1309 incarnation target DDL drifted")

SOURCE_OBJECTS, _computed_source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _computed_target_actual = migration_core._schema_contract(TARGET_SQL)
if _computed_source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1308 incarnation source object contract drifted")
if _computed_target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1309 incarnation target object contract drifted")

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
    store._INCARNATION_ATTESTATION_SCHEMA_SQL,
    "code_assist.notion_workspace_incarnation_migration.v1",
    (
        "install_incarnation_attestation_relation",
        "install_incarnation_attestation_guards",
        "preserve_existing_bindings_unattested",
        "replace_migration_marker",
    ),
    "1308",
    "1309",
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
    "INCARNATION_ATTESTATION_OBJECTS",
    "MigrationRefused",
    "SOURCE_VERSION",
    "TARGET_VERSION",
    "apply_plan",
    "build_plan",
]
