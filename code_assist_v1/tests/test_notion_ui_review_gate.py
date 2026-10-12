from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

import code_assist_v1.tools.verify_notion_core_free_ui_review_gate as review_gate_module
from code_assist_v1.tools.verify_notion_core_free_ui_completion import load_json
from code_assist_v1.tools.verify_notion_core_free_ui_review_gate import (
    DEFAULT_APPROVAL_RECEIPT,
    DEFAULT_GATE,
    _file_sha256,
    verify_review_gate,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUEUE = (
    PROJECT_ROOT
    / "code_assist_v1"
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "review_implementation_queue.v1.json"
)


def _write_gate(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "review-gate.json"
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return path


def _verify_mutation(tmp_path: Path, mutate) -> set[str]:
    value = copy.deepcopy(load_json(DEFAULT_GATE))
    mutate(value)
    report, exit_code = verify_review_gate(_write_gate(tmp_path, value))
    assert exit_code == 1
    assert report["status"] == "fail"
    return {item["code"] for item in report["errors"]}


@pytest.mark.unit
def test_checked_in_review_gate_records_exact_approval_without_promoting_work() -> None:
    report, exit_code = verify_review_gate()

    assert exit_code == 0, report
    assert report == {
        "contract": "code_assist.notion.core_free_ui.evidence_review_gate.v1",
        "status": "pass",
        "review_state": "owner_approved_revalidation_authorized",
        "profile": "free-owner-chrome-desktop-ko-KR",
        "counts": {
            "certified": 25,
            "reference_observed_review_pending": 0,
            "local_integration_review_pending": 0,
            "reference_partially_observed": 0,
        },
        "errors": [],
    }

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.verify_notion_core_free_ui_review_gate",
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
    assert json.loads(completed.stdout)["review_state"] == (
        "owner_approved_revalidation_authorized"
    )


@pytest.mark.unit
def test_review_gate_rejects_checkpoint_hash_tamper(tmp_path: Path) -> None:
    def mutate(value: dict[str, object]) -> None:
        value["inputs"]["completion_matrix"]["sha256"] = (  # type: ignore[index]
            "sha256:" + ("0" * 64)
        )

    assert "BOUND_FILE_HASH_MISMATCH" in _verify_mutation(tmp_path, mutate)


@pytest.mark.unit
def test_review_gate_rejects_overlap_and_denominator_loss(tmp_path: Path) -> None:
    def overlap(value: dict[str, object]) -> None:
        value["categories"]["certified"].append("UI-SHELL-001")  # type: ignore[index]

    codes = _verify_mutation(tmp_path, overlap)
    assert {"CATEGORY_OVERLAP", "CATEGORY_COVERAGE_INVALID"}.issubset(codes)


@pytest.mark.unit
def test_review_gate_rejects_certified_scenario_swap(tmp_path: Path) -> None:
    def swap(value: dict[str, object]) -> None:
        entries = value["categories"]["certified"]  # type: ignore[index]
        entries[-1] = "UI-A11Y-001"

    assert "CATEGORY_COVERAGE_INVALID" in _verify_mutation(tmp_path, swap)


@pytest.mark.unit
def test_review_gate_rejects_approval_status_or_scope_forgery(tmp_path: Path) -> None:
    def forge_status(value: dict[str, object]) -> None:
        value["review_decisions"]["reference_observed_summary"]["status"] = "pending_owner_approval"  # type: ignore[index]

    assert "APPROVAL_STATUS_INVALID" in _verify_mutation(tmp_path, forge_status)

    def forge_scope(value: dict[str, object]) -> None:
        value["review_decisions"]["local_second_brain_policy"]["scenario_ids"].pop()  # type: ignore[index]

    assert "APPROVAL_SCOPE_MISMATCH" in _verify_mutation(tmp_path, forge_scope)


@pytest.mark.unit
def test_review_gate_rejects_owner_approval_receipt_hash_tamper(tmp_path: Path) -> None:
    def mutate(value: dict[str, object]) -> None:
        value["inputs"]["owner_approval_receipt"]["sha256"] = (  # type: ignore[index]
            "sha256:" + ("0" * 64)
        )

    assert "BOUND_FILE_HASH_MISMATCH" in _verify_mutation(tmp_path, mutate)


@pytest.mark.unit
def test_review_gate_rejects_stale_completion_matrix_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_file_sha256 = review_gate_module._file_sha256
    completion_matrix = review_gate_module.DEFAULT_MATRIX.resolve()

    def nested_input_drift(path: Path) -> str:
        if path.resolve() == completion_matrix:
            return "sha256:" + ("0" * 64)
        return original_file_sha256(path)

    monkeypatch.setattr(review_gate_module, "_file_sha256", nested_input_drift)
    report, exit_code = review_gate_module.verify_review_gate()

    assert exit_code == 1
    assert report["status"] == "fail"
    assert "BOUND_FILE_HASH_MISMATCH" in {
        item["code"] for item in report["errors"]
    }


@pytest.mark.unit
def test_review_implementation_queue_matches_approval_and_keeps_promotion_closed() -> None:
    gate = load_json(DEFAULT_GATE)
    queue = load_json(DEFAULT_QUEUE)

    assert queue["contract"] == (
        "code_assist.notion.core_free_ui.review_implementation_queue.v1"
    )
    assert queue["profile"] == gate["profile"]
    assert queue["review_gate"] == {
        "path": "code_assist_v1/docs/notion_parity/core_free_web_ui/evidence_review_gate.v1.json",
        "sha256": _file_sha256(DEFAULT_GATE),
    }
    assert queue["owner_approval_receipt"] == {
        "path": "code_assist_v1/docs/notion_parity/core_free_web_ui/owner_approval_receipt.20260822.json",
        "sha256": _file_sha256(DEFAULT_APPROVAL_RECEIPT),
    }
    approved_ids = gate["review_decisions"]["reference_observed_summary"]["scenario_ids"]
    queued_ids = [
        scenario_id
        for batch in queue["batches"]
        for scenario_id in batch["scenario_ids"]
    ]
    assert len(queued_ids) == len(set(queued_ids)) == 11
    assert set(queued_ids) == set(approved_ids)
    assert queue["authorization"] == {
        "status": "approved",
        "behavior_change_allowed_after_revalidation": True,
        "receipt_sealing_allowed": True,
        "local_second_brain_evidence_sealing_allowed": True,
        "completion_matrix_mutation_allowed": False,
    }
    assert all(
        batch["status"] == "reference_completed_differential_passed"
        for batch in queue["batches"]
    )
    assert queue["claims"] == {
        "queued_scenario_count": 0,
        "implemented_current_source_green_count": 11,
        "behavior_change_authorized_after_revalidation": True,
        "local_second_brain_evidence_sealing_authorized": True,
        "completion_matrix_changed": True,
        "certified_count": 25,
        "core_ui_complete": False,
        "overall_goal_complete": False,
    }
