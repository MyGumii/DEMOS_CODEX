from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from flask import Flask
import pytest

from code_assist_v1.tools.verify_notion_route_contract import (
    REQUIRED_HARD_ROUTES,
    RouteSurface,
    collect_actual_routes,
    load_manifest,
    validate_manifest_data,
    verify_contract,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "docs" / "notion_route_contract.json"


def _manifest() -> dict[str, Any]:
    return load_manifest(MANIFEST_PATH)


def _errors(report: dict[str, Any]) -> set[str]:
    return {str(item["code"]) for item in report["errors"]}


@pytest.mark.unit
def test_np_1b_checked_in_contract_matches_exact_flask_surface() -> None:
    report, exit_code = verify_contract(MANIFEST_PATH)

    assert exit_code == 0, report
    assert report["status"] == "pass"
    assert report["summary"] == {
        "registrar_count": 2,
        "expected_route_count": 59,
        "actual_route_count": 59,
        "required_hard_route_count": len(REQUIRED_HARD_ROUTES),
        "error_count": 0,
    }


@pytest.mark.unit
def test_np_1b_cli_stdout_is_one_machine_readable_json_document() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.verify_notion_route_contract",
            "--compact",
        ],
        cwd=PROJECT_ROOT.parent,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    report = json.loads(completed.stdout)
    assert report["status"] == "pass"
    assert report["summary"]["actual_route_count"] == 59


@pytest.mark.unit
def test_np_1b_required_second_brain_and_atomic_routes_are_hard_gates() -> None:
    manifest = _manifest()
    identifiers = {item["id"] for item in manifest["required_hard_routes"]}

    assert identifiers == set(REQUIRED_HARD_ROUTES)
    routes, errors = collect_actual_routes()
    assert errors == []
    for required in REQUIRED_HARD_ROUTES.values():
        assert any(
            route.registrar == required.registrar
            and route.path == required.path
            and route.endpoint == required.endpoint
            and required.methods[0] in route.methods
            for route in routes
        )


@pytest.mark.unit
def test_np_1b_duplicate_route_key_and_unknown_method_fail_deterministically() -> None:
    manifest = _manifest()
    routes = manifest["registrars"][0]["routes"]
    routes.append(deepcopy(routes[0]))
    routes[1]["methods"] = ["TRACE"]

    report, exit_code = validate_manifest_data(manifest)

    assert exit_code == 2
    assert {"DUPLICATE_ROUTE_KEY", "UNKNOWN_METHOD"}.issubset(_errors(report))
    assert report["errors"] == sorted(
        report["errors"],
        key=lambda item: (
            item.get("code", ""), item.get("subject", ""), item.get("message", "")
        ),
    )


@pytest.mark.unit
def test_np_1b_duplicate_json_object_key_is_rejected(tmp_path: Path) -> None:
    manifest_path = tmp_path / "duplicate-key.json"
    manifest_path.write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )

    report, exit_code = verify_contract(manifest_path)

    assert exit_code == 2
    assert _errors(report) == {"DUPLICATE_JSON_KEY"}


@pytest.mark.unit
def test_np_1b_phase_path_escape_is_rejected() -> None:
    manifest = _manifest()
    manifest["registrars"][0]["routes"][0]["path"] = "/api/code/notion/escape"

    report, exit_code = validate_manifest_data(manifest)

    assert exit_code == 2
    assert "PHASE_PATH_ESCAPE" in _errors(report)


@pytest.mark.unit
def test_np_1b_missing_extra_and_endpoint_drift_are_rejected() -> None:
    manifest = _manifest()
    actual, actual_errors = collect_actual_routes()
    assert actual_errors == []
    missing = actual[0]
    changed = actual[1]
    drifted = [route for route in actual if route != missing and route != changed]
    drifted.append(
        RouteSurface(
            changed.registrar,
            changed.methods,
            changed.path,
            changed.endpoint + "_renamed",
        )
    )

    def extra_route(app: Flask) -> None:
        app.add_url_rule(
            "/api/notion/np1b-extra",
            endpoint="api_notion_np1b_extra",
            view_func=lambda: "",
            methods=["GET"],
        )

    extra_actual = [
        *drifted,
        RouteSurface(
            "notion", ("GET",), "/api/notion/np1b-extra", "api_notion_np1b_extra"
        ),
    ]
    report, exit_code = validate_manifest_data(
        manifest,
        actual_routes=extra_actual,
        actual_errors=[],
    )

    assert exit_code == 2
    assert {
        "MISSING_ACTUAL_ROUTE",
        "UNEXPECTED_ACTUAL_ROUTE",
        "ENDPOINT_MISMATCH",
    }.issubset(_errors(report))


@pytest.mark.unit
def test_np_1b_registers_routes_without_sending_requests_or_creating_data(
    isolated_knowledge_layout: Any,
) -> None:
    before = {
        path.relative_to(isolated_knowledge_layout.root).as_posix()
        for path in isolated_knowledge_layout.root.rglob("*")
    }

    routes, errors = collect_actual_routes()

    after = {
        path.relative_to(isolated_knowledge_layout.root).as_posix()
        for path in isolated_knowledge_layout.root.rglob("*")
    }
    assert len(routes) == 59
    assert errors == []
    assert after == before
