"""Offline 2424 -> 2425 registry for reusable workspace custom emoji.

Installing the registry does not expose an upload route or change existing page
icons. Images are stored separately from the single workspace avatar slot.
"""
from __future__ import annotations

import hashlib

from code_assist_v1 import notion_workspace_database_row_page_placement_migration as source
from code_assist_v1 import notion_workspace_healing_migration as core


SOURCE_VERSION = source.TARGET_VERSION
SOURCE_NAME = source.TARGET_NAME
SOURCE_CHECKSUM = source.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = source.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082425
TARGET_NAME = "notion_workspace_v2_custom_emoji_registry"

CUSTOM_EMOJI_SCHEMA_SQL = """
CREATE TABLE workspace_custom_emojis (
  id TEXT PRIMARY KEY CHECK(length(id)=43 AND substr(id,1,3)='we_'),
  workspace_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 64),
  name_key TEXT NOT NULL CHECK(length(name_key) BETWEEN 1 AND 64),
  mime_type TEXT NOT NULL CHECK(mime_type IN ('image/png','image/jpeg','image/webp')),
  size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 2097152),
  content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=71 AND substr(content_sha256,1,7)='sha256:'),
  image_bytes BLOB NOT NULL CHECK(typeof(image_bytes)='blob' AND length(image_bytes)=size_bytes),
  uploader_subject_id TEXT NOT NULL CHECK(length(uploader_subject_id)>0),
  created_at INTEGER NOT NULL CHECK(typeof(created_at)='integer' AND created_at>=0),
  UNIQUE(workspace_id,name_key),
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT
);
CREATE INDEX idx_workspace_custom_emojis_catalog
  ON workspace_custom_emojis(workspace_id,name_key,id);
"""

SOURCE_SQL = source.TARGET_SQL
TARGET_SQL = SOURCE_SQL + CUSTOM_EMOJI_SCHEMA_SQL
SOURCE_OBJECTS, _source_actual = core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = core._schema_contract(TARGET_SQL)
TARGET_CHECKSUM = "sha256:e51f8b345378d34a8ff7cf2034d1783987a9196ed1d872d09fa7b61768d78cf9"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:72fa5afd373cd2e4be32deaf05cbe315ba7871f70d9873b713d79aa01b019e2f"
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("custom emoji source schema drifted")
if "TO_BE_FROZEN" not in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256) and (
    "sha256:" + hashlib.sha256(TARGET_SQL.encode()).hexdigest() != TARGET_CHECKSUM
    or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
):
    raise RuntimeError("custom emoji target schema drifted")

MigrationRefused = core.MigrationRefused


def _contract():
    if "TO_BE_FROZEN" in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256):
        raise RuntimeError("custom emoji migration fingerprints are not frozen")
    return core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256,
        SOURCE_OBJECTS, TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM,
        TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS,
        CUSTOM_EMOJI_SCHEMA_SQL,
        "code_assist.notion_workspace_custom_emoji_migration.v1",
        ("install_workspace_custom_emoji_registry", "replace_migration_marker"),
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
