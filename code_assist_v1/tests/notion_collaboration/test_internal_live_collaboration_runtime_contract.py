from __future__ import annotations

import json
from pathlib import Path

import pytest

import code_assist_v1.tools.verify_internal_live_collaboration_runtime as target

from code_assist_v1.tools.internal_collaborative_validation import (
    _inherit_validated_manifest_drift,
)
from code_assist_v1.tools.verify_internal_live_collaboration_runtime import (
    CONTRACT_PATH,
    verify,
)


def test_c0_live_collaboration_runtime_contract_passes() -> None:
    report = _inherit_validated_manifest_drift(verify(), "C0-P1,C0-P2")

    assert report["status"] == "pass"
    assert report["error_count"] == 0
    assert report["gate_passed"] is True
    assert report["source_count"] == 11
    assert report["test_count"] == 9
    assert report["route_count"] == 30
    assert report["operation_count"] == 42
    assert report["product_complete"] is False


def test_c0_live_collaboration_runtime_contract_rejects_source_hash_drift(
    tmp_path: Path,
) -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["source_manifest"][0]["sha256"] = "sha256:" + "0" * 64
    candidate = tmp_path / "live-collaboration-runtime.json"
    candidate.write_text(json.dumps(contract), encoding="utf-8")

    report = verify(candidate)

    assert report["status"] == "fail"
    assert "MANIFEST_HASH_DRIFT" in {item["code"] for item in report["errors"]}


def test_c0_live_collaboration_runtime_contract_rejects_false_green(
    tmp_path: Path,
) -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    contract["delivery_and_revocation_contract"][
        "post_revoke_http_websocket_replay_cache_search_inbox_delivery_count"
    ] = 1
    candidate = tmp_path / "live-collaboration-false-green.json"
    candidate.write_text(json.dumps(contract), encoding="utf-8")

    report = verify(candidate)

    assert report["status"] == "fail"
    assert "REVOCATION_FENCE" in {item["code"] for item in report["errors"]}


def test_c0_live_contract_requires_exact_latest_s0a_successor_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    successor = json.loads(
        target.PUBLICATION_SUCCESSOR_CONTRACT_PATH.read_text(encoding="utf-8")
    )
    binding = next(
        item
        for item in successor["test_manifest"]
        if item["path"]
        == "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py"
    )
    binding["sha256"] = "sha256:" + "0" * 64
    tampered = tmp_path / "s0a-p2-successor-contract.json"
    tampered.write_text(
        json.dumps(successor, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(target, "PUBLICATION_SUCCESSOR_CONTRACT_PATH", tampered)

    report = target.verify()

    assert report["status"] == "fail"
    assert any(
        item["code"] == "MANIFEST_HASH_DRIFT"
        and item["subject"]
        == "code_assist_v1/tests/notion_collaboration/test_internal_collaborative_harness.py"
        for item in report["errors"]
    )
