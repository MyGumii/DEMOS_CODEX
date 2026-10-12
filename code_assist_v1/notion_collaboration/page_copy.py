"""Atomic native subtree copy from current, revalidated canonical content.

The public API/capability stays disabled until the remaining linked/synced
reference semantics and UI recovery are implemented. This service never falls
back to legacy storage or turns file provenance into an access grant.
"""
from __future__ import annotations

import base64
import binascii
import json
import shutil
import time

from code_assist_v1 import notion_workspace_file_copy_migration as migration
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
from code_assist_v1.notion_workspace_store import WorkspaceStore
from .canonical_authority import (
    AuthorityUnavailable, EpochConflict, NotFoundOrDenied, OperationConflict, _canonical_json,
    _clean_blocks, _identifier, _operation_id, _positive, _request_sha256, _sha256, _stable_id,
)
from .database_features import _clean_object, _property_config, MAX_SCHEMA_PAYLOAD_BYTES
from .editing_authority import DocumentSequenceConflict
from .file_authority import CapacityUnavailable
from . import page_copy_source, page_copy_remap, page_move, page_copy_rich
from .page_appearance import _metadata
from .title_projection import _frames, _generation
from . import native_team_location as team_location
from . import database_activity, database_item_page_layout, database_row_content, database_row_appearance, database_template_default

CONTRACT = "code_assist.notion.canonical_page_copy.v1"
ACTION = "collaboration.page_copy"
INTENT_ACTION = "collaboration.page_copy.intent"
INTENT_CONTRACT = "code_assist.notion.canonical_page_copy_intent.v1"
CANCEL_CONTRACT = "code_assist.notion.canonical_page_copy_cancelled.v1"


class CopyCancelled(RuntimeError):
    """The exact operation was atomically retired before a copy committed."""


def _copy_request(operation, sequence, workspace_epoch, document_epoch):
    return {"operation_id": operation, "expected_document_sequence": sequence,
        "expected_workspace_epoch": workspace_epoch, "expected_document_epoch": document_epoch}


def _owner(authority, con, actor, workspace_id):
    marker = con.execute("SELECT version,name,checksum,actual_schema_sha256 FROM workspace_schema_migrations ORDER BY version DESC LIMIT 1").fetchone()
    if marker is None or tuple(marker) not in {(item.TARGET_VERSION, item.TARGET_NAME, item.TARGET_CHECKSUM, item.TARGET_ACTUAL_SCHEMA_SHA256) for item in (migration, team_migration, row_body_migration, row_appearance_migration, same_file_migration, activity_migration, trash_migration, row_trash_migration, placement_migration, custom_emoji_migration, item_layout_migration, template_default_migration)}:
        raise AuthorityUnavailable("canonical page copy is not installed")
    return page_copy_source._owner(authority, con, actor, workspace_id)[0]


def _request(actor, workspace_id, source_page_id, operation_id, sequence, workspace_epoch, document_epoch, local_origin):
    actor = _identifier(actor, field="actor_subject_id")
    workspace_id = _identifier(workspace_id, field="workspace_id")
    source_page_id = _identifier(source_page_id, field="source_page_id")
    operation_id = _operation_id(operation_id)
    arguments = {"workspace_id": workspace_id, "source_page_id": source_page_id,
        "expected_document_sequence": _positive(sequence, field="expected_document_sequence"),
        "expected_workspace_epoch": _positive(workspace_epoch, field="expected_workspace_epoch"),
        "expected_document_epoch": _positive(document_epoch, field="expected_document_epoch"),
        "local_origin": local_origin}
    if not isinstance(local_origin, str):
        raise ValueError("configured application origin must be a string")
    return actor, workspace_id, source_page_id, operation_id, _request_sha256(actor, ACTION, arguments)


def _authorize_result(authority, con, actor, workspace_id, previous):
    if previous.get("contract") == CANCEL_CONTRACT and previous.get("state") == "cancelled":
        if previous.get("workspace_id") != workspace_id:
            raise AuthorityUnavailable("stored copy cancellation scope is invalid")
        return
    if previous.get("contract") != CONTRACT or previous.get("workspace", {}).get("id") != workspace_id:
        raise AuthorityUnavailable("stored copy result scope is invalid")
    ids = previous.get("page_ids")
    if not isinstance(ids, list) or not ids or len(ids) > page_copy_source.MAX_SOURCE_DOCUMENTS or previous.get("page", {}).get("id") not in ids:
        raise AuthorityUnavailable("stored copy result identity is invalid")
    # Recovery authorizes destinations, including archived ones, not the source.
    for document_id in ids:
        page_copy_source._owner(authority, con, actor, workspace_id, document_id)


