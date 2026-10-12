"""R3/R9: keyboard focus remains usable when narrow sidebar becomes a dialog."""

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page


@pytest.mark.integration
@pytest.mark.regression
def test_mobile_sidebar_traps_focus_and_restores_desktop_navigation(document_page):
    from playwright.sync_api import expect

    page = document_page
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": 390, "height": 700})
    restore = page.locator("#sidebarRestoreBtn")
    sidebar = page.locator(".sidebar")
    main = page.locator("#workspaceMain")
    expect(restore).to_be_visible()
    restore.click()
    expect(sidebar).to_be_visible()
    expect(sidebar).to_have_attribute("role", "dialog")
    expect(sidebar).to_have_attribute("aria-modal", "true")
    assert main.evaluate("element => element.inert")
    expect(page.locator("#workspaceName")).to_be_focused()

    last = page.locator("#sidebarNewBtn")
    last.focus()
    page.keyboard.press("Tab")
    first = page.locator("#workspaceSidebarGroup")
    expect(first).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(last).to_be_focused()

    page.keyboard.press("Escape")
    expect(sidebar).to_be_hidden()
    expect(restore).to_be_focused()
    assert not main.evaluate("element => element.inert")

    restore.click()
    expect(sidebar).to_be_visible()
    page.set_viewport_size({"width": 900, "height": 700})
    expect(sidebar).to_be_visible()
    expect(sidebar).not_to_have_attribute("role", "dialog")
    assert not main.evaluate("element => element.inert")
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_mobile_page_navigation_focuses_content_after_sidebar_closes(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({"width": 390, "height": 700})
    page.locator("#sidebarRestoreBtn").click()
    row = page.locator(".sidebar .page-row.active").first
    expect(row).to_be_visible()
    row.click()
    expect(page.locator(".sidebar")).to_be_hidden()
    expect(page.locator("#pageView")).to_be_visible()
    page.wait_for_function("() => !document.body.classList.contains('mobile-sidebar-open')")
    active = page.evaluate("""() => ({
      id: document.activeElement?.id,
      inMain: document.querySelector('#workspaceMain').contains(document.activeElement),
    })""")
    assert active["inMain"], active

    page.locator("#sidebarRestoreBtn").click()
    page.locator("#workspaceHomeBtn").click()
    expect(page.locator(".sidebar")).to_be_hidden()
    expect(page.locator("#homeView")).to_be_focused()
    expect(page.locator("#blocks .block-row")).to_have_count(0)
    expect(page.locator("#pageTitle")).to_have_value("")
