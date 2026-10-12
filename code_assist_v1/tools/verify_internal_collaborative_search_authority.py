from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
from typing import Any, Sequence

from flask import Flask

from code_assist_v1 import notion_workspace_search_authority_migration as migration
from code_assist_v1.tools.adopted_surface_successor import owns_current_artifact


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
CONTRACT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_search_authority.v1.json"
)
EXPECTED_SOURCE_PATHS = [
    "code_assist_v1/notion_workspace_search_authority_migration.py",
    "code_assist_v1/notion_collaboration/search_authority.py",
    "code_assist_v1/notion_collaboration/editing_authority.py",
    "code_assist_v1/notion_collaboration/iam_runtime.py",
    "code_assist_v1/notion_collaboration/approved_dev_pair.py",
    "code_assist_v1/routes_notion_search.py",
    "code_assist_v1/routes_notion_workspace.py",
    "code_assist_v1/app_notion.py",
    "code_assist_v1/notion_static/notion.js",
    "code_assist_v1/notion_static/index.html",
    "code_assist_v1/tools/search_authority_successor.py",
    "code_assist_v1/tools/database_feature_successor.py",
    "code_assist_v1/tools/file_authority_successor.py",
    "code_assist_v1/tools/verify_internal_collaborative_history_authority.py",
    "code_assist_v1/tools/benchmark_collaborative_search.py",
    "code_assist_v1/tools/verify_internal_collaborative_search_authority.py",
    "code_assist_v1/tools/internal_collaborative_validation.py",
]
EXPECTED_TEST_PATHS = [
    "code_assist_v1/tests/notion_collaboration/test_search_authority.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_search_authority.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py",
    "code_assist_v1/tests/notion_collaboration/test_database_features.py",
    "code_assist_v1/tests/notion_collaboration/test_publication_high_water.py",
    "code_assist_v1/tests/notion_collaboration/browser/test_share_and_workspace_membership.py",
    "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_editor_ui.py",
]
EXPECTED_ROUTES = sorted(
    [
        ("GET", "/api/notion/workspaces/<workspace_id>/search", "api_notion_collaborative_search"),
        ("POST", "/api/notion/workspaces/<workspace_id>/search/rebuild", "api_notion_collaborative_search_rebuild"),
        ("GET", "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/backlinks", "api_notion_collaborative_backlinks"),
        ("GET", "/api/notion/workspaces/<workspace_id>/reminders", "api_notion_collaborative_reminders"),
        ("GET", "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/export_markdown", "api_notion_collaborative_export_markdown"),
        ("GET", "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/export_html", "api_notion_collaborative_export_html"),
        ("GET", "/api/notion/workspaces/<workspace_id>/pages/<path:page_id>/export_csv", "api_notion_collaborative_export_csv"),
        ("POST", "/api/notion/workspaces/<workspace_id>/imports/markdown", "api_notion_collaborative_import_markdown"),
        ("POST", "/api/notion/workspaces/<workspace_id>/imports/csv", "api_notion_collaborative_import_csv"),
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
        errors.append(
            {"code": "MANIFEST_SHAPE", "subject": subject, "message": "array required"}
        )
        return
    paths = [item.get("path") for item in value if isinstance(item, dict)]
    if paths != expected:
        errors.append(
            {
                "code": "MANIFEST_DENOMINATOR",
                "subject": subject,
                "message": "exact ordered paths required",
            }
        )
    for index, item in enumerate(value):
        target = _repository_file(item.get("path")) if isinstance(item, dict) else None
        if target is None:
            errors.append(
                {
                    "code": "MANIFEST_FILE_MISSING",
                    "subject": f"{subject}[{index}]",
                    "message": "file missing",
                }
            )
        elif item.get("sha256") != _sha256(target) and not (
            allow_successor and owns_current_artifact(str(item.get("path")), target)
        ):
            errors.append(
                {
                    "code": "MANIFEST_HASH_DRIFT",
                    "subject": str(item.get("path")),
                    "message": "bound file hash drift",
                }
            )


def _actual_routes() -> list[tuple[str, str, str]]:
    with redirect_stdout(io.StringIO()):
        from code_assist_v1.routes_notion_search import register_notion_search_routes

        app = Flask("f0-p4-search-route-audit", static_folder=None)
        register_notion_search_routes(app)
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
        return {
            "contract": "code_assist.notion.internal_collaborative_search_authority.audit.v1",
            "status": "fail",
            "error_count": 1,
            "errors": [
                {
                    "code": "CONTRACT_READ_FAILED",
                    "subject": str(path),
                    "message": str(exc),
                }
            ],
            "gate_passed": False,
            "product_complete": False,
        }
    if not isinstance(value, dict):
        return {
            "status": "fail",
            "error_count": 1,
            "errors": [
                {"code": "CONTRACT_SHAPE", "subject": "$", "message": "object required"}
            ],
            "gate_passed": False,
            "product_complete": False,
        }
    check(
        value.get("schema_version") == 1
        and value.get("contract")
        == "code_assist.notion.internal_collaborative_search_authority.f0_p4.v1"
        and value.get("packet_id") == "F0-P4"
        and value.get("status") == "implemented_and_validated",
        "CONTRACT_IDENTITY",
        "$",
        "F0-P4 identity or status drift",
    )
    prerequisite = value.get("prerequisite_evidence", {})
    f0 = prerequisite.get("F0-P3_checkpoint", {}) if isinstance(prerequisite, dict) else {}
    i0 = prerequisite.get("I0-P2_checkpoint", {}) if isinstance(prerequisite, dict) else {}
    f0_path = _repository_file(f0.get("path")) if isinstance(f0, dict) else None
    i0_path = _repository_file(i0.get("path")) if isinstance(i0, dict) else None
    check(
        prerequisite.get("G0") == "frozen"
        and prerequisite.get("F0-P3") == "gate_passed"
        and prerequisite.get("I0-P2") == "gate_passed"
        and f0_path is not None
        and f0.get("sha256") == _sha256(f0_path)
        and i0_path is not None
        and i0.get("sha256") == _sha256(i0_path),
        "PREREQUISITE_DRIFT",
        "$.prerequisite_evidence",
        "exact F0-P3 and I0-P2 prerequisite checkpoints required",
    )
    instruction = value.get("dynamic_instruction_binding", {})
    agents = _repository_file(instruction.get("path")) if isinstance(instruction, dict) else None
    check(
        agents is not None
        and instruction.get("sha256") == _sha256(agents)
        and instruction.get("write_policy") == "read_only",
        "DYNAMIC_INSTRUCTION_DRIFT",
        "AGENTS.md",
        "current read-only instruction binding required",
    )
    lineage = value.get("migration_lineage", {})
    check(
        lineage.get("source_version") == migration.SOURCE_VERSION == 2026082409
        and lineage.get("target_version") == migration.TARGET_VERSION == 2026082410
        and lineage.get("target_name") == migration.TARGET_NAME
        and lineage.get("target_checksum") == migration.TARGET_CHECKSUM
        and lineage.get("target_actual_schema_sha256")
        == migration.TARGET_ACTUAL_SCHEMA_SHA256
        and lineage.get("target_object_count") == len(migration.TARGET_OBJECTS)
        and lineage.get("forward_only") is True,
        "MIGRATION_LINEAGE_DRIFT",
        "$.migration_lineage",
        "exact frozen 2410 lineage required",
    )
    search = value.get("search_contract", {})
    check(
        search.get("canonical_content_and_backlink_indexing") is True
        and search.get("workspace_page_and_role_scoping") is True
        and search.get("current_iam_reauthorization_per_request") is True
        and search.get("principal_specific_result_or_snippet_cache") is False
        and search.get("http_cache_policy") == "no_store"
        and search.get("revoke_removes_results_backlinks_and_reminders") is True
        and search.get("restart_rebuild_and_high_water_convergence") is True
        and search.get("projection_integrity_hash_repair") is True,
        "SEARCH_CONTRACT_DRIFT",
        "$.search_contract",
        "permission-aware search contract drift",
    )
    transfer = value.get("transfer_contract", {})
    check(
        transfer.get("markdown_exact_snapshot_envelope") is True
        and transfer.get("html_export") is True
        and transfer.get("csv_import_export") is True
        and transfer.get("reminder_metadata_preserved") is True
        and transfer.get("colliding_block_ids_deterministically_remapped") is True
        and transfer.get("operation_id_replay_and_conflict") is True
        and transfer.get("full_normalized_database_restore_owned_by_F0_P5") is True,
        "TRANSFER_CONTRACT_DRIFT",
        "$.transfer_contract",
        "adopted transfer contract drift",
    )
    route = value.get("route_contract", {})
    app_notion = (PROJECT_ROOT / "app_notion.py").read_text(encoding="utf-8")
    app_code = (PROJECT_ROOT / "app_code.py").read_text(encoding="utf-8")
    check(
        route.get("registrar")
        == "code_assist_v1.routes_notion_search.register_notion_search_routes"
        and route.get("notion_host_only") is True
        and route.get("route_count") == 9
        and _actual_routes() == EXPECTED_ROUTES
        and app_notion.count("register_notion_search_routes(app)") == 1
        and "routes_notion_search" not in app_code,
        "ROUTE_CONTRACT_DRIFT",
        "$.route_contract",
        "exact Notion-only F0-P4 route surface required",
    )
    browser = value.get("browser_contract", {})
    browser_source = (PROJECT_ROOT / "notion_static" / "notion.js").read_text(
        encoding="utf-8"
    )
    check(
        browser.get("canonical_search") is True
        and browser.get("canonical_backlinks") is True
        and browser.get("canonical_reminders") is True
        and browser.get("canonical_import_export") is True
        and "collaborativeSearch" in browser_source
        and "/search?${canonicalParams.toString()}" in browser_source
        and "/reminders?limit=200" in browser_source
        and "/imports/${isCsv ? \"csv\" : \"markdown\"}" in browser_source
        and "const exportBase = workspaceBase" in browser_source,
        "BROWSER_CONTRACT_DRIFT",
        "$.browser_contract",
        "canonical search and transfer UI wiring required",
    )
    validation = value.get("validation", {})
    benchmark = (
        validation.get("bounded_concurrency_measurement", {})
        if isinstance(validation, dict)
        else {}
    )
    search_latency = benchmark.get("search_latency_ms", {}) if isinstance(benchmark, dict) else {}
    overall_latency = benchmark.get("overall_latency_ms", {}) if isinstance(benchmark, dict) else {}
    measured_latency_values = [
        search_latency.get(name) for name in ("p50", "p95", "p99", "max")
    ] + [overall_latency.get(name) for name in ("p50", "p95", "p99", "max")]
    check(
        validation.get("search_authority_tests", {}).get("passed") == 5
        and validation.get("search_authority_tests", {}).get("failed") == 0
        and validation.get("real_multiprocess_reauthorization") is True
        and validation.get("python_and_javascript_syntax") == "pass"
        and validation.get("persistent_development_schema_after") == 2026082410
        and validation.get("persistent_development_integrity") == "ok"
        and validation.get("persistent_development_foreign_key_errors") == 0
        and validation.get("persistent_development_ports") == [10111, 10112, 10113]
        and validation.get("persistent_development_gateway_state") == "READY"
        and validation.get("production_10011_10012_changed") is False,
        "VALIDATION_DRIFT",
        "$.validation",
        "exact measured F0-P4 validation evidence required",
    )
    check(
        benchmark.get("contract")
        == "code_assist.notion.collaborative_search_benchmark.v1"
        and benchmark.get("request_count") == 100
        and benchmark.get("active_workers") == 30
        and benchmark.get("search_read_count") == 90
        and benchmark.get("index_write_count") == 10
        and benchmark.get("success_count") == 100
        and benchmark.get("error_count") == 0
        and benchmark.get("sqlite_busy_count") == 0
        and benchmark.get("converged") is True
        and benchmark.get("latency_policy") == "measurement_only_until_Q0_P1"
        and benchmark.get("latency_budget_gate") is False
        and all(
            isinstance(item, (int, float))
            and not isinstance(item, bool)
            and 0 <= float(item) < 60000
            for item in measured_latency_values
        )
        and search_latency.get("p50") <= search_latency.get("p95")
        <= search_latency.get("p99")
        <= search_latency.get("max")
        and overall_latency.get("p50") <= overall_latency.get("p95")
        <= overall_latency.get("p99")
        <= overall_latency.get("max"),
        "SEARCH_BENCHMARK_DRIFT",
        "$.validation.bounded_concurrency_measurement",
        "approved 100/30/10 floor and measurement-only latency evidence required",
    )
    publication = value.get("second_brain_fence", {})
    check(
        publication.get("protected_input_sha256")
        == "sha256:0beefd78448cdd19767be0e0276fdbf6951ff417c39c135105e7754d755df494"
        and publication.get("goal_count") == 20
        and publication.get("scenario_count") == 284
        and publication.get("protected_second_brain_changed") is False,
        "SECOND_BRAIN_FENCE_DRIFT",
        "$.second_brain_fence",
        "protected Second Brain boundary drift",
    )
    _verify_manifest(
        value.get("source_manifest"), EXPECTED_SOURCE_PATHS, "$.source_manifest", errors,
        allow_successor=allow_successor,
    )
    _verify_manifest(
        value.get("test_manifest"), EXPECTED_TEST_PATHS, "$.test_manifest", errors,
        allow_successor=allow_successor,
    )
    claims = value.get("claims", {})
    check(
        claims.get("F0_P4_gate_passed") is True
        and claims.get("SEARCH_001_closed") is True
        and claims.get("DB_001_closed") is False
        and claims.get("production_changed") is False
        and claims.get("product_complete") is False
        and claims.get("active_shared_goal_complete") is False,
        "CLAIMS_DRIFT",
        "$.claims",
        "partial completion truth drift",
    )
    return {
        "contract": "code_assist.notion.internal_collaborative_search_authority.audit.v1",
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
