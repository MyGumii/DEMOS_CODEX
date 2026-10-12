"""Explicit categorical-axis successor; import never activates it."""
from .model import Migration, MigrationCatalog
from .pdf_complete_source_packing_catalog import PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG
from .v090_pdf_native_categorical_axes import V090_STATEMENTS, V090_POSTCONDITION_REPLACEMENTS

V090_EXPECTED_SCHEMA_SHA256 = '5ab78fe07f283348423a8aa7d5e89c96ab0deea73773e2dd5083ad31c7d0014f'
SCHEMA_V90_MIGRATION = Migration(version=90, name='pdf_native_categorical_axes_v90',
    statements=V090_STATEMENTS, expected_schema_sha256=V090_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V090_POSTCONDITION_REPLACEMENTS)
PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG.migrations, SCHEMA_V90_MIGRATION))
