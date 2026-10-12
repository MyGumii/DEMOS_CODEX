"""Fail-closed validation for redacted NP-0 evidence manifests.

This module reads only the explicitly supplied JSON file.  It never opens a
browser, imports an application factory, or discovers user-data roots.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys
from typing import Any, Iterable


SCHEMA_VERSION = 1
LIVE_STATES = {"live-characterized", "differential-passed"}
ALL_STATES = {
    "unknown",
    "reference-only",
    "exists",
    "contract-tested",
    "live-characterized",
    "differential-passed",
    "reference-conflict",
    "excluded",
}
PROMOTION_FOR_OBSERVATION = {
    "unknown": None,
    "reference-only": None,
    "reference-conflict": None,
    "excluded": None,
    "exists": "exists",
    "contract-tested": "contract_tested",
    "live-characterized": "live_characterized",
    "differential-passed": "differential_passed",
}
PLANS = {"free", "plus", "business", "enterprise", "not_applicable"}
ROLES = {
    "workspace_owner",
    "workspace_admin",
    "workspace_member",
    "guest_editor",
    "guest_commenter",
    "guest_viewer",
    "public_anonymous",
}
PLATFORMS = {
    "desktop_web",
    "desktop_app_windows",
    "desktop_app_macos",
    "mobile_web",
    "ios_app",
    "android_app",
    "public_web",
}
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
SCENARIO_PATTERN = re.compile(r"^NP(?:[0-9]|1[01])-[A-Z0-9]+(?:-[A-Z0-9]+)*-[0-9]{3}$")
RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)

_SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")),
    ("BEARER", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    ("AUTH_HEADER", re.compile(r"(?i)\bauthorization\s*[:=]")),
    ("COOKIE", re.compile(r"(?i)\b(?:set-cookie|cookie)\s*[:=]")),
    ("WINDOWS_PATH", re.compile(r"(?i)(?:\b[A-Z]:\\|\\\\)[^\r\n]+")),
    ("POSIX_HOME", re.compile(r"(?:^|[\s'\"])/(?:home|Users)/[^\s'\"]+")),
    (
        "SECRET_QUERY",
        re.compile(r"(?i)[?&](?:token|key|secret|signature|sig|code)=[^&#\s]{6,}"),
    ),
)

_REQUIRED_FIELDS = {
    "schema_version",
    "scenario_id",
    "observation_state",
    "promotion_status",
    "reference_date",
    "reference_sources",
    "plan",
    "role",
    "platform",
    "app_or_browser_version",
    "locale",
    "input_sha256",
    "result_sha256",
    "evidence",
    "reference_conflicts",
    "reviewer",
    "reviewed_at",
}


class DuplicateJsonKey(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJsonKey(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def load_json(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_object_without_duplicates,
    )


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _walk_strings(value: Any, subject: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield subject, value
    elif isinstance(value, dict):
        for key in sorted(value):
            yield from _walk_strings(value[key], f"{subject}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_strings(item, f"{subject}[{index}]")


def _safe_relative_path(value: str) -> bool:
    if not value or "\x00" in value or "\\" in value:
        return False
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    if windows.is_absolute() or windows.drive or posix.is_absolute():
        return False
    return all(part not in {"", ".", ".."} for part in posix.parts)


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def validate_manifest(data: Any) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not isinstance(data, dict):
        return [_error("ROOT_TYPE", "$", "evidence manifest must be an object")]

    missing = sorted(_REQUIRED_FIELDS - set(data))
    extra = sorted(set(data) - _REQUIRED_FIELDS - {"comparison", "deletion"})
    for key in missing:
        errors.append(_error("FIELD_MISSING", key, f"required field missing: {key}"))
    for key in extra:
        errors.append(_error("UNKNOWN_FIELD", key, f"unknown field: {key}"))

    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(_error("SCHEMA_VERSION", "schema_version", "schema_version must be 1"))

    scenario_id = data.get("scenario_id")
    if not isinstance(scenario_id, str) or not SCENARIO_PATTERN.fullmatch(scenario_id):
        errors.append(_error("SCENARIO_ID", "scenario_id", "invalid scenario_id"))

    state = data.get("observation_state")
    if state not in ALL_STATES:
        errors.append(_error("STATE", "observation_state", "unknown evidence state"))
    expected_promotion = PROMOTION_FOR_OBSERVATION.get(state)
    if data.get("promotion_status") != expected_promotion:
        errors.append(
            _error(
                "STATUS_PROGRESSION",
                "promotion_status",
                "promotion_status is incompatible with observation_state",
            )
        )

    reference_sources = data.get("reference_sources")
    if not isinstance(reference_sources, list) or not reference_sources or any(
        not isinstance(item, str) or not item.startswith(("https://www.notion.com/", "https://developers.notion.com/"))
        for item in reference_sources
    ):
        errors.append(
            _error(
                "REFERENCE_SOURCE",
                "reference_sources",
                "reference_sources must be non-empty official Notion HTTPS URLs",
            )
        )

    for key in ("reference_date", "plan", "role", "platform", "app_or_browser_version", "locale"):
        if not isinstance(data.get(key), str) or not data.get(key, "").strip():
            errors.append(_error("FIELD_VALUE", key, f"{key} must be a non-empty string"))
    for key, allowed in (("plan", PLANS), ("role", ROLES), ("platform", PLATFORMS)):
        value = data.get(key)
        if isinstance(value, str) and value.strip() and value not in allowed:
            errors.append(_error("PROFILE_VALUE", key, f"unsupported {key}"))

    for key in ("input_sha256", "result_sha256"):
        value = data.get(key)
        if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
            errors.append(_error("HASH", key, f"{key} must be sha256:<64 lowercase hex>"))

    evidence = data.get("evidence")
    if not isinstance(evidence, list) or any(
        not isinstance(item, str) or not _safe_relative_path(item) for item in evidence
    ):
        errors.append(_error("EVIDENCE_PATH", "evidence", "evidence paths must be safe relative paths"))
    if state in LIVE_STATES and not evidence:
        errors.append(_error("LIVE_EVIDENCE", "evidence", "live states require evidence paths"))

    conflicts = data.get("reference_conflicts")
    if not isinstance(conflicts, list):
        errors.append(_error("CONFLICTS_TYPE", "reference_conflicts", "reference_conflicts must be a list"))
    if state == "reference-conflict" and isinstance(conflicts, list) and len(conflicts) < 2:
        errors.append(_error("CONFLICT_EVIDENCE", "reference_conflicts", "reference-conflict requires at least two conflicts"))

    reviewer = data.get("reviewer")
    reviewed_at = data.get("reviewed_at")
    if state in LIVE_STATES:
        if not isinstance(reviewer, str) or not reviewer.strip():
            errors.append(_error("REVIEWER", "reviewer", "live states require a reviewer"))
        if not isinstance(reviewed_at, str) or not RFC3339_PATTERN.fullmatch(reviewed_at):
            errors.append(_error("REVIEWED_AT", "reviewed_at", "live states require RFC3339 reviewed_at"))

    for subject, value in _walk_strings(data):
        for code, pattern in _SENSITIVE_PATTERNS:
            if pattern.search(value):
                errors.append(_error(f"SENSITIVE_{code}", subject, "sensitive value was not redacted"))

    return sorted(errors, key=lambda item: (item["code"], item["subject"], item["message"]))


def verify_file(path: Path) -> tuple[dict[str, Any], int]:
    try:
        data = load_json(path)
    except DuplicateJsonKey as exc:
        errors = [_error("DUPLICATE_JSON_KEY", "$", str(exc))]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors = [_error("MANIFEST_READ", "$", f"manifest could not be read: {type(exc).__name__}")]
    else:
        errors = validate_manifest(data)
    report = {
        "report_schema_version": 1,
        "status": "pass" if not errors else "fail",
        "errors": errors,
    }
    return report, 0 if not errors else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate one redacted Notion parity evidence manifest")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_file(args.manifest)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
