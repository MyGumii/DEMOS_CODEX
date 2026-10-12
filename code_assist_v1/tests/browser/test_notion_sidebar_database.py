import re

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _seed_blocks, _screenshot


@pytest.mark.integration
@pytest.mark.regression
def test_ui_personal_database_favorite_is_independent_and_survives_reload(document_page):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    parent_id = page.evaluate('() => State.activePageId')
    assert page.evaluate('() => !State.navigation.enabled && !State.navigation.authoritative')
    _seed_blocks(page, [{'type':'table', 'content':'독립 즐겨찾기 표', 'props': {
        'views':[{'id':'view_table', 'type':'table', 'name':'표'}],
        'active_view_id':'view_table', 'rows':[{'title':'검증 항목'}],
    }}])
    block_id = page.evaluate('() => State.pageData.blocks[0].id')
    page.evaluate('renderPageLists()')
    row = page.locator(f'#ownPages .sidebar-database-row[data-sidebar-db-block="{block_id}"]')
    expect(row).to_be_visible()
    row.hover()
    row.locator('[data-sidebar-db-menu]').click()
    action = page.locator('#pageMenu [data-page-action="favorite"]')
    expect(action).to_contain_text('즐겨찾기에 추가')
    action.click()
    favorite = page.locator(f'#favoritePages .sidebar-database-row[data-sidebar-db-block="{block_id}"]')
    expect(favorite).to_be_visible()
    expect(page.locator(f'#favoritePages .page-row[data-page-id="{parent_id}"]')).to_have_count(0)
    assert page.evaluate('''id => [...State.legacyDatabaseFavorites].some(key => JSON.parse(key)[2] === id)''', block_id)

    page.reload(wait_until='domcontentloaded')
    expect(favorite).to_be_visible(timeout=20_000)
    expect(page.locator(f'#favoritePages .page-row[data-page-id="{parent_id}"]')).to_have_count(0)
    favorite.click()
    expect(page.locator('#pageView')).to_have_class(re.compile(r'\bdatabase-full-page\b'))
    expect(page.locator('#favoritePageBtn')).to_have_attribute('aria-pressed', 'true')
    page.locator('#favoritePageBtn').click()
    expect(favorite).to_have_count(0)
    expect(page.locator('#favoritePageBtn')).to_have_attribute('aria-pressed', 'false')
    page.locator('#favoritePageBtn').click()
    expect(favorite).to_be_visible()
    favorite.hover()
    favorite.locator('[data-sidebar-db-menu]').click()
    expect(action).to_contain_text('즐겨찾기 해제')
    action.click()
    expect(favorite).to_have_count(0)
    expect(page.locator(f'#favoritePages .page-row[data-page-id="{parent_id}"]')).to_have_count(0)
    assert page.evaluate('() => State.pageData.page.id') == parent_id
    assert page.evaluate('() => State.pageData.blocks[0].id') == block_id
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_ui_sidebar_database_child_opens_full_page_and_survives_reload(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    parent_id = page.evaluate('() => State.activePageId')
    child_id = page.evaluate('''async () => {
      const parentId = State.activePageId;
      const data = await api('/api/notion/pages', {
        method:'POST', body:{title:'검증 하위 페이지', parent_id:parentId}
      });
      await loadPages();
      await openPage(parentId, false, PageFocusPolicy.PRESERVE);
      return data.page.id;
    }''')
    _seed_blocks(page, [{
        'type': 'table', 'content': '사이드바 검증 표', 'props': {
            'views': [
                {'id': 'view_table', 'type': 'table', 'name': '표'},
                {'id': 'view_calendar', 'type': 'calendar', 'name': '날짜 보기'},
            ],
            'active_view_id': 'view_table',
            'rows': [{'title': '검증 항목'}],
        },
    }, {'type':'child_page', 'content':'검증 하위 페이지', 'props':{'page_id':child_id}}])
    block_id = page.evaluate('() => State.pageData.blocks[0].id')
    page.evaluate('renderPageLists()')

    own_parent = page.locator(f'#ownPages .page-row[data-page-id="{parent_id}"]')
    own_database = page.locator(f'#ownPages .sidebar-database-row[data-sidebar-db-block="{block_id}"]')
    expect(own_parent).to_have_attribute('aria-expanded', 'true')
    expect(own_database).to_be_visible()
    expect(own_database).to_have_attribute('aria-level', '2')
    assert page.locator('#ownPages .page-row').evaluate_all('''rows => rows
      .filter(row => row.dataset.sidebarDbBlock || row.dataset.pageId)
      .map(row => row.dataset.sidebarDbBlock ? 'database' : row.dataset.pageId)''')[:3] == [
          parent_id, 'database', child_id]
    own_database.hover()
    own_database.locator('[data-sidebar-db-menu]').click()
    expect(page.locator('#pageMenu')).to_be_visible()
    menu_box = page.locator('#pageMenu').bounding_box()
    copy_box = page.locator('#pageMenu [data-page-action="copy_link"]').bounding_box()
    assert abs(menu_box['width'] - 265) <= 2, menu_box
    assert abs(copy_box['height'] - 28) <= 2, copy_box
    _screenshot(page, tmp_path, 'sidebar-database-menu.png')
    expect(page.locator('#pageMenu')).to_be_focused()
    expect(page.locator('#pageMenu')).to_have_attribute('aria-label', '데이터베이스 옵션')
    expect(page.locator('#pageMenu [data-page-action="copy_link"]')).to_have_attribute('role', 'menuitem')
    assert page.locator('#pageMenu [data-page-action]').evaluate_all(
        'buttons => buttons.map(button => button.dataset.pageAction)') == [
            'favorite', 'copy_link', 'rename', 'new_tab', 'side_peek', 'help']
    help_link = page.locator('#pageMenu [data-page-action="help"]')
    expect(help_link).to_have_attribute('href', 'https://www.notion.com/ko/help/intro-to-databases')
    expect(help_link).to_have_attribute('target', '_blank')
    help_link.focus()
    page.evaluate('renderPageLists()')
    page.evaluate('refreshOpenPageMenu(State.pageData.page)')
    expect(page.locator('#pageMenu')).to_be_visible()
    expect(page.locator('#pageMenu')).to_have_attribute('aria-label', '데이터베이스 옵션')
    expect(page.locator('#pageMenu [data-page-action="help"]')).to_be_focused()
    expect(own_database.locator('[data-sidebar-db-menu]')).to_have_attribute('aria-expanded', 'true')
    page.keyboard.press('Escape')
    expect(own_database.locator('[data-sidebar-db-menu]')).to_be_focused()
    own_database.focus()
    page.keyboard.press('Shift+F10')
    expect(page.locator('#pageMenu')).to_be_focused()
    page.keyboard.press('Escape')
    expect(own_database).to_be_focused()
    row_box = own_database.bounding_box()
    pointer_x = row_box['x'] + min(85, row_box['width'] / 2)
    pointer_y = row_box['y'] + row_box['height'] / 2
    page.mouse.click(pointer_x, pointer_y, button='right')
    expect(page.locator('#pageMenu')).to_be_visible()
    expect(page.locator('#pageMenu')).to_be_focused()
    page.wait_for_function('''x => {
      const menu=document.querySelector('#pageMenu');
      return Math.abs(menu.getBoundingClientRect().left - x) <= 8;
    }''', arg=pointer_x)
    page.keyboard.press('ArrowUp')
    expect(page.locator('#pageMenu [data-page-action="help"]')).to_be_focused()
    page.keyboard.press('ArrowUp')
    expect(page.locator('#pageMenu [data-page-action="side_peek"]')).to_be_focused()
    assert page.evaluate('() => document.querySelector("#pageMenu").dataset.surface') == 'database'
    assert page.evaluate(f'() => sidebarDatabaseUrl("{parent_id}", "{block_id}")') == (
        f'{page.url.split("/page/")[0]}/page/{parent_id}?sidebar_db={block_id}')
    original_page_title = page.evaluate('() => State.pageData.page.title')
    page.locator('#pageMenu [data-page-action="rename"]').click()
    expect(page.locator('[data-db-title]')).to_be_focused()
    assert page.evaluate('() => State.pageData.page.title') == original_page_title
    own_database.hover()
    own_database.locator('[data-sidebar-db-toggle]').click()
    expect(own_database.locator('[data-sidebar-db-toggle]')).to_be_focused()
    expect(page.locator('#ownPages .sidebar-database-view')).to_have_count(2)
    page.locator('#ownPages .sidebar-database-view[data-sidebar-db-view="view_calendar"]').click()
    expect(page.locator('#pageView')).to_have_class(re.compile(r'\bdatabase-full-page\b'))
    assert f'sidebar_db={block_id}' in page.url
    page.wait_for_function('() => State.pageData.blocks[0].props.active_view_id === "view_calendar"')
    assert page.evaluate('() => State.pageData.blocks[0].props.active_view_id') == 'view_calendar'
    page.reload(wait_until='domcontentloaded')
    expect(page.locator('#pageView')).to_have_class(re.compile(r'\bdatabase-full-page\b'), timeout=20_000)
    assert page.evaluate('() => State.pageData.blocks[0].props.active_view_id') == 'view_calendar'
    expect(page.locator(f'#ownPages .sidebar-database-row[data-sidebar-db-block="{block_id}"]')).to_be_visible()
    expect(page.locator('#ownPages .sidebar-database-view')).to_have_count(0)
    page.locator('[data-db-close-full-page]').click()
    assert 'sidebar_db=' not in page.url

    # The child also appears when its parent is no longer the open document.
    page.evaluate('''async () => {
      const data = await api('/api/notion/pages', {method:'POST', body:{title:'다른 검증 페이지'}});
      await loadPages();
      await openPage(data.page.id);
    }''')
    expect(own_database).to_be_visible(timeout=20_000)
    own_database.click()
    expect(page.locator('#pageView')).to_have_class(re.compile(r'\bdatabase-full-page\b'))
    assert page.evaluate('() => State.activePageId') == parent_id

    own_parent.click(button='right')
    page.locator('#pageMenu [data-page-action="favorite"]').click()
    favorite_database = page.locator(f'#favoritePages .sidebar-database-row[data-sidebar-db-block="{block_id}"]')
    expect(favorite_database).to_be_visible()
    expect(favorite_database).to_have_attribute('aria-level', '2')
    favorite_database.hover()
    _screenshot(page, tmp_path, 'sidebar-database-child.png')
    favorite_database.locator('[data-sidebar-db-add]').click()
    expect(page.locator('#sidePeek')).to_be_visible(timeout=20_000)
    assert page.evaluate('() => State.pageData.blocks[0].props.rows.length') == 2
    assert page.evaluate('() => State.activePageId') == parent_id
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_ui_sidebar_database_side_peek_targets_database_and_saves(document_page):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    parent_id = page.evaluate('() => State.activePageId')
    original_page_title = page.evaluate('() => State.pageData.page.title')
    original_url = page.url
    _seed_blocks(page, [{
        'type': 'table', 'content': '옆 보기 검증 표', 'props': {
            'views': [{'id': 'view_table', 'type': 'table', 'name': '표'}],
            'active_view_id': 'view_table',
            'rows': [{'title': '검증 항목'}],
        },
    }])
    block_id = page.evaluate('() => State.pageData.blocks[0].id')
    page.evaluate('renderPageLists()')
    row = page.locator(f'#ownPages .sidebar-database-row[data-sidebar-db-block="{block_id}"]')
    row.hover()
    row.locator('[data-sidebar-db-menu]').click()
    page.locator('#pageMenu [data-page-action="side_peek"]').click()

    expect(page.locator('#sidePeek')).to_be_visible()
    frame = page.frame_locator('#sidePeekEditorSlot iframe')
    expect(frame.locator('#pageView')).to_have_class(re.compile(r'\bdatabase-full-page\b'), timeout=20_000)
    expect(frame.locator('[data-db-title]')).to_have_value('옆 보기 검증 표')
    expect(page.locator('#sidePeekCrumb')).to_have_text('옆 보기 검증 표')
    assert page.url == original_url
    assert page.evaluate('() => State.sidePeek.databaseBlockId') == block_id
    assert page.evaluate('() => State.sidePeek.pageId') == parent_id
    assert page.evaluate('() => State.pageData.page.title') == original_page_title

    frame.locator('[data-db-title]').fill('옆 보기에서 바꾼 표')
    expect(page.locator('#sidePeekCrumb')).to_have_text('옆 보기에서 바꾼 표')
    page.locator('#sidePeekCloseBtn').click()
    expect(page.locator('#sidePeek')).to_be_hidden()
    expect(row.locator('[data-sidebar-db-menu]')).to_be_focused()
    page.wait_for_function('() => State.pageData.blocks[0].content === "옆 보기에서 바꾼 표"')
    assert page.evaluate('() => State.pageData.page.title') == original_page_title
    page.reload(wait_until='domcontentloaded')
    expect(page.locator('#ownPages .sidebar-database-row .page-title-label').first).to_have_text(
        '옆 보기에서 바꾼 표', timeout=20_000)
    page.wait_for_function('id => State.pageData?.page?.id === id', arg=parent_id, timeout=20_000)
    assert page.evaluate('() => State.pageData.page.title') == original_page_title
    other_id = page.evaluate('''async () => {
      const data = await api('/api/notion/pages', {method:'POST', body:{title:'옆 보기 비교 페이지'}});
      await loadPages();
      await openPage(data.page.id);
      return data.page.id;
    }''')
    row.click(modifiers=['Alt'])
    expect(page.locator('#sidePeek')).to_be_visible()
    expect(page.frame_locator('#sidePeekEditorSlot iframe').locator('[data-db-title]')).to_have_value(
        '옆 보기에서 바꾼 표', timeout=20_000)
    assert page.evaluate('() => State.activePageId') == other_id
    page.locator('#sidePeekMoreBtn').click()
    expect(page.locator('#pageMenu')).to_have_attribute('aria-label', '데이터베이스 옵션')
    assert page.evaluate('() => document.querySelector("#pageMenu").dataset.databaseBlockId') == block_id
    page.keyboard.press('Escape')
    page.locator('#sidePeekFullBtn').click()
    expect(page.locator('#sidePeek')).to_be_hidden()
    expect(page.locator('#pageView')).to_have_class(re.compile(r'\bdatabase-full-page\b'))
    assert page.evaluate('() => State.activePageId') == parent_id
    assert f'sidebar_db={block_id}' in page.url
    assert not errors, errors
