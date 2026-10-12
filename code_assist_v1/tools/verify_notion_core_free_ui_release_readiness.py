"""Verify the fail-closed Core Free UI H8/H9 release-readiness snapshot.

This verifier is intentionally read-only.  It distinguishes completed local
development evidence from human review, operational recovery, and live rollout
authorization.  A green report therefore means that the pending boundary is
truthfully recorded; it never authorizes a feature flag or canary.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = (
    PROJECT_ROOT
    / "code_assist_v1"
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "release_readiness.v1.json"
)

CONTRACT = "code_assist.notion.core_free_ui.release_readiness.v1"
PROFILE = "free-owner-chrome-desktop-ko-KR"
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")

EXPECTED_INPUTS = {
    "completion_matrix": "code_assist.notion.core_free_ui.completion_matrix.v1",
    "evidence_review_gate": "code_assist.notion.core_free_ui.evidence_review_gate.v1",
    "performance_checkpoint": "code_assist.notion.core_free_ui.execution_checkpoint.v2",
    "isolated_h9_checkpoint": "code_assist.notion.core_free_ui_checkpoint.v2",
    "navigation_contract": None,
    "pilot_control": "code_assist.notion.core_free_ui.pilot_control.v1",
    "pilot_runbook": None,
}

EXPECTED_GATES = {
    "scenario_evidence": "passed_25_of_25",
    "performance_budget": "passed_isolated_local_precalibration",
    "isolated_h9_recovery": "passed_copy_on_write",
    "second_brain_hard_journeys": "passed_local",
    "formal_accessibility_review": "pending_human_review",
    "legal_brand_review": "pending_human_review",
    "production_default_off": "implemented_contract_tested_not_enabled",
    "operational_backup_restore": "isolated_rehearsal_only",
    "live_canary": "not_authorized_not_run",
}

EXPECTED_AUTHORIZATION = {
    "live_feature_enable_authorized": False,
    "live_canary_authorized": False,
    "operational_restore_authorized": False,
    "external_release_authorized": False,
}

EXPECTED_CLAIMS = {
    "development_evidence_complete": True,
    "release_ready": False,
    "h8_complete": False,
    "h9_complete": False,
    "core_ui_complete": False,
    "overall_goal_complete": False,
}


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonfinite,
    )
    if not isinstance(value, dict):
        raise ValueError("JSON document must be an object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _error(code: str, field: str, message: str) -> dict[str, str]:
    return {"code": code, "field": field, "message": message}


def _exact_keys(
    value: object,
    expected: set[str],
    field: str,
    errors: list[dict[str, str]],
) -> bool:
    if not isinstance(value, dict):
        errors.append(_error("OBJECT_REQUIRED", field, "value must be an object"))
        return False
    if set(value) != expected:
        errors.append(_error("FIELDS_INVALID", field, "object fields are not exact"))
        return False
    return True


def _safe_bound_path(
    raw: object,
    *,
    repo_root: Path,
    field: str,
    errors: list[dict[str, str]],
) -> Path | None:
    if (
        not isinstance(raw, str)
        or not raw.startswith("code_assist_v1/")
        or "\\" in raw
        or raw.startswith("/")
        or any(part in {"", ".", ".."} for part in raw.split("/"))
    ):
        errors.append(_error("PATH_INVALID", field, "path must be safe and repository-relative"))
        return None
    candidate = repo_root.joinpath(*raw.split("/"))
    try:
        resolved_root = repo_root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError:
        errors.append(_error("BOUND_FILE_MISSING", field, "bound file is missing"))
        return None
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        errors.append(_error("PATH_ESCAPE", field, "bound path escapes the repository"))
        return None
    current = resolved_root
    for part in resolved.relative_to(resolved_root).parts:
        current = current / part
        if current.is_symlink() or bool(getattr(current, "is_junction", lambda: False)()):
            errors.append(_error("PATH_LINK_FORBIDDEN", field, "bound path contains a link"))
            return None
    if not resolved.is_file():
        errors.append(_error("BOUND_FILE_MISSING", field, "bound path is not a file"))
        return None
    return resolved


def _load_binding(
    binding: object,
    *,
    repo_root: Path,
    field: str,
    expected_contract: str | None,
    errors: list[dict[str, str]],
) -> tuple[Path | None, dict[str, Any] | None]:
    if not _exact_keys(binding, {"path", "sha256"}, field, errors):
        return None, None
    assert isinstance(binding, dict)
    path = _safe_bound_path(
        binding.get("path"), repo_root=repo_root, field=f"{field}.path", errors=errors
    )
    expected_sha = binding.get("sha256")
    if not isinstance(expected_sha, str) or not SHA256_PATTERN.fullmatch(expected_sha):
        errors.append(_error("SHA256_INVALID", f"{field}.sha256", "SHA-256 is invalid"))
        return path, None
    if path is None:
        return None, None
    if _sha256(path) != expected_sha:
        errors.append(_error("SHA256_MISMATCH", f"{field}.sha256", "bound file hash drifted"))
        return path, None
    if expected_contract is None:
        return path, None
    try:
        payload = _load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(_error("BOUND_JSON_INVALID", field, str(exc)))
        return path, None
    if payload.get("contract") != expected_contract:
        errors.append(_error("BOUND_CONTRACT_INVALID", field, "bound contract is invalid"))
    return path, payload


def _validate_completion(value: dict[str, Any] | None, errors: list[dict[str, str]]) -> None:
    if value is None:
        return
    summary = value.get("summary")
    claims = value.get("completion_claims")
    if not isinstance(summary, dict) or (
        summary.get("required_scenarios") != 25
        or summary.get("evidence_satisfied") != 25
        or summary.get("unresolved") != 0
    ):
        errors.append(_error("SCENARIO_EVIDENCE_INCOMPLETE", "inputs.completion_matrix", "25-of-25 evidence is required"))
    required_claims = (
        "all_required_local_contracts_green",
        "all_required_reference_characterized",
        "all_required_differential_passed",
        "all_required_evidence_satisfied",
    )
    if not isinstance(claims, dict) or any(claims.get(key) is not True for key in required_claims):
        errors.append(_error("COMPLETION_CLAIMS_INCOMPLETE", "inputs.completion_matrix", "scenario completion claims are incomplete"))
    if isinstance(claims, dict) and any(
        claims.get(key) is not False
        for key in ("h8_complete", "h9_complete", "core_ui_complete", "overall_goal_complete")
    ):
        errors.append(_error("PREMATURE_COMPLETION", "inputs.completion_matrix", "release completion must remain false"))


def _validate_review(value: dict[str, Any] | None, errors: list[dict[str, str]]) -> None:
    if value is None:
        return
    categories = value.get("categories")
    claims = value.get("claims")
    if (
        not isinstance(categories, dict)
        or len(categories.get("certified", [])) != 25
        or any(categories.get(key) != [] for key in (
            "reference_observed_review_pending",
            "local_integration_review_pending",
            "reference_partially_observed",
        ))
        or not isinstance(claims, dict)
        or claims.get("certified_count") != 25
    ):
        errors.append(_error("OWNER_REVIEW_INCOMPLETE", "inputs.evidence_review_gate", "all 25 evidence contracts must be certified"))


def _validate_performance(value: dict[str, Any] | None, errors: list[dict[str, str]]) -> None:
    if value is None:
        return
    claims = value.get("claims")
    measurements = value.get("measurements")
    if (
        not isinstance(claims, dict)
        or claims.get("ui_perf_001_evidence_satisfied") is not True
        or claims.get("local_budget_approved") is not True
        or claims.get("current_local_budget_green") is not True
        or not isinstance(measurements, list)
        or len(measurements) != 2
        or any(item.get("exit_code") != 0 for item in measurements if isinstance(item, dict))
    ):
        errors.append(_error("PERFORMANCE_GATE_INCOMPLETE", "inputs.performance_checkpoint", "two green approved local-budget measurements are required"))


def _validate_h9(value: dict[str, Any] | None, errors: list[dict[str, str]]) -> None:
    if value is None:
        return
    claims = value.get("claims")
    runtime = value.get("runtime")
    if (
        not isinstance(claims, dict)
        or claims.get("h9_isolated_pilot_foundation_green") is not True
        or claims.get("h9_live_pilot_ready") is not False
        or claims.get("h9_complete") is not False
        or not isinstance(runtime, dict)
        or runtime.get("live_mutation") is not False
        or runtime.get("flags_unchanged") is not True
    ):
        errors.append(_error("ISOLATED_H9_GATE_INVALID", "inputs.isolated_h9_checkpoint", "isolated H9 must be green while live rollout remains false"))


def _validate_navigation(path: Path | None, errors: list[dict[str, str]]) -> None:
    if path is None:
        return
    try:
        value = _load_json(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(_error("NAVIGATION_CONTRACT_INVALID", "inputs.navigation_contract", str(exc)))
        return
    ui = value.get("ui_contract")
    implementation = value.get("implementation")
    if (
        value.get("name") != "np2b_server_backed_navigation_state"
        or not isinstance(ui, dict)
        or ui.get("implementation_status") != "implemented_contract_tested_default_off"
        or ui.get("default_enabled") is not False
        or ui.get("kill_switch_overrides_enable") is not True
        or ui.get("kill_switch_nonzero_or_unknown_value_fails_closed") is not True
        or not isinstance(implementation, dict)
        or implementation.get("ui_enabled") is not False
    ):
        errors.append(_error("NAVIGATION_DEFAULT_OFF_INVALID", "inputs.navigation_contract", "navigation capability is not safely default-off"))


def _validate_pilot_control(
    value: dict[str, Any] | None,
    errors: list[dict[str, str]],
) -> None:
    if value is None:
        return
    controls = value.get("controls")
    readiness = value.get("readiness")
    composition = value.get("composition")
    claims = value.get("claims")
    if (
        value.get("status") != "implemented_contract_tested_not_enabled"
        or not isinstance(controls, dict)
        or controls.get("default_enabled") is not False
        or controls.get("kill_switch_overrides_enable") is not True
        or controls.get("unknown_or_malformed_value_fails_closed") is not True
        or controls.get("nonempty_subject_allowlist_required") is not True
        or controls.get("nonempty_workspace_allowlist_required") is not True
        or controls.get("all_visible_workspaces_must_be_allowlisted") is not True
        or controls.get("workspace_substrate_readiness_required") is not True
        or not isinstance(readiness, dict)
        or readiness.get("authentication_required") is not True
        or readiness.get("identifier_free") is not True
        or readiness.get("allowlist_contents_exposed") is not False
        or readiness.get("disabled_preflight_catalog_io_count") != 0
        or readiness.get("deep_health_on_request") is not False
        or not isinstance(composition, dict)
        or composition.get("core_pilot_kill_switch_independent") is not True
        or composition.get("request_or_startup_migration_enabled") is not False
        or composition.get("live_configuration_changed") is not False
        or not isinstance(claims, dict)
        or claims.get("control_foundation_complete") is not True
        or claims.get("live_feature_enabled") is not False
        or claims.get("release_ready") is not False
    ):
        errors.append(
            _error(
                "PILOT_CONTROL_INVALID",
                "inputs.pilot_control",
                "pilot controls are not default-off and fail-closed",
            )
        )


def verify_release_readiness(
    snapshot_path: Path = DEFAULT_SNAPSHOT,
    *,
    repo_root: Path = PROJECT_ROOT,
) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    try:
        snapshot = _load_json(snapshot_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "contract": CONTRACT,
            "status": "fail",
            "release_state": "unverified",
            "errors": [_error("SNAPSHOT_READ_FAILED", "$", str(exc))],
        }
        return report, 1

    if not _exact_keys(
        snapshot,
        {"contract", "recorded_at", "profile", "status", "inputs", "gates", "authorization", "claims", "next_actions"},
        "$",
        errors,
    ):
        return {"contract": CONTRACT, "status": "fail", "release_state": "unverified", "errors": errors}, 1
    if snapshot.get("contract") != CONTRACT:
        errors.append(_error("CONTRACT_INVALID", "contract", "release-readiness contract is invalid"))
    if snapshot.get("profile") != PROFILE:
        errors.append(_error("PROFILE_INVALID", "profile", "release-readiness profile is invalid"))
    recorded_at = snapshot.get("recorded_at")
    try:
        parsed = datetime.fromisoformat(str(recorded_at).replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError("timezone required")
    except ValueError:
        errors.append(_error("RECORDED_AT_INVALID", "recorded_at", "recorded_at must be timezone-aware RFC 3339"))
    if snapshot.get("status") != "development_evidence_complete_release_pending":
        errors.append(_error("STATUS_INVALID", "status", "release status is invalid"))

    inputs = snapshot.get("inputs")
    loaded: dict[str, tuple[Path | None, dict[str, Any] | None]] = {}
    if _exact_keys(inputs, set(EXPECTED_INPUTS), "inputs", errors):
        assert isinstance(inputs, dict)
        for name, expected_contract in EXPECTED_INPUTS.items():
            loaded[name] = _load_binding(
                inputs.get(name),
                repo_root=repo_root,
                field=f"inputs.{name}",
                expected_contract=expected_contract,
                errors=errors,
            )

    _validate_completion(loaded.get("completion_matrix", (None, None))[1], errors)
    _validate_review(loaded.get("evidence_review_gate", (None, None))[1], errors)
    _validate_performance(loaded.get("performance_checkpoint", (None, None))[1], errors)
    _validate_h9(loaded.get("isolated_h9_checkpoint", (None, None))[1], errors)
    _validate_navigation(loaded.get("navigation_contract", (None, None))[0], errors)
    _validate_pilot_control(loaded.get("pilot_control", (None, None))[1], errors)

    if snapshot.get("gates") != EXPECTED_GATES:
        errors.append(_error("GATES_INVALID", "gates", "release gates do not match current evidence and approval boundaries"))
    if snapshot.get("authorization") != EXPECTED_AUTHORIZATION:
        errors.append(_error("AUTHORIZATION_INVALID", "authorization", "live authorization must remain explicitly false"))
    if snapshot.get("claims") != EXPECTED_CLAIMS:
        errors.append(_error("CLAIMS_INVALID", "claims", "release claims are not fail-closed"))
    next_actions = snapshot.get("next_actions")
    if (
        not isinstance(next_actions, list)
        or len(next_actions) != 4
        or any(not isinstance(item, str) or not item.strip() for item in next_actions)
    ):
        errors.append(_error("NEXT_ACTIONS_INVALID", "next_actions", "exactly four nonempty next actions are required"))

    report = {
        "contract": CONTRACT,
        "status": "fail" if errors else "pass",
        "release_state": "unverified" if errors else "pending_external_and_operational_gates",
        "profile": snapshot.get("profile"),
        "development_evidence_complete": not errors,
        "release_ready": False,
        "pending_gates": [
            key for key, value in EXPECTED_GATES.items() if value.startswith("pending_") or value in {
                "isolated_rehearsal_only",
                "not_authorized_not_run",
            }
        ],
        "errors": errors,
    }
    return report, 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_release_readiness(args.snapshot)
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
