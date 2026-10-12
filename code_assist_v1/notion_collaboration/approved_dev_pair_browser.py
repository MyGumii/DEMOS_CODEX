"""Actual Chrome two-context isolation gate for the approved development pair."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from typing import Mapping, Sequence
from urllib.parse import urlsplit

from .approved_dev_pair import AGENT_PORT, NOTION_PORT, ApprovedDevPairError


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path not in {"", "/"}:
        raise ApprovedDevPairError("BROWSER_ORIGIN_INVALID")
    if parsed.port not in {AGENT_PORT, NOTION_PORT}:
        raise ApprovedDevPairError("BROWSER_ORIGIN_INVALID")
    return f"http://127.0.0.1:{parsed.port}"


def run_approved_dev_pair_browser_isolation(
    *,
    agent_origin: str,
    notion_origin: str,
    credentials: Mapping[str, str],
) -> dict[str, object]:
    """Prove two simultaneous principals without persisting browser state."""

    agent = _origin(agent_origin)
    notion = _origin(notion_origin)
    if agent == notion or set(credentials) != {"admin", "user01"}:
        raise ApprovedDevPairError("BROWSER_PRINCIPALS_INVALID")
    if any(not isinstance(value, str) or not value for value in credentials.values()):
        raise ApprovedDevPairError("BROWSER_CREDENTIALS_INVALID")

    from playwright.sync_api import sync_playwright

    started = time.monotonic()
    contexts = []
    browser = None
    browser_closed = False
    requests: list[str] = []
    observed_subjects: dict[str, tuple[str, str]] = {}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            browser_version = browser.version
            user_agent_sha256 = ""
            for principal in ("admin", "user01"):
                context = browser.new_context(locale="ko-KR", service_workers="block")
                contexts.append(context)
                page = context.new_page()
                page.on("request", lambda request: requests.append(request.url))
                page.goto(agent + "/login", wait_until="domcontentloaded")
                page.locator("#userId").fill(principal)
                page.locator("#password").fill(credentials[principal])
                page.locator("#submitBtn").click()
                page.wait_for_url(agent + "/", wait_until="domcontentloaded")
                agent_me = page.evaluate(
                    "async () => await fetch('/api/code/auth/me').then(response => response.json())"
                )
                if agent_me.get("user", {}).get("id") != principal:
                    raise ApprovedDevPairError("BROWSER_AGENT_SUBJECT_MISMATCH")
                page.goto(notion + "/workspace", wait_until="domcontentloaded")
                notion_me = page.evaluate(
                    "async () => await fetch('/api/code/auth/me').then(response => response.json())"
                )
                if notion_me.get("user", {}).get("id") != principal:
                    raise ApprovedDevPairError("BROWSER_NOTION_SUBJECT_MISMATCH")
                page.wait_for_function(
                    "() => document.querySelector('#codeAgentLink')?.href.endsWith(':10111/')"
                )
                if user_agent_sha256 == "":
                    user_agent = page.evaluate("() => navigator.userAgent")
                    user_agent_sha256 = "sha256:" + hashlib.sha256(
                        str(user_agent).encode("utf-8")
                    ).hexdigest()
                observed_subjects[principal] = (
                    str(agent_me["user"]["id"]),
                    str(notion_me["user"]["id"]),
                )

            for index, principal in enumerate(("admin", "user01")):
                page = contexts[index].pages[0]
                page.goto(notion + "/workspace", wait_until="domcontentloaded")
                current = page.evaluate(
                    "async () => await fetch('/api/code/auth/me').then(response => response.json())"
                )
                if current.get("user", {}).get("id") != principal:
                    raise ApprovedDevPairError("BROWSER_CONTEXT_CROSSOVER")

            observer = browser.new_context(locale="ko-KR", service_workers="block")
            contexts.append(observer)
            observer_page = observer.new_page()
            observer_page.on("request", lambda request: requests.append(request.url))
            observer_page.goto(notion + "/workspace", wait_until="domcontentloaded")
            if urlsplit(observer_page.url).path != "/login":
                raise ApprovedDevPairError("BROWSER_ANONYMOUS_CONTEXT_NOT_EMPTY")

            allowed_origins = {agent, notion}
            approved_static_dependencies = {"https://cdn.jsdelivr.net"}
            approved_static_dependency_requests = sum(
                1
                for value in requests
                if (parsed := urlsplit(value)).scheme in {"http", "https"}
                and f"{parsed.scheme}://{parsed.netloc}" in approved_static_dependencies
            )
            escaped = sorted(
                {
                    f"{parsed.scheme}://{parsed.netloc}"
                    for value in requests
                    if (parsed := urlsplit(value)).scheme in {"http", "https"}
                    and f"{parsed.scheme}://{parsed.netloc}"
                    not in allowed_origins | approved_static_dependencies
                }
            )
            if escaped:
                raise ApprovedDevPairError(
                    "BROWSER_NETWORK_TARGET_ESCAPE:" + ",".join(escaped)
                )
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
    if not browser_closed:
        raise ApprovedDevPairError("BROWSER_PROCESS_CLEANUP_FAILED")
    return {
        "schema_version": 1,
        "contract": "code_assist.notion.approved_dev_pair_two_browser_receipt.v1",
        "status": "pass",
        "browser": {
            "engine": "chromium",
            "channel": "chrome",
            "version": browser_version,
            "headless": True,
            "independent_ephemeral_contexts": 3,
            "browser_closed": browser_closed,
            "user_agent_sha256": user_agent_sha256,
        },
        "principal_ids": sorted(observed_subjects),
        "principal_count": len(observed_subjects),
        "same_subject_agent_to_notion": all(
            principal == subjects[0] == subjects[1]
            for principal, subjects in observed_subjects.items()
        ),
        "cross_context_subject_crossover_count": 0,
        "anonymous_observer_authenticated": False,
        "dynamic_pair_links_verified": True,
        "network_request_count": len(requests),
        "approved_external_static_dependency_request_count": approved_static_dependency_requests,
        "unapproved_network_target_escape_count": 0,
        "persistent_or_signed_in_profile_used": False,
        "browser_cookie_or_storage_value_recorded": False,
        "plaintext_password_hash_secret_or_session_recorded": False,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


__all__ = ["run_approved_dev_pair_browser_isolation"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval-id", required=True)
    args = parser.parse_args(argv)
    if args.approval_id != "AP-DEV-PAIR":
        raise ApprovedDevPairError("AP_DEV_PAIR_REQUIRED")
    receipt = run_approved_dev_pair_browser_isolation(
        agent_origin="http://127.0.0.1:10111",
        notion_origin="http://127.0.0.1:10112",
        credentials={"admin": "admin1234", "user01": "user1234"},
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - operator gate entry point
    raise SystemExit(main())
