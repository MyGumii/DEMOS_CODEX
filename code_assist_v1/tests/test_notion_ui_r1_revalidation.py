from __future__ import annotations

import json

import pytest

from code_assist_v1.tools.verify_notion_core_free_ui_r1_revalidation import DEFAULT_CAPTURE, verify


@pytest.mark.unit
def test_current_r1_observed_reference_fields_match() -> None:
    report, exit_code = verify()
    assert exit_code == 0
    assert report["status"] == "pass"
    assert report["scenario_count"] == 7
    assert report["compared_observed_field_count"] == 84
    assert report["observed_fields_match"] is True
    assert report["differential_ready"] is True
    assert report["remaining_gate"] == "none"
    assert report["errors"] == []


@pytest.mark.unit
def test_current_r1_observed_mismatch_fails_closed(tmp_path) -> None:
    artifact = json.loads(DEFAULT_CAPTURE.read_text(encoding="utf-8"))
    artifact["scenarios"]["UI-SHARE-001"]["action_captures"]["UI-SHARE-001-A01"]["focus"] = {"name": "PAGE-CANVAS", "state": "focused"}
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    report, exit_code = verify(changed)
    assert exit_code == 1
    assert report["status"] == "fail"
    assert any(item["code"] == "OBSERVED_FIELD_MISMATCH" for item in report["errors"])
