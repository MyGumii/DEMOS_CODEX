"""Versioned default-template selection for one canonical database."""
from __future__ import annotations

import sqlite3

from code_assist_v1 import notion_workspace_database_template_default_migration as migration
from code_assist_v1 import notion_workspace_database_sharing_migration as database_sharing_migration
from code_assist_v1 import notion_workspace_database_upload_scope_migration as upload_scope_migration
from code_assist_v1 import notion_workspace_direct_database_favorite_migration as direct_favorite_migration
from .canonical_authority import AuthorityUnavailable, NotFoundOrDenied, _identifier
from .editing_authority import TargetVersionConflict


def installed(con: sqlite3.Connection) -> bool:
    marker = con.execute(
        "SELECT version,name,checksum,actual_schema_sha256 FROM workspace_schema_migrations "
        "ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return marker is not None and tuple(marker) in {
        (item.TARGET_VERSION, item.TARGET_NAME,
         item.TARGET_CHECKSUM, item.TARGET_ACTUAL_SCHEMA_SHA256)
        for item in (migration, database_sharing_migration, upload_scope_migration, direct_favorite_migration)
    }


def read(con: sqlite3.Connection, database_id: str) -> dict[str, int | str | None]:
    if not installed(con):
        return {"template_id": None, "version": 0}
    row = con.execute(
        "SELECT template_id,row_version FROM workspace_collaboration_database_template_defaults "
        "WHERE database_id=?", (database_id,),
    ).fetchone()
    return {"template_id": str(row["template_id"]) if row["template_id"] is not None else None,
            "version": int(row["row_version"])} if row else {"template_id": None, "version": 0}


def set_default(con: sqlite3.Connection, database_id: str, template_id: str | None,
                *, expected_version: int | None, actor: str, now: int) -> int:
    if not installed(con):
        raise AuthorityUnavailable("database template defaults are not installed")
    current = read(con, database_id)
    if expected_version is None or expected_version != current["version"]:
        raise TargetVersionConflict(int(current["version"]))
    if template_id is not None:
        template_id = _identifier(template_id, field="template_id")
        template = con.execute(
            "SELECT 1 FROM workspace_collaboration_database_templates "
            "WHERE database_id=? AND id=? AND state='active'",
            (database_id, template_id),
        ).fetchone()
        if template is None:
            raise NotFoundOrDenied("not found or permission denied")
    next_version = int(current["version"]) + 1
    if current["version"] == 0:
        con.execute(
            "INSERT INTO workspace_collaboration_database_template_defaults("
            "database_id,template_id,row_version,updated_by_subject_id,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (database_id, template_id, next_version, actor, now, now),
        )
    else:
        con.execute(
            "UPDATE workspace_collaboration_database_template_defaults "
            "SET template_id=?,row_version=?,updated_by_subject_id=?,updated_at=? "
            "WHERE database_id=?",
            (template_id, next_version, actor, now, database_id),
        )
    return next_version


def clear_archived(con: sqlite3.Connection, database_id: str, template_id: str,
                   *, actor: str, now: int) -> None:
    if not installed(con):
        return
    con.execute(
        "UPDATE workspace_collaboration_database_template_defaults "
        "SET template_id=NULL,row_version=row_version+1,updated_by_subject_id=?,updated_at=? "
        "WHERE database_id=? AND template_id=?",
        (actor, now, database_id, template_id),
    )
