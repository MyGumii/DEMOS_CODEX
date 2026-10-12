"""Offline 2428 -> 2429 binding for database-scoped upload sessions.

The empty binding table changes no existing upload or database access. Only
the deployment migrator installs it; database-only upload routes remain gated.
"""
from __future__ import annotations

import hashlib

from code_assist_v1 import notion_workspace_database_sharing_migration as source
from code_assist_v1 import notion_workspace_healing_migration as core


SOURCE_VERSION = source.TARGET_VERSION
SOURCE_NAME = source.TARGET_NAME
SOURCE_CHECKSUM = source.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = source.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082429
TARGET_NAME = "notion_workspace_v2_database_upload_scope"

DATABASE_UPLOAD_SCOPE_SCHEMA_SQL = """
CREATE TABLE workspace_collaboration_database_upload_scopes (
  session_id TEXT PRIMARY KEY CHECK(length(session_id)>0),
  database_id TEXT NOT NULL CHECK(length(database_id)>0),
  database_epoch INTEGER NOT NULL CHECK(database_epoch>=1),
  created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
  FOREIGN KEY(session_id) REFERENCES workspace_collaboration_upload_sessions(id) ON DELETE RESTRICT,
  FOREIGN KEY(database_id) REFERENCES workspace_collaboration_databases(id) ON DELETE RESTRICT
);
CREATE INDEX workspace_collaboration_database_upload_scopes_database
  ON workspace_collaboration_database_upload_scopes(database_id,session_id);
CREATE TRIGGER workspace_collaboration_database_upload_scopes_owner
BEFORE INSERT ON workspace_collaboration_database_upload_scopes
WHEN NOT EXISTS (
  SELECT 1 FROM workspace_collaboration_upload_sessions session
  JOIN workspace_collaboration_databases db ON db.id=NEW.database_id
  JOIN workspace_collaboration_documents doc ON doc.id=db.document_id
  WHERE session.id=NEW.session_id AND session.document_id=db.document_id
    AND session.workspace_id=doc.workspace_id
)
BEGIN
  SELECT RAISE(ABORT,'database upload owner mismatch');
END;
CREATE TRIGGER workspace_collaboration_database_upload_scopes_immutable
BEFORE UPDATE ON workspace_collaboration_database_upload_scopes
BEGIN
  SELECT RAISE(ABORT,'database upload scope is immutable');
END;
CREATE TRIGGER workspace_collaboration_database_upload_scopes_no_delete
BEFORE DELETE ON workspace_collaboration_database_upload_scopes
BEGIN
  SELECT RAISE(ABORT,'database upload scope cannot be removed');
END;
"""

SOURCE_SQL = source.TARGET_SQL
TARGET_SQL = SOURCE_SQL + DATABASE_UPLOAD_SCOPE_SCHEMA_SQL
SOURCE_OBJECTS, _source_actual = core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = core._schema_contract(TARGET_SQL)
TARGET_CHECKSUM = "sha256:ed7d83f683fedaa7d849132034ec65619fc862c6afe8484419613b3c5bd124f1"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:f227bde2de0ad46a56a97bbfcc3a66260d53862ce484fb8a2cfb834d8fb3e808"
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("database upload scope source schema drifted")
if "TO_BE_FROZEN" not in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256) and (
    "sha256:" + hashlib.sha256(TARGET_SQL.encode()).hexdigest() != TARGET_CHECKSUM
    or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
):
    raise RuntimeError("database upload scope target schema drifted")

MigrationRefused = core.MigrationRefused


def _contract():
    if "TO_BE_FROZEN" in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256):
        raise RuntimeError("database upload scope migration fingerprints are not frozen")
    return core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256,
        SOURCE_OBJECTS, TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM,
        TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS,
        DATABASE_UPLOAD_SCOPE_SCHEMA_SQL,
        "code_assist.notion_workspace_database_upload_scope_migration.v1",
        ("install_database_upload_scope", "replace_migration_marker"),
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
