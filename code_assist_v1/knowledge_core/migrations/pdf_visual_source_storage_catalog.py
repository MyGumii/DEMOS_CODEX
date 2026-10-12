"""Opt-in visual source retention; no runtime/default migration activation."""
from .model import Migration, MigrationCatalog
from .pdf_publication_catalog import PDF_PUBLICATION_MIGRATION_CATALOG
from .v042_pdf_visual_source_storage import V042_STATEMENTS, V042_POSTCONDITION_QUERIES

V042_EXPECTED_SCHEMA_SHA256 = "6bba1ebeabf66af2375b8454a96cd1117054312b0c37cbff6696ea4944228601"
SCHEMA_V42_MIGRATION = Migration(version=42,name="pdf_visual_source_storage_v42",
    statements=V042_STATEMENTS,expected_schema_sha256=V042_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V042_POSTCONDITION_QUERIES)
PDF_VISUAL_SOURCE_STORAGE_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_PUBLICATION_MIGRATION_CATALOG.migrations,SCHEMA_V42_MIGRATION,
))
