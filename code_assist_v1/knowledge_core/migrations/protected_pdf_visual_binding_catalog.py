"""Explicit v51 successor for authorized protected PDF visual sources."""

from .model import Migration, MigrationCatalog
from .protected_extraction_provenance_catalog import PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG
from .v051_protected_pdf_visual_binding import (
    V051_POSTCONDITION_REPLACEMENTS, V051_STATEMENTS,
)


V051_EXPECTED_SCHEMA_SHA256 = "6adb6f468c2cc48fa2ee9a7007a00ef2a02f16e560d0a86a3087ab55f1353cfd"
SCHEMA_V51_MIGRATION = Migration(
    version=51,
    name="protected_pdf_visual_binding_v51",
    statements=V051_STATEMENTS,
    expected_schema_sha256=V051_EXPECTED_SCHEMA_SHA256,
    postcondition_replacements=V051_POSTCONDITION_REPLACEMENTS,
)
PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG = MigrationCatalog((
    *PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG.migrations,
    SCHEMA_V51_MIGRATION,
))


__all__ = [
    "PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG",
    "SCHEMA_V51_MIGRATION", "V051_EXPECTED_SCHEMA_SHA256",
]
