"""Current database block identities for navigation favorites.

Collaborative editor blocks live in durable CRDT frames; the legacy block table
can lag behind them. Decode outside the catalog transaction and recheck the
document stamp before using the result for a navigation write.
"""
from __future__ import annotations

import json

from . import database_trash, search_projection
from .title_projection import _frames, _generation

DATABASE_TYPES = {"table", "board", "calendar", "list", "gallery", "timeline"}


def archived(con, workspace_id, page_id, block_id):
    """Return only the display identity of an exact, retained owned DB block."""
    if not database_trash.installed(con):
        return None
    row = con.execute(
        "SELECT trash.database_id,trash.block_snapshot_json,database.title "
        "FROM workspace_collaboration_database_trash_state trash "
        "JOIN workspace_collaboration_databases database "
        "ON database.id=trash.database_id AND database.document_id=trash.document_id "
        "WHERE trash.workspace_id=? AND trash.document_id=? AND trash.block_id=?",
        (workspace_id, page_id, block_id),
    ).fetchone()
    if row is None:
        return None
    try:
        block = json.loads(str(row["block_snapshot_json"]))
    except (TypeError, ValueError):
        return None
    if (not isinstance(block, dict) or block.get("id") != block_id
            or block.get("type") not in DATABASE_TYPES
            or not isinstance(block.get("props"), dict)
            or block["props"].get("linked_database")
            or block["props"].get("database_id", block_id) != row["database_id"]):
        return None
    return {"database_id": str(row["database_id"]),
        "title": str(row["title"]), "type": str(block["type"])}


def _database_ids(blocks):
    return {str(block["id"]) for block in blocks
            if isinstance(block, dict) and block.get("id")
            and block.get("type") in DATABASE_TYPES
            and not (block.get("props") or {}).get("linked_database")}


def prepare(store, workspace_id, page_id):
    with store.iam._reader() as con:
        con.execute("BEGIN")
        document = con.execute("SELECT document_sequence,archived FROM workspace_collaboration_documents "
                               "WHERE workspace_id=? AND id=?", (workspace_id, page_id)).fetchone()
        if document is None or document["archived"]:
            return None
        generation = _generation(con, page_id)
        stamp = (int(document["document_sequence"]), generation)
        canonical = [{"id": str(row["id"]), "type": str(row["block_type"]),
                      "props": json.loads(str(row["props_json"]))}
                     for row in con.execute("SELECT id,block_type,props_json FROM workspace_collaboration_blocks "
                                            "WHERE document_id=?", (page_id,)).fetchall()]
        frames = _frames(con, page_id, generation)
        last_frame_sequence = con.execute(
            "SELECT COALESCE(MAX(update_row.document_sequence),0) "
            "FROM workspace_collaboration_crdt_updates update_row "
            "JOIN workspace_collaboration_crdt_update_bindings binding ON binding.update_id=update_row.id "
            "WHERE binding.document_id=? AND binding.generation=?",
            (page_id, generation),
        ).fetchone()[0]
        post_frame_inserts = {str(row["target_id"]) for row in con.execute(
            "SELECT target_id FROM workspace_collaboration_typed_commands "
            "WHERE document_id=? AND command_kind='block.insert' AND document_sequence>?",
            (page_id, int(last_frame_sequence)),
        ).fetchall()}
    projected = search_projection.materialize([frames])[0]["blocks"] if frames else None
    blocks = canonical if projected is None else projected
    ids = _database_ids(blocks)
    if projected is not None:
        ids.update(_database_ids(block for block in canonical if block["id"] in post_frame_inserts))
    return {"stamp": stamp, "ids": ids}


def current(con, workspace_id, page_id, proof):
    if proof is None:
        return False
    document = con.execute("SELECT document_sequence,archived FROM workspace_collaboration_documents "
                           "WHERE workspace_id=? AND id=?", (workspace_id, page_id)).fetchone()
    return bool(document and not document["archived"]
                and (int(document["document_sequence"]), _generation(con, page_id)) == proof["stamp"])
