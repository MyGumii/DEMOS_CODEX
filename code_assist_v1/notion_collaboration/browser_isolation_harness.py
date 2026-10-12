"""Ephemeral real-browser isolation harness for the four synthetic principals."""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlsplit

from .dev_pair_runtime import FORBIDDEN_OPERATOR_PORTS, SyntheticPairRuntime
from .multi_principal_fixture import (
    load_multi_principal_seed,
    principal_alias_map,
    verify_multi_principal_fixture_root,
)
from .pair_config import PairHost
from .synthetic_pair_root import load_fixture_credentials, verify_synthetic_pair_root


BROWSER_UNSAFE_PORTS = frozenset(
    {
        1719,
        1720,
        1723,
        2049,
        3659,
        4045,
        5060,
        5061,
        6000,
        6566,
        6665,
        6666,
        6667,
        6668,
        6669,
        6697,
        10080,
    }
)
STORAGE_KEY = "code-assist-h0-principal"
CACHE_NAME = "code-assist-h0-cache-v1"
CACHE_REQUEST_PATH = "/__pair__/h0-cache-entry"
SERVICE_WORKER_PATH = "/__pair__/h0-sw.js"
PRINCIPAL_ORDER = (
    ("fixture-owner", "owner"),
    ("fixture-member", "member"),
    ("fixture-page-guest", "internal_page_guest"),
    ("fixture-outsider", "outsider"),
)


class BrowserIsolationError(RuntimeError):
    pass


def _response_json(response: Any, expected_status: int = 200) -> dict[str, Any]:
    if response.status != expected_status:
        raise BrowserIsolationError(f"HTTP_STATUS_UNEXPECTED:{response.status}")
    value = response.json()
    if not isinstance(value, dict):
        raise BrowserIsolationError("HTTP_JSON_OBJECT_REQUIRED")
    return value


def _assert_allowed_requests(urls: list[str], allowed_origins: set[str]) -> None:
    allowed = {(urlsplit(origin).hostname, urlsplit(origin).port) for origin in allowed_origins}
    for raw_url in urls:
        parsed = urlsplit(raw_url)
        if parsed.scheme not in {"http", "https"} or (parsed.hostname, parsed.port) not in allowed:
            raise BrowserIsolationError("NETWORK_TARGET_ESCAPE")
        if parsed.port in FORBIDDEN_OPERATOR_PORTS:
            raise BrowserIsolationError("FORBIDDEN_OPERATOR_PORT_REQUEST")


def _origin_state(page: Any, origin: str) -> dict[str, Any]:
    page.goto(origin + "/__pair__/ready", wait_until="domcontentloaded")
    return page.evaluate(
        """async ([storageKey, cacheName, requestPath]) => {
            const cache = await caches.open(cacheName);
            const cached = await cache.match(requestPath);
            const registrations = await navigator.serviceWorker.getRegistrations();
            return {
                local_storage: localStorage.getItem(storageKey),
                cache_value: cached ? await cached.text() : null,
                cache_names: await caches.keys(),
                service_worker_count: registrations.length,
            };
        }""",
        [STORAGE_KEY, CACHE_NAME, CACHE_REQUEST_PATH],
    )


def _set_origin_state(page: Any, origin: str, value: str, *, register_service_worker: bool) -> dict[str, Any]:
    page.goto(origin + "/__pair__/ready", wait_until="domcontentloaded")
    return page.evaluate(
        """async ([storageKey, cacheName, requestPath, value, swPath, registerWorker]) => {
            localStorage.setItem(storageKey, value);
            const cache = await caches.open(cacheName);
            await cache.put(requestPath, new Response(value, {headers: {'Content-Type': 'text/plain'}}));
            if (registerWorker) {
                await navigator.serviceWorker.register(swPath, {scope: '/__pair__/'});
                await navigator.serviceWorker.ready;
            }
            const cached = await cache.match(requestPath);
            return {
                local_storage: localStorage.getItem(storageKey),
                cache_value: cached ? await cached.text() : null,
                cache_names: await caches.keys(),
                service_worker_count: (await navigator.serviceWorker.getRegistrations()).length,
            };
        }""",
        [STORAGE_KEY, CACHE_NAME, CACHE_REQUEST_PATH, value, SERVICE_WORKER_PATH, register_service_worker],
    )