def _replay(authority, con, actor, workspace_id, operation, digest):
    previous = authority._replay(con, actor=actor, action=ACTION, operation_id=operation,
        request_sha256=digest, authorize=lambda: _owner(authority, con, actor, workspace_id))
    if previous is None:
        return None
    _authorize_result(authority, con, actor, workspace_id, previous)
    return {**previous, "replayed": True}


def _copy_replay(*args):
    result = _replay(*args)
    if result and result.get("state") == "cancelled":
        raise CopyCancelled("copy operation was cancelled")
    return result


def _intent_replay(authority, con, actor, workspace_id, source_page_id, operation, digest):
    previous = authority._replay(
        con, actor=actor, action=INTENT_ACTION, operation_id=operation,
        request_sha256=digest, authorize=lambda: _owner(authority, con, actor, workspace_id))
    if previous is not None:
        body = previous.get("copy_request")
        if (previous.get("contract") != INTENT_CONTRACT
                or previous.get("workspace_id") != workspace_id
                or previous.get("source_page_id") != source_page_id
                or not isinstance(body, dict)
                or set(body) != {"operation_id", "expected_document_sequence",
                                   "expected_workspace_epoch", "expected_document_epoch"}
                or not isinstance(previous.get("local_origin"), str)):
            raise AuthorityUnavailable("stored copy intent scope is invalid")
        try:
            _, _, _, stored_operation, stored_digest = _request(
                actor, workspace_id, source_page_id, body["operation_id"],
                body["expected_document_sequence"], body["expected_workspace_epoch"],
                body["expected_document_epoch"], previous["local_origin"])
        except (ValueError, TypeError, KeyError) as exc:
            raise AuthorityUnavailable("stored copy intent request is invalid") from exc
        if stored_operation != operation or stored_digest != digest:
            raise AuthorityUnavailable("stored copy intent request does not match its receipt")
    return previous


def register_page_copy_intent(authority, actor_subject_id, workspace_id, source_page_id, *,
        operation_id, expected_document_sequence, expected_workspace_epoch,
        expected_document_epoch, local_origin="", fault=None):
    """Persist the exact request before a browser dispatches a copy mutation."""
    actor, ws, source, operation, digest = _request(
        actor_subject_id, workspace_id, source_page_id, operation_id,
        expected_document_sequence, expected_workspace_epoch,
        expected_document_epoch, local_origin)
    with authority._transaction() as con:
        workspace = _owner(authority, con, actor, ws)
        terminal = _replay(authority, con, actor, ws, operation, digest)
        if terminal is not None:
            return _result_status(authority, con, terminal)
        previous = _intent_replay(authority, con, actor, ws, source, operation, digest)
        if previous is not None:
            return {"state": "pending", "request": previous["copy_request"],
                    "source_page_id": source, "replayed": True}
        active = con.execute(
            "SELECT intent.operation_id FROM workspace_collaboration_operations intent "
            "WHERE intent.actor_subject_id=? AND intent.action=? AND intent.workspace_id=? "
            "AND json_extract(intent.result_json,'$.source_page_id')=? "
            "AND NOT EXISTS (SELECT 1 FROM workspace_collaboration_operations terminal "
            "WHERE terminal.actor_subject_id=intent.actor_subject_id AND terminal.action=? "
            "AND terminal.operation_id=intent.operation_id) LIMIT 1",
            (actor, INTENT_ACTION, ws, source, ACTION),
        ).fetchone()
        if active is not None:
            raise OperationConflict("another page copy request is still pending")
        _workspace, document = page_copy_source._owner(authority, con, actor, ws, source)
        if document["archived"]:
            raise NotFoundOrDenied("not found or permission denied")
        if (workspace["security_epoch"] != expected_workspace_epoch
                or document["access_epoch"] != expected_document_epoch):
            raise EpochConflict(workspace["security_epoch"], document["access_epoch"])
        if document["document_sequence"] != expected_document_sequence:
            raise DocumentSequenceConflict(document["document_sequence"])
        request = _copy_request(operation, expected_document_sequence,
                                expected_workspace_epoch, expected_document_epoch)
        result = {"contract": INTENT_CONTRACT, "workspace_id": ws,
                  "source_page_id": source, "copy_request": request,
                  "local_origin": local_origin}
        authority._finish(
            con, actor=actor, action=INTENT_ACTION, operation_id=operation,
            request_sha256=digest, workspace_id=ws, document_id=None,
            workspace_epoch=workspace["security_epoch"], document_epoch=None,
            document_sequence=None, event_type="page.copy.requested",
            event_payload={"source_page_id": source, "operation_id": operation},
            result=result, fault=fault)
        return {"state": "pending", "request": request,
                "source_page_id": source, "replayed": False}


