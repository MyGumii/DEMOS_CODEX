"""Explicit V87 text JSON-contract successor; imports never activate it."""
from .model import Migration, MigrationCatalog
from .pdf_visual_contract_alignment_catalog import PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG
from .v087_pdf_text_json_contract_alignment import V087_STATEMENTS, V087_POSTCONDITION_REPLACEMENTS

V087_EXPECTED_SCHEMA_SHA256 = '79858c450a34c30d7bff5ddd6fbacad56851177ae3ca4a533ace5a14d539f441'
SCHEMA_V87_MIGRATION = Migration(version=87, name='pdf_text_json_contract_alignment_v87',
    statements=V087_STATEMENTS, expected_schema_sha256=V087_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V087_POSTCONDITION_REPLACEMENTS)
PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.migrations, SCHEMA_V87_MIGRATION))
