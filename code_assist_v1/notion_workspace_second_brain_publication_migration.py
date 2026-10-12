"""Guarded 2026082405 -> 2026082406 Second Brain publication-intent migration.

The migration adds only Notion-side coordination state.  The Second Brain
database remains an independent canonical authority and is never attached to
the workspace SQLite connection.  A publication intent records the exact
checkpointed source fence before review; approval durably linearizes as
``sbe_committing`` before any Second Brain service call.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from code_assist_v1 import notion_workspace_editing_authority_migration as schema_2405
from code_assist_v1 import notion_workspace_healing_migration as migration_core


SOURCE_VERSION = 2026082405
SOURCE_NAME = "notion_workspace_v2_collaborative_editing_authority"
SOURCE_CHECKSUM = schema_2405.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = schema_2405.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082406
TARGET_NAME = "notion_workspace_v2_second_brain_publication_intents"


SECOND_BRAIN_PUBLICATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workspace_collaboration_publication_intents (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  actor_subject_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN (
    'prepared','sbe_committing','committed','canceled','reconciliation_required'
  )),
  source_revision INTEGER NOT NULL CHECK(source_revision >= 1),
  source_document_sequence INTEGER NOT NULL CHECK(source_document_sequence >= 1),
  source_workspace_epoch INTEGER NOT NULL CHECK(source_workspace_epoch >= 1),
  source_document_epoch INTEGER NOT NULL CHECK(source_document_epoch >= 1),
  source_history_checkpoint_id TEXT NOT NULL,
  source_canonical_content_sha256 TEXT NOT NULL CHECK(
    length(source_canonical_content_sha256)=71 AND
    substr(source_canonical_content_sha256,1,7)='sha256:'
  ),
  source_content_sha256 TEXT NOT NULL CHECK(length(source_content_sha256)=64),
  source_asset_manifest_sha256 TEXT NOT NULL CHECK(
    length(source_asset_manifest_sha256)=64
  ),
  source_snapshot_sha256 TEXT NOT NULL CHECK(length(source_snapshot_sha256)=64),
  source_fence_sha256 TEXT NOT NULL CHECK(
    length(source_fence_sha256)=71 AND substr(source_fence_sha256,1,7)='sha256:'
  ),
  preview_operation_id TEXT NOT NULL,
  preview_request_sha256 TEXT NOT NULL CHECK(
    length(preview_request_sha256)=71 AND substr(preview_request_sha256,1,7)='sha256:'
  ),
  approve_request_id TEXT,
  publish_operation_id TEXT,
  publication_request_sha256 TEXT CHECK(
    publication_request_sha256 IS NULL OR
    (length(publication_request_sha256)=71 AND
     substr(publication_request_sha256,1,7)='sha256:')
  ),
  sbe_actor_scope_epoch INTEGER CHECK(
    sbe_actor_scope_epoch IS NULL OR sbe_actor_scope_epoch >= 1
  ),
  target_mode TEXT CHECK(
    target_mode IS NULL OR target_mode IN ('personal','part','both','explicit')
  ),
  personal_space_id TEXT,
  target_descriptor_json TEXT,
  sbe_preview_id TEXT,
  publication_result_json TEXT,
  cancel_operation_id TEXT,
  cancel_request_sha256 TEXT CHECK(
    cancel_request_sha256 IS NULL OR
    (length(cancel_request_sha256)=71 AND substr(cancel_request_sha256,1,7)='sha256:')
  ),
  terminal_reason TEXT,
  row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE(actor_subject_id,preview_operation_id),
  UNIQUE(actor_subject_id,publish_operation_id),
  UNIQUE(actor_subject_id,cancel_operation_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(document_id,source_revision)
    REFERENCES workspace_collaboration_revisions(document_id,revision)
    ON DELETE RESTRICT,
  FOREIGN KEY(source_history_checkpoint_id)
    REFERENCES workspace_collaboration_revision_checkpoints(id) ON DELETE RESTRICT,
  CHECK(
    (state='prepared' AND approve_request_id IS NULL AND
      publish_operation_id IS NULL AND publication_request_sha256 IS NULL AND
      sbe_actor_scope_epoch IS NULL AND target_mode IS NULL AND
      personal_space_id IS NULL AND target_descriptor_json IS NULL AND
      sbe_preview_id IS NULL AND publication_result_json IS NULL AND
      cancel_operation_id IS NULL AND cancel_request_sha256 IS NULL AND
      terminal_reason IS NULL)
    OR
    (state IN ('sbe_committing','reconciliation_required') AND
      approve_request_id IS NOT NULL AND publish_operation_id IS NOT NULL AND
      publication_request_sha256 IS NOT NULL AND sbe_actor_scope_epoch IS NOT NULL AND
      target_mode IS NOT NULL AND publication_result_json IS NULL AND
      cancel_operation_id IS NULL AND cancel_request_sha256 IS NULL)
    OR
    (state='committed' AND approve_request_id IS NOT NULL AND
      publish_operation_id IS NOT NULL AND publication_request_sha256 IS NOT NULL AND
      sbe_actor_scope_epoch IS NOT NULL AND target_mode IS NOT NULL AND
      sbe_preview_id IS NOT NULL AND publication_result_json IS NOT NULL AND
      cancel_operation_id IS NULL AND cancel_request_sha256 IS NULL AND
      terminal_reason='sbe_publication_committed')
    OR
    (state='canceled' AND publication_result_json IS NULL AND
      cancel_operation_id IS NOT NULL AND cancel_request_sha256 IS NOT NULL AND
      terminal_reason IN ('user_canceled','source_authority_revoked'))
  )
);

CREATE INDEX IF NOT EXISTS idx_collab_publication_intents_document_state
  ON workspace_collaboration_publication_intents(
    document_id,actor_subject_id,state,updated_at,id
  );
CREATE INDEX IF NOT EXISTS idx_collab_publication_intents_reconcile
  ON workspace_collaboration_publication_intents(state,updated_at,id)
  WHERE state IN ('sbe_committing','reconciliation_required');

CREATE TABLE IF NOT EXISTS workspace_collaboration_publication_intent_events (
  intent_id TEXT NOT NULL,
  event_sequence INTEGER NOT NULL CHECK(event_sequence >= 1),
  event_type TEXT NOT NULL CHECK(event_type IN (
    'intent.prepared','intent.approved','sbe.preview_bound',
    'sbe.commit_ambiguous','sbe.committed','intent.canceled'
  )),
  state TEXT NOT NULL CHECK(state IN (
    'prepared','sbe_committing','committed','canceled','reconciliation_required'
  )),
  actor_subject_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  request_sha256 TEXT NOT NULL CHECK(
    length(request_sha256)=71 AND substr(request_sha256,1,7)='sha256:'
  ),
  details_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(intent_id,event_sequence),
  FOREIGN KEY(intent_id)
    REFERENCES workspace_collaboration_publication_intents(id) ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS workspace_collaboration_publication_outbox (
  id TEXT PRIMARY KEY,
  intent_id TEXT NOT NULL,
  event_sequence INTEGER NOT NULL CHECK(event_sequence >= 1),
  topic TEXT NOT NULL CHECK(topic='notion.second_brain.publication_intent'),
  payload_sha256 TEXT NOT NULL CHECK(
    length(payload_sha256)=71 AND substr(payload_sha256,1,7)='sha256:'
  ),
  payload_json TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
  next_attempt_at INTEGER NOT NULL,
  delivered_at INTEGER,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE(intent_id,event_sequence),
  FOREIGN KEY(intent_id,event_sequence)
    REFERENCES workspace_collaboration_publication_intent_events(
      intent_id,event_sequence
    ) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_collab_publication_outbox_delivery
  ON workspace_collaboration_publication_outbox(
    delivered_at,next_attempt_at,created_at,id
  );

CREATE TRIGGER IF NOT EXISTS trg_collab_publication_intent_identity_immutable
BEFORE UPDATE ON workspace_collaboration_publication_intents
WHEN NEW.id<>OLD.id OR NEW.workspace_id<>OLD.workspace_id OR
     NEW.document_id<>OLD.document_id OR
     NEW.actor_subject_id<>OLD.actor_subject_id OR
     NEW.source_revision<>OLD.source_revision OR
     NEW.source_document_sequence<>OLD.source_document_sequence OR
     NEW.source_workspace_epoch<>OLD.source_workspace_epoch OR
     NEW.source_document_epoch<>OLD.source_document_epoch OR
     NEW.source_history_checkpoint_id<>OLD.source_history_checkpoint_id OR
     NEW.source_canonical_content_sha256<>OLD.source_canonical_content_sha256 OR
     NEW.source_content_sha256<>OLD.source_content_sha256 OR
     NEW.source_asset_manifest_sha256<>OLD.source_asset_manifest_sha256 OR
     NEW.source_snapshot_sha256<>OLD.source_snapshot_sha256 OR
     NEW.source_fence_sha256<>OLD.source_fence_sha256 OR
     NEW.preview_operation_id<>OLD.preview_operation_id OR
     NEW.preview_request_sha256<>OLD.preview_request_sha256 OR
     NEW.created_at<>OLD.created_at
BEGIN
  SELECT RAISE(ABORT, 'publication intent source identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_publication_intent_transition
BEFORE UPDATE ON workspace_collaboration_publication_intents
WHEN NOT (
  (OLD.state='prepared' AND NEW.state IN ('sbe_committing','canceled')) OR
  (OLD.state='sbe_committing' AND
    NEW.state IN ('sbe_committing','committed','reconciliation_required')) OR
  (OLD.state='reconciliation_required' AND
    NEW.state IN ('sbe_committing','committed','canceled'))
) OR NEW.row_version<>OLD.row_version+1 OR NEW.updated_at<OLD.updated_at
BEGIN
  SELECT RAISE(ABORT, 'publication intent transition is invalid');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_publication_intents_no_delete
BEFORE DELETE ON workspace_collaboration_publication_intents
BEGIN
  SELECT RAISE(ABORT, 'publication intent cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_publication_intent_events_immutable_update
BEFORE UPDATE ON workspace_collaboration_publication_intent_events
BEGIN
  SELECT RAISE(ABORT, 'publication intent event is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_publication_intent_events_no_delete
BEFORE DELETE ON workspace_collaboration_publication_intent_events
BEGIN
  SELECT RAISE(ABORT, 'publication intent event cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_publication_outbox_payload_immutable
BEFORE UPDATE ON workspace_collaboration_publication_outbox
WHEN NEW.id<>OLD.id OR NEW.intent_id<>OLD.intent_id OR
     NEW.event_sequence<>OLD.event_sequence OR NEW.topic<>OLD.topic OR
     NEW.payload_sha256<>OLD.payload_sha256 OR NEW.payload_json<>OLD.payload_json OR
     NEW.created_at<>OLD.created_at OR NEW.attempt_count<OLD.attempt_count OR
     NEW.next_attempt_at<OLD.next_attempt_at OR
     (OLD.delivered_at IS NOT NULL AND NEW.delivered_at IS NOT OLD.delivered_at)
BEGIN
  SELECT RAISE(ABORT, 'publication outbox payload or progress is invalid');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_publication_outbox_no_delete
BEFORE DELETE ON workspace_collaboration_publication_outbox
BEGIN
  SELECT RAISE(ABORT, 'publication outbox cannot be deleted');
END;
"""

