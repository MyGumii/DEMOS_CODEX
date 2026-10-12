"""Authorized and deterministic hybrid retrieval.

The repository owns the ``canonical-hybrid-rerank-v4-shared-preview-budget`` fallback
pipeline.  It never broadens the caller supplied corpus closure and returns
only current published revisions. Every selected Wiki/view scans its complete
authorized lexical corpus; graph expansion retains cycle/hop safety bounds and
the final Agent safety boundary is a byte budget rather than a document-count
cap. The optional model reranker is deliberately a later service-layer concern.
"""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
import math
import sqlite3
from typing import Callable, Iterable, Literal, Mapping

from ..db import ManagedConnection
from ..evidence_passages import select_evidence_passages
from .retrieval import (
    CanonicalRetrievalSnapshot,
    GraphPreviewWork,
    HybridRetrievalExplain,
    RankedDocument,
    RankedDocumentIdentity,
    RetrievalQueryIntent,
    RetrievalHit,
    RetrievalIntegrityError,
    RetrievalQueryError,
    RetrievalRepository,
    RetrievalRepositoryError,
    SupplementalBodyDocument,
    WikiManifest,
    WikiManifestEntry,
    _connection,
    _integer,
    _limit,
    _normalize_query,
    _optional_uuid,
    _text,
    _uuid,
)


PIPELINE_VERSION = "canonical-hybrid-rerank-v4-shared-preview-budget"
DIRECT_CAP = 40
ONE_HOP_SEED_CAP = 12
ONE_HOP_PER_SEED_CAP = 4
TWO_HOP_ANCHOR_CAP = 5
TWO_HOP_ADDITION_CAP = 20
PRE_RANK_CAP = 40
FINAL_CAP = 10
WIKI_MANIFEST_CAP = 40
RELEVANCE_THRESHOLD_PPM = 300_000
_CACHE_LIMIT = 128
_EXCERPT_CHARS = 4_096
_GRAPH_PREVIEW_BYTES = 12_288
_GRAPH_SOURCE_BUDGET_BYTES = 8 * 1024 * 1024
_MANIFEST_TITLE_CHARS = 240
_QUERY_INTENTS = frozenset(
    {"topic", "overview", "continuation", "exhaustive", "unknown"}
)
_REQUEST_TOP_K_MAX = 100


@dataclass(frozen=True, slots=True)
class _Candidate:
    item_id: str
    revision_id: str
    source_space_id: str
    title: str
    excerpt_md: str
    updated_at_ms: int
    normalized_content_sha256: str
    score_ppm: int
    provenance: tuple[Literal["fts", "one_hop", "two_hop"], ...]
    graph_hop: int
    relation_type: str | None
    origin: Literal["native", "inherited", "overlay"]
    overlay_grant_id: str | None


@dataclass(slots=True)
class _GraphPreviewBudget:
    previews: dict[tuple[str, str], str]
    records: dict[tuple[str, str], dict[str, object]] = field(default_factory=dict)
    limit: int = _GRAPH_SOURCE_BUDGET_BYTES
    source_bytes: int = 0
    source_bodies_read: int = 0
    preview_reuses: int = 0
    deferred: set[tuple[str, str]] = field(default_factory=set)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _terms(normalized_query: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(term.casefold() for term in normalized_query.split()))


def _tag_matches(metadata_json: object, terms: tuple[str, ...]) -> int:
    if not isinstance(metadata_json, str):
        return 0
    try:
        metadata = json.loads(metadata_json)
    except json.JSONDecodeError:
        return 0
    if not isinstance(metadata, dict):
        return 0
    tags = metadata.get("tags", ())
    if not isinstance(tags, list):
        return 0
    folded = tuple(str(tag).casefold() for tag in tags if isinstance(tag, str))
    return sum(1 for term in terms if any(term in tag for tag in folded))


def _relation_semantics(relation_type: str | None) -> str:
    if relation_type is None:
        return "direct_match"
    if relation_type == "contradicts":
        return "conflicting_evidence"
    if relation_type in {"supports", "depends_on", "extends", "example_of"}:
        return "supporting_context"
    return "related_context"


def _origin(
    *, selected_space_id: str, source_space_id: str, overlay_grant_id: str | None
) -> Literal["native", "inherited", "overlay"]:
    if overlay_grant_id is not None:
        return "overlay"
    return "native" if source_space_id == selected_space_id else "inherited"


