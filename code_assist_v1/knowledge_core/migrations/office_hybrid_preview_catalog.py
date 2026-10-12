"""Explicit v55 successor for a separate Office native/page preview identity."""

from .model import Migration, MigrationCatalog
from .office_hybrid_visual_archive_catalog import OFFICE_HYBRID_VISUAL_ARCHIVE_MIGRATION_CATALOG
from .v055_office_hybrid_preview import V055_POSTCONDITION_QUERIES, V055_STATEMENTS


V055_EXPECTED_SCHEMA_SHA256 = "301898d845504554223531698ca50132dfe878cab1c1d6b43dde70c5fb753757"
SCHEMA_V55_MIGRATION = Migration(
    version=55,
    name="office_hybrid_preview_v55",
    statements=V055_STATEMENTS,
    expected_schema_sha256=V055_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V055_POSTCONDITION_QUERIES,
)
OFFICE_HYBRID_PREVIEW_MIGRATION_CATALOG = MigrationCatalog((
    *OFFICE_HYBRID_VISUAL_ARCHIVE_MIGRATION_CATALOG.migrations,
    SCHEMA_V55_MIGRATION,
))

__all__ = ["OFFICE_HYBRID_PREVIEW_MIGRATION_CATALOG",
    "SCHEMA_V55_MIGRATION", "V055_EXPECTED_SCHEMA_SHA256"]
