"""Read the canonical placement of a database row at schema 2424."""

from __future__ import annotations

import sqlite3

from code_assist_v1 import notion_workspace_database_row_page_placement_migration as migration
from code_assist_v1 import notion_workspace_custom_emoji_migration as custom_emoji_migration
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as item_layout_migration
from code_assist_v1 import notion_workspace_database_template_default_migration as template_default_migration
from code_assist_v1 import notion_workspace_database_sharing_migration as database_sharing_migration
from code_assist_v1 import notion_workspace_database_upload_scope_migration as upload_scope_migration
from code_assist_v1 import notion_workspace_direct_database_favorite_migration as direct_favorite_migration
from .canonical_authority import NotFoundOrDenied


def installed(con: sqlite3.Connection) -> bool:
    marker = con.execute(
        "SELECT version,name,checksum,actual_schema_sha256 "
        "FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return marker is not None and tuple(marker) in {
        (item.TARGET_VERSION, item.TARGET_NAME, item.TARGET_CHECKSUM,
         item.TARGET_ACTUAL_SCHEMA_SHA256)
        for item in (migration, custom_emoji_migration, item_layout_migration, template_default_migration, database_sharing_migration, upload_scope_migration, direct_favorite_migration)
    }


def page_row_ids(con: sqlite3.Connection, database_id: str) -> frozenset[str]:
    """Rows placed on ordinary pages must not project into their source DB."""
    if not installed(con):
        return frozenset()
    return frozenset(str(row[0]) for row in con.execute(
        "SELECT placement.row_id FROM workspace_collaboration_database_row_page_placements placement "
        "JOIN workspace_collaboration_database_rows item ON item.id=placement.row_id "
        "WHERE item.database_id=? AND placement.placement='page'",
        (database_id,),
    ).fetchall())


def require_database_placement(con: sqlite3.Connection, database_id: str,
                               row_id: str) -> None:
    if row_id in page_row_ids(con, database_id):
        raise NotFoundOrDenied("not found or permission denied")
