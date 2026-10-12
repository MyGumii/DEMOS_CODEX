"""Read-only legacy JSON-session views over canonical conversations.

This adapter exists only for a bounded UI compatibility window.  It never
opens a legacy session path, never writes JSON, and never falls back to a
second source of truth.  P2.5 remains the sole migration authority.  An old
session identifier is resolved by the SHA-256 evidence stored on its imported
canonical conversation, while all returned history comes from canonical rows.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import re

from ..db import ConnectionFactory
from ..repositories.conversations import (
    ConversationListEntry,
    ConversationRecord,
    ConversationRepository,
    ConversationScopeError,
)
from ..repositories.users import UserRepository


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)


class LegacySessionCompatibilityError(ValueError):
    code = "LEGACY_SESSION_COMPATIBILITY_ERROR"


class LegacySessionNotFoundError(LegacySessionCompatibilityError):
    code = "LEGACY_SESSION_NOT_FOUND"


class LegacySessionScopeError(LegacySessionCompatibilityError):
    code = "LEGACY_SESSION_SCOPE_INVALID"


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("actor_user_id must be a canonical user ID.")
    return value


def _epoch(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("actor_scope_epoch must be a positive integer.")
    return value


def _legacy_reference(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 255
        or "\x00" in value
        or "/" in value
        or "\\" in value
        or ".." in value
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ValueError("session_reference is invalid.")
    return value


def _text_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            part for part in (_text_value(item) for item in value) if part.strip()
        )
    if isinstance(value, Mapping):
        if "text" in value:
            return _text_value(value.get("text"))
        return " ".join(
            part for part in (_text_value(item) for item in value.values()) if part
        )
    return str(value)


def _legacy_metadata(
    conversation: ConversationRecord,
) -> tuple[dict[str, object], list[object], list[object], str | None]:
    metadata = conversation.metadata
    legacy = metadata.get("legacy_p2_5")
    if not isinstance(legacy, Mapping):
        return dict(metadata), [], [], None
    rows = legacy.get("metadata")
    metadata_value: dict[str, object] = {}
    skills: list[object] = []
    if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], Mapping):
        raw_metadata = rows[0].get("metadata")
        raw_skills = rows[0].get("skills_used")
        if isinstance(raw_metadata, Mapping):
            metadata_value = dict(raw_metadata)
        if isinstance(raw_skills, list):
            skills = list(raw_skills)
    uploads = legacy.get("uploads")
    safe_uploads = list(uploads) if isinstance(uploads, list) else []
    session_hash = legacy.get("session_id_sha256")
    if not isinstance(session_hash, str) or not re.fullmatch(
        r"[0-9a-f]{64}", session_hash
    ):
        session_hash = None
    return metadata_value, skills, safe_uploads, session_hash


class LegacyJsonSessionAdapter:
    """Project canonical rows into the former JSON-session response shape."""

    __slots__ = ("_conversations", "_factory", "_users")
    migration_authority = "offline_p2_5_only"
    reads_legacy_files = False
    writes_legacy_files = False

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        conversations: ConversationRepository | None = None,
        users: UserRepository | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("LegacyJsonSessionAdapter requires a ConnectionFactory.")
        self._factory = factory
        self._conversations = conversations or ConversationRepository()
        self._users = users or UserRepository()

    def _active_actor(self, connection, actor_user_id: str, epoch: int) -> None:
        actor = self._users.get(connection, actor_user_id)
        if actor is None or actor.account_status != "active":
            raise LegacySessionScopeError("An active canonical user is required.")
        if actor.scope_epoch != epoch:
            raise LegacySessionScopeError("The canonical session scope is stale.")

    def _resolve(
        self,
        connection,
        *,
        actor_user_id: str,
        session_reference: str,
    ) -> ConversationRecord:
        if _UUID_RE.fullmatch(session_reference) is not None:
            value = self._conversations.get(connection, session_reference)
            if value is None or value.owner_user_id != actor_user_id:
                raise LegacySessionNotFoundError("The session does not exist.")
            return value
        session_hash = sha256(session_reference.encode("utf-8")).hexdigest()
        rows = connection.execute(
            """SELECT id FROM conversation
               WHERE owner_user_id=?
                 AND json_extract(metadata_json,
                   '$.legacy_p2_5.session_id_sha256')=?
               ORDER BY id LIMIT 2""",
            (actor_user_id, session_hash),
        ).fetchall()
        if len(rows) != 1:
            raise LegacySessionNotFoundError("The session does not exist.")
        value = self._conversations.get(connection, str(rows[0]["id"]))
        if value is None or value.owner_user_id != actor_user_id:
            raise LegacySessionNotFoundError("The session does not exist.")
        return value

    def resolve_conversation_id(
        self,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
        session_reference: str,
    ) -> str:
        actor_id = _user(actor_user_id)
        epoch = _epoch(actor_scope_epoch)
        reference = _legacy_reference(session_reference)
        connection = self._factory.open()
        try:
            self._active_actor(connection, actor_id, epoch)
            conversation = self._resolve(
                connection,
                actor_user_id=actor_id,
                session_reference=reference,
            )
            try:
                self._conversations.require_accessible(
                    connection,
                    conversation_id=conversation.id,
                    owner_user_id=actor_id,
                )
            except ConversationScopeError as exc:
                raise LegacySessionScopeError(str(exc)) from exc
            return conversation.id
        finally:
            connection.close()

    def list_sessions(
        self,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
        include_archived: bool = True,
        limit: int = 100,
    ) -> tuple[dict[str, object], ...]:
        actor_id = _user(actor_user_id)
        epoch = _epoch(actor_scope_epoch)
        connection = self._factory.open()
        try:
            self._active_actor(connection, actor_id, epoch)
            entries = self._conversations.list_for_owner(
                connection,
                owner_user_id=actor_id,
                include_archived=include_archived,
                limit=limit,
            )
            return tuple(self._list_item(connection, entry) for entry in entries)
        finally:
            connection.close()

    def _list_item(
        self,
        connection,
        entry: ConversationListEntry,
    ) -> dict[str, object]:
        conversation = entry.conversation
        count_row = connection.execute(
            "SELECT count(*) AS n FROM conversation_message WHERE conversation_id=?",
            (conversation.id,),
        ).fetchone()
        first_row = connection.execute(
            """SELECT content_json FROM conversation_message
               WHERE conversation_id=? AND role='user'
               ORDER BY sequence_no LIMIT 1""",
            (conversation.id,),
        ).fetchone()
        first_message = ""
        if first_row is not None:
            try:
                parsed = json.loads(str(first_row["content_json"]))
            except (TypeError, ValueError):
                parsed = {}
            if isinstance(parsed, Mapping):
                first_message = _text_value(parsed.get("content")).strip()[:80]
        _metadata, skills, _uploads, session_hash = _legacy_metadata(conversation)
        return {
            "accessible": entry.accessible,
            "archived": conversation.status == "archived",
            "canonical": True,
            "first_message": first_message,
            "legacy_session_id_sha256": session_hash,
            "message_count": 0 if count_row is None else int(count_row["n"]),
            "session_id": conversation.id,
            "skills_used": skills,
            "space_id": conversation.space_id,
            "timestamp": conversation.updated_at_ms / 1000,
            "title": conversation.title,
        }

    def load_session(
        self,
        *,
        actor_user_id: str,
        actor_scope_epoch: int,
        session_reference: str,
        after_message_sequence_no: int = 0,
        limit: int = 100,
    ) -> dict[str, object]:
        actor_id = _user(actor_user_id)
        epoch = _epoch(actor_scope_epoch)
        reference = _legacy_reference(session_reference)
        if (
            isinstance(after_message_sequence_no, bool)
            or not isinstance(after_message_sequence_no, int)
            or after_message_sequence_no < 0
        ):
            raise ValueError(
                "after_message_sequence_no must be a non-negative integer."
            )
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer from 1 through 100.")
        connection = self._factory.open()
        try:
            self._active_actor(connection, actor_id, epoch)
            conversation = self._resolve(
                connection,
                actor_user_id=actor_id,
                session_reference=reference,
            )
            try:
                self._conversations.require_accessible(
                    connection,
                    conversation_id=conversation.id,
                    owner_user_id=actor_id,
                )
                messages = self._conversations.list_messages(
                    connection,
                    conversation_id=conversation.id,
                    owner_user_id=actor_id,
                    after_sequence_no=after_message_sequence_no,
                    limit=limit,
                )
                summary = self._conversations.get_active_summary(
                    connection,
                    conversation_id=conversation.id,
                    owner_user_id=actor_id,
                )
            except ConversationScopeError as exc:
                raise LegacySessionScopeError(str(exc)) from exc
            metadata, skills, uploads, session_hash = _legacy_metadata(conversation)
            total_row = connection.execute(
                "SELECT count(*) AS n FROM conversation_message WHERE conversation_id=?",
                (conversation.id,),
            ).fetchone()
            total = 0 if total_row is None else int(total_row["n"])
            rendered_messages: list[dict[str, object]] = []
            for row in messages:
                value = row.content
                value["role"] = row.role
                rendered_messages.append(value)
            first_row = connection.execute(
                """SELECT content_json FROM conversation_message
                   WHERE conversation_id=? AND role='user'
                   ORDER BY sequence_no LIMIT 1""",
                (conversation.id,),
            ).fetchone()
            first = ""
            if first_row is not None:
                try:
                    first_payload = json.loads(str(first_row["content_json"]))
                except (TypeError, ValueError):
                    first_payload = {}
                if isinstance(first_payload, Mapping):
                    first = _text_value(first_payload.get("content")).strip()[:80]
            return {
                "archived": conversation.status == "archived",
                "canonical": True,
                "compact_summary": "" if summary is None else summary.summary_md,
                "first_message": first,
                "legacy_session_id_sha256": session_hash,
                "has_more": bool(messages) and messages[-1].sequence_no < total,
                "message_count": total,
                "messages": rendered_messages,
                "metadata": metadata,
                "session_id": conversation.id,
                "skills_used": skills,
                "space_id": conversation.space_id,
                "summary_status": "absent" if summary is None else "ready",
                "next_message_sequence": (
                    after_message_sequence_no
                    if not messages
                    else messages[-1].sequence_no
                ),
                "timestamp": conversation.updated_at_ms / 1000,
                "title": conversation.title,
                "uploaded_files": uploads,
            }
        finally:
            connection.close()


__all__ = [
    "LegacyJsonSessionAdapter",
    "LegacySessionCompatibilityError",
    "LegacySessionNotFoundError",
    "LegacySessionScopeError",
]
