"""Verify complete V0R-P1 external reference capture and cleanup evidence."""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping, Sequence

from code_assist_v1.tools.internal_collaborative_reference_multiuser_approval import (
    verify_approval_receipt,
)
from code_assist_v1.tools.notion_parity.exact_pixel import (
    ExactPixelContractError,
    canonical_sha256,
    decode_png_srgb_rgba8,
    validate_capture_environment,
    validate_mask_manifest,
)
from code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_execution import (
    CONTRACT_PATH as EXECUTION_MANIFEST_PATH,
    EXPECTED_ACTIONS,
    EXPECTED_STATES,
    verify as verify_execution_manifest,
)
from code_assist_v1.tools.verify_v0r_p1_lossless_capture_ingest import (
    AUDIT_CONTRACT as LOSSLESS_INGEST_AUDIT_CONTRACT,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
RECEIPT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "evidence"
    / "internal_collaboration_receipts"
    / "v0r-p1-l6-reference-multiuser-20260825.json"
)
APPROVAL_RECEIPT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "evidence"
    / "internal_collaboration_approvals"
    / "v0r-p1-ap-reference-multiuser-20260825.json"
)
SCREENSHOT_ROOT = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "evidence"
    / "internal_collaboration_reference"
    / "v0r-p1"
    / "screenshots"
)
CONTRACT = "code_assist.notion.internal_collaborative_reference_multiuser_receipt.v0r_p1.v1"
MANIFEST_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/core_free_web_ui/"
    "internal_collaborative_reference_multiuser_execution.v1.json"
)
APPROVAL_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_approvals/"
    "v0r-p1-ap-reference-multiuser-20260825.json"
)
SCREENSHOT_REPO_ROOT = (
    "code_assist_v1/docs/notion_parity/evidence/"
    "internal_collaboration_reference/v0r-p1/screenshots"
)
ORIGINAL_RECEIPT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/"
    "v0r-p1-l6-reference-multiuser-20260825.json"
)
RECEIPT_REPO_ROOT = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts"
)
SUPERSESSION_REFERENCE_ROOT_PREFIXES = (
    "code_assist_v1/docs/notion_parity/evidence/"
    "internal_collaboration_reference/v0r-p1-corrective/",
    "code_assist_v1/docs/notion_parity/evidence/"
    "internal_collaboration_reference/v0r-p1-lossless-",
)
EXPECTED_CAPTURE_KEYS = {
    (state[0], width, height)
    for state in EXPECTED_STATES
    for width, height in ((1440, 900), (1280, 800))
}
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_PNG_GEOMETRY_CACHE: dict[tuple[str, int], tuple[int, int]] = {}
_DEFAULT_PNG_DECODER = decode_png_srgb_rgba8


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _valid_time(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def _safe_sibling_json(receipt_path: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    pure = PurePosixPath(value)
    if pure.is_absolute() or pure.suffix.lower() != ".json" or ".." in pure.parts:
        return None
    root = receipt_path.parent.resolve(strict=False)
    candidate = (root / Path(*pure.parts)).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _lossless_ingest_binding(
    receipt: Mapping[str, Any],
    transport: Mapping[str, Any],
    *,
    receipt_path: Path,
) -> dict[str, Any]:
    binding = transport.get("lossless_ingest_audit")
    if not isinstance(binding, Mapping):
        return {
            "declared": False,
            "valid": False,
            "relative_path": "",
            "sha256": "",
            "capture_manifest_sha256": "",
            "reason_codes": ["LOSSLESS_INGEST_AUDIT_NOT_DECLARED"],
        }

    relative_path = binding.get("relative_path")
    declared_sha256 = binding.get("sha256")
    declared_manifest_sha256 = binding.get("capture_manifest_sha256")
    reason_codes: list[str] = []
    audit_path = _safe_sibling_json(receipt_path, relative_path)
    if audit_path is None:
        reason_codes.append("LOSSLESS_INGEST_AUDIT_PATH")
    if not isinstance(declared_sha256, str) or not _SHA256_RE.fullmatch(
        declared_sha256
    ):
        reason_codes.append("LOSSLESS_INGEST_AUDIT_HASH_DECLARATION")
    if not isinstance(declared_manifest_sha256, str) or not _SHA256_RE.fullmatch(
        declared_manifest_sha256
    ):
        reason_codes.append("LOSSLESS_INGEST_MANIFEST_HASH_DECLARATION")

    audit: Mapping[str, Any] | None = None
    actual_sha256 = ""
    if audit_path is not None:
        actual_sha256 = _sha256_file(audit_path)
        if actual_sha256 != declared_sha256:
            reason_codes.append("LOSSLESS_INGEST_AUDIT_HASH_DRIFT")
        try:
            loaded = _load(audit_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            reason_codes.append("LOSSLESS_INGEST_AUDIT_LOAD")
        else:
            if isinstance(loaded, Mapping):
                audit = loaded
            else:
                reason_codes.append("LOSSLESS_INGEST_AUDIT_TYPE")

    capture_count = len(receipt.get("captures", [])) if isinstance(
        receipt.get("captures"), list
    ) else 0
    if audit is not None:
        if audit.get("contract") != LOSSLESS_INGEST_AUDIT_CONTRACT:
            reason_codes.append("LOSSLESS_INGEST_AUDIT_CONTRACT")
        if (
            audit.get("status") != "pass"
            or audit.get("error_count") != 0
            or audit.get("exact_reference_transport_eligible") is not True
        ):
            reason_codes.append("LOSSLESS_INGEST_AUDIT_NOT_PASS")
        if audit.get("capture_manifest_sha256") != declared_manifest_sha256:
            reason_codes.append("LOSSLESS_INGEST_MANIFEST_HASH_DRIFT")
        if (
            audit.get("capture_count") != capture_count
            or audit.get("expected_capture_count") != len(EXPECTED_CAPTURE_KEYS)
            or audit.get("capture_state_count") != len(EXPECTED_STATES)
            or audit.get("viewport_count") != 2
            or audit.get("source_bytes_preserved_count")
            != len(EXPECTED_CAPTURE_KEYS)
        ):
            reason_codes.append("LOSSLESS_INGEST_AUDIT_DENOMINATOR")

        audit_transport = audit.get("capture_transport")
        expected_transport = {
            "browser_capture_returned_codec": "png",
            "stored_codec": "png_srgb_rgba8",
            "conversion": "none_source_bytes_copied_without_reencode",
            "source_bytes_preserved": True,
            "source_encoding_lossless": True,
            "zero_RGBA_equivalence_claimed": True,
        }
        if not isinstance(audit_transport, Mapping) or any(
            audit_transport.get(key) != expected
            for key, expected in expected_transport.items()
        ):
            reason_codes.append("LOSSLESS_INGEST_AUDIT_TRANSPORT")
        if any(transport.get(key) != expected for key, expected in expected_transport.items()):
            reason_codes.append("LOSSLESS_INGEST_RECEIPT_TRANSPORT")

        receipt_scenarios = {
            f"{capture.get('state_id')}__{viewport.get('width')}x{viewport.get('height')}"
            for capture in receipt.get("captures", [])
            if isinstance(capture, Mapping)
            and isinstance((viewport := capture.get("viewport")), Mapping)
        }
        checks = audit.get("capture_checks")
        audit_scenarios = {
            check.get("scenario_id")
            for check in checks
            if isinstance(check, Mapping)
            and check.get("source_bytes_preserved") is True
        } if isinstance(checks, list) else set()
        if (
            receipt_scenarios != audit_scenarios
            or len(audit_scenarios) != len(EXPECTED_CAPTURE_KEYS)
        ):
            reason_codes.append("LOSSLESS_INGEST_AUDIT_SCENARIOS")

    return {
        "declared": True,
        "valid": not reason_codes,
        "relative_path": relative_path if isinstance(relative_path, str) else "",
        "sha256": actual_sha256,
        "capture_manifest_sha256": (
            declared_manifest_sha256
            if isinstance(declared_manifest_sha256, str)
            else ""
        ),
        "reason_codes": reason_codes,
    }


def _capture_transport_classification(
    receipt: Mapping[str, Any], *, receipt_path: Path
) -> dict[str, Any]:
    transport = receipt.get("capture_transport")
    if not isinstance(transport, Mapping):
        return {
            "declared": False,
            "eligible": False,
            "browser_capture_returned_codec": "",
            "stored_codec": "",
            "source_encoding_lossless": False,
            "zero_RGBA_equivalence_claimed": False,
            "lossless_ingest_audit": {
                "declared": False,
                "valid": False,
                "relative_path": "",
                "sha256": "",
                "capture_manifest_sha256": "",
                "reason_codes": [],
            },
            "reason_codes": ["CAPTURE_TRANSPORT_NOT_DECLARED"],
        }

    source_codec = str(transport.get("browser_capture_returned_codec") or "").lower()
    stored_codec = str(transport.get("stored_codec") or "").lower()
    source_encoding_lossless = transport.get("source_encoding_lossless") is True
    zero_rgba_equivalence = transport.get("zero_RGBA_equivalence_claimed") is True
    reason_codes: list[str] = []
    if source_codec != "png":
        reason_codes.append("SOURCE_CODEC_NOT_PNG")
    if stored_codec != "png_srgb_rgba8":
        reason_codes.append("STORED_CODEC_NOT_PNG_SRGB_RGBA8")
    if not source_encoding_lossless:
        reason_codes.append("SOURCE_ENCODING_NOT_LOSSLESS")
    if not zero_rgba_equivalence:
        reason_codes.append("ZERO_RGBA_EQUIVALENCE_NOT_DECLARED")
    ingest_binding = {
        "declared": False,
        "valid": False,
        "relative_path": "",
        "sha256": "",
        "capture_manifest_sha256": "",
        "reason_codes": [],
    }
    if not reason_codes:
        ingest_binding = _lossless_ingest_binding(
            receipt, transport, receipt_path=receipt_path
        )
        reason_codes.extend(ingest_binding["reason_codes"])
    return {
        "declared": True,
        "eligible": not reason_codes,
        "browser_capture_returned_codec": source_codec,
        "stored_codec": stored_codec,
        "source_encoding_lossless": source_encoding_lossless,
        "zero_RGBA_equivalence_claimed": zero_rgba_equivalence,
        "lossless_ingest_audit": ingest_binding,
        "reason_codes": reason_codes,
    }


def _safe_receipt_repo_path(value: Any) -> tuple[str, Path] | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    pure = PurePosixPath(value)
    expected_root = PurePosixPath(RECEIPT_REPO_ROOT)
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or pure.suffix.lower() != ".json"
        or pure.parts[: len(expected_root.parts)] != expected_root.parts
    ):
        return None
    filesystem_path = (REPOSITORY_ROOT / Path(*pure.parts)).resolve(strict=False)
    receipt_root = (
        REPOSITORY_ROOT / Path(*expected_root.parts)
    ).resolve(strict=False)
    try:
        filesystem_path.relative_to(receipt_root)
    except ValueError:
        return None
    if not filesystem_path.is_file():
        return None
    return pure.as_posix(), filesystem_path


def _supersession_chain(receipt: Mapping[str, Any]) -> dict[str, Any]:
    current: Mapping[str, Any] = receipt
    links: list[dict[str, Any]] = []
    reason_codes: list[str] = []
    seen: set[str] = set()
    root_receipt_path = ""

    for _ in range(16):
        supersession = current.get("supersession")
        if supersession is None:
            root_receipt_path = links[-1]["path"] if links else ""
            if root_receipt_path != ORIGINAL_RECEIPT_REPO_PATH:
                reason_codes.append("SUPERSESSION_ROOT_MISMATCH")
            break
        if not isinstance(supersession, Mapping):
            reason_codes.append("SUPERSESSION_LINK_SHAPE")
            break
        if not (
            supersession.get("mode") == "pixel_reference_only"
            and supersession.get("preserves_original_semantic_evidence") is True
            and supersession.get("original_receipt_modified") is False
            and supersession.get("authority_received") is True
        ):
            reason_codes.append("SUPERSESSION_LINK_CONTRACT")

        resolved = _safe_receipt_repo_path(
            supersession.get("supersedes_receipt_path")
        )
        if resolved is None:
            reason_codes.append("SUPERSESSION_LINK_PATH")
            break
        repo_path, filesystem_path = resolved
        if repo_path in seen:
            reason_codes.append("SUPERSESSION_CHAIN_CYCLE")
            break
        seen.add(repo_path)
        actual_sha256 = _sha256_file(filesystem_path)
        expected_sha256 = supersession.get("supersedes_receipt_sha256")
        if expected_sha256 != actual_sha256:
            reason_codes.append("SUPERSESSION_LINK_HASH")
        links.append(
            {
                "path": repo_path,
                "expected_sha256": expected_sha256,
                "actual_sha256": actual_sha256,
                "hash_matches": expected_sha256 == actual_sha256,
            }
        )
        try:
            previous = _load(filesystem_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            reason_codes.append("SUPERSESSION_LINK_READ")
            break
        if not isinstance(previous, Mapping):
            reason_codes.append("SUPERSESSION_LINK_RECEIPT_SHAPE")
            break
        current = previous
    else:
        reason_codes.append("SUPERSESSION_CHAIN_DEPTH")

    return {
        "present": True,
        "valid": not reason_codes,
        "link_count": len(links),
        "links": links,
        "root_receipt_path": root_receipt_path,
        "reaches_original_semantic_receipt": (
            root_receipt_path == ORIGINAL_RECEIPT_REPO_PATH
        ),
        "reason_codes": sorted(set(reason_codes)),
    }


def _safe_screenshot_path(root: Path, relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or pure.suffix.lower() != ".png":
        return None
    candidate = (root / Path(*pure.parts)).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _safe_json_path(root: Path, relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or pure.suffix.lower() != ".json":
        return None
    candidate = (root / Path(*pure.parts)).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _strip_hash(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    return {key: child for key, child in value.items() if key != field}


def _masked_union_area(rectangles: Sequence[Mapping[str, int]]) -> int:
    """Return the exact integer-pixel union area without materializing pixels."""

    if not rectangles:
        return 0
    minimum_y = min(rectangle["y"] for rectangle in rectangles)
    maximum_y = max(
        rectangle["y"] + rectangle["height"] for rectangle in rectangles
    )
    area = 0
    for row in range(minimum_y, maximum_y):
        intervals = sorted(
            (rectangle["x"], rectangle["x"] + rectangle["width"])
            for rectangle in rectangles
            if rectangle["y"] <= row < rectangle["y"] + rectangle["height"]
        )
        if not intervals:
            continue
        start, end = intervals[0]
        for next_start, next_end in intervals[1:]:
            if next_start > end:
                area += end - start
                start, end = next_start, next_end
            else:
                end = max(end, next_end)
        area += end - start
    return area


def _png_hash_and_geometry(payload: bytes) -> tuple[str, tuple[int, int]]:
    """Hash PNG bytes and reuse only geometry from an identical decoded payload."""

    content_sha256 = "sha256:" + hashlib.sha256(payload).hexdigest()
    cache_key = (content_sha256, id(decode_png_srgb_rgba8))
    geometry = _PNG_GEOMETRY_CACHE.get(cache_key)
    if geometry is None:
        image = decode_png_srgb_rgba8(payload)
        geometry = (image.width, image.height)
        _PNG_GEOMETRY_CACHE[cache_key] = geometry
    return content_sha256, geometry


def _screenshot_metadata_task(
    task: tuple[int, Path],
) -> tuple[int, str, tuple[int, int]]:
    index, screenshot = task
    content_sha256, geometry = _png_hash_and_geometry(screenshot.read_bytes())
    return index, content_sha256, geometry


def _parallel_screenshot_metadata(
    captures: Any,
    *,
    screenshot_root: Path,
) -> dict[int, tuple[str, tuple[int, int]]]:
    """Decode independent screenshots in workers while preserving fallback."""

    if (
        decode_png_srgb_rgba8 is not _DEFAULT_PNG_DECODER
        or not isinstance(captures, list)
        or len(captures) < 2
    ):
        return {}
    tasks: list[tuple[int, Path]] = []
    cache_keys: dict[int, tuple[str, int]] = {}
    results: dict[int, tuple[str, tuple[int, int]]] = {}
    for index, item in enumerate(captures):
        if not isinstance(item, Mapping):
            continue
        screenshot = _safe_screenshot_path(
            screenshot_root,
            item.get("screenshot_relative_path"),
        )
        if screenshot is None:
            continue
        try:
            content_sha256 = "sha256:" + hashlib.sha256(
                screenshot.read_bytes()
            ).hexdigest()
        except OSError:
            continue
        cache_key = (content_sha256, id(decode_png_srgb_rgba8))
        geometry = _PNG_GEOMETRY_CACHE.get(cache_key)
        if geometry is not None:
            results[index] = (content_sha256, geometry)
            continue
        cache_keys[index] = cache_key
        tasks.append((index, screenshot))

    worker_count = min(8, len(tasks), os.cpu_count() or 1)
    if worker_count < 2:
        return results
    try:
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(_screenshot_metadata_task, task): task[0]
                for task in tasks
            }
            for future in as_completed(futures):
                try:
                    index, content_sha256, geometry = future.result()
                except Exception:
                    continue
                if cache_keys.get(index) != (
                    content_sha256,
                    id(decode_png_srgb_rgba8),
                ):
                    continue
                _PNG_GEOMETRY_CACHE[cache_keys[index]] = geometry
                results[index] = (content_sha256, geometry)
    except Exception:
        return results
    return results


def verify(
    path: Path = RECEIPT_PATH,
    *,
    approval_receipt_path: Path = APPROVAL_RECEIPT_PATH,
    screenshot_root: Path = SCREENSHOT_ROOT,
    mask_root: Path | None = None,
    manifest_path: Path = EXECUTION_MANIFEST_PATH,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        receipt = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": "code_assist.notion.internal_collaborative_reference_multiuser_receipt.audit.v1",
            "status": "fail",
            "error_count": 1,
            "errors": [
                {"code": "RECEIPT_READ_FAILED", "subject": str(path), "message": str(exc)}
            ],
        }
    check(isinstance(receipt, dict), "RECEIPT_SHAPE", "$", "receipt must be an object")
    if not isinstance(receipt, dict):
        return {"status": "fail", "error_count": len(errors), "errors": errors}

    check(
        receipt.get("schema_version") == 1
        and receipt.get("contract") == CONTRACT
        and receipt.get("packet_id") == "V0R-P1"
        and isinstance(receipt.get("receipt_id"), str)
        and str(receipt.get("receipt_id", "")).startswith("V0R-P1-L6-REFERENCE-MULTIUSER-")
        and _valid_time(receipt.get("recorded_at")),
        "RECEIPT_IDENTITY",
        "$",
        "V0R-P1 receipt identity, id, or time drift",
    )

    manifest_report = verify_execution_manifest(manifest_path)
    manifest_binding = receipt.get("execution_manifest", {})
    check(
        manifest_report.get("status") == "pass"
        and isinstance(manifest_binding, dict)
        and manifest_binding.get("path") == MANIFEST_REPO_PATH
        and manifest_binding.get("sha256") == _sha256_file(manifest_path)
        and manifest_binding.get("audit_status") == "pass",
        "MANIFEST_BINDING",
        "$.execution_manifest",
        "current audited V0R-P1 execution manifest required",
    )

    approval_report, approval_exit = verify_approval_receipt(
        approval_receipt_path,
        manifest_path=manifest_path,
    )
    approval_binding = receipt.get("approval_receipt", {})
    check(
        approval_exit == 0
        and approval_report.get("status") == "pass"
        and isinstance(approval_binding, dict)
        and approval_binding.get("path") == APPROVAL_REPO_PATH
        and approval_binding.get("sha256") == _sha256_file(approval_receipt_path)
        and approval_binding.get("approval_id") == "AP-REFERENCE-MULTIUSER",
        "APPROVAL_BINDING",
        "$.approval_receipt",
        "exact current owner approval receipt required",
    )

    supersession = receipt.get("supersession")
    supersession_chain: dict[str, Any] = {
        "present": False,
        "valid": path.resolve(strict=False)
        == (REPOSITORY_ROOT / ORIGINAL_RECEIPT_REPO_PATH).resolve(strict=False),
        "link_count": 0,
        "links": [],
        "root_receipt_path": ORIGINAL_RECEIPT_REPO_PATH,
        "reaches_original_semantic_receipt": True,
        "reason_codes": [],
    }
    expected_screenshot_repo_root = SCREENSHOT_REPO_ROOT
    expected_mask_repo_root: str | None = None
    if supersession is not None:
        check(
            isinstance(supersession, dict),
            "SUPERSESSION_SHAPE",
            "$.supersession",
            "supersession must be an object",
        )
        if isinstance(supersession, dict):
            supersession_chain = _supersession_chain(receipt)
            expected_screenshot_repo_root = str(
                supersession.get("corrective_screenshot_root", "")
            )
            expected_mask_repo_root = str(supersession.get("corrective_mask_root", ""))
            check(
                supersession_chain["valid"] is True,
                "SUPERSESSION_BINDING",
                "$.supersession",
                "corrective receipt must preserve a safe immutable hash chain to the original semantic receipt",
            )
            expected_screenshot_path = REPOSITORY_ROOT / Path(
                *PurePosixPath(expected_screenshot_repo_root).parts
            )
            check(
                expected_screenshot_repo_root.startswith(
                    SUPERSESSION_REFERENCE_ROOT_PREFIXES
                )
                and expected_screenshot_repo_root.endswith("/screenshots")
                and screenshot_root.resolve(strict=False)
                == expected_screenshot_path.resolve(strict=False),
                "SUPERSESSION_SCREENSHOT_ROOT",
                "$.supersession.corrective_screenshot_root",
                "corrective screenshot root must match the audited filesystem root",
            )
            if mask_root is None:
                mask_root = screenshot_root.parent / "masks"
            expected_mask_path = REPOSITORY_ROOT / Path(
                *PurePosixPath(expected_mask_repo_root).parts
            )
            check(
                expected_mask_repo_root.startswith(
                    SUPERSESSION_REFERENCE_ROOT_PREFIXES
                )
                and expected_mask_repo_root.endswith("/masks")
                and mask_root.resolve(strict=False)
                == expected_mask_path.resolve(strict=False),
                "SUPERSESSION_MASK_ROOT",
                "$.supersession.corrective_mask_root",
                "corrective mask root must match the audited filesystem root",
            )

    run = receipt.get("run", {})
    check(
        isinstance(run, dict)
        and isinstance(run.get("run_id"), str)
        and str(run.get("run_id", "")).startswith("v0r-p1-")
        and _valid_time(run.get("started_at"))
        and _valid_time(run.get("ended_at"))
        and run.get("browser") == "chrome_signed_in_two_independent_test_sessions"
        and isinstance(run.get("chrome_major_version"), str)
        and str(run.get("chrome_major_version", "")).isdigit()
        and isinstance(run.get("reference_build_identifier"), str)
        and bool(run.get("reference_build_identifier"))
        and _SHA256_RE.fullmatch(str(run.get("reference_build_sha256", ""))) is not None
        and run.get("workspace_aliases") == ["REF-FREE-A", "REF-FREE-B"]
        and run.get("identity_aliases") == ["REF-OWNER", "REF-COLLABORATOR"]
        and run.get("screenshot_root") == expected_screenshot_repo_root,
        "RUN_BOUNDARY",
        "$.run",
        "exact two-session reference run identity and source build required",
    )

    actions = receipt.get("actions")
    expected_action_rows = [
        (index + 1, item[0], item[1], item[2], item[3])
        for index, item in enumerate(EXPECTED_ACTIONS)
    ]
    actual_action_rows = [
        (
            item.get("order"),
            item.get("id"),
            item.get("workspace"),
            item.get("actor"),
            item.get("mutation_class"),
        )
        for item in actions
        if isinstance(item, dict)
    ] if isinstance(actions, list) else []
    check(
        actual_action_rows == expected_action_rows,
        "ACTION_DENOMINATOR",
        "$.actions",
        "exact 11 ordered manifest actions required",
    )
    if isinstance(actions, list):
        for index, item in enumerate(actions):
            location = f"$.actions[{index}]"
            check(
                isinstance(item, dict)
                and item.get("status") == "pass"
                and _valid_time(item.get("started_at"))
                and _valid_time(item.get("ended_at"))
                and isinstance(item.get("result"), str)
                and bool(item.get("result"))
                and item.get("raw_identifier_persisted") is False,
                "ACTION_EVIDENCE",
                location,
                "passed timestamped redacted action evidence required",
            )
    check(
        isinstance(run, dict)
        and isinstance(actions, list)
        and run.get("action_log_sha256") == canonical_sha256(actions),
        "ACTION_LOG_HASH",
        "$.run.action_log_sha256",
        "canonical inline action-log hash required",
    )

    state_map = {
        state[0]: {
            "workspace": state[1],
            "actor": state[2],
            "surface": state[3],
            "state": state[4],
        }
        for state in EXPECTED_STATES
    }
    captures = receipt.get("captures")
    actual_capture_keys: list[tuple[str, int, int]] = []
    seen_relative_paths: set[str] = set()
    seen_mask_paths: set[str] = set()
    corrective_manifest_rows: list[dict[str, Any]] = []
    build_hashes: set[str] = set()
    build_identifiers: set[str] = set()
    parallel_screenshot_metadata = _parallel_screenshot_metadata(
        captures,
        screenshot_root=screenshot_root,
    )
    if isinstance(captures, list):
        for index, item in enumerate(captures):
            location = f"$.captures[{index}]"
            if not isinstance(item, dict):
                check(False, "CAPTURE_SHAPE", location, "capture must be an object")
                continue
            viewport = item.get("viewport", {})
            width = viewport.get("width") if isinstance(viewport, dict) else None
            height = viewport.get("height") if isinstance(viewport, dict) else None
            state_id = item.get("state_id")
            if isinstance(state_id, str) and isinstance(width, int) and isinstance(height, int):
                actual_capture_keys.append((state_id, width, height))
            expected_state = state_map.get(str(state_id), {})
            check(
                bool(expected_state)
                and item.get("workspace_alias") == expected_state.get("workspace")
                and item.get("actor_alias") == expected_state.get("actor")
                and item.get("surface") == expected_state.get("surface")
                and item.get("expected_state") == expected_state.get("state"),
                "CAPTURE_STATE_BINDING",
                location,
                "capture workspace, actor, surface, or expected state drift",
            )
            relative = item.get("screenshot_relative_path")
            screenshot = _safe_screenshot_path(screenshot_root, relative)
            check(
                screenshot is not None,
                "SCREENSHOT_PATH",
                f"{location}.screenshot_relative_path",
                "unique in-root PNG screenshot required",
            )
            check(
                isinstance(relative, str) and relative not in seen_relative_paths,
                "SCREENSHOT_PATH_DUPLICATE",
                f"{location}.screenshot_relative_path",
                "screenshot path must be unique",
            )
            if isinstance(relative, str):
                seen_relative_paths.add(relative)
            screenshot_hash = item.get("screenshot_sha256")
            check(
                isinstance(screenshot_hash, str)
                and _SHA256_RE.fullmatch(screenshot_hash) is not None,
                "SCREENSHOT_HASH_FORMAT",
                f"{location}.screenshot_sha256",
                "canonical screenshot hash required",
            )
            if screenshot is not None:
                try:
                    metadata = parallel_screenshot_metadata.get(index)
                    if metadata is not None:
                        actual_screenshot_hash, image_geometry = metadata
                    else:
                        actual_screenshot_hash, image_geometry = _png_hash_and_geometry(
                            screenshot.read_bytes()
                        )
                except (OSError, ExactPixelContractError) as exc:
                    check(False, "SCREENSHOT_PNG", str(relative), str(exc))
                else:
                    check(
                        actual_screenshot_hash == screenshot_hash,
                        "SCREENSHOT_HASH_DRIFT",
                        str(relative),
                        "screenshot hash drift",
                    )
                    check(
                        image_geometry == (width, height),
                        "SCREENSHOT_GEOMETRY",
                        str(relative),
                        "PNG dimensions must equal the declared viewport at DPR 1",
                    )

            environment = item.get("environment")
            try:
                normalized_environment = validate_capture_environment(environment)
            except (ExactPixelContractError, TypeError) as exc:
                check(False, "ENVIRONMENT_INVALID", f"{location}.environment", str(exc))
                normalized_environment = {}
            check(
                normalized_environment.get("viewport") == viewport
                and normalized_environment.get("environment_sha256")
                == item.get("environment_sha256")
                and normalized_environment.get("source", {}).get("kind") == "reference"
                and normalized_environment.get("source", {}).get("build_sha256")
                == run.get("reference_build_sha256")
                and normalized_environment.get("browser", {}).get("major_version")
                == run.get("chrome_major_version"),
                "ENVIRONMENT_BINDING",
                f"{location}.environment",
                "normalized reference environment, build, browser, viewport, or hash drift",
            )
            if isinstance(item.get("reference_build_sha256"), str):
                build_hashes.add(item["reference_build_sha256"])
            if isinstance(item.get("reference_build_identifier"), str):
                build_identifiers.add(item["reference_build_identifier"])
            check(
                item.get("reference_build_sha256") == run.get("reference_build_sha256")
                and item.get("reference_build_identifier") == run.get("reference_build_identifier")
                and _valid_time(item.get("captured_at")),
                "CAPTURE_RUN_BINDING",
                location,
                "capture build identifier, build hash, or timestamp drift",
            )
            focus = item.get("focus", {})
            check(
                isinstance(focus, dict)
                and isinstance(focus.get("active_alias"), str)
                and bool(focus.get("active_alias"))
                and isinstance(focus.get("sequence"), list)
                and bool(focus.get("sequence"))
                and all(isinstance(alias, str) and alias for alias in focus.get("sequence", [])),
                "FOCUS_EVIDENCE",
                f"{location}.focus",
                "non-empty active focus alias and sequence required",
            )
            accessibility = item.get("accessible_name_role_state")
            check(
                isinstance(accessibility, list)
                and bool(accessibility)
                and all(
                    isinstance(node, dict)
                    and set(node) == {"alias", "name", "role", "state"}
                    and all(isinstance(node.get(field), str) and node.get(field) for field in ("alias", "name", "role", "state"))
                    for node in accessibility
                ),
                "ACCESSIBILITY_EVIDENCE",
                f"{location}.accessible_name_role_state",
                "non-empty aliased name/role/state evidence required",
            )
            outcome = item.get("interaction_outcome", {})
            check(
                isinstance(outcome, dict)
                and outcome.get("status") == "pass"
                and outcome.get("expected_state") == expected_state.get("state")
                and isinstance(outcome.get("observation"), str)
                and bool(outcome.get("observation")),
                "INTERACTION_EVIDENCE",
                f"{location}.interaction_outcome",
                "passed state-bound interaction observation required",
            )
            check(
                item.get("raw_identifier_persisted") is False
                and item.get("personal_content_visible") is False,
                "CAPTURE_REDACTION",
                location,
                "raw identifiers and personal content must not be persisted",
            )
            if isinstance(supersession, dict):
                mask_relative = item.get("mask_relative_path")
                mask = (
                    _safe_json_path(mask_root, mask_relative)
                    if isinstance(mask_root, Path)
                    else None
                )
                check(
                    mask is not None
                    and isinstance(mask_relative, str)
                    and mask_relative not in seen_mask_paths,
                    "CORRECTIVE_MASK_PATH",
                    f"{location}.mask_relative_path",
                    "unique in-root corrective mask manifest required",
                )
                if isinstance(mask_relative, str):
                    seen_mask_paths.add(mask_relative)
                mask_hash = item.get("mask_sha256")
                mask_manifest_hash = item.get("mask_manifest_sha256")
                mask_scenario_id = item.get("mask_scenario_id")
                masked_pixel_count = item.get("masked_pixel_count")
                if mask is not None:
                    check(
                        _sha256_file(mask) == mask_hash,
                        "CORRECTIVE_MASK_HASH_DRIFT",
                        str(mask_relative),
                        "corrective mask file hash drift",
                    )
                    try:
                        mask_value = _load(mask)
                        expected_mask_scenarios = {
                            str(state_id),
                            str(relative).removesuffix(".png"),
                        }
                        if mask_scenario_id not in expected_mask_scenarios:
                            raise ValueError("corrective mask scenario binding drift")
                        normalized_mask = validate_mask_manifest(
                            mask_value,
                            scenario_id=str(mask_scenario_id),
                            width=int(width),
                            height=int(height),
                        )
                    except (OSError, UnicodeError, ValueError, TypeError, ExactPixelContractError) as exc:
                        check(False, "CORRECTIVE_MASK_INVALID", str(mask_relative), str(exc))
                    else:
                        rectangles = [entry["rect"] for entry in normalized_mask["masks"]]
                        check(
                            normalized_mask.get("manifest_sha256") == mask_manifest_hash
                            and masked_pixel_count == _masked_union_area(rectangles),
                            "CORRECTIVE_MASK_BINDING",
                            str(mask_relative),
                            "canonical corrective mask hash or masked pixel count drift",
                        )
                corrective_manifest_rows.append(
                    {
                        "state_id": state_id,
                        "viewport": viewport,
                        "screenshot_relative_path": relative,
                        "screenshot_sha256": screenshot_hash,
                        "mask_relative_path": mask_relative,
                        "mask_scenario_id": mask_scenario_id,
                        "mask_sha256": mask_hash,
                        "mask_manifest_sha256": mask_manifest_hash,
                        "masked_pixel_count": masked_pixel_count,
                    }
                )
    check(
        isinstance(captures, list)
        and len(captures) == len(EXPECTED_CAPTURE_KEYS)
        and len(actual_capture_keys) == len(set(actual_capture_keys))
        and set(actual_capture_keys) == EXPECTED_CAPTURE_KEYS,
        "CAPTURE_DENOMINATOR",
        "$.captures",
        "exact 18-state by 2-viewport Cartesian capture set required",
    )
    check(
        build_hashes == {str(run.get("reference_build_sha256"))}
        and build_identifiers == {str(run.get("reference_build_identifier"))},
        "REFERENCE_BUILD_UNIFORMITY",
        "$.captures",
        "one reference build identifier and hash required for the run",
    )

    if isinstance(supersession, dict):
        corrective = receipt.get("corrective_evidence", {})
        raw_repo_root = corrective.get("raw_runtime_root") if isinstance(corrective, dict) else None
        raw_root = (
            REPOSITORY_ROOT / Path(*PurePosixPath(raw_repo_root).parts)
            if isinstance(raw_repo_root, str) and raw_repo_root
            else None
        )
        raw_file_count = (
            sum(1 for candidate in raw_root.rglob("*") if candidate.is_file())
            if raw_root is not None and raw_root.exists()
            else 0
        )
        check(
            isinstance(corrective, dict)
            and corrective.get("screenshot_count") == len(EXPECTED_CAPTURE_KEYS)
            and corrective.get("mask_manifest_count") == len(EXPECTED_CAPTURE_KEYS)
            and corrective.get("raw_file_count") == 0
            and raw_file_count == 0
            and corrective.get("capture_manifest_sha256")
            == canonical_sha256(corrective_manifest_rows),
            "CORRECTIVE_EVIDENCE_DENOMINATOR",
            "$.corrective_evidence",
            "36 screenshots, 36 reviewed masks, zero raw files, and canonical manifest hash required",
        )

    cleanup = receipt.get("cleanup", {})
    cleanup_hash = cleanup.get("cleanup_receipt_sha256") if isinstance(cleanup, dict) else None
    check(
        isinstance(cleanup, dict)
        and cleanup.get("status") == "complete"
        and cleanup.get("trigger") in {"after_success", "after_failure"}
        and cleanup.get("mode") == "trash_only_recoverable"
        and cleanup.get("revoked_page_grant_count") == 1
        and cleanup.get("revoked_workspace_membership_count") == 1
        and cleanup.get("active_test_grant_count") == 0
        and cleanup.get("active_fixture_root_count") == 0
        and cleanup.get("trashed_fixture_aliases") == ["V0R-A-ROOT", "V0R-B-ROOT"]
        and cleanup.get("permanent_delete_used") is False
        and _valid_time(cleanup.get("completed_at"))
        and cleanup_hash == canonical_sha256(_strip_hash(cleanup, "cleanup_receipt_sha256")),
        "CLEANUP_EVIDENCE",
        "$.cleanup",
        "complete revoke-before-Trash cleanup receipt and canonical hash required",
    )

    redaction = receipt.get("redaction", {})
    check(
        isinstance(redaction, dict)
        and redaction.get("status") == "pass"
        and all(
            redaction.get(field) is False
            for field in (
                "raw_email_or_account_identifier_persisted",
                "credentials_cookie_token_or_storage_persisted",
                "raw_dom_or_network_payload_persisted",
                "personal_workspace_or_page_captured",
                "non_test_notification_sent",
                "public_publish_or_public_link_used",
                "permanent_delete_used",
            )
        ),
        "REDACTION_EVIDENCE",
        "$.redaction",
        "complete private-data and forbidden-side-effect redaction attestation required",
    )

    claims = receipt.get("claims", {})
    check(
        isinstance(claims, dict)
        and all(
            claims.get(field) is True
            for field in (
                "owner_approval_received",
                "external_reference_mutation_executed",
                "reference_action_log_complete",
                "reference_screenshots_captured",
                "capture_environment_receipts_complete",
                "cleanup_executed",
                "normalized_evidence_sealed",
                "V0R_P1_reference_capture_complete",
                "V0R_P1_gate_passed",
            )
        )
        and all(
            claims.get(field) is False
            for field in (
                "zero_RGBA_difference_claimed",
                "PIX_001_closed",
                "A11Y_001_closed",
                "UI_CORE_25_closed",
                "production_changed",
                "second_brain_changed",
                "product_complete",
                "active_shared_goal_complete",
            )
        ),
        "CLAIMS_FALSE_GREEN",
        "$.claims",
        "V0R reference capture may close its packet but not V0B rows or product completion",
    )

    normalized = sorted(
        {
            (item["code"], item["subject"], item["message"]): item
            for item in errors
        }.values(),
        key=lambda item: (item["code"], item["subject"], item["message"]),
    )
    semantic_reference_gate_passed = not normalized
    capture_transport = _capture_transport_classification(
        receipt, receipt_path=path
    )
    exact_reference_gate_passed = (
        semantic_reference_gate_passed and capture_transport["eligible"] is True
    )
    return {
        "contract": "code_assist.notion.internal_collaborative_reference_multiuser_receipt.audit.v1",
        "status": "pass" if not normalized else "fail",
        "error_count": len(normalized),
        "errors": normalized,
        "receipt_sha256": _sha256_file(path),
        "action_count": len(actions) if isinstance(actions, list) else 0,
        "capture_count": len(captures) if isinstance(captures, list) else 0,
        "capture_state_count": len({item[0] for item in actual_capture_keys}),
        "viewport_count": len({(item[1], item[2]) for item in actual_capture_keys}),
        "cleanup_complete": not normalized and cleanup.get("status") == "complete",
        "gate_passed": semantic_reference_gate_passed,
        "gate_scope": "semantic_reference_capture_and_cleanup",
        "semantic_reference_gate_passed": semantic_reference_gate_passed,
        "exact_reference_transport": capture_transport,
        "exact_reference_gate_passed": exact_reference_gate_passed,
        "supersession_chain": supersession_chain,
        "zero_RGBA_difference_claimed": False,
        "product_complete": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, default=RECEIPT_PATH)
    parser.add_argument("--approval-receipt", type=Path, default=APPROVAL_RECEIPT_PATH)
    parser.add_argument("--screenshot-root", type=Path, default=SCREENSHOT_ROOT)
    parser.add_argument("--mask-root", type=Path)
    parser.add_argument("--manifest", type=Path, default=EXECUTION_MANIFEST_PATH)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report = verify(
        args.receipt,
        approval_receipt_path=args.approval_receipt,
        screenshot_root=args.screenshot_root,
        mask_root=args.mask_root,
        manifest_path=args.manifest,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "APPROVAL_RECEIPT_PATH",
    "CONTRACT",
    "EXPECTED_CAPTURE_KEYS",
    "RECEIPT_PATH",
    "SCREENSHOT_ROOT",
    "SUPERSESSION_REFERENCE_ROOT_PREFIXES",
    "verify",
]
