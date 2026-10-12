"""Authorization and transaction orchestration boundaries.

Services are intentionally unbound in P1.1.  They will own authorization,
idempotency, audit, and outbox orchestration rather than route modules.
"""

SERVICE_MODULES = (
    "agent_context",
    "aggregate_graph",
    "automatic_semantic_edges",
    "backup",
    "backup_retention",
    "blob_gc",
    "conversations",
    "curation",
    "curation_metrics",
    "curation_review",
    "editing",
    "higher_authority",
    "hybrid_reranking",
    "jobs",
    "lifecycle",
    "management_reads",
    "organization",
    "personal_wiki_search",
    "personal_wikis",
    "publication",
    "purge",
    "restore",
    "retrieval",
    "scope_selection",
    "semantic_edge_correction",
    "semantic_edge_seeding",
    "spaces",
    "temporal",
)

__all__ = ["SERVICE_MODULES"]
