"""Explicit V85 native curve-prompt successor; imports never activate it."""
from .model import Migration, MigrationCatalog
from .pdf_curve_named_bounds_catalog import PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG
from .v085_pdf_curve_prompt_alignment import V085_STATEMENTS, V085_POSTCONDITION_REPLACEMENTS

V085_EXPECTED_SCHEMA_SHA256 = 'ad267545a360e39924d30cf78a2e5e845b85be4357d7b5f84700cb166d430388'
SCHEMA_V85_MIGRATION = Migration(version=85, name='pdf_curve_prompt_alignment_v85',
    statements=V085_STATEMENTS, expected_schema_sha256=V085_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V085_POSTCONDITION_REPLACEMENTS)
PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG.migrations, SCHEMA_V85_MIGRATION))
