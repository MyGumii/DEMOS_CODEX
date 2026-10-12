"""Isolated browser pages and synthetic audio; never captures a user device."""
import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _screenshot
from code_assist_v1.tests.browser.test_notion_ai_notes import _insert
from code_assist_v1.tests.browser.test_notion_audio import fake_microphone, transcribe_reply, show_audio_settings
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready


def capabilities(page, local=False):
    page.route("**/api/notion/ai-notes/capabilities", lambda route: route.fulfill(json={
        "models": [{"id": "codex-local", "provider": "codex", "name": "Codex Local Agent"}],
        "codex_bridge": {"enabled": True, "connected": True},
        "translation": {"enabled": True, "default_model_id": ""},
        "audio": {"enabled": True, "local_only": local, "chunk_seconds": 1, "auto_summary_default": False},
    }))


def translated(route):
    data = route.request.post_data_json
    route.fulfill(json={"text": data["text"], "target_language": data["target_language"],
                        "translation": "번역: " + data["text"], "model_name": "Codex (synthetic)"})
    return data


@pytest.mark.parametrize("save_succeeds", [True, False])
def test_auto_translation_paints_while_result_save_is_pending(document_page, save_succeeds):
    from playwright.sync_api import expect
    page = document_page
    capabilities(page)
    calls = []
    page.route("**/api/notion/ai-notes/translate", lambda route: calls.append(translated(route)))
    card = _insert(page)
    card.get_by_label("회의록 원문", exact=True).fill("Synthetic pending save.")
    page.evaluate("""async () => {
      await savePage();
      const original = window.flushPageDraft;
      window.restoreTranslationSave = () => { window.flushPageDraft = original; };
      window.flushPageDraft = async (...args) => {
        if (State.pageData.blocks[0].props.translation_text) {
          const accepted = await new Promise(resolve => { window.releaseTranslationSave = resolve; });
          if (!accepted) return false;
        }
        return original(...args);
      };
    }""")
    card.get_by_role("tab", name="번역", exact=True).click()
    card.locator("[data-ai-translate-auto]").check()
    page.wait_for_function("() => typeof releaseTranslationSave === 'function'")
    expect(card.locator("[data-ai-translation-text]")).to_contain_text("번역: Synthetic pending save.")
    expect(card.locator("[data-ai-translate-auto]")).to_be_checked()
    expect(card.locator("[data-ai-translation-status]")).to_contain_text("저장 중")
    assert len(calls) == 1
    page.evaluate("success => { restoreTranslationSave(); releaseTranslationSave(success); }", save_succeeds)
    if save_succeeds:
        expect(card.locator("[data-ai-translation-status]")).to_contain_text("새 전사를 기다리는 중")
        card.locator("[data-ai-translate-auto]").uncheck()
    else:
        expect(card.locator("[data-ai-translation-status]")).to_contain_text("저장하지 못했습니다")
        expect(card.locator("[data-ai-translate-auto]")).not_to_be_checked()
        expect(card.locator("[data-ai-translation-text]")).to_contain_text("번역: Synthetic pending save.")
        card.locator("[data-ai-translate-start]").click()
        expect(card.locator("[data-ai-translation-status]")).to_have_text("현재 원문 번역 완료 · 저장됨")
    assert len(calls) == 1
    page.reload(); _wait_for_notion_ready(page)
    card.get_by_role("tab", name="번역", exact=True).click()
    expect(card.locator("[data-ai-translation-text]")).to_contain_text("번역: Synthetic pending save.")


