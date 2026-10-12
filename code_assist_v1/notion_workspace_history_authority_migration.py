"""Guarded 2026082408 -> 2026082409 collaborative history authority migration."""

from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from code_assist_v1 import notion_workspace_file_authority_migration as schema_2408
from code_assist_v1 import notion_workspace_healing_migration as migration_core


SOURCE_VERSION = 2026082408
SOURCE_NAME = schema_2408.TARGET_NAME
SOURCE_CHECKSUM = schema_2408.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = schema_2408.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082409
TARGET_NAME = "notion_workspace_v2_collaborative_history_authority"


HISTORY_AUTHORITY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workspace_collaboration_history_restores (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  source_checkpoint_id TEXT,
  source_revision INTEGER NOT NULL CHECK(source_revision >= 1),
  result_checkpoint_id TEXT NOT NULL UNIQUE,
  result_revision INTEGER NOT NULL CHECK(result_revision > source_revision),
  actor_subject_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  source_content_sha256 TEXT NOT NULL CHECK(
    length(source_content_sha256)=71 AND substr(source_content_sha256,1,7)='sha256:'
  ),
  result_content_sha256 TEXT NOT NULL CHECK(
    length(result_content_sha256)=71 AND substr(result_content_sha256,1,7)='sha256:'
  ),
  created_at INTEGER NOT NULL,
  UNIQUE(actor_subject_id,document_id,operation_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(document_id,source_revision)
    REFERENCES workspace_collaboration_revisions(document_id,revision) ON DELETE RESTRICT,
  FOREIGN KEY(document_id,result_revision)
    REFERENCES workspace_collaboration_revisions(document_id,revision) ON DELETE RESTRICT,
  FOREIGN KEY(source_checkpoint_id)
    REFERENCES workspace_collaboration_revision_checkpoints(id) ON DELETE RESTRICT,
  FOREIGN KEY(result_checkpoint_id)
    REFERENCES workspace_collaboration_revision_checkpoints(id) ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_collab_history_restores_document
  ON workspace_collaboration_history_restores(document_id,result_revision DESC,id);

CREATE TRIGGER IF NOT EXISTS trg_collab_history_restores_immutable_update
BEFORE UPDATE ON workspace_collaboration_history_restores
BEGIN SELECT RAISE(ABORT, 'history restore is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_collab_history_restores_no_delete
BEFORE DELETE ON workspace_collaboration_history_restores
BEGIN SELECT RAISE(ABORT, 'history restore cannot be deleted'); END;

DROP TRIGGER IF EXISTS trg_collab_files_update_guard;
CREATE TRIGGER trg_collab_files_update_guard
BEFORE UPDATE ON workspace_collaboration_files
WHEN NEW.id<>OLD.id OR NEW.workspace_id<>OLD.workspace_id OR
     NEW.document_id<>OLD.document_id OR NEW.upload_session_id<>OLD.upload_session_id OR
     NEW.uploader_subject_id<>OLD.uploader_subject_id OR NEW.original_name<>OLD.original_name OR
     NEW.mime_type<>OLD.mime_type OR NEW.size_bytes<>OLD.size_bytes OR
     NEW.content_sha256<>OLD.content_sha256 OR NEW.blob_relative_path<>OLD.blob_relative_path OR
     NEW.created_at<>OLD.created_at OR NEW.row_version<>OLD.row_version+1 OR
     NEW.updated_at<OLD.updated_at OR NOT (
       (OLD.state='active' AND NEW.state='deleted' AND
        NEW.deleted_at IS NOT NULL AND NEW.deleted_by_subject_id IS NOT NULL) OR
       (OLD.state='deleted' AND NEW.state='active' AND
        NEW.deleted_at IS NULL AND NEW.deleted_by_subject_id IS NULL)
     )
BEGIN
  SELECT RAISE(ABORT, 'file transition is invalid');
END;
"""

SOURCE_SQL = schema_2408.TARGET_SQL
TARGET_SQL = SOURCE_SQL + HISTORY_AUTHORITY_SCHEMA_SQL
TARGET_CHECKSUM = "sha256:439d5b88d305a39b58454c55c857e45c81f4e864b446c90c9a1a970eff7f3cea"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:975a9493d8afc0166cbeafd7113119598a5d70664dcfd109985388b4b1de56be"

if (
    schema_2408.TARGET_VERSION,
    schema_2408.TARGET_NAME,
    schema_2408.TARGET_CHECKSUM,
    schema_2408.TARGET_ACTUAL_SCHEMA_SHA256,
) != (SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256):
    raise RuntimeError("frozen 2408 history-authority source contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 2408 history-authority source DDL drifted")
SOURCE_OBJECTS, _source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = migration_core._schema_contract(TARGET_SQL)
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 2408 history-authority source objects drifted")
if "TO_BE_FROZEN" not in TARGET_CHECKSUM:
    if (
        "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest()
        != TARGET_CHECKSUM
        or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
    ):
        raise RuntimeError("frozen 2409 history-authority target drifted")

MigrationRefused = migration_core.MigrationRefused


def _contract() -> migration_core._MigrationContract:
    if "TO_BE_FROZEN" in TARGET_CHECKSUM or "TO_BE_FROZEN" in TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("history-authority migration fingerprints are not frozen")
    return migration_core._MigrationContract(
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
        HISTORY_AUTHORITY_SCHEMA_SQL,
        "code_assist.notion_workspace_history_authority_migration.v1",
        (
            "install_immutable_history_restore_ledger",
            "allow_authorized_history_file_reactivation",
            "replace_migration_marker",
        ),
        "2026082408",
        "2026082409",
    )


def build_plan(
    catalog_path: os.PathLike[str] | str,
    user_data_root: os.PathLike[str] | str,
) -> dict[str, Any]:
    return migration_core.build_plan(catalog_path, user_data_root, _contract=_contract())


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
        _contract=_contract(),
    )


__all__ = [
    "HISTORY_AUTHORITY_SCHEMA_SQL",
    "MigrationRefused",
    "SOURCE_ACTUAL_SCHEMA_SHA256",
    "SOURCE_CHECKSUM",
    "SOURCE_NAME",
    "SOURCE_VERSION",
    "TARGET_ACTUAL_SCHEMA_SHA256",
    "TARGET_CHECKSUM",
    "TARGET_NAME",
    "TARGET_VERSION",
    "apply_plan",
    "build_plan",
]
