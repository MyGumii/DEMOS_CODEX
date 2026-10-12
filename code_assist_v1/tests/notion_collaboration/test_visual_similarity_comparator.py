from __future__ import annotations

import copy

import pytest

from code_assist_v1.tools.notion_parity.exact_pixel import (
    MASK_MANIFEST_CONTRACT,
    RgbaImage,
    canonical_sha256,
    sha256_bytes,
)
from code_assist_v1.tools.notion_parity.visual_similarity import (
    ALGORITHM,
    METRIC_SCALE_PPM,
    VISUAL_SIMILARITY_CONTRACT,
    VISUAL_SIMILARITY_POLICY_CONTRACT,
    VisualSimilarityContractError,
    compare_decoded_rgba8_similarity,
    validate_visual_similarity_policy,
    validate_visual_similarity_receipt,
)


STATE_ID = "V0R-MU-001"
SCENARIO_ID = "V0B-P1-V0R-MU-001-16X16"
ENVIRONMENT_SHA256 = "sha256:" + "1" * 64


def _image(color: bytes = b"\x40\x40\x40\xff") -> RgbaImage:
    pixels = color * (16 * 16)
    return RgbaImage(16, 16, pixels, sha256_bytes(pixels))


def _replace_rect(
    image: RgbaImage, *, x: int, y: int, width: int, height: int, color: bytes
) -> RgbaImage:
    pixels = bytearray(image.pixels)
    for row in range(y, y + height):
        for column in range(x, x + width):
            offset = (row * image.width + column) * 4
            pixels[offset : offset + 4] = color
    payload = bytes(pixels)
    return RgbaImage(image.width, image.height, payload, sha256_bytes(payload))


def _thresholds(
    *,
    ssim: int = METRIC_SCALE_PPM,
    mae: int = 0,
    changed: int = 0,
    edge: int = 0,
    component: int = 0,
) -> dict[str, int]:
    return {
        "min_mean_luminance_ssim_ppm": ssim,
        "max_normalized_rgb_mae_ppm": mae,
        "max_delta16_changed_pixel_ratio_ppm": changed,
        "max_edge_mismatch_ratio_ppm": edge,
        "max_largest_delta16_component_ratio_ppm": component,
    }


def _seal_policy(
    *,
    whole: dict[str, int] | None = None,
    region: dict[str, int] | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "contract": VISUAL_SIMILARITY_POLICY_CONTRACT,
        "policy_id": "v0b-unit-strict-similarity-v1",
        "algorithm": copy.deepcopy(ALGORITHM),
        "review": {
            "status": "approved",
            "receipt_id": "V0B-SIMILARITY-UNIT-REVIEW",
            "reviewer": "unit-contract-reviewer",
            "reviewed_at": "2026-09-07T03:00:00Z",
        },
        "states": [
            {
                "state_id": STATE_ID,
                "viewport": {"width": 16, "height": 16},
                "whole_frame_thresholds": whole or _thresholds(),
                "required_regions": [
                    {
                        "id": "PRIMARY-REGION",
                        "rect": {"x": 0, "y": 0, "width": 8, "height": 8},
                        "thresholds": region or _thresholds(),
                    }
                ],
            }
        ],
    }
    value["policy_sha256"] = canonical_sha256(value)
    return value


def _mask(
    masks: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract": MASK_MANIFEST_CONTRACT,
        "scenario_id": SCENARIO_ID,
        "image": {"width": 16, "height": 16},
        "content_independent": True,
        "review": {
            "status": "approved",
            "receipt_id": "V0B-SIMILARITY-MASK-REVIEW",
            "reviewer": "unit-contract-reviewer",
            "reviewed_at": "2026-09-07T03:00:00Z",
        },
        "masks": [] if masks is None else masks,
    }


def _compare(
    reference: RgbaImage,
    candidate: RgbaImage,
    *,
    policy: dict[str, object],
    mask: dict[str, object] | None = None,
):
    return compare_decoded_rgba8_similarity(
        reference,
        candidate,
        state_id=STATE_ID,
        scenario_id=SCENARIO_ID,
        mask_manifest=mask or _mask(),
        policy=policy,
        environment_sha256=ENVIRONMENT_SHA256,
    )


