"""Replayable placement of selected database rows under an ordinary page."""

from __future__ import annotations

import json
import time
from typing import Any, Mapping

from . import (
    database_activity, database_move, database_row_content, database_row_move,
    database_row_page_placement, database_row_trash, database_trash,
    native_team_location, page_move, search_projection,
)
from .canonical_authority import (
    AuthorityUnavailable, NotFoundOrDenied, _canonical_json, _content_descriptor,
    _identifier, _operation_id, _request_sha256, _require_unlocked_or_temporary,
    _sha256, _stable_id,
)
from .editing_authority import DocumentSequenceConflict
from .title_projection import _frames, _generation

CONTRACT = "code_assist.notion.collaborative_database_row_to_page_batch.v1"
ACTION = "collaboration.database.row_to_page_batch"
RETURN_CONTRACT = "code_assist.notion.collaborative_page_to_database_row.v1"
RETURN_ACTION = "collaboration.database.page_to_row"
RETURN_TARGET_CONTRACT = "code_assist.notion.page_to_database_row_target.v1"


def _positive(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"positive {field} required")
    return value


def _row_title(con, row_id: str, database_id: str) -> str:
    cell = con.execute(
        "SELECT cell.value_json FROM workspace_collaboration_database_properties property "
        "LEFT JOIN workspace_collaboration_database_cells cell "
        "ON cell.property_id=property.id AND cell.row_id=? "
        "WHERE property.database_id=? AND property.property_type='title' "
        "ORDER BY property.position,property.id LIMIT 1",
        (row_id, database_id),
    ).fetchone()
    if cell is None or cell["value_json"] is None:
        return "새 페이지"
    value = json.loads(str(cell["value_json"]))
    if not isinstance(value, str):
        raise database_row_move.DatabaseRowMoveShapeUnavailable(
            "item title is not plain text")
    return value.strip()[:1000] or "새 페이지"


def _row_body(con, row_id: str) -> list[dict[str, Any]]:
    body = con.execute(
        "SELECT blocks_json FROM workspace_collaboration_database_row_bodies "
        "WHERE row_id=?", (row_id,),
    ).fetchone()
    blocks = [] if body is None else json.loads(str(body["blocks_json"]))
    if database_move._has_child_page(blocks):
        raise database_row_move.DatabaseRowMoveShapeUnavailable(
            "item child pages require a subtree transfer")
    return database_row_content.clean_blocks(blocks)


def _row_appearance(con, row_id: str) -> dict[str, Any]:
    row = con.execute(
        "SELECT appearance_json FROM workspace_collaboration_database_row_appearance "
        "WHERE row_id=?", (row_id,),
    ).fetchone()
    return {} if row is None else json.loads(str(row["appearance_json"]))


def _page_database_property_plan(con, actor: str, workspace_id: str,
                                 page_id: str, source_database: str,
                                 destination_database: str,
                                 operation: str):
    database_row_move._require_retained_relations(
        con, page_id, source_database)
    database_row_move._validate_inbound_relations(
        con, source_database, {page_id})
    cells = con.execute(
        "SELECT cell.property_id,cell.value_json FROM "
        "workspace_collaboration_database_cells cell "
        "JOIN workspace_collaboration_database_properties property "
        "ON property.id=cell.property_id "
        "WHERE cell.row_id=? AND property.database_id=?",
        (page_id, source_database),
    ).fetchall()
    selected = {str(cell["property_id"]) for cell in cells}
    title = con.execute(
        "SELECT id FROM workspace_collaboration_database_properties "
        "WHERE database_id=? AND property_type='title' "
        "ORDER BY position,id LIMIT 1", (source_database,),
    ).fetchone()
    if title is None:
        raise database_row_move.DatabaseRowMoveShapeUnavailable(
            "source database has no title property")
    selected.add(str(title["id"]))
    for cell in cells:
        try:
            json.loads(str(cell["value_json"]))
        except (TypeError, ValueError) as exc:
            raise database_row_move.DatabaseRowMoveShapeUnavailable(
                "item cell value is malformed") from exc
    return database_row_move._property_plan(
        con, source_database, destination_database, actor, workspace_id,
        operation, selected)


def read_page_return_target(authority, actor_subject_id: str,
                            workspace_id: str, page_document_id: str,
                            destination_document_id: str | None = None,
                            destination_database_id: str | None = None,
                            ) -> dict[str, Any]:
    """Expose an owned DB destination to editors of both placements."""
    actor = _identifier(actor_subject_id, field="actor_subject_id")
    workspace_id = _identifier(workspace_id, field="workspace_id")
    page_id = _identifier(page_document_id, field="page_document_id")
    if (destination_document_id is None) != (destination_database_id is None):
        raise ValueError("destination document and database must be supplied together")
    requested_document = (_identifier(destination_document_id,
        field="destination_document_id") if destination_document_id is not None
        else None)
    requested_database = (_identifier(destination_database_id,
        field="destination_database_id") if destination_database_id is not None
        else None)
    authority._require_active(actor)
    with authority._reader() as con:
        con.execute("BEGIN")
        if not database_row_page_placement.installed(con):
            raise AuthorityUnavailable("page-to-row placement requires the 2424 authority")
        page = page_move._authorize(
            authority, con, actor, workspace_id, page_id, "edit_content")
        parent_id = str(page["parent_document_id"] or "")
        parent = (page_move._authorize(
            authority, con, actor, workspace_id, parent_id, "edit_content")
            if parent_id else None)
        placed = con.execute(
            "SELECT placement.parent_document_id,item.database_id,item.row_version,"
            "database.document_id,database.title "
            "FROM workspace_collaboration_database_row_page_placements placement "
            "JOIN workspace_collaboration_database_rows item ON item.id=placement.row_id "
            "JOIN workspace_collaboration_databases database ON database.id=item.database_id "
            "WHERE placement.row_id=? AND placement.page_document_id=? "
            "AND placement.placement='page'", (page_id, page_id),
        ).fetchone()
        if placed is None or str(placed["parent_document_id"] or "") != parent_id:
            raise NotFoundOrDenied("not found or permission denied")
        source_id = str(placed["document_id"])
        source_database = str(placed["database_id"])
        destination_id = requested_document or source_id
        database_id = requested_database or source_database
        destination = page_move._authorize(
            authority, con, actor, workspace_id, destination_id,
            "edit_content")
        authority._database_for_document(con, destination_id, database_id)
        destination_info = con.execute(
            "SELECT title FROM workspace_collaboration_databases WHERE id=?",
            (database_id,),
        ).fetchone()
        if destination_info is None:
            raise NotFoundOrDenied("not found or permission denied")
        source = (destination if source_id == destination_id else
                  page_move._authorize(authority, con, actor, workspace_id,
                                       source_id, "edit_content"))
        database_trash.require_active(con, source_database)
        database_trash.require_active(con, database_id)
        if page_id in database_row_trash.archived_row_ids(con, source_database):
            raise NotFoundOrDenied("not found or permission denied")
        documents = (page, source, destination) if parent is None else (
            page, parent, source, destination)
        for document in documents:
            _require_unlocked_or_temporary(con, document, actor)
        if source_database != database_id:
            _mapping, additions = _page_database_property_plan(
                con, actor, workspace_id, page_id, source_database,
                database_id, "page-to-database-preview")
            if additions:
                page_move._authorize(
                    authority, con, actor, workspace_id, destination_id,
                    "edit")
        workspace = authority._workspace(con, workspace_id)
        return {
            "contract": RETURN_TARGET_CONTRACT,
            "workspace_id": workspace_id, "page_id": page_id,
            "row_id": page_id, "parent_document_id": parent_id,
            "destination_document_id": destination_id,
            "destination_database_id": database_id,
            "destination_database_title": str(destination_info["title"]),
            "expected_page_sequence": int(page["document_sequence"]),
            "expected_parent_sequence": int(parent["document_sequence"])
                if parent is not None else 0,
            "expected_destination_sequence": int(destination["document_sequence"]),
            "expected_row_version": int(placed["row_version"]),
            "expected_workspace_epoch": int(workspace["security_epoch"]),
            "expected_page_epoch": int(page["access_epoch"]),
            "expected_parent_epoch": int(parent["access_epoch"])
                if parent is not None else 0,
            "expected_destination_epoch": int(destination["access_epoch"]),
        }


