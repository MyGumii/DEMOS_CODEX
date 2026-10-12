"""Exact 2423 database-row Trash lifecycle and membership helpers.

The 2423 runtime is intentionally staged. HTTP transport exists behind a
disabled capability; normal readers must keep rows separate from explicit
Trash listing and restore surfaces.
"""
from __future__ import annotations

import sqlite3
import json
import time

from code_assist_v1 import notion_workspace_database_row_trash_migration as migration
from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement_migration
from code_assist_v1 import notion_workspace_custom_emoji_migration as custom_emoji_migration
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as item_layout_migration
from code_assist_v1 import notion_workspace_database_template_default_migration as template_default_migration
from code_assist_v1 import notion_workspace_database_sharing_migration as database_sharing_migration
from code_assist_v1 import notion_workspace_database_upload_scope_migration as upload_scope_migration
from code_assist_v1 import notion_workspace_direct_database_favorite_migration as direct_favorite_migration
from .canonical_authority import (
    AuthorityUnavailable, NotFoundOrDenied, _canonical_json, _identifier,
    _operation_id, _positive, _request_sha256, _require_unlocked_or_temporary,
    _sha256,
)


CONTRACT = "code_assist.notion.database_row_trash.v1"
BATCH_CONTRACT = "code_assist.notion.database_row_trash_batch.v1"
BATCH_ACTION = "collaboration.database_row_trash_batch"
BATCH_RESTORE_ACTION = "collaboration.database_row_restore_batch"


def installed(con: sqlite3.Connection) -> bool:
    marker = con.execute(
        "SELECT version,name,checksum,actual_schema_sha256 "
        "FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return marker is not None and tuple(marker) in {
        (item.TARGET_VERSION, item.TARGET_NAME,
         item.TARGET_CHECKSUM, item.TARGET_ACTUAL_SCHEMA_SHA256)
        for item in (migration, placement_migration, custom_emoji_migration, item_layout_migration, template_default_migration, database_sharing_migration, upload_scope_migration, direct_favorite_migration)
    }


def archived_row_ids(con: sqlite3.Connection, database_id: str) -> frozenset[str]:
    if not installed(con):
        return frozenset()
    return frozenset(str(row[0]) for row in con.execute(
        "SELECT row_id FROM workspace_collaboration_database_row_trash_state "
        "WHERE database_id=?", (database_id,),
    ).fetchall())


def require_active(con: sqlite3.Connection, database_id: str, row_id: str) -> None:
    if row_id in archived_row_ids(con, database_id):
        raise NotFoundOrDenied("not found or permission denied")
    from . import database_row_page_placement
    database_row_page_placement.require_database_placement(con, database_id, row_id)


def hidden_comment_ids(con: sqlite3.Connection, document_id: str) -> frozenset[str]:
    """Hide archived item threads and their descendants in normal readers."""
    if not installed(con):
        return frozenset()
    members = {(str(row[0]), str(row[1])) for row in con.execute(
        "SELECT t.database_id,t.row_id "
        "FROM workspace_collaboration_database_row_trash_state t "
        "JOIN workspace_collaboration_databases d ON d.id=t.database_id "
        "WHERE d.document_id=?", (document_id,),
    ).fetchall()}
    if not members:
        return frozenset()
    hidden: set[str] = set()
    pending: list[tuple[str, str]] = []
    for row in con.execute(
        "SELECT id,parent_comment_id,anchor_json "
        "FROM workspace_collaboration_comments WHERE document_id=?",
        (document_id,),
    ).fetchall():
        try:
            anchor = json.loads(str(row["anchor_json"]))
        except (TypeError, ValueError) as exc:
            raise AuthorityUnavailable("database-row comment anchor is invalid") from exc
        comment_id = str(row["id"])
        if (isinstance(anchor, dict) and anchor.get("kind") == "database_row"
                and (anchor.get("database_id"), anchor.get("row_id")) in members):
            hidden.add(comment_id)
        elif row["parent_comment_id"] is not None:
            pending.append((comment_id, str(row["parent_comment_id"])))
    while pending:
        next_pending = [(comment_id, parent_id)
            for comment_id, parent_id in pending if parent_id not in hidden]
        if len(next_pending) == len(pending):
            break
        hidden.update(comment_id for comment_id, parent_id in pending
            if parent_id in hidden)
        pending = next_pending
    return frozenset(hidden)


