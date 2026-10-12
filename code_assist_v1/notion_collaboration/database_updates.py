"""Authorized, content-free update summaries for one owned database."""
from __future__ import annotations

import json

from .canonical_authority import NotFoundOrDenied, _identifier
from .iam_authority import CollaborativeIAMAuthority


def read_database_updates(
    authority: CollaborativeIAMAuthority,
    actor_subject_id: str,
    workspace_id: str,
    database_id: str,
    *,
    before_sequence: int | None = None,
    limit: int = 50,
    _scoped: bool = False,
) -> dict:
    actor = authority._require_active(actor_subject_id)
    workspace = _identifier(workspace_id, field="workspace_id")
    database = _identifier(database_id, field="database_id")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if before_sequence is not None and before_sequence < 1:
        raise ValueError("before_sequence must be positive")

    with authority._reader() as con:
        con.execute("BEGIN")
        if _scoped:
            from . import database_access
            access = database_access.resolve_on_connection(
                authority, con, actor, workspace, database,
            )
            document = access.document_id
            owned = authority._document(con, document)
        else:
            owned = con.execute(
                "SELECT db.document_id,page.workspace_id,page.document_sequence "
                "FROM workspace_collaboration_databases db "
                "JOIN workspace_collaboration_documents page ON page.id=db.document_id "
                "WHERE db.id=? AND page.workspace_id=?",
                (database, workspace),
            ).fetchone()
            if owned is None:
                raise NotFoundOrDenied("not found or permission denied")
            document = str(owned["document_id"])
        trash_schema = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='workspace_collaboration_database_trash_state'",
        ).fetchone()
        if trash_schema and con.execute(
            "SELECT 1 FROM workspace_collaboration_database_trash_state WHERE database_id=?",
            (database,),
        ).fetchone():
            raise NotFoundOrDenied("not found or permission denied")
        if not _scoped:
            role, _ = authority._resolution_on_connection(
                con, actor, workspace, document,
            )
            if role is None:
                raise NotFoundOrDenied("not found or permission denied")
        ceiling = min(
            int(owned["document_sequence"]),
            (before_sequence - 1) if before_sequence is not None else int(owned["document_sequence"]),
        )
        rows = con.execute(
            "SELECT event.document_sequence,event.event_type,event.actor_subject_id,"
            "event.created_at,event.payload_json,"
            "json_extract(event.payload_json,'$.command_kind') AS command_kind "
            "FROM workspace_collaboration_events event "
            "LEFT JOIN workspace_collaboration_database_properties cell_property "
            "ON cell_property.id=json_extract(event.payload_json,'$.property_id') "
            "LEFT JOIN workspace_collaboration_database_properties property "
            "ON property.id=json_extract(event.payload_json,'$.target_id') "
            "WHERE event.workspace_id=? AND event.document_id=? "
            "AND event.document_sequence<=? AND ("
            "(event.event_type LIKE 'database.%' "
            "AND json_extract(event.payload_json,'$.database_id')=?) "
            "OR (event.event_type='typed.command.committed' AND ("
            "(json_extract(event.payload_json,'$.command_kind')='database.cell.set' "
            "AND cell_property.database_id=?) "
            "OR (json_extract(event.payload_json,'$.command_kind')='database.property.rename' "
            "AND property.database_id=?)))) "
            "ORDER BY event.document_sequence DESC LIMIT ?",
            (workspace, document, ceiling, database, database, database, limit + 1),
        ).fetchall()
        row_trash_schema = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='workspace_collaboration_database_row_trash_state'",
        ).fetchone()
        active_condition = (
            " AND NOT EXISTS (SELECT 1 FROM workspace_collaboration_database_row_trash_state trash "
            "WHERE trash.row_id=item.id)" if row_trash_schema else ""
        )
        visible_row_ids = {
            str(row["id"]) for row in con.execute(
                "SELECT item.id FROM workspace_collaboration_database_rows item "
                "WHERE item.database_id=?" + active_condition,
                (database,),
            )
        }
        items = []
        for row in rows[:limit]:
            kind = str(row["command_kind"] or "")
            event_type = str(row["event_type"])
            payload = json.loads(str(row["payload_json"]))
            if event_type == "database.surface.committed" and not kind:
                if isinstance(payload.get("cells"), list):
                    kind = "database.cells.set"
                elif isinstance(payload.get("row_id"), str):
                    kind = "database.row.content.set"
            candidate = None
            if kind == "database.cell.set" or kind.startswith("database.row.") \
                    or event_type in ("database.row.trashed", "database.row.restored"):
                candidate = payload.get("row_id") or payload.get("target_id")
            elif kind == "database.cells.set":
                affected = {cell.get("row_id") for cell in payload.get("cells", [])
                            if isinstance(cell, dict) and isinstance(cell.get("row_id"), str)}
                if len(affected) == 1:
                    candidate = next(iter(affected))
            elif event_type == "database.surface.committed":
                if isinstance(payload.get("row_id"), str):
                    candidate = payload["row_id"]
                elif isinstance(payload.get("cells"), list):
                    affected = {cell.get("row_id") for cell in payload["cells"]
                                if isinstance(cell, dict)
                                and isinstance(cell.get("row_id"), str)}
                    if len(affected) == 1:
                        candidate = next(iter(affected))
            item = {
                "document_sequence": int(row["document_sequence"]),
                "event_type": event_type,
                "command_kind": kind,
                "actor_subject_id": str(row["actor_subject_id"]),
                "created_at": int(row["created_at"]),
            }
            if isinstance(candidate, str) and candidate in visible_row_ids:
                item["row_id"] = candidate
            items.append(item)
        result = {
            "database_id": database,
            "items": items,
            "next_before_sequence": items[-1]["document_sequence"] if len(rows) > limit else None,
        }
        if not _scoped:
            result["document_id"] = document
        return result


def read_database_updates_scoped(authority: CollaborativeIAMAuthority,
                                 actor_subject_id: str, workspace_id: str,
                                 database_id: str, *,
                                 before_sequence: int | None = None,
                                 limit: int = 50) -> dict:
    """Stage content-free updates under the independent database grant."""
    return read_database_updates(
        authority, actor_subject_id, workspace_id, database_id,
        before_sequence=before_sequence, limit=limit, _scoped=True,
    )
