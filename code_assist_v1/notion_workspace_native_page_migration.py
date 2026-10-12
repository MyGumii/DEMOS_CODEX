"""Offline 2413 -> 2414 registry for pages created directly in canonical storage."""
from __future__ import annotations

import hashlib

from code_assist_v1 import notion_workspace_page_archive_migration as source
from code_assist_v1 import notion_workspace_healing_migration as core

SOURCE_VERSION = source.TARGET_VERSION
SOURCE_NAME = source.TARGET_NAME
SOURCE_CHECKSUM = source.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = source.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082414
TARGET_NAME = "notion_workspace_v2_native_pages"
NATIVE_PAGE_SCHEMA_SQL = """
CREATE TABLE workspace_collaboration_native_pages (
  document_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  creation_kind TEXT NOT NULL CHECK(creation_kind IN ('create','copy')),
  source_document_id TEXT,
  created_by_subject_id TEXT NOT NULL,
  operation_id TEXT NOT NULL CHECK(length(operation_id) BETWEEN 1 AND 256),
  created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
  CHECK((creation_kind='create' AND source_document_id IS NULL) OR
        (creation_kind='copy' AND source_document_id IS NOT NULL AND length(source_document_id)>0)),
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id) ON DELETE RESTRICT,
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT
);
CREATE INDEX idx_collaboration_native_pages_workspace
  ON workspace_collaboration_native_pages(workspace_id,document_id);
"""
SOURCE_SQL = source.TARGET_SQL
TARGET_SQL = SOURCE_SQL + NATIVE_PAGE_SCHEMA_SQL
SOURCE_OBJECTS, _source_actual = core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = core._schema_contract(TARGET_SQL)
TARGET_CHECKSUM = "sha256:43a409b75d807677e5c5b9cc7a9e137d75d812db6000d31f075c3b85c493cb50"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:1d56270a1ecf3c4a994a90418455ca3c31b04320ea663634264317174621399b"
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("native page source schema drifted")
if "TO_BE_FROZEN" not in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256) and (
    "sha256:" + hashlib.sha256(TARGET_SQL.encode()).hexdigest() != TARGET_CHECKSUM
    or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
):
    raise RuntimeError("native page target schema drifted")
MigrationRefused = core.MigrationRefused


def _contract():
    if "TO_BE_FROZEN" in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256):
        raise RuntimeError("native page fingerprints are not frozen")
    return core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256, SOURCE_OBJECTS,
        TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS,
        NATIVE_PAGE_SCHEMA_SQL, "code_assist.notion_workspace_native_page_migration.v1",
        ("install_native_pages", "replace_migration_marker"), str(SOURCE_VERSION), str(TARGET_VERSION),
    )


def build_plan(catalog_path, user_data_root):
    return core.build_plan(catalog_path, user_data_root, _contract=_contract())


def apply_plan(catalog_path, user_data_root, *, exact_plan_checksum, backup_confirmed,
               catalog_quiesced_confirmed, backup_path, fault_hook=None):
    return core.apply_plan(catalog_path, user_data_root, exact_plan_checksum=exact_plan_checksum,
        backup_confirmed=backup_confirmed, catalog_quiesced_confirmed=catalog_quiesced_confirmed,
        backup_path=backup_path, fault_hook=fault_hook, _contract=_contract())
