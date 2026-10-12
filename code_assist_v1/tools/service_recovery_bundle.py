"""Offline service recovery bundle: code, private supplementary data, DB and CAS.

Requires the operator to stop the source service first. This directory bundle
contains credentials and is not encrypted; keep it in protected local storage.
It never stops/starts services, changes passwords, migrates, or overwrites a
destination. Runtime binaries, external environment secrets and model weights
are deployment dependencies, not silently collected from the host.
"""
from dataclasses import asdict
from contextlib import closing
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
from uuid import uuid4

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.backup_storage import _archive_root
from code_assist_v1.knowledge_core.migrations import MigrationStorageLayout
from code_assist_v1.knowledge_core.migrations.legacy_authoring_retention_catalog import (
    LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG as CATALOG,
)
from code_assist_v1.knowledge_core.migrations.pdf_publication_catalog import PDF_PUBLICATION_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_visual_publication_catalog import PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_visual_writing_policy_catalog import PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_evidence_units_catalog import PDF_EVIDENCE_UNITS_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.native_semantic_targets_catalog import NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.semantic_rename_reseed_catalog import SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.protected_extraction_provenance_catalog import PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.protected_pdf_visual_binding_catalog import PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.temporal_root_rebinding_catalog import TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.office_hybrid_publication_body_catalog import OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.office_personal_writer_part_catalog import OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.company_policy_provenance_catalog import COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_owned_evidence_prompt_catalog import PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG
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
from code_assist_v1.knowledge_core.migrations.pdf_v10_chart_tables_catalog import PDF_V10_CHART_TABLES_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_chart_labels_catalog import PDF_CHART_LABELS_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_numeric_append_catalog import PDF_NUMERIC_APPEND_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_atomic_request_partition_catalog import PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG
from code_assist_v1.knowledge_core.migrations.pdf_context_resume_catalog import PDF_CONTEXT_RESUME_MIGRATION_CATALOG
from code_assist_v1.tools.pdf_context_resume_activation import ACTIVATION_NAME as _PDF_CONTEXT_RESUME_ACTIVATION
from code_assist_v1.knowledge_core.migrations.pdf_terminal_result_reuse_catalog import PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG
from code_assist_v1.tools.pdf_terminal_result_reuse_activation import ACTIVATION_NAME as _PDF_TERMINAL_RESULT_REUSE_ACTIVATION
from code_assist_v1.knowledge_core.services.backup import BackupService, BackupServiceConfiguration
from code_assist_v1.knowledge_core.services.restore import RestoreDrillService
from code_assist_v1.tools.legacy_inventory import _extended_windows_path, _display_windows_path
from code_assist_v1.tools.windows_volume import is_syntactic_unc_path, probe_path
from code_assist_v1.tools.recovered_preview_binding import _REQUIRED, _OPTIONAL, _PDF_ACTIVATION, _PDF_VISUAL_ACTIVATION, _PDF_VISUAL_WRITING_ACTIVATION, _PDF_EVIDENCE_ACTIVATION, _NATIVE_TARGETS_ACTIVATION, _RENAME_RESEED_ACTIVATION, _CLEAN_V49_ACTIVATION, _PROVENANCE_ACTIVATION, _VISUAL_BINDING_ACTIVATION, _TEMPORAL_REBINDING_ACTIVATION, _OFFICE_HYBRID_ACTIVATION, _PERSONAL_WRITER_ACTIVATION, _COMPANY_POLICY_ACTIVATION, _PDF_OWNED_PROMPT_ACTIVATION, _CLEAN_RECOVERY_KIND, _read
from code_assist_v1.tools.recovered_preview_binding import (
    _MODEL_SWITCH_ACTIVATION, _PDF_FENCED_RESPONSE_ACTIVATION,
    _PDF_SECTION_WRITING_ACTIVATION, _PDF_SECTION_JSON_ACTIVATION,
    _PDF_SECTION_GUARD_ACTIVATION, _PDF_SECTION_SCHEMA_ACTIVATION,
    _PDF_SECTION_MARKDOWN_ACTIVATION, _PDF_SECTION_DELIMITERS_ACTIVATION,
    _PDF_BATCH_MARKDOWN_ACTIVATION, _PDF_SOURCE_FAITHFUL_ACTIVATION,
    _PDF_LAYOUT_RETENTION_ACTIVATION,
    _PDF_LAYOUT_MATH_FORMAT_ACTIVATION,
    _PDF_LAYOUT_SUBJECT_BINDING_ACTIVATION,
    _PDF_LAYOUT_NONBLANK_GROUPS_ACTIVATION,
    _PDF_LAYOUT_LITERAL_APPENDIX_ACTIVATION,
    _PDF_FIGURE_ROI_ACTIVATION,
    _PDF_CURVE_DOMAIN_ACTIVATION,
    _PDF_NONPLOT_WRITING_ACTIVATION,
    _PDF_NONTHINKING_WRITING_ACTIVATION,
    _PDF_CURVE_ARRAY_GUIDANCE_ACTIVATION,
    _PDF_TEXT_OUTPUT_HEADROOM_ACTIVATION,
    _PDF_FINITE_TEXT_WRITING_ACTIVATION,
    _PDF_BOUNDED_TEXT_PARTITION_ACTIVATION,
    _PDF_CURVE_NAMED_BOUNDS_ACTIVATION,
    _PDF_CURVE_PROMPT_ALIGNMENT_ACTIVATION,
    _PDF_VISUAL_CONTRACT_ALIGNMENT_ACTIVATION,
    _PDF_TEXT_JSON_CONTRACT_ALIGNMENT_ACTIVATION,
    _PDF_NATIVE_FRAME_CONTRACT_ACTIVATION,
    _PDF_COMPLETE_SOURCE_PACKING_ACTIVATION,
    _PDF_NATIVE_CATEGORICAL_AXES_ACTIVATION,
    _PDF_NATIVE_TICK_CONDITIONS_ACTIVATION,
    _PDF_V10_LITERAL_TABLES_ACTIVATION,
    _PDF_V10_CHART_TABLES_ACTIVATION,
    _PDF_CHART_LABELS_ACTIVATION,
    _PDF_NUMERIC_APPEND_ACTIVATION,
    _PDF_ATOMIC_PARTITION_ACTIVATION,
)

MANIFEST = "service-manifest.json"
CODE_DIRECTORIES = frozenset(("agent_integrations", "collaboration_runtime", "docs", "images",
    "integrations", "knowledge_core", "notion_collaboration", "notion_static", "ops", "scripts",
    "skills", "static", "tools", "validation"))
_DATA_OWNERS = frozenset(("core", "blobs", "projections"))
_CONFIG_REQUIRED = frozenset((".runtime/users.json", ".runtime/secret.key", ".runtime/api_config.json",
    ".runtime/full-second-brain-preview.json", *(".runtime/" + name for name in _REQUIRED)))
_CODE_REQUIRED = frozenset(("__init__.py", "app_code.py", "config.py", "auth.py",
    "tools/serve_recovered_second_brain_preview.py", "tools/service_recovery_bundle.py",
    "requirements-runtime.lock"))
