import json
import os
from pathlib import Path

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import (
    _seed_blocks,
    _select_first_quote,
    document_page,
)


def _capture(page, tmp_path, name):
    output = Path(os.environ.get("NOTION_INLINE_LINK_ARTIFACT_DIR", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / name))


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("theme,width,height", [("light", 1280, 900), ("dark", 390, 430)])
def test_inline_link_cancel_search_url_and_page_save(document_page, tmp_path, theme, width, height):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("dialog", lambda dialog: dialog.dismiss())
    page.set_viewport_size({"width": width, "height": height})
    page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
    target = page.evaluate("""async () => (await api('/api/notion/pages', {
      method:'POST',body:{title:'연결 대상 페이지',icon:'🏠'}
    })).page""")
    _seed_blocks(page, [{"type": "paragraph", "content": "링크 선택 문구 유지"}])
    page_id = page.evaluate("State.activePageId")
    origin = "/".join(page.url.split("/", 3)[:3])
    url = f"{origin}/api/notion/pages/{page_id}"
    original = page.request.get(url).json()
    editor = page.locator("#blocks .block-editor").first
    toolbar = page.locator("#inlineToolbar")
    link = toolbar.locator('[data-inline-format="link"]')
    popover = page.locator("#inlineLinkPopover")
    search = popover.get_by_role("combobox")

    _select_first_quote(page, "선택")
    link.click()
    _capture(page, tmp_path, f"inline-link-open-{theme}-{width}.png")
    expect(popover).to_be_visible(timeout=1500)
    expect(search).to_be_focused()
    expect(popover.get_by_role("option", name="제목 없음", exact=True)).to_be_visible()
    geometry = popover.evaluate("""el => ({rect:el.getBoundingClientRect().toJSON(),
      radius:getComputedStyle(el).borderRadius,input:el.querySelector('input').getBoundingClientRect().toJSON()})""")
    output = Path(os.environ.get("NOTION_INLINE_LINK_ARTIFACT_DIR", str(tmp_path)))
    (output / f"inline-link-{theme}-{width}-geometry.json").write_text(
        json.dumps(geometry, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert abs(geometry["rect"]["width"] - 330) < 1
    assert geometry["radius"] == "10px"
    assert abs(geometry["input"]["height"] - 30) < 1
    _capture(page, tmp_path, f"inline-link-recent-{theme}-{width}.png")
    search.fill("없는검색결과zznomatch")
    expect(popover.get_by_role("status")).to_have_text("검색 결과가 없습니다.")
    search.press("Enter")
    expect(popover).to_be_visible()
    assert page.request.get(url).json() == original
    search.press("Escape")
    expect(popover).not_to_be_visible()
    expect(link).to_be_focused()
    expect(toolbar).to_be_visible()
    link.press("Escape")
    expect(toolbar).not_to_be_visible()
    expect(editor).to_be_focused()
    assert page.evaluate("window.getSelection().isCollapsed")
    assert page.request.get(url).json() == original

    _select_first_quote(page, "선택")
    editor.press("Control+k")
    expect(search).to_be_focused()
    search.fill("javascript:alert(1)")
    expect(popover.get_by_role("status")).to_have_text("검색 결과가 없습니다.")
    search.press("Enter")
    assert page.request.get(url).json() == original
    search.fill("https://example.com/선택?q=1")
    expect(popover.locator('[data-inline-link-url]')).to_be_visible()
    search.press("Enter")
    expect(popover).not_to_be_visible()
    expect(toolbar).not_to_be_visible()
    expect(editor).to_be_focused()
    page.wait_for_function("() => !State.saving && !State.dirty")
    saved = page.request.get(url).json()
    assert saved["blocks"][0]["content"] == original["blocks"][0]["content"]
    assert "https://example.com/" in saved["blocks"][0]["props"]["rich_text_html"]
    expect(editor.locator("a")).to_have_text("선택")

    _select_first_quote(page, "선택")
    link.press("Control+k")
    expect(search).to_be_focused()
    assert search.input_value().startswith("https://example.com/")
    search.fill("https://example.org/revised")
    search.press("Enter")
    page.wait_for_function("() => !State.saving && !State.dirty")
    expect(editor.locator('a[href="https://example.org/revised"]')).to_have_text("선택")
    _select_first_quote(page, "선택")
    link.click()
    popover.get_by_role("option", name="링크 제거", exact=True).click()
    page.wait_for_function("() => !State.saving && !State.dirty")
    expect(editor.locator("a")).to_have_count(0)
    assert page.request.get(url).json()["blocks"][0]["content"] == original["blocks"][0]["content"]

    _select_first_quote(page, "문구")
    link.click()
    search.fill("연결 대상")
    option = popover.get_by_role("option", name="연결 대상 페이지", exact=True)
    expect(option).to_be_visible()
    _capture(page, tmp_path, f"inline-link-page-search-{theme}-{width}.png")
    search.press("Tab")
    expect(option).to_be_focused()
    option.press("Home")
    expect(option).to_be_focused()
    option.press("Shift+Tab")
    expect(search).to_be_focused()
    search.press("ArrowDown")
    search.press("Enter")
    expect(popover).not_to_be_visible()
    page.wait_for_function("() => !State.saving && !State.dirty")
    saved = page.request.get(url).json()
    assert saved["blocks"][0]["content"] == original["blocks"][0]["content"]
    expect(editor.locator(f'a[href$="/page/{target["id"]}"]')).to_have_text("문구")
    page.reload()
    expect(editor).to_have_text("링크 선택 문구 유지")
    expect(editor.locator(f'a[href$="/page/{target["id"]}"]')).to_have_text("문구")
    _select_first_quote(page, "유지")
    link.click()
    expect(search).to_be_focused()
    page.set_viewport_size({"width": 320, "height": 360})
    page.wait_for_function("""() => {
      const r=document.querySelector('#inlineLinkPopover').getBoundingClientRect();
      return r.left>=7 && r.top>=7 && r.right<=innerWidth-7 && r.bottom<=innerHeight-7;
    }""")
    expect(search).to_be_focused()
    _capture(page, tmp_path, f"inline-link-resized-{theme}-{width}.png")
    page.locator("#pageMoreBtn").click()
    expect(popover).not_to_be_visible()
    expect(page.locator("#pageMenu")).to_be_visible()
    assert not errors


@pytest.mark.integration
@pytest.mark.regression
def test_inline_link_search_failure_late_results_and_permission_change(document_page):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{"type": "paragraph", "content": "원래 선택 문장"}])
    original = page.evaluate("JSON.stringify(State.pageData.blocks)")
    _select_first_quote(page, "선택")
    page.locator('#inlineToolbar [data-inline-format="link"]').click()
    popover = page.locator("#inlineLinkPopover")
    search = popover.get_by_role("combobox")
    expect(popover).to_be_visible()
    page.evaluate("""() => {
      window.inlineLinkOriginalApi=api;
      api=(path,options)=>path.startsWith('/api/notion/pages?q=')
        ? new Promise(resolve=>window.inlineLinkDeferred={path,resolve})
        : window.inlineLinkOriginalApi(path,options);
    }""")
    search.fill("지연 검색")
    page.wait_for_function("window.inlineLinkDeferred")
    search.press("Escape")
    page.evaluate("""() => {
      window.inlineLinkDeferred.resolve({items:[{id:'stale-page',title:'늦은 검색 결과'}]});
      api=window.inlineLinkOriginalApi;
    }""")
    expect(popover).not_to_be_visible()
    expect(page.get_by_text("늦은 검색 결과", exact=True)).to_have_count(0)
    assert page.evaluate("JSON.stringify(State.pageData.blocks)") == original

    page.evaluate("""() => {
      api=(path,options)=>path.startsWith('/api/notion/pages?q=')
        ? Promise.reject(new Error('isolated search failure')) : window.inlineLinkOriginalApi(path,options);
    }""")
    page.locator('#inlineToolbar [data-inline-format="link"]').click()
    expect(popover.get_by_role("status")).to_have_text("페이지를 불러오지 못했습니다. 다시 검색해 주세요.")
    page.evaluate("() => {api=window.inlineLinkOriginalApi;}")
    popover.get_by_role("button", name="다시 검색", exact=True).click()
    expect(search).to_be_focused()
    expect(popover.get_by_role("option", name="제목 없음", exact=True)).to_be_visible()
    search.press("Escape")
    assert page.evaluate("JSON.stringify(State.pageData.blocks)") == original

    page.locator('#inlineToolbar [data-inline-format="link"]').click()
    search.fill("https://example.com")
    expect(popover.locator('[data-inline-link-url]')).to_be_visible()
    page.evaluate("State.pageData.page.can_edit=false")
    search.press("Enter")
    expect(popover).not_to_be_visible()
    expect(page.locator("#inlineToolbar")).not_to_be_visible()
    assert page.evaluate("JSON.stringify(State.pageData.blocks)") == original
    assert page.evaluate("State.dirty") is False

    page.evaluate("State.pageData.page.can_edit=true")
    for change in ("page", "workspace"):
        _select_first_quote(page, "선택")
        page.locator('#inlineToolbar [data-inline-format="link"]').click()
        expect(popover).to_be_visible()
        page.evaluate("""change => {
          const pageId=State.activePageId, sequence=State.navigation.sequence;
          if (change==='page') State.activePageId='changed-page';
          else State.navigation.sequence+=1;
          applyInlineLink({url:'https://example.com'});
          State.activePageId=pageId;State.navigation.sequence=sequence;
        }""", change)
        expect(popover).not_to_be_visible()
        assert page.evaluate("JSON.stringify(State.pageData.blocks)") == original
        assert page.evaluate("State.dirty") is False


