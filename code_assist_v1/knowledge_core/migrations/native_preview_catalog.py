"""Explicit preview-only v28 catalog. Not registered in runtime startup."""

from .model import Migration, MigrationCatalog
from .native_table_structure_catalog import NATIVE_TABLE_STRUCTURE_MIGRATION_CATALOG
from .v028_native_preview import V028_POSTCONDITION_QUERIES, V028_STATEMENTS


V028_EXPECTED_SCHEMA_SHA256 = "09335d3e68074232155d14abe9d594d2538f4bffe177520fe7a59c89261098d3"
SCHEMA_V28_MIGRATION = Migration(
    version=28, name="native_publication_preview_v28", statements=V028_STATEMENTS,
    expected_schema_sha256=V028_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V028_POSTCONDITION_QUERIES,
)
NATIVE_PREVIEW_MIGRATION_CATALOG = MigrationCatalog((
    *NATIVE_TABLE_STRUCTURE_MIGRATION_CATALOG.migrations,
    SCHEMA_V28_MIGRATION,
))
