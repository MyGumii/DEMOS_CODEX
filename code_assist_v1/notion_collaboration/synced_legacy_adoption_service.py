"""Reviewed legacy adoption requests, durable intent and terminal recovery.

Only exact selections are recorded. No group original is inferred, no operating
data is adopted automatically, and route activation is independently gated.
"""
from __future__ import annotations

import base64
import binascii
import json
import re

from . import page_move, synced_blocks as synced
from . import synced_legacy_adoption as adoption
from . import synced_legacy_adoption_commit as commit
from .canonical_authority import AuthorityUnavailable, NotFoundOrDenied, OperationConflict, _canonical_json, _operation_id, _request_sha256, _sha256
from .page_copy_source import _owner

REVIEW_CONTRACT = "code_assist.notion.synced_legacy_adoption_review.v1"
INTENT_CONTRACT = "code_assist.notion.synced_legacy_adoption_intent.v1"
CANCEL_CONTRACT = "code_assist.notion.synced_legacy_adoption_cancel.v1"
HISTORY_CONTRACT = "code_assist.notion.synced_legacy_adoption_history.v1"
INTENT_ACTION = "collaboration.synced_legacy_adoption_intent"
REQUEST_FIELDS = {"origin", "references", "preparation_sha256", "operation_id"}
CANDIDATES_CONTRACT = "code_assist.notion.synced_legacy_adoption_candidates.v1"
CANDIDATE_PAGE_SIZE = 100


def list_legacy_candidates(authority, actor_subject_id, workspace_id, *, anchor, document_id, cursor=""):
    """Inspect an explicitly chosen page. Never search for or infer an original."""
    selected = adoption.prepare_legacy_adoption(authority, actor_subject_id, workspace_id,
        origin=anchor, references=[])
    actor, ws = selected.actor, selected.workspace_id
    document = synced._exact_id(document_id, "document_id")
    source = synced._current(authority, actor, document, adoption.MAX_SOURCE_BYTES, ws, strict_structure=True)
    original_candidates = {}
    with authority._reader() as con:
        con.execute("BEGIN")
        adoption.revalidate_legacy_adoption(authority, con, selected)
        adoption._authorize(authority, con, actor, ws, source)
        for block in json.loads(source.descriptor_json)["blocks"]:
            props = block.get("props", {})
            if block.get("type") != "synced_block" or "synced_from" not in props or props["synced_from"] is not None:
                continue
            try:
                _block, _signature = adoption._original_block(source, block["id"])
                adoption.original_provenance(authority, con, actor, ws, (document, block["id"]),
                    legacy_id=selected.value()["legacy_sync_id"])
                original_candidates[block["id"]] = _signature
            except synced.SyncedShapeUnavailable:
                continue
    original, group, signature = adoption._legacy_block(selected.documents[0].source, selected.origin[1])
    fingerprint = _sha256(_canonical_json([CANDIDATES_CONTRACT, actor, ws, anchor,
        selected.payload_sha256, source.descriptor_json, adoption._frames_digest(source.frames)]))
    offset = 0
    if not isinstance(cursor, str) or len(cursor) > 2048:
        raise ValueError("invalid legacy candidate cursor")
    if cursor:
        try:
            value = json.loads(base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True))
            if not isinstance(value, list) or len(value) != 2 or type(value[1]) is not int or value[1] < 0:
                raise ValueError()
            if value[0] != fingerprint:
                raise synced.SyncedSourceChanged("legacy candidates changed; reload the selected page")
            offset = value[1]
        except (ValueError, UnicodeError, binascii.Error) as exc:
            raise ValueError("invalid legacy candidate cursor") from exc
    candidates = []
    for block in json.loads(source.descriptor_json)["blocks"]:
        props = block.get("props", {})
        original_only = block["id"] in original_candidates
        if not original_only and (block.get("type") != "synced_block" or props.get("sync_id") != group or "synced_from" in props):
            continue
        reason = ""
        try:
            if original_only:
                candidate_signature = original_candidates[block["id"]]
            else:
                _block, _group, candidate_signature = adoption._legacy_block(source, block["id"])
            if signature != candidate_signature:
                reason = "원본 후보와 내용 또는 서식이 다릅니다. 내용을 확인해 주세요."
        except synced.SyncedShapeUnavailable:
            reason = "하위 내용을 포함한 동기화 블록의 전환은 준비 중입니다."
        candidates.append({"endpoint": {"document_id": document, "block_id": block["id"]},
            "content_preview": block.get("content", "")[:1000], "unavailable_reason": reason,
            "original_only": original_only})
    if offset > len(candidates):
        raise ValueError("invalid legacy candidate offset")
    next_offset = offset + CANDIDATE_PAGE_SIZE
    next_cursor = base64.urlsafe_b64encode(_canonical_json([fingerprint, next_offset]).encode()).decode() if next_offset < len(candidates) else None
    return {"contract": CANDIDATES_CONTRACT, "workspace_id": ws, "document_id": document,
            "items": candidates[offset:next_offset], "next_cursor": next_cursor}


