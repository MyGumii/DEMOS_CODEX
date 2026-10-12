"""C0-P1 canonical live-collaboration authority.

Comments, reactions, mentions and inbox notifications are durable canonical
state.  Every command commits its domain change, event, audit record, outbox
record and idempotency result in one SQLite transaction.  Presence deliberately
does not appear here; the runtime gateway owns it as expiring memory only.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterable, Mapping, Sequence

from code_assist_v1 import notion_workspace_healing_migration as migration_core
from code_assist_v1 import notion_workspace_live_collaboration_migration as migration
from code_assist_v1.notion_collaboration.canonical_authority import (
    AuthorityUnavailable,
    NotFoundOrDenied,
    _canonical_json,
    _identifier,
    _operation_id,
    _positive,
    _request_sha256,
    _stable_id,
)
from code_assist_v1.notion_collaboration.iam_authority import (
    CollaborativeIAMAuthority,
    InMemoryIdentityDirectory,
    PAGE_ROLE_LEVEL,
)


LIVE_AUTHORITY_CONTRACT = "code_assist.notion.live_collaboration_authority.v1"
LIVE_EVENT_TYPES = (
    "database.surface.committed",
    "database.schema.committed",
    "page.appearance.updated",
    "page.settings.updated",
    "page.archived",
    "page.restored",
    "page.moved",
    "comment.added",
    "comment.updated",
    "comment.deleted",
    "comment.resolved",
    "comment.reopened",
    "comment.reaction_added",
    "comment.reaction_removed",
)
MAX_COMMENT_CHARS = 20_000
MAX_MENTIONS = 100
MAX_REACTION_CHARS = 32


class CommentRevisionConflict(RuntimeError):
    """The caller edited a comment version that is no longer current."""

    def __init__(self, current_row_version: int) -> None:
        self.current_row_version = int(current_row_version)
        super().__init__("comment row version conflict")


@dataclass(frozen=True, slots=True)
class LiveDomainEvent:
    event_id: str
    workspace_id: str
    document_id: str
    document_sequence: int
    event_type: str
    actor_subject_id: str
    operation_id: str
    workspace_epoch: int
    document_epoch: int
    payload: Mapping[str, Any]

    def public_document(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "workspace_id": self.workspace_id,
            "document_id": self.document_id,
            "document_sequence": self.document_sequence,
            "event_type": self.event_type,
            "actor_subject_id": self.actor_subject_id,
            "operation_id": self.operation_id,
            "workspace_epoch": self.workspace_epoch,
            "document_epoch": self.document_epoch,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class LiveDomainReplayBatch:
    after_document_sequence: int
    next_document_sequence: int
    high_water_sequence: int
    events: tuple[LiveDomainEvent, ...]
    has_more: bool


def replay_live_domain_events_on_connection(
    con: sqlite3.Connection,
    *,
    workspace: sqlite3.Row,
    document: sqlite3.Row,
    document_id: str,
    after_document_sequence: int,
    limit: int = 100,
) -> LiveDomainReplayBatch:
    """Replay authorized live-domain events using an existing read connection."""

    after = int(after_document_sequence)
    batch_limit = int(limit)
    if after < 0 or batch_limit < 1 or batch_limit > 200:
        raise ValueError("live replay cursor or limit is invalid")
    high_water = int(document["document_sequence"])
    if after > high_water:
        raise ValueError("live replay cursor exceeds high-water mark")
    placeholders = ",".join("?" for _ in LIVE_EVENT_TYPES)
    rows = con.execute(
        f"SELECT * FROM workspace_collaboration_events WHERE document_id=? "
        f"AND document_sequence>? AND event_type IN ({placeholders}) "
        "ORDER BY document_sequence LIMIT ?",
        (document_id, after, *LIVE_EVENT_TYPES, batch_limit + 1),
    ).fetchall()
    selected = rows[:batch_limit]
    events = tuple(
        LiveDomainEvent(
            event_id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            document_id=document_id,
            document_sequence=int(row["document_sequence"]),
            event_type=str(row["event_type"]),
            actor_subject_id=str(row["actor_subject_id"]),
            operation_id=str(row["operation_id"]),
            workspace_epoch=int(workspace["security_epoch"]),
            document_epoch=int(document["access_epoch"]),
            payload=_json_object(str(row["payload_json"]), field="live event payload"),
        )
        for row in selected
    )
    next_cursor = (
        events[-1].document_sequence
        if len(rows) > batch_limit and events
        else high_water
    )
    return LiveDomainReplayBatch(
        after,
        next_cursor,
        high_water,
        events,
        len(rows) > batch_limit,
    )


def _comment_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("comment content required")
    if len(text) > MAX_COMMENT_CHARS:
        raise ValueError("comment content is too long")
    return text


def _reaction(value: str) -> str:
    emoji = str(value or "").strip()
    if not emoji or len(emoji) > MAX_REACTION_CHARS:
        raise ValueError("valid reaction required")
    return emoji


def _json_object(value: str, *, field: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AuthorityUnavailable(f"stored {field} is invalid") from exc
    if not isinstance(parsed, dict):
        raise AuthorityUnavailable(f"stored {field} is invalid")
    return parsed


class CollaborativeLiveAuthority(CollaborativeIAMAuthority):
    """Exact 2404 IAM and live-collaboration authority."""

    @classmethod
    def open(
        cls,
        catalog_path: str | os.PathLike[str],
        user_data_root: str | os.PathLike[str],
        identity_directory: InMemoryIdentityDirectory,
    ) -> "CollaborativeLiveAuthority":
        try:
            catalog, root = migration_core._resolved_target(catalog_path, user_data_root)
            con = sqlite3.connect(f"{catalog.as_uri()}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            try:
                con.execute("PRAGMA query_only=ON")
                if migration_core._inspect(con, migration._contract())["state"] != "already_current":
                    raise AuthorityUnavailable("live collaboration schema is not current")
            finally:
                con.close()
        except AuthorityUnavailable:
            raise
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            raise AuthorityUnavailable("live collaboration authority is unavailable") from exc
        return cls(catalog, root, identity_directory)

    def _connect(self, *, query_only: bool) -> sqlite3.Connection:
        try:
            if query_only:
                con = sqlite3.connect(
                    f"{self.catalog_path.as_uri()}?mode=ro",
                    uri=True,
                    timeout=5.0,
                    isolation_level=None,
                )
            else:
                con = sqlite3.connect(self.catalog_path, timeout=5.0, isolation_level=None)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=5000")
            if query_only:
                con.execute("PRAGMA query_only=ON")
            marker = con.execute(
                "SELECT name,checksum,actual_schema_sha256 "
                "FROM workspace_schema_migrations WHERE version=?",
                (migration.TARGET_VERSION,),
            ).fetchone()
            latest = con.execute("SELECT MAX(version) FROM workspace_schema_migrations").fetchone()
            if marker is None or tuple(marker) != (
                migration.TARGET_NAME,
                migration.TARGET_CHECKSUM,
                migration.TARGET_ACTUAL_SCHEMA_SHA256,
            ) or latest is None or int(latest[0]) != migration.TARGET_VERSION:
                con.close()
                raise AuthorityUnavailable("live collaboration schema marker mismatch")
            return con
        except AuthorityUnavailable:
            raise
        except sqlite3.Error as exc:
            raise AuthorityUnavailable("live collaboration authority is unavailable") from exc

    @staticmethod
    def _comment(con: sqlite3.Connection, document_id: str, comment_id: str, *, include_deleted: bool = False) -> sqlite3.Row:
        row = con.execute(
            "SELECT * FROM workspace_collaboration_comments "
            "WHERE id=? AND document_id=? AND (deleted_at IS NULL OR ?)",
            (comment_id, document_id, include_deleted),
        ).fetchone()
        if row is None:
            raise NotFoundOrDenied("not found or permission denied")
        from . import database_row_trash, database_trash
        if str(row["id"]) in (database_trash.hidden_comment_ids(con, document_id)
                | database_row_trash.hidden_comment_ids(con, document_id)):
            raise NotFoundOrDenied("not found or permission denied")
        from .database_row_comments import require_binding
        require_binding(con, document_id, _json_object(str(row["anchor_json"]), field="comment anchor"), block_id=row["block_id"])
        return row

    @classmethod
    def _exact_role(
        cls,
        con: sqlite3.Connection,
        subject_id: str,
        workspace_id: str,
        document_id: str,
    ) -> str:
        role, _sources = cls._resolution_on_connection(
            con, subject_id, workspace_id, document_id
        )
        if role is None:
            raise NotFoundOrDenied("not found or permission denied")
        return role

    @classmethod
    def _require_comment_owner_or_manager(
        cls,
        con: sqlite3.Connection,
        actor: str,
        workspace_id: str,
        document_id: str,
        comment_row: sqlite3.Row,
    ) -> None:
        if str(comment_row["author_subject_id"]) == actor:
            return
        if cls._exact_role(con, actor, workspace_id, document_id) != "full_access":
            raise NotFoundOrDenied("not found or permission denied")

    def _mention_subjects(
        self,
        con: sqlite3.Connection,
        values: Sequence[str] | None,
        *,
        document_id: str,
        workspace_id: str,
    ) -> tuple[str, ...]:
        raw = list(values or ())
        if len(raw) > MAX_MENTIONS:
            raise ValueError("too many mentions")
        subjects = tuple(sorted({_identifier(value, field="mentioned_subject_id") for value in raw}))
        for subject in subjects:
            self._require_active(subject)
            workspace, _page, _role = self._access(
                con, subject, document_id, minimum_role="view"
            )
            if str(workspace["id"]) != workspace_id:
                raise NotFoundOrDenied("not found or permission denied")
        return subjects

    @staticmethod
    def _insert_mentions(
        con: sqlite3.Connection,
        comment_id: str,
        subjects: Iterable[str],
        *,
        now: int,
    ) -> None:
        con.executemany(
            "INSERT INTO workspace_collaboration_comment_mentions("
            "comment_id,mentioned_subject_id,created_at) VALUES(?,?,?)",
            ((comment_id, subject, now) for subject in subjects),
        )

    @staticmethod
    def _insert_notifications(
        con: sqlite3.Connection,
        *,
        actor: str,
        workspace_id: str,
        document_id: str,
        comment_id: str,
        source_event_id: str,
        recipients: Mapping[str, str],
        workspace_epoch: int,
        document_epoch: int,
        now: int,
    ) -> None:
        for recipient, kind in sorted(recipients.items()):
            if recipient == actor:
                continue
            notification_id = _stable_id("cnot", recipient, source_event_id)
            payload = {
                "notification_id": notification_id,
                "kind": kind,
                "workspace_id": workspace_id,
                "document_id": document_id,
                "comment_id": comment_id,
                "actor_subject_id": actor,
            }
            con.execute(
                "INSERT INTO workspace_collaboration_notifications("
                "id,recipient_subject_id,workspace_id,document_id,comment_id,kind,"
                "actor_subject_id,source_event_id,payload_json,workspace_epoch,"
                "document_epoch,notification_epoch,read_at,redacted_at,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,1,NULL,NULL,?,?)",
                (
                    notification_id,
                    recipient,
                    workspace_id,
                    document_id,
                    comment_id,
                    kind,
                    actor,
                    source_event_id,
                    _canonical_json(payload),
                    workspace_epoch,
                    document_epoch,
                    now,
                    now,
                ),
            )

    @staticmethod
    def _advance_document(
        con: sqlite3.Connection, document_row: sqlite3.Row, *, now: int
    ) -> int:
        sequence = int(document_row["document_sequence"]) + 1
        con.execute(
            "UPDATE workspace_collaboration_documents SET document_sequence=?,"
            "row_version=row_version+1,updated_at=? WHERE id=?",
            (sequence, now, str(document_row["id"])),
        )
        return sequence

    def list_comments(
        self,
        subject_id: str,
        document_id: str,
        *,
        expected_workspace_epoch: int | None = None,
        expected_document_epoch: int | None = None,
        item_target: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .database_row_comments import require_binding, target
        selected_target = target(dict(item_target)) if item_target is not None else None
        if item_target is not None and selected_target is None:
            raise ValueError("invalid database item comment target")
        subject = self._require_active(subject_id)
        document = _identifier(document_id, field="document_id")
        with self._reader() as con:
            con.execute("BEGIN")
            workspace, page, role = self._access(
                con,
                subject,
                document,
                minimum_role="view",
                expected_workspace_epoch=expected_workspace_epoch,
                expected_document_epoch=expected_document_epoch,
            )
            if selected_target is not None:
                require_binding(con, document, selected_target)
            rows = con.execute(
                "SELECT c.*,COALESCE((SELECT json_group_array(m.mentioned_subject_id) "
                "FROM workspace_collaboration_comment_mentions m "
                "WHERE m.comment_id=c.id),'[]') AS mention_ids_json "
                "FROM workspace_collaboration_comments c "
                "WHERE c.document_id=? AND c.deleted_at IS NULL "
                "ORDER BY c.created_at,c.id",
                (document,),
            ).fetchall()
            from . import database_row_trash, database_trash
            hidden_comments = (database_trash.hidden_comment_ids(con, document)
                | database_row_trash.hidden_comment_ids(con, document))
            items: list[dict[str, Any]] = []
            for row in rows:
                if str(row["id"]) in hidden_comments:
                    continue
                anchor = _json_object(str(row["anchor_json"]), field="comment anchor")
                if target(anchor) != selected_target:
                    continue
                mentions = json.loads(str(row["mention_ids_json"]))
                items.append(
                    {
                        "id": str(row["id"]),
                        "document_id": document,
                        "block_id": row["block_id"],
                        "author_subject_id": str(row["author_subject_id"]),
                        "content": str(row["content"]),
                        "anchor": anchor,
                        "parent_comment_id": row["parent_comment_id"],
                        "reactions": _json_object(str(row["reactions_json"]), field="comment reactions"),
                        "mention_subject_ids": mentions,
                        "resolved": bool(row["resolved"]),
                        "resolved_by_subject_id": row["resolved_by_subject_id"],
                        "resolved_at": row["resolved_at"],
                        "row_version": int(row["row_version"]),
                        "created_at": int(row["created_at"]),
                        "updated_at": int(row["updated_at"]),
                    }
                )
            return {
                "contract": LIVE_AUTHORITY_CONTRACT,
                "workspace_id": str(workspace["id"]),
                "document_id": document,
                "role": role,
                **({"item_target": selected_target} if selected_target else {}),
                "workspace_epoch": int(workspace["security_epoch"]),
                "document_epoch": int(page["access_epoch"]),
                "document_sequence": int(page["document_sequence"]),
                "items": items,
                "count": len(items),
            }

    def add_comment(
        self,
        actor_subject_id: str,
        document_id: str,
        comment_id: str,
        *,
        content: str,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        block_id: str | None = None,
        parent_comment_id: str | None = None,
        anchor: Mapping[str, Any] | None = None,
        mention_subject_ids: Sequence[str] | None = None,
        fault: str | None = None,
        runtime_generation_id: str | None = None,
    ) -> dict[str, Any]:
        from .database_row_comments import require_binding, target
        actor = self._require_active(actor_subject_id)
        document = _identifier(document_id, field="document_id")
        comment = _identifier(comment_id, field="comment_id")
        block = None if block_id is None else _identifier(block_id, field="block_id")
        parent = None if parent_comment_id is None else _identifier(parent_comment_id, field="parent_comment_id")
        text = _comment_text(content)
        workspace_epoch = _positive(expected_workspace_epoch, field="workspace_epoch")
        document_epoch = _positive(expected_document_epoch, field="document_epoch")
        op_id = _operation_id(operation_id)
        action = "collaboration.comment_added"
        requested_mentions = tuple(sorted(set(mention_subject_ids or ())))
        request = {
            "document_id": document,
            "comment_id": comment,
            "block_id": block,
            "parent_comment_id": parent,
            "content": text,
            "anchor": dict(anchor or {}),
            "mention_subject_ids": list(requested_mentions),
            "expected_workspace_epoch": workspace_epoch,
            "expected_document_epoch": document_epoch,
        }
        request_hash = _request_sha256(actor, action, request)
        with self._transaction() as con:
            def authorize():
                access = self._access(con, actor, document, minimum_role="comment",
                    expected_workspace_epoch=workspace_epoch, expected_document_epoch=document_epoch)
                item_target = require_binding(con, document, dict(anchor or {}), block_id=block)
                if item_target is not None and con.execute(
                    'SELECT 1 FROM workspace_collaboration_comments WHERE id=? AND deleted_at IS NOT NULL', (comment,)
                ).fetchone():
                    raise NotFoundOrDenied("not found or permission denied")
                return access
            replay = self._replay(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=authorize,
            )
            if replay is not None:
                return replay
            workspace, page, _role = authorize()
            workspace_id = str(workspace["id"])
            mentions = self._mention_subjects(
                con,
                requested_mentions,
                document_id=document,
                workspace_id=workspace_id,
            )
            parent_row = None if parent is None else self._comment(con, document, parent)
            if parent_row is not None and target(dict(anchor or {})) != target(
                _json_object(str(parent_row["anchor_json"]), field="comment anchor")
            ):
                raise NotFoundOrDenied("not found or permission denied")
            if block is not None and con.execute(
                "SELECT 1 FROM workspace_collaboration_blocks WHERE id=? AND document_id=?",
                (block, document),
            ).fetchone() is None:
                raise NotFoundOrDenied("not found or permission denied")
            now = int(time.time())
            sequence = self._advance_document(con, page, now=now)
            con.execute(
                "INSERT INTO workspace_collaboration_comments("
                "id,document_id,block_id,author_subject_id,content,anchor_json,"
                "parent_comment_id,reactions_json,resolved,row_version,created_at,updated_at,"
                "deleted_at,resolved_by_subject_id,resolved_at) "
                "VALUES(?,?,?,?,?,?,?,'{}',0,1,?,?,NULL,NULL,NULL)",
                (comment, document, block, actor, text, _canonical_json(dict(anchor or {})), parent, now, now),
            )
            self._insert_mentions(con, comment, mentions, now=now)
            event_id = _stable_id("cevt", actor, action, op_id)
            recipients: dict[str, str] = {}
            if parent_row is not None:
                recipients[str(parent_row["author_subject_id"])] = "comment_replied"
            recipients.update({subject: "comment_mentioned" for subject in mentions})
            self._insert_notifications(
                con,
                actor=actor,
                workspace_id=workspace_id,
                document_id=document,
                comment_id=comment,
                source_event_id=event_id,
                recipients=recipients,
                workspace_epoch=workspace_epoch,
                document_epoch=document_epoch,
                now=now,
            )
            result = {
                "contract": LIVE_AUTHORITY_CONTRACT,
                "workspace_id": workspace_id,
                "document_id": document,
                "comment_id": comment,
                "document_sequence": sequence,
                "workspace_epoch": workspace_epoch,
                "document_epoch": document_epoch,
                "notification_count": sum(recipient != actor for recipient in recipients),
            }
            return self._finish(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=workspace_id,
                document_id=document,
                workspace_epoch=workspace_epoch,
                document_epoch=document_epoch,
                document_sequence=sequence,
                event_type="comment.added",
                event_payload={
                    "workspace_id": workspace_id,
                    "document_id": document,
                    "comment_id": comment,
                    "parent_comment_id": parent,
                    "author_subject_id": actor,
                    "document_sequence": sequence,
                },
                result=result,
                fault=fault,
                runtime_generation_id=runtime_generation_id,
            )

    def update_comment(
        self,
        actor_subject_id: str,
        document_id: str,
        comment_id: str,
        *,
        content: str,
        mention_subject_ids: Sequence[str] | None,
        expected_row_version: int,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        fault: str | None = None,
    ) -> dict[str, Any]:
        return self._mutate_comment_content(
            actor_subject_id,
            document_id,
            comment_id,
            content=content,
            mention_subject_ids=mention_subject_ids,
            expected_row_version=expected_row_version,
            expected_workspace_epoch=expected_workspace_epoch,
            expected_document_epoch=expected_document_epoch,
            operation_id=operation_id,
            fault=fault,
        )

    def _mutate_comment_content(
        self,
        actor_subject_id: str,
        document_id: str,
        comment_id: str,
        *,
        content: str,
        mention_subject_ids: Sequence[str] | None,
        expected_row_version: int,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        fault: str | None,
    ) -> dict[str, Any]:
        actor = self._require_active(actor_subject_id)
        document = _identifier(document_id, field="document_id")
        comment = _identifier(comment_id, field="comment_id")
        text = _comment_text(content)
        row_version = _positive(expected_row_version, field="row_version")
        workspace_epoch = _positive(expected_workspace_epoch, field="workspace_epoch")
        document_epoch = _positive(expected_document_epoch, field="document_epoch")
        op_id = _operation_id(operation_id)
        action = "collaboration.comment_updated"
        requested_mentions = tuple(sorted(set(mention_subject_ids or ())))
        request = {
            "document_id": document,
            "comment_id": comment,
            "content": text,
            "mention_subject_ids": list(requested_mentions),
            "expected_row_version": row_version,
            "expected_workspace_epoch": workspace_epoch,
            "expected_document_epoch": document_epoch,
        }
        request_hash = _request_sha256(actor, action, request)
        with self._transaction() as con:
            def authorize() -> None:
                workspace, _page, _role = self._access(
                    con,
                    actor,
                    document,
                    minimum_role="comment",
                    expected_workspace_epoch=workspace_epoch,
                    expected_document_epoch=document_epoch,
                )
                row = self._comment(con, document, comment)
                self._require_comment_owner_or_manager(
                    con, actor, str(workspace["id"]), document, row
                )
            replay = self._replay(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=authorize,
            )
            if replay is not None:
                return replay
            workspace, page, _role = self._access(
                con,
                actor,
                document,
                minimum_role="comment",
                expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=document_epoch,
            )
            workspace_id = str(workspace["id"])
            row = self._comment(con, document, comment)
            self._require_comment_owner_or_manager(con, actor, workspace_id, document, row)
            if int(row["row_version"]) != row_version:
                raise CommentRevisionConflict(int(row["row_version"]))
            mentions = self._mention_subjects(
                con,
                requested_mentions,
                document_id=document,
                workspace_id=workspace_id,
            )
            now = int(time.time())
            sequence = self._advance_document(con, page, now=now)
            con.execute(
                "UPDATE workspace_collaboration_comments SET content=?,row_version=row_version+1,"
                "updated_at=? WHERE id=?",
                (text, now, comment),
            )
            con.execute(
                "DELETE FROM workspace_collaboration_comment_mentions WHERE comment_id=?",
                (comment,),
            )
            self._insert_mentions(con, comment, mentions, now=now)
            event_id = _stable_id("cevt", actor, action, op_id)
            recipients = {subject: "comment_mentioned" for subject in mentions}
            self._insert_notifications(
                con,
                actor=actor,
                workspace_id=workspace_id,
                document_id=document,
                comment_id=comment,
                source_event_id=event_id,
                recipients=recipients,
                workspace_epoch=workspace_epoch,
                document_epoch=document_epoch,
                now=now,
            )
            result = {
                "contract": LIVE_AUTHORITY_CONTRACT,
                "workspace_id": workspace_id,
                "document_id": document,
                "comment_id": comment,
                "row_version": row_version + 1,
                "document_sequence": sequence,
                "workspace_epoch": workspace_epoch,
                "document_epoch": document_epoch,
                "notification_count": sum(subject != actor for subject in mentions),
            }
            return self._finish(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=workspace_id,
                document_id=document,
                workspace_epoch=workspace_epoch,
                document_epoch=document_epoch,
                document_sequence=sequence,
                event_type="comment.updated",
                event_payload={
                    "workspace_id": workspace_id,
                    "document_id": document,
                    "comment_id": comment,
                    "actor_subject_id": actor,
                    "document_sequence": sequence,
                },
                result=result,
                fault=fault,
            )

    def set_comment_resolved(
        self,
        actor_subject_id: str,
        document_id: str,
        comment_id: str,
        *,
        resolved: bool,
        expected_row_version: int,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        fault: str | None = None,
    ) -> dict[str, Any]:
        actor = self._require_active(actor_subject_id)
        document = _identifier(document_id, field="document_id")
        comment = _identifier(comment_id, field="comment_id")
        state = resolved is True
        row_version = _positive(expected_row_version, field="row_version")
        workspace_epoch = _positive(expected_workspace_epoch, field="workspace_epoch")
        document_epoch = _positive(expected_document_epoch, field="document_epoch")
        op_id = _operation_id(operation_id)
        action = "collaboration.comment_resolved" if state else "collaboration.comment_reopened"
        request = {
            "document_id": document,
            "comment_id": comment,
            "resolved": state,
            "expected_row_version": row_version,
            "expected_workspace_epoch": workspace_epoch,
            "expected_document_epoch": document_epoch,
        }
        request_hash = _request_sha256(actor, action, request)
        with self._transaction() as con:
            def authorize():
                access = self._access(con, actor, document, minimum_role="comment",
                    expected_workspace_epoch=workspace_epoch, expected_document_epoch=document_epoch)
                self._comment(con, document, comment)
                return access
            replay = self._replay(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=authorize,
            )
            if replay is not None:
                return replay
            workspace, page, _role = authorize()
            row = self._comment(con, document, comment)
            if int(row["row_version"]) != row_version:
                raise CommentRevisionConflict(int(row["row_version"]))
            now = int(time.time())
            sequence = self._advance_document(con, page, now=now)
            con.execute(
                "UPDATE workspace_collaboration_comments SET resolved=?,"
                "resolved_by_subject_id=?,resolved_at=?,row_version=row_version+1,updated_at=? "
                "WHERE id=?",
                (int(state), actor if state else None, now if state else None, now, comment),
            )
            event_type = "comment.resolved" if state else "comment.reopened"
            result = {
                "contract": LIVE_AUTHORITY_CONTRACT,
                "workspace_id": str(workspace["id"]),
                "document_id": document,
                "comment_id": comment,
                "resolved": state,
                "row_version": row_version + 1,
                "document_sequence": sequence,
                "workspace_epoch": workspace_epoch,
                "document_epoch": document_epoch,
            }
            return self._finish(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=str(workspace["id"]),
                document_id=document,
                workspace_epoch=workspace_epoch,
                document_epoch=document_epoch,
                document_sequence=sequence,
                event_type=event_type,
                event_payload={
                    "workspace_id": str(workspace["id"]),
                    "document_id": document,
                    "comment_id": comment,
                    "resolved": state,
                    "actor_subject_id": actor,
                    "document_sequence": sequence,
                },
                result=result,
                fault=fault,
            )

    def delete_comment(
        self,
        actor_subject_id: str,
        document_id: str,
        comment_id: str,
        *,
        expected_row_version: int,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        fault: str | None = None,
    ) -> dict[str, Any]:
        actor = self._require_active(actor_subject_id)
        document = _identifier(document_id, field="document_id")
        comment = _identifier(comment_id, field="comment_id")
        row_version = _positive(expected_row_version, field="row_version")
        workspace_epoch = _positive(expected_workspace_epoch, field="workspace_epoch")
        document_epoch = _positive(expected_document_epoch, field="document_epoch")
        op_id = _operation_id(operation_id)
        action = "collaboration.comment_deleted"
        request = {
            "document_id": document,
            "comment_id": comment,
            "expected_row_version": row_version,
            "expected_workspace_epoch": workspace_epoch,
            "expected_document_epoch": document_epoch,
        }
        request_hash = _request_sha256(actor, action, request)
        with self._transaction() as con:
            def authorize() -> None:
                workspace, _page, _role = self._access(
                    con,
                    actor,
                    document,
                    minimum_role="comment",
                    expected_workspace_epoch=workspace_epoch,
                    expected_document_epoch=document_epoch,
                )
                self._require_comment_owner_or_manager(
                    con,
                    actor,
                    str(workspace["id"]),
                    document,
                    self._comment(con, document, comment, include_deleted=True),
                )
            replay = self._replay(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=authorize,
            )
            if replay is not None:
                return replay
            workspace, page, _role = self._access(
                con,
                actor,
                document,
                minimum_role="comment",
                expected_workspace_epoch=workspace_epoch,
                expected_document_epoch=document_epoch,
            )
            row = self._comment(con, document, comment)
            self._require_comment_owner_or_manager(
                con, actor, str(workspace["id"]), document, row
            )
            if int(row["row_version"]) != row_version:
                raise CommentRevisionConflict(int(row["row_version"]))
            now = int(time.time())
            sequence = self._advance_document(con, page, now=now)
            con.execute(
                "UPDATE workspace_collaboration_comments SET content='',reactions_json='{}',"
                "deleted_at=?,row_version=row_version+1,updated_at=? WHERE id=?",
                (now, now, comment),
            )
            con.execute(
                "DELETE FROM workspace_collaboration_comment_mentions WHERE comment_id=?",
                (comment,),
            )
            con.execute(
                "UPDATE workspace_collaboration_notifications SET "
                "redacted_at=COALESCE(redacted_at,?),notification_epoch=notification_epoch+1,"
                "updated_at=? WHERE comment_id=? AND redacted_at IS NULL",
                (now, now, comment),
            )
            result = {
                "contract": LIVE_AUTHORITY_CONTRACT,
                "workspace_id": str(workspace["id"]),
                "document_id": document,
                "comment_id": comment,
                "deleted": True,
                "document_sequence": sequence,
                "workspace_epoch": workspace_epoch,
                "document_epoch": document_epoch,
            }
            return self._finish(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=str(workspace["id"]),
                document_id=document,
                workspace_epoch=workspace_epoch,
                document_epoch=document_epoch,
                document_sequence=sequence,
                event_type="comment.deleted",
                event_payload={
                    "workspace_id": str(workspace["id"]),
                    "document_id": document,
                    "comment_id": comment,
                    "actor_subject_id": actor,
                    "document_sequence": sequence,
                },
                result=result,
                fault=fault,
            )

    def toggle_reaction(
        self,
        actor_subject_id: str,
        document_id: str,
        comment_id: str,
        *,
        emoji: str,
        expected_row_version: int,
        expected_workspace_epoch: int,
        expected_document_epoch: int,
        operation_id: str,
        fault: str | None = None,
    ) -> dict[str, Any]:
        actor = self._require_active(actor_subject_id)
        document = _identifier(document_id, field="document_id")
        comment = _identifier(comment_id, field="comment_id")
        reaction = _reaction(emoji)
        row_version = _positive(expected_row_version, field="row_version")
        workspace_epoch = _positive(expected_workspace_epoch, field="workspace_epoch")
        document_epoch = _positive(expected_document_epoch, field="document_epoch")
        op_id = _operation_id(operation_id)
        action = "collaboration.comment_reaction_toggled"
        request = {
            "document_id": document,
            "comment_id": comment,
            "emoji": reaction,
            "expected_row_version": row_version,
            "expected_workspace_epoch": workspace_epoch,
            "expected_document_epoch": document_epoch,
        }
        request_hash = _request_sha256(actor, action, request)
        with self._transaction() as con:
            def authorize():
                access = self._access(con, actor, document, minimum_role="comment",
                    expected_workspace_epoch=workspace_epoch, expected_document_epoch=document_epoch)
                self._comment(con, document, comment)
                return access
            replay = self._replay(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=authorize,
            )
            if replay is not None:
                return replay
            workspace, page, _role = authorize()
            row = self._comment(con, document, comment)
            if int(row["row_version"]) != row_version:
                raise CommentRevisionConflict(int(row["row_version"]))
            reactions = _json_object(str(row["reactions_json"]), field="comment reactions")
            members = reactions.get(reaction, [])
            if not isinstance(members, list) or not all(isinstance(item, str) for item in members):
                raise AuthorityUnavailable("stored comment reactions are invalid")
            normalized = set(members)
            added = actor not in normalized
            if added:
                normalized.add(actor)
            else:
                normalized.remove(actor)
            if normalized:
                reactions[reaction] = sorted(normalized)
            else:
                reactions.pop(reaction, None)
            now = int(time.time())
            sequence = self._advance_document(con, page, now=now)
            con.execute(
                "UPDATE workspace_collaboration_comments SET reactions_json=?,"
                "row_version=row_version+1,updated_at=? WHERE id=?",
                (_canonical_json(reactions), now, comment),
            )
            event_type = "comment.reaction_added" if added else "comment.reaction_removed"
            event_id = _stable_id("cevt", actor, action, op_id)
            recipients = (
                {str(row["author_subject_id"]): "comment_reacted"} if added else {}
            )
            self._insert_notifications(
                con,
                actor=actor,
                workspace_id=str(workspace["id"]),
                document_id=document,
                comment_id=comment,
                source_event_id=event_id,
                recipients=recipients,
                workspace_epoch=workspace_epoch,
                document_epoch=document_epoch,
                now=now,
            )
            result = {
                "contract": LIVE_AUTHORITY_CONTRACT,
                "workspace_id": str(workspace["id"]),
                "document_id": document,
                "comment_id": comment,
                "emoji": reaction,
                "added": added,
                "reactions": reactions,
                "row_version": row_version + 1,
                "document_sequence": sequence,
                "workspace_epoch": workspace_epoch,
                "document_epoch": document_epoch,
            }
            return self._finish(
                con,
                actor=actor,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=str(workspace["id"]),
                document_id=document,
                workspace_epoch=workspace_epoch,
                document_epoch=document_epoch,
                document_sequence=sequence,
                event_type=event_type,
                event_payload={
                    "workspace_id": str(workspace["id"]),
                    "document_id": document,
                    "comment_id": comment,
                    "emoji": reaction,
                    "added": added,
                    "actor_subject_id": actor,
                    "document_sequence": sequence,
                },
                result=result,
                fault=fault,
            )

    def list_inbox(
        self,
        subject_id: str,
        *,
        workspace_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        subject = self._require_active(subject_id)
        workspace_filter = (
            None
            if workspace_id is None
            else _identifier(workspace_id, field="workspace_id")
        )
        batch_limit = int(limit)
        if batch_limit < 1 or batch_limit > 200:
            raise ValueError("inbox limit is invalid")
        with self._reader() as con:
            con.execute("BEGIN")
            rows = con.execute(
                "SELECT n.*,c.document_id AS current_document_id "
                "FROM workspace_collaboration_notifications n "
                "JOIN workspace_collaboration_comments c ON c.id=n.comment_id "
                "WHERE n.recipient_subject_id=? AND n.redacted_at IS NULL "
                "AND c.deleted_at IS NULL AND (? IS NULL OR n.workspace_id=?) "
                "ORDER BY n.created_at DESC,n.id DESC LIMIT ?",
                (subject, workspace_filter, workspace_filter, batch_limit * 4),
            ).fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                if len(items) >= batch_limit:
                    break
                try:
                    workspace, page, _role = self._access(
                        con, subject, str(row["current_document_id"]), minimum_role="view"
                    )
                    comment_row = self._comment(con, str(row["current_document_id"]), str(row["comment_id"]))
                except NotFoundOrDenied:
                    continue
                if str(workspace["id"]) != str(row["workspace_id"]):
                    continue
                payload = _json_object(str(row["payload_json"]), field="notification payload")
                payload["document_id"] = str(row["current_document_id"])
                from .database_row_comments import target
                item_target = target(_json_object(str(comment_row["anchor_json"]), field="comment anchor"))
                items.append(
                    {
                        "id": str(row["id"]),
                        "kind": str(row["kind"]),
                        "workspace_id": str(row["workspace_id"]),
                        "document_id": str(row["current_document_id"]),
                        "comment_id": str(row["comment_id"]),
                        "actor_subject_id": str(row["actor_subject_id"]),
                        "payload": payload,
                        "workspace_epoch": int(workspace["security_epoch"]),
                        "document_epoch": int(page["access_epoch"]),
                        "notification_epoch": int(row["notification_epoch"]),
                        "read": row["read_at"] is not None,
                        "read_at": row["read_at"],
                        "created_at": int(row["created_at"]),
                        **({"item_target": item_target} if item_target else {}),
                    }
                )
            return {
                "contract": LIVE_AUTHORITY_CONTRACT,
                "items": items,
                "count": len(items),
                "unread_count": sum(not item["read"] for item in items),
            }

    def mark_notification_read(
        self,
        subject_id: str,
        notification_id: str,
        *,
        expected_workspace_id: str | None = None,
        operation_id: str,
        fault: str | None = None,
    ) -> dict[str, Any]:
        subject = self._require_active(subject_id)
        notification = _identifier(notification_id, field="notification_id")
        workspace_filter = (
            None
            if expected_workspace_id is None
            else _identifier(expected_workspace_id, field="workspace_id")
        )
        op_id = _operation_id(operation_id)
        action = "collaboration.notification_read"
        request = {"notification_id": notification}
        request_hash = _request_sha256(subject, action, request)
        with self._transaction() as con:
            row = con.execute(
                "SELECT n.*,c.document_id AS current_document_id "
                "FROM workspace_collaboration_notifications n "
                "JOIN workspace_collaboration_comments c ON c.id=n.comment_id "
                "WHERE n.id=? AND n.recipient_subject_id=? AND n.redacted_at IS NULL "
                "AND (? IS NULL OR n.workspace_id=?)",
                (notification, subject, workspace_filter, workspace_filter),
            ).fetchone()
            if row is None:
                raise NotFoundOrDenied("not found or permission denied")
            document = str(row["current_document_id"])
            def authorize() -> None:
                self._access(con, subject, document, minimum_role="view")
                self._comment(con, document, str(row["comment_id"]))
            replay = self._replay(
                con,
                actor=subject,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                authorize=authorize,
            )
            if replay is not None:
                return replay
            workspace, page, _role = self._access(
                con, subject, document, minimum_role="view"
            )
            self._comment(con, document, str(row["comment_id"]))
            now = int(time.time())
            con.execute(
                "UPDATE workspace_collaboration_notifications SET "
                "read_at=COALESCE(read_at,?),notification_epoch=notification_epoch+1,updated_at=? "
                "WHERE id=?",
                (now, now, notification),
            )
            result = {
                "contract": LIVE_AUTHORITY_CONTRACT,
                "notification_id": notification,
                "read": True,
                "workspace_id": str(workspace["id"]),
                "document_id": document,
                "workspace_epoch": int(workspace["security_epoch"]),
                "document_epoch": int(page["access_epoch"]),
            }
            return self._finish(
                con,
                actor=subject,
                action=action,
                operation_id=op_id,
                request_sha256=request_hash,
                workspace_id=str(workspace["id"]),
                document_id=document,
                workspace_epoch=int(workspace["security_epoch"]),
                document_epoch=int(page["access_epoch"]),
                document_sequence=None,
                event_type="notification.read",
                event_payload={
                    "notification_id": notification,
                    "recipient_subject_id": subject,
                    "workspace_id": str(workspace["id"]),
                    "document_id": document,
                },
                result=result,
                fault=fault,
            )

    def replay_domain_events(
        self,
        subject_id: str,
        document_id: str,
        *,
        after_document_sequence: int,
        limit: int = 100,
    ) -> LiveDomainReplayBatch:
        subject = self._require_active(subject_id)
        document = _identifier(document_id, field="document_id")
        with self._reader() as con:
            workspace, page, _role = self._access(
                con, subject, document, minimum_role="view"
            )
            return replay_live_domain_events_on_connection(
                con,
                workspace=workspace,
                document=page,
                document_id=document,
                after_document_sequence=after_document_sequence,
                limit=limit,
            )


__all__ = [
    "CommentRevisionConflict",
    "CollaborativeLiveAuthority",
    "LIVE_AUTHORITY_CONTRACT",
    "LIVE_EVENT_TYPES",
    "LiveDomainEvent",
    "LiveDomainReplayBatch",
    "replay_live_domain_events_on_connection",
]
