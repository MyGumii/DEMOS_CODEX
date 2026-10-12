"""Canonical, reversible subtree archive with exact membership and access fences.

Only the current workspace owner may manage their own pages here, matching the
existing workspace lifecycle contract. Node prepares parent-link deltas before
the write transaction. No legacy page store, revision body or CRDT generation is
replaced. Actual browser and rollout verification remain separate gates.
"""
from __future__ import annotations

import json
import time

from code_assist_v1 import notion_workspace_page_archive_migration as migration
from code_assist_v1 import notion_workspace_native_page_migration as native_page_migration
from code_assist_v1 import notion_workspace_member_navigation_migration as member_navigation_migration
from code_assist_v1 import notion_workspace_file_copy_migration as file_copy_migration
from code_assist_v1 import notion_workspace_native_team_migration as team_migration
from code_assist_v1 import notion_workspace_row_body_migration as row_body_migration
from code_assist_v1 import notion_workspace_row_appearance_migration as row_appearance_migration
from code_assist_v1 import notion_workspace_same_document_file_copy_migration as same_file_migration
from code_assist_v1 import notion_workspace_database_activity_migration as activity_migration
from code_assist_v1 import notion_workspace_database_trash_migration as trash_migration
from code_assist_v1 import notion_workspace_database_row_trash_migration as row_trash_migration
from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement_migration
from code_assist_v1 import notion_workspace_custom_emoji_migration as custom_emoji_migration
from code_assist_v1 import notion_workspace_database_item_page_layout_migration as item_layout_migration
from code_assist_v1 import notion_workspace_database_template_default_migration as template_default_migration
from .canonical_authority import (
    AuthorityUnavailable, EpochConflict, NotFoundOrDenied, _canonical_json,
    _identifier, _operation_id, _positive, _request_sha256, _sha256, _stable_id,
)
from .editing_authority import DocumentSequenceConflict
from .page_appearance import _metadata
from . import page_move
from .title_projection import _frames, _generation, _materialize, MAX_DOCUMENT_BYTES

CONTRACT = "code_assist.notion.page_archive.v1"
MAX_SUBTREE = 128


def read_page_lifecycle_result(authority, actor_subject_id, workspace_id, document_id, *, archived,
                               expected_document_sequence, expected_workspace_epoch, expected_document_epoch,
                               operation_id):
    """Read an exact receipt without needing ordinary access to an archived body.

    An absent receipt is unknown, not proof that an in-flight request cannot
    commit. This path never retries the mutation or decodes a document body.
    """
    if type(archived) is not bool:
        raise ValueError("archived must be a boolean")
    actor = authority._require_active(actor_subject_id)
    workspace_id = _identifier(workspace_id, field="workspace_id")
    document_id = _identifier(document_id, field="document_id")
    operation = _operation_id(operation_id)
    seq = _positive(expected_document_sequence, field="expected_document_sequence")
    we = _positive(expected_workspace_epoch, field="expected_workspace_epoch")
    de = _positive(expected_document_epoch, field="expected_document_epoch")
    action = "collaboration.page_archive" if archived else "collaboration.page_restore"
    digest = _request_sha256(actor, action, {"workspace_id": workspace_id, "document_id": document_id,
        "expected_document_sequence": seq, "expected_workspace_epoch": we, "expected_document_epoch": de})
    with authority._reader() as con:
        con.execute("BEGIN")
        workspace, page = _owner(authority, con, actor, workspace_id, document_id)
        result = authority._replay(con, actor=actor, action=action, operation_id=operation, request_sha256=digest,
            authorize=lambda: _owner(authority, con, actor, workspace_id, document_id))
        if result is None:
            return {"state": "unknown"}
        for key in result["page_ids"]:
            _owner(authority, con, actor, workspace_id, key)
        return {"state": "committed", "result": result, "current": {
            "archived": bool(page["archived"]), "document_sequence": page["document_sequence"],
            "document_epoch": page["access_epoch"], "workspace_epoch": workspace["security_epoch"],
            "workspace": {"id": workspace_id, "version": workspace["row_version"]}}}


def marker_current(con):
    row = con.execute("SELECT version,name,checksum,actual_schema_sha256 FROM workspace_schema_migrations "
                      "ORDER BY version DESC LIMIT 1").fetchone()
    return row is not None and tuple(row) in {
        (item.TARGET_VERSION, item.TARGET_NAME, item.TARGET_CHECKSUM, item.TARGET_ACTUAL_SCHEMA_SHA256)
        for item in (migration, native_page_migration, member_navigation_migration, file_copy_migration, team_migration, row_body_migration, row_appearance_migration, same_file_migration, activity_migration, trash_migration, row_trash_migration, placement_migration, custom_emoji_migration, item_layout_migration, template_default_migration)
    }