_MAX_FILES = 100_000
_MAX_BYTES = 32 * 1024 ** 3
DEFAULT_SCHEMA_CATALOG = "legacy-authoring-retention-v38"
_CATALOGS = {
    DEFAULT_SCHEMA_CATALOG: CATALOG,
    "pdf-publication-v41": PDF_PUBLICATION_MIGRATION_CATALOG,
    "pdf-visual-publication-v45": PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG,
    "pdf-visual-writing-policy-v46": PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG,
    "pdf-evidence-units-v47": PDF_EVIDENCE_UNITS_MIGRATION_CATALOG,
    "native-semantic-targets-v48": NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG,
    "semantic-rename-reseed-v49": SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG,
    "protected-extraction-provenance-v50": PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG,
    "protected-pdf-visual-binding-v51": PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG,
    "temporal-root-rebinding-v52": TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG,
    "office-hybrid-publication-body-v58": OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG,
    "office-personal-writer-part-v59": OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG,
    "company-policy-provenance-v60": COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
    "pdf-owned-evidence-prompt-v61": PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG,
    "pdf-batch-markdown-v69": PDF_BATCH_MARKDOWN_MIGRATION_CATALOG,
    "pdf-source-faithful-markdown-v70": PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG,
    "pdf-layout-retention-v71": PDF_LAYOUT_RETENTION_MIGRATION_CATALOG,
    "pdf-layout-math-format-v72": PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG,
    "pdf-layout-subject-binding-v73": PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG,
    "pdf-layout-nonblank-groups-v74": PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG,
    "pdf-layout-literal-appendix-v75": PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG,
    "pdf-figure-roi-v76": PDF_FIGURE_ROI_MIGRATION_CATALOG,
    "pdf-curve-domain-v77": PDF_CURVE_DOMAIN_MIGRATION_CATALOG,
    "pdf-nonplot-writing-v78": PDF_NONPLOT_WRITING_MIGRATION_CATALOG,
    "pdf-nonthinking-writing-v79": PDF_NONTHINKING_WRITING_MIGRATION_CATALOG,
    "pdf-curve-array-guidance-v80": PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG,
    "pdf-text-output-headroom-v81": PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG,
    "pdf-finite-text-writing-v82": PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG,
    "pdf-bounded-text-partition-v83": PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG,
    "pdf-curve-named-bounds-v84": PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG,
    "pdf-curve-prompt-alignment-v85": PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG,
    "pdf-visual-contract-alignment-v86": PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG,
    "pdf-text-json-contract-alignment-v87": PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG,
    "pdf-native-frame-contract-v88": PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG,
    "pdf-complete-source-packing-v89": PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG,
    "pdf-native-categorical-axes-v90": PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG,
    "pdf-native-tick-conditions-v91": PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG,
    "pdf-v10-literal-tables-v92": PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG,
    "pdf-v10-chart-tables-v93": PDF_V10_CHART_TABLES_MIGRATION_CATALOG,
    "pdf-chart-labels-v94": PDF_CHART_LABELS_MIGRATION_CATALOG,
    "pdf-numeric-append-v95": PDF_NUMERIC_APPEND_MIGRATION_CATALOG,
    "pdf-atomic-request-partition-v96": PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG,
    "pdf-context-resume-v97": PDF_CONTEXT_RESUME_MIGRATION_CATALOG,
    "pdf-terminal-result-reuse-v98": PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG,
}

_CLEAN_CATALOGS = {
    PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG: (
        "python-clean-10521-v50", _PROVENANCE_ACTIVATION),
    PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG: (
        "python-clean-10521-v51", _VISUAL_BINDING_ACTIVATION),
    TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG: (
        "python-clean-10521-v52", _TEMPORAL_REBINDING_ACTIVATION),
    OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG: (
        "python-clean-10521-v58", _OFFICE_HYBRID_ACTIVATION),
    OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG: (
        "python-clean-10521-v59", _PERSONAL_WRITER_ACTIVATION),
    COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG: (
        "python-clean-10521-v60", _COMPANY_POLICY_ACTIVATION),
    PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG: (
        "python-clean-10521-v61", _PDF_OWNED_PROMPT_ACTIVATION),
    PDF_BATCH_MARKDOWN_MIGRATION_CATALOG: (
        "python-clean-10521-v69", _PDF_BATCH_MARKDOWN_ACTIVATION),
    PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG: (
        "python-clean-10521-v70", _PDF_SOURCE_FAITHFUL_ACTIVATION),
    PDF_LAYOUT_RETENTION_MIGRATION_CATALOG: (
        "python-clean-10521-v71", _PDF_LAYOUT_RETENTION_ACTIVATION),
    PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG: (
        "python-clean-10521-v72", _PDF_LAYOUT_MATH_FORMAT_ACTIVATION),
    PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG: (
        "python-clean-10521-v73", _PDF_LAYOUT_SUBJECT_BINDING_ACTIVATION),
    PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG: (
        "python-clean-10521-v74", _PDF_LAYOUT_NONBLANK_GROUPS_ACTIVATION),
    PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG: (
        "python-clean-10521-v75", _PDF_LAYOUT_LITERAL_APPENDIX_ACTIVATION),
    PDF_FIGURE_ROI_MIGRATION_CATALOG: (
        "python-clean-10521-v76", _PDF_FIGURE_ROI_ACTIVATION),
    PDF_CURVE_DOMAIN_MIGRATION_CATALOG: (
        "python-clean-10521-v77", _PDF_CURVE_DOMAIN_ACTIVATION),
    PDF_NONPLOT_WRITING_MIGRATION_CATALOG: (
        "python-clean-10521-v78", _PDF_NONPLOT_WRITING_ACTIVATION),
    PDF_NONTHINKING_WRITING_MIGRATION_CATALOG: (
        "python-clean-10521-v79", _PDF_NONTHINKING_WRITING_ACTIVATION),
    PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG: (
        "python-clean-10521-v80", _PDF_CURVE_ARRAY_GUIDANCE_ACTIVATION),
    PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG: (
        "python-clean-10521-v81", _PDF_TEXT_OUTPUT_HEADROOM_ACTIVATION),
    PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG: (
        "python-clean-10521-v82", _PDF_FINITE_TEXT_WRITING_ACTIVATION),
    PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG: (
        "python-clean-10521-v83", _PDF_BOUNDED_TEXT_PARTITION_ACTIVATION),
    PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG: (
        "python-clean-10521-v84", _PDF_CURVE_NAMED_BOUNDS_ACTIVATION),
    PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG: (
        "python-clean-10521-v85", _PDF_CURVE_PROMPT_ALIGNMENT_ACTIVATION),
    PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG: (
        "python-clean-10521-v86", _PDF_VISUAL_CONTRACT_ALIGNMENT_ACTIVATION),
    PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG: (
        "python-clean-10521-v87", _PDF_TEXT_JSON_CONTRACT_ALIGNMENT_ACTIVATION),
    PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG: (
        "python-clean-10521-v88", _PDF_NATIVE_FRAME_CONTRACT_ACTIVATION),
    PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG: (
        "python-clean-10521-v89", _PDF_COMPLETE_SOURCE_PACKING_ACTIVATION),
    PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG: (
        "python-clean-10521-v90", _PDF_NATIVE_CATEGORICAL_AXES_ACTIVATION),
    PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG: (
        "python-clean-10521-v91", _PDF_NATIVE_TICK_CONDITIONS_ACTIVATION),
    PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG: (
        "python-clean-10521-v92", _PDF_V10_LITERAL_TABLES_ACTIVATION),
    PDF_V10_CHART_TABLES_MIGRATION_CATALOG: (
        "python-clean-10521-v93", _PDF_V10_CHART_TABLES_ACTIVATION),
    PDF_CHART_LABELS_MIGRATION_CATALOG: (
        "python-clean-10521-v94", _PDF_CHART_LABELS_ACTIVATION),
    PDF_NUMERIC_APPEND_MIGRATION_CATALOG: (
        "python-clean-10521-v95", _PDF_NUMERIC_APPEND_ACTIVATION),
    PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG: (
        "python-clean-10541-v96", _PDF_ATOMIC_PARTITION_ACTIVATION),
    PDF_CONTEXT_RESUME_MIGRATION_CATALOG: (
        "python-clean-10541-v97", _PDF_CONTEXT_RESUME_ACTIVATION),
    PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG: (
        "python-clean-10541-v98", _PDF_TERMINAL_RESULT_REUSE_ACTIVATION),
}

