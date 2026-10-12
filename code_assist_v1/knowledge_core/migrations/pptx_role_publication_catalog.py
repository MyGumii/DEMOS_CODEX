"""Opt-in role publication schema; no default startup activation."""
from .model import Migration, MigrationCatalog
from .pptx_role_workflow_catalog import PPTX_ROLE_WORKFLOW_MIGRATION_CATALOG
from .v037_pptx_role_publication import (
    V037_STATEMENTS, V037_POSTCONDITION_QUERIES, V037_POSTCONDITION_REPLACEMENTS,
)

V037_EXPECTED_SCHEMA_SHA256 = "9fad03157f9dd03efd1e458d86071d720db6aa467bacb5cfa130d399d4e3aa78"
SCHEMA_V37_MIGRATION = Migration(version=37, name="pptx_role_publication_v37",
    statements=V037_STATEMENTS, expected_schema_sha256=V037_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V037_POSTCONDITION_QUERIES, requires_foreign_keys_off=True,
    postcondition_replacements=V037_POSTCONDITION_REPLACEMENTS)
PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG = MigrationCatalog((
    *PPTX_ROLE_WORKFLOW_MIGRATION_CATALOG.migrations, SCHEMA_V37_MIGRATION))
