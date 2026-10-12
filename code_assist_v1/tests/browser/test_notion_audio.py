"""Real browser/audio-worklet, synthetic oscillator only; no device microphone."""
import re

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _screenshot
from code_assist_v1.tests.browser.test_notion_ai_notes import _insert as _insert_note
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready
from code_assist_v1.tests.test_notion_ai_notes import MODELS, RESULT, SOURCE
from code_assist_v1.tests.test_notion_audio import wav_bytes


def show_audio_settings(card):
    settings = card.locator('details.ai-notes-audio-settings')
    if settings.get_attribute('open') is None:
        settings.locator('summary').click()


def _insert(page):
    card = _insert_note(page)
    show_audio_settings(card)
    card.get_by_label("소리 입력", exact=True).select_option("microphone")
    return card


def audio_models(page, enabled=True):
    page.route("**/api/notion/ai-notes/capabilities", lambda route: route.fulfill(json={
        "models": MODELS, "audio": {"enabled": enabled, "chunk_seconds": 1, "max_audio_bytes": 20 * 1024 * 1024, "reason": "테스트: STT 미설정"},
    }))


def transcribe_reply(route, text):
    body = route.request.post_data_buffer
    segment = re.search(rb'name="segment_id"\r\n\r\n([^\r]+)', body).group(1).decode()
    assert b'name="consent"\r\n\r\ntrue' in body
    assert b"RIFF" in body and b"WAVE" in body
    route.fulfill(json={"segment_id": segment, "text": text, "model": "synthetic-stt"})
    return segment


def fake_microphone(page):
    page.evaluate("""() => {
      window.syntheticMicCalls = 0;
      Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { configurable: true, value: async () => {
        window.syntheticMicCalls++;
        const context = new AudioContext({ sampleRate: 16000 });
        const source = context.createOscillator();
        const output = context.createMediaStreamDestination();
        source.connect(output); source.start(); await context.resume();
        window.syntheticMic = { context, source, stream: output.stream };
        return output.stream;
      }});
    }""")


@pytest.mark.integration
@pytest.mark.regression
def test_audio_file_explicit_selection_final_summary_and_reload(document_page, tmp_path):
    from playwright.sync_api import expect
    page = document_page
    errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
    audio_models(page)
    transcriptions = []; summaries = []
    page.route("**/api/notion/ai-notes/transcribe", lambda route: transcriptions.append(transcribe_reply(route, SOURCE)))

    def summarize(route):
        summaries.append(route.request.post_data_json)
        route.fulfill(json={"result": RESULT, "model_name": "Gemma4 (synthetic)"})

    page.route("**/api/notion/ai-notes/summarize", summarize)
    card = _insert(page)
    expect(card.locator("[data-ai-record]")).to_be_enabled()
    expect(card.locator("[data-ai-audio-import]")).to_be_enabled()
    expect(card.locator("[data-ai-audio-consent]")).to_have_count(0)
    assert transcriptions == [] and summaries == []
    card.locator("[data-ai-audio-import]").set_input_files({"name": "synthetic.wav", "mimeType": "audio/wav", "buffer": wav_bytes()})
    expect(card.locator(".ai-notes-summary")).to_have_text(RESULT["summary"], timeout=15000)
    expect(card.locator("[data-ai-status]")).to_have_text("요약 저장됨", timeout=15000)
    assert len(transcriptions) == 1
    assert len(summaries) == 1 and summaries[0]["summary_phase"] == "final"
    assert summaries[0]["transcript"] == "[음성 파일] " + SOURCE
    page.reload(); _wait_for_notion_ready(page)
    assert page.evaluate("() => State.pageData.blocks[0].props.transcript") == "[음성 파일] " + SOURCE
    expect(card.locator(".ai-notes-summary")).to_have_text(RESULT["summary"])
    expect(card.locator("[data-ai-audio-consent]")).to_have_count(0)
    _screenshot(page, tmp_path, "ai-notes-audio-final.png")
    assert not errors


