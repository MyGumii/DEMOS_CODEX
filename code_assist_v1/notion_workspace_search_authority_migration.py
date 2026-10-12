"""Guarded 2026082409 -> 2026082410 collaborative search authority migration."""

from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_history_authority_migration as schema_2409


SOURCE_VERSION = 2026082409
SOURCE_NAME = schema_2409.TARGET_NAME
SOURCE_CHECKSUM = schema_2409.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = schema_2409.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082410
TARGET_NAME = "notion_workspace_v2_collaborative_search_authority"


SEARCH_AUTHORITY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workspace_collaboration_search_state (
  workspace_id TEXT PRIMARY KEY,
  desired_generation INTEGER NOT NULL DEFAULT 1 CHECK(desired_generation >= 1),
  built_generation INTEGER NOT NULL DEFAULT 0 CHECK(built_generation >= 0),
  source_document_count INTEGER NOT NULL DEFAULT 0 CHECK(source_document_count >= 0),
  indexed_document_count INTEGER NOT NULL DEFAULT 0 CHECK(indexed_document_count >= 0),
  backlink_count INTEGER NOT NULL DEFAULT 0 CHECK(backlink_count >= 0),
  reminder_count INTEGER NOT NULL DEFAULT 0 CHECK(reminder_count >= 0),
  last_rebuild_sha256 TEXT,
  built_at INTEGER,
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
) WITHOUT ROWID;

INSERT OR IGNORE INTO workspace_collaboration_search_state(workspace_id)
SELECT id FROM workspaces;

