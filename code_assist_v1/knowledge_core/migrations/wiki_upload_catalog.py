"""Opt-in v17 upload entry; never imported by ordinary runtime startup."""
from .synthesis_workflow_catalog import SYNTHESIS_WORKFLOW_MIGRATION_CATALOG
from .model import Migration, MigrationCatalog
from .v017_wiki_upload import V017_POSTCONDITION_QUERIES, V017_STATEMENTS


V017_EXPECTED_SCHEMA_SHA256 = "433ef8621df188e108098be7804efd3c3cdb9fb20acdd74e50ed56e376afbf6f"
SCHEMA_V17_MIGRATION = Migration(version=17, name="durable_wiki_upload_entry_v17",
    statements=V017_STATEMENTS, expected_schema_sha256=V017_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V017_POSTCONDITION_QUERIES)
WIKI_UPLOAD_MIGRATION_CATALOG = MigrationCatalog((*SYNTHESIS_WORKFLOW_MIGRATION_CATALOG.migrations, SCHEMA_V17_MIGRATION))
