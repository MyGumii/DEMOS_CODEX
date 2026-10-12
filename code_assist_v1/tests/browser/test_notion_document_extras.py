from pathlib import Path
import json
import os

import pytest

from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import (
    _isolated_server,
    _wait_for_notion_ready,
)


@pytest.fixture
def document_page(tmp_path: Path):
    from playwright.sync_api import expect, sync_playwright

    with _isolated_server(tmp_path / "isolated") as (origin, user_id, password):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
            page.goto(origin)
            page.locator("#userId").fill(user_id)
            page.locator("#password").fill(password)
            page.locator("#submitBtn").click()
            _wait_for_notion_ready(page)
            page.locator("#sidebarNewBtn").click()
            page.locator('#newPageMenu [data-new-page-type="page"]').click()
            expect(page.locator("#pageView")).to_be_visible()
            page.wait_for_function("() => State.pageData && !State.saving")
            yield page
            browser.close()


def _seed_blocks(page, blocks):
    page.evaluate("""async blocks => {
      State.pageData.blocks = blocks.map(block => EditorCore.createBlock(block.type, block));
      renderBlocks();
      markDirty();
      await savePage();
    }""", blocks)


def _screenshot(page, tmp_path, name):
    output = Path(os.environ.get("NOTION_DOCUMENT_EXTRAS_ARTIFACT_DIR", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / name))


def _select_first_quote(page, phrase):
    page.evaluate("""phrase => {
      const editor = document.querySelector('#blocks .block-editor');
      const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const offset = node.nodeValue.indexOf(phrase);
        if (offset < 0) continue;
        const range = document.createRange();
        range.setStart(node, offset); range.setEnd(node, offset + phrase.length);
        editor.focus();
        const selection = window.getSelection();
        selection.removeAllRanges(); selection.addRange(range);
        document.dispatchEvent(new Event('selectionchange'));
        return;
      }
      throw Error(`missing quote: ${phrase}`);
    }""", phrase)


@pytest.mark.integration
@pytest.mark.regression
def test_database_toolbar_filter_sort_open_compact_property_panels(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _seed_blocks(page, [{"type": "table", "content": "빠른 필터 정렬", "props": {
        "view": "table",
        "properties": [{"id": "name", "name": "이름", "type": "title"},
                       {"id": "state", "name": "상태", "type": "status"}],
        "rows": [{"name": "첫째", "state": "진행"}, {"name": "둘째", "state": "완료"}],
    }}])
    menu = page.locator("[data-db-view-popover]")
    page.locator("[data-db-filter]").click()
    expect(menu.locator("[data-db-view-filter-panel]")).to_be_visible()
    expect(menu.locator("[data-db-view-filter-search]")).to_be_focused()
    expect(page.locator("[data-db-filter]")).to_have_attribute("aria-expanded", "true")
    expect(menu).to_have_attribute("aria-label", "필터")
    assert menu.evaluate("""element => element.classList.contains('database-view-quick-entry')
      && getComputedStyle(element.querySelector('[data-db-view-filter-panel] > .database-view-popover-head')).display==='none'""")
    _screenshot(page, tmp_path, "database-toolbar-quick-filter.png")
    menu.locator('[data-db-view-filter-choice="name"]').click()
    menu.locator('[data-db-view-filter-value="0"]').fill("첫째")
    menu.locator('[data-db-view-filter-value="0"]').press("Tab")
    expect(page.locator(".table-row:not(.table-header)")).to_have_count(1)
    expect(menu.locator("[data-db-view-filter-panel]")).to_be_visible()

    page.locator("[data-db-sort]").click()
    expect(menu.locator("[data-db-view-sort-panel]")).to_be_visible()
    expect(menu.locator("[data-db-view-sort-search]")).to_be_focused()
    expect(page.locator("[data-db-filter]")).to_have_attribute("aria-expanded", "false")
    expect(page.locator("[data-db-sort]")).to_have_attribute("aria-expanded", "true")
    expect(menu).to_have_attribute("aria-label", "정렬")
    _screenshot(page, tmp_path, "database-toolbar-quick-sort.png")
    menu.locator('[data-db-view-sort-choice="name"]').click()
    menu.locator('[data-db-view-sort-direction="0"]').select_option("desc")
    expect(page.locator(".table-row:not(.table-header)")).to_have_count(1)
    page.locator("[data-db-view-sort-search]").press("Escape")
    expect(menu).to_be_hidden()
    expect(page.locator("[data-db-sort]")).to_be_focused()
    expect(page.locator("[data-db-sort]")).to_have_attribute("aria-expanded", "false")
    page.wait_for_function("() => !State.dirty && !State.saving", timeout=30_000)
    page.reload()
    _wait_for_notion_ready(page)
    expect(page.locator(".table-row:not(.table-header)")).to_have_count(1)
    assert page.evaluate("""() => {
      const block=State.pageData.blocks.find(item=>item.type==='table');
      return block.props.filters?.[0]?.value==='첫째'
        && block.props.sorts?.[0]?.direction==='desc';
    }""")
    page.set_viewport_size({"width": 390, "height": 800})
    page.locator("[data-db-filter]").click()
    expect(menu.locator("[data-db-view-filter-panel]")).to_be_visible()
    assert menu.evaluate("""element => {
      const rect=element.getBoundingClientRect();
      return rect.left>=0 && rect.right<=innerWidth && rect.top>=0 && rect.bottom<=innerHeight;
    }""")
    page.locator("[data-db-view-filter-search]").press("Escape")
    page.set_viewport_size({"width": 1280, "height": 720})
    page.locator("[data-db-add-view]").click()
    page.locator("#dbViewName").fill("두 번째 표")
    page.get_by_role("button", name="저장", exact=True).click()
    page.locator("[data-db-open-full-page]").click()
    toolbar = page.locator(".page-view.database-full-page .database-toolbar")
    expect(toolbar).to_be_visible()
    expect(toolbar.locator("[data-db-view-id]")).to_have_count(2)
    expect(toolbar.locator("[data-db-view-id]").nth(1)).to_contain_text("두 번째 표")
    toolbar.locator("[data-db-view-id]").first.click()
    expect(toolbar.locator("[data-db-view-id]").first).to_have_attribute("aria-selected", "true")
    expect(toolbar.locator("[data-db-filter] svg")).to_be_visible()
    expect(toolbar.locator("[data-db-sort] svg")).to_be_visible()
    expect(toolbar.locator(".database-create-cluster")).to_be_visible()
    assert toolbar.evaluate("""element => {
      const box = selector => element.querySelector(selector).getBoundingClientRect();
      const tab=box('.database-view-tabs'), filter=box('[data-db-filter]');
      const settings=box('[data-db-view-menu]'), create=box('.database-create-cluster');
      return filter.left>tab.right && create.left>settings.right
        && Math.abs(filter.top-create.top)<8;
    }""")
    _screenshot(page, tmp_path, "database-full-page-toolbar.png")
    toolbar.locator("[data-db-view-menu]").click()
    expect(menu.locator("[data-db-view-group]")).to_be_visible()
    toolbar.locator("[data-db-view-menu]").click()
    toolbar.locator("[data-db-filter]").click()
    expect(menu.locator("[data-db-view-filter-panel]")).to_be_visible()
    menu.locator("[data-db-view-filter-search]").press("Escape")
    expect(menu).to_be_hidden()
    toolbar.locator("[data-db-search]").click()
    expect(toolbar.locator("[data-db-search]")).to_be_focused()
    page.wait_for_function("""() => {
      const input=document.querySelector('.page-view.database-full-page .database-toolbar [data-db-search]');
      return input===document.activeElement && input.getBoundingClientRect().width>100;
    }""", timeout=2_000)
    toolbar.locator("[data-db-search]").fill("검색 결과 없음")
    expect(page.locator(".table-row:not(.table-header)")).to_have_count(0)
    toolbar.locator("[data-db-search]").press("Escape")
    expect(toolbar.locator("[data-db-search]")).to_have_value("")
    page.wait_for_function("""() => {
      const input=document.querySelector('.page-view.database-full-page .database-toolbar [data-db-search]');
      return input!==document.activeElement && input.getBoundingClientRect().width<40;
    }""", timeout=2_000)
    expect(page.locator(".table-row:not(.table-header)")).to_have_count(1)
    page.set_viewport_size({"width": 390, "height": 800})
    expect(toolbar).to_be_visible()
    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
    _screenshot(page, tmp_path, "database-full-page-toolbar-390.png")
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_database_table_view_layout_settings_survive_reload_and_view_switch(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _seed_blocks(page, [{"type": "table", "content": "보기 설정 검증", "props": {
        "view": "table",
        "properties": [
            {"id": "name", "name": "이름", "type": "title"},
            {"id": "note", "name": "설명", "type": "text"},
            {"id": "priority", "name": "우선순위", "type": "number"},
        ],
        "rows": [{"name": "첫 번째 항목", "note": "여러 줄로 보이는 설명 " * 9, "priority": 5}],
    }}])
    table = page.locator(".database-block.table-block")
    assert table.locator('textarea[data-db-field="note"]').count() == 1
    edited_note = "여러 줄로 바꾼 설명\n저장한 다음 줄"
    table.locator('textarea[data-db-field="note"]').fill(edited_note)
    table.locator('textarea[data-db-field="note"]').press("Tab")
    page.wait_for_function("() => !State.dirty && !State.saving", timeout=30_000)
    settings = page.locator("[data-db-view-popover]")
    page.locator("[data-db-view-menu]").click()
    expect(settings).to_be_visible()
    expect(settings.locator("[data-db-view-layout]")).to_be_visible()
    settings.locator('[data-db-view-properties]').click()
    expect(settings.locator('[data-db-view-properties-panel]')).to_be_visible()
    _screenshot(page, tmp_path, 'database-view-properties.png')
    settings.locator('[data-db-view-property-search]').fill('설명')
    expect(settings.locator('[data-db-view-property-row]:visible')).to_have_count(1)
    settings.locator('[data-db-view-visible="note"]').click()
    expect(table.locator('.table-row [data-db-field="note"]')).to_have_count(0)
    expect(settings.locator('[data-db-view-properties-panel]')).to_be_visible()
    expect(settings.locator('[data-db-view-property-search]')).to_have_value('설명')
    settings.locator('[data-db-view-visible="note"]').click()
    expect(table.locator('.table-row [data-db-field="note"]')).to_have_count(1)
    settings.locator('[data-db-view-property-search]').fill('')
    settings.locator('[data-view-property-id="priority"] .database-view-property-handle').press('ArrowUp')
    expect(table.locator('.table-header > button').nth(1)).to_contain_text('우선순위')
    expect(table.locator('.table-header > button').nth(2)).to_contain_text('설명')
    assert page.evaluate("""() => State.pageData.blocks.find(block => block.type === 'table')
      .props.property_order""") == ['name', 'priority', 'note']
    settings.locator('[data-db-view-back]').nth(1).click()
    settings.locator('[data-db-view-group]').click()
    expect(settings.locator('[data-db-view-group-panel]')).to_be_visible()
    _screenshot(page, tmp_path, 'database-view-group.png')
    settings.locator('[data-db-view-group-search]').fill('설명')
    expect(settings.locator('[data-db-view-group-row]:visible')).to_have_count(1)
    settings.locator('[data-db-view-group-choice="note"]').click()
    expect(settings.locator('[data-db-view-group-choice="note"]')).to_have_attribute('aria-pressed','true')
    expect(table.locator('.database-group-label')).to_have_count(1)
    settings.locator('[data-db-view-group-choice=""]').click()
    expect(table.locator('.database-group-label')).to_have_count(0)
    settings.locator('[data-db-view-group-panel] [data-db-view-back]').click()
    settings.locator('[data-db-view-sort]').click()
    expect(settings.locator('[data-db-view-sort-panel]')).to_be_visible()
    _screenshot(page, tmp_path, 'database-view-sort.png')
    settings.locator('[data-db-view-sort-search]').fill('설명')
    expect(settings.locator('[data-db-view-sort-row]:visible')).to_have_count(1)
    settings.locator('[data-db-view-sort-choice="note"]').click()
    expect(settings.locator('[data-db-view-sort-panel]')).to_be_visible()
    expect(settings.locator('[data-db-view-sort-search]')).to_have_value('설명')
    settings.locator('[data-db-view-sort-direction="0"]').select_option('desc')
    expect(settings.locator('[data-db-view-sort-direction="0"]')).to_have_value('desc')
    settings.locator('[data-db-view-sort-remove="0"]').click()
    expect(settings.locator('[data-db-view-sort-direction]')).to_have_count(0)
    settings.locator('[data-db-view-sort-choice="note"]').click()
    settings.locator('[data-db-view-sort-direction="0"]').select_option('desc')
    settings.locator('[data-db-view-sort-panel] [data-db-view-back]').click()
    settings.locator('[data-db-view-filter]').click()
    expect(settings.locator('[data-db-view-filter-panel]')).to_be_visible()
    _screenshot(page, tmp_path, 'database-view-filter.png')
    settings.locator('[data-db-view-filter-search]').fill('설명')
    expect(settings.locator('[data-db-view-filter-row]:visible')).to_have_count(1)
    settings.locator('[data-db-view-filter-choice="note"]').click()
    _screenshot(page, tmp_path, 'database-view-filter-rule.png')
    settings.locator('[data-db-view-filter-value="0"]').fill('없는 문구')
    settings.locator('[data-db-view-filter-value="0"]').press('Tab')
    expect(table.locator('.table-row:not(.table-header)')).to_have_count(0)
    expect(settings.locator('[data-db-view-filter-search]')).to_have_value('설명')
    settings.locator('[data-db-view-filter-remove="0"]').click()
    expect(table.locator('.table-row:not(.table-header)')).to_have_count(1)
    settings.locator('[data-db-view-filter-search]').fill('우선순위')
    settings.locator('[data-db-view-filter-choice="priority"]').click()
    expect(settings.locator('[data-db-view-filter-value="0"]')).to_have_attribute('type', 'number')
    settings.locator('[data-db-view-filter-value="0"]').fill('99')
    settings.locator('[data-db-view-filter-value="0"]').press('Tab')
    expect(table.locator('.table-row:not(.table-header)')).to_have_count(0)
    settings.locator('[data-db-view-filter-remove="0"]').click()
    expect(table.locator('.table-row:not(.table-header)')).to_have_count(1)
    settings.locator('[data-db-view-filter-advanced]').click()
    expect(page.locator('#modalBody .database-rules-note')).to_contain_text('AND/OR 조건 묶음')
    page.locator('#modalActions button').filter(has_text='취소').click()
    page.locator('[data-db-view-menu]').click()
    settings.locator("[data-db-view-layout]").click()
    expect(settings.locator("[data-db-view-layout-panel]")).to_be_visible()

    for key in ("show_source_title", "show_vertical_lines", "show_page_icons", "wrap_all_content"):
        settings.locator(f'[data-db-view-display="{key}"]').uncheck()
    expect(table.locator("[data-db-title]")).to_be_hidden()
    expect(table.locator(".database-row-icon").first).to_be_hidden()
    expect(table.locator('.table-row [data-db-field="note"]')).to_have_value(edited_note.replace("\n", " "))
    assert table.locator('input[data-db-field="note"]').count() == 1
    assert table.locator('textarea[data-db-field="note"]').count() == 0
    assert table.evaluate("node => getComputedStyle(node.querySelector('.table-row > span')).borderRightWidth") == "0px"
    assert page.evaluate("""() => State.pageData.blocks.find(block => block.type === 'table')
      .props.sorts.some(rule => rule.property === 'note' && rule.direction === 'desc')""") is True
    _screenshot(page, tmp_path, "database-view-layout-settings.png")

    page.wait_for_function("() => !State.dirty && !State.saving", timeout=30_000)
    page.reload()
    _wait_for_notion_ready(page)
    table = page.locator(".database-block.table-block")
    expect(table.locator("[data-db-title]")).to_be_hidden()
    expect(table.locator(".database-row-icon").first).to_be_hidden()
    assert table.locator('input[data-db-field="note"]').count() == 1
    expect(table.locator('input[data-db-field="note"]')).to_have_value(edited_note.replace("\n", " "))
    expect(table.locator('.table-header > button').nth(1)).to_contain_text('우선순위')
    assert page.evaluate("""() => State.pageData.blocks.find(block => block.type === 'table')
      .props.views.find(view => view.id === State.pageData.blocks.find(block => block.type === 'table').props.active_view_id)
      .property_order""") == ['name', 'priority', 'note']
    assert page.evaluate("""() => State.pageData.blocks.find(block => block.type === 'table').props.rows[0].note""") == edited_note
    assert table.evaluate("node => getComputedStyle(node.querySelector('.table-row > span')).borderRightWidth") == "0px"
    page.locator("[data-db-add-view]").click()
    page.locator("#dbViewName").fill("두 번째 표")
    page.get_by_role("button", name="저장", exact=True).click()
    expect(page.locator(".database-block.table-block [data-db-title]")).to_be_visible()
    assert page.locator('textarea[data-db-field="note"]').count() == 1
    page.locator('[data-db-view-id]').first.click()
    expect(page.locator(".database-block.table-block [data-db-title]")).to_be_hidden()
    assert page.locator('input[data-db-field="note"]').count() == 1
    expect(page.locator('.database-block.table-block .table-header > button').nth(1)).to_contain_text('우선순위')
    page.evaluate("() => document.body.classList.add('collaborative-workspace-ui')")
    expect(page.locator('[data-db-view-menu]')).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 700})
    page.evaluate("() => document.documentElement.dataset.theme = 'dark'")
    page.locator('[data-db-view-menu]').click()
    settings.locator('[data-db-view-filter]').click()
    filter_bounds = settings.bounding_box()
    assert filter_bounds and 0 <= filter_bounds['x'] and filter_bounds['x'] + filter_bounds['width'] <= 390
    assert 0 <= filter_bounds['y'] and filter_bounds['y'] + filter_bounds['height'] <= 700
    settings.locator('[data-db-view-filter-panel] [data-db-view-back]').click()
    settings.locator('[data-db-view-sort]').click()
    sort_bounds = settings.bounding_box()
    assert sort_bounds and 0 <= sort_bounds['x'] and sort_bounds['x'] + sort_bounds['width'] <= 390
    assert 0 <= sort_bounds['y'] and sort_bounds['y'] + sort_bounds['height'] <= 700
    settings.locator('[data-db-view-sort-panel] [data-db-view-back]').click()
    settings.locator('[data-db-view-layout]').click()
    bounds = settings.bounding_box()
    assert bounds and 0 <= bounds['x'] and bounds['x'] + bounds['width'] <= 390, bounds
    assert 0 <= bounds['y'] and bounds['y'] + bounds['height'] <= 700, bounds
    _screenshot(page, tmp_path, 'database-view-layout-mobile-dark.png')
    page.keyboard.press('Escape')
    expect(settings.locator('[data-db-view-main]')).to_be_visible()
    page.keyboard.press('Escape')
    expect(settings).to_be_hidden()
    page.evaluate('() => renderBlocks()')
    expect(page.locator('[data-db-view-popover]')).to_be_hidden()
    page.locator('[data-db-view-menu]').click()
    expect(page.locator('[data-db-view-popover]')).to_be_visible()
    page.locator('#pageTitle').click()
    expect(page.locator('[data-db-view-popover]')).to_be_hidden()
    page.evaluate('() => renderBlocks()')
    expect(page.locator('[data-db-view-popover]')).to_be_hidden()
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_database_group_sort_and_manual_order_survive_reload(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{"type":"table","content":"그룹 순서 검증","props":{
        "view":"table",
        "properties":[{"id":"name","name":"이름","type":"title"},
                      {"id":"state","name":"상태","type":"status","options":["완료","진행"]},
                      {"id":"done","name":"확인","type":"checkbox"}],
        "rows":[{"name":"첫째","state":"진행","done":True},
                {"name":"둘째","state":"완료","done":False},
                {"name":"셋째","state":"진행","done":True}],
    }}])
    settings = page.locator('[data-db-view-popover]')
    labels = page.locator('.database-block.table-block .database-group-label')
    page.locator('[data-db-view-menu]').click()
    settings.locator('[data-db-view-group]').click()
    settings.locator('[data-db-view-group-choice="state"]').click()
    expect(labels).to_have_count(2)
    assert [label.split()[0] for label in labels.all_text_contents()] == ['진행','완료']
    settings.locator('[data-db-view-group-sort]').select_option('asc')
    assert [label.split()[0] for label in labels.all_text_contents()] == ['완료','진행']
    settings.locator('[data-db-view-group-sort]').select_option('count_desc')
    assert [label.split()[0] for label in labels.all_text_contents()] == ['진행','완료']
    settings.locator('[data-db-view-group-sort]').select_option('manual')
    settings.locator('[data-db-view-group-move="진행"][data-group-direction="1"]').click()
    assert [label.split()[0] for label in labels.all_text_contents()] == ['완료','진행']
    _screenshot(page, tmp_path, 'database-group-sort.png')
    page.set_viewport_size({'width':390,'height':700})
    bounds = settings.bounding_box()
    assert bounds and 0 <= bounds['x'] and bounds['x'] + bounds['width'] <= 390
    assert 0 <= bounds['y'] and bounds['y'] + bounds['height'] <= 700
    _screenshot(page, tmp_path, 'database-group-sort-390.png')
    page.wait_for_function('() => !State.dirty && !State.saving', timeout=30_000)
    page.reload()
    _wait_for_notion_ready(page)
    expect(page.locator('.database-group-label')).to_have_count(2)
    assert [label.split()[0] for label in page.locator('.database-group-label').all_text_contents()] == ['완료','진행']
    assert page.evaluate("""() => {
      const block=State.pageData.blocks.find(item=>item.type==='table');
      return block.props.group_sort==='manual' && block.props.group_order.join(',')==='완료,진행';
    }""")
    page.locator('[data-db-add-view]').click()
    page.locator('#dbViewName').fill('상태 보드')
    page.locator('#dbViewType').select_option('board')
    page.get_by_role('button', name='저장', exact=True).click()
    expect(page.locator('.database-block.board-database')).to_be_visible()
    page.locator('[data-db-view-menu]').click()
    settings.locator('[data-db-view-group]').click()
    expect(settings.locator('[data-db-view-group-sort]')).to_be_visible()
    settings.locator('[data-db-view-group-sort]').select_option('count_desc')
    expect(page.locator('.board-column-title').first).to_contain_text('진행 2')
    page.wait_for_function('() => !State.dirty && !State.saving', timeout=30_000)
    page.reload()
    _wait_for_notion_ready(page)
    expect(page.locator('.board-column-title').first).to_contain_text('진행 2')
    page.locator('[data-board-group]').select_option('done')
    expect(page.locator('.board-column-title').first).to_contain_text('체크됨 2')
    page.wait_for_function('() => !State.dirty && !State.saving', timeout=30_000)
    page.reload()
    _wait_for_notion_ready(page)
    expect(page.locator('.board-column-title').first).to_contain_text('체크됨 2')
    assert page.evaluate("""() => State.pageData.blocks.find(item=>item.type==='table')
      .props.views.find(view=>view.id===State.pageData.blocks.find(item=>item.type==='table').props.active_view_id)
      .board_group_property==='done'""")


