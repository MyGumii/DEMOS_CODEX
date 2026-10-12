"""Opt-in v15 catalog for isolated synthesis tests, never runtime startup."""
from .authoring_catalog import AUTHORING_MIGRATION_CATALOG
from .model import Migration, MigrationCatalog
from .v015_wiki_synthesis import V015_POSTCONDITION_QUERIES, V015_STATEMENTS


V015_EXPECTED_SCHEMA_SHA256 = "653832489729cdba46dabb8522cef23167a689ad12ef0aff2c4b41019a1f9d7a"
SCHEMA_V15_MIGRATION = Migration(
    version=15, name="source_bound_wiki_synthesis_v15", statements=V015_STATEMENTS,
    expected_schema_sha256=V015_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V015_POSTCONDITION_QUERIES,
)
SYNTHESIS_MIGRATION_CATALOG = MigrationCatalog(
    (*AUTHORING_MIGRATION_CATALOG.migrations, SCHEMA_V15_MIGRATION))
