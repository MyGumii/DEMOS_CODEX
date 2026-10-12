"""Many database views remain reachable beside the toolbar on a narrow screen."""

import os
from pathlib import Path

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _seed_blocks
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready


@pytest.mark.integration
@pytest.mark.regression
def test_full_page_many_views_remain_reachable_on_mobile(document_page):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _seed_blocks(page, [{"type": "table", "content": "여러 보기", "props": {
        "view": "table",
        "properties": [{"id": "name", "name": "이름", "type": "title"},
                       {"id": "state", "name": "상태", "type": "status"},
                       {"id": "due", "name": "기한", "type": "date"}],
        "rows": [{"name": "첫째", "state": "진행"}],
    }}])
    for index, view_type in enumerate(("board", "list", "gallery", "calendar", "timeline"), start=1):
        page.locator("[data-db-add-view]").click()
        page.locator("#dbViewName").fill(f"길이가 긴 확인용 보기 {index}")
        page.locator("#dbViewType").select_option(view_type)
        page.get_by_role("button", name="저장", exact=True).click()
        expect(page.locator(".database-view-tabs [data-db-view-id]")).to_have_count(index + 1)
    page.locator("[data-db-open-full-page]").click()
    page.set_viewport_size({"width": 390, "height": 800})
    toolbar = page.locator(".page-view.database-full-page .database-toolbar")
    expect(toolbar).to_be_visible()
    expect(toolbar.locator("[data-db-view-id]")).to_have_count(6)
    geometry = toolbar.evaluate("""element => {
      const tabs=element.querySelector('.database-view-tabs');
      const bounds=selector=>{const rect=element.querySelector(selector).getBoundingClientRect();
        return {left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom};};
      return {width:innerWidth,documentWidth:document.documentElement.scrollWidth,
        tabs:{clientWidth:tabs.clientWidth,scrollWidth:tabs.scrollWidth,...bounds('.database-view-tabs')},
        filter:bounds('[data-db-filter]'),sort:bounds('[data-db-sort]'),
        search:bounds('[data-db-search]'),settings:bounds('[data-db-view-menu]'),
        create:bounds('.database-create-cluster')};
    }""")
    assert geometry["documentWidth"] <= geometry["width"], geometry
    assert geometry["tabs"]["scrollWidth"] > geometry["tabs"]["clientWidth"], geometry
    for name in ("filter", "sort", "search", "settings", "create"):
        assert 0 <= geometry[name]["left"] < geometry[name]["right"] <= 390, (name, geometry)
    last = toolbar.locator("[data-db-view-id]").last
    last.click()
    expect(last).to_have_attribute("aria-selected", "true")
    assert last.evaluate("""element => {
      const tab=element.getBoundingClientRect(),parent=element.parentElement.getBoundingClientRect();
      return tab.left>=parent.left && tab.right<=parent.right;
    }""")
    artifact_dir = os.environ.get("NOTION_MANY_VIEWS_ARTIFACT_DIR")
    if artifact_dir:
        output = Path(artifact_dir)
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / "database-many-views-390.png"))
    toolbar.locator("[data-db-view-menu]").click()
    expect(page.locator("[data-db-view-popover]")).to_be_visible()
    page.keyboard.press("Escape")
    page.wait_for_function("() => !State.dirty && !State.saving", timeout=30_000)
    page.reload()
    _wait_for_notion_ready(page)
    if page.locator("[data-db-open-full-page]").is_visible():
        page.locator("[data-db-open-full-page]").click()
    toolbar = page.locator(".page-view.database-full-page .database-toolbar")
    expect(toolbar).to_be_visible()
    restored = toolbar.locator("[data-db-view-id]").last
    expect(restored).to_have_attribute("aria-selected", "true")
    assert restored.evaluate("""element => {
      const tab=element.getBoundingClientRect(),parent=element.parentElement.getBoundingClientRect();
      return tab.left>=parent.left && tab.right<=parent.right;
    }""")
    first = toolbar.locator("[data-db-view-id]").first
    first.focus()
    first.press("Enter")
    expect(first).to_have_attribute("aria-selected", "true")
    assert first.evaluate("""element => {
      const tab=element.getBoundingClientRect(),parent=element.parentElement.getBoundingClientRect();
      return tab.left>=parent.left && tab.right<=parent.right
        && element===document.activeElement;
    }""")
    assert not errors, errors