# Explicit forward inventory, not marker discovery. The preceding model switch
# and active registry must travel with these snapshots, not be recreated.
_V69_MARKERS = (
    _CLEAN_V49_ACTIVATION, _PROVENANCE_ACTIVATION, _VISUAL_BINDING_ACTIVATION,
    _TEMPORAL_REBINDING_ACTIVATION, _OFFICE_HYBRID_ACTIVATION,
    _PERSONAL_WRITER_ACTIVATION, _COMPANY_POLICY_ACTIVATION,
    _PDF_OWNED_PROMPT_ACTIVATION, _MODEL_SWITCH_ACTIVATION,
    _PDF_FENCED_RESPONSE_ACTIVATION, _PDF_SECTION_WRITING_ACTIVATION,
    _PDF_SECTION_JSON_ACTIVATION, _PDF_SECTION_GUARD_ACTIVATION,
    _PDF_SECTION_SCHEMA_ACTIVATION, _PDF_SECTION_MARKDOWN_ACTIVATION,
    _PDF_SECTION_DELIMITERS_ACTIVATION, _PDF_BATCH_MARKDOWN_ACTIVATION,
)
_FORWARD_CLEAN_MARKERS = {
    PDF_BATCH_MARKDOWN_MIGRATION_CATALOG: _V69_MARKERS,
    PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG: (
        *_V69_MARKERS, _PDF_SOURCE_FAITHFUL_ACTIVATION),
    PDF_LAYOUT_RETENTION_MIGRATION_CATALOG: (
        *_V69_MARKERS, _PDF_LAYOUT_RETENTION_ACTIVATION),
    PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG: (
        *_V69_MARKERS, _PDF_LAYOUT_RETENTION_ACTIVATION, _PDF_LAYOUT_MATH_FORMAT_ACTIVATION),
    PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG: (
        *_V69_MARKERS, _PDF_LAYOUT_RETENTION_ACTIVATION, _PDF_LAYOUT_MATH_FORMAT_ACTIVATION,
        _PDF_LAYOUT_SUBJECT_BINDING_ACTIVATION),
    PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG: (
        *_V69_MARKERS, _PDF_LAYOUT_RETENTION_ACTIVATION, _PDF_LAYOUT_MATH_FORMAT_ACTIVATION,
        _PDF_LAYOUT_SUBJECT_BINDING_ACTIVATION, _PDF_LAYOUT_NONBLANK_GROUPS_ACTIVATION),
    PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG: (
        *_V69_MARKERS, _PDF_LAYOUT_RETENTION_ACTIVATION, _PDF_LAYOUT_MATH_FORMAT_ACTIVATION,
        _PDF_LAYOUT_SUBJECT_BINDING_ACTIVATION, _PDF_LAYOUT_NONBLANK_GROUPS_ACTIVATION,
        _PDF_LAYOUT_LITERAL_APPENDIX_ACTIVATION),
    PDF_FIGURE_ROI_MIGRATION_CATALOG: (
        *_V69_MARKERS, _PDF_LAYOUT_RETENTION_ACTIVATION, _PDF_LAYOUT_MATH_FORMAT_ACTIVATION,
        _PDF_LAYOUT_SUBJECT_BINDING_ACTIVATION, _PDF_LAYOUT_NONBLANK_GROUPS_ACTIVATION,
        _PDF_LAYOUT_LITERAL_APPENDIX_ACTIVATION, _PDF_FIGURE_ROI_ACTIVATION),
    PDF_CURVE_DOMAIN_MIGRATION_CATALOG: (
        *_V69_MARKERS, _PDF_LAYOUT_RETENTION_ACTIVATION, _PDF_LAYOUT_MATH_FORMAT_ACTIVATION,
        _PDF_LAYOUT_SUBJECT_BINDING_ACTIVATION, _PDF_LAYOUT_NONBLANK_GROUPS_ACTIVATION,
        _PDF_LAYOUT_LITERAL_APPENDIX_ACTIVATION, _PDF_FIGURE_ROI_ACTIVATION, _PDF_CURVE_DOMAIN_ACTIVATION),
    PDF_NONPLOT_WRITING_MIGRATION_CATALOG: (
        *_V69_MARKERS, _PDF_LAYOUT_RETENTION_ACTIVATION, _PDF_LAYOUT_MATH_FORMAT_ACTIVATION,
        _PDF_LAYOUT_SUBJECT_BINDING_ACTIVATION, _PDF_LAYOUT_NONBLANK_GROUPS_ACTIVATION,
        _PDF_LAYOUT_LITERAL_APPENDIX_ACTIVATION, _PDF_FIGURE_ROI_ACTIVATION,
        _PDF_CURVE_DOMAIN_ACTIVATION, _PDF_NONPLOT_WRITING_ACTIVATION),
    PDF_NONTHINKING_WRITING_MIGRATION_CATALOG: (
        *_V69_MARKERS, _PDF_LAYOUT_RETENTION_ACTIVATION, _PDF_LAYOUT_MATH_FORMAT_ACTIVATION,
        _PDF_LAYOUT_SUBJECT_BINDING_ACTIVATION, _PDF_LAYOUT_NONBLANK_GROUPS_ACTIVATION,
        _PDF_LAYOUT_LITERAL_APPENDIX_ACTIVATION, _PDF_FIGURE_ROI_ACTIVATION,
        _PDF_CURVE_DOMAIN_ACTIVATION, _PDF_NONPLOT_WRITING_ACTIVATION, _PDF_NONTHINKING_WRITING_ACTIVATION),
    PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG: (
        *_V69_MARKERS, _PDF_LAYOUT_RETENTION_ACTIVATION, _PDF_LAYOUT_MATH_FORMAT_ACTIVATION,
        _PDF_LAYOUT_SUBJECT_BINDING_ACTIVATION, _PDF_LAYOUT_NONBLANK_GROUPS_ACTIVATION,
        _PDF_LAYOUT_LITERAL_APPENDIX_ACTIVATION, _PDF_FIGURE_ROI_ACTIVATION,
        _PDF_CURVE_DOMAIN_ACTIVATION, _PDF_NONPLOT_WRITING_ACTIVATION, _PDF_NONTHINKING_WRITING_ACTIVATION,
        _PDF_CURVE_ARRAY_GUIDANCE_ACTIVATION),
}

