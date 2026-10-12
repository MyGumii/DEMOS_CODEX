from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools import compile_v0r_p1_lossless_exact_v3_manifest as compiler
from code_assist_v1.tools.notion_parity.exact_pixel import encode_rgba_png


TEST_STATES = (
    ("V0R-MU-004", "REF-FREE-B", "REF-OWNER", "page_share_dialog", "edit_access_active"),
    (
        "V0R-MU-006",
        "REF-FREE-A",
        "REF-COLLABORATOR",
        "page_share_dialog",
        "inherited_workspace_access_explained",
    ),
)
TEST_VIEWPORTS = ((4, 3), (5, 4))


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, object]]:
    screenshot_root = tmp_path / "screenshots"
    screenshot_root.mkdir()
    rows = []
    for state_index, state in enumerate(TEST_STATES):
        for width, height in TEST_VIEWPORTS:
            name = f"{state[0]}__{width}x{height}.png"
            pixels = bytes((state_index + 1, width, height, 255)) * (width * height)
            (screenshot_root / name).write_bytes(encode_rgba_png(width, height, pixels))
            rows.append(
                {
                    "state_id": state[0],
                    "viewport": {"width": width, "height": height},
                    "browser_capture_returned_codec": "png",
                    "source_bytes_written_directly": True,
                    "semantic_postcondition_status": "verified_after_transition",
                    "capture_after_postcondition": True,
                    "captured_at": "2026-08-30T18:00:00+09:00",
                    "semantic_observation": f"{state[4]} visible after transition.",
                }
            )
    journal: dict[str, object] = {
        "schema_version": 1,
        "contract": compiler.JOURNAL_CONTRACT,
        "packet_id": "V0R-P1",
        "recorded_at": "2026-08-30T18:01:00+09:00",
        "capture_surface": "approved-lossless-surface",
        "captures": rows,
    }
    journal_path = tmp_path / "journal.json"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    return journal_path, screenshot_root, journal


def _compile(journal_path: Path, screenshot_root: Path) -> dict[str, object]:
    return compiler.compile_manifest(
        journal_path,
        screenshot_root=screenshot_root,
        expected_states=TEST_STATES,
        expected_viewports=TEST_VIEWPORTS,
    )


@pytest.mark.unit
def test_v0r_v3_manifest_compiler_derives_png_hashes_and_expected_states(
    tmp_path: Path,
) -> None:
    journal_path, screenshot_root, _ = _fixture(tmp_path)

    manifest = _compile(journal_path, screenshot_root)

    assert manifest["contract"] == compiler.ingest.CONTRACT
    assert len(manifest["captures"]) == 4
    assert all(
        row["source_sha256"] == row["screenshot_sha256"]
        and row["source_relative_path"] == row["screenshot_relative_path"]
        and row["source_bytes_copied_without_reencode"] is True
        for row in manifest["captures"]
    )
    states = {row["state_id"]: row["expected_state"] for row in manifest["captures"]}
    assert states == {
        "V0R-MU-004": "edit_access_active",
        "V0R-MU-006": "inherited_workspace_access_explained",
    }


@pytest.mark.unit
def test_v0r_v3_manifest_compiler_rejects_jpeg_transport(tmp_path: Path) -> None:
    journal_path, screenshot_root, journal = _fixture(tmp_path)
    journal["captures"][0]["browser_capture_returned_codec"] = "jpeg"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(ValueError, match="SOURCE_CODEC_NOT_PNG"):
        _compile(journal_path, screenshot_root)


@pytest.mark.unit
def test_v0r_v3_manifest_compiler_rejects_missing_denominator(tmp_path: Path) -> None:
    journal_path, screenshot_root, journal = _fixture(tmp_path)
    journal["captures"].pop()
    journal_path.write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(ValueError, match="DENOMINATOR_DRIFT"):
        _compile(journal_path, screenshot_root)


@pytest.mark.unit
def test_v0r_v3_manifest_compiler_rejects_png_geometry_drift(tmp_path: Path) -> None:
    journal_path, screenshot_root, journal = _fixture(tmp_path)
    row = journal["captures"][0]
    name = f"{row['state_id']}__{row['viewport']['width']}x{row['viewport']['height']}.png"
    (screenshot_root / name).write_bytes(
        encode_rgba_png(2, 2, bytes((1, 2, 3, 255)) * 4)
    )

    with pytest.raises(ValueError, match="SCREENSHOT_GEOMETRY"):
        _compile(journal_path, screenshot_root)


@pytest.mark.unit
def test_v0r_v3_manifest_compiler_writes_exclusively(tmp_path: Path) -> None:
    journal_path, screenshot_root, _ = _fixture(tmp_path)
    manifest = _compile(journal_path, screenshot_root)
    output = tmp_path / "manifest.json"

    first_hash = compiler.write_immutable_manifest(output, manifest)

    assert first_hash.startswith("sha256:")
    with pytest.raises(FileExistsError):
        compiler.write_immutable_manifest(output, deepcopy(manifest))
