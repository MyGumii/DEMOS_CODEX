"""Deterministic, content-safe comparison for redacted parity captures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from code_assist_v1.tools.notion_parity.evidence import canonical_sha256, load_json


SCHEMA_VERSION = 1
EXACT_SECTIONS = (
    "state",
    "keyboard",
    "a11y",
    "semantic_visual",
    "error_recovery",
)
RECT_FIELDS = ("x", "y", "width", "height")


def _difference(
    section: str,
    path: str,
    kind: str,
) -> dict[str, str]:
    return {"section": section, "path": path, "kind": kind}


def _compare_exact(
    expected: Any,
    actual: Any,
    *,
    section: str,
    path: str,
    output: list[dict[str, str]],
) -> None:
    if type(expected) is not type(actual):
        output.append(_difference(section, path, "type_mismatch"))
        return
    if isinstance(expected, dict):
        expected_keys = set(expected)
        actual_keys = set(actual)
        for key in sorted(expected_keys - actual_keys):
            output.append(_difference(section, f"{path}.{key}", "missing"))
        for key in sorted(actual_keys - expected_keys):
            output.append(_difference(section, f"{path}.{key}", "unexpected"))
        for key in sorted(expected_keys & actual_keys):
            _compare_exact(
                expected[key],
                actual[key],
                section=section,
                path=f"{path}.{key}",
                output=output,
            )
        return
    if isinstance(expected, list):
        if len(expected) != len(actual):
            output.append(_difference(section, path, "length_mismatch"))
        for index, (left, right) in enumerate(zip(expected, actual)):
            _compare_exact(
                left,
                right,
                section=section,
                path=f"{path}[{index}]",
                output=output,
            )
        return
    if expected != actual:
        output.append(_difference(section, path, "value_mismatch"))


def _compare_geometry(
    expected: Any,
    actual: Any,
    *,
    tolerance: float,
    output: list[dict[str, str]],
) -> None:
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        output.append(_difference("geometry", "$.geometry", "type_mismatch"))
        return
    expected_keys = set(expected)
    actual_keys = set(actual)
    for key in sorted(expected_keys - actual_keys):
        output.append(_difference("geometry", f"$.geometry.{key}", "missing"))
    for key in sorted(actual_keys - expected_keys):
        output.append(_difference("geometry", f"$.geometry.{key}", "unexpected"))
    for key in sorted(expected_keys & actual_keys):
        left = expected[key]
        right = actual[key]
        if not isinstance(left, dict) or not isinstance(right, dict):
            output.append(_difference("geometry", f"$.geometry.{key}", "type_mismatch"))
            continue
        for field in RECT_FIELDS:
            path = f"$.geometry.{key}.{field}"
            left_value = left.get(field)
            right_value = right.get(field)
            if isinstance(left_value, bool) or isinstance(right_value, bool):
                output.append(_difference("geometry", path, "type_mismatch"))
            elif not isinstance(left_value, (int, float)) or not isinstance(right_value, (int, float)):
                output.append(_difference("geometry", path, "type_mismatch"))
            elif abs(float(left_value) - float(right_value)) > tolerance:
                output.append(_difference("geometry", path, "tolerance_exceeded"))


def compare_captures(
    reference: Any,
    candidate: Any,
    policy: Any,
) -> dict[str, Any]:
    differences: list[dict[str, str]] = []
    if not isinstance(reference, dict) or not isinstance(candidate, dict):
        differences.append(_difference("root", "$", "type_mismatch"))
    if not isinstance(policy, dict) or policy.get("schema_version") != SCHEMA_VERSION:
        differences.append(_difference("policy", "$.schema_version", "invalid_policy"))
        policy = {}
    if policy.get("pixel_identity_required") is True:
        differences.append(_difference("policy", "$.pixel_identity_required", "forbidden_policy"))
    if policy.get("brand_identity_compared") is True:
        differences.append(_difference("policy", "$.brand_identity_compared", "forbidden_policy"))

    enabled = policy.get("comparators") if isinstance(policy.get("comparators"), list) else []
    unknown = sorted(set(enabled) - {*EXACT_SECTIONS, "geometry", "latency"})
    for name in unknown:
        differences.append(_difference("policy", f"$.comparators.{name}", "unknown_comparator"))

    if isinstance(reference, dict) and isinstance(candidate, dict):
        for section in EXACT_SECTIONS:
            if section not in enabled:
                continue
            if section not in reference:
                differences.append(_difference(section, f"$.{section}", "reference_missing"))
            elif section not in candidate:
                differences.append(_difference(section, f"$.{section}", "missing"))
            else:
                _compare_exact(
                    reference[section],
                    candidate[section],
                    section=section,
                    path=f"$.{section}",
                    output=differences,
                )

        if "geometry" in enabled:
            raw_tolerance = policy.get("geometry_tolerance_px", 0)
            if isinstance(raw_tolerance, bool) or not isinstance(raw_tolerance, (int, float)) or raw_tolerance < 0:
                differences.append(_difference("policy", "$.geometry_tolerance_px", "invalid_policy"))
            else:
                _compare_geometry(
                    reference.get("geometry"),
                    candidate.get("geometry"),
                    tolerance=float(raw_tolerance),
                    output=differences,
                )

        if "latency" in enabled:
            budget = policy.get("latency_budget_ms")
            latency = candidate.get("latency_ms")
            if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget < 0:
                differences.append(_difference("policy", "$.latency_budget_ms", "invalid_policy"))
            elif isinstance(latency, bool) or not isinstance(latency, (int, float)):
                differences.append(_difference("latency", "$.latency_ms", "type_mismatch"))
            elif latency > budget:
                differences.append(_difference("latency", "$.latency_ms", "budget_exceeded"))

    differences.sort(key=lambda item: (item["section"], item["path"], item["kind"]))
    return {
        "report_schema_version": 1,
        "status": "pass" if not differences else "fail",
        "reference_sha256": canonical_sha256(reference),
        "candidate_sha256": canonical_sha256(candidate),
        "policy_sha256": canonical_sha256(policy),
        "difference_count": len(differences),
        "differences": differences,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two redacted Notion parity captures")
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    try:
        reference = load_json(args.reference)
        candidate = load_json(args.candidate)
        policy = load_json(args.policy)
    except Exception as exc:
        report = {
            "report_schema_version": 1,
            "status": "fail",
            "difference_count": 1,
            "differences": [_difference("input", "$", f"read_error_{type(exc).__name__}")],
        }
        exit_code = 2
    else:
        report = compare_captures(reference, candidate, policy)
        exit_code = 0 if report["status"] == "pass" else 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
