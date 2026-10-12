from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools import verify_notion_workspace_route_contract as verifier
from code_assist_v1.tools.verify_notion_workspace_route_contract import (
    DEFAULT_MANIFEST,
    DEFAULT_SEMANTICS,
    EXPECTED_OPERATION_COUNT,
    EXPECTED_RULE_COUNT,
    HARD_EXPECTATIONS,
    RouteSurface,
    collect_registrar_routes,
    load_manifest,
    surface_collision_count,
    validate_manifest_data,
    verify_contract,
)


pytestmark = pytest.mark.regression


def _documents() -> tuple[dict, dict]:
    return load_manifest(DEFAULT_MANIFEST), load_manifest(DEFAULT_SEMANTICS)


def _codes(report: dict) -> set[str]:
    return {str(item.get("code")) for item in report.get("errors") or []}


def test_np2a_overlay_exact_surface_and_all_invariants_pass() -> None:
    report, exit_code = verify_contract()

    assert exit_code == 0
    assert report == {
        "report_schema_version": 1,
        "contract_id": "notion-workspace-route-contract-np2a",
        "semantic_manifest_id": "notion-workspace-route-semantics-np2a",
        "phase": "NP-2A",
        "status": "pass",
        "summary": {
            "expected_route_count": 30,
            "actual_route_count": 30,
            "operation_count": 42,
            "required_hard_operation_count": 42,
            "legacy_rule_count": 59,
            "legacy_operation_count": 61,
            "legacy_hard_operation_count": 15,
            "main_overlay_collision_count": 0,
            "app_code_overlay_registration_count": 0,
            "error_count": 0,
        },
        "errors": [],
    }


def test_np2a_registrar_alone_is_exactly_thirty_rules_and_forty_two_operations() -> None:
    manifest, semantics = _documents()
    actual = collect_registrar_routes()

    assert len(actual) == EXPECTED_RULE_COUNT
    assert sum(len(route.methods) for route in actual) == EXPECTED_OPERATION_COUNT
    assert len(semantics["operations"]) == EXPECTED_OPERATION_COUNT
    assert len(semantics["required_hard_operations"]) == len(HARD_EXPECTATIONS) == 42

    manifest_routes = manifest["registrar"]["routes"]
    assert [
        {
            "methods": list(route.methods),
            "path": route.path,
            "endpoint": route.endpoint,
        }
        for route in actual
    ] == manifest_routes


def test_np2a_workspace_pages_query_read_semantics_are_exact() -> None:
    _manifest, semantics = _documents()
    operation = next(
        item
        for item in semantics["operations"]
        if item["id"] == "notion_workspace.api_notion_workspace_pages.get"
    )

    assert operation == {
        "id": "notion_workspace.api_notion_workspace_pages.get",
        "registrar": "notion_workspace",
        "method": "GET",
        "path": "/api/notion/workspaces/<workspace_id>/pages",
        "endpoint": "api_notion_workspace_pages",
        "auth_class": "authenticated",
        "request": {
            "transport": "query",
            "required_fields": [],
            "optional_fields": ["q"],
            "path_fields": ["workspace_id"],
        },
        "success": {
            "statuses": [200],
            "content_types": ["application/json"],
            "required_keys": [
                "count",
                "degraded",
                "degraded_sources",
                "items",
                "membership_epoch",
                "workspace_id",
            ],
        },
        "error_profiles": [
            "auth_required",
            "password_change",
            "workspace_standard",
            "workspace_unavailable",
        ],
    }


