"""Deterministic, fail-closed visual-similarity evidence for Notion parity.

The exact RGBA comparator remains the authority for lossless reference
transport and exact diagnostics.  This module adds a separate visual gate.  It
reuses the exact comparator's bounded PNG decoder, capture-environment
validation, and reviewed content-independent mask contract, but it never
changes their semantics.

Similarity is evaluated independently for the whole frame and every required
policy region.  A scope passes only when all five bounded metrics pass and no
unmasked alpha mismatch is present.  A receipt passes only when the whole frame
and every required region pass; scores are never averaged across scopes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import re
from typing import Any, Iterable, Mapping

from code_assist_v1.tools.notion_parity.exact_pixel import (
    ExactPixelContractError,
    RgbaImage,
    canonical_sha256,
    decode_png_srgb_rgba8,
    validate_capture_environment,
    validate_mask_manifest,
)


VISUAL_SIMILARITY_CONTRACT = "code_assist.notion.visual_similarity.v1"
VISUAL_SIMILARITY_POLICY_CONTRACT = (
    "code_assist.notion.visual_similarity_policy.v1"
)
METRIC_SCALE_PPM = 1_000_000
WINDOW_SIZE_PX = 8
DELTA_THRESHOLD = 16
EDGE_THRESHOLD = 24

ALGORITHM = {
    "id": "fixed-window-luma-ssim-rgb-mae-delta16-edge-cc-v1",
    "metric_scale_ppm": METRIC_SCALE_PPM,
    "window_size_px": WINDOW_SIZE_PX,
    "luminance": {
        "red": 54,
        "green": 183,
        "blue": 19,
        "denominator": 256,
        "rounding": "nearest_half_up",
    },
    "ssim": {
        "k1_times_100": 1,
        "k2_times_100": 3,
        "dynamic_range": 255,
        "variance": "population",
        "window_aggregation": "per_window_ppm_then_mean_half_up",
    },
    "delta_threshold": DELTA_THRESHOLD,
    "edge_threshold": EDGE_THRESHOLD,
    "edge_neighborhood": "right_and_down",
    "component_connectivity": 4,
    "alpha_mismatch_allowed": 0,
}

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{2,127}$")
_THRESHOLD_KEYS = {
    "min_mean_luminance_ssim_ppm",
    "max_normalized_rgb_mae_ppm",
    "max_delta16_changed_pixel_ratio_ppm",
    "max_edge_mismatch_ratio_ppm",
    "max_largest_delta16_component_ratio_ppm",
}
_METRIC_KEYS = {
    "unmasked_pixel_count",
    "masked_pixel_count",
    "alpha_mismatch_pixel_count",
    "rgb_absolute_error_sum",
    "normalized_rgb_mae_ppm",
    "delta16_changed_pixel_count",
    "delta16_changed_pixel_ratio_ppm",
    "edge_comparison_count",
    "edge_mismatch_count",
    "edge_mismatch_ratio_ppm",
    "delta16_component_count",
    "largest_delta16_component_pixel_count",
    "largest_delta16_component_ratio_ppm",
    "window_count",
    "window_ssim_ppm_sum",
    "mean_luminance_ssim_ppm",
    "minimum_window_luminance_ssim_ppm",
}
_CHECK_ORDER = (
    "mean_luminance_ssim_ppm",
    "normalized_rgb_mae_ppm",
    "delta16_changed_pixel_ratio_ppm",
    "edge_mismatch_ratio_ppm",
    "largest_delta16_component_ratio_ppm",
    "alpha_mismatch_pixel_count",
)


class VisualSimilarityContractError(ValueError):
    """A stable validation failure for a similarity policy or receipt."""

    def __init__(self, code: str, location: str, message: str) -> None:
        super().__init__(f"{code} at {location}: {message}")
        self.code = code
        self.location = location
        self.message = message


@dataclass(frozen=True, slots=True)
class VisualSimilarityResult:
    report: dict[str, Any]


def _fail(code: str, location: str, message: str) -> None:
    raise VisualSimilarityContractError(code, location, message)


def _exact_keys(value: Mapping[str, Any], expected: set[str], location: str) -> None:
    actual = set(value)
    if actual != expected:
        _fail(
            "OBJECT_KEYS",
            location,
            f"expected {sorted(expected)!r}, received {sorted(actual)!r}",
        )


def _integer(
    value: Any,
    location: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if type(value) is not int:
        _fail("INTEGER", location, "integer required")
    if minimum is not None and value < minimum:
        _fail("INTEGER_RANGE", location, f"must be at least {minimum}")
    if maximum is not None and value > maximum:
        _fail("INTEGER_RANGE", location, f"must be at most {maximum}")
    return value


def _nonempty_string(value: Any, location: str, *, maximum: int = 240) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        _fail("STRING", location, f"non-empty string of at most {maximum} characters required")
    return value


def _identifier(value: Any, location: str) -> str:
    text = _nonempty_string(value, location, maximum=128)
    if _IDENTIFIER_RE.fullmatch(text) is None:
        _fail("IDENTIFIER", location, "uppercase stable identifier required")
    return text


def _sha256(value: Any, location: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _fail("SHA256", location, "sha256-prefixed lowercase digest required")
    return value


def _reviewed_time(value: Any, location: str) -> str:
    text = _nonempty_string(value, location, maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VisualSimilarityContractError(
            "TIMESTAMP", location, "ISO-8601 timestamp required"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail("TIMESTAMP", location, "timezone-aware timestamp required")
    return text


def _copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _round_div(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        _fail("DIVISOR", "$", "positive divisor required")
    sign = -1 if numerator < 0 else 1
    absolute = abs(numerator)
    return sign * ((absolute + denominator // 2) // denominator)


def _ratio_ppm(numerator: int, denominator: int) -> int:
    return _round_div(numerator * METRIC_SCALE_PPM, denominator)


def _validate_rect(
    value: Any,
    location: str,
    *,
    width: int,
    height: int,
    minimum_extent: int = 1,
) -> dict[str, int]:
    if not isinstance(value, Mapping):
        _fail("RECT", location, "rectangle object required")
    _exact_keys(value, {"x", "y", "width", "height"}, location)
    x = _integer(value["x"], f"{location}.x", minimum=0)
    y = _integer(value["y"], f"{location}.y", minimum=0)
    rect_width = _integer(
        value["width"], f"{location}.width", minimum=minimum_extent
    )
    rect_height = _integer(
        value["height"], f"{location}.height", minimum=minimum_extent
    )
    if x + rect_width > width or y + rect_height > height:
        _fail("RECT_BOUNDS", location, "rectangle escapes the viewport")
    return {"x": x, "y": y, "width": rect_width, "height": rect_height}


def _validate_thresholds(value: Any, location: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        _fail("THRESHOLDS", location, "threshold object required")
    _exact_keys(value, _THRESHOLD_KEYS, location)
    return {
        "min_mean_luminance_ssim_ppm": _integer(
            value["min_mean_luminance_ssim_ppm"],
            f"{location}.min_mean_luminance_ssim_ppm",
            minimum=-METRIC_SCALE_PPM,
            maximum=METRIC_SCALE_PPM,
        ),
        "max_normalized_rgb_mae_ppm": _integer(
            value["max_normalized_rgb_mae_ppm"],
            f"{location}.max_normalized_rgb_mae_ppm",
            minimum=0,
            maximum=METRIC_SCALE_PPM,
        ),
        "max_delta16_changed_pixel_ratio_ppm": _integer(
            value["max_delta16_changed_pixel_ratio_ppm"],
            f"{location}.max_delta16_changed_pixel_ratio_ppm",
            minimum=0,
            maximum=METRIC_SCALE_PPM,
        ),
        "max_edge_mismatch_ratio_ppm": _integer(
            value["max_edge_mismatch_ratio_ppm"],
            f"{location}.max_edge_mismatch_ratio_ppm",
            minimum=0,
            maximum=METRIC_SCALE_PPM,
        ),
        "max_largest_delta16_component_ratio_ppm": _integer(
            value["max_largest_delta16_component_ratio_ppm"],
            f"{location}.max_largest_delta16_component_ratio_ppm",
            minimum=0,
            maximum=METRIC_SCALE_PPM,
        ),
    }


def validate_visual_similarity_policy(
    value: Mapping[str, Any],
    *,
    expected_keys: Iterable[tuple[str, int, int]] | None = None,
) -> dict[str, Any]:
    """Validate a reviewed state/viewport policy catalog.

    ``expected_keys`` lets an integrating gate enforce an exact denominator,
    for example all 18 V0R states at both adopted viewports.  The comparator
    itself always requires one unambiguous matching row and never accepts a
    default/fallback threshold.
    """

    if not isinstance(value, Mapping):
        _fail("POLICY_TYPE", "$", "policy must be an object")
    _exact_keys(
        value,
        {
            "schema_version",
            "contract",
            "policy_id",
            "algorithm",
            "review",
            "states",
            "policy_sha256",
        },
        "$",
    )
    if value["schema_version"] != 1 or value["contract"] != VISUAL_SIMILARITY_POLICY_CONTRACT:
        _fail("POLICY_CONTRACT", "$", "unsupported visual-similarity policy")
    policy_id = _nonempty_string(value["policy_id"], "$.policy_id", maximum=128)
    if value["algorithm"] != ALGORITHM:
        _fail("POLICY_ALGORITHM", "$.algorithm", "algorithm contract must match exactly")

    review = value["review"]
    if not isinstance(review, Mapping):
        _fail("POLICY_REVIEW", "$.review", "review object required")
    _exact_keys(
        review,
        {"status", "receipt_id", "reviewer", "reviewed_at"},
        "$.review",
    )
    if review["status"] != "approved":
        _fail("POLICY_REVIEW", "$.review.status", "approved review required")
    normalized_review = {
        "status": "approved",
        "receipt_id": _identifier(review["receipt_id"], "$.review.receipt_id"),
        "reviewer": _nonempty_string(review["reviewer"], "$.review.reviewer"),
        "reviewed_at": _reviewed_time(review["reviewed_at"], "$.review.reviewed_at"),
    }

    states = value["states"]
    if not isinstance(states, list) or not states:
        _fail("POLICY_STATES", "$.states", "non-empty state policy list required")
    normalized_states: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, int, int]] = set()
    for state_index, state in enumerate(states):
        location = f"$.states[{state_index}]"
        if not isinstance(state, Mapping):
            _fail("POLICY_STATE", location, "state policy must be an object")
        _exact_keys(
            state,
            {"state_id", "viewport", "whole_frame_thresholds", "required_regions"},
            location,
        )
        state_id = _identifier(state["state_id"], f"{location}.state_id")
        viewport = state["viewport"]
        if not isinstance(viewport, Mapping):
            _fail("POLICY_VIEWPORT", f"{location}.viewport", "viewport object required")
        _exact_keys(viewport, {"width", "height"}, f"{location}.viewport")
        width = _integer(viewport["width"], f"{location}.viewport.width", minimum=1)
        height = _integer(viewport["height"], f"{location}.viewport.height", minimum=1)
        key = (state_id, width, height)
        if key in seen_keys:
            _fail("POLICY_STATE_DUPLICATE", location, "duplicate state/viewport policy")
        seen_keys.add(key)
        whole_thresholds = _validate_thresholds(
            state["whole_frame_thresholds"], f"{location}.whole_frame_thresholds"
        )
        regions = state["required_regions"]
        if not isinstance(regions, list) or not regions:
            _fail("POLICY_REGIONS", f"{location}.required_regions", "at least one required region is mandatory")
        normalized_regions: list[dict[str, Any]] = []
        region_ids: set[str] = set()
        for region_index, region in enumerate(regions):
            region_location = f"{location}.required_regions[{region_index}]"
            if not isinstance(region, Mapping):
                _fail("POLICY_REGION", region_location, "region must be an object")
            _exact_keys(region, {"id", "rect", "thresholds"}, region_location)
            region_id = _identifier(region["id"], f"{region_location}.id")
            if region_id == "WHOLE-FRAME" or region_id in region_ids:
                _fail("POLICY_REGION_DUPLICATE", region_location, "region id must be unique and non-reserved")
            region_ids.add(region_id)
            normalized_regions.append(
                {
                    "id": region_id,
                    "rect": _validate_rect(
                        region["rect"],
                        f"{region_location}.rect",
                        width=width,
                        height=height,
                        minimum_extent=2,
                    ),
                    "thresholds": _validate_thresholds(
                        region["thresholds"], f"{region_location}.thresholds"
                    ),
                }
            )
        if [region["id"] for region in normalized_regions] != sorted(region_ids):
            _fail("POLICY_REGION_ORDER", f"{location}.required_regions", "regions must be sorted by id")
        normalized_states.append(
            {
                "state_id": state_id,
                "viewport": {"width": width, "height": height},
                "whole_frame_thresholds": whole_thresholds,
                "required_regions": normalized_regions,
            }
        )

    ordered_keys = [
        (row["state_id"], row["viewport"]["width"], row["viewport"]["height"])
        for row in normalized_states
    ]
    if ordered_keys != sorted(ordered_keys):
        _fail("POLICY_STATE_ORDER", "$.states", "states must be sorted by state id and viewport")
    if expected_keys is not None:
        expected = set(expected_keys)
        if seen_keys != expected:
            _fail(
                "POLICY_DENOMINATOR",
                "$.states",
                f"policy keys differ: missing={sorted(expected - seen_keys)!r}, extra={sorted(seen_keys - expected)!r}",
            )

    normalized = {
        "schema_version": 1,
        "contract": VISUAL_SIMILARITY_POLICY_CONTRACT,
        "policy_id": policy_id,
        "algorithm": _copy_json(ALGORITHM),
        "review": normalized_review,
        "states": normalized_states,
    }
    expected_sha = canonical_sha256(normalized)
    if _sha256(value["policy_sha256"], "$.policy_sha256") != expected_sha:
        _fail("POLICY_HASH", "$.policy_sha256", "canonical policy hash mismatch")
    normalized["policy_sha256"] = expected_sha
    return normalized


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


def _luminance(pixels: bytes) -> bytearray:
    result = bytearray(len(pixels) // 4)
    for pixel_index in range(len(result)):
        offset = pixel_index * 4
        result[pixel_index] = (
            54 * pixels[offset]
            + 183 * pixels[offset + 1]
            + 19 * pixels[offset + 2]
            + 128
        ) // 256
    return result


def _window_ssim_ppm(reference: list[int], candidate: list[int]) -> int:
    count = len(reference)
    if count == 0 or len(candidate) != count:
        _fail("SSIM_WINDOW", "$", "non-empty equally sized windows required")
    sum_reference = sum(reference)
    sum_candidate = sum(candidate)
    sum_reference_squared = sum(value * value for value in reference)
    sum_candidate_squared = sum(value * value for value in candidate)
    sum_product = sum(left * right for left, right in zip(reference, candidate))

    # C1=(0.01*255)^2=6.5025 and C2=(0.03*255)^2=58.5225.
    # Every term is multiplied by 10_000, keeping the complete calculation in
    # integer space until the final deterministic parts-per-million rounding.
    count_squared = count * count
    luminance_numerator = 20_000 * sum_reference * sum_candidate + 65_025 * count_squared
    luminance_denominator = (
        10_000 * (sum_reference * sum_reference + sum_candidate * sum_candidate)
        + 65_025 * count_squared
    )
    covariance_numerator = (
        20_000 * (count * sum_product - sum_reference * sum_candidate)
        + 585_225 * count_squared
    )
    variance_denominator = (
        10_000
        * (
            count * sum_reference_squared
            - sum_reference * sum_reference
            + count * sum_candidate_squared
            - sum_candidate * sum_candidate
        )
        + 585_225 * count_squared
    )
    denominator = luminance_denominator * variance_denominator
    score = _round_div(
        luminance_numerator * covariance_numerator * METRIC_SCALE_PPM,
        denominator,
    )
    return max(-METRIC_SCALE_PPM, min(METRIC_SCALE_PPM, score))


def _scope_metrics(
    reference: RgbaImage,
    candidate: RgbaImage,
    reference_luma: bytearray,
    candidate_luma: bytearray,
    mask: bytearray,
    rect: Mapping[str, int],
    *,
    location: str,
) -> dict[str, int]:
    image_width = reference.width
    x0 = rect["x"]
    y0 = rect["y"]
    scope_width = rect["width"]
    scope_height = rect["height"]
    x1 = x0 + scope_width
    y1 = y0 + scope_height
    scope_area = scope_width * scope_height
    changed = bytearray(scope_area)
    unmasked_count = 0
    alpha_mismatch_count = 0
    rgb_absolute_error_sum = 0
    changed_count = 0

    for y in range(y0, y1):
        for x in range(x0, x1):
            image_index = y * image_width + x
            if mask[image_index]:
                continue
            unmasked_count += 1
            offset = image_index * 4
            red_delta = abs(reference.pixels[offset] - candidate.pixels[offset])
            green_delta = abs(reference.pixels[offset + 1] - candidate.pixels[offset + 1])
            blue_delta = abs(reference.pixels[offset + 2] - candidate.pixels[offset + 2])
            rgb_absolute_error_sum += red_delta + green_delta + blue_delta
            if reference.pixels[offset + 3] != candidate.pixels[offset + 3]:
                alpha_mismatch_count += 1
            if max(red_delta, green_delta, blue_delta) >= DELTA_THRESHOLD:
                local_index = (y - y0) * scope_width + (x - x0)
                changed[local_index] = 1
                changed_count += 1
    if unmasked_count == 0:
        _fail("SCOPE_FULLY_MASKED", location, "scope must contain an unmasked pixel")

    edge_comparison_count = 0
    edge_mismatch_count = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            index = y * image_width + x
            if mask[index]:
                continue
            for neighbor in (
                index + 1 if x + 1 < x1 else None,
                index + image_width if y + 1 < y1 else None,
            ):
                if neighbor is None or mask[neighbor]:
                    continue
                edge_comparison_count += 1
                reference_edge = abs(reference_luma[index] - reference_luma[neighbor]) >= EDGE_THRESHOLD
                candidate_edge = abs(candidate_luma[index] - candidate_luma[neighbor]) >= EDGE_THRESHOLD
                edge_mismatch_count += int(reference_edge != candidate_edge)

    visited = bytearray(scope_area)
    component_count = 0
    largest_component = 0
    for start in range(scope_area):
        if not changed[start] or visited[start]:
            continue
        component_count += 1
        visited[start] = 1
        stack = [start]
        size = 0
        while stack:
            current = stack.pop()
            size += 1
            local_x = current % scope_width
            local_y = current // scope_width
            neighbors: list[int] = []
            if local_x:
                neighbors.append(current - 1)
            if local_x + 1 < scope_width:
                neighbors.append(current + 1)
            if local_y:
                neighbors.append(current - scope_width)
            if local_y + 1 < scope_height:
                neighbors.append(current + scope_width)
            for neighbor in neighbors:
                if changed[neighbor] and not visited[neighbor]:
                    visited[neighbor] = 1
                    stack.append(neighbor)
        largest_component = max(largest_component, size)

    window_scores: list[int] = []
    for window_y in range(y0, y1, WINDOW_SIZE_PX):
        for window_x in range(x0, x1, WINDOW_SIZE_PX):
            reference_window: list[int] = []
            candidate_window: list[int] = []
            for y in range(window_y, min(window_y + WINDOW_SIZE_PX, y1)):
                for x in range(window_x, min(window_x + WINDOW_SIZE_PX, x1)):
                    index = y * image_width + x
                    if not mask[index]:
                        reference_window.append(reference_luma[index])
                        candidate_window.append(candidate_luma[index])
            if reference_window:
                window_scores.append(_window_ssim_ppm(reference_window, candidate_window))
    if not window_scores:
        _fail("SSIM_WINDOWS", location, "scope produced no unmasked SSIM windows")

    window_sum = sum(window_scores)
    return {
        "unmasked_pixel_count": unmasked_count,
        "masked_pixel_count": scope_area - unmasked_count,
        "alpha_mismatch_pixel_count": alpha_mismatch_count,
        "rgb_absolute_error_sum": rgb_absolute_error_sum,
        "normalized_rgb_mae_ppm": _ratio_ppm(
            rgb_absolute_error_sum, unmasked_count * 3 * 255
        ),
        "delta16_changed_pixel_count": changed_count,
        "delta16_changed_pixel_ratio_ppm": _ratio_ppm(
            changed_count, unmasked_count
        ),
        "edge_comparison_count": edge_comparison_count,
        "edge_mismatch_count": edge_mismatch_count,
        "edge_mismatch_ratio_ppm": (
            _ratio_ppm(edge_mismatch_count, edge_comparison_count)
            if edge_comparison_count
            else 0
        ),
        "delta16_component_count": component_count,
        "largest_delta16_component_pixel_count": largest_component,
        "largest_delta16_component_ratio_ppm": _ratio_ppm(
            largest_component, unmasked_count
        ),
        "window_count": len(window_scores),
        "window_ssim_ppm_sum": window_sum,
        "mean_luminance_ssim_ppm": _round_div(window_sum, len(window_scores)),
        "minimum_window_luminance_ssim_ppm": min(window_scores),
    }


def _failed_metrics(
    metrics: Mapping[str, int], thresholds: Mapping[str, int]
) -> list[str]:
    failures: list[str] = []
    if metrics["mean_luminance_ssim_ppm"] < thresholds["min_mean_luminance_ssim_ppm"]:
        failures.append("mean_luminance_ssim_ppm")
    if metrics["normalized_rgb_mae_ppm"] > thresholds["max_normalized_rgb_mae_ppm"]:
        failures.append("normalized_rgb_mae_ppm")
    if metrics["delta16_changed_pixel_ratio_ppm"] > thresholds["max_delta16_changed_pixel_ratio_ppm"]:
        failures.append("delta16_changed_pixel_ratio_ppm")
    if metrics["edge_mismatch_ratio_ppm"] > thresholds["max_edge_mismatch_ratio_ppm"]:
        failures.append("edge_mismatch_ratio_ppm")
    if metrics["largest_delta16_component_ratio_ppm"] > thresholds["max_largest_delta16_component_ratio_ppm"]:
        failures.append("largest_delta16_component_ratio_ppm")
    if metrics["alpha_mismatch_pixel_count"] != 0:
        failures.append("alpha_mismatch_pixel_count")
    return [name for name in _CHECK_ORDER if name in failures]


def _scope_report(
    *,
    scope_id: str,
    rect: dict[str, int],
    thresholds: dict[str, int],
    metrics: dict[str, int],
) -> dict[str, Any]:
    failures = _failed_metrics(metrics, thresholds)
    return {
        "scope_id": scope_id,
        "rect": rect,
        "status": "pass" if not failures else "fail",
        "thresholds": thresholds,
        "metrics": metrics,
        "failed_metrics": failures,
    }


def compare_decoded_rgba8_similarity(
    reference: RgbaImage,
    candidate: RgbaImage,
    *,
    state_id: str,
    scenario_id: str,
    mask_manifest: Mapping[str, Any],
    policy: Mapping[str, Any],
    environment_sha256: str,
) -> VisualSimilarityResult:
    """Compare decoded RGBA images under one exact state/viewport policy row."""

    if reference.width != candidate.width or reference.height != candidate.height:
        _fail("IMAGE_DIMENSIONS", "$", "reference and candidate dimensions differ")
    state_id = _identifier(state_id, "$.state_id")
    scenario_id = _identifier(scenario_id, "$.scenario_id")
    environment_sha256 = _sha256(environment_sha256, "$.environment_sha256")
    normalized_policy = validate_visual_similarity_policy(policy)
    rows = [
        row
        for row in normalized_policy["states"]
        if row["state_id"] == state_id
        and row["viewport"]
        == {"width": reference.width, "height": reference.height}
    ]
    if len(rows) != 1:
        _fail(
            "POLICY_STATE_MISSING",
            "$.states",
            "exactly one state/viewport policy row is required",
        )
    row = rows[0]
    try:
        normalized_mask = validate_mask_manifest(
            mask_manifest,
            scenario_id=scenario_id,
            width=reference.width,
            height=reference.height,
        )
    except ExactPixelContractError as exc:
        raise VisualSimilarityContractError(
            "MASK_CONTRACT", exc.location, exc.message
        ) from exc

    mask = _mask_bitmap(normalized_mask, reference.width, reference.height)
    reference_luma = _luminance(reference.pixels)
    candidate_luma = _luminance(candidate.pixels)
    whole_rect = {"x": 0, "y": 0, "width": reference.width, "height": reference.height}
    whole = _scope_report(
        scope_id="WHOLE-FRAME",
        rect=whole_rect,
        thresholds=row["whole_frame_thresholds"],
        metrics=_scope_metrics(
            reference,
            candidate,
            reference_luma,
            candidate_luma,
            mask,
            whole_rect,
            location="$.whole_frame",
        ),
    )
    region_reports: list[dict[str, Any]] = []
    for region in row["required_regions"]:
        region_reports.append(
            _scope_report(
                scope_id=region["id"],
                rect=region["rect"],
                thresholds=region["thresholds"],
                metrics=_scope_metrics(
                    reference,
                    candidate,
                    reference_luma,
                    candidate_luma,
                    mask,
                    region["rect"],
                    location=f"$.required_regions.{region['id']}",
                ),
            )
        )
    failed_scope_ids = [
        scope["scope_id"]
        for scope in (whole, *region_reports)
        if scope["status"] != "pass"
    ]
    report = {
        "schema_version": 1,
        "contract": VISUAL_SIMILARITY_CONTRACT,
        "evidence_type": "visual_similarity",
        "status": "pass" if not failed_scope_ids else "fail",
        "state_id": state_id,
        "scenario_id": scenario_id,
        "viewport": {"width": reference.width, "height": reference.height},
        "reference_image_sha256": reference.source_sha256,
        "candidate_image_sha256": candidate.source_sha256,
        "mask_manifest_sha256": normalized_mask["manifest_sha256"],
        "environment_sha256": environment_sha256,
        "policy_id": normalized_policy["policy_id"],
        "policy_sha256": normalized_policy["policy_sha256"],
        "algorithm": _copy_json(ALGORITHM),
        "whole_frame": whole,
        "required_regions": region_reports,
        "failed_scope_ids": failed_scope_ids,
    }
    validate_visual_similarity_receipt(report)
    return VisualSimilarityResult(report=report)


def compare_pngs_similarity(
    reference_png: bytes,
    candidate_png: bytes,
    *,
    state_id: str,
    scenario_id: str,
    mask_manifest: Mapping[str, Any],
    policy: Mapping[str, Any],
    reference_environment: Mapping[str, Any],
    candidate_environment: Mapping[str, Any],
) -> VisualSimilarityResult:
    """Validate environments, decode bounded PNGs, and compare similarity."""

    try:
        reference = decode_png_srgb_rgba8(reference_png)
        candidate = decode_png_srgb_rgba8(candidate_png)
        reference_environment_normalized = validate_capture_environment(
            reference_environment
        )
        candidate_environment_normalized = validate_capture_environment(
            candidate_environment
        )
    except ExactPixelContractError as exc:
        raise VisualSimilarityContractError(
            "EXACT_FOUNDATION", exc.location, exc.message
        ) from exc
    comparable_reference = {
        key: child
        for key, child in reference_environment_normalized.items()
        if key not in {"source", "environment_sha256"}
    }
    comparable_candidate = {
        key: child
        for key, child in candidate_environment_normalized.items()
        if key not in {"source", "environment_sha256"}
    }
    if comparable_reference != comparable_candidate:
        _fail("ENV_MISMATCH", "$", "reference and candidate capture environments differ")
    viewport = comparable_reference["viewport"]
    expected_width = viewport["width"] * comparable_reference["device_scale_factor"]
    expected_height = viewport["height"] * comparable_reference["device_scale_factor"]
    if (reference.width, reference.height) != (expected_width, expected_height):
        _fail("ENV_GEOMETRY", "$", "decoded image dimensions do not match capture geometry")
    return compare_decoded_rgba8_similarity(
        reference,
        candidate,
        state_id=state_id,
        scenario_id=scenario_id,
        mask_manifest=mask_manifest,
        policy=policy,
        environment_sha256=canonical_sha256(comparable_reference),
    )


def _validate_metrics(value: Any, location: str, *, area: int) -> dict[str, int]:
    if not isinstance(value, Mapping):
        _fail("METRICS", location, "metrics object required")
    _exact_keys(value, _METRIC_KEYS, location)
    result = {
        key: _integer(value[key], f"{location}.{key}") for key in _METRIC_KEYS
    }
    for key in (
        "unmasked_pixel_count",
        "masked_pixel_count",
        "alpha_mismatch_pixel_count",
        "rgb_absolute_error_sum",
        "normalized_rgb_mae_ppm",
        "delta16_changed_pixel_count",
        "delta16_changed_pixel_ratio_ppm",
        "edge_comparison_count",
        "edge_mismatch_count",
        "edge_mismatch_ratio_ppm",
        "delta16_component_count",
        "largest_delta16_component_pixel_count",
        "largest_delta16_component_ratio_ppm",
        "window_count",
    ):
        if result[key] < 0:
            _fail("METRIC_RANGE", f"{location}.{key}", "metric must be non-negative")
    for key in (
        "normalized_rgb_mae_ppm",
        "delta16_changed_pixel_ratio_ppm",
        "edge_mismatch_ratio_ppm",
        "largest_delta16_component_ratio_ppm",
    ):
        if result[key] > METRIC_SCALE_PPM:
            _fail("METRIC_RANGE", f"{location}.{key}", "ratio exceeds one million ppm")
    for key in ("mean_luminance_ssim_ppm", "minimum_window_luminance_ssim_ppm"):
        if not -METRIC_SCALE_PPM <= result[key] <= METRIC_SCALE_PPM:
            _fail("METRIC_RANGE", f"{location}.{key}", "SSIM must be within [-1, 1]")
    if result["unmasked_pixel_count"] <= 0 or result["window_count"] <= 0:
        _fail("METRIC_DENOMINATOR", location, "positive unmasked and window counts required")
    if result["unmasked_pixel_count"] + result["masked_pixel_count"] != area:
        _fail("METRIC_COUNT", location, "masked and unmasked counts must cover the scope")
    if result["alpha_mismatch_pixel_count"] > result["unmasked_pixel_count"]:
        _fail("METRIC_COUNT", location, "alpha mismatch count exceeds scope")
    if result["delta16_changed_pixel_count"] > result["unmasked_pixel_count"]:
        _fail("METRIC_COUNT", location, "changed count exceeds scope")
    if result["edge_mismatch_count"] > result["edge_comparison_count"]:
        _fail("METRIC_COUNT", location, "edge mismatch count exceeds comparisons")
    if result["largest_delta16_component_pixel_count"] > result["delta16_changed_pixel_count"]:
        _fail("METRIC_COUNT", location, "largest component exceeds changed count")
    expected_ratios = {
        "normalized_rgb_mae_ppm": _ratio_ppm(
            result["rgb_absolute_error_sum"],
            result["unmasked_pixel_count"] * 3 * 255,
        ),
        "delta16_changed_pixel_ratio_ppm": _ratio_ppm(
            result["delta16_changed_pixel_count"], result["unmasked_pixel_count"]
        ),
        "edge_mismatch_ratio_ppm": (
            _ratio_ppm(result["edge_mismatch_count"], result["edge_comparison_count"])
            if result["edge_comparison_count"]
            else 0
        ),
        "largest_delta16_component_ratio_ppm": _ratio_ppm(
            result["largest_delta16_component_pixel_count"],
            result["unmasked_pixel_count"],
        ),
        "mean_luminance_ssim_ppm": _round_div(
            result["window_ssim_ppm_sum"], result["window_count"]
        ),
    }
    for key, expected in expected_ratios.items():
        if result[key] != expected:
            _fail("METRIC_DERIVATION", f"{location}.{key}", "derived metric mismatch")
    return result


def _validate_scope_receipt(
    value: Any,
    location: str,
    *,
    viewport_width: int,
    viewport_height: int,
) -> tuple[dict[str, Any], bool]:
    if not isinstance(value, Mapping):
        _fail("SCOPE", location, "scope report object required")
    _exact_keys(
        value,
        {"scope_id", "rect", "status", "thresholds", "metrics", "failed_metrics"},
        location,
    )
    scope_id = _identifier(value["scope_id"], f"{location}.scope_id")
    rect = _validate_rect(
        value["rect"], location + ".rect", width=viewport_width, height=viewport_height
    )
    thresholds = _validate_thresholds(value["thresholds"], location + ".thresholds")
    metrics = _validate_metrics(
        value["metrics"], location + ".metrics", area=rect["width"] * rect["height"]
    )
    expected_failures = _failed_metrics(metrics, thresholds)
    if value["failed_metrics"] != expected_failures:
        _fail("SCOPE_FAILURES", location + ".failed_metrics", "failure list differs from metrics")
    expected_status = "pass" if not expected_failures else "fail"
    if value["status"] != expected_status:
        _fail("SCOPE_STATUS", location + ".status", "scope status differs from metrics")
    return {
        "scope_id": scope_id,
        "rect": rect,
        "status": expected_status,
        "thresholds": thresholds,
        "metrics": metrics,
        "failed_metrics": expected_failures,
    }, expected_status == "pass"


def validate_visual_similarity_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate report shape and recompute every declared pass/fail decision."""

    if not isinstance(value, Mapping):
        _fail("RECEIPT_TYPE", "$", "visual-similarity receipt must be an object")
    _exact_keys(
        value,
        {
            "schema_version",
            "contract",
            "evidence_type",
            "status",
            "state_id",
            "scenario_id",
            "viewport",
            "reference_image_sha256",
            "candidate_image_sha256",
            "mask_manifest_sha256",
            "environment_sha256",
            "policy_id",
            "policy_sha256",
            "algorithm",
            "whole_frame",
            "required_regions",
            "failed_scope_ids",
        },
        "$",
    )
    if (
        value["schema_version"] != 1
        or value["contract"] != VISUAL_SIMILARITY_CONTRACT
        or value["evidence_type"] != "visual_similarity"
    ):
        _fail("RECEIPT_CONTRACT", "$", "unsupported visual-similarity receipt")
    state_id = _identifier(value["state_id"], "$.state_id")
    scenario_id = _identifier(value["scenario_id"], "$.scenario_id")
    viewport = value["viewport"]
    if not isinstance(viewport, Mapping):
        _fail("RECEIPT_VIEWPORT", "$.viewport", "viewport object required")
    _exact_keys(viewport, {"width", "height"}, "$.viewport")
    width = _integer(viewport["width"], "$.viewport.width", minimum=1)
    height = _integer(viewport["height"], "$.viewport.height", minimum=1)
    for key in (
        "reference_image_sha256",
        "candidate_image_sha256",
        "mask_manifest_sha256",
        "environment_sha256",
        "policy_sha256",
    ):
        _sha256(value[key], f"$.{key}")
    policy_id = _nonempty_string(value["policy_id"], "$.policy_id", maximum=128)
    if value["algorithm"] != ALGORITHM:
        _fail("RECEIPT_ALGORITHM", "$.algorithm", "algorithm contract drift")

    whole, whole_pass = _validate_scope_receipt(
        value["whole_frame"], "$.whole_frame", viewport_width=width, viewport_height=height
    )
    if whole["scope_id"] != "WHOLE-FRAME" or whole["rect"] != {
        "x": 0,
        "y": 0,
        "width": width,
        "height": height,
    }:
        _fail("WHOLE_FRAME", "$.whole_frame", "whole-frame scope must cover the viewport")
    regions = value["required_regions"]
    if not isinstance(regions, list) or not regions:
        _fail("RECEIPT_REGIONS", "$.required_regions", "required region reports missing")
    normalized_regions: list[dict[str, Any]] = []
    region_passes: list[bool] = []
    seen_ids: set[str] = set()
    for index, region in enumerate(regions):
        normalized, passed = _validate_scope_receipt(
            region,
            f"$.required_regions[{index}]",
            viewport_width=width,
            viewport_height=height,
        )
        if normalized["scope_id"] == "WHOLE-FRAME" or normalized["scope_id"] in seen_ids:
            _fail("RECEIPT_REGION_DUPLICATE", f"$.required_regions[{index}]", "region id must be unique")
        seen_ids.add(normalized["scope_id"])
        normalized_regions.append(normalized)
        region_passes.append(passed)
    if [region["scope_id"] for region in normalized_regions] != sorted(seen_ids):
        _fail("RECEIPT_REGION_ORDER", "$.required_regions", "regions must be sorted by id")
    expected_failed_scopes = [
        scope["scope_id"]
        for scope in (whole, *normalized_regions)
        if scope["status"] != "pass"
    ]
    if value["failed_scope_ids"] != expected_failed_scopes:
        _fail("RECEIPT_FAILED_SCOPES", "$.failed_scope_ids", "failed scope list drift")
    expected_status = "pass" if whole_pass and all(region_passes) else "fail"
    if value["status"] != expected_status:
        _fail("RECEIPT_STATUS", "$.status", "whole frame and every region must pass")
    return {
        "schema_version": 1,
        "contract": VISUAL_SIMILARITY_CONTRACT,
        "evidence_type": "visual_similarity",
        "status": expected_status,
        "state_id": state_id,
        "scenario_id": scenario_id,
        "viewport": {"width": width, "height": height},
        "reference_image_sha256": value["reference_image_sha256"],
        "candidate_image_sha256": value["candidate_image_sha256"],
        "mask_manifest_sha256": value["mask_manifest_sha256"],
        "environment_sha256": value["environment_sha256"],
        "policy_id": policy_id,
        "policy_sha256": value["policy_sha256"],
        "algorithm": _copy_json(ALGORITHM),
        "whole_frame": whole,
        "required_regions": normalized_regions,
        "failed_scope_ids": expected_failed_scopes,
    }


__all__ = [
    "ALGORITHM",
    "DELTA_THRESHOLD",
    "EDGE_THRESHOLD",
    "METRIC_SCALE_PPM",
    "VISUAL_SIMILARITY_CONTRACT",
    "VISUAL_SIMILARITY_POLICY_CONTRACT",
    "VisualSimilarityContractError",
    "VisualSimilarityResult",
    "compare_decoded_rgba8_similarity",
    "compare_pngs_similarity",
    "validate_visual_similarity_policy",
    "validate_visual_similarity_receipt",
]
