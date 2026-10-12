from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
from typing import Any, Sequence

from flask import Flask

from code_assist_v1 import notion_workspace_history_authority_migration as migration
from code_assist_v1.tools.search_authority_successor import owns_current_artifact


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_history_authority.v1.json"
)
EXPECTED_SOURCE_PATHS = [
    "code_assist_v1/notion_workspace_history_authority_migration.py",
    "code_assist_v1/notion_collaboration/history_authority.py",
    "code_assist_v1/notion_collaboration/editing_authority.py",
    "code_assist_v1/notion_collaboration/iam_runtime.py",
    "code_assist_v1/notion_collaboration/approved_dev_pair.py",
    "code_assist_v1/routes_notion_history.py",
    "code_assist_v1/routes_notion_workspace.py",
    "code_assist_v1/app_notion.py",
    "code_assist_v1/notion_static/notion.js",
    "code_assist_v1/notion_static/index.html",
    "code_assist_v1/tools/database_feature_successor.py",
    "code_assist_v1/tools/file_authority_successor.py",
    "code_assist_v1/tools/verify_internal_collaborative_database_features.py",
    "code_assist_v1/tools/verify_internal_collaborative_file_authority.py",
    "code_assist_v1/tools/verify_internal_collaborative_history_authority.py",
    "code_assist_v1/tools/internal_collaborative_validation.py",
]
EXPECTED_TEST_PATHS = [
    "code_assist_v1/tests/notion_collaboration/test_history_authority.py",
    "code_assist_v1/tests/notion_collaboration/test_database_features.py",
    "code_assist_v1/tests/notion_collaboration/browser/test_share_and_workspace_membership.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_history_authority.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
]
EXPECTED_ROUTES = sorted(
    [
        ("GET", "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history", "api_notion_collaborative_history"),
        ("GET", "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/updates", "api_notion_collaborative_page_updates"),
        ("GET", "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/diff", "api_notion_collaborative_history_diff"),
        ("GET", "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/policy", "api_notion_collaborative_history_policy"),
        ("GET", "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/revisions/<int:revision>", "api_notion_collaborative_history_revision"),
        ("POST", "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/history/revisions/<int:revision>/restore", "api_notion_collaborative_history_restore"),
    ]
)


