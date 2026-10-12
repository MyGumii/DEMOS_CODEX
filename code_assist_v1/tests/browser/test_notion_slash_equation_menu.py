import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _seed_blocks, _screenshot
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready


@pytest.mark.integration
@pytest.mark.regression
def test_ui_slash_reference_order_search_and_keyboard(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({"width": 1280, "height": 720})
    _seed_blocks(page, [{"type": "paragraph", "content": ""}])
    block_editor = page.locator("#blocks .block-editor")
    block_editor.fill("/")
    menu = page.locator("#blockMenu")
    _screenshot(page, tmp_path, "slash-reference-order.png")
    expect(block_editor).to_be_focused()
    expect(block_editor).to_have_attribute("data-slash-hint", "검색어를 입력하세요")
    assert block_editor.evaluate("element => getComputedStyle(element).outlineStyle") == "none"
    assert block_editor.evaluate("element => getComputedStyle(element).backgroundColor") == "rgba(0, 0, 0, 0)"
    block_editor.press("Escape")
    expect(menu).to_be_hidden()
    expect(block_editor).to_have_text("/")
    assert block_editor.get_attribute("data-slash-hint") is None
    block_editor.fill("")
    block_editor.fill("/")
    expect(menu).to_be_visible()
    types = menu.locator("[data-insert-type]").evaluate_all("es => es.map(e => e.dataset.insertType)")
    assert types[:19] == ["ai_notes", "html_embed", "table", "heading1", "paragraph", "heading1", "heading2", "heading3", "heading4", "bullet", "numbered", "todo", "toggle", "child_page", "callout", "quote", "simple_table", "divider", "link_to_page"]
    assert types[19:25] == ["image", "video", "audio", "code", "file", "bookmark"]
    assert types[25:31] == ["table", "board", "gallery", "list", "calendar", "timeline"]
    assert not set(types).intersection({"chart", "dashboard", "form", "feed", "map"})
    assert menu.locator("[data-insert-type] svg").count() == len(types)
    ids = menu.locator("[role=option]").evaluate_all("es=>es.map(e=>e.id)")
    assert len(ids) == len(set(ids))
    block_editor.fill("/heading")
    expect(menu.locator('[data-insert-type="heading2"]')).to_be_visible()
    expect(block_editor).not_to_have_attribute("data-slash-hint", "검색어를 입력하세요")
    block_editor.fill("/does-not-exist")
    expect(menu.locator("[data-menu-empty]")).to_be_visible()
    block_editor.fill("/")
    expect(menu.locator('[data-insert-type="ai_notes"]').first).to_be_visible()
    expect(menu.locator("[data-menu-empty]")).to_be_hidden()
    block_editor.fill("/수학")
    expect(menu.locator('[data-insert-type="equation"]')).to_be_visible()
    block_editor.press("Enter")
    assert page.locator("#blocks [data-slash-hint]").count() == 0
    editor = page.get_by_role("textbox", name="수식(LaTeX)", exact=True)
    expect(editor).to_be_focused()
    editor.fill(r"$\alpha$")
    expect(page.locator("#blocks .katex")).to_have_count(1)
    page.wait_for_function("() => !State.dirty && !State.saveInFlight")
    expect(editor).to_be_visible()
    editor.fill(r"\alpha")
    editor.press("Shift+Enter")
    editor.type(r"+\beta")
    assert "\n" in editor.input_value()
    editor.press("Enter")
    expect(editor).to_have_count(0)
    expect(page.locator("#blocks .block-row")).to_have_count(1)
    expect(page.locator("#blocks .katex")).to_have_count(1)
    page.locator("#blocks [data-equation-trigger]").click()
    page.locator("#pageTitle").click()
    expect(editor).to_have_count(0)
    # Page navigation closes a stale popover and cannot edit the next page.
    page.locator("#blocks [data-equation-trigger]").click()
    page.evaluate("() => { State.pageLoadSequence += 1; }")
    expect(editor).to_have_count(0)


@pytest.mark.integration
@pytest.mark.regression
def test_ui_fourth_heading_and_toggle_survive_slash_insert_and_reload(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{"type": "paragraph", "content": ""}, {"type": "paragraph", "content": ""}])
    page.locator("#blocks .block-editor").nth(0).fill("/제목4")
    page.locator('#blockMenu [data-insert-type="heading4"]').first.click()
    heading = page.locator('#blocks [data-type="heading4"] .block-editor')
    expect(heading).to_have_count(1)
    heading.fill("네 번째 제목")
    assert heading.evaluate("element => { const style=getComputedStyle(element); return [style.fontSize, style.fontWeight, style.lineHeight]; }") == ["18px", "600", "23.4px"]
    _screenshot(page, tmp_path, "heading4-reference.png")
    page.locator('#blocks [data-type="paragraph"] .block-editor').fill("/제목4")
    page.locator('#blockMenu [data-convert-type="toggle_heading4"]').click()
    toggle = page.locator('#blocks [data-type="toggle_heading4"]')
    expect(toggle).to_have_count(1)
    toggle.locator('.block-editor').fill("접는 네 번째 제목")
    toggle.locator('.toggle-button').click()
    assert page.evaluate("() => State.pageData.blocks.filter(b => b.type === 'toggle_heading4')[0].props.open") is False
    page.evaluate("() => savePage()")
    page.reload()
    _wait_for_notion_ready(page)
    expect(page.locator('#blocks [data-type="heading4"] .block-editor')).to_have_text("네 번째 제목")
    expect(page.locator('#blocks [data-type="toggle_heading4"] .block-editor')).to_have_text("접는 네 번째 제목")
    assert page.evaluate("() => State.pageData.blocks.filter(b => b.type === 'toggle_heading4')[0].props.open") is False


