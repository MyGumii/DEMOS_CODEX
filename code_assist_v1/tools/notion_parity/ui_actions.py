"""Deterministic semantic action contracts for Core Free Web UI evidence."""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Sequence

from code_assist_v1.tools.notion_parity.evidence import canonical_sha256
from code_assist_v1.tools.notion_parity.ui_redaction import (
    RedactionPolicy,
    validate_redacted_payload,
)


ACTION_LOG_SCHEMA_VERSION = 1
PROFILE_ID = "core_free_owner_chrome_desktop_ko_kr"
SCENARIO_RE = re.compile(r"^UI-[A-Z0-9]+(?:-[A-Z0-9]+)*-[0-9]{3}$")
ACTION_RE = re.compile(r"^UI-[A-Z0-9]+(?:-[A-Z0-9]+)*-A[0-9]{2}$")
ALIAS_RE = re.compile(r"^[A-Z][A-Z0-9_-]*$")
STATE_RE = re.compile(r"^STATE-[A-Z0-9]+(?:-[A-Z0-9]+)*$")
RUN_ID_RE = re.compile(r"^ui[0-9]+-[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_VERBS = frozenset(
    {
        "open_surface",
        "click",
        "press",
        "type_synthetic",
        "wait_visible",
        "assert_visible",
        "assert_hidden",
        "assert_focus",
        "assert_synthetic_text",
        "capture_region",
        "reload",
        "switch_workspace_alias",
        "select_synthetic",
        "drag",
    }
)
MUTATION_CLASSES = frozenset(
    {"read_only", "synthetic_mutation", "local_second_brain_mutation"}
)
ACTION_KEYS = frozenset(
    {
        "action_id",
        "scenario_id",
        "surface",
        "verb",
        "target_alias",
        "input_alias",
        "expected_state_id",
        "expected_focus_alias",
        "capture_after",
        "mutation_class",
        "recovery_action_id",
    }
)
LOG_KEYS = frozenset(
    {"action_log_schema_version", "scenario_id", "side", "profile_id", "run_id", "actions"}
)


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _sorted(errors: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    unique = {
        (item["code"], item["subject"], item["message"]): item
        for item in errors
    }
    return [unique[key] for key in sorted(unique)]


def _json_types_only(value: Any, subject: str, errors: list[dict[str, str]]) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            errors.append(_error("NON_FINITE_NUMBER", subject, "non-finite numbers are forbidden"))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _json_types_only(item, f"{subject}[{index}]", errors)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                errors.append(_error("KEY_TYPE", subject, "object keys must be strings"))
            _json_types_only(item, f"{subject}.{key}", errors)
        return
    errors.append(_error("JSON_TYPE", subject, "value is not JSON-compatible"))


def validate_action_sequence(
    actions: Any,
    *,
    scenario_id: str,
    synthetic_allowlist: Sequence[str] = (),
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not SCENARIO_RE.fullmatch(scenario_id):
        errors.append(_error("SCENARIO_ID", "$.scenario_id", "invalid UI scenario id"))
    if not isinstance(actions, list) or not actions:
        return _sorted(errors + [_error("ACTIONS", "$.actions", "actions must be a non-empty array")])
    seen: set[str] = set()
    for index, action in enumerate(actions):
        subject = f"$.actions[{index}]"
        if not isinstance(action, dict):
            errors.append(_error("ACTION_TYPE", subject, "action must be an object"))
            continue
        missing = sorted(ACTION_KEYS - set(action))
        extra = sorted(set(action) - ACTION_KEYS)
        for key in missing:
            errors.append(_error("FIELD_MISSING", f"{subject}.{key}", "required action field is missing"))
        for key in extra:
            errors.append(_error("UNKNOWN_FIELD", f"{subject}.{key}", "unknown action field"))
        action_id = action.get("action_id")
        if not isinstance(action_id, str) or not ACTION_RE.fullmatch(action_id):
            errors.append(_error("ACTION_ID", subject + ".action_id", "invalid action id"))
        elif action_id in seen:
            errors.append(_error("ACTION_DUPLICATE", subject + ".action_id", "action id must be unique"))
        if action.get("scenario_id") != scenario_id:
            errors.append(_error("ACTION_SCENARIO", subject + ".scenario_id", "action scenario must match the log"))
        surface = action.get("surface")
        if not isinstance(surface, str) or not re.fullmatch(r"^[a-z][a-z0-9_]*$", surface):
            errors.append(_error("SURFACE", subject + ".surface", "surface must be a semantic snake-case name"))
        if action.get("verb") not in ALLOWED_VERBS:
            errors.append(_error("VERB", subject + ".verb", "unsupported action verb"))
        for key in ("target_alias", "input_alias", "expected_focus_alias"):
            value = action.get(key)
            if value is not None and (not isinstance(value, str) or not ALIAS_RE.fullmatch(value)):
                errors.append(_error("ALIAS", f"{subject}.{key}", "expected a semantic alias or null"))
        expected_state = action.get("expected_state_id")
        if expected_state is not None and (not isinstance(expected_state, str) or not STATE_RE.fullmatch(expected_state)):
            errors.append(_error("STATE_ID", subject + ".expected_state_id", "invalid state id"))
        if not isinstance(action.get("capture_after"), bool):
            errors.append(_error("CAPTURE_FLAG", subject + ".capture_after", "capture_after must be boolean"))
        if action.get("mutation_class") not in MUTATION_CLASSES:
            errors.append(_error("MUTATION_CLASS", subject + ".mutation_class", "unsupported mutation class"))
        recovery = action.get("recovery_action_id")
        if recovery is not None and recovery not in seen:
            errors.append(_error("RECOVERY_FORWARD_REFERENCE", subject + ".recovery_action_id", "recovery must reference an earlier action"))
        if isinstance(action_id, str):
            seen.add(action_id)
    declared_surfaces = frozenset(
        action.get("surface")
        for action in actions
        if isinstance(action, dict) and isinstance(action.get("surface"), str)
    )
    redaction_policy = RedactionPolicy(
        allowed_keys=ACTION_KEYS,
        allowed_strings=(
            frozenset(synthetic_allowlist)
            | ALLOWED_VERBS
            | MUTATION_CLASSES
            | declared_surfaces
        ),
    )
    errors.extend(validate_redacted_payload(actions, policy=redaction_policy))
    _json_types_only(actions, "$.actions", errors)
    return _sorted(errors)


def build_action_log(
    *,
    scenario_id: str,
    side: str,
    run_id: str,
    actions: list[dict[str, Any]],
    synthetic_allowlist: Sequence[str] = (),
) -> dict[str, Any]:
    if side not in {"reference", "local"}:
        raise ValueError("side must be reference or local")
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("invalid synthetic run id")
    errors = validate_action_sequence(
        actions,
        scenario_id=scenario_id,
        synthetic_allowlist=synthetic_allowlist,
    )
    if errors:
        first = errors[0]
        raise ValueError(f"{first['code']}:{first['subject']}")
    return {
        "action_log_schema_version": ACTION_LOG_SCHEMA_VERSION,
        "scenario_id": scenario_id,
        "side": side,
        "profile_id": PROFILE_ID,
        "run_id": run_id,
        "actions": actions,
    }


def action_log_sha256(value: Any) -> str:
    return canonical_sha256(value)
