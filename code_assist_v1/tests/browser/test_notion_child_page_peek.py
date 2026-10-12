import os

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _seed_blocks, _screenshot


def _child(page):
    return page.evaluate("""async () => {
      const data = await api('/api/notion/pages', {
        method: 'POST', body: {title: '미리 볼 하위 페이지', parent_id: State.activePageId}
      });
      await loadPages();
      return data.page.id;
    }""")


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("block_type,open_selector", [
    ("child_page", "[data-child-page]"),
    ("link_to_page", "[data-linked-page-open]"),
])
def test_ui_child_and_link_pages_offer_side_peek(document_page, tmp_path, block_type, open_selector):
    from playwright.sync_api import expect

    page = document_page
    child = _child(page)
    parent_url = page.url
    parent = page.evaluate("() => State.activePageId")
    _seed_blocks(page, [{"type": block_type, "content": "미리 볼 하위 페이지", "props": {"page_id": child}}])
    row = page.locator("#blocks .child-page-row")
    row.hover()
    button = row.get_by_role("button", name="미리 볼 하위 페이지 사이드 보기로 열기", exact=True)
    expect(button).to_be_visible()
    expect(button).to_have_css("opacity", "1")
    button.click()
    expect(page.locator("#sidePeek")).to_be_visible()
    expect(page.locator("#sidePeekTitle")).to_have_value("미리 볼 하위 페이지")
    assert page.url == parent_url
    assert page.evaluate("() => State.activePageId") == parent
    assert page.evaluate("() => State.sidePeek.pageId") == child
    _screenshot(page, tmp_path, f"{block_type}-side-peek.png")
    page.locator("#sidePeekCloseBtn").click()
    expect(page.locator("#sidePeek")).to_be_hidden()
    row.locator(open_selector).click(modifiers=["Alt"])
    expect(page.locator("#sidePeek")).to_be_visible()
    assert page.url == parent_url
    page.locator("#sidePeekCloseBtn").click()
    # Reading does not require write access, and keyboard users can discover it.
    page.evaluate("() => { State.pageData.page.can_edit=false; renderBlocks(); }")
    row.locator(open_selector).focus()
    expect(row.locator(open_selector)).to_be_focused()
    expect(button).to_have_css("opacity", "1")
    button.press("Enter")
    expect(page.locator("#sidePeek")).to_be_visible()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_database_side_peek_remains_visible_in_collaborative_presentation(document_page):
    from playwright.sync_api import expect

    page = document_page
    child = _child(page)
    parent_url = page.url
    _seed_blocks(page, [{"type": "table", "content": "테스트 DB", "props": {
        "rows": [{"title": "미리 볼 하위 페이지", "page_id": child}],
    }}])
    # Visual selector regression only; actual authorization remains server-owned.
    page.evaluate("() => document.body.classList.add('collaborative-workspace-ui')")
    button = page.locator("#blocks .database-side-peek")
    expect(button).to_be_visible()
    expect(button).to_have_attribute("title", "사이드 보기로 열기")
    button.click()
    expect(page.locator("#sidePeek")).to_be_visible()
    expect(page.locator("#sidePeekTitle")).to_have_value("미리 볼 하위 페이지")
    assert page.url == parent_url
    assert page.evaluate("() => State.sidePeek.databaseContext.rowIndex") == 0


