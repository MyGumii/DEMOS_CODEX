"""Guarded 2026082402 -> 2026082403 collaborative-IAM migration.

The extension adds the one page-access grant ledger required by I0-P1.  It is
additive and forward-only: the 2026082402 durable transport tables and every
historical direct-subject grant remain untouched.  Applying the migration is an
explicit operator action that requires the exact dry-run checksum, an offline
backup, and a quiesced catalog.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Callable

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_realtime_transport_migration as schema_2402


SOURCE_VERSION = 2026082402
SOURCE_NAME = "notion_workspace_v2_durable_realtime_transport"
SOURCE_CHECKSUM = "sha256:76377bdee7954f9d03d27dcf56dcf052799547d65d4e6ba21f117455e085baba"
SOURCE_ACTUAL_SCHEMA_SHA256 = "sha256:8f3bb03c8cecef84f9c4343234743bcd57c9264a293699d89576becb56c966c9"
TARGET_VERSION = 2026082403
TARGET_NAME = "notion_workspace_v2_collaborative_iam"


IAM_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS workspace_collaboration_access_grants (
  workspace_id TEXT NOT NULL,
  document_id TEXT NOT NULL,
  principal_type TEXT NOT NULL
    CHECK(principal_type IN ('subject','group','teamspace')),
  principal_id TEXT NOT NULL,
  role TEXT NOT NULL
    CHECK(role IN ('view','comment','edit_content','edit','full_access')),
  inherit_to_descendants INTEGER NOT NULL DEFAULT 0
    CHECK(inherit_to_descendants IN (0,1)),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  grant_epoch INTEGER NOT NULL DEFAULT 1 CHECK(grant_epoch >= 1),
  granted_by_subject_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  revoked_at INTEGER,
  CHECK(
    (active=1 AND revoked_at IS NULL) OR
    (active=0 AND revoked_at IS NOT NULL)
  ),
  PRIMARY KEY(document_id,principal_type,principal_id),
  FOREIGN KEY(workspace_id,document_id)
    REFERENCES workspace_collaboration_documents(workspace_id,id)
    ON DELETE CASCADE
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_collab_access_grants_principal
  ON workspace_collaboration_access_grants(
    workspace_id,principal_type,principal_id,active,document_id
  );
CREATE INDEX IF NOT EXISTS idx_collab_access_grants_document
  ON workspace_collaboration_access_grants(
    workspace_id,document_id,active,inherit_to_descendants
  );

CREATE TRIGGER IF NOT EXISTS trg_collab_access_subject_insert
BEFORE INSERT ON workspace_collaboration_access_grants
WHEN NEW.active=1 AND NEW.principal_type='subject' AND NOT EXISTS (
  SELECT 1 FROM workspace_memberships m
  WHERE m.workspace_id=NEW.workspace_id AND m.subject_id=NEW.principal_id
    AND m.revoked_at IS NULL
  UNION ALL
  SELECT 1 FROM workspace_guests g
  WHERE g.workspace_id=NEW.workspace_id AND g.subject_id=NEW.principal_id
    AND g.revoked_at IS NULL
)
BEGIN
  SELECT RAISE(ABORT, 'active subject grant requires active member or guest');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_access_subject_update
BEFORE UPDATE OF workspace_id,principal_type,principal_id,active
ON workspace_collaboration_access_grants
WHEN NEW.active=1 AND NEW.principal_type='subject' AND NOT EXISTS (
  SELECT 1 FROM workspace_memberships m
  WHERE m.workspace_id=NEW.workspace_id AND m.subject_id=NEW.principal_id
    AND m.revoked_at IS NULL
  UNION ALL
  SELECT 1 FROM workspace_guests g
  WHERE g.workspace_id=NEW.workspace_id AND g.subject_id=NEW.principal_id
    AND g.revoked_at IS NULL
)
BEGIN
  SELECT RAISE(ABORT, 'active subject grant requires active member or guest');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_access_group_insert
BEFORE INSERT ON workspace_collaboration_access_grants
WHEN NEW.active=1 AND NEW.principal_type='group' AND NOT EXISTS (
  SELECT 1 FROM workspace_groups g
  WHERE g.workspace_id=NEW.workspace_id AND g.id=NEW.principal_id
)
BEGIN
  SELECT RAISE(ABORT, 'group grant requires same-workspace group');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_access_group_update
BEFORE UPDATE OF workspace_id,principal_type,principal_id,active
ON workspace_collaboration_access_grants
WHEN NEW.active=1 AND NEW.principal_type='group' AND NOT EXISTS (
  SELECT 1 FROM workspace_groups g
  WHERE g.workspace_id=NEW.workspace_id AND g.id=NEW.principal_id
)
BEGIN
  SELECT RAISE(ABORT, 'group grant requires same-workspace group');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_access_teamspace_insert
BEFORE INSERT ON workspace_collaboration_access_grants
WHEN NEW.active=1 AND NEW.principal_type='teamspace' AND NOT EXISTS (
  SELECT 1 FROM teamspaces t
  WHERE t.workspace_id=NEW.workspace_id AND t.id=NEW.principal_id
    AND t.archived_at IS NULL
)
BEGIN
  SELECT RAISE(ABORT, 'teamspace grant requires active same-workspace teamspace');
END;

CREATE TRIGGER IF NOT EXISTS trg_collab_access_teamspace_update
BEFORE UPDATE OF workspace_id,principal_type,principal_id,active
ON workspace_collaboration_access_grants
WHEN NEW.active=1 AND NEW.principal_type='teamspace' AND NOT EXISTS (
  SELECT 1 FROM teamspaces t
  WHERE t.workspace_id=NEW.workspace_id AND t.id=NEW.principal_id
    AND t.archived_at IS NULL
)
BEGIN
  SELECT RAISE(ABORT, 'teamspace grant requires active same-workspace teamspace');
END;
"""

