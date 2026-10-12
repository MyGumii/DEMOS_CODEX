"""Exact 2422 database Trash state, read barrier, and reversible service."""
from __future__ import annotations

import json
import sqlite3
import time

from code_assist_v1 import notion_workspace_database_trash_migration as migration
from code_assist_v1 import notion_workspace_database_row_trash_migration as row_trash_migration
from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement_migration
from code_assist_v1 import notion_workspace_custom_emoji_migration as custom_emoji_migration
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as item_layout_migration
from code_assist_v1 import notion_workspace_database_template_default_migration as template_default_migration
from code_assist_v1 import notion_workspace_database_sharing_migration as database_sharing_migration
from code_assist_v1 import notion_workspace_database_upload_scope_migration as upload_scope_migration
from code_assist_v1 import notion_workspace_direct_database_favorite_migration as direct_favorite_migration
from .canonical_authority import (
    AuthorityUnavailable, EpochConflict, NotFoundOrDenied, _canonical_json,
    _identifier, _operation_id, _positive, _request_sha256, _sha256,
)
from .title_projection import _frames, _generation


CONTRACT = "code_assist.notion.owned_database_trash.v1"


class DatabaseTrashShapeUnavailable(RuntimeError):
    """The current live owned block cannot be safely retained in Trash."""


def _document_conflict(sequence):
    from .editing_authority import DocumentSequenceConflict
    return DocumentSequenceConflict(sequence)


def installed(con: sqlite3.Connection) -> bool:
    marker = con.execute(
        "SELECT version,name,checksum,actual_schema_sha256 "
        "FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return marker is not None and tuple(marker) in {
        (item.TARGET_VERSION, item.TARGET_NAME,
         item.TARGET_CHECKSUM, item.TARGET_ACTUAL_SCHEMA_SHA256)
        for item in (migration, row_trash_migration, placement_migration, custom_emoji_migration, item_layout_migration, template_default_migration, database_sharing_migration, upload_scope_migration, direct_favorite_migration)
    }


def document_members(con: sqlite3.Connection, document_id: str) -> tuple[tuple[str, str], ...]:
    if not installed(con):
        return ()
    return tuple((str(row[0]), str(row[1])) for row in con.execute(
        "SELECT database_id,block_id FROM workspace_collaboration_database_trash_state "
        "WHERE document_id=?", (document_id,)).fetchall())


def is_trashed(con: sqlite3.Connection, database_id: str) -> bool:
    return installed(con) and con.execute(
        "SELECT 1 FROM workspace_collaboration_database_trash_state "
        "WHERE database_id=?", (database_id,)).fetchone() is not None


def require_active(con: sqlite3.Connection, database_id: str) -> None:
    if is_trashed(con, database_id):
        raise NotFoundOrDenied("not found or permission denied")


def hidden_comment_ids(con: sqlite3.Connection, document_id: str) -> frozenset[str]:
    """Hide archived DB threads, including replies that have no own DB anchor."""
    members = document_members(con, document_id)
    if not members:
        return frozenset()
    database_ids = {database_id for database_id, _ in members}
    block_ids = {block_id for _, block_id in members}
    mapped = {str(row[0]) for row in con.execute(
        "SELECT c.comment_id FROM workspace_collaboration_database_trash_block_comments c "
        "JOIN workspace_collaboration_database_trash_state t ON t.database_id=c.database_id "
        "WHERE t.document_id=?", (document_id,)).fetchall()}
    rows = con.execute("SELECT id,parent_comment_id,block_id,anchor_json "
        "FROM workspace_collaboration_comments WHERE document_id=?",
        (document_id,)).fetchall()
    hidden = set(mapped)
    pending = []
    for row in rows:
        try:
            anchor = json.loads(str(row["anchor_json"]))
        except (TypeError, ValueError) as exc:
            raise AuthorityUnavailable("database comment anchor is invalid") from exc
        if (str(row["id"]) in mapped or row["block_id"] in block_ids
                or (isinstance(anchor, dict) and anchor.get("kind") == "database_row"
                    and anchor.get("database_id") in database_ids)):
            hidden.add(str(row["id"]))
        elif row["parent_comment_id"] is not None:
            pending.append((str(row["id"]), str(row["parent_comment_id"])))
    while pending:
        next_pending = [(comment_id, parent_id) for comment_id, parent_id in pending
            if parent_id not in hidden]
        if len(next_pending) == len(pending):
            break
        hidden.update(comment_id for comment_id, parent_id in pending
            if parent_id in hidden)
        pending = next_pending
    return frozenset(hidden)


