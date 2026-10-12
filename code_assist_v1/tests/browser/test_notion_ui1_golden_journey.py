from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from typing import Iterator
import urllib.request

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from code_assist_v1.test_safety import create_isolated_test_layout
from code_assist_v1.tools.notion_parity.ui_actions import (
    action_log_sha256,
    build_action_log,
)
from code_assist_v1.tools.notion_parity.ui_capture import (
    capture_sha256,
    validate_capture,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_PATH = (
    PROJECT_ROOT
    / "code_assist_v1"
    / "docs"
    / "notion_parity"
    / "core_free_web_ui"
    / "scenarios.v1.json"
)
PROFILE_ID = "free-owner-chrome-desktop-ko-KR"
SYNTHETIC_TITLE = "UI1 합성 페이지"
SYNTHETIC_PARAGRAPH = "UI1 합성 문단"
LOCAL_ACTION_IDS = (
    "UI-SHELL-002-A01",
    "UI-SHELL-002-A02",
    "UI-SEARCH-001-A01",
    "UI-SEARCH-001-A02",
    "UI-EDITOR-001-A01",
    "UI-EDITOR-001-A02",
    "UI-EDITOR-001-A03",
    "UI-EDITOR-001-A04",
    "UI-SEARCH-001-A03",
    "UI-SEARCH-001-A04",
    "UI-A11Y-001-A01",
)
SECOND_BRAIN_ACTION_IDS = (
    "UI-SB-001-A01",
    "UI-SB-001-A02",
    "UI-SB-002-A01",
    "UI-SB-002-A02",
    "UI-SB-002-A03",
)
_CAPTURE_ALLOWLIST = (
    "BREADCRUMB",
    "BLOCK-HEADING",
    "COMMENT-INPUT",
    "COMMENT-PANEL",
    "COMMENT-THREAD",
    "EDITOR-BLOCK",
    "HOME",
    "HOME-MAIN",
    "NEW-PAGE",
    "PAGE-ROOT",
    "PAGE-TITLE",
    "PAGE-CANVAS",
    "PAGE-FIND",
    "PAGE-FIND-INPUT",
    "PAGE-MENU",
    "PREVIEW-DIALOG",
    "SEARCH-INPUT",
    "SEARCH-RESULT",
    "SEARCH-TRIGGER",
    "SESSION-CONTEXT",
    "SHARE-DIALOG",
    "SHARE-INVITE-INPUT",
    "SHARE-TRIGGER",
    "SIDEBAR",
    "SIDEBAR-COLLAPSE",
    "SIDEBAR-RESTORE",
    "SLASH-MENU",
    "SECOND-BRAIN-PREVIEW",
    "SECOND-BRAIN-STATUS",
    "SIDE-PEEK",
    "SIDE-PEEK-CANVAS",
    "FULL-PAGE",
    "TOPBAR",
    "PAGE-TREE",
    "MOVE-CONFIRM",
    "MOVE-PAGE",
    "MOVE-TARGET",
    "TREE-CHILD-A",
    "TREE-CHILD-B",
    "TREE-ROOT",
    "SYNTHETIC-BACKLINK",
    "DUPLICATE-PAGE-ROW",
    "DATABASE-GRID",
    "DATABASE-FILTER",
    "DATABASE-GROUP",
    "DATABASE-BOARD",
    "DATABASE-CALENDAR",
    "DATABASE-GALLERY",
    "DATABASE-LIST",
    "DATABASE-ROW",
    "DATABASE-SORT",
    "DATABASE-TABLE",
    "DATABASE-VIEW-SWITCHER",
    "TRASH",
    "RESTORED-PAGE-ROW",
    "WORKSPACE-SWITCHER",
    "button",
    "dialog",
    "core-free-ui-v1",
    "local",
    "option",
    "pass",
    "restored",
    "region",
    "searchbox",
    "group",
    "posted",
    "resolved",
    "resolved_and_hidden",
    "attached",
    "detached",
    "tablist",
    "five-views-rendered",
    "hidden_with_access_tooltip",
    "table",
    "textbox",
    "treeitem",
    "visible",
    PROFILE_ID,
)


def _free_port() -> int:
    # Chromium rejects a fixed set of legacy service ports even on loopback.
    # Keep browser fixtures above that range instead of accepting an OS-selected
    # port that can make an otherwise isolated journey fail before navigation.
    for _ in range(128):
        port = 12_000 + secrets.randbelow(36_000)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            try:
                listener.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError("could not reserve a browser-safe loopback port")


def _walk_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)


def _scenario_action_hash(scenario_id: str) -> str:
    document = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
    scenario = next(
        item
        for item in document["required_scenarios"]
        if item["scenario_id"] == scenario_id
    )
    actions = scenario["actions"]
    action_log = build_action_log(
        scenario_id=scenario_id,
        side="local",
        run_id="ui1-local-capture",
        actions=actions,
        synthetic_allowlist=tuple(sorted(set(_walk_strings(actions)))),
    )
    return action_log_sha256(action_log)


def _ui_source_build_sha256() -> str:
    digest = hashlib.sha256()
    for relative in (
        "code_assist_v1/notion_static/index.html",
        "code_assist_v1/notion_static/notion.css",
        "code_assist_v1/notion_static/notion.js",
    ):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update((PROJECT_ROOT / relative).read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _capture(
    *,
    scenario_id: str,
    state: dict[str, object],
    keyboard: dict[str, object],
    focus: dict[str, object],
    a11y: dict[str, object],
    semantic_visual: dict[str, object],
    error_recovery: dict[str, object],
    geometry: dict[str, dict[str, int]],
    viewport: dict[str, int] | None = None,
) -> dict[str, object]:
    value = {
        "capture_schema_version": 1,
        "scenario_id": scenario_id,
        "side": "local",
        "profile": PROFILE_ID,
        "viewport": {
            "height": int((viewport or {"height": 900})["height"]),
            "scale": 1,
            "width": int((viewport or {"width": 1440})["width"]),
        },
        "state": state,
        "keyboard": keyboard,
        "focus": focus,
        "a11y": a11y,
        "semantic_visual": semantic_visual,
        "error_recovery": error_recovery,
        "geometry": geometry,
        "latency_ms": 0,
        "action_log_sha256": _scenario_action_hash(scenario_id),
        "screenshot_sha256": None,
        "source_build_sha256": _ui_source_build_sha256(),
        "redaction": {"status": "pass", "rule_set": "core-free-ui-v1"},
    }
    errors = validate_capture(value, synthetic_allowlist=_CAPTURE_ALLOWLIST)
    if errors:
        raise AssertionError(f"local capture validation failed: {errors[0]['code']}")
    return value


def _request_json(
    url: str,
    *,
    body: dict[str, str] | None = None,
    origin: str | None = None,
) -> dict[str, object]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if origin is not None:
        headers["Origin"] = origin
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("local fixture returned a non-object response")
    return payload


def _wait_for_health(origin: str, process: subprocess.Popen[bytes]) -> None:
    for _ in range(160):
        if process.poll() is not None:
            raise RuntimeError("isolated Notion fixture stopped before health was ready")
        try:
            health = _request_json(f"{origin}/healthz")
            if health.get("status") == "ok" and health.get("service") == "notion_workspace":
                return
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError("isolated Notion fixture health timed out")


def _seed_synthetic_auth_registry(
    root: Path,
    *,
    user_id: str,
    password: str,
) -> None:
    """Seed only the isolated UI journey identity with the production KDF.

    Authentication KDF reliability belongs to the L1 auth lane, not this L4 UI
    journey.  The local Python 3.14/OpenSSL scrypt implementation was observed
    returning an intermittently unverifiable freshly generated hash.  Retry
    fixture generation outside the measured browser path, but retain
    Werkzeug's production-default hash format so UI-PERF-001 measures the real
    login route without a test-only PBKDF2 cost.
    """

    layout = create_isolated_test_layout(root, user_id=user_id)
    password_hash = ""
    for _attempt in range(3):
        candidate = generate_password_hash(password)
        if all(check_password_hash(candidate, password) for _ in range(3)):
            password_hash = candidate
            break
    if not password_hash:
        raise RuntimeError("synthetic production-KDF account seed was not verifiable")
    now = int(time.time())
    registry = {
        "users": {
            user_id: {
                "id": user_id,
                "display_name": "UI1 Owner",
                "role": "admin",
                "disabled": False,
                "password_hash": password_hash,
                "must_change_password": False,
                "password_changed_at": now,
                "created_at": now,
                "updated_at": now,
            }
        }
    }
    registry_path = layout.runtime_dir / "users.json"
    temporary_path = layout.runtime_dir / "users.json.tmp"
    temporary_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary_path, registry_path)
    for name in ("sessions", "workspace", "skills"):
        (layout.user_data_dir / user_id / name).mkdir(parents=True, exist_ok=True)


@contextmanager
def _isolated_server(
    root: Path,
    *,
    seed_navigation_workspaces: bool = False,
    seed_navigation_tree: bool = False,
) -> Iterator[tuple[str, str, str]]:
    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    user_id = "ui1-owner"
    password = secrets.token_urlsafe(24)
    _seed_synthetic_auth_registry(root, user_id=user_id, password=password)
    command = [
        sys.executable,
        "-m",
        "code_assist_v1.tools.notion_parity.ui_local_fixture",
        "serve",
        "--data-root",
        os.fspath(root),
        "--port",
        str(port),
        "--repository-root",
        os.fspath(PROJECT_ROOT),
        "--test-mode-confirmed",
    ]
    if seed_navigation_workspaces:
        command.append("--seed-navigation-workspaces")
    if seed_navigation_tree:
        command.append("--seed-navigation-tree")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.fspath(PROJECT_ROOT)
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=(
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            if sys.platform == "win32"
            else 0
        ),
    )
    try:
        _wait_for_health(origin, process)
        for _attempt in range(3):
            verification = _request_json(
                f"{origin}/api/code/auth/login",
                body={"password": password, "user_id": user_id},
                origin=origin,
            )
            if (
                verification.get("status") != "logged_in"
                or (verification.get("user") or {}).get("id") != user_id
            ):
                raise RuntimeError(
                    "synthetic account was registered but not login-ready"
                )
        yield origin, user_id, password
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def _focus_alias(page: object) -> str:
    return str(
        page.evaluate(
            """() => {
              const element = document.activeElement;
              if (!element) return 'NONE';
              const aliases = {
                collapseBtn: 'SIDEBAR-COLLAPSE',
                globalSearchInput: 'SEARCH-INPUT',
                homeNavBtn: 'HOME',
                newPageBtn: 'NEW-PAGE',
                sidebarNewBtn: 'NEW-PAGE',
                pageTitle: 'PAGE-TITLE',
                pageMoreBtn: 'PAGE-MORE',
                publishBtn: 'SECOND-BRAIN-PREVIEW',
                skipToContent: 'SKIP-CONTENT',
                sidebarRestoreBtn: 'SIDEBAR-RESTORE',
                sidebarSearchBtn: 'SEARCH-TRIGGER',
                shareUser: 'SHARE-INVITE-INPUT',
                sidePeekBody: 'SIDE-PEEK-CANVAS',
                sidePeekTitle: 'SIDE-PEEK-TITLE',
                workspaceName: 'WORKSPACE-SWITCHER',
                workspaceSidebarGroup: 'WORKSPACE-SIDEBAR-GROUP',
                workspaceMain: 'WORKSPACE-MAIN',
                pageEditorGroup: 'PAGE-EDITOR-GROUP',
              };
              if (aliases[element.id]) return aliases[element.id];
              if (element.id === 'homeView') return 'HOME-MAIN';
              if (element.id === 'pageView') return 'PAGE-CANVAS';
              if (element.classList && element.classList.contains('block-editor')) {
                return element.closest('#pageEditorGroup')
                  ? 'PAGE-EDITOR-GROUP'
                  : 'EDITOR-BLOCK';
              }
              if (element.classList && element.classList.contains('search-result-row')) {
                return 'SEARCH-RESULT';
              }
              const modal = document.getElementById('modalBg');
              if (modal && !modal.hidden) {
                if (!modal.contains(element)) return 'FOCUS-LOST';
                const title = document.getElementById('modalTitle')?.textContent || '';
                return /Published|결과/.test(title)
                  ? 'SECOND-BRAIN-STATUS'
                  : 'PREVIEW-DIALOG';
              }
              if (element === document.body) return 'PAGE-ROOT';
              return 'UNMAPPED-CONTROL';
            }"""
        )
    )


def _wait_for_notion_ready(page: object) -> None:
    from playwright.sync_api import expect

    expect(page.locator("body[data-notion-ready='true']")).to_have_count(
        1,
        timeout=45_000,
    )


def _create_page_from_sidebar(page: object) -> None:
    from playwright.sync_api import expect

    page.locator("#sidebarNewBtn").click()
    menu = page.locator("#newPageMenu")
    expect(menu).to_be_visible()
    menu.locator('[data-new-page-type="page"]').click()
    expect(page.locator("#pageTitle")).to_be_visible(timeout=15_000)


def _control_focus_state(control: object, name: str) -> dict[str, str]:
    return {
        "name": name,
        "state": (
            "focused"
            if bool(control.evaluate("element => element === document.activeElement"))
            else "blurred"
        ),
    }


def _rounded_box(locator: object, *, timeout_seconds: float = 5.0) -> dict[str, int]:
    """Return geometry after a visible region survives an async re-render.

    Search and database result rows are replaced when their latest request
    settles.  Playwright can therefore observe a row as visible and then get a
    transient ``None`` from ``bounding_box()`` while the replacement is being
    attached.  Geometry is evidence, so retry the read for a short bounded
    interval instead of accepting a missing box or making the whole journey
    flaky.
    """

    deadline = time.monotonic() + timeout_seconds
    while True:
        box = locator.bounding_box()
        if box is not None:
            return {
                key: round(float(box[key]))
                for key in ("x", "y", "width", "height")
            }
        if time.monotonic() >= deadline:
            raise AssertionError("required semantic region has no bounding box")
        time.sleep(0.05)


def _keyboard_focus_sequence(page: object) -> list[str]:
    expected_forward = [
        "SKIP-CONTENT",
        "HOME",
        "SEARCH-TRIGGER",
        "NEW-PAGE",
        "PAGE-TITLE",
    ]
    required = set(expected_forward)
    page.evaluate(
        """() => {
          document.body.setAttribute('tabindex', '-1');
          document.body.focus();
          document.body.removeAttribute('tabindex');
        }"""
    )
    forward: list[str] = []
    for _ in range(40):
        page.keyboard.press("Tab")
        alias = _focus_alias(page)
        if alias in {"FOCUS-LOST", "NONE", "PAGE-ROOT"}:
            raise AssertionError(
                f"UI1 keyboard focus left the document before PAGE-TITLE: {alias}"
            )
        if alias in required and alias not in forward:
            forward.append(alias)
        if alias == "PAGE-TITLE":
            break
    if forward != expected_forward:
        raise AssertionError(
            "UI1 forward keyboard focus sequence differs: "
            f"expected={expected_forward}, seen={forward}, current={_focus_alias(page)}"
        )

    for _ in range(30):
        page.keyboard.press("Shift+Tab")
        alias = _focus_alias(page)
        if alias in {"FOCUS-LOST", "NONE", "PAGE-ROOT"}:
            raise AssertionError(
                f"UI1 reverse keyboard focus left the document before NEW-PAGE: {alias}"
            )
        if alias == "NEW-PAGE":
            return forward + [alias]
    raise AssertionError(
        "UI1 keyboard focus did not return to NEW-PAGE: "
        f"current={_focus_alias(page)}"
    )


def _canonical_publication_state(data_root: Path) -> dict[str, int]:
    database = data_root / "core" / "knowledge-core.db"
    if not database.is_file():
        return {"items": 0, "revisions": 0}
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro",
        uri=True,
        timeout=5,
    )
    try:
        return {
            "items": int(
                connection.execute("SELECT count(*) FROM knowledge_item").fetchone()[0]
            ),
            "revisions": int(
                connection.execute("SELECT count(*) FROM knowledge_revision").fetchone()[0]
            ),
        }
    finally:
        connection.close()


