"""Offline 2426 -> 2427 per-database default template selection.

An absent row means the database creates empty items by default. The table is
empty on installation, preserving every existing database's behavior.
"""
from __future__ import annotations

import hashlib

from code_assist_v1 import notion_workspace_database_item_page_layout_migration as source
from code_assist_v1 import notion_workspace_healing_migration as core


SOURCE_VERSION = source.TARGET_VERSION
SOURCE_NAME = source.TARGET_NAME
SOURCE_CHECKSUM = source.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = source.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082427
TARGET_NAME = "notion_workspace_v2_database_template_default"

DATABASE_TEMPLATE_DEFAULT_SCHEMA_SQL = """
CREATE TABLE workspace_collaboration_database_template_defaults (
  database_id TEXT PRIMARY KEY CHECK(length(database_id)>0),
  template_id TEXT CHECK(template_id IS NULL OR length(template_id)>0),
  row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version>=1),
  updated_by_subject_id TEXT NOT NULL CHECK(length(updated_by_subject_id)>0),
  created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
  updated_at INTEGER NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=0),
  FOREIGN KEY(database_id) REFERENCES workspace_collaboration_databases(id) ON DELETE RESTRICT,
  FOREIGN KEY(template_id) REFERENCES workspace_collaboration_database_templates(id) ON DELETE RESTRICT
);
"""

SOURCE_SQL = source.TARGET_SQL
TARGET_SQL = SOURCE_SQL + DATABASE_TEMPLATE_DEFAULT_SCHEMA_SQL
SOURCE_OBJECTS, _source_actual = core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = core._schema_contract(TARGET_SQL)
TARGET_CHECKSUM = "sha256:6884ba309f7437efc57778d0d351321dcb5e4b65364e2f064548550b4b7e249f"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:ce1313959a2ae7b0b513599004730cb2c2a7573c8cf02664aae7202ae560b460"
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("database template default source schema drifted")
if "TO_BE_FROZEN" not in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256) and (
    "sha256:" + hashlib.sha256(TARGET_SQL.encode()).hexdigest() != TARGET_CHECKSUM
    or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
):
    raise RuntimeError("database template default target schema drifted")

MigrationRefused = core.MigrationRefused


def _contract():
    if "TO_BE_FROZEN" in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256):
        raise RuntimeError("database template default migration fingerprints are not frozen")
    return core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256,
        SOURCE_OBJECTS, TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM,
        TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS,
        DATABASE_TEMPLATE_DEFAULT_SCHEMA_SQL,
        "code_assist.notion_workspace_database_template_default_migration.v1",
        ("install_database_template_default", "replace_migration_marker"),
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
