"""Explicit forward v97 candidate catalog; no default/startup activation."""
from .model import Migration, MigrationCatalog
from .pdf_atomic_request_partition_catalog import PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG
from .v097_pdf_context_resume import V097_STATEMENTS, V097_POSTCONDITION_QUERIES

# Candidate fingerprint is filled only from a fresh normal v96->97 migrator
# rejection probe and then verified again by an independent normal migration.
V097_EXPECTED_SCHEMA_SHA256 = "5197e87b61ee27ee732e2f65c7113360f416652ea3359effd52676ce50a68da0"
SCHEMA_V97_MIGRATION = Migration(version=97, name="pdf_context_resume_v97",
    statements=V097_STATEMENTS, expected_schema_sha256=V097_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V097_POSTCONDITION_QUERIES)
PDF_CONTEXT_RESUME_MIGRATION_CATALOG = MigrationCatalog((
    *PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG.migrations, SCHEMA_V97_MIGRATION,
))
