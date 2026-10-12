"""Test-process adapter for exercising a 2429/2430 catalog in the full browser app.

The production runtime intentionally rejects 2428/2429/2430. This adapter is loaded
only by an isolated browser fixture after its synthetic catalog is migrated.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

from code_assist_v1 import notion_workspace_database_upload_scope_migration as migration
from code_assist_v1 import notion_workspace_direct_database_favorite_migration as favorite_migration
from code_assist_v1 import notion_workspace_store as nws
from code_assist_v1.notion_collaboration import iam_runtime
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.workspace_image_authority import WorkspaceImageAuthority


class StagedDatabaseWorkspaceStore(iam_runtime.CollaborativeWorkspaceStore):
    def __init__(self, db_path, *, user_data_root, identity_directory,
                 base_capabilities, target_version, favorite_enabled):
        nws.WorkspaceStore.__init__(self, db_path)
        self.user_data_root = Path(user_data_root).resolve(strict=True)
        self.identity_directory = identity_directory
        self.iam = WorkspaceImageAuthority.open(
            self.db_path, self.user_data_root, identity_directory)
        self._base_capabilities = dict(base_capabilities)
        self._target_version = target_version
        self._favorite_enabled = favorite_enabled

    def capabilities(self):
        return {**self._base_capabilities,
                "schema_version": self._target_version,
                "independent_database_search": True,
                "direct_database_favorites": self._favorite_enabled}


def install_staged_database_runtime():
    """Bind exact-marker 2429/2430 only inside the synthetic app subprocess."""
    base_capabilities = json.loads(os.environ["CODE_ASSIST_TEST_STAGED_BASE_CAPABILITIES"])
    original_target = iam_runtime._current_target
    favorite_enabled = os.environ.get("CODE_ASSIST_TEST_STAGED_DIRECT_DATABASE_FAVORITES") == "1"
    target = favorite_migration if favorite_enabled else migration
    exact = (target.TARGET_VERSION, target.TARGET_NAME,
             target.TARGET_CHECKSUM, target.TARGET_ACTUAL_SCHEMA_SHA256)

    def staged_target(con):
        row = con.execute(
            "SELECT version,name,checksum,actual_schema_sha256 "
            "FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if row is not None and tuple(row) == exact:
            return exact
        return original_target(con)

    def open_staged(active_human_subject_ids, environ=None):
        catalog, root = iam_runtime.configured_paths(environ)
        with sqlite3.connect(catalog) as con:
            if staged_target(con) != exact:
                raise AuthorityUnavailable("staged database catalog marker mismatch")
        return StagedDatabaseWorkspaceStore(
            catalog, user_data_root=root,
            identity_directory=InMemoryIdentityDirectory(active_human_subject_ids),
            base_capabilities=base_capabilities,
            target_version=target.TARGET_VERSION,
            favorite_enabled=favorite_enabled,
        )

    iam_runtime._current_target = staged_target
    iam_runtime.open_runtime_store = open_staged


def install_staged_gateway_runtime():
    """Accept the exact synthetic database marker in this test gateway only."""
    from code_assist_v1.notion_collaboration import editing_authority

    favorite_enabled = os.environ.get("CODE_ASSIST_TEST_STAGED_DIRECT_DATABASE_FAVORITES") == "1"
    target = favorite_migration if favorite_enabled else migration
    exact = (target.TARGET_VERSION, target.TARGET_NAME,
             target.TARGET_CHECKSUM, target.TARGET_ACTUAL_SCHEMA_SHA256)
    original_marker = editing_authority._marker_current

    def staged_marker(con):
        row = con.execute(
            "SELECT version,name,checksum,actual_schema_sha256 "
            "FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return tuple(row) == exact if row is not None and row[0] == exact[0] \
            else original_marker(con)

    editing_authority._marker_current = staged_marker