@pytest.mark.integration
@pytest.mark.regression
def test_database_nested_filter_group_uses_and_or_after_reload(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{"type": "table", "content": "중첩 필터 검증", "props": {
        "view": "table",
        "properties": [
            {"id": "name", "name": "이름", "type": "title"},
            {"id": "state", "name": "상태", "type": "status", "options": ["진행", "완료"]},
            {"id": "due", "name": "날짜", "type": "date"},
        ],
        "rows": [
            {"name": "Alpha", "state": "진행", "due": "2026-09-17"},
            {"name": "Beta", "state": "진행", "due": "2026-09-16"},
            {"name": "Gamma", "state": "완료", "due": "2026-09-16"},
            {"name": "Delta", "state": "진행", "due": "2026-09-17"},
        ],
    }}])
    table = page.locator('.database-block.table-block')
    page.locator('[data-db-view-menu]').click()
    settings = page.locator('[data-db-view-popover]')
    settings.locator('[data-db-view-filter]').click()
    settings.locator('[data-db-view-filter-advanced]').click()
    editor = page.locator('#modalBody [data-advanced-filter-editor]')
    expect(editor).to_be_visible()
    editor.locator('[data-advanced-add-rule=""]').click()
    editor.locator('[data-advanced-property="0"]').select_option('state')
    editor.locator('[data-advanced-operator="0"]').select_option('equals')
    editor.locator('[data-advanced-filter-value="0"]').select_option('진행')
    editor.locator('[data-advanced-add-group=""]').click()
    editor.locator('[data-advanced-logic="1"]').select_option('or')
    editor.locator('[data-advanced-add-rule="1"]').click()
    editor.locator('[data-advanced-filter-value="1.0"]').fill('Alpha')
    editor.locator('[data-advanced-add-rule="1"]').click()
    editor.locator('[data-advanced-property="1.1"]').select_option('due')
    editor.locator('[data-advanced-operator="1.1"]').select_option('on_or_before')
    editor.locator('[data-advanced-filter-value="1.1"]').fill('2026-09-16')
    editor.locator('[data-advanced-add-group="1"]').click()
    expect(editor.locator('[data-advanced-add-group="1.2"]')).to_have_count(0)
    editor.locator('[data-advanced-remove="1.2"]').click()
    _screenshot(page, tmp_path, 'database-advanced-filter.png')
    previous_viewport = page.viewport_size
    page.set_viewport_size({'width':390, 'height':700})
    bounds = page.locator('#modalBg .modal').bounding_box()
    assert bounds and bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 390
    assert bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 700
    _screenshot(page, tmp_path, 'database-advanced-filter-390.png')
    page.set_viewport_size(previous_viewport)
    page.locator('#modalActions button').filter(has_text='적용').click()
    expect(table.locator('.table-row:not(.table-header)')).to_have_count(2)
    expect(table.locator('.table-row:not(.table-header)')).to_contain_text(['Alpha', 'Beta'])
    page.wait_for_function('() => !State.dirty && !State.saving')
    page.reload()
    _wait_for_notion_ready(page)
    expect(page.locator('.database-block.table-block .table-row:not(.table-header)')).to_have_count(2)
    assert page.evaluate("""() => {
      const block = State.pageData.blocks.find(item => item.type === 'table');
      const view = block.props.views.find(item => item.id === block.props.active_view_id);
      return view.filter_group?.children?.[1]?.logic === 'or';
    }""") is True


