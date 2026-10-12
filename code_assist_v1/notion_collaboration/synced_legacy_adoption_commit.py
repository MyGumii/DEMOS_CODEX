"""Atomic backend adoption of an explicitly reviewed legacy synced selection.

The gated review/request service owns public API access. This writer performs
no automatic migration or operating flag change. Preparation, Yjs replay and
delta construction precede the writer.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from . import page_move, search_projection, synced_blocks as synced
from . import synced_legacy_adoption as adoption
from .canonical_authority import AuthorityUnavailable, _canonical_json, _operation_id, _request_sha256, _sha256, _stable_id
from .page_copy_source import MAX_SOURCE_BYTES, _owner

CONTRACT = "code_assist.notion.synced_legacy_adoption_commit.v1"
DELTA_CONTRACT = "code_assist.notion.synced_legacy_adoption_delta.v1"
ACTION = "collaboration.synced_legacy_adoption"
DOCUMENT_ACTION = "collaboration.synced_legacy_adoption_document"
FRAME_ACTION = "collaboration.synced_legacy_adoption_delta"


@dataclass(frozen=True)
class PreparedAdoptionDelta:
    adoption: adoption.PreparedLegacyAdoption
    updates: tuple[tuple[str, tuple[bytes, ...]], ...]
    current_titles: tuple[tuple[str, str], ...]
    payload_json: str
    payload_sha256: str

    def value(self):
        return json.loads(self.payload_json)


def _check_updates(prepared, updates):
    if tuple(document for document, _frames in updates) != tuple(item.source.document_id for item in prepared.documents):
        raise ValueError("adoption update scope was modified")
    total = 0
    for document, frames in updates:
        if (not frames and not _read_only_original(prepared, document)) or any(not isinstance(frame, bytes) or not 0 < len(frame) <= 1_048_576 for frame in frames):
            raise ValueError("bounded adoption update frames are required")
        if _read_only_original(prepared, document) and frames:
            raise ValueError("an unchanged original cannot have prepared write frames")
        total += sum(map(len, frames))
    if total > MAX_SOURCE_BYTES:
        raise synced.SyncedShapeUnavailable("adoption updates exceed the preparation budget")


def _read_only_original(prepared, document):
    return (prepared.provenance_json is not None and document == prepared.origin[0]
            and not any(doc == document for doc, _block in prepared.references))


def _metadata(prepared, updates, current_titles):
    _check_updates(prepared, updates)
    if (tuple(document for document, _title in current_titles) != tuple(document for document, _frames in updates)
            or any(not isinstance(title, str) or len(title) > 1000 for _document, title in current_titles)):
        raise ValueError("exact current adoption titles are required")
    titles = dict(current_titles)
    return _canonical_json({"contract": DELTA_CONTRACT, "preparation_sha256": prepared.payload_sha256,
        "documents": [{"document_id": document, "current_title": titles[document],
                       "update_sha256": [_sha256(frame) for frame in frames],
                       "update_bytes": [len(frame) for frame in frames]} for document, frames in updates]})


def _prepare_updates(prepared):
    node = shutil.which("node")
    if not node:
        raise AuthorityUnavailable("legacy adoption requires the selected Yjs runtime")
    writable = tuple(item for item in prepared.documents if not _read_only_original(prepared, item.source.document_id))
    request = {"contract": DELTA_CONTRACT, "documents": [{"document_id": item.source.document_id,
        "generation": item.source.stamp[-1], "seed": json.loads(item.source.descriptor_json),
        "frames": [base64.b64encode(frame).decode("ascii") for frame in item.source.frames],
        "replacement": item.blocks()} for item in writable]}
    try:
        result = subprocess.run([node, "--max-old-space-size=512", str(Path(__file__).with_name("synced_legacy_adoption_delta.cjs"))],
            input=_canonical_json(request).encode("utf-8"), capture_output=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        response = json.loads(result.stdout) if result.returncode == 0 else {}
        if (response.get("contract") != DELTA_CONTRACT or not isinstance(response.get("documents"), list)
                or len(response["documents"]) != len(writable)):
            raise ValueError(f"invalid adoption update response (runtime exit {result.returncode})")
        updates = []
        for item, value in zip(writable, response["documents"]):
            if (not isinstance(value, dict) or value.get("document_id") != item.source.document_id
                    or value.get("generation") != item.source.stamp[-1]
                    or value.get("source_update_count") != len(item.source.frames)
                    or value.get("added_ids") != [] or value.get("removed_ids") != []
                    or not isinstance(value.get("updates"), list)):
                raise ValueError("invalid adoption update scope")
            updates.append((item.source.document_id, tuple(base64.b64decode(frame, validate=True) for frame in value["updates"])))
        by_document = dict(updates)
        updates = tuple((item.source.document_id, by_document.get(item.source.document_id, ())) for item in prepared.documents)
        _check_updates(prepared, updates)
        # Independently decode durable old + prepared updates before any writer.
        decoded = search_projection.materialize([list(item.source.frames) + list(frames)
            for item, (_document, frames) in zip(prepared.documents, updates)])
        if len(decoded) != len(prepared.documents):
            raise ValueError("adoption update projection scope differs")
        current_titles = []
        for item, projection in zip(prepared.documents, decoded):
            expected = [{key: value for key, value in block.items() if key != "position"} for block in item.blocks()]
            if _read_only_original(prepared, item.source.document_id):
                # Current replay is authoritative. Canonical-only post-frame
                # insertions already checked by _current may remain outside
                # this stream; attaching a reference must not materialize them
                # into an otherwise unchanged original page.
                projected = projection["blocks"]
                indexed = {block["id"]: block for block in expected}
                ids = [block["id"] for block in projected] if isinstance(projected, list) else []
                projected_ids = set(ids)
                if (not isinstance(projected, list) or len(projected_ids) != len(ids)
                        or any(identity not in indexed for identity in ids)
                        or projected != [indexed[identity] for identity in ids]
                        or [block["id"] for block in expected if block["id"] in projected_ids] != ids):
                    raise ValueError("unchanged original replay differs from captured body")
            elif projection["blocks"] != expected:
                raise ValueError("adoption update projection differs from prepared body")
            # A present empty Y.Text is intentional. Only a missing title lane
            # falls back to its current canonical seed, never to a placeholder.
            title = projection["title"]
            if title is None:
                title = json.loads(item.source.descriptor_json)["title"]
            current_titles.append((item.source.document_id, title))
        current_titles = tuple(current_titles)
        _metadata(prepared, updates, current_titles)
        return updates, current_titles
    except synced.SyncedShapeUnavailable:
        raise
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        raise AuthorityUnavailable("legacy adoption updates could not be prepared") from exc


def prepare_adoption_delta(authority, prepared):
    with authority._reader() as con:
        con.execute("BEGIN")
        adoption.revalidate_legacy_adoption(authority, con, prepared)
    updates, current_titles = _prepare_updates(prepared)
    payload = _metadata(prepared, updates, current_titles)
    result = PreparedAdoptionDelta(prepared, updates, current_titles, payload, _sha256(payload))
    with authority._reader() as con:
        con.execute("BEGIN")
        revalidate_adoption_delta(authority, con, result)
    return result


def revalidate_adoption_delta(authority, con, prepared):
    if (not isinstance(prepared, PreparedAdoptionDelta)
            or _sha256(prepared.payload_json) != prepared.payload_sha256
            or _metadata(prepared.adoption, prepared.updates, prepared.current_titles) != prepared.payload_json):
        raise ValueError("immutable adoption updates are required")
    adoption.revalidate_legacy_adoption(authority, con, prepared.adoption)


def _authorize_receipt(authority, con, prepared):
    selection = prepared.adoption
    for item in selection.documents:
        _owner(authority, con, selection.actor, selection.workspace_id, item.source.document_id)
        page_move._authorize(authority, con, selection.actor, selection.workspace_id, item.source.document_id, "view")


def _request(prepared):
    selected = prepared.adoption
    return _request_sha256(selected.actor, ACTION, {"workspace_id": selected.workspace_id,
        "origin": selected.origin, "references": selected.references,
        "preparation_sha256": selected.payload_sha256})


def _write_body(authority, con, item, current_title, now):
    document = item.source.document_id
    before = authority._editing_snapshot(con, authority._document(con, document))
    captured = json.loads(item.source.descriptor_json)
    for field in ("title", "icon", "cover", "settings", "databases", "files"):
        if before[field] != captured[field]:
            raise synced.SyncedSourceChanged("legacy adoption canonical resources changed")
    wanted = item.blocks()
    if [block["id"] for block in before["blocks"]] != [block["id"] for block in wanted]:
        raise synced.SyncedSourceChanged("legacy adoption body identities changed")
    # Preserve positions, creation times and unchanged block row versions.
    # Current durable text may legitimately precede its SQL projection.
    for old, block in zip(before["blocks"], wanted):
        if (old["type"], old["content"], old["props"]) != (block["type"], block["content"], block["props"]):
            con.execute("UPDATE workspace_collaboration_blocks SET block_type=?,content=?,props_json=?,"
                        "row_version=row_version+1,updated_at=? WHERE document_id=? AND id=?",
                        (block["type"], block["content"], _canonical_json(block["props"]), now, document, block["id"]))
    if before["title"] != current_title:
        con.execute("UPDATE workspace_collaboration_documents SET title=?,updated_at=? WHERE id=?",
                    (current_title, now, document))
    after = authority._editing_snapshot(con, authority._document(con, document))
    if ([{key: value for key, value in block.items() if key != "position"} for block in after["blocks"]]
            != [{key: value for key, value in block.items() if key != "position"} for block in wanted]):
        raise AuthorityUnavailable("adopted canonical body differs from prepared body")
    if (after["title"] != current_title
            or any(after[field] != before[field] for field in ("icon", "cover", "settings", "databases", "files"))):
        raise AuthorityUnavailable("adoption changed document resources")
    return after


def commit_prepared_adoption(authority, prepared, *, operation_id, fault=None, require_intent=False):
    """Commit all selected endpoints once, with matching durable body updates."""
    operation = _operation_id(operation_id)
    if not isinstance(prepared, PreparedAdoptionDelta):
        raise ValueError("prepared legacy adoption updates are required")
    selected = prepared.adoption
    request_hash = _request(prepared)
    def replay(con):
        previous = authority._replay(con, actor=selected.actor, action=ACTION,
            operation_id=operation, request_sha256=request_hash,
            authorize=lambda: _authorize_receipt(authority, con, prepared))
        return None if previous is None else {**previous, "replayed": True}
    with authority._reader() as con:
        con.execute("BEGIN")
        previous = replay(con)
        if previous is not None:
            return previous
    with authority._transaction() as con:
        previous = replay(con)
        if previous is not None:
            return previous
        if require_intent:
            from .synced_legacy_adoption_service import require_registered_intent
            require_registered_intent(authority, con, selected, operation)
        revalidate_adoption_delta(authority, con, prepared)
        workspace = authority._workspace(con, selected.workspace_id)
        now, results = int(time.time()), []
        frames_by_document = dict(prepared.updates)
        titles_by_document = dict(prepared.current_titles)
        # The primary receipt is written last; every document shares this TX.
        ordered = sorted(selected.documents, key=lambda item: item.source.document_id == selected.origin[0])
        for item in ordered:
            document = item.source.document_id
            page = authority._document(con, document)
            if _read_only_original(selected, document):
                # Adding a reference on another page does not edit the original
                # page, materialize its SQL projection, append an empty update,
                # or create a misleading original-page revision.
                results.append({"document_id": document, "revision": page["revision"],
                    "document_sequence": page["document_sequence"], "title": titles_by_document[document],
                    "document_epoch": page["access_epoch"], "generation": item.source.stamp[-1],
                    "content_sha256": page["content_sha256"], "update_frames": 0, "unchanged": True})
                continue
            after = _write_body(authority, con, item, titles_by_document[document], now)
            frames = frames_by_document[document]
            for ordinal, frame in enumerate(frames):
                frame_operation = _stable_id("legacyframe", selected.actor, operation, document, str(ordinal))
                page_move._append_link_delta(authority, con, selected.actor, workspace, page, frame,
                    item.source.stamp[-1], frame_operation, now, operation_action=ACTION,
                    receipt_action=FRAME_ACTION, client_id="synced-legacy-adoption-service",
                    binding_contract=CONTRACT, identity_prefix="legacyupd")
            sequence = authority._document(con, document)["document_sequence"] + 1
            revision = page["revision"] + 1
            checksum = _sha256(_canonical_json(after))
            con.execute("UPDATE workspace_collaboration_documents SET revision=?,document_sequence=?,"
                        "row_version=row_version+1,content_sha256=?,updated_at=? WHERE id=?",
                        (revision, sequence, checksum, now, document))
            con.execute("INSERT INTO workspace_collaboration_revisions(document_id,revision,document_sequence,"
                        "content_sha256,snapshot_json,actor_subject_id,operation_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (document, revision, sequence, checksum, _canonical_json(after), selected.actor, operation, now))
            result = {"document_id": document, "revision": revision, "document_sequence": sequence,
                "title": after["title"],
                "document_epoch": page["access_epoch"], "generation": item.source.stamp[-1],
                "content_sha256": checksum, "update_frames": len(frames)}
            results.append(result)
            if document != selected.origin[0]:
                local_operation = _stable_id("legacydoc", selected.actor, operation, document)
                authority._finish(con, actor=selected.actor, action=DOCUMENT_ACTION, operation_id=local_operation,
                    request_sha256=request_hash, workspace_id=selected.workspace_id, document_id=document,
                    workspace_epoch=workspace["security_epoch"], document_epoch=page["access_epoch"],
                    document_sequence=sequence, event_type="synced.legacy_adopted", event_payload=result, result=result, fault=None)
        con.execute("UPDATE workspaces SET row_version=row_version+1,updated_at=? WHERE id=?", (now, selected.workspace_id))
        result = {"contract": CONTRACT, "workspace_id": selected.workspace_id, "operation_id": operation,
            "preparation_sha256": selected.payload_sha256, "origin": selected.value()["origin"],
            "references": selected.value()["references"], "sync_id": selected.value()["sync_id"],
            "legacy_sync_id": selected.value()["legacy_sync_id"],
            "workspace_epoch": workspace["security_epoch"], "documents": sorted(results, key=lambda item: item["document_id"])}
        if selected.provenance_json is not None:
            result["origin_adoption"] = selected.value()["origin_adoption"]
        origin_result = next(item for item in results if item["document_id"] == selected.origin[0])
        original_unchanged = _read_only_original(selected, selected.origin[0])
        saved = authority._finish(con, actor=selected.actor, action=ACTION, operation_id=operation,
            request_sha256=request_hash, workspace_id=selected.workspace_id,
            document_id=None if original_unchanged else selected.origin[0],
            workspace_epoch=workspace["security_epoch"],
            document_epoch=None if original_unchanged else origin_result["document_epoch"],
            document_sequence=None if original_unchanged else origin_result["document_sequence"], event_type="synced.legacy_adopted",
            event_payload=result, result=result, fault=fault)
        return {**saved, "replayed": False}
