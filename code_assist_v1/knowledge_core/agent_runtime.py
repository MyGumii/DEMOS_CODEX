"""Side-effect-free composition of the canonical Second Brain read/turn path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from .agent_execution import (
    CanonicalAgentTurnExecutor,
    CanonicalConversationSummarizer,
    ModelRunner,
    _default_runner,
)
from .composition import CanonicalRouteBindings, ServiceRegistry
from .db import ConnectionFactory
from .db import TransactionMode, transaction
from .domain import FeatureFlags
from .repositories.conversations import ConversationRepository
from .repositories.hybrid_retrieval import HybridRetrievalRepository
from .scope import ScopeResolver
from .services.agent_context import AgentBoundContextResolver, AgentTurnContextService
from .services.aggregate_graph import AggregateGraphService
from .services.conversations import ConversationContextResolver, ConversationService
from .services.management_reads import CanonicalManagementReadService
from .services.personal_wikis import PersonalWikiLifecycleService
from .services.personal_wiki_search import PersonalWikiManagementSearchService
from .services.query_expansion import MultilingualQueryExpansionAdapter
from .services.higher_authority import HigherAuthorityService
from .services.hybrid_reranking import (
    HybridRankingService,
    LocalCrossEncoderAdapter,
    retrieval_query_text,
)
from .services.retrieval import CanonicalRetrievalService
from .services.scope_selection import ScopeSelectionService
from .services.semantic_edge_correction import SemanticEdgeCorrectionService
from .services.temporal import TemporalService
from .services.source_artifacts import SourceArtifactService


@dataclass(frozen=True, slots=True)
class CanonicalAgentRuntime:
    services: ServiceRegistry
    route_bindings: CanonicalRouteBindings
    scope_resolver: ScopeResolver


def canonical_cookie_principal_resolver(
    factory: ConnectionFactory,
) -> Callable[[], object]:
    """Bind the legacy signed-cookie identity to current canonical authority.

    The cookie supplies only the user ID.  Every request re-reads the canonical
    active account and current ``scope_epoch``; role or organization values in
    the cookie never become authorization facts.
    """

    if not isinstance(factory, ConnectionFactory):
        raise TypeError("factory must be a ConnectionFactory.")

    def resolve() -> object:
        from flask import session
        from .repositories.users import UserRepository

        # The cookie is signed by Flask; only its identity claim crosses this
        # boundary.  Legacy file-registry role/status values are deliberately
        # ignored because the canonical account row is the live authority.
        public = session.get("code_assist_user")
        if not isinstance(public, dict):
            return None
        user_id = public.get("id")
        if not isinstance(user_id, str):
            return None
        connection = factory.open(read_only=True)
        try:
            with transaction(connection, TransactionMode.DEFERRED):
                user = UserRepository().require(connection, user_id)
        finally:
            connection.close()
        if user.account_status != "active":
            return None
        # Canonical knowledge routes consume only ``id`` and ``scope_epoch``.
        # The administrator route group additionally needs the current
        # canonical system role; it must come from the same fresh DB read, not
        # from the signed legacy cookie.
        return {
            "id": user.id,
            "role": user.system_role,
            "scope_epoch": user.scope_epoch,
        }

    return resolve


def _revision_blob_loader(stager: object) -> Callable[[str, int], bytes]:
    """Bind the existing CAS verifier without opening files at construction."""
    def load(digest: str, size: int) -> bytes:
        observation, content = stager.verify_existing(
            expected_sha256=digest, expected_size=size,
            media_type="application/octet-stream",
            normalized_content_sha256=None, normalizer_version=None,
        )
        try:
            if not isinstance(content, bytes):
                raise ValueError("The canonical blob verifier returned invalid content.")
            return content
        finally:
            close = getattr(getattr(observation, "lease", None), "close", None)
            if callable(close):
                close()
    return load


def compose_canonical_agent_runtime(
    factory: ConnectionFactory,
    *,
    flags: FeatureFlags,
    principal_resolver: Callable[[], object],
    provider_order: Sequence[str] | None = None,
    model_ids: Sequence[str] | None = None,
    model_runner: ModelRunner = _default_runner,
    conversation_context_resolver: ConversationContextResolver | None = None,
    agent_binding_resolver: AgentBoundContextResolver | None = None,
    revision_content_loader: Callable[[str, int], bytes | str] | None = None,
    scope_resolver: ScopeResolver | None = None,
    conversation_repository: ConversationRepository | None = None,
    personal_wiki_cursor_secret: bytes | None = None,
    higher_authority_cursor_secret: bytes | None = None,
    local_reranker_runner: ModelRunner | None = None,
    local_reranker_model_ids: Sequence[str] | None = None,
    local_reranker_model_revision: str = "local-cross-encoder-unconfigured-v1",
    source_artifact_stager: object | None = None,
) -> CanonicalAgentRuntime:
    """Compose all dependencies needed for canonical one-space Agent traffic.

    Construction opens no database and performs no model/network/file work.
    The returned services own their documented snapshots/transactions, while
    both model adapters execute only after those snapshots have closed.
    """

    if not isinstance(factory, ConnectionFactory):
        raise TypeError("factory must be a ConnectionFactory.")
    if not isinstance(flags, FeatureFlags):
        raise TypeError("flags must be a FeatureFlags snapshot.")
    if not flags.core_enabled or not flags.personal_reads:
        raise ValueError("canonical Agent runtime requires core and personal reads.")
    if (flags.local_cross_encoder or flags.temporal_ranking) and not flags.hybrid_rerank:
        raise ValueError(
            "local cross-encoder and temporal ranking require hybrid retrieval."
        )
    if not callable(principal_resolver):
        raise TypeError("principal_resolver must be callable.")
    if source_artifact_stager is not None and not callable(
        getattr(source_artifact_stager, "verify_existing", None)
    ):
        raise TypeError("source_artifact_stager must provide verify_existing().")
    if revision_content_loader is None and source_artifact_stager is not None:
        revision_content_loader = _revision_blob_loader(source_artifact_stager)
    if scope_resolver is None:
        scope_resolver = ScopeResolver(flags)
    elif not isinstance(scope_resolver, ScopeResolver):
        raise TypeError("scope_resolver must be a ScopeResolver or null.")
    if conversation_repository is None:
        conversation_repository = ConversationRepository(
            allow_higher_spaces=flags.higher_spaces,
            conversation_wiki_binding=flags.conversation_wiki_binding,
        )
    elif not isinstance(conversation_repository, ConversationRepository):
        raise TypeError(
            "conversation_repository must be a ConversationRepository or null."
        )
    conversations = ConversationService(
        factory,
        conversations=conversation_repository,
        context_resolver=conversation_context_resolver,
        scope_resolver=scope_resolver,
    )
    retrieval_repository = (
        HybridRetrievalRepository(
            query_transformer=(retrieval_query_text if flags.temporal_ranking else None),
            prepare_reranker_evidence=flags.local_cross_encoder,
        )
        if flags.hybrid_rerank
        else None
    )
    providers = None if provider_order is None else tuple(provider_order)
    selected_models = None if model_ids is None else tuple(model_ids)
    query_expander = (
        MultilingualQueryExpansionAdapter(
            runner=model_runner,
            provider_order=providers,
            model_ids=selected_models,
        )
        if flags.hybrid_rerank
        else None
    )
    ranking_service = None
    if flags.local_cross_encoder or flags.temporal_ranking:
        adapter = (
            LocalCrossEncoderAdapter(
                runner=(model_runner if local_reranker_runner is None else local_reranker_runner),
                provider_order=("gguf", "codex"),
                model_ids=local_reranker_model_ids,
                model_revision=local_reranker_model_revision,
            )
            if flags.local_cross_encoder
            else None
        )
        ranking_service = HybridRankingService(
            factory,
            local_cross_encoder=flags.local_cross_encoder,
            temporal_ranking=flags.temporal_ranking,
            adapter=adapter,
        )
    personal_wikis = None
    if flags.multi_wiki_reads:
        if (
            not isinstance(personal_wiki_cursor_secret, bytes)
            or len(personal_wiki_cursor_secret) < 32
        ):
            raise ValueError(
                "multi-Wiki reads require an explicit 32-byte cursor signing secret."
            )
        personal_wikis = PersonalWikiLifecycleService(
            factory,
            cursor_secret=personal_wiki_cursor_secret,
            new_wiki_write_enabled=flags.multi_wiki_writes,
        )
    higher_authority = None
    if flags.higher_spaces:
        if (
            not isinstance(higher_authority_cursor_secret, bytes)
            or len(higher_authority_cursor_secret) < 32
        ):
            raise ValueError(
                "higher-space reads require an explicit 32-byte cursor signing secret."
            )
        higher_authority = HigherAuthorityService(
            factory,
            scope_resolver,
            cursor_secret=higher_authority_cursor_secret,
        )
    services = ServiceRegistry(
        scope_selection=ScopeSelectionService(
            factory,
            scope_resolver=scope_resolver,
            conversations=conversation_repository,
        ),
        conversations=conversations,
        retrieval=CanonicalRetrievalService(
            factory,
            scope_resolver=scope_resolver,
            conversations=conversation_repository,
            retrieval=retrieval_repository,
            postprocessor=ranking_service,
            query_expander=query_expander,
        ),
        management_reads=CanonicalManagementReadService(
            factory,
            scope_resolver=scope_resolver,
            content_loader=revision_content_loader,
        ),
        aggregate_graph=(
            AggregateGraphService(factory, scope_resolver)
            if flags.aggregate_graph_reads
            else None
        ),
        agent_context=AgentTurnContextService(
            factory,
            scope_resolver=scope_resolver,
            binding_resolver=agent_binding_resolver,
            conversations=conversation_repository,
            conversation_wiki_binding=flags.conversation_wiki_binding,
            retrieval=retrieval_repository,
            postprocessor=ranking_service,
            query_expander=query_expander,
            content_loader=revision_content_loader,
        ),
        personal_wikis=personal_wikis,
        personal_wiki_search=(
            PersonalWikiManagementSearchService(
                factory, cursor_secret=personal_wiki_cursor_secret
            )
            if flags.hybrid_rerank and personal_wiki_cursor_secret is not None
            else None
        ),
        higher_authority=higher_authority,
        temporal=TemporalService(factory, scope_resolver),
        semantic_edge_correction=(
            SemanticEdgeCorrectionService(factory, scope_resolver)
            if flags.auto_semantic_edges_local
            else None
        ),
        hybrid_ranking=ranking_service,
        source_artifacts=(
            SourceArtifactService(
                factory,
                scope_resolver=scope_resolver,
                stager=source_artifact_stager,
            )
            if source_artifact_stager is not None
            else None
        ),
    )
    bindings = CanonicalRouteBindings(
        turn_executor=CanonicalAgentTurnExecutor(
            runner=model_runner,
            provider_order=providers,
            model_ids=selected_models,
        ),
        conversation_summarizer=CanonicalConversationSummarizer(
            conversations,
            runner=model_runner,
            provider_order=providers,
            model_ids=selected_models,
        ),
        principal_resolver=principal_resolver,
    )
    return CanonicalAgentRuntime(
        services=services,
        route_bindings=bindings,
        scope_resolver=scope_resolver,
    )


__all__ = [
    "CanonicalAgentRuntime",
    "canonical_cookie_principal_resolver",
    "compose_canonical_agent_runtime",
]