@pytest.mark.integration
@pytest.mark.regression
def test_translation_chunks_reload_download_clear_and_no_autostart(document_page, tmp_path):
    from playwright.sync_api import expect
    page = document_page
    errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
    capabilities(page)
    calls = []
    page.route("**/api/notion/ai-notes/translate", lambda route: calls.append(translated(route)))
    card = _insert(page)
    source = "[00:00] Synthetic sentence.\n" * 90
    card.get_by_label("회의록 원문", exact=True).fill(source)
    card.get_by_role("tab", name="번역", exact=True).click()
    expect(card.locator("[data-ai-translate-model]")).to_have_value("")
    assert card.locator('[data-ai-translate-model] option[value="codex-local"]').get_attribute("disabled") is None
    expect(card.locator("[data-ai-translate-auto]")).not_to_be_checked()
    assert calls == []
    card.locator("[data-ai-translate-start]").click()
    expect(card.locator("[data-ai-translation-status]")).to_have_text("현재 원문 번역 완료 · 저장됨", timeout=20000)
    assert "".join(call["text"] for call in calls) == source
    assert len(calls) == 2 and all(len(call["text"]) <= 2000 for call in calls)
    assert all(call["model_id"] == "" for call in calls)
    assert calls[1]["context"] == calls[0]["text"][-1200:]
    card.locator("[data-ai-translate-start]").click()
    expect(card.locator("[data-ai-translation-status]")).to_have_text("현재 원문 번역 완료 · 저장됨")
    assert len(calls) == 2
    page.reload(); _wait_for_notion_ready(page)
    card.get_by_role("tab", name="번역", exact=True).click()
    expect(card.locator("[data-ai-translation-text]")).to_contain_text("번역: [00:00]")
    expect(card.locator("[data-ai-translate-auto]")).not_to_be_checked()
    assert len(calls) == 2
    assert page.evaluate("() => State.pageData.blocks[0].props.transcript") == source
    page.evaluate("() => openSidePeek(State.activePageId)")
    side = page.locator("#sidePeekBlocks .ai-notes")
    side.get_by_role("tab", name="번역", exact=True).click()
    expect(side.locator("[data-ai-translation-text]")).to_contain_text("번역: [00:00]")
    page.locator("#sidePeekCloseBtn").click()
    card.locator("[data-ai-export-kind]").select_option("translation")
    with page.expect_download() as download:
        card.locator("[data-ai-export]").click()
    assert "번역" in download.value.suggested_filename
    assert download.value.path().read_text(encoding="utf-8-sig").startswith("번역: ")
    card.locator('[data-ai-panel="translation"]').evaluate("node => node.scrollIntoView({block:'start'})")
    _screenshot(page, tmp_path, "ai-notes-translation.png")
    page.once("dialog", lambda dialog: dialog.accept())
    card.locator("[data-ai-translate-clear]").click()
    expect(card.locator("[data-ai-translation-text]")).to_have_text("")
    assert page.evaluate("() => State.pageData.blocks[0].props.transcript") == source
    assert not errors


@pytest.mark.integration
@pytest.mark.regression
def test_translation_stale_input_and_cancel_discard_late_result(document_page):
    from playwright.sync_api import expect
    page = document_page
    capabilities(page)
    pending = []
    page.route("**/api/notion/ai-notes/translate", lambda route: pending.append(route))
    card = _insert(page)
    card.get_by_label("회의록 원문", exact=True).fill("Original sentence.")
    card.get_by_role("tab", name="번역", exact=True).click()
    with page.expect_request("**/api/notion/ai-notes/translate"):
        card.locator("[data-ai-translate-start]").click()
    page.wait_for_function("() => document.querySelector('[data-ai-translate-start]').disabled")
    card.get_by_role("tab", name="원문", exact=True).click()
    card.get_by_label("회의록 원문", exact=True).fill("Changed original.")
    assert pending
    translated(pending.pop())
    card.get_by_role("tab", name="번역", exact=True).click()
    expect(card.locator("[data-ai-translation-status]")).to_contain_text("결과를 반영하지 않았습니다", timeout=10000)
    expect(card.locator("[data-ai-translation-text]")).to_have_text("")
    card.locator("[data-ai-translate-start]").click()
    expect(card.locator("[data-ai-translate-stop]")).to_be_visible()
    card.locator("[data-ai-translate-stop]").click()
    expect(card.locator("[data-ai-translation-status]")).to_contain_text("번역을 중지했습니다")
    expect(card.locator("[data-ai-translation-text]")).to_have_text("")
    expect(card.locator("[data-ai-translate-start]")).to_be_enabled()