def _latest_notion_provenance(data_root: Path) -> dict[str, object]:
    database = data_root / "core" / "knowledge-core.db"
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro",
        uri=True,
        timeout=5,
    )
    try:
        row = connection.execute(
            """SELECT metadata_json
               FROM source_artifact
               WHERE origin_type='notion'
               ORDER BY created_at DESC, id DESC
               LIMIT 1"""
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise AssertionError("canonical Notion publication has no source artifact")
    metadata = json.loads(str(row[0]))
    provenance = metadata.get("notion_provenance") if isinstance(metadata, dict) else None
    if not isinstance(provenance, dict):
        raise AssertionError("canonical Notion source provenance is missing")
    return provenance


def _wait_for_publication_action(page: object, label: str) -> object:
    from playwright.sync_api import expect

    action = page.locator("#modalActions button").filter(
        has_text=re.compile(rf"^{re.escape(label)}$")
    )
    expect(action).to_be_visible(timeout=45_000)
    return action


def _open_publication_preview(page: object) -> str:
    from playwright.sync_api import expect

    publish_button = page.get_by_role("button", name="Second Brain", exact=True)
    if publish_button.is_visible():
        publish_button.click()
    else:
        page.locator("#pageMoreBtn").click()
        publish_action = page.locator(
            '#pageMenu [data-page-action="second_brain"]'
        )
        expect(publish_action).to_be_visible()
        publish_action.click()
    expect(page.locator("#modalBg")).to_be_visible(timeout=45_000)
    page.wait_for_function(
        """() => [...document.querySelectorAll('#modalActions button')]
          .some((button) => ['게시', '승인 후 업로드'].includes(button.textContent.trim()))""",
        timeout=45_000,
    )
    title = page.locator("#modalTitle").inner_text()
    if title == "지식 게시":
        return "게시"
    if title == "Second Brain 업로드 확인":
        return "승인 후 업로드"
    raise AssertionError("publication preview used an unknown visible state")


def _exercise_local_ui1(
    page: object,
    origin: str,
    user_id: str,
    password: str,
    data_root: Path,
    *,
    include_second_brain: bool,
) -> dict[str, object]:
    from playwright.sync_api import expect

    page.goto(origin, wait_until="domcontentloaded")
    page.get_by_label("사번", exact=True).fill(user_id)
    page.get_by_label("비밀번호", exact=True).fill(password)
    page.get_by_role("button", name="로그인", exact=True).click()

    home = page.get_by_role("tab", name="홈", exact=True)
    expect(home).to_be_visible(timeout=20_000)
    expect(page.get_by_role("link", name=re.compile("Second Brain Agent"))).to_be_visible()
    expect(page.locator("body")).to_have_attribute(
        "data-notion-ready",
        "true",
        timeout=20_000,
    )

    collapse = page.locator("#collapseBtn")
    restore = page.locator("#sidebarRestoreBtn")
    sidebar = page.locator(".sidebar")
    expanded_box = _rounded_box(sidebar)

    collapse.click()
    expect(page.locator("body")).to_have_class(re.compile(r"\bsidebar-collapsed\b"))
    expect(restore).to_be_visible()
    collapsed_focus = _control_focus_state(restore, "SIDEBAR-RESTORE")

    restore.click()
    expect(page.locator("body")).not_to_have_class(re.compile(r"\bsidebar-collapsed\b"))
    expect(collapse).to_be_visible()
    restored_focus = _control_focus_state(collapse, "SIDEBAR-COLLAPSE")

    page.wait_for_function("() => !navigationControlsLocked()")
    collapse.focus()
    collapse.press("Enter")
    expect(page.locator("body")).to_have_class(re.compile(r"\bsidebar-collapsed\b"))
    expect(restore).to_be_focused()
    keyboard_collapsed_focus = _focus_alias(page)
    page.wait_for_function("() => !navigationControlsLocked()")
    restore.press("Enter")
    expect(page.locator("body")).not_to_have_class(re.compile(r"\bsidebar-collapsed\b"))
    expect(collapse).to_be_focused()
    keyboard_restored_focus = _focus_alias(page)
    restored_box = _rounded_box(sidebar)
    # The workspace switcher intentionally opens on hover.  Move away after
    # restoring the sidebar so the next semantic click is not covered by that
    # transient popover.
    page.mouse.move(720, 450)

    search_trigger = page.get_by_role("button", name="검색", exact=True)
    search_input = page.locator("#globalSearchInput")
    search_overlay = page.locator("#searchOverlay")
    search_trigger.click()
    expect(search_overlay).to_be_visible()
    expect(search_input).to_be_focused()
    search_open_focus = _focus_alias(page)
    search_open_capture = _capture(
        scenario_id="UI-SEARCH-001",
        state={"result": True, "visible": True},
        keyboard={"result": "not_applicable"},
        focus={"sequence": [search_open_focus]},
        a11y={"name": "SEARCH-INPUT", "role": "textbox"},
        semantic_visual={"region": "SEARCH-OVERLAY", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"SEARCH-INPUT": _rounded_box(search_input)},
    )
    page.keyboard.press("Escape")
    expect(search_overlay).to_be_hidden()
    search_escape_focus = _focus_alias(page)
    search_escape_capture = _capture(
        scenario_id="UI-SEARCH-001",
        state={"hidden": True, "result": True},
        keyboard={"key": "ESCAPE", "result": "pass"},
        focus={"name": search_escape_focus, "state": "focused"},
        a11y={"name": "SEARCH-TRIGGER", "role": "button"},
        semantic_visual={"region": "SEARCH-OVERLAY", "state": "hidden"},
        error_recovery={"result": "pass"},
        geometry={},
    )

    page.get_by_role("button", name="새 페이지", exact=True).first.click()
    new_page_menu = page.locator("#newPageMenu")
    expect(new_page_menu).to_be_visible()
    page_type = new_page_menu.locator('[data-new-page-type="page"]')
    expect(page_type).to_be_focused()
    page_type.click()
    title = page.locator("#pageTitle")
    expect(title).to_be_visible(timeout=15_000)
    expect(title).to_be_focused()
    title_focus = _focus_alias(page)
    title.fill(SYNTHETIC_TITLE)
    editor = page.locator(".block-editor[contenteditable='true']").first
    expect(editor).to_be_visible()
    editor.fill(SYNTHETIC_PARAGRAPH)
    editor_focus = _focus_alias(page)
    expect(page.locator("#status")).to_have_text("저장됨", timeout=20_000)

    editor_viewport = page.viewport_size
    assert editor_viewport is not None
    editor_saved_capture = _capture(
        scenario_id="UI-EDITOR-001",
        state={"count": 1, "state": "committed"},
        keyboard={"result": "pass"},
        focus={"name": editor_focus, "state": "focused"},
        a11y={
            "name": editor_focus,
            "role": "GROUP" if editor_focus == "PAGE-EDITOR-GROUP" else "TEXTBOX",
        },
        semantic_visual={"region": "PAGE-ROOT", "state": "visible"},
        error_recovery={"result": "unchanged"},
        geometry={
            "PAGE-TITLE": _rounded_box(title),
            "EDITOR-BLOCK": _rounded_box(editor),
        },
        viewport=editor_viewport,
    )

    page.reload(wait_until="domcontentloaded")
    expect(title).to_have_value(SYNTHETIC_TITLE, timeout=20_000)
    editor = page.locator(".block-editor[contenteditable='true']").first
    expect(editor).to_have_text(SYNTHETIC_PARAGRAPH)
    expect(page.locator("#pageView")).not_to_be_focused(timeout=15_000)
    reload_focus = _focus_alias(page)
    editor_restored_capture = _capture(
        scenario_id="UI-EDITOR-001",
        state={"count": 1, "state": "restored"},
        keyboard={"result": "not_applicable"},
        focus={"name": "PAGE-CANVAS", "state": (
            "focused" if reload_focus == "PAGE-CANVAS" else "not_focused"
        )},
        a11y={"name": "PAGE-CANVAS", "role": "MAIN"},
        semantic_visual={"region": "PAGE-ROOT", "state": "restored"},
        error_recovery={"result": "unchanged"},
        geometry={
            "PAGE-CANVAS": _rounded_box(page.locator("#pageView")),
            "EDITOR-BLOCK": _rounded_box(editor),
        },
        viewport=editor_viewport,
    )
    editor_action_captures = {
        "UI-EDITOR-001-A03": editor_saved_capture,
        "UI-EDITOR-001-A04": editor_restored_capture,
    }

    search_trigger.click()
    expect(search_input).to_be_focused()
    search_input.fill(SYNTHETIC_TITLE)
    result = page.locator(".search-result-row").filter(has_text=SYNTHETIC_TITLE).first
    expect(result).to_be_visible(timeout=15_000)
    search_result_box = _rounded_box(result)
    search_result_input_focus = _focus_alias(page)
    result.dblclick()
    expect(search_overlay).to_be_hidden()
    expect(title).to_have_value(SYNTHETIC_TITLE, timeout=15_000)
    expect(page.locator("#pageView")).not_to_be_focused(timeout=15_000)
    search_result_open_focus = _focus_alias(page)
    search_result_focus = [search_result_input_focus, search_result_open_focus]
    search_result_capture = _capture(
        scenario_id="UI-SEARCH-001",
        state={"count": 1, "opened": True, "result": True},
        keyboard={"result": "not_applicable"},
        focus={"sequence": search_result_focus},
        a11y={"name": "SEARCH-RESULT", "role": "option"},
        semantic_visual={"region": "SEARCH-RESULT", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"SEARCH-RESULT": search_result_box},
    )
    search_action_captures = {
        "UI-SEARCH-001-A01": search_open_capture,
        "UI-SEARCH-001-A02": search_escape_capture,
        "UI-SEARCH-001-A04": search_result_capture,
    }

    second_brain: dict[str, int] | None = None
    cancel_preview_focus = cancel_return_focus = None
    approve_preview_focus = approve_status_focus = replay_status_focus = None
    if include_second_brain:
        before_cancel = _canonical_publication_state(data_root)
        _open_publication_preview(page)
        cancel_preview_focus = _focus_alias(page)
        assert cancel_preview_focus == "PREVIEW-DIALOG"
        preview_text = page.locator("#modalBody").inner_text()
        assert SYNTHETIC_TITLE in preview_text
        _wait_for_publication_action(page, "취소").click()
        expect(page.locator("#modalBg")).to_be_hidden(timeout=15_000)
        cancel_return_focus = _focus_alias(page)
        assert cancel_return_focus == "PAGE-MORE"
        after_cancel = _canonical_publication_state(data_root)

        approve_label = _open_publication_preview(page)
        approve_preview_focus = _focus_alias(page)
        assert approve_preview_focus == "PREVIEW-DIALOG"
        _wait_for_publication_action(page, approve_label).click()
        expect(page.locator("#modalTitle")).to_have_text(
            re.compile(r"^(게시 결과|Published)$"), timeout=45_000
        )
        after_publish = _canonical_publication_state(data_root)
        approve_status_focus = _focus_alias(page)
        assert approve_status_focus == "SECOND-BRAIN-STATUS"
        source_snapshot = page.evaluate(
            """async () => {
              const prefix = '/page/';
              if (!location.pathname.startsWith(prefix)) {
                throw new Error('active page URL is unavailable');
              }
              const pageId = decodeURIComponent(location.pathname.slice(prefix.length));
              const response = await fetch(
                `/api/notion/pages/${encodeURIComponent(pageId)}/publish_preview`,
                { headers: { Accept: 'application/json' } },
              );
              if (!response.ok) throw new Error('source snapshot verification failed');
              const body = await response.json();
              return body.source_snapshot;
            }"""
        )
        provenance = _latest_notion_provenance(data_root)
        expected_provenance = {
            "asset_manifest_sha256": source_snapshot["asset_manifest_sha256"],
            "owner_user_id": source_snapshot["owner_user_id"],
            "page_id": source_snapshot["page_id"],
            "revision": source_snapshot["revision"],
            "source_snapshot_sha256": source_snapshot["source_snapshot_sha256"],
            "updated_at": source_snapshot["updated_at"],
        }
        page.locator("#modalClose").click()
        expect(page.locator("#modalBg")).to_be_hidden()

        approve_label = _open_publication_preview(page)
        _wait_for_publication_action(page, approve_label).click()
        expect(page.locator("#modalTitle")).to_have_text(
            re.compile(r"^(게시 결과|Published)$"), timeout=45_000
        )
        after_replay = _canonical_publication_state(data_root)
        replay_status_focus = _focus_alias(page)
        assert replay_status_focus == "SECOND-BRAIN-STATUS"
        second_brain = {
            "approve_item_delta": after_publish["items"] - after_cancel["items"],
            "approve_revision_delta": (
                after_publish["revisions"] - after_cancel["revisions"]
            ),
            "cancel_item_delta": after_cancel["items"] - before_cancel["items"],
            "cancel_revision_delta": (
                after_cancel["revisions"] - before_cancel["revisions"]
            ),
            "replay_item_delta": after_replay["items"] - after_publish["items"],
            "replay_revision_delta": (
                after_replay["revisions"] - after_publish["revisions"]
            ),
            "source_snapshot_matches": int(provenance == expected_provenance),
        }
        page.locator("#modalClose").click()
        expect(page.locator("#modalBg")).to_be_hidden()

    keyboard_focus_sequence = _keyboard_focus_sequence(page)

    viewport = page.viewport_size
    assert viewport is not None
    captures = {
        "UI-SHELL-002": _capture(
            scenario_id="UI-SHELL-002",
            state={"collapsed": True, "restored": expanded_box == restored_box},
            keyboard={"result": "pass"},
            focus={"sequence": [collapsed_focus, restored_focus]},
            a11y={"name": "SIDEBAR-COLLAPSE", "role": "button"},
            semantic_visual={"region": "SIDEBAR", "state": "restored"},
            error_recovery={"result": "pass"},
            geometry={"SIDEBAR": restored_box},
        ),
        "UI-SEARCH-001": _capture(
            scenario_id="UI-SEARCH-001",
            state={"hidden": True, "result": True, "visible": True},
            keyboard={"key": "ESCAPE", "result": "pass"},
            focus={
                "sequence": [
                    search_open_focus,
                    search_escape_focus,
                    search_result_focus,
                ]
            },
            a11y={"name": "SEARCH-TRIGGER", "role": "button"},
            semantic_visual={"region": "SEARCH-RESULT", "state": "visible"},
            error_recovery={"result": "pass"},
            geometry={"SEARCH-RESULT": search_result_box},
        ),
        "UI-EDITOR-001": _capture(
            scenario_id="UI-EDITOR-001",
            state={"count": 1, "restored": True},
            keyboard={"result": "pass"},
            focus={"sequence": [title_focus, editor_focus, reload_focus]},
            a11y={"name": "EDITOR-BLOCK", "role": "textbox"},
            semantic_visual={"region": "PAGE-ROOT", "state": "restored"},
            error_recovery={"result": "pass"},
            geometry={},
        ),
        "UI-A11Y-001": _capture(
            scenario_id="UI-A11Y-001",
            state={"count": len(keyboard_focus_sequence), "result": "success"},
            keyboard={"result": "pass", "sequence": keyboard_focus_sequence},
            focus={"name": keyboard_focus_sequence[-1], "sequence": keyboard_focus_sequence},
            a11y={"count": len(keyboard_focus_sequence), "result": "pass"},
            semantic_visual={"region": "PAGE-ROOT", "state": "visible"},
            error_recovery={"result": "pass"},
            geometry={},
        ),
    }
    if second_brain is not None:
        captures["UI-SB-001"] = _capture(
            scenario_id="UI-SB-001",
            state={"mutation_count": 0, "publication_count": 0, "result": "unchanged"},
            keyboard={"result": "pass"},
            focus={"sequence": [cancel_preview_focus, cancel_return_focus]},
            a11y={"name": "PREVIEW-DIALOG", "role": "DIALOG", "state": "visible"},
            semantic_visual={"region": "PREVIEW-DIALOG", "state": "visible"},
            error_recovery={"result": "pass"},
            geometry={},
        )
        captures["UI-SB-002"] = _capture(
            scenario_id="UI-SB-002",
            state={
                "count": second_brain["approve_revision_delta"],
                "publication_count": second_brain["approve_item_delta"],
                "result": "committed",
                "source_hash_match": bool(second_brain["source_snapshot_matches"]),
            },
            keyboard={"result": "pass"},
            focus={"sequence": [approve_preview_focus, approve_status_focus, replay_status_focus]},
            a11y={"name": "SECOND-BRAIN-STATUS", "role": "DIALOG", "state": "visible"},
            semantic_visual={"region": "SECOND-BRAIN-STATUS", "state": "visible"},
            error_recovery={"mutation_count": 0, "result": "replayed"},
            geometry={},
        )
    action_captures = {**editor_action_captures, **search_action_captures}
    return {
        "action_capture_sha256": {
            action_id: capture_sha256(value)
            for action_id, value in action_captures.items()
        },
        "action_captures": action_captures,
        "a11y": {
            "home_tab": home.count() == 1,
            "new_page_button": page.get_by_role("button", name="새 페이지", exact=True).count() >= 1,
            "search_button": search_trigger.count() == 1,
            "second_brain_button": page.get_by_role("button", name="Second Brain", exact=True).count() == 1,
        },
        "action_ids": list(LOCAL_ACTION_IDS) + (
            list(SECOND_BRAIN_ACTION_IDS) if include_second_brain else []
        ),
        "browser": "chrome-ephemeral",
        "capture_sha256": {
            scenario_id: capture_sha256(capture)
            for scenario_id, capture in sorted(captures.items())
        },
        "focus": {
            "after_reload": reload_focus,
            "after_search_escape": search_escape_focus,
            "after_search_result": search_result_focus,
            "after_sidebar_collapse": collapsed_focus,
            "after_sidebar_restore": restored_focus,
            "after_sidebar_keyboard_collapse": keyboard_collapsed_focus,
            "after_sidebar_keyboard_restore": keyboard_restored_focus,
            "search_open": search_open_focus,
        },
        "geometry": {
            "sidebar_expanded": expanded_box,
            "sidebar_restored": restored_box,
        },
        "profile": PROFILE_ID,
        "second_brain": second_brain,
        "state": {
            "content_restored": True,
            "page_saved": True,
            "search_result_visible": True,
            "sidebar_restored": expanded_box == restored_box,
        },
        "synthetic_only": True,
        "user_profile_reused": False,
        "viewport": viewport,
    }


def _run_chrome_journey(
    playwright: object,
    data_root: Path,
    *,
    include_second_brain: bool,
    viewport: dict[str, int] | None = None,
) -> tuple[dict[str, object], str, str]:
    with _isolated_server(data_root) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport=viewport or {"height": 900, "width": 1440},
            )
            try:
                report = _exercise_local_ui1(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                    data_root,
                    include_second_brain=include_second_brain,
                )
            finally:
                context.close()
        finally:
            browser.close()
    return report, user_id, password


def _exercise_workspace_switch_isolation(
    page: object,
    origin: str,
    user_id: str,
    password: str,
) -> dict[str, object]:
    from playwright.sync_api import expect

    requested_urls: list[str] = []
    page.on("request", lambda request: requested_urls.append(request.url))
    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()

    workspace_title = page.locator("#workspaceTitle")
    expect(workspace_title).to_have_text(
        re.compile(r"^Synthetic Workspace [AB]$"),
        timeout=30_000,
    )
    _wait_for_notion_ready(page)

    def assert_workspace_scope(suffix: str) -> None:
        current = f"Synthetic Page {suffix}"
        other = f"Synthetic Page {'B' if suffix == 'A' else 'A'}"
        labels = page.locator("#ownPages .page-title-label")
        expect(labels).to_have_count(1, timeout=15_000)
        expect(labels.first).to_have_text(current)
        assert other not in page.locator("#ownPages").inner_text()

        page.locator("#sidebarSearchBtn").click()
        search = page.locator("#globalSearchInput")
        expect(page.locator("#searchOverlay")).to_be_visible()
        page.wait_for_timeout(50)
        search.focus()
        expect(search).to_be_focused()
        search.fill(other)
        page.wait_for_timeout(500)
        expect(page.locator("#searchResults .search-result-row")).to_have_count(0)
        search.fill(current)
        page.wait_for_timeout(500)
        result = page.locator("#searchResults .search-result-row")
        expect(result).to_have_count(1)
        expect(result.first).to_contain_text(current)
        search.press("Escape")

    initial_suffix = workspace_title.inner_text().rsplit(" ", 1)[-1]
    assert initial_suffix == "A"
    assert_workspace_scope(initial_suffix)
    initial_page_id = page.locator("#ownPages .page-row").get_attribute("data-page-id")
    assert initial_page_id

    def switch_to(suffix: str, expected_focus: str) -> dict[str, object]:
        page.locator("#workspaceName").click(position={"x": 80, "y": 20})
        target = page.locator("[data-navigation-workspace]").filter(
            has_text=f"Synthetic Workspace {suffix}"
        )
        expect(target).to_have_count(1)
        target.click()
        expect(workspace_title).to_have_text(f"Synthetic Workspace {suffix}")
        workspace_switcher = page.locator("#workspaceName")
        workspace_group = page.locator("#workspaceSidebarGroup")
        focus_target = (
            workspace_group
            if expected_focus == "WORKSPACE-SIDEBAR-GROUP"
            else workspace_switcher
        )
        expect(focus_target).to_be_focused()
        assert _focus_alias(page) == expected_focus
        focus_role = "GROUP" if expected_focus == "WORKSPACE-SIDEBAR-GROUP" else "BUTTON"
        viewport = page.viewport_size
        assert viewport is not None
        capture = _capture(
            scenario_id="UI-SHELL-001",
            state={
                "count": 1,
                "state": "committed",
                "workspace_alias": f"REF-FREE-{suffix}",
            },
            keyboard={"result": "not_applicable"},
            focus={"name": expected_focus, "state": "focused"},
            a11y={"name": expected_focus, "role": focus_role},
            semantic_visual={"region": "SIDEBAR", "state": "visible"},
            error_recovery={"result": "unchanged"},
            geometry={"WORKSPACE-SWITCHER": _rounded_box(workspace_switcher)},
            viewport=viewport,
        )
        page.mouse.move(720, 450)
        assert_workspace_scope(suffix)
        return capture

    other_suffix = "B"
    other_capture = switch_to(other_suffix, "WORKSPACE-SIDEBAR-GROUP")
    probe_page = page.context.new_page()
    probe_page.on("request", lambda request: requested_urls.append(request.url))
    try:
        probe_page.goto(
            f"{origin}/page/{initial_page_id}",
            wait_until="domcontentloaded",
        )
        expect(probe_page.locator("#workspaceTitle")).to_have_text(
            f"Synthetic Workspace {other_suffix}"
        )
        expect(probe_page).to_have_url(re.compile(r"/workspace$"), timeout=15_000)
        other_labels = probe_page.locator("#ownPages .page-title-label")
        expect(other_labels).to_have_count(1)
        expect(other_labels.first).to_have_text(f"Synthetic Page {other_suffix}")
        expect(probe_page.locator("#pageView")).to_be_hidden()
    finally:
        probe_page.close()
    restored_capture = switch_to(initial_suffix, "WORKSPACE-SWITCHER")

    scoped_page_requests = [
        url
        for url in requested_urls
        if re.search(r"/api/notion/workspaces/[^/]+/pages(?:\?|$)", url)
    ]
    global_search_requests = [
        url for url in requested_urls if "/api/notion/search?" in url
    ]
    legacy_page_list_requests = [
        url
        for url in requested_urls
        if re.search(r"/api/notion/pages(?:\?|$)", url)
    ]
    assert len(scoped_page_requests) >= 3
    assert global_search_requests == []
    assert legacy_page_list_requests == []
    capture = _capture(
        scenario_id="UI-SHELL-001",
        state={"count": 1, "result": "pass"},
        keyboard={"result": "pass"},
        focus={"sequence": ["WORKSPACE-SIDEBAR-GROUP", "WORKSPACE-SWITCHER"]},
        a11y={"name": "WORKSPACE-SIDEBAR-GROUP", "role": "group"},
        semantic_visual={"region": "SIDEBAR", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"WORKSPACE-SWITCHER": _rounded_box(page.locator("#workspaceName"))},
    )
    action_captures = {
        "UI-SHELL-001-A01": other_capture,
        "UI-SHELL-001-A02": restored_capture,
    }
    return {
        "active_workspace_restored": True,
        "action_capture_sha256": {
            action_id: capture_sha256(value)
            for action_id, value in action_captures.items()
        },
        "action_captures": action_captures,
        "capture_sha256": {"UI-SHELL-001": capture_sha256(capture)},
        "cross_workspace_direct_open_rejected": True,
        "focus_restored": True,
        "global_search_requests": 0,
        "legacy_page_list_requests": 0,
        "scoped_page_request_count": len(scoped_page_requests),
        "synthetic_only": True,
        "workspace_page_counts": [1, 1, 1],
    }