def _result_status(authority, con, result):
    if result is None:
        return {"state": "unknown"}
    if result.get("state") == "cancelled":
        return {"state": "cancelled", "result": result}
    current = authority._document(con, result["page"]["id"])
    return {"state": "committed", "result": result, "current": {
        "archived": bool(current["archived"]), "document_sequence": current["document_sequence"],
        "document_epoch": current["access_epoch"]}}


def read_page_copy_result(authority, actor_subject_id, workspace_id, source_page_id, *,
        operation_id, expected_document_sequence, expected_workspace_epoch, expected_document_epoch, local_origin=""):
    actor, ws, _source, operation, digest = _request(actor_subject_id, workspace_id, source_page_id,
        operation_id, expected_document_sequence, expected_workspace_epoch, expected_document_epoch, local_origin)
    with authority._reader() as con:
        con.execute("BEGIN")
        _owner(authority, con, actor, ws)
        _intent_replay(authority, con, actor, ws, _source, operation, digest)
        result = _replay(authority, con, actor, ws, operation, digest)
        return _result_status(authority, con, result)


def cancel_page_copy(authority, actor_subject_id, workspace_id, source_page_id, *,
        operation_id, expected_document_sequence, expected_workspace_epoch, expected_document_epoch, local_origin="", fault=None):
    actor, ws, source, operation, digest = _request(actor_subject_id, workspace_id, source_page_id,
        operation_id, expected_document_sequence, expected_workspace_epoch, expected_document_epoch, local_origin)
    # Shares the original copy's operation key/hash and writer transaction. A
    # prepared copy must replay this terminal result before inserting anything.
    with authority._transaction() as con:
        workspace = _owner(authority, con, actor, ws)
        _intent_replay(authority, con, actor, ws, source, operation, digest)
        previous = _replay(authority, con, actor, ws, operation, digest)
        if previous is not None:
            return _result_status(authority, con, previous)
        result = {"contract": CANCEL_CONTRACT, "state": "cancelled", "workspace_id": ws,
            "source_page_id": source, "copy_request": _copy_request(operation, expected_document_sequence,
                expected_workspace_epoch, expected_document_epoch)}
        result = authority._finish(con, actor=actor, action=ACTION, operation_id=operation, request_sha256=digest,
            workspace_id=ws, document_id=None, workspace_epoch=workspace["security_epoch"], document_epoch=None,
            document_sequence=None, event_type="page.copy.cancelled",
            event_payload={"state": "cancelled", "operation_id": operation}, result=result, fault=fault)
        return {"state": "cancelled", "result": {**result, "replayed": False}}


def list_page_copy_results(authority, actor_subject_id, workspace_id, *, limit=20, cursor="", local_origin=""):
    actor = _identifier(actor_subject_id, field="actor_subject_id")
    ws = _identifier(workspace_id, field="workspace_id")
    if type(limit) is not int or not 1 <= limit <= 100 or not isinstance(cursor, str) or len(cursor) > 2048:
        raise ValueError("invalid copy result pagination")
    after = None
    if cursor:
        try:
            after = json.loads(base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True))
            if not isinstance(after, list) or len(after) != 4 or after[:2] != [actor, ws] or type(after[2]) is not int or after[2] < 0:
                raise ValueError()
            _operation_id(after[3])
        except (ValueError, TypeError, UnicodeError, binascii.Error) as exc:
            raise ValueError("invalid copy result cursor") from exc
    with authority._reader() as con:
        con.execute("BEGIN")
        _owner(authority, con, actor, ws)
        query = ("SELECT operation_id,request_sha256,result_json,created_at FROM workspace_collaboration_operations "
                 "WHERE actor_subject_id=? AND action=? AND workspace_id=?")
        values = [actor, ACTION, ws]
        if after:
            query += " AND (created_at<? OR (created_at=? AND operation_id<?))"
            values += [after[2], after[2], after[3]]
        rows = con.execute(query + " ORDER BY created_at DESC,operation_id DESC LIMIT ?", (*values, limit + 1)).fetchall()
        items = []
        for row in rows[:limit]:
            try:
                result = json.loads(row["result_json"])
                if not isinstance(result, dict):
                    raise ValueError()
                _authorize_result(authority, con, actor, ws, result)
                body = result.get("copy_request")
                if body is not None:
                    if not isinstance(body, dict) or set(body) != {"operation_id", "expected_document_sequence", "expected_workspace_epoch", "expected_document_epoch"}:
                        raise ValueError()
                    _, _, _, operation, digest = _request(actor, ws, result["source_page_id"], body["operation_id"],
                        body["expected_document_sequence"], body["expected_workspace_epoch"], body["expected_document_epoch"], local_origin)
                    if operation != row["operation_id"] or digest != row["request_sha256"]:
                        raise ValueError()
                items.append({**_result_status(authority, con, {**result, "replayed": True}),
                    "operation_id": row["operation_id"], "created_at": row["created_at"], "request": body})
            except NotFoundOrDenied:
                continue  # A later ownership change must not expose a result.
            except (ValueError, TypeError, KeyError) as exc:
                raise AuthorityUnavailable("stored copy recovery record is invalid") from exc
        last = rows[limit - 1] if len(rows) > limit else None
        next_cursor = base64.urlsafe_b64encode(_canonical_json([actor, ws, last["created_at"], last["operation_id"]]).encode()).decode() if last else None
        return {"items": items, "next_cursor": next_cursor}