@pytest.mark.integration
@pytest.mark.regression
def test_live_translation_does_not_block_transcription_and_retries_from_saved_prefix(document_page):
    from playwright.sync_api import expect
    page = document_page
    capabilities(page, local=True); fake_microphone(page)
    pending = []; transcriptions = []
    page.route("**/api/notion/ai-notes/translate", lambda route: pending.append(route))
    page.route("**/api/notion/ai-notes/transcribe", lambda route: transcriptions.append(transcribe_reply(route, "Synthetic speech.")))
    card = _insert(page)
    show_audio_settings(card)
    card.get_by_label("소리 입력", exact=True).select_option("microphone")
    card.get_by_label("음성 처리 간격", exact=True).select_option("12")
    card.get_by_role("tab", name="번역", exact=True).click()
    card.locator("[data-ai-translate-auto]").check()
    assert page.evaluate("() => syntheticMicCalls") == 0
    card.locator("[data-ai-record]").click()
    # Three full 12-second batches must keep transcribing while the model waits.
    page.wait_for_function("() => (State.pageData.blocks[0].props.transcript || '').split('Synthetic speech.').length >= 4", timeout=45000)
    card.locator("[data-ai-stop]").click()
    expect(card.locator("[data-ai-audio-status]")).to_contain_text("전사 완료", timeout=15000)
    assert len(transcriptions) >= 3 and len(pending) == 1
    pending.pop().fulfill(status=503, json={"message": "합성 번역 실패"})
    card.get_by_role("tab", name="번역", exact=True).click()
    expect(card.locator("[data-ai-translation-status]")).to_contain_text("합성 번역 실패")
    expect(card.locator("[data-ai-translate-auto]")).not_to_be_checked()
    calls = []
    page.unroute("**/api/notion/ai-notes/translate")
    page.route("**/api/notion/ai-notes/translate", lambda route: calls.append(translated(route)))
    card.locator("[data-ai-translate-start]").click()
    expect(card.locator("[data-ai-translation-status]")).to_have_text("현재 원문 번역 완료 · 저장됨", timeout=15000)
    assert calls[0]["text"] == page.evaluate("() => State.pageData.blocks[0].props.transcript")


@pytest.mark.parametrize("bridge_enabled,company_available", [(False, True), (False, False), (True, True), (True, False)])
def test_argument_gate_hides_codex_or_allows_direct_selection(document_page, bridge_enabled, company_available):
    from playwright.sync_api import expect
    page = document_page
    models = ([{"id": "gemma4", "name": "Gemma4", "provider": "hynix_api"},
               {"id": "qwen3.6", "name": "Qwen3.6", "provider": "hynix_api"}] if company_available else [])
    # Include a stale Codex entry to ensure the explicit launch gate wins in UI.
    models += [{"id": "codex-local", "name": "Codex Local Agent", "provider": "codex"}]
    page.route("**/api/notion/ai-notes/capabilities", lambda route: route.fulfill(json={
        "models": models, "codex_bridge": {"enabled": bridge_enabled, "connected": True},
        "translation": {"enabled": True, "default_model_id": "codex-local"}, "audio": {"enabled": False},
    }))
    calls = []
    page.route("**/api/notion/ai-notes/translate", lambda route: calls.append(translated(route)))
    summaries = []
    def summarized(route):
        summaries.append(route.request.post_data_json)
        route.fulfill(json={"result": {"summary": "Synthetic summary.", "decisions": [], "action_items": []}, "model_name": "Synthetic model"})
    page.route("**/api/notion/ai-notes/summarize", summarized)
    card = _insert(page)
    page.evaluate("""async () => {
      Object.assign(State.pageData.blocks[0].props, {transcript:'Synthetic source.', translation_model_id:'codex-local', model_id:'codex-local'});
      renderBlocks(); markDirty(); await savePage();
    }""")
    page.reload(); _wait_for_notion_ready(page)
    card.get_by_role("tab", name="번역", exact=True).click()
    picker = card.locator('[data-ai-translate-model]')
    expect(picker).to_have_value("codex-local" if bridge_enabled else "")
    codex_options = card.locator('option[value="codex-local"]')
    if bridge_enabled:
        expect(codex_options).to_have_count(2)
        assert codex_options.first.get_attribute("disabled") is None
        assert codex_options.last.get_attribute("disabled") is None
        # Both controls must accept a real user selection, not just display an ID.
        picker.select_option("")
        picker.select_option("codex-local")
        card.get_by_text("요약 설정", exact=True).click()
        summary_picker = card.locator("[data-ai-model]")
        summary_picker.select_option("")
        summary_picker.select_option("codex-local")
        page.evaluate("() => savePage()")
        page.reload(); _wait_for_notion_ready(page)
        card.get_by_role("tab", name="번역", exact=True).click()
        expect(picker).to_have_value("codex-local")
        expect(card.locator("[data-ai-model]")).to_have_value("codex-local")
    else:
        expect(codex_options).to_have_count(0)
        expect(card.locator('[data-ai-translate-codex-notice]')).to_be_hidden()
        assert "Codex" not in picker.inner_text()
    if company_available or bridge_enabled:
        expect(card.locator('[data-ai-translate-start]')).to_be_enabled()
        card.locator('[data-ai-translate-start]').click()
        expect(card.locator('[data-ai-translation-status]')).to_have_text('현재 원문 번역 완료 · 저장됨')
        assert calls[0]['model_id'] == ('codex-local' if bridge_enabled else '')
        card.locator('[data-ai-generate]').click()
        expect(card.locator('[data-ai-status]')).to_have_text('요약 저장됨', timeout=15000)
        assert summaries[0]['model_id'] == ('codex-local' if bridge_enabled else '')
    else:
        expect(card.locator('[data-ai-translate-start]')).to_be_disabled()
        expect(card.locator('[data-ai-translate-auto]')).to_be_disabled()
        assert calls == []


