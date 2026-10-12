from __future__ import annotations

import struct
import zlib

import pytest

from code_assist_v1.tools.notion_parity.exact_pixel import (
    MASK_MANIFEST_CONTRACT,
    ExactPixelContractError,
    RgbaImage,
    compare_decoded_rgba8,
    decode_png_srgb_rgba8,
    encode_rgba_png,
    sha256_bytes,
)


SCENARIO_ID = "V0A-P1-RGBA"
ENVIRONMENT_SHA256 = "sha256:" + "1" * 64


def _image(width: int = 10, height: int = 10, color: bytes = b"\x10\x20\x30\xff") -> RgbaImage:
    pixels = color * (width * height)
    return RgbaImage(width, height, pixels, sha256_bytes(pixels))


def _manifest(width: int = 10, height: int = 10, masks: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "schema_version": 1,
        "contract": MASK_MANIFEST_CONTRACT,
        "scenario_id": SCENARIO_ID,
        "image": {"width": width, "height": height},
        "content_independent": True,
        "review": {
            "status": "approved",
            "receipt_id": "V0A-P1-MASK-REVIEW",
            "reviewer": "synthetic-contract-reviewer",
            "reviewed_at": "2026-08-23T16:25:32Z",
        },
        "masks": [] if masks is None else masks,
    }


def _replace_pixel(image: RgbaImage, x: int, y: int, color: bytes) -> RgbaImage:
    pixels = bytearray(image.pixels)
    offset = (y * image.width + x) * 4
    pixels[offset : offset + 4] = color
    payload = bytes(pixels)
    return RgbaImage(image.width, image.height, payload, sha256_bytes(payload))


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _rgb_png(width: int, height: int, pixels: bytes) -> bytes:
    stride = width * 3
    raw = b"".join(
        b"\x00" + pixels[offset : offset + stride]
        for offset in range(0, len(pixels), stride)
    )
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            _png_chunk(b"IDAT", zlib.compress(raw)),
            _png_chunk(b"IEND", b""),
        )
    )


@pytest.mark.unit
def test_v0a_p1_exact_rgba_identical_images_emit_a_deterministic_zero_diff() -> None:
    reference = _image()
    first = compare_decoded_rgba8(
        reference,
        reference,
        scenario_id=SCENARIO_ID,
        mask_manifest=_manifest(),
        environment_sha256=ENVIRONMENT_SHA256,
    )
    second = compare_decoded_rgba8(
        reference,
        reference,
        scenario_id=SCENARIO_ID,
        mask_manifest=_manifest(),
        environment_sha256=ENVIRONMENT_SHA256,
    )
    assert first.report["status"] == "pass"
    assert first.report["differing_pixel_count"] == 0
    assert first.report["difference_bounding_boxes"] == []
    assert first.report["diff_image_sha256"] == second.report["diff_image_sha256"]
    assert first.diff_png == second.diff_png
    decoded_diff = decode_png_srgb_rgba8(first.diff_png)
    assert decoded_diff.width == 10
    assert decoded_diff.height == 10


@pytest.mark.unit
def test_v0a_p1_one_unmasked_rgba_byte_difference_fails_with_exact_box() -> None:
    reference = _image()
    candidate = _replace_pixel(reference, 3, 4, b"\x10\x20\x31\xff")
    result = compare_decoded_rgba8(
        reference,
        candidate,
        scenario_id=SCENARIO_ID,
        mask_manifest=_manifest(),
        environment_sha256=ENVIRONMENT_SHA256,
    )
    assert result.report["status"] == "fail"
    assert result.report["differing_pixel_count"] == 1
    assert result.report["masked_differing_pixel_count"] == 0
    assert result.report["total_differing_pixel_count"] == 1
    assert result.report["difference_bounding_boxes"] == [
        {"x": 3, "y": 4, "width": 1, "height": 1}
    ]


@pytest.mark.unit
def test_v0a_p1_reviewed_content_independent_mask_counts_but_excludes_one_pixel() -> None:
    reference = _image()
    candidate = _replace_pixel(reference, 3, 4, b"\x11\x20\x30\xff")
    result = compare_decoded_rgba8(
        reference,
        candidate,
        scenario_id=SCENARIO_ID,
        mask_manifest=_manifest(
            masks=[
                {
                    "id": "MASK-CARET-001",
                    "reason": "caret",
                    "rect": {"x": 3, "y": 4, "width": 1, "height": 1},
                }
            ]
        ),
        environment_sha256=ENVIRONMENT_SHA256,
    )
    assert result.report["status"] == "pass"
    assert result.report["differing_pixel_count"] == 0
    assert result.report["masked_differing_pixel_count"] == 1
    assert result.report["total_differing_pixel_count"] == 1


@pytest.mark.unit
def test_v0a_p1_rgba_encoder_and_rgb_decoder_preserve_exact_channels() -> None:
    rgba = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    decoded_rgba = decode_png_srgb_rgba8(encode_rgba_png(2, 1, rgba))
    assert decoded_rgba.pixels == rgba
    decoded_rgb = decode_png_srgb_rgba8(_rgb_png(2, 1, b"\x01\x02\x03\x05\x06\x07"))
    assert decoded_rgb.pixels == b"\x01\x02\x03\xff\x05\x06\x07\xff"


@pytest.mark.unit
def test_v0a_p1_png_crc_tamper_and_dimension_mismatch_fail_closed() -> None:
    png = bytearray(encode_rgba_png(1, 1, b"\x00\x00\x00\xff"))
    png[-5] ^= 1
    with pytest.raises(ExactPixelContractError) as crc_error:
        decode_png_srgb_rgba8(bytes(png))
    assert crc_error.value.code == "PNG_CRC"

    with pytest.raises(ExactPixelContractError) as dimension_error:
        compare_decoded_rgba8(
            _image(10, 10),
            _image(11, 10),
            scenario_id=SCENARIO_ID,
            mask_manifest=_manifest(),
            environment_sha256=ENVIRONMENT_SHA256,
        )
    assert dimension_error.value.code == "IMAGE_DIMENSIONS"