@pytest.mark.integration
@pytest.mark.regression
def test_ui_slash_heading_search_distinguishes_insert_and_convert(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{"type": "paragraph", "content": ""}])
    editor = page.locator("#blocks .block-editor")
    editor.fill("앞 문장 /제목4")
    menu = page.locator("#blockMenu")
    expect(menu.locator("[data-filtered-label]")).to_be_visible()
    expect(menu.locator('[data-insert-type="heading4"]')).to_be_visible()
    expect(menu.locator('[data-convert-type="heading4"]')).to_be_visible()
    expect(menu.locator('[data-convert-type="toggle_heading4"]')).to_be_visible()
    assert menu.locator('[data-insert-type="toggle_heading4"]').count() == 0
    assert menu.locator("[data-command-group]:visible").count() == 2
    assert 170 <= menu.bounding_box()["height"] <= 185
    expect(menu.get_by_label("추천 설정", exact=True)).to_be_hidden()
    _screenshot(page, tmp_path, "slash-heading4-filtered.png")
    menu.locator('[data-convert-type="heading4"]').click()
    expect(page.locator("#blocks .block-row")).to_have_count(1)
    expect(page.locator('#blocks [data-type="heading4"] .block-editor')).to_have_text("앞 문장 ")
    assert page.evaluate("() => State.pageData.blocks[0].content") == "앞 문장 "

    page.locator('#blocks [data-type="heading4"] .block-editor').fill("앞 문장 /제목4")
    assert menu.locator('[data-insert-type="heading4"]:visible').count() == 1
    menu.locator('[data-insert-type="heading4"]').first.click()
    expect(page.locator("#blocks .block-row")).to_have_count(2)
    assert page.evaluate("() => State.pageData.blocks.map(b => [b.type, b.content])") == [
        ["heading4", "앞 문장 "], ["heading4", ""],
    ]
    page.locator("#blocks .block-editor").nth(1).fill("/제목1")
    expect(menu.locator('[data-insert-type="heading1"]')).to_be_visible()
    expect(menu.locator('[data-insert-type="toggle_heading1"]')).to_be_visible()
    expect(menu.locator('[data-convert-type="heading1"]')).to_be_visible()
    expect(menu.locator('[data-convert-type="toggle_heading1"]')).to_be_visible()
    page.locator("#blocks .block-editor").nth(1).fill("/토글 제목4")
    expect(menu.locator("[data-menu-empty]")).to_be_visible()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_html_embed_edits_persists_and_is_isolated(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _seed_blocks(page, [{"type": "paragraph", "content": ""}])
    page.locator("#blocks .block-editor").fill("/")
    page.locator('#blockMenu [data-insert-type="html_embed"]').first.click()
    source = '<h2>안전한 HTML</h2><style>h2 { color: rgb(10, 90, 180); }</style><script>parent.__htmlEscaped = true</script><img src="https://example.invalid/leak"><a href="https://example.invalid/go">외부 링크</a><meta http-equiv="refresh" content="0; url=https://example.invalid/">'
    editor = page.get_by_role("textbox", name="HTML 소스")
    editor.fill(source)
    page.locator("[data-html-apply]").click()
    expect(editor).to_be_hidden()
    frame = page.locator('iframe[title="HTML 미리보기"]')
    expect(frame).to_have_attribute("sandbox", "")
    expect(page.frame_locator('iframe[title="HTML 미리보기"]').get_by_role("heading", name="안전한 HTML")).to_be_visible()
    assert "<script" not in frame.get_attribute("srcdoc")
    assert "example.invalid" not in frame.get_attribute("srcdoc")
    assert page.evaluate("() => window.__htmlEscaped === undefined")
    _screenshot(page, tmp_path, "html-embed-sandbox.png")
    page.evaluate("() => savePage()")
    page.reload()
    _wait_for_notion_ready(page)
    assert page.evaluate("() => State.pageData.blocks[0].content") == source
    assert page.evaluate("() => State.pageData.blocks[0].props.embed_mode") == "html"
    page.locator("[data-html-edit]").click()
    page.locator("[data-html-file]").set_input_files({"name": "sample.html", "mimeType": "text/html", "buffer": b"<p>uploaded fixture</p>"})
    expect(editor).to_have_value("<p>uploaded fixture</p>")
    page.locator("[data-html-apply]").click()
    page.evaluate("() => savePage()")
    page.evaluate("() => { State.pageData.page.can_edit=false; renderBlocks(); }")
    expect(page.locator("[data-html-edit]")).to_have_count(0)
    expect(page.locator('iframe[title="HTML 미리보기"]')).to_be_visible()
    assert not errors


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("count", [4, 5])
def test_ui_slash_column_layout_persists(document_page, count):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{"type": "paragraph", "content": ""}])
    page.locator("#blocks .block-editor").fill(f"/{count}개의 열")
    page.locator(f'#blockMenu [data-insert-type="layout_columns_{count}"]').click()
    expect(page.locator("#blocks .block-editor")).to_have_count(count)
    assert page.evaluate("() => State.pageData.blocks.filter(b => !b.props.layout_container).map(b => b.props.layout_column)") == list(range(count))
    assert page.evaluate("() => State.pageData.blocks.filter(b => !b.props.layout_container).every(b => b.props.layout_columns === " + str(count) + ")")
    assert page.evaluate("() => State.pageData.blocks.filter(b => b.props.layout_container).length") == 1
    page.evaluate("() => savePage()")
    page.reload()
    _wait_for_notion_ready(page)
    expect(page.locator("#blocks .block-editor")).to_have_count(count)
    assert page.evaluate("() => State.pageData.blocks.filter(b => !b.props.layout_container).map(b => b.props.layout_column)") == list(range(count))
