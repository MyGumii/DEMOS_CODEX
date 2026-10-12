"""Prospective nonblank-group schema; imports do not activate a writer."""
from .model import Migration, MigrationCatalog
from .pdf_layout_subject_binding_catalog import PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG
from .v074_pdf_layout_nonblank_groups import V074_STATEMENTS, V074_POSTCONDITION_REPLACEMENTS

V074_EXPECTED_SCHEMA_SHA256 = '28ec485292c42b1385b93214d186797e8dd663e07d2eb1dfcbb322ad7f89197b'
SCHEMA_V74_MIGRATION = Migration(version=74, name='pdf_layout_nonblank_groups_v74',
    statements=V074_STATEMENTS, expected_schema_sha256=V074_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V074_POSTCONDITION_REPLACEMENTS)
PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG.migrations, SCHEMA_V74_MIGRATION))