def trashed_file_ids(con: sqlite3.Connection, document_id: str, *,
                     exclude_database_id: str | None = None) -> frozenset[str]:
    """Find file references retained by independent DB Trash membership."""
    from . import database_row_trash

    references: set[str] = set(database_row_trash.retained_file_ids(con, document_id,
        exclude_database_id=exclude_database_id))
    members = tuple(member for member in document_members(con, document_id)
        if member[0] != exclude_database_id)
    if not members:
        return frozenset(references)
    from .database_move import _file_ids

    def collect(rows):
        for row in rows:
            for key in row.keys():
                value = row[key]
                if value is None:
                    continue
                if key.endswith("_json"):
                    try:
                        value = json.loads(str(value))
                    except (TypeError, ValueError) as exc:
                        raise AuthorityUnavailable("database Trash file reference is invalid") from exc
                references.update(_file_ids({key: value}))

    for database_id, _block_id in members:
        collect(con.execute(
            "SELECT block_snapshot_json FROM workspace_collaboration_database_trash_state "
            "WHERE database_id=?", (database_id,)).fetchall())
        collect(con.execute("SELECT * FROM workspace_collaboration_databases "
            "WHERE id=?", (database_id,)).fetchall())
        for table in ("properties", "views", "templates", "rows"):
            collect(con.execute("SELECT * FROM workspace_collaboration_database_" + table +
                " WHERE database_id=?", (database_id,)).fetchall())
        for table in ("cells", "row_bodies", "row_appearance"):
            key = "row_id"
            collect(con.execute("SELECT source.* FROM workspace_collaboration_database_" + table +
                " source JOIN workspace_collaboration_database_rows r "
                "ON r.id=source." + key + " WHERE r.database_id=?",
                (database_id,)).fetchall())
    return frozenset(references)


def _owner(authority, con, actor, workspace_id, document_id=None):
    """Retain the exact owner rule for 2423 without widening other services."""
    from . import database_copy, database_row_trash, page_archive

    if not database_row_trash.installed(con):
        if document_id is None:
            return page_archive._owner(authority, con, actor, workspace_id)
        return database_copy._owner(authority, con, actor, workspace_id, document_id)
    authority._require_active(actor)
    workspace = authority._workspace(con, workspace_id)
    if authority._workspace_role(con, workspace_id, actor) != "owner":
        raise NotFoundOrDenied("not found or permission denied")
    page = authority._document(con, document_id) if document_id else None
    if page is not None and (page["workspace_id"] != workspace_id
            or page["legacy_owner_subject_id"] != actor):
        raise NotFoundOrDenied("not found or permission denied")
    return workspace, page


def file_visibility_probe(authority, con: sqlite3.Connection, page):
    """Capture one consistent canonical/live source for a file read."""
    held = trashed_file_ids(con, str(page["id"]))
    if not held:
        return None
    generation = _generation(con, str(page["id"]))
    frames = _frames(con, str(page["id"]), generation)
    members = document_members(con, str(page["id"]))
    return {"stamp": (int(page["row_version"]), int(page["document_sequence"]),
            int(page["access_epoch"]), generation, members, held),
        "frames": frames, "snapshot": authority._editing_snapshot(con, page),
        "held": held, "hidden_block_ids": frozenset(block_id for _, block_id in members)}


def file_visibility_unchanged(con: sqlite3.Connection, page, probe) -> bool:
    if probe is None:
        return not trashed_file_ids(con, str(page["id"]))
    stamp = (int(page["row_version"]), int(page["document_sequence"]),
        int(page["access_epoch"]), _generation(con, str(page["id"])),
        document_members(con, str(page["id"])),
        trashed_file_ids(con, str(page["id"])))
    return stamp == probe["stamp"] and _frames(con, str(page["id"]), stamp[3]) == probe["frames"]


