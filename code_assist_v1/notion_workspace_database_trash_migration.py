"""Offline 2421 -> 2422 owned-database Trash membership; no runtime DDL."""
from __future__ import annotations

import hashlib

from code_assist_v1 import notion_workspace_database_activity_migration as source
from code_assist_v1 import notion_workspace_healing_migration as core

SOURCE_VERSION = source.TARGET_VERSION
SOURCE_NAME = source.TARGET_NAME
SOURCE_CHECKSUM = source.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = source.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082422
TARGET_NAME = "notion_workspace_v2_database_trash"

DATABASE_TRASH_SCHEMA_SQL = """
CREATE TABLE workspace_collaboration_database_trash_state (
  database_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  block_id TEXT NOT NULL UNIQUE CHECK(length(block_id)>0),
  block_snapshot_json TEXT NOT NULL CHECK(
    CASE WHEN json_valid(block_snapshot_json) THEN
      json_type(block_snapshot_json)='object'
      AND COALESCE(json_extract(block_snapshot_json,'$.id')=block_id,0)
      AND json_type(block_snapshot_json,'$.props')='object'
      AND json_type(block_snapshot_json,'$.content')='text'
    ELSE 0 END),
  source_position INTEGER NOT NULL CHECK(source_position>=0),
  block_row_version INTEGER NOT NULL CHECK(block_row_version>=1),
  block_created_at INTEGER NOT NULL CHECK(block_created_at>=0),
  restore_anchor_json TEXT CHECK(restore_anchor_json IS NULL OR (
    json_valid(restore_anchor_json) AND json_type(restore_anchor_json)='object')),
  database_row_version INTEGER NOT NULL CHECK(database_row_version>=1),
  archived_by_subject_id TEXT NOT NULL CHECK(length(archived_by_subject_id)>0),
  archived_at INTEGER NOT NULL CHECK(typeof(archived_at)='integer' AND archived_at>=0),
  archive_operation_id TEXT NOT NULL CHECK(length(archive_operation_id) BETWEEN 1 AND 256),
  FOREIGN KEY(database_id,document_id)
    REFERENCES workspace_collaboration_databases(id,document_id) ON DELETE RESTRICT,
  FOREIGN KEY(workspace_id,document_id)
    REFERENCES workspace_collaboration_documents(workspace_id,id) ON DELETE RESTRICT
);
CREATE INDEX idx_collaboration_database_trash_workspace
  ON workspace_collaboration_database_trash_state(workspace_id,archived_at DESC,database_id);
CREATE TABLE workspace_collaboration_database_trash_block_comments (
  database_id TEXT NOT NULL,
  comment_id TEXT NOT NULL UNIQUE,
  PRIMARY KEY(database_id,comment_id),
  FOREIGN KEY(database_id) REFERENCES workspace_collaboration_database_trash_state(database_id)
    ON DELETE CASCADE,
  FOREIGN KEY(comment_id) REFERENCES workspace_collaboration_comments(id)
    ON DELETE CASCADE
) WITHOUT ROWID;
"""

SOURCE_SQL = source.TARGET_SQL
TARGET_SQL = SOURCE_SQL + DATABASE_TRASH_SCHEMA_SQL
SOURCE_OBJECTS, _source_actual = core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = core._schema_contract(TARGET_SQL)
TARGET_CHECKSUM = "sha256:0416a71234559272fd573e80a60b1392a502ac317be7f07b8a3a7eb54514ae61"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:984f712605c63bd3ea7d5b7d6e9ad1e6f27bbc12dc4cc1fadaa525ef99937e23"
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("database Trash source schema drifted")
if "TO_BE_FROZEN" not in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256) and (
    "sha256:" + hashlib.sha256(TARGET_SQL.encode()).hexdigest() != TARGET_CHECKSUM
    or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
):
    raise RuntimeError("database Trash target schema drifted")
MigrationRefused = core.MigrationRefused


def _contract():
    if "TO_BE_FROZEN" in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256):
        raise RuntimeError("database Trash fingerprints are not frozen")
    return core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256,
        SOURCE_OBJECTS, TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM,
        TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS,
        DATABASE_TRASH_SCHEMA_SQL,
        "code_assist.notion_workspace_database_trash_migration.v1",
        ("install_database_trash", "replace_migration_marker"),
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
