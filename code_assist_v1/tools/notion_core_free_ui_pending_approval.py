"""Compile or verify the R1/R3 synthetic-reference approval receipt."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from code_assist_v1.tools.verify_notion_core_free_ui_completion import (
    PROFILE,
    RFC3339_PATTERN,
    _file_sha256,
    load_json,
)
from code_assist_v1.tools.verify_notion_core_free_ui_pending_approval_batch import (
    DEFAULT_BATCH,
    EXACT_APPROVAL,
    verify_pending_approval_batch,
)
from code_assist_v1.tools.verify_notion_core_free_ui_reference_completion_manifest import (
    DEFAULT_MANIFEST,
    verify_reference_completion_manifest,
)


CONTRACT = "code_assist.notion.core_free_ui.pending_approval_receipt.v1"
BATCH_REPO_PATH = "code_assist_v1/docs/notion_parity/core_free_web_ui/pending_approval_batch.r1_r3.20260822.json"
MANIFEST_REPO_PATH = "code_assist_v1/docs/notion_parity/core_free_web_ui/reference_completion_execution_manifest.r1_r3.20260822.json"
ITEM_IDS = ["UI-R1-R3-REFERENCE-COMPLETION", "UI-R1-R3-DIFFERENTIAL-MATRIX"]
SCENARIO_IDS = ["UI-SHELL-001", "UI-EDITOR-001", "UI-EDITOR-002", "UI-SEARCH-002"]
WORKSPACES = ["REF-FREE-A", "REF-FREE-B"]
DIFFERENTIAL_CONDITION = (
    "all affected reference and current-local normalized captures validate and every differential reports "
    "zero observable differences"
)


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _valid_time(value: Any) -> bool:
    try:
        return (
            isinstance(value, str)
            and RFC3339_PATTERN.fullmatch(value) is not None
            and datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
        )
    except ValueError:
        return False


def _approval_sha256(text: str) -> str:
    envelope = b"code_assist.notion.core_free_ui.pending_approval.v1\x00" + text.encode("utf-8")
    return "sha256:" + hashlib.sha256(envelope).hexdigest()


def compile_pending_approval_receipt(
    *,
    approval_text: str,
    recorded_at: str,
    batch_path: Path = DEFAULT_BATCH,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> dict[str, Any]:
    report, exit_code = verify_pending_approval_batch(batch_path)
    if exit_code != 0:
        raise ValueError("pending approval batch is invalid: " + json.dumps(report, ensure_ascii=False))
    report, exit_code = verify_reference_completion_manifest(manifest_path)
    if exit_code != 0:
        raise ValueError("reference completion manifest is invalid: " + json.dumps(report, ensure_ascii=False))
    if approval_text != EXACT_APPROVAL:
        raise ValueError("approval text does not exactly match the pending batch")
    if not _valid_time(recorded_at):
        raise ValueError("recorded_at must be an RFC 3339 timestamp with an offset")
    return {
        "contract": CONTRACT,
        "recorded_at": recorded_at,
        "profile": PROFILE,
        "batch": {"path": BATCH_REPO_PATH, "sha256": _file_sha256(batch_path)},
        "execution_manifest": {"path": MANIFEST_REPO_PATH, "sha256": _file_sha256(manifest_path)},
        "approval": {
            "exact_user_text": approval_text,
            "approval_sha256": _approval_sha256(approval_text),
            "status": "owner_approved",
        },
        "authorization": {
            "item_ids": ITEM_IDS,
            "scenario_ids": SCENARIO_IDS,
            "workspaces": WORKSPACES,
            "synthetic_only": True,
            "cleanup": "trash_only_recoverable",
            "external_reference_mutation_authorized": True,
            "conditional_differential_matrix_update_authorized": True,
            "conditional_differential_matrix_update_condition": DIFFERENTIAL_CONDITION,
            "personal_workspace_access_allowed": False,
            "public_publish_or_invite_allowed": False,
            "permanent_delete_allowed": False,
            "production_promotion_allowed": False,
        },
        "claims": {
            "reference_mutation_executed": False,
            "completion_matrix_changed": False,
            "production_or_live_change": False,
            "core_ui_complete": False,
            "overall_goal_complete": False,
        },
    }


def verify_pending_approval_receipt(
    receipt_path: Path,
    *,
    batch_path: Path = DEFAULT_BATCH,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> tuple[dict[str, Any], int]:
    try:
        value = load_json(receipt_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {"contract": CONTRACT, "status": "fail", "errors": [_error("RECEIPT_READ_FAILED", "$", str(exc))]}, 1
    if not isinstance(value, dict):
        return {"contract": CONTRACT, "status": "fail", "errors": [_error("OBJECT_REQUIRED", "$", "object required")]}, 1
    approval = value.get("approval")
    approval_text = approval.get("exact_user_text") if isinstance(approval, dict) else None
    recorded_at = value.get("recorded_at")
    try:
        expected = compile_pending_approval_receipt(
            approval_text=approval_text,
            recorded_at=recorded_at,
            batch_path=batch_path,
            manifest_path=manifest_path,
        )
    except (TypeError, ValueError) as exc:
        return {
            "contract": CONTRACT,
            "status": "fail",
            "errors": [_error("RECEIPT_CONTRACT_INVALID", "$", str(exc))],
        }, 1
    errors: list[dict[str, str]] = []
    if value != expected:
        errors.append(_error("RECEIPT_MISMATCH", "$", "receipt differs from the exact compiled contract"))
    return {
        "contract": CONTRACT,
        "status": "fail" if errors else "pass",
        "external_reference_mutation_authorized": not errors,
        "reference_mutation_executed": False,
        "errors": errors,
    }, 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval-text")
    parser.add_argument("--recorded-at")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    if args.receipt is not None:
        if args.approval_text is not None or args.recorded_at is not None:
            parser.error("--receipt cannot be combined with compilation arguments")
        value, exit_code = verify_pending_approval_receipt(args.receipt)
    else:
        if args.approval_text is None or args.recorded_at is None:
            parser.error("compilation requires --approval-text and --recorded-at")
        try:
            value = compile_pending_approval_receipt(
                approval_text=args.approval_text,
                recorded_at=args.recorded_at,
            )
            exit_code = 0
        except ValueError as exc:
            value = {"contract": CONTRACT, "status": "fail", "errors": [_error("COMPILE_REFUSED", "$", str(exc))]}
            exit_code = 1
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.compact else 2,
            separators=(",", ":") if args.compact else None,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