def _owner(authority, con, actor, workspace_id, document_id=None):
    authority._require_active(actor)
    if not marker_current(con):
        raise AuthorityUnavailable("canonical page archive is not installed")
    workspace = authority._workspace(con, workspace_id)
    if authority._workspace_role(con, workspace_id, actor) != "owner":
        raise NotFoundOrDenied("not found or permission denied")
    page = authority._document(con, document_id) if document_id else None
    if page is not None and (page["workspace_id"] != workspace_id or page["legacy_owner_subject_id"] != actor):
        raise NotFoundOrDenied("not found or permission denied")
    return workspace, page


def _scope(authority, con, actor, workspace_id, document_id, restoring):
    workspace, page = _owner(authority, con, actor, workspace_id, document_id)
    if bool(page["archived"]) != restoring:
        raise NotFoundOrDenied("page is not in the requested lifecycle state")
    if restoring:
        state = con.execute("SELECT batch_id FROM workspace_collaboration_page_archive_state WHERE document_id=?",
                            (document_id,)).fetchone()
        if state is None:
            raise AuthorityUnavailable("archived page membership is missing")
        ids = {row[0] for row in con.execute(
            "WITH RECURSIVE subtree(id) AS (SELECT ? UNION SELECT d.id "
            "FROM workspace_collaboration_documents d JOIN subtree s ON d.parent_document_id=s.id "
            "JOIN workspace_collaboration_page_archive_state a ON a.document_id=d.id "
            "WHERE d.workspace_id=? AND d.archived=1 AND a.batch_id=?) "
            "SELECT id FROM subtree LIMIT ?", (document_id, workspace_id, state["batch_id"], MAX_SUBTREE + 1))}
        # Restore the path to this page, without reviving its ancestor's other
        # children or descendants trashed by an earlier independent command.
        top = page
        seen = set()
        while top["parent_document_id"]:
            if top["id"] in seen:
                raise ValueError("cyclic page hierarchy")
            seen.add(top["id"])
            _, parent = _owner(authority, con, actor, workspace_id, top["parent_document_id"])
            if not parent["archived"]:
                break
            ids.add(parent["id"])
            if len(ids) > MAX_SUBTREE:
                raise ValueError("page subtree exceeds the lifecycle limit")
            top = parent
    else:
        role, _ = authority._resolution_on_connection(con, actor, workspace_id, document_id)
        if role is None:
            raise NotFoundOrDenied("not found or permission denied")
        ids = {row[0] for row in con.execute(
            "WITH RECURSIVE subtree(id) AS (SELECT ? UNION SELECT d.id "
            "FROM workspace_collaboration_documents d JOIN subtree s ON d.parent_document_id=s.id "
            "WHERE d.workspace_id=? AND d.archived=0) SELECT id FROM subtree LIMIT ?",
            (document_id, workspace_id, MAX_SUBTREE + 1))}
        top = page
    if len(ids) > MAX_SUBTREE:
        raise ValueError("page subtree exceeds the lifecycle limit")
    rows = {}
    for key in sorted(ids):
        _, row = _owner(authority, con, actor, workspace_id, key)
        page_move._unlocked(con, row, actor)
        state = con.execute("SELECT * FROM workspace_collaboration_page_archive_state WHERE document_id=?", (key,)).fetchone()
        if bool(row["archived"]) != restoring or (state is not None) != restoring:
            raise AuthorityUnavailable("page archive state is inconsistent")
        rows[key] = row
    parent = None
    if top["parent_document_id"]:
        if top["parent_document_id"] in ids:
            raise ValueError("cyclic page hierarchy")
        parent = page_move._authorize(authority, con, actor, workspace_id, top["parent_document_id"], "edit_content")
        page_move._unlocked(con, parent, actor)
    return workspace, page, rows, top, parent


def _stamp(row):
    return (row["id"], row["row_version"], row["document_sequence"], row["access_epoch"], row["parent_document_id"], row["archived"])


def _apply_parent_delta(authority, con, actor, workspace, parent, source, delta, operation, now):
    if not delta["changed"]:
        return
    canonical = {block["id"]: block for block in source["snapshot"]["blocks"]}
    for key in sorted(set(delta["removed"]) & canonical.keys()):
        page_move._structural_link(authority, con, actor, workspace, parent, "block.delete", key, {}, operation, now)
    if delta["placed"]:
        key = delta["placed"]
        payload = {"after_block_id": delta["after_block_id"]}
        if delta["inserted"]:
            payload.update(source["insert"])
            kind = "block.insert"
        elif key in canonical:
            kind = "block.move"
        else:
            raise AuthorityUnavailable("current child link requires structural reconciliation")
        page_move._structural_link(authority, con, actor, workspace, parent, kind, key, payload, operation, now)
    page_move._append_link_delta(authority, con, actor, workspace, parent, delta["payload"], source["generation"], operation, now)


