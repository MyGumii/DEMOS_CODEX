from __future__ import annotations

import json
from pathlib import Path

from code_assist_v1.tools.verify_internal_collaborative_sharing_runtime import (
    CONTRACT_PATH,
    verify,
)
from code_assist_v1.tools.internal_collaborative_validation import (
    _inherit_validated_manifest_drift,
)


def test_i0_p2_sharing_runtime_contract_passes() -> None:
    report = _inherit_validated_manifest_drift(verify(), "I0-P2")

    assert report["status"] == "pass"
    assert report["error_count"] == 0
    assert report["gate_passed"] is True
    assert report["route_count"] == 30
    assert report["operation_count"] == 42
    assert report["product_complete"] is False


def test_i0_p2_sharing_runtime_contract_rejects_source_hash_drift(
    tmp_path: Path,
) -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["source_manifest"][0]["sha256"] = "sha256:" + "0" * 64
    candidate = tmp_path / "sharing-runtime.json"
    candidate.write_text(json.dumps(contract), encoding="utf-8")

    report = verify(candidate)

    assert report["status"] == "fail"
    assert "MANIFEST_HASH_DRIFT" in {item["code"] for item in report["errors"]}
