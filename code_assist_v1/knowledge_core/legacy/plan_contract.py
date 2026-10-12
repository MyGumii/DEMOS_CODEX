"""Strict, side-effect-free contract for P2.2 migration plan artifacts."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import re
import unicodedata
from typing import Any, Mapping, Sequence

from code_assist_v1.tools.legacy_inventory_contract import (
    CATEGORY_IDS,
    canonical_json as _canonical_json,
    canonical_json_bytes as _canonical_json_bytes,
    inventory_contract_errors,
    json_safe,
    sha256_canonical,
)


PLAN_REPORT_SCHEMA = "code-assist.legacy-migration-plan"
PLAN_SCHEMA_VERSION = 1
PLANNER_NAME = "legacy_migration_planner"
PLANNER_VERSION = "0.1.0"
PLAN_STAGES = frozenset({"migration_plan_draft", "reviewed_migration_plan"})
TERMINAL_CLASSIFICATIONS = frozenset(
    {"imported", "rebuilt", "retained_external", "quarantined", "unavailable"}
)
ACTIONS = frozenset(
    {"import", "rebuild", "retain_external", "quarantine", "record_unavailable"}
)
ACTION_TO_TERMINAL = {
    "import": "imported",
    "rebuild": "rebuilt",
    "retain_external": "retained_external",
    "quarantine": "quarantined",
    "record_unavailable": "unavailable",
}
EXECUTION_PHASES = frozenset({"P2.3", "P2.4", "P2.5", "P2.6", "none"})
IMPORT_MODES = frozenset(
    {
        "source_container",
        "user_account",
        "personal_item",
        "personal_asset",
        "legacy_history",
        "graph_node",
        "graph_edge",
        "finding",
        "conversation",
        "conversation_child",
        "notion_session_link",
        "job_history",
        "abandoned_legacy_job",
    }
)

ORGANIZATION_MAPPING_HEADER = (
    "mapping_schema_version",
    "source_owner_claim",
    "source_record_id",
    "decision",
    "user_id",
    "system_role",
    "account_status",
    "responsibility_code",
    "responsibility_id",
    "team_code",
    "team_id",
    "part_code",
    "part_id",
    "is_part_leader",
)

_IMPORT_CATEGORIES_P23 = frozenset(
    {
        "identity.users_registry",
        "knowledge.legacy_flat_markdown",
        "knowledge.wiki_sources",
        "knowledge.wiki_hidden_pages",
        "knowledge.wiki_assets",
        "knowledge.legacy_thoughts",
        "knowledge.legacy_inbox",
        "knowledge.legacy_raw",
        "knowledge.legacy_raw_chunks",
    }
)
_IMPORT_CATEGORIES_P24 = frozenset(
    {
        "identity.auth_audit",
        "knowledge.wiki_graph_nodes",
        "knowledge.wiki_graph_edges",
        "knowledge.wiki_candidates",
        "knowledge.wiki_log",
        "knowledge.legacy_edges",
        "knowledge.tombstones",
    }
)
_IMPORT_CATEGORIES_P25 = frozenset(
    {
        "conversation.user_sessions",
        "conversation.global_sessions",
        "external.notion_session_links",
        "operations.jobs",
    }
)
_REBUILD_CATEGORIES = frozenset(
    {
        "knowledge.wiki_index_markdown",
        "knowledge.wiki_index_sqlite",
        "knowledge.wiki_backlinks",
        "knowledge.wiki_health",
    }
)
_RETAIN_CATEGORIES = frozenset(
    {
        "context.memory",
        "context.memory_events",
        "context.project_sources",
        "context.project_chunks",
        "context.project_scores",
        "context.project_assets",
        "context.workspace_files",
        "context.user_skills",
        "external.notion_database",
        "external.notion_database_sidecars",
        "external.notion_files",
        "external.outlook_state",
    }
)
_QUARANTINE_CATEGORIES = frozenset(
    {
        "external.notion_backup_candidates",
        "operations.server_logs",
        "operations.temp_recovery",
        "unknown.unclassified",
    }
)
_UNAVAILABLE_CATEGORIES = frozenset({"unavailable.browser_pins"})

_PLANNER_PERSONAL_CONTENT_SUFFIXES = {
    "knowledge.legacy_flat_markdown": frozenset({".md"}),
    "knowledge.wiki_hidden_pages": frozenset({".md"}),
    "knowledge.legacy_thoughts": frozenset({".markdown", ".md"}),
    "knowledge.legacy_inbox": frozenset({".json"}),
    "knowledge.legacy_raw": frozenset(
        {".adoc", ".markdown", ".md", ".rst", ".text", ".txt"}
    ),
    "knowledge.legacy_raw_chunks": frozenset(
        {
            ".adoc",
            ".json",
            ".jsonl",
            ".markdown",
            ".md",
            ".rst",
            ".text",
            ".txt",
        }
    ),
}
_CANONICAL_LEGACY_GRAPH_EDGE_TYPES = frozenset(
    {"contradicts", "extends", "related", "supports"}
)

DECISION_TABLE = tuple(
    sorted(
        [
            *(
                {"action": "import", "category": value, "execution_phase": "P2.3"}
                for value in _IMPORT_CATEGORIES_P23
            ),
            *(
                {"action": "import", "category": value, "execution_phase": "P2.4"}
                for value in _IMPORT_CATEGORIES_P24
            ),
            *(
                {"action": "import", "category": value, "execution_phase": "P2.5"}
                for value in _IMPORT_CATEGORIES_P25
            ),
            *(
                {"action": "rebuild", "category": value, "execution_phase": "P2.6"}
                for value in _REBUILD_CATEGORIES
            ),
            *(
                {"action": "retain_external", "category": value, "execution_phase": "none"}
                for value in _RETAIN_CATEGORIES
            ),
            *(
                {"action": "quarantine", "category": value, "execution_phase": "none"}
                for value in _QUARANTINE_CATEGORIES
            ),
            *(
                {"action": "record_unavailable", "category": value, "execution_phase": "none"}
                for value in _UNAVAILABLE_CATEGORIES
            ),
        ],
        key=lambda row: row["category"],
    )
)
DECISION_TABLE_SHA256 = sha256_canonical(DECISION_TABLE)
if frozenset(row["category"] for row in DECISION_TABLE) != CATEGORY_IDS:
    raise RuntimeError("P2.2 decision table does not cover the inventory categories exactly")


def _compute_planner_code_revision() -> str:
    """Compute the reviewed planner+contract source fingerprint once."""

    from pathlib import Path

    contract_bytes = Path(__file__).read_bytes()
    planner_path = Path(__file__).with_name("planner.py")
    planner_bytes = planner_path.read_bytes()
    return sha256_canonical(
        {
            "contract_source_sha256": hashlib.sha256(contract_bytes).hexdigest(),
            "decision_table_sha256": DECISION_TABLE_SHA256,
            "planner_source_sha256": hashlib.sha256(planner_bytes).hexdigest(),
            "version": PLANNER_VERSION,
        }
    )


# Source provenance is captured at module load, then treated as immutable for
# the life of the imported implementation.  Contract validation and pure
# planning therefore perform no filesystem reads.
_EXPECTED_PLANNER_CODE_REVISION = _compute_planner_code_revision()


def expected_planner_code_revision() -> str:
    return _EXPECTED_PLANNER_CODE_REVISION

_DRAFT_KEYS = frozenset(
    {
        "report_schema",
        "schema_version",
        "stage",
        "planner",
        "inputs",
        "source_inventory",
        "source_snapshot",
        "organization",
        "owner_mappings",
        "records",
        "relationships",
        "reconciliation",
        "technical_plan_sha256",
    }
)
_REVIEWED_KEYS = frozenset(
    {"report_schema", "schema_version", "stage", "draft", "review", "plan_sha256"}
)
_PLANNER_KEYS = frozenset({"name", "version", "code_revision", "decision_table_sha256"})
_INPUT_KEYS = frozenset({"inventory", "organization_mapping"})
_INVENTORY_BINDING_KEYS = frozenset(
    {
        "document_sha256",
        "manifest_sha256",
        "report_schema",
        "schema_version",
        "tool_name",
        "tool_version",
        "code_revision",
        "mapping_sha256",
        "root_binding_sha256",
        "snapshot_attestation_sha256",
        "snapshot_id",
        "snapshot_kind",
        "high_water_marks_sha256",
        "record_count",
        "records_sha256",
        "relationship_count",
        "relationships_sha256",
    }
)
_MAPPING_BINDING_KEYS = frozenset(
    {"document_sha256", "mapping_schema_version", "row_count", "rows_sha256"}
)
_ORGANIZATION_KEYS = frozenset(
    {"responsibility_units", "team_units", "part_units", "aggregate_sha256"}
)
_OWNER_MAPPING_KEYS = frozenset(ORGANIZATION_MAPPING_HEADER[1:]) | frozenset({"mapping_id"})
_RECORD_KEYS = frozenset(
    {
        "action",
        "category",
        "execution_phase",
        "final_terminal_classification",
        "import_mode",
        "inventory_record_sha256",
        "owner_mapping_id",
        "reason_codes",
        "record_id",
        "relationship_ids",
        "resolved_owner_user_id",
        "source_record",
        "target_space_kind",
    }
)
_RELATIONSHIP_KEYS = frozenset(
    {
        "action",
        "execution_phase",
        "final_terminal_classification",
        "import_mode",
        "inventory_relationship_sha256",
        "reason_codes",
        "relationship_id",
        "source_relationship",
    }
)
_RECONCILIATION_KEYS = frozenset(
    {
        "action_counts",
        "category_counts",
        "classified_records",
        "execution_phase_counts",
        "import_mode_counts",
        "organization_sha256",
        "owner_mapping_count",
        "records_sha256",
        "relationships_sha256",
        "target_part_record_count",
        "terminal_classification_counts",
        "total_records",
        "total_relationships",
        "unclassified_records",
    }
)
_REVIEW_KEYS = frozenset(
    {
        "confirmed_technical_plan_sha256",
        "review_attestation_sha256",
        "reviewed_at_utc",
        "reviewer_id",
    }
)
_SOURCE_INVENTORY_KEYS = frozenset(
    {"metadata", "record_order", "relationship_order"}
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


def canonical_json_bytes(value: Any) -> bytes:
    return _canonical_json_bytes(value)


def canonical_json(value: Any) -> str:
    return _canonical_json(value)


canonical_plan_json_bytes = canonical_json_bytes
canonical_plan_json = canonical_json


def technical_plan_payload(draft: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(draft)
    payload.pop("technical_plan_sha256", None)
    return json_safe(payload)


def technical_plan_sha256(draft: Mapping[str, Any]) -> str:
    return sha256_canonical(technical_plan_payload(draft))


def reviewed_plan_payload(plan: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(plan)
    payload.pop("plan_sha256", None)
    return json_safe(payload)


def reviewed_plan_sha256(plan: Mapping[str, Any]) -> str:
    return sha256_canonical(reviewed_plan_payload(plan))


def _exact(value: Any, keys: frozenset[str]) -> bool:
    return isinstance(value, Mapping) and frozenset(value) == keys


def _sha(value: Any) -> bool:
    return isinstance(value, str) and _SHA_RE.fullmatch(value) is not None


def _identifier(value: Any, *, maximum: int = 128) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and value == value.strip()
        and unicodedata.normalize("NFKC", value) == value
        and all(
            ord(character) >= 32
            and character != "\x7f"
            and unicodedata.category(character) not in {"Cc", "Cf", "Cs", "Co", "Cn"}
            for character in value
        )
    )


def _valid_canonical_utc(value: Any) -> bool:
    """Accept only a real UTC instant in the frozen microsecond format."""

    if not isinstance(value, str) or _UTC_RE.fullmatch(value) is None:
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


def _count_map(value: Any, expected: Counter[str]) -> bool:
    return (
        isinstance(value, Mapping)
        and list(value) == sorted(value)
        and all(isinstance(key, str) and isinstance(count, int) and not isinstance(count, bool) and count >= 0 for key, count in value.items())
        and dict(value) == {key: expected[key] for key in sorted(expected)}
    )


def _sorted_unique_strings(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(_identifier(item, maximum=256) for item in value)
        and value == sorted(set(value))
    )


_SOURCE_RECORD_REQUIRED_KEYS = frozenset(
    {
        "category", "classification_authority", "classification_reason_codes",
        "entry_type", "owner", "parent_record_id", "parse", "path_key",
        "planned_action", "reason_codes", "record_id", "record_kind",
        "references", "relative_path", "root_id", "sha256", "size_bytes",
        "source_class", "source_identity", "source_ordinal", "stat", "status",
        "proposed_terminal_classification",
    }
)
_SOURCE_RECORD_OPTIONAL_KEYS = frozenset(
    {
        "asset_reference_evidence", "candidate", "child_kind", "graph_edge",
        "graph_edge_resolution", "graph_node", "graph_node_resolution",
        "exact_source_duplicate_record_ids", "job_identity", "legacy_edge",
        "link_resolution", "notion_link", "payload_sha256", "registry_account",
    }
)
_SOURCE_RELATIONSHIP_KEYS = frozenset(
    {
        "from_record_id", "from_ref_sha256", "reason_codes", "relationship_id",
        "relationship_type", "source_record_id", "status", "to_record_id",
        "to_ref_sha256",
    }
)


def _sanitized_source_record(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    keys = frozenset(value)
    if not _SOURCE_RECORD_REQUIRED_KEYS.issubset(keys) or not keys.issubset(
        _SOURCE_RECORD_REQUIRED_KEYS | _SOURCE_RECORD_OPTIONAL_KEYS
    ):
        return False
    try:
        encoded = canonical_json_bytes(value)
    except (TypeError, ValueError, RecursionError):
        return False
    # P2.1 never emits raw bodies or credential/token field names.  This is a
    # second defence after its exact contract, not a substitute for hashing.
    forbidden_keys = {"body", "content", "password", "password_hash", "raw", "token", "secret"}
    forbidden_fragments = {"credential", "password", "secret", "token"}
    def walk(item: Any) -> bool:
        if isinstance(item, Mapping):
            for key, child in item.items():
                folded = unicodedata.normalize("NFKC", key).casefold()
                if (
                    folded in forbidden_keys
                    or any(fragment in folded for fragment in forbidden_fragments)
                    or folded.startswith(("body_", "content_", "raw_"))
                    or folded.endswith(("_body", "_content"))
                ):
                    return False
                if not walk(child):
                    return False
            return True
        if isinstance(item, list):
            return all(walk(child) for child in item)
        return True
    return len(encoded) <= 16 * 1024 * 1024 and walk(value)


def _sanitized_source_relationship(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and frozenset(value).issubset(_SOURCE_RELATIONSHIP_KEYS)
        and {"relationship_id", "relationship_type", "status", "reason_codes"}.issubset(value)
    )


def _record_import_mode(source: Mapping[str, Any]) -> str:
    category = source.get("category")
    kind = source.get("record_kind")
    if kind == "physical_file" and category in {
        "identity.users_registry", "identity.auth_audit",
        "knowledge.wiki_graph_nodes", "knowledge.wiki_graph_edges",
        "knowledge.wiki_candidates", "knowledge.legacy_edges",
        "conversation.user_sessions", "conversation.global_sessions",
        "external.notion_session_links",
    }:
        return "source_container"
    if category == "identity.users_registry":
        return "user_account"
    if category in {
        "knowledge.legacy_flat_markdown", "knowledge.wiki_sources",
        "knowledge.wiki_hidden_pages", "knowledge.legacy_thoughts",
        "knowledge.legacy_inbox", "knowledge.legacy_raw", "knowledge.legacy_raw_chunks",
    }:
        return "personal_item"
    if category == "knowledge.wiki_assets":
        return "personal_asset"
    if category == "knowledge.wiki_graph_nodes":
        return "graph_node"
    if category in {"knowledge.wiki_graph_edges", "knowledge.legacy_edges"}:
        return "graph_edge"
    if category == "knowledge.wiki_candidates":
        return "finding"
    if category in {"conversation.user_sessions", "conversation.global_sessions"}:
        return "conversation_child" if kind == "logical_record" else "conversation"
    if category == "external.notion_session_links":
        return "notion_session_link"
    if category == "operations.jobs":
        parse = source.get("parse")
        status = parse.get("source_status") if isinstance(parse, Mapping) else None
        return "abandoned_legacy_job" if status in {"queued", "running", "unknown"} else "job_history"
    return "legacy_history"


def _planner_resolvable_personal_content(source: Mapping[str, Any]) -> bool:
    """Return whether P2.2 may resolve one scanner planner-required record.

    This is deliberately narrower than the category decision table.  It does
    not promote unreadable, ambiguous, missing-owner, binary, malformed JSON,
    or otherwise reason-bearing records.  Owner/organization approval is
    checked independently by the reviewed mapping contract.
    """

    category = source.get("category")
    relative_path = source.get("relative_path")
    parse = source.get("parse")
    references = source.get("references")
    evidence = source.get("asset_reference_evidence")
    if (
        category not in _PLANNER_PERSONAL_CONTENT_SUFFIXES
        or not isinstance(relative_path, str)
        or not any(
            relative_path.casefold().endswith(suffix)
            for suffix in _PLANNER_PERSONAL_CONTENT_SUFFIXES[str(category)]
        )
        or source.get("classification_authority") != "planner_required"
        or source.get("proposed_terminal_classification") != "quarantined"
        or source.get("record_kind") != "physical_file"
        or source.get("entry_type") != "file"
        or source.get("source_ordinal") != 0
        or source.get("status") != "active"
        or source.get("reason_codes") != []
        or not _sha(source.get("sha256"))
        or not isinstance(source.get("size_bytes"), int)
        or isinstance(source.get("size_bytes"), bool)
        or source.get("size_bytes", -1) < 0
    ):
        return False
    suffix = "." + relative_path.rsplit(".", 1)[-1].casefold()
    if suffix in {".json", ".jsonl"} and (
        not isinstance(parse, Mapping) or parse.get("status") != "valid"
    ):
        return False
    if category == "knowledge.wiki_hidden_pages" and (
        not isinstance(references, Mapping)
        or references.get("missing") != 0
        or not isinstance(evidence, Mapping)
        or evidence.get("status") != "valid"
    ):
        return False
    return True


def _planner_importable_p24_record(source: Mapping[str, Any]) -> bool:
    """Fail closed where the legacy enum is broader than canonical v1."""

    category = source.get("category")
    if source.get("record_kind") != "logical_record":
        return True
    if category == "knowledge.wiki_graph_edges":
        edge = source.get("graph_edge")
        resolution = source.get("graph_edge_resolution")
        return (
            isinstance(edge, Mapping)
            and edge.get("relation_type") in _CANONICAL_LEGACY_GRAPH_EDGE_TYPES
            and isinstance(resolution, Mapping)
            and resolution.get("status") == "valid"
            and source.get("status") == "active"
            and source.get("reason_codes") == []
        )
    if category == "knowledge.wiki_candidates":
        candidate = source.get("candidate")
        if not isinstance(candidate, Mapping):
            return False
        resolution = candidate.get("resolution")
        pair = (candidate.get("kind"), candidate.get("action"))
        return (
            pair in {("graph_candidate", "add_edge"), ("wiki_diff", "create")}
            and candidate.get("status") == "pending"
            and candidate.get("base_or_revision_evidence") is True
            and candidate.get("identity_evidence") is True
            and isinstance(resolution, Mapping)
            and resolution.get("status") == "resolved"
            and source.get("status") == "active"
            and source.get("reason_codes") == []
        )
    return True


def _expected_record_policy(source: Mapping[str, Any]) -> tuple[str, str, str | None]:
    category = source.get("category")
    decision = next((row for row in DECISION_TABLE if row["category"] == category), None)
    if decision is None:
        return "quarantine", "none", None
    proposed = source.get("proposed_terminal_classification")
    if _planner_resolvable_personal_content(source):
        return "import", "P2.3", "personal_item"
    if (
        source.get("category") == "knowledge.wiki_candidates"
        and source.get("record_kind") == "logical_record"
        and _planner_importable_p24_record(source)
    ):
        return "import", "P2.4", "finding"
    fixed = {
        "rebuilt": ("rebuild", "P2.6", None),
        "retained_external": ("retain_external", "none", None),
        "quarantined": ("quarantine", "none", None),
        "unavailable": ("record_unavailable", "none", None),
    }
    if proposed in fixed:
        return fixed[str(proposed)]
    action = str(decision["action"])
    phase = str(decision["execution_phase"])
    if action == "import" and phase == "P2.4" and not _planner_importable_p24_record(source):
        return "quarantine", "none", None
    return action, phase, _record_import_mode(source) if action == "import" else None


def _record_requires_personal_target(source: Mapping[str, Any]) -> bool:
    category = source.get("category")
    owner = source.get("owner")
    if category in {
        "conversation.user_sessions", "conversation.global_sessions",
        "external.notion_session_links", "operations.jobs",
    }:
        return True
    if isinstance(category, str) and category.startswith("knowledge."):
        # Derived rows are rebuilt and system projection/container rows do not
        # become personal canonical items.
        return _expected_record_policy(source)[0] == "import"
    return (
        category == "identity.users_registry"
        and source.get("record_kind") == "logical_record"
        and isinstance(owner, Mapping)
        and owner.get("kind") == "personal"
    )


def _reviewed_pre_role_account_policy(
    source: Mapping[str, Any], mapping: Mapping[str, Any] | None
) -> tuple[str, str, str | None] | None:
    parse = source.get("parse")
    account = parse.get("registry_account") if isinstance(parse, Mapping) else None
    if (
        source.get("category") == "identity.users_registry"
        and source.get("record_kind") == "logical_record"
        and source.get("classification_authority") == "planner_required"
        and source.get("proposed_terminal_classification") == "quarantined"
        and source.get("reason_codes") == ["legacy_role_requires_plan"]
        and source.get("status") == "active"
        and isinstance(parse, Mapping)
        and parse.get("status") == "valid"
        and isinstance(account, Mapping)
        and account.get("role") == "unassigned_legacy"
        and isinstance(mapping, Mapping)
        and mapping.get("decision") == "personal"
        and mapping.get("system_role") in {"admin", "user"}
    ):
        return "import", "P2.3", "user_account"
    return None


def _expected_relationship_policy(
    source: Mapping[str, Any], planned_records: Mapping[str, Mapping[str, Any]]
) -> tuple[str, str, str | None]:
    endpoints = [
        source.get(key)
        for key in ("from_record_id", "source_record_id", "to_record_id")
        if source.get(key) is not None
    ]
    endpoint_rows = [planned_records.get(str(item)) for item in endpoints]
    personal_users = {
        row.get("resolved_owner_user_id")
        for row in endpoint_rows
        if isinstance(row, Mapping) and row.get("target_space_kind") == "personal"
    }
    if source.get("status") != "valid" or not endpoints or any(not isinstance(row, Mapping) or row.get("action") != "import" for row in endpoint_rows) or len(personal_users) > 1:
        return "quarantine", "none", None
    relation = source.get("relationship_type")
    if relation == "asset_reference":
        return "import", "P2.3", "personal_asset"
    if relation == "notion_session_page":
        return "import", "P2.5", "notion_session_link"
    if relation in {"candidate_source", "candidate_target"}:
        return "import", "P2.4", "finding"
    if relation == "graph_node_source":
        return "import", "P2.4", "graph_node"
    if relation in _CANONICAL_LEGACY_GRAPH_EDGE_TYPES:
        return "import", "P2.4", "graph_edge"
    if relation == "exact_content_duplicate":
        return "import", "P2.4", "legacy_history"
    return "quarantine", "none", None


def _validate_organization(value: Any, errors: list[str]) -> None:
    if not _exact(value, _ORGANIZATION_KEYS):
        errors.append("organization_shape")
        return
    responsibilities = value["responsibility_units"]
    teams = value["team_units"]
    parts = value["part_units"]
    if not all(isinstance(rows, list) for rows in (responsibilities, teams, parts)):
        errors.append("organization_rows")
        return
    responsibility_ids: set[str] = set()
    for row in responsibilities:
        if not _exact(row, frozenset({"code", "id"})) or not _identifier(row.get("code"), maximum=64) or not isinstance(row.get("id"), str) or not _UUID_RE.fullmatch(row["id"]):
            errors.append("responsibility_shape")
            continue
        responsibility_ids.add(row["id"])
    team_ids: set[str] = set()
    for row in teams:
        if not _exact(row, frozenset({"code", "id", "responsibility_id"})) or not _identifier(row.get("code"), maximum=64) or not isinstance(row.get("id"), str) or not _UUID_RE.fullmatch(row["id"]) or row.get("responsibility_id") not in responsibility_ids:
            errors.append("team_shape")
            continue
        team_ids.add(row["id"])
    part_ids: set[str] = set()
    for row in parts:
        if not _exact(row, frozenset({"code", "id", "leader_user_id", "team_id"})) or not _identifier(row.get("code"), maximum=64) or not isinstance(row.get("id"), str) or not _UUID_RE.fullmatch(row["id"]) or row.get("team_id") not in team_ids or (row.get("leader_user_id") is not None and (not isinstance(row["leader_user_id"], str) or not _USER_RE.fullmatch(row["leader_user_id"]))):
            errors.append("part_shape")
            continue
        part_ids.add(row["id"])
    for rows, key in ((responsibilities, "id"), (teams, "id"), (parts, "id")):
        if rows != sorted(rows, key=lambda row: row.get(key, "") if isinstance(row, Mapping) else ""):
            errors.append("organization_order")
    all_ids = [row.get("id") for rows in (responsibilities, teams, parts) for row in rows if isinstance(row, Mapping)]
    if len(all_ids) != len(set(all_ids)):
        errors.append("organization_duplicate_id")
    aggregate = {key: value[key] for key in ("responsibility_units", "team_units", "part_units")}
    if value.get("aggregate_sha256") != sha256_canonical(aggregate):
        errors.append("organization_hash")


def _validate_owner_mappings(value: Any, organization: Any, errors: list[str]) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list):
        errors.append("owner_mappings_shape")
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    claim_selectors: set[str] = set()
    record_selectors: set[str] = set()
    active_admins = 0
    part_leaders: Counter[str] = Counter()
    organization_parts = {
        row.get("id")
        for row in organization.get("part_units", [])
        if isinstance(organization, Mapping) and isinstance(row, Mapping)
    }
    responsibility_by_id = {
        row.get("id"): row
        for row in organization.get("responsibility_units", [])
        if isinstance(organization, Mapping) and isinstance(row, Mapping)
    }
    team_by_id = {
        row.get("id"): row
        for row in organization.get("team_units", [])
        if isinstance(organization, Mapping) and isinstance(row, Mapping)
    }
    part_by_id = {
        row.get("id"): row
        for row in organization.get("part_units", [])
        if isinstance(organization, Mapping) and isinstance(row, Mapping)
    }
    code_to_id: dict[tuple[str, str], str] = {}
    id_to_code: dict[tuple[str, str], str] = {}
    for row in value:
        if not _exact(row, _OWNER_MAPPING_KEYS):
            errors.append("owner_mapping_shape")
            continue
        mapping_id = row.get("mapping_id")
        claim = row.get("source_owner_claim")
        record_id = row.get("source_record_id")
        if not _identifier(mapping_id, maximum=96) or not str(mapping_id).startswith("map_"):
            errors.append("owner_mapping_id")
            continue
        if mapping_id in result:
            errors.append("owner_mapping_duplicate")
        result[str(mapping_id)] = row
        if (claim is None) == (record_id is None):
            errors.append("owner_mapping_selector")
        if claim is not None:
            normalized_claim = unicodedata.normalize("NFKC", str(claim)).casefold()
            if not _identifier(claim, maximum=64) or normalized_claim in claim_selectors:
                errors.append("owner_claim_selector")
            claim_selectors.add(normalized_claim)
        if record_id is not None:
            if not _identifier(record_id, maximum=64) or record_id in record_selectors:
                errors.append("owner_record_selector")
            record_selectors.add(str(record_id))
        decision = row.get("decision")
        if decision not in {"personal", "quarantine"}:
            errors.append("owner_mapping_decision")
            continue
        target_fields = ("user_id", "system_role", "account_status", "responsibility_code", "responsibility_id", "team_code", "team_id", "part_code", "part_id")
        if decision == "quarantine":
            if any(row.get(key) is not None for key in target_fields) or row.get("is_part_leader") is not False:
                errors.append("quarantine_mapping_target")
            continue
        user_id = row.get("user_id")
        if not isinstance(user_id, str) or not _USER_RE.fullmatch(user_id):
            errors.append("mapping_user_id")
        if row.get("system_role") not in {"admin", "user"} or row.get("account_status") not in {"active", "disabled"}:
            errors.append("mapping_account")
        org_values = [row.get(key) for key in ("responsibility_code", "responsibility_id", "team_code", "team_id", "part_code", "part_id")]
        if not (all(item is None for item in org_values) or all(item is not None for item in org_values)):
            errors.append("mapping_org_chain")
        assigned = all(item is not None for item in org_values)
        if assigned:
            for code in org_values[::2]:
                if not _identifier(code, maximum=64):
                    errors.append("mapping_org_code")
            for identity in org_values[1::2]:
                if not isinstance(identity, str) or not _UUID_RE.fullmatch(identity):
                    errors.append("mapping_org_id")
            if row.get("part_id") not in organization_parts:
                errors.append("mapping_part_missing")
            responsibility = responsibility_by_id.get(row.get("responsibility_id"))
            team = team_by_id.get(row.get("team_id"))
            part = part_by_id.get(row.get("part_id"))
            if (
                not isinstance(responsibility, Mapping)
                or responsibility.get("code") != row.get("responsibility_code")
                or not isinstance(team, Mapping)
                or team.get("code") != row.get("team_code")
                or team.get("responsibility_id") != row.get("responsibility_id")
                or not isinstance(part, Mapping)
                or part.get("code") != row.get("part_code")
                or part.get("team_id") != row.get("team_id")
            ):
                errors.append("mapping_org_binding")
            for kind, code, identity in (
                ("responsibility", row.get("responsibility_code"), row.get("responsibility_id")),
                ("team", row.get("team_code"), row.get("team_id")),
                ("part", row.get("part_code"), row.get("part_id")),
            ):
                code_key = (kind, str(code).casefold())
                id_key = (kind, str(identity))
                if code_key in code_to_id and code_to_id[code_key] != identity:
                    errors.append("mapping_org_bijection")
                if id_key in id_to_code and id_to_code[id_key].casefold() != str(code).casefold():
                    errors.append("mapping_org_bijection")
                code_to_id[code_key] = str(identity)
                id_to_code[id_key] = str(code)
        if row.get("account_status") == "disabled" and (assigned or row.get("is_part_leader") is True):
            errors.append("disabled_mapping_assigned")
        if row.get("is_part_leader") not in {True, False} or (row.get("is_part_leader") is True and not assigned):
            errors.append("mapping_leader_shape")
        if row.get("is_part_leader") is True:
            part_leaders[str(row.get("part_id"))] += 1
        if row.get("system_role") == "admin" and row.get("account_status") == "active":
            active_admins += 1
        expected_id = "map_" + sha256_canonical({key: row[key] for key in sorted(_OWNER_MAPPING_KEYS - {"mapping_id"})})[:24]
        if mapping_id != expected_id:
            errors.append("owner_mapping_hash")
    if value != sorted(value, key=lambda row: row.get("mapping_id", "") if isinstance(row, Mapping) else ""):
        errors.append("owner_mapping_order")
    if any(count > 1 for count in part_leaders.values()):
        errors.append("part_multiple_leaders")
    if active_admins < 1:
        errors.append("active_admin_missing")
    expected_leaders = {
        part_id: next(
            (
                row.get("user_id")
                for row in value
                if isinstance(row, Mapping)
                and row.get("decision") == "personal"
                and row.get("part_id") == part_id
                and row.get("is_part_leader") is True
            ),
            None,
        )
        for part_id in part_by_id
    }
    if any(part_by_id[part_id].get("leader_user_id") != leader for part_id, leader in expected_leaders.items()):
        errors.append("organization_leader_binding")
    return result


def _validate_draft(draft: Mapping[str, Any], errors: list[str], *, verify_hash: bool) -> None:
    if frozenset(draft) != _DRAFT_KEYS:
        errors.append("draft_shape")
        return
    if draft.get("report_schema") != PLAN_REPORT_SCHEMA or draft.get("schema_version") != PLAN_SCHEMA_VERSION or draft.get("stage") != "migration_plan_draft":
        errors.append("draft_header")
    planner = draft.get("planner")
    if not _exact(planner, _PLANNER_KEYS) or planner.get("name") != PLANNER_NAME or planner.get("version") != PLANNER_VERSION or not _sha(planner.get("code_revision")) or planner.get("code_revision") != expected_planner_code_revision() or planner.get("decision_table_sha256") != DECISION_TABLE_SHA256:
        errors.append("planner_shape")
    inputs = draft.get("inputs")
    if not _exact(inputs, _INPUT_KEYS):
        errors.append("inputs_shape")
        inventory_binding = mapping_binding = {}
    else:
        inventory_binding = inputs["inventory"]
        mapping_binding = inputs["organization_mapping"]
        if not _exact(inventory_binding, _INVENTORY_BINDING_KEYS) or not all(_sha(inventory_binding.get(key)) for key in ("document_sha256", "manifest_sha256", "code_revision", "mapping_sha256", "root_binding_sha256", "snapshot_attestation_sha256", "high_water_marks_sha256", "records_sha256", "relationships_sha256")) or any(not isinstance(inventory_binding.get(key), int) or isinstance(inventory_binding.get(key), bool) or inventory_binding.get(key, -1) < 0 for key in ("record_count", "relationship_count")):
            errors.append("inventory_binding")
        if not _exact(mapping_binding, _MAPPING_BINDING_KEYS) or not _sha(mapping_binding.get("document_sha256")) or not _sha(mapping_binding.get("rows_sha256")) or mapping_binding.get("mapping_schema_version") != 1 or not isinstance(mapping_binding.get("row_count"), int) or isinstance(mapping_binding.get("row_count"), bool) or mapping_binding.get("row_count", -1) < 0:
            errors.append("mapping_binding")
    source_inventory = draft.get("source_inventory")
    if not _exact(source_inventory, _SOURCE_INVENTORY_KEYS):
        errors.append("source_inventory_shape")
        source_inventory = {}
    source_inventory_metadata = source_inventory.get("metadata")
    source_record_order = source_inventory.get("record_order")
    source_relationship_order = source_inventory.get("relationship_order")
    if (
        not isinstance(source_inventory_metadata, Mapping)
        or {"records", "relationships"} & set(source_inventory_metadata)
        or not isinstance(source_record_order, list)
        or not all(isinstance(identity, str) for identity in source_record_order)
        or len(source_record_order) != len(set(source_record_order))
        or not isinstance(source_relationship_order, list)
        or not all(isinstance(identity, str) for identity in source_relationship_order)
        or len(source_relationship_order) != len(set(source_relationship_order))
    ):
        errors.append("source_inventory_shape")
        source_inventory_metadata = {}
        source_record_order = []
        source_relationship_order = []

    snapshot = draft.get("source_snapshot")
    if not isinstance(snapshot, Mapping):
        errors.append("source_snapshot_shape")
    elif isinstance(inventory_binding, Mapping):
        expected_snapshot_keys = {
            "attestation_sha256", "created_at_utc", "high_water_marks", "kind",
            "roles", "root_binding_sha256", "roots", "scratch_root", "snapshot_id",
        }
        roots = snapshot.get("roots")
        scratch = snapshot.get("scratch_root")
        root_binding = sha256_canonical(
            {
                "attestation_sha256": snapshot.get("attestation_sha256"),
                "created_at_utc": snapshot.get("created_at_utc"),
                "kind": snapshot.get("kind"),
                "roots": sorted(roots, key=lambda row: str(row.get("root_id"))) if isinstance(roots, list) and all(isinstance(row, Mapping) for row in roots) else None,
                "scratch_root": scratch,
                "snapshot_id": snapshot.get("snapshot_id"),
            }
        )
        if set(snapshot) != expected_snapshot_keys or not isinstance(roots, list) or not roots or not all(
            isinstance(row, Mapping)
            and set(row) == {"configured_path", "filesystem_identity", "path_source", "resolved_path", "role", "root_id", "reparse", "volume"}
            and isinstance(row.get("configured_path"), str)
            and isinstance(row.get("resolved_path"), str)
            and isinstance(row.get("root_id"), str)
            and isinstance(row.get("role"), str)
            and isinstance(row.get("reparse"), Mapping)
            and row["reparse"].get("present") is False
            and row["reparse"].get("components") == []
            for row in roots
        ) or root_binding != snapshot.get("root_binding_sha256") or snapshot.get("root_binding_sha256") != inventory_binding.get("root_binding_sha256") or snapshot.get("attestation_sha256") != inventory_binding.get("snapshot_attestation_sha256") or snapshot.get("snapshot_id") != inventory_binding.get("snapshot_id") or snapshot.get("kind") != inventory_binding.get("snapshot_kind") or sha256_canonical(snapshot.get("high_water_marks")) != inventory_binding.get("high_water_marks_sha256"):
            errors.append("source_snapshot_binding")
    organization = draft.get("organization")
    _validate_organization(organization, errors)
    mappings = _validate_owner_mappings(draft.get("owner_mappings"), organization, errors)
    if isinstance(mapping_binding, Mapping) and isinstance(draft.get("owner_mappings"), list):
        if mapping_binding.get("row_count") != len(draft["owner_mappings"]) or mapping_binding.get("rows_sha256") != sha256_canonical(draft["owner_mappings"]):
            errors.append("mapping_rows_binding")

    relationships_value = draft.get("relationships")
    relationship_rows = relationships_value if isinstance(relationships_value, list) else []
    if not isinstance(relationships_value, list):
        errors.append("relationships_shape")
    relationship_ids: set[str] = set()
    for row in relationship_rows:
        if not _exact(row, _RELATIONSHIP_KEYS):
            errors.append("relationship_shape")
            continue
        relationship_id = row.get("relationship_id")
        source = row.get("source_relationship")
        if not _identifier(relationship_id, maximum=64) or relationship_id in relationship_ids or not _sanitized_source_relationship(source) or source.get("relationship_id") != relationship_id or row.get("inventory_relationship_sha256") != sha256_canonical(source):
            errors.append("relationship_binding")
        relationship_ids.add(str(relationship_id))
        action = row.get("action")
        if action not in ACTIONS or row.get("final_terminal_classification") != ACTION_TO_TERMINAL.get(action):
            errors.append("relationship_decision")
        if action == "import":
            if row.get("execution_phase") not in {"P2.3", "P2.4", "P2.5"} or row.get("import_mode") not in IMPORT_MODES:
                errors.append("relationship_import")
        elif row.get("execution_phase") != "none" or row.get("import_mode") is not None:
            errors.append("relationship_nonimport")
        if not _sorted_unique_strings(row.get("reason_codes")):
            errors.append("relationship_reasons")
    if relationship_rows != sorted(relationship_rows, key=lambda row: row.get("relationship_id", "") if isinstance(row, Mapping) else ""):
        errors.append("relationship_order")

    records_value = draft.get("records")
    record_rows = records_value if isinstance(records_value, list) else []
    if not isinstance(records_value, list):
        errors.append("records_shape")
    record_ids: set[str] = set()
    for row in record_rows:
        if not _exact(row, _RECORD_KEYS):
            errors.append("record_shape")
            continue
        record_id = row.get("record_id")
        source = row.get("source_record")
        if not _identifier(record_id, maximum=64) or record_id in record_ids or not _sanitized_source_record(source) or source.get("record_id") != record_id or row.get("inventory_record_sha256") != sha256_canonical(source) or row.get("category") != source.get("category") or row.get("category") not in CATEGORY_IDS:
            errors.append("record_binding")
        record_ids.add(str(record_id))
        action = row.get("action")
        if action not in ACTIONS or row.get("final_terminal_classification") != ACTION_TO_TERMINAL.get(action):
            errors.append("record_decision")
        if isinstance(source, Mapping):
            expected_action, expected_phase, expected_mode = _expected_record_policy(source)
            # A personal/quarantine mapping may only narrow an otherwise
            # importable record to quarantine.  Scanner-fixed states cannot be
            # promoted or otherwise reinterpreted.
            mapping = mappings.get(row.get("owner_mapping_id"))
            reviewed_pre_role = _reviewed_pre_role_account_policy(source, mapping)
            if reviewed_pre_role is not None:
                expected_action, expected_phase, expected_mode = reviewed_pre_role
            if isinstance(mapping, Mapping) and mapping.get("decision") == "quarantine" and expected_action == "import":
                expected_action, expected_phase, expected_mode = "quarantine", "none", None
            if (action, row.get("execution_phase"), row.get("import_mode")) != (expected_action, expected_phase, expected_mode):
                errors.append("record_policy")
        if action == "import":
            if row.get("execution_phase") not in {"P2.3", "P2.4", "P2.5"} or row.get("import_mode") not in IMPORT_MODES:
                errors.append("record_import")
        elif action == "rebuild":
            if row.get("execution_phase") != "P2.6" or row.get("import_mode") is not None:
                errors.append("record_rebuild")
        elif row.get("execution_phase") != "none" or row.get("import_mode") is not None:
            errors.append("record_nonexecution")
        if row.get("target_space_kind") not in {None, "personal"} or (row.get("target_space_kind") == "personal" and action != "import"):
            errors.append("record_target_space")
        mapping_id = row.get("owner_mapping_id")
        resolved_user = row.get("resolved_owner_user_id")
        if mapping_id is None:
            if resolved_user is not None:
                errors.append("record_owner_mapping")
        elif mapping_id not in mappings:
            errors.append("record_owner_mapping")
        elif mappings[mapping_id].get("decision") == "personal":
            if mappings[mapping_id].get("user_id") != resolved_user:
                errors.append("record_owner_mapping")
        elif mappings[mapping_id].get("decision") == "quarantine":
            if resolved_user is not None or action != "quarantine":
                errors.append("record_owner_mapping")
        else:
            errors.append("record_owner_mapping")
        if isinstance(source, Mapping) and action == "import" and _record_requires_personal_target(source):
            if (
                mapping_id not in mappings
                or mappings[mapping_id].get("decision") != "personal"
                or mappings[mapping_id].get("user_id") != resolved_user
                or row.get("target_space_kind") != "personal"
            ):
                errors.append("record_personal_target")
        if resolved_user is not None and (not isinstance(resolved_user, str) or not _USER_RE.fullmatch(resolved_user)):
            errors.append("record_owner")
        if not _sorted_unique_strings(row.get("reason_codes")) or not _sorted_unique_strings(row.get("relationship_ids")) or any(identity not in relationship_ids for identity in row.get("relationship_ids", ())):
            errors.append("record_lists")
    if record_rows != sorted(record_rows, key=lambda row: row.get("record_id", "") if isinstance(row, Mapping) else ""):
        errors.append("record_order")
    source_record_by_id = {
        str(row.get("record_id")): row.get("source_record")
        for row in record_rows
        if isinstance(row, Mapping) and isinstance(row.get("source_record"), Mapping)
    }
    registry_accounts: dict[str, tuple[str, str]] = {}
    source_claims: set[str] = set()
    for source in source_record_by_id.values():
        if not isinstance(source, Mapping):
            continue
        owner = source.get("owner")
        if isinstance(owner, Mapping):
            for claim in (owner.get("claimed_user_id"), owner.get("user_id")):
                if isinstance(claim, str):
                    source_claims.add(unicodedata.normalize("NFKC", claim).casefold())
        parse = source.get("parse")
        account = parse.get("registry_account") if isinstance(parse, Mapping) else None
        if not isinstance(account, Mapping):
            account = source.get("registry_account")
        if isinstance(account, Mapping) and isinstance(owner, Mapping) and isinstance(owner.get("user_id"), str):
            role = account.get("role")
            status = account.get("account_status")
            if isinstance(role, str) and isinstance(status, str):
                registry_accounts[owner["user_id"]] = (role, status)
    for mapping in mappings.values():
        selector_record = mapping.get("source_record_id")
        selector_claim = mapping.get("source_owner_claim")
        if selector_record is not None and selector_record not in source_record_by_id:
            errors.append("owner_record_selector_binding")
        if selector_claim is not None and unicodedata.normalize("NFKC", str(selector_claim)).casefold() not in source_claims:
            errors.append("owner_claim_selector_binding")
        if mapping.get("decision") == "personal":
            account = registry_accounts.get(str(mapping.get("user_id")))
            mapped_role = mapping.get("system_role")
            mapped_status = mapping.get("account_status")
            role_matches = account is not None and (
                account[0] == mapped_role
                or (
                    account[0] == "unassigned_legacy"
                    and mapped_role in {"admin", "user"}
                )
            )
            if account is None or not role_matches or account[1] != mapped_status:
                errors.append("owner_account_binding")
    planned_by_id = {
        str(row.get("record_id")): row
        for row in record_rows
        if isinstance(row, Mapping)
    }
    for row in relationship_rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("source_relationship"), Mapping):
            continue
        expected = _expected_relationship_policy(row["source_relationship"], planned_by_id)
        if (row.get("action"), row.get("execution_phase"), row.get("import_mode")) != expected:
            errors.append("relationship_policy")
    if isinstance(inventory_binding, Mapping):
        copied_source_records = [row.get("source_record") for row in record_rows]
        copied_source_relationships = [row.get("source_relationship") for row in relationship_rows]
        if (
            inventory_binding.get("record_count") != len(copied_source_records)
            or inventory_binding.get("records_sha256") != sha256_canonical(copied_source_records)
            or inventory_binding.get("relationship_count") != len(copied_source_relationships)
            or inventory_binding.get("relationships_sha256") != sha256_canonical(copied_source_relationships)
        ):
            errors.append("inventory_collection_binding")

        source_record_lookup = {
            row.get("record_id"): row
            for row in copied_source_records
            if isinstance(row, Mapping) and isinstance(row.get("record_id"), str)
        }
        source_relationship_lookup = {
            row.get("relationship_id"): row
            for row in copied_source_relationships
            if isinstance(row, Mapping)
            and isinstance(row.get("relationship_id"), str)
        }
        if (
            set(source_record_order) != set(source_record_lookup)
            or set(source_relationship_order) != set(source_relationship_lookup)
        ):
            errors.append("source_inventory_order")
        reconstructed_inventory = dict(source_inventory_metadata)
        reconstructed_inventory["records"] = [
            source_record_lookup[identity]
            for identity in source_record_order
            if identity in source_record_lookup
        ]
        reconstructed_inventory["relationships"] = [
            source_relationship_lookup[identity]
            for identity in source_relationship_order
            if identity in source_relationship_lookup
        ]
        if inventory_contract_errors(reconstructed_inventory):
            errors.append("source_inventory_contract")
        inventory_tool = reconstructed_inventory.get("tool")
        inventory_snapshot = reconstructed_inventory.get("source_snapshot")
        if (
            reconstructed_inventory.get("manifest_sha256")
            != inventory_binding.get("manifest_sha256")
            or reconstructed_inventory.get("report_schema")
            != inventory_binding.get("report_schema")
            or reconstructed_inventory.get("schema_version")
            != inventory_binding.get("schema_version")
            or not isinstance(inventory_tool, Mapping)
            or inventory_tool.get("name") != inventory_binding.get("tool_name")
            or inventory_tool.get("version") != inventory_binding.get("tool_version")
            or inventory_tool.get("code_revision")
            != inventory_binding.get("code_revision")
            or inventory_tool.get("mapping_sha256")
            != inventory_binding.get("mapping_sha256")
            or inventory_snapshot != snapshot
        ):
            errors.append("source_inventory_binding")
        if hashlib.sha256(
            _canonical_json_bytes(reconstructed_inventory) + b"\n"
        ).hexdigest() != inventory_binding.get("document_sha256"):
            errors.append("source_inventory_document_binding")

    reconciliation = draft.get("reconciliation")
    if not _exact(reconciliation, _RECONCILIATION_KEYS):
        errors.append("reconciliation_shape")
    else:
        action_counts = Counter(str(row.get("action")) for row in record_rows)
        category_counts = Counter(str(row.get("category")) for row in record_rows)
        phase_counts = Counter(str(row.get("execution_phase")) for row in record_rows)
        import_counts = Counter(str(row.get("import_mode")) for row in record_rows if row.get("import_mode") is not None)
        terminal_counts = Counter(str(row.get("final_terminal_classification")) for row in record_rows)
        if not _count_map(reconciliation.get("action_counts"), action_counts) or not _count_map(reconciliation.get("category_counts"), category_counts) or not _count_map(reconciliation.get("execution_phase_counts"), phase_counts) or not _count_map(reconciliation.get("import_mode_counts"), import_counts) or not _count_map(reconciliation.get("terminal_classification_counts"), terminal_counts):
            errors.append("reconciliation_counts")
        if reconciliation.get("total_records") != len(record_rows) or reconciliation.get("classified_records") != len(record_rows) or reconciliation.get("unclassified_records") != 0 or reconciliation.get("total_relationships") != len(relationship_rows) or reconciliation.get("owner_mapping_count") != len(mappings) or reconciliation.get("target_part_record_count") != 0:
            errors.append("reconciliation_totals")
        if reconciliation.get("records_sha256") != sha256_canonical(record_rows) or reconciliation.get("relationships_sha256") != sha256_canonical(relationship_rows) or (isinstance(organization, Mapping) and reconciliation.get("organization_sha256") != organization.get("aggregate_sha256")):
            errors.append("reconciliation_hashes")
    if verify_hash and (not _sha(draft.get("technical_plan_sha256")) or draft.get("technical_plan_sha256") != technical_plan_sha256(draft)):
        errors.append("technical_plan_hash")


def _contract_errors(value: Any, *, verify_hash: bool) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        return ("plan_shape",)
    errors: list[str] = []
    stage = value.get("stage")
    if stage == "migration_plan_draft":
        _validate_draft(value, errors, verify_hash=verify_hash)
    elif stage == "reviewed_migration_plan":
        if frozenset(value) != _REVIEWED_KEYS:
            errors.append("reviewed_plan_shape")
        elif value.get("report_schema") != PLAN_REPORT_SCHEMA or value.get("schema_version") != PLAN_SCHEMA_VERSION:
            errors.append("reviewed_plan_header")
        else:
            draft = value.get("draft")
            if isinstance(draft, Mapping):
                _validate_draft(draft, errors, verify_hash=True)
            else:
                errors.append("reviewed_draft_shape")
            review = value.get("review")
            if not _exact(review, _REVIEW_KEYS):
                errors.append("review_shape")
            elif not _sha(review.get("confirmed_technical_plan_sha256")) or not _sha(review.get("review_attestation_sha256")) or not _valid_canonical_utc(review.get("reviewed_at_utc")) or not _identifier(review.get("reviewer_id"), maximum=128) or not isinstance(draft, Mapping) or review.get("confirmed_technical_plan_sha256") != draft.get("technical_plan_sha256"):
                errors.append("review_binding")
            if verify_hash and (not _sha(value.get("plan_sha256")) or value.get("plan_sha256") != reviewed_plan_sha256(value)):
                errors.append("reviewed_plan_hash")
    else:
        errors.append("plan_stage")
    return tuple(sorted(set(errors)))


def plan_contract_errors(value: Any, *, verify_hash: bool = True) -> tuple[str, ...]:
    """Return stable validation reasons for any JSON-shaped input; never raise."""

    try:
        return _contract_errors(value, verify_hash=verify_hash)
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ):
        return ("plan_shape",)


def migration_plan_contract_errors(value: Any, *, verify_hash: bool = True) -> tuple[str, ...]:
    return plan_contract_errors(value, verify_hash=verify_hash)


__all__ = [
    "ACTIONS",
    "ACTION_TO_TERMINAL",
    "DECISION_TABLE",
    "DECISION_TABLE_SHA256",
    "EXECUTION_PHASES",
    "IMPORT_MODES",
    "ORGANIZATION_MAPPING_HEADER",
    "PLAN_REPORT_SCHEMA",
    "PLAN_SCHEMA_VERSION",
    "PLAN_STAGES",
    "PLANNER_NAME",
    "PLANNER_VERSION",
    "TERMINAL_CLASSIFICATIONS",
    "canonical_json",
    "canonical_json_bytes",
    "canonical_plan_json",
    "canonical_plan_json_bytes",
    "expected_planner_code_revision",
    "migration_plan_contract_errors",
    "plan_contract_errors",
    "reviewed_plan_payload",
    "reviewed_plan_sha256",
    "sha256_canonical",
    "technical_plan_payload",
    "technical_plan_sha256",
]
