"""Explicit v45 selection and visual worker transport; no activation or calls on read.

The operator registry must declare image support. Builtins/company models are
never promoted implicitly. Existing text/v41 jobs keep their historical adapters.
"""
from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
from pathlib import Path

from code_assist_v1.knowledge_core.migrations.pdf_visual_publication_catalog import (
    PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG, V045_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_visual_writing_policy_catalog import (
    PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG, V046_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.pdf_evidence_units_catalog import (
    PDF_EVIDENCE_UNITS_MIGRATION_CATALOG, V047_EXPECTED_SCHEMA_SHA256,
)
from code_assist_v1.knowledge_core.migrations.native_semantic_targets_catalog import NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_owned_evidence_prompt_catalog import PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_fenced_response_snapshot_catalog import PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG
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
from code_assist_v1.knowledge_core.migrations.pdf_native_tick_conditions_catalog import PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_v10_literal_tables_catalog import PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.pdf_visual_authoring import VERSION_V33 as WRITING_V33
from code_assist_v1.knowledge_core.migrations.pdf_v10_chart_tables_catalog import PDF_V10_CHART_TABLES_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.pdf_visual_authoring import VERSION_V34 as WRITING_V34
from code_assist_v1.knowledge_core.migrations.pdf_chart_labels_catalog import PDF_CHART_LABELS_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.pdf_visual_authoring import VERSION_V35 as WRITING_V35
from code_assist_v1.knowledge_core.migrations.pdf_numeric_append_catalog import PDF_NUMERIC_APPEND_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.pdf_visual_authoring import VERSION_V36 as WRITING_V36
from code_assist_v1.knowledge_core.pdf_chart_preparation import PdfChartPreparationLimits
from code_assist_v1.knowledge_core.pdf_section_writing import VERSION as WRITING_V5, VERSION_V6 as WRITING_V6, VERSION_V7 as WRITING_V7, OUTPUT_TOKENS, response_format as section_response_format
from code_assist_v1.knowledge_core.pdf_section_markdown import VERSION as WRITING_V8, VERSION_V9 as WRITING_V9
from code_assist_v1.knowledge_core.pdf_batch_markdown import VERSION as WRITING_V10, OUTPUT_TOKENS as BATCH_OUTPUT_TOKENS
from code_assist_v1.knowledge_core.pdf_source_faithful_markdown import VERSION as WRITING_V11
from code_assist_v1.knowledge_core.pdf_layout_retention import VERSION as WRITING_V12
from code_assist_v1.knowledge_core.pdf_layout_retention_v2 import VERSION as WRITING_V13
from code_assist_v1.knowledge_core.pdf_layout_retention_v3 import VERSION as WRITING_V14
from code_assist_v1.knowledge_core.pdf_layout_retention_v4 import VERSION as WRITING_V15
from code_assist_v1.knowledge_core.pdf_layout_retention_v5 import VERSION as WRITING_V16
from code_assist_v1.knowledge_core.pdf_layout_retention_v6 import VERSION as WRITING_V17
from code_assist_v1.knowledge_core.pdf_layout_retention_v7 import VERSION as WRITING_V18
from code_assist_v1.knowledge_core.pdf_layout_retention_v8 import VERSION as WRITING_V19
from code_assist_v1.knowledge_core.pdf_layout_retention_v9 import VERSION as WRITING_V20
from code_assist_v1.knowledge_core.pdf_layout_retention_v10 import VERSION as WRITING_V21
from code_assist_v1.knowledge_core.pdf_layout_retention_v11 import VERSION as WRITING_V22
from code_assist_v1.knowledge_core.pdf_layout_retention_v12 import VERSION as WRITING_V23
from code_assist_v1.knowledge_core.pdf_layout_retention_v13 import VERSION as WRITING_V24
from code_assist_v1.knowledge_core.pdf_layout_retention_v14 import VERSION as WRITING_V25
from code_assist_v1.knowledge_core.pdf_layout_retention_v15 import VERSION as WRITING_V26
from code_assist_v1.knowledge_core.pdf_layout_retention_v16 import VERSION as WRITING_V27
from code_assist_v1.knowledge_core.pdf_layout_retention_v17 import VERSION as WRITING_V28
from code_assist_v1.knowledge_core.pdf_layout_retention_v18 import VERSION as WRITING_V29
from code_assist_v1.knowledge_core.pdf_layout_retention_v19 import VERSION as WRITING_V30
from code_assist_v1.knowledge_core.pdf_visual_authoring import VERSION_V31 as WRITING_V31
from code_assist_v1.knowledge_core.pdf_visual_authoring import VERSION_V32 as WRITING_V32
from code_assist_v1.knowledge_core.migrations.semantic_rename_reseed_catalog import SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_atomic_request_partition_catalog import PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_context_resume_catalog import PDF_CONTEXT_RESUME_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_terminal_result_reuse_catalog import PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.protected_extraction_provenance_catalog import PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.protected_pdf_visual_binding_catalog import PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.temporal_root_rebinding_catalog import TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.pdf_visual_authoring import VERSION as WRITING_V1, VERSION_V2 as WRITING_V2, VERSION_V3 as WRITING_V3
from code_assist_v1.knowledge_core.services.pdf_visual_authoring_workflow import (
    PdfVisualUploadPolicy, VERSION, HANDLER_VERSION,
)
from code_assist_v1.knowledge_core.wiki_authoring import AuthoringModelPolicy, _json, _hash
from code_assist_v1.llm_images import IMAGE_INPUT_CAPABILITY
from code_assist_v1.tools.pdf_publication_runtime import (
    PDF_PUBLICATION_MIGRATION_CATALOG, V041_EXPECTED_SCHEMA_SHA256, pdf_python_for, _pairs,
)

ACTIVATION_NAME = "pdf-visual-publication-v45-enabled.json"
WRITING_V2_ACTIVATION_NAME = "pdf-visual-writing-v46-enabled.json"
EVIDENCE_V3_ACTIVATION_NAME = "pdf-evidence-v47-enabled.json"
VISUAL_CATALOGS = (PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG, PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG,
    PDF_EVIDENCE_UNITS_MIGRATION_CATALOG, NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG,
    SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG,
    PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG,
    PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG,
    TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG,
    PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG,
    PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG,
    PDF_SECTION_WRITING_MIGRATION_CATALOG, PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG, PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG, PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG, PDF_BATCH_MARKDOWN_MIGRATION_CATALOG, PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG, PDF_LAYOUT_RETENTION_MIGRATION_CATALOG, PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG, PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG, PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG, PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG, PDF_FIGURE_ROI_MIGRATION_CATALOG, PDF_CURVE_DOMAIN_MIGRATION_CATALOG, PDF_NONPLOT_WRITING_MIGRATION_CATALOG, PDF_NONTHINKING_WRITING_MIGRATION_CATALOG, PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG, PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG, PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG, PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG, PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG, PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG, PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG, PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG, PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG, PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG, PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG,
    PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG, PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG,
    PDF_V10_CHART_TABLES_MIGRATION_CATALOG, PDF_CHART_LABELS_MIGRATION_CATALOG,
    PDF_NUMERIC_APPEND_MIGRATION_CATALOG, PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG,
    PDF_CONTEXT_RESUME_MIGRATION_CATALOG, PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG)

# Explicit local installed recognizer profile. This is not an English recognizer
# claim, an installation request, or corrected source text.
CHART_OCR_LANGUAGE = 'ko'


def _name(value):
    return (type(value) is str and bool(value) and value == value.strip()
        and len(value) <= 160 and not any(ord(c) < 32 for c in value))


def _model_identity(model_id, cfg):
    if not _name(model_id) or type(cfg) is not dict:
        raise ValueError("Invalid visual model selection.")
    capability = cfg.get("capabilities")
    if (type(capability) not in (list, tuple, set, frozenset)
            or IMAGE_INPUT_CAPABILITY not in capability
            or cfg.get("enabled") is False or cfg.get("disabled") is True):
        raise ValueError("The selected visual model must explicitly support image-input-v1.")
    runtime = cfg.get("runtime", "api")
    if runtime == "codex":
        wire = cfg.get("codex_cli_model")
        effort = cfg.get("codex_reasoning_effort")
        if not _name(wire) or effort not in ("none", "minimal", "low", "medium", "high", "xhigh"):
            raise ValueError("Visual Codex selection needs an explicit CLI model and reasoning effort.")
        provider = "codex"
    elif runtime == "api" and cfg.get("env_id", model_id) != "codex-local":
        wire, effort, provider = cfg.get("model"), None, "hynix_api"
        if not _name(wire):
            raise ValueError("Visual API selection needs an explicit wire model.")
    else:
        raise ValueError("Invalid visual model runtime.")
    return dict(provider=provider, model_id=model_id, wire_model=wire, codex_reasoning_effort=effort)


@dataclass(frozen=True, slots=True)
class PdfVisualRuntimeSelection:
    root: Path
    python_executable: str
    provider: str
    model_id: str
    wire_model: str
    codex_reasoning_effort: str | None
    registry_sha256: str
    activation_sha256: str
    policy_version: str = field(default=WRITING_V1, kw_only=True)
    forward_partition: bool = field(default=False, kw_only=True)
    forward_continuation: bool = field(default=False, kw_only=True)

    def model_policy(self):
        # API receipts bind the wire alias; Codex aliases are checked by the
        # runtime guard, NOT claimed as provider-issued receipt evidence.
        if self.policy_version in (WRITING_V35, WRITING_V36):
            return AuthoringModelPolicy(provider_order=(self.provider,), model_ids=(self.model_id,),
                wire_models=((self.model_id,self.wire_model),),max_attempts=1,
                request_budget_bytes=1_114_112,max_tokens=BATCH_OUTPUT_TOKENS)
        if self.policy_version in (WRITING_V33, WRITING_V34):
            return AuthoringModelPolicy(provider_order=(self.provider,), model_ids=(self.model_id,),
                wire_models=((self.model_id,self.wire_model),),max_attempts=1,
                request_budget_bytes=1_048_576,max_tokens=BATCH_OUTPUT_TOKENS)
        return AuthoringModelPolicy(provider_order=(self.provider,), model_ids=(self.model_id,),
            wire_models=((self.model_id, self.wire_model),), max_attempts=1, request_budget_bytes=1_048_576,
            max_tokens=8192 if self.policy_version in (WRITING_V22,WRITING_V23,WRITING_V24,WRITING_V25, WRITING_V26, WRITING_V27, WRITING_V28, WRITING_V29, WRITING_V30, WRITING_V31, WRITING_V32) else BATCH_OUTPUT_TOKENS if self.policy_version in (WRITING_V10,WRITING_V11,WRITING_V12,WRITING_V13,WRITING_V14,WRITING_V15,WRITING_V16,WRITING_V17,WRITING_V18,WRITING_V19,WRITING_V20,WRITING_V21) else OUTPUT_TOKENS if self.policy_version in (WRITING_V5,WRITING_V6,WRITING_V7,WRITING_V8,WRITING_V9) else 8192)

    def upload_policy(self):
        if self.forward_partition:
            if self.policy_version != WRITING_V36:
                raise ValueError("Forward partition requires the original V36 prose contract.")
            return PdfVisualUploadPolicy(model=self.model_policy(), policy_version=self.policy_version,
                chart_ocr_language=CHART_OCR_LANGUAGE, chart_limits=PdfChartPreparationLimits(),
                forward_partition=True)
        if self.policy_version in (WRITING_V34, WRITING_V35, WRITING_V36):
            return PdfVisualUploadPolicy(model=self.model_policy(), policy_version=self.policy_version,
                chart_ocr_language=CHART_OCR_LANGUAGE, chart_limits=PdfChartPreparationLimits())
        return PdfVisualUploadPolicy(model=self.model_policy(), policy_version=self.policy_version)


def _v45_selection_for(root):
    """Validate a read-only marker and explicit registry, never infer capabilities."""
    from code_assist_v1.tools import preservation_preview_runtime as previous
    root = Path(root).resolve(strict=True)
    if not previous._is_registered_root(root):
        raise ValueError("Invalid visual PDF activation root.")
    try:
        raw = (root / ".runtime" / ACTIVATION_NAME).read_bytes()
        marker = json.loads(raw, object_pairs_hook=_pairs)
        if type(marker) is not dict or not _name(marker.get("model_id")):
            raise ValueError
        registry_raw = (root / ".runtime/api_config.json").read_bytes()
        registry = json.loads(registry_raw, object_pairs_hook=_pairs)
        if type(registry) is not dict or type(registry.get("models")) is not dict:
            raise ValueError
        identity = _model_identity(marker["model_id"], registry["models"].get(marker["model_id"]))
        python = pdf_python_for(root)
        selection = PdfVisualRuntimeSelection(root, python, **identity,
            registry_sha256=sha256(registry_raw).hexdigest(), activation_sha256=sha256(raw).hexdigest())
        expected = dict(port=10421, schema_version=45, schema_sha256=V045_EXPECTED_SCHEMA_SHA256,
            data_root=str(previous.activation_identity_root(root)), predecessor_schema_version=41,
            predecessor_schema_sha256=V041_EXPECTED_SCHEMA_SHA256,
            workflow_version=VERSION, handler_version=HANDLER_VERSION,
            policy_sha256=_hash(selection.upload_policy().encoded()),
            image_input_capability=IMAGE_INPUT_CAPABILITY, semantic_review_performed=False,
            python_executable=python, registry_sha256=selection.registry_sha256, **identity)
        # JSON equality retains integer/bool distinctions, including nested policy.
        if _json(marker) != _json(expected):
            raise ValueError
        return selection
    except (ValueError, TypeError, OSError, KeyError, UnicodeError) as exc:
        raise ValueError("Invalid visual PDF activation receipt or image model registry.") from exc


def _v46_selection_for(root):
    """V45 still means v1; v2 requires a separate exact successor receipt."""
    previous = _v45_selection_for(root)
    path = previous.root / '.runtime' / WRITING_V2_ACTIVATION_NAME
    if not path.exists():
        return previous
    from code_assist_v1.tools.preservation_preview_runtime import activation_identity_root
    try:
        raw = path.read_bytes()
        marker = json.loads(raw, object_pairs_hook=_pairs)
        selection = replace(previous, policy_version=WRITING_V2, activation_sha256=sha256(raw).hexdigest())
        expected = dict(port=10421, schema_version=46, schema_sha256=V046_EXPECTED_SCHEMA_SHA256,
            data_root=str(activation_identity_root(selection.root)), predecessor_schema_version=45,
            predecessor_schema_sha256=V045_EXPECTED_SCHEMA_SHA256,
            predecessor_activation_sha256=previous.activation_sha256,
            workflow_version=VERSION, handler_version=HANDLER_VERSION, writing_policy_version=WRITING_V2,
            policy_sha256=_hash(selection.upload_policy().encoded()),
            image_input_capability=IMAGE_INPUT_CAPABILITY, semantic_review_performed=False,
            python_executable=selection.python_executable, registry_sha256=selection.registry_sha256,
            provider=selection.provider, model_id=selection.model_id, wire_model=selection.wire_model,
            codex_reasoning_effort=selection.codex_reasoning_effort)
        if _json(marker) != _json(expected):
            raise ValueError
        return selection
    except (ValueError, TypeError, OSError, KeyError, UnicodeError) as exc:
        raise ValueError('Invalid visual PDF v2 writing activation receipt.') from exc


def pdf_visual_selection_for(root):
    """A v3 marker requires the unchanged and verified v45 -> v46 chain."""
    from code_assist_v1.tools.clean_10521_runtime import is_clean_root, selection_for
    if is_clean_root(root):
        return selection_for(root)
    previous = _v46_selection_for(root)
    path = previous.root / '.runtime' / EVIDENCE_V3_ACTIVATION_NAME
    if not path.exists():
        return previous
    if previous.policy_version != WRITING_V2:
        raise ValueError('Visual PDF v3 requires its unchanged v46 predecessor.')
    from code_assist_v1.tools.preservation_preview_runtime import activation_identity_root
    try:
        raw = path.read_bytes()
        marker = json.loads(raw, object_pairs_hook=_pairs)
        selection = replace(previous, policy_version=WRITING_V3, activation_sha256=sha256(raw).hexdigest())
        expected = dict(port=10421, schema_version=47, schema_sha256=V047_EXPECTED_SCHEMA_SHA256,
            data_root=str(activation_identity_root(selection.root)), predecessor_schema_version=46,
            predecessor_schema_sha256=V046_EXPECTED_SCHEMA_SHA256,
            predecessor_activation_sha256=previous.activation_sha256,
            workflow_version=VERSION, handler_version=HANDLER_VERSION, writing_policy_version=WRITING_V3,
            policy_sha256=_hash(selection.upload_policy().encoded()),
            image_input_capability=IMAGE_INPUT_CAPABILITY, semantic_review_performed=False,
            python_executable=selection.python_executable, registry_sha256=selection.registry_sha256,
            provider=selection.provider, model_id=selection.model_id, wire_model=selection.wire_model,
            codex_reasoning_effort=selection.codex_reasoning_effort)
        if _json(marker) != _json(expected):
            raise ValueError
        return selection
    except (ValueError, TypeError, OSError, KeyError, UnicodeError) as exc:
        raise ValueError('Invalid visual PDF v3 evidence activation receipt.') from exc


def catalog_with_pdf_visual_activation(root, predecessor_catalog):
    from code_assist_v1.tools import preservation_preview_runtime as previous
    root = Path(root).resolve(strict=True)
    if not previous._is_registered_root(root):
        raise ValueError("Invalid visual PDF activation root.")
    if not (root / ".runtime" / ACTIVATION_NAME).exists():
        if any((root / '.runtime' / name).exists() for name in (WRITING_V2_ACTIVATION_NAME, EVIDENCE_V3_ACTIVATION_NAME)):
            raise ValueError('Visual PDF writing requires its unchanged v45 predecessor.')
        return predecessor_catalog
    if predecessor_catalog != PDF_PUBLICATION_MIGRATION_CATALOG:
        raise ValueError("Visual PDF requires its validated v41 predecessor.")
    selection = pdf_visual_selection_for(root)
    return {WRITING_V1: PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG,
        WRITING_V2: PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG,
        WRITING_V3: PDF_EVIDENCE_UNITS_MIGRATION_CATALOG}[selection.policy_version]


def resolved_visual_config(selection):
    """Use the actual internal runner's resolver, including enabled/provider gates."""
    from code_assist_v1 import models, internal_llm
    if type(selection) is not PdfVisualRuntimeSelection or pdf_visual_selection_for(selection.root) != selection:
        raise ValueError("Visual PDF activation changed during execution.")
    from code_assist_v1.tools.clean_10521_runtime import (
        ACTIVE_REGISTRY_NAME, MODEL_SWITCH_ACTIVATION_NAME, is_clean_root,
    )
    registry_name = (ACTIVE_REGISTRY_NAME
        if is_clean_root(selection.root)
        and (selection.root / ".runtime" / MODEL_SWITCH_ACTIVATION_NAME).is_file()
        else "api_config.json")
    if Path(models.API_CONFIG_PATH).resolve(strict=True) != (selection.root / ".runtime" / registry_name).resolve(strict=True):
        raise ValueError("Visual PDF worker loaded a different model registry.")
    candidates = internal_llm._candidate_configs(selection.provider, (selection.model_id,))
    if len(candidates) != 1 or candidates[0][0] != selection.model_id:
        raise ValueError("The activated visual model is unavailable in this worker.")
    cfg = candidates[0][1]
    expected = dict(provider=selection.provider, model_id=selection.model_id,
        wire_model=selection.wire_model, codex_reasoning_effort=selection.codex_reasoning_effort)
    if _model_identity(selection.model_id, cfg) != expected or cfg.get("_registry_id") != selection.model_id:
        raise ValueError("The resolved visual model differs from activation.")
    return cfg


def _config_digest(cfg):
    # In-memory comparison only; never log secrets or store the resolved cfg.
    def serializable(value):
        if type(value) in (set, frozenset):
            return sorted(value)
        raise ValueError("Unsupported visual model configuration value.")
    return _hash(json.dumps(cfg, sort_keys=True, allow_nan=False, default=serializable))


def visual_runner_for(selection, *, execute_models):
    from code_assist_v1 import internal_llm
    from code_assist_v1.codex_response import CodexResponsePolicy
    from code_assist_v1.llm_response import ChatResponsePolicy
    from code_assist_v1.knowledge_core.pdf_visual_authoring import TASK
    if type(execute_models) is not bool:
        raise TypeError("Visual execution choice must be boolean.")
    config_digest = _config_digest(resolved_visual_config(selection))
    policy = selection.upload_policy()

    def runner(task, payload, **kwargs):
        if not execute_models:
            raise RuntimeError("Authoring execution is reserved for the durable worker.")
        providers, ids = kwargs.get("provider_order"), kwargs.get("model_ids")
        budget = kwargs.get("request_budget_bytes")
        transport_ceiling = policy.model.request_budget_bytes
        expected_task = TASK
        if "_pdf_continuation_transport" in kwargs:
            from code_assist_v1.knowledge_core.pdf_visual_continuation import _continuation_transport_budget, TASK as CONTINUATION_TASK
            permit = kwargs.pop("_pdf_continuation_transport")
            if (not selection.forward_continuation or selection.policy_version != WRITING_V36
                    or selection.provider != "hynix_api" or "_pdf_forward_transport" in kwargs):
                raise ValueError("Continuation transport requires its isolated V97 API runtime owner.")
            transport_ceiling = _continuation_transport_budget(permit, payload, kwargs)
            expected_task = CONTINUATION_TASK
        if '_pdf_forward_transport' in kwargs:
            from code_assist_v1.knowledge_core.pdf_visual_authoring import _admitted_forward_transport_budget
            permit = kwargs.pop('_pdf_forward_transport')
            if selection.policy_version != WRITING_V36:
                raise ValueError('Forward transport requires its ordinary V36 runtime owner.')
            transport_ceiling = _admitted_forward_transport_budget(permit,payload,kwargs)
        if (task != expected_task or type(providers) not in (list, tuple) or tuple(providers) != (selection.provider,)
                or type(ids) not in (list, tuple) or tuple(ids) != (selection.model_id,)
                or type(budget) is not int or not 0 < budget <= transport_ceiling
                or type(kwargs.get("max_attempts")) is not int or kwargs["max_attempts"] != 1):
            raise ValueError("Visual call must retain its single frozen model and budget.")
        response_key = "codex_response_policy" if selection.provider == "codex" else "response_policy"
        response = kwargs.get(response_key)
        expected = (CodexResponsePolicy(budget, 4_194_304, 1_048_576, 524_288, 4096, 65536)
            if selection.provider == "codex" else ChatResponsePolicy(1_048_576, 65536))
        if (type(response) is not type(expected) or response != expected
                or any(key in kwargs for key in {"codex_response_policy", "response_policy", "api_response_policy"} - {response_key})):
            raise ValueError("Visual call response policy differs from its frozen plan.")
        guard = kwargs.get("attempt_guard")
        section_writing = selection.policy_version in (WRITING_V5,WRITING_V6,WRITING_V7,WRITING_V8,WRITING_V9,WRITING_V10,WRITING_V11,WRITING_V12,WRITING_V13,WRITING_V14,WRITING_V15,WRITING_V16,WRITING_V17,WRITING_V18,WRITING_V19,WRITING_V20,WRITING_V21,WRITING_V22,WRITING_V23,WRITING_V24,WRITING_V25, WRITING_V26, WRITING_V27, WRITING_V28, WRITING_V29, WRITING_V30, WRITING_V31, WRITING_V32)
        images = kwargs.get("image_inputs",())
        if selection.policy_version in (WRITING_V33, WRITING_V34, WRITING_V35, WRITING_V36):
            section_writing = True
        if selection.policy_version in (WRITING_V34, WRITING_V35, WRITING_V36) and (
                type(kwargs.get('max_tokens')) is not int or kwargs['max_tokens'] != BATCH_OUTPUT_TOKENS):
            raise ValueError('Chart-context writing retains the exact V10 output reservation.')
        image_count = len(images) if type(images) is tuple else -1
        image_contract = (0 <= image_count <= 2 and
            ((image_count == 0 and not any(k in kwargs for k in ("image_inputs","image_policy","image_attempt_receipt")))
             or (image_count > 0 and type(kwargs.get("image_attempt_receipt")) is dict
                and kwargs.get("image_policy") == policy.render_policy.image_policy(image_count))))
        if (not callable(guard) or not image_contract
                or (not section_writing and image_count != 1)
                or kwargs.get("preserve_codex_input") is not True
                or kwargs.get("capture_request_receipt") is not True):
            raise ValueError("Visual call requires its image, receipt and current invocation guard.")
        expected_format = (section_response_format(payload) if selection.policy_version == WRITING_V7 else
            {"type":"json_object"} if selection.policy_version == WRITING_V6 else None) if selection.provider == "hynix_api" else None
        if selection.policy_version in (WRITING_V12,WRITING_V13) and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V14 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v3 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V16 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v5 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V20 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v9 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V22 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v11 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V23 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v12 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V32 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v21 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V31 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v20 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V30 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v19 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V29 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v18 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V28 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v17 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V27 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v16 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V26 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v15 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V25 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v14 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V24 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v13 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V21 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v10 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V19 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v8 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V18 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v7 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V17 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v6 import response_format
            expected_format=response_format(payload)
        if selection.policy_version == WRITING_V15 and selection.provider=='hynix_api':
            from code_assist_v1.knowledge_core.pdf_layout_retention_v4 import response_format
            expected_format=response_format(payload)
        if kwargs.get("response_format") != expected_format:
            raise ValueError("Visual output format differs from the frozen policy.")

        def guarded():
            guard()
            if _config_digest(resolved_visual_config(selection)) != config_digest:
                raise ValueError("Visual model configuration changed during execution.")

        guarded()
        kwargs["attempt_guard"] = guarded
        # Keep exact image bytes, response policy, messages and mutable attempt
        # receipt; do not truncate or substitute the historical 64 KiB adapter.
        return internal_llm.run_internal_text(task, payload, **kwargs)
    return runner


