"""Verify the pre-promotion Core Free UI evidence review gate.

A passing report proves that every required scenario is categorized exactly
once, every pending checkpoint is hash-bound, and an approved decision is
backed by the exact owner-approval receipt.  It deliberately does not change
the completion matrix.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from code_assist_v1.tools.verify_notion_core_free_ui_completion import (
    DEFAULT_MATRIX,
    DEFAULT_PLAN,
    PROFILE,
    RFC3339_PATTERN,
    SHA_PATTERN,
    _file_sha256,
    _safe_repo_path,
    load_json,
    verify_completion_matrix,
)
from code_assist_v1.tools.notion_core_free_ui_approval_batch_v4 import (
    verify_approval_batch_receipt_v4,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GATE = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "evidence_review_gate.v1.json"
)
DEFAULT_APPROVAL_RECEIPT = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "owner_approval_receipt.20260822.json"
)
DEFAULT_EDITOR006_POLICY_RECEIPT = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "approval_batch_receipt.v4.20260822.json"
)

CONTRACT = "code_assist.notion.core_free_ui.evidence_review_gate.v1"
CHECKPOINT_CONTRACT = "code_assist.notion.core_free_ui.execution_checkpoint.v2"
APPROVAL_RECEIPT_CONTRACT = (
    "code_assist.notion.core_free_ui.owner_approval_receipt.v1"
)
APPROVAL_THREAD_ID = "019ff607-9116-78a1-9f40-c580c87c0038"
EXPECTED_TOP_FIELDS = {
    "contract",
    "recorded_at",
    "profile",
    "inputs",
    "categories",
    "review_decisions",
    "claims",
}
EXPECTED_CATEGORY_FIELDS = {
    "certified",
    "reference_observed_review_pending",
    "local_integration_review_pending",
    "reference_partially_observed",
}
EXPECTED_REFERENCE_APPROVAL = (
    "UI-SHELL-001, UI-EDITOR-001, UI-EDITOR-002, UI-EDITOR-005, "
    "UI-DB-001, UI-DB-002, UI-DB-003, UI-SEARCH-001, UI-SEARCH-002, "
    "UI-COLLAB-001, "
    "UI-SHARE-001 요약 승인"
)
EXPECTED_REFERENCE_APPROVED_SCENARIOS = [
    "UI-SHELL-001",
    "UI-EDITOR-001",
    "UI-EDITOR-002",
    "UI-EDITOR-005",
    "UI-DB-001",
    "UI-DB-002",
    "UI-DB-003",
    "UI-SEARCH-001",
    "UI-SEARCH-002",
    "UI-COLLAB-001",
    "UI-SHARE-001",
]
EXPECTED_SECOND_BRAIN_APPROVAL = (
    "UI-SB-001, UI-SB-002, UI-SB-003 로컬 전용 통합 증거 정책 승인"
)
EXPECTED_SECOND_BRAIN_SCENARIOS = ["UI-SB-001", "UI-SB-002", "UI-SB-003"]
EXPECTED_EDITOR006_APPROVAL = (
    "UI-EDITOR-006 공식 실패 유도 제외 및 로컬 회복 계약 승인"
)
EXPECTED_EDITOR006_SCENARIOS = ["UI-EDITOR-006"]
EXPECTED_COMBINED_APPROVAL = (
    f"{EXPECTED_REFERENCE_APPROVAL}; {EXPECTED_SECOND_BRAIN_APPROVAL}"
)
EXPECTED_APPROVAL_AUTHORIZATION = {
    "reference_behavior_revalidation_allowed": True,
    "reference_behavior_change_allowed_after_revalidation": True,
    "local_second_brain_evidence_sealing_allowed": True,
    "completion_matrix_mutation_allowed": False,
}
EXPECTED_APPROVAL_SAFETY = {
    "synthetic_data_only": True,
    "personal_notion_content_access_allowed": False,
    "live_second_brain_data_access_allowed": False,
    "live_second_brain_mutation_allowed": False,
    "external_publish_or_message_allowed": False,
}


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _check_exact_fields(
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
    errors.extend(_error("FIELD_MISSING", f"{subject}.{key}", "required field missing") for key in missing)
    errors.extend(_error("UNKNOWN_FIELD", f"{subject}.{key}", "unknown field") for key in extra)
    return not missing and not extra


def _bound_path(
    value: Any,
    subject: str,
    errors: list[dict[str, str]],
    *,
    extra_fields: set[str] | None = None,
) -> Path | None:
    expected = {"path", "sha256"} | (extra_fields or set())
    if not _check_exact_fields(value, expected, subject, errors):
        return None
    path = _safe_repo_path(value.get("path"))
    if path is None:
        errors.append(_error("PATH_INVALID", f"{subject}.path", "safe repository-relative path required"))
        return None
    digest = value.get("sha256")
    if not isinstance(digest, str) or not SHA_PATTERN.fullmatch(digest):
        errors.append(_error("SHA256_INVALID", f"{subject}.sha256", "canonical lowercase sha256 required"))
        return None
    if not path.is_file():
        errors.append(_error("BOUND_FILE_MISSING", f"{subject}.path", "bound file is missing"))
        return None
    if _file_sha256(path) != digest:
        errors.append(_error("BOUND_FILE_HASH_MISMATCH", subject, "bound file hash does not match"))
    return path


def _checkpoint_matches(
    path: Path,
    *,
    scenario_id: str,
    category: str,
    subject: str,
    errors: list[dict[str, str]],
) -> None:
    try:
        checkpoint = load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(_error("CHECKPOINT_READ_FAILED", subject, str(exc)))
        return
    if not isinstance(checkpoint, dict) or checkpoint.get("contract") != CHECKPOINT_CONTRACT:
        errors.append(_error("CHECKPOINT_CONTRACT_INVALID", subject, "checkpoint contract is invalid"))
        return
    scope = checkpoint.get("scope")
    slice_state = checkpoint.get("slice")
    if not isinstance(scope, dict) or not isinstance(slice_state, dict):
        errors.append(_error("CHECKPOINT_SHAPE_INVALID", subject, "scope and slice objects are required"))
        return
    if scope.get("primary_profile") != PROFILE:
        errors.append(_error("CHECKPOINT_PROFILE_INVALID", f"{subject}.scope", "profile mismatch"))
    if category == "local_integration_review_pending":
        scenario_ids = scope.get("scenario_ids")
        if not isinstance(scenario_ids, list) or scenario_id not in scenario_ids:
            errors.append(_error("CHECKPOINT_SCENARIO_MISMATCH", subject, "scenario is not in local checkpoint scope"))
    elif scope.get("scenario_id") != scenario_id:
        errors.append(_error("CHECKPOINT_SCENARIO_MISMATCH", subject, "checkpoint scenario does not match"))
    if slice_state.get("review_required") is not True or slice_state.get("promotion_allowed") is not False:
        errors.append(_error("CHECKPOINT_REVIEW_STATE_INVALID", f"{subject}.slice", "checkpoint must remain review-pending and unpromoted"))
    protected_inputs = checkpoint.get("protected_inputs")
    if protected_inputs is None:
        return
    if not isinstance(protected_inputs, list) or not protected_inputs:
        errors.append(_error("CHECKPOINT_INPUTS_INVALID", f"{subject}.protected_inputs", "non-empty protected input bindings are required"))
        return
    for index, binding in enumerate(protected_inputs):
        binding_subject = f"{subject}.protected_inputs[{index}]"
        extra_fields = {"canonical_sha256"} if isinstance(binding, dict) and "canonical_sha256" in binding else set()
        _bound_path(
            binding,
            binding_subject,
            errors,
            extra_fields=extra_fields,
        )
        if extra_fields:
            canonical_digest = binding.get("canonical_sha256")
            if not isinstance(canonical_digest, str) or not SHA_PATTERN.fullmatch(canonical_digest):
                errors.append(
                    _error(
                        "CANONICAL_SHA256_INVALID",
                        f"{binding_subject}.canonical_sha256",
                        "canonical lowercase sha256 required",
                    )
                )


def _valid_recorded_at(value: Any) -> bool:
    if not isinstance(value, str) or not RFC3339_PATTERN.fullmatch(value):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        return False


def _approval_receipt_matches(
    path: Path,
    *,
    expected_decisions: dict[str, dict[str, Any]],
    errors: list[dict[str, str]],
) -> None:
    subject = "inputs.owner_approval_receipt"
    try:
        receipt = load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(_error("APPROVAL_RECEIPT_READ_FAILED", subject, str(exc)))
        return
    expected_fields = {
        "contract",
        "recorded_at",
        "thread_id",
        "profile",
        "source",
        "decisions",
        "authorization",
        "safety",
    }
    if not _check_exact_fields(receipt, expected_fields, subject, errors):
        return
    if receipt.get("contract") != APPROVAL_RECEIPT_CONTRACT:
        errors.append(_error("APPROVAL_RECEIPT_CONTRACT_INVALID", f"{subject}.contract", "approval receipt contract mismatch"))
    if receipt.get("thread_id") != APPROVAL_THREAD_ID:
        errors.append(_error("APPROVAL_RECEIPT_THREAD_INVALID", f"{subject}.thread_id", "approval thread mismatch"))
    if receipt.get("profile") != PROFILE:
        errors.append(_error("APPROVAL_RECEIPT_PROFILE_INVALID", f"{subject}.profile", "approval profile mismatch"))
    if not _valid_recorded_at(receipt.get("recorded_at")):
        errors.append(_error("APPROVAL_RECEIPT_TIME_INVALID", f"{subject}.recorded_at", "valid RFC 3339 timestamp required"))

    expected_source = {
        "kind": "user_message",
        "normalization_policy": "Collapse display whitespace and rejoin a line-wrapped scenario identifier before exact phrase comparison.",
        "line_wrap_repair": "UI- DB-003 -> UI-DB-003",
        "normalized_approval_text": EXPECTED_COMBINED_APPROVAL,
    }
    source = receipt.get("source")
    if not _check_exact_fields(source, set(expected_source), f"{subject}.source", errors) or source != expected_source:
        errors.append(_error("APPROVAL_RECEIPT_SOURCE_INVALID", f"{subject}.source", "normalized approval source mismatch"))

    decisions = receipt.get("decisions")
    if not _check_exact_fields(decisions, set(expected_decisions), f"{subject}.decisions", errors) or decisions != expected_decisions:
        errors.append(_error("APPROVAL_RECEIPT_DECISIONS_INVALID", f"{subject}.decisions", "approval decisions mismatch"))
    authorization = receipt.get("authorization")
    if not _check_exact_fields(authorization, set(EXPECTED_APPROVAL_AUTHORIZATION), f"{subject}.authorization", errors) or authorization != EXPECTED_APPROVAL_AUTHORIZATION:
        errors.append(_error("APPROVAL_RECEIPT_AUTHORIZATION_INVALID", f"{subject}.authorization", "approval authorization mismatch"))
    safety = receipt.get("safety")
    if not _check_exact_fields(safety, set(EXPECTED_APPROVAL_SAFETY), f"{subject}.safety", errors) or safety != EXPECTED_APPROVAL_SAFETY:
        errors.append(_error("APPROVAL_RECEIPT_SAFETY_INVALID", f"{subject}.safety", "approval safety boundary mismatch"))


def verify_review_gate(gate_path: Path = DEFAULT_GATE) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    try:
        gate = load_json(gate_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": CONTRACT,
            "status": "fail",
            "review_state": "unverified",
            "errors": [_error("GATE_READ_FAILED", "$", str(exc))],
        }, 1

    if not _check_exact_fields(gate, EXPECTED_TOP_FIELDS, "$", errors):
        return {
            "contract": CONTRACT,
            "status": "fail",
            "review_state": "unverified",
            "errors": errors,
        }, 1
    if gate.get("contract") != CONTRACT:
        errors.append(_error("CONTRACT_INVALID", "contract", "review gate contract is invalid"))
    if gate.get("profile") != PROFILE:
        errors.append(_error("PROFILE_INVALID", "profile", "profile is invalid"))
    recorded_at = gate.get("recorded_at")
    if not _valid_recorded_at(recorded_at):
        errors.append(_error("RECORDED_AT_INVALID", "recorded_at", "RFC 3339 timestamp required"))

    inputs = gate.get("inputs")
    plan_path: Path | None = None
    matrix_path: Path | None = None
    approval_receipt_path: Path | None = None
    editor006_policy_receipt_path: Path | None = None
    if _check_exact_fields(
        inputs,
        {
            "scenario_plan",
            "completion_matrix",
            "owner_approval_receipt",
            "editor006_policy_receipt",
        },
        "inputs",
        errors,
    ):
        plan_path = _bound_path(inputs.get("scenario_plan"), "inputs.scenario_plan", errors)
        matrix_path = _bound_path(inputs.get("completion_matrix"), "inputs.completion_matrix", errors)
        approval_receipt_path = _bound_path(
            inputs.get("owner_approval_receipt"),
            "inputs.owner_approval_receipt",
            errors,
        )
        editor006_policy_receipt_path = _bound_path(
            inputs.get("editor006_policy_receipt"),
            "inputs.editor006_policy_receipt",
            errors,
        )
    if plan_path is not None and plan_path.resolve() != DEFAULT_PLAN.resolve():
        errors.append(_error("PLAN_PATH_INVALID", "inputs.scenario_plan.path", "canonical scenario plan required"))
    if matrix_path is not None and matrix_path.resolve() != DEFAULT_MATRIX.resolve():
        errors.append(_error("MATRIX_PATH_INVALID", "inputs.completion_matrix.path", "canonical completion matrix required"))
    if approval_receipt_path is not None and approval_receipt_path.resolve() != DEFAULT_APPROVAL_RECEIPT.resolve():
        errors.append(_error("APPROVAL_RECEIPT_PATH_INVALID", "inputs.owner_approval_receipt.path", "canonical approval receipt required"))
    if (
        editor006_policy_receipt_path is not None
        and editor006_policy_receipt_path.resolve()
        != DEFAULT_EDITOR006_POLICY_RECEIPT.resolve()
    ):
        errors.append(
            _error(
                "APPROVAL_RECEIPT_PATH_INVALID",
                "inputs.editor006_policy_receipt.path",
                "canonical UI-EDITOR-006 policy receipt required",
            )
        )
    if editor006_policy_receipt_path is not None:
        report, exit_code = verify_approval_batch_receipt_v4(
            editor006_policy_receipt_path
        )
        if exit_code != 0:
            errors.append(
                _error(
                    "APPROVAL_RECEIPT_INVALID",
                    "inputs.editor006_policy_receipt",
                    json.dumps(report, ensure_ascii=False),
                )
            )

    plan: dict[str, Any] = {}
    matrix: dict[str, Any] = {}
    if plan_path is not None and matrix_path is not None:
        completion_report, completion_exit = verify_completion_matrix(matrix_path, plan_path)
        if completion_exit != 0 or completion_report.get("status") != "pass":
            errors.append(_error("COMPLETION_MATRIX_INVALID", "inputs.completion_matrix", "completion verifier did not pass"))
        try:
            plan = load_json(plan_path)
            matrix = load_json(matrix_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(_error("INPUT_READ_FAILED", "inputs", str(exc)))

    categories = gate.get("categories")
    category_ids: dict[str, list[str]] = {key: [] for key in EXPECTED_CATEGORY_FIELDS}
    if _check_exact_fields(categories, EXPECTED_CATEGORY_FIELDS, "categories", errors):
        certified = categories.get("certified")
        if not isinstance(certified, list) or not all(isinstance(item, str) for item in certified):
            errors.append(_error("CATEGORY_INVALID", "categories.certified", "string array required"))
        else:
            category_ids["certified"] = certified
        for category in (
            "reference_observed_review_pending",
            "local_integration_review_pending",
            "reference_partially_observed",
        ):
            entries = categories.get(category)
            if not isinstance(entries, list):
                errors.append(_error("CATEGORY_INVALID", f"categories.{category}", "array required"))
                continue
            for index, entry in enumerate(entries):
                subject = f"categories.{category}[{index}]"
                extra = {"scenario_id"}
                if category == "reference_partially_observed":
                    extra.add("remaining_gate")
                path = _bound_path(entry, subject, errors, extra_fields=extra)
                if not isinstance(entry, dict) or not isinstance(entry.get("scenario_id"), str):
                    errors.append(_error("SCENARIO_ID_INVALID", f"{subject}.scenario_id", "scenario id required"))
                    continue
                scenario_id = entry["scenario_id"]
                category_ids[category].append(scenario_id)
                if category == "reference_partially_observed" and not isinstance(entry.get("remaining_gate"), str):
                    errors.append(_error("REMAINING_GATE_INVALID", f"{subject}.remaining_gate", "remaining gate text required"))
                if path is not None:
                    _checkpoint_matches(
                        path,
                        scenario_id=scenario_id,
                        category=category,
                        subject=subject,
                        errors=errors,
                    )

    plan_ids = [item.get("scenario_id") for item in plan.get("required_scenarios", []) if isinstance(item, dict)]
    flattened = [item for key in EXPECTED_CATEGORY_FIELDS for item in category_ids[key]]
    if len(flattened) != len(set(flattened)):
        errors.append(_error("CATEGORY_OVERLAP", "categories", "a scenario appears in more than one category"))
    if set(flattened) != set(plan_ids) or len(flattened) != len(plan_ids):
        errors.append(_error("CATEGORY_COVERAGE_INVALID", "categories", "categories must cover the exact plan denominator"))
    certified_matrix_ids = [
        item.get("scenario_id")
        for item in matrix.get("scenarios", [])
        if isinstance(item, dict)
        and item.get("differential_state")
        in {
            "differential_passed",
            "local_integration_passed",
            "local_recovery_contract_passed",
            "measurement_contract_passed",
        }
    ]
    if category_ids["certified"] != certified_matrix_ids:
        errors.append(_error("CERTIFIED_SET_MISMATCH", "categories.certified", "certified set must equal the completion matrix"))

    decisions = gate.get("review_decisions")
    if _check_exact_fields(
        decisions,
        {
            "reference_observed_summary",
            "local_second_brain_policy",
            "editor006_local_recovery_policy",
        },
        "review_decisions",
        errors,
    ):
        expected_decisions = (
            (
                "reference_observed_summary",
                EXPECTED_REFERENCE_APPROVED_SCENARIOS,
                EXPECTED_REFERENCE_APPROVAL,
            ),
            (
                "local_second_brain_policy",
                EXPECTED_SECOND_BRAIN_SCENARIOS,
                EXPECTED_SECOND_BRAIN_APPROVAL,
            ),
            (
                "editor006_local_recovery_policy",
                EXPECTED_EDITOR006_SCENARIOS,
                EXPECTED_EDITOR006_APPROVAL,
            ),
        )
        canonical_decisions: dict[str, dict[str, Any]] = {}
        for key, scenario_ids, phrase in expected_decisions:
            canonical_decision = {
                "status": "approved",
                "scenario_ids": scenario_ids,
                "approval_phrase": phrase,
            }
            canonical_decisions[key] = canonical_decision
            decision = decisions.get(key)
            subject = f"review_decisions.{key}"
            if _check_exact_fields(decision, {"status", "scenario_ids", "approval_phrase"}, subject, errors):
                if decision.get("status") != "approved":
                    errors.append(_error("APPROVAL_STATUS_INVALID", f"{subject}.status", "recorded owner approval required"))
                if decision.get("scenario_ids") != scenario_ids:
                    errors.append(_error("APPROVAL_SCOPE_MISMATCH", f"{subject}.scenario_ids", "approval scope does not match the category"))
                if decision.get("approval_phrase") != phrase:
                    errors.append(_error("APPROVAL_PHRASE_MISMATCH", f"{subject}.approval_phrase", "exact approval phrase mismatch"))
        if approval_receipt_path is not None:
            _approval_receipt_matches(
                approval_receipt_path,
                expected_decisions={
                    key: canonical_decisions[key]
                    for key in (
                        "reference_observed_summary",
                        "local_second_brain_policy",
                    )
                },
                errors=errors,
            )

    claims = gate.get("claims")
    expected_claims = {
        "all_scenarios_categorized_once": len(flattened) == len(set(flattened)) == len(plan_ids) and set(flattened) == set(plan_ids),
        "certified_count": len(certified_matrix_ids),
        "completion_matrix_changed": len(certified_matrix_ids) > 5,
        "core_ui_complete": False,
        "overall_goal_complete": False,
    }
    if not _check_exact_fields(claims, set(expected_claims), "claims", errors) or claims != expected_claims:
        errors.append(_error("CLAIMS_INVALID", "claims", "claims are not truthful for this gate"))

    counts = {key: len(value) for key, value in category_ids.items()}
    report = {
        "contract": CONTRACT,
        "status": "fail" if errors else "pass",
        "review_state": "unverified" if errors else "owner_approved_revalidation_authorized",
        "profile": gate.get("profile"),
        "counts": counts,
        "errors": errors,
    }
    return report, 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", type=Path, default=DEFAULT_GATE)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_review_gate(args.gate)
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
