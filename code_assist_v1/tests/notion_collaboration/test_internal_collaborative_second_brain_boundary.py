from __future__ import annotations

import json
from pathlib import Path

from code_assist_v1.tools.verify_internal_collaborative_second_brain_boundary import (
    CONTRACT_PATH,
    verify,
)


def _write(tmp_path: Path, value: object) -> Path:
    target = tmp_path / "second-brain-boundary.json"
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def test_s0a_p1_frozen_second_brain_boundary_rejects_later_sbe_inputs() -> None:
    report = verify()

    assert report["status"] == "fail"
    assert report["error_count"] == 21
    assert {item["code"] for item in report["errors"]} == {
        "DYNAMIC_INSTRUCTION_DRIFT",
        "MANIFEST_HASH_DRIFT",
        "SBE_DOCUMENT_HASH_DRIFT",
        "SBE_DYNAMIC_PREFLIGHT_DRIFT",
        "SBE_PROTECTED_FILE_HASH_DRIFT",
    }
    assert report["gate_passed"] is False
    assert report["sbe_document_count"] == 18
    assert report["sbe_scenario_count"] == 9
    assert report["protected_sbe_change_required"] is False
    assert report["product_complete"] is False


def test_s0a_p1_second_brain_boundary_rejects_false_completion(
    tmp_path: Path,
) -> None:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    value["claims"]["product_complete"] = True

    report = verify(_write(tmp_path, value))

    assert report["status"] == "fail"
    assert any(item["code"] == "CLAIMS_FALSE_GREEN" for item in report["errors"])


def test_s0a_p1_second_brain_boundary_rejects_timestamp_identity_drift(
    tmp_path: Path,
) -> None:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    value["notion_source_boundary"]["timestamp_fields_in_publication_identity"] = [
        "updated_at"
    ]

    report = verify(_write(tmp_path, value))

    assert report["status"] == "fail"
    assert any(item["code"] == "SOURCE_BOUNDARY_DRIFT" for item in report["errors"])


def test_s0a_p1_second_brain_boundary_rejects_revocation_linearization_drift(
    tmp_path: Path,
) -> None:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    value["cross_database_publication_adr"]["revocation_contract"] = []

    report = verify(_write(tmp_path, value))

    assert report["status"] == "fail"
    assert any(item["code"] == "LINEARIZATION_ADR_DRIFT" for item in report["errors"])
