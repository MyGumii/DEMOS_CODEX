"""Offline 2427 -> 2428 independent database-sharing storage.

An empty grant table preserves every existing database's inherited access.
Only the deployment migrator may install this schema; runtime authorization
remains unchanged until all database-scoped read and write gates are wired.
"""
from __future__ import annotations

import hashlib

from code_assist_v1 import notion_workspace_database_template_default_migration as source
from code_assist_v1 import notion_workspace_healing_migration as core


SOURCE_VERSION = source.TARGET_VERSION
SOURCE_NAME = source.TARGET_NAME
SOURCE_CHECKSUM = source.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = source.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082428
TARGET_NAME = "notion_workspace_v2_database_sharing"

DATABASE_SHARING_SCHEMA_SQL = """
CREATE TABLE workspace_collaboration_database_access_state (
  database_id TEXT PRIMARY KEY CHECK(length(database_id)>0),
  access_epoch INTEGER NOT NULL DEFAULT 1 CHECK(access_epoch>=1),
  updated_at INTEGER NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=0),
  FOREIGN KEY(database_id) REFERENCES workspace_collaboration_databases(id) ON DELETE RESTRICT
);
CREATE TABLE workspace_collaboration_database_access_grants (
  database_id TEXT NOT NULL CHECK(length(database_id)>0),
  principal_type TEXT NOT NULL CHECK(principal_type IN ('subject','group','teamspace')),
  principal_id TEXT NOT NULL CHECK(length(principal_id)>0),
  role TEXT NOT NULL CHECK(role IN ('view','comment','edit_content','edit','full_access')),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  grant_epoch INTEGER NOT NULL DEFAULT 1 CHECK(grant_epoch>=1),
  granted_by_subject_id TEXT NOT NULL CHECK(length(granted_by_subject_id)>0),
  created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
  updated_at INTEGER NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=0),
  revoked_at INTEGER CHECK(revoked_at IS NULL OR (typeof(revoked_at)='integer' AND revoked_at>=0)),
  CHECK((active=1 AND revoked_at IS NULL) OR (active=0 AND revoked_at IS NOT NULL)),
  PRIMARY KEY(database_id,principal_type,principal_id),
  FOREIGN KEY(database_id) REFERENCES workspace_collaboration_databases(id) ON DELETE RESTRICT
);
CREATE INDEX workspace_collaboration_database_access_grants_principal
  ON workspace_collaboration_database_access_grants(principal_type,principal_id,active);
"""

SOURCE_SQL = source.TARGET_SQL
TARGET_SQL = SOURCE_SQL + DATABASE_SHARING_SCHEMA_SQL
SOURCE_OBJECTS, _source_actual = core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = core._schema_contract(TARGET_SQL)
TARGET_CHECKSUM = "sha256:66ffd986c9649f242081506c5f8aa2963ed8dd6cb4ffcc98ec6097a5a64f7d5e"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:8d658a20c17b5b7219d509e7bdefd506dd06b05776f3ff7f4ea05b4e85186f51"
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("database sharing source schema drifted")
if "TO_BE_FROZEN" not in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256) and (
    "sha256:" + hashlib.sha256(TARGET_SQL.encode()).hexdigest() != TARGET_CHECKSUM
    or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
):
    raise RuntimeError("database sharing target schema drifted")

MigrationRefused = core.MigrationRefused


def _contract():
    if "TO_BE_FROZEN" in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256):
        raise RuntimeError("database sharing migration fingerprints are not frozen")
    return core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256,
        SOURCE_OBJECTS, TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM,
        TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS,
        DATABASE_SHARING_SCHEMA_SQL,
        "code_assist.notion_workspace_database_sharing_migration.v1",
        ("install_database_sharing", "replace_migration_marker"),
        str(SOURCE_VERSION), str(TARGET_VERSION),
    )


def build_plan(catalog_path, user_data_root):
    return core.build_plan(catalog_path, user_data_root, _contract=_contract())


def apply_plan(catalog_path, user_data_root, *, exact_plan_checksum,
               backup_confirmed, catalog_quiesced_confirmed, backup_path, fault_hook=None):
    return core.apply_plan(
        catalog_path, user_data_root,
        exact_plan_checksum=exact_plan_checksum,
        backup_confirmed=backup_confirmed,
        catalog_quiesced_confirmed=catalog_quiesced_confirmed,
        backup_path=backup_path, fault_hook=fault_hook, _contract=_contract(),
    )
