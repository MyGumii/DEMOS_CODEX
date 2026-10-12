"""Explicit v67 successor with exact raw-Markdown receipt retention."""
from .model import Migration, MigrationCatalog
from .pdf_section_schema_output_catalog import PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG
from .v067_pdf_section_markdown_output import V067_STATEMENTS,V067_POSTCONDITION_REPLACEMENTS
V067_EXPECTED_SCHEMA_SHA256 = '77f1111f404f5414c8b5ce1e606da13a3863a770e480f641e6a75730d6cc71da'
SCHEMA_V67_MIGRATION = Migration(version=67,name='pdf_section_markdown_output_v67',
    statements=V067_STATEMENTS,expected_schema_sha256=V067_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True,postcondition_replacements=V067_POSTCONDITION_REPLACEMENTS)
PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG.migrations,SCHEMA_V67_MIGRATION))
