import re

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _seed_blocks, _screenshot


@pytest.mark.integration
@pytest.mark.regression
def test_ui_page_word_count_is_body_only_and_topbar_only(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    page_id = page.evaluate("() => State.activePageId")
    row = page.locator(f'#ownPages .page-row[data-page-id="{page_id}"]')
    page.locator('#pageTitle').fill('Codex UI 비교 — 슬래시·메뉴 2026-09-16')
    _seed_blocks(page, [{"type": "paragraph", "content": "one two"}])
    row.click(button='right')
    footer = page.locator('#pageMenu .menu-foot')
    expect(footer).to_be_visible()
    expect(footer).not_to_contain_text('단어')
    expect(page.locator('#pageMenu .page-menu-title-stats')).to_have_count(0)
    page.locator('#pageMenu').press('Escape')
    page.locator('#pageMoreBtn').click()
    expect(page.locator('#pageMenu .page-menu-word-count')).to_have_text('2개의 단어')
    page.locator('#pageMenu').press('Escape')
    _seed_blocks(page, [
        {"type": "paragraph", "content": "앞 문장 /"},
        {"type": "paragraph", "content": "UI-PARITY-DB-TARGET-20260927"},
    ])
    page.locator('#pageMoreBtn').click()
    expect(page.locator('#pageMenu .page-menu-word-count')).to_have_text('7개의 단어')
    _screenshot(page, tmp_path, 'notion_topbar_body_word_count.png')
    page.locator('#pageMenu').press('Escape')


@pytest.mark.integration
@pytest.mark.regression
def test_ui_page_row_pointer_and_keyboard_menus_keep_their_focus_modes(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page_id = page.evaluate("() => State.activePageId")
    row = page.locator(f'.page-row[data-page-id="{page_id}"]:not(.compact)').first
    expect(row).to_be_visible()
    bounds = row.bounding_box()
    assert bounds
    click_x = bounds["x"] + min(bounds["width"] / 2, 105)
    click_y = bounds["y"] + bounds["height"] / 2
    row.click(button="right", position={"x": click_x - bounds["x"],
                                        "y": click_y - bounds["y"]})
    menu = page.locator("#pageMenu")
    expect(menu).to_be_visible()
    expect(menu.locator('[data-page-action="favorite"]')).to_have_attribute('role', 'menuitem')
    assert menu.get_attribute("data-page-id") == page_id
    assert page.evaluate("() => document.activeElement === document.querySelector('#pageMenu')")
    menu_bounds = menu.bounding_box()
    assert menu_bounds and abs(menu_bounds["x"] - click_x) <= 3
    assert abs(menu_bounds["y"] - click_y) <= 3
    page.screenshot(path=str(tmp_path / "page-row-right-click-menu.png"))
    page.evaluate("renderPageLists()")
    expect(menu).to_be_visible()
    assert page.evaluate("() => document.activeElement === document.querySelector('#pageMenu')")
    menu.press("ArrowDown")
    assert page.evaluate("() => document.activeElement?.dataset?.pageAction === 'favorite'")
    menu.press("Escape")
    expect(menu).to_be_hidden()
    assert row.evaluate("element => document.activeElement === element")

    more = row.locator("[data-page-menu]")
    row.hover()
    more.click()
    expect(menu).to_be_visible()
    assert page.evaluate("() => document.activeElement === document.querySelector('#pageMenu')")
    menu.press("Escape")
    expect(menu).to_be_hidden()

    more.focus()
    more.press("Enter")
    expect(menu).to_be_visible()
    assert page.evaluate("() => document.activeElement?.dataset?.pageAction === 'favorite'")
    menu.press("Escape")
    expect(menu).to_be_hidden()
    assert more.evaluate("element => document.activeElement === element")

    page.locator('#pageMoreBtn').click()
    expect(menu).to_have_attribute('data-surface', 'page')
    topbar_actions = menu.locator('[data-page-action]').evaluate_all(
        'buttons => buttons.map(button => button.dataset.pageAction)')
    assert topbar_actions.index('copy_link') < topbar_actions.index('small_text')
    assert topbar_actions.index('small_text') < topbar_actions.index('lock')
    assert all(action not in topbar_actions for action in ('rename', 'new_tab', 'side_peek'))
    expect(menu.locator('[data-page-action="history"]')).to_contain_text('버전 기록')
    menu.press('Escape')
    row.click(button='right')
    expect(menu).to_have_attribute('data-surface', 'row')
    assert all(menu.locator(f'[data-page-action="{action}"]').count() == 1
               for action in ('rename', 'new_tab', 'side_peek'))
    menu.press('Escape')
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_ui_page_row_compact_breadcrumb_right_click_uses_page_menu(document_page):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page_id = page.evaluate("() => State.activePageId")
    page.locator('#breadcrumbWrap').hover()
    row = page.locator(f'#breadcrumbHoverPages .page-row.compact[data-page-id="{page_id}"]')
    expect(row).to_be_visible()
    expect(row.locator('[data-page-menu]')).to_have_count(0)
    row.click(button='right')
    menu = page.locator('#pageMenu')
    expect(menu).to_be_visible()
    expect(menu).to_have_attribute('data-page-id', page_id)
    expect(menu).to_have_attribute('data-surface', 'row')
    expect(menu.locator('[data-page-action="copy_link"]')).to_have_count(1)
    page.evaluate('renderBreadcrumbPopover(); renderPageLists()')
    expect(menu).to_be_visible()
    assert page.evaluate("() => document.querySelector('#pageMenu').pageMenuAnchor?.isConnected")
    menu.press('Escape')
    expect(menu).to_be_hidden()
    assert page.evaluate("""id => {
      const row = document.querySelector(`#breadcrumbHoverPages .page-row.compact[data-page-id="${id}"]`);
      return document.activeElement === (row?.getClientRects().length
        ? row : document.querySelector('#breadcrumb .breadcrumb-segment'));
    }""", page_id)
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_ui_favorite_row_uses_its_own_menu_actions_without_a_row_star(document_page):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page_id = page.evaluate("() => State.activePageId")
    ordinary = page.locator(f'#ownPages .page-row[data-page-id="{page_id}"]')
    ordinary.click(button="right")
    menu = page.locator("#pageMenu")
    expect(menu).to_be_visible()
    expect(menu.locator('[data-page-action="duplicate"]')).to_have_count(1)
    expect(menu.locator('[data-page-action="trash"]')).to_have_count(1)
    menu.locator('[data-page-action="favorite"]').click()

    favorite = page.locator(f'#favoritePages .page-row[data-page-id="{page_id}"]')
    expect(favorite).to_be_visible()
    expect(favorite.locator('[data-page-child]')).to_have_count(1)
    expect(favorite.locator('[data-page-menu]')).to_have_attribute(
        "aria-label", "제거, 이름 바꾸기 등…")
    expect(page.locator(f'.page-row[data-page-id="{page_id}"] .page-favorite-mark')).to_have_count(0)

    favorite.click(button="right")
    expect(menu).to_be_visible()
    expect(menu).to_have_attribute("data-surface", "favorite")
    expect(menu.locator('[data-page-action="favorite"]')).to_contain_text("즐겨찾기 해제")
    expect(menu.locator('[data-page-action="duplicate"]')).to_have_count(0)
    expect(menu.locator('[data-page-action="trash"]')).to_have_count(0)
    expect(menu.locator('[data-page-action="rename"]')).to_have_count(1)
    expect(menu.locator('[data-page-action="move"]')).to_have_count(1)
    page.evaluate("renderPageLists()")
    expect(menu).to_be_visible()
    expect(menu).to_have_attribute("data-surface", "favorite")
    assert page.evaluate("""() => document.querySelector('#pageMenu').pageMenuAnchor?.isConnected
      && document.querySelector('#pageMenu').pageMenuAnchor?.closest('#favoritePages') != null
      && document.activeElement === document.querySelector('#pageMenu')""")
    menu.press("Escape")
    expect(menu).to_be_hidden()
    assert favorite.evaluate("element => document.activeElement === element")

    favorite.hover()
    favorite.locator('[data-page-menu]').click()
    expect(menu).to_have_attribute("data-surface", "favorite")
    expect(menu.locator('[data-page-action="duplicate"]')).to_have_count(0)
    menu.press("Escape")

    favorite.focus()
    page.evaluate('renderPageLists()')
    assert favorite.evaluate('element => document.activeElement === element')
    favorite.hover()
    favorite.locator('[data-page-child]').focus()
    page.evaluate('renderPageLists()')
    assert favorite.locator('[data-page-child]').evaluate('element => document.activeElement === element')

    ordinary.click(button="right")
    expect(menu).to_have_attribute("data-surface", "row")
    expect(menu.locator('[data-page-action="duplicate"]')).to_have_count(1)
    expect(menu.locator('[data-page-action="trash"]')).to_have_count(1)
    menu.press("Escape")
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_ui_favorite_parent_expands_children_and_child_keeps_normal_actions(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    parent_id = page.evaluate("() => State.activePageId")
    parent = page.locator(f'#ownPages .page-row[data-page-id="{parent_id}"]')
    parent.click(button="right")
    page.locator('#pageMenu [data-page-action="favorite"]').click()
    favorite_parent = page.locator(f'#favoritePages .page-row[data-page-id="{parent_id}"]')
    expect(favorite_parent).to_be_visible()

    favorite_parent.hover()
    favorite_parent.locator('[data-page-child]').click()
    expect(page.locator('#sidePeek')).to_be_visible()
    assert page.evaluate("() => State.sidePeek.draft?.context?.parentId") == parent_id
    page.wait_for_function('() => Boolean(State.sidePeek.pageId)', timeout=20_000)
    blank_child_id = page.evaluate('() => State.sidePeek.pageId')
    expect(page.locator(f'#favoritePages .page-row[data-page-id="{blank_child_id}"]')).to_be_visible()
    expect(page.locator(f'#ownPages .page-row[data-page-id="{blank_child_id}"]')).to_be_visible()
    expect(favorite_parent).to_have_attribute('aria-expanded', 'true')
    expect(page.locator('#sidePeekTitle')).to_have_attribute('placeholder', '새 페이지')
    page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
    expect(page.locator('#sidePeek')).to_be_hidden()
    expect(page.locator('.page-draft-row')).to_have_count(0)
    expect(page.locator(f'#ownPages .page-row[data-page-id="{blank_child_id}"]')).to_have_count(0)
    with page.expect_popup() as popup:
        page.evaluate('(id) => window.open(pageUrl(id), "_blank")', blank_child_id)
    archived = popup.value
    expect(archived.locator('#archivedPageNotice')).to_be_visible(timeout=20_000)
    archived.close()
    with page.expect_popup() as popup:
        page.evaluate('(ids) => window.open(pageUrl(ids.parent) + "?p=" + encodeURIComponent(ids.child) + "&showMoveTo=true", "_blank")',
                      {'parent': parent_id, 'child': blank_child_id})
    archived_modal = popup.value
    expect(archived_modal.locator('#sidePeekArchivedNotice')).to_be_visible(timeout=20_000)
    expect(archived_modal.locator('#sidePeekArchivedTitle')).to_have_text('새 페이지')
    archived_modal.close()

    favorite_parent.hover()
    favorite_parent.locator('[data-page-child]').click()
    expect(page.locator('#sidePeek')).to_be_visible()
    page.locator('#sidePeekTitle').fill('즐겨찾기 하위 검증')
    page.locator('#sidePeekTitle').press('Tab')
    child = page.locator('#favoritePages .page-row').filter(has_text='즐겨찾기 하위 검증')
    expect(child).to_be_visible(timeout=20_000)
    expect(favorite_parent).to_have_class(re.compile(r'\bhas-children\b'))
    expect(favorite_parent).to_have_attribute('aria-expanded', 'true')
    expect(child).to_have_attribute('aria-level', '2')
    page.screenshot(path=str(tmp_path / 'favorite-page-tree.png'))
    page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
    expect(page.locator('#sidePeek')).to_be_hidden()

    child.click(button='right')
    menu = page.locator('#pageMenu')
    expect(menu).to_have_attribute('data-surface', 'row')
    expect(menu.locator('[data-page-action="duplicate"]')).to_have_count(1)
    expect(menu.locator('[data-page-action="trash"]')).to_have_count(1)
    expect(child.locator('[data-page-menu]')).to_have_attribute('aria-label', '제거, 이름 바꾸기 등…')
    page.evaluate('renderPageLists()')
    expect(menu).to_be_visible()
    assert page.evaluate("""() => document.querySelector('#pageMenu').pageMenuAnchor?.isConnected
      && document.querySelector('#pageMenu').pageMenuAnchor?.closest('#favoritePages') != null""")
    menu.press('Escape')

    favorite_parent.hover()
    favorite_parent.locator('[data-page-toggle]').click()
    expect(child).to_have_count(0)
    expect(favorite_parent).to_have_attribute('aria-expanded', 'false')
    ordinary_child = page.locator('#ownPages .page-row').filter(has_text='즐겨찾기 하위 검증')
    expect(ordinary_child).to_be_visible()
    expect(parent).to_have_attribute('aria-expanded', 'true')

    page.reload(wait_until='domcontentloaded')
    expect(favorite_parent).to_be_visible(timeout=20_000)
    expect(favorite_parent).to_have_attribute('aria-expanded', 'false')
    expect(child).to_have_count(0)
    expect(ordinary_child).to_be_visible()
    favorite_parent.hover()
    favorite_parent.locator('[data-page-toggle]').click()
    expect(child).to_be_visible()
    parent.hover()
    parent.locator('[data-page-toggle]').click()
    expect(ordinary_child).to_have_count(0)
    expect(parent).to_have_attribute('aria-expanded', 'false')
    expect(child).to_be_visible()
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_ui_child_page_draft_keeps_input_after_rejected_create_and_retries_once(document_page):
    from playwright.sync_api import expect

    page = document_page
    parent_id = page.evaluate('() => State.activePageId')
    attempts = []
    reject = {'enabled': True}

    def create_route(route):
        if route.request.method != 'POST':
            route.continue_()
            return
        attempts.append(route.request.post_data_json)
        if reject['enabled']:
            route.fulfill(status=403, json={'error': '권한이 회수되었습니다.'})
        else:
            route.continue_()

    page.route('**/api/notion/pages', create_route)
    parent = page.locator(f'#ownPages .page-row[data-page-id="{parent_id}"]')
    parent.hover()
    parent.locator('[data-page-child]').click()
    peek = page.locator('#sidePeek')
    expect(peek).to_be_visible()
    title = page.locator('#sidePeekTitle')
    body = page.locator('#sidePeekBlocks [data-side-text="0"]')
    title.fill('복구할 하위 페이지')
    body.fill('권한 오류 뒤에도 남을 본문')
    expect(page.locator('#sidePeekDraftStatus')).to_contain_text('저장하지 못했습니다.', timeout=20_000)
    assert page.evaluate('() => State.sidePeek.draft?.phase') == 'failed'
    expect(title).to_have_value('복구할 하위 페이지')
    expect(body).to_have_value('권한 오류 뒤에도 남을 본문')
    expect(page.locator(f'#ownPages .page-draft-row[data-draft-parent-id="{parent_id}"]')).to_be_visible()

    page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
    expect(peek).to_be_visible()
    expect(title).to_have_value('복구할 하위 페이지')
    expect(body).to_have_value('권한 오류 뒤에도 남을 본문')
    reject['enabled'] = False
    page.locator('#sidePeekDraftRetry').click()
    page.wait_for_function('() => Boolean(State.sidePeek.pageId) && !State.sidePeek.dirty', timeout=20_000)
    child_id = page.evaluate('() => State.sidePeek.pageId')
    assert child_id != parent_id
    assert len(attempts) >= 2
    assert len({request['operation_id'] for request in attempts}) == 1
    page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
    expect(peek).to_be_hidden()
    child = page.locator(f'#ownPages .page-row[data-page-id="{child_id}"]')
    expect(child).to_be_visible()
    child.click()
    expect(page.locator('#pageTitle')).to_have_value('복구할 하위 페이지')
    expect(page.locator('#blocks .block-editor')).to_have_text('권한 오류 뒤에도 남을 본문')


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize(('theme', 'width'), [('light', 1280), ('dark', 390)])
def test_ui_r5_other_parent_child_lost_reply_retains_input_and_one_parent_link(
    document_page, tmp_path, theme, width,
):
    """A successful create with a lost reply must retry the chosen sidebar parent."""
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({'width': width, 'height': 844})
    page.evaluate('theme => applyColorTheme(theme)', theme)
    source_id = page.evaluate('() => State.activePageId')
    _seed_blocks(page, [{'type': 'paragraph', 'content': '열린 문서는 그대로 보존'}])
    setup = page.evaluate("""async source => {
      const parent = await api('/api/notion/pages', {method:'POST', body:{title:'다른 부모 대상'}});
      await loadPages();
      return {parentId:parent.page.id, source:await api('/api/notion/pages/'+source),
        parent:await api('/api/notion/pages/'+parent.page.id), pages:await api('/api/notion/pages')};
    }""", source_id)
    parent_id = setup['parentId']
    before_ids = {item['id'] for item in setup['pages']['items']}
    attempts = []
    committed = []

    def lose_first_create_reply(route):
        if route.request.method != 'POST':
            route.continue_()
            return
        attempts.append(route.request.post_data_json)
        if not committed:
            response = route.fetch()
            assert response.ok, response.status
            committed.append(response.json()['page']['id'])
            route.abort('failed')
        else:
            route.continue_()

    page.route('**/api/notion/pages', lose_first_create_reply)
    if width <= 720:
        page.locator('#sidebarRestoreBtn').click()
    target = page.locator(f'#ownPages .page-row[data-page-id="{parent_id}"]')
    target.hover()
    target.locator('[data-page-child]').click()
    expect(page.locator('#sidePeek')).to_be_visible()
    expect(page.locator('#sidePeekDraftStatus')).to_contain_text('저장하지 못했습니다.', timeout=20_000)
    assert committed and page.evaluate('() => State.activePageId') == source_id
    title = page.locator('#sidePeekTitle')
    body = page.locator('#sidePeekBlocks [data-side-text="0"]')
    title.fill('응답 유실 뒤 작성한 제목')
    body.fill('원래 부모가 아닌 선택한 부모에 남겨야 하는 본문')
    page.locator('#sidePeekDraftRetry').click()
    page.wait_for_function('() => Boolean(State.sidePeek.pageId) && !State.sidePeek.dirty', timeout=20_000)
    assert page.evaluate('() => State.sidePeek.pageId') == committed[0]
    assert len(attempts) >= 2 and all(request == attempts[0] for request in attempts)
    assert attempts[0]['parent_id'] == parent_id
    expect(title).to_have_value('응답 유실 뒤 작성한 제목')
    expect(body).to_have_value('원래 부모가 아닌 선택한 부모에 남겨야 하는 본문')
    if width <= 720:
        page.locator('#sidePeekCloseBtn').click()
    else:
        page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
    expect(page.locator('#sidePeek')).to_be_hidden()
    result = page.evaluate("""async ids => ({
      child:await api('/api/notion/pages/'+ids.child),
      parent:await api('/api/notion/pages/'+ids.parent),
      source:await api('/api/notion/pages/'+ids.source), pages:await api('/api/notion/pages')
    })""", {'child': committed[0], 'parent': parent_id, 'source': source_id})
    assert {item['id'] for item in result['pages']['items']} == before_ids | {committed[0]}
    assert result['child']['page']['parent_id'] == parent_id
    assert result['child']['page']['title'] == '응답 유실 뒤 작성한 제목'
    assert result['child']['blocks'][0]['content'] == '원래 부모가 아닌 선택한 부모에 남겨야 하는 본문'
    assert result['source']['page'] == setup['source']['page']
    assert result['source']['blocks'] == setup['source']['blocks']
    assert sum(block.get('props', {}).get('page_id') == committed[0] for block in result['parent']['blocks']) == 1
    assert result['parent']['blocks'][:-1] == setup['parent']['blocks']
    _screenshot(page, tmp_path, f'other-parent-lost-reply-{theme}-{width}.png')
    page.reload(wait_until='domcontentloaded')
    page.wait_for_function('expected => State.activePageId === expected && State.pageData?.page?.id === expected', arg=source_id)
    persisted = page.evaluate('async id => api("/api/notion/pages/"+id)', committed[0])
    assert persisted['page']['title'] == result['child']['page']['title']
    assert persisted['blocks'] == result['child']['blocks']


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize(('timing', 'theme', 'width'), [
    ('before_reply', 'light', 1280), ('after_reply', 'light', 1280),
    ('composing', 'light', 1280), ('composing', 'dark', 390),
])
def test_ui_draft_title_input_survives_reserved_page_reply(
    document_page, tmp_path, timing, theme, width,
):
    """Keep the original input control during composition across the creation reply."""
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({'width': width, 'height': 844})
    page.evaluate('theme => applyColorTheme(theme)', theme)
    parent_id = page.evaluate('() => State.activePageId')
    page.evaluate("""() => {
      const original = api;
      window.draftCreateReply = {release:null, serverId:null};
      api = async (path, options) => {
        const result = await original(path, options);
        if (path === '/api/notion/pages' && options?.method === 'POST') {
          window.draftCreateReply.serverId = result.page.id;
          await new Promise(resolve => {window.draftCreateReply.release = resolve;});
        }
        return result;
      };
    }""")
    if width <= 720:
        page.locator('#sidebarRestoreBtn').click()
    parent = page.locator(f'#ownPages .page-row[data-page-id="{parent_id}"]')
    parent.hover()
    parent.locator('[data-page-child]').click()
    expect(page.locator('#sidePeek')).to_be_visible()
    page.wait_for_function('() => Boolean(window.draftCreateReply.release)')
    title = page.locator('#sidePeekTitle')
    if timing == 'composing':
        title.dispatch_event('compositionstart', {'data': ''})
        title.fill('제목 조합 중')
    elif timing == 'before_reply':
        title.fill('응답 전에 입력한 제목')
    page.evaluate('() => window.draftCreateReply.release()')
    page.wait_for_function('() => Boolean(State.sidePeek.pageId) && !State.sidePeek.creationFlight')
    if timing == 'before_reply':
        expect(title).to_have_value('응답 전에 입력한 제목')
    final_title = '응답 뒤 이어 쓴 제목'
    title.fill(final_title)
    assert page.evaluate('() => State.sidePeek.data.page.title') == final_title
    if timing == 'composing':
        assert page.evaluate('() => State.sidePeek.composing') is True
        title.dispatch_event('compositionend', {'data': final_title})
    body = page.locator('#sidePeekBlocks [data-side-text="0"]')
    body.fill('생성 응답 전후에 계속 작성한 본문')
    page.evaluate('() => saveSidePeek()')
    page.wait_for_function('() => !State.sidePeek.dirty && !State.sidePeek.saveInFlight')
    expect(title).to_have_value(final_title)
    child_id = page.evaluate('() => State.sidePeek.pageId')
    assert child_id == page.evaluate('() => window.draftCreateReply.serverId')
    saved = page.evaluate('async id => api("/api/notion/pages/"+id)', child_id)
    assert saved['page']['title'] == final_title
    assert saved['blocks'][0]['content'] == '생성 응답 전후에 계속 작성한 본문'
    _screenshot(page, tmp_path, f'draft-reply-{timing}-{theme}-{width}.png')
