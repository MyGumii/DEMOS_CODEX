"""Compile and verify the create-only UI-EDITOR-006 policy receipt."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from code_assist_v1.tools.notion_core_free_ui_approval_batch import (
    _canonical_bytes,
    _write_create_only,
)
from code_assist_v1.tools.verify_notion_core_free_ui_approval_batch_v3 import (
    CONTRACT as QUEUE_CONTRACT,
    DEFAULT_QUEUE,
    EXPECTED_ENTRY,
    THREAD_ID,
    verify_approval_batch_v3,
)
from code_assist_v1.tools.verify_notion_core_free_ui_completion import (
    PROFILE,
    RFC3339_PATTERN,
    _file_sha256,
    load_json,
)
from code_assist_v1.tools.verify_notion_core_free_ui_editor006_policy_proposal import (
    EXACT_APPROVAL_TEXT,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = "code_assist.notion.core_free_ui.approval_batch_receipt.v3"


def _valid_time(value: str) -> bool:
    if RFC3339_PATTERN.fullmatch(value) is None:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        return False


def _relative(path: Path) -> str:
    try:
        return path.resolve(strict=True).relative_to(REPO_ROOT.resolve(strict=True)).as_posix()
    except (OSError, ValueError) as exc:
        raise ValueError("approval queue must be an existing repository file") from exc


def compile_approval_batch_receipt_v3(
    *,
    approval_text: str,
    recorded_at: str,
    queue_path: Path = DEFAULT_QUEUE,
) -> dict[str, Any]:
    report, exit_code = verify_approval_batch_v3(queue_path)
    if exit_code != 0:
        raise ValueError(f"approval queue verification failed: {report['errors']}")
    if not _valid_time(recorded_at):
        raise ValueError("recorded_at must be an RFC 3339 timestamp with an offset")
    if approval_text != EXACT_APPROVAL_TEXT:
        raise ValueError("approval text does not exactly match the queued batch")
    queue = load_json(queue_path)
    if not isinstance(queue, dict) or queue.get("contract") != QUEUE_CONTRACT:
        raise ValueError("approval queue contract mismatch")
    entry = queue.get("entries", [None])[0]
    if not isinstance(entry, dict):
        raise ValueError("approval queue entry is invalid")
    actual = {key: entry.get(key) for key in EXPECTED_ENTRY}
    if actual != EXPECTED_ENTRY:
        raise ValueError("approval queue entry does not match the reviewed policy")
    return {
        "contract": CONTRACT,
        "recorded_at": recorded_at,
        "thread_id": THREAD_ID,
        "profile": PROFILE,
        "queue": {"path": _relative(queue_path), "sha256": _file_sha256(queue_path)},
        "source": {"kind": "exact_user_message", "exact_text": approval_text},
        "resolved_entries": [
            {
                "entry_id": EXPECTED_ENTRY["entry_id"],
                "kind": EXPECTED_ENTRY["kind"],
                "scenario_ids": EXPECTED_ENTRY["scenario_ids"],
                "authorization_effect": EXPECTED_ENTRY["authorization_effect"],
                "external_action_executed": False,
            }
        ],
        "safety": {
            "receipt_creation_only": True,
            "queue_mutated": False,
            "browser_control_used": False,
            "external_action_executed": False,
            "completion_matrix_mutated": False,
            "personal_workspace_access_allowed": False,
            "live_local_data_access_allowed": False,
        },
        "claims": {
            "resolved_entry_count": 1,
            "human_policy_approval_count": 1,
            "external_action_executed_count": 0,
            "core_ui_complete": False,
            "overall_goal_complete": False,
        },
    }


def verify_approval_batch_receipt_v3(
    receipt_path: Path,
    *,
    queue_path: Path = DEFAULT_QUEUE,
) -> tuple[dict[str, Any], int]:
    errors: list[str] = []
    try:
        receipt = load_json(receipt_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        receipt = None
        errors.append(f"receipt read failed: {exc}")
    if isinstance(receipt, dict):
        source = receipt.get("source")
        approval_text = source.get("exact_text") if isinstance(source, dict) else None
        recorded_at = receipt.get("recorded_at")
        if not isinstance(approval_text, str) or not isinstance(recorded_at, str):
            errors.append("receipt source or timestamp is invalid")
        else:
            try:
                expected = compile_approval_batch_receipt_v3(
                    approval_text=approval_text,
                    recorded_at=recorded_at,
                    queue_path=queue_path,
                )
            except ValueError as exc:
                errors.append(str(exc))
            else:
                if receipt != expected:
                    errors.append("receipt does not exactly match the canonical batch")
    elif receipt is not None:
        errors.append("receipt object required")
    report = {
        "contract": CONTRACT,
        "status": "fail" if errors else "pass",
        "profile": receipt.get("profile") if isinstance(receipt, dict) else None,
        "resolved_entry_count": (
            len(receipt.get("resolved_entries", []))
            if isinstance(receipt, dict) and isinstance(receipt.get("resolved_entries"), list)
            else 0
        ),
        "external_action_executed": (
            receipt.get("safety", {}).get("external_action_executed")
            if isinstance(receipt, dict) and isinstance(receipt.get("safety"), dict)
            else None
        ),
        "errors": errors,
    }
    return report, 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    compile_parser = sub.add_parser("compile")
    compile_parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    compile_parser.add_argument("--approval-text", required=True)
    compile_parser.add_argument("--recorded-at", required=True)
    compile_parser.add_argument("--output", type=Path, required=True)
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    verify_parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "compile":
        try:
            receipt = compile_approval_batch_receipt_v3(
                approval_text=args.approval_text,
                recorded_at=args.recorded_at,
                queue_path=args.queue,
            )
            disposition = _write_create_only(args.output, _canonical_bytes(receipt))
        except (OSError, ValueError) as exc:
            print(json.dumps({"contract": CONTRACT, "status": "fail", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
            return 1
        print(json.dumps({"contract": CONTRACT, "status": "pass", "disposition": disposition, "external_action_executed": False}, ensure_ascii=False, sort_keys=True))
        return 0
    report, exit_code = verify_approval_batch_receipt_v3(
        args.receipt,
        queue_path=args.queue,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