@pytest.mark.integration
@pytest.mark.regression
def test_slash_recommendation_keeps_html_detail_and_badge(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({"width": 873, "height": 836})
    editor = page.locator(".block-editor[contenteditable='true']").first
    editor.focus()
    editor.press("/")
    menu = page.locator('#blockMenu[data-mode="slash"]')
    expect(menu).to_be_visible()
    menu.hover()
    expect(menu.locator(".slash-preferences, [data-slash-pin]")).to_have_count(0)
    expect(menu.locator(".block-menu-footer button")).to_have_count(1)
    recommended = menu.locator('[data-command-group][aria-label="추천"]')
    assert recommended.locator('[data-insert-type]').evaluate_all(
        "items => items.map(item => item.dataset.insertType)"
    ) == ["ai_notes", "html_embed", "table", "heading1"]
    html = recommended.locator('[data-insert-type="html_embed"]')
    expect(html.locator('.slash-command-detail')).to_have_text(' · 임베드')
    expect(html.locator('.slash-command-badge')).to_have_text('New')
    expect(recommended.locator('[data-insert-type="table"] .slash-command-detail')).to_have_text(' · 데이터베이스')
    expect(menu.locator('[data-command-group][aria-label="임베드"] [data-insert-type="html_embed"] .slash-command-badge')).to_have_count(0)
    editor.press("Escape")
    expect(menu).to_be_hidden()
    expect(editor).to_have_text("/")


@pytest.mark.integration
@pytest.mark.regression
def test_slash_menu_matches_reference_height_at_1013(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({"width": 1013, "height": 847})
    editor = page.locator(".block-editor[contenteditable='true']").first
    editor.focus()
    editor.press("/")
    menu = page.locator('#blockMenu[data-mode="slash"]')
    expect(menu).to_be_visible()
    geometry = page.evaluate("""() => {
      const menu = document.querySelector('#blockMenu');
      const options = menu.querySelector('#blockMenuOptions');
      const footer = menu.querySelector('.block-menu-footer');
      const bounds = element => {
        const rect = element.getBoundingClientRect();
        return {x:rect.x,y:rect.y,width:rect.width,height:rect.height};
      };
      return {menu:bounds(menu),options:bounds(options),footer:bounds(footer)};
    }""")
    _screenshot(page, tmp_path, "notion_slash_menu_1013.png")
    assert abs(geometry['menu']['width'] - 324) <= 1, geometry
    assert abs(geometry['menu']['height'] - 847 * .4) <= 2, geometry
    assert geometry['footer']['y'] > geometry['options']['y'], geometry
    page.set_viewport_size({"width": 1920, "height": 863})
    page.wait_for_function("""() => Math.abs(
      document.querySelector('#blockMenu').getBoundingClientRect().height - innerHeight * .4
    ) <= 2""")
    tall = menu.bounding_box()
    assert tall and abs(tall['height'] - 863 * .4) <= 2, tall
    page.set_viewport_size({"width": 390, "height": 700})
    mobile = menu.bounding_box()
    assert mobile['x'] >= 8 and mobile['x'] + mobile['width'] <= 382
    assert mobile['y'] >= 8 and mobile['y'] + mobile['height'] <= 692
    editor.press('Escape')
    expect(editor).to_have_text('/')

    page.set_viewport_size({'width': 1280, 'height': 720})
    page.locator('#pageTitle').fill('Codex UI 비교 — 슬래시·메뉴 2026-09-16')
    page.wait_for_function('() => !State.dirty && !State.saving', timeout=30_000)
    _seed_blocks(page, [{'type':'paragraph','content':'앞 문장 /'},
                        {'type':'paragraph','content':''}])
    paragraphs = page.locator('#blocks > .block-row[data-type="paragraph"]')
    first_row = paragraphs.nth(0).bounding_box()
    second_row = paragraphs.nth(1).bounding_box()
    first_editor = paragraphs.nth(0).locator('.block-editor').bounding_box()
    second_editor = paragraphs.nth(1).locator('.block-editor').bounding_box()
    assert first_row and second_row and first_editor and second_editor
    assert abs(first_row['y'] - 268) <= 2
    assert abs(first_row['height'] - 40) <= 2
    assert abs(first_editor['y'] - 274) <= 2
    assert abs(second_row['y'] - 308) <= 2
    assert abs(second_editor['y'] - 314) <= 2
    blank = page.locator('.block-editor[contenteditable="true"]').nth(1)
    expect(blank).to_be_visible()
    blank.focus()
    blank.press('/')
    expect(menu).to_be_visible()
    short = menu.bounding_box()
    assert short and abs(short['y'] - 350) <= 3, short
    assert abs(short['height'] - 288) <= 3, short
    assert abs(short['y'] + short['height'] - 638) <= 3, short
    _screenshot(page, tmp_path, 'notion_slash_menu_720.png')
    blank.press('Escape')
    expect(blank).to_have_text('/')


@pytest.mark.integration
@pytest.mark.regression
def test_slash_unmatched_query_escape_and_heading_survive_reload(document_page):
    from playwright.sync_api import expect

    page = document_page
    editor = page.locator(".block-editor[contenteditable='true']").first
    editor.focus()
    editor.press("/")
    menu = page.locator('#blockMenu[data-mode="slash"]')
    expect(menu).to_be_visible()
    expect(editor).to_have_attribute("role", "combobox")
    expect(editor).to_have_attribute("aria-expanded", "true")
    expect(editor).to_be_focused()

    page.keyboard.type("unsupported-command")
    expect(menu.locator(".menu-empty")).to_be_visible()
    editor.press("Escape")
    expect(menu).to_be_hidden()
    expect(editor).to_have_text("/unsupported-command")
    expect(editor).to_have_attribute("role", "textbox")

    editor.fill("")
    editor.press("/")
    expect(menu).to_be_visible()
    menu.locator('[data-insert-type="heading1"]').first.click()
    heading = page.locator('.block-row[data-type="heading1"] .block-editor').first
    expect(menu).to_be_hidden()
    expect(heading).to_be_focused()
    heading.fill("합성 제목 블록")
    expect(page.locator("#status")).to_have_text("저장됨")
    page.reload()
    _wait_for_notion_ready(page)
    expect(page.locator('.block-row[data-type="heading1"] .block-editor').first).to_have_text("합성 제목 블록")


@pytest.mark.integration
@pytest.mark.regression
def test_icon_picker_recent_choices_and_notion_density(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    assert page.evaluate("() => Boolean(State.user?.id)")
    page.locator("#addIconBtn").click()
    picker = page.locator("#pageIconPicker")
    expect(picker).to_be_visible()
    icon_rect = page.locator("#pageIconBtn").bounding_box()
    popup_rect = page.locator("#modalBg .modal").bounding_box()
    page.wait_for_function("""() => {
      const root=document.querySelector('#pageIconPicker .icon-picker-results');
      const bounds=root.getBoundingClientRect();
      const visible=[...root.querySelectorAll('img')].filter(image=>{
        const r=image.getBoundingClientRect();return r.bottom>bounds.top && r.top<bounds.bottom;
      });
      return visible.length>0 && visible.every(image=>image.complete && image.naturalWidth>0);
    }""")
    _screenshot(page, tmp_path, "notion_icon_picker_initial.png")
    geometry = page.locator("#modalBg .modal").evaluate("""root => ({
      rect:root.getBoundingClientRect().toJSON(),radius:getComputedStyle(root).borderRadius,
      firstCell:root.querySelector('.icon-picker-grid button').getBoundingClientRect().toJSON(),
      search:root.querySelector('.icon-picker-search-field input').getBoundingClientRect().toJSON(),
      anchor:document.querySelector('#pageIconBtn').getBoundingClientRect().toJSON()
    })""")
    output = Path(os.environ.get("NOTION_DOCUMENT_EXTRAS_ARTIFACT_DIR", str(tmp_path)))
    (output / "notion_icon_picker_geometry.json").write_text(json.dumps(geometry, indent=2), encoding="utf-8")
    assert abs((icon_rect["x"] + icon_rect["width"] / 2) - (popup_rect["x"] + popup_rect["width"] / 2)) < 1
    assert abs(popup_rect["y"] - (icon_rect["y"] + icon_rect["height"] + 4)) < 1
    grid = picker.locator(".icon-picker-grid").first
    assert grid.evaluate("element => getComputedStyle(element).gridTemplateColumns.trim().split(/\\s+/).length") == 12
    faces = picker.locator('section[aria-label="사람"] [data-icon-value]')
    expect(faces).to_have_count(85)
    assert faces.evaluate_all("items => items.slice(0, 12).map(item => item.dataset.iconValue)") == [
        "😀", "😃", "😄", "😁", "😆", "😅", "🤣", "😂", "🙂", "🙃", "🫠", "😉",
    ]
    page.wait_for_function("() => [...document.querySelectorAll('#pageIconPicker section[aria-label=\"사람\"] img')].slice(0, 12).every(img => img.complete && img.naturalWidth > 0)")
    assert not picker.locator('section[aria-label="최근 사용한 항목"]').count()
    search = picker.locator('input[aria-label="아이콘 검색"]')
    search.fill("집")
    results = picker.locator('.icon-picker-search-grid [data-icon-value]')
    expect(results).to_have_count(20)
    assert results.evaluate_all("items => items.map(item => item.dataset.iconValue)") == [
        "🏠", "🛖", "🏘️", "🪴", "🪹", "🏚️", "🦞", "😣", "🐈", "🫳",
        "🙃", "👌", "🤌", "🤏", "🐚", "🍵", "🍺", "🏡", "🏮", "🇪🇬",
    ]
    page.wait_for_function("""() => {
      const images=[...document.querySelectorAll('#pageIconPicker .icon-picker-search-grid img')];
      return images.length === 20 && images.every(image => image.complete && image.naturalWidth > 0);
    }""")
    assert abs(page.locator("#modalBg .modal").bounding_box()["height"] - popup_rect["height"]) < 1
    assert not picker.locator('.icon-picker-results section').count()
    expect(picker.locator('.icon-picker-categories')).to_be_hidden()
    _screenshot(page, tmp_path, "notion_icon_search_flat.png")
    search.fill("zzzznonexistent")
    expect(picker.locator('.icon-picker-search-empty')).to_have_text("결과 없음")
    expect(picker.locator('.icon-picker-categories')).to_be_hidden()
    search.fill("")
    expect(picker.locator('section[aria-label="사람"]')).to_be_visible()
    expect(picker.locator('.icon-picker-categories')).to_be_visible()
    picker.locator('[data-icon-value="😀"]').click()
    expect(page.locator("#modalBg")).to_be_hidden()

    page.locator("#pageIconBtn").click()
    recent = page.locator('#pageIconPicker section[aria-label="최근 사용한 항목"]')
    expect(recent).to_be_visible()
    expect(recent.locator('[data-icon-value="😀"]')).to_be_visible()
    assert page.locator('#pageIconPicker [data-icon-category="__recent__"]').count() == 1
    _screenshot(page, tmp_path, "notion_icon_picker_recent_12_columns.png")
    icon_rect = page.locator("#pageIconBtn").bounding_box()
    popup_rect = page.locator("#modalBg .modal").bounding_box()
    assert abs((icon_rect["x"] + icon_rect["width"] / 2) - (popup_rect["x"] + popup_rect["width"] / 2)) < 1
    page.set_viewport_size({"width": 390, "height": 700})
    page.wait_for_function("() => {const r=document.querySelector('#modalBg .modal').getBoundingClientRect();return r.left>=7 && r.right<=innerWidth-7}")
    page.set_viewport_size({"width": 1440, "height": 900})

    picker.locator('[data-icon-tab="vector"]').click()
    expect(picker.locator('[data-icon-skin-toggle]')).to_be_hidden()
    picker.locator('[data-icon-value="ni:doc:default"]').click()
    expect(page.locator("#modalBg")).to_be_hidden()
    page.locator("#pageIconBtn").click()
    picker = page.locator("#pageIconPicker")
    expect(picker.locator('section[aria-label="최근 사용한 항목"] [data-icon-value="ni:doc:default"]')).to_be_visible()
    picker.locator('[data-icon-color-toggle]').click()
    picker.locator('[data-color="blue"]').click()
    expect(picker.locator('section[aria-label="최근 사용한 항목"] [data-icon-value="ni:doc:blue"]')).to_be_visible()
    picker.locator('[data-icon-tab="upload"]').click()
    expect(picker.locator('.icon-picker-upload .icon-file-label')).to_be_visible()
    expect(picker.locator('.icon-picker-credit')).to_be_hidden()
    expect(picker.locator('.icon-picker-upload p')).to_have_text("또는 Ctrl+V 단축키로 이미지나 링크를 붙여넣으세요.")
    assert 188 <= page.locator("#modalBg .modal").bounding_box()["height"] <= 196
    _screenshot(page, tmp_path, "notion_icon_upload_empty.png")


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize(('image_format', 'mime', 'extension'), [
    ('PNG', 'image/png', 'png'), ('JPEG', 'image/jpeg', 'jpg'), ('WEBP', 'image/webp', 'webp'),
])
def test_uploaded_page_icon_survives_reload_in_title_and_sidebar(document_page, image_format, mime, extension):
    from io import BytesIO
    from PIL import Image
    from playwright.sync_api import expect

    page = document_page
    body_before = page.evaluate('() => State.pageData.blocks')
    image = BytesIO()
    Image.new("RGB", (8, 8), "#2585df").save(image, image_format)
    page.locator("#addIconBtn").click()
    picker = page.locator("#pageIconPicker")
    picker.locator('[data-icon-tab="upload"]').click()
    picker.locator('input[aria-label="아이콘 이미지 파일"]').set_input_files({
        "name": f"page-icon.{extension}", "mimeType": mime, "buffer": image.getvalue(),
    })
    expect(picker.locator(".icon-upload-preview")).to_be_visible()
    picker.locator("[data-icon-apply]").click()
    expect(picker).to_be_hidden(timeout=30_000)
    page.wait_for_function("() => State.pageData?.page?.icon?.startsWith('nf:')")
    page_id = page.evaluate("State.activePageId")
    icon = page.evaluate("State.pageData.page.icon")
    expect(page.locator("#pageIconBtn img.notion-icon-image")).to_be_visible()
    expect(page.locator(f'[data-page-id="{page_id}"] img.notion-icon-image').first).to_be_visible()
    expect(page.locator('#pageIconBtn img.notion-icon-image')).to_have_js_property('naturalWidth', 8)

    page.reload()
    _wait_for_notion_ready(page)
    assert page.evaluate("State.pageData.page.icon") == icon
    expect(page.locator("#pageIconBtn img.notion-icon-image")).to_be_visible()
    expect(page.locator(f'[data-page-id="{page_id}"] img.notion-icon-image').first).to_be_visible()
    expect(page.locator('#pageIconBtn img.notion-icon-image')).to_have_js_property('naturalWidth', 8)
    expect(page.locator(f'[data-page-id="{page_id}"] img.notion-icon-image').first).to_have_js_property('naturalWidth', 8)
    assert page.evaluate('() => State.pageData.blocks') == body_before


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("block_type", ["table", "calendar"])
def test_database_row_icon_is_kept_in_page_side_peek(document_page, block_type, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{"type": block_type, "content": "아이콘이 있는 표", "props": {
        "view": "table",
        "properties": [{"id": "name", "name": "이름", "type": "title"}],
        "rows": [{"name": "아이콘 행", "title": "아이콘 행", "date": "2026-10-01",
                  "_canonical_appearance": {"icon": "📌"}},
                 {"name": "비운 아이콘 행", "title": "비운 아이콘 행", "date": "2026-10-02",
                  "icon": "📌", "_canonical_appearance": {"icon": ""}}],
    }}])
    page.evaluate("() => openSidePeek(State.activePageId)")
    row = page.locator('#sidePeekBlocks .side-peek-child').first
    expect(row).to_contain_text("아이콘 행")
    expect(row.locator('img.notion-emoji-image')).to_be_visible()
    expect(row.locator('svg[data-icon-id="doc"]')).to_have_count(0)
    cleared = page.locator('#sidePeekBlocks .side-peek-child').nth(1)
    expect(cleared).to_contain_text("비운 아이콘 행")
    expect(cleared.locator('img.notion-emoji-image')).to_have_count(0)
    expect(cleared.locator('svg[data-icon-id="doc"]')).to_be_visible()
    _screenshot(page, tmp_path, f"side-peek-{block_type}-row-icon.png")


@pytest.mark.integration
@pytest.mark.regression
def test_child_page_reference_keeps_fresh_metadata_while_sidebar_is_filtered(document_page):
    from playwright.sync_api import expect

    page = document_page
    child_id = page.evaluate("""async () => {
      const created = await api('/api/notion/pages', {method:'POST',
        body:{title:'현재 하위 페이지명', icon:'📌', parent_id:State.activePageId}});
      await loadPages();
      return created.page.id;
    }""")
    _seed_blocks(page, [
        {"type": "paragraph", "content": "보존할 이웃 문단"},
        {"type": "child_page", "content": "원문 하위 페이지명", "props": {"page_id": child_id}},
    ])
    page.evaluate("() => openSidePeek(State.activePageId)")
    main = page.locator('#blocks [data-page-reference]').first
    side = page.locator('#sidePeekBlocks [data-page-reference]').first
    expect(main).to_contain_text('현재 하위 페이지명')
    expect(side.locator('img.notion-emoji-image')).to_be_visible()
    original_blocks = page.evaluate("() => JSON.stringify(State.pageData.blocks)")
    main.evaluate("element => { element.dataset.metadataWitness = 'kept'; }")
    page.evaluate("""async () => {
      document.querySelector('#pageSearch').value = '목록 밖 하위 페이지';
      await loadPages();
    }""")
    expect(main).to_contain_text('현재 하위 페이지명')
    expect(main.locator('img.notion-emoji-image')).to_be_visible()
    page.evaluate("""async childId => {
      await api('/api/notion/pages/' + encodeURIComponent(childId), {
        method:'PATCH', body:{title:'바뀐 하위 페이지명', icon:''},
      });
      await loadPages();
    }""", child_id)
    for reference in (main, side):
        expect(reference).to_contain_text('바뀐 하위 페이지명')
        expect(reference.locator('img.notion-emoji-image')).to_have_count(0)
        expect(reference.locator('svg[data-icon-id="doc"]')).to_be_visible()
    expect(main).to_have_attribute('data-metadata-witness', 'kept')
    expect(page.locator('#blocks .block-editor').first).to_have_text('보존할 이웃 문단')
    assert page.evaluate("() => JSON.stringify(State.pageData.blocks)") == original_blocks
    assert not page.evaluate("childId => State.pages.some(item => item.id === childId)", child_id)

    # Body projections/redraws while the sidebar remains filtered must keep
    # the same authorized metadata, rather than falling back to stored text.
    page.evaluate("() => { renderBlocks(); renderSidePeek(); }")
    for reference in (main, side):
        expect(reference).to_contain_text('바뀐 하위 페이지명')
        expect(reference.locator('svg[data-icon-id="doc"]')).to_be_visible()
    assert page.evaluate("() => JSON.stringify(State.pageData.blocks)") == original_blocks


@pytest.mark.integration
@pytest.mark.regression
def test_personal_database_row_page_icon_updates_without_rebuilding_inputs(document_page, tmp_path):
    import json
    from urllib.parse import urlsplit
    from playwright.sync_api import expect

    page = document_page
    child_id = page.evaluate("""async () => {
      const created = await api('/api/notion/pages', {
        method:'POST', body:{title:'실제 아이콘 행', icon:'📌'},
      });
      await loadPages();
      return created.page.id;
    }""")
    _seed_blocks(page, [{"type": "table", "content": "페이지 아이콘 표", "props": {
        "view": "table",
        "properties": [{"id": "name", "name": "이름", "type": "title"}],
        "rows": [{"name": "실제 아이콘 행", "page_id": child_id}],
    }}])
    page.evaluate("() => openSidePeek(State.activePageId)")
    main_icon = page.locator('#blocks .table-row:not(.table-header) .database-row-icon').first
    side_row = page.locator('#sidePeekBlocks .side-peek-child').first
    expect(main_icon.locator('img.notion-emoji-image')).to_be_visible()
    expect(side_row.locator('img.notion-emoji-image')).to_be_visible()
    _screenshot(page, tmp_path, "personal-page-row-icon-before-clear.png")
    old_icon_pages = page.evaluate("() => State.pages")
    original_rows = page.evaluate("() => JSON.stringify(State.pageData.blocks[0].props.rows)")
    page.evaluate("""async () => {
      document.querySelector('#pageSearch').value = '아이콘 행을 제외한 검색';
      await loadPages();
      renderBlocks(); renderSidePeek();
    }""")
    expect(main_icon.locator('img.notion-emoji-image')).to_be_visible()
    expect(side_row.locator('img.notion-emoji-image')).to_be_visible()
    input_identity = page.locator('#blocks [data-db-field="name"]').first
    input_identity.evaluate("element => { element.dataset.iconRefreshWitness = 'kept'; }")

    page.evaluate("""async childId => {
      document.querySelector('#pageSearch').value = '아이콘 행을 제외한 검색';
      await api('/api/notion/pages/' + encodeURIComponent(childId), {
        method:'PATCH', body:{icon:''},
      });
      await loadPages();
    }""", child_id)
    expect(main_icon.locator('img.notion-emoji-image')).to_have_count(0)
    expect(side_row.locator('img.notion-emoji-image')).to_have_count(0)
    expect(main_icon.locator('svg')).to_be_visible()
    expect(side_row.locator('svg')).to_be_visible()
    expect(input_identity).to_have_attribute('data-icon-refresh-witness', 'kept')
    assert not page.evaluate("childId => State.pages.some(item => item.id === childId)", child_id)
    assert page.evaluate("() => JSON.stringify(State.pageData.blocks[0].props.rows)") == original_rows
    _screenshot(page, tmp_path, "personal-page-row-icon-after-clear.png")

    fresh_icon_pages = page.evaluate("async () => (await api('/api/notion/pages')).items")
    held_reads = []
    full_reads = []

    def delay_old_icon_read(route):
        address = urlsplit(route.request.url)
        if address.path != '/api/notion/pages' or route.request.method != 'GET':
            route.continue_()
        elif address.query:
            route.fulfill(status=200, content_type='application/json', body='{"items":[]}')
        else:
            full_reads.append(route)
            if len(full_reads) == 1:
                held_reads.append(route)
            else:
                route.fulfill(status=200, content_type='application/json',
                              body=json.dumps({'items': fresh_icon_pages}))

    page.route('**/api/notion/pages**', delay_old_icon_read)
    page.evaluate("""() => {
      document.querySelector('#pageSearch').value = '이전 필터';
      window.pendingOldIconRead = loadPages();
    }""")
    for _ in range(100):
        if held_reads:
            break
        page.wait_for_timeout(50)
    assert len(held_reads) == 1, 'the old unfiltered icon response must be held'
    page.evaluate("""async () => {
      document.querySelector('#pageSearch').value = '현재 필터';
      await loadPages();
    }""")
    held_reads[0].fulfill(status=200, content_type='application/json',
                         body=json.dumps({'items': old_icon_pages}))
    page.evaluate("async () => { await window.pendingOldIconRead; }")
    expect(main_icon.locator('img.notion-emoji-image')).to_have_count(0)
    expect(side_row.locator('img.notion-emoji-image')).to_have_count(0)
    expect(input_identity).to_have_attribute('data-icon-refresh-witness', 'kept')


@pytest.mark.integration
@pytest.mark.regression
def test_uploaded_page_icon_retries_apply_without_reuploading_file(document_page):
    from io import BytesIO
    from PIL import Image
    from playwright.sync_api import expect

    page = document_page
    page_id = page.evaluate('State.activePageId')
    uploads = []
    failed = []

    def fail_first_apply(route):
        if route.request.method == 'PATCH' and not failed:
            failed.append(route.request.url)
            route.fulfill(status=503, content_type='application/json', body='{"error":"temporary apply failure"}')
        else:
            route.continue_()

    image = BytesIO()
    Image.new('RGB', (8, 8), '#36a782').save(image, 'PNG')
    page.locator('#addIconBtn').click()
    picker = page.locator('#pageIconPicker')
    expect(picker).to_be_visible()
    page.wait_for_function("() => !!State.pageData?.page?.icon && !State.saving")
    page.on('request', lambda request: uploads.append(request.url)
            if request.method == 'POST' and request.url.endswith(f'/api/notion/pages/{page_id}/files') else None)
    page.route(f'**/api/notion/pages/{page_id}', fail_first_apply)
    picker.locator('[data-icon-tab="upload"]').click()
    picker.locator('input[aria-label="아이콘 이미지 파일"]').set_input_files({
        'name': 'retry-icon.png', 'mimeType': 'image/png', 'buffer': image.getvalue(),
    })
    expect(picker.locator('.icon-upload-preview')).to_be_visible()
    picker.locator('[data-icon-apply]').click()
    expect(picker.locator('.icon-picker-status')).to_contain_text('temporary apply failure', timeout=30_000)
    expect(picker).to_be_visible()
    assert len(uploads) == len(failed) == 1
    picker.locator('[data-icon-apply]').click()
    expect(picker).to_be_hidden(timeout=30_000)
    page.wait_for_function("() => State.pageData?.page?.icon?.startsWith('nf:')")
    assert len(uploads) == 1
    expect(page.locator('#pageIconBtn img.notion-icon-image')).to_be_visible()
    page.reload()
    _wait_for_notion_ready(page)
    expect(page.locator('#pageIconBtn img.notion-icon-image')).to_be_visible()


@pytest.mark.integration
@pytest.mark.regression
def test_uploaded_icon_on_blank_child_draft_survives_close_and_reload(document_page):
    from io import BytesIO
    from PIL import Image
    from playwright.sync_api import expect

    page = document_page
    parent_id = page.evaluate("State.activePageId")
    parent = page.locator(f'#ownPages .page-row[data-page-id="{parent_id}"]')
    parent.hover()
    parent.locator('[data-page-child]').click()
    expect(page.locator('#sidePeek')).to_be_visible()
    expect(page.locator('#sidePeekTitle')).to_have_value("")
    page.locator('#sidePeekIconBtn').click()
    picker = page.locator('#pageIconPicker')
    expect(picker).to_be_visible()
    picker.locator('[data-icon-tab="upload"]').click()
    image = BytesIO()
    Image.new("RGB", (8, 8), "#e88631").save(image, "PNG")
    picker.locator('input[aria-label="아이콘 이미지 파일"]').set_input_files({
        "name": "draft-icon.png", "mimeType": "image/png", "buffer": image.getvalue(),
    })
    expect(picker.locator('.icon-upload-preview')).to_be_visible()
    picker.locator('[data-icon-apply]').click()
    expect(picker).to_be_hidden(timeout=30_000)
    page.wait_for_function("() => State.sidePeek.data?.page?.icon?.startsWith('nf:')")
    child_id = page.evaluate('State.sidePeek.pageId')
    icon = page.evaluate('State.sidePeek.data.page.icon')
    expect(page.locator('#sidePeekIconBtn img.notion-icon-image')).to_be_visible()
    expect(page.locator(f'#ownPages .page-row[data-page-id="{child_id}"] img.notion-icon-image')).to_be_visible()

    page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
    expect(page.locator('#sidePeek')).to_be_hidden()
    expect(page.locator(f'#ownPages .page-row[data-page-id="{child_id}"]')).to_be_visible()
    page.reload()
    _wait_for_notion_ready(page)
    expect(page.locator(f'#ownPages .page-row[data-page-id="{child_id}"] img.notion-icon-image')).to_be_visible()
    saved = page.request.get(f"{page.evaluate('location.origin')}/api/notion/pages/{child_id}").json()
    assert saved['page']['icon'] == icon


@pytest.mark.integration
@pytest.mark.regression
def test_blank_child_icon_apply_failure_retries_without_duplicate_upload(document_page):
    from io import BytesIO
    from PIL import Image
    from playwright.sync_api import expect

    page = document_page
    parent_id = page.evaluate('State.activePageId')
    parent = page.locator(f'#ownPages .page-row[data-page-id="{parent_id}"]')
    parent.hover()
    parent.locator('[data-page-child]').click()
    expect(page.locator('#sidePeekTitle')).to_have_value('')
    uploads = []
    failed = []
    page.on('request', lambda request: uploads.append(request.url)
            if request.method == 'POST' and '/api/notion/pages/' in request.url
            and request.url.endswith('/files') else None)

    def fail_first_child_apply(route):
        request = route.request
        body = request.post_data_json if request.method == 'PATCH' else None
        if (request.method == 'PATCH' and not request.url.endswith(f'/{parent_id}')
                and isinstance(body, dict) and 'icon' in body and not failed):
            failed.append(request.url)
            route.fulfill(status=503, content_type='application/json', body='{"error":"temporary draft icon failure"}')
        else:
            route.continue_()

    page.route('**/api/notion/pages/*', fail_first_child_apply)
    page.locator('#sidePeekIconBtn').click()
    picker = page.locator('#pageIconPicker')
    expect(picker).to_be_visible()
    picker.locator('[data-icon-tab="upload"]').click()
    image = BytesIO()
    Image.new('RGB', (8, 8), '#dc7f2a').save(image, 'PNG')
    picker.locator('input[aria-label="아이콘 이미지 파일"]').set_input_files({
        'name': 'retry-draft-icon.png', 'mimeType': 'image/png', 'buffer': image.getvalue(),
    })
    expect(picker.locator('.icon-upload-preview')).to_be_visible()
    picker.locator('[data-icon-apply]').click()
    expect(picker.locator('.icon-picker-status')).to_contain_text('temporary draft icon failure', timeout=30_000)
    expect(picker).to_be_visible()
    assert len(uploads) == len(failed) == 1
    child_id = page.evaluate('State.sidePeek.pageId')
    assert child_id and child_id != parent_id
    expect(page.locator('#sidePeekTitle')).to_have_value('새 페이지')
    picker.locator('[data-icon-apply]').click()
    expect(picker).to_be_hidden(timeout=30_000)
    page.wait_for_function("() => State.sidePeek.data?.page?.icon?.startsWith('nf:')")
    assert len(uploads) == 1
    expect(page.locator('#sidePeekIconBtn img.notion-icon-image')).to_be_visible()
    page.reload()
    _wait_for_notion_ready(page)
    expect(page.locator(f'#ownPages .page-row[data-page-id="{child_id}"] img.notion-icon-image')).to_be_visible()


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize(('image_format', 'mime', 'extension'), [
    ('PNG', 'image/png', 'png'), ('JPEG', 'image/jpeg', 'jpg'), ('WEBP', 'image/webp', 'webp'),
])
def test_uploaded_personal_workspace_icon_reaches_header_and_switcher(document_page, image_format, mime, extension):
    from io import BytesIO
    from PIL import Image
    from playwright.sync_api import expect

    page = document_page
    page.locator('#workspaceName').click()
    page.locator('#workspacePopover [data-workspace-action="settings"]').click()
    button = page.locator('#personalWorkspaceIconBtn')
    expect(button).to_be_enabled()
    button.click()
    picker = page.locator('#workspaceIconPicker')
    expect(picker).to_be_visible()
    picker.locator('[data-icon-tab="upload"]').click()
    image = BytesIO()
    Image.new('RGB', (8, 8), '#915cc8').save(image, image_format)
    picker.locator('input[aria-label="아이콘 이미지 파일"]').set_input_files({
        'name': f'workspace-icon.{extension}', 'mimeType': mime, 'buffer': image.getvalue(),
    })
    expect(picker.locator('.icon-upload-preview')).to_be_visible()
    picker.locator('[data-icon-apply]').click()
    expect(picker).to_be_hidden(timeout=30_000)
    expect(page.locator('#workspaceName .notion-cube img.notion-icon-image')).to_be_visible()
    expect(page.locator('#workspaceName .notion-cube img.notion-icon-image')).to_have_js_property('naturalWidth', 8)
    page.locator('#modalClose').click()
    expect(page.locator('#modalBg')).to_be_hidden()
    page.locator('#workspaceName').click()
    expect(page.locator('#workspacePopover .workspace-switch-avatar img.notion-icon-image').first).to_be_visible()
    expect(page.locator('#workspacePopover .workspace-switch-avatar img.notion-icon-image').first).to_have_js_property('naturalWidth', 8)

    page.reload()
    _wait_for_notion_ready(page)
    expect(page.locator('#workspaceName .notion-cube img.notion-icon-image')).to_be_visible()
    expect(page.locator('#workspaceName .notion-cube img.notion-icon-image')).to_have_js_property('naturalWidth', 8)


@pytest.mark.integration
@pytest.mark.regression
def test_icon_picker_skin_tone_is_persistent_and_saved_icon_renders(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    page.locator("#addIconBtn").click()
    picker = page.locator("#pageIconPicker")
    expect(picker).to_be_visible()
    original = page.evaluate("() => State.pageData.page.icon")
    toggle = picker.locator("[data-icon-skin-toggle]")
    toggle.click()
    menu = picker.locator(".icon-picker-skin-menu")
    expect(menu).to_be_visible()
    expect(menu.locator("[data-skin-tone]")).to_have_count(6)
    page.wait_for_function("""() => {
      const menu = [...document.querySelectorAll('.icon-picker-skin-menu img')];
      const firstRow = [...document.querySelectorAll('.icon-picker-results [data-icon-value] img')].slice(0, 12);
      return menu.length === 6 && firstRow.length === 12
        && [...menu, ...firstRow].every(image => image.complete && image.naturalWidth > 0);
    }""")
    _screenshot(page, tmp_path, "notion_icon_skin_tones.png")
    menu.locator('[data-skin-tone="3"]').press("Escape")
    expect(menu).to_be_hidden()
    expect(picker).to_be_visible()
    toggle.press("ArrowDown")
    expect(menu).to_be_visible()
    menu.locator('[data-skin-tone="3"]').click()
    expect(menu).to_be_hidden()
    expect(toggle).to_have_attribute("aria-label", "피부색 선택, 중간 피부 선택됨")
    assert page.evaluate("() => State.pageData.page.icon") == original
    variant = picker.locator('section[aria-label="사람"] [data-icon-value="👋🏽"]')
    expect(variant).to_have_count(1)
    variant.click()
    expect(page.locator("#modalBg")).to_be_hidden()
    page.wait_for_function("() => State.pageData.page.icon === '👋🏽' && !State.saving")
    expect(page.locator("#pageIconBtn img.notion-emoji-image")).to_have_attribute("src", "/notion_static/assets/emoji/1f44b-1f3fd.svg")
    page.wait_for_function("() => {const image=document.querySelector('#pageIconBtn img.notion-emoji-image');return image?.complete && image.naturalWidth > 0}")

    page.reload()
    _wait_for_notion_ready(page)
    page.wait_for_function("() => State.pageData?.page?.icon === '👋🏽'")
    page.wait_for_function("() => {const image=document.querySelector('#pageIconBtn img.notion-emoji-image');return image?.complete && image.naturalWidth > 0}")
    page.locator("#pageIconBtn").click()
    picker = page.locator("#pageIconPicker")
    expect(picker.locator('[data-icon-skin-toggle]')).to_have_attribute("aria-label", "피부색 선택, 중간 피부 선택됨")
    expect(picker.locator('section[aria-label="최근 사용한 항목"] [data-icon-value="👋🏽"]')).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 700})
    picker.locator('[data-icon-skin-toggle]').click()
    page.wait_for_function("() => {const r=document.querySelector('.icon-picker-skin-menu').getBoundingClientRect();return r.left>=7 && r.right<=innerWidth-7}")


@pytest.mark.integration
@pytest.mark.regression
def test_personal_page_topbar_matches_notion_at_1280_and_1440(document_page):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({"width": 1280, "height": 720})
    expect(page.locator("#pageEditTime")).to_be_hidden()
    expect(page.locator("#commentDrawerBtn")).to_be_hidden()
    expect(page.locator("#addCommentBtn")).to_be_visible()
    button_ids = ["shareDrawerBtn", "pageCopyLinkBtn", "favoritePageBtn", "pageMoreBtn"]
    page.wait_for_function("""ids => ids.every((id, index) =>
      Math.round(document.getElementById(id).getBoundingClientRect().x)
      === [1120, 1182, 1212, 1242][index])""", arg=button_ids)
    positions = []
    for button_id in button_ids:
        button = page.locator(f"#{button_id}")
        expect(button).to_be_visible()
        positions.append(button.bounding_box()["x"])
    assert positions == sorted(positions)
    assert [round(value) for value in positions] == [1120, 1182, 1212, 1242]

    # Native changes this affordance with the available topbar width, including
    # sidebar collapse; the browser viewport alone does not decide visibility.
    page.locator('#collapseBtn').click()
    expect(page.locator('#pageEditTime')).to_be_visible()
    for width, visible in ((1149, False), (1150, True)):
        page.set_viewport_size({'width': width, 'height': 720})
        page.wait_for_function("""visible => (document.querySelector('#pageEditTime')
          .getBoundingClientRect().width > 0) === visible""", arg=visible)
    page.set_viewport_size({'width': 1280, 'height': 720})
    page.locator('#sidebarRestoreBtn').click()
    expect(page.locator('#pageEditTime')).to_be_hidden()

    page.locator("#pageMoreBtn").click()
    expect(page.locator("#pageMenu .menu-foot")).to_contain_text("최종 편집")
    page.locator('#pageMenu [data-page-action="history"]').click()
    expect(page.locator("#historyPanel")).to_be_visible()
    expect(page.locator("#historyList")).not_to_contain_text("불러오는 중")
    page.locator("#drawerCloseBtn").click()

    page.set_viewport_size({"width": 1440, "height": 900})
    expect(page.locator("#pageEditTime")).to_be_visible()
    handle = page.locator('#sidebarResize')
    for _ in range(3):
        handle.press('ArrowRight')
    expect(page.locator('#pageEditTime')).to_be_hidden()
    handle.press('Home')
    expect(page.locator('#pageEditTime')).to_be_visible()
    page.wait_for_function("""ids => ids.every((id, index) =>
      Math.round(document.getElementById(id).getBoundingClientRect().x)
      === [1280, 1342, 1372, 1402][index])""", arg=button_ids)

    page.locator("#pageEditTime").click()
    expect(page.locator("#pageUpdatesPanel")).to_be_visible()
    expect(page.locator("#drawerCloseBtn")).to_have_attribute("aria-label", "패널 닫기")
    expect(page.locator("#pageEditTime")).to_be_hidden()
    expect(page.locator("#pageCopyLinkBtn")).to_be_hidden()
    expect(page.locator("#pageUpdatesPanel [role=tab]").first).to_have_text("업데이트")
    expect(page.locator("#pageUpdatesPanel [role=tab]").last).to_be_disabled()
    expect(page.locator("#pageUpdatesList")).not_to_contain_text("불러오는 중")
    expect(page.locator("#pageUpdatesList .page-update-page-link").first).to_be_visible()
    expect(page.locator("#pageUpdatesList .page-update-preview").first).to_be_visible()
    page.locator("#pageUpdatesList [data-update-version]").first.click()
    expect(page.locator("#pageVersionOverlay")).to_be_visible()
    expect(page.locator("#pageVersionDocument .page-version-title")).to_be_visible()
    expect(page.locator("#pageVersionDocument .document-preview")).to_be_visible()
    expect(page.locator("#pageVersionList button").first).to_contain_text(
        page.evaluate("State.user.display_name"))
    if os.environ.get("NOTION_VERSION_PREVIEW_SCREENSHOT"):
        page.screenshot(path=os.environ["NOTION_VERSION_PREVIEW_SCREENSHOT"])
    page.locator("#pageVersionCloseBtn").click()
    expect(page.locator("#pageVersionOverlay")).to_be_hidden()
    expect(page.locator("#pageUpdatesPanel")).to_be_visible()
    if os.environ.get("NOTION_PAGE_UPDATES_SCREENSHOT"):
        page.screenshot(path=os.environ["NOTION_PAGE_UPDATES_SCREENSHOT"])
    page.set_viewport_size({'width': 390, 'height': 700})
    page.keyboard.press('Escape')
    expect(page.locator('#pageUpdatesPanel')).to_be_hidden()
    expect(page.locator('#pageMoreBtn')).to_be_focused()
    page.set_viewport_size({'width': 1440, 'height': 900})
    expect(page.locator('#pageEditTime')).to_be_visible()
    page.locator('#pageEditTime').click()
    expect(page.locator('#pageUpdatesPanel')).to_be_visible()
    page.locator("#drawerCloseBtn").click()
    expect(page.locator('#pageEditTime')).to_be_focused()

    for width, visible in ((620, False), (600, False)):
        page.set_viewport_size({"width": width, "height": 700})
        if visible:
            expect(page.locator("#pageEditTime")).to_be_visible()
        else:
            expect(page.locator("#pageEditTime")).to_be_hidden()
        assert page.evaluate("""() => {
          const bar = document.querySelector('.topbar');
          const context = document.querySelector('.topbar-page-context').getBoundingClientRect();
          const actions = document.querySelector('.top-actions').getBoundingClientRect();
          return bar.scrollWidth <= bar.clientWidth + 1 && context.right <= actions.left + 1;
        }"""), width

    page.set_viewport_size({"width": 390, "height": 700})
    expect(page.locator("#pageEditTime")).to_be_hidden()
    assert page.evaluate("""() => {
      const bar = document.querySelector('.topbar');
      return bar.scrollWidth <= bar.clientWidth + 1;
    }""")
    page.locator("#pageMoreBtn").click()
    expect(page.locator('#pageMenu [data-page-action="updates"]')).to_be_visible()
    page.locator('#pageMenu [data-page-action="updates"]').click()
    expect(page.locator("#pageUpdatesPanel")).to_be_visible()
    page.locator("#pageUpdatesList [data-update-version]").first.click()
    expect(page.locator("#pageVersionDocument .page-version-title")).to_be_visible()
    assert page.evaluate("""() => {
      const dialog = document.querySelector('.page-version-dialog');
      const title = document.querySelector('.page-version-title');
      return dialog.getBoundingClientRect().width <= innerWidth
        && title.getBoundingClientRect().right <= innerWidth;
    }""")
    page.locator("#pageVersionCloseBtn").click()


@pytest.mark.integration
@pytest.mark.regression
def test_personal_compact_topbar_retains_document_tools_and_workspace_logout(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    errors, submissions = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('request', lambda request: submissions.append(request.url) if request.method == 'POST'
        and any(request.url.split('?', 1)[0].endswith(endpoint) for endpoint in
            ('/document-ai/ask', '/publish_second_brain', '/attach')) else None)
    page_id = page.evaluate('State.activePageId')
    origin = '/'.join(page.url.split('/', 3)[:3])
    url = f'{origin}/api/notion/pages/{page_id}'
    original = page.request.get(url).json()
    output = Path(os.environ.get('NOTION_PERSONAL_TOPBAR_ARTIFACT_DIR', str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    for theme, width in [('light', 1280), ('dark', 390)]:
        page.evaluate('theme => applyColorTheme(theme, false)', theme)
        page.set_viewport_size({'width': width, 'height': 720})
        expect(page.locator('#pageEditTime')).to_be_hidden()
        page.screenshot(path=str(output / f'personal-topbar-{theme}-{width}.png'))
        for action, panel in [('context', '#contextPanel'), ('document_ai', '#aiPanel'), ('files', '#filesPanel')]:
            page.locator('#pageMoreBtn').click()
            menu = page.locator('#pageMenu')
            expect(menu.locator('[data-page-action="second_brain"]')).to_be_visible()
            expect(menu.locator('[data-page-action="logout"]')).to_have_count(0)
            menu.locator(f'[data-page-action="{action}"]').click()
            expect(page.locator(panel)).to_be_visible()
            expect(page.locator('#inspector')).to_be_focused()
            assert page.evaluate('State.activePageId') == page_id
            page.keyboard.press('Escape')
            expect(page.locator('#inspector')).to_be_hidden()
            expect(page.locator('#pageMoreBtn')).to_be_focused()
        current = page.request.get(url).json()
        assert current['blocks'] == original['blocks']
        assert current['page']['title'] == original['page']['title']

    page.locator('#sidebarRestoreBtn').click()
    page.locator('#workspaceName').click()
    expect(page.locator('#workspacePopover [data-workspace-action="logout"]')).to_be_visible()
    page.keyboard.press('Escape')
    expect(page.locator('#workspacePopover')).to_be_hidden()
    assert not submissions, submissions
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_personal_update_version_preview_is_read_only_until_explicit_restore(document_page):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{"type":"paragraph", "content":"이전 버전 본문", "props":{}}])
    _seed_blocks(page, [{"type":"paragraph", "content":"현재 버전 본문", "props":{}}])
    page_id = page.evaluate("State.activePageId")
    origin = page.evaluate("location.origin")
    versions = page.request.get(f"{origin}/api/notion/pages/{page_id}/versions").json()["items"]
    older_id = next(item["id"] for item in versions
                    if any(block.get("content") == "이전 버전 본문" for block in
                           page.request.get(f"{origin}/api/notion/pages/{page_id}/versions/{item['id']}").json()["blocks"]))
    page.locator("#pageEditTime").click()
    page.locator("#pageUpdatesList [data-update-version]").first.click()
    page.locator(f'[data-page-version-select="{older_id}"]').click()
    expect(page.locator("#pageVersionDocument")).to_contain_text("이전 버전 본문")
    assert page.evaluate("State.pageData.blocks[0].content") == "현재 버전 본문"
    expect(page.locator("#pageVersionRestoreBtn")).to_be_enabled()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#pageVersionRestoreBtn").click()
    expect(page.locator("#pageVersionOverlay")).to_be_hidden()
    expect(page.locator("#blocks")).to_contain_text("이전 버전 본문")


@pytest.mark.integration
@pytest.mark.regression
def test_personal_version_restore_keeps_newer_edit_from_another_tab(document_page):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{"type": "paragraph", "content": "이전 내용", "props": {}}])
    _seed_blocks(page, [{"type": "paragraph", "content": "현재 내용", "props": {}}])
    page_id = page.evaluate("State.activePageId")
    origin = page.evaluate("location.origin")
    base = f"{origin}/api/notion/pages/{page_id}"
    versions = page.request.get(f"{base}/versions").json()["items"]
    older_id = next(
        item["id"] for item in versions
        if any(block.get("content") == "이전 내용" for block in
               page.request.get(f"{base}/versions/{item['id']}").json()["blocks"])
    )

    page.locator("#pageEditTime").click()
    page.locator("#pageUpdatesList [data-update-version]").first.click()
    page.locator(f'[data-page-version-select="{older_id}"]').click()
    expect(page.locator("#pageVersionRestoreBtn")).to_be_enabled()
    # Mutate the server without updating this editor's State, as another tab would.
    newer = page.evaluate("""async url => await api(url, {
      method:"PATCH", body:{title:"다른 탭의 새 제목"},
    })""", base)
    assert newer["page"]["title"] == "다른 탭의 새 제목"

    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#pageVersionRestoreBtn").click()
    expect(page.locator("#pageVersionNotice")).to_contain_text("페이지 내용이 변경됐습니다")
    expect(page.locator("#pageVersionOverlay")).to_be_visible()
    saved = page.request.get(base).json()
    assert saved["page"]["title"] == "다른 탭의 새 제목"
    assert saved["blocks"][0]["content"] == "현재 내용"