@pytest.mark.integration
@pytest.mark.regression
def test_live_worklet_provisional_pause_resume_final_and_mixed_notes(document_page, tmp_path):
    from playwright.sync_api import expect
    page = document_page
    errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
    audio_models(page); fake_microphone(page)
    transcriptions = []; summaries = []
    page.route("**/api/notion/ai-notes/transcribe", lambda route: transcriptions.append(transcribe_reply(route, SOURCE)))

    def summarize(route):
        summaries.append(route.request.post_data_json)
        route.fulfill(json={"result": RESULT, "model_name": "Qwen3.6 (synthetic)"})

    page.route("**/api/notion/ai-notes/summarize", summarize)
    card = _insert(page)
    assert page.evaluate("() => syntheticMicCalls") == 0
    card.locator("[data-ai-record]").click()
    expect(card.locator(".ai-notes-badge")).to_have_text("잠정 요약", timeout=15000)
    expect(card.locator('[data-ai-field="transcript"]')).to_have_attribute("readonly", "")
    card.get_by_role("tab", name="메모", exact=True).click()
    card.get_by_label("회의 메모", exact=True).fill("음성 전사 중 직접 작성한 메모")
    card.locator("[data-ai-pause]").click()
    expect(card.locator("[data-ai-pause]")).to_have_text("녹음 재개")
    assert page.evaluate("() => !syntheticMic.stream.getAudioTracks()[0].enabled")
    card.locator("[data-ai-pause]").click()
    assert page.evaluate("() => syntheticMic.stream.getAudioTracks()[0].enabled")
    card.locator("[data-ai-stop]").click()
    expect(card.locator("[data-ai-status]")).to_have_text("요약 저장됨", timeout=15000)
    expect(card.locator(".ai-notes-badge")).to_have_text("회의 기록", timeout=15000)
    assert summaries[0]["summary_phase"] == "provisional"
    assert summaries[-1]["summary_phase"] == "final"
    assert summaries[-1]["notes"] == "음성 전사 중 직접 작성한 메모"
    assert len(transcriptions) >= 2  # Full segment plus stop/pause tail.
    assert len(transcriptions) == len(set(transcriptions))
    assert page.evaluate("() => syntheticMic.stream.getAudioTracks()[0].readyState") == "ended"
    card.get_by_role("tab", name="원문", exact=True).click()
    expect(card.locator('[data-ai-field="transcript"]')).to_be_editable()
    _screenshot(page, tmp_path, "ai-notes-live-transcript.png")
    assert not errors


@pytest.mark.integration
@pytest.mark.regression
def test_audio_error_retry_keeps_segment_and_never_duplicates(document_page):
    from playwright.sync_api import expect
    page = document_page
    audio_models(page)
    attempts = []

    def transcribe(route):
        body = route.request.post_data_buffer
        attempts.append(re.search(rb'name="segment_id"\r\n\r\n([^\r]+)', body).group(1))
        if len(attempts) == 1:
            route.fulfill(status=503, json={"message": "합성 STT 연결 실패"})
        else:
            transcribe_reply(route, SOURCE)

    page.route("**/api/notion/ai-notes/transcribe", transcribe)
    page.route("**/api/notion/ai-notes/summarize", lambda route: route.fulfill(json={"result": RESULT}))
    card = _insert(page)
    card.locator("[data-ai-audio-import]").set_input_files({"name": "synthetic.wav", "mimeType": "audio/wav", "buffer": wav_bytes()})
    expect(card.locator("[data-ai-audio-status]")).to_contain_text("합성 STT 연결 실패")
    expect(card.locator("[data-ai-download-audio]")).to_be_visible()
    expect(card.locator("[data-ai-record]")).to_be_disabled()
    card.locator("[data-ai-retry-audio]").click()
    expect(card.locator(".ai-notes-summary")).to_have_text(RESULT["summary"], timeout=15000)
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    assert page.evaluate("() => State.pageData.blocks[0].props.transcript") == "[음성 파일] " + SOURCE
    expect(card.locator("[data-ai-retry-audio]")).to_be_hidden()


