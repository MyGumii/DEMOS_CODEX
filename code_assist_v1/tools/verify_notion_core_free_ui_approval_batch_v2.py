"""Verify the second Core Free UI accumulated approval queue."""

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
from code_assist_v1.tools.verify_notion_core_free_ui_manual_ime_receipt import (
    verify_manual_ime_receipt,
)
from code_assist_v1.tools.verify_notion_core_free_ui_performance_budget_proposal import (
    verify_performance_budget_proposal,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUEUE = PACKAGE_ROOT / "docs" / "notion_parity" / "core_free_web_ui" / "approval_batch_queue.v2.json"
CONTRACT = "code_assist.notion.core_free_ui.approval_batch_queue.v2"
THREAD_ID = "019ff607-9116-78a1-9f40-c580c87c0038"
EXACT_BATCH = "UI-EDITOR-004 네이티브 IME 요약 승인; UI-PERF-001 로컬 성능 예산 제안 승인"
EXPECTED_ENTRIES = [
    {
        "entry_id": "UI-EDITOR-004-NATIVE-IME-SUMMARY-REVIEW",
        "kind": "human_evidence_acceptance",
        "status": "pending_user_approval",
        "scenario_ids": ["UI-EDITOR-004"],
        "required_user_text": "UI-EDITOR-004 네이티브 IME 요약 승인",
        "external_state_change": False,
        "may_be_batched": True,
        "authorization_effect": "allow_redacted_observable_differential_recomputation",
    },
    {
        "entry_id": "UI-PERF-001-LOCAL-BUDGET-REVIEW",
        "kind": "human_policy_acceptance",
        "status": "pending_user_approval",
        "scenario_ids": ["UI-PERF-001"],
        "required_user_text": "UI-PERF-001 로컬 성능 예산 제안 승인",
        "external_state_change": False,
        "may_be_batched": True,
        "authorization_effect": "allow_local_precalibration_regression_budget",
    },
]


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _fields(value: Any, expected: set[str], subject: str, errors: list[dict[str, str]]) -> bool:
    if not isinstance(value, dict):
        errors.append(_error("OBJECT_REQUIRED", subject, "object required"))
        return False
    missing, extra = sorted(expected - set(value)), sorted(set(value) - expected)
    errors.extend(_error("FIELD_MISSING", f"{subject}.{key}", "required field missing") for key in missing)
    errors.extend(_error("UNKNOWN_FIELD", f"{subject}.{key}", "unknown field") for key in extra)
    return not missing and not extra


def _binding(value: Any, subject: str, errors: list[dict[str, str]]) -> Path | None:
    if not _fields(value, {"path", "sha256"}, subject, errors):
        return None
    path, digest = _safe_repo_path(value.get("path")), value.get("sha256")
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


def verify_approval_batch_v2(path: Path = DEFAULT_QUEUE) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    try:
        value = load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {"contract": CONTRACT, "status": "fail", "errors": [_error("QUEUE_READ_FAILED", "$", str(exc))]}, 1
    top = {"contract", "recorded_at", "thread_id", "profile", "prior_batch", "manual_input", "policy", "entries", "batch_execution", "claims"}
    if not _fields(value, top, "$", errors):
        return {"contract": CONTRACT, "status": "fail", "errors": errors}, 1
    for key, expected in {"contract": CONTRACT, "thread_id": THREAD_ID, "profile": PROFILE}.items():
        if value.get(key) != expected:
            errors.append(_error("VALUE_INVALID", key, f"expected {expected}"))
    recorded_at = value.get("recorded_at")
    try:
        valid_time = isinstance(recorded_at, str) and RFC3339_PATTERN.fullmatch(recorded_at) and datetime.fromisoformat(recorded_at.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        valid_time = False
    if not valid_time:
        errors.append(_error("RECORDED_AT_INVALID", "recorded_at", "RFC 3339 timestamp required"))

    prior = value.get("prior_batch")
    if _fields(prior, {"queue", "approval_receipt", "resolved_entry_count"}, "prior_batch", errors):
        _binding(prior.get("queue"), "prior_batch.queue", errors)
        _binding(prior.get("approval_receipt"), "prior_batch.approval_receipt", errors)
        if prior.get("resolved_entry_count") != 3:
            errors.append(_error("PRIOR_COUNT_INVALID", "prior_batch.resolved_entry_count", "expected three resolved entries"))

    manual = value.get("manual_input")
    manual_path = None
    if _fields(manual, {"receipt", "entry_id", "status"}, "manual_input", errors):
        manual_path = _binding(manual.get("receipt"), "manual_input.receipt", errors)
        if manual.get("entry_id") != "UI-EDITOR-004-NATIVE-IME-INPUT" or manual.get("status") != "resolved":
            errors.append(_error("MANUAL_INPUT_INVALID", "manual_input", "resolved exact IME entry required"))
    if manual_path is not None:
        report, exit_code = verify_manual_ime_receipt(manual_path)
        if exit_code != 0:
            errors.append(_error("MANUAL_RECEIPT_INVALID", "manual_input.receipt", json.dumps(report, ensure_ascii=False)))

    expected_policy = {"mode": "accumulate_then_process_serially", "approval_is_never_inferred": True, "external_action_authorized": False, "personal_workspace_access_allowed": False, "live_local_data_access_allowed": False, "completion_matrix_mutation_allowed_by_receipt_alone": False}
    policy = value.get("policy")
    if not _fields(policy, set(expected_policy), "policy", errors) or policy != expected_policy:
        errors.append(_error("POLICY_INVALID", "policy", "approval policy mismatch"))

    entries = value.get("entries")
    if not isinstance(entries, list) or len(entries) != 2:
        errors.append(_error("ENTRIES_INVALID", "entries", "exactly two pending entries required"))
    else:
        for index, (entry, expected) in enumerate(zip(entries, EXPECTED_ENTRIES)):
            subject = f"entries[{index}]"
            fields = set(expected) | {"evidence"}
            if not _fields(entry, fields, subject, errors):
                continue
            actual = {key: entry.get(key) for key in expected}
            if actual != expected:
                errors.append(_error("ENTRY_INVALID", subject, "pending entry mismatch"))
            evidence_path = _binding(entry.get("evidence"), f"{subject}.evidence", errors)
            if index == 1 and evidence_path is not None:
                checkpoint = load_json(evidence_path)
                proposal_path = _safe_repo_path(checkpoint.get("proposal", {}).get("path")) if isinstance(checkpoint, dict) else None
                if proposal_path is None:
                    errors.append(_error("PROPOSAL_PATH_INVALID", subject, "proposal binding missing"))
                else:
                    report, exit_code = verify_performance_budget_proposal(proposal_path)
                    if exit_code != 0:
                        errors.append(_error("PROPOSAL_INVALID", subject, json.dumps(report, ensure_ascii=False)))

    expected_execution = {"status": "waiting_for_exact_user_text", "exact_user_text": EXACT_BATCH, "entry_ids": [item["entry_id"] for item in EXPECTED_ENTRIES], "processing_order": ["seal_native_ime_summary_receipt", "seal_local_performance_budget_decision", "recompute_only_authorized_local_and_observable_differential_claims", "rerun_manual_ime_performance_review_and_completion_verifiers"]}
    execution = value.get("batch_execution")
    if not _fields(execution, set(expected_execution), "batch_execution", errors) or execution != expected_execution:
        errors.append(_error("BATCH_EXECUTION_INVALID", "batch_execution", "exact accumulated batch required"))
    expected_claims = {"pending_entry_count": 2, "pending_human_evidence_approval_count": 1, "pending_human_policy_approval_count": 1, "external_action_pending_count": 0, "approval_inferred_count": 0, "manual_input_pending_count": 0, "core_ui_complete": False, "overall_goal_complete": False}
    claims = value.get("claims")
    if not _fields(claims, set(expected_claims), "claims", errors) or claims != expected_claims:
        errors.append(_error("CLAIMS_INVALID", "claims", "queue claims mismatch"))
    report = {"contract": CONTRACT, "status": "fail" if errors else "pass", "pending_entry_count": 2, "external_action_pending_count": 0, "exact_user_text": EXACT_BATCH, "errors": errors}
    return report, 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_approval_batch_v2(args.queue)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2, separators=(",", ":") if args.compact else None))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
