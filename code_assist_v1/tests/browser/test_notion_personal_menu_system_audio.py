"""Isolated local pages, synthetic system/mic streams; never captures a real device."""
import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _seed_blocks, _screenshot
from code_assist_v1.tests.browser.test_notion_ai_notes import _insert
from code_assist_v1.tests.browser.test_notion_audio import audio_models, fake_microphone, transcribe_reply, show_audio_settings
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready
from code_assist_v1.tests.test_notion_ai_notes import SOURCE, RESULT
from code_assist_v1.tests.notion_collaboration.test_workspace_images import png


def fake_display(page, mode="audio"):
    page.evaluate("""mode => {
      window.displayCalls = [];
      Object.defineProperty(navigator.mediaDevices, 'getDisplayMedia', {configurable: true, value: async options => {
        displayCalls.push({options, activated: navigator.userActivation.isActive});
        if (mode === 'cancel') throw new DOMException('cancel', 'NotAllowedError');
        const canvas = document.createElement('canvas'); canvas.width = canvas.height = 16;
        const video = canvas.captureStream().getVideoTracks()[0];
        const context = new AudioContext({sampleRate: 16000});
        const osc = context.createOscillator(); const dest = context.createMediaStreamDestination();
        osc.connect(dest); osc.start(); await context.resume();
        const stream = new MediaStream([video, ...(mode === 'audio' ? dest.stream.getAudioTracks() : [])]);
        window.syntheticDisplay = {stream, context, osc};
        return stream;
      }});
    }""", mode)


@pytest.mark.integration
@pytest.mark.regression
def test_personal_plus_menu_recommends_recent_use_without_extra_controls(document_page, tmp_path):
    from playwright.sync_api import expect
    page = document_page
    _seed_blocks(page, [{"type": "paragraph", "content": ""}])
    menu = page.locator("#blockMenu")
    def open_plus_menu():
        row = page.locator('#blocks .block-row').first
        row.hover()
        before_ids = page.evaluate('() => State.pageData.blocks.map(block => block.id)')
        row.locator('[data-action="insert"]').click()
        expect(menu).to_have_attribute('data-mode', 'inserted')
        after_ids = page.evaluate('() => State.pageData.blocks.map(block => block.id)')
        assert len(after_ids) == len(before_ids) + 1
        assert after_ids[0] == before_ids[0] and after_ids[2:] == before_ids[1:]
        assert page.evaluate('() => State.pageData.blocks[1].content') == ''
        return menu.locator('.menu-search')

    search = open_plus_menu()
    inserted_ids = page.evaluate('() => State.pageData.blocks.map(block => block.id)')
    expect(menu.locator('.slash-preferences, [data-slash-pin]')).to_have_count(0)
    expect(menu.locator('.block-menu-footer button')).to_have_count(1)
    search.fill("수학")
    expect(menu).to_be_visible()
    expect(page.locator("[data-equation-trigger]")).to_have_count(0)
    assert page.evaluate('() => State.pageData.blocks.map(block => block.id)') == inserted_ids
    menu.locator('.menu-search').fill("제목2")
    menu.locator('[data-insert-type="heading2"]:visible').first.click()
    assert page.evaluate('() => State.pageData.blocks.map(block => block.type)') == ['paragraph', 'heading2']
    page.evaluate('() => applyEditorHistory(-1)')
    assert page.evaluate('() => State.pageData.blocks.map(block => block.type)') == ['paragraph', 'paragraph']
    page.evaluate('() => applyEditorHistory(-1)')
    assert page.evaluate('() => State.pageData.blocks.map(block => [block.type,block.content])') == [['paragraph', '']]
    page.evaluate('() => applyEditorHistory(1)')
    page.evaluate('() => applyEditorHistory(1)')
    assert page.evaluate('() => State.pageData.blocks.map(block => block.type)') == ['paragraph', 'heading2']
    page.wait_for_function("() => !State.dirty && !State.saveInFlight")
    page.reload(); _wait_for_notion_ready(page)
    open_plus_menu()
    reopened_ids = page.evaluate('() => State.pageData.blocks.map(block => block.id)')
    expect(menu.locator('.slash-preferences, [data-slash-pin]')).to_have_count(0)
    expect(menu.locator('[aria-label="추천"] [data-insert-type]').first).to_have_attribute("data-insert-type", "heading2")
    _screenshot(page, tmp_path, "personal-plus-recommendations.png")
    menu.locator('.menu-search').press('Escape')
    expect(menu).to_be_hidden()
    assert page.evaluate('() => State.pageData.blocks.map(block => block.id)') == reopened_ids
    page.evaluate('() => applyEditorHistory(-1)')
    assert page.evaluate('() => State.pageData.blocks.map(block => block.type)') == ['paragraph', 'heading2']


