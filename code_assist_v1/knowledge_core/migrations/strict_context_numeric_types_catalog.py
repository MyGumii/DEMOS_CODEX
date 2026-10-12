"""Explicit v26 numeric-type candidate; no startup or rollout activation."""

from .model import Migration, MigrationCatalog
from .scoped_context_authoring_catalog import (
    SCOPED_CONTEXT_AUTHORING_MIGRATION_CATALOG,
)
from .v026_strict_context_numeric_types import (
    V026_POSTCONDITION_QUERIES,
    V026_STATEMENTS,
)


# Generated from the complete v1-v26 statement chain. Isolated tests prove
# fresh->v26 and retained v25->v26 convergence before rollout is considered.
V026_EXPECTED_SCHEMA_SHA256 = "a3f3a5f7e776b856bd1af4697f3f918c11db3d87c39b7ce186eb630556b17c5e"
SCHEMA_V26_MIGRATION = Migration(
    version=26,
    name="strict_context_numeric_types_v26",
    statements=V026_STATEMENTS,
    expected_schema_sha256=V026_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V026_POSTCONDITION_QUERIES,
)
STRICT_CONTEXT_NUMERIC_TYPES_MIGRATION_CATALOG = MigrationCatalog(
    (
        *SCOPED_CONTEXT_AUTHORING_MIGRATION_CATALOG.migrations,
        SCHEMA_V26_MIGRATION,
    )
)


__all__ = [
    "SCHEMA_V26_MIGRATION",
    "STRICT_CONTEXT_NUMERIC_TYPES_MIGRATION_CATALOG",
    "V026_EXPECTED_SCHEMA_SHA256",
]