@pytest.mark.integration
@pytest.mark.regression
def test_ui_legacy_database_side_peek_date_picker_continues_and_closes(document_page):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    child = _child(page)
    _seed_blocks(page, [{"type": "table", "content": "날짜 선택 검증", "props": {
        "rows": [{"title": "항목", "page_id": child}],
    }}])
    page.locator("#blocks .database-side-peek").click()
    expect(page.locator("#sidePeek")).to_be_visible()
    date_button = page.locator('#sidePeek [data-db-date-picker="date"]')
    expect(date_button).to_be_enabled()
    date_button.click()
    picker = page.locator(".database-date-picker")
    expect(picker.locator("[data-date-day]")).to_have_count(42)
    help_button = picker.locator("[data-date-help]")
    expect(help_button).to_be_visible()
    help_button.click()
    expect(help_button).to_have_attribute("aria-expanded", "true")
    expect(picker.locator("#databaseDateReminderHelp")).to_contain_text("알림 시간도 함께 갱신")
    help_button.click()
    expect(help_button).to_have_attribute("aria-expanded", "false")
    picker.locator("[data-date-start]").fill("2026-09-21")
    picker.locator("[data-date-start]").press("Tab")
    page.wait_for_function("() => State.pageData.blocks[0].props.rows[0].date === '2026-09-21'")
    expect(picker).to_be_visible()
    picker.locator("[data-date-end-toggle]").click()
    page.wait_for_function("() => State.pageData.blocks[0].props.rows[0].date?.end === '2026-09-21'")
    expect(picker).to_be_visible()
    format_toggle = picker.locator('[data-date-format-toggle]')
    format_toggle.click()
    choice_menu = page.locator('.database-date-choice-menu')
    expect(choice_menu.locator('[data-date-choice]')).to_have_count(6)
    expect(format_toggle).to_have_attribute('aria-expanded', 'true')
    choice_menu.locator('[data-date-choice="short"]').click()
    page.wait_for_function("() => State.pageData.blocks[0].props.rows[0].date?.date_format === 'short'")
    expect(choice_menu).to_have_count(0)
    format_toggle.click()
    choice_menu.locator('[data-date-choice="full"]').click()
    page.wait_for_function("() => !State.pageData.blocks[0].props.rows[0].date?.date_format")
    # Legacy rows retain their separate reminder command. The collaborative
    # date cell uses this picker for both format and reminder choices.
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_function("""() => {
      const rect=document.querySelector('.database-date-picker')?.getBoundingClientRect();
      return rect && rect.left>=0 && rect.right<=innerWidth && rect.top>=0 && rect.bottom<=innerHeight;
    }""", timeout=5000)
    bounds = picker.evaluate("""element => {
      const rect=element.getBoundingClientRect();
      return {left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom,
        width:innerWidth,height:innerHeight};
    }""")
    assert bounds["left"] >= 0 and bounds["right"] <= bounds["width"]
    assert bounds["top"] >= 0 and bounds["bottom"] <= bounds["height"]
    format_toggle.click()
    choice_bounds = choice_menu.bounding_box()
    assert choice_bounds and choice_bounds['x'] >= 0
    assert choice_bounds['x'] + choice_bounds['width'] <= 390
    if os.environ.get('NOTION_DATE_CHOICE_SCREENSHOT'):
        page.screenshot(path=os.environ['NOTION_DATE_CHOICE_SCREENSHOT'])
    choice_menu.locator('[data-date-choice="full"]').press('Escape')
    expect(choice_menu).to_have_count(0)
    help_button.click()
    page.wait_for_function("""() => {
      const rect=document.querySelector('.database-date-picker')?.getBoundingClientRect();
      return rect && rect.left>=0 && rect.right<=innerWidth && rect.top>=0 && rect.bottom<=innerHeight;
    }""", timeout=5000)
    expect(picker.locator("#databaseDateReminderHelp")).to_be_visible()
    page.locator("#sidePeekCloseBtn").click()
    expect(page.locator("#sidePeek")).to_be_hidden()
    expect(picker).to_have_count(0)
    page.reload()
    page.locator("#blocks .database-side-peek").click()
    expect(date_button).to_contain_text("21일")
    assert page.evaluate("() => State.pageData.blocks[0].props.rows[0].date") == {
        "start": "2026-09-21", "end": "2026-09-21",
    }
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_ui_grid_date_picker_survives_same_value_redraw_and_closes_on_changed_cell(document_page):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    child = _child(page)
    _seed_blocks(page, [{"type": "table", "content": "날짜 다시 그리기", "props": {
        "rows": [{"title": "항목", "page_id": child, "date": "2026-09-16"}],
    }}])
    button = page.locator('#blocks [data-db-date-picker="date"]').first
    button.click()
    picker = page.locator(".database-date-picker")
    expect(picker).to_be_visible()
    page.evaluate("renderBlocks()")
    expect(picker).to_be_visible()
    assert page.evaluate("""() => activeDatabaseDatePickerAnchor?.isConnected
      && activeDatabaseDatePickerAnchor?.getAttribute('aria-expanded') === 'true'""")
    picker.locator('[data-date-day="2026-09-17"]').click()
    page.wait_for_function("""() => {
      const value=State.pageData.blocks[0].props.rows[0].date;
      return (typeof value==='object'?value?.start:value)==='2026-09-17';
    }""")
    expect(picker).to_be_visible()
    page.evaluate("""() => {
      State.pageData.blocks[0].props.rows[0].date='2026-09-18';
      renderBlocks();
    }""")
    expect(picker).to_have_count(0)
    button.click()
    expect(picker).to_be_visible()
    page.evaluate("""() => {
      State.pageData.blocks[0].props.rows[0]._canonical_cell_versions={date:1};
      renderBlocks();
    }""")
    expect(picker).to_have_count(0)
    assert not errors, errors
