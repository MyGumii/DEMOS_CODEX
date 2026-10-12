"""Opt-in PDF publication contract; no default startup activation."""
from .model import Migration, MigrationCatalog
from .pdf_authoring_workflow_catalog import PDF_AUTHORING_WORKFLOW_MIGRATION_CATALOG
from .v041_pdf_publication import (
    V041_STATEMENTS, V041_POSTCONDITION_QUERIES, V041_POSTCONDITION_REPLACEMENTS,
)

V041_EXPECTED_SCHEMA_SHA256 = "5d3e011c64355e60158b0c49edda2d704ec36b2f0102d1ab8263fa296d6eb010"
SCHEMA_V41_MIGRATION = Migration(version=41, name="pdf_publication_v41",
    statements=V041_STATEMENTS, expected_schema_sha256=V041_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V041_POSTCONDITION_QUERIES, requires_foreign_keys_off=True,
    postcondition_replacements=V041_POSTCONDITION_REPLACEMENTS)
PDF_PUBLICATION_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_AUTHORING_WORKFLOW_MIGRATION_CATALOG.migrations, SCHEMA_V41_MIGRATION,
))