@pytest.mark.integration
@pytest.mark.regression
def test_mic_permission_denial_and_page_revocation_stop_capture(document_page):
    from playwright.sync_api import expect
    page = document_page
    audio_models(page)
    card = _insert(page)
    page.evaluate("""() => Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { configurable: true,
      value: async () => { throw new DOMException('synthetic denial', 'NotAllowedError'); } })""")
    card.locator("[data-ai-record]").click()
    expect(card.locator("[data-ai-audio-status]")).to_contain_text("권한이 거부")
    expect(card.locator("[data-ai-record]")).to_be_enabled()
    fake_microphone(page)
    pending = []
    page.route("**/api/notion/ai-notes/transcribe", lambda route: pending.append(route))
    with page.expect_request("**/api/notion/ai-notes/transcribe"):
        card.locator("[data-ai-record]").click()
    page.evaluate("() => { State.pageData.page.can_edit = false; renderBlocks(); }")
    page.wait_for_function("() => syntheticMic.stream.getAudioTracks()[0].readyState === 'ended'")
    assert page.evaluate("() => State.pageData.blocks[0].props.transcript") == ""
    expect(card.locator("[data-ai-record]")).to_have_count(0)
    assert page.evaluate("() => NotionAiNotes.beforeNavigate()") is True


@pytest.mark.integration
@pytest.mark.regression
def test_navigation_cancel_then_stop_save_and_home(document_page):
    from playwright.sync_api import expect
    page = document_page
    audio_models(page); fake_microphone(page)
    page.route("**/api/notion/ai-notes/transcribe", lambda route: transcribe_reply(route, SOURCE))
    page.route("**/api/notion/ai-notes/summarize", lambda route: route.fulfill(json={"result": RESULT}))
    card = _insert(page)
    page_id = page.evaluate("() => State.activePageId")
    card.locator("[data-ai-record]").click()
    expect(card.locator("[data-ai-pause]")).to_be_visible()
    page.wait_for_function("() => Boolean(State.pageData.blocks[0].props.transcript)")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.evaluate("() => goHome()")
    assert page.evaluate("() => State.activePageId") == page_id
    page.once("dialog", lambda dialog: dialog.accept())
    page.evaluate("() => goHome()")
    assert page.evaluate("() => State.activePageId") == ""
    assert page.evaluate("() => syntheticMic.stream.getAudioTracks()[0].readyState") == "ended"
    page.evaluate("id => openPage(id)", page_id)
    expect(card.locator(".ai-notes-summary")).to_have_text(RESULT["summary"], timeout=15000)


@pytest.mark.integration
@pytest.mark.regression
def test_slow_provisional_coalesces_final_after_last_transcript(document_page):
    from playwright.sync_api import expect
    page = document_page
    audio_models(page); fake_microphone(page)
    summaries = []; pending = []
    page.route("**/api/notion/ai-notes/transcribe", lambda route: transcribe_reply(route, SOURCE))

    def summarize(route):
        summaries.append(route.request.post_data_json)
        if len(summaries) == 1:
            pending.append(route)
        else:
            route.fulfill(json={"result": RESULT})

    page.route("**/api/notion/ai-notes/summarize", summarize)
    card = _insert(page)
    with page.expect_request("**/api/notion/ai-notes/summarize"):
        card.locator("[data-ai-record]").click()
    page.wait_for_function("() => State.pageData.blocks[0].props.transcript.includes('[00:01]')")
    card.locator("[data-ai-stop]").click()
    page.wait_for_function("() => syntheticMic.stream.getAudioTracks()[0].readyState === 'ended'")
    assert len(summaries) == 1  # No concurrent request while the provisional result is delayed.
    pending[0].fulfill(json={"result": RESULT})
    expect(card.locator("[data-ai-status]")).to_have_text("요약 저장됨", timeout=15000)
    try:
        expect(card.locator(".ai-notes-badge")).to_have_text("회의 기록", timeout=15000)
    except AssertionError:
        pytest.fail(str({"phases": [item["summary_phase"] for item in summaries],
            "audio_status": card.locator("[data-ai-audio-status]").inner_text(),
            "save": page.evaluate("() => ({dirty:State.dirty,queued:State.saveQueued,inflight:Boolean(State.saveInFlight),revision:State.editRevision,conflict:State.saveConflict})")}))
    assert [item["summary_phase"] for item in summaries] == ["provisional", "final"]
    assert summaries[1]["transcript"].startswith(summaries[0]["transcript"])
    assert len(summaries[1]["transcript"]) > len(summaries[0]["transcript"])
    assert summaries[1]["transcript"] == page.evaluate("() => State.pageData.blocks[0].props.transcript")
