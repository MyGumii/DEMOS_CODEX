"""Search/editing/file authority on the exact additive workspace image schema."""
from __future__ import annotations

import sqlite3

from code_assist_v1 import notion_workspace_icon_image_migration as migration
from code_assist_v1 import notion_workspace_guest_navigation_migration as guest_navigation_migration
from code_assist_v1 import notion_workspace_page_archive_migration as archive_migration
from code_assist_v1 import notion_workspace_native_page_migration as native_page_migration
from code_assist_v1 import notion_workspace_member_navigation_migration as member_navigation_migration
from code_assist_v1 import notion_workspace_file_copy_migration as file_copy_migration
from code_assist_v1 import notion_workspace_native_team_migration as team_migration
from code_assist_v1 import notion_workspace_row_body_migration as row_body_migration
from code_assist_v1 import notion_workspace_row_appearance_migration as row_appearance_migration
from code_assist_v1 import notion_workspace_same_document_file_copy_migration as same_file_migration
from code_assist_v1 import notion_workspace_database_activity_migration as activity_migration
from code_assist_v1 import notion_workspace_database_trash_migration as database_trash_migration
from code_assist_v1 import notion_workspace_database_row_trash_migration as row_trash_migration
from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement_migration
from code_assist_v1 import notion_workspace_custom_emoji_migration as custom_emoji_migration
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as item_layout_migration
from code_assist_v1 import notion_workspace_database_template_default_migration as template_default_migration
from code_assist_v1 import notion_workspace_database_sharing_migration as database_sharing_migration
from code_assist_v1 import notion_workspace_database_upload_scope_migration as upload_scope_migration
from code_assist_v1 import notion_workspace_direct_database_favorite_migration as direct_favorite_migration
from code_assist_v1 import notion_workspace_healing_migration as core
from .canonical_authority import AuthorityUnavailable
from .search_authority import CollaborativeSearchAuthority


def marker_current(con):
    row = con.execute("SELECT version,name,checksum,actual_schema_sha256 FROM workspace_schema_migrations "
                      "ORDER BY version DESC LIMIT 1").fetchone()
    return row is not None and tuple(row) in {
        (item.TARGET_VERSION, item.TARGET_NAME, item.TARGET_CHECKSUM, item.TARGET_ACTUAL_SCHEMA_SHA256)
        for item in (migration, guest_navigation_migration, archive_migration, native_page_migration, member_navigation_migration, file_copy_migration, team_migration, row_body_migration, row_appearance_migration, same_file_migration, activity_migration, database_trash_migration, row_trash_migration, placement_migration, custom_emoji_migration, item_layout_migration, template_default_migration, database_sharing_migration, upload_scope_migration, direct_favorite_migration)
    }


class WorkspaceImageAuthority(CollaborativeSearchAuthority):
    @classmethod
    def open(cls, catalog_path, user_data_root, identity_directory, **kwargs):
        catalog, root = core._resolved_target(catalog_path, user_data_root)
        authority = cls(catalog, root, identity_directory, **kwargs)
        con = authority._connect(query_only=True)
        con.close()
        authority._validate_storage()
        return authority

    def _connect(self, *, query_only):
        con = None
        try:
            con = sqlite3.connect(f"{self.catalog_path.as_uri()}?mode=ro" if query_only else self.catalog_path,
                                  uri=query_only, timeout=5.0, isolation_level=None)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=5000")
            if query_only:
                con.execute("PRAGMA query_only=ON")
            if not marker_current(con):
                raise AuthorityUnavailable("workspace image schema marker mismatch")
            return con
        except (OSError, sqlite3.Error, AuthorityUnavailable) as exc:
            if con is not None:
                con.close()
            raise AuthorityUnavailable("workspace image authority is unavailable") from exc
