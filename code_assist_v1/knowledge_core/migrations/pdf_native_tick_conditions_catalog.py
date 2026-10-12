"""Explicit tick-condition successor; importing does not activate it."""
from .model import Migration, MigrationCatalog
from .pdf_native_categorical_axes_catalog import PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG
from .v091_pdf_native_tick_conditions import V091_STATEMENTS, V091_POSTCONDITION_REPLACEMENTS

V091_EXPECTED_SCHEMA_SHA256 = 'eaa2fb703448e5e73e37ac1a5afc843ec4524eea879c577a83295a358be50892'
SCHEMA_V91_MIGRATION = Migration(version=91, name='pdf_native_tick_conditions_v91',
    statements=V091_STATEMENTS, expected_schema_sha256=V091_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V091_POSTCONDITION_REPLACEMENTS)
PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG.migrations, SCHEMA_V91_MIGRATION))
