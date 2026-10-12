"""Explicit, guarded 2410 -> 2411 workspace-owned icon images."""
from __future__ import annotations

import hashlib

from code_assist_v1 import notion_workspace_search_authority_migration as source
from code_assist_v1 import notion_workspace_healing_migration as core

SOURCE_VERSION = source.TARGET_VERSION
SOURCE_NAME = source.TARGET_NAME
SOURCE_CHECKSUM = source.TARGET_CHECKSUM
SOURCE_ACTUAL_SCHEMA_SHA256 = source.TARGET_ACTUAL_SCHEMA_SHA256
TARGET_VERSION = 2026082411
TARGET_NAME = "notion_workspace_v2_workspace_icon_images"
ICON_IMAGE_SCHEMA_SQL = """
CREATE TABLE workspace_icon_images (
  workspace_id TEXT PRIMARY KEY,
  image_id TEXT NOT NULL UNIQUE,
  mime_type TEXT NOT NULL CHECK(mime_type IN ('image/png','image/jpeg','image/webp')),
  size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 2097152),
  content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=71 AND substr(content_sha256,1,7)='sha256:'),
  image_bytes BLOB NOT NULL CHECK(typeof(image_bytes)='blob' AND length(image_bytes)=size_bytes),
  uploader_subject_id TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE RESTRICT
);
"""
SOURCE_SQL = source.TARGET_SQL
TARGET_SQL = SOURCE_SQL + ICON_IMAGE_SCHEMA_SQL
SOURCE_OBJECTS, _source_actual = core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = core._schema_contract(TARGET_SQL)
TARGET_CHECKSUM = "sha256:3935539ed7d15cc67ccb8d51a2aef3ef72c451f55699cc93aecc637ebd0f1741"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:630fc3f3b8cd9e973e624ddf132dc5155046b0feb47d028432110bd5ff5af61e"

if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("workspace image source schema drifted")
if "TO_BE_FROZEN" not in TARGET_CHECKSUM and (
    "sha256:" + hashlib.sha256(TARGET_SQL.encode()).hexdigest() != TARGET_CHECKSUM
    or _target_actual != TARGET_ACTUAL_SCHEMA_SHA256
):
    raise RuntimeError("workspace image target schema drifted")

MigrationRefused = core.MigrationRefused


def _contract():
    if "TO_BE_FROZEN" in (TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256):
        raise RuntimeError("workspace image migration fingerprints are not frozen")
    return core._MigrationContract(
        SOURCE_VERSION, SOURCE_NAME, SOURCE_CHECKSUM, SOURCE_ACTUAL_SCHEMA_SHA256, SOURCE_OBJECTS,
        TARGET_VERSION, TARGET_NAME, TARGET_CHECKSUM, TARGET_ACTUAL_SCHEMA_SHA256, TARGET_OBJECTS,
        ICON_IMAGE_SCHEMA_SQL, "code_assist.notion_workspace_icon_image_migration.v1",
        ("install_workspace_owned_icon_images", "replace_migration_marker"),
        str(SOURCE_VERSION), str(TARGET_VERSION),
    )


def build_plan(catalog_path, user_data_root):
    return core.build_plan(catalog_path, user_data_root, _contract=_contract())


def apply_plan(catalog_path, user_data_root, *, exact_plan_checksum, backup_confirmed,
               catalog_quiesced_confirmed, backup_path, fault_hook=None):
    return core.apply_plan(catalog_path, user_data_root, exact_plan_checksum=exact_plan_checksum,
        backup_confirmed=backup_confirmed, catalog_quiesced_confirmed=catalog_quiesced_confirmed,
        backup_path=backup_path, fault_hook=fault_hook, _contract=_contract())