def list_page_copy_intents(authority, actor_subject_id, workspace_id, *, limit=20, cursor=""):
    """Discover only this owner's requests without a terminal copy or cancellation."""
    actor = _identifier(actor_subject_id, field="actor_subject_id")
    ws = _identifier(workspace_id, field="workspace_id")
    if type(limit) is not int or not 1 <= limit <= 100 or not isinstance(cursor, str) or len(cursor) > 2048:
        raise ValueError("invalid copy intent pagination")
    after = None
    if cursor:
        try:
            after = json.loads(base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True))
            if (not isinstance(after, list) or len(after) != 4 or after[:2] != [actor, ws]
                    or type(after[2]) is not int or after[2] < 0):
                raise ValueError()
            _operation_id(after[3])
        except (ValueError, TypeError, UnicodeError, binascii.Error) as exc:
            raise ValueError("invalid copy intent cursor") from exc
    with authority._reader() as con:
        con.execute("BEGIN")
        _owner(authority, con, actor, ws)
        query = (
            "SELECT intent.operation_id,intent.request_sha256,intent.result_json,intent.created_at "
            "FROM workspace_collaboration_operations intent WHERE intent.actor_subject_id=? "
            "AND intent.action=? AND intent.workspace_id=? AND NOT EXISTS ("
            "SELECT 1 FROM workspace_collaboration_operations terminal WHERE "
            "terminal.actor_subject_id=intent.actor_subject_id AND terminal.action=? "
            "AND terminal.operation_id=intent.operation_id)"
        )
        values = [actor, INTENT_ACTION, ws, ACTION]
        if after:
            query += " AND (intent.created_at<? OR (intent.created_at=? AND intent.operation_id<?))"
            values += [after[2], after[2], after[3]]
        rows = con.execute(query + " ORDER BY intent.created_at DESC,intent.operation_id DESC LIMIT ?",
                           (*values, limit + 1)).fetchall()
        items = []
        for row in rows[:limit]:
            try:
                result = json.loads(row["result_json"])
                if (not isinstance(result, dict) or result.get("contract") != INTENT_CONTRACT
                        or result.get("workspace_id") != ws
                        or not isinstance(result.get("source_page_id"), str)
                        or not isinstance(result.get("local_origin"), str)):
                    raise ValueError()
                body = result["copy_request"]
                if not isinstance(body, dict) or set(body) != {
                        "operation_id", "expected_document_sequence",
                        "expected_workspace_epoch", "expected_document_epoch"}:
                    raise ValueError()
                _, _, _, operation, digest = _request(
                    actor, ws, result["source_page_id"], body["operation_id"],
                    body["expected_document_sequence"], body["expected_workspace_epoch"],
                    body["expected_document_epoch"], result["local_origin"])
                if operation != row["operation_id"] or digest != row["request_sha256"]:
                    raise ValueError()
                items.append({"state": "pending", "source_page_id": result["source_page_id"],
                              "operation_id": operation, "request": body,
                              "created_at": row["created_at"]})
            except (ValueError, TypeError, KeyError) as exc:
                raise AuthorityUnavailable("stored copy intent is invalid") from exc
        last = rows[limit - 1] if len(rows) > limit else None
        next_cursor = base64.urlsafe_b64encode(_canonical_json(
            [actor, ws, last["created_at"], last["operation_id"]]).encode()).decode() if last else None
        return {"items": items, "next_cursor": next_cursor}


