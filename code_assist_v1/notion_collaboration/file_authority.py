"""Bounded-memory, resumable and authorization-safe collaborative files."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
from typing import Any, BinaryIO, Mapping

from code_assist_v1 import notion_workspace_file_authority_migration as migration
from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1.notion_collaboration.canonical_authority import (
    AuthorityUnavailable,
    NotFoundOrDenied,
    OperationConflict,
    _canonical_json,
    _identifier,
    _operation_id,
    _request_sha256,
    _require_unlocked_or_temporary,
    _sha256,
    _stable_id,
)
from code_assist_v1.notion_collaboration.database_features import CollaborativeDatabaseAuthority
from code_assist_v1.notion_collaboration.editing_authority import DocumentSequenceConflict, TargetVersionConflict
from code_assist_v1.notion_collaboration.iam_authority import (
    CollaborativeIAMAuthority,
    InMemoryIdentityDirectory,
)


FILE_AUTHORITY_CONTRACT = "code_assist.notion.collaborative_file_authority.v1"
STORE_MARKER = ".collaborative-file-store.v1.json"
STORE_DIRECTORY = ".collaboration-files-v1"
MIN_CHUNK_SIZE = 65_536
MAX_CHUNK_SIZE = 8_388_608
DEFAULT_CHUNK_SIZE = 1_048_576
STREAM_BUFFER_SIZE = 1_048_576
MAX_OPERATIONAL_FILE_BYTES = 5 * 1024 * 1024 * 1024
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class CapacityUnavailable(RuntimeError):
    pass


class UploadStateConflict(RuntimeError):
    pass


def _marker_current(con: sqlite3.Connection) -> bool:
    row = con.execute(
        "SELECT name,checksum,actual_schema_sha256 FROM workspace_schema_migrations "
        "WHERE version=(SELECT MAX(version) FROM workspace_schema_migrations)"
    ).fetchone()
    return bool(
        row is not None
        and tuple(row)
        == (migration.TARGET_NAME, migration.TARGET_CHECKSUM, migration.TARGET_ACTUAL_SCHEMA_SHA256)
    )


def _safe_filename(value: str) -> str:
    clean = Path(str(value or "").replace("\\", "/")).name.strip()
    clean = "".join(character for character in clean if ord(character) >= 32 and character not in '<>:"/\\|?*')
    if not clean or clean in {".", ".."}:
        raise ValueError("valid filename required")
    return clean[:255]


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(STREAM_BUFFER_SIZE), b""):
            size += len(chunk)
            digest.update(chunk)
    return "sha256:" + digest.hexdigest(), size


class CollaborativeFileAuthority(CollaborativeDatabaseAuthority):
    def __init__(
        self,
        catalog_path: Path,
        user_data_root: Path,
        identity_directory: InMemoryIdentityDirectory,
        *,
        max_inflight_bytes: int = MAX_OPERATIONAL_FILE_BYTES,
        minimum_free_bytes: int = 0,
        minimum_free_percent: float = 0.0,
    ) -> None:
        super().__init__(catalog_path, user_data_root, identity_directory)
        self.storage_root = user_data_root / STORE_DIRECTORY
        self.max_inflight_bytes = int(max_inflight_bytes)
        self.minimum_free_bytes = int(minimum_free_bytes)
        self.minimum_free_percent = float(minimum_free_percent)

    @classmethod
    def open(
        cls,
        catalog_path: str | os.PathLike[str],
        user_data_root: str | os.PathLike[str],
        identity_directory: InMemoryIdentityDirectory,
        *,
        initialize_storage: bool = False,
        max_inflight_bytes: int = MAX_OPERATIONAL_FILE_BYTES,
        minimum_free_bytes: int = 0,
        minimum_free_percent: float = 0.0,
    ) -> "CollaborativeFileAuthority":
        try:
            catalog, root = migration_core._resolved_target(catalog_path, user_data_root)
            con = sqlite3.connect(f"{catalog.as_uri()}?mode=ro", uri=True)
            try:
                if not _marker_current(con):
                    raise AuthorityUnavailable("collaborative file schema is not current")
            finally:
                con.close()
        except AuthorityUnavailable:
            raise
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            raise AuthorityUnavailable("collaborative file authority is unavailable") from exc
        authority = cls(
            catalog,
            root,
            identity_directory,
            max_inflight_bytes=max_inflight_bytes,
            minimum_free_bytes=minimum_free_bytes,
            minimum_free_percent=minimum_free_percent,
        )
        if initialize_storage:
            authority.initialize_storage()
        else:
            authority._validate_storage()
        return authority

    def _connect(self, *, query_only: bool) -> sqlite3.Connection:
        try:
            uri = f"{self.catalog_path.as_uri()}?mode=ro" if query_only else None
            con = (
                sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
                if query_only
                else sqlite3.connect(self.catalog_path, timeout=5.0, isolation_level=None)
            )
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=5000")
            if query_only:
                con.execute("PRAGMA query_only=ON")
            if not _marker_current(con):
                con.close()
                raise AuthorityUnavailable("collaborative file schema marker mismatch")
            return con
        except AuthorityUnavailable:
            raise
        except sqlite3.Error as exc:
            raise AuthorityUnavailable("collaborative file authority is unavailable") from exc

    def initialize_storage(self) -> None:
        root = self.storage_root.resolve(strict=False)
        if root.parent != self.user_data_root.resolve(strict=True) or root.is_symlink():
            raise AuthorityUnavailable("file storage root is unsafe")
        root.mkdir(mode=0o700, parents=False, exist_ok=True)
        (root / "staging").mkdir(mode=0o700, exist_ok=True)
        (root / "blobs").mkdir(mode=0o700, exist_ok=True)
        marker = root / STORE_MARKER
        expected = {
            "contract": FILE_AUTHORITY_CONTRACT,
            "catalog_locator_sha256": _sha256(os.path.normcase(str(self.catalog_path)).encode("utf-8")),
        }
        if marker.exists():
            try:
                observed = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                raise AuthorityUnavailable("file storage marker is invalid") from exc
            if observed != expected:
                raise AuthorityUnavailable("file storage marker mismatch")
        else:
            temporary = root / (STORE_MARKER + ".tmp")
            temporary.write_text(_canonical_json(expected), encoding="utf-8")
            os.replace(temporary, marker)
        self._validate_storage()

    def _validate_storage(self) -> None:
        root = self.storage_root.resolve(strict=True)
        if root.parent != self.user_data_root.resolve(strict=True) or root.is_symlink():
            raise AuthorityUnavailable("file storage root is unsafe")
        marker = root / STORE_MARKER
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise AuthorityUnavailable("file storage marker is unavailable") from exc
        expected_locator = _sha256(os.path.normcase(str(self.catalog_path)).encode("utf-8"))
        if value != {"contract": FILE_AUTHORITY_CONTRACT, "catalog_locator_sha256": expected_locator}:
            raise AuthorityUnavailable("file storage marker mismatch")

    def _session_directory(self, session_id: str) -> Path:
        staging = (self.storage_root / "staging").resolve(strict=True)
        target = staging / _identifier(session_id, field="session_id")
        if target.parent != staging or target.is_symlink():
            raise AuthorityUnavailable("upload staging path escaped")
        return target

    def _blob_path(self, content_sha256: str) -> tuple[Path, str]:
        digest = content_sha256.removeprefix("sha256:")
        relative = f"blobs/{digest[:2]}/{digest[2:4]}/{digest}"
        target = (self.storage_root / relative).resolve(strict=False)
        target.relative_to(self.storage_root.resolve(strict=True))
        return target, relative

    @staticmethod
    def _editing_snapshot(con: sqlite3.Connection, document: sqlite3.Row) -> dict[str, Any]:
        descriptor = CollaborativeDatabaseAuthority._editing_snapshot(con, document)
        descriptor["files"] = [
            {
                "id": str(row["id"]),
                "name": str(row["original_name"]),
                "mime_type": str(row["mime_type"]),
                "size": int(row["size_bytes"]),
                "sha256": str(row["content_sha256"]),
                "state": str(row["state"]),
                "version": int(row["row_version"]),
            }
            for row in con.execute(
                "SELECT * FROM workspace_collaboration_files "
                "WHERE document_id=? AND state='active' ORDER BY created_at,id",
                (str(document["id"]),),
            ).fetchall()
        ]
        return descriptor

    def _reserve_capacity(self, con: sqlite3.Connection, total_size: int) -> None:
        reserved = int(
            con.execute(
                "SELECT COALESCE(SUM(reserved_bytes),0) FROM workspace_collaboration_upload_sessions "
                "WHERE state IN ('prepared','ready','committing')"
            ).fetchone()[0]
        )
        if total_size > self.max_inflight_bytes or reserved + total_size > self.max_inflight_bytes:
            raise CapacityUnavailable("operational inflight capacity unavailable")
        usage = shutil.disk_usage(self.storage_root)
        remaining = usage.free - total_size
        if remaining < self.minimum_free_bytes or (usage.total and remaining * 100 / usage.total < self.minimum_free_percent):
            raise CapacityUnavailable("operational disk reserve unavailable")

    def _session_access(self, con: sqlite3.Connection, actor: str,
                        row: sqlite3.Row):
        """Recheck a session's original page or immutable database scope."""
        from . import database_access

        scope = None
        if database_access.upload_scope_installed(con):
            scope = con.execute(
                "SELECT database_id,database_epoch FROM "
                "workspace_collaboration_database_upload_scopes WHERE session_id=?",
                (str(row["id"]),),
            ).fetchone()
        if scope is not None:
            access = database_access.require_write_on_connection(
                self, con, actor, str(row["workspace_id"]),
                str(scope["database_id"]), minimum_role="edit_content",
                expected_workspace_epoch=int(row["workspace_epoch"]),
                expected_document_epoch=int(row["document_epoch"]),
                expected_database_epoch=int(scope["database_epoch"]),
            )
            if access.document_id != str(row["document_id"]):
                raise NotFoundOrDenied("not found or permission denied")
            page = self._document(con, access.document_id)
            _require_unlocked_or_temporary(con, page, actor)
            return self._workspace(con, access.workspace_id), page, access.role, scope
        workspace, page, role = self._exact_access(
            con, actor, str(row["document_id"]), minimum_role="edit_content",
            expected_workspace_epoch=int(row["workspace_epoch"]),
            expected_document_epoch=int(row["document_epoch"]),
        )
        return workspace, page, role, None

    def begin_upload(
        self,
        actor_subject_id: str,
        document_id: str,
        *,
        original_name: str,
        mime_type: str,
        total_size: int,
        expected_sha256: str,
        expected_document_sequence: int,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        document = _identifier(document_id, field="document_id")
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
        request = {
            "document_id": document,
            "original_name": _safe_filename(original_name),
            "mime_type": str(mime_type or "application/octet-stream")[:255],
            "total_size": size,
            "chunk_size": chunk,
            "expected_sha256": checksum,
            "expected_document_sequence": int(expected_document_sequence),
            "expected_workspace_epoch": int(expected_workspace_epoch),
            "expected_document_epoch": int(expected_document_epoch),
        }
        action = "collaboration.file_upload_begun"
        request_sha = _request_sha256(actor, action, request)
        with self._transaction() as con:
            def authorize() -> None:
                self._exact_access(con, actor, document, minimum_role="edit_content", expected_workspace_epoch=int(expected_workspace_epoch), expected_document_epoch=int(expected_document_epoch))
            replay = self._replay(con, actor=actor, action=action, operation_id=operation, request_sha256=request_sha, authorize=authorize)
            if replay is not None:
                return replay
            workspace, page, role = self._exact_access(con, actor, document, minimum_role="edit_content", expected_workspace_epoch=int(expected_workspace_epoch), expected_document_epoch=int(expected_document_epoch))
            _require_unlocked_or_temporary(con, page, actor)
            if int(page["document_sequence"]) != int(expected_document_sequence):
                raise DocumentSequenceConflict(int(page["document_sequence"]))
            self._reserve_capacity(con, size)
            session_id = _stable_id("upload", actor, document, operation)
            now = int(time.time())
            con.execute(
                "INSERT INTO workspace_collaboration_upload_sessions("
                "id,workspace_id,document_id,actor_subject_id,state,original_name,mime_type,"
                "total_size,chunk_size,chunk_count,expected_sha256,reserved_bytes,workspace_epoch,"
                "document_epoch,source_document_sequence,begin_operation_id,row_version,created_at,updated_at,expires_at) "
                "VALUES(?,?,?,?,'prepared',?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
                (session_id, str(workspace["id"]), document, actor, request["original_name"], request["mime_type"], size, chunk, count, checksum, size, int(expected_workspace_epoch), int(expected_document_epoch), int(expected_document_sequence), operation, now, now, now + 86400),
            )
            result = {"contract": FILE_AUTHORITY_CONTRACT, "session_id": session_id, "document_id": document, "chunk_size": chunk, "chunk_count": count, "total_size": size, "expected_sha256": checksum, "state": "prepared", "effective_role": role}
            return self._finish(con, actor=actor, action=action, operation_id=operation, request_sha256=request_sha, workspace_id=str(workspace["id"]), document_id=document, workspace_epoch=int(expected_workspace_epoch), document_epoch=int(expected_document_epoch), document_sequence=None, event_type="file.upload.begun", event_payload=result, result=result, fault=None)

    def stage_chunk(
        self,
        actor_subject_id: str,
        session_id: str,
        *,
        chunk_index: int,
        stream: BinaryIO,
        operation_id: str,
    ) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        session = _identifier(session_id, field="session_id")
        operation = _operation_id(operation_id)
        index = int(chunk_index)
        with self._reader() as con:
            row = con.execute("SELECT * FROM workspace_collaboration_upload_sessions WHERE id=? AND actor_subject_id=?", (session, actor)).fetchone()
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")
            self._session_access(con, actor, row)
            if str(row["state"]) not in {"prepared", "ready"} or index < 0 or index >= int(row["chunk_count"]):
                raise UploadStateConflict("upload session does not accept this chunk")
            expected_size = int(row["chunk_size"])
            if index == int(row["chunk_count"]) - 1:
                expected_size = int(row["total_size"]) - int(row["chunk_size"]) * index
        directory = self._session_directory(session)
        directory.mkdir(mode=0o700, parents=False, exist_ok=True)
        # The full operation ID remains in the receipt binding, not the path.
        # Repeating it in the leaf can exceed Windows' path limit even when
        # the session directory and final chunk name fit.
        temporary_identity = hashlib.sha256(operation.encode("utf-8")).hexdigest()[:24]
        temporary = directory / f"{index:08d}.{temporary_identity}.tmp"
        final = directory / f"{index:08d}.chunk"
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("xb") as destination:
                while True:
                    chunk = stream.read(min(STREAM_BUFFER_SIZE, expected_size + 1 - size))
                    if not chunk:
                        break
                    if not isinstance(chunk, (bytes, bytearray)):
                        raise ValueError("binary chunk stream required")
                    size += len(chunk)
                    if size > expected_size:
                        raise ValueError("chunk exceeds expected size")
                    digest.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        if size != expected_size:
            temporary.unlink(missing_ok=True)
            raise ValueError("chunk size does not match manifest")
        checksum = "sha256:" + digest.hexdigest()
        if final.exists():
            existing_hash, existing_size = _sha256_file(final)
            temporary.unlink(missing_ok=True)
            if (existing_hash, existing_size) != (checksum, size):
                raise OperationConflict("chunk index already contains different bytes")
        else:
            os.replace(temporary, final)
        relative = final.relative_to(self.storage_root).as_posix()
        with self._transaction() as con:
            row = con.execute("SELECT * FROM workspace_collaboration_upload_sessions WHERE id=? AND actor_subject_id=?", (session, actor)).fetchone()
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")
            self._session_access(con, actor, row)
            if str(row["state"]) not in {"prepared", "ready"}:
                raise UploadStateConflict("upload session no longer accepts chunks")
            existing = con.execute("SELECT * FROM workspace_collaboration_upload_chunks WHERE session_id=? AND chunk_index=?", (session, index)).fetchone()
            if existing is not None:
                if str(existing["content_sha256"]) != checksum or int(existing["size_bytes"]) != size or str(existing["operation_id"]) != operation:
                    raise OperationConflict("chunk retry binding differs")
                return {"contract": FILE_AUTHORITY_CONTRACT, "session_id": session, "chunk_index": index, "size_bytes": size, "content_sha256": checksum, "replayed": True}
            con.execute("INSERT INTO workspace_collaboration_upload_chunks(session_id,chunk_index,size_bytes,content_sha256,relative_path,operation_id,created_at) VALUES(?,?,?,?,?,?,?)", (session, index, size, checksum, relative, operation, int(time.time())))
            received = int(con.execute("SELECT COUNT(*) FROM workspace_collaboration_upload_chunks WHERE session_id=?", (session,)).fetchone()[0])
            next_state = "ready" if received == int(row["chunk_count"]) else "prepared"
            con.execute("UPDATE workspace_collaboration_upload_sessions SET state=?,row_version=row_version+1,updated_at=? WHERE id=?", (next_state, int(time.time()), session))
            return {"contract": FILE_AUTHORITY_CONTRACT, "session_id": session, "chunk_index": index, "size_bytes": size, "content_sha256": checksum, "received_chunks": received, "state": next_state, "replayed": False}

    def upload_status(self, actor_subject_id: str, session_id: str) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        session = _identifier(session_id, field="session_id")
        with self._reader() as con:
            row = con.execute("SELECT * FROM workspace_collaboration_upload_sessions WHERE id=? AND actor_subject_id=?", (session, actor)).fetchone()
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")
            _workspace, _page, _role, scope = self._session_access(con, actor, row)
            received = [int(item[0]) for item in con.execute("SELECT chunk_index FROM workspace_collaboration_upload_chunks WHERE session_id=? ORDER BY chunk_index", (session,)).fetchall()]
            result = {"contract": FILE_AUTHORITY_CONTRACT, "session_id": session, "state": str(row["state"]), "chunk_size": int(row["chunk_size"]), "chunk_count": int(row["chunk_count"]), "total_size": int(row["total_size"]), "expected_sha256": str(row["expected_sha256"]), "received_chunks": received, "missing_chunks": [index for index in range(int(row["chunk_count"])) if index not in set(received)], "row_version": int(row["row_version"])}
            if scope is None:
                result["document_id"] = str(row["document_id"])
            else:
                result["database_id"] = str(scope["database_id"])
            return result

    def _assemble(self, row: sqlite3.Row, chunks: list[sqlite3.Row], operation_id: str) -> tuple[Path, str]:
        session = str(row["id"])
        # Keep the temporary leaf short enough for target Windows roots while
        # retaining collision resistance and the cleanup prefix contract.
        temporary_identity = hashlib.sha256(
            f"{session}\0{operation_id}\0{os.getpid()}\0{time.time_ns()}".encode(
                "utf-8"
            )
        ).hexdigest()[:24]
        temporary = self.storage_root / "staging" / f"assemble-{temporary_identity}.tmp"
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("xb") as destination:
                for chunk in chunks:
                    source_path = (self.storage_root / str(chunk["relative_path"])).resolve(strict=True)
                    source_path.relative_to(self._session_directory(session).resolve(strict=True))
                    with source_path.open("rb") as source:
                        for data in iter(lambda: source.read(STREAM_BUFFER_SIZE), b""):
                            size += len(data)
                            digest.update(data)
                            destination.write(data)
                destination.flush()
                os.fsync(destination.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        checksum = "sha256:" + digest.hexdigest()
        if size != int(row["total_size"]) or checksum != str(row["expected_sha256"]):
            temporary.unlink(missing_ok=True)
            raise UploadStateConflict("assembled file checksum or size mismatch")
        blob, relative = self._blob_path(checksum)
        blob.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if blob.exists():
            observed = _sha256_file(blob)
            temporary.unlink(missing_ok=True)
            if observed != (checksum, size):
                raise AuthorityUnavailable("content-addressed blob collision")
        else:
            os.replace(temporary, blob)
        return blob, relative

    def finalize_upload(self, actor_subject_id: str, session_id: str, *, operation_id: str) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        session = _identifier(session_id, field="session_id")
        operation = _operation_id(operation_id)
        action = "collaboration.file_upload_finalized"
        with self._reader() as con:
            row = con.execute("SELECT * FROM workspace_collaboration_upload_sessions WHERE id=? AND actor_subject_id=?", (session, actor)).fetchone()
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")
            request_sha = _request_sha256(actor, action, {"session_id": session})
            replay = self._replay(con, actor=actor, action=action, operation_id=operation, request_sha256=request_sha, authorize=lambda: self._session_access(con, actor, row))
            if replay is not None:
                return replay
            _workspace, page, _role, _scope = self._session_access(con, actor, row)
            _require_unlocked_or_temporary(con, page, actor)
            if str(row["state"]) != "ready":
                raise UploadStateConflict("upload is not ready")
            chunks = con.execute("SELECT * FROM workspace_collaboration_upload_chunks WHERE session_id=? ORDER BY chunk_index", (session,)).fetchall()
            if [int(item["chunk_index"]) for item in chunks] != list(range(int(row["chunk_count"]))):
                raise UploadStateConflict("upload chunks are incomplete")
            stable_row = row
        _blob, relative = self._assemble(stable_row, chunks, operation)
        with self._transaction() as con:
            row = con.execute("SELECT * FROM workspace_collaboration_upload_sessions WHERE id=? AND actor_subject_id=?", (session, actor)).fetchone()
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")
            workspace, page, role, scope = self._session_access(con, actor, row)
            replay = self._replay(con, actor=actor, action=action, operation_id=operation, request_sha256=request_sha, authorize=lambda: None)
            if replay is not None:
                return replay
            _require_unlocked_or_temporary(con, page, actor)
            source_sequence = int(row["source_document_sequence"])
            current_sequence = int(page["document_sequence"])
            intervening = []
            if current_sequence > source_sequence:
                intervening = con.execute(
                    "SELECT event_type,document_sequence FROM workspace_collaboration_events "
                    "WHERE document_id=? AND document_sequence>? AND document_sequence<=? "
                    "ORDER BY document_sequence,id",
                    (str(row["document_id"]), source_sequence, current_sequence),
                ).fetchall()
            attachment_only_advance = (
                current_sequence >= source_sequence
                and len(intervening) == current_sequence - source_sequence
                and all(str(item["event_type"]) == "file.upload.committed" for item in intervening)
            )
            if str(row["state"]) != "ready" or not attachment_only_advance:
                raise UploadStateConflict("upload source fence changed")
            blob_path = (self.storage_root / relative).resolve(strict=True)
            blob_path.relative_to(self.storage_root.resolve(strict=True))
            if _sha256_file(blob_path) != (str(row["expected_sha256"]), int(row["total_size"])):
                raise AuthorityUnavailable("assembled blob is unavailable")
            now = int(time.time())
            file_id = _stable_id("file", actor, str(row["document_id"]), operation)
            con.execute("UPDATE workspace_collaboration_upload_sessions SET state='committing',finalize_operation_id=?,file_id=?,row_version=row_version+1,updated_at=? WHERE id=?", (operation, file_id, now, session))
            con.execute("INSERT INTO workspace_collaboration_files(id,workspace_id,document_id,upload_session_id,uploader_subject_id,original_name,mime_type,size_bytes,content_sha256,blob_relative_path,state,row_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,'active',1,?,?)", (file_id, str(workspace["id"]), str(row["document_id"]), session, actor, str(row["original_name"]), str(row["mime_type"]), int(row["total_size"]), str(row["expected_sha256"]), relative, now, now))
            next_sequence = current_sequence + 1
            snapshot = self._editing_snapshot(con, page)
            content_sha = _sha256(_canonical_json(snapshot))
            con.execute("UPDATE workspace_collaboration_documents SET document_sequence=?,content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?", (next_sequence, content_sha, now, str(row["document_id"])))
            con.execute("UPDATE workspace_collaboration_upload_sessions SET state='committed',row_version=row_version+1,updated_at=?,completed_at=? WHERE id=?", (now, now, session))
            result = {"contract": FILE_AUTHORITY_CONTRACT, "file_id": file_id, "session_id": session, "size_bytes": int(row["total_size"]), "content_sha256": str(row["expected_sha256"]), "mime_type": str(row["mime_type"]), "original_name": str(row["original_name"]), "state": "active", "document_sequence": next_sequence, "effective_role": role}
            if scope is None:
                result["document_id"] = str(row["document_id"])
            else:
                result["database_id"] = str(scope["database_id"])
            return self._finish(con, actor=actor, action=action, operation_id=operation, request_sha256=request_sha, workspace_id=str(workspace["id"]), document_id=str(row["document_id"]), workspace_epoch=int(row["workspace_epoch"]), document_epoch=int(row["document_epoch"]), document_sequence=next_sequence, event_type="file.upload.committed", event_payload=result, result=result, fault=None)

    def get_file(self, subject_id: str, file_id: str) -> dict[str, Any]:
        subject = self._require_active(subject_id)
        file_identity = _identifier(file_id, field="file_id")
        from . import database_trash
        for _attempt in range(3):
            with self._reader() as con:
                con.execute("BEGIN")
                row = con.execute("SELECT * FROM workspace_collaboration_files "
                    "WHERE id=? AND state='active'", (file_identity,)).fetchone()
                if row is None:
                    raise NotFoundOrDenied("not found or permission denied")
                page = self._document(con, str(row["document_id"]))
                workspace = self._workspace(con, str(page["workspace_id"]))
                role, _sources = CollaborativeIAMAuthority._resolution_on_connection(
                    con, subject, str(workspace["id"]), str(page["id"]))
                if role is None:
                    raise NotFoundOrDenied("not found or permission denied")
                probe = database_trash.file_visibility_probe(self, con, page)
            hidden_files = database_trash.hidden_file_ids_from_probe(probe)
            with self._reader() as con:
                con.execute("BEGIN")
                current = con.execute("SELECT * FROM workspace_collaboration_files "
                    "WHERE id=? AND state='active'", (file_identity,)).fetchone()
                if current is None:
                    raise NotFoundOrDenied("not found or permission denied")
                page = self._document(con, str(current["document_id"]))
                workspace = self._workspace(con, str(page["workspace_id"]))
                role, _sources = CollaborativeIAMAuthority._resolution_on_connection(
                    con, subject, str(workspace["id"]), str(page["id"]))
                if role is None:
                    raise NotFoundOrDenied("not found or permission denied")
                if (tuple(current) != tuple(row)
                        or not database_trash.file_visibility_unchanged(con, page, probe)):
                    continue
                if file_identity in hidden_files:
                    raise NotFoundOrDenied("not found or permission denied")
                try:
                    path = (self.storage_root / str(current["blob_relative_path"])).resolve(strict=True)
                    path.relative_to(self.storage_root.resolve(strict=True))
                except (OSError, ValueError) as exc:
                    raise AuthorityUnavailable("authorized file content is unavailable") from exc
                return {"contract": FILE_AUTHORITY_CONTRACT, "file_id": file_identity,
                    "document_id": str(current["document_id"]),
                    "original_name": str(current["original_name"]),
                    "mime_type": str(current["mime_type"]),
                    "size_bytes": int(current["size_bytes"]),
                    "content_sha256": str(current["content_sha256"]),
                    "path": path, "effective_role": role}
        raise AuthorityUnavailable("file visibility changed during read; retry")

    def list_files(self, subject_id: str, document_id: str) -> dict[str, Any]:
        subject = self._require_active(subject_id)
        document = _identifier(document_id, field="document_id")
        from . import database_trash
        for _attempt in range(3):
            with self._reader() as con:
                con.execute("BEGIN")
                page = self._document(con, document)
                workspace = self._workspace(con, str(page["workspace_id"]))
                role, _sources = CollaborativeIAMAuthority._resolution_on_connection(
                    con, subject, str(workspace["id"]), document)
                if role is None:
                    raise NotFoundOrDenied("not found or permission denied")
                probe = database_trash.file_visibility_probe(self, con, page)
            hidden_files = database_trash.hidden_file_ids_from_probe(probe)
            with self._reader() as con:
                con.execute("BEGIN")
                page = self._document(con, document)
                workspace = self._workspace(con, str(page["workspace_id"]))
                role, _sources = CollaborativeIAMAuthority._resolution_on_connection(
                    con, subject, str(workspace["id"]), document)
                if role is None:
                    raise NotFoundOrDenied("not found or permission denied")
                if not database_trash.file_visibility_unchanged(con, page, probe):
                    continue
                files = [
                    {"id": str(row["id"]),
                     "original_name": str(row["original_name"]),
                     "mime_type": str(row["mime_type"]),
                     "size_bytes": int(row["size_bytes"]),
                     "content_sha256": str(row["content_sha256"]),
                     "created_at": int(row["created_at"]),
                     "row_version": int(row["row_version"])}
                    for row in con.execute(
                        "SELECT * FROM workspace_collaboration_files "
                        "WHERE document_id=? AND state='active' ORDER BY created_at,id",
                        (document,),
                    ).fetchall()
                    if str(row["id"]) not in hidden_files
                ]
                return {"contract": FILE_AUTHORITY_CONTRACT,
                    "document_id": document, "effective_role": role,
                    "files": files}
        raise AuthorityUnavailable("file visibility changed during read; retry")

    def delete_file(
        self,
        actor_subject_id: str,
        file_id: str,
        *,
        expected_document_sequence: int,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
    ) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        file_identity = _identifier(file_id, field="file_id")
        operation = _operation_id(operation_id)
        action = "collaboration.file_deleted"
        request = {
            "file_id": file_identity,
            "expected_document_sequence": int(expected_document_sequence),
            "expected_workspace_epoch": int(expected_workspace_epoch),
            "expected_document_epoch": int(expected_document_epoch),
        }
        request_sha = _request_sha256(actor, action, request)
        blob_relative_path: str | None = None
        with self._transaction() as con:
            row = con.execute(
                "SELECT * FROM workspace_collaboration_files WHERE id=?", (file_identity,)
            ).fetchone()
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")

            def authorize() -> None:
                self._exact_access(
                    con,
                    actor,
                    str(row["document_id"]),
                    minimum_role="edit_content",
                    expected_workspace_epoch=int(expected_workspace_epoch),
                    expected_document_epoch=int(expected_document_epoch),
                )

            replay = self._replay(
                con,
                actor=actor,
                action=action,
                operation_id=operation,
                request_sha256=request_sha,
                authorize=authorize,
            )
            if replay is not None:
                return replay
            workspace, page, role = self._exact_access(
                con,
                actor,
                str(row["document_id"]),
                minimum_role="edit_content",
                expected_workspace_epoch=int(expected_workspace_epoch),
                expected_document_epoch=int(expected_document_epoch),
            )
            _require_unlocked_or_temporary(con, page, actor)
            if str(row["state"]) != "active":
                raise NotFoundOrDenied("not found or permission denied")
            from . import database_trash
            if file_identity in database_trash.trashed_file_ids(con, str(page["id"])):
                raise NotFoundOrDenied("not found or permission denied")
            if int(page["document_sequence"]) != int(expected_document_sequence):
                raise DocumentSequenceConflict(int(page["document_sequence"]))
            now = int(time.time())
            con.execute(
                "UPDATE workspace_collaboration_files SET state='deleted',"
                "row_version=row_version+1,updated_at=?,deleted_at=?,deleted_by_subject_id=? "
                "WHERE id=?",
                (now, now, actor, file_identity),
            )
            next_sequence = int(page["document_sequence"]) + 1
            snapshot = self._editing_snapshot(con, page)
            content_sha = _sha256(_canonical_json(snapshot))
            con.execute(
                "UPDATE workspace_collaboration_documents SET document_sequence=?,"
                "content_sha256=?,row_version=row_version+1,updated_at=? WHERE id=?",
                (next_sequence, content_sha, now, str(row["document_id"])),
            )
            result = {
                "contract": FILE_AUTHORITY_CONTRACT,
                "file_id": file_identity,
                "document_id": str(row["document_id"]),
                "state": "deleted",
                "document_sequence": next_sequence,
                "effective_role": role,
            }
            stable = self._finish(
                con,
                actor=actor,
                action=action,
                operation_id=operation,
                request_sha256=request_sha,
                workspace_id=str(workspace["id"]),
                document_id=str(row["document_id"]),
                workspace_epoch=int(expected_workspace_epoch),
                document_epoch=int(expected_document_epoch),
                document_sequence=next_sequence,
                event_type="file.deleted",
                event_payload=result,
                result=result,
                fault=None,
            )
            blob_relative_path = str(row["blob_relative_path"])
        if blob_relative_path is not None:
            self._remove_unreferenced_blob(blob_relative_path)
        return stable

    def capacity_policy(self) -> dict[str, Any]:
        return {
            "contract": FILE_AUTHORITY_CONTRACT,
            "plan_entitlement_limits": False,
            "operational_inflight_bytes": self.max_inflight_bytes,
            "operational_minimum_free_bytes": self.minimum_free_bytes,
            "operational_minimum_free_percent": self.minimum_free_percent,
        }

    def _remove_unreferenced_blob(self, relative_path: str) -> bool:
        try:
            candidate = (self.storage_root / relative_path).resolve(strict=True)
            candidate.relative_to((self.storage_root / "blobs").resolve(strict=True))
            if candidate.is_symlink() or not candidate.is_file():
                return False
            with self._transaction() as con:
                active = int(
                    con.execute(
                        "SELECT COUNT(*) FROM workspace_collaboration_files "
                        "WHERE blob_relative_path=? AND state='active'",
                        (relative_path,),
                    ).fetchone()[0]
                )
                if active:
                    return False
                candidate.unlink(missing_ok=True)
            return True
        except (OSError, ValueError):
            return False

    def cleanup_orphans(self, *, now: int | None = None, max_items: int = 256) -> dict[str, Any]:
        cutoff = int(time.time()) if now is None else int(now)
        limit = max(1, min(int(max_items), 4096))
        marked: list[str] = []
        with self._transaction() as con:
            rows = con.execute(
                "SELECT * FROM workspace_collaboration_upload_sessions "
                "WHERE (state IN ('prepared','ready') AND expires_at<=?) "
                "OR state IN ('canceled','cleanup_required') "
                "ORDER BY updated_at,id LIMIT ?",
                (cutoff, limit),
            ).fetchall()
            for row in rows:
                session = str(row["id"])
                state = str(row["state"])
                transition_time = max(cutoff, int(row["updated_at"]))
                if state in {"prepared", "ready"}:
                    cleanup_operation = _stable_id(
                        "cleanup", session, str(row["expires_at"])
                    )
                    con.execute(
                        "UPDATE workspace_collaboration_upload_sessions SET "
                        "state='cleanup_required',cancel_operation_id=?,"
                        "row_version=row_version+1,updated_at=?,completed_at=? WHERE id=?",
                        (cleanup_operation, transition_time, transition_time, session),
                    )
                elif state == "canceled":
                    con.execute(
                        "UPDATE workspace_collaboration_upload_sessions SET "
                        "state='cleanup_required',row_version=row_version+1,updated_at=? "
                        "WHERE id=?",
                        (transition_time, session),
                    )
                marked.append(session)

        removed_sessions: list[str] = []
        cleanup_failures: list[str] = []
        staging = (self.storage_root / "staging").resolve(strict=True)
        candidates = list(marked)
        for candidate in sorted(staging.iterdir(), key=lambda item: item.name):
            if len(candidates) >= limit or not candidate.is_dir() or candidate.is_symlink():
                continue
            session = candidate.name
            with self._reader() as con:
                row = con.execute(
                    "SELECT state,expires_at FROM workspace_collaboration_upload_sessions WHERE id=?",
                    (session,),
                ).fetchone()
            if row is not None and (
                str(row["state"]) in {"committed", "canceled", "cleanup_required", "cleaned"}
                or (str(row["state"]) in {"prepared", "ready"} and int(row["expires_at"]) <= cutoff)
            ):
                candidates.append(session)
        for session in dict.fromkeys(candidates):
            try:
                directory = self._session_directory(session)
                if directory.exists():
                    shutil.rmtree(directory)
                removed_sessions.append(session)
            except (OSError, ValueError, AuthorityUnavailable):
                cleanup_failures.append(session)

        if removed_sessions:
            with self._transaction() as con:
                for session in removed_sessions:
                    con.execute(
                        "UPDATE workspace_collaboration_upload_sessions SET state='cleaned',"
                        "row_version=row_version+1,updated_at=MAX(updated_at,?) "
                        "WHERE id=? AND state IN ('canceled','cleanup_required')",
                        (cutoff, session),
                    )

        removed_assembly_files = 0
        assembly_stale_before = cutoff - 3600
        for candidate in sorted(staging.glob("assemble-*.tmp"))[:limit]:
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                if int(candidate.stat().st_mtime) <= assembly_stale_before:
                    candidate.unlink(missing_ok=True)
                    removed_assembly_files += 1
            except OSError:
                continue

        removed_blobs = 0
        blobs = (self.storage_root / "blobs").resolve(strict=True)
        for candidate in sorted(blobs.rglob("*")):
            if removed_blobs >= limit:
                break
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                relative = candidate.relative_to(self.storage_root.resolve(strict=True)).as_posix()
            except (OSError, ValueError):
                continue
            if self._remove_unreferenced_blob(relative):
                removed_blobs += 1
        return {
            "contract": FILE_AUTHORITY_CONTRACT,
            "marked_sessions": marked,
            "removed_sessions": removed_sessions,
            "cleanup_failures": cleanup_failures,
            "removed_assembly_files": removed_assembly_files,
            "removed_blobs": removed_blobs,
        }

    def cancel_upload(self, actor_subject_id: str, session_id: str, *, operation_id: str) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        session = _identifier(session_id, field="session_id")
        operation = _operation_id(operation_id)
        action = "collaboration.file_upload_canceled"
        request_sha = _request_sha256(actor, action, {"session_id": session})
        with self._transaction() as con:
            row = con.execute("SELECT * FROM workspace_collaboration_upload_sessions WHERE id=? AND actor_subject_id=?", (session, actor)).fetchone()
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")
            workspace, _page, role, _scope = self._session_access(con, actor, row)
            replay = self._replay(con, actor=actor, action=action, operation_id=operation, request_sha256=request_sha, authorize=lambda: None)
            if replay is not None:
                stable = replay
            else:
                if str(row["state"]) not in {"prepared", "ready"}:
                    raise UploadStateConflict("upload cannot be canceled")
                now = int(time.time())
                con.execute("UPDATE workspace_collaboration_upload_sessions SET state='canceled',cancel_operation_id=?,row_version=row_version+1,updated_at=?,completed_at=? WHERE id=?", (operation, now, now, session))
                result = {"contract": FILE_AUTHORITY_CONTRACT, "session_id": session, "state": "canceled", "cleanup_queued": True, "effective_role": role}
                stable = self._finish(con, actor=actor, action=action, operation_id=operation, request_sha256=request_sha, workspace_id=str(workspace["id"]), document_id=str(row["document_id"]), workspace_epoch=int(row["workspace_epoch"]), document_epoch=int(row["document_epoch"]), document_sequence=None, event_type="file.upload.canceled", event_payload=result, result=result, fault=None)
        self.cleanup_orphans(max_items=1)
        return stable


__all__ = ["CapacityUnavailable", "CollaborativeFileAuthority", "FILE_AUTHORITY_CONTRACT", "UploadStateConflict"]
