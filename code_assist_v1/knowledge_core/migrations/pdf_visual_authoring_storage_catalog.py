"""Opt-in visual authoring ledger; not a runtime/default migration catalog."""
from .model import Migration, MigrationCatalog
from .pdf_visual_source_storage_catalog import PDF_VISUAL_SOURCE_STORAGE_MIGRATION_CATALOG
from .v043_pdf_visual_authoring_storage import V043_STATEMENTS, V043_POSTCONDITION_QUERIES

V043_EXPECTED_SCHEMA_SHA256 = "2411e3c1e0cee32b7fafe4b9b1932b75e36caca6d1ff481a6262d24c29997211"
SCHEMA_V43_MIGRATION = Migration(version=43, name="pdf_visual_authoring_storage_v43",
    statements=V043_STATEMENTS, expected_schema_sha256=V043_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V043_POSTCONDITION_QUERIES)
PDF_VISUAL_AUTHORING_STORAGE_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_VISUAL_SOURCE_STORAGE_MIGRATION_CATALOG.migrations, SCHEMA_V43_MIGRATION,
))
