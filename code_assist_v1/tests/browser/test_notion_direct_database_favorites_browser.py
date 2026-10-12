"""UI-R2/R10: a directly shared table can be favorited without its parent."""
import os
import pytest

from code_assist_v1.notion_collaboration import database_access
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app,
)


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r2_r10_direct_database_favorite_reload_and_revoke(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(
        tmp_path, locked=False, gateway_enabled=False, navigation_ui=True,
        owned_database_copy=True, database_activity=True,
        owned_database_trash=True, relation_pair=True, custom_emoji=True,
        item_page_layout=True, template_default=True,
        staged_database_sharing=True, direct_database_favorites=True,
    ) as (origin, password, authority, workspace_id, _page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                context = browser.new_context(locale="ko-KR", viewport={"width": 1280,
                                                                        "height": 800})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                base = f"/api/notion/workspaces/{workspace_id}"
                page.goto(origin + f"/database/{workspace_id}/database-e1",
                          wait_until="domcontentloaded")
                page.locator("#userId").fill("db-viewer")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=30_000)
                expect(page.locator("#favoritePageBtn")).to_be_visible()
                expect(page.locator("#favoritePageBtn")).to_be_enabled()
                expect(page.locator("#favoritePageBtn")).to_have_attribute(
                    "aria-pressed", "false")
                other = context.new_page()
                other.goto(origin + f"/database/{workspace_id}/database-e1",
                           wait_until="domcontentloaded")
                expect(other.locator("#independentDatabaseView")).to_be_visible(
                    timeout=30_000)
                expect(other.locator("#favoritePageBtn")).to_have_attribute(
                    "aria-pressed", "false")
                with page.expect_response(lambda response:
                        response.url.endswith("/databases/database-e1/favorite")
                        and response.request.method == "PUT") as saved:
                    page.locator("#favoritePageBtn").click()
                assert saved.value.status == 200, saved.value.text()
                assert "page-e1" not in saved.value.text()
                expect(page.locator("#favoritePageBtn")).to_have_attribute(
                    "aria-pressed", "true")
                other.bring_to_front()
                expect(other.locator("#favoritePageBtn")).to_have_attribute(
                    "aria-pressed", "true", timeout=15_000)
                favorite = page.locator(
                    "#favoritePages [data-direct-favorite][data-direct-db-id='database-e1']")
                expect(favorite).to_be_visible(timeout=15_000)
                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=30_000)
                expect(page.locator("#favoritePageBtn")).to_have_attribute(
                    "aria-pressed", "true", timeout=15_000)
                expect(favorite).to_be_visible()
                assert page.locator("#independentDatabaseView").evaluate(
                    "element => getComputedStyle(element).outlineStyle"
                ) == "none"
                if os.environ.get("NOTION_DIRECT_FAVORITE_SCREENSHOT"):
                    page.screenshot(path=os.environ["NOTION_DIRECT_FAVORITE_SCREENSHOT"])
                page.locator("#independentDatabaseView [data-db-close-full-page]").click()
                expect(page.locator("#homeView")).to_be_visible()
                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#homeView")).to_be_visible(timeout=30_000)
                expect(favorite).to_be_visible(timeout=15_000)
                favorite.click()
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=30_000)
                with page.expect_response(lambda response:
                        response.url.endswith("/databases/database-e1/favorite")
                        and response.request.method == "DELETE") as removed:
                    page.locator("#favoritePageBtn").click()
                assert removed.value.status == 200, removed.value.text()
                expect(page.locator("#favoritePageBtn")).to_have_attribute(
                    "aria-pressed", "false")
                expect(favorite).to_have_count(0)
                expect(other.locator("#favoritePageBtn")).to_have_attribute(
                    "aria-pressed", "false", timeout=15_000)
                other.close()
                page.locator("#favoritePageBtn").click()
                expect(favorite).to_be_visible(timeout=15_000)
                owner = database_access.resolve(authority, "owner", workspace_id,
                                                "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role=None,
                    expected_workspace_epoch=owner.workspace_epoch,
                    expected_document_epoch=owner.document_epoch,
                    expected_database_epoch=owner.database_epoch,
                    operation_id="browser-direct-favorite-revoke",
                )
                denied = page.request.get(origin + base +
                    "/database-favorites?access_database_id=database-e1")
                assert denied.status == 404
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(page.locator("#independentDatabaseView")).to_be_hidden(
                    timeout=15_000)
                expect(favorite).to_have_count(0)
                assert page.url.endswith("/workspace")
                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_hidden(
                    timeout=30_000)
                expect(favorite).to_have_count(0)
                assert "page-e1" not in page.locator("#sharedPages").inner_text()
                assert not errors, errors
            finally:
                browser.close()
