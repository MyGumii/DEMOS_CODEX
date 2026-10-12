"""Read-only, one-space canonical FTS retrieval.

The repository consumes one caller-owned database snapshot.  It never opens a
file projection, vector cache, legacy store, or second space.  ``search_document``
and its external-content FTS table are rebuildable indexes; current item state is
rechecked in every query before a row can leave the repository.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
import sqlite3
import unicodedata
from typing import Literal

from ..db import ManagedConnection
from ..errors import KnowledgeCoreError, KnowledgeCoreTransactionError


SemanticDirection = Literal["incoming", "outgoing"]
ProjectionStatus = Literal["missing", "idle", "building", "ready", "error"]
PinAvailability = Literal["available", "unavailable"]
RankedProvenance = Literal["fts", "one_hop", "two_hop"]
RetrievalQueryIntent = Literal[
    "topic", "overview", "continuation", "exhaustive", "unknown"
]

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)
_MAX_QUERY_CHARS = 500
_MAX_QUERY_TERMS = 32
_MAX_HITS = 20
_MAX_NEIGHBORS = 80
_MAX_EVENTS = 100
_NEIGHBOR_EXCERPT_CHARS = 2_000
_PIN_EXCERPT_CHARS = 8_000
_MAX_PINS = 20
_MAX_CLOSURE_SOURCES = 4096
_MAX_CLOSURE_OVERLAYS = 4096


class RetrievalRepositoryError(KnowledgeCoreError):
    code = "RETRIEVAL_REPOSITORY_ERROR"


class RetrievalQueryError(RetrievalRepositoryError):
    code = "VALIDATION_ERROR"


class RetrievalIntegrityError(RetrievalRepositoryError):
    code = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    item_id: str
    space_id: str
    revision_id: str
    title: str
    excerpt_md: str
    updated_at_ms: int
    rank: int
    bm25_score: float
    trust_boundary: Literal["untrusted_reference"] = "untrusted_reference"
    selected_view_space_id: str | None = None
    inherited: bool = False
    overlay_grant_id: str | None = None
    origin: Literal["native", "inherited", "overlay"] = "native"


@dataclass(frozen=True, slots=True)
class RetrievalSemanticNeighbor:
    edge_id: str
    edge_type: str
    direction: SemanticDirection
    seed_item_id: str
    neighbor_item_id: str
    neighbor_revision_id: str
    neighbor_title: str
    neighbor_excerpt_md: str
    needs_revalidation: bool
    trust_boundary: Literal["untrusted_reference"] = "untrusted_reference"
    space_id: str | None = None
    selected_view_space_id: str | None = None
    inherited: bool = False


@dataclass(frozen=True, slots=True)
class RetrievalItemEvent:
    event_uuid: str
    item_id: str
    action: str
    outcome: str
    base_revision_id: str | None
    new_revision_id: str | None
    occurred_at_ms: int
    space_id: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievalProjectionState:
    projection_name: Literal["markdown"]
    status: ProjectionStatus
    canonical_generation: int
    desired_generation: int
    built_generation: int
    canonical_fallback_used: bool


@dataclass(frozen=True, slots=True)
class RetrievalPinReference:
    item_id: str
    revision_id: str | None
    availability: PinAvailability
    title: str | None
    excerpt_md: str | None
    revision_content_sha256: str | None
    unavailable_reason: str | None
    trust_boundary: Literal["untrusted_reference"] = "untrusted_reference"


@dataclass(frozen=True, slots=True)
class WikiManifestEntry:
    """Bounded navigation metadata for one authorized published Wiki item."""

    item_id: str
    revision_id: str
    source_space_id: str
    title: str
    updated_at_ms: int
    origin: Literal["native", "inherited", "overlay"]
    inherited: bool
    overlay_grant_id: str | None
    title_truncated: bool = False
    trust_boundary: Literal["untrusted_reference"] = "untrusted_reference"


@dataclass(frozen=True, slots=True)
class WikiManifest:
    """Query-independent, authorization-bound replacement for legacy index.md."""

    selected_view_space_id: str
    authorized_published_count: int
    entries: tuple[WikiManifestEntry, ...]
    truncated: bool
    entry_cap: int


@dataclass(frozen=True, slots=True)
class RankedDocumentIdentity:
    item_id: str
    revision_id: str
    source_space_id: str


@dataclass(frozen=True, slots=True)
class RankedTemporalValue:
    precision: Literal["instant", "date"]
    instant_value_ms: int | None
    date_value: str | None
    source_timezone: str
    source_utc_offset_minutes: int | None


@dataclass(frozen=True, slots=True)
class RankedTemporalMetadata:
    registered_at_ms: int
    content_modified_at_ms: int
    temporal_summary_sha256: str
    source_modified: RankedTemporalValue | None
    information_effective: RankedTemporalValue | None
    selected_time_kind: Literal[
        "information_effective",
        "source_modified",
        "content_modified",
        "registered",
    ]
    selected_time_ms: int
    relative_state: Literal["scheduled", "current", "historical"]
    verification_current: bool
    last_verified_at_ms: int | None


@dataclass(frozen=True, slots=True)
class RankedDocument:
    item_id: str
    revision_id: str
    source_space_id: str
    selected_view_space_id: str
    title: str
    excerpt_md: str
    score_ppm: int
    rank: int
    provenance: tuple[RankedProvenance, ...]
    graph_hop: int
    relation_type: str | None
    relation_semantics: Literal[
        "direct_match", "supporting_context", "conflicting_evidence", "related_context"
    ]
    origin: Literal["native", "inherited", "overlay"]
    inherited: bool
    overlay_grant_id: str | None
    normalized_content_sha256: str
    alternate_identities: tuple[RankedDocumentIdentity, ...] = ()
    trust_boundary: Literal["untrusted_reference"] = "untrusted_reference"
    deterministic_score_ppm: int | None = None
    model_grade: int | None = None
    temporal_metadata: RankedTemporalMetadata | None = None


@dataclass(frozen=True, slots=True)
class SupplementalBodyDocument:
    """Authorized current body evidence outside the final ranked-search lane.

    These rows let an Agent inspect real Markdown for corpus-wide or follow-up
    questions without padding ``ranked_documents`` with lexically irrelevant
    hits. Every selected Wiki/view may expose all authorized bodies to the
    byte-budgeted Agent composer without a document-count cap.
    """

    item_id: str
    revision_id: str
    source_space_id: str
    selected_view_space_id: str
    title: str
    excerpt_md: str
    updated_at_ms: int
    selection_reason: Literal["continuation", "representative"]
    origin: Literal["native", "inherited", "overlay"]
    inherited: bool
    overlay_grant_id: str | None
    normalized_content_sha256: str
    trust_boundary: Literal["untrusted_reference"] = "untrusted_reference"


@dataclass(frozen=True, slots=True)
class HybridRankingEnvelope:
    pipeline_version: Literal["hybrid-rerank-v4-shared-preview-budget"]
    reranker_status: Literal["disabled", "applied", "fallback"]
    reranker_reason: Literal[
        "model_unavailable",
        "timeout",
        "oom",
        "malformed_output",
        "injected_output",
        "provider_failure",
        "input_bound",
    ] | None
    model_id: str | None
    model_revision_sha256: str
    prompt_revision_sha256: str
    input_sha256: str
    temporal_intent: Literal[
        "none", "current", "scheduled", "historical", "generic"
    ]
    as_of_ms: int
    as_of_explicit: bool
    cache_expires_at_ms: int
    next_effective_boundary_ms: int | None
    cache_hit: bool


@dataclass(frozen=True, slots=True)
class HybridRetrievalExplain:
    pipeline_version: Literal["canonical-hybrid-rerank-v4-shared-preview-budget"]
    direct_candidate_count: int
    one_hop_candidate_count: int
    two_hop_candidate_count: int
    pre_rank_candidate_count: int
    final_document_count: int
    two_hop_executed: bool
    sufficiency_gate_passed: bool
    cache_hit: bool
    direct_cap: int | None = 40
    one_hop_seed_cap: int = 12
    one_hop_per_seed_cap: int = 4
    two_hop_anchor_cap: int = 5
    two_hop_addition_cap: int = 20
    pre_rank_cap: int | None = 40
    final_cap: int | None = None


@dataclass(frozen=True, slots=True)
class GraphPreviewWork:
    """Internal graph-body work, not a full retrieval/SQL/process memory bound."""
    source_budget_bytes: int
    source_bytes: int
    source_bodies_read: int
    preview_reuses: int
    deferred_bodies: int
    preparation_enabled: bool = True


@dataclass(frozen=True, slots=True)
class CanonicalRetrievalSnapshot:
    space_id: str
    normalized_query: str
    hits: tuple[RetrievalHit, ...]
    semantic_neighbors: tuple[RetrievalSemanticNeighbor, ...]
    recent_events: tuple[RetrievalItemEvent, ...]
    projection: RetrievalProjectionState
    retrieval_source: Literal["canonical_fts"] = "canonical_fts"
    corpus_closure_sha256: str | None = None
    ranked_documents: tuple[RankedDocument, ...] = ()
    reranker_candidates: tuple[RankedDocument, ...] = ()
    hybrid_explain: HybridRetrievalExplain | None = None
    cache_identity_sha256: str | None = None
    ranking_envelope: HybridRankingEnvelope | None = None
    wiki_manifest: WikiManifest | None = None
    query_intent: RetrievalQueryIntent = "unknown"
    supplemental_documents: tuple[SupplementalBodyDocument, ...] = ()
    # Internal lexical plan, not a replacement for the user's semantic query.
    effective_search_query: str | None = None
    graph_preview_work: GraphPreviewWork | None = None
    reranker_evidence_complete: bool = True


def _connection(value: object) -> ManagedConnection:
    if not isinstance(value, ManagedConnection):
        raise TypeError("RetrievalRepository requires a ManagedConnection.")
    if not value.in_transaction:
        raise KnowledgeCoreTransactionError(
            "Canonical retrieval requires one caller-owned transaction."
        )
    return value


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise RetrievalQueryError(f"{field} must be a canonical lowercase UUID.")
    return value


def _limit(value: object, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise RetrievalQueryError(f"{field} must be an integer from 0 through {maximum}.")
    return value


def _normalize_query(value: object) -> tuple[str, str]:
    if not isinstance(value, str):
        raise RetrievalQueryError("query must be text.")
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized or len(normalized) > _MAX_QUERY_CHARS:
        raise RetrievalQueryError("query must contain from 1 through 500 characters.")
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise RetrievalQueryError("query contains a disallowed control character.")
    chunks = tuple(
        chunk for chunk in normalized.split()
        if chunk and any(character.isalnum() for character in chunk)
    )
    if not chunks or len(chunks) > _MAX_QUERY_TERMS:
        raise RetrievalQueryError("query must contain from 1 through 32 searchable terms.")
    # Each term is an FTS literal.  Operators, column selectors, prefixes, and
    # parentheses supplied by the caller therefore have no query-language authority.
    literals = tuple('"' + chunk.replace('"', '""') + '"' for chunk in chunks)
    return " ".join(chunks), " OR ".join(literals)


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RetrievalIntegrityError(f"Stored {field} is invalid.")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise RetrievalIntegrityError(f"Stored {field} is invalid.")
    return value


def _optional_uuid(value: object, field: str) -> str | None:
    return None if value is None else _uuid(value, field)


def _retrieval_intent(value: object) -> RetrievalQueryIntent:
    if value not in {"topic", "overview", "continuation", "exhaustive", "unknown"}:
        raise RetrievalQueryError("retrieval_intent is invalid.")
    return value  # type: ignore[return-value]


class RetrievalRepository:
    """Build one bounded retrieval snapshot from canonical DB state."""

    __slots__ = ()

    def retrieve(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        query: str,
        search_query: str | None = None,
        top_k: int = 8,
        neighbor_limit: int = 16,
        event_limit: int = 20,
        retrieval_intent: RetrievalQueryIntent = "unknown",
        continuation_documents: tuple[RankedDocumentIdentity, ...] = (),
    ) -> CanonicalRetrievalSnapshot:
        connection = _connection(connection)
        space_id = _uuid(space_id, "space_id")
        top_k = _limit(top_k, "top_k", _MAX_HITS)
        neighbor_limit = _limit(neighbor_limit, "neighbor_limit", _MAX_NEIGHBORS)
        event_limit = _limit(event_limit, "event_limit", _MAX_EVENTS)
        retrieval_intent = _retrieval_intent(retrieval_intent)
        normalized_query, _ = _normalize_query(query)
        _, fts_query = _normalize_query(
            normalized_query if search_query is None else search_query
        )
        projection = self._projection_state(connection, space_id)
        if top_k == 0:
            return CanonicalRetrievalSnapshot(
                space_id=space_id,
                normalized_query=normalized_query,
                hits=(),
                semantic_neighbors=(),
                recent_events=(),
                projection=projection,
                query_intent=retrieval_intent,
            )
        hits = self._hits(connection, space_id, fts_query, top_k)
        neighbors = self._neighbors(
            connection,
            space_id,
            tuple(hit.item_id for hit in hits),
            neighbor_limit,
        )
        relevant_ids = tuple(
            dict.fromkeys(
                [hit.item_id for hit in hits]
                + [neighbor.neighbor_item_id for neighbor in neighbors]
            )
        )
        events = self._events(connection, space_id, relevant_ids, event_limit)
        return CanonicalRetrievalSnapshot(
            space_id=space_id,
            normalized_query=normalized_query,
            hits=hits,
            semantic_neighbors=neighbors,
            recent_events=events,
            projection=projection,
            query_intent=retrieval_intent,
        )

    def retrieve_closure(
        self,
        connection: ManagedConnection,
        *,
        selected_space_id: str,
        source_space_ids: tuple[str, ...],
        overlays: tuple[dict[str, str], ...],
        corpus_closure_sha256: str,
        query: str,
        search_query: str | None = None,
        top_k: int = 8,
        neighbor_limit: int = 16,
        event_limit: int = 20,
        retrieval_intent: RetrievalQueryIntent = "unknown",
        continuation_documents: tuple[RankedDocumentIdentity, ...] = (),
    ) -> CanonicalRetrievalSnapshot:
        """Retrieve from one exact server-resolved ``CorpusClosure``."""

        connection = _connection(connection)
        selected_space_id = _uuid(selected_space_id, "selected_space_id")
        if (
            not isinstance(source_space_ids, tuple)
            or not 1 <= len(source_space_ids) <= _MAX_CLOSURE_SOURCES
        ):
            raise RetrievalQueryError("The corpus source closure is invalid.")
        sources = tuple(
            dict.fromkeys(_uuid(value, "source_space_id") for value in source_space_ids)
        )
        if len(sources) != len(source_space_ids) or selected_space_id not in sources:
            raise RetrievalQueryError("The corpus source closure is incoherent.")
        if not isinstance(overlays, tuple) or len(overlays) > _MAX_CLOSURE_OVERLAYS:
            raise RetrievalQueryError("The distribution overlay closure is invalid.")
        normalized_overlays: list[dict[str, str]] = []
        for value in overlays:
            if not isinstance(value, dict) or set(value) != {
                "grant_id",
                "source_item_id",
                "source_revision_id",
                "source_space_id",
            }:
                raise RetrievalQueryError("A distribution overlay is invalid.")
            normalized_overlays.append(
                {
                    key: _uuid(value[key], key)
                    for key in (
                        "grant_id",
                        "source_item_id",
                        "source_revision_id",
                        "source_space_id",
                    )
                }
            )
        if (
            not isinstance(corpus_closure_sha256, str)
            or len(corpus_closure_sha256) != 64
            or any(character not in "0123456789abcdef" for character in corpus_closure_sha256)
        ):
            raise RetrievalQueryError("The corpus closure proof is invalid.")
        top_k = _limit(top_k, "top_k", _MAX_HITS)
        neighbor_limit = _limit(neighbor_limit, "neighbor_limit", _MAX_NEIGHBORS)
        event_limit = _limit(event_limit, "event_limit", _MAX_EVENTS)
        retrieval_intent = _retrieval_intent(retrieval_intent)
        normalized_query, _ = _normalize_query(query)
        _, fts_query = _normalize_query(
            normalized_query if search_query is None else search_query
        )
        projection = self._projection_state(connection, selected_space_id)
        if top_k == 0:
            return CanonicalRetrievalSnapshot(
                space_id=selected_space_id,
                normalized_query=normalized_query,
                hits=(),
                semantic_neighbors=(),
                recent_events=(),
                projection=projection,
                corpus_closure_sha256=corpus_closure_sha256,
                query_intent=retrieval_intent,
            )
        source_json = json.dumps(sources, separators=(",", ":"))
        overlay_json = json.dumps(normalized_overlays, separators=(",", ":"))
        hits = self._closure_hits(
            connection,
            selected_space_id=selected_space_id,
            source_json=source_json,
            overlay_json=overlay_json,
            fts_query=fts_query,
            limit=top_k,
        )
        neighbors = self._closure_neighbors(
            connection,
            selected_space_id=selected_space_id,
            source_json=source_json,
            seed_ids=tuple(hit.item_id for hit in hits),
            limit=neighbor_limit,
        )
        relevant_ids = tuple(
            dict.fromkeys(
                [hit.item_id for hit in hits]
                + [neighbor.neighbor_item_id for neighbor in neighbors]
            )
        )
        events = self._closure_events(
            connection,
            source_json=source_json,
            overlay_json=overlay_json,
            item_ids=relevant_ids,
            limit=event_limit,
        )
        return CanonicalRetrievalSnapshot(
            space_id=selected_space_id,
            normalized_query=normalized_query,
            hits=hits,
            semantic_neighbors=neighbors,
            recent_events=events,
            projection=projection,
            corpus_closure_sha256=corpus_closure_sha256,
            query_intent=retrieval_intent,
        )

    def conversation_pins(
        self,
        connection: ManagedConnection,
        *,
        conversation_id: str,
        space_id: str,
        limit: int = _MAX_PINS,
    ) -> tuple[RetrievalPinReference, ...]:
        """Return every attached pin with an explicit per-turn validity result."""

        connection = _connection(connection)
        conversation_id = _uuid(conversation_id, "conversation_id")
        space_id = _uuid(space_id, "space_id")
        limit = _limit(limit, "pin_limit", _MAX_PINS)
        rows = connection.execute(
            f"""SELECT p.space_id,p.item_id,p.revision_id,
                       i.lifecycle_status,i.curation_status,i.current_revision_id,
                       r.title,r.id AS stored_revision_id,
                       CASE WHEN b.content_md IS NULL THEN 1
                            ELSE substr(b.content_md,1,{_PIN_EXCERPT_CHARS})=
                                 substr(d.content_md,1,{_PIN_EXCERPT_CHARS})
                        END AS revision_prefix_matches,
                       b.content_blob_hash,b.content_sha256,
                       d.revision_id AS search_revision_id,d.title AS search_title,
                       substr(d.content_md,1,{_PIN_EXCERPT_CHARS}) AS excerpt_md
                  FROM conversation_pin AS p
                  LEFT JOIN knowledge_item AS i ON i.id=p.item_id
                  LEFT JOIN knowledge_revision AS r
                    ON r.id=p.revision_id AND r.item_id=p.item_id
                  LEFT JOIN knowledge_revision_body AS b
                    ON b.revision_id=p.revision_id AND b.item_id=p.item_id
                  LEFT JOIN search_document AS d
                    ON d.item_id=p.item_id AND d.space_id=p.space_id
                   AND d.revision_id=p.revision_id
                 WHERE p.conversation_id=?
                 ORDER BY p.created_at,p.item_id LIMIT ?""",
            (conversation_id, limit + 1),
        ).fetchall()
        if len(rows) > limit:
            raise RetrievalQueryError(
                f"A conversation may contribute at most {limit} pins per turn."
            )
        result: list[RetrievalPinReference] = []
        for row in rows:
            observed_space = _uuid(row["space_id"], "pin space_id")
            if observed_space != space_id:
                raise RetrievalIntegrityError(
                    "A conversation pin crosses the authorized space."
                )
            item_id = _uuid(row["item_id"], "pin item_id")
            revision_id = _optional_uuid(row["revision_id"], "pin revision_id")
            reason: str | None = None
            if row["lifecycle_status"] != "active" or row["curation_status"] != "published":
                reason = "item_not_visible"
            elif revision_id is None or row["current_revision_id"] != revision_id:
                reason = "revision_not_current"
            elif row["stored_revision_id"] != revision_id or row["content_sha256"] is None:
                reason = "revision_body_unavailable"
            elif (
                row["search_revision_id"] != revision_id
                or row["search_title"] != row["title"]
                or row["excerpt_md"] is None
            ):
                reason = "search_document_unavailable"
            elif not row["revision_prefix_matches"]:
                raise RetrievalIntegrityError(
                    "A pinned search document does not match its revision body."
                )
            if reason is not None:
                result.append(
                    RetrievalPinReference(
                        item_id=item_id,
                        revision_id=revision_id,
                        availability="unavailable",
                        title=None,
                        excerpt_md=None,
                        revision_content_sha256=None,
                        unavailable_reason=reason,
                    )
                )
                continue
            result.append(
                RetrievalPinReference(
                    item_id=item_id,
                    revision_id=revision_id,
                    availability="available",
                    title=_text(row["title"], "pin title"),
                    excerpt_md=_text(row["excerpt_md"], "pin excerpt"),
                    revision_content_sha256=_text(
                        row["content_sha256"], "pin content SHA-256"
                    ),
                    unavailable_reason=None,
                )
            )
        return tuple(result)

    @staticmethod
    def _projection_state(
        connection: ManagedConnection, space_id: str
    ) -> RetrievalProjectionState:
        space = connection.execute(
            "SELECT generation FROM knowledge_space WHERE id=?", (space_id,)
        ).fetchone()
        if space is None:
            raise RetrievalIntegrityError("The authorized knowledge space is absent.")
        generation = _integer(space["generation"], "space generation")
        row = connection.execute(
            """SELECT desired_generation,built_generation,status
                 FROM projection_state
                WHERE projection_name='markdown' AND space_id=?""",
            (space_id,),
        ).fetchone()
        if row is None:
            return RetrievalProjectionState(
                projection_name="markdown",
                status="missing",
                canonical_generation=generation,
                desired_generation=generation,
                built_generation=0,
                canonical_fallback_used=True,
            )
        desired = _integer(row["desired_generation"], "desired generation")
        built = _integer(row["built_generation"], "built generation")
        status = row["status"]
        if status not in {"idle", "building", "ready", "error"}:
            raise RetrievalIntegrityError("Stored projection status is invalid.")
        fallback = status != "ready" or built < generation or desired < generation
        return RetrievalProjectionState(
            projection_name="markdown",
            status=status,
            canonical_generation=generation,
            desired_generation=desired,
            built_generation=built,
            canonical_fallback_used=fallback,
        )

    @staticmethod
    def _hits(
        connection: ManagedConnection,
        space_id: str,
        fts_query: str,
        limit: int,
    ) -> tuple[RetrievalHit, ...]:
        try:
            rows = connection.execute(
                """SELECT d.item_id,d.space_id,d.revision_id,d.title,d.updated_at,
                          bm25(knowledge_fts,6.0,1.0) AS bm25_score,
                          snippet(knowledge_fts,1,'','',' ... ',48) AS excerpt_md
                     FROM knowledge_fts
                     JOIN search_document AS d ON d.rowid=knowledge_fts.rowid
                     JOIN knowledge_item AS i
                       ON i.id=d.item_id AND i.space_id=d.space_id
                     JOIN knowledge_revision AS r
                       ON r.id=d.revision_id AND r.item_id=d.item_id
                     JOIN knowledge_revision_body AS b
                       ON b.revision_id=d.revision_id AND b.item_id=d.item_id
                    WHERE knowledge_fts MATCH ? AND d.space_id=?
                      AND i.lifecycle_status='active'
                      AND i.curation_status='published'
                      AND i.current_revision_id=d.revision_id
                      AND d.title=r.title
                      AND (b.content_md IS NULL OR d.content_md=b.content_md)
                    ORDER BY bm25_score ASC,d.updated_at DESC,d.item_id ASC
                    LIMIT ?""",
                (fts_query, space_id, limit),
            ).fetchall()
        except sqlite3.Error as exc:
            raise RetrievalRepositoryError("Canonical FTS retrieval failed.") from exc
        result: list[RetrievalHit] = []
        for rank, row in enumerate(rows, start=1):
            score = row["bm25_score"]
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise RetrievalIntegrityError("Stored FTS rank is invalid.")
            score = float(score)
            if not math.isfinite(score):
                raise RetrievalIntegrityError("Stored FTS rank is not finite.")
            observed_space = _uuid(row["space_id"], "stored space_id")
            if observed_space != space_id:
                raise RetrievalIntegrityError("FTS returned a row from another space.")
            result.append(
                RetrievalHit(
                    item_id=_uuid(row["item_id"], "stored item_id"),
                    space_id=observed_space,
                    revision_id=_uuid(row["revision_id"], "stored revision_id"),
                    title=_text(row["title"], "title"),
                    excerpt_md=_text(row["excerpt_md"], "excerpt"),
                    updated_at_ms=_integer(row["updated_at"], "search timestamp"),
                    rank=rank,
                    bm25_score=score,
                )
            )
        return tuple(result)

    @staticmethod
    def _closure_hits(
        connection: ManagedConnection,
        *,
        selected_space_id: str,
        source_json: str,
        overlay_json: str,
        fts_query: str,
        limit: int,
    ) -> tuple[RetrievalHit, ...]:
        try:
            rows = connection.execute(
                """WITH authorized_sources(space_id) AS (
                     SELECT value FROM json_each(?)
                   ), authorized_overlays(
                     grant_id,source_space_id,source_item_id,source_revision_id
                   ) AS (
                     SELECT json_extract(value,'$.grant_id'),
                            json_extract(value,'$.source_space_id'),
                            json_extract(value,'$.source_item_id'),
                            json_extract(value,'$.source_revision_id')
                       FROM json_each(?)
                   )
                   SELECT d.item_id,d.space_id,d.revision_id,d.title,d.updated_at,
                          bm25(knowledge_fts,6.0,1.0) AS bm25_score,
                          snippet(knowledge_fts,1,'','',' ... ',48) AS excerpt_md,
                          (SELECT overlay.grant_id
                             FROM authorized_overlays AS overlay
                            WHERE overlay.source_space_id=d.space_id
                              AND overlay.source_item_id=d.item_id
                              AND overlay.source_revision_id=d.revision_id
                            LIMIT 1) AS overlay_grant_id
                     FROM knowledge_fts
                     JOIN search_document AS d ON d.rowid=knowledge_fts.rowid
                     JOIN knowledge_item AS i
                       ON i.id=d.item_id AND i.space_id=d.space_id
                     JOIN knowledge_revision AS r
                       ON r.id=d.revision_id AND r.item_id=d.item_id
                     JOIN knowledge_revision_body AS b
                       ON b.revision_id=d.revision_id AND b.item_id=d.item_id
                    WHERE knowledge_fts MATCH ?
                      AND (
                        EXISTS (SELECT 1 FROM authorized_sources AS source
                                 WHERE source.space_id=d.space_id)
                        OR EXISTS (
                          SELECT 1 FROM authorized_overlays AS overlay
                           WHERE overlay.source_space_id=d.space_id
                             AND overlay.source_item_id=d.item_id
                             AND overlay.source_revision_id=d.revision_id
                        )
                      )
                      AND i.lifecycle_status='active'
                      AND i.curation_status='published'
                      AND i.current_revision_id=d.revision_id
                      AND d.title=r.title
                      AND (b.content_md IS NULL OR d.content_md=b.content_md)
                    ORDER BY bm25_score ASC,d.updated_at DESC,d.item_id ASC
                    LIMIT ?""",
                (source_json, overlay_json, fts_query, limit),
            ).fetchall()
        except sqlite3.Error as exc:
            raise RetrievalRepositoryError(
                "Canonical closure FTS retrieval failed."
            ) from exc
        sources = frozenset(json.loads(source_json))
        result: list[RetrievalHit] = []
        for rank, row in enumerate(rows, start=1):
            score = row["bm25_score"]
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise RetrievalIntegrityError("Stored FTS rank is invalid.")
            score = float(score)
            if not math.isfinite(score):
                raise RetrievalIntegrityError("Stored FTS rank is not finite.")
            observed_space = _uuid(row["space_id"], "stored space_id")
            overlay_grant = _optional_uuid(
                row["overlay_grant_id"], "overlay grant_id"
            )
            if observed_space not in sources and overlay_grant is None:
                raise RetrievalIntegrityError(
                    "FTS returned a row outside the authorized corpus closure."
                )
            origin: Literal["native", "inherited", "overlay"] = (
                "overlay"
                if overlay_grant is not None
                else "native"
                if observed_space == selected_space_id
                else "inherited"
            )
            result.append(
                RetrievalHit(
                    item_id=_uuid(row["item_id"], "stored item_id"),
                    space_id=observed_space,
                    revision_id=_uuid(row["revision_id"], "stored revision_id"),
                    title=_text(row["title"], "title"),
                    excerpt_md=_text(row["excerpt_md"], "excerpt"),
                    updated_at_ms=_integer(row["updated_at"], "search timestamp"),
                    rank=rank,
                    bm25_score=score,
                    selected_view_space_id=selected_space_id,
                    inherited=observed_space != selected_space_id,
                    overlay_grant_id=overlay_grant,
                    origin=origin,
                )
            )
        return tuple(result)

    @staticmethod
    def _closure_neighbors(
        connection: ManagedConnection,
        *,
        selected_space_id: str,
        source_json: str,
        seed_ids: tuple[str, ...],
        limit: int,
    ) -> tuple[RetrievalSemanticNeighbor, ...]:
        if not seed_ids or limit == 0:
            return ()
        placeholders = ",".join("?" for _ in seed_ids)
        priority = " ".join(
            f"WHEN e.source_item_id=? OR e.target_item_id=? THEN {rank}"
            for rank, _item_id in enumerate(seed_ids)
        )
        parameters: list[object] = []
        for item_id in seed_ids:
            parameters.extend((item_id, item_id))
        parameters.extend(
            (
                source_json,
                *seed_ids,
                *seed_ids,
                min(512, max(limit * 8, limit)),
            )
        )
        rows = connection.execute(
            f"""SELECT e.id,e.space_id,e.edge_type,e.source_item_id,
                         e.target_item_id,e.needs_revalidation,e.updated_at,
                         sd.revision_id AS source_revision_id,
                         sd.title AS source_title,
                         substr(sd.content_md,1,{_NEIGHBOR_EXCERPT_CHARS})
                           AS source_excerpt,
                         td.revision_id AS target_revision_id,
                         td.title AS target_title,
                         substr(td.content_md,1,{_NEIGHBOR_EXCERPT_CHARS})
                           AS target_excerpt,
                         CASE {priority} ELSE {len(seed_ids)} END AS seed_rank
                    FROM knowledge_edge AS e
                    JOIN knowledge_item AS si
                      ON si.id=e.source_item_id AND si.space_id=e.space_id
                    JOIN knowledge_item AS ti
                      ON ti.id=e.target_item_id AND ti.space_id=e.space_id
                    JOIN search_document AS sd
                      ON sd.item_id=si.id AND sd.space_id=si.space_id
                     AND sd.revision_id=si.current_revision_id
                    JOIN knowledge_revision AS sr
                      ON sr.id=sd.revision_id AND sr.item_id=sd.item_id
                    JOIN knowledge_revision_body AS sb
                      ON sb.revision_id=sd.revision_id AND sb.item_id=sd.item_id
                    JOIN search_document AS td
                      ON td.item_id=ti.id AND td.space_id=ti.space_id
                     AND td.revision_id=ti.current_revision_id
                    JOIN knowledge_revision AS tr
                      ON tr.id=td.revision_id AND tr.item_id=td.item_id
                    JOIN knowledge_revision_body AS tb
                      ON tb.revision_id=td.revision_id AND tb.item_id=td.item_id
                   WHERE EXISTS (
                           SELECT 1 FROM json_each(?) AS source
                            WHERE source.value=e.space_id
                         )
                     AND e.edge_status='active'
                     AND si.lifecycle_status='active'
                     AND si.curation_status='published'
                     AND ti.lifecycle_status='active'
                     AND ti.curation_status='published'
                     AND sd.title=sr.title
                     AND (sb.content_md IS NULL OR sd.content_md=sb.content_md)
                     AND td.title=tr.title
                     AND (tb.content_md IS NULL OR td.content_md=tb.content_md)
                     AND (e.source_item_id IN ({placeholders})
                          OR e.target_item_id IN ({placeholders}))
                   ORDER BY seed_rank ASC,e.updated_at DESC,e.id ASC LIMIT ?""",
            tuple(parameters),
        ).fetchall()
        rank_by_id = {item_id: rank for rank, item_id in enumerate(seed_ids)}
        allowed_spaces = frozenset(json.loads(source_json))
        result: list[RetrievalSemanticNeighbor] = []
        for row in rows:
            edge_space = _uuid(row["space_id"], "edge space_id")
            if edge_space not in allowed_spaces:
                raise RetrievalIntegrityError(
                    "A neighbor escaped the authorized source closure."
                )
            source = _uuid(row["source_item_id"], "edge source item")
            target = _uuid(row["target_item_id"], "edge target item")
            if source in rank_by_id and (
                target not in rank_by_id
                or rank_by_id[source] <= rank_by_id[target]
            ):
                direction: SemanticDirection = "outgoing"
                seed, neighbor = source, target
                revision = row["target_revision_id"]
                title, excerpt = row["target_title"], row["target_excerpt"]
            elif target in rank_by_id:
                direction = "incoming"
                seed, neighbor = target, source
                revision = row["source_revision_id"]
                title, excerpt = row["source_title"], row["source_excerpt"]
            else:  # pragma: no cover - SQL seed predicate
                raise RetrievalIntegrityError(
                    "A graph row is not bound to a search hit."
                )
            result.append(
                RetrievalSemanticNeighbor(
                    edge_id=_uuid(row["id"], "edge_id"),
                    edge_type=_text(row["edge_type"], "edge type"),
                    direction=direction,
                    seed_item_id=seed,
                    neighbor_item_id=neighbor,
                    neighbor_revision_id=_uuid(revision, "neighbor revision"),
                    neighbor_title=_text(title, "neighbor title"),
                    neighbor_excerpt_md=_text(excerpt, "neighbor excerpt"),
                    needs_revalidation=row["needs_revalidation"] == 1,
                    space_id=edge_space,
                    selected_view_space_id=selected_space_id,
                    inherited=edge_space != selected_space_id,
                )
            )
            if len(result) == limit:
                break
        return tuple(result)

    @staticmethod
    def _closure_events(
        connection: ManagedConnection,
        *,
        source_json: str,
        overlay_json: str,
        item_ids: tuple[str, ...],
        limit: int,
    ) -> tuple[RetrievalItemEvent, ...]:
        if not item_ids or limit == 0:
            return ()
        placeholders = ",".join("?" for _ in item_ids)
        rows = connection.execute(
            f"""WITH authorized_sources(space_id) AS (
                   SELECT value FROM json_each(?)
                 ), authorized_overlays(source_space_id,source_item_id) AS (
                   SELECT json_extract(value,'$.source_space_id'),
                          json_extract(value,'$.source_item_id')
                     FROM json_each(?)
                 )
                 SELECT audit.event_uuid,audit.space_id,audit.item_id,
                        audit.action,audit.outcome,audit.base_revision_id,
                        audit.new_revision_id,audit.occurred_at
                   FROM audit_event AS audit
                   JOIN knowledge_item AS item
                     ON item.id=audit.item_id AND item.space_id=audit.space_id
                  WHERE audit.item_id IN ({placeholders})
                    AND (
                      EXISTS (SELECT 1 FROM authorized_sources AS source
                               WHERE source.space_id=audit.space_id)
                      OR EXISTS (
                        SELECT 1 FROM authorized_overlays AS overlay
                         WHERE overlay.source_space_id=audit.space_id
                           AND overlay.source_item_id=audit.item_id
                      )
                    )
                    AND (audit.base_revision_id IS NULL OR EXISTS (
                          SELECT 1 FROM knowledge_revision AS br
                           WHERE br.id=audit.base_revision_id
                             AND br.item_id=audit.item_id
                        ))
                    AND (audit.new_revision_id IS NULL OR EXISTS (
                          SELECT 1 FROM knowledge_revision AS nr
                           WHERE nr.id=audit.new_revision_id
                             AND nr.item_id=audit.item_id
                        ))
                  ORDER BY audit.occurred_at DESC,audit.id DESC LIMIT ?""",
            (source_json, overlay_json, *item_ids, limit),
        ).fetchall()
        allowed = frozenset(item_ids)
        result: list[RetrievalItemEvent] = []
        for row in rows:
            item_id = _uuid(row["item_id"], "event item_id")
            if item_id not in allowed:
                raise RetrievalIntegrityError(
                    "An unrelated item event was returned."
                )
            result.append(
                RetrievalItemEvent(
                    event_uuid=_uuid(row["event_uuid"], "event_uuid"),
                    item_id=item_id,
                    action=_text(row["action"], "event action"),
                    outcome=_text(row["outcome"], "event outcome"),
                    base_revision_id=_optional_uuid(
                        row["base_revision_id"], "base_revision_id"
                    ),
                    new_revision_id=_optional_uuid(
                        row["new_revision_id"], "new_revision_id"
                    ),
                    occurred_at_ms=_integer(
                        row["occurred_at"], "event timestamp"
                    ),
                    space_id=_uuid(row["space_id"], "event space_id"),
                )
            )
        return tuple(result)

    @staticmethod
    def _neighbors(
        connection: ManagedConnection,
        space_id: str,
        seed_ids: tuple[str, ...],
        limit: int,
    ) -> tuple[RetrievalSemanticNeighbor, ...]:
        if not seed_ids or limit == 0:
            return ()
        placeholders = ",".join("?" for _ in seed_ids)
        priority = " ".join(
            f"WHEN e.source_item_id=? OR e.target_item_id=? THEN {rank}"
            for rank, _item_id in enumerate(seed_ids)
        )
        parameters: list[object] = []
        for item_id in seed_ids:
            parameters.extend((item_id, item_id))
        parameters.extend((space_id, *seed_ids, *seed_ids, min(512, max(limit * 8, limit))))
        sql = f"""SELECT e.id,e.edge_type,e.source_item_id,e.target_item_id,
                         e.needs_revalidation,e.updated_at,
                         sd.revision_id AS source_revision_id,
                         sd.title AS source_title,
                         substr(sd.content_md,1,{_NEIGHBOR_EXCERPT_CHARS}) AS source_excerpt,
                         td.revision_id AS target_revision_id,
                         td.title AS target_title,
                         substr(td.content_md,1,{_NEIGHBOR_EXCERPT_CHARS}) AS target_excerpt,
                         CASE {priority} ELSE {len(seed_ids)} END AS seed_rank
                    FROM knowledge_edge AS e
                    JOIN knowledge_item AS si
                      ON si.id=e.source_item_id AND si.space_id=e.space_id
                    JOIN knowledge_item AS ti
                      ON ti.id=e.target_item_id AND ti.space_id=e.space_id
                    JOIN search_document AS sd
                      ON sd.item_id=si.id AND sd.space_id=si.space_id
                     AND sd.revision_id=si.current_revision_id
                    JOIN knowledge_revision AS sr
                      ON sr.id=sd.revision_id AND sr.item_id=sd.item_id
                    JOIN knowledge_revision_body AS sb
                      ON sb.revision_id=sd.revision_id AND sb.item_id=sd.item_id
                    JOIN search_document AS td
                      ON td.item_id=ti.id AND td.space_id=ti.space_id
                     AND td.revision_id=ti.current_revision_id
                    JOIN knowledge_revision AS tr
                      ON tr.id=td.revision_id AND tr.item_id=td.item_id
                    JOIN knowledge_revision_body AS tb
                      ON tb.revision_id=td.revision_id AND tb.item_id=td.item_id
                   WHERE e.space_id=? AND e.edge_status='active'
                     AND si.lifecycle_status='active' AND si.curation_status='published'
                     AND ti.lifecycle_status='active' AND ti.curation_status='published'
                     AND sd.title=sr.title
                     AND (sb.content_md IS NULL OR sd.content_md=sb.content_md)
                     AND td.title=tr.title
                     AND (tb.content_md IS NULL OR td.content_md=tb.content_md)
                     AND (e.source_item_id IN ({placeholders})
                          OR e.target_item_id IN ({placeholders}))
                   ORDER BY seed_rank ASC,e.updated_at DESC,e.id ASC LIMIT ?"""
        rows = connection.execute(sql, tuple(parameters)).fetchall()
        rank_by_id = {item_id: rank for rank, item_id in enumerate(seed_ids)}
        result: list[RetrievalSemanticNeighbor] = []
        for row in rows:
            source = _uuid(row["source_item_id"], "edge source item")
            target = _uuid(row["target_item_id"], "edge target item")
            if source in rank_by_id and (
                target not in rank_by_id or rank_by_id[source] <= rank_by_id[target]
            ):
                direction: SemanticDirection = "outgoing"
                seed = source
                neighbor = target
                revision = row["target_revision_id"]
                title = row["target_title"]
                excerpt = row["target_excerpt"]
            elif target in rank_by_id:
                direction = "incoming"
                seed = target
                neighbor = source
                revision = row["source_revision_id"]
                title = row["source_title"]
                excerpt = row["source_excerpt"]
            else:
                raise RetrievalIntegrityError("A graph row is not bound to a search hit.")
            result.append(
                RetrievalSemanticNeighbor(
                    edge_id=_uuid(row["id"], "edge_id"),
                    edge_type=_text(row["edge_type"], "edge type"),
                    direction=direction,
                    seed_item_id=seed,
                    neighbor_item_id=neighbor,
                    neighbor_revision_id=_uuid(revision, "neighbor revision"),
                    neighbor_title=_text(title, "neighbor title"),
                    neighbor_excerpt_md=_text(excerpt, "neighbor excerpt"),
                    needs_revalidation=row["needs_revalidation"] == 1,
                )
            )
            if len(result) == limit:
                break
        return tuple(result)

    @staticmethod
    def _events(
        connection: ManagedConnection,
        space_id: str,
        item_ids: tuple[str, ...],
        limit: int,
    ) -> tuple[RetrievalItemEvent, ...]:
        if not item_ids or limit == 0:
            return ()
        placeholders = ",".join("?" for _ in item_ids)
        rows = connection.execute(
            f"""SELECT event_uuid,item_id,action,outcome,base_revision_id,
                       new_revision_id,occurred_at
                  FROM audit_event
                 WHERE space_id=? AND item_id IN ({placeholders})
                   AND (base_revision_id IS NULL OR EXISTS (
                         SELECT 1 FROM knowledge_revision AS br
                          WHERE br.id=audit_event.base_revision_id
                            AND br.item_id=audit_event.item_id
                       ))
                   AND (new_revision_id IS NULL OR EXISTS (
                         SELECT 1 FROM knowledge_revision AS nr
                          WHERE nr.id=audit_event.new_revision_id
                            AND nr.item_id=audit_event.item_id
                       ))
                 ORDER BY occurred_at DESC,id DESC LIMIT ?""",
            (space_id, *item_ids, limit),
        ).fetchall()
        allowed = frozenset(item_ids)
        result: list[RetrievalItemEvent] = []
        for row in rows:
            item_id = _uuid(row["item_id"], "event item_id")
            if item_id not in allowed:
                raise RetrievalIntegrityError("An unrelated item event was returned.")
            result.append(
                RetrievalItemEvent(
                    event_uuid=_uuid(row["event_uuid"], "event_uuid"),
                    item_id=item_id,
                    action=_text(row["action"], "event action"),
                    outcome=_text(row["outcome"], "event outcome"),
                    base_revision_id=_optional_uuid(
                        row["base_revision_id"], "base_revision_id"
                    ),
                    new_revision_id=_optional_uuid(
                        row["new_revision_id"], "new_revision_id"
                    ),
                    occurred_at_ms=_integer(row["occurred_at"], "event timestamp"),
                )
            )
        return tuple(result)


__all__ = [
    "CanonicalRetrievalSnapshot",
    "HybridRankingEnvelope",
    "HybridRetrievalExplain",
    "RankedDocument",
    "RankedDocumentIdentity",
    "RankedTemporalMetadata",
    "RankedTemporalValue",
    "RetrievalHit",
    "RetrievalIntegrityError",
    "RetrievalItemEvent",
    "RetrievalProjectionState",
    "RetrievalPinReference",
    "RetrievalQueryIntent",
    "RetrievalQueryError",
    "RetrievalRepository",
    "RetrievalRepositoryError",
    "RetrievalSemanticNeighbor",
    "SupplementalBodyDocument",
    "WikiManifest",
    "WikiManifestEntry",
]
