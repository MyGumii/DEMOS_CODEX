"""Explicit PDF worker successor; no startup or publication activation."""
from .model import Migration, MigrationCatalog
from .pdf_authoring_storage_catalog import PDF_AUTHORING_STORAGE_MIGRATION_CATALOG
from .v040_pdf_authoring_workflow import V040_STATEMENTS, V040_POSTCONDITION_QUERIES

V040_EXPECTED_SCHEMA_SHA256 = "b030c8d8b07e37347133ea56c79d5d4a4548e0a624a8954994cd4ebc113b612b"
SCHEMA_V40_MIGRATION = Migration(version=40, name="pdf_authoring_workflow_v40",
    statements=V040_STATEMENTS, expected_schema_sha256=V040_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V040_POSTCONDITION_QUERIES)
PDF_AUTHORING_WORKFLOW_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_AUTHORING_STORAGE_MIGRATION_CATALOG.migrations, SCHEMA_V40_MIGRATION,
))
