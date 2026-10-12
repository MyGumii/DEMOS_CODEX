"""Speech/fixed-twelve selection and legacy settings with synthetic audio only."""
import struct
import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _screenshot, _seed_blocks
from code_assist_v1.tests.browser.test_notion_notes_translation import capabilities, translated
from code_assist_v1.tests.browser.test_notion_ai_notes import _insert
from code_assist_v1.tests.browser.test_notion_audio import transcribe_reply, fake_microphone, show_audio_settings
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready


def _requests(page):
    durations, calls = [], []
    def transcribe(route):
        body = route.request.post_data_buffer
        wav = body[body.index(b"RIFF"):]
        durations.append(struct.unpack_from("<I", wav, 40)[0] / 2 / struct.unpack_from("<I", wav, 24)[0])
        transcribe_reply(route, "말끝까지 모아서 처리한 합성 문장입니다.")
    page.route("**/api/notion/ai-notes/transcribe", transcribe)
    page.route("**/api/notion/ai-notes/translate", lambda route: calls.append(translated(route)))
    return durations, calls


@pytest.mark.integration
@pytest.mark.regression
def test_speech_pause_then_fixed_twelve_and_reload_without_translation_autostart(document_page, tmp_path):
    from playwright.sync_api import expect
    page = document_page
    capabilities(page, local=True)
    # No microphone permission or real device: emit a tone then silence in the
    # same WebAudio clock, which gives a reproducible acoustic boundary.
    page.evaluate("""() => Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
      configurable: true, value: async () => {
        const ctx = new AudioContext({sampleRate:16000});
        const tone = ctx.createOscillator(), gain = ctx.createGain(), out = ctx.createMediaStreamDestination();
        tone.connect(gain); gain.connect(out); gain.gain.value = .2;
        await ctx.resume();
        const now = ctx.currentTime;
        gain.gain.setValueAtTime(0, now + 12);
        gain.gain.setValueAtTime(.2, now + 13.2);
        tone.start();
        window.endpointAudio = {ctx, tone};
        return out.stream;
      }
    })""")
    durations, calls = _requests(page)
    card = _insert(page)
    picker = card.get_by_label("음성 처리 간격", exact=True)
    expect(picker).to_have_value("speech")
    expect(picker.locator("option")).to_have_text(["말끝 우선 · 10~20초 (기본)", "고정 · 12초"])
    show_audio_settings(card)
    card.get_by_label("소리 입력", exact=True).select_option("microphone")
    card.locator("[data-ai-record]").click()
    card.get_by_role("tab", name="번역", exact=True).click()
    card.locator("[data-ai-translate-auto]").check()
    page.wait_for_timeout(3000)
    assert not durations and not calls
    expect(card.locator("[data-ai-translation-text]")).to_contain_text("말끝까지", timeout=20000)
    assert len(durations) == 1 and 12.65 <= durations[0] <= 12.9
    assert len(calls) == 1
    expect(picker).to_be_disabled()
    expect(card.locator("[data-ai-audio-latency]")).to_contain_text("말끝 우선")
    card.locator("[data-ai-stop]").click()
    expect(card.locator("[data-ai-audio-status]")).to_have_text("전사 완료", timeout=15000)
    page.wait_for_function("State.pageData.blocks[0].props.translation_source === State.pageData.blocks[0].props.transcript")
    card.locator("[data-ai-translate-auto]").uncheck()
    show_audio_settings(card)
    picker.select_option("12")
    page.evaluate("() => savePage()")
    page.reload(); _wait_for_notion_ready(page)
    expect(picker).to_have_value("12")
    fake_microphone(page)
    durations.clear(); calls.clear()
    show_audio_settings(card)
    card.get_by_label("소리 입력", exact=True).select_option("microphone")
    card.locator("[data-ai-record]").click()
    expect(card.locator("[data-ai-audio-latency]")).to_contain_text("최근 구간 12.0초", timeout=18000)
    assert durations == [12]
    card.locator("[data-ai-stop]").click()
    expect(card.locator("[data-ai-audio-status]")).to_have_text("전사 완료", timeout=15000)
    assert not calls  # Reload does not enable auto translation.
    show_audio_settings(card)
    picker.select_option("speech")
    page.evaluate("() => savePage()")
    page.reload(); _wait_for_notion_ready(page)
    expect(picker).to_have_value("speech")
    _screenshot(page, tmp_path, "speech-and-fixed12-picker.png")


@pytest.mark.parametrize("legacy, mode, seconds", [
    ({"audio_chunk_mode": "fixed", "audio_chunk_seconds": 10}, "12", 12),
    ({"audio_chunk_mode": "speech", "audio_chunk_seconds": 12}, "speech", 20),
])
def test_legacy_fixed_ten_normalizes_and_speech_remains_without_rewriting_history(document_page, legacy, mode, seconds):
    from playwright.sync_api import expect
    page = document_page; capabilities(page, local=True); fake_microphone(page)
    durations, calls = _requests(page)
    history = {"transcript": "[00:00] Previous recording", "translation_text": "Previous translation"}
    _seed_blocks(page, [{"type": "ai_notes", "props": {**history, **legacy}}])
    page.reload(); _wait_for_notion_ready(page)
    card = page.locator("#blocks .ai-notes")
    expect(card.get_by_label("음성 처리 간격", exact=True)).to_have_value(mode)
    stored = page.evaluate("() => State.pageData.blocks[0].props")
    assert all(stored[key] == value for key, value in {**history, **legacy}.items())
    assert not durations and not calls
    fake_microphone(page)
    show_audio_settings(card)
    card.get_by_label("소리 입력", exact=True).select_option("microphone")
    card.locator("[data-ai-record]").click()
    expect(card.locator("[data-ai-audio-latency]")).to_contain_text(f"최근 구간 {seconds}.0초", timeout=28000)
    assert durations == [seconds] and not calls
    card.locator("[data-ai-stop]").click()
    expect(card.locator("[data-ai-audio-status]")).to_have_text("전사 완료", timeout=15000)
    stored = page.evaluate("() => State.pageData.blocks[0].props")
    assert stored["transcript"].startswith(history["transcript"])
    assert stored["translation_text"] == history["translation_text"]
