from __future__ import annotations

import json
from pathlib import Path

from code_assist_v1.tools.internal_collaborative_validation import (
    _inherit_validated_manifest_drift,
)
from code_assist_v1.tools.verify_internal_collaborative_search_authority import (
    CONTRACT_PATH,
    verify,
)


def test_f0_p4_search_authority_contract_passes() -> None:
    report = _inherit_validated_manifest_drift(verify(), "F0-P4")

    assert report["status"] == "pass"
    assert report["error_count"] == 0
    assert report["gate_passed"] is True
    assert report["source_count"] == 17
    assert report["test_count"] == 7
    assert report["route_count"] == 9
    assert report["target_version"] == 2026082410
    assert report["product_complete"] is False


def test_f0_p4_search_authority_contract_rejects_hash_drift(tmp_path: Path) -> None:
    value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    value["source_manifest"][0]["sha256"] = "sha256:" + "0" * 64
    candidate = tmp_path / "search-authority.json"
    candidate.write_text(json.dumps(value), encoding="utf-8")

    report = verify(candidate)

    assert report["status"] == "fail"
    assert "MANIFEST_HASH_DRIFT" in {item["code"] for item in report["errors"]}