def _row_resources(row):
    """Validate nested item content before any copied database is inserted."""
    result = {}
    if "body" in row:
        body = row["body"]
        if not isinstance(body, dict) or set(body) != {"blocks"}:
            raise ValueError("copied item body requires blocks")
        result["body"] = {"blocks": database_row_content.clean_blocks(body["blocks"])}
    if "appearance" in row:
        appearance = row["appearance"]
        if not isinstance(appearance, dict) or set(appearance) != set(database_row_appearance.DEFAULT):
            raise ValueError("copied item appearance requires complete metadata")
        result["appearance"] = database_row_appearance.clean_updates(appearance)
    return result


def _contents(plan):
    """Validate and order descriptors as the existing canonical reader will."""
    contents = {}
    for document in plan.documents:
        descriptor = document.descriptor()
        descriptor["blocks"] = _clean_blocks(descriptor["blocks"])
        descriptor["databases"].sort(key=lambda item: item["id"])
        for database in descriptor["databases"]:
            database["properties"].sort(key=lambda item: (item["position"], item["id"]))
            property_ids = {item["id"] for item in database["properties"]}
            for prop in database["properties"]:
                # Expanded formula aliases must obey the normal schema payload
                # limit, even if the complete source fits the copy budget.
                _property_config(prop["type"], prop["config"])
            if "item_page_layout" in database:
                layout = database["item_page_layout"]
                database_item_page_layout.clean_layout(
                    layout["value"],
                    property_ids={item["id"] for item in database["properties"]
                                  if item["type"] != "title"},
                )
            database["rows"].sort(key=lambda item: (item["position"], item["id"]))
            for row in database["rows"]:
                row.update(_row_resources(row))
                if not set(row["cells"]).issubset(property_ids):
                    raise AuthorityUnavailable("copy source cell is outside its database")
                for value in row["cells"].values():
                    if len(_canonical_json(value).encode()) > MAX_SCHEMA_PAYLOAD_BYTES:
                        raise ValueError("copied cell is too large")
            database["views"].sort(key=lambda item: (item["position"], item["id"]))
            for view in database["views"]:
                _clean_object(view["config"], field="copied view config")
            # The remapper prefixes template IDs with their source ordinal so
            # this canonical order retains the visible source template order.
            database["templates"].sort(key=lambda item: item["id"])
            for template in database["templates"]:
                _clean_object(template["default_values"], field="copied template defaults")
                if len(_canonical_json(template["body"]).encode()) > MAX_SCHEMA_PAYLOAD_BYTES:
                    raise ValueError("copied template body is too large")
            selected = database.get("default_template", {}).get("template_id")
            if selected and selected not in {item["id"] for item in database["templates"]}:
                raise AuthorityUnavailable("copied default template is outside its database")
        descriptor["files"] = [{"id": item["id"], "name": item["original_name"],
            "mime_type": item["mime_type"], "size": item["size_bytes"], "sha256": item["content_sha256"],
            "state": "active", "version": 1} for item in sorted(json.loads(document.files_json), key=lambda item: item["id"])]
        contents[document.document_id] = descriptor
    return contents


def _check_capacity(authority, plan, seeds=None):
    # Copies reuse verified immutable blobs and create no upload reservation.
    # Reserve headroom for two metadata copies and WAL/index overhead before
    # entering the transaction. SQLite still provides rollback on a later IO fault.
    metadata_bytes = sum(len(item.descriptor_json.encode()) + len(item.files_json.encode()) + len(item.rich_text_json.encode()) for item in plan.documents)
    metadata_bytes += sum(map(len, (seeds or {}).values()))
    usage = shutil.disk_usage(authority.catalog_path.parent)
    remaining = usage.free - metadata_bytes * 4 - 4 * 1024 * 1024
    if remaining < authority.minimum_free_bytes or (usage.total and remaining * 100 / usage.total < authority.minimum_free_percent):
        raise CapacityUnavailable("operational copy disk reserve unavailable")