def _run_workspace_switch_journey(
    playwright: object,
    data_root: Path,
    *,
    viewport: dict[str, int] | None = None,
) -> dict[str, object]:
    with _isolated_server(
        data_root,
        seed_navigation_workspaces=True,
    ) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport=viewport or {"height": 900, "width": 1440},
            )
            try:
                return _exercise_workspace_switch_isolation(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _exercise_home_page_breadcrumb_hierarchy(
    page: object,
    origin: str,
    user_id: str,
    password: str,
) -> dict[str, object]:
    from playwright.sync_api import expect

    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    expect(page.locator("#workspaceTitle")).to_have_text(
        re.compile(r"^Synthetic Workspace [AB]$"),
        timeout=30_000,
    )

    home = page.locator("#homeView")
    page_view = page.locator("#pageView")
    topbar = page.locator(".topbar")
    breadcrumb = page.locator("#breadcrumb")

    home_nav = page.locator("#homeNavBtn")
    workspace_home = page.locator("#workspaceHomeBtn")
    workspace_home.focus()
    workspace_home.press("Enter")
    expect(home).to_be_visible()
    expect(page_view).to_be_hidden()
    expect(home).to_be_focused()
    expect(home_nav).to_have_attribute("role", "tab")
    expect(home_nav).to_have_attribute("aria-selected", "true")
    home_focus = _focus_alias(page)
    home_box = _rounded_box(home)

    row = page.locator("#ownPages .page-row")
    expect(row).to_have_count(1)
    current_title = row.locator(".page-title-label").inner_text()
    other_title = "Synthetic Page B" if current_title.endswith("A") else "Synthetic Page A"
    row.focus()
    row.press("Enter")
    expect(page_view).to_be_visible(timeout=15_000)
    expect(home).to_be_hidden()
    expect(page.locator("#pageTitle")).to_have_value(current_title)
    expect(breadcrumb.locator(".breadcrumb-segment").last).to_have_text(
        current_title
    )
    expect(page_view).to_be_focused()
    page_focus = _focus_alias(page)
    assert other_title not in topbar.inner_text()
    assert other_title not in breadcrumb.inner_text()

    workspace_home.click()
    expect(home).to_be_visible()
    expect(home).not_to_be_focused()
    pointer_home_content_blurred = _focus_alias(page) != "HOME-MAIN"
    row = page.locator("#ownPages .page-row")
    expect(row).to_have_count(1)
    row.click()
    expect(page_view).to_be_visible(timeout=15_000)
    expect(page_view).not_to_be_focused()
    pointer_page_content_blurred = _focus_alias(page) != "PAGE-CANVAS"

    capture = _capture(
        scenario_id="UI-SHELL-003",
        state={"count": 1, "result": "pass"},
        keyboard={"result": "pass"},
        focus={"sequence": [home_focus, page_focus]},
        a11y={"name": "PAGE-CANVAS", "role": "region"},
        semantic_visual={"region": "PAGE-CANVAS", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={
            "BREADCRUMB": _rounded_box(breadcrumb),
            "HOME-MAIN": home_box,
            "PAGE-CANVAS": _rounded_box(page_view),
            "TOPBAR": _rounded_box(topbar),
        },
    )
    return {
        "breadcrumb_matches_active_page": True,
        "capture_sha256": {"UI-SHELL-003": capture_sha256(capture)},
        "focus_sequence": [home_focus, page_focus],
        "hidden_workspace_metadata_count": 0,
        "pointer_home_content_blurred": pointer_home_content_blurred,
        "pointer_page_content_blurred": pointer_page_content_blurred,
        "synthetic_only": True,
    }


def _run_home_page_breadcrumb_journey(
    playwright: object,
    data_root: Path,
) -> dict[str, object]:
    with _isolated_server(
        data_root,
        seed_navigation_workspaces=True,
    ) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport={"height": 900, "width": 1440},
            )
            try:
                return _exercise_home_page_breadcrumb_hierarchy(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _exercise_nested_page_tree(
    page: object,
    origin: str,
    user_id: str,
    password: str,
) -> dict[str, object]:
    from playwright.sync_api import expect

    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    workspace_title = page.locator("#workspaceTitle")
    expect(workspace_title).to_have_text(
        re.compile(r"^Synthetic Workspace [AB]$"),
        timeout=30_000,
    )
    _wait_for_notion_ready(page)
    if workspace_title.inner_text() != "Synthetic Workspace A":
        page.locator("#workspaceName").click(position={"x": 80, "y": 20})
        target = page.locator("[data-navigation-workspace]").filter(
            has_text="Synthetic Workspace A"
        )
        expect(target).to_have_count(1)
        target.click()
        expect(workspace_title).to_have_text("Synthetic Workspace A")

    tree = page.locator("#ownPages")
    root = tree.locator('.page-row[aria-label="Synthetic Page A"]')
    child = tree.locator('.page-row[aria-label="Synthetic Child A"]')
    expect(root).to_have_count(1)

    disclosure = root.locator("[data-page-toggle]")
    # Establish the scenario's required collapsed starting state without
    # adding a synthetic mutation or changing the durable page tree.
    root.focus()
    if child.count() == 1:
        disclosure.click()
    assert child.count() == 0
    expect(root).to_be_focused()

    disclosure.click()
    assert child.count() == 1
    expect(root).to_be_focused()
    expect(root).to_have_attribute("role", "treeitem")
    expect(root).to_have_attribute("aria-expanded", "true")
    expect(tree).to_have_attribute("role", "tree")
    expanded_focus = "TREE-ROOT"

    root.press("ArrowLeft")
    assert child.count() == 0
    expect(root).to_be_focused()
    expect(root).to_have_attribute("aria-expanded", "false")
    collapsed_focus = "TREE-ROOT"

    capture = _capture(
        scenario_id="UI-TREE-001",
        state={"count": 1, "result": "pass"},
        keyboard={"result": "pass"},
        focus={"sequence": [expanded_focus, collapsed_focus]},
        a11y={"name": "TREE-ROOT", "role": "treeitem"},
        semantic_visual={"region": "PAGE-TREE", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"PAGE-TREE": _rounded_box(tree)},
    )
    return {
        "capture_sha256": {"UI-TREE-001": capture_sha256(capture)},
        "collapsed_descendant_count": 0,
        "arrow_left_collapsed": True,
        "focus_sequence": [expanded_focus, collapsed_focus],
        "root_aria_expanded": root.get_attribute("aria-expanded"),
        "synthetic_only": True,
        "tree_role": tree.get_attribute("role"),
    }


def _run_nested_page_tree_journey(
    playwright: object,
    data_root: Path,
) -> dict[str, object]:
    with _isolated_server(
        data_root,
        seed_navigation_tree=True,
    ) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport={"height": 900, "width": 1440},
            )
            try:
                return _exercise_nested_page_tree(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _exercise_page_tree_mutations(
    page: object,
    origin: str,
    user_id: str,
    password: str,
) -> dict[str, object]:
    from playwright.sync_api import expect

    requested_urls: list[str] = []
    page.on("request", lambda request: requested_urls.append(request.url))
    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    workspace_title = page.locator("#workspaceTitle")
    expect(workspace_title).to_have_text(
        re.compile(r"^Synthetic Workspace [AB]$"),
        timeout=30_000,
    )
    if workspace_title.inner_text() != "Synthetic Workspace A":
        page.locator("#workspaceName").click(position={"x": 80, "y": 20})
        target = page.locator("[data-navigation-workspace]").filter(
            has_text="Synthetic Workspace A"
        )
        expect(target).to_have_count(1)
        target.click()
        expect(workspace_title).to_have_text("Synthetic Workspace A")

    tree = page.locator("#ownPages")
    root = tree.locator(".page-row").filter(has_text="Synthetic Page A")
    child_a = tree.locator(".page-row").filter(has_text="Synthetic Child A")
    expect(root).to_have_count(1)
    expect(child_a).to_have_count(1)
    root_id = root.get_attribute("data-page-id")
    assert root_id

    page.locator("#sidebarNewBtn").click()
    new_page_menu = page.locator("#newPageMenu")
    expect(new_page_menu).to_be_visible()
    page_type = new_page_menu.locator('[data-new-page-type="page"]')
    expect(page_type).to_be_focused()
    page_type.click()
    title = page.locator("#pageTitle")
    expect(title).to_be_focused(timeout=15_000)
    title.fill("Synthetic Child B")
    expect(page.locator("#status")).to_have_text("저장됨", timeout=20_000)
    child_b = tree.locator(".page-row").filter(has_text="Synthetic Child B")
    expect(child_b).to_have_count(1)
    expect(child_b).to_have_class(re.compile(r"\bactive\b"))
    expect(
        page.locator("#breadcrumb .breadcrumb-segment").last
    ).to_have_text("Synthetic Child B")
    title_focus = "PAGE-TITLE"
    viewport = page.viewport_size
    assert viewport is not None
    title_capture = _capture(
        scenario_id="UI-TREE-002",
        state={"count": 1, "state": "committed"},
        keyboard={"result": "pass"},
        focus={"name": "PAGE-TITLE", "state": "focused"},
        a11y={"name": "PAGE-TITLE", "role": "TEXTBOX"},
        semantic_visual={"region": "PAGE-TITLE", "state": "visible"},
        error_recovery={"result": "unchanged"},
        geometry={"PAGE-TITLE": _rounded_box(title)},
        viewport=viewport,
    )

    page.locator("#pageMoreBtn").click()
    move_action = page.locator('#pageMenu [data-page-action="move"]')
    expect(move_action).to_be_visible()
    move_action.click()
    move_target = page.locator("#movePageSearch")
    expect(move_target).to_be_focused()
    move_target.fill("Synthetic Page A")
    move_result = page.locator('#movePageResults [role="option"]').filter(
        has_text="Synthetic Page A"
    )
    expect(move_result).to_have_count(1)
    move_result.click()
    expect(page.locator("#status")).to_have_text("페이지를 옮겼습니다.", timeout=20_000)
    child_b = tree.locator(".page-row").filter(has_text="Synthetic Child B")
    expect(child_b).to_have_attribute("data-parent-id", root_id)
    expect(child_b).to_have_class(re.compile(r"\bactive\b"))
    breadcrumb_items = page.locator("#breadcrumb button")
    expect(breadcrumb_items).to_have_count(2)
    expect(breadcrumb_items.nth(0)).to_have_text("Synthetic Page A")
    expect(breadcrumb_items.nth(1)).to_have_text("Synthetic Child B")
    expect(page.locator("#pageMoreBtn")).to_be_focused()
    nested_focus = "PAGE-MENU"
    nested_capture = _capture(
        scenario_id="UI-TREE-002",
        state={"count": 2, "state": "committed"},
        keyboard={"result": "not_applicable"},
        focus={"name": "PAGE-MENU", "state": "focused"},
        a11y={"name": "PAGE-MENU", "role": "BUTTON"},
        semantic_visual={"region": "PAGE-TREE", "state": "visible"},
        error_recovery={"result": "unchanged"},
        geometry={"PAGE-TREE": _rounded_box(tree)},
        viewport=viewport,
    )

    child_a = tree.locator(".page-row").filter(has_text="Synthetic Child A")
    child_b.drag_to(child_a, target_position={"x": 10, "y": 1})
    children = tree.locator(f'.page-row[data-parent-id="{root_id}"]')
    expect(children).to_have_count(2)
    expect(children.nth(0).locator(".page-title-label")).to_have_text(
        "Synthetic Child B"
    )
    expect(children.nth(1).locator(".page-title-label")).to_have_text(
        "Synthetic Child A"
    )
    child_b = tree.locator(".page-row").filter(has_text="Synthetic Child B")
    expect(child_b).to_be_focused()
    reordered_focus = "TREE-CHILD-B"
    reordered_capture = _capture(
        scenario_id="UI-TREE-002",
        state={
            "count": 2,
            "order": ["TREE-CHILD-B", "TREE-CHILD-A"],
            "state": "committed",
        },
        keyboard={"result": "not_applicable"},
        focus={"name": "TREE-CHILD-B", "state": "focused"},
        a11y={"name": "TREE-CHILD-B", "role": "TREEITEM"},
        semantic_visual={"region": "PAGE-TREE", "state": "visible"},
        error_recovery={"result": "unchanged"},
        geometry={"PAGE-TREE": _rounded_box(tree)},
        viewport=viewport,
    )

    move_requests_before = len(
        [url for url in requested_urls if re.search(r"/api/notion/pages/[^/]+/move$", url)]
    )
    root = tree.locator(".page-row").filter(has_text="Synthetic Page A")
    root.focus()
    root.drag_to(child_b)
    page.wait_for_timeout(300)
    move_requests_after = len(
        [url for url in requested_urls if re.search(r"/api/notion/pages/[^/]+/move$", url)]
    )
    expect(root).to_have_attribute("data-parent-id", "")
    expect(breadcrumb_items).to_have_count(2)
    expect(breadcrumb_items.nth(0)).to_have_text("Synthetic Page A")
    expect(breadcrumb_items.nth(1)).to_have_text("Synthetic Child B")
    expect(root).to_be_focused()
    refused_focus = "TREE-ROOT"
    refused_capture = _capture(
        scenario_id="UI-TREE-002",
        state={
            "count": 2,
            "order": ["TREE-CHILD-B", "TREE-CHILD-A"],
            "state": "unchanged",
        },
        keyboard={"result": "not_applicable"},
        focus={"name": "TREE-ROOT", "state": "focused"},
        a11y={"name": "TREE-ROOT", "role": "TREEITEM"},
        semantic_visual={"region": "PAGE-TREE", "state": "visible"},
        error_recovery={"result": "unchanged"},
        geometry={"PAGE-TREE": _rounded_box(tree)},
        viewport=viewport,
    )

    workspace_create_requests = [
        url
        for url in requested_urls
        if re.search(r"/api/notion/workspaces/[^/]+/pages$", url)
    ]
    legacy_create_requests = [
        url for url in requested_urls if re.search(r"/api/notion/pages$", url)
    ]
    assert len(workspace_create_requests) == 1
    assert legacy_create_requests == []
    assert move_requests_after == move_requests_before

    capture = _capture(
        scenario_id="UI-TREE-002",
        state={"count": 3, "result": "pass"},
        keyboard={"result": "pass"},
        focus={
            "sequence": [
                title_focus,
                nested_focus,
                reordered_focus,
                refused_focus,
            ]
        },
        a11y={"name": "TREE-CHILD-B", "role": "treeitem"},
        semantic_visual={"region": "PAGE-TREE", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"PAGE-TREE": _rounded_box(tree)},
    )
    action_captures = {
        "UI-TREE-002-A03": title_capture,
        "UI-TREE-002-A07": nested_capture,
        "UI-TREE-002-A08": reordered_capture,
        "UI-TREE-002-A09": refused_capture,
    }
    return {
        "active_path_coherent": True,
        "action_capture_sha256": {
            action_id: capture_sha256(value)
            for action_id, value in action_captures.items()
        },
        "action_captures": action_captures,
        "capture_sha256": {"UI-TREE-002": capture_sha256(capture)},
        "failed_move_request_delta": move_requests_after - move_requests_before,
        "focus_sequence": [
            title_focus,
            nested_focus,
            reordered_focus,
            refused_focus,
        ],
        "legacy_create_requests": len(legacy_create_requests),
        "nested_child_count": 2,
        "synthetic_only": True,
        "workspace_create_requests": len(workspace_create_requests),
    }


def _run_page_tree_mutation_journey(
    playwright: object,
    data_root: Path,
    *,
    viewport: dict[str, int] | None = None,
) -> dict[str, object]:
    with _isolated_server(
        data_root,
        seed_navigation_tree=True,
    ) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport=viewport or {"height": 900, "width": 1440},
            )
            try:
                return _exercise_page_tree_mutations(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _exercise_page_tree_lifecycle(
    page: object,
    origin: str,
    user_id: str,
    password: str,
    data_root: Path,
) -> dict[str, object]:
    from playwright.sync_api import expect

    requests: list[tuple[str, str]] = []
    page.on("request", lambda request: requests.append((request.method, request.url)))
    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    workspace_title = page.locator("#workspaceTitle")
    expect(workspace_title).to_have_text(
        re.compile(r"^Synthetic Workspace [AB]$"), timeout=30_000
    )
    if workspace_title.inner_text() != "Synthetic Workspace A":
        page.locator("#workspaceName").click(position={"x": 80, "y": 20})
        target = page.locator("[data-navigation-workspace]").filter(
            has_text="Synthetic Workspace A"
        )
        expect(target).to_have_count(1)
        target.click()
        expect(workspace_title).to_have_text("Synthetic Workspace A")

    tree = page.locator("#ownPages")
    root = tree.locator(".page-row").filter(has_text="Synthetic Page A")
    child = tree.locator(".page-row").filter(has_text="Synthetic Child A")
    expect(root).to_have_count(1)
    expect(child).to_have_count(1)
    root.click()
    expect(page.locator("#pageTitle")).to_have_value("Synthetic Page A")

    before_publish = _canonical_publication_state(data_root)
    approve_label = _open_publication_preview(page)
    _wait_for_publication_action(page, approve_label).click()
    expect(page.locator("#modalTitle")).to_have_text(
        re.compile(r"^(게시 결과|Published)$"), timeout=45_000
    )
    after_publish = _canonical_publication_state(data_root)
    assert after_publish["items"] - before_publish["items"] == 1
    assert after_publish["revisions"] - before_publish["revisions"] == 1
    page.locator("#modalClose").click()
    expect(page.locator("#modalBg")).to_be_hidden()

    root = tree.locator(".page-row").filter(has_text="Synthetic Page A")
    root.hover()
    root.locator("[data-page-menu]").click()
    duplicate_action = page.locator('#pageMenu [data-page-action="duplicate"]')
    expect(duplicate_action).to_be_visible()
    duplicate_action.click()
    expect(page.locator("#status")).to_have_text(
        "페이지를 복제했습니다.", timeout=30_000
    )
    duplicate_root = tree.locator(".page-row").filter(
        has_text="Synthetic Page A copy"
    )
    expect(duplicate_root).to_have_count(1)
    duplicate_root_id = duplicate_root.get_attribute("data-page-id")
    assert duplicate_root_id
    duplicate_child = tree.locator(
        f'.page-row[data-parent-id="{duplicate_root_id}"]'
    ).filter(has_text="Synthetic Child A")
    expect(duplicate_child).to_have_count(1)
    expect(duplicate_root).to_have_class(re.compile(r"\bactive\b"))
    page_menu_trigger = page.locator("#pageMoreBtn")
    expect(page_menu_trigger).to_be_focused()
    expect(
        page.locator("#breadcrumb .breadcrumb-segment").last
    ).to_have_text("Synthetic Page A copy")
    duplicate_focus = "PAGE-MENU"
    viewport = page.viewport_size
    assert viewport is not None
    duplicate_capture = _capture(
        scenario_id="UI-TREE-003",
        state={"count": 2, "state": "committed"},
        keyboard={"result": "not_applicable"},
        focus={"name": "PAGE-MENU", "state": "focused"},
        a11y={"name": "PAGE-MENU", "role": "BUTTON"},
        semantic_visual={"region": "PAGE-TREE", "state": "visible"},
        error_recovery={"result": "unchanged"},
        geometry={"PAGE-TREE": _rounded_box(tree)},
        viewport=viewport,
    )

    duplicate_root.hover()
    duplicate_root.locator("[data-page-menu]").click()
    trash_action = page.locator('#pageMenu [data-page-action="trash"]')
    expect(trash_action).to_be_visible()
    page.once("dialog", lambda dialog: dialog.accept())
    trash_action.click()
    expect(page.locator("#status")).to_have_text(
        "휴지통으로 이동했습니다.", timeout=30_000
    )
    expect(duplicate_root).to_have_count(0)
    expect(duplicate_child).to_have_count(0)
    expect(tree.locator(".page-row")).to_have_count(2)
    expect(page_menu_trigger).to_be_hidden()
    trash_button = page.locator("#trashBtn")
    expect(trash_button).to_be_visible()
    expect(trash_button).to_be_focused()
    trash_focus = "TRASH-BUTTON"
    after_archive = _canonical_publication_state(data_root)
    assert after_archive == after_publish
    trash_capture = _capture(
        scenario_id="UI-TREE-003",
        state={"count": 2, "state": "committed"},
        keyboard={"result": "not_applicable"},
        focus={"name": "TRASH-BUTTON", "state": "focused"},
        a11y={"name": "TRASH-BUTTON", "role": "BUTTON"},
        semantic_visual={"region": "PAGE-TREE", "state": "visible"},
        error_recovery={"result": "unchanged"},
        geometry={"PAGE-TREE": _rounded_box(tree)},
        viewport=viewport,
    )

    trash_button.click()
    trash_overlay = page.locator("#trashOverlay")
    expect(trash_overlay).to_be_visible()
    expect(page.locator("#trashSearchInput")).to_be_focused()
    trash_rows = page.locator("#trashResults .trash-row")
    expect(trash_rows).to_have_count(2)
    duplicate_trash_row = trash_rows.filter(has_text="Synthetic Page A copy")
    expect(duplicate_trash_row).to_have_count(1)
    duplicate_trash_row.locator("[data-restore]").click()
    expect(page.locator("#status")).to_have_text("페이지 복원됨", timeout=30_000)
    expect(trash_overlay).to_be_hidden()
    restored_root = tree.locator(".page-row").filter(
        has_text="Synthetic Page A copy"
    )
    expect(restored_root).to_have_count(1)
    expect(restored_root).to_have_class(re.compile(r"\bactive\b"))
    restored_action_button = page.locator("#breadcrumb .breadcrumb-segment").last
    expect(restored_action_button).to_be_focused()
    restored_root_id = restored_root.get_attribute("data-page-id")
    assert restored_root_id == duplicate_root_id
    restored_child = tree.locator(
        f'.page-row[data-parent-id="{restored_root_id}"]'
    ).filter(has_text="Synthetic Child A")
    expect(restored_child).to_have_count(1)
    expect(
        page.locator("#breadcrumb .breadcrumb-segment").last
    ).to_have_text("Synthetic Page A copy")
    restored_focus = "RESTORE-POST-ACTION-BUTTON"
    after_restore = _canonical_publication_state(data_root)
    assert after_restore == after_publish
    restored_capture = _capture(
        scenario_id="UI-TREE-003",
        state={"count": 2, "state": "committed"},
        keyboard={"result": "not_applicable"},
        focus={"name": "RESTORE-POST-ACTION-BUTTON", "state": "focused"},
        a11y={"name": "RESTORE-POST-ACTION-BUTTON", "role": "BUTTON"},
        semantic_visual={"region": "PAGE-TREE", "state": "visible"},
        error_recovery={"result": "unchanged"},
        geometry={"PAGE-TREE": _rounded_box(tree)},
        viewport=viewport,
    )

    workspace_duplicate_requests = [
        item
        for item in requests
        if re.search(r"/api/notion/workspaces/[^/]+/pages/[^/]+/duplicate$", item[1])
    ]
    workspace_archive_requests = [
        item
        for item in requests
        if re.search(r"/api/notion/workspaces/[^/]+/pages/[^/]+/trash$", item[1])
    ]
    workspace_restore_requests = [
        item
        for item in requests
        if re.search(r"/api/notion/workspaces/[^/]+/trash/[^/]+/restore$", item[1])
    ]
    legacy_lifecycle_requests = [
        item
        for item in requests
        if re.search(r"/api/notion/(?:pages/[^/]+/duplicate|trash(?:\?|/|$))", item[1])
    ]
    permanent_delete_requests = [item for item in requests if item[0] == "DELETE"]
    assert len(workspace_duplicate_requests) == 1
    assert len(workspace_archive_requests) == 1
    assert len(workspace_restore_requests) == 1
    assert legacy_lifecycle_requests == []
    assert permanent_delete_requests == []

    capture = _capture(
        scenario_id="UI-TREE-003",
        state={"count": 2, "result": "pass"},
        keyboard={"result": "pass"},
        focus={
            "sequence": [duplicate_focus, trash_focus, restored_focus],
        },
        a11y={"name": "RESTORE-POST-ACTION-BUTTON", "role": "button"},
        semantic_visual={"region": "PAGE-TREE", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"PAGE-TREE": _rounded_box(tree)},
    )
    action_captures = {
        "UI-TREE-003-A01": duplicate_capture,
        "UI-TREE-003-A02": trash_capture,
        "UI-TREE-003-A03": restored_capture,
    }
    return {
        "action_capture_sha256": {
            action_id: capture_sha256(value)
            for action_id, value in action_captures.items()
        },
        "action_captures": action_captures,
        "capture_sha256": {"UI-TREE-003": capture_sha256(capture)},
        "duplicate_subtree_count": 2,
        "focus_sequence": [duplicate_focus, trash_focus, restored_focus],
        "legacy_lifecycle_requests": len(legacy_lifecycle_requests),
        "permanent_delete_requests": len(permanent_delete_requests),
        "second_brain_item_delta": after_restore["items"] - after_publish["items"],
        "second_brain_revision_delta": (
            after_restore["revisions"] - after_publish["revisions"]
        ),
        "synthetic_only": True,
        "workspace_archive_requests": len(workspace_archive_requests),
        "workspace_duplicate_requests": len(workspace_duplicate_requests),
        "workspace_restore_requests": len(workspace_restore_requests),
    }


def _run_page_tree_lifecycle_journey(
    playwright: object,
    data_root: Path,
    *,
    viewport: dict[str, int] | None = None,
) -> dict[str, object]:
    with _isolated_server(
        data_root,
        seed_navigation_tree=True,
    ) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport=viewport or {"height": 900, "width": 1440},
            )
            try:
                return _exercise_page_tree_lifecycle(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                    data_root,
                )
            finally:
                context.close()
        finally:
            try:
                browser.close()
            except Exception:
                if browser.is_connected():
                    raise


def _exercise_korean_ime_composition(
    page: object,
    origin: str,
    user_id: str,
    password: str,
    data_root: Path,
) -> dict[str, object]:
    from playwright.sync_api import expect

    save_requests: list[str] = []
    page.on(
        "request",
        lambda request: save_requests.append(request.url)
        if request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", request.url)
        else None,
    )
    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    expect(page.locator("#homeNavBtn")).to_be_visible(timeout=20_000)
    expect(page.locator("#workspaceTitle")).to_contain_text(
        "UI1 Owner",
        timeout=30_000,
    )
    _wait_for_notion_ready(page)

    page.locator("#sidebarNewBtn").click()
    page.locator('#newPageMenu [data-new-page-type="page"]').click()
    title = page.locator("#pageTitle")
    expect(title).to_be_visible(timeout=15_000)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        title.fill("UI-EDITOR-004")
    expect(page.locator("#status")).to_have_class(re.compile(r"\bok\b"), timeout=20_000)
    save_requests.clear()

    editor = page.locator(".block-editor[contenteditable='true']").first
    expect(editor).to_be_visible()
    editor.focus()
    before_publication = _canonical_publication_state(data_root)
    page.evaluate(
        """() => {
          const editor = document.querySelector(".block-editor[contenteditable='true']");
          if (!editor) throw new Error('editor unavailable');
          editor.dispatchEvent(new CompositionEvent('compositionstart', {
            bubbles: true,
            composed: true,
            data: '',
          }));
          editor.textContent = 'ㅎ';
          editor.dispatchEvent(new InputEvent('input', {
            bubbles: true,
            composed: true,
            data: 'ㅎ',
            inputType: 'insertCompositionText',
            isComposing: true,
          }));
          editor.dispatchEvent(new KeyboardEvent('keydown', {
            bubbles: true,
            composed: true,
            isComposing: true,
            key: 'Enter',
          }));
        }"""
    )
    page.wait_for_timeout(1_200)
    intermediate_save_count = len(save_requests)
    assert intermediate_save_count == 0
    block_count_during_composition = page.locator("#blocks .block-row").count()
    assert block_count_during_composition == 1

    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        page.evaluate(
            """() => {
              const editor = document.querySelector(".block-editor[contenteditable='true']");
              if (!editor) throw new Error('editor unavailable');
              const value = '한글 조합 입력';
              editor.textContent = value;
              editor.dispatchEvent(new InputEvent('input', {
                bubbles: true,
                composed: true,
                data: value,
                inputType: 'insertCompositionText',
                isComposing: true,
              }));
              editor.dispatchEvent(new CompositionEvent('compositionend', {
                bubbles: true,
                composed: true,
                data: value,
              }));
            }"""
        )
    assert len(save_requests) == 1
    expect(editor).to_be_focused()
    viewport = page.viewport_size
    assert viewport is not None
    composition_capture = _capture(
        scenario_id="UI-EDITOR-004",
        state={"count": 1, "state": "committed"},
        keyboard={"result": "pass"},
        focus={"name": "EDITOR-BLOCK", "state": "focused"},
        a11y={"name": "EDITOR-BLOCK", "role": "TEXTBOX"},
        semantic_visual={"region": "PAGE-ROOT", "state": "visible"},
        error_recovery={"result": "unchanged"},
        geometry={"EDITOR-BLOCK": _rounded_box(editor)},
        viewport=viewport,
    )

    page.reload(wait_until="domcontentloaded")
    editor = page.locator(".block-editor[contenteditable='true']").first
    expect(editor).to_have_text("한글 조합 입력", timeout=20_000)
    after_publication = _canonical_publication_state(data_root)
    assert after_publication == before_publication

    capture = _capture(
        scenario_id="UI-EDITOR-004",
        state={"count": 1, "result": "pass"},
        keyboard={"result": "pass"},
        focus={"sequence": ["EDITOR-BLOCK", "PAGE-CANVAS"]},
        a11y={"name": "EDITOR-BLOCK", "role": "textbox"},
        semantic_visual={"region": "PAGE-ROOT", "state": "restored"},
        error_recovery={"result": "pass"},
        geometry={"PAGE-ROOT": _rounded_box(page.locator("#pageView"))},
    )
    return {
        "action_capture_sha256": {
            "UI-EDITOR-004-A01": capture_sha256(composition_capture),
        },
        "action_captures": {
            "UI-EDITOR-004-A01": composition_capture,
        },
        "capture_sha256": {"UI-EDITOR-004": capture_sha256(capture)},
        "block_count_during_composition": block_count_during_composition,
        "committed_save_count": len(save_requests),
        "content_restored": True,
        "focus_sequence": ["EDITOR-BLOCK", "PAGE-CANVAS"],
        "intermediate_save_count": intermediate_save_count,
        "second_brain_item_delta": (
            after_publication["items"] - before_publication["items"]
        ),
        "second_brain_revision_delta": (
            after_publication["revisions"] - before_publication["revisions"]
        ),
        "synthetic_only": True,
    }


def _run_korean_ime_journey(
    playwright: object,
    data_root: Path,
    *,
    viewport: dict[str, int] | None = None,
) -> dict[str, object]:
    with _isolated_server(data_root) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport=viewport or {"height": 900, "width": 1440},
            )
            try:
                return _exercise_korean_ime_composition(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                    data_root,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _exercise_slash_menu_block_transform(
    page: object,
    origin: str,
    user_id: str,
    password: str,
    data_root: Path,
) -> dict[str, object]:
    from playwright.sync_api import expect

    save_requests: list[str] = []
    page.on(
        "request",
        lambda request: save_requests.append(request.url)
        if request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", request.url)
        else None,
    )
    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    expect(page.locator("#homeNavBtn")).to_be_visible(timeout=20_000)
    expect(page.locator("#workspaceTitle")).to_contain_text(
        "UI1 Owner",
        timeout=30_000,
    )
    _wait_for_notion_ready(page)

    page.locator("#sidebarNewBtn").click()
    page.locator('#newPageMenu [data-new-page-type="page"]').click()
    title = page.locator("#pageTitle")
    expect(title).to_be_visible(timeout=15_000)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        title.fill("UI-EDITOR-002")
    expect(page.locator("#status")).to_have_class(re.compile(r"\bok\b"), timeout=20_000)
    save_requests.clear()

    before_publication = _canonical_publication_state(data_root)
    editor = page.locator(".block-editor[contenteditable='true']").first
    menu = page.locator("#blockMenu")
    editor.focus()
    editor.press("/")
    expect(menu).to_be_visible()
    expect(menu).to_have_attribute("role", "dialog")
    expect(editor).to_be_focused()
    expect(editor).to_have_attribute("role", "combobox")
    expect(editor).to_have_attribute("aria-expanded", "true")
    menu_focus = _focus_alias(page)
    assert menu_focus == "PAGE-EDITOR-GROUP"

    menu_capture = _capture(
        scenario_id="UI-EDITOR-002",
        state={"count": 1, "result": "pass"},
        keyboard={"result": "pass"},
        focus={"name": menu_focus, "state": "focused"},
        a11y={"name": "SLASH-MENU", "role": "DIALOG"},
        semantic_visual={"region": "SLASH-MENU", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"SLASH-MENU": _rounded_box(menu)},
    )

    page.keyboard.type("unsupported-command")
    expect(menu.locator(".menu-empty")).to_be_visible()
    editor.press("Escape")
    expect(menu).to_be_hidden()
    expect(editor).to_be_focused()
    expect(editor).to_have_text("/unsupported-command")

    editor.fill("")
    editor.press("/")
    expect(editor).to_be_focused()
    heading = menu.locator('[data-insert-type="heading1"]')
    expect(heading).to_be_visible()
    heading.click()
    editor = page.locator(".block-row[data-type='heading1'] .block-editor").first
    expect(menu).to_be_hidden()
    expect(editor).to_be_focused()

    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        editor.fill("합성 제목 블록")
    assert len(save_requests) == 1

    transformed_focus = _focus_alias(page)
    block_capture = _capture(
        scenario_id="UI-EDITOR-002",
        state={"count": 1, "result": "pass"},
        keyboard={"result": "pass"},
        focus={"name": transformed_focus, "state": "focused"},
        a11y={"name": "BLOCK-HEADING", "role": "TEXTBOX"},
        semantic_visual={"region": "PAGE-ROOT", "state": "restored"},
        error_recovery={"result": "pass"},
        geometry={"BLOCK-HEADING": _rounded_box(editor)},
    )

    page.reload(wait_until="domcontentloaded")
    editor = page.locator(".block-row[data-type='heading1'] .block-editor").first
    expect(editor).to_have_text("합성 제목 블록", timeout=20_000)
    after_publication = _canonical_publication_state(data_root)
    assert after_publication == before_publication
    return {
        "action_captures": {
            "UI-EDITOR-002-A01": menu_capture,
            "UI-EDITOR-002-A02": block_capture,
        },
        "capture_sha256": {
            "UI-EDITOR-002-A01": capture_sha256(menu_capture),
            "UI-EDITOR-002-A02": capture_sha256(block_capture),
        },
        "content_restored": True,
        "focus_sequence": [menu_focus, transformed_focus],
        "save_count": len(save_requests),
        "second_brain_item_delta": (
            after_publication["items"] - before_publication["items"]
        ),
        "second_brain_revision_delta": (
            after_publication["revisions"] - before_publication["revisions"]
        ),
        "synthetic_only": True,
        "unsupported_command_preserved": True,
    }


def _run_slash_menu_block_transform_journey(
    playwright: object,
    data_root: Path,
) -> dict[str, object]:
    with _isolated_server(data_root) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport={"height": 900, "width": 1440},
            )
            try:
                return _exercise_slash_menu_block_transform(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                    data_root,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _exercise_selection_clipboard_history(
    page: object,
    origin: str,
    user_id: str,
    password: str,
    data_root: Path,
) -> dict[str, object]:
    from playwright.sync_api import expect

    save_requests: list[str] = []
    page.on(
        "request",
        lambda request: save_requests.append(request.url)
        if request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", request.url)
        else None,
    )
    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    expect(page.locator("#homeNavBtn")).to_be_visible(timeout=20_000)
    expect(page.locator("#workspaceTitle")).to_contain_text(
        "UI1 Owner",
        timeout=30_000,
    )
    _wait_for_notion_ready(page)

    page.locator("#sidebarNewBtn").click()
    page.locator('#newPageMenu [data-new-page-type="page"]').click()
    title = page.locator("#pageTitle")
    expect(title).to_be_visible(timeout=15_000)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        title.fill("UI-EDITOR-003")
    expect(page.locator("#status")).to_have_class(re.compile(r"\bok\b"), timeout=20_000)

    first = page.locator(".block-editor[contenteditable='true']").first
    first.fill("첫 번째 블록")
    first.press("End")
    first.press("Enter")
    second = page.locator(".block-editor[contenteditable='true']").nth(1)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        second.fill("두 번째 블록")
    expect(page.locator("#status")).to_have_class(re.compile(r"\bok\b"), timeout=20_000)
    save_requests.clear()
    before_publication = _canonical_publication_state(data_root)

    first = page.locator(".block-editor[contenteditable='true']").first
    first.focus()
    first.press("Control+A")
    first.press("Control+A")
    selected = page.locator("#blocks .block-row.selected")
    expect(selected).to_have_count(2)
    expect(page.locator("#pageEditorGroup")).to_be_focused()

    selection_capture = _capture(
        scenario_id="UI-EDITOR-003",
        state={"count": 2, "result": "pass"},
        keyboard={"result": "pass"},
        focus={"sequence": ["PAGE-EDITOR-GROUP"]},
        a11y={"name": "PAGE-EDITOR-GROUP", "role": "group"},
        semantic_visual={"region": "PAGE-ROOT", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"PAGE-ROOT": _rounded_box(page.locator("#pageView"))},
    )

    page.evaluate(
        """() => {
          const editor = document.querySelector(".block-editor[contenteditable='true']");
          if (!editor) throw new Error('editor unavailable');
          editor.focus();
          const range = document.createRange();
          range.selectNodeContents(editor);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          const data = new DataTransfer();
          data.setData('text/plain', '안전한 굵은 텍스트 위험 링크');
          data.setData(
            'text/html',
            '<strong>안전한 굵은 텍스트</strong><img src=x onerror=alert(1)>'
              + '<script>window.__uiEditor003Executed = true</script>'
              + '<a href="javascript:alert(2)" onclick="alert(3)">위험 링크</a>'
          );
          editor.dispatchEvent(new ClipboardEvent('paste', {
            bubbles: true,
            cancelable: true,
            clipboardData: data,
          }));
        }"""
    )
    first = page.locator(".block-editor[contenteditable='true']").first
    expect(first).to_have_text("안전한 굵은 텍스트위험 링크")
    pasted_html = first.evaluate("editor => editor.innerHTML")
    assert "<strong>" in pasted_html
    unsafe_tokens = [
        token
        for token in ("<script", "<img", "onerror", "onclick", "javascript:")
        if token in pasted_html.lower()
    ]
    assert unsafe_tokens == [], pasted_html
    assert page.evaluate("() => Boolean(window.__uiEditor003Executed)") is False

    first.press("Control+Z")
    first = page.locator(".block-editor[contenteditable='true']").first
    expect(first).to_have_text("첫 번째 블록")
    expect(first).to_be_focused()
    first.press("Control+Shift+Z")
    first = page.locator(".block-editor[contenteditable='true']").first
    expect(first).to_have_text("안전한 굵은 텍스트위험 링크")
    expect(first).to_be_focused()

    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        page.wait_for_timeout(1_100)
    assert len(save_requests) == 1

    page.reload(wait_until="domcontentloaded")
    first = page.locator(".block-editor[contenteditable='true']").first
    expect(first).to_have_text("안전한 굵은 텍스트위험 링크", timeout=20_000)
    persisted_html = first.evaluate("editor => editor.innerHTML")
    assert "<strong>" in persisted_html
    assert all(
        token not in persisted_html.lower()
        for token in ("<script", "<img", "onerror", "onclick", "javascript:")
    )
    after_publication = _canonical_publication_state(data_root)
    assert after_publication == before_publication

    history_capture = _capture(
        scenario_id="UI-EDITOR-003",
        state={"count": 2, "result": "pass"},
        keyboard={"result": "pass"},
        focus={"sequence": ["EDITOR-BLOCK"]},
        a11y={"name": "EDITOR-BLOCK", "role": "textbox"},
        semantic_visual={"region": "PAGE-ROOT", "state": "restored"},
        error_recovery={"result": "pass"},
        geometry={"PAGE-ROOT": _rounded_box(page.locator("#pageView"))},
    )
    return {
        "capture_sha256": {
            "UI-EDITOR-003-A01": capture_sha256(selection_capture),
            "UI-EDITOR-003-A02": capture_sha256(history_capture),
        },
        "content_restored": True,
        "focus_sequence": ["PAGE-EDITOR-GROUP", "EDITOR-BLOCK"],
        "sanitized_markup_persisted": True,
        "save_count": len(save_requests),
        "second_brain_item_delta": (
            after_publication["items"] - before_publication["items"]
        ),
        "second_brain_revision_delta": (
            after_publication["revisions"] - before_publication["revisions"]
        ),
        "selected_block_count": 2,
        "synthetic_only": True,
        "unsafe_markup_persisted": False,
    }


def _run_selection_clipboard_history_journey(
    playwright: object,
    data_root: Path,
) -> dict[str, object]:
    with _isolated_server(data_root) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport={"height": 900, "width": 1440},
            )
            try:
                return _exercise_selection_clipboard_history(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                    data_root,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _exercise_editor_surface_parity(
    page: object,
    origin: str,
    user_id: str,
    password: str,
    data_root: Path,
) -> dict[str, object]:
    from playwright.sync_api import expect

    save_requests: list[str] = []
    page.on(
        "request",
        lambda request: save_requests.append(request.url)
        if request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", request.url)
        else None,
    )
    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    expect(page.locator("#homeNavBtn")).to_be_visible(timeout=20_000)
    expect(page.locator("#workspaceTitle")).to_contain_text(
        "UI1 Owner",
        timeout=30_000,
    )
    _wait_for_notion_ready(page)

    page.locator("#sidebarNewBtn").click()
    page.locator('#newPageMenu [data-new-page-type="page"]').click()
    title = page.locator("#pageTitle")
    expect(title).to_be_visible(timeout=15_000)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        title.fill("UI-EDITOR-005")
    main_editor = page.locator(".block-editor[contenteditable='true']").first
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        main_editor.fill("메인 편집 내용")
    expect(page.locator("#status")).to_have_class(re.compile(r"\bok\b"), timeout=20_000)
    save_requests.clear()
    before_publication = _canonical_publication_state(data_root)

    row = page.locator('#ownPages .page-row.active').first
    row.hover()
    row.locator('[data-page-menu]').click()
    page.locator('#pageMenu [data-page-action="side_peek"]').click()
    side_peek = page.locator("#sidePeek")
    side_title = page.locator("#sidePeekTitle")
    expect(side_peek).to_be_visible(timeout=20_000)
    expect(side_title).to_be_focused()
    side_focus = _focus_alias(page)
    side_editor = page.locator("#sidePeekBlocks [data-side-text]").first
    expect(side_editor).to_have_value("메인 편집 내용")

    side_capture = _capture(
        scenario_id="UI-EDITOR-005",
        state={"count": 1, "result": "pass"},
        keyboard={"result": "not_applicable"},
        focus={"name": side_focus, "state": "focused"},
        a11y={"name": "SIDE-PEEK", "role": "REGION"},
        semantic_visual={"region": "SIDE-PEEK", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"SIDE-PEEK": _rounded_box(side_peek)},
    )

    side_editor.fill("사이드 보기 편집 내용")
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        side_editor.press("Control+S")
    expect(page.locator("#status")).to_have_class(re.compile(r"\bok\b"), timeout=20_000)
    side_editor = page.locator("#sidePeekBlocks [data-side-text]").first
    expect(side_editor).to_be_focused()
    page.locator("#sidePeekFullBtn").click()
    expect(side_peek).to_be_hidden(timeout=20_000)
    expect(page.locator("#pageView")).to_be_visible()
    expect(page.locator("#pageView")).not_to_be_focused()
    full_focus = _focus_alias(page)
    main_editor = page.locator(".block-editor[contenteditable='true']").first
    expect(main_editor).to_have_text("사이드 보기 편집 내용")

    full_capture = _capture(
        scenario_id="UI-EDITOR-005",
        state={"count": 1, "result": "pass"},
        keyboard={"result": "not_applicable"},
        focus={"name": "PAGE-CANVAS", "state": "not_focused"},
        a11y={"name": "PAGE-CANVAS", "role": "MAIN"},
        semantic_visual={"region": "PAGE-ROOT", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"PAGE-ROOT": _rounded_box(page.locator("#pageView"))},
    )

    main_editor.fill("전체 페이지 편집 내용")
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        main_editor.press("Control+S")
    expect(page.locator("#status")).to_have_class(re.compile(r"\bok\b"), timeout=20_000)
    page.reload(wait_until="domcontentloaded")
    main_editor = page.locator(".block-editor[contenteditable='true']").first
    expect(main_editor).to_have_text("전체 페이지 편집 내용", timeout=20_000)
    after_publication = _canonical_publication_state(data_root)
    assert after_publication == before_publication

    return {
        "action_captures": {
            "UI-EDITOR-005-A01": side_capture,
            "UI-EDITOR-005-A02": full_capture,
        },
        "capture_sha256": {
            "UI-EDITOR-005-A01": capture_sha256(side_capture),
            "UI-EDITOR-005-A02": capture_sha256(full_capture),
        },
        "content_restored": True,
        "focus_sequence": [side_focus, full_focus],
        "save_count": len(save_requests),
        "second_brain_item_delta": (
            after_publication["items"] - before_publication["items"]
        ),
        "second_brain_revision_delta": (
            after_publication["revisions"] - before_publication["revisions"]
        ),
        "side_peek_closed_on_full_page": True,
        "synthetic_only": True,
    }


def _run_editor_surface_parity_journey(
    playwright: object,
    data_root: Path,
) -> dict[str, object]:
    with _isolated_server(data_root) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport={"height": 900, "width": 1440},
            )
            try:
                return _exercise_editor_surface_parity(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                    data_root,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _exercise_editor_draft_recovery(
    page: object,
    origin: str,
    user_id: str,
    password: str,
    data_root: Path,
) -> dict[str, object]:
    from playwright.sync_api import expect

    save_payloads: list[dict[str, object]] = []

    def track_save(request: object) -> None:
        if request.method != "POST" or not re.search(
            r"/api/notion/pages/[^/]+/save$",
            request.url,
        ):
            return
        try:
            payload = request.post_data_json
        except Exception:
            payload = None
        if isinstance(payload, dict):
            save_payloads.append(payload)

    page.on("request", track_save)
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    expect(page.locator("#homeNavBtn")).to_be_visible(timeout=20_000)
    expect(page.locator("#workspaceTitle")).to_contain_text(
        "UI1 Owner",
        timeout=30_000,
    )
    _wait_for_notion_ready(page)

    _create_page_from_sidebar(page)
    title = page.locator("#pageTitle")
    expect(title).to_be_visible(timeout=15_000)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        title.fill("UI-EDITOR-006")
    editor = page.locator(".block-editor[contenteditable='true']").first
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        editor.fill("기준 서버 내용")
    expect(page.locator("#status")).to_have_class(re.compile(r"\bok\b"), timeout=20_000)
    save_payloads.clear()
    before_publication = _canonical_publication_state(data_root)

    failed_once = {"value": False}

    def fail_first_save(route: object) -> None:
        if not failed_once["value"]:
            failed_once["value"] = True
            route.abort("failed")
            return
        route.continue_()

    page.route(re.compile(r".*/api/notion/pages/[^/]+/save$"), fail_first_save)
    editor.fill("네트워크 오류 뒤 복구할 편집본")
    with page.expect_request(
        lambda request: request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", request.url),
        timeout=20_000,
    ):
        editor.press("Control+S")
    expect(page.locator("#status")).to_contain_text("저장 실패", timeout=20_000)
    assert page.evaluate(
        """() => Array.from({length: sessionStorage.length}, (_, index) => sessionStorage.key(index))
          .filter(key => key && key.startsWith('code-assist.notion.editor-draft.v1:')).length"""
    ) == 1
    expect(editor).to_be_focused()
    dirty_capture = _capture(
        scenario_id="UI-EDITOR-006",
        state={"count": 1, "result": "pass"},
        keyboard={"result": "pass"},
        focus={"sequence": ["EDITOR-BLOCK"]},
        a11y={"name": "EDITOR-BLOCK", "role": "textbox"},
        semantic_visual={"region": "PAGE-ROOT", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"PAGE-ROOT": _rounded_box(page.locator("#pageView"))},
    )

    page.reload(wait_until="domcontentloaded")
    editor = page.locator(".block-editor[contenteditable='true']").first
    expect(editor).to_have_text("네트워크 오류 뒤 복구할 편집본", timeout=20_000)
    expect(page.locator("#pageView")).to_be_focused()
    expect(page.locator("#status")).to_contain_text("편집본을 복구", timeout=20_000)
    recovery_capture = _capture(
        scenario_id="UI-EDITOR-006",
        state={"count": 1, "result": "pass"},
        keyboard={"result": "pass"},
        focus={"sequence": ["PAGE-CANVAS"]},
        a11y={"name": "PAGE-CANVAS", "role": "region"},
        semantic_visual={"region": "PAGE-ROOT", "state": "restored"},
        error_recovery={"result": "pass"},
        geometry={"PAGE-ROOT": _rounded_box(page.locator("#pageView"))},
    )

    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.status == 200
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        editor.press("Control+S")
    expect(page.locator("#status")).to_have_class(re.compile(r"\bok\b"), timeout=20_000)
    assert page.evaluate(
        """() => Array.from({length: sessionStorage.length}, (_, index) => sessionStorage.key(index))
          .filter(key => key && key.startsWith('code-assist.notion.editor-draft.v1:')).length"""
    ) == 0
    assert len(save_payloads) == 2
    assert {
        key: value for key, value in save_payloads[0].items() if key != "operation_id"
    } == {
        key: value for key, value in save_payloads[1].items() if key != "operation_id"
    }
    assert save_payloads[0]["operation_id"] == save_payloads[1]["operation_id"], save_payloads

    page_id = page.url.rsplit("/page/", 1)[-1]
    snapshot_response = page.context.request.get(
        f"{origin}/api/notion/pages/{page_id}",
        headers={"Origin": origin},
    )
    assert snapshot_response.ok
    snapshot = snapshot_response.json()
    external_blocks = json.loads(json.dumps(snapshot["blocks"]))
    external_blocks[0]["content"] = "서버 최신 내용"
    auth_response = page.context.request.get(f"{origin}/api/code/auth/me")
    assert auth_response.ok
    csrf_token = auth_response.json()["csrf_token"]
    external_response = page.context.request.post(
        f"{origin}/api/notion/pages/{page_id}/save",
        headers={"Origin": origin, "X-CSRF-Token": csrf_token},
        data={
            "base_revision": snapshot["revision"],
            "operation_id": f"ui-editor-006-external-{snapshot['revision']}",
            "page": {
                "title": snapshot["page"]["title"],
                "icon": snapshot["page"].get("icon", ""),
                "cover": snapshot["page"].get("cover", ""),
                "settings": snapshot["page"].get("settings", {}),
            },
            "blocks": external_blocks,
        },
    )
    assert external_response.ok

    editor.fill("충돌 뒤 보존할 편집본")
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.status == 409
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        editor.press("Control+S")
    expect(page.locator("#modalTitle")).to_have_text("저장 충돌")
    page.get_by_role("button", name="편집본 유지", exact=True).click()
    conflict_attempt_count = len(save_payloads)

    page.reload(wait_until="domcontentloaded")
    editor = page.locator(".block-editor[contenteditable='true']").first
    expect(editor).to_have_text("충돌 뒤 보존할 편집본", timeout=20_000)
    expect(page.locator("#pageView")).to_be_focused()
    expect(page.locator("#status")).to_contain_text("저장 충돌", timeout=20_000)
    page.wait_for_timeout(1_100)
    assert len(save_payloads) == conflict_attempt_count
    assert page.evaluate(
        """() => Array.from({length: sessionStorage.length}, (_, index) => sessionStorage.key(index))
          .filter(key => key && key.startsWith('code-assist.notion.editor-draft.v1:')).length"""
    ) == 1

    editor.press("Control+S")
    expect(page.locator("#modalTitle")).to_have_text("저장 충돌")
    with page.expect_response(
        lambda response: response.request.method == "GET"
        and response.url.endswith(f"/api/notion/pages/{page_id}"),
        timeout=20_000,
    ):
        page.get_by_role(
            "button",
            name="편집본 버리고 다시 불러오기",
            exact=True,
        ).click()
    editor = page.locator(".block-editor[contenteditable='true']").first
    expect(editor).to_have_text("서버 최신 내용", timeout=20_000)
    expect(page.locator("#status")).to_have_class(re.compile(r"\bok\b"), timeout=20_000)
    assert page.evaluate(
        """() => Array.from({length: sessionStorage.length}, (_, index) => sessionStorage.key(index))
          .filter(key => key && key.startsWith('code-assist.notion.editor-draft.v1:')).length"""
    ) == 0
    after_publication = _canonical_publication_state(data_root)
    assert after_publication == before_publication

    return {
        "capture_sha256": {
            "UI-EDITOR-006-A01": capture_sha256(dirty_capture),
            "UI-EDITOR-006-A02": capture_sha256(recovery_capture),
        },
        "conflict_autosave_attempt_delta": len(save_payloads) - conflict_attempt_count,
        "conflict_draft_restored": True,
        "discard_loaded_server_revision": True,
        "focus_sequence": ["PAGE-CANVAS", "PAGE-CANVAS"],
        "network_draft_restored": True,
        "retry_operation_reused": True,
        "second_brain_item_delta": after_publication["items"] - before_publication["items"],
        "second_brain_revision_delta": after_publication["revisions"] - before_publication["revisions"],
        "synthetic_only": True,
    }


def _run_editor_draft_recovery_journey(
    playwright: object,
    data_root: Path,
) -> dict[str, object]:
    with _isolated_server(data_root) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport={"height": 900, "width": 1440},
            )
            try:
                return _exercise_editor_draft_recovery(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                    data_root,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _exercise_typed_database_round_trip(
    page: object,
    origin: str,
    user_id: str,
    password: str,
    data_root: Path,
) -> dict[str, object]:
    from playwright.sync_api import expect

    save_requests: list[str] = []
    page.on(
        "request",
        lambda request: save_requests.append(request.url)
        if request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", request.url)
        else None,
    )
    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    expect(page.locator("#homeNavBtn")).to_be_visible(timeout=20_000)
    expect(page.locator("#workspaceTitle")).to_contain_text(
        "UI1 Owner",
        timeout=30_000,
    )
    _wait_for_notion_ready(page)

    _create_page_from_sidebar(page)
    title = page.locator("#pageTitle")
    expect(title).to_be_visible(timeout=15_000)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        title.fill("UI-DB-001")
    save_requests.clear()
    before_publication = _canonical_publication_state(data_root)

    editor = page.locator(".block-editor[contenteditable='true']").first
    editor.focus()
    editor.press("/")
    menu = page.locator("#blockMenu")
    expect(menu).to_be_visible()
    table_option = menu.locator('[data-insert-type="table"]')
    expect(table_option).to_be_visible()
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        table_option.click()

    table = page.locator(".table-grid").first
    expect(table).to_be_visible()
    expect(table).not_to_be_focused()
    expect(page.locator(".database-block [role='grid']")).to_have_count(1)
    table_capture = _capture(
        scenario_id="UI-DB-001",
        state={"count": 4, "result": "pass"},
        keyboard={"result": "not_applicable"},
        focus={"name": "DATABASE-GRID", "state": "not_focused"},
        a11y={"name": "DATABASE-TABLE", "role": "GRID"},
        semantic_visual={"region": "DATABASE-TABLE", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"DATABASE-TABLE": _rounded_box(table)},
    )

    page.locator("[data-db-add-property]").first.click()
    expect(page.locator("#modalTitle")).to_have_text("속성 추가")
    page.locator("#dbPropertyName").fill("메모")
    page.locator("#dbPropertyType").select_option("text")
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        page.get_by_role("button", name="저장", exact=True).click()

    with page.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.rstrip("/").endswith("/api/notion/pages"),
        timeout=20_000,
    ):
        page.locator("[data-database-add]").first.click()
    side_peek = page.locator("#sidePeek")
    expect(side_peek).to_be_visible(timeout=20_000)
    expect(page.locator("#sidePeekTitle")).to_have_value("새 페이지")

    properties = side_peek.locator(".side-peek-properties")
    memo = properties.locator("label").filter(has_text="메모").locator("[data-db-field]")
    status = properties.locator("label").filter(has_text="상태").locator("[data-db-field]")
    date_button = properties.locator('[data-db-date-picker="date"]')
    expect(memo).to_be_visible()
    expect(status).to_be_visible()
    expect(date_button).to_be_visible()
    memo.fill("합성 메모")
    memo.press("Tab")
    status.fill("진행 중")
    status.press("Tab")
    date_button.click()
    picker = page.locator('.database-date-picker')
    expect(picker).to_be_visible()
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        picker.locator('[data-date-start]').fill("2026-08-20")
        picker.locator('[data-date-start]').press("Tab")
    expect(page.locator("#status")).to_have_class(re.compile(r"\bok\b"), timeout=20_000)
    row_title = page.locator("#sidePeekTitle")
    row_title.focus()
    expect(row_title).to_be_focused()
    row_capture = _capture(
        scenario_id="UI-DB-001",
        state={"count": 1, "result": "pass"},
        keyboard={"result": "not_applicable"},
        focus={"name": "DATABASE-ROW", "state": "focused"},
        a11y={"name": "DATABASE-ROW", "role": "textbox"},
        semantic_visual={"region": "DATABASE-ROW", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"DATABASE-ROW": _rounded_box(properties)},
    )

    page.locator("#sidePeekCloseBtn").click()
    expect(side_peek).to_be_hidden()
    page.reload(wait_until="domcontentloaded")
    table = page.locator(".table-grid").first
    expect(table).to_be_visible(timeout=20_000)
    expect(table.locator('[data-db-field="status"]')).to_have_value("진행 중")
    expect(table.locator('[data-db-date-picker="date"]')).to_contain_text("2026년 8월 20일")
    memo_header = table.locator("[data-db-property]").filter(has_text="메모")
    memo_field = memo_header.get_attribute("data-db-property")
    assert memo_field
    expect(table.locator(f'[data-db-field="{memo_field}"]')).to_have_value("합성 메모")
    table.locator(".database-open-page").first.click()
    expect(side_peek).to_be_visible(timeout=20_000)
    properties = side_peek.locator(".side-peek-properties")
    expect(properties.locator(f'[data-db-field="{memo_field}"]')).to_have_value("합성 메모")
    expect(properties.locator('[data-db-field="status"]')).to_have_value("진행 중")
    properties.locator('[data-db-date-picker="date"]').click()
    expect(page.locator('.database-date-picker [data-date-start]')).to_have_value("2026. 8. 20.")

    after_publication = _canonical_publication_state(data_root)
    assert after_publication == before_publication
    return {
        "action_captures": {
            "UI-DB-001-A01": table_capture,
            "UI-DB-001-A02": row_capture,
        },
        "capture_sha256": {
            "UI-DB-001-A01": capture_sha256(table_capture),
            "UI-DB-001-A02": capture_sha256(row_capture),
        },
        "focus_sequence": ["DATABASE-GRID:not_focused", "DATABASE-ROW"],
        "properties_restored": True,
        "row_page_reopened": True,
        "save_count": len(save_requests),
        "second_brain_item_delta": after_publication["items"] - before_publication["items"],
        "second_brain_revision_delta": after_publication["revisions"] - before_publication["revisions"],
        "synthetic_only": True,
    }


