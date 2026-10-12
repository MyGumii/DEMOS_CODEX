"""Clear only isolated synthetic AI notes; never live user data or microphone."""
from playwright.sync_api import expect

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _seed_blocks, _screenshot
from code_assist_v1.tests.browser.test_notion_ai_notes import _models, _insert
from code_assist_v1.tests.browser.test_notion_audio import audio_models, fake_microphone, transcribe_reply, show_audio_settings
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready
from code_assist_v1.tests.test_notion_ai_notes import SOURCE, RESULT


def seed(page):
    _seed_blocks(page, [{"type": "ai_notes", "props": {
        "transcript": SOURCE, "notes": "보존할 메모", "result": RESULT,
        "instructions": "보존할 지침", "model_id": "gemma4", "output_language": "ko",
        "generated_source": "previous source", "summary_phase": "final", "generated_at": "2026-09-11T00:00:00Z",
        "provider": "synthetic", "model_name": "Synthetic", "source_sha256": "synthetic",
    }}, {"type": "paragraph", "content": "다른 블록은 유지"}])
    return page.locator("#blocks .ai-notes")


def clear(page, card, kind, accept=True):
    if kind != "all":
        card.locator(f'[data-ai-tab="{kind}"]').click()
    messages = []

    def confirm(dialog):
        messages.append(dialog.message)
        dialog.accept() if accept else dialog.dismiss()

    page.once("dialog", confirm)
    card.locator(f'[data-ai-clear="{kind}"]').click()
    assert len(messages) == 1 and "먼저 다운로드" in messages[0]
    if accept:
        expect(card.locator("[data-ai-status]")).to_have_text("내용을 비우고 저장했습니다.", timeout=15000)


def test_clear_cancel_individual_scope_and_persistence(document_page, tmp_path):
    page = document_page
    _models(page)
    calls = []
    page.route("**/api/notion/ai-notes/summarize", lambda route: (calls.append(True), route.abort()))
    card = seed(page)
    before = page.evaluate("() => JSON.stringify(State.pageData.blocks)")
    clear(page, card, "transcript", accept=False)
    assert page.evaluate("() => JSON.stringify(State.pageData.blocks)") == before
    clear(page, card, "transcript")
    props = page.evaluate("() => State.pageData.blocks[0].props")
    assert props["transcript"] == "" and props["notes"] == "보존할 메모" and props["result"] == RESULT
    expect(card.locator('[data-ai-clear="transcript"]')).to_be_disabled()
    card.get_by_role("tab", name="요약", exact=True).click()
    expect(card.locator("[data-ai-stale]")).to_be_visible()
    clear(page, card, "summary")
    props = page.evaluate("() => State.pageData.blocks[0].props")
    assert props["notes"] == "보존할 메모" and "result" not in props and "generated_source" not in props
    expect(card.locator(".ai-notes-summary")).to_have_count(0)
    clear(page, card, "notes")
    expect(card.locator('[data-ai-clear="all"]')).to_be_disabled()
    _screenshot(page, tmp_path, "ai-notes-clear-empty.png")
    page.reload(); _wait_for_notion_ready(page)
    props = page.evaluate("() => State.pageData.blocks[0].props")
    assert props["transcript"] == props["notes"] == "" and "result" not in props
    assert props["instructions"] == "보존할 지침" and props["model_id"] == "gemma4" and props["output_language"] == "ko"
    assert page.evaluate("() => State.pageData.blocks[1].content") == "다른 블록은 유지"
    assert not calls


