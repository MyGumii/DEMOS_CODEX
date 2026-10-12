from __future__ import annotations

import pytest

from code_assist_v1.notion_collaboration.approved_dev_pair import ApprovedDevPairError
from code_assist_v1.notion_collaboration.v0b_p1_capture import (
    _apply_reference_capture_matte,
    _exact_reference_transport_eligible,
    _require_exact_reference_transport,
)
from code_assist_v1.tools.notion_parity.exact_pixel import (
    decode_png_srgb_rgba8,
    encode_rgba_png,
)


def _png(width: int, height: int, rgba: bytes) -> bytes:
    return encode_rgba_png(width, height, rgba * (width * height))


@pytest.mark.unit
def test_v0b_candidate_adopts_receipt_bound_privacy_capture_matte() -> None:
    reference_pixels = bytearray(b"\xff\xff\xff\xff" * 16)
    for y in range(1, 3):
        for x in range(1, 3):
            offset = (y * 4 + x) * 4
            reference_pixels[offset : offset + 4] = b"\x20\x20\x20\xff"
    reference = encode_rgba_png(4, 4, bytes(reference_pixels))
    candidate = _png(4, 4, b"\xaa\xbb\xcc\xff")
    matte = {
        "scenario_id": "V0R-MU-013__4x4",
        "kind": "content_independent_privacy_capture_matte",
        "color": "#202020",
        "rectangles": [{"x": 1, "y": 1, "width": 2, "height": 2}],
    }

    output = _apply_reference_capture_matte(
        reference,
        candidate,
        matte,
        scenario_id="V0R-MU-013__4x4",
    )
    decoded = decode_png_srgb_rgba8(output)

    assert decoded.pixels[(1 * 4 + 1) * 4 : (1 * 4 + 2) * 4] == b"\x20\x20\x20\xff"
    assert decoded.pixels[0:4] == b"\xaa\xbb\xcc\xff"


@pytest.mark.unit
def test_v0b_capture_matte_rejects_reference_pixel_drift() -> None:
    matte = {
        "scenario_id": "V0R-MU-006__4x4",
        "kind": "content_independent_privacy_capture_matte",
        "color": "#000000",
        "rectangles": [{"x": 1, "y": 1, "width": 2, "height": 2}],
    }

    with pytest.raises(ApprovedDevPairError, match="V0B_CAPTURE_MATTE_REFERENCE_DRIFT"):
        _apply_reference_capture_matte(
            _png(4, 4, b"\xff\xff\xff\xff"),
            _png(4, 4, b"\xaa\xbb\xcc\xff"),
            matte,
            scenario_id="V0R-MU-006__4x4",
        )


@pytest.mark.unit
def test_v0b_exact_capture_rejects_declared_lossy_reference_transport() -> None:
    with pytest.raises(
        ApprovedDevPairError,
        match="V0B_REFERENCE_CAPTURE_TRANSPORT_INELIGIBLE",
    ):
        _require_exact_reference_transport(
            {
                "capture_transport": {
                    "browser_capture_returned_codec": "jpeg",
                    "stored_codec": "png_srgb_rgba8",
                    "lossless_storage_after_decode": True,
                    "source_encoding_lossless": False,
                    "zero_RGBA_equivalence_claimed": False,
                }
            }
        )


@pytest.mark.unit
def test_v0b_exact_capture_accepts_declared_lossless_reference_transport() -> None:
    _require_exact_reference_transport(
        {
            "capture_transport": {
                "browser_capture_returned_codec": "png",
                "stored_codec": "png_srgb_rgba8",
                "lossless_storage_after_decode": True,
                "source_encoding_lossless": True,
                "zero_RGBA_equivalence_claimed": True,
            }
        }
    )
    assert _exact_reference_transport_eligible({}) is True
    assert (
        _exact_reference_transport_eligible(
            {
                "capture_transport": {
                    "source_encoding_lossless": False,
                    "zero_RGBA_equivalence_claimed": False,
                }
            }
        )
        is False
    )
