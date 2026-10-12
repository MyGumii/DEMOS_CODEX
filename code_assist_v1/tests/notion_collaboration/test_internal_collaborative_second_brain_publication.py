from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_assist_v1.tools.internal_collaborative_validation import (
    _inherit_validated_manifest_drift,
)
from code_assist_v1.tools.verify_internal_collaborative_second_brain_publication import (
    CONTRACT_PATH,
    verify,
)


@pytest.mark.unit
def test_checked_in_s0a_p2_publication_contract_passes() -> None:
    report = _inherit_validated_manifest_drift(verify(), "S0A-P2")
    assert report["status"] == "pass", report
    assert report["error_count"] == 0
    assert report["gate_passed"] is True
    assert report["sbe_scenario_count"] == 9
    assert report["source_count"] == 14
    assert report["test_count"] == 6
    assert report["protected_sbe_change_required"] is False
    assert report["product_complete"] is False


@pytest.mark.unit
def test_s0a_p2_publication_contract_fails_closed_on_manifest_drift(
    tmp_path: Path,
) -> None:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    value["source_manifest"][0]["sha256"] = "sha256:" + ("0" * 64)
    target = tmp_path / "tampered.json"
    target.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    report = verify(target)
    assert report["status"] == "fail"
    assert "MANIFEST_HASH_DRIFT" in {
        item["code"] for item in report["errors"]
    }
