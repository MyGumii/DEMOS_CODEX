"""Explicit, externally pinned startup binding for a recovered preview.

This is not a migrator or an automatic root registration mechanism. The operator
must supply both the binding file and its separately retained SHA256. Historical
activation files stay byte-for-byte intact; their source identity is validated
while mutable stores are bound exclusively to the recovered destination.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat

PATH_ENV = "CODE_ASSIST_RECOVERY_BINDING"
SHA_ENV = "CODE_ASSIST_RECOVERY_BINDING_SHA256"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_REQUIRED = frozenset((
    "preservation-v20-enabled.json", "authoring-only-v21-enabled.json",
    "structured-authoring-v22-enabled.json", "full-label-authoring-v23-enabled.json",
    "native-publication-v31-enabled.json", "pptx-role-publication-v37-enabled.json",
    "legacy-authoring-retention-v38-enabled.json",
))
_OPTIONAL = frozenset(("native-display-publication-v34-enabled.json",))
_PDF_ACTIVATION = "pdf-publication-v41-enabled.json"
_PDF_VISUAL_ACTIVATION = "pdf-visual-publication-v45-enabled.json"
_PDF_VISUAL_WRITING_ACTIVATION = "pdf-visual-writing-v46-enabled.json"
_PDF_EVIDENCE_ACTIVATION = "pdf-evidence-v47-enabled.json"
_NATIVE_TARGETS_ACTIVATION = "native-semantic-targets-v48-enabled.json"
_RENAME_RESEED_ACTIVATION = "semantic-rename-reseed-v49-enabled.json"
_CLEAN_V49_ACTIVATION = "clean-second-brain-10521-v49-enabled.json"
_PROVENANCE_ACTIVATION = "clean-second-brain-10521-v50-enabled.json"
_VISUAL_BINDING_ACTIVATION = "clean-second-brain-10521-v51-enabled.json"
_TEMPORAL_REBINDING_ACTIVATION = "clean-second-brain-10521-v52-enabled.json"
_OFFICE_HYBRID_ACTIVATION = "clean-second-brain-10521-v58-enabled.json"
_PERSONAL_WRITER_ACTIVATION = "clean-second-brain-10521-v59-enabled.json"
_COMPANY_POLICY_ACTIVATION = "clean-second-brain-10521-v60-enabled.json"
_PDF_OWNED_PROMPT_ACTIVATION = "clean-second-brain-10521-v61-enabled.json"
_PDF_FENCED_RESPONSE_ACTIVATION = "clean-second-brain-10521-v62-enabled.json"
_PDF_SECTION_WRITING_ACTIVATION = "clean-second-brain-10521-v63-enabled.json"
_PDF_SECTION_JSON_ACTIVATION = "clean-second-brain-10521-v64-enabled.json"
_PDF_SECTION_GUARD_ACTIVATION = "clean-second-brain-10521-v65-enabled.json"
_PDF_SECTION_SCHEMA_ACTIVATION = "clean-second-brain-10521-v66-enabled.json"
_PDF_SECTION_MARKDOWN_ACTIVATION = "clean-second-brain-10521-v67-enabled.json"
_PDF_SECTION_DELIMITERS_ACTIVATION = "clean-second-brain-10521-v68-enabled.json"
_PDF_BATCH_MARKDOWN_ACTIVATION = "clean-second-brain-10521-v69-enabled.json"
_PDF_SOURCE_FAITHFUL_ACTIVATION = "clean-second-brain-10521-v70-enabled.json"
_PDF_LAYOUT_RETENTION_ACTIVATION = "clean-second-brain-10521-v71-enabled.json"
_PDF_LAYOUT_MATH_FORMAT_ACTIVATION = "clean-second-brain-10521-v72-enabled.json"
_PDF_LAYOUT_SUBJECT_BINDING_ACTIVATION = "clean-second-brain-10521-v73-enabled.json"
_PDF_LAYOUT_NONBLANK_GROUPS_ACTIVATION = "clean-second-brain-10521-v74-enabled.json"
_PDF_LAYOUT_LITERAL_APPENDIX_ACTIVATION = "clean-second-brain-10521-v75-enabled.json"
_PDF_FIGURE_ROI_ACTIVATION = "clean-second-brain-10521-v76-enabled.json"
_PDF_CURVE_DOMAIN_ACTIVATION = "clean-second-brain-10521-v77-enabled.json"
_PDF_NONPLOT_WRITING_ACTIVATION = "clean-second-brain-10521-v78-enabled.json"
_PDF_NONTHINKING_WRITING_ACTIVATION = "clean-second-brain-10521-v79-enabled.json"
_PDF_CURVE_ARRAY_GUIDANCE_ACTIVATION = "clean-second-brain-10521-v80-enabled.json"
_PDF_TEXT_OUTPUT_HEADROOM_ACTIVATION = "clean-second-brain-10521-v81-enabled.json"
_PDF_FINITE_TEXT_WRITING_ACTIVATION = "clean-second-brain-10521-v82-enabled.json"
_PDF_BOUNDED_TEXT_PARTITION_ACTIVATION = "clean-second-brain-10521-v83-enabled.json"
_PDF_CURVE_NAMED_BOUNDS_ACTIVATION = "clean-second-brain-10521-v84-enabled.json"
_PDF_CURVE_PROMPT_ALIGNMENT_ACTIVATION = "clean-second-brain-10521-v85-enabled.json"
_PDF_VISUAL_CONTRACT_ALIGNMENT_ACTIVATION = "clean-second-brain-10521-v86-enabled.json"
_PDF_TEXT_JSON_CONTRACT_ALIGNMENT_ACTIVATION = "clean-second-brain-10521-v87-enabled.json"
_PDF_NATIVE_FRAME_CONTRACT_ACTIVATION = "clean-second-brain-10521-v88-enabled.json"
_PDF_COMPLETE_SOURCE_PACKING_ACTIVATION = "clean-second-brain-10521-v89-enabled.json"
_PDF_NATIVE_CATEGORICAL_AXES_ACTIVATION = "clean-second-brain-10521-v90-enabled.json"
_PDF_NATIVE_TICK_CONDITIONS_ACTIVATION = "clean-second-brain-10521-v91-enabled.json"
_PDF_V10_LITERAL_TABLES_ACTIVATION = "clean-second-brain-10521-v92-enabled.json"
_PDF_V10_CHART_TABLES_ACTIVATION = "clean-second-brain-10521-v93-enabled.json"
_PDF_CHART_LABELS_ACTIVATION = "clean-second-brain-10521-v94-enabled.json"
_PDF_NUMERIC_APPEND_ACTIVATION = "clean-second-brain-10521-v95-enabled.json"
_PDF_ATOMIC_PARTITION_ACTIVATION = "clean-second-brain-10541-v96-enabled.json"
_MODEL_SWITCH_ACTIVATION = "clean-second-brain-10521-model-switch-v1-enabled.json"
_CLEAN_RECOVERY_KIND = "second-brain-recovered-clean-10521"
_FIELDS = frozenset(("schema_version", "kind", "data_root", "source_data_root",
    "code_root", "source_marker_sha256", "activation_sha256", "api_config_sha256",
    "auth_secret_sha256", "schema_catalog_checksum", "port", "provider_mode"))
_FORWARD_FIELDS = _FIELDS | frozenset(("source_binding_document", "source_binding_sha256",
    "source_schema_catalog_checksum", "activation_data_root", "current_marker_sha256"))
_A_ORIGIN_NAME = "wiki-generation-ab-A-10541-20261011-r1"
_V96_MARKER_FIELDS = frozenset(("kind", "port", "data_root", "source_data_root", "schema_version",
    "schema_sha256", "catalog_checksum_sha256", "source_schema_catalog_checksum", "source_binding_sha256",
    "predecessor_activation_sha256", "pdf_writing_policy_version", "forward_partition", "pdf_prompt_sha256",
    "pdf_upload_policy_sha256", "pdf_upload_configuration", "python_executable", "registry_sha256",
    "provider", "model_id", "wire_model", "codex_reasoning_effort", "pdf_context_window_tokens",
    "pdf_output_token_reservation", "semantic_review_performed", "pdf_chart_numeric_accuracy_verified"))


class RecoveryBindingError(ValueError):
    """The supplied recovery binding is missing, changed, or inconsistent."""


def _digest(value):
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise RecoveryBindingError("Invalid recovery binding digest.")
    return value


def _read(path, maximum=65_536):
    """Read a small regular input; never follow a final symlink/reparse point."""
    try:
        before = path.stat(follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or getattr(before, "st_file_attributes", 0) & 0x400
                or path.resolve(strict=True) != path.absolute()
                or not 0 < before.st_size <= maximum):
            raise RecoveryBindingError("Invalid recovery binding input file.")
        raw = path.read_bytes()
        after = path.stat(follow_symlinks=False)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or len(raw) != before.st_size):
            raise RecoveryBindingError("Recovery binding input changed while read.")
        return raw
    except OSError as exc:
        raise RecoveryBindingError("Recovery binding input is unavailable.") from exc


def _absolute(value):
    if type(value) is not str or not Path(value).is_absolute():
        raise RecoveryBindingError("Recovery paths must be absolute.")
    # Do not resolve/stat the former source: its disk may no longer exist.
    path = Path(os.path.abspath(value))
    if path == Path(path.anchor):
        raise RecoveryBindingError("A recovery path cannot be a filesystem root.")
    return path


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryBindingError("Duplicate recovery binding field.")
        result[key] = value
    return result


@dataclass(frozen=True)
class RecoveryBinding:
    data_root: Path
    source_data_root: Path
    source_marker_sha256: str
    document_sha256: str
    port: int
    provider_mode: str
    kind: str = "second-brain-recovered-preview"
    current_marker_sha256: str | None = None
    current_schema_catalog_checksum: str | None = None
    source_binding_sha256: str | None = None
    activation_data_root: Path | None = None
    partition_marker_sha256: str | None = None
    previous_binding_sha256: str | None = None
    continuation_marker_sha256: str | None = None
    predecessor_binding_sha256: str | None = None


def _forward_source_view(value):
    """Validate a new operator document without relabeling its immutable V95 origin."""
    if type(value) is dict and value.get("schema_version") == 4:
        from code_assist_v1.tools.pdf_terminal_result_reuse_activation import terminal_result_reuse_source_view
        return terminal_result_reuse_source_view(value, previous_source_view=_forward_source_view)
    if type(value) is dict and value.get("schema_version") == 3:
        from code_assist_v1.tools.pdf_context_resume_activation import context_source_view
        return context_source_view(value, legacy_forward_view=_forward_source_view)
    from code_assist_v1.knowledge_core.migrations.pdf_numeric_append_catalog import PDF_NUMERIC_APPEND_MIGRATION_CATALOG as old
    from code_assist_v1.knowledge_core.migrations.pdf_atomic_request_partition_catalog import PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG as current
    if (type(value) is not dict or set(value) != _FORWARD_FIELDS
            or type(value["schema_version"]) is not int or value["schema_version"] != 2
            or value["kind"] != _CLEAN_RECOVERY_KIND or value["port"] != 10541
            or type(value["port"]) is not int or value["provider_mode"] != "openai-compatible"
            or value["schema_catalog_checksum"] != current.checksum_sha256
            or value["source_schema_catalog_checksum"] != old.checksum_sha256
            or type(value["source_binding_document"]) is not str):
        raise RecoveryBindingError("Invalid isolated V96 operator binding.")
    raw = value["source_binding_document"].encode("utf-8")
    if not 0 < len(raw) <= 65_536 or sha256(raw).hexdigest() != _digest(value["source_binding_sha256"]):
        raise RecoveryBindingError("The immutable V95 source binding differs from its pin.")
    source = json.loads(raw, object_pairs_hook=_unique_object)
    if (type(source) is not dict or set(source) != _FIELDS
            or type(source["schema_version"]) is not int or source["schema_version"] != 1
            or source["kind"] != _CLEAN_RECOVERY_KIND or type(source["port"]) is not int
            or source["port"] != 10541 or source["provider_mode"] != "openai-compatible"
            or source["schema_catalog_checksum"] != old.checksum_sha256):
        raise RecoveryBindingError("V96 requires the exact isolated V95 source contract.")
    origin = _absolute(value["activation_data_root"])
    old_code = _absolute(source["code_root"])
    if (origin != _absolute(source["data_root"]) or origin.parent.name != "code"
            or origin.name != "data-root" or origin.parent.parent.name != _A_ORIGIN_NAME
            or old_code != origin.parent / "code_assist_v1"):
        raise RecoveryBindingError("V96 is restricted to the A10541 activation origin.")
    for name in ("source_data_root", "source_marker_sha256", "api_config_sha256", "auth_secret_sha256"):
        if value[name] != source[name]:
            raise RecoveryBindingError("V96 changed an immutable V95 provenance field.")
    hashes, old_hashes = value["activation_sha256"], source["activation_sha256"]
    if (type(hashes) is not dict or type(old_hashes) is not dict
            or set(hashes) != set(old_hashes) | {_PDF_ATOMIC_PARTITION_ACTIVATION}
            or any(hashes[name] != digest for name, digest in old_hashes.items())
            or hashes[_PDF_ATOMIC_PARTITION_ACTIVATION] != _digest(value["current_marker_sha256"])):
        raise RecoveryBindingError("V96 must append, not rewrite, the V95 activation inventory.")
    # All original semantic checks below still run against the V95 provenance.
    # Only explicitly operator-bound current paths are rebased after a restore.
    return {**source, "data_root": value["data_root"], "code_root": value["code_root"]}


def _validate_forward_marker(root, value):
    if type(value) is dict and value.get("schema_version") == 4:
        from code_assist_v1.tools.pdf_terminal_result_reuse_activation import validate_terminal_result_reuse_marker
        validate_terminal_result_reuse_marker(root, value, validate_previous=_validate_forward_marker)
        return
    if type(value) is dict and value.get("schema_version") == 3:
        from code_assist_v1.tools.pdf_context_resume_activation import validate_context_marker
        validate_context_marker(root, value, validate_previous=_validate_forward_marker)
        return
    from code_assist_v1.knowledge_core.migrations.pdf_atomic_request_partition_catalog import PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG as catalog
    raw = _read(Path(root) / ".runtime" / _PDF_ATOMIC_PARTITION_ACTIVATION)
    if sha256(raw).hexdigest() != value["current_marker_sha256"]:
        raise RecoveryBindingError("The V96 current activation changed.")
    try:
        marker = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RecoveryBindingError("Invalid V96 current activation JSON.") from exc
    if (type(marker) is not dict or set(marker) != _V96_MARKER_FIELDS
            or marker.get("kind") != "second-brain-clean-10541-v96"
            or type(marker.get("schema_version")) is not int or marker["schema_version"] != 96
            or marker.get("port") != 10541 or type(marker.get("port")) is not int
            or marker.get("data_root") != value["activation_data_root"]
            or marker.get("source_data_root") != value["source_data_root"]
            or marker.get("catalog_checksum_sha256") != catalog.checksum_sha256
            or marker.get("schema_sha256") != catalog.migration(96).expected_schema_sha256
            or marker.get("predecessor_activation_sha256") != value["source_marker_sha256"]
            or marker.get("source_binding_sha256") != value["source_binding_sha256"]
            or marker.get("source_schema_catalog_checksum") != value["source_schema_catalog_checksum"]
            or marker.get("forward_partition") is not True
            or type(marker.get("pdf_context_window_tokens")) is not int or marker["pdf_context_window_tokens"] != 16_384
            or type(marker.get("pdf_output_token_reservation")) is not int or marker["pdf_output_token_reservation"] != 4096
            or marker.get("semantic_review_performed") is not False
            or marker.get("pdf_chart_numeric_accuracy_verified") is not False):
        raise RecoveryBindingError("The current V96 activation differs from its provenance.")


def forward_10541_binding_document(root, *, source_binding_path, source_binding_sha256):
    """Pure operator constructor; never write artifacts, migrate, or start services."""
    raw = _read(_absolute(str(source_binding_path)))
    if sha256(raw).hexdigest() != _digest(source_binding_sha256):
        raise RecoveryBindingError("The source operator binding differs from its retained pin.")
    source = json.loads(raw, object_pairs_hook=_unique_object)
    selected = Path(root).resolve(strict=True)
    verified = load_binding(selected, environ={PATH_ENV: str(source_binding_path), SHA_ENV: source_binding_sha256})
    if verified is None or verified.current_marker_sha256 is not None:
        raise RecoveryBindingError("The forward operator constructor needs an original V95 binding.")
    from code_assist_v1.knowledge_core.migrations.pdf_atomic_request_partition_catalog import PDF_ATOMIC_REQUEST_PARTITION_MIGRATION_CATALOG as catalog
    marker_sha = sha256(_read(selected / ".runtime" / _PDF_ATOMIC_PARTITION_ACTIVATION)).hexdigest()
    value = {**source, "schema_version": 2, "schema_catalog_checksum": catalog.checksum_sha256,
        "source_schema_catalog_checksum": source["schema_catalog_checksum"],
        "source_binding_document": raw.decode("utf-8"), "source_binding_sha256": source_binding_sha256,
        "activation_data_root": str(selected), "current_marker_sha256": marker_sha,
        "activation_sha256": {**source["activation_sha256"], _PDF_ATOMIC_PARTITION_ACTIVATION: marker_sha}}
    _forward_source_view(value)
    _validate_forward_marker(selected, value)
    return value


def load_binding(root, *, environ=None):
    """Return a verified explicit binding, or None only when neither input exists."""
    env = os.environ if environ is None else environ
    supplied_path, supplied_sha = env.get(PATH_ENV), env.get(SHA_ENV)
    if supplied_path is None and supplied_sha is None:
        return None
    path = _absolute(supplied_path)
    expected_sha = _digest(supplied_sha)
    raw = _read(path)
    if sha256(raw).hexdigest() != expected_sha:
        raise RecoveryBindingError("Recovery binding does not match its external digest.")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RecoveryBindingError("Recovery binding is not valid JSON.") from exc
    forward = value if type(value) is dict and value.get("schema_version") in (2, 3, 4) else None
    if forward is not None:
        try:
            value = _forward_source_view(forward)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RecoveryBindingError("Invalid V96 source binding JSON.") from exc
    if (type(value) is not dict or set(value) != _FIELDS
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] not in {"second-brain-recovered-preview", _CLEAN_RECOVERY_KIND}):
        raise RecoveryBindingError("Invalid recovery binding schema.")
    clean = value["kind"] == _CLEAN_RECOVERY_KIND
    selected = Path(root).resolve(strict=True)
    target = _absolute(value["data_root"])
    source = _absolute(value["source_data_root"])
    if (target != selected or target.resolve(strict=True) != target
            or target == source or target.is_relative_to(source) or source.is_relative_to(target)
            or path.resolve(strict=True).is_relative_to(target)):
        raise RecoveryBindingError("Recovery binding paths do not identify an independent target.")
    if _absolute(value["code_root"]).resolve(strict=True) != Path(__file__).resolve().parents[1]:
        raise RecoveryBindingError("Recovery binding does not match the loaded code root.")
    if (type(value["port"]) is not int or not 1 <= value["port"] <= 65535
            or value["port"] in {10011, 10111, 10221, 10321, 10421}
            or value["provider_mode"] not in ("openai-compatible", "codex-validation")):
        raise RecoveryBindingError("Invalid isolated recovery endpoint/provider.")
    from code_assist_v1.knowledge_core.migrations.legacy_authoring_retention_catalog import (
        LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG,
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
    from code_assist_v1.knowledge_core.migrations.pdf_fenced_response_snapshot_catalog import PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.pdf_section_writing_catalog import PDF_SECTION_WRITING_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.pdf_section_json_output_catalog import PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.pdf_section_guard_dispatch_catalog import PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.pdf_section_schema_output_catalog import PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.pdf_section_markdown_output_catalog import PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.pdf_section_markdown_delimiters_catalog import PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.pdf_batch_markdown_catalog import PDF_BATCH_MARKDOWN_MIGRATION_CATALOG
    from code_assist_v1.knowledge_core.migrations.pdf_source_faithful_markdown_catalog import PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG
    required_by_catalog = {
        LEGACY_AUTHORING_RETENTION_MIGRATION_CATALOG.checksum_sha256: _REQUIRED,
        PDF_PUBLICATION_MIGRATION_CATALOG.checksum_sha256: _REQUIRED | {_PDF_ACTIVATION},
        PDF_VISUAL_PUBLICATION_MIGRATION_CATALOG.checksum_sha256: _REQUIRED | {_PDF_ACTIVATION, _PDF_VISUAL_ACTIVATION},
        PDF_VISUAL_WRITING_POLICY_MIGRATION_CATALOG.checksum_sha256: _REQUIRED | {_PDF_ACTIVATION, _PDF_VISUAL_ACTIVATION, _PDF_VISUAL_WRITING_ACTIVATION},
        PDF_EVIDENCE_UNITS_MIGRATION_CATALOG.checksum_sha256: _REQUIRED | {_PDF_ACTIVATION, _PDF_VISUAL_ACTIVATION, _PDF_VISUAL_WRITING_ACTIVATION, _PDF_EVIDENCE_ACTIVATION},
        NATIVE_SEMANTIC_TARGETS_MIGRATION_CATALOG.checksum_sha256: _REQUIRED | {_PDF_ACTIVATION, _PDF_VISUAL_ACTIVATION, _PDF_VISUAL_WRITING_ACTIVATION, _PDF_EVIDENCE_ACTIVATION, _NATIVE_TARGETS_ACTIVATION},
        SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG.checksum_sha256: _REQUIRED | {_PDF_ACTIVATION, _PDF_VISUAL_ACTIVATION, _PDF_VISUAL_WRITING_ACTIVATION, _PDF_EVIDENCE_ACTIVATION, _NATIVE_TARGETS_ACTIVATION, _RENAME_RESEED_ACTIVATION},
    }
    checksum = value["schema_catalog_checksum"]
    clean_catalogs = {
        PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG.checksum_sha256: (
            {_CLEAN_V49_ACTIVATION, _PROVENANCE_ACTIVATION}, _PROVENANCE_ACTIVATION),
        PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG.checksum_sha256: (
            {_CLEAN_V49_ACTIVATION, _PROVENANCE_ACTIVATION, _VISUAL_BINDING_ACTIVATION},
            _VISUAL_BINDING_ACTIVATION),
        TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG.checksum_sha256: (
            {_CLEAN_V49_ACTIVATION, _PROVENANCE_ACTIVATION,
             _VISUAL_BINDING_ACTIVATION, _TEMPORAL_REBINDING_ACTIVATION},
            _TEMPORAL_REBINDING_ACTIVATION),
        OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG.checksum_sha256: (
            {_CLEAN_V49_ACTIVATION, _PROVENANCE_ACTIVATION,
             _VISUAL_BINDING_ACTIVATION, _TEMPORAL_REBINDING_ACTIVATION,
             _OFFICE_HYBRID_ACTIVATION},
            _OFFICE_HYBRID_ACTIVATION),
        OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG.checksum_sha256: (
            {_CLEAN_V49_ACTIVATION, _PROVENANCE_ACTIVATION,
             _VISUAL_BINDING_ACTIVATION, _TEMPORAL_REBINDING_ACTIVATION,
             _OFFICE_HYBRID_ACTIVATION, _PERSONAL_WRITER_ACTIVATION},
            _PERSONAL_WRITER_ACTIVATION),
        COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG.checksum_sha256: (
            {_CLEAN_V49_ACTIVATION, _PROVENANCE_ACTIVATION,
             _VISUAL_BINDING_ACTIVATION, _TEMPORAL_REBINDING_ACTIVATION,
             _OFFICE_HYBRID_ACTIVATION, _PERSONAL_WRITER_ACTIVATION,
             _COMPANY_POLICY_ACTIVATION},
            _COMPANY_POLICY_ACTIVATION),
        PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG.checksum_sha256: (
            {_CLEAN_V49_ACTIVATION, _PROVENANCE_ACTIVATION,
             _VISUAL_BINDING_ACTIVATION, _TEMPORAL_REBINDING_ACTIVATION,
             _OFFICE_HYBRID_ACTIVATION, _PERSONAL_WRITER_ACTIVATION,
             _COMPANY_POLICY_ACTIVATION, _PDF_OWNED_PROMPT_ACTIVATION},
            _PDF_OWNED_PROMPT_ACTIVATION),
        PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG.checksum_sha256: (
            {_CLEAN_V49_ACTIVATION, _PROVENANCE_ACTIVATION,
             _VISUAL_BINDING_ACTIVATION, _TEMPORAL_REBINDING_ACTIVATION,
             _OFFICE_HYBRID_ACTIVATION, _PERSONAL_WRITER_ACTIVATION,
             _COMPANY_POLICY_ACTIVATION, _PDF_OWNED_PROMPT_ACTIVATION,
             _MODEL_SWITCH_ACTIVATION, _PDF_FENCED_RESPONSE_ACTIVATION},
            _PDF_FENCED_RESPONSE_ACTIVATION),
        PDF_SECTION_WRITING_MIGRATION_CATALOG.checksum_sha256: (
            {_CLEAN_V49_ACTIVATION, _PROVENANCE_ACTIVATION,
             _VISUAL_BINDING_ACTIVATION, _TEMPORAL_REBINDING_ACTIVATION,
             _OFFICE_HYBRID_ACTIVATION, _PERSONAL_WRITER_ACTIVATION,
             _COMPANY_POLICY_ACTIVATION, _PDF_OWNED_PROMPT_ACTIVATION,
             _MODEL_SWITCH_ACTIVATION, _PDF_FENCED_RESPONSE_ACTIVATION,
             _PDF_SECTION_WRITING_ACTIVATION}, _PDF_SECTION_WRITING_ACTIVATION),
    }
    clean_catalogs[PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_SECTION_WRITING_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_SECTION_JSON_ACTIVATION},
        _PDF_SECTION_JSON_ACTIVATION)
    clean_catalogs[PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_SECTION_GUARD_ACTIVATION},
        _PDF_SECTION_GUARD_ACTIVATION)
    clean_catalogs[PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_SECTION_SCHEMA_ACTIVATION},
        _PDF_SECTION_SCHEMA_ACTIVATION)
    clean_catalogs[PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_SECTION_MARKDOWN_ACTIVATION},
        _PDF_SECTION_MARKDOWN_ACTIVATION)
    clean_catalogs[PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_SECTION_DELIMITERS_ACTIVATION},
        _PDF_SECTION_DELIMITERS_ACTIVATION)
    clean_catalogs[PDF_BATCH_MARKDOWN_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_BATCH_MARKDOWN_ACTIVATION},
        _PDF_BATCH_MARKDOWN_ACTIVATION)
    clean_catalogs[PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_BATCH_MARKDOWN_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_SOURCE_FAITHFUL_ACTIVATION},
        _PDF_SOURCE_FAITHFUL_ACTIVATION)
    # The active v69 writer may move directly to the layout lane. The schema
    # includes inert v70 guards, but no v70 writer activation is fabricated.
    from code_assist_v1.knowledge_core.migrations.pdf_layout_retention_catalog import PDF_LAYOUT_RETENTION_MIGRATION_CATALOG
    clean_catalogs[PDF_LAYOUT_RETENTION_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_BATCH_MARKDOWN_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_LAYOUT_RETENTION_ACTIVATION},
        _PDF_LAYOUT_RETENTION_ACTIVATION)
    from code_assist_v1.knowledge_core.migrations.pdf_layout_math_format_catalog import PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG
    clean_catalogs[PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_LAYOUT_RETENTION_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_LAYOUT_MATH_FORMAT_ACTIVATION},
        _PDF_LAYOUT_MATH_FORMAT_ACTIVATION)
    from code_assist_v1.knowledge_core.migrations.pdf_layout_subject_binding_catalog import PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG
    clean_catalogs[PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_LAYOUT_SUBJECT_BINDING_ACTIVATION},
        _PDF_LAYOUT_SUBJECT_BINDING_ACTIVATION)
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
    clean_catalogs[PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_LAYOUT_NONBLANK_GROUPS_ACTIVATION},
        _PDF_LAYOUT_NONBLANK_GROUPS_ACTIVATION)
    clean_catalogs[PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_LAYOUT_LITERAL_APPENDIX_ACTIVATION},
        _PDF_LAYOUT_LITERAL_APPENDIX_ACTIVATION)
    clean_catalogs[PDF_FIGURE_ROI_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_FIGURE_ROI_ACTIVATION},
        _PDF_FIGURE_ROI_ACTIVATION)
    clean_catalogs[PDF_CURVE_DOMAIN_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_FIGURE_ROI_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_CURVE_DOMAIN_ACTIVATION},
        _PDF_CURVE_DOMAIN_ACTIVATION)
    clean_catalogs[PDF_NONPLOT_WRITING_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_CURVE_DOMAIN_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_NONPLOT_WRITING_ACTIVATION},
        _PDF_NONPLOT_WRITING_ACTIVATION)
    clean_catalogs[PDF_NONTHINKING_WRITING_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_NONPLOT_WRITING_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_NONTHINKING_WRITING_ACTIVATION},
        _PDF_NONTHINKING_WRITING_ACTIVATION)
    clean_catalogs[PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_NONTHINKING_WRITING_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_CURVE_ARRAY_GUIDANCE_ACTIVATION},
        _PDF_CURVE_ARRAY_GUIDANCE_ACTIVATION)
    clean_catalogs[PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_TEXT_OUTPUT_HEADROOM_ACTIVATION},
        _PDF_TEXT_OUTPUT_HEADROOM_ACTIVATION)
    clean_catalogs[PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_FINITE_TEXT_WRITING_ACTIVATION},
        _PDF_FINITE_TEXT_WRITING_ACTIVATION)
    clean_catalogs[PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_BOUNDED_TEXT_PARTITION_ACTIVATION},
        _PDF_BOUNDED_TEXT_PARTITION_ACTIVATION)
    clean_catalogs[PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_CURVE_NAMED_BOUNDS_ACTIVATION},
        _PDF_CURVE_NAMED_BOUNDS_ACTIVATION)
    clean_catalogs[PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_CURVE_PROMPT_ALIGNMENT_ACTIVATION},
        _PDF_CURVE_PROMPT_ALIGNMENT_ACTIVATION)
    clean_catalogs[PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_VISUAL_CONTRACT_ALIGNMENT_ACTIVATION},
        _PDF_VISUAL_CONTRACT_ALIGNMENT_ACTIVATION)
    clean_catalogs[PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_TEXT_JSON_CONTRACT_ALIGNMENT_ACTIVATION},
        _PDF_TEXT_JSON_CONTRACT_ALIGNMENT_ACTIVATION)
    clean_catalogs[PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_NATIVE_FRAME_CONTRACT_ACTIVATION},
        _PDF_NATIVE_FRAME_CONTRACT_ACTIVATION)
    clean_catalogs[PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_COMPLETE_SOURCE_PACKING_ACTIVATION},
        _PDF_COMPLETE_SOURCE_PACKING_ACTIVATION)
    clean_catalogs[PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_NATIVE_CATEGORICAL_AXES_ACTIVATION},
        _PDF_NATIVE_CATEGORICAL_AXES_ACTIVATION)
    clean_catalogs[PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_NATIVE_TICK_CONDITIONS_ACTIVATION},
        _PDF_NATIVE_TICK_CONDITIONS_ACTIVATION)
    clean_catalogs[PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_V10_LITERAL_TABLES_ACTIVATION},
        _PDF_V10_LITERAL_TABLES_ACTIVATION)
    clean_catalogs[PDF_V10_CHART_TABLES_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_V10_CHART_TABLES_ACTIVATION},
        _PDF_V10_CHART_TABLES_ACTIVATION)
    clean_catalogs[PDF_CHART_LABELS_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_V10_CHART_TABLES_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_CHART_LABELS_ACTIVATION},
        _PDF_CHART_LABELS_ACTIVATION)
    clean_catalogs[PDF_NUMERIC_APPEND_MIGRATION_CATALOG.checksum_sha256] = (
        clean_catalogs[PDF_CHART_LABELS_MIGRATION_CATALOG.checksum_sha256][0] | {_PDF_NUMERIC_APPEND_ACTIVATION},
        _PDF_NUMERIC_APPEND_ACTIVATION)
    if type(checksum) is not str or (checksum not in clean_catalogs
                                      if clean else checksum not in required_by_catalog):
        raise RecoveryBindingError("Recovery binding catalog differs from the loaded code.")
    # Only the new layout lane may occupy the separate loopback validation
    # endpoint, and only in the independent code/data layout made by a full
    # service restore. The ordinary clean root and every historical catalog
    # retain their exact 10521 port contract.
    code_container = Path(__file__).resolve().parents[2]
    isolated_layout_port = (clean
        and checksum in (PDF_LAYOUT_RETENTION_MIGRATION_CATALOG.checksum_sha256,
            PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG.checksum_sha256,
            PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG.checksum_sha256,
            PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG.checksum_sha256,
            PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG.checksum_sha256,
            PDF_FIGURE_ROI_MIGRATION_CATALOG.checksum_sha256,
            PDF_CURVE_DOMAIN_MIGRATION_CATALOG.checksum_sha256,
            PDF_NONPLOT_WRITING_MIGRATION_CATALOG.checksum_sha256,
            PDF_NONTHINKING_WRITING_MIGRATION_CATALOG.checksum_sha256,
            PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG.checksum_sha256, PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG.checksum_sha256, PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG.checksum_sha256, PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG.checksum_sha256, PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG.checksum_sha256, PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG.checksum_sha256, PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.checksum_sha256, PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.checksum_sha256, PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG.checksum_sha256, PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG.checksum_sha256, PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG.checksum_sha256)
        and value["port"] == 10531 and code_container.name == "code"
        and target == code_container / "data-root")
    isolated_layout_port = isolated_layout_port or (clean
        and checksum == PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG.checksum_sha256
        and value["port"] == 10531 and code_container.name == "code"
        and target == code_container / "data-root")
    isolated_layout_port = isolated_layout_port or (clean
        and checksum == PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG.checksum_sha256
        and value["port"] == 10531 and code_container.name == "code"
        and target == code_container / "data-root")
    isolated_layout_port = isolated_layout_port or (clean
        and checksum == PDF_V10_CHART_TABLES_MIGRATION_CATALOG.checksum_sha256
        and value["port"] == 10531 and code_container.name == "code"
        and target == code_container / "data-root")
    isolated_layout_port = isolated_layout_port or (clean
        and checksum == PDF_CHART_LABELS_MIGRATION_CATALOG.checksum_sha256
        and value["port"] == 10531 and code_container.name == "code"
        and target == code_container / "data-root")
    isolated_layout_port = isolated_layout_port or (clean
        and checksum == PDF_NUMERIC_APPEND_MIGRATION_CATALOG.checksum_sha256
        and (value["port"] == 10531 or (value["port"] == 10541
            and code_container.parent.name == 'wiki-generation-ab-A-10541-20261011-r1'))
        and code_container.name == "code"
        and target == code_container / "data-root")
    isolated_layout_port = isolated_layout_port or (forward is not None and clean
        and value["port"] == 10541 and code_container.name == "code"
        and target == code_container / "data-root"
        and code_container.parent.name != "wiki-generation-ab-B-10551-20261011-r1")
    if ((clean or checksum == SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG.checksum_sha256)
            and value["port"] != 10521 and not isolated_layout_port):
        raise RecoveryBindingError("The v49 preview is bound to port 10521.")
    required = clean_catalogs[checksum][0] if clean else required_by_catalog[checksum]
    optional = ({_MODEL_SWITCH_ACTIVATION}
                if clean and checksum == PDF_OWNED_EVIDENCE_PROMPT_MIGRATION_CATALOG.checksum_sha256
                else set())
    hashes = value["activation_sha256"]
    if (type(hashes) is not dict or not required <= set(hashes)
            or set(hashes) - (required | optional if clean else required | _OPTIONAL)):
        raise RecoveryBindingError("Recovery activation inventory is incomplete.")
    latest_marker = (clean_catalogs[checksum][1]
                     if clean and checksum in {PDF_FENCED_RESPONSE_SNAPSHOT_MIGRATION_CATALOG.checksum_sha256,
                         PDF_SECTION_WRITING_MIGRATION_CATALOG.checksum_sha256,
                         PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG.checksum_sha256,
                         PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG.checksum_sha256,
                         PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG.checksum_sha256,
                         PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG.checksum_sha256,
                         PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG.checksum_sha256,
                         PDF_BATCH_MARKDOWN_MIGRATION_CATALOG.checksum_sha256,
                         PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG.checksum_sha256,
                         PDF_LAYOUT_RETENTION_MIGRATION_CATALOG.checksum_sha256,
                         PDF_LAYOUT_MATH_FORMAT_MIGRATION_CATALOG.checksum_sha256,
                         PDF_LAYOUT_SUBJECT_BINDING_MIGRATION_CATALOG.checksum_sha256,
                         PDF_LAYOUT_NONBLANK_GROUPS_MIGRATION_CATALOG.checksum_sha256,
                         PDF_LAYOUT_LITERAL_APPENDIX_MIGRATION_CATALOG.checksum_sha256,
                         PDF_FIGURE_ROI_MIGRATION_CATALOG.checksum_sha256,
                         PDF_CURVE_DOMAIN_MIGRATION_CATALOG.checksum_sha256,
                         PDF_NONPLOT_WRITING_MIGRATION_CATALOG.checksum_sha256,
                         PDF_NONTHINKING_WRITING_MIGRATION_CATALOG.checksum_sha256,
                         PDF_CURVE_ARRAY_GUIDANCE_MIGRATION_CATALOG.checksum_sha256, PDF_TEXT_OUTPUT_HEADROOM_MIGRATION_CATALOG.checksum_sha256, PDF_FINITE_TEXT_WRITING_MIGRATION_CATALOG.checksum_sha256, PDF_BOUNDED_TEXT_PARTITION_MIGRATION_CATALOG.checksum_sha256, PDF_CURVE_NAMED_BOUNDS_MIGRATION_CATALOG.checksum_sha256, PDF_CURVE_PROMPT_ALIGNMENT_MIGRATION_CATALOG.checksum_sha256, PDF_VISUAL_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.checksum_sha256, PDF_TEXT_JSON_CONTRACT_ALIGNMENT_MIGRATION_CATALOG.checksum_sha256, PDF_NATIVE_FRAME_CONTRACT_MIGRATION_CATALOG.checksum_sha256, PDF_COMPLETE_SOURCE_PACKING_MIGRATION_CATALOG.checksum_sha256, PDF_NATIVE_CATEGORICAL_AXES_MIGRATION_CATALOG.checksum_sha256}
                     else _MODEL_SWITCH_ACTIVATION if clean and _MODEL_SWITCH_ACTIVATION in hashes
                     else clean_catalogs[checksum][1] if clean else None)
    if clean and checksum == PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG.checksum_sha256:
        latest_marker = _PDF_V10_LITERAL_TABLES_ACTIVATION
    if clean and checksum == PDF_V10_CHART_TABLES_MIGRATION_CATALOG.checksum_sha256:
        latest_marker = _PDF_V10_CHART_TABLES_ACTIVATION
    if clean and checksum == PDF_CHART_LABELS_MIGRATION_CATALOG.checksum_sha256:
        latest_marker = _PDF_CHART_LABELS_ACTIVATION
    if clean and checksum == PDF_NUMERIC_APPEND_MIGRATION_CATALOG.checksum_sha256:
        latest_marker = _PDF_NUMERIC_APPEND_ACTIVATION
    runtime = target / ".runtime"
    if (_PDF_NUMERIC_APPEND_ACTIVATION in hashes) != (runtime / _PDF_NUMERIC_APPEND_ACTIVATION).exists():
        raise RecoveryBindingError("The v95 activation inventory changed.")
    if (_PDF_CHART_LABELS_ACTIVATION in hashes) != (runtime / _PDF_CHART_LABELS_ACTIVATION).exists():
        raise RecoveryBindingError("The v94 activation inventory changed.")
    if clean and value["source_marker_sha256"] != hashes[latest_marker]:
        raise RecoveryBindingError("The clean source marker differs from the pinned activation.")
    if (_PDF_V10_CHART_TABLES_ACTIVATION in hashes) != (runtime / _PDF_V10_CHART_TABLES_ACTIVATION).exists():
        raise RecoveryBindingError("Recovery chart-table activation inventory changed.")
    if (_PDF_V10_LITERAL_TABLES_ACTIVATION in hashes) != (runtime / _PDF_V10_LITERAL_TABLES_ACTIVATION).exists():
        raise RecoveryBindingError("Recovery literal-table activation inventory changed.")
    if (_PDF_NATIVE_TICK_CONDITIONS_ACTIVATION in hashes) != (runtime / _PDF_NATIVE_TICK_CONDITIONS_ACTIVATION).exists():
        raise RecoveryBindingError("Recovery tick-condition activation inventory changed.")
    previous_hashes = set(hashes) - {_PDF_NATIVE_TICK_CONDITIONS_ACTIVATION, _PDF_V10_LITERAL_TABLES_ACTIVATION,
        _PDF_V10_CHART_TABLES_ACTIVATION, _PDF_CHART_LABELS_ACTIVATION, _PDF_NUMERIC_APPEND_ACTIVATION}
    if {name for name in _REQUIRED | _OPTIONAL | {_PDF_ACTIVATION, _PDF_VISUAL_ACTIVATION, _PDF_VISUAL_WRITING_ACTIVATION, _PDF_EVIDENCE_ACTIVATION, _NATIVE_TARGETS_ACTIVATION, _RENAME_RESEED_ACTIVATION, _CLEAN_V49_ACTIVATION, _PROVENANCE_ACTIVATION, _VISUAL_BINDING_ACTIVATION, _TEMPORAL_REBINDING_ACTIVATION, _OFFICE_HYBRID_ACTIVATION, _PERSONAL_WRITER_ACTIVATION, _COMPANY_POLICY_ACTIVATION, _PDF_OWNED_PROMPT_ACTIVATION, _MODEL_SWITCH_ACTIVATION, _PDF_FENCED_RESPONSE_ACTIVATION, _PDF_SECTION_WRITING_ACTIVATION, _PDF_SECTION_JSON_ACTIVATION, _PDF_SECTION_GUARD_ACTIVATION, _PDF_SECTION_SCHEMA_ACTIVATION, _PDF_SECTION_MARKDOWN_ACTIVATION, _PDF_SECTION_DELIMITERS_ACTIVATION, _PDF_BATCH_MARKDOWN_ACTIVATION, _PDF_SOURCE_FAITHFUL_ACTIVATION, _PDF_LAYOUT_RETENTION_ACTIVATION, _PDF_LAYOUT_MATH_FORMAT_ACTIVATION, _PDF_LAYOUT_SUBJECT_BINDING_ACTIVATION, _PDF_LAYOUT_NONBLANK_GROUPS_ACTIVATION, _PDF_LAYOUT_LITERAL_APPENDIX_ACTIVATION, _PDF_FIGURE_ROI_ACTIVATION, _PDF_CURVE_DOMAIN_ACTIVATION, _PDF_NONPLOT_WRITING_ACTIVATION, _PDF_NONTHINKING_WRITING_ACTIVATION, _PDF_CURVE_ARRAY_GUIDANCE_ACTIVATION, _PDF_TEXT_OUTPUT_HEADROOM_ACTIVATION, _PDF_FINITE_TEXT_WRITING_ACTIVATION, _PDF_BOUNDED_TEXT_PARTITION_ACTIVATION, _PDF_CURVE_NAMED_BOUNDS_ACTIVATION, _PDF_CURVE_PROMPT_ALIGNMENT_ACTIVATION, _PDF_VISUAL_CONTRACT_ALIGNMENT_ACTIVATION, _PDF_TEXT_JSON_CONTRACT_ALIGNMENT_ACTIVATION, _PDF_NATIVE_FRAME_CONTRACT_ACTIVATION, _PDF_COMPLETE_SOURCE_PACKING_ACTIVATION, _PDF_NATIVE_CATEGORICAL_AXES_ACTIVATION} if (runtime / name).exists()} != previous_hashes:
        raise RecoveryBindingError("Recovery activation inventory changed.")
    for name, digest in hashes.items():
        if sha256(_read(runtime / name)).hexdigest() != _digest(digest):
            raise RecoveryBindingError("Recovery activation file changed.")
    for name, key in (("api_config.json", "api_config_sha256"), ("secret.key", "auth_secret_sha256")):
        if sha256(_read(runtime / name, 1_048_576)).hexdigest() != _digest(value[key]):
            raise RecoveryBindingError("Recovery configuration or secret changed.")
    _read(runtime / "users.json", 8_388_608)  # must exist; never recreate accounts
    if forward is not None:
        _validate_forward_marker(target, forward)
    partition_marker = None
    previous_document = None
    continuation_marker = None
    predecessor_document = None
    if forward is not None and forward['schema_version'] == 3:
        from code_assist_v1.tools.pdf_context_resume_activation import previous_binding
        partition_marker = previous_binding(forward)['current_marker_sha256']
        previous_document = forward["previous_binding_sha256"]
    elif forward is not None and forward['schema_version'] == 4:
        from code_assist_v1.tools.pdf_terminal_result_reuse_activation import previous_binding as previous_continuation_binding
        from code_assist_v1.tools.pdf_context_resume_activation import previous_binding as previous_partition_binding
        continuation = previous_continuation_binding(forward)
        partition_marker = previous_partition_binding(continuation)['current_marker_sha256']
        # The old V97 receipt consumes this historical V96 document pin.
        # Keep the immediate schema3 predecessor in a separate new field.
        previous_document = continuation["previous_binding_sha256"]
        continuation_marker = continuation["current_marker_sha256"]
        predecessor_document = forward["previous_binding_sha256"]
    return RecoveryBinding(target, source, _digest(value["source_marker_sha256"]),
        expected_sha, value["port"], value["provider_mode"], value["kind"],
        current_marker_sha256=None if forward is None else forward["current_marker_sha256"],
        current_schema_catalog_checksum=None if forward is None else forward["schema_catalog_checksum"],
        source_binding_sha256=None if forward is None else forward["source_binding_sha256"],
        activation_data_root=None if forward is None else _absolute(forward["activation_data_root"]),
        partition_marker_sha256=partition_marker,
        previous_binding_sha256=previous_document,
        continuation_marker_sha256=continuation_marker,
        predecessor_binding_sha256=predecessor_document)


