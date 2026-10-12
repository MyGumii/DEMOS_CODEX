"""Read-only explicit legacy-retention successor selection, never a migrator."""
import json
from pathlib import Path

from code_assist_v1.knowledge_core.migrations.native_publication_catalog import NATIVE_PUBLICATION_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.native_display_publication_catalog import NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pptx_role_publication_catalog import (
    PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG, V037_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.legacy_authoring_retention_catalog import (
    LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG, V038_EXPECTED_SCHEMA_SHA256,
)

ACTIVATION_NAME = "legacy-authoring-retention-v38-enabled.json"


def catalog_with_legacy_retention_activation(root, predecessor_catalog):
    """Only the exact registered v37 predecessor may opt in; no inference."""
    from code_assist_v1.tools import preservation_preview_runtime as previous

    root = Path(root).resolve(strict=True)
    if not previous._is_registered_root(root):
        raise ValueError("Invalid native-publication retention activation root.")
    if predecessor_catalog not in (NATIVE_PUBLICATION_MIGRATION_CATALOG,
            NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG, PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG):
        raise ValueError("Invalid native-publication retention predecessor catalog.")
    path = root / ".runtime" / ACTIVATION_NAME
    if not path.exists():
        return predecessor_catalog
    if predecessor_catalog != PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG:
        raise ValueError("Native-publication retention requires its validated role predecessor.")
    expected = dict(port=10421, schema_version=38, schema_sha256=V038_EXPECTED_SCHEMA_SHA256,
        data_root=str(previous.activation_identity_root(root)), predecessor_schema_version=37, predecessor_schema_sha256=V037_EXPECTED_SCHEMA_SHA256,
        retention_version="legacy-authoring-retention-v1")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (type(value) is not dict or set(value) != set(expected)
            or any(type(value[key]) is not type(item) or value[key] != item for key, item in expected.items())):
        raise ValueError("Invalid native-publication retention activation receipt.")
    return LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG
