from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.verify_notion_core_free_ui_completion import load_json
from code_assist_v1.tools.verify_notion_core_free_ui_pending_approval_batch import (
    DEFAULT_BATCH,
    EXACT_APPROVAL,
    verify_pending_approval_batch,
)


def _write(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "pending.json"
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _codes(tmp_path: Path, mutate) -> set[str]:
    value = copy.deepcopy(load_json(DEFAULT_BATCH))
    mutate(value)
    report, exit_code = verify_pending_approval_batch(_write(tmp_path, value))
    assert exit_code == 1
    return {item["code"] for item in report["errors"]}


@pytest.mark.unit
def test_checked_in_pending_batch_is_exact_bounded_and_not_authorized() -> None:
    report, exit_code = verify_pending_approval_batch()
    assert exit_code == 0, report
    assert report == {
        "contract": "code_assist.notion.core_free_ui.pending_approval_batch.v1",
        "status": "pass",
        "pending_item_count": 2,
        "external_mutation_authorized": False,
        "exact_approval_phrase": EXACT_APPROVAL,
        "errors": [],
    }


@pytest.mark.unit
def test_pending_batch_rejects_workspace_action_and_forbidden_scope_drift(tmp_path: Path) -> None:
    def workspace(value: dict[str, object]) -> None:
        value["pending_items"][0]["workspaces"] = ["PERSONAL"]  # type: ignore[index]

    assert "PENDING_ITEMS_INVALID" in _codes(tmp_path, workspace)

    def action(value: dict[str, object]) -> None:
        value["pending_items"][0]["actions"].append("publish a public link")  # type: ignore[index]

    assert "PENDING_ITEMS_INVALID" in _codes(tmp_path, action)

    def forbidden(value: dict[str, object]) -> None:
        value["pending_items"][0]["forbidden"].remove("permanent delete")  # type: ignore[index]

    assert "PENDING_ITEMS_INVALID" in _codes(tmp_path, forbidden)


@pytest.mark.unit
def test_pending_batch_rejects_phrase_claim_and_evidence_drift(tmp_path: Path) -> None:
    def phrase(value: dict[str, object]) -> None:
        value["exact_combined_approval_phrase"] = "approve"

    assert "APPROVAL_PHRASE_INVALID" in _codes(tmp_path, phrase)

    def claim(value: dict[str, object]) -> None:
        value["claims"]["reference_mutation_executed_by_this_batch"] = True  # type: ignore[index]

    assert "CLAIMS_INVALID" in _codes(tmp_path, claim)

    def evidence(value: dict[str, object]) -> None:
        value["current_evidence"]["certified_count"] = 6  # type: ignore[index]

    assert "CURRENT_EVIDENCE_INVALID" in _codes(tmp_path, evidence)


@pytest.mark.unit
def test_pending_batch_rejects_bound_hash_or_unknown_field(tmp_path: Path) -> None:
    def digest(value: dict[str, object]) -> None:
        value["inputs"]["r1_capture"]["sha256"] = "sha256:" + ("0" * 64)  # type: ignore[index]

    assert "BOUND_FILE_HASH_MISMATCH" in _codes(tmp_path, digest)

    def unknown(value: dict[str, object]) -> None:
        value["auto_approve"] = True

    assert "UNKNOWN_FIELD" in _codes(tmp_path, unknown)
