from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_assist_v1.tools import seal_v0r_corrective_reference_receipt as sealer
from code_assist_v1.tools import seal_v0r_p1_lossless_exact_v3_20260830 as v3
from code_assist_v1.tools import (
    verify_internal_collaborative_reference_multiuser_receipt as receipt_verifier,
)
from code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_execution import (
    EXPECTED_STATES,
)


def _semantic_manifest() -> dict[str, object]:
    captures = []
    for state_id, _, _, _, expected_state in EXPECTED_STATES:
        for width, height in ((1280, 800), (1440, 900)):
            captures.append(
                {
                    "state_id": state_id,
                    "viewport": {"width": width, "height": height},
                    "expected_state": expected_state,
                    "semantic_postcondition_status": "verified_after_transition",
                    "capture_after_postcondition": True,
                    "captured_at": "2026-08-30T18:00:00+09:00",
                    "semantic_observation": (
                        f"{state_id} reached {expected_state} before capture."
                    ),
                }
            )
    return {"capture_surface": "approved-lossless-surface", "captures": captures}


@pytest.mark.unit
def test_v0r_v3_semantic_review_requires_corrected_transition_timing() -> None:
    manifest = _semantic_manifest()

    result = v3._validate_semantic_postconditions(manifest)

    assert result["status"] == "pass"
    assert result["capture_count"] == 36
    assert result["corrected_timing_states"] == {
        "V0R-MU-004": "edit_access_active",
        "V0R-MU-006": "inherited_workspace_access_explained",
    }


@pytest.mark.unit
@pytest.mark.parametrize("state_id", ["V0R-MU-004", "V0R-MU-006"])
def test_v0r_v3_semantic_review_rejects_pretransition_capture(
    state_id: str,
) -> None:
    manifest = _semantic_manifest()
    capture = next(row for row in manifest["captures"] if row["state_id"] == state_id)
    capture["capture_after_postcondition"] = False

    with pytest.raises(ValueError, match="SEMANTIC_POSTCONDITION_NOT_VERIFIED"):
        v3._validate_semantic_postconditions(manifest)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "error"),
    [
        ("captured_at", "CAPTURE_TIME_INVALID"),
        ("semantic_observation", "SEMANTIC_OBSERVATION_REQUIRED"),
    ],
)
def test_v0r_v3_semantic_review_requires_capture_provenance(
    field: str, error: str
) -> None:
    manifest = _semantic_manifest()
    manifest["captures"][0][field] = ""

    with pytest.raises(ValueError, match=error):
        v3._validate_semantic_postconditions(manifest)


@pytest.mark.unit
def test_v0r_v3_ingest_audit_is_exclusive_and_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    screenshot_root = tmp_path / "screenshots"
    screenshot_root.mkdir()
    audit_path = tmp_path / "audit.json"
    expected = {
        "contract": "audit",
        "status": "pass",
        "error_count": 0,
        "exact_reference_transport_eligible": True,
        "capture_manifest_sha256": "sha256:" + "a" * 64,
    }
    monkeypatch.setattr(v3.ingest, "verify", lambda *args, **kwargs: expected)

    first, first_hash, manifest_hash = v3._load_or_create_ingest_audit(
        manifest_path=manifest_path,
        screenshot_root=screenshot_root,
        audit_path=audit_path,
    )
    second, second_hash, second_manifest_hash = v3._load_or_create_ingest_audit(
        manifest_path=manifest_path,
        screenshot_root=screenshot_root,
        audit_path=audit_path,
    )

    assert first == second == expected
    assert first_hash == second_hash
    assert manifest_hash == second_manifest_hash
    audit_path.write_text('{"status":"tampered"}', encoding="utf-8")
    with pytest.raises(ValueError, match="IMMUTABLE_INGEST_AUDIT_DRIFT"):
        v3._load_or_create_ingest_audit(
            manifest_path=manifest_path,
            screenshot_root=screenshot_root,
            audit_path=audit_path,
        )


