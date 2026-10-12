"""Database-scoped upload entry points for the staged sharing successor."""
from __future__ import annotations

import time

from .canonical_authority import (
    AuthorityUnavailable, NotFoundOrDenied, OperationConflict,
    _canonical_json, _identifier, _operation_id, _request_sha256,
    _require_unlocked_or_temporary, _sha256, _stable_id,
)
from .database_access import require_write_on_connection, upload_scope_installed
from .editing_authority import DocumentSequenceConflict
from .file_authority import (
    DEFAULT_CHUNK_SIZE, MAX_CHUNK_SIZE, MIN_CHUNK_SIZE, FILE_AUTHORITY_CONTRACT,
    UploadStateConflict, _SHA256_RE, _safe_filename,
)
from . import database_trash


def begin_upload(authority, actor_subject_id: str, workspace_id: str,
                 database_id: str, *, original_name: str, mime_type: str,
                 total_size: int, expected_sha256: str,
                 expected_document_sequence: int,
                 expected_workspace_epoch: int,
                 expected_document_epoch: int,
                 expected_database_epoch: int,
                 operation_id: str,
                 chunk_size: int = DEFAULT_CHUNK_SIZE) -> dict:
    """Reserve one upload and bind it atomically to an authorized database."""
    actor = authority._require_active(actor_subject_id)
    workspace = _identifier(workspace_id, field="workspace_id")
    database = _identifier(database_id, field="database_id")
    operation = _operation_id(operation_id)
    size = int(total_size)
    chunk = int(chunk_size)
    if size < 0:
        raise ValueError("operational file size is invalid")
    if chunk < MIN_CHUNK_SIZE or chunk > MAX_CHUNK_SIZE:
        raise ValueError("chunk size is outside operational bounds")
    checksum = str(expected_sha256 or "").lower()
    if _SHA256_RE.fullmatch(checksum) is None:
        raise ValueError("canonical sha256 required")
    count = max(1, (size + chunk - 1) // chunk)
    sequence = int(expected_document_sequence)
    workspace_epoch = int(expected_workspace_epoch)
    document_epoch = int(expected_document_epoch)
    database_epoch = int(expected_database_epoch)
    request = {
        "workspace_id": workspace, "database_id": database,
        "original_name": _safe_filename(original_name),
        "mime_type": str(mime_type or "application/octet-stream")[:255],
        "total_size": size, "chunk_size": chunk, "expected_sha256": checksum,
        "expected_document_sequence": sequence,
        "expected_workspace_epoch": workspace_epoch,
        "expected_document_epoch": document_epoch,
        "expected_database_epoch": database_epoch,
    }
    action = "database.file_upload_begun"
    request_sha = _request_sha256(actor, action, request)
    with authority._transaction() as con:
        if not upload_scope_installed(con):
            raise AuthorityUnavailable("database upload scope is not installed")

        def authorize():
            return require_write_on_connection(
                authority, con, actor, workspace, database,
                minimum_role="edit_content",
                expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=document_epoch,
                expected_database_epoch=database_epoch,
            )

        replay = authority._replay(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=request_sha, authorize=authorize,
        )
        if replay is not None:
            return replay
        access = authorize()
        page = authority._document(con, access.document_id)
        _require_unlocked_or_temporary(con, page, actor)
        if int(page["document_sequence"]) != sequence:
            raise DocumentSequenceConflict(int(page["document_sequence"]))
        if con.execute(
            "SELECT 1 FROM workspace_collaboration_upload_sessions "
            "WHERE actor_subject_id=? AND begin_operation_id=?",
            (actor, operation),
        ).fetchone() is not None:
            raise OperationConflict("upload operation ID already belongs to another scope")
        authority._reserve_capacity(con, size)
        session_id = _stable_id("dbupload", actor, database, operation)
        now = int(time.time())
        con.execute(
            "INSERT INTO workspace_collaboration_upload_sessions("
            "id,workspace_id,document_id,actor_subject_id,state,original_name,mime_type,"
            "total_size,chunk_size,chunk_count,expected_sha256,reserved_bytes,workspace_epoch,"
            "document_epoch,source_document_sequence,begin_operation_id,row_version,created_at,"
            "updated_at,expires_at) VALUES(?,?,?,?,'prepared',?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
            (session_id, workspace, access.document_id, actor,
             request["original_name"], request["mime_type"], size, chunk, count,
             checksum, size, workspace_epoch, document_epoch, sequence,
             operation, now, now, now + 86400),
        )
        con.execute(
            "INSERT INTO workspace_collaboration_database_upload_scopes("
            "session_id,database_id,database_epoch,created_at) VALUES(?,?,?,?)",
            (session_id, database, database_epoch, now),
        )
        result = {
            "contract": FILE_AUTHORITY_CONTRACT, "session_id": session_id,
            "database_id": database, "chunk_size": chunk,
            "chunk_count": count, "total_size": size,
            "expected_sha256": checksum, "state": "prepared",
            "effective_role": access.role,
        }
        return authority._finish(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=request_sha, workspace_id=workspace,
            document_id=access.document_id, workspace_epoch=workspace_epoch,
            document_epoch=document_epoch, document_sequence=None,
            event_type="database.file_upload.begun", event_payload=result,
            result=result, fault=None,
        )


def delete_file(authority, actor_subject_id: str, workspace_id: str,
                database_id: str, file_id: str, *,
                expected_document_sequence: int,
                expected_workspace_epoch: int,
                expected_document_epoch: int,
                expected_database_epoch: int,
                operation_id: str) -> dict:
    """Delete an unreferenced file originally uploaded into this database."""
    from .database_move import _file_ids

    actor = authority._require_active(actor_subject_id)
    workspace = _identifier(workspace_id, field="workspace_id")
    database = _identifier(database_id, field="database_id")
    file_identity = _identifier(file_id, field="file_id")
    operation = _operation_id(operation_id)
    sequence = int(expected_document_sequence)
    workspace_epoch = int(expected_workspace_epoch)
    document_epoch = int(expected_document_epoch)
    database_epoch = int(expected_database_epoch)
    action = "database.file_deleted"
    request_sha = _request_sha256(actor, action, {
        "workspace_id": workspace, "database_id": database,
        "file_id": file_identity, "expected_document_sequence": sequence,
        "expected_workspace_epoch": workspace_epoch,
        "expected_document_epoch": document_epoch,
        "expected_database_epoch": database_epoch,
    })
    blob_relative_path = None
    with authority._transaction() as con:
        if not upload_scope_installed(con):
            raise AuthorityUnavailable("database upload scope is not installed")
        row = con.execute(
            "SELECT file.* FROM workspace_collaboration_files file "
            "JOIN workspace_collaboration_database_upload_scopes scope "
            "ON scope.session_id=file.upload_session_id "
            "WHERE file.id=? AND scope.database_id=?",
            (file_identity, database),
        ).fetchone()
        if row is None:
            raise NotFoundOrDenied("not found or permission denied")

        def authorize():
            access = require_write_on_connection(
                authority, con, actor, workspace, database,
                minimum_role="edit_content",
                expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=document_epoch,
                expected_database_epoch=database_epoch,
            )
            if (str(row["document_id"]) != access.document_id
                    or str(row["workspace_id"]) != access.workspace_id):
                raise NotFoundOrDenied("not found or permission denied")
            return access

        replay = authority._replay(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=request_sha, authorize=authorize,
        )
        if replay is not None:
            return replay
        access = authorize()
        page = authority._document(con, access.document_id)
        _require_unlocked_or_temporary(con, page, actor)
        if str(row["state"]) != "active":
            raise NotFoundOrDenied("not found or permission denied")
        if file_identity in database_trash.trashed_file_ids(con, access.document_id):
            raise UploadStateConflict("file remains in database Trash")
        if int(page["document_sequence"]) != sequence:
            raise DocumentSequenceConflict(int(page["document_sequence"]))
        before = authority._editing_snapshot(con, page)
        if file_identity in _file_ids(before):
            raise UploadStateConflict("file is still referenced")
        now = int(time.time())
        con.execute(
            "UPDATE workspace_collaboration_files SET state='deleted',"
            "row_version=row_version+1,updated_at=?,deleted_at=?,"
            "deleted_by_subject_id=? WHERE id=?",
            (now, now, actor, file_identity),
        )
        next_sequence = sequence + 1
        checksum = _sha256(_canonical_json(
            authority._editing_snapshot(con, authority._document(con, access.document_id))))
        con.execute(
            "UPDATE workspace_collaboration_documents SET document_sequence=?,"
            "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
            (next_sequence, checksum, now, access.document_id),
        )
        result = {
            "contract": FILE_AUTHORITY_CONTRACT, "workspace_id": workspace,
            "database_id": database, "file_id": file_identity,
            "state": "deleted", "document_sequence": next_sequence,
            "effective_role": access.role,
        }
        stable = authority._finish(
            con, actor=actor, action=action, operation_id=operation,
            request_sha256=request_sha, workspace_id=workspace,
            document_id=access.document_id, workspace_epoch=workspace_epoch,
            document_epoch=document_epoch, document_sequence=next_sequence,
            event_type="database.file.deleted", event_payload=result,
            result=result, fault=None,
        )
        blob_relative_path = str(row["blob_relative_path"])
    if blob_relative_path is not None:
        authority._remove_unreferenced_blob(blob_relative_path)
    return stable
