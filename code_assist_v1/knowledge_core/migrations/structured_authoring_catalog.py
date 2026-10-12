"""Explicit v22 candidate catalog; ordinary/live startup remains unchanged."""
from .model import Migration, MigrationCatalog
from .authoring_only_catalog import AUTHORING_ONLY_MIGRATION_CATALOG
from .v022_structured_authoring import V022_POSTCONDITION_QUERIES, V022_STATEMENTS

V022_EXPECTED_SCHEMA_SHA256 = "9dc6e0fa3e3badd4b8c71f1c503dbef781d1939d8040787ad0edf5f5f6e32e41"
SCHEMA_V22_MIGRATION = Migration(version=22, name="structured_authoring_v22",
    statements=V022_STATEMENTS, expected_schema_sha256=V022_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V022_POSTCONDITION_QUERIES)
STRUCTURED_AUTHORING_MIGRATION_CATALOG = MigrationCatalog(
    (*AUTHORING_ONLY_MIGRATION_CATALOG.migrations, SCHEMA_V22_MIGRATION))