@pytest.mark.integration
@pytest.mark.regression
def test_ui_editor_equations_render_source_and_survive_save(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _seed_blocks(page, [{"type": "equation", "content": ""}])
    editor = page.get_by_role("textbox", name="수식(LaTeX)", exact=True)
    preview = page.locator('#blocks [data-type="equation"] .equation-preview')
    expect(editor).to_have_count(0)
    preview.click()
    for source in (r"\alpha", r"$\alpha$", r"$$\alpha$$", r"\frac{a}{b}"):
        editor.fill(source)
        expect(preview.locator(".katex")).to_have_count(1)
        expect(preview.locator(".equation-error")).to_have_count(0)
        expect(editor).to_have_value(source)
    editor.fill(r"\frac{")
    expect(preview.locator(".equation-error")).to_be_visible()
    source = "\\begin{aligned}\na &= \\alpha \\\\\nb &= \\frac{1}{2}\n\\end{aligned}"
    editor.fill(source)
    expect(page.locator("#blocks .block-row")).to_have_count(1)
    expect(preview.locator(".katex")).to_have_count(1)
    _screenshot(page, tmp_path, "equation-popover.png")
    page.get_by_role("button", name="완료", exact=False).click()
    expect(editor).to_have_count(0)
    preview.press("Control+Enter")
    expect(editor).to_be_focused()
    editor.press("Escape")
    expect(editor).to_have_count(0)
    page.evaluate("() => savePage()")
    page.reload()
    _wait_for_notion_ready(page)
    expect(preview.locator(".katex")).to_have_count(1)
    assert page.evaluate("() => State.pageData.blocks[0].content") == source
    assert page.evaluate("() => State.pageData.blocks[0].props.rich_text_html || ''") == ""
    assert page.locator("#blocks .block-row").count() == 1
    page.evaluate("() => openSidePeek(State.activePageId)")
    side_preview = page.locator('#sidePeekBlocks [data-type="equation"] .equation-preview')
    expect(side_preview.locator(".katex")).to_have_count(1)
    side_preview.click()
    editor.fill(r"\beta")
    expect(side_preview.locator("mi")).to_have_text("β")
    editor.press("Enter")
    expect(editor).to_have_count(0)
    page.evaluate("() => saveSidePeek()")
    page.locator("#sidePeekCloseBtn").click()
    page.reload()
    _wait_for_notion_ready(page)
    expect(preview.locator("mi")).to_have_text("β")
    preview.click()
    page.evaluate("() => { State.pageData.page.can_edit = false; renderBlocks(); }")
    expect(editor).to_be_hidden()
    expect(preview).to_be_visible()
    _screenshot(page, tmp_path, "equation-rendered.png")
    assert not errors


@pytest.mark.integration
@pytest.mark.regression
def test_ui_editor_page_outline_tracks_headings_navigation_and_edits(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [
        {"type": "heading1", "content": "첫 번째 절"},
        {"type": "paragraph", "content": "본문\n" * 45},
        {"type": "heading2", "content": "두 번째 절"},
        {"type": "heading3", "content": "들여쓴 제목", "props": {"indent": 1}},
        {"type": "paragraph", "content": "두 번째 본문\n" * 45},
        {"type": "toggle_heading3", "content": "마지막 절"},
    ])
    outline = page.locator("#pageOutline")
    expect(outline).to_be_visible()
    expect(outline.locator("button")).to_have_count(4)
    page.locator("#pageMoreBtn").click()
    page.locator('#pageMenu [data-page-action="customize_page"]').click()
    toggle = page.locator('#pageCustomizePopover [data-customize-setting="show_outline"]')
    expect(toggle).to_have_attribute("aria-checked", "true")
    _screenshot(page, tmp_path, "notion_page_outline_menu_toggle.png")
    toggle.click()
    expect(toggle).to_have_attribute("aria-checked", "false")
    expect(outline).to_be_hidden()
    page.keyboard.press("Escape")
    page.reload()
    _wait_for_notion_ready(page)
    expect(outline).to_be_hidden()
    page.locator("#pageMoreBtn").click()
    page.locator('#pageMenu [data-page-action="customize_page"]').click()
    toggle = page.locator('#pageCustomizePopover [data-customize-setting="show_outline"]')
    toggle.click()
    expect(toggle).to_have_attribute("aria-checked", "true")
    expect(outline).to_be_visible()
    page.keyboard.press("Escape")
    page.locator("#workspaceMain").evaluate("element => element.scrollTo(0, 0)")
    expect(outline.locator("[aria-current]")).to_have_attribute("aria-label", "첫 번째 절")
    outline.hover()
    expect(outline.locator(".page-outline-label").first).to_be_visible()
    outline.get_by_role("button", name="두 번째 절", exact=True).click()
    expect(outline.locator("[aria-current]")).to_have_attribute("aria-label", "두 번째 절")


    page.mouse.move(10, 10)
    expect(outline.locator(".page-outline-label").first).to_be_hidden()
    heading = page.locator('#blocks [data-type="heading2"] .block-editor')
    heading.fill("수정된 두 번째 절")
    expect(outline.get_by_role("button", name="수정된 두 번째 절")).to_have_count(1)
    first = outline.get_by_role("button", name="첫 번째 절", exact=True)
    first.focus()
    first.press("End")
    expect(outline.get_by_role("button", name="마지막 절", exact=True)).to_be_focused()
    page.keyboard.press("Enter")
    expect(outline.locator("[aria-current]")).to_have_attribute("aria-label", "마지막 절")
    _screenshot(page, tmp_path, "outline-expanded.png")
    page.set_viewport_size({"width": 600, "height": 900})
    expect(outline).to_be_hidden()
    page.set_viewport_size({"width": 1440, "height": 900})
    expect(outline).to_be_visible()
    page.evaluate("""() => {
      State.pageData.blocks = [EditorCore.createBlock('heading1', {content: '제목 하나'})];
      renderBlocks();
    }""")
    expect(outline).to_be_visible()
    expect(outline.locator("button")).to_have_count(1)
    expect(outline.locator("[aria-current]")).to_have_attribute("aria-label", "제목 하나")
    outline.hover()
    expect(outline.locator(".page-outline-label")).to_have_text("제목 하나")
    outline.get_by_role("button", name="제목 하나", exact=True).click()
    page.mouse.move(10, 10)
    expect(outline.locator(".page-outline-label")).to_be_hidden()
    page.locator('#blocks [data-type="heading1"] .block-editor').fill("")
    expect(outline).to_be_hidden()
    page.locator('#blocks [data-type="heading1"] .block-editor').fill("다시 입력한 제목")
    expect(outline).to_be_visible()
    expect(outline.get_by_role("button", name="다시 입력한 제목", exact=True)).to_have_count(1)
    page.evaluate("""() => {
      State.pageData.blocks = [EditorCore.createBlock('paragraph', {content: '제목 없는 본문'})];
      renderBlocks();
    }""")
    expect(outline).to_be_hidden()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_page_customize_comments_and_inline_markers_persist(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    block_id = page.evaluate("State.pageData.blocks[0].id")
    page.evaluate("""async blockId => {
      await api(`/api/notion/pages/${State.activePageId}/comments`, {
        method:'POST', body:{content:'블록에 연결된 검증 댓글',block_id:blockId,anchor:{text:'본문'},parent_id:null}
      });
      await api(`/api/notion/pages/${State.activePageId}/comments`, {
        method:'POST', body:{content:'페이지 전체에 남긴 검증 댓글',block_id:'',anchor:{},parent_id:null}
      });
      await loadComments('');
    }""", block_id)
    marker = page.locator('.page-inline-comment-marker')
    expect(marker).to_have_count(1)
    expect(marker).to_contain_text('블록에 연결된 검증 댓글')
    expect(page.locator('#pageCommentAffordance')).to_contain_text('페이지 전체에 남긴 검증 댓글')
    _screenshot(page, tmp_path, 'page-inline-comment-default.png')
    page.locator('#pageMoreBtn').click()
    page.locator('#pageMenu [data-page-action="customize_page"]').click()
    panel = page.locator('#pageCustomizePopover')
    expect(panel).to_be_visible()
    _screenshot(page, tmp_path, 'page-customize-panel.png')
    expect(panel.locator('[data-customize-setting="show_page_comments"]')).to_have_attribute('aria-checked','true')
    panel.locator('[data-customize-setting="show_page_comments"]').click()
    expect(panel.locator('[data-customize-setting="show_page_comments"]')).to_have_attribute('aria-checked','false')
    expect(page.locator('#addCommentBtn')).to_be_hidden()
    expect(page.locator('#pageCommentAffordance')).to_be_hidden()
    panel.locator('#pageInlineCommentStyle').click()
    panel.locator('[data-inline-comment-style="minimized"]').click()
    expect(marker).to_have_class('page-inline-comment-marker minimized')
    assert '블록에 연결된 검증 댓글' not in marker.inner_text()
    page.keyboard.press('Escape')
    page.reload(); _wait_for_notion_ready(page)
    expect(page.locator('#addCommentBtn')).to_be_hidden()
    expect(page.locator('#pageCommentAffordance')).to_be_hidden()
    expect(marker).to_have_class('page-inline-comment-marker minimized')
    marker.click()
    expect(page.locator('#inspector')).to_be_visible()
    expect(page.locator('#commentList')).to_contain_text('블록에 연결된 검증 댓글')
    page.keyboard.press('Escape')
    page.set_viewport_size({'width':390,'height':700})
    page.locator('#pageMoreBtn').click()
    page.locator('#pageMenu [data-page-action="customize_page"]').click()
    panel_box = panel.bounding_box()
    assert panel_box and panel_box['x'] >= 0 and panel_box['x'] + panel_box['width'] <= 390
    assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
    _screenshot(page, tmp_path, 'page-customize-390.png')
    panel.locator('[data-customize-setting="show_page_comments"]').click()
    expect(page.locator('#pageCommentAffordance')).to_contain_text('페이지 전체에 남긴 검증 댓글')
    assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth')


@pytest.mark.integration
@pytest.mark.regression
def test_ui_inline_comments_follow_selected_lines_and_open_the_exact_thread(document_page):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{"type":"paragraph", "content":"첫 줄의 글자\n둘째 줄의 글자\n셋째 줄의 글자"}])
    created = page.evaluate("""async () => {
      const editor = document.querySelector('#blocks .block-editor');
      const blockId = State.pageData.blocks[0].id;
      const create = async (phrase, content) => {
        const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
        let node;
        while (node = walker.nextNode()) {
          const index = node.nodeValue.indexOf(phrase);
          if (index < 0) continue;
          const range = document.createRange();
          range.setStart(node, index); range.setEnd(node, index + phrase.length);
          const selection = window.getSelection();
          selection.removeAllRanges(); selection.addRange(range);
          const anchor = selectionOffsets(editor);
          const lineY = range.getBoundingClientRect().top;
          const saved = await api(`/api/notion/pages/${State.activePageId}/comments`, {
            method:'POST', body:{content,block_id:blockId,anchor,parent_id:null}
          });
          selection.removeAllRanges();
          return {id:saved.comment.id, lineY, anchor};
        }
        throw Error(`missing selected text: ${phrase}`);
      };
      const third = await create('셋째 줄', '셋째 줄 댓글');
      const first = await create('첫 줄', '첫 줄 댓글');
      await loadComments('');
      return {first, third};
    }""")
    assert created['first']['anchor']['end'] > created['first']['anchor']['start']
    assert created['third']['anchor']['start'] > created['first']['anchor']['start']
    page.locator('#pageMoreBtn').click()
    page.locator('#pageMenu [data-page-action="customize_page"]').click()
    page.locator('#pageInlineCommentStyle').click()
    page.locator('[data-inline-comment-style="minimized"]').click()
    expect(page.locator('.page-inline-comment-marker.minimized')).to_have_count(2)
    page.keyboard.press('Escape')
    first = page.locator(f'[data-comment-id="{created["first"]["id"]}"].page-inline-comment-marker')
    third = page.locator(f'[data-comment-id="{created["third"]["id"]}"].page-inline-comment-marker')
    expect(first).to_be_visible()
    expect(third).to_be_visible()
    first_box, third_box = first.bounding_box(), third.bounding_box()
    line_positions = page.evaluate("""() => {
      const editor = document.querySelector('#blocks .block-editor');
      const find = phrase => {
        const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
        let node;
        while (node = walker.nextNode()) {
          const offset = node.nodeValue.indexOf(phrase);
          if (offset < 0) continue;
          const range = document.createRange();
          range.setStart(node, offset); range.setEnd(node, offset + phrase.length);
          return range.getBoundingClientRect().top;
        }
        return null;
      };
      return {first:find('첫 줄'), third:find('셋째 줄')};
    }""")
    assert first_box and third_box and third_box['y'] > first_box['y'] + 25
    assert abs(first_box['y'] - line_positions['first']) <= 12
    assert abs(third_box['y'] - line_positions['third']) <= 12, (third_box, line_positions)
    third.click()
    thread = page.locator(f'[data-comment-thread="{created["third"]["id"]}"]')
    expect(page.locator('#inspector')).to_be_visible()
    expect(thread).to_be_focused()
    expect(thread).to_contain_text('셋째 줄 댓글')
    page.keyboard.press('Escape')
    expect(page.locator('#inspector')).to_be_hidden()
    page.evaluate("""() => {
      const editor = document.querySelector('#blocks .block-editor');
      const firstText = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT).nextNode();
      const range = document.createRange();
      range.setStart(firstText, 0); range.collapse(true);
      editor.focus();
      const selection = window.getSelection();
      selection.removeAllRanges(); selection.addRange(range);
    }""")
    page.keyboard.insert_text('PREFIX PREFIX PREFIX ')
    def third_line_y():
        return page.evaluate("""() => {
          const editor = document.querySelector('#blocks .block-editor');
          const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
          for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            const offset = node.nodeValue.indexOf('셋째 줄');
            if (offset < 0) continue;
            const range = document.createRange();
            range.setStart(node, offset); range.setEnd(node, offset + 3);
            return range.getBoundingClientRect().top;
          }
          return null;
        }""")
    assert abs(third.bounding_box()['y'] - third_line_y()) <= 12
    page.wait_for_function('() => !State.dirty && !State.saving', timeout=30_000)
    page.reload(); _wait_for_notion_ready(page)
    expect(third).to_be_visible()
    assert abs(third.bounding_box()['y'] - third_line_y()) <= 12


