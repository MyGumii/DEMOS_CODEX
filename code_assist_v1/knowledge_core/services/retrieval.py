"""Scope-bound canonical FTS and graph retrieval service boundary.

P3.3 remains inert: this module registers no route and changes no rollout flag.
It resolves one exact server-owned ``CorpusClosure`` and reads one canonical
database snapshot.  Prompt assembly belongs to P3.4; all returned Markdown is
therefore explicitly labelled untrusted reference data here.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from ..db import ConnectionFactory, TransactionMode, transaction
from ..errors import KnowledgeCoreError
from ..repositories.conversations import (
    ConversationNotFoundError,
    ConversationRepository,
    ConversationScopeError,
)
from ..repositories.retrieval import (
    CanonicalRetrievalSnapshot,
    RetrievalHit,
    RetrievalItemEvent,
    RetrievalProjectionState,
    RetrievalQueryIntent,
    RankedDocument,
    HybridRankingEnvelope,
    HybridRetrievalExplain,
    RetrievalRepository,
    RetrievalSemanticNeighbor,
    WikiManifest,
    SupplementalBodyDocument,
)
from ..scope import AccessPlan, Capability, ScopeResolver, SessionPrincipal


_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.ASCII,
)


class RetrievalServiceError(KnowledgeCoreError):
    code = "RETRIEVAL_ERROR"
    http_status = 500


class RetrievalNotFoundError(RetrievalServiceError):
    code = "NOT_FOUND"
    http_status = 404


class RetrievalScopeStaleError(RetrievalServiceError):
    code = "SCOPE_STALE"
    http_status = 409


class RetrievalStateConflictError(RetrievalServiceError):
    code = "STATE_CONFLICT"
    http_status = 409


@dataclass(frozen=True, slots=True)
class ConversationRetrievalRequest:
    actor_user_id: str
    actor_scope_epoch: int
    conversation_id: str
    query: str
    top_k: int = 8
    neighbor_limit: int = 16
    event_limit: int = 20
    as_of_ms: int | None = None


@dataclass(frozen=True, slots=True)
class SpaceRetrievalRequest:
    actor_user_id: str
    actor_scope_epoch: int
    space_id: str
    query: str
    top_k: int = 8
    neighbor_limit: int = 16
    event_limit: int = 20
    as_of_ms: int | None = None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    actor_user_id: str
    conversation_id: str | None
    space_id: str
    normalized_query: str
    hits: tuple[RetrievalHit, ...]
    semantic_neighbors: tuple[RetrievalSemanticNeighbor, ...]
    recent_events: tuple[RetrievalItemEvent, ...]
    projection: RetrievalProjectionState
    retrieval_source: str
    trust_boundary: str = "untrusted_reference"
    corpus_closure_sha256: str | None = None
    ranked_documents: tuple[RankedDocument, ...] = ()
    hybrid_explain: HybridRetrievalExplain | None = None
    cache_identity_sha256: str | None = None
    ranking_envelope: HybridRankingEnvelope | None = None
    wiki_manifest: WikiManifest | None = None
    query_intent: RetrievalQueryIntent = "unknown"
    supplemental_documents: tuple[SupplementalBodyDocument, ...] = ()


def _user(value: object) -> str:
    if not isinstance(value, str) or _USER_RE.fullmatch(value) is None:
        raise ValueError("actor_user_id is invalid.")
    return value


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str) or _UUID_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a canonical lowercase UUID.")
    return value


def _epoch(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("actor_scope_epoch must be a positive integer.")
    return value


class CanonicalRetrievalService:
    """Authorize and execute bounded retrieval in one read snapshot."""

    __slots__ = (
        "_conversations",
        "_factory",
        "_postprocessor",
        "_query_expander",
        "_retrieval",
        "_scopes",
    )

    def __init__(
        self,
        factory: ConnectionFactory,
        *,
        scope_resolver: ScopeResolver,
        conversations: ConversationRepository | None = None,
        retrieval: RetrievalRepository | None = None,
        postprocessor: object | None = None,
        query_expander: object | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("CanonicalRetrievalService requires a ConnectionFactory.")
        if not isinstance(scope_resolver, ScopeResolver):
            raise TypeError("CanonicalRetrievalService requires a ScopeResolver.")
        self._factory = factory
        self._scopes = scope_resolver
        self._conversations = conversations or ConversationRepository()
        self._retrieval = retrieval or RetrievalRepository()
        if postprocessor is not None and not callable(
            getattr(postprocessor, "process", None)
        ):
            raise TypeError("postprocessor must expose process() or be None.")
        self._postprocessor = postprocessor
        if query_expander is not None and not callable(
            getattr(query_expander, "expand", None)
        ):
            raise TypeError("query_expander must expose expand() or be None.")
        self._query_expander = query_expander

    def retrieve_for_conversation(
        self, request: ConversationRetrievalRequest
    ) -> RetrievalResult:
        if not isinstance(request, ConversationRetrievalRequest):
            raise TypeError("A ConversationRetrievalRequest is required.")
        actor = _user(request.actor_user_id)
        epoch = _epoch(request.actor_scope_epoch)
        conversation_id = _uuid(request.conversation_id, "conversation_id")
        search_query, retrieval_intent = self._query_plan(request.query)
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                try:
                    conversation = self._conversations.require_accessible(
                        connection,
                        conversation_id=conversation_id,
                        owner_user_id=actor,
                    )
                except ConversationNotFoundError as exc:
                    raise RetrievalNotFoundError(
                        "The requested conversation was not found."
                    ) from exc
                except ConversationScopeError as exc:
                    raise RetrievalScopeStaleError(
                        "The conversation scope is no longer accessible."
                    ) from exc
                if conversation.status != "active":
                    raise RetrievalStateConflictError(
                        "Archived conversations cannot start a retrieval turn."
                    )
                scope = self._scopes.resolve_in_transaction(
                    connection,
                    SessionPrincipal(actor, epoch),
                    conversation.space_id,
                    Capability.READ,
                )
                snapshot = self._retrieve(
                    connection,
                    scope.access_plan,
                    scope.space.id,
                    request,
                    search_query=search_query,
                    retrieval_intent=retrieval_intent,
                )
        finally:
            connection.close()
        snapshot = self._postprocess(snapshot, request)
        return self._result(actor, conversation_id, snapshot)

    def retrieve_for_space(self, request: SpaceRetrievalRequest) -> RetrievalResult:
        if not isinstance(request, SpaceRetrievalRequest):
            raise TypeError("A SpaceRetrievalRequest is required.")
        actor = _user(request.actor_user_id)
        epoch = _epoch(request.actor_scope_epoch)
        space_id = _uuid(request.space_id, "space_id")
        search_query, retrieval_intent = self._query_plan(request.query)
        connection = self._factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                scope = self._scopes.resolve_in_transaction(
                    connection,
                    SessionPrincipal(actor, epoch),
                    space_id,
                    Capability.READ,
                )
                snapshot = self._retrieve(
                    connection,
                    scope.access_plan,
                    scope.space.id,
                    request,
                    search_query=search_query,
                    retrieval_intent=retrieval_intent,
                )
        finally:
            connection.close()
        snapshot = self._postprocess(snapshot, request)
        return self._result(actor, None, snapshot)

    def _postprocess(
        self,
        snapshot: CanonicalRetrievalSnapshot,
        request: ConversationRetrievalRequest | SpaceRetrievalRequest,
    ) -> CanonicalRetrievalSnapshot:
        if self._postprocessor is None:
            return snapshot
        return self._postprocessor.process(
            snapshot,
            query=request.query,
            as_of_ms=request.as_of_ms,
        )

    def _retrieve(
        self,
        connection,
        access_plan: AccessPlan | None,
        space_id: str,
        request: ConversationRetrievalRequest | SpaceRetrievalRequest,
        *,
        search_query: str,
        retrieval_intent: RetrievalQueryIntent,
    ) -> CanonicalRetrievalSnapshot:
        if access_plan is None:
            return self._retrieval.retrieve(
                connection,
                space_id=space_id,
                query=request.query,
                search_query=search_query,
                top_k=request.top_k,
                neighbor_limit=request.neighbor_limit,
                event_limit=request.event_limit,
                retrieval_intent=retrieval_intent,
            )
        closure = access_plan.corpus_closure
        return self._retrieval.retrieve_closure(
            connection,
            selected_space_id=space_id,
            source_space_ids=tuple(
                source.source_space_id for source in closure.sources
            ),
            overlays=tuple(
                {
                    "grant_id": overlay.grant_id,
                    "source_item_id": overlay.source_item_id,
                    "source_revision_id": overlay.source_revision_id,
                    "source_space_id": overlay.source_space_id,
                }
                for overlay in closure.distribution_overlays
            ),
            corpus_closure_sha256=closure.sha256,
            query=request.query,
            search_query=search_query,
            top_k=request.top_k,
            neighbor_limit=request.neighbor_limit,
            event_limit=request.event_limit,
            retrieval_intent=retrieval_intent,
        )

    def _expand_query(self, query: str) -> str:
        return self._query_plan(query)[0]

    def _query_plan(self, query: str) -> tuple[str, RetrievalQueryIntent]:
        if self._query_expander is None:
            return query, "unknown"
        value = self._query_expander.expand(query)
        search_query = value if isinstance(value, str) and value.strip() else query
        intent: object = "unknown"
        intent_reader = getattr(self._query_expander, "intent_for", None)
        if callable(intent_reader):
            intent = intent_reader(query)
        if intent not in {
            "topic", "overview", "continuation", "exhaustive", "unknown"
        }:
            intent = "unknown"
        return search_query, intent  # type: ignore[return-value]

    @staticmethod
    def _result(
        actor: str,
        conversation_id: str | None,
        snapshot: CanonicalRetrievalSnapshot,
    ) -> RetrievalResult:
        return RetrievalResult(
            actor_user_id=actor,
            conversation_id=conversation_id,
            space_id=snapshot.space_id,
            normalized_query=snapshot.normalized_query,
            hits=snapshot.hits,
            semantic_neighbors=snapshot.semantic_neighbors,
            recent_events=snapshot.recent_events,
            projection=snapshot.projection,
            retrieval_source=snapshot.retrieval_source,
            corpus_closure_sha256=snapshot.corpus_closure_sha256,
            ranked_documents=snapshot.ranked_documents,
            hybrid_explain=snapshot.hybrid_explain,
            cache_identity_sha256=snapshot.cache_identity_sha256,
            ranking_envelope=snapshot.ranking_envelope,
            wiki_manifest=snapshot.wiki_manifest,
            query_intent=snapshot.query_intent,
            supplemental_documents=snapshot.supplemental_documents,
        )


__all__ = [
    "CanonicalRetrievalService",
    "ConversationRetrievalRequest",
    "RetrievalNotFoundError",
    "RetrievalResult",
    "RetrievalScopeStaleError",
    "RetrievalServiceError",
    "RetrievalStateConflictError",
    "SpaceRetrievalRequest",
]