def interpreted(route):
    from code_assist_v1.notion_notes_translation import PAIR_LANGUAGES, pair_output
    data = route.request.post_data_json
    pair = data["language_pair"]
    assert data["translation_mode"] == "pair"
    phrases = {"ko": "안녕하세요.", "en": "Hello.", "ja": "こんにちは。"}
    segments = []
    for span in data["text"].splitlines(keepends=True):
        origin = next((code for code in pair if phrases[code] in span), "und")
        segments.append({"text": span, "source_language": origin,
                         "target_language": pair[1 - pair.index(origin)] if origin != "und" else "",
                         "translation": "합성 통역" if origin != "und" else ""})
    route.fulfill(json={"text": data["text"], "target_language": " ↔ ".join(PAIR_LANGUAGES[code] for code in pair),
                        "translation_mode": "pair", "language_pair": pair,
                        "translation": pair_output({"segments": segments}, data["text"], pair), "model_name": "Synthetic interpreter"})
    return data


def test_existing_two_second_backlog_is_not_translated_as_current_recording(document_page, tmp_path):
    from playwright.sync_api import expect
    from code_assist_v1.tests.browser.test_notion_document_extras import _seed_blocks
    page = document_page; capabilities(page, local=True)
    old = "[00:00] OLD TWO SECOND\n[00:02] OLD FRAGMENT\n[00:04] OLD LAST\n"
    current = "[00:00] 안녕하세요. 오늘은 회의 내용을 길게 수집합니다.\n[00:12] 안녕하세요. 다음 묶음입니다."
    _seed_blocks(page, [{"type":"ai_notes", "props": {
        "transcript":old + current, "audio_started_at":"synthetic-new-recording",
        "translation_mode":"pair", "translation_pair":["ko","ja"],
        "translation_source":old[:20], "translation_language":"한국어 ↔ 일본어",
        "translation_text":"OLD 2-SECOND TRANSLATION"}}])
    calls = []
    page.route("**/api/notion/ai-notes/translate", lambda route: calls.append(interpreted(route)))
    page.reload(); _wait_for_notion_ready(page)
    card = page.locator("#blocks .ai-notes")
    card.get_by_role("tab", name="번역", exact=True).click()
    expect(card.get_by_label("번역 원문 범위", exact=True)).to_have_value("current")
    expect(card.locator("[data-ai-translation-text]")).to_have_text("")
    expect(card.locator("[data-ai-translation-history-text]")).not_to_be_visible()
    expect(card.locator("[data-ai-translation-history-text]")).to_have_text("OLD 2-SECOND TRANSLATION")
    card.locator("[data-ai-translate-start]").click()
    expect(card.locator("[data-ai-translation-status]")).to_have_text("현재 원문 번역 완료 · 저장됨", timeout=15000)
    assert len(calls) == 1 and calls[0]["text"] == current and calls[0]["context"] == ""
    expect(card.locator("[data-ai-translation-text]")).not_to_contain_text("OLD")
    assert page.evaluate("() => State.pageData.blocks[0].props.transcript") == old + current
    _screenshot(page, tmp_path, "current-recording-not-old-two-second-backlog.png")
    page.reload(); _wait_for_notion_ready(page)
    card.get_by_role("tab", name="번역", exact=True).click()
    expect(card.locator("[data-ai-translation-text]")).to_contain_text("한국어 → 일본어")
    expect(card.locator("[data-ai-translation-text]")).not_to_contain_text("OLD")
    card.locator("[data-ai-translate-start]").click()
    expect(card.locator("[data-ai-translation-status]")).to_have_text("현재 원문 번역 완료 · 저장됨")
    assert len(calls) == 1
    card.get_by_label("번역 원문 범위", exact=True).select_option("all")
    card.locator("[data-ai-translate-start]").click()
    expect(card.locator("[data-ai-translation-status]")).to_have_text("현재 원문 번역 완료 · 저장됨")
    assert calls[-1]["text"] == old + current


