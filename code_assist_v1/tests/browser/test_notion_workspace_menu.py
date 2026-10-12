"""Workspace menu grouping in an isolated collaborative browser."""

import json
import os
from pathlib import Path

import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app,
)
from code_assist_v1.tests.browser.test_notion_document_extras import document_page
from code_assist_v1.tests.test_notion_personal_workspace import environment as personal_appearance_environment


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
def test_workspace_menu_groups_primary_workspace_and_local_actions(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False) as (origin, password, _authority, _workspace_id, page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 873, "height": 836})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' && State.navigation.enabled && State.navigation.collaborativeSharing", timeout=30_000)
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.wait_for_function("expected => typeof State !== 'undefined' && State.pageData?.page?.id === expected && State.liveCollaboration.crdtReady", arg=page_id, timeout=30_000)
                sidebar = page.locator(".notion-root > .sidebar").bounding_box()
                assert sidebar and 268 <= sidebar["width"] <= 272
                crumb = page.locator('#breadcrumb [data-crumb-id]').first.bounding_box()
                assert crumb and 280 <= crumb["x"] <= 284

                switcher = page.locator("#workspaceName")
                switcher.click()
                menu = page.locator("#workspacePopover")
                expect(menu).to_be_visible()
                expect(menu.locator(".popover-title")).to_have_count(0)
                expect(menu.locator('[data-workspace-action="people"]')).to_be_visible()
                expect(menu.locator('[data-workspace-action="invite"]')).to_be_visible()
                expect(menu.locator('[data-workspace-action="people"] .workspace-menu-gear')).to_be_visible()
                expect(menu.locator('[data-workspace-action="invite"] .workspace-menu-mail')).to_be_visible()
                assert menu.locator(".workspace-menu-gear").evaluate("icon => getComputedStyle(icon).width") == "16px"
                expect(menu.locator("[data-navigation-workspace]")).to_have_count(1)
                expect(menu.locator('[data-create-collaborative-workspace]')).to_be_visible()
                expect(menu.locator('[data-workspace-action="home"]')).to_be_visible()
                expect(menu.locator('[data-workspace-action="logout"]')).to_be_visible()
                order = menu.evaluate("""root => [...root.querySelectorAll(
                  '.workspace-popover-head, .workspace-menu-primary, .workspace-account, '
                  + '[data-navigation-workspace], [data-create-collaborative-workspace], '
                  + '.workspace-menu-utilities, [data-workspace-action="logout"]'
                )].map(node => node.classList.contains('workspace-popover-head') ? 'header'
                  : node.classList.contains('workspace-menu-primary') ? 'primary'
                  : node.classList.contains('workspace-account') ? 'account'
                  : node.hasAttribute('data-navigation-workspace') ? 'workspace'
                  : node.hasAttribute('data-create-collaborative-workspace') ? 'new-workspace'
                  : node.classList.contains('workspace-menu-utilities') ? 'utilities' : 'logout')""")
                assert order == ["header", "primary", "account", "workspace", "new-workspace", "utilities", "logout"]
                bounds = menu.bounding_box()
                assert bounds and 298 <= bounds["width"] <= 302
                assert 7 <= bounds["x"] <= 9 and bounds["x"] + bounds["width"] <= 873
                assert bounds["y"] + bounds["height"] <= 836
                artifact_dir = os.environ.get("NOTION_WORKSPACE_MENU_ARTIFACT_DIR")
                for theme, width, height in (("light", 873, 836), ("dark", 390, 700), ("light", 280, 430)):
                    page.set_viewport_size({"width": width, "height": height})
                    page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
                    if width <= 720 and not page.evaluate("() => document.body.classList.contains('mobile-sidebar-open')"):
                        page.locator("#sidebarRestoreBtn").click()
                    if switcher.get_attribute("aria-expanded") != "true":
                        switcher.click()
                    expect(menu).to_be_visible()
                    page.wait_for_function("width => {const r=document.querySelector('#workspacePopover').getBoundingClientRect();return r.width>0 && r.left>=0 && r.right<=width;}", arg=width)
                    metrics = menu.evaluate("""root => ({
                      rect:root.getBoundingClientRect().toJSON(),
                      viewport:{width:innerWidth,height:innerHeight},
                      radius:getComputedStyle(root).borderRadius,
                      headIcon:root.querySelector('.workspace-popover-head .workspace-switch-avatar').getBoundingClientRect().toJSON(),
                      actions:[...root.querySelectorAll('.workspace-menu-row')].map(node=>({
                        name:node.textContent.trim(),...node.getBoundingClientRect().toJSON()})),
                      switches:[...root.querySelectorAll('.workspace-switch-row')].map(node=>({
                        row:node.getBoundingClientRect().toJSON(),
                        icon:node.querySelector('.workspace-switch-avatar').getBoundingClientRect().toJSON(),
                        label:node.querySelector('strong').getBoundingClientRect().toJSON(),
                        role:node.querySelector('small').getBoundingClientRect().toJSON()
                      }))
                    })""")
                    assert metrics["rect"]["x"] >= 0 and metrics["rect"]["right"] <= width
                    assert metrics["rect"]["y"] >= 0 and metrics["rect"]["bottom"] <= height
                    assert metrics["radius"] == "10px"
                    assert metrics["headIcon"]["width"] == metrics["headIcon"]["height"] == 36
                    assert all(item["height"] == 28 for item in metrics["actions"]), metrics["actions"]
                    for item in metrics["switches"]:
                        assert item["row"]["height"] == 28
                        assert item["icon"]["width"] == item["icon"]["height"] == 20
                        assert item["label"]["right"] <= item["role"]["left"]
                    if artifact_dir:
                        target = Path(artifact_dir)
                        target.mkdir(parents=True, exist_ok=True)
                        (target / f"workspace-menu-{theme}-{width}.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
                        page.screenshot(path=str(target / f"workspace-menu-{theme}-{width}.png"))
                    if height <= 430:
                        logout = menu.locator('[data-workspace-action="logout"]')
                        logout.scroll_into_view_if_needed()
                        logout.focus()
                        scroller = page.locator("#workspaceHoverPages")
                        scroll_before = scroller.evaluate("root => root.scrollTop")
                        assert scroll_before > 0
                        page.evaluate("() => renderWorkspacePopover()")
                        expect(logout).to_be_focused()
                        assert abs(scroller.evaluate("root => root.scrollTop") - scroll_before) < 1
                        logout_bounds = logout.bounding_box()
                        assert logout_bounds and logout_bounds["y"] >= metrics["rect"]["top"]
                        assert logout_bounds["y"] + logout_bounds["height"] <= metrics["rect"]["bottom"]
                        if artifact_dir:
                            page.screenshot(path=str(target / f"workspace-menu-{theme}-{width}-scrolled.png"))
                page.set_viewport_size({"width":873,"height":836})
                page.evaluate("() => document.documentElement.dataset.theme = 'light'")
                page.keyboard.press("Escape")
                expect(menu).to_be_hidden()
                expect(switcher).to_be_focused()

                def downgraded_workspace_list(route):
                    response = route.fetch()
                    payload = response.json()
                    selected = next(item for item in payload["items"]
                                    if item["id"] == _workspace_id)
                    selected["access"]["role"] = "member"
                    route.fulfill(response=response, json=payload)

                switcher.press("Enter")
                menu.locator('[data-workspace-action="invite"]').focus()
                page.route("**/api/notion/workspaces", downgraded_workspace_list, times=1)
                assert page.evaluate("() => refreshWorkspaceNavigationCapability()") is True
                page.wait_for_function(
                    "id => State.navigation.workspaces.some(item => item.id === id"
                    " && item.access?.role === 'member')", arg=_workspace_id,
                )
                expect(page.locator("#workspaceMemberSection")).to_have_attribute("hidden", "")
                expect(menu).to_be_hidden()
                expect(switcher).to_be_focused()
                switcher.press("Enter")
                expect(menu).to_be_visible()
                expect(menu.locator('[data-workspace-action="people"]')).to_have_count(0)
                expect(menu.locator('[data-workspace-action="invite"]')).to_have_count(0)
                expect(menu.locator('[data-workspace-action="workspace-icon"]')).to_have_count(0)
                expect(menu.locator('[data-workspace-action="home"]')).to_be_visible()
                expect(menu.locator(f'[data-navigation-workspace="{_workspace_id}"]')).to_be_focused()
                page.keyboard.press("Escape")
                assert page.evaluate("() => refreshWorkspaceNavigationCapability()") is True
                switcher.click()
                menu.locator('[data-workspace-action="invite"]').click()
                expect(menu).to_be_hidden()
                expect(page.locator("#inspector")).to_be_visible()
                expect(page.locator("#inspector")).to_have_attribute("data-share-surface", "workspace-invite")
                assert not errors
            finally:
                browser.close()


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
def test_workspace_menu_page_member_keeps_local_actions_without_admin_controls(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False) as (origin, password, _authority, workspace_id, page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 873, "height": 836})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("content-editor")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                page.wait_for_function(
                    "() => typeof State !== 'undefined' && State.user?.id === 'content-editor'"
                    " && State.navigation.enabled && State.navigation.collaborativeSharing",
                    timeout=30_000,
                )
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.wait_for_function(
                    "expected => State.pageData?.page?.id === expected.pageId"
                    " && State.liveCollaboration.crdtReady"
                    " && State.navigation.workspaceId === expected.workspaceId"
                    " && State.navigation.workspaces.some(item => item.id === expected.workspaceId"
                    " && item.access?.role === 'member')",
                    arg={"pageId": page_id, "workspaceId": workspace_id}, timeout=30_000,
                )
                switcher = page.locator("#workspaceName")
                switcher.click()
                menu = page.locator("#workspacePopover")
                expect(menu).to_be_visible()
                expect(menu.locator('[data-workspace-action="invite"]')).to_have_count(0)
                expect(menu.locator('[data-workspace-action="people"]')).to_have_count(0)
                expect(menu.locator('[data-workspace-action="workspace-icon"]')).to_have_count(0)
                expect(menu.locator('[data-workspace-action="home"]')).to_be_visible()
                expect(menu.locator('[data-workspace-action="theme"]')).to_be_visible()
                expect(menu.locator('[data-workspace-action="logout"]')).to_be_visible()
                page.keyboard.press("Escape")
                expect(menu).to_be_hidden()
                expect(switcher).to_be_focused()
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_personal_workspace_menu_order_focus_and_shared_settings(
        document_page, personal_appearance_environment, monkeypatch):
    from flask import Flask
    from playwright.sync_api import expect
    from urllib.parse import urlsplit
    from code_assist_v1 import notion_store as store
    from code_assist_v1 import routes_notion_personal_workspace as routes

    page = document_page
    users, _layout = personal_appearance_environment
    actor = page.evaluate("State.user.id")
    users.append({"id":actor,"display_name":"UI1 Owner","disabled":False})
    store.bootstrap_workspace(actor)
    shared = store.create_page("owner", title="격리 공유 메뉴 검증")["page"]
    store.share_page("owner", shared["id"], actor, role="view")
    shared_before = store.get_page("owner", shared["id"])
    monkeypatch.setattr(routes, "current_user_id", lambda: actor)
    app = Flask(__name__)
    app.config.update(TESTING=True)
    routes.register_personal_workspace_routes(app)
    client = app.test_client()
    requests, errors = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def proxy_appearance(route):
        request = route.request
        address = urlsplit(request.url)
        target = address.path + ("?" + address.query if address.query else "")
        response = client.open(target, method=request.method,
            data=request.post_data, content_type=request.headers.get("content-type"))
        requests.append((request.method, address.path, response.status_code))
        route.fulfill(status=response.status_code, content_type=response.mimetype,
            body=response.get_data())

    page.route("**/api/notion/personal-workspaces**", proxy_appearance)
    own_before = page.evaluate("() => JSON.stringify({id:State.activePageId,page:State.pageData.page,blocks:State.pageData.blocks})")
    page.evaluate("""async shared => {
      State.pages.push({...shared,scope:'shared',can_edit:false,can_share:false});
      State.users.push({id:'owner',display_name:'owner'});
      await PersonalWorkspaceAppearance.refresh();
    }""", shared)
    page.set_viewport_size({"width":873,"height":836})
    switcher = page.locator("#workspaceName")
    menu = page.locator("#workspacePopover")
    switcher.press("Enter")
    expect(menu).to_be_visible()
    settings = menu.locator('[data-workspace-action="settings"]')
    expect(settings).to_be_focused()
    assert menu.evaluate("""root => [...root.firstElementChild.children].map(node =>
      node.classList.contains('workspace-popover-head') ? 'header'
      : node.classList.contains('workspace-menu-primary') ? 'primary'
      : node.classList.contains('workspace-account') ? 'account'
      : node.hasAttribute('data-owner') ? 'workspace'
      : node.classList.contains('workspace-menu-utilities') ? 'utilities' : 'logout')""") == [
        "header", "primary", "account", "workspace", "workspace", "utilities", "logout"]
    assert menu.locator('.workspace-menu-primary [data-workspace-action="settings"]').count() == 1
    page.evaluate("() => PersonalWorkspaceAppearance.refresh()")
    expect(settings).to_be_focused()
    artifact_dir = os.environ.get("NOTION_WORKSPACE_MENU_ARTIFACT_DIR")
    if artifact_dir:
        target = Path(artifact_dir)
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / "personal-menu-light-873.png"))
    settings.click()
    expect(menu).to_be_hidden()
    expect(page.locator("#personalWorkspaceIconBtn")).to_be_enabled()
    expect(page.locator('.personal-settings-nav strong')).to_have_text("UI1 Owner의 Notion")
    page.keyboard.press("Escape")
    expect(page.locator("#modalBg")).to_be_hidden()
    expect(switcher).to_be_focused()

    switcher.press("Enter")
    menu.locator('[data-owner="owner"]').click()
    page.wait_for_function("() => State.workspaceOwnerFilter === 'owner'")
    switcher.press("Enter")
    expect(menu.locator('.workspace-popover-head strong')).to_have_text("owner의 Notion")
    expect(menu.locator('[data-owner="owner"]')).to_contain_text("게스트")
    page.set_viewport_size({"width":390,"height":700})
    if not page.evaluate("() => document.body.classList.contains('mobile-sidebar-open')"):
        page.locator("#sidebarRestoreBtn").click()
    if switcher.get_attribute("aria-expanded") != "true":
        switcher.click()
    page.wait_for_function("() => {const r=document.querySelector('#workspacePopover').getBoundingClientRect();return r.width>0&&r.left>=0&&r.right<=innerWidth}")
    if artifact_dir:
        page.screenshot(path=str(target / "personal-guest-menu-light-390.png"))
    settings.click()
    expect(page.locator("#personalWorkspaceIconBtn")).to_be_disabled()
    expect(page.locator('.personal-settings-nav strong')).to_have_text("owner의 Notion")
    expect(page.locator('.personal-settings-row').first).to_contain_text("워크스페이스 소유자가 변경할 수 있습니다.")
    page.keyboard.press("Escape")
    expect(page.locator("#modalBg")).to_be_hidden()
    expect(switcher).to_be_focused()
    assert page.evaluate("() => JSON.stringify({id:State.activePageId,page:State.pageData.page,blocks:State.pageData.blocks})") == own_before
    assert store.get_page("owner", shared["id"]) == shared_before
    assert requests and all(method == "GET" and status == 200 for method, _path, status in requests), requests
    assert not errors, errors
