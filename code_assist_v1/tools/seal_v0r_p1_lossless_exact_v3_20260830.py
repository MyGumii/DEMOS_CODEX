"""Seal the next source-lossless V0R-P1 exact reference as immutable v3.

The script is intentionally inert until the approved 36-capture manifest and
PNG files exist. It preserves the V2 semantic lineage, verifies or creates the
immutable lossless-ingest audit, regenerates reviewed masks in a new evidence
root, and enables the common sealer's exact-reference preseal gate.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from code_assist_v1.tools import seal_v0r_corrective_reference_receipt as sealer
from code_assist_v1.tools import (
    seal_v0r_p1_lossless_corrective_20260830 as mask_geometry,
)
from code_assist_v1.tools import (
    verify_internal_collaborative_reference_multiuser_receipt as receipt_verifier,
)
from code_assist_v1.tools import verify_v0r_p1_lossless_capture_ingest as ingest
from code_assist_v1.tools.notion_parity.exact_pixel import (
    decode_png_srgb_rgba8,
    validate_mask_manifest,
)
from code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_execution import (
    EXPECTED_STATES,
)


V2_RECEIPT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/"
    "v0r-p1-l6-reference-multiuser-lossless-corrective-v2-20260830.json"
)
V3_RECEIPT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/"
    "v0r-p1-l6-reference-multiuser-lossless-exact-v3-20260830.json"
)
V3_INGEST_MANIFEST_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/"
    "v0r-p1-lossless-exact-v3-20260830-ingest-manifest.json"
)
V3_INGEST_AUDIT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/"
    "v0r-p1-lossless-exact-v3-20260830-ingest-audit.json"
)
V3_CHECKPOINT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/checkpoints/"
    "ui-internal-collaboration-v0r-p1-lossless-exact-v3-cleanup-gate-pass-20260830.json"
)
SCREENSHOT_REPO_ROOT = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_reference/"
    "v0r-p1-lossless-exact-v3-20260830/screenshots"
)
MASK_REPO_ROOT = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_reference/"
    "v0r-p1-lossless-exact-v3-20260830/masks"
)
RAW_REPO_ROOT = "code_assist_v1/.runtime/v0r-p1-lossless-exact-v3-20260830/raw"
PREDECESSOR_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/checkpoints/"
    "ui-internal-collaboration-v0r-p1-lossless-ingest-receipt-binding-20260830.json"
)
EXPECTED_V2_RECEIPT_SHA256 = (
    "sha256:3eee297813e3fcb3e4d218daa3c4e3c76cd86b76315549fa0b4a1db0673078aa"
)
SUPERSESSION_AUTHORITY_TEXT = (
    "V0R-P1 lossless corrective reference 재수집 및 새 immutable receipt로 "
    "supersede 승인"
)
CLEANUP_AUTHORITY_TEXT = "V0R 합성 A/B 루트 Trash 이동 및 최종 cleanup 승인"
REFERENCE_BUILD_IDENTIFIER = (
    "notion-web-live-ui-source-lossless-exact-v3-observation-20260830"
)
RECEIPT_ID = "V0R-P1-L6-REFERENCE-MULTIUSER-LOSSLESS-EXACT-V3-20260830"
MASK_REVIEW_ID = "V0R-P1-LOSSLESS-EXACT-V3-20260830-MASK-REVIEW"
SEMANTIC_ROOT_RECEIPT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/"
    "v0r-p1-l6-reference-multiuser-20260825.json"
)


def _path(repo_path: str) -> Path:
    return sealer.REPOSITORY_ROOT / Path(*PurePosixPath(repo_path).parts)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")


def _capture_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def _validate_semantic_postconditions(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest.get("capture_surface"), str) or not manifest.get(
        "capture_surface"
    ):
        raise ValueError("V0R_V3_CAPTURE_SURFACE_REQUIRED")
    expected_state_by_id = {row[0]: row[4] for row in EXPECTED_STATES}
    expected_keys = {
        (state_id, width, height)
        for state_id in expected_state_by_id
        for width, height in ((1280, 800), (1440, 900))
    }
    captures = manifest.get("captures")
    if not isinstance(captures, list):
        raise ValueError("V0R_V3_SEMANTIC_CAPTURE_ARRAY_REQUIRED")
    seen: set[tuple[str, int, int]] = set()
    capture_times: list[datetime] = []
    for capture in captures:
        if not isinstance(capture, Mapping):
            raise ValueError("V0R_V3_SEMANTIC_CAPTURE_OBJECT_REQUIRED")
        viewport = capture.get("viewport")
        if not isinstance(viewport, Mapping):
            raise ValueError("V0R_V3_SEMANTIC_VIEWPORT_REQUIRED")
        key = (
            capture.get("state_id"),
            viewport.get("width"),
            viewport.get("height"),
        )
        if key not in expected_keys or key in seen:
            raise ValueError(f"V0R_V3_SEMANTIC_CAPTURE_KEY_DRIFT:{key!r}")
        seen.add(key)
        expected_state = expected_state_by_id[str(key[0])]
        if (
            capture.get("expected_state") != expected_state
            or capture.get("semantic_postcondition_status")
            != "verified_after_transition"
            or capture.get("capture_after_postcondition") is not True
        ):
            raise ValueError(
                f"V0R_V3_SEMANTIC_POSTCONDITION_NOT_VERIFIED:{key!r}"
            )
        captured_at = _capture_time(capture.get("captured_at"))
        if captured_at is None:
            raise ValueError(f"V0R_V3_CAPTURE_TIME_INVALID:{key!r}")
        capture_times.append(captured_at)
        if not isinstance(capture.get("semantic_observation"), str) or not capture.get(
            "semantic_observation"
        ):
            raise ValueError(f"V0R_V3_SEMANTIC_OBSERVATION_REQUIRED:{key!r}")
    if seen != expected_keys or len(captures) != len(expected_keys):
        raise ValueError("V0R_V3_SEMANTIC_DENOMINATOR_DRIFT")
    return {
        "status": "pass",
        "capture_count": len(captures),
        "capture_state_count": len(expected_state_by_id),
        "viewport_count": 2,
        "capture_started_at": min(capture_times).isoformat(),
        "capture_ended_at": max(capture_times).isoformat(),
        "corrected_timing_states": {
            "V0R-MU-004": "edit_access_active",
            "V0R-MU-006": "inherited_workspace_access_explained",
        },
    }


def _load_or_create_ingest_audit(
    *,
    manifest_path: Path,
    screenshot_root: Path,
    audit_path: Path,
) -> tuple[dict[str, Any], str, str]:
    audit = ingest.verify(
        manifest_path,
        source_root=screenshot_root,
        screenshot_root=screenshot_root,
    )
    if (
        audit.get("status") != "pass"
        or audit.get("exact_reference_transport_eligible") is not True
    ):
        raise ValueError("V0R_V3_LOSSLESS_INGEST_AUDIT_FAILED:" + json.dumps(audit))
    payload = _json_bytes(audit)
    if audit_path.exists():
        if _load(audit_path) != audit:
            raise ValueError("V0R_V3_IMMUTABLE_INGEST_AUDIT_DRIFT")
    else:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("xb") as handle:
            handle.write(payload)
    return (
        audit,
        sealer._sha256_file(audit_path),
        sealer._sha256_file(manifest_path),
    )


def _regenerate_masks(reviewed_at: str) -> None:
    screenshot_root = _path(SCREENSHOT_REPO_ROOT)
    mask_root = _path(MASK_REPO_ROOT)
    screenshots = sorted(screenshot_root.glob("V0R-MU-*.png"))
    if len(screenshots) != 36:
        raise ValueError(f"V0R_V3_EXPECTED_36_SCREENSHOTS:{len(screenshots)}")
    expected_names = {f"{screenshot.stem}.json" for screenshot in screenshots}
    if mask_root.exists() and any(mask_root.iterdir()):
        existing_names = {path.name for path in mask_root.iterdir() if path.is_file()}
        if existing_names != expected_names:
            raise FileExistsError("V0R_V3_IMMUTABLE_MASK_ROOT_DRIFT")
        for screenshot in screenshots:
            state_id, viewport = screenshot.stem.split("__", 1)
            width, height = (int(value) for value in viewport.split("x", 1))
            mask = _load(mask_root / f"{screenshot.stem}.json")
            validate_mask_manifest(
                mask,
                scenario_id=screenshot.stem,
                width=width,
                height=height,
            )
            expected_masks = [
                {
                    "id": f"DYNAMIC-REGION-{index:02d}",
                    "reason": "avatar",
                    "rect": rect,
                }
                for index, rect in enumerate(
                    mask_geometry.MASK_RECTS[(state_id, width, height)], start=1
                )
            ]
            if (
                mask.get("content_independent") is not True
                or mask.get("masks") != expected_masks
                or mask.get("review", {}).get("status") != "approved"
                or mask.get("review", {}).get("receipt_id") != MASK_REVIEW_ID
            ):
                raise ValueError(f"V0R_V3_IMMUTABLE_MASK_DRIFT:{screenshot.stem}")
        return
    mask_root.mkdir(parents=True, exist_ok=True)
    for screenshot in screenshots:
        state_id, viewport = screenshot.stem.split("__", 1)
        width, height = (int(value) for value in viewport.split("x", 1))
        decoded = decode_png_srgb_rgba8(screenshot.read_bytes())
        if (decoded.width, decoded.height) != (width, height):
            raise ValueError(f"V0R_V3_PNG_GEOMETRY_DRIFT:{screenshot.name}")
        rectangles = mask_geometry.MASK_RECTS[(state_id, width, height)]
        mask = {
            "schema_version": 1,
            "contract": "code_assist.notion.pixel_mask_manifest.v1",
            "scenario_id": screenshot.stem,
            "image": {"width": width, "height": height},
            "content_independent": True,
            "review": {
                "status": "approved",
                "receipt_id": MASK_REVIEW_ID,
                "reviewer": "v0r-p1-lossless-exact-v3-main-agent",
                "reviewed_at": reviewed_at,
            },
            "masks": [
                {
                    "id": f"DYNAMIC-REGION-{index:02d}",
                    "reason": "avatar",
                    "rect": rect,
                }
                for index, rect in enumerate(rectangles, start=1)
            ],
        }
        validate_mask_manifest(
            mask,
            scenario_id=screenshot.stem,
            width=width,
            height=height,
        )
        with (mask_root / f"{screenshot.stem}.json").open("xb") as handle:
            handle.write(_json_bytes(mask))


def _decorate_receipt(
    receipt: dict[str, Any],
    *,
    manifest: Mapping[str, Any],
    semantic_review: Mapping[str, Any],
    audit: Mapping[str, Any],
    audit_sha256: str,
    manifest_file_sha256: str,
) -> dict[str, Any]:
    receipt["receipt_id"] = RECEIPT_ID
    receipt["run"]["run_id"] = "v0r-p1-lossless-exact-v3-reference-20260830"
    receipt["run"]["semantic_action_log_source"] = V2_RECEIPT_REPO_PATH
    receipt["run"]["prior_corrective_receipt_sha256"] = (
        EXPECTED_V2_RECEIPT_SHA256
    )
    receipt["run"]["capture_surface"] = manifest.get("capture_surface")
    receipt["run"]["started_at"] = semantic_review["capture_started_at"]
    receipt["run"]["capture_ended_at"] = semantic_review["capture_ended_at"]
    receipt["run"]["lossless_ingest_manifest_path"] = (
        V3_INGEST_MANIFEST_REPO_PATH
    )
    receipt["run"]["lossless_ingest_manifest_file_sha256"] = (
        manifest_file_sha256
    )
    receipt["run"]["lossless_ingest_audit_path"] = V3_INGEST_AUDIT_REPO_PATH
    receipt["run"]["lossless_ingest_audit_sha256"] = audit_sha256
    receipt["capture_transport"] = {
        "browser_capture_returned_codec": "png",
        "stored_codec": "png_srgb_rgba8",
        "conversion": "none_source_bytes_copied_without_reencode",
        "source_bytes_preserved": True,
        "source_encoding_lossless": True,
        "zero_RGBA_equivalence_claimed": True,
        "lossless_ingest_audit": {
            "relative_path": Path(V3_INGEST_AUDIT_REPO_PATH).name,
            "sha256": audit_sha256,
            "capture_manifest_sha256": audit["capture_manifest_sha256"],
        },
    }
    manifest_captures = manifest.get("captures")
    manifest_rows = {
        (
            row.get("state_id"),
            row.get("viewport", {}).get("width"),
            row.get("viewport", {}).get("height"),
        ): row
        for row in manifest_captures
        if isinstance(row, Mapping) and isinstance(row.get("viewport"), Mapping)
    } if isinstance(manifest_captures, list) else {}
    receipt_captures = receipt.get("captures")
    if not isinstance(receipt_captures, list):
        raise ValueError("V0R_V3_RECEIPT_CAPTURES_REQUIRED")
    for capture in receipt_captures:
        if not isinstance(capture, dict) or not isinstance(
            capture.get("viewport"), Mapping
        ):
            raise ValueError("V0R_V3_RECEIPT_CAPTURE_SHAPE")
        viewport = capture["viewport"]
        key = (
            capture.get("state_id"),
            viewport.get("width"),
            viewport.get("height"),
        )
        source = manifest_rows.get(key)
        if source is None:
            raise ValueError(f"V0R_V3_RECEIPT_MANIFEST_BINDING_MISSING:{key!r}")
        capture["captured_at"] = source["captured_at"]
        capture["capture_timestamp_basis"] = "source_lossless_capture_manifest"
        capture["semantic_postcondition_status"] = source[
            "semantic_postcondition_status"
        ]
        capture["capture_after_postcondition"] = source[
            "capture_after_postcondition"
        ]
        outcome = capture.get("interaction_outcome")
        if not isinstance(outcome, dict):
            raise ValueError("V0R_V3_RECEIPT_INTERACTION_OUTCOME_REQUIRED")
        outcome["status"] = "pass"
        outcome["expected_state"] = source["expected_state"]
        outcome["observation"] = source["semantic_observation"]
    receipt["semantic_capture_review"] = dict(semantic_review)
    receipt["corrective_evidence"]["lossless_ingest_manifest_path"] = (
        V3_INGEST_MANIFEST_REPO_PATH
    )
    receipt["corrective_evidence"]["lossless_ingest_audit_path"] = (
        V3_INGEST_AUDIT_REPO_PATH
    )
    receipt["corrective_evidence"]["lossless_ingest_audit_sha256"] = (
        audit_sha256
    )
    return receipt


def _decorate_checkpoint(
    checkpoint: dict[str, Any],
    *,
    audit: Mapping[str, Any],
    audit_sha256: str,
    manifest_file_sha256: str,
) -> dict[str, Any]:
    checkpoint["checkpoint_id"] = (
        "UI-INTERNAL-COLLABORATION-V0R-P1-LOSSLESS-EXACT-V3-"
        "CLEANUP-GATE-PASS-20260830"
    )
    checkpoint["checkpoint_kind"] = (
        "source_lossless_exact_reference_cleanup_and_receipt_gate_pass"
    )
    checkpoint["purpose"] = (
        "Seal the source-lossless 18-state by two-viewport reference with an "
        "immutable ingest audit, corrected transition timing for V0R-MU-004 and "
        "V0R-MU-006, reviewed masks, and preserved semantic lineage."
    )
    checkpoint["lossless_ingest_binding"] = {
        "manifest_path": V3_INGEST_MANIFEST_REPO_PATH,
        "manifest_file_sha256": manifest_file_sha256,
        "capture_manifest_sha256": audit["capture_manifest_sha256"],
        "audit_path": V3_INGEST_AUDIT_REPO_PATH,
        "audit_sha256": audit_sha256,
        "exact_reference_transport_eligible": True,
    }
    checkpoint["final_receipt"]["exact_reference_gate_passed"] = True
    checkpoint["claims"]["V0R_P1_exact_reference_transport_passed"] = True
    checkpoint["next_action"] = (
        "Run the exact Full36 comparison against this v3 receipt, then Core25 and "
        "quiet-host performance only after all exact and accessibility rows pass."
    )
    return checkpoint


def _configure_sealer(
    *,
    manifest: Mapping[str, Any],
    semantic_review: Mapping[str, Any],
    ingest_audit: Mapping[str, Any],
    audit_sha256: str,
    manifest_file_sha256: str,
) -> None:
    sealer.ORIGINAL_RECEIPT_REPO_PATH = V2_RECEIPT_REPO_PATH
    sealer.CORRECTIVE_RECEIPT_REPO_PATH = V3_RECEIPT_REPO_PATH
    sealer.CORRECTIVE_CHECKPOINT_REPO_PATH = V3_CHECKPOINT_REPO_PATH
    sealer.SCREENSHOT_REPO_ROOT = SCREENSHOT_REPO_ROOT
    sealer.MASK_REPO_ROOT = MASK_REPO_ROOT
    sealer.RAW_REPO_ROOT = RAW_REPO_ROOT
    sealer.PREDECESSOR_REPO_PATH = PREDECESSOR_REPO_PATH
    sealer.EXPECTED_ORIGINAL_RECEIPT_SHA256 = EXPECTED_V2_RECEIPT_SHA256
    sealer.SUPERSESSION_AUTHORITY_TEXT = SUPERSESSION_AUTHORITY_TEXT
    sealer.CLEANUP_AUTHORITY_TEXT = CLEANUP_AUTHORITY_TEXT
    sealer.REFERENCE_BUILD_IDENTIFIER = REFERENCE_BUILD_IDENTIFIER
    sealer.REQUIRE_EXACT_REFERENCE_GATE = True
    receipt_verifier.ORIGINAL_RECEIPT_REPO_PATH = (
        SEMANTIC_ROOT_RECEIPT_REPO_PATH
    )

    original_build_receipt = sealer.build_receipt
    original_build_checkpoint = sealer.build_checkpoint

    def build_receipt(*, recorded_at: str) -> dict[str, Any]:
        return _decorate_receipt(
            original_build_receipt(recorded_at=recorded_at),
            manifest=manifest,
            semantic_review=semantic_review,
            audit=ingest_audit,
            audit_sha256=audit_sha256,
            manifest_file_sha256=manifest_file_sha256,
        )

    def build_checkpoint(
        *, recorded_at: str, receipt_sha256: str, audit: dict[str, Any]
    ) -> dict[str, Any]:
        return _decorate_checkpoint(
            original_build_checkpoint(
                recorded_at=recorded_at,
                receipt_sha256=receipt_sha256,
                audit=audit,
            ),
            audit=ingest_audit,
            audit_sha256=audit_sha256,
            manifest_file_sha256=manifest_file_sha256,
        )

    sealer.build_receipt = build_receipt
    sealer.build_checkpoint = build_checkpoint


def main() -> int:
    if sealer._sha256_file(_path(V2_RECEIPT_REPO_PATH)) != EXPECTED_V2_RECEIPT_SHA256:
        raise ValueError("V0R_V3_V2_RECEIPT_HASH_DRIFT")
    if _path(V3_RECEIPT_REPO_PATH).exists() or _path(V3_CHECKPOINT_REPO_PATH).exists():
        raise FileExistsError("V0R_V3_IMMUTABLE_OUTPUT_EXISTS")
    manifest_path = _path(V3_INGEST_MANIFEST_REPO_PATH)
    manifest = _load(manifest_path)
    if not isinstance(manifest, Mapping):
        raise ValueError("V0R_V3_INGEST_MANIFEST_OBJECT_REQUIRED")
    semantic_review = _validate_semantic_postconditions(manifest)
    audit, audit_sha256, manifest_file_sha256 = _load_or_create_ingest_audit(
        manifest_path=manifest_path,
        screenshot_root=_path(SCREENSHOT_REPO_ROOT),
        audit_path=_path(V3_INGEST_AUDIT_REPO_PATH),
    )
    reviewed_at = (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    _regenerate_masks(reviewed_at)
    _configure_sealer(
        manifest=manifest,
        semantic_review=semantic_review,
        ingest_audit=audit,
        audit_sha256=audit_sha256,
        manifest_file_sha256=manifest_file_sha256,
    )
    result = sealer.seal()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
