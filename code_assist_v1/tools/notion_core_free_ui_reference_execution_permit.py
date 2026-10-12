"""Compile a non-executing permit for the approved R1/R3 reference run."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from code_assist_v1.tools.notion_core_free_ui_pending_approval import (
    verify_pending_approval_receipt,
)
from code_assist_v1.tools.notion_parity.ui_reference_guard import (
    authorize_reference_action,
    reference_c0_decision_from_descriptor,
)
from code_assist_v1.tools.verify_notion_core_free_ui_completion import (
    PROFILE,
    RFC3339_PATTERN,
    _file_sha256,
    load_json,
)
from code_assist_v1.tools.verify_notion_core_free_ui_reference_completion_manifest import (
    DEFAULT_MANIFEST,
    verify_reference_completion_manifest,
)


CONTRACT = "code_assist.notion.core_free_ui.reference_execution_permit.v1"
MANIFEST_REPO_PATH = "code_assist_v1/docs/notion_parity/core_free_web_ui/reference_completion_execution_manifest.r1_r3.20260822.json"


def _valid_time(value: Any) -> bool:
    try:
        return (
            isinstance(value, str)
            and RFC3339_PATTERN.fullmatch(value) is not None
            and datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
        )
    except ValueError:
        return False


def compile_reference_execution_permit(
    *,
    receipt_path: Path,
    c0_decision_path: Path,
    recorded_at: str,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> dict[str, Any]:
    if not _valid_time(recorded_at):
        raise ValueError("recorded_at must be an RFC 3339 timestamp with an offset")
    receipt_report, exit_code = verify_pending_approval_receipt(
        receipt_path,
        manifest_path=manifest_path,
    )
    if exit_code != 0:
        raise ValueError("owner approval receipt is invalid: " + json.dumps(receipt_report, ensure_ascii=False))
    manifest_report, exit_code = verify_reference_completion_manifest(manifest_path)
    if exit_code != 0:
        raise ValueError("reference execution manifest is invalid: " + json.dumps(manifest_report, ensure_ascii=False))
    try:
        decision = reference_c0_decision_from_descriptor(load_json(c0_decision_path))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("C0 decision is invalid") from exc
    if decision.status != "pass":
        raise ValueError("C0 decision did not pass")
    manifest = load_json(manifest_path)
    authorizations: list[dict[str, Any]] = []
    for action in manifest["actions"]:
        authorization = authorize_reference_action(
            decision,
            scenario_id=action["scenario_id"],
            action_id=action["action_id"],
            workspace_alias=action["workspace"],
            mutation_class=action["mutation_class"],
            synthetic_fixture_confirmed=action["mutation_class"] == "synthetic_mutation",
            opens_personal_workspace=False,
            creates_external_side_effect=False,
        )
        if not authorization.allowed:
            raise ValueError(f"reference action refused: {authorization.reason_code}")
        authorizations.append(authorization.descriptor())
    return {
        "contract": CONTRACT,
        "recorded_at": recorded_at,
        "profile": PROFILE,
        "approval_receipt_sha256": _file_sha256(receipt_path),
        "execution_manifest": {
            "path": MANIFEST_REPO_PATH,
            "sha256": _file_sha256(manifest_path),
        },
        "c0": {
            "run_id": decision.run_id,
            "decision_sha256": decision.sha256(),
            "status": "pass",
        },
        "action_authorizations": authorizations,
        "cleanup_authorization": {
            "allowed": True,
            "mode": "trash_only_recoverable",
            "target_aliases": ["UI-PARITY-R1R3-A-ROOT", "UI-PARITY-R1R3-B-MARKER"],
            "permanent_delete_allowed": False,
        },
        "claims": {
            "permit_compiled": True,
            "reference_mutation_executed": False,
            "cleanup_executed": False,
            "completion_matrix_changed": False,
            "production_or_live_change": False,
            "core_ui_complete": False,
            "overall_goal_complete": False,
        },
    }


__all__ = ["CONTRACT", "compile_reference_execution_permit"]
