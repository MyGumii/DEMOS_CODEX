"""Explicit V88 native frame successor; imports never activate it."""
from .model import Migration, MigrationCatalog
from .pdf_text_json_contract_alignment_catalog import PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG
from .v088_pdf_native_frame_contract import V088_STATEMENTS, V088_POSTCONDITION_REPLACEMENTS

V088_EXPECTED_SCHEMA_SHA256 = '8293cede30fcbbe7f881920690990be98f94e5a9ff1e082e0d6551c1544e29c9'
SCHEMA_V88_MIGRATION = Migration(version=88, name='pdf_native_frame_contract_v88',
    statements=V088_STATEMENTS, expected_schema_sha256=V088_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V088_POSTCONDITION_REPLACEMENTS)
PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.migrations, SCHEMA_V88_MIGRATION))