def test_new_recording_waits_for_real_twelve_second_wav_without_old_backlog(document_page):
    from playwright.sync_api import expect
    from code_assist_v1.tests.browser.test_notion_document_extras import _seed_blocks
    import struct
    page = document_page; capabilities(page, local=True); fake_microphone(page)
    old = "[00:00] OLD TWO SECOND\n[00:02] OLD LAST"
    _seed_blocks(page, [{"type":"ai_notes", "props": {"transcript":old,
        "audio_started_at":"old-recording", "translation_target":"日本語"}}])
    durations, calls = [], []
    def transcribed(route):
        body = route.request.post_data_buffer
        wav = body[body.index(b"RIFF"):]
        durations.append(struct.unpack_from("<I", wav, 40)[0] / 2 / struct.unpack_from("<I", wav, 24)[0])
        transcribe_reply(route, "이번 열두 초 동안 모은 온전한 문장을 한꺼번에 번역합니다.")
    page.route("**/api/notion/ai-notes/transcribe", transcribed)
    page.route("**/api/notion/ai-notes/translate", lambda route: calls.append(translated(route)))
    card = page.locator("#blocks .ai-notes")
    show_audio_settings(card)
    card.get_by_label("소리 입력", exact=True).select_option("microphone")
    card.get_by_label("음성 처리 간격", exact=True).select_option("12")
    card.locator("[data-ai-record]").click()
    card.get_by_role("tab", name="번역", exact=True).click()
    card.locator("[data-ai-translate-auto]").check()
    page.wait_for_timeout(3000)
    assert not durations and not calls
    expect(card.locator("[data-ai-translation-text]")).to_contain_text("온전한 문장", timeout=20000)
    assert durations == [12] and len(calls) == 1
    assert "OLD" not in calls[0]["text"] and calls[0]["context"] == ""
    assert page.evaluate("() => State.pageData.blocks[0].props.audio_transcript_start") == len(old)
    card.locator("[data-ai-stop]").click()
    expect(card.locator("[data-ai-audio-status]")).to_contain_text("전사 완료", timeout=15000)
    page.wait_for_function("() => State.pageData.blocks[0].props.translation_source === State.pageData.blocks[0].props.transcript", timeout=15000)
    card.locator("[data-ai-translate-auto]").uncheck()
    assert all("OLD" not in call["text"] and "OLD" not in call["context"] for call in calls)
    assert page.evaluate("() => State.pageData.blocks[0].props.transcript").startswith(old)


