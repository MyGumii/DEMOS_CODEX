from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.notion_core_free_ui_approval_batch_v4 import (
    compile_approval_batch_receipt_v4,
    verify_approval_batch_receipt_v4,
)
from code_assist_v1.tools.verify_notion_core_free_ui_approval_batch_v4 import (
    DEFAULT_QUEUE,
    verify_approval_batch_v4,
)
from code_assist_v1.tools.verify_notion_core_free_ui_completion import load_json
from code_assist_v1.tools.verify_notion_core_free_ui_editor006_policy_proposal import (
    EXACT_APPROVAL_TEXT,
)
from code_assist_v1.tools.verify_notion_core_free_ui_editor006_policy_proposal_v2 import (
    DEFAULT_PROPOSAL,
    verify_editor006_policy_proposal_v2,
)


TIME = "2026-08-22T23:40:00+09:00"
RECEIPT = DEFAULT_QUEUE.parent / "approval_batch_receipt.v4.20260822.json"


def _write(tmp_path: Path, name: str, value: object) -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.unit
def test_corrected_editor006_proposal_binds_immutable_pending_checkpoint() -> None:
    report, exit_code = verify_editor006_policy_proposal_v2()
    assert exit_code == 0, report
    assert report["status"] == "pass"
    assert report["required_user_text"] == EXACT_APPROVAL_TEXT
    assert report["approval_scope_changed"] is False
    assert report["authorization_effect_changed"] is False


@pytest.mark.unit
def test_corrected_editor006_proposal_rejects_checkpoint_or_scope_drift(
    tmp_path: Path,
) -> None:
    value = copy.deepcopy(load_json(DEFAULT_PROPOSAL))
    value["evidence"]["approval_pending_checkpoint"]["sha256"] = "sha256:" + ("0" * 64)
    report, exit_code = verify_editor006_policy_proposal_v2(
        _write(tmp_path, "checkpoint-drift.json", value)
    )
    assert exit_code == 1
    assert "BOUND_FILE_HASH_MISMATCH" in {item["code"] for item in report["errors"]}

    value = copy.deepcopy(load_json(DEFAULT_PROPOSAL))
    value["correction"]["approval_scope_changed"] = True
    report, exit_code = verify_editor006_policy_proposal_v2(
        _write(tmp_path, "scope-drift.json", value)
    )
    assert exit_code == 1
    assert "APPROVAL_SCOPE_CHANGED" in {item["code"] for item in report["errors"]}


@pytest.mark.unit
def test_fourth_queue_reseals_existing_exact_approval_without_new_request() -> None:
    report, exit_code = verify_approval_batch_v4()
    assert exit_code == 0, report
    assert report["approved_entry_count"] == 1
    assert report["new_user_approval_pending_count"] == 0
    assert report["external_action_pending_count"] == 0
    assert report["exact_user_text"] == EXACT_APPROVAL_TEXT


@pytest.mark.unit
def test_fourth_queue_rejects_supersession_or_authorization_drift(
    tmp_path: Path,
) -> None:
    value = copy.deepcopy(load_json(DEFAULT_QUEUE))
    value["supersedes"]["authorization_effect_changed"] = True
    report, exit_code = verify_approval_batch_v4(
        _write(tmp_path, "supersession.json", value)
    )
    assert exit_code == 1
    assert "AUTHORIZATION_EFFECT_CHANGED" in {
        item["code"] for item in report["errors"]
    }

    value = copy.deepcopy(load_json(DEFAULT_QUEUE))
    value["entries"][0]["authorization_effect"] = "release_promotion"
    report, exit_code = verify_approval_batch_v4(
        _write(tmp_path, "authorization.json", value)
    )
    assert exit_code == 1
    assert "ENTRY_INVALID" in {item["code"] for item in report["errors"]}


@pytest.mark.unit
def test_v4_receipt_requires_exact_user_text_and_is_non_mutating(
    tmp_path: Path,
) -> None:
    receipt = compile_approval_batch_receipt_v4(
        approval_text=EXACT_APPROVAL_TEXT,
        recorded_at=TIME,
    )
    report, exit_code = verify_approval_batch_receipt_v4(
        _write(tmp_path, "receipt.json", receipt)
    )
    assert exit_code == 0, report
    assert receipt["correction"]["new_user_approval_requested"] is False
    assert receipt["safety"]["external_action_executed"] is False
    assert receipt["safety"]["completion_matrix_mutated"] is False

    with pytest.raises(ValueError, match="exactly match"):
        compile_approval_batch_receipt_v4(
            approval_text="승인",
            recorded_at=TIME,
        )


@pytest.mark.unit
def test_v4_checked_in_receipt_is_exact_and_rejects_tamper(tmp_path: Path) -> None:
    report, exit_code = verify_approval_batch_receipt_v4(RECEIPT)
    assert exit_code == 0, report

    receipt = copy.deepcopy(load_json(RECEIPT))
    receipt["resolved_entries"][0]["authorization_effect"] = "release_promotion"
    report, exit_code = verify_approval_batch_receipt_v4(
        _write(tmp_path, "tampered.json", receipt)
    )
    assert exit_code == 1
    assert report["status"] == "fail"