def _insert_database(con, document_id, database, now, *, actor=None):
    if not con.in_transaction:
        raise ValueError("copied database insertion requires a transaction")
    # Preflight every row, including schema support, before inserting anything.
    # New rows start at version one; ordinary set_body/set_appearance commands
    # would advance that version and misrepresent this initial snapshot.
    resources = [_row_resources(row) for row in database["rows"]]
    if any("body" in value for value in resources) and not database_row_content.installed(con):
        raise AuthorityUnavailable("copied database item content is not installed")
    if any("appearance" in value for value in resources) and not database_row_appearance.installed(con):
        raise AuthorityUnavailable("copied database item appearance is not installed")
    database_id = database["id"]
    con.execute("INSERT INTO workspace_collaboration_databases(id,document_id,title,row_version,created_at,updated_at) VALUES(?,?,?,1,?,?)",
        (database_id, document_id, database["title"], now, now))
    for prop in database["properties"]:
        con.execute("INSERT INTO workspace_collaboration_database_properties(id,database_id,name,property_type,position,config_json,row_version,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?)",
            (prop["id"], database_id, prop["name"], prop["type"], prop["position"], _canonical_json(prop["config"]), now, now))
    for row, resource in zip(database["rows"], resources):
        con.execute("INSERT INTO workspace_collaboration_database_rows(id,database_id,position,row_version,created_at,updated_at) VALUES(?,?,?,1,?,?)",
            (row["id"], database_id, row["position"], now, now))
        con.executemany("INSERT INTO workspace_collaboration_database_cells(row_id,property_id,value_json,row_version,updated_at) VALUES(?,?,?,1,?)",
            [(row["id"], prop, _canonical_json(value), now) for prop, value in row["cells"].items()])
        if "body" in resource:
            con.execute("INSERT INTO workspace_collaboration_database_row_bodies(row_id,blocks_json) VALUES(?,?)",
                (row["id"], _canonical_json(resource["body"]["blocks"])))
        if "appearance" in resource:
            con.execute("INSERT INTO workspace_collaboration_database_row_appearance(row_id,appearance_json) VALUES(?,?)",
                (row["id"], _canonical_json(resource["appearance"])))
    for view in database["views"]:
        con.execute("INSERT INTO workspace_collaboration_database_views(id,database_id,name,view_type,position,config_json,state,row_version,created_at,updated_at,archived_at) VALUES(?,?,?,?,?,?,'active',1,?,?,NULL)",
            (view["id"], database_id, view["name"], view["type"], view["position"], _canonical_json(view["config"]), now, now))
    for template in database["templates"]:
        con.execute("INSERT INTO workspace_collaboration_database_templates(id,database_id,name,body_json,default_values_json,state,row_version,created_at,updated_at,archived_at) VALUES(?,?,?,?,?,'active',1,?,?,NULL)",
            (template["id"], database_id, template["name"], _canonical_json(template["body"]), _canonical_json(template["default_values"]), now, now))
    selected = database.get("default_template", {}).get("template_id")
    if selected:
        if not database_template_default.installed(con):
            raise AuthorityUnavailable("copied database template default is not installed")
        if not actor:
            raise ValueError("copied database template default requires an actor")
        con.execute(
            "INSERT INTO workspace_collaboration_database_template_defaults("
            "database_id,template_id,row_version,updated_by_subject_id,created_at,updated_at) "
            "VALUES(?,?,1,?,?,?)", (database_id, selected, actor, now, now),
        )
    if "item_page_layout" in database:
        if not database_item_page_layout.installed(con):
            raise AuthorityUnavailable("copied database item-page layout is not installed")
        if not actor:
            raise ValueError("copied database item-page layout requires an actor")
        con.execute(
            "INSERT INTO workspace_collaboration_database_item_page_layouts("
            "database_id,layout_json,row_version,updated_by_subject_id,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (database_id, _canonical_json(database["item_page_layout"]["value"]),
             1, actor, now, now),
        )


def _insert_files(con, plan, document, now):
    for item in json.loads(document.files_json):
        original = con.execute("SELECT * FROM workspace_collaboration_files WHERE id=? AND workspace_id=? AND document_id=? AND state='active'",
            (item["source_file_id"], plan.source.workspace_id, document.source_document_id)).fetchone()
        if original is None or item["source_document_id"] != document.source_document_id or any(
                original[key] != item[key] for key in ("original_name", "mime_type", "size_bytes", "content_sha256", "blob_relative_path")):
            raise page_copy_source.CopySourceChanged("copy attachment changed; prepare it again")
        con.execute("INSERT INTO workspace_collaboration_files(id,workspace_id,document_id,upload_session_id,uploader_subject_id,original_name,mime_type,size_bytes,content_sha256,blob_relative_path,state,row_version,created_at,updated_at,origin_kind,source_file_id,source_document_id,copied_by_subject_id,copy_operation_id) VALUES(?,?,?,NULL,?,?,?,?,?,?,'active',1,?,?,'copy',?,?,?,?)",
            (item["id"], plan.source.workspace_id, document.document_id, original["uploader_subject_id"],
             item["original_name"], item["mime_type"], item["size_bytes"], item["content_sha256"], item["blob_relative_path"],
             now, now, item["source_file_id"], document.source_document_id, plan.source.actor, plan.operation_id))


