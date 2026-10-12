"""Opt-in v60 catalog; live 10521 remains on v59 until routing is proven."""

from .model import Migration, MigrationCatalog
from .office_personal_writer_part_catalog import (
    OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG,
)
from .v060_company_policy_provenance import (
    V060_POSTCONDITION_QUERIES,
    V060_STATEMENTS,
)


V060_EXPECTED_SCHEMA_SHA256 = "807fa46069084ab5fffa1611ceec779ebcd39f861f000be94f3c4ec45421bf6e"
SCHEMA_V60_MIGRATION = Migration(
    version=60,
    name="company_policy_provenance_v60",
    statements=V060_STATEMENTS,
    expected_schema_sha256=V060_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V060_POSTCONDITION_QUERIES,
)
COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG = MigrationCatalog((
    *OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG.migrations,
    SCHEMA_V60_MIGRATION,
))


__all__ = [
    "COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG",
    "SCHEMA_V60_MIGRATION",
    "V060_EXPECTED_SCHEMA_SHA256",
]