def hidden_file_ids_from_probe(probe) -> frozenset[str]:
    """Replay live blocks outside SQLite; canonical database rows stay authoritative."""
    if probe is None:
        return frozenset()
    from .database_move import _file_ids
    blocks = probe["snapshot"]["blocks"]
    if probe["frames"]:
        from . import search_projection
        current = search_projection.materialize([probe["frames"]])[0]
        if current["blocks"] is not None:
            blocks = current["blocks"]
    blocks = [block for block in blocks
        if block["id"] not in probe["hidden_block_ids"]]
    active = _file_ids({"blocks": blocks,
        "databases": probe["snapshot"]["databases"],
        "icon": probe["snapshot"].get("icon", ""),
        "cover": probe["snapshot"].get("cover", "")})
    return frozenset(probe["held"] - active)


def _request(actor_subject_id, workspace_id, document_id, database_id, *, archived,
             operation_id, expected_document_sequence, expected_workspace_epoch,
             expected_document_epoch, expected_database_version):
    actor = _identifier(actor_subject_id, field="actor_subject_id")
    workspace = _identifier(workspace_id, field="workspace_id")
    document = _identifier(document_id, field="document_id")
    database = _identifier(database_id, field="database_id")
    if type(archived) is not bool:
        raise ValueError("archived must be a boolean")
    operation = _operation_id(operation_id)
    sequence = _positive(expected_document_sequence, field="expected_document_sequence")
    workspace_epoch = _positive(expected_workspace_epoch, field="expected_workspace_epoch")
    document_epoch = _positive(expected_document_epoch, field="expected_document_epoch")
    database_version = _positive(expected_database_version, field="expected_database_version")
    action = "collaboration.database_trash" if archived else "collaboration.database_restore"
    digest = _request_sha256(actor, action, {"workspace_id": workspace,
        "document_id": document, "database_id": database, "archived": archived,
        "expected_document_sequence": sequence,
        "expected_workspace_epoch": workspace_epoch,
        "expected_document_epoch": document_epoch,
        "expected_database_version": database_version})
    return (actor, workspace, document, database, archived, operation, action,
        digest, sequence, workspace_epoch, document_epoch, database_version)


def _scope(authority, con, actor, workspace_id, document_id, database_id,
           archived, sequence, workspace_epoch, document_epoch, database_version):
    from . import page_move, page_copy_remap
    if not installed(con):
        raise AuthorityUnavailable("database Trash requires the 2422 schema")
    workspace, page = _owner(authority, con, actor, workspace_id, document_id)
    if page["archived"]:
        raise NotFoundOrDenied("not found or permission denied")
    page_move._unlocked(con, page, actor)
    if workspace["security_epoch"] != workspace_epoch or page["access_epoch"] != document_epoch:
        raise EpochConflict(workspace["security_epoch"], page["access_epoch"])
    if page["document_sequence"] != sequence:
        raise _document_conflict(page["document_sequence"])
    database = con.execute("SELECT * FROM workspace_collaboration_databases "
        "WHERE id=? AND document_id=?", (database_id, document_id)).fetchone()
    if database is None:
        raise NotFoundOrDenied("not found or permission denied")
    if database["row_version"] != database_version:
        raise AuthorityUnavailable("database version changed")
    state = con.execute("SELECT * FROM workspace_collaboration_database_trash_state "
        "WHERE database_id=?", (database_id,)).fetchone()
    if (state is None) != archived:
        raise NotFoundOrDenied("database is not in the requested lifecycle state")
    if state is not None and int(state["database_row_version"]) + 1 != database_version:
        raise AuthorityUnavailable("archived database changed after Trash")
    body = authority._editing_snapshot(con, page)
    if archived:
        matches = [item for item in body["blocks"]
            if item["type"] in page_copy_remap.DATABASE_BLOCKS
            and not item.get("props", {}).get("linked_database")
            and item.get("props", {}).get("database_id", item["id"]) == database_id]
        if len(matches) != 1 or not any(item["id"] == database_id
                for item in body["databases"]):
            raise AuthorityUnavailable("owned database block is inconsistent")
        block = matches[0]
        stored = con.execute("SELECT row_version,created_at,position "
            "FROM workspace_collaboration_blocks WHERE id=? AND document_id=?",
            (block["id"], document_id)).fetchone()
        if stored is None:
            raise AuthorityUnavailable("owned database block is missing")
    else:
        try:
            block = json.loads(str(state["block_snapshot_json"]))
        except (TypeError, ValueError) as exc:
            raise AuthorityUnavailable("database Trash snapshot is invalid") from exc
        if (not isinstance(block, dict) or block.get("id") != state["block_id"]
                or block.get("type") not in page_copy_remap.DATABASE_BLOCKS
                or block.get("props", {}).get("database_id", block["id"]) != database_id
                or any(item["id"] == block["id"] for item in body["blocks"])):
            raise AuthorityUnavailable("database Trash block is inconsistent")
        stored = None
    return workspace, page, database, state, block, stored, body


