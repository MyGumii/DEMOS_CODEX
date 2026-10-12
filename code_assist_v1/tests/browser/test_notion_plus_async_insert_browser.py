"""UI-R5: plus-menu async insertion against isolated canonical pages."""
from __future__ import annotations

import os

import pytest

from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.page_creation import create_page
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_plus_linked_database_replaces_saved_blank_and_survives_reload(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        pytest.skip("exact selected gateway runtime is unavailable")

    with _isolated_collaborative_app(tmp_path, locked=False) as (
            origin, password, authority, workspace_id, source_id):
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root,
            identity_directory=authority.identity_directory)
        created = create_page(authority, "owner", workspace_id,
            title="연결 보기 대상", parent_id="",
            base_version=store.get_workspace("owner", workspace_id)["version"],
            operation_id="browser-plus-linked-destination")
        destination_id = created["page"]["id"]

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1280, "height": 800})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + source_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                page.wait_for_function("() => typeof State !== 'undefined' "
                    "&& State.liveCollaboration.crdtReady && State.pageData.page.collaborative_databases",
                    timeout=30_000)
                database_id = page.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return State.pageData.blocks.find(block => block.type === 'table')?.id || '';
                }""")
                assert database_id
                page.wait_for_function("""id => State.pageData.databases?.some(db => db.id === id)
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""", arg=database_id, timeout=30_000)
                source_database_ids = page.evaluate('() => State.pageData.databases.map(db => db.id)')

                page.goto(origin + "/page/" + destination_id, wait_until="domcontentloaded")
                page.wait_for_function("() => typeof State !== 'undefined' "
                    "&& State.activePageId && State.liveCollaboration.crdtReady", timeout=30_000)
                page.evaluate("() => { if (!State.pageData.blocks.length) insertBlockAfter(-1, 'paragraph'); }")
                row = page.locator('#blocks .block-row').first
                expect(row).to_be_visible()
                row.hover()
                row.locator('[data-action="insert"]').click()
                menu = page.locator('#blockMenu')
                expect(menu).to_have_attribute('data-mode', 'inserted')
                blank_id = page.evaluate('() => State.insertedBlockMenu.blockId')
                page.wait_for_function("() => !State.dirty && !State.liveCollaboration.unsyncedCount "
                    "&& !State.liveCollaboration.sendQueueActive", timeout=30_000)
                expect(menu).to_be_visible()
                menu.locator('[data-insert-type="linked_database"]:visible').first.click()
                expect(page.locator('#linkedSourceSearch')).to_be_visible()
                option = page.locator('#linkedSourceResults .linked-source-option').filter(
                    has_text='Collaborative browser lock')
                expect(option).to_be_visible(timeout=30_000)
                option.click()
                view = page.locator('#linkedSourceResults .linked-source-option').filter(has_text='새 표 보기')
                expect(view).to_be_visible(timeout=30_000)
                view.click()
                page.wait_for_function("""args => State.pageData.blocks.some(block =>
                  block.id === args.blank && block.props?.linked_database?.document_id === args.source
                  && args.databases.includes(block.props.linked_database.database_id))
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""",
                  arg={"blank": blank_id, "source": source_id,
                       "databases": source_database_ids}, timeout=30_000)
                page.reload(wait_until='domcontentloaded')
                page.wait_for_function("""args => State.activePageId === args.destination
                  && State.liveCollaboration.crdtReady
                  && State.pageData.blocks.some(block => block.id === args.blank
                    && block.props?.linked_database?.document_id === args.source
                    && args.databases.includes(block.props.linked_database.database_id))""",
                    arg={"destination": destination_id, "blank": blank_id,
                         "source": source_id, "databases": source_database_ids}, timeout=30_000)
                assert not errors, errors
            finally:
                browser.close()
