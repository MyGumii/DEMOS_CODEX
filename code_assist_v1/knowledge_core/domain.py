"""Phase-independent Knowledge Core value objects.

Domain lifecycle enums arrive with P1.3.  P1.1 defines only host and feature
identity needed to compose the disabled scaffold safely.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class HostKind(str, Enum):
    CODE = "app_code"
    NOTION = "app_notion"
    BLUEPRINT = "blueprint"


class HigherScopeRole(str, Enum):
    TEAM_LEADER = "team_leader"
    RESPONSIBILITY_EXECUTIVE = "responsibility_executive"


class DistributionTargetKind(str, Enum):
    ORG_UNIT = "org_unit"
    NATIVE_SPACE = "native_space"


class SemanticRelationType(str, Enum):
    RELATED = "related"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    DEPENDS_ON = "depends_on"
    EXTENDS = "extends"
    EXAMPLE_OF = "example_of"


class SemanticEdgeDirection(str, Enum):
    SOURCE_TO_TARGET = "source_to_target"
    TARGET_TO_SOURCE = "target_to_source"
    SYMMETRIC = "symmetric"


class SemanticIntentTargetKind(str, Enum):
    LOCAL = "local"
    AGGREGATE = "aggregate"


class SemanticIntentProvenanceKind(str, Enum):
    PUBLICATION = "publication"
    BASELINE_BACKFILL = "baseline_backfill"
    ORG_RETARGET = "org_retarget"
    DETECTOR_RESEED = "detector_reseed"
    LIFECYCLE_REACTIVATE = "lifecycle_reactivate"


class AggregateEdgeStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    NEEDS_REVALIDATION = "needs_revalidation"
    SUPPRESSED = "suppressed"


class ExplicitReferenceAvailability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class TemporalAssertionKind(str, Enum):
    SOURCE_MODIFIED = "source_modified"
    INFORMATION_EFFECTIVE = "information_effective"


class TemporalPrecision(str, Enum):
    INSTANT = "instant"
    DATE = "date"


class TemporalAssertionOrigin(str, Enum):
    USER = "user"
    TRUSTED_UPSTREAM = "trusted_upstream"
    REVIEWED_LEGACY_MANIFEST = "reviewed_legacy_manifest"
    SYSTEM_IMPORT = "system_import"


@dataclass(frozen=True, slots=True)
class FeatureFlags:
    core_enabled: bool = False
    multi_wiki_schema: bool = False
    multi_wiki_reads: bool = False
    multi_wiki_writes: bool = False
    conversation_wiki_binding: bool = False
    personal_reads: bool = False
    personal_writes: bool = False
    part_reads: bool = False
    part_writes: bool = False
    projections: bool = False
    projection_footer_v2: bool = False
    higher_spaces: bool = False
    aggregate_graph_reads: bool = False
    auto_semantic_edges_local: bool = False
    auto_semantic_edges_aggregate: bool = False
    hybrid_rerank: bool = False
    local_cross_encoder: bool = False
    temporal_ranking: bool = False
    team_writes: bool = False
    responsibility_writes: bool = False

    @property
    def traffic_or_worker_enabled(self) -> bool:
        return any(
            (
                self.personal_reads,
                self.personal_writes,
                self.multi_wiki_reads,
                self.multi_wiki_writes,
                self.conversation_wiki_binding,
                self.part_reads,
                self.part_writes,
                self.projections,
                self.projection_footer_v2,
                self.higher_spaces,
                self.aggregate_graph_reads,
                self.auto_semantic_edges_local,
                self.auto_semantic_edges_aggregate,
                self.hybrid_rerank,
                self.local_cross_encoder,
                self.temporal_ranking,
                self.team_writes,
                self.responsibility_writes,
            )
        )

    def as_environment_map(self) -> dict[str, int]:
        return {
            "KNOWLEDGE_CORE_ENABLED": int(self.core_enabled),
            "KNOWLEDGE_CORE_HIGHER_SPACES": int(self.higher_spaces),
            "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS": int(
                self.aggregate_graph_reads
            ),
            "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL": int(
                self.auto_semantic_edges_local
            ),
            "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_AGGREGATE": int(
                self.auto_semantic_edges_aggregate
            ),
            "KNOWLEDGE_CORE_HYBRID_RERANK": int(self.hybrid_rerank),
            "KNOWLEDGE_CORE_LOCAL_CROSS_ENCODER": int(self.local_cross_encoder),
            "KNOWLEDGE_CORE_TEMPORAL_RANKING": int(self.temporal_ranking),
            "KNOWLEDGE_CORE_MULTI_WIKI_READS": int(self.multi_wiki_reads),
            "KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA": int(self.multi_wiki_schema),
            "KNOWLEDGE_CORE_MULTI_WIKI_WRITES": int(self.multi_wiki_writes),
            "KNOWLEDGE_CORE_CONVERSATION_WIKI_BINDING": int(
                self.conversation_wiki_binding
            ),
            "KNOWLEDGE_CORE_PART_READS": int(self.part_reads),
            "KNOWLEDGE_CORE_PART_WRITES": int(self.part_writes),
            "KNOWLEDGE_CORE_PERSONAL_WRITES": int(self.personal_writes),
            "KNOWLEDGE_CORE_PROJECTIONS": int(self.projections),
            "KNOWLEDGE_CORE_PROJECTION_FOOTER_V2": int(
                self.projection_footer_v2
            ),
            "KNOWLEDGE_CORE_READS": int(self.personal_reads),
            "KNOWLEDGE_CORE_RESPONSIBILITY_WRITES": int(
                self.responsibility_writes
            ),
            "KNOWLEDGE_CORE_TEAM_WRITES": int(self.team_writes),
        }


__all__ = [
    "AggregateEdgeStatus",
    "DistributionTargetKind",
    "ExplicitReferenceAvailability",
    "FeatureFlags",
    "HigherScopeRole",
    "HostKind",
    "SemanticEdgeDirection",
    "SemanticIntentProvenanceKind",
    "SemanticIntentTargetKind",
    "SemanticRelationType",
    "TemporalAssertionKind",
    "TemporalAssertionOrigin",
    "TemporalPrecision",
]