@pytest.mark.integration
@pytest.mark.regression
def test_ui_canceling_selected_comment_does_not_anchor_the_next_comment(document_page):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{'type':'paragraph', 'content':'선택 대상 문구와 다른 글'}])
    _select_first_quote(page, '선택 대상')
    page.locator('#inlineToolbar [data-inline-format="comment"]').click()
    expect(page.locator('#inspector')).to_be_visible()
    expect(page.locator('#inspector')).to_have_attribute('data-selection-comment', '')
    expect(page.locator('.page-inline-comment-marker.pending')).to_be_visible()
    assert page.evaluate("CSS.highlights.has('pending-selected-comment')")
    page.keyboard.press('Escape')
    expect(page.locator('#inspector')).to_be_hidden()
    expect(page.locator('.page-inline-comment-marker.pending')).to_have_count(0)
    assert not page.evaluate("CSS.highlights.has('pending-selected-comment')")
    assert page.evaluate("window.getSelection().toString()") == ''
    page.evaluate("""() => {
      State.commentBlockId = State.pageData.blocks[0].id;
      openDrawer('comments');
    }""")
    page.locator('#commentInput').fill('새로 단 범위 없는 댓글')
    page.locator('#commentSubmitBtn').click()
    expect(page.locator('#commentList')).to_contain_text('새로 단 범위 없는 댓글')
    anchor = page.evaluate("""() => State.comments.find(
      item => item.content === '새로 단 범위 없는 댓글'
    )?.anchor""")
    assert not anchor or not anchor.get('text')
    _select_first_quote(page, '다른 글')
    page.locator('#inlineToolbar [data-inline-format="comment"]').click()
    expect(page.locator('.page-inline-comment-marker.pending')).to_be_visible()
    page.locator('.page-inline-comment-marker:not(.pending)').first.click()
    expect(page.locator('.page-inline-comment-marker.pending')).to_have_count(0)
    assert page.evaluate('State.commentSelection') is None
    page.locator('#commentInput').fill('전환 뒤 새 일반 댓글')
    page.locator('#commentSubmitBtn').click()
    expect(page.locator('#commentList')).to_contain_text('전환 뒤 새 일반 댓글')
    switched_anchor = page.evaluate("""() => State.comments.find(
      item => item.content === '전환 뒤 새 일반 댓글'
    )?.anchor""")
    assert not switched_anchor or not switched_anchor.get('text')


