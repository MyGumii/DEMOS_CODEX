from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.tools.notion_core_free_ui_approval_batch import (
    CONTRACT,
    _canonical_bytes,
    _write_create_only,
    compile_approval_batch_receipt,
    verify_approval_batch_receipt,
)
from code_assist_v1.tools.verify_notion_core_free_ui_approval_batch import (
    DEFAULT_QUEUE,
)
from code_assist_v1.tools.verify_notion_core_free_ui_completion import load_json


PROJECT_ROOT = Path(__file__).resolve().parents[2]
QUEUE = load_json(DEFAULT_QUEUE)
EXACT_TEXT = QUEUE["batch_execution"]["exact_user_text"]
RECORDED_AT = "2026-08-22T17:00:00+09:00"


@pytest.mark.unit
def test_compile_receipt_resolves_only_batched_approvals_without_external_action() -> None:
    receipt = compile_approval_batch_receipt(
        approval_text=EXACT_TEXT,
        recorded_at=RECORDED_AT,
    )

    assert receipt["contract"] == CONTRACT
    assert [item["entry_id"] for item in receipt["resolved_entries"]] == QUEUE[
        "batch_execution"
    ]["entry_ids"]
    assert receipt["excluded_entries"] == ["UI-EDITOR-004-NATIVE-IME-INPUT"]
    assert receipt["claims"] == {
        "resolved_entry_count": 3,
        "human_evidence_approval_count": 2,
        "destructive_confirmation_count": 1,
        "manual_input_resolved_count": 0,
        "external_action_executed_count": 0,
        "core_ui_complete": False,
        "overall_goal_complete": False,
    }
    assert receipt["safety"]["receipt_creation_only"] is True
    assert receipt["safety"]["browser_control_used"] is False
    assert receipt["safety"]["external_action_executed"] is False


@pytest.mark.unit
def test_compile_rejects_partial_phrase_or_manual_input_substitution() -> None:
    with pytest.raises(ValueError, match="exactly match"):
        compile_approval_batch_receipt(
            approval_text=EXACT_TEXT.rsplit(";", 1)[0],
            recorded_at=RECORDED_AT,
        )
    with pytest.raises(ValueError, match="exactly match"):
        compile_approval_batch_receipt(
            approval_text=EXACT_TEXT + "; UI-EDITOR-004 IME 입력 완료",
            recorded_at=RECORDED_AT,
        )


@pytest.mark.unit
def test_create_only_write_replays_identical_bytes_and_rejects_drift(
    tmp_path: Path,
) -> None:
    output = tmp_path / "receipt.json"
    payload = _canonical_bytes(
        compile_approval_batch_receipt(
            approval_text=EXACT_TEXT,
            recorded_at=RECORDED_AT,
        )
    )

    assert _write_create_only(output, payload) == "created"
    assert _write_create_only(output, payload) == "replayed"
    with pytest.raises(FileExistsError, match="different bytes"):
        _write_create_only(output, payload + b" ")


@pytest.mark.unit
def test_receipt_verifier_rejects_tampered_external_action_claim(
    tmp_path: Path,
) -> None:
    receipt = compile_approval_batch_receipt(
        approval_text=EXACT_TEXT,
        recorded_at=RECORDED_AT,
    )
    receipt["safety"]["external_action_executed"] = True
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")

    report, exit_code = verify_approval_batch_receipt(path)
    assert exit_code == 1
    assert report["status"] == "fail"
    assert report["external_action_executed"] is True
    assert any(
        "receipt does not exactly match" in error for error in report["errors"]
    )


@pytest.mark.unit
def test_cli_compile_and_verify_are_create_only(tmp_path: Path) -> None:
    output = tmp_path / "receipt.json"
    compile_command = [
        sys.executable,
        "-m",
        "code_assist_v1.tools.notion_core_free_ui_approval_batch",
        "compile",
        "--approval-text",
        EXACT_TEXT,
        "--recorded-at",
        RECORDED_AT,
        "--output",
        str(output),
    ]
    first = subprocess.run(
        compile_command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    second = subprocess.run(
        compile_command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    verified = subprocess.run(
        [
            sys.executable,
            "-m",
            "code_assist_v1.tools.notion_core_free_ui_approval_batch",
            "verify",
            "--receipt",
            str(output),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert first.returncode == second.returncode == verified.returncode == 0
    assert json.loads(first.stdout)["disposition"] == "created"
    assert json.loads(second.stdout)["disposition"] == "replayed"
    assert json.loads(verified.stdout) == {
        "contract": CONTRACT,
        "status": "pass",
        "profile": "free-owner-chrome-desktop-ko-KR",
        "resolved_entry_count": 3,
        "external_action_executed": False,
        "errors": [],
    }
