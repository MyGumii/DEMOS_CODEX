"""Guarded 2026081317 -> 2026082401 collaboration-authority migration.

The migration is additive and forward-only.  It extends the existing Notion
workspace catalog so access control, canonical page content, revisions,
comments, database data, idempotent operations, durable events, audit, and the
outbox live in one SQLite authority.  Application imports never apply it; an
operator must use the explicit migrator with an exact dry-run checksum, an
offline logical backup, and a quiesced catalog.

``2026081318`` is deliberately not part of this module.  That number remains
reserved by NP2C archive work.  If the workspace head changes before an apply,
the exact-source checks fail and this migration must be rebased rather than
silently accepting a different lineage.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from typing import Any, Callable

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import (
    notion_workspace_lifecycle_subtree_finalize_migration as schema_1317,
)
from code_assist_v1 import notion_workspace_store as store


SOURCE_VERSION = 2026081317
SOURCE_NAME = "notion_workspace_v2_np2c_lifecycle_subtree_finalize_authority"
SOURCE_CHECKSUM = (
    "sha256:6cb890ccc026435715a1c9171b4d1fd0f74ae6896f63d3cdf66a27b6eec5b61b"
)
SOURCE_ACTUAL_SCHEMA_SHA256 = (
    "sha256:650a031ff1f7d1794bde06d9f9479794d0c89fa2faba8e07762badfe3c500aad"
)
PARALLEL_RESERVED_VERSION = 2026081318
TARGET_VERSION = 2026082401
TARGET_NAME = "notion_workspace_v2_collaboration_canonical_authority"


COLLABORATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workspace_collaboration_documents (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  legacy_owner_subject_id TEXT NOT NULL,
  parent_document_id TEXT,
  title TEXT NOT NULL,
  icon TEXT NOT NULL DEFAULT '',
  cover TEXT NOT NULL DEFAULT '',
  settings_json TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
  archived_at INTEGER,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  document_sequence INTEGER NOT NULL CHECK(document_sequence >= 1),
  access_epoch INTEGER NOT NULL DEFAULT 1 CHECK(access_epoch >= 1),
  row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
  content_sha256 TEXT NOT NULL CHECK(
    length(content_sha256)=71 AND substr(content_sha256,1,7)='sha256:'
  ),
  imported_snapshot_id TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE(workspace_id,id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(parent_document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS workspace_collaboration_blocks (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  position INTEGER NOT NULL CHECK(position >= 0),
  block_type TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  props_json TEXT NOT NULL,
  row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE(document_id,position,id),
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS workspace_collaboration_comments (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  block_id TEXT,
  author_subject_id TEXT NOT NULL,
  content TEXT NOT NULL,
  anchor_json TEXT NOT NULL,
  parent_comment_id TEXT,
  reactions_json TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0 CHECK(resolved IN (0,1)),
  row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE CASCADE,
  FOREIGN KEY(block_id) REFERENCES workspace_collaboration_blocks(id)
    ON DELETE SET NULL,
  FOREIGN KEY(parent_comment_id) REFERENCES workspace_collaboration_comments(id)
    ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS workspace_collaboration_page_grants (
  document_id TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('view','comment','edit')),
  active INTEGER NOT NULL CHECK(active IN (0,1)),
  grant_epoch INTEGER NOT NULL DEFAULT 1 CHECK(grant_epoch >= 1),
  granted_by_subject_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  revoked_at INTEGER,
  PRIMARY KEY(document_id,subject_id),
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS workspace_collaboration_databases (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  title TEXT NOT NULL,
  row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE(document_id,id),
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS workspace_collaboration_database_properties (
  id TEXT PRIMARY KEY,
  database_id TEXT NOT NULL,
  name TEXT NOT NULL,
  property_type TEXT NOT NULL,
  position INTEGER NOT NULL CHECK(position >= 0),
  config_json TEXT NOT NULL,
  row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE(database_id,id),
  UNIQUE(database_id,position,id),
  FOREIGN KEY(database_id) REFERENCES workspace_collaboration_databases(id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS workspace_collaboration_database_rows (
  id TEXT PRIMARY KEY,
  database_id TEXT NOT NULL,
  position INTEGER NOT NULL CHECK(position >= 0),
  row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE(database_id,id),
  UNIQUE(database_id,position,id),
  FOREIGN KEY(database_id) REFERENCES workspace_collaboration_databases(id)
    ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS workspace_collaboration_database_cells (
  row_id TEXT NOT NULL,
  property_id TEXT NOT NULL,
  value_json TEXT NOT NULL,
  row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
  updated_at INTEGER NOT NULL,
  PRIMARY KEY(row_id,property_id),
  FOREIGN KEY(row_id) REFERENCES workspace_collaboration_database_rows(id)
    ON DELETE CASCADE,
  FOREIGN KEY(property_id) REFERENCES workspace_collaboration_database_properties(id)
    ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS workspace_collaboration_revisions (
  document_id TEXT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision >= 1),
  document_sequence INTEGER NOT NULL CHECK(document_sequence >= 1),
  content_sha256 TEXT NOT NULL CHECK(
    length(content_sha256)=71 AND substr(content_sha256,1,7)='sha256:'
  ),
  snapshot_json TEXT NOT NULL,
  actor_subject_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(document_id,revision),
  UNIQUE(document_id,document_sequence),
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS workspace_collaboration_operations (
  actor_subject_id TEXT NOT NULL,
  action TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  request_sha256 TEXT NOT NULL CHECK(
    length(request_sha256)=71 AND substr(request_sha256,1,7)='sha256:'
  ),
  workspace_id TEXT NOT NULL,
  document_id TEXT,
  result_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  PRIMARY KEY(actor_subject_id,action,operation_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS workspace_collaboration_events (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  document_id TEXT,
  document_sequence INTEGER,
  event_type TEXT NOT NULL,
  actor_subject_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  workspace_epoch INTEGER NOT NULL CHECK(workspace_epoch >= 1),
  document_epoch INTEGER,
  payload_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE(document_id,document_sequence),
  UNIQUE(actor_subject_id,event_type,operation_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS workspace_collaboration_audit_events (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  document_id TEXT,
  action TEXT NOT NULL,
  actor_subject_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE(actor_subject_id,action,operation_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS workspace_collaboration_outbox (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  document_id TEXT,
  event_type TEXT NOT NULL,
  actor_subject_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  workspace_epoch INTEGER NOT NULL CHECK(workspace_epoch >= 1),
  document_epoch INTEGER,
  payload_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  delivered_at INTEGER,
  UNIQUE(actor_subject_id,event_type,operation_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS workspace_collaboration_imports (
  snapshot_id TEXT PRIMARY KEY,
  manifest_sha256 TEXT NOT NULL UNIQUE CHECK(
    length(manifest_sha256)=71 AND substr(manifest_sha256,1,7)='sha256:'
  ),
  workspace_id TEXT NOT NULL,
  actor_subject_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  source_high_water_json TEXT NOT NULL,
  imported_page_count INTEGER NOT NULL CHECK(imported_page_count >= 0),
  imported_block_count INTEGER NOT NULL CHECK(imported_block_count >= 0),
  imported_comment_count INTEGER NOT NULL CHECK(imported_comment_count >= 0),
  imported_at INTEGER NOT NULL,
  UNIQUE(actor_subject_id,operation_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS workspace_collaboration_cutovers (
  realm TEXT NOT NULL CHECK(realm='development'),
  deployment_generation_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  activated_at INTEGER NOT NULL,
  runtime_write_count INTEGER NOT NULL DEFAULT 0 CHECK(runtime_write_count >= 0),
  first_runtime_write_at INTEGER,
  last_runtime_write_at INTEGER,
  PRIMARY KEY(realm,deployment_generation_id,workspace_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_collab_documents_workspace
  ON workspace_collaboration_documents(workspace_id,archived,position,id);
CREATE INDEX IF NOT EXISTS idx_collab_documents_parent
  ON workspace_collaboration_documents(workspace_id,parent_document_id,position,id);
CREATE INDEX IF NOT EXISTS idx_collab_blocks_document
  ON workspace_collaboration_blocks(document_id,position,id);
CREATE INDEX IF NOT EXISTS idx_collab_comments_document
  ON workspace_collaboration_comments(document_id,resolved,created_at,id);
CREATE INDEX IF NOT EXISTS idx_collab_grants_subject
  ON workspace_collaboration_page_grants(subject_id,active,document_id);
CREATE INDEX IF NOT EXISTS idx_collab_events_document
  ON workspace_collaboration_events(document_id,document_sequence);
CREATE INDEX IF NOT EXISTS idx_collab_outbox_pending
  ON workspace_collaboration_outbox(delivered_at,created_at,id);

CREATE TRIGGER IF NOT EXISTS trg_collab_operations_immutable_update
BEFORE UPDATE ON workspace_collaboration_operations
BEGIN
  SELECT RAISE(ABORT, 'collaboration operation ledger is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_operations_immutable_delete
BEFORE DELETE ON workspace_collaboration_operations
BEGIN
  SELECT RAISE(ABORT, 'collaboration operation ledger is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_events_immutable_update
BEFORE UPDATE ON workspace_collaboration_events
BEGIN
  SELECT RAISE(ABORT, 'collaboration event ledger is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_events_immutable_delete
BEFORE DELETE ON workspace_collaboration_events
BEGIN
  SELECT RAISE(ABORT, 'collaboration event ledger is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_audit_immutable_update
BEFORE UPDATE ON workspace_collaboration_audit_events
BEGIN
  SELECT RAISE(ABORT, 'collaboration audit ledger is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_audit_immutable_delete
BEFORE DELETE ON workspace_collaboration_audit_events
BEGIN
  SELECT RAISE(ABORT, 'collaboration audit ledger is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_outbox_immutable_update
BEFORE UPDATE ON workspace_collaboration_outbox
WHEN NEW.delivered_at IS NOT OLD.delivered_at OR
     NEW.id<>OLD.id OR NEW.workspace_id<>OLD.workspace_id OR
     NEW.document_id IS NOT OLD.document_id OR NEW.event_type<>OLD.event_type OR
     NEW.actor_subject_id<>OLD.actor_subject_id OR
     NEW.operation_id<>OLD.operation_id OR
     NEW.workspace_epoch<>OLD.workspace_epoch OR
     NEW.document_epoch IS NOT OLD.document_epoch OR
     NEW.payload_json<>OLD.payload_json OR NEW.created_at<>OLD.created_at
BEGIN
  SELECT RAISE(ABORT, 'collaboration outbox payload is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_outbox_immutable_delete
BEFORE DELETE ON workspace_collaboration_outbox
BEGIN
  SELECT RAISE(ABORT, 'collaboration outbox is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_revisions_immutable_update
BEFORE UPDATE ON workspace_collaboration_revisions
BEGIN
  SELECT RAISE(ABORT, 'collaboration revision ledger is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_revisions_immutable_delete
BEFORE DELETE ON workspace_collaboration_revisions
BEGIN
  SELECT RAISE(ABORT, 'collaboration revision ledger is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_imports_immutable_update
BEFORE UPDATE ON workspace_collaboration_imports
BEGIN
  SELECT RAISE(ABORT, 'collaboration import receipt is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_imports_immutable_delete
BEFORE DELETE ON workspace_collaboration_imports
BEGIN
  SELECT RAISE(ABORT, 'collaboration import receipt is immutable');
END;
"""