@pytest.mark.integration
@pytest.mark.regression
def test_personal_plus_keyboard_adds_blank_without_opening_menu(document_page):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{"type":"paragraph","content":"기존 문장"}])
    page.evaluate('() => resetEditorHistory()')
    row = page.locator('#blocks .block-row').first
    row.locator('[data-action="insert"]').focus()
    row.locator('[data-action="insert"]').press('Enter')
    expect(page.locator('#blockMenu')).to_be_hidden()
    assert page.evaluate('() => State.pageData.blocks.map(block => [block.type,block.content])') == [
        ['paragraph','기존 문장'], ['paragraph','']]
    page.evaluate('() => applyEditorHistory(-1)')
    assert page.evaluate('() => State.pageData.blocks.map(block => [block.type,block.content])') == [
        ['paragraph','기존 문장']]


@pytest.mark.integration
@pytest.mark.regression
def test_personal_plus_file_chooser_waits_to_insert_until_file_is_selected(document_page):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{"type":"paragraph","content":"기존 본문"}])
    page.evaluate('() => resetEditorHistory()')
    original_ids = page.evaluate('() => State.pageData.blocks.map(block => block.id)')

    def choose_file(files, before=False):
        before_ids = page.evaluate('() => State.pageData.blocks.map(block => block.id)')
        row = page.locator('#blocks .block-row').first
        row.hover()
        row.locator('[data-action="insert"]').click(modifiers=['Alt'] if before else [])
        menu = page.locator('#blockMenu')
        expect(menu).to_have_attribute('data-mode', 'inserted')
        assert page.evaluate('() => State.pageData.blocks.length') == len(before_ids) + 1
        with page.expect_file_chooser() as chooser:
            menu.locator('[data-insert-type="file"]:visible').first.click()
        chooser.value.set_files(files)

    choose_file([])
    assert page.evaluate('() => State.pageData.blocks.length') == len(original_ids) + 1
    page.evaluate('() => applyEditorHistory(-1)')
    assert page.evaluate('() => State.pageData.blocks.map(block => [block.type,block.content])') == [['paragraph', '기존 본문']]
    assert page.evaluate('() => State.uploadMode === null && State.uploadTarget === null')
    choose_file({'name':'plus-attachment.png','mimeType':'image/png','buffer':png()})
    page.wait_for_function("""() => State.pageData.blocks.length===2
      && State.pageData.blocks[1].type==='file'
      && State.pageData.blocks[1].content==='plus-attachment.png'
      && !State.dirty && !State.saveInFlight""", timeout=30_000)
    assert page.evaluate('() => State.pageData.blocks[0].content') == '기존 본문'
    choose_file({'name':'before-paragraph.png','mimeType':'image/png','buffer':png()}, before=True)
    page.wait_for_function("""() => State.pageData.blocks.length===3
      && State.pageData.blocks[0].type==='file'
      && State.pageData.blocks[0].content==='before-paragraph.png'
      && State.pageData.blocks[1].content==='기존 본문'
      && !State.dirty && !State.saveInFlight""", timeout=30_000)
    page.reload(); _wait_for_notion_ready(page)
    assert page.evaluate('() => State.pageData.blocks.map(block => block.type)') == ['file','paragraph','file']


@pytest.mark.integration
@pytest.mark.regression
def test_personal_plus_divider_and_html_embed_preserve_the_blank_undo_step(document_page):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{"type":"paragraph","content":"원본"}])
    page.evaluate('() => resetEditorHistory()')

    def select_from_plus(type_name):
        row = page.locator('#blocks .block-row').first
        row.hover()
        row.locator('[data-action="insert"]').click()
        menu = page.locator('#blockMenu')
        expect(menu).to_have_attribute('data-mode', 'inserted')
        menu.locator(f'[data-insert-type="{type_name}"]:visible').first.click()

    select_from_plus('divider')
    assert page.evaluate('() => State.pageData.blocks.map(block => block.type)') == ['paragraph','divider','paragraph']
    page.evaluate('() => applyEditorHistory(-1)')
    assert page.evaluate('() => State.pageData.blocks.map(block => block.type)') == ['paragraph','paragraph']
    page.evaluate('() => applyEditorHistory(-1)')
    assert page.evaluate('() => State.pageData.blocks.map(block => block.type)') == ['paragraph']
    page.evaluate('() => applyEditorHistory(1)')
    page.evaluate('() => applyEditorHistory(1)')
    assert page.evaluate('() => State.pageData.blocks.map(block => block.type)') == ['paragraph','divider','paragraph']

    select_from_plus('html_embed')
    assert page.evaluate('() => State.pageData.blocks.map(block => block.type)') == ['paragraph','embed','divider','paragraph']
    assert page.evaluate('() => State.pageData.blocks[1].props.embed_mode') == 'html'
    page.evaluate('() => applyEditorHistory(-1)')
    assert page.evaluate('() => State.pageData.blocks.map(block => block.type)') == ['paragraph','paragraph','divider','paragraph']
    page.evaluate('() => applyEditorHistory(-1)')
    assert page.evaluate('() => State.pageData.blocks.map(block => block.type)') == ['paragraph','divider','paragraph']


