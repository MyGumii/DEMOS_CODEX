"""Strict, read-only verifier for the Core Free Web UI scenario overlay.

The verifier reads only explicitly selected JSON contracts and the local source
references named by the overlay.  It never imports the application, discovers
data roots, opens a browser, or mutates the filesystem.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys
from typing import Any, Iterable, Sequence

from code_assist_v1.tools.notion_parity.evidence import canonical_sha256
from code_assist_v1.tools.verify_notion_parity_catalog import (
    DuplicateJsonKeyError,
    JsonSchemaValidator,
    load_json,
)


REPORT_SCHEMA_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
DEFAULT_CONTRACT_DIR = PROJECT_ROOT / "docs" / "notion_parity" / "core_free_web_ui"
DEFAULT_SCHEMA = DEFAULT_CONTRACT_DIR / "scenario.schema.json"
DEFAULT_PLAN = DEFAULT_CONTRACT_DIR / "scenarios.v1.json"

PROFILE_ID = "core_free_owner_chrome_desktop_ko_kr"
EXPECTED_REQUIRED_SCENARIOS = (
    "UI-SHELL-001",
    "UI-SHELL-002",
    "UI-SHELL-003",
    "UI-TREE-001",
    "UI-TREE-002",
    "UI-TREE-003",
    "UI-EDITOR-001",
    "UI-EDITOR-002",
    "UI-EDITOR-003",
    "UI-EDITOR-004",
    "UI-EDITOR-005",
    "UI-EDITOR-006",
    "UI-DB-001",
    "UI-DB-002",
    "UI-DB-003",
    "UI-SEARCH-001",
    "UI-SEARCH-002",
    "UI-COLLAB-001",
    "UI-SHARE-001",
    "UI-SB-001",
    "UI-SB-002",
    "UI-SB-003",
    "UI-A11Y-001",
    "UI-RESP-001",
    "UI-PERF-001",
)
UI1_SCENARIOS = frozenset(
    {
        "UI-SHELL-002",
        "UI-SEARCH-001",
        "UI-EDITOR-001",
        "UI-SB-001",
        "UI-SB-002",
        "UI-A11Y-001",
    }
)
UI1_SEARCH_REQUIRED_TRACE = (
    ("click", "SEARCH-TRIGGER", None, "STATE-SEARCH-VISIBLE", "SEARCH-INPUT"),
    ("press", "SEARCH-INPUT", "ESCAPE", "STATE-SEARCH-HIDDEN", "SEARCH-TRIGGER"),
    ("click", "SEARCH-TRIGGER", None, "STATE-SEARCH-VISIBLE", "SEARCH-INPUT"),
    (
        "type_synthetic",
        "SEARCH-INPUT",
        "SYNTHETIC-KOREAN-TITLE",
        "STATE-SEARCH-RESULT-VISIBLE",
        "SEARCH-RESULT",
    ),
)
COUNT_KEYS = {
    "planned": "planned_count",
    "exists": "exists_count",
    "contract-tested": "contract_tested_count",
    "live-characterized": "live_characterized_count",
    "differential-passed": "differential_passed_count",
    "unknown": "unknown_count",
    "blocked": "blocked_count",
}
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
FORBIDDEN_ACTION_TEXT = (
    "css=",
    "xpath",
    "document.queryselector",
    "[data-",
    "notion.so/",
    "notion.com/",
)
SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")),
    ("BEARER", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    ("AUTH", re.compile(r"(?i)\b(?:authorization|cookie|set-cookie)\s*[:=]")),
    ("WINDOWS_PATH", re.compile(r"(?i)(?:\b[A-Z]:\\|\\\\)[^\r\n]+")),
    ("POSIX_HOME", re.compile(r"(?:^|[\s'\"])/(?:home|Users)/[^\s'\"]+")),
    ("SECRET_QUERY", re.compile(r"(?i)[?&](?:token|key|secret|signature|sig|code)=[^&#\s]{6,}")),
)


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _sorted_errors(errors: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    unique = {
        (item["code"], item["subject"], item["message"]): item
        for item in errors
    }
    return [unique[key] for key in sorted(unique)]


def _walk_strings(value: Any, subject: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield subject, value
    elif isinstance(value, dict):
        for key in sorted(value):
            yield from _walk_strings(value[key], f"{subject}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_strings(item, f"{subject}[{index}]")


def _safe_repository_path(value: str) -> bool:
    if not value or "\x00" in value or "\\" in value:
        return False
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    return not (
        windows.is_absolute()
        or bool(windows.drive)
        or posix.is_absolute()
        or any(part in {"", ".", ".."} for part in posix.parts)
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _validate_schema(schema: Any) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not isinstance(schema, dict):
        return [_error("SCHEMA_ROOT_INVALID", "schema", "schema root must be an object")]
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append(_error("SCHEMA_DIALECT_INVALID", "schema.$schema", "expected JSON Schema draft 2020-12"))
    if schema.get("$id") != "https://code-assist.local/schemas/notion-core-free-web-ui-v1.json":
        errors.append(_error("SCHEMA_ID_INVALID", "schema.$id", "unexpected schema identifier"))
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        errors.append(_error("SCHEMA_ROOT_INVALID", "schema", "schema root must be a closed object"))
    return errors


def _validate_sources(plan: dict[str, Any], errors: list[dict[str, str]]) -> None:
    for index, item in enumerate(plan.get("source_refs", [])):
        if not isinstance(item, dict):
            continue
        subject = f"$.source_refs[{index}]"
        relative = item.get("path")
        expected_hash = item.get("sha256")
        if not isinstance(relative, str) or not _safe_repository_path(relative):
            errors.append(_error("SOURCE_PATH_INVALID", subject + ".path", "source path must be a safe repository-relative path"))
            continue
        resolved = (REPOSITORY_ROOT / relative).resolve(strict=False)
        try:
            resolved.relative_to(REPOSITORY_ROOT.resolve())
        except ValueError:
            errors.append(_error("SOURCE_PATH_ESCAPE", subject + ".path", "source path escapes the repository"))
            continue
        if not resolved.is_file():
            errors.append(_error("SOURCE_MISSING", subject + ".path", "source file is missing"))
            continue
        actual_hash = _sha256_file(resolved)
        if not isinstance(expected_hash, str) or not SHA256_RE.fullmatch(expected_hash):
            errors.append(_error("SOURCE_HASH_INVALID", subject + ".sha256", "source hash must be canonical SHA-256"))
        elif expected_hash != actual_hash:
            errors.append(_error("SOURCE_HASH_MISMATCH", subject + ".sha256", "source hash does not match current file"))


def _validate_policies(plan: dict[str, Any], errors: list[dict[str, str]]) -> None:
    policies = plan.get("policy_refs", [])
    statuses: dict[str, str] = {}
    for index, item in enumerate(policies):
        if not isinstance(item, dict):
            continue
        relative = item.get("path")
        if not isinstance(relative, str) or not _safe_repository_path(relative):
            errors.append(_error("POLICY_PATH_INVALID", f"$.policy_refs[{index}].path", "policy path must be repository-relative"))
            continue
        resolved = REPOSITORY_ROOT / relative
        if not resolved.is_file():
            errors.append(_error("POLICY_MISSING", f"$.policy_refs[{index}].path", "policy file is missing"))
        statuses[relative] = str(item.get("status", ""))

    historical = "code_assist_v1/docs/notion_parity/policies/default_differential.json"
    precalibration = "code_assist_v1/docs/notion_parity/core_free_web_ui/policies/precalibration.v1.json"
    if statuses.get(historical) != "historical_not_adopted_for_core":
        errors.append(_error("HISTORICAL_POLICY_PROMOTED", "$.policy_refs", "historical 2px/500ms policy must not be adopted for Core UI"))
    if statuses.get(precalibration) != "active_measurement_only":
        errors.append(_error("PRECALIBRATION_POLICY_MISSING", "$.policy_refs", "active measurement-only policy is required"))
    else:
        try:
            policy = load_json(REPOSITORY_ROOT / precalibration)
        except (OSError, ValueError, DuplicateJsonKeyError) as exc:
            errors.append(_error("PRECALIBRATION_POLICY_INVALID", "$.policy_refs", f"policy could not be read: {type(exc).__name__}"))
        else:
            if not isinstance(policy, dict) or policy.get("state") != "precalibration":
                errors.append(_error("PRECALIBRATION_POLICY_INVALID", "$.policy_refs", "policy state must be precalibration"))
            if policy.get("geometry") != "measurement_only" or policy.get("latency") != "measurement_only":
                errors.append(_error("PRECALIBRATION_THRESHOLD_INVALID", "$.policy_refs", "geometry and latency must remain measurement-only"))
            if policy.get("pixel_identity_required") is not False or policy.get("brand_identity_compared") is not False:
                errors.append(_error("BRAND_COMPARISON_FORBIDDEN", "$.policy_refs", "pixel and brand identity comparison must remain disabled"))


def _validate_scenarios(plan: dict[str, Any], errors: list[dict[str, str]]) -> None:
    scenarios = plan.get("required_scenarios", [])
    if not isinstance(scenarios, list):
        return
    ids = [item.get("scenario_id") for item in scenarios if isinstance(item, dict)]
    if tuple(ids) != EXPECTED_REQUIRED_SCENARIOS:
        errors.append(_error("REQUIRED_DENOMINATOR_MISMATCH", "$.required_scenarios", "required scenarios must equal the ordered 25-scenario Core denominator"))
    duplicates = sorted(value for value, count in Counter(ids).items() if count > 1)
    for value in duplicates:
        errors.append(_error("SCENARIO_DUPLICATE", "$.required_scenarios", f"duplicate scenario: {value}"))
    if not UI1_SCENARIOS.issubset(set(ids)):
        errors.append(_error("UI1_SCENARIO_MISSING", "$.required_scenarios", "UI1 acceptance subset is incomplete"))

    global_action_ids: set[str] = set()
    state_counts: Counter[str] = Counter()
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            continue
        subject = f"$.required_scenarios[{index}]"
        scenario_id = scenario.get("scenario_id")
        if scenario.get("profile_id") != PROFILE_ID:
            errors.append(_error("PROFILE_SCOPE_INVALID", subject + ".profile_id", "required scenarios must use the fixed Free owner profile"))
        state = scenario.get("evidence_state")
        if isinstance(state, str):
            state_counts[state] += 1
        actions = scenario.get("actions", [])
        seen: set[str] = set()
        for action_index, action in enumerate(actions if isinstance(actions, list) else []):
            if not isinstance(action, dict):
                continue
            action_subject = f"{subject}.actions[{action_index}]"
            action_id = action.get("action_id")
            if action.get("scenario_id") != scenario_id:
                errors.append(_error("ACTION_SCENARIO_MISMATCH", action_subject + ".scenario_id", "action scenario_id must match its parent"))
            if not isinstance(action_id, str):
                continue
            if action_id in seen or action_id in global_action_ids:
                errors.append(_error("ACTION_ID_DUPLICATE", action_subject + ".action_id", "action_id must be globally unique"))
            recovery = action.get("recovery_action_id")
            if recovery is not None and recovery not in seen:
                errors.append(_error("RECOVERY_FORWARD_REFERENCE", action_subject + ".recovery_action_id", "recovery_action_id must reference an earlier action in the same scenario"))
            seen.add(action_id)
            global_action_ids.add(action_id)
            for field in ("target_alias", "input_alias"):
                value = action.get(field)
                if isinstance(value, str) and any(token in value.lower() for token in FORBIDDEN_ACTION_TEXT):
                    errors.append(_error("PRIVATE_SELECTOR_FORBIDDEN", action_subject + f".{field}", "actions must use semantic aliases, not private selectors or URLs"))

        if scenario_id == "UI-SEARCH-001" and isinstance(actions, list):
            trace = tuple(
                (
                    action.get("verb"),
                    action.get("target_alias"),
                    action.get("input_alias"),
                    action.get("expected_state_id"),
                    action.get("expected_focus_alias"),
                )
                for action in actions
                if isinstance(action, dict)
            )
            if trace != UI1_SEARCH_REQUIRED_TRACE:
                errors.append(
                    _error(
                        "UI1_SEARCH_FLOW_INVALID",
                        subject + ".actions",
                        "UI1 search must reopen after Escape before typing the synthetic query",
                    )
                )

        assertion_ids: list[str] = []
        for key in ("exact_assertions", "tolerance_assertions"):
            values = scenario.get(key, [])
            if isinstance(values, list):
                assertion_ids.extend(item.get("assertion_id") for item in values if isinstance(item, dict))
        for value, count in Counter(assertion_ids).items():
            if count > 1:
                errors.append(_error("ASSERTION_ID_DUPLICATE", subject, f"duplicate assertion id: {value}"))

    expected_counts = {
        "required_core_scenarios": len(scenarios),
        **{target: state_counts.get(state, 0) for state, target in COUNT_KEYS.items()},
        "extension_count": len(plan.get("extension_scenarios", [])),
        "conditional_count": len(plan.get("conditional_scenarios", [])),
    }
    declared = plan.get("generated_counts")
    if isinstance(declared, dict):
        for key, expected in expected_counts.items():
            if declared.get(key) != expected:
                errors.append(_error("GENERATED_COUNT_MISMATCH", f"$.generated_counts.{key}", f"expected recomputed value {expected}"))

    for list_name, expected_status in (("extension_scenarios", "extension"), ("conditional_scenarios", "conditional")):
        values = plan.get(list_name, [])
        ids_for_list: list[str] = []
        for index, item in enumerate(values if isinstance(values, list) else []):
            if not isinstance(item, dict):
                continue
            if item.get("status") != expected_status:
                errors.append(_error("DEFERRED_STATUS_INVALID", f"$.{list_name}[{index}].status", f"expected {expected_status}"))
            ids_for_list.append(str(item.get("scenario_id", "")))
        for value, count in Counter(ids_for_list).items():
            if count > 1:
                errors.append(_error("DEFERRED_SCENARIO_DUPLICATE", f"$.{list_name}", f"duplicate scenario: {value}"))


def validate_plan(plan: Any, schema: Any) -> list[dict[str, str]]:
    errors = _validate_schema(schema)
    if not errors:
        errors.extend(JsonSchemaValidator(schema).validate(plan))
    if not isinstance(plan, dict):
        return _sorted_errors(errors)

    profile = plan.get("primary_profile")
    expected_profile = {
        "profile_id": PROFILE_ID,
        "plan": "free",
        "role": "workspace_owner",
        "platform": "desktop_web",
        "locale": "ko-KR",
        "browser": "chrome",
        "reference_workspaces": ["REF-FREE-A", "REF-FREE-B"],
        "personal_workspace": "forbidden",
    }
    if profile != expected_profile:
        errors.append(_error("PRIMARY_PROFILE_INVALID", "$.primary_profile", "primary profile must be the exact Free owner Chrome desktop ko-KR contract"))

    _validate_scenarios(plan, errors)
    _validate_sources(plan, errors)
    _validate_policies(plan, errors)

    artifact = plan.get("artifact_contract")
    if isinstance(artifact, dict):
        required_forbidden = {"raw_dom", "raw_accessibility_tree", "network_log", "cookies", "browser_storage", "personal_labels"}
        if artifact.get("raw_artifacts_in_repository") is not False:
            errors.append(_error("RAW_ARTIFACT_POLICY_INVALID", "$.artifact_contract.raw_artifacts_in_repository", "raw artifacts must remain outside the repository"))
        if not required_forbidden.issubset(set(artifact.get("forbidden_artifacts", []))):
            errors.append(_error("FORBIDDEN_ARTIFACT_SET_INCOMPLETE", "$.artifact_contract.forbidden_artifacts", "artifact denylist is incomplete"))

    sensitive_scope = {
        "required_scenarios": plan.get("required_scenarios"),
        "extension_scenarios": plan.get("extension_scenarios"),
        "conditional_scenarios": plan.get("conditional_scenarios"),
        "excluded_capabilities": plan.get("excluded_capabilities"),
    }
    for subject, value in _walk_strings(sensitive_scope):
        for code, pattern in SENSITIVE_PATTERNS:
            if pattern.search(value):
                errors.append(_error(f"SENSITIVE_{code}", subject, "sensitive value is not allowed in the UI plan"))
    return _sorted_errors(errors)


def verify_plan(
    plan_path: str | Path = DEFAULT_PLAN,
    schema_path: str | Path = DEFAULT_SCHEMA,
) -> tuple[dict[str, Any], int]:
    plan: Any = None
    schema: Any = None
    errors: list[dict[str, str]] = []
    try:
        schema = load_json(schema_path)
    except (OSError, ValueError, DuplicateJsonKeyError) as exc:
        errors.append(_error("SCHEMA_READ_FAILED", "schema", f"schema could not be read: {type(exc).__name__}"))
    try:
        plan = load_json(plan_path)
    except (OSError, ValueError, DuplicateJsonKeyError) as exc:
        errors.append(_error("PLAN_READ_FAILED", "plan", f"plan could not be read: {type(exc).__name__}"))
    if not errors:
        errors = validate_plan(plan, schema)

    counts = plan.get("generated_counts") if isinstance(plan, dict) else None
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "status": "pass" if not errors else "fail",
        "plan_sha256": canonical_sha256(plan),
        "schema_sha256": canonical_sha256(schema),
        "required_scenario_count": len(plan.get("required_scenarios", [])) if isinstance(plan, dict) else 0,
        "ui1_scenario_count": len(UI1_SCENARIOS & {item.get("scenario_id") for item in plan.get("required_scenarios", []) if isinstance(item, dict)}) if isinstance(plan, dict) else 0,
        "generated_counts": counts,
        "error_count": len(errors),
        "errors": errors,
    }
    return report, 0 if not errors else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the Core Free Web UI scenario overlay")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_plan(args.plan, args.schema)
    json.dump(
        report,
        sys.stdout,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":") if args.compact else None,
        indent=None if args.compact else 2,
    )
    sys.stdout.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
