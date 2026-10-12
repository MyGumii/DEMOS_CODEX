from __future__ import annotations

import copy

import pytest

from code_assist_v1.tools.notion_parity.exact_pixel import (
    CAPTURE_ENVIRONMENT_CONTRACT,
    EXACT_PIXEL_CONTRACT,
    MASK_MANIFEST_CONTRACT,
    ExactPixelContractError,
    canonical_sha256,
    validate_capture_environment,
    validate_exact_pixel_receipt,
    validate_mask_manifest,
)


SCENARIO_ID = "V0A-P1-MASK"
HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


def _manifest(*, masks: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract": MASK_MANIFEST_CONTRACT,
        "scenario_id": SCENARIO_ID,
        "image": {"width": 100, "height": 100},
        "content_independent": True,
        "review": {
            "status": "approved",
            "receipt_id": "V0A-P1-MASK-REVIEW",
            "reviewer": "synthetic-contract-reviewer",
            "reviewed_at": "2026-08-23T16:25:32Z",
        },
        "masks": [] if masks is None else masks,
    }


def _environment(*, width: int = 1280, height: int = 800, source: str = "reference") -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract": CAPTURE_ENVIRONMENT_CONTRACT,
        "viewport": {"width": width, "height": height},
        "device_scale_factor": 1,
        "zoom": 1,
        "locale": "ko-KR",
        "timezone_id": "Asia/Seoul",
        "color_scheme": "light",
        "reduced_motion": "reduce",
        "time": {"frozen": True, "epoch_ms": 1787500800000},
        "animations": {
            "disabled": True,
            "transition_duration_ms": 0,
            "caret_hidden": True,
        },
        "font_manifest": [
            {
                "family": "Arial",
                "style": "normal",
                "weight": 400,
                "sha256": HASH_A,
            }
        ],
        "browser": {"engine": "chromium", "channel": "chrome", "major_version": "140"},
        "capture": {"full_page": False, "omit_background": False},
        "source": {"kind": source, "build_sha256": HASH_B},
    }


def _receipt() -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract": EXACT_PIXEL_CONTRACT,
        "evidence_type": "exact_pixel",
        "status": "pass",
        "scenario_id": SCENARIO_ID,
        "viewport": {"width": 1280, "height": 800},
        "reference_image_sha256": HASH_A,
        "candidate_image_sha256": HASH_B,
        "mask_manifest_sha256": HASH_A,
        "diff_image_sha256": HASH_B,
        "environment_sha256": HASH_A,
        "differing_pixel_count": 0,
        "masked_differing_pixel_count": 0,
        "total_differing_pixel_count": 0,
        "difference_bounding_boxes": [],
    }


@pytest.mark.unit
def test_v0a_p1_mask_manifest_is_scenario_bound_reviewed_and_hash_stable() -> None:
    manifest = _manifest(
        masks=[
            {
                "id": "MASK-CLOCK-001",
                "reason": "clock",
                "rect": {"x": 10, "y": 20, "width": 5, "height": 5},
            },
            {
                "id": "MASK-CARET-001",
                "reason": "caret",
                "rect": {"x": 30, "y": 40, "width": 1, "height": 8},
            },
        ]
    )
    first = validate_mask_manifest(manifest, scenario_id=SCENARIO_ID, width=100, height=100)
    second = validate_mask_manifest(copy.deepcopy(manifest), scenario_id=SCENARIO_ID, width=100, height=100)
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert [item["id"] for item in first["masks"]] == ["MASK-CARET-001", "MASK-CLOCK-001"]


@pytest.mark.unit
def test_v0a_p1_content_derived_unreviewed_oversized_and_wrong_scenario_masks_fail() -> None:
    content_derived = _manifest()
    content_derived["masks"] = [
        {
            "id": "MASK-CARET-001",
            "reason": "caret",
            "selector": "text=secret",
            "rect": {"x": 1, "y": 1, "width": 1, "height": 1},
        }
    ]
    with pytest.raises(ExactPixelContractError) as content_error:
        validate_mask_manifest(content_derived, scenario_id=SCENARIO_ID, width=100, height=100)
    assert content_error.value.code == "MASK_CONTENT_DEPENDENT"

    unreviewed = _manifest()
    unreviewed["review"]["status"] = "pending"  # type: ignore[index]
    with pytest.raises(ExactPixelContractError) as review_error:
        validate_mask_manifest(unreviewed, scenario_id=SCENARIO_ID, width=100, height=100)
    assert review_error.value.code == "MASK_REVIEW"

    oversized = _manifest(
        masks=[
            {
                "id": "MASK-AVATAR-001",
                "reason": "avatar",
                "rect": {"x": 0, "y": 0, "width": 21, "height": 10},
            }
        ]
    )
    with pytest.raises(ExactPixelContractError) as area_error:
        validate_mask_manifest(oversized, scenario_id=SCENARIO_ID, width=100, height=100)
    assert area_error.value.code == "MASK_AREA"

    with pytest.raises(ExactPixelContractError) as scenario_error:
        validate_mask_manifest(_manifest(), scenario_id="V0A-P1-OTHER", width=100, height=100)
    assert scenario_error.value.code == "MASK_SCENARIO"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("device_scale_factor",), 2, "ENV_SCALE"),
        (("zoom",), 1.25, "ENV_SCALE"),
        (("locale",), "en-US", "ENV_LOCALE"),
        (("time", "frozen"), False, "ENV_TIME"),
        (("animations", "disabled"), False, "ENV_ANIMATIONS"),
        (("capture", "full_page"), True, "ENV_CAPTURE"),
    ],
)
def test_v0a_p1_capture_environment_drift_fails_closed(
    path: tuple[str, ...], value: object, code: str
) -> None:
    environment = _environment()
    target: dict[str, object] = environment
    for key in path[:-1]:
        target = target[key]  # type: ignore[assignment,index]
    target[path[-1]] = value
    with pytest.raises(ExactPixelContractError) as error:
        validate_capture_environment(environment)
    assert error.value.code == code


@pytest.mark.unit
def test_v0a_p1_capture_environment_binds_fonts_browser_viewport_and_source() -> None:
    environment = validate_capture_environment(_environment())
    assert environment["viewport"] == {"width": 1280, "height": 800}
    assert environment["environment_sha256"] == canonical_sha256(
        {key: value for key, value in environment.items() if key != "environment_sha256"}
    )
    wrong_viewport = _environment(width=1024, height=768)
    with pytest.raises(ExactPixelContractError) as viewport_error:
        validate_capture_environment(wrong_viewport)
    assert viewport_error.value.code == "ENV_VIEWPORT"


@pytest.mark.unit
def test_v0a_p1_legacy_semantic_differential_and_tampered_receipt_are_rejected() -> None:
    legacy = {
        "report_schema_version": 1,
        "status": "pass",
        "difference_count": 0,
        "differences": [],
    }
    with pytest.raises(ExactPixelContractError) as legacy_error:
        validate_exact_pixel_receipt(legacy)
    assert legacy_error.value.code in {"FIELD_MISSING", "FIELD_UNKNOWN"}

    tampered = _receipt()
    tampered["total_differing_pixel_count"] = 1
    with pytest.raises(ExactPixelContractError) as count_error:
        validate_exact_pixel_receipt(tampered)
    assert count_error.value.code == "RECEIPT_COUNT"

    false_green = _receipt()
    false_green["differing_pixel_count"] = 1
    false_green["total_differing_pixel_count"] = 1
    with pytest.raises(ExactPixelContractError) as status_error:
        validate_exact_pixel_receipt(false_green)
    assert status_error.value.code == "RECEIPT_STATUS"
