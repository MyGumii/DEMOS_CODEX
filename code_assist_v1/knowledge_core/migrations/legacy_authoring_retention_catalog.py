"""Opt-in successor; deployment must explicitly select this verified catalog."""
from .model import Migration, MigrationCatalog
from .pptx_role_publication_catalog import PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG
from .v038_legacy_authoring_retention import V038_STATEMENTS, V038_POSTCONDITION_QUERIES

V038_EXPECTED_SCHEMA_SHA256 = "e06c7cd12e6f5ce7d7fb4d1b663c5e7ca18e3073e21be7d92b3fa456d09ee695"
SCHEMA_V38_MIGRATION = Migration(
    version=38, name="legacy_authoring_retention_v38", statements=V038_STATEMENTS,
    expected_schema_sha256=V038_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V038_POSTCONDITION_QUERIES,
)
LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG = MigrationCatalog((
    *PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG.migrations, SCHEMA_V38_MIGRATION,
))
