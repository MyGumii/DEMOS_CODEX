"""Fail-closed redaction for the superseding V0R-P1 pixel reference.

Raw screenshots are admitted only from the run-owned runtime directory.  The
tool applies already-reviewed dynamic avatar-region rectangles, validates the
same strict mask contract used by exact-pixel comparison, writes the redacted
PNG and mask manifest exclusively, and removes the raw input on every exit.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, UnidentifiedImageError

from code_assist_v1.tools.notion_parity.exact_pixel import (
    MASK_MANIFEST_CONTRACT,
    decode_png_srgb_rgba8,
    encode_rgba_png,
    validate_mask_manifest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = PROJECT_ROOT / ".runtime" / "v0r-p1-corrective-20260827" / "raw"
EVIDENCE_ROOT = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "evidence"
    / "internal_collaboration_reference"
    / "v0r-p1-corrective"
)
SCREENSHOT_ROOT = EVIDENCE_ROOT / "screenshots"
MASK_ROOT = EVIDENCE_ROOT / "masks"
REDACTION_RGBA = (36, 36, 36, 255)


class CorrectiveRedactionError(ValueError):
    """Raised when a raw screenshot cannot be admitted safely."""


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _direct_child(
    path: Path,
    root: Path,
    suffix: str | frozenset[str],
) -> Path:
    resolved_root = root.resolve(strict=False)
    resolved = path.resolve(strict=False)
    allowed_suffixes = frozenset({suffix}) if isinstance(suffix, str) else suffix
    if (
        resolved.parent != resolved_root
        or resolved.suffix.lower() not in allowed_suffixes
    ):
        raise CorrectiveRedactionError("V0R_CORRECTIVE_PATH_OUTSIDE_RUN_ROOT")
    return resolved


def _decode_raw(
    raw: Path,
    payload: bytes,
    *,
    expected_width: int,
    expected_height: int,
) -> tuple[int, int, bytes, str]:
    suffix = raw.suffix.lower()
    if suffix == ".png":
        decoded = decode_png_srgb_rgba8(payload)
        return decoded.width, decoded.height, decoded.pixels, "png"
    try:
        with Image.open(BytesIO(payload)) as image:
            if image.format != "JPEG":
                raise CorrectiveRedactionError(
                    "V0R_CORRECTIVE_JPEG_SIGNATURE_REQUIRED"
                )
            if image.size != (expected_width, expected_height):
                raise CorrectiveRedactionError(
                    "V0R_CORRECTIVE_IMAGE_DIMENSION_MISMATCH"
                )
            converted = image.convert("RGBA")
            return converted.width, converted.height, converted.tobytes(), "jpeg"
    except UnidentifiedImageError as exc:
        raise CorrectiveRedactionError(
            "V0R_CORRECTIVE_JPEG_SIGNATURE_REQUIRED"
        ) from exc


def _rectangles(value: Any) -> list[dict[str, int]]:
    if not isinstance(value, list):
        raise CorrectiveRedactionError("V0R_CORRECTIVE_RECTS_LIST_REQUIRED")
    normalized: list[dict[str, int]] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"x", "y", "width", "height"}:
            raise CorrectiveRedactionError("V0R_CORRECTIVE_RECT_INVALID")
        rect: dict[str, int] = {}
        for key in ("x", "y", "width", "height"):
            raw = item[key]
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise CorrectiveRedactionError("V0R_CORRECTIVE_RECT_INTEGER_REQUIRED")
            rect[key] = raw
        if rect["x"] < 0 or rect["y"] < 0 or rect["width"] < 1 or rect["height"] < 1:
            raise CorrectiveRedactionError("V0R_CORRECTIVE_RECT_BOUNDS")
        normalized.append(rect)
    return normalized


def redact(
    *,
    raw_path: Path,
    output_path: Path,
    mask_path: Path,
    scenario_id: str,
    expected_width: int,
    expected_height: int,
    rectangles: Sequence[Mapping[str, int]],
) -> dict[str, Any]:
    raw = _direct_child(raw_path, RAW_ROOT, frozenset({".png", ".jpg", ".jpeg"}))
    output = _direct_child(output_path, SCREENSHOT_ROOT, ".png")
    mask_output = _direct_child(mask_path, MASK_ROOT, ".json")
    if not raw.is_file() or raw.is_symlink():
        raise CorrectiveRedactionError("V0R_CORRECTIVE_RAW_SCREENSHOT_REQUIRED")
    if output.exists() or mask_output.exists():
        raise CorrectiveRedactionError("V0R_CORRECTIVE_EXCLUSIVE_OUTPUT_REQUIRED")

    normalized_rectangles = _rectangles(list(rectangles))
    output_created = False
    try:
        width, height, source_pixels, source_format = _decode_raw(
            raw,
            raw.read_bytes(),
            expected_width=expected_width,
            expected_height=expected_height,
        )
        if (width, height) != (expected_width, expected_height):
            raise CorrectiveRedactionError("V0R_CORRECTIVE_IMAGE_DIMENSION_MISMATCH")

        masks = [
            {
                "id": f"DYNAMIC-AVATAR-{index:02d}",
                "reason": "avatar",
                "rect": dict(rect),
            }
            for index, rect in enumerate(normalized_rectangles, start=1)
        ]
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "contract": MASK_MANIFEST_CONTRACT,
            "scenario_id": scenario_id,
            "image": {"width": width, "height": height},
            "content_independent": True,
            "review": {
                "status": "approved",
                "receipt_id": "V0R-P1-CORRECTIVE-MASK-REVIEW",
                "reviewer": "v0r-corrective-dynamic-avatar-region-review",
                "reviewed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
            "masks": masks,
        }
        validated = validate_mask_manifest(
            manifest,
            scenario_id=scenario_id,
            width=width,
            height=height,
        )
        persisted_manifest = {
            key: value
            for key, value in validated.items()
            if key != "manifest_sha256"
        }

        pixels = bytearray(source_pixels)
        fill = bytes(REDACTION_RGBA)
        for rect in normalized_rectangles:
            x0, y0 = rect["x"], rect["y"]
            x1, y1 = x0 + rect["width"], y0 + rect["height"]
            if x1 > width or y1 > height:
                raise CorrectiveRedactionError("V0R_CORRECTIVE_RECT_BOUNDS")
            row = fill * rect["width"]
            for y in range(y0, y1):
                start = (y * width + x0) * 4
                pixels[start : start + len(row)] = row

        redacted_png = encode_rgba_png(width, height, bytes(pixels))
        mask_payload = (
            json.dumps(
                persisted_manifest,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        output.parent.mkdir(parents=True, exist_ok=True)
        mask_output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as handle:
            handle.write(redacted_png)
        output_created = True
        try:
            with mask_output.open("xb") as handle:
                handle.write(mask_payload)
        except Exception:
            output.unlink(missing_ok=True)
            output_created = False
            raise

        return {
            "contract": "code_assist.notion.v0r_corrective_redaction.v1",
            "status": "pass",
            "scenario_id": scenario_id,
            "width": width,
            "height": height,
            "source_format": source_format,
            "mask_count": len(masks),
            "redacted_png_sha256": _sha256(redacted_png),
            "mask_manifest_sha256": _sha256(mask_payload),
            "raw_removed": True,
        }
    except Exception:
        if output_created and not mask_output.exists():
            output.unlink(missing_ok=True)
        raise
    finally:
        raw.unlink(missing_ok=True)


def redact_vertical_tiles(
    *,
    raw_paths: Sequence[Path],
    tile_heights: Sequence[int],
    output_path: Path,
    mask_path: Path,
    scenario_id: str,
    expected_width: int,
    expected_height: int,
    rectangles: Sequence[Mapping[str, int]],
) -> dict[str, Any]:
    """Assemble reviewed vertical JPEG/PNG tiles, then apply the same contract."""

    if not raw_paths or len(raw_paths) != len(tile_heights):
        raise CorrectiveRedactionError("V0R_CORRECTIVE_TILE_COUNT_MISMATCH")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in tile_heights):
        raise CorrectiveRedactionError("V0R_CORRECTIVE_TILE_HEIGHT_INVALID")
    if sum(tile_heights) != expected_height:
        raise CorrectiveRedactionError("V0R_CORRECTIVE_TILE_HEIGHT_MISMATCH")

    raws = [
        _direct_child(path, RAW_ROOT, frozenset({".png", ".jpg", ".jpeg"}))
        for path in raw_paths
    ]
    assembled = _direct_child(
        RAW_ROOT / f"{scenario_id}__assembled.png",
        RAW_ROOT,
        ".png",
    )
    if assembled.exists():
        raise CorrectiveRedactionError("V0R_CORRECTIVE_EXCLUSIVE_OUTPUT_REQUIRED")
    try:
        decoded_tiles: list[bytes] = []
        source_formats: list[str] = []
        for raw, tile_height in zip(raws, tile_heights, strict=True):
            if not raw.is_file() or raw.is_symlink():
                raise CorrectiveRedactionError(
                    "V0R_CORRECTIVE_RAW_SCREENSHOT_REQUIRED"
                )
            width, height, pixels, source_format = _decode_raw(
                raw,
                raw.read_bytes(),
                expected_width=expected_width,
                expected_height=tile_height,
            )
            if (width, height) != (expected_width, tile_height):
                raise CorrectiveRedactionError(
                    "V0R_CORRECTIVE_IMAGE_DIMENSION_MISMATCH"
                )
            decoded_tiles.append(pixels)
            source_formats.append(source_format)

        assembled.parent.mkdir(parents=True, exist_ok=True)
        with assembled.open("xb") as handle:
            handle.write(
                encode_rgba_png(
                    expected_width,
                    expected_height,
                    b"".join(decoded_tiles),
                )
            )
        report = redact(
            raw_path=assembled,
            output_path=output_path,
            mask_path=mask_path,
            scenario_id=scenario_id,
            expected_width=expected_width,
            expected_height=expected_height,
            rectangles=rectangles,
        )
        report["source_format"] = "+".join(source_formats) + "_vertical_tiles"
        report["source_tile_count"] = len(raws)
        return report
    finally:
        assembled.unlink(missing_ok=True)
        for raw in raws:
            raw.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mask", type=Path, required=True)
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--rectangles-json", required=True)
    args = parser.parse_args(argv)
    report = redact(
        raw_path=args.raw,
        output_path=args.output,
        mask_path=args.mask,
        scenario_id=args.scenario_id,
        expected_width=args.width,
        expected_height=args.height,
        rectangles=json.loads(args.rectangles_json),
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "CorrectiveRedactionError",
    "EVIDENCE_ROOT",
    "MASK_ROOT",
    "RAW_ROOT",
    "SCREENSHOT_ROOT",
    "redact",
    "redact_vertical_tiles",
]