_FORWARD_CLEAN_MARKERS[PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG],
    _PDF_TEXT_OUTPUT_HEADROOM_ACTIVATION)
_FORWARD_CLEAN_MARKERS[PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG],
    _PDF_FINITE_TEXT_WRITING_ACTIVATION)
_FORWARD_CLEAN_MARKERS[PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG],
    _PDF_BOUNDED_TEXT_PARTITION_ACTIVATION)
_FORWARD_CLEAN_MARKERS[PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG],
    _PDF_CURVE_NAMED_BOUNDS_ACTIVATION)
_FORWARD_CLEAN_MARKERS[PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG],
    _PDF_CURVE_PROMPT_ALIGNMENT_ACTIVATION)
_FORWARD_CLEAN_MARKERS[PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG],
    _PDF_VISUAL_CONTRACT_ALIGNMENT_ACTIVATION)
_FORWARD_CLEAN_MARKERS[PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG],
    _PDF_TEXT_JSON_CONTRACT_ALIGNMENT_ACTIVATION)
_FORWARD_CLEAN_MARKERS[PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG],
    _PDF_NATIVE_FRAME_CONTRACT_ACTIVATION)
_FORWARD_CLEAN_MARKERS[PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG],
    _PDF_COMPLETE_SOURCE_PACKING_ACTIVATION)
_FORWARD_CLEAN_MARKERS[PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG],
    _PDF_NATIVE_CATEGORICAL_AXES_ACTIVATION)
_FORWARD_CLEAN_MARKERS[PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG],
    _PDF_NATIVE_TICK_CONDITIONS_ACTIVATION)
_FORWARD_CLEAN_MARKERS[PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG],
    _PDF_V10_LITERAL_TABLES_ACTIVATION)
_FORWARD_CLEAN_MARKERS[PDF_V10_CHART_TABLES_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG],
    _PDF_V10_CHART_TABLES_ACTIVATION)
_FORWARD_CLEAN_MARKERS[PDF_CHART_LABELS_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_V10_CHART_TABLES_MIGRATION_CATALOG],
    _PDF_CHART_LABELS_ACTIVATION)
_FORWARD_CLEAN_MARKERS[PDF_NUMERIC_APPEND_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_CHART_LABELS_MIGRATION_CATALOG],
    _PDF_NUMERIC_APPEND_ACTIVATION)
_FORWARD_CLEAN_MARKERS[PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_NUMERIC_APPEND_MIGRATION_CATALOG],
    _PDF_ATOMIC_PARTITION_ACTIVATION)
_FORWARD_CLEAN_MARKERS[PDF_CONTEXT_RESUME_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG],
    _PDF_CONTEXT_RESUME_ACTIVATION)
_FORWARD_CLEAN_MARKERS[PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG] = (
    *_FORWARD_CLEAN_MARKERS[PDF_CONTEXT_RESUME_MIGRATION_CATALOG],
    _PDF_TERMINAL_RESULT_REUSE_ACTIVATION)


class ServiceBundleError(ValueError):
    pass


def _selected_catalog(name):
    # The operator selects a compiled catalog. Neither a DB nor a bundle may
    # opt itself into a newer schema; older callers retain their v38 contract.
    if type(name) is not str or name not in _CATALOGS:
        raise ServiceBundleError("An explicitly supported service schema catalog is required.")
    return _CATALOGS[name]


