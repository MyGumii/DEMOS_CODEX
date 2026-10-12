from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.tools.verify_notion_core_free_ui_approval_batch import (
    CONTRACT,
    DEFAULT_QUEUE,
    verify_approval_batch_queue,
)
from code_assist_v1.tools.verify_notion_core_free_ui_completion import load_json


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _write_queue(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "approval-queue.json"
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return path


def _verify_mutation(tmp_path: Path, mutate) -> set[str]:
    value = copy.deepcopy(load_json(DEFAULT_QUEUE))
    mutate(value)
    report, exit_code = verify_approval_batch_queue(_write_queue(tmp_path, value))
    assert exit_code == 1
    assert report["status"] == "fail"
    return {item["code"] for item in report["errors"]}


@pytest.mark.unit
def test_checked_in_approval_queue_is_safe_and_independent_lane_is_ready() -> None:
    report, exit_code = verify_approval_batch_queue()

    assert exit_code == 0, report
    assert report == {
        "contract": CONTRACT,
        "status": "pass",
        "profile": "free-owner-chrome-desktop-ko-KR",
        "counts": {
            "pending": 4,
            "human_approval": 2,
            "manual_input": 1,
            "destructive_external_action": 1,
        },
        "independent_lane": "ready_for_local_verification_only",
        "errors": [],
    }

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.verify_notion_core_free_ui_approval_batch",
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
    assert json.loads(completed.stdout)["independent_lane"] == (
        "ready_for_local_verification_only"
    )


@pytest.mark.unit
def test_approval_queue_rejects_evidence_hash_drift(tmp_path: Path) -> None:
    def mutate(value: dict[str, object]) -> None:
        value["entries"][0]["evidence"][0]["sha256"] = "sha256:" + ("0" * 64)  # type: ignore[index]

    assert "EVIDENCE_HASH_MISMATCH" in _verify_mutation(tmp_path, mutate)


@pytest.mark.unit
def test_approval_queue_never_auto_authorizes_destructive_action(tmp_path: Path) -> None:
    def mutate(value: dict[str, object]) -> None:
        value["entries"][3]["status"] = "approved"  # type: ignore[index]

    assert "ENTRY_STATE_INVALID" in _verify_mutation(tmp_path, mutate)


@pytest.mark.unit
def test_manual_input_cannot_be_reclassified_as_approval(tmp_path: Path) -> None:
    def mutate(value: dict[str, object]) -> None:
        value["entries"][1]["kind"] = "human_evidence_acceptance"  # type: ignore[index]

    assert "ENTRY_STATE_INVALID" in _verify_mutation(tmp_path, mutate)


@pytest.mark.unit
def test_batch_command_cannot_absorb_manual_input_or_drop_trash_confirmation(
    tmp_path: Path,
) -> None:
    def mutate(value: dict[str, object]) -> None:
        value["batch_execution"]["exact_user_text"] = (  # type: ignore[index]
            "UI-A11Y-001, UI-EDITOR-003 요약 승인; UI-RESP-001 다중 뷰포트 요약 승인"
        )
        value["batch_execution"]["manual_input_excluded"] = ""  # type: ignore[index]

    assert "BATCH_EXECUTION_INVALID" in _verify_mutation(tmp_path, mutate)
