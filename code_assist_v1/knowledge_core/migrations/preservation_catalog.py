"""Opt-in v18 preservation storage; never imported by ordinary startup."""
from .model import Migration, MigrationCatalog
from .v018_wiki_preservation import V018_POSTCONDITION_QUERIES, V018_STATEMENTS
from .wiki_upload_catalog import WIKI_UPLOAD_MIGRATION_CATALOG


V018_EXPECTED_SCHEMA_SHA256 = "9283224fdbb7c2549bf8f71f48fbd995adfc8db1a6210a383b6adbac6c206ca5"
SCHEMA_V18_MIGRATION = Migration(
    version=18,
    name="durable_wiki_preservation_v18",
    statements=V018_STATEMENTS,
    expected_schema_sha256=V018_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V018_POSTCONDITION_QUERIES,
)
PRESERVATION_MIGRATION_CATALOG = MigrationCatalog(
    (*WIKI_UPLOAD_MIGRATION_CATALOG.migrations, SCHEMA_V18_MIGRATION)
)


__all__ = [
    "PRESERVATION_MIGRATION_CATALOG",
    "SCHEMA_V18_MIGRATION",
    "V018_EXPECTED_SCHEMA_SHA256",
]
