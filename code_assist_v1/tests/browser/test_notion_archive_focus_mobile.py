"""Archive focus must stay on a visible control after the page closes."""

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page


@pytest.mark.integration
@pytest.mark.regression
def test_mobile_page_archive_restores_visible_focus(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({"width": 390, "height": 800})
    page.wait_for_function("() => innerWidth === 390")
    trigger = page.locator("#pageMoreBtn")
    expect(trigger).to_be_visible()
    trigger.click()
    archive = page.locator('#pageMenu [data-page-action="trash"]')
    expect(archive).to_be_visible()
    page.once("dialog", lambda dialog: dialog.accept())
    archive.click()

    expect(page.locator("#pageView")).to_be_hidden(timeout=20_000)
    page.wait_for_url("**/workspace")
    focused = page.evaluate("""() => {
      const element = document.activeElement;
      return {id: element?.id || '', tag: element?.tagName, markup: element?.outerHTML?.slice(0, 220),
        visible: Boolean(element?.getClientRects().length),
        hidden: Boolean(element?.hidden || element?.closest('[hidden],[inert]'))};
    }""")
    assert focused["visible"] is True and focused["hidden"] is False, focused
    assert focused["id"] == "sidebarRestoreBtn", focused
    page.keyboard.press("Enter")
    assert page.evaluate("() => document.body.classList.contains('mobile-sidebar-open')")
    expect(trigger).to_be_hidden()
