"""Direct summary edits on isolated synthetic pages only; model/audio routes mocked."""
from playwright.sync_api import expect

from code_assist_v1.tests.browser.test_notion_document_extras import document_page
from code_assist_v1.tests.browser.test_notion_ai_notes import _models
from code_assist_v1.tests.browser.test_notion_ai_notes_clear import seed
from code_assist_v1.tests.browser.test_notion_audio import audio_models, fake_microphone, transcribe_reply, show_audio_settings
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready
from code_assist_v1.tests.test_notion_ai_notes import SOURCE, RESULT


def test_summary_direct_edit_save_reload_export_and_side_peek(document_page):
    page = document_page
    errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
    _models(page)
    card = seed(page)
    card.get_by_role("tab", name="요약", exact=True).click()
    editor = card.get_by_label("요약 직접 수정", exact=True)
    expect(editor).to_be_editable()
    text = "사용자가 수정한 요약\n한국어 日本語 English <b>plain text</b>"
    editor.fill(text)
    editor.press("Control+s")
    expect(card.locator("[data-ai-status]")).to_have_text("수정한 요약 저장됨")
    props = page.evaluate("() => State.pageData.blocks[0].props")
    assert props["result"] == {**RESULT, "summary": text}
    assert props["transcript"] == SOURCE and props["notes"] == "보존할 메모"
    assert props["summary_manually_edited"] and props["summary_edited_at"]
    assert props["generated_source"] == "previous source"
    assert page.evaluate("() => State.pageData.blocks[1].content") == "다른 블록은 유지"
    page.reload(); _wait_for_notion_ready(page)
    card.get_by_role("tab", name="요약", exact=True).click()
    expect(editor).to_have_value(text)
    card.locator("[data-ai-export-kind]").select_option("summary")
    card.locator("[data-ai-export-format]").select_option("txt")
    with page.expect_download() as download:
        card.locator("[data-ai-export]").click()
    exported = download.value.path().read_text(encoding="utf-8-sig")
    assert text in exported and "[직접 수정한 요약]" in exported
    page.evaluate("() => openSidePeek(State.activePageId)")
    side = page.locator("#sidePeekBlocks .ai-notes")
    side.get_by_role("tab", name="요약", exact=True).click()
    side.get_by_label("요약 직접 수정", exact=True).fill("사이드 보기에서 수정한 요약")
    side.get_by_role("tab", name="메모", exact=True).click()  # blur persists
    expect(side.locator("[data-ai-status]")).to_have_text("수정한 요약 저장됨")
    page.locator("#sidePeekCloseBtn").click()
    page.reload(); _wait_for_notion_ready(page)
    card.get_by_role("tab", name="요약", exact=True).click()
    expect(editor).to_have_value("사이드 보기에서 수정한 요약")
    editor.fill("")
    # No blur, explicit save, or other click: idle autosave must persist an empty edit.
    expect(card.locator("[data-ai-status]")).to_have_text("수정한 요약 저장됨")
    page.reload(); _wait_for_notion_ready(page)
    expect(editor).to_have_value("")
    assert page.evaluate("() => State.pageData.blocks[0].props.result.decisions") == RESULT["decisions"]
    assert not errors


