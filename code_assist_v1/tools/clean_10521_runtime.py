"""Independent 10521 activation, without forged historical 10421 receipts.

Only the exact sibling data root of this isolated code copy is eligible;
an offline-restored code/data layout selects its data root only through a
verified external clean recovery binding. A recovered sibling may retain the
original activation bytes when an externally
pinned clean recovery binding proves its source identity. Missing or changed
activation fails closed; old preview roots keep their predecessor chain.
"""

from hashlib import sha256
from dataclasses import replace
import json
from pathlib import Path

from code_assist_v1.knowledge_core.migrations.semantic_rename_reseed_catalog import (
    SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG as CATALOG,
    V049_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.protected_extraction_provenance_catalog import (
    PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG as V50_CATALOG,
    V050_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.protected_pdf_visual_binding_catalog import (
    PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG as V51_CATALOG,
    V051_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.temporal_root_rebinding_catalog import (
    TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG as V52_CATALOG,
    V052_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.office_hybrid_publication_body_catalog import (
    OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG as V58_CATALOG,
    V058_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.office_personal_writer_part_catalog import (
    OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG as V59_CATALOG,
    V059_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.company_policy_provenance_catalog import (
    COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG as V60_CATALOG,
    V060_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_owned_evidence_prompt_catalog import (
    PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG as V61_CATALOG,
    V061_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_fenced_response_snapshot_catalog import (
    PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG as V62_CATALOG,
    V062_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_section_writing_catalog import (
    PDF_SECTION_WRITING_MIGRATION_CATALOG as V63_CATALOG, V063_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_section_json_output_catalog import (
    PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG as V64_CATALOG,V064_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_section_guard_dispatch_catalog import (
    PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG as V65_CATALOG,V065_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_section_schema_output_catalog import (
    PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG as V66_CATALOG,V066_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_section_markdown_output_catalog import (
    PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG as V67_CATALOG,V067_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_section_markdown_delimiters_catalog import (
    PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG as V68_CATALOG,V068_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_batch_markdown_catalog import (
    PDF_BATCH_MARKDOWN_MIGRATION_CATALOG as V69_CATALOG,V069_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_source_faithful_markdown_catalog import (
    PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG as V70_CATALOG,V070_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_layout_retention_catalog import (
    PDF_LAYOUT_RETENTION_MIGRATION_CATALOG as V71_CATALOG,V071_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.pdf_layout_retention import VERSION as VERSION_V12
from code_assist_v1.knowledge_core.pdf_layout_retention_v2 import VERSION as VERSION_V13
from code_assist_v1.knowledge_core.migrations.pdf_layout_math_format_catalog import (
    PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG as V72_CATALOG,V072_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.pdf_layout_retention_v3 import VERSION as VERSION_V14
from code_assist_v1.knowledge_core.migrations.pdf_layout_subject_binding_catalog import (
    PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG as V73_CATALOG,V073_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.pdf_layout_retention_v4 import VERSION as VERSION_V15
from code_assist_v1.knowledge_core.migrations.pdf_layout_nonblank_groups_catalog import (
    PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG as V74_CATALOG,V074_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.pdf_layout_retention_v5 import VERSION as VERSION_V16
from code_assist_v1.knowledge_core.migrations.pdf_layout_literal_appendix_catalog import (
    PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG as V75_CATALOG,V075_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.pdf_layout_retention_v6 import VERSION as VERSION_V17
from code_assist_v1.knowledge_core.pdf_layout_retention_v7 import VERSION as VERSION_V18
from code_assist_v1.knowledge_core.pdf_layout_retention_v8 import VERSION as VERSION_V19
from code_assist_v1.knowledge_core.pdf_layout_retention_v9 import VERSION as VERSION_V20
from code_assist_v1.knowledge_core.pdf_layout_retention_v10 import VERSION as VERSION_V21
from code_assist_v1.knowledge_core.pdf_layout_retention_v11 import VERSION as VERSION_V22
from code_assist_v1.knowledge_core.pdf_layout_retention_v12 import VERSION as VERSION_V23
from code_assist_v1.knowledge_core.pdf_layout_retention_v13 import VERSION as VERSION_V24
from code_assist_v1.knowledge_core.pdf_layout_retention_v14 import VERSION as VERSION_V25
from code_assist_v1.knowledge_core.pdf_layout_retention_v15 import VERSION as VERSION_V26
from code_assist_v1.knowledge_core.pdf_layout_retention_v16 import VERSION as VERSION_V27
from code_assist_v1.knowledge_core.pdf_layout_retention_v17 import VERSION as VERSION_V28
from code_assist_v1.knowledge_core.pdf_layout_retention_v18 import VERSION as VERSION_V29
from code_assist_v1.knowledge_core.pdf_visual_authoring import VERSION_V30, VERSION_V31, VERSION_V32, VERSION_V33, VERSION_V34, VERSION_V35, VERSION_V36
from code_assist_v1.knowledge_core.migrations.pdf_v10_literal_tables_catalog import (
    PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG as V92_CATALOG,V092_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_v10_chart_tables_catalog import (
    PDF_V10_CHART_TABLES_MIGRATION_CATALOG as V93_CATALOG,V093_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_chart_labels_catalog import (
    PDF_CHART_LABELS_MIGRATION_CATALOG as V94_CATALOG, V094_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_numeric_append_catalog import (
    PDF_NUMERIC_APPEND_MIGRATION_CATALOG as V95_CATALOG, V095_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_atomic_request_partition_catalog import (
    PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG as V96_CATALOG, V096_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_text_json_contract_alignment_catalog import (
    PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG as V87_CATALOG,V087_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_native_frame_contract_catalog import (
    PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG as V88_CATALOG,V088_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_complete_source_packing_catalog import (
    PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG as V89_CATALOG,V089_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_native_categorical_axes_catalog import (
    PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG as V90_CATALOG,V090_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_native_tick_conditions_catalog import (
    PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG as V91_CATALOG,V091_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_visual_contract_alignment_catalog import (
    PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG as V86_CATALOG,V086_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_curve_prompt_alignment_catalog import (
    PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG as V85_CATALOG,V085_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_curve_named_bounds_catalog import (
    PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG as V84_CATALOG,V084_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_bounded_text_partition_catalog import (
    PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG as V83_CATALOG,V083_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_finite_text_writing_catalog import (
    PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG as V82_CATALOG,V082_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_text_output_headroom_catalog import (
    PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG as V81_CATALOG,V081_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_curve_array_guidance_catalog import (
    PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG as V80_CATALOG,V080_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_nonthinking_writing_catalog import (
    PDF_NONTHINKING_WRITING_MIGRATION_CATALOG as V79_CATALOG,V079_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_nonplot_writing_catalog import (
    PDF_NONPLOT_WRITING_MIGRATION_CATALOG as V78_CATALOG,V078_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_curve_domain_catalog import (
    PDF_CURVE_DOMAIN_MIGRATION_CATALOG as V77_CATALOG,V077_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_figure_roi_catalog import (
    PDF_FIGURE_ROI_MIGRATION_CATALOG as V76_CATALOG,V076_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.pdf_visual_authoring import VERSION_V3, VERSION_V4, VERSION_V5, VERSION_V6, VERSION_V7, VERSION_V8, VERSION_V9, VERSION_V10, VERSION_V11, prompt_for_policy
from code_assist_v1.knowledge_core.wiki_authoring import _hash, _json
from code_assist_v1.llm_images import IMAGE_INPUT_CAPABILITY
from code_assist_v1.tools.recovered_preview_binding import (
    _CLEAN_RECOVERY_KIND, _read, load_binding,
)


def _data_root_for_loaded_code() -> Path:
    """Use a restored root only when its external clean binding is verified."""
    code_container = Path(__file__).resolve().parents[2]
    ordinary_root = code_container / "data-root"
    if code_container.name != "code":
        return ordinary_root
    restored_root = code_container.parent / "data-root"
    if not restored_root.is_dir():
        return ordinary_root
    binding = load_binding(restored_root)
    if binding is None:
        return ordinary_root
    if binding.kind != _CLEAN_RECOVERY_KIND:
        raise ValueError("Only a verified clean recovery binding can select the restored root.")
    return binding.data_root


DATA_ROOT = _data_root_for_loaded_code()
ACTIVATION_NAME = "clean-second-brain-10521-v49-enabled.json"
KIND = "second-brain-clean-10521-v49"
V50_ACTIVATION_NAME = "clean-second-brain-10521-v50-enabled.json"
V50_KIND = "second-brain-clean-10521-v50"
V51_ACTIVATION_NAME = "clean-second-brain-10521-v51-enabled.json"
V51_KIND = "second-brain-clean-10521-v51"
V52_ACTIVATION_NAME = "clean-second-brain-10521-v52-enabled.json"
V52_KIND = "second-brain-clean-10521-v52"
V58_ACTIVATION_NAME = "clean-second-brain-10521-v58-enabled.json"
V58_KIND = "second-brain-clean-10521-v58"
V58_RENDERER_CONTRACT = "authorized-office-original-derivative-visual-v1"
V59_ACTIVATION_NAME = "clean-second-brain-10521-v59-enabled.json"
V59_KIND = "second-brain-clean-10521-v59"
V60_ACTIVATION_NAME = "clean-second-brain-10521-v60-enabled.json"
V60_KIND = "second-brain-clean-10521-v60"
V61_ACTIVATION_NAME = "clean-second-brain-10521-v61-enabled.json"
V61_KIND = "second-brain-clean-10521-v61"
V62_ACTIVATION_NAME = "clean-second-brain-10521-v62-enabled.json"
V62_KIND = "second-brain-clean-10521-v62"
V63_ACTIVATION_NAME = "clean-second-brain-10521-v63-enabled.json"
V63_KIND = "second-brain-clean-10521-v63"
V64_ACTIVATION_NAME = "clean-second-brain-10521-v64-enabled.json"
V64_KIND = "second-brain-clean-10521-v64"
V65_ACTIVATION_NAME = "clean-second-brain-10521-v65-enabled.json"
V65_KIND = "second-brain-clean-10521-v65"
V66_ACTIVATION_NAME = "clean-second-brain-10521-v66-enabled.json"
V66_KIND = "second-brain-clean-10521-v66"
V67_ACTIVATION_NAME = "clean-second-brain-10521-v67-enabled.json"
V67_KIND = "second-brain-clean-10521-v67"
V68_ACTIVATION_NAME = "clean-second-brain-10521-v68-enabled.json"
V68_KIND = "second-brain-clean-10521-v68"
V69_ACTIVATION_NAME = "clean-second-brain-10521-v69-enabled.json"
V69_KIND = "second-brain-clean-10521-v69"
V70_ACTIVATION_NAME = "clean-second-brain-10521-v70-enabled.json"
V70_KIND = "second-brain-clean-10521-v70"
V71_ACTIVATION_NAME = "clean-second-brain-10521-v71-enabled.json"
V71_KIND = "second-brain-clean-10521-v71"
V72_ACTIVATION_NAME = "clean-second-brain-10521-v72-enabled.json"
V72_KIND = "second-brain-clean-10521-v72"
V73_ACTIVATION_NAME = "clean-second-brain-10521-v73-enabled.json"
V73_KIND = "second-brain-clean-10521-v73"
V74_ACTIVATION_NAME = "clean-second-brain-10521-v74-enabled.json"
V74_KIND = "second-brain-clean-10521-v74"
V75_ACTIVATION_NAME = "clean-second-brain-10521-v75-enabled.json"
V75_KIND = "second-brain-clean-10521-v75"
V76_ACTIVATION_NAME = "clean-second-brain-10521-v76-enabled.json"
V76_KIND = "second-brain-clean-10521-v76"
V77_ACTIVATION_NAME = "clean-second-brain-10521-v77-enabled.json"
V77_KIND = "second-brain-clean-10521-v77"
V78_ACTIVATION_NAME = "clean-second-brain-10521-v78-enabled.json"
V78_KIND = "second-brain-clean-10521-v78"
V79_ACTIVATION_NAME = "clean-second-brain-10521-v79-enabled.json"
V79_KIND = "second-brain-clean-10521-v79"
V80_ACTIVATION_NAME = "clean-second-brain-10521-v80-enabled.json"
V80_KIND = "second-brain-clean-10521-v80"
V81_ACTIVATION_NAME = "clean-second-brain-10521-v81-enabled.json"
V81_KIND = "second-brain-clean-10521-v81"
V82_ACTIVATION_NAME = "clean-second-brain-10521-v82-enabled.json"
V82_KIND = "second-brain-clean-10521-v82"
V83_ACTIVATION_NAME = "clean-second-brain-10521-v83-enabled.json"
V83_KIND = "second-brain-clean-10521-v83"
V84_ACTIVATION_NAME = "clean-second-brain-10521-v84-enabled.json"
V84_KIND = "second-brain-clean-10521-v84"
V85_ACTIVATION_NAME = "clean-second-brain-10521-v85-enabled.json"
V85_KIND = "second-brain-clean-10521-v85"
V86_ACTIVATION_NAME = "clean-second-brain-10521-v86-enabled.json"
V86_KIND = "second-brain-clean-10521-v86"
V87_ACTIVATION_NAME = "clean-second-brain-10521-v87-enabled.json"
V87_KIND = "second-brain-clean-10521-v87"
V88_ACTIVATION_NAME = "clean-second-brain-10521-v88-enabled.json"
V88_KIND = "second-brain-clean-10521-v88"
V89_ACTIVATION_NAME = "clean-second-brain-10521-v89-enabled.json"
V89_KIND = "second-brain-clean-10521-v89"
V90_ACTIVATION_NAME = "clean-second-brain-10521-v90-enabled.json"
V90_KIND = "second-brain-clean-10521-v90"
V91_ACTIVATION_NAME = "clean-second-brain-10521-v91-enabled.json"
V91_KIND = "second-brain-clean-10521-v91"
V92_ACTIVATION_NAME = "clean-second-brain-10521-v92-enabled.json"
V92_KIND = "second-brain-clean-10521-v92"
V93_ACTIVATION_NAME = "clean-second-brain-10521-v93-enabled.json"
V93_KIND = "second-brain-clean-10521-v93"
V94_ACTIVATION_NAME = "clean-second-brain-10521-v94-enabled.json"
V94_KIND = "second-brain-clean-10521-v94"
V95_ACTIVATION_NAME = "clean-second-brain-10521-v95-enabled.json"
V95_KIND = "second-brain-clean-10521-v95"
V96_ACTIVATION_NAME = "clean-second-brain-10541-v96-enabled.json"
V96_KIND = "second-brain-clean-10541-v96"
from code_assist_v1.tools.pdf_context_resume_activation import (
    ACTIVATION_NAME as V97_ACTIVATION_NAME, context_resume_receipt as _v97_receipt,
)
from code_assist_v1.knowledge_core.migrations.pdf_context_resume_catalog import (
    PDF_CONTEXT_RESUME_MIGRATION_CATALOG as V97_CATALOG,
)
from code_assist_v1.tools.pdf_terminal_result_reuse_activation import (
    ACTIVATION_NAME as V98_ACTIVATION_NAME, terminal_result_reuse_receipt as _v98_receipt,
)
from code_assist_v1.knowledge_core.migrations.pdf_terminal_result_reuse_catalog import (
    PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG as V98_CATALOG,
)
MODEL_SWITCH_ACTIVATION_NAME = "clean-second-brain-10521-model-switch-v1-enabled.json"
ACTIVE_REGISTRY_NAME = "api_config_active.json"
MODEL_SWITCH_KIND = "second-brain-clean-10521-model-switch-v1"


def is_clean_root(root) -> bool:
    candidate = Path(root).absolute()
    return candidate == DATA_ROOT.absolute() and candidate.is_dir() and candidate.resolve(strict=True) == candidate


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate clean activation field.")
        result[key] = value
    return result


def _binding_provider_compatible(binding, provider, root):
    """Historical receipts retain their Codex lane after a forward model switch.

    The final model-switch receipt is verified before startup. Mere presence of
    the marker is not enough to make ``selection_for`` succeed.
    """
    if binding is None:
        return True
    historical_mode = "codex-validation" if provider == "codex" else "openai-compatible"
    return (binding.provider_mode == historical_mode
            or (root / ".runtime" / MODEL_SWITCH_ACTIVATION_NAME).is_file())


def _v49_selection_for(root):
    """Validate one exact v49 receipt and return the existing visual selection."""
    from code_assist_v1.tools.pdf_visual_publication_runtime import (
        PdfVisualRuntimeSelection, _model_identity,
    )

    if not is_clean_root(root):
        return None
    root = DATA_ROOT.resolve(strict=True)
    try:
        binding = load_binding(root)
        if binding is not None and binding.kind != _CLEAN_RECOVERY_KIND:
            raise ValueError("A historical binding cannot activate a clean root.")
        identity_root = binding.source_data_root if binding is not None else root
        raw = _read(root / ".runtime" / ACTIVATION_NAME)
        marker = json.loads(raw, object_pairs_hook=_pairs)
        registry_raw = _read(root / ".runtime" / "api_config.json", 1_048_576)
        registry = json.loads(registry_raw, object_pairs_hook=_pairs)
        if type(marker) is not dict or type(registry) is not dict or type(registry.get("models")) is not dict:
            raise ValueError("Invalid clean activation or model registry shape.")
        model_id = marker.get("model_id")
        identity = _model_identity(model_id, registry["models"].get(model_id))
        python = Path(marker.get("python_executable", ""))
        if not python.is_absolute() or not python.is_file():
            raise ValueError("The clean PDF interpreter is unavailable.")
        python = python.resolve(strict=True)
        selection = PdfVisualRuntimeSelection(
            root=root,
            python_executable=str(python),
            **identity,
            registry_sha256=sha256(registry_raw).hexdigest(),
            activation_sha256=sha256(raw).hexdigest(),
            policy_version=VERSION_V3,
        )
        expected = dict(
            kind=KIND,
            port=10521,
            data_root=str(identity_root),
            schema_version=49,
            schema_sha256=V049_EXPECTED_SCHEMA_SHA256,
            catalog_checksum_sha256=CATALOG.checksum_sha256,
            pdf_writing_policy_version=VERSION_V3,
            pdf_prompt_sha256=_hash(prompt_for_policy(VERSION_V3)),
            pdf_upload_policy_sha256=_hash(selection.upload_policy().encoded()),
            python_executable=str(python),
            registry_sha256=selection.registry_sha256,
            image_input_capability=IMAGE_INPUT_CAPABILITY,
            semantic_review_performed=False,
            **identity,
        )
        if _json(marker) != _json(expected):
            raise ValueError("The clean 10521 receipt differs from its contract.")
        return selection
    except (ValueError, TypeError, OSError, KeyError, UnicodeError) as exc:
        raise ValueError("Invalid clean 10521 activation receipt.") from exc


def _v50_selection_for(root):
    """Select v49 or an explicitly activated v50 successor, never by DB guess."""
    prior = _v49_selection_for(root)
    if prior is None:
        return None
    root = DATA_ROOT.resolve(strict=True)
    path = root / ".runtime" / V50_ACTIVATION_NAME
    if not path.exists():
        return prior
    from code_assist_v1.tools.pdf_visual_publication_runtime import (
        PdfVisualRuntimeSelection, _model_identity,
    )
    try:
        binding = load_binding(root)
        if binding is not None and binding.kind != _CLEAN_RECOVERY_KIND:
            raise ValueError("A historical binding cannot activate a clean root.")
        identity_root = binding.source_data_root if binding is not None else root
        raw = _read(path)
        marker = json.loads(raw, object_pairs_hook=_pairs)
        predecessor = _read(root / ".runtime" / ACTIVATION_NAME)
        registry_raw = _read(root / ".runtime" / "api_config.json", 1_048_576)
        registry = json.loads(registry_raw, object_pairs_hook=_pairs)
        if (type(marker) is not dict or type(registry) is not dict
                or type(registry.get("models")) is not dict):
            raise ValueError("Invalid v50 registry or marker shape.")
        model_id = marker.get("model_id")
        identity = _model_identity(model_id, registry["models"].get(model_id))
        python = Path(marker.get("python_executable", ""))
        if not python.is_absolute() or not python.is_file():
            raise ValueError("The v50 interpreter is unavailable.")
        python = python.resolve(strict=True)
        selection = PdfVisualRuntimeSelection(
            root=root, python_executable=str(python), **identity,
            registry_sha256=sha256(registry_raw).hexdigest(),
            activation_sha256=sha256(raw).hexdigest(), policy_version=VERSION_V3,
        )
        expected = dict(
            kind=V50_KIND, port=10521, data_root=str(identity_root), schema_version=50,
            schema_sha256=V050_EXPECTED_SCHEMA_SHA256,
            catalog_checksum_sha256=V50_CATALOG.checksum_sha256,
            predecessor_activation_sha256=sha256(predecessor).hexdigest(),
            pdf_writing_policy_version=VERSION_V3,
            pdf_prompt_sha256=_hash(prompt_for_policy(VERSION_V3)),
            pdf_upload_policy_sha256=_hash(selection.upload_policy().encoded()),
            python_executable=str(python),
            registry_sha256=selection.registry_sha256,
            image_input_capability=IMAGE_INPUT_CAPABILITY,
            semantic_review_performed=False,
            **identity,
        )
        if _json(marker) != _json(expected):
            raise ValueError("The v50 receipt differs from its contract.")
        if not _binding_provider_compatible(binding, selection.provider, root):
            raise ValueError("The recovered provider differs from its activation.")
        return selection
    except (ValueError, TypeError, OSError, KeyError, UnicodeError) as exc:
        raise ValueError("Invalid clean 10521 v50 activation receipt.") from exc


def _v51_selection_for(root):
    """Select v50 or an explicitly activated v51 successor."""
    prior = _v50_selection_for(root)
    if prior is None:
        return None
    root = DATA_ROOT.resolve(strict=True)
    path = root / ".runtime" / V51_ACTIVATION_NAME
    if not path.exists():
        return prior
    if not (root / ".runtime" / V50_ACTIVATION_NAME).is_file():
        raise ValueError("The v51 activation lacks its v50 predecessor.")
    from code_assist_v1.tools.pdf_visual_publication_runtime import (
        PdfVisualRuntimeSelection, _model_identity,
    )
    try:
        binding = load_binding(root)
        if binding is not None and binding.kind != _CLEAN_RECOVERY_KIND:
            raise ValueError("A historical binding cannot activate a clean root.")
        identity_root = binding.source_data_root if binding is not None else root
        raw = _read(path)
        marker = json.loads(raw, object_pairs_hook=_pairs)
        predecessor = _read(root / ".runtime" / V50_ACTIVATION_NAME)
        registry_raw = _read(root / ".runtime" / "api_config.json", 1_048_576)
        registry = json.loads(registry_raw, object_pairs_hook=_pairs)
        if (type(marker) is not dict or type(registry) is not dict
                or type(registry.get("models")) is not dict):
            raise ValueError("Invalid v51 registry or marker shape.")
        model_id = marker.get("model_id")
        identity = _model_identity(model_id, registry["models"].get(model_id))
        python = Path(marker.get("python_executable", ""))
        if not python.is_absolute() or not python.is_file():
            raise ValueError("The v51 interpreter is unavailable.")
        python = python.resolve(strict=True)
        selection = PdfVisualRuntimeSelection(
            root=root, python_executable=str(python), **identity,
            registry_sha256=sha256(registry_raw).hexdigest(),
            activation_sha256=sha256(raw).hexdigest(), policy_version=VERSION_V3,
        )
        expected = dict(
            kind=V51_KIND, port=10521, data_root=str(identity_root), schema_version=51,
            schema_sha256=V051_EXPECTED_SCHEMA_SHA256,
            catalog_checksum_sha256=V51_CATALOG.checksum_sha256,
            predecessor_activation_sha256=sha256(predecessor).hexdigest(),
            pdf_writing_policy_version=VERSION_V3,
            pdf_prompt_sha256=_hash(prompt_for_policy(VERSION_V3)),
            pdf_upload_policy_sha256=_hash(selection.upload_policy().encoded()),
            python_executable=str(python),
            registry_sha256=selection.registry_sha256,
            image_input_capability=IMAGE_INPUT_CAPABILITY,
            semantic_review_performed=False,
            **identity,
        )
        if _json(marker) != _json(expected):
            raise ValueError("The v51 receipt differs from its contract.")
        if not _binding_provider_compatible(binding, selection.provider, root):
            raise ValueError("The recovered provider differs from its activation.")
        return selection
    except (ValueError, TypeError, OSError, KeyError, UnicodeError) as exc:
        raise ValueError("Invalid clean 10521 v51 activation receipt.") from exc


def _v52_selection_for(root):
    """Select only the newest v49–v52 receipt, excluding future successors."""
    prior = _v51_selection_for(root)
    if prior is None:
        return None
    root = DATA_ROOT.resolve(strict=True)
    path = root / ".runtime" / V52_ACTIVATION_NAME
    if not path.exists():
        return prior
    if not (root / ".runtime" / V51_ACTIVATION_NAME).is_file():
        raise ValueError("The v52 activation lacks its v51 predecessor.")
    from code_assist_v1.tools.pdf_visual_publication_runtime import (
        PdfVisualRuntimeSelection, _model_identity,
    )
    try:
        binding = load_binding(root)
        if binding is not None and binding.kind != _CLEAN_RECOVERY_KIND:
            raise ValueError("A historical binding cannot activate a clean root.")
        identity_root = binding.source_data_root if binding is not None else root
        raw = _read(path)
        marker = json.loads(raw, object_pairs_hook=_pairs)
        predecessor = _read(root / ".runtime" / V51_ACTIVATION_NAME)
        registry_raw = _read(root / ".runtime" / "api_config.json", 1_048_576)
        registry = json.loads(registry_raw, object_pairs_hook=_pairs)
        if (type(marker) is not dict or type(registry) is not dict
                or type(registry.get("models")) is not dict):
            raise ValueError("Invalid v52 registry or marker shape.")
        model_id = marker.get("model_id")
        identity = _model_identity(model_id, registry["models"].get(model_id))
        python = Path(marker.get("python_executable", ""))
        if not python.is_absolute() or not python.is_file():
            raise ValueError("The v52 interpreter is unavailable.")
        python = python.resolve(strict=True)
        selection = PdfVisualRuntimeSelection(
            root=root, python_executable=str(python), **identity,
            registry_sha256=sha256(registry_raw).hexdigest(),
            activation_sha256=sha256(raw).hexdigest(), policy_version=VERSION_V3,
        )
        expected = dict(
            kind=V52_KIND, port=10521, data_root=str(identity_root), schema_version=52,
            schema_sha256=V052_EXPECTED_SCHEMA_SHA256,
            catalog_checksum_sha256=V52_CATALOG.checksum_sha256,
            predecessor_activation_sha256=sha256(predecessor).hexdigest(),
            pdf_writing_policy_version=VERSION_V3,
            pdf_prompt_sha256=_hash(prompt_for_policy(VERSION_V3)),
            pdf_upload_policy_sha256=_hash(selection.upload_policy().encoded()),
            python_executable=str(python), registry_sha256=selection.registry_sha256,
            image_input_capability=IMAGE_INPUT_CAPABILITY,
            semantic_review_performed=False,
            **identity,
        )
        if _json(marker) != _json(expected):
            raise ValueError("The v52 receipt differs from its contract.")
        if not _binding_provider_compatible(binding, selection.provider, root):
            raise ValueError("The recovered provider differs from its activation.")
        return selection
    except (ValueError, TypeError, OSError, KeyError, UnicodeError) as exc:
        raise ValueError("Invalid clean 10521 v52 activation receipt.") from exc


def _v58_office_policy(selection):
    """One pure source of truth for the receipt and composed Office policy."""
    from code_assist_v1.knowledge_core.services.native_authoring_workflow import NativeUploadPolicy
    from code_assist_v1.knowledge_core.services.office_hybrid_preview import OfficeHybridUploadPolicy
    from code_assist_v1.knowledge_core.wiki_authoring import AuthoringModelPolicy
    native_model = (AuthoringModelPolicy(provider_order=("codex",),
        model_ids=("codex-local",), max_attempts=1)
        if selection.provider == "codex" else AuthoringModelPolicy())
    return OfficeHybridUploadPolicy(
        native=NativeUploadPolicy(model=native_model),
        visual_model=selection.model_policy(),
        render=selection.upload_policy().render_policy)


def _v58_receipt(root, prior):
    """Build the exact Office successor identity from a validated v52 parent."""
    if (prior is None or not (root / ".runtime" / V52_ACTIVATION_NAME).is_file()):
        raise ValueError("The v58 activation lacks its v52 predecessor.")
    predecessor = _read(root / ".runtime" / V52_ACTIVATION_NAME)
    previous = json.loads(predecessor, object_pairs_hook=_pairs)
    return {**previous,
        "kind": V58_KIND,
        "schema_version": 58,
        "schema_sha256": V058_EXPECTED_SCHEMA_SHA256,
        "catalog_checksum_sha256": V58_CATALOG.checksum_sha256,
        "predecessor_activation_sha256": sha256(predecessor).hexdigest(),
        "office_upload_policy_sha256": _hash(_v58_office_policy(prior).encoded()),
        "office_renderer_contract": V58_RENDERER_CONTRACT}


def _v59_receipt(root, prior):
    """Preserve the exact Office policy while activating the NULL-safe trigger."""
    if (prior is None or not (root / ".runtime" / V58_ACTIVATION_NAME).is_file()):
        raise ValueError("The v59 activation lacks its v58 predecessor.")
    predecessor = _read(root / ".runtime" / V58_ACTIVATION_NAME)
    previous = json.loads(predecessor, object_pairs_hook=_pairs)
    return {**previous,
        "kind": V59_KIND,
        "schema_version": 59,
        "schema_sha256": V059_EXPECTED_SCHEMA_SHA256,
        "catalog_checksum_sha256": V59_CATALOG.checksum_sha256,
        "predecessor_activation_sha256": sha256(predecessor).hexdigest()}


def _v60_receipt(root, prior):
    """Bind policy-provenance schema to the exact validated v59 receipt."""
    if (prior is None or not (root / ".runtime" / V59_ACTIVATION_NAME).is_file()):
        raise ValueError("The v60 activation lacks its v59 predecessor.")
    predecessor = _read(root / ".runtime" / V59_ACTIVATION_NAME)
    previous = json.loads(predecessor, object_pairs_hook=_pairs)
    return {**previous,
        "kind": V60_KIND,
        "schema_version": 60,
        "schema_sha256": V060_EXPECTED_SCHEMA_SHA256,
        "catalog_checksum_sha256": V60_CATALOG.checksum_sha256,
        "predecessor_activation_sha256": sha256(predecessor).hexdigest()}


def _v61_receipt(root, prior):
    """Bind a distinct v4 PDF prompt without rewriting retained v3 receipts."""
    if (prior is None or prior.policy_version != VERSION_V3
            or not (root / ".runtime" / V60_ACTIVATION_NAME).is_file()):
        raise ValueError("The v61 PDF activation lacks its v60 predecessor.")
    predecessor = _read(root / ".runtime" / V60_ACTIVATION_NAME)
    previous = json.loads(predecessor, object_pairs_hook=_pairs)
    selected = replace(prior, policy_version=VERSION_V4)
    return {**previous,
        "kind": V61_KIND,
        "schema_version": 61,
        "schema_sha256": V061_EXPECTED_SCHEMA_SHA256,
        "catalog_checksum_sha256": V61_CATALOG.checksum_sha256,
        "predecessor_activation_sha256": sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version": VERSION_V4,
        "pdf_prompt_sha256": _hash(prompt_for_policy(VERSION_V4)),
        "pdf_upload_policy_sha256": _hash(selected.upload_policy().encoded())}


def _v62_receipt(root, prior):
    """Bind the fenced-response snapshot guard after the local model switch."""
    if (prior is None or prior.policy_version != VERSION_V4
            or not (root / ".runtime" / V61_ACTIVATION_NAME).is_file()
            or not (root / ".runtime" / MODEL_SWITCH_ACTIVATION_NAME).is_file()):
        raise ValueError("The v62 PDF activation lacks its active v61 predecessor.")
    predecessor = _read(root / ".runtime" / MODEL_SWITCH_ACTIVATION_NAME)
    previous = json.loads(_read(root / ".runtime" / V61_ACTIVATION_NAME), object_pairs_hook=_pairs)
    return {**previous,
        "kind": V62_KIND,
        "schema_version": 62,
        "schema_sha256": V062_EXPECTED_SCHEMA_SHA256,
        "catalog_checksum_sha256": V62_CATALOG.checksum_sha256,
        "predecessor_activation_sha256": sha256(predecessor).hexdigest()}


def _model_switch_receipt(root, prior):
    """Bind a new active API model without editing any v49-v61 receipt."""
    from code_assist_v1.tools.pdf_visual_publication_runtime import _model_identity

    if (prior is None or prior.policy_version != VERSION_V4
            or not (root / ".runtime" / V61_ACTIVATION_NAME).is_file()):
        raise ValueError("The active model switch requires verified v61 history.")
    binding = load_binding(root)
    identity_root = binding.source_data_root if binding is not None else root
    raw = _read(root / ".runtime" / ACTIVE_REGISTRY_NAME, 1_048_576)
    registry = json.loads(raw, object_pairs_hook=_pairs)
    if (type(registry) is not dict or type(registry.get("models")) is not dict
            or type(registry.get("default_model_priority")) is not list
            or len(registry["default_model_priority"]) != 1):
        raise ValueError("The active model registry must name one default model.")
    model_id = registry["default_model_priority"][0]
    identity = _model_identity(model_id, registry["models"].get(model_id))
    if identity["provider"] != "hynix_api":
        raise ValueError("The forward model switch requires an OpenAI-compatible API model.")
    predecessor = _read(root / ".runtime" / V61_ACTIVATION_NAME)
    return dict(
        kind=MODEL_SWITCH_KIND, schema_version=1, port=10521,
        data_root=str(identity_root),
        predecessor_activation_sha256=sha256(predecessor).hexdigest(),
        registry_sha256=sha256(raw).hexdigest(),
        python_executable=prior.python_executable,
        pdf_writing_policy_version=VERSION_V4,
        image_input_capability=IMAGE_INPUT_CAPABILITY,
        semantic_review_performed=False,
        **identity,
    )


def _v63_receipt(root, prior):
    """Only new uploads select section writing; archived prompts stay frozen."""
    from code_assist_v1.knowledge_core.pdf_section_writing import CONTEXT_TOKENS, OUTPUT_TOKENS
    if (prior is None or prior.policy_version != VERSION_V4
            or not (root / ".runtime" / V62_ACTIVATION_NAME).is_file()):
        raise ValueError("The v63 PDF activation lacks its v62 predecessor.")
    predecessor = _read(root / ".runtime" / V62_ACTIVATION_NAME)
    previous = json.loads(predecessor, object_pairs_hook=_pairs)
    selected = replace(prior, policy_version=VERSION_V5)
    return {**previous,
        "kind": V63_KIND, "schema_version": 63,
        "schema_sha256": V063_EXPECTED_SCHEMA_SHA256,
        "catalog_checksum_sha256": V63_CATALOG.checksum_sha256,
        "predecessor_activation_sha256": sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version": VERSION_V5,
        "pdf_prompt_sha256": _hash(prompt_for_policy(VERSION_V5)),
        "pdf_upload_policy_sha256": _hash(selected.upload_policy().encoded()),
        "pdf_context_tokens": CONTEXT_TOKENS, "pdf_output_tokens": OUTPUT_TOKENS,
        "pdf_semantic_review_performed": False}


def _active_model_selection(root, prior):
    path = root / ".runtime" / MODEL_SWITCH_ACTIVATION_NAME
    if not path.exists():
        return prior
    try:
        raw = _read(path)
        marker = json.loads(raw, object_pairs_hook=_pairs)
        expected = _model_switch_receipt(root, prior)
        if _json(marker) != _json(expected):
            raise ValueError("The active model switch differs from its contract.")
        binding = load_binding(root)
        if binding is not None and binding.provider_mode != "openai-compatible":
            raise ValueError("The recovered provider differs from the active model.")
        return replace(
            prior,
            provider=expected["provider"],
            model_id=expected["model_id"],
            wire_model=expected["wire_model"],
            codex_reasoning_effort=None,
            registry_sha256=expected["registry_sha256"],
            activation_sha256=sha256(raw).hexdigest(),
        )
    except (ValueError, TypeError, OSError, KeyError, UnicodeError) as exc:
        raise ValueError("Invalid clean 10521 active model switch.") from exc


def _v64_receipt(root,prior):
    if (prior is None or prior.policy_version != VERSION_V5
            or not (root/".runtime"/V63_ACTIVATION_NAME).is_file()):
        raise ValueError("The v64 activation lacks its v63 predecessor.")
    predecessor = _read(root/".runtime"/V63_ACTIVATION_NAME)
    previous = json.loads(predecessor,object_pairs_hook=_pairs)
    selected = replace(prior,policy_version=VERSION_V6)
    return {**previous,"kind":V64_KIND,"schema_version":64,
        "schema_sha256":V064_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V64_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V6,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V6)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_image_token_reservation":4096,"pdf_response_format":{"type":"json_object"}}


def selection_for(root):
    """Select only an exact validated clean successor marker."""
    prior = _v52_selection_for(root)
    if prior is None:
        return None
    root = DATA_ROOT.resolve(strict=True)
    path = root / ".runtime" / V58_ACTIVATION_NAME
    v59_path = root / ".runtime" / V59_ACTIVATION_NAME
    v60_path = root / ".runtime" / V60_ACTIVATION_NAME
    v61_path = root / ".runtime" / V61_ACTIVATION_NAME
    v62_path = root / ".runtime" / V62_ACTIVATION_NAME
    v63_path = root / ".runtime" / V63_ACTIVATION_NAME
    v64_path = root / ".runtime" / V64_ACTIVATION_NAME
    v65_path = root / ".runtime" / V65_ACTIVATION_NAME
    v66_path = root / ".runtime" / V66_ACTIVATION_NAME
    v67_path = root / ".runtime" / V67_ACTIVATION_NAME
    v68_path = root / ".runtime" / V68_ACTIVATION_NAME
    v69_path = root / ".runtime" / V69_ACTIVATION_NAME
    v70_path = root / ".runtime" / V70_ACTIVATION_NAME
    v71_path = root / ".runtime" / V71_ACTIVATION_NAME
    v72_path = root / ".runtime" / V72_ACTIVATION_NAME
    v73_path = root / ".runtime" / V73_ACTIVATION_NAME
    v74_path = root / ".runtime" / V74_ACTIVATION_NAME
    v75_path = root / ".runtime" / V75_ACTIVATION_NAME
    v76_path = root / ".runtime" / V76_ACTIVATION_NAME
    v77_path = root / ".runtime" / V77_ACTIVATION_NAME
    v78_path = root / ".runtime" / V78_ACTIVATION_NAME
    v79_path = root / ".runtime" / V79_ACTIVATION_NAME
    v80_path = root / ".runtime" / V80_ACTIVATION_NAME
    v81_path = root / ".runtime" / V81_ACTIVATION_NAME
    v82_path = root / ".runtime" / V82_ACTIVATION_NAME
    v83_path = root / ".runtime" / V83_ACTIVATION_NAME
    v84_path = root / ".runtime" / V84_ACTIVATION_NAME
    v85_path = root / ".runtime" / V85_ACTIVATION_NAME
    v86_path = root / ".runtime" / V86_ACTIVATION_NAME
    v87_path = root / ".runtime" / V87_ACTIVATION_NAME
    v88_path = root / ".runtime" / V88_ACTIVATION_NAME
    v89_path = root / ".runtime" / V89_ACTIVATION_NAME
    v90_path = root / ".runtime" / V90_ACTIVATION_NAME
    v91_path = root / ".runtime" / V91_ACTIVATION_NAME
    v92_path = root / ".runtime" / V92_ACTIVATION_NAME
    v93_path = root / ".runtime" / V93_ACTIVATION_NAME
    v94_path = root / ".runtime" / V94_ACTIVATION_NAME
    v95_path = root / ".runtime" / V95_ACTIVATION_NAME
    v96_path = root / ".runtime" / V96_ACTIVATION_NAME
    v97_path = root / ".runtime" / V97_ACTIVATION_NAME
    v98_path = root / ".runtime" / V98_ACTIVATION_NAME
    if v98_path.exists() and not v97_path.exists():
        raise ValueError("The A10541 V98 activation requires its immutable V97 predecessor.")
    if v97_path.exists() and not v96_path.exists():
        raise ValueError("The A10541 V97 activation requires its immutable V96 predecessor.")
    if v96_path.exists() and not v95_path.exists():
        raise ValueError("The A10541 V96 activation requires its immutable V95 predecessor.")
    if v95_path.exists() and not v94_path.exists():
        raise ValueError("The v95 numeric-append activation requires its exact v94 predecessor.")
    if v94_path.exists() and not v93_path.exists():
        raise ValueError("The v94 chart-label activation requires its exact v93 predecessor.")
    if v93_path.exists() and not v92_path.exists():
        raise ValueError("The v93 chart-table activation requires its exact v92 predecessor.")
    if v92_path.exists() and not v91_path.exists():
        raise ValueError("The v92 literal-table activation requires its exact v91 predecessor.")
    if v91_path.exists() and not v90_path.exists():
        raise ValueError("The v91 tick-condition activation requires its exact v90 predecessor.")
    if v90_path.exists() and not v89_path.exists():
        raise ValueError("The v90 categorical native-axis activation requires its exact v89 predecessor.")
    if v89_path.exists() and not v88_path.exists():
        raise ValueError("The v89 complete-source packing activation requires its exact v88 predecessor.")
    if v88_path.exists() and not v87_path.exists():
        raise ValueError("The v88 native-frame contract activation requires its exact v87 predecessor.")
    if v87_path.exists() and not v86_path.exists():
        raise ValueError("The v87 text-JSON contract alignment activation requires its exact v86 predecessor.")
    if v86_path.exists() and not v85_path.exists():
        raise ValueError("The v86 visual-contract alignment activation requires its exact v85 predecessor.")
    if v85_path.exists() and not v84_path.exists():
        raise ValueError("The v85 curve-prompt alignment activation requires its exact v84 predecessor.")
    if v84_path.exists() and not v83_path.exists():
        raise ValueError("The v84 named-curve-bounds activation requires its exact v83 predecessor.")
    if v83_path.exists() and not v82_path.exists():
        raise ValueError("The v83 bounded-text partition activation requires its exact v82 predecessor.")
    if v82_path.exists() and not v81_path.exists():
        raise ValueError("The v82 finite-text writing activation requires its exact v81 predecessor.")
    if v81_path.exists() and not v80_path.exists():
        raise ValueError("The v81 text-output headroom activation requires its exact v80 predecessor.")
    if v80_path.exists() and not v79_path.exists():
        raise ValueError("The v80 curve-array guidance activation requires its exact v79 predecessor.")
    if v79_path.exists() and not v78_path.exists():
        raise ValueError("The v79 nonthinking-writing activation requires its exact v78 predecessor.")
    if v78_path.exists() and not v77_path.exists():
        raise ValueError("The v78 nonplot-writing activation requires its exact v77 predecessor.")
    if v77_path.exists() and not v76_path.exists():
        raise ValueError("The v77 curve-domain activation requires its exact v76 predecessor.")
    if v76_path.exists() and not v75_path.exists():
        raise ValueError("The v76 appearance-ROI activation requires its exact v75 predecessor.")
    if v75_path.exists() and not v74_path.exists():
        raise ValueError("The v75 literal-appendix activation requires its exact v74 predecessor.")
    if v74_path.exists() and not v73_path.exists():
        raise ValueError("The v74 nonblank-group activation requires its exact v73 predecessor.")
    if v73_path.exists() and not v72_path.exists():
        raise ValueError("The v73 caption-subject activation requires its exact v72 predecessor.")
    if v72_path.exists() and not v71_path.exists():
        raise ValueError("The v72 math-format activation requires its exact v71 predecessor.")
    if v71_path.exists() and (not v69_path.exists() or v70_path.exists()):
        raise ValueError("The v71 layout activation requires its exact v69 predecessor, not a mixed v70 activation.")
    if v70_path.exists() and not v69_path.exists():
        raise ValueError("The v70 activation lacks its v69 predecessor.")
    if v69_path.exists() and not v68_path.exists():
        raise ValueError("The v69 activation lacks its v68 predecessor.")
    if v68_path.exists() and not v67_path.exists():
        raise ValueError("The v68 activation lacks its v67 predecessor.")
    if v67_path.exists() and not v66_path.exists():
        raise ValueError("The v67 activation lacks its v66 predecessor.")
    if v66_path.exists() and not v65_path.exists():
        raise ValueError("The v66 activation lacks its v65 predecessor.")
    if v65_path.exists() and not v64_path.exists():
        raise ValueError("The v65 activation lacks its v64 predecessor.")
    if v64_path.exists() and not v63_path.exists():
        raise ValueError("The v64 activation lacks its v63 predecessor.")
    if v63_path.exists() and not v62_path.exists():
        raise ValueError("The v63 activation lacks its v62 predecessor.")
    if not path.exists():
        if v59_path.exists() or v60_path.exists() or v61_path.exists() or v62_path.exists():
            raise ValueError("The later activation lacks its v58 predecessor.")
        return prior
    if not (root / ".runtime" / V52_ACTIVATION_NAME).is_file():
        raise ValueError("The v58 activation lacks its v52 predecessor.")
    try:
        raw = _read(path)
        marker = json.loads(raw, object_pairs_hook=_pairs)
        if _json(marker) != _json(_v58_receipt(root, prior)):
            raise ValueError("The v58 receipt differs from its contract.")
        v58 = replace(prior, activation_sha256=sha256(raw).hexdigest())
        if not v59_path.exists():
            if v60_path.exists() or v61_path.exists() or v62_path.exists():
                raise ValueError("The v60 activation lacks its v59 predecessor.")
            return v58
        v59_raw = _read(v59_path)
        v59_marker = json.loads(v59_raw, object_pairs_hook=_pairs)
        if _json(v59_marker) != _json(_v59_receipt(root, v58)):
            raise ValueError("The v59 receipt differs from its contract.")
        v59 = replace(v58, activation_sha256=sha256(v59_raw).hexdigest())
        if not v60_path.exists():
            if v61_path.exists() or v62_path.exists():
                raise ValueError("The v61 activation lacks its v60 predecessor.")
            return v59
        v60_raw = _read(v60_path)
        v60_marker = json.loads(v60_raw, object_pairs_hook=_pairs)
        if _json(v60_marker) != _json(_v60_receipt(root, v59)):
            raise ValueError("The v60 receipt differs from its contract.")
        v60 = replace(v59, activation_sha256=sha256(v60_raw).hexdigest())
        if not v61_path.exists():
            if v62_path.exists():
                raise ValueError("The v62 activation lacks its v61 predecessor.")
            return v60
        v61_raw = _read(v61_path)
        v61_marker = json.loads(v61_raw, object_pairs_hook=_pairs)
        if _json(v61_marker) != _json(_v61_receipt(root, v60)):
            raise ValueError("The v61 receipt differs from its contract.")
        v61 = replace(v60, policy_version=VERSION_V4,
            activation_sha256=sha256(v61_raw).hexdigest())
        selected = _active_model_selection(root, v61)
        if not v62_path.exists():
            return selected
        v62_raw = _read(v62_path)
        v62_marker = json.loads(v62_raw, object_pairs_hook=_pairs)
        if _json(v62_marker) != _json(_v62_receipt(root, selected)):
            raise ValueError("The v62 receipt differs from its contract.")
        v62 = replace(selected, activation_sha256=sha256(v62_raw).hexdigest())
        if not v63_path.exists():
            return v62
        v63_raw = _read(v63_path)
        v63_marker = json.loads(v63_raw, object_pairs_hook=_pairs)
        if _json(v63_marker) != _json(_v63_receipt(root, v62)):
            raise ValueError("The v63 receipt differs from its contract.")
        v63 = replace(v62,policy_version=VERSION_V5,activation_sha256=sha256(v63_raw).hexdigest())
        if not v64_path.exists():
            return v63
        v64_raw = _read(v64_path)
        v64_marker = json.loads(v64_raw,object_pairs_hook=_pairs)
        if _json(v64_marker) != _json(_v64_receipt(root,v63)):
            raise ValueError("The v64 receipt differs from its contract.")
        v64 = replace(v63,policy_version=VERSION_V6,activation_sha256=sha256(v64_raw).hexdigest())
        if not v65_path.exists():return v64
        v65_raw=_read(v65_path)
        if _json(json.loads(v65_raw,object_pairs_hook=_pairs)) != _json(_v65_receipt(root,v64)):
            raise ValueError("The v65 receipt differs from its contract.")
        v65 = replace(v64,activation_sha256=sha256(v65_raw).hexdigest())
        if not v66_path.exists():return v65
        v66_raw=_read(v66_path)
        if _json(json.loads(v66_raw,object_pairs_hook=_pairs)) != _json(_v66_receipt(root,v65)):
            raise ValueError("The v66 receipt differs from its contract.")
        v66=replace(v65,policy_version=VERSION_V7,activation_sha256=sha256(v66_raw).hexdigest())
        if not v67_path.exists():return v66
        v67_raw=_read(v67_path)
        if _json(json.loads(v67_raw,object_pairs_hook=_pairs)) != _json(_v67_receipt(root,v66)):
            raise ValueError("The v67 receipt differs from its contract.")
        v67=replace(v66,policy_version=VERSION_V8,activation_sha256=sha256(v67_raw).hexdigest())
        if not v68_path.exists():return v67
        v68_raw=_read(v68_path)
        if _json(json.loads(v68_raw,object_pairs_hook=_pairs)) != _json(_v68_receipt(root,v67)):
            raise ValueError("The v68 receipt differs from its contract.")
        v68=replace(v67,policy_version=VERSION_V9,activation_sha256=sha256(v68_raw).hexdigest())
        if not v69_path.exists():return v68
        v69_raw=_read(v69_path)
        if _json(json.loads(v69_raw,object_pairs_hook=_pairs)) != _json(_v69_receipt(root,v68)):
            raise ValueError("The v69 receipt differs from its contract.")
        v69=replace(v68,policy_version=VERSION_V10,activation_sha256=sha256(v69_raw).hexdigest())
        if v71_path.exists():
            v71_raw=_read(v71_path)
            if _json(json.loads(v71_raw,object_pairs_hook=_pairs)) != _json(_v71_receipt(root,v69)):
                raise ValueError("The v71 receipt differs from its contract.")
            v71=replace(v69,policy_version=VERSION_V12,activation_sha256=sha256(v71_raw).hexdigest())
            if not v72_path.exists():return v71
            v72_raw=_read(v72_path)
            if _json(json.loads(v72_raw,object_pairs_hook=_pairs)) != _json(_v72_receipt(root,v71)):
                raise ValueError("The v72 receipt differs from its contract.")
            v72=replace(v71,policy_version=VERSION_V13,activation_sha256=sha256(v72_raw).hexdigest())
            if not v73_path.exists():return v72
            v73_raw=_read(v73_path)
            if _json(json.loads(v73_raw,object_pairs_hook=_pairs)) != _json(_v73_receipt(root,v72)):
                raise ValueError("The v73 receipt differs from its contract.")
            v73=replace(v72,policy_version=VERSION_V14,activation_sha256=sha256(v73_raw).hexdigest())
            if not v74_path.exists():return v73
            v74_raw=_read(v74_path)
            if _json(json.loads(v74_raw,object_pairs_hook=_pairs)) != _json(_v74_receipt(root,v73)):
                raise ValueError("The v74 receipt differs from its contract.")
            v74=replace(v73,policy_version=VERSION_V15,activation_sha256=sha256(v74_raw).hexdigest())
            if not v75_path.exists():return v74
            v75_raw=_read(v75_path)
            if _json(json.loads(v75_raw,object_pairs_hook=_pairs)) != _json(_v75_receipt(root,v74)):
                raise ValueError("The v75 receipt differs from its contract.")
            v75=replace(v74,policy_version=VERSION_V16,activation_sha256=sha256(v75_raw).hexdigest())
            if not v76_path.exists():return v75
            v76_raw=_read(v76_path)
            if _json(json.loads(v76_raw,object_pairs_hook=_pairs)) != _json(_v76_receipt(root,v75)):
                raise ValueError("The v76 receipt differs from its contract.")
            v76=replace(v75,policy_version=VERSION_V17,activation_sha256=sha256(v76_raw).hexdigest())
            if not v77_path.exists():return v76
            v77_raw=_read(v77_path)
            if _json(json.loads(v77_raw,object_pairs_hook=_pairs)) != _json(_v77_receipt(root,v76)):
                raise ValueError("The v77 receipt differs from its contract.")
            v77=replace(v76,policy_version=VERSION_V18,activation_sha256=sha256(v77_raw).hexdigest())
            if not v78_path.exists():return v77
            v78_raw=_read(v78_path)
            if _json(json.loads(v78_raw,object_pairs_hook=_pairs)) != _json(_v78_receipt(root,v77)):
                raise ValueError("The v78 receipt differs from its contract.")
            v78=replace(v77,policy_version=VERSION_V19,activation_sha256=sha256(v78_raw).hexdigest())
            if not v79_path.exists():return v78
            v79_raw=_read(v79_path)
            if _json(json.loads(v79_raw,object_pairs_hook=_pairs)) != _json(_v79_receipt(root,v78)):
                raise ValueError("The v79 receipt differs from its contract.")
            v79=replace(v78,policy_version=VERSION_V20,activation_sha256=sha256(v79_raw).hexdigest())
            if not v80_path.exists():return v79
            v80_raw=_read(v80_path)
            if _json(json.loads(v80_raw,object_pairs_hook=_pairs)) != _json(_v80_receipt(root,v79)):
                raise ValueError("The v80 receipt differs from its contract.")
            v80=replace(v79,policy_version=VERSION_V21,activation_sha256=sha256(v80_raw).hexdigest())
            if not v81_path.exists():return v80
            v81_raw=_read(v81_path)
            if _json(json.loads(v81_raw,object_pairs_hook=_pairs)) != _json(_v81_receipt(root,v80)):
                raise ValueError("The v81 receipt differs from its contract.")
            v81=replace(v80,policy_version=VERSION_V22,activation_sha256=sha256(v81_raw).hexdigest())
            if not v82_path.exists():return v81
            v82_raw=_read(v82_path)
            if _json(json.loads(v82_raw,object_pairs_hook=_pairs)) != _json(_v82_receipt(root,v81)):
                raise ValueError("The v82 receipt differs from its contract.")
            v82=replace(v81,policy_version=VERSION_V23,activation_sha256=sha256(v82_raw).hexdigest())
            if not v83_path.exists():return v82
            v83_raw=_read(v83_path)
            if _json(json.loads(v83_raw,object_pairs_hook=_pairs)) != _json(_v83_receipt(root,v82)):
                raise ValueError("The v83 receipt differs from its contract.")
            v83=replace(v82,policy_version=VERSION_V24,activation_sha256=sha256(v83_raw).hexdigest())
            if not v84_path.exists():return v83
            v84_raw=_read(v84_path)
            if _json(json.loads(v84_raw,object_pairs_hook=_pairs)) != _json(_v84_receipt(root,v83)):
                raise ValueError("The v84 receipt differs from its contract.")
            v84=replace(v83,policy_version=VERSION_V25,activation_sha256=sha256(v84_raw).hexdigest())
            if not v85_path.exists():return v84
            v85_raw=_read(v85_path)
            if _json(json.loads(v85_raw,object_pairs_hook=_pairs)) != _json(_v85_receipt(root,v84)):
                raise ValueError("The v85 receipt differs from its contract.")
            v85=replace(v84,policy_version=VERSION_V26,activation_sha256=sha256(v85_raw).hexdigest())
            if not v86_path.exists():return v85
            v86_raw=_read(v86_path)
            if _json(json.loads(v86_raw,object_pairs_hook=_pairs)) != _json(_v86_receipt(root,v85)):
                raise ValueError("The v86 receipt differs from its contract.")
            v86=replace(v85,policy_version=VERSION_V27,activation_sha256=sha256(v86_raw).hexdigest())
            if not v87_path.exists():return v86
            v87_raw=_read(v87_path)
            if _json(json.loads(v87_raw,object_pairs_hook=_pairs)) != _json(_v87_receipt(root,v86)):
                raise ValueError("The v87 receipt differs from its contract.")
            v87=replace(v86,policy_version=VERSION_V28,activation_sha256=sha256(v87_raw).hexdigest())
            if not v88_path.exists():return v87
            v88_raw=_read(v88_path)
            if _json(json.loads(v88_raw,object_pairs_hook=_pairs)) != _json(_v88_receipt(root,v87)):
                raise ValueError("The v88 receipt differs from its contract.")
            v88=replace(v87,policy_version=VERSION_V29,activation_sha256=sha256(v88_raw).hexdigest())
            if not v89_path.exists():return v88
            v89_raw=_read(v89_path)
            if _json(json.loads(v89_raw,object_pairs_hook=_pairs)) != _json(_v89_receipt(root,v88)):
                raise ValueError("The v89 receipt differs from its contract.")
            v89=replace(v88,policy_version=VERSION_V30,activation_sha256=sha256(v89_raw).hexdigest())
            if not v90_path.exists():return v89
            v90_raw=_read(v90_path)
            if _json(json.loads(v90_raw,object_pairs_hook=_pairs)) != _json(_v90_receipt(root,v89)):
                raise ValueError("The v90 receipt differs from its contract.")
            v90=replace(v89,policy_version=VERSION_V31,activation_sha256=sha256(v90_raw).hexdigest())
            if not v91_path.exists():return v90
            v91_raw=_read(v91_path)
            if _json(json.loads(v91_raw,object_pairs_hook=_pairs)) != _json(_v91_receipt(root,v90)):
                raise ValueError("The v91 receipt differs from its contract.")
            v91=replace(v90,policy_version=VERSION_V32,activation_sha256=sha256(v91_raw).hexdigest())
            if not v92_path.exists():return v91
            v92_raw=_read(v92_path)
            if _json(json.loads(v92_raw,object_pairs_hook=_pairs)) != _json(_v92_receipt(root,v91)):
                raise ValueError("The v92 receipt differs from its contract.")
            v92=replace(v91,policy_version=VERSION_V33,activation_sha256=sha256(v92_raw).hexdigest())
            if not v93_path.exists():return v92
            v93_raw=_read(v93_path)
            if _json(json.loads(v93_raw,object_pairs_hook=_pairs)) != _json(_v93_receipt(root,v92)):
                raise ValueError("The v93 receipt differs from its contract.")
            v93 = replace(v92,policy_version=VERSION_V34,activation_sha256=sha256(v93_raw).hexdigest())
            if not v94_path.exists():return v93
            v94_raw = _read(v94_path)
            if _json(json.loads(v94_raw,object_pairs_hook=_pairs)) != _json(_v94_receipt(root,v93)):
                raise ValueError("The v94 receipt differs from its contract.")
            v94 = replace(v93,policy_version=VERSION_V35,activation_sha256=sha256(v94_raw).hexdigest())
            if not v95_path.exists():return v94
            v95_raw = _read(v95_path)
            if _json(json.loads(v95_raw,object_pairs_hook=_pairs)) != _json(_v95_receipt(root,v94)):
                raise ValueError("The v95 receipt differs from its contract.")
            v95 = replace(v94,policy_version=VERSION_V36,activation_sha256=sha256(v95_raw).hexdigest())
            if not v96_path.exists():return v95
            binding = load_binding(root)
            if binding is None or binding.port != 10541 or binding.current_marker_sha256 is None:
                raise ValueError("V96 requires its separately pinned A10541 operator binding.")
            v96_raw = _read(v96_path)
            if (_json(json.loads(v96_raw,object_pairs_hook=_pairs)) != _json(_v96_receipt(root,v95))
                    or sha256(v96_raw).hexdigest() != (binding.partition_marker_sha256 or binding.current_marker_sha256)):
                raise ValueError("The A10541 V96 receipt differs from its contract.")
            v96 = replace(v95,forward_partition=True,activation_sha256=sha256(v96_raw).hexdigest())
            if not v97_path.exists():
                return v96
            v97_raw = _read(v97_path)
            v97_marker_sha = (binding.continuation_marker_sha256 if v98_path.exists()
                else binding.current_marker_sha256)
            if (_json(json.loads(v97_raw, object_pairs_hook=_pairs)) != _json(_v97_receipt(root, v96))
                    or sha256(v97_raw).hexdigest() != v97_marker_sha):
                raise ValueError("The A10541 V97 receipt differs from its predecessor contract.")
            v97 = replace(v96, forward_continuation=True, activation_sha256=sha256(v97_raw).hexdigest())
            if not v98_path.exists():
                return v97
            v98_raw = _read(v98_path)
            if (_json(json.loads(v98_raw, object_pairs_hook=_pairs)) != _json(_v98_receipt(root, v97))
                    or sha256(v98_raw).hexdigest() != binding.current_marker_sha256):
                raise ValueError("The A10541 V98 receipt differs from its predecessor contract.")
            return replace(v97, activation_sha256=sha256(v98_raw).hexdigest())
        if not v70_path.exists():return v69
        v70_raw=_read(v70_path)
        if _json(json.loads(v70_raw,object_pairs_hook=_pairs)) != _json(_v70_receipt(root,v69)):
            raise ValueError("The v70 receipt differs from its contract.")
        return replace(v69,policy_version=VERSION_V11,activation_sha256=sha256(v70_raw).hexdigest())
    except (ValueError, TypeError, OSError, KeyError, UnicodeError) as exc:
        if v98_path.exists():
            raise ValueError("Invalid A10541 V98 activation receipt.") from exc
        if v97_path.exists():
            raise ValueError("Invalid A10541 V97 activation receipt.") from exc
        if v96_path.exists():
            raise ValueError("Invalid A10541 V96 activation receipt.") from exc
        if v95_path.exists():
            raise ValueError("Invalid clean 10521 v95 activation receipt.") from exc
        if v94_path.exists():
            raise ValueError("Invalid clean 10521 v94 activation receipt.") from exc
        if v93_path.exists():
            raise ValueError("Invalid clean 10521 v93 activation receipt.") from exc
        if v92_path.exists():
            raise ValueError("Invalid clean 10521 v92 activation receipt.") from exc
        if v91_path.exists():
            raise ValueError("Invalid clean 10521 v91 activation receipt.") from exc
        if v90_path.exists():
            raise ValueError("Invalid clean 10521 v90 activation receipt.") from exc
        if v89_path.exists():
            raise ValueError("Invalid clean 10521 v89 activation receipt.") from exc
        if v88_path.exists():
            raise ValueError("Invalid clean 10521 v88 activation receipt.") from exc
        if v87_path.exists():
            raise ValueError("Invalid clean 10521 v87 activation receipt.") from exc
        if v86_path.exists():
            raise ValueError("Invalid clean 10521 v86 activation receipt.") from exc
        if v85_path.exists():
            raise ValueError("Invalid clean 10521 v85 activation receipt.") from exc
        if v84_path.exists():
            raise ValueError("Invalid clean 10521 v84 activation receipt.") from exc
        if v83_path.exists():
            raise ValueError("Invalid clean 10521 v83 activation receipt.") from exc
        if v82_path.exists():
            raise ValueError("Invalid clean 10521 v82 activation receipt.") from exc
        if v81_path.exists():
            raise ValueError("Invalid clean 10521 v81 activation receipt.") from exc
        if v80_path.exists():
            raise ValueError("Invalid clean 10521 v80 activation receipt.") from exc
        if v79_path.exists():
            raise ValueError("Invalid clean 10521 v79 activation receipt.") from exc
        if v78_path.exists():
            raise ValueError("Invalid clean 10521 v78 activation receipt.") from exc
        if v77_path.exists():
            raise ValueError("Invalid clean 10521 v77 activation receipt.") from exc
        if v76_path.exists():
            raise ValueError("Invalid clean 10521 v76 activation receipt.") from exc
        if v75_path.exists():
            raise ValueError("Invalid clean 10521 v75 activation receipt.") from exc
        if v74_path.exists():
            raise ValueError("Invalid clean 10521 v74 activation receipt.") from exc
        if v73_path.exists():
            raise ValueError("Invalid clean 10521 v73 activation receipt.") from exc
        if v72_path.exists():
            raise ValueError("Invalid clean 10521 v72 activation receipt.") from exc
        if v71_path.exists():
            raise ValueError("Invalid clean 10521 v71 activation receipt.") from exc
        if v70_path.exists():
            raise ValueError("Invalid clean 10521 v70 activation receipt.") from exc
        if v69_path.exists():
            raise ValueError("Invalid clean 10521 v69 activation receipt.") from exc
        if v68_path.exists():
            raise ValueError("Invalid clean 10521 v68 activation receipt.") from exc
        if v67_path.exists():
            raise ValueError("Invalid clean 10521 v67 activation receipt.") from exc
        if v66_path.exists():
            raise ValueError("Invalid clean 10521 v66 activation receipt.") from exc
        if v65_path.exists():
            raise ValueError("Invalid clean 10521 v65 activation receipt.") from exc
        if v64_path.exists():
            raise ValueError("Invalid clean 10521 v64 activation receipt.") from exc
        if v63_path.exists():
            raise ValueError("Invalid clean 10521 v63 activation receipt.") from exc
        if v62_path.exists():
            raise ValueError("Invalid clean 10521 v62 activation receipt.") from exc
        if v61_path.exists():
            raise ValueError("Invalid clean 10521 v61 activation receipt.") from exc
        if v60_path.exists():
            raise ValueError("Invalid clean 10521 v60 activation receipt.") from exc
        if v59_path.exists():
            raise ValueError("Invalid clean 10521 v59 activation receipt.") from exc
        raise ValueError("Invalid clean 10521 v58 activation receipt.") from exc


def catalog_for(root):
    """Return the exact catalog named by the validated 10521 receipt."""
    if selection_for(root) is None:
        raise ValueError("The clean 10521 runtime is not activated.")
    if (Path(root)/".runtime"/V98_ACTIVATION_NAME).is_file():return V98_CATALOG
    if (Path(root)/".runtime"/V97_ACTIVATION_NAME).is_file():return V97_CATALOG
    if (Path(root)/".runtime"/V96_ACTIVATION_NAME).is_file():return V96_CATALOG
    if (Path(root)/".runtime"/V95_ACTIVATION_NAME).is_file():return V95_CATALOG
    if (Path(root)/".runtime"/V94_ACTIVATION_NAME).is_file():return V94_CATALOG
    if (Path(root)/".runtime"/V93_ACTIVATION_NAME).is_file():return V93_CATALOG
    if (Path(root)/".runtime"/V92_ACTIVATION_NAME).is_file():return V92_CATALOG
    if (Path(root)/".runtime"/V91_ACTIVATION_NAME).is_file():return V91_CATALOG
    if (Path(root)/".runtime"/V90_ACTIVATION_NAME).is_file():return V90_CATALOG
    if (Path(root)/".runtime"/V89_ACTIVATION_NAME).is_file():return V89_CATALOG
    if (Path(root)/".runtime"/V88_ACTIVATION_NAME).is_file():return V88_CATALOG
    if (Path(root)/".runtime"/V87_ACTIVATION_NAME).is_file():return V87_CATALOG
    if (Path(root)/".runtime"/V86_ACTIVATION_NAME).is_file():return V86_CATALOG
    if (Path(root)/".runtime"/V85_ACTIVATION_NAME).is_file():return V85_CATALOG
    if (Path(root)/".runtime"/V84_ACTIVATION_NAME).is_file():return V84_CATALOG
    if (Path(root)/".runtime"/V83_ACTIVATION_NAME).is_file():return V83_CATALOG
    if (Path(root)/".runtime"/V82_ACTIVATION_NAME).is_file():return V82_CATALOG
    if (Path(root)/".runtime"/V81_ACTIVATION_NAME).is_file():return V81_CATALOG
    if (Path(root)/".runtime"/V80_ACTIVATION_NAME).is_file():return V80_CATALOG
    if (Path(root)/".runtime"/V79_ACTIVATION_NAME).is_file():return V79_CATALOG
    if (Path(root)/".runtime"/V78_ACTIVATION_NAME).is_file():return V78_CATALOG
    if (Path(root)/".runtime"/V77_ACTIVATION_NAME).is_file():return V77_CATALOG
    if (Path(root)/".runtime"/V76_ACTIVATION_NAME).is_file():return V76_CATALOG
    if (Path(root)/".runtime"/V75_ACTIVATION_NAME).is_file():return V75_CATALOG
    if (Path(root)/".runtime"/V74_ACTIVATION_NAME).is_file():return V74_CATALOG
    if (Path(root)/".runtime"/V73_ACTIVATION_NAME).is_file():return V73_CATALOG
    if (Path(root)/".runtime"/V72_ACTIVATION_NAME).is_file():return V72_CATALOG
    if (Path(root)/".runtime"/V71_ACTIVATION_NAME).is_file():return V71_CATALOG
    if (Path(root)/".runtime"/V70_ACTIVATION_NAME).is_file():return V70_CATALOG
    if (Path(root)/".runtime"/V69_ACTIVATION_NAME).is_file():return V69_CATALOG
    if (Path(root)/".runtime"/V68_ACTIVATION_NAME).is_file():return V68_CATALOG
    if (Path(root)/".runtime"/V67_ACTIVATION_NAME).is_file():return V67_CATALOG
    if (Path(root)/".runtime"/V66_ACTIVATION_NAME).is_file():return V66_CATALOG
    if (Path(root)/".runtime"/V65_ACTIVATION_NAME).is_file():return V65_CATALOG
    if (Path(root)/".runtime"/V64_ACTIVATION_NAME).is_file():
        return V64_CATALOG
    if (Path(root) / ".runtime" / V63_ACTIVATION_NAME).is_file():
        return V63_CATALOG
    if (Path(root) / ".runtime" / V62_ACTIVATION_NAME).is_file():
        return V62_CATALOG
    if (Path(root) / ".runtime" / V61_ACTIVATION_NAME).is_file():
        return V61_CATALOG
    if (Path(root) / ".runtime" / V60_ACTIVATION_NAME).is_file():
        return V60_CATALOG
    if (Path(root) / ".runtime" / V59_ACTIVATION_NAME).is_file():
        return V59_CATALOG
    if (Path(root) / ".runtime" / V58_ACTIVATION_NAME).is_file():
        return V58_CATALOG
    if (Path(root) / ".runtime" / V52_ACTIVATION_NAME).is_file():
        return V52_CATALOG
    if (Path(root) / ".runtime" / V51_ACTIVATION_NAME).is_file():
        return V51_CATALOG
    return (V50_CATALOG if (Path(root) / ".runtime" / V50_ACTIVATION_NAME).is_file()
            else CATALOG)


def _v65_receipt(root,prior):
    if prior is None or prior.policy_version != VERSION_V6:
        raise ValueError("The v65 activation requires the v6 predecessor policy.")
    predecessor=_read(root/".runtime"/V64_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    return {**previous,"kind":V65_KIND,"schema_version":65,
        "schema_sha256":V065_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V65_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest()}


def _v66_receipt(root,prior):
    if prior is None or prior.policy_version != VERSION_V6:
        raise ValueError("The v66 activation requires the verified v6 predecessor.")
    predecessor=_read(root/".runtime"/V65_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    selected=replace(prior,policy_version=VERSION_V7)
    return {**previous,"kind":V66_KIND,"schema_version":66,
        "schema_sha256":V066_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V66_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V7,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V7)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":"per-source-json-schema-v1"}


def _v67_receipt(root,prior):
    if prior is None or prior.policy_version != VERSION_V7:
        raise ValueError("The v67 activation requires the verified v7 predecessor.")
    predecessor=_read(root/".runtime"/V66_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    selected=replace(prior,policy_version=VERSION_V8)
    return {**previous,"kind":V67_KIND,"schema_version":67,
        "schema_sha256":V067_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V67_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V8,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V8)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":"pdf-section-markdown-v1"}


def _v68_receipt(root,prior):
    if prior is None or prior.policy_version != VERSION_V8:
        raise ValueError("The v68 activation requires the verified v8 predecessor.")
    predecessor=_read(root/".runtime"/V67_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    selected=replace(prior,policy_version=VERSION_V9)
    return {**previous,"kind":V68_KIND,"schema_version":68,
        "schema_sha256":V068_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V68_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V9,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V9)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":"pdf-section-markdown-v2"}


def _v69_receipt(root,prior):
    if prior is None or prior.policy_version != VERSION_V9:
        raise ValueError("The v69 activation requires the verified v9 predecessor.")
    predecessor=_read(root/".runtime"/V68_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    selected=replace(prior,policy_version=VERSION_V10)
    return {**previous,"kind":V69_KIND,"schema_version":69,
        "schema_sha256":V069_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V69_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V10,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V10)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":"pdf-batch-markdown-v1","pdf_output_token_reservation":4096,
        "pdf_provenance_level":"batch_input_context"}


def _v70_receipt(root,prior):
    """Build an explicit successor contract; never write it or promote a model.

    The deployment operator must complete real-model acceptance separately.
    Importing this helper and reading a v69 deployment leave it on v10.
    """
    if prior is None or prior.policy_version != VERSION_V10:
        raise ValueError("The v70 activation requires the verified v10 predecessor.")
    predecessor=_read(root/".runtime"/V69_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    selected=replace(prior,policy_version=VERSION_V11)
    return {**previous,"kind":V70_KIND,"schema_version":70,
        "schema_sha256":V070_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V70_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V11,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V11)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":"pdf-batch-markdown-v2"}


def _v71_receipt(root,prior):
    """Explicit v69 -> layout successor; no migration or marker write occurs."""
    from code_assist_v1.knowledge_core.pdf_layout_retention import DECODER_VERSION
    if prior is None or prior.policy_version != VERSION_V10:
        raise ValueError("The v71 activation requires the verified v10 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v71 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V69_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    selected=replace(prior,policy_version=VERSION_V12)
    return {**previous,"kind":V71_KIND,"schema_version":71,
        "schema_sha256":V071_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V71_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V12,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V12)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"pdf_output_token_reservation":4096,
        "pdf_provenance_level":"batch_input_context","pdf_layout_source":"same_verified_original_v1"}


def _v72_receipt(root,prior):
    """Explicit V71 -> math-format successor; never migrate or write markers."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v2 import DECODER_VERSION
    if prior is None or prior.policy_version != VERSION_V12:
        raise ValueError("The v72 activation requires the verified v12 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v72 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V71_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v72 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V13)
    return {**previous,"kind":V72_KIND,"schema_version":72,
        "schema_sha256":V072_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V72_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V13,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V13)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION}


def _v73_receipt(root,prior):
    """Explicit V72 -> caption-subject successor; no writes or model calls.

    The binding constrains exact source-caption literals only. It does not
    certify caption/image association, legend ownership or numeric accuracy.
    """
    from code_assist_v1.knowledge_core.pdf_layout_retention_v3 import DECODER_VERSION, SUBJECT_BINDING_VERSION
    if prior is None or prior.policy_version != VERSION_V13:
        raise ValueError("The v73 activation requires the verified v13 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v73 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V72_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v73 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V14)
    return {**previous,"kind":V73_KIND,"schema_version":73,
        "schema_sha256":V073_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V73_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V14,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V14)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"pdf_subject_binding_version":SUBJECT_BINDING_VERSION}


def _v74_receipt(root,prior):
    """Explicit V73 -> nonblank-group successor; no writes or model calls."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v4 import DECODER_VERSION
    if prior is None or prior.policy_version != VERSION_V14:
        raise ValueError("The v74 activation requires the verified v14 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v74 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V73_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v74 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V15)
    return {**previous,"kind":V74_KIND,"schema_version":74,
        "schema_sha256":V074_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V74_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V15,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V15)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION}


def _v75_receipt(root,prior):
    """Explicit V74 -> literal-appendix successor; never rewrites old output."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v5 import DECODER_VERSION, LITERAL_APPENDIX_VERSION
    if prior is None or prior.policy_version != VERSION_V15:
        raise ValueError("The v75 activation requires the verified v15 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v75 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V74_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v75 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V16)
    return {**previous,"kind":V75_KIND,"schema_version":75,
        "schema_sha256":V075_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V75_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V16,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V16)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"literal_appendix_version":LITERAL_APPENDIX_VERSION}


def _v76_receipt(root,prior):
    """Only the verified V16 predecessor may opt in to direct appearance ROI."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v6 import (
        DECODER_VERSION, LITERAL_APPENDIX_VERSION, ROI_INPUT_VERSION)
    if prior is None or prior.policy_version != VERSION_V16:
        raise ValueError("The v76 activation requires the verified v16 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v76 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V75_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v76 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V17)
    return {**previous,"kind":V76_KIND,"schema_version":76,
        "schema_sha256":V076_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V76_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V17,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V17)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"literal_appendix_version":LITERAL_APPENDIX_VERSION,
        "figure_roi_input_version":ROI_INPUT_VERSION}


def _v77_receipt(root,prior):
    """Verified V17 opt-in; model domain checks are not source accuracy proof."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v7 import (
        DECODER_VERSION, LITERAL_APPENDIX_VERSION, ROI_INPUT_VERSION, CURVE_DOMAIN_VERSION)
    if prior is None or prior.policy_version != VERSION_V17:
        raise ValueError("The v77 activation requires the verified v17 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v77 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V76_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v77 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V18)
    return {**previous,"kind":V77_KIND,"schema_version":77,
        "schema_sha256":V077_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V77_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V18,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V18)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"literal_appendix_version":LITERAL_APPENDIX_VERSION,
        "figure_roi_input_version":ROI_INPUT_VERSION,"curve_domain_version":CURVE_DOMAIN_VERSION}


def _v78_receipt(root,prior):
    """Verified V18 opt-in; writing guidance changes, curve guards do not."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v8 import (
        DECODER_VERSION, LITERAL_APPENDIX_VERSION, ROI_INPUT_VERSION, CURVE_DOMAIN_VERSION)
    if prior is None or prior.policy_version != VERSION_V18:
        raise ValueError("The v78 activation requires the verified v18 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v78 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V77_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v78 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V19)
    return {**previous,"kind":V78_KIND,"schema_version":78,
        "schema_sha256":V078_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V78_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V19,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V19)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"literal_appendix_version":LITERAL_APPENDIX_VERSION,
        "figure_roi_input_version":ROI_INPUT_VERSION,"curve_domain_version":CURVE_DOMAIN_VERSION}


def _v79_receipt(root,prior):
    """Verified V19 opt-in; root-owned request mode is sealed in the plan."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v9 import (
        DECODER_VERSION, LITERAL_APPENDIX_VERSION, ROI_INPUT_VERSION, CURVE_DOMAIN_VERSION)
    if prior is None or prior.policy_version != VERSION_V19:
        raise ValueError("The v79 activation requires the verified v19 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v79 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V78_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v79 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V20)
    return {**previous,"kind":V79_KIND,"schema_version":79,
        "schema_sha256":V079_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V79_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V20,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V20)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"literal_appendix_version":LITERAL_APPENDIX_VERSION,
        "figure_roi_input_version":ROI_INPUT_VERSION,"curve_domain_version":CURVE_DOMAIN_VERSION}


def _v80_receipt(root,prior):
    """Verified V20 predecessor; forward guidance never replaces old receipts."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v10 import (
        DECODER_VERSION, LITERAL_APPENDIX_VERSION, ROI_INPUT_VERSION, CURVE_DOMAIN_VERSION)
    if prior is None or prior.policy_version != VERSION_V20:
        raise ValueError("The v80 activation requires the verified v20 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v80 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V79_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v80 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V21)
    return {**previous,"kind":V80_KIND,"schema_version":80,
        "schema_sha256":V080_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V80_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V21,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V21)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"literal_appendix_version":LITERAL_APPENDIX_VERSION,
        "figure_roi_input_version":ROI_INPUT_VERSION,"curve_domain_version":CURVE_DOMAIN_VERSION}


def _v81_receipt(root,prior):
    """New bounded allocation contract; all V80 receipts remain exact."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v11 import (
        DECODER_VERSION, LITERAL_APPENDIX_VERSION, ROI_INPUT_VERSION, CURVE_DOMAIN_VERSION,
        ALLOCATION_VERSION)
    if prior is None or prior.policy_version != VERSION_V21:
        raise ValueError("The v81 activation requires the verified v21 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v81 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V80_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v81 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V22)
    return {**previous,"kind":V81_KIND,"schema_version":81,
        "schema_sha256":V081_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V81_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V22,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V22)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"literal_appendix_version":LITERAL_APPENDIX_VERSION,
        "figure_roi_input_version":ROI_INPUT_VERSION,"curve_domain_version":CURVE_DOMAIN_VERSION,
        "pdf_output_token_reservation":8192,"pdf_base_planning_output_tokens":4096,
        "pdf_output_allocation_version":ALLOCATION_VERSION}


def _v96_receipt(root, prior):
    """Pure A-only successor constructor; source V95 records remain untouched."""
    if prior is None or prior.policy_version != VERSION_V36 or prior.forward_partition:
        raise ValueError("V96 requires the original verified V95 selection.")
    root = Path(root).resolve(strict=True)
    binding = load_binding(root)
    if (binding is None or binding.kind != _CLEAN_RECOVERY_KIND or binding.port != 10541
            or binding.provider_mode != "openai-compatible"):
        raise ValueError("V96 requires the explicit A10541 OpenAI-compatible origin.")
    predecessor = _read(root/".runtime"/V95_ACTIVATION_NAME)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The V96 predecessor differs from its verified V95 selection.")
    activation_root = binding.activation_data_root or root
    if (activation_root.parent.name != "code" or activation_root.name != "data-root"
            or activation_root.parent.parent.name != "wiki-generation-ab-A-10541-20261011-r1"):
        raise ValueError("V96 is restricted to the A10541 activation origin.")
    selected = replace(prior,forward_partition=True)
    policy = selected.upload_policy()
    return dict(kind=V96_KIND, port=10541, data_root=str(activation_root),
        source_data_root=str(binding.source_data_root), schema_version=96,
        schema_sha256=V096_EXPECTED_SCHEMA_SHA256, catalog_checksum_sha256=V96_CATALOG.checksum_sha256,
        source_schema_catalog_checksum=V95_CATALOG.checksum_sha256,
        source_binding_sha256=binding.source_binding_sha256 or binding.document_sha256,
        predecessor_activation_sha256=sha256(predecessor).hexdigest(),
        pdf_writing_policy_version=VERSION_V36, forward_partition=True,
        pdf_prompt_sha256=_hash(prompt_for_policy(VERSION_V36)),
        pdf_upload_policy_sha256=_hash(policy.encoded()),
        pdf_upload_configuration=json.loads(policy.encoded())["configuration"],
        python_executable=prior.python_executable, registry_sha256=prior.registry_sha256,
        provider=prior.provider, model_id=prior.model_id, wire_model=prior.wire_model,
        codex_reasoning_effort=prior.codex_reasoning_effort,
        pdf_context_window_tokens=16_384, pdf_output_token_reservation=4096,
        semantic_review_performed=False, pdf_chart_numeric_accuracy_verified=False)


def _v95_receipt(root, prior):
    """Select original prose plus existing numerical append after verified V94."""
    if prior is None or prior.policy_version != VERSION_V35:
        raise ValueError("The v95 activation requires the verified v94 predecessor.")
    predecessor = _read(root/".runtime"/V94_ACTIVATION_NAME)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v95 predecessor differs from its verified selection.")
    previous = json.loads(predecessor, object_pairs_hook=_pairs)
    selected = replace(prior, policy_version=VERSION_V36)
    policy = selected.upload_policy()
    return {**previous, "kind": V95_KIND, "schema_version": 95,
        "schema_sha256": V095_EXPECTED_SCHEMA_SHA256,
        "catalog_checksum_sha256": V95_CATALOG.checksum_sha256,
        "predecessor_activation_sha256": sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version": VERSION_V36,
        "pdf_prompt_sha256": _hash(prompt_for_policy(VERSION_V36)),
        "pdf_upload_policy_sha256": _hash(policy.encoded()),
        "chart_preparation": json.loads(policy.encoded())["configuration"]["chart_preparation"],
        "pdf_source_definitions": False, "pdf_request_budget_bytes": 1_114_112,
        "pdf_output_token_reservation": 4096, "pdf_chart_numeric_accuracy_verified": False}


def _v94_receipt(root, prior):
    """Activate already-trialed source labels without rewriting old receipts."""
    if prior is None or prior.policy_version != VERSION_V34:
        raise ValueError("The v94 activation requires the verified v93 predecessor.")
    predecessor = _read(root/".runtime"/V93_ACTIVATION_NAME)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v94 predecessor differs from its verified selection.")
    previous = json.loads(predecessor, object_pairs_hook=_pairs)
    selected = replace(prior, policy_version=VERSION_V35)
    policy = selected.upload_policy()
    return {**previous, "kind": V94_KIND, "schema_version": 94,
        "schema_sha256": V094_EXPECTED_SCHEMA_SHA256,
        "catalog_checksum_sha256": V94_CATALOG.checksum_sha256,
        "predecessor_activation_sha256": sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version": VERSION_V35,
        "pdf_prompt_sha256": _hash(prompt_for_policy(VERSION_V35)),
        "pdf_upload_policy_sha256": _hash(policy.encoded()),
        "chart_preparation": json.loads(policy.encoded())["configuration"]["chart_preparation"],
        "pdf_source_definitions": True, "pdf_page_appearance_crops": True,
        "pdf_page_appearance_dpi": 288, "pdf_request_budget_bytes": 1_114_112,
        "pdf_output_token_reservation": 4096, "pdf_chart_numeric_accuracy_verified": False}


def _v93_receipt(root,prior):
    """Explicit bounded chart-context successor; never relabel V92 evidence."""
    if prior is None or prior.policy_version != VERSION_V33:
        raise ValueError("The v93 activation requires the verified v92 predecessor.")
    predecessor=_read(root/".runtime"/V92_ACTIVATION_NAME)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v93 predecessor differs from its verified selection.")
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    selected=replace(prior,policy_version=VERSION_V34)
    policy=selected.upload_policy()
    from code_assist_v1.knowledge_core.pdf_chart_supplement import VERSION as supplement_version
    return {**previous,"kind":V93_KIND,"schema_version":93,
        "schema_sha256":V093_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V93_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V34,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V34)),
        "pdf_upload_policy_sha256":_hash(policy.encoded()),
        "chart_preparation":json.loads(policy.encoded())["configuration"]["chart_preparation"],
        "pdf_chart_supplement_version":supplement_version,"pdf_chart_numeric_accuracy_verified":False}


def _v92_receipt(root,prior):
    """Explicit table-only forward activation, without native/curve contracts."""
    if prior is None or prior.policy_version != VERSION_V32:
        raise ValueError("The v92 activation requires the verified v91 predecessor.")
    predecessor=_read(root/".runtime"/V91_ACTIVATION_NAME)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v92 predecessor differs from its verified selection.")
    # The entire chain has already verified V69. Retain its prose contract,
    # not the superseded native/ROI/curve fields accumulated in V71–V91.
    original=json.loads(_read(root/".runtime"/V69_ACTIVATION_NAME),object_pairs_hook=_pairs)
    selected=replace(prior,policy_version=VERSION_V33)
    return {**original,"kind":V92_KIND,"schema_version":92,
        "schema_sha256":V092_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V92_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V33,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V33)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":"pdf-batch-markdown-v1","pdf_output_token_reservation":4096,
        "pdf_provenance_level":"batch_input_context",
        "pdf_literal_table_supplement_version":"pdf-layout-literal-table-supplement-v1"}


def _v91_receipt(root,prior):
    """Clarified native tick conditions; every V90 activation stays exact."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v21 import (
        DECODER_VERSION, LITERAL_APPENDIX_VERSION, ROI_INPUT_VERSION, CURVE_DOMAIN_VERSION,
        ALLOCATION_VERSION, SUPPLEMENTAL_BUDGET_VERSION, TEXT_PARTITION_VERSION,
        TEXT_CHUNK_BYTES, native_prompt_identity, structured_prompt_identity,
        text_prompt_identity, text_response_contract_identity)
    if prior is None or prior.policy_version != VERSION_V31:
        raise ValueError("The v91 activation requires the verified v31 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v91 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V90_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v91 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V32)
    return {**previous,"kind":V91_KIND,"schema_version":91,
        "schema_sha256":V091_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V91_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V32,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V32)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"literal_appendix_version":LITERAL_APPENDIX_VERSION,
        "figure_roi_input_version":ROI_INPUT_VERSION,"curve_domain_version":CURVE_DOMAIN_VERSION,
        "pdf_output_token_reservation":8192,"pdf_base_planning_output_tokens":4096,
        "pdf_output_allocation_version":ALLOCATION_VERSION,
        "pdf_native_prompt_identity":native_prompt_identity(),
        "pdf_structured_prompt_identity":structured_prompt_identity(),
        "pdf_text_prompt_identity":text_prompt_identity(),
        "pdf_supplemental_budget_version":SUPPLEMENTAL_BUDGET_VERSION,
        "pdf_text_partition":{"version":TEXT_PARTITION_VERSION,"max_chunk_bytes":TEXT_CHUNK_BYTES},
        "pdf_text_response_contract":text_response_contract_identity()}


def _v90_receipt(root,prior):
    """Categorical native-axis successor; all V89 receipts stay exact."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v20 import (
        DECODER_VERSION, LITERAL_APPENDIX_VERSION, ROI_INPUT_VERSION, CURVE_DOMAIN_VERSION,
        ALLOCATION_VERSION, SUPPLEMENTAL_BUDGET_VERSION, TEXT_PARTITION_VERSION,
        TEXT_CHUNK_BYTES, native_prompt_identity, structured_prompt_identity,
        text_prompt_identity, text_response_contract_identity)
    if prior is None or prior.policy_version != VERSION_V30:
        raise ValueError("The v90 activation requires the verified v30 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v90 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V89_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v90 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V31)
    return {**previous,"kind":V90_KIND,"schema_version":90,
        "schema_sha256":V090_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V90_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V31,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V31)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"literal_appendix_version":LITERAL_APPENDIX_VERSION,
        "figure_roi_input_version":ROI_INPUT_VERSION,"curve_domain_version":CURVE_DOMAIN_VERSION,
        "pdf_output_token_reservation":8192,"pdf_base_planning_output_tokens":4096,
        "pdf_output_allocation_version":ALLOCATION_VERSION,
        "pdf_native_prompt_identity":native_prompt_identity(),
        "pdf_structured_prompt_identity":structured_prompt_identity(),
        "pdf_text_prompt_identity":text_prompt_identity(),
        "pdf_supplemental_budget_version":SUPPLEMENTAL_BUDGET_VERSION,
        "pdf_text_partition":{"version":TEXT_PARTITION_VERSION,"max_chunk_bytes":TEXT_CHUNK_BYTES},
        "pdf_text_response_contract":text_response_contract_identity()}


def _v89_receipt(root,prior):
    """Complete-source packing successor; all V88 receipts stay exact."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v19 import (
        DECODER_VERSION, LITERAL_APPENDIX_VERSION, ROI_INPUT_VERSION, CURVE_DOMAIN_VERSION,
        ALLOCATION_VERSION, SUPPLEMENTAL_BUDGET_VERSION, TEXT_PARTITION_VERSION,
        TEXT_CHUNK_BYTES, native_prompt_identity, structured_prompt_identity,
        text_prompt_identity, text_response_contract_identity)
    if prior is None or prior.policy_version != VERSION_V29:
        raise ValueError("The v89 activation requires the verified v29 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v89 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V88_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v89 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V30)
    return {**previous,"kind":V89_KIND,"schema_version":89,
        "schema_sha256":V089_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V89_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V30,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V30)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"literal_appendix_version":LITERAL_APPENDIX_VERSION,
        "figure_roi_input_version":ROI_INPUT_VERSION,"curve_domain_version":CURVE_DOMAIN_VERSION,
        "pdf_output_token_reservation":8192,"pdf_base_planning_output_tokens":4096,
        "pdf_output_allocation_version":ALLOCATION_VERSION,
        "pdf_native_prompt_identity":native_prompt_identity(),
        "pdf_structured_prompt_identity":structured_prompt_identity(),
        "pdf_text_prompt_identity":text_prompt_identity(),
        "pdf_supplemental_budget_version":SUPPLEMENTAL_BUDGET_VERSION,
        "pdf_text_partition":{"version":TEXT_PARTITION_VERSION,"max_chunk_bytes":TEXT_CHUNK_BYTES},
        "pdf_text_response_contract":text_response_contract_identity()}


def _v88_receipt(root,prior):
    """Forward native frame guidance; exact V87 text/visual history stays frozen."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v18 import (
        DECODER_VERSION, LITERAL_APPENDIX_VERSION, ROI_INPUT_VERSION, CURVE_DOMAIN_VERSION,
        ALLOCATION_VERSION, SUPPLEMENTAL_BUDGET_VERSION, native_prompt_identity,
        structured_prompt_identity, text_prompt_identity)
    if prior is None or prior.policy_version != VERSION_V28:
        raise ValueError("The v88 activation requires the verified v28 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v88 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V87_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v88 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V29)
    return {**previous,"kind":V88_KIND,"schema_version":88,
        "schema_sha256":V088_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V88_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V29,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V29)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"literal_appendix_version":LITERAL_APPENDIX_VERSION,
        "figure_roi_input_version":ROI_INPUT_VERSION,"curve_domain_version":CURVE_DOMAIN_VERSION,
        "pdf_output_token_reservation":8192,"pdf_base_planning_output_tokens":4096,
        "pdf_output_allocation_version":ALLOCATION_VERSION,
        "pdf_native_prompt_identity":native_prompt_identity(),
        "pdf_structured_prompt_identity":structured_prompt_identity(),
        "pdf_text_prompt_identity":text_prompt_identity(),
        "pdf_supplemental_budget_version":SUPPLEMENTAL_BUDGET_VERSION}



def _v87_receipt(root,prior):
    """Forward text JSON guidance; exact V86 visual/native history stays frozen."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v17 import (
        DECODER_VERSION, LITERAL_APPENDIX_VERSION, ROI_INPUT_VERSION, CURVE_DOMAIN_VERSION,
        ALLOCATION_VERSION, SUPPLEMENTAL_BUDGET_VERSION, native_prompt_identity,
        structured_prompt_identity, text_prompt_identity)
    if prior is None or prior.policy_version != VERSION_V27:
        raise ValueError("The v87 activation requires the verified v27 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v87 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V86_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v87 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V28)
    return {**previous,"kind":V87_KIND,"schema_version":87,
        "schema_sha256":V087_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V87_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V28,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V28)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"literal_appendix_version":LITERAL_APPENDIX_VERSION,
        "figure_roi_input_version":ROI_INPUT_VERSION,"curve_domain_version":CURVE_DOMAIN_VERSION,
        "pdf_output_token_reservation":8192,"pdf_base_planning_output_tokens":4096,
        "pdf_output_allocation_version":ALLOCATION_VERSION,
        "pdf_native_prompt_identity":native_prompt_identity(),
        "pdf_structured_prompt_identity":structured_prompt_identity(),
        "pdf_text_prompt_identity":text_prompt_identity(),
        "pdf_supplemental_budget_version":SUPPLEMENTAL_BUDGET_VERSION}


def _v86_receipt(root,prior):
    """Forward visual prompt alignment; exact V85/native history stays frozen."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v16 import (
        DECODER_VERSION, LITERAL_APPENDIX_VERSION, ROI_INPUT_VERSION, CURVE_DOMAIN_VERSION,
        ALLOCATION_VERSION, SUPPLEMENTAL_BUDGET_VERSION, native_prompt_identity,
        structured_prompt_identity)
    if prior is None or prior.policy_version != VERSION_V26:
        raise ValueError("The v86 activation requires the verified v26 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v86 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V85_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v86 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V27)
    return {**previous,"kind":V86_KIND,"schema_version":86,
        "schema_sha256":V086_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V86_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V27,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V27)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"literal_appendix_version":LITERAL_APPENDIX_VERSION,
        "figure_roi_input_version":ROI_INPUT_VERSION,"curve_domain_version":CURVE_DOMAIN_VERSION,
        "pdf_output_token_reservation":8192,"pdf_base_planning_output_tokens":4096,
        "pdf_output_allocation_version":ALLOCATION_VERSION,
        "pdf_native_prompt_identity":native_prompt_identity(),
        "pdf_structured_prompt_identity":structured_prompt_identity(),
        "pdf_supplemental_budget_version":SUPPLEMENTAL_BUDGET_VERSION}


def _v85_receipt(root,prior):
    """Forward native prompt alignment; the exact V84 predecessor remains frozen."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v15 import (
        DECODER_VERSION, LITERAL_APPENDIX_VERSION, ROI_INPUT_VERSION, CURVE_DOMAIN_VERSION,
        ALLOCATION_VERSION, SUPPLEMENTAL_BUDGET_VERSION, native_prompt_identity)
    if prior is None or prior.policy_version != VERSION_V25:
        raise ValueError("The v85 activation requires the verified v25 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v85 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V84_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v85 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V26)
    return {**previous,"kind":V85_KIND,"schema_version":85,
        "schema_sha256":V085_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V85_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V26,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V26)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"literal_appendix_version":LITERAL_APPENDIX_VERSION,
        "figure_roi_input_version":ROI_INPUT_VERSION,"curve_domain_version":CURVE_DOMAIN_VERSION,
        "pdf_output_token_reservation":8192,"pdf_base_planning_output_tokens":4096,
        "pdf_output_allocation_version":ALLOCATION_VERSION,
        "pdf_native_prompt_identity":native_prompt_identity(),
        "pdf_supplemental_budget_version":SUPPLEMENTAL_BUDGET_VERSION}


def _v82_receipt(root,prior):
    """Forward finite-text guidance, preserving exact V81 allocation and history."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v12 import (
        DECODER_VERSION, LITERAL_APPENDIX_VERSION, ROI_INPUT_VERSION, CURVE_DOMAIN_VERSION,
        ALLOCATION_VERSION)
    if prior is None or prior.policy_version != VERSION_V22:
        raise ValueError("The v82 activation requires the verified v22 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v82 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V81_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v82 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V23)
    return {**previous,"kind":V82_KIND,"schema_version":82,
        "schema_sha256":V082_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V82_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V23,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V23)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"literal_appendix_version":LITERAL_APPENDIX_VERSION,
        "figure_roi_input_version":ROI_INPUT_VERSION,"curve_domain_version":CURVE_DOMAIN_VERSION,
        "pdf_output_token_reservation":8192,"pdf_base_planning_output_tokens":4096,
        "pdf_output_allocation_version":ALLOCATION_VERSION}




def _v83_receipt(root,prior):
    """Forward bounded-text partition, preserving exact V82 configuration and history."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v13 import (
        DECODER_VERSION, LITERAL_APPENDIX_VERSION, ROI_INPUT_VERSION, CURVE_DOMAIN_VERSION,
        ALLOCATION_VERSION)
    if prior is None or prior.policy_version != VERSION_V23:
        raise ValueError("The v83 activation requires the verified v23 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v83 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V82_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v83 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V24)
    return {**previous,"kind":V83_KIND,"schema_version":83,
        "schema_sha256":V083_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V83_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V24,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V24)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"literal_appendix_version":LITERAL_APPENDIX_VERSION,
        "figure_roi_input_version":ROI_INPUT_VERSION,"curve_domain_version":CURVE_DOMAIN_VERSION,
        "pdf_output_token_reservation":8192,"pdf_base_planning_output_tokens":4096,
        "pdf_output_allocation_version":ALLOCATION_VERSION}


def _v84_receipt(root,prior):
    """Forward named bounds; original V83 activation and allocation stay exact."""
    from code_assist_v1.knowledge_core.pdf_layout_retention_v14 import (
        DECODER_VERSION, LITERAL_APPENDIX_VERSION, ROI_INPUT_VERSION, CURVE_DOMAIN_VERSION,
        ALLOCATION_VERSION)
    if prior is None or prior.policy_version != VERSION_V24:
        raise ValueError("The v84 activation requires the verified v24 predecessor.")
    if (root/".runtime"/V70_ACTIVATION_NAME).exists():
        raise ValueError("The v84 activation must not mix a v70 writer activation.")
    predecessor=_read(root/".runtime"/V83_ACTIVATION_NAME)
    previous=json.loads(predecessor,object_pairs_hook=_pairs)
    if sha256(predecessor).hexdigest() != prior.activation_sha256:
        raise ValueError("The v84 predecessor activation differs from its verified selection.")
    selected=replace(prior,policy_version=VERSION_V25)
    return {**previous,"kind":V84_KIND,"schema_version":84,
        "schema_sha256":V084_EXPECTED_SCHEMA_SHA256,"catalog_checksum_sha256":V84_CATALOG.checksum_sha256,
        "predecessor_activation_sha256":sha256(predecessor).hexdigest(),
        "pdf_writing_policy_version":VERSION_V25,"pdf_prompt_sha256":_hash(prompt_for_policy(VERSION_V25)),
        "pdf_upload_policy_sha256":_hash(selected.upload_policy().encoded()),
        "pdf_response_format":DECODER_VERSION,"literal_appendix_version":LITERAL_APPENDIX_VERSION,
        "figure_roi_input_version":ROI_INPUT_VERSION,"curve_domain_version":CURVE_DOMAIN_VERSION,
        "pdf_output_token_reservation":8192,"pdf_base_planning_output_tokens":4096,
        "pdf_output_allocation_version":ALLOCATION_VERSION}
