"""UI-R5: owned database Trash and restore from the isolated browser UI."""
from __future__ import annotations

import os
import zipfile

import pytest

from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app,
)


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_owned_database_trash_recovers_lost_reply_and_restores(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        pytest.skip("exact selected gateway runtime is unavailable")

    with _isolated_collaborative_app(tmp_path, locked=False,
            owned_database_copy=True, database_activity=True,
            owned_database_trash=True) as (origin, password, authority, workspace_id, page_id):
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
                page.wait_for_function("() => typeof State !== 'undefined' "
                    "&& State.navigation.collaborativeOwnedDatabaseTrash "
                    "&& State.liveCollaboration.crdtReady", timeout=30_000)
                block_id = page.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return State.pageData.blocks.find(block => block.type === 'table')?.id || '';
                }""")
                assert block_id
                page.wait_for_function("""id => State.pageData.blocks.some(block => block.id === id)
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""", arg=block_id, timeout=30_000)
                title_field = page.evaluate("""id => {
                  const block = State.pageData.blocks.find(block => block.id === id);
                  return databasePropertyId(block, 'title', 'title');
                }""", block_id)
                for row_index, title in enumerate(('Alpha', 'Zeta')):
                    page.evaluate("""async id => {
                      const index = State.pageData.blocks.findIndex(block => block.id === id);
                      await createCanonicalDatabaseItem(index, 'table');
                    }""", block_id)
                    title_input = page.locator(
                        f'[data-block-id="{block_id}"] '
                        f'[data-db-row="{row_index}"][data-db-field="{title_field}"]')
                    expect(title_input).to_be_visible(timeout=30_000)
                    title_input.fill(title)
                    title_input.press('Tab')
                    page.wait_for_function("""args => {
                      const database = State.pageData.databases?.find(item => item.id === args.id);
                      return database?.rows?.some(row => row.cells?.[args.field] === args.title);
                    }""", arg={'id': block_id, 'field': title_field, 'title': title}, timeout=30_000)
                page.evaluate("""async id => {
                  const block = State.pageData.blocks.find(item => item.id === id);
                  const rows = databaseRows(block);
                  const dateField = databasePropertyId(block, 'date', 'date');
                  rows[0][dateField] = localIsoDate(new Date());
                  syncActiveDatabaseView(block);
                  const base = structuredClone(activeDatabaseView(block));
                  for (const type of ['board', 'list', 'gallery', 'calendar', 'timeline']) {
                    block.props.views.push({...structuredClone(base),
                      id:`view_archived_${type}`, name:DATABASE_VIEW_LABELS[type], type});
                  }
                  markDirty(); renderBlocks();
                  await flushCollaborativeDatabaseSync();
                }""", block_id)
                page.wait_for_function("""id => {
                  const database = State.pageData.databases?.find(item => item.id === id);
                  return database?.views?.length === 6
                    && database.rows?.some(row => Boolean(row.cells?.date))
                    && !State.dirty && !State.liveCollaboration.unsyncedCount;
                }""", arg=block_id, timeout=30_000)
                page.reload(wait_until="domcontentloaded")
                row = page.locator(
                    f'.sidebar-database-row[data-sidebar-db-block="{block_id}"]'
                    ':not([data-sidebar-db-archived="true"])').first
                expect(row).to_be_visible(timeout=30_000)
                row.hover()
                row.locator('[data-sidebar-db-menu]').click()
                page.locator('#pageMenu [data-page-action="favorite"]').click()
                favorite_row = page.locator(
                    f'#favoritePages .sidebar-database-row[data-sidebar-db-block="{block_id}"]')
                expect(favorite_row).to_be_visible(timeout=30_000)
                posted = []

                def lose_reply(route):
                    response = route.fetch()
                    assert response.status == 201, response.text()
                    posted.append(response.json())
                    route.abort("failed")

                page.route(f'**/databases/{block_id}/trash', lose_reply)
                row.hover()
                row.locator('[data-sidebar-db-menu]').click()
                page.locator('#pageMenu [data-page-action="trash"]').click()
                expect(page.locator('#databaseTrashStatus')).to_contain_text(
                    '결과를 확인해야 합니다', timeout=30_000)
                assert len(posted) == 1
                assert posted[0]['database_id'] == block_id
                with pytest.raises(NotFoundOrDenied):
                    authority.read_database('owner', block_id)
                page.reload(wait_until="domcontentloaded")
                expect(page.locator('#databaseTrashStatus')).to_contain_text(
                    '결과를 확인해야 합니다', timeout=30_000)
                page.locator('#databaseTrashStatus button').filter(has_text='결과 확인').click()
                expect(page.locator('#databaseTrashStatus')).to_contain_text(
                    '휴지통으로 이동했습니다', timeout=30_000)
                navigation = page.request.get(
                    f'{origin}/api/notion/workspaces/{workspace_id}/navigation').json()
                visible_favorites = page.evaluate('() => State.navigation.snapshot?.favorites')
                assert visible_favorites == navigation['favorites'], (
                    visible_favorites, navigation['favorites'])
                assert any(item.get('database_block_id') == block_id
                    for item in navigation['favorites']), navigation['favorites']
                archived_title = next(item['archived_database']['title']
                    for item in navigation['favorites']
                    if item.get('database_block_id') == block_id)
                expect(row).to_have_count(0, timeout=30_000)
                expect(favorite_row).to_have_attribute('data-sidebar-db-archived', 'true')
                favorite_row.click()
                expect(page.locator('#archivedPageNotice')).to_be_visible(timeout=30_000)
                expect(page.locator('#databaseTrashStatus')).to_be_hidden()
                expect(page.locator('#workspaceToast')).to_be_hidden()
                expect(page.locator('#archivedPageTitle')).to_have_text(archived_title)
                expect(page.locator('#archivedPageMessage')).to_contain_text('휴지통으로 이동')
                expect(page.locator('#archivedDatabasePreview th').first).to_be_visible()
                preview_rows = page.locator('#archivedDatabasePreview tbody tr')
                expect(preview_rows).to_have_count(2)
                expect(preview_rows.first).to_contain_text('Alpha')
                expect(preview_rows.last).to_contain_text('Zeta')
                view_tabs = page.locator('[data-archived-db-view]')
                expect(view_tabs).to_have_count(6)
                for view, selector, count in (
                    ('board', '.archived-database-board article', 2),
                    ('list', '.archived-database-list [role="listitem"]', 2),
                    ('gallery', '.archived-database-gallery article', 2),
                    ('calendar', '.archived-database-calendar-grid p', 1),
                    ('timeline', '.archived-database-timeline-bar', 1),
                ):
                    page.locator(f'[data-archived-db-view="view_archived_{view}"]').click()
                    expect(page.locator('#archivedDatabasePreview .archived-database-grid'))\
                        .to_have_attribute('data-view', view)
                    expect(page.locator(selector)).to_have_count(count)
                    if capture := os.environ.get(f'NOTION_ARCHIVED_DB_{view.upper()}_SCREENSHOT'):
                        page.screenshot(path=capture)
                view_tabs.first.click()
                expect(preview_rows).to_have_count(2)
                filter_tool = page.locator('[data-archived-db-tool="filter"]')
                sort_tool = page.locator('[data-archived-db-tool="sort"]')
                search_tool = page.locator('[data-archived-db-tool="search"]')
                settings_tool = page.locator('[data-archived-db-tool="settings"]')
                expect(filter_tool).to_be_visible()
                expect(sort_tool).to_be_visible()
                expect(search_tool).to_be_visible()
                expect(settings_tool).to_be_visible()
                expect(page.get_by_role('button', name='자동화 추가 불가')).to_be_disabled()
                filter_tool.click()
                expect(page.locator('[data-archived-db-property]').first).to_be_visible()
                page.locator('[data-archived-db-property]').first.click()
                page.locator('[data-archived-db-back]').click()
                expect(page.locator('[data-archived-db-property]').first).to_be_visible()
                page.locator('[data-archived-db-property]').first.click()
                page.locator('[data-archived-db-filter-value]').fill('Alpha')
                page.locator('[data-archived-db-filter-apply]').click()
                expect(page.locator('.archived-database-criteria')).to_contain_text('필터')
                expect(preview_rows).to_have_count(1)
                expect(preview_rows.first).to_contain_text('Alpha')
                page.locator('[data-archived-db-clear="filter"]').click()
                expect(page.locator('.archived-database-criteria')).to_be_hidden()
                expect(preview_rows).to_have_count(2)
                sort_tool.click()
                page.locator('[data-archived-db-property]').first.click()
                page.locator('[data-archived-db-sort-direction="desc"]').click()
                expect(page.locator('.archived-database-criteria')).to_contain_text('정렬')
                expect(preview_rows.first).to_contain_text('Zeta')
                page.locator('[data-archived-db-clear="sort"]').click()
                search_tool.click()
                search_input = page.locator('[data-archived-db-search]')
                expect(search_input).to_be_visible()
                search_input.fill('Zeta')
                expect(preview_rows).to_have_count(1)
                expect(preview_rows.first).to_contain_text('Zeta')
                search_input.press('Escape')
                expect(search_input).to_be_hidden()
                expect(preview_rows).to_have_count(2)
                settings_tool.click()
                expect(page.locator('.archived-database-settings')).to_be_visible()
                page.locator('[data-archived-db-settings-tool="filter"]').click()
                expect(page.locator('[data-archived-db-property]').first).to_be_visible()
                page.keyboard.press('Escape')
                expect(page.locator('.archived-database-settings')).to_be_hidden()
                expect(page.locator('#shareDrawerBtn')).to_be_visible()
                expect(page.locator('#pageCopyLinkBtn')).to_be_visible()
                expect(page.locator('#pageMoreBtn')).to_be_visible()
                expect(page.locator('#favoritePageBtn')).to_be_hidden()
                page.locator('#shareDrawerBtn').click()
                expect(page.locator('#archivedDatabaseSharePopover')).to_be_visible()
                expect(page.locator('#archivedDatabaseShareAccess')).to_contain_text(
                    '부모 페이지의 직접 공유', timeout=30_000)
                share_bounds = page.locator('#archivedDatabaseSharePopover').bounding_box()
                assert share_bounds and share_bounds['x'] >= 0 and share_bounds['y'] >= 0
                assert share_bounds['x'] + share_bounds['width'] <= 1280
                assert share_bounds['y'] + share_bounds['height'] <= 800
                if capture := os.environ.get('NOTION_ARCHIVED_DB_SHARE_SCREENSHOT'):
                    page.screenshot(path=capture)
                page.set_viewport_size({'width': 390, 'height': 700})
                compact_bounds = page.locator('#archivedDatabaseSharePopover').bounding_box()
                assert compact_bounds and compact_bounds['x'] >= 0 and compact_bounds['y'] >= 0
                assert compact_bounds['x'] + compact_bounds['width'] <= 390
                assert compact_bounds['y'] + compact_bounds['height'] <= 700
                assert page.evaluate("""() => {
                  const popover = document.getElementById('archivedDatabaseSharePopover');
                  const rect = popover.getBoundingClientRect();
                  return popover.contains(document.elementFromPoint(rect.left + 20, rect.top + 20));
                }""")
                if capture := os.environ.get('NOTION_ARCHIVED_DB_SHARE_COMPACT_SCREENSHOT'):
                    page.screenshot(path=capture)
                page.keyboard.press('Escape')
                expect(page.locator('#archivedDatabaseSharePopover')).to_be_hidden()
                for tool in (filter_tool, sort_tool, search_tool, settings_tool):
                    bounds = tool.bounding_box()
                    assert bounds and 0 <= bounds['x']
                    assert bounds['x'] + bounds['width'] <= 390
                timeline_tab = page.locator('[data-archived-db-view="view_archived_timeline"]')
                timeline_tab.click()
                expect(page.locator('#archivedDatabasePreview .archived-database-grid'))\
                    .to_have_attribute('data-view', 'timeline')
                expect(page.locator('.archived-database-timeline-bar')).to_have_count(1)
                timeline_bounds = timeline_tab.bounding_box()
                assert timeline_bounds and timeline_bounds['x'] >= 0
                assert timeline_bounds['x'] + timeline_bounds['width'] <= 390
                strip_bounds = page.locator('.archived-database-views').bounding_box()
                assert strip_bounds and timeline_bounds['x'] >= strip_bounds['x']
                assert timeline_bounds['x'] + timeline_bounds['width'] <= (
                    strip_bounds['x'] + strip_bounds['width'] + 1)
                assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
                filter_tool.click()
                filter_popover = page.locator('.archived-database-tool-popover')
                expect(filter_popover).to_be_visible()
                filter_bounds = filter_popover.bounding_box()
                assert filter_bounds and filter_bounds['x'] >= 0 and filter_bounds['y'] >= 0
                assert filter_bounds['x'] + filter_bounds['width'] <= 390
                assert filter_bounds['y'] + filter_bounds['height'] <= 700
                if capture := os.environ.get('NOTION_ARCHIVED_DB_TIMELINE_390_SCREENSHOT'):
                    page.screenshot(path=capture)
                page.keyboard.press('Escape')
                expect(filter_popover).to_be_hidden()
                view_tabs.first.click()
                expect(page.locator('#archivedDatabasePreview .archived-database-grid'))\
                    .to_have_attribute('data-view', 'table')
                page.locator('#pageMoreBtn').click()
                compact_menu = page.locator('#pageMenu')
                expect(compact_menu.locator('.menu-search')).to_be_focused()
                menu_bounds = compact_menu.bounding_box()
                assert menu_bounds and menu_bounds['x'] >= 0 and menu_bounds['y'] >= 0
                assert menu_bounds['x'] + menu_bounds['width'] <= 390
                assert menu_bounds['y'] + menu_bounds['height'] <= 700
                if capture := os.environ.get('NOTION_ARCHIVED_DB_MORE_COMPACT_SCREENSHOT'):
                    page.screenshot(path=capture)
                compact_menu.locator('[data-page-action="export_archive"]').click()
                expect(page.locator('#modalTitle')).to_have_text('내보내기')
                expect(page.locator('#archivedDatabaseExportContent')).to_have_value('all')
                expect(page.locator('#modalBody .database-export-switch input:disabled')).to_have_count(3)
                expect(page.locator('#archivedDatabaseExportCommentsRow')).to_be_hidden()
                dialog_bounds = page.locator('#modalBg .modal').bounding_box()
                assert dialog_bounds and dialog_bounds['x'] >= 0 and dialog_bounds['y'] >= 0
                assert dialog_bounds['width'] <= 320
                assert dialog_bounds['x'] + dialog_bounds['width'] <= 390
                assert dialog_bounds['y'] + dialog_bounds['height'] <= 700
                expect(page.locator('#modalClose')).to_be_hidden()
                if capture := os.environ.get('NOTION_ARCHIVED_DB_EXPORT_COMPACT_SCREENSHOT'):
                    page.screenshot(path=capture)
                page.locator('#modalActions button').first.click()
                expect(page.locator('#modalBg')).to_be_hidden()
                if capture := os.environ.get('NOTION_ARCHIVED_DB_COMPACT_SCREENSHOT'):
                    page.screenshot(path=capture)
                page.set_viewport_size({'width': 1280, 'height': 800})
                page.locator('#pageMoreBtn').click()
                menu = page.locator('#pageMenu')
                expect(menu.locator('.menu-search')).to_be_visible()
                expect(menu.locator('[data-page-action="export_archive"]')).to_be_visible()
                expect(menu).to_contain_text('업데이트와 애널리틱스')
                expect(menu).to_contain_text('Windows 앱에서 열기')
                expect(menu).to_contain_text('최종 편집')
                expect(menu.locator('[data-page-action="help"]')).to_be_visible()
                expect(menu.locator('[data-page-action="copy_link"]')).to_have_count(0)
                expect(menu.locator('[data-page-action="open_archived"]')).to_have_count(0)
                expect(menu.locator('[data-page-action="restore"]')).to_have_count(0)
                if capture := os.environ.get('NOTION_ARCHIVED_DB_MORE_SCREENSHOT'):
                    page.screenshot(path=capture)
                menu.locator('.menu-search').fill('내보')
                expect(menu.locator('[data-page-action="export_archive"]')).to_be_visible()
                expect(menu.get_by_text('Windows 앱에서 열기')).to_be_hidden()
                menu.locator('.menu-search').fill('')
                menu.locator('[data-page-action="export_archive"]').click()
                expect(page.locator('#modalTitle')).to_have_text('내보내기')
                expect(page.locator('#archivedDatabaseExportScope')).to_have_value('current')
                page.locator('#archivedDatabaseExportFormat').select_option('html')
                expect(page.locator('#archivedDatabaseExportCommentsRow')).to_be_visible()
                page.locator('#archivedDatabaseExportFormat').select_option('markdown_csv')
                expect(page.locator('#archivedDatabaseExportCommentsRow')).to_be_hidden()
                page.locator('#archivedDatabaseExportContent').select_option('exclude_files_images')
                assert page.locator('#modalBg .modal').bounding_box()['width'] <= 320
                if capture := os.environ.get('NOTION_ARCHIVED_DB_EXPORT_SCREENSHOT'):
                    page.screenshot(path=capture)
                with page.expect_download() as archived_download:
                    page.locator('#modalActions button.primary').click()
                assert archived_download.value.suggested_filename.endswith('.zip')
                assert 'bundle=markdown_csv' in archived_download.value.url
                assert 'page_content=exclude_files_images' in archived_download.value.url
                with zipfile.ZipFile(archived_download.value.path()) as archive_file:
                    assert any(name.endswith('.csv') for name in archive_file.namelist())
                    assert any(name.endswith('_all.csv') for name in archive_file.namelist())
                    for name in archive_file.namelist():
                        content = archive_file.read(name).decode('utf-8')
                        assert 'Alpha' in content and 'Zeta' in content
                expect(page.locator('#modalBg')).to_be_hidden()
                if capture := os.environ.get('NOTION_ARCHIVED_DB_SCREENSHOT'):
                    page.screenshot(path=capture)
                assert f'sidebar_db={block_id}' in page.url
                page.locator('[data-archived-db-view="view_archived_board"]').click()
                expect(page.locator('.archived-database-grid')).to_have_attribute('data-view', 'board')
                page.reload(wait_until='domcontentloaded')
                expect(page.locator('#archivedPageNotice')).to_be_visible(timeout=30_000)
                expect(page.locator('#archivedPageTitle')).to_have_text(archived_title)
                expect(page.locator('.archived-database-grid')).to_have_attribute('data-view', 'table')
                expect(page.locator('.archived-database-criteria')).to_be_hidden()
                expect(page.locator('[data-archived-db-search]')).to_be_hidden()
                page.locator('#archivedPageRestoreBtn').click()
                expect(row).to_be_visible(timeout=30_000)
                expect(favorite_row).not_to_have_attribute('data-sidebar-db-archived', 'true')
                assert authority.read_database('owner', block_id)['id'] == block_id
                assert len(posted) == 1
                assert not errors, errors
            finally:
                browser.close()
