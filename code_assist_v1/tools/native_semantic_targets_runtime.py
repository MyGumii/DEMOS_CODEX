"""Read-only opt-in v48 catalog binding, independent of the v47 writing policy.

No migration, receipt creation, feature-flag activation or model call occurs here.
The unchanged v45 -> v46 -> v47 receipts still own document authoring selection.
"""
import json
from pathlib import Path

from code_assist_v1.knowledge_core.migrations.native_semantic_targets_catalog import (
    NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG, SCHEMA_V48_MIGRATION, V048_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_evidence_units_catalog import (
    PDF_EVIDENCE_UNITS_MIGRATION_CATALOG, V047_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.pdf_visual_authoring import VERSION_V3
from code_assist_v1.knowledge_core.wiki_authoring import _json
from code_assist_v1.tools.pdf_publication_runtime import _pairs

ACTIVATION_NAME = 'native-semantic-targets-v48-enabled.json'
CONTRACT_VERSION = 'native-targets-v1'


def catalog_with_native_semantic_targets_activation(root, predecessor_catalog):
    from code_assist_v1.tools import preservation_preview_runtime as previous
    from code_assist_v1.tools.pdf_visual_publication_runtime import pdf_visual_selection_for
    from code_assist_v1.tools.recovered_preview_binding import _read

    root = Path(root).resolve(strict=True)
    if not previous._is_registered_root(root):
        raise ValueError('Invalid native semantic target activation root.')
    path = root / '.runtime' / ACTIVATION_NAME
    if not path.exists():
        return predecessor_catalog
    if predecessor_catalog != PDF_EVIDENCE_UNITS_MIGRATION_CATALOG:
        raise ValueError('Native semantic targets require their unchanged v47 predecessor.')
    try:
        selected = pdf_visual_selection_for(root)
        if selected.policy_version != VERSION_V3:
            raise ValueError('The v47 writing policy is unavailable.')
        marker = json.loads(_read(path), object_pairs_hook=_pairs)
        expected = dict(port=10421, schema_version=48, schema_sha256=V048_EXPECTED_SCHEMA_SHA256,
            data_root=str(previous.activation_identity_root(root)), predecessor_schema_version=47,
            predecessor_schema_sha256=V047_EXPECTED_SCHEMA_SHA256,
            predecessor_activation_sha256=selected.activation_sha256,
            migration_checksum_sha256=SCHEMA_V48_MIGRATION.checksum_sha256,
            native_semantic_targets_version=CONTRACT_VERSION)
        # Canonical JSON also distinguishes bool/float from integer identities.
        if _json(marker) != _json(expected):
            raise ValueError('Native target activation differs from its contract.')
        return NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG
    except (ValueError, TypeError, OSError, KeyError, UnicodeError) as exc:
        raise ValueError('Invalid native semantic target activation receipt.') from exc
