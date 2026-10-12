"""The isolated browser can create, reuse, and persist a workspace emoji."""

import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app,
)
from code_assist_v1.tests.notion_collaboration.test_workspace_images import png


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
def test_ui_custom_emoji_from_empty_search_persists_as_page_icon(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(
        tmp_path, locked=False, owned_database_copy=True,
        database_activity=True, owned_database_trash=True,
        relation_pair=True, custom_emoji=True,
    ) as (origin, password, authority, workspace_id, page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1280, "height": 800})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                page.wait_for_function(
                    "() => typeof State !== 'undefined' && State.pageData?.page?.workspace_id && State.pageData?.page?.id",
                    timeout=30_000,
                )
                assert page.evaluate("() => State.pageData.page.workspace_id") == workspace_id

                trigger = page.locator("#addIconBtn")
                if not trigger.is_visible():
                    trigger = page.locator("#pageIconBtn")
                trigger.click()
                picker = page.locator("#pageIconPicker")
                expect(picker).to_be_visible(timeout=30_000)
                picker.locator('[data-icon-tab="emoji"]').click()
                picker.locator('input[type="search"]').fill("testcustomemojinotfound")
                picker.locator("[data-icon-custom-add]").click()
                expect(picker.locator(".icon-picker-custom-form")).to_be_visible()
                picker.locator("[data-icon-custom-file]").set_input_files({
                    "name": "team.png", "mimeType": "image/png", "buffer": png(),
                })
                picker.locator("[data-icon-custom-name]").fill("Team")
                picker.locator("[data-icon-custom-save]").click()
                option = picker.locator('[data-icon-value^="ne:we_"]')
                expect(option).to_be_visible(timeout=30_000)
                icon = option.get_attribute("data-icon-value")
                assert icon and icon.startswith("ne:we_")
                option.click()
                expect(picker).to_be_hidden(timeout=30_000)
                page.wait_for_function("expected => State.pageData?.page?.icon === expected", arg=icon,
                                       timeout=30_000)
                assert page.locator("#pageIconBtn img.notion-emoji-image").count() == 1

                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                page.wait_for_function("expected => State.pageData?.page?.icon === expected", arg=icon,
                                       timeout=30_000)
                expect(page.locator("#pageIconBtn img.notion-emoji-image")).to_be_visible()
                page.wait_for_function("() => State.liveCollaboration?.crdtReady && State.navigation?.collaborativeSharing",
                                       timeout=30_000)

                block_id = page.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return State.pageData.blocks.find(block => block.type === 'table')?.id || '';
                }""")
                assert block_id
                page.wait_for_function("""id => State.pageData.blocks.some(block => block.id === id)
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""", arg=block_id, timeout=30_000)
                page.reload(wait_until="domcontentloaded")
                expect(page.locator(f'[data-block-id="{block_id}"] [data-database-add]')).to_be_visible(timeout=30_000)
                page.locator(f'[data-block-id="{block_id}"] [data-database-add]').click()
                page.wait_for_function("""id => State.pageData?.databases?.find(database =>
                  database.id === id)?.rows?.length === 1 && !State.dirty
                  && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive""",
                  arg=block_id, timeout=30_000)
                row_id = authority.read_database("owner", block_id)["rows"][0]["id"]
                page.evaluate("""async ({blockId,rowId}) => {
                  const scope={...linkedDatabaseScope(),itemKind:'owned'};
                  const block=State.pageData.blocks.find(item=>item.id===blockId);
                  await linkedItemController().open(scope,blockId,block,ownedItemData(block),rowId,
                    document.querySelector(`[data-block-id="${blockId}"]`));
                }""", {"blockId": block_id, "rowId": row_id})
                item_icon = page.locator('#linkedItemPeek [data-item-appearance="icon"]')
                expect(item_icon).to_be_visible(timeout=30_000)
                item_icon.click()
                item_picker = page.locator('#itemIconPicker')
                expect(item_picker).to_be_visible(timeout=30_000)
                item_picker.locator('[data-icon-tab="emoji"]').click()
                item_picker.locator('input[type="search"]').fill('Team')
                item_option = item_picker.locator(f'[data-icon-value="{icon}"]')
                expect(item_option).to_be_visible(timeout=30_000)
                item_option.click()
                page.wait_for_function("""({databaseId,rowId,icon}) => {
                  const database=State.pageData?.databases?.find(item=>item.id===databaseId);
                  return database?.rows?.find(row=>row.id===rowId)?.appearance?.icon===icon;
                }""", arg={"databaseId": block_id, "rowId": row_id, "icon": icon}, timeout=30_000)
                assert authority.read_database("owner", block_id)["rows"][0]["appearance"]["icon"] == icon
                assert not errors, errors
            finally:
                browser.close()
