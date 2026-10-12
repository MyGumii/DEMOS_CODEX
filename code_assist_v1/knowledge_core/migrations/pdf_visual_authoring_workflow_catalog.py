"""Explicit visual worker successor, not runtime/publication activation."""
from .model import Migration, MigrationCatalog
from .pdf_visual_authoring_storage_catalog import PDF_VISUAL_AUTHORING_STORAGE_MIGRATION_CATALOG
from .v044_pdf_visual_authoring_workflow import V044_STATEMENTS, V044_POSTCONDITION_QUERIES

V044_EXPECTED_SCHEMA_SHA256 = "2725da18c6ce110676440ad1f99f804a26846537637b342e3f76a06f513645b8"
SCHEMA_V44_MIGRATION = Migration(version=44,name="pdf_visual_authoring_workflow_v44",
    statements=V044_STATEMENTS,expected_schema_sha256=V044_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V044_POSTCONDITION_QUERIES)
PDF_VISUAL_AUTHORING_WORKFLOW_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_VISUAL_AUTHORING_STORAGE_MIGRATION_CATALOG.migrations,SCHEMA_V44_MIGRATION,
))
