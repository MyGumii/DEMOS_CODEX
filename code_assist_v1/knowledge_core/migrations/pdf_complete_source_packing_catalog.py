"""Explicit complete-source packing successor; import never activates it."""
from .model import Migration, MigrationCatalog
from .pdf_native_frame_contract_catalog import PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG
from .v089_pdf_complete_source_packing import V089_STATEMENTS, V089_POSTCONDITION_REPLACEMENTS

V089_EXPECTED_SCHEMA_SHA256 = '1bab0a4af8f861250701ae0e559a6fe8f1a4b0a556fe12bfa284aaba8ad34826'
SCHEMA_V89_MIGRATION = Migration(version=89, name='pdf_complete_source_packing_v89',
    statements=V089_STATEMENTS, expected_schema_sha256=V089_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V089_POSTCONDITION_REPLACEMENTS)
PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG.migrations, SCHEMA_V89_MIGRATION))
