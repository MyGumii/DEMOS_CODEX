"""Opt-in authoring catalog for isolated integration tests, not runtime startup."""
from .canonical_catalog import CANONICAL_MIGRATION_CATALOG
from .model import Migration, MigrationCatalog
from .v014_wiki_authoring import V014_POSTCONDITION_QUERIES, V014_STATEMENTS


V014_EXPECTED_SCHEMA_SHA256 = "da3e5236c49f2a3c717d4116ad236b5a297a05f455b509901fc54329b4fcc898"
SCHEMA_V14_MIGRATION = Migration(
    version=14, name="source_preserving_wiki_authoring_v14", statements=V014_STATEMENTS,
    expected_schema_sha256=V014_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V014_POSTCONDITION_QUERIES,
)
AUTHORING_MIGRATION_CATALOG = MigrationCatalog((*CANONICAL_MIGRATION_CATALOG.migrations, SCHEMA_V14_MIGRATION))
