"""Read-only, explicit v31/v34/v37/v38/v41/v45 composition for the registered clone.

This module does not create activation receipts, migrate, start workers, or call
models. The deployment migrator owns activation after the remaining gates pass.
Missing activation keeps the historical path; invalid activation fails closed.
"""
import json
import os
from pathlib import Path

from code_assist_v1.knowledge_core.migrations.native_publication_catalog import (
    NATIVE_PUBLICATION_MIGRATION_CATALOG,
    V031_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.native_display_publication_catalog import (
    NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG, V034_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.v032_native_display_storage import (
    DISPLAY_SOURCE_POLICY, DISPLAY_SLOT_POLICY, DISPLAY_ASSEMBLY_POLICY,
    DISPLAY_POLICY, DISPLAY_PROMPT_SHA256,
)
from code_assist_v1.knowledge_core.migrations.v033_native_display_workflow import DISPLAY_WORKFLOW_VERSION
from code_assist_v1.knowledge_core.native_wiki_batches import (
    NATIVE_BATCH_VERSION, NativeWikiBatchAuthor,
)
from code_assist_v1.knowledge_core.native_wiki_authoring import NATIVE_AUTHORING_VERSION
from code_assist_v1.knowledge_core.wiki_preservation import (
    SCOPED_AUTHORING_VERSION, PreservingWikiAuthor,
)
from code_assist_v1.tools.pptx_role_publication_runtime import (
    ACTIVATION_NAME as PPTX_ROLE_ACTIVATION_NAME,
    PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG,
    catalog_with_role_activation,
)
from code_assist_v1.tools.legacy_authoring_retention_runtime import (
    ACTIVATION_NAME as LEGACY_RETENTION_ACTIVATION_NAME,
    LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG,
    catalog_with_legacy_retention_activation,
)
from code_assist_v1.tools.pdf_publication_runtime import (
    ACTIVATION_NAME as PDF_ACTIVATION_NAME, PDF_PUBLICATION_MIGRATION_CATALOG,
    catalog_with_pdf_activation, pdf_python_for,
)
from code_assist_v1.tools.pdf_visual_publication_runtime import (
    ACTIVATION_NAME as PDF_VISUAL_ACTIVATION_NAME, PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG,
    WRITING_V2_ACTIVATION_NAME, EVIDENCE_V3_ACTIVATION_NAME, VISUAL_CATALOGS,
    catalog_with_pdf_visual_activation, pdf_visual_selection_for, visual_runner_for,
)
from code_assist_v1.tools.native_semantic_targets_runtime import (
    ACTIVATION_NAME as NATIVE_TARGETS_ACTIVATION_NAME,
    catalog_with_native_semantic_targets_activation,
)
from code_assist_v1.tools.semantic_rename_reseed_runtime import (
    ACTIVATION_NAME as RENAME_RESEED_ACTIVATION_NAME,
    catalog_with_semantic_rename_reseed_activation,
)
from code_assist_v1.knowledge_core.migrations.office_hybrid_publication_body_catalog import (
    OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG,
)
from code_assist_v1.knowledge_core.migrations.office_personal_writer_part_catalog import (
    OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG,
)
from code_assist_v1.knowledge_core.migrations.company_policy_provenance_catalog import (
    COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
)
from code_assist_v1.knowledge_core.migrations.pdf_owned_evidence_prompt_catalog import (
    PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG,
)
from code_assist_v1.knowledge_core.migrations.pdf_fenced_response_snapshot_catalog import (
    PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG,
)
from code_assist_v1.knowledge_core.migrations.pdf_section_writing_catalog import PDF_SECTION_WRITING_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_section_json_output_catalog import PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_section_guard_dispatch_catalog import PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_section_schema_output_catalog import PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_section_markdown_output_catalog import PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_section_markdown_delimiters_catalog import PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_batch_markdown_catalog import PDF_BATCH_MARKDOWN_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_source_faithful_markdown_catalog import PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_layout_retention_catalog import PDF_LAYOUT_RETENTION_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_layout_math_format_catalog import PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_layout_subject_binding_catalog import PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_layout_nonblank_groups_catalog import PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_layout_literal_appendix_catalog import PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_figure_roi_catalog import PDF_FIGURE_ROI_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_curve_domain_catalog import PDF_CURVE_DOMAIN_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_nonplot_writing_catalog import PDF_NONPLOT_WRITING_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_nonthinking_writing_catalog import PDF_NONTHINKING_WRITING_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_curve_array_guidance_catalog import PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_text_output_headroom_catalog import PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_finite_text_writing_catalog import PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_bounded_text_partition_catalog import PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_curve_named_bounds_catalog import PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_curve_prompt_alignment_catalog import PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_visual_contract_alignment_catalog import PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_text_json_contract_alignment_catalog import PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_native_frame_contract_catalog import PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_complete_source_packing_catalog import PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_native_categorical_axes_catalog import PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_v10_literal_tables_catalog import PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_v10_chart_tables_catalog import PDF_V10_CHART_TABLES_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_chart_labels_catalog import PDF_CHART_LABELS_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_numeric_append_catalog import PDF_NUMERIC_APPEND_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_atomic_request_partition_catalog import PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_context_resume_catalog import PDF_CONTEXT_RESUME_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_terminal_result_reuse_catalog import PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG

ACTIVATION_NAME = "native-publication-v31-enabled.json"
DISPLAY_ACTIVATION_NAME = "native-display-publication-v34-enabled.json"
_ROLE_CATALOGS = (PPTX_ROLE_PUBLICATION_MIGRATION_CATALOG, LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG,
                  PDF_PUBLICATION_MIGRATION_CATALOG, *VISUAL_CATALOGS,
                  OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG,
                  OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG,
                  COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
                  PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG,
                  PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG, PDF_SECTION_WRITING_MIGRATION_CATALOG, PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG, PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG, PDF_BATCH_MARKDOWN_MIGRATION_CATALOG, PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG)
_PDF_CATALOGS = (PDF_PUBLICATION_MIGRATION_CATALOG, *VISUAL_CATALOGS,
                 OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG,
                 OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG,
                 COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
                 PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG,
                 PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG, PDF_SECTION_WRITING_MIGRATION_CATALOG, PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG, PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG, PDF_BATCH_MARKDOWN_MIGRATION_CATALOG, PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG)
_VISUAL_CATALOGS = (*VISUAL_CATALOGS, OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG,
                    OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG,
                    COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
                    PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG,
                    PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG, PDF_SECTION_WRITING_MIGRATION_CATALOG, PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG, PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG, PDF_BATCH_MARKDOWN_MIGRATION_CATALOG, PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG)


def native_publication_catalog_for(root):
    """Read the exact opt-in catalog; never migrate or create activation files."""
    from code_assist_v1.tools import preservation_preview_runtime as previous
    from code_assist_v1.tools.clean_10521_runtime import is_clean_root, catalog_for

    root = Path(root).resolve(strict=True)
    if is_clean_root(root):
        return catalog_for(root)
    if not previous._is_registered_root(root):
        return None
    path = root / ".runtime" / ACTIVATION_NAME
    display_path = root / ".runtime" / DISPLAY_ACTIVATION_NAME
    if not path.is_file():
        if (display_path.exists() or (root / ".runtime" / PPTX_ROLE_ACTIVATION_NAME).exists()
                or (root / ".runtime" / LEGACY_RETENTION_ACTIVATION_NAME).exists()
                or (root / ".runtime" / PDF_ACTIVATION_NAME).exists()
                or (root / ".runtime" / PDF_VISUAL_ACTIVATION_NAME).exists()
                or (root / ".runtime" / WRITING_V2_ACTIVATION_NAME).exists()
                or (root / ".runtime" / EVIDENCE_V3_ACTIVATION_NAME).exists()
                or (root / ".runtime" / NATIVE_TARGETS_ACTIVATION_NAME).exists()
                or (root / ".runtime" / RENAME_RESEED_ACTIVATION_NAME).exists()):
            raise ValueError("Invalid native-publication predecessor activation receipt.")
        return None
    expected = {
        "port": 10421,
        "schema_version": 31,
        "schema_sha256": V031_EXPECTED_SCHEMA_SHA256,
        "data_root": str(previous.activation_identity_root(root)),
        "native_authoring_version": NATIVE_AUTHORING_VERSION,
        "native_batch_version": NATIVE_BATCH_VERSION,
        "legacy_authoring_version": SCOPED_AUTHORING_VERSION,
        "semantic_review_performed": False,
    }
    marker = json.loads(path.read_text(encoding="utf-8"))
    if (type(marker) is not dict or set(marker) != set(expected)
            or any(type(marker[key]) is not type(value) or marker[key] != value
                   for key, value in expected.items())
            or not previous.full_label_authoring_enabled(root)):
        raise ValueError("Invalid native-publication activation receipt.")
    if not display_path.exists():
        return catalog_with_semantic_rename_reseed_activation(root, catalog_with_native_semantic_targets_activation(root, catalog_with_pdf_visual_activation(root, catalog_with_pdf_activation(root,
            catalog_with_legacy_retention_activation(root,
                catalog_with_role_activation(root, NATIVE_PUBLICATION_MIGRATION_CATALOG))))))
    expected_display = {
        "port": 10421, "schema_version": 34,
        "schema_sha256": V034_EXPECTED_SCHEMA_SHA256, "data_root": str(previous.activation_identity_root(root)),
        "workflow_version": DISPLAY_WORKFLOW_VERSION,
        "source_preparation_version": DISPLAY_SOURCE_POLICY,
        "slot_policy_version": DISPLAY_SLOT_POLICY,
        "assembly_version": DISPLAY_ASSEMBLY_POLICY,
        "batch_policy_version": DISPLAY_POLICY, "prompt_sha256": DISPLAY_PROMPT_SHA256,
        "word_authoring_version": NATIVE_AUTHORING_VERSION,
        "legacy_authoring_version": SCOPED_AUTHORING_VERSION,
        "semantic_review_performed": False,
    }
    marker = json.loads(display_path.read_text(encoding="utf-8"))
    if (type(marker) is not dict or set(marker) != set(expected_display)
            or any(type(marker[key]) is not type(value) or marker[key] != value
                   for key, value in expected_display.items())):
        raise ValueError("Invalid native-publication display activation receipt.")
    return catalog_with_semantic_rename_reseed_activation(root, catalog_with_native_semantic_targets_activation(root, catalog_with_pdf_visual_activation(root, catalog_with_pdf_activation(root,
        catalog_with_legacy_retention_activation(root,
            catalog_with_role_activation(root, NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG))))))


def native_publication_enabled(root):
    return native_publication_catalog_for(root) is not None


def _authoring_components(*, codex_validation, execute_models=False, display_enabled=False,
                         pptx_role_enabled=False, pdf_enabled=False, pdf_python_executable=None,
                         pdf_visual_selection=None, office_selection=None,
                         office_renderer=None, office_export_authorizer=None,
                         office_authorization_method=None):
    """All format routes use the same explicit provider choice, never fallback."""
    if any(type(value) is not bool for value in (
            codex_validation, execute_models, display_enabled, pptx_role_enabled, pdf_enabled)):
        raise TypeError("Provider and execution choices must be boolean")
    if display_enabled and pptx_role_enabled:
        raise ValueError("Only one authoring policy may own new PPT uploads.")
    if pdf_enabled and (not pptx_role_enabled or not isinstance(pdf_python_executable, (str, Path))
            or not str(pdf_python_executable).strip()):
        raise ValueError("PDF authoring needs explicit role routing and a configured parser.")
    if not pdf_enabled and pdf_python_executable is not None:
        raise ValueError("A PDF parser cannot activate an otherwise disabled route.")
    if pdf_visual_selection is not None:
        from code_assist_v1.tools.pdf_visual_publication_runtime import PdfVisualRuntimeSelection
        if (type(pdf_visual_selection) is not PdfVisualRuntimeSelection or not pdf_enabled
                or pdf_visual_selection.python_executable != str(pdf_python_executable)
                or pdf_visual_selection.provider != ("codex" if codex_validation else "hynix_api")):
            raise ValueError("Visual PDF selection does not match this worker deployment.")
    office_options = (office_selection, office_renderer,
        office_export_authorizer, office_authorization_method)
    if any(value is not None for value in office_options):
        if (office_selection is None or office_selection != pdf_visual_selection
                or not callable(getattr(office_renderer, "render_for_actor", None))
                or not callable(office_export_authorizer)
                or type(office_authorization_method) is not str
                or not office_authorization_method.strip()):
            raise ValueError("Office route requires the exact visual model and approved export renderer.")
    from code_assist_v1.codex_response import CodexResponsePolicy
    from code_assist_v1.llm_response import ChatResponsePolicy
    from code_assist_v1.internal_llm import run_internal_text
    from code_assist_v1.knowledge_core.services.native_authoring_workflow import NativeUploadPolicy
    from code_assist_v1.knowledge_core.services.wiki_preservation import WikiPreservationUploadPolicy
    from code_assist_v1.knowledge_core.wiki_authoring import AuthoringModelPolicy

    model = (AuthoringModelPolicy(provider_order=("codex",), model_ids=("codex-local",), max_attempts=1)
             if codex_validation else AuthoringModelPolicy())

    def runner_for(text_bytes):
        codex_response = CodexResponsePolicy(prompt_bytes=65_536, stdout_bytes=2_097_152,
            stderr_bytes=262_144, line_bytes=262_144, event_count=4_096, text_bytes=text_bytes)
        api_response = ChatResponsePolicy(envelope_bytes=1_048_576, text_bytes=text_bytes)

        def runner(task, payload, **kwargs):
            if not execute_models:
                raise RuntimeError("Authoring execution is reserved for the durable worker.")
            providers = kwargs.get("provider_order")
            if (type(providers) not in (list, tuple)
                    or tuple(providers) not in (("codex",), ("hynix_api",))):
                raise ValueError("Authoring requires a single frozen provider.")
            # A retained request owns its provider, but cannot authorize a lane
            # disabled by this deployment. Drain mismatches before cutover;
            # never switch an old job to a new provider or ambient fallback.
            if tuple(providers) != model.provider_order:
                raise ValueError("Frozen authoring provider is disabled by this worker deployment.")
            if any(key in kwargs for key in ("response_policy", "api_response_policy", "codex_response_policy")):
                raise ValueError("Authoring response policy is owned by the worker.")
            response_options = ({"codex_response_policy": codex_response}
                if tuple(providers) == ("codex",) else {"response_policy": api_response})
            return run_internal_text(task, payload, **kwargs, **response_options)
        return runner

    components = dict(
        native_author=NativeWikiBatchAuthor(runner=runner_for(65_536)),
        native_policy=NativeUploadPolicy(model=model),
        legacy_author=PreservingWikiAuthor(runner=runner_for(32_768)),
        legacy_policy=WikiPreservationUploadPolicy(model=model, preservation_version=SCOPED_AUTHORING_VERSION),
    )
    if display_enabled:
        components["display_policy"] = NativeUploadPolicy(model=model,
            source_preparation_version=DISPLAY_SOURCE_POLICY)
    if pptx_role_enabled:
        from code_assist_v1.knowledge_core.pptx_role_authoring import PptxRoleAuthor
        from code_assist_v1.knowledge_core.services.pptx_role_authoring_workflow import PptxRoleUploadPolicy
        components["pptx_role_author"] = PptxRoleAuthor(runner=runner_for(65_536))
        components["pptx_role_policy"] = PptxRoleUploadPolicy(model=model)
    if pdf_enabled:
        from code_assist_v1.knowledge_core.pdf_geometry_authoring import PdfAuthor
        from code_assist_v1.knowledge_core.services.pdf_authoring_workflow import PdfUploadPolicy
        components["pdf_author"] = PdfAuthor(runner=runner_for(65_536))
        components["pdf_policy"] = PdfUploadPolicy(model=model)
        components["pdf_python_executable"] = pdf_python_executable
    if pdf_visual_selection is not None:
        from code_assist_v1.knowledge_core.pdf_visual_authoring import PdfVisualAuthor
        components["pdf_visual_author"] = PdfVisualAuthor(runner=visual_runner_for(
            pdf_visual_selection, execute_models=execute_models))
        components["pdf_visual_policy"] = pdf_visual_selection.upload_policy()
        if pdf_visual_selection.forward_continuation:
            components["pdf_continuation_enabled"] = True
    if office_selection is not None:
        from code_assist_v1.knowledge_core.services.office_hybrid_preview import OfficeHybridUploadPolicy
        from code_assist_v1.tools.office_hybrid_publication_runtime import office_visual_author_for
        office_policy = OfficeHybridUploadPolicy(
            native=components["native_policy"],
            visual_model=office_selection.model_policy(),
            render=components["pdf_visual_policy"].render_policy)
        components.update(office_policy=office_policy,
            office_renderer=office_renderer,
            office_export_authorizer=office_export_authorizer,
            office_authorization_method=office_authorization_method,
            office_native_author=NativeWikiBatchAuthor(runner=runner_for(65_536)),
            office_visual_author=office_visual_author_for(
                office_selection, policy=office_policy,
                execute_models=execute_models))
    return components


def native_mode_for_factory(factory, root):
    """A receipt disappearing between factory and service setup is not fallback."""
    catalog = native_publication_catalog_for(root)
    selected = factory.migration_catalog.migrations
    known_native = selected in (NATIVE_PUBLICATION_MIGRATION_CATALOG.migrations,
        NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG.migrations,
        *(value.migrations for value in _ROLE_CATALOGS))
    if (catalog is None and known_native) or (catalog is not None and selected != catalog.migrations):
        raise ValueError("Native publication activation changed during startup.")
    return catalog is not None


def _verify_pending_office_policy(factory, *, codex_validation, office_policy):
    """Reject a v58 restart that cannot replay its exact active Office source."""
    from code_assist_v1.knowledge_core.db import transaction, TransactionMode, db_now_ms
    from code_assist_v1.knowledge_core.migrations.office_hybrid_publication_body_catalog import (
        OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG as V58_CATALOG,
    )
    from code_assist_v1.knowledge_core.services.office_hybrid_preview import OfficeHybridUploadPolicy
    from code_assist_v1.knowledge_core.wiki_authoring import _hash

    active = factory.migration_catalog in (PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG,
        PDF_V10_CHART_TABLES_MIGRATION_CATALOG, PDF_CHART_LABELS_MIGRATION_CATALOG,
        PDF_NUMERIC_APPEND_MIGRATION_CATALOG,
        PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG, PDF_CONTEXT_RESUME_MIGRATION_CATALOG,
        PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG) or factory.migration_catalog.migrations in (
        PDF_LAYOUT_RETENTION_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG.migrations,
        PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG.migrations,
        PDF_FIGURE_ROI_MIGRATION_CATALOG.migrations,
        PDF_CURVE_DOMAIN_MIGRATION_CATALOG.migrations,
        PDF_NONPLOT_WRITING_MIGRATION_CATALOG.migrations,
        PDF_NONTHINKING_WRITING_MIGRATION_CATALOG.migrations,
        PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG.migrations,
        PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG.migrations,
        PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG.migrations,
        PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG.migrations,
        PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG.migrations,
        PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG.migrations,
        PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.migrations,
        PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.migrations,
        PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG.migrations,
        PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG.migrations,
        PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG.migrations,
        V58_CATALOG.migrations,
        OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG.migrations,
        COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG.migrations,
        PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG.migrations,
        PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG.migrations, PDF_SECTION_WRITING_MIGRATION_CATALOG.migrations, PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG.migrations, PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG.migrations, PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG.migrations, PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG.migrations, PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG.migrations, PDF_BATCH_MARKDOWN_MIGRATION_CATALOG.migrations, PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG.migrations)
    if not active:
        if office_policy is not None:
            raise ValueError("An Office policy cannot activate an older catalog.")
        return
    expected_provider = ("codex",) if codex_validation else ("hynix_api",)
    if (type(office_policy) is not OfficeHybridUploadPolicy
            or office_policy.native.model.provider_order != expected_provider
            or office_policy.visual_model.provider_order != expected_provider):
        raise ValueError("The v58 Office route requires one explicit frozen provider policy.")
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            now = db_now_ms(connection)
            rows = connection.execute("""SELECT q.policy_json,q.policy_sha256
                FROM office_hybrid_upload_request q
                JOIN publication_preview p ON p.id=q.preview_id
                JOIN durable_job parent ON parent.id=q.parent_job_id
                LEFT JOIN office_hybrid_wiki_run r ON r.preview_id=p.id
                LEFT JOIN durable_job writer ON writer.id=r.job_id
                LEFT JOIN durable_job dispatch ON dispatch.effect_key=
                    'office-hybrid-wiki-dispatch:' || p.id
                WHERE p.status IN ('queued','extracting','ready')
                  AND p.expires_at>?
                  AND parent.state NOT IN ('failed','dead','cancelled')
                  AND parent.cancel_requested=0
                  AND (writer.state IS NULL OR writer.state NOT IN ('failed','dead','cancelled'))
                  AND (dispatch.state IS NULL OR dispatch.state NOT IN ('failed','dead','cancelled'))""",
                (now,)).fetchall()
            for row in rows:
                encoded = row["policy_json"]
                if (type(encoded) is not str
                        or _hash(encoded) != row["policy_sha256"]
                        or OfficeHybridUploadPolicy.restore(encoded) != office_policy):
                    raise ValueError(
                        "An active Office source has a different frozen authoring or render policy.")
    finally:
        connection.close()


def _verify_pending_authoring_providers(factory, *, codex_validation, pdf_visual_policy=None,
        office_policy=None):
    """Read-only readiness check, one retained policy at a time, before claiming.

Completed/cancelled/expired work needs no new author call and must not prevent
changing provider. Pending work retains its own settings; startup never changes,
cancels or reauthors it to make the selected deployment appear compatible.
Unpublished completed PDFs still need source rebind, so their retained parser
policy must match even when no further model call is required.
"""
    if type(codex_validation) is not bool:
        raise TypeError("Provider choice must be boolean")
    _verify_pending_office_policy(factory, codex_validation=codex_validation,
        office_policy=office_policy)
    from code_assist_v1.knowledge_core.db import transaction, TransactionMode, db_now_ms
    from code_assist_v1.knowledge_core.services.native_authoring_workflow import NativeUploadPolicy
    from code_assist_v1.knowledge_core.services.wiki_preservation import WikiPreservationUploadPolicy
    from code_assist_v1.knowledge_core.wiki_authoring import _hash

    expected = ("codex",) if codex_validation else ("hynix_api",)
    native_sql = """SELECT q.policy_sha256,b.policy_json FROM native_workflow_request q
        JOIN publication_preview p ON p.id=q.preview_id
        JOIN durable_job j ON j.id=q.parent_job_id
        LEFT JOIN native_workflow_policy_body b ON b.preview_id=q.preview_id
        LEFT JOIN native_authoring_run r ON r.preview_id=q.preview_id
        LEFT JOIN native_authoring_state s ON s.run_id=r.id
        WHERE p.status IN ('queued','extracting','ready') AND p.expires_at>?
          AND j.state NOT IN ('failed','cancelled','dead') AND j.cancel_requested=0
          AND (s.status IS NULL OR s.status='pending')
          AND NOT EXISTS(SELECT 1 FROM native_workflow_stop x WHERE x.preview_id=q.preview_id)"""
    legacy_sql = """SELECT q.policy_sha256,q.policy_json FROM publication_preservation_request q
        JOIN publication_preview p ON p.id=q.preview_id
        JOIN durable_job j ON j.id=q.parent_job_id
        LEFT JOIN publication_preservation_source_result x ON x.preview_id=q.preview_id
        LEFT JOIN publication_preservation_run r ON r.preview_id=q.preview_id
        WHERE p.status IN ('queued','extracting','ready') AND p.expires_at>?
          AND j.state NOT IN ('failed','cancelled','dead') AND j.cancel_requested=0
          AND (x.outcome IS NULL OR x.outcome='planned')
          AND (r.status IS NULL OR r.status='pending')"""
    queries = [(native_sql, NativeUploadPolicy), (legacy_sql, WikiPreservationUploadPolicy)]
    if factory.migration_catalog.migrations in tuple(value.migrations for value in _ROLE_CATALOGS):
        from code_assist_v1.knowledge_core.services.pptx_role_authoring_workflow import PptxRoleUploadPolicy
        role_sql = """SELECT q.policy_sha256,b.policy_json FROM pptx_role_workflow_request q
            JOIN publication_preview p ON p.id=q.preview_id
            JOIN durable_job j ON j.id=q.parent_job_id
            LEFT JOIN pptx_role_workflow_policy_body b ON b.preview_id=q.preview_id
            LEFT JOIN pptx_role_authoring_run r ON r.preview_id=q.preview_id
            LEFT JOIN pptx_role_authoring_state s ON s.run_id=r.id
            WHERE p.status IN ('queued','extracting','ready') AND p.expires_at>?
              AND j.state NOT IN ('failed','cancelled','dead') AND j.cancel_requested=0
              AND (s.status IS NULL OR s.status='pending')
              AND NOT EXISTS(SELECT 1 FROM pptx_role_workflow_stop x WHERE x.preview_id=q.preview_id)"""
        queries.append((role_sql, PptxRoleUploadPolicy))
    pdf_policy_type = None
    if factory.migration_catalog in _PDF_CATALOGS:
        from code_assist_v1.knowledge_core.services.pdf_authoring_workflow import PdfUploadPolicy
        pdf_policy_type = PdfUploadPolicy
        pdf_sql = """SELECT q.policy_sha256,b.policy_json,
            CASE WHEN s.status='ready' THEN 0 ELSE 1 END AS needs_model FROM pdf_workflow_request q
            JOIN publication_preview p ON p.id=q.preview_id
            JOIN durable_job j ON j.id=q.parent_job_id
            LEFT JOIN pdf_workflow_policy_body b ON b.preview_id=q.preview_id
            LEFT JOIN pdf_authoring_run r ON r.preview_id=q.preview_id
            LEFT JOIN pdf_authoring_state s ON s.run_id=r.id
            WHERE p.status IN ('queued','extracting','ready') AND min(p.expires_at,q.deadline_at)>?
              AND j.state NOT IN ('failed','cancelled','dead') AND j.cancel_requested=0
              AND (s.status IS NULL OR s.status IN ('pending','ready'))
              AND NOT EXISTS(SELECT 1 FROM pdf_workflow_stop x WHERE x.preview_id=q.preview_id)"""
        queries.append((pdf_sql, PdfUploadPolicy))
    visual_policy_type = None
    if factory.migration_catalog in _VISUAL_CATALOGS:
        from code_assist_v1.knowledge_core.services.pdf_visual_authoring_workflow import PdfVisualUploadPolicy
        visual_policy_type = PdfVisualUploadPolicy
        if type(pdf_visual_policy) is not PdfVisualUploadPolicy:
            raise ValueError("Visual PDF readiness requires the activated policy.")
        visual_sql = """SELECT q.policy_sha256,b.policy_json,
            CASE WHEN s.status='ready' THEN 0 ELSE 1 END AS needs_model FROM pdf_visual_workflow_request q
            JOIN publication_preview p ON p.id=q.preview_id
            JOIN durable_job j ON j.id=q.parent_job_id
            LEFT JOIN pdf_visual_workflow_policy_body b ON b.preview_id=q.preview_id
            LEFT JOIN pdf_visual_authoring_run r ON r.preview_id=q.preview_id
            LEFT JOIN pdf_visual_authoring_state s ON s.run_id=r.id
            WHERE p.status IN ('queued','extracting','ready') AND min(p.expires_at,q.deadline_at)>?
              AND j.state NOT IN ('failed','cancelled','dead') AND j.cancel_requested=0
              AND (s.status IS NULL OR s.status IN ('pending','ready'))
              AND NOT EXISTS(SELECT 1 FROM pdf_visual_workflow_stop x WHERE x.preview_id=q.preview_id)"""
        queries.append((visual_sql, PdfVisualUploadPolicy))
    connection = factory.open(read_only=True)
    try:
        with transaction(connection, TransactionMode.DEFERRED):
            now = db_now_ms(connection)
            for query, policy_type in queries:
                for row in connection.execute(query, (now,)):
                    encoded = row['policy_json']
                    if type(encoded) is not str or _hash(encoded) != row['policy_sha256']:
                        raise ValueError("A pending authoring provider policy is unavailable or changed.")
                    policy = policy_type.restore(encoded)
                    if policy_type is pdf_policy_type:
                        configured = pdf_policy_type()
                        if (policy.geometry_limits != configured.geometry_limits
                                or policy.process_policy != configured.process_policy):
                            raise ValueError("A retained PDF parser policy differs from this deployment.")
                        if not row['needs_model']:
                            continue
                    if policy_type is visual_policy_type:
                        if any(getattr(policy, name) != getattr(pdf_visual_policy, name) for name in
                                ("geometry_limits", "process_policy", "render_policy", "retained_asset_policy")):
                            raise ValueError("A retained visual PDF source policy differs from this deployment.")
                        if not row['needs_model']:
                            continue
                        if (policy.model.model_ids != pdf_visual_policy.model.model_ids
                                or policy.model.wire_models != pdf_visual_policy.model.wire_models
                                or policy.model.max_attempts != 1
                                or policy.model.request_budget_bytes > pdf_visual_policy.model.request_budget_bytes):
                            raise ValueError("A pending visual PDF model differs from this deployment.")
                    if policy.model.provider_order != expected:
                        raise ValueError("A pending authoring provider differs from this deployment; drain it before cutover.")
    finally:
        connection.close()


def _local_ordinary_office_renderer(factory, scope_resolver, *, root, visual_selection):
    """Keep COM default; explicit LibreOffice is confined to verified A10541 V97.

    Neither environment flags nor a renderer grant export rights. Both routes
    receive the same current canonical ordinary-Office export authority.
    """
    from code_assist_v1.tools.clean_10521_runtime import is_clean_root
    from code_assist_v1.knowledge_core.office_visual_windows import WindowsOfficePdfRenderer
    from code_assist_v1.knowledge_core.services.office_export_authority import (
        CanonicalOrdinaryOfficeExportAuthority,
    )
    executable = os.environ.get("CODE_ASSIST_10541_LIBREOFFICE_VALIDATION_EXE")
    executable_sha = os.environ.get("CODE_ASSIST_10541_LIBREOFFICE_VALIDATION_SHA256")
    libreoffice = executable is not None or executable_sha is not None
    if libreoffice:
        from code_assist_v1.tools.recovered_preview_binding import load_binding, _CLEAN_RECOVERY_KIND
        from code_assist_v1.tools.pdf_visual_publication_runtime import PdfVisualRuntimeSelection
        binding = load_binding(root)
        origin = (binding.activation_data_root or root) if binding is not None else None
        if (not executable or not executable_sha or not is_clean_root(root)
                or type(visual_selection) is not PdfVisualRuntimeSelection
                or visual_selection.forward_continuation is not True
                or Path(visual_selection.root).resolve(strict=True) != Path(root).resolve(strict=True)
                or binding is None or binding.kind != _CLEAN_RECOVERY_KIND or binding.port != 10541
                or origin.name != "data-root" or origin.parent.name != "code"
                or origin.parent.parent.name != "wiki-generation-ab-A-10541-20261011-r1"):
            raise ValueError("LibreOffice validation is restricted to the verified A10541 continuation runtime.")
    elif (not is_clean_root(root)
            or os.environ.get("CODE_ASSIST_10521_LOCAL_OFFICE_COM_VALIDATION") != "1"):
        raise ValueError("Local Office COM validation is disabled for v58.")
    authorizer = CanonicalOrdinaryOfficeExportAuthority(factory, scope_resolver)
    if libreoffice:
        from code_assist_v1.knowledge_core.office_visual_libreoffice import LibreOfficePdfRenderer
        renderer = LibreOfficePdfRenderer(authorizer, executable=executable,
            executable_sha256=executable_sha)
    else:
        renderer = WindowsOfficePdfRenderer(authorizer)
    return renderer, authorizer, "canonical-ordinary-office-export-v1"


def composition_for(factory, scope_resolver, stager, *, root, codex_validation, worker=False,
                    additional_handlers=(), preparation_read_binding=None,
                    office_renderer=None, office_export_authorizer=None,
                    office_authorization_method=None,
                    protected_source_opener=None,
                    protected_publication_authorizer=None,
                    policy_protection_classifier=None):
    """Construct the actual API/worker registry only for a verified migrated DB."""
    if protected_source_opener is not None:
        if not worker:
            raise ValueError("The protected source opener is worker-only.")
        if not callable(getattr(protected_source_opener, "open_for_actor", None)):
            raise TypeError("The protected source opener must bind the current actor.")
        if protected_publication_authorizer is None:
            raise ValueError("A protected source opener requires current publication authority.")
    if (protected_publication_authorizer is not None
            and not callable(protected_publication_authorizer)):
        raise TypeError("The protected publication authorizer must be callable.")
    policy_catalog = factory.migration_catalog in (PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG,
        PDF_V10_CHART_TABLES_MIGRATION_CATALOG, PDF_CHART_LABELS_MIGRATION_CATALOG,
        PDF_NUMERIC_APPEND_MIGRATION_CATALOG,
        PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG, PDF_CONTEXT_RESUME_MIGRATION_CATALOG,
        PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG) or factory.migration_catalog in (
        PDF_LAYOUT_RETENTION_MIGRATION_CATALOG,
        PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG,
        PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG,
        PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG, PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG,
        PDF_FIGURE_ROI_MIGRATION_CATALOG, PDF_CURVE_DOMAIN_MIGRATION_CATALOG, PDF_NONPLOT_WRITING_MIGRATION_CATALOG, PDF_NONTHINKING_WRITING_MIGRATION_CATALOG, PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG, PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG, PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG, PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG, PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG, PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG,
        PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG,
        PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG,
        PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG,
        PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG, PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG,
        COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
        PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG,
        PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG, PDF_SECTION_WRITING_MIGRATION_CATALOG, PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG, PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG, PDF_BATCH_MARKDOWN_MIGRATION_CATALOG, PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG)
    if ((policy_catalog and (policy_protection_classifier is None
            or protected_publication_authorizer is None
            or (worker and policy_protection_classifier is not protected_source_opener)))
            or (policy_protection_classifier is not None
                and (not policy_catalog
                    or not callable(getattr(policy_protection_classifier, "classify_for_actor", None))
                    or protected_publication_authorizer is None))):
        raise ValueError("Policy-protected routing requires one v60 provider on web and worker.")
    root = Path(root).resolve(strict=True)
    if not native_mode_for_factory(factory, root):
        raise ValueError("Native publication must be explicitly activated first.")
    catalog = native_publication_catalog_for(root)
    if (catalog is None
            or Path(factory.database_path).resolve(strict=True) != (root / "core/knowledge_core.db").resolve(strict=True)
            or factory.migration_catalog.migrations != catalog.migrations):
        raise ValueError("Native publication factory does not match the activated database.")
    probe = factory.open(read_only=True)
    probe.close()
    visual_selection = (pdf_visual_selection_for(root)
        if catalog in _VISUAL_CATALOGS else None)
    office_active = catalog in (PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG,
        PDF_V10_CHART_TABLES_MIGRATION_CATALOG, PDF_CHART_LABELS_MIGRATION_CATALOG,
        PDF_NUMERIC_APPEND_MIGRATION_CATALOG,
        PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG, PDF_CONTEXT_RESUME_MIGRATION_CATALOG,
        PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG) or catalog in (OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG,
        PDF_LAYOUT_RETENTION_MIGRATION_CATALOG,
        PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG,
        PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG,
        PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG, PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG,
        PDF_FIGURE_ROI_MIGRATION_CATALOG, PDF_CURVE_DOMAIN_MIGRATION_CATALOG, PDF_NONPLOT_WRITING_MIGRATION_CATALOG, PDF_NONTHINKING_WRITING_MIGRATION_CATALOG, PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG, PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG, PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG, PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG, PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG, PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG,
        PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG,
        PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG,
        PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG,
        PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG, PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG,
        OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG,
        COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
        PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG,
        PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG, PDF_SECTION_WRITING_MIGRATION_CATALOG, PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG, PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG, PDF_BATCH_MARKDOWN_MIGRATION_CATALOG, PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG)
    from code_assist_v1.tools.clean_10521_runtime import is_clean_root, _v58_office_policy
    if (office_active and office_renderer is None
            and office_export_authorizer is None
            and office_authorization_method is None):
        # The company DRM lane still supplies its own approved opener. This
        # ordinary route neither installs an engine nor opens protected files.
        office_renderer, office_export_authorizer, office_authorization_method = (
            _local_ordinary_office_renderer(factory, scope_resolver,
                root=root, visual_selection=visual_selection))
    if office_active and (not callable(getattr(office_renderer, "render_for_actor", None))
            or not callable(office_export_authorizer)
            or type(office_authorization_method) is not str
            or not office_authorization_method.strip()):
        raise ValueError("Activated Office v58 requires an approved export renderer and authority.")
    components = _authoring_components(
        codex_validation=codex_validation, execute_models=worker,
        display_enabled=catalog == NATIVE_DISPLAY_PUBLICATION_MIGRATION_CATALOG,
        pptx_role_enabled=catalog in _ROLE_CATALOGS,
        pdf_enabled=catalog in _PDF_CATALOGS,
        pdf_python_executable=pdf_python_for(root) if catalog in _PDF_CATALOGS else None,
        pdf_visual_selection=visual_selection,
        office_selection=visual_selection if office_active else None,
        office_renderer=office_renderer,
        office_export_authorizer=office_export_authorizer,
        office_authorization_method=office_authorization_method)
    if office_active and components.get("office_policy") != _v58_office_policy(visual_selection):
        raise ValueError("Composed Office policy differs from the v58 activation receipt.")
    _verify_pending_authoring_providers(factory, codex_validation=codex_validation,
        pdf_visual_policy=visual_selection.upload_policy() if visual_selection is not None else None,
        office_policy=components.get("office_policy"))
    from code_assist_v1.knowledge_core.services.native_publication_composition import compose_native_publication
    from code_assist_v1.tools.protected_source_runtime import (
        owner_pdf_opener_for_10521_runtime,
        owner_pdf_opener_for_10521_worker,
    )
    from code_assist_v1.knowledge_core.migrations.protected_pdf_visual_binding_catalog import (
        PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG as V51_CATALOG,
    )
    from code_assist_v1.knowledge_core.migrations.temporal_root_rebinding_catalog import (
        TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG as V52_CATALOG,
    )
    composition = compose_native_publication(factory, scope_resolver, stager,
        **components,
        additional_handlers=additional_handlers, preparation_read_binding=preparation_read_binding,
        protected_source_opener=protected_source_opener,
        policy_protection_classifier=policy_protection_classifier,
        protected_publication_authorizer=protected_publication_authorizer,
        require_protected_publication_authorization=(
            protected_publication_authorizer is not None),
        protected_authoring_authorizer=protected_publication_authorizer,
        require_protected_authoring_authorization=(
            protected_publication_authorizer is not None),
        protected_pdf_opener=(
            owner_pdf_opener_for_10521_runtime()
            if catalog in (PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG,
                PDF_V10_CHART_TABLES_MIGRATION_CATALOG, PDF_CHART_LABELS_MIGRATION_CATALOG,
                PDF_NUMERIC_APPEND_MIGRATION_CATALOG,
                PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG, PDF_CONTEXT_RESUME_MIGRATION_CATALOG,
                PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG) or catalog in (V51_CATALOG, V52_CATALOG,
                PDF_LAYOUT_RETENTION_MIGRATION_CATALOG,
                PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG,
                PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG,
                PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG, PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG,
                PDF_FIGURE_ROI_MIGRATION_CATALOG, PDF_CURVE_DOMAIN_MIGRATION_CATALOG, PDF_NONPLOT_WRITING_MIGRATION_CATALOG, PDF_NONTHINKING_WRITING_MIGRATION_CATALOG, PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG, PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG, PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG, PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG, PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG, PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG,
                PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG,
                PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG,
                PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG,
                PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG, PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG,
                OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG,
                OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG,
                COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
                PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG,
                PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG, PDF_SECTION_WRITING_MIGRATION_CATALOG, PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG, PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG, PDF_BATCH_MARKDOWN_MIGRATION_CATALOG, PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG) and is_clean_root(root) else
            owner_pdf_opener_for_10521_worker()
            if worker and is_clean_root(root) else None))
    # An older nonterminal workflow must be drained/retained at offline cutover,
    # not discovered only after the server has announced readiness.
    composition.kernel.verify_registry_coverage()
    return composition


