"""Explicit V78 writing-guidance successor; importing never activates it."""
from .model import Migration, MigrationCatalog
from .pdf_curve_domain_catalog import PDF_CURVE_DOMAIN_MIGRATION_CATALOG
from .v078_pdf_nonplot_writing import V078_STATEMENTS, V078_POSTCONDITION_REPLACEMENTS

V078_EXPECTED_SCHEMA_SHA256 = 'eb3d64df7bea83cad67028b0d3bd211f6b96fa5b2f8811dc4914d95f1c879c13'
SCHEMA_V78_MIGRATION = Migration(version=78, name='pdf_nonplot_writing_v78',
    statements=V078_STATEMENTS, expected_schema_sha256=V078_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V078_POSTCONDITION_REPLACEMENTS)
PDF_NONPLOT_WRITING_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_CURVE_DOMAIN_MIGRATION_CATALOG.migrations, SCHEMA_V78_MIGRATION))