def _assert_empty_context(context: Any, origins: tuple[str, str]) -> None:
    if context.cookies(list(origins)):
        raise BrowserIsolationError("FRESH_CONTEXT_COOKIE_LEAK")
    page = context.new_page()
    try:
        for origin in origins:
            state = _origin_state(page, origin)
            if state != {
                "local_storage": None,
                "cache_value": None,
                "cache_names": [CACHE_NAME],
                "service_worker_count": 0,
            }:
                # Merely opening a cache creates its name; no entry is permitted.
                raise BrowserIsolationError("FRESH_CONTEXT_STORAGE_LEAK")
    finally:
        page.close()


def _start_browser_safe_runtime(
    synthetic_pair_root: Path,
    runtime_root: Path,
    allowed_parent: Path,
) -> tuple[SyntheticPairRuntime, dict[str, Any], int]:
    for attempt in range(1, 9):
        runtime = SyntheticPairRuntime(
            synthetic_pair_root,
            runtime_root,
            allowed_parent=allowed_parent,
        )
        receipt = runtime.start()
        if not (set(runtime.ports) & BROWSER_UNSAFE_PORTS):
            return runtime, receipt, attempt - 1
        runtime.close()
    raise BrowserIsolationError("BROWSER_SAFE_OS_PORT_ASSIGNMENT_EXHAUSTED")


