"""Offline 2429 -> 2430 storage for database-only personal favorites.

The state stores database IDs without source-page or owner identifiers. Existing
page favorites are unchanged. Runtime exposure remains separately gated.
"""
from __future__ import annotations

import hashlib

from code_assist_v1 import notion_workspace_database_upload_scope_migration as source
from code_assist_v1 import notion_workspace_healing_migration as core


SOURCE_VERSION = source.TARGET_VERSION
SOURCE_NAME = source.TARGET_NAME
SOURCE_CHECKSUM = source.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = source.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082430
TARGET_NAME = "notion_workspace_v2_direct_database_favorites"

DIRECT_DATABASE_FAVORITES_SCHEMA_SQL = """
CREATE TABLE workspace_collaboration_direct_database_favorite_states (
  workspace_id TEXT NOT NULL,
  subject_id TEXT NOT NULL CHECK(length(subject_id)>0),
  favorite_version INTEGER NOT NULL CHECK(
    typeof(favorite_version)='integer' AND favorite_version BETWEEN 0 AND 9007199254740991
  ),
  database_ids_json TEXT NOT NULL CHECK(
    length(database_ids_json)<=262144 AND json_valid(database_ids_json)
    AND json_type(database_ids_json)='array'
    AND json_array_length(database_ids_json)<=100
  ),
  updated_at INTEGER NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=0),
  PRIMARY KEY(workspace_id,subject_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT
) WITHOUT ROWID;
"""

SOURCE_SQL = source.TARGET_SQL
TARGET_SQL = SOURCE_SQL + DIRECT_DATABASE_FAVORITES_SCHEMA_SQL
SOURCE_OBJECTS, _source_actual = core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = core._schema_contract(TARGET_SQL)
TARGET_CHECKSUM = "sha256:829525c25d1b837620ee2bfb2fe6f237cceef6668a0b91b52831d9f908e781c3"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:6ebfcecca0a23c0738300d94c5689ccf700db073acfaeb6614407401bdcf1be1"
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("direct database favorite source schema drifted")
if "TO_BE_FROZEN" not in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256) and (
    "sha256:" + hashlib.sha256(TARGET_SQL.encode()).hexdigest() != TARGET_CHECKSUM
    or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
):
    raise RuntimeError("direct database favorite target schema drifted")

MigrationRefused = core.MigrationRefused


def _contract():
    if "TO_BE_FROZEN" in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256):
        raise RuntimeError("direct database favorite migration fingerprints are not frozen")
    return core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256,
        SOURCE_OBJECTS, TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM,
        TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS,
        DIRECT_DATABASE_FAVORITES_SCHEMA_SQL,
        "code_assist.notion_workspace_direct_database_favorite_migration.v1",
        ("install_direct_database_favorites", "replace_migration_marker"),
        str(SOURCE_VERSION), str(TARGET_VERSION),
    )


def build_plan(catalog_path, user_data_root):
    return core.build_plan(catalog_path, user_data_root, _contract=_contract())


def apply_plan(catalog_path, user_data_root, *, exact_plan_checksum,
               backup_confirmed, catalog_quiesced_confirmed, backup_path,
               fault_hook=None):
    return core.apply_plan(
        catalog_path, user_data_root,
        exact_plan_checksum=exact_plan_checksum,
        backup_confirmed=backup_confirmed,
        catalog_quiesced_confirmed=catalog_quiesced_confirmed,
        backup_path=backup_path, fault_hook=fault_hook, _contract=_contract(),
    )