def test_np2a_workspace_page_lifecycle_semantics_are_exact() -> None:
    _manifest, semantics = _documents()
    lifecycle_ids = {
        "notion_workspace.api_notion_workspace_page_duplicate.post",
        "notion_workspace.api_notion_workspace_page_trash.post",
        "notion_workspace.api_notion_workspace_pages.post",
        "notion_workspace.api_notion_workspace_trash.get",
        "notion_workspace.api_notion_workspace_trash_restore.post",
    }
    operations = {
        item["id"]: item for item in semantics["operations"] if item["id"] in lifecycle_ids
    }

    assert set(operations) == lifecycle_ids
    assert operations["notion_workspace.api_notion_workspace_pages.post"]["success"] == {
        "statuses": [200, 201],
        "content_types": ["application/json"],
        "required_keys": ["binding", "page", "replayed", "status", "workspace"],
    }
    assert operations[
        "notion_workspace.api_notion_workspace_page_duplicate.post"
    ]["success"] == {
        "statuses": [200, 201],
        "content_types": ["application/json"],
        "required_keys": [
            "bindings",
            "page",
            "replayed",
            "status",
            "subtree_count",
            "workspace",
        ],
    }
    assert operations["notion_workspace.api_notion_workspace_page_trash.post"][
        "success"
    ]["required_keys"] == [
        "archived_count",
        "page_id",
        "replayed",
        "status",
        "workspace",
    ]
    assert operations["notion_workspace.api_notion_workspace_trash.get"]["request"] == {
        "transport": "query",
        "required_fields": [],
        "optional_fields": ["q"],
        "path_fields": ["workspace_id"],
    }
    assert operations[
        "notion_workspace.api_notion_workspace_trash_restore.post"
    ]["success"]["required_keys"] == [
        "page",
        "replayed",
        "restored_count",
        "status",
        "workspace",
    ]


def test_np2a_contract_rejects_unknown_keys_methods_and_path_escape() -> None:
    manifest, semantics = _documents()
    manifest["np2a_unknown"] = True
    manifest["registrar"]["routes"][0]["methods"] = ["TRACE"]
    manifest["registrar"]["routes"][1]["path"] = "/api/notion/workspaces/../escape"

    report, exit_code = validate_manifest_data(
        manifest,
        semantics,
        actual_routes=collect_registrar_routes(),
        check_runtime_invariants=False,
    )

    assert exit_code == 2
    assert {"UNKNOWN_KEY", "METHODS_INVALID", "PATH_INVALID"} <= _codes(report)


def test_np2a_actual_missing_extra_and_endpoint_drift_are_rejected() -> None:
    manifest, semantics = _documents()
    actual = collect_registrar_routes()
    missing = actual[0]
    changed = actual[1]
    supplied = [
        route
        for route in actual
        if route not in {missing, changed}
    ]
    supplied.extend(
        [
            RouteSurface(changed.methods, changed.path, changed.endpoint + "_drift"),
            RouteSurface(
                ("GET",),
                "/api/notion/workspaces/<workspace_id>/np2a-extra",
                "api_notion_workspace_extra",
            ),
        ]
    )

    report, exit_code = validate_manifest_data(
        manifest,
        semantics,
        actual_routes=supplied,
        check_runtime_invariants=False,
    )

    assert exit_code == 2
    assert {
        "MISSING_ACTUAL_ROUTE",
        "UNEXPECTED_ACTUAL_ROUTE",
        "ACTUAL_ENDPOINT_MISMATCH",
        "HARD_ROUTE_ACTUAL_MISSING",
    } <= _codes(report)


def test_np2a_semantics_are_exact_and_cover_every_route_operation() -> None:
    manifest, semantics = _documents()
    target = semantics["operations"][0]
    target["success"]["statuses"] = [202]
    removed = semantics["operations"].pop(1)
    assert removed["id"]

    report, exit_code = validate_manifest_data(
        manifest,
        semantics,
        actual_routes=collect_registrar_routes(),
        check_runtime_invariants=False,
    )

    assert exit_code == 2
    assert {
        "SEMANTIC_OPERATION_MISMATCH",
        "SEMANTIC_OPERATION_SET_INVALID",
        "SEMANTIC_ROUTE_LINK_INVALID",
        "HARD_OPERATION_MISSING",
    } <= _codes(report)


