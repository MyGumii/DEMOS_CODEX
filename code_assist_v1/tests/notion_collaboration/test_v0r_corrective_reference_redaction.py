from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from code_assist_v1.tools.notion_parity.exact_pixel import (
    decode_png_srgb_rgba8,
    encode_rgba_png,
    validate_mask_manifest,
)
from code_assist_v1.tools.v0r_corrective_reference_redaction import (
    CorrectiveRedactionError,
    redact,
    redact_vertical_tiles,
)


@pytest.mark.unit
def test_corrective_redaction_writes_valid_mask_and_removes_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_root = tmp_path / "raw"
    screenshot_root = tmp_path / "screenshots"
    mask_root = tmp_path / "masks"
    for root in (raw_root, screenshot_root, mask_root):
        root.mkdir()
    monkeypatch.setattr(
        "code_assist_v1.tools.v0r_corrective_reference_redaction.RAW_ROOT",
        raw_root,
    )
    monkeypatch.setattr(
        "code_assist_v1.tools.v0r_corrective_reference_redaction.SCREENSHOT_ROOT",
        screenshot_root,
    )
    monkeypatch.setattr(
        "code_assist_v1.tools.v0r_corrective_reference_redaction.MASK_ROOT",
        mask_root,
    )

    raw = raw_root / "state.png"
    raw.write_bytes(encode_rgba_png(100, 100, bytes((255, 255, 255, 255)) * 10_000))
    output = screenshot_root / "state.png"
    mask = mask_root / "state.json"
    report = redact(
        raw_path=raw,
        output_path=output,
        mask_path=mask,
        scenario_id="V0R-CORRECTIVE-STATE-100X100",
        expected_width=100,
        expected_height=100,
        rectangles=[{"x": 10, "y": 10, "width": 10, "height": 10}],
    )

    assert report["status"] == "pass"
    assert report["raw_removed"] is True
    assert not raw.exists()
    decoded = decode_png_srgb_rgba8(output.read_bytes())
    assert decoded.pixels[(10 * 100 + 10) * 4 : (10 * 100 + 10) * 4 + 4] == bytes(
        (36, 36, 36, 255)
    )
    value = json.loads(mask.read_text(encoding="utf-8"))
    validated = validate_mask_manifest(
        value,
        scenario_id="V0R-CORRECTIVE-STATE-100X100",
        width=100,
        height=100,
    )
    assert len(validated["masks"]) == 1


@pytest.mark.unit
def test_corrective_redaction_canonicalizes_jpeg_and_removes_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_root = tmp_path / "raw"
    screenshot_root = tmp_path / "screenshots"
    mask_root = tmp_path / "masks"
    for root in (raw_root, screenshot_root, mask_root):
        root.mkdir()
    monkeypatch.setattr(
        "code_assist_v1.tools.v0r_corrective_reference_redaction.RAW_ROOT",
        raw_root,
    )
    monkeypatch.setattr(
        "code_assist_v1.tools.v0r_corrective_reference_redaction.SCREENSHOT_ROOT",
        screenshot_root,
    )
    monkeypatch.setattr(
        "code_assist_v1.tools.v0r_corrective_reference_redaction.MASK_ROOT",
        mask_root,
    )

    jpeg = BytesIO()
    Image.new("RGB", (100, 100), (240, 240, 240)).save(jpeg, format="JPEG")
    raw = raw_root / "state.jpg"
    raw.write_bytes(jpeg.getvalue())
    output = screenshot_root / "state.png"
    report = redact(
        raw_path=raw,
        output_path=output,
        mask_path=mask_root / "state.json",
        scenario_id="V0R-CORRECTIVE-JPEG-100X100",
        expected_width=100,
        expected_height=100,
        rectangles=[],
    )

    assert report["source_format"] == "jpeg"
    assert decode_png_srgb_rgba8(output.read_bytes()).width == 100
    assert not raw.exists()


@pytest.mark.unit
def test_corrective_redaction_assembles_vertical_tiles_and_removes_raws(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_root = tmp_path / "raw"
    screenshot_root = tmp_path / "screenshots"
    mask_root = tmp_path / "masks"
    for root in (raw_root, screenshot_root, mask_root):
        root.mkdir()
    monkeypatch.setattr(
        "code_assist_v1.tools.v0r_corrective_reference_redaction.RAW_ROOT",
        raw_root,
    )
    monkeypatch.setattr(
        "code_assist_v1.tools.v0r_corrective_reference_redaction.SCREENSHOT_ROOT",
        screenshot_root,
    )
    monkeypatch.setattr(
        "code_assist_v1.tools.v0r_corrective_reference_redaction.MASK_ROOT",
        mask_root,
    )

    raws: list[Path] = []
    for index, color in enumerate(((255, 0, 0), (0, 0, 255)), start=1):
        jpeg = BytesIO()
        Image.new("RGB", (100, 50), color).save(jpeg, format="JPEG")
        raw = raw_root / f"tile-{index}.jpg"
        raw.write_bytes(jpeg.getvalue())
        raws.append(raw)

    output = screenshot_root / "state.png"
    report = redact_vertical_tiles(
        raw_paths=raws,
        tile_heights=[50, 50],
        output_path=output,
        mask_path=mask_root / "state.json",
        scenario_id="V0R-CORRECTIVE-TILES-100X100",
        expected_width=100,
        expected_height=100,
        rectangles=[],
    )

    decoded = decode_png_srgb_rgba8(output.read_bytes())
    assert report["source_tile_count"] == 2
    assert report["source_format"] == "jpeg+jpeg_vertical_tiles"
    assert decoded.pixels[0:3] != decoded.pixels[(75 * 100) * 4 : (75 * 100) * 4 + 3]
    assert not any(raw.exists() for raw in raws)


@pytest.mark.unit
def test_corrective_redaction_rejects_oversized_mask_and_removes_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_root = tmp_path / "raw"
    screenshot_root = tmp_path / "screenshots"
    mask_root = tmp_path / "masks"
    for root in (raw_root, screenshot_root, mask_root):
        root.mkdir()
    monkeypatch.setattr(
        "code_assist_v1.tools.v0r_corrective_reference_redaction.RAW_ROOT",
        raw_root,
    )
    monkeypatch.setattr(
        "code_assist_v1.tools.v0r_corrective_reference_redaction.SCREENSHOT_ROOT",
        screenshot_root,
    )
    monkeypatch.setattr(
        "code_assist_v1.tools.v0r_corrective_reference_redaction.MASK_ROOT",
        mask_root,
    )

    raw = raw_root / "state.png"
    raw.write_bytes(encode_rgba_png(100, 100, bytes((255, 255, 255, 255)) * 10_000))
    with pytest.raises(Exception, match="2 percent"):
        redact(
            raw_path=raw,
            output_path=screenshot_root / "state.png",
            mask_path=mask_root / "state.json",
            scenario_id="V0R-CORRECTIVE-STATE-100X100",
            expected_width=100,
            expected_height=100,
            rectangles=[{"x": 0, "y": 0, "width": 21, "height": 10}],
        )
    assert not raw.exists()


@pytest.mark.unit
def test_corrective_redaction_refuses_paths_outside_run_roots(tmp_path: Path) -> None:
    with pytest.raises(CorrectiveRedactionError, match="OUTSIDE_RUN_ROOT"):
        redact(
            raw_path=tmp_path / "raw.png",
            output_path=tmp_path / "out.png",
            mask_path=tmp_path / "mask.json",
            scenario_id="V0R-CORRECTIVE-STATE-100X100",
            expected_width=100,
            expected_height=100,
            rectangles=[],
        )