@pytest.mark.integration
@pytest.mark.regression
def test_ui_selected_comment_draft_reopens_after_escape(document_page):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{'type':'paragraph', 'content':'앞 문장과 선택 댓글 대상이 있습니다.'}])
    _select_first_quote(page, '선택 댓글 대상')
    page.locator('#inlineToolbar [data-inline-format="comment"]').click()
    page.locator('#commentInput').fill('다시 열어도 남아야 하는 초안')
    page.locator('#commentInput').press('Escape')
    expect(page.locator('#inspector')).to_be_hidden()
    expect(page.locator('.page-inline-comment-marker.pending')).to_be_visible()
    assert page.evaluate("CSS.highlights.has('pending-selected-comment')")
    page.locator('.page-inline-comment-marker.pending').click()
    expect(page.locator('#inspector[data-selection-comment]')).to_be_visible()
    expect(page.locator('#commentInput')).to_have_value('다시 열어도 남아야 하는 초안')
    assert page.evaluate("State.commentSelection?.text") == '선택 댓글 대상'
    page.locator('#commentInput').fill('')
    page.locator('#commentInput').press('Escape')
    expect(page.locator('.page-inline-comment-marker.pending')).to_have_count(0)
    assert not page.evaluate("CSS.highlights.has('pending-selected-comment')")


