"""Explicit original-prose numeric-append successor; no import activation."""
from .model import Migration, MigrationCatalog
from .pdf_chart_labels_catalog import PDF_CHART_LABELS_MIGRATION_CATALOG
from .v095_pdf_numeric_append import V095_STATEMENTS, V095_POSTCONDITION_REPLACEMENTS

V095_EXPECTED_SCHEMA_SHA256 = 'f354cf99472af3571193d69e9548fb4eed2e5fbda93da51be16b35dc9a79304d'
SCHEMA_V95_MIGRATION = Migration(version=95, name='pdf_numeric_append_v95',
    statements=V095_STATEMENTS, expected_schema_sha256=V095_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V095_POSTCONDITION_REPLACEMENTS)
PDF_NUMERIC_APPEND_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_CHART_LABELS_MIGRATION_CATALOG.migrations, SCHEMA_V95_MIGRATION))