def _require_configuration_inventory(extra, catalog):
    files = set(extra["files"])
    if catalog in _FORWARD_CLEAN_MARKERS:
        activation_files={".runtime/"+name for name in _FORWARD_CLEAN_MARKERS[catalog]}
        known_markers=(_REQUIRED | _OPTIONAL | set(_V69_MARKERS) | {
            _PDF_ACTIVATION, _PDF_VISUAL_ACTIVATION, _PDF_VISUAL_WRITING_ACTIVATION,
            _PDF_EVIDENCE_ACTIVATION, _NATIVE_TARGETS_ACTIVATION,
            _RENAME_RESEED_ACTIVATION, _PDF_SOURCE_FAITHFUL_ACTIVATION,
            _PDF_LAYOUT_RETENTION_ACTIVATION, _PDF_LAYOUT_MATH_FORMAT_ACTIVATION,
            _PDF_LAYOUT_SUBJECT_BINDING_ACTIVATION, _PDF_LAYOUT_NONBLANK_GROUPS_ACTIVATION,
            _PDF_LAYOUT_LITERAL_APPENDIX_ACTIVATION, _PDF_FIGURE_ROI_ACTIVATION, _PDF_CURVE_DOMAIN_ACTIVATION,
            _PDF_NONPLOT_WRITING_ACTIVATION, _PDF_NONTHINKING_WRITING_ACTIVATION,
            _PDF_CURVE_ARRAY_GUIDANCE_ACTIVATION, _PDF_TEXT_OUTPUT_HEADROOM_ACTIVATION, _PDF_FINITE_TEXT_WRITING_ACTIVATION, _PDF_BOUNDED_TEXT_PARTITION_ACTIVATION, _PDF_CURVE_NAMED_BOUNDS_ACTIVATION, _PDF_CURVE_PROMPT_ALIGNMENT_ACTIVATION, _PDF_VISUAL_CONTRACT_ALIGNMENT_ACTIVATION, _PDF_TEXT_JSON_CONTRACT_ALIGNMENT_ACTIVATION, _PDF_NATIVE_FRAME_CONTRACT_ACTIVATION, _PDF_COMPLETE_SOURCE_PACKING_ACTIVATION, _PDF_NATIVE_CATEGORICAL_AXES_ACTIVATION})
        known_markers = known_markers | {_PDF_NATIVE_TICK_CONDITIONS_ACTIVATION, _PDF_V10_LITERAL_TABLES_ACTIVATION,
            _PDF_V10_CHART_TABLES_ACTIVATION, _PDF_CHART_LABELS_ACTIVATION, _PDF_NUMERIC_APPEND_ACTIVATION,
            _PDF_ATOMIC_PARTITION_ACTIVATION, _PDF_CONTEXT_RESUME_ACTIVATION,
            _PDF_TERMINAL_RESULT_REUSE_ACTIVATION}
        required={".runtime/users.json", ".runtime/secret.key", ".runtime/api_config.json",
            ".runtime/api_config_active.json"} | activation_files
        if (not required <= files
                or files & {".runtime/"+name for name in known_markers} != activation_files):
            raise ServiceBundleError("The forward 10521 activation/active-registry inventory is incomplete or mixed.")
        return
    if catalog in _CLEAN_CATALOGS:
        clean = {".runtime/" + _CLEAN_V49_ACTIVATION,
                 ".runtime/" + _PROVENANCE_ACTIVATION}
        if catalog in {PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG,
                       TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG,
                       OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG,
                       OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG,
                       COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
                       PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG}:
            clean.add(".runtime/" + _VISUAL_BINDING_ACTIVATION)
        if catalog in {TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG,
                       OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG,
                       OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG,
                       COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
                       PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG}:
            clean.add(".runtime/" + _TEMPORAL_REBINDING_ACTIVATION)
        if catalog in {OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG,
                       OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG,
                       COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
                       PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG}:
            clean.add(".runtime/" + _OFFICE_HYBRID_ACTIVATION)
        if catalog in {OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG,
                       COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
                       PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG}:
            clean.add(".runtime/" + _PERSONAL_WRITER_ACTIVATION)
        if catalog in {COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
                       PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG}:
            clean.add(".runtime/" + _COMPANY_POLICY_ACTIVATION)
        if catalog == PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG:
            clean.add(".runtime/" + _PDF_OWNED_PROMPT_ACTIVATION)
        historical = {".runtime/" + name for name in (
            _REQUIRED | _OPTIONAL | {
                _PDF_ACTIVATION, _PDF_VISUAL_ACTIVATION,
                _PDF_VISUAL_WRITING_ACTIVATION, _PDF_EVIDENCE_ACTIVATION,
                _NATIVE_TARGETS_ACTIVATION, _RENAME_RESEED_ACTIVATION,
            })}
        required = {".runtime/users.json", ".runtime/secret.key",
                    ".runtime/api_config.json"} | clean
        if not required <= files or files & (historical | {
                ".runtime/" + _VISUAL_BINDING_ACTIVATION,
                ".runtime/" + _TEMPORAL_REBINDING_ACTIVATION,
                ".runtime/" + _OFFICE_HYBRID_ACTIVATION,
                ".runtime/" + _PERSONAL_WRITER_ACTIVATION,
                ".runtime/" + _COMPANY_POLICY_ACTIVATION,
                ".runtime/" + _PDF_OWNED_PROMPT_ACTIVATION}) - clean:
            raise ServiceBundleError("The clean 10521 activation inventory is incomplete or mixed with historical receipts.")
        return
    pdf_marker = ".runtime/" + _PDF_ACTIVATION
    visual_marker = ".runtime/" + _PDF_VISUAL_ACTIVATION
    writing_marker = ".runtime/" + _PDF_VISUAL_WRITING_ACTIVATION
    evidence_marker = ".runtime/" + _PDF_EVIDENCE_ACTIVATION
    native_marker = ".runtime/" + _NATIVE_TARGETS_ACTIVATION
    rename_marker = ".runtime/" + _RENAME_RESEED_ACTIVATION
    provenance_marker = ".runtime/" + _PROVENANCE_ACTIVATION
    clean_v49_marker = ".runtime/" + _CLEAN_V49_ACTIVATION
    additions = ({pdf_marker, visual_marker, writing_marker, evidence_marker, native_marker, rename_marker} if catalog == SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG
        else {pdf_marker, visual_marker, writing_marker, evidence_marker, native_marker} if catalog == NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG
        else {pdf_marker, visual_marker, writing_marker, evidence_marker} if catalog == PDF_EVIDENCE_UNITS_MIGRATION_CATALOG
        else {pdf_marker, visual_marker, writing_marker} if catalog == PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG
        else {pdf_marker, visual_marker} if catalog == PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG
        else {pdf_marker} if catalog == PDF_PUBLICATION_MIGRATION_CATALOG else set())
    required = _CONFIG_REQUIRED | additions
    if not required <= files or (files & {pdf_marker, visual_marker, writing_marker, evidence_marker, native_marker, rename_marker, clean_v49_marker, provenance_marker, ".runtime/" + _VISUAL_BINDING_ACTIVATION, ".runtime/" + _TEMPORAL_REBINDING_ACTIVATION, ".runtime/" + _OFFICE_HYBRID_ACTIVATION, ".runtime/" + _PERSONAL_WRITER_ACTIVATION, ".runtime/" + _COMPANY_POLICY_ACTIVATION, ".runtime/" + _PDF_OWNED_PROMPT_ACTIVATION}) != additions:
        raise ServiceBundleError("Accounts, secret, API and activation inventory must match the selected catalog.")
    # Activation bytes are archived unchanged. The recovered startup validates
    # their full semantic contract and external parser availability, not here.


def _json(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":")) + "\n").encode("utf-8")


def _recovery_provider(extra_root, catalog, identity_root):
    """Select the archived active lane, without rewriting historical receipts."""
    runtime = extra_root / ".runtime"
    marker = json.loads(_read(runtime / _CLEAN_CATALOGS[catalog][1]))
    provider = marker.get("provider")
    if catalog in _FORWARD_CLEAN_MARKERS:
        from code_assist_v1.tools.clean_10521_runtime import MODEL_SWITCH_KIND
        switch = json.loads(_read(runtime / _MODEL_SWITCH_ACTIVATION))
        if (type(switch) is not dict or switch.get("kind") != MODEL_SWITCH_KIND
                or type(switch.get("schema_version")) is not int
                or switch["schema_version"] != 1 or switch.get("port") != 10521
                or switch.get("data_root") != str(identity_root)
                or switch.get("provider") != "hynix_api"
                or switch.get("registry_sha256") != sha256(_read(
                    runtime / "api_config_active.json", 1_048_576)).hexdigest()
                or switch.get("predecessor_activation_sha256") != sha256(_read(
                    runtime / _PDF_OWNED_PROMPT_ACTIVATION)).hexdigest()):
            raise ServiceBundleError("The archived active model switch differs from its registry or origin.")
        # Later PDF-policy markers intentionally retain their original Codex
        # identity. The verified switch, not those historical fields, selects
        # the live HTTP model. Restored startup revalidates its full contract.
        provider = switch["provider"]
    if provider not in {"codex", "hynix_api"}:
        raise ServiceBundleError("The archived recovery provider is unsupported.")
    return provider


def _absolute(path):
    value = Path(path)
    if not value.is_absolute() or is_syntactic_unc_path(value):
        raise ServiceBundleError("A local absolute path is required.")
    value = Path(os.path.abspath(value))
    if value == Path(value.anchor):
        raise ServiceBundleError("A filesystem root is not a service target.")
    return value


def _regular(path, *, directory=False):
    io_path = _extended_windows_path(path)
    value = io_path.stat(follow_symlinks=False)
    correct = stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode)
    if (not correct or getattr(value, "st_file_attributes", 0) & 0x400
            or (not directory and value.st_nlink != 1)
            or Path(_display_windows_path(io_path.resolve(strict=True))) != path):
        raise ServiceBundleError("Service bundle inputs must be ordinary unaliased files/directories.")
    return value


def _directory(path):
    path = _absolute(path)
    _regular(path, directory=True)
    facts = probe_path(path)
    if facts.get("eligible") is not True or (facts.get("reparse") or {}).get("present") is not False:
        raise ServiceBundleError("Service recovery requires ordinary local NTFS storage.")
    return path


def _overlap(a, b):
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def _new_target(path, forbidden):
    value = _absolute(path)
    _directory(value.parent)
    if os.path.lexists(_extended_windows_path(value)) or any(_overlap(value, p) for p in forbidden):
        raise ServiceBundleError("Use a new disjoint destination; existing targets are never overwritten.")
    return value


