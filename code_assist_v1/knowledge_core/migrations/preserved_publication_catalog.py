"""Explicit isolated-preview successor; never the ordinary startup catalog."""
from .model import Migration, MigrationCatalog
from .preservation_workflow_catalog import PRESERVATION_WORKFLOW_MIGRATION_CATALOG
from .v020_preserved_publication import V020_STATEMENTS, V020_POSTCONDITION_QUERIES

V020_EXPECTED_SCHEMA_SHA256 = "d46e5c62bfba8bee383b211aed00aa871f2ebe3b9df75536efb78b2aa2656ad9"
PRESERVED_PUBLICATION_MIGRATION_CATALOG = MigrationCatalog((
    *PRESERVATION_WORKFLOW_MIGRATION_CATALOG.migrations,
    Migration(version=20, name="preserved_wiki_publication_v20", statements=V020_STATEMENTS,
        expected_schema_sha256=V020_EXPECTED_SCHEMA_SHA256,
        postcondition_queries=V020_POSTCONDITION_QUERIES),
))