SOURCE_SQL = store._SCHEMA_SQL + store._HEALING_SCHEMA_SQL
TARGET_SQL = SOURCE_SQL + COLLABORATION_SCHEMA_SQL

# These literals are frozen after deriving the exact SQL and sqlite_master
# contracts below.  They intentionally cover the complete target, not merely
# the additive extension.
TARGET_CHECKSUM = (
    "sha256:13f018dfe76330241c022dc9dd2b052f8612deefd8701f6a20ed526c13cd3d11"
)
TARGET_ACTUAL_SCHEMA_SHA256 = (
    "sha256:d2145ebaf231a9592684a5b80f5f4962dcf2413f949203a7c266265a050d5d88"
)


if (
    schema_1317.TARGET_VERSION,
    schema_1317.TARGET_NAME,
    schema_1317.TARGET_CHECKSUM,
    schema_1317.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 1317 collaboration source contract drifted")
if (
    store.SCHEMA_VERSION,
    store.SCHEMA_MIGRATION_NAME,
    store._SCHEMA_CHECKSUM,
    store._SCHEMA_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("runtime workspace source contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 1317 collaboration source DDL drifted")

SOURCE_OBJECTS, _source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = migration_core._schema_contract(TARGET_SQL)
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 1317 collaboration source objects drifted")
if TARGET_CHECKSUM != "sha256:TO_BE_FROZEN":
    if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
        raise RuntimeError("frozen collaboration target DDL drifted")
    if _target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("frozen collaboration target objects drifted")


MigrationRefused = migration_core.MigrationRefused


def _contract() -> migration_core._MigrationContract:
    if "TO_BE_FROZEN" in TARGET_CHECKSUM or "TO_BE_FROZEN" in TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("collaboration migration fingerprints are not frozen")
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
        COLLABORATION_SCHEMA_SQL,
        "code_assist.notion_workspace_collaboration_migration.v1",
        (
            "install_canonical_document_block_comment_database_schema",
            "install_operation_revision_event_audit_outbox_authority",
            "install_import_and_dev_cutover_receipts",
            "replace_migration_marker",
        ),
        "2026081317",
        "2026082401",
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
    "COLLABORATION_SCHEMA_SQL",
    "MigrationRefused",
    "PARALLEL_RESERVED_VERSION",
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
