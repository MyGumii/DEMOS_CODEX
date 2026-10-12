"""Owner-only management search across active personal Wikis.

This operation is intentionally not a retrieval/Agent-context entry point.  It
performs one owner/status-filtered FTS statement, preserves real provenance,
and never reports archived hit counts.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import re

from ..db import ConnectionFactory, TransactionMode, transaction
from ..repositories.retrieval import _normalize_query
from ..repositories.users import UserNotFoundError, UserRepository
from .personal_wikis import (
    PersonalWikiAuthenticationError,
    PersonalWikiCursorError,
    PersonalWikiScopeStaleError,
)


_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_CURSOR_VERSION = 1
_MAX_CURSOR = 4096
_MAX_OFFSET = 990


@dataclass(frozen=True, slots=True)
class PersonalWikiManagementSearchRequest:
    actor_user_id: str
    actor_scope_epoch: int
    request_id: str
    query: str
    limit: int = 10
    cursor: str | None = None


@dataclass(frozen=True, slots=True)
class PersonalWikiManagementSearchItem:
    item_id: str
    revision_id: str
    source_space_id: str
    wiki_display_name: str
    title: str
    excerpt_md: str
    score_ppm: int
    provenance: tuple[str, ...] = ("fts",)


@dataclass(frozen=True, slots=True)
class PersonalWikiManagementSearchResult:
    request_id: str
    normalized_query: str
    items: tuple[PersonalWikiManagementSearchItem, ...]
    next_cursor: str | None


def _canonical_json(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class PersonalWikiManagementSearchService:
    __slots__ = ("_cursor_secret", "_factory", "_users")

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        cursor_secret: bytes,
        users: UserRepository | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("factory must be a ConnectionFactory.")
        if not isinstance(cursor_secret, bytes) or len(cursor_secret) < 32:
            raise ValueError("cursor_secret must contain at least 32 bytes.")
        self._factory = factory
        self._cursor_secret = bytes(cursor_secret)
        self._users = users or UserRepository()

    def _signature(self, raw: bytes) -> str:
        return hmac.new(self._cursor_secret, raw, sha256).hexdigest()

    def _encode_cursor(self, *, actor: str, query_sha: str, offset: int) -> str:
        raw = _canonical_json(
            {
                "actor_user_id": actor,
                "offset": offset,
                "query_sha256": query_sha,
                "version": _CURSOR_VERSION,
            }
        )
        body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        return f"{body}.{self._signature(raw)}"

    def _decode_cursor(self, value: str, *, actor: str, query_sha: str) -> int:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > _MAX_CURSOR
            or value.count(".") != 1
        ):
            raise PersonalWikiCursorError("The management-search cursor is invalid.")
        body, signature = value.split(".", 1)
        if re.fullmatch(r"[A-Za-z0-9_-]+", body) is None or re.fullmatch(
            r"[0-9a-f]{64}", signature
        ) is None:
            raise PersonalWikiCursorError("The management-search cursor is invalid.")
        try:
            raw = base64.b64decode(body + ("=" * (-len(body) % 4)), altchars=b"-_", validate=True)
            decoded = json.loads(raw.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise PersonalWikiCursorError("The management-search cursor is invalid.") from exc
        if not hmac.compare_digest(signature, self._signature(raw)):
            raise PersonalWikiCursorError("The management-search cursor is invalid.")
        if (
            not isinstance(decoded, dict)
            or set(decoded) != {"actor_user_id", "offset", "query_sha256", "version"}
            or _canonical_json(decoded) != raw
            or decoded.get("version") != _CURSOR_VERSION
            or decoded.get("actor_user_id") != actor
            or decoded.get("query_sha256") != query_sha
        ):
            raise PersonalWikiCursorError(
                "The management-search cursor does not match this owner or query."
            )
        offset = decoded.get("offset")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 1 <= offset <= _MAX_OFFSET:
            raise PersonalWikiCursorError("The management-search cursor is invalid.")
        return offset

    def search(
        self, request: PersonalWikiManagementSearchRequest
    ) -> PersonalWikiManagementSearchResult:
        if not isinstance(request, PersonalWikiManagementSearchRequest):
            raise TypeError("request must be PersonalWikiManagementSearchRequest.")
        actor = request.actor_user_id
        if not isinstance(actor, str) or _USER_RE.fullmatch(actor) is None:
            raise ValueError("actor_user_id is invalid.")
        if (
            isinstance(request.actor_scope_epoch, bool)
            or not isinstance(request.actor_scope_epoch, int)
            or request.actor_scope_epoch < 1
        ):
            raise ValueError("actor_scope_epoch is invalid.")
        if not isinstance(request.request_id, str) or _UUID_RE.fullmatch(request.request_id) is None:
            raise ValueError("request_id is invalid.")
        if isinstance(request.limit, bool) or not isinstance(request.limit, int) or not 1 <= request.limit <= 10:
            raise ValueError("limit must be from 1 through 10.")
        normalized_query, fts_query = _normalize_query(request.query)
        query_sha = sha256(normalized_query.encode("utf-8")).hexdigest()
        offset = 0
        if request.cursor is not None:
            offset = self._decode_cursor(request.cursor, actor=actor, query_sha=query_sha)

        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                try:
                    user = self._users.require(connection, actor)
                except UserNotFoundError as exc:
                    raise PersonalWikiAuthenticationError(
                        "A valid active account session is required."
                    ) from exc
                if user.account_status != "active":
                    raise PersonalWikiAuthenticationError(
                        "A valid active account session is required."
                    )
                if user.scope_epoch != request.actor_scope_epoch:
                    raise PersonalWikiScopeStaleError("The account session scope is stale.")
                rows = connection.execute(
                    """SELECT document.item_id,document.revision_id,
                              document.space_id,profile.display_name,
                              document.title,document.updated_at,
                              bm25(knowledge_fts,6.0,1.0) AS bm25_score,
                              snippet(knowledge_fts,1,'','',' ... ',48) AS excerpt_md
                         FROM knowledge_fts
                         JOIN search_document AS document
                           ON document.rowid=knowledge_fts.rowid
                         JOIN personal_wiki_profile AS profile
                           ON profile.space_id=document.space_id
                          AND profile.owner_user_id=?
                          AND profile.lifecycle_status='active'
                         JOIN knowledge_space AS space
                           ON space.id=profile.space_id AND space.status='active'
                         JOIN knowledge_item AS item
                           ON item.id=document.item_id
                          AND item.space_id=document.space_id
                         JOIN knowledge_revision AS revision
                           ON revision.id=document.revision_id
                          AND revision.item_id=document.item_id
                         JOIN knowledge_revision_body AS body
                           ON body.revision_id=document.revision_id
                          AND body.item_id=document.item_id
                        WHERE knowledge_fts MATCH ?
                          AND item.lifecycle_status='active'
                          AND item.curation_status='published'
                          AND item.current_revision_id=document.revision_id
                          AND document.title=revision.title
                          AND (body.content_md IS NULL OR document.content_md=body.content_md)
                        ORDER BY bm25_score,document.updated_at DESC,
                                 document.space_id,document.item_id
                        LIMIT ? OFFSET ?""",
                    (actor, fts_query, request.limit + 1, offset),
                ).fetchall()
        finally:
            connection.close()
        visible = rows[: request.limit]
        items = tuple(
            PersonalWikiManagementSearchItem(
                item_id=str(row["item_id"]),
                revision_id=str(row["revision_id"]),
                source_space_id=str(row["space_id"]),
                wiki_display_name=str(row["display_name"]),
                title=str(row["title"]),
                excerpt_md=str(row["excerpt_md"]),
                score_ppm=max(0, 900_000 - ((offset + rank - 1) * 18_000)),
            )
            for rank, row in enumerate(visible, start=1)
        )
        next_cursor = None
        next_offset = offset + len(items)
        if len(rows) > request.limit and next_offset <= _MAX_OFFSET:
            next_cursor = self._encode_cursor(
                actor=actor, query_sha=query_sha, offset=next_offset
            )
        return PersonalWikiManagementSearchResult(
            request_id=request.request_id,
            normalized_query=normalized_query,
            items=items,
            next_cursor=next_cursor,
        )


__all__ = [
    "PersonalWikiManagementSearchItem",
    "PersonalWikiManagementSearchRequest",
    "PersonalWikiManagementSearchResult",
    "PersonalWikiManagementSearchService",
]