def _run_typed_database_round_trip_journey(
    playwright: object,
    data_root: Path,
) -> dict[str, object]:
    with _isolated_server(data_root) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport={"height": 900, "width": 1440},
            )
            try:
                return _exercise_typed_database_round_trip(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                    data_root,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _exercise_database_view_state_round_trip(
    page: object,
    origin: str,
    user_id: str,
    password: str,
    data_root: Path,
) -> dict[str, object]:
    from playwright.sync_api import expect

    save_requests: list[str] = []
    page.on(
        "request",
        lambda request: save_requests.append(request.url)
        if request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", request.url)
        else None,
    )
    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    expect(page.locator("#homeNavBtn")).to_be_visible(timeout=20_000)
    expect(page.locator("#workspaceTitle")).to_contain_text("UI1 Owner", timeout=30_000)
    _wait_for_notion_ready(page)

    _create_page_from_sidebar(page)
    title = page.locator("#pageTitle")
    expect(title).to_be_visible(timeout=15_000)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        title.fill("UI-DB-002")
    before_publication = _canonical_publication_state(data_root)

    editor = page.locator(".block-editor[contenteditable='true']").first
    editor.focus()
    editor.press("/")
    menu = page.locator("#blockMenu")
    expect(menu).to_be_visible()
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        menu.locator('[data-insert-type="table"]').click()

    row_values = (
        ("UI-ROW-A", "진행 중"),
        ("UI-ROW-B", "완료"),
        ("UI-ROW-C", "진행 중"),
    )
    for row_index, (row_title, row_status) in enumerate(row_values):
        with page.expect_response(
            lambda response: response.request.method == "POST"
            and response.url.rstrip("/").endswith("/api/notion/pages"),
            timeout=20_000,
        ):
            page.locator("[data-database-add]").first.click()
        side_peek = page.locator("#sidePeek")
        expect(side_peek).to_be_visible(timeout=20_000)
        page.locator("#sidePeekCloseBtn").click()
        expect(side_peek).to_be_hidden()

        table = page.locator(".table-grid").first
        title_input = table.locator('[data-db-field="title"]').nth(row_index)
        status_input = table.locator('[data-db-field="status"]').nth(row_index)
        title_input.fill(row_title)
        title_input.press("Tab")
        with page.expect_response(
            lambda response: response.request.method == "POST"
            and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
            timeout=20_000,
        ):
            status_input.fill(row_status)
            status_input.press("Tab")

    save_requests.clear()
    page.locator("[data-db-filter]").first.click()
    quick_filter = page.locator("[data-db-view-popover]").first
    expect(quick_filter.locator("[data-db-view-filter-panel]")).to_be_visible()
    quick_filter.locator('[data-db-view-filter-choice="title"]').click()
    quick_filter.locator('[data-db-view-filter-value="0"]').fill("UI-ROW")
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        quick_filter.locator('[data-db-view-filter-value="0"]').press("Tab")

    page.locator("[data-db-sort]").first.click()
    quick_sort = page.locator("[data-db-view-popover]").first
    expect(quick_sort.locator("[data-db-view-sort-panel]")).to_be_visible()
    quick_sort.locator('[data-db-view-sort-choice="title"]').click()
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        quick_sort.locator('[data-db-view-sort-direction="0"]').select_option("desc")

    page.locator("[data-db-group]").first.click()
    expect(page.locator("#modalTitle")).to_have_text("데이터베이스 그룹")
    page.locator("#dbGroupProperty").select_option("status")
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        page.get_by_role("button", name="적용", exact=True).click()

    table = page.locator(".table-grid").first
    expect(table).not_to_be_focused()
    expect(page.locator(".database-block [role='grid']")).to_have_count(1)
    expect(page.locator(".database-group-label")).to_have_count(2)
    assert table.locator('[data-db-field="title"]').evaluate_all(
        "elements => elements.map(element => element.value)",
    ) == [
        "UI-ROW-C",
        "UI-ROW-A",
        "UI-ROW-B",
    ]
    configured_capture = _capture(
        scenario_id="UI-DB-002",
        state={"count": 3, "result": "pass"},
        keyboard={"result": "not_applicable"},
        focus={"name": "DATABASE-GRID", "state": "not_focused"},
        a11y={"name": "DATABASE-TABLE", "role": "GRID"},
        semantic_visual={"region": "DATABASE-GROUP", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"DATABASE-TABLE": _rounded_box(table)},
    )

    page.reload(wait_until="domcontentloaded")
    table = page.locator(".table-grid").first
    expect(table).to_be_visible(timeout=20_000)
    expect(page.locator("[data-db-filter]").first).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator("[data-db-sort]").first).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator("[data-db-group]").first).to_have_class(re.compile(r"\bactive\b"))
    expect(page.locator(".database-group-label")).to_have_count(2)
    assert table.locator('[data-db-field="title"]').evaluate_all(
        "elements => elements.map(element => element.value)",
    ) == [
        "UI-ROW-C",
        "UI-ROW-A",
        "UI-ROW-B",
    ]
    assert table.locator('[data-db-field="status"]').evaluate_all(
        "elements => elements.map(element => element.value)",
    ) == [
        "진행 중",
        "진행 중",
        "완료",
    ]
    expect(table).not_to_be_focused()
    expect(page.locator(".database-block [role='grid']")).to_have_count(1)
    restored_capture = _capture(
        scenario_id="UI-DB-002",
        state={"count": 3, "result": "pass"},
        keyboard={"result": "not_applicable"},
        focus={"name": "DATABASE-GRID", "state": "not_focused"},
        a11y={"name": "DATABASE-TABLE", "role": "GRID"},
        semantic_visual={"region": "DATABASE-GROUP", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"DATABASE-TABLE": _rounded_box(table)},
    )

    after_publication = _canonical_publication_state(data_root)
    assert after_publication == before_publication
    return {
        "action_captures": {
            "UI-DB-002-A01": configured_capture,
            "UI-DB-002-A02": restored_capture,
        },
        "capture_sha256": {
            "UI-DB-002-A01": capture_sha256(configured_capture),
            "UI-DB-002-A02": capture_sha256(restored_capture),
        },
        "filter_sort_group_restored": True,
        "source_rows_unchanged": True,
        "save_count": len(save_requests),
        "second_brain_item_delta": after_publication["items"] - before_publication["items"],
        "second_brain_revision_delta": after_publication["revisions"] - before_publication["revisions"],
        "synthetic_only": True,
    }


