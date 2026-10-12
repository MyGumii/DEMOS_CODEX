from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.notion_core_free_ui_pending_approval import (
    compile_pending_approval_receipt,
)
from code_assist_v1.tools.notion_core_free_ui_reference_execution_permit import (
    CONTRACT,
    compile_reference_execution_permit,
)
from code_assist_v1.tools.notion_parity.ui_reference_guard import (
    ReferenceC0Observation,
    evaluate_reference_c0,
)
from code_assist_v1.tools.verify_notion_core_free_ui_pending_approval_batch import (
    EXACT_APPROVAL,
)


TIME = "2026-08-22T20:30:00+09:00"


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _passing_decision() -> dict[str, object]:
    return evaluate_reference_c0(
        ReferenceC0Observation(
            run_id="ui1-r1r3-reference",
            browser_connection_visible=True,
            chrome_session_confirmed=True,
            signed_in_visible=True,
            workspace_switcher_opened=True,
            ref_free_a_confirmed=True,
            ref_free_b_confirmed=True,
        )
    ).descriptor()


def _receipt() -> dict[str, object]:
    return compile_pending_approval_receipt(
        approval_text=EXACT_APPROVAL,
        recorded_at=TIME,
    )


@pytest.mark.unit
def test_permit_requires_exact_owner_receipt_c0_and_manifest_before_any_execution(tmp_path: Path) -> None:
    permit = compile_reference_execution_permit(
        receipt_path=_write(tmp_path / "receipt.json", _receipt()),
        c0_decision_path=_write(tmp_path / "c0.json", _passing_decision()),
        recorded_at=TIME,
    )
    assert permit["contract"] == CONTRACT
    assert len(permit["action_authorizations"]) == 10
    assert all(item["allowed"] is True for item in permit["action_authorizations"])
    assert {item["workspace_alias"] for item in permit["action_authorizations"]} == {"REF-FREE-A", "REF-FREE-B"}
    assert permit["cleanup_authorization"] == {
        "allowed": True,
        "mode": "trash_only_recoverable",
        "target_aliases": ["UI-PARITY-R1R3-A-ROOT", "UI-PARITY-R1R3-B-MARKER"],
        "permanent_delete_allowed": False,
    }
    assert permit["claims"]["reference_mutation_executed"] is False


@pytest.mark.unit
def test_permit_refuses_failed_or_noncanonical_c0(tmp_path: Path) -> None:
    failed = evaluate_reference_c0(ReferenceC0Observation(run_id="ui1-r1r3-reference")).descriptor()
    with pytest.raises(ValueError, match="did not pass"):
        compile_reference_execution_permit(
            receipt_path=_write(tmp_path / "receipt.json", _receipt()),
            c0_decision_path=_write(tmp_path / "c0.json", failed),
            recorded_at=TIME,
        )

    bad = _passing_decision()
    bad["workspace_name"] = "raw"
    with pytest.raises(ValueError, match="C0 decision is invalid"):
        compile_reference_execution_permit(
            receipt_path=tmp_path / "receipt.json",
            c0_decision_path=_write(tmp_path / "c0-bad.json", bad),
            recorded_at=TIME,
        )


@pytest.mark.unit
def test_permit_refuses_tampered_receipt_or_invalid_time(tmp_path: Path) -> None:
    receipt = copy.deepcopy(_receipt())
    receipt["authorization"]["personal_workspace_access_allowed"] = True
    with pytest.raises(ValueError, match="approval receipt is invalid"):
        compile_reference_execution_permit(
            receipt_path=_write(tmp_path / "receipt.json", receipt),
            c0_decision_path=_write(tmp_path / "c0.json", _passing_decision()),
            recorded_at=TIME,
        )
    with pytest.raises(ValueError, match="RFC 3339"):
        compile_reference_execution_permit(
            receipt_path=tmp_path / "receipt.json",
            c0_decision_path=tmp_path / "c0.json",
            recorded_at="today",
        )