SOURCE_SQL = schema_2405.TARGET_SQL
TARGET_SQL = SOURCE_SQL + SECOND_BRAIN_PUBLICATION_SCHEMA_SQL
TARGET_CHECKSUM = "sha256:8aa02ee6e148823d3a69050a5baf3bc34b88e14003c082e0a66a950f7852b3c7"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:d47cf6e417ecf69b0c3d138f67b557679e374dffa999eeb314caf7cceee848e3"


if (
    schema_2405.TARGET_VERSION,
    schema_2405.TARGET_NAME,
    schema_2405.TARGET_CHECKSUM,
    schema_2405.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 2405 publication-intent source contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 2405 publication-intent source DDL drifted")

SOURCE_OBJECTS, _source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = migration_core._schema_contract(TARGET_SQL)
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 2405 publication-intent source objects drifted")
if "TO_BE_FROZEN" not in TARGET_CHECKSUM:
    if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
        raise RuntimeError("frozen publication-intent target DDL drifted")
    if _target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("frozen publication-intent target objects drifted")


MigrationRefused = migration_core.MigrationRefused


def _contract() -> migration_core._MigrationContract:
    if "TO_BE_FROZEN" in TARGET_CHECKSUM or "TO_BE_FROZEN" in TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("publication-intent migration fingerprints are not frozen")
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
        SECOND_BRAIN_PUBLICATION_SCHEMA_SQL,
        "code_assist.notion_workspace_second_brain_publication_migration.v1",
        (
            "install_exact_revision_publication_intent_ledger",
            "install_immutable_transition_event_and_outbox_ledgers",
            "install_reconciliation_and_revocation_fence_indexes",
            "replace_migration_marker",
        ),
        "2026082405",
        "2026082406",
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
    "SECOND_BRAIN_PUBLICATION_SCHEMA_SQL",
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
