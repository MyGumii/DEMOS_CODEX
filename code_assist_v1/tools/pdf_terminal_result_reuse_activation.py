"""Pure A10541 V98 activation retaining the exact schema3/V97 predecessor.

No migration, database, provider, service, or marker write is performed here.
Schema4 appends one activation and embeds the externally pinned schema3 bytes;
all historical source/credential/provider/partition validation is delegated.
"""
from hashlib import sha256
import json
from pathlib import Path

from code_assist_v1.tools import pdf_context_resume_activation as context

ACTIVATION_NAME = "clean-second-brain-10541-v98-enabled.json"
KIND = "second-brain-clean-10541-v98"
CONTRACT = "pdf-terminal-result-reuse-v1"
CATALOG_SHA256 = "b7209b89f4a588222ec77879458957e8aec79ee3e092cc417b49786c901be7cd"
SCHEMA_SHA256 = "c5199f955b602b34193b6b596a35ef2b11b6a01ef3502ab22f88ef000962ce85"
PREDECESSOR_CATALOG_SHA256 = "e59c8842b139f816c5f166e71f713e9dba818223918c8ea8d55d752f84bc24ff"
_MAX_DOCUMENT_BYTES = 65_536
_PREVIOUS_FIELDS = frozenset({"previous_binding_document", "previous_binding_sha256"})
_MARKER_FIELDS = context._MARKER_FIELDS | frozenset({"terminal_result_reuse_contract"})
_FROZEN_MARKER_FIELDS = (
    "data_root", "source_data_root", "continuation_contract",
    "pdf_upload_policy_sha256", "pdf_context_window_tokens",
    "pdf_output_token_reservation", "additional_llm_review_calls",
    "registry_sha256", "provider", "model_id", "wire_model", "codex_reasoning_effort",
)


def _catalog():
    from code_assist_v1.knowledge_core.migrations.pdf_context_resume_catalog import PDF_CONTEXT_RESUME_MIGRATION_CATALOG as prior
    from code_assist_v1.knowledge_core.migrations.pdf_terminal_result_reuse_catalog import PDF_TERMINAL_RESULT_REUSE_MIGRATION_CATALOG as catalog
    migration = catalog.migration(98)
    if (prior.checksum_sha256 != PREDECESSOR_CATALOG_SHA256
            or catalog.checksum_sha256 != CATALOG_SHA256
            or catalog.latest_version != 98 or catalog.migrations[:-1] != prior.migrations
            or migration.name != "pdf_terminal_result_reuse_v98"
            or migration.expected_schema_sha256 != SCHEMA_SHA256
            or len(migration.statements) != 30 or not migration.postcondition_queries
            or any(type(query) is not str or not query.strip() for query in migration.postcondition_queries)):
        raise ValueError("The pinned V98 catalog, physical schema, or postconditions changed.")
    return catalog


def previous_binding(value):
    """Read only the direct immutable schema3 predecessor, never a reference chain."""
    from code_assist_v1.tools import recovered_preview_binding as binding
    if type(value) is not dict:
        raise binding.RecoveryBindingError("A schema4 operator object is required.")
    raw = value.get("previous_binding_document")
    if type(raw) is not str or not 0 < len(raw.encode("utf-8")) <= _MAX_DOCUMENT_BYTES:
        raise binding.RecoveryBindingError("Missing bounded immutable V97 predecessor document.")
    if sha256(raw.encode("utf-8")).hexdigest() != binding._digest(value.get("previous_binding_sha256")):
        raise binding.RecoveryBindingError("The V97 predecessor document differs from its external pin.")
    try:
        prior = json.loads(raw, object_pairs_hook=binding._unique_object)
    except (ValueError, UnicodeError) as exc:
        raise binding.RecoveryBindingError("Invalid V97 predecessor document.") from exc
    if (type(prior) is not dict or type(prior.get("schema_version")) is not int
            or prior["schema_version"] != 3
            or prior.get("schema_catalog_checksum") != PREDECESSOR_CATALOG_SHA256):
        raise binding.RecoveryBindingError("V98 requires the exact schema3/V97 predecessor.")
    return prior


