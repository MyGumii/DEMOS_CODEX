"""Atomic owned-database transfer between two canonical documents.

This capability-gated service preserves navigation favorites and linked views
while transferring an owned database. Owned-block content/properties, typed Files cells,
and item body/appearance resources copy file provenance into the destination;
database comment threads retarget with their notification read projection.
Unsupported attachment shapes and item child pages fail before commit.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import quote, unquote

from code_assist_v1 import notion_workspace_store as nws

from .canonical_authority import (
    AuthorityUnavailable, EpochConflict, NotFoundOrDenied, _canonical_json,
    _identifier, _operation_id, _positive, _request_sha256, _sha256, _stable_id,
)
from .editing_authority import DocumentSequenceConflict
from .file_authority import CapacityUnavailable
from . import database_activity, database_copy, database_row_comments, database_row_trash, database_trash, linked_databases, page_copy_remap, page_move, search_projection
from .title_projection import _frames, _generation


CONTRACT = "code_assist.notion.owned_database_move.v1"
ACTION = "collaboration.database_move"


class DatabaseMoveShapeUnavailable(ValueError):
    """A reference requiring document-scope migration blocks this packet."""


def _owner(authority, con, actor, workspace_id, document_id):
    """Accept exact 2423 only for this move service's owner-scoped path."""
    if not database_row_trash.installed(con):
        return database_copy._owner(authority, con, actor, workspace_id, document_id)
    authority._require_active(actor)
    workspace = authority._workspace(con, workspace_id)
    if authority._workspace_role(con, workspace_id, actor) != "owner":
        raise NotFoundOrDenied("not found or permission denied")
    page = authority._document(con, document_id)
    if page["workspace_id"] != workspace_id or page["legacy_owner_subject_id"] != actor:
        raise NotFoundOrDenied("not found or permission denied")
    return workspace, page


def _file_ids(value, field=""):
    if isinstance(value, dict):
        result = set()
        for key, nested in value.items():
            result.update(_file_ids(nested, key))
        return result
    if isinstance(value, list):
        result = set()
        for nested in value:
            result.update(_file_ids(nested, field))
        return result
    if not isinstance(value, str):
        return set()
    result = {value} if field == "file_id" and value else set()
    if field in {"icon", "cover"} and value.startswith("nf:"):
        result.add(value[3:])
    result.update(unquote(match.group(1)) for match in page_copy_remap._FILE_URL.finditer(value))
    return result


