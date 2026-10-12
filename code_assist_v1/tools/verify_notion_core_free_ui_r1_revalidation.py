"""Verify current R1 captures against explicitly observed reference fields."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from code_assist_v1.tools.notion_parity.evidence import load_json
from code_assist_v1.tools.notion_parity.ui_capture import capture_sha256, validate_capture
from code_assist_v1.tools.verify_notion_core_free_ui_completion import (
    EVIDENCE_CAPTURE_ALLOWLIST,
    PROFILE,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAPTURE = PACKAGE_ROOT / "docs" / "notion_parity" / "core_free_web_ui" / "R1_CURRENT_SOURCE_CAPTURE_20260822.json"
COMPLETION_OBSERVATION = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "reference_completion_observation.r1_r3.20260822.json"
)
CHECKPOINT_ROOT = PACKAGE_ROOT / "docs" / "notion_parity" / "checkpoints"
REFERENCE_CHECKPOINTS = {
    "UI-DB-001": CHECKPOINT_ROOT / "ui-db-001-reference-observed-review-pending-20260821.json",
    "UI-DB-002": CHECKPOINT_ROOT / "ui-db-002-reference-observed-review-pending-20260822.json",
    "UI-DB-003": CHECKPOINT_ROOT / "ui-db-003-reference-observed-review-pending-20260822.json",
    "UI-SEARCH-001": CHECKPOINT_ROOT / "ui-search-001-reference-observed-review-pending-20260822.json",
    "UI-SEARCH-002": CHECKPOINT_ROOT / "ui-search-002-reference-observed-review-pending-20260822.json",
    "UI-COLLAB-001": CHECKPOINT_ROOT / "ui-collab-001-reference-observed-review-pending-20260822.json",
    "UI-SHARE-001": CHECKPOINT_ROOT / "ui-share-001-reference-observed-review-pending-20260822.json",
}
OBSERVABLE_FIELDS = ("state", "keyboard", "focus", "a11y", "semantic_visual", "error_recovery")
R1_CAPTURE_ALLOWLIST = EVIDENCE_CAPTURE_ALLOWLIST + (
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


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _verify_search_002_observations(
    checkpoint: dict[str, Any],
    captures: dict[str, Any],
    errors: list[dict[str, str]],
    supplement: dict[str, Any] | None = None,
) -> tuple[int, dict[str, list[str]]]:
    """Map the approved find/backlink observation contract without inventing captures."""

    expected = {"UI-SEARCH-002-A01", "UI-SEARCH-002-A02"}
    if set(captures) != expected:
        errors.append(_error("REFERENCE_ACTION_SET", "UI-SEARCH-002", "local action set does not match the approved observation contract"))
        return 0, {}
    find = checkpoint.get("reference_find_observation")
    backlink = checkpoint.get("reference_backlink_observation")
    if not isinstance(find, dict) or not isinstance(backlink, dict):
        errors.append(_error("REFERENCE_OBSERVATION", "UI-SEARCH-002", "approved find/backlink observations are missing"))
        return 0, {}

    if supplement:
        expected_fields = {
            "UI-SEARCH-002-A01": {
                "state": {"count": 1, "result": "pass"},
                "keyboard": {"result": "pass"},
                "focus": {"sequence": ["PAGE-FIND-INPUT"]},
                "a11y": supplement.get("UI-SEARCH-002-A01", {}).get("a11y"),
                "semantic_visual": {"region": "PAGE-FIND", "state": "visible"},
                "error_recovery": {"result": "pass"},
            },
            "UI-SEARCH-002-A02": {
                "state": {"count": backlink.get("backlink_count"), "result": "pass"},
                "keyboard": supplement.get("UI-SEARCH-002-A02", {}).get("keyboard"),
                "focus": supplement.get("UI-SEARCH-002-A02", {}).get("focus"),
                "a11y": supplement.get("UI-SEARCH-002-A02", {}).get("a11y"),
                "semantic_visual": {"region": "SYNTHETIC-BACKLINK", "state": "visible"},
                "error_recovery": supplement.get("UI-SEARCH-002-A02", {}).get("error_recovery"),
            },
        }
        if not (
            find.get("ctrl_f_surface") == "notion_page_find"
            and find.get("ctrl_f_surface_is_chrome_native") is False
            and find.get("escape_restored_prior_focus_group") is True
            and backlink.get("source_and_target_were_synthetic") is True
            and backlink.get("source_backlink_opened_exact_source") is True
            and backlink.get("permanent_delete_used") is False
        ):
            errors.append(_error("REFERENCE_OBSERVATION", "UI-SEARCH-002", "approved find/backlink observations are incomplete"))
        for action_id, fields in expected_fields.items():
            for field, expected_value in fields.items():
                if expected_value is None or captures[action_id].get(field) != expected_value:
                    errors.append(_error("OBSERVED_FIELD_MISMATCH", f"{action_id}.{field}", "current local behavior differs from the completed find/backlink observation"))
        return 12, {"UI-SEARCH-002-A01": [], "UI-SEARCH-002-A02": []}

    local_find = captures["UI-SEARCH-002-A01"]
    local_backlink = captures["UI-SEARCH-002-A02"]
    checks = (
        (find.get("ctrl_f_surface") == "notion_page_find" and find.get("ctrl_f_surface_is_chrome_native") is False, True, "reference_find_surface"),
        (local_find.get("state", {}).get("result"), "pass", "UI-SEARCH-002-A01.state"),
        (local_find.get("keyboard", {}).get("result"), "pass", "UI-SEARCH-002-A01.keyboard"),
        (local_find.get("focus"), {"sequence": ["PAGE-FIND-INPUT"]}, "UI-SEARCH-002-A01.focus"),
        (local_find.get("semantic_visual"), {"region": "PAGE-FIND", "state": "visible"}, "UI-SEARCH-002-A01.semantic_visual"),
        (find.get("escape_restored_prior_focus_group"), True, "reference_find_escape"),
        (local_backlink.get("state"), {"count": backlink.get("backlink_count"), "result": "pass"}, "UI-SEARCH-002-A02.state"),
        (local_backlink.get("semantic_visual"), {"region": "SYNTHETIC-BACKLINK", "state": "visible"}, "UI-SEARCH-002-A02.semantic_visual"),
    )
    for actual, expected_value, subject in checks:
        if actual != expected_value:
            errors.append(_error("OBSERVED_FIELD_MISMATCH", subject, "current local behavior differs from the approved find/backlink observation"))
    return len(checks), {
        "UI-SEARCH-002-A01": ["a11y"],
        "UI-SEARCH-002-A02": ["keyboard", "focus", "a11y", "error_recovery"],
    }


def _completion_supplements() -> dict[str, Any]:
    if not COMPLETION_OBSERVATION.is_file():
        return {}
    try:
        observation = load_json(COMPLETION_OBSERVATION)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return {}
    if not (
        observation.get("contract") == "code_assist.notion.core_free_ui.reference_completion_observation.v1"
        and observation.get("profile") == PROFILE
        and observation.get("claims", {}).get("normalized_evidence_sealed") is True
        and observation.get("claims", {}).get("all_differentials_zero") is True
        and observation.get("cleanup", {}).get("status") == "complete"
        and observation.get("cleanup", {}).get("permanent_delete_used") is False
    ):
        return {}
    supplements = observation.get("supplemental_observable_fields")
    return supplements if isinstance(supplements, dict) else {}


def verify(capture_path: Path = DEFAULT_CAPTURE) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    try:
        artifact = load_json(capture_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "fail", "errors": [_error("CAPTURE_READ_FAILED", "$", str(exc))]}, 1
    expected_top = {"contract", "isolated_data_root", "profile", "recorded_at", "repeatability", "scenarios", "synthetic_only"}
    if set(artifact) != expected_top:
        errors.append(_error("CAPTURE_SHAPE", "$", "capture fields must match the frozen contract"))
    if artifact.get("contract") != "code_assist.notion.core_free_ui.r1_current_capture.v1":
        errors.append(_error("CAPTURE_CONTRACT", "$.contract", "capture contract mismatch"))
    if artifact.get("profile") != PROFILE:
        errors.append(_error("CAPTURE_PROFILE", "$.profile", "profile mismatch"))
    if artifact.get("synthetic_only") is not True or artifact.get("isolated_data_root") is not True:
        errors.append(_error("CAPTURE_BOUNDARY", "$", "synthetic isolated capture required"))
    scenarios = artifact.get("scenarios")
    if not isinstance(scenarios, dict) or set(scenarios) != set(REFERENCE_CHECKPOINTS):
        errors.append(_error("SCENARIO_SET", "$.scenarios", "exact R1 scenario set required"))
        scenarios = {}

    compared_fields = 0
    missing_reference_fields: dict[str, dict[str, list[str]]] = {}
    supplements = _completion_supplements()
    for scenario_id, checkpoint_path in REFERENCE_CHECKPOINTS.items():
        scenario = scenarios.get(scenario_id)
        if not isinstance(scenario, dict):
            errors.append(_error("SCENARIO_MISSING", scenario_id, "scenario capture missing"))
            continue
        captures = scenario.get("action_captures")
        hashes = scenario.get("action_capture_sha256")
        if not isinstance(captures, dict) or not isinstance(hashes, dict) or set(captures) != set(hashes):
            errors.append(_error("ACTION_SET", scenario_id, "capture/hash action sets must match"))
            continue
        for action_id, capture in captures.items():
            for item in validate_capture(capture, synthetic_allowlist=R1_CAPTURE_ALLOWLIST):
                errors.append(_error("CAPTURE_INVALID", f"{scenario_id}.{action_id}:{item['subject']}", item["code"]))
            if hashes.get(action_id) != capture_sha256(capture):
                errors.append(_error("CAPTURE_HASH", f"{scenario_id}.{action_id}", "capture hash mismatch"))

        checkpoint = load_json(checkpoint_path)
        reference = checkpoint.get("reference_action_captures")
        if scenario_id == "UI-SEARCH-002" and not isinstance(reference, dict):
            compared, missing = _verify_search_002_observations(
                checkpoint,
                captures,
                errors,
                supplements.get(scenario_id),
            )
            compared_fields += compared
            missing_reference_fields[scenario_id] = missing
            continue
        if not isinstance(reference, dict) or set(reference) != set(captures):
            errors.append(_error("REFERENCE_ACTION_SET", scenario_id, "reference action set mismatch"))
            continue
        missing_reference_fields[scenario_id] = {}
        for action_id, capture in captures.items():
            observed_reference = dict(reference[action_id])
            completed = supplements.get(scenario_id, {}).get(action_id, {})
            if isinstance(completed, dict):
                for field, value in completed.items():
                    if field not in observed_reference:
                        observed_reference[field] = value
            missing_reference_fields[scenario_id][action_id] = [field for field in OBSERVABLE_FIELDS if field not in observed_reference]
            for field in OBSERVABLE_FIELDS:
                if field not in observed_reference:
                    continue
                compared_fields += 1
                if capture.get(field) != observed_reference[field]:
                    errors.append(_error("OBSERVED_FIELD_MISMATCH", f"{scenario_id}.{action_id}.{field}", "current local capture differs from the approved reference observation"))

    differential_ready = not errors and all(
        not fields
        for actions in missing_reference_fields.values()
        for fields in actions.values()
    )
    report = {
        "contract": "code_assist.notion.core_free_ui.r1_revalidation_report.v1",
        "status": "pass" if not errors else "fail",
        "profile": PROFILE,
        "scenario_count": len(REFERENCE_CHECKPOINTS),
        "compared_observed_field_count": compared_fields,
        "observed_fields_match": not errors,
        "missing_reference_fields": missing_reference_fields,
        "differential_ready": differential_ready,
        "remaining_gate": "none" if differential_ready else "repeat the approved synthetic reference actions and capture every normalized field before creating differential packets",
        "errors": errors,
    }
    return report, 0 if not errors else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    report, exit_code = verify(args.capture)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":") if args.compact else None))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
