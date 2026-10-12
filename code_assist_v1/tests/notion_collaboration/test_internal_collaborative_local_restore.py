from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_assist_v1.tools.internal_collaborative_validation import (
    _inherit_validated_manifest_drift,
)
from code_assist_v1.tools.verify_internal_collaborative_local_restore import (
    CONTRACT_PATH,
    verify,
)


@pytest.mark.unit
def test_o1a_p1_local_backup_restore_contract_passes_fail_closed_verifier() -> None:
    report = _inherit_validated_manifest_drift(verify(), "O1A-P1")
    assert report["status"] == "pass", report
    assert report["gate_passed"] is True
    assert report["scenario_count"] == 6
    assert report["full_target_restore_lane_closed"] is False
    assert report["product_complete"] is False


@pytest.mark.unit
def test_o1a_p1_local_backup_restore_contract_rejects_false_lane_completion(
    tmp_path: Path,
) -> None:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    value["claims"]["full_target_restore_lane_closed"] = True
    value["claims"]["target_restore_executed"] = True
    tampered = tmp_path / "tampered-o1a-local-restore.json"
    tampered.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    report = verify(tampered)
    assert report["status"] == "fail"
    assert report["gate_passed"] is False
    assert any(error["code"] == "CLAIMS_DRIFT" for error in report["errors"])
