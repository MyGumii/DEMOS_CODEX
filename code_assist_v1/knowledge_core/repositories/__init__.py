"""Canonical SQL repository boundaries.

P1.1 declares the stable modules only.  Repositories gain connection-scoped
SQL and row mapping in later P1 tasks; importing this package performs no I/O.
"""

REPOSITORY_MODULES = (
    "aggregate_graph",
    "artifacts",
    "audit",
    "automatic_semantic_edges",
    "backup",
    "blob_gc",
    "conversations",
    "curation",
    "curation_metrics",
    "graph",
    "higher_spaces",
    "hybrid_retrieval",
    "items",
    "jobs",
    "leases",
    "management_reads",
    "migration",
    "organization",
    "outbox",
    "personal_wikis",
    "publication",
    "purge",
    "retrieval",
    "retrieval_temporal",
    "semantic_graph_contracts",
    "spaces",
    "temporal",
    "users",
)

__all__ = ["REPOSITORY_MODULES"]