def retained_file_ids(con: sqlite3.Connection, document_id: str, *,
                      exclude_database_id: str | None = None) -> frozenset[str]:
    """Keep files referenced by archived rows, using their current DB owner."""
    if not installed(con):
        return frozenset()
    from .database_move import _file_ids

    references: set[str] = set()
    sources = (
        ("workspace_collaboration_database_cells", "value_json"),
        ("workspace_collaboration_database_row_bodies", "blocks_json"),
        ("workspace_collaboration_database_row_appearance", "appearance_json"),
    )
    exclusion = " AND t.database_id<>?" if exclude_database_id is not None else ""
    parameters = ((document_id, exclude_database_id) if exclude_database_id is not None
        else (document_id,))
    for table, column in sources:
        rows = con.execute(
            f"SELECT source.{column} FROM {table} source "
            "JOIN workspace_collaboration_database_row_trash_state t "
            "ON t.row_id=source.row_id "
            "JOIN workspace_collaboration_databases d ON d.id=t.database_id "
            "WHERE d.document_id=?" + exclusion, parameters,
        ).fetchall()
        for row in rows:
            try:
                value = json.loads(str(row[0]))
            except (TypeError, ValueError) as exc:
                raise AuthorityUnavailable("database-row Trash file reference is invalid") from exc
            references.update(_file_ids(value))
    return frozenset(references)


def _request(actor_subject_id, workspace_id, document_id, database_id, row_id,
             *, archived, operation_id, expected_document_sequence,
             expected_workspace_epoch, expected_document_epoch,
             expected_database_version, expected_row_version):
    actor = _identifier(actor_subject_id, field="actor_subject_id")
    workspace = _identifier(workspace_id, field="workspace_id")
    document = _identifier(document_id, field="document_id")
    database = _identifier(database_id, field="database_id")
    row = _identifier(row_id, field="row_id")
    if type(archived) is not bool:
        raise ValueError("archived must be a boolean")
    operation = _operation_id(operation_id)
    sequence = _positive(expected_document_sequence, field="expected_document_sequence")
    workspace_epoch = _positive(expected_workspace_epoch, field="expected_workspace_epoch")
    document_epoch = _positive(expected_document_epoch, field="expected_document_epoch")
    database_version = _positive(expected_database_version, field="expected_database_version")
    row_version = _positive(expected_row_version, field="expected_row_version")
    action = "collaboration.database_row_trash" if archived else "collaboration.database_row_restore"
    digest = _request_sha256(actor, action, {
        "workspace_id": workspace, "document_id": document,
        "database_id": database, "row_id": row, "archived": archived,
        "expected_document_sequence": sequence,
        "expected_workspace_epoch": workspace_epoch,
        "expected_document_epoch": document_epoch,
        "expected_database_version": database_version,
        "expected_row_version": row_version,
    })
    return (actor, workspace, document, database, row, archived, operation,
        action, digest, sequence, workspace_epoch, document_epoch,
        database_version, row_version)