def list_page_database_destinations(authority, actor_subject_id: str,
                                    workspace_id: str, page_document_id: str,
                                    query: str = "") -> dict[str, Any]:
    """Find editable database destinations for an active placed row-page."""
    actor = _identifier(actor_subject_id, field="actor_subject_id")
    workspace_id = _identifier(workspace_id, field="workspace_id")
    page_id = _identifier(page_document_id, field="page_document_id")
    if not isinstance(query, str) or len(query) > 200:
        raise ValueError("database destination query must be at most 200 characters")
    source = read_page_return_target(authority, actor, workspace_id, page_id)
    needle = query.strip().casefold()
    items: list[dict[str, str]] = []
    has_more = False
    with authority._reader() as con:
        for database in con.execute(
                "SELECT database.id,database.document_id,database.title "
                "FROM workspace_collaboration_databases database "
                "JOIN workspace_collaboration_documents document "
                "ON document.id=database.document_id "
                "WHERE document.workspace_id=? AND document.archived=0 "
                "ORDER BY database.title,database.id", (workspace_id,)):
            database_id = str(database["id"])
            if database_id == source["destination_database_id"]:
                continue
            title = str(database["title"])
            if needle and needle not in title.casefold():
                continue
            document_id = str(database["document_id"])
            try:
                page = page_move._authorize(
                    authority, con, actor, workspace_id, document_id,
                    "edit_content")
                _require_unlocked_or_temporary(con, page, actor)
                database_trash.require_active(con, database_id)
                _mapping, additions = _page_database_property_plan(
                    con, actor, workspace_id, page_id,
                    source["destination_database_id"], database_id,
                    "page-to-database-preview")
                if additions:
                    page_move._authorize(
                        authority, con, actor, workspace_id, document_id,
                        "edit")
            except (NotFoundOrDenied, AuthorityUnavailable,
                    database_row_move.DatabaseRowMoveShapeUnavailable):
                continue
            if len(items) == 100:
                has_more = True
                break
            items.append({"document_id": document_id,
                          "database_id": database_id, "title": title})
    return {"contract": "code_assist.notion.page_database_destinations.v1",
            "workspace_id": workspace_id, "page_id": page_id,
            "query": query, "items": items, "has_more": has_more}