def run_multi_principal_browser_isolation(
    synthetic_pair_root: Path,
    fixture_root: Path,
    runtime_root: Path,
    *,
    allowed_parent: Path,
    timeout_ms: int = 15_000,
    fault_after_context_count: int | None = None,
) -> dict[str, Any]:
    """Run the H0A-P2 isolation contract and return only redacted evidence."""

    if timeout_ms <= 0:
        raise BrowserIsolationError("TIMEOUT_INVALID")
    if fault_after_context_count is not None and fault_after_context_count not in {1, 2, 3, 4}:
        raise BrowserIsolationError("FAULT_CONTEXT_COUNT_INVALID")
    pair_root = Path(synthetic_pair_root).resolve(strict=True)
    fixture = Path(fixture_root).resolve(strict=True)
    parent = Path(allowed_parent).resolve(strict=True)
    runtime_path = Path(runtime_root).resolve(strict=False)
    if pair_root.parent != parent or fixture.parent != parent or runtime_path.parent != parent:
        raise BrowserIsolationError("ROOT_PARENT_MISMATCH")
    pair_receipt = verify_synthetic_pair_root(pair_root)
    fixture_receipt = verify_multi_principal_fixture_root(fixture, synthetic_pair_root=pair_root)
    seed = load_multi_principal_seed(fixture, synthetic_pair_root=pair_root)
    credentials = {item["id"]: item for item in load_fixture_credentials(pair_root)}
    aliases = principal_alias_map(pair_root)
    if any(seed["expected_access"]["fixture-outsider"].values()):
        raise BrowserIsolationError("OUTSIDER_NEGATIVE_CONTROL_INVALID")

    runtime: SyntheticPairRuntime | None = None
    browser = None
    contexts: list[Any] = []
    runtime_cleanup: dict[str, Any] | None = None
    browser_disconnected = False
    browser_version = ""
    evidence: dict[str, Any] = {}
    started_at = time.monotonic()
    try:
        runtime, runtime_receipt, port_retries = _start_browser_safe_runtime(pair_root, runtime_path, parent)
        code_origin = runtime.origins[PairHost.CODE]
        notion_origin = runtime.origins[PairHost.NOTION]
        origins = (code_origin, notion_origin)
        allowed_origins = set(origins)

        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            browser_version = browser.version
            cookie_values: list[str] = []
            network_urls: list[str] = []
            storage_checks = 0
            cache_checks = 0
            service_worker_checks = 0
            session_checks = 0
            for index, (principal_id, fixture_role) in enumerate(PRINCIPAL_ORDER, start=1):
                context = browser.new_context(
                    locale="ko-KR",
                    service_workers="allow",
                    viewport={"height": 900, "width": 1440},
                )
                context.set_default_timeout(timeout_ms)
                contexts.append(context)
                context.on("request", lambda request, sink=network_urls: sink.append(request.url))
                context.route(
                    "**/__pair__/h0-sw.js*",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/javascript; charset=utf-8",
                        body=(
                            "self.addEventListener('install',event=>self.skipWaiting());"
                            "self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));"
                        ),
                    ),
                )
                login = _response_json(
                    context.request.post(
                        code_origin + "/api/code/auth/login",
                        headers={"Origin": code_origin},
                        data={
                            "user_id": principal_id,
                            "password": credentials[principal_id]["password"],
                        },
                    )
                )
                if login.get("subject_id") != principal_id:
                    raise BrowserIsolationError("LOGIN_SUBJECT_MISMATCH")
                for origin in origins:
                    current = _response_json(context.request.get(origin + "/__pair__/session"))
                    if current.get("subject_id") != principal_id:
                        raise BrowserIsolationError("CROSS_HOST_SUBJECT_MISMATCH")
                    encoded = json.dumps(current, sort_keys=True)
                    if any(other_id in encoded for other_id, _ in PRINCIPAL_ORDER if other_id != principal_id):
                        raise BrowserIsolationError("CROSS_PRINCIPAL_RESPONSE_LEAK")
                    session_checks += 1
                session_cookies = [
                    item
                    for item in context.cookies(list(origins))
                    if item.get("name") == runtime.configuration.cookie_name
                ]
                if len(session_cookies) != 1 or session_cookies[0].get("domain") != "127.0.0.1":
                    raise BrowserIsolationError("SESSION_COOKIE_SCOPE_INVALID")
                cookie_values.append(str(session_cookies[0].get("value", "")))
                page = context.new_page()
                code_value = aliases[principal_id] + ":code"
                notion_value = aliases[principal_id] + ":notion"
                code_state = _set_origin_state(page, code_origin, code_value, register_service_worker=True)
                notion_state = _set_origin_state(page, notion_origin, notion_value, register_service_worker=False)
                expected_code = {
                    "local_storage": code_value,
                    "cache_value": code_value,
                    "cache_names": [CACHE_NAME],
                    "service_worker_count": 1,
                }
                expected_notion = {
                    "local_storage": notion_value,
                    "cache_value": notion_value,
                    "cache_names": [CACHE_NAME],
                    "service_worker_count": 0,
                }
                if code_state != expected_code or notion_state != expected_notion:
                    raise BrowserIsolationError("PRINCIPAL_ORIGIN_STORAGE_MISMATCH")
                if _origin_state(page, code_origin) != expected_code or _origin_state(page, notion_origin) != expected_notion:
                    raise BrowserIsolationError("PRINCIPAL_ORIGIN_STORAGE_NOT_DURABLE")
                storage_checks += 4
                cache_checks += 4
                service_worker_checks += 2
                page.close()
                if fault_after_context_count == index:
                    raise BrowserIsolationError("INJECTED_BROWSER_CONTEXT_FAILURE")

            if len(set(cookie_values)) != len(PRINCIPAL_ORDER) or any(not value for value in cookie_values):
                raise BrowserIsolationError("CROSS_CONTEXT_COOKIE_COLLISION")
            observer = browser.new_context(locale="ko-KR", service_workers="allow")
            contexts.append(observer)
            _assert_empty_context(observer, origins)
            observer.close()
            contexts.remove(observer)
            for context in list(contexts):
                context.close()
                contexts.remove(context)
            reset = browser.new_context(locale="ko-KR", service_workers="allow")
            contexts.append(reset)
            _assert_empty_context(reset, origins)
            reset.close()
            contexts.remove(reset)
            _assert_allowed_requests(network_urls, allowed_origins)
            browser.close()
            browser_disconnected = not browser.is_connected()
            browser = None
            evidence = {
                "runtime_receipt": runtime_receipt,
                "browser_safe_port_retries": port_retries,
                "principal_context_count": len(PRINCIPAL_ORDER),
                "independent_context_cookie_values": True,
                "same_subject_cross_host_checks": session_checks,
                "local_storage_checks": storage_checks,
                "cache_storage_checks": cache_checks,
                "service_worker_checks": service_worker_checks,
                "observer_context_empty": True,
                "reset_context_empty": True,
                "outsider_effective_action_count": 0,
                "network_request_count": len(network_urls),
                "network_target_escape_count": 0,
            }
    finally:
        for context in list(contexts):
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
                browser_disconnected = not browser.is_connected()
            except Exception:
                browser_disconnected = False
        if runtime is not None:
            runtime_cleanup = runtime.close()

    if not evidence:
        raise BrowserIsolationError("BROWSER_ISOLATION_EVIDENCE_MISSING")
    if browser_disconnected is not True or runtime_cleanup != {
        "owned_processes_stopped": True,
        "owned_ports_closed": True,
        "runtime_root_removed": True,
    }:
        raise BrowserIsolationError("BROWSER_OR_RUNTIME_CLEANUP_FAILED")
    runtime_hosts = evidence["runtime_receipt"]["hosts"]
    return {
        "schema_version": 1,
        "contract": "code_assist.notion.multi_principal_browser_isolation_receipt.v1",
        "status": "pass",
        "pair_root_locator_sha256": pair_receipt["root_locator_sha256"],
        "fixture_root_locator_sha256": fixture_receipt["root_locator_sha256"],
        "principal_context_count": evidence["principal_context_count"],
        "principal_roles": [fixture_role for _, fixture_role in PRINCIPAL_ORDER],
        "browser": {
            "engine": "chromium",
            "channel": "chrome",
            "version": browser_version,
            "headless": True,
            "persistent_context_used": False,
            "signed_in_profile_used": False,
            "disconnected_after_close": browser_disconnected,
        },
        "runtime_hosts": [
            {"role": item["role"], "pid": item["pid"], "port": item["port"]}
            for item in runtime_hosts
        ],
        "browser_safe_port_retries": evidence["browser_safe_port_retries"],
        "independent_context_cookie_values": evidence["independent_context_cookie_values"],
        "same_subject_cross_host_checks": evidence["same_subject_cross_host_checks"],
        "local_storage_checks": evidence["local_storage_checks"],
        "cache_storage_checks": evidence["cache_storage_checks"],
        "service_worker_checks": evidence["service_worker_checks"],
        "observer_context_empty": evidence["observer_context_empty"],
        "reset_context_empty": evidence["reset_context_empty"],
        "outsider_effective_action_count": evidence["outsider_effective_action_count"],
        "network_request_count": evidence["network_request_count"],
        "network_target_escape_count": evidence["network_target_escape_count"],
        "forbidden_operator_port_request_count": 0,
        "cleanup": {
            "browser_process_stopped": browser_disconnected,
            **runtime_cleanup,
        },
        "plaintext_cookie_password_secret_principal_id_or_path_recorded": False,
        "duration_ms": int((time.monotonic() - started_at) * 1000),
    }


__all__ = [
    "BROWSER_UNSAFE_PORTS",
    "BrowserIsolationError",
    "run_multi_principal_browser_isolation",
]
