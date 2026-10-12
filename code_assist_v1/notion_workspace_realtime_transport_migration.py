"""Guarded 2026082401 -> 2026082402 durable realtime transport migration.

The additive schema persists only ticket digests, client ACK state, opaque Yjs
updates, and validated snapshots beside the canonical collaboration authority.
It does not start a gateway, import an identity, or enable a capability.  Apply
remains an explicit, quiesced, backup-bound operator action.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from code_assist_v1 import notion_workspace_collaboration_migration as schema_2401
from code_assist_v1 import notion_workspace_healing_migration as migration_core


SOURCE_VERSION = 2026082401
SOURCE_NAME = "notion_workspace_v2_collaboration_canonical_authority"
SOURCE_CHECKSUM = "sha256:13f018dfe76330241c022dc9dd2b052f8612deefd8701f6a20ed526c13cd3d11"
SOURCE_ACTUAL_SCHEMA_SHA256 = "sha256:d2145ebaf231a9592684a5b80f5f4962dcf2413f949203a7c266265a050d5d88"
PARALLEL_RESERVED_VERSION = 2026081318
TARGET_VERSION = 2026082402
TARGET_NAME = "notion_workspace_v2_durable_realtime_transport"


REALTIME_TRANSPORT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workspace_collaboration_tickets (
  ticket_sha256 TEXT PRIMARY KEY CHECK(
    length(ticket_sha256)=71 AND substr(ticket_sha256,1,7)='sha256:'
  ),
  jti TEXT NOT NULL UNIQUE,
  subject_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  session_binding_sha256 TEXT NOT NULL CHECK(
    length(session_binding_sha256)=71 AND substr(session_binding_sha256,1,7)='sha256:'
  ),
  origin TEXT NOT NULL,
  capabilities_json TEXT NOT NULL,
  workspace_epoch INTEGER NOT NULL CHECK(workspace_epoch >= 1),
  document_epoch INTEGER NOT NULL CHECK(document_epoch >= 1),
  issued_at_ms INTEGER NOT NULL CHECK(issued_at_ms >= 0),
  expires_at_ms INTEGER NOT NULL CHECK(expires_at_ms > issued_at_ms),
  consumed_at_ms INTEGER,
  burn_reason TEXT,
  created_at INTEGER NOT NULL,
  CHECK((consumed_at_ms IS NULL AND burn_reason IS NULL) OR
        (consumed_at_ms IS NOT NULL AND burn_reason IS NOT NULL)),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS workspace_collaboration_client_ack_state (
  subject_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  session_binding_sha256 TEXT NOT NULL CHECK(
    length(session_binding_sha256)=71 AND substr(session_binding_sha256,1,7)='sha256:'
  ),
  next_client_sequence INTEGER NOT NULL CHECK(next_client_sequence >= 1),
  acknowledged_document_sequence INTEGER NOT NULL DEFAULT 0
    CHECK(acknowledged_document_sequence >= 0),
  workspace_epoch INTEGER NOT NULL CHECK(workspace_epoch >= 1),
  document_epoch INTEGER NOT NULL CHECK(document_epoch >= 1),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(subject_id,document_id,client_id,session_binding_sha256),
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS workspace_collaboration_crdt_updates (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  update_sequence INTEGER NOT NULL CHECK(update_sequence >= 1),
  document_sequence INTEGER NOT NULL CHECK(document_sequence >= 1),
  actor_subject_id TEXT NOT NULL,
  client_id TEXT NOT NULL,
  session_binding_sha256 TEXT NOT NULL CHECK(
    length(session_binding_sha256)=71 AND substr(session_binding_sha256,1,7)='sha256:'
  ),
  operation_id TEXT NOT NULL,
  client_sequence INTEGER NOT NULL CHECK(client_sequence >= 1),
  payload_encoding TEXT NOT NULL CHECK(payload_encoding='application/vnd.yjs-update'),
  payload_bytes INTEGER NOT NULL CHECK(payload_bytes > 0 AND payload_bytes <= 1048576),
  payload_sha256 TEXT NOT NULL CHECK(
    length(payload_sha256)=71 AND substr(payload_sha256,1,7)='sha256:'
  ),
  payload BLOB NOT NULL,
  workspace_epoch INTEGER NOT NULL CHECK(workspace_epoch >= 1),
  document_epoch INTEGER NOT NULL CHECK(document_epoch >= 1),
  runtime_binding_sha256 TEXT NOT NULL CHECK(
    length(runtime_binding_sha256)=71 AND substr(runtime_binding_sha256,1,7)='sha256:'
  ),
  created_at INTEGER NOT NULL,
  UNIQUE(document_id,update_sequence),
  UNIQUE(document_id,document_sequence),
  UNIQUE(actor_subject_id,document_id,operation_id),
  UNIQUE(actor_subject_id,document_id,client_id,session_binding_sha256,client_sequence),
  CHECK(length(payload)=payload_bytes),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS workspace_collaboration_crdt_snapshots (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  actor_subject_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  base_snapshot_id TEXT,
  base_update_sequence INTEGER NOT NULL CHECK(base_update_sequence >= 0),
  through_update_sequence INTEGER NOT NULL CHECK(through_update_sequence > base_update_sequence),
  through_document_sequence INTEGER NOT NULL CHECK(through_document_sequence >= 1),
  snapshot_bytes INTEGER NOT NULL CHECK(snapshot_bytes > 0),
  snapshot_sha256 TEXT NOT NULL CHECK(
    length(snapshot_sha256)=71 AND substr(snapshot_sha256,1,7)='sha256:'
  ),
  snapshot BLOB NOT NULL,
  state_vector_bytes INTEGER NOT NULL CHECK(state_vector_bytes > 0),
  state_vector_sha256 TEXT NOT NULL CHECK(
    length(state_vector_sha256)=71 AND substr(state_vector_sha256,1,7)='sha256:'
  ),
  state_vector BLOB NOT NULL,
  request_sha256 TEXT NOT NULL CHECK(
    length(request_sha256)=71 AND substr(request_sha256,1,7)='sha256:'
  ),
  runtime_binding_sha256 TEXT NOT NULL CHECK(
    length(runtime_binding_sha256)=71 AND substr(runtime_binding_sha256,1,7)='sha256:'
  ),
  workspace_epoch INTEGER NOT NULL CHECK(workspace_epoch >= 1),
  document_epoch INTEGER NOT NULL CHECK(document_epoch >= 1),
  created_at INTEGER NOT NULL,
  UNIQUE(document_id,through_update_sequence),
  UNIQUE(actor_subject_id,document_id,operation_id),
  CHECK(length(snapshot)=snapshot_bytes),
  CHECK(length(state_vector)=state_vector_bytes),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE CASCADE,
  FOREIGN KEY(base_snapshot_id) REFERENCES workspace_collaboration_crdt_snapshots(id)
    ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_collab_tickets_expiry
  ON workspace_collaboration_tickets(expires_at_ms,consumed_at_ms);
CREATE INDEX IF NOT EXISTS idx_collab_crdt_updates_replay
  ON workspace_collaboration_crdt_updates(document_id,document_sequence);
CREATE INDEX IF NOT EXISTS idx_collab_crdt_snapshots_latest
  ON workspace_collaboration_crdt_snapshots(document_id,through_update_sequence DESC);

CREATE TRIGGER IF NOT EXISTS trg_collab_tickets_consume_once
BEFORE UPDATE ON workspace_collaboration_tickets
WHEN OLD.consumed_at_ms IS NOT NULL OR NEW.consumed_at_ms IS NULL OR NEW.burn_reason IS NULL OR
     NEW.ticket_sha256<>OLD.ticket_sha256 OR NEW.jti<>OLD.jti OR
     NEW.subject_id<>OLD.subject_id OR NEW.workspace_id<>OLD.workspace_id OR
     NEW.document_id<>OLD.document_id OR NEW.client_id<>OLD.client_id OR
     NEW.session_binding_sha256<>OLD.session_binding_sha256 OR NEW.origin<>OLD.origin OR
     NEW.capabilities_json<>OLD.capabilities_json OR
     NEW.workspace_epoch<>OLD.workspace_epoch OR NEW.document_epoch<>OLD.document_epoch OR
     NEW.issued_at_ms<>OLD.issued_at_ms OR NEW.expires_at_ms<>OLD.expires_at_ms OR
     NEW.created_at<>OLD.created_at
BEGIN
  SELECT RAISE(ABORT, 'collaboration ticket is immutable except one consume');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_tickets_no_delete
BEFORE DELETE ON workspace_collaboration_tickets
BEGIN
  SELECT RAISE(ABORT, 'collaboration ticket ledger is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_client_ack_monotonic
BEFORE UPDATE ON workspace_collaboration_client_ack_state
WHEN NEW.subject_id<>OLD.subject_id OR NEW.document_id<>OLD.document_id OR
     NEW.client_id<>OLD.client_id OR
     NEW.session_binding_sha256<>OLD.session_binding_sha256 OR
     NEW.next_client_sequence<OLD.next_client_sequence OR
     NEW.acknowledged_document_sequence<OLD.acknowledged_document_sequence OR
     NEW.workspace_epoch<OLD.workspace_epoch OR NEW.document_epoch<OLD.document_epoch
BEGIN
  SELECT RAISE(ABORT, 'collaboration client ACK state must be monotonic');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_client_ack_no_delete
BEFORE DELETE ON workspace_collaboration_client_ack_state
BEGIN
  SELECT RAISE(ABORT, 'collaboration client ACK state may not be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_crdt_updates_immutable_update
BEFORE UPDATE ON workspace_collaboration_crdt_updates
BEGIN
  SELECT RAISE(ABORT, 'collaboration CRDT update is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_crdt_updates_immutable_delete
BEFORE DELETE ON workspace_collaboration_crdt_updates
BEGIN
  SELECT RAISE(ABORT, 'collaboration CRDT update is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_crdt_snapshots_immutable_update
BEFORE UPDATE ON workspace_collaboration_crdt_snapshots
BEGIN
  SELECT RAISE(ABORT, 'collaboration CRDT snapshot is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_crdt_snapshots_immutable_delete
BEFORE DELETE ON workspace_collaboration_crdt_snapshots
BEGIN
  SELECT RAISE(ABORT, 'collaboration CRDT snapshot is immutable');
END;
"""

