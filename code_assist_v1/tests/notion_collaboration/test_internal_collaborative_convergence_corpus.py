from __future__ import annotations

import json
from pathlib import Path

from code_assist_v1.tools.internal_collaborative_validation import (
    _inherit_validated_manifest_drift,
)
from code_assist_v1.tools.verify_internal_collaborative_convergence_corpus import (
    CONTRACT_PATH,
    verify,
)


def _tampered_contract(tmp_path: Path, value: dict[str, object]) -> Path:
    target = tmp_path / "e1-p3-convergence-contract.json"
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def test_e1_p3_completed_convergence_contract_is_current_and_strict() -> None:
    report = _inherit_validated_manifest_drift(verify(), "E1-P3")

    assert report["status"] == "pass", report
    assert report["error_count"] == 0
    assert report["automated_validation_passed"] is True
    assert report["gate_passed"] is True
    assert report["native_ime_pending"] is False
    assert report["product_complete"] is False


def test_e1_p3_completed_convergence_contract_rejects_false_gate_claim(
    tmp_path: Path,
) -> None:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    value["claims"]["E1_P3_gate_passed"] = False

    report = _inherit_validated_manifest_drift(
        verify(_tampered_contract(tmp_path, value)),
        "E1-P3",
    )

    assert report["status"] == "fail"
    assert any(item["code"] == "CLAIMS_FALSE_GREEN" for item in report["errors"])


def test_e1_p3_completed_convergence_contract_rejects_corpus_hash_drift(
    tmp_path: Path,
) -> None:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    value["convergence_corpus"]["canonical_corpus_sha256"] = "sha256:" + "0" * 64

    report = _inherit_validated_manifest_drift(
        verify(_tampered_contract(tmp_path, value)),
        "E1-P3",
    )

    assert report["status"] == "fail"
    assert any(item["code"] == "CORPUS_DENOMINATOR" for item in report["errors"])


def test_e1_p3_completed_convergence_contract_rejects_receipt_hash_drift(
    tmp_path: Path,
) -> None:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    value["native_ime_gate"]["finalization_receipt_sha256"] = "sha256:" + "0" * 64

    report = verify(_tampered_contract(tmp_path, value))

    assert report["status"] == "fail"
    assert any(
        item["code"] == "NATIVE_IME_RECEIPT_HASH_DRIFT" for item in report["errors"]
    )


def test_e1_p3_verifier_still_accepts_exact_pending_lifecycle(tmp_path: Path) -> None:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    value["status"] = "automated_validated_native_ime_pending"
    value["native_ime_gate"].update(
        {
            "status": "pending_human_os_ime_input",
            "user_confirmation_received": False,
            "native_os_ime_verified": False,
            "two_principal_server_convergence_verified_after_human_input": False,
            "test_page_moved_to_trash_after_verification": False,
            "finalization_receipt_path": None,
            "finalization_receipt_sha256": None,
        }
    )
    for field in (
        "native_ime_verified",
        "E1_P3_gate_passed",
        "EDIT_001_closed",
        "EDIT_002_closed",
        "EDIT_003_closed",
        "E1_phase_gate_closed",
    ):
        value["claims"][field] = False

    report = _inherit_validated_manifest_drift(
        verify(_tampered_contract(tmp_path, value)),
        "E1-P3",
    )

    assert report["status"] == "pass", report
    assert report["gate_passed"] is False
    assert report["native_ime_pending"] is True
