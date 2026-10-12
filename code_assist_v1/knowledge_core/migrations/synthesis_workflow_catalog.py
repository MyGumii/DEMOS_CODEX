"""Opt-in v16 workflow catalog, not imported by runtime startup."""
from .synthesis_catalog import SYNTHESIS_MIGRATION_CATALOG
from .model import Migration, MigrationCatalog
from .v016_synthesis_workflow import V016_POSTCONDITION_QUERIES, V016_STATEMENTS


V016_EXPECTED_SCHEMA_SHA256 = "4b40bac740d3c283472a2b71e18badf1b9a93a46bcaa2f5a9befcacbb4c1a322"
SCHEMA_V16_MIGRATION = Migration(
    version=16, name="durable_wiki_synthesis_workflow_v16", statements=V016_STATEMENTS,
    expected_schema_sha256=V016_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V016_POSTCONDITION_QUERIES,
)
SYNTHESIS_WORKFLOW_MIGRATION_CATALOG = MigrationCatalog(
    (*SYNTHESIS_MIGRATION_CATALOG.migrations, SCHEMA_V16_MIGRATION))
