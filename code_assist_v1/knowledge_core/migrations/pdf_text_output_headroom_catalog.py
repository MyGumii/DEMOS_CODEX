"""Explicit V81 text-output headroom successor; importing never activates it."""
from .model import Migration, MigrationCatalog
from .pdf_curve_array_guidance_catalog import PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG
from .v081_pdf_text_output_headroom import V081_STATEMENTS, V081_POSTCONDITION_REPLACEMENTS

V081_EXPECTED_SCHEMA_SHA256 = '09176f1f10f32f9649c505e3b18a7438af1f8a06894356153e84f4c77d2de1e8'
SCHEMA_V81_MIGRATION = Migration(version=81, name='pdf_text_output_headroom_v81',
    statements=V081_STATEMENTS, expected_schema_sha256=V081_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True, postcondition_replacements=V081_POSTCONDITION_REPLACEMENTS)
PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG.migrations, SCHEMA_V81_MIGRATION))
