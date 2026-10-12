"""Verify the C0-P1/C0-P2 live collaboration runtime contract offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from code_assist_v1.tools.database_feature_successor import owns_current_artifact as f0_owns_current_artifact

from code_assist_v1.tools.verify_notion_workspace_route_contract import (
    verify_contract as verify_route_contract,
)
from code_assist_v1.tools.verify_internal_collaborative_second_brain_boundary import (
    verify as verify_second_brain_boundary,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_live_collaboration_runtime.v1.json"
)
SUCCESSOR_CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_editor_ui.v1.json"
)
LATEST_SUCCESSOR_CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_convergence_corpus.v1.json"
)
SECOND_BRAIN_SUCCESSOR_CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_second_brain_boundary.v1.json"
)
PUBLICATION_SUCCESSOR_CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_second_brain_publication.v1.json"
)
EXPECTED_CONTRACT = "code_assist.notion.internal_live_collaboration_runtime.c0.v1"
EXPECTED_PREREQUISITES = {"I0-P1", "T0B-P3", "K0-P1", "I0-P2", "UI0-P1"}
EXPECTED_SOURCE_PATHS = (
    "code_assist_v1/notion_workspace_live_collaboration_migration.py",
    "code_assist_v1/notion_collaboration/durable_transport.py",
    "code_assist_v1/notion_collaboration/live_authority.py",
    "code_assist_v1/notion_collaboration/iam_runtime.py",
    "code_assist_v1/notion_collaboration/runtime_gateway.py",
    "code_assist_v1/notion_collaboration/gateway_service.py",
    "code_assist_v1/notion_collaboration/approved_dev_pair.py",
    "code_assist_v1/routes_notion_workspace.py",
    "code_assist_v1/notion_static/index.html",
    "code_assist_v1/notion_static/notion.css",
    "code_assist_v1/notion_static/notion.js",
)
EXPECTED_TEST_PATHS = (
    "code_assist_v1/notion_collaboration/c0_p2_browser.py",
    "code_assist_v1/tests/notion_collaboration/test_live_collaboration_authority.py",
    "code_assist_v1/tests/notion_collaboration/test_runtime_gateway.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
    "code_assist_v1/tests/notion_collaboration/browser/test_c0_p2_live_collaboration.py",
    "code_assist_v1/tests/test_notion_workspace_auth_isolation.py",
    "code_assist_v1/tests/test_verify_notion_workspace_route_contract.py",
    "code_assist_v1/tools/internal_collaborative_validation.py",
    "code_assist_v1/tools/verify_notion_workspace_route_contract.py",
)
SUCCESSOR_OWNED_PATHS = {
    "code_assist_v1/notion_collaboration/live_authority.py",
    "code_assist_v1/notion_static/index.html",
    "code_assist_v1/notion_static/notion.css",
    "code_assist_v1/notion_static/notion.js",
    "code_assist_v1/tools/internal_collaborative_validation.py",
}
LATEST_SUCCESSOR_OWNED_PATHS = {
    "code_assist_v1/tools/internal_collaborative_validation.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
}
SECOND_BRAIN_SUCCESSOR_OWNED_PATHS = {
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
}
PUBLICATION_SUCCESSOR_OWNED_PATHS = {
    "code_assist_v1/notion_collaboration/approved_dev_pair.py",
    "code_assist_v1/notion_collaboration/iam_runtime.py",
    "code_assist_v1/tools/internal_collaborative_validation.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
}


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _load(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {value}")
        ),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _repository_file(relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    candidate = (REPOSITORY_ROOT / relative).resolve(strict=False)
    try:
        candidate.relative_to(REPOSITORY_ROOT.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _successor_owns_current_artifact(relative: str, target: Path) -> bool:
    if f0_owns_current_artifact(relative, target):
        return True
    successor_contracts = (
        (
            SUCCESSOR_CONTRACT_PATH,
            SUCCESSOR_OWNED_PATHS,
            "E1-P2",
            "E1_P2_gate_passed",
        ),
        (
            LATEST_SUCCESSOR_CONTRACT_PATH,
            LATEST_SUCCESSOR_OWNED_PATHS,
            "E1-P3",
            "E1_P3_gate_passed",
        ),
        (
            SECOND_BRAIN_SUCCESSOR_CONTRACT_PATH,
            SECOND_BRAIN_SUCCESSOR_OWNED_PATHS,
            "S0A-P1",
            "S0A_P1_gate_passed",
        ),
        (
            PUBLICATION_SUCCESSOR_CONTRACT_PATH,
            PUBLICATION_SUCCESSOR_OWNED_PATHS,
            "S0A-P2",
            "S0A_P2_gate_passed",
        ),
    )
    for contract_path, owned_paths, packet_id, gate_claim in successor_contracts:
        if relative not in owned_paths:
            continue
        if contract_path == SECOND_BRAIN_SUCCESSOR_CONTRACT_PATH:
            report = verify_second_brain_boundary(contract_path)
            if report.get("status") != "pass" or report.get("gate_passed") is not True:
                continue
        try:
            successor = _load(contract_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(successor, dict):
            continue
        rows = [
            *successor.get("source_manifest", []),
            *successor.get("test_manifest", []),
        ]
        binding = next(
            (
                item
                for item in rows
                if isinstance(item, dict) and item.get("path") == relative
            ),
            None,
        )
        if (
            successor.get("packet_id") == packet_id
            and successor.get("status") == "implemented_and_validated"
            and successor.get("claims", {}).get(gate_claim) is True
            and isinstance(binding, dict)
            and binding.get("sha256") == _sha256_file(target)
        ):
            return True
    return False


def verify(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    allow_successor = path.resolve(strict=False) == CONTRACT_PATH.resolve(strict=False)

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        value = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": "code_assist.notion.internal_live_collaboration_runtime.audit.v1",
            "status": "fail",
            "error_count": 1,
            "errors": [{"code": "CONTRACT_READ_FAILED", "subject": str(path), "message": str(exc)}],
        }
    check(isinstance(value, dict), "CONTRACT_SHAPE", "$", "contract must be an object")
    if not isinstance(value, dict):
        return {"status": "fail", "error_count": len(errors), "errors": errors}
    check(
        value.get("schema_version") == 1
        and value.get("contract") == EXPECTED_CONTRACT
        and value.get("packet_ids") == ["C0-P1", "C0-P2"]
        and value.get("status") == "implemented_and_validated",
        "CONTRACT_IDENTITY",
        "$",
        "C0 contract identity or status drift",
    )

    prerequisites = value.get("prerequisite_evidence", {})
    check(
        isinstance(prerequisites, dict) and set(prerequisites) == EXPECTED_PREREQUISITES,
        "PREREQUISITE_DENOMINATOR",
        "$.prerequisite_evidence",
        "exact C0 prerequisite set required",
    )
    if isinstance(prerequisites, dict):
        for identifier, item in prerequisites.items():
            relative = item.get("path") if isinstance(item, dict) else None
            target = (CONTRACT_PATH.parent / str(relative or "")).resolve(strict=False)
            try:
                target.relative_to(REPOSITORY_ROOT.resolve())
            except ValueError:
                target = None
            else:
                if not target.is_file():
                    target = None
            check(target is not None, "PREREQUISITE_MISSING", identifier, "checkpoint missing")
            if target is not None:
                check(
                    _sha256_file(target) == item.get("sha256"),
                    "PREREQUISITE_HASH_DRIFT",
                    identifier,
                    "checkpoint hash drift",
                )
            check(
                isinstance(item, dict) and item.get("status") == "gate_passed",
                "PREREQUISITE_STATUS",
                identifier,
                "gate-passed prerequisite required",
            )

    runtime = value.get("runtime_contract", {})
    check(
        isinstance(runtime, dict)
        and [runtime.get("source_schema_version"), runtime.get("target_schema_version")]
        == [2026082403, 2026082404]
        and runtime.get("target_schema_name") == "notion_workspace_v2_live_collaboration"
        and runtime.get("development_ports") == [10111, 10112, 10113]
        and runtime.get("approval_id") == "AP-DEV-PAIR"
        and runtime.get("approved_principals") == ["admin", "user01"]
        and runtime.get("production_ports_changed") is False
        and runtime.get("production_mutation_allowed") is False,
        "RUNTIME_BOUNDARY",
        "$.runtime_contract",
        "schema or development/production boundary drift",
    )
    durability = value.get("durability_contract", {})
    check(
        isinstance(durability, dict)
        and durability.get("canonical_domains") == ["comments", "reactions", "mentions", "notifications"]
        and durability.get("command_commit_contains")
        == ["canonical_state", "domain_event", "audit_event", "outbox_event", "idempotency_receipt"]
        and all(
            durability.get(field) is True
            for field in (
                "durable_commit_precedes_live_delivery",
                "comment_domain_event_payload_is_identifier_only",
                "comment_content_excluded_from_live_event_payload",
                "comment_soft_delete_and_resolution_supported",
            )
        )
        and durability.get("optimistic_comment_revision_conflict_status") == 409,
        "DURABILITY_CONTRACT",
        "$.durability_contract",
        "canonical durability or event redaction drift",
    )
    presence = value.get("presence_contract", {})
    check(
        isinstance(presence, dict)
        and presence.get("storage") == "process_memory_only"
        and presence.get("canonical_table_count") == 0
        and [presence.get(key) for key in ("minimum_ttl_ms", "default_ttl_ms", "maximum_ttl_ms")]
        == [5000, 15000, 60000]
        and presence.get("heartbeat_update_and_leave_coalesced") is True
        and presence.get("disconnect_and_expiry_emit_leave") is True
        and presence.get("presence_history_persisted") is False,
        "PRESENCE_CONTRACT",
        "$.presence_contract",
        "ephemeral presence or TTL drift",
    )
    revocation = value.get("delivery_and_revocation_contract", {})
    check(
        isinstance(revocation, dict)
        and all(
            revocation.get(field) is True
            for field in (
                "same_origin_one_time_realtime_ticket",
                "permission_revalidated_before_replay_and_send",
                "reconnect_replays_committed_domain_events",
                "revocation_closes_subscription",
                "inbox_omits_documents_after_access_revocation",
                "stable_non_oracle_errors",
            )
        )
        and revocation.get("post_revoke_http_status") == 404
        and revocation.get("post_revoke_ticket_status") == 404
        and revocation.get("post_revoke_http_websocket_replay_cache_search_inbox_delivery_count") == 0,
        "REVOCATION_FENCE",
        "$.delivery_and_revocation_contract",
        "live delivery or revoke fence drift",
    )
    browser = value.get("browser_gate", {})
    check(
        isinstance(browser, dict)
        and browser.get("independent_ephemeral_chrome_contexts") == 2
        and browser.get("principal_count") == 2
        and browser.get("presence_comment_and_reaction_delivery_budget_ms") == 2500
        and [browser.get("presence_expiry_lower_bound_ms"), browser.get("presence_expiry_upper_bound_ms")]
        == [12000, 22000]
        and browser.get("reconnect_budget_ms") == 7000
        and all(
            browser.get(field) is True
            for field in (
                "comment_delivery_without_reload",
                "reaction_delivery_without_reload",
                "mention_inbox_verified",
                "post_revoke_zero_delivery_verified",
            )
        )
        and browser.get("unapproved_network_target_escape_count") == 0
        and browser.get("credentials_cookie_or_session_value_recorded") is False,
        "BROWSER_GATE",
        "$.browser_gate",
        "two-principal browser denominator drift",
    )

    for key, expected_paths in (("source_manifest", EXPECTED_SOURCE_PATHS), ("test_manifest", EXPECTED_TEST_PATHS)):
        manifest = value.get(key)
        paths = tuple(
            item.get("path")
            for item in manifest
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        ) if isinstance(manifest, list) else ()
        check(paths == expected_paths, "MANIFEST_DENOMINATOR", f"$.{key}", "path set or order drift")
        if isinstance(manifest, list):
            for index, item in enumerate(manifest):
                target = _repository_file(item.get("path")) if isinstance(item, dict) else None
                check(target is not None, "MANIFEST_FILE_MISSING", f"$.{key}[{index}]", "file missing")
                if target is not None:
                    check(
                        _sha256_file(target) == item.get("sha256")
                        or _successor_owns_current_artifact(str(item.get("path")), target),
                        "MANIFEST_HASH_DRIFT",
                        str(item.get("path")),
                        "bound file hash drift",
                    )

    route = value.get("route_contract", {})
    route_report, route_exit = verify_route_contract()
    check(
        isinstance(route, dict)
        and route_exit == 0
        and route_report.get("status") == "pass"
        and (
            route_report.get("summary", {}).get("actual_route_count") == 28
            or (
                allow_successor
                and route_report.get("summary", {}).get("actual_route_count") == 30
                and _successor_owns_current_artifact(
                    "code_assist_v1/routes_notion_workspace.py",
                    PROJECT_ROOT / "routes_notion_workspace.py",
                )
            )
        )
        and route_report.get("summary", {}).get("operation_count") == 42
        and route.get("rule_count") == 28
        and route.get("operation_count") == 40,
        "ROUTE_CONTRACT",
        "$.route_contract",
        "route or semantic overlay drift",
    )
    if isinstance(route, dict):
        for path_key, hash_key in (("manifest_path", "manifest_sha256"), ("semantics_path", "semantics_sha256")):
            target = _repository_file(route.get(path_key))
            check(target is not None, "ROUTE_FILE_MISSING", f"$.route_contract.{path_key}", "file missing")
            if target is not None:
                check(
                    _sha256_file(target) == route.get(hash_key)
                    or (
                        allow_successor
                        and _successor_owns_current_artifact(
                            str(route.get(path_key)), target
                        )
                    ),
                    "ROUTE_HASH_DRIFT",
                    str(route.get(path_key)),
                    "route artifact hash drift",
                )

    claims = value.get("claims", {})
    check(
        isinstance(claims, dict)
        and all(
            claims.get(field) is True
            for field in ("C0_P1_gate_passed", "C0_P2_gate_passed", "C0_GATE_001_closed", "RT_002_closed", "IAM_003_closed")
        )
        and all(
            claims.get(field) is False
            for field in ("production_changed", "product_complete", "active_shared_goal_complete")
        ),
        "CLAIMS_FALSE_GREEN",
        "$.claims",
        "C0 closure or non-completion claims drift",
    )
    normalized = sorted(
        {(item["code"], item["subject"], item["message"]): item for item in errors}.values(),
        key=lambda item: (item["code"], item["subject"], item["message"]),
    )
    return {
        "contract": "code_assist.notion.internal_live_collaboration_runtime.audit.v1",
        "status": "pass" if not normalized else "fail",
        "error_count": len(normalized),
        "errors": normalized,
        "contract_sha256": _sha256_file(path),
        "source_count": len(EXPECTED_SOURCE_PATHS),
        "test_count": len(EXPECTED_TEST_PATHS),
        "route_count": route_report.get("summary", {}).get("actual_route_count"),
        "operation_count": route_report.get("summary", {}).get("operation_count"),
        "gate_passed": not normalized,
        "product_complete": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report = verify(args.contract)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
