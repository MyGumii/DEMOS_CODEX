"""Explicit v64 successor for v6 JSON-object section authoring."""
from .model import Migration,MigrationCatalog
from .pdf_section_writing_catalog import PDF_SECTION_WRITING_MIGRATION_CATALOG
from .v064_pdf_section_json_output import V064_STATEMENTS,V064_POSTCONDITION_REPLACEMENTS

V064_EXPECTED_SCHEMA_SHA256 = "9cb4b2fdec5a275a994435237f6ef6a730250f05da741d17f53ee73faea30c47"
SCHEMA_V64_MIGRATION = Migration(version=64,name="pdf_section_json_output_v64",
    statements=V064_STATEMENTS,expected_schema_sha256=V064_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True,postcondition_replacements=V064_POSTCONDITION_REPLACEMENTS)
PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_SECTION_WRITING_MIGRATION_CATALOG.migrations,SCHEMA_V64_MIGRATION,
))
