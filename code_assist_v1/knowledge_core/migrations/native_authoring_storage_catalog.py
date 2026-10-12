"""Explicit storage-only successor. Never imported by default startup."""
from .model import Migration, MigrationCatalog
from .native_preview_catalog import NATIVE_PREVIEW_MIGRATION_CATALOG
from .v029_native_authoring_storage import V029_STATEMENTS, V029_POSTCONDITION_QUERIES

V029_EXPECTED_SCHEMA_SHA256 = "cd7b1251b5e37b41a750019444271310e7bfa6490f6ac8f6753beb4020728878"
SCHEMA_V29_MIGRATION = Migration(version=29, name="native_authoring_storage_v29",
    statements=V029_STATEMENTS, expected_schema_sha256=V029_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V029_POSTCONDITION_QUERIES)
NATIVE_AUTHORING_STORAGE_MIGRATION_CATALOG = MigrationCatalog((
    *NATIVE_PREVIEW_MIGRATION_CATALOG.migrations, SCHEMA_V29_MIGRATION))
