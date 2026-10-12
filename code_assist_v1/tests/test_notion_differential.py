from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.tools.notion_parity.differential import compare_captures


def _capture() -> dict:
    return {
        "state": {"page_count": 1, "sidebar": "expanded"},
        "keyboard": [
            {"key": "Enter", "focus": "block-2", "selection": "collapsed"}
        ],
        "a11y": [
            {"role": "textbox", "name": "Page content", "focused": True}
        ],
        "semantic_visual": {"sidebar_visible": True, "primary_action_visible": True},
        "error_recovery": {"retry": "no_duplicate", "partial_write": False},
        "geometry": {"sidebar": {"x": 0, "y": 0, "width": 240, "height": 800}},
        "latency_ms": 120,
    }


def _policy() -> dict:
    return {
        "schema_version": 1,
        "comparators": [
            "state",
            "keyboard",
            "a11y",
            "semantic_visual",
            "error_recovery",
            "geometry",
            "latency",
        ],
        "geometry_tolerance_px": 2,
        "latency_budget_ms": 500,
        "pixel_identity_required": False,
        "brand_identity_compared": False,
    }


@pytest.mark.unit
def test_equal_redacted_captures_pass_deterministically() -> None:
    first = compare_captures(_capture(), _capture(), _policy())
    second = compare_captures(_capture(), _capture(), _policy())

    assert first == second
    assert first["status"] == "pass"
    assert first["difference_count"] == 0


@pytest.mark.unit
def test_geometry_inside_tolerance_passes() -> None:
    candidate = _capture()
    candidate["geometry"]["sidebar"]["width"] = 242

    assert compare_captures(_capture(), candidate, _policy())["status"] == "pass"


@pytest.mark.unit
def test_differences_expose_paths_and_kinds_not_values() -> None:
    reference = _capture()
    candidate = _capture()
    candidate["state"]["sidebar"] = "secret-user-content"
    candidate["keyboard"][0]["focus"] = "private-page-title"
    candidate["geometry"]["sidebar"]["width"] = 250
    candidate["latency_ms"] = 800

    report = compare_captures(reference, candidate, _policy())
    serialized = json.dumps(report)

    assert report["status"] == "fail"
    assert report["differences"] == sorted(
        report["differences"],
        key=lambda item: (item["section"], item["path"], item["kind"]),
    )
    assert "secret-user-content" not in serialized
    assert "private-page-title" not in serialized
    assert {item["kind"] for item in report["differences"]} == {
        "budget_exceeded",
        "tolerance_exceeded",
        "value_mismatch",
    }


@pytest.mark.unit
def test_unknown_comparator_and_invalid_policy_fail_closed() -> None:
    policy = _policy()
    policy["comparators"].append("pixel_clone")
    policy["geometry_tolerance_px"] = -1

    report = compare_captures(_capture(), _capture(), policy)

    assert report["status"] == "fail"
    assert {item["kind"] for item in report["differences"]} == {
        "invalid_policy",
        "unknown_comparator",
    }


@pytest.mark.unit
def test_pixel_or_brand_clone_policy_is_rejected() -> None:
    policy = _policy()
    policy["pixel_identity_required"] = True
    policy["brand_identity_compared"] = True

    report = compare_captures(_capture(), _capture(), policy)

    assert report["status"] == "fail"
    assert [item["path"] for item in report["differences"]] == [
        "$.brand_identity_compared",
        "$.pixel_identity_required",
    ]
    assert {item["kind"] for item in report["differences"]} == {"forbidden_policy"}


@pytest.mark.unit
def test_checked_in_default_policy_is_valid() -> None:
    policy_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "notion_parity"
        / "policies"
        / "default_differential.json"
    )
    policy = json.loads(policy_path.read_text(encoding="utf-8"))

    report = compare_captures(_capture(), _capture(), policy)

    assert report["status"] == "pass"


@pytest.mark.unit
def test_cli_returns_machine_readable_pass_and_fail(tmp_path: Path) -> None:
    reference = tmp_path / "reference.json"
    candidate = tmp_path / "candidate.json"
    policy = tmp_path / "policy.json"
    reference.write_text(json.dumps(_capture()), encoding="utf-8")
    candidate.write_text(json.dumps(_capture()), encoding="utf-8")
    policy.write_text(json.dumps(_policy()), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "code_assist_v1.tools.notion_parity.differential",
        "--reference",
        str(reference),
        "--candidate",
        str(candidate),
        "--policy",
        str(policy),
        "--compact",
    ]

    passed = subprocess.run(command, capture_output=True, text=True, check=False)
    candidate.write_text(json.dumps({**_capture(), "latency_ms": 1000}), encoding="utf-8")
    failed = subprocess.run(command, capture_output=True, text=True, check=False)

    assert passed.returncode == 0
    assert json.loads(passed.stdout)["status"] == "pass"
    assert failed.returncode == 2
    assert json.loads(failed.stdout)["status"] == "fail"
    assert passed.stderr == failed.stderr == ""
