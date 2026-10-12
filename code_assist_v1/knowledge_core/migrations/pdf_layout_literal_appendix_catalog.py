"""Prospective source-literal registration; imports never activate a writer."""
from .model import Migration, MigrationCatalog
from .pdf_layout_nonblank_groups_catalog import PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG
from .v075_pdf_layout_literal_appendix import V075_STATEMENTS, V075_POSTCONDITION_REPLACEMENTS

V075_EXPECTED_SCHEMA_SHA256 = '62ff8464b7b87b50118dd0ecd5eecd3dc9679a1e983bed083e25f7900bcc8369'
SCHEMA_V75_MIGRATION = Migration(version=75, name='pdf_layout_literal_appendix_v75',
    statements=V075_STATEMENTS, expected_schema_sha256=V075_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V075_POSTCONDITION_REPLACEMENTS)
PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG.migrations, SCHEMA_V75_MIGRATION))