def _has_child_page(value):
    if isinstance(value, dict):
        return value.get("type") == "child_page" or any(_has_child_page(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_child_page(item) for item in value)
    return False


def _request(actor_subject_id, workspace_id, source_document_id, destination_document_id,
             database_id, *, operation_id, expected_source_sequence,
             expected_destination_sequence, expected_workspace_epoch,
             expected_source_epoch, expected_destination_epoch):
    actor = _identifier(actor_subject_id, field="actor_subject_id")
    workspace = _identifier(workspace_id, field="workspace_id")
    source = _identifier(source_document_id, field="source_document_id")
    destination = _identifier(destination_document_id, field="destination_document_id")
    database = _identifier(database_id, field="database_id")
    if source == destination:
        raise ValueError("a cross-document database destination is required")
    operation = _operation_id(operation_id)
    source_sequence = _positive(expected_source_sequence, field="expected_source_sequence")
    destination_sequence = _positive(expected_destination_sequence, field="expected_destination_sequence")
    workspace_epoch = _positive(expected_workspace_epoch, field="expected_workspace_epoch")
    source_epoch = _positive(expected_source_epoch, field="expected_source_epoch")
    destination_epoch = _positive(expected_destination_epoch, field="expected_destination_epoch")
    digest = _request_sha256(actor, ACTION, {"workspace_id": workspace,
        "source_document_id": source, "destination_document_id": destination,
        "database_id": database, "expected_source_sequence": source_sequence,
        "expected_destination_sequence": destination_sequence,
        "expected_workspace_epoch": workspace_epoch, "expected_source_epoch": source_epoch,
        "expected_destination_epoch": destination_epoch})
    return (actor, workspace, source, destination, database, operation, digest,
            source_sequence, destination_sequence, workspace_epoch, source_epoch, destination_epoch)


def _replay(authority, con, actor, workspace, source, destination, database, operation, digest):
    previous = authority._replay(con, actor=actor, action=ACTION, operation_id=operation,
        request_sha256=digest, authorize=lambda: (
            _owner(authority, con, actor, workspace, source),
            _owner(authority, con, actor, workspace, destination)))
    if previous is None:
        return None
    if (previous.get("contract") != CONTRACT or previous.get("operation_id") != operation
            or previous.get("workspace_id") != workspace
            or previous.get("source_document_id") != source
            or previous.get("destination_document_id") != destination
            or previous.get("database_id") != database):
        raise AuthorityUnavailable("stored database move result scope is invalid")
    return {**previous, "replayed": True}


def _scope(authority, con, actor, workspace_id, source_id, destination_id, database_id,
           source_sequence, destination_sequence, workspace_epoch,
           source_epoch, destination_epoch):
    if not database_activity.installed(con):
        raise AuthorityUnavailable("database move requires the activity schema")
    workspace, source = _owner(authority, con, actor, workspace_id, source_id)
    _, destination = _owner(authority, con, actor, workspace_id, destination_id)
    if source["archived"] or destination["archived"]:
        raise NotFoundOrDenied("not found or permission denied")
    page_move._unlocked(con, source, actor)
    page_move._unlocked(con, destination, actor)
    if (workspace["security_epoch"] != workspace_epoch
            or source["access_epoch"] != source_epoch
            or destination["access_epoch"] != destination_epoch):
        raise EpochConflict(workspace["security_epoch"], source["access_epoch"])
    if source["document_sequence"] != source_sequence:
        raise DocumentSequenceConflict(source["document_sequence"])
    if destination["document_sequence"] != destination_sequence:
        raise DocumentSequenceConflict(destination["document_sequence"])
    database = con.execute("SELECT * FROM workspace_collaboration_databases "
        "WHERE id=? AND document_id=?", (database_id, source_id)).fetchone()
    if database is None:
        raise NotFoundOrDenied("not found or permission denied")
    source_body = authority._editing_snapshot(con, source)
    destination_body = authority._editing_snapshot(con, destination)
    blocks = [item for item in source_body["blocks"]
        if item["type"] in page_copy_remap.DATABASE_BLOCKS
        and not item.get("props", {}).get("linked_database")
        and item.get("props", {}).get("database_id", item["id"]) == database_id]
    if len(blocks) != 1:
        raise DatabaseMoveShapeUnavailable("one owned source database block required")
    block = blocks[0]
    if any(item["id"] == block["id"] for item in destination_body["blocks"]):
        raise DatabaseMoveShapeUnavailable("database block already exists at destination")
    data = [item for item in source_body.get("databases", []) if item["id"] == database_id]
    if len(data) != 1:
        raise DatabaseMoveShapeUnavailable("one canonical source database required")
    if _has_child_page(data[0]):
        raise DatabaseMoveShapeUnavailable("database child pages require a subtree transfer")
    return workspace, source, destination, database, block, source_body, destination_body


def _comment_plan(con, source_id, database_id, block_id):
    """Keep each owned-database comment thread intact while changing its locator."""
    rows = con.execute("SELECT id,row_version,block_id,anchor_json,parent_comment_id "
        "FROM workspace_collaboration_comments WHERE document_id=? ORDER BY id",
        (source_id,)).fetchall()
    selected = set()
    for row in rows:
        try:
            anchor = json.loads(str(row["anchor_json"]))
        except (TypeError, ValueError) as exc:
            raise DatabaseMoveShapeUnavailable("comment anchor is malformed") from exc
        if not isinstance(anchor, dict):
            raise DatabaseMoveShapeUnavailable("comment anchor is malformed")
        try:
            item_target = database_row_comments.target(anchor)
        except ValueError as exc:
            raise DatabaseMoveShapeUnavailable("comment item anchor is malformed") from exc
        if anchor.get("database_id") == database_id and item_target is None:
            raise DatabaseMoveShapeUnavailable("database comment anchor is ambiguous")
        if row["block_id"] == block_id or (
                item_target is not None and item_target["database_id"] == database_id):
            selected.add(str(row["id"]))
    for row in rows:
        parent = row["parent_comment_id"]
        if parent is not None and ((str(row["id"]) in selected) != (str(parent) in selected)):
            raise DatabaseMoveShapeUnavailable("database comment thread crosses the move boundary")
    return tuple((str(row["id"]), int(row["row_version"]))
        for row in rows if str(row["id"]) in selected)


def _remap_database_files(value, mapping, field=""):
    if isinstance(value, dict):
        return {key: _remap_database_files(nested, mapping, key)
            for key, nested in value.items()}
    if isinstance(value, list):
        return [_remap_database_files(nested, mapping, field) for nested in value]
    if not isinstance(value, str):
        return value
    if field == "file_id" and value in mapping:
        return mapping[value]
    if field in {"icon", "cover"} and value.startswith("nf:") and value[3:] in mapping:
        return "nf:" + mapping[value[3:]]
    return page_copy_remap._FILE_URL.sub(lambda match:
        match.group(0)[:-len(match.group(1))]
        + quote(mapping.get(unquote(match.group(1)), unquote(match.group(1))), safe=""), value)


def _archived_rows_for_file_plan(con, database_id):
    """Include retained row resources without adding them to the visible snapshot."""
    from . import database_row_trash

    archived = database_row_trash.archived_row_ids(con, database_id)
    if not archived:
        return ()
    rows = []
    for row in con.execute("SELECT id,position,row_version FROM "
            "workspace_collaboration_database_rows WHERE database_id=? "
            "ORDER BY position,id", (database_id,)).fetchall():
        row_id = str(row["id"])
        if row_id not in archived:
            continue
        cell_rows = con.execute("SELECT property_id,value_json,row_version FROM "
            "workspace_collaboration_database_cells WHERE row_id=? "
            "ORDER BY property_id", (row_id,)).fetchall()
        body = con.execute("SELECT blocks_json FROM "
            "workspace_collaboration_database_row_bodies WHERE row_id=?",
            (row_id,)).fetchone()
        appearance = con.execute("SELECT appearance_json FROM "
            "workspace_collaboration_database_row_appearance WHERE row_id=?",
            (row_id,)).fetchone()
        rows.append({"id": row_id, "position": int(row["position"]),
            "version": int(row["row_version"]),
            "cells": {str(cell["property_id"]): json.loads(str(cell["value_json"]))
                for cell in cell_rows},
            "cell_versions": {str(cell["property_id"]): int(cell["row_version"])
                for cell in cell_rows},
            **({"body": {"blocks": json.loads(str(body[0]))}}
                if body is not None else {}),
            **({"appearance": json.loads(str(appearance[0]))}
                if appearance is not None else {})})
    if {row["id"] for row in rows} != archived:
        raise AuthorityUnavailable("archived database row changed during move")
    return tuple(rows)


def _database_file_plan(con, actor, workspace_id, source_id, destination_id,
                        database, block, operation):
    """Copy owned block, typed Files-cell, and item resources into destination authority."""
    from . import database_access

    scoped_uploads = database_access.upload_scope_installed(con)
    if scoped_uploads and con.execute(
            "SELECT 1 FROM workspace_collaboration_database_upload_scopes scope "
            "JOIN workspace_collaboration_upload_sessions session "
            "ON session.id=scope.session_id "
            "WHERE scope.database_id=? AND session.state IN "
            "('prepared','ready','committing') LIMIT 1",
            (database["id"],)).fetchone() is not None:
        raise DatabaseMoveShapeUnavailable(
            "finish or cancel pending database uploads before moving")
    block_references = _file_ids(block)
    file_properties = {item["id"] for item in database["properties"] if item["type"] == "files"}
    archived_rows = _archived_rows_for_file_plan(con, database["id"])
    if {row["id"] for row in archived_rows} & {row["id"] for row in database["rows"]}:
        raise AuthorityUnavailable("archived database row appeared in active snapshot")
    all_rows = (*database["rows"], *archived_rows)
    archived_ids = {row["id"] for row in archived_rows}
    references, cells, rows = set(block_references), [], []
    for row in all_rows:
        for property_id, value in row["cells"].items():
            if property_id not in file_properties or not value:
                continue
            if not isinstance(value, list) or any(not isinstance(item, dict)
                    or not isinstance(item.get("file_id"), str) or not item["file_id"]
                    for item in value):
                raise DatabaseMoveShapeUnavailable("database file cell shape is unsupported")
            references.update(_file_ids(value))
        for key in ("body", "appearance"):
            if key in row:
                references.update(_file_ids(row[key]))
    if (_file_ids(database) | _file_ids(list(archived_rows)) | block_references) != references or len(references) > 64:
        raise DatabaseMoveShapeUnavailable("database attachments require a scoped transfer")
    if scoped_uploads:
        for file in con.execute(
                "SELECT file.id,file.document_id FROM workspace_collaboration_files file "
                "JOIN workspace_collaboration_database_upload_scopes scope "
                "ON scope.session_id=file.upload_session_id "
                "WHERE scope.database_id=? AND file.state='active'",
                (database["id"],)).fetchall():
            if (str(file["document_id"]) != source_id
                    or str(file["id"]) not in references):
                raise DatabaseMoveShapeUnavailable(
                    "attach or delete unreferenced database uploads before moving")
    mapping = {file_id: _stable_id("file", actor, workspace_id, operation,
        source_id, destination_id, "database-move", file_id) for file_id in sorted(references)}
    moved_block = _remap_database_files(block, mapping)
    if _file_ids(moved_block) & references:
        raise DatabaseMoveShapeUnavailable("database block has an unconverted attachment")
    for row in all_rows:
        for property_id, value in row["cells"].items():
            if property_id not in file_properties or not value:
                continue
            copied = _remap_database_files(value, mapping)
            if _file_ids(copied) & references:
                raise DatabaseMoveShapeUnavailable("database file cell has an unconverted reference")
            cells.append((row["id"], property_id, row["cell_versions"][property_id],
                _canonical_json(value), _canonical_json(copied)))
        body = row.get("body", {}).get("blocks")
        appearance = row.get("appearance")
        old_body = _canonical_json(body) if body is not None and _file_ids(body) else None
        old_appearance = (_canonical_json(appearance)
            if appearance is not None and _file_ids(appearance) else None)
        if old_body is not None or old_appearance is not None:
            new_body = (_canonical_json(_remap_database_files(body, mapping))
                if old_body is not None else None)
            new_appearance = (_canonical_json(_remap_database_files(appearance, mapping))
                if old_appearance is not None else None)
            if ((new_body is not None and _file_ids(json.loads(new_body)) & references)
                    or (new_appearance is not None
                        and _file_ids(json.loads(new_appearance)) & references)):
                raise DatabaseMoveShapeUnavailable("database item has an unconverted attachment")
            rows.append((row["id"], row["version"], old_body, new_body,
                old_appearance, new_appearance, row["id"] in archived_ids))
    files = []
    for file_id in sorted(references):
        row = con.execute("SELECT * FROM workspace_collaboration_files WHERE id=? "
            "AND workspace_id=? AND document_id=? AND state='active'",
            (file_id, workspace_id, source_id)).fetchone()
        if row is None:
            raise DatabaseMoveShapeUnavailable("database attachment is unavailable at its source")
        files.append((file_id, mapping[file_id], int(row["row_version"]),
            str(row["uploader_subject_id"]), str(row["original_name"]),
            str(row["mime_type"]), int(row["size_bytes"]),
            str(row["content_sha256"]), str(row["blob_relative_path"])))
    return {"files": tuple(files), "cells": tuple(cells), "rows": tuple(rows),
        "block": moved_block}


def _copy_database_files(con, plan, actor, workspace_id, source_id, destination_id,
                         operation, shared_files, now):
    for (file_id, copied_id, version, uploader, name, mime, size, sha256, blob) in plan["files"]:
        current = con.execute("SELECT row_version,blob_relative_path FROM workspace_collaboration_files "
            "WHERE id=? AND workspace_id=? AND document_id=? AND state='active'",
            (file_id, workspace_id, source_id)).fetchone()
        if current is None or int(current["row_version"]) != version or current["blob_relative_path"] != blob:
            raise AuthorityUnavailable("database attachment changed during move")
        con.execute("INSERT INTO workspace_collaboration_files(id,workspace_id,document_id,"
            "upload_session_id,uploader_subject_id,original_name,mime_type,size_bytes,"
            "content_sha256,blob_relative_path,state,row_version,created_at,updated_at,"
            "origin_kind,source_file_id,source_document_id,copied_by_subject_id,copy_operation_id) "
            "VALUES(?,?,?,NULL,?,?,?,?,?,?,'active',1,?,?,'copy',?,?,?,?)",
            (copied_id, workspace_id, destination_id, uploader, name, mime, size, sha256,
             blob, now, now, file_id, source_id, actor, operation))
        if file_id not in shared_files:
            retired = con.execute("UPDATE workspace_collaboration_files "
                "SET state='deleted',deleted_at=?,deleted_by_subject_id=?,"
                "row_version=row_version+1,updated_at=? "
                "WHERE id=? AND workspace_id=? AND document_id=? AND state='active' "
                "AND row_version=?", (now, actor, now, file_id, workspace_id,
                    source_id, version))
            if retired.rowcount != 1:
                raise AuthorityUnavailable("database attachment changed during move")
    for row_id, property_id, version, previous, copied in plan["cells"]:
        updated = con.execute("UPDATE workspace_collaboration_database_cells "
            "SET value_json=?,row_version=row_version+1,updated_at=? "
            "WHERE row_id=? AND property_id=? AND row_version=? AND value_json=?",
            (copied, now, row_id, property_id, version, previous))
        if updated.rowcount != 1:
            raise AuthorityUnavailable("database file cell changed during move")
    for row_id, version, old_body, new_body, old_appearance, new_appearance, archived in plan["rows"]:
        if archived:
            membership = con.execute("SELECT t.archived_row_version,r.row_version "
                "FROM workspace_collaboration_database_row_trash_state t "
                "JOIN workspace_collaboration_database_rows r ON r.id=t.row_id "
                "WHERE t.row_id=? AND t.database_id=r.database_id",
                (row_id,)).fetchone()
            if (membership is None or int(membership["row_version"]) != version
                    or int(membership["archived_row_version"]) + 1 != version):
                raise AuthorityUnavailable("archived database row changed during move")
        if old_body is not None:
            updated = con.execute("UPDATE workspace_collaboration_database_row_bodies "
                "SET blocks_json=? WHERE row_id=? AND blocks_json=?",
                (new_body, row_id, old_body))
            if updated.rowcount != 1:
                raise AuthorityUnavailable("database item body changed during move")
        if old_appearance is not None:
            updated = con.execute("UPDATE workspace_collaboration_database_row_appearance "
                "SET appearance_json=? WHERE row_id=? AND appearance_json=?",
                (new_appearance, row_id, old_appearance))
            if updated.rowcount != 1:
                raise AuthorityUnavailable("database item appearance changed during move")
        if not archived:
            updated = con.execute("UPDATE workspace_collaboration_database_rows "
                "SET row_version=row_version+1,updated_at=? WHERE id=? AND row_version=?",
                (now, row_id, version))
            if updated.rowcount != 1:
                raise AuthorityUnavailable("database item changed during move")


def _check_file_plan_capacity(authority, plan):
    if not plan["files"]:
        return
    metadata_bytes = sum(len(_canonical_json(item).encode()) for item in
        (*plan["files"], *plan["cells"], *plan["rows"]))
    usage = shutil.disk_usage(authority.catalog_path.parent)
    remaining = usage.free - metadata_bytes * 4 - 4 * 1024 * 1024
    if remaining < authority.minimum_free_bytes or (usage.total
            and remaining * 100 / usage.total < authority.minimum_free_percent):
        raise CapacityUnavailable("operational database move disk reserve unavailable")


def _shared_file_ids(authority, workspace_id, source_id, database_id, block_id,
                     plan, witnesses, live):
    references = {item[0] for item in plan["files"]}
    if not references:
        return frozenset()
    shared = set()
    with authority._reader() as con:
        con.execute("BEGIN")
        _require_link_witnesses(con, workspace_id, witnesses)
        for document_id, *_ in witnesses:
            page = authority._document(con, document_id)
            body = authority._editing_snapshot(con, page)
            if document_id == source_id:
                body["blocks"] = [item for item in body["blocks"] if item["id"] != block_id]
                body["databases"] = [item for item in body["databases"] if item["id"] != database_id]
            shared.update(_file_ids(body) & references)
            if document_id == source_id:
                shared.update(database_trash.trashed_file_ids(con, source_id,
                    exclude_database_id=database_id) & references)
            if shared == references:
                return frozenset(shared)
    projected = search_projection.materialize([frames for _, frames in live]) if live else []
    for (document_id, _), value in zip(live, projected, strict=True):
        for block in value["blocks"] or ():
            if document_id != source_id or block["id"] != block_id:
                shared.update(_file_ids(block) & references)
        if shared == references:
            break
    return frozenset(shared)


def _retarget_favorites(con, workspace_id, source_id, destination_id, block_id, owner, now):
    """Keep personal favorite order/identity while changing the DB locator."""
    changed = 0
    for table in ("workspace_member_navigation_states", "workspace_guest_navigation_states"):
        rows = con.execute(f"SELECT subject_id,favorites_json,favorite_version FROM {table} "
            "WHERE workspace_id=? AND EXISTS (SELECT 1 FROM json_each(favorites_json) AS favorite "
            "WHERE json_extract(favorite.value,'$.source_page_id')=? "
            "AND json_extract(favorite.value,'$.database_block_id')=?)",
            (workspace_id, source_id, block_id)).fetchall()
        for row in rows:
            try:
                favorites = json.loads(str(row["favorites_json"]))
            except (TypeError, ValueError) as exc:
                raise AuthorityUnavailable("database favorite state is invalid") from exc
            if not isinstance(favorites, list) or any(not isinstance(item, dict) for item in favorites):
                raise AuthorityUnavailable("database favorite state is invalid")
            matches = [item for item in favorites
                if item.get("source_page_id") == source_id
                and item.get("database_block_id") == block_id]
            if len(matches) != 1 or matches[0].get("source_owner_subject_id") != owner:
                raise AuthorityUnavailable("database favorite locator is invalid")
            if int(row["favorite_version"]) >= nws.MAX_HEALING_SEQUENCE_VALUE:
                raise AuthorityUnavailable("database favorite version is exhausted")
            matches[0]["source_page_id"] = destination_id
            updated = con.execute(f"UPDATE {table} SET favorites_json=?,favorite_version=favorite_version+1,"
                "updated_at=? WHERE workspace_id=? AND subject_id=? AND favorite_version=?",
                (_canonical_json(favorites), now, workspace_id, row["subject_id"],
                 row["favorite_version"]))
            if updated.rowcount != 1:
                raise AuthorityUnavailable("database favorite changed during move")
            changed += 1
    return changed


def _workspace_link_witnesses(con, workspace_id):
    """Capture all document stamps and live frames without running Node in SQLite."""
    witnesses, live = [], []
    rows = con.execute("SELECT id,row_version,document_sequence,access_epoch,archived "
        "FROM workspace_collaboration_documents WHERE workspace_id=? ORDER BY id",
        (workspace_id,)).fetchall()
    for row in rows:
        document_id = str(row["id"])
        generation = _generation(con, document_id)
        witnesses.append((document_id, int(row["row_version"]),
            int(row["document_sequence"]), int(row["access_epoch"]),
            int(row["archived"]), generation))
        frames = _frames(con, document_id, generation)
        if frames:
            live.append((document_id, frames))
    return tuple(witnesses), live


def _links_to(block, source_id, database_id):
    linked = (block.get("props") or {}).get("linked_database")
    return (isinstance(linked, dict) and linked.get("document_id") == source_id
        and linked.get("database_id") == database_id)


def _linked_plan(authority, workspace_id, source_id, destination_id, database_id,
                 owned_block, witnesses, live):
    projected = search_projection.materialize([frames for _, frames in live]) if live else []
    live_by_document = dict(zip((identity for identity, _ in live), projected, strict=True))
    frames_by_document = dict(live)
    source_live = live_by_document.get(source_id)
    if source_live is not None and source_live["blocks"] is not None:
        matches = [item for item in source_live["blocks"] if item["id"] == owned_block["id"]]
        if len(matches) != 1 or matches[0]["type"] != owned_block["type"]:
            raise DatabaseMoveShapeUnavailable("live owned database block changed shape")
        live_props = matches[0].get("props") or {}
        if (live_props.get("linked_database")
                or live_props.get("database_id", owned_block["id"]) != database_id):
            raise DatabaseMoveShapeUnavailable("live owned database source changed")
        if _file_ids(matches[0]) and matches[0] != owned_block:
            raise DatabaseMoveShapeUnavailable("live database attachments changed from the canonical block")
    with authority._reader() as con:
        con.execute("BEGIN")
        _require_link_witnesses(con, workspace_id, witnesses)
        candidate = {}
        for row in con.execute("SELECT block.document_id,block.id FROM workspace_collaboration_blocks AS block "
                "JOIN workspace_collaboration_documents AS doc ON doc.id=block.document_id "
                "WHERE doc.workspace_id=? AND json_valid(block.props_json) "
                "AND json_extract(block.props_json,'$.linked_database.document_id')=? "
                "AND json_extract(block.props_json,'$.linked_database.database_id')=?",
                (workspace_id, source_id, database_id)):
            candidate.setdefault(str(row["document_id"]), set()).add(str(row["id"]))
        for document_id, value in live_by_document.items():
            for block in value["blocks"] or ():
                if _links_to(block, source_id, database_id):
                    candidate.setdefault(document_id, set()).add(block["id"])
        captured = {}
        for document_id, identities in sorted(candidate.items()):
            page = authority._document(con, document_id)
            seed = authority._editing_snapshot(con, page)
            generation = _generation(con, document_id)
            frames = frames_by_document.get(document_id, [])
            witness = page_move.parent_rebase.capture_witness(con, document_id, generation, seed)
            versions = {str(row["id"]): int(row["row_version"]) for row in con.execute(
                "SELECT id,row_version FROM workspace_collaboration_blocks WHERE document_id=?", (document_id,))}
            captured[document_id] = (identities, generation, seed, frames, witness, versions)
    # Independent history/CRDT/rich replay must not retain the authorized reader.
    plans = {}
    for document_id, (identities, generation, seed, frames, witness, versions) in captured.items():
        page_move.finalize_witness(witness["post_frame_structure"], seed, document_id, generation)
        canonical = {block["id"]: block for block in seed["blocks"]}
        value = live_by_document.get(document_id)
        if value is not None and value["blocks"] is not None:
            retained, _title, _missing = page_move.parent_rebase.rebased_projection(
                {"id": document_id, "generation": generation,
                 "frames": frames, "snapshot": seed, **witness}, value)
            current = {block["id"]: block for block in retained}
        else:
            current = canonical
        actual = {identity for identity, block in current.items() if _links_to(block, source_id, database_id)}
        if actual != identities:
            raise DatabaseMoveShapeUnavailable("canonical and live linked views differ")
        references = []
        for identity in sorted(identities):
            stored, block = canonical.get(identity), current.get(identity)
            if stored is None or block is None or stored["type"] != block["type"] or identity not in versions:
                raise DatabaseMoveShapeUnavailable("linked view has no canonical block")
            if stored.get("props", {}).get("linked_database") and not _links_to(stored, source_id, database_id):
                raise DatabaseMoveShapeUnavailable("linked view source changed")
            try:
                if linked_databases.source_of(block) != (source_id, database_id):
                    raise DatabaseMoveShapeUnavailable("linked view source changed")
            except linked_databases.LinkedDatabaseShapeUnavailable as exc:
                raise DatabaseMoveShapeUnavailable("linked view shape is invalid") from exc
            new_props = deepcopy(block["props"])
            new_props["linked_database"]["document_id"] = destination_id
            references.append({"block_id": identity, "expected_props": block["props"],
                "new_props": new_props, "row_version": versions[identity]})
        plans[document_id] = {"document_id": document_id, "generation": generation, **witness,
            "seed": seed, "frames": frames, "references": references, "source_document_id": source_id,
            "destination_document_id": destination_id, "database_id": database_id}
    return plans


def _prepare_linked_deltas(plans, base_updates):
    if not plans:
        return {}
    for plan in plans.values():
        if "post_frame_structure" in plan:
            page_move.finalize_witness(plan["post_frame_structure"], plan["seed"], plan["document_id"], plan["generation"])
        if sum(map(len, plan["frames"])) + page_move.restore_frame_bytes(plan) > page_move.MAX_DOCUMENT_BYTES:
            raise AuthorityUnavailable("linked host restore replay requires compaction")
    node = shutil.which("node")
    if not node:
        raise AuthorityUnavailable("linked view retargeting requires Node.js")
    documents = [{**plan, "frames": [base64.b64encode(frame).decode("ascii")
        for frame in plan["frames"]], "references": [{"block_id": ref["block_id"],
        "expected_props": ref["expected_props"]} for ref in plan["references"]],
        "base_update": (base64.b64encode(base_updates[plan["document_id"]]).decode("ascii")
            if plan["document_id"] in base_updates else None)} for plan in plans.values()]
    try:
        process = subprocess.run([node, "--max-old-space-size=256",
            str(Path(__file__).with_name("database_move_linked_delta.cjs"))],
            input=json.dumps({"contract": "code_assist.notion.database_move_linked_delta.v1",
                "documents": documents}).encode(), capture_output=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if process.returncode:
            raise ValueError("linked retarget preparation failed")
        response = json.loads(process.stdout)
        if response.get("contract") != "code_assist.notion.database_move_linked_delta.v1":
            raise ValueError("invalid linked retarget response")
        deltas = {}
        for item in response["documents"]:
            document_id = item["document_id"]
            if document_id not in plans or document_id in deltas:
                raise ValueError("invalid linked retarget document")
            expected = {ref["block_id"] for ref in plans[document_id]["references"]}
            if set(item["retargeted"]) != expected or len(item["retargeted"]) != len(expected):
                raise ValueError("linked retarget identity changed")
            reconciled = item.get("reconciled_insert_ids", [])
            if (not isinstance(reconciled, list) or any(not isinstance(key, str) for key in reconciled)
                    or len(set(reconciled)) != len(reconciled)
                    or not set(reconciled).issubset(page_move.allowed_insert_ids(plans[document_id]))
                    or document_id in base_updates and reconciled):
                raise ValueError("linked retarget insert proof changed")
            payload = base64.b64decode(item["update"], validate=True)
            if not 0 < len(payload) <= 1_048_576:
                raise ValueError("invalid linked retarget update")
            deltas[document_id] = payload
        if set(deltas) != set(plans):
            raise ValueError("missing linked retarget delta")
        return deltas
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        raise AuthorityUnavailable("collaborative linked views could not be retargeted") from exc


def _validate_linked_deltas(plans, base_updates, deltas):
    """Independently check every host body/title and exact source-locator edit."""
    documents = []
    for key, plan in plans.items():
        frames = [*plan["frames"], *([base_updates[key]] if key in base_updates else [])]
        documents.extend((frames, [*frames, deltas[key]]))
    projections = search_projection.materialize(documents) if documents else []
    for index, (key, plan) in enumerate(plans.items()):
        old, updated = projections[index * 2:index * 2 + 2]
        if key in base_updates:
            expected, title = old["blocks"], old["title"]
        elif old["blocks"] is None:
            # The linked engine's existing first-generation bootstrap accepts
            # title-only streams; they contain no competing projected body.
            if plan["generation"] != 1:
                raise AuthorityUnavailable("linked host has no complete current body")
            expected = [{name: value for name, value in block.items() if name != "position"}
                        for block in plan["seed"]["blocks"]]
            title = old["title"] if old["title"] is not None else plan["seed"]["title"] or None
        else:
            expected, title, _missing = page_move.parent_rebase.rebased_projection({
                "id": key, "generation": plan["generation"],
                "frames": plan["frames"], "snapshot": plan["seed"],
                "post_frame_insert_ids": plan["post_frame_insert_ids"],
                "post_frame_structure": plan["post_frame_structure"]}, old)
        if expected is None:
            raise AuthorityUnavailable("linked host base update has no body")
        references = {item["block_id"]: item for item in plan["references"]}
        found = set()
        retained = []
        for block in expected:
            reference = references.get(block["id"])
            if reference is not None:
                if block["props"] != reference["expected_props"]:
                    raise AuthorityUnavailable("linked host settings changed before retargeting")
                found.add(block["id"])
                block = {**block, "props": reference["new_props"]}
            retained.append(block)
        if (found != set(references) or updated["blocks"] != retained or updated["title"] != title):
            raise AuthorityUnavailable("linked retarget changed unrelated host content")
        from .parent_restore_recovery import validate_rich
        validate_rich({"id": key, "generation": plan["generation"],
            "snapshot": plan["seed"], "post_frame_structure": plan.get("post_frame_structure", {}),
            "frames": [*plan["frames"], *([base_updates[key]] if key in base_updates else [])]}, deltas[key])


def _require_link_witnesses(con, workspace_id, expected):
    rows = con.execute("SELECT id,row_version,document_sequence,access_epoch,archived "
        "FROM workspace_collaboration_documents WHERE workspace_id=? ORDER BY id",
        (workspace_id,)).fetchall()
    current = tuple((str(row["id"]), int(row["row_version"]),
        int(row["document_sequence"]), int(row["access_epoch"]),
        int(row["archived"]), _generation(con, str(row["id"]))) for row in rows)
    if current != expected:
        raise AuthorityUnavailable("workspace linked-reference scan changed during move")


def read_owned_database_move_result(authority, actor_subject_id, workspace_id,
                                    source_document_id, destination_document_id,
                                    database_id, *, operation_id,
                                    expected_source_sequence,
                                    expected_destination_sequence,
                                    expected_workspace_epoch,
                                    expected_source_epoch,
                                    expected_destination_epoch):
    (actor, workspace_id, source_id, destination_id, database_id, operation, digest,
     _, _, _, _, _) = _request(actor_subject_id, workspace_id, source_document_id,
        destination_document_id, database_id, operation_id=operation_id,
        expected_source_sequence=expected_source_sequence,
        expected_destination_sequence=expected_destination_sequence,
        expected_workspace_epoch=expected_workspace_epoch,
        expected_source_epoch=expected_source_epoch,
        expected_destination_epoch=expected_destination_epoch)
    with authority._reader() as con:
        con.execute("BEGIN")
        _owner(authority, con, actor, workspace_id, source_id)
        _owner(authority, con, actor, workspace_id, destination_id)
        previous = _replay(authority, con, actor, workspace_id, source_id,
            destination_id, database_id, operation, digest)
        if previous is None:
            return {"state": "unknown"}
        destination = authority._document(con, destination_id)
        return {"state": "committed", "result": previous,
            "current": {"destination_archived": bool(destination["archived"]),
                "document_sequence": destination["document_sequence"],
                "document_epoch": destination["access_epoch"]}}


def move_owned_database(authority, actor_subject_id, workspace_id, source_document_id,
                        destination_document_id, database_id, *, operation_id,
                        expected_source_sequence, expected_destination_sequence,
                        expected_workspace_epoch, expected_source_epoch,
                        expected_destination_epoch, fault=None):
    (actor, workspace_id, source_id, destination_id, database_id, operation, digest,
     source_sequence, destination_sequence, workspace_epoch, source_epoch,
     destination_epoch) = _request(actor_subject_id, workspace_id, source_document_id,
        destination_document_id, database_id, operation_id=operation_id,
        expected_source_sequence=expected_source_sequence,
        expected_destination_sequence=expected_destination_sequence,
        expected_workspace_epoch=expected_workspace_epoch,
        expected_source_epoch=expected_source_epoch,
        expected_destination_epoch=expected_destination_epoch)
    with authority._reader() as con:
        con.execute("BEGIN")
        previous = _replay(authority, con, actor, workspace_id, source_id,
            destination_id, database_id, operation, digest)
        if previous is not None:
            return previous
        scope = _scope(authority, con, actor, workspace_id, source_id, destination_id,
            database_id, source_sequence, destination_sequence, workspace_epoch,
            source_epoch, destination_epoch)
        workspace, source, destination, database, block, source_body, destination_body = scope
        source_database = next(item for item in source_body["databases"] if item["id"] == database_id)
        file_plan = _database_file_plan(con, actor, workspace_id, source_id, destination_id,
            source_database, block, operation)
        comment_plan = _comment_plan(con, source_id, database_id, block["id"])
        link_witnesses, live = _workspace_link_witnesses(con, workspace_id)
        sources = {}
        for page, body, insert, count in ((source, source_body, None, 1),
                                          (destination, destination_body, block, 0)):
            doc = page["id"]
            generation = _generation(con, doc)
            sources[doc] = {"id": doc, "stamp": page_move._stamp(page, generation),
                "generation": generation, "frames": _frames(con, doc, generation),
                "snapshot": body, "match_kind": "owned_database", "match_id": database_id,
                **page_move.parent_rebase.capture_witness(con, doc, generation, body),
                "expected_match_count": count,
                "insert": file_plan["block"] if insert is not None else None}
        sources[source_id]["database_version"] = database["row_version"]
    linked_plans = _linked_plan(authority, workspace_id, source_id, destination_id,
        database_id, block, link_witnesses, live)
    shared_files = _shared_file_ids(authority, workspace_id, source_id,
        database_id, block["id"], file_plan, link_witnesses, live)
    _check_file_plan_capacity(authority, file_plan)
    deltas = page_move._prepare_deltas(sources, database_id, operation)
    page_move.parent_rebase.validate_deltas(sources, deltas, database_id)
    if (deltas[source_id]["removed"] != [block["id"]]
            or not deltas[source_id]["changed"]
            or deltas[destination_id]["removed"]
            or deltas[destination_id]["placed"] != block["id"]
            or not deltas[destination_id]["inserted"]):
        raise AuthorityUnavailable("database transfer delta changed identity")
    base_updates = {document_id: delta["payload"] for document_id, delta in deltas.items()}
    linked_deltas = _prepare_linked_deltas(linked_plans, base_updates)
    _validate_linked_deltas(linked_plans, base_updates, linked_deltas)

    with authority._transaction() as con:
        previous = _replay(authority, con, actor, workspace_id, source_id,
            destination_id, database_id, operation, digest)
        if previous is not None:
            return previous
        scope = _scope(authority, con, actor, workspace_id, source_id, destination_id,
            database_id, source_sequence, destination_sequence, workspace_epoch,
            source_epoch, destination_epoch)
        workspace, source, destination, database, current_block, source_body, destination_body = scope
        source_database = next(item for item in source_body["databases"] if item["id"] == database_id)
        if _database_file_plan(con, actor, workspace_id, source_id, destination_id,
                source_database, current_block, operation) != file_plan:
            raise AuthorityUnavailable("database attachment plan changed during move")
        if _comment_plan(con, source_id, database_id, block["id"]) != comment_plan:
            raise AuthorityUnavailable("database comment plan changed during move")
        _require_link_witnesses(con, workspace_id, link_witnesses)
        if current_block != block or database["row_version"] != sources[source_id]["database_version"]:
            raise DocumentSequenceConflict(source["document_sequence"])
        for page in (source, destination):
            if page_move._stamp(page, _generation(con, page["id"])) != sources[page["id"]]["stamp"]:
                raise DocumentSequenceConflict(page["document_sequence"])
        now = int(time.time())
        con.execute("DELETE FROM workspace_collaboration_database_activity WHERE database_id=?", (database_id,))
        updated_block = con.execute("UPDATE workspace_collaboration_blocks "
            "SET document_id=?,position=?,content=?,props_json=?,"
            "row_version=row_version+1,updated_at=? "
            "WHERE id=? AND document_id=? AND content=? AND props_json=?",
            (destination_id, len(destination_body["blocks"]),
                file_plan["block"]["content"],
                _canonical_json(file_plan["block"]["props"]), now, block["id"], source_id,
                block["content"], _canonical_json(block["props"])))
        if updated_block.rowcount != 1:
            raise AuthorityUnavailable("owned database block changed during move")
        authority._rewrite_block_positions(con, source_id,
            [item["id"] for item in source_body["blocks"] if item["id"] != block["id"]], now)
        con.execute("UPDATE workspace_collaboration_databases SET document_id=?,"
            "row_version=row_version+1,updated_at=? WHERE id=? AND document_id=?",
            (destination_id, now, database_id, source_id))
        for comment_id, version in comment_plan:
            updated_comment = con.execute("UPDATE workspace_collaboration_comments "
                "SET document_id=?,row_version=row_version+1,updated_at=? "
                "WHERE id=? AND document_id=? AND row_version=?",
                (destination_id, now, comment_id, source_id, version))
            if updated_comment.rowcount != 1:
                raise AuthorityUnavailable("database comment changed during move")
        _copy_database_files(con, file_plan, actor, workspace_id, source_id,
            destination_id, operation, shared_files, now)
        favorite_count = _retarget_favorites(con, workspace_id, source_id,
            destination_id, block["id"], actor, now)
        for document_id, plan in linked_plans.items():
            for reference in plan["references"]:
                updated = con.execute("UPDATE workspace_collaboration_blocks SET props_json=?,"
                    "row_version=row_version+1,updated_at=? WHERE document_id=? AND id=? "
                    "AND row_version=?", (_canonical_json(reference["new_props"]), now,
                    document_id, reference["block_id"], reference["row_version"]))
                if updated.rowcount != 1:
                    raise AuthorityUnavailable("linked view changed during move")
        for page in (source, destination):
            doc = page["id"]
            page_move._append_link_delta(authority, con, actor, workspace, page,
                deltas[doc]["payload"], sources[doc]["generation"], operation, now,
                operation_action=ACTION, receipt_action="collaboration.database_move_delta",
                client_id="database-move-service", binding_contract=CONTRACT,
                identity_prefix="dbmupd")
        for document_id, delta in linked_deltas.items():
            page_move._append_link_delta(authority, con, actor, workspace,
                authority._document(con, document_id), delta,
                linked_plans[document_id]["generation"], operation, now,
                operation_action=ACTION, receipt_action="collaboration.database_move_linked_delta",
                client_id="database-move-service", binding_contract=CONTRACT,
                identity_prefix="dbmlinkupd")
        affected = tuple(dict.fromkeys((source_id, destination_id, *linked_plans)))
        for doc in affected:
            updated = authority._document(con, doc)
            checksum = _sha256(_canonical_json(authority._editing_snapshot(con, updated)))
            con.execute("UPDATE workspace_collaboration_documents SET content_sha256=? WHERE id=?",
                (checksum, doc))
        authority._bump_document_epochs(con, affected)
        authority._bump_workspace(con, workspace_id)
        con.execute("UPDATE workspace_collaboration_documents SET document_sequence=document_sequence+1,"
            "row_version=row_version+1 WHERE id=?", (destination_id,))
        workspace = authority._workspace(con, workspace_id)
        destination = authority._document(con, destination_id)
        database_activity.touch(con, database_ids=(database_id,), document_id=destination_id,
            actor_subject_id=actor, edited_at=now,
            document_sequence=destination["document_sequence"])
        result = {"contract": CONTRACT, "operation_id": operation,
            "workspace_id": workspace_id,
            "source_document_id": source_id, "destination_document_id": destination_id,
            "database_id": database_id, "block_id": block["id"],
            "document_sequence": destination["document_sequence"],
            "workspace_epoch": workspace["security_epoch"],
            "source_document_epoch": authority._document(con, source_id)["access_epoch"],
            "destination_document_epoch": destination["access_epoch"],
            "refresh_page_ids": list(affected),
            "retargeted_favorite_count": favorite_count,
            "retargeted_comment_count": len(comment_plan),
            "copied_file_count": len(file_plan["files"]),
            "retired_source_file_count": len(file_plan["files"]) - len(shared_files),
            "retargeted_linked_view_count": sum(len(plan["references"])
                for plan in linked_plans.values())}
        stable = authority._finish(con, actor=actor, action=ACTION, operation_id=operation,
            request_sha256=digest, workspace_id=workspace_id, document_id=destination_id,
            workspace_epoch=workspace["security_epoch"],
            document_epoch=destination["access_epoch"],
            document_sequence=destination["document_sequence"], event_type="database.moved",
            event_payload=result, result=result, fault=fault)
        return {**stable, "replayed": False}
