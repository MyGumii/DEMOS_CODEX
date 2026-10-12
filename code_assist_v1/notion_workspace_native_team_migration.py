"""Offline 2416 -> 2417 explicit native team-root placement."""
from __future__ import annotations

import hashlib

from code_assist_v1 import notion_workspace_file_copy_migration as source
from code_assist_v1 import notion_workspace_healing_migration as core

SOURCE_VERSION = source.TARGET_VERSION
SOURCE_NAME = source.TARGET_NAME
SOURCE_CHECKSUM = source.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = source.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082417
TARGET_NAME = "notion_workspace_v2_native_team_roots"
NATIVE_TEAM_SCHEMA_SQL = """
CREATE TABLE workspace_collaboration_native_team_roots (
  document_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  teamspace_id TEXT NOT NULL,
  created_by_subject_id TEXT NOT NULL,
  created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
  FOREIGN KEY(document_id) REFERENCES workspace_collaboration_native_pages(document_id) ON DELETE RESTRICT,
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT,
  FOREIGN KEY(teamspace_id) REFERENCES teamspaces(id) ON DELETE RESTRICT
);
CREATE INDEX idx_collab_native_team_roots_team
  ON workspace_collaboration_native_team_roots(workspace_id,teamspace_id,document_id);
CREATE TRIGGER trg_collab_native_team_root_insert
BEFORE INSERT ON workspace_collaboration_native_team_roots
WHEN NOT EXISTS (
  SELECT 1 FROM workspace_collaboration_documents d JOIN teamspaces t ON t.workspace_id=d.workspace_id
  WHERE d.id=NEW.document_id AND d.workspace_id=NEW.workspace_id AND d.parent_document_id IS NULL
    AND t.id=NEW.teamspace_id AND t.archived_at IS NULL
)
BEGIN SELECT RAISE(ABORT, 'native team root requires same-workspace root and active team'); END;
CREATE TRIGGER trg_collab_native_team_root_no_update
BEFORE UPDATE ON workspace_collaboration_native_team_roots
BEGIN SELECT RAISE(ABORT, 'replace native team placement through the service'); END;

"""
SOURCE_SQL = source.TARGET_SQL
TARGET_SQL = SOURCE_SQL + NATIVE_TEAM_SCHEMA_SQL
SOURCE_OBJECTS, _source_actual = core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = core._schema_contract(TARGET_SQL)
TARGET_CHECKSUM = "sha256:f6964e87ac0b777f2c20cd93ecaeb0cf5ef98a89f621defe60667eb855e65b9a"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:003c54da162bcd5b7e8567f33cbf2361fc5b1f0724b10e65563da894f33680f4"
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("native team source schema drifted")
if "TO_BE_FROZEN" not in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256) and (
    "sha256:" + hashlib.sha256(TARGET_SQL.encode()).hexdigest() != TARGET_CHECKSUM
    or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
):
    raise RuntimeError("native team target schema drifted")
MigrationRefused = core.MigrationRefused


def _contract():
    if "TO_BE_FROZEN" in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256):
        raise RuntimeError("native team fingerprints are not frozen")
    return core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256, SOURCE_OBJECTS,
        TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS,
        NATIVE_TEAM_SCHEMA_SQL, "code_assist.notion_workspace_native_team_migration.v1",
        ("install_native_team_roots", "replace_migration_marker"), str(SOURCE_VERSION), str(TARGET_VERSION),
    )


def build_plan(catalog_path, user_data_root):
    return core.build_plan(catalog_path, user_data_root, _contract=_contract())


def apply_plan(catalog_path, user_data_root, *, exact_plan_checksum, backup_confirmed,
               catalog_quiesced_confirmed, backup_path, fault_hook=None):
    return core.apply_plan(catalog_path, user_data_root, exact_plan_checksum=exact_plan_checksum,
        backup_confirmed=backup_confirmed, catalog_quiesced_confirmed=catalog_quiesced_confirmed,
        backup_path=backup_path, fault_hook=fault_hook, _contract=_contract())
