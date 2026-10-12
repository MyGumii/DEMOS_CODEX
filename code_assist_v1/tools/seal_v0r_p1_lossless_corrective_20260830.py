"""Seal the fresh 2026-08-30 V0R-P1 corrective reference run.

This keeps the 2026-08-29 receipt byte-for-byte immutable, regenerates the
review manifests for the already captured 36 PNGs, and delegates the contract
audit plus exclusive receipt/checkpoint writes to the established V0R sealer.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath

from code_assist_v1.tools import build_v0r_p1_lossless_receipt as geometry
from code_assist_v1.tools import seal_v0r_corrective_reference_receipt as sealer
from code_assist_v1.tools import (
    verify_internal_collaborative_reference_multiuser_receipt as verifier,
)
from code_assist_v1.tools.notion_parity.exact_pixel import (
    decode_png_srgb_rgba8,
    validate_mask_manifest,
)


OLD_RECEIPT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/"
    "v0r-p1-l6-reference-multiuser-lossless-20260829.json"
)
NEW_RECEIPT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/"
    "v0r-p1-l6-reference-multiuser-lossless-corrective-20260830.json"
)
NEW_CHECKPOINT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/checkpoints/"
    "ui-internal-collaboration-v0r-p1-lossless-corrective-cleanup-gate-pass-20260830.json"
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
PREDECESSOR_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/checkpoints/"
    "ui-internal-collaboration-v0r-p1-lossless-cleanup-gate-pass-20260829.json"
)
EXPECTED_OLD_RECEIPT_SHA256 = (
    "sha256:84c6f7e917adbcb6af3f71d53312a1589a99172ac92e35be2cb7d382bfc7cd17"
)
SUPERSESSION_AUTHORITY_TEXT = (
    "V0R-P1 lossless corrective reference 재수집 및 새 immutable receipt로 "
    "supersede 승인"
)
CLEANUP_AUTHORITY_TEXT = "V0R 합성 A/B 루트 Trash 이동 및 최종 cleanup 승인"
REFERENCE_BUILD_IDENTIFIER = (
    "notion-web-live-ui-lossless-storage-corrective-observation-20260830"
)
MASK_REVIEW_ID = "V0R-P1-LOSSLESS-CORRECTIVE-20260830-MASK-REVIEW"
RECEIPT_ID = "V0R-P1-L6-REFERENCE-MULTIUSER-LOSSLESS-CORRECTIVE-20260830"


def _path(repo_path: str) -> Path:
    return sealer.REPOSITORY_ROOT / Path(*PurePosixPath(repo_path).parts)


MASK_RECTS = deepcopy(geometry.MASK_RECTS)
MASK_RECTS.update(
    {
        ("V0R-MU-008", 1280, 800): [
            geometry._rect(1095, 8, 52, 28),
            geometry._rect(466, 328, 32, 32),
        ],
        ("V0R-MU-008", 1440, 900): [
            geometry._rect(1255, 8, 52, 28),
            geometry._rect(466, 328, 32, 32),
        ],
        ("V0R-MU-009", 1280, 800): [
            geometry._rect(1095, 8, 52, 28),
            geometry._rect(466, 328, 32, 32),
        ],
        ("V0R-MU-009", 1440, 900): [
            geometry._rect(1255, 8, 52, 28),
            geometry._rect(466, 328, 32, 32),
        ],
        ("V0R-MU-010", 1280, 800): [
            geometry._rect(1095, 8, 52, 28),
            geometry._rect(456, 416, 32, 32),
        ],
        ("V0R-MU-010", 1440, 900): [
            geometry._rect(1255, 8, 52, 28),
            geometry._rect(456, 416, 32, 32),
        ],
        ("V0R-MU-011", 1280, 800): [
            geometry._rect(1095, 8, 52, 28),
            geometry._rect(456, 657, 34, 32),
        ],
        ("V0R-MU-011", 1440, 900): [
            geometry._rect(1255, 8, 52, 28),
            geometry._rect(456, 657, 34, 32),
        ],
        ("V0R-MU-012", 1280, 800): [
            geometry._rect(1067, 8, 80, 28),
            geometry._rect(490, 335, 135, 35),
            geometry._rect(490, 400, 135, 35),
            geometry._rect(490, 462, 35, 35),
        ],
        ("V0R-MU-012", 1440, 900): [
            geometry._rect(1227, 8, 80, 28),
            geometry._rect(490, 335, 135, 35),
            geometry._rect(490, 400, 135, 35),
            geometry._rect(490, 462, 35, 35),
        ],
        ("V0R-MU-014", 1280, 800): [
            geometry._rect(1067, 8, 80, 28),
            geometry._rect(490, 335, 135, 35),
            geometry._rect(490, 400, 135, 35),
            geometry._rect(490, 520, 135, 35),
            geometry._rect(490, 583, 35, 35),
        ],
        ("V0R-MU-014", 1440, 900): [
            geometry._rect(1227, 8, 80, 28),
            geometry._rect(490, 335, 135, 35),
            geometry._rect(490, 400, 135, 35),
            geometry._rect(490, 520, 135, 35),
            geometry._rect(490, 583, 35, 35),
        ],
    }
)


def _regenerate_masks(reviewed_at: str) -> None:
    screenshot_root = _path(SCREENSHOT_REPO_ROOT)
    mask_root = _path(MASK_REPO_ROOT)
    screenshots = sorted(screenshot_root.glob("V0R-MU-*.png"))
    if len(screenshots) != 36:
        raise ValueError(f"expected 36 screenshots, found {len(screenshots)}")
    if set(MASK_RECTS) != {
        (f"V0R-MU-{index:03d}", width, height)
        for index in range(1, 19)
        for width, height in ((1280, 800), (1440, 900))
    }:
        raise ValueError("mask geometry denominator drift")
    mask_root.mkdir(parents=True, exist_ok=True)
    for screenshot in screenshots:
        state_id, viewport = screenshot.stem.split("__", 1)
        width, height = (int(value) for value in viewport.split("x", 1))
        decoded = decode_png_srgb_rgba8(screenshot.read_bytes())
        if (decoded.width, decoded.height) != (width, height):
            raise ValueError(f"PNG geometry drift: {screenshot.name}")
        rectangles = MASK_RECTS[(state_id, width, height)]
        mask = {
            "schema_version": 1,
            "contract": "code_assist.notion.pixel_mask_manifest.v1",
            "scenario_id": screenshot.stem,
            "image": {"width": width, "height": height},
            "content_independent": True,
            "review": {
                "status": "approved",
                "receipt_id": MASK_REVIEW_ID,
                "reviewer": "v0r-p1-lossless-corrective-main-agent",
                "reviewed_at": reviewed_at,
            },
            "masks": [
                {
                    "id": f"DYNAMIC-REGION-{index:02d}",
                    "reason": "avatar",
                    "rect": rect,
                }
                for index, rect in enumerate(rectangles, start=1)
            ],
        }
        validate_mask_manifest(
            mask,
            scenario_id=screenshot.stem,
            width=width,
            height=height,
        )
        (mask_root / f"{screenshot.stem}.json").write_text(
            json.dumps(mask, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _configure_sealer() -> None:
    sealer.ORIGINAL_RECEIPT_REPO_PATH = OLD_RECEIPT_REPO_PATH
    sealer.CORRECTIVE_RECEIPT_REPO_PATH = NEW_RECEIPT_REPO_PATH
    sealer.CORRECTIVE_CHECKPOINT_REPO_PATH = NEW_CHECKPOINT_REPO_PATH
    sealer.SCREENSHOT_REPO_ROOT = SCREENSHOT_REPO_ROOT
    sealer.MASK_REPO_ROOT = MASK_REPO_ROOT
    sealer.RAW_REPO_ROOT = RAW_REPO_ROOT
    sealer.PREDECESSOR_REPO_PATH = PREDECESSOR_REPO_PATH
    sealer.EXPECTED_ORIGINAL_RECEIPT_SHA256 = EXPECTED_OLD_RECEIPT_SHA256
    sealer.SUPERSESSION_AUTHORITY_TEXT = SUPERSESSION_AUTHORITY_TEXT
    sealer.CLEANUP_AUTHORITY_TEXT = CLEANUP_AUTHORITY_TEXT
    sealer.REFERENCE_BUILD_IDENTIFIER = REFERENCE_BUILD_IDENTIFIER
    verifier.ORIGINAL_RECEIPT_REPO_PATH = OLD_RECEIPT_REPO_PATH

    original_build_receipt = sealer.build_receipt
    original_build_checkpoint = sealer.build_checkpoint

    def build_receipt(*, recorded_at: str) -> dict[str, object]:
        receipt = original_build_receipt(recorded_at=recorded_at)
        receipt["receipt_id"] = RECEIPT_ID
        receipt["run"]["run_id"] = (
            "v0r-p1-lossless-corrective-reference-20260830"
        )
        receipt["run"]["semantic_action_log_source"] = OLD_RECEIPT_REPO_PATH
        receipt["run"]["prior_corrective_receipt_sha256"] = (
            EXPECTED_OLD_RECEIPT_SHA256
        )
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
            "UI-INTERNAL-COLLABORATION-V0R-P1-LOSSLESS-CORRECTIVE-"
            "CLEANUP-GATE-PASS-20260830"
        )
        checkpoint["checkpoint_kind"] = (
            "lossless_storage_corrective_reference_cleanup_and_receipt_gate_pass"
        )
        checkpoint["purpose"] = (
            "Seal the fresh 18-state by 2-viewport corrective reference, "
            "reviewed masks, and revoke-before-recoverable-Trash cleanup while "
            "preserving the 2026-08-29 receipt byte-for-byte."
        )
        checkpoint["next_action"] = (
            "Resume V0B-P1 at the current I0 exact-parity material-readiness cursor; "
            "defer Full36, Core25, and performance gates until I0 is materially ready."
        )
        return checkpoint

    sealer.build_receipt = build_receipt
    sealer.build_checkpoint = build_checkpoint


def main() -> int:
    old_path = _path(OLD_RECEIPT_REPO_PATH)
    if sealer._sha256_file(old_path) != EXPECTED_OLD_RECEIPT_SHA256:
        raise ValueError("V0R_20260829_RECEIPT_HASH_DRIFT")
    if _path(NEW_RECEIPT_REPO_PATH).exists() or _path(NEW_CHECKPOINT_REPO_PATH).exists():
        raise FileExistsError("V0R_20260830_IMMUTABLE_OUTPUT_EXISTS")
    reviewed_at = (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    _regenerate_masks(reviewed_at)
    _configure_sealer()
    result = sealer.seal()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
