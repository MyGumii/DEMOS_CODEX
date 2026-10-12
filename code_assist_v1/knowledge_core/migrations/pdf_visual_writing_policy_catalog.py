"""Opt-in v2 PDF writing storage. Existing runtime/default catalog is unchanged."""
from .model import Migration, MigrationCatalog
from .pdf_visual_publication_catalog import PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG
from .v046_pdf_visual_writing_policy import (
    V046_STATEMENTS, V046_POSTCONDITION_QUERIES, V046_POSTCONDITION_REPLACEMENTS,
)

V046_EXPECTED_SCHEMA_SHA256 = "07fe2f5b2396bda423405d0e5052799919fdf07e239917243d2802b066bde71f"
SCHEMA_V46_MIGRATION = Migration(version=46, name="pdf_visual_writing_policy_v46",
    statements=V046_STATEMENTS, expected_schema_sha256=V046_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V046_POSTCONDITION_QUERIES, requires_foreign_keys_off=True,
    postcondition_replacements=V046_POSTCONDITION_REPLACEMENTS)
PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG.migrations, SCHEMA_V46_MIGRATION,
))
