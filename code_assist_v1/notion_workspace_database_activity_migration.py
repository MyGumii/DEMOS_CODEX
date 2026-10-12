"""Offline 2420 -> 2421 database activity identity; no runtime DDL."""
from __future__ import annotations

import hashlib

from code_assist_v1 import notion_workspace_same_document_file_copy_migration as source
from code_assist_v1 import notion_workspace_healing_migration as core

SOURCE_VERSION = source.TARGET_VERSION
SOURCE_NAME = source.TARGET_NAME
SOURCE_CHECKSUM = source.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = source.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082421
TARGET_NAME = "notion_workspace_v2_database_activity"

DATABASE_ACTIVITY_SCHEMA_SQL = """
CREATE TABLE workspace_collaboration_database_activity (
  database_id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  last_editor_subject_id TEXT NOT NULL CHECK(length(last_editor_subject_id)>0),
  last_edited_at INTEGER NOT NULL CHECK(last_edited_at>=0),
  document_sequence INTEGER NOT NULL CHECK(document_sequence>=1),
  FOREIGN KEY(database_id,document_id)
    REFERENCES workspace_collaboration_databases(id,document_id) ON DELETE CASCADE
);
CREATE INDEX idx_collab_database_activity_document
  ON workspace_collaboration_database_activity(document_id,database_id);
CREATE TRIGGER trg_collab_database_activity_direct
AFTER INSERT ON workspace_collaboration_events
WHEN NEW.event_type IN ('database.schema.committed','database.surface.committed','database.copied')
  AND NEW.document_id IS NOT NULL AND NEW.document_sequence IS NOT NULL
BEGIN
  INSERT INTO workspace_collaboration_database_activity(
    database_id,document_id,last_editor_subject_id,last_edited_at,document_sequence)
  SELECT database.id,database.document_id,NEW.actor_subject_id,NEW.created_at,NEW.document_sequence
  FROM workspace_collaboration_databases AS database
  WHERE database.id=json_extract(NEW.payload_json,'$.database_id')
    AND database.document_id=NEW.document_id
  ON CONFLICT(database_id) DO UPDATE SET
    last_editor_subject_id=excluded.last_editor_subject_id,
    last_edited_at=excluded.last_edited_at,
    document_sequence=excluded.document_sequence
  WHERE excluded.document_sequence>workspace_collaboration_database_activity.document_sequence;
END;
CREATE TRIGGER trg_collab_database_activity_cell
AFTER INSERT ON workspace_collaboration_events
WHEN NEW.event_type='typed.command.committed'
  AND json_extract(NEW.payload_json,'$.command_kind')='database.cell.set'
BEGIN
  INSERT INTO workspace_collaboration_database_activity(
    database_id,document_id,last_editor_subject_id,last_edited_at,document_sequence)
  SELECT database.id,database.document_id,NEW.actor_subject_id,NEW.created_at,NEW.document_sequence
  FROM workspace_collaboration_database_rows AS item
  JOIN workspace_collaboration_databases AS database ON database.id=item.database_id
  WHERE item.id=json_extract(NEW.payload_json,'$.row_id')
    AND database.document_id=NEW.document_id AND NEW.document_sequence IS NOT NULL
  ON CONFLICT(database_id) DO UPDATE SET
    last_editor_subject_id=excluded.last_editor_subject_id,
    last_edited_at=excluded.last_edited_at,
    document_sequence=excluded.document_sequence
  WHERE excluded.document_sequence>workspace_collaboration_database_activity.document_sequence;
END;
CREATE TRIGGER trg_collab_database_activity_property_rename
AFTER INSERT ON workspace_collaboration_events
WHEN NEW.event_type='typed.command.committed'
  AND json_extract(NEW.payload_json,'$.command_kind')='database.property.rename'
BEGIN
  INSERT INTO workspace_collaboration_database_activity(
    database_id,document_id,last_editor_subject_id,last_edited_at,document_sequence)
  SELECT database.id,database.document_id,NEW.actor_subject_id,NEW.created_at,NEW.document_sequence
  FROM workspace_collaboration_database_properties AS property
  JOIN workspace_collaboration_databases AS database ON database.id=property.database_id
  WHERE property.id=json_extract(NEW.payload_json,'$.target_id')
    AND database.document_id=NEW.document_id AND NEW.document_sequence IS NOT NULL
  ON CONFLICT(database_id) DO UPDATE SET
    last_editor_subject_id=excluded.last_editor_subject_id,
    last_edited_at=excluded.last_edited_at,
    document_sequence=excluded.document_sequence
  WHERE excluded.document_sequence>workspace_collaboration_database_activity.document_sequence;
END;
"""

SOURCE_SQL = source.TARGET_SQL
TARGET_SQL = SOURCE_SQL + DATABASE_ACTIVITY_SCHEMA_SQL
SOURCE_OBJECTS, _source_actual = core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = core._schema_contract(TARGET_SQL)
TARGET_CHECKSUM = "sha256:841da80edd9b85f9e3cc7a1ef7ec7960c378384a49ad21286cca8b1356874fd3"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:258bf1e2ab9ec41bc2d74589621ad1dad109b444d1d224ba17f64c9f1b339e60"
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("database activity source schema drifted")
if "TO_BE_FROZEN" not in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256) and (
    "sha256:" + hashlib.sha256(TARGET_SQL.encode()).hexdigest() != TARGET_CHECKSUM
    or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
):
    raise RuntimeError("database activity target schema drifted")
MigrationRefused = core.MigrationRefused


def _contract():
    if "TO_BE_FROZEN" in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256):
        raise RuntimeError("database activity fingerprints are not frozen")
    return core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256,
        SOURCE_OBJECTS, TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM,
        TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS,
        DATABASE_ACTIVITY_SCHEMA_SQL,
        "code_assist.notion_workspace_database_activity_migration.v1",
        ("install_database_activity", "replace_migration_marker"),
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
