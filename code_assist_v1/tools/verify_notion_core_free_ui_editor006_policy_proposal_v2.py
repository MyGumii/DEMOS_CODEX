"""Verify the corrected immutable UI-EDITOR-006 evidence-policy proposal."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from code_assist_v1.tools.verify_notion_core_free_ui_completion import (
    PROFILE,
    RFC3339_PATTERN,
    load_json,
)
from code_assist_v1.tools.verify_notion_core_free_ui_editor006_policy_proposal import (
    EXACT_APPROVAL_TEXT,
    EXPECTED_ASSERTIONS,
    EXPECTED_DENIALS,
    _binding,
    _error,
    _fields,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROPOSAL = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "editor006_local_recovery_evidence_policy_proposal.v2.json"
)
CONTRACT = "code_assist.notion.core_free_ui.editor006_evidence_policy_proposal.v2"
CHECKPOINT_CONTRACT = "code_assist.notion.core_free_ui.execution_checkpoint.v2"


def _valid_time(value: Any) -> bool:
    try:
        return (
            isinstance(value, str)
            and RFC3339_PATTERN.fullmatch(value) is not None
            and datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset()
            is not None
        )
    except ValueError:
        return False


def verify_editor006_policy_proposal_v2(
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
        "correction",
        "safety",
    }
    if not _fields(value, top, "$", errors):
        return {"contract": CONTRACT, "status": "fail", "errors": errors}, 1
    for key, expected in {
        "contract": CONTRACT,
        "scenario_id": "UI-EDITOR-006",
        "profile": PROFILE,
        "status": "owner_approved_corrected_binding",
        "proposed_evidence_mode": "local_failure_recovery_contract",
    }.items():
        if value.get(key) != expected:
            errors.append(_error("VALUE_INVALID", key, f"expected {expected}"))
    if not _valid_time(value.get("recorded_at")):
        errors.append(
            _error("RECORDED_AT_INVALID", "recorded_at", "RFC 3339 timestamp required")
        )

    evidence_paths: dict[str, Path] = {}
    evidence = value.get("evidence")
    expected_evidence = {
        "local_result",
        "current_browser_checkpoint",
        "scenario_manifest",
        "approval_pending_checkpoint",
    }
    if _fields(evidence, expected_evidence, "evidence", errors):
        for key in sorted(expected_evidence):
            candidate = _binding(evidence.get(key), f"evidence.{key}", errors)
            if candidate is not None:
                evidence_paths[key] = candidate

    local_contract = value.get("local_contract")
    if _fields(
        local_contract,
        {"required_assertions", "execution_boundary"},
        "local_contract",
        errors,
    ):
        if local_contract.get("required_assertions") != EXPECTED_ASSERTIONS:
            errors.append(
                _error(
                    "ASSERTIONS_INVALID",
                    "local_contract.required_assertions",
                    "exact recovery assertions required",
                )
            )
        expected_execution = {
            "isolated_os_temp_only": True,
            "synthetic_accounts_and_pages_only": True,
            "real_browser_required": True,
            "live_local_data_allowed": False,
            "personal_notion_content_allowed": False,
        }
        execution = local_contract.get("execution_boundary")
        if (
            not _fields(
                execution,
                set(expected_execution),
                "local_contract.execution_boundary",
                errors,
            )
            or execution != expected_execution
        ):
            errors.append(
                _error(
                    "EXECUTION_BOUNDARY_INVALID",
                    "local_contract.execution_boundary",
                    "safe local boundary required",
                )
            )

    expected_reference = {
        "official_notion_failure_injection_required": False,
        "official_notion_failure_or_stale_state_observed": False,
        "reason": "Normal signed-in UI use does not provide a safe deterministic way to force Notion network-save failure or a stale-revision race, and inducing one would require unapproved transport manipulation or concurrent mutation.",
        "reference_state_if_approved": "safely_inapplicable_for_failure_injection",
        "differential_state_if_approved": "not_applicable_local_recovery_contract",
    }
    reference = value.get("reference_boundary")
    if (
        not _fields(reference, set(expected_reference), "reference_boundary", errors)
        or reference != expected_reference
    ):
        errors.append(
            _error(
                "REFERENCE_BOUNDARY_INVALID",
                "reference_boundary",
                "reference claim boundary mismatch",
            )
        )

    expected_decision = {
        "required_user_text": EXACT_APPROVAL_TEXT,
        "effect_if_approved": "allow_ui_editor_006_local_failure_recovery_evidence_satisfaction",
        "does_not_allow": EXPECTED_DENIALS,
    }
    decision = value.get("decision")
    if (
        not _fields(decision, set(expected_decision), "decision", errors)
        or decision != expected_decision
    ):
        errors.append(
            _error("DECISION_INVALID", "decision", "exact owner decision required")
        )

    correction = value.get("correction")
    correction_paths: dict[str, Path] = {}
    correction_fields = {
        "reason_code",
        "superseded_proposal",
        "superseded_queue",
        "superseded_receipt",
        "approval_scope_changed",
        "authorization_effect_changed",
    }
    if _fields(correction, correction_fields, "correction", errors):
        if correction.get("reason_code") != "mutable_predecision_matrix_binding":
            errors.append(
                _error("CORRECTION_REASON_INVALID", "correction.reason_code", "exact correction reason required")
            )
        for key in ("superseded_proposal", "superseded_queue", "superseded_receipt"):
            candidate = _binding(correction.get(key), f"correction.{key}", errors)
            if candidate is not None:
                correction_paths[key] = candidate
        if correction.get("approval_scope_changed") is not False:
            errors.append(
                _error("APPROVAL_SCOPE_CHANGED", "correction.approval_scope_changed", "approval scope must be unchanged")
            )
        if correction.get("authorization_effect_changed") is not False:
            errors.append(
                _error("AUTHORIZATION_EFFECT_CHANGED", "correction.authorization_effect_changed", "authorization effect must be unchanged")
            )

    expected_safety = {
        "external_state_change": False,
        "browser_control_required_for_approval": False,
        "approval_is_never_inferred": True,
        "completion_matrix_mutated_by_proposal": False,
        "product_code_mutated": False,
    }
    safety = value.get("safety")
    if (
        not _fields(safety, set(expected_safety), "safety", errors)
        or safety != expected_safety
    ):
        errors.append(
            _error("SAFETY_INVALID", "safety", "corrected proposal safety boundary mismatch")
        )

    checkpoint_path = evidence_paths.get("approval_pending_checkpoint")
    if checkpoint_path is not None:
        try:
            checkpoint = load_json(checkpoint_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(
                _error("CHECKPOINT_READ_FAILED", "evidence.approval_pending_checkpoint", str(exc))
            )
        else:
            scenario = checkpoint.get("scenario") if isinstance(checkpoint, dict) else None
            approval = checkpoint.get("approval") if isinstance(checkpoint, dict) else None
            claims = checkpoint.get("claims") if isinstance(checkpoint, dict) else None
            if (
                checkpoint.get("contract") != CHECKPOINT_CONTRACT
                or not isinstance(scenario, dict)
                or scenario.get("scenario_id") != "UI-EDITOR-006"
                or scenario.get("approval_state") != "pending_exact_user_text"
                or scenario.get("promotion_allowed_before_receipt") is not False
                or scenario.get("completion_matrix_changed") is not False
                or not isinstance(approval, dict)
                or approval.get("exact_user_text") != EXACT_APPROVAL_TEXT
                or approval.get("authorization_effect")
                != "allow_ui_editor_006_local_failure_recovery_evidence_satisfaction"
                or approval.get("external_state_change") is not False
                or approval.get("approval_inferred") is not False
                or not isinstance(claims, dict)
                or claims.get("pending_approval_count") != 1
                or claims.get("ui_editor_006_complete") is not False
                or claims.get("core_ui_complete") is not False
            ):
                errors.append(
                    _error(
                        "APPROVAL_CHECKPOINT_INVALID",
                        "evidence.approval_pending_checkpoint",
                        "immutable approval-pending checkpoint does not preserve the exact decision boundary",
                    )
                )

    current_path = evidence_paths.get("current_browser_checkpoint")
    if current_path is not None:
        checkpoint = load_json(current_path)
        slice_value = checkpoint.get("slice") if isinstance(checkpoint, dict) else None
        if (
            not isinstance(slice_value, dict)
            or slice_value.get("review_required") is not True
            or slice_value.get("promotion_allowed") is not False
        ):
            errors.append(
                _error(
                    "CHECKPOINT_PRECONDITION_INVALID",
                    "evidence.current_browser_checkpoint",
                    "review-pending browser checkpoint required",
                )
            )

    expected_superseded = {
        "superseded_proposal": "code_assist.notion.core_free_ui.editor006_evidence_policy_proposal.v1",
        "superseded_queue": "code_assist.notion.core_free_ui.approval_batch_queue.v3",
        "superseded_receipt": "code_assist.notion.core_free_ui.approval_batch_receipt.v3",
    }
    for key, expected_contract in expected_superseded.items():
        bound_path = correction_paths.get(key)
        if bound_path is not None:
            try:
                bound_value = load_json(bound_path)
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                errors.append(_error("SUPERSEDED_READ_FAILED", f"correction.{key}", str(exc)))
            else:
                if not isinstance(bound_value, dict) or bound_value.get("contract") != expected_contract:
                    errors.append(
                        _error("SUPERSEDED_CONTRACT_INVALID", f"correction.{key}", "superseded contract mismatch")
                    )

    report = {
        "contract": CONTRACT,
        "status": "fail" if errors else "pass",
        "scenario_id": "UI-EDITOR-006",
        "proposal_state": value.get("status"),
        "required_user_text": EXACT_APPROVAL_TEXT,
        "approval_scope_changed": False,
        "authorization_effect_changed": False,
        "external_state_change": False,
        "errors": errors,
    }
    return report, 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", type=Path, default=DEFAULT_PROPOSAL)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_editor006_policy_proposal_v2(args.proposal)
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
