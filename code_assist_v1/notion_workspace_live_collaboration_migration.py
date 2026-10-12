"""Guarded 2026082403 -> 2026082404 live-collaboration migration.

The extension makes comments fully canonical (soft deletion, resolution and
mention membership) and adds a permission-fenced notification inbox.  Presence
is intentionally absent from this schema: it is an expiring gateway concern
and must never become a surveillance history.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_iam_migration as schema_2403


SOURCE_VERSION = 2026082403
SOURCE_NAME = "notion_workspace_v2_collaborative_iam"
SOURCE_CHECKSUM = schema_2403.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = schema_2403.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082404
TARGET_NAME = "notion_workspace_v2_live_collaboration"


LIVE_COLLABORATION_SCHEMA_SQL = """
ALTER TABLE workspace_collaboration_comments
  ADD COLUMN deleted_at INTEGER;
ALTER TABLE workspace_collaboration_comments
  ADD COLUMN resolved_by_subject_id TEXT;
ALTER TABLE workspace_collaboration_comments
  ADD COLUMN resolved_at INTEGER;

CREATE TABLE IF NOT EXISTS workspace_collaboration_comment_mentions (
  comment_id TEXT NOT NULL,
  mentioned_subject_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(comment_id,mentioned_subject_id),
  FOREIGN KEY(comment_id) REFERENCES workspace_collaboration_comments(id)
    ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS workspace_collaboration_notifications (
  id TEXT PRIMARY KEY,
  recipient_subject_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  comment_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(
    kind IN ('comment_mentioned','comment_replied','comment_reacted')
  ),
  actor_subject_id TEXT NOT NULL,
  source_event_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  workspace_epoch INTEGER NOT NULL CHECK(workspace_epoch >= 1),
  document_epoch INTEGER NOT NULL CHECK(document_epoch >= 1),
  notification_epoch INTEGER NOT NULL DEFAULT 1
    CHECK(notification_epoch >= 1),
  read_at INTEGER,
  redacted_at INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE(recipient_subject_id,source_event_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE CASCADE,
  FOREIGN KEY(comment_id) REFERENCES workspace_collaboration_comments(id)
    ON DELETE CASCADE,
  FOREIGN KEY(source_event_id) REFERENCES workspace_collaboration_events(id)
    DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS idx_collab_mentions_subject
  ON workspace_collaboration_comment_mentions(
    mentioned_subject_id,created_at,comment_id
  );
CREATE INDEX IF NOT EXISTS idx_collab_notifications_recipient
  ON workspace_collaboration_notifications(
    recipient_subject_id,redacted_at,read_at,created_at,id
  );
CREATE INDEX IF NOT EXISTS idx_collab_notifications_document
  ON workspace_collaboration_notifications(
    document_id,recipient_subject_id,created_at,id
  );
CREATE INDEX IF NOT EXISTS idx_collab_comments_visible
  ON workspace_collaboration_comments(
    document_id,deleted_at,resolved,created_at,id
  );

CREATE TRIGGER IF NOT EXISTS trg_collab_comment_resolution_insert
BEFORE INSERT ON workspace_collaboration_comments
WHEN NOT (
  (NEW.resolved=0 AND NEW.resolved_by_subject_id IS NULL AND NEW.resolved_at IS NULL)
  OR
  (NEW.resolved=1 AND NEW.resolved_by_subject_id IS NOT NULL AND NEW.resolved_at IS NOT NULL)
)
BEGIN
  SELECT RAISE(ABORT, 'comment resolution fields are inconsistent');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_comment_resolution_update
BEFORE UPDATE OF resolved,resolved_by_subject_id,resolved_at
ON workspace_collaboration_comments
WHEN NOT (
  (NEW.resolved=0 AND NEW.resolved_by_subject_id IS NULL AND NEW.resolved_at IS NULL)
  OR
  (NEW.resolved=1 AND NEW.resolved_by_subject_id IS NOT NULL AND NEW.resolved_at IS NOT NULL)
)
BEGIN
  SELECT RAISE(ABORT, 'comment resolution fields are inconsistent');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_comment_deleted_immutable
BEFORE UPDATE OF deleted_at ON workspace_collaboration_comments
WHEN OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NOT OLD.deleted_at
BEGIN
  SELECT RAISE(ABORT, 'deleted comment cannot be restored in place');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_notification_payload_immutable
BEFORE UPDATE ON workspace_collaboration_notifications
WHEN NEW.id<>OLD.id OR
     NEW.recipient_subject_id<>OLD.recipient_subject_id OR
     NEW.workspace_id<>OLD.workspace_id OR
     NEW.document_id<>OLD.document_id OR
     NEW.comment_id<>OLD.comment_id OR
     NEW.kind<>OLD.kind OR
     NEW.actor_subject_id<>OLD.actor_subject_id OR
     NEW.source_event_id<>OLD.source_event_id OR
     NEW.payload_json<>OLD.payload_json OR
     NEW.workspace_epoch<>OLD.workspace_epoch OR
     NEW.document_epoch<>OLD.document_epoch OR
     NEW.created_at<>OLD.created_at
BEGIN
  SELECT RAISE(ABORT, 'notification payload is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_notifications_no_delete
BEFORE DELETE ON workspace_collaboration_notifications
BEGIN
  SELECT RAISE(ABORT, 'notification ledger cannot be deleted');
END;
"""

SOURCE_SQL = schema_2403.TARGET_SQL
TARGET_SQL = SOURCE_SQL + LIVE_COLLABORATION_SCHEMA_SQL

# Frozen after the additive DDL and sqlite_master contract are derived.
TARGET_CHECKSUM = "sha256:c57bcbe38e07d72c7b22401077c6cdb0faeb2a51ec5d72d89f59d01c52d02979"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:73d3efe81413600465b279067501328bb1a979c96bdda58ff19cf3168c935ced"


if (
    schema_2403.TARGET_VERSION,
    schema_2403.TARGET_NAME,
    schema_2403.TARGET_CHECKSUM,
    schema_2403.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 2403 live-collaboration source contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 2403 live-collaboration source DDL drifted")

SOURCE_OBJECTS, _source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = migration_core._schema_contract(TARGET_SQL)
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 2403 live-collaboration source objects drifted")
if "TO_BE_FROZEN" not in TARGET_CHECKSUM:
    if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
        raise RuntimeError("frozen live-collaboration target DDL drifted")
    if _target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("frozen live-collaboration target objects drifted")


MigrationRefused = migration_core.MigrationRefused


def _contract() -> migration_core._MigrationContract:
    if "TO_BE_FROZEN" in TARGET_CHECKSUM or "TO_BE_FROZEN" in TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("live-collaboration migration fingerprints are not frozen")
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
        LIVE_COLLABORATION_SCHEMA_SQL,
        "code_assist.notion_workspace_live_collaboration_migration.v1",
        (
            "extend_comments_with_soft_delete_and_resolution",
            "install_canonical_mentions_and_permission_fenced_inbox",
            "install_notification_and_comment_integrity_guards",
            "replace_migration_marker",
        ),
        "2026082403",
        "2026082404",
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
    "LIVE_COLLABORATION_SCHEMA_SQL",
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