def move_owned_rows_to_page(authority, actor_subject_id: str, workspace_id: str,
                            source_document_id: str, source_database_id: str,
                            parent_document_id: str, *, rows: list[Mapping[str, Any]],
                            expected_source_sequence: int,
                            expected_parent_sequence: int,
                            expected_workspace_epoch: int,
                            expected_source_epoch: int,
                            expected_parent_epoch: int,
                            operation_id: str,
                            fault: str | None = None) -> dict[str, Any]:
    """Move 1–100 rows as child pages, retaining typed cells in their source DB."""
    actor = _identifier(actor_subject_id, field="actor_subject_id")
    workspace_id = _identifier(workspace_id, field="workspace_id")
    source_document = _identifier(source_document_id, field="source_document_id")
    source_database = _identifier(source_database_id, field="source_database_id")
    parent_document = _identifier(parent_document_id, field="parent_document_id")
    operation = _operation_id(operation_id)
    for name, value in (
        ("source sequence", expected_source_sequence),
        ("parent sequence", expected_parent_sequence),
        ("workspace epoch", expected_workspace_epoch),
        ("source epoch", expected_source_epoch),
        ("parent epoch", expected_parent_epoch),
    ):
        _positive(value, name)
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError("row-to-page move requires 1 to 100 rows")
    requested: dict[str, int] = {}
    for entry in rows:
        if not isinstance(entry, Mapping) or set(entry) != {
                "row_id", "expected_target_version"}:
            raise ValueError("row-to-page entry fields do not match the contract")
        row_id = _identifier(entry["row_id"], field="row_id")
        if row_id in requested:
            raise ValueError("row-to-page targets must be unique")
        requested[row_id] = _positive(entry["expected_target_version"],
                                      "row version")
    if source_document == parent_document and (
            expected_source_sequence != expected_parent_sequence
            or expected_source_epoch != expected_parent_epoch):
        raise ValueError("same-page move requires identical document fences")
    request = {
        "workspace_id": workspace_id,
        "source_document_id": source_document,
        "source_database_id": source_database,
        "parent_document_id": parent_document,
        "rows": [{"row_id": row_id, "expected_target_version": version}
                 for row_id, version in requested.items()],
        "expected_source_sequence": expected_source_sequence,
        "expected_parent_sequence": expected_parent_sequence,
        "expected_workspace_epoch": expected_workspace_epoch,
        "expected_source_epoch": expected_source_epoch,
        "expected_parent_epoch": expected_parent_epoch,
    }
    digest = _request_sha256(actor, ACTION, request)

    def authorize(con):
        if not database_row_page_placement.installed(con):
            raise AuthorityUnavailable("row-to-page placement requires the 2424 authority")
        workspace, source_page, _ = authority._exact_access(
            con, actor, source_document, minimum_role="edit_content",
            expected_workspace_epoch=expected_workspace_epoch,
            expected_document_epoch=expected_source_epoch)
        if str(workspace["id"]) != workspace_id:
            raise NotFoundOrDenied("not found or permission denied")
        authority._database_for_document(con, source_document, source_database)
        database_trash.require_active(con, source_database)
        parent_workspace, parent_page, _ = authority._exact_access(
            con, actor, parent_document, minimum_role="edit_content",
            expected_workspace_epoch=expected_workspace_epoch,
            expected_document_epoch=expected_parent_epoch)
        if str(parent_workspace["id"]) != workspace_id or bool(parent_page["archived"]):
            raise NotFoundOrDenied("not found or permission denied")
        _require_unlocked_or_temporary(con, source_page, actor)
        _require_unlocked_or_temporary(con, parent_page, actor)
        return workspace, source_page, parent_page

    def current(con):
        workspace, source_page, parent_page = authorize(con)
        if int(source_page["document_sequence"]) != expected_source_sequence:
            raise DocumentSequenceConflict(int(source_page["document_sequence"]))
        if int(parent_page["document_sequence"]) != expected_parent_sequence:
            raise DocumentSequenceConflict(int(parent_page["document_sequence"]))
        source_rows = con.execute(
            "SELECT id,position,row_version FROM workspace_collaboration_database_rows "
            "WHERE database_id=? ORDER BY position,id", (source_database,),
        ).fetchall()
        selected = [str(item["id"]) for item in source_rows
                    if str(item["id"]) in requested]
        if len(selected) != len(requested):
            raise NotFoundOrDenied("not found or permission denied")
        reusable_shells = {}
        for item in source_rows:
            row_id = str(item["id"])
            if row_id not in requested:
                continue
            database_row_trash.require_active(con, source_database, row_id)
            authority._check_target_version(item, requested[row_id])
            shell = con.execute(
                "SELECT * FROM workspace_collaboration_documents WHERE id=?",
                (row_id,),
            ).fetchone()
            if shell is not None:
                placement = con.execute(
                    "SELECT placement,parent_document_id FROM "
                    "workspace_collaboration_database_row_page_placements "
                    "WHERE row_id=? AND page_document_id=?",
                    (row_id, row_id),
                ).fetchone()
                archived_batch = con.execute(
                    "SELECT 1 FROM workspace_collaboration_page_archive_state "
                    "WHERE document_id=?", (row_id,),
                ).fetchone()
                child = con.execute(
                    "SELECT 1 FROM workspace_collaboration_documents "
                    "WHERE parent_document_id=? LIMIT 1", (row_id,),
                ).fetchone()
                if (placement is None or placement["placement"] != "database"
                        or placement["parent_document_id"] is not None
                        or str(shell["workspace_id"]) != workspace_id
                        or not bool(shell["archived"])
                        or shell["parent_document_id"] is not None
                        or archived_batch is not None or child is not None):
                    raise database_row_move.DatabaseRowMoveShapeUnavailable(
                        "item page identity is already occupied")
                reusable_shells[row_id] = (
                    int(shell["row_version"]), int(shell["revision"]),
                    int(shell["document_sequence"]), int(shell["access_epoch"]),
                    str(shell["content_sha256"]), _generation(con, row_id),
                )
            for cell in con.execute(
                "SELECT value_json FROM workspace_collaboration_database_cells "
                "WHERE row_id=?", (row_id,),
            ).fetchall():
                try:
                    json.loads(str(cell["value_json"]))
                except (TypeError, ValueError) as exc:
                    raise database_row_move.DatabaseRowMoveShapeUnavailable(
                        "item cell value is malformed") from exc
            for block in _row_body(con, row_id):
                existing_block = con.execute(
                    "SELECT document_id FROM workspace_collaboration_blocks "
                    "WHERE id=?", (block["id"],),
                ).fetchone()
                if existing_block is not None and (
                        row_id not in reusable_shells
                        or str(existing_block["document_id"]) != row_id):
                    raise database_row_move.DatabaseRowMoveShapeUnavailable(
                        "item body block identity is already occupied")
        database_row_move._validate_inbound_relations(
            con, source_database, set(selected))
        comments = database_row_move._comment_plan(
            con, source_document, source_database, set(selected))
        titles = {row_id: _row_title(con, row_id, source_database)
                  for row_id in selected}
        return (workspace, source_page, parent_page, selected, comments,
                titles, reusable_shells)

    with authority._reader() as con:
        con.execute("BEGIN")
        replay = authority._replay(
            con, actor=actor, action=ACTION, operation_id=operation,
            request_sha256=digest, authorize=lambda: authorize(con))
        if replay is not None:
            return replay
        (_workspace, _source_page, parent_page, selected, _comments, titles,
         reusable_shells) = current(con)
        generation = _generation(con, parent_document)
        stamp = page_move._stamp(parent_page, generation)
        insert_many = [{
            "id": _stable_id("child_link", actor, operation, row_id),
            "type": "child_page", "content": titles[row_id],
            "props": {"page_id": row_id},
        } for row_id in selected]
        parent_source = {
            "id": parent_document, "generation": generation,
            "frames": _frames(con, parent_document, generation),
            "snapshot": authority._editing_snapshot(con, parent_page),
            "insert": None, "insert_many": insert_many,
        }
        parent_source.update(page_move.parent_rebase.capture_witness(
            con, parent_document, generation, parent_source["snapshot"]))
    delta = page_move._prepare_deltas(
        {parent_document: parent_source}, selected[0], operation)[parent_document]
    page_move.parent_rebase.validate_deltas({parent_document: parent_source}, {parent_document: delta}, selected[0])
    if (not delta["changed"] or len(delta["placed_many"] or []) != len(selected)
            or [item["page_id"] for item in delta["placed_many"]] != selected):
        raise AuthorityUnavailable("child-page link preparation changed the selected rows")

    with authority._transaction() as con:
        replay = authority._replay(
            con, actor=actor, action=ACTION, operation_id=operation,
            request_sha256=digest, authorize=lambda: authorize(con))
        if replay is not None:
            return replay
        (workspace, source_page, parent_page, moving, comments, titles,
         current_shells) = current(con)
        if (moving != selected or current_shells != reusable_shells
                or titles != {row_id: item["content"]
                              for row_id, item in zip(selected, insert_many)}):
            raise DocumentSequenceConflict(int(source_page["document_sequence"]))
        if page_move._stamp(parent_page, _generation(con, parent_document)) != stamp:
            raise DocumentSequenceConflict(int(parent_page["document_sequence"]))
        now = int(time.time())
        copied_files = 0
        for row_id in moving:
            row_comments = tuple(item for item in comments if
                json.loads(item[2]).get("row_id") == row_id)
            file_mapping = database_row_move._cross_document_files(
                con, actor, workspace_id, source_document, row_id, [row_id],
                row_comments, operation, include_cells=False)
            appearance = _row_appearance(con, row_id)
            appearance = database_move._remap_database_files(appearance,
                                                             file_mapping)
            blocks = database_move._remap_database_files(
                _row_body(con, row_id), file_mapping)
            title = titles[row_id]
            icon = str(appearance.get("icon") or "")
            cover = str(appearance.get("cover") or "")
            settings = ({"cover_position": appearance["cover_position"]}
                        if "cover_position" in appearance else {})
            descriptor = _content_descriptor(
                title=title, icon=icon, cover=cover, settings=settings,
                blocks=blocks)
            checksum = _sha256(_canonical_json(descriptor))
            position = con.execute(
                "SELECT COALESCE(MAX(position),-1)+1 FROM workspace_collaboration_documents "
                "WHERE workspace_id=? AND parent_document_id=? AND archived=0",
                (workspace_id, parent_document),
            ).fetchone()[0]
            shell_info = reusable_shells.get(row_id)
            if shell_info is None:
                revision = sequence = epoch = 1
                con.execute(
                    "INSERT INTO workspace_collaboration_documents("
                    "id,workspace_id,legacy_owner_subject_id,parent_document_id,"
                    "title,icon,cover,settings_json,position,archived,archived_at,revision,"
                    "document_sequence,access_epoch,row_version,content_sha256,"
                    "imported_snapshot_id,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,0,NULL,1,1,1,1,?,NULL,?,?)",
                    (row_id, workspace_id, actor, parent_document, title, icon,
                     cover, _canonical_json(settings), position, checksum, now,
                     now),
                )
            else:
                old_version, old_revision, old_sequence, old_epoch, _, old_generation = shell_info
                revision, sequence, epoch = (old_revision + 1,
                                             old_sequence + 1, old_epoch + 1)
                native_team_location.replace_root(
                    con, workspace_id, row_id, None, actor, now)
                changed = con.execute(
                    "UPDATE workspace_collaboration_documents SET "
                    "parent_document_id=?,title=?,icon=?,cover=?,settings_json=?,"
                    "position=?,archived=0,archived_at=NULL,revision=?,"
                    "document_sequence=?,access_epoch=?,row_version=row_version+1,"
                    "content_sha256=?,updated_at=? WHERE id=? AND archived=1 "
                    "AND row_version=?",
                    (parent_document, title, icon, cover,
                     _canonical_json(settings), position, revision, sequence,
                     epoch, checksum, now, row_id, old_version),
                )
                if changed.rowcount != 1:
                    raise AuthorityUnavailable("archived item page changed")
                con.execute(
                    "DELETE FROM workspace_collaboration_blocks WHERE document_id=?",
                    (row_id,),
                )
                con.execute(
                    "UPDATE workspace_collaboration_crdt_generations SET "
                    "state='retired',retired_at=? WHERE document_id=? "
                    "AND generation=? AND state='current'",
                    (now, row_id, old_generation),
                )
                con.execute(
                    "INSERT INTO workspace_collaboration_crdt_generations("
                    "document_id,generation,yjs_document_key,state,created_at,retired_at) "
                    "VALUES(?,?,?,'current',?,NULL)",
                    (row_id, old_generation + 1,
                     f"document:{row_id}:rich-text:g{old_generation + 1}", now),
                )
            authority._insert_blocks(con, row_id, blocks, now=now)
            con.execute(
                "INSERT INTO workspace_collaboration_revisions("
                "document_id,revision,document_sequence,content_sha256,snapshot_json,"
                "actor_subject_id,operation_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (row_id, revision, sequence, checksum,
                 _canonical_json(descriptor), actor, operation, now),
            )
            if shell_info is None:
                authority._record_document_creation_successor_state(
                    con, workspace_id=workspace_id, document_id=row_id,
                    content_sha256=checksum, actor_subject_id=actor,
                    operation_id=operation, created_at=now)
                con.execute(
                    "INSERT INTO workspace_collaboration_native_pages("
                    "document_id,workspace_id,creation_kind,source_document_id,"
                    "created_by_subject_id,operation_id,created_at) "
                    "VALUES(?,?,'create',NULL,?,?,?)",
                    (row_id, workspace_id, actor, operation, now),
                )
            else:
                command_sequence = int(con.execute(
                    "SELECT COALESCE(MAX(command_sequence),0) FROM "
                    "workspace_collaboration_typed_commands WHERE document_id=?",
                    (row_id,),
                ).fetchone()[0])
                con.execute(
                    "INSERT INTO workspace_collaboration_revision_checkpoints("
                    "id,workspace_id,document_id,revision,document_sequence,"
                    "crdt_generation,crdt_snapshot_id,through_update_sequence,"
                    "through_structure_command_sequence,content_sha256,"
                    "actor_subject_id,operation_id,checkpoint_reason,"
                    "publication_revision_id,created_at) "
                    "VALUES(?,?,?,?,?,?,NULL,0,?,?,?,?,'automatic',NULL,?)",
                    (_stable_id("hist", row_id, f"r{revision}", operation),
                     workspace_id, row_id, revision, sequence,
                     old_generation + 1, command_sequence, checksum, actor,
                     operation, now),
                )
            database_row_move._copy_cross_document_files(
                con, file_mapping, actor, workspace_id, source_document,
                row_id, operation, now)
            copied_files += len(file_mapping)
            if shell_info is None:
                con.execute(
                    "INSERT INTO workspace_collaboration_database_row_page_placements("
                    "row_id,page_document_id,placement,parent_document_id,"
                    "created_by_subject_id,created_operation_id,row_version,created_at,updated_at) "
                    "VALUES(?,?,'page',?,?,?,1,?,?)",
                    (row_id, row_id, parent_document, actor, operation, now, now),
                )
            else:
                con.execute(
                    "UPDATE workspace_collaboration_database_row_page_placements "
                    "SET placement='page',parent_document_id=?,"
                    "row_version=row_version+1,updated_at=? "
                    "WHERE row_id=? AND placement='database'",
                    (parent_document, now, row_id),
                )
            changed = con.execute(
                "UPDATE workspace_collaboration_database_rows SET "
                "row_version=row_version+1,updated_at=? "
                "WHERE id=? AND database_id=? AND row_version=?",
                (now, row_id, source_database, requested[row_id]),
            )
            if changed.rowcount != 1:
                raise AuthorityUnavailable("item changed during page move")
            for comment_id, version, previous in row_comments:
                comment = con.execute(
                    "SELECT content FROM workspace_collaboration_comments "
                    "WHERE id=? AND document_id=? AND row_version=?",
                    (comment_id, source_document, version),
                ).fetchone()
                if comment is None:
                    raise AuthorityUnavailable("item comment changed during page move")
                content = database_move._remap_database_files(
                    str(comment["content"]), file_mapping)
                changed = con.execute(
                    "UPDATE workspace_collaboration_comments SET document_id=?,"
                    "anchor_json='{}',content=?,row_version=row_version+1,updated_at=? "
                    "WHERE id=? AND document_id=? AND row_version=? AND anchor_json=?",
                    (row_id, content, now, comment_id, source_document, version,
                     previous),
                )
                if changed.rowcount != 1:
                    raise AuthorityUnavailable("item comment changed during page move")
                for notification in con.execute(
                    "SELECT id,payload_json FROM workspace_collaboration_notifications "
                    "WHERE comment_id=?", (comment_id,),
                ).fetchall():
                    payload = json.loads(str(notification["payload_json"]))
                    payload["document_id"] = row_id
                    con.execute(
                        "UPDATE workspace_collaboration_notifications SET "
                        "document_id=?,document_epoch=?,payload_json=?,"
                        "notification_epoch=notification_epoch+1,updated_at=? WHERE id=?",
                        (row_id, epoch, _canonical_json(payload), now,
                         str(notification["id"])),
                    )
            child_result = {
                "contract": CONTRACT, "workspace_id": workspace_id,
                "row_id": row_id, "page_id": row_id,
                "parent_document_id": parent_document,
                "document_sequence": sequence,
            }
            child_operation = _stable_id("row_page_child", actor, operation,
                                         row_id)
            authority._finish(
                con, actor=actor,
                action="collaboration.database.row_to_page_child",
                operation_id=child_operation,
                request_sha256=_request_sha256(actor, ACTION, child_result),
                workspace_id=workspace_id, document_id=row_id,
                workspace_epoch=expected_workspace_epoch, document_epoch=epoch,
                document_sequence=sequence, event_type="page.created",
                event_payload=child_result, result=child_result, fault=None)
        for link, prepared in zip(insert_many, delta["placed_many"]):
            if link["id"] != prepared["id"] or link["props"]["page_id"] != prepared["page_id"]:
                raise AuthorityUnavailable("prepared child link identity changed")
            page_move._structural_link(
                authority, con, actor, workspace, parent_page,
                "block.insert", link["id"],
                {**link, "after_block_id": prepared["after_block_id"]},
                operation, now,
                operation_action=ACTION,
                receipt_action="collaboration.database.row_to_page_link",
                identity_prefix="rplcmd")
        page_move._append_link_delta(
            authority, con, actor, workspace, parent_page,
            delta["payload"], generation, operation, now,
            operation_action=ACTION,
            receipt_action="collaboration.database.row_to_page_delta",
            client_id="row-to-page-service", binding_contract=CONTRACT,
            identity_prefix="rplupd")
        source_sequence = int(authority._document(
            con, source_document)["document_sequence"]) + 1
        source_hash = _sha256(_canonical_json(authority._editing_snapshot(
            con, authority._document(con, source_document))))
        con.execute(
            "UPDATE workspace_collaboration_documents SET document_sequence=?,"
            "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
            (source_sequence, source_hash, now, source_document),
        )
        database_activity.touch(
            con, database_ids=(source_database,), document_id=source_document,
            actor_subject_id=actor, edited_at=now,
            document_sequence=source_sequence)
        con.execute("UPDATE workspaces SET row_version=row_version+1,updated_at=? "
                    "WHERE id=?", (now, workspace_id))
        parent_sequence = int(authority._document(
            con, parent_document)["document_sequence"])
        result = {
            "contract": CONTRACT, "workspace_id": workspace_id,
            "source_document_id": source_document,
            "source_database_id": source_database,
            "parent_document_id": parent_document,
            "command_kind": "database.rows.move_to_page",
            "document_sequence": source_sequence,
            "parent_document_sequence": parent_sequence,
            "content_sha256": source_hash,
            "copied_file_count": copied_files,
            "rows": [{"row_id": row_id, "page_id": row_id,
                      "target_version": requested[row_id] + 1}
                     for row_id in moving],
        }
        return authority._finish(
            con, actor=actor, action=ACTION, operation_id=operation,
            request_sha256=digest, workspace_id=workspace_id,
            document_id=source_document,
            workspace_epoch=expected_workspace_epoch,
            document_epoch=expected_source_epoch,
            document_sequence=source_sequence,
            event_type="database.surface.committed",
            event_payload=result, result=result, fault=fault)


