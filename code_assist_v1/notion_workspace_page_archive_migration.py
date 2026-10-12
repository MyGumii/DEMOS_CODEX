"""Explicit 2412 -> 2413 canonical page archive membership; no startup writes."""
from __future__ import annotations

import hashlib

from code_assist_v1 import notion_workspace_guest_navigation_migration as source
from code_assist_v1 import notion_workspace_healing_migration as core

SOURCE_VERSION = source.TARGET_VERSION
SOURCE_NAME = source.TARGET_NAME
SOURCE_CHECKSUM = source.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = source.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082413
TARGET_NAME = "notion_workspace_v2_page_archive"
PAGE_ARCHIVE_SCHEMA_SQL = """
CREATE TABLE workspace_collaboration_page_archive_state (
  document_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  batch_id TEXT NOT NULL CHECK(length(batch_id) BETWEEN 1 AND 256),
  root_document_id TEXT NOT NULL,
  archived_by_subject_id TEXT NOT NULL,
  archived_at INTEGER NOT NULL CHECK(typeof(archived_at)='integer' AND archived_at>=0),
  link_anchor_json TEXT CHECK(link_anchor_json IS NULL OR (length(link_anchor_json)<=1024 AND json_valid(link_anchor_json) AND json_type(link_anchor_json)='object')),
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id) ON DELETE RESTRICT,
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(root_document_id) REFERENCES workspace_collaboration_documents(id) ON DELETE RESTRICT
);
CREATE INDEX idx_collaboration_page_archive_batch
  ON workspace_collaboration_page_archive_state(workspace_id,batch_id);
INSERT INTO workspace_collaboration_page_archive_state
  (document_id,workspace_id,batch_id,root_document_id,archived_by_subject_id,archived_at)
SELECT id,workspace_id,'imported:'||id,id,legacy_owner_subject_id,COALESCE(archived_at,updated_at)
FROM workspace_collaboration_documents WHERE archived=1;
"""
SOURCE_SQL = source.TARGET_SQL
TARGET_SQL = SOURCE_SQL + PAGE_ARCHIVE_SCHEMA_SQL
SOURCE_OBJECTS, _source_actual = core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = core._schema_contract(TARGET_SQL)
TARGET_CHECKSUM = "sha256:21457646577c8dcad3f23e2c834b173efae0d9d900447450464db356e344447f"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:af6a280fab62623cb8762a189ea739c77d1d5e333e233d50be9be2cbbc39d6d7"
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("page archive source schema drifted")
if "TO_BE_FROZEN" not in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256) and (
    "sha256:" + hashlib.sha256(TARGET_SQL.encode()).hexdigest() != TARGET_CHECKSUM
    or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
):
    raise RuntimeError("page archive target schema drifted")
MigrationRefused = core.MigrationRefused


def _contract():
    if "TO_BE_FROZEN" in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256):
        raise RuntimeError("page archive fingerprints are not frozen")
    return core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256, SOURCE_OBJECTS,
        TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS,
        PAGE_ARCHIVE_SCHEMA_SQL, "code_assist.notion_workspace_page_archive_migration.v1",
        ("install_page_archive", "replace_migration_marker"), str(SOURCE_VERSION), str(TARGET_VERSION),
    )


def build_plan(catalog_path, user_data_root):
    return core.build_plan(catalog_path, user_data_root, _contract=_contract())


def apply_plan(catalog_path, user_data_root, *, exact_plan_checksum, backup_confirmed,
               catalog_quiesced_confirmed, backup_path, fault_hook=None):
    return core.apply_plan(catalog_path, user_data_root, exact_plan_checksum=exact_plan_checksum,
        backup_confirmed=backup_confirmed, catalog_quiesced_confirmed=catalog_quiesced_confirmed,
        backup_path=backup_path, fault_hook=fault_hook, _contract=_contract())