def terminal_result_reuse_source_view(value, *, previous_source_view):
    from code_assist_v1.tools import recovered_preview_binding as binding
    if (type(value) is not dict or set(value) != binding._FORWARD_FIELDS | _PREVIOUS_FIELDS
            or type(value.get("schema_version")) is not int or value["schema_version"] != 4
            or value.get("schema_catalog_checksum") != _catalog().checksum_sha256):
        raise binding.RecoveryBindingError("Invalid isolated V98 operator binding.")
    prior = previous_binding(value)
    legacy = previous_source_view(prior)
    for name in binding._FORWARD_FIELDS - {
            "schema_version", "schema_catalog_checksum", "current_marker_sha256",
            "activation_sha256", "data_root", "code_root"}:
        if value[name] != prior[name]:
            raise binding.RecoveryBindingError("V98 changed immutable V97 source provenance.")
    hashes, old_hashes = value["activation_sha256"], prior["activation_sha256"]
    if (type(hashes) is not dict or set(hashes) != set(old_hashes) | {ACTIVATION_NAME}
            or any(hashes[name] != digest for name, digest in old_hashes.items())
            or hashes[ACTIVATION_NAME] != binding._digest(value["current_marker_sha256"])):
        raise binding.RecoveryBindingError("V98 must append exactly one activation without rewriting predecessors.")
    return {**legacy, "data_root": value["data_root"], "code_root": value["code_root"]}


def validate_terminal_result_reuse_marker(root, value, *, validate_previous):
    from code_assist_v1.tools import recovered_preview_binding as binding
    terminal_result_reuse_source_view(value, previous_source_view=binding._forward_source_view)
    prior = previous_binding(value)
    validate_previous(root, prior)
    raw = binding._read(Path(root) / ".runtime" / ACTIVATION_NAME)
    if sha256(raw).hexdigest() != value["current_marker_sha256"]:
        raise binding.RecoveryBindingError("The V98 activation differs from its pin.")
    try:
        marker = json.loads(raw, object_pairs_hook=binding._unique_object)
    except (ValueError, UnicodeError) as exc:
        raise binding.RecoveryBindingError("Invalid V98 activation JSON.") from exc
    catalog = _catalog()
    if (type(marker) is not dict or set(marker) != _MARKER_FIELDS
            or marker["kind"] != KIND or type(marker["port"]) is not int or marker["port"] != 10541
            or type(marker["schema_version"]) is not int or marker["schema_version"] != 98
            or marker["data_root"] != value["activation_data_root"]
            or marker["source_data_root"] != value["source_data_root"]
            or marker["catalog_checksum_sha256"] != catalog.checksum_sha256
            or marker["schema_sha256"] != SCHEMA_SHA256
            or marker["predecessor_catalog_checksum_sha256"] != PREDECESSOR_CATALOG_SHA256
            or marker["predecessor_activation_sha256"] != prior["current_marker_sha256"]
            or marker["previous_binding_sha256"] != value["previous_binding_sha256"]
            or marker["continuation_contract"] != context.CONTRACT
            or marker["terminal_result_reuse_contract"] != CONTRACT
            or type(marker["pdf_context_window_tokens"]) is not int or marker["pdf_context_window_tokens"] != 16384
            or type(marker["pdf_output_token_reservation"]) is not int or marker["pdf_output_token_reservation"] != 4096
            or type(marker["additional_llm_review_calls"]) is not int or marker["additional_llm_review_calls"] != 0):
        raise binding.RecoveryBindingError("The V98 activation differs from its exact V97 successor contract.")
    old_marker = json.loads(binding._read(Path(root) / ".runtime" / context.ACTIVATION_NAME),
        object_pairs_hook=binding._unique_object)
    if any(marker[name] != old_marker[name] for name in _FROZEN_MARKER_FIELDS):
        raise binding.RecoveryBindingError("V98 changed the frozen source, provider, upload, or review policy.")


def require_terminal_result_reuse_binding(root, *, binding=None):
    """Require the normal externally pinned schema4 binding, without selection or DB access."""
    from code_assist_v1.tools import recovered_preview_binding as bindings
    root = Path(root).resolve(strict=True)
    current = bindings.load_binding(root)
    if binding is not None and binding != current:
        raise bindings.RecoveryBindingError("The supplied V98 binding differs from the current external pin.")
    if (current is None or current.kind != bindings._CLEAN_RECOVERY_KIND
            or current.port != 10541 or current.provider_mode != "openai-compatible"
            or current.data_root != root or current.current_schema_catalog_checksum != _catalog().checksum_sha256
            or current.activation_data_root is None or current.continuation_marker_sha256 is None
            or current.partition_marker_sha256 is None or current.previous_binding_sha256 is None
            or current.predecessor_binding_sha256 is None or current.current_marker_sha256 is None):
        raise bindings.RecoveryBindingError("V98 requires its exact normal A10541 schema4 operator binding.")
    origin = current.activation_data_root
    if (origin.name != "data-root" or origin.parent.name != "code"
            or origin.parent.parent.name != bindings._A_ORIGIN_NAME):
        raise bindings.RecoveryBindingError("V98 changed its immutable A10541 activation origin.")
    for value in (current.current_marker_sha256, current.continuation_marker_sha256,
            current.partition_marker_sha256, current.previous_binding_sha256,
            current.predecessor_binding_sha256):
        bindings._digest(value)
    return current


