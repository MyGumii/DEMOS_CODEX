import os
from pathlib import Path

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _seed_blocks, _select_first_quote


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("theme,width,height", [("light", 1280, 900), ("dark", 390, 430)])
def test_inline_format_type_color_selection_and_reset(document_page, tmp_path, theme, width, height):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": width, "height": height})
    page.evaluate("theme => applyColorTheme(theme)", theme)
    _seed_blocks(page, [{"type":"paragraph", "content":"앞 선택 뒤", "props": {
        "rich_text_html":'앞 <b><a href="https://example.com">선택</a></b> 뒤'}}])
    page_id = page.evaluate("State.activePageId")
    origin = "/".join(page.url.split("/", 3)[:3])
    url = f"{origin}/api/notion/pages/{page_id}"
    original = page.request.get(url).json()
    editor = page.locator("#blocks .block-editor").first
    toolbar = page.locator("#inlineToolbar")
    pop = page.locator("#inlineFormatPopover")
    color = toolbar.get_by_role("button",name="텍스트 색상",exact=True)
    _select_first_quote(page,"선택")
    expect(color).to_be_visible(timeout=1500)
    color.click()
    expect(pop).to_be_visible()
    output = Path(os.environ.get("NOTION_INLINE_FORMAT_ARTIFACT_DIR", str(tmp_path)))
    output.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(output / f"inline-color-{theme}-{width}.png"))
    geometry = pop.evaluate("""el=>({width:el.getBoundingClientRect().width,radius:getComputedStyle(el).borderRadius,
        swatches:[...el.querySelectorAll('[data-inline-color]')].map(button=>button.getBoundingClientRect().width)})""")
    assert abs(geometry["width"]-190)<1
    assert geometry["radius"]=="10px"
    assert all(abs(width-30)<1 for width in geometry["swatches"])
    pop.get_by_role("menuitem",name="빨간색 텍스트",exact=True).press("Escape")
    expect(pop).not_to_be_visible()
    expect(color).to_be_focused()
    assert page.request.get(url).json()==original
    color.press("Enter")
    pop.get_by_role("menuitem",name="빨간색 텍스트",exact=True).click()
    page.wait_for_function("() => !State.saving && !State.dirty")
    expect(editor.locator('[data-notion-inline-color="red"]')).to_have_text("선택")
    expect(editor.locator("b a")).to_have_text("선택")
    assert page.request.get(url).json()["blocks"][0]["content"]=="앞 선택 뒤"

    page.evaluate("""() => {
      const el=document.querySelector('#blocks .block-editor'),r=document.createRange();r.selectNodeContents(el);
      el.focus();getSelection().removeAllRanges();getSelection().addRange(r);document.dispatchEvent(new Event('selectionchange'));
    }""")
    color.click()
    pop.get_by_role("menuitem",name="노란색 배경",exact=True).click()
    _select_first_quote(page,"선택")
    color.click()
    pop.get_by_role("menuitem",name="기본 배경",exact=True).click()
    page.wait_for_function("() => !State.saving && !State.dirty")
    assert editor.evaluate("""el => {
      const node=[...el.querySelectorAll('[data-notion-inline-background="default"]')].find(node=>node.textContent==='선택');
      return Boolean(node && !node.parentElement.closest('[data-notion-inline-background="yellow"]'));
    }""")
    assert editor.locator('[data-notion-inline-background="yellow"]').all_text_contents()==["앞 "," 뒤"]
    assert editor.locator('[data-notion-inline-background="yellow"]').evaluate_all(
        "els => els.every(el=>getComputedStyle(el).backgroundColor!=='rgba(0, 0, 0, 0)')")
    expect(editor.locator("b a")).to_have_text("선택")
    page.reload()
    expect(editor).to_have_text("앞 선택 뒤")
    assert page.evaluate("document.documentElement.dataset.theme") == theme
    expect(editor.locator('[data-notion-inline-color="red"]')).to_have_text("선택")
    assert editor.locator('[data-notion-inline-background="yellow"]').all_text_contents()==["앞 "," 뒤"]
    _select_first_quote(page,"선택")
    type_button = toolbar.get_by_role("button",name="블록 유형 전환",exact=True)
    type_button.click()
    expect(pop).to_be_visible()
    assert abs(pop.evaluate("el=>el.getBoundingClientRect().width")-220)<1
    assert pop.get_by_role("menuitem").evaluate_all("els=>els.every(el=>Math.abs(el.getBoundingClientRect().height-28)<1)")
    page.screenshot(path=str(output / f"inline-type-{theme}-{width}.png"))
    pop.get_by_role("menuitem",name="제목4",exact=True).click()
    page.wait_for_function("() => !State.saving && !State.dirty")
    saved=page.request.get(url).json()["blocks"][0]
    assert saved["type"]=="heading4" and saved["id"]==original["blocks"][0]["id"]
    assert saved["content"]=="앞 선택 뒤"
    expect(editor.locator("b a")).to_have_text("선택")
    _select_first_quote(page,"선택")
    toolbar.locator('[data-inline-format="removeFormat"]').click()
    page.wait_for_function("() => !State.saving && !State.dirty")
    expect(editor.locator('[data-notion-inline-color="red"]')).to_have_count(0)
    expect(editor.locator("b")).to_have_count(0)
    expect(editor.locator("a")).to_have_text("선택")
    assert editor.locator('[data-notion-inline-background="yellow"]').all_text_contents()==["앞 "," 뒤"]
    saved=page.request.get(url).json()["blocks"][0]
    _select_first_quote(page,"선택")
    color.click()
    page.set_viewport_size({"width":320,"height":360})
    page.wait_for_function("""() => {const r=document.querySelector('#inlineFormatPopover').getBoundingClientRect();
      return r.left>=7 && r.top>=7 && r.right<=innerWidth-7 && r.bottom<=innerHeight-7;}""")
    page.screenshot(path=str(output / f"inline-color-resized-{theme}-{width}.png"))
    pop.get_by_role("menuitem",name="파란색 텍스트",exact=True).focus()
    page.evaluate("State.pageData.page.can_edit=false")
    pop.get_by_role("menuitem",name="파란색 텍스트",exact=True).press("Enter")
    expect(pop).not_to_be_visible()
    assert page.request.get(url).json()["blocks"][0]==saved
    assert not errors