def test_clear_all_side_peek_readonly_and_permission_recheck(document_page, tmp_path):
    page = document_page
    _models(page)
    card = seed(page)
    page.evaluate("() => openSidePeek(State.activePageId)")
    side = page.locator("#sidePeekBlocks .ai-notes")
    clear(page, side, "all")
    expect(side.locator('[data-ai-clear="all"]')).to_be_disabled()
    page.locator("#sidePeekCloseBtn").click()
    page.reload(); _wait_for_notion_ready(page)
    props = page.evaluate("() => State.pageData.blocks[0].props")
    assert props["transcript"] == props["notes"] == "" and "result" not in props
    assert props["instructions"] == "보존할 지침" and props["model_id"] == "gemma4"
    card = seed(page)
    # Permission may change while a confirmation is presented: handler must recheck.
    page.evaluate("() => { window.confirm = () => { State.pageData.page.can_edit = false; return true; }; }")
    card.locator('[data-ai-clear="all"]').click()
    assert page.evaluate("() => State.pageData.blocks[0].props.transcript") == SOURCE
    page.evaluate("() => renderBlocks()")
    expect(card.locator("[data-ai-clear]")).to_have_count(0)
    expect(card.locator("[data-ai-export]")).to_be_enabled()
    _screenshot(page, tmp_path, "ai-notes-clear-readonly.png")


def test_clear_blocked_during_recording_pause_pending_transcription_and_summary(document_page):
    page = document_page
    audio_models(page); fake_microphone(page)
    pending = []
    page.route("**/api/notion/ai-notes/transcribe", lambda route: pending.append(route))
    card = _insert(page)
    card.get_by_label("회의록 원문", exact=True).fill(SOURCE)
    show_audio_settings(card)
    card.get_by_label("소리 입력", exact=True).select_option("microphone")
    card.locator("[data-ai-auto-summary]").uncheck()
    with page.expect_request("**/api/notion/ai-notes/transcribe"):
        card.locator("[data-ai-record]").click()
    expect(card.locator('[data-ai-clear="all"]')).to_be_disabled()
    card.locator('[data-ai-clear="all"]').dispatch_event("click")
    assert page.evaluate("() => State.pageData.blocks[0].props.transcript") == SOURCE
    card.locator("[data-ai-pause]").click()
    expect(card.locator('[data-ai-clear="transcript"]')).to_be_disabled()
    card.locator("[data-ai-stop]").click()
    expect(card.locator('[data-ai-clear="all"]')).to_be_disabled()
    page.unroute("**/api/notion/ai-notes/transcribe")
    page.route("**/api/notion/ai-notes/transcribe", lambda route: transcribe_reply(route, ""))
    for route in pending:
        transcribe_reply(route, "")
    expect(card.locator("[data-ai-audio-status]")).to_contain_text("전사 완료", timeout=15000)
    expect(card.locator('[data-ai-clear="all"]')).to_be_enabled()
    summaries = []
    page.route("**/api/notion/ai-notes/summarize", lambda route: summaries.append(route))
    with page.expect_request("**/api/notion/ai-notes/summarize"):
        card.locator("[data-ai-generate]").click()
    expect(card.locator('[data-ai-clear="all"]')).to_be_disabled()
    card.locator('[data-ai-clear="all"]').dispatch_event("click")
    assert page.evaluate("() => State.pageData.blocks[0].props.transcript") == SOURCE
    summaries[0].fulfill(json={"result": RESULT})
    expect(card.locator("[data-ai-status]")).to_have_text("요약 저장됨", timeout=15000)
    clear(page, card, "all")


def test_clear_save_failure_disclosed_and_controls_recover(document_page, tmp_path):
    page = document_page
    _models(page)
    card = seed(page)
    # Use the existing adapter's save boundary, with only the synthetic fixture failing.
    page.evaluate("() => { window.flushPageDraft = async () => false; window.confirm = () => true; }")
    card.locator('[data-ai-clear="all"]').click()
    expect(card.locator("[data-ai-status]")).to_contain_text("저장하지 못했습니다", timeout=15000)
    expect(card.locator("[data-ai-generate]")).to_be_enabled()
    expect(card.locator('[data-ai-clear="all"]')).to_be_disabled()
    assert page.evaluate("() => State.pageData.blocks[0].props.transcript") == ""
    page.set_viewport_size({"width": 600, "height": 900})
    card.locator("footer").scroll_into_view_if_needed()
    assert card.locator("footer").evaluate("e => e.scrollWidth <= e.clientWidth + 1")
    _screenshot(page, tmp_path, "ai-notes-clear-save-warning.png")
