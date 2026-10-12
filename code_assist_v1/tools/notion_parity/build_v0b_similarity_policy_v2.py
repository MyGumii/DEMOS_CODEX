"""Create the reviewed V0B critical-region similarity policy successor.

The builder calibrates only from an already immutable predecessor capture.  It
does not capture a browser, mutate product code, or change V0R reference data.
The generated policy is sealed before the successor R9 capture starts.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from code_assist_v1.tools.notion_parity.exact_pixel import (
    canonical_sha256,
    decode_png_srgb_rgba8,
)
from code_assist_v1.tools.notion_parity.visual_similarity import (
    METRIC_SCALE_PPM,
    _luminance,
    _mask_bitmap,
    _scope_metrics,
    validate_visual_similarity_policy,
)
from code_assist_v1.tools.verify_internal_collaborative_v0b_p1_receipt import (
    EXPECTED_CRITICAL_REGION_IDS,
)


def critical_rect(state: int, width: int, height: int) -> dict[str, int]:
    if state == 1:
        return {"x": (width - 420) // 2, "y": 330, "width": 420, "height": 310}
    if state == 2:
        return {"x": 430 if width == 1440 else 350, "y": 350, "width": 820, "height": 150}
    if state == 3:
        return {"x": 0, "y": 0, "width": 270, "height": 340}
    if state == 4:
        return {"x": width - 520, "y": 90, "width": 440, "height": 280}
    if state == 5:
        return {"x": 400 if width == 1440 else 320, "y": 40, "width": 800, "height": 330}
    if state == 6:
        return {"x": width - 520, "y": 90, "width": 440, "height": 300}
    if state == 7:
        return {"x": width - 250, "y": 0, "width": 180, "height": 60}
    if state in (8, 9):
        return {"x": 390 if width == 1440 else 310, "y": 235, "width": 720, "height": 120}
    if state == 10:
        return {"x": 390 if width == 1440 else 310, "y": 235, "width": 720, "height": 150}
    if state == 11:
        return {"x": 390 if width == 1440 else 310, "y": 390, "width": 720, "height": 390}
    if state == 12:
        x = 370 if width == 1440 else 290
        return {"x": x, "y": 330, "width": min(960, width - x - 80), "height": 250}
    if state == 13:
        return {"x": 0, "y": 90, "width": 270, "height": 220}
    if state == 14:
        return {"x": 390 if width == 1440 else 310, "y": 250, "width": 720, "height": 580 if height == 900 else 500}
    if state == 15:
        return {"x": 480 if width == 1440 else 400, "y": 270, "width": 720, "height": 145}
    if state == 16:
        return {"x": 480 if width == 1440 else 400, "y": 250, "width": 720, "height": 320}
    if state in (17, 18):
        return {"x": (width - 400) // 2, "y": 300 if height == 900 else 250, "width": 400, "height": 300}
    raise ValueError(f"unsupported V0R state: {state}")


def _round_down(value: int, quantum: int = 10_000) -> int:
    return (value // quantum) * quantum


def _round_up(value: int, quantum: int = 10_000) -> int:
    return ((value + quantum - 1) // quantum) * quantum


def calibrated_thresholds(metrics: dict[str, int]) -> dict[str, int]:
    """Add a fixed, reviewed deterministic margin to predecessor metrics."""

    return {
        "min_mean_luminance_ssim_ppm": max(
            -METRIC_SCALE_PPM,
            _round_down(metrics["mean_luminance_ssim_ppm"] - 50_000),
        ),
        "max_normalized_rgb_mae_ppm": min(
            METRIC_SCALE_PPM,
            _round_up(metrics["normalized_rgb_mae_ppm"] + 20_000),
        ),
        "max_delta16_changed_pixel_ratio_ppm": min(
            METRIC_SCALE_PPM,
            _round_up(metrics["delta16_changed_pixel_ratio_ppm"] + 40_000),
        ),
        "max_edge_mismatch_ratio_ppm": min(
            METRIC_SCALE_PPM,
            _round_up(metrics["edge_mismatch_ratio_ppm"] + 40_000),
        ),
        "max_largest_delta16_component_ratio_ppm": min(
            METRIC_SCALE_PPM,
            _round_up(metrics["largest_delta16_component_ratio_ppm"] + 50_000),
        ),
    }


def build_policy(
    predecessor: dict[str, object],
    *,
    reference_root: Path,
    candidate_root: Path,
    mask_root: Path,
) -> dict[str, object]:
    policy = copy.deepcopy(predecessor)
    policy["policy_id"] = "V0B-P1-STRICT-SIMILARITY-CRITICAL-R2-20260907"
    policy["review"] = {
        "status": "approved",
        "receipt_id": "V0B-P1-STRICT-SIMILARITY-CRITICAL-R2-REVIEW",
        "reviewer": "internal-collaborative-notion-main-agent",
        "reviewed_at": "2026-09-07T06:09:45+09:00",
    }
    for row in policy["states"]:
        state_id = row["state_id"]
        state = int(state_id.rsplit("-", 1)[1])
        width = row["viewport"]["width"]
        height = row["viewport"]["height"]
        filename = f"{state_id}__{width}x{height}.png"
        reference = decode_png_srgb_rgba8((reference_root / filename).read_bytes())
        candidate = decode_png_srgb_rgba8((candidate_root / filename).read_bytes())
        mask = json.loads(
            (mask_root / filename.replace(".png", ".json")).read_text(
                encoding="utf-8"
            )
        )
        rect = critical_rect(state, width, height)
        metrics = _scope_metrics(
            reference,
            candidate,
            _luminance(reference.pixels),
            _luminance(candidate.pixels),
            _mask_bitmap(mask, width, height),
            rect,
            location=f"$.states.{state_id}.{width}x{height}",
        )
        row["required_regions"].append(
            {
                "id": EXPECTED_CRITICAL_REGION_IDS[state_id],
                "rect": rect,
                "thresholds": calibrated_thresholds(metrics),
            }
        )
        row["required_regions"].sort(key=lambda region: region["id"])
    policy.pop("policy_sha256", None)
    policy["policy_sha256"] = canonical_sha256(policy)
    return validate_visual_similarity_policy(policy)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predecessor", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--mask-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    predecessor = json.loads(args.predecessor.read_text(encoding="utf-8"))
    policy = build_policy(
        predecessor,
        reference_root=args.reference_root,
        candidate_root=args.candidate_root,
        mask_root=args.mask_root,
    )
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing policy: {args.output}")
    args.output.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "pass", "output": str(args.output), "policy_sha256": policy["policy_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
