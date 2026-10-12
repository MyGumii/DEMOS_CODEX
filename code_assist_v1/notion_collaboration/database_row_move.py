"""Replayable transfer of selected canonical items between owned databases."""

from __future__ import annotations

import json
import time
from typing import Any, Mapping

from . import database_activity, database_move, database_row_comments, database_row_trash, database_trash
from .canonical_authority import (
    AuthorityUnavailable, NotFoundOrDenied, _canonical_json, _identifier,
    _operation_id, _request_sha256, _require_unlocked_or_temporary, _sha256,
    _stable_id,
)
from .editing_authority import DocumentSequenceConflict


class DatabaseRowMoveShapeUnavailable(ValueError):
    """The requested transfer cannot preserve every selected item's data."""


def read_owned_row_location(authority, actor_subject_id: str,
                            workspace_id: str, row_id: str) -> dict[str, str]:
    """Resolve an active row's current owned database without disclosing denied pages."""
    actor = _identifier(actor_subject_id, field="actor_subject_id")
    workspace = _identifier(workspace_id, field="workspace_id")
    row = _identifier(row_id, field="row_id")
    authority._require_active(actor)
    with authority._reader() as con:
        if not database_row_trash.installed(con):
            raise AuthorityUnavailable("database-row location requires the 2423 authority")
        from . import database_row_page_placement
        if database_row_page_placement.installed(con):
            placed = con.execute(
                "SELECT page.workspace_id,page.archived,placement.page_document_id "
                "FROM workspace_collaboration_database_row_page_placements placement "
                "JOIN workspace_collaboration_documents page "
                "ON page.id=placement.page_document_id "
                "WHERE placement.row_id=? AND placement.placement='page'",
                (row,),
            ).fetchone()
            if placed is not None:
                if (str(placed["workspace_id"]) != workspace
                        or bool(placed["archived"])):
                    raise NotFoundOrDenied("not found or permission denied")
                page_id = str(placed["page_document_id"])
                role, _sources = authority._resolution_on_connection(
                    con, actor, workspace, page_id)
                if role is None:
                    raise NotFoundOrDenied("not found or permission denied")
                return {"contract": "code_assist.notion.database_row_location.v1",
                        "workspace_id": workspace, "document_id": page_id,
                        "row_id": row, "placement": "page"}
        current = con.execute(
            "SELECT item.database_id,database.document_id,document.workspace_id,"
            "document.archived FROM workspace_collaboration_database_rows item "
            "JOIN workspace_collaboration_databases database ON database.id=item.database_id "
            "JOIN workspace_collaboration_documents document ON document.id=database.document_id "
            "WHERE item.id=?", (row,),
        ).fetchone()
        if (current is None or str(current["workspace_id"]) != workspace
                or bool(current["archived"])):
            raise NotFoundOrDenied("not found or permission denied")
        document_id = str(current["document_id"])
        database_id = str(current["database_id"])
        role, _sources = authority._resolution_on_connection(
            con, actor, workspace, document_id)
        if role is None:
            raise NotFoundOrDenied("not found or permission denied")
        database_trash.require_active(con, database_id)
        database_row_trash.require_active(con, database_id, row)
        return {"contract": "code_assist.notion.database_row_location.v1",
                "workspace_id": workspace, "document_id": document_id,
                "database_id": database_id, "row_id": row}


def _positive(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"positive {name} required")
    return value


_CLONABLE_PROPERTY_TYPES = frozenset({
    "text", "number", "checkbox", "date", "select", "multi_select",
    "status", "person", "files", "url", "email", "phone",
})


def _require_retained_relations(con, row_id: str, current_database: str) -> None:
    """Only relation cells may be retained outside the row's current schema."""
    for cell in con.execute(
            "SELECT property.property_type FROM "
            "workspace_collaboration_database_cells cell "
            "JOIN workspace_collaboration_database_properties property "
            "ON property.id=cell.property_id "
            "WHERE cell.row_id=? AND property.database_id<>?",
            (row_id, current_database)).fetchall():
        if str(cell["property_type"]) != "relation":
            raise DatabaseRowMoveShapeUnavailable(
                "item has a non-relation cell outside its current schema")


