import json

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _screenshot
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready
from code_assist_v1.tests.test_notion_ai_notes import SOURCE, RESULT, MODELS


def _models(page):
    page.route("**/api/notion/ai-notes/capabilities", lambda route: route.fulfill(json={"models": MODELS, "audio_transcription": False}))


def _insert(page):
    from playwright.sync_api import expect
    page.locator("#blocks .block-editor").first.fill("/회의")
    page.locator('#blockMenu [data-command-group][aria-label="추천"] [data-insert-type="ai_notes"]').click()
    expect(page.locator("#blocks .ai-notes")).to_be_visible()
    return page.locator("#blocks .ai-notes")


@pytest.mark.integration
@pytest.mark.regression
def test_ai_notes_generate_persist_citations_checklist_and_side_peek(document_page, tmp_path):
    from playwright.sync_api import expect
    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _models(page)
    requests = []

    def generate(route):
        requests.append(route.request.post_data_json)
        route.fulfill(json={"status": "draft", "result": RESULT, "model_name": "Gemma4 (test)", "provider": "hynix_api", "source_sha256": "test"})

    page.route("**/api/notion/ai-notes/summarize", generate)
    card = _insert(page)
    card.get_by_label("회의록 원문", exact=True).fill(SOURCE)
    card.get_by_role("tab", name="메모", exact=True).click()
    card.get_by_label("회의 메모", exact=True).fill("주간 회의 메모")
    card.get_by_text("요약 설정", exact=True).click()
    card.get_by_label("AI 노트 모델", exact=True).select_option("gemma4")
    card.get_by_label("AI 노트 요약 지침", exact=True).fill("핵심 일정 중심으로 정리")
    card.get_by_role("button", name="요약 생성", exact=True).click()
    expect(card.locator(".ai-notes-summary")).to_have_text(RESULT["summary"], timeout=15000)
    expect(card.locator("[data-ai-status]")).to_have_text("요약 저장됨", timeout=15000)
    expect(card.locator("[data-ai-stale]")).to_be_hidden()
    assert len(requests) == 1
    assert requests[0]["transcript"] == SOURCE
    assert requests[0]["model_id"] == "gemma4"
    card.get_by_role("button", name="근거", exact=True).last.click()
    transcript = card.get_by_label("회의록 원문", exact=True)
    expect(transcript).to_be_focused()
    assert transcript.evaluate("e => e.value.substring(e.selectionStart, e.selectionEnd)") == RESULT["action_items"][0]["source_quote"]
    card.get_by_role("tab", name="요약", exact=True).click()
    card.locator("[data-ai-done]").check()
    page.evaluate("() => savePage()")
    page.reload()
    _wait_for_notion_ready(page)
    expect(card.locator(".ai-notes-summary")).to_have_text(RESULT["summary"])
    expect(card.locator("[data-ai-done]")).to_be_checked()
    assert page.evaluate("() => State.pageData.blocks[0].props.transcript") == SOURCE
    assert page.evaluate("() => State.pageData.blocks[0].content.includes('주간 회의 메모')")
    page.evaluate("() => openSidePeek(State.activePageId)")
    side = page.locator("#sidePeekBlocks .ai-notes")
    expect(side.locator(".ai-notes-summary")).to_have_text(RESULT["summary"])
    side.get_by_role("tab", name="메모", exact=True).click()
    side.get_by_label("회의 메모", exact=True).fill("사이드 보기에서 수정")
    assert page.evaluate("() => State.sidePeek.data.blocks[0].props.notes") == "사이드 보기에서 수정"
    page.evaluate("() => saveSidePeek()")
    assert page.evaluate("() => State.sidePeek.data.blocks[0].props.notes") == "사이드 보기에서 수정"
    page.locator("#sidePeekCloseBtn").click()
    page.reload()
    _wait_for_notion_ready(page)
    assert page.evaluate("() => State.pageData.blocks[0].props.notes") == "사이드 보기에서 수정"
    assert page.evaluate("() => State.pageData.blocks[0].props.generated_source !== NotionAiNotes.sourceKey(State.pageData.blocks[0].props)")
    expect(card.locator("[data-ai-stale]")).to_be_visible()
    _screenshot(page, tmp_path, "ai-notes-summary.png")
    page.evaluate("() => openSidePeek(State.activePageId)")
    side.get_by_role("button", name="요약 다시 생성", exact=True).click()
    expect(side.locator("[data-ai-status]")).to_have_text("요약 저장됨", timeout=15000)
    expect(side.locator("[data-ai-generate]")).to_be_enabled()
    expect(card.locator("[data-ai-generate]")).to_be_enabled()
    page.locator("#sidePeekCloseBtn").click()
    page.evaluate("() => { State.pageData.page.can_edit = false; renderBlocks(); }")
    expect(card.locator("[data-ai-generate]")).to_have_count(0)
    expect(card.locator("[data-ai-done]")).to_be_disabled()
    assert not errors


@pytest.mark.integration
@pytest.mark.regression
def test_ai_notes_failure_import_keyboard_and_stale_response(document_page):
    from playwright.sync_api import expect
    page = document_page
    _models(page)
    card = _insert(page)
    card.locator("[data-ai-import]").set_input_files({"name": "meeting.txt", "mimeType": "text/plain", "buffer": SOURCE.encode("utf-8")})
    expect(card.get_by_label("회의록 원문", exact=True)).to_have_value(SOURCE)
    tab = card.get_by_role("tab", name="원문", exact=True)
    tab.focus()
    tab.press("Home")
    expect(card.get_by_role("tab", name="요약", exact=True)).to_be_focused()
    page.route("**/api/notion/ai-notes/summarize", lambda route: route.fulfill(status=503, json={"error": "모델 연결 실패. 원문은 유지됩니다."}))
    card.get_by_role("button", name="요약 생성", exact=True).click()
    expect(card.locator("[data-ai-status]")).to_contain_text("모델 연결 실패", timeout=15000)
    assert page.evaluate("() => State.pageData.blocks[0].props.transcript") == SOURCE
    page.unroute("**/api/notion/ai-notes/summarize")
    pending = []
    page.route("**/api/notion/ai-notes/summarize", lambda route: pending.append(route))
    with page.expect_request("**/api/notion/ai-notes/summarize"):
        card.get_by_role("button", name="요약 생성", exact=True).click()
    expect(card.locator("[data-ai-generate]")).to_be_disabled()
    card.get_by_role("tab", name="원문", exact=True).click()
    card.get_by_label("회의록 원문", exact=True).fill(SOURCE + "\n추가 내용")
    assert pending
    pending[0].fulfill(json={"result": RESULT})
    expect(card.locator("[data-ai-status]")).to_contain_text("원문이 변경", timeout=15000)
    assert page.evaluate("() => !State.pageData.blocks[0].props.result")
