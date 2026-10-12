from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.notion_core_free_ui_approval_batch_v3 import (
    verify_approval_batch_receipt_v3,
)
from code_assist_v1.tools.verify_notion_core_free_ui_approval_batch_v3 import (
    DEFAULT_QUEUE,
    verify_approval_batch_v3,
)
from code_assist_v1.tools.verify_notion_core_free_ui_completion import load_json
from code_assist_v1.tools.verify_notion_core_free_ui_editor006_policy_proposal import (
    DEFAULT_PROPOSAL,
    EXACT_APPROVAL_TEXT,
    verify_editor006_policy_proposal,
)


RECEIPT = (
    DEFAULT_QUEUE.parent / "approval_batch_receipt.v3.20260822.json"
)


def _write(tmp_path: Path, name: str, value: object) -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.unit
def test_editor006_policy_proposal_v1_is_preserved_as_superseded_history() -> None:
    report, exit_code = verify_editor006_policy_proposal()
    assert exit_code == 1
    assert report["status"] == "fail"
    assert "BOUND_FILE_HASH_MISMATCH" in {
        item["code"] for item in report["errors"]
    }


@pytest.mark.unit
def test_editor006_policy_proposal_rejects_claim_or_evidence_drift(
    tmp_path: Path,
) -> None:
    value = copy.deepcopy(load_json(DEFAULT_PROPOSAL))
    value["decision"]["does_not_allow"].remove("release_promotion")
    report, exit_code = verify_editor006_policy_proposal(
        _write(tmp_path, "claim-drift.json", value)
    )
    assert exit_code == 1
    assert "DECISION_INVALID" in {item["code"] for item in report["errors"]}

    value = copy.deepcopy(load_json(DEFAULT_PROPOSAL))
    value["evidence"]["local_result"]["sha256"] = "sha256:" + ("0" * 64)
    report, exit_code = verify_editor006_policy_proposal(
        _write(tmp_path, "evidence-drift.json", value)
    )
    assert exit_code == 1
    assert "BOUND_FILE_HASH_MISMATCH" in {
        item["code"] for item in report["errors"]
    }


@pytest.mark.unit
def test_third_approval_queue_is_preserved_as_superseded_history() -> None:
    report, exit_code = verify_approval_batch_v3()
    assert exit_code == 1
    assert report["pending_entry_count"] == 1
    assert report["external_action_pending_count"] == 0
    assert report["exact_user_text"] == EXACT_APPROVAL_TEXT
    assert "PROPOSAL_INVALID" in {item["code"] for item in report["errors"]}


@pytest.mark.unit
def test_third_approval_queue_rejects_inference_or_scope_drift(
    tmp_path: Path,
) -> None:
    value = copy.deepcopy(load_json(DEFAULT_QUEUE))
    value["claims"]["approval_inferred_count"] = 1
    report, exit_code = verify_approval_batch_v3(
        _write(tmp_path, "inference.json", value)
    )
    assert exit_code == 1
    assert "CLAIMS_INVALID" in {item["code"] for item in report["errors"]}

    value = copy.deepcopy(load_json(DEFAULT_QUEUE))
    value["entries"][0]["scenario_ids"] = ["UI-PERF-001"]
    report, exit_code = verify_approval_batch_v3(
        _write(tmp_path, "scope.json", value)
    )
    assert exit_code == 1
    assert "ENTRY_INVALID" in {item["code"] for item in report["errors"]}


@pytest.mark.unit
def test_v3_receipt_fails_closed_after_legitimate_matrix_advancement() -> None:
    report, exit_code = verify_approval_batch_receipt_v3(RECEIPT)
    assert exit_code == 1
    assert report["status"] == "fail"
    assert any("approval queue verification failed" in item for item in report["errors"])


@pytest.mark.unit
def test_v3_receipt_rejects_authorization_or_safety_tamper(
    tmp_path: Path,
) -> None:
    receipt = copy.deepcopy(load_json(RECEIPT))
    receipt["resolved_entries"][0]["authorization_effect"] = "release_promotion"
    report, exit_code = verify_approval_batch_receipt_v3(
        _write(tmp_path, "authorization.json", receipt)
    )
    assert exit_code == 1
    assert report["status"] == "fail"

    receipt = copy.deepcopy(load_json(RECEIPT))
    receipt["safety"]["external_action_executed"] = True
    report, exit_code = verify_approval_batch_receipt_v3(
        _write(tmp_path, "safety.json", receipt)
    )
    assert exit_code == 1
    assert report["status"] == "fail"