def test_np2a_hard_route_and_operation_mapping_are_fail_closed() -> None:
    manifest, semantics = _documents()
    manifest["required_hard_routes"][0]["endpoint"] = "api_notion_workspace_wrong"
    semantics["required_hard_operations"][0]["operation_id"] = (
        "notion_workspace.api_notion_workspace_wrong.post"
    )

    report, exit_code = validate_manifest_data(
        manifest,
        semantics,
        actual_routes=collect_registrar_routes(),
        check_runtime_invariants=False,
    )

    assert exit_code == 2
    assert {"HARD_ROUTE_MISMATCH", "HARD_OPERATIONS_INVALID"} <= _codes(report)


def test_np2a_duplicate_json_keys_fail_deterministically(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate-workspace-contract.json"
    duplicate.write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )

    first_report, first_exit = verify_contract(duplicate, DEFAULT_SEMANTICS)
    second_report, second_exit = verify_contract(duplicate, DEFAULT_SEMANTICS)

    assert first_exit == second_exit == 2
    assert first_report == second_report
    assert _codes(first_report) == {"MANIFEST_LOAD_FAILED"}
    assert "duplicate JSON object key" in first_report["errors"][0]["message"]


def test_np2a_legacy_59_61_15_gate_rejects_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, semantics = _documents()
    monkeypatch.setattr(
        verifier,
        "verify_legacy_route_contract",
        lambda: (
            {
                "summary": {
                    "expected_route_count": 58,
                    "actual_route_count": 58,
                    "required_hard_route_count": 14,
                },
                "errors": [],
            },
            0,
        ),
    )
    monkeypatch.setattr(
        verifier,
        "verify_legacy_route_semantics",
        lambda: (
            {
                "summary": {
                    "rule_count": 58,
                    "operation_count": 60,
                    "hard_operation_count": 14,
                },
                "errors": [],
            },
            0,
        ),
    )

    report, exit_code = validate_manifest_data(manifest, semantics)

    assert exit_code == 2
    assert {
        "LEGACY_ROUTE_BASELINE_FAILED",
        "LEGACY_SEMANTIC_BASELINE_FAILED",
    } <= _codes(report)


def test_np2a_main_overlay_collision_guard_detects_method_path_and_endpoint() -> None:
    main = [
        RouteSurface(("GET",), "/api/notion/workspaces", "api_notion_main_collision")
    ]
    overlay = [
        RouteSurface(("GET",), "/api/notion/workspaces", "api_notion_main_collision")
    ]

    assert surface_collision_count(main, overlay) == 2


@pytest.mark.parametrize(
    ("source", "expected_count"),
    [
        (
            "from code_assist_v1.routes_notion_workspace import "
            "register_notion_workspace_routes\n"
            "register_notion_workspace_routes(app)\n",
            2,
        ),
        ("from code_assist_v1 import routes_notion_workspace\n", 1),
        (
            "import importlib\n"
            "importlib.import_module('code_assist_v1.routes_notion_workspace')\n",
            1,
        ),
    ],
)
def test_np2a_app_code_overlay_import_call_or_dynamic_load_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    expected_count: int,
) -> None:
    unsafe_app_code = tmp_path / "app_code.py"
    unsafe_app_code.write_text(source, encoding="utf-8")
    monkeypatch.setattr(verifier, "APP_CODE_PATH", unsafe_app_code)
    errors: list[dict[str, str]] = []

    count = verifier._app_code_overlay_registration_count(errors)

    assert count == expected_count
    assert {item["code"] for item in errors} == {"APP_CODE_OVERLAY_REGISTERED"}


def test_np2a_cli_emits_one_deterministic_compact_report(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = verifier.main(["--compact"])
    first = capsys.readouterr().out
    second_exit = verifier.main(["--compact"])
    second = capsys.readouterr().out

    assert exit_code == second_exit == 0
    assert first == second
    payload = json.loads(first)
    assert payload["status"] == "pass"
    assert payload["summary"]["actual_route_count"] == 30
    assert payload["summary"]["operation_count"] == 42
    assert payload["summary"]["legacy_rule_count"] == 59
    assert payload["summary"]["legacy_operation_count"] == 61
    assert payload["summary"]["legacy_hard_operation_count"] == 15
    assert payload["summary"]["main_overlay_collision_count"] == 0
    assert payload["summary"]["app_code_overlay_registration_count"] == 0
