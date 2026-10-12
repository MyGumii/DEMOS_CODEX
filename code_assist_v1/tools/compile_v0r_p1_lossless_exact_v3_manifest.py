"""Compile the immutable V0R-P1 v3 ingest manifest from a capture journal.

The capture operator records only the semantic observation and transport facts
for each state. This compiler derives filenames, hashes, dimensions, expected
states, and the exact ingest contract from the actual source-lossless PNGs.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from code_assist_v1.tools import seal_v0r_p1_lossless_exact_v3_20260830 as v3
from code_assist_v1.tools import verify_v0r_p1_lossless_capture_ingest as ingest
from code_assist_v1.tools.notion_parity.exact_pixel import (
    canonical_sha256,
    decode_png_srgb_rgba8,
)
from code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_execution import (
    EXPECTED_STATES,
)


JOURNAL_CONTRACT = "code_assist.notion.v0r_p1.lossless_capture_journal.v1"
EXPECTED_VIEWPORTS = ((1280, 800), (1440, 900))


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {value}")
        ),
    )


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")


def _valid_time(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def compile_manifest(
    journal_path: Path,
    *,
    screenshot_root: Path,
    expected_states: Sequence[tuple[str, str, str, str, str]] = EXPECTED_STATES,
    expected_viewports: Sequence[tuple[int, int]] = EXPECTED_VIEWPORTS,
) -> dict[str, Any]:
    journal = _load(journal_path)
    if not isinstance(journal, Mapping):
        raise ValueError("V0R_V3_JOURNAL_OBJECT_REQUIRED")
    if (
        journal.get("schema_version") != 1
        or journal.get("contract") != JOURNAL_CONTRACT
        or journal.get("packet_id") != "V0R-P1"
    ):
        raise ValueError("V0R_V3_JOURNAL_IDENTITY_DRIFT")
    capture_surface = journal.get("capture_surface")
    if not isinstance(capture_surface, str) or not capture_surface:
        raise ValueError("V0R_V3_JOURNAL_CAPTURE_SURFACE_REQUIRED")
    if not _valid_time(journal.get("recorded_at")):
        raise ValueError("V0R_V3_JOURNAL_TIME_INVALID")

    state_spec = {row[0]: row for row in expected_states}
    expected_keys = {
        (state_id, width, height)
        for state_id in state_spec
        for width, height in expected_viewports
    }
    journal_rows = journal.get("captures")
    if not isinstance(journal_rows, list):
        raise ValueError("V0R_V3_JOURNAL_CAPTURES_REQUIRED")
    seen: set[tuple[str, int, int]] = set()
    captures: list[dict[str, Any]] = []
    for row in journal_rows:
        if not isinstance(row, Mapping) or not isinstance(
            row.get("viewport"), Mapping
        ):
            raise ValueError("V0R_V3_JOURNAL_CAPTURE_SHAPE")
        viewport = row["viewport"]
        key = (
            row.get("state_id"),
            viewport.get("width"),
            viewport.get("height"),
        )
        if key not in expected_keys or key in seen:
            raise ValueError(f"V0R_V3_JOURNAL_CAPTURE_KEY_DRIFT:{key!r}")
        seen.add(key)
        if row.get("browser_capture_returned_codec") != "png":
            raise ValueError(f"V0R_V3_JOURNAL_SOURCE_CODEC_NOT_PNG:{key!r}")
        if row.get("source_bytes_written_directly") is not True:
            raise ValueError(f"V0R_V3_JOURNAL_SOURCE_BYTES_NOT_DIRECT:{key!r}")
        if row.get("semantic_postcondition_status") != "verified_after_transition":
            raise ValueError(f"V0R_V3_JOURNAL_POSTCONDITION_NOT_VERIFIED:{key!r}")
        if row.get("capture_after_postcondition") is not True:
            raise ValueError(f"V0R_V3_JOURNAL_CAPTURE_TOO_EARLY:{key!r}")
        if not _valid_time(row.get("captured_at")):
            raise ValueError(f"V0R_V3_JOURNAL_CAPTURE_TIME_INVALID:{key!r}")
        observation = row.get("semantic_observation")
        if not isinstance(observation, str) or not observation:
            raise ValueError(f"V0R_V3_JOURNAL_OBSERVATION_REQUIRED:{key!r}")

        state_id, width, height = key
        name = f"{state_id}__{width}x{height}.png"
        screenshot = screenshot_root / name
        if not screenshot.is_file():
            raise ValueError(f"V0R_V3_JOURNAL_SCREENSHOT_MISSING:{name}")
        payload = screenshot.read_bytes()
        decoded = decode_png_srgb_rgba8(payload)
        if (decoded.width, decoded.height) != (width, height):
            raise ValueError(f"V0R_V3_JOURNAL_SCREENSHOT_GEOMETRY:{name}")
        screenshot_sha256 = _sha256_bytes(payload)
        captures.append(
            {
                "scenario_id": f"{state_id}__{width}x{height}",
                "state_id": state_id,
                "viewport": {"width": width, "height": height},
                "expected_state": state_spec[str(state_id)][4],
                "browser_capture_returned_codec": "png",
                "source_relative_path": name,
                "source_sha256": screenshot_sha256,
                "screenshot_relative_path": name,
                "screenshot_sha256": screenshot_sha256,
                "source_bytes_copied_without_reencode": True,
                "semantic_postcondition_status": "verified_after_transition",
                "capture_after_postcondition": True,
                "captured_at": row["captured_at"],
                "semantic_observation": observation,
            }
        )
    if seen != expected_keys or len(journal_rows) != len(expected_keys):
        raise ValueError("V0R_V3_JOURNAL_DENOMINATOR_DRIFT")
    captures.sort(
        key=lambda row: (
            row["state_id"],
            row["viewport"]["width"],
            row["viewport"]["height"],
        )
    )
    return {
        "schema_version": 1,
        "contract": ingest.CONTRACT,
        "packet_id": "V0R-P1",
        "recorded_at": journal["recorded_at"],
        "capture_surface": capture_surface,
        "capture_journal": {
            "contract": JOURNAL_CONTRACT,
            "sha256": _sha256_file(journal_path),
        },
        "capture_transport": {
            "browser_capture_returned_codec": "png",
            "stored_codec": "png_srgb_rgba8",
            "conversion": "none_source_bytes_copied_without_reencode",
            "source_bytes_preserved": True,
            "source_encoding_lossless": True,
            "zero_RGBA_equivalence_claimed": True,
        },
        "captures": captures,
        "capture_set_sha256": canonical_sha256(captures),
    }


def write_immutable_manifest(path: Path, manifest: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_bytes(manifest)
    with path.open("xb") as handle:
        handle.write(payload)
    return _sha256_bytes(payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument(
        "--screenshot-root",
        type=Path,
        default=v3._path(v3.SCREENSHOT_REPO_ROOT),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=v3._path(v3.V3_INGEST_MANIFEST_REPO_PATH),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = compile_manifest(
        args.journal,
        screenshot_root=args.screenshot_root,
    )
    v3._validate_semantic_postconditions(manifest)
    runtime_root = v3.sealer.PROJECT_ROOT / ".runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".json",
            prefix="v0r-v3-ingest-manifest-",
            dir=runtime_root,
            delete=False,
        ) as handle:
            handle.write(_json_bytes(manifest))
            temporary_path = Path(handle.name)
        audit = ingest.verify(
            temporary_path,
            source_root=args.screenshot_root,
            screenshot_root=args.screenshot_root,
        )
        if (
            audit.get("status") != "pass"
            or audit.get("exact_reference_transport_eligible") is not True
        ):
            raise ValueError(
                "V0R_V3_COMPILED_MANIFEST_AUDIT_FAILED:" + json.dumps(audit)
            )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    manifest_sha256 = write_immutable_manifest(args.output, manifest)
    print(
        json.dumps(
            {
                "status": "pass",
                "manifest_path": str(args.output),
                "manifest_sha256": manifest_sha256,
                "capture_count": 36,
                "capture_manifest_sha256": audit["capture_manifest_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
