"""Explicit V83 bounded text-partition successor; importing never activates it."""
from .model import Migration, MigrationCatalog
from .pdf_finite_text_writing_catalog import PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG
from .v083_pdf_bounded_text_partition import V083_STATEMENTS, V083_POSTCONDITION_REPLACEMENTS

V083_EXPECTED_SCHEMA_SHA256 = 'd7531d69f0a063f9f6ec20248a450c9abb66e32bc922a85497ec173c8e5b9359'
SCHEMA_V83_MIGRATION = Migration(version=83, name='pdf_bounded_text_partition_v83',
    statements=V083_STATEMENTS, expected_schema_sha256=V083_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V083_POSTCONDITION_REPLACEMENTS)
PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG.migrations, SCHEMA_V83_MIGRATION))
