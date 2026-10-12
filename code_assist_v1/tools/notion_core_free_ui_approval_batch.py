"""Compile and verify a create-only Core Free UI approval batch receipt.

This tool records an exact user approval phrase.  It never controls a browser,
changes the approval queue, or performs an external action.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any

from code_assist_v1.tools.verify_notion_core_free_ui_approval_batch import (
    CONTRACT as QUEUE_CONTRACT,
    DEFAULT_QUEUE,
    THREAD_ID,
    verify_approval_batch_queue,
)
from code_assist_v1.tools.verify_notion_core_free_ui_completion import (
    PROFILE,
    RFC3339_PATTERN,
    _file_sha256,
    load_json,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = "code_assist.notion.core_free_ui.approval_batch_receipt.v1"


def _valid_recorded_at(value: str) -> bool:
    if not RFC3339_PATTERN.fullmatch(value):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        return False


def _queue_relative_path(queue_path: Path) -> str:
    try:
        return queue_path.resolve(strict=True).relative_to(
            REPO_ROOT.resolve(strict=True)
        ).as_posix()
    except (OSError, ValueError) as exc:
        raise ValueError("approval queue must be an existing repository file") from exc


def compile_approval_batch_receipt(
    *,
    approval_text: str,
    recorded_at: str,
    queue_path: Path = DEFAULT_QUEUE,
) -> dict[str, Any]:
    report, exit_code = verify_approval_batch_queue(queue_path)
    if exit_code != 0:
        raise ValueError(f"approval queue verification failed: {report['errors']}")
    if not _valid_recorded_at(recorded_at):
        raise ValueError("recorded_at must be an RFC 3339 timestamp with an offset")

    queue = load_json(queue_path)
    if not isinstance(queue, dict) or queue.get("contract") != QUEUE_CONTRACT:
        raise ValueError("approval queue contract mismatch")
    batch = queue.get("batch_execution")
    entries = queue.get("entries")
    if not isinstance(batch, dict) or not isinstance(entries, list):
        raise ValueError("approval queue batch shape is invalid")
    if approval_text != batch.get("exact_user_text"):
        raise ValueError("approval text does not exactly match the queued batch")

    entries_by_id = {
        entry["entry_id"]: entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("entry_id"), str)
    }
    resolved_entries: list[dict[str, Any]] = []
    for entry_id in batch["entry_ids"]:
        entry = entries_by_id.get(entry_id)
        if entry is None:
            raise ValueError("approval batch references a missing entry")
        kind = entry["kind"]
        if kind == "human_evidence_acceptance":
            effect = "summary_receipt_sealing_allowed"
        elif kind == "destructive_external_action":
            effect = "action_time_confirmation_recorded_for_next_exact_action"
        else:
            raise ValueError("approval batch contains a non-approvable entry")
        resolved_entries.append(
            {
                "entry_id": entry_id,
                "kind": kind,
                "scenario_ids": entry["scenario_ids"],
                "authorization_effect": effect,
                "external_action_executed": False,
            }
        )

    return {
        "contract": CONTRACT,
        "recorded_at": recorded_at,
        "thread_id": THREAD_ID,
        "profile": PROFILE,
        "queue": {
            "path": _queue_relative_path(queue_path),
            "sha256": _file_sha256(queue_path),
        },
        "source": {
            "kind": "exact_user_message",
            "exact_text": approval_text,
        },
        "resolved_entries": resolved_entries,
        "excluded_entries": [batch["manual_input_excluded"]],
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
            "resolved_entry_count": len(resolved_entries),
            "human_evidence_approval_count": sum(
                item["kind"] == "human_evidence_acceptance"
                for item in resolved_entries
            ),
            "destructive_confirmation_count": sum(
                item["kind"] == "destructive_external_action"
                for item in resolved_entries
            ),
            "manual_input_resolved_count": 0,
            "external_action_executed_count": 0,
            "core_ui_complete": False,
            "overall_goal_complete": False,
        },
    }


def verify_approval_batch_receipt(
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
                expected = compile_approval_batch_receipt(
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

    return (
        {
            "contract": CONTRACT,
            "status": "fail" if errors else "pass",
            "profile": receipt.get("profile") if isinstance(receipt, dict) else None,
            "resolved_entry_count": (
                len(receipt.get("resolved_entries", []))
                if isinstance(receipt, dict)
                and isinstance(receipt.get("resolved_entries"), list)
                else 0
            ),
            "external_action_executed": (
                receipt.get("safety", {}).get("external_action_executed")
                if isinstance(receipt, dict)
                and isinstance(receipt.get("safety"), dict)
                else None
            ),
            "errors": errors,
        },
        1 if errors else 0,
    )


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _write_create_only(path: Path, payload: bytes) -> str:
    if not path.parent.is_dir():
        raise ValueError("output parent must already exist")
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
    except FileExistsError:
        if path.read_bytes() == payload:
            return "replayed"
        raise FileExistsError("output exists with different bytes") from None
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink(missing_ok=True)
        finally:
            raise
    return "created"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    compile_parser.add_argument("--approval-text", required=True)
    compile_parser.add_argument("--recorded-at", required=True)
    compile_parser.add_argument("--output", type=Path, required=True)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    verify_parser.add_argument("--receipt", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "compile":
        try:
            receipt = compile_approval_batch_receipt(
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

    report, exit_code = verify_approval_batch_receipt(
        args.receipt,
        queue_path=args.queue,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
