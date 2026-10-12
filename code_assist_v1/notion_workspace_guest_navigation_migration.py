"""Explicit 2411 -> 2412 guest-owned sidebar preferences and favorites."""
from __future__ import annotations

import hashlib

from code_assist_v1 import notion_workspace_icon_image_migration as source
from code_assist_v1 import notion_workspace_healing_migration as core

SOURCE_VERSION = source.TARGET_VERSION
SOURCE_NAME = source.TARGET_NAME
SOURCE_CHECKSUM = source.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = source.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082412
TARGET_NAME = "notion_workspace_v2_guest_navigation"
GUEST_NAVIGATION_SCHEMA_SQL = """
CREATE TABLE workspace_guest_navigation_states (
  workspace_id TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  preference_version INTEGER NOT NULL CHECK(typeof(preference_version)='integer' AND preference_version BETWEEN 0 AND 9007199254740991),
  favorite_version INTEGER NOT NULL CHECK(typeof(favorite_version)='integer' AND favorite_version BETWEEN 0 AND 9007199254740991),
  sidebar_width_px INTEGER NOT NULL CHECK(typeof(sidebar_width_px)='integer' AND sidebar_width_px BETWEEN 180 AND 600),
  sidebar_collapsed INTEGER NOT NULL CHECK(typeof(sidebar_collapsed)='integer' AND sidebar_collapsed IN (0,1)),
  sections_json TEXT NOT NULL CHECK(length(sections_json)<=16384 AND json_valid(sections_json) AND json_type(sections_json)='array'),
  favorites_json TEXT NOT NULL CHECK(length(favorites_json)<=262144 AND json_valid(favorites_json) AND json_type(favorites_json)='array'),
  updated_at INTEGER NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=0),
  PRIMARY KEY(workspace_id,subject_id),
  FOREIGN KEY(workspace_id,subject_id) REFERENCES workspace_guests(workspace_id,subject_id) ON DELETE RESTRICT
);
"""
SOURCE_SQL = source.TARGET_SQL
TARGET_SQL = SOURCE_SQL + GUEST_NAVIGATION_SCHEMA_SQL
SOURCE_OBJECTS, _source_actual = core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = core._schema_contract(TARGET_SQL)
TARGET_CHECKSUM = "sha256:b1932d7e8f322cb02621aa438c3104799934d07a1250488b5485db5740f93eea"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:7083e8ca25c0e766081ef989ab1d429322c9c5eb7626c9749cc0ffcf00d572aa"
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("guest navigation source schema drifted")
if "TO_BE_FROZEN" not in TARGET_CHECKSUM and (
    "sha256:" + hashlib.sha256(TARGET_SQL.encode()).hexdigest() != TARGET_CHECKSUM
    or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
):
    raise RuntimeError("guest navigation target schema drifted")
MigrationRefused = core.MigrationRefused


def _contract():
    if "TO_BE_FROZEN" in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256):
        raise RuntimeError("guest navigation fingerprints are not frozen")
    return core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256, SOURCE_OBJECTS,
        TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS,
        GUEST_NAVIGATION_SCHEMA_SQL, "code_assist.notion_workspace_guest_navigation_migration.v1",
        ("install_guest_navigation", "replace_migration_marker"), str(SOURCE_VERSION), str(TARGET_VERSION),
    )


def build_plan(catalog_path, user_data_root):
    return core.build_plan(catalog_path, user_data_root, _contract=_contract())


def apply_plan(catalog_path, user_data_root, *, exact_plan_checksum, backup_confirmed,
               catalog_quiesced_confirmed, backup_path, fault_hook=None):
    return core.apply_plan(catalog_path, user_data_root, exact_plan_checksum=exact_plan_checksum,
        backup_confirmed=backup_confirmed, catalog_quiesced_confirmed=catalog_quiesced_confirmed,
        backup_path=backup_path, fault_hook=fault_hook, _contract=_contract())