def _relative(value):
    if (type(value) is not str or not value or "\\" in value or ":" in value
            or PurePosixPath(value).is_absolute()
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ServiceBundleError("Invalid service bundle relative path.")
    return value


def _code_path(relative, directory):
    parts = PurePosixPath(relative).parts
    if any(part == "__pycache__" or part.startswith(".") for part in parts):
        return False
    if parts[0] in CODE_DIRECTORIES:
        return not relative.endswith((".pyc", ".pyo"))
    return len(parts) == 1 and not directory and (
        relative.endswith((".py", ".md", ".ps1")) or relative == "skills.zip"
        or (relative.startswith("requirements-") and relative.endswith((".lock", ".txt"))))


def _extra_path(relative, directory):
    del directory
    return PurePosixPath(relative).parts[0] not in _DATA_OWNERS


def _fact(path, destination=None):
    before = _regular(path)
    if before.st_size > _MAX_BYTES:
        raise ServiceBundleError("A service file exceeds the bundle size budget.")
    digest, copied = sha256(), 0
    output = None
    try:
        if destination is not None:
            _extended_windows_path(destination.parent).mkdir(parents=True, exist_ok=True)
            output = _extended_windows_path(destination).open("xb")
        with _extended_windows_path(path).open("rb") as source:
            while block := source.read(1024 * 1024):
                copied += len(block)
                if copied > before.st_size:
                    raise ServiceBundleError("A service file changed during copying.")
                digest.update(block)
                if output is not None:
                    output.write(block)
    finally:
        if output is not None:
            output.close()
    after = _regular(path)
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if identity(before) != identity(after) or copied != before.st_size:
        raise ServiceBundleError("A service file changed during copying.")
    return dict(sha256=digest.hexdigest(), size_bytes=copied)


def _tree(root, *, selector=lambda relative, directory: True, destination=None):
    root = _directory(root)
    files, directories, seen, total = {}, [], set(), 0
    def visit(parent):
        nonlocal total
        _regular(parent, directory=True)
        for entry in sorted(_extended_windows_path(parent).iterdir(), key=lambda p: p.name):
            path = parent / entry.name
            value = _extended_windows_path(entry).stat(follow_symlinks=False)
            directory = stat.S_ISDIR(value.st_mode)
            relative = _relative(path.relative_to(root).as_posix())
            if not selector(relative, directory):
                continue
            folded = relative.casefold()
            if folded in seen or len(seen) >= _MAX_FILES:
                raise ServiceBundleError("Duplicate or excessive service bundle entries.")
            seen.add(folded)
            _regular(path, directory=directory)
            if directory:
                directories.append(relative)
                if destination is not None:
                    _extended_windows_path(destination / relative).mkdir(parents=True, exist_ok=False)
                visit(path)
            else:
                fact = _fact(path, None if destination is None else destination / relative)
                total += fact["size_bytes"]
                if total > _MAX_BYTES:
                    raise ServiceBundleError("The service bundle exceeds its size budget.")
                files[relative] = fact
    visit(root)
    return dict(files=files, directories=sorted(directories))


def code_inventory(code_root, *, destination=None):
    result = _tree(code_root, selector=_code_path, destination=destination)
    if not _CODE_REQUIRED <= set(result["files"]):
        raise ServiceBundleError("The deployable code profile is incomplete.")
    return result


def _layout(root):
    return MigrationStorageLayout(data_root=root, database=root / "core/knowledge_core.db",
        blob_root=root / "blobs", projection_root=root / "projections")


def create_bundle(*, code_root, data_root, output, offline_source=False,
        schema_catalog=DEFAULT_SCHEMA_CATALOG):
    catalog = _selected_catalog(schema_catalog)
    if offline_source is not True:
        raise ServiceBundleError("Stop the source service and explicitly acknowledge an offline bundle.")
    code_root, data_root = _directory(code_root), _directory(data_root)
    if _overlap(code_root, data_root):
        raise ServiceBundleError("Code and mutable data must have separate roots.")
    output = _new_target(output, (code_root, data_root))
    # Fail before copying code/private configuration. BackupService remains the
    # authoritative check against the final destination when creating the DB/CAS
    # archive; this preflight preserves its existing production volume policy.
    _, source_volume = _archive_root(data_root)
    _, output_volume = _archive_root(output.parent)
    if source_volume == output_volume and os.environ.get("CODE_ASSIST_TEST_MODE") != "1":
        raise ServiceBundleError("Production backups require a different local NTFS volume.")
    code = code_inventory(code_root)
    extra = _tree(data_root, selector=_extra_path)
    _require_configuration_inventory(extra, catalog)
    activation_identity_root = None
    forward_binding = None
    if catalog in _CLEAN_CATALOGS:
        from code_assist_v1.tools import clean_10521_runtime as clean_runtime
        from code_assist_v1.tools.recovered_preview_binding import load_binding
        if (code_root != Path(clean_runtime.__file__).resolve().parents[1]
                or data_root != clean_runtime.DATA_ROOT.resolve(strict=True)
                or clean_runtime.catalog_for(data_root) != catalog
                or "tools/serve_clean_10521.py" not in code["files"]):
            raise ServiceBundleError("The clean 10521 source code, root or activation differs.")
        recovery = load_binding(data_root)
        activation_identity_root = (recovery.source_data_root
            if recovery is not None else data_root)
        marker = json.loads(_read(data_root / ".runtime" / _CLEAN_CATALOGS[catalog][1]))
        marker_identity_root = activation_identity_root
        if catalog in (PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG, PDF_CONTEXT_RESUME_MIGRATION_CATALOG,
                PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG):
            from code_assist_v1.tools.recovered_preview_binding import PATH_ENV, _forward_source_view, _validate_forward_marker
            if recovery is None or recovery.current_marker_sha256 is None:
                raise ServiceBundleError("V96 backup requires its explicit forward operator binding.")
            forward_binding = _read(Path(os.environ[PATH_ENV])).decode("utf-8")
            if sha256(forward_binding.encode("utf-8")).hexdigest() != recovery.document_sha256:
                raise ServiceBundleError("The V96 operator binding changed during backup admission.")
            forward_value = json.loads(forward_binding, object_pairs_hook=_unique)
            _forward_source_view(forward_value)
            _validate_forward_marker(data_root, forward_value)
            marker_identity_root = recovery.activation_data_root
        if marker.get("data_root") != str(marker_identity_root):
            raise ServiceBundleError("The clean activation origin differs from its verified binding.")
    factory = ConnectionFactory(_layout(data_root).database, catalog)
    with closing(factory.open(read_only=True)):
        pass
    output.mkdir()
    for name in ("code", "supplementary", "data-backups"):
        (output / name).mkdir()
    code_destination = output / "code/code_assist_v1"
    code_destination.mkdir()
    if code_inventory(code_root, destination=code_destination) != code:
        raise ServiceBundleError("The source code changed during backup.")
    if _tree(data_root, selector=_extra_path, destination=output / "supplementary") != extra:
        raise ServiceBundleError("Supplementary source files changed during backup.")
    backup = BackupService(factory, _layout(data_root), backup_root=output / "data-backups",
        configuration=BackupServiceConfiguration(app_version=f"service-bundle-v{catalog.latest_version}",
            build_revision=sha256(_json(code)).hexdigest(), configuration_version="offline-service-v1",
            lease_hmac_key_set_version="preserved-preview-marker-v1")).create(backup_run_id=str(uuid4()))
    if code_inventory(code_root) != code or _tree(data_root, selector=_extra_path) != extra:
        raise ServiceBundleError("Source files changed before bundle completion.")
    inventory = _tree(output)
    manifest = dict(schema_version=1, kind="second-brain-offline-service-bundle",
        code_profile=(_CLEAN_CATALOGS[catalog][0] if catalog in _CLEAN_CATALOGS
                      else "python-app-v1"), source_code_root=str(code_root), source_data_root=str(data_root),
        schema_catalog_checksum=catalog.checksum_sha256, backup=asdict(backup), inventory=inventory,
        contains_private_credentials=True, encrypted=False, source_declared_offline=True)
    if activation_identity_root is not None:
        manifest["activation_identity_root"] = str(activation_identity_root)
    if forward_binding is not None:
        manifest["forward_binding_document"] = forward_binding
        manifest["forward_binding_sha256"] = sha256(forward_binding.encode("utf-8")).hexdigest()
    raw = _json(manifest)
    with (output / MANIFEST).open("xb") as handle:
        handle.write(raw)
    return dict(bundle=str(output), manifest_sha256=sha256(raw).hexdigest(), files=len(inventory["files"]),
        backup_run_id=backup.backup_run_id, complete=True, contains_private_credentials=True)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ServiceBundleError("Duplicate service manifest field.")
        result[key] = value
    return result


def verify_bundle(*, bundle, expected_sha256, schema_catalog=DEFAULT_SCHEMA_CATALOG):
    catalog = _selected_catalog(schema_catalog)
    bundle = _directory(bundle)
    if type(expected_sha256) is not str or len(expected_sha256) != 64:
        raise ServiceBundleError("An externally retained manifest hash is required.")
    raw = _read(bundle / MANIFEST, 32 * 1024 ** 2)
    if sha256(raw).hexdigest() != expected_sha256:
        raise ServiceBundleError("The service manifest differs from its external hash.")
    value = json.loads(raw, object_pairs_hook=_unique)
    fields = {"schema_version", "kind", "code_profile", "source_code_root", "source_data_root",
        "schema_catalog_checksum", "backup", "inventory", "contains_private_credentials", "encrypted",
        "source_declared_offline"}
    accepted_fields = (fields, fields | {"activation_identity_root"}) if catalog in _CLEAN_CATALOGS else (fields,)
    if catalog in (PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG, PDF_CONTEXT_RESUME_MIGRATION_CATALOG,
            PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG):
        accepted_fields = (fields | {"activation_identity_root", "forward_binding_document", "forward_binding_sha256"},)
    if (type(value) is not dict or set(value) not in accepted_fields or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or value["kind"] != "second-brain-offline-service-bundle"
            or value["code_profile"] != (_CLEAN_CATALOGS[catalog][0] if catalog in _CLEAN_CATALOGS else "python-app-v1")
            or value["schema_catalog_checksum"] != catalog.checksum_sha256
            or value["contains_private_credentials"] is not True or value["encrypted"] is not False
            or value["source_declared_offline"] is not True):
        raise ServiceBundleError("Invalid service bundle contract.")
    _absolute(value["source_code_root"])
    _absolute(value["source_data_root"])
    activation_identity_root = _absolute(value.get(
        "activation_identity_root", value["source_data_root"]))
    inventory = _tree(bundle, selector=lambda p, d: p != MANIFEST)
    if inventory != value["inventory"]:
        raise ServiceBundleError("Service bundle file contents or inventory changed.")
    if not {"code", "code/code_assist_v1", "supplementary", "data-backups"} <= set(inventory["directories"]):
        raise ServiceBundleError("Service bundle components are missing.")
    code_root, extra_root = bundle / "code/code_assist_v1", bundle / "supplementary"
    if _tree(code_root) != code_inventory(code_root):
        raise ServiceBundleError("The bundled code contains entries outside its declared profile.")
    extra = _tree(extra_root)
    if _tree(extra_root, selector=_extra_path) != extra:
        raise ServiceBundleError("Service bundle credentials/configuration are incomplete.")
    _require_configuration_inventory(extra, catalog)
    if catalog in _CLEAN_CATALOGS and "tools/serve_clean_10521.py" not in code_inventory(code_root)["files"]:
        raise ServiceBundleError("The clean 10521 host entry point is missing.")
    if catalog in _CLEAN_CATALOGS:
        marker = json.loads(_read(extra_root / ".runtime" / _CLEAN_CATALOGS[catalog][1]))
        marker_identity_root = activation_identity_root
        if catalog in (PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG, PDF_CONTEXT_RESUME_MIGRATION_CATALOG,
                PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG):
            from code_assist_v1.tools.recovered_preview_binding import _forward_source_view, _validate_forward_marker
            document = value["forward_binding_document"]
            if (type(document) is not str or len(document.encode("utf-8")) > 65_536
                    or sha256(document.encode("utf-8")).hexdigest() != value["forward_binding_sha256"]):
                raise ServiceBundleError("The archived V96 operator binding changed.")
            forward = json.loads(document, object_pairs_hook=_unique)
            _forward_source_view(forward)
            _validate_forward_marker(extra_root, forward)
            if (forward["data_root"] != value["source_data_root"]
                    or forward["code_root"] != value["source_code_root"]
                    or forward["source_data_root"] != str(activation_identity_root)):
                raise ServiceBundleError("The archived V96 operator root provenance differs.")
            marker_identity_root = _absolute(forward["activation_data_root"])
        if (type(marker) is not dict
                or marker.get("data_root") != str(marker_identity_root)
                or marker.get("schema_version") != catalog.latest_version
                or marker.get("catalog_checksum_sha256") != catalog.checksum_sha256):
            raise ServiceBundleError("The clean activation origin or schema differs from the bundle.")
        _recovery_provider(extra_root, catalog, activation_identity_root)
    return value


def restore_bundle(*, bundle, expected_sha256, target, incident_at_ms, max_rpo_minutes=60,
        schema_catalog=DEFAULT_SCHEMA_CATALOG):
    catalog = _selected_catalog(schema_catalog)
    bundle = _directory(bundle)
    value = verify_bundle(bundle=bundle, expected_sha256=expected_sha256, schema_catalog=schema_catalog)
    target = _new_target(target, (bundle, _absolute(value["source_code_root"]), _absolute(value["source_data_root"])))
    # The normal data recovery service independently checks DB/CAS/catalog/RPO.
    service = RestoreDrillService.from_backup(backup_root=bundle / "data-backups", migration_catalog=catalog,
        expected_manifest_sha256=value["backup"]["manifest_sha256"])
    target.mkdir()
    code_root = target / "code/code_assist_v1"
    data_root = (target / "code/data-root" if catalog in _CLEAN_CATALOGS
                 else target / "data")
    code_root.mkdir(parents=True)
    for path in (data_root, data_root / "core", data_root / "blobs", data_root / "projections"):
        path.mkdir()
    restored = service.restore(backup_run_id=value["backup"]["backup_run_id"], target_layout=_layout(data_root),
        assumed_incident_at_ms=incident_at_ms, max_rpo_minutes=max_rpo_minutes)
    if not restored.passed:
        raise ServiceBundleError("Data recovery did not pass; the partial target is not runnable.")
    code_inventory(bundle / "code/code_assist_v1", destination=code_root)
    _tree(bundle / "supplementary", destination=data_root)
    verify_bundle(bundle=bundle, expected_sha256=expected_sha256, schema_catalog=schema_catalog)
    if code_inventory(code_root) != code_inventory(bundle / "code/code_assist_v1"):
        raise ServiceBundleError("Restored code differs from the verified bundle.")
    if _tree(data_root, selector=_extra_path) != _tree(bundle / "supplementary"):
        raise ServiceBundleError("Restored supplementary data differs from the verified bundle.")
    receipt = dict(schema_version=1, complete=True, code_root=str(code_root), data_root=str(data_root),
        service_manifest_sha256=expected_sha256, data_restore=asdict(restored),
        started=False, credentials_recreated=False, migrated=False)
    if catalog in _CLEAN_CATALOGS:
        runtime = data_root / ".runtime"
        marker_names = [_CLEAN_V49_ACTIVATION, _PROVENANCE_ACTIVATION]
        if catalog in {PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG,
                       TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG,
                       OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG,
                       OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG,
                       COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
                       PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG}:
            marker_names.append(_VISUAL_BINDING_ACTIVATION)
        if catalog in {TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG,
                       OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG,
                       OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG,
                       COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
                       PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG}:
            marker_names.append(_TEMPORAL_REBINDING_ACTIVATION)
        if catalog in {OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG,
                       OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG,
                       COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
                       PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG}:
            marker_names.append(_OFFICE_HYBRID_ACTIVATION)
        if catalog in {OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG,
                       COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
                       PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG}:
            marker_names.append(_PERSONAL_WRITER_ACTIVATION)
        if catalog in {COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG,
                       PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG}:
            marker_names.append(_COMPANY_POLICY_ACTIVATION)
        if catalog == PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG:
            marker_names.append(_PDF_OWNED_PROMPT_ACTIVATION)
        if catalog in _FORWARD_CLEAN_MARKERS:
            marker_names=list(_FORWARD_CLEAN_MARKERS[catalog])
        markers = {name: sha256(_read(runtime / name)).hexdigest()
                   for name in marker_names}
        latest_marker = _CLEAN_CATALOGS[catalog][1]
        marker = json.loads(_read(runtime / latest_marker))
        activation_identity_root = value.get("activation_identity_root", value["source_data_root"])
        marker_identity_root = (json.loads(value["forward_binding_document"])["activation_data_root"]
            if catalog in (PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG, PDF_CONTEXT_RESUME_MIGRATION_CATALOG,
                PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG) else activation_identity_root)
        if (type(marker) is not dict or marker.get("data_root") != marker_identity_root
                or marker.get("schema_version") != catalog.latest_version
                or marker.get("catalog_checksum_sha256") != catalog.checksum_sha256
                or marker.get("provider") not in {"codex", "hynix_api"}):
            raise ServiceBundleError("The clean source marker does not match the archive.")
        binding = dict(
            schema_version=1, kind=_CLEAN_RECOVERY_KIND,
            data_root=str(data_root), source_data_root=activation_identity_root,
            code_root=str(code_root),
            source_marker_sha256=markers[latest_marker],
            activation_sha256=markers,
            api_config_sha256=sha256(_read(runtime / "api_config.json", 1_048_576)).hexdigest(),
            auth_secret_sha256=sha256(_read(runtime / "secret.key", 1_048_576)).hexdigest(),
            schema_catalog_checksum=catalog.checksum_sha256, port=10521,
            provider_mode=("codex-validation" if _recovery_provider(
                bundle / "supplementary", catalog, activation_identity_root) == "codex"
                           else "openai-compatible"),
        )
        if catalog in (PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG, PDF_CONTEXT_RESUME_MIGRATION_CATALOG,
                PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG):
            # Keep the original source95/current96 origin records immutable;
            # only the new operator-bound mutable roots are rebased.
            from code_assist_v1.tools.recovered_preview_binding import _forward_source_view, _validate_forward_marker
            binding = {**json.loads(value["forward_binding_document"], object_pairs_hook=_unique),
                "data_root": str(data_root), "code_root": str(code_root)}
            _forward_source_view(binding)
            _validate_forward_marker(data_root, binding)
        binding_bytes = _json(binding)
        binding_path = target / "recovery-binding.json"
        with binding_path.open("xb") as handle:
            handle.write(binding_bytes)
        receipt["recovery_binding_path"] = str(binding_path)
        receipt["recovery_binding_sha256"] = sha256(binding_bytes).hexdigest()
    with (target / "restore-receipt.json").open("xb") as handle:
        handle.write(_json(receipt))
    return receipt


def _failure_diagnostics(exc):
    """Stable code locations only; never include exception text or locals."""
    result = []
    root = Path(__file__).resolve().parents[1]
    for _ in range(4):
        if exc is None:
            break
        frames, current = [], exc.__traceback__
        while current is not None:
            path = Path(current.tb_frame.f_code.co_filename)
            if path.is_relative_to(root):
                frames.append(dict(module=path.relative_to(root).as_posix(), line=current.tb_lineno,
                    function=current.tb_frame.f_code.co_name))
            current = current.tb_next
        result.append(dict(exception_type=type(exc).__name__, sites=frames[-6:],
            winerror=getattr(exc, "winerror", None)))
        exc = exc.__cause__ or exc.__context__
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--code-root", required=True, type=Path)
    create.add_argument("--data-root", required=True, type=Path)
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--offline-source", action="store_true")
    create.add_argument("--schema-catalog", choices=tuple(_CATALOGS), default=DEFAULT_SCHEMA_CATALOG)
    for name in ("verify", "restore"):
        sub = commands.add_parser(name)
        sub.add_argument("--bundle", required=True, type=Path)
        sub.add_argument("--expected-sha256", required=True)
        sub.add_argument("--schema-catalog", choices=tuple(_CATALOGS), default=DEFAULT_SCHEMA_CATALOG)
        if name == "restore":
            sub.add_argument("--target", required=True, type=Path)
            sub.add_argument("--incident-at-ms", required=True, type=int)
            sub.add_argument("--max-rpo-minutes", type=int, default=60)
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    try:
        if command == "create":
            result = create_bundle(**args)
        elif command == "restore":
            result = restore_bundle(**args)
        else:
            value = verify_bundle(**args)
            result = dict(verified=True, files=len(value["inventory"]["files"]), contains_private_credentials=True)
        print(_json(dict(status="pass", result=result)).decode(), end="")
        return 0
    except Exception as exc:
        # Do not print file contents, tokens, passwords, configuration or secrets.
        print(_json(dict(status="error", error=dict(code="SERVICE_BUNDLE_FAILED",
            exception_type=type(exc).__name__, diagnostics=_failure_diagnostics(exc),
            partial_target_must_not_be_started=True))).decode(), end="")
        return 2


if __name__ == "__main__":
    sys.exit(main())


