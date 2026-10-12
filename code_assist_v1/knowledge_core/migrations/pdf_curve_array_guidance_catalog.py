"""Explicit V80 curve-array guidance successor; importing never activates it."""
from .model import Migration, MigrationCatalog
from .pdf_nonthinking_writing_catalog import PDF_NONTHINKING_WRITING_MIGRATION_CATALOG
from .v080_pdf_curve_array_guidance import V080_STATEMENTS, V080_POSTCONDITION_REPLACEMENTS

V080_EXPECTED_SCHEMA_SHA256 = '9d3b00a7bb82eb12fb837c6449e93e70e97eeb6658f87a51d12e5d5950e8c956'
SCHEMA_V80_MIGRATION = Migration(version=80, name='pdf_curve_array_guidance_v80',
    statements=V080_STATEMENTS, expected_schema_sha256=V080_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V080_POSTCONDITION_REPLACEMENTS)
PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_NONTHINKING_WRITING_MIGRATION_CATALOG.migrations, SCHEMA_V80_MIGRATION))
