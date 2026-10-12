"""Explicit v53 successor for Office original / PDF derivative provenance."""

from .model import Migration, MigrationCatalog
from .temporal_root_rebinding_catalog import TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG
from .v053_office_visual_derivative import V053_POSTCONDITION_QUERIES, V053_STATEMENTS


V053_EXPECTED_SCHEMA_SHA256 = "3374dde3ef2ba95d33718a34ad0204be3b0a226de9da6025ee2d1e23fa694343"
SCHEMA_V53_MIGRATION = Migration(
    version=53,
    name="office_visual_derivative_v53",
    statements=V053_STATEMENTS,
    expected_schema_sha256=V053_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V053_POSTCONDITION_QUERIES,
)
OFFICE_VISUAL_DERIVATIVE_MIGRATION_CATALOG = MigrationCatalog((
    *TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG.migrations,
    SCHEMA_V53_MIGRATION,
))

__all__ = ["OFFICE_VISUAL_DERIVATIVE_MIGRATION_CATALOG",
    "SCHEMA_V53_MIGRATION", "V053_EXPECTED_SCHEMA_SHA256"]
