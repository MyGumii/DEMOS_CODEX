import json
import os
from pathlib import Path

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import (
    _seed_blocks,
    document_page,
)


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("theme,width,height", [("light", 1280, 900), ("dark", 390, 430)])
def test_block_action_menu_empty_search_keyboard_and_viewport(document_page, tmp_path, theme, width, height):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": width, "height": height})
    page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
    _seed_blocks(page, [{"type": "paragraph", "content": "선택 메뉴 검증 문장"}])
    page_id = page.evaluate("State.activePageId")
    origin = page.url.split("/", 3)[:3]
    url = "/".join(origin) + f"/api/notion/pages/{page_id}"
    original = page.request.get(url).json()
    output = Path(os.environ.get("NOTION_BLOCK_MENU_ARTIFACT_DIR", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)

    row = page.locator("#blocks .block-row").first
    handle = row.locator(".block-handle")
    row.hover()
    handle.click()
    menu = page.locator('#blockMenu[data-mode="actions"]')
    search = menu.get_by_role("combobox", name="블록 작업 검색")
    expect(menu).to_be_visible()
    expect(search).to_be_focused()
    page.wait_for_function("""() => {
      const menu = document.querySelector('#blockMenu').getBoundingClientRect();
      const anchor = document.querySelector('#blocks .block-handle').getBoundingClientRect();
      const expectedTop = Math.max(8, Math.min((anchor.top + anchor.bottom - menu.height) / 2,
        innerHeight - menu.height - 8));
      return Math.abs(menu.top - expectedTop) < 1;
    }""")
    geometry = menu.evaluate("""element => {
      const rect = element.getBoundingClientRect();
      const anchor = document.querySelector('#blocks .block-handle').getBoundingClientRect();
      return {menu:rect.toJSON(),anchor:anchor.toJSON(),radius:getComputedStyle(element).borderRadius,
        rows:[...element.querySelectorAll('[role="option"]')].filter(item=>item.offsetHeight>0).map(item=>({
          text:item.innerText,rect:item.getBoundingClientRect().toJSON()
        }))};
    }""")
    (output / f"block-menu-{theme}-{width}-geometry.json").write_text(
        json.dumps(geometry, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    page.screenshot(path=str(output / f"block-menu-{theme}-{width}.png"))

    search.fill("zznomatch")
    expect(menu.locator('[data-menu-empty]')).to_be_visible()
    expect(menu.locator('[role="option"]:visible')).to_have_count(0)
    assert search.get_attribute("aria-activedescendant") is None
    search.press("Enter")
    expect(menu).to_be_visible()
    assert page.request.get(url).json() == original
    page.screenshot(path=str(output / f"block-menu-empty-{theme}-{width}.png"))
    search.press("Escape")
    expect(menu).not_to_be_visible()
    expect(handle).to_be_focused()

    handle.press("Enter")
    expect(search).to_be_focused()
    search.press("ArrowDown")
    expect(menu.locator('[data-menu-empty]')).not_to_be_visible()
    search.press("ArrowRight")
    flyout = menu.locator("#blockFlyout")
    expect(flyout).to_be_visible()
    expect(flyout.get_by_role("menuitem").first).to_be_focused()
    flyout.get_by_role("menuitem").first.press("Tab")
    expect(flyout.get_by_role("menuitem").nth(1)).to_be_focused()
    flyout.get_by_role("menuitem").nth(1).press("Escape")
    expect(flyout).not_to_be_visible()
    expect(menu).to_be_visible()
    expect(search).to_be_focused()
    expect(search).to_have_value("")
    search.press("ArrowRight")
    expect(flyout).to_be_visible()
    search.fill("zznomatch")
    expect(flyout).not_to_be_visible()
    expect(menu.locator('[data-menu-empty]')).to_be_visible()
    expect(menu.locator('[data-submenu="color"]')).to_have_attribute("aria-expanded", "false")
    search.press("Escape")
    expect(handle).to_be_focused()
    handle.press("Tab")
    expect(row.locator('.block-editor')).to_be_focused()
    handle.press("Space")
    expect(search).to_be_focused()
    page.set_viewport_size({"width": 320, "height": 360})
    expect(search).to_be_focused()
    page.wait_for_function("""() => {
      const rect = document.querySelector('#blockMenu').getBoundingClientRect();
      return rect.left >= 7 && rect.top >= 7 && rect.right <= innerWidth - 7 && rect.bottom <= innerHeight - 7;
    }""")
    page.screenshot(path=str(output / f"block-menu-resized-{theme}-{width}.png"))
    search.press("Escape")
    assert page.request.get(url).json() == original
    assert not errors

    row.locator('[data-action="insert"]').press("Enter")
    expect(page.locator('#blocks .block-row')).to_have_count(2)
    expect(menu).not_to_be_visible()
    expect(page.locator('#blocks .block-row').nth(1).locator('.block-editor')).to_be_focused()
    page.wait_for_function("() => !State.saving && !State.dirty")
    saved = page.request.get(url).json()
    assert len(saved["blocks"]) == 2
    assert saved["blocks"][0]["content"] == original["blocks"][0]["content"]
    assert saved["blocks"][1]["content"] == ""
    assert not errors

    # These are measurements of the directly observed Notion reference, alongside
    # the interaction checks above; they are not a static CSS assertion.
    assert abs(geometry["menu"]["width"] - 265) < 1
    assert geometry["radius"] == "10px"
    assert all(abs(item["rect"]["height"] - 28) < 1 for item in geometry["rows"])
    if width == 1280:
        assert abs(geometry["menu"]["right"] - (geometry["anchor"]["left"] - 5)) < 1
        assert abs((geometry["menu"]["top"] + geometry["menu"]["bottom"]) / 2
                   - (geometry["anchor"]["top"] + geometry["anchor"]["bottom"]) / 2) < 1
