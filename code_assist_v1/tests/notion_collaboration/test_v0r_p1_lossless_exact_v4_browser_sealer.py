from __future__ import annotations

import pytest

from code_assist_v1.tools import seal_v0r_corrective_reference_receipt as sealer
from code_assist_v1.tools import (
    seal_v0r_p1_lossless_exact_v3_20260830 as v3,
)
from code_assist_v1.tools import (
    seal_v0r_p1_lossless_exact_v4_browser_20260830 as v4,
)
from code_assist_v1.tools import (
    verify_internal_collaborative_reference_multiuser_receipt as receipt_verifier,
)


@pytest.mark.unit
def test_v0r_v4_corrects_only_browser_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sealer_names = (
        "ORIGINAL_RECEIPT_REPO_PATH",
        "CORRECTIVE_RECEIPT_REPO_PATH",
        "CORRECTIVE_CHECKPOINT_REPO_PATH",
        "SCREENSHOT_REPO_ROOT",
        "MASK_REPO_ROOT",
        "RAW_REPO_ROOT",
        "PREDECESSOR_REPO_PATH",
        "EXPECTED_ORIGINAL_RECEIPT_SHA256",
        "SUPERSESSION_AUTHORITY_TEXT",
        "CLEANUP_AUTHORITY_TEXT",
        "REFERENCE_BUILD_IDENTIFIER",
        "REQUIRE_EXACT_REFERENCE_GATE",
        "build_receipt",
        "build_checkpoint",
    )
    original_sealer = {name: getattr(sealer, name) for name in sealer_names}
    original_verifier_root = receipt_verifier.ORIGINAL_RECEIPT_REPO_PATH
    monkeypatch.setattr(
        sealer,
        "build_receipt",
        lambda *, recorded_at: {
            "receipt_id": "prior",
            "run": {},
            "corrective_evidence": {},
            "captures": [
                {
                    "environment": {
                        "browser": {
                            "engine": "chromium",
                            "channel": "chrome",
                            "major_version": "151",
                        }
                    },
                    "environment_sha256": "sha256:" + "0" * 64,
                }
            ],
        },
    )
    monkeypatch.setattr(
        sealer,
        "build_checkpoint",
        lambda **kwargs: {
            "checkpoint_id": "prior",
            "supersession": {},
            "final_receipt": {},
            "claims": {},
        },
    )
    monkeypatch.setattr(v3, "_decorate_receipt", lambda receipt, **kwargs: receipt)
    monkeypatch.setattr(
        sealer,
        "validate_capture_environment",
        lambda environment: {
            **environment,
            "environment_sha256": "sha256:" + "c" * 64,
        },
    )

    try:
        v4._configure_sealer(
            manifest={},
            semantic_review={},
            ingest_audit={},
            audit_sha256="sha256:" + "a" * 64,
            manifest_file_sha256="sha256:" + "b" * 64,
        )
        receipt = sealer.build_receipt(recorded_at="2026-08-30T23:00:00+09:00")

        assert sealer.ORIGINAL_RECEIPT_REPO_PATH == v4.V3_RECEIPT_REPO_PATH
        assert receipt["receipt_id"] == v4.RECEIPT_ID
        assert receipt["run"]["chrome_major_version"] == "153"
        assert receipt["run"]["chrome_version"] == "153.0.8010.12"
        assert (
            receipt["captures"][0]["environment"]["browser"]["major_version"]
            == "153"
        )
        assert receipt["captures"][0]["environment_sha256"] == (
            "sha256:" + "c" * 64
        )
        assert receipt["corrective_evidence"]["browser_version_correction"][
            "v3_receipt_modified"
        ] is False
        assert (
            receipt_verifier.ORIGINAL_RECEIPT_REPO_PATH
            == v3.SEMANTIC_ROOT_RECEIPT_REPO_PATH
        )
    finally:
        for name, value in original_sealer.items():
            setattr(sealer, name, value)
        receipt_verifier.ORIGINAL_RECEIPT_REPO_PATH = original_verifier_root
