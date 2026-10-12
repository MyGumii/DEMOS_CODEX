"""Composition owned only by the isolated, explicitly migrated 10421 clone."""
from pathlib import Path

REGISTERED_ROOT = Path("D:/CodexTestTemp/SecondBrain-10421-20260911")

_ACTIVATION_NAMES = (
    "preservation-v20-enabled.json",
    "authoring-only-v21-enabled.json",
    "structured-authoring-v22-enabled.json",
    "full-label-authoring-v23-enabled.json",
    "native-publication-v31-enabled.json",
    "native-display-publication-v34-enabled.json",
    "pptx-role-publication-v37-enabled.json",
    "legacy-authoring-retention-v38-enabled.json",
    "pdf-publication-v41-enabled.json",
    "pdf-visual-publication-v45-enabled.json",
    "pdf-visual-writing-v46-enabled.json",
    "pdf-evidence-v47-enabled.json",
    "native-semantic-targets-v48-enabled.json",
    "semantic-rename-reseed-v49-enabled.json",
    "clean-second-brain-10521-v49-enabled.json",
)


def _is_registered_root(root):
    """Reject relocated activation before any legacy bootstrap or DB access.

    The selected root must exist, but checking an unrelated root must not
    require the original disk to remain available. Copied receipts do not
    register a new deployment and must never be mistaken for legacy mode.
    """
    root = Path(root).resolve(strict=True)
    from code_assist_v1.tools.clean_10521_runtime import is_clean_root
    if is_clean_root(root):
        return True
    from code_assist_v1.tools.recovered_preview_binding import load_binding
    if load_binding(root) is not None:
        return True
    if root == REGISTERED_ROOT.resolve(strict=False):
        return True
    runtime = root / ".runtime"
    if any((runtime / name).exists() for name in _ACTIVATION_NAMES):
        raise ValueError("Invalid native-publication activation root.")
    return False


def activation_identity_root(root):
    """Historical receipt identity; never the location used for mutable data."""
    from code_assist_v1.tools.recovered_preview_binding import load_binding
    binding = load_binding(root)
    return binding.source_data_root if binding is not None else Path(root).resolve(strict=True)


def authoring_only_enabled(root):
    root = Path(root).resolve(strict=True)
    if not _is_registered_root(root):
        return False
    import json
    from code_assist_v1.knowledge_core.wiki_preservation import AUTHORING_ONLY_VERSION
    path = root / ".runtime/authoring-only-v21-enabled.json"
    if not path.is_file():
        return False
    marker = json.loads(path.read_text(encoding="utf-8"))
    if (marker.get("port") != 10421 or marker.get("schema_version") != 21
            or marker.get("policy_version") != AUTHORING_ONLY_VERSION
            or marker.get("semantic_review_performed") is not False):
        raise ValueError("Invalid authoring-only activation receipt.")
    return True


def enabled(root):
    root = Path(root).resolve(strict=True)
    if not _is_registered_root(root):
        return False
    import json
    marker = json.loads((root / ".runtime/preservation-v20-enabled.json").read_text(encoding="utf-8")) if (root / ".runtime/preservation-v20-enabled.json").is_file() else {}
    return marker.get("port") == 10421 and marker.get("schema_version") == 20


def structured_authoring_enabled(root):
    """A separate offline receipt activates the policy AND native extractor."""
    root = Path(root).resolve(strict=True)
    if not _is_registered_root(root):
        return False
    import json
    from code_assist_v1.knowledge_core.wiki_preservation import STRUCTURED_AUTHORING_VERSION
    path = root / ".runtime/structured-authoring-v22-enabled.json"
    if not path.is_file():
        return False
    marker = json.loads(path.read_text(encoding="utf-8"))
    if (not enabled(root) or not authoring_only_enabled(root)
            or marker.get("port") != 10421 or marker.get("schema_version") != 22
            or marker.get("policy_version") != STRUCTURED_AUTHORING_VERSION
            or marker.get("extractor_version") != "rich-source-v3-bounded-tables"
            or marker.get("semantic_review_performed") is not False):
        raise ValueError("Invalid structured-authoring activation receipt.")
    return True


