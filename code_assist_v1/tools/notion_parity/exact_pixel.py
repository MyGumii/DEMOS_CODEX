"""Strict, dependency-free exact-pixel contracts for Notion parity evidence.

This module deliberately does not capture external reference images.  It only
decodes bounded PNG inputs, validates independently declared capture and mask
contracts, and produces deterministic exact-RGBA evidence suitable for the V0
validation lane.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import struct
from typing import Any, Mapping, Sequence
import zlib


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
EXACT_PIXEL_CONTRACT = "code_assist.notion.exact_pixel.v1"
MASK_MANIFEST_CONTRACT = "code_assist.notion.pixel_mask_manifest.v1"
CAPTURE_ENVIRONMENT_CONTRACT = "code_assist.notion.pixel_capture_environment.v1"
ADOPTED_VIEWPORTS = frozenset({(1280, 800), (1440, 900)})
MAX_PNG_BYTES = 25 * 1024 * 1024
MAX_DECODED_BYTES = 64 * 1024 * 1024
MAX_MASKED_PIXEL_RATIO = 0.02

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{2,127}$")
_MASK_REASONS = frozenset({"avatar", "caret", "clock", "os_raster_noise"})
_CONTENT_DEPENDENT_KEYS = frozenset(
    {
        "candidate",
        "color",
        "content",
        "difference",
        "hash",
        "pixel",
        "reference",
        "selector",
        "text",
    }
)


class ExactPixelContractError(ValueError):
    """A stable fail-closed exact-pixel contract error."""

    def __init__(self, code: str, location: str, message: str) -> None:
        super().__init__(f"{code} at {location}: {message}")
        self.code = code
        self.location = location
        self.message = message


@dataclass(frozen=True, slots=True)
class RgbaImage:
    width: int
    height: int
    pixels: bytes
    source_sha256: str

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ExactPixelContractError(
                "IMAGE_DIMENSIONS", "$", "image dimensions must be positive"
            )
        if len(self.pixels) != self.width * self.height * 4:
            raise ExactPixelContractError(
                "IMAGE_LENGTH", "$", "RGBA byte length does not match dimensions"
            )
        _require_sha256(self.source_sha256, "$.source_sha256")


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    report: dict[str, Any]
    diff_png: bytes


def _fail(code: str, location: str, message: str) -> None:
    raise ExactPixelContractError(code, location, message)


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], location: str
) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        _fail("FIELD_MISSING", location, f"missing fields: {', '.join(missing)}")
    if unknown:
        _fail("FIELD_UNKNOWN", location, f"unknown fields: {', '.join(unknown)}")


def _require_integer(value: Any, location: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail("INTEGER_REQUIRED", location, f"integer >= {minimum} required")
    return value


def _require_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value:
        _fail("STRING_REQUIRED", location, "non-empty string required")
    return value


def _require_sha256(value: Any, location: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        _fail("SHA256_REQUIRED", location, "canonical sha256:<lowercase-hex> required")
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def encode_rgba_png(width: int, height: int, pixels: bytes) -> bytes:
    """Encode a deterministic, non-interlaced sRGB RGBA8 PNG."""

    image = RgbaImage(width, height, bytes(pixels), "sha256:" + "0" * 64)
    stride = image.width * 4
    raw = b"".join(
        b"\x00" + image.pixels[offset : offset + stride]
        for offset in range(0, len(image.pixels), stride)
    )
    return b"".join(
        (
            PNG_SIGNATURE,
            _png_chunk(
                b"IHDR",
                struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0),
            ),
            _png_chunk(b"sRGB", b"\x00"),
            _png_chunk(b"IDAT", zlib.compress(raw, level=9)),
            _png_chunk(b"IEND", b""),
        )
    )


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def decode_png_srgb_rgba8(payload: bytes) -> RgbaImage:
    """Decode bounded RGB8/RGBA8 PNG bytes into exact sRGB RGBA8 pixels.

    Web screenshots without an explicit color-profile chunk are interpreted as
    sRGB.  An explicit incompatible ICC profile or gamma is rejected rather
    than silently color-converted.
    """

    data = bytes(payload)
    if len(data) > MAX_PNG_BYTES:
        _fail("PNG_SIZE", "$", "PNG exceeds the bounded input size")
    if not data.startswith(PNG_SIGNATURE):
        _fail("PNG_SIGNATURE", "$", "invalid PNG signature")

    offset = len(PNG_SIGNATURE)
    chunks: list[tuple[bytes, bytes]] = []
    saw_iend = False
    while offset < len(data):
        if len(data) - offset < 12:
            _fail("PNG_TRUNCATED", "$", "truncated PNG chunk")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            _fail("PNG_TRUNCATED", "$", "chunk length exceeds PNG payload")
        chunk_payload = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        actual_crc = zlib.crc32(kind + chunk_payload) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            _fail("PNG_CRC", f"$.{kind.decode('latin-1')}", "chunk CRC mismatch")
        if saw_iend:
            _fail("PNG_TRAILING_CHUNK", "$", "chunk appears after IEND")
        chunks.append((kind, chunk_payload))
        saw_iend = kind == b"IEND"
        offset = end
    if offset != len(data) or not saw_iend:
        _fail("PNG_IEND", "$", "PNG must end with exactly one IEND chunk")
    if not chunks or chunks[0][0] != b"IHDR":
        _fail("PNG_IHDR", "$", "IHDR must be the first chunk")
    if sum(kind == b"IHDR" for kind, _ in chunks) != 1:
        _fail("PNG_IHDR", "$", "PNG must contain exactly one IHDR")
    if sum(kind == b"IEND" for kind, _ in chunks) != 1:
        _fail("PNG_IEND", "$", "PNG must contain exactly one IEND")

    ihdr = chunks[0][1]
    if len(ihdr) != 13:
        _fail("PNG_IHDR", "$.IHDR", "IHDR length must equal 13")
    width, height, depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    if width <= 0 or height <= 0:
        _fail("PNG_DIMENSIONS", "$.IHDR", "dimensions must be positive")
    channels = {2: 3, 6: 4}.get(color_type)
    if depth != 8 or channels is None:
        _fail("PNG_COLOR_TYPE", "$.IHDR", "only RGB8 and RGBA8 are supported")
    if compression != 0 or filtering != 0 or interlace != 0:
        _fail(
            "PNG_ENCODING",
            "$.IHDR",
            "only standard compression/filtering and non-interlaced PNG are supported",
        )
    decoded_length = width * height * channels
    if decoded_length > MAX_DECODED_BYTES:
        _fail("PNG_DECODED_SIZE", "$.IHDR", "decoded image exceeds bounded size")

    known_critical = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}
    for kind, chunk_payload in chunks[1:]:
        if kind[0] & 0x20 == 0 and kind not in known_critical:
            _fail(
                "PNG_CRITICAL_CHUNK",
                f"$.{kind.decode('latin-1')}",
                "unknown critical chunk",
            )
        if kind == b"iCCP":
            _fail("PNG_COLOR_PROFILE", "$.iCCP", "ICC conversion is not permitted")
        if kind == b"sRGB" and (len(chunk_payload) != 1 or chunk_payload[0] > 3):
            _fail("PNG_SRGB", "$.sRGB", "invalid sRGB rendering intent")
        if kind == b"gAMA":
            if len(chunk_payload) != 4 or struct.unpack(">I", chunk_payload)[0] != 45455:
                _fail("PNG_GAMMA", "$.gAMA", "only canonical sRGB gamma is accepted")

    compressed = b"".join(value for kind, value in chunks if kind == b"IDAT")
    if not compressed:
        _fail("PNG_IDAT", "$", "PNG must contain image data")
    stride = width * channels
    expected_raw_length = height * (stride + 1)
    decompressor = zlib.decompressobj()
    try:
        raw = decompressor.decompress(compressed, expected_raw_length + 1)
        raw += decompressor.flush()
    except zlib.error as exc:
        raise ExactPixelContractError(
            "PNG_DEFLATE", "$.IDAT", "invalid compressed image data"
        ) from exc
    if (
        len(raw) != expected_raw_length
        or not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
    ):
        _fail("PNG_DECODED_LENGTH", "$.IDAT", "decoded scanline length mismatch")

    reconstructed = bytearray(width * height * channels)
    previous = bytearray(stride)
    raw_offset = 0
    output_offset = 0
    for row_index in range(height):
        filter_type = raw[raw_offset]
        row = bytearray(raw[raw_offset + 1 : raw_offset + 1 + stride])
        raw_offset += stride + 1
        if filter_type > 4:
            _fail("PNG_FILTER", f"$.row[{row_index}]", "unsupported PNG filter")
        for index in range(stride):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                predictor = _paeth(left, above, upper_left)
            else:
                predictor = 0
            row[index] = (row[index] + predictor) & 0xFF
        reconstructed[output_offset : output_offset + stride] = row
        output_offset += stride
        previous = row

    if channels == 4:
        rgba = bytes(reconstructed)
    else:
        rgba_buffer = bytearray(width * height * 4)
        target = 0
        for source in range(0, len(reconstructed), 3):
            rgba_buffer[target : target + 3] = reconstructed[source : source + 3]
            rgba_buffer[target + 3] = 255
            target += 4
        rgba = bytes(rgba_buffer)
    return RgbaImage(width, height, rgba, sha256_bytes(data))


def _validate_identifier(value: Any, location: str) -> str:
    text = _require_string(value, location)
    if not _IDENTIFIER_RE.fullmatch(text):
        _fail("IDENTIFIER", location, "canonical uppercase identifier required")
    return text


def _content_dependent_key(value: Any, location: str = "$") -> tuple[str, str] | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered != "content_independent" and (
                lowered in _CONTENT_DEPENDENT_KEYS
                or any(
                    lowered.startswith(token + "_")
                    or lowered.endswith("_" + token)
                    for token in _CONTENT_DEPENDENT_KEYS
                )
            ):
                return location, str(key)
            found = _content_dependent_key(child, f"{location}.{key}")
            if found:
                return found
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            found = _content_dependent_key(child, f"{location}[{index}]")
            if found:
                return found
    return None


def _masked_union_area(rectangles: Sequence[tuple[int, int, int, int]]) -> int:
    if not rectangles:
        return 0
    minimum_y = min(y for _, y, _, _ in rectangles)
    maximum_y = max(y + height for _, y, _, height in rectangles)
    area = 0
    for row in range(minimum_y, maximum_y):
        intervals = sorted(
            (x, x + width)
            for x, y, width, height in rectangles
            if y <= row < y + height
        )
        if not intervals:
            continue
        start, end = intervals[0]
        for next_start, next_end in intervals[1:]:
            if next_start > end:
                area += end - start
                start, end = next_start, next_end
            else:
                end = max(end, next_end)
        area += end - start
    return area


def validate_mask_manifest(
    manifest: Mapping[str, Any],
    *,
    scenario_id: str,
    width: int,
    height: int,
) -> dict[str, Any]:
    """Validate and normalize a reviewed, scenario-bound mask manifest."""

    if not isinstance(manifest, Mapping):
        _fail("MASK_TYPE", "$", "mask manifest must be an object")
    dependent = _content_dependent_key(manifest)
    if dependent:
        _fail(
            "MASK_CONTENT_DEPENDENT",
            dependent[0],
            f"content-derived field is forbidden: {dependent[1]}",
        )
    _require_exact_keys(
        manifest,
        {
            "schema_version",
            "contract",
            "scenario_id",
            "image",
            "content_independent",
            "review",
            "masks",
        },
        "$",
    )
    if manifest["schema_version"] != 1 or manifest["contract"] != MASK_MANIFEST_CONTRACT:
        _fail("MASK_CONTRACT", "$", "unsupported mask manifest contract")
    if manifest["scenario_id"] != scenario_id:
        _fail("MASK_SCENARIO", "$.scenario_id", "scenario binding mismatch")
    if manifest["content_independent"] is not True:
        _fail(
            "MASK_CONTENT_INDEPENDENT",
            "$.content_independent",
            "content-independent declaration must be true",
        )

    image = manifest["image"]
    if not isinstance(image, Mapping):
        _fail("MASK_IMAGE", "$.image", "image binding must be an object")
    _require_exact_keys(image, {"width", "height"}, "$.image")
    if image["width"] != width or image["height"] != height:
        _fail("MASK_DIMENSIONS", "$.image", "mask image dimensions do not match")

    review = manifest["review"]
    if not isinstance(review, Mapping):
        _fail("MASK_REVIEW", "$.review", "review must be an object")
    _require_exact_keys(
        review,
        {"status", "receipt_id", "reviewer", "reviewed_at"},
        "$.review",
    )
    if review["status"] != "approved":
        _fail("MASK_REVIEW", "$.review.status", "mask review must be approved")
    _validate_identifier(review["receipt_id"], "$.review.receipt_id")
    _require_string(review["reviewer"], "$.review.reviewer")
    reviewed_at = _require_string(review["reviewed_at"], "$.review.reviewed_at")
    if not reviewed_at.endswith("Z"):
        _fail("MASK_REVIEW_TIME", "$.review.reviewed_at", "UTC timestamp required")

    masks = manifest["masks"]
    if not isinstance(masks, list):
        _fail("MASK_LIST", "$.masks", "masks must be an array")
    normalized_masks: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    rectangles: list[tuple[int, int, int, int]] = []
    for index, item in enumerate(masks):
        location = f"$.masks[{index}]"
        if not isinstance(item, Mapping):
            _fail("MASK_ITEM", location, "mask must be an object")
        _require_exact_keys(item, {"id", "reason", "rect"}, location)
        identifier = _validate_identifier(item["id"], f"{location}.id")
        if identifier in identifiers:
            _fail("MASK_DUPLICATE", f"{location}.id", "mask id must be unique")
        identifiers.add(identifier)
        reason = item["reason"]
        if reason not in _MASK_REASONS:
            _fail("MASK_REASON", f"{location}.reason", "unsupported mask reason")
        rect = item["rect"]
        if not isinstance(rect, Mapping):
            _fail("MASK_RECT", f"{location}.rect", "rectangle must be an object")
        _require_exact_keys(rect, {"x", "y", "width", "height"}, f"{location}.rect")
        x = _require_integer(rect["x"], f"{location}.rect.x")
        y = _require_integer(rect["y"], f"{location}.rect.y")
        rect_width = _require_integer(rect["width"], f"{location}.rect.width", minimum=1)
        rect_height = _require_integer(rect["height"], f"{location}.rect.height", minimum=1)
        if x + rect_width > width or y + rect_height > height:
            _fail("MASK_BOUNDS", f"{location}.rect", "rectangle exceeds image bounds")
        rectangles.append((x, y, rect_width, rect_height))
        normalized_masks.append(
            {
                "id": identifier,
                "reason": reason,
                "rect": {"x": x, "y": y, "width": rect_width, "height": rect_height},
            }
        )
    masked_area = _masked_union_area(rectangles)
    if masked_area > int(width * height * MAX_MASKED_PIXEL_RATIO):
        _fail("MASK_AREA", "$.masks", "masked union exceeds the 2 percent cap")

    normalized = {
        "schema_version": 1,
        "contract": MASK_MANIFEST_CONTRACT,
        "scenario_id": scenario_id,
        "image": {"width": width, "height": height},
        "content_independent": True,
        "review": {
            "status": "approved",
            "receipt_id": review["receipt_id"],
            "reviewer": review["reviewer"],
            "reviewed_at": reviewed_at,
        },
        "masks": sorted(normalized_masks, key=lambda value: value["id"]),
    }
    normalized["manifest_sha256"] = canonical_sha256(normalized)
    return normalized


def validate_capture_environment(
    value: Mapping[str, Any],
    *,
    require_adopted_viewport: bool = True,
) -> dict[str, Any]:
    """Validate a frozen browser capture environment and return canonical data."""

    if not isinstance(value, Mapping):
        _fail("ENV_TYPE", "$", "capture environment must be an object")
    _require_exact_keys(
        value,
        {
            "schema_version",
            "contract",
            "viewport",
            "device_scale_factor",
            "zoom",
            "locale",
            "timezone_id",
            "color_scheme",
            "reduced_motion",
            "time",
            "animations",
            "font_manifest",
            "browser",
            "capture",
            "source",
        },
        "$",
    )
    if value["schema_version"] != 1 or value["contract"] != CAPTURE_ENVIRONMENT_CONTRACT:
        _fail("ENV_CONTRACT", "$", "unsupported capture environment contract")

    viewport = value["viewport"]
    if not isinstance(viewport, Mapping):
        _fail("ENV_VIEWPORT", "$.viewport", "viewport must be an object")
    _require_exact_keys(viewport, {"width", "height"}, "$.viewport")
    width = _require_integer(viewport["width"], "$.viewport.width", minimum=1)
    height = _require_integer(viewport["height"], "$.viewport.height", minimum=1)
    if require_adopted_viewport and (width, height) not in ADOPTED_VIEWPORTS:
        _fail("ENV_VIEWPORT", "$.viewport", "viewport is not in the adopted set")
    if value["device_scale_factor"] != 1 or value["zoom"] != 1:
        _fail("ENV_SCALE", "$", "device scale factor and zoom must both equal one")
    if value["locale"] != "ko-KR" or value["timezone_id"] != "Asia/Seoul":
        _fail("ENV_LOCALE", "$", "locale and timezone must match the adopted profile")
    if value["color_scheme"] != "light" or value["reduced_motion"] != "reduce":
        _fail("ENV_MEDIA", "$", "light color scheme and reduced motion are required")

    frozen_time = value["time"]
    if not isinstance(frozen_time, Mapping):
        _fail("ENV_TIME", "$.time", "time must be an object")
    _require_exact_keys(frozen_time, {"frozen", "epoch_ms"}, "$.time")
    if frozen_time["frozen"] is not True:
        _fail("ENV_TIME", "$.time.frozen", "time must be frozen")
    epoch_ms = _require_integer(frozen_time["epoch_ms"], "$.time.epoch_ms", minimum=1)

    animations = value["animations"]
    if not isinstance(animations, Mapping):
        _fail("ENV_ANIMATIONS", "$.animations", "animations must be an object")
    _require_exact_keys(
        animations,
        {"disabled", "transition_duration_ms", "caret_hidden"},
        "$.animations",
    )
    if (
        animations["disabled"] is not True
        or animations["caret_hidden"] is not True
        or animations["transition_duration_ms"] != 0
    ):
        _fail("ENV_ANIMATIONS", "$.animations", "animation and caret freeze is incomplete")

    fonts = value["font_manifest"]
    if not isinstance(fonts, list) or not fonts:
        _fail("ENV_FONTS", "$.font_manifest", "non-empty font manifest required")
    normalized_fonts: list[dict[str, Any]] = []
    identities: set[tuple[str, str, int]] = set()
    for index, font in enumerate(fonts):
        location = f"$.font_manifest[{index}]"
        if not isinstance(font, Mapping):
            _fail("ENV_FONT", location, "font entry must be an object")
        _require_exact_keys(font, {"family", "style", "weight", "sha256"}, location)
        family = _require_string(font["family"], f"{location}.family")
        style = _require_string(font["style"], f"{location}.style")
        weight = _require_integer(font["weight"], f"{location}.weight", minimum=1)
        digest = _require_sha256(font["sha256"], f"{location}.sha256")
        identity = (family, style, weight)
        if identity in identities:
            _fail("ENV_FONT_DUPLICATE", location, "font identity must be unique")
        identities.add(identity)
        normalized_fonts.append(
            {"family": family, "style": style, "weight": weight, "sha256": digest}
        )

    browser = value["browser"]
    if not isinstance(browser, Mapping):
        _fail("ENV_BROWSER", "$.browser", "browser must be an object")
    _require_exact_keys(browser, {"engine", "channel", "major_version"}, "$.browser")
    if browser["engine"] != "chromium" or browser["channel"] != "chrome":
        _fail("ENV_BROWSER", "$.browser", "Chrome channel on Chromium is required")
    major_version = _require_string(browser["major_version"], "$.browser.major_version")
    if not major_version.isdigit():
        _fail("ENV_BROWSER", "$.browser.major_version", "numeric major version required")

    capture = value["capture"]
    if not isinstance(capture, Mapping):
        _fail("ENV_CAPTURE", "$.capture", "capture must be an object")
    _require_exact_keys(capture, {"full_page", "omit_background"}, "$.capture")
    if capture["full_page"] is not False or capture["omit_background"] is not False:
        _fail("ENV_CAPTURE", "$.capture", "viewport capture with opaque background required")

    source = value["source"]
    if not isinstance(source, Mapping):
        _fail("ENV_SOURCE", "$.source", "source must be an object")
    _require_exact_keys(source, {"kind", "build_sha256"}, "$.source")
    if source["kind"] not in {"reference", "candidate", "synthetic"}:
        _fail("ENV_SOURCE", "$.source.kind", "unsupported source kind")
    build_sha256 = _require_sha256(source["build_sha256"], "$.source.build_sha256")

    normalized = {
        "schema_version": 1,
        "contract": CAPTURE_ENVIRONMENT_CONTRACT,
        "viewport": {"width": width, "height": height},
        "device_scale_factor": 1,
        "zoom": 1,
        "locale": "ko-KR",
        "timezone_id": "Asia/Seoul",
        "color_scheme": "light",
        "reduced_motion": "reduce",
        "time": {"frozen": True, "epoch_ms": epoch_ms},
        "animations": {
            "disabled": True,
            "transition_duration_ms": 0,
            "caret_hidden": True,
        },
        "font_manifest": sorted(
            normalized_fonts,
            key=lambda font: (font["family"], font["style"], font["weight"]),
        ),
        "browser": {
            "engine": "chromium",
            "channel": "chrome",
            "major_version": major_version,
        },
        "capture": {"full_page": False, "omit_background": False},
        "source": {"kind": source["kind"], "build_sha256": build_sha256},
    }
    normalized["environment_sha256"] = canonical_sha256(normalized)
    return normalized


def _comparison_environment(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: child for key, child in value.items() if key not in {"source", "environment_sha256"}}


def _mask_bitmap(
    normalized_manifest: Mapping[str, Any], width: int, height: int
) -> bytearray:
    bitmap = bytearray(width * height)
    for item in normalized_manifest["masks"]:
        rect = item["rect"]
        for y in range(rect["y"], rect["y"] + rect["height"]):
            start = y * width + rect["x"]
            bitmap[start : start + rect["width"]] = b"\x01" * rect["width"]
    return bitmap


def _compare_images(
    reference: RgbaImage,
    candidate: RgbaImage,
    *,
    scenario_id: str,
    mask_manifest: Mapping[str, Any],
    environment_sha256: str,
) -> ComparisonResult:
    if reference.width != candidate.width or reference.height != candidate.height:
        _fail("IMAGE_DIMENSIONS", "$", "reference and candidate dimensions differ")
    _validate_identifier(scenario_id, "$.scenario_id")
    _require_sha256(environment_sha256, "$.environment_sha256")
    normalized_mask = validate_mask_manifest(
        mask_manifest,
        scenario_id=scenario_id,
        width=reference.width,
        height=reference.height,
    )
    bitmap = _mask_bitmap(normalized_mask, reference.width, reference.height)
    diff_pixels = bytearray(reference.width * reference.height * 4)
    total_differences = 0
    masked_differences = 0
    minimum_x = reference.width
    minimum_y = reference.height
    maximum_x = -1
    maximum_y = -1
    for pixel_index in range(reference.width * reference.height):
        offset = pixel_index * 4
        reference_pixel = reference.pixels[offset : offset + 4]
        candidate_pixel = candidate.pixels[offset : offset + 4]
        if reference_pixel == candidate_pixel:
            diff_pixels[offset + 3] = 255
            continue
        total_differences += 1
        x = pixel_index % reference.width
        y = pixel_index // reference.width
        if bitmap[pixel_index]:
            masked_differences += 1
            diff_pixels[offset : offset + 4] = b"\xff\xbf\x00\xff"
            continue
        minimum_x = min(minimum_x, x)
        minimum_y = min(minimum_y, y)
        maximum_x = max(maximum_x, x)
        maximum_y = max(maximum_y, y)
        diff_pixels[offset : offset + 4] = b"\xff\x00\x00\xff"
    unmasked_differences = total_differences - masked_differences
    boxes: list[dict[str, int]] = []
    if unmasked_differences:
        boxes.append(
            {
                "x": minimum_x,
                "y": minimum_y,
                "width": maximum_x - minimum_x + 1,
                "height": maximum_y - minimum_y + 1,
            }
        )
    diff_png = encode_rgba_png(reference.width, reference.height, bytes(diff_pixels))
    report = {
        "schema_version": 1,
        "contract": EXACT_PIXEL_CONTRACT,
        "evidence_type": "exact_pixel",
        "status": "pass" if unmasked_differences == 0 else "fail",
        "scenario_id": scenario_id,
        "viewport": {"width": reference.width, "height": reference.height},
        "reference_image_sha256": reference.source_sha256,
        "candidate_image_sha256": candidate.source_sha256,
        "mask_manifest_sha256": normalized_mask["manifest_sha256"],
        "diff_image_sha256": sha256_bytes(diff_png),
        "environment_sha256": environment_sha256,
        "differing_pixel_count": unmasked_differences,
        "masked_differing_pixel_count": masked_differences,
        "total_differing_pixel_count": total_differences,
        "difference_bounding_boxes": boxes,
    }
    validate_exact_pixel_receipt(report)
    return ComparisonResult(report=report, diff_png=diff_png)


def compare_decoded_rgba8(
    reference: RgbaImage,
    candidate: RgbaImage,
    *,
    scenario_id: str,
    mask_manifest: Mapping[str, Any],
    environment_sha256: str,
) -> ComparisonResult:
    """Compare already decoded images; useful for bounded algorithm tests."""

    return _compare_images(
        reference,
        candidate,
        scenario_id=scenario_id,
        mask_manifest=mask_manifest,
        environment_sha256=environment_sha256,
    )


def compare_pngs_exact(
    reference_png: bytes,
    candidate_png: bytes,
    *,
    scenario_id: str,
    mask_manifest: Mapping[str, Any],
    reference_environment: Mapping[str, Any],
    candidate_environment: Mapping[str, Any],
) -> ComparisonResult:
    """Validate capture parity, decode both PNGs, and compare every RGBA byte."""

    reference = decode_png_srgb_rgba8(reference_png)
    candidate = decode_png_srgb_rgba8(candidate_png)
    reference_env = validate_capture_environment(reference_environment)
    candidate_env = validate_capture_environment(candidate_environment)
    if _comparison_environment(reference_env) != _comparison_environment(candidate_env):
        _fail("ENV_MISMATCH", "$", "reference and candidate capture environments differ")
    viewport = reference_env["viewport"]
    expected_width = viewport["width"] * reference_env["device_scale_factor"]
    expected_height = viewport["height"] * reference_env["device_scale_factor"]
    if (reference.width, reference.height) != (expected_width, expected_height):
        _fail("ENV_GEOMETRY", "$", "decoded image dimensions do not match capture geometry")
    environment_sha256 = canonical_sha256(_comparison_environment(reference_env))
    return _compare_images(
        reference,
        candidate,
        scenario_id=scenario_id,
        mask_manifest=mask_manifest,
        environment_sha256=environment_sha256,
    )


def validate_exact_pixel_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    """Reject semantic differential reports and validate exact-pixel evidence."""

    if not isinstance(value, Mapping):
        _fail("RECEIPT_TYPE", "$", "exact-pixel receipt must be an object")
    _require_exact_keys(
        value,
        {
            "schema_version",
            "contract",
            "evidence_type",
            "status",
            "scenario_id",
            "viewport",
            "reference_image_sha256",
            "candidate_image_sha256",
            "mask_manifest_sha256",
            "diff_image_sha256",
            "environment_sha256",
            "differing_pixel_count",
            "masked_differing_pixel_count",
            "total_differing_pixel_count",
            "difference_bounding_boxes",
        },
        "$",
    )
    if (
        value["schema_version"] != 1
        or value["contract"] != EXACT_PIXEL_CONTRACT
        or value["evidence_type"] != "exact_pixel"
    ):
        _fail("RECEIPT_CONTRACT", "$", "semantic differential is not exact-pixel evidence")
    _validate_identifier(value["scenario_id"], "$.scenario_id")
    viewport = value["viewport"]
    if not isinstance(viewport, Mapping):
        _fail("RECEIPT_VIEWPORT", "$.viewport", "viewport must be an object")
    _require_exact_keys(viewport, {"width", "height"}, "$.viewport")
    _require_integer(viewport["width"], "$.viewport.width", minimum=1)
    _require_integer(viewport["height"], "$.viewport.height", minimum=1)
    for key in (
        "reference_image_sha256",
        "candidate_image_sha256",
        "mask_manifest_sha256",
        "diff_image_sha256",
        "environment_sha256",
    ):
        _require_sha256(value[key], f"$.{key}")
    differing = _require_integer(value["differing_pixel_count"], "$.differing_pixel_count")
    masked = _require_integer(
        value["masked_differing_pixel_count"], "$.masked_differing_pixel_count"
    )
    total = _require_integer(
        value["total_differing_pixel_count"], "$.total_differing_pixel_count"
    )
    if total != differing + masked:
        _fail("RECEIPT_COUNT", "$", "total difference count does not add up")
    boxes = value["difference_bounding_boxes"]
    if not isinstance(boxes, list):
        _fail("RECEIPT_BOXES", "$.difference_bounding_boxes", "boxes must be an array")
    for index, box in enumerate(boxes):
        location = f"$.difference_bounding_boxes[{index}]"
        if not isinstance(box, Mapping):
            _fail("RECEIPT_BOX", location, "box must be an object")
        _require_exact_keys(box, {"x", "y", "width", "height"}, location)
        _require_integer(box["x"], f"{location}.x")
        _require_integer(box["y"], f"{location}.y")
        _require_integer(box["width"], f"{location}.width", minimum=1)
        _require_integer(box["height"], f"{location}.height", minimum=1)
    if differing == 0:
        if value["status"] != "pass" or boxes:
            _fail("RECEIPT_STATUS", "$", "zero unmasked differences must be a box-free pass")
    elif value["status"] != "fail" or not boxes:
        _fail("RECEIPT_STATUS", "$", "unmasked differences must be a boxed failure")
    return dict(value)


__all__ = [
    "ADOPTED_VIEWPORTS",
    "CAPTURE_ENVIRONMENT_CONTRACT",
    "ComparisonResult",
    "EXACT_PIXEL_CONTRACT",
    "ExactPixelContractError",
    "MASK_MANIFEST_CONTRACT",
    "RgbaImage",
    "canonical_json_bytes",
    "canonical_sha256",
    "compare_decoded_rgba8",
    "compare_pngs_exact",
    "decode_png_srgb_rgba8",
    "encode_rgba_png",
    "sha256_bytes",
    "validate_capture_environment",
    "validate_exact_pixel_receipt",
    "validate_mask_manifest",
]
