"""Explicit additive PPTX role storage; importing does not migrate or activate."""
from .model import Migration, MigrationCatalog
from .native_display_publication_catalog import NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG
from .v035_pptx_role_storage import V035_STATEMENTS, V035_POSTCONDITION_QUERIES

V035_EXPECTED_SCHEMA_SHA256 = "26e67ef2ad27f1de0c2614710094f6f22a42f08c765d4b4acd09b5deeada6625"
SCHEMA_V35_MIGRATION = Migration(version=35, name="pptx_role_authoring_storage_v35",
    statements=V035_STATEMENTS, expected_schema_sha256=V035_EXPECTED_SCHEMA_SHA256,
    postcondition_queries=V035_POSTCONDITION_QUERIES)
PPTX_ROLE_STORAGE_MIGRATION_CATALOG = MigrationCatalog((
    *NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG.migrations, SCHEMA_V35_MIGRATION))
