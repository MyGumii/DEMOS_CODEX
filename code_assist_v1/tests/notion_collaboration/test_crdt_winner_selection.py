from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.notion_parity.crdt_winner_selection import (
    ALLOWED_SPDX,
    DEFAULT_MANIFEST,
    DEFAULT_PACKAGE_JSON,
    DEFAULT_PACKAGE_LOCK,
    DEFAULT_PYTHON_LOCK,
    PROJECT_ROOT,
    SELECTED_CANDIDATE_ID,
    WinnerSelectionError,
    parse_python_lock,
    validate_global_runtime_lock,
    validate_checked_in_selection,
)
from code_assist_v1.tools.verify_internal_collaborative_e0_runtime_lock_successor import (
    CURRENT_RUNTIME_LOCK_SHA256,
)


ADR = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "internal_collaborative_crdt_winner_adr.v1.json"
)


@pytest.mark.unit
def test_e0_p4_selects_only_the_candidate_that_passes_every_frozen_gate() -> None:
    report = validate_checked_in_selection()
    assert report["selected_candidate_id"] == SELECTED_CANDIDATE_ID
    assert report["eligible_candidate_ids"] == ["PY_OPAQUE_NODE_COMPACTOR"]
    assert {item["candidate_id"] for item in report["rejected_candidates"]} == {
        "PY_NATIVE_PYCRDT",
        "NODE_YJS_HOCUSPOCUS",
    }
    assert {item["license_spdx"] for item in report["rejected_candidates"]} == {
        "PSF-2.0",
        "0BSD",
    }
    assert all(report["mandatory_gate_evidence"].values())
    assert report["claims"] == {
        "winner_selected": True,
        "dependency_versions_and_integrity_pinned": True,
        "runtime_service_implemented": False,
        "runtime_enabled": False,
        "product_complete": False,
    }


@pytest.mark.unit
def test_e0_p4_python_and_node_locks_are_exact_and_fully_hash_bound() -> None:
    python = parse_python_lock()
    assert {item["name"] for item in python} == {
        "click",
        "colorama",
        "h11",
        "uvicorn",
        "websockets",
    }
    assert all(len(item["digest"]) == 64 for item in python)
    package_json = json.loads(DEFAULT_PACKAGE_JSON.read_text(encoding="utf-8"))
    package_lock = json.loads(DEFAULT_PACKAGE_LOCK.read_text(encoding="utf-8"))
    assert package_json["private"] is True
    assert package_json["dependencies"] == {"yjs": "13.6.32"}
    assert package_lock["lockfileVersion"] == 3
    assert set(package_lock["packages"]) == {
        "",
        "node_modules/isomorphic.js",
        "node_modules/lib0",
        "node_modules/yjs",
    }
    assert all(
        item.get("integrity", "").startswith("sha512-")
        for path, item in package_lock["packages"].items()
        if path
    )


@pytest.mark.unit
def test_e0_p4_selected_transitive_graph_stays_within_frozen_license_policy() -> None:
    manifest = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    selected = manifest["python_lock"]["packages"] + manifest["node_lock"]["packages"]
    assert len(selected) == 8
    assert {item["license_spdx"] for item in selected}.issubset(set(ALLOWED_SPDX))
    assert manifest["license_policy"]["disallowed_selected_package_count"] == 0
    assert manifest["global_runtime_lock"]["modified"] is False
    successor = validate_global_runtime_lock()
    assert successor["status"] == "pass"
    assert successor["current_runtime_lock_sha256"] == CURRENT_RUNTIME_LOCK_SHA256
    assert successor["forbidden_global_package_count"] == 0


@pytest.mark.unit
def test_e0_p4_python_lock_rejects_missing_hash_and_duplicate_denominator(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.lock"
    invalid.write_text("uvicorn==0.52.4\n", encoding="utf-8")
    with pytest.raises(WinnerSelectionError, match="non-hash-pinned"):
        parse_python_lock(invalid)

    duplicate = tmp_path / "duplicate.lock"
    row = "uvicorn==0.52.4 --hash=sha256:" + "0" * 64
    duplicate.write_text("\n".join([row] * 5), encoding="utf-8")
    with pytest.raises(WinnerSelectionError, match="denominator"):
        parse_python_lock(duplicate)


@pytest.mark.unit
def test_e0_p4_adr_is_receipt_bound_and_does_not_claim_runtime_completion() -> None:
    adr = json.loads(ADR.read_text(encoding="utf-8"))
    assert adr["decision"]["selected_candidate_id"] == SELECTED_CANDIDATE_ID
    assert adr["authority_inputs"]["e0_p4_execution_receipt"]["sha256"] == (
        "sha256:c358230bc289d8b1c2244ec46b8b5ebe939fd051fa0b4a595625044f7b0d490a"
    )
    assert adr["pinned_runtime"]["global_application_runtime_lock_modified"] is False
    assert adr["claims"]["actual_gateway_transport_implemented"] is False
    assert adr["claims"]["actual_compactor_service_implemented"] is False
    assert adr["claims"]["runtime_enabled"] is False
    assert adr["claims"]["product_complete"] is False
    assert hashlib.sha256(DEFAULT_PYTHON_LOCK.read_bytes()).hexdigest() == (
        adr["pinned_runtime"]["python_lock_sha256"].removeprefix("sha256:")
    )