SOURCE_SQL = schema_2401.TARGET_SQL
TARGET_SQL = SOURCE_SQL + REALTIME_TRANSPORT_SCHEMA_SQL
TARGET_CHECKSUM = "sha256:76377bdee7954f9d03d27dcf56dcf052799547d65d4e6ba21f117455e085baba"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:8f3bb03c8cecef84f9c4343234743bcd57c9264a293699d89576becb56c966c9"

if (
    schema_2401.TARGET_VERSION,
    schema_2401.TARGET_NAME,
    schema_2401.TARGET_CHECKSUM,
    schema_2401.TARGET_ACTUAL_SCHEMA_SHA256,
) != (SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256):
    raise RuntimeError("frozen 2026082401 realtime source contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 2026082401 realtime source DDL drifted")

SOURCE_OBJECTS, _source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = migration_core._schema_contract(TARGET_SQL)
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 2026082401 realtime source objects drifted")
if "TO_BE_FROZEN" not in TARGET_CHECKSUM:
    if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
        raise RuntimeError("frozen realtime target DDL drifted")
    if _target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("frozen realtime target objects drifted")

MigrationRefused = migration_core.MigrationRefused


def _contract() -> migration_core._MigrationContract:
    if "TO_BE_FROZEN" in TARGET_CHECKSUM or "TO_BE_FROZEN" in TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("realtime transport migration fingerprints are not frozen")
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
        REALTIME_TRANSPORT_SCHEMA_SQL,
        "code_assist.notion_workspace_realtime_transport_migration.v1",
        (
            "install_digest_only_one_time_ticket_ledger",
            "install_monotonic_client_ack_state",
            "install_opaque_yjs_update_and_snapshot_ledgers",
            "install_immutable_and_monotonic_guards",
            "replace_migration_marker",
        ),
        "2026082401",
        "2026082402",
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
    "PARALLEL_RESERVED_VERSION",
    "REALTIME_TRANSPORT_SCHEMA_SQL",
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
