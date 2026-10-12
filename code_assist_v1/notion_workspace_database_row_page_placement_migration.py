"""Offline 2423 -> 2424 placement identity for database rows used as pages.

The row and its typed cells remain canonical while its ordinary-page shell is
active. Installing this table does not enable the move feature or migrate any
existing row placement.
"""
from __future__ import annotations

import hashlib

from code_assist_v1 import notion_workspace_database_row_trash_migration as source
from code_assist_v1 import notion_workspace_healing_migration as core

SOURCE_VERSION = source.TARGET_VERSION
SOURCE_NAME = source.TARGET_NAME
SOURCE_CHECKSUM = source.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = source.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082424
TARGET_NAME = "notion_workspace_v2_database_row_page_placement"

DATABASE_ROW_PAGE_PLACEMENT_SCHEMA_SQL = """
CREATE TABLE workspace_collaboration_database_row_page_placements (
  row_id TEXT PRIMARY KEY CHECK(length(row_id)>0),
  page_document_id TEXT NOT NULL UNIQUE CHECK(length(page_document_id)>0),
  placement TEXT NOT NULL CHECK(placement IN ('database','page')),
  parent_document_id TEXT,
  created_by_subject_id TEXT NOT NULL CHECK(length(created_by_subject_id)>0),
  created_operation_id TEXT NOT NULL CHECK(length(created_operation_id) BETWEEN 1 AND 256),
  row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version>=1),
  created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
  updated_at INTEGER NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=0),
  CHECK(row_id=page_document_id),
  CHECK(placement='page' OR parent_document_id IS NULL),
  FOREIGN KEY(row_id) REFERENCES workspace_collaboration_database_rows(id) ON DELETE RESTRICT,
  FOREIGN KEY(page_document_id) REFERENCES workspace_collaboration_documents(id) ON DELETE RESTRICT,
  FOREIGN KEY(parent_document_id) REFERENCES workspace_collaboration_documents(id) ON DELETE RESTRICT
);
CREATE INDEX idx_collaboration_database_row_page_placement
  ON workspace_collaboration_database_row_page_placements(placement,parent_document_id,row_id);
"""

SOURCE_SQL = source.TARGET_SQL
TARGET_SQL = SOURCE_SQL + DATABASE_ROW_PAGE_PLACEMENT_SCHEMA_SQL
SOURCE_OBJECTS, _source_actual = core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = core._schema_contract(TARGET_SQL)
TARGET_CHECKSUM = "sha256:3edb445ea36d9d055f92e3be84295146fa1b244f5450a844ab9ec562c9736494"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:3ce048f9e94d6a6b078f6bdd6a6d157a0b761e370772e0e6bad5f6ad79e1866d"
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("database-row page-placement source schema drifted")
if "TO_BE_FROZEN" not in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256) and (
    "sha256:" + hashlib.sha256(TARGET_SQL.encode()).hexdigest() != TARGET_CHECKSUM
    or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
):
    raise RuntimeError("database-row page-placement target schema drifted")
MigrationRefused = core.MigrationRefused


def _contract():
    if "TO_BE_FROZEN" in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256):
        raise RuntimeError("database-row page-placement fingerprints are not frozen")
    return core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256,
        SOURCE_OBJECTS, TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM,
        TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS,
        DATABASE_ROW_PAGE_PLACEMENT_SCHEMA_SQL,
        "code_assist.notion_workspace_database_row_page_placement_migration.v1",
        ("install_database_row_page_placement", "replace_migration_marker"),
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
