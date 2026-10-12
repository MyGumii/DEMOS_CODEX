"""Frozen, executable migration catalog for canonical schema version 1."""

from __future__ import annotations

from .model import Migration, MigrationCatalog
from .v001_fts import FTS_STATEMENTS
from .v001_indexes import INDEX_STATEMENTS
from .v001_invariants import POSTCONDITION_QUERIES
from .v001_seed import SEED_STATEMENTS
from .v001_tables import TABLE_STATEMENTS
from .v001_triggers import TRIGGER_STATEMENTS


# Generated and independently reproduced from the exact reviewed statement
# order below with SQLite 3.53.4/source-id bf7c...59bcc.  The same manifest was
# also reproduced by the isolated development runtime; production still must
# pass the exact runtime allowlist before it may open or migrate the database.
V001_EXPECTED_SCHEMA_SHA256 = (
    "694fa29e2e184c8ab546e7fb603cf014bc1eb58ded639504548f9fa3f920761e"
)

V001_STATEMENTS: tuple[str, ...] = (
    *TABLE_STATEMENTS,
    *INDEX_STATEMENTS,
    *TRIGGER_STATEMENTS,
    *FTS_STATEMENTS,
    *SEED_STATEMENTS,
)

SCHEMA_V1_MIGRATION = Migration(
    version=1,
    name="canonical_schema_v1",
    statements=V001_STATEMENTS,
    expected_schema_sha256=V001_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=POSTCONDITION_QUERIES,
)

CANONICAL_MIGRATION_CATALOG = MigrationCatalog((SCHEMA_V1_MIGRATION,))


__all__ = [
    "CANONICAL_MIGRATION_CATALOG",
    "SCHEMA_V1_MIGRATION",
    "V001_EXPECTED_SCHEMA_SHA256",
    "V001_STATEMENTS",
]
