"""Offline 2422 -> 2423 database-row Trash membership; no runtime DDL."""
from __future__ import annotations

import hashlib

from code_assist_v1 import notion_workspace_database_trash_migration as source
from code_assist_v1 import notion_workspace_healing_migration as core

SOURCE_VERSION = source.TARGET_VERSION
SOURCE_NAME = source.TARGET_NAME
SOURCE_CHECKSUM = source.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = source.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082423
TARGET_NAME = "notion_workspace_v2_database_row_trash"

DATABASE_ROW_TRASH_SCHEMA_SQL = """
CREATE TABLE workspace_collaboration_database_row_trash_state (
  row_id TEXT PRIMARY KEY CHECK(length(row_id)>0),
  database_id TEXT NOT NULL CHECK(length(database_id)>0),
  archive_workspace_id TEXT NOT NULL CHECK(length(archive_workspace_id)>0),
  archive_document_id TEXT NOT NULL CHECK(length(archive_document_id)>0),
  archived_row_version INTEGER NOT NULL CHECK(archived_row_version>=1),
  archived_by_subject_id TEXT NOT NULL CHECK(length(archived_by_subject_id)>0),
  archived_at INTEGER NOT NULL CHECK(typeof(archived_at)='integer' AND archived_at>=0),
  archive_operation_id TEXT NOT NULL CHECK(length(archive_operation_id) BETWEEN 1 AND 256),
  FOREIGN KEY(database_id,row_id)
    REFERENCES workspace_collaboration_database_rows(database_id,id) ON DELETE RESTRICT
);
CREATE INDEX idx_collaboration_database_row_trash_database
  ON workspace_collaboration_database_row_trash_state(database_id,archived_at DESC,row_id);
"""

SOURCE_SQL = source.TARGET_SQL
TARGET_SQL = SOURCE_SQL + DATABASE_ROW_TRASH_SCHEMA_SQL
SOURCE_OBJECTS, _source_actual = core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = core._schema_contract(TARGET_SQL)
TARGET_CHECKSUM = "sha256:05a2cd337ab1dd7743eaee79ba6f38979093764a063e89cf0afbdc6fd21f79ac"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:6195211956ae0bb1f46012819207ad1cac61ae1963b977a1717c62d7be89c01a"
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("database-row Trash source schema drifted")
if (
    "sha256:" + hashlib.sha256(TARGET_SQL.encode()).hexdigest() != TARGET_CHECKSUM
    or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
):
    raise RuntimeError("database-row Trash target schema drifted")
MigrationRefused = core.MigrationRefused


def _contract():
    return core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256,
        SOURCE_OBJECTS, TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM,
        TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS,
        DATABASE_ROW_TRASH_SCHEMA_SQL,
        "code_assist.notion_workspace_database_row_trash_migration.v1",
        ("install_database_row_trash", "replace_migration_marker"),
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
