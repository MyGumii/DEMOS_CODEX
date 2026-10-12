"""Offline 2417 -> 2418 canonical database item bodies; no runtime DDL."""
from __future__ import annotations

import hashlib

from code_assist_v1 import notion_workspace_native_team_migration as source
from code_assist_v1 import notion_workspace_healing_migration as core

SOURCE_VERSION = source.TARGET_VERSION
SOURCE_NAME = source.TARGET_NAME
SOURCE_CHECKSUM = source.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = source.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082418
TARGET_NAME = "notion_workspace_v2_database_row_bodies"
ROW_BODY_SCHEMA_SQL = """
CREATE TABLE workspace_collaboration_database_row_bodies (
  row_id TEXT PRIMARY KEY,
  blocks_json TEXT NOT NULL CHECK(json_valid(blocks_json) AND json_type(blocks_json)='array'),
  FOREIGN KEY(row_id) REFERENCES workspace_collaboration_database_rows(id) ON DELETE CASCADE
);

"""
SOURCE_SQL = source.TARGET_SQL
TARGET_SQL = SOURCE_SQL + ROW_BODY_SCHEMA_SQL
SOURCE_OBJECTS, _source_actual = core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = core._schema_contract(TARGET_SQL)
TARGET_CHECKSUM = 'sha256:041e0fa425b3312b47c860346f06edf1a0937ffd70f55048c4fd6eecce47e5d6'
TARGET_ACTUAL_SCHEMA_SHA256 = 'sha256:b9bbd67a44636badf76705855fffb7d1098b4f6a63683a6135dfc32308a3b09b'
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("database row body source schema drifted")
if "TO_BE_FROZEN" not in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256) and (
    "sha256:" + hashlib.sha256(TARGET_SQL.encode()).hexdigest() != TARGET_CHECKSUM
    or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
):
    raise RuntimeError("database row body target schema drifted")
MigrationRefused = core.MigrationRefused


def _contract():
    if "TO_BE_FROZEN" in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256):
        raise RuntimeError("database row body fingerprints are not frozen")
    return core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256, SOURCE_OBJECTS,
        TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS,
        ROW_BODY_SCHEMA_SQL, "code_assist.notion_workspace_row_body_migration.v1",
        ("install_database_row_bodies", "replace_migration_marker"), str(SOURCE_VERSION), str(TARGET_VERSION),
    )


def build_plan(catalog_path, user_data_root):
    return core.build_plan(catalog_path, user_data_root, _contract=_contract())


def apply_plan(catalog_path, user_data_root, *, exact_plan_checksum, backup_confirmed,
               catalog_quiesced_confirmed, backup_path, fault_hook=None):
    return core.apply_plan(catalog_path, user_data_root, exact_plan_checksum=exact_plan_checksum,
        backup_confirmed=backup_confirmed, catalog_quiesced_confirmed=catalog_quiesced_confirmed,
        backup_path=backup_path, fault_hook=fault_hook, _contract=_contract())
