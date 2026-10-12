from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.notion_parity.crdt_runtime_spike import (
    APPROVAL_CONTRACT,
    APPROVAL_ID,
    ApprovalRequired,
    DEFAULT_MATRIX,
    EXPECTED_CANDIDATES,
    EXPECTED_PACKAGES,
    FORBIDDEN_EFFECTS,
    MATRIX_SHA256,
    SpikeContractError,
    approval_request_descriptor,
    build_execution_manifest,
    load_candidate_matrix,
)


def _approved_receipt(scope_sha256: str) -> dict[str, object]:
    return {
        "contract": APPROVAL_CONTRACT,
        "approval_id": APPROVAL_ID,
        "status": "approved",
        "approved_by": "user",
        "approval_source": "explicit_conversation",
        "approved_at": "2026-08-24T00:00:00Z",
        "scope_sha256": scope_sha256,
        "matrix_sha256": MATRIX_SHA256,
        "candidate_ids": sorted(EXPECTED_CANDIDATES),
        "package_ids": sorted(EXPECTED_PACKAGES),
        "single_use": True,
    }


@pytest.mark.unit
def test_e0_p2_preflight_binds_exact_frozen_e0_p1_matrix() -> None:
    matrix = load_candidate_matrix()
    request = approval_request_descriptor(matrix)

    assert request["approval_id"] == "AP-RUNTIME-SPIKE"
    assert request["status"] == "pending"
    assert request["explicit_user_approval_observed"] is False
    assert request["network_install_or_execution_allowed"] is False
    assert request["scope"]["candidate_ids"] == sorted(EXPECTED_CANDIDATES)
    assert [item["package_id"] for item in request["scope"]["packages"]] == sorted(
        EXPECTED_PACKAGES
    )
    assert request["scope"]["forbidden_effects"] == list(FORBIDDEN_EFFECTS)
    assert request["scope_sha256"].startswith("sha256:")
    assert len(request["scope_sha256"]) == 71


@pytest.mark.unit
def test_e0_p2_manifest_fails_closed_without_explicit_approval() -> None:
    matrix = load_candidate_matrix()
    with pytest.raises(ApprovalRequired, match="explicit user approval"):
        build_execution_manifest(matrix, None)


@pytest.mark.unit
def test_e0_p2_valid_approval_builds_authorized_but_unexecuted_manifest() -> None:
    matrix = load_candidate_matrix()
    request = approval_request_descriptor(matrix)
    manifest = build_execution_manifest(
        matrix,
        _approved_receipt(request["scope_sha256"]),
    )

    assert manifest["status"] == "authorized_not_executed"
    assert [item["candidate_id"] for item in manifest["candidate_plans"]] == sorted(
        EXPECTED_CANDIDATES
    )
    assert all(item["persistent_service"] is False for item in manifest["candidate_plans"])
    assert all(item["fixed_port"] is False for item in manifest["candidate_plans"])
    assert all(item["candidate_selected"] is False for item in manifest["candidate_plans"])
    assert all(value is False for value in manifest["claims"].values())
    assert manifest["forbidden_effects"] == list(FORBIDDEN_EFFECTS)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("key", "replacement"),
    [
        ("approval_id", "AP-OTHER"),
        ("status", "pending"),
        ("approved_by", "agent"),
        ("approval_source", "inferred"),
        ("approved_at", "2026-08-24"),
        ("scope_sha256", "sha256:" + ("0" * 64)),
        ("matrix_sha256", "sha256:" + ("0" * 64)),
        ("candidate_ids", ["PY_NATIVE_PYCRDT"]),
        ("package_ids", ["pycrdt"]),
        ("single_use", False),
    ],
)
def test_e0_p2_approval_tamper_is_rejected(key: str, replacement: object) -> None:
    matrix = load_candidate_matrix()
    request = approval_request_descriptor(matrix)
    receipt = _approved_receipt(request["scope_sha256"])
    receipt[key] = replacement

    with pytest.raises(ApprovalRequired):
        build_execution_manifest(matrix, receipt)


@pytest.mark.unit
def test_e0_p2_approval_receipt_requires_exact_fields() -> None:
    matrix = load_candidate_matrix()
    request = approval_request_descriptor(matrix)
    receipt = _approved_receipt(request["scope_sha256"])
    receipt["unreviewed_extra_authority"] = True
    with pytest.raises(ApprovalRequired, match="exact"):
        build_execution_manifest(matrix, receipt)

    receipt = _approved_receipt(request["scope_sha256"])
    del receipt["approved_at"]
    with pytest.raises(ApprovalRequired, match="exact"):
        build_execution_manifest(matrix, receipt)


@pytest.mark.unit
def test_e0_p2_matrix_hash_duplicate_and_nonfinite_drift_fail_closed(
    tmp_path: Path,
) -> None:
    original = DEFAULT_MATRIX.read_text(encoding="utf-8")
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"packet_id":"E0-P1","packet_id":"E0-P1"}', encoding="utf-8")
    duplicate_hash = "sha256:" + hashlib.sha256(duplicate.read_bytes()).hexdigest()
    with pytest.raises(SpikeContractError, match="duplicate JSON key"):
        load_candidate_matrix(duplicate, expected_sha256=duplicate_hash)

    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"packet_id":"E0-P1","value":NaN}', encoding="utf-8")
    nonfinite_hash = "sha256:" + hashlib.sha256(nonfinite.read_bytes()).hexdigest()
    with pytest.raises(SpikeContractError, match="non-finite"):
        load_candidate_matrix(nonfinite, expected_sha256=nonfinite_hash)

    drift = tmp_path / "drift.json"
    drift.write_text(original + "\n", encoding="utf-8")
    with pytest.raises(SpikeContractError, match="SHA-256 drift"):
        load_candidate_matrix(drift)


@pytest.mark.unit
def test_e0_p2_candidate_selection_or_boundary_drift_fails_closed(tmp_path: Path) -> None:
    matrix = copy.deepcopy(load_candidate_matrix())
    matrix["candidates"][0]["eligibility"] = "selected"
    matrix["spike_boundary"]["production_lockfile_modification"] = True
    matrix["claims"]["candidate_winner_selected"] = True
    path = tmp_path / "false-green.json"
    path.write_text(json.dumps(matrix, ensure_ascii=False), encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(SpikeContractError):
        load_candidate_matrix(path, expected_sha256=digest)


@pytest.mark.unit
def test_e0_p2_preflight_builders_have_no_filesystem_or_execution_side_effects(
    tmp_path: Path,
) -> None:
    before = tuple(tmp_path.iterdir())
    matrix = load_candidate_matrix()
    request = approval_request_descriptor(matrix)
    build_execution_manifest(matrix, _approved_receipt(request["scope_sha256"]))
    assert tuple(tmp_path.iterdir()) == before

    source = Path(
        "code_assist_v1/tools/notion_parity/crdt_runtime_spike.py"
    ).read_text(encoding="utf-8")
    for forbidden_import in (
        "import os",
        "import shutil",
        "import socket",
        "import subprocess",
        "import tempfile",
        "import urllib",
        "import venv",
        "from pip",
        "import requests",
    ):
        assert forbidden_import not in source