def _property_plan(con, source_database: str, destination_database: str,
                   actor: str, workspace_id: str, operation: str,
                   selected_property_ids: set[str]
                   ) -> tuple[dict[str, str], list[tuple[str, str, str, str, int, str]]]:
    source = con.execute(
        "SELECT id,name,property_type,position,config_json FROM workspace_collaboration_database_properties "
        "WHERE database_id=? ORDER BY position,id", (source_database,)
    ).fetchall()
    destination = con.execute(
        "SELECT id,name,property_type,position,config_json FROM workspace_collaboration_database_properties "
        "WHERE database_id=? ORDER BY position,id", (destination_database,)
    ).fetchall()
    mapping: dict[str, str] = {}
    used: set[str] = set()
    names = {str(item["name"]) for item in destination}
    position = max((int(item["position"]) for item in destination), default=-1) + 1
    additions: list[tuple[str, str, str, str, int, str]] = []
    source_ids = {str(item["id"]) for item in source}
    if not selected_property_ids <= source_ids:
        raise DatabaseRowMoveShapeUnavailable(
            "item has a cell outside its source schema")
    for item in source:
        if str(item["id"]) not in selected_property_ids:
            continue
        kind = str(item["property_type"])
        if kind == "relation" and json.loads(str(item["config_json"])).get(
                "reciprocal_property_id"):
            # The inverse property names this exact source property. Moving a
            # row must retain the cell under that ID for a later return, even
            # if the destination happens to have a matching-looking column.
            continue
        candidates = [other for other in destination
            if str(other["property_type"]) == kind and (
                kind == "title" or (
                    str(other["name"]) == str(item["name"])
                    and str(other["config_json"]) == str(item["config_json"])
            ))]
        if not candidates and kind != "title":
            source_name = str(item["name"])
            source_config = str(item["config_json"])
            candidates = [other for other in destination
                if str(other["property_type"]) == kind
                and str(other["config_json"]) == source_config
                and (str(other["name"]).startswith(source_name + " ")
                     and str(other["name"])[len(source_name) + 1:].isdigit()
                     or source_name.startswith(str(other["name"]) + " ")
                     and source_name[len(str(other["name"])) + 1:].isdigit())]
        if len(candidates) > 1 or (candidates and str(candidates[0]["id"]) in used):
            raise DatabaseRowMoveShapeUnavailable(
                "destination property mapping is ambiguous")
        if candidates:
            target = str(candidates[0]["id"])
        else:
            if kind == "relation":
                # Notion keeps an unmatched source relation hidden while a row
                # belongs to another database, then exposes it on return.
                # Its original property ID remains canonical on the row.
                continue
            if kind not in _CLONABLE_PROPERTY_TYPES:
                raise DatabaseRowMoveShapeUnavailable(
                    "destination property schema cannot preserve source values")
            source_name = str(item["name"])
            name = source_name
            suffix = 1
            while name in names:
                name = f"{source_name} {suffix}"
                suffix += 1
            names.add(name)
            target = _stable_id("dbprop", actor, workspace_id, operation,
                                destination_database, str(item["id"]))
            if con.execute(
                    "SELECT 1 FROM workspace_collaboration_database_properties WHERE id=?",
                    (target,)).fetchone():
                raise AuthorityUnavailable("destination property identity is occupied")
            additions.append((target, destination_database, name, kind,
                              position, str(item["config_json"])))
            position += 1
        mapping[str(item["id"])] = target
        used.add(target)
    if len(destination) + len(additions) > 500:
        raise DatabaseRowMoveShapeUnavailable(
            "destination database property limit would be exceeded")
    return mapping, additions


def _comment_plan(con, document: str, source_database: str,
                  selected: set[str]) -> tuple[tuple[str, int, str], ...]:
    comments = con.execute(
        "SELECT id,row_version,parent_comment_id,anchor_json FROM "
        "workspace_collaboration_comments WHERE document_id=? ORDER BY id",
        (document,),
    ).fetchall()
    picked: set[str] = set()
    for comment in comments:
        try:
            anchor = json.loads(str(comment["anchor_json"]))
            target = database_row_comments.target(anchor)
        except (TypeError, ValueError) as exc:
            raise DatabaseRowMoveShapeUnavailable("item comment anchor is malformed") from exc
        if (target is not None and target["database_id"] == source_database
                and target["row_id"] in selected):
            picked.add(str(comment["id"]))
    for comment in comments:
        parent = comment["parent_comment_id"]
        if parent is not None and (
                (str(comment["id"]) in picked) != (str(parent) in picked)):
            raise DatabaseRowMoveShapeUnavailable(
                "item comment thread crosses the move boundary")
    return tuple((str(item["id"]), int(item["row_version"]),
                  str(item["anchor_json"])) for item in comments
                 if str(item["id"]) in picked)


