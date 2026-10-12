"""Database item content shares the source document's authority and transaction."""
from copy import deepcopy
import json

from code_assist_v1 import notion_workspace_row_body_migration as migration
from code_assist_v1 import notion_workspace_row_appearance_migration as appearance_migration
from code_assist_v1 import notion_workspace_same_document_file_copy_migration as same_file_migration
from code_assist_v1 import notion_workspace_database_activity_migration as activity_migration
from code_assist_v1 import notion_workspace_database_trash_migration as trash_migration
from code_assist_v1 import notion_workspace_database_row_trash_migration as row_trash_migration
from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement_migration
from code_assist_v1 import notion_workspace_custom_emoji_migration as custom_emoji_migration
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as item_layout_migration
from code_assist_v1 import notion_workspace_database_template_default_migration as template_default_migration
from code_assist_v1 import notion_workspace_database_sharing_migration as database_sharing_migration
from code_assist_v1 import notion_workspace_database_upload_scope_migration as upload_scope_migration
from code_assist_v1 import notion_workspace_direct_database_favorite_migration as direct_favorite_migration
from .canonical_authority import AuthorityUnavailable, _canonical_json, _clean_blocks

MAX_BODY_BLOCKS = 1000
MAX_BODY_BYTES = 1_048_576


def installed(con):
    marker = con.execute("SELECT version,name,checksum,actual_schema_sha256 FROM workspace_schema_migrations "
                         "ORDER BY version DESC LIMIT 1").fetchone()
    return marker is not None and tuple(marker) in {
        (item.TARGET_VERSION,item.TARGET_NAME,item.TARGET_CHECKSUM,item.TARGET_ACTUAL_SCHEMA_SHA256)
        for item in (migration,appearance_migration,same_file_migration,activity_migration,trash_migration,row_trash_migration,placement_migration,custom_emoji_migration,item_layout_migration,template_default_migration,database_sharing_migration, upload_scope_migration, direct_favorite_migration)
    }


def clean_blocks(value):
    if not isinstance(value, list) or len(value) > MAX_BODY_BLOCKS:
        raise ValueError("database item body must contain at most 1000 blocks")
    for block in value:
        if not isinstance(block, dict) or not isinstance(block.get("content", ""), str):
            raise ValueError("database item block content must be text")
    blocks = _clean_blocks(value)
    try:
        encoded = json.dumps(blocks, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("database item body must be valid JSON") from exc
    if len(encoded.encode("utf-8")) > MAX_BODY_BYTES:
        raise ValueError("database item body is too large")
    return blocks


def set_body(con, row_id, payload, *, expected_version, now):
    if not installed(con):
        raise AuthorityUnavailable("database item content is not installed")
    if set(payload) != {"blocks"}:
        raise ValueError("database item content requires only blocks")
    blocks = clean_blocks(payload["blocks"])
    con.execute("INSERT INTO workspace_collaboration_database_row_bodies(row_id,blocks_json) VALUES(?,?) "
                "ON CONFLICT(row_id) DO UPDATE SET blocks_json=excluded.blocks_json",
                (row_id, _canonical_json(blocks)))
    con.execute("UPDATE workspace_collaboration_database_rows SET row_version=row_version+1,updated_at=? "
                "WHERE id=? AND row_version=?", (now, row_id, expected_version))
    return {"target_version": expected_version + 1, "body": {"blocks": blocks}}


def snapshot_bodies(con, document_id):
    if not installed(con):
        return {}
    return {str(row["row_id"]): {"blocks": json.loads(str(row["blocks_json"]))}
            for row in con.execute(
                "SELECT b.row_id,b.blocks_json FROM workspace_collaboration_database_row_bodies b "
                "JOIN workspace_collaboration_database_rows r ON r.id=b.row_id "
                "JOIN workspace_collaboration_databases d ON d.id=r.database_id WHERE d.document_id=?",
                (document_id,))}


def restore_bodies(con, current_databases, desired_databases, *, now):
    """Restore bodies only when all other DB state matches; never report a partial restore.

    The pre-existing page restorer does not restore database structure or cells.
    A 2418 restore must therefore reject those changes before writing anything.
    Row versions are monotonic and may differ because body edits advance them.
    """
    if not installed(con):
        return
    from . import database_row_appearance
    def without_bodies(databases):
        result = deepcopy(databases)
        for database in result:
            for row in database["rows"]:
                row.pop("body", None)
                row.pop("version", None)
                if database_row_appearance.installed(con):row.pop("appearance",None)
        return result
    if not isinstance(desired_databases, list) or without_bodies(current_databases) != without_bodies(desired_databases):
        raise ValueError("database structure or properties changed; database history restore is unavailable")
    desired = {row["id"]: row for database in desired_databases for row in database["rows"]}
    for database in current_databases:
        for row in database["rows"]:
            source = desired[row["id"]]
            appearance_changed=database_row_appearance.restore_appearance(con,row["id"],row.get("appearance"),source.get("appearance"))
            if source.get("body") == row.get("body"):
                if appearance_changed:
                    con.execute("UPDATE workspace_collaboration_database_rows SET row_version=row_version+1,updated_at=? WHERE id=?",(now,row["id"]))
                continue
            body = source.get("body")
            if body is None:
                con.execute("DELETE FROM workspace_collaboration_database_row_bodies WHERE row_id=?", (row["id"],))
                con.execute("UPDATE workspace_collaboration_database_rows SET row_version=row_version+1,updated_at=? WHERE id=?",
                            (now, row["id"]))
            else:
                if not isinstance(body, dict):
                    raise ValueError("invalid database item history body")
                set_body(con, row["id"], body, expected_version=row["version"], now=now)
