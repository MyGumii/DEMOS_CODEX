"""Explicit chart/table successor catalog; importing never activates a DB."""
from .model import Migration, MigrationCatalog
from .pdf_v10_literal_tables_catalog import PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG
from .v093_pdf_v10_chart_tables import V093_STATEMENTS, V093_POSTCONDITION_REPLACEMENTS, V093_POSTCONDITION_QUERIES

V093_EXPECTED_SCHEMA_SHA256 = '2084c499eecb11fae51d9903207e9d7213b5f13d09d12c0962845dfb41c44c32'
SCHEMA_V93_MIGRATION = Migration(version=93,name='pdf_v10_chart_tables_v93',
    statements=V093_STATEMENTS,expected_schema_sha256=V093_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True,postcondition_replacements=V093_POSTCONDITION_REPLACEMENTS,
    postcondition_queries=V093_POSTCONDITION_QUERIES)
PDF_V10_CHART_TABLES_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG.migrations,SCHEMA_V93_MIGRATION))
