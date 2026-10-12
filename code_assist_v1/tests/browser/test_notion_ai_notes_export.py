"""Downloads from isolated synthetic notes; no live user content or AI calls."""
import re

from playwright.sync_api import expect

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _seed_blocks, _screenshot
from code_assist_v1.tests.browser.test_notion_ai_notes import _insert, _models
from code_assist_v1.tests.browser.test_notion_audio import audio_models, fake_microphone, transcribe_reply, show_audio_settings
from code_assist_v1.tests.test_notion_ai_notes import SOURCE, RESULT


def download_text(page, card, tmp_path, kind, extension):
    card.get_by_label("다운로드 내용", exact=True).select_option(kind)
    card.get_by_label("다운로드 형식", exact=True).select_option(extension)
    with page.expect_download() as event:
        card.get_by_role("button", name="다운로드", exact=True).click()
    download = event.value
    assert download.suggested_filename.endswith("." + extension)
    assert download.suggested_filename.startswith("AI노트_")
    target = tmp_path / download.suggested_filename
    download.save_as(target)
    data = target.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    return data.decode("utf-8-sig")


def test_download_empty_and_exact_current_text_without_ai_or_save(document_page, tmp_path):
    page = document_page
    _models(page)
    card = _insert(page)
    expect(card.get_by_role("button", name="다운로드", exact=True)).to_be_disabled()
    card.get_by_label("회의록 원문", exact=True).fill(SOURCE + "\n이모지 😀")
    calls = []
    page.route("**/api/notion/ai-notes/summarize", lambda route: (calls.append(True), route.abort()))
    before = page.evaluate("() => JSON.stringify(State.pageData.blocks[0].props)")
    assert download_text(page, card, tmp_path, "transcript", "txt") == SOURCE + "\n이모지 😀"
    assert page.evaluate("() => JSON.stringify(State.pageData.blocks[0].props)") == before
    card.get_by_label("다운로드 내용", exact=True).select_option("summary")
    expect(card.get_by_role("button", name="다운로드", exact=True)).to_be_disabled()
    expect(card.locator("[data-ai-export-status]")).to_contain_text("아직 없습니다")
    assert calls == []


def test_summary_all_notes_formats_readonly_and_side_peek(document_page, tmp_path):
    page = document_page
    _models(page)
    props = {"transcript": SOURCE, "notes": "추가 회의 메모", "result": RESULT, "summary_phase": "provisional",
             "generated_source": "older version", "instructions": "private export-excluded instructions"}
    _seed_blocks(page, [{"type": "ai_notes", "props": props}])
    card = page.locator("#blocks .ai-notes")
    summary = download_text(page, card, tmp_path, "summary", "txt")
    assert RESULT["summary"] in summary and RESULT["action_items"][0]["task"] in summary
    assert "[잠정 요약]" in summary and "[이전 요약]" in summary
    assert "추가 회의 메모" not in summary
    all_notes = download_text(page, card, tmp_path, "all", "md")
    assert "## 회의록 원문" in all_notes and SOURCE in all_notes and "추가 회의 메모" in all_notes
    assert "private export-excluded instructions" not in all_notes
    _screenshot(page, tmp_path, "ai-notes-download-controls.png")
    assert download_text(page, card, tmp_path, "notes", "txt") == "추가 회의 메모"
    page.evaluate("() => openSidePeek(State.activePageId)")
    side = page.locator("#sidePeekBlocks .ai-notes")
    assert download_text(page, side, tmp_path, "transcript", "txt") == SOURCE
    page.locator("#sidePeekCloseBtn").click()
    page.evaluate("() => { State.pageData.page.can_edit = false; renderBlocks(); }")
    expect(card.locator("[data-ai-generate]")).to_have_count(0)
    assert download_text(page, card, tmp_path, "transcript", "txt") == SOURCE
    page.set_viewport_size({"width": 600, "height": 900})
    controls = card.locator(".ai-notes-export")
    assert controls.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
    _screenshot(page, tmp_path, "ai-notes-download-narrow.png")


def test_download_during_capture_is_snapshot_and_does_not_stop_recording(document_page, tmp_path):
    page = document_page
    audio_models(page); fake_microphone(page)
    page.route("**/api/notion/ai-notes/transcribe", lambda route: transcribe_reply(route, SOURCE))
    summaries = []
    page.route("**/api/notion/ai-notes/summarize", lambda route: (summaries.append(True), route.abort()))
    card = _insert(page)
    show_audio_settings(card)
    card.get_by_label("소리 입력", exact=True).select_option("microphone")
    card.locator("[data-ai-auto-summary]").uncheck()
    card.locator("[data-ai-record]").click()
    expect(card.locator('[data-ai-field="transcript"]')).to_have_value(re.compile(".*민수.*", re.S), timeout=15000)
    text = download_text(page, card, tmp_path, "transcript", "txt")
    assert SOURCE in text
    assert page.evaluate("() => syntheticMic.stream.getAudioTracks().every(t => t.readyState === 'live')")
    expect(card.locator("[data-ai-stop]")).to_be_visible()
    card.locator("[data-ai-stop]").click()
    expect(card.locator("[data-ai-audio-status]")).to_contain_text("전사 완료", timeout=15000)
    assert summaries == []
