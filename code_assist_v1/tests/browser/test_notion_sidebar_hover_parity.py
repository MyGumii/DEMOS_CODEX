"""Sidebar page hover controls compared with the signed-in Notion sidebar."""

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _screenshot
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app


@pytest.mark.integration
@pytest.mark.regression
def test_sidebar_section_heading_is_full_toggle_target(document_page):
    from playwright.sync_api import expect

    page = document_page
    heading = page.locator('#privateSection .section-label')
    pages = page.locator('#ownPages')
    expect(heading).to_have_attribute('aria-controls', 'ownPages')
    expect(heading).to_have_attribute('aria-expanded', 'true')
    expect(pages).to_be_visible()

    heading.get_by_text('개인 페이지').click()
    expect(heading).to_have_attribute('aria-expanded', 'false')
    expect(pages).to_be_hidden()

    heading.focus()
    heading.press('Enter')
    expect(heading).to_have_attribute('aria-expanded', 'true')
    expect(pages).to_be_visible()


@pytest.mark.integration
@pytest.mark.regression
def test_sidebar_page_sections_start_at_top_without_losing_local_tools(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({"width": 1280, "height": 720})
    heading = page.locator('#privateSection .section-label')
    home = page.locator('#workspaceHomeBtn')
    filter_panel = page.locator('#sidebarFilter')
    expect(heading).to_be_visible()
    assert heading.bounding_box()['y'] < home.bounding_box()['y'] < filter_panel.bounding_box()['y']

    filter_panel.locator('summary').click()
    search = page.locator('#pageSearch')
    search.fill('SIDEBAR-NO-MATCH-20260928')
    expect(page.locator('#ownPages .page-row')).to_have_count(0)
    search.fill('')
    expect(page.locator('#ownPages .page-row').first).to_be_visible()

    home.click()
    expect(page.locator('#homeView')).to_be_visible()


@pytest.mark.integration
@pytest.mark.regression
def test_recent_page_row_removes_only_history_and_reappears_after_open(document_page):
    from playwright.sync_api import expect

    page = document_page
    page_id = page.evaluate('State.activePageId')
    recent = page.locator(f'#recentPages .page-row[data-page-id="{page_id}"]')
    expect(recent).to_be_visible()
    recent.hover()
    recent.locator('[data-page-menu]').click()
    menu = page.locator('#pageMenu')
    expect(menu.locator('[data-page-action="remove_recent"]')).to_have_text('×최근 항목에서 제거')
    page.evaluate('renderPageLists()')
    expect(menu.locator('[data-page-action="remove_recent"]')).to_be_visible()
    menu.locator('[data-page-action="remove_recent"]').click()
    expect(recent).to_have_count(0)
    own_row = page.locator(f'#ownPages .page-row[data-page-id="{page_id}"]')
    expect(own_row).to_have_count(1)
    expect(own_row).to_be_focused()

    page.locator('#workspaceHomeBtn').click()
    page.reload()
    expect(page.locator('#homeView')).to_be_visible()
    expect(recent).to_have_count(0)
    own_row.click()
    expect(recent).to_be_visible()


@pytest.mark.integration
@pytest.mark.regression
def test_recent_page_menu_ignores_stale_workspace_after_switch(document_page):
    from playwright.sync_api import expect

    page = document_page
    page_id = page.evaluate('State.activePageId')
    recent = page.locator(f'#recentPages .page-row[data-page-id="{page_id}"]')
    recent.hover()
    recent.locator('[data-page-menu]').click()
    menu = page.locator('#pageMenu')
    expect(menu.locator('[data-page-action="remove_recent"]')).to_be_visible()
    scope = page.evaluate("""() => {
      const original = State.navigation.workspaceId;
      State.navigation.workspaceId = 'stale-workspace';
      return {original, staleKey:recentPageHistoryKey()};
    }""")
    try:
        menu.locator('[data-page-action="remove_recent"]').click()
        expect(menu).to_be_hidden()
        assert page.evaluate('key => localStorage.getItem(key)', scope['staleKey']) is None
    finally:
        page.evaluate('workspace => {State.navigation.workspaceId=workspace;renderPageLists()}', scope['original'])
    expect(recent).to_be_visible()


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('sidebar_width', [240, 255, 270])
def test_sidebar_page_hover_controls_with_scrollbar(document_page, tmp_path, sidebar_width):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({"width": 1280, "height": 720})
    page.evaluate('width => setSidebarWidth(width, false)', sidebar_width)
    page.evaluate("""() => {
      const list = document.querySelector('#ownPages');
      const source = list.querySelector('.page-row');
      source.querySelector('.page-title-label').textContent = '아주 긴 페이지 제목이 좁은 사이드바에서 잘리지 않고 줄임표로 표시되는지 확인';
      for (let index = 0; index < 30; index += 1) {
        const clone = source.cloneNode(true);
        clone.classList.remove('active');
        clone.dataset.pageId = `layout-only-${index}`;
        clone.querySelector('.page-title-label').textContent = `배치 확인 ${index}`;
        list.append(clone);
      }
    }""")
    row = page.locator('#ownPages .page-row').first
    expect(row).to_have_css('padding-right', '8px')
    rest_geometry = page.evaluate("""() => {
      const row = document.querySelector('#ownPages .page-row');
      const label = row.querySelector('.page-title-label');
      return {rowRight:row.getBoundingClientRect().right,labelRight:label.getBoundingClientRect().right};
    }""")
    assert rest_geometry['labelRight'] == rest_geometry['rowRight'] - 8, rest_geometry
    row.hover()
    expect(row).to_have_css('padding-right', '54px')
    add = row.locator('[data-page-child]')
    more = row.locator('[data-page-menu]')
    expect(add).to_be_visible()
    expect(more).to_be_visible()
    geometry = page.evaluate("""() => {
      const bounds = selector => {
        const rect = document.querySelector(selector).getBoundingClientRect();
        return {x:rect.x,y:rect.y,width:rect.width,height:rect.height};
      };
      const scroll = document.querySelector('.sidebar-scroll');
      const firstRow = document.querySelector('#ownPages .page-row');
      const label = document.querySelector('#ownPages .page-row .page-title-label');
      return {sidebar:bounds('.sidebar'),scroll:bounds('.sidebar-scroll'),
        row:bounds('#ownPages .page-row'),
        rowHovered:firstRow.matches(':hover'),
        rowPaddingRight:getComputedStyle(firstRow).paddingRight,
        label:bounds('#ownPages .page-row .page-title-label'),
        labelScrollWidth:label.scrollWidth,labelClientWidth:label.clientWidth,
        add:bounds('#ownPages .page-row [data-page-child]'),
        more:bounds('#ownPages .page-row [data-page-menu]'),
        scrollWidth:scroll.clientWidth,scrollHeight:scroll.scrollHeight,
        clientHeight:scroll.clientHeight};
    }""")
    _screenshot(page, tmp_path, f'notion_sidebar_hover_{sidebar_width}.png')
    assert geometry['scrollHeight'] > geometry['clientHeight']
    assert geometry['sidebar']['width'] == sidebar_width
    assert geometry['row']['x'] == 8
    assert geometry['row']['width'] == geometry['scrollWidth'] - 16
    assert geometry['add']['x'] == geometry['row']['x'] + geometry['row']['width'] - 50
    assert geometry['more']['x'] == geometry['row']['x'] + geometry['row']['width'] - 28
    assert geometry['rowHovered'] and geometry['rowPaddingRight'] == '54px', geometry
    assert geometry['label']['width'] > 0 and geometry['label']['x'] + geometry['label']['width'] <= geometry['add']['x'], geometry
    assert geometry['labelScrollWidth'] > geometry['labelClientWidth']
    if sidebar_width == 255:
        assert (geometry['row']['x'], geometry['row']['width'], geometry['add']['x'], geometry['more']['x']) == (8, 239, 197, 219), geometry
    more.click()
    menu = page.locator('#pageMenu')
    expect(menu).to_be_visible()
    expect(row.locator('.page-row-actions')).to_have_css('opacity', '0')
    menu_geometry = page.evaluate("""() => {
      const menu = document.querySelector('#pageMenu');
      const rect = menu.getBoundingClientRect();
      const row = document.querySelector('#ownPages .page-row');
      const actions = row.querySelector('.page-row-actions');
      return {menu:{x:rect.x,y:rect.y,width:rect.width,height:rect.height,left:menu.style.left,top:menu.style.top,pointer:menu.pageMenuPointer},
        actionsOpacity:getComputedStyle(actions).opacity};
    }""")
    assert abs(menu_geometry['menu']['x'] - (geometry['more']['x'] + geometry['more']['width'] / 2)) <= 1, menu_geometry
    assert abs(menu_geometry['menu']['y'] - (geometry['more']['y'] + geometry['more']['height'] / 2)) <= 1
    assert menu_geometry['menu']['width'] == 265
    assert menu_geometry['actionsOpacity'] == '0', menu_geometry
    _screenshot(page, tmp_path, f'notion_sidebar_menu_{sidebar_width}.png')
    menu.press('Escape')
    expect(menu).to_be_hidden()
    expect(more).to_be_visible()
    more.press('Enter')
    expect(menu).to_be_visible()
    page.wait_for_function("""() => {
      const menu = document.querySelector('#pageMenu').getBoundingClientRect();
      const anchor = document.querySelector('#ownPages .page-row [data-page-menu]').getBoundingClientRect();
      return menu.x === anchor.x + 8 && menu.y === anchor.y + 9;
    }""", timeout=1500)
    keyboard_geometry = page.evaluate("""() => {
      const panel = document.querySelector('#pageMenu');
      const menu = panel.getBoundingClientRect();
      const anchor = document.querySelector('#ownPages .page-row [data-page-menu]').getBoundingClientRect();
      return {menu:{x:menu.x,y:menu.y},anchor:{x:anchor.x,y:anchor.y}};
    }""")
    assert (
        keyboard_geometry['menu']['x'], keyboard_geometry['menu']['y']
    ) == (
        keyboard_geometry['anchor']['x'] + 8,
        keyboard_geometry['anchor']['y'] + 9,
    ), keyboard_geometry
    menu.press('Escape')
    expect(menu).to_be_hidden()


@pytest.mark.integration
@pytest.mark.regression
def test_narrow_desktop_topbar_keeps_sidebar_and_actions_separate(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({"width": 826, "height": 836})
    page.locator('#pageTitle').fill('Codex UI 비교 — 연결된 DB 원본 2026-09-16')
    expect(page.locator('#pageMoreBtn')).to_be_visible()
    geometry = page.evaluate("""() => {
      const rect = selector => {
        const element = document.querySelector(selector);
        if (!element || getComputedStyle(element).display === 'none') return null;
        const bounds = element.getBoundingClientRect();
        return {left:bounds.left,right:bounds.right,width:bounds.width,top:bounds.top};
      };
      return {sidebar:rect('.sidebar'),main:rect('#workspaceMain'),
        breadcrumb:rect('#breadcrumbWrap'),location:rect('#pageLocationBtn'),
        lock:rect('#pageLockBtn'),share:rect('#shareDrawerBtn'),
        link:rect('#pageCopyLinkBtn'),favorite:rect('#favoritePageBtn'),
        more:rect('#pageMoreBtn'),viewport:window.innerWidth};
    }""")
    _screenshot(page, tmp_path, 'notion_narrow_topbar_826.png')
    assert geometry['main']['left'] == geometry['sidebar']['right']
    assert geometry['breadcrumb']['left'] >= geometry['main']['left']
    assert geometry['share']['right'] < geometry['link']['left'] + 3
    assert geometry['link']['right'] <= geometry['favorite']['left'] + 3
    assert geometry['favorite']['right'] <= geometry['more']['left'] + 3
    assert geometry['more']['right'] <= geometry['viewport']
    page.set_viewport_size({"width": 390, "height": 700})
    page.wait_for_function("""() => {
      const box=document.querySelector('#pageView').getBoundingClientRect();
      return innerWidth===390 && Math.abs(box.x-14)<=1 && Math.abs(box.width-362)<=1;
    }""", timeout=3_000)
    mobile_canvas = page.locator('#pageView').bounding_box()
    mobile_geometry = page.evaluate("""() => Object.fromEntries(
      ['html','body','.notion-root','#workspaceMain','.topbar','#pageView'].map(selector => {
        const element = document.querySelector(selector), box = element.getBoundingClientRect();
        return [selector,{x:box.x,width:box.width,scrollWidth:element.scrollWidth,
          display:getComputedStyle(element).display,minWidth:getComputedStyle(element).minWidth}];
      }))""")
    _screenshot(page, tmp_path, 'notion_narrow_topbar_390.png')
    assert abs(mobile_canvas['x'] - 14) <= 1, mobile_geometry
    assert abs(mobile_canvas['width'] - 362) <= 1, mobile_geometry


@pytest.mark.integration
@pytest.mark.regression
def test_legacy_full_database_share_identifies_parent_scope(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({'width':390,'height':700})
    assert page.evaluate("""() => {
      if (State.navigation.collaborativeSharing) return false;
      const block=EditorCore.createBlock('table',{content:'Legacy database'});
      State.pageData.blocks=[block];renderBlocks();
      return openDatabaseFullPage(block.id);
    }""")
    page.wait_for_function("() => document.querySelector('#pageView').classList.contains('database-full-page')")
    page.locator('#shareDrawerBtn').click()
    expect(page.locator('#inspector')).to_be_visible()
    expect(page.locator('#drawerTitle')).to_have_text('부모 페이지 공유')
    expect(page.locator('#databaseParentShareLegacyNotice')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.locator('#inspector')).to_be_hidden()
    page.evaluate('closeDatabaseFullPage()')
    page.locator('#shareDrawerBtn').click()
    expect(page.locator('#databaseParentShareLegacyNotice')).to_be_hidden()


@pytest.mark.integration
@pytest.mark.regression
def test_collaborative_canvas_and_topbar_match_reference_geometry(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False) as (origin, password, _authority, _workspace_id, page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width':1013,'height':847})
                page.goto(origin + '/page/' + page_id, wait_until='domcontentloaded')
                page.locator('#userId').fill('owner')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                expect(page.locator('#pageTitle')).to_be_enabled(timeout=30_000)
                page.wait_for_function('() => typeof State !== "undefined" && State.navigation.enabled && State.navigation.collaborativeSharing && State.liveCollaboration.crdtReady', timeout=30_000)
                # The signed-in Notion reference used a 255px resized sidebar at 1013px.
                page.evaluate('() => setSidebarWidth(255, false)')
                geometry = page.evaluate("""() => {
                  const rect = selector => {
                    const element = document.querySelector(selector);
                    if (!element || getComputedStyle(element).display === 'none') return null;
                    const bounds = element.getBoundingClientRect();
                    return {left:bounds.left,right:bounds.right,width:bounds.width};
                  };
                  return {sidebar:rect('.sidebar'),main:rect('#workspaceMain'),
                    canvas:rect('#pageView'),title:rect('#pageTitle'),
                    breadcrumb:rect('#breadcrumbWrap'),share:rect('#shareDrawerBtn'),
                    link:rect('#pageCopyLinkBtn'),favorite:rect('#favoritePageBtn'),
                    more:rect('#pageMoreBtn'),viewport:innerWidth};
                }""")
                _screenshot(page, tmp_path, 'notion_collaborative_canvas_1013.png')
                assert geometry['main']['left'] == geometry['sidebar']['right']
                assert geometry['sidebar']['right'] == 255
                assert abs(geometry['canvas']['left'] - 355) <= 2, geometry
                assert abs(geometry['title']['left'] - 355) <= 2, geometry
                assert geometry['more']['right'] <= geometry['viewport']
            finally:
                browser.close()

