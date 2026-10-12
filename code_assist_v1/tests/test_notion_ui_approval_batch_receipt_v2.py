from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.notion_core_free_ui_approval_batch_v2 import (
    compile_approval_batch_receipt_v2,
    verify_approval_batch_receipt_v2,
)


EXACT = "UI-EDITOR-004 네이티브 IME 요약 승인; UI-PERF-001 로컬 성능 예산 제안 승인"
TIME = "2026-08-22T17:41:57+09:00"


@pytest.mark.unit
def test_v2_receipt_compiles_exact_two_non_mutating_decisions(tmp_path: Path) -> None:
    receipt = compile_approval_batch_receipt_v2(approval_text=EXACT, recorded_at=TIME)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    report, exit_code = verify_approval_batch_receipt_v2(path)
    assert exit_code == 0, report
    assert [item["kind"] for item in receipt["resolved_entries"]] == ["human_evidence_acceptance", "human_policy_acceptance"]
    assert receipt["safety"]["external_action_executed"] is False
    assert receipt["safety"]["completion_matrix_mutated"] is False


@pytest.mark.unit
def test_v2_receipt_rejects_phrase_or_time_drift() -> None:
    with pytest.raises(ValueError, match="exactly match"):
        compile_approval_batch_receipt_v2(approval_text="승인", recorded_at=TIME)
    with pytest.raises(ValueError, match="RFC 3339"):
        compile_approval_batch_receipt_v2(approval_text=EXACT, recorded_at="today")


@pytest.mark.unit
def test_v2_receipt_verifier_rejects_tampered_effect(tmp_path: Path) -> None:
    receipt = compile_approval_batch_receipt_v2(approval_text=EXACT, recorded_at=TIME)
    receipt = copy.deepcopy(receipt)
    receipt["resolved_entries"][1]["authorization_effect"] = "release_promotion"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    report, exit_code = verify_approval_batch_receipt_v2(path)
    assert exit_code == 1
    assert report["status"] == "fail"


@pytest.mark.unit
def test_v2_receipt_verifier_rejects_external_action_claim(tmp_path: Path) -> None:
    receipt = compile_approval_batch_receipt_v2(approval_text=EXACT, recorded_at=TIME)
    receipt = copy.deepcopy(receipt)
    receipt["safety"]["external_action_executed"] = True
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    report, exit_code = verify_approval_batch_receipt_v2(path)
    assert exit_code == 1
    assert report["status"] == "fail"
