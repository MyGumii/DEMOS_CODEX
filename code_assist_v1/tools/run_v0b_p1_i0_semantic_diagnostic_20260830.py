"""Run only I0 against the lossy Chrome reference as non-gating diagnostics."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from code_assist_v1.notion_collaboration.v0b_p1_browser import _browser_version
from code_assist_v1.notion_collaboration.v0b_p1_capture import V0BP1Collector
from code_assist_v1.notion_collaboration.v0b_p1_iam_browser import (
    run_v0b_p1_iam_journey,
)
from code_assist_v1.tools.verify_internal_collaborative_v0b_p1_receipt import (
    current_runtime_source_binding,
    current_source_binding,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_EVIDENCE_ROOT = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "evidence"
    / "internal_collaboration_reference"
    / "v0r-p1-lossless-corrective-20260830"
)
REFERENCE_RECEIPT_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "evidence"
    / "internal_collaboration_receipts"
    / "v0r-p1-l6-reference-multiuser-lossless-corrective-v2-20260830.json"
)
OUTPUT_ROOT = (
    PROJECT_ROOT / ".runtime" / "v0b-p1-i0-v2-semantic-diagnostic-20260830-retry67"
)
I0_STATE_IDS = {
    "V0R-MU-001",
    "V0R-MU-002",
    "V0R-MU-003",
    "V0R-MU-004",
    "V0R-MU-005",
    "V0R-MU-006",
    "V0R-MU-015",
    "V0R-MU-016",
    "V0R-MU-017",
    "V0R-MU-018",
}
SEMANTIC_REFERENCE_EXCLUSIONS = {
    "V0R-MU-004": (
        "reference shows the pre-submit invite composer although the contract requires "
        "post-submit edit_access_active"
    ),
    "V0R-MU-006": (
        "reference omits the page-share dialog although the contract requires "
        "inherited_workspace_access_explained"
    ),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_new_json(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _semantic_suitability(
    state_totals: dict[str, dict[str, int]],
) -> dict[str, Any]:
    if set(state_totals) != I0_STATE_IDS:
        raise RuntimeError("V0B_I0_SEMANTIC_SUITABILITY_DENOMINATOR")
    eligible_state_ids = sorted(I0_STATE_IDS - set(SEMANTIC_REFERENCE_EXCLUSIONS))
    excluded_state_ids = sorted(SEMANTIC_REFERENCE_EXCLUSIONS)

    def total(field: str, state_ids: list[str]) -> int:
        return sum(int(state_totals[state_id][field]) for state_id in state_ids)

    return {
        "audit_contract": (
            "code_assist.notion.internal_collaborative_v0b_p1."
            "semantic_reference_suitability_audit.v1"
        ),
        "audit_checkpoint_path": (
            "code_assist_v1/docs/notion_parity/checkpoints/"
            "ui-internal-collaboration-v0b-p1-v2-semantic-reference-"
            "suitability-audit-20260830.json"
        ),
        "reference_fully_usable": False,
        "eligible_state_ids": eligible_state_ids,
        "eligible_state_count": len(eligible_state_ids),
        "eligible_capture_count": len(eligible_state_ids) * 2,
        "eligible_unmasked_difference_count": total(
            "unmasked_differing_pixel_count", eligible_state_ids
        ),
        "eligible_masked_difference_count": total(
            "masked_differing_pixel_count", eligible_state_ids
        ),
        "excluded_states": [
            {
                "state_id": state_id,
                "capture_count": 2,
                "reason": SEMANTIC_REFERENCE_EXCLUSIONS[state_id],
                "unmasked_difference_count": int(
                    state_totals[state_id]["unmasked_differing_pixel_count"]
                ),
                "masked_difference_count": int(
                    state_totals[state_id]["masked_differing_pixel_count"]
                ),
            }
            for state_id in excluded_state_ids
        ],
        "excluded_state_count": len(excluded_state_ids),
        "excluded_capture_count": len(excluded_state_ids) * 2,
        "excluded_unmasked_difference_count": total(
            "unmasked_differing_pixel_count", excluded_state_ids
        ),
        "excluded_masked_difference_count": total(
            "masked_differing_pixel_count", excluded_state_ids
        ),
        "policy": {
            "excluded_states_used_for_visual_optimization": False,
            "any_state_used_for_exact_RGBA": False,
        },
    }


def main() -> int:
    if OUTPUT_ROOT.exists():
        raise FileExistsError(OUTPUT_ROOT)
    started_at = _now()
    browser_version = _browser_version()
    collector = V0BP1Collector(
        reference_receipt_path=REFERENCE_RECEIPT_PATH,
        reference_screenshot_root=REFERENCE_EVIDENCE_ROOT / "screenshots",
        reference_mask_root=REFERENCE_EVIDENCE_ROOT / "masks",
        candidate_screenshot_root=OUTPUT_ROOT / "screenshots",
        diff_root=OUTPUT_ROOT / "diffs",
        browser_version=browser_version,
        exact_gate_required=False,
    )
    journey = run_v0b_p1_iam_journey(
        agent_origin="http://127.0.0.1:10111",
        notion_origin="http://127.0.0.1:10112",
        credentials={"admin": "admin1234", "user01": "user1234"},
        v0b_collector=collector,
    )
    actual_states = {str(item["state_id"]) for item in collector.captures}
    if actual_states != I0_STATE_IDS or len(collector.captures) != 20:
        raise RuntimeError("V0B_I0_DIAGNOSTIC_CAPTURE_DENOMINATOR")
    state_totals: dict[str, dict[str, int]] = {}
    for state_id in sorted(I0_STATE_IDS):
        rows = [item for item in collector.captures if item["state_id"] == state_id]
        state_totals[state_id] = {
            "unmasked_differing_pixel_count": sum(
                int(item["exact_pixel_report"]["differing_pixel_count"])
                for item in rows
            ),
            "masked_differing_pixel_count": sum(
                int(item["exact_pixel_report"]["masked_differing_pixel_count"])
                for item in rows
            ),
        }
    semantic_suitability = _semantic_suitability(state_totals)
    for capture in collector.captures:
        state_id = str(capture["state_id"])
        capture["semantic_reference_eligible"] = (
            state_id not in SEMANTIC_REFERENCE_EXCLUSIONS
        )
        if state_id in SEMANTIC_REFERENCE_EXCLUSIONS:
            capture["semantic_reference_exclusion_reason"] = (
                SEMANTIC_REFERENCE_EXCLUSIONS[state_id]
            )
    receipt = {
        "schema_version": 1,
        "contract": "code_assist.notion.v0b_p1_i0_semantic_diagnostic.v1",
        "status": "pass",
        "recorded_at": _now(),
        "started_at": started_at,
        "reference_receipt": {
            "path": REFERENCE_RECEIPT_PATH.relative_to(PROJECT_ROOT.parent).as_posix(),
            "sha256": _sha256(REFERENCE_RECEIPT_PATH),
            "semantic_reference_only": True,
            "exact_gate_eligible": False,
        },
        "release_candidate": current_source_binding(),
        "runtime_candidate": current_runtime_source_binding(),
        "browser": {"channel": "chrome", "version": browser_version},
        "capture_count": len(collector.captures),
        "capture_state_count": len(actual_states),
        "state_totals": state_totals,
        "semantic_suitability": semantic_suitability,
        "captures": collector.captures,
        "journey": journey,
        "cleanup": {
            "test_root_moved_to_trash": journey.get("test_root_moved_to_trash") is True,
            "synthetic_member_absent_after_cleanup": journey.get(
                "synthetic_member_absent_after_cleanup"
            )
            is True,
            "browser_closed": journey.get("browser", {}).get("browser_closed") is True,
            "permanent_delete_used": False,
        },
        "claims": {
            "semantic_diagnostic_complete": True,
            "semantic_reference_fully_usable": False,
            "exact_RGBA_gate_passed": False,
            "reference_transport_lossless": False,
            "product_complete": False,
            "active_shared_goal_complete": False,
        },
    }
    _write_new_json(OUTPUT_ROOT / "diagnostic.json", receipt)
    print(json.dumps({
        "status": "pass",
        "output": str(OUTPUT_ROOT / "diagnostic.json"),
        "state_totals": state_totals,
        "cleanup": receipt["cleanup"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
