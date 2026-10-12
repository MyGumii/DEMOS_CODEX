"""Verify the create-only UI-EDITOR-004 native IME evidence receipt."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from code_assist_v1.tools.verify_notion_core_free_ui_completion import (
    PROFILE,
    RFC3339_PATTERN,
    SHA_PATTERN,
    _file_sha256,
    _safe_repo_path,
    load_json,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECEIPT = (
    PACKAGE_ROOT
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "manual_ime_input_verified_receipt.20260822.json"
)
CONTRACT = "code_assist.notion.core_free_ui.manual_ime_input_verified_receipt.v1"
THREAD_ID = "019ff607-9116-78a1-9f40-c580c87c0038"
ENTRY_ID = "UI-EDITOR-004-NATIVE-IME-INPUT"
SCENARIO_ID = "UI-EDITOR-004"
REQUIRED_TEXT = "한글 조합 테스트 입력 후 UI-EDITOR-004 IME 입력 완료"
EXPECTED_FINAL_TEXT = "한글 조합 테스트"
EXPECTED_FIELDS = {
    "contract",
    "recorded_at",
    "thread_id",
    "profile",
    "entry_id",
    "scenario_id",
    "queue",
    "supersedes_failed_attempt",
    "source",
    "reference_observation",
    "disposition",
    "safety",
}


def _error(code: str, subject: str, message: str) -> dict[str, str]:
    return {"code": code, "subject": subject, "message": message}


def _exact_fields(
    value: Any, expected: set[str], subject: str, errors: list[dict[str, str]]
) -> bool:
    if not isinstance(value, dict):
        errors.append(_error("OBJECT_REQUIRED", subject, "object required"))
        return False
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    errors.extend(
        _error("FIELD_MISSING", f"{subject}.{key}", "required field missing")
        for key in missing
    )
    errors.extend(
        _error("UNKNOWN_FIELD", f"{subject}.{key}", "unknown field")
        for key in extra
    )
    return not missing and not extra


def _bound_file(
    value: Any,
    subject: str,
    errors: list[dict[str, str]],
    *,
    extra_fields: set[str] | None = None,
) -> Path | None:
    fields = {"path", "sha256"} | (extra_fields or set())
    if not _exact_fields(value, fields, subject, errors):
        return None
    path = _safe_repo_path(value.get("path"))
    digest = value.get("sha256")
    if path is None:
        errors.append(_error("PATH_INVALID", f"{subject}.path", "safe repository path required"))
        return None
    if not isinstance(digest, str) or not SHA_PATTERN.fullmatch(digest):
        errors.append(_error("SHA256_INVALID", f"{subject}.sha256", "canonical sha256 required"))
        return None
    if not path.is_file():
        errors.append(_error("BOUND_FILE_MISSING", f"{subject}.path", "bound file is missing"))
        return None
    if _file_sha256(path) != digest:
        errors.append(_error("BOUND_FILE_HASH_MISMATCH", subject, "bound file hash mismatch"))
    return path


def _valid_time(value: Any) -> bool:
    if not isinstance(value, str) or not RFC3339_PATTERN.fullmatch(value):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        return False


def verify_manual_ime_receipt(
    receipt_path: Path = DEFAULT_RECEIPT,
) -> tuple[dict[str, Any], int]:
    errors: list[dict[str, str]] = []
    try:
        receipt = load_json(receipt_path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": CONTRACT,
            "status": "fail",
            "manual_input_resolved": False,
            "errors": [_error("RECEIPT_READ_FAILED", "$", str(exc))],
        }, 1

    if not _exact_fields(receipt, EXPECTED_FIELDS, "$", errors):
        return {
            "contract": CONTRACT,
            "status": "fail",
            "manual_input_resolved": False,
            "errors": errors,
        }, 1

    expected_scalars = {
        "contract": CONTRACT,
        "thread_id": THREAD_ID,
        "profile": PROFILE,
        "entry_id": ENTRY_ID,
        "scenario_id": SCENARIO_ID,
    }
    for key, expected in expected_scalars.items():
        if receipt.get(key) != expected:
            errors.append(_error("VALUE_INVALID", key, f"expected {expected}"))
    if not _valid_time(receipt.get("recorded_at")):
        errors.append(_error("RECORDED_AT_INVALID", "recorded_at", "RFC 3339 timestamp required"))

    queue_path = _bound_file(receipt.get("queue"), "queue", errors)
    if queue_path is not None:
        try:
            queue = load_json(queue_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(_error("QUEUE_READ_FAILED", "queue", str(exc)))
        else:
            entries = queue.get("entries") if isinstance(queue, dict) else None
            matching = [
                item
                for item in entries or []
                if isinstance(item, dict) and item.get("entry_id") == ENTRY_ID
            ]
            if len(matching) != 1:
                errors.append(_error("QUEUE_ENTRY_INVALID", "queue", "exactly one IME entry required"))
            else:
                entry = matching[0]
                if entry.get("kind") != "manual_evidence_input" or entry.get("required_user_text") != REQUIRED_TEXT:
                    errors.append(_error("QUEUE_ENTRY_INVALID", "queue", "IME queue contract mismatch"))

    _bound_file(
        receipt.get("supersedes_failed_attempt"),
        "supersedes_failed_attempt",
        errors,
        extra_fields={"disposition"},
    )
    supersedes = receipt.get("supersedes_failed_attempt")
    if isinstance(supersedes, dict) and supersedes.get("disposition") != "preserved_as_failed_verification_attempt":
        errors.append(_error("SUPERSEDES_INVALID", "supersedes_failed_attempt.disposition", "failed attempt must remain preserved"))

    expected_source = {
        "kind": "exact_user_message",
        "received_text": REQUIRED_TEXT,
        "required_text_matched": True,
        "native_korean_ime_use_attested_by_user": True,
    }
    source = receipt.get("source")
    if not _exact_fields(source, set(expected_source), "source", errors) or source != expected_source:
        errors.append(_error("SOURCE_INVALID", "source", "exact manual input statement required"))

    expected_observation = {
        "workspace_alias": "REF-FREE-A",
        "expected_final_text": EXPECTED_FINAL_TEXT,
        "normalized_exact_count_immediate": 1,
        "normalized_exact_count_after_3_5_seconds": 1,
        "normalized_exact_count_after_reload": 1,
        "visible_text_after_reload": True,
        "duplicate_or_loss_observed": False,
        "round_trip_durable": True,
        "browser_control_observed_intermediate_composition_events": False,
        "browser_control_observed_intermediate_save_count": False,
    }
    observation = receipt.get("reference_observation")
    if not _exact_fields(observation, set(expected_observation), "reference_observation", errors) or observation != expected_observation:
        errors.append(_error("OBSERVATION_INVALID", "reference_observation", "exact durable single occurrence required"))

    expected_disposition = {
        "status": "accepted_native_ime_reference_observation",
        "manual_input_resolved": True,
        "observable_outcome_complete": True,
        "reference_summary_review_required": True,
        "differential_promotion_allowed": False,
    }
    disposition = receipt.get("disposition")
    if not _exact_fields(disposition, set(expected_disposition), "disposition", errors) or disposition != expected_disposition:
        errors.append(_error("DISPOSITION_INVALID", "disposition", "unreviewed observation disposition required"))

    expected_safety = {
        "synthetic_workspace_only": True,
        "personal_workspace_accessed": False,
        "raw_url_or_page_id_persisted": False,
        "raw_dom_network_cookie_or_storage_persisted": False,
        "screenshot_persisted_to_repository": False,
        "live_local_data_accessed": False,
        "external_publish_or_message_executed": False,
    }
    safety = receipt.get("safety")
    if not _exact_fields(safety, set(expected_safety), "safety", errors) or safety != expected_safety:
        errors.append(_error("SAFETY_INVALID", "safety", "safety boundary mismatch"))

    report = {
        "contract": CONTRACT,
        "status": "fail" if errors else "pass",
        "manual_input_resolved": not errors,
        "scenario_id": SCENARIO_ID,
        "reference_summary_review_required": not errors,
        "errors": errors,
    }
    return report, 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report, exit_code = verify_manual_ime_receipt(args.receipt)
    print(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            indent=None if args.compact else 2,
            separators=(",", ":") if args.compact else None,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
