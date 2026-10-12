"""Opt-in display worker catalog; publication and startup remain disabled."""
from .model import Migration, MigrationCatalog
from .native_display_storage_catalog import NATIVE_DISPLAY_STORAGE_MIGRATION_CATALOG
from .v033_native_display_workflow import V033_STATEMENTS, V033_POSTCONDITION_QUERIES

V033_EXPECTED_SCHEMA_SHA256 = "b6bf26f9fb0cb5af6fb2652ade2dc9936905cb42825bd2741c1f3114a8520261"
SCHEMA_V33_MIGRATION = Migration(version=33, name="native_display_workflow_v33",
    statements=V033_STATEMENTS, expected_schema_sha256=V033_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V033_POSTCONDITION_QUERIES)
NATIVE_DISPLAY_WORKFLOW_MIGRATION_CATALOG = MigrationCatalog((
    *NATIVE_DISPLAY_STORAGE_MIGRATION_CATALOG.migrations, SCHEMA_V33_MIGRATION))
