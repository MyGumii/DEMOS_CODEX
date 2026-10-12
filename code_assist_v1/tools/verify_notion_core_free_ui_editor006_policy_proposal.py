"""Verify the pending UI-EDITOR-006 local recovery evidence policy proposal."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from code_assist_v1.tools.verify_notion_core_free_ui_completion import (
    PROFILE,
    RFC3339_PATTERN,
    SHA_PATTERN,
    _file_sha256,
    _safe_repo_path,
    load_json,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROPOSAL = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "editor006_local_recovery_evidence_policy_proposal.v1.json"
)
CONTRACT = "code_assist.notion.core_free_ui.editor006_evidence_policy_proposal.v1"
EXACT_APPROVAL_TEXT = "UI-EDITOR-006 공식 실패 유도 제외 및 로컬 회복 계약 승인"
EXPECTED_ASSERTIONS = [
    "failed_save_keeps_dirty_draft",
    "reload_restores_tab_scoped_draft_without_server_acknowledgement",
    "retry_reuses_exact_operation_id_and_request_body",
    "stale_server_revision_restores_explicit_conflict_state",
    "conflict_disables_automatic_save_until_user_decision",
    "discard_loads_latest_server_revision_and_removes_draft",
    "successful_save_removes_draft",
    "second_brain_item_delta_is_zero",
    "second_brain_revision_delta_is_zero",
]
EXPECTED_DENIALS = [
    "official_notion_failure_ui_parity_claim",
    "official_notion_error_wording_or_timing_claim",
    "collaborative_realtime_merge_parity_claim",
    "personal_workspace_access",
    "network_or_devtools_failure_injection_against_notion",
    "completion_of_ui_perf_001",
    "h8_or_h9_completion_claim",
    "release_promotion",
]


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _fields(
    value: Any,
    expected: set[str],
    subject: str,
    errors: list[dict[str, str]],
) -> bool:
    if not isinstance(value, dict):
        errors.append(_error("OBJECT_REQUIRED", subject, "object required"))
        return False
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    errors.extend(
        _error("FIELD_MISSING", f"{subject}.{key}", "required field missing")
        for key in missing
    )
    errors.extend(
        _error("UNKNOWN_FIELD", f"{subject}.{key}", "unknown field")
        for key in extra
    )
    return not missing and not extra


def _binding(
    value: Any,
    subject: str,
    errors: list[dict[str, str]],
) -> Path | None:
    if not _fields(value, {"path", "sha256"}, subject, errors):
        return None
    path = _safe_repo_path(value.get("path"))
    digest = value.get("sha256")
    if path is None:
        errors.append(_error("PATH_INVALID", f"{subject}.path", "safe repository path required"))
        return None
    if not path.is_file():
        errors.append(_error("BOUND_FILE_MISSING", f"{subject}.path", "bound file missing"))
        return None
    if not isinstance(digest, str) or not SHA_PATTERN.fullmatch(digest):
        errors.append(_error("SHA256_INVALID", f"{subject}.sha256", "canonical sha256 required"))
    elif _file_sha256(path) != digest:
        errors.append(_error("BOUND_FILE_HASH_MISMATCH", subject, "bound file hash mismatch"))
    return path


def verify_editor006_policy_proposal(
    path: Path = DEFAULT_PROPOSAL,
) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    try:
        value = load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": CONTRACT,
            "status": "fail",
            "errors": [_error("PROPOSAL_READ_FAILED", "$", str(exc))],
        }, 1

    top = {
        "contract",
        "recorded_at",
        "scenario_id",
        "profile",
        "status",
        "proposed_evidence_mode",
        "evidence",
        "local_contract",
        "reference_boundary",
        "decision",
        "safety",
    }
    if not _fields(value, top, "$", errors):
        return {"contract": CONTRACT, "status": "fail", "errors": errors}, 1
    expected_scalars = {
        "contract": CONTRACT,
        "scenario_id": "UI-EDITOR-006",
        "profile": PROFILE,
        "status": "pending_owner_approval",
        "proposed_evidence_mode": "local_failure_recovery_contract",
    }
    for key, expected in expected_scalars.items():
        if value.get(key) != expected:
            errors.append(_error("VALUE_INVALID", key, f"expected {expected}"))

    recorded_at = value.get("recorded_at")
    try:
        valid_time = (
            isinstance(recorded_at, str)
            and RFC3339_PATTERN.fullmatch(recorded_at) is not None
            and datetime.fromisoformat(recorded_at.replace("Z", "+00:00")).utcoffset()
            is not None
        )
    except ValueError:
        valid_time = False
    if not valid_time:
        errors.append(_error("RECORDED_AT_INVALID", "recorded_at", "RFC 3339 timestamp required"))

    evidence = value.get("evidence")
    evidence_paths: dict[str, Path] = {}
    expected_evidence = {
        "local_result",
        "current_browser_checkpoint",
        "scenario_manifest",
        "completion_matrix_before_decision",
    }
    if _fields(evidence, expected_evidence, "evidence", errors):
        for key in sorted(expected_evidence):
            candidate = _binding(evidence.get(key), f"evidence.{key}", errors)
            if candidate is not None:
                evidence_paths[key] = candidate

    local_contract = value.get("local_contract")
    if _fields(local_contract, {"required_assertions", "execution_boundary"}, "local_contract", errors):
        if local_contract.get("required_assertions") != EXPECTED_ASSERTIONS:
            errors.append(_error("ASSERTIONS_INVALID", "local_contract.required_assertions", "exact recovery assertions required"))
        expected_execution = {
            "isolated_os_temp_only": True,
            "synthetic_accounts_and_pages_only": True,
            "real_browser_required": True,
            "live_local_data_allowed": False,
            "personal_notion_content_allowed": False,
        }
        execution = local_contract.get("execution_boundary")
        if not _fields(execution, set(expected_execution), "local_contract.execution_boundary", errors) or execution != expected_execution:
            errors.append(_error("EXECUTION_BOUNDARY_INVALID", "local_contract.execution_boundary", "safe local boundary required"))

    expected_reference = {
        "official_notion_failure_injection_required": False,
        "official_notion_failure_or_stale_state_observed": False,
        "reason": "Normal signed-in UI use does not provide a safe deterministic way to force Notion network-save failure or a stale-revision race, and inducing one would require unapproved transport manipulation or concurrent mutation.",
        "reference_state_if_approved": "safely_inapplicable_for_failure_injection",
        "differential_state_if_approved": "not_applicable_local_recovery_contract",
    }
    reference = value.get("reference_boundary")
    if not _fields(reference, set(expected_reference), "reference_boundary", errors) or reference != expected_reference:
        errors.append(_error("REFERENCE_BOUNDARY_INVALID", "reference_boundary", "reference claim boundary mismatch"))

    expected_decision = {
        "required_user_text": EXACT_APPROVAL_TEXT,
        "effect_if_approved": "allow_ui_editor_006_local_failure_recovery_evidence_satisfaction",
        "does_not_allow": EXPECTED_DENIALS,
    }
    decision = value.get("decision")
    if not _fields(decision, set(expected_decision), "decision", errors) or decision != expected_decision:
        errors.append(_error("DECISION_INVALID", "decision", "exact owner decision required"))

    expected_safety = {
        "external_state_change": False,
        "browser_control_required_for_approval": False,
        "approval_is_never_inferred": True,
        "completion_matrix_mutated": False,
        "product_code_mutated": False,
    }
    safety = value.get("safety")
    if not _fields(safety, set(expected_safety), "safety", errors) or safety != expected_safety:
        errors.append(_error("SAFETY_INVALID", "safety", "proposal safety boundary mismatch"))

    matrix_path = evidence_paths.get("completion_matrix_before_decision")
    if matrix_path is not None:
        matrix = load_json(matrix_path)
        scenarios = matrix.get("scenarios") if isinstance(matrix, dict) else None
        matching = [
            item
            for item in scenarios or []
            if isinstance(item, dict) and item.get("scenario_id") == "UI-EDITOR-006"
        ]
        if len(matching) != 1 or matching[0].get("local_state") != "local_real_browser_green" or matching[0].get("promotion_allowed") is not False:
            errors.append(_error("MATRIX_PRECONDITION_INVALID", "evidence.completion_matrix_before_decision", "UI-EDITOR-006 must remain local-green and unpromoted"))

    checkpoint_path = evidence_paths.get("current_browser_checkpoint")
    if checkpoint_path is not None:
        checkpoint = load_json(checkpoint_path)
        slice_value = checkpoint.get("slice") if isinstance(checkpoint, dict) else None
        if not isinstance(slice_value, dict) or slice_value.get("review_required") is not True or slice_value.get("promotion_allowed") is not False:
            errors.append(_error("CHECKPOINT_PRECONDITION_INVALID", "evidence.current_browser_checkpoint", "review-pending checkpoint required"))

    report = {
        "contract": CONTRACT,
        "status": "fail" if errors else "pass",
        "scenario_id": "UI-EDITOR-006",
        "proposal_state": value.get("status"),
        "required_user_text": EXACT_APPROVAL_TEXT,
        "external_state_change": False,
        "errors": errors,
    }
    return report, 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", type=Path, default=DEFAULT_PROPOSAL)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_editor006_policy_proposal(args.proposal)
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.compact else 2,
            separators=(",", ":") if args.compact else None,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
