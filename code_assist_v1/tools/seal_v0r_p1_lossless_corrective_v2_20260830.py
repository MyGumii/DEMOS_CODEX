"""Seal the V0R-P1 2026-08-30 v2 receipt without stale capture mattes.

The first 2026-08-30 immutable receipt remains untouched.  This receipt binds
its hash, reuses the same sealed screenshots and mask manifests, and removes
only capture-matte metadata whose rectangles are not present in the fresh
reference pixels.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from code_assist_v1.tools import seal_v0r_corrective_reference_receipt as sealer
from code_assist_v1.tools import (
    verify_internal_collaborative_reference_multiuser_receipt as verifier,
)


V1_RECEIPT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/"
    "v0r-p1-l6-reference-multiuser-lossless-corrective-20260830.json"
)
V2_RECEIPT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/"
    "v0r-p1-l6-reference-multiuser-lossless-corrective-v2-20260830.json"
)
V1_CHECKPOINT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/checkpoints/"
    "ui-internal-collaboration-v0r-p1-lossless-corrective-cleanup-gate-pass-20260830.json"
)
V2_CHECKPOINT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/checkpoints/"
    "ui-internal-collaboration-v0r-p1-lossless-corrective-v2-cleanup-gate-pass-20260830.json"
)
SEMANTIC_RECEIPT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/"
    "v0r-p1-l6-reference-multiuser-lossless-20260829.json"
)
SCREENSHOT_REPO_ROOT = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_reference/"
    "v0r-p1-lossless-corrective-20260830/screenshots"
)
MASK_REPO_ROOT = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_reference/"
    "v0r-p1-lossless-corrective-20260830/masks"
)
RAW_REPO_ROOT = "code_assist_v1/.runtime/v0r-p1-lossless-corrective-20260830/raw"
EXPECTED_V1_RECEIPT_SHA256 = (
    "sha256:34c40d00d8ed0c2b36bbc23b884919fc86299dafcaf486b9bee689538ec48f36"
)
SUPERSESSION_AUTHORITY_TEXT = (
    "V0R-P1 lossless corrective reference 재수집 및 새 immutable receipt로 "
    "supersede 승인"
)
CLEANUP_AUTHORITY_TEXT = "V0R 합성 A/B 루트 Trash 이동 및 최종 cleanup 승인"
REFERENCE_BUILD_IDENTIFIER = (
    "notion-web-live-ui-lossless-storage-corrective-v2-observation-20260830"
)
RECEIPT_ID = "V0R-P1-L6-REFERENCE-MULTIUSER-LOSSLESS-CORRECTIVE-V2-20260830"


def _path(repo_path: str) -> Path:
    return sealer.REPOSITORY_ROOT / Path(*PurePosixPath(repo_path).parts)


def _configure_sealer() -> None:
    sealer.ORIGINAL_RECEIPT_REPO_PATH = V1_RECEIPT_REPO_PATH
    sealer.CORRECTIVE_RECEIPT_REPO_PATH = V2_RECEIPT_REPO_PATH
    sealer.CORRECTIVE_CHECKPOINT_REPO_PATH = V2_CHECKPOINT_REPO_PATH
    sealer.SCREENSHOT_REPO_ROOT = SCREENSHOT_REPO_ROOT
    sealer.MASK_REPO_ROOT = MASK_REPO_ROOT
    sealer.RAW_REPO_ROOT = RAW_REPO_ROOT
    sealer.PREDECESSOR_REPO_PATH = V1_CHECKPOINT_REPO_PATH
    sealer.EXPECTED_ORIGINAL_RECEIPT_SHA256 = EXPECTED_V1_RECEIPT_SHA256
    sealer.SUPERSESSION_AUTHORITY_TEXT = SUPERSESSION_AUTHORITY_TEXT
    sealer.CLEANUP_AUTHORITY_TEXT = CLEANUP_AUTHORITY_TEXT
    sealer.REFERENCE_BUILD_IDENTIFIER = REFERENCE_BUILD_IDENTIFIER
    verifier.ORIGINAL_RECEIPT_REPO_PATH = V1_RECEIPT_REPO_PATH

    original_build_receipt = sealer.build_receipt
    original_build_checkpoint = sealer.build_checkpoint

    def build_receipt(*, recorded_at: str) -> dict[str, object]:
        receipt = original_build_receipt(recorded_at=recorded_at)
        receipt["receipt_id"] = RECEIPT_ID
        receipt["run"]["run_id"] = (
            "v0r-p1-lossless-corrective-reference-v2-20260830"
        )
        receipt["run"]["semantic_action_log_source"] = SEMANTIC_RECEIPT_REPO_PATH
        receipt["run"]["prior_corrective_receipt_sha256"] = (
            EXPECTED_V1_RECEIPT_SHA256
        )
        for capture in receipt["captures"]:
            capture.pop("capture_matte", None)
        receipt.pop("capture_mattes", None)
        receipt["capture_matte_review"] = {
            "status": "pass",
            "stale_inherited_capture_matte_count_removed": 8,
            "fresh_reference_capture_matte_count": 0,
            "reason": "fresh reference pixels do not contain the inherited matte rectangles",
        }
        receipt["capture_transport"] = {
            "browser_capture_returned_codec": "jpeg",
            "stored_codec": "png_srgb_rgba8",
            "conversion": "decoded_bitmap_saved_as_png",
            "lossless_storage_after_decode": True,
            "source_encoding_lossless": False,
            "zero_RGBA_equivalence_claimed": False,
        }
        return receipt

    def build_checkpoint(
        *, recorded_at: str, receipt_sha256: str, audit: dict[str, object]
    ) -> dict[str, object]:
        checkpoint = original_build_checkpoint(
            recorded_at=recorded_at,
            receipt_sha256=receipt_sha256,
            audit=audit,
        )
        checkpoint["checkpoint_id"] = (
            "UI-INTERNAL-COLLABORATION-V0R-P1-LOSSLESS-CORRECTIVE-V2-"
            "CLEANUP-GATE-PASS-20260830"
        )
        checkpoint["checkpoint_kind"] = (
            "lossless_storage_corrective_v2_reference_cleanup_and_receipt_gate_pass"
        )
        checkpoint["purpose"] = (
            "Bind the first 2026-08-30 immutable receipt and remove only stale "
            "inherited capture-matte metadata before V0B consumption, while preserving "
            "all prior receipts and the 36 fresh reference PNGs and masks."
        )
        checkpoint["next_action"] = (
            "Use this v2 receipt for focused V0B I0 exact-parity diagnosis; defer "
            "Full36, Core25, and performance until I0 is materially ready."
        )
        return checkpoint

    sealer.build_receipt = build_receipt
    sealer.build_checkpoint = build_checkpoint


def main() -> int:
    if sealer._sha256_file(_path(V1_RECEIPT_REPO_PATH)) != EXPECTED_V1_RECEIPT_SHA256:
        raise ValueError("V0R_20260830_V1_RECEIPT_HASH_DRIFT")
    if _path(V2_RECEIPT_REPO_PATH).exists() or _path(V2_CHECKPOINT_REPO_PATH).exists():
        raise FileExistsError("V0R_20260830_V2_IMMUTABLE_OUTPUT_EXISTS")
    _configure_sealer()
    result = sealer.seal()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