def set_page_archived(authority, actor_subject_id, workspace_id, document_id, *, archived,
                      expected_document_sequence, expected_workspace_epoch, expected_document_epoch,
                      operation_id, fault=None):
    if type(archived) is not bool:
        raise ValueError("archived must be a boolean")
    actor = authority._require_active(actor_subject_id)
    workspace_id = _identifier(workspace_id, field="workspace_id")
    document_id = _identifier(document_id, field="document_id")
    operation = _operation_id(operation_id)
    seq = _positive(expected_document_sequence, field="expected_document_sequence")
    we = _positive(expected_workspace_epoch, field="expected_workspace_epoch")
    de = _positive(expected_document_epoch, field="expected_document_epoch")
    action = "collaboration.page_archive" if archived else "collaboration.page_restore"
    digest = _request_sha256(actor, action, {"workspace_id": workspace_id, "document_id": document_id,
        "expected_document_sequence": seq, "expected_workspace_epoch": we, "expected_document_epoch": de})

    def replay(con):
        result = authority._replay(con, actor=actor, action=action, operation_id=operation, request_sha256=digest,
            authorize=lambda: _owner(authority, con, actor, workspace_id, document_id))
        if result is not None:
            for key in result["page_ids"]:
                _owner(authority, con, actor, workspace_id, key)
        return result

    def collect(con):
        values = _scope(authority, con, actor, workspace_id, document_id, not archived)
        workspace, page = values[:2]
        if workspace["security_epoch"] != we or page["access_epoch"] != de:
            raise EpochConflict(workspace["security_epoch"], page["access_epoch"])
        if page["document_sequence"] != seq:
            raise DocumentSequenceConflict(page["document_sequence"])
        return values

    with authority._reader() as con:
        con.execute("BEGIN")
        previous = replay(con)
        if previous is not None:
            return previous
        workspace, page, rows, top, parent = collect(con)
        stamps = {key: _stamp(row) for key, row in rows.items()}
        source = None
        title_frames = []
        if parent is not None:
            generation = _generation(con, parent["id"])
            snapshot = authority._editing_snapshot(con, parent)
            source = {"id": parent["id"], "stamp": page_move._stamp(parent, generation), "generation": generation,
                "frames": _frames(con, parent["id"], generation), "snapshot": snapshot,
                **page_move.parent_rebase.capture_witness(con, parent["id"], generation, snapshot),
                "insert": None}
            if not archived:
                state = con.execute("SELECT link_anchor_json FROM workspace_collaboration_page_archive_state WHERE document_id=?", (top["id"],)).fetchone()
                source["restore_anchor"] = json.loads(state["link_anchor_json"]) if state["link_anchor_json"] else None
                title_frames = _frames(con, top["id"], _generation(con, top["id"]))
                if sum(map(len, title_frames)) > MAX_DOCUMENT_BYTES:
                    raise AuthorityUnavailable("page title replay requires compaction")
        top_id = top["id"]
        title = top["title"]
    if source is not None and not archived:
        current_title = _materialize([title_frames])[0] if title_frames else None
        source["insert"] = {"id": _stable_id("archive_link", actor, operation, top_id), "type": "child_page",
                            "content": title if current_title is None else current_title, "props": {"page_id": top_id}}
    # Namespace nested structural/Yjs operation IDs away from page-move commands.
    link_operation = _stable_id("archive_links", actor, action, operation)
    sources = {source["id"]: source} if source else {}
    deltas = page_move._prepare_deltas(sources, top_id, link_operation)
    page_move.parent_rebase.validate_deltas(sources, deltas, top_id)

    with authority._transaction() as con:
        previous = replay(con)
        if previous is not None:
            return previous
        workspace, page, rows, top, parent = collect(con)
        if top["id"] != top_id or {key: _stamp(row) for key, row in rows.items()} != stamps:
            raise DocumentSequenceConflict(page["document_sequence"])
        if (parent is None) != (source is None) or (source is not None and (
            parent["id"] != source["id"] or page_move._stamp(parent, _generation(con, parent["id"])) != source["stamp"]
        )):
            raise DocumentSequenceConflict(page["document_sequence"])
        now = int(time.time())
        batch = _stable_id("archive_batch", actor, action, operation)
        for key in rows:
            if archived:
                con.execute("INSERT INTO workspace_collaboration_page_archive_state "
                    "(document_id,workspace_id,batch_id,root_document_id,archived_by_subject_id,archived_at,link_anchor_json) VALUES(?,?,?,?,?,?,?)",
                    (key, workspace_id, batch, document_id, actor, now,
                     _canonical_json(deltas[parent["id"]]["restore_anchor"]) if source and key == top_id and deltas[parent["id"]]["restore_anchor"] is not None else None))
            else:
                con.execute("DELETE FROM workspace_collaboration_page_archive_state WHERE document_id=?", (key,))
            con.execute("UPDATE workspace_collaboration_documents SET archived=?,archived_at=?,access_epoch=access_epoch+1,"
                        "document_sequence=document_sequence+1,row_version=row_version+1,updated_at=? WHERE id=?",
                        (int(archived), now if archived else None, now, key))
            refreshed = authority._document(con, key)
            checksum = _sha256(_canonical_json(authority._editing_snapshot(con, refreshed)))
            con.execute("UPDATE workspace_collaboration_documents SET content_sha256=? WHERE id=?", (checksum, key))
        authority._bump_workspace(con, workspace_id)
        workspace = authority._workspace(con, workspace_id)
        if source:
            _apply_parent_delta(authority, con, actor, workspace, parent, source, deltas[parent["id"]], link_operation, now)
        event = "page.archived" if archived else "page.restored"
        for key in rows:
            row = authority._document(con, key)
            if key != document_id:
                member_operation = _stable_id("archive_member", actor, action, operation, key)
                member = {"document_id": key, "root_document_id": document_id, "archived": archived,
                          "document_sequence": row["document_sequence"]}
                authority._finish(con, actor=actor, action=action + "_member", operation_id=member_operation,
                    request_sha256=_request_sha256(actor, action, {"operation": operation, "document_id": key}),
                    workspace_id=workspace_id, document_id=key, workspace_epoch=workspace["security_epoch"],
                    document_epoch=row["access_epoch"], document_sequence=row["document_sequence"],
                    event_type=event, event_payload=member, result=member, fault=None)
        page = authority._document(con, document_id)
        result = {"contract": CONTRACT, "workspace_id": workspace_id, "page_id": document_id,
            "workspace": {"id": workspace_id, "version": workspace["row_version"]},
            "page_ids": sorted(rows), "archived": archived, "document_sequence": page["document_sequence"],
            "workspace_epoch": workspace["security_epoch"], "document_epoch": page["access_epoch"],
            "archived_count" if archived else "restored_count": len(rows),
            "refresh_page_ids": [*sorted(rows), *([parent["id"]] if parent else [])]}
        authority._require_active(actor)
        return authority._finish(con, actor=actor, action=action, operation_id=operation, request_sha256=digest,
            workspace_id=workspace_id, document_id=document_id, workspace_epoch=workspace["security_epoch"],
            document_epoch=page["access_epoch"], document_sequence=page["document_sequence"],
            event_type=event, event_payload=result, result=result, fault=fault)


