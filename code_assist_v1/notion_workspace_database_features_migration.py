"""Guarded 2026082406 -> 2026082407 collaborative database-feature migration.

The additive schema gives adopted database views and templates durable canonical
identity and records schema mutations separately from content-only cell edits.
Relation, rollup, and formula definitions remain typed property configuration;
guards reject malformed or unsupported definitions even for direct SQL writes.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_second_brain_publication_migration as schema_2406


SOURCE_VERSION = 2026082406
SOURCE_NAME = "notion_workspace_v2_second_brain_publication_intents"
SOURCE_CHECKSUM = schema_2406.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = schema_2406.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082407
TARGET_NAME = "notion_workspace_v2_collaborative_database_features"


DATABASE_FEATURE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workspace_collaboration_database_views (
  id TEXT PRIMARY KEY,
  database_id TEXT NOT NULL,
  name TEXT NOT NULL,
  view_type TEXT NOT NULL CHECK(view_type IN (
    'table','board','list','gallery','calendar','timeline'
  )),
  position INTEGER NOT NULL CHECK(position >= 0),
  config_json TEXT NOT NULL CHECK(json_valid(config_json)=1),
  state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','archived')),
  row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  archived_at INTEGER,
  UNIQUE(database_id,id),
  UNIQUE(database_id,position,id),
  CHECK(
    (state='active' AND archived_at IS NULL) OR
    (state='archived' AND archived_at IS NOT NULL)
  ),
  FOREIGN KEY(database_id) REFERENCES workspace_collaboration_databases(id)
    ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_collab_database_views_order
  ON workspace_collaboration_database_views(database_id,state,position,id);

CREATE TABLE IF NOT EXISTS workspace_collaboration_database_templates (
  id TEXT PRIMARY KEY,
  database_id TEXT NOT NULL,
  name TEXT NOT NULL,
  body_json TEXT NOT NULL CHECK(json_valid(body_json)=1),
  default_values_json TEXT NOT NULL CHECK(json_valid(default_values_json)=1),
  state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','archived')),
  row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  archived_at INTEGER,
  UNIQUE(database_id,id),
  CHECK(
    json_type(body_json,'$')='array' AND
    json_type(default_values_json,'$')='object' AND
    ((state='active' AND archived_at IS NULL) OR
     (state='archived' AND archived_at IS NOT NULL))
  ),
  FOREIGN KEY(database_id) REFERENCES workspace_collaboration_databases(id)
    ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_collab_database_templates_active
  ON workspace_collaboration_database_templates(database_id,state,created_at,id);

CREATE TABLE IF NOT EXISTS workspace_collaboration_database_schema_commands (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  database_id TEXT NOT NULL,
  actor_subject_id TEXT NOT NULL,
  operation_id TEXT NOT NULL,
  command_kind TEXT NOT NULL CHECK(command_kind IN (
    'database.create',
    'database.property.create','database.property.update',
    'database.view.create','database.view.update','database.view.archive',
    'database.template.create','database.template.update','database.template.archive'
  )),
  target_id TEXT NOT NULL,
  expected_document_sequence INTEGER NOT NULL CHECK(expected_document_sequence >= 1),
  expected_target_version INTEGER,
  result_target_version INTEGER NOT NULL CHECK(result_target_version >= 1),
  payload_json TEXT NOT NULL CHECK(json_valid(payload_json)=1),
  result_json TEXT NOT NULL CHECK(json_valid(result_json)=1),
  document_sequence INTEGER NOT NULL CHECK(document_sequence >= 1),
  workspace_epoch INTEGER NOT NULL CHECK(workspace_epoch >= 1),
  document_epoch INTEGER NOT NULL CHECK(document_epoch >= 1),
  created_at INTEGER NOT NULL,
  UNIQUE(document_id,document_sequence),
  UNIQUE(actor_subject_id,document_id,operation_id),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_documents(id)
    ON DELETE RESTRICT,
  FOREIGN KEY(database_id) REFERENCES workspace_collaboration_databases(id)
    DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS idx_collab_database_schema_commands_replay
  ON workspace_collaboration_database_schema_commands(document_id,document_sequence);

CREATE TRIGGER IF NOT EXISTS trg_collab_database_property_shape_insert
BEFORE INSERT ON workspace_collaboration_database_properties
WHEN CASE
  WHEN NEW.property_type NOT IN (
    'title','text','number','checkbox','date','select','multi_select','status',
    'person','files','url','email','phone','relation','rollup','formula',
    'created_time','last_edited_time'
  ) THEN 1
  WHEN json_valid(NEW.config_json)<>1 OR json_type(NEW.config_json,'$')<>'object' THEN 1
  WHEN NEW.property_type='relation' THEN
    COALESCE(json_type(NEW.config_json,'$.target_database_id')<>'text',1)
  WHEN NEW.property_type='rollup' THEN
    COALESCE(json_type(NEW.config_json,'$.relation_property_id')<>'text',1) OR
    COALESCE(json_type(NEW.config_json,'$.target_property_id')<>'text',1) OR
    COALESCE(json_extract(NEW.config_json,'$.aggregation') NOT IN
      ('count','count_values','sum','average','min','max','show_unique'),1)
  WHEN NEW.property_type='formula' THEN
    COALESCE(json_type(NEW.config_json,'$.expression')<>'text',1)
  ELSE 0
END
BEGIN
  SELECT RAISE(ABORT, 'database property definition is invalid');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_database_property_shape_update
BEFORE UPDATE OF property_type,config_json ON workspace_collaboration_database_properties
WHEN CASE
  WHEN NEW.property_type NOT IN (
    'title','text','number','checkbox','date','select','multi_select','status',
    'person','files','url','email','phone','relation','rollup','formula',
    'created_time','last_edited_time'
  ) THEN 1
  WHEN json_valid(NEW.config_json)<>1 OR json_type(NEW.config_json,'$')<>'object' THEN 1
  WHEN NEW.property_type='relation' THEN
    COALESCE(json_type(NEW.config_json,'$.target_database_id')<>'text',1)
  WHEN NEW.property_type='rollup' THEN
    COALESCE(json_type(NEW.config_json,'$.relation_property_id')<>'text',1) OR
    COALESCE(json_type(NEW.config_json,'$.target_property_id')<>'text',1) OR
    COALESCE(json_extract(NEW.config_json,'$.aggregation') NOT IN
      ('count','count_values','sum','average','min','max','show_unique'),1)
  WHEN NEW.property_type='formula' THEN
    COALESCE(json_type(NEW.config_json,'$.expression')<>'text',1)
  ELSE 0
END
BEGIN
  SELECT RAISE(ABORT, 'database property definition is invalid');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_database_cell_json_insert
BEFORE INSERT ON workspace_collaboration_database_cells
WHEN json_valid(NEW.value_json)<>1
BEGIN
  SELECT RAISE(ABORT, 'database cell value must be canonical JSON');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_database_cell_json_update
BEFORE UPDATE OF value_json ON workspace_collaboration_database_cells
WHEN json_valid(NEW.value_json)<>1
BEGIN
  SELECT RAISE(ABORT, 'database cell value must be canonical JSON');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_database_views_update_guard
BEFORE UPDATE ON workspace_collaboration_database_views
WHEN NEW.id<>OLD.id OR NEW.database_id<>OLD.database_id OR
     NEW.created_at<>OLD.created_at OR OLD.state='archived' OR
     NEW.row_version<>OLD.row_version+1 OR NEW.updated_at<OLD.updated_at OR
     NOT ((OLD.state='active' AND NEW.state IN ('active','archived')))
BEGIN
  SELECT RAISE(ABORT, 'database view transition is invalid');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_database_views_no_delete
BEFORE DELETE ON workspace_collaboration_database_views
BEGIN
  SELECT RAISE(ABORT, 'database view cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_database_templates_update_guard
BEFORE UPDATE ON workspace_collaboration_database_templates
WHEN NEW.id<>OLD.id OR NEW.database_id<>OLD.database_id OR
     NEW.created_at<>OLD.created_at OR OLD.state='archived' OR
     NEW.row_version<>OLD.row_version+1 OR NEW.updated_at<OLD.updated_at OR
     NOT ((OLD.state='active' AND NEW.state IN ('active','archived')))
BEGIN
  SELECT RAISE(ABORT, 'database template transition is invalid');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_database_templates_no_delete
BEFORE DELETE ON workspace_collaboration_database_templates
BEGIN
  SELECT RAISE(ABORT, 'database template cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_database_schema_commands_immutable
BEFORE UPDATE ON workspace_collaboration_database_schema_commands
BEGIN
  SELECT RAISE(ABORT, 'database schema command ledger is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_database_schema_commands_no_delete
BEFORE DELETE ON workspace_collaboration_database_schema_commands
BEGIN
  SELECT RAISE(ABORT, 'database schema command ledger cannot be deleted');
END;
"""