def test_local_twelve_second_batch_before_auto_checkbox_and_latency_display(document_page):
    from playwright.sync_api import expect
    page = document_page
    capabilities(page, local=True); fake_microphone(page)
    transcriptions, pending, summaries = [], [], []
    page.route("**/api/notion/ai-notes/transcribe", lambda route: transcriptions.append(transcribe_reply(route, "Synthetic latency speech.")))
    page.route("**/api/notion/ai-notes/translate", lambda route: pending.append(route))
    page.route("**/api/notion/ai-notes/summarize", lambda route: (summaries.append(True), route.abort()))
    card = _insert(page)
    show_audio_settings(card)
    card.get_by_label("소리 입력", exact=True).select_option("microphone")
    expect(card.get_by_label("음성 처리 간격", exact=True)).to_have_value("speech")
    card.get_by_label("음성 처리 간격", exact=True).select_option("12")
    expect(card.locator("[data-ai-translate-auto]")).not_to_be_checked()
    card.locator("[data-ai-record]").click()
    expect(card.get_by_label("음성 처리 간격", exact=True)).to_be_disabled()
    page.wait_for_timeout(3000)
    assert not transcriptions  # No meaningless two-second request before a full batch.
    page.wait_for_function("() => (State.pageData.blocks[0].props.transcript || '').includes('[00:12]')", timeout=35000)
    card.get_by_role("tab", name="번역", exact=True).click()
    with page.expect_request("**/api/notion/ai-notes/translate"):
        card.locator("[data-ai-translate-auto]").check()
    expect(card.locator("[data-ai-translation-latency]")).to_contain_text("번역 응답 대기")
    card.locator("[data-ai-stop]").click()
    expect(card.locator("[data-ai-audio-status]")).to_contain_text("전사 완료", timeout=15000)
    expect(card.locator("[data-ai-audio-latency]")).to_contain_text("음성 구간 12초")
    expect(card.locator("[data-ai-audio-latency]")).to_contain_text("최근 전사 요청")
    page.unroute("**/api/notion/ai-notes/translate")
    page.route("**/api/notion/ai-notes/translate", translated)
    for route in pending:
        translated(route)
    page.wait_for_function("() => State.pageData.blocks[0].props.translation_source === State.pageData.blocks[0].props.transcript")
    expect(card.locator("[data-ai-translation-latency]")).to_contain_text("최근 번역 요청")
    card.locator("[data-ai-translate-auto]").uncheck()
    show_audio_settings(card)
    card.get_by_label("음성 처리 간격", exact=True).select_option("12")
    page.evaluate("() => savePage()")
    page.reload(); _wait_for_notion_ready(page)
    expect(card.get_by_label("음성 처리 간격", exact=True)).to_have_value("12")
    assert len(transcriptions) >= 2 and not summaries


@pytest.mark.parametrize("pair", [["ko", "en"], ["ko", "ja"], ["en", "ja"]])
def test_pair_picker_both_directions_reload_export_clear_and_stale(document_page, pair):
    from playwright.sync_api import expect
    from code_assist_v1.notion_notes_translation import PAIR_LANGUAGES
    page = document_page
    errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
    capabilities(page)
    calls = []
    page.route("**/api/notion/ai-notes/translate", lambda route: calls.append(interpreted(route)))
    card = _insert(page)
    phrases = {"ko": "안녕하세요.", "en": "Hello.", "ja": "こんにちは。"}
    source = "\n".join(phrases[code] for code in pair)
    card.get_by_label("회의록 원문", exact=True).fill(source)
    show_audio_settings(card)
    card.get_by_label("음성 언어", exact=True).select_option("ko")
    card.get_by_role("tab", name="번역", exact=True).click()
    card.get_by_label("번역 방식", exact=True).select_option("pair")
    card.get_by_label("통역 언어 2", exact=True).select_option(pair[1])
    card.get_by_label("통역 언어 1", exact=True).select_option(pair[0])
    expect(card.get_by_label("음성 언어", exact=True)).to_have_value("")
    expect(card.get_by_label("음성 언어", exact=True)).to_be_disabled()
    expect(card.locator("[data-ai-translate-single]")).to_be_hidden()
    expect(card.locator(f'[data-ai-translate-pair="0"] option[value="{pair[1]}"]')).to_have_attribute("disabled", "")
    assert calls == []
    card.locator("[data-ai-translate-start]").click()
    expect(card.locator("[data-ai-translation-status]")).to_have_text("현재 원문 번역 완료 · 저장됨", timeout=15000)
    assert len(calls) == 1 and calls[0]["language_pair"] == pair
    for origin in pair:
        other = pair[1 - pair.index(origin)]
        expect(card.locator("[data-ai-translation-text]")).to_contain_text(f"{PAIR_LANGUAGES[origin]} → {PAIR_LANGUAGES[other]}")
    page.reload(); _wait_for_notion_ready(page)
    card.get_by_role("tab", name="번역", exact=True).click()
    expect(card.get_by_label("번역 방식", exact=True)).to_have_value("pair")
    expect(card.get_by_label("통역 언어 1", exact=True)).to_have_value(pair[0])
    expect(card.get_by_label("통역 언어 2", exact=True)).to_have_value(pair[1])
    expect(card.locator("[data-ai-translate-auto]")).not_to_be_checked()
    expect(card.locator("[data-ai-translation-stale]")).to_be_hidden()
    card.locator("[data-ai-translate-start]").click()
    expect(card.locator("[data-ai-translation-status]")).to_have_text("현재 원문 번역 완료 · 저장됨")
    assert len(calls) == 1
    card.locator("[data-ai-export-kind]").select_option("translation")
    with page.expect_download() as download:
        card.locator("[data-ai-export]").click()
    saved = download.value.path().read_text(encoding="utf-8-sig")
    assert "→" in saved and all(phrases[code] in saved for code in pair)
    # Changing the configuration preserves old results until explicit clear.
    card.get_by_label("번역 방식", exact=True).select_option("single")
    expect(card.locator("[data-ai-translation-stale]")).to_be_visible()
    expect(card.locator("[data-ai-translate-start]")).to_be_disabled()
    expect(card.locator("[data-ai-translation-text]")).not_to_have_text("")
    card.get_by_label("번역 방식", exact=True).select_option("pair")
    page.once("dialog", lambda dialog: dialog.accept())
    card.locator("[data-ai-translate-clear]").click()
    expect(card.locator("[data-ai-translation-text]")).to_have_text("")
    assert page.evaluate("() => State.pageData.blocks[0].props.transcript") == source
    expect(card.get_by_label("통역 언어 1", exact=True)).to_have_value(pair[0])
    assert not errors