def copy_page(authority, actor_subject_id, workspace_id, source_page_id, *, operation_id,
        expected_document_sequence, expected_workspace_epoch, expected_document_epoch,
        local_origin="", fault=None, require_registered_intent=False):
    actor, ws, source_id, operation, digest = _request(actor_subject_id, workspace_id, source_page_id,
        operation_id, expected_document_sequence, expected_workspace_epoch, expected_document_epoch, local_origin)
    with authority._reader() as con:
        con.execute("BEGIN")
        _owner(authority, con, actor, ws)
        intent = _intent_replay(authority, con, actor, ws, source_id, operation, digest)
        previous = _copy_replay(authority, con, actor, ws, operation, digest)
        if previous is not None:
            return previous
        if require_registered_intent and intent is None:
            raise OperationConflict("page copy request must be registered before dispatch")
    sources = {}
    link_operation = _stable_id("page_copy_link", actor, ws, operation)
    try:
        prepared = page_copy_source.prepare_page_copy(authority, actor, ws, source_id,
            expected_document_sequence=expected_document_sequence, expected_workspace_epoch=expected_workspace_epoch,
            expected_document_epoch=expected_document_epoch)
        plan = page_copy_remap.remap_page_copy(prepared, operation, local_origin=local_origin)
        root_id = plan.documents[0].document_id
        contents = _contents(plan)
        seeds = page_copy_rich.prepare(plan, contents)
        if prepared.parent_id:
            with authority._reader() as con:
                con.execute("BEGIN")
                _owner(authority, con, actor, ws)
                _workspace, _rows, parent = page_copy_source.revalidate_copy_source(authority, con, prepared)
                generation = _generation(con, parent["id"])
                snapshot = authority._editing_snapshot(con, parent)
                sources[parent["id"]] = {"id": parent["id"], "generation": generation,
                    "frames": _frames(con, parent["id"], generation), "snapshot": snapshot,
                    **page_move.parent_rebase.capture_witness(con, parent["id"], generation, snapshot),
                    "insert": {"id": _stable_id("child_link", actor, ws, operation, root_id), "type": "child_page",
                        "content": contents[root_id]["title"], "props": {"page_id": root_id}}}
        deltas = page_move._prepare_deltas(sources, root_id, link_operation)
        page_move.parent_rebase.validate_deltas(sources, deltas, root_id)
        _check_capacity(authority, plan, seeds)
    except (page_copy_source.CopySourceChanged, AuthorityUnavailable, NotFoundOrDenied, EpochConflict, DocumentSequenceConflict):
        # A concurrent identical operation may have won during preparation.
        with authority._reader() as con:
            con.execute("BEGIN")
            previous = _copy_replay(authority, con, actor, ws, operation, digest)
            if previous is not None:
                return previous
        raise
    with authority._transaction() as con:
        _owner(authority, con, actor, ws)
        intent = _intent_replay(authority, con, actor, ws, source_id, operation, digest)
        previous = _copy_replay(authority, con, actor, ws, operation, digest)
        if previous is not None:
            return previous
        if require_registered_intent and intent is None:
            raise OperationConflict("page copy request must be registered before dispatch")
        workspace, _source_rows, parent = page_copy_source.revalidate_copy_source(authority, con, prepared)
        now = int(time.time())
        root_position = con.execute("SELECT COALESCE(MAX(position),-1)+1 FROM workspace_collaboration_documents WHERE workspace_id=? AND parent_document_id IS ? AND archived=0",
            (ws, prepared.parent_id)).fetchone()[0]
        for document in plan.documents:
            descriptor = contents[document.document_id]
            checksum = _sha256(_canonical_json(descriptor))
            con.execute("INSERT INTO workspace_collaboration_documents(id,workspace_id,legacy_owner_subject_id,parent_document_id,title,icon,cover,settings_json,position,archived,archived_at,revision,document_sequence,access_epoch,row_version,content_sha256,imported_snapshot_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,0,NULL,1,1,1,1,?,NULL,?,?)",
                (document.document_id, ws, actor, document.parent_id, descriptor["title"], descriptor["icon"], descriptor["cover"],
                 _canonical_json(descriptor["settings"]), root_position if document.document_id == root_id else document.position, checksum, now, now))
            authority._insert_blocks(con, document.document_id, descriptor["blocks"], now=now)
            for database in descriptor["databases"]:
                _insert_database(con, document.document_id, database, now,
                                 actor=plan.source.actor)
            if descriptor["databases"]:
                database_activity.touch(
                    con, database_ids=(database["id"] for database in descriptor["databases"]),
                    document_id=document.document_id, actor_subject_id=actor,
                    edited_at=now, document_sequence=1,
                )
            _insert_files(con, plan, document, now)
            actual = authority._editing_snapshot(con, authority._document(con, document.document_id))
            if _canonical_json(actual) != _canonical_json(descriptor):
                raise AuthorityUnavailable("copied canonical content does not match its prepared descriptor")
            con.execute("INSERT INTO workspace_collaboration_revisions(document_id,revision,document_sequence,content_sha256,snapshot_json,actor_subject_id,operation_id,created_at) VALUES(?,1,1,?,?,?,?,?)",
                (document.document_id, checksum, _canonical_json(actual), actor, operation, now))
            authority._record_document_creation_successor_state(con, workspace_id=ws, document_id=document.document_id,
                content_sha256=checksum, actor_subject_id=actor, operation_id=operation, created_at=now,
                initial_update_sequence=1 if document.document_id in seeds else 0)
            if document.document_id in seeds:
                page_copy_rich.insert(con, plan, document, seeds[document.document_id], workspace, now)
            con.execute("INSERT INTO workspace_collaboration_native_pages(document_id,workspace_id,creation_kind,source_document_id,created_by_subject_id,operation_id,created_at) VALUES(?,?,'copy',?,?,?,?)",
                (document.document_id, ws, document.source_document_id, actor, operation, now))
            if document.document_id != root_id:
                event_operation = _stable_id("page_copy_document", actor, ws, operation, document.document_id)
                event = {"document_id": document.document_id, "source_document_id": document.source_document_id,
                    "copy_operation_id": operation, "document_sequence": 1}
                authority._finish(con, actor=actor, action="collaboration.page_copy_document", operation_id=event_operation,
                    request_sha256=_request_sha256(actor, ACTION, event), workspace_id=ws, document_id=document.document_id,
                    workspace_epoch=workspace["security_epoch"], document_epoch=1, document_sequence=1,
                    event_type="page.created", event_payload=event, result=event, fault=None)
        source_location = team_location.root_record(con, ws, source_id)
        if source_location is not None and prepared.parent_id is None:
            team_location.require_creator(authority, con, actor, ws, source_location["teamspace_id"])
            team_location.replace_root(con, ws, root_id, source_location["teamspace_id"], actor, now)
        if parent is not None:
            source, delta = sources[parent["id"]], deltas[parent["id"]]
            if not delta["inserted"] or not delta["changed"] or delta["removed"]:
                raise AuthorityUnavailable("copied page parent link was not prepared independently")
            page_move._structural_link(authority, con, actor, workspace, parent, "block.insert", delta["placed"],
                {**source["insert"], "after_block_id": delta["after_block_id"]}, link_operation, now)
            page_move._append_link_delta(authority, con, actor, workspace, parent, delta["payload"], source["generation"], link_operation, now)
        con.execute("UPDATE workspaces SET row_version=row_version+1,updated_at=? WHERE id=?", (now, ws))
        workspace = authority._workspace(con, ws)
        result = {"contract": CONTRACT, "page": _metadata(authority._document(con, root_id), workspace, "full_access", actor),
            "workspace": WorkspaceStore._workspace_public(workspace), "source_page_id": source_id,
            "copy_request": _copy_request(operation, expected_document_sequence, expected_workspace_epoch, expected_document_epoch),
            "page_ids": [item.document_id for item in plan.documents],
            "parent_link_saved": bool(prepared.parent_id), "refresh_page_ids": [*(item.document_id for item in plan.documents), *sources]}
        result = authority._finish(con, actor=actor, action=ACTION, operation_id=operation, request_sha256=digest,
            workspace_id=ws, document_id=root_id, workspace_epoch=workspace["security_epoch"], document_epoch=1, document_sequence=1,
            event_type="page.copied", event_payload=result, result=result, fault=fault)
        return {**result, "replayed": False}
