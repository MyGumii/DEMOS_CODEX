"""Opt-in native worker catalog; not registered by any live startup."""
from .model import Migration, MigrationCatalog
from .native_authoring_storage_catalog import NATIVE_AUTHORING_STORAGE_MIGRATION_CATALOG
from .v030_native_authoring_workflow import V030_STATEMENTS, V030_POSTCONDITION_QUERIES

V030_EXPECTED_SCHEMA_SHA256 = "dc098d418776826c36b4493f682dd48ec2f88215090261b5253ba967f66840ac"
SCHEMA_V30_MIGRATION = Migration(version=30, name="native_authoring_workflow_v30",
    statements=V030_STATEMENTS, expected_schema_sha256=V030_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V030_POSTCONDITION_QUERIES)
NATIVE_AUTHORING_WORKFLOW_MIGRATION_CATALOG = MigrationCatalog((
    *NATIVE_AUTHORING_STORAGE_MIGRATION_CATALOG.migrations, SCHEMA_V30_MIGRATION))
