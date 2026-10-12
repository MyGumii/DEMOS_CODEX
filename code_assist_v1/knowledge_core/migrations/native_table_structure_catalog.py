"""Explicit v27 candidate catalog; never imported by startup/rollout."""

from .model import Migration, MigrationCatalog
from .strict_context_numeric_types_catalog import STRICT_CONTEXT_NUMERIC_TYPES_MIGRATION_CATALOG
from .v027_native_table_structure import V027_POSTCONDITION_QUERIES, V027_STATEMENTS


# Generated using the exact approved SQLite 3.53.4 source ID in an isolated
# interpreter; fresh/sequential convergence is tested before any activation.
V027_EXPECTED_SCHEMA_SHA256 = "834b25ecfc1367c275dc1c3ca4d32cfd44844d9198006e0a24ce08735c132c01"
SCHEMA_V27_MIGRATION = Migration(
    version=27, name="native_table_structure_v27", statements=V027_STATEMENTS,
    expected_schema_sha256=V027_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V027_POSTCONDITION_QUERIES,
)
NATIVE_TABLE_STRUCTURE_MIGRATION_CATALOG = MigrationCatalog((
    *STRICT_CONTEXT_NUMERIC_TYPES_MIGRATION_CATALOG.migrations,
    SCHEMA_V27_MIGRATION,
))
