"""Verify the non-promoting Core Free UI approval batching queue."""

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
DEFAULT_QUEUE = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "approval_batch_queue.v1.json"
)
CONTRACT = "code_assist.notion.core_free_ui.approval_batch_queue.v1"
THREAD_ID = "019ff607-9116-78a1-9f40-c580c87c0038"
EXPECTED_POLICY = {
    "mode": "accumulate_then_process_serially",
    "independent_work_continues": True,
    "approval_is_never_inferred": True,
    "action_time_confirmation_required_for_destructive_external_actions": True,
    "manual_evidence_input_is_not_approval": True,
    "personal_workspace_access_allowed": False,
    "live_local_data_access_allowed": False,
    "completion_matrix_mutation_allowed": False,
}
EXPECTED_ENTRIES = {
    "UI-A11Y-001-UI-EDITOR-003-SUMMARY-REVIEW": {
        "kind": "human_evidence_acceptance",
        "status": "pending_user_approval",
        "scenario_ids": ["UI-A11Y-001", "UI-EDITOR-003"],
        "required_user_text": "UI-A11Y-001, UI-EDITOR-003 요약 승인",
        "external_state_change": False,
        "may_be_batched": True,
    },
    "UI-EDITOR-004-NATIVE-IME-INPUT": {
        "kind": "manual_evidence_input",
        "status": "pending_user_input",
        "scenario_ids": ["UI-EDITOR-004"],
        "required_user_text": "한글 조합 테스트 입력 후 UI-EDITOR-004 IME 입력 완료",
        "external_state_change": False,
        "may_be_batched": False,
    },
    "UI-RESP-001-MULTIVIEWPORT-SUMMARY-REVIEW": {
        "kind": "human_evidence_acceptance",
        "status": "pending_user_approval",
        "scenario_ids": ["UI-RESP-001"],
        "required_user_text": "UI-RESP-001 다중 뷰포트 요약 승인",
        "external_state_change": False,
        "may_be_batched": True,
    },
    "UI-PERF-001-REFERENCE-CREATE-TRASH": {
        "kind": "destructive_external_action",
        "status": "pending_action_time_confirmation",
        "scenario_ids": ["UI-PERF-001"],
        "required_user_text": "UI-PERF-001-REFERENCE-CREATE 휴지통 이동 승인",
        "external_state_change": True,
        "may_be_batched": True,
    },
}
TOP_FIELDS = {
    "contract",
    "recorded_at",
    "thread_id",
    "profile",
    "policy",
    "entries",
    "batch_execution",
    "independent_lane",
    "claims",
}

EXPECTED_BATCH_EXECUTION = {
    "status": "waiting_for_exact_user_text",
    "exact_user_text": "UI-A11Y-001, UI-EDITOR-003 요약 승인; UI-RESP-001 다중 뷰포트 요약 승인; UI-PERF-001-REFERENCE-CREATE 휴지통 이동 승인",
    "entry_ids": [
        "UI-A11Y-001-UI-EDITOR-003-SUMMARY-REVIEW",
        "UI-RESP-001-MULTIVIEWPORT-SUMMARY-REVIEW",
        "UI-PERF-001-REFERENCE-CREATE-TRASH",
    ],
    "processing_order": [
        "seal_non_mutating_summary_receipts",
        "reclaim_and_revalidate_exact_synthetic_page",
        "move_only_the_exact_synthetic_page_to_trash",
        "verify_reference_tree_restoration",
        "rerun_all_three_machine_verifiers",
    ],
    "manual_input_excluded": "UI-EDITOR-004-NATIVE-IME-INPUT",
}
ENTRY_FIELDS = {
    "entry_id",
    "kind",
    "status",
    "scenario_ids",
    "required_user_text",
    "external_state_change",
    "may_be_batched",
    "blocks",
    "does_not_block",
    "evidence",
}


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _exact_fields(
    value: Any,
    expected: set[str],
    subject: str,
    errors: list[dict[str, str]],
) -> bool:
    if not isinstance(value, dict):
        errors.append(_error("OBJECT_REQUIRED", subject, "object required"))
        return False
    for key in sorted(expected - set(value)):
        errors.append(_error("FIELD_MISSING", f"{subject}.{key}", "required field missing"))
    for key in sorted(set(value) - expected):
        errors.append(_error("UNKNOWN_FIELD", f"{subject}.{key}", "unknown field"))
    return set(value) == expected


def _valid_recorded_at(value: Any) -> bool:
    if not isinstance(value, str) or not RFC3339_PATTERN.fullmatch(value):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        return False


