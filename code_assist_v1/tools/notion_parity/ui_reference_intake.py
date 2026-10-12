"""Fail-closed manual intake for synthetic Notion reference observations.

This module does not control a browser and never discovers profiles, tabs,
workspaces, or local data roots.  It creates an identifier-free worksheet and
validates a human-observed submission against the same C0, action, capture, and
redaction contracts used by automated reference evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any, Iterable

from code_assist_v1.tools.notion_parity.evidence import canonical_sha256, load_json
from code_assist_v1.tools.notion_parity.ui_actions import (
    ACTION_KEYS,
    LOG_KEYS,
    PROFILE_ID,
    action_log_sha256,
    build_action_log,
)
from code_assist_v1.tools.notion_parity.ui_capture import (
    CAPTURE_KEYS,
    PROFILE,
    RECT_KEYS,
    REDACTION_KEYS,
    SAFE_NESTED_KEYS,
    VIEWPORT_KEYS,
    validate_capture,
)
from code_assist_v1.tools.notion_parity.ui_redaction import (
    RedactionPolicy,
    validate_redacted_payload,
)
from code_assist_v1.tools.notion_parity.ui_reference_guard import (
    C0_CONTRACT,
    C0_DESCRIPTOR_KEYS,
    REFERENCE_WORKSPACES,
    ReferenceC0Decision,
    ReferenceC0Observation,
    authorize_reference_action,
    evaluate_reference_c0,
    reference_c0_decision_from_descriptor,
)
from code_assist_v1.tools.verify_notion_core_free_ui_plan import (
    DEFAULT_PLAN,
    DEFAULT_SCHEMA,
    verify_plan,
)


WORKSHEET_CONTRACT = "notion_core_free_ui_manual_reference_worksheet_v1"
SUBMISSION_CONTRACT = "notion_core_free_ui_manual_reference_submission_v1"
RECEIPT_CONTRACT = "notion_core_free_ui_manual_reference_receipt_v1"
WORKSHEET_SCHEMA_VERSION = 1
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
WORKSHEET_KEYS = frozenset(
    {
        "contract",
        "schema_version",
        "run_id",
        "profile",
        "scenario_id",
        "scenario_sha256",
        "c0_contract",
        "c0_must_pass",
        "workspace_aliases",
        "actions",
        "capture_after_action_ids",
        "required_capture_sections",
        "operator_attestation_fields",
        "forbidden",
        "promotion_allowed",
    }
)
SUBMISSION_KEYS = frozenset(
    {
        "contract",
        "worksheet_sha256",
        "c0_decision",
        "action_log",
        "captures",
        "operator_attestation",
    }
)
CAPTURE_ENTRY_KEYS = frozenset({"action_id", "capture"})
ATTESTATION_KEYS = frozenset(
    {
        "reviewer_alias",
        "recorded_at",
        "synthetic_workspace_only",
        "synthetic_fixture_confirmed",
        "personal_workspace_opened",
        "personal_page_opened",
        "raw_dom_captured",
        "network_log_captured",
        "cookies_or_storage_captured",
        "external_side_effect_attempted",
        "public_posting_attempted",
        "capture_reviewed_redacted",
    }
)
REQUIRED_CAPTURE_SECTIONS = (
    "state",
    "keyboard",
    "focus",
    "a11y",
    "semantic_visual",
    "error_recovery",
    "geometry",
    "latency",
)
FORBIDDEN = (
    "PERSONAL-WORKSPACE",
    "PERSONAL-PAGE",
    "RAW-DOM",
    "RAW-ACCESSIBILITY-TREE",
    "NETWORK-LOG",
    "COOKIES-OR-BROWSER-STORAGE",
    "PRIVATE-ENDPOINT",
    "INVITE",
    "PUBLIC-POSTING",
    "PLAN-CHANGE",
    "EXTERNAL-SIDE-EFFECT",
)


def _is_link_like(path: Path) -> bool:
    if path.is_symlink():
        return True
    if os.name != "nt":
        return False
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _trusted_temp_output_path(output_path: str | os.PathLike[str]) -> Path:
    raw = Path(output_path)
    if not raw.is_absolute() or raw.suffix.lower() != ".json":
        raise ReferenceIntakeError("output must be an absolute .json path below the OS temporary root")
    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    if _is_link_like(temp_root):
        raise ReferenceIntakeError("OS temporary root cannot be a link or reparse point")
    parent = raw.parent.resolve(strict=True)
    try:
        relative_parent = parent.relative_to(temp_root)
    except ValueError as exc:
        raise ReferenceIntakeError("output must remain below the OS temporary root") from exc
    current = temp_root
    for part in relative_parent.parts:
        current /= part
        if _is_link_like(current):
            raise ReferenceIntakeError("output parent cannot contain a link or reparse point")
    target = parent / raw.name
    if os.path.lexists(target):
        raise ReferenceIntakeError("output must be a new file")
    return target


def write_manual_reference_json(
    output_path: str | os.PathLike[str],
    payload: dict[str, Any],
) -> None:
    """Exclusively write canonical JSON below the trusted OS temporary root."""

    target = _trusted_temp_output_path(output_path)
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    with target.open("xb") as stream:
        stream.write(rendered)
        stream.flush()
        os.fsync(stream.fileno())


class ReferenceIntakeError(ValueError):
    pass


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _sorted(errors: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    unique = {
        (item["code"], item["subject"], item["message"]): item for item in errors
    }
    return [unique[key] for key in sorted(unique)]


def _scenario_strings(value: Any) -> frozenset[str]:
    strings: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, str):
            strings.add(item)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)

    visit(value)
    return frozenset(strings)


def _verified_scenario(scenario_id: str, plan_path: Path) -> dict[str, Any]:
    report, exit_code = verify_plan(plan_path, DEFAULT_SCHEMA)
    if exit_code:
        raise ReferenceIntakeError(
            f"scenario plan is not verified: {report.get('error_count', 'unknown')}"
        )
    plan = load_json(plan_path)
    scenarios = plan.get("required_scenarios") if isinstance(plan, dict) else None
    if not isinstance(scenarios, list):
        raise ReferenceIntakeError("scenario plan is invalid")
    matches = [
        item
        for item in scenarios
        if isinstance(item, dict) and item.get("scenario_id") == scenario_id
    ]
    if len(matches) != 1:
        raise ReferenceIntakeError("scenario is not in the required denominator")
    return matches[0]


def build_manual_reference_worksheet(
    *,
    scenario_id: str,
    run_id: str,
    plan_path: Path = DEFAULT_PLAN,
) -> dict[str, Any]:
    """Build an identifier-free operator worksheet from the verified plan."""

    scenario = _verified_scenario(scenario_id, plan_path)
    actions = scenario["actions"]
    workspace_aliases = sorted(
        alias
        for alias in scenario.get("fixture_aliases", [])
        if alias in REFERENCE_WORKSPACES
    )
    if not workspace_aliases:
        workspace_aliases = ["REF-FREE-A"]
    # build_action_log validates the run id and the exact action vocabulary.
    build_action_log(
        scenario_id=scenario_id,
        side="reference",
        run_id=run_id,
        actions=actions,
        synthetic_allowlist=tuple(_scenario_strings(scenario)),
    )
    worksheet = {
        "contract": WORKSHEET_CONTRACT,
        "schema_version": WORKSHEET_SCHEMA_VERSION,
        "run_id": run_id,
        "profile": PROFILE,
        "scenario_id": scenario_id,
        "scenario_sha256": canonical_sha256(scenario),
        "c0_contract": C0_CONTRACT,
        "c0_must_pass": True,
        "workspace_aliases": workspace_aliases,
        "actions": actions,
        "capture_after_action_ids": [
            action["action_id"] for action in actions if action["capture_after"]
        ],
        "required_capture_sections": list(REQUIRED_CAPTURE_SECTIONS),
        "operator_attestation_fields": sorted(ATTESTATION_KEYS),
        "forbidden": list(FORBIDDEN),
        "promotion_allowed": False,
    }
    if set(worksheet) != WORKSHEET_KEYS:
        raise AssertionError("worksheet contract drift")
    return worksheet


def build_manual_reference_submission_draft(
    *,
    scenario_id: str,
    run_id: str,
    c0_decision: Any,
    plan_path: Path = DEFAULT_PLAN,
) -> dict[str, Any]:
    """Build a deliberately non-acceptable draft after an exact passing C0."""

    decision = reference_c0_decision_from_descriptor(c0_decision)
    if decision.status != "pass" or decision.run_id != run_id:
        raise ReferenceIntakeError("a matching passing C0 decision is required")
    worksheet = build_manual_reference_worksheet(
        scenario_id=scenario_id,
        run_id=run_id,
        plan_path=plan_path,
    )
    action_log = build_action_log(
        scenario_id=scenario_id,
        side="reference",
        run_id=run_id,
        actions=worksheet["actions"],
        synthetic_allowlist=tuple(_scenario_strings(worksheet)),
    )
    captures = []
    for action_id in worksheet["capture_after_action_ids"]:
        captures.append(
            {
                "action_id": action_id,
                "capture": {
                    "capture_schema_version": 1,
                    "scenario_id": scenario_id,
                    "side": "reference",
                    "profile": PROFILE,
                    "viewport": {"width": None, "height": None, "scale": None},
                    "state": {},
                    "keyboard": {},
                    "focus": {},
                    "a11y": {},
                    "semantic_visual": {},
                    "error_recovery": {},
                    "geometry": {},
                    "latency_ms": None,
                    "action_log_sha256": action_log_sha256(action_log),
                    "screenshot_sha256": None,
                    "source_build_sha256": None,
                    "redaction": {"status": "pending", "rule_set": "core-free-ui-v1"},
                },
            }
        )
    return {
        "contract": SUBMISSION_CONTRACT,
        "worksheet_sha256": canonical_sha256(worksheet),
        "c0_decision": decision.descriptor(),
        "action_log": action_log,
        "captures": captures,
        "operator_attestation": {
            "reviewer_alias": "USER-OPERATOR",
            "recorded_at": None,
            "synthetic_workspace_only": True,
            "synthetic_fixture_confirmed": True,
            "personal_workspace_opened": False,
            "personal_page_opened": False,
            "raw_dom_captured": False,
            "network_log_captured": False,
            "cookies_or_storage_captured": False,
            "external_side_effect_attempted": False,
            "public_posting_attempted": False,
            "capture_reviewed_redacted": False,
        },
    }


def _workspace_for_action(
    action: dict[str, Any],
    worksheet: dict[str, Any],
) -> str:
    for key in ("target_alias", "input_alias"):
        value = action.get(key)
        if value in REFERENCE_WORKSPACES:
            return value
    aliases = worksheet["workspace_aliases"]
    return aliases[0]


def _attestation_errors(value: Any) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not isinstance(value, dict):
        return [_error("ATTESTATION_TYPE", "$.operator_attestation", "attestation must be an object")]
    for key in sorted(ATTESTATION_KEYS - set(value)):
        errors.append(_error("FIELD_MISSING", f"$.operator_attestation.{key}", "required field is missing"))
    for key in sorted(set(value) - ATTESTATION_KEYS):
        errors.append(_error("UNKNOWN_FIELD", f"$.operator_attestation.{key}", "unknown attestation field"))
    if value.get("reviewer_alias") != "USER-OPERATOR":
        errors.append(_error("REVIEWER_ALIAS", "$.operator_attestation.reviewer_alias", "reviewer alias must remain identifier-free"))
    recorded_at = value.get("recorded_at")
    if not isinstance(recorded_at, str) or not RFC3339_RE.fullmatch(recorded_at):
        errors.append(_error("RECORDED_AT", "$.operator_attestation.recorded_at", "recorded_at must be RFC 3339"))
    else:
        try:
            if datetime.fromisoformat(recorded_at.replace("Z", "+00:00")).utcoffset() is None:
                raise ValueError("timezone required")
        except ValueError:
            errors.append(_error("RECORDED_AT", "$.operator_attestation.recorded_at", "recorded_at must include a valid timezone"))
    expected = {
        "synthetic_workspace_only": True,
        "synthetic_fixture_confirmed": True,
        "personal_workspace_opened": False,
        "personal_page_opened": False,
        "raw_dom_captured": False,
        "network_log_captured": False,
        "cookies_or_storage_captured": False,
        "external_side_effect_attempted": False,
        "public_posting_attempted": False,
        "capture_reviewed_redacted": True,
    }
    for key, expected_value in expected.items():
        if type(value.get(key)) is not bool or value.get(key) is not expected_value:
            errors.append(_error("ATTESTATION_SAFETY", f"$.operator_attestation.{key}", f"{key} must be {str(expected_value).lower()}"))
    return _sorted(errors)


def validate_manual_reference_submission(
    submission: Any,
    *,
    plan_path: Path = DEFAULT_PLAN,
) -> list[dict[str, str]]:
    """Validate one human-observed submission without writing evidence."""

    errors: list[dict[str, str]] = []
    if not isinstance(submission, dict):
        return [_error("SUBMISSION_TYPE", "$", "submission must be an object")]
    for key in sorted(SUBMISSION_KEYS - set(submission)):
        errors.append(_error("FIELD_MISSING", f"$.{key}", "required field is missing"))
    for key in sorted(set(submission) - SUBMISSION_KEYS):
        errors.append(_error("UNKNOWN_FIELD", f"$.{key}", "unknown submission field"))
    if errors:
        return _sorted(errors)
    if submission.get("contract") != SUBMISSION_CONTRACT:
        errors.append(_error("CONTRACT", "$.contract", "submission contract is invalid"))

    decision: ReferenceC0Decision | None = None
    try:
        decision = reference_c0_decision_from_descriptor(submission.get("c0_decision"))
    except (TypeError, ValueError) as exc:
        errors.append(_error("C0_INVALID", "$.c0_decision", str(exc)))
    if decision is not None and (
        decision.status != "pass"
        or not decision.capture_allowed
        or not decision.synthetic_workspace_entry_allowed
    ):
        errors.append(_error("C0_NOT_PASSED", "$.c0_decision", "C0 must pass before submission"))

    action_log = submission.get("action_log")
    captures = submission.get("captures")
    scenario_id = action_log.get("scenario_id") if isinstance(action_log, dict) else None
    run_id = action_log.get("run_id") if isinstance(action_log, dict) else None
    scenario: dict[str, Any] | None = None
    worksheet: dict[str, Any] | None = None
    if not isinstance(scenario_id, str) or not isinstance(run_id, str):
        errors.append(_error("ACTION_LOG_IDENTITY", "$.action_log", "action log identity is invalid"))
    else:
        try:
            scenario = _verified_scenario(scenario_id, plan_path)
            worksheet = build_manual_reference_worksheet(
                scenario_id=scenario_id,
                run_id=run_id,
                plan_path=plan_path,
            )
        except (ReferenceIntakeError, TypeError, ValueError) as exc:
            errors.append(_error("WORKSHEET_INVALID", "$.action_log", str(exc)))

    if decision is not None and isinstance(run_id, str) and decision.run_id != run_id:
        errors.append(_error("C0_RUN_MISMATCH", "$.c0_decision.run_id", "C0 and action log runs differ"))
    if worksheet is not None:
        if submission.get("worksheet_sha256") != canonical_sha256(worksheet):
            errors.append(_error("WORKSHEET_HASH", "$.worksheet_sha256", "worksheet hash does not match"))
        expected_log = build_action_log(
            scenario_id=scenario_id,
            side="reference",
            run_id=run_id,
            actions=scenario["actions"],
            synthetic_allowlist=tuple(_scenario_strings(scenario)),
        )
        if action_log != expected_log:
            errors.append(_error("ACTION_LOG_DRIFT", "$.action_log", "action log must exactly match the verified plan"))
        if decision is not None:
            for action in scenario["actions"]:
                authorization = authorize_reference_action(
                    decision,
                    scenario_id=scenario_id,
                    action_id=action["action_id"],
                    workspace_alias=_workspace_for_action(action, worksheet),
                    mutation_class=action["mutation_class"],
                    synthetic_fixture_confirmed=True,
                )
                if not authorization.allowed:
                    errors.append(_error("ACTION_NOT_AUTHORIZED", f"$.action_log.actions.{action['action_id']}", authorization.reason_code))

    expected_capture_ids = (
        worksheet["capture_after_action_ids"] if worksheet is not None else []
    )
    capture_builds: set[str] = set()
    if not isinstance(captures, list):
        errors.append(_error("CAPTURES_TYPE", "$.captures", "captures must be an array"))
        captures = []
    actual_capture_ids: list[str] = []
    for index, entry in enumerate(captures):
        subject = f"$.captures[{index}]"
        if not isinstance(entry, dict):
            errors.append(_error("CAPTURE_ENTRY_TYPE", subject, "capture entry must be an object"))
            continue
        for key in sorted(CAPTURE_ENTRY_KEYS - set(entry)):
            errors.append(_error("FIELD_MISSING", f"{subject}.{key}", "required capture entry field is missing"))
        for key in sorted(set(entry) - CAPTURE_ENTRY_KEYS):
            errors.append(_error("UNKNOWN_FIELD", f"{subject}.{key}", "unknown capture entry field"))
        action_id = entry.get("action_id")
        if isinstance(action_id, str):
            actual_capture_ids.append(action_id)
        else:
            errors.append(_error("CAPTURE_ACTION_ID", f"{subject}.action_id", "capture action id must be text"))
        capture = entry.get("capture")
        if not isinstance(capture, dict):
            errors.append(_error("CAPTURE_TYPE", f"{subject}.capture", "capture must be an object"))
            continue
        allowlist = tuple(_scenario_strings(scenario)) if scenario is not None else ()
        for item in validate_capture(capture, synthetic_allowlist=allowlist):
            errors.append(_error("CAPTURE_" + item["code"], f"{subject}.capture" + item["subject"][1:], item["message"]))
        if capture.get("side") != "reference":
            errors.append(_error("CAPTURE_SIDE", f"{subject}.capture.side", "manual intake accepts reference captures only"))
        if scenario_id is not None and capture.get("scenario_id") != scenario_id:
            errors.append(_error("CAPTURE_SCENARIO", f"{subject}.capture.scenario_id", "capture scenario differs from action log"))
        if isinstance(action_log, dict) and capture.get("action_log_sha256") != action_log_sha256(action_log):
            errors.append(_error("CAPTURE_ACTION_HASH", f"{subject}.capture.action_log_sha256", "capture does not bind the action log"))
        source_build = capture.get("source_build_sha256")
        if isinstance(source_build, str):
            capture_builds.add(source_build)
    if actual_capture_ids != expected_capture_ids:
        errors.append(_error("CAPTURE_DENOMINATOR", "$.captures", "capture entries must exactly match ordered capture_after actions"))
    if len(capture_builds) > 1:
        errors.append(_error("CAPTURE_BUILD_DRIFT", "$.captures", "one reference scenario cannot mix source builds"))

    errors.extend(_attestation_errors(submission.get("operator_attestation")))
    redaction_keys = (
        SUBMISSION_KEYS
        | C0_DESCRIPTOR_KEYS
        | ACTION_KEYS
        | LOG_KEYS
        | CAPTURE_ENTRY_KEYS
        | CAPTURE_KEYS
        | VIEWPORT_KEYS
        | RECT_KEYS
        | REDACTION_KEYS
        | SAFE_NESTED_KEYS
        | ATTESTATION_KEYS
        | frozenset(
            {
                "action_log_schema_version",
                "profile_id",
                "run_id",
                "actions",
                "viewport",
                "scale",
                "redaction",
                "rule_set",
                "raw_label_disclosed",
                "phase",
                "failure_codes",
                "connection_verified",
                "workspace_boundary_verified",
                "read_only_verified",
                "capture_allowed",
                "synthetic_workspace_entry_allowed",
                "other_workspace_labels_visible",
                "artifact_created",
                "mutation_attempted",
                "allowed",
                "reason_code",
            }
        )
    )
    allowed_strings = set(_scenario_strings(scenario)) if scenario is not None else set()
    allowed_strings.update(
        {
            SUBMISSION_CONTRACT,
            C0_CONTRACT,
            PROFILE,
            PROFILE_ID,
            "USER-OPERATOR",
            "C0",
        }
    )
    attestation = submission.get("operator_attestation")
    recorded_at = attestation.get("recorded_at") if isinstance(attestation, dict) else None
    if isinstance(recorded_at, str) and RFC3339_RE.fullmatch(recorded_at):
        allowed_strings.add(recorded_at)
    errors.extend(
        validate_redacted_payload(
            submission,
            policy=RedactionPolicy(
                allowed_keys=frozenset(redaction_keys),
                allowed_strings=frozenset(allowed_strings),
            ),
        )
    )
    return _sorted(errors)


def accept_manual_reference_submission(
    submission: Any,
    *,
    plan_path: Path = DEFAULT_PLAN,
) -> dict[str, Any]:
    errors = validate_manual_reference_submission(submission, plan_path=plan_path)
    if errors:
        first = errors[0]
        raise ReferenceIntakeError(f"{first['code']}:{first['subject']}")
    decision = reference_c0_decision_from_descriptor(submission["c0_decision"])
    action_log = submission["action_log"]
    captures = submission["captures"]
    return {
        "contract": RECEIPT_CONTRACT,
        "status": "accepted_reference_observation",
        "run_id": action_log["run_id"],
        "scenario_id": action_log["scenario_id"],
        "profile": PROFILE,
        "c0_sha256": decision.sha256(),
        "worksheet_sha256": submission["worksheet_sha256"],
        "action_log_sha256": action_log_sha256(action_log),
        "captures_sha256": canonical_sha256(captures),
        "capture_count": len(captures),
        "screenshot_count": sum(
            entry["capture"]["screenshot_sha256"] is not None for entry in captures
        ),
        "personal_workspace_accessed": False,
        "personal_page_accessed": False,
        "external_side_effect_attempted": False,
        "path_disclosed": False,
        "promotion_allowed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    worksheet_parser = subparsers.add_parser("worksheet")
    worksheet_parser.add_argument("--scenario", required=True)
    worksheet_parser.add_argument("--run-id", required=True)
    worksheet_parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    worksheet_parser.add_argument("--output", type=Path)
    c0_parser = subparsers.add_parser("c0")
    c0_parser.add_argument("--run-id", required=True)
    c0_parser.add_argument("--browser-connection-visible", action="store_true")
    c0_parser.add_argument("--chrome-session-confirmed", action="store_true")
    c0_parser.add_argument("--signed-in-visible", action="store_true")
    c0_parser.add_argument("--workspace-switcher-opened", action="store_true")
    c0_parser.add_argument("--ref-free-a-confirmed", action="store_true")
    c0_parser.add_argument("--ref-free-b-confirmed", action="store_true")
    c0_parser.add_argument("--other-workspace-labels-visible", action="store_true")
    c0_parser.add_argument("--personal-workspace-opened", action="store_true")
    c0_parser.add_argument("--personal-page-opened", action="store_true")
    c0_parser.add_argument("--unknown-label-captured", action="store_true")
    c0_parser.add_argument("--screenshot-created", action="store_true")
    c0_parser.add_argument("--raw-artifact-created", action="store_true")
    c0_parser.add_argument("--mutation-attempted", action="store_true")
    c0_parser.add_argument("--external-side-effect-attempted", action="store_true")
    c0_parser.add_argument("--output", type=Path)
    draft_parser = subparsers.add_parser("draft")
    draft_parser.add_argument("c0_decision", type=Path)
    draft_parser.add_argument("--scenario", required=True)
    draft_parser.add_argument("--run-id", required=True)
    draft_parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    draft_parser.add_argument("--synthetic-only-confirmed", action="store_true")
    draft_parser.add_argument("--output", type=Path)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("submission", type=Path)
    verify_parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    verify_parser.add_argument("--synthetic-only-confirmed", action="store_true")
    verify_parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "worksheet":
            output = build_manual_reference_worksheet(
                scenario_id=args.scenario,
                run_id=args.run_id,
                plan_path=args.plan,
            )
            exit_code = 0
        elif args.command == "c0":
            decision = evaluate_reference_c0(
                ReferenceC0Observation(
                    run_id=args.run_id,
                    browser_connection_visible=args.browser_connection_visible,
                    chrome_session_confirmed=args.chrome_session_confirmed,
                    signed_in_visible=args.signed_in_visible,
                    workspace_switcher_opened=args.workspace_switcher_opened,
                    ref_free_a_confirmed=args.ref_free_a_confirmed,
                    ref_free_b_confirmed=args.ref_free_b_confirmed,
                    other_workspace_labels_visible=args.other_workspace_labels_visible,
                    personal_workspace_opened=args.personal_workspace_opened,
                    personal_page_opened=args.personal_page_opened,
                    unknown_label_captured=args.unknown_label_captured,
                    screenshot_created=args.screenshot_created,
                    raw_artifact_created=args.raw_artifact_created,
                    mutation_attempted=args.mutation_attempted,
                    external_side_effect_attempted=args.external_side_effect_attempted,
                )
            )
            output = decision.descriptor()
            exit_code = 0 if decision.status == "pass" else 1
        elif args.command == "draft":
            if not args.synthetic_only_confirmed:
                raise ReferenceIntakeError(
                    "synthetic-only confirmation is required before reading a C0 decision"
                )
            output = build_manual_reference_submission_draft(
                scenario_id=args.scenario,
                run_id=args.run_id,
                c0_decision=load_json(args.c0_decision),
                plan_path=args.plan,
            )
            exit_code = 0
        else:
            if not args.synthetic_only_confirmed:
                raise ReferenceIntakeError(
                    "synthetic-only confirmation is required before reading a submission"
                )
            output = accept_manual_reference_submission(
                load_json(args.submission),
                plan_path=args.plan,
            )
            exit_code = 0
        if args.output is not None:
            write_manual_reference_json(args.output, output)
    except (OSError, UnicodeError, ValueError, ReferenceIntakeError) as exc:
        output = {
            "contract": RECEIPT_CONTRACT,
            "status": "rejected",
            "error": str(exc),
        }
        exit_code = 1
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "RECEIPT_CONTRACT",
    "SUBMISSION_CONTRACT",
    "WORKSHEET_CONTRACT",
    "ReferenceIntakeError",
    "accept_manual_reference_submission",
    "build_manual_reference_submission_draft",
    "build_manual_reference_worksheet",
    "write_manual_reference_json",
    "validate_manual_reference_submission",
]
