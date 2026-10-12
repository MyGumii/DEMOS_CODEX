"""Explicit v59 successor for personal-only Office Wiki writer starts."""

from .model import Migration, MigrationCatalog
from .office_hybrid_publication_body_catalog import (
    OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG,
)
from .v059_office_personal_writer_part import (
    V059_POSTCONDITION_QUERIES, V059_STATEMENTS,
)


V059_EXPECTED_SCHEMA_SHA256 = "2660387eee9e866a5d5a3ae0906dbe08088cb6fdff4386468902eb31548c5287"
SCHEMA_V59_MIGRATION = Migration(
    version=59,
    name="office_personal_writer_part_v59",
    statements=V059_STATEMENTS,
    expected_schema_sha256=V059_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V059_POSTCONDITION_QUERIES,
)
OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG = MigrationCatalog((
    *OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG.migrations,
    SCHEMA_V59_MIGRATION,
))


__all__ = ["OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG",
    "SCHEMA_V59_MIGRATION", "V059_EXPECTED_SCHEMA_SHA256"]
