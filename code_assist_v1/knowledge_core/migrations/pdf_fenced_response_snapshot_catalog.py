"""Opt-in v62 successor for exact fenced v4 PDF response storage."""

from .model import Migration, MigrationCatalog
from .pdf_owned_evidence_prompt_catalog import PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG
from .v062_pdf_fenced_response_snapshot import (
    V062_POSTCONDITION_REPLACEMENTS,
    V062_STATEMENTS,
)


# Reviewed against a physical copy of the active v61 10521 schema.
V062_EXPECTED_SCHEMA_SHA256 = "55e6bf684813dbb4e43f2cef9b65d7a80fb4b4593fc440d1bb576588f505d87d"
SCHEMA_V62_MIGRATION = Migration(
    version=62,
    name="pdf_fenced_response_snapshot_v62",
    statements=V062_STATEMENTS,
    expected_schema_sha256=V062_EXPECTED_SCHEMA_SHA256,
    postcondition_replacements=V062_POSTCONDITION_REPLACEMENTS,
)
PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG.migrations,
    SCHEMA_V62_MIGRATION,
))


__all__ = [
    "PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG",
    "SCHEMA_V62_MIGRATION",
    "V062_EXPECTED_SCHEMA_SHA256",
]
