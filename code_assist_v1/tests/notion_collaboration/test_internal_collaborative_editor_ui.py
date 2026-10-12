from __future__ import annotations

import json
from pathlib import Path

import pytest

import code_assist_v1.tools.verify_internal_collaborative_editor_ui as target

from code_assist_v1.tools.internal_collaborative_validation import (
    _inherit_validated_manifest_drift,
)
from code_assist_v1.tools.verify_internal_collaborative_editor_ui import (
    CONTRACT_PATH,
    verify,
)


def test_e1_p2_editor_ui_contract_is_fail_closed_and_current() -> None:
    report = _inherit_validated_manifest_drift(verify(), "E1-P2")
    assert report["status"] == "pass", report
    assert report["error_count"] == 0
    assert report["gate_passed"] is True
    assert report["product_complete"] is False


def test_e1_p2_editor_ui_contract_rejects_a_false_completion_claim(
    tmp_path: Path,
) -> None:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    value["claims"]["product_complete"] = True
    tampered = tmp_path / "editor-ui-contract.json"
    tampered.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = verify(tampered)
    assert report["status"] == "fail"
    assert any(item["code"] == "CLAIMS_FALSE_GREEN" for item in report["errors"])


def test_e1_p2_editor_ui_contract_requires_exact_latest_s0a_successor_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    successor = json.loads(
        target.SECOND_BRAIN_SUCCESSOR_CONTRACT_PATH.read_text(encoding="utf-8")
    )
    binding = next(
        item
        for item in successor["source_manifest"]
        if item["path"] == "code_assist_v1/tools/internal_collaborative_validation.py"
    )
    binding["sha256"] = "sha256:" + "0" * 64
    tampered = tmp_path / "s0a-p2-successor-contract.json"
    tampered.write_text(
        json.dumps(successor, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(target, "SECOND_BRAIN_SUCCESSOR_CONTRACT_PATH", tampered)

    report = target.verify()

    assert report["status"] == "fail"
    assert any(
        item["code"] == "MANIFEST_HASH_DRIFT"
        and item["subject"] == "code_assist_v1/tools/internal_collaborative_validation.py"
        for item in report["errors"]
    )
