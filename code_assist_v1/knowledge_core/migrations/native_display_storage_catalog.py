"""Explicit storage-only display successor. Not a startup or publication switch."""
from .model import Migration, MigrationCatalog
from .native_publication_catalog import NATIVE_PUBLICATION_MIGRATION_CATALOG
from .v032_native_display_storage import V032_STATEMENTS, V032_POSTCONDITION_QUERIES

V032_EXPECTED_SCHEMA_SHA256 = "7e51ffe16135c133b9c7b4be047aa4abb637ebf024dea1d2ca87c35cf8c21f0f"
SCHEMA_V32_MIGRATION = Migration(version=32, name="native_display_storage_v32",
    statements=V032_STATEMENTS, expected_schema_sha256=V032_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V032_POSTCONDITION_QUERIES)
NATIVE_DISPLAY_STORAGE_MIGRATION_CATALOG = MigrationCatalog((
    *NATIVE_PUBLICATION_MIGRATION_CATALOG.migrations, SCHEMA_V32_MIGRATION))
