"""Prospective caption-subject schema; importing never activates a writer."""
from .model import Migration, MigrationCatalog
from .pdf_layout_math_format_catalog import PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG
from .v073_pdf_layout_subject_binding import V073_STATEMENTS, V073_POSTCONDITION_REPLACEMENTS

V073_EXPECTED_SCHEMA_SHA256 = '68b04a91c3d6e0b4576a7e83749daef16f8237be65b5cbe81df3cc8c2a445bfc'
SCHEMA_V73_MIGRATION = Migration(version=73, name='pdf_layout_subject_binding_v73',
    statements=V073_STATEMENTS, expected_schema_sha256=V073_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V073_POSTCONDITION_REPLACEMENTS)
PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG.migrations, SCHEMA_V73_MIGRATION))
