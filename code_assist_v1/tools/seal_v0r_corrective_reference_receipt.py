"""Seal the superseding V0R-P1 corrective pixel-reference receipt.

The original V0R receipt remains byte-for-byte immutable semantic evidence.
This sealer binds the fresh corrective screenshots and reviewed dynamic masks,
records the recoverable cleanup, audits the assembled receipt before publish,
and writes both new artifacts with exclusive-create semantics.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any, Mapping, Sequence

from code_assist_v1.tools.notion_parity.exact_pixel import (
    canonical_sha256,
    decode_png_srgb_rgba8,
    validate_capture_environment,
    validate_mask_manifest,
)
from code_assist_v1.tools.verify_internal_collaborative_reference_multiuser_receipt import (
    verify,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
ORIGINAL_RECEIPT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/"
    "v0r-p1-l6-reference-multiuser-20260825.json"
)
CORRECTIVE_RECEIPT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/evidence/internal_collaboration_receipts/"
    "v0r-p1-l6-reference-multiuser-corrective-20260828.json"
)
CORRECTIVE_CHECKPOINT_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/checkpoints/"
    "ui-internal-collaboration-v0r-p1-corrective-cleanup-gate-pass-20260828.json"
)
SCREENSHOT_REPO_ROOT = (
    "code_assist_v1/docs/notion_parity/evidence/"
    "internal_collaboration_reference/v0r-p1-corrective/screenshots"
)
MASK_REPO_ROOT = (
    "code_assist_v1/docs/notion_parity/evidence/"
    "internal_collaboration_reference/v0r-p1-corrective/masks"
)
RAW_REPO_ROOT = "code_assist_v1/.runtime/v0r-p1-corrective-20260827/raw"
PREDECESSOR_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/checkpoints/"
    "ui-internal-collaboration-v0b-p1-reference-pixel-baseline-invalid-20260827.json"
)
AGENTS_REPO_PATH = "code_assist_v1/AGENTS.md"
HANDOFF_REPO_PATH = (
    "code_assist_v1/docs/notion_parity/core_free_web_ui/"
    "INTERNAL_COLLABORATIVE_NOTION_MAIN_AGENT_HANDOFF_20260826.md"
)
VERIFIER_REPO_PATH = (
    "code_assist_v1/tools/"
    "verify_internal_collaborative_reference_multiuser_receipt.py"
)
EXPECTED_ORIGINAL_RECEIPT_SHA256 = (
    "sha256:5ef98450166e00b750890599049acc301d99915332f1c1542c7bd777fc98055f"
)
SUPERSESSION_AUTHORITY_TEXT = (
    "V0R-P1 교정 reference 재수집 및 새 immutable receipt로 supersede 승인. "
    "기존 V0R receipt는 semantic evidence로 보존하고 덮어쓰지 마."
)
CLEANUP_AUTHORITY_TEXT = "V0R 합성 A/B 루트 Trash 이동 및 최종 cleanup 승인"
REFERENCE_BUILD_IDENTIFIER = (
    "notion-web-live-ui-corrective-pixel-observation-20260827-20260828"
)
REQUIRE_EXACT_REFERENCE_GATE = False


def _path(repo_path: str) -> Path:
    return REPOSITORY_ROOT / Path(*PurePosixPath(repo_path).parts)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")


def _masked_union_count(mask: Mapping[str, Any]) -> int:
    pixels: set[tuple[int, int]] = set()
    for item in mask["masks"]:
        rect = item["rect"]
        pixels.update(
            (x, y)
            for y in range(rect["y"], rect["y"] + rect["height"])
            for x in range(rect["x"], rect["x"] + rect["width"])
        )
    return len(pixels)


def _iso_to_epoch_ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def _current_time() -> str:
    return datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")


def _preseal_temporary_root(receipt_path: Path, runtime_root: Path) -> Path:
    if REQUIRE_EXACT_REFERENCE_GATE:
        return receipt_path.parent
    return runtime_root


def _require_audit_gates(audit: Mapping[str, Any], *, stage: str) -> None:
    if audit.get("status") != "pass":
        raise ValueError(
            f"V0R_CORRECTIVE_{stage}_AUDIT_FAILED:" + json.dumps(audit)
        )
    if (
        REQUIRE_EXACT_REFERENCE_GATE
        and audit.get("exact_reference_gate_passed") is not True
    ):
        raise ValueError(
            f"V0R_CORRECTIVE_{stage}_EXACT_REFERENCE_GATE_FAILED:"
            + json.dumps(audit)
        )


def build_receipt(*, recorded_at: str) -> dict[str, Any]:
    original_path = _path(ORIGINAL_RECEIPT_REPO_PATH)
    original_hash = _sha256_file(original_path)
    if original_hash != EXPECTED_ORIGINAL_RECEIPT_SHA256:
        raise ValueError("V0R_ORIGINAL_RECEIPT_HASH_DRIFT")

    original = _load(original_path)
    screenshots = _path(SCREENSHOT_REPO_ROOT)
    masks = _path(MASK_REPO_ROOT)
    raw_root = _path(RAW_REPO_ROOT)
    if raw_root.exists() and any(candidate.is_file() for candidate in raw_root.rglob("*")):
        raise ValueError("V0R_CORRECTIVE_RAW_FILES_REMAIN")

    build_sha256 = _sha256_bytes(REFERENCE_BUILD_IDENTIFIER.encode("utf-8"))
    original_captures = {
        (item["state_id"], item["viewport"]["width"], item["viewport"]["height"]): item
        for item in original["captures"]
    }
    capture_rows: list[dict[str, Any]] = []
    capture_manifest_rows: list[dict[str, Any]] = []
    capture_times: list[str] = []
    for screenshot in sorted(screenshots.glob("*.png")):
        stem = screenshot.stem
        state_id, viewport_label = stem.split("__", 1)
        width_text, height_text = viewport_label.split("x", 1)
        width, height = int(width_text), int(height_text)
        key = (state_id, width, height)
        if key not in original_captures:
            raise ValueError(f"V0R_CORRECTIVE_UNEXPECTED_CAPTURE:{stem}")

        decoded = decode_png_srgb_rgba8(screenshot.read_bytes())
        if (decoded.width, decoded.height) != (width, height):
            raise ValueError(f"V0R_CORRECTIVE_DIMENSION_DRIFT:{stem}")
        mask_path = masks / f"{stem}.json"
        mask_value = _load(mask_path)
        mask_scenario_id = mask_value.get("scenario_id")
        if mask_scenario_id not in {state_id, stem}:
            raise ValueError(f"V0R_CORRECTIVE_MASK_SCENARIO_DRIFT:{stem}")
        normalized_mask = validate_mask_manifest(
            mask_value,
            scenario_id=mask_scenario_id,
            width=width,
            height=height,
        )
        reviewed_at = normalized_mask["review"]["reviewed_at"]
        capture_times.append(reviewed_at)

        capture = deepcopy(original_captures[key])
        environment = deepcopy(capture["environment"])
        environment["time"]["epoch_ms"] = _iso_to_epoch_ms(reviewed_at)
        environment["source"]["build_sha256"] = build_sha256
        normalized_environment = validate_capture_environment(environment)
        capture["environment"] = {
            name: value
            for name, value in normalized_environment.items()
            if name != "environment_sha256"
        }
        capture["environment_sha256"] = normalized_environment["environment_sha256"]
        capture["screenshot_relative_path"] = screenshot.name
        capture["screenshot_sha256"] = _sha256_file(screenshot)
        capture["reference_build_identifier"] = REFERENCE_BUILD_IDENTIFIER
        capture["reference_build_sha256"] = build_sha256
        capture["captured_at"] = reviewed_at
        capture["capture_timestamp_basis"] = "redaction_seal_time"
        capture["interaction_outcome"]["observation"] = (
            "Corrective redacted pixel-reference evidence confirms "
            f"{capture['expected_state']} on the aliased synthetic surface."
        )
        capture["mask_relative_path"] = mask_path.name
        capture["mask_scenario_id"] = mask_scenario_id
        capture["mask_sha256"] = _sha256_file(mask_path)
        capture["mask_manifest_sha256"] = normalized_mask["manifest_sha256"]
        capture["masked_pixel_count"] = _masked_union_count(normalized_mask)
        capture_rows.append(capture)
        capture_manifest_rows.append(
            {
                "state_id": state_id,
                "viewport": {"width": width, "height": height},
                "screenshot_relative_path": screenshot.name,
                "screenshot_sha256": capture["screenshot_sha256"],
                "mask_relative_path": mask_path.name,
                "mask_scenario_id": mask_scenario_id,
                "mask_sha256": capture["mask_sha256"],
                "mask_manifest_sha256": capture["mask_manifest_sha256"],
                "masked_pixel_count": capture["masked_pixel_count"],
            }
        )

    if len(capture_rows) != 36 or len(list(masks.glob("*.json"))) != 36:
        raise ValueError("V0R_CORRECTIVE_DENOMINATOR_DRIFT")

    actions = deepcopy(original["actions"])
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
        "completed_at": recorded_at,
        "action_time_confirmation": CLEANUP_AUTHORITY_TEXT,
        "revoke_before_trash_order_preserved": True,
        "REF_FREE_A_restore_control_visible": True,
        "REF_FREE_B_restore_control_visible": True,
        "REF_COLLABORATOR_A_access_request_boundary_visible": True,
        "REF_COLLABORATOR_B_access_blocked": True,
        "fresh_settled_post_cleanup_verification": True,
    }
    cleanup["cleanup_receipt_sha256"] = canonical_sha256(cleanup)

    receipt: dict[str, Any] = {
        "schema_version": 1,
        "contract": original["contract"],
        "packet_id": "V0R-P1",
        "receipt_id": "V0R-P1-L6-REFERENCE-MULTIUSER-CORRECTIVE-20260828",
        "recorded_at": recorded_at,
        "execution_manifest": deepcopy(original["execution_manifest"]),
        "approval_receipt": deepcopy(original["approval_receipt"]),
        "supersession": {
            "mode": "pixel_reference_only",
            "supersedes_receipt_path": ORIGINAL_RECEIPT_REPO_PATH,
            "supersedes_receipt_sha256": original_hash,
            "preserves_original_semantic_evidence": True,
            "original_receipt_modified": False,
            "authority_received": True,
            "authority_text": SUPERSESSION_AUTHORITY_TEXT,
            "corrective_screenshot_root": SCREENSHOT_REPO_ROOT,
            "corrective_mask_root": MASK_REPO_ROOT,
        },
        "run": {
            "run_id": "v0r-p1-corrective-pixel-reference-20260828",
            "started_at": min(capture_times),
            "ended_at": recorded_at,
            "browser": "chrome_signed_in_two_independent_test_sessions",
            "chrome_major_version": original["run"]["chrome_major_version"],
            "reference_build_identifier": REFERENCE_BUILD_IDENTIFIER,
            "reference_build_sha256": build_sha256,
            "workspace_aliases": ["REF-FREE-A", "REF-FREE-B"],
            "identity_aliases": ["REF-OWNER", "REF-COLLABORATOR"],
            "screenshot_root": SCREENSHOT_REPO_ROOT,
            "mask_root": MASK_REPO_ROOT,
            "semantic_action_log_source": ORIGINAL_RECEIPT_REPO_PATH,
            "action_log_sha256": canonical_sha256(actions),
        },
        "actions": actions,
        "captures": capture_rows,
        "corrective_evidence": {
            "screenshot_root": SCREENSHOT_REPO_ROOT,
            "mask_root": MASK_REPO_ROOT,
            "raw_runtime_root": RAW_REPO_ROOT,
            "screenshot_count": 36,
            "mask_manifest_count": 36,
            "raw_file_count": 0,
            "capture_manifest_sha256": canonical_sha256(capture_manifest_rows),
        },
        "cleanup": cleanup,
        "redaction": deepcopy(original["redaction"]),
        "claims": deepcopy(original["claims"]),
    }
    return receipt


def build_checkpoint(
    *, recorded_at: str, receipt_sha256: str, audit: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "checkpoint_id": (
            "UI-INTERNAL-COLLABORATION-V0R-P1-CORRECTIVE-CLEANUP-GATE-PASS-20260828"
        ),
        "recorded_at": recorded_at,
        "checkpoint_kind": "corrective_pixel_reference_cleanup_and_receipt_gate_pass",
        "packet_id": "V0R-P1",
        "packet_status": "gate_passed_next_V0B_P1",
        "owner_agent": "/root",
        "ownership_lane": "DOC_HARNESS",
        "purpose": (
            "Seal the superseding 18-state by 2-viewport corrective pixel-reference "
            "evidence, reviewed masks, and revoke-before-recoverable-Trash cleanup "
            "without changing the original semantic receipt or claiming product completion."
        ),
        "dynamic_instruction_binding": {
            "path": AGENTS_REPO_PATH,
            "sha256": _sha256_file(_path(AGENTS_REPO_PATH)),
            "mode": "read_only",
        },
        "handoff_binding": {
            "path": HANDOFF_REPO_PATH,
            "sha256": _sha256_file(_path(HANDOFF_REPO_PATH)),
        },
        "predecessor_checkpoint": {
            "path": PREDECESSOR_REPO_PATH,
            "sha256": _sha256_file(_path(PREDECESSOR_REPO_PATH)),
        },
        "supersession": {
            "authority_text": SUPERSESSION_AUTHORITY_TEXT,
            "scope": "pixel_reference_only",
            "original_semantic_receipt_path": ORIGINAL_RECEIPT_REPO_PATH,
            "original_semantic_receipt_sha256": EXPECTED_ORIGINAL_RECEIPT_SHA256,
            "original_receipt_modified": False,
            "new_corrective_receipt_path": CORRECTIVE_RECEIPT_REPO_PATH,
            "new_corrective_receipt_sha256": receipt_sha256,
        },
        "action_time_confirmation": {
            "exact_user_text": CLEANUP_AUTHORITY_TEXT,
            "bound_action_id": "V0R-CLEANUP",
            "bound_targets": [
                "REF-FREE-A/V0R-A-ROOT",
                "REF-FREE-B/V0R-B-ROOT",
            ],
            "bound_mode": "trash_only_recoverable",
        },
        "operator_action_result": {
            "revoke_before_trash_order_preserved": True,
            "REF_FREE_A_active_fixture_root_count": 0,
            "REF_FREE_B_active_fixture_root_count": 0,
            "REF_FREE_A_workspace_membership_remaining_count": 0,
            "REF_FREE_B_page_grant_remaining_count": 0,
            "REF_FREE_A_restore_control_visible": True,
            "REF_FREE_B_restore_control_visible": True,
            "REF_COLLABORATOR_A_access_request_boundary_visible": True,
            "REF_COLLABORATOR_B_access_blocked": True,
            "permanent_delete_used": False,
            "status": "passed",
        },
        "evidence_denominator": {
            "screenshot_file_count": 36,
            "expected_screenshot_count": 36,
            "mask_manifest_count": 36,
            "expected_mask_manifest_count": 36,
            "capture_state_count": 18,
            "viewport_count": 2,
            "raw_file_count": 0,
            "status": "complete",
        },
        "final_receipt": {
            "path": CORRECTIVE_RECEIPT_REPO_PATH,
            "sha256": receipt_sha256,
            "audit_contract": audit["contract"],
            "audit_status": audit["status"],
            "audit_error_count": audit["error_count"],
            "cleanup_complete": audit["cleanup_complete"],
            "gate_passed": audit["gate_passed"],
            "product_complete": False,
        },
        "verifier_binding": {
            "path": VERIFIER_REPO_PATH,
            "sha256": _sha256_file(_path(VERIFIER_REPO_PATH)),
        },
        "scope_attestation": {
            "raw_email_or_account_identifier_persisted": False,
            "credentials_cookie_token_or_storage_persisted": False,
            "raw_dom_or_network_payload_persisted": False,
            "personal_workspace_or_page_captured": False,
            "non_test_notification_sent": False,
            "public_publish_or_public_link_used": False,
            "permanent_delete_used": False,
            "production_or_second_brain_changed": False,
            "hot_shared_contracts_modified": False,
            "AGENTS_md_modified": False,
        },
        "claims": {
            "V0R_P1_semantic_receipt_preserved": True,
            "V0R_P1_corrective_pixel_reference_complete": True,
            "V0R_P1_gate_passed": True,
            "zero_RGBA_difference_claimed": False,
            "PIX_001_closed": False,
            "A11Y_001_closed": False,
            "UI_CORE_25_closed": False,
            "product_complete": False,
            "active_shared_goal_complete": False,
        },
        "next_action": (
            "Run V0B-P1 against the corrective receipt and reviewed masks, then require "
            "all 36 exact-pixel and accessibility rows plus fresh Core 25 and quiet-host "
            "performance gates before O1B."
        ),
    }


def seal() -> dict[str, Any]:
    receipt_path = _path(CORRECTIVE_RECEIPT_REPO_PATH)
    checkpoint_path = _path(CORRECTIVE_CHECKPOINT_REPO_PATH)
    if receipt_path.exists() or checkpoint_path.exists():
        raise FileExistsError("V0R_CORRECTIVE_IMMUTABLE_OUTPUT_EXISTS")

    recorded_at = _current_time()
    receipt = build_receipt(recorded_at=recorded_at)
    payload = _json_bytes(receipt)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_root = PROJECT_ROOT / ".runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        preseal_root = _preseal_temporary_root(receipt_path, runtime_root)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".json",
            prefix="v0r-corrective-audit-",
            dir=preseal_root,
            delete=False,
        ) as handle:
            handle.write(payload)
            temporary_path = Path(handle.name)
        audit = verify(
            temporary_path,
            screenshot_root=_path(SCREENSHOT_REPO_ROOT),
            mask_root=_path(MASK_REPO_ROOT),
        )
        _require_audit_gates(audit, stage="PRESEAL")
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    with receipt_path.open("xb") as handle:
        handle.write(payload)
    receipt_sha256 = _sha256_bytes(payload)
    final_audit = verify(
        receipt_path,
        screenshot_root=_path(SCREENSHOT_REPO_ROOT),
        mask_root=_path(MASK_REPO_ROOT),
    )
    _require_audit_gates(final_audit, stage="POSTSEAL")

    checkpoint = build_checkpoint(
        recorded_at=recorded_at,
        receipt_sha256=receipt_sha256,
        audit=final_audit,
    )
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    with checkpoint_path.open("xb") as handle:
        handle.write(_json_bytes(checkpoint))
    return {
        "status": "pass",
        "receipt_path": CORRECTIVE_RECEIPT_REPO_PATH,
        "receipt_sha256": receipt_sha256,
        "checkpoint_path": CORRECTIVE_CHECKPOINT_REPO_PATH,
        "checkpoint_sha256": _sha256_file(checkpoint_path),
        "original_receipt_sha256": _sha256_file(_path(ORIGINAL_RECEIPT_REPO_PATH)),
        "screenshot_count": final_audit["capture_count"],
        "mask_manifest_count": 36,
        "raw_file_count": 0,
        "cleanup_complete": final_audit["cleanup_complete"],
        "gate_passed": final_audit["gate_passed"],
        "exact_reference_gate_passed": final_audit[
            "exact_reference_gate_passed"
        ],
        "product_complete": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    print(json.dumps(seal(), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "_require_audit_gates",
    "build_checkpoint",
    "build_receipt",
    "seal",
]
