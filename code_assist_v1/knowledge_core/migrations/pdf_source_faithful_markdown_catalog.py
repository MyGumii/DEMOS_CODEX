"""Prospective v70 catalog; importing it does not activate/migrate anything."""
from .model import Migration,MigrationCatalog
from .pdf_batch_markdown_catalog import PDF_BATCH_MARKDOWN_MIGRATION_CATALOG
from .v070_pdf_source_faithful_markdown import V070_STATEMENTS,V070_POSTCONDITION_REPLACEMENTS

V070_EXPECTED_SCHEMA_SHA256='14fe98c28051cfa79a6b551d0a7fcbec62671cc54576554a0e3495ec52c5e94d'
SCHEMA_V70_MIGRATION=Migration(version=70,name='pdf_source_faithful_markdown_v70',
    statements=V070_STATEMENTS,expected_schema_sha256=V070_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True,postcondition_replacements=V070_POSTCONDITION_REPLACEMENTS)
PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG=MigrationCatalog((
    *PDF_BATCH_MARKDOWN_MIGRATION_CATALOG.migrations,SCHEMA_V70_MIGRATION))
