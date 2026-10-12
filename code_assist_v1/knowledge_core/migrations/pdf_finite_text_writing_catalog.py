"""Explicit V82 finite text-writing successor; importing never activates it."""
from .model import Migration, MigrationCatalog
from .pdf_text_output_headroom_catalog import PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG
from .v082_pdf_finite_text_writing import V082_STATEMENTS, V082_POSTCONDITION_REPLACEMENTS

V082_EXPECTED_SCHEMA_SHA256 = 'fb54562a2de1557d61bdce054cf890c0592e0224800e393182fdc8f455eff422'
SCHEMA_V82_MIGRATION = Migration(version=82, name='pdf_finite_text_writing_v82',
    statements=V082_STATEMENTS, expected_schema_sha256=V082_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V082_POSTCONDITION_REPLACEMENTS)
PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG.migrations, SCHEMA_V82_MIGRATION))
