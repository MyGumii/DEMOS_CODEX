"""Offline 2414 -> 2415 member navigation supporting legacy and native pages."""
from __future__ import annotations

import hashlib

from code_assist_v1 import notion_workspace_native_page_migration as source
from code_assist_v1 import notion_workspace_healing_migration as core

SOURCE_VERSION = source.TARGET_VERSION
SOURCE_NAME = source.TARGET_NAME
SOURCE_CHECKSUM = source.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = source.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082415
TARGET_NAME = "notion_workspace_v2_member_navigation"
MEMBER_NAVIGATION_SCHEMA_SQL = """
CREATE TABLE workspace_member_navigation_states (
  workspace_id TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  preference_version INTEGER NOT NULL CHECK(typeof(preference_version)='integer' AND preference_version BETWEEN 0 AND 9007199254740991),
  favorite_version INTEGER NOT NULL CHECK(typeof(favorite_version)='integer' AND favorite_version BETWEEN 0 AND 9007199254740991),
  sidebar_width_px INTEGER NOT NULL CHECK(typeof(sidebar_width_px)='integer' AND sidebar_width_px BETWEEN 180 AND 600),
  sidebar_collapsed INTEGER NOT NULL CHECK(typeof(sidebar_collapsed)='integer' AND sidebar_collapsed IN (0,1)),
  sections_json TEXT NOT NULL CHECK(length(sections_json)<=16384 AND json_valid(sections_json) AND json_type(sections_json)='array' AND json_array_length(sections_json)=5),
  favorites_json TEXT NOT NULL CHECK(length(favorites_json)<=262144 AND json_valid(favorites_json) AND json_type(favorites_json)='array' AND json_array_length(favorites_json)<=100),
  updated_at INTEGER NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=0),
  PRIMARY KEY(workspace_id,subject_id),
  FOREIGN KEY(workspace_id,subject_id) REFERENCES workspace_memberships(workspace_id,subject_id) ON DELETE RESTRICT
);
INSERT INTO workspace_member_navigation_states
  (workspace_id,subject_id,preference_version,favorite_version,sidebar_width_px,sidebar_collapsed,sections_json,favorites_json,updated_at)
SELECT s.workspace_id,s.subject_id,s.preference_version,s.favorite_version,s.sidebar_width_px,s.sidebar_collapsed,
  CASE WHEN NOT EXISTS (SELECT 1 FROM workspace_sidebar_section_preferences p WHERE p.workspace_id=s.workspace_id AND p.subject_id=s.subject_id)
    THEN '[{"section":"favorites","display_order":0,"hidden":false,"collapsed":false,"item_limit":10},{"section":"recents","display_order":1,"hidden":false,"collapsed":false,"item_limit":10},{"section":"teamspaces","display_order":2,"hidden":false,"collapsed":false,"item_limit":10},{"section":"shared","display_order":3,"hidden":false,"collapsed":false,"item_limit":10},{"section":"private","display_order":4,"hidden":false,"collapsed":false,"item_limit":10}]'
    ELSE (SELECT json_group_array(json_object('section',section_key,'display_order',display_order,
      'hidden',json(CASE hidden WHEN 1 THEN 'true' ELSE 'false' END),
      'collapsed',json(CASE collapsed WHEN 1 THEN 'true' ELSE 'false' END),'item_limit',item_limit))
      FROM (SELECT * FROM workspace_sidebar_section_preferences p WHERE p.workspace_id=s.workspace_id AND p.subject_id=s.subject_id ORDER BY display_order,section_key))
  END,
  (SELECT json_group_array(json_object('source_owner_subject_id',source_owner_subject_id,'source_page_id',source_page_id))
    FROM (SELECT * FROM workspace_page_favorites f WHERE f.workspace_id=s.workspace_id AND f.subject_id=s.subject_id ORDER BY favorite_ordinal,source_owner_subject_id,source_page_id)),
  s.updated_at
FROM workspace_navigation_states s;
"""
SOURCE_SQL = source.TARGET_SQL
TARGET_SQL = SOURCE_SQL + MEMBER_NAVIGATION_SCHEMA_SQL
SOURCE_OBJECTS, _source_actual = core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = core._schema_contract(TARGET_SQL)
TARGET_CHECKSUM = "sha256:32cbc5b811f384da7c87371388d28a0156c25c0ed8b211422e6e1ec9418837c0"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:8dca0d978713cbb659287ce5c23eca64e7ef1c157b47989caa329355b36ad6f2"
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("member navigation source schema drifted")
if "TO_BE_FROZEN" not in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256) and (
    "sha256:" + hashlib.sha256(TARGET_SQL.encode()).hexdigest() != TARGET_CHECKSUM
    or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
):
    raise RuntimeError("member navigation target schema drifted")
MigrationRefused = core.MigrationRefused


def _contract():
    if "TO_BE_FROZEN" in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256):
        raise RuntimeError("member navigation fingerprints are not frozen")
    return core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256, SOURCE_OBJECTS,
        TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS,
        MEMBER_NAVIGATION_SCHEMA_SQL, "code_assist.notion_workspace_member_navigation_migration.v1",
        ("install_member_navigation", "replace_migration_marker"), str(SOURCE_VERSION), str(TARGET_VERSION),
    )


def build_plan(catalog_path, user_data_root):
    return core.build_plan(catalog_path, user_data_root, _contract=_contract())


def apply_plan(catalog_path, user_data_root, *, exact_plan_checksum, backup_confirmed,
               catalog_quiesced_confirmed, backup_path, fault_hook=None):
    return core.apply_plan(catalog_path, user_data_root, exact_plan_checksum=exact_plan_checksum,
        backup_confirmed=backup_confirmed, catalog_quiesced_confirmed=catalog_quiesced_confirmed,
        backup_path=backup_path, fault_hook=fault_hook, _contract=_contract())
