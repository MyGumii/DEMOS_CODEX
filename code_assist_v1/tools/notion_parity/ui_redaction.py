"""Allowlist-first redaction validation for Core Free Web UI artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable


SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SEMANTIC_ALIAS_RE = re.compile(r"^[A-Z][A-Z0-9_-]{0,127}$")
SCENARIO_RE = re.compile(r"^UI-[A-Z0-9]+(?:-[A-Z0-9]+)*-[0-9]{3}$")
ACTION_RE = re.compile(r"^UI-[A-Z0-9]+(?:-[A-Z0-9]+)*-A[0-9]{2}$")
SYNTHETIC_FIXTURE_RE = re.compile(r"^UI-PARITY-RUN-[A-Z0-9]+(?:-[A-Z0-9]+)*$")
RUN_ID_RE = re.compile(r"^ui[0-9]+-[a-z0-9]+(?:-[a-z0-9]+)*$")
UUID_RE = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b")
LEGACY_PAGE_ID_RE = re.compile(r"(?i)\bp_[0-9a-f]{20,}\b")

SAFE_LITERALS = frozenset(
    {
        "reference",
        "local",
        "unknown",
        "planned",
        "exists",
        "contract-tested",
        "live-characterized",
        "differential-passed",
        "differential-failed",
        "contract_tested",
        "live_characterized",
        "differential_passed",
        "pass",
        "fail",
        "pending",
        "complete",
        "not_applicable",
        "visible",
        "hidden",
        "hidden_with_access_tooltip",
        "enabled",
        "disabled",
        "expanded",
        "collapsed",
        "active",
        "inactive",
        "focused",
        "not_focused",
        "blurred",
        "success",
        "failure",
        "unchanged",
        "committed",
        "cancelled",
        "replayed",
        "restored",
        "resolved_and_hidden",
        "read_only",
        "synthetic_mutation",
        "local_second_brain_mutation",
        "free-owner-chrome-desktop-ko-KR",
        "core_free_owner_chrome_desktop_ko_kr",
        "core-free-ui-v1",
        "REF-FREE-A",
        "REF-FREE-B",
    }
)

SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")),
    ("BEARER", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    ("AUTH", re.compile(r"(?i)\b(?:authorization|cookie|set-cookie)\s*[:=]")),
    ("WINDOWS_PATH", re.compile(r"(?i)(?:\b[A-Z]:\\|\\\\)[^\r\n]+")),
    ("POSIX_HOME", re.compile(r"(?:^|[\s'\"])/(?:home|Users)/[^\s'\"]+")),
    ("SECRET_QUERY", re.compile(r"(?i)[?&](?:token|key|secret|signature|sig|code)=[^&#\s]{6,}")),
    ("UUID", UUID_RE),
    ("LEGACY_PAGE_ID", LEGACY_PAGE_ID_RE),
)


@dataclass(frozen=True)
class RedactionPolicy:
    allowed_keys: frozenset[str]
    allowed_strings: frozenset[str] = frozenset()


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _iter_nodes(
    value: Any,
    subject: str = "$",
) -> Iterable[tuple[str, str, str]]:
    if isinstance(value, dict):
        for key in sorted(value):
            yield "key", f"{subject}.{key}", str(key)
            yield from _iter_nodes(value[key], f"{subject}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_nodes(item, f"{subject}[{index}]")
    elif isinstance(value, str):
        yield "value", subject, value


def _is_allowed_value(value: str, allowed_strings: frozenset[str]) -> bool:
    return bool(
        value in SAFE_LITERALS
        or value in allowed_strings
        or SHA256_RE.fullmatch(value)
        or SCENARIO_RE.fullmatch(value)
        or ACTION_RE.fullmatch(value)
        or SEMANTIC_ALIAS_RE.fullmatch(value)
        or SYNTHETIC_FIXTURE_RE.fullmatch(value)
        or RUN_ID_RE.fullmatch(value)
    )


def validate_redacted_payload(
    value: Any,
    *,
    policy: RedactionPolicy,
) -> list[dict[str, str]]:
    """Return deterministic errors for unapproved keys or string values."""

    errors: list[dict[str, str]] = []
    for kind, subject, text in _iter_nodes(value):
        for code, pattern in SENSITIVE_PATTERNS:
            if pattern.search(text):
                errors.append(_error(f"SENSITIVE_{code}", subject, "sensitive value is not allowed"))
        if kind == "key":
            if text not in policy.allowed_keys and not SEMANTIC_ALIAS_RE.fullmatch(text):
                errors.append(_error("KEY_NOT_ALLOWLISTED", subject, "artifact key is not allowlisted"))
        elif not _is_allowed_value(text, policy.allowed_strings):
            errors.append(_error("VALUE_NOT_ALLOWLISTED", subject, "artifact string is not allowlisted"))
    unique = {
        (item["code"], item["subject"], item["message"]): item
        for item in errors
    }
    return [unique[key] for key in sorted(unique)]


def require_redacted_payload(value: Any, *, policy: RedactionPolicy) -> None:
    errors = validate_redacted_payload(value, policy=policy)
    if errors:
        first = errors[0]
        raise ValueError(f"{first['code']}:{first['subject']}")