def set_owned_database_trashed(authority, actor_subject_id, workspace_id,
                              document_id, database_id, *, archived,
                              operation_id, expected_document_sequence,
                              expected_workspace_epoch, expected_document_epoch,
                              expected_database_version, fault=None):
    from . import database_activity, page_move, search_projection
    (actor, workspace_id, document_id, database_id, archived, operation, action,
     digest, sequence, workspace_epoch, document_epoch, database_version) = _request(
        actor_subject_id, workspace_id, document_id, database_id,
        archived=archived, operation_id=operation_id,
        expected_document_sequence=expected_document_sequence,
        expected_workspace_epoch=expected_workspace_epoch,
        expected_document_epoch=expected_document_epoch,
        expected_database_version=expected_database_version)

    def replay(con):
        return authority._replay(con, actor=actor, action=action,
            operation_id=operation, request_sha256=digest,
            authorize=lambda: _owner(authority, con, actor,
                workspace_id, document_id))

    with authority._reader() as con:
        con.execute("BEGIN")
        previous = replay(con)
        if previous is not None:
            return {**previous, "replayed": True}
        scope = _scope(authority, con, actor, workspace_id, document_id,
            database_id, archived, sequence, workspace_epoch, document_epoch,
            database_version)
        workspace, page, database, state, block, stored, body = scope
        generation = _generation(con, document_id)
        stamp = page_move._stamp(page, generation)
        frames = _frames(con, document_id, generation)
        state_witness = tuple(state) if state is not None else None
        comment_ids = tuple(str(row[0]) for row in con.execute(
            "SELECT id FROM workspace_collaboration_comments "
            "WHERE document_id=? AND block_id=? ORDER BY id",
            (document_id, block["id"])).fetchall()) if archived else tuple(
            str(row[0]) for row in con.execute(
                "SELECT comment_id FROM workspace_collaboration_database_trash_block_comments "
                "WHERE database_id=? ORDER BY comment_id", (database_id,)).fetchall())
        source = {"id": document_id, "stamp": stamp, "generation": generation,
            "frames": frames, "snapshot": body, "match_kind": "owned_database",
            **page_move.parent_rebase.capture_witness(con, document_id, generation, body),
            "match_id": database_id, "expected_match_count": 1 if archived else 0,
            "insert": None if archived else block}
        if state is not None:
            try:
                source["restore_anchor"] = json.loads(str(state["restore_anchor_json"]))
            except (TypeError, ValueError) as exc:
                raise AuthorityUnavailable("database Trash restore anchor is invalid") from exc
    if archived and frames:
        current = search_projection.materialize([frames])[0]
        live_blocks = current["blocks"]
        if live_blocks is not None:
            live = [item for item in live_blocks if item["id"] == block["id"]]
            from .database_move import _file_ids
            if len(live) != 1 or _file_ids(live[0]) != _file_ids(block):
                raise DatabaseTrashShapeUnavailable(
                    "live owned database attachments differ from the canonical block")
    delta = page_move._prepare_deltas({document_id: source}, database_id,
        operation)[document_id]
    page_move.parent_rebase.validate_deltas({document_id: source}, {document_id: delta}, database_id)
    if archived:
        if (delta["removed"] != [block["id"]] or not delta["changed"]
                or delta["placed"] is not None or delta["restore_anchor"] is None):
            raise AuthorityUnavailable("database Trash removal changed identity")
    elif (delta["removed"] or delta["placed"] != block["id"]
            or not delta["inserted"] or not delta["changed"]):
        raise AuthorityUnavailable("database Trash restore changed identity")

    with authority._transaction() as con:
        previous = replay(con)
        if previous is not None:
            return {**previous, "replayed": True}
        current = _scope(authority, con, actor, workspace_id, document_id,
            database_id, archived, sequence, workspace_epoch, document_epoch,
            database_version)
        workspace, page, database, state, current_block, current_stored, body = current
        if (page_move._stamp(page, _generation(con, document_id)) != stamp
                or current_block != block
                or (tuple(state) if state is not None else None) != state_witness
                or (tuple(current_stored) if current_stored is not None else None)
                    != (tuple(stored) if stored is not None else None)):
            raise _document_conflict(page["document_sequence"])
        current_comments = tuple(str(row[0]) for row in con.execute(
            "SELECT id FROM workspace_collaboration_comments "
            "WHERE document_id=? AND block_id=? ORDER BY id",
            (document_id, block["id"])).fetchall()) if archived else tuple(
            str(row[0]) for row in con.execute(
                "SELECT comment_id FROM workspace_collaboration_database_trash_block_comments "
                "WHERE database_id=? ORDER BY comment_id", (database_id,)).fetchall())
        if current_comments != comment_ids:
            raise AuthorityUnavailable("database block comments changed during lifecycle operation")
        now = int(time.time())
        if archived:
            con.execute("INSERT INTO workspace_collaboration_database_trash_state "
                "(database_id,workspace_id,document_id,block_id,block_snapshot_json,"
                "source_position,block_row_version,block_created_at,restore_anchor_json,"
                "database_row_version,archived_by_subject_id,archived_at,archive_operation_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (database_id, workspace_id, document_id, block["id"],
                    _canonical_json(block), stored["position"], stored["row_version"],
                    stored["created_at"], _canonical_json(delta["restore_anchor"]),
                    database["row_version"], actor, now, operation))
            con.executemany("INSERT INTO workspace_collaboration_database_trash_block_comments "
                "(database_id,comment_id) VALUES(?,?)",
                [(database_id, comment_id) for comment_id in comment_ids])
            deleted = con.execute("DELETE FROM workspace_collaboration_blocks "
                "WHERE id=? AND document_id=? AND row_version=?",
                (block["id"], document_id, stored["row_version"]))
            if deleted.rowcount != 1:
                raise AuthorityUnavailable("owned database block changed during Trash")
            ordered = [item["id"] for item in body["blocks"] if item["id"] != block["id"]]
            authority._rewrite_block_positions(con, document_id, ordered, now)
        else:
            current_ids = [item["id"] for item in body["blocks"]]
            after = delta["after_block_id"]
            at = current_ids.index(after) + 1 if after in current_ids else 0
            ordered = [*current_ids[:at], block["id"], *current_ids[at:]]
            con.execute("INSERT INTO workspace_collaboration_blocks "
                "(id,document_id,position,block_type,content,props_json,row_version,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (block["id"], document_id, len(current_ids), block["type"],
                    block["content"], _canonical_json(block["props"]),
                    int(state["block_row_version"]) + 1,
                    state["block_created_at"], now))
            authority._rewrite_block_positions(con, document_id, ordered, now)
            for comment_id in comment_ids:
                updated = con.execute("UPDATE workspace_collaboration_comments "
                    "SET block_id=?,row_version=row_version+1,updated_at=? "
                    "WHERE id=? AND document_id=? AND block_id IS NULL",
                    (block["id"], now, comment_id, document_id))
                if updated.rowcount != 1:
                    raise AuthorityUnavailable("database block comment changed during restore")
            con.execute("DELETE FROM workspace_collaboration_database_trash_state "
                "WHERE database_id=?", (database_id,))
        updated_database = con.execute("UPDATE workspace_collaboration_databases "
            "SET row_version=row_version+1,updated_at=? "
            "WHERE id=? AND document_id=? AND row_version=?",
            (now, database_id, document_id, database_version))
        if updated_database.rowcount != 1:
            raise AuthorityUnavailable("database changed during lifecycle operation")
        page_move._append_link_delta(authority, con, actor, workspace, page,
            delta["payload"], generation, operation, now,
            operation_action=action, receipt_action=action + "_delta",
            client_id="database-trash-service", binding_contract=CONTRACT,
            identity_prefix="dbtrashupd")
        updated_page = authority._document(con, document_id)
        checksum = _sha256(_canonical_json(authority._editing_snapshot(con, updated_page)))
        con.execute("UPDATE workspace_collaboration_documents SET content_sha256=? WHERE id=?",
            (checksum, document_id))
        con.execute("UPDATE workspace_collaboration_documents SET "
            "document_sequence=document_sequence+1,row_version=row_version+1,updated_at=? "
            "WHERE id=?", (now, document_id))
        authority._bump_document_epochs(con, (document_id,))
        authority._bump_workspace(con, workspace_id)
        workspace = authority._workspace(con, workspace_id)
        page = authority._document(con, document_id)
        database_activity.touch(con, database_ids=(database_id,),
            document_id=document_id, actor_subject_id=actor, edited_at=now,
            document_sequence=page["document_sequence"])
        result = {"contract": CONTRACT, "workspace_id": workspace_id,
            "document_id": document_id, "database_id": database_id,
            "block_id": block["id"], "archived": archived,
            "document_sequence": page["document_sequence"],
            "workspace_epoch": workspace["security_epoch"],
            "document_epoch": page["access_epoch"],
            "database_version": database_version + 1,
            "block_comment_count": len(comment_ids),
            "refresh_page_ids": [document_id]}
        stable = authority._finish(con, actor=actor, action=action,
            operation_id=operation, request_sha256=digest,
            workspace_id=workspace_id, document_id=document_id,
            workspace_epoch=workspace["security_epoch"],
            document_epoch=page["access_epoch"],
            document_sequence=page["document_sequence"],
            event_type="database.trashed" if archived else "database.restored",
            event_payload=result, result=result, fault=fault)
        return {**stable, "replayed": False}


