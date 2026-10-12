"""Read-only v41 activation and server-owned PDF interpreter configuration.

Never creates a receipt, migrates, runs a parser/model, or changes a live root.
The configured interpreter is trusted deployment input, never an upload field.
Startup validates its absolute file path; parser dependency/version validation
still occurs in the existing bounded PDF child, not by importing it into web.
"""
import json
from pathlib import Path

from code_assist_v1.knowledge_core.migrations.native_publication_catalog import NATIVE_PUBLICATION_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.native_display_publication_catalog import NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pptx_role_publication_catalog import PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.legacy_authoring_retention_catalog import (
    LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG, V038_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_publication_catalog import (
    PDF_PUBLICATION_MIGRATION_CATALOG, V041_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.v039_pdf_authoring_storage import (
    POLICY, SOURCE_POLICY, GEOMETRY_VERSION, ENGINE_VERSION, ASSEMBLY, TABLE_POLICY, PROMPT_SHA256,
)
from code_assist_v1.knowledge_core.migrations.v040_pdf_authoring_workflow import VERSION, HANDLER_VERSION

ACTIVATION_NAME = "pdf-publication-v41-enabled.json"


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate PDF activation key.")
        value[key] = item
    return value


def pdf_python_for(root):
    """Read the exact server receipt after the caller validates predecessors."""
    from code_assist_v1.tools import preservation_preview_runtime as previous
    from code_assist_v1.tools.clean_10521_runtime import is_clean_root, selection_for

    root = Path(root).resolve(strict=True)
    if is_clean_root(root):
        return selection_for(root).python_executable
    if not previous._is_registered_root(root):
        raise ValueError("Invalid native-publication PDF activation root.")
    try:
        marker = json.loads((root / ".runtime" / ACTIVATION_NAME).read_text(encoding="utf-8"),
            object_pairs_hook=_pairs)
        if type(marker) is not dict or type(marker.get("python_executable")) is not str:
            raise ValueError
        python = Path(marker["python_executable"])
        if not python.is_absolute() or not python.is_file():
            raise ValueError
        python = python.resolve(strict=True)
        expected = dict(port=10421, schema_version=41, schema_sha256=V041_EXPECTED_SCHEMA_SHA256,
            data_root=str(previous.activation_identity_root(root)), predecessor_schema_version=38,
            predecessor_schema_sha256=V038_EXPECTED_SCHEMA_SHA256,
            workflow_version=VERSION, handler_version=HANDLER_VERSION,
            source_preparation_version=SOURCE_POLICY, geometry_version=GEOMETRY_VERSION,
            engine_version=ENGINE_VERSION, assembly_version=ASSEMBLY,
            batch_policy_version=POLICY, table_rendering_version=TABLE_POLICY,
            prompt_sha256=PROMPT_SHA256, semantic_review_performed=False,
            python_executable=str(python))
        if (set(marker) != set(expected) or any(type(marker[k]) is not type(v) or marker[k] != v
                for k, v in expected.items())):
            raise ValueError
    except (ValueError, TypeError, OSError) as exc:
        raise ValueError("Invalid native-publication PDF activation receipt or parser path.") from exc
    return str(python)


def catalog_with_pdf_activation(root, predecessor_catalog):
    """Only a validated v38 predecessor can select the compiled v41 catalog."""
    from code_assist_v1.tools import preservation_preview_runtime as previous

    root = Path(root).resolve(strict=True)
    if not previous._is_registered_root(root):
        raise ValueError("Invalid native-publication PDF activation root.")
    if predecessor_catalog not in (NATIVE_PUBLICATION_MIGRATION_CATALOG,
            NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG, PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG,
            LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG):
        raise ValueError("Invalid native-publication PDF predecessor catalog.")
    if not (root / ".runtime" / ACTIVATION_NAME).exists():
        return predecessor_catalog
    if predecessor_catalog != LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG:
        raise ValueError("Native-publication PDF requires its validated retention predecessor.")
    pdf_python_for(root)
    return PDF_PUBLICATION_MIGRATION_CATALOG