def _authorize(authority, con, actor, workspace_id, document_id,
               database_id, row_id, *, workspace_epoch=None, document_epoch=None):
    if not installed(con):
        raise AuthorityUnavailable("database-row Trash requires the 2423 schema")
    page = authority._document(con, document_id)
    workspace = authority._workspace(con, str(page["workspace_id"]))
    if str(workspace["id"]) != workspace_id or page["archived"]:
        raise NotFoundOrDenied("not found or permission denied")
    if workspace_epoch is None:
        workspace_epoch = int(workspace["security_epoch"])
    if document_epoch is None:
        document_epoch = int(page["access_epoch"])
    workspace, page, role = authority._exact_access(con, actor, document_id,
        minimum_role="edit_content",
        expected_workspace_epoch=workspace_epoch,
        expected_document_epoch=document_epoch)
    database = con.execute(
        "SELECT * FROM workspace_collaboration_databases "
        "WHERE id=? AND document_id=?", (database_id, document_id),
    ).fetchone()
    if database is None:
        raise NotFoundOrDenied("not found or permission denied")
    from . import database_trash
    database_trash.require_active(con, database_id)
    row = con.execute(
        "SELECT * FROM workspace_collaboration_database_rows "
        "WHERE id=? AND database_id=?", (row_id, database_id),
    ).fetchone()
    if row is None:
        raise NotFoundOrDenied("not found or permission denied")
    return workspace, page, database, row, role


def set_database_row_trashed(authority, actor_subject_id, workspace_id,
                             document_id, database_id, row_id, *, archived,
                             operation_id, expected_document_sequence,
                             expected_workspace_epoch, expected_document_epoch,
                             expected_database_version, expected_row_version,
                             fault=None):
    """Stage a reversible row lifecycle mutation behind the disabled gate."""
    from . import database_activity
    from .editing_authority import DocumentSequenceConflict, TargetVersionConflict

    (actor, workspace_id, document_id, database_id, row_id, archived,
     operation, action, digest, sequence, workspace_epoch, document_epoch,
     database_version, row_version) = _request(
        actor_subject_id, workspace_id, document_id, database_id, row_id,
        archived=archived, operation_id=operation_id,
        expected_document_sequence=expected_document_sequence,
        expected_workspace_epoch=expected_workspace_epoch,
        expected_document_epoch=expected_document_epoch,
        expected_database_version=expected_database_version,
        expected_row_version=expected_row_version)

    with authority._transaction() as con:
        def authorize():
            return _authorize(authority, con, actor, workspace_id,
                document_id, database_id, row_id)

        previous = authority._replay(con, actor=actor, action=action,
            operation_id=operation, request_sha256=digest, authorize=authorize)
        if previous is not None:
            return {**previous, "replayed": True}

        workspace, page, database, row, role = _authorize(authority, con,
            actor, workspace_id, document_id, database_id, row_id,
            workspace_epoch=workspace_epoch, document_epoch=document_epoch)
        _require_unlocked_or_temporary(con, page, actor)
        if int(page["document_sequence"]) != sequence:
            raise DocumentSequenceConflict(int(page["document_sequence"]))
        if int(database["row_version"]) != database_version:
            raise TargetVersionConflict(int(database["row_version"]))
        if int(row["row_version"]) != row_version:
            raise TargetVersionConflict(int(row["row_version"]))
        state = con.execute(
            "SELECT * FROM workspace_collaboration_database_row_trash_state "
            "WHERE row_id=? AND database_id=?", (row_id, database_id),
        ).fetchone()
        if (state is None) != archived:
            raise NotFoundOrDenied("database row is not in the requested lifecycle state")
        if state is not None and int(state["archived_row_version"]) + 1 != row_version:
            raise AuthorityUnavailable("archived database row changed after Trash")

        now = int(time.time())
        if archived:
            con.execute(
                "INSERT INTO workspace_collaboration_database_row_trash_state "
                "(row_id,database_id,archive_workspace_id,archive_document_id,"
                "archived_row_version,archived_by_subject_id,archived_at,archive_operation_id) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (row_id, database_id, workspace_id, document_id,
                 row_version, actor, now, operation),
            )
        else:
            deleted = con.execute(
                "DELETE FROM workspace_collaboration_database_row_trash_state "
                "WHERE row_id=? AND database_id=? AND archived_row_version=?",
                (row_id, database_id, row_version - 1),
            )
            if deleted.rowcount != 1:
                raise AuthorityUnavailable("database-row Trash state changed")
        updated_row = con.execute(
            "UPDATE workspace_collaboration_database_rows "
            "SET row_version=row_version+1,updated_at=? "
            "WHERE id=? AND database_id=? AND row_version=?",
            (now, row_id, database_id, row_version),
        )
        if updated_row.rowcount != 1:
            raise TargetVersionConflict(int(row["row_version"]))
        snapshot = authority._editing_snapshot(con, page)
        checksum = _sha256(_canonical_json(snapshot))
        updated_page = con.execute(
            "UPDATE workspace_collaboration_documents SET "
            "document_sequence=document_sequence+1,row_version=row_version+1,"
            "content_sha256=?,updated_at=? WHERE id=? AND document_sequence=?",
            (checksum, now, document_id, sequence),
        )
        if updated_page.rowcount != 1:
            raise DocumentSequenceConflict(sequence)
        database_activity.touch(con, database_ids=(database_id,),
            document_id=document_id, actor_subject_id=actor,
            edited_at=now, document_sequence=sequence + 1)
        result = {"contract": CONTRACT, "workspace_id": workspace_id,
            "document_id": document_id, "database_id": database_id,
            "row_id": row_id, "archived": archived,
            "row_version": row_version + 1,
            "database_version": database_version,
            "document_sequence": sequence + 1,
            "workspace_epoch": int(workspace["security_epoch"]),
            "document_epoch": int(page["access_epoch"]),
            "effective_role": role, "content_sha256": checksum,
            "refresh_page_ids": [document_id]}
        stable = authority._finish(con, actor=actor, action=action,
            operation_id=operation, request_sha256=digest,
            workspace_id=workspace_id, document_id=document_id,
            workspace_epoch=int(workspace["security_epoch"]),
            document_epoch=int(page["access_epoch"]),
            document_sequence=sequence + 1,
            event_type="database.row.trashed" if archived else "database.row.restored",
            event_payload=result, result=result, fault=fault)
        return {**stable, "replayed": False}