def read_owned_database_trash_result(authority, actor_subject_id, workspace_id,
                                     document_id, database_id, *, archived,
                                     operation_id, expected_document_sequence,
                                     expected_workspace_epoch, expected_document_epoch,
                                     expected_database_version):
    (actor, workspace_id, document_id, database_id, archived, operation, action,
     digest, _, _, _, _) = _request(actor_subject_id, workspace_id, document_id,
        database_id, archived=archived, operation_id=operation_id,
        expected_document_sequence=expected_document_sequence,
        expected_workspace_epoch=expected_workspace_epoch,
        expected_document_epoch=expected_document_epoch,
        expected_database_version=expected_database_version)
    with authority._reader() as con:
        con.execute("BEGIN")
        if not installed(con):
            raise AuthorityUnavailable("database Trash requires the 2422 schema")
        workspace, page = _owner(authority, con, actor,
            workspace_id, document_id)
        database = con.execute("SELECT row_version FROM workspace_collaboration_databases "
            "WHERE id=? AND document_id=?", (database_id, document_id)).fetchone()
        if database is None:
            raise NotFoundOrDenied("not found or permission denied")
        result = authority._replay(con, actor=actor, action=action,
            operation_id=operation, request_sha256=digest,
            authorize=lambda: _owner(authority, con, actor,
                workspace_id, document_id))
        if result is None:
            return {"state": "unknown"}
        return {"state": "committed", "result": result, "current": {
            "archived": is_trashed(con, database_id),
            "document_sequence": page["document_sequence"],
            "workspace_epoch": workspace["security_epoch"],
            "document_epoch": page["access_epoch"],
            "database_version": database["row_version"]}}


