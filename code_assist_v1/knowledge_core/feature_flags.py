"""Strict, side-effect-free feature flag parsing for Knowledge Core hosts."""

from __future__ import annotations

import os
from typing import Mapping

from .domain import FeatureFlags
from .errors import KnowledgeCoreConfigurationError, KnowledgeCoreNotReadyError


FEATURE_FLAG_NAMES = (
    "KNOWLEDGE_CORE_ENABLED",
    "KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA",
    "KNOWLEDGE_CORE_MULTI_WIKI_READS",
    "KNOWLEDGE_CORE_MULTI_WIKI_WRITES",
    "KNOWLEDGE_CORE_CONVERSATION_WIKI_BINDING",
    "KNOWLEDGE_CORE_READS",
    "KNOWLEDGE_CORE_PERSONAL_WRITES",
    "KNOWLEDGE_CORE_PART_READS",
    "KNOWLEDGE_CORE_PART_WRITES",
    "KNOWLEDGE_CORE_PROJECTIONS",
    "KNOWLEDGE_CORE_PROJECTION_FOOTER_V2",
    "KNOWLEDGE_CORE_HIGHER_SPACES",
    "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS",
    "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL",
    "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_AGGREGATE",
    "KNOWLEDGE_CORE_HYBRID_RERANK",
    "KNOWLEDGE_CORE_LOCAL_CROSS_ENCODER",
    "KNOWLEDGE_CORE_TEMPORAL_RANKING",
    "KNOWLEDGE_CORE_TEAM_WRITES",
    "KNOWLEDGE_CORE_RESPONSIBILITY_WRITES",
)


def _flag(environment: Mapping[str, str], name: str) -> bool:
    raw = environment.get(name, "0")
    if raw not in {"0", "1"}:
        raise KnowledgeCoreConfigurationError(
            f"{name} must be the exact string '0' or '1'."
        )
    return raw == "1"


def read_feature_flags(
    environ: Mapping[str, str] | None = None,
) -> FeatureFlags:
    """Parse and validate rollout flags without reading unrelated config."""

    environment = os.environ if environ is None else environ
    flags = FeatureFlags(
        core_enabled=_flag(environment, "KNOWLEDGE_CORE_ENABLED"),
        multi_wiki_schema=_flag(environment, "KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA"),
        multi_wiki_reads=_flag(environment, "KNOWLEDGE_CORE_MULTI_WIKI_READS"),
        multi_wiki_writes=_flag(environment, "KNOWLEDGE_CORE_MULTI_WIKI_WRITES"),
        conversation_wiki_binding=_flag(
            environment, "KNOWLEDGE_CORE_CONVERSATION_WIKI_BINDING"
        ),
        personal_reads=_flag(environment, "KNOWLEDGE_CORE_READS"),
        personal_writes=_flag(environment, "KNOWLEDGE_CORE_PERSONAL_WRITES"),
        part_reads=_flag(environment, "KNOWLEDGE_CORE_PART_READS"),
        part_writes=_flag(environment, "KNOWLEDGE_CORE_PART_WRITES"),
        projections=_flag(environment, "KNOWLEDGE_CORE_PROJECTIONS"),
        projection_footer_v2=_flag(
            environment, "KNOWLEDGE_CORE_PROJECTION_FOOTER_V2"
        ),
        higher_spaces=_flag(environment, "KNOWLEDGE_CORE_HIGHER_SPACES"),
        aggregate_graph_reads=_flag(
            environment, "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS"
        ),
        auto_semantic_edges_local=_flag(
            environment, "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL"
        ),
        auto_semantic_edges_aggregate=_flag(
            environment, "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_AGGREGATE"
        ),
        hybrid_rerank=_flag(environment, "KNOWLEDGE_CORE_HYBRID_RERANK"),
        local_cross_encoder=_flag(
            environment, "KNOWLEDGE_CORE_LOCAL_CROSS_ENCODER"
        ),
        temporal_ranking=_flag(environment, "KNOWLEDGE_CORE_TEMPORAL_RANKING"),
        team_writes=_flag(environment, "KNOWLEDGE_CORE_TEAM_WRITES"),
        responsibility_writes=_flag(
            environment, "KNOWLEDGE_CORE_RESPONSIBILITY_WRITES"
        ),
    )
    if flags.traffic_or_worker_enabled and not flags.core_enabled:
        raise KnowledgeCoreConfigurationError(
            "A Knowledge Core traffic/worker flag requires KNOWLEDGE_CORE_ENABLED=1."
        )
    if flags.higher_spaces and not flags.part_reads:
        raise KnowledgeCoreConfigurationError(
            "Higher-space reads require canonical Part reads."
        )
    if flags.aggregate_graph_reads and not flags.higher_spaces:
        raise KnowledgeCoreConfigurationError(
            "Aggregate graph reads require higher-space reads."
        )
    if flags.auto_semantic_edges_aggregate and not all(
        (flags.auto_semantic_edges_local, flags.aggregate_graph_reads)
    ):
        raise KnowledgeCoreConfigurationError(
            "Aggregate automatic semantic edges require local automatic edges "
            "and aggregate graph reads."
        )
    if flags.hybrid_rerank and not flags.personal_reads:
        raise KnowledgeCoreConfigurationError(
            "Hybrid retrieval requires canonical personal reads."
        )
    if flags.local_cross_encoder and not flags.hybrid_rerank:
        raise KnowledgeCoreConfigurationError(
            "Local cross-encoder reranking requires hybrid retrieval."
        )
    if flags.temporal_ranking and not flags.hybrid_rerank:
        raise KnowledgeCoreConfigurationError(
            "Temporal ranking requires hybrid retrieval."
        )
    if flags.team_writes and not flags.higher_spaces:
        raise KnowledgeCoreConfigurationError(
            "Team writes require higher-space reads."
        )
    if flags.responsibility_writes and not flags.higher_spaces:
        raise KnowledgeCoreConfigurationError(
            "Responsibility writes require higher-space reads."
        )
    if flags.part_writes:
        raise KnowledgeCoreNotReadyError(
            "Part writes require the complete P1-P7 rollout record and approval."
        )
    if flags.part_reads and not flags.personal_reads:
        raise KnowledgeCoreConfigurationError(
            "Part reads require the canonical personal-read surface."
        )
    if flags.multi_wiki_reads and not all(
        (flags.multi_wiki_schema, flags.personal_reads)
    ):
        raise KnowledgeCoreConfigurationError(
            "Multi-Wiki reads require schema installation and canonical personal reads."
        )
    if flags.multi_wiki_writes and not all(
        (flags.multi_wiki_reads, flags.personal_writes)
    ):
        raise KnowledgeCoreConfigurationError(
            "Multi-Wiki writes require multi-Wiki reads and personal writes."
        )
    if flags.conversation_wiki_binding and not flags.multi_wiki_reads:
        raise KnowledgeCoreConfigurationError(
            "Conversation-Wiki binding requires canonical multi-Wiki reads."
        )
    if flags.projection_footer_v2 and not flags.projections:
        raise KnowledgeCoreConfigurationError(
            "Projection footer v2 requires KNOWLEDGE_CORE_PROJECTIONS=1."
        )
    return flags


__all__ = ["FEATURE_FLAG_NAMES", "read_feature_flags"]
