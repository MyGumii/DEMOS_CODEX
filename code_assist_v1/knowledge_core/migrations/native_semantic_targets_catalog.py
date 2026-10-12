"""Explicit v48 successor. No default catalog or deployment activation."""
from .model import Migration, MigrationCatalog
from .pdf_evidence_units_catalog import PDF_EVIDENCE_UNITS_MIGRATION_CATALOG
from .v048_native_semantic_targets import V048_STATEMENTS, V048_POSTCONDITION_QUERIES

V048_EXPECTED_SCHEMA_SHA256 = '60247c793d4660d79d6be517fa3f5cb09a01513fff62e0bd54ef74ab33ebb63f'
SCHEMA_V48_MIGRATION = Migration(version=48, name='native_semantic_targets_v48',
    statements=V048_STATEMENTS, expected_schema_sha256=V048_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V048_POSTCONDITION_QUERIES)
NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_EVIDENCE_UNITS_MIGRATION_CATALOG.migrations, SCHEMA_V48_MIGRATION,
))
