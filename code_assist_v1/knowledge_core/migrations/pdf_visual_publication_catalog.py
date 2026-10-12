"""Opt-in seventh publication body; not startup/default activation."""
from .model import Migration, MigrationCatalog
from .pdf_visual_authoring_workflow_catalog import PDF_VISUAL_AUTHORING_WORKFLOW_MIGRATION_CATALOG
from .v045_pdf_visual_publication import V045_STATEMENTS, V045_POSTCONDITION_QUERIES, V045_POSTCONDITION_REPLACEMENTS

V045_EXPECTED_SCHEMA_SHA256 = "a657f6d975bc138f42dd7552325564175c14f882efc7e80c07e566007d35cd8b"
SCHEMA_V45_MIGRATION = Migration(version=45, name="pdf_visual_publication_v45",
    statements=V045_STATEMENTS, expected_schema_sha256=V045_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V045_POSTCONDITION_QUERIES, requires_foreign_keys_off=True,
    postcondition_replacements=V045_POSTCONDITION_REPLACEMENTS)
PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_VISUAL_AUTHORING_WORKFLOW_MIGRATION_CATALOG.migrations, SCHEMA_V45_MIGRATION,
))