def _run_database_view_state_round_trip_journey(
    playwright: object,
    data_root: Path,
) -> dict[str, object]:
    with _isolated_server(data_root) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport={"height": 900, "width": 1440},
            )
            try:
                return _exercise_database_view_state_round_trip(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                    data_root,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _exercise_database_multi_view_round_trip(
    page: object,
    origin: str,
    user_id: str,
    password: str,
    data_root: Path,
) -> dict[str, object]:
    from playwright.sync_api import expect

    save_requests: list[str] = []
    row_mutation_requests: list[str] = []

    def record_request(request: object) -> None:
        url = request.url.rstrip("/")
        method = request.method
        if method == "POST" and re.search(r"/api/notion/pages/[^/]+/save$", url):
            save_requests.append(url)
        if (method == "POST" and url.endswith("/api/notion/pages")) or (
            method == "DELETE" and re.search(r"/api/notion/pages/[^/]+$", url)
        ):
            row_mutation_requests.append(f"{method}:{url}")

    page.on("request", record_request)
    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    expect(page.locator("#homeNavBtn")).to_be_visible(timeout=20_000)
    expect(page.locator("#workspaceTitle")).to_contain_text("UI1 Owner", timeout=30_000)
    _wait_for_notion_ready(page)

    _create_page_from_sidebar(page)
    title = page.locator("#pageTitle")
    expect(title).to_be_visible(timeout=15_000)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        title.fill("UI-DB-003")

    before_publication = _canonical_publication_state(data_root)
    editor = page.locator(".block-editor[contenteditable='true']").first
    editor.focus()
    editor.press("/")
    menu = page.locator("#blockMenu")
    expect(menu).to_be_visible()
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        menu.locator('[data-insert-type="table"]').click()

    month = page.evaluate("() => new Date().toISOString().slice(0, 7)")
    row_values = (
        ("UI-VIEW-A", "진행 중", f"{month}-10"),
        ("UI-VIEW-B", "완료", f"{month}-11"),
        ("UI-VIEW-C", "진행 중", f"{month}-12"),
    )
    for row_index, (row_title, row_status, row_date) in enumerate(row_values):
        with page.expect_response(
            lambda response: response.request.method == "POST"
            and response.url.rstrip("/").endswith("/api/notion/pages"),
            timeout=20_000,
        ):
            page.locator("[data-database-add]").first.click()
        side_peek = page.locator("#sidePeek")
        expect(side_peek).to_be_visible(timeout=20_000)
        page.locator("#sidePeekCloseBtn").click()
        expect(side_peek).to_be_hidden()

        table = page.locator(".table-grid").first
        table.locator('[data-db-field="title"]').nth(row_index).fill(row_title)
        table.locator('[data-db-field="title"]').nth(row_index).press("Tab")
        table.locator('[data-db-field="status"]').nth(row_index).fill(row_status)
        table.locator('[data-db-field="status"]').nth(row_index).press("Tab")
        with page.expect_response(
            lambda response: response.request.method == "POST"
            and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
            timeout=20_000,
        ):
            table.locator('[data-db-date-picker="date"]').nth(row_index).click()
            picker = page.locator('.database-date-picker')
            expect(picker).to_be_visible()
            picker.locator('[data-date-start]').fill(row_date)
            picker.locator('[data-date-start]').press("Tab")

    expected_titles = [item[0] for item in row_values]
    row_mutation_requests.clear()
    save_requests.clear()
    view_boxes: dict[str, dict[str, int]] = {}

    def visible_titles(view_type: str) -> list[str]:
        if view_type == "table":
            return page.locator('.table-grid [data-db-field="title"]').evaluate_all(
                "elements => elements.map(element => ({index: Number(element.dataset.dbRow), title: element.value}))"
                ".sort((left, right) => left.index - right.index).map(entry => entry.title)",
            )
        selector = {
            "board": ".board-card",
            "list": ".database-list [data-table-row]",
            "gallery": ".gallery-card",
            "calendar": ".calendar-event",
        }[view_type]
        title_selector = {
            "board": "strong",
            "list": "span:last-of-type",
            "gallery": "strong",
            "calendar": "",
        }[view_type]
        return page.locator(selector).evaluate_all(
            "(elements, titleSelector) => elements.map(element => ({"
            "index: Number(element.dataset.tableRow ?? element.dataset.calendarRow), "
            "title: (titleSelector ? element.querySelector(titleSelector) : element)?.textContent?.trim() || ''"
            "})).sort((left, right) => left.index - right.index).map(entry => entry.title)",
            title_selector,
        )

    view_specs = (
        ("board", "보드 보기", ".board-columns", "DATABASE-BOARD"),
        ("list", "목록 보기", ".database-list", "DATABASE-LIST"),
        ("gallery", "갤러리 보기", ".gallery-grid", "DATABASE-GALLERY"),
        ("calendar", "캘린더 보기", ".calendar-grid", "DATABASE-CALENDAR"),
    )
    for view_type, view_name, view_selector, geometry_alias in view_specs:
        page.locator("[data-db-add-view]").first.click()
        expect(page.locator("#modalTitle")).to_have_text("새 보기")
        page.locator("#dbViewName").fill(view_name)
        page.locator("#dbViewType").select_option(view_type)
        with page.expect_response(
            lambda response: response.request.method == "POST"
            and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
            timeout=20_000,
        ):
            page.get_by_role("button", name="저장", exact=True).click()
        view_region = page.locator(view_selector).first
        expect(view_region).to_be_visible(timeout=20_000)
        assert visible_titles(view_type) == expected_titles
        view_boxes[geometry_alias] = _rounded_box(view_region)

    view_sequence = (
        ("표", "table", ".table-grid"),
        ("보드 보기", "board", ".board-columns"),
        ("목록 보기", "list", ".database-list"),
        ("갤러리 보기", "gallery", ".gallery-grid"),
        ("캘린더 보기", "calendar", ".calendar-grid"),
    )
    for view_name, view_type, view_selector in view_sequence:
        tab = page.locator("[data-db-view-id]").filter(has_text=view_name).first
        with page.expect_response(
            lambda response: response.request.method == "POST"
            and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
            timeout=20_000,
        ):
            tab.click()
        expect(page.locator(view_selector).first).to_be_visible(timeout=20_000)
        expect(page.locator("[data-db-view-id].active")).to_have_text(view_name)
        expect(page.locator("[data-db-view-id].active")).to_be_focused()
        assert visible_titles(view_type) == expected_titles

    page.reload(wait_until="domcontentloaded")
    expect(page.locator(".calendar-grid").first).to_be_visible(timeout=20_000)
    expect(page.locator("[data-db-view-id].active")).to_have_text("캘린더 보기")
    assert visible_titles("calendar") == expected_titles

    table_tab = page.locator("[data-db-view-id]").filter(has_text="표").first
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        table_tab.click()
    table = page.locator(".table-grid").first
    expect(table).to_be_visible(timeout=20_000)
    expect(page.locator("[data-db-view-id].active")).to_be_focused()
    assert visible_titles("table") == expected_titles
    assert table.locator('[data-db-field="status"]').evaluate_all(
        "elements => elements.map(element => element.value)",
    ) == [item[1] for item in row_values]
    expect(table.locator('[data-db-date-picker="date"]')).to_have_count(3)
    assert page.evaluate("""() => State.pageData.blocks.find(block => block.type === 'table')
      .props.rows.map(row => typeof row.date === 'string' ? row.date : row.date?.start || '')""") == [
        item[2] for item in row_values
    ]

    switcher = page.locator(".database-view-tabs").first
    view_boxes["DATABASE-VIEW-SWITCHER"] = _rounded_box(switcher)
    capture = _capture(
        scenario_id="UI-DB-003",
        state={"count": 5, "result": "pass"},
        keyboard={"result": "not_applicable"},
        focus={"sequence": ["TABLE", "BOARD", "LIST", "GALLERY", "CALENDAR", "TABLE"]},
        a11y={"name": "DATABASE-VIEW-SWITCHER", "role": "tablist"},
        semantic_visual={"region": "DATABASE-VIEW-SWITCHER", "state": "five-views-rendered"},
        error_recovery={"result": "pass"},
        geometry=view_boxes,
    )
    after_publication = _canonical_publication_state(data_root)
    assert after_publication == before_publication
    assert row_mutation_requests == []
    return {
        "action_captures": {"UI-DB-003-A01": capture},
        "capture_sha256": {"UI-DB-003-A01": capture_sha256(capture)},
        "canonical_rows_preserved": True,
        "focus_sequence": ["TABLE", "BOARD", "LIST", "GALLERY", "CALENDAR", "TABLE"],
        "row_mutation_count": len(row_mutation_requests),
        "second_brain_item_delta": after_publication["items"] - before_publication["items"],
        "second_brain_revision_delta": after_publication["revisions"] - before_publication["revisions"],
        "synthetic_only": True,
        "view_count": 5,
        "view_save_count": len(save_requests),
        "view_state_restored": True,
    }


def _run_database_multi_view_round_trip_journey(
    playwright: object,
    data_root: Path,
) -> dict[str, object]:
    with _isolated_server(data_root) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport={"height": 900, "width": 1440},
            )
            try:
                return _exercise_database_multi_view_round_trip(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                    data_root,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _exercise_in_page_find_and_backlink_navigation(
    page: object,
    origin: str,
    user_id: str,
    password: str,
    data_root: Path,
) -> dict[str, object]:
    from playwright.sync_api import expect

    page_mutations: list[str] = []

    def record_request(request: object) -> None:
        url = request.url.rstrip("/")
        if request.method == "POST" and (
            url.endswith("/api/notion/pages")
            or re.search(r"/api/notion/pages/[^/]+/save$", url)
        ):
            page_mutations.append(f"POST:{url}")
        if request.method == "DELETE" and re.search(r"/api/notion/pages/[^/]+$", url):
            page_mutations.append(f"DELETE:{url}")

    page.on("request", record_request)
    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    workspace_title = page.locator("#workspaceTitle")
    expect(workspace_title).to_have_text(re.compile(r"^Synthetic Workspace [AB]$"), timeout=30_000)
    if workspace_title.inner_text() != "Synthetic Workspace A":
        page.locator("#workspaceName").click(position={"x": 80, "y": 20})
        target = page.locator("[data-navigation-workspace]").filter(has_text="Synthetic Workspace A")
        expect(target).to_have_count(1)
        target.click()
        expect(workspace_title).to_have_text("Synthetic Workspace A")

    tree = page.locator("#ownPages")
    root_row = tree.locator(".page-row").filter(has_text="Synthetic Page A")
    expect(root_row).to_have_count(1)
    root_row.click()
    expect(page.locator("#pageTitle")).to_have_value("Synthetic Page A", timeout=20_000)
    before_publication = _canonical_publication_state(data_root)

    editor = page.locator(".block-editor[contenteditable='true']").first
    editor.focus()
    editor.press("End")
    editor.type(" UI-FIND-MATCH ")
    editor.type("@Synthetic")
    mention_menu = page.locator("#mentionMenu")
    expect(mention_menu).to_be_visible()
    expect(mention_menu).not_to_contain_text("Synthetic Page B")
    child_option = mention_menu.locator("[data-mention-page]").filter(has_text="Synthetic Child A")
    expect(child_option).to_have_count(1)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        child_option.click()
    page_link = editor.locator("a.page-mention").filter(has_text="Synthetic Child A")
    expect(page_link).to_have_count(1)

    page_mutations.clear()
    editor.focus()
    page.keyboard.press("Control+f")
    find_bar = page.locator("#pageFindBar")
    find_input = page.locator("#pageFindInput")
    expect(find_bar).to_be_visible()
    expect(find_input).to_be_focused()
    find_input.fill("UI-FIND-MATCH")
    expect(page.locator("#pageFindCount")).to_have_text("1/1")
    expect(page.locator(".page-find-hit")).to_have_count(1)
    expect(find_input).to_be_focused()
    find_capture = _capture(
        scenario_id="UI-SEARCH-002",
        state={"count": 1, "result": "pass"},
        keyboard={"result": "pass"},
        focus={"sequence": ["PAGE-FIND-INPUT"]},
        a11y={"name": "PAGE-FIND-INPUT", "role": "searchbox"},
        semantic_visual={"region": "PAGE-FIND", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"PAGE-FIND": _rounded_box(find_bar)},
    )
    find_input.press("Escape")
    expect(find_bar).to_be_hidden()
    expect(editor).to_be_focused()

    page_link.click()
    expect(page.locator("#pageTitle")).to_have_value("Synthetic Child A", timeout=20_000)
    backlinks_button = page.locator("#pageBacklinksBtn")
    expect(backlinks_button).to_be_visible(timeout=20_000)
    expect(backlinks_button).to_contain_text("1개의 백링크")
    backlinks_button.click()
    expect(page.locator("#modalTitle")).to_have_text("이 페이지의 백링크")
    backlink = page.locator("[data-open-backlink]").filter(has_text="Synthetic Page A")
    expect(backlink).to_have_count(1)
    expect(page.locator(".backlink-list")).not_to_contain_text("Synthetic Page B")
    backlink.click()
    expect(page.locator("#pageTitle")).to_have_value("Synthetic Page A", timeout=20_000)
    page_canvas = page.locator("#pageView")
    expect(page_canvas).to_be_focused()
    backlink_capture = _capture(
        scenario_id="UI-SEARCH-002",
        state={"count": 1, "result": "pass"},
        keyboard={"result": "pass"},
        focus={"sequence": ["PAGE-CANVAS"]},
        a11y={"name": "PAGE-CANVAS", "role": "region"},
        semantic_visual={"region": "SYNTHETIC-BACKLINK", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"PAGE-CANVAS": _rounded_box(page_canvas)},
    )

    after_publication = _canonical_publication_state(data_root)
    assert after_publication == before_publication
    assert page_mutations == []
    return {
        "action_captures": {
            "UI-SEARCH-002-A01": find_capture,
            "UI-SEARCH-002-A02": backlink_capture,
        },
        "capture_sha256": {
            "UI-SEARCH-002-A01": capture_sha256(find_capture),
            "UI-SEARCH-002-A02": capture_sha256(backlink_capture),
        },
        "focus_sequence": ["PAGE-FIND-INPUT", "EDITOR-BLOCK", "PAGE-CANVAS"],
        "page_mutation_count_after_setup": len(page_mutations),
        "second_brain_item_delta": after_publication["items"] - before_publication["items"],
        "second_brain_revision_delta": after_publication["revisions"] - before_publication["revisions"],
        "synthetic_only": True,
        "workspace_scope_leak_count": 0,
    }


def _run_in_page_find_and_backlink_navigation_journey(
    playwright: object,
    data_root: Path,
) -> dict[str, object]:
    with _isolated_server(
        data_root,
        seed_navigation_tree=True,
    ) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport={"height": 900, "width": 1440},
            )
            try:
                return _exercise_in_page_find_and_backlink_navigation(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                    data_root,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _exercise_comment_thread_round_trip(
    page: object,
    origin: str,
    user_id: str,
    password: str,
    data_root: Path,
) -> dict[str, object]:
    from playwright.sync_api import expect

    comment_mutations: list[str] = []
    external_requests: list[str] = []

    def record_request(request: object) -> None:
        if not request.url.startswith(origin):
            external_requests.append(request.url)
        if re.search(r"/api/notion/pages/[^/]+/comments(?:/[^/]+)?$", request.url.rstrip("/")):
            if request.method in {"POST", "PATCH", "DELETE"}:
                comment_mutations.append(request.method)

    page.on("request", record_request)
    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    expect(page.locator("#homeNavBtn")).to_be_visible(timeout=20_000)
    expect(page.locator("#workspaceTitle")).to_contain_text("UI1 Owner", timeout=30_000)
    _wait_for_notion_ready(page)

    _create_page_from_sidebar(page)
    title = page.locator("#pageTitle")
    expect(title).to_be_visible(timeout=15_000)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        title.fill("UI-COLLAB-001")
    before_publication = _canonical_publication_state(data_root)

    page.locator("#addCommentBtn").click()
    discussion = page.locator("#pageInlineDiscussion")
    comments_panel = page.locator("#commentsPanel")
    comment_input = page.locator("#commentInput")
    expect(discussion).to_be_visible()
    expect(comments_panel).to_be_visible()
    expect(comment_input).to_be_enabled(timeout=20_000)
    comment_input.fill("UI 합성 댓글")
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/comments$", response.url),
        timeout=20_000,
    ):
        page.locator("#commentSubmitBtn").click()

    thread = page.locator(".comment-thread").first
    expect(thread).to_have_count(1)
    expect(thread).to_contain_text("UI 합성 댓글")
    expect(comment_input).to_be_focused()
    posted_capture = _capture(
        scenario_id="UI-COLLAB-001",
        state={"count": 1, "result": "pass"},
        keyboard={"result": "not_applicable"},
        focus={"sequence": ["COMMENT-INPUT"]},
        a11y={"name": "COMMENT-THREAD", "role": "textbox"},
        semantic_visual={"region": "COMMENT-PANEL", "state": "posted"},
        error_recovery={"result": "pass"},
        geometry={"COMMENT-PANEL": _rounded_box(comments_panel)},
    )

    resolve_button = thread.locator("[data-comment-resolve]")
    expect(resolve_button).to_have_text("해결")
    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and re.search(r"/api/notion/pages/[^/]+/comments/[^/]+$", response.url),
        timeout=20_000,
    ):
        resolve_button.click()
    expect(page.locator(".comment-thread")).to_have_count(0)
    expect(page.locator("body")).to_be_focused()
    resolved_capture = _capture(
        scenario_id="UI-COLLAB-001",
        state={"count": 1, "result": "pass"},
        keyboard={"result": "not_applicable"},
        focus={"sequence": ["BODY"]},
        a11y={"name": "COMMENT-THREAD", "role": "NONE"},
        semantic_visual={"region": "COMMENT-PANEL", "state": "resolved_and_hidden"},
        error_recovery={"result": "pass"},
        geometry={},
    )

    page.locator("#commentFilterSelect").select_option("all")
    thread = page.locator(".comment-thread").first
    expect(thread.locator(".comment-item").first).to_have_class(re.compile(r"\bresolved\b"))
    expect(thread.locator("[data-comment-resolve]")).to_have_text("다시 열기")
    with page.expect_response(
        lambda response: response.request.method == "PATCH"
        and re.search(r"/api/notion/pages/[^/]+/comments/[^/]+$", response.url),
        timeout=20_000,
    ):
        thread.locator("[data-comment-resolve]").click()
    thread = page.locator(".comment-thread").first
    expect(thread.locator(".comment-item").first).not_to_have_class(re.compile(r"\bresolved\b"))
    expect(thread.locator("[data-comment-resolve]")).to_have_text("해결")
    expect(thread.locator("[data-comment-resolve]")).to_be_focused()

    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#pageTitle")).to_have_value("UI-COLLAB-001", timeout=20_000)
    page.locator("#addCommentBtn").click()
    expect(page.locator(".comment-thread")).to_have_count(1, timeout=20_000)
    expect(page.locator(".comment-thread")).to_contain_text("UI 합성 댓글")
    expect(page.locator(".comment-thread .comment-item").first).not_to_have_class(re.compile(r"\bresolved\b"))

    after_publication = _canonical_publication_state(data_root)
    assert after_publication == before_publication
    assert external_requests == []
    return {
        "action_captures": {
            "UI-COLLAB-001-A01": posted_capture,
            "UI-COLLAB-001-A02": resolved_capture,
        },
        "capture_sha256": {
            "UI-COLLAB-001-A01": capture_sha256(posted_capture),
            "UI-COLLAB-001-A02": capture_sha256(resolved_capture),
        },
        "comment_mutation_sequence": comment_mutations,
        "comment_round_trip": True,
        "external_request_count": len(external_requests),
        "focus_sequence": ["COMMENT-INPUT", "BODY", "RESOLVE-CONTROL"],
        "second_brain_item_delta": after_publication["items"] - before_publication["items"],
        "second_brain_revision_delta": after_publication["revisions"] - before_publication["revisions"],
        "synthetic_only": True,
    }


