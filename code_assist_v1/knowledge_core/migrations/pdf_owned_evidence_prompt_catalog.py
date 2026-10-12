"""Opt-in v61 successor for a distinct, source-owned PDF writing prompt."""

from .model import Migration, MigrationCatalog
from .company_policy_provenance_catalog import COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG
from .v061_pdf_owned_evidence_prompt import (
    V061_POSTCONDITION_QUERIES,
    V061_POSTCONDITION_REPLACEMENTS,
    V061_STATEMENTS,
)


# Frozen from a v60 -> v61 physical-schema migration probe; never inferred at runtime.
V061_EXPECTED_SCHEMA_SHA256 = "ed1fd23815bd62e9a9a4aed4f43a8326808956e06e74a54a552e7f84d3791840"
SCHEMA_V61_MIGRATION = Migration(
    version=61,
    name="pdf_owned_evidence_prompt_v61",
    statements=V061_STATEMENTS,
    expected_schema_sha256=V061_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V061_POSTCONDITION_QUERIES,
    postcondition_replacements=V061_POSTCONDITION_REPLACEMENTS,
    requires_foreign_keys_off=True,
)
PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG = MigrationCatalog((
    *COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG.migrations,
    SCHEMA_V61_MIGRATION,
))


__all__ = [
    "PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG",
    "SCHEMA_V61_MIGRATION",
    "V061_EXPECTED_SCHEMA_SHA256",
]
