"""Stable JSON contract for the P0.5 read-only legacy inventory.

This module intentionally depends only on the Python standard library.  It is
safe to import without importing the legacy application, configuration, or
store modules (several of which create directories or repair databases as an
import/read side effect).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Mapping


REPORT_SCHEMA = "code-assist.legacy-inventory"
SCHEMA_VERSION = 2
TOOL_NAME = "legacy_inventory"
TOOL_VERSION = "0.2.0"


# P2 planners may refine a proposed disposition, while the P2 verifier records
# the observed outcome separately.  Keeping this vocabulary frozen here avoids
# inventing states that the migration reconciliation contract cannot consume.
TERMINAL_CLASSIFICATIONS = frozenset(
    {"imported", "rebuilt", "retained_external", "quarantined", "unavailable"}
)

# ``entry_type`` preserves the scanner observation (for example ``reparse``
# or ``special``), while ``record_kind`` is deliberately a much smaller
# reconciliation vocabulary.  Entries that cannot be opened as ordinary
# files are represented by an unavailable record rather than extending this
# public contract ad hoc.
RECORD_KINDS = frozenset({"physical_file", "logical_record", "unavailable"})
RELATIONSHIP_TYPES = frozenset(
    {
        "asset_reference",
        "candidate_source",
        "candidate_target",
        "contradicts",
        "exact_content_duplicate",
        "extends",
        "graph_node_source",
        "notion_session_page",
        "references",
        "related",
        "requires",
        "source-of",
        "supersedes",
        "supports",
        "unknown",
    }
)
_LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_CLAIMED_USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
)
_GRAPH_RELATIONSHIP_TYPES = frozenset(
    {
        "contradicts",
        "extends",
        "references",
        "related",
        "requires",
        "source-of",
        "supersedes",
        "supports",
    }
)
_SESSION_CHILD_KINDS = (
    "session_core_metadata",
    "messages",
    "metadata",
    "summaries",
    "uploads",
    "bindings",
)
_JOB_KINDS = frozenset(
    {
        "outlook_daily_digest",
        "outlook_initial_sync",
        "outlook_sync",
        "project_context.import_source",
        "project_context.import_text",
        "second_brain.import_source",
        "second_brain.import_source_batch",
    }
)
_JOB_STATUSES = frozenset(
    {"cancelled", "failed", "queued", "running", "succeeded", "unknown"}
)
_CODE_CALLSITE_ACCESS = {
    "context_reference": frozenset({"reference"}),
    "job_submit": frozenset({"submit"}),
    "legacy_store_call": frozenset({"read", "reference", "write"}),
    "legacy_store_import": frozenset({"reference"}),
    "route_decorator": frozenset({"read", "write"}),
    "route_registration": frozenset({"registration"}),
    "ui_api_call": frozenset({"ui"}),
    "ui_context_reference": frozenset({"ui"}),
}
_REQUIRED_SOURCE_ROLES = (
    "legacy",
    "knowledge",
    "runtime",
    "sessions",
    "user_data",
    "workspace",
)
_REPORT_KEYS = frozenset(
    {
        "categories",
        "code_coverage",
        "complete",
        "errors",
        "exclusions",
        "limitations",
        "manifest_sha256",
        "owners",
        "reconciliation",
        "records",
        "relationships",
        "report_schema",
        "run",
        "runtime",
        "schema_version",
        "source_snapshot",
        "summary",
        "tool",
    }
)

_CATEGORY_ROW_KEYS = frozenset(
    {
        "action_counts",
        "aggregate_sha256",
        "bytes",
        "category",
        "classification_authority_counts",
        "error_count",
        "owner_kind_counts",
        "parse_status_counts",
        "planned_action",
        "record_kind_counts",
        "records",
        "source_class",
        "source_rows",
        "terminal_classification_counts",
    }
)

_ACTION_TERMINAL_CLASSIFICATION = {
    "adapter_or_quarantine": "quarantined",
    "classify_or_quarantine": "quarantined",
    "compare_then_import": "quarantined",
    "import": "imported",
    "import_or_quarantine": "quarantined",
    "import_referenced": "imported",
    "owner_required": "quarantined",
    "preserve_history": "imported",
    "preserve_terminal_history": "imported",
    "quarantine": "quarantined",
    "quarantine_candidate": "quarantined",
    "rebuild": "rebuilt",
    "report_only": "unavailable",
    "retain_external": "retained_external",
    "validate_or_quarantine": "quarantined",
    "validate_then_import": "imported",
}

_SCANNER_DETERMINED_ACTIONS = frozenset(
    {"quarantine", "quarantine_candidate", "rebuild", "report_only", "retain_external"}
)
PLANNED_ACTIONS = frozenset(_ACTION_TERMINAL_CLASSIFICATION)


# Categories are emitted even when their count is zero.  The tuple order is
# part of the public contract, although reports also sort categories by id.
_CATEGORY_ROWS = (
    ("identity.users_registry", "identity", "import"),
    ("identity.auth_audit", "identity", "preserve_history"),
    ("knowledge.legacy_flat_markdown", "knowledge", "import_or_quarantine"),
    ("knowledge.wiki_sources", "knowledge", "import"),
    ("knowledge.wiki_hidden_pages", "knowledge", "compare_then_import"),
    ("knowledge.wiki_graph_nodes", "knowledge", "import"),
    ("knowledge.wiki_graph_edges", "knowledge", "validate_then_import"),
    ("knowledge.wiki_candidates", "knowledge", "classify_or_quarantine"),
    ("knowledge.wiki_assets", "knowledge", "import_referenced"),
    ("knowledge.wiki_log", "knowledge", "preserve_history"),
    ("knowledge.wiki_index_markdown", "projection", "rebuild"),
    ("knowledge.wiki_index_sqlite", "projection", "rebuild"),
    ("knowledge.wiki_backlinks", "projection", "rebuild"),
    ("knowledge.wiki_health", "projection", "rebuild"),
    ("knowledge.legacy_thoughts", "knowledge", "adapter_or_quarantine"),
    ("knowledge.legacy_edges", "knowledge", "validate_or_quarantine"),
    ("knowledge.legacy_inbox", "knowledge", "adapter_or_quarantine"),
    ("knowledge.legacy_raw", "knowledge", "adapter_or_quarantine"),
    ("knowledge.legacy_raw_chunks", "knowledge", "adapter_or_quarantine"),
    ("knowledge.tombstones", "knowledge", "preserve_history"),
    ("conversation.user_sessions", "conversation", "import"),
    ("conversation.global_sessions", "conversation", "owner_required"),
    ("context.memory", "external_context", "retain_external"),
    ("context.memory_events", "external_context", "retain_external"),
    ("context.project_sources", "external_context", "retain_external"),
    ("context.project_chunks", "external_context", "retain_external"),
    ("context.project_scores", "external_context", "retain_external"),
    ("context.project_assets", "external_context", "retain_external"),
    ("context.workspace_files", "external_context", "retain_external"),
    ("context.user_skills", "external_context", "retain_external"),
    ("external.notion_database", "external", "retain_external"),
    ("external.notion_database_sidecars", "external", "retain_external"),
    ("external.notion_files", "external", "retain_external"),
    # The Notion database and files remain an external source of truth, but
    # session/page links are migration inputs: P2.5 must rebind a structurally
    # valid link to the canonical conversation.  The scanner therefore never
    # treats the relationship itself as retained external data.
    ("external.notion_session_links", "external", "validate_then_import"),
    ("external.notion_backup_candidates", "external", "quarantine_candidate"),
    ("external.outlook_state", "external", "retain_external"),
    ("operations.jobs", "operations", "preserve_terminal_history"),
    ("operations.server_logs", "operations", "quarantine_candidate"),
    ("operations.temp_recovery", "operations", "quarantine_candidate"),
    ("unknown.unclassified", "unknown", "quarantine"),
    ("unavailable.browser_pins", "unavailable", "report_only"),
)

CATEGORY_DEFINITIONS: tuple[dict[str, str], ...] = tuple(
    {"category": category, "source_class": source_class, "planned_action": action}
    for category, source_class, action in _CATEGORY_ROWS
)
CATEGORY_IDS = frozenset(row[0] for row in _CATEGORY_ROWS)
CATEGORY_BY_ID = {row[0]: row for row in _CATEGORY_ROWS}


ERROR_CODES = frozenset(
    {
        "CLI_INVALID_ARGUMENT",
        "CODE_SCAN_FAILED",
        "DUPLICATE_RELATIONSHIP",
        "ENUMERATION_DENIED",
        "FILE_DISAPPEARED",
        "HASH_FAILED",
        "INVALID_JSON",
        "INVALID_JSONL",
        "INVALID_SQLITE",
        "INVALID_UTF8",
        "LEGACY_AUTO_RELATIONSHIP",
        "MISSING_REFERENCE",
        "OWNER_ID_COLLISION",
        "OUTPUT_EXISTS",
        "OUTPUT_INSIDE_SOURCE",
        "OUTPUT_PARENT_INELIGIBLE",
        "OUTPUT_PARENT_MISSING",
        "OUTPUT_WRITE_FAILED",
        "PATH_ESCAPES_ROOT",
        "PATH_KEY_COLLISION",
        "READ_FAILED",
        "ROOT_DUPLICATE",
        "ROOT_INELIGIBLE",
        "ROOT_MISSING",
        "ROOT_NOT_ABSOLUTE",
        "ROOT_NOT_DIRECTORY",
        "ROOT_OVERLAP",
        "RECONCILIATION_INCOMPLETE",
        "SOURCE_CHANGED",
        "SQLITE_SNAPSHOT_INCONSISTENT",
        "UNMAPPED_CALLSITE",
        "UNKNOWN_SQLITE_TABLE",
        "UNKNOWN_ENTRY",
        "UNSAFE_REPARSE_ENTRY",
    }
)

# ``--allow-partial`` is only a data-quality relaxation.  These conditions
# mean the scanner cannot prove that it inspected the requested immutable,
# local source set under the declared code/path contract, so they always fail
# closed with exit code 2.
HARD_ERROR_CODES = frozenset(
    {
        "CLI_INVALID_ARGUMENT",
        "CODE_SCAN_FAILED",
        "FILE_DISAPPEARED",
        "OUTPUT_PARENT_INELIGIBLE",
        "PATH_ESCAPES_ROOT",
        "PATH_KEY_COLLISION",
        "ROOT_DUPLICATE",
        "ROOT_INELIGIBLE",
        "ROOT_MISSING",
        "ROOT_NOT_ABSOLUTE",
        "ROOT_NOT_DIRECTORY",
        "ROOT_OVERLAP",
        "RECONCILIATION_INCOMPLETE",
        "SOURCE_CHANGED",
        "SQLITE_SNAPSHOT_INCONSISTENT",
        "UNMAPPED_CALLSITE",
        "UNSAFE_REPARSE_ENTRY",
    }
)


def canonical_json_bytes(value: Any) -> bytes:
    """Encode *value* using the byte-level manifest hash contract."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def sha256_canonical(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def json_safe(value: Any) -> Any:
    """Return a recursively key-sorted JSON-safe copy.

    Providers may return tuples or mappings.  Arbitrary objects, bytes, NaN,
    and infinity are rejected rather than stringified, because stringifying an
    exception or application object could disclose secrets.
    """
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non_finite_number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key in sorted(value, key=lambda item: str(item)):
            if not isinstance(key, str):
                raise TypeError("non_string_key")
            result[key] = json_safe(value[key])
        return result
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    raise TypeError("non_json_value")


