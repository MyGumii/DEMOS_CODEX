from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_assist_v1.tools.internal_collaborative_validation import (
    _inherit_validated_manifest_drift,
)
from code_assist_v1.tools.verify_internal_collaborative_adopted_surface import (
    CONTRACT_PATH,
    verify,
)


@pytest.mark.unit
def test_f0_p5_adopted_surface_contract_passes_fail_closed_verifier() -> None:
    report = _inherit_validated_manifest_drift(verify(), "F0-P5")
    assert report["status"] == "pass", report
    assert report["gate_passed"] is True
    assert report["adopted_feature_count"] == 15
    assert report["product_complete"] is False


@pytest.mark.unit
def test_f0_p5_adopted_surface_contract_rejects_false_completion(
    tmp_path: Path,
) -> None:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    value["claims"]["product_complete"] = True
    tampered = tmp_path / "tampered-adopted-surface.json"
    tampered.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    report = verify(tampered)
    assert report["status"] == "fail"
    assert report["gate_passed"] is False
    assert any(error["code"] == "CLAIMS_DRIFT" for error in report["errors"])