def list_archived_pages(authority, actor_subject_id, workspace_id, *, query=""):
    actor = authority._require_active(actor_subject_id)
    workspace_id = _identifier(workspace_id, field="workspace_id")
    if not isinstance(query, str) or len(query) > 200:
        raise ValueError("query must be at most 200 characters")
    with authority._reader() as con:
        con.execute("BEGIN")
        workspace, _ = _owner(authority, con, actor, workspace_id)
        rows = con.execute("SELECT d.*,a.archived_by_subject_id FROM workspace_collaboration_documents d "
            "JOIN workspace_collaboration_page_archive_state a ON a.document_id=d.id "
            "WHERE d.workspace_id=? AND d.legacy_owner_subject_id=? AND d.archived=1 ORDER BY d.archived_at DESC,d.id",
            (workspace_id, actor)).fetchmany(1001)
        if len(rows) > 1000:
            raise AuthorityUnavailable("trash listing requires pagination")
        frames = [_frames(con, row["id"], _generation(con, row["id"])) for row in rows]
        if sum(len(frame) for document in frames for frame in document) > MAX_DOCUMENT_BYTES:
            raise AuthorityUnavailable("trash title replay requires compaction")
        items = [{**_metadata(row, workspace, "full_access", actor), "archived": True, "archived_at": row["archived_at"],
            "archived_by_subject_id": row["archived_by_subject_id"],
            "can_edit": False, "can_move": False, "can_change_settings": False, "can_change_appearance": False} for row in rows]
    titles = _materialize(frames) if frames else []
    with authority._reader() as con:
        con.execute("BEGIN")
        current, _ = _owner(authority, con, actor, workspace_id)
        if current["security_epoch"] != workspace["security_epoch"]:
            raise EpochConflict(current["security_epoch"])
        for index, row in enumerate(rows):
            _, latest = _owner(authority, con, actor, workspace_id, row["id"])
            if _stamp(row) != _stamp(latest):
                raise DocumentSequenceConflict(latest["document_sequence"])
            if titles[index] is not None:
                items[index]["title"] = titles[index]
        authority._require_active(actor)
    items = [item for item in items if query.casefold() in item["title"].casefold()]
    return {"workspace_id": workspace_id, "items": items, "count": len(items)}