@pytest.mark.integration
@pytest.mark.regression
def test_personal_plus_child_page_replaces_saved_blank_and_survives_reload(document_page):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{"type":"paragraph","content":"기존 본문"}])
    parent_id = page.evaluate('() => State.activePageId')
    original_id = page.evaluate('() => State.pageData.blocks[0].id')
    row = page.locator('#blocks .block-row').first
    row.hover()
    row.locator('[data-action="insert"]').click(modifiers=['Alt'])
    menu = page.locator('#blockMenu')
    expect(menu).to_have_attribute('data-mode', 'inserted')
    blank_id = page.evaluate('() => State.pageData.blocks[0].id')
    assert page.evaluate('() => State.pageData.blocks.map(block => block.content)') == ['', '기존 본문']
    page.wait_for_function('() => !State.dirty && !State.saveInFlight', timeout=30_000)
    expect(menu).to_be_visible()
    menu.locator('[data-insert-type="child_page"]:visible').first.click()
    page.wait_for_function("""() => State.pageData.blocks.length === 2
      && State.pageData.blocks[0].type === 'child_page'
      && State.pageData.blocks[0].props.page_id
      && !State.dirty && !State.saveInFlight""", timeout=30_000)
    child_id = page.evaluate('() => State.pageData.blocks[0].props.page_id')
    assert page.evaluate('() => State.pageData.blocks[0].id') == blank_id
    assert page.evaluate('() => State.pageData.blocks[1].id') == original_id
    assert page.evaluate('() => State.pageData.blocks[1].content') == '기존 본문'
    assert child_id != parent_id
    expect(page.locator(f'#blocks [data-child-page="{child_id}"]')).to_be_visible()
    page.reload(); _wait_for_notion_ready(page)
    assert page.evaluate('() => State.pageData.blocks.map(block => block.type)') == ['child_page','paragraph']
    assert page.evaluate('() => State.pageData.blocks[0].props.page_id') == child_id
    assert page.evaluate('() => State.pageData.blocks[1].content') == '기존 본문'


@pytest.mark.parametrize('block_type', ['layout_columns_3', 'tabs'])
@pytest.mark.integration
@pytest.mark.regression
def test_personal_plus_layout_wraps_saved_blank_with_one_undo_step(document_page, block_type):
    from playwright.sync_api import expect

    page = document_page
    _seed_blocks(page, [{"type":"paragraph","content":"원래 문단"}])
    page.evaluate('() => resetEditorHistory()')
    original_id = page.evaluate('() => State.pageData.blocks[0].id')
    row = page.locator('#blocks .block-row').first
    row.hover()
    row.locator('[data-action="insert"]').click(modifiers=['Alt'])
    menu = page.locator('#blockMenu')
    expect(menu).to_have_attribute('data-mode', 'inserted')
    blank_id = page.evaluate('() => State.insertedBlockMenu.blockId')
    page.wait_for_function('() => !State.dirty && !State.saveInFlight', timeout=30_000)
    expect(menu).to_be_visible()
    menu.locator(f'[data-insert-type="{block_type}"]:visible').first.click()
    assert page.evaluate('id => State.pageData.blocks.filter(block => block.id === id).length', blank_id) == 1
    assert page.evaluate('() => State.pageData.blocks.at(-1).id') == original_id
    assert page.evaluate('() => State.pageData.blocks.at(-1).content') == '원래 문단'
    if block_type == 'tabs':
        assert page.evaluate('''id => {
          const member = State.pageData.blocks.find(block => block.id === id);
          return State.pageData.blocks.some(block => block.type === "tabs"
            && member.props.tab_group === block.id && member.props.tab_id === block.props.tabs[0].id);
        }''', blank_id)
    else:
        assert page.evaluate('''id => {
          const member = State.pageData.blocks.find(block => block.id === id);
          return member.props.layout_column === 0 && member.props.layout_columns === 3
            && State.pageData.blocks.filter(block => block.props.layout_container).length === 1;
        }''', blank_id)
    page.evaluate('() => applyEditorHistory(-1)')
    assert page.evaluate('() => State.pageData.blocks.map(block => block.id)') == [blank_id, original_id]
    page.evaluate('() => applyEditorHistory(-1)')
    assert page.evaluate('() => State.pageData.blocks.map(block => block.id)') == [original_id]
    page.evaluate('() => applyEditorHistory(1)')
    page.evaluate('() => applyEditorHistory(1)')
    page.wait_for_function('() => !State.dirty && !State.saveInFlight', timeout=30_000)
    page.reload(); _wait_for_notion_ready(page)
    assert page.evaluate('id => State.pageData.blocks.filter(block => block.id === id).length', blank_id) == 1
    assert page.evaluate('() => State.pageData.blocks.at(-1).id') == original_id