def _request(actor, workspace_id, origin, references, preparation_sha256, operation_id):
    actor = synced._exact_id(actor, "actor_subject_id")
    workspace_id = synced._exact_id(workspace_id, "workspace_id")
    original, targets = adoption._selection(origin, references)
    operation = _operation_id(operation_id)
    if not isinstance(preparation_sha256, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", preparation_sha256):
        raise ValueError("the exact reviewed preparation hash is required")
    body = {"origin": {"document_id": original[0], "block_id": original[1]},
        "references": [{"document_id": doc, "block_id": block} for doc, block in targets],
        "preparation_sha256": preparation_sha256, "operation_id": operation}
    digest = _request_sha256(actor, commit.ACTION, {"workspace_id": workspace_id,
        "origin": original, "references": targets, "preparation_sha256": preparation_sha256})
    return actor, workspace_id, body, digest


def _authorize_selection(authority, con, actor, ws, body):
    workspace, _page = _owner(authority, con, actor, ws)
    pages = []
    for document in sorted({item["document_id"] for item in [body["origin"], *body["references"]]}):
        _owner(authority, con, actor, ws, document)
        pages.append(page_move._authorize(authority, con, actor, ws, document, "view"))
    return workspace, pages


def _terminal(authority, con, actor, ws, body, digest):
    value = authority._replay(con, actor=actor, action=commit.ACTION,
        operation_id=body["operation_id"], request_sha256=digest,
        authorize=lambda: _authorize_selection(authority, con, actor, ws, body))
    if value is None:
        return None
    if (value.get("contract") not in {commit.CONTRACT, CANCEL_CONTRACT}
            or value.get("workspace_id") != ws or value.get("operation_id") != body["operation_id"]
            or value.get("origin") != body["origin"] or value.get("references") != body["references"]
            or value.get("preparation_sha256") != body["preparation_sha256"]):
        raise AuthorityUnavailable("stored legacy adoption result scope is invalid")
    if value.get("contract") == CANCEL_CONTRACT and value.get("state") != "cancelled":
        raise AuthorityUnavailable("stored legacy adoption cancellation is invalid")
    return {**value, "replayed": True}


def _intent(authority, con, actor, ws, body, digest):
    value = authority._replay(con, actor=actor, action=INTENT_ACTION,
        operation_id=body["operation_id"], request_sha256=digest,
        authorize=lambda: _authorize_selection(authority, con, actor, ws, body))
    if value is not None and (value.get("contract") != INTENT_CONTRACT
            or value.get("workspace_id") != ws or value.get("request") != body
            or not isinstance(value.get("legacy_sync_id"), str)):
        raise AuthorityUnavailable("stored legacy adoption intent is invalid")
    return value


def _status(authority, con, actor, ws, body, value):
    if value is None:
        return {"state": "unknown"}
    if value.get("state") == "cancelled":
        return {"state": "cancelled", "result": value}
    _workspace, pages = _authorize_selection(authority, con, actor, ws, body)
    if value.get("contract") != commit.CONTRACT:
        raise AuthorityUnavailable("stored adoption result contract is invalid")
    return {"state": "committed", "result": value,
        "current": [{"document_id": page["id"], "archived": bool(page["archived"]),
                     "document_sequence": page["document_sequence"], "document_epoch": page["access_epoch"]} for page in pages]}


def review_legacy_adoption(authority, actor_subject_id, workspace_id, *, origin, references):
    selected = adoption.prepare_legacy_adoption(authority, actor_subject_id, workspace_id,
        origin=origin, references=references)
    source = next(item.source for item in selected.documents if item.source.document_id == selected.origin[0])
    block, _signature = adoption._original_block(source, selected.origin[1]) if selected.provenance_json is not None else adoption._flat_block(source, selected.origin[1], explicit_original=False)
    return {"contract": REVIEW_CONTRACT, "workspace_id": selected.workspace_id,
        "origin": selected.value()["origin"], "references": selected.value()["references"],
        "preparation_sha256": selected.payload_sha256,
        "original": {"title": json.loads(source.descriptor_json)["title"],
                     "content_preview": block["content"][:4000], "preview_truncated": len(block["content"]) > 4000,
                     "already_designated": selected.provenance_json is not None},
        "document_count": len(selected.documents)}


def _prepare_reviewed(authority, actor, ws, body):
    selected = adoption.prepare_legacy_adoption(authority, actor, ws,
        origin=body["origin"], references=body["references"])
    if selected.payload_sha256 != body["preparation_sha256"]:
        raise synced.SyncedSourceChanged("legacy selection changed after review")
    return selected


def register_legacy_adoption_intent(authority, actor_subject_id, workspace_id, *,
        origin, references, preparation_sha256, operation_id, fault=None):
    actor, ws, body, digest = _request(actor_subject_id, workspace_id, origin,
        references, preparation_sha256, operation_id)
    with authority._reader() as con:
        con.execute("BEGIN")
        _authorize_selection(authority, con, actor, ws, body)
        terminal = _terminal(authority, con, actor, ws, body, digest)
        if terminal is not None:
            return _status(authority, con, actor, ws, body, terminal)
        previous = _intent(authority, con, actor, ws, body, digest)
        if previous is not None:
            return {"state": "pending", "request": body, "replayed": True}
    try:
        selected = _prepare_reviewed(authority, actor, ws, body)
    except (synced.SyncedSourceChanged, synced.SyncedShapeUnavailable):
        with authority._reader() as con:
            con.execute("BEGIN")
            terminal = _terminal(authority, con, actor, ws, body, digest)
            if terminal is not None:
                return _status(authority, con, actor, ws, body, terminal)
        raise
    legacy_id = selected.value()["legacy_sync_id"]
    with authority._transaction() as con:
        terminal = _terminal(authority, con, actor, ws, body, digest)
        if terminal is not None:
            return _status(authority, con, actor, ws, body, terminal)
        previous = _intent(authority, con, actor, ws, body, digest)
        if previous is not None:
            return {"state": "pending", "request": body, "replayed": True}
        adoption.revalidate_legacy_adoption(authority, con, selected)
        workspace = authority._workspace(con, ws)
        active = con.execute("SELECT intent.operation_id FROM workspace_collaboration_operations intent "
            "WHERE intent.actor_subject_id=? AND intent.workspace_id=? AND intent.action=? "
            "AND json_extract(intent.result_json,'$.legacy_sync_id')=? "
            "AND NOT EXISTS (SELECT 1 FROM workspace_collaboration_operations terminal "
            "WHERE terminal.actor_subject_id=intent.actor_subject_id AND terminal.action=? "
            "AND terminal.operation_id=intent.operation_id) LIMIT 1", (actor, ws, INTENT_ACTION, legacy_id, commit.ACTION)).fetchone()
        if active is not None:
            raise OperationConflict("another request for this legacy group is still pending")
        value = {"contract": INTENT_CONTRACT, "workspace_id": ws, "request": body, "legacy_sync_id": legacy_id}
        authority._finish(con, actor=actor, action=INTENT_ACTION, operation_id=body["operation_id"],
            request_sha256=digest, workspace_id=ws, document_id=None,
            workspace_epoch=workspace["security_epoch"], document_epoch=None, document_sequence=None,
            event_type="synced.legacy_adoption_requested", event_payload={"operation_id": body["operation_id"]},
            result=value, fault=fault)
        return {"state": "pending", "request": body, "replayed": False}


def require_registered_intent(authority, con, selected, operation_id):
    """Called again inside the adoption writer, never as an authorization lease."""
    actor, ws, body, digest = _request(selected.actor, selected.workspace_id,
        selected.value()["origin"], selected.value()["references"], selected.payload_sha256, operation_id)
    value = _intent(authority, con, actor, ws, body, digest)
    if value is None:
        raise OperationConflict("register the exact legacy adoption request before dispatch")
    if value["legacy_sync_id"] != selected.value()["legacy_sync_id"]:
        raise AuthorityUnavailable("registered legacy group differs from its preparation")


def adopt_legacy_selection(authority, actor_subject_id, workspace_id, *,
        origin, references, preparation_sha256, operation_id, fault=None):
    actor, ws, body, digest = _request(actor_subject_id, workspace_id, origin,
        references, preparation_sha256, operation_id)
    with authority._reader() as con:
        con.execute("BEGIN")
        _authorize_selection(authority, con, actor, ws, body)
        terminal = _terminal(authority, con, actor, ws, body, digest)
        if terminal is not None:
            return _status(authority, con, actor, ws, body, terminal)
        if _intent(authority, con, actor, ws, body, digest) is None:
            raise OperationConflict("register the exact legacy adoption request before dispatch")
    try:
        selected = _prepare_reviewed(authority, actor, ws, body)
        delta = commit.prepare_adoption_delta(authority, selected)
    except (synced.SyncedSourceChanged, synced.SyncedShapeUnavailable) as exc:
        # Another process may have completed this same operation while replay
        # ran outside the writer. Its exact receipt takes precedence over the
        # now-converted legacy shape; current access is still revalidated.
        with authority._reader() as con:
            con.execute("BEGIN")
            terminal = _terminal(authority, con, actor, ws, body, digest)
            if terminal is not None:
                return _status(authority, con, actor, ws, body, terminal)
        if isinstance(exc, synced.SyncedShapeUnavailable):
            raise synced.SyncedSourceChanged("reviewed legacy selection is no longer available") from exc
        raise
    if commit._request(delta) != digest:
        raise ValueError("legacy adoption request changed during preparation")
    result = commit.commit_prepared_adoption(authority, delta, operation_id=operation_id,
        fault=fault, require_intent=True)
    with authority._reader() as con:
        con.execute("BEGIN")
        # A concurrent cancellation may have won before this writer.
        _authorize_selection(authority, con, actor, ws, body)
        return _status(authority, con, actor, ws, body, result)


def read_legacy_adoption_result(authority, actor_subject_id, workspace_id, *,
        origin, references, preparation_sha256, operation_id):
    actor, ws, body, digest = _request(actor_subject_id, workspace_id, origin,
        references, preparation_sha256, operation_id)
    with authority._reader() as con:
        con.execute("BEGIN")
        _authorize_selection(authority, con, actor, ws, body)
        _intent(authority, con, actor, ws, body, digest)
        return _status(authority, con, actor, ws, body, _terminal(authority, con, actor, ws, body, digest))


def cancel_legacy_adoption(authority, actor_subject_id, workspace_id, *,
        origin, references, preparation_sha256, operation_id, fault=None):
    actor, ws, body, digest = _request(actor_subject_id, workspace_id, origin,
        references, preparation_sha256, operation_id)
    with authority._transaction() as con:
        workspace, _pages = _authorize_selection(authority, con, actor, ws, body)
        terminal = _terminal(authority, con, actor, ws, body, digest)
        if terminal is not None:
            return _status(authority, con, actor, ws, body, terminal)
        _intent(authority, con, actor, ws, body, digest)
        result = {"contract": CANCEL_CONTRACT, "state": "cancelled", "workspace_id": ws, **body}
        saved = authority._finish(con, actor=actor, action=commit.ACTION, operation_id=operation_id,
            request_sha256=digest, workspace_id=ws, document_id=None, workspace_epoch=workspace["security_epoch"],
            document_epoch=None, document_sequence=None, event_type="synced.legacy_adoption_cancelled",
            event_payload={"operation_id": operation_id}, result=result, fault=fault)
        return {"state": "cancelled", "result": {**saved, "replayed": False}}


def list_legacy_adoption_requests(authority, actor_subject_id, workspace_id, *, limit=20, cursor=""):
    actor = synced._exact_id(actor_subject_id, "actor_subject_id")
    ws = synced._exact_id(workspace_id, "workspace_id")
    if type(limit) is not int or not 1 <= limit <= 100 or not isinstance(cursor, str) or len(cursor) > 2048:
        raise ValueError("invalid legacy adoption pagination")
    after = None
    if cursor:
        try:
            after = json.loads(base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True))
            if (not isinstance(after, list) or len(after) != 5 or after[:3] != [HISTORY_CONTRACT, actor, ws]
                    or type(after[3]) is not int or after[3] < 0):
                raise ValueError()
            _operation_id(after[4])
        except (ValueError, UnicodeError, binascii.Error) as exc:
            raise ValueError("invalid legacy adoption cursor") from exc
    with authority._reader() as con:
        con.execute("BEGIN")
        _owner(authority, con, actor, ws)
        query = ("SELECT record.operation_id,record.request_sha256,record.result_json,record.created_at,record.action,"
            "terminal.result_json AS terminal_json FROM workspace_collaboration_operations record "
            "LEFT JOIN workspace_collaboration_operations terminal ON terminal.actor_subject_id=record.actor_subject_id "
            "AND terminal.action=? AND terminal.operation_id=record.operation_id "
            "WHERE record.actor_subject_id=? AND record.workspace_id=? AND (record.action=? OR (record.action=? "
            "AND NOT EXISTS (SELECT 1 FROM workspace_collaboration_operations intent "
            "WHERE intent.actor_subject_id=record.actor_subject_id AND intent.action=? AND intent.operation_id=record.operation_id)))")
        values = [commit.ACTION, actor, ws, INTENT_ACTION, commit.ACTION, INTENT_ACTION]
        if after:
            query += " AND (record.created_at<? OR (record.created_at=? AND record.operation_id<?))"
            values.extend([after[3], after[3], after[4]])
        rows = con.execute(query + " ORDER BY record.created_at DESC,record.operation_id DESC LIMIT ?", (*values, limit + 1)).fetchall()
        items = []
        for row in rows[:limit]:
            try:
                recorded = json.loads(row["result_json"])
                body = recorded["request"] if row["action"] == INTENT_ACTION else {key: recorded[key] for key in REQUEST_FIELDS}
                _actor, _ws, body, digest = _request(actor, ws, **body)
                if body["operation_id"] != row["operation_id"] or digest != row["request_sha256"]:
                    raise ValueError("stored legacy adoption request differs")
                _authorize_selection(authority, con, actor, ws, body)
                terminal = _terminal(authority, con, actor, ws, body, digest) if row["terminal_json"] is not None else None
                status = _status(authority, con, actor, ws, body, terminal) if terminal is not None else {"state": "pending"}
                items.append({**status, "request": body, "operation_id": body["operation_id"], "created_at": row["created_at"]})
            except NotFoundOrDenied:
                continue
            except (ValueError, TypeError, KeyError) as exc:
                raise AuthorityUnavailable("stored legacy adoption recovery record is invalid") from exc
        last = rows[limit - 1] if len(rows) > limit else None
        next_cursor = base64.urlsafe_b64encode(_canonical_json([HISTORY_CONTRACT, actor, ws, last["created_at"], last["operation_id"]]).encode()).decode() if last else None
        return {"contract": HISTORY_CONTRACT, "workspace_id": ws, "items": items, "next_cursor": next_cursor}
