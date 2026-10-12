"""Explicit v58 successor for an eighth, exact Office publication body."""

from .model import Migration, MigrationCatalog
from .office_hybrid_publication_candidate_catalog import (
    OFFICE_HYBRID_PUBLICATION_CANDIDATE_MIGRATION_CATALOG,
)
from .v058_office_hybrid_publication_body import (
    V058_POSTCONDITION_QUERIES, V058_POSTCONDITION_REPLACEMENTS,
    V058_STATEMENTS,
)


V058_EXPECTED_SCHEMA_SHA256 = "2608198ce905c71fd84054bb7a48b146587ab8c2dfd53282ffe6aa199c1d8fd9"
SCHEMA_V58_MIGRATION = Migration(
    version=58,
    name="office_hybrid_publication_body_v58",
    statements=V058_STATEMENTS,
    expected_schema_sha256=V058_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V058_POSTCONDITION_QUERIES,
    postcondition_replacements=V058_POSTCONDITION_REPLACEMENTS,
    requires_foreign_keys_off=True,
)
OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG = MigrationCatalog((
    *OFFICE_HYBRID_PUBLICATION_CANDIDATE_MIGRATION_CATALOG.migrations,
    SCHEMA_V58_MIGRATION,
))


__all__ = ["OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG",
    "SCHEMA_V58_MIGRATION", "V058_EXPECTED_SCHEMA_SHA256"]
