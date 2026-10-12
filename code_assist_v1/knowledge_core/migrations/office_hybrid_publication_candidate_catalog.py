"""Explicit v57 successor for Office draft publication candidate identity."""

from .model import Migration, MigrationCatalog
from .office_hybrid_wiki_catalog import OFFICE_HYBRID_WIKI_MIGRATION_CATALOG
from .v057_office_hybrid_publication_candidate import (
    V057_POSTCONDITION_QUERIES, V057_STATEMENTS,
)


V057_EXPECTED_SCHEMA_SHA256 = "10ed91b876a9c12e7add351a0dfee8fda8c8ad3f0f05f64b9b99cded345d81ca"
SCHEMA_V57_MIGRATION = Migration(
    version=57,
    name="office_hybrid_publication_candidate_v57",
    statements=V057_STATEMENTS,
    expected_schema_sha256=V057_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V057_POSTCONDITION_QUERIES,
)
OFFICE_HYBRID_PUBLICATION_CANDIDATE_MIGRATION_CATALOG = MigrationCatalog((
    *OFFICE_HYBRID_WIKI_MIGRATION_CATALOG.migrations,
    SCHEMA_V57_MIGRATION,
))


__all__ = ["OFFICE_HYBRID_PUBLICATION_CANDIDATE_MIGRATION_CATALOG",
    "SCHEMA_V57_MIGRATION", "V057_EXPECTED_SCHEMA_SHA256"]
