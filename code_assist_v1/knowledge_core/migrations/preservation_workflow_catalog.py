"""Opt-in v19 preservation workflow catalog; never ordinary startup state."""
from .model import Migration, MigrationCatalog
from .preservation_catalog import PRESERVATION_MIGRATION_CATALOG
from .v019_wiki_preservation_workflow import V019_POSTCONDITION_QUERIES, V019_STATEMENTS


V019_EXPECTED_SCHEMA_SHA256 = "a593a225bb38891b8be4b067fb6806a35b9eb83b2f27b3b8331a7930825b25f3"
SCHEMA_V19_MIGRATION = Migration(
    version=19,
    name="durable_wiki_preservation_workflow_v19",
    statements=V019_STATEMENTS,
    expected_schema_sha256=V019_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V019_POSTCONDITION_QUERIES,
)
PRESERVATION_WORKFLOW_MIGRATION_CATALOG = MigrationCatalog(
    (*PRESERVATION_MIGRATION_CATALOG.migrations, SCHEMA_V19_MIGRATION)
)


__all__ = [
    "PRESERVATION_WORKFLOW_MIGRATION_CATALOG",
    "SCHEMA_V19_MIGRATION",
    "V019_EXPECTED_SCHEMA_SHA256",
]
