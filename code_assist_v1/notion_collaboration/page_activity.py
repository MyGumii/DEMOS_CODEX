"""Current authorized edit time without replaying or returning document content."""
from __future__ import annotations

from .canonical_authority import NotFoundOrDenied, _identifier
from .iam_authority import CollaborativeIAMAuthority


# Only document edits identify an editor. Comments, grants, receipts and
# checkpoint/compaction maintenance must not replace the last editing actor.
EDIT_EVENTS = (
    "document.created", "document.saved", "page.created", "legacy.snapshot_imported",
    "typed.command.committed", "typed.command", "crdt.update",
    "database.surface.committed", "database.schema.committed",
    "page.appearance.updated", "page.settings.updated", "history.revision.restored",
    "file.upload.committed", "file.deleted", "page.moved", "page.archived", "page.restored",
)


def read_page_activity(authority: CollaborativeIAMAuthority, actor_subject_id: str,
                       workspace_id: str, document_id: str) -> dict:
    actor = authority._require_active(actor_subject_id)
    workspace_id = _identifier(workspace_id, field="workspace_id")
    document_id = _identifier(document_id, field="document_id")
    with authority._reader() as con:
        con.execute("BEGIN")
        page = authority._document(con, document_id)
        if page["workspace_id"] != workspace_id:
            raise NotFoundOrDenied("not found or permission denied")
        role, _ = authority._resolution_on_connection(con, actor, workspace_id, document_id)
        if role is None:
            raise NotFoundOrDenied("not found or permission denied")
        workspace = authority._workspace(con, workspace_id)
        edit = con.execute(
            "SELECT actor_subject_id,document_sequence,created_at "
            "FROM workspace_collaboration_events WHERE workspace_id=? AND document_id=? "
            "AND document_sequence<=? AND event_type IN (" + ",".join("?" for _ in EDIT_EVENTS) + ") "
            "ORDER BY document_sequence DESC LIMIT 1",
            (workspace_id, document_id, int(page["document_sequence"]), *EDIT_EVENTS),
        ).fetchone()
        return {"page": {
            "id": document_id, "workspace_id": workspace_id,
            "document_sequence": int(page["document_sequence"]),
            "workspace_epoch": int(workspace["security_epoch"]),
            "document_epoch": int(page["access_epoch"]),
            "updated_at": int(page["updated_at"]),
            "last_edit": {"actor_subject_id": str(edit["actor_subject_id"]),
                          "document_sequence": int(edit["document_sequence"]),
                          "edited_at": int(edit["created_at"])} if edit else None,
        }}
