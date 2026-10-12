"""Explicit v50 catalog for the protected-source provenance contract."""

from .model import Migration, MigrationCatalog
from .semantic_rename_reseed_catalog import SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG
from .v050_protected_extraction_provenance import (
    V050_POSTCONDITION_QUERIES, V050_STATEMENTS,
)


V050_EXPECTED_SCHEMA_SHA256 = "c9e0e01f68bca34847f572f52ba8f4287cb911376c8f529ce9831aee5550a106"
SCHEMA_V50_MIGRATION = Migration(
    version=50,
    name="protected_extraction_provenance_v50",
    statements=V050_STATEMENTS,
    expected_schema_sha256=V050_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V050_POSTCONDITION_QUERIES,
)
PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG = MigrationCatalog((
    *SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG.migrations,
    SCHEMA_V50_MIGRATION,
))
