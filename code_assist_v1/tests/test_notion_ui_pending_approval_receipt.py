from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.notion_core_free_ui_pending_approval import (
    CONTRACT,
    compile_pending_approval_receipt,
    verify_pending_approval_receipt,
)
from code_assist_v1.tools.verify_notion_core_free_ui_pending_approval_batch import EXACT_APPROVAL


TIME = "2026-08-22T20:00:00+09:00"


def _write(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


@pytest.mark.unit
def test_exact_text_compiles_a_non_executed_bounded_receipt(tmp_path: Path) -> None:
    receipt = compile_pending_approval_receipt(approval_text=EXACT_APPROVAL, recorded_at=TIME)
    assert receipt["authorization"]["external_reference_mutation_authorized"] is True
    assert receipt["authorization"]["workspaces"] == ["REF-FREE-A", "REF-FREE-B"]
    assert receipt["authorization"]["personal_workspace_access_allowed"] is False
    assert receipt["authorization"]["permanent_delete_allowed"] is False
    assert receipt["claims"]["reference_mutation_executed"] is False
    report, exit_code = verify_pending_approval_receipt(_write(tmp_path, receipt))
    assert exit_code == 0, report
    assert report == {
        "contract": CONTRACT,
        "status": "pass",
        "external_reference_mutation_authorized": True,
        "reference_mutation_executed": False,
        "errors": [],
    }


@pytest.mark.unit
def test_compiler_refuses_inexact_text_or_time() -> None:
    with pytest.raises(ValueError, match="exactly match"):
        compile_pending_approval_receipt(approval_text="approve", recorded_at=TIME)
    with pytest.raises(ValueError, match="RFC 3339"):
        compile_pending_approval_receipt(approval_text=EXACT_APPROVAL, recorded_at="today")


@pytest.mark.unit
def test_receipt_verifier_rejects_authorization_or_claim_tamper(tmp_path: Path) -> None:
    receipt = compile_pending_approval_receipt(approval_text=EXACT_APPROVAL, recorded_at=TIME)
    changed = copy.deepcopy(receipt)
    changed["authorization"]["permanent_delete_allowed"] = True
    report, exit_code = verify_pending_approval_receipt(_write(tmp_path, changed))
    assert exit_code == 1
    assert {item["code"] for item in report["errors"]} == {"RECEIPT_MISMATCH"}

    changed = copy.deepcopy(receipt)
    changed["claims"]["reference_mutation_executed"] = True
    report, exit_code = verify_pending_approval_receipt(_write(tmp_path, changed))
    assert exit_code == 1
    assert {item["code"] for item in report["errors"]} == {"RECEIPT_MISMATCH"}


@pytest.mark.unit
def test_receipt_verifier_rejects_batch_binding_or_unknown_field(tmp_path: Path) -> None:
    receipt = compile_pending_approval_receipt(approval_text=EXACT_APPROVAL, recorded_at=TIME)
    changed = copy.deepcopy(receipt)
    changed["batch"]["sha256"] = "sha256:" + ("0" * 64)
    report, exit_code = verify_pending_approval_receipt(_write(tmp_path, changed))
    assert exit_code == 1
    assert {item["code"] for item in report["errors"]} == {"RECEIPT_MISMATCH"}

    changed = copy.deepcopy(receipt)
    changed["authorization"]["personal_workspace_name"] = "anything"
    report, exit_code = verify_pending_approval_receipt(_write(tmp_path, changed))
    assert exit_code == 1
    assert {item["code"] for item in report["errors"]} == {"RECEIPT_MISMATCH"}
