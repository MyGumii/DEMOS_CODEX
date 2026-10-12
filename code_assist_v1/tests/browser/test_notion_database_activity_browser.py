"""UI-R5: authenticated sidebar database activity in an isolated 2421 app."""
from __future__ import annotations

import os

import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app,
)


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_sidebar_database_menu_shows_database_editor_after_reload(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        pytest.skip("exact selected gateway runtime is unavailable")

    with _isolated_collaborative_app(tmp_path, locked=False,
            owned_database_copy=True, database_activity=True) as (
            origin, password, authority, _workspace_id, page_id):
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
                page.wait_for_function("() => typeof State !== 'undefined' "
                    "&& State.liveCollaboration.crdtReady", timeout=30_000)
                block_id = page.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return State.pageData.blocks.find(block => block.type === 'table')?.id || '';
                }""")
                assert block_id
                page.wait_for_function("""id => State.pageData.blocks.some(block => block.id === id)
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""", arg=block_id, timeout=30_000)
                page.reload(wait_until="domcontentloaded")
                row = page.locator(f'.sidebar-database-row[data-sidebar-db-block="{block_id}"]').first
                expect(row).to_be_visible(timeout=30_000)
                page.locator(f'[data-block-id="{block_id}"] [data-database-add]').click()
                page.wait_for_function("""id => State.pageData?.databases?.some(database =>
                  database.id === id && database.rows?.length === 1) && !State.dirty
                  && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive""",
                  arg=block_id, timeout=30_000)

                current = authority.read_document("schema-editor", page_id)
                authority.execute_schema_command("schema-editor", page_id,
                    database_id=block_id, command_kind="database.property.create",
                    target_id="activity-browser-property",
                    payload={"name": "Activity", "type": "text", "config": {}},
                    operation_id="activity-browser-schema",
                    expected_document_sequence=current["document_sequence"],
                    expected_workspace_epoch=current["workspace_epoch"],
                    expected_document_epoch=current["document_epoch"])
                assert authority.read_database("owner", block_id)["last_edit"]["actor_subject_id"] == "schema-editor"
                page.wait_for_function("""id => State.pageData?.databases?.find(database =>
                  database.id === id)?.last_edit?.actor_subject_id === 'schema-editor'""",
                  arg=block_id, timeout=30_000)
                row = page.locator(f'.sidebar-database-row[data-sidebar-db-block="{block_id}"]').first
                expect(row).to_be_visible(timeout=30_000)
                row.hover()
                row.locator("[data-sidebar-db-menu]").click()
                footer = page.locator("#pageMenu .menu-foot")
                expect(footer).to_contain_text("schema-editor 최종 편집")
                assert footer.inner_text().splitlines()[-1] != "-"

                current = authority.read_document("owner", page_id)
                authority.execute_typed_command("owner", page_id,
                    command_kind="block.insert", target_id="activity-browser-paragraph",
                    payload={"type": "paragraph", "content": "unrelated", "after_block_id": None},
                    operation_id="activity-browser-unrelated",
                    expected_document_sequence=current["document_sequence"],
                    expected_workspace_epoch=current["workspace_epoch"],
                    expected_document_epoch=current["document_epoch"])
                page.wait_for_function("""id => State.liveCollaboration.liveCursor >= id""",
                    arg=current["document_sequence"] + 1, timeout=30_000)
                expect(page.locator("#pageMenu .menu-foot")).to_contain_text("schema-editor 최종 편집")
                page.reload(wait_until="domcontentloaded")
                row = page.locator(f'.sidebar-database-row[data-sidebar-db-block="{block_id}"]').first
                expect(row).to_be_visible(timeout=30_000)
                row.hover()
                row.locator("[data-sidebar-db-menu]").click()
                expect(page.locator("#pageMenu .menu-foot")).to_contain_text("schema-editor 최종 편집")
                page.keyboard.press('Escape')
                row.click()
                page.wait_for_function("() => document.querySelector('#pageView').classList.contains('database-full-page')")
                edit_time = page.locator('#pageEditTime')
                expect(edit_time).to_be_hidden()
                assert edit_time.get_attribute('title').startswith('schema-editor 최종 편집 · ')
                page.set_viewport_size({'width':390,'height':700})
                expect(edit_time).to_be_hidden()
                assert page.evaluate("""() => {
                  const top=document.querySelector('.topbar');
                  const more=document.querySelector('#pageMoreBtn').getBoundingClientRect();
                  return more.right <= innerWidth && top.scrollWidth <= top.clientWidth + 1;
                }""")
                before_share = authority.read_document('owner', page_id)['document_sequence']
                expect(page.locator('#shareDrawerBtn')).to_be_visible()
                page.locator('#shareDrawerBtn').click()
                share_dialog = page.locator('#archivedDatabaseSharePopover')
                expect(share_dialog).to_be_visible()
                expect(share_dialog.locator('#databaseShareScopeNote')).to_contain_text(
                    '부모 페이지의 접근 권한을 따릅니다')
                expect(share_dialog.locator('#archivedDatabaseShareAccess')).to_contain_text('내 권한')
                expect(share_dialog.locator('#databaseParentShareBtn')).to_be_visible()
                share_bounds = share_dialog.bounding_box()
                assert share_bounds and share_bounds['x'] >= 0
                assert share_bounds['x'] + share_bounds['width'] <= 390
                if os.environ.get('NOTION_DATABASE_SHARE_SCREENSHOT'):
                    page.screenshot(path=os.environ['NOTION_DATABASE_SHARE_SCREENSHOT'])
                share_dialog.locator('#databaseParentShareBtn').click()
                expect(page.locator('#inspector')).to_be_visible()
                expect(page.locator('#drawerTitle')).to_have_text('부모 페이지 공유')
                expect(page.locator('#pageInheritedShareHeading')).to_have_text('부모 페이지 공유')
                expect(page.locator('#databaseParentShareNotice')).to_be_visible()
                parent_share_bounds = page.locator('#inspector').bounding_box()
                assert parent_share_bounds and parent_share_bounds['x'] >= 0
                assert parent_share_bounds['x'] + parent_share_bounds['width'] <= 390
                if os.environ.get('NOTION_DATABASE_PARENT_SHARE_SCREENSHOT'):
                    page.screenshot(path=os.environ['NOTION_DATABASE_PARENT_SHARE_SCREENSHOT'])
                assert authority.read_document('owner', page_id)['document_sequence'] == before_share
                page.keyboard.press('Escape')
                expect(page.locator('#inspector')).to_be_hidden()
                page.locator('#pageMoreBtn').click()
                page.locator('#pageMenu [data-page-action="updates"]').click()
                updates_panel = page.locator('#databaseUpdatesPanel')
                expect(updates_panel).to_be_visible()
                expect(page.locator('#drawerTitle')).to_have_text('업데이트')
                expect(page.locator('#databaseUpdatesList')).to_contain_text('schema-editor')
                expect(page.locator('#databaseUpdatesList')).to_contain_text('속성을 변경했습니다.')
                assert 'unrelated' not in page.locator('#databaseUpdatesList').inner_text()
                updates_bounds = page.locator('#inspector').bounding_box()
                assert updates_bounds and updates_bounds['x'] >= 0
                assert updates_bounds['x'] + updates_bounds['width'] <= 390
                assert abs(updates_bounds['y'] - 45) <= 1
                assert abs(updates_bounds['width'] - 384) <= 1
                if os.environ.get('NOTION_DATABASE_UPDATES_SCREENSHOT'):
                    page.screenshot(path=os.environ['NOTION_DATABASE_UPDATES_SCREENSHOT'])
                assert authority.read_document('owner', page_id)['document_sequence'] == before_share
                page.keyboard.press('Escape')
                expect(page.locator('#inspector')).to_be_hidden()
                page.locator('#pageMoreBtn').click()
                updates_action = page.locator('#pageMenu [data-page-action="updates"]')
                expect(updates_action).to_be_enabled()
                updates_action.click()
                expect(updates_panel).to_be_visible()
                expect(page.locator('#databaseUpdatesList')).to_contain_text('schema-editor')
                page.keyboard.press('Escape')
                expect(page.locator('#inspector')).to_be_hidden()
                expect(page.locator('#pageMoreBtn')).to_be_focused()
                page.locator('#pageMoreBtn').click()
                page.locator('#pageMenu [data-page-action="updates"]').click()
                expect(updates_panel).to_be_visible()
                row_update = page.locator('#databaseUpdatesList [data-database-update-row]').first
                expect(row_update).to_be_visible()
                assert row_update.inner_text().strip()
                row_update.click()
                expect(page.locator('#inspector')).to_be_hidden()
                expect(page.locator('#linkedItemPeek')).to_be_visible(timeout=30_000)
                page.locator('#linkedItemPeek [data-linked-item-close]').click()
                expect(page.locator('#linkedItemPeek')).to_be_hidden()
                current = authority.read_document('owner', page_id)
                authority.execute_schema_command('owner', page_id,
                    database_id=block_id, command_kind='database.property.create',
                    target_id='activity-browser-live-property',
                    payload={'name':'Live activity','type':'text','config':{}},
                    operation_id='activity-browser-live-schema',
                    expected_document_sequence=current['document_sequence'],
                    expected_workspace_epoch=current['workspace_epoch'],
                    expected_document_epoch=current['document_epoch'])
                try:
                    page.wait_for_function("""id => State.pageData?.databases?.find(database =>
                      database.id === id)?.last_edit?.actor_subject_id === 'owner'
                      && document.querySelector('#pageEditTime')?.title.startsWith(
                        `${State.user?.display_name || State.user?.id} 최종 편집 · `)
                    """, arg=block_id, timeout=15_000)
                except Exception as exc:
                    state = page.evaluate("""id => ({
                      activePage:State.activePageId,fullDatabase:State.databaseFullPageBlockId,
                      database:State.pageData?.databases?.find(item=>item.id===id)?.last_edit,
                      editTime:document.querySelector('#pageEditTime')?.title,
                      liveCursor:State.liveCollaboration?.liveCursor,
                      durableCursor:State.liveCollaboration?.durableCursor,
                      status:document.querySelector('#status')?.textContent,
                    })""", block_id)
                    raise AssertionError(state) from exc
                if os.environ.get('NOTION_DATABASE_TOPBAR_SCREENSHOT'):
                    page.screenshot(path=os.environ['NOTION_DATABASE_TOPBAR_SCREENSHOT'])
                assert not errors, errors
            finally:
                browser.close()
