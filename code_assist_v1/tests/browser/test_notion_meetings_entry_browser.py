"""UI-SHELL-003: full meeting navigation preserves its current page scope."""

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page


@pytest.mark.integration
@pytest.mark.regression
def test_ui_shell_003_meetings_full_view_opens_from_a_page(document_page):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.locator("#sidebarMeetingsNavBtn").click()
    expect(page.locator("#sidebarMeetingsPanel")).to_be_visible()
    page.locator("#sidebarMeetingsOpenBtn").click()
    expect(page.locator("#meetingsView")).to_be_visible(timeout=30_000)
    expect(page.locator("#pageView")).to_be_hidden()
    assert "view=meetings" in page.url
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_ui_shell_003_meetings_entry_cancel_keeps_current_page(document_page):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.locator("#sidebarMeetingsNavBtn").click()
    page_id = page.evaluate("() => State.activePageId")
    page.evaluate("""() => {
      const gate=()=>new Promise(resolve=>{
        window.__finishMeetingEntry=()=>resolve(true);
      });
      window.NotionAiNotes={...window.NotionAiNotes,beforeNavigate:gate};
    }""")
    page.locator("#sidebarMeetingsOpenBtn").click()
    page.wait_for_function("() => typeof window.__finishMeetingEntry === 'function'", timeout=10_000)
    page.evaluate("""() => {
      window.NotionMeetings.leave();
      window.__finishMeetingEntry();
    }""")
    expect(page.locator("#meetingsView")).to_be_hidden()
    expect(page.locator("#pageView")).to_be_visible()
    assert page.evaluate("() => State.activePageId") == page_id
    assert "view=meetings" not in page.url
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_ui_shell_003_meetings_entry_ignores_changed_workspace(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.locator("#sidebarMeetingsNavBtn").click()
    page_id = page.evaluate("() => State.activePageId")
    page.evaluate("""() => {
      window.NotionAiNotes={...window.NotionAiNotes,beforeNavigate:()=>new Promise(resolve=>{
        window.__finishMeetingWorkspaceEntry=()=>resolve(true);
      })};
    }""")
    page.locator("#sidebarMeetingsOpenBtn").click()
    page.wait_for_function("() => typeof window.__finishMeetingWorkspaceEntry === 'function'")
    page.evaluate("""() => {
      State.navigation.workspaceId='different-workspace';
      window.__finishMeetingWorkspaceEntry();
    }""")
    expect(page.locator("#meetingsView")).to_be_hidden()
    expect(page.locator("#pageView")).to_be_visible()
    assert page.evaluate("() => State.activePageId") == page_id
    assert "view=meetings" not in page.url
