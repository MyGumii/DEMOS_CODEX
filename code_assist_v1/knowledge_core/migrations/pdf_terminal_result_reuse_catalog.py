"""Explicit v98 successor catalog; no default or startup activation.

Physical schema identity was measured by a normal fresh97->98 rejection probe;
the failed probe rolled back to the exact97 ledger with no runtime override.
"""
from .model import Migration, MigrationCatalog
from .pdf_context_resume_catalog import PDF_CONTEXT_RESUME_MIGRATION_CATALOG
from .v098_pdf_terminal_result_reuse import V098_STATEMENTS, V098_POSTCONDITION_QUERIES

if PDF_CONTEXT_RESUME_MIGRATION_CATALOG.checksum_sha256 != "e59c8842b139f816c5f166e71f713e9dba818223918c8ea8d55d752f84bc24ff":
    raise ValueError("The immutable historical97 catalog differs")

V098_EXPECTED_SCHEMA_SHA256 = "c5199f955b602b34193b6b596a35ef2b11b6a01ef3502ab22f88ef000962ce85"
SCHEMA_V98_MIGRATION = Migration(version=98, name="pdf_terminal_result_reuse_v98",
    statements=V098_STATEMENTS, expected_schema_sha256=V098_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V098_POSTCONDITION_QUERIES)
PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_CONTEXT_RESUME_MIGRATION_CATALOG.migrations, SCHEMA_V98_MIGRATION,
))

__all__ = ["V098_EXPECTED_SCHEMA_SHA256", "SCHEMA_V98_MIGRATION", "PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG"]