CREATE TABLE IF NOT EXISTS workspace_collaboration_search_documents (
  document_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  legacy_owner_subject_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  document_sequence INTEGER NOT NULL CHECK(document_sequence >= 1),
  access_epoch INTEGER NOT NULL CHECK(access_epoch >= 1),
  content_sha256 TEXT NOT NULL CHECK(
    length(content_sha256)=71 AND substr(content_sha256,1,7)='sha256:'
  ),
  title TEXT NOT NULL,
  body_text TEXT NOT NULL,
  search_text TEXT NOT NULL,
  indexed_at INTEGER NOT NULL,
  source_updated_at INTEGER NOT NULL,
  UNIQUE(workspace_id,document_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_collab_search_documents_workspace_recent
  ON workspace_collaboration_search_documents(workspace_id,source_updated_at DESC,document_id);
CREATE INDEX IF NOT EXISTS idx_collab_search_documents_owner
  ON workspace_collaboration_search_documents(workspace_id,legacy_owner_subject_id,document_id);

CREATE TABLE IF NOT EXISTS workspace_collaboration_search_backlinks (
  workspace_id TEXT NOT NULL,
  target_document_id TEXT NOT NULL,
  source_document_id TEXT NOT NULL,
  source_block_id TEXT NOT NULL,
  source_revision INTEGER NOT NULL CHECK(source_revision >= 1),
  context_text TEXT NOT NULL,
  indexed_at INTEGER NOT NULL,
  PRIMARY KEY(target_document_id,source_document_id,source_block_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
  FOREIGN KEY(target_document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE CASCADE,
  FOREIGN KEY(source_document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE CASCADE
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_collab_search_backlinks_workspace_source
  ON workspace_collaboration_search_backlinks(workspace_id,source_document_id,target_document_id);

CREATE TABLE IF NOT EXISTS workspace_collaboration_search_reminders (
  reminder_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  block_id TEXT NOT NULL,
  source_locator TEXT NOT NULL,
  recipient_subject_id TEXT NOT NULL,
  remind_at TEXT NOT NULL,
  trigger_at INTEGER NOT NULL,
  lead_minutes INTEGER NOT NULL CHECK(lead_minutes >= 0 AND lead_minutes <= 525600),
  message TEXT NOT NULL,
  source_revision INTEGER NOT NULL CHECK(source_revision >= 1),
  indexed_at INTEGER NOT NULL,
  UNIQUE(document_id,source_locator,recipient_subject_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_collab_search_reminders_recipient_due
  ON workspace_collaboration_search_reminders(recipient_subject_id,trigger_at,reminder_id);

CREATE TRIGGER IF NOT EXISTS trg_collab_search_state_document_insert
AFTER INSERT ON workspace_collaboration_documents
BEGIN
  INSERT INTO workspace_collaboration_search_state(workspace_id,desired_generation)
  VALUES(NEW.workspace_id,1)
  ON CONFLICT(workspace_id) DO UPDATE SET
    desired_generation=workspace_collaboration_search_state.desired_generation+1;
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_search_state_document_update
AFTER UPDATE ON workspace_collaboration_documents
BEGIN
  INSERT INTO workspace_collaboration_search_state(workspace_id,desired_generation)
  VALUES(NEW.workspace_id,1)
  ON CONFLICT(workspace_id) DO UPDATE SET
    desired_generation=workspace_collaboration_search_state.desired_generation+1;
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_search_state_document_delete
AFTER DELETE ON workspace_collaboration_documents
BEGIN
  INSERT INTO workspace_collaboration_search_state(workspace_id,desired_generation)
  VALUES(OLD.workspace_id,1)
  ON CONFLICT(workspace_id) DO UPDATE SET
    desired_generation=workspace_collaboration_search_state.desired_generation+1;
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_search_state_block_insert
AFTER INSERT ON workspace_collaboration_blocks
BEGIN
  UPDATE workspace_collaboration_search_state
  SET desired_generation=desired_generation+1
  WHERE workspace_id=(
    SELECT workspace_id FROM workspace_collaboration_documents WHERE id=NEW.document_id
  );
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_search_state_block_update
AFTER UPDATE ON workspace_collaboration_blocks
BEGIN
  UPDATE workspace_collaboration_search_state
  SET desired_generation=desired_generation+1
  WHERE workspace_id=(
    SELECT workspace_id FROM workspace_collaboration_documents WHERE id=NEW.document_id
  );
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_search_state_block_delete
AFTER DELETE ON workspace_collaboration_blocks
BEGIN
  UPDATE workspace_collaboration_search_state
  SET desired_generation=desired_generation+1
  WHERE workspace_id=(
    SELECT workspace_id FROM workspace_collaboration_documents WHERE id=OLD.document_id
  );
END;
"""

SOURCE_SQL = schema_2409.TARGET_SQL
TARGET_SQL = SOURCE_SQL + SEARCH_AUTHORITY_SCHEMA_SQL
TARGET_CHECKSUM = "sha256:4cc837e65833919c1ff4b0572d89578a689eb70ab2836252c3e813682ff2074f"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:51511a28586325ace00e0d991774618715e6c3196ca357421d193fcbee126912"

if (
    schema_2409.TARGET_VERSION,
    schema_2409.TARGET_NAME,
    schema_2409.TARGET_CHECKSUM,
    schema_2409.TARGET_ACTUAL_SCHEMA_SHA256,
) != (SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256):
    raise RuntimeError("frozen 2409 search-authority source contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 2409 search-authority source DDL drifted")
SOURCE_OBJECTS, _source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = migration_core._schema_contract(TARGET_SQL)
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 2409 search-authority source objects drifted")
if "TO_BE_FROZEN" not in TARGET_CHECKSUM:
    if (
        "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest()
        != TARGET_CHECKSUM
        or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
    ):
        raise RuntimeError("frozen 2410 search-authority target drifted")

MigrationRefused = migration_core.MigrationRefused


def _contract() -> migration_core._MigrationContract:
    if "TO_BE_FROZEN" in TARGET_CHECKSUM or "TO_BE_FROZEN" in TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("search-authority migration fingerprints are not frozen")
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
        SEARCH_AUTHORITY_SCHEMA_SQL,
        "code_assist.notion_workspace_search_authority_migration.v1",
        (
            "install_search_backlink_and_reminder_indexes",
            "install_source_generation_triggers",
            "replace_migration_marker",
        ),
        "2026082409",
        "2026082410",
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
    "MigrationRefused",
    "SEARCH_AUTHORITY_SCHEMA_SQL",
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
