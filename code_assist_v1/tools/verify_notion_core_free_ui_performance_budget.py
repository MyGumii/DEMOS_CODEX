"""Verify and load the approved local UI-PERF-001 regression budget."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from code_assist_v1.tools.notion_core_free_ui_approval_batch_v2 import (
    verify_approval_batch_receipt_v2,
)
from code_assist_v1.tools.verify_notion_core_free_ui_completion import (
    PROFILE,
    RFC3339_PATTERN,
    SHA_PATTERN,
    _file_sha256,
    _safe_repo_path,
    load_json,
)
from code_assist_v1.tools.verify_notion_core_free_ui_performance_budget_proposal import (
    verify_performance_budget_proposal,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUDGET = PACKAGE_ROOT / "docs" / "notion_parity" / "core_free_web_ui" / "performance_budget.v1.json"
CONTRACT = "code_assist.notion.core_free_ui.performance_budget.v1"
METRIC_MAP = {
    "login_shell_ready": "shell_ready_ms",
    "existing_page_ready": "page_ready_ms",
    "editor_input_ack": "editor_input_ack_ms",
    "save_ready": "save_ready_ms",
    "reload_restored_ready": "reload_ready_ms",
    "search_overlay_ready": "search_open_ms",
    "search_result_ready": "search_result_ms",
    "second_brain_preview_ready": "second_brain_preview_ready_ms",
}


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


def verify_performance_budget(path: Path = DEFAULT_BUDGET) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    try:
        value = load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {"contract": CONTRACT, "status": "fail", "errors": [_error("BUDGET_READ_FAILED", "$", str(exc))]}, 1
    top = {"contract", "activated_at", "scenario_id", "profile", "state", "proposal", "approval_receipt", "approval_text", "enforcement", "claim_boundary", "safety"}
    if not _fields(value, top, "$", errors):
        return {"contract": CONTRACT, "status": "fail", "errors": errors}, 1
    for key, expected in {"contract": CONTRACT, "scenario_id": "UI-PERF-001", "profile": PROFILE, "state": "active_local_precalibration", "approval_text": "UI-PERF-001 로컬 성능 예산 제안 승인"}.items():
        if value.get(key) != expected:
            errors.append(_error("VALUE_INVALID", key, f"expected {expected}"))
    activated_at = value.get("activated_at")
    try:
        valid_time = isinstance(activated_at, str) and RFC3339_PATTERN.fullmatch(activated_at) and datetime.fromisoformat(activated_at.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        valid_time = False
    if not valid_time:
        errors.append(_error("ACTIVATED_AT_INVALID", "activated_at", "RFC 3339 timestamp required"))
    proposal_path = _binding(value.get("proposal"), "proposal", errors)
    receipt_path = _binding(value.get("approval_receipt"), "approval_receipt", errors)
    proposal: dict[str, Any] = {}
    if proposal_path is not None:
        report, exit_code = verify_performance_budget_proposal(proposal_path)
        if exit_code != 0:
            errors.append(_error("PROPOSAL_INVALID", "proposal", json.dumps(report, ensure_ascii=False)))
        else:
            proposal = load_json(proposal_path)
    if receipt_path is not None:
        report, exit_code = verify_approval_batch_receipt_v2(receipt_path)
        if exit_code != 0:
            errors.append(_error("APPROVAL_RECEIPT_INVALID", "approval_receipt", json.dumps(report, ensure_ascii=False)))
        else:
            receipt = load_json(receipt_path)
            matching = [item for item in receipt.get("resolved_entries", []) if isinstance(item, dict) and item.get("entry_id") == "UI-PERF-001-LOCAL-BUDGET-REVIEW"]
            if len(matching) != 1 or matching[0].get("authorization_effect") != "allow_local_precalibration_regression_budget":
                errors.append(_error("APPROVAL_SCOPE_INVALID", "approval_receipt", "exact budget approval required"))
    enforcement = value.get("enforcement")
    expected_keys = {"percentile", "sample_count_per_metric", "metric_budgets_ms", "measurement_only_unbudgeted_metrics", "failure_effect"}
    if _fields(enforcement, expected_keys, "enforcement", errors):
        if enforcement.get("percentile") != "p95" or enforcement.get("sample_count_per_metric") != 6 or enforcement.get("measurement_only_unbudgeted_metrics") != ["create_page_ready_ms"] or enforcement.get("failure_effect") != "fail_isolated_local_regression_test_only":
            errors.append(_error("ENFORCEMENT_INVALID", "enforcement", "budget enforcement contract mismatch"))
        expected_budgets = {METRIC_MAP[item["metric_id"]]: item["proposed_budget_ms"] for item in proposal.get("metrics", []) if isinstance(item, dict) and item.get("metric_id") in METRIC_MAP}
        if enforcement.get("metric_budgets_ms") != expected_budgets:
            errors.append(_error("BUDGETS_INVALID", "enforcement.metric_budgets_ms", "active budgets must exactly match approved proposal"))
    expected_boundary = {"notion_reference_timing_claimed": False, "production_slo_claimed": False, "h8_complete": False, "release_promotion_allowed": False, "completion_matrix_mutated": False}
    boundary = value.get("claim_boundary")
    if not _fields(boundary, set(expected_boundary), "claim_boundary", errors) or boundary != expected_boundary:
        errors.append(_error("CLAIM_BOUNDARY_INVALID", "claim_boundary", "claim boundary mismatch"))
    expected_safety = {"synthetic_only": True, "live_local_data_accessed": False, "personal_notion_content_accessed": False, "external_action_executed": False}
    safety = value.get("safety")
    if not _fields(safety, set(expected_safety), "safety", errors) or safety != expected_safety:
        errors.append(_error("SAFETY_INVALID", "safety", "safety boundary mismatch"))
    budgets = enforcement.get("metric_budgets_ms", {}) if isinstance(enforcement, dict) else {}
    report = {"contract": CONTRACT, "status": "fail" if errors else "pass", "state": value.get("state"), "budgeted_metric_count": len(budgets) if isinstance(budgets, dict) else 0, "unbudgeted_metric_count": 1, "errors": errors}
    return report, 1 if errors else 0


def load_active_performance_budget(path: Path = DEFAULT_BUDGET) -> dict[str, int]:
    report, exit_code = verify_performance_budget(path)
    if exit_code != 0:
        raise AssertionError(f"performance budget verification failed: {report['errors']}")
    value = load_json(path)
    return dict(value["enforcement"]["metric_budgets_ms"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget", type=Path, default=DEFAULT_BUDGET)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_performance_budget(args.budget)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2, separators=(",", ":") if args.compact else None))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
