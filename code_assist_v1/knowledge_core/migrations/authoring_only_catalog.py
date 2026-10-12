"""Explicit isolated-preview v21 catalog; never ordinary startup state."""
from .model import Migration, MigrationCatalog
from .preserved_publication_catalog import PRESERVED_PUBLICATION_MIGRATION_CATALOG
from .v021_authoring_only import V021_POSTCONDITION_QUERIES, V021_STATEMENTS


V021_EXPECTED_SCHEMA_SHA256 = "51e829c4a7119341febb00b7dbe2a5494528286e69a91e60a59e49b88ed8d0f9"
SCHEMA_V21_MIGRATION = Migration(
    version=21,
    name="authoring_only_preservation_v21",
    statements=V021_STATEMENTS,
    expected_schema_sha256=V021_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V021_POSTCONDITION_QUERIES,
)
AUTHORING_ONLY_MIGRATION_CATALOG = MigrationCatalog(
    (*PRESERVED_PUBLICATION_MIGRATION_CATALOG.migrations, SCHEMA_V21_MIGRATION)
)


__all__ = [
    "AUTHORING_ONLY_MIGRATION_CATALOG",
    "SCHEMA_V21_MIGRATION",
    "V021_EXPECTED_SCHEMA_SHA256",
]
