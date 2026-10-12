"""Explicit v52 successor for root-aware temporal projection recovery."""

from .model import Migration, MigrationCatalog
from .protected_pdf_visual_binding_catalog import (
    PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG,
)
from .v052_temporal_root_rebinding import (
    V052_POSTCONDITION_QUERIES,
    V052_STATEMENTS,
)


V052_EXPECTED_SCHEMA_SHA256 = "d8f3c308500d721abc16f012eb5edfdb4b1c0ea32f5ec5745dfb97463be74316"
SCHEMA_V52_MIGRATION = Migration(
    version=52,
    name="temporal_root_rebinding_v52",
    statements=V052_STATEMENTS,
    expected_schema_sha256=V052_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V052_POSTCONDITION_QUERIES,
)
TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG = MigrationCatalog((
    *PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG.migrations,
    SCHEMA_V52_MIGRATION,
))

__all__ = [
    "TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG",
    "SCHEMA_V52_MIGRATION", "V052_EXPECTED_SCHEMA_SHA256",
]
