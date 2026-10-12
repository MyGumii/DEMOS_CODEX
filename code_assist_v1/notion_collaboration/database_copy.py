"""Atomic copy of one owned database within its current canonical page.

This owner-only service is not exposed by the UI capability until recovery and
the remaining database copy shapes are wired through the public route.
"""
from __future__ import annotations

from dataclasses import replace
import time

from code_assist_v1.notion_workspace_store import WorkspaceStore
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

from .canonical_authority import (
    AuthorityUnavailable, EpochConflict, NotFoundOrDenied, _canonical_json,
    _identifier, _operation_id, _positive, _request_sha256, _stable_id,
)
from .editing_authority import DocumentSequenceConflict
from . import page_archive, page_copy, page_copy_remap, page_copy_source, page_move
from .title_projection import _frames, _generation

CONTRACT = "code_assist.notion.owned_database_copy.v1"
ACTION = "collaboration.database_copy"


def _request(actor_subject_id, workspace_id, document_id, database_id, *, operation_id,
             expected_document_sequence, expected_workspace_epoch, expected_document_epoch,
             local_origin, include_content):
    actor = _identifier(actor_subject_id, field="actor_subject_id")
    workspace_id = _identifier(workspace_id, field="workspace_id")
    document_id = _identifier(document_id, field="document_id")
    database_id = _identifier(database_id, field="database_id")
    operation = _operation_id(operation_id)
    sequence = _positive(expected_document_sequence, field="expected_document_sequence")
    workspace_epoch = _positive(expected_workspace_epoch, field="expected_workspace_epoch")
    document_epoch = _positive(expected_document_epoch, field="expected_document_epoch")
    if not isinstance(local_origin, str):
        raise ValueError("configured application origin must be a string")
    if type(include_content) is not bool:
        raise ValueError("include_content must be a boolean")
    digest = _request_sha256(actor, ACTION, {"workspace_id": workspace_id,
        "document_id": document_id, "database_id": database_id,
        "expected_document_sequence": sequence, "expected_workspace_epoch": workspace_epoch,
        "expected_document_epoch": document_epoch, "local_origin": local_origin,
        "include_content": include_content})
    return actor, workspace_id, document_id, database_id, operation, digest