def terminal_result_reuse_receipt(root, prior):
    """Construct a reviewed successor marker from the exact validated V97 selection."""
    from code_assist_v1.tools import recovered_preview_binding as binding
    if prior is None or prior.forward_partition is not True or prior.forward_continuation is not True:
        raise ValueError("V98 activation requires the exact V97 continuation selection.")
    root = Path(root).resolve(strict=True)
    current = binding.load_binding(root)
    if current is None or current.port != 10541 or current.provider_mode != "openai-compatible":
        raise ValueError("V98 activation is restricted to the explicit A10541 API binding.")
    catalog = _catalog()
    if current.current_schema_catalog_checksum == PREDECESSOR_CATALOG_SHA256:
        marker_sha, document_sha = current.current_marker_sha256, current.document_sha256
    elif current.current_schema_catalog_checksum == catalog.checksum_sha256:
        current = require_terminal_result_reuse_binding(root, binding=current)
        marker_sha, document_sha = current.continuation_marker_sha256, current.predecessor_binding_sha256
    else:
        raise ValueError("V98 activation requires a pinned V97 predecessor, not an older catalog.")
    if marker_sha != prior.activation_sha256:
        raise ValueError("The V98 predecessor selection changed.")
    origin = current.activation_data_root
    if (origin is None or origin.name != "data-root" or origin.parent.name != "code"
            or origin.parent.parent.name != binding._A_ORIGIN_NAME):
        raise ValueError("V98 activation requires its immutable A10541 origin.")
    previous_raw = binding._read(root / ".runtime" / context.ACTIVATION_NAME)
    if sha256(previous_raw).hexdigest() != marker_sha:
        raise ValueError("The pinned V97 activation changed during successor construction.")
    previous = json.loads(previous_raw, object_pairs_hook=binding._unique_object)
    if (previous["pdf_upload_policy_sha256"] != sha256(prior.upload_policy().encoded().encode("utf-8")).hexdigest()
            or any(previous[name] != getattr(prior, name) for name in
                ("registry_sha256", "provider", "model_id", "wire_model", "codex_reasoning_effort"))):
        raise ValueError("V98 changed the predecessor selection's frozen writer policy.")
    return {**previous, "kind": KIND, "schema_version": 98,
        "schema_sha256": SCHEMA_SHA256, "catalog_checksum_sha256": catalog.checksum_sha256,
        "predecessor_catalog_checksum_sha256": PREDECESSOR_CATALOG_SHA256,
        "predecessor_activation_sha256": marker_sha, "previous_binding_sha256": document_sha,
        "terminal_result_reuse_contract": CONTRACT}


def terminal_result_reuse_10541_binding_document(root, *, source_binding_path, source_binding_sha256):
    from code_assist_v1.tools import recovered_preview_binding as binding
    root = Path(root).resolve(strict=True)
    raw = binding._read(binding._absolute(str(source_binding_path)))
    if sha256(raw).hexdigest() != binding._digest(source_binding_sha256):
        raise binding.RecoveryBindingError("The current schema3/V97 operator document changed.")
    source = json.loads(raw, object_pairs_hook=binding._unique_object)
    verified = binding.load_binding(root, environ={binding.PATH_ENV: str(source_binding_path),
        binding.SHA_ENV: source_binding_sha256})
    if (verified is None or type(source.get("schema_version")) is not int or source["schema_version"] != 3
            or verified.current_schema_catalog_checksum != PREDECESSOR_CATALOG_SHA256
            or verified.current_marker_sha256 is None):
        raise binding.RecoveryBindingError("V98 requires the exact externally pinned V97 operator.")
    marker_sha = sha256(binding._read(root / ".runtime" / ACTIVATION_NAME)).hexdigest()
    value = {**source, "schema_version": 4, "schema_catalog_checksum": _catalog().checksum_sha256,
        "current_marker_sha256": marker_sha, "previous_binding_document": raw.decode("utf-8"),
        "previous_binding_sha256": source_binding_sha256,
        "activation_sha256": {**source["activation_sha256"], ACTIVATION_NAME: marker_sha}}
    encoded = (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":")) + "\n").encode("utf-8")
    if not 0 < len(encoded) <= _MAX_DOCUMENT_BYTES:
        raise binding.RecoveryBindingError("The schema4 successor exceeds the unchanged binding size bound.")
    terminal_result_reuse_source_view(value, previous_source_view=binding._forward_source_view)
    validate_terminal_result_reuse_marker(root, value, validate_previous=binding._validate_forward_marker)
    return value


__all__ = ["ACTIVATION_NAME", "KIND", "CONTRACT", "CATALOG_SHA256", "SCHEMA_SHA256",
    "previous_binding", "terminal_result_reuse_source_view", "validate_terminal_result_reuse_marker",
    "require_terminal_result_reuse_binding", "terminal_result_reuse_receipt",
    "terminal_result_reuse_10541_binding_document"]
