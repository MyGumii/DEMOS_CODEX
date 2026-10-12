"""P3.5 read-only legacy knowledge and graph response adapters.

The adapter preserves the small JSON shapes consumed by the existing knowledge
UI while reading only canonical SQLite state.  It never imports a legacy store,
opens a projection file, refreshes an index, or accepts ``legacy_user_id`` as an
authorization input.  All compatibility reads resolve the authenticated
actor's personal space through :class:`ScopeResolver` in one read-only snapshot.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import re
import unicodedata
from typing import Iterator, Mapping

from ..db import ConnectionFactory, ManagedConnection, TransactionMode, transaction
from ..errors import KnowledgeCoreError
from ..scope import ScopeResolutionError, ScopeResolver, SessionPrincipal


_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_EDGE_TYPES = (
    "contradicts",
    "depends_on",
    "example_of",
    "extends",
    "related",
    "supports",
)
_FINDING_STATUSES = ("pending", "applied", "rejected", "skipped", "deleted")
_MAX_ITEMS = 500
_MAX_EDGES = 2_000
_MAX_FINDINGS = 500
_MAX_SEARCH_RESULTS = 20
_MAX_SEARCH_CONTENT_CHARS = 32_768
_MAX_QUERY_CHARS = 500
_MAX_QUERY_TERMS = 32


class LegacyReadCompatibilityError(KnowledgeCoreError):
    code = "LEGACY_READ_COMPATIBILITY_ERROR"


class LegacyReadScopeError(LegacyReadCompatibilityError):
    code = "SCOPE_STALE"
    http_status = 409


class LegacyReadStateConflictError(LegacyReadCompatibilityError):
    code = "STATE_CONFLICT"
    http_status = 409


class LegacyReadIntegrityError(LegacyReadCompatibilityError):
    code = "INTERNAL_ERROR"
    http_status = 500


@dataclass(frozen=True, slots=True)
class LegacyPersonalReadRequest:
    actor_user_id: str
    actor_scope_epoch: int
    legacy_user_id: str | None = None
    limit: int = 200


@dataclass(frozen=True, slots=True)
class LegacySearchReadRequest:
    actor_user_id: str
    actor_scope_epoch: int
    query: str
    legacy_user_id: str | None = None
    max_results: int = 8
    max_content_chars: int = 4_000


@dataclass(frozen=True, slots=True)
class LegacyGraphReadRequest:
    actor_user_id: str
    actor_scope_epoch: int
    legacy_user_id: str | None = None
    q: str = ""
    focus: str = ""
    depth: int | None = None
    memory_type: str = ""
    service: str = ""
    topic: str = ""
    edge_type: str = ""
    include_legacy: bool = True
    view: str = "wiki"


@dataclass(frozen=True, slots=True)
class LegacyFindingReadRequest:
    actor_user_id: str
    actor_scope_epoch: int
    legacy_user_id: str | None = None
    status: str | None = None
    q: str = ""
    include_deleted: bool = True
    limit: int = 200


@dataclass(frozen=True, slots=True)
class _Identity:
    actor_user_id: str
    actor_scope_epoch: int


@dataclass(frozen=True, slots=True)
class _Item:
    item_id: str
    space_id: str
    revision_id: str
    title: str
    content_md: str
    content_sha256: str
    content_size_bytes: int
    lifecycle_status: str
    curation_status: str
    updated_at_ms: int
    metadata: dict[str, object]


def _identity(value: object) -> _Identity:
    actor = getattr(value, "actor_user_id", None)
    epoch = getattr(value, "actor_scope_epoch", None)
    legacy = getattr(value, "legacy_user_id", None)
    if not isinstance(actor, str) or _USER_RE.fullmatch(actor) is None:
        raise ValueError("actor_user_id is invalid.")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
        raise ValueError("actor_scope_epoch must be a positive integer.")
    if legacy is not None:
        if (
            not isinstance(legacy, str)
            or len(legacy) > 256
            or any(unicodedata.category(ch).startswith("C") for ch in legacy)
        ):
            raise ValueError("legacy_user_id is invalid compatibility text.")
        # Deliberately do not normalize, compare, or use this value.  The
        # authenticated actor remains the only personal-space selector.
    return _Identity(actor, epoch)


def _limit(value: object, field: str, maximum: int, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{field} must be an integer from {minimum} through {maximum}.")
    return value


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise LegacyReadIntegrityError(f"Stored {field} is invalid.")
    return value


def _sha(value: object, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise LegacyReadIntegrityError(f"Stored {field} is invalid.")
    return value


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LegacyReadIntegrityError(f"Stored {field} is invalid.")
    return value


def _safe_text(value: object, field: str, *, maximum: int = 1_000_000) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise LegacyReadIntegrityError(f"Stored {field} is invalid.")
    if any(
        unicodedata.category(ch).startswith("C") and ch not in {"\n", "\r", "\t"}
        for ch in value
    ):
        raise LegacyReadIntegrityError(f"Stored {field} contains unsafe controls.")
    return value


def _input_filter_text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")
    normalized = unicodedata.normalize("NFKC", value).strip()
    if len(normalized) > 200 or any(
        unicodedata.category(ch).startswith("C") for ch in normalized
    ):
        raise ValueError(f"{field} is invalid.")
    return normalized


def _metadata_text(metadata: Mapping[str, object], key: str) -> str:
    value = metadata.get(key)
    if value is None:
        return ""
    text = _safe_text(value, f"revision metadata {key}", maximum=200)
    if any(unicodedata.category(ch).startswith("C") for ch in text):
        raise LegacyReadIntegrityError(f"Stored revision metadata {key} is invalid.")
    return text


def _metadata_topics(metadata: Mapping[str, object]) -> list[str]:
    value = metadata.get("topics")
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 100:
        raise LegacyReadIntegrityError("Stored revision metadata topics is invalid.")
    topics: list[str] = []
    for raw in value:
        text = _safe_text(raw, "revision metadata topic", maximum=200)
        if any(unicodedata.category(ch).startswith("C") for ch in text):
            raise LegacyReadIntegrityError(
                "Stored revision metadata topic is invalid."
            )
        topics.append(text)
    return topics


def _canonical_object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, str):
        raise LegacyReadIntegrityError(f"Stored {field} is invalid.")
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        raise LegacyReadIntegrityError(f"Stored {field} is invalid.") from None
    if not isinstance(parsed, dict):
        raise LegacyReadIntegrityError(f"Stored {field} is not an object.")
    canonical = json.dumps(
        parsed,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if canonical != value:
        raise LegacyReadIntegrityError(f"Stored {field} is not canonical JSON.")
    return parsed


def _query(value: object, *, allow_empty: bool = False) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError("query must be text.")
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        if allow_empty:
            return "", ""
        raise ValueError("query cannot be empty.")
    if len(normalized) > _MAX_QUERY_CHARS or any(
        unicodedata.category(ch).startswith("C") for ch in normalized
    ):
        raise ValueError("query is invalid.")
    chunks = tuple(
        chunk
        for chunk in normalized.split()
        if chunk and any(character.isalnum() for character in chunk)
    )
    if not chunks:
        if allow_empty:
            return normalized, ""
        raise ValueError("query has no searchable term.")
    if len(chunks) > _MAX_QUERY_TERMS:
        raise ValueError("query has too many searchable terms.")
    literals = tuple('"' + chunk.replace('"', '""') + '"' for chunk in chunks)
    return " ".join(chunks), " OR ".join(literals)


def _status(value: str) -> str:
    return {
        "applied": "applied",
        "approved": "pending",
        "apply_failed": "pending",
        "ignored": "skipped",
        "obsolete": "deleted",
        "pending": "pending",
        "rejected": "rejected",
    }[value]


def _canonical_path(space_id: str, item_id: str) -> str:
    return f"canonical://knowledge-spaces/{space_id}/items/{item_id}"


class LegacyKnowledgeReadAdapter:
    """Project canonical personal-space reads into legacy JSON response shapes."""

    __slots__ = ("_factory", "_scopes")

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        scope_resolver: ScopeResolver,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("LegacyKnowledgeReadAdapter requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("LegacyKnowledgeReadAdapter requires a ScopeResolver.")
        self._factory = factory
        self._scopes = scope_resolver

    @contextmanager
    def _snapshot(self, request: object) -> Iterator[tuple[ManagedConnection, str, str]]:
        identity = _identity(request)
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                try:
                    selectable = self._scopes.list_selectable_in_transaction(
                        connection,
                        SessionPrincipal(
                            identity.actor_user_id,
                            identity.actor_scope_epoch,
                        ),
                    )
                except ScopeResolutionError as exc:
                    raise LegacyReadScopeError(
                        "The authenticated personal knowledge scope is unavailable."
                    ) from exc
                personal = next(
                    (space for space in selectable.items if space.kind == "personal"),
                    None,
                )
                if personal is None:
                    raise LegacyReadScopeError(
                        "The authenticated personal knowledge scope is unavailable."
                    )
                yield connection, personal.id, identity.actor_user_id
        finally:
            connection.close()

    @staticmethod
    def _item_from_row(row: object, expected_space_id: str) -> _Item:
        space_id = _uuid(row["space_id"], "item space_id")  # type: ignore[index]
        if space_id != expected_space_id:
            raise LegacyReadIntegrityError("A canonical item crossed the authorized space.")
        item_id = _uuid(row["item_id"], "item_id")  # type: ignore[index]
        revision_id = _uuid(row["revision_id"], "revision_id")  # type: ignore[index]
        if row["current_revision_id"] != revision_id:  # type: ignore[index]
            raise LegacyReadIntegrityError("A compatibility item is not current.")
        lifecycle = row["lifecycle_status"]  # type: ignore[index]
        curation = row["curation_status"]  # type: ignore[index]
        if lifecycle != "active" or curation not in {
            "published",
            "provisional",
            "quarantined",
        }:
            raise LegacyReadIntegrityError("A compatibility item has invalid visibility.")
        title = _safe_text(row["title"], "item title", maximum=500)  # type: ignore[index]
        body_sha = _sha(row["body_sha256"], "revision body SHA-256")  # type: ignore[index]
        normalized_sha = _sha(  # type: ignore[index]
            row["normalized_sha256"], "revision normalized SHA-256"
        )
        if body_sha != normalized_sha:
            raise LegacyReadIntegrityError("A revision body hash is not current evidence.")
        size = _integer(row["body_size"], "revision body size")  # type: ignore[index]
        if size != _integer(row["revision_size"], "revision size"):  # type: ignore[index]
            raise LegacyReadIntegrityError("Revision size evidence disagrees.")
        inline = row["inline_content_md"]  # type: ignore[index]
        blob_hash = row["content_blob_hash"]  # type: ignore[index]
        search_revision = row["search_revision_id"]  # type: ignore[index]
        search_title = row["search_title"]  # type: ignore[index]
        search_content = row["search_content_md"]  # type: ignore[index]
        if (inline is None) == (blob_hash is None):
            raise LegacyReadIntegrityError("Revision body storage is ambiguous.")
        if inline is not None:
            content = _safe_text(inline, "revision content")
        else:
            _sha(blob_hash, "revision body blob SHA-256")
            if search_content is None:
                raise LegacyReadStateConflictError(
                    "Blob-backed current content has no canonical search document."
                )
            content = _safe_text(search_content, "search content")
        encoded = content.encode("utf-8")
        if len(encoded) != size or hashlib.sha256(encoded).hexdigest() != body_sha:
            raise LegacyReadIntegrityError("Current compatibility content is not hash-bound.")
        if search_revision is not None and (
            search_revision != revision_id
            or search_title != title
            or search_content != content
        ):
            raise LegacyReadIntegrityError("The canonical search document is inconsistent.")
        return _Item(
            item_id=item_id,
            space_id=space_id,
            revision_id=revision_id,
            title=title,
            content_md=content,
            content_sha256=body_sha,
            content_size_bytes=size,
            lifecycle_status=lifecycle,
            curation_status=curation,
            updated_at_ms=_integer(row["updated_at"], "item update timestamp"),  # type: ignore[index]
            metadata=_canonical_object(row["metadata_json"], "revision metadata"),  # type: ignore[index]
        )

    @staticmethod
    def _item_sql(where: str) -> str:
        return f"""SELECT i.id AS item_id,i.space_id,i.lifecycle_status,
                           i.curation_status,i.current_revision_id,i.updated_at,
                           r.id AS revision_id,r.title,r.normalized_content_sha256
                             AS normalized_sha256,r.content_size_bytes AS revision_size,
                           r.metadata_json,b.content_md AS inline_content_md,
                           b.content_blob_hash,b.content_sha256 AS body_sha256,
                           b.content_size_bytes AS body_size,
                           d.revision_id AS search_revision_id,d.title AS search_title,
                           d.content_md AS search_content_md
                      FROM knowledge_item AS i
                      JOIN knowledge_revision AS r
                        ON r.id=i.current_revision_id AND r.item_id=i.id
                      JOIN knowledge_revision_body AS b
                        ON b.revision_id=r.id AND b.item_id=i.id
                 LEFT JOIN search_document AS d
                        ON d.item_id=i.id AND d.space_id=i.space_id
                       AND d.revision_id=r.id
                     WHERE i.lifecycle_status='active' AND {where}"""

    def _items(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        limit: int,
    ) -> tuple[_Item, ...]:
        rows = connection.execute(
            self._item_sql("i.space_id=?")
            + " ORDER BY i.updated_at DESC,i.id ASC LIMIT ?",
            (space_id, limit + 1),
        ).fetchall()
        if len(rows) > limit:
            raise LegacyReadStateConflictError(
                f"The compatibility item view exceeds its {limit}-row bound."
            )
        return tuple(self._item_from_row(row, space_id) for row in rows)

    def _item(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        item_id: str,
    ) -> _Item | None:
        if not isinstance(item_id, str) or _UUID_RE.fullmatch(item_id) is None:
            return None
        row = connection.execute(
            self._item_sql("i.space_id=? AND i.id=?"),
            (space_id, item_id),
        ).fetchone()
        return None if row is None else self._item_from_row(row, space_id)

    @staticmethod
    def _file_item(item: _Item) -> dict[str, object]:
        return {
            "filename": item.item_id,
            "size": item.content_size_bytes,
            "mtime": item.updated_at_ms / 1000.0,
            "item_id": item.item_id,
            "revision_id": item.revision_id,
            "title": item.title,
            "lifecycle_status": item.lifecycle_status,
            "curation_status": item.curation_status,
        }

    @staticmethod
    def _thought_item(item: _Item, *, include_content: bool) -> dict[str, object]:
        topics = _metadata_topics(item.metadata)
        meta: dict[str, object] = {
            "id": item.item_id,
            "title": item.title,
            "memory_type": "document",
            "domain": _metadata_text(item.metadata, "domain"),
            "service": _metadata_text(item.metadata, "service"),
            "topics": topics,
            "sources": [{"type": "canonical", "path": _canonical_path(item.space_id, item.item_id)}],
            "status": "active",
            "meaning_version": 1,
            "updated_at": item.updated_at_ms // 1000,
            "path": _canonical_path(item.space_id, item.item_id),
            "filename": item.item_id,
            "kind": "canonical",
            "content_preview": item.content_md[:300],
            "revision_id": item.revision_id,
            "curation_status": item.curation_status,
        }
        return {"meta": meta, "content": item.content_md} if include_content else meta

    def list_knowledge(self, request: LegacyPersonalReadRequest) -> dict[str, object]:
        if not isinstance(request, LegacyPersonalReadRequest):
            raise TypeError("A LegacyPersonalReadRequest is required.")
        limit = _limit(request.limit, "limit", _MAX_ITEMS)
        with self._snapshot(request) as (connection, space_id, actor_id):
            items = [
                self._file_item(item)
                for item in self._items(
                    connection, space_id=space_id, limit=_MAX_ITEMS
                )[:limit]
            ]
            return {"items": items, "user_id": actor_id}

    def view_knowledge(
        self,
        request: LegacyPersonalReadRequest,
        item_id: str,
    ) -> dict[str, object] | None:
        if not isinstance(request, LegacyPersonalReadRequest):
            raise TypeError("A LegacyPersonalReadRequest is required.")
        with self._snapshot(request) as (connection, space_id, _actor_id):
            item = self._item(connection, space_id=space_id, item_id=item_id)
            if item is None:
                return None
            return {
                "filename": item.item_id,
                "content": item.content_md,
                "path": _canonical_path(item.space_id, item.item_id),
                "item_id": item.item_id,
                "revision_id": item.revision_id,
                "title": item.title,
                "curation_status": item.curation_status,
            }

    def list_thoughts(self, request: LegacyPersonalReadRequest) -> dict[str, object]:
        if not isinstance(request, LegacyPersonalReadRequest):
            raise TypeError("A LegacyPersonalReadRequest is required.")
        limit = _limit(request.limit, "limit", _MAX_ITEMS)
        with self._snapshot(request) as (connection, space_id, actor_id):
            items = [
                self._thought_item(item, include_content=False)
                for item in self._items(
                    connection, space_id=space_id, limit=_MAX_ITEMS
                )[:limit]
            ]
            return {"items": items, "count": len(items), "user_id": actor_id}

    def get_thought(
        self,
        request: LegacyPersonalReadRequest,
        item_id: str,
    ) -> dict[str, object] | None:
        if not isinstance(request, LegacyPersonalReadRequest):
            raise TypeError("A LegacyPersonalReadRequest is required.")
        with self._snapshot(request) as (connection, space_id, _actor_id):
            item = self._item(connection, space_id=space_id, item_id=item_id)
            return None if item is None else self._thought_item(item, include_content=True)

    def search_knowledge(self, request: LegacySearchReadRequest) -> dict[str, object]:
        if not isinstance(request, LegacySearchReadRequest):
            raise TypeError("A LegacySearchReadRequest is required.")
        normalized, fts_query = _query(request.query)
        max_results = _limit(request.max_results, "max_results", _MAX_SEARCH_RESULTS)
        max_chars = _limit(
            request.max_content_chars,
            "max_content_chars",
            _MAX_SEARCH_CONTENT_CHARS,
        )
        with self._snapshot(request) as (connection, space_id, actor_id):
            rows = connection.execute(
                """SELECT d.item_id,bm25(knowledge_fts,6.0,1.0) AS score,
                          snippet(knowledge_fts,1,'','',' ... ',48) AS snippet
                     FROM knowledge_fts
                     JOIN search_document AS d ON d.rowid=knowledge_fts.rowid
                     JOIN knowledge_item AS i
                       ON i.id=d.item_id AND i.space_id=d.space_id
                    WHERE knowledge_fts MATCH ? AND d.space_id=?
                      AND i.lifecycle_status='active'
                      AND i.current_revision_id=d.revision_id
                    ORDER BY score ASC,d.updated_at DESC,d.item_id ASC LIMIT ?""",
                (fts_query, space_id, max_results),
            ).fetchall()
            results: list[dict[str, object]] = []
            for rank, row in enumerate(rows, start=1):
                item = self._item(
                    connection,
                    space_id=space_id,
                    item_id=_uuid(row["item_id"], "search item_id"),
                )
                if item is None:
                    raise LegacyReadIntegrityError("A search hit has no current item.")
                score = row["score"]
                if isinstance(score, bool) or not isinstance(score, (int, float)):
                    raise LegacyReadIntegrityError("Stored search score is invalid.")
                numeric = float(score)
                if not math.isfinite(numeric):
                    raise LegacyReadIntegrityError("Stored search score is invalid.")
                results.append(
                    {
                        "filename": item.item_id,
                        "score": round(max(0.0, -numeric), 6),
                        "rank": rank,
                        "content": item.content_md[:max_chars],
                        "path": _canonical_path(space_id, item.item_id),
                        "snippet": _safe_text(row["snippet"], "search snippet", maximum=4_000),
                        "item_id": item.item_id,
                        "revision_id": item.revision_id,
                        "title": item.title,
                        "curation_status": item.curation_status,
                    }
                )
            return {
                "query": normalized,
                "results": results,
                "count": len(results),
                "user_id": actor_id,
            }

    def graph(self, request: LegacyGraphReadRequest) -> dict[str, object]:
        if not isinstance(request, LegacyGraphReadRequest):
            raise TypeError("A LegacyGraphReadRequest is required.")
        _identity(request)
        query_text, _unused = _query(request.q, allow_empty=True)
        if not isinstance(request.focus, str):
            raise ValueError("focus must be text.")
        if not isinstance(request.memory_type, str):
            raise ValueError("memory_type must be text.")
        if not isinstance(request.edge_type, str):
            raise ValueError("edge_type must be text.")
        if not isinstance(request.view, str):
            raise ValueError("view must be text.")
        if request.depth is not None:
            depth = _limit(request.depth, "depth", 2, allow_zero=True)
        else:
            depth = None
        if request.edge_type and request.edge_type not in _EDGE_TYPES:
            raise ValueError("edge_type is invalid.")
        if request.memory_type not in {"", "document"}:
            memory_type_miss = True
        else:
            memory_type_miss = False
        if request.view != "wiki":
            raise ValueError("view must be wiki.")
        if not isinstance(request.include_legacy, bool):
            raise ValueError("include_legacy must be Boolean.")
        service_filter = _input_filter_text(request.service, "service").casefold()
        topic_filter = _input_filter_text(request.topic, "topic").casefold()
        with self._snapshot(request) as (connection, space_id, _actor_id):
            all_items = self._items(connection, space_id=space_id, limit=_MAX_ITEMS)
            by_id = {item.item_id: item for item in all_items}
            edge_rows = connection.execute(
                """SELECT e.id,e.source_item_id,e.target_item_id,e.edge_type,
                          e.needs_revalidation,e.updated_at
                     FROM knowledge_edge AS e
                     JOIN knowledge_item AS source
                       ON source.id=e.source_item_id AND source.space_id=e.space_id
                     JOIN knowledge_item AS target
                       ON target.id=e.target_item_id AND target.space_id=e.space_id
                    WHERE e.space_id=? AND e.edge_status='active'
                      AND source.lifecycle_status='active'
                      AND target.lifecycle_status='active'
                    ORDER BY e.updated_at DESC,e.id ASC LIMIT ?""",
                (space_id, _MAX_EDGES + 1),
            ).fetchall()
            if len(edge_rows) > _MAX_EDGES:
                raise LegacyReadStateConflictError("The compatibility graph exceeds its edge bound.")
            edges: list[dict[str, object]] = []
            adjacency: dict[str, set[str]] = {item_id: set() for item_id in by_id}
            for row in edge_rows:
                source = _uuid(row["source_item_id"], "edge source")
                target = _uuid(row["target_item_id"], "edge target")
                if source not in by_id or target not in by_id or source == target:
                    raise LegacyReadIntegrityError("An effective graph edge has invalid endpoints.")
                edge_type = row["edge_type"]
                if edge_type not in _EDGE_TYPES:
                    raise LegacyReadIntegrityError("An effective graph edge type is invalid.")
                adjacency[source].add(target)
                adjacency[target].add(source)
                if not request.edge_type or edge_type == request.edge_type:
                    edges.append(
                        {
                            "id": _uuid(row["id"], "edge_id"),
                            "source": source,
                            "target": target,
                            "edge_type": edge_type,
                            "weight": 1.0,
                            "needs_revalidation": row["needs_revalidation"] == 1,
                        }
                    )
            visible: set[str] = set()
            query_fold = query_text.casefold()
            for item in all_items:
                service = _metadata_text(item.metadata, "service").casefold()
                topics = _metadata_topics(item.metadata)
                topic_values = [value.casefold() for value in topics]
                haystack = f"{item.title}\n{item.content_md}".casefold()
                if memory_type_miss:
                    continue
                if query_fold and query_fold not in haystack:
                    continue
                if service_filter and service_filter != service:
                    continue
                if topic_filter and topic_filter not in topic_values:
                    continue
                visible.add(item.item_id)
            if request.focus:
                if _UUID_RE.fullmatch(request.focus) is None or request.focus not in by_id:
                    visible.clear()
                elif depth is not None:
                    reached = {request.focus}
                    frontier = {request.focus}
                    for _level in range(depth):
                        frontier = {
                            neighbor
                            for item_id in frontier
                            for neighbor in adjacency.get(item_id, ())
                        } - reached
                        reached.update(frontier)
                    visible.intersection_update(reached)
            filtered_edges = [
                edge
                for edge in edges
                if edge["source"] in visible and edge["target"] in visible
            ]
            degree = {item_id: 0 for item_id in visible}
            for edge in filtered_edges:
                degree[str(edge["source"])] += 1
                degree[str(edge["target"])] += 1
            nodes = []
            for item_id in sorted(visible, key=lambda value: (by_id[value].title.casefold(), value)):
                item = by_id[item_id]
                topics = _metadata_topics(item.metadata)
                nodes.append(
                    {
                        "data": {
                            "id": item.item_id,
                            "label": item.title,
                            "title": item.title,
                            "memory_type": "document",
                            "view_type": "canonical",
                            "kind": "canonical",
                            "domain": _metadata_text(item.metadata, "domain"),
                            "service": _metadata_text(item.metadata, "service"),
                            "topics": topics,
                            "content_preview": item.content_md[:300],
                            "filename": item.item_id,
                            "rel_path": "",
                            "degree": degree[item.item_id],
                            "revision_id": item.revision_id,
                            "curation_status": item.curation_status,
                        }
                    }
                )
            return {
                "view": "wiki",
                "nodes": nodes,
                "edges": [{"data": edge} for edge in filtered_edges],
                "counts": {"nodes": len(nodes), "edges": len(filtered_edges)},
                "filters": {
                    "memory_types": ["document"],
                    "edge_types": list(_EDGE_TYPES),
                },
            }

    @staticmethod
    def _finding_item(row: object, space_id: str) -> dict[str, object]:
        if _uuid(row["space_id"], "finding space_id") != space_id:  # type: ignore[index]
            raise LegacyReadIntegrityError("A finding crossed the authorized space.")
        canonical_status = row["status"]  # type: ignore[index]
        if canonical_status not in {
            "pending",
            "approved",
            "rejected",
            "ignored",
            "obsolete",
            "applied",
            "apply_failed",
        }:
            raise LegacyReadIntegrityError("Stored finding status is invalid.")
        evidence = _canonical_object(row["evidence_json"], "finding evidence")  # type: ignore[index]
        proposed = _canonical_object(  # type: ignore[index]
            row["proposed_action_json"], "finding proposed action"
        )
        candidate = evidence.get("candidate")
        candidate_kind = (
            candidate.get("kind")
            if isinstance(candidate, Mapping)
            and candidate.get("kind") in {"graph_candidate", "wiki_diff"}
            else (
                "graph_candidate"
                if row["finding_kind"] == "semantic_edge"  # type: ignore[index]
                else "wiki_diff"
            )
        )
        source_title = _safe_text(row["source_title"], "finding source title", maximum=500)  # type: ignore[index]
        target_title_value = row["target_title"]  # type: ignore[index]
        target_title = (
            ""
            if target_title_value is None
            else _safe_text(target_title_value, "finding target title", maximum=500)
        )
        return {
            "id": _uuid(row["id"], "finding_id"),  # type: ignore[index]
            "candidate_kind": candidate_kind,
            "status": _status(str(canonical_status)),
            "title": source_title,
            "content": "",
            "proposed_content": "",
            "source_id": _uuid(row["source_item_id"], "finding source item"),  # type: ignore[index]
            "source_revision_id": _uuid(row["source_revision_id"], "finding source revision"),  # type: ignore[index]
            "target_id": None
            if row["target_item_id"] is None  # type: ignore[index]
            else _uuid(row["target_item_id"], "finding target item"),  # type: ignore[index]
            "target_revision_id": None
            if row["target_revision_id"] is None  # type: ignore[index]
            else _uuid(row["target_revision_id"], "finding target revision"),  # type: ignore[index]
            "target_filename": target_title,
            "finding_kind": row["finding_kind"],  # type: ignore[index]
            "risk_tier": row["risk_tier"],  # type: ignore[index]
            "severity": row["severity"],  # type: ignore[index]
            "visibility_effect": row["visibility_effect"],  # type: ignore[index]
            "proposed_action": row["proposed_action"],  # type: ignore[index]
            "evidence": evidence,
            "proposed_action_details": proposed,
            "created_at": _integer(row["created_at"], "finding creation timestamp") // 1000,  # type: ignore[index]
            "updated_at": _integer(row["updated_at"], "finding update timestamp") // 1000,  # type: ignore[index]
            "row_version": _integer(row["row_version"], "finding row version"),  # type: ignore[index]
        }

    @staticmethod
    def _finding_sql(where: str) -> str:
        return f"""SELECT f.id,f.space_id,f.finding_kind,f.risk_tier,f.severity,
                           f.status,f.visibility_effect,f.proposed_action,
                           f.source_item_id,f.source_revision_id,f.target_item_id,
                           f.target_revision_id,f.evidence_json,f.proposed_action_json,
                           f.created_at,f.updated_at,f.row_version,
                           source.title AS source_title,target.title AS target_title
                      FROM curation_finding AS f
                      JOIN knowledge_revision AS source
                        ON source.id=f.source_revision_id
                       AND source.item_id=f.source_item_id
                 LEFT JOIN knowledge_revision AS target
                        ON target.id=f.target_revision_id
                       AND target.item_id=f.target_item_id
                     WHERE {where}"""

    def _findings(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        limit: int,
    ) -> tuple[dict[str, object], ...]:
        rows = connection.execute(
            self._finding_sql("f.space_id=?")
            + " ORDER BY f.updated_at DESC,f.id ASC LIMIT ?",
            (space_id, limit + 1),
        ).fetchall()
        if len(rows) > limit:
            raise LegacyReadStateConflictError(
                f"The compatibility finding view exceeds its {limit}-row bound."
            )
        return tuple(self._finding_item(row, space_id) for row in rows)

    def list_findings(self, request: LegacyFindingReadRequest) -> dict[str, object]:
        if not isinstance(request, LegacyFindingReadRequest):
            raise TypeError("A LegacyFindingReadRequest is required.")
        limit = _limit(request.limit, "limit", _MAX_FINDINGS)
        if request.status is not None and request.status not in _FINDING_STATUSES:
            raise ValueError("status is not a legacy finding status.")
        if not isinstance(request.include_deleted, bool):
            raise ValueError("include_deleted must be Boolean.")
        query_text, _unused = _query(request.q, allow_empty=True)
        query_fold = query_text.casefold()
        with self._snapshot(request) as (connection, space_id, actor_id):
            all_items = self._findings(
                connection, space_id=space_id, limit=_MAX_FINDINGS
            )
            counts = {key: 0 for key in _FINDING_STATUSES}
            for item in all_items:
                counts[str(item["status"])] += 1
            counts["all"] = len(all_items)
            items = [
                item
                for item in all_items
                if (request.status is None or item["status"] == request.status)
                and (request.include_deleted or item["status"] != "deleted")
                and (
                    not query_fold
                    or query_fold
                    in (
                        f"{item['title']}\n{item['target_filename']}\n"
                        f"{item['candidate_kind']}\n{item['finding_kind']}"
                    ).casefold()
                )
            ][:limit]
            return {
                "items": items,
                "count": len(items),
                "counts": counts,
                "user_id": actor_id,
            }

    def get_finding(
        self,
        request: LegacyPersonalReadRequest,
        finding_id: str,
    ) -> dict[str, object] | None:
        if not isinstance(request, LegacyPersonalReadRequest):
            raise TypeError("A LegacyPersonalReadRequest is required.")
        if not isinstance(finding_id, str) or _UUID_RE.fullmatch(finding_id) is None:
            return None
        with self._snapshot(request) as (connection, space_id, _actor_id):
            row = connection.execute(
                self._finding_sql("f.space_id=? AND f.id=?"),
                (space_id, finding_id),
            ).fetchone()
            return None if row is None else self._finding_item(row, space_id)

    def system_document(
        self,
        request: LegacyPersonalReadRequest,
        name: str,
    ) -> dict[str, object] | None:
        """Generate bounded index/log compatibility views without file refresh."""

        if not isinstance(request, LegacyPersonalReadRequest):
            raise TypeError("A LegacyPersonalReadRequest is required.")
        if name not in {"index", "log"}:
            return None
        limit = _limit(request.limit, "limit", _MAX_ITEMS)
        with self._snapshot(request) as (connection, space_id, _actor_id):
            if name == "index":
                items = self._items(
                    connection, space_id=space_id, limit=_MAX_ITEMS
                )[:limit]
                lines = ["# Index", ""] + [
                    f"- {item.title} (`{item.item_id}`) [{item.curation_status}]"
                    for item in sorted(items, key=lambda value: (value.title.casefold(), value.item_id))
                ]
                updated = max((item.updated_at_ms for item in items), default=0)
                content = "\n".join(lines).rstrip() + "\n"
                title = "Index"
            else:
                event_rows = connection.execute(
                    """SELECT event_uuid,occurred_at,action,outcome
                         FROM audit_event WHERE space_id=?
                         ORDER BY occurred_at DESC,event_uuid DESC LIMIT 100""",
                    (space_id,),
                ).fetchall()
                lines = ["# Log", ""]
                for row in event_rows:
                    occurred_at = _integer(
                        row["occurred_at"], "audit occurrence timestamp"
                    )
                    lines.append(
                        f"- {occurred_at}: "
                        f"{_safe_text(row['action'], 'audit action', maximum=200)} "
                        f"[{_safe_text(row['outcome'], 'audit outcome', maximum=200)}]"
                    )
                updated = max(
                    (
                        _integer(row["occurred_at"], "audit occurrence timestamp")
                        for row in event_rows
                    ),
                    default=0,
                )
                content = "\n".join(lines).rstrip() + "\n"
                title = "Log"
            return {
                "id": f"system/{name}",
                "title": title,
                "filename": f"{name}.md",
                "kind": "system",
                "path": f"canonical://knowledge-spaces/{space_id}/system/{name}",
                "rel_path": f"system/{name}.md",
                "updated_at": updated // 1000,
                "content": content,
            }


__all__ = [
    "LegacyFindingReadRequest",
    "LegacyGraphReadRequest",
    "LegacyKnowledgeReadAdapter",
    "LegacyPersonalReadRequest",
    "LegacyReadCompatibilityError",
    "LegacyReadIntegrityError",
    "LegacyReadScopeError",
    "LegacyReadStateConflictError",
    "LegacySearchReadRequest",
]