def test_pair_live_audio_forces_detection_and_keeps_incremental_order(document_page):
    from playwright.sync_api import expect
    page = document_page
    capabilities(page, local=True); fake_microphone(page)
    transcriptions, calls = [], []
    def transcribed(route):
        # A previously selected Korean language must not pin a bilingual recording.
        assert b'name="language"\r\n\r\nko' not in route.request.post_data_buffer
        phrase = "안녕하세요." if len(transcriptions) % 2 == 0 else "こんにちは。"
        transcriptions.append(transcribe_reply(route, phrase))
    page.route("**/api/notion/ai-notes/transcribe", transcribed)
    page.route("**/api/notion/ai-notes/translate", lambda route: calls.append(interpreted(route)))
    card = _insert(page)
    show_audio_settings(card)
    card.get_by_label("소리 입력", exact=True).select_option("microphone")
    card.get_by_label("음성 언어", exact=True).select_option("ko")
    card.get_by_role("tab", name="번역", exact=True).click()
    card.get_by_label("번역 방식", exact=True).select_option("pair")
    card.get_by_label("통역 언어 2", exact=True).select_option("ja")
    card.locator("[data-ai-translate-auto]").check()
    assert page.evaluate("() => syntheticMicCalls") == 0
    card.locator("[data-ai-record]").click()
    expect(card.get_by_label("번역 방식", exact=True)).to_be_disabled()
    # The default speech mode bounds continuous sound at 20s; two segments
    # plus STT/translation/save overhead are required.
    page.wait_for_function("() => (State.pageData.blocks[0].props.translation_text || '').includes('일본어 → 한국어')", timeout=55000)
    card.locator("[data-ai-stop]").click()
    expect(card.locator("[data-ai-audio-status]")).to_contain_text("전사 완료", timeout=15000)
    card.get_by_role("tab", name="번역", exact=True).click()
    page.wait_for_function("() => State.pageData.blocks[0].props.translation_source === State.pageData.blocks[0].props.transcript", timeout=15000)
    card.locator("[data-ai-translate-stop]").click()
    expect(card.locator("[data-ai-translation-text]")).to_contain_text("한국어 → 일본어")
    expect(card.locator("[data-ai-translation-text]")).to_contain_text("일본어 → 한국어")
    assert "".join(call["text"] for call in calls) == page.evaluate("() => State.pageData.blocks[0].props.transcript")
    assert all(call["language_pair"] == ["ko", "ja"] for call in calls)
