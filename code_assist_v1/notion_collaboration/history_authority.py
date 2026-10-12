"""Immutable page history, deterministic diff, and fenced revision restore."""

from __future__ import annotations

import difflib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

from code_assist_v1 import notion_workspace_history_authority_migration as migration
from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1.notion_update_preview import page_change_preview
from code_assist_v1.notion_collaboration import database_activity, database_row_content
from code_assist_v1.notion_collaboration.canonical_authority import (
    AuthorityUnavailable,
    NotFoundOrDenied,
    RevisionConflict,
    _canonical_json,
    _clean_blocks,
    _identifier,
    _operation_id,
    _request_sha256,
    _require_unlocked_or_temporary,
    _sha256,
    _stable_id,
)
from code_assist_v1.notion_collaboration.editing_authority import (
    DocumentSequenceConflict,
)
from code_assist_v1.notion_collaboration.file_authority import CollaborativeFileAuthority
from code_assist_v1.notion_collaboration.iam_authority import (
    CollaborativeIAMAuthority,
    InMemoryIdentityDirectory,
    PAGE_ROLE_LEVEL,
)


HISTORY_AUTHORITY_CONTRACT = "code_assist.notion.collaborative_history_authority.v1"
MAX_HISTORY_PAGE_SIZE = 100
MAX_DIFF_LINES = 5000


class HistoryIntegrityError(RuntimeError):
    pass


def _marker_current(con: sqlite3.Connection) -> bool:
    row = con.execute(
        "SELECT name,checksum,actual_schema_sha256 FROM workspace_schema_migrations "
        "WHERE version=(SELECT MAX(version) FROM workspace_schema_migrations)"
    ).fetchone()
    return bool(
        row is not None
        and tuple(row)
        == (
            migration.TARGET_NAME,
            migration.TARGET_CHECKSUM,
            migration.TARGET_ACTUAL_SCHEMA_SHA256,
        )
    )


