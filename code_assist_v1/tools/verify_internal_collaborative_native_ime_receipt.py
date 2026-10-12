"""Verify the E1-P3 human native-IME finalization receipt offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUEST_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "evidence"
    / "internal_collaboration_operator"
    / "e1-p3-native-ime-manual-request-20260825.json"
)
RECEIPT_PATH = REQUEST_PATH.with_name("e1-p3-native-ime-finalization-20260825.json")
EXPECTED_CONTRACT = "code_assist.notion.e1_p3_native_ime_finalization.v1"
EXPECTED_CONFIRMATION = "E1-P3 네이티브 IME 입력 완료"
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
EXPECTED_TOP_LEVEL_FIELDS = {
    "schema_version",
    "contract",
    "status",
    "approval_id",
    "request_id",
    "manual_request_sha256",
    "test_page_locator_sha256",
    "user_confirmation_sha256",
    "browser",
    "native_ime_action_performed_by_human",
    "automated_text_input_performed",
    "principal_count",
    "same_projection_hash",
    "required_text_occurrence_count",
    "final_projection_sha256",
    "unsynced_count",
    "page_error_count",
    "test_page_moved_to_trash",
    "credential_or_session_value_recorded",
    "production_10011_10012_changed",
    "duration_ms",
    "product_complete",
}


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _load(path: Path) -> Any:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {value}")
        ),
    )


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def verify(path: Path = RECEIPT_PATH) -> dict[str, Any]:
    errors: list[dict[str, str]] = []

    def check(condition: bool, code: str, subject: str, message: str) -> None:
        if not condition:
            errors.append({"code": code, "subject": subject, "message": message})

    try:
        value = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": "code_assist.notion.e1_p3_native_ime_finalization.audit.v1",
            "status": "fail",
            "error_count": 1,
            "errors": [
                {"code": "RECEIPT_READ_FAILED", "subject": str(path), "message": str(exc)}
            ],
            "native_ime_verified": False,
            "gate_passed": False,
            "product_complete": False,
        }
    check(isinstance(value, dict), "RECEIPT_SHAPE", "$", "receipt must be an object")
    if not isinstance(value, dict):
        return {"status": "fail", "error_count": len(errors), "errors": errors}
    check(
        set(value) == EXPECTED_TOP_LEVEL_FIELDS,
        "RECEIPT_FIELD_DENOMINATOR",
        "$",
        "exact redacted receipt field set required",
    )
    check(
        value.get("schema_version") == 1
        and value.get("contract") == EXPECTED_CONTRACT
        and value.get("status") == "pass"
        and value.get("approval_id") == "AP-DEV-PAIR"
        and value.get("request_id") == "E1-P3-NATIVE-IME-MANUAL-20260825",
        "RECEIPT_IDENTITY",
        "$",
        "native IME receipt identity drift",
    )
    check(
        REQUEST_PATH.is_file()
        and value.get("manual_request_sha256") == _sha256_file(REQUEST_PATH),
        "REQUEST_HASH_DRIFT",
        "$.manual_request_sha256",
        "receipt is not bound to the immutable manual request",
    )
    expected_confirmation_hash = "sha256:" + hashlib.sha256(
        EXPECTED_CONFIRMATION.encode("utf-8")
    ).hexdigest()
    check(
        value.get("user_confirmation_sha256") == expected_confirmation_hash,
        "CONFIRMATION_HASH_DRIFT",
        "$.user_confirmation_sha256",
        "exact human completion phrase hash required",
    )
    check(
        isinstance(value.get("test_page_locator_sha256"), str)
        and SHA256_RE.fullmatch(value["test_page_locator_sha256"]) is not None
        and isinstance(value.get("final_projection_sha256"), str)
        and SHA256_RE.fullmatch(value["final_projection_sha256"]) is not None,
        "HASH_FORMAT",
        "$",
        "page locator and final projection require canonical SHA-256 values",
    )
    browser = value.get("browser", {})
    check(
        isinstance(browser, dict)
        and set(browser)
        == {
            "engine",
            "channel",
            "version",
            "headless_verification_only",
            "independent_ephemeral_contexts",
            "browser_closed",
        }
        and browser.get("engine") == "chromium"
        and browser.get("channel") == "chrome"
        and isinstance(browser.get("version"), str)
        and bool(browser.get("version"))
        and browser.get("headless_verification_only") is True
        and browser.get("independent_ephemeral_contexts") == 2
        and browser.get("browser_closed") is True,
        "BROWSER_BOUNDARY",
        "$.browser",
        "two closed independent Chrome verification contexts required",
    )
    check(
        value.get("native_ime_action_performed_by_human") is True
        and value.get("automated_text_input_performed") is False
        and value.get("principal_count") == 2
        and value.get("same_projection_hash") is True
        and value.get("required_text_occurrence_count") == 1
        and value.get("unsynced_count") == 0
        and value.get("page_error_count") == 0,
        "NATIVE_IME_CONVERGENCE_DENOMINATOR",
        "$",
        "human input, exact occurrence, two-principal convergence, and clean client state required",
    )
    check(
        value.get("test_page_moved_to_trash") is True
        and value.get("credential_or_session_value_recorded") is False
        and value.get("production_10011_10012_changed") is False
        and isinstance(value.get("duration_ms"), int)
        and not isinstance(value.get("duration_ms"), bool)
        and value.get("duration_ms", -1) >= 0
        and value.get("product_complete") is False,
        "CLEANUP_AND_FALSE_GREEN_BOUNDARY",
        "$",
        "cleanup, redaction, production isolation, and non-completion claims required",
    )
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raw = ""
    check(
        all(secret not in raw for secret in ("admin1234", "user1234")),
        "SECRET_VALUE_RECORDED",
        str(path),
        "receipt contains a forbidden credential or session value",
    )

    normalized = sorted(
        {(item["code"], item["subject"], item["message"]): item for item in errors}.values(),
        key=lambda item: (item["code"], item["subject"], item["message"]),
    )
    return {
        "contract": "code_assist.notion.e1_p3_native_ime_finalization.audit.v1",
        "status": "pass" if not normalized else "fail",
        "error_count": len(normalized),
        "errors": normalized,
        "receipt_sha256": _sha256_file(path),
        "native_ime_verified": not normalized,
        "gate_passed": not normalized,
        "product_complete": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, default=RECEIPT_PATH)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    report = verify(args.receipt)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=None if args.compact else 2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
