"""Verify the corrected UI-EDITOR-006 approval-reseal queue."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from code_assist_v1.tools.notion_core_free_ui_approval_batch_v2 import (
    verify_approval_batch_receipt_v2,
)
from code_assist_v1.tools.verify_notion_core_free_ui_approval_batch_v2 import (
    verify_approval_batch_v2,
)
from code_assist_v1.tools.verify_notion_core_free_ui_completion import (
    PROFILE,
    RFC3339_PATTERN,
    load_json,
)
from code_assist_v1.tools.verify_notion_core_free_ui_editor006_policy_proposal import (
    EXACT_APPROVAL_TEXT,
    _binding,
    _error,
    _fields,
)
from code_assist_v1.tools.verify_notion_core_free_ui_editor006_policy_proposal_v2 import (
    verify_editor006_policy_proposal_v2,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUEUE = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "approval_batch_queue.v4.json"
)
CONTRACT = "code_assist.notion.core_free_ui.approval_batch_queue.v4"
THREAD_ID = "019ff607-9116-78a1-9f40-c580c87c0038"
EXPECTED_ENTRY = {
    "entry_id": "UI-EDITOR-006-LOCAL-RECOVERY-EVIDENCE-POLICY",
    "kind": "human_policy_acceptance",
    "status": "owner_approved_pending_corrected_receipt",
    "scenario_ids": ["UI-EDITOR-006"],
    "required_user_text": EXACT_APPROVAL_TEXT,
    "external_state_change": False,
    "authorization_effect": "allow_ui_editor_006_local_failure_recovery_evidence_satisfaction",
}


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


def _verify_bound_contract(
    path: Path | None,
    expected_contract: str,
    subject: str,
    errors: list[dict[str, str]],
) -> None:
    if path is None:
        return
    try:
        value = load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(_error("BOUND_READ_FAILED", subject, str(exc)))
        return
    if not isinstance(value, dict) or value.get("contract") != expected_contract:
        errors.append(_error("BOUND_CONTRACT_INVALID", subject, "bound contract mismatch"))


def verify_approval_batch_v4(
    path: Path = DEFAULT_QUEUE,
) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    try:
        value = load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": CONTRACT,
            "status": "fail",
            "errors": [_error("QUEUE_READ_FAILED", "$", str(exc))],
        }, 1

    top = {
        "contract",
        "recorded_at",
        "thread_id",
        "profile",
        "prior_batch",
        "supersedes",
        "policy",
        "entries",
        "batch_execution",
        "claims",
    }
    if not _fields(value, top, "$", errors):
        return {"contract": CONTRACT, "status": "fail", "errors": errors}, 1
    for key, expected in {
        "contract": CONTRACT,
        "thread_id": THREAD_ID,
        "profile": PROFILE,
    }.items():
        if value.get(key) != expected:
            errors.append(_error("VALUE_INVALID", key, f"expected {expected}"))
    if not _valid_time(value.get("recorded_at")):
        errors.append(_error("RECORDED_AT_INVALID", "recorded_at", "RFC 3339 timestamp required"))

    prior = value.get("prior_batch")
    prior_queue: Path | None = None
    prior_receipt: Path | None = None
    if _fields(prior, {"queue", "approval_receipt", "resolved_entry_count"}, "prior_batch", errors):
        prior_queue = _binding(prior.get("queue"), "prior_batch.queue", errors)
        prior_receipt = _binding(prior.get("approval_receipt"), "prior_batch.approval_receipt", errors)
        if prior.get("resolved_entry_count") != 2:
            errors.append(_error("PRIOR_COUNT_INVALID", "prior_batch.resolved_entry_count", "expected two resolved entries"))
    if prior_queue is not None:
        report, exit_code = verify_approval_batch_v2(prior_queue)
        if exit_code != 0:
            errors.append(_error("PRIOR_QUEUE_INVALID", "prior_batch.queue", json.dumps(report, ensure_ascii=False)))
    if prior_queue is not None and prior_receipt is not None:
        report, exit_code = verify_approval_batch_receipt_v2(prior_receipt, queue_path=prior_queue)
        if exit_code != 0:
            errors.append(_error("PRIOR_RECEIPT_INVALID", "prior_batch.approval_receipt", json.dumps(report, ensure_ascii=False)))

    supersedes = value.get("supersedes")
    superseded_paths: dict[str, Path] = {}
    expected_supersedes = {
        "queue",
        "approval_receipt",
        "reason_code",
        "approval_scope_changed",
        "authorization_effect_changed",
    }
    if _fields(supersedes, expected_supersedes, "supersedes", errors):
        for key in ("queue", "approval_receipt"):
            candidate = _binding(supersedes.get(key), f"supersedes.{key}", errors)
            if candidate is not None:
                superseded_paths[key] = candidate
        if supersedes.get("reason_code") != "mutable_predecision_matrix_binding":
            errors.append(_error("SUPERSESSION_REASON_INVALID", "supersedes.reason_code", "exact supersession reason required"))
        if supersedes.get("approval_scope_changed") is not False:
            errors.append(_error("APPROVAL_SCOPE_CHANGED", "supersedes.approval_scope_changed", "approval scope must remain unchanged"))
        if supersedes.get("authorization_effect_changed") is not False:
            errors.append(_error("AUTHORIZATION_EFFECT_CHANGED", "supersedes.authorization_effect_changed", "authorization effect must remain unchanged"))
    _verify_bound_contract(
        superseded_paths.get("queue"),
        "code_assist.notion.core_free_ui.approval_batch_queue.v3",
        "supersedes.queue",
        errors,
    )
    _verify_bound_contract(
        superseded_paths.get("approval_receipt"),
        "code_assist.notion.core_free_ui.approval_batch_receipt.v3",
        "supersedes.approval_receipt",
        errors,
    )

    expected_policy = {
        "mode": "rebind_existing_exact_approval_to_immutable_checkpoint",
        "approval_is_never_inferred": True,
        "new_user_approval_required": False,
        "external_action_authorized": False,
        "personal_workspace_access_allowed": False,
        "live_local_data_access_allowed": False,
        "completion_matrix_mutation_allowed_by_receipt_alone": False,
    }
    policy = value.get("policy")
    if not _fields(policy, set(expected_policy), "policy", errors) or policy != expected_policy:
        errors.append(_error("POLICY_INVALID", "policy", "approval-reseal policy mismatch"))

    entries = value.get("entries")
    if not isinstance(entries, list) or len(entries) != 1:
        errors.append(_error("ENTRIES_INVALID", "entries", "exactly one corrected approved entry required"))
    else:
        entry = entries[0]
        if _fields(entry, set(EXPECTED_ENTRY) | {"evidence"}, "entries[0]", errors):
            actual = {key: entry.get(key) for key in EXPECTED_ENTRY}
            if actual != EXPECTED_ENTRY:
                errors.append(_error("ENTRY_INVALID", "entries[0]", "corrected entry mismatch"))
            evidence_path = _binding(entry.get("evidence"), "entries[0].evidence", errors)
            if evidence_path is not None:
                report, exit_code = verify_editor006_policy_proposal_v2(evidence_path)
                if exit_code != 0:
                    errors.append(_error("PROPOSAL_INVALID", "entries[0].evidence", json.dumps(report, ensure_ascii=False)))

    expected_execution = {
        "status": "ready_to_reseal_from_exact_user_text",
        "exact_user_text": EXACT_APPROVAL_TEXT,
        "entry_ids": [EXPECTED_ENTRY["entry_id"]],
        "processing_order": [
            "verify_immutable_approval_pending_checkpoint",
            "seal_corrected_editor006_policy_receipt",
            "revalidate_exact_local_recovery_evidence",
            "apply_only_the_authorized_local_failure_recovery_evidence_mode",
            "rerun_completion_review_and_editor006_browser_verifiers",
        ],
    }
    execution = value.get("batch_execution")
    if (
        not _fields(execution, set(expected_execution), "batch_execution", errors)
        or execution != expected_execution
    ):
        errors.append(_error("BATCH_EXECUTION_INVALID", "batch_execution", "exact corrected batch required"))

    expected_claims = {
        "approved_entry_count": 1,
        "corrected_receipt_pending_count": 1,
        "new_user_approval_pending_count": 0,
        "external_action_pending_count": 0,
        "approval_inferred_count": 0,
        "core_ui_complete": False,
        "overall_goal_complete": False,
    }
    claims = value.get("claims")
    if not _fields(claims, set(expected_claims), "claims", errors) or claims != expected_claims:
        errors.append(_error("CLAIMS_INVALID", "claims", "corrected queue claims mismatch"))

    report = {
        "contract": CONTRACT,
        "status": "fail" if errors else "pass",
        "approved_entry_count": 1,
        "new_user_approval_pending_count": 0,
        "external_action_pending_count": 0,
        "exact_user_text": EXACT_APPROVAL_TEXT,
        "approval_scope_changed": False,
        "authorization_effect_changed": False,
        "errors": errors,
    }
    return report, 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_approval_batch_v4(args.queue)
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