SOURCE_SQL = schema_2402.TARGET_SQL
TARGET_SQL = SOURCE_SQL + IAM_SCHEMA_SQL

# Frozen immediately after the additive DDL and sqlite_master contract are
# derived.  Placeholder guards make an accidentally unfinished migration
# impossible to apply.
TARGET_CHECKSUM = "sha256:4a89feb59410077be42d496983523d734879b8e986b85c69d5eacfe78de58aaf"
TARGET_ACTUAL_SCHEMA_SHA256 = "sha256:704503c90d97856fbeb20542ff9bd20324d55f71907e2d966ca9ec574514954c"


if (
    schema_2402.TARGET_VERSION,
    schema_2402.TARGET_NAME,
    schema_2402.TARGET_CHECKSUM,
    schema_2402.TARGET_ACTUAL_SCHEMA_SHA256,
) != (
    SOURCE_VERSION,
    SOURCE_NAME,
    SOURCE_CHECKSUM,
    SOURCE_ACTUAL_SCHEMA_SHA256,
):
    raise RuntimeError("frozen 2402 IAM source contract drifted")
if "sha256:" + hashlib.sha256(SOURCE_SQL.encode("utf-8")).hexdigest() != SOURCE_CHECKSUM:
    raise RuntimeError("frozen 2402 IAM source DDL drifted")

SOURCE_OBJECTS, _source_actual = migration_core._schema_contract(SOURCE_SQL)
TARGET_OBJECTS, _target_actual = migration_core._schema_contract(TARGET_SQL)
if _source_actual != SOURCE_ACTUAL_SCHEMA_SHA256:
    raise RuntimeError("frozen 2402 IAM source objects drifted")
if "TO_BE_FROZEN" not in TARGET_CHECKSUM:
    if "sha256:" + hashlib.sha256(TARGET_SQL.encode("utf-8")).hexdigest() != TARGET_CHECKSUM:
        raise RuntimeError("frozen IAM target DDL drifted")
    if _target_actual != TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("frozen IAM target objects drifted")


MigrationRefused = migration_core.MigrationRefused


def _contract() -> migration_core._MigrationContract:
    if "TO_BE_FROZEN" in TARGET_CHECKSUM or "TO_BE_FROZEN" in TARGET_ACTUAL_SCHEMA_SHA256:
        raise RuntimeError("IAM migration fingerprints are not frozen")
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
        IAM_SCHEMA_SQL,
        "code_assist.notion_workspace_iam_migration.v1",
        (
            "install_direct_inherited_subject_group_and_teamspace_grants",
            "install_principal_and_workspace_integrity_guards",
            "install_access_resolution_indexes",
            "replace_migration_marker",
        ),
        "2026082402",
        "2026082403",
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
    "IAM_SCHEMA_SQL",
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
