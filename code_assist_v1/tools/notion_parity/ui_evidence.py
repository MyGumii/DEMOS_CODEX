"""Fail-closed manifest validation for Core Free Web UI evidence."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys
from typing import Any, Iterable, Sequence

from code_assist_v1.tools.notion_parity.evidence import canonical_sha256, load_json


SCHEMA_VERSION = 1
PROFILE = "free-owner-chrome-desktop-ko-KR"
SCENARIO_RE = re.compile(r"^UI-[A-Z0-9]+(?:-[A-Z0-9]+)*-[0-9]{3}$")
RUN_ID_RE = re.compile(r"^ui[0-9]+-[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REVIEWER_RE = re.compile(r"^REVIEWER-[A-Z0-9]+(?:-[A-Z0-9]+)*$")
ROOT_KEYS = frozenset(
    {
        "schema_version",
        "contract",
        "scenario_id",
        "run_id",
        "observation_state",
        "promotion_status",
        "profile",
        "source_build_sha256",
        "action_log",
        "captures",
        "differential",
        "screenshots",
        "reviewer",
        "reviewed_at",
        "redaction",
        "raw_artifact_deletion",
    }
)
ARTIFACT_KEYS = frozenset({"kind", "path", "sha256"})
CAPTURE_KEYS = frozenset({"reference", "local"})
DIFFERENTIAL_KEYS = frozenset(
    {"kind", "path", "sha256", "status", "reference_sha256", "candidate_sha256", "policy_sha256"}
)
REDACTION_KEYS = frozenset({"status", "rule_set", "allowlist_sha256"})
DELETION_KEYS = frozenset({"status", "record_sha256"})
STATES = {
    "unknown": None,
    "contract-tested": "contract_tested",
    "live-characterized": "live_characterized",
    "differential-failed": "live_characterized",
    "differential-passed": "differential_passed",
}
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


def _sorted(errors: Iterable[dict[str, str]]) -> list[dict[str, str]]:
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


def _safe_relative_path(value: str) -> bool:
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


def _exact_keys(
    value: Any,
    expected: frozenset[str],
    subject: str,
    errors: list[dict[str, str]],
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        errors.append(_error("OBJECT_TYPE", subject, "expected an object"))
        return None
    for key in sorted(expected - set(value)):
        errors.append(_error("FIELD_MISSING", f"{subject}.{key}", "required field is missing"))
    for key in sorted(set(value) - expected):
        errors.append(_error("UNKNOWN_FIELD", f"{subject}.{key}", "unknown field"))
    return value


def _artifact(
    value: Any,
    subject: str,
    errors: list[dict[str, str]],
    *,
    expected_kind: str | None = None,
) -> dict[str, Any] | None:
    artifact = _exact_keys(value, ARTIFACT_KEYS, subject, errors)
    if artifact is None:
        return None
    kind = artifact.get("kind")
    allowed_kinds = {"action_log", "capture", "screenshot_redacted", "deletion_record"}
    if kind not in allowed_kinds or (expected_kind is not None and kind != expected_kind):
        errors.append(_error("ARTIFACT_KIND", subject + ".kind", "unexpected artifact kind"))
    path = artifact.get("path")
    if not isinstance(path, str) or not _safe_relative_path(path):
        errors.append(_error("ARTIFACT_PATH", subject + ".path", "artifact path must be safe and relative"))
    digest = artifact.get("sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        errors.append(_error("ARTIFACT_HASH", subject + ".sha256", "artifact hash must be canonical SHA-256"))
    return artifact


def _date_time(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_evidence_manifest(value: Any) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    manifest = _exact_keys(value, ROOT_KEYS, "$", errors)
    if manifest is None:
        return _sorted(errors)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(_error("SCHEMA_VERSION", "$.schema_version", "schema_version must be 1"))
    if manifest.get("contract") != "notion_core_free_ui_evidence_v1":
        errors.append(_error("CONTRACT", "$.contract", "unexpected evidence contract"))
    if not isinstance(manifest.get("scenario_id"), str) or not SCENARIO_RE.fullmatch(manifest["scenario_id"]):
        errors.append(_error("SCENARIO_ID", "$.scenario_id", "invalid UI scenario id"))
    if not isinstance(manifest.get("run_id"), str) or not RUN_ID_RE.fullmatch(manifest["run_id"]):
        errors.append(_error("RUN_ID", "$.run_id", "invalid synthetic run id"))
    state = manifest.get("observation_state")
    if state not in STATES:
        errors.append(_error("STATE", "$.observation_state", "unsupported evidence state"))
    elif manifest.get("promotion_status") != STATES[state]:
        errors.append(_error("PROMOTION", "$.promotion_status", "promotion status does not match evidence state"))
    if manifest.get("profile") != PROFILE:
        errors.append(_error("PROFILE", "$.profile", "evidence must use the fixed Core profile"))
    source_hash = manifest.get("source_build_sha256")
    if not isinstance(source_hash, str) or not SHA256_RE.fullmatch(source_hash):
        errors.append(_error("SOURCE_HASH", "$.source_build_sha256", "source build hash must be canonical SHA-256"))

    _artifact(manifest.get("action_log"), "$.action_log", errors, expected_kind="action_log")
    captures = _exact_keys(manifest.get("captures"), CAPTURE_KEYS, "$.captures", errors)
    reference = local = None
    if captures is not None:
        reference = captures.get("reference")
        local = captures.get("local")
        if reference is not None:
            reference = _artifact(reference, "$.captures.reference", errors, expected_kind="capture")
        if local is not None:
            local = _artifact(local, "$.captures.local", errors, expected_kind="capture")

    differential = manifest.get("differential")
    if differential is not None:
        item = _exact_keys(differential, DIFFERENTIAL_KEYS, "$.differential", errors)
        if item is not None:
            if item.get("kind") != "differential":
                errors.append(_error("DIFFERENTIAL_KIND", "$.differential.kind", "kind must be differential"))
            path = item.get("path")
            if not isinstance(path, str) or not _safe_relative_path(path):
                errors.append(_error("ARTIFACT_PATH", "$.differential.path", "differential path must be safe and relative"))
            for field in ("sha256", "reference_sha256", "candidate_sha256", "policy_sha256"):
                digest = item.get(field)
                if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
                    errors.append(_error("ARTIFACT_HASH", f"$.differential.{field}", "expected canonical SHA-256"))
            if item.get("status") not in {"pass", "fail"}:
                errors.append(_error("DIFFERENTIAL_STATUS", "$.differential.status", "status must be pass or fail"))

    screenshots = manifest.get("screenshots")
    if not isinstance(screenshots, list):
        errors.append(_error("SCREENSHOT_TYPE", "$.screenshots", "screenshots must be an array"))
    else:
        for index, item in enumerate(screenshots):
            _artifact(item, f"$.screenshots[{index}]", errors, expected_kind="screenshot_redacted")

    redaction = _exact_keys(manifest.get("redaction"), REDACTION_KEYS, "$.redaction", errors)
    if redaction is not None:
        if redaction.get("status") != "pass" or redaction.get("rule_set") != "core-free-ui-v1":
            errors.append(_error("REDACTION", "$.redaction", "redaction must pass Core Free UI v1"))
        digest = redaction.get("allowlist_sha256")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            errors.append(_error("REDACTION_HASH", "$.redaction.allowlist_sha256", "allowlist hash must be canonical SHA-256"))

    deletion = _exact_keys(manifest.get("raw_artifact_deletion"), DELETION_KEYS, "$.raw_artifact_deletion", errors)
    if deletion is not None:
        deletion_status = deletion.get("status")
        if deletion_status not in {"not_applicable", "pending", "complete"}:
            errors.append(_error("DELETION_STATUS", "$.raw_artifact_deletion.status", "invalid deletion status"))
        record = deletion.get("record_sha256")
        if deletion_status == "complete":
            if not isinstance(record, str) or not SHA256_RE.fullmatch(record):
                errors.append(_error("DELETION_RECORD", "$.raw_artifact_deletion.record_sha256", "completed deletion requires a canonical record hash"))
        elif record is not None:
            errors.append(_error("DELETION_RECORD", "$.raw_artifact_deletion.record_sha256", "record hash is only valid after completed deletion"))

    reviewer = manifest.get("reviewer")
    reviewed_at = manifest.get("reviewed_at")
    live = state in {"live-characterized", "differential-failed", "differential-passed"}
    if live:
        if not isinstance(reviewer, str) or not REVIEWER_RE.fullmatch(reviewer):
            errors.append(_error("REVIEWER", "$.reviewer", "live evidence requires an opaque reviewer alias"))
        if not _date_time(reviewed_at):
            errors.append(_error("REVIEWED_AT", "$.reviewed_at", "live evidence requires a timezone-aware timestamp"))
        if reference is None or local is None:
            errors.append(_error("LIVE_CAPTURES", "$.captures", "live evidence requires both reference and local captures"))
    else:
        if reviewer is not None or reviewed_at is not None:
            errors.append(_error("REVIEW_METADATA", "$.reviewer", "non-live evidence cannot claim review metadata"))

    if state in {"unknown", "contract-tested", "live-characterized"} and differential is not None:
        errors.append(_error("DIFFERENTIAL_UNEXPECTED", "$.differential", "this evidence state cannot contain a differential report"))
    if state in {"unknown", "contract-tested"} and (reference is not None or local is not None):
        errors.append(_error("CAPTURE_UNEXPECTED", "$.captures", "this evidence state cannot contain live captures"))
    if state == "differential-passed" and (not isinstance(differential, dict) or differential.get("status") != "pass"):
        errors.append(_error("DIFFERENTIAL_REQUIRED", "$.differential", "differential-passed requires a passing report"))
    if state == "differential-failed" and (not isinstance(differential, dict) or differential.get("status") != "fail"):
        errors.append(_error("DIFFERENTIAL_REQUIRED", "$.differential", "differential-failed requires a failing report"))

    for subject, text in _walk_strings(manifest):
        for code, pattern in SENSITIVE_PATTERNS:
            if pattern.search(text):
                errors.append(_error(f"SENSITIVE_{code}", subject, "sensitive value is not allowed"))
    return _sorted(errors)


def verify_evidence_file(path: str | Path) -> tuple[dict[str, Any], int]:
    value: Any = None
    errors: list[dict[str, str]] = []
    try:
        value = load_json(Path(path))
    except (OSError, ValueError) as exc:
        errors.append(_error("MANIFEST_READ_FAILED", "$", f"manifest could not be read: {type(exc).__name__}"))
    if not errors:
        errors = validate_evidence_manifest(value)
    report = {
        "report_schema_version": 1,
        "status": "pass" if not errors else "fail",
        "manifest_sha256": canonical_sha256(value),
        "error_count": len(errors),
        "errors": errors,
    }
    return report, 0 if not errors else 1


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Validate a Core Free UI evidence manifest")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_evidence_file(args.manifest)
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
