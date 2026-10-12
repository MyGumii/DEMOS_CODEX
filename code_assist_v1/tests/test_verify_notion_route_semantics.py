from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from code_assist_v1.tools.verify_notion_route_contract import (
    load_manifest as load_route_contract,
)
from code_assist_v1.tools.verify_notion_route_semantics import (
    HARD_EXPECTATIONS,
    load_manifest,
    validate_manifest_data,
    verify_manifest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "docs" / "notion_route_semantics.json"
ROUTE_CONTRACT_PATH = PROJECT_ROOT / "docs" / "notion_route_contract.json"
UNSAFE_METHODS = {"DELETE", "PATCH", "POST", "PUT"}


def _manifest() -> dict[str, Any]:
    return load_manifest(MANIFEST_PATH)


def _route_contract() -> dict[str, Any]:
    return load_route_contract(ROUTE_CONTRACT_PATH)


def _errors(report: dict[str, Any]) -> set[str]:
    return {str(item["code"]) for item in report["errors"]}


def _validate(manifest: dict[str, Any]) -> tuple[dict[str, Any], int]:
    return validate_manifest_data(
        manifest,
        route_contract=_route_contract(),
        verify_route_surface=False,
    )


def _non_hard_operation(
    manifest: dict[str, Any],
    *,
    predicate: Any = lambda operation: True,
) -> dict[str, Any]:
    hard_ids = {
        str(item["operation_id"])
        for item in manifest["required_hard_operations"]
    }
    return next(
        operation
        for operation in manifest["operations"]
        if operation["id"] not in hard_ids and predicate(operation)
    )


@pytest.mark.unit
def test_np_1c_checked_in_semantics_cover_exact_route_and_operation_surface() -> None:
    report, exit_code = verify_manifest(MANIFEST_PATH)

    assert exit_code == 0, report
    assert report["status"] == "pass"
    assert report["summary"] == {
        "rule_count": 59,
        "operation_count": 61,
        "hard_operation_count": len(HARD_EXPECTATIONS),
        "error_count": 0,
    }


@pytest.mark.unit
def test_np_1c_duplicate_json_object_key_is_rejected_deterministically(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "duplicate-key.json"
    manifest_path.write_text(
        '{"schema_version":1,"schema_version":1}',
        encoding="utf-8",
    )

    first_report, first_exit = verify_manifest(manifest_path)
    second_report, second_exit = verify_manifest(manifest_path)

    assert first_exit == second_exit == 2
    assert first_report == second_report
    assert _errors(first_report) == {"DUPLICATE_JSON_KEY"}
    assert first_report["errors"][0]["message"] == (
        "duplicate JSON object key: schema_version"
    )


@pytest.mark.unit
def test_np_1c_unknown_key_method_and_path_escape_fail_closed() -> None:
    manifest = _manifest()
    manifest["operations"][0]["np1c_unknown"] = True
    manifest["operations"][1]["method"] = "TRACE"
    manifest["operations"][2]["path"] = "/api/notion/../np1c-escape"

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert {"UNKNOWN_KEY", "METHOD_UNKNOWN", "PHASE_PATH_ESCAPE"}.issubset(
        _errors(report)
    )
    assert report["errors"] == sorted(
        report["errors"],
        key=lambda item: (
            item.get("code", ""),
            item.get("subject", ""),
            item.get("message", ""),
        ),
    )


@pytest.mark.unit
def test_np_1c_missing_and_extra_operation_are_both_rejected() -> None:
    manifest = _manifest()
    original = _non_hard_operation(manifest)
    replacement = deepcopy(original)
    replacement.update(
        {
            "id": f"notion.api_notion_np1c_extra.{str(original['method']).lower()}",
            "registrar": "notion",
            "path": "/api/notion/np1c-extra",
            "endpoint": "api_notion_np1c_extra",
        }
    )
    replacement["request"]["path_fields"] = []
    index = manifest["operations"].index(original)
    manifest["operations"][index] = replacement

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert {"MISSING_OPERATION", "UNEXPECTED_OPERATION"}.issubset(_errors(report))


@pytest.mark.unit
def test_np_1c_endpoint_must_link_exactly_to_frozen_route_contract() -> None:
    manifest = _manifest()
    operation = _non_hard_operation(manifest)
    operation["endpoint"] = f"{operation['endpoint']}_drift"
    operation["id"] = (
        f"{operation['registrar']}.{operation['endpoint']}."
        f"{str(operation['method']).lower()}"
    )

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert "ENDPOINT_MISMATCH" in _errors(report)


@pytest.mark.unit
def test_np_1c_unsafe_authenticated_json_requires_body_and_auth_error_profiles() -> None:
    manifest = _manifest()
    operation = _non_hard_operation(
        manifest,
        predicate=lambda item: (
            item["auth_class"] == "authenticated"
            and item["method"] in UNSAFE_METHODS
            and item["request"]["transport"] == "json_object"
        ),
    )
    operation["error_profiles"] = sorted(
        set(operation["error_profiles"])
        - {"auth_required", "password_change", "json_body"}
    )

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert {
        "AUTH_ERROR_PROFILE_MISSING",
        "INVALID_JSON_PROFILE_MISSING",
    }.issubset(_errors(report))
    assert sum(
        item["code"] == "AUTH_ERROR_PROFILE_MISSING"
        for item in report["errors"]
    ) == 2


@pytest.mark.unit
def test_np_1c_hard_route_semantic_field_drift_is_rejected() -> None:
    manifest = _manifest()
    atomic_operation_id = next(
        item["operation_id"]
        for item in manifest["required_hard_operations"]
        if item["id"] == "atomic_save"
    )
    atomic_operation = next(
        item for item in manifest["operations"] if item["id"] == atomic_operation_id
    )
    atomic_operation["request"]["required_fields"] = [
        field
        for field in atomic_operation["request"]["required_fields"]
        if field != "operation_id"
    ]

    report, exit_code = _validate(manifest)

    assert exit_code == 2
    assert "HARD_REQUIRED_FIELDS_MISMATCH" in _errors(report)


@pytest.mark.unit
def test_np_1c_cli_emits_one_json_document_and_uses_exit_status(
    tmp_path: Path,
) -> None:
    valid = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.verify_notion_route_semantics",
            "--compact",
        ],
        cwd=PROJECT_ROOT.parent,
        text=True,
        capture_output=True,
        check=False,
    )

    assert valid.returncode == 0, valid.stderr
    assert valid.stderr == ""
    valid_report = json.loads(valid.stdout)
    assert valid_report["status"] == "pass"
    assert valid_report["summary"]["rule_count"] == 59
    assert valid_report["summary"]["operation_count"] == 61

    invalid_manifest = _manifest()
    invalid_manifest["np1c_unknown"] = True
    invalid_path = tmp_path / "invalid-semantics.json"
    invalid_path.write_text(
        json.dumps(invalid_manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    invalid = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.verify_notion_route_semantics",
            "--manifest",
            str(invalid_path),
            "--compact",
        ],
        cwd=PROJECT_ROOT.parent,
        text=True,
        capture_output=True,
        check=False,
    )

    assert invalid.returncode == 2
    assert invalid.stderr == ""
    invalid_report = json.loads(invalid.stdout)
    assert invalid_report["status"] == "fail"
    assert "UNKNOWN_KEY" in _errors(invalid_report)
