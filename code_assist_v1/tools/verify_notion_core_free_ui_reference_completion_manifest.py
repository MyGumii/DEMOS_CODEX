"""Verify the exact R1/R3 synthetic-reference execution manifest."""

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
from code_assist_v1.tools.verify_notion_core_free_ui_pending_approval_batch import (
    DEFAULT_BATCH,
    verify_pending_approval_batch,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "reference_completion_execution_manifest.r1_r3.20260822.json"
)
CONTRACT = "code_assist.notion.core_free_ui.reference_completion_execution.v1"
PLAN_PATH = "code_assist_v1/docs/notion_parity/core_free_web_ui/scenarios.v1.json"
BATCH_PATH = "code_assist_v1/docs/notion_parity/core_free_web_ui/pending_approval_batch.r1_r3.20260822.json"
OBSERVABLE_FIELDS = ["state", "keyboard", "focus", "a11y", "semantic_visual", "error_recovery"]
EXPECTED_ACTIONS = [
    ("UI-SHELL-001", "UI-SHELL-001-A01", "REF-FREE-B", False),
    ("UI-SHELL-001", "UI-SHELL-001-A02", "REF-FREE-A", False),
    ("UI-EDITOR-001", "UI-EDITOR-001-A01", "REF-FREE-A", True),
    ("UI-EDITOR-001", "UI-EDITOR-001-A02", "REF-FREE-A", True),
    ("UI-EDITOR-001", "UI-EDITOR-001-A03", "REF-FREE-A", False),
    ("UI-EDITOR-001", "UI-EDITOR-001-A04", "REF-FREE-A", False),
    ("UI-EDITOR-002", "UI-EDITOR-002-A01", "REF-FREE-A", False),
    ("UI-EDITOR-002", "UI-EDITOR-002-A02", "REF-FREE-A", False),
    ("UI-SEARCH-002", "UI-SEARCH-002-A01", "REF-FREE-A", False),
    ("UI-SEARCH-002", "UI-SEARCH-002-A02", "REF-FREE-A", False),
]
EXPECTED_FIXTURE_ALIASES = [
    "UI-PARITY-R1R3-A-ROOT",
    "UI-PARITY-R1R3-A-CHILD",
    "UI-PARITY-R1R3-B-MARKER",
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


def _binding(value: Any, expected_path: str, subject: str, errors: list[dict[str, str]]) -> Path | None:
    if not _fields(value, {"path", "sha256"}, subject, errors):
        return None
    if value.get("path") != expected_path:
        errors.append(_error("BOUND_PATH_MISMATCH", f"{subject}.path", "exact path required"))
        return None
    path = _safe_repo_path(value.get("path"))
    digest = value.get("sha256")
    if path is None or not path.is_file():
        errors.append(_error("BOUND_FILE_MISSING", subject, "safe bound file required"))
        return None
    if not isinstance(digest, str) or SHA_PATTERN.fullmatch(digest) is None:
        errors.append(_error("SHA256_INVALID", f"{subject}.sha256", "canonical sha256 required"))
    elif _file_sha256(path) != digest:
        errors.append(_error("BOUND_FILE_HASH_MISMATCH", subject, "bound file hash mismatch"))
    return path


def verify_reference_completion_manifest(path: Path = DEFAULT_MANIFEST) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    try:
        value = load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {"contract": CONTRACT, "status": "fail", "errors": [_error("MANIFEST_READ_FAILED", "$", str(exc))]}, 1
    top = {"contract", "recorded_at", "profile", "inputs", "preflight", "fixtures", "actions", "execution_policy", "claims"}
    if not _fields(value, top, "$", errors):
        return {"contract": CONTRACT, "status": "fail", "errors": errors}, 1
    if value.get("contract") != CONTRACT:
        errors.append(_error("CONTRACT_INVALID", "contract", "contract mismatch"))
    if value.get("profile") != PROFILE:
        errors.append(_error("PROFILE_INVALID", "profile", "profile mismatch"))
    recorded_at = value.get("recorded_at")
    try:
        valid_time = isinstance(recorded_at, str) and RFC3339_PATTERN.fullmatch(recorded_at) is not None and datetime.fromisoformat(recorded_at.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        valid_time = False
    if not valid_time:
        errors.append(_error("RECORDED_AT_INVALID", "recorded_at", "RFC 3339 timestamp required"))

    plan_path = batch_path = None
    inputs = value.get("inputs")
    if _fields(inputs, {"scenario_plan", "pending_approval_batch"}, "inputs", errors):
        plan_path = _binding(inputs.get("scenario_plan"), PLAN_PATH, "inputs.scenario_plan", errors)
        batch_path = _binding(inputs.get("pending_approval_batch"), BATCH_PATH, "inputs.pending_approval_batch", errors)
    if batch_path is not None:
        report, exit_code = verify_pending_approval_batch(batch_path)
        if exit_code != 0:
            errors.append(_error("PENDING_BATCH_INVALID", "inputs.pending_approval_batch", json.dumps(report, ensure_ascii=False)))

    expected_preflight = {
        "approval_receipt_required": True,
        "approval_receipt_must_bind_this_manifest": True,
        "c0_reference_guard_required": True,
        "chrome_control_required": True,
        "exact_workspace_aliases": ["REF-FREE-A", "REF-FREE-B"],
        "synthetic_only": True,
        "personal_workspace_access_allowed": False,
        "existing_page_reuse_allowed": False,
        "public_publish_or_invite_allowed": False,
        "permanent_delete_allowed": False,
    }
    if value.get("preflight") != expected_preflight:
        errors.append(_error("PREFLIGHT_INVALID", "preflight", "fail-closed preflight mismatch"))

    fixtures = value.get("fixtures")
    aliases = [item.get("alias") for item in fixtures] if isinstance(fixtures, list) and all(isinstance(item, dict) for item in fixtures) else []
    if aliases != EXPECTED_FIXTURE_ALIASES:
        errors.append(_error("FIXTURES_INVALID", "fixtures", "exact three fresh synthetic fixtures required"))
    if isinstance(fixtures, list):
        for index, fixture in enumerate(fixtures):
            if not isinstance(fixture, dict) or fixture.get("workspace") not in {"REF-FREE-A", "REF-FREE-B"} or "new_synthetic" not in str(fixture.get("kind")):
                errors.append(_error("FIXTURE_SCOPE_INVALID", f"fixtures[{index}]", "fresh synthetic A/B fixture required"))

    action_contracts: dict[str, dict[str, Any]] = {}
    if plan_path is not None:
        plan = load_json(plan_path)
        for scenario in plan.get("required_scenarios", []) if isinstance(plan, dict) else []:
            if isinstance(scenario, dict):
                for action in scenario.get("actions", []) if isinstance(scenario.get("actions"), list) else []:
                    if isinstance(action, dict) and isinstance(action.get("action_id"), str):
                        action_contracts[action["action_id"]] = action
    actions = value.get("actions")
    if not isinstance(actions, list) or len(actions) != len(EXPECTED_ACTIONS):
        errors.append(_error("ACTIONS_INVALID", "actions", "exact ten ordered actions required"))
    else:
        expected_fields = {"order", "scenario_id", "action_id", "workspace", "mutation_class", "setup_only", "capture_fields"}
        for index, (action, expected) in enumerate(zip(actions, EXPECTED_ACTIONS), start=1):
            subject = f"actions[{index - 1}]"
            if not _fields(action, expected_fields, subject, errors):
                continue
            scenario_id, action_id, workspace, setup_only = expected
            if (action.get("order"), action.get("scenario_id"), action.get("action_id"), action.get("workspace"), action.get("setup_only")) != (index, scenario_id, action_id, workspace, setup_only):
                errors.append(_error("ACTION_ORDER_OR_SCOPE_INVALID", subject, "action order or approved A/B scope mismatch"))
            contract_action = action_contracts.get(action_id)
            if contract_action is None or contract_action.get("scenario_id") != scenario_id or contract_action.get("mutation_class") != action.get("mutation_class"):
                errors.append(_error("ACTION_CONTRACT_INVALID", subject, "scenario action contract mismatch"))
            expected_capture = [] if setup_only else OBSERVABLE_FIELDS
            if action.get("capture_fields") != expected_capture:
                errors.append(_error("CAPTURE_FIELDS_INVALID", f"{subject}.capture_fields", "exact normalized capture fields required"))

    expected_policy = {
        "authorize_each_action_with_reference_guard": True,
        "persist_only_normalized_redacted_observations": True,
        "persist_page_ids_or_raw_labels": False,
        "compute_differential_before_matrix_update": True,
        "matrix_update_requires_zero_differences": True,
        "cleanup_runs_after_success_or_capture_failure": True,
        "cleanup_mode": "trash_only_recoverable",
        "cleanup_targets": ["UI-PARITY-R1R3-A-ROOT", "UI-PARITY-R1R3-B-MARKER"],
        "stop_on_personal_workspace_visibility": True,
        "stop_on_workspace_identity_uncertainty": True,
        "stop_on_browser_control_loss": True,
        "stop_before_public_invite_or_permanent_delete": True,
    }
    if value.get("execution_policy") != expected_policy:
        errors.append(_error("EXECUTION_POLICY_INVALID", "execution_policy", "execution/cleanup policy mismatch"))
    expected_claims = {
        "approval_received": False,
        "reference_mutation_executed": False,
        "cleanup_executed": False,
        "differential_packets_sealed": False,
        "completion_matrix_changed": False,
        "core_ui_complete": False,
        "overall_goal_complete": False,
    }
    if value.get("claims") != expected_claims:
        errors.append(_error("CLAIMS_INVALID", "claims", "pre-execution claims mismatch"))
    report = {
        "contract": CONTRACT,
        "status": "fail" if errors else "pass",
        "action_count": len(EXPECTED_ACTIONS),
        "fixture_count": len(EXPECTED_FIXTURE_ALIASES),
        "approval_received": False,
        "reference_mutation_executed": False,
        "errors": errors,
    }
    return report, 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_reference_completion_manifest(args.manifest)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2, separators=(",", ":") if args.compact else None))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
