"""Explicit original-range admission successor; importing never migrates."""
from .model import Migration, MigrationCatalog
from .pdf_numeric_append_catalog import PDF_NUMERIC_APPEND_MIGRATION_CATALOG
from .v096_pdf_atomic_request_partition import V096_STATEMENTS, V096_POSTCONDITION_REPLACEMENTS

V096_EXPECTED_SCHEMA_SHA256 = '1cdd4225f9cb9297557690876596267e7c72c8fb2a20c36c0906c3ddf62cab1f'
SCHEMA_V96_MIGRATION = Migration(version=96, name='pdf_atomic_request_partition_v96',
    statements=V096_STATEMENTS, expected_schema_sha256=V096_EXPECTED_SCHEMA_SHA256,
    postcondition_replacements=V096_POSTCONDITION_REPLACEMENTS)
PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_NUMERIC_APPEND_MIGRATION_CATALOG.migrations, SCHEMA_V96_MIGRATION))
