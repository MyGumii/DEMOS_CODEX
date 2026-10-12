"""Explicit v56 successor for restartable Office native/page Wiki receipts."""

from .model import Migration, MigrationCatalog
from .office_hybrid_preview_catalog import OFFICE_HYBRID_PREVIEW_MIGRATION_CATALOG
from .v056_office_hybrid_wiki import V056_POSTCONDITION_QUERIES, V056_STATEMENTS


V056_EXPECTED_SCHEMA_SHA256 = "58395f4f0db3659f1c0df5ef0028cdcb8519aff0c01b6da6c802a17825d63f21"
SCHEMA_V56_MIGRATION = Migration(
    version=56,
    name="office_hybrid_wiki_v56",
    statements=V056_STATEMENTS,
    expected_schema_sha256=V056_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V056_POSTCONDITION_QUERIES,
)
OFFICE_HYBRID_WIKI_MIGRATION_CATALOG = MigrationCatalog((
    *OFFICE_HYBRID_PREVIEW_MIGRATION_CATALOG.migrations,
    SCHEMA_V56_MIGRATION,
))

__all__ = ["OFFICE_HYBRID_WIKI_MIGRATION_CATALOG",
    "SCHEMA_V56_MIGRATION", "V056_EXPECTED_SCHEMA_SHA256"]