@pytest.mark.integration
@pytest.mark.regression
def test_inline_page_text_links_preserve_labels_and_legacy_mentions(document_page):
    from playwright.sync_api import expect

    page = document_page
    target = page.evaluate("""async () => {
      const data=await api('/api/notion/pages',{method:'POST',body:{title:'현재 페이지 제목'}});
      await loadPages();return data.page;
    }""")
    href = f'/page/{target["id"]}'
    _seed_blocks(page, [
        {"type": "paragraph", "content": "옛 페이지 제목", "props": {
            "rich_text_html": f'<a href="{href}">옛 페이지 제목</a>'}},
        {"type": "paragraph", "content": "사용자 문구", "props": {
            "rich_text_html": f'<a href="{href}" data-notion-text-link="true">사용자 문구</a>'}},
    ])
    editors = page.locator("#blocks .block-editor")
    expect(editors.nth(0).locator("a")).to_have_text("현재 페이지 제목")
    expect(editors.nth(1).locator("a")).to_have_text("사용자 문구")
    previews = page.evaluate("""target => {
      const data={contract:'code_assist.notion.search_preview.v1', references:{[target.id]:target}};
      return State.pageData.blocks.map(block => searchPreviewRichHtml(block,data));
    }""", target)
    assert "현재 페이지 제목" in previews[0]
    assert "사용자 문구" in previews[1]
    assert "현재 페이지 제목" not in previews[1]
    assert all('href=' not in preview for preview in previews)
    page.reload()
    expect(editors.nth(0).locator("a")).to_have_text("현재 페이지 제목")
    expect(editors.nth(1).locator("a")).to_have_text("사용자 문구")
