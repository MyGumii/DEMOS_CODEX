from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.tools.verify_notion_core_free_ui_completion import (
    DEFAULT_MATRIX,
    DEFAULT_PLAN,
    _validate_reference_differential_packet,
    _validate_mode_evidence_packet,
    load_json,
    verify_completion_matrix,
)
from code_assist_v1.tools.notion_core_free_ui_remaining_evidence import (
    build_remaining_evidence,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _write_matrix(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "completion.json"
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return path


def _verify_mutation(tmp_path: Path, mutate) -> set[str]:
    value = copy.deepcopy(load_json(DEFAULT_MATRIX))
    mutate(value)
    report, exit_code = verify_completion_matrix(_write_matrix(tmp_path, value), DEFAULT_PLAN)
    assert exit_code == 1
    assert report["status"] == "fail"
    return {item["code"] for item in report["errors"]}


@pytest.mark.unit
def test_checked_in_completion_matrix_is_truthful_but_incomplete() -> None:
    report, exit_code = verify_completion_matrix()

    assert exit_code == 0, report
    assert report == {
        "contract": "code_assist.notion.core_free_ui.completion_matrix.v1",
        "status": "pass",
        "completion_state": "incomplete",
        "profile": "free-owner-chrome-desktop-ko-KR",
        "summary": {
            "required_scenarios": 25,
            "local_real_browser_green": 24,
            "local_measurement_green": 1,
            "reference_characterized": 21,
            "differential_passed": 20,
            "local_integration_passed": 3,
            "local_recovery_contract_passed": 1,
            "measurement_contract_passed": 1,
            "evidence_satisfied": 25,
            "unresolved": 0,
            "promoted": 0,
        },
        "errors": [],
    }

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.verify_notion_core_free_ui_completion",
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
    assert json.loads(completed.stdout)["completion_state"] == "incomplete"


@pytest.mark.unit
def test_loader_rejects_duplicate_keys_and_non_finite_numbers(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"contract":"a","contract":"b"}', encoding="utf-8")
    report, exit_code = verify_completion_matrix(duplicate, DEFAULT_PLAN)
    assert exit_code == 1
    assert report["errors"][0]["code"] == "MATRIX_READ_FAILED"

    non_finite = tmp_path / "non-finite.json"
    non_finite.write_text('{"contract":NaN}', encoding="utf-8")
    report, exit_code = verify_completion_matrix(non_finite, DEFAULT_PLAN)
    assert exit_code == 1
    assert report["errors"][0]["code"] == "MATRIX_READ_FAILED"


@pytest.mark.unit
def test_denominator_phase_and_summary_forgery_fail_closed(tmp_path: Path) -> None:
    codes = _verify_mutation(tmp_path, lambda value: value["scenarios"].pop())
    assert {"SCENARIO_DENOMINATOR_MISMATCH", "SCENARIO_COUNT_INVALID", "SUMMARY_MISMATCH"}.issubset(codes)

    def forge_phase(value: dict[str, object]) -> None:
        value["scenarios"][0]["phase"] = "H9"  # type: ignore[index]

    assert "SCENARIO_PHASE_MISMATCH" in _verify_mutation(tmp_path, forge_phase)

    def forge_summary(value: dict[str, object]) -> None:
        value["summary"]["differential_passed"] = 25  # type: ignore[index]

    assert "SUMMARY_MISMATCH" in _verify_mutation(tmp_path, forge_summary)


@pytest.mark.unit
def test_evidence_path_hash_and_plan_binding_fail_closed(tmp_path: Path) -> None:
    def escape_path(value: dict[str, object]) -> None:
        value["scenarios"][0]["local_evidence"]["path"] = "../private.md"  # type: ignore[index]

    assert "PATH_INVALID" in _verify_mutation(tmp_path, escape_path)

    def forge_evidence_hash(value: dict[str, object]) -> None:
        value["scenarios"][0]["local_evidence"]["sha256"] = "sha256:" + ("0" * 64)  # type: ignore[index]

    assert "BOUND_FILE_HASH_MISMATCH" in _verify_mutation(tmp_path, forge_evidence_hash)

    def forge_plan_hash(value: dict[str, object]) -> None:
        value["scenario_plan"]["canonical_sha256"] = "sha256:" + ("0" * 64)  # type: ignore[index]

    assert "PLAN_CANONICAL_SHA_MISMATCH" in _verify_mutation(tmp_path, forge_plan_hash)


@pytest.mark.unit
def test_reference_promotion_and_completion_claims_cannot_be_invented(tmp_path: Path) -> None:
    def forge_reference(value: dict[str, object]) -> None:
        item = next(  # type: ignore[index]
            candidate
            for candidate in value["scenarios"]
            if candidate["scenario_id"] == "UI-EDITOR-006"
        )
        item["reference_state"] = "reference_characterized"
        item["differential_state"] = "differential_passed"
        item["promotion_allowed"] = True
        value["summary"]["reference_characterized"] = 17  # type: ignore[index]
        value["summary"]["differential_passed"] = 17  # type: ignore[index]
        value["summary"]["promoted"] = 1  # type: ignore[index]

    codes = _verify_mutation(tmp_path, forge_reference)
    assert {
        "REFERENCE_EVIDENCE_REQUIRED",
        "DIFFERENTIAL_STATE_INVALID",
        "PROMOTION_FORBIDDEN",
    }.issubset(codes)

    def forge_completion(value: dict[str, object]) -> None:
        value["completion_claims"]["core_ui_complete"] = True  # type: ignore[index]
        value["completion_claims"]["overall_goal_complete"] = True  # type: ignore[index]

    assert "COMPLETION_CLAIMS_INVALID" in _verify_mutation(tmp_path, forge_completion)


@pytest.mark.unit
def test_reference_differential_packet_rejects_capture_or_report_tamper(
    tmp_path: Path,
) -> None:
    matrix = load_json(DEFAULT_MATRIX)
    scenario = next(
        item for item in matrix["scenarios"] if item["scenario_id"] == "UI-SHELL-002"
    )
    packet_path = PROJECT_ROOT / scenario["differential_evidence"]["path"]
    packet = load_json(packet_path)
    packet["local"]["captures"]["UI-SHELL-002-A01"]["focus"]["state"] = "focused"
    tampered = _write_matrix(tmp_path, packet)

    errors: list[dict[str, str]] = []
    _validate_reference_differential_packet(
        tampered,
        scenario_id="UI-SHELL-002",
        subject="packet",
        errors=errors,
    )

    assert {"LOCAL_CAPTURE_HASH_MISMATCH", "DIFFERENTIAL_REPORT_MISMATCH"}.issubset(
        {item["code"] for item in errors}
    )


@pytest.mark.unit
def test_approved_evidence_modes_are_deterministic_and_tamper_evident(
    tmp_path: Path,
) -> None:
    matrix = load_json(DEFAULT_MATRIX)
    scenario = next(
        item for item in matrix["scenarios"] if item["scenario_id"] == "UI-SB-002"
    )
    packet_path = PROJECT_ROOT / scenario["differential_evidence"]["path"]
    packet = load_json(packet_path)
    assert packet == build_remaining_evidence()

    tampered = copy.deepcopy(packet)
    entry = next(
        item for item in tampered["scenarios"] if item["scenario_id"] == "UI-SB-002"
    )
    entry["assertions"][0]["status"] = "fail"
    tampered_path = _write_matrix(tmp_path, tampered)
    errors: list[dict[str, str]] = []
    _validate_mode_evidence_packet(
        tampered_path,
        scenario_id="UI-SB-002",
        expected_mode="local_second_brain_contract",
        require_satisfied=True,
        subject="packet",
        errors=errors,
    )
    assert "EVIDENCE_MODE_PACKET_INVALID" in {item["code"] for item in errors}


@pytest.mark.unit
def test_second_brain_and_performance_modes_cannot_be_relabelled(
    tmp_path: Path,
) -> None:
    def forge_second_brain(value: dict[str, object]) -> None:
        item = next(  # type: ignore[index]
            candidate
            for candidate in value["scenarios"]
            if candidate["scenario_id"] == "UI-SB-001"
        )
        item["evidence_mode"] = "reference_differential"
        item["reference_state"] = "reference_characterized"
        item["differential_state"] = "differential_passed"

    assert "EVIDENCE_MODE_MISMATCH" in _verify_mutation(tmp_path, forge_second_brain)

    def forge_performance(value: dict[str, object]) -> None:
        item = next(  # type: ignore[index]
            candidate
            for candidate in value["scenarios"]
            if candidate["scenario_id"] == "UI-PERF-001"
        )
        item["differential_state"] = "differential_passed"

    assert "DIFFERENTIAL_STATE_INVALID" in _verify_mutation(tmp_path, forge_performance)

    def forge_budget_summary(value: dict[str, object]) -> None:
        value["summary"]["measurement_contract_passed"] = 0  # type: ignore[index]
        value["summary"]["evidence_satisfied"] = 24  # type: ignore[index]
        value["summary"]["unresolved"] = 1  # type: ignore[index]

    assert "SUMMARY_MISMATCH" in _verify_mutation(tmp_path, forge_budget_summary)
