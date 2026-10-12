from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.internal_collaborative_validation import (
    SOURCE_COMPATIBILITY_COVERAGE,
    SOURCE_COMPATIBILITY_PATH,
    V0B_COMPATIBLE_RECEIPT_PATH,
    V0B_RUNTIME_COMPATIBILITY_PATH,
    _inherit_validated_manifest_drift,
    _inherit_v0b_runtime_compatibility,
    _is_exact_v0b_capture_product_build_drift,
    canonical_sha256,
)


def _report(coverage_id: str) -> dict[str, object]:
    subjects = sorted(SOURCE_COMPATIBILITY_COVERAGE[coverage_id])
    return {
        "status": "fail",
        "error_count": len(subjects),
        "errors": [
            {"code": "MANIFEST_HASH_DRIFT", "subject": subject}
            for subject in subjects
        ],
        "gate_passed": False,
        "product_complete": False,
    }


def _write_record(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _v0b_report() -> dict[str, object]:
    return {
        "status": "pass",
        "error_count": 0,
        "errors": [],
        "gate_passed": False,
        "runtime_compatibility_required": True,
        "core_test_count": 21,
        "performance_run_count": 2,
        "visual_similarity_pass_count": 36,
        "accessibility_pass_count": 36,
        "product_complete": False,
    }


def _v0b_capture_product_build_report(
    selected: dict[str, object],
) -> dict[str, object]:
    relative = selected["fresh_gate_evidence"]["base_capture_receipt"]["path"]  # type: ignore[index]
    subject = str((Path.cwd() / str(relative)).resolve())
    return {
        "contract": "code_assist.notion.internal_collaborative_v0b_p1.fresh_gate_audit.v1",
        "status": "fail",
        "error_count": 1,
        "errors": [
            {
                "code": "CAPTURE_PRODUCT_BUILD",
                "subject": subject,
                "message": "capture must bind the current product build",
            }
        ],
        "gate_passed": False,
        "product_complete": False,
    }


@pytest.mark.unit
@pytest.mark.parametrize("coverage_id", sorted(SOURCE_COMPATIBILITY_COVERAGE))
def test_exact_regression_backed_manifest_drift_is_inherited(
    coverage_id: str,
) -> None:
    result = _inherit_validated_manifest_drift(_report(coverage_id), coverage_id)
    assert result["status"] == "pass"
    assert result["error_count"] == 0
    assert result["errors"] == []
    assert result["gate_passed"] is True
    assert result["product_complete"] is False
    assert result["source_compatibility_inheritance"]["coverage_id"] == coverage_id


@pytest.mark.unit
def test_uncovered_manifest_drift_remains_failed() -> None:
    report = _report("E1-P1")
    report["errors"].append(  # type: ignore[union-attr]
        {"code": "MANIFEST_HASH_DRIFT", "subject": "code_assist_v1/uncovered.py"}
    )
    report["error_count"] = len(report["errors"])  # type: ignore[arg-type]
    assert _inherit_validated_manifest_drift(report, "E1-P1") == report


@pytest.mark.unit
def test_non_manifest_failure_is_never_inherited() -> None:
    report = _report("E1-P1")
    report["errors"][0]["code"] = "BEHAVIORAL_FAILURE"  # type: ignore[index]
    assert _inherit_validated_manifest_drift(report, "E1-P1") == report


@pytest.mark.unit
def test_tampered_compatibility_self_hash_is_rejected(tmp_path: Path) -> None:
    value = json.loads(SOURCE_COMPATIBILITY_PATH.read_text(encoding="utf-8"))
    value["claims"]["product_complete"] = True
    path = tmp_path / "tampered.json"
    _write_record(path, value)
    report = _report("E1-P1")
    assert (
        _inherit_validated_manifest_drift(
            report,
            "E1-P1",
            compatibility_path=path,
        )
        == report
    )


@pytest.mark.unit
def test_resealed_wrong_current_source_hash_is_rejected(tmp_path: Path) -> None:
    value = json.loads(SOURCE_COMPATIBILITY_PATH.read_text(encoding="utf-8"))
    changed = copy.deepcopy(value)
    central_binding = next(
        item
        for item in changed["source_bindings"]
        if item["path"]
        == "code_assist_v1/tools/internal_collaborative_validation.py"
    )
    central_binding["sha256"] = "sha256:" + "0" * 64
    changed["record_sha256"] = canonical_sha256(
        {key: item for key, item in changed.items() if key != "record_sha256"}
    )
    path = tmp_path / "wrong-current-hash.json"
    _write_record(path, changed)
    report = _report("E1-P1")
    assert (
        _inherit_validated_manifest_drift(
            report,
            "E1-P1",
            compatibility_path=path,
        )
        == report
    )


@pytest.mark.unit
def test_v0b_exact_ui_evidence_inherits_only_q0_verified_runtime_drift() -> None:
    selected = json.loads(V0B_COMPATIBLE_RECEIPT_PATH.read_text(encoding="utf-8"))
    result = _inherit_v0b_runtime_compatibility(
        _v0b_report(),
        selected_receipt=selected,
        selected_path=V0B_COMPATIBLE_RECEIPT_PATH,
    )
    assert result["status"] == "pass"
    assert result["error_count"] == 0
    assert result["errors"] == []
    assert result["gate_passed"] is True
    assert result["product_complete"] is False


@pytest.mark.unit
def test_v0b_behavior_failure_is_not_hidden_by_runtime_compatibility() -> None:
    selected = json.loads(V0B_COMPATIBLE_RECEIPT_PATH.read_text(encoding="utf-8"))
    report = _v0b_report()
    report["status"] = "fail"
    report["error_count"] = 1
    report["errors"] = [
        {"code": "VISUAL_SIMILARITY", "subject": "capture:V0R-MU-001"}
    ]
    assert (
        _inherit_v0b_runtime_compatibility(
            report,
            selected_receipt=selected,
            selected_path=V0B_COMPATIBLE_RECEIPT_PATH,
        )
        == report
    )


@pytest.mark.unit
def test_exact_v0b_current_ui_build_drift_is_recognized() -> None:
    selected = json.loads(V0B_COMPATIBLE_RECEIPT_PATH.read_text(encoding="utf-8"))
    report = _v0b_capture_product_build_report(selected)
    assert _is_exact_v0b_capture_product_build_drift(report, selected) is True


@pytest.mark.unit
def test_exact_v0b_current_ui_build_drift_inherits_complete_audit_shape() -> None:
    selected = json.loads(V0B_COMPATIBLE_RECEIPT_PATH.read_text(encoding="utf-8"))
    report = _v0b_capture_product_build_report(selected)
    result = _inherit_v0b_runtime_compatibility(
        report,
        selected_receipt=selected,
        selected_path=V0B_COMPATIBLE_RECEIPT_PATH,
    )
    assert result["status"] == "pass"
    assert result["error_count"] == 0
    assert result["errors"] == []
    assert result["gate_passed"] is True
    assert result["receipt_id"] == selected["receipt_id"]
    assert result["core_test_count"] == 21
    assert result["performance_run_count"] == 2
    assert result["visual_similarity_pass_count"] == 36
    assert result["accessibility_pass_count"] == 36
    assert result["runtime_compatibility_required"] is False


@pytest.mark.unit
def test_v0b_current_ui_build_drift_with_any_extra_failure_is_rejected() -> None:
    selected = json.loads(V0B_COMPATIBLE_RECEIPT_PATH.read_text(encoding="utf-8"))
    report = _v0b_capture_product_build_report(selected)
    report["errors"].append(  # type: ignore[union-attr]
        {"code": "VISUAL_SIMILARITY", "subject": "capture:V0R-MU-001"}
    )
    report["error_count"] = 2
    assert _is_exact_v0b_capture_product_build_drift(report, selected) is False


@pytest.mark.unit
def test_v0b_tampered_runtime_compatibility_is_rejected(tmp_path: Path) -> None:
    selected = json.loads(V0B_COMPATIBLE_RECEIPT_PATH.read_text(encoding="utf-8"))
    value = json.loads(V0B_RUNTIME_COMPATIBILITY_PATH.read_text(encoding="utf-8"))
    value["claims"]["ui_source_changed"] = True
    path = tmp_path / "tampered-v0b-runtime.json"
    _write_record(path, value)
    report = _v0b_report()
    assert (
        _inherit_v0b_runtime_compatibility(
            report,
            selected_receipt=selected,
            selected_path=V0B_COMPATIBLE_RECEIPT_PATH,
            compatibility_path=path,
        )
        == report
    )
