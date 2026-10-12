"""Explicit V77 model-domain successor; importing never activates it."""
from .model import Migration, MigrationCatalog
from .pdf_figure_roi_catalog import PDF_FIGURE_ROI_MIGRATION_CATALOG
from .v077_pdf_curve_domain import V077_STATEMENTS, V077_POSTCONDITION_REPLACEMENTS

V077_EXPECTED_SCHEMA_SHA256 = '0ca0763ce616f53aeaf64ce0d992f555b2930ad602b61b3463161a6a5a18dfef'
SCHEMA_V77_MIGRATION = Migration(version=77, name='pdf_curve_domain_v77',
    statements=V077_STATEMENTS, expected_schema_sha256=V077_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V077_POSTCONDITION_REPLACEMENTS)
PDF_CURVE_DOMAIN_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_FIGURE_ROI_MIGRATION_CATALOG.migrations, SCHEMA_V77_MIGRATION))
