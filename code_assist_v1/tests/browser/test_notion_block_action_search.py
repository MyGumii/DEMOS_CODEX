"""Block action search executes individual conversions on the captured block."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _seed_blocks
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready


def _body(block):
    return {key: block.get(key) for key in ("id", "type", "content", "props")}


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("theme,width", [("light", 1280), ("dark", 390)])
def test_block_action_search_converts_whole_source_and_preserves_target(document_page, tmp_path, theme, width):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": width, "height": 800})
    page.evaluate("theme => applyColorTheme(theme)", theme)
    _seed_blocks(page, [{"type": "paragraph", "content": "앞 선택 뒤", "props": {
        "rich_text_html": "앞 <b>선택</b> 뒤", "text_color": "red"}},
        {"type": "paragraph", "content": "다음 문장은 그대로"}])
    page_id = page.evaluate("State.activePageId")
    origin = "/".join(page.url.split("/", 3)[:3])
    url = f"{origin}/api/notion/pages/{page_id}"
    original = page.request.get(url).json()["blocks"]
    source_id, sibling_id = [block["id"] for block in original]
    menu = page.locator('#blockMenu[data-mode="actions"]')
    search = menu.get_by_role("combobox", name="블록 작업 검색")
    output = Path(os.environ.get("NOTION_BLOCK_SEARCH_ARTIFACT_DIR", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)

    def open_search(query):
        row = page.locator(f'#blocks .block-row[data-block-id="{source_id}"]')
        row.hover()
        row.locator(".block-handle").click()
        expect(search).to_be_focused()
        search.fill(query)

    open_search("제목")
    expect(menu.locator('[data-convert-type]:visible')).to_have_count(8)
    expect(menu.locator('[data-submenu="convert"]')).to_be_hidden()
    expect(menu.get_by_role("group", name="전환")).to_be_visible()
    page.screenshot(path=str(output / f"block-search-headings-{theme}-{width}.png"))
    search.press("Escape")
    expect(menu).to_be_hidden()
    expect(page.locator(f'#block-{source_id} .block-handle')).to_be_focused()
    assert [_body(b) for b in page.request.get(url).json()["blocks"]] == [_body(b) for b in original]

    open_search("빨간")
    expect(menu.locator('[data-text-color]:visible, [data-bg-color]:visible')).to_have_count(2)
    expect(menu.get_by_role("group", name="텍스트 색상", exact=True)).to_be_visible()
    expect(menu.get_by_role("group", name="배경 색상", exact=True)).to_be_visible()
    # Placement observes the resized result list on the next rendered frame.
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    geometry = menu.evaluate("""e => ({menu:e.getBoundingClientRect().toJSON(),
      rows:[...e.querySelectorAll('[data-text-color], [data-bg-color]')].filter(b=>b.offsetHeight>0).map(b=>({
        label:b.textContent.trim(),rect:b.getBoundingClientRect().toJSON(),
        swatch:b.querySelector('.color-swatch').getBoundingClientRect().toJSON()}))})""")
    (output / f"block-search-color-geometry-{theme}-{width}.json").write_text(
        json.dumps(geometry, ensure_ascii=False, indent=2), encoding="utf-8")
    page.screenshot(path=str(output / f"block-search-colors-{theme}-{width}.png"))
    search.press("ArrowDown")
    expect(menu.locator('[data-bg-color="red"]')).to_have_attribute("aria-selected", "true")
    search.press("Enter")
    page.wait_for_function("() => !State.dirty && !State.saving")
    colored = page.request.get(url).json()["blocks"]
    assert colored[0]["content"] == original[0]["content"]
    assert colored[0]["props"] == {**original[0]["props"], "background_color": "red"}
    assert _body(colored[1]) == _body(original[1])
    open_search("기본 배경")
    expect(menu.locator('[data-text-color]:visible, [data-bg-color]:visible')).to_have_count(1)
    search.press("Enter")
    page.wait_for_function("() => !State.dirty && !State.saving")
    assert [_body(b) for b in page.request.get(url).json()["blocks"]] == [_body(b) for b in original]

    open_search("전환")
    expect(menu.locator('[data-convert-type="paragraph"]')).to_have_attribute("aria-selected", "true")
    search.press("ArrowDown")
    expect(menu.locator('[data-convert-type="heading1"]')).to_have_attribute("aria-selected", "true")
    search.press("Enter")
    expect(menu).to_be_hidden()
    page.wait_for_function("() => !State.dirty && !State.saving")
    rows = page.request.get(url).json()["blocks"]
    assert rows[0]["id"] == source_id
    assert rows[0]["type"] == "heading1"
    assert rows[0]["content"] == original[0]["content"]
    assert rows[0]["props"] == original[0]["props"]
    assert _body(rows[1]) == _body(original[1])

    # A refreshed projection may reorder blocks while the menu owns focus.
    # The command must keep its captured ID instead of using the old index.
    open_search("코드")
    expect(menu.locator('[data-convert-type]:visible')).to_have_count(1)
    page.evaluate("() => State.pageData.blocks.reverse()")
    search.press("Enter")
    page.wait_for_function("() => !State.dirty && !State.saving")
    rows = page.request.get(url).json()["blocks"]
    assert rows[0]["id"] == sibling_id
    assert _body(rows[0]) == _body(original[1])
    assert rows[1]["id"] == source_id
    assert rows[1]["type"] == "code"
    assert rows[1]["content"] == original[0]["content"]
    assert rows[1]["props"]["text_color"] == "red"
    assert "rich_text_html" not in rows[1]["props"]

    open_search("3열")
    expect(menu.locator('[data-convert-type]:visible')).to_have_count(1)
    search.press("Enter")
    page.wait_for_function("() => !State.dirty && !State.saving")
    rows = page.request.get(url).json()["blocks"]
    source = next(b for b in rows if b["id"] == source_id)
    wrapper = next(b for b in rows if b["props"].get("layout_container"))
    lanes = [b for b in rows if b["props"].get("layout_group") == source["props"]["layout_group"]]
    assert sorted(b["props"]["layout_column"] for b in lanes) == [0, 1, 2]
    assert source["content"] == original[0]["content"]
    assert source_id in wrapper["props"]["children"]
    assert sibling_id not in wrapper["props"]["children"]
    assert _body(next(b for b in rows if b["id"] == sibling_id)) == _body(original[1])
    page.screenshot(path=str(output / f"block-search-columns-{theme}-{width}.png"))
    page.reload()
    _wait_for_notion_ready(page)
    assert [_body(b) for b in page.request.get(url).json()["blocks"]] == [_body(b) for b in rows]

    # A stale source or revoked edit capability must never execute a conversion.
    for stale in ("changed", "removed", "readonly", "workspace"):
        open_search("제목1")
        before = page.request.get(url).json()["blocks"]
        page.evaluate("""({kind,id}) => {
          if(kind==='changed') State.pageData.blocks.find(b=>b.id===id).content='새로 받은 내용';
          else if(kind==='removed') State.pageData.blocks=State.pageData.blocks.filter(b=>b.id!==id);
          else if(kind==='readonly') State.pageData.page.can_edit=false;
          else State.navigation.workspaceId='다른 워크스페이스';
        }""", {"kind": stale, "id": source_id})
        search.press("Enter")
        expect(menu).to_be_hidden()
        assert not page.evaluate("State.dirty || State.saving")
        assert [_body(b) for b in page.request.get(url).json()["blocks"]] == [_body(b) for b in before]
        page.reload()
        _wait_for_notion_ready(page)

    (output / f"block-search-result-{theme}-{width}.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_block_action_search_page_conversion_keeps_full_title_and_single_link(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    source = "검색으로 전환해도 남아야 하는 전체 문장"
    _seed_blocks(page, [{"type": "paragraph", "content": source},
        {"type": "paragraph", "content": "뒤 문장은 그대로"}])
    parent_id = page.evaluate("State.activePageId")
    origin = "/".join(page.url.split("/", 3)[:3])
    url = f"{origin}/api/notion/pages/{parent_id}"
    original = page.request.get(url).json()["blocks"]
    row = page.locator("#blocks .block-row").first
    row.hover()
    row.locator(".block-handle").click()
    menu = page.locator('#blockMenu[data-mode="actions"]')
    search = menu.get_by_role("combobox", name="블록 작업 검색")
    search.fill("페이지")
    expect(menu.locator('[data-convert-type]:visible')).to_have_count(1)
    search.press("Enter")
    page.wait_for_function("""() => State.pageData.blocks[0].type==='child_page'
      && State.pageData.blocks[0].props.page_id && !State.dirty && !State.saving""", timeout=15000)
    parent = page.request.get(url).json()
    links = [b for b in parent["blocks"] if b["type"] == "child_page"]
    assert len(links) == 1
    child_id = links[0]["props"]["page_id"]
    child = page.request.get(f"{origin}/api/notion/pages/{child_id}").json()
    assert child["page"]["title"] == source
    assert child["page"]["parent_id"] == parent_id
    assert links[0]["content"] == source
    assert _body(parent["blocks"][1]) == _body(original[1])
    assert not any(b.get("content", "").strip() for b in child["blocks"])
    assert page.evaluate("State.activePageId") == parent_id
    output = Path(os.environ.get("NOTION_BLOCK_SEARCH_ARTIFACT_DIR", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / "block-search-page-parent.png"))
    (output / "block-search-page-result.json").write_text(
        json.dumps({"parent": parent, "child": child}, ensure_ascii=False, indent=2), encoding="utf-8")
    page.reload()
    expect(page.locator('#blocks [data-child-page]')).to_contain_text(source)
    assert page.request.get(url).json()["blocks"] == parent["blocks"]