def verify_approval_batch_queue(
    queue_path: Path = DEFAULT_QUEUE,
) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    try:
        queue = load_json(queue_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "contract": CONTRACT,
            "status": "fail",
            "profile": None,
            "counts": {},
            "independent_lane": "unverified",
            "errors": [_error("QUEUE_READ_FAILED", "queue", str(exc))],
        }
        return report, 1

    if not _exact_fields(queue, TOP_FIELDS, "queue", errors):
        pass
    if queue.get("contract") != CONTRACT:
        errors.append(_error("CONTRACT_INVALID", "contract", "approval queue contract mismatch"))
    if queue.get("thread_id") != THREAD_ID:
        errors.append(_error("THREAD_INVALID", "thread_id", "goal thread mismatch"))
    if queue.get("profile") != PROFILE:
        errors.append(_error("PROFILE_INVALID", "profile", "profile mismatch"))
    if not _valid_recorded_at(queue.get("recorded_at")):
        errors.append(_error("RECORDED_AT_INVALID", "recorded_at", "RFC 3339 timestamp with offset required"))
    if queue.get("policy") != EXPECTED_POLICY:
        errors.append(_error("POLICY_INVALID", "policy", "safe batching policy mismatch"))

    entries = queue.get("entries")
    observed: dict[str, dict[str, Any]] = {}
    if not isinstance(entries, list):
        errors.append(_error("ENTRIES_INVALID", "entries", "entry list required"))
        entries = []
    for index, entry in enumerate(entries):
        subject = f"entries[{index}]"
        if not _exact_fields(entry, ENTRY_FIELDS, subject, errors):
            continue
        entry_id = entry.get("entry_id")
        if not isinstance(entry_id, str) or entry_id in observed:
            errors.append(_error("ENTRY_ID_INVALID", f"{subject}.entry_id", "unique known entry id required"))
            continue
        observed[entry_id] = entry
        expected = EXPECTED_ENTRIES.get(entry_id)
        if expected is None:
            errors.append(_error("ENTRY_UNKNOWN", subject, "unknown active approval entry"))
        else:
            for key, value in expected.items():
                if entry.get(key) != value:
                    errors.append(_error("ENTRY_STATE_INVALID", f"{subject}.{key}", "entry state mismatch"))
        if not isinstance(entry.get("blocks"), list) or not entry.get("blocks"):
            errors.append(_error("BLOCKS_INVALID", f"{subject}.blocks", "non-empty blocked result list required"))
        if not isinstance(entry.get("does_not_block"), list) or not entry.get("does_not_block"):
            errors.append(_error("PARALLEL_WORK_INVALID", f"{subject}.does_not_block", "non-empty independent work list required"))
        evidence = entry.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(_error("EVIDENCE_INVALID", f"{subject}.evidence", "non-empty evidence list required"))
            continue
        for evidence_index, binding in enumerate(evidence):
            binding_subject = f"{subject}.evidence[{evidence_index}]"
            if not _exact_fields(binding, {"path", "sha256"}, binding_subject, errors):
                continue
            path = _safe_repo_path(binding.get("path"))
            digest = binding.get("sha256")
            if path is None or not path.is_file():
                errors.append(_error("EVIDENCE_PATH_INVALID", binding_subject, "existing safe repository path required"))
            elif not isinstance(digest, str) or not SHA_PATTERN.fullmatch(digest):
                errors.append(_error("EVIDENCE_SHA_INVALID", binding_subject, "canonical sha256 required"))
            elif _file_sha256(path) != digest:
                errors.append(_error("EVIDENCE_HASH_MISMATCH", binding_subject, "bound evidence hash mismatch"))

    if set(observed) != set(EXPECTED_ENTRIES):
        errors.append(_error("ENTRY_SET_INVALID", "entries", "active approval entry set mismatch"))

    if queue.get("batch_execution") != EXPECTED_BATCH_EXECUTION:
        errors.append(_error("BATCH_EXECUTION_INVALID", "batch_execution", "exact safe batch command mismatch"))

    expected_lane = {
        "status": "ready_for_local_verification_only",
        "ordered_work": ["local-verification"],
        "next_exact_work": "Run the serial approval-queue, review-gate, completion, and UI-RESP-001 local regression checks. Do not induce an official Notion network failure or stale-revision conflict without a separately reviewed method.",
        "forbidden_without_new_authorization": [
            "personal-workspace-access",
            "external-publish-or-message",
            "invite-or-share-membership-change",
            "purchase-or-plan-upgrade",
            "production-migration-or-feature-enable",
        ],
    }
    if queue.get("independent_lane") != expected_lane:
        errors.append(_error("INDEPENDENT_LANE_INVALID", "independent_lane", "independent lane mismatch"))

    expected_claims = {
        "pending_entry_count": 4,
        "pending_human_approval_count": 2,
        "pending_manual_input_count": 1,
        "pending_destructive_external_action_count": 1,
        "approval_inferred_count": 0,
        "external_action_executed_count": 0,
        "independent_work_blocked": False,
        "core_ui_complete": False,
        "overall_goal_complete": False,
    }
    if queue.get("claims") != expected_claims:
        errors.append(_error("CLAIMS_INVALID", "claims", "queue claims mismatch"))

    counts = {
        "pending": len(entries),
        "human_approval": sum(item.get("kind") == "human_evidence_acceptance" for item in entries if isinstance(item, dict)),
        "manual_input": sum(item.get("kind") == "manual_evidence_input" for item in entries if isinstance(item, dict)),
        "destructive_external_action": sum(item.get("kind") == "destructive_external_action" for item in entries if isinstance(item, dict)),
    }
    report = {
        "contract": CONTRACT,
        "status": "fail" if errors else "pass",
        "profile": queue.get("profile"),
        "counts": counts,
        "independent_lane": "unverified" if errors else "ready_for_local_verification_only",
        "errors": errors,
    }
    return report, 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_approval_batch_queue(args.queue)
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
