import json
import os
from pathlib import Path

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import (
    _seed_blocks,
    _select_first_quote,
    document_page,
)


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("theme,width,height", [("light", 1920, 900), ("light", 1280, 900), ("dark", 390, 430)])
def test_inline_toolbar_selection_keyboard_format_and_viewport(document_page, tmp_path, theme, width, height):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": width, "height": height})
    page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
    _seed_blocks(page, [{"type": "paragraph", "content": "서식 검증 문장"}])
    page_id = page.evaluate("State.activePageId")
    url = "/".join(page.url.split("/", 3)[:3]) + f"/api/notion/pages/{page_id}"
    original = page.request.get(url).json()
    output = Path(os.environ.get("NOTION_INLINE_TOOLBAR_ARTIFACT_DIR", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    toolbar = page.locator("#inlineToolbar")
    bold = toolbar.locator('[data-inline-format="bold"]')
    editor = page.locator("#blocks .block-editor").first

    _select_first_quote(page, "검증")
    expect(toolbar).to_be_visible()
    page.screenshot(path=str(output / f"inline-toolbar-{theme}-{width}.png"))
    geometry = toolbar.evaluate("""element => ({rect:element.getBoundingClientRect().toJSON(),
      radius:getComputedStyle(element).borderRadius,background:getComputedStyle(element).backgroundColor,
      buttons:[...element.querySelectorAll('[data-inline-format]')].map(button=>({
        action:button.dataset.inlineFormat,label:button.getAttribute('aria-label'),rect:button.getBoundingClientRect().toJSON()
      }))})""")
    (output / f"inline-toolbar-{theme}-{width}-geometry.json").write_text(
        json.dumps(geometry, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    bold.press("Escape")
    expect(toolbar).not_to_be_visible()
    assert page.evaluate("() => window.getSelection().isCollapsed")
    assert page.evaluate("State.inlineSelection === null")
    expect(editor).to_be_focused()
    assert page.request.get(url).json() == original

    _select_first_quote(page, "검증")
    expect(toolbar).to_be_visible()
    bold.focus()
    bold.press("ArrowRight")
    expect(toolbar.locator('[data-inline-format="italic"]')).to_be_focused()
    toolbar.locator('[data-inline-format="italic"]').press("Home")
    expect(toolbar.locator('[data-inline-format="type"]')).to_be_focused()
    bold.press("End")
    expect(toolbar.locator('[data-inline-format="ai"]')).to_be_focused()
    toolbar.locator('[data-inline-format="ai"]').press("ArrowLeft")
    expect(toolbar.locator('[data-inline-format="comment"]')).to_be_focused()
    assert page.request.get(url).json() == original
    toolbar.locator('[data-inline-format="comment"]').press("Escape")
    expect(editor).to_be_focused()

    _select_first_quote(page, "검증")
    bold.click()
    expect(bold).to_have_attribute("aria-pressed", "true")
    page.wait_for_function("() => !State.saving && !State.dirty")
    formatted = page.request.get(url).json()
    assert formatted["blocks"][0]["content"] == original["blocks"][0]["content"]
    assert "검증" in formatted["blocks"][0]["props"]["rich_text_html"]
    toolbar.locator('[data-inline-format="removeFormat"]').click()
    page.wait_for_function("() => !State.saving && !State.dirty")
    cleared = page.request.get(url).json()
    assert cleared["blocks"][0]["content"] == original["blocks"][0]["content"]
    assert "rich_text_html" not in cleared["blocks"][0]["props"]

    bold.focus()
    page.set_viewport_size({"width": 320, "height": 360})
    page.wait_for_function("""() => {
      const rect = document.querySelector('#inlineToolbar').getBoundingClientRect();
      return rect.left >= 7 && rect.top >= 7 && rect.right <= innerWidth - 7 && rect.bottom <= innerHeight - 7;
    }""")
    expect(bold).to_be_focused()
    page.screenshot(path=str(output / f"inline-toolbar-resized-{theme}-{width}.png"))
    bold.press("Escape")
    expect(toolbar).not_to_be_visible()
    expect(editor).to_be_focused()
    assert not errors
    assert abs(geometry["rect"]["width"] - 192) < 1
    assert geometry["radius"] == "14px"
    assert all(button["label"] for button in geometry["buttons"])
    assert all(abs(button["rect"]["height"] - 28) < 1 for button in geometry["buttons"])


@pytest.mark.integration
@pytest.mark.regression
def test_inline_toolbar_rejects_stale_page_block_and_permission(document_page):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{"type": "paragraph", "content": "원래 선택 문장"}])
    toolbar = page.locator("#inlineToolbar")
    original = page.evaluate("JSON.stringify(State.pageData.blocks)")
    for change in ("page", "block", "permission", "text"):
        _select_first_quote(page, "선택")
        expect(toolbar).to_be_visible()
        page.evaluate("""change => {
          const pageId=State.activePageId,blockId=State.pageData.blocks[0].id,canEdit=State.pageData.page.can_edit;
          const editor=document.querySelector('#blocks .block-editor'),html=editor.innerHTML;
          if(change==='page')State.activePageId='other-page';
          if(change==='block')State.pageData.blocks[0].id='other-block';
          if(change==='permission')State.pageData.page.can_edit=false;
          if(change==='text')editor.textContent='원격에서 바뀐 문장';
          applyToolbarFormat('bold');
          State.activePageId=pageId;State.pageData.blocks[0].id=blockId;State.pageData.page.can_edit=canEdit;
          editor.innerHTML=html;
        }""", change)
        expect(toolbar).not_to_be_visible()
        assert page.evaluate("JSON.stringify(State.pageData.blocks)") == original
        assert page.evaluate("State.dirty") is False


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("theme,width,height,motion", [("light",1280,800,"reduce"),("dark",390,600,"reduce"),("light",800,600,"no-preference")])
def test_inline_toolbar_focused_popovers_follow_scroll_with_native_lifetimes(document_page,tmp_path,theme,width,height,motion):
    from playwright.sync_api import expect

    page=document_page
    errors=[]
    page.on("pageerror",lambda error:errors.append(str(error)))
    page.set_viewport_size({"width":width,"height":height})
    page.emulate_media(reduced_motion=motion)
    page.evaluate("theme=>applyColorTheme(theme)",theme)
    _seed_blocks(page,[{"type":"paragraph","content":"스크롤 검증 문장"}]
        + [{"type":"paragraph","content":f"긴 문서의 문단 {index}"} for index in range(70)])
    origin="/".join(page.url.split("/",3)[:3])
    url=origin+f"/api/notion/pages/{page.evaluate('State.activePageId')}"
    original=page.request.get(url).json()
    toolbar=page.locator("#inlineToolbar")
    output=Path(os.environ.get("NOTION_INLINE_SCROLL_ARTIFACT_DIR",str(tmp_path)))
    output.mkdir(parents=True,exist_ok=True)
    for kind in ("toolbar","color","type","link"):
        page.mouse.move(width-25,height-30)
        page.mouse.wheel(0,-10000)
        page.wait_for_function("document.querySelector('#workspaceMain').scrollTop===0")
        _select_first_quote(page,"검증")
        expect(toolbar).to_be_visible()
        if kind=="toolbar":
            focused=toolbar.locator('[data-inline-format="bold"]')
            focused.focus()
        else:
            toolbar.locator(f'[data-inline-format="{kind}"]').click()
            focused=page.locator("#inlineLinkSearch" if kind=="link" else "#inlineFormatPopover")
        expect(focused).to_be_focused()
        if kind=="type" and focused.evaluate("el=>el.scrollHeight>el.clientHeight"):
            box=focused.bounding_box()
            page.mouse.move(box["x"]+box["width"]-15,box["y"]+box["height"]/2)
            page.mouse.wheel(0,150)
            page.wait_for_function("document.querySelector('#inlineFormatPopover').scrollTop>0")
            expect(focused).to_be_visible()
            expect(focused).to_be_focused()
        page.mouse.move(width-25,height-30)
        page.mouse.wheel(0,45)
        page.wait_for_function("document.querySelector('#workspaceMain').scrollTop>=40")
        expect(toolbar).to_be_visible()
        if kind=="type":
            expect(page.locator("#inlineFormatPopover")).not_to_be_visible()
            focused=toolbar.locator('[data-inline-format="type"]')
        expect(focused).to_be_focused()
        assert page.evaluate("State.inlineSelection.text")=="검증"
        assert toolbar.evaluate("el=>{const r=el.getBoundingClientRect();return r.left>=7&&r.right<=innerWidth-7&&r.top>=7&&r.bottom<=innerHeight-7;}")
        page.screenshot(path=str(output/f"inline-scroll-visible-{kind}-{theme}-{width}.png"))
        page.mouse.wheel(0,1200)
        page.wait_for_function("document.querySelector('#blocks .block-editor').getBoundingClientRect().bottom<0")
        if kind=="link":
            expect(toolbar).to_be_visible()
            expect(page.locator("#inlineLinkPopover")).to_be_visible()
            expect(focused).to_be_focused()
            assert page.evaluate("State.inlineSelection.text")=="검증"
            assert page.request.get(url).json()==original
            page.wait_for_function("""() => {
              const a=document.querySelector('#inlineToolbar [data-inline-format="link"]').getBoundingClientRect(),
                p=document.querySelector('#inlineLinkPopover').getBoundingClientRect();
              return Math.abs(p.top-a.bottom-4)<1 && Math.abs(p.left-a.left)<1;
            }""",timeout=2000)
            position=page.evaluate("""() => ({
              trigger:document.querySelector('#inlineToolbar [data-inline-format="link"]').getBoundingClientRect().toJSON(),
              popup:document.querySelector('#inlineLinkPopover').getBoundingClientRect().toJSON()
            })""")
            (output/f"inline-scroll-link-position-{theme}-{width}.json").write_text(json.dumps(position,ensure_ascii=False,indent=2),encoding="utf-8")
            page.screenshot(path=str(output/f"inline-scroll-retained-link-{theme}-{width}.png"))
            focused.press("Escape")
            toolbar.locator('[data-inline-format="link"]').press("Escape")
            expect(toolbar).not_to_be_visible()
            assert page.request.get(url).json()==original
            continue
        expect(toolbar).not_to_be_visible(timeout=2000)
        expect(page.locator("#inlineFormatPopover")).not_to_be_visible()
        expect(page.locator("#inlineLinkPopover")).not_to_be_visible()
        assert page.evaluate("State.inlineSelection===null && State.inlineFormat===null && State.inlineLink===null")
        assert page.evaluate("getSelection().toString()")=="검증"
        assert page.evaluate("!document.activeElement.closest('#inlineToolbar,#inlineFormatPopover,#inlineLinkPopover')")
        assert page.evaluate("!CSS.highlights.has('notion-inline-selection')")
        assert page.evaluate("document.querySelector('#workspaceMain').scrollTop")>1000
        assert page.request.get(url).json()==original
        page.screenshot(path=str(output/f"inline-scroll-closed-{kind}-{theme}-{width}.png"))
    assert not errors
