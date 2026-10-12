from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.verify_notion_core_free_ui_completion import load_json
from code_assist_v1.tools.verify_notion_core_free_ui_manual_ime_receipt import (
    DEFAULT_RECEIPT,
    verify_manual_ime_receipt,
)


def _write(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _codes(tmp_path: Path, mutate) -> set[str]:
    receipt = copy.deepcopy(load_json(DEFAULT_RECEIPT))
    mutate(receipt)
    report, exit_code = verify_manual_ime_receipt(_write(tmp_path, receipt))
    assert exit_code == 1
    assert report["manual_input_resolved"] is False
    return {item["code"] for item in report["errors"]}


@pytest.mark.unit
def test_checked_in_manual_ime_receipt_is_exact_and_unpromoted() -> None:
    report, exit_code = verify_manual_ime_receipt()
    assert exit_code == 0, report
    assert report == {
        "contract": "code_assist.notion.core_free_ui.manual_ime_input_verified_receipt.v1",
        "status": "pass",
        "manual_input_resolved": True,
        "scenario_id": "UI-EDITOR-004",
        "reference_summary_review_required": True,
        "errors": [],
    }


@pytest.mark.unit
def test_manual_ime_receipt_rejects_missing_or_duplicate_final_text(tmp_path: Path) -> None:
    def missing(value: dict[str, object]) -> None:
        value["reference_observation"]["normalized_exact_count_after_reload"] = 0  # type: ignore[index]

    assert "OBSERVATION_INVALID" in _codes(tmp_path, missing)

    def duplicate(value: dict[str, object]) -> None:
        value["reference_observation"]["normalized_exact_count_immediate"] = 2  # type: ignore[index]

    assert "OBSERVATION_INVALID" in _codes(tmp_path, duplicate)


@pytest.mark.unit
def test_manual_ime_receipt_rejects_forged_user_statement(tmp_path: Path) -> None:
    def mutate(value: dict[str, object]) -> None:
        value["source"]["received_text"] = "자동 입력 완료"  # type: ignore[index]

    assert "SOURCE_INVALID" in _codes(tmp_path, mutate)


@pytest.mark.unit
def test_manual_ime_receipt_rejects_internal_event_or_promotion_claims(tmp_path: Path) -> None:
    def event_claim(value: dict[str, object]) -> None:
        value["reference_observation"]["browser_control_observed_intermediate_composition_events"] = True  # type: ignore[index]

    assert "OBSERVATION_INVALID" in _codes(tmp_path, event_claim)

    def promote(value: dict[str, object]) -> None:
        value["disposition"]["differential_promotion_allowed"] = True  # type: ignore[index]

    assert "DISPOSITION_INVALID" in _codes(tmp_path, promote)


@pytest.mark.unit
def test_manual_ime_receipt_rejects_bound_file_or_safety_tamper(tmp_path: Path) -> None:
    def hash_tamper(value: dict[str, object]) -> None:
        value["queue"]["sha256"] = "sha256:" + ("0" * 64)  # type: ignore[index]

    assert "BOUND_FILE_HASH_MISMATCH" in _codes(tmp_path, hash_tamper)

    def safety_tamper(value: dict[str, object]) -> None:
        value["safety"]["personal_workspace_accessed"] = True  # type: ignore[index]

    assert "SAFETY_INVALID" in _codes(tmp_path, safety_tamper)
