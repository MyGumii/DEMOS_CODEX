"""R2: collaborator topbar remains usable when editing access is absent."""

import os
import json

import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app,
)


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('role', ['view', 'comment'])
def test_viewer_topbar_keeps_actions_in_view_at_narrow_widths(tmp_path, role):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        pytest.skip("exact selected gateway runtime is unavailable")

    with _isolated_collaborative_app(tmp_path, locked=False) as (
        origin, password, authority, _workspace_id, page_id,
    ):
        witness = authority.read_document("owner", page_id)
        authority.set_page_grant(
            "owner", page_id, "subject", "content-editor", role=role,
            inherit_to_descendants=False,
            expected_workspace_epoch=witness["workspace_epoch"],
            expected_document_epoch=witness["document_epoch"],
            operation_id=f"viewer-topbar-grant-{role}",
        )
        document_before = authority.read_document('owner', page_id)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1280, "height": 800})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("content-editor")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready
                _wait_for_notion_ready(page)
                expect(page.locator("#pageTitle")).to_be_disabled(timeout=30_000)
                page.wait_for_function("""() => State.liveCollaboration?.crdtReady
                  && State.pageData?.page?.id === State.activePageId
                  && !document.querySelector('#pageView')?.textContent?.includes('페이지를 불러오는 중...')
                """, timeout=30_000)

                from itertools import product
                observations = []
                for theme, width in product(('light', 'dark'), (1440, 1420, 1419, 1280, 800, 620, 600, 390)):
                    page.evaluate('theme => applyColorTheme(theme, false)', theme)
                    page.set_viewport_size({"width": width, "height": 700})
                    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                    expect(page.locator("#pageTitle")).to_be_visible()
                    expect(page.locator("#pageMoreBtn")).to_be_visible()
                    expect(page.locator("#shareDrawerBtn")).to_be_visible()
                    expect(page.locator("#blocks .block-row.read-only").first).to_be_visible()
                    expect(page.locator("#blocks .block-row .block-controls").first).to_be_hidden()
                    appearance = page.evaluate("""() => {
                      const title=document.querySelector('#pageTitle');
                      const editor=document.querySelector('#blocks .block-editor');
                      return {role:document.body.dataset.pageIamRole,
                        titleColor:getComputedStyle(title).color,
                        titleFill:getComputedStyle(title).webkitTextFillColor,
                        titleOpacity:getComputedStyle(title).opacity,
                        bodyColor:getComputedStyle(document.body).color,
                        placeholder:getComputedStyle(editor, '::before').content};
                    }""")
                    assert appearance["titleFill"] == appearance["bodyColor"], appearance
                    assert appearance["titleColor"] == appearance["bodyColor"], appearance
                    assert appearance["titleOpacity"] == "1", appearance
                    assert appearance["placeholder"] == "none", appearance
                    page.wait_for_function("""() => {
                      const bar=document.querySelector('.topbar');
                      const context=document.querySelector('.topbar-page-context');
                      const actions=document.querySelector('.top-actions');
                      return bar && context && actions
                        && bar.scrollWidth <= bar.clientWidth + 1
                        && context.getBoundingClientRect().right <= actions.getBoundingClientRect().left + 1;
                    }""", timeout=5_000)
                    geometry = page.evaluate("""() => {
                      const rect = selector => {
                        const box=document.querySelector(selector).getBoundingClientRect();
                        return {left:box.left,right:box.right};
                      };
                      return {actions:rect('.top-actions'),more:rect('#pageMoreBtn'),
                        share:rect('#shareDrawerBtn'),topbarWidth:
                        document.querySelector('.topbar').getBoundingClientRect().width,editTimeVisible:
                        document.querySelector('#pageEditTime').getBoundingClientRect().width>0};
                    }""")
                    assert geometry["more"]["right"] <= width, geometry
                    assert geometry["share"]["right"] <= width, geometry
                    assert geometry["actions"]["left"] >= 0, geometry
                    assert geometry["editTimeVisible"] == (geometry["topbarWidth"] >= 1150), geometry
                    observations.append({"theme": theme, "width": width, **geometry})
                    screenshot_dir = os.environ.get("NOTION_VIEWER_TOPBAR_ARTIFACT_DIR")
                    if screenshot_dir and width in (1440, 1280, 390):
                        from pathlib import Path
                        output = Path(screenshot_dir)
                        output.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(output / f"{role}-topbar-{theme}-{width}.png"))
                        assert page.url.endswith("/page/" + page_id), page.url
                        expect(page.locator("#pageTitle")).to_be_visible()
                if os.environ.get("NOTION_VIEWER_TOPBAR_ARTIFACT_DIR"):
                    (output / f"{role}-topbar-observations.json").write_text(
                        json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8")

                page.locator("#pageMoreBtn").click()
                expect(page.locator("#pageMenu")).to_be_visible()
                menu = page.locator('#pageMenu')
                for action in ('rename', 'duplicate', 'move', 'trash', 'import', 'lock', 'full_width', 'small_text'):
                    expect(menu.locator(f'[data-page-action="{action}"]')).to_have_count(0)
                expect(menu.locator('[data-page-action="copy_link"]')).to_be_visible()
                page.keyboard.press("Escape")
                expect(page.locator("#pageMenu")).to_be_hidden()
                expect(page.locator("#pageMoreBtn")).to_be_focused()
                for action, panel in [('context', '#contextPanel'), ('document_ai', '#aiPanel'), ('files', '#filesPanel')]:
                    page.locator('#pageMoreBtn').click()
                    menu.locator(f'[data-page-action="{action}"]').click()
                    expect(page.locator(panel)).to_be_visible()
                    assert page.evaluate('State.activePageId') == page_id
                    page.keyboard.press('Escape')
                    expect(page.locator('#inspector')).to_be_hidden()
                    expect(page.locator('#pageMoreBtn')).to_be_focused()
                page.set_viewport_size({'width': 1280, 'height': 800})
                row = page.locator(f'#sharedPages .page-row[data-page-id="{page_id}"]').first
                expect(row).to_be_visible()
                row.hover()
                expect(row.locator('[data-page-child]')).to_have_count(0)
                expect(row.locator('[data-page-icon-picker]')).to_have_count(0)
                expect(row).to_have_attribute('data-row-action-count', '1')
                row.locator('[data-page-menu]').click()
                expect(menu).to_be_visible()
                for action in ('rename', 'duplicate', 'move', 'trash', 'import', 'lock'):
                    expect(menu.locator(f'[data-page-action="{action}"]')).to_have_count(0)
                page.keyboard.press('Escape')
                expect(menu).to_be_hidden()
                expect(row.locator('[data-page-menu]')).to_be_focused()
                after = authority.read_document('owner', page_id)
                for field in ('title', 'icon', 'blocks', 'document_sequence'):
                    assert after[field] == document_before[field]
                assert not errors, errors
            finally:
                browser.close()
