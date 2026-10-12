from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.tools.verify_notion_core_free_ui_plan import (
    DEFAULT_PLAN,
    DEFAULT_SCHEMA,
    EXPECTED_REQUIRED_SCENARIOS,
    UI1_SCENARIOS,
    load_json,
    verify_plan,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _write_plan(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "scenarios.json"
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return path


def _verify_mutation(tmp_path: Path, mutate) -> dict[str, object]:
    value = copy.deepcopy(load_json(DEFAULT_PLAN))
    mutate(value)
    report, exit_code = verify_plan(_write_plan(tmp_path, value), DEFAULT_SCHEMA)
    assert exit_code == 1
    assert report["status"] == "fail"
    return report


def _codes(report: dict[str, object]) -> set[str]:
    return {str(item["code"]) for item in report["errors"]}  # type: ignore[index]


@pytest.mark.unit
def test_checked_in_core_free_ui_overlay_and_cli_pass() -> None:
    report, exit_code = verify_plan()

    assert exit_code == 0, report
    assert report["status"] == "pass"
    assert report["required_scenario_count"] == 25
    assert report["ui1_scenario_count"] == 6
    assert tuple(
        item["scenario_id"] for item in load_json(DEFAULT_PLAN)["required_scenarios"]
    ) == EXPECTED_REQUIRED_SCENARIOS
    assert UI1_SCENARIOS.issubset(set(EXPECTED_REQUIRED_SCENARIOS))

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.verify_notion_core_free_ui_plan",
            "--compact",
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["status"] == "pass"


@pytest.mark.unit
def test_plan_loader_rejects_duplicate_keys_and_non_finite_numbers(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    report, exit_code = verify_plan(duplicate, DEFAULT_SCHEMA)
    assert exit_code == 1
    assert "PLAN_READ_FAILED" in _codes(report)

    non_finite = tmp_path / "non-finite.json"
    non_finite.write_text('{"schema_version":NaN}', encoding="utf-8")
    report, exit_code = verify_plan(non_finite, DEFAULT_SCHEMA)
    assert exit_code == 1
    assert "PLAN_READ_FAILED" in _codes(report)


@pytest.mark.unit
def test_unknown_fields_and_schema_shape_fail_closed(tmp_path: Path) -> None:
    report = _verify_mutation(tmp_path, lambda value: value.update({"surprise": True}))
    assert "UNKNOWN_FIELD" in _codes(report)

    def add_action_field(value: dict[str, object]) -> None:
        value["required_scenarios"][0]["actions"][0]["selector"] = "#private"  # type: ignore[index]

    report = _verify_mutation(tmp_path, add_action_field)
    assert "UNKNOWN_FIELD" in _codes(report)


@pytest.mark.unit
def test_required_denominator_and_generated_counts_are_recomputed(tmp_path: Path) -> None:
    def remove_one(value: dict[str, object]) -> None:
        value["required_scenarios"].pop()  # type: ignore[union-attr]
        value["generated_counts"]["required_core_scenarios"] = 24  # type: ignore[index]
        value["generated_counts"]["planned_count"] = 24  # type: ignore[index]

    report = _verify_mutation(tmp_path, remove_one)
    assert "REQUIRED_DENOMINATOR_MISMATCH" in _codes(report)

    def forge_count(value: dict[str, object]) -> None:
        value["generated_counts"]["differential_passed_count"] = 25  # type: ignore[index]

    report = _verify_mutation(tmp_path, forge_count)
    assert "GENERATED_COUNT_MISMATCH" in _codes(report)


@pytest.mark.unit
def test_required_profile_cannot_expand_to_paid_or_other_roles(tmp_path: Path) -> None:
    def use_business(value: dict[str, object]) -> None:
        value["primary_profile"]["plan"] = "business"  # type: ignore[index]
        value["required_scenarios"][0]["profile_id"] = "desktop_business_admin"  # type: ignore[index]

    report = _verify_mutation(tmp_path, use_business)
    assert {"PRIMARY_PROFILE_INVALID", "PROFILE_SCOPE_INVALID"}.issubset(_codes(report))


@pytest.mark.unit
def test_action_parent_recovery_and_alias_contracts_fail_closed(tmp_path: Path) -> None:
    def break_action(value: dict[str, object]) -> None:
        action = value["required_scenarios"][0]["actions"][0]  # type: ignore[index]
        action["scenario_id"] = "UI-SHELL-002"
        action["recovery_action_id"] = "UI-SHELL-001-A99"
        action["target_alias"] = "NOTION.COM/PRIVATE"

    report = _verify_mutation(tmp_path, break_action)
    codes = _codes(report)
    assert "ACTION_SCENARIO_MISMATCH" in codes
    assert "RECOVERY_FORWARD_REFERENCE" in codes
    assert "PRIVATE_SELECTOR_FORBIDDEN" in codes


@pytest.mark.unit
def test_ui1_search_reopens_after_escape_before_typing(tmp_path: Path) -> None:
    plan = load_json(DEFAULT_PLAN)
    search = next(
        scenario
        for scenario in plan["required_scenarios"]
        if scenario["scenario_id"] == "UI-SEARCH-001"
    )
    assert [action["action_id"] for action in search["actions"]] == [
        "UI-SEARCH-001-A01",
        "UI-SEARCH-001-A02",
        "UI-SEARCH-001-A03",
        "UI-SEARCH-001-A04",
    ]
    assert search["actions"][2] == {
        "action_id": "UI-SEARCH-001-A03",
        "scenario_id": "UI-SEARCH-001",
        "surface": "search",
        "verb": "click",
        "target_alias": "SEARCH-TRIGGER",
        "input_alias": None,
        "expected_state_id": "STATE-SEARCH-VISIBLE",
        "expected_focus_alias": "SEARCH-INPUT",
        "capture_after": False,
        "mutation_class": "read_only",
        "recovery_action_id": None,
    }

    def remove_reopen(value: dict[str, object]) -> None:
        candidate = next(
            scenario
            for scenario in value["required_scenarios"]  # type: ignore[index]
            if scenario["scenario_id"] == "UI-SEARCH-001"
        )
        candidate["actions"].pop(2)

    report = _verify_mutation(tmp_path, remove_reopen)
    assert "UI1_SEARCH_FLOW_INVALID" in _codes(report)


@pytest.mark.unit
def test_duplicate_action_and_assertion_ids_are_rejected(tmp_path: Path) -> None:
    def duplicate_ids(value: dict[str, object]) -> None:
        scenario = value["required_scenarios"][1]  # type: ignore[index]
        scenario["actions"][1]["action_id"] = scenario["actions"][0]["action_id"]
        scenario["tolerance_assertions"][0]["assertion_id"] = "A1"

    report = _verify_mutation(tmp_path, duplicate_ids)
    assert {"ACTION_ID_DUPLICATE", "ASSERTION_ID_DUPLICATE"}.issubset(_codes(report))


@pytest.mark.unit
def test_sensitive_strings_and_unsafe_source_paths_are_rejected(tmp_path: Path) -> None:
    def add_email(value: dict[str, object]) -> None:
        value["required_scenarios"][0]["outcome"] = "Expose person@example.test"

    report = _verify_mutation(tmp_path, add_email)
    assert "SENSITIVE_EMAIL" in _codes(report)

    def escape_source(value: dict[str, object]) -> None:
        value["source_refs"][0]["path"] = "../outside.md"  # type: ignore[index]

    report = _verify_mutation(tmp_path, escape_source)
    assert "SOURCE_PATH_INVALID" in _codes(report)


@pytest.mark.unit
def test_source_hashes_bind_the_current_authoritative_documents(tmp_path: Path) -> None:
    def forge_hash(value: dict[str, object]) -> None:
        value["source_refs"][0]["sha256"] = "sha256:" + ("0" * 64)  # type: ignore[index]

    report = _verify_mutation(tmp_path, forge_hash)
    assert "SOURCE_HASH_MISMATCH" in _codes(report)


@pytest.mark.unit
def test_historical_thresholds_cannot_be_silently_promoted(tmp_path: Path) -> None:
    def promote(value: dict[str, object]) -> None:
        value["policy_refs"][0]["status"] = "approved"  # type: ignore[index]

    report = _verify_mutation(tmp_path, promote)
    assert "HISTORICAL_POLICY_PROMOTED" in _codes(report)

    policy = load_json(
        PROJECT_ROOT
        / "code_assist_v1"
        / "docs"
        / "notion_parity"
        / "core_free_web_ui"
        / "policies"
        / "precalibration.v1.json"
    )
    assert policy["geometry"] == "measurement_only"
    assert policy["latency"] == "measurement_only"
    assert "geometry_tolerance_px" not in policy
    assert "latency_budget_ms" not in policy


@pytest.mark.unit
def test_artifact_contract_cannot_admit_raw_browser_material(tmp_path: Path) -> None:
    def weaken(value: dict[str, object]) -> None:
        value["artifact_contract"]["raw_artifacts_in_repository"] = True  # type: ignore[index]
        value["artifact_contract"]["forbidden_artifacts"].remove("network_log")  # type: ignore[index]

    report = _verify_mutation(tmp_path, weaken)
    assert {"RAW_ARTIFACT_POLICY_INVALID", "FORBIDDEN_ARTIFACT_SET_INCOMPLETE"}.issubset(_codes(report))


@pytest.mark.unit
def test_verifier_import_is_side_effect_free_and_does_not_load_app_boundaries(tmp_path: Path) -> None:
    script = """
import json
from pathlib import Path
import sys

root = Path.cwd()
before = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
import code_assist_v1.tools.verify_notion_core_free_ui_plan  # noqa: F401
after = sorted(str(path.relative_to(root)) for path in root.rglob('*'))
forbidden = sorted(
    name for name in sys.modules
    if name in {
        'code_assist_v1.app_code',
        'code_assist_v1.auth',
        'code_assist_v1.config',
        'code_assist_v1.data_paths',
        'code_assist_v1.knowledge_core',
    }
    or name.startswith('code_assist_v1.routes_')
    or name.startswith('code_assist_v1.notion_workspace')
)
print(json.dumps({'forbidden': forbidden, 'changed': before != after}, sort_keys=True))
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(PROJECT_ROOT)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"changed": False, "forbidden": []}