def manifest_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return the deterministic portion covered by ``manifest_sha256``."""
    payload = dict(report)
    payload.pop("manifest_sha256", None)
    payload.pop("run", None)
    return json_safe(payload)


def manifest_sha256(report: Mapping[str, Any]) -> str:
    return sha256_canonical(manifest_payload(report))


def has_hard_errors(errors: Any) -> bool:
    """Return whether an error collection contains a fail-closed gate."""
    if not isinstance(errors, (list, tuple)):
        return True
    return any(
        isinstance(error, Mapping)
        and error.get("severity", "error") == "error"
        and error.get("code") in HARD_ERROR_CODES
        for error in errors
    )


def category_definition(category: str) -> dict[str, str]:
    row = CATEGORY_BY_ID[category]
    return {
        "category": row[0],
        "source_class": row[1],
        "planned_action": row[2],
    }


def proposed_terminal_classification(planned_action: str) -> str:
    """Map one scanner action to the frozen P2 terminal vocabulary."""
    return _ACTION_TERMINAL_CLASSIFICATION[planned_action]


def classification_authority(planned_action: str) -> str:
    """Return whether the scanner can decide or a reviewed plan is required."""
    return "scanner" if planned_action in _SCANNER_DETERMINED_ACTIONS else "planner_required"


def empty_category_rows() -> list[dict[str, Any]]:
    return [
        {
            **category_definition(category),
            "action_counts": {},
            "aggregate_sha256": sha256_canonical([]),
            "bytes": 0,
            "classification_authority_counts": {},
            "error_count": 0,
            "owner_kind_counts": {},
            "parse_status_counts": {},
            "record_kind_counts": {},
            "records": 0,
            "source_rows": 0,
            "terminal_classification_counts": {
                key: 0 for key in sorted(TERMINAL_CLASSIFICATIONS)
            },
        }
        for category in sorted(CATEGORY_IDS)
    ]


def _record_owner_identity(record: Mapping[str, Any] | None) -> tuple[str, str | None] | None:
    if not isinstance(record, Mapping):
        return None
    owner = record.get("owner")
    if not isinstance(owner, Mapping):
        return None
    kind = owner.get("kind")
    user_id = owner.get("user_id")
    if not isinstance(kind, str) or (user_id is not None and not isinstance(user_id, str)):
        return None
    return kind, user_id


def _valid_record_owner(value: Any) -> bool:
    """Validate the scanner's closed ownership vocabulary and combinations."""
    if not isinstance(value, Mapping) or set(value) != {
        "claimed_user_id",
        "kind",
        "mapping_status",
        "user_id",
    }:
        return False
    kind = value.get("kind")
    status = value.get("mapping_status")
    claimed = value.get("claimed_user_id")
    user_id = value.get("user_id")
    claimed_valid = (
        isinstance(claimed, str)
        and _CLAIMED_USER_ID_RE.fullmatch(claimed) is not None
    )
    canonical_valid = (
        isinstance(user_id, str)
        and _CANONICAL_USER_ID_RE.fullmatch(user_id) is not None
    )
    if kind == "system":
        return status == "confirmed" and claimed is None and user_id is None
    if kind == "unknown":
        return status == "unresolved" and claimed is None and user_id is None
    if kind == "personal":
        if status == "confirmed":
            return (
                claimed_valid
                and canonical_valid
                and unicodedata.normalize("NFKC", claimed).casefold()
                == unicodedata.normalize("NFKC", user_id).casefold()
            )
        if status == "unverified":
            return claimed_valid and canonical_valid and claimed == user_id
        return False
    if kind == "ambiguous_registry_owner":
        return status == "ambiguous" and claimed_valid and user_id is None
    if kind == "unresolved_path_owner":
        return status == "unresolved" and claimed_valid and user_id is None
    return False


def _relationship_identity_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    relationship_type = value.get("relationship_type")
    status = value.get("status")
    if status == "valid":
        if relationship_type in {
            "asset_reference",
            "candidate_source",
            "candidate_target",
            "exact_content_duplicate",
            "graph_node_source",
            "notion_session_page",
        }:
            return {
                "relationship_type": relationship_type,
                "from_record_id": value.get("from_record_id"),
                "to_record_id": value.get("to_record_id"),
            }
        if relationship_type in {
            "contradicts",
            "extends",
            "references",
            "related",
            "requires",
            "source-of",
            "supersedes",
            "supports",
        }:
            return {
                "relationship_type": relationship_type,
                "from_record_id": value.get("from_record_id"),
                "to_record_id": value.get("to_record_id"),
            }
    return {
        key: value.get(key)
        for key in (
            "relationship_type",
            "source_record_id",
            "from_record_id",
            "to_record_id",
            "from_ref_sha256",
            "to_ref_sha256",
            "status",
        )
        if value.get(key) is not None
    }


def _valid_nonnegative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_canonical_utc(value: Any) -> bool:
    """Accept only a real UTC instant in the frozen microsecond format."""

    if not isinstance(value, str) or _UTC_TIMESTAMP_RE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return (
        parsed.tzinfo is not None
        and parsed.utcoffset() == timezone.utc.utcoffset(parsed)
        and parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        == value
    )


def _valid_positive_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _valid_root_fact(value: Any, *, scratch: bool = False) -> bool:
    if not isinstance(value, Mapping) or set(value) != {
        "configured_path",
        "filesystem_identity",
        "path_source",
        "resolved_path",
        "role",
        "root_id",
        "reparse",
        "volume",
    }:
        return False
    configured = value.get("configured_path")
    resolved = value.get("resolved_path")
    identity = value.get("filesystem_identity")
    reparse = value.get("reparse")
    volume = value.get("volume")
    if (
        not isinstance(configured, str)
        or not os.path.isabs(configured)
        or not isinstance(resolved, str)
        or not os.path.isabs(resolved)
        or value.get("path_source") not in {"derived", "explicit"}
        or (scratch and value.get("path_source") != "explicit")
        or not isinstance(value.get("role"), str)
        or not isinstance(value.get("root_id"), str)
        or (scratch and (value.get("role"), value.get("root_id")) != ("scratch", "scratch"))
        or not isinstance(identity, Mapping)
        or set(identity) != {"device", "inode"}
        or not all(_valid_nonnegative_integer(identity.get(key)) for key in ("device", "inode"))
        or not isinstance(reparse, Mapping)
        or set(reparse) != {"components", "present"}
        or reparse.get("present") is not False
        or reparse.get("components") != []
        or not isinstance(volume, Mapping)
        or set(volume) != {"drive_type", "filesystem", "root"}
        or str(volume.get("drive_type") or "").casefold() != "fixed"
        or str(volume.get("filesystem") or "").casefold() != "ntfs"
        or not isinstance(volume.get("root"), str)
        or not volume.get("root")
    ):
        return False
    return True


def _valid_high_water(value: Any, *, code_root: bool) -> bool:
    if not isinstance(value, Mapping) or set(value) != {
        "directories",
        "entries",
        "files",
        "tree_sha256",
    }:
        return False
    directories = value.get("directories")
    if code_root:
        if directories is not None:
            return False
    elif not _valid_nonnegative_integer(directories):
        return False
    return (
        _valid_nonnegative_integer(value.get("entries"))
        and _valid_nonnegative_integer(value.get("files"))
        and value.get("files") <= value.get("entries")
        and isinstance(value.get("tree_sha256"), str)
        and _LOWER_SHA256_RE.fullmatch(value["tree_sha256"]) is not None
    )


