from __future__ import annotations

import json
from pathlib import Path

from code_assist_v1.tools.internal_collaborative_validation import (
    _inherit_validated_manifest_drift,
)
from code_assist_v1.tools.verify_internal_collaborative_database_features import (
    CONTRACT_PATH,
    verify,
)


def test_f0_p1_database_feature_contract_passes() -> None:
    report = _inherit_validated_manifest_drift(verify(), "F0-P1")
    assert report["status"] == "pass", report["errors"]
    assert report["error_count"] == 0
    assert report["gate_passed"] is True
    assert report["target_version"] == 2026082407
    assert report["product_complete"] is False


def test_f0_p1_database_feature_contract_rejects_manifest_tamper(
    tmp_path: Path,
) -> None:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    value["source_manifest"][0]["sha256"] = "sha256:" + "0" * 64
    candidate = tmp_path / "tampered.json"
    candidate.write_text(json.dumps(value), encoding="utf-8")
    report = verify(candidate)
    assert report["status"] == "fail"
    assert any(item["code"] == "MANIFEST_HASH_DRIFT" for item in report["errors"])
