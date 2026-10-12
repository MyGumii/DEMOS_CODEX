"""Canonical database edit identity for the optional 2421 successor schema."""
from __future__ import annotations

import sqlite3
from typing import Iterable

from code_assist_v1 import notion_workspace_database_activity_migration as migration
from code_assist_v1 import notion_workspace_database_trash_migration as trash_migration
from code_assist_v1 import notion_workspace_database_row_trash_migration as row_trash_migration
from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement_migration
from code_assist_v1 import notion_workspace_custom_emoji_migration as custom_emoji_migration
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as item_layout_migration
from code_assist_v1 import notion_workspace_database_template_default_migration as template_default_migration
from code_assist_v1 import notion_workspace_database_sharing_migration as database_sharing_migration
from code_assist_v1 import notion_workspace_database_upload_scope_migration as upload_scope_migration
from code_assist_v1 import notion_workspace_direct_database_favorite_migration as direct_favorite_migration


def installed(con: sqlite3.Connection) -> bool:
    marker = con.execute(
        "SELECT version,name,checksum,actual_schema_sha256 FROM workspace_schema_migrations "
        "ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return marker is not None and tuple(marker) in {
        (item.TARGET_VERSION, item.TARGET_NAME,
         item.TARGET_CHECKSUM, item.TARGET_ACTUAL_SCHEMA_SHA256)
        for item in (migration, trash_migration, row_trash_migration, placement_migration, custom_emoji_migration, item_layout_migration, template_default_migration, database_sharing_migration, upload_scope_migration, direct_favorite_migration)
    }


def touch(con: sqlite3.Connection, *, database_ids: Iterable[str], document_id: str,
          actor_subject_id: str, edited_at: int, document_sequence: int) -> None:
    """Record only caller-proven DB changes inside its existing transaction."""
    if not installed(con):
        return
    for database_id in set(database_ids):
        con.execute(
            "INSERT INTO workspace_collaboration_database_activity("
            "database_id,document_id,last_editor_subject_id,last_edited_at,document_sequence) "
            "SELECT id,document_id,?,?,? FROM workspace_collaboration_databases "
            "WHERE id=? AND document_id=? "
            "ON CONFLICT(database_id) DO UPDATE SET "
            "last_editor_subject_id=excluded.last_editor_subject_id,"
            "last_edited_at=excluded.last_edited_at,"
            "document_sequence=excluded.document_sequence "
            "WHERE excluded.document_sequence>workspace_collaboration_database_activity.document_sequence",
            (actor_subject_id, edited_at, document_sequence, database_id, document_id),
        )