def _load(path: Path) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _repository_file(relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    target = (REPOSITORY_ROOT / relative).resolve(strict=False)
    try:
        target.relative_to(REPOSITORY_ROOT.resolve())
    except ValueError:
        return None
    return target if target.is_file() else None


def _verify_manifest(
    value: Any,
    expected: list[str],
    subject: str,
    errors: list[dict[str, str]],
    *,
    allow_successor: bool,
) -> None:
    if not isinstance(value, list):
        errors.append({"code": "MANIFEST_SHAPE", "subject": subject, "message": "array required"})
        return
    paths = [item.get("path") for item in value if isinstance(item, dict)]
    if paths != expected:
        errors.append({"code": "MANIFEST_DENOMINATOR", "subject": subject, "message": "exact ordered paths required"})
    for index, item in enumerate(value):
        target = _repository_file(item.get("path")) if isinstance(item, dict) else None
        if target is None:
            errors.append({"code": "MANIFEST_FILE_MISSING", "subject": f"{subject}[{index}]", "message": "file missing"})
        elif item.get("sha256") != _sha256(target) and not (
            allow_successor and owns_current_artifact(str(item.get("path")), target)
        ):
            errors.append({"code": "MANIFEST_HASH_DRIFT", "subject": str(item.get("path")), "message": "bound file hash drift"})


def _actual_routes() -> list[tuple[str, str, str]]:
    with redirect_stdout(io.StringIO()):
        from code_assist_v1.routes_notion_history import register_notion_history_routes

        app = Flask("f0-p3-history-route-audit", static_folder=None)
        register_notion_history_routes(app)
    return sorted(
        (method, rule.rule, rule.endpoint)
        for rule in app.url_map.iter_rules()
        if rule.endpoint != "static"
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"})
    )


def verify(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    allow_successor = path.resolve(strict=False) == CONTRACT_PATH.resolve(strict=False)

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        value = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {"contract": "code_assist.notion.internal_collaborative_history_authority.audit.v1", "status": "fail", "error_count": 1, "errors": [{"code": "CONTRACT_READ_FAILED", "subject": str(path), "message": str(exc)}], "gate_passed": False, "product_complete": False}
    if not isinstance(value, dict):
        return {"status": "fail", "error_count": 1, "errors": [{"code": "CONTRACT_SHAPE", "subject": "$", "message": "object required"}], "gate_passed": False, "product_complete": False}
    check(
        value.get("schema_version") == 1
        and value.get("contract") == "code_assist.notion.internal_collaborative_history_authority.f0_p3.v1"
        and value.get("packet_id") == "F0-P3"
        and value.get("status") == "implemented_and_validated",
        "CONTRACT_IDENTITY", "$", "F0-P3 identity or status drift",
    )
    prerequisite = value.get("prerequisite_evidence", {})
    checkpoint = prerequisite.get("F0-P2_checkpoint", {}) if isinstance(prerequisite, dict) else {}
    checkpoint_path = _repository_file(checkpoint.get("path")) if isinstance(checkpoint, dict) else None
    check(
        prerequisite.get("G0") == "frozen"
        and prerequisite.get("F0-P2") == "gate_passed"
        and prerequisite.get("E1-P3") == "gate_passed"
        and checkpoint_path is not None
        and checkpoint.get("sha256") == _sha256(checkpoint_path),
        "PREREQUISITE_DRIFT", "$.prerequisite_evidence", "exact frozen F0-P2 prerequisite required",
    )
    instruction = value.get("dynamic_instruction_binding", {})
    agents = _repository_file(instruction.get("path")) if isinstance(instruction, dict) else None
    check(
        agents is not None
        and instruction.get("sha256") == _sha256(agents)
        and instruction.get("write_policy") == "read_only",
        "DYNAMIC_INSTRUCTION_DRIFT", "AGENTS.md", "current read-only instruction binding required",
    )
    lineage = value.get("migration_lineage", {})
    check(
        lineage.get("source_version") == migration.SOURCE_VERSION == 2026082408
        and lineage.get("target_version") == migration.TARGET_VERSION == 2026082409
        and lineage.get("target_name") == migration.TARGET_NAME
        and lineage.get("target_checksum") == migration.TARGET_CHECKSUM
        and lineage.get("target_actual_schema_sha256") == migration.TARGET_ACTUAL_SCHEMA_SHA256
        and lineage.get("forward_only") is True,
        "MIGRATION_LINEAGE_DRIFT", "$.migration_lineage", "exact frozen 2409 lineage required",
    )
    history = value.get("history_contract", {})
    check(
        history.get("user_visible_revision_retention") == "indefinite_no_automatic_deletion"
        and history.get("deterministic_bounded_diff") is True
        and history.get("restore_creates_new_revision") is True
        and history.get("restore_creates_new_crdt_generation") is True
        and history.get("restore_increments_document_epoch") is True
        and history.get("operation_id_replay") is True
        and history.get("audit_event_outbox_atomic") is True
        and history.get("publication_revision_identity_preserved") is True
        and history.get("committed_attachment_blob_history_retention") is True
        and history.get("raw_crdt_pruning_enabled") is False
        and history.get("database_restore_owned_by_F0_P5") is True,
        "HISTORY_CONTRACT_DRIFT", "$.history_contract", "collaborative history contract drift",
    )
    route_contract = value.get("route_contract", {})
    app_notion = (PROJECT_ROOT / "app_notion.py").read_text(encoding="utf-8")
    app_code = (PROJECT_ROOT / "app_code.py").read_text(encoding="utf-8")
    check(
        route_contract.get("registrar") == "code_assist_v1.routes_notion_history.register_notion_history_routes"
        and route_contract.get("notion_host_only") is True
        and route_contract.get("route_count") == 5
        and _actual_routes() == EXPECTED_ROUTES
        and app_notion.count("register_notion_history_routes(app)") == 1
        and "routes_notion_history" not in app_code,
        "ROUTE_CONTRACT_DRIFT", "$.route_contract", "exact Notion-only history surface required",
    )
    browser = value.get("browser_contract", {})
    browser_source = (PROJECT_ROOT / "notion_static" / "notion.js").read_text(encoding="utf-8")
    check(
        browser.get("canonical_history_drawer") is True
        and browser.get("restore_confirmation") is True
        and browser.get("restore_reopens_live_page") is True
        and "collaborativeHistory" in browser_source
        and "const base = `/api/notion/workspaces/" in browser_source
        and "${base}/revisions/${encodeURIComponent(versionId)}/restore" in browser_source,
        "BROWSER_CONTRACT_DRIFT", "$.browser_contract", "canonical history UI contract required",
    )
    validation = value.get("validation", {})
    check(
        validation.get("history_authority_tests", {}).get("passed") == 3
        and validation.get("history_authority_tests", {}).get("failed") == 0
        and validation.get("editing_sharing_database_file_regression", {}).get("passed") == 22
        and validation.get("editing_sharing_database_file_regression", {}).get("failed") == 0
        and validation.get("workspace_route_contract_error_count") == 0
        and validation.get("host_composition_tests_passed") == 68
        and validation.get("persistent_development_schema_after") == 2026082409
        and validation.get("persistent_development_integrity") == "ok"
        and validation.get("persistent_development_ports") == [10111, 10112, 10113]
        and validation.get("production_10011_10012_changed") is False,
        "VALIDATION_DRIFT", "$.validation", "exact measured validation evidence required",
    )
    _verify_manifest(
        value.get("source_manifest"),
        EXPECTED_SOURCE_PATHS,
        "$.source_manifest",
        errors,
        allow_successor=allow_successor,
    )
    _verify_manifest(
        value.get("test_manifest"),
        EXPECTED_TEST_PATHS,
        "$.test_manifest",
        errors,
        allow_successor=allow_successor,
    )
    claims = value.get("claims", {})
    check(
        claims.get("F0_P3_gate_passed") is True
        and claims.get("HIST_001_closed") is True
        and claims.get("SB_001_closed") is True
        and claims.get("production_changed") is False
        and claims.get("product_complete") is False
        and claims.get("active_shared_goal_complete") is False,
        "CLAIMS_DRIFT", "$.claims", "partial completion truth drift",
    )
    return {
        "contract": "code_assist.notion.internal_collaborative_history_authority.audit.v1",
        "contract_sha256": _sha256(path),
        "status": "pass" if not errors else "fail",
        "error_count": len(errors),
        "errors": errors,
        "gate_passed": not errors,
        "source_count": len(EXPECTED_SOURCE_PATHS),
        "test_count": len(EXPECTED_TEST_PATHS),
        "route_count": len(EXPECTED_ROUTES),
        "target_version": migration.TARGET_VERSION,
        "product_complete": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    args = parser.parse_args(argv)
    report = verify(args.contract)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("status") == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
