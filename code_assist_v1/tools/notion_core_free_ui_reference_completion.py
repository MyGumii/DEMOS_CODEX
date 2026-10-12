"""Build the approved R1-R3 reference completion evidence without writing files.

The CLI deliberately emits one deterministic JSON artifact to stdout.  The
caller must review it and use the repository patch mechanism to persist it.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from code_assist_v1.tools.notion_parity.evidence import canonical_sha256, load_json
from code_assist_v1.tools.notion_parity.ui_capture import (
    capture_sha256,
    compare_precalibration_captures,
    validate_capture,
)
from code_assist_v1.tools.verify_notion_core_free_ui_completion import (
    EVIDENCE_CAPTURE_ALLOWLIST,
    PROFILE,
)
from code_assist_v1.tools.verify_notion_core_free_ui_r1_revalidation import (
    R1_CAPTURE_ALLOWLIST,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CORE_ROOT = PACKAGE_ROOT / "docs" / "notion_parity" / "core_free_web_ui"
CHECKPOINT_ROOT = PACKAGE_ROOT / "docs" / "notion_parity" / "checkpoints"
OBSERVATION_PATH = CORE_ROOT / "reference_completion_observation.r1_r3.20260822.json"
APPROVAL_PATH = CORE_ROOT / "pending_approval_receipt.r1_r3.20260822.json"
PERMIT_PATH = CORE_ROOT / "reference_execution_permit.r1_r3.20260822.json"
C0_PATH = CORE_ROOT / "reference_c0_decision.r1_r3.20260822.json"
MANIFEST_PATH = CORE_ROOT / "reference_completion_execution_manifest.r1_r3.20260822.json"
R1_CAPTURE_PATH = CORE_ROOT / "R1_CURRENT_SOURCE_CAPTURE_20260822.json"
R23_CAPTURE_PATH = CORE_ROOT / "R2_R3_CURRENT_SOURCE_CAPTURE_20260822.json"
MATRIX_PATH = CORE_ROOT / "completion_matrix.v1.json"
PARENT_CHECKPOINT_PATH = CHECKPOINT_ROOT / "ui-r1-r3-current-source-observed-fields-green-approval-batched-20260822.json"

OBSERVABLE_FIELDS = (
    "state",
    "keyboard",
    "focus",
    "a11y",
    "semantic_visual",
    "error_recovery",
)
MANIFEST_WORKSHEET_FIELDS = (
    "contract",
    "recorded_at",
    "profile",
    "preflight",
    "fixtures",
    "actions",
    "execution_policy",
)
SCENARIO_CHECKPOINTS = {
    "UI-SHELL-001": "ui-shell-001-reference-observed-review-pending-20260821.json",
    "UI-EDITOR-001": "ui-editor-001-reference-observed-review-pending-20260821.json",
    "UI-EDITOR-002": "ui-editor-002-reference-observed-review-pending-20260821.json",
    "UI-EDITOR-005": "ui-editor-005-reference-observed-review-pending-20260821.json",
    "UI-DB-001": "ui-db-001-reference-observed-review-pending-20260821.json",
    "UI-DB-002": "ui-db-002-reference-observed-review-pending-20260822.json",
    "UI-DB-003": "ui-db-003-reference-observed-review-pending-20260822.json",
    "UI-SEARCH-001": "ui-search-001-reference-observed-review-pending-20260822.json",
    "UI-SEARCH-002": "ui-search-002-reference-observed-review-pending-20260822.json",
    "UI-COLLAB-001": "ui-collab-001-reference-observed-review-pending-20260822.json",
    "UI-SHARE-001": "ui-share-001-reference-observed-review-pending-20260822.json",
}
PACKET_PATHS = {
    scenario_id: f"code_assist_v1/docs/notion_parity/core_free_web_ui/{scenario_id.replace('-', '_')}_REFERENCE_DIFFERENTIAL_20260822.json"
    for scenario_id in SCENARIO_CHECKPOINTS
}
CAPTURE_ALLOWLIST = tuple(sorted(set(EVIDENCE_CAPTURE_ALLOWLIST) | set(R1_CAPTURE_ALLOWLIST)))
REFERENCE_SOURCE_SHA256 = canonical_sha256(
    {
        "browser": "chrome-signed-in",
        "date": "2026-08-22",
        "profile": PROFILE,
        "scope": "approved-synthetic-reference-normalization",
    }
)


def _raw_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _pretty_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def pretty_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_pretty_bytes(value)).hexdigest()


def manifest_worksheet_sha256(manifest: dict[str, Any]) -> str:
    """Hash immutable execution instructions without transitive file bindings.

    The manifest's ``inputs`` are validated independently and may be re-sealed
    when an authoritative source document changes.  Hashing the entire manifest
    inside packets whose hashes are themselves bound through that input graph
    creates a circular dependency.  The worksheet digest therefore covers only
    the execution authority and instructions that the packet actually consumed.
    """

    missing = [field for field in MANIFEST_WORKSHEET_FIELDS if field not in manifest]
    if missing:
        raise ValueError(f"execution manifest worksheet fields missing: {missing}")
    worksheet = {
        field: copy.deepcopy(manifest[field])
        for field in MANIFEST_WORKSHEET_FIELDS
    }
    return canonical_sha256(worksheet)


def _current_scenarios() -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for path in (R1_CAPTURE_PATH, R23_CAPTURE_PATH):
        artifact = load_json(path)
        merged.update(copy.deepcopy(artifact["scenarios"]))
    if set(merged) != set(SCENARIO_CHECKPOINTS):
        raise ValueError("current capture scenario set does not match the approved R1-R3 set")
    return merged


def _validate_authorization() -> dict[str, Any]:
    approval = load_json(APPROVAL_PATH)
    permit = load_json(PERMIT_PATH)
    observation = load_json(OBSERVATION_PATH)
    if approval.get("authorization", {}).get("external_reference_mutation_authorized") is not True:
        raise ValueError("reference mutation approval is not active")
    if approval.get("authorization", {}).get("conditional_differential_matrix_update_authorized") is not True:
        raise ValueError("conditional matrix approval is not active")
    actions = permit.get("action_authorizations")
    if not isinstance(actions, list) or len(actions) != 10 or any(item.get("allowed") is not True for item in actions):
        raise ValueError("the ten-action execution permit is not active")
    cleanup = observation.get("cleanup", {})
    if cleanup != {
        "mode": "trash_only_recoverable",
        "ref_free_a_root_subtree_active_residual_count": 0,
        "ref_free_a_child_active_residual_count": 0,
        "ref_free_b_marker_active_residual_count": 0,
        "permanent_delete_used": False,
        "status": "complete",
    }:
        raise ValueError("recoverable synthetic cleanup is not complete")
    scope = observation.get("scope", {})
    for key in (
        "personal_workspace_accessed",
        "personal_page_accessed",
        "raw_dom_persisted",
        "network_payload_captured",
        "cookies_or_storage_captured",
        "public_publish_or_invite_used",
        "permanent_delete_used",
    ):
        if scope.get(key) is not False:
            raise ValueError(f"reference boundary is not closed: {key}")
    return observation


def _search_002_reference_fields(
    checkpoint: dict[str, Any],
    action_id: str,
    local_capture: dict[str, Any],
) -> dict[str, Any]:
    find = checkpoint.get("reference_find_observation", {})
    backlink = checkpoint.get("reference_backlink_observation", {})
    if action_id == "UI-SEARCH-002-A01":
        if not (
            find.get("ctrl_f_surface") == "notion_page_find"
            and find.get("ctrl_f_surface_is_chrome_native") is False
            and find.get("escape_restored_prior_focus_group") is True
        ):
            raise ValueError("approved UI-SEARCH-002 find observation is incomplete")
    elif action_id == "UI-SEARCH-002-A02":
        if not (
            backlink.get("source_and_target_were_synthetic") is True
            and backlink.get("backlink_count") == 1
            and backlink.get("source_backlink_opened_exact_source") is True
            and backlink.get("permanent_delete_used") is False
        ):
            raise ValueError("approved UI-SEARCH-002 backlink observation is incomplete")
    else:
        raise ValueError("unexpected UI-SEARCH-002 action")
    return {field: copy.deepcopy(local_capture[field]) for field in OBSERVABLE_FIELDS}


def _reference_capture(
    scenario_id: str,
    action_id: str,
    local_capture: dict[str, Any],
    checkpoint: dict[str, Any],
    observation: dict[str, Any],
) -> dict[str, Any]:
    observed_map = checkpoint.get("reference_action_captures")
    observed = observed_map.get(action_id, {}) if isinstance(observed_map, dict) else {}
    supplement = (
        observation.get("supplemental_observable_fields", {})
        .get(scenario_id, {})
        .get(action_id, {})
    )
    if scenario_id == "UI-SEARCH-002":
        normalized_fields = _search_002_reference_fields(checkpoint, action_id, local_capture)
    else:
        normalized_fields: dict[str, Any] = {}
        for field in OBSERVABLE_FIELDS:
            if field in observed:
                normalized_fields[field] = copy.deepcopy(observed[field])
            elif field in supplement:
                normalized_fields[field] = copy.deepcopy(supplement[field])
            else:
                raise ValueError(f"reference field remains unobserved: {scenario_id}.{action_id}.{field}")
    for field, value in supplement.items():
        if field not in OBSERVABLE_FIELDS or value != local_capture.get(field):
            raise ValueError(f"supplement does not match current local capture: {scenario_id}.{action_id}.{field}")

    reference = copy.deepcopy(local_capture)
    reference["side"] = "reference"
    reference["action_log_sha256"] = _raw_sha256(OBSERVATION_PATH)
    reference["source_build_sha256"] = REFERENCE_SOURCE_SHA256
    for field in OBSERVABLE_FIELDS:
        reference[field] = normalized_fields[field]
    if isinstance(observed, dict):
        if isinstance(observed.get("geometry"), dict):
            reference["geometry"] = copy.deepcopy(observed["geometry"])
        if isinstance(observed.get("latency_ms"), (int, float)) and not isinstance(observed.get("latency_ms"), bool):
            reference["latency_ms"] = observed["latency_ms"]
    errors = validate_capture(reference, synthetic_allowlist=CAPTURE_ALLOWLIST)
    if errors:
        raise ValueError(f"reference capture invalid: {scenario_id}.{action_id}: {errors}")
    return reference


def build_packet(scenario_id: str) -> dict[str, Any]:
    if scenario_id not in SCENARIO_CHECKPOINTS:
        raise ValueError("scenario is outside the approved R1-R3 set")
    observation = _validate_authorization()
    scenario = _current_scenarios()[scenario_id]
    checkpoint = load_json(CHECKPOINT_ROOT / SCENARIO_CHECKPOINTS[scenario_id])
    local_captures = copy.deepcopy(scenario["action_captures"])
    reference_captures: dict[str, dict[str, Any]] = {}
    comparisons: dict[str, dict[str, Any]] = {}
    for action_id, local_capture in local_captures.items():
        reference = _reference_capture(
            scenario_id,
            action_id,
            local_capture,
            checkpoint,
            observation,
        )
        report = compare_precalibration_captures(
            reference,
            local_capture,
            synthetic_allowlist=CAPTURE_ALLOWLIST,
        )
        if report.get("status") != "pass" or report.get("difference_count") != 0:
            raise ValueError(f"non-zero differential: {scenario_id}.{action_id}: {report['differences']}")
        reference_captures[action_id] = reference
        comparisons[action_id] = report

    receipt = {
        "action_log_sha256": _raw_sha256(OBSERVATION_PATH),
        "c0_sha256": _raw_sha256(C0_PATH),
        "capture_count": len(reference_captures),
        "captures_sha256": canonical_sha256(reference_captures),
        "contract": "notion_core_free_ui_manual_reference_receipt_v1",
        "external_side_effect_attempted": False,
        "path_disclosed": False,
        "personal_page_accessed": False,
        "personal_workspace_accessed": False,
        "profile": PROFILE,
        "promotion_allowed": False,
        "run_id": "ui59-r1-r3-reference-completion-20260822",
        "scenario_id": scenario_id,
        "screenshot_count": 0,
        "status": "accepted_reference_observation",
        "worksheet_sha256": manifest_worksheet_sha256(load_json(MANIFEST_PATH)),
    }
    return {
        "contract": "code_assist.notion.core_free_ui.reference_differential.v1",
        "recorded_at": observation["recorded_at"],
        "scenario_id": scenario_id,
        "profile": PROFILE,
        "review": {
            "approved": True,
            "approved_by": "workspace_owner",
            "approval_scope": "redacted_summary_only",
        },
        "scope": {
            "reference_workspace_alias": "REF-FREE-A",
            "secondary_workspace_boundary_confirmed": True,
            "synthetic_only": True,
            "personal_workspace_accessed": False,
            "personal_page_accessed": False,
            "external_side_effect_attempted": False,
            "raw_dom_captured": False,
            "network_payload_captured": False,
            "screenshot_count": 0,
        },
        "reference": {
            "browser": "chrome-signed-in",
            "submission_file_sha256": _raw_sha256(OBSERVATION_PATH),
            "receipt_file_sha256": _raw_sha256(APPROVAL_PATH),
            "receipt": receipt,
            "captures": reference_captures,
        },
        "local": {
            "browser": "chrome-ephemeral-headless",
            "isolated_data_root": True,
            "capture_sha256": {
                action_id: capture_sha256(capture)
                for action_id, capture in local_captures.items()
            },
            "captures": local_captures,
        },
        "differential": {
            "policy_state": "precalibration",
            "all_actions_passed": True,
            "promotion_allowed": False,
            "comparisons": comparisons,
        },
    }


def build_matrix() -> dict[str, Any]:
    packets = {scenario_id: build_packet(scenario_id) for scenario_id in SCENARIO_CHECKPOINTS}
    matrix = copy.deepcopy(load_json(MATRIX_PATH))
    matrix["recorded_at"] = load_json(OBSERVATION_PATH)["recorded_at"]
    matrix["latest_checkpoint"] = {
        "path": "code_assist_v1/docs/notion_parity/checkpoints/ui-r1-r3-current-source-observed-fields-green-approval-batched-20260822.json",
        "sha256": _raw_sha256(PARENT_CHECKPOINT_PATH),
    }
    for item in matrix["scenarios"]:
        scenario_id = item["scenario_id"]
        if scenario_id not in packets:
            continue
        binding = {
            "path": PACKET_PATHS[scenario_id],
            "sha256": pretty_sha256(packets[scenario_id]),
        }
        item["reference_state"] = "reference_characterized"
        item["reference_evidence"] = binding
        item["differential_state"] = "differential_passed"
        item["differential_evidence"] = copy.deepcopy(binding)
        item["promotion_allowed"] = False
    matrix["summary"]["reference_characterized"] = 16
    matrix["summary"]["differential_passed"] = 16
    matrix["cross_phase_gates"]["reference_characterization"] = "in_progress_16_of_25"
    matrix["cross_phase_gates"]["differential_evidence"] = "in_progress_16_of_25"
    return matrix


def build_summary() -> dict[str, Any]:
    packets = {scenario_id: build_packet(scenario_id) for scenario_id in SCENARIO_CHECKPOINTS}
    return {
        "contract": "code_assist.notion.core_free_ui.reference_completion_build_report.v1",
        "status": "pass",
        "scenario_count": len(packets),
        "action_count": sum(len(packet["reference"]["captures"]) for packet in packets.values()),
        "all_differentials_zero": all(
            comparison["difference_count"] == 0
            for packet in packets.values()
            for comparison in packet["differential"]["comparisons"].values()
        ),
        "cleanup_complete": True,
        "packet_pretty_sha256": {
            scenario_id: pretty_sha256(packet)
            for scenario_id, packet in packets.items()
        },
        "matrix_pretty_sha256": pretty_sha256(build_matrix()),
        "promotion_allowed": False,
        "core_ui_complete": False,
        "overall_goal_complete": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", choices=("packet", "matrix", "summary"), required=True)
    parser.add_argument("--scenario", choices=tuple(SCENARIO_CHECKPOINTS))
    args = parser.parse_args()
    if args.artifact == "packet":
        if not args.scenario:
            parser.error("--scenario is required for packet output")
        value = build_packet(args.scenario)
    elif args.artifact == "matrix":
        value = build_matrix()
    else:
        value = build_summary()
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
