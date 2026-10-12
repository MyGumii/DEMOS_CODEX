from playwright.sync_api import expect

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _screenshot
from code_assist_v1.tests.browser.test_notion_ai_notes import _models, _insert
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready
from code_assist_v1.tests.test_notion_ai_notes import SOURCE, RESULT, MODELS


def test_custom_language_request_reload_and_safe_audio_ui(document_page, tmp_path):
    page = document_page
    _models(page)
    calls = []
    page.route("**/api/notion/ai-notes/summarize", lambda route: (calls.append(route.request.post_data_json), route.fulfill(json={"result": RESULT})))
    card = _insert(page)
    assert "참석자의 녹음" not in card.inner_text()
    assert "브라우저·OS에 따라" not in card.inner_text()
    expect(card.locator("[data-ai-audio-consent]")).to_have_count(0)
    card.get_by_label("회의록 원문", exact=True).fill(SOURCE)
    card.locator(".ai-notes-options summary").click()
    card.get_by_label("번역·요약 언어", exact=True).select_option("custom")
    card.locator("[data-ai-generate]").click()
    expect(card.locator("[data-ai-status]")).to_contain_text("언어를 입력")
    assert calls == []
    card.get_by_label("직접 지정할 요약 언어", exact=True).fill("한국어와 영어 병기")
    card.locator("[data-ai-generate]").click()
    expect(card.locator("[data-ai-status]")).to_have_text("요약 저장됨", timeout=15000)
    assert calls[0]["output_language"] == "한국어와 영어 병기"
    page.reload(); _wait_for_notion_ready(page)
    card.locator(".ai-notes-options summary").click()
    expect(card.get_by_label("번역·요약 언어", exact=True)).to_have_value("custom")
    expect(card.get_by_label("직접 지정할 요약 언어", exact=True)).to_have_value("한국어와 영어 병기")
    card.locator(".ai-notes-options").scroll_into_view_if_needed()
    _screenshot(page, tmp_path, "ai-notes-language-options.png")
    card.get_by_label("번역·요약 언어", exact=True).select_option("en")
    expect(card.get_by_label("직접 지정할 요약 언어", exact=True)).to_be_hidden()


def test_comparison_only_status_no_summary_body_and_reload_poll(document_page, tmp_path):
    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/notion/ai-notes/capabilities", lambda route: route.fulfill(json={"models": MODELS, "comparison": {"enabled": True}}))
    page.route("**/api/notion/ai-notes/comparison-sources", lambda route: route.fulfill(json={"items": [{"space_id": "wiki-1", "display_name": "내 지식 Wiki"}], "next_cursor": "next"}))
    page.route("**/api/notion/ai-notes/comparison-sources?cursor=next", lambda route: route.fulfill(json={"items": [{"space_id": "wiki-2", "display_name": "다른 개인 Wiki"}], "next_cursor": None}))
    requests, summaries = [], []
    job_id = "comparison-test-id"
    state = {"state": "processing"}
    page.route("**/api/notion/ai-notes/comparisons", lambda route: (requests.append(route.request.post_data_json), route.fulfill(status=202, json={"id": job_id, "state": "queued", "message": "비교 처리 대기 중"})))
    page.route("**/api/notion/ai-notes/comparisons/" + job_id, lambda route: route.fulfill(json={"id": job_id, "state": state["state"], "message": "호스트 비교 처리 중" if state["state"] == "processing" else "비교 기록 저장 완료"}))
    page.route("**/api/notion/ai-notes/summarize", lambda route: (summaries.append(True), route.abort()))
    card = _insert(page)
    card.get_by_label("회의록 원문", exact=True).fill(SOURCE)
    card.locator(".ai-notes-options summary").click()
    card.locator("[data-ai-comparison]").check()
    card.locator(".ai-notes-options summary").click()
    picker = card.get_by_label("비교에 사용할 Wiki", exact=True)
    expect(picker.locator('option[value="wiki-1"]')).to_have_count(1)
    card.locator("[data-ai-more-wikis]").click()
    expect(picker.locator('option[value="wiki-2"]')).to_have_count(1)
    picker.select_option("wiki-1")
    card.get_by_label("비교 지식 검색어", exact=True).fill("출시 보고서")
    page.once("dialog", lambda dialog: dialog.dismiss())
    card.get_by_role("button", name="비교 수집 시작", exact=True).click()
    assert not requests
    page.once("dialog", lambda dialog: dialog.accept())
    card.get_by_role("button", name="비교 수집 시작", exact=True).click()
    expect(card.locator("[data-ai-status]")).to_contain_text("호스트 비교 처리 중", timeout=15000)
    assert len(requests) == 1 and requests[0]["knowledge_space_id"] == "wiki-1"
    assert requests[0]["comparison_consent"] is True and requests[0]["knowledge_query"] == "출시 보고서"
    assert not summaries
    expect(card.locator('[data-ai-clear="all"]')).to_be_disabled()
    page.reload(); _wait_for_notion_ready(page)
    expect(card.locator("[data-ai-status]")).to_contain_text("호스트 비교 처리 중")
    state["state"] = "completed"
    expect(card.locator("[data-ai-status]")).to_have_text("비교 기록 저장 완료", timeout=10000)
    expect(card.locator("[data-ai-generate]")).to_be_enabled()
    card.get_by_role("tab", name="요약", exact=True).click()
    expect(card.locator(".ai-notes-summary")).to_have_count(0)
    props = page.evaluate("() => State.pageData.blocks[0].props")
    assert "result" not in props and "baseline" not in props and "enriched" not in props
    assert props["comparison_run_id"] == job_id
    assert page.evaluate("() => !State.pageData.blocks[0].content.includes('knowledge_insights')")
    card.get_by_label("다운로드 내용", exact=True).select_option("summary")
    expect(card.locator("[data-ai-export]")).to_be_disabled()
    card.locator(".ai-notes-options summary").click()
    card.locator(".ai-notes-options").scroll_into_view_if_needed()
    _screenshot(page, tmp_path, "ai-notes-host-comparison-status.png")
    assert not errors
