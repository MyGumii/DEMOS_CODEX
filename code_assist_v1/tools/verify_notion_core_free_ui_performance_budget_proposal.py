"""Verify the unapproved UI-PERF-001 local regression budget proposal."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
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
DEFAULT_PROPOSAL = PACKAGE_ROOT / "docs" / "notion_parity" / "core_free_web_ui" / "performance_budget_proposal.v1.json"
CONTRACT = "code_assist.notion.core_free_ui.performance_budget_proposal.v1"
EXPECTED_METRICS = {
    "login_shell_ready": 779.891,
    "existing_page_ready": 179.463,
    "editor_input_ack": 70.257,
    "save_ready": 1401.620,
    "reload_restored_ready": 489.261,
    "search_overlay_ready": 94.642,
    "search_result_ready": 66.431,
    "second_brain_preview_ready": 932.077,
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


def _binding(value: Any, subject: str, errors: list[dict[str, str]]) -> None:
    if not _fields(value, {"path", "sha256"}, subject, errors):
        return
    path, digest = _safe_repo_path(value.get("path")), value.get("sha256")
    if path is None:
        errors.append(_error("PATH_INVALID", f"{subject}.path", "safe repository path required"))
    elif not path.is_file():
        errors.append(_error("BOUND_FILE_MISSING", f"{subject}.path", "bound file missing"))
    elif not isinstance(digest, str) or not SHA_PATTERN.fullmatch(digest):
        errors.append(_error("SHA256_INVALID", f"{subject}.sha256", "canonical sha256 required"))
    elif _file_sha256(path) != digest:
        errors.append(_error("BOUND_FILE_HASH_MISMATCH", subject, "bound file hash mismatch"))


def verify_performance_budget_proposal(path: Path = DEFAULT_PROPOSAL) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    try:
        value = load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {"contract": CONTRACT, "status": "fail", "errors": [_error("PROPOSAL_READ_FAILED", "$", str(exc))]}, 1
    top = {"contract", "recorded_at", "scenario_id", "profile", "status", "inputs", "method", "metrics", "owner_decision", "safety", "claims"}
    if not _fields(value, top, "$", errors):
        return {"contract": CONTRACT, "status": "fail", "errors": errors}, 1
    expected_scalars = {"contract": CONTRACT, "scenario_id": "UI-PERF-001", "profile": PROFILE, "status": "pending_owner_approval"}
    for key, expected in expected_scalars.items():
        if value.get(key) != expected:
            errors.append(_error("VALUE_INVALID", key, f"expected {expected}"))
    recorded_at = value.get("recorded_at")
    try:
        valid_time = isinstance(recorded_at, str) and RFC3339_PATTERN.fullmatch(recorded_at) and datetime.fromisoformat(recorded_at.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        valid_time = False
    if not valid_time:
        errors.append(_error("RECORDED_AT_INVALID", "recorded_at", "RFC 3339 timestamp required"))
    inputs = value.get("inputs")
    if _fields(inputs, {"local_measurement", "reference_cleanup"}, "inputs", errors):
        _binding(inputs.get("local_measurement"), "inputs.local_measurement", errors)
        _binding(inputs.get("reference_cleanup"), "inputs.reference_cleanup", errors)
    expected_method = {"sample_count_per_metric": 6, "observed_percentile": "p95_nearest_rank", "headroom_multiplier": 1.75, "rounding": "ceil_to_next_10_ms", "formula": "ceil(observed_p95_ms * 1.75 / 10) * 10", "scope": "isolated_local_regression_only"}
    method = value.get("method")
    if not _fields(method, set(expected_method), "method", errors) or method != expected_method:
        errors.append(_error("METHOD_INVALID", "method", "frozen proposal method mismatch"))
    metrics = value.get("metrics")
    seen: set[str] = set()
    if not isinstance(metrics, list) or len(metrics) != len(EXPECTED_METRICS):
        errors.append(_error("METRICS_INVALID", "metrics", "exact metric set required"))
    else:
        for index, item in enumerate(metrics):
            subject = f"metrics[{index}]"
            if not _fields(item, {"metric_id", "observed_p95_ms", "proposed_budget_ms"}, subject, errors):
                continue
            metric_id, observed, budget = item.get("metric_id"), item.get("observed_p95_ms"), item.get("proposed_budget_ms")
            if metric_id not in EXPECTED_METRICS or metric_id in seen or observed != EXPECTED_METRICS.get(metric_id):
                errors.append(_error("METRIC_SOURCE_INVALID", subject, "metric source mismatch"))
                continue
            seen.add(metric_id)
            expected_budget = math.ceil(float(observed) * 1.75 / 10) * 10
            if type(budget) is not int or budget != expected_budget:
                errors.append(_error("BUDGET_FORMULA_INVALID", f"{subject}.proposed_budget_ms", "budget formula mismatch"))
    if seen != set(EXPECTED_METRICS):
        errors.append(_error("METRIC_SET_INVALID", "metrics", "metric set mismatch"))
    expected_decision = {"required_text": "UI-PERF-001 로컬 성능 예산 제안 승인", "approved": False, "effect_if_approved": "allow_local_precalibration_regression_budget", "does_not_allow": ["notion_reference_timing_claim", "production_slo_claim", "h8_completion_claim", "release_promotion"]}
    decision = value.get("owner_decision")
    if not _fields(decision, set(expected_decision), "owner_decision", errors) or decision != expected_decision:
        errors.append(_error("DECISION_INVALID", "owner_decision", "unapproved decision boundary required"))
    expected_safety = {"synthetic_only": True, "live_local_data_accessed": False, "personal_notion_content_accessed": False, "external_action_executed": False, "completion_matrix_mutated": False}
    safety = value.get("safety")
    if not _fields(safety, set(expected_safety), "safety", errors) or safety != expected_safety:
        errors.append(_error("SAFETY_INVALID", "safety", "safety boundary mismatch"))
    expected_claims = {"local_measurement_green": True, "reference_create_timing_accepted": False, "budget_approved": False, "notion_performance_parity_claimed": False, "core_ui_complete": False, "overall_goal_complete": False}
    claims = value.get("claims")
    if not _fields(claims, set(expected_claims), "claims", errors) or claims != expected_claims:
        errors.append(_error("CLAIMS_INVALID", "claims", "proposal claims mismatch"))
    report = {"contract": CONTRACT, "status": "fail" if errors else "pass", "metric_count": len(seen), "budget_approved": False, "errors": errors}
    return report, 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", type=Path, default=DEFAULT_PROPOSAL)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_performance_budget_proposal(args.proposal)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2, separators=(",", ":") if args.compact else None))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
