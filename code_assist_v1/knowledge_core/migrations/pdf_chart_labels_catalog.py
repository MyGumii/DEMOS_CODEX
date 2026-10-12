"""Explicit label-context successor; importing never activates a database."""
from .model import Migration, MigrationCatalog
from .pdf_v10_chart_tables_catalog import PDF_V10_CHART_TABLES_MIGRATION_CATALOG
from .v094_pdf_chart_labels import V094_STATEMENTS, V094_POSTCONDITION_REPLACEMENTS

V094_EXPECTED_SCHEMA_SHA256 = '4acdcbae932d34498e4363e8e6141c68be70f291e5792b83fbfc28fef4030232'
SCHEMA_V94_MIGRATION = Migration(version=94,name='pdf_chart_labels_v94',
    statements=V094_STATEMENTS,expected_schema_sha256=V094_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True,postcondition_replacements=V094_POSTCONDITION_REPLACEMENTS)
PDF_CHART_LABELS_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_V10_CHART_TABLES_MIGRATION_CATALOG.migrations,SCHEMA_V94_MIGRATION))
