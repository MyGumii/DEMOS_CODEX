from __future__ import annotations

import hashlib
import json
from pathlib import Path

from code_assist_v1.tools.prepare_internal_collaborative_e1_p3_seal import (
    CATALOG_PATH,
    POINTER_PATH,
    prepare,
)
from code_assist_v1.tools.verify_internal_collaborative_native_ime_receipt import (
    EXPECTED_CONFIRMATION,
    EXPECTED_CONTRACT,
    REQUEST_PATH,
)


def _valid_receipt() -> dict[str, object]:
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


def _preseal_inputs(tmp_path: Path) -> tuple[Path, Path]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    packet = next(item for item in catalog["packets"] if item["id"] == "E1-P3")
    packet["status"] = "planned"
    catalog_path = tmp_path / "preseal-catalog.json"
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    pointer = json.loads(POINTER_PATH.read_text(encoding="utf-8"))
    predecessor_path = Path(
        "code_assist_v1/docs/notion_parity/checkpoints/"
        "ui-internal-collaboration-e1-p2-host-bound-editor-20260825.json"
    )
    repository_root = CATALOG_PATH.parents[4]
    pointer["packet_id"] = "E1-P2"
    pointer["checkpoint_path"] = predecessor_path.as_posix()
    pointer["checkpoint_sha256"] = "sha256:" + hashlib.sha256(
        (repository_root / predecessor_path).read_bytes()
    ).hexdigest()
    pointer_path = tmp_path / "preseal-pointer.json"
    pointer_path.write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return catalog_path, pointer_path


def _sealed_inputs(tmp_path: Path) -> tuple[Path, Path]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    packet = next(item for item in catalog["packets"] if item["id"] == "E1-P3")
    assert packet["status"] == "gate_passed"
    catalog_path = tmp_path / "sealed-catalog.json"
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    checkpoint_path = Path(
        "code_assist_v1/docs/notion_parity/checkpoints/"
        "ui-internal-collaboration-e1-p3-convergence-native-ime-20260825.json"
    )
    repository_root = CATALOG_PATH.parents[4]
    pointer = json.loads(POINTER_PATH.read_text(encoding="utf-8"))
    pointer["packet_id"] = "E1-P3"
    pointer["checkpoint_path"] = checkpoint_path.as_posix()
    pointer["checkpoint_sha256"] = "sha256:" + hashlib.sha256(
        (repository_root / checkpoint_path).read_bytes()
    ).hexdigest()
    pointer_path = tmp_path / "sealed-pointer.json"
    pointer_path.write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return catalog_path, pointer_path


def test_e1_p3_seal_preflight_reports_only_native_ime_pending(tmp_path: Path) -> None:
    missing = tmp_path / "missing-native-ime-receipt.json"
    catalog, pointer = _preseal_inputs(tmp_path)

    report = prepare(receipt_path=missing, catalog_path=catalog, pointer_path=pointer)

    assert report["status"] == "pending_native_ime", report
    assert report["error_count"] == 0
    assert report["blocker_count"] == 1
    assert report["blockers"][0]["code"] == "NATIVE_IME_RECEIPT_MISSING"
    assert report["ready_to_apply_seal"] is False
    assert report["current_predecessor_packet"] == "E1-P2"
    assert report["current_pointer_packet"] == "E1-P2"
    assert report["sbe_dynamic_baseline"]["status"] == "pass"
    assert report["sbe_dynamic_baseline"]["goal_count"] == 20
    assert report["sbe_dynamic_baseline"]["scenario_count"] == 284
    assert report["mutated"] is False
    assert report["production_10011_10012_changed"] is False


def test_e1_p3_seal_preflight_becomes_ready_with_valid_receipt(tmp_path: Path) -> None:
    receipt = tmp_path / "native-ime-receipt.json"
    receipt.write_text(
        json.dumps(_valid_receipt(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    catalog, pointer = _preseal_inputs(tmp_path)

    report = prepare(receipt_path=receipt, catalog_path=catalog, pointer_path=pointer)

    assert report["status"] == "ready_to_apply_seal", report
    assert report["error_count"] == 0
    assert report["blocker_count"] == 0
    assert report["ready_to_apply_seal"] is True
    assert report["native_ime_receipt_verified"] is True
    assert report["seal_mutation_plan"]["next_packet_after_seal"] == "S0A-P1"
    assert report["seal_mutation_plan"]["matrix_semantic_mutation_required"] is False


def test_e1_p3_seal_preflight_is_idempotent_after_seal(tmp_path: Path) -> None:
    catalog, pointer = _sealed_inputs(tmp_path)
    report = prepare(catalog_path=catalog, pointer_path=pointer)

    assert report["status"] == "sealed", report
    assert report["error_count"] == 0
    assert report["blocker_count"] == 0
    assert report["ready_to_apply_seal"] is False
    assert report["seal_applied"] is True
    assert report["current_pointer_packet"] == "E1-P3"
    assert report["seal_mutation_plan"]["publish_execution_receipt_and_checkpoint"] is False
    assert report["seal_mutation_plan"]["advance_current_pointer_to"] is None


def test_e1_p3_seal_preflight_rejects_premature_catalog_completion(
    tmp_path: Path,
) -> None:
    _, preseal_pointer = _preseal_inputs(tmp_path)

    report = prepare(
        pointer_path=preseal_pointer,
        receipt_path=tmp_path / "missing.json",
    )

    assert report["status"] == "fail"
    assert any(item["code"] == "CURRENT_POINTER_DRIFT" for item in report["errors"])
    assert report["ready_to_apply_seal"] is False
    assert report["mutated"] is False
