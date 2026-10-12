"""Explicit v49 graph-rename successor; not a production default switch."""

from .model import Migration, MigrationCatalog
from .native_semantic_targets_catalog import NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG
from .v049_semantic_rename_reseed import V049_STATEMENTS, V049_POSTCONDITION_QUERIES


V049_EXPECTED_SCHEMA_SHA256 = "33b110d2988f1a97a006dbcfa781d71289c58501bf64b438f1b06d60c7c60734"
SCHEMA_V49_MIGRATION = Migration(
    version=49,
    name="semantic_rename_reseed_v49",
    statements=V049_STATEMENTS,
    expected_schema_sha256=V049_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V049_POSTCONDITION_QUERIES,
    requires_foreign_keys_off=True,
)
SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG = MigrationCatalog((
    *NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG.migrations,
    SCHEMA_V49_MIGRATION,
))
