"""Explicit V86 non-native visual-contract successor; imports never activate it."""
from .model import Migration, MigrationCatalog
from .pdf_curve_prompt_alignment_catalog import PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG
from .v086_pdf_visual_contract_alignment import V086_STATEMENTS, V086_POSTCONDITION_REPLACEMENTS

V086_EXPECTED_SCHEMA_SHA256 = '02dddb37f18ba2e3546dad4c25dd2ba3db6dd7c11330243d9703e65aec1516d0'
SCHEMA_V86_MIGRATION = Migration(version=86, name='pdf_visual_contract_alignment_v86',
    statements=V086_STATEMENTS, expected_schema_sha256=V086_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V086_POSTCONDITION_REPLACEMENTS)
PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG.migrations, SCHEMA_V86_MIGRATION))