def list_trashed_owned_databases(authority, actor_subject_id, workspace_id, *, query=""):
    actor = authority._require_active(actor_subject_id)
    workspace_id = _identifier(workspace_id, field="workspace_id")
    if not isinstance(query, str) or len(query) > 200:
        raise ValueError("query must be at most 200 characters")
    with authority._reader() as con:
        con.execute("BEGIN")
        if not installed(con):
            raise AuthorityUnavailable("database Trash requires the 2422 schema")
        workspace, _ = _owner(authority, con, actor, workspace_id)
        rows = con.execute(
            "SELECT t.database_id,t.document_id,t.block_id,t.archived_at,"
            "t.archived_by_subject_id,db.title AS database_title,"
            "db.row_version AS database_version,d.title AS page_title,"
            "d.document_sequence,d.access_epoch "
            "FROM workspace_collaboration_database_trash_state t "
            "JOIN workspace_collaboration_databases db ON db.id=t.database_id "
            "JOIN workspace_collaboration_documents d ON d.id=t.document_id "
            "WHERE t.workspace_id=? AND d.workspace_id=? "
            "AND d.legacy_owner_subject_id=? AND d.archived=0 "
            "ORDER BY t.archived_at DESC,t.database_id",
            (workspace_id, workspace_id, actor),
        ).fetchmany(1001)
        if len(rows) > 1000:
            raise AuthorityUnavailable("database Trash listing requires pagination")
        items = [{"id": str(row["database_id"]),
            "database_id": str(row["database_id"]),
            "document_id": str(row["document_id"]),
            "block_id": str(row["block_id"]),
            "title": str(row["database_title"]),
            "page_title": str(row["page_title"]),
            "archived": True, "archived_at": int(row["archived_at"]),
            "archived_by_subject_id": str(row["archived_by_subject_id"]),
            "database_version": int(row["database_version"]),
            "document_sequence": int(row["document_sequence"]),
            "document_epoch": int(row["access_epoch"]),
            "workspace_epoch": int(workspace["security_epoch"])}
            for row in rows if query.casefold() in str(row["database_title"]).casefold()
            or query.casefold() in str(row["page_title"]).casefold()]
        authority._require_active(actor)
        return {"contract": CONTRACT, "workspace_id": workspace_id,
            "items": items, "count": len(items)}


