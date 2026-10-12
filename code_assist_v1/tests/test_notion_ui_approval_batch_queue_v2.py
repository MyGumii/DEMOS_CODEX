from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.verify_notion_core_free_ui_approval_batch_v2 import DEFAULT_QUEUE, verify_approval_batch_v2
from code_assist_v1.tools.verify_notion_core_free_ui_completion import load_json


def _write(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "queue.json"
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _codes(tmp_path: Path, mutate) -> set[str]:
    value = copy.deepcopy(load_json(DEFAULT_QUEUE))
    mutate(value)
    report, exit_code = verify_approval_batch_v2(_write(tmp_path, value))
    assert exit_code == 1
    return {item["code"] for item in report["errors"]}


@pytest.mark.unit
def test_checked_in_second_approval_batch_is_exact_and_non_mutating() -> None:
    report, exit_code = verify_approval_batch_v2()
    assert exit_code == 0, report
    assert report["status"] == "pass"
    assert report["pending_entry_count"] == 2
    assert report["external_action_pending_count"] == 0
    assert report["exact_user_text"] == "UI-EDITOR-004 네이티브 IME 요약 승인; UI-PERF-001 로컬 성능 예산 제안 승인"


@pytest.mark.unit
def test_second_approval_batch_rejects_scope_or_phrase_drift(tmp_path: Path) -> None:
    def scope(value: dict[str, object]) -> None:
        value["entries"][0]["scenario_ids"] = ["UI-EDITOR-005"]  # type: ignore[index]

    assert "ENTRY_INVALID" in _codes(tmp_path, scope)

    def phrase(value: dict[str, object]) -> None:
        value["batch_execution"]["exact_user_text"] = "승인"  # type: ignore[index]

    assert "BATCH_EXECUTION_INVALID" in _codes(tmp_path, phrase)


@pytest.mark.unit
def test_second_approval_batch_rejects_external_action_or_inference(tmp_path: Path) -> None:
    def action(value: dict[str, object]) -> None:
        value["policy"]["external_action_authorized"] = True  # type: ignore[index]

    assert "POLICY_INVALID" in _codes(tmp_path, action)

    def inference(value: dict[str, object]) -> None:
        value["claims"]["approval_inferred_count"] = 1  # type: ignore[index]

    assert "CLAIMS_INVALID" in _codes(tmp_path, inference)


@pytest.mark.unit
def test_second_approval_batch_rejects_bound_evidence_tamper(tmp_path: Path) -> None:
    def mutate(value: dict[str, object]) -> None:
        value["entries"][1]["evidence"]["sha256"] = "sha256:" + ("0" * 64)  # type: ignore[index]

    assert "BOUND_FILE_HASH_MISMATCH" in _codes(tmp_path, mutate)