def _batch_request(actor_subject_id, workspace_id, document_id, database_id, *,
                   archived, rows, operation_id, expected_document_sequence,
                   expected_workspace_epoch, expected_document_epoch,
                   expected_database_version):
    actor = _identifier(actor_subject_id, field="actor_subject_id")
    workspace = _identifier(workspace_id, field="workspace_id")
    document = _identifier(document_id, field="document_id")
    database = _identifier(database_id, field="database_id")
    if type(archived) is not bool:
        raise ValueError("archived must be a boolean")
    action = BATCH_ACTION if archived else BATCH_RESTORE_ACTION
    operation = _operation_id(operation_id)
    sequence = _positive(expected_document_sequence, field="expected_document_sequence")
    workspace_epoch = _positive(expected_workspace_epoch, field="expected_workspace_epoch")
    document_epoch = _positive(expected_document_epoch, field="expected_document_epoch")
    database_version = _positive(expected_database_version, field="expected_database_version")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError("row Trash batch requires 1 to 100 rows")
    clean_rows = []
    for item in rows:
        if not isinstance(item, dict) or set(item) != {"row_id", "expected_row_version"}:
            raise ValueError("row Trash batch row requires identity and version")
        clean_rows.append({"row_id": _identifier(item["row_id"], field="row_id"),
            "expected_row_version": _positive(item["expected_row_version"],
                field="expected_row_version")})
    if len({item["row_id"] for item in clean_rows}) != len(clean_rows):
        raise ValueError("row Trash batch contains duplicate rows")
    digest = _request_sha256(actor, action, {"workspace_id": workspace,
        "document_id": document, "database_id": database, "rows": clean_rows,
        "expected_document_sequence": sequence,
        "expected_workspace_epoch": workspace_epoch,
        "expected_document_epoch": document_epoch,
        "expected_database_version": database_version})
    return (actor, workspace, document, database, clean_rows, operation,
        action, digest, sequence, workspace_epoch, document_epoch, database_version)


