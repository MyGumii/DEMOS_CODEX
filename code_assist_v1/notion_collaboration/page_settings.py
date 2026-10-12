"""Canonical page display/lock settings without replacing body or CRDT state."""
from __future__ import annotations

import json
import time

from .canonical_authority import NotFoundOrDenied, _canonical_json, _identifier, _lock_generation_on_connection, _operation_id, _positive, _request_sha256, _require_unlocked_or_temporary, _sha256
from .editing_authority import CollaborativeEditingAuthority, DocumentSequenceConflict
from .iam_authority import PAGE_ROLE_LEVEL
from .page_appearance import _metadata

SETTINGS_CONTRACT = "code_assist.notion.page_settings.v1"
SETTINGS_EVENT = "page.settings.updated"
TEMPORARY_UNLOCK_SALT = "notion-collaborative-page-temporary-unlock-v1"


def current_page_lock_generation(authority: CollaborativeEditingAuthority, actor_subject_id: str,
                                 workspace_id: str, document_id: str) -> str:
    """Read one locked page generation with current settings authority."""
    actor = _identifier(actor_subject_id, field="actor_subject_id")
    workspace_id = _identifier(workspace_id, field="workspace_id")
    document = _identifier(document_id, field="document_id")
    access = authority.resolve_access(actor, document)
    if access.workspace_id != workspace_id or PAGE_ROLE_LEVEL[access.role] < PAGE_ROLE_LEVEL["edit"]:
        raise NotFoundOrDenied("not found or permission denied")
    with authority._reader() as con:
        con.execute("BEGIN")
        workspace, page, _role = authority._exact_access(
            con, actor, document, minimum_role="edit",
            expected_workspace_epoch=access.workspace_epoch,
            expected_document_epoch=access.document_epoch,
        )
        if str(workspace["id"]) != workspace_id or page["archived"]:
            raise NotFoundOrDenied("not found or permission denied")
        return _lock_generation_on_connection(con, page)


def _updates(value):
    if not isinstance(value, dict) or not value or not set(value) <= {"font", "small_text", "full_width", "show_outline", "show_page_comments", "inline_comments", "locked"}:
        raise ValueError("invalid page settings")
    for key, item in value.items():
        if key == "font":
            if not isinstance(item, str) or item not in {"default", "serif", "mono"}:
                raise ValueError("invalid page font")
        elif key == "inline_comments":
            if not isinstance(item, str) or item not in {"default", "minimized"}:
                raise ValueError("invalid inline comments display")
        elif type(item) is not bool:
            raise ValueError(f"{key} must be a boolean")
    return dict(value)


def update_page_settings(authority: CollaborativeEditingAuthority, actor_subject_id: str,
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
    action = "collaboration.page_settings"
    request_hash = _request_sha256(actor, action, {
        "workspace_id": workspace_id, "document_id": document, "updates": clean,
        "expected_document_sequence": sequence, "expected_workspace_epoch": workspace_epoch,
        "expected_document_epoch": document_epoch,
    })
    with authority._transaction() as con:
        def authorize():
            workspace, page, role = authority._exact_access(con, actor, document, minimum_role="edit",
                expected_workspace_epoch=workspace_epoch, expected_document_epoch=document_epoch)
            if workspace["id"] != workspace_id or page["archived"]:
                raise NotFoundOrDenied("not found or permission denied")
            return workspace, page, role

        replay = authority._replay(con, actor=actor, action=action, operation_id=operation,
                                   request_sha256=request_hash, authorize=authorize)
        if replay is not None:
            return replay
        workspace, page, role = authorize()
        if page["document_sequence"] != sequence:
            raise DocumentSequenceConflict(page["document_sequence"])
        settings = json.loads(page["settings_json"])
        if settings.get("locked") and clean != {"locked": False}:
            try:
                _require_unlocked_or_temporary(con, page, actor)
            except PermissionError:
                raise PermissionError("unlock the page before changing its settings") from None
        settings.update(clean)
        con.execute("UPDATE workspace_collaboration_documents SET settings_json=?,document_sequence=?,"
                    "row_version=row_version+1,updated_at=? WHERE id=?",
                    (_canonical_json(settings), sequence + 1, int(time.time()), document))
        refreshed = authority._document(con, document)
        checksum = _sha256(_canonical_json(authority._editing_snapshot(con, refreshed)))
        con.execute("UPDATE workspace_collaboration_documents SET content_sha256=? WHERE id=?", (checksum, document))
        result = {"contract": SETTINGS_CONTRACT, "page": _metadata(refreshed, workspace, role, actor),
                  "document_id": document, "workspace_id": workspace_id,
                  "document_sequence": sequence + 1, "content_sha256": checksum}
        return authority._finish(con, actor=actor, action=action, operation_id=operation,
            request_sha256=request_hash, workspace_id=workspace_id, document_id=document,
            workspace_epoch=workspace_epoch, document_epoch=document_epoch, document_sequence=sequence + 1,
            event_type=SETTINGS_EVENT, event_payload={"changed_fields": sorted(clean)}, result=result, fault=fault)
