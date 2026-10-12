"""Offline 2419 -> 2420 provenance for independent same-document database files."""
from __future__ import annotations

import hashlib

from code_assist_v1 import notion_workspace_row_appearance_migration as source
from code_assist_v1 import notion_workspace_healing_migration as core

SOURCE_VERSION = source.TARGET_VERSION
SOURCE_NAME = source.TARGET_NAME
SOURCE_CHECKSUM = source.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = source.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082420
TARGET_NAME = "notion_workspace_v2_same_document_file_copy"

SAME_DOCUMENT_FILE_COPY_SCHEMA_SQL = """
PRAGMA defer_foreign_keys=ON;
CREATE TABLE workspace_collaboration_files_replacement (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  upload_session_id TEXT UNIQUE,
  uploader_subject_id TEXT NOT NULL,
  original_name TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
  content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=71 AND substr(content_sha256,1,7)='sha256:'),
  blob_relative_path TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('active','deleted')),
  row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  deleted_at INTEGER,
  deleted_by_subject_id TEXT,
  origin_kind TEXT NOT NULL DEFAULT 'upload' CHECK(origin_kind IN ('upload','copy')),
  source_file_id TEXT,
  source_document_id TEXT,
  copied_by_subject_id TEXT,
  copy_operation_id TEXT,
  UNIQUE(id,workspace_id,document_id),
  UNIQUE(copied_by_subject_id,copy_operation_id,source_file_id),
  CHECK((state='active' AND deleted_at IS NULL AND deleted_by_subject_id IS NULL) OR
        (state='deleted' AND deleted_at IS NOT NULL AND deleted_by_subject_id IS NOT NULL)),
  CHECK((origin_kind='upload' AND upload_session_id IS NOT NULL AND source_file_id IS NULL AND
         source_document_id IS NULL AND copied_by_subject_id IS NULL AND copy_operation_id IS NULL) OR
        (origin_kind='copy' AND upload_session_id IS NULL AND source_file_id IS NOT NULL AND
         source_file_id<>id AND source_document_id IS NOT NULL AND
         copied_by_subject_id IS NOT NULL AND length(copied_by_subject_id)>0 AND
         copy_operation_id IS NOT NULL AND length(copy_operation_id) BETWEEN 1 AND 256)),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id) ON DELETE RESTRICT,
  FOREIGN KEY(upload_session_id) REFERENCES workspace_collaboration_upload_sessions(id) ON DELETE RESTRICT,
  FOREIGN KEY(source_file_id,workspace_id,source_document_id)
    REFERENCES workspace_collaboration_files_replacement(id,workspace_id,document_id) ON DELETE RESTRICT
);
INSERT INTO workspace_collaboration_files_replacement (
  id,workspace_id,document_id,upload_session_id,uploader_subject_id,original_name,mime_type,
  size_bytes,content_sha256,blob_relative_path,state,row_version,created_at,updated_at,
  deleted_at,deleted_by_subject_id,origin_kind,source_file_id,source_document_id,
  copied_by_subject_id,copy_operation_id)
SELECT id,workspace_id,document_id,upload_session_id,uploader_subject_id,original_name,mime_type,
  size_bytes,content_sha256,blob_relative_path,state,row_version,created_at,updated_at,
  deleted_at,deleted_by_subject_id,origin_kind,source_file_id,source_document_id,
  copied_by_subject_id,copy_operation_id
FROM workspace_collaboration_files;
DROP TRIGGER trg_collab_files_update_guard;
DROP TRIGGER trg_collab_files_no_delete;
DROP TABLE workspace_collaboration_files;
ALTER TABLE workspace_collaboration_files_replacement RENAME TO workspace_collaboration_files;
CREATE UNIQUE INDEX idx_collab_files_copy_migration_identity
  ON workspace_collaboration_files(id,workspace_id,document_id);
CREATE INDEX idx_collab_files_document ON workspace_collaboration_files(document_id,state,created_at,id);
CREATE INDEX idx_collab_files_blob ON workspace_collaboration_files(content_sha256,state,id);
CREATE TRIGGER trg_collab_files_update_guard
BEFORE UPDATE ON workspace_collaboration_files
WHEN NEW.id IS NOT OLD.id OR NEW.workspace_id IS NOT OLD.workspace_id OR
     NEW.document_id IS NOT OLD.document_id OR NEW.upload_session_id IS NOT OLD.upload_session_id OR
     NEW.uploader_subject_id IS NOT OLD.uploader_subject_id OR NEW.original_name IS NOT OLD.original_name OR
     NEW.mime_type IS NOT OLD.mime_type OR NEW.size_bytes IS NOT OLD.size_bytes OR
     NEW.content_sha256 IS NOT OLD.content_sha256 OR NEW.blob_relative_path IS NOT OLD.blob_relative_path OR
     NEW.origin_kind IS NOT OLD.origin_kind OR NEW.source_file_id IS NOT OLD.source_file_id OR
     NEW.source_document_id IS NOT OLD.source_document_id OR NEW.copied_by_subject_id IS NOT OLD.copied_by_subject_id OR
     NEW.copy_operation_id IS NOT OLD.copy_operation_id OR NEW.created_at IS NOT OLD.created_at OR
     NEW.row_version<>OLD.row_version+1 OR NEW.updated_at<OLD.updated_at OR NOT (
       (OLD.state='active' AND NEW.state='deleted' AND
        NEW.deleted_at IS NOT NULL AND NEW.deleted_by_subject_id IS NOT NULL) OR
       (OLD.state='deleted' AND NEW.state='active' AND
        NEW.deleted_at IS NULL AND NEW.deleted_by_subject_id IS NULL)
     )
BEGIN SELECT RAISE(ABORT, 'file transition is invalid'); END;
CREATE TRIGGER trg_collab_files_no_delete
BEFORE DELETE ON workspace_collaboration_files
BEGIN SELECT RAISE(ABORT, 'file metadata cannot be deleted'); END;
"""

SOURCE_SQL = source.TARGET_SQL
TARGET_SQL = SOURCE_SQL + SAME_DOCUMENT_FILE_COPY_SCHEMA_SQL
SOURCE_OBJECTS, _source_actual = core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = core._schema_contract(TARGET_SQL)
TARGET_CHECKSUM = "sha256:8053bb8dea912322ac0cb8215d76090b90f607130133a9a6e0fbdee7a0e19a6d"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:167f19a89cb13e3a70ce6bd15eea4a1b75b2818704694739ae698aaf74913bf1"
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("same-document file copy source schema drifted")
if "TO_BE_FROZEN" not in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256) and (
    "sha256:" + hashlib.sha256(TARGET_SQL.encode()).hexdigest() != TARGET_CHECKSUM
    or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
):
    raise RuntimeError("same-document file copy target schema drifted")
MigrationRefused = core.MigrationRefused


def _contract():
    if "TO_BE_FROZEN" in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256):
        raise RuntimeError("same-document file copy fingerprints are not frozen")
    return core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256,
        SOURCE_OBJECTS, TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM,
        TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS,
        SAME_DOCUMENT_FILE_COPY_SCHEMA_SQL,
        "code_assist.notion_workspace_same_document_file_copy_migration.v1",
        ("rebuild_same_document_file_provenance", "replace_migration_marker"),
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