SOURCE_SQL = schema_2406.TARGET_SQL
TARGET_SQL = SOURCE_SQL + DATABASE_FEATURE_SCHEMA_SQL
TARGET_CHECKSUM = "sha256:f9b948c8de93fe7167c6528e71141b360bae7b01b5e9e288a432b1db29e3deb9"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:aecb144698f4a965838e9012d0313c789400d5bf72146c9bf4aa774fbd30fa40"


if (
    schema_2406.TARGET_VERSION,
    schema_2406.TARGET_NAME,
    schema_2406.TARGET_CHECKSUM,
    schema_2406.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 2406 database-feature source contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 2406 database-feature source DDL drifted")

SOURCE_OBJECTS, _source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = migration_core._schema_contract(TARGET_SQL)
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 2406 database-feature source objects drifted")
if "TO_BE_FROZEN" not in TARGET_CHECKSUM:
    if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
        raise RuntimeError("frozen database-feature target DDL drifted")
    if _target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("frozen database-feature target objects drifted")


MigrationRefused = migration_core.MigrationRefused


def _contract() -> migration_core._MigrationContract:
    if "TO_BE_FROZEN" in TARGET_CHECKSUM or "TO_BE_FROZEN" in TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("database-feature migration fingerprints are not frozen")
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
        DATABASE_FEATURE_SCHEMA_SQL,
        "code_assist.notion_workspace_database_features_migration.v1",
        (
            "install_adopted_database_view_and_template_authority",
            "install_relation_rollup_formula_property_guards",
            "install_immutable_database_schema_command_ledger",
            "replace_migration_marker",
        ),
        "2026082406",
        "2026082407",
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
    "DATABASE_FEATURE_SCHEMA_SQL",
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
