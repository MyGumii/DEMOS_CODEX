from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from code_assist_v1.tools.verify_notion_core_free_ui_completion import load_json
from code_assist_v1.tools.verify_notion_core_free_ui_reference_completion_manifest import (
    DEFAULT_MANIFEST,
    verify_reference_completion_manifest,
)


def _write(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _codes(tmp_path: Path, mutate) -> set[str]:
    value = copy.deepcopy(load_json(DEFAULT_MANIFEST))
    mutate(value)
    report, exit_code = verify_reference_completion_manifest(_write(tmp_path, value))
    assert exit_code == 1
    return {item["code"] for item in report["errors"]}


@pytest.mark.unit
def test_checked_in_manifest_is_exact_preapproved_scope_but_not_executed() -> None:
    report, exit_code = verify_reference_completion_manifest()
    assert exit_code == 0, report
    assert report == {
        "contract": "code_assist.notion.core_free_ui.reference_completion_execution.v1",
        "status": "pass",
        "action_count": 10,
        "fixture_count": 3,
        "approval_received": False,
        "reference_mutation_executed": False,
        "errors": [],
    }


@pytest.mark.unit
def test_manifest_rejects_personal_workspace_or_action_order_drift(tmp_path: Path) -> None:
    def workspace(value: dict[str, object]) -> None:
        value["actions"][0]["workspace"] = "PERSONAL"  # type: ignore[index]

    assert "ACTION_ORDER_OR_SCOPE_INVALID" in _codes(tmp_path, workspace)

    def order(value: dict[str, object]) -> None:
        value["actions"][0]["order"] = 2  # type: ignore[index]

    assert "ACTION_ORDER_OR_SCOPE_INVALID" in _codes(tmp_path, order)


@pytest.mark.unit
def test_manifest_rejects_capture_cleanup_or_permission_weakening(tmp_path: Path) -> None:
    def capture(value: dict[str, object]) -> None:
        value["actions"][0]["capture_fields"] = ["state"]  # type: ignore[index]

    assert "CAPTURE_FIELDS_INVALID" in _codes(tmp_path, capture)

    def cleanup(value: dict[str, object]) -> None:
        value["execution_policy"]["cleanup_mode"] = "permanent_delete"  # type: ignore[index]

    assert "EXECUTION_POLICY_INVALID" in _codes(tmp_path, cleanup)

    def permission(value: dict[str, object]) -> None:
        value["preflight"]["public_publish_or_invite_allowed"] = True  # type: ignore[index]

    assert "PREFLIGHT_INVALID" in _codes(tmp_path, permission)


@pytest.mark.unit
def test_manifest_rejects_bound_input_or_execution_claim_drift(tmp_path: Path) -> None:
    def digest(value: dict[str, object]) -> None:
        value["inputs"]["pending_approval_batch"]["sha256"] = "sha256:" + ("0" * 64)  # type: ignore[index]

    assert "BOUND_FILE_HASH_MISMATCH" in _codes(tmp_path, digest)

    def claim(value: dict[str, object]) -> None:
        value["claims"]["approval_received"] = True  # type: ignore[index]

    assert "CLAIMS_INVALID" in _codes(tmp_path, claim)
