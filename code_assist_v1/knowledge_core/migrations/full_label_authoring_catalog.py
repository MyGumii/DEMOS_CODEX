"""Explicit v23 full-label candidate; no implicit live migration or activation."""
from .model import Migration, MigrationCatalog
from .structured_authoring_catalog import STRUCTURED_AUTHORING_MIGRATION_CATALOG
from .v023_full_label_authoring import V023_POSTCONDITION_QUERIES, V023_STATEMENTS

V023_EXPECTED_SCHEMA_SHA256 = "a6b97e4ef7bb4646222287c07b5de81a20ef6d0997bb28ee9384a9fd095fe209"
SCHEMA_V23_MIGRATION = Migration(version=23, name="full_label_authoring_v23",
    statements=V023_STATEMENTS, expected_schema_sha256=V023_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V023_POSTCONDITION_QUERIES)
FULL_LABEL_AUTHORING_MIGRATION_CATALOG = MigrationCatalog(
    (*STRUCTURED_AUTHORING_MIGRATION_CATALOG.migrations, SCHEMA_V23_MIGRATION))