@pytest.mark.integration
@pytest.mark.regression
def test_personal_plus_menu_shape_matches_reference_and_stays_usable_on_mobile(document_page, tmp_path):
    from playwright.sync_api import expect

    page = document_page
    page.set_viewport_size({"width": 1043, "height": 837})
    _seed_blocks(page, [{"type":"paragraph","content":"앞 문장 /"}])
    page.evaluate('() => setSidebarWidth(255, false)')
    page.locator('#pageTitle').fill('Codex UI 비교 — 슬래시·메뉴 2026-09-16')
    page.locator('#pageTitle').press('Tab')
    row = page.locator('#blocks .block-row').first
    row.hover()
    row.locator('[data-action="insert"]').click()
    menu = page.locator('#blockMenu')
    expect(menu).to_have_attribute('data-mode', 'inserted')
    expect(page.locator('#blocks .block-row').first.locator('.block-controls')).to_have_css('opacity', '0')
    expect(page.locator('#blocks .block-row').nth(1).locator('.block-controls')).to_have_css('pointer-events', 'none')
    page.evaluate('() => renderBlocks()')
    expect(page.locator('#blocks .block-row').nth(1).locator('.block-controls')).to_have_css('opacity', '0')
    search = menu.locator('.menu-search')
    expect(search).to_have_attribute('placeholder', '필터링 기준을 입력하세요.')
    geometry = menu.evaluate('''node => ({
      rect:node.getBoundingClientRect().toJSON(),
      searchRect:node.querySelector('.menu-search').getBoundingClientRect().toJSON(),
      placement:node.dataset.anchorPlacement,
      searchBorder:getComputedStyle(node.querySelector('.menu-search')).borderTopWidth
    })''')
    assert 300 <= geometry['rect']['width'] <= 312
    assert geometry['rect']['height'] <= 320
    assert geometry['searchBorder'] == '0px'
    assert geometry['placement'] == 'below'
    assert geometry['searchRect']['bottom'] <= geometry['rect']['top']
    _screenshot(page, tmp_path, 'personal-plus-menu-1043.png')
    page.set_viewport_size({"width": 390, "height": 700})
    page.wait_for_function('''() => {
      const rect = document.querySelector('#blockMenu').getBoundingClientRect();
      return rect.left >= 0 && rect.right <= innerWidth && rect.top >= 0 && rect.bottom <= innerHeight;
    }''')
    assert menu.evaluate('''node => node.querySelector('.menu-search').getBoundingClientRect().top
      >= node.getBoundingClientRect().top''')
    search.fill('제목')
    expect(menu.locator('[data-insert-type="heading1"]:visible').first).to_be_visible()
    _screenshot(page, tmp_path, 'personal-plus-menu-390.png')
    page.set_viewport_size({"width": 1043, "height": 400})
    page.wait_for_function('''() => {
      const menu = document.querySelector('#blockMenu');
      return menu.dataset.anchorPlacement === 'above'
        && menu.querySelector('.menu-search').getBoundingClientRect().top
          >= menu.getBoundingClientRect().top;
    }''')
    expect(search).to_be_visible()
    search.press('Escape')
    expect(menu).to_be_hidden()
    expect(page.locator('#blocks .block-row').nth(1).locator('.block-controls')).to_have_css('pointer-events', 'auto')
    assert page.evaluate('() => State.pageData.blocks.map(block => block.content)') == ['앞 문장 /', '']


