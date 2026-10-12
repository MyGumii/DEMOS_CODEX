"""Opt-in native publication catalog, never automatic live startup."""
from .model import Migration, MigrationCatalog
from .native_authoring_workflow_catalog import NATIVE_AUTHORING_WORKFLOW_MIGRATION_CATALOG
from .v031_native_publication import V031_STATEMENTS, V031_POSTCONDITION_QUERIES
from .v031_publication_body_selection import extend_native_publication

V031_STATEMENTS, V031_POSTCONDITION_QUERIES = extend_native_publication(
    V031_STATEMENTS, V031_POSTCONDITION_QUERIES)
V031_EXPECTED_SCHEMA_SHA256 = "58961dd77b531529e88c95ad69596745f6a590f864600b2f8de0b4bdb66c1683"
SCHEMA_V31_MIGRATION = Migration(version=31, name="native_publication_v31",
    statements=V031_STATEMENTS, expected_schema_sha256=V031_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V031_POSTCONDITION_QUERIES)
NATIVE_PUBLICATION_MIGRATION_CATALOG = MigrationCatalog((
    *NATIVE_AUTHORING_WORKFLOW_MIGRATION_CATALOG.migrations, SCHEMA_V31_MIGRATION))
