"""Read-only v37 successor selection; activation is owned by deployment.

No receipt creation, migration, model execution, or live root changes occur in
this module. Both the registered root and the validated predecessor are required.
"""
import json
from pathlib import Path

from code_assist_v1.knowledge_core.migrations.native_publication_catalog import NATIVE_PUBLICATION_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.native_display_publication_catalog import NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pptx_role_publication_catalog import (
    PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG, V037_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.v035_pptx_role_storage import (
    POLICY, SOURCE_POLICY, ROLE_VERSION, ROLE_COVERAGE, ASSEMBLY, LITERAL, PROMPT_SHA256,
)
from code_assist_v1.knowledge_core.migrations.v036_pptx_role_workflow import VERSION, HANDLER_VERSION
from code_assist_v1.knowledge_core.native_wiki_authoring import NATIVE_AUTHORING_VERSION
from code_assist_v1.knowledge_core.wiki_preservation import SCOPED_AUTHORING_VERSION

ACTIVATION_NAME = "pptx-role-publication-v37-enabled.json"


def catalog_with_role_activation(root, predecessor_catalog):
    """Extend an already validated v31/v34 opt-in, never infer activation."""
    from code_assist_v1.tools import preservation_preview_runtime as previous

    root = Path(root).resolve(strict=True)
    if not previous._is_registered_root(root):
        raise ValueError("Invalid native-publication role activation root.")
    if predecessor_catalog not in (NATIVE_PUBLICATION_MIGRATION_CATALOG,
                                   NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG):
        raise ValueError("Invalid native-publication role predecessor catalog.")
    path = root / ".runtime" / ACTIVATION_NAME
    if not path.exists():
        return predecessor_catalog
    predecessor_version = (31 if predecessor_catalog == NATIVE_PUBLICATION_MIGRATION_CATALOG else 34)
    expected = {
        "port": 10421, "schema_version": 37,
        "schema_sha256": V037_EXPECTED_SCHEMA_SHA256, "data_root": str(previous.activation_identity_root(root)),
        "predecessor_schema_version": predecessor_version,
        "workflow_version": VERSION, "handler_version": HANDLER_VERSION,
        "source_preparation_version": SOURCE_POLICY, "role_version": ROLE_VERSION,
        "role_context_coverage": ROLE_COVERAGE, "assembly_version": ASSEMBLY,
        "batch_policy_version": POLICY, "literal_render_version": LITERAL,
        "prompt_sha256": PROMPT_SHA256, "word_authoring_version": NATIVE_AUTHORING_VERSION,
        "legacy_authoring_version": SCOPED_AUTHORING_VERSION,
        "semantic_review_performed": False,
    }
    marker = json.loads(path.read_text(encoding="utf-8"))
    if (type(marker) is not dict or set(marker) != set(expected)
            or any(type(marker[key]) is not type(value) or marker[key] != value
                   for key, value in expected.items())):
        raise ValueError("Invalid native-publication role activation receipt.")
    return PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG
