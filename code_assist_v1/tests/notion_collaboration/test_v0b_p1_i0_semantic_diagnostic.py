from __future__ import annotations

from code_assist_v1.tools.run_v0b_p1_i0_semantic_diagnostic_20260830 import (
    I0_STATE_IDS,
    SEMANTIC_REFERENCE_EXCLUSIONS,
    _semantic_suitability,
)


def _totals() -> dict[str, dict[str, int]]:
    return {
        state_id: {
            "unmasked_differing_pixel_count": index * 10,
            "masked_differing_pixel_count": index,
        }
        for index, state_id in enumerate(sorted(I0_STATE_IDS), start=1)
    }


def test_semantic_suitability_excludes_capture_timing_mismatches() -> None:
    totals = _totals()
    result = _semantic_suitability(totals)

    assert set(SEMANTIC_REFERENCE_EXCLUSIONS) == {"V0R-MU-004", "V0R-MU-006"}
    assert result["reference_fully_usable"] is False
    assert result["eligible_state_count"] == 8
    assert result["eligible_capture_count"] == 16
    assert result["excluded_state_count"] == 2
    assert result["excluded_capture_count"] == 4
    assert {item["state_id"] for item in result["excluded_states"]} == {
        "V0R-MU-004",
        "V0R-MU-006",
    }
    assert result["eligible_unmasked_difference_count"] == sum(
        totals[state_id]["unmasked_differing_pixel_count"]
        for state_id in I0_STATE_IDS - set(SEMANTIC_REFERENCE_EXCLUSIONS)
    )
    assert result["excluded_unmasked_difference_count"] == sum(
        totals[state_id]["unmasked_differing_pixel_count"]
        for state_id in SEMANTIC_REFERENCE_EXCLUSIONS
    )


def test_semantic_suitability_rejects_partial_denominator() -> None:
    totals = _totals()
    totals.pop("V0R-MU-018")

    try:
        _semantic_suitability(totals)
    except RuntimeError as exc:
        assert str(exc) == "V0B_I0_SEMANTIC_SUITABILITY_DENOMINATOR"
    else:
        raise AssertionError("partial semantic denominator must be rejected")
