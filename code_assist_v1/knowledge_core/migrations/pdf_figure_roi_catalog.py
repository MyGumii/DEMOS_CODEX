"""Explicit V76 appearance-input successor; imports never activate it."""
from .model import Migration, MigrationCatalog
from .pdf_layout_literal_appendix_catalog import PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG
from .v076_pdf_figure_roi import V076_STATEMENTS, V076_POSTCONDITION_REPLACEMENTS

V076_EXPECTED_SCHEMA_SHA256 = '09cd942c5e301e627cd3c716a093035c99a2b3e4f6468f51dd1eede299b848f0'
SCHEMA_V76_MIGRATION = Migration(version=76, name='pdf_figure_roi_v76',
    statements=V076_STATEMENTS, expected_schema_sha256=V076_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V076_POSTCONDITION_REPLACEMENTS)
PDF_FIGURE_ROI_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG.migrations, SCHEMA_V76_MIGRATION))