def read_trashed_owned_database(authority, actor_subject_id, workspace_id,
                                document_id, block_id):
    """Read one retained database for its owner without lifting normal read barriers."""
    from .database_features import CollaborativeDatabaseAuthority, _database_last_edit
    from . import database_activity
    from .editing_authority import CollaborativeEditingAuthority

    actor = authority._require_active(actor_subject_id)
    workspace_id = _identifier(workspace_id, field="workspace_id")
    document_id = _identifier(document_id, field="document_id")
    block_id = _identifier(block_id, field="block_id")
    with authority._reader() as con:
        con.execute("BEGIN")
        if not installed(con):
            raise AuthorityUnavailable("database Trash requires the 2422 schema")
        _workspace, document = _owner(authority, con, actor, workspace_id, document_id)
        if document["archived"]:
            raise NotFoundOrDenied("not found or permission denied")
        held = con.execute(
            "SELECT t.database_id,t.block_snapshot_json,t.archived_at,"
            "t.archived_by_subject_id,db.title "
            "FROM workspace_collaboration_database_trash_state t "
            "JOIN workspace_collaboration_databases db "
            "ON db.id=t.database_id AND db.document_id=t.document_id "
            "WHERE t.workspace_id=? AND t.document_id=? AND t.block_id=?",
            (workspace_id, document_id, block_id),
        ).fetchone()
        if held is None:
            raise NotFoundOrDenied("not found or permission denied")
        try:
            block = json.loads(str(held["block_snapshot_json"]))
        except (TypeError, ValueError) as exc:
            raise AuthorityUnavailable("database Trash block snapshot is invalid") from exc
        if (not isinstance(block, dict) or block.get("id") != block_id
                or block.get("type") not in {"table", "board", "calendar", "list", "gallery", "timeline"}
                or not isinstance(block.get("props"), dict)
                or block["props"].get("linked_database")
                or block["props"].get("database_id", block_id) != held["database_id"]):
            raise AuthorityUnavailable("database Trash block snapshot changed")
        descriptor = CollaborativeEditingAuthority._editing_snapshot(con, document,
            include_trashed_databases=frozenset({str(held["database_id"])}))
        database = next((item for item in descriptor["databases"]
            if item["id"] == held["database_id"]), None)
        if database is None:
            raise AuthorityUnavailable("database Trash content is unavailable")
        database["rows"] = CollaborativeDatabaseAuthority._materialize_rows(
            con, actor, database)
        activity = con.execute(
            "SELECT last_editor_subject_id,last_edited_at,document_sequence "
            "FROM workspace_collaboration_database_activity WHERE database_id=?",
            (str(held["database_id"]),),
        ).fetchone() if database_activity.installed(con) else None
        authority._require_active(actor)
        return {"contract": CONTRACT, "workspace_id": workspace_id,
            "document_id": document_id, "block_id": block_id,
            "database_id": str(held["database_id"]),
            "title": str(held["title"]),
            "parent_title": str(document["title"]),
            "archived_at": int(held["archived_at"]),
            "archived_by_subject_id": str(held["archived_by_subject_id"]),
            "last_edit": _database_last_edit(activity),
            "block": block, "database": database}