@pytest.mark.parametrize("source", ["system", "mixed"])
@pytest.mark.integration
@pytest.mark.regression
def test_system_audio_worklet_translation_and_track_cleanup(document_page, tmp_path, source):
    from playwright.sync_api import expect
    page = document_page
    errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
    audio_models(page); fake_display(page); fake_microphone(page)
    summaries = []
    page.route("**/api/notion/ai-notes/transcribe", lambda route: transcribe_reply(route, SOURCE))
    def summarize(route):
        summaries.append(route.request.post_data_json)
        route.fulfill(json={"result": RESULT})
    page.route("**/api/notion/ai-notes/summarize", summarize)
    card = _insert(page)
    show_audio_settings(card)
    card.get_by_label("소리 입력", exact=True).select_option(source)
    card.locator(".ai-notes-options summary").click()
    card.get_by_label("번역·요약 언어", exact=True).select_option("en")
    card.get_by_label("음성 언어", exact=True).select_option("en")
    assert page.evaluate("() => displayCalls.length") == 0
    card.locator("[data-ai-record]").click()
    expect(card.locator(".ai-notes-badge")).to_have_text("잠정 요약", timeout=15000)
    assert page.evaluate("() => displayCalls[0].activated")
    assert page.evaluate("() => syntheticMicCalls") == (1 if source == "mixed" else 0)
    card.locator("[data-ai-pause]").click()
    assert page.evaluate("() => !syntheticDisplay.stream.getAudioTracks()[0].enabled")
    card.locator("[data-ai-pause]").click()
    card.locator("[data-ai-stop]").click()
    page.wait_for_function("() => syntheticDisplay.stream.getTracks().every(t => t.readyState === 'ended')")
    expect(card.locator(".ai-notes-badge")).to_have_text("회의 기록", timeout=15000)
    expect(card.locator("[data-ai-status]")).to_have_text("요약 저장됨", timeout=15000)
    assert all(item["output_language"] == "en" for item in summaries)
    assert page.evaluate("() => syntheticDisplay.stream.getTracks().every(t => t.readyState === 'ended')")
    if source == "mixed":
        assert page.evaluate("() => syntheticMic.stream.getTracks().every(t => t.readyState === 'ended')")
    _screenshot(page, tmp_path, "system-audio-" + source + ".png")
    assert not errors


@pytest.mark.parametrize("mode", ["empty", "cancel"])
@pytest.mark.integration
@pytest.mark.regression
def test_display_without_audio_or_cancel_never_uploads(document_page, mode):
    from playwright.sync_api import expect
    page = document_page
    audio_models(page); fake_display(page, mode)
    uploads = []
    page.route("**/api/notion/ai-notes/transcribe", lambda route: (uploads.append(True), route.abort()))
    card = _insert(page)
    card.locator("[data-ai-record]").click()
    expect(card.locator("[data-ai-audio-status]")).to_contain_text("공유된 오디오가 없습니다" if mode == "empty" else "취소")
    expect(card.locator("[data-ai-record]")).to_be_enabled()
    assert not uploads
    if mode == "empty":
        assert page.evaluate("() => syntheticDisplay.stream.getTracks().every(t => t.readyState === 'ended')")


@pytest.mark.integration
@pytest.mark.regression
def test_system_share_ended_stops_all_tracks(document_page):
    from playwright.sync_api import expect
    page = document_page
    audio_models(page); fake_display(page)
    page.route("**/api/notion/ai-notes/transcribe", lambda route: transcribe_reply(route, SOURCE))
    card = _insert(page)
    card.locator("[data-ai-auto-summary]").uncheck()
    card.locator("[data-ai-record]").click()
    expect(card.locator("[data-ai-pause]")).to_be_visible()
    page.evaluate("() => { const video = syntheticDisplay.stream.getVideoTracks()[0]; video.stop(); video.dispatchEvent(new Event('ended')); }")
    expect(card.locator("[data-ai-audio-status]")).to_contain_text("연결이 종료")
    page.wait_for_function("() => syntheticDisplay.stream.getTracks().every(t => t.readyState === 'ended')")


@pytest.mark.integration
@pytest.mark.regression
def test_mixed_mic_denial_releases_system_capture(document_page):
    from playwright.sync_api import expect
    page = document_page
    audio_models(page); fake_display(page)
    page.evaluate("""() => Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { configurable: true,
        value: async () => { throw new DOMException('synthetic denial', 'NotAllowedError'); } })""")
    card = _insert(page)
    show_audio_settings(card)
    card.get_by_label("소리 입력", exact=True).select_option("mixed")
    card.locator("[data-ai-record]").click()
    expect(card.locator("[data-ai-audio-status]")).to_contain_text("권한이 거부")
    assert page.evaluate("() => syntheticDisplay.stream.getTracks().every(t => t.readyState === 'ended')")
