"""Guarded 2026082407 -> 2026082408 collaborative file authority migration."""

from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from code_assist_v1 import notion_workspace_database_features_migration as schema_2407
from code_assist_v1 import notion_workspace_healing_migration as migration_core


SOURCE_VERSION = 2026082407
SOURCE_NAME = schema_2407.TARGET_NAME
SOURCE_CHECKSUM = schema_2407.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = schema_2407.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082408
TARGET_NAME = "notion_workspace_v2_collaborative_file_authority"


FILE_AUTHORITY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workspace_collaboration_upload_sessions (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  actor_subject_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN (
    'prepared','ready','committing','committed','canceled','cleanup_required','cleaned'
  )),
  original_name TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  total_size INTEGER NOT NULL CHECK(total_size >= 0),
  chunk_size INTEGER NOT NULL CHECK(chunk_size BETWEEN 65536 AND 8388608),
  chunk_count INTEGER NOT NULL CHECK(chunk_count >= 1),
  expected_sha256 TEXT NOT NULL CHECK(
    length(expected_sha256)=71 AND substr(expected_sha256,1,7)='sha256:'
  ),
  reserved_bytes INTEGER NOT NULL CHECK(reserved_bytes=total_size),
  workspace_epoch INTEGER NOT NULL CHECK(workspace_epoch >= 1),
  document_epoch INTEGER NOT NULL CHECK(document_epoch >= 1),
  source_document_sequence INTEGER NOT NULL CHECK(source_document_sequence >= 1),
  begin_operation_id TEXT NOT NULL,
  finalize_operation_id TEXT,
  cancel_operation_id TEXT,
  file_id TEXT,
  row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  completed_at INTEGER,
  UNIQUE(actor_subject_id,begin_operation_id),
  UNIQUE(actor_subject_id,finalize_operation_id),
  UNIQUE(actor_subject_id,cancel_operation_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id) ON DELETE RESTRICT,
  CHECK(
    (state IN ('prepared','ready') AND file_id IS NULL AND completed_at IS NULL) OR
    (state='committing' AND finalize_operation_id IS NOT NULL AND file_id IS NOT NULL AND completed_at IS NULL) OR
    (state='committed' AND finalize_operation_id IS NOT NULL AND file_id IS NOT NULL AND completed_at IS NOT NULL) OR
    (state IN ('canceled','cleanup_required','cleaned') AND cancel_operation_id IS NOT NULL AND completed_at IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_collab_upload_sessions_resume
  ON workspace_collaboration_upload_sessions(actor_subject_id,document_id,state,updated_at,id);
CREATE INDEX IF NOT EXISTS idx_collab_upload_sessions_cleanup
  ON workspace_collaboration_upload_sessions(state,expires_at,updated_at,id)
  WHERE state IN ('prepared','ready','canceled','cleanup_required');

CREATE TABLE IF NOT EXISTS workspace_collaboration_upload_chunks (
  session_id TEXT NOT NULL,
  chunk_index INTEGER NOT NULL CHECK(chunk_index >= 0),
  size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
  content_sha256 TEXT NOT NULL CHECK(
    length(content_sha256)=71 AND substr(content_sha256,1,7)='sha256:'
  ),
  relative_path TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(session_id,chunk_index),
  UNIQUE(session_id,operation_id),
  FOREIGN KEY(session_id) REFERENCES workspace_collaboration_upload_sessions(id) ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS workspace_collaboration_files (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  upload_session_id TEXT NOT NULL UNIQUE,
  uploader_subject_id TEXT NOT NULL,
  original_name TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
  content_sha256 TEXT NOT NULL CHECK(
    length(content_sha256)=71 AND substr(content_sha256,1,7)='sha256:'
  ),
  blob_relative_path TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('active','deleted')),
  row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  deleted_at INTEGER,
  deleted_by_subject_id TEXT,
  CHECK(
    (state='active' AND deleted_at IS NULL AND deleted_by_subject_id IS NULL) OR
    (state='deleted' AND deleted_at IS NOT NULL AND deleted_by_subject_id IS NOT NULL)
  ),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id) ON DELETE RESTRICT,
  FOREIGN KEY(upload_session_id) REFERENCES workspace_collaboration_upload_sessions(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_collab_files_document
  ON workspace_collaboration_files(document_id,state,created_at,id);
CREATE INDEX IF NOT EXISTS idx_collab_files_blob
  ON workspace_collaboration_files(content_sha256,state,id);

CREATE TRIGGER IF NOT EXISTS trg_collab_upload_session_identity_immutable
BEFORE UPDATE ON workspace_collaboration_upload_sessions
WHEN NEW.id<>OLD.id OR NEW.workspace_id<>OLD.workspace_id OR
     NEW.document_id<>OLD.document_id OR NEW.actor_subject_id<>OLD.actor_subject_id OR
     NEW.original_name<>OLD.original_name OR NEW.mime_type<>OLD.mime_type OR
     NEW.total_size<>OLD.total_size OR NEW.chunk_size<>OLD.chunk_size OR
     NEW.chunk_count<>OLD.chunk_count OR NEW.expected_sha256<>OLD.expected_sha256 OR
     NEW.reserved_bytes<>OLD.reserved_bytes OR NEW.workspace_epoch<>OLD.workspace_epoch OR
     NEW.document_epoch<>OLD.document_epoch OR
     NEW.source_document_sequence<>OLD.source_document_sequence OR
     NEW.begin_operation_id<>OLD.begin_operation_id OR NEW.created_at<>OLD.created_at OR
     NEW.row_version<>OLD.row_version+1 OR NEW.updated_at<OLD.updated_at
BEGIN
  SELECT RAISE(ABORT, 'upload session transition is invalid');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_upload_session_state_guard
BEFORE UPDATE ON workspace_collaboration_upload_sessions
WHEN NOT (
  (OLD.state='prepared' AND NEW.state IN ('prepared','ready','canceled','cleanup_required')) OR
  (OLD.state='ready' AND NEW.state IN ('ready','committing','canceled','cleanup_required')) OR
  (OLD.state='committing' AND NEW.state IN ('committed','cleanup_required')) OR
  (OLD.state IN ('canceled','cleanup_required') AND NEW.state IN ('cleanup_required','cleaned'))
)
BEGIN
  SELECT RAISE(ABORT, 'upload session state transition is invalid');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_upload_sessions_no_delete
BEFORE DELETE ON workspace_collaboration_upload_sessions
BEGIN SELECT RAISE(ABORT, 'upload session cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS trg_collab_upload_chunks_immutable_update
BEFORE UPDATE ON workspace_collaboration_upload_chunks
BEGIN SELECT RAISE(ABORT, 'upload chunk receipt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_collab_upload_chunks_no_delete
BEFORE DELETE ON workspace_collaboration_upload_chunks
BEGIN SELECT RAISE(ABORT, 'upload chunk receipt cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS trg_collab_files_update_guard
BEFORE UPDATE ON workspace_collaboration_files
WHEN NEW.id<>OLD.id OR NEW.workspace_id<>OLD.workspace_id OR
     NEW.document_id<>OLD.document_id OR NEW.upload_session_id<>OLD.upload_session_id OR
     NEW.uploader_subject_id<>OLD.uploader_subject_id OR NEW.original_name<>OLD.original_name OR
     NEW.mime_type<>OLD.mime_type OR NEW.size_bytes<>OLD.size_bytes OR
     NEW.content_sha256<>OLD.content_sha256 OR NEW.blob_relative_path<>OLD.blob_relative_path OR
     NEW.created_at<>OLD.created_at OR OLD.state='deleted' OR NEW.state<>'deleted' OR
     NEW.row_version<>OLD.row_version+1 OR NEW.updated_at<OLD.updated_at
BEGIN
  SELECT RAISE(ABORT, 'file transition is invalid');
END;
CREATE TRIGGER IF NOT EXISTS trg_collab_files_no_delete
BEFORE DELETE ON workspace_collaboration_files
BEGIN SELECT RAISE(ABORT, 'file metadata cannot be deleted'); END;
"""

SOURCE_SQL = schema_2407.TARGET_SQL
TARGET_SQL = SOURCE_SQL + FILE_AUTHORITY_SCHEMA_SQL
TARGET_CHECKSUM = "sha256:96f990b4bc2b8551f1a10080825c7a7df658539fec24fcd8fac00fc211045f13"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:ffe6d2e275b9d058231eb56a75ab8500babbd915b441d2753d19d480e21f6ba8"

if (schema_2407.TARGET_VERSION, schema_2407.TARGET_NAME, schema_2407.TARGET_CHECKSUM, schema_2407.TARGET_ACTUAL_SCHEMA_SHA256) != (SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256):
    raise RuntimeError("frozen 2407 file-authority source contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 2407 file-authority source DDL drifted")
SOURCE_OBJECTS, _source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = migration_core._schema_contract(TARGET_SQL)
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 2407 file-authority source objects drifted")
if "TO_BE_FROZEN" not in TARGET_CHECKSUM:
    if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("frozen 2408 file-authority target drifted")

MigrationRefused = migration_core.MigrationRefused


def _contract() -> migration_core._MigrationContract:
    if "TO_BE_FROZEN" in TARGET_CHECKSUM or "TO_BE_FROZEN" in TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("file-authority migration fingerprints are not frozen")
    return migration_core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256,
        SOURCE_OBJECTS, TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM,
        TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS, FILE_AUTHORITY_SCHEMA_SQL,
        "code_assist.notion_workspace_file_authority_migration.v1",
        ("install_resumable_upload_sessions", "install_immutable_chunk_receipts", "install_authorized_file_metadata", "replace_migration_marker"),
        "2026082407", "2026082408",
    )


def build_plan(catalog_path: os.PathLike[str] | str, user_data_root: os.PathLike[str] | str) -> dict[str, Any]:
    return migration_core.build_plan(catalog_path, user_data_root, _contract=_contract())


def apply_plan(
    catalog_path: os.PathLike[str] | str,
    user_data_root: os.PathLike[str] | str,
    *, exact_plan_checksum: str, backup_confirmed: bool,
    catalog_quiesced_confirmed: bool, backup_path: os.PathLike[str] | str,
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    return migration_core.apply_plan(
        catalog_path, user_data_root, exact_plan_checksum=exact_plan_checksum,
        backup_confirmed=backup_confirmed,
        catalog_quiesced_confirmed=catalog_quiesced_confirmed,
        backup_path=backup_path, fault_hook=fault_hook, _contract=_contract(),
    )


__all__ = ["FILE_AUTHORITY_SCHEMA_SQL", "MigrationRefused", "SOURCE_ACTUAL_SCHEMA_SHA256", "SOURCE_CHECKSUM", "SOURCE_NAME", "SOURCE_VERSION", "TARGET_ACTUAL_SCHEMA_SHA256", "TARGET_CHECKSUM", "TARGET_NAME", "TARGET_VERSION", "apply_plan", "build_plan"]