def return_page_to_owned_database(
        authority, actor_subject_id: str, workspace_id: str, page_document_id: str,
        destination_document_id: str, destination_database_id: str, *,
        expected_page_sequence: int, expected_parent_sequence: int,
        expected_destination_sequence: int, expected_row_version: int,
        expected_workspace_epoch: int, expected_page_epoch: int,
        expected_parent_epoch: int, expected_destination_epoch: int,
        operation_id: str, fault: str | None = None) -> dict[str, Any]:
    """Place a row-page in an owned DB without losing its hidden cells."""
    actor = _identifier(actor_subject_id, field="actor_subject_id")
    workspace_id = _identifier(workspace_id, field="workspace_id")
    page_id = _identifier(page_document_id, field="page_document_id")
    destination_document = _identifier(
        destination_document_id, field="destination_document_id")
    destination_database = _identifier(
        destination_database_id, field="destination_database_id")
    operation = _operation_id(operation_id)
    for field, value in (
        ("page sequence", expected_page_sequence),
        ("destination sequence", expected_destination_sequence),
        ("row version", expected_row_version),
        ("workspace epoch", expected_workspace_epoch),
        ("page epoch", expected_page_epoch),
        ("destination epoch", expected_destination_epoch),
    ):
        _positive(value, field)
    for field, value in (("parent sequence", expected_parent_sequence),
                         ("parent epoch", expected_parent_epoch)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"nonnegative {field} required")
    request = {
        "workspace_id": workspace_id, "page_document_id": page_id,
        "destination_document_id": destination_document,
        "destination_database_id": destination_database,
        "expected_page_sequence": expected_page_sequence,
        "expected_parent_sequence": expected_parent_sequence,
        "expected_destination_sequence": expected_destination_sequence,
        "expected_row_version": expected_row_version,
        "expected_workspace_epoch": expected_workspace_epoch,
        "expected_page_epoch": expected_page_epoch,
        "expected_parent_epoch": expected_parent_epoch,
        "expected_destination_epoch": expected_destination_epoch,
    }
    digest = _request_sha256(actor, RETURN_ACTION, request)

    def replay(con):
        def authorize():
            page_move._authorize(
                authority, con, actor, workspace_id, destination_document,
                "edit_content")
        return authority._replay(
            con, actor=actor, action=RETURN_ACTION, operation_id=operation,
            request_sha256=digest, authorize=authorize)

    def current(con):
        if not database_row_page_placement.installed(con):
            raise AuthorityUnavailable("page-to-row placement requires the 2424 authority")
        workspace, page, _ = authority._exact_access(
            con, actor, page_id, minimum_role="edit_content",
            expected_workspace_epoch=expected_workspace_epoch,
            expected_document_epoch=expected_page_epoch)
        if str(workspace["id"]) != workspace_id or bool(page["archived"]):
            raise NotFoundOrDenied("not found or permission denied")
        destination_workspace, destination, _ = authority._exact_access(
            con, actor, destination_document, minimum_role="edit_content",
            expected_workspace_epoch=expected_workspace_epoch,
            expected_document_epoch=expected_destination_epoch)
        if str(destination_workspace["id"]) != workspace_id:
            raise NotFoundOrDenied("not found or permission denied")
        authority._database_for_document(
            con, destination_document, destination_database)
        database_trash.require_active(con, destination_database)
        placed = con.execute(
            "SELECT placement.parent_document_id,item.database_id,item.row_version "
            "FROM workspace_collaboration_database_row_page_placements placement "
            "JOIN workspace_collaboration_database_rows item ON item.id=placement.row_id "
            "WHERE placement.row_id=? AND placement.page_document_id=? "
            "AND placement.placement='page'", (page_id, page_id),
        ).fetchone()
        if placed is None:
            raise NotFoundOrDenied("not found or permission denied")
        source_database = str(placed["database_id"])
        source_database_row = con.execute(
            "SELECT document_id FROM workspace_collaboration_databases WHERE id=?",
            (source_database,),
        ).fetchone()
        if source_database_row is None:
            raise NotFoundOrDenied("not found or permission denied")
        source_document = str(source_database_row["document_id"])
        source = (destination if source_document == destination_document else
                  page_move._authorize(authority, con, actor, workspace_id,
                                       source_document, "edit_content"))
        database_trash.require_active(con, source_database)
        parent_id = str(page["parent_document_id"] or "")
        if str(placed["parent_document_id"] or "") != parent_id:
            raise database_row_move.DatabaseRowMoveShapeUnavailable(
                "page parent changed outside the row placement")
        parent = None
        if parent_id:
            parent_workspace, parent, _ = authority._exact_access(
                con, actor, parent_id, minimum_role="edit_content",
                expected_workspace_epoch=expected_workspace_epoch,
                expected_document_epoch=expected_parent_epoch)
            if str(parent_workspace["id"]) != workspace_id:
                raise NotFoundOrDenied("not found or permission denied")
        elif expected_parent_sequence != 0 or expected_parent_epoch != 0:
            raise ValueError("root return requires zero parent fences")
        documents = (page, source, destination) if parent is None else (
            page, parent, source, destination)
        for document in documents:
            _require_unlocked_or_temporary(con, document, actor)
        fences = [(page, expected_page_sequence),
                  (destination, expected_destination_sequence)]
        if parent is not None:
            fences.append((parent, expected_parent_sequence))
        for document, expected in fences:
            if int(document["document_sequence"]) != expected:
                raise DocumentSequenceConflict(int(document["document_sequence"]))
        if int(placed["row_version"]) != expected_row_version:
            raise AuthorityUnavailable("item changed during page placement")
        if page_id in database_row_trash.archived_row_ids(con, source_database):
            raise NotFoundOrDenied("not found or permission denied")
        if con.execute(
            "SELECT 1 FROM workspace_collaboration_documents "
            "WHERE parent_document_id=? AND archived=0 LIMIT 1", (page_id,),
        ).fetchone():
            raise database_row_move.DatabaseRowMoveShapeUnavailable(
                "page descendants require a subtree transfer")
        comments = tuple((str(item["id"]), int(item["row_version"]),
                          str(item["anchor_json"])) for item in con.execute(
            "SELECT id,row_version,anchor_json FROM workspace_collaboration_comments "
            "WHERE document_id=? ORDER BY id", (page_id,),
        ).fetchall())
        return (workspace, page, parent, source, destination, parent_id,
                source_database, source_document, comments)

    with authority._reader() as con:
        con.execute("BEGIN")
        previous = replay(con)
        if previous is not None:
            return previous
        (_workspace, page, parent, _source, _destination, parent_id,
         source_database, source_document, comments) = current(con)
        generation = _generation(con, parent_id) if parent is not None else None
        parent_stamp = (page_move._stamp(parent, generation)
                        if parent is not None else None)
        page_generation = _generation(con, page_id)
        page_stamp = page_move._stamp(page, page_generation)
        page_snapshot = authority._editing_snapshot(con, page)
        if page_snapshot.get("databases"):
            raise database_row_move.DatabaseRowMoveShapeUnavailable(
                "page databases require a subtree transfer")
        page_frames = _frames(con, page_id, page_generation)
        parent_source = ({"id": parent_id, "generation": generation,
            "frames": _frames(con, parent_id, generation),
            "snapshot": authority._editing_snapshot(con, parent),
            "insert": None, "expected_match_count": 1}
            if parent is not None else None)
        if parent_source is not None:
            parent_source.update(page_move.parent_rebase.capture_witness(
                con, parent_id, generation, parent_source["snapshot"]))
    live = search_projection.materialize([page_frames])[0]
    title = live["title"] if live["title"] is not None else page_snapshot["title"]
    if not isinstance(title, str) or len(title) > 1000:
        raise database_row_move.DatabaseRowMoveShapeUnavailable(
            "page title is not a supported item title")
    blocks = live["blocks"] if live["blocks"] is not None else page_snapshot["blocks"]
    if live["blocks"] is not None and (
            [item["id"] for item in blocks]
            != [item["id"] for item in page_snapshot["blocks"]]):
        raise database_row_move.DatabaseRowMoveShapeUnavailable(
            "page block structure changed during live replay")
    blocks = database_row_content.clean_blocks(blocks)
    if database_move._has_child_page(blocks):
        raise database_row_move.DatabaseRowMoveShapeUnavailable(
            "page child links require a subtree transfer")
    delta = (page_move._prepare_deltas(
        {parent_id: parent_source}, page_id, operation)[parent_id]
        if parent_source is not None else None)
    if delta is not None:
        page_move.parent_rebase.validate_deltas({parent_id: parent_source}, {parent_id: delta}, page_id)
    if delta is not None and (
            not delta["changed"] or not delta["removed"] or delta["placed"]):
        raise AuthorityUnavailable("page child link could not be removed")

    with authority._transaction() as con:
        previous = replay(con)
        if previous is not None:
            return previous
        (workspace, page, parent, source, destination, current_parent,
         current_source_database, current_source_document,
         current_comments) = current(con)
        if (current_parent != parent_id or current_comments != comments
                or current_source_database != source_database
                or current_source_document != source_document
                or (parent is not None and page_move._stamp(
                    parent, _generation(con, parent_id)) != parent_stamp)
                or page_move._stamp(page, _generation(con, page_id)) != page_stamp):
            raise DocumentSequenceConflict(int(page["document_sequence"]))
        now = int(time.time())
        title_cell = con.execute(
            "SELECT property.id,cell.row_version FROM "
            "workspace_collaboration_database_properties property "
            "LEFT JOIN workspace_collaboration_database_cells cell "
            "ON cell.property_id=property.id AND cell.row_id=? "
            "WHERE property.database_id=? AND property.property_type='title' "
            "ORDER BY property.position,property.id LIMIT 1",
            (page_id, source_database),
        ).fetchone()
        if title_cell is None:
            raise database_row_move.DatabaseRowMoveShapeUnavailable(
                "destination database has no title property")
        if title_cell["row_version"] is None:
            con.execute(
                "INSERT INTO workspace_collaboration_database_cells("
                "row_id,property_id,value_json,row_version,updated_at) "
                "VALUES(?,?,?,1,?)",
                (page_id, str(title_cell["id"]), _canonical_json(title), now),
            )
        else:
            con.execute(
                "UPDATE workspace_collaboration_database_cells SET value_json=?,"
                "row_version=row_version+1,updated_at=? WHERE row_id=? AND property_id=?",
                (_canonical_json(title), now, page_id, str(title_cell["id"])),
            )
        property_ids: dict[str, str] = {}
        new_properties: list[tuple[str, str, str, str, int, str]] = []
        if source_database != destination_database:
            property_ids, new_properties = _page_database_property_plan(
                con, actor, workspace_id, page_id, source_database,
                destination_database, operation)
            if new_properties:
                authority._exact_access(
                    con, actor, destination_document, minimum_role="edit",
                    expected_workspace_epoch=expected_workspace_epoch,
                    expected_document_epoch=expected_destination_epoch)
                for property_id, database_id, name, kind, position, config in new_properties:
                    con.execute(
                        "INSERT INTO workspace_collaboration_database_properties("
                        "id,database_id,name,property_type,position,config_json,"
                        "row_version,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?)",
                        (property_id, database_id, name, kind, position, config,
                         now, now),
                    )
        appearance = {
            "icon": str(page["icon"] or ""), "cover": str(page["cover"] or ""),
            "cover_position": float(json.loads(str(page["settings_json"])).get(
                "cover_position", 0.5)),
        }
        con.execute(
            "INSERT INTO workspace_collaboration_database_row_bodies(row_id,blocks_json) "
            "VALUES(?,?) ON CONFLICT(row_id) DO UPDATE SET blocks_json=excluded.blocks_json",
            (page_id, _canonical_json(blocks)),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_row_appearance("
            "row_id,appearance_json) VALUES(?,?) "
            "ON CONFLICT(row_id) DO UPDATE SET appearance_json=excluded.appearance_json",
            (page_id, _canonical_json(appearance)),
        )
        file_mapping = database_row_move._cross_document_files(
            con, actor, workspace_id, page_id, destination_document,
            [page_id], comments, operation, include_cells=False)
        database_row_move._copy_cross_document_files(
            con, file_mapping, actor, workspace_id, page_id,
            destination_document, operation, now)
        cell_file_mapping = (database_row_move._cross_document_files(
            con, actor, workspace_id, source_document, destination_document,
            [page_id], (), operation + "-cells", include_cells=True,
            include_row_content=False)
            if source_database != destination_database
            and source_document != destination_document else {})
        if cell_file_mapping:
            database_row_move._copy_cross_document_files(
                con, cell_file_mapping, actor, workspace_id, source_document,
                destination_document, operation + "-cells", now)
        if source_database != destination_database:
            for cell in con.execute(
                    "SELECT cell.property_id,cell.value_json,cell.row_version FROM "
                    "workspace_collaboration_database_cells cell "
                    "JOIN workspace_collaboration_database_properties property "
                    "ON property.id=cell.property_id "
                    "WHERE cell.row_id=? AND property.database_id=? "
                    "ORDER BY cell.property_id",
                    (page_id, source_database)).fetchall():
                source_property = str(cell["property_id"])
                target_property = property_ids.get(source_property)
                if target_property is None:
                    continue  # Retain an unmatched relation under its source schema.
                if con.execute(
                        "SELECT 1 FROM workspace_collaboration_database_cells "
                        "WHERE row_id=? AND property_id=?",
                        (page_id, target_property)).fetchone():
                    raise database_row_move.DatabaseRowMoveShapeUnavailable(
                        "destination has a retained value for the same property")
                value = database_move._remap_database_files(
                    json.loads(str(cell["value_json"])), cell_file_mapping)
                changed = con.execute(
                    "UPDATE workspace_collaboration_database_cells SET property_id=?,"
                    "value_json=?,row_version=row_version+1,updated_at=? "
                    "WHERE row_id=? AND property_id=? AND row_version=? "
                    "AND value_json=?",
                    (target_property,
                     _canonical_json(value), now, page_id,
                     str(cell["property_id"]), int(cell["row_version"]),
                     str(cell["value_json"])),
                )
                if changed.rowcount != 1:
                    raise AuthorityUnavailable("item cell changed during page return")
        for table, column in (
            ("workspace_collaboration_database_row_bodies", "blocks_json"),
            ("workspace_collaboration_database_row_appearance", "appearance_json"),
        ):
            value = con.execute(
                f"SELECT {column} FROM {table} WHERE row_id=?", (page_id,),
            ).fetchone()[0]
            remapped = database_move._remap_database_files(
                json.loads(str(value)), file_mapping)
            con.execute(
                f"UPDATE {table} SET {column}=? WHERE row_id=?",
                (_canonical_json(remapped), page_id),
            )
        anchor = _canonical_json({"kind": "database_row",
                                  "database_id": destination_database,
                                  "row_id": page_id})
        for comment_id, version, previous_anchor in comments:
            content = con.execute(
                "SELECT content FROM workspace_collaboration_comments "
                "WHERE id=? AND document_id=? AND row_version=?",
                (comment_id, page_id, version),
            ).fetchone()
            if content is None:
                raise AuthorityUnavailable("page comment changed during return")
            remapped = database_move._remap_database_files(
                str(content["content"]), file_mapping)
            changed = con.execute(
                "UPDATE workspace_collaboration_comments SET document_id=?,"
                "anchor_json=?,content=?,row_version=row_version+1,updated_at=? "
                "WHERE id=? AND document_id=? AND row_version=? AND anchor_json=?",
                (destination_document, anchor, remapped, now, comment_id,
                 page_id, version, previous_anchor),
            )
            if changed.rowcount != 1:
                raise AuthorityUnavailable("page comment changed during return")
            for notification in con.execute(
                "SELECT id,payload_json FROM workspace_collaboration_notifications "
                "WHERE comment_id=?", (comment_id,),
            ).fetchall():
                payload = json.loads(str(notification["payload_json"]))
                payload["document_id"] = destination_document
                con.execute(
                    "UPDATE workspace_collaboration_notifications SET document_id=?,"
                    "document_epoch=?,payload_json=?,"
                    "notification_epoch=notification_epoch+1,updated_at=? WHERE id=?",
                    (destination_document, expected_destination_epoch,
                     _canonical_json(payload), now, str(notification["id"])),
                )
        destination_position = con.execute(
            "SELECT COALESCE(MAX(position),-1)+1 FROM "
            "workspace_collaboration_database_rows WHERE database_id=?",
            (destination_database,),
        ).fetchone()[0]
        changed = con.execute(
            "UPDATE workspace_collaboration_database_rows SET database_id=?,"
            "position=CASE WHEN database_id=? THEN position ELSE ? END,"
            "row_version=row_version+1,updated_at=? WHERE id=? AND "
            "database_id=? AND row_version=?",
            (destination_database, destination_database,
             destination_position, now, page_id, source_database,
             expected_row_version),
        )
        if changed.rowcount != 1:
            raise AuthorityUnavailable("item changed during page return")
        con.execute(
            "UPDATE workspace_collaboration_database_row_page_placements SET "
            "placement='database',parent_document_id=NULL,row_version=row_version+1,"
            "updated_at=? WHERE row_id=? AND placement='page'",
            (now, page_id),
        )
        con.execute(
            "UPDATE workspace_collaboration_documents SET archived=1,archived_at=?,"
            "parent_document_id=NULL,access_epoch=access_epoch+1,"
            "document_sequence=document_sequence+1,row_version=row_version+1,"
            "updated_at=? WHERE id=?", (now, now, page_id),
        )
        if not parent_id:
            native_team_location.replace_root(
                con, workspace_id, page_id, None, actor, now)
        authority._bump_workspace(con, workspace_id)
        workspace = authority._workspace(con, workspace_id)
        if delta is not None:
            canonical = {item["id"] for item in parent_source["snapshot"]["blocks"]}
            for block_id in sorted(set(delta["removed"]) & canonical):
                page_move._structural_link(
                    authority, con, actor, workspace, parent, "block.delete",
                    block_id, {}, operation, now,
                    operation_action=RETURN_ACTION,
                    receipt_action="collaboration.database.page_to_row_link",
                    identity_prefix="prlcmd")
            page_move._append_link_delta(
                authority, con, actor, workspace, parent, delta["payload"],
                generation, operation, now, operation_action=RETURN_ACTION,
                receipt_action="collaboration.database.page_to_row_delta",
                client_id="page-to-row-service", binding_contract=RETURN_CONTRACT,
                identity_prefix="prlupd")
        if source_database != destination_database and source_document != destination_document:
            source_now = authority._document(con, source_document)
            source_hash = _sha256(_canonical_json(authority._editing_snapshot(
                con, source_now)))
            if source_document == parent_id:
                source_sequence = int(source_now["document_sequence"])
                con.execute(
                    "UPDATE workspace_collaboration_documents SET content_sha256=?,"
                    "row_version=row_version+1,updated_at=? WHERE id=?",
                    (source_hash, now, source_document),
                )
            else:
                source_sequence = int(source_now["document_sequence"]) + 1
                con.execute(
                    "UPDATE workspace_collaboration_documents SET document_sequence=?,"
                    "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
                    (source_sequence, source_hash, now, source_document),
                )
        else:
            source_sequence = 0
        destination_sequence = int(authority._document(
            con, destination_document)["document_sequence"]) + 1
        destination_hash = _sha256(_canonical_json(authority._editing_snapshot(
            con, authority._document(con, destination_document))))
        con.execute(
            "UPDATE workspace_collaboration_documents SET document_sequence=?,"
            "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
            (destination_sequence, destination_hash, now, destination_document),
        )
        if source_database != destination_database:
            database_activity.touch(
                con, database_ids=(source_database,),
                document_id=source_document, actor_subject_id=actor,
                edited_at=now,
                document_sequence=(source_sequence if source_sequence else
                    destination_sequence))
        database_activity.touch(
            con, database_ids=(destination_database,),
            document_id=destination_document, actor_subject_id=actor,
            edited_at=now, document_sequence=destination_sequence)
        con.execute(
            "UPDATE workspaces SET row_version=row_version+1,updated_at=? "
            "WHERE id=?", (now, workspace_id),
        )
        result = {
            "contract": RETURN_CONTRACT, "workspace_id": workspace_id,
            "page_id": page_id, "row_id": page_id,
            "destination_document_id": destination_document,
            "destination_database_id": destination_database,
            "source_document_id": source_document,
            "source_database_id": source_database,
            "source_document_sequence": (source_sequence if source_sequence else
                destination_sequence),
            "parent_document_id": parent_id,
            "document_sequence": destination_sequence,
            "parent_document_sequence": int(authority._document(
                con, parent_id)["document_sequence"]) if parent_id else 0,
            "target_version": expected_row_version + 1,
            "copied_file_count": len(file_mapping) + len(cell_file_mapping),
            "created_properties": [
                {"id": item[0], "name": item[2], "type": item[3]}
                for item in new_properties],
        }
        if (source_database != destination_database
                and source_document not in (destination_document, parent_id)):
            source_event = {"contract": RETURN_CONTRACT,
                            "source_document_id": source_document,
                            "source_database_id": source_database,
                            "destination_database_id": destination_database,
                            "row_id": page_id, "document_sequence": source_sequence}
            source_receipt = authority._finish(
                con, actor=actor,
                action="collaboration.database.page_to_row_source",
                operation_id=_stable_id("page_to_row_source", actor, operation,
                                        source_document),
                request_sha256=_request_sha256(actor, RETURN_ACTION,
                                               source_event),
                workspace_id=workspace_id, document_id=source_document,
                workspace_epoch=int(workspace["security_epoch"]),
                document_epoch=int(authority._document(
                    con, source_document)["access_epoch"]),
                document_sequence=source_sequence,
                event_type="database.surface.committed",
                event_payload=source_event, result=source_event, fault=None)
            result["source_event_id"] = source_receipt["event_id"]
            result["source_outbox_id"] = source_receipt["outbox_id"]
        page_result = {"contract": RETURN_CONTRACT, "page_id": page_id,
                       "row_id": page_id, "placement": "database",
                       "destination_document_id": destination_document}
        authority._finish(
            con, actor=actor, action="collaboration.database.page_to_row_shell",
            operation_id=_stable_id("page_row_shell", actor, operation, page_id),
            request_sha256=_request_sha256(actor, RETURN_ACTION, page_result),
            workspace_id=workspace_id, document_id=page_id,
            workspace_epoch=int(workspace["security_epoch"]),
            document_epoch=int(authority._document(con, page_id)["access_epoch"]),
            document_sequence=int(authority._document(
                con, page_id)["document_sequence"]),
            event_type="page.placed_in_database", event_payload=page_result,
            result=page_result, fault=None)
        return authority._finish(
            con, actor=actor, action=RETURN_ACTION, operation_id=operation,
            request_sha256=digest, workspace_id=workspace_id,
            document_id=destination_document,
            workspace_epoch=int(workspace["security_epoch"]),
            document_epoch=expected_destination_epoch,
            document_sequence=destination_sequence,
            event_type="database.surface.committed",
            event_payload=result, result=result, fault=fault)
