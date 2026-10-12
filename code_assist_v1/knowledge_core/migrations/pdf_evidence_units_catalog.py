"""Explicit v47 successor. No runtime/default activation."""
from .model import Migration, MigrationCatalog
from .pdf_visual_writing_policy_catalog import PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG
from .v047_pdf_evidence_units import V047_STATEMENTS, V047_POSTCONDITION_QUERIES, V047_POSTCONDITION_REPLACEMENTS

V047_EXPECTED_SCHEMA_SHA256 = "6bc9c0785502fe20cf6193ad0c220955ab8911db99c5ea0ee91330567624fe40"
SCHEMA_V47_MIGRATION = Migration(version=47, name="pdf_evidence_units_v47",
    statements=V047_STATEMENTS, expected_schema_sha256=V047_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V047_POSTCONDITION_QUERIES, requires_foreign_keys_off=True,
    postcondition_replacements=V047_POSTCONDITION_REPLACEMENTS)
PDF_EVIDENCE_UNITS_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG.migrations, SCHEMA_V47_MIGRATION,
))
