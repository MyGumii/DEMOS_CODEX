"""Verify the checked-in Core Free Web UI completion snapshot.

The verifier is deliberately read-only and import-side-effect-free.  A passing
result means that the snapshot is internally consistent and hash-bound; it does
not mean that UI parity is complete.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import Any

from code_assist_v1.tools.notion_parity.evidence import canonical_sha256
from code_assist_v1.tools.notion_parity.ui_capture import (
    capture_sha256,
    compare_precalibration_captures,
    validate_capture,
)
from code_assist_v1.tools.notion_core_free_ui_remaining_evidence import (
    CONTRACT as EVIDENCE_MODES_CONTRACT,
    SCENARIO_MODES as APPROVED_EVIDENCE_MODES,
    validate_remaining_evidence_packet,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "completion_matrix.v1.json"
)
DEFAULT_PLAN = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "scenarios.v1.json"
)

CONTRACT = "code_assist.notion.core_free_ui.completion_matrix.v1"
PROFILE = "free-owner-chrome-desktop-ko-KR"
EXPECTED_SCENARIO_COUNT = 25
EXPECTED_TOP_FIELDS = {
    "contract",
    "recorded_at",
    "profile",
    "scenario_plan",
    "latest_checkpoint",
    "summary",
    "scenarios",
    "cross_phase_gates",
    "completion_claims",
}
EXPECTED_BINDING_FIELDS = {"path", "sha256"}
EXPECTED_SCENARIO_FIELDS = {
    "scenario_id",
    "phase",
    "manifest_state",
    "local_state",
    "local_evidence",
    "reference_state",
    "differential_state",
    "promotion_allowed",
}
OPTIONAL_SCENARIO_FIELDS = {
    "evidence_mode",
    "reference_evidence",
    "differential_evidence",
}
EXPECTED_SUMMARY_FIELDS = {
    "required_scenarios",
    "local_real_browser_green",
    "local_measurement_green",
    "reference_characterized",
    "differential_passed",
    "local_integration_passed",
    "local_recovery_contract_passed",
    "measurement_contract_passed",
    "evidence_satisfied",
    "unresolved",
    "promoted",
}
EXPECTED_CROSS_PHASE_GATE_FIELDS = {
    "safe_signed_in_browser_control",
    "personal_workspace_accessed",
    "reference_workspace_accessed",
    "reference_characterization",
    "differential_evidence",
    "scenario_evidence",
    "performance_budget",
    "formal_accessibility_review",
    "legal_brand_review",
    "production_default_off_flag",
    "live_canary",
    "operational_backup_restore",
    "isolated_h9_recovery",
    "second_brain_hard_journeys",
    "evidence_pytest_host_gate",
}
STATIC_CROSS_PHASE_GATES = {
    "performance_budget": "approved_isolated_local_precalibration",
    "formal_accessibility_review": "pending",
    "legal_brand_review": "pending",
    "production_default_off_flag": "not_authorized_not_implemented",
    "live_canary": "not_authorized_not_run",
    "operational_backup_restore": "not_authorized_not_run",
    "isolated_h9_recovery": "green",
    "second_brain_hard_journeys": "local_green",
}
EXPECTED_COMPLETION_CLAIM_FIELDS = {
    "all_required_local_contracts_green",
    "all_required_reference_characterized",
    "all_required_differential_passed",
    "all_required_evidence_satisfied",
    "h8_complete",
    "h9_complete",
    "core_ui_complete",
    "overall_goal_complete",
}
EVIDENCE_PACKET_CONTRACT = "code_assist.notion.core_free_ui.reference_differential.v1"
EVIDENCE_PACKET_FIELDS = {
    "contract",
    "recorded_at",
    "scenario_id",
    "profile",
    "review",
    "scope",
    "reference",
    "local",
    "differential",
}
EVIDENCE_CAPTURE_ALLOWLIST = (
    "REF-FREE-A",
    "REF-FREE-B",
    "button",
    "dialog",
    "five-views-rendered",
    "hidden_with_access_tooltip",
    "option",
    "posted",
    "region",
    "resolved_and_hidden",
    "searchbox",
    "tablist",
    "textbox",
)
SHA_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


class DuplicateJsonKey(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJsonKey(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def load_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_object_without_duplicates,
        parse_constant=_reject_constant,
    )


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _safe_repo_path(value: Any) -> Path | None:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        return None
    pure = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if pure.is_absolute() or windows.is_absolute() or windows.drive:
        return None
    if not pure.parts or pure.parts[0] != "code_assist_v1":
        return None
    if any(part in {"", ".", ".."} for part in pure.parts):
        return None
    candidate = REPO_ROOT.joinpath(*pure.parts)
    try:
        candidate.resolve(strict=False).relative_to(REPO_ROOT.resolve(strict=True))
    except (OSError, ValueError):
        return None
    current = REPO_ROOT
    for part in pure.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            return None
    return candidate


def _check_exact_fields(
    value: Any,
    expected: set[str],
    subject: str,
    errors: list[dict[str, str]],
) -> bool:
    if not isinstance(value, dict):
        errors.append(_error("OBJECT_REQUIRED", subject, f"{subject} must be an object"))
        return False
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    for key in missing:
        errors.append(_error("FIELD_MISSING", f"{subject}.{key}", "required field missing"))
    for key in extra:
        errors.append(_error("UNKNOWN_FIELD", f"{subject}.{key}", "unknown field"))
    return not missing and not extra


def _check_bound_file(
    binding: Any,
    subject: str,
    errors: list[dict[str, str]],
) -> Path | None:
    if not _check_exact_fields(binding, EXPECTED_BINDING_FIELDS, subject, errors):
        return None
    path = _safe_repo_path(binding.get("path"))
    if path is None:
        errors.append(_error("PATH_INVALID", f"{subject}.path", "path must be a safe repository-relative file"))
        return None
    digest = binding.get("sha256")
    if not isinstance(digest, str) or not SHA_PATTERN.fullmatch(digest):
        errors.append(_error("SHA256_INVALID", f"{subject}.sha256", "sha256 must be canonical lowercase"))
        return None
    if not path.is_file():
        errors.append(_error("BOUND_FILE_MISSING", f"{subject}.path", "bound file is missing"))
        return None
    if _file_sha256(path) != digest:
        errors.append(_error("BOUND_FILE_HASH_MISMATCH", subject, "bound file hash does not match"))
    return path


def _check_scenario_fields(
    value: Any,
    subject: str,
    errors: list[dict[str, str]],
) -> bool:
    if not isinstance(value, dict):
        errors.append(_error("OBJECT_REQUIRED", subject, f"{subject} must be an object"))
        return False
    missing = sorted(EXPECTED_SCENARIO_FIELDS - set(value))
    extra = sorted(set(value) - EXPECTED_SCENARIO_FIELDS - OPTIONAL_SCENARIO_FIELDS)
    for key in missing:
        errors.append(_error("FIELD_MISSING", f"{subject}.{key}", "required field missing"))
    for key in extra:
        errors.append(_error("UNKNOWN_FIELD", f"{subject}.{key}", "unknown field"))
    return not missing and not extra


def _validate_reference_differential_packet(
    path: Path,
    *,
    scenario_id: str,
    subject: str,
    errors: list[dict[str, str]],
) -> None:
    try:
        packet = load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(_error("EVIDENCE_READ_FAILED", subject, str(exc)))
        return
    if not _check_exact_fields(packet, EVIDENCE_PACKET_FIELDS, subject, errors):
        return
    if packet.get("contract") != EVIDENCE_PACKET_CONTRACT:
        errors.append(_error("EVIDENCE_CONTRACT_INVALID", f"{subject}.contract", "evidence contract is invalid"))
    if packet.get("scenario_id") != scenario_id:
        errors.append(_error("EVIDENCE_SCENARIO_MISMATCH", f"{subject}.scenario_id", "evidence scenario does not match"))
    if packet.get("profile") != PROFILE:
        errors.append(_error("EVIDENCE_PROFILE_INVALID", f"{subject}.profile", "evidence profile is invalid"))

    review = packet.get("review")
    if review != {
        "approved": True,
        "approved_by": "workspace_owner",
        "approval_scope": "redacted_summary_only",
    }:
        errors.append(_error("EVIDENCE_REVIEW_INVALID", f"{subject}.review", "redacted reference review is not approved"))
    scope = packet.get("scope")
    required_scope = {
        "reference_workspace_alias": "REF-FREE-A",
        "secondary_workspace_boundary_confirmed": True,
        "synthetic_only": True,
        "personal_workspace_accessed": False,
        "personal_page_accessed": False,
        "external_side_effect_attempted": False,
        "raw_dom_captured": False,
        "network_payload_captured": False,
        "screenshot_count": 0,
    }
    if scope != required_scope:
        errors.append(_error("EVIDENCE_SCOPE_INVALID", f"{subject}.scope", "evidence scope is not the approved synthetic-only boundary"))

    reference = packet.get("reference")
    local = packet.get("local")
    differential = packet.get("differential")
    if not isinstance(reference, dict) or not isinstance(local, dict) or not isinstance(differential, dict):
        errors.append(_error("EVIDENCE_SECTION_INVALID", subject, "reference, local, and differential must be objects"))
        return
    receipt = reference.get("receipt")
    if not isinstance(receipt, dict) or any(
        (
            receipt.get("contract") != "notion_core_free_ui_manual_reference_receipt_v1",
            receipt.get("status") != "accepted_reference_observation",
            receipt.get("scenario_id") != scenario_id,
            receipt.get("profile") != PROFILE,
            receipt.get("personal_workspace_accessed") is not False,
            receipt.get("personal_page_accessed") is not False,
            receipt.get("external_side_effect_attempted") is not False,
            receipt.get("path_disclosed") is not False,
            receipt.get("promotion_allowed") is not False,
            receipt.get("screenshot_count") != 0,
        )
    ):
        errors.append(_error("REFERENCE_RECEIPT_INVALID", f"{subject}.reference.receipt", "manual reference receipt is invalid"))

    reference_captures = reference.get("captures")
    local_captures = local.get("captures")
    comparisons = differential.get("comparisons")
    if not isinstance(reference_captures, dict) or not isinstance(local_captures, dict) or not isinstance(comparisons, dict):
        errors.append(_error("EVIDENCE_CAPTURES_INVALID", subject, "capture and comparison maps are required"))
        return
    action_ids = sorted(reference_captures)
    if not action_ids or sorted(local_captures) != action_ids or sorted(comparisons) != action_ids:
        errors.append(_error("EVIDENCE_ACTION_SET_MISMATCH", subject, "reference, local, and differential action sets must match"))
        return
    local_hashes = local.get("capture_sha256")
    if not isinstance(local_hashes, dict) or sorted(local_hashes) != action_ids:
        errors.append(_error("LOCAL_CAPTURE_HASHES_INVALID", f"{subject}.local.capture_sha256", "local capture hashes must cover every action"))
        return

    for action_id in action_ids:
        ref_capture = reference_captures[action_id]
        local_capture = local_captures[action_id]
        for side, capture in (("reference", ref_capture), ("local", local_capture)):
            capture_errors = validate_capture(
                capture,
                synthetic_allowlist=EVIDENCE_CAPTURE_ALLOWLIST,
            )
            for item in capture_errors:
                errors.append(
                    _error(
                        "EVIDENCE_CAPTURE_INVALID",
                        f"{subject}.{side}.captures.{action_id}:{item['subject']}",
                        item["code"],
                    )
                )
        if isinstance(local_hashes, dict) and local_hashes.get(action_id) != capture_sha256(local_capture):
            errors.append(_error("LOCAL_CAPTURE_HASH_MISMATCH", f"{subject}.local.capture_sha256.{action_id}", "local capture hash does not match"))
        expected_report = compare_precalibration_captures(
            ref_capture,
            local_capture,
            synthetic_allowlist=EVIDENCE_CAPTURE_ALLOWLIST,
        )
        if comparisons.get(action_id) != expected_report:
            errors.append(_error("DIFFERENTIAL_REPORT_MISMATCH", f"{subject}.differential.comparisons.{action_id}", "differential report does not match the captures"))
        elif expected_report.get("status") != "pass" or expected_report.get("difference_count") != 0:
            errors.append(_error("DIFFERENTIAL_NOT_PASSED", f"{subject}.differential.comparisons.{action_id}", "differential contains a mismatch"))
    if differential.get("policy_state") != "precalibration" or differential.get("promotion_allowed") is not False:
        errors.append(_error("DIFFERENTIAL_POLICY_INVALID", f"{subject}.differential", "precalibration evidence cannot promote"))
    if differential.get("all_actions_passed") is not True:
        errors.append(_error("DIFFERENTIAL_NOT_PASSED", f"{subject}.differential.all_actions_passed", "all actions must pass"))


def _validate_mode_evidence_packet(
    path: Path,
    *,
    scenario_id: str,
    expected_mode: str,
    require_satisfied: bool,
    subject: str,
    errors: list[dict[str, str]],
) -> None:
    try:
        packet = load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(_error("EVIDENCE_READ_FAILED", subject, str(exc)))
        return
    if packet.get("contract") != EVIDENCE_MODES_CONTRACT:
        errors.append(_error("EVIDENCE_CONTRACT_INVALID", f"{subject}.contract", "evidence-mode contract is invalid"))
        return
    for message in validate_remaining_evidence_packet(packet):
        errors.append(_error("EVIDENCE_MODE_PACKET_INVALID", subject, message))
    entries = packet.get("scenarios")
    if not isinstance(entries, list):
        errors.append(_error("EVIDENCE_MODE_SCENARIOS_INVALID", f"{subject}.scenarios", "scenario entries are required"))
        return
    matches = [item for item in entries if isinstance(item, dict) and item.get("scenario_id") == scenario_id]
    if len(matches) != 1:
        errors.append(_error("EVIDENCE_MODE_SCENARIO_MISSING", f"{subject}.scenarios", "exactly one scenario entry is required"))
        return
    entry = matches[0]
    if entry.get("evidence_mode") != expected_mode:
        errors.append(_error("EVIDENCE_MODE_MISMATCH", f"{subject}.scenarios.{scenario_id}", "scenario evidence mode does not match"))
    valid_statuses = {"evidence_satisfied"} if require_satisfied else {"evidence_satisfied", "evidence_pending"}
    if entry.get("result", {}).get("status") not in valid_statuses:
        errors.append(_error("EVIDENCE_MODE_NOT_SATISFIED", f"{subject}.scenarios.{scenario_id}.result", "scenario evidence is not satisfied"))


def _validate_completed_binding(
    binding: Any,
    *,
    scenario_id: str,
    evidence_mode: str,
    require_satisfied: bool,
    subject: str,
    errors: list[dict[str, str]],
) -> Path | None:
    path = _check_bound_file(binding, subject, errors)
    if path is None:
        return None
    try:
        contract = load_json(path).get("contract")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(_error("EVIDENCE_READ_FAILED", subject, str(exc)))
        return path
    if contract == EVIDENCE_PACKET_CONTRACT:
        if evidence_mode != "reference_differential":
            errors.append(_error("EVIDENCE_MODE_MISMATCH", subject, "legacy reference packet requires reference_differential mode"))
        _validate_reference_differential_packet(
            path,
            scenario_id=scenario_id,
            subject=f"{subject}.packet",
            errors=errors,
        )
    elif contract == EVIDENCE_MODES_CONTRACT:
        _validate_mode_evidence_packet(
            path,
            scenario_id=scenario_id,
            expected_mode=evidence_mode,
            require_satisfied=require_satisfied,
            subject=f"{subject}.packet",
            errors=errors,
        )
    else:
        errors.append(_error("EVIDENCE_CONTRACT_INVALID", f"{subject}.contract", "unsupported evidence contract"))
    return path


def verify_completion_matrix(
    matrix_path: Path = DEFAULT_MATRIX,
    plan_path: Path = DEFAULT_PLAN,
) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    try:
        matrix = load_json(matrix_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "contract": CONTRACT,
            "status": "fail",
            "completion_state": "unverified",
            "errors": [_error("MATRIX_READ_FAILED", "$", str(exc))],
        }
        return report, 1
    try:
        plan = load_json(plan_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "contract": CONTRACT,
            "status": "fail",
            "completion_state": "unverified",
            "errors": [_error("PLAN_READ_FAILED", "$", str(exc))],
        }
        return report, 1

    if not _check_exact_fields(matrix, EXPECTED_TOP_FIELDS, "$", errors):
        report = {
            "contract": CONTRACT,
            "status": "fail",
            "completion_state": "unverified",
            "errors": errors,
        }
        return report, 1

    if matrix.get("contract") != CONTRACT:
        errors.append(_error("CONTRACT_INVALID", "contract", "completion contract is invalid"))
    if matrix.get("profile") != PROFILE:
        errors.append(_error("PROFILE_INVALID", "profile", "completion profile is invalid"))
    recorded_at = matrix.get("recorded_at")
    if not isinstance(recorded_at, str) or not RFC3339_PATTERN.fullmatch(recorded_at):
        errors.append(_error("RECORDED_AT_INVALID", "recorded_at", "recorded_at must be RFC 3339"))
    else:
        try:
            if datetime.fromisoformat(recorded_at.replace("Z", "+00:00")).utcoffset() is None:
                raise ValueError("timezone is required")
        except ValueError:
            errors.append(_error("RECORDED_AT_INVALID", "recorded_at", "recorded_at must include a valid timezone"))

    scenario_plan = matrix.get("scenario_plan")
    bound_plan_path: Path | None = None
    if _check_exact_fields(
        scenario_plan,
        {"path", "file_sha256", "canonical_sha256"},
        "scenario_plan",
        errors,
    ):
        bound_plan_path = _safe_repo_path(scenario_plan.get("path"))
        if bound_plan_path is None:
            errors.append(
                _error(
                    "PATH_INVALID",
                    "scenario_plan.path",
                    "path must be a safe repository-relative file",
                )
            )
        elif not bound_plan_path.is_file():
            errors.append(
                _error(
                    "BOUND_FILE_MISSING",
                    "scenario_plan.path",
                    "bound plan is missing",
                )
            )
    _check_bound_file(matrix.get("latest_checkpoint"), "latest_checkpoint", errors)
    try:
        if bound_plan_path is not None and bound_plan_path.resolve() != plan_path.resolve(strict=True):
            errors.append(_error("PLAN_PATH_MISMATCH", "scenario_plan.path", "bound plan is not the supplied plan"))
    except OSError:
        errors.append(_error("PLAN_PATH_MISMATCH", "scenario_plan.path", "supplied plan cannot be resolved"))
    if isinstance(scenario_plan, dict):
        expected_canonical = scenario_plan.get("canonical_sha256")
        if not isinstance(expected_canonical, str) or not SHA_PATTERN.fullmatch(expected_canonical):
            errors.append(_error("PLAN_CANONICAL_SHA_INVALID", "scenario_plan.canonical_sha256", "canonical plan hash is invalid"))
        elif canonical_sha256(plan) != expected_canonical:
            errors.append(_error("PLAN_CANONICAL_SHA_MISMATCH", "scenario_plan.canonical_sha256", "canonical plan hash does not match"))
        file_digest = scenario_plan.get("file_sha256")
        if not isinstance(file_digest, str) or not SHA_PATTERN.fullmatch(file_digest):
            errors.append(_error("PLAN_FILE_SHA_INVALID", "scenario_plan.file_sha256", "plan byte hash is invalid"))
        elif plan_path.is_file() and _file_sha256(plan_path) != file_digest:
            errors.append(_error("PLAN_FILE_SHA_MISMATCH", "scenario_plan.file_sha256", "plan byte hash does not match"))

    plan_scenarios = plan.get("required_scenarios") if isinstance(plan, dict) else None
    scenarios = matrix.get("scenarios")
    if not isinstance(plan_scenarios, list):
        errors.append(_error("PLAN_SCENARIOS_INVALID", "plan.required_scenarios", "required scenarios must be an array"))
        plan_scenarios = []
    if not isinstance(scenarios, list):
        errors.append(_error("SCENARIOS_INVALID", "scenarios", "completion scenarios must be an array"))
        scenarios = []

    plan_by_id: dict[str, dict[str, Any]] = {}
    plan_ids: list[str] = []
    for index, item in enumerate(plan_scenarios):
        if not isinstance(item, dict) or not isinstance(item.get("scenario_id"), str):
            errors.append(_error("PLAN_SCENARIO_INVALID", f"plan.required_scenarios[{index}]", "scenario must have an id"))
            continue
        scenario_id = item["scenario_id"]
        if scenario_id in plan_by_id:
            errors.append(_error("PLAN_SCENARIO_DUPLICATE", f"plan.required_scenarios[{index}]", "scenario id is duplicated"))
        plan_by_id[scenario_id] = item
        plan_ids.append(scenario_id)

    matrix_ids: list[str] = []
    local_real_browser_green = 0
    local_measurement_green = 0
    reference_characterized = 0
    differential_passed = 0
    local_integration_passed = 0
    local_recovery_contract_passed = 0
    measurement_contract_passed = 0
    evidence_satisfied = 0
    reference_applicable = 0
    differential_applicable = 0
    promoted = 0
    for index, item in enumerate(scenarios):
        subject = f"scenarios[{index}]"
        if not _check_scenario_fields(item, subject, errors):
            continue
        scenario_id = item.get("scenario_id")
        if not isinstance(scenario_id, str):
            errors.append(_error("SCENARIO_ID_INVALID", f"{subject}.scenario_id", "scenario id must be text"))
            continue
        matrix_ids.append(scenario_id)
        planned = plan_by_id.get(scenario_id)
        if planned is None:
            errors.append(_error("SCENARIO_UNKNOWN", f"{subject}.scenario_id", "scenario is not in the plan"))
        else:
            if item.get("phase") != planned.get("phase"):
                errors.append(_error("SCENARIO_PHASE_MISMATCH", f"{subject}.phase", "phase does not match the plan"))
            if item.get("manifest_state") != planned.get("evidence_state"):
                errors.append(_error("MANIFEST_STATE_MISMATCH", f"{subject}.manifest_state", "manifest state does not match the plan"))

        expected_local = "local_measurement_green" if scenario_id == "UI-PERF-001" else "local_real_browser_green"
        if item.get("local_state") != expected_local:
            errors.append(_error("LOCAL_STATE_INVALID", f"{subject}.local_state", f"local state must be {expected_local}"))
        local_real_browser_green += item.get("local_state") == "local_real_browser_green"
        local_measurement_green += item.get("local_state") == "local_measurement_green"
        _check_bound_file(item.get("local_evidence"), f"{subject}.local_evidence", errors)

        evidence_mode = item.get("evidence_mode", "reference_differential")
        expected_mode = APPROVED_EVIDENCE_MODES.get(scenario_id, "reference_differential")
        if evidence_mode != expected_mode:
            errors.append(_error("EVIDENCE_MODE_MISMATCH", f"{subject}.evidence_mode", "scenario evidence mode does not match the approved contract"))
        reference_applicable += evidence_mode not in {
            "local_second_brain_contract",
            "local_failure_recovery_contract",
        }
        differential_applicable += evidence_mode in {
            "reference_differential",
            "reference_observable_outcome",
        }

        reference_state = item.get("reference_state")
        valid_reference_states = {
            "reference_differential": {"pending_safe_browser_transport", "reference_characterized"},
            "reference_observable_outcome": {"pending_safe_browser_transport", "reference_characterized"},
            "local_second_brain_contract": {"not_applicable_local_integration"},
            "local_failure_recovery_contract": {
                "safely_inapplicable_for_failure_injection"
            },
            "reference_measurement_local_budget": {"pending_safe_browser_transport", "reference_measurement_observed"},
        }.get(evidence_mode, set())
        if reference_state not in valid_reference_states:
            errors.append(_error("REFERENCE_STATE_INVALID", f"{subject}.reference_state", "reference state is invalid"))
        reference_is_characterized = reference_state in {"reference_characterized", "reference_measurement_observed"}
        reference_characterized += reference_is_characterized
        reference_binding = item.get("reference_evidence")
        reference_path: Path | None = None
        if reference_is_characterized:
            if reference_binding is None:
                errors.append(_error("REFERENCE_EVIDENCE_REQUIRED", f"{subject}.reference_evidence", "characterized reference requires hash-bound evidence"))
            else:
                reference_path = _validate_completed_binding(
                    reference_binding,
                    scenario_id=scenario_id,
                    evidence_mode=evidence_mode,
                    require_satisfied=False,
                    subject=f"{subject}.reference_evidence",
                    errors=errors,
                )
        elif reference_binding is not None:
            errors.append(_error("REFERENCE_EVIDENCE_FORBIDDEN", f"{subject}.reference_evidence", "pending reference cannot bind completed evidence"))

        differential_state = item.get("differential_state")
        valid_differential_states = {
            "reference_differential": {"pending_reference", "differential_passed"},
            "reference_observable_outcome": {"pending_reference", "differential_passed"},
            "local_second_brain_contract": {"local_integration_passed"},
            "local_failure_recovery_contract": {
                "local_recovery_contract_passed"
            },
            "reference_measurement_local_budget": {"pending_reference_and_budget", "measurement_contract_passed"},
        }.get(evidence_mode, set())
        if differential_state not in valid_differential_states:
            errors.append(_error("DIFFERENTIAL_STATE_INVALID", f"{subject}.differential_state", "differential state is invalid"))
        differential_is_passed = differential_state == "differential_passed"
        local_is_passed = differential_state == "local_integration_passed"
        recovery_is_passed = (
            differential_state == "local_recovery_contract_passed"
        )
        measurement_is_passed = differential_state == "measurement_contract_passed"
        differential_passed += differential_is_passed
        local_integration_passed += local_is_passed
        local_recovery_contract_passed += recovery_is_passed
        measurement_contract_passed += measurement_is_passed
        scenario_is_satisfied = (
            differential_is_passed
            or local_is_passed
            or recovery_is_passed
            or measurement_is_passed
        )
        evidence_satisfied += scenario_is_satisfied
        differential_binding = item.get("differential_evidence")
        if scenario_is_satisfied:
            if evidence_mode not in {
                "local_second_brain_contract",
                "local_failure_recovery_contract",
            } and not reference_is_characterized:
                errors.append(_error("DIFFERENTIAL_REFERENCE_REQUIRED", f"{subject}.differential_state", "differential pass requires a characterized reference"))
            if differential_binding is None:
                errors.append(_error("DIFFERENTIAL_EVIDENCE_REQUIRED", f"{subject}.differential_evidence", "differential pass requires hash-bound evidence"))
            else:
                differential_path = _validate_completed_binding(
                    differential_binding,
                    scenario_id=scenario_id,
                    evidence_mode=evidence_mode,
                    require_satisfied=True,
                    subject=f"{subject}.differential_evidence",
                    errors=errors,
                )
                if differential_path is not None and differential_path != reference_path:
                    pass
        elif differential_binding is not None:
            errors.append(_error("DIFFERENTIAL_EVIDENCE_FORBIDDEN", f"{subject}.differential_evidence", "pending differential cannot bind completed evidence"))
        if item.get("promotion_allowed") is not False:
            errors.append(_error("PROMOTION_FORBIDDEN", f"{subject}.promotion_allowed", "precalibration evidence cannot promote"))
        promoted += item.get("promotion_allowed") is True

    if matrix_ids != plan_ids:
        errors.append(_error("SCENARIO_DENOMINATOR_MISMATCH", "scenarios", "matrix scenario order and denominator must exactly match the plan"))
    if len(matrix_ids) != EXPECTED_SCENARIO_COUNT:
        errors.append(_error("SCENARIO_COUNT_INVALID", "scenarios", "exactly 25 required scenarios are expected"))

    recomputed_summary = {
        "required_scenarios": len(matrix_ids),
        "local_real_browser_green": local_real_browser_green,
        "local_measurement_green": local_measurement_green,
        "reference_characterized": reference_characterized,
        "differential_passed": differential_passed,
        "local_integration_passed": local_integration_passed,
        "local_recovery_contract_passed": local_recovery_contract_passed,
        "measurement_contract_passed": measurement_contract_passed,
        "evidence_satisfied": evidence_satisfied,
        "unresolved": len(matrix_ids) - evidence_satisfied,
        "promoted": promoted,
    }
    summary = matrix.get("summary")
    if _check_exact_fields(summary, EXPECTED_SUMMARY_FIELDS, "summary", errors):
        if summary != recomputed_summary:
            errors.append(_error("SUMMARY_MISMATCH", "summary", "summary does not equal recomputed scenario counts"))

    gates = matrix.get("cross_phase_gates")
    if _check_exact_fields(gates, EXPECTED_CROSS_PHASE_GATE_FIELDS, "cross_phase_gates", errors):
        for key, expected in STATIC_CROSS_PHASE_GATES.items():
            if gates.get(key) != expected:
                errors.append(_error("CROSS_PHASE_GATES_INVALID", f"cross_phase_gates.{key}", "cross-phase gate does not match the frozen policy"))
        expected_progress = "pending" if reference_characterized == 0 else f"in_progress_{reference_characterized}_of_{EXPECTED_SCENARIO_COUNT}"
        expected_diff_progress = "pending" if differential_passed == 0 else f"in_progress_{differential_passed}_of_{EXPECTED_SCENARIO_COUNT}"
        if gates.get("reference_characterization") != expected_progress:
            errors.append(_error("CROSS_PHASE_GATES_INVALID", "cross_phase_gates.reference_characterization", "reference progress does not match the matrix"))
        if gates.get("differential_evidence") != expected_diff_progress:
            errors.append(_error("CROSS_PHASE_GATES_INVALID", "cross_phase_gates.differential_evidence", "differential progress does not match the matrix"))
        expected_evidence_progress = "complete" if evidence_satisfied == EXPECTED_SCENARIO_COUNT else f"in_progress_{evidence_satisfied}_of_{EXPECTED_SCENARIO_COUNT}"
        if gates.get("scenario_evidence") != expected_evidence_progress:
            errors.append(_error("CROSS_PHASE_GATES_INVALID", "cross_phase_gates.scenario_evidence", "scenario evidence progress does not match the matrix"))
        expected_reference_access = reference_characterized > 0
        if gates.get("reference_workspace_accessed") is not expected_reference_access:
            errors.append(_error("CROSS_PHASE_GATES_INVALID", "cross_phase_gates.reference_workspace_accessed", "reference access flag does not match evidence"))
        expected_control = (
            "connected_synthetic_free_only"
            if reference_characterized > 0
            else "blocked_repeated_host_path_initialization_before_browser_selection"
        )
        if gates.get("safe_signed_in_browser_control") != expected_control:
            errors.append(_error("CROSS_PHASE_GATES_INVALID", "cross_phase_gates.safe_signed_in_browser_control", "browser control state does not match evidence"))
        if gates.get("personal_workspace_accessed") is not False:
            errors.append(_error("PERSONAL_WORKSPACE_FORBIDDEN", "cross_phase_gates.personal_workspace_accessed", "personal workspace access is forbidden"))
        if gates.get("evidence_pytest_host_gate") not in {
            "0xc0000409_repeated_after_last_green",
            "current_serial_targeted_green",
        }:
            errors.append(_error("CROSS_PHASE_GATES_INVALID", "cross_phase_gates.evidence_pytest_host_gate", "host gate state is invalid"))
    claims = matrix.get("completion_claims")
    expected_claims = {
        "all_required_local_contracts_green": local_real_browser_green + local_measurement_green == EXPECTED_SCENARIO_COUNT,
        "all_required_reference_characterized": reference_characterized == reference_applicable,
        "all_required_differential_passed": differential_passed == differential_applicable,
        "all_required_evidence_satisfied": evidence_satisfied == EXPECTED_SCENARIO_COUNT,
        "h8_complete": False,
        "h9_complete": False,
        "core_ui_complete": False,
        "overall_goal_complete": False,
    }
    if not _check_exact_fields(claims, EXPECTED_COMPLETION_CLAIM_FIELDS, "completion_claims", errors) or claims != expected_claims:
        errors.append(_error("COMPLETION_CLAIMS_INVALID", "completion_claims", "completion claims are not truthful for this snapshot"))

    report = {
        "contract": CONTRACT,
        "status": "fail" if errors else "pass",
        "completion_state": "unverified" if errors else "incomplete",
        "profile": matrix.get("profile"),
        "summary": recomputed_summary,
        "errors": errors,
    }
    return report, 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_completion_matrix(args.matrix, args.plan)
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
