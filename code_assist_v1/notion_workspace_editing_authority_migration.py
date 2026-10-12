"""Guarded 2026082404 -> 2026082405 collaborative-editing migration.

Rich-text updates remain opaque Yjs v1 bytes in the Python authority.  This
additive extension binds those updates and their verified snapshots to an
explicit CRDT generation, while structural block/database mutations receive a
separate server sequence.  User-visible history checkpoints are intentionally
distinct from client operations, document event sequences, CRDT generations,
and publication revisions.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_live_collaboration_migration as schema_2404


SOURCE_VERSION = 2026082404
SOURCE_NAME = "notion_workspace_v2_live_collaboration"
SOURCE_CHECKSUM = schema_2404.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = schema_2404.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082405
TARGET_NAME = "notion_workspace_v2_collaborative_editing_authority"


EDITING_AUTHORITY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workspace_collaboration_crdt_generations (
  document_id TEXT NOT NULL,
  generation INTEGER NOT NULL CHECK(generation >= 1),
  yjs_document_key TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL CHECK(state IN ('current','retired')),
  created_at INTEGER NOT NULL,
  retired_at INTEGER,
  PRIMARY KEY(document_id,generation),
  CHECK(
    (state='current' AND retired_at IS NULL) OR
    (state='retired' AND retired_at IS NOT NULL)
  ),
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE UNIQUE INDEX IF NOT EXISTS idx_collab_crdt_generation_current
  ON workspace_collaboration_crdt_generations(document_id)
  WHERE state='current';

CREATE TABLE IF NOT EXISTS workspace_collaboration_crdt_update_bindings (
  update_id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  generation INTEGER NOT NULL CHECK(generation >= 1),
  rich_text_scope TEXT NOT NULL CHECK(rich_text_scope='document_rich_text_leaves'),
  created_at INTEGER NOT NULL,
  FOREIGN KEY(update_id) REFERENCES workspace_collaboration_crdt_updates(id)
    ON DELETE RESTRICT,
  FOREIGN KEY(document_id,generation)
    REFERENCES workspace_collaboration_crdt_generations(document_id,generation)
    ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS workspace_collaboration_crdt_snapshot_bindings (
  snapshot_id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  generation INTEGER NOT NULL CHECK(generation >= 1),
  created_at INTEGER NOT NULL,
  FOREIGN KEY(snapshot_id) REFERENCES workspace_collaboration_crdt_snapshots(id)
    ON DELETE RESTRICT,
  FOREIGN KEY(document_id,generation)
    REFERENCES workspace_collaboration_crdt_generations(document_id,generation)
    ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS workspace_collaboration_typed_commands (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  command_sequence INTEGER NOT NULL CHECK(command_sequence >= 1),
  document_sequence INTEGER NOT NULL CHECK(document_sequence >= 1),
  actor_subject_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  command_kind TEXT NOT NULL CHECK(command_kind IN (
    'block.insert','block.move','block.delete',
    'database.cell.set','database.property.rename'
  )),
  target_id TEXT NOT NULL,
  expected_document_sequence INTEGER NOT NULL
    CHECK(expected_document_sequence >= 1),
  expected_target_version INTEGER,
  payload_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  workspace_epoch INTEGER NOT NULL CHECK(workspace_epoch >= 1),
  document_epoch INTEGER NOT NULL CHECK(document_epoch >= 1),
  created_at INTEGER NOT NULL,
  UNIQUE(document_id,command_sequence),
  UNIQUE(document_id,document_sequence),
  UNIQUE(actor_subject_id,document_id,operation_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_collab_typed_commands_replay
  ON workspace_collaboration_typed_commands(document_id,document_sequence);

CREATE TABLE IF NOT EXISTS workspace_collaboration_block_tombstones (
  block_id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  deletion_command_id TEXT NOT NULL UNIQUE,
  deleted_document_sequence INTEGER NOT NULL CHECK(deleted_document_sequence >= 1),
  crdt_generation INTEGER NOT NULL CHECK(crdt_generation >= 1),
  through_update_sequence INTEGER NOT NULL CHECK(through_update_sequence >= 0),
  recovery_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  FOREIGN KEY(document_id,crdt_generation)
    REFERENCES workspace_collaboration_crdt_generations(document_id,generation)
    ON DELETE RESTRICT,
  FOREIGN KEY(deletion_command_id) REFERENCES workspace_collaboration_typed_commands(id)
    DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS workspace_collaboration_revision_checkpoints (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  document_sequence INTEGER NOT NULL CHECK(document_sequence >= 1),
  crdt_generation INTEGER NOT NULL CHECK(crdt_generation >= 1),
  crdt_snapshot_id TEXT,
  through_update_sequence INTEGER NOT NULL CHECK(through_update_sequence >= 0),
  through_structure_command_sequence INTEGER NOT NULL
    CHECK(through_structure_command_sequence >= 0),
  content_sha256 TEXT NOT NULL CHECK(
    length(content_sha256)=71 AND substr(content_sha256,1,7)='sha256:'
  ),
  actor_subject_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  checkpoint_reason TEXT NOT NULL CHECK(
    checkpoint_reason IN ('initial','manual','automatic','pre_restore','restore')
  ),
  publication_revision_id TEXT,
  created_at INTEGER NOT NULL,
  UNIQUE(document_id,revision),
  UNIQUE(document_id,document_sequence),
  UNIQUE(actor_subject_id,document_id,operation_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(document_id,revision)
    REFERENCES workspace_collaboration_revisions(document_id,revision)
    ON DELETE RESTRICT,
  FOREIGN KEY(document_id,crdt_generation)
    REFERENCES workspace_collaboration_crdt_generations(document_id,generation)
    ON DELETE RESTRICT,
  FOREIGN KEY(crdt_snapshot_id)
    REFERENCES workspace_collaboration_crdt_snapshots(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_collab_revision_checkpoints_history
  ON workspace_collaboration_revision_checkpoints(document_id,revision DESC);

CREATE TRIGGER IF NOT EXISTS trg_collab_crdt_generations_immutable_identity
BEFORE UPDATE ON workspace_collaboration_crdt_generations
WHEN NEW.document_id<>OLD.document_id OR NEW.generation<>OLD.generation OR
     NEW.yjs_document_key<>OLD.yjs_document_key OR NEW.created_at<>OLD.created_at OR
     OLD.state='retired' OR NEW.state NOT IN ('current','retired')
BEGIN
  SELECT RAISE(ABORT, 'CRDT generation identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_crdt_generations_no_delete
BEFORE DELETE ON workspace_collaboration_crdt_generations
BEGIN
  SELECT RAISE(ABORT, 'CRDT generation ledger cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_crdt_update_bindings_immutable
BEFORE UPDATE ON workspace_collaboration_crdt_update_bindings
BEGIN
  SELECT RAISE(ABORT, 'CRDT update binding is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_crdt_update_bindings_no_delete
BEFORE DELETE ON workspace_collaboration_crdt_update_bindings
BEGIN
  SELECT RAISE(ABORT, 'CRDT update binding cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_crdt_snapshot_bindings_immutable
BEFORE UPDATE ON workspace_collaboration_crdt_snapshot_bindings
BEGIN
  SELECT RAISE(ABORT, 'CRDT snapshot binding is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_crdt_snapshot_bindings_no_delete
BEFORE DELETE ON workspace_collaboration_crdt_snapshot_bindings
BEGIN
  SELECT RAISE(ABORT, 'CRDT snapshot binding cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_typed_commands_immutable
BEFORE UPDATE ON workspace_collaboration_typed_commands
BEGIN
  SELECT RAISE(ABORT, 'typed command ledger is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_typed_commands_no_delete
BEFORE DELETE ON workspace_collaboration_typed_commands
BEGIN
  SELECT RAISE(ABORT, 'typed command ledger cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_block_tombstones_immutable
BEFORE UPDATE ON workspace_collaboration_block_tombstones
BEGIN
  SELECT RAISE(ABORT, 'block tombstone is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_block_tombstones_no_delete
BEFORE DELETE ON workspace_collaboration_block_tombstones
BEGIN
  SELECT RAISE(ABORT, 'block tombstone cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_revision_checkpoints_immutable
BEFORE UPDATE ON workspace_collaboration_revision_checkpoints
BEGIN
  SELECT RAISE(ABORT, 'revision checkpoint is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_revision_checkpoints_no_delete
BEFORE DELETE ON workspace_collaboration_revision_checkpoints
BEGIN
  SELECT RAISE(ABORT, 'revision checkpoint cannot be deleted');
END;

INSERT INTO workspace_collaboration_crdt_generations(
  document_id,generation,yjs_document_key,state,created_at,retired_at
)
SELECT id,1,'document:' || id || ':rich-text:g1','current',created_at,NULL
FROM workspace_collaboration_documents;

INSERT INTO workspace_collaboration_crdt_update_bindings(
  update_id,document_id,generation,rich_text_scope,created_at
)
SELECT id,document_id,1,'document_rich_text_leaves',created_at
FROM workspace_collaboration_crdt_updates;

INSERT INTO workspace_collaboration_crdt_snapshot_bindings(
  snapshot_id,document_id,generation,created_at
)
SELECT id,document_id,1,created_at
FROM workspace_collaboration_crdt_snapshots;

INSERT INTO workspace_collaboration_revision_checkpoints(
  id,workspace_id,document_id,revision,document_sequence,crdt_generation,
  crdt_snapshot_id,through_update_sequence,through_structure_command_sequence,
  content_sha256,actor_subject_id,operation_id,checkpoint_reason,
  publication_revision_id,created_at
)
SELECT
  'hist:' || revision.document_id || ':r' || revision.revision,
  document.workspace_id,
  revision.document_id,
  revision.revision,
  revision.document_sequence,
  1,
  NULL,
  COALESCE((
    SELECT MAX(update_sequence)
    FROM workspace_collaboration_crdt_updates update_row
    WHERE update_row.document_id=revision.document_id
      AND update_row.document_sequence<=revision.document_sequence
  ),0),
  0,
  revision.content_sha256,
  revision.actor_subject_id,
  revision.operation_id,
  'initial',
  NULL,
  revision.created_at
FROM workspace_collaboration_revisions revision
JOIN workspace_collaboration_documents document ON document.id=revision.document_id;
"""