def export_trashed_owned_database(authority, actor_subject_id, workspace_id,
                                  document_id, block_id, *, view_id=None,
                                  include_media=True, with_asset_bundle=False):
    """Export one owner-authorized retained snapshot without restoring it."""
    from code_assist_v1 import notion_database_export_bundle
    from code_assist_v1 import notion_database_export_assets
    from .search_authority import CollaborativeSearchAuthority

    requested_view = _identifier(view_id, field="view_id") if view_id is not None else None
    preview = read_trashed_owned_database(authority, actor_subject_id,
        workspace_id, document_id, block_id)
    database = preview["database"]
    view = next((item for item in database.get("views", [])
        if item.get("id") == requested_view and item.get("state") == "active"), None)
    if requested_view is not None and view is None:
        raise NotFoundOrDenied("not found or permission denied")
    model = {"database": database,
        "config": view.get("config", {}) if view else {},
        "type": view.get("type", "table") if view else "table",
        "include_media": include_media}
    archive = None
    try:
        manifest = notion_database_export_assets.archived_owned_manifest(
            authority, actor_subject_id, workspace_id, document_id,
            preview["database_id"], model) if with_asset_bundle and include_media else ()
        if manifest:
            model["asset_paths"] = {item["file_id"]: item["zip_path"] for item in manifest}
        result = CollaborativeSearchAuthority._export_html_document(None,
            {"title": preview["title"], "icon": "", "blocks": [preview["block"]]},
            "", {block_id: model})
        result["title"] = preview["title"]
        result["database_id"] = preview["database_id"]
        result["row_count"] = len(database.get("rows", []))
        result["csv"] = notion_database_export_bundle.database_csv(model,
            current_view=requested_view is not None)
        result["all_csv"] = notion_database_export_bundle.database_csv(model,
            current_view=False)
        if with_asset_bundle:
            archive = notion_database_export_bundle.html_archive_with_assets(
                result["title"], result["html"], result["csv"],
                identity=result["database_id"], assets=manifest)
        current = read_trashed_owned_database(authority, actor_subject_id,
            workspace_id, document_id, block_id)
        if (_canonical_json(current["database"]) != _canonical_json(database)
                or _canonical_json(current["block"]) != _canonical_json(preview["block"])):
            raise AuthorityUnavailable("database changed while exporting; retry")
        if manifest and notion_database_export_assets.archived_owned_manifest(
                authority, actor_subject_id, workspace_id, document_id,
                preview["database_id"], model) != manifest:
            raise AuthorityUnavailable("archived database file changed while exporting; retry")
        if archive is not None:
            result["archive"] = archive
        return result
    except notion_database_export_bundle.ExportAttachmentChanged as exc:
        if archive is not None:
            archive.close()
        raise AuthorityUnavailable("archived database file changed while exporting; retry") from exc
    except Exception:
        if archive is not None:
            archive.close()
        raise