class CollaborativeHistoryAuthority(CollaborativeFileAuthority):
    @classmethod
    def open(
        cls,
        catalog_path: str | os.PathLike[str],
        user_data_root: str | os.PathLike[str],
        identity_directory: InMemoryIdentityDirectory,
        **kwargs: Any,
    ) -> "CollaborativeHistoryAuthority":
        try:
            catalog, root = migration_core._resolved_target(catalog_path, user_data_root)
            con = sqlite3.connect(f"{catalog.as_uri()}?mode=ro", uri=True)
            try:
                if not _marker_current(con):
                    raise AuthorityUnavailable("collaborative history schema is not current")
            finally:
                con.close()
        except AuthorityUnavailable:
            raise
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            raise AuthorityUnavailable("collaborative history authority is unavailable") from exc
        authority = cls(catalog, root, identity_directory, **kwargs)
        authority._validate_storage()
        return authority

    def _connect(self, *, query_only: bool) -> sqlite3.Connection:
        try:
            con = (
                sqlite3.connect(
                    f"{self.catalog_path.as_uri()}?mode=ro",
                    uri=True,
                    timeout=5.0,
                    isolation_level=None,
                )
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
                raise AuthorityUnavailable("collaborative history schema marker mismatch")
            return con
        except AuthorityUnavailable:
            raise
        except sqlite3.Error as exc:
            raise AuthorityUnavailable("collaborative history authority is unavailable") from exc

    def _remove_unreferenced_blob(self, relative_path: str) -> bool:
        """2409 retains every committed blob while any history-capable row names it."""

        try:
            candidate = (self.storage_root / relative_path).resolve(strict=True)
            candidate.relative_to((self.storage_root / "blobs").resolve(strict=True))
            if candidate.is_symlink() or not candidate.is_file():
                return False
            with self._reader() as con:
                references = int(
                    con.execute(
                        "SELECT COUNT(*) FROM workspace_collaboration_files "
                        "WHERE blob_relative_path=?",
                        (relative_path,),
                    ).fetchone()[0]
                )
            if references:
                return False
            candidate.unlink(missing_ok=True)
            return True
        except (OSError, ValueError, AuthorityUnavailable):
            return False

    @staticmethod
    def _authorized_document(
        con: sqlite3.Connection,
        subject_id: str,
        document_id: str,
    ) -> tuple[sqlite3.Row, sqlite3.Row, str]:
        document = CollaborativeHistoryAuthority._document(con, document_id)
        workspace = CollaborativeHistoryAuthority._workspace(
            con, str(document["workspace_id"])
        )
        role, _sources = CollaborativeIAMAuthority._resolution_on_connection(
            con, subject_id, str(workspace["id"]), document_id
        )
        if role is None:
            raise NotFoundOrDenied("not found or permission denied")
        return workspace, document, role

    @staticmethod
    def _decode_revision(row: sqlite3.Row) -> dict[str, Any]:
        try:
            snapshot = json.loads(str(row["snapshot_json"]))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HistoryIntegrityError("history snapshot is invalid") from exc
        if not isinstance(snapshot, dict):
            raise HistoryIntegrityError("history snapshot is invalid")
        observed = _sha256(_canonical_json(snapshot))
        if observed != str(row["content_sha256"]):
            raise HistoryIntegrityError("history snapshot hash mismatch")
        return snapshot

    @staticmethod
    def _revision_on_connection(
        con: sqlite3.Connection, document_id: str, revision: int
    ) -> sqlite3.Row:
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("positive revision required")
        row = con.execute(
            "SELECT * FROM workspace_collaboration_revisions "
            "WHERE document_id=? AND revision=?",
            (document_id, revision),
        ).fetchone()
        if row is None:
            raise NotFoundOrDenied("not found or permission denied")
        return row

    def retention_policy(self) -> dict[str, Any]:
        return {
            "contract": HISTORY_AUTHORITY_CONTRACT,
            "user_visible_revision_retention": "indefinite_no_automatic_deletion",
            "raw_crdt_compaction_requires": [
                "verified_snapshot_high_water",
                "verified_backup_generation",
                "verified_replay_equivalence",
            ],
            "raw_crdt_pruning_enabled": False,
            "committed_attachment_blobs_retained_for_history": True,
            "plan_entitlement_limits": False,
        }

    def list_history(
        self,
        subject_id: str,
        document_id: str,
        *,
        before_revision: int | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        subject = self._require_active(subject_id)
        document = _identifier(document_id, field="document_id")
        page_size = max(1, min(int(limit), MAX_HISTORY_PAGE_SIZE))
        if before_revision is not None and (
            isinstance(before_revision, bool)
            or not isinstance(before_revision, int)
            or before_revision < 1
        ):
            raise ValueError("positive before_revision required")
        with self._reader() as con:
            workspace, current, role = self._authorized_document(con, subject, document)
            parameters: list[Any] = [document]
            boundary = ""
            if before_revision is not None:
                boundary = "AND revision.revision<? "
                parameters.append(before_revision)
            parameters.append(page_size + 1)
            rows = con.execute(
                "SELECT revision.document_id,revision.revision,revision.document_sequence,"
                "revision.content_sha256,revision.actor_subject_id,revision.operation_id,"
                "revision.created_at,length(CAST(revision.snapshot_json AS BLOB)) AS snapshot_size,"
                "checkpoint.id AS checkpoint_id,checkpoint.checkpoint_reason,"
                "checkpoint.publication_revision_id,restore.source_revision AS restored_from_revision "
                "FROM workspace_collaboration_revisions revision "
                "LEFT JOIN workspace_collaboration_revision_checkpoints checkpoint "
                "ON checkpoint.document_id=revision.document_id "
                "AND checkpoint.revision=revision.revision "
                "LEFT JOIN workspace_collaboration_history_restores restore "
                "ON restore.document_id=revision.document_id "
                "AND restore.result_revision=revision.revision "
                "WHERE revision.document_id=? " + boundary +
                "ORDER BY revision.revision DESC LIMIT ?",
                tuple(parameters),
            ).fetchall()
            has_more = len(rows) > page_size
            visible = rows[:page_size]
            items = [dict(row) for row in visible]
            return {
                "contract": HISTORY_AUTHORITY_CONTRACT,
                "workspace_id": str(workspace["id"]),
                "document_id": document,
                "current_revision": int(current["revision"]),
                "effective_role": role,
                "items": items,
                "count": len(items),
                "has_more": has_more,
                "next_before_revision": (
                    int(visible[-1]["revision"]) if has_more and visible else None
                ),
            }

    def list_update_previews(
        self, subject_id: str, document_id: str, *,
        before_revision: int | None = None, limit: int = 20,
    ) -> dict[str, Any]:
        """Authorized, paged edit summaries without sending full snapshots."""
        subject = self._require_active(subject_id)
        document = _identifier(document_id, field="document_id")
        if before_revision is not None and (
            isinstance(before_revision, bool) or not isinstance(before_revision, int)
            or before_revision < 1
        ):
            raise ValueError("positive before_revision required")
        page_size = max(1, min(int(limit), 30))
        with self._reader() as con:
            workspace, _current, _role = self._authorized_document(con, subject, document)
            params: list[Any] = [document]
            boundary = ""
            if before_revision is not None:
                boundary = "AND revision.revision<? "
                params.append(before_revision)
            params.append(page_size + 1)
            rows = con.execute(
                "SELECT revision.revision,revision.actor_subject_id,revision.created_at,"
                "revision.content_sha256,"
                "CASE WHEN length(CAST(revision.snapshot_json AS BLOB))<=2097152 "
                "THEN revision.snapshot_json END AS snapshot_json,"
                "restore.source_revision AS restored_from_revision "
                "FROM workspace_collaboration_revisions revision "
                "LEFT JOIN workspace_collaboration_history_restores restore "
                "ON restore.document_id=revision.document_id "
                "AND restore.result_revision=revision.revision "
                "WHERE revision.document_id=? " + boundary +
                "ORDER BY revision.revision DESC LIMIT ?",
                tuple(params),
            ).fetchall()
            self._authorized_document(con, subject, document)

        def snapshot(row):
            return self._decode_revision(row) if row is not None and row["snapshot_json"] is not None else None

        visible = rows[:page_size]
        items = []
        for index, row in enumerate(visible):
            older = rows[index + 1] if index + 1 < len(rows) else None
            after = snapshot(row)
            before = snapshot(older) if older is not None else None
            items.append({
                "revision": int(row["revision"]),
                "actor_subject_id": str(row["actor_subject_id"]),
                "created_at": int(row["created_at"]),
                "restored_from_revision": row["restored_from_revision"],
                "preview": page_change_preview(before, after) if after is not None
                    and (older is None or before is not None) else None,
            })
        has_more = len(rows) > page_size
        return {
            "workspace_id": str(workspace["id"]), "document_id": document,
            "items": items, "has_more": has_more,
            "next_before_revision": int(visible[-1]["revision"]) if has_more and visible else None,
        }

    def get_revision(
        self, subject_id: str, document_id: str, revision: int
    ) -> dict[str, Any]:
        subject = self._require_active(subject_id)
        document = _identifier(document_id, field="document_id")
        with self._reader() as con:
            workspace, current, role = self._authorized_document(con, subject, document)
            row = self._revision_on_connection(con, document, revision)
            snapshot = self._decode_revision(row)
            return {
                "contract": HISTORY_AUTHORITY_CONTRACT,
                "workspace_id": str(workspace["id"]),
                "document_id": document,
                "revision": int(row["revision"]),
                "document_sequence": int(row["document_sequence"]),
                "content_sha256": str(row["content_sha256"]),
                "actor_subject_id": str(row["actor_subject_id"]),
                "created_at": int(row["created_at"]),
                "current": int(current["revision"]) == int(row["revision"]),
                "effective_role": role,
                "snapshot": snapshot,
            }

    def diff_revisions(
        self,
        subject_id: str,
        document_id: str,
        *,
        from_revision: int,
        to_revision: int,
    ) -> dict[str, Any]:
        first = self.get_revision(subject_id, document_id, from_revision)
        second = self.get_revision(subject_id, document_id, to_revision)
        before_lines = json.dumps(
            first["snapshot"], ensure_ascii=False, sort_keys=True, indent=2
        ).splitlines()
        after_lines = json.dumps(
            second["snapshot"], ensure_ascii=False, sort_keys=True, indent=2
        ).splitlines()
        full = list(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=f"revision-{from_revision}",
                tofile=f"revision-{to_revision}",
                lineterm="",
            )
        )
        lines = full[:MAX_DIFF_LINES]
        return {
            "contract": HISTORY_AUTHORITY_CONTRACT,
            "document_id": _identifier(document_id, field="document_id"),
            "from_revision": from_revision,
            "to_revision": to_revision,
            "from_content_sha256": first["content_sha256"],
            "to_content_sha256": second["content_sha256"],
            "changed": first["content_sha256"] != second["content_sha256"],
            "lines": lines,
            "line_count": len(lines),
            "truncated": len(full) > len(lines),
        }

    def _replace_core_snapshot(
        self,
        con: sqlite3.Connection,
        document: sqlite3.Row,
        snapshot: dict[str, Any],
        *,
        actor: str,
        now: int,
    ) -> None:
        # Durable Yjs titles may be empty or contain intentional edge spaces.
        # A restore must retain the exact recorded string, not normalize it.
        title = snapshot.get("title")
        if not isinstance(title, str) or len(title) > 1000:
            raise HistoryIntegrityError("history title is invalid")
        settings = snapshot.get("settings", {})
        if not isinstance(settings, dict):
            raise HistoryIntegrityError("history settings are invalid")
        blocks = _clean_blocks(snapshot.get("blocks", []))
        if database_row_content.installed(con):
            current_databases = self._editing_snapshot(con, document)["databases"]
            try:
                database_row_content.restore_bodies(con, current_databases,
                    snapshot.get("databases", []), now=now)
            except (ValueError, KeyError, TypeError) as exc:
                raise HistoryIntegrityError(str(exc)) from exc
        document_id = str(document["id"])
        con.execute(
            "UPDATE workspace_collaboration_documents SET title=?,icon=?,cover=?,"
            "settings_json=? WHERE id=?",
            (
                title,
                str(snapshot.get("icon") or ""),
                str(snapshot.get("cover") or ""),
                _canonical_json(settings),
                document_id,
            ),
        )
        con.execute(
            "DELETE FROM workspace_collaboration_blocks WHERE document_id=?",
            (document_id,),
        )
        CollaborativeHistoryAuthority._insert_blocks(con, document_id, blocks, now=now)

        desired_files = snapshot.get("files", [])
        if not isinstance(desired_files, list):
            raise HistoryIntegrityError("history files are invalid")
        desired_ids = {
            _identifier(str(item.get("id") or ""), field="history_file_id")
            for item in desired_files
            if isinstance(item, dict)
        }
        if len(desired_ids) != len(desired_files):
            raise HistoryIntegrityError("history file identities are invalid")
        file_rows = con.execute(
            "SELECT * FROM workspace_collaboration_files WHERE document_id=?",
            (document_id,),
        ).fetchall()
        by_id = {str(row["id"]): row for row in file_rows}
        if not desired_ids.issubset(by_id):
            raise HistoryIntegrityError("history attachment metadata is unavailable")
        for file_id in desired_ids:
            row = by_id[file_id]
            blob = self.storage_root / str(row["blob_relative_path"])
            if not blob.is_file():
                raise HistoryIntegrityError("history attachment blob is unavailable")
        for file_id, row in by_id.items():
            state = str(row["state"])
            if file_id in desired_ids and state == "deleted":
                con.execute(
                    "UPDATE workspace_collaboration_files SET state='active',"
                    "row_version=row_version+1,updated_at=?,deleted_at=NULL,"
                    "deleted_by_subject_id=NULL WHERE id=?",
                    (now, file_id),
                )
            elif file_id not in desired_ids and state == "active":
                con.execute(
                    "UPDATE workspace_collaboration_files SET state='deleted',"
                    "row_version=row_version+1,updated_at=?,deleted_at=?,"
                    "deleted_by_subject_id=? WHERE id=?",
                    (now, now, actor, file_id),
                )

    def restore_revision(
        self,
        actor_subject_id: str,
        document_id: str,
        revision: int,
        *,
        expected_revision: int,
        expected_document_sequence: int,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
    ) -> dict[str, Any]:
        actor = _identifier(actor_subject_id, field="actor_subject_id")
        document = _identifier(document_id, field="document_id")
        source_revision = int(revision)
        if source_revision < 1:
            raise ValueError("positive revision required")
        operation = _operation_id(operation_id)
        action = "collaboration.history_revision_restored"
        request = {
            "document_id": document,
            "source_revision": source_revision,
            "expected_revision": int(expected_revision),
            "expected_document_sequence": int(expected_document_sequence),
            "expected_workspace_epoch": int(expected_workspace_epoch),
            "expected_document_epoch": int(expected_document_epoch),
        }
        request_sha256 = _request_sha256(actor, action, request)
        with self._transaction() as con:
            def authorize_replay() -> None:
                _workspace, _document, current_role = self._authorized_document(
                    con, actor, document
                )
                if PAGE_ROLE_LEVEL[current_role] < PAGE_ROLE_LEVEL["edit_content"]:
                    raise NotFoundOrDenied("not found or permission denied")

            replay = self._replay(
                con,
                actor=actor,
                action=action,
                operation_id=operation,
                request_sha256=request_sha256,
                authorize=authorize_replay,
            )
            if replay is not None:
                return replay
            workspace, current, role = self._exact_access(
                con,
                actor,
                document,
                minimum_role="edit_content",
                expected_workspace_epoch=int(expected_workspace_epoch),
                expected_document_epoch=int(expected_document_epoch),
            )
            _require_unlocked_or_temporary(con, current, actor)
            if int(current["revision"]) != int(expected_revision):
                raise RevisionConflict(int(current["revision"]))
            if int(current["document_sequence"]) != int(expected_document_sequence):
                raise DocumentSequenceConflict(int(current["document_sequence"]))
            source_row = self._revision_on_connection(con, document, source_revision)
            source_snapshot = self._decode_revision(source_row)
            before_database_items = {}
            if database_activity.installed(con):
                before_database_items = {
                    database["id"]: {
                        row["id"]: (row.get("body"), row.get("appearance"))
                        for row in database["rows"]
                    }
                    for database in self._editing_snapshot(con, current)["databases"]
                }
            now = int(time.time())
            current_generation = self._generation(con, document)
            generation = int(current_generation["generation"])
            latest_snapshot = con.execute(
                "SELECT snapshot.* FROM workspace_collaboration_crdt_snapshots snapshot "
                "JOIN workspace_collaboration_crdt_snapshot_bindings binding "
                "ON binding.snapshot_id=snapshot.id WHERE binding.document_id=? "
                "AND binding.generation=? ORDER BY snapshot.through_update_sequence DESC LIMIT 1",
                (document, generation),
            ).fetchone()
            command_high_water = int(
                con.execute(
                    "SELECT COALESCE(MAX(command_sequence),0) "
                    "FROM workspace_collaboration_typed_commands WHERE document_id=?",
                    (document,),
                ).fetchone()[0]
            )
            pre_checkpoint = con.execute(
                "SELECT id FROM workspace_collaboration_revision_checkpoints "
                "WHERE document_id=? AND revision=?",
                (document, int(current["revision"])),
            ).fetchone()
            if pre_checkpoint is None:
                pre_checkpoint_id = _stable_id(
                    "hist", document, f"r{int(current['revision'])}", "pre_restore"
                )
                con.execute(
                    "INSERT INTO workspace_collaboration_revision_checkpoints("
                    "id,workspace_id,document_id,revision,document_sequence,crdt_generation,"
                    "crdt_snapshot_id,through_update_sequence,through_structure_command_sequence,"
                    "content_sha256,actor_subject_id,operation_id,checkpoint_reason,"
                    "publication_revision_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        pre_checkpoint_id,
                        str(workspace["id"]),
                        document,
                        int(current["revision"]),
                        int(current["document_sequence"]),
                        generation,
                        None if latest_snapshot is None else str(latest_snapshot["id"]),
                        0 if latest_snapshot is None else int(latest_snapshot["through_update_sequence"]),
                        command_high_water,
                        str(current["content_sha256"]),
                        actor,
                        _stable_id("op", operation, "pre_restore"),
                        "pre_restore",
                        None,
                        now,
                    ),
                )

            self._replace_core_snapshot(con, current, source_snapshot, actor=actor, now=now)
            con.execute(
                "UPDATE workspace_collaboration_crdt_generations SET state='retired',"
                "retired_at=? WHERE document_id=? AND generation=?",
                (now, document, generation),
            )
            next_generation = generation + 1
            con.execute(
                "INSERT INTO workspace_collaboration_crdt_generations("
                "document_id,generation,yjs_document_key,state,created_at,retired_at) "
                "VALUES(?,?,?,'current',?,NULL)",
                (document, next_generation, f"document:{document}:rich-text:g{next_generation}", now),
            )
            next_revision = int(current["revision"]) + 1
            next_sequence = int(current["document_sequence"]) + 1
            next_document_epoch = int(current["access_epoch"]) + 1
            refreshed = self._document(con, document)
            restored_snapshot = self._editing_snapshot(con, refreshed)
            if database_activity.installed(con):
                changed_databases = [
                    database["id"] for database in restored_snapshot["databases"]
                    if before_database_items.get(database["id"]) != {
                        row["id"]: (row.get("body"), row.get("appearance"))
                        for row in database["rows"]
                    }
                ]
                database_activity.touch(
                    con, database_ids=changed_databases, document_id=document,
                    actor_subject_id=actor, edited_at=now, document_sequence=next_sequence,
                )
            result_content_sha256 = _sha256(_canonical_json(restored_snapshot))
            con.execute(
                "UPDATE workspace_collaboration_documents SET revision=?,document_sequence=?,"
                "access_epoch=?,content_sha256=?,row_version=row_version+1,updated_at=? "
                "WHERE id=?",
                (
                    next_revision,
                    next_sequence,
                    next_document_epoch,
                    result_content_sha256,
                    now,
                    document,
                ),
            )
            con.execute(
                "INSERT INTO workspace_collaboration_revisions("
                "document_id,revision,document_sequence,content_sha256,snapshot_json,"
                "actor_subject_id,operation_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    document,
                    next_revision,
                    next_sequence,
                    result_content_sha256,
                    _canonical_json(restored_snapshot),
                    actor,
                    operation,
                    now,
                ),
            )
            result_checkpoint_id = _stable_id(
                "hist", document, f"r{next_revision}", operation
            )
            con.execute(
                "INSERT INTO workspace_collaboration_revision_checkpoints("
                "id,workspace_id,document_id,revision,document_sequence,crdt_generation,"
                "crdt_snapshot_id,through_update_sequence,through_structure_command_sequence,"
                "content_sha256,actor_subject_id,operation_id,checkpoint_reason,"
                "publication_revision_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    result_checkpoint_id,
                    str(workspace["id"]),
                    document,
                    next_revision,
                    next_sequence,
                    next_generation,
                    None,
                    0,
                    command_high_water,
                    result_content_sha256,
                    actor,
                    operation,
                    "restore",
                    None,
                    now,
                ),
            )
            source_checkpoint = con.execute(
                "SELECT id,publication_revision_id FROM workspace_collaboration_revision_checkpoints "
                "WHERE document_id=? AND revision=?",
                (document, source_revision),
            ).fetchone()
            restore_id = _stable_id("restore", document, str(next_revision), operation)
            con.execute(
                "INSERT INTO workspace_collaboration_history_restores("
                "id,workspace_id,document_id,source_checkpoint_id,source_revision,"
                "result_checkpoint_id,result_revision,actor_subject_id,operation_id,"
                "source_content_sha256,result_content_sha256,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    restore_id,
                    str(workspace["id"]),
                    document,
                    None if source_checkpoint is None else str(source_checkpoint["id"]),
                    source_revision,
                    result_checkpoint_id,
                    next_revision,
                    actor,
                    operation,
                    str(source_row["content_sha256"]),
                    result_content_sha256,
                    now,
                ),
            )
            result = {
                "contract": HISTORY_AUTHORITY_CONTRACT,
                "workspace_id": str(workspace["id"]),
                "document_id": document,
                "restored_from_revision": source_revision,
                "restored_from_content_sha256": str(source_row["content_sha256"]),
                "preserved_publication_revision_id": (
                    None
                    if source_checkpoint is None
                    else source_checkpoint["publication_revision_id"]
                ),
                "revision": next_revision,
                "document_sequence": next_sequence,
                "document_epoch": next_document_epoch,
                "crdt_generation": next_generation,
                "content_sha256": result_content_sha256,
                "checkpoint_id": result_checkpoint_id,
                "effective_role": role,
            }
            return self._finish(
                con,
                actor=actor,
                action=action,
                operation_id=operation,
                request_sha256=request_sha256,
                workspace_id=str(workspace["id"]),
                document_id=document,
                workspace_epoch=int(workspace["security_epoch"]),
                document_epoch=next_document_epoch,
                document_sequence=next_sequence,
                event_type="history.revision.restored",
                event_payload=result,
                result=result,
                fault=None,
            )


__all__ = [
    "CollaborativeHistoryAuthority",
    "HISTORY_AUTHORITY_CONTRACT",
    "HistoryIntegrityError",
]
