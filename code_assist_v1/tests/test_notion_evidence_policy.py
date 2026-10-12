from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.notion_parity.evidence import (
    canonical_sha256,
    validate_manifest,
    verify_file,
)


def _manifest() -> dict:
    return {
        "schema_version": 1,
        "scenario_id": "NP3-EDITOR-ENTER-001",
        "observation_state": "live-characterized",
        "promotion_status": "live_characterized",
        "reference_date": "2026-08-13",
        "reference_sources": ["https://www.notion.com/help/writing-and-editing-basics"],
        "plan": "free",
        "role": "workspace_owner",
        "platform": "desktop_web",
        "app_or_browser_version": "Chromium 140.0.0.0",
        "locale": "ko-KR",
        "input_sha256": "sha256:" + "1" * 64,
        "result_sha256": "sha256:" + "2" * 64,
        "evidence": ["NP3-EDITOR-ENTER-001/result.redacted.json"],
        "reference_conflicts": [],
        "reviewer": "reviewer-01",
        "reviewed_at": "2026-08-13T01:00:00+09:00",
    }


@pytest.mark.unit
def test_valid_redacted_manifest_passes() -> None:
    assert validate_manifest(_manifest()) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "secret,code",
    (
        ("person@example.com", "SENSITIVE_EMAIL"),
        ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz", "SENSITIVE_BEARER"),
        (r"G:\\private\\user\\notion.db", "SENSITIVE_WINDOWS_PATH"),
        ("/Users/alice/private/page.json", "SENSITIVE_POSIX_HOME"),
        ("https://example.test/cb?token=super-secret", "SENSITIVE_SECRET_QUERY"),
    ),
)
def test_sensitive_values_fail_closed(secret: str, code: str) -> None:
    manifest = _manifest()
    manifest["reviewer"] = secret

    errors = validate_manifest(manifest)

    assert any(item["code"] == code for item in errors)
    assert all(secret not in item["message"] for item in errors)


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    (
        "../outside.json",
        "/absolute/result.json",
        "C:/private/result.json",
        r"folder\\result.json",
    ),
)
def test_evidence_path_escape_is_rejected(path: str) -> None:
    manifest = _manifest()
    manifest["evidence"] = [path]

    assert any(item["code"] == "EVIDENCE_PATH" for item in validate_manifest(manifest))


@pytest.mark.unit
def test_live_state_requires_evidence_reviewer_and_timestamp() -> None:
    manifest = _manifest()
    manifest.update(evidence=[], reviewer="", reviewed_at="unknown")

    codes = {item["code"] for item in validate_manifest(manifest)}

    assert {"LIVE_EVIDENCE", "REVIEWER", "REVIEWED_AT"}.issubset(codes)


@pytest.mark.unit
def test_observation_and_promotion_status_must_progress_together() -> None:
    manifest = _manifest()
    manifest["observation_state"] = "reference-only"

    errors = validate_manifest(manifest)

    assert any(item["code"] == "STATUS_PROGRESSION" for item in errors)


@pytest.mark.unit
def test_duplicate_key_is_rejected_deterministically(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")

    first, first_exit = verify_file(path)
    second, second_exit = verify_file(path)

    assert first_exit == second_exit == 2
    assert first == second
    assert first["errors"][0]["code"] == "DUPLICATE_JSON_KEY"


@pytest.mark.unit
def test_errors_are_sorted_and_do_not_reflect_sensitive_values() -> None:
    manifest = deepcopy(_manifest())
    manifest["reviewer"] = r"G:\\private\\name@example.com"
    manifest["evidence"] = ["../private.json"]

    errors = validate_manifest(manifest)

    assert errors == sorted(errors, key=lambda item: (item["code"], item["subject"], item["message"]))
    assert "private" not in json.dumps(errors).lower()


@pytest.mark.unit
def test_canonical_hash_is_order_independent() -> None:
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})