@pytest.mark.integration
@pytest.mark.regression
def test_ui_selected_comment_draft_prompts_before_leaving_page(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{'type':'paragraph', 'content':'선택한 구절의 댓글 초안입니다.'}])
    _select_first_quote(page, '선택한 구절')
    page.locator('#inlineToolbar [data-inline-format="comment"]').click()
    page.locator('#commentInput').fill('페이지 이동 전에 보존할 초안')
    page.locator('#commentInput').press('Escape')
    page.locator('#workspaceHomeBtn').click()
    expect(page.locator('#modalBg')).to_be_visible()
    expect(page.locator('#modalBody')).to_contain_text('댓글 초안이 삭제됩니다')
    dialog = page.locator('#modalBg.comment-draft-leave-modal .modal')
    assert dialog.bounding_box()['width'] <= 324
    actions = page.locator('#modalActions button').evaluate_all(
        '(buttons) => buttons.map(button => button.getBoundingClientRect().top)')
    assert actions[1] > actions[0]
    _screenshot(page, tmp_path, 'selected-comment-draft-leave-dialog.png')
    page.set_viewport_size({'width':390,'height':700})
    small_dialog = dialog.bounding_box()
    assert small_dialog and small_dialog['x'] >= 8 and small_dialog['x'] + small_dialog['width'] <= 382
    _screenshot(page, tmp_path, 'selected-comment-draft-leave-dialog-mobile.png')
    page.set_viewport_size({'width':1440,'height':900})
    assert page.evaluate('State.activePageId')
    page.locator('#modalActions').get_by_role('button', name='초안으로 돌아가기').click()
    expect(page.locator('#inspector[data-selection-comment]')).to_be_visible()
    expect(page.locator('#commentInput')).to_have_value('페이지 이동 전에 보존할 초안')
    page.locator('#workspaceHomeBtn').click()
    expect(page.locator('#modalBg')).to_be_visible()
    page.locator('#modalActions').get_by_role('button', name='삭제').click()
    expect(page.locator('#homeView')).to_be_visible()
    assert page.evaluate('State.activePageId') == ''
    assert not page.evaluate("CSS.highlights.has('pending-selected-comment')")


@pytest.mark.integration
@pytest.mark.regression
def test_ui_selected_comment_draft_does_not_move_to_another_page(document_page):
    from playwright.sync_api import expect

    page = document_page
    first_page_id = page.evaluate('State.activePageId')
    page.locator('#sidebarNewBtn').click()
    page.locator('#newPageMenu [data-new-page-type="page"]').click()
    page.wait_for_function('(id) => State.activePageId && State.activePageId !== id', arg=first_page_id)
    second_page_id = page.evaluate('State.activePageId')
    assert second_page_id != first_page_id
    page.locator(f'#ownPages .page-row[data-page-id="{first_page_id}"] .page-title-label').click()
    page.wait_for_function('(id) => State.activePageId === id', arg=first_page_id)
    _seed_blocks(page, [{'type':'paragraph', 'content':'첫 페이지의 댓글 대상입니다.'}])
    _select_first_quote(page, '댓글 대상')
    page.locator('#inlineToolbar [data-inline-format="comment"]').click()
    page.locator('#commentInput').fill('다른 페이지에 옮겨가면 안 되는 초안')
    page.locator('#commentInput').press('Escape')
    page.locator(f'#ownPages .page-row[data-page-id="{second_page_id}"] .page-title-label').click()
    expect(page.locator('#modalBg')).to_be_visible()
    assert page.evaluate('State.activePageId') == first_page_id
    page.locator('#modalActions').get_by_role('button', name='삭제').click()
    page.wait_for_function('(id) => State.activePageId === id', arg=second_page_id)
    expect(page.locator('#pageView')).to_be_visible()
    assert not page.evaluate('PendingSelectedCommentRange')
    page.locator('#addCommentBtn').click()
    expect(page.locator('#commentInput')).to_have_value('')


@pytest.mark.integration
@pytest.mark.regression
def test_ui_selected_comment_draft_back_drops_closed_draft_and_forward_does_not_restore(document_page):
    from playwright.sync_api import expect

    page = document_page
    first_page_id = page.evaluate('State.activePageId')
    page.locator('#sidebarNewBtn').click()
    page.locator('#newPageMenu [data-new-page-type="page"]').click()
    page.wait_for_function('(id) => State.activePageId && State.activePageId !== id', arg=first_page_id)
    second_page_id = page.evaluate('State.activePageId')
    page.locator(f'#ownPages .page-row[data-page-id="{first_page_id}"] .page-title-label').click()
    page.wait_for_function('(id) => State.activePageId === id', arg=first_page_id)
    _seed_blocks(page, [{'type':'paragraph', 'content':'뒤로가기 확인 대상 문장입니다.'}])
    _select_first_quote(page, '확인 대상')
    page.locator('#inlineToolbar [data-inline-format="comment"]').click()
    page.locator('#commentInput').fill('뒤로가면 삭제할 초안')
    page.locator('#commentInput').press('Escape')
    page.go_back()
    page.wait_for_function('(id) => State.activePageId === id', arg=second_page_id)
    assert page.url.endswith(f'/page/{second_page_id}')
    expect(page.locator('#modalBg')).to_be_hidden()
    assert not page.evaluate('PendingSelectedCommentRange')
    page.go_forward()
    page.wait_for_function('(id) => State.activePageId === id', arg=first_page_id)
    assert page.url.endswith(f'/page/{first_page_id}')
    assert not page.evaluate('PendingSelectedCommentRange')
    expect(page.locator('.page-inline-comment-marker.pending')).to_have_count(0)
    page.locator('#addCommentBtn').click()
    expect(page.locator('#commentInput')).to_have_value('')


