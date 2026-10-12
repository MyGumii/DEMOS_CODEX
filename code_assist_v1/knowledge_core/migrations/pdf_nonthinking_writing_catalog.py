"""Explicit V79 local writing-mode successor; importing never activates it."""
from .model import Migration, MigrationCatalog
from .pdf_nonplot_writing_catalog import PDF_NONPLOT_WRITING_MIGRATION_CATALOG
from .v079_pdf_nonthinking_writing import V079_STATEMENTS, V079_POSTCONDITION_REPLACEMENTS

V079_EXPECTED_SCHEMA_SHA256 = '7409199ffa8bdd3faeaaed70612fcdb69f271fb9ee3688ea63d0adeaf9ecf859'
SCHEMA_V79_MIGRATION = Migration(version=79, name='pdf_nonthinking_writing_v79',
    statements=V079_STATEMENTS, expected_schema_sha256=V079_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V079_POSTCONDITION_REPLACEMENTS)
PDF_NONTHINKING_WRITING_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_NONPLOT_WRITING_MIGRATION_CATALOG.migrations, SCHEMA_V79_MIGRATION))