def _inventory_contract_errors_unchecked(
    report: Mapping[str, Any], *, verify_manifest: bool = True
) -> tuple[str, ...]:
    """Return deterministic schema-v2 closure violations without mutating input."""
    errors: list[str] = []
    if set(report) != _REPORT_KEYS:
        errors.append("report_fields")
    if report.get("report_schema") != REPORT_SCHEMA or report.get("schema_version") != SCHEMA_VERSION:
        errors.append("report_identity")
    records = report.get("records")
    categories = report.get("categories")
    reconciliation = report.get("reconciliation")
    relationships = report.get("relationships")
    report_errors = report.get("errors")
    if (
        not isinstance(records, list)
        or not isinstance(categories, list)
        or not isinstance(reconciliation, Mapping)
        or not isinstance(relationships, list)
        or not isinstance(report_errors, list)
    ):
        return tuple(sorted(set((*errors, "report_shape"))))
    observed_error_rows: set[str] = set()
    for error in report_errors:
        if not isinstance(error, Mapping) or set(error) != {
            "code",
            "operation",
            "reason_codes",
            "relative_path",
            "root_id",
            "severity",
        }:
            errors.append("error_shape")
            continue
        if (
            error.get("code") not in ERROR_CODES
            or not isinstance(error.get("operation"), str)
            or not error.get("operation")
            or not isinstance(error.get("reason_codes"), list)
            or not all(isinstance(item, str) for item in error["reason_codes"])
            or error.get("reason_codes") != sorted(set(error["reason_codes"]))
            or not isinstance(error.get("relative_path"), str)
            or not isinstance(error.get("root_id"), str)
            or error.get("severity") not in {"error", "warning"}
        ):
            errors.append("error_shape")
        identity = sha256_canonical(error)
        if identity in observed_error_rows:
            errors.append("error_duplicate")
        observed_error_rows.add(identity)
    record_ids: set[str] = set()
    record_by_id: dict[str, Mapping[str, Any]] = {}
    source_identities: set[tuple[Any, Any, Any]] = set()
    children: dict[str, int] = {}
    child_records_by_parent: dict[str, list[Mapping[str, Any]]] = {}
    terminal_counts = {key: 0 for key in sorted(TERMINAL_CLASSIFICATIONS)}
    category_records: dict[str, list[Mapping[str, Any]]] = {key: [] for key in CATEGORY_IDS}
    for record in records:
        if not isinstance(record, Mapping):
            errors.append("record_shape")
            continue
        record_id = record.get("record_id")
        category = record.get("category")
        terminal = record.get("proposed_terminal_classification")
        if not isinstance(record_id, str) or not record_id or record_id in record_ids:
            errors.append("record_identity")
        else:
            record_ids.add(record_id)
            record_by_id[record_id] = record
        expected_record_id = "rec_" + sha256_canonical(
            {
                "category": category,
                "owner": record.get("owner"),
                "path": record.get("relative_path"),
                "root_id": record.get("root_id"),
                "source_ordinal": record.get("source_ordinal"),
            }
        )[:24]
        if record_id != expected_record_id:
            errors.append("record_identity_binding")
        if category not in CATEGORY_IDS:
            errors.append("record_category")
        else:
            category_records[str(category)].append(record)
            if record.get("source_class") != category_definition(str(category))["source_class"]:
                errors.append("record_source_class")
        if terminal not in TERMINAL_CLASSIFICATIONS:
            errors.append("record_terminal")
        else:
            terminal_counts[str(terminal)] += 1
        if record.get("classification_authority") not in {"planner_required", "scanner"}:
            errors.append("record_authority")
        if record.get("record_kind") not in RECORD_KINDS:
            errors.append("record_kind")
        entry_type = record.get("entry_type")
        expected_record_kind = {
            "file": "physical_file",
            "logical_record": "logical_record",
            "missing": "unavailable",
            "reparse": "unavailable",
            "special": "unavailable",
            "unavailable": "unavailable",
        }.get(entry_type)
        if expected_record_kind is None or record.get("record_kind") != expected_record_kind:
            errors.append("record_entry_type")
        relative_path = record.get("relative_path")
        path_key = record.get("path_key")
        if (
            not isinstance(relative_path, str)
            or not relative_path
            or relative_path.startswith(("/", "\\"))
            or re.match(r"^[A-Za-z]:", relative_path) is not None
            or any(part in {"", ".", ".."} for part in relative_path.replace("\\", "/").split("/"))
            or path_key != unicodedata.normalize("NFKC", relative_path).casefold()
        ):
            errors.append("record_path")
        source_ordinal = record.get("source_ordinal")
        if not _valid_nonnegative_integer(source_ordinal) or (
            record.get("record_kind") == "logical_record" and source_ordinal < 1
        ) or (
            record.get("record_kind") != "logical_record" and source_ordinal != 0
        ):
            errors.append("record_ordinal")
        record_sha256 = record.get("sha256")
        if record_sha256 is not None and (
            not isinstance(record_sha256, str)
            or _LOWER_SHA256_RE.fullmatch(record_sha256) is None
        ):
            errors.append("record_sha256")
        reasons = record.get("reason_codes")
        classification_reasons = record.get("classification_reason_codes")
        if (
            not isinstance(reasons, list)
            or not all(isinstance(item, str) and item for item in reasons)
            or reasons != sorted(set(reasons))
            or not isinstance(classification_reasons, list)
            or not all(isinstance(item, str) and item for item in classification_reasons)
            or classification_reasons != sorted(set(classification_reasons))
        ):
            errors.append("record_reasons")
        references = record.get("references")
        if (
            not isinstance(references, Mapping)
            or set(references) != {"declared", "missing", "resolved"}
            or not all(_valid_nonnegative_integer(references.get(key)) for key in references)
            or references.get("missing", 0) + references.get("resolved", 0)
            != references.get("declared", 0)
        ):
            errors.append("record_references")
        size_bytes = record.get("size_bytes")
        stat = record.get("stat")
        if (
            (size_bytes is not None and not _valid_nonnegative_integer(size_bytes))
            or not isinstance(stat, Mapping)
            or set(stat) != {"mtime_ns"}
            or (
                stat.get("mtime_ns") is not None
                and not _valid_nonnegative_integer(stat.get("mtime_ns"))
            )
        ):
            errors.append("record_stat")
        action = record.get("planned_action")
        if action not in PLANNED_ACTIONS:
            errors.append("record_action")
        else:
            definition_action = category_definition(str(category))["planned_action"] if category in CATEGORY_IDS else None
            reasons = record.get("reason_codes")
            override_valid = (
                action == definition_action
                or (
                    action == "report_only"
                    and record.get("status") == "unavailable"
                    and isinstance(reasons, list)
                    and bool(reasons)
                )
                or (
                    action == "quarantine_candidate"
                    and isinstance(reasons, list)
                    and bool(reasons)
                )
                or (
                    action == "classify_or_quarantine"
                    and category == "identity.users_registry"
                    and reasons == ["legacy_role_requires_plan"]
                )
            )
            if not override_valid:
                errors.append("record_action_override")
            parse = record.get("parse")
            base_parse_keys = {
                "child_count",
                "logical_sha256",
                "record_count",
                "schema_version",
                "source_member_count",
                "status",
            }
            category_parse_keys = {
                "identity.users_registry": {"registry_account"},
                "knowledge.legacy_edges": {"kind_counts", "status_counts"},
                "knowledge.wiki_graph_edges": {"graph"},
                "knowledge.wiki_candidates": {"candidate_counts"},
                "conversation.user_sessions": {
                    "child_aggregate_sha256",
                    "child_counts",
                    "session_id_sha256",
                },
                "conversation.global_sessions": {
                    "child_aggregate_sha256",
                    "child_counts",
                    "session_id_sha256",
                },
                "operations.jobs": {
                    "job_identity",
                    "source_member_count",
                    "source_status",
                },
                "external.notion_database": {
                    "foreign_key_violation_count",
                    "quick_check_ok",
                    "schema_sha256",
                    "table_counts",
                    "table_row_count",
                    "table_sha256",
                    "unknown_table_count",
                    "unknown_table_names_sha256",
                },
            }
            if not isinstance(parse, Mapping) or not set(parse).issubset(
                base_parse_keys | category_parse_keys.get(str(category), set())
            ):
                errors.append("record_parse_shape")
            elif "source_member_count" in parse and not _valid_nonnegative_integer(
                parse.get("source_member_count")
            ):
                errors.append("record_parse_shape")
            owner = record.get("owner")
            invalid = (
                record.get("status") in {"invalid", "unknown", "unreadable"}
                or (
                    isinstance(parse, Mapping)
                    and parse.get("status") == "invalid"
                )
                or (
                    isinstance(owner, Mapping)
                    and owner.get("mapping_status") in {"ambiguous", "unresolved"}
                )
            )
            expected_terminal = (
                "quarantined"
                if invalid
                else proposed_terminal_classification(str(action))
            )
            expected_authority = (
                "scanner" if invalid else classification_authority(str(action))
            )
            if terminal != expected_terminal:
                errors.append("record_action_terminal")
            if record.get("classification_authority") != expected_authority:
                errors.append("record_action_authority")
        source_identity = record.get("source_identity")
        if not isinstance(source_identity, Mapping) or set(source_identity) != {
            "path_key",
            "root_id",
            "source_ordinal",
        }:
            errors.append("record_source_identity")
        elif (
            source_identity.get("path_key") != record.get("path_key")
            or source_identity.get("root_id") != record.get("root_id")
            or source_identity.get("source_ordinal") != record.get("source_ordinal")
        ):
            errors.append("record_source_identity_binding")
        else:
            source_identity_key = (
                source_identity.get("root_id"),
                source_identity.get("path_key"),
                source_identity.get("source_ordinal"),
            )
            if source_identity_key in source_identities:
                errors.append("record_source_identity_duplicate")
            source_identities.add(source_identity_key)
        owner = record.get("owner")
        if not _valid_record_owner(owner):
            errors.append("record_owner_shape")
        if record.get("record_kind") == "logical_record":
            parent = record.get("parent_record_id")
            if isinstance(parent, str):
                children[parent] = children.get(parent, 0) + 1
                child_records_by_parent.setdefault(parent, []).append(record)
            else:
                errors.append("record_parent")
        elif record.get("parent_record_id") is not None:
            errors.append("record_parent")
        if (
            record.get("record_kind") != "unavailable"
            and not isinstance(record.get("sha256"), str)
            and record.get("status") not in {"invalid", "unavailable", "unreadable"}
        ):
            errors.append("record_hash")
    for record in records:
        if not isinstance(record, Mapping):
            continue
        if record.get("record_kind") == "logical_record" and record.get("parent_record_id") not in record_ids:
            errors.append("record_parent")
        parse = record.get("parse")
        child_count = parse.get("child_count") if isinstance(parse, Mapping) else None
        if (
            not isinstance(child_count, int)
            or isinstance(child_count, bool)
            or child_count < 0
            or child_count != children.get(str(record.get("record_id")), 0)
        ):
            errors.append("record_child_count")
    relationship_ids: set[str] = set()
    relationship_facts: set[str] = set()
    relationship_rows: list[Mapping[str, Any]] = []
    for relationship in relationships:
        if not isinstance(relationship, Mapping):
            errors.append("relationship_shape")
            continue
        relationship_rows.append(relationship)
        relationship_id = relationship.get("relationship_id")
        if (
            not isinstance(relationship_id, str)
            or not relationship_id
            or relationship_id in relationship_ids
        ):
            errors.append("relationship_identity")
        else:
            relationship_ids.add(relationship_id)
        if not set(relationship).issubset(
            {
                "from_record_id",
                "from_ref_sha256",
                "reason_codes",
                "relationship_id",
                "relationship_type",
                "source_record_id",
                "status",
                "to_record_id",
                "to_ref_sha256",
            }
        ):
            errors.append("relationship_shape")
        reason_codes = relationship.get("reason_codes")
        if (
            not isinstance(reason_codes, list)
            or not all(isinstance(item, str) and item for item in reason_codes)
            or reason_codes != sorted(set(reason_codes))
            or (relationship.get("status") == "valid" and reason_codes)
            or (relationship.get("status") != "valid" and not reason_codes)
        ):
            errors.append("relationship_reasons")
        semantic_fact = sha256_canonical(
            _relationship_identity_payload(relationship)
        )
        if semantic_fact in relationship_facts:
            errors.append("relationship_duplicate")
        relationship_facts.add(semantic_fact)
        if relationship_id != "rel_" + semantic_fact[:24]:
            errors.append("relationship_identity_binding")
        relationship_type = relationship.get("relationship_type")
        if relationship_type not in RELATIONSHIP_TYPES:
            errors.append("relationship_type")
        status = relationship.get("status")
        if status not in {
            "duplicate",
            "invalid",
            "legacy_auto",
            "orphan",
            "unresolved_legacy_reference",
            "unavailable",
            "valid",
        }:
            errors.append("relationship_status")
        if status == "valid":
            allowed_valid_keys = {
                "from_record_id",
                "reason_codes",
                "relationship_id",
                "relationship_type",
                "status",
                "to_record_id",
            }
            if relationship_type in {
                "contradicts",
                "extends",
                "references",
                "related",
                "requires",
                "source-of",
                "supersedes",
                "supports",
                "notion_session_page",
            }:
                allowed_valid_keys.add("source_record_id")
            if set(relationship) != allowed_valid_keys:
                errors.append("relationship_valid_shape")
        for field in ("from_record_id", "source_record_id", "to_record_id"):
            endpoint = relationship.get(field)
            if endpoint is not None and endpoint not in record_ids:
                errors.append("relationship_endpoint")
        for field in ("from_ref_sha256", "to_ref_sha256"):
            reference = relationship.get(field)
            if reference is not None and (
                not isinstance(reference, str)
                or _LOWER_SHA256_RE.fullmatch(reference) is None
            ):
                errors.append("relationship_reference")
        if status == "valid" and (
            relationship.get("from_record_id") not in record_ids
            or relationship.get("to_record_id") not in record_ids
        ):
            errors.append("relationship_valid_endpoints")
        if not any(
            relationship.get(field) in record_ids
            for field in ("from_record_id", "source_record_id")
        ):
            errors.append("relationship_source")
        if not (
            relationship.get("to_record_id") in record_ids
            or isinstance(relationship.get("to_ref_sha256"), str)
        ):
            errors.append("relationship_target")
        from_record = record_by_id.get(str(relationship.get("from_record_id") or ""))
        source_record = record_by_id.get(str(relationship.get("source_record_id") or ""))
        to_record = record_by_id.get(str(relationship.get("to_record_id") or ""))
        if status == "valid":
            endpoint_categories = (
                from_record.get("category") if isinstance(from_record, Mapping) else None,
                to_record.get("category") if isinstance(to_record, Mapping) else None,
            )
            expected_categories: dict[str, tuple[set[str], set[str]]] = {
                "asset_reference": (
                    {"knowledge.wiki_hidden_pages", "knowledge.wiki_sources"},
                    {"knowledge.wiki_assets"},
                ),
                "candidate_source": (
                    {"knowledge.wiki_candidates"},
                    {"knowledge.wiki_hidden_pages", "knowledge.wiki_sources"},
                ),
                "candidate_target": (
                    {"knowledge.wiki_candidates"},
                    {"knowledge.wiki_hidden_pages", "knowledge.wiki_sources"},
                ),
                "exact_content_duplicate": (
                    {"knowledge.wiki_hidden_pages"},
                    {"knowledge.wiki_sources"},
                ),
                "graph_node_source": (
                    {"knowledge.wiki_graph_nodes"},
                    {"knowledge.wiki_hidden_pages", "knowledge.wiki_sources"},
                ),
                "notion_session_page": (
                    {"conversation.user_sessions"},
                    {"external.notion_session_links"},
                ),
            }
            if relationship_type in expected_categories:
                expected_from, expected_to = expected_categories[str(relationship_type)]
                if endpoint_categories[0] not in expected_from or endpoint_categories[1] not in expected_to:
                    errors.append("relationship_category_direction")
                if _record_owner_identity(from_record) != _record_owner_identity(to_record):
                    errors.append("relationship_owner_direction")
                if relationship_type == "notion_session_page" and (
                    not isinstance(source_record, Mapping)
                    or source_record.get("category")
                    != "external.notion_session_links"
                    or source_record.get("record_id") != to_record.get("record_id")
                ):
                    errors.append("relationship_source_evidence")
            elif relationship_type in {
                "contradicts",
                "extends",
                "references",
                "related",
                "requires",
                "source-of",
                "supersedes",
                "supports",
            }:
                document_categories = {
                    "knowledge.wiki_hidden_pages",
                    "knowledge.wiki_sources",
                }
                if not set(endpoint_categories).issubset(document_categories):
                    errors.append("relationship_category_direction")
                if not isinstance(source_record, Mapping) or source_record.get("category") != "knowledge.wiki_graph_edges":
                    errors.append("relationship_source_evidence")
                if _record_owner_identity(from_record) != _record_owner_identity(to_record):
                    errors.append("relationship_owner_direction")
            else:
                errors.append("relationship_valid_type")
    valid_relationships_by_from: dict[str, list[Mapping[str, Any]]] = {}
    valid_relationships_by_source: dict[str, list[Mapping[str, Any]]] = {}
    relationships_by_from: dict[str, list[Mapping[str, Any]]] = {}
    relationships_by_source: dict[str, list[Mapping[str, Any]]] = {}
    relationships_by_to: dict[str, list[Mapping[str, Any]]] = {}
    for relationship in relationship_rows:
        from_id = relationship.get("from_record_id")
        source_id = relationship.get("source_record_id")
        to_id = relationship.get("to_record_id")
        if isinstance(from_id, str):
            relationships_by_from.setdefault(from_id, []).append(relationship)
        if isinstance(source_id, str):
            relationships_by_source.setdefault(source_id, []).append(relationship)
        if isinstance(to_id, str):
            relationships_by_to.setdefault(to_id, []).append(relationship)
        if relationship.get("status") != "valid":
            continue
        if isinstance(from_id, str):
            valid_relationships_by_from.setdefault(from_id, []).append(relationship)
        if isinstance(source_id, str):
            valid_relationships_by_source.setdefault(source_id, []).append(relationship)
    for record in records:
        if not isinstance(record, Mapping):
            continue
        record_id = record.get("record_id")
        if not isinstance(record_id, str):
            continue
        if (
            record.get("category") == "knowledge.wiki_candidates"
            and record.get("record_kind") == "logical_record"
        ):
            candidate = record.get("candidate")
            resolution = (
                candidate.get("resolution")
                if isinstance(candidate, Mapping)
                else None
            )
            rows = valid_relationships_by_from.get(record_id, ())
            candidate_rows = [
                row
                for row in rows
                if row.get("relationship_type")
                in {"candidate_source", "candidate_target"}
            ]
            expected_candidate_keys = {
                "action",
                "base_or_revision_evidence",
                "identity_evidence",
                "kind",
                "payload_sha256",
                "resolution",
                "source_artifact_sha256",
                "status",
                "target_sha256",
            }
            valid_resolution = (
                isinstance(resolution, Mapping)
                and set(resolution)
                == {"source_record_id", "status", "target_record_id"}
                and resolution.get("status") in {"resolved", "unresolved"}
            )
            valid_candidate_shape = (
                isinstance(candidate, Mapping)
                and set(candidate) == expected_candidate_keys
                and candidate.get("kind") in {"graph_candidate", "unknown", "wiki_diff"}
                and candidate.get("action") in {"add_edge", "create", "unknown"}
                and candidate.get("status")
                in {"applied", "deleted", "pending", "rejected", "skipped", "unknown"}
                and isinstance(candidate.get("base_or_revision_evidence"), bool)
                and isinstance(candidate.get("identity_evidence"), bool)
                and isinstance(candidate.get("payload_sha256"), str)
                and _LOWER_SHA256_RE.fullmatch(candidate.get("payload_sha256"))
                is not None
                and all(
                    value is None
                    or (
                        isinstance(value, str)
                        and _LOWER_SHA256_RE.fullmatch(value) is not None
                    )
                    for value in (
                        candidate.get("source_artifact_sha256"),
                        candidate.get("target_sha256"),
                    )
                )
            )
            if (
                not valid_candidate_shape
                or not valid_resolution
            ):
                errors.append("candidate_relationship_closure")
            elif resolution.get("status") == "resolved":
                expected = {
                    "candidate_source": resolution.get("source_record_id"),
                    "candidate_target": resolution.get("target_record_id"),
                }
                observed = {
                    str(row.get("relationship_type")): row.get("to_record_id")
                    for row in candidate_rows
                }
                if len(candidate_rows) != 2 or observed != expected:
                    errors.append("candidate_relationship_closure")
                if (
                    record.get("status") != "active"
                    or record.get("reason_codes") != []
                ):
                    errors.append("candidate_relationship_closure")
            elif candidate_rows:
                errors.append("candidate_relationship_closure")
            elif candidate.get("kind") != "unknown" and (
                record.get("status") != "unresolved"
                or record.get("planned_action") != "quarantine_candidate"
                or "candidate_reference_unresolved"
                not in (record.get("reason_codes") or ())
            ):
                errors.append("candidate_relationship_closure")
        elif record.get("category") == "knowledge.wiki_graph_nodes":
            resolution = record.get("graph_node_resolution")
            rows = [
                row
                for row in valid_relationships_by_from.get(record_id, ())
                if row.get("relationship_type") == "graph_node_source"
            ]
            if not isinstance(resolution, Mapping) or set(resolution) != {
                "source_record_id",
                "status",
            } or resolution.get("status") not in {"resolved", "unresolved"}:
                errors.append("graph_node_relationship_closure")
            elif resolution.get("status") == "resolved":
                if (
                    len(rows) != 1
                    or rows[0].get("to_record_id")
                    != resolution.get("source_record_id")
                ):
                    errors.append("graph_node_relationship_closure")
                if (
                    record.get("status") != "active"
                    or record.get("planned_action") != "import"
                    or record.get("reason_codes") != []
                ):
                    errors.append("graph_node_relationship_closure")
            elif (
                rows
                or record.get("status") != "orphan"
                or record.get("planned_action") != "quarantine_candidate"
                or "graph_node_source_unresolved"
                not in (record.get("reason_codes") or ())
            ):
                errors.append("graph_node_relationship_closure")
        elif record.get("category") == "external.notion_session_links":
            resolution = record.get("link_resolution")
            rows = [
                row
                for row in relationships_by_source.get(record_id, ())
                if row.get("relationship_type") == "notion_session_page"
            ]
            valid_resolution = (
                isinstance(resolution, Mapping)
                and set(resolution)
                == {
                    "page_identity_sha256",
                    "page_exists",
                    "session_record_id",
                    "status",
                }
                and isinstance(resolution.get("page_identity_sha256"), str)
                and _LOWER_SHA256_RE.fullmatch(
                    resolution.get("page_identity_sha256")
                )
                is not None
                and isinstance(resolution.get("page_exists"), bool)
                and resolution.get("status")
                in {"owner_mismatch", "parent_invalid", "resolved", "unavailable"}
            )
            if not valid_resolution or len(rows) != 1:
                errors.append("notion_relationship_closure")
            elif resolution.get("status") == "resolved":
                if (
                    rows[0].get("status") != "valid"
                    or rows[0].get("from_record_id")
                    != resolution.get("session_record_id")
                    or rows[0].get("to_record_id") != record_id
                    or record.get("status") != "active"
                    or record.get("planned_action") != "validate_then_import"
                    or record.get("reason_codes") != []
                ):
                    errors.append("notion_relationship_closure")
            else:
                expected = {
                    "owner_mismatch": (
                        "invalid",
                        "quarantine_candidate",
                        "notion_link_owner_mismatch",
                        "invalid",
                    ),
                    "parent_invalid": (
                        "invalid",
                        "quarantine_candidate",
                        "notion_parent_invalid",
                        "invalid",
                    ),
                    "unavailable": (
                        "unavailable",
                        "report_only",
                        "notion_link_target_unavailable",
                        "unavailable",
                    ),
                }[str(resolution.get("status"))]
                if (
                    record.get("status") != expected[0]
                    or record.get("planned_action") != expected[1]
                    or record.get("reason_codes") != [expected[2]]
                    or rows[0].get("status") != expected[3]
                    or rows[0].get("reason_codes") != [expected[2]]
                ):
                    errors.append("notion_relationship_closure")

    for parent in records:
        if (
            not isinstance(parent, Mapping)
            or parent.get("category") != "knowledge.wiki_candidates"
            or parent.get("record_kind") != "physical_file"
        ):
            continue
        parse = parent.get("parse")
        candidate_counts = (
            parse.get("candidate_counts") if isinstance(parse, Mapping) else None
        )
        child_rows = sorted(
            (
                row
                for row in child_records_by_parent.get(
                    str(parent.get("record_id")), ()
                )
                if row.get("category") == "knowledge.wiki_candidates"
            ),
            key=lambda row: int(row.get("source_ordinal") or 0),
        )
        countable = [
            row
            for row in child_rows
            if isinstance(row.get("candidate"), Mapping)
            and "invalid_candidate" not in (row.get("reason_codes") or ())
        ]

        def candidate_counter(field: str) -> dict[str, int]:
            result: dict[str, int] = {}
            for row in countable:
                value = str(row["candidate"].get(field))
                result[value] = result.get(value, 0) + 1
            return {key: result[key] for key in sorted(result)}

        expected_counts = {
            "actions": candidate_counter("action"),
            "evidence": {
                "base_or_revision": sum(
                    row["candidate"].get("base_or_revision_evidence") is True
                    for row in countable
                ),
                "identity_evidence": sum(
                    row["candidate"].get("identity_evidence") is True
                    for row in countable
                ),
                "resolved": sum(
                    isinstance(row["candidate"].get("resolution"), Mapping)
                    and row["candidate"]["resolution"].get("status") == "resolved"
                    for row in countable
                ),
                "source": sum(
                    row["candidate"].get("source_artifact_sha256") is not None
                    for row in countable
                ),
                "target": sum(
                    row["candidate"].get("target_sha256") is not None
                    for row in countable
                ),
            },
            "kinds": candidate_counter("kind"),
            "statuses": candidate_counter("status"),
        }
        if (
            candidate_counts != expected_counts
            or not isinstance(parse, Mapping)
            or parse.get("child_count") != len(child_rows)
            or parse.get("record_count") != len(child_rows)
            or parse.get("logical_sha256")
            != sha256_canonical([row.get("sha256") for row in child_rows])
        ):
            errors.append("candidate_parent_closure")

    for hidden in records:
        if (
            not isinstance(hidden, Mapping)
            or hidden.get("category") != "knowledge.wiki_hidden_pages"
            or hidden.get("record_kind") != "physical_file"
        ):
            continue
        exact_rows = [
            row
            for row in valid_relationships_by_from.get(
                str(hidden.get("record_id")), ()
            )
            if row.get("relationship_type") == "exact_content_duplicate"
        ]
        expected_ids = sorted(str(row.get("to_record_id")) for row in exact_rows)
        if hidden.get("exact_source_duplicate_record_ids") != expected_ids:
            errors.append("hidden_duplicate_relationship_closure")
        for row in exact_rows:
            target = record_by_id.get(str(row.get("to_record_id") or ""))
            if (
                not isinstance(target, Mapping)
                or hidden.get("sha256") != target.get("sha256")
            ):
                errors.append("hidden_duplicate_relationship_closure")

    # Graph edge rows are reconciliation authority.  A logical edge and its
    # relationship are an exact one-to-one pair; neither may be removed or
    # retagged while leaving the other half apparently valid.
    graph_edge_children = [
        row
        for row in records
        if isinstance(row, Mapping)
        and row.get("category") == "knowledge.wiki_graph_edges"
        and row.get("record_kind") == "logical_record"
    ]
    graph_edge_child_ids = {
        row.get("record_id")
        for row in graph_edge_children
        if isinstance(row.get("record_id"), str)
    }
    for relationship in relationship_rows:
        relationship_source = record_by_id.get(
            str(relationship.get("source_record_id") or "")
        )
        if (
            isinstance(relationship_source, Mapping)
            and relationship_source.get("category") == "knowledge.wiki_graph_edges"
            and relationship.get("source_record_id") not in graph_edge_child_ids
        ):
            errors.append("graph_edge_relationship_closure")
    for child in graph_edge_children:
        child_id = child.get("record_id")
        parent = record_by_id.get(str(child.get("parent_record_id") or ""))
        edge = child.get("graph_edge")
        resolution = child.get("graph_edge_resolution")
        rows = relationships_by_source.get(str(child_id), ())
        if (
            not isinstance(edge, Mapping)
            or set(edge) != {
                "disposition",
                "payload_sha256",
                "relation_type",
                "source_sha256",
                "target_sha256",
            }
            or edge.get("disposition")
            not in {"duplicate", "legacy_auto", "orphan", "valid"}
            or edge.get("relation_type")
            not in _GRAPH_RELATIONSHIP_TYPES | {"unknown"}
            or any(
                not isinstance(edge.get(field), str)
                or _LOWER_SHA256_RE.fullmatch(edge[field]) is None
                for field in ("payload_sha256", "source_sha256", "target_sha256")
            )
            or not isinstance(resolution, Mapping)
            or set(resolution) != {"from_record_id", "status", "to_record_id"}
            or resolution.get("status")
            not in {"duplicate", "legacy_auto", "orphan", "valid"}
            or (
                resolution.get("from_record_id") is not None
                and resolution.get("from_record_id") not in record_ids
            )
            or (
                resolution.get("to_record_id") is not None
                and resolution.get("to_record_id") not in record_ids
            )
            or not isinstance(parent, Mapping)
            or child.get("sha256")
            != sha256_canonical(
                {
                    "ordinal": child.get("source_ordinal"),
                    "shape": edge,
                    "source_sha256": parent.get("sha256"),
                }
            )
        ):
            errors.append("graph_edge_child_binding")
        status = resolution.get("status") if isinstance(resolution, Mapping) else None
        expected_reasons = [] if status == "valid" else [f"graph_edge_{status}"]
        expected_action = (
            "validate_then_import" if status == "valid" else "quarantine_candidate"
        )
        expected_child_status = "active" if status == "valid" else status
        if (
            child.get("reason_codes") != expected_reasons
            or child.get("planned_action") != expected_action
            or child.get("status") != expected_child_status
            or len(rows) != 1
        ):
            errors.append("graph_edge_relationship_closure")
            continue
        relationship = rows[0]
        if (
            relationship.get("relationship_type")
            != (edge.get("relation_type") if isinstance(edge, Mapping) else None)
            or relationship.get("status") != status
            or relationship.get("reason_codes") != expected_reasons
            or relationship.get("from_record_id")
            != (
                resolution.get("from_record_id")
                if isinstance(resolution, Mapping)
                else None
            )
            or relationship.get("to_record_id")
            != (
                resolution.get("to_record_id")
                if isinstance(resolution, Mapping)
                else None
            )
        ):
            errors.append("graph_edge_relationship_closure")
        if status == "valid" and (
            relationship.get("from_ref_sha256") is not None
            or relationship.get("to_ref_sha256") is not None
        ):
            errors.append("graph_edge_relationship_closure")
        if status != "valid" and isinstance(edge, Mapping) and (
            relationship.get("from_ref_sha256") != edge.get("source_sha256")
            or relationship.get("to_ref_sha256") != edge.get("target_sha256")
        ):
            errors.append("graph_edge_relationship_closure")

    for parent in records:
        if (
            not isinstance(parent, Mapping)
            or parent.get("category") != "knowledge.wiki_graph_edges"
            or parent.get("record_kind") != "physical_file"
        ):
            continue
        parse = parent.get("parse")
        graph = parse.get("graph") if isinstance(parse, Mapping) else None
        if graph is None:
            if isinstance(parse, Mapping) and parse.get("status") == "valid":
                errors.append("graph_parent_counts")
            continue
        child_rows = child_records_by_parent.get(str(parent.get("record_id")), ())
        node_rows = [
            row for row in child_rows if row.get("category") == "knowledge.wiki_graph_nodes"
        ]
        edge_rows = [
            row for row in child_rows if row.get("category") == "knowledge.wiki_graph_edges"
        ]
        status_counts = {
            key: sum(
                isinstance(row.get("graph_edge_resolution"), Mapping)
                and row["graph_edge_resolution"].get("status") == key
                for row in edge_rows
            )
            for key in ("duplicate", "legacy_auto", "orphan", "valid")
        }
        expected_graph = {
            **status_counts,
            "edges": len(edge_rows),
            "nodes": len(node_rows),
        }
        if (
            not isinstance(graph, Mapping)
            or graph != expected_graph
            or parse.get("child_count") != len(child_rows)
            or parse.get("record_count") != len(child_rows)
        ):
            errors.append("graph_parent_counts")

    # Markdown reference counters and the relationship ledger are two views
    # of the same facts and must reconcile exactly.
    asset_relationships = [
        row
        for row in relationship_rows
        if row.get("relationship_type") == "asset_reference"
    ]
    for relationship in asset_relationships:
        source = record_by_id.get(str(relationship.get("from_record_id") or ""))
        target = record_by_id.get(str(relationship.get("to_record_id") or ""))
        if (
            relationship.get("status") not in {"unavailable", "valid"}
            or not isinstance(source, Mapping)
            or source.get("category")
            not in {"knowledge.wiki_hidden_pages", "knowledge.wiki_sources"}
            or source.get("record_kind") != "physical_file"
            or not isinstance(target, Mapping)
            or target.get("category") != "knowledge.wiki_assets"
            or source.get("root_id") != target.get("root_id")
            or _record_owner_identity(source) != _record_owner_identity(target)
        ):
            errors.append("asset_reference_closure")
        elif relationship.get("status") == "valid" and (
            target.get("record_kind") != "physical_file"
            or not isinstance(target.get("sha256"), str)
        ):
            errors.append("asset_reference_closure")
        elif relationship.get("status") == "unavailable" and (
            target.get("record_kind") != "unavailable"
            or target.get("status") != "unavailable"
            or target.get("planned_action") != "report_only"
            or target.get("reason_codes") != ["missing_asset_reference"]
            or relationship.get("reason_codes") != ["missing_asset_reference"]
        ):
            errors.append("asset_reference_closure")
    for source in records:
        if (
            not isinstance(source, Mapping)
            or source.get("category")
            not in {"knowledge.wiki_hidden_pages", "knowledge.wiki_sources"}
            or source.get("record_kind") != "physical_file"
        ):
            continue
        rows = [
            row
            for row in relationships_by_from.get(str(source.get("record_id")), ())
            if row.get("relationship_type") == "asset_reference"
        ]
        expected_references = {
            "declared": len(rows),
            "missing": sum(row.get("status") == "unavailable" for row in rows),
            "resolved": sum(row.get("status") == "valid" for row in rows),
        }
        evidence = source.get("asset_reference_evidence")
        expected_target_hashes = sorted(
            hashlib.sha256(str(target.get("path_key")).encode("utf-8")).hexdigest()
            for row in rows
            for target in (record_by_id.get(str(row.get("to_record_id") or "")),)
            if isinstance(target, Mapping) and isinstance(target.get("path_key"), str)
        )
        if (
            source.get("references") != expected_references
            or not isinstance(evidence, Mapping)
            or set(evidence) != {"declared_path_sha256", "status", "syntax"}
            or evidence.get("syntax") != "known_producer_inline_markdown_v1"
            or not isinstance(evidence.get("declared_path_sha256"), list)
            or evidence.get("status") not in {"invalid", "valid"}
            or (
                evidence.get("status") == "valid"
                and len(evidence.get("declared_path_sha256")) != len(rows)
            )
            or evidence.get("declared_path_sha256")
            != sorted(evidence.get("declared_path_sha256"))
            or (
                evidence.get("status") == "valid"
                and evidence.get("declared_path_sha256")
                != expected_target_hashes
            )
            or (
                evidence.get("status") == "invalid"
                and (
                    evidence.get("declared_path_sha256") != []
                    or rows
                    or source.get("parse", {}).get("status") != "invalid"
                    or source.get("planned_action") != "quarantine_candidate"
                    or "asset_reference_parse_failed"
                    not in (source.get("reason_codes") or ())
                )
            )
            or any(
                not isinstance(value, str)
                or _LOWER_SHA256_RE.fullmatch(value) is None
                for value in evidence.get("declared_path_sha256", ())
            )
        ):
            errors.append("asset_reference_closure")
    for asset in records:
        if (
            not isinstance(asset, Mapping)
            or asset.get("category") != "knowledge.wiki_assets"
        ):
            continue
        incoming_valid = [
            row
            for row in relationships_by_to.get(str(asset.get("record_id")), ())
            if row.get("relationship_type") == "asset_reference"
            and row.get("status") == "valid"
        ]
        reasons = asset.get("reason_codes")
        if asset.get("record_kind") == "physical_file":
            has_unreferenced_reason = (
                isinstance(reasons, list) and "unreferenced_asset" in reasons
            )
            if (not incoming_valid) != has_unreferenced_reason:
                errors.append("asset_action_closure")
            if not incoming_valid and asset.get("planned_action") != "quarantine_candidate":
                errors.append("asset_action_closure")
            if (
                incoming_valid
                and asset.get("planned_action") == "import_referenced"
                and has_unreferenced_reason
            ):
                errors.append("asset_action_closure")

    # Session children are the only payload-safe reconciliation units.  Bind
    # their kind, ordinal and payload hashes back to the parent aggregates.
    session_categories = {"conversation.global_sessions", "conversation.user_sessions"}
    for parent in records:
        if (
            not isinstance(parent, Mapping)
            or parent.get("category") not in session_categories
            or parent.get("record_kind") != "physical_file"
        ):
            continue
        parse = parent.get("parse")
        child_counts = parse.get("child_counts") if isinstance(parse, Mapping) else None
        if child_counts is None:
            if isinstance(parse, Mapping) and parse.get("status") == "valid":
                errors.append("session_parent_closure")
            continue
        aggregate = parse.get("child_aggregate_sha256")
        child_rows = sorted(
            child_records_by_parent.get(str(parent.get("record_id")), ()),
            key=lambda row: int(row.get("source_ordinal") or 0),
        )
        expected_parse_keys = {
            "child_aggregate_sha256",
            "child_count",
            "child_counts",
            "logical_sha256",
            "record_count",
            "schema_version",
            "session_id_sha256",
            "status",
        }
        if (
            set(parse) != expected_parse_keys
            or not isinstance(child_counts, Mapping)
            or set(child_counts) != set(_SESSION_CHILD_KINDS)
            or not isinstance(aggregate, Mapping)
            or set(aggregate) != set(_SESSION_CHILD_KINDS)
            or child_counts.get("session_core_metadata") != 1
            or [row.get("source_ordinal") for row in child_rows]
            != list(range(1, len(child_rows) + 1))
        ):
            errors.append("session_parent_closure")
            continue
        payloads_by_kind: dict[str, list[str]] = {
            kind: [] for kind in _SESSION_CHILD_KINDS
        }
        last_kind_index = 0
        for child in child_rows:
            kind = child.get("child_kind")
            payload_sha256 = child.get("payload_sha256")
            if kind not in payloads_by_kind:
                errors.append("session_child_shape")
                continue
            kind_index = _SESSION_CHILD_KINDS.index(str(kind))
            if kind_index < last_kind_index:
                errors.append("session_child_shape")
            last_kind_index = kind_index
            if (
                child.get("category") != parent.get("category")
                or child.get("root_id") != parent.get("root_id")
                or child.get("relative_path") != parent.get("relative_path")
                or child.get("owner") != parent.get("owner")
                or not isinstance(payload_sha256, str)
                or _LOWER_SHA256_RE.fullmatch(payload_sha256) is None
                or child.get("sha256")
                != sha256_canonical(
                    {
                        "ordinal": child.get("source_ordinal"),
                        "shape": {
                            "child_kind": kind,
                            "payload_sha256": payload_sha256,
                        },
                        "source_sha256": parent.get("sha256"),
                    }
                )
            ):
                errors.append("session_child_binding")
            payloads_by_kind[str(kind)].append(str(payload_sha256))
        expected_counts = {
            kind: len(payloads_by_kind[kind]) for kind in _SESSION_CHILD_KINDS
        }
        expected_aggregate = {
            kind: sha256_canonical(payloads_by_kind[kind])
            for kind in _SESSION_CHILD_KINDS
        }
        if (
            child_counts != expected_counts
            or aggregate != expected_aggregate
            or parse.get("child_count") != len(child_rows)
            or parse.get("record_count") != len(child_rows)
            or parse.get("logical_sha256") != sha256_canonical(expected_aggregate)
        ):
            errors.append("session_parent_closure")
    for child in records:
        if (
            not isinstance(child, Mapping)
            or child.get("category") not in session_categories
            or child.get("record_kind") != "logical_record"
        ):
            continue
        parent = record_by_id.get(str(child.get("parent_record_id") or ""))
        if (
            not isinstance(parent, Mapping)
            or not isinstance(parent.get("parse"), Mapping)
            or "child_counts" not in parent["parse"]
        ):
            errors.append("session_parent_closure")

    # Registry logical rows are the only credential-free account authority in
    # the report.  Bind their exact hash-only shape to the physical registry.
    registry_children = [
        row
        for row in records
        if isinstance(row, Mapping)
        and row.get("category") == "identity.users_registry"
        and row.get("record_kind") == "logical_record"
    ]
    for child in registry_children:
        parent = record_by_id.get(str(child.get("parent_record_id") or ""))
        parse = child.get("parse")
        account = (
            parse.get("registry_account") if isinstance(parse, Mapping) else None
        )
        owner = child.get("owner")
        expected_user_hash = (
            hashlib.sha256(str(owner.get("claimed_user_id")).encode("utf-8")).hexdigest()
            if isinstance(owner, Mapping)
            and isinstance(owner.get("claimed_user_id"), str)
            else None
        )
        if (
            not isinstance(parent, Mapping)
            or parent.get("category") != "identity.users_registry"
            or not isinstance(account, Mapping)
            or set(account)
            != {"account_status", "payload_sha256", "role", "user_id_sha256"}
            or account.get("account_status") not in {"active", "disabled", "unknown"}
            or account.get("role") not in {"admin", "user", "unassigned_legacy", None}
            or not isinstance(account.get("payload_sha256"), str)
            or _LOWER_SHA256_RE.fullmatch(account.get("payload_sha256")) is None
            or account.get("user_id_sha256") != expected_user_hash
            or child.get("sha256")
            != sha256_canonical(
                {
                    "ordinal": child.get("source_ordinal"),
                    "shape": account,
                    "source_sha256": parent.get("sha256"),
                }
            )
        ):
            errors.append("registry_account_closure")
    for parent in records:
        if (
            not isinstance(parent, Mapping)
            or parent.get("category") != "identity.users_registry"
            or parent.get("record_kind") != "physical_file"
        ):
            continue
        parse = parent.get("parse")
        child_rows = sorted(
            child_records_by_parent.get(str(parent.get("record_id")), ()),
            key=lambda row: int(row.get("source_ordinal") or 0),
        )
        if isinstance(parse, Mapping) and parse.get("status") == "valid" and (
            parse.get("child_count") != len(child_rows)
            or parse.get("record_count") != len(child_rows)
            or parse.get("logical_sha256")
            != sha256_canonical([row.get("sha256") for row in child_rows])
        ):
            errors.append("registry_account_closure")

    # A job's normalized state is derivable from its frozen identity evidence;
    # a report cannot promote queued/running work by changing source_status.
    for job in records:
        if (
            not isinstance(job, Mapping)
            or job.get("category") != "operations.jobs"
            or job.get("record_kind") != "physical_file"
        ):
            continue
        parse = job.get("parse")
        if not isinstance(parse, Mapping):
            errors.append("job_semantic_closure")
            continue
        source_status = parse.get("source_status")
        identity = parse.get("job_identity")
        if source_status is None:
            # Invalid JSON/non-object inputs carry no interpreted authority.
            if identity is not None or parse.get("status") == "valid":
                errors.append("job_semantic_closure")
            if job.get("reason_codes") == ["job_record_not_object"] and (
                job.get("status") != "invalid"
                or job.get("planned_action") != "quarantine_candidate"
            ):
                errors.append("job_semantic_closure")
            continue
        if (
            source_status not in _JOB_STATUSES
            or parse.get("status") != "valid"
            or not isinstance(identity, Mapping)
            or set(identity) != {
                "aliases_consistent",
                "job_id_sha256",
                "kind",
                "owner_user_id_sha256",
                "payload_sha256",
            }
            or not isinstance(identity.get("aliases_consistent"), bool)
            or identity.get("kind") not in _JOB_KINDS | {"unknown"}
            or not isinstance(identity.get("payload_sha256"), str)
            or _LOWER_SHA256_RE.fullmatch(identity.get("payload_sha256")) is None
            or any(
                value is not None
                and (
                    not isinstance(value, str)
                    or _LOWER_SHA256_RE.fullmatch(value) is None
                )
                for value in (
                    identity.get("job_id_sha256"),
                    identity.get("owner_user_id_sha256"),
                    identity.get("payload_sha256"),
                )
            )
            or parse.get("logical_sha256")
            != sha256_canonical(
                {
                    "container_type": "dict",
                    "payload_sha256": identity.get("payload_sha256"),
                }
            )
        ):
            errors.append("job_semantic_closure")
            continue
        relative_path = str(job.get("relative_path") or "").replace("\\", "/")
        filename = relative_path.rsplit("/", 1)[-1]
        filename_stem = filename.rsplit(".", 1)[0]
        owner = job.get("owner")
        owner_user_id = owner.get("user_id") if isinstance(owner, Mapping) else None
        expected_identity_reasons: list[str] = []
        if identity.get("aliases_consistent") is not True:
            expected_identity_reasons.append("conflicting_job_identity_aliases")
        if identity.get("job_id_sha256") != hashlib.sha256(
            filename_stem.encode("utf-8")
        ).hexdigest():
            expected_identity_reasons.append("job_id_filename_mismatch")
        if identity.get("kind") == "unknown":
            expected_identity_reasons.append("unknown_job_kind")
        if (
            not isinstance(owner_user_id, str)
            or identity.get("owner_user_id_sha256")
            != hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()
        ):
            expected_identity_reasons.append("job_owner_mismatch")
        if source_status == "unknown":
            expected_identity_reasons.append("unknown_job_status")
        expected_identity_reasons = sorted(set(expected_identity_reasons))
        if expected_identity_reasons:
            expected_action = "quarantine_candidate"
            expected_status = "unknown"
            expected_reasons = expected_identity_reasons
        elif source_status in {"queued", "running"}:
            expected_action = "preserve_terminal_history"
            expected_status = "abandoned_legacy"
            expected_reasons = ["legacy_job_not_replayable"]
        else:
            expected_action = "preserve_terminal_history"
            expected_status = "active"
            expected_reasons = []
        if (
            job.get("planned_action") != expected_action
            or job.get("status") != expected_status
            or job.get("reason_codes") != expected_reasons
        ):
            errors.append("job_semantic_closure")
    category_by_id = {
        row.get("category"): row for row in categories if isinstance(row, Mapping)
    }
    if set(category_by_id) != CATEGORY_IDS or len(category_by_id) != len(categories):
        errors.append("category_set")
    for category, items in category_records.items():
        row = category_by_id.get(category)
        if not isinstance(row, Mapping) or row.get("records") != len(items):
            errors.append("category_count")
            continue
        if set(row) != _CATEGORY_ROW_KEYS:
            errors.append("category_shape")
        definition = category_definition(category)
        if any(row.get(key) != value for key, value in definition.items()):
            errors.append("category_definition")
        expected_maps: dict[str, dict[str, int]] = {
            "action_counts": {},
            "classification_authority_counts": {},
            "owner_kind_counts": {},
            "parse_status_counts": {},
            "record_kind_counts": {},
        }
        observed = {key: 0 for key in sorted(TERMINAL_CLASSIFICATIONS)}
        for item in items:
            terminal = item.get("proposed_terminal_classification")
            if terminal in observed:
                observed[str(terminal)] += 1
            parse = item.get("parse")
            owner = item.get("owner")
            values = {
                "action_counts": item.get("planned_action"),
                "classification_authority_counts": item.get("classification_authority"),
                "owner_kind_counts": owner.get("kind") if isinstance(owner, Mapping) else None,
                "parse_status_counts": parse.get("status") if isinstance(parse, Mapping) else None,
                "record_kind_counts": item.get("record_kind"),
            }
            for field, value in values.items():
                if not isinstance(value, str):
                    errors.append("category_record_shape")
                    continue
                counts = expected_maps[field]
                counts[value] = counts.get(value, 0) + 1
        if row.get("terminal_classification_counts") != observed:
            errors.append("category_terminal_counts")
        for field, counts in expected_maps.items():
            expected = {key: counts[key] for key in sorted(counts)}
            if row.get(field) != expected:
                errors.append(f"category_{field}")
        aggregate = sorted(
            [
                (
                item.get("record_id"),
                item.get("sha256"),
                item.get("size_bytes"),
                )
                for item in items
            ],
            key=lambda value: (
            str(value[0]),
            "" if value[1] is None else str(value[1]),
            str(value[2]),
            ),
        )
        if row.get("aggregate_sha256") != sha256_canonical(aggregate):
            errors.append("category_aggregate_hash")
        sizes = [item.get("size_bytes") for item in items]
        if any(
            value is not None
            and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            )
            for value in sizes
        ):
            errors.append("category_record_size")
            expected_bytes = None
        else:
            expected_bytes = sum(value or 0 for value in sizes)
        if row.get("bytes") != expected_bytes:
            errors.append("category_bytes")
        source_row_values = [
            item.get("parse", {}).get("record_count")
            for item in items
            if item.get("record_kind") == "physical_file"
            and isinstance(item.get("parse"), Mapping)
        ]
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for value in source_row_values
        ):
            errors.append("category_record_count")
            source_rows = None
        else:
            source_rows = sum(source_row_values)
        if row.get("source_rows") != source_rows:
            errors.append("category_source_rows")
        item_locations = {
            (str(item.get("root_id") or ""), str(item.get("relative_path") or ""))
            for item in items
        }
        expected_error_count = sum(
            isinstance(error, Mapping)
            and error.get("severity", "error") == "error"
            and (
                str(error.get("root_id") or ""),
                str(error.get("relative_path") or ""),
            )
            in item_locations
            for error in report_errors
        )
        if row.get("error_count") != expected_error_count:
            errors.append("category_error_count")
    if reconciliation.get("total_records") != len(records):
        errors.append("reconciliation_total")
    if reconciliation.get("proposed_classified_records") != len(records):
        errors.append("reconciliation_classified")
    if reconciliation.get("unclassified_records") != 0:
        errors.append("reconciliation_unclassified")
    if reconciliation.get("proposed_terminal_classification_counts") != terminal_counts:
        errors.append("reconciliation_terminal_counts")
    observed_authorities: dict[str, int] = {}
    observed_kinds: dict[str, int] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        for counts, value in (
            (observed_authorities, record.get("classification_authority")),
            (observed_kinds, record.get("record_kind")),
        ):
            if isinstance(value, str):
                counts[value] = counts.get(value, 0) + 1
    if reconciliation.get("classification_authority_counts") != {
        key: observed_authorities[key] for key in sorted(observed_authorities)
    }:
        errors.append("reconciliation_authority_counts")
    if reconciliation.get("record_kind_counts") != {
        key: observed_kinds[key] for key in sorted(observed_kinds)
    }:
        errors.append("reconciliation_record_kind_counts")
    if reconciliation.get("stage") != "inventory_proposal":
        errors.append("reconciliation_stage")
    if set(reconciliation) != {
        "classification_authority_counts",
        "final_plan_required",
        "proposed_classified_records",
        "proposed_terminal_classification_counts",
        "record_kind_counts",
        "stage",
        "total_records",
        "unclassified_records",
    }:
        errors.append("reconciliation_shape")
    if reconciliation.get("final_plan_required") != (
        observed_authorities.get("planner_required", 0) > 0
    ):
        errors.append("reconciliation_plan_flag")
    summary = report.get("summary")
    if not isinstance(summary, Mapping) or summary.get("records") != len(records):
        errors.append("summary_records")
    elif summary.get("relationships") != len(relationships):
        errors.append("summary_relationships")
    error_count = sum(
        isinstance(error, Mapping) and error.get("severity", "error") == "error"
        for error in report_errors
    )
    warning_count = sum(
        isinstance(error, Mapping) and error.get("severity", "error") != "error"
        for error in report_errors
    )
    if isinstance(summary, Mapping):
        if set(summary) != {
            "account_count",
            "bytes",
            "categories",
            "errors",
            "job_states",
            "owners",
            "records",
            "relationships",
            "user_count",
            "warnings",
        }:
            errors.append("summary_shape")
        if summary.get("errors") != error_count:
            errors.append("summary_errors")
        if summary.get("warnings") != warning_count:
            errors.append("summary_warnings")
        if summary.get("categories") != len(CATEGORY_IDS):
            errors.append("summary_categories")
        if summary.get("bytes") != sum(
            int(record.get("size_bytes") or 0)
            for record in records
            if isinstance(record, Mapping)
            and isinstance(record.get("size_bytes"), (int, type(None)))
            and not isinstance(record.get("size_bytes"), bool)
        ):
            errors.append("summary_bytes")
    if report.get("complete") is not (error_count == 0):
        errors.append("complete_error_coherence")

    run = report.get("run")
    if (
        not isinstance(run, Mapping)
        or set(run) != {"completed_at_utc", "run_id", "started_at_utc"}
        or not isinstance(run.get("run_id"), str)
        or re.fullmatch(r"run_[0-9a-f]{24}", run.get("run_id", "")) is None
        or any(
            not isinstance(run.get(field), str)
            or not _valid_canonical_utc(run[field])
            for field in ("completed_at_utc", "started_at_utc")
        )
    ):
        errors.append("run_shape")
    elif run["completed_at_utc"] < run["started_at_utc"]:
        # Canonical UTC timestamps share one fixed-width representation, so
        # lexical order is chronological order.
        errors.append("run_chronology")
    runtime = report.get("runtime")
    if (
        not isinstance(runtime, Mapping)
        or set(runtime) != {
            "app_revision",
            "python_implementation",
            "python_version",
            "sqlite_source_id",
            "sqlite_version",
        }
        or not all(
            isinstance(runtime.get(field), str) and bool(runtime.get(field))
            for field in (
                "app_revision",
                "python_implementation",
                "python_version",
                "sqlite_version",
            )
        )
        or (
            runtime.get("sqlite_source_id") is not None
            and not isinstance(runtime.get("sqlite_source_id"), str)
        )
    ):
        errors.append("runtime_shape")
    tool = report.get("tool")
    if (
        not isinstance(tool, Mapping)
        or set(tool) != {
            "code_revision",
            "code_tree_sha256",
            "mapping_sha256",
            "name",
            "version",
        }
        or tool.get("name") != TOOL_NAME
        or tool.get("version") != TOOL_VERSION
        or any(
            not isinstance(tool.get(field), str)
            or _LOWER_SHA256_RE.fullmatch(tool[field]) is None
            for field in (
                "code_revision",
                "code_tree_sha256",
                "mapping_sha256",
            )
        )
    ):
        errors.append("tool_shape")

    source_snapshot = report.get("source_snapshot")
    if not isinstance(source_snapshot, Mapping):
        errors.append("source_snapshot")
    else:
        if set(source_snapshot) != {
            "attestation_sha256",
            "created_at_utc",
            "high_water_marks",
            "kind",
            "roles",
            "root_binding_sha256",
            "roots",
            "scratch_root",
            "snapshot_id",
        }:
            errors.append("source_snapshot_shape")
        snapshot_id = source_snapshot.get("snapshot_id")
        snapshot_kind = source_snapshot.get("kind")
        snapshot_time = source_snapshot.get("created_at_utc")
        attestation = source_snapshot.get("attestation_sha256")
        roots = source_snapshot.get("roots")
        roles = source_snapshot.get("roles")
        high_water_marks = source_snapshot.get("high_water_marks")
        scratch_root = source_snapshot.get("scratch_root")
        if (
            not isinstance(snapshot_id, str)
            or not snapshot_id.strip()
            or snapshot_kind not in {"frozen_copy", "vss"}
            or not isinstance(snapshot_time, str)
            or not _valid_canonical_utc(snapshot_time)
            or not isinstance(attestation, str)
            or _LOWER_SHA256_RE.fullmatch(attestation) is None
            or not isinstance(roots, list)
            or not isinstance(roles, list)
            or not isinstance(high_water_marks, list)
        ):
            errors.append("source_snapshot_shape")
        else:
            root_ids: set[str] = set()
            root_roles: dict[str, list[str]] = {}
            for root in roots:
                if not _valid_root_fact(root):
                    errors.append("source_root_shape")
                    continue
                root_id = root.get("root_id")
                role = root.get("role")
                if root_id in root_ids:
                    errors.append("source_root_shape")
                    continue
                root_ids.add(root_id)
                root_roles.setdefault(role, []).append(root_id)
            root_path_pairs = [
                (
                    str(root.get("configured_path")),
                    str(root.get("resolved_path")),
                    str(root.get("role")),
                )
                for root in roots
                if isinstance(root, Mapping)
            ]
            for index, (configured, resolved, role) in enumerate(root_path_pairs):
                for other_configured, other_resolved, other_role in root_path_pairs[index + 1 :]:
                    # Physical legacy stores may deliberately be nested below
                    # the code root and are then sealed as explicit exclusions
                    # from code coverage.  Physical roots may never overlap
                    # one another.
                    if "legacy" in {role, other_role}:
                        continue
                    for left in (configured, resolved):
                        for right in (other_configured, other_resolved):
                            try:
                                common = os.path.normcase(os.path.commonpath((left, right)))
                            except ValueError:
                                continue
                            if common in {
                                os.path.normcase(left),
                                os.path.normcase(right),
                            }:
                                errors.append("source_root_overlap")
            role_by_name = {
                row.get("role"): row for row in roles if isinstance(row, Mapping)
            }
            if set(role_by_name) != set(_REQUIRED_SOURCE_ROLES) or len(role_by_name) != len(roles):
                errors.append("source_roles")
            else:
                for role in _REQUIRED_SOURCE_ROLES:
                    expected_ids = sorted(root_roles.get(role, ()))
                    expected_status = "present" if expected_ids else "missing"
                    if role_by_name[role] != {
                        "role": role,
                        "root_ids": expected_ids,
                        "status": expected_status,
                    }:
                        errors.append("source_role_binding")
                if report.get("complete") is True and any(
                    role_by_name[role].get("status") != "present"
                    for role in _REQUIRED_SOURCE_ROLES
                ):
                    errors.append("source_role_complete")
            expected_binding = sha256_canonical(
                {
                    "attestation_sha256": attestation,
                    "created_at_utc": snapshot_time,
                    "kind": snapshot_kind,
                    "roots": sorted(roots, key=lambda root: str(root.get("root_id"))),
                    "scratch_root": scratch_root,
                    "snapshot_id": snapshot_id,
                }
            )
            if source_snapshot.get("root_binding_sha256") != expected_binding:
                errors.append("source_root_binding")
            if (
                isinstance(run, Mapping)
                and _valid_canonical_utc(run.get("started_at_utc"))
            ):
                if snapshot_time > run["started_at_utc"]:
                    errors.append("source_snapshot_chronology")
                expected_run_id = "run_" + sha256_canonical(
                    {
                        "attestation_sha256": attestation,
                        "snapshot_id": snapshot_id,
                        "snapshot_kind": snapshot_kind,
                        "started_at_utc": run["started_at_utc"],
                    }
                )[:24]
                if run.get("run_id") != expected_run_id:
                    errors.append("run_binding")
            if not _valid_root_fact(scratch_root, scratch=True):
                errors.append("source_scratch_shape")
            else:
                scratch_paths = (
                    str(scratch_root.get("configured_path")),
                    str(scratch_root.get("resolved_path")),
                )
                for root_path in root_path_pairs:
                    for left in scratch_paths:
                        for right in root_path[:2]:
                            try:
                                common = os.path.normcase(os.path.commonpath((left, right)))
                            except ValueError:
                                continue
                            if common in {
                                os.path.normcase(left),
                                os.path.normcase(right),
                            }:
                                errors.append("source_scratch_overlap")
            for record in records:
                if isinstance(record, Mapping) and record.get("root_id") not in root_ids:
                    errors.append("record_root_binding")
            high_water_ids: set[str] = set()
            for mark in high_water_marks:
                if not isinstance(mark, Mapping):
                    errors.append("source_high_water_shape")
                    continue
                root_id = mark.get("root_id")
                before = mark.get("before")
                after = mark.get("after")
                changed = mark.get("changed")
                if (
                    root_id not in root_ids
                    or root_id in high_water_ids
                    or not _valid_high_water(before, code_root=root_roles.get("legacy") == [root_id])
                    or not _valid_high_water(after, code_root=root_roles.get("legacy") == [root_id])
                    or not isinstance(changed, bool)
                    or changed != (before != after)
                ):
                    errors.append("source_high_water_shape")
                elif report.get("complete") is True and changed:
                    errors.append("source_high_water_complete")
                if isinstance(root_id, str):
                    high_water_ids.add(root_id)
            if report.get("complete") is True and high_water_ids != root_ids:
                errors.append("source_high_water_set")

    code_coverage = report.get("code_coverage")
    run = report.get("run")
    is_inventory_proposal = (
        isinstance(run, Mapping)
        and isinstance(run.get("run_id"), str)
        and bool(run.get("run_id"))
    )
    if is_inventory_proposal and (
        not isinstance(code_coverage, Mapping)
        or code_coverage.get("status") != "complete"
    ):
        # A started inventory run may fail closed, but it may never omit the
        # G-02 code/callsite seal.  CLI argument/preflight error envelopes have
        # no run_id and intentionally retain the base-report ``skipped`` value.
        errors.append("code_coverage_shape")
    if isinstance(code_coverage, Mapping) and code_coverage.get("status") == "complete":
        code_summary = code_coverage.get("summary")
        code_files = code_coverage.get("files")
        code_callsites = code_coverage.get("callsites")
        code_errors = code_coverage.get("errors")
        code_mapping = code_coverage.get("mapping")
        source_fingerprint = code_coverage.get("source_fingerprint_sha256")
        mapping_sha256 = (
            code_mapping.get("sha256") if isinstance(code_mapping, Mapping) else None
        )
        if (
            set(code_coverage) != {
                "callsites",
                "errors",
                "files",
                "mapping",
                "report_schema",
                "source_fingerprint_sha256",
                "status",
                "summary",
                "unclassified_callsite_ids",
                "unmapped_submitter_ids",
            }
            or code_coverage.get("report_schema")
            != "code_assist.legacy_inventory.code_coverage.v1"
            or not isinstance(code_summary, Mapping)
            or set(code_summary) != {
                "discovered",
                "mapped",
                "pass",
                "retained_excluded",
                "submitter_count",
                "unclassified",
                "unmapped_submitters",
            }
            or code_summary.get("pass") is not True
            or any(
                not _valid_nonnegative_integer(code_summary.get(field))
                for field in (
                    "discovered",
                    "mapped",
                    "retained_excluded",
                    "submitter_count",
                    "unclassified",
                    "unmapped_submitters",
                )
            )
            or not isinstance(code_files, list)
            or not isinstance(code_callsites, list)
            or not isinstance(code_errors, list)
            or bool(code_errors)
            or not isinstance(source_fingerprint, str)
            or _LOWER_SHA256_RE.fullmatch(source_fingerprint) is None
            or not isinstance(mapping_sha256, str)
            or _LOWER_SHA256_RE.fullmatch(mapping_sha256) is None
            or not isinstance(code_mapping, Mapping)
            or set(code_mapping) != {
                "callsite_ids_sha256",
                "expected_callsite_ids_sha256",
                "expected_submitter_count",
                "path",
                "schema_version",
                "sha256",
                "unused_mapping_ids",
            }
            or code_mapping.get("schema_version") != 1
            or not _valid_positive_integer(
                code_mapping.get("expected_submitter_count")
            )
            or code_mapping.get("expected_submitter_count") != 8
            or not isinstance(code_mapping.get("path"), str)
            or not code_mapping.get("path")
        ):
            errors.append("code_coverage_shape")
        else:
            file_by_path: dict[str, Mapping[str, Any]] = {}
            for file in code_files:
                if (
                    not isinstance(file, Mapping)
                    or set(file) != {"language", "path", "sha256"}
                    or file.get("language") not in {"javascript", "python"}
                    or not isinstance(file.get("path"), str)
                    or not file.get("path")
                    or file.get("path") in file_by_path
                    or not isinstance(file.get("sha256"), str)
                    or _LOWER_SHA256_RE.fullmatch(file["sha256"]) is None
                ):
                    errors.append("code_file_shape")
                    continue
                file_by_path[file["path"]] = file
            expected_source_fingerprint = hashlib.sha256(
                json.dumps(
                    sorted(
                        [
                            [file["path"], file["sha256"]]
                            for file in code_files
                            if isinstance(file, Mapping)
                            and isinstance(file.get("path"), str)
                            and isinstance(file.get("sha256"), str)
                        ]
                    ),
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            if source_fingerprint != expected_source_fingerprint:
                errors.append("code_source_fingerprint")
            callsite_ids_digest = hashlib.sha256(
                json.dumps(
                    sorted(
                        str(callsite.get("callsite_id"))
                        for callsite in code_callsites
                        if isinstance(callsite, Mapping)
                        and isinstance(callsite.get("callsite_id"), str)
                    ),
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            if (
                not isinstance(code_mapping, Mapping)
                or code_mapping.get("callsite_ids_sha256") != callsite_ids_digest
                or code_mapping.get("expected_callsite_ids_sha256")
                != callsite_ids_digest
                or code_mapping.get("unused_mapping_ids") != []
                or code_coverage.get("unclassified_callsite_ids") != []
                or code_coverage.get("unmapped_submitter_ids") != []
            ):
                errors.append("code_mapping_binding")
            callsite_ids: set[str] = set()
            mapped = 0
            retained = 0
            submitters = 0
            unclassified = 0
            action_dispositions = {"migrate", "rebuild", "replace"}
            retained_dispositions = {
                "exclude_non_knowledge",
                "retain_compatibility",
                "retain_external",
            }
            for callsite in code_callsites:
                if not isinstance(callsite, Mapping):
                    errors.append("code_callsite_shape")
                    continue
                callsite_id = callsite.get("callsite_id")
                disposition = callsite.get("disposition")
                required_callsite_keys = {
                    "access",
                    "callee",
                    "callsite_id",
                    "column",
                    "disposition",
                    "file",
                    "file_sha256",
                    "kind",
                    "line",
                    "mapping_id",
                    "ordinal",
                    "reason",
                    "replacement_phase",
                    "scope_owner",
                    "symbol",
                }
                optional_callsite_keys = {"fragment_present", "query_present"}
                expected_callsite_id = None
                if all(
                    isinstance(callsite.get(field), str)
                    for field in ("file", "symbol", "callee")
                ) and _valid_positive_integer(callsite.get("ordinal")):
                    expected_callsite_id = "callsite-" + hashlib.sha256(
                        json.dumps(
                            [
                                callsite.get("file"),
                                callsite.get("symbol"),
                                callsite.get("callee"),
                                callsite.get("ordinal"),
                            ],
                            ensure_ascii=True,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    ).hexdigest()
                if (
                    not required_callsite_keys.issubset(callsite)
                    or not set(callsite).issubset(
                        required_callsite_keys | optional_callsite_keys
                    )
                    or not isinstance(callsite_id, str)
                    or callsite_id != expected_callsite_id
                    or callsite_id in callsite_ids
                    or callsite.get("file") not in file_by_path
                    or callsite.get("file_sha256")
                    != file_by_path.get(str(callsite.get("file")), {}).get("sha256")
                    or disposition
                    not in action_dispositions | retained_dispositions
                    or callsite.get("access")
                    not in {"read", "reference", "registration", "submit", "ui", "write"}
                    or not isinstance(callsite.get("callee"), str)
                    or not callsite.get("callee")
                    or callsite.get("kind") not in _CODE_CALLSITE_ACCESS
                    or callsite.get("access")
                    not in _CODE_CALLSITE_ACCESS.get(callsite.get("kind"), ())
                    or not isinstance(callsite.get("symbol"), str)
                    or not callsite.get("symbol")
                    or not _valid_positive_integer(callsite.get("line"))
                    or not _valid_nonnegative_integer(callsite.get("column"))
                    or not _valid_positive_integer(callsite.get("ordinal"))
                    or any(
                        not isinstance(callsite.get(field), str)
                        or not callsite.get(field)
                        for field in (
                            "mapping_id",
                            "reason",
                            "replacement_phase",
                            "scope_owner",
                        )
                    )
                    or any(
                        field in callsite
                        and not isinstance(callsite.get(field), bool)
                        for field in optional_callsite_keys
                    )
                ):
                    errors.append("code_callsite_shape")
                else:
                    callsite_ids.add(callsite_id)
                mapped += disposition in action_dispositions
                retained += disposition in retained_dispositions
                submitters += callsite.get("kind") == "job_submit"
                unclassified += disposition is None
            if (
                code_summary.get("discovered") != len(code_callsites)
                or code_summary.get("mapped") != mapped
                or code_summary.get("retained_excluded") != retained
                or code_summary.get("unclassified") != unclassified
                or code_summary.get("submitter_count") != submitters
                or code_summary.get("unmapped_submitters") != 0
                or len(code_callsites) != mapped + retained
                or code_mapping.get("expected_submitter_count") != submitters
                or code_callsites
                != sorted(code_callsites, key=lambda item: str(item.get("callsite_id")))
                or code_files
                != sorted(code_files, key=lambda item: str(item.get("path")))
            ):
                errors.append("code_summary_binding")
            expected_code_mark = {
                "directories": None,
                "entries": len(code_files) + 1,
                "files": len(code_files),
                "tree_sha256": sha256_canonical(
                    {
                        "mapping_sha256": mapping_sha256,
                        "source_fingerprint_sha256": source_fingerprint,
                    }
                ),
            }
            if (
                not isinstance(source_snapshot, Mapping)
                or not any(
                    isinstance(mark, Mapping)
                    and mark.get("root_id") == "legacy"
                    and mark.get("before") == expected_code_mark
                    and mark.get("after") == expected_code_mark
                    and mark.get("changed") is False
                    for mark in source_snapshot.get("high_water_marks", ())
                )
            ):
                errors.append("code_high_water_binding")
            runtime = report.get("runtime")
            tool = report.get("tool")
            if (
                not isinstance(runtime, Mapping)
                or runtime.get("app_revision") != source_fingerprint
                or not isinstance(tool, Mapping)
                or tool.get("mapping_sha256") != mapping_sha256
                or tool.get("code_tree_sha256") != expected_code_mark["tree_sha256"]
            ):
                errors.append("code_revision_binding")

    owners = report.get("owners")
    if not isinstance(owners, list):
        errors.append("owners_shape")
    else:
        owner_rows: dict[tuple[str, str | None], Mapping[str, Any]] = {}
        for owner in owners:
            if not isinstance(owner, Mapping):
                errors.append("owners_shape")
                continue
            if set(owner) != {
                "account_status",
                "claimed_user_ids",
                "kind",
                "mapping_status",
                "record_count",
                "registry_record_id",
                "role",
                "source_paths",
                "user_id",
            }:
                errors.append("owners_shape")
                continue
            key = (str(owner.get("kind") or ""), owner.get("user_id"))
            if not key[0] or key in owner_rows:
                errors.append("owners_identity")
            owner_rows[key] = owner
        expected_owner_records: dict[tuple[str, str | None], int] = {}
        expected_claims: dict[tuple[str, str | None], set[str]] = {}
        expected_paths: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
        expected_mapping_statuses: dict[tuple[str, str | None], set[str]] = {}
        for record in records:
            if not isinstance(record, Mapping) or not isinstance(record.get("owner"), Mapping):
                continue
            record_owner = record["owner"]
            key = (str(record_owner.get("kind") or ""), record_owner.get("user_id"))
            expected_owner_records[key] = expected_owner_records.get(key, 0) + 1
            mapping_status = record_owner.get("mapping_status")
            if isinstance(mapping_status, str):
                expected_mapping_statuses.setdefault(key, set()).add(mapping_status)
            claimed = record_owner.get("claimed_user_id")
            if isinstance(claimed, str):
                expected_claims.setdefault(key, set()).add(claimed)
            if record.get("entry_type") == "file":
                expected_paths.setdefault(key, []).append(
                    {
                        "category": record.get("category"),
                        "path_key": record.get("path_key"),
                        "root_id": record.get("root_id"),
                    }
                )
        if set(owner_rows) != set(expected_owner_records):
            errors.append("owners_set")
        for key, count in expected_owner_records.items():
            owner = owner_rows.get(key)
            if not isinstance(owner, Mapping):
                continue
            expected_source_paths = sorted(
                expected_paths.get(key, ()),
                key=lambda item: (
                    str(item["root_id"]),
                    str(item["path_key"]),
                    str(item["category"]),
                ),
            )
            if owner.get("record_count") != count:
                errors.append("owners_record_count")
            if owner.get("claimed_user_ids") != sorted(
                expected_claims.get(key, ()),
                key=lambda value: (unicodedata.normalize("NFKC", value).casefold(), value),
            ):
                errors.append("owners_claims")
            if owner.get("source_paths") != expected_source_paths:
                errors.append("owners_source_paths")
            statuses = expected_mapping_statuses.get(key, set())
            if len(statuses) != 1 or owner.get("mapping_status") not in statuses:
                errors.append("owners_mapping_status")
            is_account = isinstance(owner.get("registry_record_id"), str)
            if is_account:
                if (
                    key[0] != "personal"
                    or owner.get("mapping_status") != "confirmed"
                    or owner.get("account_status") not in {"active", "disabled"}
                    or owner.get("role") not in {"admin", "user", "unassigned_legacy"}
                    or not isinstance(owner.get("user_id"), str)
                    or not owner.get("user_id")
                ):
                    errors.append("owners_account_shape")
                registry_record = record_by_id.get(str(owner.get("registry_record_id")))
                registry_parse = (
                    registry_record.get("parse")
                    if isinstance(registry_record, Mapping)
                    else None
                )
                registry_account = (
                    registry_parse.get("registry_account")
                    if isinstance(registry_parse, Mapping)
                    else None
                )
                if (
                    not isinstance(registry_record, Mapping)
                    or registry_record.get("category") != "identity.users_registry"
                    or registry_record.get("record_kind") != "logical_record"
                    or _record_owner_identity(registry_record) != key
                    or not isinstance(registry_account, Mapping)
                    or registry_account.get("account_status") != owner.get("account_status")
                    or registry_account.get("role") != owner.get("role")
                ):
                    errors.append("owners_account_binding")
            elif owner.get("account_status") is not None or owner.get("role") is not None:
                errors.append("owners_account_shape")
        if isinstance(summary, Mapping) and summary.get("owners") != len(owners):
            errors.append("summary_owners")
        account_count = sum(
            isinstance(owner.get("registry_record_id"), str)
            for owner in owner_rows.values()
        )
        if isinstance(summary, Mapping) and (
            summary.get("account_count") != account_count
            or summary.get("user_count") != account_count
        ):
            errors.append("summary_accounts")
        expected_job_states: dict[str, int] = {}
        for record in records:
            if (
                isinstance(record, Mapping)
                and record.get("category") == "operations.jobs"
                and record.get("entry_type") == "file"
            ):
                parse = record.get("parse")
                status = (
                    str(parse.get("source_status") or "unknown")
                    if isinstance(parse, Mapping)
                    else "unknown"
                )
                expected_job_states[status] = expected_job_states.get(status, 0) + 1
        if isinstance(summary, Mapping) and summary.get("job_states") != {
            key: expected_job_states[key] for key in sorted(expected_job_states)
        }:
            errors.append("summary_job_states")
    if verify_manifest:
        digest = report.get("manifest_sha256")
        if not isinstance(digest, str) or digest != manifest_sha256(report):
            errors.append("manifest_hash")
    return tuple(sorted(set(errors)))


def inventory_contract_errors(
    report: Mapping[str, Any], *, verify_manifest: bool = True
) -> tuple[str, ...]:
    """Total validator for arbitrary JSON-shaped input.

    A migration verifier must fail closed on malformed, attacker-controlled
    reports; it must never leak a ``TypeError`` merely because an object or
    array appeared where a scalar identity was expected.
    """
    try:
        return _inventory_contract_errors_unchecked(
            report, verify_manifest=verify_manifest
        )
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ):
        return ("report_shape",)


__all__ = [
    "CATEGORY_DEFINITIONS",
    "CATEGORY_IDS",
    "ERROR_CODES",
    "HARD_ERROR_CODES",
    "REPORT_SCHEMA",
    "PLANNED_ACTIONS",
    "RECORD_KINDS",
    "SCHEMA_VERSION",
    "TOOL_NAME",
    "TOOL_VERSION",
    "TERMINAL_CLASSIFICATIONS",
    "canonical_json",
    "canonical_json_bytes",
    "category_definition",
    "classification_authority",
    "empty_category_rows",
    "json_safe",
    "has_hard_errors",
    "inventory_contract_errors",
    "manifest_payload",
    "manifest_sha256",
    "proposed_terminal_classification",
    "sha256_canonical",
]
