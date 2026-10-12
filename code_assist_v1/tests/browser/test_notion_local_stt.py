"""Local-test UI policy on isolated pages and synthetic audio only."""
from playwright.sync_api import expect

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _screenshot
from code_assist_v1.tests.browser.test_notion_ai_notes import _insert
from code_assist_v1.tests.browser.test_notion_audio import transcribe_reply, show_audio_settings
from code_assist_v1.tests.test_notion_audio import wav_bytes
from code_assist_v1.tests.test_notion_ai_notes import MODELS, RESULT, SOURCE


def test_local_transcription_does_not_auto_send_text_to_summary(document_page, tmp_path):
    page = document_page
    page.route("**/api/notion/ai-notes/capabilities", lambda route: route.fulfill(json={
        "models": MODELS, "audio": {"enabled": True, "local_only": True, "auto_summary_default": False,
                                     "label": "로컬 Whisper base · CPU · 테스트", "max_audio_seconds": 120},
    }))
    transcriptions, summaries = [], []
    page.route("**/api/notion/ai-notes/transcribe", lambda route: transcriptions.append(transcribe_reply(route, SOURCE)))
    def summarize(route):
        summaries.append(route.request.post_data_json)
        route.fulfill(json={"result": RESULT, "model_name": "Synthetic test model"})
    page.route("**/api/notion/ai-notes/summarize", summarize)
    card = _insert(page)
    expect(card.locator("[data-ai-audio-status]")).to_contain_text("로컬 Whisper")
    expect(card.locator("[data-ai-audio-status]")).to_contain_text("120초 이하")
    expect(card.locator("[data-ai-auto-summary]")).not_to_be_checked()
    expect(card.locator("[data-ai-audio-provider]")).to_contain_text("음성은 이 PC에서 전사")
    expect(card.locator("[data-ai-audio-consent]")).to_have_count(0)
    expect(card.locator("[data-ai-record]")).to_be_enabled()
    assert "첫 소리 테스트" not in card.inner_text()
    show_audio_settings(card)
    card.locator("[data-ai-audio-import]").set_input_files({"name": "synthetic.wav", "mimeType": "audio/wav", "buffer": wav_bytes()})
    expect(card.locator("[data-ai-audio-status]")).to_contain_text("전사 완료", timeout=15000)
    assert len(transcriptions) == 1 and summaries == []
    expect(card.locator('[data-ai-field="transcript"]')).to_have_value("[음성 파일] " + SOURCE)
    _screenshot(page, tmp_path, "local-whisper-transcript-only.png")
    card.locator("[data-ai-generate]").click()
    expect(card.locator(".ai-notes-summary")).to_have_text(RESULT["summary"], timeout=15000)
    assert len(summaries) == 1
