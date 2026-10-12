"""Read-only, explicit v49 graph rename catalog selection for the 10521 preview.

The deployment migrator creates the receipt only after its schema and worker
checks pass. Reading this module never creates a receipt or runs a migration.
"""

from hashlib import sha256
import json
from pathlib import Path

from code_assist_v1.knowledge_core.migrations.native_semantic_targets_catalog import (
    NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG, V048_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.semantic_rename_reseed_catalog import (
    SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG, SCHEMA_V49_MIGRATION,
    V049_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.wiki_authoring import _json
from code_assist_v1.tools.native_semantic_targets_runtime import (
    ACTIVATION_NAME as PREDECESSOR_ACTIVATION_NAME,
)
from code_assist_v1.tools.pdf_publication_runtime import _pairs
from code_assist_v1.tools.recovered_preview_binding import _read


ACTIVATION_NAME = "semantic-rename-reseed-v49-enabled.json"
CONTRACT_VERSION = "org-rename-reseed-v1"


def catalog_with_semantic_rename_reseed_activation(root, predecessor_catalog):
    """Select v49 only when its exact predecessor and receipt are present."""
    from code_assist_v1.tools import preservation_preview_runtime as previous

    root = Path(root).resolve(strict=True)
    if not previous._is_registered_root(root):
        raise ValueError("Invalid graph rename activation root.")
    path = root / ".runtime" / ACTIVATION_NAME
    if not path.exists():
        return predecessor_catalog
    if predecessor_catalog != NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG:
        raise ValueError("Graph rename activation requires unchanged v48 predecessor.")
    try:
        predecessor_raw = _read(root / ".runtime" / PREDECESSOR_ACTIVATION_NAME)
        marker = json.loads(_read(path), object_pairs_hook=_pairs)
        expected = dict(
            port=10521,
            schema_version=49,
            schema_sha256=V049_EXPECTED_SCHEMA_SHA256,
            data_root=str(root),
            predecessor_schema_version=48,
            predecessor_schema_sha256=V048_EXPECTED_SCHEMA_SHA256,
            predecessor_activation_sha256=sha256(predecessor_raw).hexdigest(),
            migration_checksum_sha256=SCHEMA_V49_MIGRATION.checksum_sha256,
            semantic_rename_reseed_version=CONTRACT_VERSION,
        )
        if _json(marker) != _json(expected):
            raise ValueError("Graph rename activation differs from its contract.")
        return SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG
    except (ValueError, TypeError, OSError, KeyError, UnicodeError) as exc:
        raise ValueError("Invalid graph rename activation receipt.") from exc