def _validate_inbound_relations(con, source_database: str,
                                selected: set[str]) -> None:
    """Retain inbound cells while validating their canonical value shape."""
    if not selected:
        return
    for item in con.execute(
        "SELECT cell.value_json FROM "
        "workspace_collaboration_database_properties property "
        "JOIN workspace_collaboration_database_cells cell ON cell.property_id=property.id "
        "WHERE property.property_type='relation' "
        "AND json_extract(property.config_json,'$.target_database_id')=?",
        (source_database,),
    ).fetchall():
        try:
            values = json.loads(str(item["value_json"]))
        except (TypeError, ValueError) as exc:
            raise DatabaseRowMoveShapeUnavailable("relation value is malformed") from exc
        if not isinstance(values, list):
            raise DatabaseRowMoveShapeUnavailable("relation value is malformed")


def _cross_document_files(con, actor: str, workspace_id: str,
                          source_document: str, destination_document: str,
                          moving: list[str], comments: tuple[tuple[str, int, str], ...],
                          operation: str, *, include_cells: bool = True,
                          include_row_content: bool = True) -> dict[str, str]:
    """Clone document-scoped file identities while retaining shared source blobs."""
    references: set[str] = set()
    for row_id in moving:
        if include_cells:
            for cell in con.execute(
                    "SELECT value_json FROM workspace_collaboration_database_cells "
                    "WHERE row_id=?", (row_id,)):
                references.update(database_move._file_ids(json.loads(str(cell["value_json"]))))
        if include_row_content:
            body = con.execute(
                "SELECT blocks_json FROM workspace_collaboration_database_row_bodies "
                "WHERE row_id=?", (row_id,),
            ).fetchone()
            if body is not None:
                blocks = json.loads(str(body["blocks_json"]))
                if database_move._has_child_page(blocks):
                    raise DatabaseRowMoveShapeUnavailable(
                        "item child pages require a subtree transfer")
                references.update(database_move._file_ids(blocks))
            appearance = con.execute(
                "SELECT appearance_json FROM workspace_collaboration_database_row_appearance "
                "WHERE row_id=?", (row_id,),
            ).fetchone()
            if appearance is not None:
                references.update(database_move._file_ids(
                    json.loads(str(appearance["appearance_json"]))))
    for comment_id, _, _ in comments:
        comment = con.execute(
            "SELECT content FROM workspace_collaboration_comments WHERE id=?",
            (comment_id,),
        ).fetchone()
        if comment is None:
            raise AuthorityUnavailable("item comment changed during move")
        references.update(database_move._file_ids(str(comment["content"])))
    if len(references) > 64:
        raise DatabaseRowMoveShapeUnavailable("item attachments require a scoped transfer")
    mapping = {file_id: _stable_id("file", actor, workspace_id, operation,
        source_document, destination_document, "row-move", file_id)
        for file_id in sorted(references)}
    for file_id in sorted(references):
        file = con.execute(
            "SELECT id FROM workspace_collaboration_files WHERE id=? AND "
            "workspace_id=? AND document_id=? AND state='active'",
            (file_id, workspace_id, source_document),
        ).fetchone()
        if file is None:
            raise DatabaseRowMoveShapeUnavailable(
                "item attachment is unavailable at its source")
        if con.execute("SELECT 1 FROM workspace_collaboration_files WHERE id=?",
                       (mapping[file_id],)).fetchone():
            raise AuthorityUnavailable("item attachment copy identity is occupied")
    return mapping


def _copy_cross_document_files(con, mapping: Mapping[str, str], actor: str,
                               workspace_id: str, source_document: str,
                               destination_document: str, operation: str,
                               now: int) -> None:
    for source_id, target_id in mapping.items():
        file = con.execute(
            "SELECT * FROM workspace_collaboration_files WHERE id=? AND "
            "workspace_id=? AND document_id=? AND state='active'",
            (source_id, workspace_id, source_document),
        ).fetchone()
        if file is None:
            raise AuthorityUnavailable("item attachment changed during move")
        con.execute(
            "INSERT INTO workspace_collaboration_files(id,workspace_id,document_id,"
            "upload_session_id,uploader_subject_id,original_name,mime_type,size_bytes,"
            "content_sha256,blob_relative_path,state,row_version,created_at,updated_at,"
            "origin_kind,source_file_id,source_document_id,copied_by_subject_id,copy_operation_id) "
            "VALUES(?,?,?,NULL,?,?,?,?,?,?,'active',1,?,?,'copy',?,?,?,?)",
            (target_id, workspace_id, destination_document,
             str(file["uploader_subject_id"]), str(file["original_name"]),
             str(file["mime_type"]), int(file["size_bytes"]),
             str(file["content_sha256"]), str(file["blob_relative_path"]),
             now, now, source_id, source_document, actor, operation),
        )


def _emit_destination_event(con, *, actor: str, action: str, operation: str,
                            workspace_id: str, document_id: str, sequence: int,
                            workspace_epoch: int, document_epoch: int,
                            result: dict[str, Any], now: int) -> None:
    event_operation = _stable_id("rmop", actor, action, operation, document_id)
    event_id = _stable_id("cevt", actor, action, operation, document_id)
    outbox_id = _stable_id("cout", actor, action, operation, document_id)
    result["destination_event_id"] = event_id
    result["destination_outbox_id"] = outbox_id
    payload = _canonical_json(dict(result))
    con.execute(
        "INSERT INTO workspace_collaboration_events(id,workspace_id,document_id,"
        "document_sequence,event_type,actor_subject_id,operation_id,workspace_epoch,"
        "document_epoch,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (event_id, workspace_id, document_id, sequence,
         "database.surface.committed", actor, event_operation,
         workspace_epoch, document_epoch, payload, now),
    )
    con.execute(
        "INSERT INTO workspace_collaboration_outbox(id,workspace_id,document_id,"
        "event_type,actor_subject_id,operation_id,workspace_epoch,document_epoch,"
        "payload_json,created_at,delivered_at) VALUES(?,?,?,?,?,?,?,?,?,?,NULL)",
        (outbox_id, workspace_id, document_id,
         "database.surface.committed", actor, event_operation,
         workspace_epoch, document_epoch, payload, now),
    )


