from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.verify_notion_core_free_ui_completion import load_json
from code_assist_v1.tools.verify_notion_core_free_ui_performance_budget import DEFAULT_BUDGET, load_active_performance_budget, verify_performance_budget


def _write(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "budget.json"
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _codes(tmp_path: Path, mutate) -> set[str]:
    value = copy.deepcopy(load_json(DEFAULT_BUDGET))
    mutate(value)
    report, exit_code = verify_performance_budget(_write(tmp_path, value))
    assert exit_code == 1
    return {item["code"] for item in report["errors"]}


@pytest.mark.unit
def test_checked_in_performance_budget_is_approved_local_only() -> None:
    report, exit_code = verify_performance_budget()
    assert exit_code == 0, report
    assert report == {"contract": "code_assist.notion.core_free_ui.performance_budget.v1", "status": "pass", "state": "active_local_precalibration", "budgeted_metric_count": 8, "unbudgeted_metric_count": 1, "errors": []}
    budgets = load_active_performance_budget()
    assert budgets["save_ready_ms"] == 2460
    assert "create_page_ready_ms" not in budgets


@pytest.mark.unit
def test_active_budget_rejects_threshold_or_scope_tamper(tmp_path: Path) -> None:
    def threshold(value: dict[str, object]) -> None:
        value["enforcement"]["metric_budgets_ms"]["save_ready_ms"] += 10  # type: ignore[index,operator]

    assert "BUDGETS_INVALID" in _codes(tmp_path, threshold)

    def scope(value: dict[str, object]) -> None:
        value["enforcement"]["measurement_only_unbudgeted_metrics"] = []  # type: ignore[index]

    assert "ENFORCEMENT_INVALID" in _codes(tmp_path, scope)


@pytest.mark.unit
def test_active_budget_rejects_parity_or_external_action_claim(tmp_path: Path) -> None:
    def parity(value: dict[str, object]) -> None:
        value["claim_boundary"]["notion_reference_timing_claimed"] = True  # type: ignore[index]

    assert "CLAIM_BOUNDARY_INVALID" in _codes(tmp_path, parity)

    def external(value: dict[str, object]) -> None:
        value["safety"]["external_action_executed"] = True  # type: ignore[index]

    assert "SAFETY_INVALID" in _codes(tmp_path, external)
