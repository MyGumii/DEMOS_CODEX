"""Explicit V10 prose/literal-table successor; importing does not activate it."""
from .model import Migration, MigrationCatalog
from .pdf_native_tick_conditions_catalog import PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG
from .v092_pdf_v10_literal_tables import V092_STATEMENTS, V092_POSTCONDITION_REPLACEMENTS

V092_EXPECTED_SCHEMA_SHA256 = '93dc5e2496b1f424b3be76a5568ee4dd989ffc606d864c93c5952c8cf8f7dfb0'
SCHEMA_V92_MIGRATION = Migration(version=92,name='pdf_v10_literal_tables_v92',
    statements=V092_STATEMENTS,expected_schema_sha256=V092_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True,postcondition_replacements=V092_POSTCONDITION_REPLACEMENTS)
PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG.migrations,SCHEMA_V92_MIGRATION))
