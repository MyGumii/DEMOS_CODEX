"""Explicit v63 successor; retained v1-v4 authoring records remain immutable."""

from .model import Migration, MigrationCatalog
from .pdf_fenced_response_snapshot_catalog import PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG
from .v063_pdf_section_writing import (
    V063_STATEMENTS, V063_POSTCONDITION_REPLACEMENTS, V063_POSTCONDITION_QUERIES,
)

V063_EXPECTED_SCHEMA_SHA256 = "3317cd4dcd7e0ab455ff145160428f6cf6d2651cc05a584d84e339e6b38519a7"
SCHEMA_V63_MIGRATION = Migration(
    version=63,
    name="pdf_section_writing_v63",
    statements=V063_STATEMENTS,
    expected_schema_sha256=V063_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True,
    postcondition_queries=V063_POSTCONDITION_QUERIES,
    postcondition_replacements=V063_POSTCONDITION_REPLACEMENTS,
)
PDF_SECTION_WRITING_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG.migrations,
    SCHEMA_V63_MIGRATION,
))
