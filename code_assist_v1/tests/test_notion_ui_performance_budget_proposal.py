from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.verify_notion_core_free_ui_completion import load_json
from code_assist_v1.tools.verify_notion_core_free_ui_performance_budget_proposal import DEFAULT_PROPOSAL, verify_performance_budget_proposal


def _write(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _codes(tmp_path: Path, mutate) -> set[str]:
    value = copy.deepcopy(load_json(DEFAULT_PROPOSAL))
    mutate(value)
    report, exit_code = verify_performance_budget_proposal(_write(tmp_path, value))
    assert exit_code == 1
    return {item["code"] for item in report["errors"]}


@pytest.mark.unit
def test_checked_in_performance_budget_proposal_is_truthful_and_unapproved() -> None:
    report, exit_code = verify_performance_budget_proposal()
    assert exit_code == 0, report
    assert report == {"contract": "code_assist.notion.core_free_ui.performance_budget_proposal.v1", "status": "pass", "metric_count": 8, "budget_approved": False, "errors": []}


@pytest.mark.unit
def test_budget_proposal_rejects_source_or_formula_tamper(tmp_path: Path) -> None:
    def source(value: dict[str, object]) -> None:
        value["metrics"][0]["observed_p95_ms"] = 1  # type: ignore[index]

    assert "METRIC_SOURCE_INVALID" in _codes(tmp_path, source)

    def formula(value: dict[str, object]) -> None:
        value["metrics"][0]["proposed_budget_ms"] += 10  # type: ignore[index,operator]

    assert "BUDGET_FORMULA_INVALID" in _codes(tmp_path, formula)


@pytest.mark.unit
def test_budget_proposal_rejects_approval_or_parity_forgery(tmp_path: Path) -> None:
    def approve(value: dict[str, object]) -> None:
        value["owner_decision"]["approved"] = True  # type: ignore[index]

    assert "DECISION_INVALID" in _codes(tmp_path, approve)

    def parity(value: dict[str, object]) -> None:
        value["claims"]["notion_performance_parity_claimed"] = True  # type: ignore[index]

    assert "CLAIMS_INVALID" in _codes(tmp_path, parity)


@pytest.mark.unit
def test_budget_proposal_rejects_bound_file_or_safety_tamper(tmp_path: Path) -> None:
    def binding(value: dict[str, object]) -> None:
        value["inputs"]["local_measurement"]["sha256"] = "sha256:" + ("0" * 64)  # type: ignore[index]

    assert "BOUND_FILE_HASH_MISMATCH" in _codes(tmp_path, binding)

    def safety(value: dict[str, object]) -> None:
        value["safety"]["external_action_executed"] = True  # type: ignore[index]

    assert "SAFETY_INVALID" in _codes(tmp_path, safety)
