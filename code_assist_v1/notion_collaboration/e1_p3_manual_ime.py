"""Finalize E1-P3 native Korean IME evidence in the approved dev pair.

The human performs the operating-system IME action.  This harness never types
the requested text; it only verifies durable two-principal convergence and then
moves the synthetic page to Trash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

from .approved_dev_pair import ApprovedDevPairError
from .e1_p2_browser import _api, _login, _open_collaborative_page, _operation


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REQUEST_PATH = (
    PROJECT_ROOT
    / "docs"
    / "notion_parity"
    / "evidence"
    / "internal_collaboration_operator"
    / "e1-p3-native-ime-manual-request-20260825.json"
)
REQUEST_CONTRACT = "code_assist.notion.e1_p3_native_ime_manual_request.v1"
RECEIPT_CONTRACT = "code_assist.notion.e1_p3_native_ime_finalization.v1"
EXPECTED_CONFIRMATION = "E1-P3 네이티브 IME 입력 완료"
EXPECTED_INPUT = "한글 조합 테스트"
EXPECTED_TITLE = "E1-P3 네이티브 IME 검증"
APPROVED_AGENT_ORIGIN = "http://127.0.0.1:10111"
APPROVED_NOTION_ORIGIN = "http://127.0.0.1:10112"


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


def validate_request(path: Path = REQUEST_PATH) -> dict[str, str]:
    try:
        value = _load(path)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ApprovedDevPairError("E1_P3_IME_REQUEST_INVALID") from exc
    exact = {
        "contract": REQUEST_CONTRACT,
        "request_id": "E1-P3-NATIVE-IME-MANUAL-20260825",
        "packet_id": "E1-P3",
        "status": "ready_for_single_manual_input",
        "approval_id": "AP-DEV-PAIR",
        "development_origin": APPROVED_NOTION_ORIGIN,
        "approved_subject_id": "admin",
        "page_title": EXPECTED_TITLE,
        "required_input": EXPECTED_INPUT,
        "required_user_completion_text": EXPECTED_CONFIRMATION,
    }
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ApprovedDevPairError("E1_P3_IME_REQUEST_INVALID")
    if any(value.get(key) != expected for key, expected in exact.items()):
        raise ApprovedDevPairError("E1_P3_IME_REQUEST_DRIFT")
    for key in ("workspace_id", "page_id"):
        item = value.get(key)
        if not isinstance(item, str) or not item or len(item) > 160:
            raise ApprovedDevPairError("E1_P3_IME_REQUEST_SCOPE_INVALID")
    boundary = value.get("evidence_boundary", {})
    cleanup = value.get("cleanup", {})
    claims = value.get("claims", {})
    if not (
        isinstance(boundary, dict)
        and boundary.get("synthetic_development_page_only") is True
        and boundary.get("personal_content_forbidden") is True
        and boundary.get("automated_fill_or_synthetic_composition_is_not_native_ime_evidence") is True
        and boundary.get("server_convergence_and_cleanup_are_verified_after_confirmation") is True
        and isinstance(cleanup, dict)
        and cleanup.get("agent_moves_the_synthetic_page_to_trash_after_verification") is True
        and cleanup.get("production_10011_10012_changed") is False
        and isinstance(claims, dict)
        and claims.get("native_ime_verified") is False
        and claims.get("E1_P3_gate_passed") is False
        and claims.get("E1_phase_gate_closed") is False
        and claims.get("product_complete") is False
    ):
        raise ApprovedDevPairError("E1_P3_IME_REQUEST_FALSE_GREEN")
    return {
        "workspace_id": value["workspace_id"],
        "page_id": value["page_id"],
        "required_input": value["required_input"],
    }


def validate_confirmation(value: str) -> str:
    if value != EXPECTED_CONFIRMATION:
        raise ApprovedDevPairError("E1_P3_NATIVE_IME_CONFIRMATION_REQUIRED")
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _projection_state(page: Any) -> dict[str, Any]:
    value = page.evaluate(
        """() => ({
            text: (window.NotionCollaborativeEditor.projection()?.blocks || [])
                .map(block => block.content || '').join('\\n'),
            descriptor: window.NotionCollaborativeEditor.descriptor(),
            connected: State.liveCollaboration.connected,
            crdt_ready: State.liveCollaboration.crdtReady,
            unsynced_count: State.liveCollaboration.unsyncedCount,
            recovery_only: State.liveCollaboration.recoveryOnly,
            offline_policy_blocked: State.liveCollaboration.offlinePolicyBlocked,
        })"""
    )
    if not isinstance(value, dict):
        raise ApprovedDevPairError("E1_P3_NATIVE_IME_PROJECTION_INVALID")
    return value


def run_finalization(
    *,
    user_confirmation: str,
    request_path: Path = REQUEST_PATH,
    credentials: Mapping[str, str],
) -> dict[str, object]:
    """Verify the human-entered text in two clients and clean the test page."""

    confirmation_sha256 = validate_confirmation(user_confirmation)
    request = validate_request(request_path)
    manual_request_sha256 = "sha256:" + hashlib.sha256(request_path.read_bytes()).hexdigest()
    test_page_locator_sha256 = "sha256:" + hashlib.sha256(
        (request["workspace_id"] + "\n" + request["page_id"]).encode("utf-8")
    ).hexdigest()
    if set(credentials) != {"admin", "user01"} or any(not item for item in credentials.values()):
        raise ApprovedDevPairError("E1_P3_NATIVE_IME_PRINCIPALS_INVALID")
    from playwright.sync_api import sync_playwright

    started = time.monotonic()
    page_errors: list[str] = []
    cleaned_to_trash = False
    browser_closed = False
    final_text = ""
    browser = None
    contexts: list[Any] = []
    admin_page = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            browser_version = browser.version
            for _index in range(2):
                context = browser.new_context(
                    locale="ko-KR",
                    service_workers="block",
                    viewport={"width": 1440, "height": 900},
                )
                context.set_default_timeout(20_000)
                contexts.append(context)
            admin_page = contexts[0].new_page()
            user_page = contexts[1].new_page()
            admin_page.on("pageerror", lambda error: page_errors.append(str(error)))
            user_page.on("pageerror", lambda error: page_errors.append(str(error)))
            _login(
                admin_page,
                APPROVED_AGENT_ORIGIN,
                APPROVED_NOTION_ORIGIN,
                "admin",
                credentials["admin"],
            )
            _login(
                user_page,
                APPROVED_AGENT_ORIGIN,
                APPROVED_NOTION_ORIGIN,
                "user01",
                credentials["user01"],
            )
            workspace_id = request["workspace_id"]
            page_id = request["page_id"]
            collaboration = _api(
                admin_page,
                f"/api/notion/workspaces/{workspace_id}/pages/{page_id}/collaboration",
            )
            shares = _api(
                admin_page,
                f"/api/notion/workspaces/{workspace_id}/pages/{page_id}/shares",
            )
            items = shares.get("items") or shares.get("shares") or []
            if not any(
                isinstance(item, dict)
                and item.get("principal_type") == "subject"
                and item.get("principal_id") == "user01"
                for item in items
            ):
                _api(
                    admin_page,
                    f"/api/notion/workspaces/{workspace_id}/pages/{page_id}/shares",
                    method="POST",
                    body={
                        "principal_type": "subject",
                        "principal_id": "user01",
                        "role": "edit",
                        "inherit_to_descendants": False,
                        "expected_workspace_epoch": collaboration["page"]["workspace_epoch"],
                        "expected_document_epoch": collaboration["page"]["document_epoch"],
                        "operation_id": _operation("e1p3-ime-share"),
                    },
                )
            _open_collaborative_page(admin_page, workspace_id, page_id)
            _open_collaborative_page(user_page, workspace_id, page_id)
            for page in (admin_page, user_page):
                page.wait_for_function(
                    """required => {
                        const text = (window.NotionCollaborativeEditor.projection()?.blocks || [])
                            .map(block => block.content || '').join('\\n');
                        return State.liveCollaboration.connected
                            && State.liveCollaboration.crdtReady
                            && State.liveCollaboration.unsyncedCount === 0
                            && !State.liveCollaboration.recoveryOnly
                            && !State.liveCollaboration.offlinePolicyBlocked
                            && text.includes(required);
                    }""",
                    arg=request["required_input"],
                )
            states = [_projection_state(admin_page), _projection_state(user_page)]
            texts = [str(item.get("text") or "") for item in states]
            if len(set(texts)) != 1:
                raise ApprovedDevPairError("E1_P3_NATIVE_IME_CONVERGENCE_FAILED")
            final_text = texts[0]
            if final_text.count(request["required_input"]) != 1:
                raise ApprovedDevPairError("E1_P3_NATIVE_IME_DUPLICATE_OR_MISSING")
            if any(
                item.get("connected") is not True
                or item.get("crdt_ready") is not True
                or item.get("unsynced_count") != 0
                or item.get("recovery_only") is not False
                or item.get("offline_policy_blocked") is not False
                for item in states
            ):
                raise ApprovedDevPairError("E1_P3_NATIVE_IME_CLIENT_NOT_DURABLE")
            if page_errors:
                raise ApprovedDevPairError("E1_P3_NATIVE_IME_PAGE_ERROR")
            current = _api(admin_page, f"/api/notion/workspaces/{workspace_id}")["workspace"]
            _api(
                admin_page,
                f"/api/notion/workspaces/{workspace_id}/pages/{page_id}/trash",
                method="POST",
                body={
                    "base_version": current["version"],
                    "operation_id": _operation("e1p3-ime-trash"),
                },
            )
            cleaned_to_trash = True
            for context in reversed(contexts):
                context.close()
            contexts.clear()
            browser.close()
            browser_closed = not browser.is_connected()
            browser = None
    finally:
        for context in reversed(contexts):
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
                browser_closed = not browser.is_connected()
            except Exception:
                browser_closed = False
    if not cleaned_to_trash or not browser_closed:
        raise ApprovedDevPairError("E1_P3_NATIVE_IME_CLEANUP_FAILED")
    return {
        "schema_version": 1,
        "contract": RECEIPT_CONTRACT,
        "status": "pass",
        "approval_id": "AP-DEV-PAIR",
        "request_id": "E1-P3-NATIVE-IME-MANUAL-20260825",
        "manual_request_sha256": manual_request_sha256,
        "test_page_locator_sha256": test_page_locator_sha256,
        "user_confirmation_sha256": confirmation_sha256,
        "browser": {
            "engine": "chromium",
            "channel": "chrome",
            "version": browser_version,
            "headless_verification_only": True,
            "independent_ephemeral_contexts": 2,
            "browser_closed": browser_closed,
        },
        "native_ime_action_performed_by_human": True,
        "automated_text_input_performed": False,
        "principal_count": 2,
        "same_projection_hash": True,
        "required_text_occurrence_count": 1,
        "final_projection_sha256": "sha256:"
        + hashlib.sha256(final_text.encode("utf-8")).hexdigest(),
        "unsynced_count": 0,
        "page_error_count": len(page_errors),
        "test_page_moved_to_trash": cleaned_to_trash,
        "credential_or_session_value_recorded": False,
        "production_10011_10012_changed": False,
        "duration_ms": int((time.monotonic() - started) * 1_000),
        "product_complete": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--user-confirmation", required=True)
    parser.add_argument("--request", type=Path, default=REQUEST_PATH)
    args = parser.parse_args(argv)
    if args.approval_id != "AP-DEV-PAIR":
        raise ApprovedDevPairError("AP_DEV_PAIR_REQUIRED")
    receipt = run_finalization(
        user_confirmation=args.user_confirmation,
        request_path=args.request,
        credentials={"admin": "admin1234", "user01": "user1234"},
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())


__all__ = [
    "EXPECTED_CONFIRMATION",
    "EXPECTED_INPUT",
    "REQUEST_PATH",
    "run_finalization",
    "validate_confirmation",
    "validate_request",
]
