"""Explicit role-worker successor; importing does not activate the service."""
from .model import Migration, MigrationCatalog
from .pptx_role_storage_catalog import PPTX_ROLE_STORAGE_MIGRATION_CATALOG
from .v036_pptx_role_workflow import V036_STATEMENTS, V036_POSTCONDITION_QUERIES

V036_EXPECTED_SCHEMA_SHA256 = "268d9aa21ebdf11d1c4ad540f55733dab76f6dd20da077973a2a9b5a45931319"
SCHEMA_V36_MIGRATION = Migration(version=36, name="pptx_role_authoring_workflow_v36",
    statements=V036_STATEMENTS, expected_schema_sha256=V036_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V036_POSTCONDITION_QUERIES)
PPTX_ROLE_WORKFLOW_MIGRATION_CATALOG = MigrationCatalog((
    *PPTX_ROLE_STORAGE_MIGRATION_CATALOG.migrations, SCHEMA_V36_MIGRATION))
