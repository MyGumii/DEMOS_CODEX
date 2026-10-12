"""Normalized capture validation and pre-calibration comparison for UI evidence."""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Sequence

from code_assist_v1.tools.notion_parity.evidence import canonical_sha256
from code_assist_v1.tools.notion_parity.ui_redaction import (
    RedactionPolicy,
    validate_redacted_payload,
)


CAPTURE_SCHEMA_VERSION = 1
PROFILE = "free-owner-chrome-desktop-ko-KR"
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SCENARIO_RE = re.compile(r"^UI-[A-Z0-9]+(?:-[A-Z0-9]+)*-[0-9]{3}$")
CAPTURE_KEYS = frozenset(
    {
        "capture_schema_version",
        "scenario_id",
        "side",
        "profile",
        "viewport",
        "state",
        "keyboard",
        "focus",
        "a11y",
        "semantic_visual",
        "error_recovery",
        "geometry",
        "latency_ms",
        "action_log_sha256",
        "screenshot_sha256",
        "source_build_sha256",
        "redaction",
    }
)
VIEWPORT_KEYS = frozenset({"width", "height", "scale"})
RECT_KEYS = frozenset({"x", "y", "width", "height"})
REDACTION_KEYS = frozenset({"status", "rule_set"})
SAFE_NESTED_KEYS = frozenset(
    {
        "visible",
        "hidden",
        "active",
        "opened",
        "expanded",
        "collapsed",
        "enabled",
        "disabled",
        "selected",
        "value",
        "count",
        "order",
        "role",
        "name",
        "state",
        "result",
        "key",
        "sequence",
        "restored",
        "mutation_count",
        "publication_count",
        "source_hash_match",
        "workspace_alias",
        "page_alias",
        "region",
        "status",
        "reason",
        "x",
        "y",
        "width",
        "height",
    }
)


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _sorted(errors: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    unique = {
        (item["code"], item["subject"], item["message"]): item
        for item in errors
    }
    return [unique[key] for key in sorted(unique)]


def _number(value: Any, *, allow_zero: bool = True) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not math.isfinite(float(value)):
        return False
    return value >= 0 if allow_zero else value > 0


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


def validate_capture(
    capture: Any,
    *,
    synthetic_allowlist: Sequence[str] = (),
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    value = _exact_keys(capture, CAPTURE_KEYS, "$", errors)
    if value is None:
        return _sorted(errors)
    if value.get("capture_schema_version") != CAPTURE_SCHEMA_VERSION:
        errors.append(_error("SCHEMA_VERSION", "$.capture_schema_version", "unsupported capture schema"))
    scenario_id = value.get("scenario_id")
    if not isinstance(scenario_id, str) or not SCENARIO_RE.fullmatch(scenario_id):
        errors.append(_error("SCENARIO_ID", "$.scenario_id", "invalid UI scenario id"))
    if value.get("side") not in {"reference", "local"}:
        errors.append(_error("SIDE", "$.side", "side must be reference or local"))
    if value.get("profile") != PROFILE:
        errors.append(_error("PROFILE", "$.profile", "capture must use the fixed Core profile"))

    viewport = _exact_keys(value.get("viewport"), VIEWPORT_KEYS, "$.viewport", errors)
    if viewport is not None:
        if not isinstance(viewport.get("width"), int) or isinstance(viewport.get("width"), bool) or viewport["width"] <= 0:
            errors.append(_error("VIEWPORT_WIDTH", "$.viewport.width", "width must be a positive integer"))
        if not isinstance(viewport.get("height"), int) or isinstance(viewport.get("height"), bool) or viewport["height"] <= 0:
            errors.append(_error("VIEWPORT_HEIGHT", "$.viewport.height", "height must be a positive integer"))
        if not _number(viewport.get("scale"), allow_zero=False):
            errors.append(_error("VIEWPORT_SCALE", "$.viewport.scale", "scale must be a positive finite number"))

    for section in ("state", "keyboard", "focus", "a11y", "semantic_visual", "error_recovery"):
        if not isinstance(value.get(section), dict):
            errors.append(_error("SECTION_TYPE", f"$.{section}", "capture section must be an object"))

    geometry = value.get("geometry")
    if not isinstance(geometry, dict):
        errors.append(_error("GEOMETRY_TYPE", "$.geometry", "geometry must be an object"))
    else:
        for region, rectangle in geometry.items():
            region_subject = f"$.geometry.{region}"
            rect = _exact_keys(rectangle, RECT_KEYS, region_subject, errors)
            if rect is None:
                continue
            for field in RECT_KEYS:
                if not _number(rect.get(field)):
                    errors.append(_error("GEOMETRY_NUMBER", f"{region_subject}.{field}", "geometry fields must be finite non-negative numbers"))

    if not _number(value.get("latency_ms")):
        errors.append(_error("LATENCY", "$.latency_ms", "latency must be a finite non-negative number"))
    for field in ("action_log_sha256", "source_build_sha256"):
        item = value.get(field)
        if not isinstance(item, str) or not SHA256_RE.fullmatch(item):
            errors.append(_error("HASH", f"$.{field}", "expected canonical SHA-256"))
    screenshot = value.get("screenshot_sha256")
    if screenshot is not None and (not isinstance(screenshot, str) or not SHA256_RE.fullmatch(screenshot)):
        errors.append(_error("HASH", "$.screenshot_sha256", "expected canonical SHA-256 or null"))

    redaction = _exact_keys(value.get("redaction"), REDACTION_KEYS, "$.redaction", errors)
    if redaction is not None and redaction != {"status": "pass", "rule_set": "core-free-ui-v1"}:
        errors.append(_error("REDACTION", "$.redaction", "redaction must pass the Core Free UI v1 rules"))

    allowed_keys = CAPTURE_KEYS | VIEWPORT_KEYS | RECT_KEYS | REDACTION_KEYS | SAFE_NESTED_KEYS
    errors.extend(
        validate_redacted_payload(
            value,
            policy=RedactionPolicy(
                allowed_keys=allowed_keys,
                allowed_strings=frozenset(synthetic_allowlist),
            ),
        )
    )
    _json_types_only(value, "$", errors)
    return _sorted(errors)


def capture_sha256(capture: Any) -> str:
    return canonical_sha256(capture)


def compare_precalibration_captures(
    reference: Any,
    candidate: Any,
    *,
    synthetic_allowlist: Sequence[str] = (),
) -> dict[str, Any]:
    """Compare exact sections while reporting geometry/latency as measurements."""

    reference_errors = validate_capture(reference, synthetic_allowlist=synthetic_allowlist)
    candidate_errors = validate_capture(candidate, synthetic_allowlist=synthetic_allowlist)
    differences: list[dict[str, str]] = []
    for side, errors in (("reference", reference_errors), ("candidate", candidate_errors)):
        for item in errors:
            differences.append({"section": "validation", "path": f"{side}:{item['subject']}", "kind": item["code"]})
    if not reference_errors and not candidate_errors:
        for section in ("state", "keyboard", "focus", "a11y", "semantic_visual", "error_recovery"):
            if reference[section] != candidate[section]:
                differences.append({"section": section, "path": f"$.{section}", "kind": "value_mismatch"})
        if reference["scenario_id"] != candidate["scenario_id"]:
            differences.append({"section": "identity", "path": "$.scenario_id", "kind": "value_mismatch"})
        if reference["viewport"] != candidate["viewport"]:
            differences.append({"section": "identity", "path": "$.viewport", "kind": "value_mismatch"})
    differences.sort(key=lambda item: (item["section"], item["path"], item["kind"]))
    return {
        "report_schema_version": 1,
        "policy_state": "precalibration",
        "status": "pass" if not differences else "fail",
        "promotion_allowed": False,
        "reference_sha256": canonical_sha256(reference),
        "candidate_sha256": canonical_sha256(candidate),
        "difference_count": len(differences),
        "differences": differences,
        "measurements": {
            "reference_geometry": reference.get("geometry") if isinstance(reference, dict) else None,
            "candidate_geometry": candidate.get("geometry") if isinstance(candidate, dict) else None,
            "reference_latency_ms": reference.get("latency_ms") if isinstance(reference, dict) else None,
            "candidate_latency_ms": candidate.get("latency_ms") if isinstance(candidate, dict) else None,
        },
    }