@pytest.mark.integration
@pytest.mark.regression
def test_ui_selected_comment_draft_back_drops_open_draft(document_page):
    from playwright.sync_api import expect

    page = document_page
    first_page_id = page.evaluate('State.activePageId')
    page.locator('#sidebarNewBtn').click()
    page.locator('#newPageMenu [data-new-page-type="page"]').click()
    page.wait_for_function('(id) => State.activePageId && State.activePageId !== id', arg=first_page_id)
    second_page_id = page.evaluate('State.activePageId')
    page.locator(f'#ownPages .page-row[data-page-id="{first_page_id}"] .page-title-label').click()
    page.wait_for_function('(id) => State.activePageId === id', arg=first_page_id)
    _seed_blocks(page, [{'type':'paragraph', 'content':'뒤로가기 삭제 확인 대상입니다.'}])
    _select_first_quote(page, '삭제 확인 대상')
    page.locator('#inlineToolbar [data-inline-format="comment"]').click()
    page.locator('#commentInput').fill('열려 있는 테스트 초안')
    page.go_back()
    page.wait_for_function('(id) => State.activePageId === id', arg=second_page_id)
    assert page.url.endswith(f'/page/{second_page_id}')
    expect(page.locator('#modalBg')).to_be_hidden()
    assert not page.evaluate('PendingSelectedCommentRange')
    page.locator('#addCommentBtn').click()
    expect(page.locator('#commentInput')).to_have_value('')


@pytest.mark.integration
@pytest.mark.regression
def test_ui_selected_comment_draft_is_not_reused_for_page_discussion(document_page):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{'type':'paragraph', 'content':'본문 선택 댓글과 페이지 댓글은 별개입니다.'}])
    _select_first_quote(page, '본문 선택 댓글')
    page.locator('#inlineToolbar [data-inline-format="comment"]').click()
    page.locator('#commentInput').fill('선택한 글에만 달 초안')
    page.locator('#addCommentBtn').click()
    expect(page.locator('#modalBg')).to_be_visible()
    expect(page.locator('#modalBody')).to_contain_text('다른 댓글 화면')
    page.locator('#modalActions').get_by_role('button', name='초안으로 돌아가기').click()
    expect(page.locator('#inspector[data-selection-comment]')).to_be_visible()
    expect(page.locator('#commentInput')).to_have_value('선택한 글에만 달 초안')
    page.locator('#addCommentBtn').click()
    page.locator('#modalActions').get_by_role('button', name='삭제').click()
    expect(page.locator('#pageInlineDiscussion')).to_be_visible()
    expect(page.locator('#commentInput')).to_have_value('')
    assert page.evaluate('State.commentSelection') is None
    assert not page.evaluate('PendingSelectedCommentRange')


@pytest.mark.integration
@pytest.mark.regression
def test_ui_selected_comment_draft_keeps_anchor_when_switching_threads(document_page):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{'type':'paragraph', 'content':'첫 구절과 두 번째 구절이 있습니다.'}])
    _select_first_quote(page, '첫 구절')
    page.locator('#inlineToolbar [data-inline-format="comment"]').click()
    page.locator('#commentInput').fill('먼저 저장한 댓글')
    page.locator('#commentSubmitBtn').click()
    saved_marker = page.locator('.page-inline-comment-marker:not(.pending)')
    expect(saved_marker).to_be_visible()
    _select_first_quote(page, '두 번째 구절')
    page.locator('#inlineToolbar [data-inline-format="comment"]').click()
    page.locator('#commentInput').fill('두 번째 구절에만 연결할 초안')
    saved_marker.click()
    expect(page.locator('#modalBg')).to_be_visible()
    page.locator('#modalActions').get_by_role('button', name='초안으로 돌아가기').click()
    expect(page.locator('#inspector[data-selection-comment]')).to_be_visible()
    expect(page.locator('#commentInput')).to_have_value('두 번째 구절에만 연결할 초안')
    assert page.evaluate('State.commentSelection?.text') == '두 번째 구절'
    saved_marker.click()
    page.locator('#modalActions').get_by_role('button', name='삭제').click()
    expect(page.locator('#commentList')).to_contain_text('먼저 저장한 댓글')
    expect(page.locator('#commentInput')).to_have_value('')
    assert page.evaluate('State.commentSelection') is None


@pytest.mark.integration
@pytest.mark.regression
def test_ui_selected_comment_composer_saves_anchor_and_clears_pending_ui(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{'type':'paragraph', 'content':'앞 문장과 댓글 대상 문구가 있습니다.'}])
    _select_first_quote(page, '댓글 대상 문구')
    page.locator('#inlineToolbar [data-inline-format="comment"]').click()
    popup = page.locator('#inspector[data-selection-comment]')
    pending = page.locator('.page-inline-comment-marker.pending')
    expect(popup).to_be_visible()
    expect(pending).to_be_visible()
    expect(page.locator('#inlineToolbar')).to_be_hidden()
    expect(popup.locator('.drawer-head')).to_be_hidden()
    expect(page.locator('#commentSubmitBtn')).to_be_disabled()
    assert page.locator('.comment-composer-tools button').evaluate_all(
        '(buttons) => buttons.map(button => button.id)') == [
            'commentAttachBtn', 'commentMentionBtn', 'commentSubmitBtn']
    geometry = page.evaluate("""() => {
      const popup = document.querySelector('#inspector').getBoundingClientRect();
      const marker = document.querySelector('.page-inline-comment-marker.pending').getBoundingClientRect();
      const quote = PendingSelectedCommentRange.getBoundingClientRect();
      return {popup:{x:popup.x,y:popup.y,width:popup.width,height:popup.height},
        marker:{x:marker.x,y:marker.y,width:marker.width,height:marker.height},
        quote:{x:quote.x,y:quote.y,width:quote.width,height:quote.height}};
    }""")
    assert abs(geometry['popup']['width'] - 472) <= 2
    assert geometry['popup']['height'] <= 55
    assert geometry['popup']['x'] >= 8 and geometry['popup']['x'] + geometry['popup']['width'] <= 1432
    assert geometry['popup']['y'] >= 8 and geometry['popup']['y'] + geometry['popup']['height'] <= 892
    assert abs(geometry['popup']['x'] + geometry['popup']['width'] / 2 - geometry['quote']['x'] - geometry['quote']['width'] / 2) <= 2, geometry
    assert abs(geometry['popup']['y'] - (geometry['quote']['y'] + geometry['quote']['height'] + 3)) <= 2, geometry
    _screenshot(page, tmp_path, 'selected-comment-before-send.png')
    long_draft = ('Long draft layout check. This is a temporary unsent comment used only to compare '
                  'how the composer wraps as the text becomes longer than one line.')
    page.locator('#commentInput').fill(long_draft)
    expect(page.locator('#commentsPanel.expanded')).to_be_visible()
    expanded = page.evaluate("""() => {
      const popup = document.querySelector('#inspector').getBoundingClientRect();
      const input = document.querySelector('#commentInput');
      const tools = document.querySelector('.inspector .comment-composer-tools').getBoundingClientRect();
      return {height:popup.height, inputBottom:input.getBoundingClientRect().bottom,
        inputHeight:input.clientHeight, inputScrollHeight:input.scrollHeight,
        inlineHeight:input.style.height, inputWidth:input.getBoundingClientRect().width, toolsTop:tools.top};
    }""")
    assert 80 <= expanded['height'] <= 145, expanded
    assert expanded['toolsTop'] >= expanded['inputBottom'] - 2, expanded
    assert expanded['inputScrollHeight'] <= expanded['inputHeight'] + 2, expanded
    _screenshot(page, tmp_path, 'selected-comment-long-draft.png')
    page.locator('#commentInput').fill('선택 구절에 단 댓글')
    expect(page.locator('#commentsPanel.expanded')).to_have_count(0)
    expect(page.locator('#commentSubmitBtn')).to_be_enabled()
    page.locator('#commentSubmitBtn').click()
    expect(popup).to_be_hidden()
    expect(pending).to_have_count(0)
    assert not page.evaluate("CSS.highlights.has('pending-selected-comment')")
    expect(page.locator('.page-inline-comment-marker:not(.pending)')).to_be_visible()
    saved = page.evaluate("""() => State.comments.find(item => item.content === '선택 구절에 단 댓글')""")
    assert saved['anchor']['text'] == '댓글 대상 문구'
    page.set_viewport_size({'width':390,'height':700})
    _select_first_quote(page, '앞 문장')
    page.locator('#inlineToolbar [data-inline-format="comment"]').click()
    expect(popup).to_be_visible()
    mobile = popup.bounding_box()
    assert mobile and mobile['x'] >= 8 and mobile['x'] + mobile['width'] <= 382
    assert mobile['y'] >= 8 and mobile['y'] + mobile['height'] <= 692
    _screenshot(page, tmp_path, 'selected-comment-mobile.png')
    page.locator('#commentInput').fill(long_draft)
    expect(page.locator('#commentsPanel.expanded')).to_be_visible()
    mobile_long = popup.bounding_box()
    assert mobile_long and mobile_long['x'] >= 8 and mobile_long['x'] + mobile_long['width'] <= 382
    assert mobile_long['y'] >= 8 and mobile_long['y'] + mobile_long['height'] <= 692
    _screenshot(page, tmp_path, 'selected-comment-mobile-long-draft.png')
    assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
    page.evaluate("applyColorTheme('dark')")
    page.wait_for_function("""() => getComputedStyle(document.querySelector('#inspector')).backgroundColor === 'rgb(25, 25, 25)'""")
    _screenshot(page, tmp_path, 'selected-comment-mobile-dark.png')
    dark = page.evaluate("""() => ({theme:document.documentElement.dataset.theme,
      rootBg:getComputedStyle(document.documentElement).getPropertyValue('--bg'),
      popupBg:getComputedStyle(document.querySelector('#inspector')).backgroundColor})""")
    assert dark['popupBg'] == 'rgb(25, 25, 25)', dark
    page.locator('#pageTitle').click()
    expect(popup).to_be_hidden()
    expect(page.locator('.page-inline-comment-marker.pending')).to_be_visible()
    page.locator('.page-inline-comment-marker.pending').click()
    expect(page.locator('#commentInput')).to_have_value(long_draft)
    page.locator('#commentInput').fill('')
    page.locator('#commentInput').press('Escape')
    expect(page.locator('.page-inline-comment-marker.pending')).to_have_count(0)


@pytest.mark.integration
@pytest.mark.regression
def test_ui_repeated_comment_quote_keeps_its_original_context_after_prepend(document_page):
    from playwright.sync_api import expect

    page = document_page
    content = '첫 번째 반복 구절이 여기 있습니다.\n두 번째 반복 구절이 여기 있습니다.'
    _seed_blocks(page, [{'type':'paragraph', 'content':content}])
    page.evaluate("""() => {
      const editor = document.querySelector('#blocks .block-editor');
      const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
      let target = null;
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const offset = node.nodeValue.lastIndexOf('반복 구절');
        if (offset >= 0) target = {node, offset};
      }
      if (!target) throw Error('repeated quote missing');
      const range = document.createRange();
      range.setStart(target.node, target.offset);
      range.setEnd(target.node, target.offset + '반복 구절'.length);
      editor.focus();
      const selection = window.getSelection();
      selection.removeAllRanges(); selection.addRange(range);
      document.dispatchEvent(new Event('selectionchange'));
    }""")
    page.locator('#inlineToolbar [data-inline-format="comment"]').click()
    page.locator('#commentInput').fill('두 번째 구절의 댓글')
    page.locator('#commentSubmitBtn').click()
    expect(page.locator('.page-inline-comment-marker:not(.pending)')).to_be_visible()
    saved = page.evaluate("""() => State.comments.find(item => item.content === '두 번째 구절의 댓글')""")
    assert '두 번째' in saved['anchor']['context_before']
    assert saved['anchor']['text'] == '반복 구절'
    marker = page.locator(f'[data-comment-id="{saved["id"]}"].page-inline-comment-marker')
    distance = content.rfind('반복 구절') - content.find('반복 구절')
    page.evaluate("""() => {
      const editor = document.querySelector('#blocks .block-editor');
      const node = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT).nextNode();
      const range = document.createRange();
      range.setStart(node, 0); range.collapse(true);
      editor.focus();
      const selection = window.getSelection();
      selection.removeAllRanges(); selection.addRange(range);
    }""")
    page.keyboard.insert_text('P' * distance)
    def quote_lines():
        return page.evaluate("""() => {
          const editor = document.querySelector('#blocks .block-editor');
          const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
          const lines = [];
          for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            for (let offset = node.nodeValue.indexOf('반복 구절'); offset >= 0;
                 offset = node.nodeValue.indexOf('반복 구절', offset + 1)) {
              const range = document.createRange();
              range.setStart(node, offset); range.setEnd(node, offset + 1);
              lines.push(range.getBoundingClientRect().top);
            }
          }
          return lines;
        }""")
    lines = quote_lines()
    assert len(lines) == 2 and lines[1] > lines[0] + 20
    assert abs(marker.bounding_box()['y'] - lines[1]) <= 12
    page.wait_for_function('() => !State.dirty && !State.saving', timeout=30_000)
    page.reload(); _wait_for_notion_ready(page)
    expect(marker).to_be_visible()
    assert abs(marker.bounding_box()['y'] - quote_lines()[1]) <= 12
