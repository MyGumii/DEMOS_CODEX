from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.notion_parity.exact_pixel import encode_rgba_png
from code_assist_v1.tools.verify_v0r_p1_lossless_capture_ingest import (
    CONTRACT,
    main,
    verify,
)


TEST_STATES = ("V0R-MU-001", "V0R-MU-004")
TEST_VIEWPORTS = ((4, 3), (5, 4))


def _sha256(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, object]]:
    source_root = tmp_path / "source"
    screenshot_root = tmp_path / "screenshots"
    source_root.mkdir()
    screenshot_root.mkdir()
    captures: list[dict[str, object]] = []
    for state_index, state_id in enumerate(TEST_STATES):
        for width, height in TEST_VIEWPORTS:
            scenario_id = f"{state_id}__{width}x{height}"
            pixel = bytes((state_index + 1, width, height, 255))
            payload = encode_rgba_png(width, height, pixel * (width * height))
            name = f"{scenario_id}.png"
            (source_root / name).write_bytes(payload)
            (screenshot_root / name).write_bytes(payload)
            captures.append(
                {
                    "scenario_id": scenario_id,
                    "state_id": state_id,
                    "viewport": {"width": width, "height": height},
                    "browser_capture_returned_codec": "png",
                    "source_relative_path": name,
                    "source_sha256": _sha256(payload),
                    "screenshot_relative_path": name,
                    "screenshot_sha256": _sha256(payload),
                    "source_bytes_copied_without_reencode": True,
                }
            )
    manifest: dict[str, object] = {
        "schema_version": 1,
        "contract": CONTRACT,
        "packet_id": "V0R-P1",
        "capture_transport": {
            "browser_capture_returned_codec": "png",
            "stored_codec": "png_srgb_rgba8",
            "conversion": "none_source_bytes_copied_without_reencode",
            "source_bytes_preserved": True,
            "source_encoding_lossless": True,
            "zero_RGBA_equivalence_claimed": True,
        },
        "captures": captures,
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, source_root, screenshot_root, manifest


def _verify(
    manifest_path: Path, source_root: Path, screenshot_root: Path
) -> dict[str, object]:
    return verify(
        manifest_path,
        source_root=source_root,
        screenshot_root=screenshot_root,
        expected_states=TEST_STATES,
        expected_viewports=TEST_VIEWPORTS,
    )


def _rewrite(path: Path, manifest: dict[str, object]) -> None:
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_lossless_capture_ingest_accepts_direct_png_byte_copy(tmp_path: Path) -> None:
    manifest_path, source_root, screenshot_root, _ = _fixture(tmp_path)

    audit = _verify(manifest_path, source_root, screenshot_root)

    assert audit["status"] == "pass"
    assert audit["error_count"] == 0
    assert audit["capture_count"] == 4
    assert audit["source_bytes_preserved_count"] == 4
    assert audit["exact_reference_transport_eligible"] is True


def test_lossless_capture_ingest_rejects_jpeg_source_declaration(tmp_path: Path) -> None:
    manifest_path, source_root, screenshot_root, manifest = _fixture(tmp_path)
    manifest["capture_transport"]["browser_capture_returned_codec"] = "jpeg"
    manifest["capture_transport"]["source_encoding_lossless"] = False
    manifest["capture_transport"]["zero_RGBA_equivalence_claimed"] = False
    manifest["captures"][0]["browser_capture_returned_codec"] = "jpeg"
    _rewrite(manifest_path, manifest)

    audit = _verify(manifest_path, source_root, screenshot_root)

    assert audit["status"] == "fail"
    assert audit["exact_reference_transport_eligible"] is False
    assert "SOURCE_CODEC_NOT_PNG" in {
        error["code"] for error in audit["errors"]
    }


def test_lossless_capture_ingest_rejects_reencoded_stored_png(tmp_path: Path) -> None:
    manifest_path, source_root, screenshot_root, manifest = _fixture(tmp_path)
    capture = manifest["captures"][0]
    stored_path = screenshot_root / capture["screenshot_relative_path"]
    width = capture["viewport"]["width"]
    height = capture["viewport"]["height"]
    replacement = encode_rgba_png(width, height, bytes((9, 8, 7, 255)) * (width * height))
    stored_path.write_bytes(replacement)
    capture["screenshot_sha256"] = _sha256(replacement)
    _rewrite(manifest_path, manifest)

    audit = _verify(manifest_path, source_root, screenshot_root)

    assert audit["status"] == "fail"
    assert "SOURCE_BYTES_NOT_PRESERVED" in {
        error["code"] for error in audit["errors"]
    }


def test_lossless_capture_ingest_rejects_jpeg_bytes_named_png(tmp_path: Path) -> None:
    manifest_path, source_root, screenshot_root, manifest = _fixture(tmp_path)
    capture = manifest["captures"][0]
    payload = b"\xff\xd8\xff\xe0fake-jpeg"
    (source_root / capture["source_relative_path"]).write_bytes(payload)
    (screenshot_root / capture["screenshot_relative_path"]).write_bytes(payload)
    capture["source_sha256"] = _sha256(payload)
    capture["screenshot_sha256"] = _sha256(payload)
    _rewrite(manifest_path, manifest)

    audit = _verify(manifest_path, source_root, screenshot_root)

    codes = {error["code"] for error in audit["errors"]}
    assert audit["status"] == "fail"
    assert "SOURCE_PNG_SIGNATURE" in codes
    assert "SCREENSHOT_PNG_SIGNATURE" in codes
    assert "SOURCE_PNG_DECODE" in codes


def test_lossless_capture_ingest_rejects_missing_denominator(tmp_path: Path) -> None:
    manifest_path, source_root, screenshot_root, manifest = _fixture(tmp_path)
    manifest["captures"].pop()
    _rewrite(manifest_path, manifest)

    audit = _verify(manifest_path, source_root, screenshot_root)

    codes = {error["code"] for error in audit["errors"]}
    assert audit["status"] == "fail"
    assert "CAPTURE_DENOMINATOR_MISSING" in codes
    assert "CAPTURE_DENOMINATOR_COUNT" in codes


@pytest.mark.parametrize("field", ["source_relative_path", "screenshot_relative_path"])
def test_lossless_capture_ingest_rejects_path_escape(
    tmp_path: Path, field: str
) -> None:
    manifest_path, source_root, screenshot_root, manifest = _fixture(tmp_path)
    manifest["captures"][0][field] = "../escape.png"
    _rewrite(manifest_path, manifest)

    audit = _verify(manifest_path, source_root, screenshot_root)

    expected = "SOURCE_PATH" if field == "source_relative_path" else "SCREENSHOT_PATH"
    assert expected in {error["code"] for error in audit["errors"]}


def test_lossless_capture_ingest_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    manifest_path, source_root, screenshot_root, _ = _fixture(tmp_path)
    manifest_path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")

    audit = _verify(manifest_path, source_root, screenshot_root)

    assert audit["status"] == "fail"
    assert audit["errors"][0]["code"] == "MANIFEST_LOAD"


def test_lossless_capture_ingest_writes_immutable_audit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path, source_root, screenshot_root, _ = _fixture(tmp_path)
    output = tmp_path / "lossless-ingest-audit.json"

    result = main(
        [
            "--manifest",
            str(manifest_path),
            "--source-root",
            str(source_root),
            "--screenshot-root",
            str(screenshot_root),
            "--output",
            str(output),
            "--compact",
        ]
    )

    assert result == 1  # production denominator is intentionally not the unit fixture
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "fail"
    capsys.readouterr()
    with pytest.raises(FileExistsError):
        main(
            [
                "--manifest",
                str(manifest_path),
                "--source-root",
                str(source_root),
                "--screenshot-root",
                str(screenshot_root),
                "--output",
                str(output),
                "--compact",
            ]
        )