@pytest.mark.unit
def test_similarity_is_deterministic_and_receipt_is_self_consistent() -> None:
    reference = _image()
    policy = _seal_policy()
    first = _compare(reference, reference, policy=policy)
    second = _compare(reference, reference, policy=policy)

    assert first.report == second.report
    assert first.report["contract"] == VISUAL_SIMILARITY_CONTRACT
    assert first.report["status"] == "pass"
    assert first.report["whole_frame"]["metrics"] == {
        "unmasked_pixel_count": 256,
        "masked_pixel_count": 0,
        "alpha_mismatch_pixel_count": 0,
        "rgb_absolute_error_sum": 0,
        "normalized_rgb_mae_ppm": 0,
        "delta16_changed_pixel_count": 0,
        "delta16_changed_pixel_ratio_ppm": 0,
        "edge_comparison_count": 480,
        "edge_mismatch_count": 0,
        "edge_mismatch_ratio_ppm": 0,
        "delta16_component_count": 0,
        "largest_delta16_component_pixel_count": 0,
        "largest_delta16_component_ratio_ppm": 0,
        "window_count": 4,
        "window_ssim_ppm_sum": 4_000_000,
        "mean_luminance_ssim_ppm": 1_000_000,
        "minimum_window_luminance_ssim_ppm": 1_000_000,
    }
    assert validate_visual_similarity_receipt(first.report) == first.report


@pytest.mark.unit
def test_threshold_equality_passes_and_one_ppm_tighter_fails() -> None:
    reference = _image()
    candidate = _replace_rect(
        reference, x=1, y=1, width=2, height=2, color=b"\x80\x80\x80\xff"
    )
    diagnostic = _compare(
        reference,
        candidate,
        policy=_seal_policy(
            whole=_thresholds(ssim=-METRIC_SCALE_PPM, mae=METRIC_SCALE_PPM, changed=METRIC_SCALE_PPM, edge=METRIC_SCALE_PPM, component=METRIC_SCALE_PPM),
            region=_thresholds(ssim=-METRIC_SCALE_PPM, mae=METRIC_SCALE_PPM, changed=METRIC_SCALE_PPM, edge=METRIC_SCALE_PPM, component=METRIC_SCALE_PPM),
        ),
    )
    def boundary_for(metrics: dict[str, int]) -> dict[str, int]:
        return _thresholds(
            ssim=metrics["mean_luminance_ssim_ppm"],
            mae=metrics["normalized_rgb_mae_ppm"],
            changed=metrics["delta16_changed_pixel_ratio_ppm"],
            edge=metrics["edge_mismatch_ratio_ppm"],
            component=metrics["largest_delta16_component_ratio_ppm"],
        )

    boundary = boundary_for(diagnostic.report["whole_frame"]["metrics"])
    region_boundary = boundary_for(
        diagnostic.report["required_regions"][0]["metrics"]
    )
    at_boundary = _compare(
        reference,
        candidate,
        policy=_seal_policy(whole=boundary, region=region_boundary),
    )
    assert at_boundary.report["status"] == "pass"

    tighter = copy.deepcopy(boundary)
    tighter["max_delta16_changed_pixel_ratio_ppm"] -= 1
    failed = _compare(
        reference,
        candidate,
        policy=_seal_policy(whole=tighter, region=region_boundary),
    )
    assert failed.report["status"] == "fail"
    assert failed.report["failed_scope_ids"] == ["WHOLE-FRAME"]
    assert failed.report["whole_frame"]["failed_metrics"] == [
        "delta16_changed_pixel_ratio_ppm"
    ]


@pytest.mark.unit
def test_reviewed_mask_excludes_pixels_without_changing_policy_thresholds() -> None:
    reference = _image()
    candidate = _replace_rect(
        reference, x=2, y=2, width=1, height=1, color=b"\xff\x00\x00\xff"
    )
    result = _compare(
        reference,
        candidate,
        policy=_seal_policy(),
        mask=_mask(
            [
                {
                    "id": "MASK-CARET-001",
                    "reason": "caret",
                    "rect": {"x": 2, "y": 2, "width": 1, "height": 1},
                }
            ]
        ),
    )
    assert result.report["status"] == "pass"
    assert result.report["whole_frame"]["metrics"]["masked_pixel_count"] == 1
    assert result.report["whole_frame"]["metrics"]["delta16_changed_pixel_count"] == 0
    assert result.report["required_regions"][0]["metrics"]["masked_pixel_count"] == 1


