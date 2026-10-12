"""Verify the G0-E engineering decision without mutating schema or runtime."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = PACKAGE_ROOT / "docs" / "notion_parity" / "core_free_web_ui"
ADR = CONTRACT_ROOT / "internal_collaborative_engineering_decision.v1.json"
EXPECTED_CONTRACT = "code_assist.notion.internal_collaborative_engineering_decision.g0_e.v1"


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _load(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=_reject,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _workspace_schema_version(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: list[int] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "SCHEMA_VERSION" for target in node.targets):
            value = ast.literal_eval(node.value)
            if type(value) is int:
                values.append(value)
    if values != [2026081317]:
        raise ValueError(f"workspace schema lineage drift: {values!r}")
    return values[0]


def verify(path: Path = ADR) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        value = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": "code_assist.notion.internal_collaborative_engineering_decision_verification.v1",
            "status": "fail",
            "error_count": 1,
            "errors": [{"code": "ADR_READ_FAILED", "subject": str(path), "message": str(exc)}],
        }
    check(isinstance(value, dict), "ADR_SHAPE", "$", "ADR must be an object")
    if not isinstance(value, dict):
        return {
            "contract": "code_assist.notion.internal_collaborative_engineering_decision_verification.v1",
            "status": "fail",
            "error_count": len(errors),
            "errors": errors,
        }
    check(value.get("schema_version") == 1, "ADR_VERSION", "$.schema_version", "schema version drift")
    check(value.get("contract") == EXPECTED_CONTRACT, "ADR_CONTRACT", "$.contract", "contract drift")
    check(value.get("decision_id") == "G0-E", "DECISION_ID", "$.decision_id", "decision id drift")
    check(value.get("status") == "frozen_engineering_subgate", "DECISION_STATUS", "$.status", "decision is not frozen")

    prerequisites = value.get("prerequisite_evidence", {})
    check(
        isinstance(prerequisites, dict) and set(prerequisites) == {"A0A-P2", "E0-P4", "T0A-P3", "O0A-P3"},
        "PREREQUISITE_DENOMINATOR",
        "$.prerequisite_evidence",
        "exact four prerequisite packets required",
    )
    if isinstance(prerequisites, dict):
        for packet_id, item in prerequisites.items():
            if not isinstance(item, dict):
                errors.append({"code": "PREREQUISITE_SHAPE", "subject": packet_id, "message": "prerequisite must be an object"})
                continue
            evidence_path = (CONTRACT_ROOT / str(item.get("path"))).resolve()
            check(evidence_path.is_file(), "PREREQUISITE_MISSING", packet_id, "evidence file missing")
            if evidence_path.is_file():
                check(_sha256_file(evidence_path) == item.get("sha256"), "PREREQUISITE_HASH", packet_id, "evidence hash drift")
            check(str(item.get("packet_status", "")).startswith("gate_passed"), "PREREQUISITE_STATUS", packet_id, "prerequisite not gate-passed")

    authority = value.get("authority_inputs", {})
    expected_authority = {
        "authority_adr",
        "winner_adr",
        "gateway_topology_adr",
        "protocol_contract",
        "gateway_contract",
        "workspace_runtime_lineage",
        "parallel_archive_contract",
    }
    check(isinstance(authority, dict) and set(authority) == expected_authority, "AUTHORITY_DENOMINATOR", "$.authority_inputs", "authority input denominator drift")
    if isinstance(authority, dict):
        for key, item in authority.items():
            if not isinstance(item, dict):
                errors.append({"code": "AUTHORITY_SHAPE", "subject": key, "message": "authority input must be an object"})
                continue
            authority_path = (CONTRACT_ROOT / str(item.get("path"))).resolve()
            check(authority_path.is_file(), "AUTHORITY_MISSING", key, "authority file missing")
            if authority_path.is_file():
                check(_sha256_file(authority_path) == item.get("sha256"), "AUTHORITY_HASH", key, "authority hash drift")

    decisions = value.get("decisions", {})
    check(
        isinstance(decisions, dict)
        and set(decisions)
        == {
            "canonical_authority_architecture",
            "gateway_and_crdt_winner",
            "protocol_and_compaction_binding",
            "next_available_forward_schema_version",
        },
        "DECISION_DENOMINATOR",
        "$.decisions",
        "exact four engineering decisions required",
    )
    if isinstance(decisions, dict):
        canonical = decisions.get("canonical_authority_architecture", {})
        winner = decisions.get("gateway_and_crdt_winner", {})
        protocol = decisions.get("protocol_and_compaction_binding", {})
        schema = decisions.get("next_available_forward_schema_version", {})
        check(canonical.get("selected") == "workspace_canonical_collaborative_documents", "CANONICAL_SELECTION", "$.decisions.canonical_authority_architecture.selected", "canonical authority drift")
        check(canonical.get("dual_write_allowed") is False and canonical.get("second_brain_database_merged_into_notion") is False, "AUTHORITY_BOUNDARY", "$.decisions.canonical_authority_architecture", "authority boundary weakened")
        check(winner.get("selected_candidate_id") == "PY_OPAQUE_NODE_COMPACTOR", "WINNER_SELECTION", "$.decisions.gateway_and_crdt_winner", "selected runtime drift")
        check(winner.get("gateway_owns_IAM_or_ACK") is False and winner.get("compactor_network_listener_allowed") is False, "RUNTIME_BOUNDARY", "$.decisions.gateway_and_crdt_winner", "runtime boundary weakened")
        check(protocol.get("runtime_binding_sha256") == "sha256:99b8e01015bbf92c385493bebbc08b6600add4b43afd2ab6e1746a6bb8e74057", "PROTOCOL_BINDING", "$.decisions.protocol_and_compaction_binding", "runtime binding drift")
        check(protocol.get("canonical_commit_before_ACK") is True and protocol.get("workspace_and_page_epoch_bound") is True, "PROTOCOL_SAFETY", "$.decisions.protocol_and_compaction_binding", "protocol safety drift")
        try:
            observed_version = _workspace_schema_version(PACKAGE_ROOT / "notion_workspace_store.py")
        except (OSError, UnicodeError, ValueError, SyntaxError) as exc:
            observed_version = None
            errors.append({"code": "SCHEMA_LINEAGE_READ_FAILED", "subject": "notion_workspace_store.py", "message": str(exc)})
        check(schema.get("current_version") == observed_version == 2026081317, "SCHEMA_CURRENT", "$.decisions.next_available_forward_schema_version.current_version", "current version drift")
        check(schema.get("parallel_reserved_version") == 2026081318, "SCHEMA_RESERVED", "$.decisions.next_available_forward_schema_version.parallel_reserved_version", "parallel reservation drift")
        check(schema.get("selected_version") == 2026082401, "SCHEMA_SELECTED", "$.decisions.next_available_forward_schema_version.selected_version", "selected forward version drift")
        check(schema.get("selected_version") != schema.get("parallel_reserved_version"), "SCHEMA_COLLISION", "$.decisions.next_available_forward_schema_version", "schema version collision")
        check(schema.get("migration_file_created") is False and schema.get("DDL_frozen") is False and schema.get("runtime_schema_changed") is False, "SCHEMA_FALSE_IMPLEMENTATION", "$.decisions.next_available_forward_schema_version", "decision falsely claims implementation")

    boundary = value.get("g0_aggregate_boundary", {})
    check(
        isinstance(boundary, dict)
        and boundary.get("G0_E_status") == "frozen"
        and boundary.get("G0_U_status") == "open"
        and boundary.get("G0_status") == "open"
        and boundary.get("implementation_packets_requiring_G0_may_start") is False,
        "G0_BOUNDARY",
        "$.g0_aggregate_boundary",
        "G0 aggregate must remain open",
    )
    claims = value.get("claims", {})
    check(
        isinstance(claims, dict)
        and claims.get("G0_E_frozen") is True
        and claims.get("G0_U_frozen") is False
        and claims.get("G0_aggregate_frozen") is False
        and claims.get("schema_migration_implemented") is False
        and claims.get("product_complete") is False
        and claims.get("active_shared_goal_complete") is False,
        "CLAIMS_FALSE_GREEN",
        "$.claims",
        "engineering decision completion truth drift",
    )
    return {
        "contract": "code_assist.notion.internal_collaborative_engineering_decision_verification.v1",
        "status": "pass" if not errors else "fail",
        "error_count": len(errors),
        "errors": errors,
        "adr_sha256": _sha256_file(path),
        "engineering_decision_sha256": _canonical_sha256(decisions) if isinstance(decisions, Mapping) else None,
        "selected_forward_schema_version": decisions.get("next_available_forward_schema_version", {}).get("selected_version") if isinstance(decisions, dict) else None,
        "claims": {
            "G0_E_frozen": not errors,
            "G0_U_frozen": False,
            "G0_aggregate_frozen": False,
            "schema_migration_implemented": False,
            "product_complete": False,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adr", type=Path, default=ADR)
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = verify(args.adr)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
