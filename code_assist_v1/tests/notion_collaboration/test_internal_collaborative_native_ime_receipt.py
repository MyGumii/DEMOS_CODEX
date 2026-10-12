from __future__ import annotations

import hashlib
import json
from pathlib import Path

from code_assist_v1.tools.verify_internal_collaborative_native_ime_receipt import (
    EXPECTED_CONFIRMATION,
    EXPECTED_CONTRACT,
    REQUEST_PATH,
    verify,
)


def _receipt() -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract": EXPECTED_CONTRACT,
        "status": "pass",
        "approval_id": "AP-DEV-PAIR",
        "request_id": "E1-P3-NATIVE-IME-MANUAL-20260825",
        "manual_request_sha256": "sha256:"
        + hashlib.sha256(REQUEST_PATH.read_bytes()).hexdigest(),
        "test_page_locator_sha256": "sha256:" + "1" * 64,
        "user_confirmation_sha256": "sha256:"
        + hashlib.sha256(EXPECTED_CONFIRMATION.encode("utf-8")).hexdigest(),
        "browser": {
            "engine": "chromium",
            "channel": "chrome",
            "version": "151.0.7922.173",
            "headless_verification_only": True,
            "independent_ephemeral_contexts": 2,
            "browser_closed": True,
        },
        "native_ime_action_performed_by_human": True,
        "automated_text_input_performed": False,
        "principal_count": 2,
        "same_projection_hash": True,
        "required_text_occurrence_count": 1,
        "final_projection_sha256": "sha256:" + "2" * 64,
        "unsynced_count": 0,
        "page_error_count": 0,
        "test_page_moved_to_trash": True,
        "credential_or_session_value_recorded": False,
        "production_10011_10012_changed": False,
        "duration_ms": 1234,
        "product_complete": False,
    }


def _write(tmp_path: Path, value: dict[str, object]) -> Path:
    target = tmp_path / "receipt.json"
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def test_e1_p3_native_ime_receipt_accepts_only_the_complete_redacted_shape(
    tmp_path: Path,
) -> None:
    report = verify(_write(tmp_path, _receipt()))

    assert report["status"] == "pass", report
    assert report["error_count"] == 0
    assert report["native_ime_verified"] is True
    assert report["gate_passed"] is True
    assert report["product_complete"] is False


def test_e1_p3_native_ime_receipt_rejects_automated_input(tmp_path: Path) -> None:
    receipt = _receipt()
    receipt["automated_text_input_performed"] = True

    report = verify(_write(tmp_path, receipt))

    assert report["status"] == "fail"
    assert any(
        item["code"] == "NATIVE_IME_CONVERGENCE_DENOMINATOR"
        for item in report["errors"]
    )


def test_e1_p3_native_ime_receipt_rejects_invalid_projection_hash(tmp_path: Path) -> None:
    receipt = _receipt()
    receipt["final_projection_sha256"] = "sha256:invalid"

    report = verify(_write(tmp_path, receipt))

    assert report["status"] == "fail"
    assert any(item["code"] == "HASH_FORMAT" for item in report["errors"])


def test_e1_p3_native_ime_receipt_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    target = tmp_path / "duplicate.json"
    target.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")

    report = verify(target)

    assert report["status"] == "fail"
    assert report["errors"][0]["code"] == "RECEIPT_READ_FAILED"