def full_label_authoring_enabled(root):
    """v23 is opt-in and retains both historical activation receipts."""
    root = Path(root).resolve(strict=True)
    if not _is_registered_root(root):
        return False
    import json
    from code_assist_v1.knowledge_core.wiki_preservation import CAPTION_AUTHORING_VERSION
    path = root / ".runtime/full-label-authoring-v23-enabled.json"
    if not path.is_file():
        return False
    marker = json.loads(path.read_text(encoding="utf-8"))
    if (not structured_authoring_enabled(root)
            or marker.get("port") != 10421 or marker.get("schema_version") != 23
            or marker.get("policy_version") != CAPTION_AUTHORING_VERSION
            or marker.get("extractor_version") != "rich-source-v3-bounded-tables"
            or marker.get("semantic_review_performed") is not False):
        raise ValueError("Invalid full-label-authoring activation receipt.")
    return True


def extractor_for(root):
    from code_assist_v1.knowledge_core.services.rich_publication import CanonicalRichPublicationExtractor
    structured = full_label_authoring_enabled(root) or structured_authoring_enabled(root)
    return CanonicalRichPublicationExtractor(
        source_only=enabled(root), bounded_table_labels=structured)


def factory_for(root):
    if not enabled(root):
        raise ValueError("10421 preservation must be migrated and explicitly enabled first.")
    from code_assist_v1.knowledge_core.db import ConnectionFactory
    from code_assist_v1.knowledge_core.migrations.preserved_publication_catalog import PRESERVED_PUBLICATION_MIGRATION_CATALOG
    catalog = PRESERVED_PUBLICATION_MIGRATION_CATALOG
    if authoring_only_enabled(root):
        from code_assist_v1.knowledge_core.migrations.authoring_only_catalog import AUTHORING_ONLY_MIGRATION_CATALOG
        catalog = AUTHORING_ONLY_MIGRATION_CATALOG
    if structured_authoring_enabled(root):
        from code_assist_v1.knowledge_core.migrations.structured_authoring_catalog import STRUCTURED_AUTHORING_MIGRATION_CATALOG
        catalog = STRUCTURED_AUTHORING_MIGRATION_CATALOG
    if full_label_authoring_enabled(root):
        from code_assist_v1.knowledge_core.migrations.full_label_authoring_catalog import FULL_LABEL_AUTHORING_MIGRATION_CATALOG
        catalog = FULL_LABEL_AUTHORING_MIGRATION_CATALOG
    from code_assist_v1.tools.native_publication_preview_runtime import native_publication_catalog_for
    native_catalog = native_publication_catalog_for(root)
    if native_catalog is not None:
        catalog = native_catalog
    from code_assist_v1.tools.recovered_preview_binding import load_binding
    binding = load_binding(root)
    factory = (ConnectionFactory(Path(root) / "core/knowledge_core.db", migration_catalog=catalog)
        if binding is not None else
        ConnectionFactory._for_isolated_tests(Path(root) / "core/knowledge_core.db", catalog))
    probe = factory.open(read_only=True)
    probe.close()
    return factory

def workflow_for(factory, *, codex_validation):
    from code_assist_v1.knowledge_core.services.wiki_preservation import WikiPreservationWorkflow, WikiPreservationUploadPolicy
    from code_assist_v1.knowledge_core.wiki_authoring import AuthoringModelPolicy
    from code_assist_v1.knowledge_core.wiki_preservation import PreservingWikiAuthor, AUTHORING_ONLY_VERSION, PRESERVATION_VERSION, STRUCTURED_AUTHORING_VERSION, CAPTION_AUTHORING_VERSION
    from code_assist_v1.codex_response import CodexResponsePolicy
    from code_assist_v1.internal_llm import run_internal_text
    # Same bounded transport used by the real source-to-Wiki run. No fixture
    # content, expected answers or special topic terms enter this adapter.
    response = CodexResponsePolicy(prompt_bytes=65_536, stdout_bytes=2_097_152,
        stderr_bytes=262_144, line_bytes=262_144, event_count=4_096, text_bytes=32_768)
    def runner(task, payload, **kwargs):
        return run_internal_text(task, payload, **kwargs, codex_response_policy=response)
    model = AuthoringModelPolicy(provider_order=("codex",), model_ids=("codex-local",), max_attempts=1) if codex_validation else AuthoringModelPolicy()
    root = Path(factory.database_path).parent.parent
    version = AUTHORING_ONLY_VERSION if authoring_only_enabled(root) else PRESERVATION_VERSION
    if structured_authoring_enabled(root):
        version = STRUCTURED_AUTHORING_VERSION
    if full_label_authoring_enabled(root):
        version = CAPTION_AUTHORING_VERSION
    return WikiPreservationWorkflow(factory, author=PreservingWikiAuthor(runner=runner),
        policy=WikiPreservationUploadPolicy(model=model, preservation_version=version))
