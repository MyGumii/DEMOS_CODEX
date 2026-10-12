"""Explicit v25 scoped-context candidate; no startup or rollout activation."""

from .boundary_context_authoring_catalog import (
    BOUNDARY_CONTEXT_AUTHORING_MIGRATION_CATALOG,
)
from .model import Migration, MigrationCatalog
from .v025_scoped_context_authoring import (
    V025_POSTCONDITION_QUERIES,
    V025_STATEMENTS,
)


# Generated from the complete v1-v25 statement chain.  Isolated tests prove
# fresh->v25 and retained v24->v25 convergence before rollout is considered.
V025_EXPECTED_SCHEMA_SHA256 = "e797acdf5a18f7cb5c1999f3df3a7a20dc8521c63f1a4f5e3b0891aaad4c4837"
SCHEMA_V25_MIGRATION = Migration(
    version=25,
    name="scoped_context_authoring_v25",
    statements=V025_STATEMENTS,
    expected_schema_sha256=V025_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V025_POSTCONDITION_QUERIES,
)
SCOPED_CONTEXT_AUTHORING_MIGRATION_CATALOG = MigrationCatalog(
    (
        *BOUNDARY_CONTEXT_AUTHORING_MIGRATION_CATALOG.migrations,
        SCHEMA_V25_MIGRATION,
    )
)


__all__ = [
    "SCOPED_CONTEXT_AUTHORING_MIGRATION_CATALOG",
    "SCHEMA_V25_MIGRATION",
    "V025_EXPECTED_SCHEMA_SHA256",
]