SOURCE_SQL = schema_2404.TARGET_SQL
TARGET_SQL = SOURCE_SQL + EDITING_AUTHORITY_SCHEMA_SQL

# Frozen after the additive DDL and sqlite_master contract are derived.
TARGET_CHECKSUM = "sha256:0110f15428458a6bee11a1c4edca86c9cb2a0c80f8260048e5a371217b34dcf9"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:89a140d1369e6fe742db2d4247f522c7288f6a736dfb5a0557460738be2f550f"


if (
    schema_2404.TARGET_VERSION,
    schema_2404.TARGET_NAME,
    schema_2404.TARGET_CHECKSUM,
    schema_2404.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 2404 collaborative-editing source contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 2404 collaborative-editing source DDL drifted")

SOURCE_OBJECTS, _source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = migration_core._schema_contract(TARGET_SQL)
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 2404 collaborative-editing source objects drifted")
if "TO_BE_FROZEN" not in TARGET_CHECKSUM:
    if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
        raise RuntimeError("frozen collaborative-editing target DDL drifted")
    if _target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("frozen collaborative-editing target objects drifted")


MigrationRefused = migration_core.MigrationRefused


def _contract() -> migration_core._MigrationContract:
    if "TO_BE_FROZEN" in TARGET_CHECKSUM or "TO_BE_FROZEN" in TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("collaborative-editing migration fingerprints are not frozen")
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
        EDITING_AUTHORITY_SCHEMA_SQL,
        "code_assist.notion_workspace_editing_authority_migration.v1",
        (
            "install_document_rich_text_crdt_generation_bindings",
            "install_server_sequenced_typed_command_and_tombstone_ledgers",
            "install_revision_checkpoint_identity_ledger",
            "backfill_existing_generations_updates_snapshots_and_revisions",
            "replace_migration_marker",
        ),
        "2026082404",
        "2026082405",
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
    "EDITING_AUTHORITY_SCHEMA_SQL",
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
