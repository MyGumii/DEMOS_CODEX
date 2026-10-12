"""The new-page chooser attaches an existing source without making a copy."""

import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app


@pytest.mark.integration
@pytest.mark.regression
def test_new_page_can_link_an_existing_database_source(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(
        tmp_path, locked=False, owned_database_copy=True, navigation_ui=True,
    ) as (origin, password, authority, _workspace_id, initial_page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1280, "height": 800})
                errors, creation_responses = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("response", lambda response: creation_responses.append((response.status, response.text()))
                    if response.request.method == "POST" and response.url.endswith("/pages") else None)
                page.goto(origin + "/page/" + initial_page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' && State.navigation.pageCreationTemplates?.includes('linked_database')", timeout=30_000)
                page.goto(origin + "/page/" + initial_page_id, wait_until="domcontentloaded")
                page.wait_for_function("id => typeof State !== 'undefined' && State.activePageId === id && State.pageData", arg=initial_page_id, timeout=30_000)

                def open_database_chooser():
                    page.locator("#sidebarNewBtn").click()
                    page.locator('#newPageMenu [data-new-page-type="database"]').click()
                    expect(page.locator("#pageCreationChooser")).to_be_visible()

                open_database_chooser()
                page.locator('[data-create-template="database"]').click()
                page.wait_for_function("""initial => State.pageData && State.activePageId !== initial
                  && State.pageData.blocks.some(block => block.props?.database_id)
                  && !State.saving""", arg=initial_page_id, timeout=30_000)
                original = page.evaluate("""() => ({
                  pageId: State.activePageId,
                  databaseId: State.pageData.blocks.find(block => block.props?.database_id).props.database_id,
                })""")
                original_database = authority.read_database("owner", original["databaseId"])

                open_database_chooser()
                page.locator('[data-create-template="linked_database"]').click()
                expect(page.locator("#newPageSourceSearch")).to_be_visible()
                option = page.locator("#newPageSourceResults .linked-source-option").filter(has_text="새 데이터베이스").first
                expect(option).to_be_visible(timeout=20_000)
                option.click()
                try:
                    page.wait_for_function("""source => State.pageData && State.activePageId !== source.pageId
                      && State.pageData.blocks.some(block => block.props?.linked_database?.database_id === source.databaseId)
                      && !State.saving""", arg=original, timeout=30_000)
                except Exception:
                    pytest.fail(f"linked page did not open; responses={creation_responses}; "
                        f"state={page.evaluate('() => ({active:State.activePageId,pending:State.navigation.pendingPageCreate})')}")
                assert page.evaluate("""source => {
                  const block = State.pageData.blocks.find(item => item.props?.linked_database?.database_id === source.databaseId);
                  return block?.props.linked_database.document_id === source.pageId
                    && !block.props.rows && !block.props.properties;
                }""", original)
                expect(page.locator(".linked-database")).to_be_visible(timeout=20_000)
                assert authority.read_database("owner", original["databaseId"]) == original_database
                page.set_viewport_size({"width": 390, "height": 800})
                expect(page.locator(".linked-database")).to_be_visible()
                assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
                assert not errors, errors
            finally:
                browser.close()
