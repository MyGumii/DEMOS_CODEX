"""Pure P2.2 inventory-to-plan transformation.

The planner consumes already captured, sanitized artifacts.  It never scans a
source tree, opens the canonical database, imports the legacy application, or
applies a migration.
"""

from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import re
import unicodedata
from typing import Any, Mapping, Sequence

from code_assist_v1.knowledge_core.legacy.plan_contract import (
    ACTION_TO_TERMINAL,
    DECISION_TABLE,
    DECISION_TABLE_SHA256,
    ORGANIZATION_MAPPING_HEADER,
    PLAN_REPORT_SCHEMA,
    PLAN_SCHEMA_VERSION,
    PLANNER_NAME,
    PLANNER_VERSION,
    canonical_json_bytes,
    expected_planner_code_revision,
    _planner_resolvable_personal_content,
    _planner_importable_p24_record,
    plan_contract_errors,
    reviewed_plan_sha256,
    sha256_canonical,
    technical_plan_sha256,
)
from code_assist_v1.tools.legacy_inventory_contract import (
    HARD_ERROR_CODES,
    inventory_contract_errors,
)


_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_USER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_CLAIM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RECORD_RE = re.compile(r"^rec_[0-9a-f]{24}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_MAX_JSON_BYTES = 256 * 1024 * 1024
_MAX_MAPPING_BYTES = 8 * 1024 * 1024

PLANNER_CODE_REVISION = expected_planner_code_revision()


class MigrationPlanError(ValueError):
    """Stable public planning failure without source values or exception text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _sha(value: Any) -> bool:
    return isinstance(value, str) and _SHA_RE.fullmatch(value) is not None


def _normal_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _safe_text(value: str, *, maximum: int) -> bool:
    return (
        1 <= len(value) <= maximum
        and value == value.strip()
        and unicodedata.normalize("NFKC", value) == value
        and all(
            ord(character) >= 32
            and character != "\x7f"
            and unicodedata.category(character) not in {"Cc", "Cf", "Cs", "Co", "Cn"}
            for character in value
        )
    )


def _reject_duplicate_members(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_member")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    del value
    raise ValueError("non_finite_json")


def _parse_json_document(data: bytes, *, code: str) -> Mapping[str, Any]:
    if not isinstance(data, bytes) or len(data) > _MAX_JSON_BYTES or data.startswith(b"\xef\xbb\xbf"):
        raise MigrationPlanError(code)
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_members,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise MigrationPlanError(code) from None
    if not isinstance(value, Mapping):
        raise MigrationPlanError(code)
    return value


def parse_inventory_document(
    data: bytes,
    *,
    expected_document_sha256: str | None = None,
    expected_manifest_sha256: str,
    expected_snapshot_attestation_sha256: str,
    expected_root_binding_sha256: str,
    allow_data_quality_errors: bool = False,
) -> Mapping[str, Any]:
    """Parse and bind one complete P2.1 inventory without touching its roots."""

    expectations = (
        expected_manifest_sha256,
        expected_snapshot_attestation_sha256,
        expected_root_binding_sha256,
    )
    if not all(_sha(value) for value in expectations) or (
        expected_document_sha256 is not None and not _sha(expected_document_sha256)
    ):
        raise MigrationPlanError("INVENTORY_INVALID")
    digest = hashlib.sha256(data).hexdigest()
    if expected_document_sha256 is not None and digest != expected_document_sha256:
        raise MigrationPlanError("INVENTORY_INVALID")
    inventory = _parse_json_document(data, code="INVENTORY_INVALID")
    # P2.1 emits exactly canonical JSON plus one LF.  Requiring that transport
    # form makes the externally supplied document digest reproducible from the
    # sanitized plan copy, instead of leaving whitespace as an unverifiable
    # source anchor.
    if data != canonical_json_bytes(inventory) + b"\n":
        raise MigrationPlanError("INVENTORY_INVALID")
    if inventory_contract_errors(inventory):
        raise MigrationPlanError("INVENTORY_INVALID")
    snapshot = inventory.get("source_snapshot")
    if (
        inventory.get("manifest_sha256") != expected_manifest_sha256
        or not isinstance(snapshot, Mapping)
        or snapshot.get("attestation_sha256") != expected_snapshot_attestation_sha256
        or snapshot.get("root_binding_sha256") != expected_root_binding_sha256
    ):
        raise MigrationPlanError("INVENTORY_INVALID")
    errors = inventory.get("errors")
    if not isinstance(errors, list):
        raise MigrationPlanError("INVENTORY_INVALID")
    if inventory.get("complete") is not True:
        if not allow_data_quality_errors:
            raise MigrationPlanError("INVENTORY_INVALID")
        if any(
            isinstance(row, Mapping)
            and row.get("severity") == "error"
            and row.get("code") in HARD_ERROR_CODES
            for row in errors
        ):
            raise MigrationPlanError("INVENTORY_INVALID")
    return inventory


def _empty(value: str) -> str | None:
    return None if value == "" else value


def _mapping_id(row: Mapping[str, Any]) -> str:
    return "map_" + sha256_canonical({key: row[key] for key in sorted(row) if key != "mapping_id"})[:24]


def parse_organization_mapping_csv(
    data: bytes,
    *,
    expected_document_sha256: str,
) -> tuple[dict[str, Any], ...]:
    """Parse the exact RFC4180 organization-map v1 shape."""

    if not _sha(expected_document_sha256) or not isinstance(data, bytes) or len(data) > _MAX_MAPPING_BYTES or data.startswith(b"\xef\xbb\xbf") or hashlib.sha256(data).hexdigest() != expected_document_sha256:
        raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
    try:
        text = data.decode("utf-8")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        rows = list(reader)
    except (UnicodeError, csv.Error):
        raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID") from None
    if not rows or tuple(rows[0]) != ORGANIZATION_MAPPING_HEADER:
        raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
    parsed: list[dict[str, Any]] = []
    normalized_claims: set[str] = set()
    record_ids: set[str] = set()
    user_targets: dict[str, tuple[Any, ...]] = {}
    code_to_id: dict[tuple[str, str], str] = {}
    id_to_code: dict[tuple[str, str], str] = {}
    part_leaders: Counter[str] = Counter()
    for raw in rows[1:]:
        if len(raw) != len(ORGANIZATION_MAPPING_HEADER) or any(
            value != value.strip()
            or unicodedata.normalize("NFKC", value) != value
            or any(
                (ord(character) < 32 and character not in "\r\n\t")
                or unicodedata.category(character) in {"Cc", "Cf", "Cs", "Co", "Cn"}
                for character in value
            )
            for value in raw
        ):
            raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
        source = dict(zip(ORGANIZATION_MAPPING_HEADER, raw, strict=True))
        if source["mapping_schema_version"] != "1":
            raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
        claim = _empty(source["source_owner_claim"])
        record_id = _empty(source["source_record_id"])
        if (claim is None) == (record_id is None):
            raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
        if claim is not None:
            if not _safe_text(claim, maximum=64) or not _CLAIM_RE.fullmatch(claim):
                raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
            key = _normal_key(claim)
            if key in normalized_claims:
                raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
            normalized_claims.add(key)
        if record_id is not None:
            if not _RECORD_RE.fullmatch(record_id) or record_id in record_ids:
                raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
            record_ids.add(record_id)
        decision = source["decision"]
        if decision not in {"personal", "quarantine"}:
            raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
        leader_text = source["is_part_leader"]
        if leader_text not in {"true", "false"}:
            raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
        leader = leader_text == "true"
        converted: dict[str, Any] = {
            "source_owner_claim": claim,
            "source_record_id": record_id,
            "decision": decision,
            "user_id": _empty(source["user_id"]),
            "system_role": _empty(source["system_role"]),
            "account_status": _empty(source["account_status"]),
            "responsibility_code": _empty(source["responsibility_code"]),
            "responsibility_id": _empty(source["responsibility_id"]),
            "team_code": _empty(source["team_code"]),
            "team_id": _empty(source["team_id"]),
            "part_code": _empty(source["part_code"]),
            "part_id": _empty(source["part_id"]),
            "is_part_leader": leader,
        }
        target_keys = (
            "user_id",
            "system_role",
            "account_status",
            "responsibility_code",
            "responsibility_id",
            "team_code",
            "team_id",
            "part_code",
            "part_id",
        )
        if decision == "quarantine":
            if any(converted[key] is not None for key in target_keys) or leader:
                raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
        else:
            user_id = converted["user_id"]
            if not isinstance(user_id, str) or not _USER_RE.fullmatch(user_id) or converted["system_role"] not in {"admin", "user"} or converted["account_status"] not in {"active", "disabled"}:
                raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
            org_keys = (
                "responsibility_code",
                "responsibility_id",
                "team_code",
                "team_id",
                "part_code",
                "part_id",
            )
            org_values = tuple(converted[key] for key in org_keys)
            assigned = all(value is not None for value in org_values)
            if not (assigned or all(value is None for value in org_values)):
                raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
            if assigned:
                for code in org_values[::2]:
                    if not isinstance(code, str) or not _safe_text(code, maximum=64):
                        raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
                for identity in org_values[1::2]:
                    if not isinstance(identity, str) or not _UUID_RE.fullmatch(identity):
                        raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
                for kind, code, identity in (
                    ("responsibility", str(org_values[0]), str(org_values[1])),
                    ("team", str(org_values[2]), str(org_values[3])),
                    ("part", str(org_values[4]), str(org_values[5])),
                ):
                    code_key = (kind, _normal_key(code))
                    id_key = (kind, identity)
                    if code_key in code_to_id and code_to_id[code_key] != identity:
                        raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
                    if id_key in id_to_code and _normal_key(id_to_code[id_key]) != _normal_key(code):
                        raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
                    code_to_id[code_key] = identity
                    id_to_code[id_key] = code
            if converted["account_status"] == "disabled" and (assigned or leader):
                raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
            if leader and not assigned:
                raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
            if leader:
                part_leaders[str(converted["part_id"])] += 1
            target = tuple(converted[key] for key in target_keys)
            previous = user_targets.get(user_id)
            if previous is not None and previous != target:
                raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
            user_targets[user_id] = target
        converted["mapping_id"] = _mapping_id(converted)
        parsed.append(converted)
    if any(count > 1 for count in part_leaders.values()):
        raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
    if not any(
        row["decision"] == "personal"
        and row["system_role"] == "admin"
        and row["account_status"] == "active"
        for row in parsed
    ):
        raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
    parsed.sort(key=lambda row: row["mapping_id"])
    return tuple(parsed)


def _inventory_account_index(inventory: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    owners = inventory.get("owners")
    if not isinstance(owners, list):
        raise MigrationPlanError("INVENTORY_INVALID")
    for owner in owners:
        if not isinstance(owner, Mapping) or owner.get("kind") != "personal" or owner.get("mapping_status") != "confirmed":
            continue
        user_id = owner.get("user_id")
        if isinstance(user_id, str):
            result[user_id] = owner
    return result


def _validate_mapping_against_inventory(
    inventory: Mapping[str, Any], mappings: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    records = inventory.get("records")
    if not isinstance(records, list):
        raise MigrationPlanError("INVENTORY_INVALID")
    record_by_id = {
        row.get("record_id"): row
        for row in records
        if isinstance(row, Mapping) and isinstance(row.get("record_id"), str)
    }
    accounts = _inventory_account_index(inventory)
    claims: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for user_id, account in accounts.items():
        claim_values = account.get("claimed_user_ids")
        if not isinstance(claim_values, list):
            continue
        for claim in claim_values:
            if isinstance(claim, str):
                key = _normal_key(claim)
                if key in claims and claims[key][0] != user_id:
                    raise MigrationPlanError("INVENTORY_INVALID")
                claims[key] = (user_id, account)
    claim_mappings: dict[str, Mapping[str, Any]] = {}
    record_mappings: dict[str, Mapping[str, Any]] = {}
    for mapping in mappings:
        claim = mapping.get("source_owner_claim")
        record_id = mapping.get("source_record_id")
        account: Mapping[str, Any] | None = None
        if isinstance(claim, str):
            found = claims.get(_normal_key(claim))
            if found is None:
                raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
            canonical_user, account = found
            claim_mappings[_normal_key(claim)] = mapping
            if mapping.get("decision") == "personal" and mapping.get("user_id") != canonical_user:
                raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
        elif isinstance(record_id, str):
            source_record = record_by_id.get(record_id)
            if source_record is None:
                raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
            record_mappings[record_id] = mapping
            owner = source_record.get("owner")
            source_user = owner.get("user_id") if isinstance(owner, Mapping) else None
            if isinstance(source_user, str):
                account = accounts.get(source_user)
                if mapping.get("decision") == "personal" and mapping.get("user_id") != source_user:
                    raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
            elif mapping.get("decision") == "personal":
                account = accounts.get(str(mapping.get("user_id")))
        if mapping.get("decision") == "personal":
            account_role = None if account is None else account.get("role")
            mapped_role = mapping.get("system_role")
            role_matches = mapped_role == account_role or (
                account_role == "unassigned_legacy"
                and mapped_role in {"admin", "user"}
            )
            if (
                account is None
                or mapping.get("account_status") != account.get("account_status")
                or not role_matches
            ):
                raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
    # Every confirmed legacy account must be explicitly decided either once by
    # claim or for each record it owns.  System/scanner-fixed rows need no CSV.
    for user_id, account in accounts.items():
        account_claims = [
            _normal_key(value)
            for value in account.get("claimed_user_ids", [])
            if isinstance(value, str)
        ]
        if any(key in claim_mappings for key in account_claims):
            continue
        owned = [
            row
            for row in records
            if isinstance(row, Mapping)
            and isinstance(row.get("owner"), Mapping)
            and row["owner"].get("user_id") == user_id
        ]
        if any(row.get("record_id") not in record_mappings for row in owned):
            raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
    for row in records:
        if not isinstance(row, Mapping):
            continue
        owner = row.get("owner")
        category = row.get("category")
        proposed = row.get("proposed_terminal_classification")
        is_unowned = not isinstance(owner, Mapping) or owner.get("kind") != "personal" or owner.get("mapping_status") != "confirmed"
        needs_explicit_personal_disposition = category == "conversation.global_sessions"
        needs_explicit_personal_disposition = needs_explicit_personal_disposition or (
            is_unowned
            and proposed == "quarantined"
            and category in {
                "knowledge.legacy_flat_markdown",
                "knowledge.legacy_thoughts",
                "knowledge.legacy_inbox",
                "knowledge.legacy_raw",
                "knowledge.legacy_raw_chunks",
                "conversation.user_sessions",
                "external.notion_session_links",
                "operations.jobs",
            }
        )
        if needs_explicit_personal_disposition and row.get("record_id") not in record_mappings:
            raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
    return claim_mappings, record_mappings


def _organization(mappings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    responsibilities: dict[str, dict[str, Any]] = {}
    teams: dict[str, dict[str, Any]] = {}
    parts: dict[str, dict[str, Any]] = {}
    for row in mappings:
        if row.get("decision") != "personal" or row.get("part_id") is None:
            continue
        responsibility_id = str(row["responsibility_id"])
        team_id = str(row["team_id"])
        part_id = str(row["part_id"])
        responsibility = {"code": row["responsibility_code"], "id": responsibility_id}
        team = {"code": row["team_code"], "id": team_id, "responsibility_id": responsibility_id}
        part = parts.get(part_id, {"code": row["part_code"], "id": part_id, "leader_user_id": None, "team_id": team_id})
        if responsibilities.get(responsibility_id, responsibility) != responsibility or teams.get(team_id, team) != team or part["code"] != row["part_code"] or part["team_id"] != team_id:
            raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
        responsibilities[responsibility_id] = responsibility
        teams[team_id] = team
        if row.get("is_part_leader") is True:
            if part["leader_user_id"] not in (None, row["user_id"]):
                raise MigrationPlanError("ORGANIZATION_MAPPING_INVALID")
            part["leader_user_id"] = row["user_id"]
        parts[part_id] = part
    value: dict[str, Any] = {
        "responsibility_units": sorted(responsibilities.values(), key=lambda row: row["id"]),
        "team_units": sorted(teams.values(), key=lambda row: row["id"]),
        "part_units": sorted(parts.values(), key=lambda row: row["id"]),
    }
    value["aggregate_sha256"] = sha256_canonical(value)
    return value


def _mapping_for_record(
    record: Mapping[str, Any],
    claim_mappings: Mapping[str, Mapping[str, Any]],
    record_mappings: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    record_id = record.get("record_id")
    if isinstance(record_id, str) and record_id in record_mappings:
        return record_mappings[record_id]
    owner = record.get("owner")
    if not isinstance(owner, Mapping):
        return None
    for value in (owner.get("claimed_user_id"), owner.get("user_id")):
        if isinstance(value, str) and _normal_key(value) in claim_mappings:
            return claim_mappings[_normal_key(value)]
    return None


def _default_import_mode(record: Mapping[str, Any]) -> str:
    category = str(record.get("category"))
    kind = record.get("record_kind")
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
        "knowledge.legacy_flat_markdown",
        "knowledge.wiki_sources",
        "knowledge.wiki_hidden_pages",
        "knowledge.legacy_thoughts",
        "knowledge.legacy_inbox",
        "knowledge.legacy_raw",
        "knowledge.legacy_raw_chunks",
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
        parse = record.get("parse")
        source_status = parse.get("source_status") if isinstance(parse, Mapping) else None
        return "abandoned_legacy_job" if source_status in {"queued", "running", "unknown"} else "job_history"
    return "legacy_history"


def _category_policy(category: str) -> tuple[str, str]:
    row = next((item for item in DECISION_TABLE if item["category"] == category), None)
    if row is None:
        raise MigrationPlanError("PLAN_INVALID")
    return str(row["action"]), str(row["execution_phase"])


def _record_decision(
    record: Mapping[str, Any], mapping: Mapping[str, Any] | None
) -> tuple[str, str, str | None, list[str], str | None, str | None, str | None]:
    proposed = record.get("proposed_terminal_classification")
    category = str(record.get("category"))
    policy_action, policy_phase = _category_policy(category)
    action_by_terminal = {
        "rebuilt": "rebuild",
        "retained_external": "retain_external",
        "quarantined": "quarantine",
        "unavailable": "record_unavailable",
    }
    action = action_by_terminal.get(str(proposed), policy_action)
    phase = "P2.6" if action == "rebuild" else policy_phase if action == "import" else "none"
    mode = _default_import_mode(record) if action == "import" else None
    reasons: set[str] = set()
    if action == "rebuild":
        reasons.add("derived_rebuild")
    elif action == "retain_external":
        reasons.add("external_source_retained")
    elif action == "record_unavailable":
        reasons.add("browser_state_unavailable")
    elif action == "quarantine":
        reasons.add("scanner_decision_preserved")
    else:
        reasons.add("scanner_decision_preserved")
    parse = record.get("parse")
    registry_account = (
        parse.get("registry_account") if isinstance(parse, Mapping) else None
    )
    pre_role_account_resolved = (
        category == "identity.users_registry"
        and record.get("record_kind") == "logical_record"
        and record.get("classification_authority") == "planner_required"
        and record.get("proposed_terminal_classification") == "quarantined"
        and record.get("reason_codes") == ["legacy_role_requires_plan"]
        and record.get("status") == "active"
        and isinstance(parse, Mapping)
        and parse.get("status") == "valid"
        and isinstance(registry_account, Mapping)
        and registry_account.get("role") == "unassigned_legacy"
        and mapping is not None
        and mapping.get("decision") == "personal"
        and mapping.get("system_role") in {"admin", "user"}
    )
    if pre_role_account_resolved:
        action, phase, mode = "import", "P2.3", "user_account"
        reasons.discard("scanner_decision_preserved")
        reasons.add("explicit_legacy_role_resolution")
    elif _planner_resolvable_personal_content(record):
        action, phase, mode = "import", "P2.3", "personal_item"
        reasons.discard("scanner_decision_preserved")
        reasons.add("reviewed_legacy_personal_content_adapter")
    elif (
        category == "knowledge.wiki_candidates"
        and record.get("record_kind") == "logical_record"
        and _planner_importable_p24_record(record)
    ):
        action, phase, mode = "import", "P2.4", "finding"
        reasons.discard("scanner_decision_preserved")
        reasons.add("reviewed_pending_candidate")
    elif action == "import" and phase == "P2.4" and not _planner_importable_p24_record(record):
        action, phase, mode = "quarantine", "none", None
        reasons.add("canonical_p24_adapter_unavailable")
    owner = record.get("owner")
    personal_source = isinstance(owner, Mapping) and owner.get("kind") == "personal"
    personal_required = category.startswith("knowledge.") and action == "import"
    personal_required = personal_required or category in {
        "conversation.user_sessions",
        "conversation.global_sessions",
        "external.notion_session_links",
        "operations.jobs",
    }
    personal_required = personal_required or (
        category == "identity.users_registry"
        and record.get("record_kind") == "logical_record"
        and personal_source
    )
    if mapping is not None and mapping.get("decision") == "quarantine":
        action, phase, mode = "quarantine", "none", None
        reasons.add("owner_mapping_quarantine")
    elif action == "import" and personal_required:
        if mapping is None or mapping.get("decision") != "personal":
            action, phase, mode = "quarantine", "none", None
            reasons.add("owner_mapping_quarantine")
        else:
            reasons.add("explicit_record_mapping" if mapping.get("source_record_id") else "explicit_owner_claim_mapping")
    elif action == "import" and personal_source and mapping is not None and mapping.get("decision") == "personal":
        reasons.add("validated_personal_owner")
    mapping_id = mapping.get("mapping_id") if isinstance(mapping, Mapping) else None
    user_id = mapping.get("user_id") if isinstance(mapping, Mapping) and mapping.get("decision") == "personal" else None
    target = "personal" if action == "import" and personal_required and user_id is not None else None
    if action == "import" and mode == "abandoned_legacy_job":
        reasons.add("legacy_nonterminal_job_abandoned")
    return action, phase, mode, sorted(reasons), mapping_id, user_id, target


def _relationship_decision(
    relationship: Mapping[str, Any], planned_records: Mapping[str, Mapping[str, Any]]
) -> tuple[str, str, str | None, list[str]]:
    relationship_type = relationship.get("relationship_type")
    status = relationship.get("status")
    endpoints = [
        relationship.get(key)
        for key in ("from_record_id", "source_record_id", "to_record_id")
        if relationship.get(key) is not None
    ]
    endpoint_rows = [planned_records.get(str(value)) for value in endpoints]
    valid_endpoints = bool(endpoints) and all(
        isinstance(row, Mapping) and row.get("action") == "import"
        for row in endpoint_rows
    )
    personal_users = {
        row.get("resolved_owner_user_id")
        for row in endpoint_rows
        if isinstance(row, Mapping) and row.get("target_space_kind") == "personal"
    }
    if len(personal_users) > 1:
        valid_endpoints = False
    if status != "valid" or not valid_endpoints:
        return "quarantine", "none", None, ["relationship_quarantine"]
    if relationship_type == "asset_reference":
        return "import", "P2.3", "personal_asset", ["scanner_decision_preserved"]
    if relationship_type == "notion_session_page":
        return "import", "P2.5", "notion_session_link", ["scanner_decision_preserved"]
    if relationship_type in {"candidate_source", "candidate_target"}:
        return "import", "P2.4", "finding", ["scanner_decision_preserved"]
    if relationship_type in {"graph_node_source"}:
        return "import", "P2.4", "graph_node", ["scanner_decision_preserved"]
    if relationship_type in {"contradicts", "extends", "related", "supports"}:
        return "import", "P2.4", "graph_edge", ["scanner_decision_preserved"]
    if relationship_type == "exact_content_duplicate":
        return "import", "P2.4", "legacy_history", ["hidden_exact_duplicate_provenance"]
    return "quarantine", "none", None, ["relationship_quarantine"]


def build_migration_plan_draft(
    inventory: Mapping[str, Any],
    mappings: Sequence[Mapping[str, Any]],
    *,
    inventory_document_sha256: str,
    organization_mapping_document_sha256: str,
) -> dict[str, Any]:
    """Build a deterministic self-contained technical plan from parsed inputs."""

    if not _sha(inventory_document_sha256) or not _sha(organization_mapping_document_sha256):
        raise MigrationPlanError("PLAN_INVALID")
    # Freeze one canonical, detached copy so the returned plan cannot be
    # changed by mutating the caller's parsed inventory after planning.
    inventory = json.loads(canonical_json_bytes(inventory).decode("utf-8"))
    claim_mappings, record_mappings = _validate_mapping_against_inventory(inventory, mappings)
    organization = _organization(mappings)
    source_records = inventory.get("records")
    source_relationships = inventory.get("relationships")
    snapshot = inventory.get("source_snapshot")
    tool = inventory.get("tool")
    if not isinstance(source_records, list) or not isinstance(source_relationships, list) or not isinstance(snapshot, Mapping) or not isinstance(tool, Mapping):
        raise MigrationPlanError("INVENTORY_INVALID")

    interim: list[dict[str, Any]] = []
    for source in source_records:
        if not isinstance(source, Mapping):
            raise MigrationPlanError("INVENTORY_INVALID")
        mapping = _mapping_for_record(source, claim_mappings, record_mappings)
        action, phase, mode, reasons, mapping_id, user_id, target = _record_decision(source, mapping)
        interim.append(
            {
                "action": action,
                "category": source["category"],
                "execution_phase": phase,
                "final_terminal_classification": ACTION_TO_TERMINAL[action],
                "import_mode": mode,
                "inventory_record_sha256": sha256_canonical(source),
                "owner_mapping_id": mapping_id,
                "reason_codes": reasons,
                "record_id": source["record_id"],
                "relationship_ids": [],
                "resolved_owner_user_id": user_id,
                "source_record": source,
                "target_space_kind": target,
            }
        )
    planned_by_id = {row["record_id"]: row for row in interim}
    relationship_rows: list[dict[str, Any]] = []
    record_relationship_ids: dict[str, set[str]] = {row["record_id"]: set() for row in interim}
    for source in source_relationships:
        if not isinstance(source, Mapping):
            raise MigrationPlanError("INVENTORY_INVALID")
        action, phase, mode, reasons = _relationship_decision(source, planned_by_id)
        relationship_id = source["relationship_id"]
        relationship_rows.append(
            {
                "action": action,
                "execution_phase": phase,
                "final_terminal_classification": ACTION_TO_TERMINAL[action],
                "import_mode": mode,
                "inventory_relationship_sha256": sha256_canonical(source),
                "reason_codes": reasons,
                "relationship_id": relationship_id,
                "source_relationship": source,
            }
        )
        for key in ("from_record_id", "source_record_id", "to_record_id"):
            record_id = source.get(key)
            if record_id in record_relationship_ids:
                record_relationship_ids[str(record_id)].add(str(relationship_id))
    relationship_rows.sort(key=lambda row: row["relationship_id"])
    for row in interim:
        row["relationship_ids"] = sorted(record_relationship_ids[row["record_id"]])
    interim.sort(key=lambda row: row["record_id"])
    mapping_rows = sorted((dict(row) for row in mappings), key=lambda row: row["mapping_id"])

    inventory_binding = {
        "document_sha256": inventory_document_sha256,
        "manifest_sha256": inventory["manifest_sha256"],
        "report_schema": inventory["report_schema"],
        "schema_version": inventory["schema_version"],
        "tool_name": tool["name"],
        "tool_version": tool["version"],
        "code_revision": tool["code_revision"],
        "mapping_sha256": tool["mapping_sha256"],
        "root_binding_sha256": snapshot["root_binding_sha256"],
        "snapshot_attestation_sha256": snapshot["attestation_sha256"],
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_kind": snapshot["kind"],
        "high_water_marks_sha256": sha256_canonical(snapshot["high_water_marks"]),
        "record_count": len(source_records),
        "records_sha256": sha256_canonical(
            sorted(source_records, key=lambda row: row["record_id"])
        ),
        "relationship_count": len(source_relationships),
        "relationships_sha256": sha256_canonical(
            sorted(source_relationships, key=lambda row: row["relationship_id"])
        ),
    }
    reconciliation = {
        "action_counts": dict(sorted(Counter(row["action"] for row in interim).items())),
        "category_counts": dict(sorted(Counter(row["category"] for row in interim).items())),
        "classified_records": len(interim),
        "execution_phase_counts": dict(sorted(Counter(row["execution_phase"] for row in interim).items())),
        "import_mode_counts": dict(sorted(Counter(row["import_mode"] for row in interim if row["import_mode"] is not None).items())),
        "organization_sha256": organization["aggregate_sha256"],
        "owner_mapping_count": len(mapping_rows),
        "records_sha256": sha256_canonical(interim),
        "relationships_sha256": sha256_canonical(relationship_rows),
        "target_part_record_count": 0,
        "terminal_classification_counts": dict(sorted(Counter(row["final_terminal_classification"] for row in interim).items())),
        "total_records": len(interim),
        "total_relationships": len(relationship_rows),
        "unclassified_records": 0,
    }
    draft: dict[str, Any] = {
        "report_schema": PLAN_REPORT_SCHEMA,
        "schema_version": PLAN_SCHEMA_VERSION,
        "stage": "migration_plan_draft",
        "planner": {
            "name": PLANNER_NAME,
            "version": PLANNER_VERSION,
            "code_revision": PLANNER_CODE_REVISION,
            "decision_table_sha256": DECISION_TABLE_SHA256,
        },
        "inputs": {
            "inventory": inventory_binding,
            "organization_mapping": {
                "document_sha256": organization_mapping_document_sha256,
                "mapping_schema_version": 1,
                "row_count": len(mapping_rows),
                "rows_sha256": sha256_canonical(mapping_rows),
            },
        },
        # The complete P2.1 authority metadata is retained without duplicating
        # its two large ledgers.  The plan rows carry those ledgers verbatim;
        # the independent contract reconstructs and validates the exact P2.1
        # document, including its manifest and every closure invariant.
        "source_inventory": {
            "metadata": {
                key: value
                for key, value in inventory.items()
                if key not in {"records", "relationships"}
            },
            "record_order": [row["record_id"] for row in source_records],
            "relationship_order": [
                row["relationship_id"] for row in source_relationships
            ],
        },
        "source_snapshot": snapshot,
        "organization": organization,
        "owner_mappings": mapping_rows,
        "records": interim,
        "relationships": relationship_rows,
        "reconciliation": reconciliation,
        "technical_plan_sha256": "",
    }
    draft["technical_plan_sha256"] = technical_plan_sha256(draft)
    contract_errors = plan_contract_errors(draft)
    if contract_errors:
        raise MigrationPlanError("PLAN_INVALID")
    return draft


def build_plan_draft(
    inventory_json_bytes: bytes,
    organization_mapping_csv_bytes: bytes,
    *,
    expected_inventory_manifest_sha256: str,
    expected_snapshot_attestation_sha256: str,
    expected_root_binding_sha256: str,
    expected_organization_mapping_document_sha256: str,
    allow_data_quality_errors: bool = False,
) -> dict[str, Any]:
    inventory_document_sha256 = hashlib.sha256(inventory_json_bytes).hexdigest()
    inventory = parse_inventory_document(
        inventory_json_bytes,
        expected_document_sha256=inventory_document_sha256,
        expected_manifest_sha256=expected_inventory_manifest_sha256,
        expected_snapshot_attestation_sha256=expected_snapshot_attestation_sha256,
        expected_root_binding_sha256=expected_root_binding_sha256,
        allow_data_quality_errors=allow_data_quality_errors,
    )
    mappings = parse_organization_mapping_csv(
        organization_mapping_csv_bytes,
        expected_document_sha256=expected_organization_mapping_document_sha256,
    )
    return build_migration_plan_draft(
        inventory,
        mappings,
        inventory_document_sha256=inventory_document_sha256,
        organization_mapping_document_sha256=expected_organization_mapping_document_sha256,
    )


def _canonical_reviewed_at(value: str) -> str:
    if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
        raise MigrationPlanError("REVIEW_INVALID")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise MigrationPlanError("REVIEW_INVALID") from None
    if parsed.tzinfo is None or parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z") != value:
        raise MigrationPlanError("REVIEW_INVALID")
    return value


def confirm_migration_plan(
    draft: bytes | Mapping[str, Any],
    *,
    expected_draft_sha256: str,
    review_attestation_sha256: str,
    reviewed_at_utc: str,
    reviewer_id: str,
) -> dict[str, Any]:
    if not _sha(expected_draft_sha256) or not _sha(review_attestation_sha256) or not isinstance(reviewer_id, str) or not _safe_text(reviewer_id, maximum=128):
        raise MigrationPlanError("REVIEW_INVALID")
    if isinstance(draft, bytes):
        if hashlib.sha256(draft).hexdigest() != expected_draft_sha256:
            raise MigrationPlanError("REVIEW_INVALID")
        parsed = _parse_json_document(draft, code="PLAN_INVALID")
    elif isinstance(draft, Mapping):
        canonical = canonical_json_bytes(draft) + b"\n"
        if hashlib.sha256(canonical).hexdigest() != expected_draft_sha256:
            raise MigrationPlanError("REVIEW_INVALID")
        parsed = draft
    else:
        raise MigrationPlanError("PLAN_INVALID")
    if plan_contract_errors(parsed) or parsed.get("stage") != "migration_plan_draft":
        raise MigrationPlanError("PLAN_INVALID")
    review = {
        "confirmed_technical_plan_sha256": parsed["technical_plan_sha256"],
        "review_attestation_sha256": review_attestation_sha256,
        "reviewed_at_utc": _canonical_reviewed_at(reviewed_at_utc),
        "reviewer_id": reviewer_id,
    }
    result: dict[str, Any] = {
        "report_schema": PLAN_REPORT_SCHEMA,
        "schema_version": PLAN_SCHEMA_VERSION,
        "stage": "reviewed_migration_plan",
        "draft": parsed,
        "review": review,
        "plan_sha256": "",
    }
    result["plan_sha256"] = reviewed_plan_sha256(result)
    if plan_contract_errors(result):
        raise MigrationPlanError("PLAN_INVALID")
    return result


confirm_plan = confirm_migration_plan


__all__ = [
    "MigrationPlanError",
    "PLANNER_CODE_REVISION",
    "build_migration_plan_draft",
    "build_plan_draft",
    "confirm_migration_plan",
    "confirm_plan",
    "parse_inventory_document",
    "parse_organization_mapping_csv",
]