def _run_comment_thread_round_trip_journey(
    playwright: object,
    data_root: Path,
) -> dict[str, object]:
    with _isolated_server(data_root) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport={"height": 900, "width": 1440},
            )
            try:
                return _exercise_comment_thread_round_trip(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                    data_root,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _exercise_share_dialog_read_only(
    page: object,
    origin: str,
    user_id: str,
    password: str,
    data_root: Path,
) -> dict[str, object]:
    from playwright.sync_api import expect

    access_mutations: list[str] = []
    external_requests: list[str] = []

    def record_request(request: object) -> None:
        if not request.url.startswith(origin):
            external_requests.append(request.url)
        if re.search(
            r"/api/notion/pages/[^/]+/(?:shares(?:/[^/]+)?|public_link)$",
            request.url.rstrip("/"),
        ) and request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            access_mutations.append(request.method)

    page.on("request", record_request)
    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    expect(page.locator("#homeNavBtn")).to_be_visible(timeout=20_000)
    expect(page.locator("#workspaceTitle")).to_contain_text("UI1 Owner", timeout=30_000)
    _wait_for_notion_ready(page)

    page.locator("#sidebarNewBtn").click()
    page.locator('#newPageMenu [data-new-page-type="page"]').click()
    title = page.locator("#pageTitle")
    expect(title).to_be_visible(timeout=15_000)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        title.fill("UI-SHARE-001")
    before_publication = _canonical_publication_state(data_root)

    trigger = page.locator("#shareDrawerBtn")
    inspector = page.locator("#inspector")
    trigger.focus()
    trigger.click()
    expect(inspector).to_be_visible()
    expect(inspector).to_have_attribute("role", "dialog")
    expect(inspector).to_have_attribute("aria-labelledby", "drawerTitle")
    expect(page.locator("#drawerTitle")).to_have_text("공유")
    expect(page.locator("#sharePanel")).to_be_visible()
    expect(page.locator("#shareList")).to_contain_text("공유 없음", timeout=20_000)
    expect(page.locator("#publicShareStatus")).to_have_text(
        "아직 웹에 게시되지 않았습니다.",
        timeout=20_000,
    )
    share_invite = page.locator("#shareUser")
    expect(share_invite).to_be_focused()
    visible_focus = _focus_alias(page)
    visible_capture = _capture(
        scenario_id="UI-SHARE-001",
        state={"count": 1, "result": "pass"},
        keyboard={"result": "not_applicable"},
        focus={"sequence": [visible_focus]},
        a11y={"name": "SHARE-DIALOG", "role": "dialog"},
        semantic_visual={"region": "SHARE-DIALOG", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={"SHARE-DIALOG": _rounded_box(inspector)},
    )

    page.keyboard.press("Escape")
    expect(inspector).to_be_hidden()
    expect(trigger).to_be_focused()
    share_access_tooltip = page.locator("#shareAccessTooltip")
    expect(share_access_tooltip).to_be_visible()
    expect(share_access_tooltip).to_have_attribute("role", "dialog")
    hidden_capture = _capture(
        scenario_id="UI-SHARE-001",
        state={"count": 0, "result": "pass"},
        keyboard={"key": "ESCAPE", "result": "pass"},
        focus={"sequence": ["SHARE-TRIGGER"]},
        a11y={"name": "SHARE-TRIGGER", "role": "button"},
        semantic_visual={"region": "SHARE-DIALOG", "state": "hidden_with_access_tooltip"},
        error_recovery={"result": "pass"},
        geometry={"SHARE-TRIGGER": _rounded_box(trigger)},
    )

    after_publication = _canonical_publication_state(data_root)
    assert after_publication == before_publication
    assert access_mutations == []
    assert external_requests == []
    return {
        "access_mutation_count": len(access_mutations),
        "action_captures": {
            "UI-SHARE-001-A01": visible_capture,
            "UI-SHARE-001-A02": hidden_capture,
        },
        "capture_sha256": {
            "UI-SHARE-001-A01": capture_sha256(visible_capture),
            "UI-SHARE-001-A02": capture_sha256(hidden_capture),
        },
        "external_request_count": len(external_requests),
        "focus_sequence": [visible_focus, "SHARE-TRIGGER"],
        "second_brain_item_delta": after_publication["items"] - before_publication["items"],
        "second_brain_revision_delta": after_publication["revisions"] - before_publication["revisions"],
        "synthetic_only": True,
    }


def _run_share_dialog_read_only_journey(
    playwright: object,
    data_root: Path,
) -> dict[str, object]:
    with _isolated_server(data_root) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport={"height": 900, "width": 1440},
            )
            try:
                return _exercise_share_dialog_read_only(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                    data_root,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _seed_synthetic_session(data_root: Path) -> None:
    session_root = data_root / "user_data" / "ui1-owner" / "sessions"
    session_root.mkdir(parents=True, exist_ok=True)
    (session_root / "ui-sb-session.json").write_text(
        json.dumps(
            {
                "first_message": "UI synthetic session",
                "message_count": 0,
                "messages": [],
                "session_id": "ui-sb-session",
                "timestamp": 1,
                "title": "UI 합성 세션",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _exercise_session_attachment_round_trip(
    page: object,
    origin: str,
    user_id: str,
    password: str,
    data_root: Path,
) -> dict[str, object]:
    from playwright.sync_api import expect

    _seed_synthetic_session(data_root)
    attachment_mutations: list[str] = []
    external_requests: list[str] = []
    session_context_reads = 0

    def record_request(request: object) -> None:
        nonlocal session_context_reads
        if not request.url.startswith(origin):
            external_requests.append(request.url)
        if request.method == "POST" and re.search(
            r"/api/notion/pages/[^/]+/attach$",
            request.url.rstrip("/"),
        ):
            attachment_mutations.append("POST")
        elif request.method == "DELETE" and re.search(
            r"/api/notion/session_pages/[^/]+(?:\?.*)?$",
            request.url,
        ):
            attachment_mutations.append("DELETE")
        elif request.method == "GET" and "/api/notion/session_pages?" in request.url:
            session_context_reads += 1

    page.on("request", record_request)
    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    expect(page.locator("#homeNavBtn")).to_be_visible(timeout=20_000)
    expect(page.locator("#workspaceTitle")).to_contain_text("UI1 Owner", timeout=30_000)
    _wait_for_notion_ready(page)

    _create_page_from_sidebar(page)
    title = page.locator("#pageTitle")
    expect(title).to_be_visible(timeout=15_000)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        title.fill("UI-SB-003")
    before_publication = _canonical_publication_state(data_root)

    page.locator("#pageMoreBtn").click()
    page.locator('#pageMenu [data-page-action="context"]').click()
    context_panel = page.locator("#contextPanel")
    session_select = page.locator("#sessionSelect")
    attached_pages = page.locator("#attachedPages")
    expect(context_panel).to_be_visible()
    expect(context_panel).to_have_attribute("role", "region")
    expect(context_panel).to_have_attribute("aria-label", "Second Brain 세션 컨텍스트")
    expect(context_panel).to_have_attribute("tabindex", "-1")
    expect(session_select.locator("option")).to_contain_text("UI 합성 세션")
    session_select.select_option("ui-sb-session")
    expect(attached_pages).to_contain_text("연결된 페이지 없음", timeout=20_000)

    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/attach$", response.url),
        timeout=20_000,
    ):
        page.locator("#attachBtn").click()
    expect(attached_pages).to_contain_text("UI-SB-003", timeout=20_000)
    expect(context_panel).to_be_focused()
    attached_capture = _capture(
        scenario_id="UI-SB-003",
        state={"count": 1, "result": "pass"},
        keyboard={"result": "pass"},
        focus={"sequence": ["SESSION-CONTEXT"]},
        a11y={"name": "SESSION-CONTEXT", "role": "region"},
        semantic_visual={"region": "SESSION-CONTEXT", "state": "attached"},
        error_recovery={"result": "pass"},
        geometry={"SESSION-CONTEXT": _rounded_box(context_panel)},
    )

    with page.expect_response(
        lambda response: response.request.method == "DELETE"
        and re.search(r"/api/notion/session_pages/[^/]+(?:\?.*)?$", response.url),
        timeout=20_000,
    ):
        attached_pages.locator("button.danger").click()
    expect(attached_pages).to_contain_text("연결된 페이지 없음", timeout=20_000)
    expect(context_panel).to_be_focused()
    detached_capture = _capture(
        scenario_id="UI-SB-003",
        state={"count": 0, "result": "pass"},
        keyboard={"result": "pass"},
        focus={"sequence": ["SESSION-CONTEXT"]},
        a11y={"name": "SESSION-CONTEXT", "role": "region"},
        semantic_visual={"region": "SESSION-CONTEXT", "state": "detached"},
        error_recovery={"result": "pass"},
        geometry={"SESSION-CONTEXT": _rounded_box(context_panel)},
    )

    after_publication = _canonical_publication_state(data_root)
    assert after_publication == before_publication
    assert attachment_mutations == ["POST", "DELETE"]
    assert external_requests == []
    assert session_context_reads >= 3
    return {
        "attachment_mutation_sequence": attachment_mutations,
        "capture_sha256": {
            "UI-SB-003-A01": capture_sha256(attached_capture),
            "UI-SB-003-A02": capture_sha256(detached_capture),
        },
        "current_access_revalidated": session_context_reads >= 3,
        "external_request_count": len(external_requests),
        "focus_sequence": ["SESSION-CONTEXT", "SESSION-CONTEXT"],
        "second_brain_item_delta": after_publication["items"] - before_publication["items"],
        "second_brain_revision_delta": after_publication["revisions"] - before_publication["revisions"],
        "synthetic_only": True,
    }


def _run_session_attachment_round_trip_journey(
    playwright: object,
    data_root: Path,
) -> dict[str, object]:
    with _isolated_server(data_root) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport={"height": 900, "width": 1440},
            )
            try:
                return _exercise_session_attachment_round_trip(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                    data_root,
                )
            finally:
                context.close()
        finally:
            browser.close()


_RESPONSIVE_VIEWPORTS = (
    {"height": 900, "width": 1440},
    {"height": 800, "width": 1280},
    {"height": 800, "width": 980},
    {"height": 800, "width": 720},
)


def _viewport_overflow(page: object) -> dict[str, int]:
    return page.evaluate(
        """() => ({
          body: Math.max(0, document.body.scrollWidth - window.innerWidth),
          document: Math.max(
            0,
            document.documentElement.scrollWidth - window.innerWidth,
          ),
        })"""
    )


def _assert_box_inside_viewport(
    page: object,
    locator: object,
    *,
    include_vertical: bool,
) -> dict[str, int]:
    box = _rounded_box(locator)
    viewport = page.viewport_size
    assert viewport is not None
    assert box["x"] >= -1
    assert box["x"] + box["width"] <= viewport["width"] + 1
    if include_vertical:
        assert box["y"] >= -1
        assert box["y"] + box["height"] <= viewport["height"] + 1
    return box


def _exercise_responsive_shell_editor_and_overlays(
    page: object,
    origin: str,
    user_id: str,
    password: str,
    data_root: Path,
) -> dict[str, object]:
    from playwright.sync_api import expect

    external_requests: list[str] = []

    def record_request(request: object) -> None:
        if not request.url.startswith(origin):
            external_requests.append(request.url)

    page.on("request", record_request)
    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    expect(page.locator("#homeNavBtn")).to_be_visible(timeout=20_000)
    expect(page.locator("#workspaceTitle")).to_contain_text("UI1 Owner", timeout=30_000)
    _wait_for_notion_ready(page)

    page.locator("#sidebarNewBtn").click()
    page.locator('#newPageMenu [data-new-page-type="page"]').click()
    title = page.locator("#pageTitle")
    expect(title).to_be_visible(timeout=15_000)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        title.fill("UI-RESP-001")
    editor = page.locator(".block-editor[contenteditable='true']").first
    editor.fill("UI responsive synthetic block")
    expect(page.locator("#status")).to_have_text("저장됨", timeout=20_000)
    before_publication = _canonical_publication_state(data_root)

    viewport_reports: dict[str, dict[str, object]] = {}
    capture_hashes: dict[str, str] = {}
    for viewport in _RESPONSIVE_VIEWPORTS:
        page.set_viewport_size(viewport)
        key = f"{viewport['width']}x{viewport['height']}"
        page.evaluate("window.scrollTo(0, 0)")

        sidebar = page.locator(".sidebar")
        topbar = page.locator(".topbar")
        page_view = page.locator("#pageView")
        search_trigger = page.locator("#sidebarSearchBtn")
        share_trigger = page.locator("#shareDrawerBtn")
        sidebar_was_hidden = not sidebar.is_visible()
        if sidebar_was_hidden:
            restore_sidebar = page.locator("#sidebarRestoreBtn")
            expect(restore_sidebar).to_be_visible()
            restore_sidebar.click()
        expect(sidebar).to_be_visible()
        expect(search_trigger).to_be_visible()
        expect(page_view).to_be_visible()
        expect(title).to_be_visible()

        sidebar_box = _assert_box_inside_viewport(
            page,
            sidebar,
            include_vertical=False,
        )
        title.scroll_into_view_if_needed()
        page_view_box = _assert_box_inside_viewport(
            page,
            page_view,
            include_vertical=False,
        )
        assert _viewport_overflow(page) == {"body": 0, "document": 0}

        search_trigger.scroll_into_view_if_needed()
        search_trigger.focus()
        search_trigger.click()
        search_overlay = page.locator("#searchOverlay")
        search_dialog = page.locator(".search-dialog")
        expect(search_overlay).to_be_visible()
        expect(page.locator("#globalSearchInput")).to_be_focused()
        _assert_box_inside_viewport(page, search_dialog, include_vertical=True)
        page.keyboard.press("Escape")
        expect(search_overlay).to_be_hidden()
        expect(search_trigger).to_be_focused()
        if sidebar_was_hidden:
            page.locator("#collapseBtn").click()
            expect(sidebar).to_be_hidden()
            expect(restore_sidebar).to_be_visible()

        share_trigger.scroll_into_view_if_needed()
        share_trigger.focus()
        share_trigger.click()
        inspector = page.locator("#inspector")
        drawer_close = page.locator("#drawerCloseBtn")
        share_user = page.locator("#shareUser")
        expect(inspector).to_be_visible()
        expect(inspector).to_have_attribute(
            "data-share-surface",
            "page-popover",
            timeout=20_000,
        )
        expect(share_user).to_be_focused()
        expect(drawer_close).to_be_visible()
        inspector_box = _assert_box_inside_viewport(
            page,
            inspector,
            include_vertical=True,
        )
        _assert_box_inside_viewport(page, drawer_close, include_vertical=True)
        topbar_box = _assert_box_inside_viewport(
            page,
            topbar,
            include_vertical=False,
        )
        assert _viewport_overflow(page) == {"body": 0, "document": 0}
        page.keyboard.press("Escape")
        expect(inspector).to_be_hidden()
        expect(share_trigger).to_be_focused()

        responsive_capture = _capture(
            scenario_id="UI-RESP-001",
            state={"count": 4, "result": "pass"},
            keyboard={"key": "ESCAPE", "result": "pass"},
            focus={
                "sequence": [
                    "PAGE-CANVAS",
                    "SEARCH-TRIGGER",
                    "SHARE-DIALOG",
                    "SHARE-TRIGGER",
                ]
            },
            a11y={"name": "SHARE-DIALOG", "role": "dialog"},
            semantic_visual={"region": "PAGE-ROOT", "state": "visible"},
            error_recovery={"result": "pass"},
            geometry={
                "PAGE-CANVAS": page_view_box,
                "SHARE-DIALOG": inspector_box,
                "SIDEBAR": sidebar_box,
                "TOPBAR": topbar_box,
            },
            viewport=viewport,
        )
        capture_hashes[key] = capture_sha256(responsive_capture)
        viewport_reports[key] = {
            "dismissal_controls_reachable": True,
            "editor_reachable": True,
            "global_horizontal_overflow": 0,
            "primary_navigation_reachable": True,
        }

    after_publication = _canonical_publication_state(data_root)
    assert after_publication == before_publication
    assert external_requests == []
    return {
        "capture_sha256": capture_hashes,
        "external_request_count": len(external_requests),
        "second_brain_item_delta": after_publication["items"] - before_publication["items"],
        "second_brain_revision_delta": (
            after_publication["revisions"] - before_publication["revisions"]
        ),
        "synthetic_only": True,
        "viewport_reports": viewport_reports,
        "viewport_sequence": [
            f"{viewport['width']}x{viewport['height']}"
            for viewport in _RESPONSIVE_VIEWPORTS
        ],
    }


def _run_responsive_shell_editor_and_overlays_journey(
    playwright: object,
    data_root: Path,
) -> dict[str, object]:
    with _isolated_server(data_root) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport={"height": 900, "width": 1440},
            )
            try:
                return _exercise_responsive_shell_editor_and_overlays(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                    data_root,
                )
            finally:
                context.close()
        finally:
            browser.close()


def _active_focus_indicator_visible(page: object) -> bool:
    return bool(
        page.evaluate(
            """() => {
              const element = document.activeElement;
              if (!(element instanceof HTMLElement)) return false;
              const style = getComputedStyle(element);
              const outlineVisible = style.outlineStyle !== 'none'
                && parseFloat(style.outlineWidth || '0') >= 2;
              const shadowVisible = style.boxShadow !== 'none';
              const selection = window.getSelection();
              const editorCaretVisible = element.isContentEditable
                && selection?.isCollapsed
                && element.contains(selection.anchorNode);
              return outlineVisible || shadowVisible || editorCaretVisible;
            }"""
        )
    )


def _install_accessible_tab_order_probe(page: object) -> dict[str, object]:
    return page.evaluate(
        """() => {
          const visible = element => {
            const style = getComputedStyle(element);
            const closedDetails = element.closest('details:not([open])');
            return !element.hidden
              && (!closedDetails || closedDetails.querySelector(':scope > summary')?.contains(element))
              && element.getAttribute('tabindex') !== '-1'
              && style.display !== 'none'
              && style.visibility !== 'hidden'
              && element.getClientRects().length > 0;
          };
          const selector = [
            'a[href]',
            'summary',
            'button:not([disabled])',
            'input:not([disabled]):not([type="hidden"])',
            'select:not([disabled])',
            'textarea:not([disabled])',
            '[contenteditable="true"]',
            '[tabindex]:not([tabindex="-1"])',
          ].join(',');
          const targets = [...document.querySelectorAll(selector)]
            .filter((element, index, all) => visible(element) && all.indexOf(element) === index);
          const referencedText = element => String(element.getAttribute('aria-labelledby') || '')
            .split(/\\s+/)
            .filter(Boolean)
            .map(id => document.getElementById(id)?.textContent || '')
            .join(' ')
            .trim();
          const labelText = element => {
            if (element.labels?.length) {
              return [...element.labels].map(label => label.textContent || '').join(' ').trim();
            }
            return '';
          };
          const accessibleName = element => String(
            element.getAttribute('aria-label')
            || referencedText(element)
            || labelText(element)
            || element.getAttribute('title')
            || element.getAttribute('placeholder')
            || ((element.matches('button, summary, a[href]') || element.hasAttribute('role'))
              ? element.textContent
              : '')
            || '',
          ).trim();
          const implicitRole = element => {
            if (element.matches('button, summary')) return 'button';
            if (element.matches('a[href]')) return 'link';
            if (element.matches('select')) return 'combobox';
            if (element.matches('textarea')) return 'textbox';
            if (element.matches('input')) return element.type === 'checkbox' ? 'checkbox' : 'textbox';
            return '';
          };
          const violations = [];
          for (const [index, element] of targets.entries()) {
            if (!accessibleName(element)) violations.push(`name:${index}`);
            if (!(element.getAttribute('role') || implicitRole(element))) {
              violations.push(`role:${index}`);
            }
            for (const attribute of ['aria-expanded', 'aria-pressed', 'aria-selected']) {
              const value = element.getAttribute(attribute);
              if (value !== null && !['true', 'false'].includes(value)) {
                violations.push(`state:${index}:${attribute}`);
              }
            }
          }
          window.__uiA11yTargets = targets;
          return { count: targets.length, violations };
        }"""
    )


def _exercise_full_surface_accessibility(
    page: object,
    origin: str,
    user_id: str,
    password: str,
    data_root: Path,
) -> dict[str, object]:
    from playwright.sync_api import expect

    external_requests: list[str] = []

    def record_request(request: object) -> None:
        if not request.url.startswith(origin):
            external_requests.append(request.url)

    page.on("request", record_request)
    page.goto(origin, wait_until="domcontentloaded")
    page.locator("#userId").fill(user_id)
    page.locator("#password").fill(password)
    page.locator("#submitBtn").click()
    expect(page.locator("#homeNavBtn")).to_be_visible(timeout=20_000)
    expect(page.locator("#workspaceTitle")).to_contain_text("UI1 Owner", timeout=30_000)
    _wait_for_notion_ready(page)

    page.locator("#sidebarNewBtn").click()
    page.locator('#newPageMenu [data-new-page-type="page"]').click()
    title = page.locator("#pageTitle")
    expect(title).to_be_visible(timeout=15_000)
    with page.expect_response(
        lambda response: response.request.method == "POST"
        and re.search(r"/api/notion/pages/[^/]+/save$", response.url),
        timeout=20_000,
    ):
        title.fill("UI-A11Y-001")
    editor = page.locator(".block-editor[contenteditable='true']").first
    editor.fill("UI accessibility synthetic block")
    expect(page.locator("#status")).to_have_text("저장됨", timeout=20_000)
    before_publication = _canonical_publication_state(data_root)

    expect(title).to_have_attribute("aria-label", "페이지 제목")
    expect(editor).to_have_attribute("role", "textbox")
    expect(editor).to_have_attribute("aria-multiline", "true")
    expect(editor).to_have_attribute("aria-label", "페이지 블록")
    expect(page.locator("#collapseBtn")).to_have_attribute("aria-label", "사이드바 접기")
    expect(page.locator("#sidebarRestoreBtn")).to_have_attribute("aria-label", "사이드바 열기")
    expect(page.locator("#drawerCloseBtn")).to_have_attribute("aria-label", "닫기")

    skip_link = page.locator("#skipToContent")
    workspace_main = page.locator("#workspaceMain")
    expect(skip_link).to_have_attribute("href", "#workspaceMain")
    expect(workspace_main).to_have_attribute("tabindex", "-1")
    page.evaluate(
        """() => {
          document.body.setAttribute('tabindex', '-1');
          document.body.focus();
          document.body.removeAttribute('tabindex');
        }"""
    )
    page.keyboard.press("Tab")
    expect(skip_link).to_be_focused()
    expect(skip_link).to_be_visible()
    assert _active_focus_indicator_visible(page)
    page.keyboard.press("Enter")
    expect(workspace_main).to_be_focused()
    page.evaluate(
        "() => history.replaceState(null, '', location.pathname + location.search)"
    )
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#pageTitle")).to_have_value("UI-A11Y-001", timeout=20_000)
    skip_link = page.locator("#skipToContent")

    probe = _install_accessible_tab_order_probe(page)
    assert probe["count"] >= 20
    assert probe["violations"] == []
    assert page.evaluate("() => window.__uiA11yTargets[0]?.id || ''") == "skipToContent"
    skip_link.focus()
    assert _active_focus_indicator_visible(page)
    focus_indicator_count = 1
    for expected_index in range(1, int(probe["count"])):
        page.keyboard.press("Tab")
        actual_index = page.evaluate(
            "() => window.__uiA11yTargets.indexOf(document.activeElement)"
        )
        assert actual_index == expected_index, page.evaluate(
            """expected => ({expected,actual:window.__uiA11yTargets.indexOf(document.activeElement),
              focus:document.activeElement?.outerHTML?.slice(0,350),
              focusVisible:document.activeElement?.matches(':focus-visible'),
              caretInside:document.activeElement?.contains(window.getSelection()?.anchorNode),
              selectionCollapsed:window.getSelection()?.isCollapsed,
              skipped:window.__uiA11yTargets.slice(expected,window.__uiA11yTargets.indexOf(document.activeElement))
                .map(el=>el.outerHTML.slice(0,280))})""", expected_index
        )
        assert _active_focus_indicator_visible(page), page.evaluate(
            """() => ({index:window.__uiA11yTargets.indexOf(document.activeElement),
              element:document.activeElement?.outerHTML?.slice(0,420),
              focusVisible:document.activeElement?.matches(':focus-visible'),
              caretInside:document.activeElement?.contains(window.getSelection()?.anchorNode),
              selectionCollapsed:window.getSelection()?.isCollapsed,
              outline:getComputedStyle(document.activeElement).outline,
              shadow:getComputedStyle(document.activeElement).boxShadow})"""
        )
        focus_indicator_count += 1

    search_trigger = page.locator("#sidebarSearchBtn")
    search_trigger.focus()
    page.keyboard.press("Enter")
    search_dialog = page.locator(".search-dialog")
    search_input = page.locator("#globalSearchInput")
    expect(search_dialog).to_be_visible()
    expect(search_dialog).to_have_attribute("role", "dialog")
    expect(search_dialog).to_have_attribute("aria-modal", "true")
    expect(search_input).to_be_focused()
    assert page.evaluate("() => document.activeElement.matches(':focus-visible')")
    assert page.evaluate(
        "() => visibleModalFocusables(document.querySelector('.search-dialog')).length > 0"
    )
    page.keyboard.press("Shift+Tab")
    assert page.evaluate(
        """() => document.activeElement === visibleModalFocusables(document.querySelector('.search-dialog')).at(-1)"""
    )
    assert _active_focus_indicator_visible(page)
    page.keyboard.press("Tab")
    expect(search_input).to_be_focused()
    page.keyboard.press("Escape")
    expect(search_dialog).to_be_hidden()
    expect(search_trigger).to_be_focused()

    share_trigger = page.locator("#shareDrawerBtn")
    share_trigger.focus()
    page.keyboard.press("Enter")
    inspector = page.locator("#inspector")
    share_user = page.locator("#shareUser")
    share_role = page.locator("#shareRole")
    expect(inspector).to_be_visible()
    expect(inspector).to_have_attribute("role", "dialog")
    expect(inspector).to_have_attribute("aria-modal", "false")
    expect(share_user).to_be_focused()
    assert _active_focus_indicator_visible(page)
    page.keyboard.press("Tab")
    expect(share_role).to_be_focused()
    assert _active_focus_indicator_visible(page)
    page.keyboard.press("Escape")
    expect(inspector).to_be_hidden()
    expect(share_trigger).to_be_focused()

    publish_trigger = page.locator("#pageMoreBtn")
    publish_trigger.focus()
    page.keyboard.press("Enter")
    publish_action = page.locator('#pageMenu [data-page-action="second_brain"]')
    expect(publish_action).to_be_visible()
    publish_action.focus()
    page.keyboard.press("Enter")
    modal = page.locator("#modalBg .modal")
    expect(modal).to_be_visible(timeout=45_000)
    cancel_action = _wait_for_publication_action(page, "취소")
    expect(modal).to_be_focused()
    expect(modal).to_have_attribute("role", "dialog")
    expect(modal).to_have_attribute("aria-modal", "true")
    page.keyboard.press("Tab")
    assert page.evaluate(
        """() => document.activeElement === visibleModalFocusables(document.querySelector('#modalBg .modal'))[0]"""
    )
    assert _active_focus_indicator_visible(page)
    page.keyboard.press("Shift+Tab")
    assert page.evaluate(
        """() => document.activeElement === visibleModalFocusables(document.querySelector('#modalBg .modal')).at(-1)"""
    )
    assert _active_focus_indicator_visible(page)
    page.keyboard.press("Tab")
    assert page.evaluate(
        """() => document.activeElement === visibleModalFocusables(document.querySelector('#modalBg .modal'))[0]"""
    )
    cancel_action.focus()
    assert _active_focus_indicator_visible(page)
    page.keyboard.press("Enter")
    expect(page.locator("#modalBg")).to_be_hidden()
    expect(publish_trigger).to_be_focused()

    icon_trigger = page.locator("#addIconBtn")
    icon_trigger.focus()
    page.keyboard.press("Enter")
    expect(modal).to_be_visible()
    expect(page.locator("#pageIconPicker input[type='search']")).to_be_focused()
    expect(page.locator("#modalClose")).to_be_hidden()
    page.keyboard.press("Escape")
    expect(page.locator("#modalBg")).to_be_hidden()
    expect(icon_trigger).to_be_hidden()
    expect(page.locator("#pageIconBtn")).to_be_focused()

    page.emulate_media(reduced_motion="reduce")
    assert page.evaluate("() => matchMedia('(prefers-reduced-motion: reduce)').matches")
    assert page.evaluate("() => getComputedStyle(document.body).scrollBehavior") == "auto"
    page.emulate_media(forced_colors="active")
    assert page.evaluate("() => matchMedia('(forced-colors: active)').matches")
    page.evaluate(
        """() => {
          document.body.setAttribute('tabindex', '-1');
          document.body.focus();
          document.body.removeAttribute('tabindex');
        }"""
    )
    page.keyboard.press("Tab")
    assert _active_focus_indicator_visible(page)
    page.emulate_media(reduced_motion="no-preference", forced_colors="none")

    after_publication = _canonical_publication_state(data_root)
    assert after_publication == before_publication
    assert external_requests == []
    capture = _capture(
        scenario_id="UI-A11Y-001",
        state={"count": int(probe["count"]), "result": "pass"},
        keyboard={
            "result": "pass",
            "sequence": [
                "PAGE-ROOT",
                "SKIP-CONTENT",
                "WORKSPACE-MAIN",
                "SECOND-BRAIN-PREVIEW",
            ],
        },
        focus={
            "sequence": [
                "SEARCH-TRIGGER",
                "SEARCH-INPUT",
                "SEARCH-TRIGGER",
                "SHARE-DIALOG",
                "SHARE-TRIGGER",
                "PREVIEW-DIALOG",
                "SECOND-BRAIN-PREVIEW",
            ]
        },
        a11y={"count": focus_indicator_count, "result": "pass"},
        semantic_visual={"region": "PAGE-ROOT", "state": "visible"},
        error_recovery={"result": "pass"},
        geometry={},
    )
    return {
        "accessible_tab_target_count": int(probe["count"]),
        "capture_sha256": capture_sha256(capture),
        "external_request_count": len(external_requests),
        "focus_indicator_count": focus_indicator_count,
        "modal_focus_traps": 2,
        "unlocked_modal_escape_restored": True,
        "second_brain_item_delta": after_publication["items"] - before_publication["items"],
        "second_brain_revision_delta": (
            after_publication["revisions"] - before_publication["revisions"]
        ),
        "synthetic_only": True,
    }


def _run_full_surface_accessibility_journey(
    playwright: object,
    data_root: Path,
) -> dict[str, object]:
    with _isolated_server(data_root) as (origin, user_id, password):
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            context = browser.new_context(
                locale="ko-KR",
                viewport={"height": 900, "width": 1440},
            )
            try:
                return _exercise_full_surface_accessibility(
                    context.new_page(),
                    origin,
                    user_id,
                    password,
                    data_root,
                )
            finally:
                context.close()
        finally:
            browser.close()


@pytest.mark.integration
def test_ui1_local_chrome_shell_search_and_editor_are_repeatable_and_isolated(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    secrets_used: list[tuple[str, str]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            report, user_id, password = _run_chrome_journey(
                playwright,
                tmp_path / label,
                include_second_brain=False,
            )
            reports.append(report)
            secrets_used.append((user_id, password))

    assert reports[0] == reports[1]
    assert reports[0]["second_brain"] is None
    assert reports[0]["state"] == {
        "content_restored": True,
        "page_saved": True,
        "search_result_visible": True,
        "sidebar_restored": True,
    }
    assert reports[0]["focus"]["after_search_escape"] == "SEARCH-TRIGGER"
    assert reports[0]["focus"]["after_sidebar_collapse"] == {
        "name": "SIDEBAR-RESTORE",
        "state": "blurred",
    }
    assert reports[0]["focus"]["after_sidebar_restore"] == {
        "name": "SIDEBAR-COLLAPSE",
        "state": "blurred",
    }
    assert reports[0]["focus"]["after_sidebar_keyboard_collapse"] == "SIDEBAR-RESTORE"
    assert reports[0]["focus"]["after_sidebar_keyboard_restore"] == "SIDEBAR-COLLAPSE"
    assert set(reports[0]["capture_sha256"]) == {
        "UI-A11Y-001",
        "UI-EDITOR-001",
        "UI-SEARCH-001",
        "UI-SHELL-002",
    }
    assert set(reports[0]["action_capture_sha256"]) == {
        "UI-EDITOR-001-A03",
        "UI-EDITOR-001-A04",
        "UI-SEARCH-001-A01",
        "UI-SEARCH-001-A02",
        "UI-SEARCH-001-A04",
    }
    assert reports[0]["action_captures"]["UI-EDITOR-001-A03"]["focus"] == {
        "name": "PAGE-EDITOR-GROUP",
        "state": "focused",
    }
    assert reports[0]["action_captures"]["UI-EDITOR-001-A03"]["a11y"] == {
        "name": "PAGE-EDITOR-GROUP",
        "role": "GROUP",
    }
    assert reports[0]["action_captures"]["UI-EDITOR-001-A04"]["focus"] == {
        "name": "PAGE-CANVAS",
        "state": "not_focused",
    }
    assert reports[0]["action_captures"]["UI-SEARCH-001-A04"]["focus"] == {
        "sequence": ["SEARCH-INPUT", "PAGE-ROOT"],
    }
    assert reports[0]["action_captures"]["UI-SEARCH-001-A04"]["a11y"] == {
        "name": "SEARCH-RESULT",
        "role": "option",
    }
    encoded = json.dumps(reports[0], ensure_ascii=False, sort_keys=True)
    assert str(tmp_path) not in encoded
    for user_id, password in secrets_used:
        assert user_id not in encoded
        assert password not in encoded


@pytest.mark.integration
def test_ui1_second_brain_cancel_approve_and_replay_contract(tmp_path: Path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    secrets_used: list[tuple[str, str]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            report, user_id, password = _run_chrome_journey(
                playwright,
                tmp_path / label,
                include_second_brain=True,
            )
            reports.append(report)
            secrets_used.append((user_id, password))

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["second_brain"] is not None
    assert report["second_brain"]["cancel_item_delta"] == 0
    assert report["second_brain"]["cancel_revision_delta"] == 0
    assert report["second_brain"]["approve_item_delta"] == 1
    assert report["second_brain"]["approve_revision_delta"] == 1
    assert report["second_brain"]["replay_item_delta"] == 0
    assert report["second_brain"]["replay_revision_delta"] == 0
    assert report["second_brain"]["source_snapshot_matches"] == 1
    assert set(report["capture_sha256"]) == {
        "UI-A11Y-001",
        "UI-EDITOR-001",
        "UI-SB-001",
        "UI-SB-002",
        "UI-SEARCH-001",
        "UI-SHELL-002",
    }
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert str(tmp_path) not in encoded
    for user_id, password in secrets_used:
        assert user_id not in encoded
        assert password not in encoded


@pytest.mark.integration
def test_ui_shell_001_workspace_switch_is_scoped_repeatable_and_focus_safe(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_workspace_switch_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["active_workspace_restored"] is True
    assert report["cross_workspace_direct_open_rejected"] is True
    assert report["focus_restored"] is True
    assert report["global_search_requests"] == 0
    assert report["legacy_page_list_requests"] == 0
    assert report["scoped_page_request_count"] == 13
    assert report["synthetic_only"] is True
    assert report["workspace_page_counts"] == [1, 1, 1]
    assert set(report["action_capture_sha256"]) == {
        "UI-SHELL-001-A01",
        "UI-SHELL-001-A02",
    }
    assert report["action_captures"]["UI-SHELL-001-A01"]["state"][
        "workspace_alias"
    ] == "REF-FREE-B"
    assert report["action_captures"]["UI-SHELL-001-A01"]["focus"] == {
        "name": "WORKSPACE-SIDEBAR-GROUP",
        "state": "focused",
    }
    assert report["action_captures"]["UI-SHELL-001-A01"]["a11y"] == {
        "name": "WORKSPACE-SIDEBAR-GROUP",
        "role": "GROUP",
    }
    assert report["action_captures"]["UI-SHELL-001-A02"]["state"][
        "workspace_alias"
    ] == "REF-FREE-A"
    assert report["action_captures"]["UI-SHELL-001-A02"]["focus"] == {
        "name": "WORKSPACE-SWITCHER",
        "state": "focused",
    }
    assert set(report["capture_sha256"]) == {"UI-SHELL-001"}


@pytest.mark.integration
def test_ui_shell_003_home_page_topbar_and_breadcrumb_are_coherent(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_home_page_breadcrumb_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["breadcrumb_matches_active_page"] is True
    assert report["focus_sequence"] == ["HOME-MAIN", "PAGE-CANVAS"]
    assert report["hidden_workspace_metadata_count"] == 0
    assert report["pointer_home_content_blurred"] is True
    assert report["pointer_page_content_blurred"] is True
    assert report["synthetic_only"] is True
    assert set(report["capture_sha256"]) == {"UI-SHELL-003"}


@pytest.mark.integration
def test_ui_tree_001_nested_page_expand_collapse_is_keyboard_focus_safe(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_nested_page_tree_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["collapsed_descendant_count"] == 0
    assert report["arrow_left_collapsed"] is True
    assert report["focus_sequence"] == ["TREE-ROOT", "TREE-ROOT"]
    assert report["root_aria_expanded"] == "false"
    assert report["synthetic_only"] is True
    assert report["tree_role"] == "tree"
    assert set(report["capture_sha256"]) == {"UI-TREE-001"}


@pytest.mark.integration
def test_ui_tree_002_create_rename_nest_reorder_move_is_coherent(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_page_tree_mutation_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["active_path_coherent"] is True
    assert report["failed_move_request_delta"] == 0
    assert report["focus_sequence"] == [
        "PAGE-TITLE",
        "PAGE-MENU",
        "TREE-CHILD-B",
        "TREE-ROOT",
    ]
    assert report["legacy_create_requests"] == 0
    assert report["nested_child_count"] == 2
    assert report["synthetic_only"] is True
    assert report["workspace_create_requests"] == 1
    assert set(report["capture_sha256"]) == {"UI-TREE-002"}
    assert set(report["action_capture_sha256"]) == {
        "UI-TREE-002-A03",
        "UI-TREE-002-A07",
        "UI-TREE-002-A08",
        "UI-TREE-002-A09",
    }
    assert report["action_captures"]["UI-TREE-002-A07"]["focus"] == {
        "name": "PAGE-MENU",
        "state": "focused",
    }


@pytest.mark.integration
def test_ui_tree_003_duplicate_trash_restore_preserves_second_brain(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_page_tree_lifecycle_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["duplicate_subtree_count"] == 2
    assert report["focus_sequence"] == [
        "PAGE-MENU",
        "TRASH-BUTTON",
        "RESTORE-POST-ACTION-BUTTON",
    ]
    assert report["legacy_lifecycle_requests"] == 0
    assert report["permanent_delete_requests"] == 0
    assert report["second_brain_item_delta"] == 0
    assert report["second_brain_revision_delta"] == 0
    assert report["synthetic_only"] is True
    assert report["workspace_archive_requests"] == 1
    assert report["workspace_duplicate_requests"] == 1
    assert report["workspace_restore_requests"] == 1
    assert set(report["capture_sha256"]) == {"UI-TREE-003"}
    assert set(report["action_capture_sha256"]) == {
        "UI-TREE-003-A01",
        "UI-TREE-003-A02",
        "UI-TREE-003-A03",
    }


@pytest.mark.integration
def test_ui_editor_004_korean_ime_commits_once_after_composition(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(_run_korean_ime_journey(playwright, tmp_path / label))

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["block_count_during_composition"] == 1
    assert report["committed_save_count"] == 1
    assert report["content_restored"] is True
    assert report["focus_sequence"] == ["EDITOR-BLOCK", "PAGE-CANVAS"]
    assert report["intermediate_save_count"] == 0
    assert report["second_brain_item_delta"] == 0
    assert report["second_brain_revision_delta"] == 0
    assert report["synthetic_only"] is True
    assert set(report["capture_sha256"]) == {"UI-EDITOR-004"}
    assert set(report["action_capture_sha256"]) == {"UI-EDITOR-004-A01"}


@pytest.mark.integration
def test_ui_editor_002_slash_menu_transform_is_accessible_and_persistent(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_slash_menu_block_transform_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["content_restored"] is True
    assert report["focus_sequence"] == ["PAGE-EDITOR-GROUP", "PAGE-EDITOR-GROUP"]
    assert report["action_captures"]["UI-EDITOR-002-A02"]["focus"] == {
        "name": "PAGE-EDITOR-GROUP",
        "state": "focused",
    }
    assert report["save_count"] == 1
    assert report["second_brain_item_delta"] == 0
    assert report["second_brain_revision_delta"] == 0
    assert report["synthetic_only"] is True
    assert report["unsupported_command_preserved"] is True
    assert set(report["capture_sha256"]) == {
        "UI-EDITOR-002-A01",
        "UI-EDITOR-002-A02",
    }


@pytest.mark.integration
def test_ui_editor_003_selection_clipboard_and_history_are_deterministic(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_selection_clipboard_history_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["content_restored"] is True
    assert report["focus_sequence"] == ["PAGE-EDITOR-GROUP", "EDITOR-BLOCK"]
    assert report["sanitized_markup_persisted"] is True
    assert report["save_count"] == 1
    assert report["second_brain_item_delta"] == 0
    assert report["second_brain_revision_delta"] == 0
    assert report["selected_block_count"] == 2
    assert report["synthetic_only"] is True
    assert report["unsafe_markup_persisted"] is False
    assert set(report["capture_sha256"]) == {
        "UI-EDITOR-003-A01",
        "UI-EDITOR-003-A02",
    }


@pytest.mark.integration
def test_ui_editor_005_main_full_and_side_peek_share_one_persistence_model(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_editor_surface_parity_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["content_restored"] is True
    assert report["focus_sequence"] == ["SIDE-PEEK-TITLE", "WORKSPACE-MAIN"]
    assert report["action_captures"]["UI-EDITOR-005-A01"]["a11y"] == {
        "name": "SIDE-PEEK",
        "role": "REGION",
    }
    assert report["action_captures"]["UI-EDITOR-005-A02"]["focus"] == {
        "name": "PAGE-CANVAS",
        "state": "not_focused",
    }
    assert report["save_count"] == 2
    assert report["second_brain_item_delta"] == 0
    assert report["second_brain_revision_delta"] == 0
    assert report["side_peek_closed_on_full_page"] is True
    assert report["synthetic_only"] is True
    assert set(report["capture_sha256"]) == {
        "UI-EDITOR-005-A01",
        "UI-EDITOR-005-A02",
    }


@pytest.mark.integration
def test_ui_editor_006_network_and_conflict_drafts_recover_without_false_save(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_editor_draft_recovery_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["conflict_autosave_attempt_delta"] == 0
    assert report["conflict_draft_restored"] is True
    assert report["discard_loaded_server_revision"] is True
    assert report["focus_sequence"] == ["PAGE-CANVAS", "PAGE-CANVAS"]
    assert report["network_draft_restored"] is True
    assert report["retry_operation_reused"] is True
    assert report["second_brain_item_delta"] == 0
    assert report["second_brain_revision_delta"] == 0
    assert report["synthetic_only"] is True
    assert set(report["capture_sha256"]) == {
        "UI-EDITOR-006-A01",
        "UI-EDITOR-006-A02",
    }


@pytest.mark.integration
def test_ui_db_001_typed_row_properties_round_trip_through_a_page(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_typed_database_round_trip_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["focus_sequence"] == ["DATABASE-GRID:not_focused", "DATABASE-ROW"]
    assert report["properties_restored"] is True
    assert report["row_page_reopened"] is True
    assert report["save_count"] >= 4
    assert report["second_brain_item_delta"] == 0
    assert report["second_brain_revision_delta"] == 0
    assert report["synthetic_only"] is True
    assert set(report["capture_sha256"]) == {
        "UI-DB-001-A01",
        "UI-DB-001-A02",
    }
    assert set(report["action_captures"]) == {
        "UI-DB-001-A01",
        "UI-DB-001-A02",
    }


@pytest.mark.integration
def test_ui_db_002_filter_sort_and_group_state_survive_reload(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_database_view_state_round_trip_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["filter_sort_group_restored"] is True
    assert report["source_rows_unchanged"] is True
    assert report["save_count"] == 3
    assert report["second_brain_item_delta"] == 0
    assert report["second_brain_revision_delta"] == 0
    assert report["synthetic_only"] is True
    assert set(report["capture_sha256"]) == {
        "UI-DB-002-A01",
        "UI-DB-002-A02",
    }
    assert set(report["action_captures"]) == {
        "UI-DB-002-A01",
        "UI-DB-002-A02",
    }


@pytest.mark.integration
def test_ui_db_003_database_views_preserve_canonical_rows_and_focus(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_database_multi_view_round_trip_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["canonical_rows_preserved"] is True
    assert report["focus_sequence"] == ["TABLE", "BOARD", "LIST", "GALLERY", "CALENDAR", "TABLE"]
    assert report["row_mutation_count"] == 0
    assert report["second_brain_item_delta"] == 0
    assert report["second_brain_revision_delta"] == 0
    assert report["synthetic_only"] is True
    assert report["view_count"] == 5
    assert report["view_save_count"] == 10
    assert report["view_state_restored"] is True
    assert set(report["capture_sha256"]) == {"UI-DB-003-A01"}
    assert set(report["action_captures"]) == {"UI-DB-003-A01"}


@pytest.mark.integration
def test_ui_search_002_in_page_find_link_and_backlink_stay_workspace_scoped(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_in_page_find_and_backlink_navigation_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["focus_sequence"] == ["PAGE-FIND-INPUT", "EDITOR-BLOCK", "PAGE-CANVAS"]
    assert report["page_mutation_count_after_setup"] == 0
    assert report["second_brain_item_delta"] == 0
    assert report["second_brain_revision_delta"] == 0
    assert report["synthetic_only"] is True
    assert report["workspace_scope_leak_count"] == 0
    assert set(report["capture_sha256"]) == {
        "UI-SEARCH-002-A01",
        "UI-SEARCH-002-A02",
    }
    assert set(report["action_captures"]) == {
        "UI-SEARCH-002-A01",
        "UI-SEARCH-002-A02",
    }


@pytest.mark.integration
def test_ui_collab_001_comment_thread_create_resolve_reopen_round_trip(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_comment_thread_round_trip_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["comment_mutation_sequence"] == ["POST", "PATCH", "PATCH"]
    assert report["comment_round_trip"] is True
    assert report["external_request_count"] == 0
    assert report["focus_sequence"] == ["COMMENT-INPUT", "BODY", "RESOLVE-CONTROL"]
    assert report["second_brain_item_delta"] == 0
    assert report["second_brain_revision_delta"] == 0
    assert report["synthetic_only"] is True
    assert set(report["capture_sha256"]) == {
        "UI-COLLAB-001-A01",
        "UI-COLLAB-001-A02",
    }
    assert set(report["action_captures"]) == {
        "UI-COLLAB-001-A01",
        "UI-COLLAB-001-A02",
    }


@pytest.mark.integration
def test_ui_share_001_dialog_open_escape_and_focus_are_read_only(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_share_dialog_read_only_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["access_mutation_count"] == 0
    assert report["external_request_count"] == 0
    assert report["focus_sequence"] == ["SHARE-INVITE-INPUT", "SHARE-TRIGGER"]
    assert report["second_brain_item_delta"] == 0
    assert report["second_brain_revision_delta"] == 0
    assert report["synthetic_only"] is True
    assert set(report["capture_sha256"]) == {
        "UI-SHARE-001-A01",
        "UI-SHARE-001-A02",
    }
    assert set(report["action_captures"]) == {
        "UI-SHARE-001-A01",
        "UI-SHARE-001-A02",
    }


@pytest.mark.integration
def test_ui_sb_003_session_attach_detach_revalidates_current_access(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_session_attachment_round_trip_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["attachment_mutation_sequence"] == ["POST", "DELETE"]
    assert report["current_access_revalidated"] is True
    assert report["external_request_count"] == 0
    assert report["focus_sequence"] == ["SESSION-CONTEXT", "SESSION-CONTEXT"]
    assert report["second_brain_item_delta"] == 0
    assert report["second_brain_revision_delta"] == 0
    assert report["synthetic_only"] is True
    assert set(report["capture_sha256"]) == {
        "UI-SB-003-A01",
        "UI-SB-003-A02",
    }


@pytest.mark.integration
def test_ui_resp_001_shell_editor_and_overlays_fit_adopted_viewports(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_responsive_shell_editor_and_overlays_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["external_request_count"] == 0
    assert report["second_brain_item_delta"] == 0
    assert report["second_brain_revision_delta"] == 0
    assert report["synthetic_only"] is True
    assert report["viewport_sequence"] == [
        "1440x900",
        "1280x800",
        "980x800",
        "720x800",
    ]
    assert set(report["capture_sha256"]) == set(report["viewport_sequence"])
    assert all(
        viewport_report == {
            "dismissal_controls_reachable": True,
            "editor_reachable": True,
            "global_horizontal_overflow": 0,
            "primary_navigation_reachable": True,
        }
        for viewport_report in report["viewport_reports"].values()
    )


@pytest.mark.integration
def test_ui_a11y_001_full_surface_keyboard_focus_and_media_contract(
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        for label in ("run-a", "run-b"):
            reports.append(
                _run_full_surface_accessibility_journey(
                    playwright,
                    tmp_path / label,
                )
            )

    assert reports[0] == reports[1]
    report = reports[0]
    assert report["accessible_tab_target_count"] >= 20
    assert report["focus_indicator_count"] == report["accessible_tab_target_count"]
    assert report["modal_focus_traps"] == 2
    assert report["unlocked_modal_escape_restored"] is True
    assert report["external_request_count"] == 0
    assert report["second_brain_item_delta"] == 0
    assert report["second_brain_revision_delta"] == 0
    assert report["synthetic_only"] is True