def test_summary_edit_save_failure_retry_permission_and_comparison_guards(document_page):
    page = document_page
    _models(page)
    card = seed(page)
    card.get_by_role("tab", name="요약", exact=True).click()
    page.evaluate("() => { window.restoreSummarySave = window.flushPageDraft; window.flushPageDraft = async () => false; }")
    editor = card.get_by_label("요약 직접 수정", exact=True)
    editor.fill("저장 실패에도 보존할 수정")
    editor.press("Control+s")
    expect(card.locator("[data-ai-status]")).to_contain_text("저장하지 못했습니다")
    expect(editor).to_have_value("저장 실패에도 보존할 수정")
    page.evaluate("() => { window.flushPageDraft = window.restoreSummarySave; }")
    editor.press("Control+s")
    expect(card.locator("[data-ai-status]")).to_have_text("수정한 요약 저장됨")
    page.evaluate("() => { State.pageData.page.can_edit = false; }")
    editor.fill("권한 회수 후 반영하면 안 됨")
    expect(editor).to_have_value("저장 실패에도 보존할 수정")
    expect(editor).not_to_be_editable()
    page.evaluate("() => renderBlocks()")
    expect(editor).to_have_count(0)
    expect(card.locator(".ai-notes-summary")).to_have_text("저장 실패에도 보존할 수정")
    page.evaluate("() => { State.pageData.page.can_edit = true; State.pageData.blocks[0].props.comparison_enabled = true; renderBlocks(); }")
    expect(editor).to_have_count(0)
    expect(card.locator(".ai-notes-summary")).to_have_count(0)


def test_summary_regeneration_confirmation_and_late_result_guard(document_page):
    page = document_page
    _models(page)
    pending = []
    page.route("**/api/notion/ai-notes/summarize", lambda route: pending.append(route))
    card = seed(page)
    card.get_by_role("tab", name="요약", exact=True).click()
    editor = card.get_by_label("요약 직접 수정", exact=True)
    editor.fill("보호할 직접 수정본")
    editor.press("Control+s")
    expect(card.locator("[data-ai-status]")).to_have_text("수정한 요약 저장됨")
    page.once("dialog", lambda dialog: dialog.dismiss())
    card.locator("[data-ai-generate]").click()
    assert pending == []
    expect(editor).to_have_value("보호할 직접 수정본")
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_request("**/api/notion/ai-notes/summarize"):
        card.locator("[data-ai-generate]").click()
    expect(editor).not_to_be_editable()
    # Simulate a newer edit delivered by another view while AI is responding.
    page.evaluate("() => { Object.assign(State.pageData.blocks[0].props, NotionAiNotes.editedSummary(State.pageData.blocks[0].props, '다른 보기의 최신 수정')); }")
    pending.pop().fulfill(json={"result": RESULT})
    expect(card.locator("[data-ai-status]")).to_contain_text("늦게 도착한 AI 결과를 반영하지 않았습니다")
    expect(editor).to_have_value("다른 보기의 최신 수정")
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_request("**/api/notion/ai-notes/summarize"):
        card.locator("[data-ai-generate]").click()
    pending.pop().fulfill(json={"result": RESULT})
    expect(card.locator("[data-ai-status]")).to_have_text("요약 저장됨")
    expect(editor).to_have_value(RESULT["summary"])
    assert page.evaluate("() => State.pageData.blocks[0].props.summary_manually_edited") is None


def test_manual_summary_survives_auto_summary_and_meeting_finish(document_page):
    page = document_page
    audio_models(page); fake_microphone(page)
    calls = []
    page.route("**/api/notion/ai-notes/summarize", lambda route: (calls.append(True), route.abort()))
    page.route("**/api/notion/ai-notes/transcribe", lambda route: transcribe_reply(route, "Synthetic later speech."))
    card = seed(page)
    card.get_by_role("tab", name="요약", exact=True).click()
    card.get_by_label("요약 직접 수정", exact=True).fill("자동 요약이 덮어쓰면 안 되는 수정본")
    show_audio_settings(card)
    card.get_by_label("소리 입력", exact=True).select_option("microphone")
    card.locator("[data-ai-auto-summary]").check()
    card.locator("[data-ai-record]").click()
    page.wait_for_function("() => State.pageData.blocks[0].props.transcript.includes('Synthetic later speech.')", timeout=15000)
    card.locator("[data-ai-stop]").click()
    expect(card.locator("[data-ai-audio-status]")).to_contain_text("전사 완료", timeout=15000)
    expect(card.locator("[data-ai-status]")).to_contain_text("직접 수정한 요약을 보존했습니다")
    assert page.evaluate("() => State.pageData.blocks[0].props.result.summary") == "자동 요약이 덮어쓰면 안 되는 수정본"
    assert calls == []
