"""Opt-in PDF storage successor; no automatic migration or runtime activation."""
from .model import Migration, MigrationCatalog
from .legacy_authoring_retention_catalog import LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG
from .v039_pdf_authoring_storage import V039_STATEMENTS, V039_POSTCONDITION_QUERIES

V039_EXPECTED_SCHEMA_SHA256 = "b295772af4b8a79ef0c530f1f74e1fc63ac8e73eb8a85177ba3c9eb5e52582dd"
SCHEMA_V39_MIGRATION = Migration(version=39, name="pdf_authoring_storage_v39",
    statements=V039_STATEMENTS, expected_schema_sha256=V039_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V039_POSTCONDITION_QUERIES)
PDF_AUTHORING_STORAGE_MIGRATION_CATALOG = MigrationCatalog((
    *LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG.migrations, SCHEMA_V39_MIGRATION,
))
