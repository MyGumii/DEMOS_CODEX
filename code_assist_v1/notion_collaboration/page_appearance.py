"""Page appearance mutations and authorized metadata projections.

Use the active editing authority's transaction, receipt and event contracts.
Appearance never replaces blocks, CRDT generations, or immutable revisions.
"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

from code_assist_v1 import notion_icons
from .canonical_authority import (
    NotFoundOrDenied, _canonical_json, _identifier, _lock_generation_on_connection, _operation_id,
    _positive, _request_sha256, _sha256,
)
from .editing_authority import CollaborativeEditingAuthority, DocumentSequenceConflict
from .iam_authority import CollaborativeIAMAuthority, PAGE_ROLE_LEVEL
from .title_projection import read_current_titles, retry_title_projection


APPEARANCE_CONTRACT = "code_assist.notion.page_appearance.v1"
APPEARANCE_EVENT = "page.appearance.updated"
MAX_IMAGE_BYTES = 2 * 1024 * 1024


def _updates(value: dict) -> dict[str, str]:
    if not isinstance(value, dict) or not value or not set(value) <= {"icon", "cover"}:
        raise ValueError("appearance updates require icon or cover")
    clean = {}
    for key, raw in value.items():
        if not isinstance(raw, str) or len(raw) > (64 if key == "icon" else 2048):
            raise ValueError(f"invalid {key}")
        clean[key] = raw.strip()
    icon = clean.get("icon", "")
    if icon.startswith("ni:"):
        catalog = notion_icons._catalog()
        valid = {f"ni:{v['id']}:{c['id']}" for v in catalog["vectors"] for c in catalog["colors"]}
        if icon not in valid:
            raise ValueError("unknown icon or color")
    if icon.startswith("nf:") and not re.fullmatch(r"nf:(?:f_|file_)[A-Za-z0-9_-]{1,56}", icon):
        raise ValueError("invalid icon file")
    if icon.startswith("ne:") and not re.fullmatch(r"ne:we_[a-f0-9]{40}", icon):
        raise ValueError("invalid custom emoji")
    cover = clean.get("cover", "")
    if cover and not re.fullmatch(r"linear-gradient\([#a-zA-Z0-9%., ()-]+\)", cover):
        parsed = urlsplit(cover)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("cover requires an image URL or gradient")
    return clean


def _image_descriptor(authority, actor: str, document: str, icon: str):
    if not icon.startswith("nf:"):
        return None
    if not callable(getattr(authority, "get_file", None)):
        raise ValueError("image icons are unavailable")
    file = authority.get_file(actor, icon[3:])
    if file["document_id"] != document:
        raise NotFoundOrDenied("not found or permission denied")
    if not 0 < file["size_bytes"] <= MAX_IMAGE_BYTES:
        raise ValueError("icon image must be at most 2 MiB")
    # Blob IO remains outside the write transaction; identity is checked again inside it.
    with file["path"].open("rb") as stream:
        raw = stream.read(MAX_IMAGE_BYTES + 1)
    mime = file["mime_type"]
    valid = ((mime == "image/png" and raw.startswith(b"\x89PNG\r\n\x1a\n"))
             or (mime == "image/jpeg" and raw.startswith(b"\xff\xd8\xff"))
             or (mime == "image/webp" and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"))
    if not valid or len(raw) != file["size_bytes"] or _sha256(raw) != file["content_sha256"]:
        raise ValueError("icon image content does not match its file descriptor")
    return file


def _metadata(document, workspace, role: str, actor: str = "") -> dict:
    settings = json.loads(document["settings_json"])
    return {
        "id": document["id"], "workspace_id": document["workspace_id"],
        "parent_id": document["parent_document_id"] or "",
        "owner_user_id": document["legacy_owner_subject_id"],
        "scope": "own" if document["legacy_owner_subject_id"] == actor else "shared",
        "iam_role": role,
        "role": {"full_access": "owner", "edit": "edit", "edit_content": "edit", "comment": "comment", "view": "view"}[role],
        "can_edit": PAGE_ROLE_LEVEL[role] >= PAGE_ROLE_LEVEL["edit_content"],
        "can_share": role == "full_access",
        "can_move": role == "full_access" and not settings.get("locked"),
        "created_at": document["created_at"],
        "title": document["title"], "icon": document["icon"], "cover": document["cover"],
        "settings": settings, "updated_at": document["updated_at"],
        "revision": document["revision"], "document_sequence": document["document_sequence"],
        "workspace_epoch": workspace["security_epoch"], "document_epoch": document["access_epoch"],
        "can_change_appearance": PAGE_ROLE_LEVEL[role] >= PAGE_ROLE_LEVEL["edit"] and not settings.get("locked"),
        "can_change_settings": PAGE_ROLE_LEVEL[role] >= PAGE_ROLE_LEVEL["edit"],
    }


def update_page_appearance(authority: CollaborativeEditingAuthority, actor_subject_id: str,
                           workspace_id: str, document_id: str, *, updates: dict,
                           expected_document_sequence: int, expected_workspace_epoch: int,
                           expected_document_epoch: int, operation_id: str, fault=None) -> dict:
    actor = _identifier(actor_subject_id, field="actor_subject_id")
    workspace_id = _identifier(workspace_id, field="workspace_id")
    document = _identifier(document_id, field="document_id")
    operation = _operation_id(operation_id)
    clean = _updates(updates)
    sequence = _positive(expected_document_sequence, field="expected_document_sequence")
    workspace_epoch = _positive(expected_workspace_epoch, field="expected_workspace_epoch")
    document_epoch = _positive(expected_document_epoch, field="expected_document_epoch")
    action = "collaboration.page_appearance"
    request_hash = _request_sha256(actor, action, {
        "workspace_id": workspace_id, "document_id": document, "updates": clean,
        "expected_document_sequence": sequence, "expected_workspace_epoch": workspace_epoch,
        "expected_document_epoch": document_epoch,
    })
    file = _image_descriptor(authority, actor, document, clean.get("icon", ""))
    with authority._transaction() as con:
        def authorize():
            workspace, page, role = authority._exact_access(
                con, actor, document, minimum_role="edit",
                expected_workspace_epoch=workspace_epoch, expected_document_epoch=document_epoch,
            )
            if workspace["id"] != workspace_id:
                raise NotFoundOrDenied("not found or permission denied")
            from .canonical_authority import _require_unlocked_or_temporary
            _require_unlocked_or_temporary(con, page, actor)
            return workspace, page, role

        replay = authority._replay(con, actor=actor, action=action, operation_id=operation,
                                   request_sha256=request_hash, authorize=authorize)
        if replay is not None:
            return replay
        workspace, page, role = authorize()
        if page["document_sequence"] != sequence:
            raise DocumentSequenceConflict(page["document_sequence"])
        if file:
            row = con.execute("SELECT * FROM workspace_collaboration_files WHERE id=? AND document_id=? AND state='active'",
                              (file["file_id"], document)).fetchone()
            if row is None or any(row[key] != file[key] for key in ("content_sha256", "size_bytes", "mime_type")):
                raise NotFoundOrDenied("not found or permission denied")
        if clean.get("icon", "").startswith("ne:"):
            row = con.execute(
                "SELECT 1 FROM workspace_custom_emojis WHERE id=? AND workspace_id=?",
                (clean["icon"][3:], workspace_id),
            ).fetchone()
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")
        now = int(time.time())
        assignments = ",".join(f"{key}=?" for key in clean)  # keys are restricted by _updates.
        con.execute(f"UPDATE workspace_collaboration_documents SET {assignments},document_sequence=?,row_version=row_version+1,updated_at=? WHERE id=?",
                    (*clean.values(), sequence + 1, now, document))
        refreshed = authority._document(con, document)
        checksum = _sha256(_canonical_json(authority._editing_snapshot(con, refreshed)))
        con.execute("UPDATE workspace_collaboration_documents SET content_sha256=? WHERE id=?", (checksum, document))
        result = {"contract": APPEARANCE_CONTRACT, "page": _metadata(refreshed, workspace, role, actor),
                  "document_id": document, "workspace_id": workspace_id,
                  "document_sequence": sequence + 1, "content_sha256": checksum}
        return authority._finish(
            con, actor=actor, action=action, operation_id=operation, request_sha256=request_hash,
            workspace_id=workspace_id, document_id=document, workspace_epoch=workspace_epoch,
            document_epoch=document_epoch, document_sequence=sequence + 1,
            event_type=APPEARANCE_EVENT, event_payload={"changed_fields": sorted(clean)},
            result=result, fault=fault,
        )


@retry_title_projection
def project_page_metadata(authority: CollaborativeEditingAuthority, actor: str,
                          workspace_id: str, items: list[dict]) -> list[dict]:
    """Refresh only the already authorized bound set; never discover extra pages."""
    actor = authority._require_active(actor)
    with authority._reader() as con:
        con.execute("BEGIN")
        workspace = authority._workspace(con, workspace_id)
        rows = {}
        ids = [item["id"] for item in items]
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            marks = ",".join("?" for _ in chunk)
            for row in con.execute(f"SELECT * FROM workspace_collaboration_documents WHERE workspace_id=? AND archived=0 AND id IN ({marks})",
                                   (workspace_id, *chunk)):
                rows[row["id"]] = row
        result = []
        for item in items:
            row = rows.get(item["id"])
            if row is None:
                continue
            role, _ = CollaborativeIAMAuthority._resolution_on_connection(con, actor, workspace_id, row["id"])
            if role is not None:
                metadata = _metadata(row, workspace, role, actor)
                metadata["lock_generation"] = (_lock_generation_on_connection(con, row)
                    if metadata["settings"].get("locked") else "")
                result.append({**item, **metadata})
        # Roots have durable canonical positions. Below a parent, the child
        # links retain their body order, including explicit block moves.
        authorized_ids = {item["id"] for item in result}
        link_positions = {}
        for parent in {item["parent_id"] for item in result} & authorized_ids:
            for block in con.execute(
                "SELECT position,props_json FROM workspace_collaboration_blocks WHERE document_id=? AND block_type='child_page' ORDER BY position,id", (parent,)
            ):
                child = json.loads(block["props_json"]).get("page_id")
                if child in authorized_ids:
                    link_positions.setdefault((parent, child), block["position"])
        for item in result:
            if "position" not in item:
                item["position"] = (rows[item["id"]]["position"] if not item["parent_id"]
                    else link_positions.get((item["parent_id"], item["id"]), item["created_at"]))
    titles = read_current_titles(authority, actor, workspace_id, result)
    return [{**item, **titles[item["id"]]} for item in result if item["id"] in titles]