def _owner(authority, con, actor, workspace_id, document_id):
    marker = con.execute("SELECT version,name,checksum,actual_schema_sha256 "
        "FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
    versions = (same_file_migration, activity_migration, trash_migration,
        row_trash_migration, placement_migration, custom_emoji_migration,
        item_layout_migration, template_default_migration,
        database_sharing_migration, upload_scope_migration, direct_favorite_migration)
    if marker is None or tuple(marker) not in {(item.TARGET_VERSION, item.TARGET_NAME,
            item.TARGET_CHECKSUM, item.TARGET_ACTUAL_SCHEMA_SHA256) for item in versions}:
        raise AuthorityUnavailable("canonical database copy is not installed")
    if int(marker[0]) in {
            database_sharing_migration.TARGET_VERSION,
            upload_scope_migration.TARGET_VERSION,
            direct_favorite_migration.TARGET_VERSION}:
        workspace, page = page_copy_source._owner(
            authority, con, actor, workspace_id, document_id)
    else:
        workspace, page = page_archive._owner(
            authority, con, actor, workspace_id, document_id)
    return workspace, page


def _replay(authority, con, actor, workspace_id, document_id, database_id, operation, digest):
    previous = authority._replay(con, actor=actor, action=ACTION, operation_id=operation,
        request_sha256=digest, authorize=lambda: _owner(authority, con, actor, workspace_id, document_id))
    if previous is None:
        return None
    if (previous.get("contract") != CONTRACT or previous.get("workspace_id") != workspace_id
            or previous.get("document_id") != document_id
            or previous.get("source_database_id") != database_id):
        raise AuthorityUnavailable("stored database copy result scope is invalid")
    return {**previous, "replayed": True}


def read_owned_database_copy_result(authority, actor_subject_id, workspace_id, document_id, database_id, *,
        operation_id, expected_document_sequence, expected_workspace_epoch,
        expected_document_epoch, local_origin="", include_content=True):
    actor, ws, doc, database, operation, digest = _request(actor_subject_id, workspace_id,
        document_id, database_id, operation_id=operation_id,
        expected_document_sequence=expected_document_sequence,
        expected_workspace_epoch=expected_workspace_epoch,
        expected_document_epoch=expected_document_epoch, local_origin=local_origin,
        include_content=include_content)
    with authority._reader() as con:
        con.execute("BEGIN")
        _owner(authority, con, actor, ws, doc)
        previous = _replay(authority, con, actor, ws, doc, database, operation, digest)
        if previous is None:
            return {"state": "unknown"}
        current = authority._document(con, doc)
        return {"state": "committed", "result": previous,
            "current": {"archived": bool(current["archived"]),
                "document_sequence": current["document_sequence"],
                "document_epoch": current["access_epoch"]}}


def copy_owned_database(authority, actor_subject_id, workspace_id, document_id, database_id, *,
        operation_id, expected_document_sequence, expected_workspace_epoch,
        expected_document_epoch, local_origin="", include_content=True, fault=None):
    actor, ws, doc, database, operation, digest = _request(actor_subject_id, workspace_id,
        document_id, database_id, operation_id=operation_id,
        expected_document_sequence=expected_document_sequence,
        expected_workspace_epoch=expected_workspace_epoch,
        expected_document_epoch=expected_document_epoch, local_origin=local_origin,
        include_content=include_content)
    with authority._reader() as con:
        con.execute("BEGIN")
        _owner(authority, con, actor, ws, doc)
        previous = _replay(authority, con, actor, ws, doc, database, operation, digest)
        if previous is not None:
            return previous

    try:
        # The existing preparation witness also covers descendants. Copy only
        # the root descriptor, while retaining that full witness for commit CAS.
        prepared = page_copy_source.prepare_page_copy(authority, actor, ws, doc,
            expected_document_sequence=expected_document_sequence,
            expected_workspace_epoch=expected_workspace_epoch,
            expected_document_epoch=expected_document_epoch)
        root = replace(prepared, documents=(prepared.documents[0],))
        plan = page_copy_remap.remap_owned_database_copy(root, database, operation,
            local_origin=local_origin, include_content=include_content)
        copied_document = page_copy_remap.CopyDocumentPlan(doc, doc, prepared.parent_id,
            prepared.documents[0].position,
            _canonical_json({**prepared.documents[0].descriptor(), "blocks": [plan.block()],
                "databases": [plan.database()]}), plan.files_json)
        copied_plan = page_copy_remap.PageCopyPlan(root, operation, (copied_document,))
        contents = page_copy._contents(copied_plan)[doc]
        block, copied_database = contents["blocks"][0], contents["databases"][0]
        current_blocks = prepared.documents[0].descriptor()["blocks"]
        source_positions = [index for index, item in enumerate(current_blocks)
            if item.get("type") in page_copy_remap.DATABASE_BLOCKS
            and not item.get("props", {}).get("linked_database")
            and item.get("props", {}).get("database_id", item.get("id")) == database]
        if len(source_positions) != 1:
            raise page_copy_remap.CopyShapeUnavailable("current database block changed")
        source_index = source_positions[0]
        with authority._reader() as con:
            con.execute("BEGIN")
            workspace, rows, _parent = page_copy_source.revalidate_copy_source(authority, con, prepared)
            page_move._unlocked(con, rows[0], actor)
            generation = _generation(con, doc)
            snapshot = authority._editing_snapshot(con, rows[0])
            source = {"id": doc, "generation": generation,
                "frames": _frames(con, doc, generation),
                "snapshot": snapshot, "insert": block,
                **page_move.parent_rebase.capture_witness(con, doc, generation, snapshot),
                "restore_anchor": {"previous_block_id": current_blocks[source_index]["id"],
                    "next_block_id": current_blocks[source_index + 1]["id"]
                        if source_index + 1 < len(current_blocks) else None,
                    "index": source_index + 1}}
        sentinel = _stable_id("db_copy_sentinel", actor, ws, operation)
        delta = page_move._prepare_deltas({doc: source}, sentinel, operation)[doc]
        page_move.parent_rebase.validate_deltas({doc: source}, {doc: delta}, sentinel)
        if (not delta["inserted"] or not delta["changed"] or delta["removed"]
                or delta["placed"] != block["id"]):
            raise AuthorityUnavailable("database block placement was not prepared independently")
        page_copy._check_capacity(authority, copied_plan)
    except (page_copy_source.CopySourceChanged, AuthorityUnavailable, NotFoundOrDenied,
            EpochConflict, DocumentSequenceConflict):
        with authority._reader() as con:
            con.execute("BEGIN")
            previous = _replay(authority, con, actor, ws, doc, database, operation, digest)
            if previous is not None:
                return previous
        raise

    with authority._transaction() as con:
        _owner(authority, con, actor, ws, doc)
        previous = _replay(authority, con, actor, ws, doc, database, operation, digest)
        if previous is not None:
            return previous
        workspace, rows, _parent = page_copy_source.revalidate_copy_source(authority, con, prepared)
        page_move._unlocked(con, rows[0], actor)
        now = int(time.time())
        page_copy._insert_database(con, doc, copied_database, now, actor=actor)
        page_copy._insert_files(con, copied_plan, copied_document, now)
        link_operation = _stable_id("database_copy_link", actor, ws, operation)
        page_move._structural_link(authority, con, actor, workspace, rows[0], "block.insert", block["id"],
            {**block, "after_block_id": delta["after_block_id"]}, link_operation, now,
            operation_action=ACTION, receipt_action="collaboration.database_copy_block",
            identity_prefix="dbcmd")
        page_move._append_link_delta(authority, con, actor, workspace, rows[0],
            delta["payload"], source["generation"], link_operation, now,
            operation_action=ACTION, receipt_action="collaboration.database_copy_delta",
            client_id="database-copy-service", binding_contract=CONTRACT,
            identity_prefix="dbupd")
        con.execute("UPDATE workspaces SET row_version=row_version+1,updated_at=? WHERE id=?", (now, ws))
        # The final command receipt owns its own document event sequence.
        con.execute("UPDATE workspace_collaboration_documents SET document_sequence=document_sequence+1,"
            "row_version=row_version+1 WHERE id=?", (doc,))
        workspace = authority._workspace(con, ws)
        page = authority._document(con, doc)
        result = {"contract": CONTRACT, "workspace_id": ws, "document_id": doc,
            "source_database_id": database, "database_id": copied_database["id"],
            "block_id": block["id"], "include_content": include_content,
            "document_sequence": page["document_sequence"],
            "workspace_epoch": workspace["security_epoch"], "document_epoch": page["access_epoch"],
            "workspace": WorkspaceStore._workspace_public(workspace),
            "refresh_page_ids": [doc]}
        result = authority._finish(con, actor=actor, action=ACTION, operation_id=operation,
            request_sha256=digest, workspace_id=ws, document_id=doc,
            workspace_epoch=workspace["security_epoch"], document_epoch=page["access_epoch"],
            document_sequence=page["document_sequence"], event_type="database.copied",
            event_payload=result, result=result, fault=fault)
        return {**result, "replayed": False}
