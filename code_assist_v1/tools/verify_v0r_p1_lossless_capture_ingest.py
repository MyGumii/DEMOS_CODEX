"""Fail-closed preflight for source-lossless V0R-P1 reference captures.

The immutable reference receipt may describe a transport as exact only after
this audit proves that the capture surface returned PNG bytes and that those
same bytes were copied into the evidence screenshot root without re-encoding.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from code_assist_v1.tools.notion_parity.exact_pixel import (
    canonical_sha256,
    decode_png_srgb_rgba8,
)


CONTRACT = "code_assist.notion.v0r_p1.lossless_capture_ingest_manifest.v1"
AUDIT_CONTRACT = "code_assist.notion.v0r_p1.lossless_capture_ingest_audit.v1"
EXPECTED_STATES = tuple(f"V0R-MU-{index:03d}" for index in range(1, 19))
EXPECTED_VIEWPORTS = ((1280, 800), (1440, 900))
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


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


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")


def _safe_png(root: Path, relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    pure = PurePosixPath(relative)
    if pure.is_absolute() or pure.suffix.lower() != ".png" or ".." in pure.parts:
        return None
    candidate = (root / Path(*pure.parts)).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve(strict=False))
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _error(
    errors: list[dict[str, str]], code: str, location: str, detail: str
) -> None:
    errors.append({"code": code, "location": location, "detail": detail})


def _empty_audit(errors: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "contract": AUDIT_CONTRACT,
        "status": "fail",
        "error_count": len(errors),
        "errors": errors,
        "capture_count": 0,
        "capture_state_count": 0,
        "viewport_count": 0,
        "source_bytes_preserved_count": 0,
        "exact_reference_transport_eligible": False,
        "capture_transport": {
            "browser_capture_returned_codec": "",
            "stored_codec": "",
            "conversion": "",
            "source_bytes_preserved": False,
            "source_encoding_lossless": False,
            "zero_RGBA_equivalence_claimed": False,
        },
        "capture_checks": [],
    }


def verify(
    manifest_path: Path,
    *,
    source_root: Path,
    screenshot_root: Path,
    expected_states: Sequence[str] = EXPECTED_STATES,
    expected_viewports: Sequence[tuple[int, int]] = EXPECTED_VIEWPORTS,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    try:
        manifest = _load(manifest_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        _error(errors, "MANIFEST_LOAD", "$", str(exc))
        return _empty_audit(errors)
    if not isinstance(manifest, dict):
        _error(errors, "MANIFEST_TYPE", "$", "manifest must be a JSON object")
        return _empty_audit(errors)

    if manifest.get("schema_version") != 1:
        _error(errors, "SCHEMA_VERSION", "$.schema_version", "expected 1")
    if manifest.get("contract") != CONTRACT:
        _error(errors, "CONTRACT", "$.contract", f"expected {CONTRACT}")
    if manifest.get("packet_id") != "V0R-P1":
        _error(errors, "PACKET_ID", "$.packet_id", "expected V0R-P1")

    transport = manifest.get("capture_transport")
    if not isinstance(transport, dict):
        _error(
            errors,
            "CAPTURE_TRANSPORT",
            "$.capture_transport",
            "object required",
        )
        transport = {}
    expected_transport = {
        "browser_capture_returned_codec": "png",
        "stored_codec": "png_srgb_rgba8",
        "conversion": "none_source_bytes_copied_without_reencode",
        "source_bytes_preserved": True,
        "source_encoding_lossless": True,
        "zero_RGBA_equivalence_claimed": True,
    }
    for key, expected in expected_transport.items():
        actual = transport.get(key)
        if actual != expected:
            _error(
                errors,
                "CAPTURE_TRANSPORT_DECLARATION",
                f"$.capture_transport.{key}",
                f"expected {expected!r}, got {actual!r}",
            )

    expected_keys = {
        (state_id, width, height)
        for state_id in expected_states
        for width, height in expected_viewports
    }
    captures = manifest.get("captures")
    if not isinstance(captures, list):
        _error(errors, "CAPTURES", "$.captures", "array required")
        captures = []

    seen: set[tuple[str, int, int]] = set()
    checks: list[dict[str, Any]] = []
    source_bytes_preserved_count = 0
    for index, capture in enumerate(captures):
        location = f"$.captures[{index}]"
        if not isinstance(capture, dict):
            _error(errors, "CAPTURE_TYPE", location, "object required")
            continue
        state_id = capture.get("state_id")
        viewport = capture.get("viewport")
        width = viewport.get("width") if isinstance(viewport, dict) else None
        height = viewport.get("height") if isinstance(viewport, dict) else None
        key = (state_id, width, height)
        if key not in expected_keys:
            _error(errors, "CAPTURE_KEY", location, f"unexpected capture key {key!r}")
            continue
        if key in seen:
            _error(errors, "CAPTURE_DUPLICATE", location, f"duplicate capture key {key!r}")
            continue
        seen.add(key)
        scenario_id = f"{state_id}__{width}x{height}"
        if capture.get("scenario_id") != scenario_id:
            _error(
                errors,
                "SCENARIO_ID",
                f"{location}.scenario_id",
                f"expected {scenario_id}",
            )
        if capture.get("browser_capture_returned_codec") != "png":
            _error(
                errors,
                "SOURCE_CODEC_NOT_PNG",
                f"{location}.browser_capture_returned_codec",
                "capture surface must report png",
            )
        if capture.get("source_bytes_copied_without_reencode") is not True:
            _error(
                errors,
                "SOURCE_COPY_NOT_DECLARED",
                f"{location}.source_bytes_copied_without_reencode",
                "must be true",
            )

        source = _safe_png(source_root, capture.get("source_relative_path"))
        stored = _safe_png(
            screenshot_root, capture.get("screenshot_relative_path")
        )
        if source is None:
            _error(
                errors,
                "SOURCE_PATH",
                f"{location}.source_relative_path",
                "safe existing PNG required beneath source root",
            )
        if stored is None:
            _error(
                errors,
                "SCREENSHOT_PATH",
                f"{location}.screenshot_relative_path",
                "safe existing PNG required beneath screenshot root",
            )
        if source is None or stored is None:
            continue

        source_bytes = source.read_bytes()
        stored_bytes = stored.read_bytes()
        source_sha256 = _sha256_bytes(source_bytes)
        stored_sha256 = _sha256_bytes(stored_bytes)
        if capture.get("source_sha256") != source_sha256:
            _error(
                errors,
                "SOURCE_HASH",
                f"{location}.source_sha256",
                "declared hash does not match source bytes",
            )
        if capture.get("screenshot_sha256") != stored_sha256:
            _error(
                errors,
                "SCREENSHOT_HASH",
                f"{location}.screenshot_sha256",
                "declared hash does not match stored bytes",
            )
        if not source_bytes.startswith(PNG_SIGNATURE):
            _error(
                errors,
                "SOURCE_PNG_SIGNATURE",
                f"{location}.source_relative_path",
                "source bytes are not PNG",
            )
        if not stored_bytes.startswith(PNG_SIGNATURE):
            _error(
                errors,
                "SCREENSHOT_PNG_SIGNATURE",
                f"{location}.screenshot_relative_path",
                "stored bytes are not PNG",
            )

        decoded_geometry: dict[str, int] | None = None
        try:
            decoded = decode_png_srgb_rgba8(source_bytes)
            decoded_geometry = {"width": decoded.width, "height": decoded.height}
            if (decoded.width, decoded.height) != (width, height):
                _error(
                    errors,
                    "SOURCE_DIMENSIONS",
                    f"{location}.viewport",
                    "decoded source dimensions do not match the manifest",
                )
        except ValueError as exc:
            _error(
                errors,
                "SOURCE_PNG_DECODE",
                f"{location}.source_relative_path",
                str(exc),
            )

        bytes_identical = source_bytes == stored_bytes
        if not bytes_identical:
            _error(
                errors,
                "SOURCE_BYTES_NOT_PRESERVED",
                location,
                "stored screenshot differs from capture-returned source bytes",
            )
        else:
            source_bytes_preserved_count += 1
        checks.append(
            {
                "scenario_id": scenario_id,
                "source_sha256": source_sha256,
                "screenshot_sha256": stored_sha256,
                "source_bytes_preserved": bytes_identical,
                "decoded_geometry": decoded_geometry,
            }
        )

    missing = sorted(expected_keys - seen)
    if missing:
        _error(
            errors,
            "CAPTURE_DENOMINATOR_MISSING",
            "$.captures",
            f"missing {len(missing)} capture keys",
        )
    if len(captures) != len(expected_keys):
        _error(
            errors,
            "CAPTURE_DENOMINATOR_COUNT",
            "$.captures",
            f"expected {len(expected_keys)}, found {len(captures)}",
        )

    eligible = not errors and source_bytes_preserved_count == len(expected_keys)
    normalized_transport = {
        key: transport.get(key) for key in expected_transport
    }
    return {
        "schema_version": 1,
        "contract": AUDIT_CONTRACT,
        "status": "pass" if eligible else "fail",
        "error_count": len(errors),
        "errors": errors,
        "capture_count": len(captures),
        "capture_state_count": len({key[0] for key in seen}),
        "viewport_count": len({(key[1], key[2]) for key in seen}),
        "expected_capture_count": len(expected_keys),
        "source_bytes_preserved_count": source_bytes_preserved_count,
        "exact_reference_transport_eligible": eligible,
        "capture_transport": normalized_transport,
        "capture_manifest_sha256": canonical_sha256(manifest),
        "capture_checks": checks,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--screenshot-root", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        help="write the audit with exclusive-create semantics",
    )
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    audit = verify(
        args.manifest,
        source_root=args.source_root,
        screenshot_root=args.screenshot_root,
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as handle:
            handle.write(_json_bytes(audit))
    print(
        json.dumps(
            audit,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.compact else 2,
        )
    )
    return 0 if audit["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