class HybridRetrievalRepository(RetrievalRepository):
    """One-snapshot hybrid retrieval with an authority/version-bound LRU."""

    __slots__ = ("_cache", "_cache_limit", "_query_transformer", "_prepare_reranker_evidence")

    def __init__(
        self,
        *,
        cache_limit: int = _CACHE_LIMIT,
        query_transformer: Callable[[str], str] | None = None,
        prepare_reranker_evidence: bool = True,
    ) -> None:
        if isinstance(cache_limit, bool) or not isinstance(cache_limit, int):
            raise TypeError("cache_limit must be an integer.")
        if not 1 <= cache_limit <= 1024:
            raise ValueError("cache_limit must be from 1 through 1024.")
        if query_transformer is not None and not callable(query_transformer):
            raise TypeError("query_transformer must be callable or None.")
        if not isinstance(prepare_reranker_evidence, bool):
            raise TypeError("prepare_reranker_evidence must be a boolean.")
        self._cache: OrderedDict[str, CanonicalRetrievalSnapshot] = OrderedDict()
        self._cache_limit = cache_limit
        self._query_transformer = query_transformer
        self._prepare_reranker_evidence = prepare_reranker_evidence

    def retrieve(
        self,
        connection: ManagedConnection,
        *,
        space_id: str,
        query: str,
        search_query: str | None = None,
        top_k: int = 10,
        neighbor_limit: int = 16,
        event_limit: int = 20,
        retrieval_intent: RetrievalQueryIntent = "unknown",
        continuation_documents: tuple[RankedDocumentIdentity, ...] = (),
    ) -> CanonicalRetrievalSnapshot:
        connection = _connection(connection)
        space_id = _uuid(space_id, "space_id")
        # The public search contract accepts this legacy request hint through
        # 100. Full-corpus retrieval does not slice by it, but the repository
        # must validate the same range instead of turning a valid request into
        # an internal error.
        _limit(top_k, "top_k", _REQUEST_TOP_K_MAX)
        _limit(neighbor_limit, "neighbor_limit", 80)
        event_limit = _limit(event_limit, "event_limit", 100)
        return self._retrieve_hybrid(
            connection,
            selected_space_id=space_id,
            source_space_ids=(space_id,),
            overlays=(),
            corpus_closure_sha256=None,
            query=query,
            search_query=search_query,
            event_limit=event_limit,
            retrieval_intent=retrieval_intent,
            continuation_documents=continuation_documents,
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
        top_k: int = 10,
        neighbor_limit: int = 16,
        event_limit: int = 20,
        retrieval_intent: RetrievalQueryIntent = "unknown",
        continuation_documents: tuple[RankedDocumentIdentity, ...] = (),
    ) -> CanonicalRetrievalSnapshot:
        connection = _connection(connection)
        selected = _uuid(selected_space_id, "selected_space_id")
        if not isinstance(source_space_ids, tuple) or not source_space_ids:
            raise RetrievalQueryError("The corpus source closure is invalid.")
        sources = tuple(dict.fromkeys(_uuid(value, "source_space_id") for value in source_space_ids))
        if len(sources) != len(source_space_ids) or selected not in sources:
            raise RetrievalQueryError("The corpus source closure is incoherent.")
        if len(sources) > 4096:
            raise RetrievalQueryError("The corpus source closure is too large.")
        normalized_overlays: list[dict[str, str]] = []
        if not isinstance(overlays, tuple) or len(overlays) > 4096:
            raise RetrievalQueryError("The distribution overlay closure is invalid.")
        expected_overlay_keys = {
            "grant_id", "source_item_id", "source_revision_id", "source_space_id"
        }
        for value in overlays:
            if not isinstance(value, dict) or set(value) != expected_overlay_keys:
                raise RetrievalQueryError("A distribution overlay is invalid.")
            normalized_overlays.append(
                {key: _uuid(value[key], key) for key in sorted(expected_overlay_keys)}
            )
        if (
            not isinstance(corpus_closure_sha256, str)
            or len(corpus_closure_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in corpus_closure_sha256)
        ):
            raise RetrievalQueryError("The corpus closure proof is invalid.")
        _limit(top_k, "top_k", _REQUEST_TOP_K_MAX)
        _limit(neighbor_limit, "neighbor_limit", 80)
        event_limit = _limit(event_limit, "event_limit", 100)
        return self._retrieve_hybrid(
            connection,
            selected_space_id=selected,
            source_space_ids=sources,
            overlays=tuple(normalized_overlays),
            corpus_closure_sha256=corpus_closure_sha256,
            query=query,
            search_query=search_query,
            event_limit=event_limit,
            retrieval_intent=retrieval_intent,
            continuation_documents=continuation_documents,
        )

    def _retrieve_hybrid(
        self,
        connection: ManagedConnection,
        *,
        selected_space_id: str,
        source_space_ids: tuple[str, ...],
        overlays: tuple[dict[str, str], ...],
        corpus_closure_sha256: str | None,
        query: str,
        search_query: str | None,
        event_limit: int,
        retrieval_intent: RetrievalQueryIntent,
        continuation_documents: tuple[RankedDocumentIdentity, ...],
    ) -> CanonicalRetrievalSnapshot:
        source_json = _canonical_json(source_space_ids)
        overlay_json = _canonical_json(overlays)
        generation_sha, _selected_view_labels, selected_view_kind = self._generation_vector(
            connection,
            source_json=source_json,
            selected_space_id=selected_space_id,
        )
        full_corpus = selected_view_kind in {
            "personal", "part", "team", "responsibility"
        }
        normalized_query, _ = _normalize_query(query)
        if retrieval_intent not in _QUERY_INTENTS:
            raise RetrievalQueryError("retrieval_intent is invalid.")
        continuation_documents = self._continuation_identities(
            continuation_documents
        )
        search_query, fts_query = _normalize_query(
            normalized_query if search_query is None else search_query
        )
        if self._query_transformer is not None:
            transformed = self._query_transformer(normalized_query)
            if not isinstance(transformed, str):
                raise RetrievalQueryError("The retrieval query transformer is invalid.")
            # Temporal normalization is an original-query fallback.  A service-layer
            # multilingual expansion is already a bounded lexical query and must not
            # be overwritten here, otherwise Korean questions lose their translated
            # English terms immediately before FTS.
            if transformed.strip() and search_query == normalized_query:
                search_query, fts_query = _normalize_query(transformed)
        cache_identity = _sha(
            {
                "closure": corpus_closure_sha256 or _sha(source_space_ids),
                "generation": generation_sha,
                "pipeline": PIPELINE_VERSION,
                "graph_preview_policy": {
                    "enabled": self._prepare_reranker_evidence,
                    "source_bytes": _GRAPH_SOURCE_BUDGET_BYTES,
                },
                "query": normalized_query,
                "query_intent": retrieval_intent,
                "search_query": search_query,
                "continuation_documents": [
                    {
                        "item_id": row.item_id,
                        "revision_id": row.revision_id,
                        "source_space_id": row.source_space_id,
                    }
                    for row in continuation_documents
                ],
            }
        )
        cached = self._cache.get(cache_identity)
        if cached is not None:
            self._cache.move_to_end(cache_identity)
            explain = cached.hybrid_explain
            if explain is None:  # pragma: no cover - cache is private and typed
                raise RetrievalIntegrityError("A hybrid cache entry is malformed.")
            return replace(
                cached,
                projection=self._projection_state(connection, selected_space_id),
                hybrid_explain=replace(explain, cache_hit=True),
            )

        projection = self._projection_state(connection, selected_space_id)
        wiki_manifest = self._wiki_manifest(
            connection,
            selected_space_id=selected_space_id,
            source_json=source_json,
            overlay_json=overlay_json,
        )
        if retrieval_intent in {"overview", "exhaustive", "continuation"}:
            hits, direct = (), ()
        else:
            hits, direct = self._direct_candidates(
                connection,
                selected_space_id=selected_space_id,
                source_json=source_json,
                overlay_json=overlay_json,
                fts_query=fts_query,
                normalized_query=search_query,
                direct_cap=None if full_corpus else DIRECT_CAP,
            )
        candidate_map = {candidate.item_id: candidate for candidate in direct}
        direct_seed_ids = tuple(
            candidate.item_id for candidate in direct[:ONE_HOP_SEED_CAP]
        )
        graph_work = _GraphPreviewBudget(
            previews={(row.item_id, row.revision_id): row.excerpt_md for row in direct},
            limit=_GRAPH_SOURCE_BUDGET_BYTES,
        )
        one_hop_rows = self._graph_candidates(
            connection,
            source_json=source_json,
            seed_ids=direct_seed_ids,
            hop=1,
            addition_cap=PRE_RANK_CAP,
            evidence_query=f"{normalized_query} {search_query}",
            work=graph_work,
        )
        one_hop_added = self._merge_graph_candidates(
            candidate_map,
            one_hop_rows,
            selected_space_id=selected_space_id,
            normalized_query=normalized_query,
            hop=1,
        )
        if not full_corpus:
            candidate_map = dict(
                sorted(
                    candidate_map.items(),
                    key=lambda pair: self._candidate_key(pair[1]),
                )[:PRE_RANK_CAP]
            )
        sufficiency = self._sufficient(tuple(candidate_map.values()), len(direct))
        two_hop_executed = not sufficiency and bool(candidate_map)
        two_hop_added = 0
        if two_hop_executed:
            anchors = tuple(
                candidate.item_id
                for candidate in sorted(candidate_map.values(), key=self._candidate_key)
                if candidate.relation_type != "contradicts"
            )[:TWO_HOP_ANCHOR_CAP]
            rows = self._graph_candidates(
                connection,
                source_json=source_json,
                seed_ids=anchors,
                hop=2,
                addition_cap=TWO_HOP_ADDITION_CAP,
                evidence_query=f"{normalized_query} {search_query}",
                work=graph_work,
            )
            two_hop_added = self._merge_graph_candidates(
                candidate_map,
                rows,
                selected_space_id=selected_space_id,
                normalized_query=normalized_query,
                hop=2,
            )

        pre_rank_ordered = tuple(
            sorted(candidate_map.values(), key=self._candidate_key)
        )
        pre_rank = (
            pre_rank_ordered
            if full_corpus
            else pre_rank_ordered[:PRE_RANK_CAP]
        )
        deduped = self._dedupe_exact_content(pre_rank)
        relevant = tuple(
            candidate
            for candidate in deduped
            if candidate.score_ppm >= RELEVANCE_THRESHOLD_PPM
        )
        if self._prepare_reranker_evidence:
            relevant = self._hydrate_graph_candidates(
                connection, relevant, work=graph_work,
                evidence_query=f"{normalized_query} {search_query}",
            )
        final_cap = None if full_corpus else FINAL_CAP
        selected = self._fair_final(
            relevant,
            source_space_ids,
            final_cap=final_cap,
        )
        reranker_candidates = self._ranked_documents(
            relevant,
            all_candidates=pre_rank,
            selected_space_id=selected_space_id,
        )
        ranked = self._ranked_documents(
            selected,
            all_candidates=pre_rank,
            selected_space_id=selected_space_id,
        )
        preferred_continuation = (
            continuation_documents
            if retrieval_intent in {"continuation", "unknown"}
            else ()
        )
        supplemental_documents = self._supplemental_body_documents(
            connection,
            selected_space_id=selected_space_id,
            source_json=source_json,
            overlay_json=overlay_json,
            ranked_documents=ranked,
            continuation_documents=preferred_continuation,
            limit=(None if full_corpus else max(0, FINAL_CAP - len(ranked))),
        )
        item_ids = tuple(
            document.item_id for document in (*ranked, *supplemental_documents)
        )
        events = self._closure_events(
            connection,
            source_json=source_json,
            overlay_json=overlay_json,
            item_ids=item_ids,
            limit=event_limit,
        )
        explain = HybridRetrievalExplain(
            pipeline_version=PIPELINE_VERSION,
            direct_candidate_count=len(direct),
            one_hop_candidate_count=one_hop_added,
            two_hop_candidate_count=two_hop_added,
            pre_rank_candidate_count=len(pre_rank),
            final_document_count=len(ranked),
            two_hop_executed=two_hop_executed,
            sufficiency_gate_passed=sufficiency,
            cache_hit=False,
            direct_cap=None if full_corpus else DIRECT_CAP,
            pre_rank_cap=None if full_corpus else PRE_RANK_CAP,
            final_cap=final_cap,
        )
        snapshot = CanonicalRetrievalSnapshot(
            space_id=selected_space_id,
            normalized_query=normalized_query,
            hits=hits,
            semantic_neighbors=(),
            recent_events=events,
            projection=projection,
            corpus_closure_sha256=corpus_closure_sha256,
            ranked_documents=ranked,
            reranker_candidates=reranker_candidates,
            hybrid_explain=explain,
            cache_identity_sha256=cache_identity,
            wiki_manifest=wiki_manifest,
            query_intent=retrieval_intent,
            supplemental_documents=supplemental_documents,
            effective_search_query=search_query,
            graph_preview_work=GraphPreviewWork(
                source_budget_bytes=graph_work.limit,
                source_bytes=graph_work.source_bytes,
                source_bodies_read=graph_work.source_bodies_read,
                preview_reuses=graph_work.preview_reuses,
                deferred_bodies=len(graph_work.deferred),
                preparation_enabled=self._prepare_reranker_evidence,
            ),
            reranker_evidence_complete=not any(
                (row.item_id, row.revision_id) not in graph_work.previews
                for row in reranker_candidates
            ),
        )
        self._cache[cache_identity] = snapshot
        self._cache.move_to_end(cache_identity)
        while len(self._cache) > self._cache_limit:
            self._cache.popitem(last=False)
        return snapshot

    @staticmethod
    def _continuation_identities(
        values: tuple[RankedDocumentIdentity, ...],
    ) -> tuple[RankedDocumentIdentity, ...]:
        if not isinstance(values, tuple) or len(values) > FINAL_CAP:
            raise RetrievalQueryError("Continuation document identities are invalid.")
        result: list[RankedDocumentIdentity] = []
        seen_items: set[str] = set()
        for value in values:
            if not isinstance(value, RankedDocumentIdentity):
                raise RetrievalQueryError(
                    "A continuation document identity is invalid."
                )
            normalized = RankedDocumentIdentity(
                item_id=_uuid(value.item_id, "continuation item_id"),
                revision_id=_uuid(value.revision_id, "continuation revision_id"),
                source_space_id=_uuid(
                    value.source_space_id, "continuation source_space_id"
                ),
            )
            if normalized.item_id in seen_items:
                raise RetrievalQueryError(
                    "Continuation document identities contain a duplicate item."
                )
            seen_items.add(normalized.item_id)
            result.append(normalized)
        return tuple(result)

    @classmethod
    def _supplemental_body_documents(
        cls,
        connection: ManagedConnection,
        *,
        selected_space_id: str,
        source_json: str,
        overlay_json: str,
        ranked_documents: tuple[RankedDocument, ...],
        continuation_documents: tuple[RankedDocumentIdentity, ...],
        limit: int | None,
    ) -> tuple[SupplementalBodyDocument, ...]:
        """Load authorized current bodies without changing ranked-search semantics.

        ``limit=None`` removes the document-count cap; the Agent composer still
        enforces the exact combined excerpt-byte budget before model execution.
        """

        if limit is not None and (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 0 <= limit <= FINAL_CAP
        ):
            raise RetrievalQueryError("The supplemental document limit is invalid.")
        if limit == 0:
            return ()

        allowed_sources = frozenset(json.loads(source_json))
        excluded_items = {document.item_id for document in ranked_documents}
        excluded_hashes = {
            document.normalized_content_sha256 for document in ranked_documents
        }
        selected: list[SupplementalBodyDocument] = []

        def append_rows(
            rows: Iterable[sqlite3.Row],
            reason: Literal["continuation", "representative"],
        ) -> None:
            for row in rows:
                if limit is not None and len(selected) >= limit:
                    break
                item_id = _uuid(row["item_id"], "supplemental item_id")
                source_space_id = _uuid(
                    row["space_id"], "supplemental source space_id"
                )
                overlay_grant_id = _optional_uuid(
                    row["overlay_grant_id"], "supplemental overlay grant_id"
                )
                if source_space_id not in allowed_sources and overlay_grant_id is None:
                    raise RetrievalIntegrityError(
                        "Supplemental body evidence escaped the authorized closure."
                    )
                content_hash = _text(
                    row["normalized_content_sha256"],
                    "supplemental normalized content SHA-256",
                )
                if item_id in excluded_items or content_hash in excluded_hashes:
                    continue
                body = _text(row["excerpt_md"], "supplemental body excerpt")
                if not body.strip():
                    continue
                excluded_items.add(item_id)
                excluded_hashes.add(content_hash)
                selected.append(
                    SupplementalBodyDocument(
                        item_id=item_id,
                        revision_id=_uuid(
                            row["revision_id"], "supplemental revision_id"
                        ),
                        source_space_id=source_space_id,
                        selected_view_space_id=selected_space_id,
                        title=_text(row["title"], "supplemental title"),
                        excerpt_md=body,
                        updated_at_ms=_integer(
                            row["updated_at"], "supplemental timestamp"
                        ),
                        selection_reason=reason,
                        origin=_origin(
                            selected_space_id=selected_space_id,
                            source_space_id=source_space_id,
                            overlay_grant_id=overlay_grant_id,
                        ),
                        inherited=source_space_id != selected_space_id,
                        overlay_grant_id=overlay_grant_id,
                        normalized_content_sha256=content_hash,
                    )
                )

        if continuation_documents:
            preferred_json = _canonical_json(
                [
                    {
                        "item_id": row.item_id,
                        "revision_id": row.revision_id,
                        "source_space_id": row.source_space_id,
                    }
                    for row in continuation_documents
                ]
            )
            try:
                rows = connection.execute(
                    f"""WITH preferred(item_id,revision_id,source_space_id,ordinal) AS (
                         SELECT json_extract(value,'$.item_id'),
                                json_extract(value,'$.revision_id'),
                                json_extract(value,'$.source_space_id'),
                                CAST(key AS INTEGER)
                           FROM json_each(?)
                       ), authorized_sources(space_id) AS (
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
                              substr(d.content_md,1,{_EXCERPT_CHARS}) AS excerpt_md,
                              r.normalized_content_sha256,
                              (SELECT overlay.grant_id
                                 FROM authorized_overlays AS overlay
                                WHERE overlay.source_space_id=d.space_id
                                  AND overlay.source_item_id=d.item_id
                                  AND overlay.source_revision_id=d.revision_id
                                LIMIT 1) AS overlay_grant_id
                         FROM preferred AS p
                         JOIN search_document AS d
                           ON d.item_id=p.item_id AND d.revision_id=p.revision_id
                          AND d.space_id=p.source_space_id
                         JOIN knowledge_item AS i
                           ON i.id=d.item_id AND i.space_id=d.space_id
                         JOIN knowledge_revision AS r
                           ON r.id=d.revision_id AND r.item_id=d.item_id
                         JOIN knowledge_revision_body AS b
                           ON b.revision_id=d.revision_id AND b.item_id=d.item_id
                        WHERE (
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
                          AND length(trim(d.content_md))>0
                        ORDER BY p.ordinal
                        LIMIT {FINAL_CAP}""",
                    (preferred_json, source_json, overlay_json),
                ).fetchall()
            except sqlite3.Error as exc:
                raise RetrievalRepositoryError(
                    "Bounded continuation body retrieval failed."
                ) from exc
            append_rows(rows, "continuation")

        if limit is None or len(selected) < limit:
            representative_limit = (
                "" if limit is None else f"LIMIT {PRE_RANK_CAP}"
            )
            try:
                rows = connection.execute(
                    f"""WITH authorized_sources(space_id) AS (
                         SELECT value FROM json_each(?)
                       ), authorized_overlays(
                         grant_id,source_space_id,source_item_id,source_revision_id
                       ) AS (
                         SELECT json_extract(value,'$.grant_id'),
                                json_extract(value,'$.source_space_id'),
                                json_extract(value,'$.source_item_id'),
                                json_extract(value,'$.source_revision_id')
                           FROM json_each(?)
                       ), eligible AS (
                         SELECT d.item_id,d.space_id,d.revision_id,d.title,d.updated_at,
                                substr(d.content_md,1,{_EXCERPT_CHARS}) AS excerpt_md,
                                r.normalized_content_sha256,
                                (SELECT overlay.grant_id
                                   FROM authorized_overlays AS overlay
                                  WHERE overlay.source_space_id=d.space_id
                                    AND overlay.source_item_id=d.item_id
                                    AND overlay.source_revision_id=d.revision_id
                                  LIMIT 1) AS overlay_grant_id,
                                row_number() OVER (
                                  PARTITION BY d.space_id
                                  ORDER BY d.updated_at DESC,d.item_id ASC
                                ) AS per_source_rank
                           FROM search_document AS d
                           JOIN knowledge_item AS i
                             ON i.id=d.item_id AND i.space_id=d.space_id
                           JOIN knowledge_revision AS r
                             ON r.id=d.revision_id AND r.item_id=d.item_id
                           JOIN knowledge_revision_body AS b
                             ON b.revision_id=d.revision_id AND b.item_id=d.item_id
                          WHERE (
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
                            AND length(trim(d.content_md))>0
                       )
                       SELECT item_id,space_id,revision_id,title,updated_at,excerpt_md,
                              normalized_content_sha256,overlay_grant_id
                         FROM eligible
                        ORDER BY per_source_rank,
                                 CASE WHEN space_id=? THEN 0 ELSE 1 END,
                                 updated_at DESC,item_id ASC
                        {representative_limit}""",
                    (source_json, overlay_json, selected_space_id),
                ).fetchall()
            except sqlite3.Error as exc:
                raise RetrievalRepositoryError(
                    "Bounded representative body retrieval failed."
                ) from exc
            append_rows(rows, "representative")
        return tuple(selected)

    @staticmethod
    def _generation_vector(
        connection: ManagedConnection,
        *,
        source_json: str,
        selected_space_id: str,
    ) -> tuple[str, tuple[str, ...], Literal["personal", "part", "team", "responsibility"]]:
        rows = connection.execute(
            """WITH authorized_sources(space_id) AS (
                 SELECT value FROM json_each(?)
               )
               SELECT s.id AS source_space_id,s.space_kind,s.generation,s.temporal_generation,
                      s.row_version,u.row_version AS organization_row_version,
                      CASE WHEN s.space_kind='personal' THEN p.display_name
                           ELSE u.name END AS selected_view_name,
                      coalesce(g.home_edge_generation,0) AS home_edge_generation,
                      coalesce(g.desired_generation,0) AS aggregate_desired_generation,
                      coalesce(g.built_generation,0) AS aggregate_built_generation,
                      coalesce(g.source_vector_sha256,'')
                        AS aggregate_source_vector_sha256
                 FROM authorized_sources AS a
                 JOIN knowledge_space AS s ON s.id=a.space_id
                 LEFT JOIN org_unit AS u ON u.id=s.owner_org_unit_id
                 LEFT JOIN personal_wiki_profile AS p ON p.space_id=s.id
                 LEFT JOIN aggregate_generation_vector AS g
                   ON g.selected_space_id=s.id
                WHERE s.status='active'
                ORDER BY s.id""",
            (source_json,),
        ).fetchall()
        expected = tuple(sorted(json.loads(source_json)))
        if tuple(str(row["source_space_id"]) for row in rows) != expected:
            raise RetrievalIntegrityError("The authorized generation vector is incomplete.")
        selected_row = next(
            (
                row
                for row in rows
                if str(row["source_space_id"]) == selected_space_id
            ),
            None,
        )
        if selected_row is None:
            raise RetrievalIntegrityError("The selected Wiki view is unavailable.")
        selected_view_name = _text(
            selected_row["selected_view_name"], "selected view name"
        ).strip()
        if not selected_view_name:
            raise RetrievalIntegrityError("The selected Wiki view name is invalid.")
        selected_view_kind = str(selected_row["space_kind"])
        if selected_view_kind not in {"personal", "part", "team", "responsibility"}:
            raise RetrievalIntegrityError("The selected Wiki view kind is invalid.")
        vector = [
            {
                "aggregate_built_generation": int(row["aggregate_built_generation"]),
                "aggregate_desired_generation": int(row["aggregate_desired_generation"]),
                "aggregate_source_vector_sha256": str(row["aggregate_source_vector_sha256"]),
                "generation": int(row["generation"]),
                "home_edge_generation": int(row["home_edge_generation"]),
                "organization_row_version": int(row["organization_row_version"] or 0),
                "row_version": int(row["row_version"]),
                "source_space_id": str(row["source_space_id"]),
                "space_kind": str(row["space_kind"]),
                "temporal_generation": int(row["temporal_generation"]),
            }
            for row in rows
        ]
        return _sha(vector), (selected_view_name,), selected_view_kind  # type: ignore[return-value]

    @staticmethod
    def _wiki_manifest(
        connection: ManagedConnection,
        *,
        selected_space_id: str,
        source_json: str,
        overlay_json: str,
    ) -> WikiManifest:
        """Build a query-independent bounded replacement for legacy index.md."""

        try:
            rows = connection.execute(
                f"""WITH authorized_sources(space_id) AS (
                     SELECT value FROM json_each(?)
                   ), authorized_overlays(
                     grant_id,source_space_id,source_item_id,source_revision_id
                   ) AS (
                     SELECT json_extract(value,'$.grant_id'),
                            json_extract(value,'$.source_space_id'),
                            json_extract(value,'$.source_item_id'),
                            json_extract(value,'$.source_revision_id')
                       FROM json_each(?)
                   ), eligible AS (
                   SELECT d.item_id,d.space_id,d.revision_id,d.updated_at,
                          substr(d.title,1,{_MANIFEST_TITLE_CHARS}) AS title,
                          length(d.title) AS title_length,
                          (SELECT overlay.grant_id
                             FROM authorized_overlays AS overlay
                            WHERE overlay.source_space_id=d.space_id
                              AND overlay.source_item_id=d.item_id
                              AND overlay.source_revision_id=d.revision_id
                            LIMIT 1) AS overlay_grant_id
                     FROM search_document AS d
                     JOIN knowledge_item AS i
                       ON i.id=d.item_id AND i.space_id=d.space_id
                     JOIN knowledge_revision AS r
                       ON r.id=d.revision_id AND r.item_id=d.item_id
                    WHERE (
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
                   )
                   SELECT eligible.*,count(*) OVER() AS total_count
                     FROM eligible
                    ORDER BY CASE WHEN space_id=? THEN 0 ELSE 1 END,
                             updated_at DESC,item_id ASC
                    LIMIT {WIKI_MANIFEST_CAP}""",
                (source_json, overlay_json, selected_space_id),
            ).fetchall()
        except sqlite3.Error as exc:
            raise RetrievalRepositoryError(
                "Bounded Wiki manifest retrieval failed."
            ) from exc
        allowed_sources = frozenset(json.loads(source_json))
        entries: list[WikiManifestEntry] = []
        total_count = 0 if not rows else _integer(
            rows[0]["total_count"], "Wiki manifest total count"
        )
        for row in rows:
            source_space_id = _uuid(row["space_id"], "stored space_id")
            overlay_grant_id = _optional_uuid(
                row["overlay_grant_id"], "overlay grant_id"
            )
            if source_space_id not in allowed_sources and overlay_grant_id is None:
                raise RetrievalIntegrityError(
                    "The Wiki manifest escaped the authorized closure."
                )
            observed_origin = _origin(
                selected_space_id=selected_space_id,
                source_space_id=source_space_id,
                overlay_grant_id=overlay_grant_id,
            )
            entries.append(WikiManifestEntry(
                item_id=_uuid(row["item_id"], "stored item_id"),
                revision_id=_uuid(row["revision_id"], "stored revision id"),
                source_space_id=source_space_id,
                title=_text(row["title"], "manifest title"),
                updated_at_ms=_integer(row["updated_at"], "manifest timestamp"),
                origin=observed_origin,
                inherited=source_space_id != selected_space_id,
                overlay_grant_id=overlay_grant_id,
                title_truncated=(
                    _integer(row["title_length"], "manifest title length")
                    > _MANIFEST_TITLE_CHARS
                ),
            ))
        if total_count < len(entries) or len(entries) > WIKI_MANIFEST_CAP:
            raise RetrievalIntegrityError("The Wiki manifest bounds are incoherent.")
        return WikiManifest(
            selected_view_space_id=selected_space_id,
            authorized_published_count=total_count,
            entries=tuple(entries),
            truncated=total_count > len(entries),
            entry_cap=WIKI_MANIFEST_CAP,
        )

    @staticmethod
    def _direct_candidates(
        connection: ManagedConnection,
        *,
        selected_space_id: str,
        source_json: str,
        overlay_json: str,
        fts_query: str,
        normalized_query: str,
        direct_cap: int | None,
    ) -> tuple[tuple[RetrievalHit, ...], tuple[_Candidate, ...]]:
        if direct_cap is not None and direct_cap != DIRECT_CAP:
            raise RetrievalQueryError("The direct candidate cap is invalid.")
        direct_limit = "" if direct_cap is None else f"LIMIT {direct_cap}"
        try:
            rows = connection.execute(
                f"""WITH authorized_sources(space_id) AS (
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
                          r.normalized_content_sha256,r.metadata_json,
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
                    {direct_limit}""",
                (source_json, overlay_json, fts_query),
            ).fetchall()
        except sqlite3.Error as exc:
            raise RetrievalRepositoryError("Bounded direct FTS retrieval failed.") from exc
        allowed_sources = frozenset(json.loads(source_json))
        terms = _terms(normalized_query)
        hits: list[RetrievalHit] = []
        candidates: list[_Candidate] = []
        for rank, row in enumerate(rows, start=1):
            score = row["bm25_score"]
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise RetrievalIntegrityError("Stored FTS rank is invalid.")
            if not math.isfinite(float(score)):
                raise RetrievalIntegrityError("Stored FTS rank is not finite.")
            source_space_id = _uuid(row["space_id"], "stored space_id")
            overlay_grant_id = _optional_uuid(row["overlay_grant_id"], "overlay grant_id")
            if source_space_id not in allowed_sources and overlay_grant_id is None:
                raise RetrievalIntegrityError("FTS escaped the authorized closure.")
            title = _text(row["title"], "title")
            excerpt = _text(row["excerpt_md"], "excerpt")
            observed_origin = _origin(
                selected_space_id=selected_space_id,
                source_space_id=source_space_id,
                overlay_grant_id=overlay_grant_id,
            )
            hit = RetrievalHit(
                item_id=_uuid(row["item_id"], "stored item_id"),
                space_id=source_space_id,
                revision_id=_uuid(row["revision_id"], "stored revision_id"),
                title=title,
                excerpt_md=excerpt,
                updated_at_ms=_integer(row["updated_at"], "search timestamp"),
                rank=rank,
                bm25_score=float(score),
                selected_view_space_id=selected_space_id,
                inherited=source_space_id != selected_space_id,
                overlay_grant_id=overlay_grant_id,
                origin=observed_origin,
            )
            hits.append(hit)
            title_folded = title.casefold()
            title_matches = sum(1 for term in terms if term in title_folded)
            rank_component = max(0, 900_000 - ((rank - 1) * 18_000))
            title_component = min(60_000, title_matches * 20_000)
            tag_component = min(
                40_000,
                _tag_matches(row["metadata_json"], terms) * 20_000,
            )
            scope_component = 20_000 if source_space_id == selected_space_id else 0
            candidates.append(
                _Candidate(
                    item_id=hit.item_id,
                    revision_id=hit.revision_id,
                    source_space_id=source_space_id,
                    title=title,
                    # FTS already located the expanded query in this exact,
                    # authorized revision. A prefix can hide a late match from
                    # the optional grader. Full answer evidence is loaded
                    # separately by the canonical context service.
                    excerpt_md=excerpt,
                    updated_at_ms=hit.updated_at_ms,
                    normalized_content_sha256=_text(
                        row["normalized_content_sha256"], "normalized content SHA-256"
                    ),
                    score_ppm=min(
                        1_000_000,
                        rank_component + title_component + tag_component + scope_component,
                    ),
                    provenance=("fts",),
                    graph_hop=0,
                    relation_type=None,
                    origin=observed_origin,
                    overlay_grant_id=overlay_grant_id,
                )
            )
        return tuple(hits), tuple(candidates)

    @staticmethod
    def _graph_candidates(
        connection: ManagedConnection,
        *,
        source_json: str,
        seed_ids: tuple[str, ...],
        hop: Literal[1, 2],
        addition_cap: int,
        evidence_query: str,
        work: _GraphPreviewBudget,
    ) -> tuple[dict[str, object], ...]:
        if not seed_ids or addition_cap == 0:
            return ()
        seed_json = _canonical_json(seed_ids)
        cursor = connection.execute(
            f"""WITH seeds(seed_id,seed_rank) AS (
                   SELECT value,CAST(key AS INTEGER) FROM json_each(:seeds)
                 ), authorized_sources(space_id) AS (
                   SELECT value FROM json_each(:sources)
                 ), traversed AS (
                   SELECT e.id AS edge_id,e.edge_type,'local' AS graph_kind,
                          coalesce(e.confidence_ppm,500000) AS confidence_ppm,
                          e.updated_at,s.seed_id,s.seed_rank,
                          CASE WHEN e.source_item_id=s.seed_id
                               THEN e.target_item_id ELSE e.source_item_id END AS neighbor_item_id,
                          e.space_id AS neighbor_space_id,
                          CASE WHEN e.source_item_id=s.seed_id
                               THEN e.target_evidence_revision_id
                               ELSE e.source_evidence_revision_id END
                            AS neighbor_evidence_revision_id
                     FROM knowledge_edge AS e JOIN seeds AS s
                       ON s.seed_id IN (e.source_item_id,e.target_item_id)
                    WHERE e.edge_status='active' AND e.needs_revalidation=0
                      AND EXISTS (SELECT 1 FROM authorized_sources a
                                   WHERE a.space_id=e.space_id)
                      AND EXISTS (
                        SELECT 1 FROM knowledge_item si JOIN knowledge_item ti
                          ON ti.id=e.target_item_id AND ti.space_id=e.space_id
                         WHERE si.id=e.source_item_id AND si.space_id=e.space_id
                           AND si.lifecycle_status='active'
                           AND si.curation_status='published'
                           AND ti.lifecycle_status='active'
                           AND ti.curation_status='published'
                           AND (e.source_evidence_revision_id IS NULL
                                OR si.current_revision_id=e.source_evidence_revision_id)
                           AND (e.target_evidence_revision_id IS NULL
                                OR ti.current_revision_id=e.target_evidence_revision_id)
                      )
                   UNION ALL
                   SELECT e.id,e.edge_type,'aggregate',e.confidence_ppm,e.updated_at,
                          s.seed_id,s.seed_rank,
                          CASE WHEN e.source_item_id=s.seed_id
                               THEN e.target_item_id ELSE e.source_item_id END,
                          CASE WHEN e.source_item_id=s.seed_id
                               THEN e.target_space_id ELSE e.source_space_id END,
                          CASE WHEN e.source_item_id=s.seed_id
                               THEN e.target_revision_id ELSE e.source_revision_id END
                     FROM aggregate_knowledge_edge AS e JOIN seeds AS s
                       ON s.seed_id IN (e.source_item_id,e.target_item_id)
                    WHERE e.edge_status='active'
                      AND EXISTS (SELECT 1 FROM authorized_sources a
                                   WHERE a.space_id=e.home_space_id)
                      AND EXISTS (
                        SELECT 1 FROM knowledge_space home
                         WHERE home.id=e.home_space_id AND home.status='active'
                           AND home.space_kind IN ('team','responsibility')
                      )
                      AND EXISTS (SELECT 1 FROM authorized_sources a
                                   WHERE a.space_id=e.source_space_id)
                      AND EXISTS (SELECT 1 FROM authorized_sources a
                                   WHERE a.space_id=e.target_space_id)
                      AND EXISTS (
                        SELECT 1 FROM knowledge_item si JOIN knowledge_item ti
                          ON ti.id=e.target_item_id AND ti.space_id=e.target_space_id
                         WHERE si.id=e.source_item_id AND si.space_id=e.source_space_id
                           AND si.lifecycle_status='active'
                           AND si.curation_status='published'
                           AND ti.lifecycle_status='active'
                           AND ti.curation_status='published'
                           AND si.current_revision_id=e.source_revision_id
                           AND ti.current_revision_id=e.target_revision_id
                      )
                 ), bounded AS (
                   SELECT *,row_number() OVER (
                     PARTITION BY seed_id
                     ORDER BY confidence_ppm DESC,updated_at DESC,edge_id ASC,
                              neighbor_item_id ASC
                   ) AS per_seed_rank,
                   count(*) OVER (PARTITION BY neighbor_item_id) AS bounded_hub_degree
                     FROM traversed
                 )
                 SELECT b.edge_id,b.edge_type,b.confidence_ppm,b.seed_id,b.seed_rank,
                        b.neighbor_item_id,b.neighbor_space_id,d.revision_id,d.title,
                        d.updated_at,
                        body.content_size_bytes,body.content_sha256,body.content_blob_hash,
                        r.normalized_content_sha256,r.metadata_json,
                        b.bounded_hub_degree
                   FROM bounded AS b
                   JOIN knowledge_item AS i
                     ON i.id=b.neighbor_item_id AND i.space_id=b.neighbor_space_id
                   JOIN search_document AS d
                     ON d.item_id=i.id AND d.space_id=i.space_id
                    AND d.revision_id=i.current_revision_id
                   JOIN knowledge_revision AS r
                     ON r.id=d.revision_id AND r.item_id=d.item_id
                   JOIN knowledge_revision_body AS body
                     ON body.revision_id=d.revision_id AND body.item_id=d.item_id
                  WHERE b.per_seed_rank<={ONE_HOP_PER_SEED_CAP}
                    AND i.lifecycle_status='active' AND i.curation_status='published'
                    AND (b.neighbor_evidence_revision_id IS NULL
                         OR b.neighbor_evidence_revision_id=d.revision_id)
                    AND d.title=r.title
                  ORDER BY b.seed_rank,b.per_seed_rank,b.edge_id,b.neighbor_item_id
                  LIMIT :addition_cap""",
            {
                "addition_cap": addition_cap,
                "seeds": seed_json,
                "sources": source_json,
            },
        )
        try:
            # Bounded edge metadata only: no full-text equality/selection before
            # admission. Repeated edges must not cause repeated body scans.
            rows = [dict(row) for row in cursor]
        finally:
            cursor.close()
        for row in rows:
            key = (str(row["neighbor_item_id"]), str(row["revision_id"]))
            if key in work.previews or key in work.records:
                work.preview_reuses += 1
            work.records[key] = row
            # Direct/graph intersections keep the FTS snippet when graph score
            # metadata wins. No graph-only source is read before final dedupe.
            row["excerpt_md"] = work.previews.get(key, "")
        return tuple(rows)

    @staticmethod
    def _hydrate_graph_candidates(
        connection: ManagedConnection,
        candidates: tuple[_Candidate, ...],
        *,
        work: _GraphPreviewBudget,
        evidence_query: str,
    ) -> tuple[_Candidate, ...]:
        admitted = {
            (row.item_id, row.revision_id): work.records[row.item_id, row.revision_id]
            for row in candidates
            if (row.item_id, row.revision_id) not in work.previews
        }
        planned_bytes = 0
        for key, row in admitted.items():
            size = _integer(row["content_size_bytes"], "graph source bytes")
            if (row["content_blob_hash"] is not None
                    and row["content_blob_hash"] != row["content_sha256"]):
                raise RetrievalIntegrityError("Graph preview source failed canonical integrity checks.")
            planned_bytes += size
        if planned_bytes > work.limit - work.source_bytes:
            # All candidate IDs/scores/relations remain available. Do not spend
            # body I/O for a grading call that cannot read every chosen preview.
            work.deferred.update(admitted)
            return candidates
        if admitted:
            identities = [
                {"item_id": key[0], "revision_id": key[1],
                 "space_id": row["neighbor_space_id"], "size": row["content_size_bytes"]}
                for key, row in admitted.items()
            ]
            body_cursor = connection.execute(
                """SELECT body.item_id,body.revision_id,
                          substr(CAST(coalesce(body.content_md,d.content_md) AS BLOB),
                                 1,json_extract(w.value,'$.size')+1) AS preview_source
                     FROM json_each(:identities) AS w
                     JOIN knowledge_revision_body AS body
                       ON body.item_id=json_extract(w.value,'$.item_id')
                      AND body.revision_id=json_extract(w.value,'$.revision_id')
                     JOIN search_document AS d
                       ON d.item_id=body.item_id AND d.revision_id=body.revision_id
                      AND d.space_id=json_extract(w.value,'$.space_id')""",
                {"identities": _canonical_json(identities)},
            )
            seen: set[tuple[str, str]] = set()
            try:
                for body in body_cursor:
                    key = (str(body["item_id"]), str(body["revision_id"]))
                    metadata = admitted[key]
                    raw = body["preview_source"]
                    if (not isinstance(raw, bytes)
                            or len(raw) != metadata["content_size_bytes"]
                            or sha256(raw).hexdigest() != metadata["content_sha256"]):
                        raise RetrievalIntegrityError("Graph preview source failed canonical integrity checks.")
                    try:
                        source = raw.decode("utf-8", errors="strict")
                    except UnicodeDecodeError as exc:
                        raise RetrievalIntegrityError("Graph preview source is not UTF-8.") from exc
                    source_bytes = len(raw)
                    del raw, body
                    work.previews[key] = select_evidence_passages(
                        source, _GRAPH_PREVIEW_BYTES, query=evidence_query,
                    ).text
                    del source
                    work.source_bodies_read += 1
                    work.source_bytes += source_bytes
                    seen.add(key)
            finally:
                body_cursor.close()
            if seen != set(admitted):
                raise RetrievalIntegrityError("A graph preview source is unavailable.")
        return tuple(replace(row, excerpt_md=work.previews[row.item_id, row.revision_id])
                     for row in candidates)

    @staticmethod
    def _merge_graph_candidates(
        candidate_map: dict[str, _Candidate],
        rows: Iterable[Mapping[str, object]],
        *,
        selected_space_id: str,
        normalized_query: str,
        hop: Literal[1, 2],
    ) -> int:
        added = 0
        terms = _terms(normalized_query)
        provenance: Literal["one_hop", "two_hop"] = "one_hop" if hop == 1 else "two_hop"
        for row in rows:
            item_id = _uuid(row["neighbor_item_id"], "neighbor item_id")
            if item_id == row["seed_id"]:
                continue
            relation = _text(row["edge_type"], "edge type")
            confidence = _integer(
                500_000 if row["confidence_ppm"] is None else row["confidence_ppm"],
                "edge confidence",
            )
            title = _text(row["title"], "neighbor title")
            title_matches = sum(1 for term in terms if term in title.casefold())
            tag_matches = _tag_matches(row["metadata_json"], terms)
            base = 500_000 if hop == 1 else 330_000
            relation_bonus = {
                "supports": 45_000,
                "depends_on": 35_000,
                "extends": 35_000,
                "example_of": 25_000,
                "related": 15_000,
                "contradicts": 20_000,
            }.get(relation, 0)
            hub_degree = _integer(row["bounded_hub_degree"], "bounded hub degree")
            hub_penalty = min(90_000, max(0, hub_degree - 2) * 15_000)
            scope_bonus = 20_000 if row["neighbor_space_id"] == selected_space_id else 0
            score = min(
                1_000_000,
                max(
                    0,
                    base
                    + (confidence // 4)
                    + relation_bonus
                    + min(40_000, title_matches * 20_000)
                    + min(30_000, tag_matches * 15_000)
                    + scope_bonus
                    - hub_penalty,
                ),
            )
            source_space_id = _uuid(row["neighbor_space_id"], "neighbor space_id")
            candidate = _Candidate(
                item_id=item_id,
                revision_id=_uuid(row["revision_id"], "neighbor revision_id"),
                source_space_id=source_space_id,
                title=title,
                excerpt_md=_text(row["excerpt_md"], "neighbor excerpt"),
                updated_at_ms=_integer(row["updated_at"], "neighbor timestamp"),
                normalized_content_sha256=_text(
                    row["normalized_content_sha256"], "normalized content SHA-256"
                ),
                score_ppm=score,
                provenance=(provenance,),
                graph_hop=hop,
                relation_type=relation,
                origin=_origin(
                    selected_space_id=selected_space_id,
                    source_space_id=source_space_id,
                    overlay_grant_id=None,
                ),
                overlay_grant_id=None,
            )
            existing = candidate_map.get(item_id)
            # Two-hop is an additive discovery lane only.  Revisiting a direct
            # or one-hop item through a cycle must not inflate score/provenance.
            if hop == 2 and existing is not None:
                continue
            if existing is None:
                candidate_map[item_id] = candidate
                added += 1
                continue
            combined_provenance = tuple(dict.fromkeys((*existing.provenance, provenance)))
            if score > existing.score_ppm:
                candidate_map[item_id] = replace(candidate, provenance=combined_provenance)
            else:
                candidate_map[item_id] = replace(existing, provenance=combined_provenance)
        return added

    @staticmethod
    def _candidate_key(candidate: _Candidate) -> tuple[object, ...]:
        return (
            -candidate.score_ppm,
            candidate.graph_hop,
            candidate.source_space_id,
            candidate.item_id,
            candidate.revision_id,
        )

    @classmethod
    def _ranked_documents(
        cls,
        candidates: tuple[_Candidate, ...],
        *,
        all_candidates: tuple[_Candidate, ...],
        selected_space_id: str,
    ) -> tuple[RankedDocument, ...]:
        return tuple(
            RankedDocument(
                item_id=candidate.item_id,
                revision_id=candidate.revision_id,
                source_space_id=candidate.source_space_id,
                selected_view_space_id=selected_space_id,
                title=candidate.title,
                excerpt_md=candidate.excerpt_md,
                score_ppm=candidate.score_ppm,
                rank=rank,
                provenance=candidate.provenance,
                graph_hop=candidate.graph_hop,
                relation_type=candidate.relation_type,
                relation_semantics=_relation_semantics(candidate.relation_type),
                origin=candidate.origin,
                inherited=candidate.source_space_id != selected_space_id,
                overlay_grant_id=candidate.overlay_grant_id,
                normalized_content_sha256=candidate.normalized_content_sha256,
                alternate_identities=alternates.get(
                    candidate.normalized_content_sha256, ()
                ),
                deterministic_score_ppm=candidate.score_ppm,
            )
            for rank, candidate in enumerate(candidates, start=1)
            for alternates in (cls._alternate_identities(all_candidates, candidate),)
        )

    @staticmethod
    def _sufficient(candidates: tuple[_Candidate, ...], direct_count: int) -> bool:
        strong_direct = sum(
            1
            for candidate in candidates
            if "fts" in candidate.provenance and candidate.score_ppm >= 500_000
        )
        relevant = sum(
            1 for candidate in candidates if candidate.score_ppm >= RELEVANCE_THRESHOLD_PPM
        )
        return direct_count >= 3 and strong_direct >= 3 and relevant >= FINAL_CAP

    @classmethod
    def _dedupe_exact_content(cls, candidates: tuple[_Candidate, ...]) -> tuple[_Candidate, ...]:
        selected: dict[str, _Candidate] = {}
        for candidate in candidates:
            existing = selected.get(candidate.normalized_content_sha256)
            if existing is None or cls._candidate_key(candidate) < cls._candidate_key(existing):
                selected[candidate.normalized_content_sha256] = candidate
        return tuple(sorted(selected.values(), key=cls._candidate_key))

    @staticmethod
    def _alternate_identities(
        candidates: tuple[_Candidate, ...], selected: _Candidate
    ) -> dict[str, tuple[RankedDocumentIdentity, ...]]:
        alternates = tuple(
            RankedDocumentIdentity(
                item_id=candidate.item_id,
                revision_id=candidate.revision_id,
                source_space_id=candidate.source_space_id,
            )
            for candidate in sorted(
                candidates,
                key=lambda value: (value.source_space_id, value.item_id, value.revision_id),
            )
            if candidate.normalized_content_sha256 == selected.normalized_content_sha256
            and candidate.item_id != selected.item_id
        )
        return {selected.normalized_content_sha256: alternates}

    @classmethod
    def _fair_final(
        cls,
        candidates: tuple[_Candidate, ...],
        source_space_ids: tuple[str, ...],
        *,
        final_cap: int | None = FINAL_CAP,
    ) -> tuple[_Candidate, ...]:
        ordered = tuple(sorted(candidates, key=cls._candidate_key))
        if final_cap is None:
            return ordered
        if final_cap != FINAL_CAP:
            raise RetrievalQueryError("The final document cap is invalid.")
        if len(source_space_ids) <= 1:
            return ordered[:final_cap]
        by_source: dict[str, list[_Candidate]] = defaultdict(list)
        for candidate in ordered:
            by_source[candidate.source_space_id].append(candidate)
        represented = sorted(
            (values[0] for values in by_source.values() if values),
            key=cls._candidate_key,
        )
        chosen: list[_Candidate] = represented[:final_cap]
        chosen_ids = {candidate.item_id for candidate in chosen}
        for candidate in ordered:
            if len(chosen) == final_cap:
                break
            if candidate.item_id not in chosen_ids:
                chosen.append(candidate)
                chosen_ids.add(candidate.item_id)
        return tuple(sorted(chosen, key=cls._candidate_key))


__all__ = [
    "DIRECT_CAP",
    "FINAL_CAP",
    "HybridRetrievalRepository",
    "ONE_HOP_PER_SEED_CAP",
    "ONE_HOP_SEED_CAP",
    "PIPELINE_VERSION",
    "PRE_RANK_CAP",
    "TWO_HOP_ADDITION_CAP",
    "TWO_HOP_ANCHOR_CAP",
    "WIKI_MANIFEST_CAP",
]