def move_owned_rows(authority, actor_subject_id: str, workspace_id: str,
                    source_document_id: str, source_database_id: str,
                    destination_document_id: str, destination_database_id: str,
                    *, rows: list[Mapping[str, Any]],
                    expected_source_sequence: int,
                    expected_destination_sequence: int,
                    expected_workspace_epoch: int,
                    expected_source_epoch: int,
                    expected_destination_epoch: int,
                    operation_id: str, fault: str | None = None) -> dict[str, Any]:
    """Move 1–100 active rows, adding lossless destination properties."""
    actor = _identifier(actor_subject_id, field="actor_subject_id")
    workspace_id = _identifier(workspace_id, field="workspace_id")
    source_document = _identifier(source_document_id, field="source_document_id")
    destination_document = _identifier(destination_document_id, field="destination_document_id")
    source_database = _identifier(source_database_id, field="source_database_id")
    destination_database = _identifier(destination_database_id, field="destination_database_id")
    operation = _operation_id(operation_id)
    for name, value in (
        ("source sequence", expected_source_sequence),
        ("destination sequence", expected_destination_sequence),
        ("workspace epoch", expected_workspace_epoch),
        ("source epoch", expected_source_epoch),
        ("destination epoch", expected_destination_epoch),
    ):
        _positive(value, name)
    if source_database == destination_database:
        raise ValueError("source and destination databases must differ")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError("row move requires 1 to 100 rows")
    clean_rows: list[dict[str, Any]] = []
    selected: set[str] = set()
    for item in rows:
        if not isinstance(item, Mapping) or set(item) != {
                "row_id", "expected_target_version"}:
            raise ValueError("row move entry fields do not match the contract")
        row_id = _identifier(item["row_id"], field="row_id")
        version = _positive(item["expected_target_version"], "row version")
        if row_id in selected:
            raise ValueError("row move targets must be unique")
        selected.add(row_id)
        clean_rows.append({"row_id": row_id, "expected_target_version": version})
    request = {
        "workspace_id": workspace_id,
        "source_document_id": source_document,
        "source_database_id": source_database,
        "destination_document_id": destination_document,
        "destination_database_id": destination_database,
        "rows": clean_rows,
        "expected_source_sequence": expected_source_sequence,
        "expected_destination_sequence": expected_destination_sequence,
        "expected_workspace_epoch": expected_workspace_epoch,
        "expected_source_epoch": expected_source_epoch,
        "expected_destination_epoch": expected_destination_epoch,
    }
    action = "collaboration.database.row_move_batch"
    digest = _request_sha256(actor, action, request)
    with authority._transaction() as con:
        if not database_row_trash.installed(con):
            raise AuthorityUnavailable("database-row move requires the 2423 authority")

        def authorize() -> None:
            workspace, _page, _role = authority._exact_access(
                con, actor, source_document, minimum_role="edit_content",
                expected_workspace_epoch=expected_workspace_epoch,
                expected_document_epoch=expected_source_epoch)
            if str(workspace["id"]) != workspace_id:
                raise NotFoundOrDenied("not found or permission denied")
            authority._database_for_document(con, source_document, source_database)
            destination_workspace, _destination_page, _destination_role = authority._exact_access(
                con, actor, destination_document, minimum_role="edit_content",
                expected_workspace_epoch=expected_workspace_epoch,
                expected_document_epoch=expected_destination_epoch)
            if str(destination_workspace["id"]) != workspace_id:
                raise NotFoundOrDenied("not found or permission denied")
            authority._database_for_document(con, destination_document,
                                             destination_database)

        replay = authority._replay(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=digest, authorize=authorize)
        if replay is not None:
            return replay
        authorize()
        cross_document = source_document != destination_document
        if (not cross_document and (expected_source_sequence != expected_destination_sequence
                or expected_source_epoch != expected_destination_epoch)):
            raise ValueError("same-document move requires identical document fences")
        source_page = authority._document(con, source_document)
        destination_page = (authority._document(con, destination_document)
                            if cross_document else source_page)
        _require_unlocked_or_temporary(con, source_page, actor)
        if cross_document:
            _require_unlocked_or_temporary(con, destination_page, actor)
        if int(source_page["document_sequence"]) != expected_source_sequence:
            raise DocumentSequenceConflict(int(source_page["document_sequence"]))
        if int(destination_page["document_sequence"]) != expected_destination_sequence:
            raise DocumentSequenceConflict(int(destination_page["document_sequence"]))
        selected_property_ids: set[str] = set()
        for item in clean_rows:
            row = con.execute(
                "SELECT * FROM workspace_collaboration_database_rows "
                "WHERE database_id=? AND id=?",
                (source_database, item["row_id"]),
            ).fetchone()
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")
            database_row_trash.require_active(con, source_database, item["row_id"])
            authority._check_target_version(row, item["expected_target_version"])
            _require_retained_relations(con, item["row_id"], source_database)
            for cell in con.execute(
                "SELECT cell.property_id,cell.value_json FROM "
                "workspace_collaboration_database_cells cell "
                "JOIN workspace_collaboration_database_properties property "
                "ON property.id=cell.property_id "
                "WHERE cell.row_id=? AND property.database_id=?",
                (item["row_id"], source_database)
            ).fetchall():
                selected_property_ids.add(str(cell["property_id"]))
                try:
                    json.loads(str(cell["value_json"]))
                except (TypeError, ValueError) as exc:
                    raise DatabaseRowMoveShapeUnavailable(
                        "item cell value is malformed") from exc
        property_ids, new_properties = _property_plan(
            con, source_database, destination_database, actor, workspace_id,
            operation, selected_property_ids)
        if new_properties:
            authority._exact_access(
                con, actor, destination_document, minimum_role="edit",
                expected_workspace_epoch=expected_workspace_epoch,
                expected_document_epoch=expected_destination_epoch)
        _validate_inbound_relations(con, source_database, selected)
        comments = _comment_plan(con, source_document, source_database, selected)
        source_order = [str(item["id"]) for item in con.execute(
            "SELECT id FROM workspace_collaboration_database_rows WHERE database_id=? "
            "ORDER BY position,id", (source_database,))]
        destination_order = [str(item["id"]) for item in con.execute(
            "SELECT id FROM workspace_collaboration_database_rows WHERE database_id=? "
            "ORDER BY position,id", (destination_database,))]
        moving = [row_id for row_id in source_order if row_id in selected]
        if len(moving) != len(selected):
            raise NotFoundOrDenied("not found or permission denied")
        file_mapping = (_cross_document_files(con, actor, workspace_id,
            source_document, destination_document, moving, comments, operation)
            if cross_document else {})
        now = int(time.time())
        for property_id, database_id, name, kind, position, config in new_properties:
            con.execute(
                "INSERT INTO workspace_collaboration_database_properties("
                "id,database_id,name,property_type,position,config_json,"
                "row_version,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?)",
                (property_id, database_id, name, kind, position, config, now, now),
            )
        if cross_document:
            _copy_cross_document_files(con, file_mapping, actor, workspace_id,
                source_document, destination_document, operation, now)
        for offset, row_id in enumerate(moving):
            version = next(item["expected_target_version"] for item in clean_rows
                           if item["row_id"] == row_id)
            changed = con.execute(
                "UPDATE workspace_collaboration_database_rows SET database_id=?,"
                "position=?,row_version=row_version+1,updated_at=? "
                "WHERE id=? AND database_id=? AND row_version=?",
                (destination_database, len(destination_order) + offset, now,
                 row_id, source_database, version),
            )
            if changed.rowcount != 1:
                raise AuthorityUnavailable("item changed during move")
            for cell in con.execute(
                "SELECT cell.property_id,cell.value_json,cell.row_version FROM "
                "workspace_collaboration_database_cells cell "
                "JOIN workspace_collaboration_database_properties property "
                "ON property.id=cell.property_id "
                "WHERE cell.row_id=? AND property.database_id=?",
                (row_id, source_database)
            ).fetchall():
                source_property = str(cell["property_id"])
                target_property = property_ids.get(source_property)
                if target_property is None:
                    continue  # Unmatched source relation remains hidden on this row.
                if con.execute(
                        "SELECT 1 FROM workspace_collaboration_database_cells "
                        "WHERE row_id=? AND property_id=?",
                        (row_id, target_property)).fetchone():
                    raise DatabaseRowMoveShapeUnavailable(
                        "destination has a retained value for the same property")
                previous = str(cell["value_json"])
                value = json.loads(previous)
                converted = (database_move._remap_database_files(value, file_mapping)
                             if cross_document else value)
                if cross_document and database_move._file_ids(converted) & set(file_mapping):
                    raise DatabaseRowMoveShapeUnavailable(
                        "item cell has an unconverted attachment")
                changed = con.execute(
                    "UPDATE workspace_collaboration_database_cells SET property_id=?,"
                    "value_json=?,row_version=row_version+1,updated_at=? "
                    "WHERE row_id=? AND property_id=? AND row_version=? AND value_json=?",
                    (target_property, _canonical_json(converted),
                     now, row_id, str(cell["property_id"]),
                     int(cell["row_version"]), previous),
                )
                if changed.rowcount != 1:
                    raise AuthorityUnavailable("item cell changed during move")
            if cross_document:
                for table, column in (
                    ("workspace_collaboration_database_row_bodies", "blocks_json"),
                    ("workspace_collaboration_database_row_appearance", "appearance_json"),
                ):
                    resource = con.execute(
                        f"SELECT {column} FROM {table} WHERE row_id=?", (row_id,),
                    ).fetchone()
                    if resource is None:
                        continue
                    previous = str(resource[column])
                    converted = database_move._remap_database_files(
                        json.loads(previous), file_mapping)
                    if database_move._file_ids(converted) & set(file_mapping):
                        raise DatabaseRowMoveShapeUnavailable(
                            "item body or appearance has an unconverted attachment")
                    if _canonical_json(converted) != previous:
                        changed = con.execute(
                            f"UPDATE {table} SET {column}=? WHERE row_id=? AND {column}=?",
                            (_canonical_json(converted), row_id, previous),
                        )
                        if changed.rowcount != 1:
                            raise AuthorityUnavailable("item resource changed during move")
        remaining = [row_id for row_id in source_order if row_id not in selected]
        for position, row_id in enumerate(remaining):
            con.execute(
                "UPDATE workspace_collaboration_database_rows SET position=?,updated_at=? "
                "WHERE database_id=? AND id=?",
                (position, now, source_database, row_id),
            )
        for comment_id, version, previous in comments:
            anchor = json.loads(previous)
            anchor["database_id"] = destination_database
            original = con.execute(
                "SELECT content FROM workspace_collaboration_comments "
                "WHERE id=? AND document_id=? AND row_version=?",
                (comment_id, source_document, version),
            ).fetchone()
            if original is None:
                raise AuthorityUnavailable("item comment changed during move")
            content = (database_move._remap_database_files(str(original["content"]),
                        file_mapping) if cross_document else str(original["content"]))
            if cross_document and database_move._file_ids(content) & set(file_mapping):
                raise DatabaseRowMoveShapeUnavailable(
                    "item comment has an unconverted attachment")
            changed = con.execute(
                "UPDATE workspace_collaboration_comments SET anchor_json=?,"
                "document_id=?,content=?,row_version=row_version+1,updated_at=? "
                "WHERE id=? AND document_id=? AND row_version=? AND anchor_json=? "
                "AND content=?",
                (_canonical_json(anchor), destination_document, content, now,
                 comment_id, source_document, version, previous,
                 str(original["content"])),
            )
            if changed.rowcount != 1:
                raise AuthorityUnavailable("item comment changed during move")
        source_sequence = expected_source_sequence + 1
        destination_sequence = (expected_destination_sequence + 1
                                if cross_document else source_sequence)
        source_hash = _sha256(_canonical_json(authority._editing_snapshot(
            con, authority._document(con, source_document))))
        destination_hash = (_sha256(_canonical_json(authority._editing_snapshot(
            con, authority._document(con, destination_document))))
            if cross_document else source_hash)
        for document_id, sequence, checksum in (
                ((source_document, source_sequence, source_hash),
                 (destination_document, destination_sequence, destination_hash))
                if cross_document else ((source_document, source_sequence, source_hash),)):
            con.execute(
                "UPDATE workspace_collaboration_documents SET document_sequence=?,"
                "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
                (sequence, checksum, now, document_id),
            )
        if cross_document:
            database_activity.touch(
                con, database_ids=(source_database,), document_id=source_document,
                actor_subject_id=actor, edited_at=now,
                document_sequence=source_sequence)
            database_activity.touch(
                con, database_ids=(destination_database,),
                document_id=destination_document, actor_subject_id=actor,
                edited_at=now, document_sequence=destination_sequence)
        else:
            database_activity.touch(
                con, database_ids=(source_database, destination_database),
                document_id=source_document, actor_subject_id=actor,
                edited_at=now, document_sequence=source_sequence)
        result = {
            "contract": "code_assist.notion.collaborative_database_row_move_batch.v1",
            "workspace_id": workspace_id, "source_document_id": source_document,
            "destination_document_id": destination_document,
            "source_database_id": source_database,
            "destination_database_id": destination_database,
            "command_kind": "database.rows.move",
            "document_sequence": source_sequence,
            "destination_document_sequence": destination_sequence,
            "content_sha256": source_hash,
            "destination_content_sha256": destination_hash,
            "copied_file_count": len(file_mapping),
            "created_properties": [
                {"id": item[0], "name": item[2], "type": item[3]}
                for item in new_properties],
            "rows": [{"row_id": row_id, "target_version":
                     next(item["expected_target_version"] for item in clean_rows
                          if item["row_id"] == row_id) + 1,
                     "position": len(destination_order) + offset}
                    for offset, row_id in enumerate(moving)],
        }
        if cross_document:
            _emit_destination_event(
                con, actor=actor, action=action, operation=operation,
                workspace_id=workspace_id, document_id=destination_document,
                sequence=destination_sequence,
                workspace_epoch=expected_workspace_epoch,
                document_epoch=expected_destination_epoch, result=result,
                now=now)
        return authority._finish(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=digest, workspace_id=workspace_id,
            document_id=source_document, workspace_epoch=expected_workspace_epoch,
            document_epoch=expected_source_epoch,
            document_sequence=source_sequence,
            event_type="database.surface.committed", event_payload=result,
            result=result, fault=fault)