@pytest.mark.unit
def test_v0r_v3_receipt_decorator_binds_exact_ingest_audit() -> None:
    manifest = _semantic_manifest()
    receipt = {
        "run": {},
        "corrective_evidence": {},
        "captures": [
            {
                "state_id": row["state_id"],
                "viewport": row["viewport"],
                "interaction_outcome": {},
            }
            for row in manifest["captures"]
        ],
    }
    audit = {"capture_manifest_sha256": "sha256:" + "a" * 64}
    semantic_review = v3._validate_semantic_postconditions(manifest)

    result = v3._decorate_receipt(
        receipt,
        manifest=manifest,
        semantic_review=semantic_review,
        audit=audit,
        audit_sha256="sha256:" + "b" * 64,
        manifest_file_sha256="sha256:" + "c" * 64,
    )

    assert result["receipt_id"] == v3.RECEIPT_ID
    assert result["capture_transport"]["source_encoding_lossless"] is True
    assert result["capture_transport"]["zero_RGBA_equivalence_claimed"] is True
    assert result["capture_transport"]["lossless_ingest_audit"] == {
        "relative_path": Path(v3.V3_INGEST_AUDIT_REPO_PATH).name,
        "sha256": "sha256:" + "b" * 64,
        "capture_manifest_sha256": "sha256:" + "a" * 64,
    }
    capture_004 = next(
        capture
        for capture in result["captures"]
        if capture["state_id"] == "V0R-MU-004"
    )
    assert capture_004["captured_at"] == "2026-08-30T18:00:00+09:00"
    assert capture_004["capture_timestamp_basis"] == (
        "source_lossless_capture_manifest"
    )
    assert capture_004["interaction_outcome"]["expected_state"] == (
        "edit_access_active"
    )
    assert result["run"]["started_at"] == "2026-08-30T18:00:00+09:00"


@pytest.mark.unit
def test_common_sealer_exact_mode_rejects_semantic_only_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sealer, "REQUIRE_EXACT_REFERENCE_GATE", True)

    with pytest.raises(ValueError, match="EXACT_REFERENCE_GATE_FAILED"):
        sealer._require_audit_gates(
            {"status": "pass", "exact_reference_gate_passed": False},
            stage="PRESEAL",
        )
    sealer._require_audit_gates(
        {"status": "pass", "exact_reference_gate_passed": True},
        stage="PRESEAL",
    )


@pytest.mark.unit
def test_common_sealer_exact_preseal_candidate_is_audit_sibling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt_path = tmp_path / "receipts" / "receipt.json"
    runtime_root = tmp_path / "runtime"

    monkeypatch.setattr(sealer, "REQUIRE_EXACT_REFERENCE_GATE", True)
    assert (
        sealer._preseal_temporary_root(receipt_path, runtime_root)
        == receipt_path.parent
    )
    monkeypatch.setattr(sealer, "REQUIRE_EXACT_REFERENCE_GATE", False)
    assert sealer._preseal_temporary_root(receipt_path, runtime_root) == runtime_root


@pytest.mark.unit
def test_v0r_v3_configures_v2_as_predecessor_but_keeps_semantic_chain_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        receipt_verifier,
        "ORIGINAL_RECEIPT_REPO_PATH",
        "drifted-before-configure.json",
    )

    v3._configure_sealer(
        manifest={},
        semantic_review={},
        ingest_audit={},
        audit_sha256="sha256:" + "a" * 64,
        manifest_file_sha256="sha256:" + "b" * 64,
    )

    assert sealer.ORIGINAL_RECEIPT_REPO_PATH == v3.V2_RECEIPT_REPO_PATH
    assert (
        receipt_verifier.ORIGINAL_RECEIPT_REPO_PATH
        == v3.SEMANTIC_ROOT_RECEIPT_REPO_PATH
    )


@pytest.mark.unit
def test_v0r_v3_output_paths_are_new_and_do_not_alias_v2() -> None:
    assert v3.V3_RECEIPT_REPO_PATH != v3.V2_RECEIPT_REPO_PATH
    assert v3.V3_INGEST_MANIFEST_REPO_PATH != v3.V3_INGEST_AUDIT_REPO_PATH
    assert v3.SCREENSHOT_REPO_ROOT != v3.MASK_REPO_ROOT
