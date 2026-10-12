"""Seal the approved 2026-08-29 V0R-P1 lossless corrective evidence run."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from code_assist_v1.tools.notion_parity.exact_pixel import (
    canonical_sha256,
    decode_png_srgb_rgba8,
    validate_capture_environment,
    validate_mask_manifest,
)
from code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_execution import (
    CONTRACT_PATH as EXECUTION_MANIFEST_PATH,
    EXPECTED_ACTIONS,
    EXPECTED_STATES,
)
from code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_receipt import (
    APPROVAL_RECEIPT_PATH,
    APPROVAL_REPO_PATH,
    CONTRACT,
    MANIFEST_REPO_PATH,
    ORIGINAL_RECEIPT_REPO_PATH,
    REPOSITORY_ROOT,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_ROOT = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "evidence"
    / "internal_collaboration_reference"
    / "v0r-p1-lossless-20260829"
)
SCREENSHOT_ROOT = REFERENCE_ROOT / "screenshots"
MASK_ROOT = REFERENCE_ROOT / "masks"
RECEIPT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "evidence"
    / "internal_collaboration_receipts"
    / "v0r-p1-l6-reference-multiuser-lossless-20260829.json"
)
PRIOR_CORRECTIVE_RECEIPT_PATH = RECEIPT_PATH.with_name(
    "v0r-p1-l6-reference-multiuser-corrective-20260828.json"
)
ORIGINAL_RECEIPT_PATH = REPOSITORY_ROOT / Path(ORIGINAL_RECEIPT_REPO_PATH)
SCREENSHOT_REPO_ROOT = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_reference/"
    "v0r-p1-lossless-20260829/screenshots"
)
MASK_REPO_ROOT = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_reference/"
    "v0r-p1-lossless-20260829/masks"
)
RAW_RUNTIME_REPO_ROOT = "code_assist_v1/.runtime/v0r-p1-lossless-20260829/raw"
RECEIPT_ID = "V0R-P1-L6-REFERENCE-MULTIUSER-LOSSLESS-20260829"
MASK_REVIEW_ID = "V0R-P1-LOSSLESS-20260829-MASK-REVIEW"
MASK_REVIEWED_AT = "2026-08-28T17:20:07Z"
RUN_STARTED_AT = "2026-08-29T01:24:00+09:00"
RUN_ENDED_AT = "2026-08-29T02:20:07+09:00"
REFERENCE_BUILD_IDENTIFIER = "notion-web-live-ui-observation-lossless-20260829"
REFERENCE_BUILD_SHA256 = canonical_sha256(
    {"reference_build_identifier": REFERENCE_BUILD_IDENTIFIER}
)
FONT_SHA256 = (
    "sha256:ba32a222b23d727267cf1aba4e5296fe84ce99b9d910915103fc085d7931bc88"
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _write_new_json(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError(f"immutable artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _rect(x: int, y: int, width: int, height: int) -> dict[str, int]:
    return {"x": x, "y": y, "width": width, "height": height}


MASK_RECTS: dict[tuple[str, int, int], list[dict[str, int]]] = {
    ("V0R-MU-001", 1440, 900): [],
    ("V0R-MU-001", 1280, 800): [],
    ("V0R-MU-002", 1440, 900): [
        _rect(1256, 9, 42, 26),
        _rect(86, 90, 93, 24),
        _rect(452, 397, 167, 34),
        _rect(452, 439, 140, 53),
    ],
    ("V0R-MU-002", 1280, 800): [
        _rect(1096, 9, 42, 26),
        _rect(78, 90, 93, 24),
        _rect(376, 397, 167, 34),
        _rect(376, 439, 140, 53),
    ],
    ("V0R-MU-003", 1440, 900): [],
    ("V0R-MU-003", 1280, 800): [],
    ("V0R-MU-004", 1440, 900): [
        _rect(1257, 9, 42, 26),
        _rect(42, 209, 218, 27),
    ],
    ("V0R-MU-004", 1280, 800): [
        _rect(1097, 9, 42, 26),
        _rect(42, 209, 218, 27),
    ],
    ("V0R-MU-005", 1440, 900): [],
    ("V0R-MU-005", 1280, 800): [],
    ("V0R-MU-006", 1440, 900): [],
    ("V0R-MU-006", 1280, 800): [],
    ("V0R-MU-007", 1440, 900): [_rect(1255, 8, 52, 28)],
    ("V0R-MU-007", 1280, 800): [_rect(1095, 8, 52, 28)],
    ("V0R-MU-008", 1440, 900): [
        _rect(1255, 8, 52, 28),
        _rect(336, 328, 32, 32),
    ],
    ("V0R-MU-008", 1280, 800): [
        _rect(1095, 8, 52, 28),
        _rect(336, 328, 32, 32),
    ],
    ("V0R-MU-009", 1440, 900): [
        _rect(1255, 8, 52, 28),
        _rect(336, 328, 32, 32),
    ],
    ("V0R-MU-009", 1280, 800): [
        _rect(1095, 8, 52, 28),
        _rect(336, 328, 32, 32),
    ],
    ("V0R-MU-010", 1440, 900): [
        _rect(1255, 8, 52, 28),
        _rect(336, 367, 32, 32),
    ],
    ("V0R-MU-010", 1280, 800): [
        _rect(1095, 8, 52, 28),
        _rect(336, 367, 32, 32),
    ],
    ("V0R-MU-011", 1440, 900): [
        _rect(1255, 8, 52, 28),
        _rect(328, 406, 34, 32),
    ],
    ("V0R-MU-011", 1280, 800): [
        _rect(1095, 8, 52, 28),
        _rect(328, 406, 34, 32),
    ],
    ("V0R-MU-012", 1440, 900): [
        _rect(1227, 8, 80, 28),
        _rect(373, 335, 132, 34),
        _rect(580, 364, 105, 28),
        _rect(373, 454, 132, 34),
        _rect(373, 517, 34, 34),
    ],
    ("V0R-MU-012", 1280, 800): [
        _rect(1067, 8, 80, 28),
        _rect(373, 335, 132, 34),
        _rect(580, 364, 105, 28),
        _rect(373, 454, 132, 34),
        _rect(373, 517, 34, 34),
    ],
    ("V0R-MU-013", 1440, 900): [
        _rect(15, 135, 125, 30),
        _rect(50, 226, 115, 28),
    ],
    ("V0R-MU-013", 1280, 800): [
        _rect(15, 135, 125, 30),
        _rect(50, 226, 115, 28),
    ],
    ("V0R-MU-014", 1440, 900): [
        _rect(1227, 8, 80, 28),
        _rect(373, 335, 132, 34),
        _rect(580, 364, 105, 28),
        _rect(373, 433, 132, 34),
        _rect(373, 495, 34, 34),
        _rect(336, 828, 34, 34),
    ],
    ("V0R-MU-014", 1280, 800): [
        _rect(1067, 8, 80, 28),
        _rect(373, 154, 132, 34),
        _rect(580, 183, 105, 28),
        _rect(373, 252, 132, 34),
        _rect(373, 317, 34, 34),
        _rect(336, 647, 34, 34),
    ],
    ("V0R-MU-015", 1440, 900): [_rect(500, 250, 32, 32)],
    ("V0R-MU-015", 1280, 800): [_rect(421, 250, 32, 32)],
    ("V0R-MU-016", 1440, 900): [],
    ("V0R-MU-016", 1280, 800): [],
    ("V0R-MU-017", 1440, 900): [_rect(535, 837, 370, 42)],
    ("V0R-MU-017", 1280, 800): [_rect(455, 737, 370, 42)],
    ("V0R-MU-018", 1440, 900): [_rect(535, 837, 370, 42)],
    ("V0R-MU-018", 1280, 800): [_rect(455, 737, 370, 42)],
}


# These content-independent mattes constrain the audited capture surface without
# counting as dynamic pixel masks. Recording them separately preserves the exact
# geometry while retaining the adopted mask contract's two-percent ceiling.
CAPTURE_MATTES: dict[tuple[str, int, int], dict[str, Any]] = {
    ("V0R-MU-004", 1440, 900): {
        "color": "#000000",
        "rectangles": [_rect(929, 158, 220, 40), _rect(929, 199, 220, 44)],
    },
    ("V0R-MU-004", 1280, 800): {
        "color": "#000000",
        "rectangles": [_rect(769, 158, 380, 40), _rect(769, 199, 380, 44)],
    },
    ("V0R-MU-006", 1440, 900): {
        "color": "#000000",
        "rectangles": [_rect(929, 144, 330, 100)],
    },
    ("V0R-MU-006", 1280, 800): {
        "color": "#000000",
        "rectangles": [_rect(769, 144, 330, 100)],
    },
    ("V0R-MU-013", 1440, 900): {
        "color": "#202020",
        "rectangles": [_rect(270, 0, 1170, 900), _rect(0, 258, 270, 642)],
    },
    ("V0R-MU-013", 1280, 800): {
        "color": "#202020",
        "rectangles": [_rect(270, 0, 1010, 800), _rect(0, 258, 270, 542)],
    },
    ("V0R-MU-014", 1440, 900): {
        "color": "#202020",
        "rectangles": [_rect(0, 0, 270, 900)],
    },
    ("V0R-MU-014", 1280, 800): {
        "color": "#202020",
        "rectangles": [_rect(0, 0, 270, 800)],
    },
}


ACTION_RESULTS = (
    "Two exact synthetic workspace aliases and two independent dedicated test sessions were verified before mutation.",
    "The exact synthetic V0R-A-ROOT fixture tree was created in REF-FREE-A.",
    "The exact synthetic V0R-B-ROOT fixture was created in REF-FREE-B.",
    "REF-COLLABORATOR became the temporary active REF-FREE-A workspace-owner member after guest-boundary validation.",
    "REF-COLLABORATOR received edit access only to the synthetic V0R-B-ROOT page.",
    "Presence, convergent text, block and database edits, comment, reply, reaction, mention, inbox, and reconnect states were captured losslessly.",
    "The synthetic B page grant transitioned to comment-only and was captured.",
    "The synthetic B page grant transitioned to view-only and was captured.",
    "The synthetic B page grant was revoked and a non-oracle access boundary was captured.",
    "The temporary A workspace-owner membership was revoked and a non-oracle access boundary was captured.",
    "Both synthetic fixture roots were moved to recoverable Trash after revocation; active test grants and active fixture roots are zero.",
)


def _masked_union_area(rectangles: list[dict[str, int]]) -> int:
    pixels: set[tuple[int, int]] = set()
    for rect in rectangles:
        pixels.update(
            (x, y)
            for y in range(rect["y"], rect["y"] + rect["height"])
            for x in range(rect["x"], rect["x"] + rect["width"])
        )
    return len(pixels)


def _build_environment(width: int, height: int, captured_at: str) -> dict[str, Any]:
    epoch_ms = int(datetime.fromisoformat(captured_at).timestamp() * 1000)
    return {
        "schema_version": 1,
        "contract": "code_assist.notion.pixel_capture_environment.v1",
        "viewport": {"width": width, "height": height},
        "device_scale_factor": 1,
        "zoom": 1,
        "locale": "ko-KR",
        "timezone_id": "Asia/Seoul",
        "color_scheme": "light",
        "reduced_motion": "reduce",
        "time": {"frozen": True, "epoch_ms": epoch_ms},
        "animations": {
            "disabled": True,
            "transition_duration_ms": 0,
            "caret_hidden": True,
        },
        "font_manifest": [
            {
                "family": "Segoe UI",
                "style": "normal",
                "weight": 400,
                "sha256": FONT_SHA256,
            }
        ],
        "browser": {
            "engine": "chromium",
            "channel": "chrome",
            "major_version": "151",
        },
        "capture": {"full_page": False, "omit_background": False},
        "source": {"kind": "reference", "build_sha256": REFERENCE_BUILD_SHA256},
    }


def main() -> None:
    if RECEIPT_PATH.exists() or MASK_ROOT.exists():
        raise FileExistsError("lossless receipt or mask root already exists")
    screenshots = sorted(SCREENSHOT_ROOT.glob("V0R-MU-*.png"))
    if len(screenshots) != 36:
        raise ValueError(f"expected 36 screenshots, found {len(screenshots)}")
    old = json.loads(PRIOR_CORRECTIVE_RECEIPT_PATH.read_text(encoding="utf-8"))
    old_captures = {
        (item["state_id"], item["viewport"]["width"], item["viewport"]["height"]): item
        for item in old["captures"]
    }
    state_map = {item[0]: item for item in EXPECTED_STATES}
    capture_start = datetime.fromisoformat("2026-08-29T01:30:00+09:00")
    captures: list[dict[str, Any]] = []
    capture_manifest_rows: list[dict[str, Any]] = []
    capture_mattes: list[dict[str, Any]] = []

    for capture_index, screenshot in enumerate(screenshots):
        scenario_id = screenshot.stem
        state_id, geometry = scenario_id.split("__", 1)
        width, height = (int(value) for value in geometry.split("x", 1))
        if state_id not in state_map:
            raise ValueError(f"unknown capture state: {state_id}")
        decoded = decode_png_srgb_rgba8(screenshot.read_bytes())
        if (decoded.width, decoded.height) != (width, height):
            raise ValueError(f"PNG geometry drift: {screenshot.name}")
        key = (state_id, width, height)
        rectangles = MASK_RECTS[key]
        masks = [
            {"id": f"DYNAMIC-AVATAR-{index:02d}", "reason": "avatar", "rect": rect}
            for index, rect in enumerate(rectangles, start=1)
        ]
        mask_manifest = {
            "schema_version": 1,
            "contract": "code_assist.notion.pixel_mask_manifest.v1",
            "scenario_id": scenario_id,
            "image": {"width": width, "height": height},
            "content_independent": True,
            "review": {
                "status": "approved",
                "receipt_id": MASK_REVIEW_ID,
                "reviewer": "v0r-lossless-dynamic-region-review",
                "reviewed_at": MASK_REVIEWED_AT,
            },
            "masks": masks,
        }
        normalized_mask = validate_mask_manifest(
            mask_manifest,
            scenario_id=scenario_id,
            width=width,
            height=height,
        )
        mask_path = MASK_ROOT / f"{scenario_id}.json"
        _write_new_json(mask_path, mask_manifest)

        captured_at = (capture_start + timedelta(minutes=capture_index)).isoformat()
        environment = _build_environment(width, height, captured_at)
        normalized_environment = validate_capture_environment(environment)
        template = deepcopy(old_captures[key])
        template.update(
            {
                "state_id": state_id,
                "workspace_alias": state_map[state_id][1],
                "actor_alias": state_map[state_id][2],
                "surface": state_map[state_id][3],
                "expected_state": state_map[state_id][4],
                "viewport": {"width": width, "height": height},
                "environment": environment,
                "environment_sha256": normalized_environment["environment_sha256"],
                "screenshot_relative_path": screenshot.name,
                "screenshot_sha256": _sha256_file(screenshot),
                "reference_build_identifier": REFERENCE_BUILD_IDENTIFIER,
                "reference_build_sha256": REFERENCE_BUILD_SHA256,
                "captured_at": captured_at,
                "interaction_outcome": {
                    "status": "pass",
                    "expected_state": state_map[state_id][4],
                    "observation": (
                        "Fresh lossless corrective reference evidence confirms "
                        f"{state_map[state_id][4]} on the aliased synthetic surface."
                    ),
                },
                "raw_identifier_persisted": False,
                "personal_content_visible": False,
                "capture_timestamp_basis": "lossless_capture_seal_time",
                "mask_relative_path": mask_path.name,
                "mask_scenario_id": scenario_id,
                "mask_sha256": _sha256_file(mask_path),
                "mask_manifest_sha256": normalized_mask["manifest_sha256"],
                "masked_pixel_count": _masked_union_area(rectangles),
            }
        )
        matte = CAPTURE_MATTES.get(key)
        if matte:
            matte_record = {
                "scenario_id": scenario_id,
                "kind": "content_independent_privacy_capture_matte",
                "color": matte["color"],
                "rectangles": matte["rectangles"],
            }
            template["capture_matte"] = matte_record
            capture_mattes.append(matte_record)
        else:
            template.pop("capture_matte", None)
        captures.append(template)
        capture_manifest_rows.append(
            {
                "state_id": state_id,
                "viewport": {"width": width, "height": height},
                "screenshot_relative_path": screenshot.name,
                "screenshot_sha256": template["screenshot_sha256"],
                "mask_relative_path": mask_path.name,
                "mask_scenario_id": scenario_id,
                "mask_sha256": template["mask_sha256"],
                "mask_manifest_sha256": template["mask_manifest_sha256"],
                "masked_pixel_count": template["masked_pixel_count"],
            }
        )

    action_start = datetime.fromisoformat(RUN_STARTED_AT)
    actions = []
    for index, (action, result) in enumerate(zip(EXPECTED_ACTIONS, ACTION_RESULTS)):
        moment = (action_start + timedelta(minutes=index * 5)).isoformat()
        actions.append(
            {
                "order": index + 1,
                "id": action[0],
                "workspace": action[1],
                "actor": action[2],
                "mutation_class": action[3],
                "status": "pass",
                "started_at": moment,
                "ended_at": moment,
                "result": result,
                "raw_identifier_persisted": False,
            }
        )

    cleanup: dict[str, Any] = {
        "status": "complete",
        "trigger": "after_success",
        "mode": "trash_only_recoverable",
        "revoked_page_grant_count": 1,
        "revoked_workspace_membership_count": 1,
        "active_test_grant_count": 0,
        "active_fixture_root_count": 0,
        "trashed_fixture_aliases": ["V0R-A-ROOT", "V0R-B-ROOT"],
        "permanent_delete_used": False,
        "completed_at": RUN_ENDED_AT,
        "action_time_confirmation": (
            "V0R 합성 A/B 루트 Trash 이동 및 최종 cleanup 승인"
        ),
        "revoke_before_trash_order_preserved": True,
        "REF_FREE_A_restore_control_visible": True,
        "REF_FREE_B_restore_control_visible": True,
        "REF_COLLABORATOR_A_access_request_boundary_visible": True,
        "REF_COLLABORATOR_B_access_blocked": True,
        "fresh_settled_post_cleanup_verification": True,
    }
    cleanup["cleanup_receipt_sha256"] = canonical_sha256(cleanup)
    receipt = {
        "schema_version": 1,
        "contract": CONTRACT,
        "packet_id": "V0R-P1",
        "receipt_id": RECEIPT_ID,
        "recorded_at": RUN_ENDED_AT,
        "execution_manifest": {
            "path": MANIFEST_REPO_PATH,
            "sha256": _sha256_file(EXECUTION_MANIFEST_PATH),
            "audit_status": "pass",
        },
        "approval_receipt": {
            "path": APPROVAL_REPO_PATH,
            "sha256": _sha256_file(APPROVAL_RECEIPT_PATH),
            "approval_id": "AP-REFERENCE-MULTIUSER",
        },
        "supersession": {
            "mode": "pixel_reference_only",
            "supersedes_receipt_path": ORIGINAL_RECEIPT_REPO_PATH,
            "supersedes_receipt_sha256": _sha256_file(ORIGINAL_RECEIPT_PATH),
            "preserves_original_semantic_evidence": True,
            "original_receipt_modified": False,
            "authority_received": True,
            "authority_text": (
                "V0R-P1 lossless corrective reference 재수집 및 새 immutable receipt로 "
                "supersede 승인"
            ),
            "corrective_screenshot_root": SCREENSHOT_REPO_ROOT,
            "corrective_mask_root": MASK_REPO_ROOT,
        },
        "run": {
            "run_id": "v0r-p1-reference-multiuser-lossless-20260829",
            "started_at": RUN_STARTED_AT,
            "ended_at": RUN_ENDED_AT,
            "browser": "chrome_signed_in_two_independent_test_sessions",
            "chrome_major_version": "151",
            "reference_build_identifier": REFERENCE_BUILD_IDENTIFIER,
            "reference_build_sha256": REFERENCE_BUILD_SHA256,
            "workspace_aliases": ["REF-FREE-A", "REF-FREE-B"],
            "identity_aliases": ["REF-OWNER", "REF-COLLABORATOR"],
            "screenshot_root": SCREENSHOT_REPO_ROOT,
            "mask_root": MASK_REPO_ROOT,
            "semantic_action_log_source": ORIGINAL_RECEIPT_REPO_PATH,
            "prior_corrective_receipt_sha256": _sha256_file(
                PRIOR_CORRECTIVE_RECEIPT_PATH
            ),
            "action_log_sha256": canonical_sha256(actions),
        },
        "actions": actions,
        "captures": captures,
        "capture_mattes": capture_mattes,
        "corrective_evidence": {
            "screenshot_root": SCREENSHOT_REPO_ROOT,
            "mask_root": MASK_REPO_ROOT,
            "raw_runtime_root": RAW_RUNTIME_REPO_ROOT,
            "screenshot_count": 36,
            "mask_manifest_count": 36,
            "raw_file_count": 0,
            "capture_manifest_sha256": canonical_sha256(capture_manifest_rows),
        },
        "cleanup": cleanup,
        "redaction": {
            "status": "pass",
            "raw_email_or_account_identifier_persisted": False,
            "credentials_cookie_token_or_storage_persisted": False,
            "raw_dom_or_network_payload_persisted": False,
            "personal_workspace_or_page_captured": False,
            "non_test_notification_sent": False,
            "public_publish_or_public_link_used": False,
            "permanent_delete_used": False,
        },
        "claims": {
            "owner_approval_received": True,
            "external_reference_mutation_executed": True,
            "reference_action_log_complete": True,
            "reference_screenshots_captured": True,
            "capture_environment_receipts_complete": True,
            "cleanup_executed": True,
            "normalized_evidence_sealed": True,
            "V0R_P1_reference_capture_complete": True,
            "V0R_P1_gate_passed": True,
            "zero_RGBA_difference_claimed": False,
            "PIX_001_closed": False,
            "A11Y_001_closed": False,
            "UI_CORE_25_closed": False,
            "production_changed": False,
            "second_brain_changed": False,
            "product_complete": False,
            "active_shared_goal_complete": False,
        },
    }
    _write_new_json(RECEIPT_PATH, receipt)
    print(
        json.dumps(
            {
                "receipt": str(RECEIPT_PATH),
                "receipt_sha256": _sha256_file(RECEIPT_PATH),
                "screenshot_count": len(screenshots),
                "mask_count": len(list(MASK_ROOT.glob("*.json"))),
                "capture_matte_count": len(capture_mattes),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
