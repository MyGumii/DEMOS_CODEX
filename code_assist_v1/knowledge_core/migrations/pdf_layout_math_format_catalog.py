"""Prospective math-format schema; importing never activates a writer."""
from .model import Migration,MigrationCatalog
from .pdf_layout_retention_catalog import PDF_LAYOUT_RETENTION_MIGRATION_CATALOG
from .v072_pdf_layout_math_format import V072_STATEMENTS,V072_POSTCONDITION_REPLACEMENTS

V072_EXPECTED_SCHEMA_SHA256='9af545a1bc3adfa0d5567f4b9deaab73996c1a2805c4d6f16834cd58f93268cc'
SCHEMA_V72_MIGRATION=Migration(version=72,name='pdf_layout_math_format_v72',
    statements=V072_STATEMENTS,expected_schema_sha256=V072_EXPECTED_SCHEMA_SHA256,
    requires_foreign_keys_off=True,postcondition_replacements=V072_POSTCONDITION_REPLACEMENTS)
PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG=MigrationCatalog((
    *PDF_LAYOUT_RETENTION_MIGRATION_CATALOG.migrations,SCHEMA_V72_MIGRATION))
