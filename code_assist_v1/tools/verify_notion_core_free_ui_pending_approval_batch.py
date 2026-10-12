"""Verify the bounded R1/R3 synthetic-reference approval batch."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from code_assist_v1.tools.verify_notion_core_free_ui_completion import (
    DEFAULT_MATRIX,
    PROFILE,
    RFC3339_PATTERN,
    SHA_PATTERN,
    _file_sha256,
    _safe_repo_path,
    load_json,
    verify_completion_matrix,
)
from code_assist_v1.tools.verify_notion_core_free_ui_r1_revalidation import verify as verify_r1
from code_assist_v1.tools.verify_notion_core_free_ui_r2_r3_revalidation import verify as verify_r2_r3
from code_assist_v1.tools.verify_notion_core_free_ui_review_gate import verify_review_gate


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BATCH = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "pending_approval_batch.r1_r3.20260822.json"
)
COMPLETION_OBSERVATION = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "reference_completion_observation.r1_r3.20260822.json"
)
APPROVAL_RECEIPT = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "pending_approval_receipt.r1_r3.20260822.json"
)
CONTRACT = "code_assist.notion.core_free_ui.pending_approval_batch.v1"
THREAD_ID = "019ff607-9116-78a1-9f40-c580c87c0038"
EXACT_APPROVAL = (
    "UI-R1-R3 \ud569\uc131 reference \ubcf4\uc644 \ucea1\ucc98\uc640 Trash \uc815\ub9ac \uc2b9\uc778; "
    "zero-difference \uac80\uc99d \ud6c4 differential \ub9e4\ud2b8\ub9ad\uc2a4 \uac31\uc2e0 \uc2b9\uc778"
)
EXPECTED_INPUT_PATHS = {
    "scenario_plan": "code_assist_v1/docs/notion_parity/core_free_web_ui/scenarios.v1.json",
    "completion_matrix": "code_assist_v1/docs/notion_parity/core_free_web_ui/completion_matrix.v1.json",
    "review_gate": "code_assist_v1/docs/notion_parity/core_free_web_ui/evidence_review_gate.v1.json",
    "r1_capture": "code_assist_v1/docs/notion_parity/core_free_web_ui/R1_CURRENT_SOURCE_CAPTURE_20260822.json",
    "r2_r3_capture": "code_assist_v1/docs/notion_parity/core_free_web_ui/R2_R3_CURRENT_SOURCE_CAPTURE_20260822.json",
}
EXPECTED_RESOLVED = [
    {
        "phrase": "UI-EDITOR-004 \ub124\uc774\ud2f0\ube0c IME \uc694\uc57d \uc2b9\uc778",
        "status": "resolved_and_sealed",
    },
    {
        "phrase": "UI-PERF-001 \ub85c\uceec \uc131\ub2a5 \uc608\uc0b0 \uc81c\uc548 \uc2b9\uc778",
        "status": "resolved_and_active",
    },
]
EXPECTED_PENDING = [
    {
        "id": "UI-R1-R3-REFERENCE-COMPLETION",
        "authorization_kind": "recoverable_synthetic_external_mutation",
        "scenario_ids": ["UI-SHELL-001", "UI-EDITOR-001", "UI-EDITOR-002", "UI-SEARCH-002"],
        "workspaces": ["REF-FREE-A", "REF-FREE-B"],
        "actions": [
            "repeat only the approved missing reference observations",
            "create synthetic pages or links only where the scenario requires them",
            "capture normalized state, keyboard, focus, accessibility, semantic visual, and error recovery fields",
            "move every created synthetic page to Trash after verification",
        ],
        "forbidden": [
            "personal workspace or personal page access",
            "invitation or external message",
            "public publishing or link creation",
            "permanent delete",
            "raw DOM, network payload, cookie, token, or storage persistence",
        ],
        "cleanup": "trash_only_recoverable",
        "status": "awaiting_owner_approval",
    },
    {
        "id": "UI-R1-R3-DIFFERENTIAL-MATRIX",
        "authorization_kind": "conditional_internal_evidence_mutation",
        "condition": (
            "all affected reference and current-local normalized captures validate and every differential reports "
            "zero observable differences"
        ),
        "actions": [
            "seal immutable differential packets",
            "update only the corresponding completion-matrix scenario rows and aggregate counts",
            "rerun completion, review, Second Brain, and adjacent route gates",
        ],
        "forbidden": [
            "promotion_allowed=true",
            "production flag enablement",
            "live canary or operational migration",
            "claiming full Core UI or overall goal completion",
        ],
        "status": "awaiting_owner_approval_and_zero_difference_condition",
    },
]
EXPECTED_CLAIMS = {
    "personal_content_accessed": False,
    "reference_mutation_executed_by_this_batch": False,
    "completion_matrix_changed_by_this_batch": False,
    "core_ui_complete": False,
    "overall_goal_complete": False,
}
EXPECTED_HISTORICAL_EVIDENCE = {
    "ui_source_build_sha256": "sha256:823563f460ae4f4c878c508654144e6bc6dae0cf77da217a69ae33501abc7ee5",
    "r1_observed_fields": 80,
    "r1_observed_fields_match": True,
    "r2_r3_observed_fields": 38,
    "r2_r3_observed_fields_match": True,
    "differential_ready": False,
    "certified_count": 5,
    "required_scenario_count": 25,
    "promoted_count": 0,
}


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _fields(value: Any, expected: set[str], subject: str, errors: list[dict[str, str]]) -> bool:
    if not isinstance(value, dict):
        errors.append(_error("OBJECT_REQUIRED", subject, "object required"))
        return False
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    errors.extend(_error("FIELD_MISSING", f"{subject}.{key}", "required field missing") for key in missing)
    errors.extend(_error("UNKNOWN_FIELD", f"{subject}.{key}", "unknown field") for key in extra)
    return not missing and not extra


def _binding(
    value: Any,
    expected_path: str,
    subject: str,
    errors: list[dict[str, str]],
    *,
    allow_historical_hash: bool = False,
) -> Path | None:
    if not _fields(value, {"path", "sha256"}, subject, errors):
        return None
    if value.get("path") != expected_path:
        errors.append(_error("BOUND_PATH_MISMATCH", f"{subject}.path", "exact approved repository path required"))
        return None
    path = _safe_repo_path(value.get("path"))
    digest = value.get("sha256")
    if path is None or not path.is_file():
        errors.append(_error("BOUND_FILE_MISSING", subject, "safe bound file required"))
        return None
    if not isinstance(digest, str) or SHA_PATTERN.fullmatch(digest) is None:
        errors.append(_error("SHA256_INVALID", f"{subject}.sha256", "canonical sha256 required"))
    elif not allow_historical_hash and _file_sha256(path) != digest:
        errors.append(_error("BOUND_FILE_HASH_MISMATCH", subject, "bound file hash mismatch"))
    return path


def _executed_completion_is_current() -> bool:
    try:
        observation = load_json(COMPLETION_OBSERVATION)
        completion, exit_code = verify_completion_matrix()
        matrix = load_json(DEFAULT_MATRIX)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return False
    completed_ids = {
        item.get("scenario_id")
        for item in matrix.get("scenarios", [])
        if isinstance(item, dict)
        and item.get("reference_state") == "reference_characterized"
        and item.get("differential_state") == "differential_passed"
    }
    executed_ids = {
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
    }
    return bool(
        observation.get("contract")
        == "code_assist.notion.core_free_ui.reference_completion_observation.v1"
        and observation.get("approval", {}).get("receipt_sha256") == _file_sha256(APPROVAL_RECEIPT)
        and observation.get("cleanup", {}).get("status") == "complete"
        and observation.get("cleanup", {}).get("permanent_delete_used") is False
        and observation.get("claims")
        == {
            "reference_mutation_executed": True,
            "cleanup_executed": True,
            "normalized_evidence_sealed": True,
            "all_differentials_zero": True,
            "completion_matrix_changed": True,
            "core_ui_complete": False,
            "overall_goal_complete": False,
        }
        and exit_code == 0
        and completion.get("status") == "pass"
        and executed_ids.issubset(completed_ids)
        and completion.get("summary", {}).get("reference_characterized", 0) >= 16
        and completion.get("summary", {}).get("differential_passed", 0) >= 16
        and completion.get("summary", {}).get("promoted") == 0
    )


def _source_builds(capture: Any) -> set[str]:
    builds: set[str] = set()
    if not isinstance(capture, dict) or not isinstance(capture.get("scenarios"), dict):
        return builds
    for scenario in capture["scenarios"].values():
        if not isinstance(scenario, dict) or not isinstance(scenario.get("action_captures"), dict):
            continue
        for action in scenario["action_captures"].values():
            if isinstance(action, dict) and isinstance(action.get("source_build_sha256"), str):
                builds.add(action["source_build_sha256"])
    return builds


def verify_pending_approval_batch(path: Path = DEFAULT_BATCH) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    execution_completed = _executed_completion_is_current() if path.resolve() == DEFAULT_BATCH.resolve() else False
    try:
        value = load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {"contract": CONTRACT, "status": "fail", "errors": [_error("BATCH_READ_FAILED", "$", str(exc))]}, 1

    top = {
        "contract",
        "recorded_at",
        "profile",
        "goal",
        "inputs",
        "resolved_approvals",
        "pending_items",
        "exact_combined_approval_phrase",
        "current_evidence",
        "claims",
    }
    if not _fields(value, top, "$", errors):
        return {"contract": CONTRACT, "status": "fail", "errors": errors}, 1
    if value.get("contract") != CONTRACT:
        errors.append(_error("CONTRACT_INVALID", "contract", "contract mismatch"))
    if value.get("profile") != PROFILE:
        errors.append(_error("PROFILE_INVALID", "profile", "profile mismatch"))
    recorded_at = value.get("recorded_at")
    try:
        valid_time = (
            isinstance(recorded_at, str)
            and RFC3339_PATTERN.fullmatch(recorded_at) is not None
            and datetime.fromisoformat(recorded_at.replace("Z", "+00:00")).utcoffset() is not None
        )
    except ValueError:
        valid_time = False
    if not valid_time:
        errors.append(_error("RECORDED_AT_INVALID", "recorded_at", "RFC 3339 timestamp required"))

    goal = value.get("goal")
    expected_goal = {"thread_id": THREAD_ID, "status": "active"}
    if not _fields(goal, set(expected_goal), "goal", errors) or goal != expected_goal:
        errors.append(_error("GOAL_INVALID", "goal", "active exact thread binding required"))

    input_paths: dict[str, Path] = {}
    inputs = value.get("inputs")
    if _fields(inputs, set(EXPECTED_INPUT_PATHS), "inputs", errors):
        for name, expected_path in EXPECTED_INPUT_PATHS.items():
            bound = _binding(
                inputs.get(name),
                expected_path,
                f"inputs.{name}",
                errors,
                allow_historical_hash=execution_completed and name in {"completion_matrix", "review_gate"},
            )
            if bound is not None:
                input_paths[name] = bound

    if value.get("resolved_approvals") != EXPECTED_RESOLVED:
        errors.append(_error("RESOLVED_APPROVALS_INVALID", "resolved_approvals", "sealed approvals mismatch"))
    if value.get("pending_items") != EXPECTED_PENDING:
        errors.append(_error("PENDING_ITEMS_INVALID", "pending_items", "bounded pending actions mismatch"))
    if value.get("exact_combined_approval_phrase") != EXACT_APPROVAL:
        errors.append(_error("APPROVAL_PHRASE_INVALID", "exact_combined_approval_phrase", "exact phrase mismatch"))
    if value.get("claims") != EXPECTED_CLAIMS:
        errors.append(_error("CLAIMS_INVALID", "claims", "non-execution claims mismatch"))

    reports: dict[str, Any] = {}
    if "r1_capture" in input_paths:
        reports["r1"], exit_code = verify_r1(input_paths["r1_capture"])
        if exit_code != 0:
            errors.append(_error("R1_CAPTURE_INVALID", "inputs.r1_capture", "R1 verifier failed"))
    if "r2_r3_capture" in input_paths:
        reports["r2_r3"], exit_code = verify_r2_r3(input_paths["r2_r3_capture"])
        if exit_code != 0:
            errors.append(_error("R2_R3_CAPTURE_INVALID", "inputs.r2_r3_capture", "R2/R3 verifier failed"))
    if "review_gate" in input_paths:
        reports["review"], exit_code = verify_review_gate(input_paths["review_gate"])
        if exit_code != 0:
            errors.append(_error("REVIEW_GATE_INVALID", "inputs.review_gate", "review gate verifier failed"))
    if "completion_matrix" in input_paths and "scenario_plan" in input_paths:
        reports["completion"], exit_code = verify_completion_matrix(
            input_paths["completion_matrix"], input_paths["scenario_plan"]
        )
        if exit_code != 0:
            errors.append(_error("COMPLETION_MATRIX_INVALID", "inputs.completion_matrix", "completion verifier failed"))

    builds: set[str] = set()
    for name in ("r1_capture", "r2_r3_capture"):
        if name in input_paths:
            try:
                builds.update(_source_builds(load_json(input_paths[name])))
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                pass
    expected_evidence = EXPECTED_HISTORICAL_EVIDENCE if execution_completed else {
        "ui_source_build_sha256": next(iter(builds)) if len(builds) == 1 else None,
        "r1_observed_fields": reports.get("r1", {}).get("compared_observed_field_count"),
        "r1_observed_fields_match": reports.get("r1", {}).get("observed_fields_match"),
        "r2_r3_observed_fields": reports.get("r2_r3", {}).get("compared_observed_field_count"),
        "r2_r3_observed_fields_match": reports.get("r2_r3", {}).get("observed_fields_match"),
        "differential_ready": bool(reports.get("r1", {}).get("differential_ready"))
        and bool(reports.get("r2_r3", {}).get("differential_ready")),
        "certified_count": reports.get("review", {}).get("counts", {}).get("certified"),
        "required_scenario_count": reports.get("completion", {}).get("summary", {}).get("required_scenarios"),
        "promoted_count": reports.get("completion", {}).get("summary", {}).get("promoted"),
    }
    if value.get("current_evidence") != expected_evidence:
        errors.append(_error("CURRENT_EVIDENCE_INVALID", "current_evidence", "bound verifier summary mismatch"))

    report = {
        "contract": CONTRACT,
        "status": "fail" if errors else "pass",
        "pending_item_count": 2,
        "external_mutation_authorized": False,
        "exact_approval_phrase": EXACT_APPROVAL,
        "errors": errors,
    }
    return report, 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_pending_approval_batch(args.batch)
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