def _batch_authorize(authority, con, actor, workspace_id, document_id,
                     database_id, rows, *, workspace_epoch=None,
                     document_epoch=None):
    first = None
    items = []
    for item in rows:
        authorized = _authorize(authority, con, actor, workspace_id,
            document_id, database_id, item["row_id"],
            workspace_epoch=workspace_epoch, document_epoch=document_epoch)
        if first is None:
            first = authorized
        items.append(authorized[3])
    return (*first[:3], items, first[4])


def set_database_rows_batch_trashed(authority, actor_subject_id, workspace_id,
                                    document_id, database_id, *, archived, rows,
                                    operation_id, expected_document_sequence,
                                    expected_workspace_epoch, expected_document_epoch,
                                    expected_database_version, fault=None):
    """Transition every selected row in one fenced transaction and receipt."""
    from . import database_activity
    from .editing_authority import DocumentSequenceConflict, TargetVersionConflict

    (actor, workspace_id, document_id, database_id, rows, operation, action,
     digest, sequence, workspace_epoch, document_epoch,
     database_version) = _batch_request(actor_subject_id, workspace_id,
        document_id, database_id, archived=archived, rows=rows, operation_id=operation_id,
        expected_document_sequence=expected_document_sequence,
        expected_workspace_epoch=expected_workspace_epoch,
        expected_document_epoch=expected_document_epoch,
        expected_database_version=expected_database_version)
    with authority._transaction() as con:
        authorize = lambda: _batch_authorize(authority, con, actor,
            workspace_id, document_id, database_id, rows)
        previous = authority._replay(con, actor=actor, action=action,
            operation_id=operation, request_sha256=digest, authorize=authorize)
        if previous is not None:
            return {**previous, "replayed": True}
        workspace, page, database, current_rows, role = _batch_authorize(
            authority, con, actor, workspace_id, document_id, database_id,
            rows, workspace_epoch=workspace_epoch, document_epoch=document_epoch)
        _require_unlocked_or_temporary(con, page, actor)
        if int(page["document_sequence"]) != sequence:
            raise DocumentSequenceConflict(int(page["document_sequence"]))
        if int(database["row_version"]) != database_version:
            raise TargetVersionConflict(int(database["row_version"]))
        for item, current in zip(rows, current_rows):
            if int(current["row_version"]) != item["expected_row_version"]:
                raise TargetVersionConflict(int(current["row_version"]))
            state = con.execute("SELECT archived_row_version FROM workspace_collaboration_database_row_trash_state "
                    "WHERE row_id=? AND database_id=?",
                    (item["row_id"], database_id)).fetchone()
            if (state is None) != archived:
                raise NotFoundOrDenied("database row is not in the requested lifecycle state")
            if state is not None and int(state["archived_row_version"]) + 1 != item["expected_row_version"]:
                raise AuthorityUnavailable("archived database row changed after Trash")
        now = int(time.time())
        for item in rows:
            row_id, version = item["row_id"], item["expected_row_version"]
            if archived:
                con.execute("INSERT INTO workspace_collaboration_database_row_trash_state "
                    "(row_id,database_id,archive_workspace_id,archive_document_id,"
                    "archived_row_version,archived_by_subject_id,archived_at,archive_operation_id) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (row_id, database_id, workspace_id, document_id, version, actor, now, operation))
            else:
                deleted = con.execute("DELETE FROM workspace_collaboration_database_row_trash_state "
                    "WHERE row_id=? AND database_id=? AND archived_row_version=?",
                    (row_id, database_id, version - 1))
                if deleted.rowcount != 1:
                    raise AuthorityUnavailable("database-row Trash state changed")
            updated = con.execute("UPDATE workspace_collaboration_database_rows "
                "SET row_version=row_version+1,updated_at=? "
                "WHERE id=? AND database_id=? AND row_version=?",
                (now, row_id, database_id, version))
            if updated.rowcount != 1:
                raise TargetVersionConflict(version)
        checksum = _sha256(_canonical_json(authority._editing_snapshot(con, page)))
        updated_page = con.execute("UPDATE workspace_collaboration_documents SET "
            "document_sequence=document_sequence+1,row_version=row_version+1,"
            "content_sha256=?,updated_at=? WHERE id=? AND document_sequence=?",
            (checksum, now, document_id, sequence))
        if updated_page.rowcount != 1:
            raise DocumentSequenceConflict(sequence)
        database_activity.touch(con, database_ids=(database_id,),
            document_id=document_id, actor_subject_id=actor,
            edited_at=now, document_sequence=sequence + 1)
        result = {"contract": BATCH_CONTRACT, "workspace_id": workspace_id,
            "document_id": document_id, "database_id": database_id,
            "rows": [{"row_id": item["row_id"],
                      "row_version": item["expected_row_version"] + 1}
                     for item in rows], "archived": archived,
            "database_version": database_version,
            "document_sequence": sequence + 1,
            "workspace_epoch": int(workspace["security_epoch"]),
            "document_epoch": int(page["access_epoch"]),
            "effective_role": role, "content_sha256": checksum,
            "refresh_page_ids": [document_id]}
        stable = authority._finish(con, actor=actor, action=action,
            operation_id=operation, request_sha256=digest,
            workspace_id=workspace_id, document_id=document_id,
            workspace_epoch=int(workspace["security_epoch"]),
            document_epoch=int(page["access_epoch"]),
            document_sequence=sequence + 1,
            event_type="database.rows.trashed" if archived else "database.rows.restored",
            event_payload=result,
            result=result, fault=fault)
        return {**stable, "replayed": False}


def trash_database_rows_batch(authority, actor_subject_id, workspace_id,
                              document_id, database_id, **kwargs):
    return set_database_rows_batch_trashed(authority, actor_subject_id,
        workspace_id, document_id, database_id, archived=True, **kwargs)


def restore_database_rows_batch(authority, actor_subject_id, workspace_id,
                                document_id, database_id, **kwargs):
    return set_database_rows_batch_trashed(authority, actor_subject_id,
        workspace_id, document_id, database_id, archived=False, **kwargs)


def read_database_rows_batch_trash_result(authority, actor_subject_id,
                                          workspace_id, document_id, database_id,
                                          *, archived=True, rows, operation_id,
                                          expected_document_sequence,
                                          expected_workspace_epoch,
                                          expected_document_epoch,
                                          expected_database_version):
    (actor, workspace_id, document_id, database_id, rows, operation,
     action, digest, *_fences) = _batch_request(actor_subject_id, workspace_id,
        document_id, database_id, archived=archived, rows=rows, operation_id=operation_id,
        expected_document_sequence=expected_document_sequence,
        expected_workspace_epoch=expected_workspace_epoch,
        expected_document_epoch=expected_document_epoch,
        expected_database_version=expected_database_version)
    with authority._reader() as con:
        con.execute("BEGIN")
        authorize = lambda: _batch_authorize(authority, con, actor,
            workspace_id, document_id, database_id, rows)
        authorize()
        previous = authority._replay(con, actor=actor, action=action,
            operation_id=operation, request_sha256=digest, authorize=authorize)
        return None if previous is None else {**previous, "replayed": True}


def read_database_row_trash_result(authority, actor_subject_id, workspace_id,
                                   document_id, database_id, row_id, *, archived,
                                   operation_id, expected_document_sequence,
                                   expected_workspace_epoch, expected_document_epoch,
                                   expected_database_version, expected_row_version):
    """Read an exact lost-response receipt without repeating the mutation."""
    (actor, workspace_id, document_id, database_id, row_id, _archived,
     operation, action, digest, *_fences) = _request(
        actor_subject_id, workspace_id, document_id, database_id, row_id,
        archived=archived, operation_id=operation_id,
        expected_document_sequence=expected_document_sequence,
        expected_workspace_epoch=expected_workspace_epoch,
        expected_document_epoch=expected_document_epoch,
        expected_database_version=expected_database_version,
        expected_row_version=expected_row_version)
    with authority._reader() as con:
        con.execute("BEGIN")
        _authorize(authority, con, actor, workspace_id, document_id,
            database_id, row_id)
        previous = authority._replay(con, actor=actor, action=action,
            operation_id=operation, request_sha256=digest,
            authorize=lambda: _authorize(authority, con, actor, workspace_id,
                document_id, database_id, row_id))
        return None if previous is None else {**previous, "replayed": True}


def list_trashed_database_rows(authority, actor_subject_id, workspace_id,
                               document_id, database_id, *, limit=100,
                               row_id=None):
    """Read explicit row Trash under the database's current document grant."""
    actor = _identifier(actor_subject_id, field="actor_subject_id")
    workspace_id = _identifier(workspace_id, field="workspace_id")
    document_id = _identifier(document_id, field="document_id")
    database_id = _identifier(database_id, field="database_id")
    if row_id is not None:
        row_id = _identifier(row_id, field="row_id")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("row Trash limit must be between 1 and 200")
    with authority._reader() as con:
        con.execute("BEGIN")
        if not installed(con):
            raise AuthorityUnavailable("database-row Trash requires the 2423 schema")
        page = authority._document(con, document_id)
        workspace = authority._workspace(con, str(page["workspace_id"]))
        if str(workspace["id"]) != workspace_id or page["archived"]:
            raise NotFoundOrDenied("not found or permission denied")
        _workspace, _page, role = authority._exact_access(con, actor, document_id,
            minimum_role="edit_content",
            expected_workspace_epoch=int(workspace["security_epoch"]),
            expected_document_epoch=int(page["access_epoch"]))
        database = con.execute("SELECT id FROM workspace_collaboration_databases "
            "WHERE id=? AND document_id=?", (database_id, document_id)).fetchone()
        if database is None:
            raise NotFoundOrDenied("not found or permission denied")
        from . import database_trash
        database_trash.require_active(con, database_id)
        rows = con.execute(
            "SELECT t.*,r.position,r.row_version "
            "FROM workspace_collaboration_database_row_trash_state t "
            "JOIN workspace_collaboration_database_rows r "
            "ON r.id=t.row_id AND r.database_id=t.database_id "
            "WHERE t.database_id=? "
            + ("AND t.row_id=? " if row_id is not None else "")
            + "ORDER BY t.archived_at DESC,t.row_id LIMIT ?",
            (database_id, row_id, limit) if row_id is not None else (database_id, limit),
        ).fetchall()
        items = []
        for row in rows:
            row_id = str(row["row_id"])
            cells = {str(item["property_id"]): json.loads(str(item["value_json"]))
                for item in con.execute(
                    "SELECT cell.property_id,cell.value_json FROM "
                    "workspace_collaboration_database_cells cell "
                    "JOIN workspace_collaboration_database_properties property "
                    "ON property.id=cell.property_id "
                    "WHERE cell.row_id=? AND property.database_id=? "
                    "ORDER BY cell.property_id", (row_id, database_id),
                ).fetchall()}
            body = con.execute("SELECT blocks_json FROM "
                "workspace_collaboration_database_row_bodies WHERE row_id=?",
                (row_id,)).fetchone()
            appearance = con.execute("SELECT appearance_json FROM "
                "workspace_collaboration_database_row_appearance WHERE row_id=?",
                (row_id,)).fetchone()
            items.append({"row_id": row_id, "database_id": database_id,
                "position": int(row["position"]),
                "row_version": int(row["row_version"]),
                "archived_at": int(row["archived_at"]),
                "archived_by_subject_id": str(row["archived_by_subject_id"]),
                "cells": cells,
                **({"body": {"blocks": json.loads(str(body[0]))}}
                    if body is not None else {}),
                **({"appearance": json.loads(str(appearance[0]))}
                    if appearance is not None else {})})
        return {"contract": CONTRACT, "workspace_id": workspace_id,
            "document_id": document_id, "database_id": database_id,
            "effective_role": role, "items": items, "count": len(items)}
