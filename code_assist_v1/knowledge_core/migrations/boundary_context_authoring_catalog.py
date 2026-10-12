"""Explicit v24 bounded-context candidate; no startup or rollout activation."""

from .full_label_authoring_catalog import FULL_LABEL_AUTHORING_MIGRATION_CATALOG
from .model import Migration, MigrationCatalog
from .v024_boundary_context_authoring import (
    V024_POSTCONDITION_QUERIES,
    V024_STATEMENTS,
)


# Generated from the complete v1-v24 statement chain.  The isolated tests also
# prove v23->v24 and fresh->v24 convergence before any rollout is considered.
V024_EXPECTED_SCHEMA_SHA256 = "adcb54dfc76f4a274a72a5bddbeb612b95b963bccc529fd451e1563a0f1b7911"
SCHEMA_V24_MIGRATION = Migration(
    version=24,
    name="boundary_context_authoring_v24",
    statements=V024_STATEMENTS,
    expected_schema_sha256=V024_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V024_POSTCONDITION_QUERIES,
)
BOUNDARY_CONTEXT_AUTHORING_MIGRATION_CATALOG = MigrationCatalog(
    (*FULL_LABEL_AUTHORING_MIGRATION_CATALOG.migrations, SCHEMA_V24_MIGRATION)
)


__all__ = [
    "BOUNDARY_CONTEXT_AUTHORING_MIGRATION_CATALOG",
    "SCHEMA_V24_MIGRATION",
    "V024_EXPECTED_SCHEMA_SHA256",
]
