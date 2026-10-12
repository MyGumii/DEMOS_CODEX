"""Explicit v54 successor retaining ordinary Office visual input bytes."""

from .model import Migration, MigrationCatalog
from .office_visual_derivative_catalog import OFFICE_VISUAL_DERIVATIVE_MIGRATION_CATALOG
from .v054_office_hybrid_visual_archive import V054_POSTCONDITION_QUERIES, V054_STATEMENTS


V054_EXPECTED_SCHEMA_SHA256 = "ec59ce5a7e75bb0caca84815b90dd011dd3b35c3772cfd791663ec2143ddb329"
SCHEMA_V54_MIGRATION = Migration(
    version=54,
    name="office_hybrid_visual_archive_v54",
    statements=V054_STATEMENTS,
    expected_schema_sha256=V054_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V054_POSTCONDITION_QUERIES,
)
OFFICE_HYBRID_VISUAL_ARCHIVE_MIGRATION_CATALOG = MigrationCatalog((
    *OFFICE_VISUAL_DERIVATIVE_MIGRATION_CATALOG.migrations,
    SCHEMA_V54_MIGRATION,
))

__all__ = ["OFFICE_HYBRID_VISUAL_ARCHIVE_MIGRATION_CATALOG",
    "SCHEMA_V54_MIGRATION", "V054_EXPECTED_SCHEMA_SHA256"]