@pytest.mark.unit
def test_whole_frame_and_regions_fail_independently_without_score_averaging() -> None:
    reference = _image()
    loose = _thresholds(
        ssim=-METRIC_SCALE_PPM,
        mae=METRIC_SCALE_PPM,
        changed=METRIC_SCALE_PPM,
        edge=METRIC_SCALE_PPM,
        component=METRIC_SCALE_PPM,
    )

    outside_region = _replace_rect(
        reference, x=12, y=12, width=2, height=2, color=b"\xff\xff\xff\xff"
    )
    global_failure = _compare(
        reference,
        outside_region,
        policy=_seal_policy(whole=_thresholds(), region=loose),
    )
    assert global_failure.report["whole_frame"]["status"] == "fail"
    assert global_failure.report["required_regions"][0]["status"] == "pass"
    assert global_failure.report["failed_scope_ids"] == ["WHOLE-FRAME"]

    inside_region = _replace_rect(
        reference, x=2, y=2, width=2, height=2, color=b"\xff\xff\xff\xff"
    )
    region_failure = _compare(
        reference,
        inside_region,
        policy=_seal_policy(whole=loose, region=_thresholds()),
    )
    assert region_failure.report["whole_frame"]["status"] == "pass"
    assert region_failure.report["required_regions"][0]["status"] == "fail"
    assert region_failure.report["failed_scope_ids"] == ["PRIMARY-REGION"]
    metrics = region_failure.report["required_regions"][0]["metrics"]
    assert metrics["edge_mismatch_count"] > 0
    assert metrics["delta16_component_count"] == 1
    assert metrics["largest_delta16_component_pixel_count"] == 4


@pytest.mark.unit
def test_small_required_control_deletion_fails_its_critical_region() -> None:
    reference = _image()
    control = _replace_rect(
        reference, x=2, y=2, width=2, height=2, color=b"\xf0\xf0\xf0\xff"
    )
    loose = _thresholds(
        ssim=-METRIC_SCALE_PPM,
        mae=METRIC_SCALE_PPM,
        changed=METRIC_SCALE_PPM,
        edge=METRIC_SCALE_PPM,
        component=METRIC_SCALE_PPM,
    )
    policy = _seal_policy(whole=loose, region=_thresholds())

    unchanged = _compare(control, control, policy=policy)
    deleted = _compare(control, reference, policy=policy)

    assert unchanged.report["status"] == "pass"
    assert deleted.report["whole_frame"]["status"] == "pass"
    assert deleted.report["required_regions"][0]["status"] == "fail"
    assert deleted.report["failed_scope_ids"] == ["PRIMARY-REGION"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutation,expected_code",
    [
        (lambda value: value.pop("review"), "OBJECT_KEYS"),
        (lambda value: value.__setitem__("default_thresholds", _thresholds()), "OBJECT_KEYS"),
        (lambda value: value["algorithm"].__setitem__("delta_threshold", 15), "POLICY_ALGORITHM"),
        (lambda value: value["states"].append(copy.deepcopy(value["states"][0])), "POLICY_STATE_DUPLICATE"),
        (lambda value: value["states"][0]["required_regions"][0]["rect"].__setitem__("width", 17), "RECT_BOUNDS"),
        (lambda value: value.__setitem__("policy_sha256", "sha256:" + "0" * 64), "POLICY_HASH"),
    ],
)
def test_malformed_or_fallback_policy_fails_closed(mutation, expected_code: str) -> None:
    policy = _seal_policy()
    mutation(policy)
    # Re-seal structural mutations so the expected validation failure is about
    # the malformed structure rather than merely the stale hash.
    if expected_code != "POLICY_HASH" and "policy_sha256" in policy:
        unsigned = {key: value for key, value in policy.items() if key != "policy_sha256"}
        policy["policy_sha256"] = canonical_sha256(unsigned)
    with pytest.raises(VisualSimilarityContractError) as error:
        validate_visual_similarity_policy(policy)
    assert error.value.code == expected_code


@pytest.mark.unit
def test_policy_expected_denominator_and_report_status_are_fail_closed() -> None:
    policy = _seal_policy()
    with pytest.raises(VisualSimilarityContractError) as denominator_error:
        validate_visual_similarity_policy(
            policy,
            expected_keys={(STATE_ID, 16, 16), ("V0R-MU-002", 16, 16)},
        )
    assert denominator_error.value.code == "POLICY_DENOMINATOR"

    report = _compare(_image(), _image(), policy=policy).report
    report["status"] = "fail"
    with pytest.raises(VisualSimilarityContractError) as status_error:
        validate_visual_similarity_receipt(report)
    assert status_error.value.code == "RECEIPT_STATUS"
