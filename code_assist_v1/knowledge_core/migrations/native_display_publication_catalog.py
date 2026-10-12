"""Explicit display publication catalog; no server or migration runs on import."""
from .model import Migration, MigrationCatalog
from .native_display_workflow_catalog import NATIVE_DISPLAY_WORKFLOW_MIGRATION_CATALOG
from .v034_native_display_publication import V034_STATEMENTS, V034_POSTCONDITION_QUERIES

V034_EXPECTED_SCHEMA_SHA256 = "1b6b1086ce7c7cdd12a645eac1e539dd4fea670549ce504fb619b6019423ef6f"
SCHEMA_V34_MIGRATION = Migration(version=34, name="native_display_publication_v34",
    statements=V034_STATEMENTS, expected_schema_sha256=V034_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V034_POSTCONDITION_QUERIES)
NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG = MigrationCatalog((
    *NATIVE_DISPLAY_WORKFLOW_MIGRATION_CATALOG.migrations, SCHEMA_V34_MIGRATION))
