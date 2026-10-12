"""Verify the I0-P2 collaborative sharing runtime contract without live access."""

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


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_sharing_runtime.v1.json"
)
SUCCESSOR_CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_editor_ui.v1.json"
)
SECOND_BRAIN_SUCCESSOR_CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_second_brain_publication.v1.json"
)
EXPECTED_CONTRACT = "code_assist.notion.internal_collaborative_sharing_runtime.i0_p2.v1"
EXPECTED_SOURCE_PATHS = (
    "code_assist_v1/notion_workspace_collaboration_migration.py",
    "code_assist_v1/notion_collaboration/approved_dev_pair.py",
    "code_assist_v1/notion_collaboration/gateway_service.py",
    "code_assist_v1/notion_collaboration/iam_runtime.py",
    "code_assist_v1/notion_collaboration/runtime_gateway.py",
    "code_assist_v1/notion_static/index.html",
    "code_assist_v1/notion_static/notion.css",
    "code_assist_v1/notion_static/notion.js",
    "code_assist_v1/routes_notion_workspace.py",
)
EXPECTED_TEST_PATHS = (
    "code_assist_v1/notion_collaboration/i0_p2_browser.py",
    "code_assist_v1/tests/notion_collaboration/browser/test_i0_p2_live_share_journey.py",
    "code_assist_v1/tests/notion_collaboration/browser/test_share_and_workspace_membership.py",
    "code_assist_v1/tests/test_notion_navigation_ui.js",
    "code_assist_v1/tests/test_notion_workspace_auth_isolation.py",
    "code_assist_v1/tests/test_verify_notion_workspace_route_contract.py",
    "code_assist_v1/tools/verify_notion_workspace_route_contract.py",
)
SUCCESSOR_OWNED_PATHS = {
    "code_assist_v1/notion_static/index.html",
    "code_assist_v1/notion_static/notion.css",
    "code_assist_v1/notion_static/notion.js",
    "code_assist_v1/tests/test_notion_navigation_ui.js",
}
SECOND_BRAIN_SUCCESSOR_OWNED_PATHS = {
    "code_assist_v1/notion_collaboration/approved_dev_pair.py",
    "code_assist_v1/notion_collaboration/iam_runtime.py",
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
    contracts = (
        (SUCCESSOR_CONTRACT_PATH, SUCCESSOR_OWNED_PATHS, "E1-P2", "E1_P2_gate_passed"),
        (
            SECOND_BRAIN_SUCCESSOR_CONTRACT_PATH,
            SECOND_BRAIN_SUCCESSOR_OWNED_PATHS,
            "S0A-P2",
            "S0A_P2_gate_passed",
        ),
    )
    for contract_path, owned_paths, packet_id, gate_claim in contracts:
        if relative not in owned_paths:
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
            "contract": "code_assist.notion.internal_collaborative_sharing_runtime.audit.v1",
            "status": "fail",
            "error_count": 1,
            "errors": [
                {"code": "CONTRACT_READ_FAILED", "subject": str(path), "message": str(exc)}
            ],
        }
    check(isinstance(value, dict), "CONTRACT_SHAPE", "$", "contract must be an object")
    if not isinstance(value, dict):
        return {"status": "fail", "error_count": len(errors), "errors": errors}
    check(
        value.get("schema_version") == 1
        and value.get("contract") == EXPECTED_CONTRACT
        and value.get("packet_id") == "I0-P2"
        and value.get("status") == "implemented_and_validated",
        "CONTRACT_IDENTITY",
        "$",
        "I0-P2 contract identity or status drift",
    )

    prerequisites = value.get("prerequisite_evidence", {})
    check(
        isinstance(prerequisites, dict) and set(prerequisites) == {"I0-P1", "UI0-P1"},
        "PREREQUISITE_DENOMINATOR",
        "$.prerequisite_evidence",
        "exact I0-P1 and UI0-P1 prerequisites required",
    )
    if isinstance(prerequisites, dict):
        for identifier, item in prerequisites.items():
            target = (
                (CONTRACT_PATH.parent / str(item.get("path", ""))).resolve(strict=False)
                if isinstance(item, dict)
                else None
            )
            if target is not None:
                try:
                    target.relative_to(REPOSITORY_ROOT.resolve())
                except ValueError:
                    target = None
                else:
                    if not target.is_file():
                        target = None
            check(target is not None, "PREREQUISITE_MISSING", identifier, "file missing")
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
        and runtime.get("schema_version") == 2026082405
        and runtime.get("schema_name") == "notion_workspace_v2_collaborative_editing_authority"
        and [runtime.get(key) for key in (
            "development_agent_port",
            "development_notion_port",
            "development_gateway_port",
        )] == [10111, 10112, 10113]
        and runtime.get("approval_id") == "AP-DEV-PAIR"
        and runtime.get("production_mutation_allowed") is False,
        "RUNTIME_BOUNDARY",
        "$.runtime_contract",
        "schema, approved development pair, or production boundary drift",
    )
    sharing = value.get("sharing_contract", {})
    check(
        isinstance(sharing, dict)
        and sharing.get("page_roles")
        == ["view", "comment", "edit_content", "edit", "full_access"]
        and sharing.get("principal_types") == ["subject", "group", "teamspace"]
        and sharing.get("workspace_membership_actions")
        == ["invite", "role_change", "revoke"]
        and all(
            sharing.get(field) is True
            for field in (
                "direct_and_inherited_grants",
                "inherited_access_explanation",
                "effective_access_legacy_projection_reconciled",
                "workspace_revoke_visibility_fence",
            )
        )
        and [sharing.get(field) for field in (
            "page_revoke_non_oracle_status",
            "optimistic_epoch_conflict_status",
            "authority_unavailable_status",
        )] == [404, 409, 503],
        "SHARING_CONTRACT",
        "$.sharing_contract",
        "sharing role, inheritance, revoke, or failure semantics drift",
    )

    for key, expected_paths in (
        ("source_manifest", EXPECTED_SOURCE_PATHS),
        ("test_manifest", EXPECTED_TEST_PATHS),
    ):
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
        and route.get("operation_count") == 40
        and route.get("collaboration_operation_count") == 15
        and route.get("share_operation_count") == 3,
        "ROUTE_CONTRACT",
        "$.route_contract",
        "route or semantic overlay drift",
    )
    if isinstance(route, dict):
        for path_key, hash_key in (
            ("manifest_path", "manifest_sha256"),
            ("semantics_path", "semantics_sha256"),
        ):
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
            for field in (
                "I0_P2_source_implemented",
                "normal_share_invite_role_change_and_revoke_UI_implemented",
                "inherited_access_explanation_implemented",
                "multi_principal_browser_gate_required",
                "persistent_development_runtime_migrated",
            )
        )
        and all(
            claims.get(field) is False
            for field in ("production_changed", "product_complete", "active_shared_goal_complete")
        ),
        "CLAIMS_FALSE_GREEN",
        "$.claims",
        "implementation or non-completion claims drift",
    )
    normalized = sorted(
        {
            (item["code"], item["subject"], item["message"]): item
            for item in errors
        }.values(),
        key=lambda item: (item["code"], item["subject"], item["message"]),
    )
    return {
        "contract": "code_assist.notion.internal_collaborative_sharing_runtime.audit.v1",
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
