"""Document AI UI journeys; no live pages, audio or model calls."""
from playwright.sync_api import expect

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _seed_blocks, _screenshot
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready

MODELS = [{"id": "gemma4", "name": "Gemma4", "provider": "hynix_api"},
          {"id": "qwen3.6", "name": "Qwen3.6", "provider": "hynix_api"}]


def setup(page, models=None):
    page.route("**/api/notion/document-ai/capabilities", lambda route: route.fulfill(json={"models": MODELS if models is None else models}))
    _seed_blocks(page, [{"type": "heading2", "content": "주간 계획"},
                        {"type": "paragraph", "content": "금요일까지 보고서를 제출합니다."}])


def open_ai(page):
    page.get_by_role("button", name="문서 AI", exact=True).click()
    expect(page.locator("#aiPanel")).to_be_visible()
    expect(page.locator("#documentAiCapabilities")).not_to_have_text("모델 연결 확인 중…")


def reply(route, answer="금요일 보고서 제출", **kwargs):
    data = route.request.post_data_json
    route.fulfill(json={"answer": answer, "model_name": "Gemma4", "source": {"scope": "selection" if data.get("block_id") else "page"}, **kwargs})


def test_sidebar_page_summary_insert_save_and_template_removed(document_page, tmp_path):
    page = document_page
    errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
    setup(page)
    requests = []
    def generate(route):
        requests.append(route.request.post_data_json); reply(route, "## 핵심 요약\n금요일에 보고서를 제출합니다.\n<script>untrusted</script>")
    page.route("**/api/notion/document-ai/ask", generate)
    expect(page.locator(".sidebar-footer").get_by_text("Second Brain 템플릿 만들기")).to_have_count(0)
    open_ai(page)
    expect(page.locator("#sidebarPagePanel")).to_be_visible()
    page.get_by_role("button", name="페이지 요약", exact=True).click()
    expect(page.locator("#aiAnswer")).to_contain_text("핵심 요약")
    expect(page.locator("#aiAnswer script")).to_have_count(0)
    expect(page.locator("#blocks .block-row")).to_have_count(2)
    assert requests[0]["model_id"] == "" and requests[0]["history"] == []
    expect(page.locator('[data-ai-result-action="replace"]')).to_be_hidden()
    _screenshot(page, tmp_path, "document-ai-summary.png")
    page.get_by_role("button", name="페이지 끝에 삽입", exact=True).click()
    expect(page.locator("#blocks")).to_contain_text("금요일에 보고서를 제출합니다.")
    page.evaluate("() => savePage()")
    page.reload(); _wait_for_notion_ready(page)
    expect(page.locator("#blocks")).to_contain_text("금요일에 보고서를 제출합니다.")
    assert not errors


def test_selected_text_translation_preserves_other_text_and_format(document_page):
    page = document_page; setup(page)
    _seed_blocks(page, [{"type": "paragraph", "content": "앞 문장 번역할 문장 뒤 문장", "props": {"rich_text_html": "<strong>앞 문장</strong> 번역할 문장 뒤 문장"}}])
    requests = []
    def generate(route):
        requests.append(route.request.post_data_json); reply(route, "翻訳した文章")
    page.route("**/api/notion/document-ai/ask", generate)
    editor = page.locator("#blocks .block-editor").first
    editor.evaluate("""editor => {
      editor.focus(); const node = editor.childNodes[1]; const range = document.createRange();
      range.setStart(node, 1); range.setEnd(node, 7);
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    }""")
    page.locator('[data-inline-format="ai"]').click()
    expect(page.locator("#documentAiSource")).to_have_text("번역할 문장")
    expect(page.locator("#documentAiCapabilities")).to_contain_text("Gemma4")
    page.get_by_label("문서 AI 번역 언어", exact=True).fill("일본어")
    page.get_by_role("button", name="선택한 글 번역", exact=True).click()
    expect(page.locator("#aiAnswer")).to_have_text("翻訳した文章")
    assert requests[0]["selected_text"] == "번역할 문장" and "일본어" in requests[0]["question"]
    page.get_by_role("button", name="선택한 글 바꾸기", exact=True).click()
    expect(page.locator("#blocks .block-editor")).to_have_text("앞 문장 翻訳した文章 뒤 문장")
    expect(page.locator("#blocks .block-editor strong")).to_have_text("앞 문장")
    page.evaluate("() => savePage()")
    page.reload(); _wait_for_notion_ready(page)
    expect(page.locator("#blocks .block-editor")).to_have_text("앞 문장 翻訳した文章 뒤 문장")


def test_provider_visibility_explicit_selection_and_chat_context(document_page):
    page = document_page; setup(page)
    requests = []
    def generate(route):
        requests.append(route.request.post_data_json); reply(route)
    page.route("**/api/notion/document-ai/ask", generate)
    open_ai(page)
    expect(page.locator("#documentAiModel option")).to_have_count(3)
    assert "codex" not in page.locator("#aiPanel").inner_text().lower()
    page.locator("#documentAiModel").select_option("qwen3.6")
    page.locator("#aiQuestion").fill("결론은 뭐야?")
    page.locator("#aiQuestion").press("Control+Enter")
    expect(page.locator("#aiAnswer")).to_contain_text("금요일")
    page.locator("#aiQuestion").fill("더 짧게 정리해줘")
    page.locator("#aiAskBtn").click()
    expect(page.locator("#aiAskBtn")).to_have_text("보내기")
    assert requests[-1]["model_id"] == "qwen3.6" and len(requests[-1]["history"]) == 2
    page.route("**/api/notion/document-ai/capabilities", lambda route: route.fulfill(json={"models": [*MODELS, {"id": "codex", "name": "Codex bridge", "provider": "codex"}]}))
    page.locator("#documentAiReconnect").click()
    expect(page.locator("#documentAiModel option")).to_have_count(4)
    page.locator("#documentAiModel").select_option("codex")
    page.locator("#aiAskBtn").click()
    expect(page.locator("#aiAskBtn")).to_have_text("보내기")
    assert requests[-1]["model_id"] == "codex"
    page.locator("#documentAiClear").click()
    expect(page.locator("#aiAnswer")).to_be_empty()
    expect(page.locator("#documentAiHistory")).to_be_empty()
    page.route("**/api/notion/document-ai/capabilities", lambda route: route.fulfill(json={"models": []}))
    page.locator("#documentAiReconnect").click()
    expect(page.locator("#documentAiModel option")).to_have_count(1)
    expect(page.locator("#aiAskBtn")).to_be_disabled()
    assert "codex" not in page.locator("#aiPanel").inner_text().lower()


def test_late_response_stale_apply_and_revoked_edit_guard(document_page):
    page = document_page; setup(page)
    pending = []
    page.route("**/api/notion/document-ai/ask", lambda route: pending.append(route))
    open_ai(page)
    page.locator("#aiQuestion").fill("요약해줘")
    with page.expect_request("**/api/notion/document-ai/ask"):
        page.locator("#aiAskBtn").click()
    page.evaluate("() => { State.pageData.blocks[1].content = '생성 중 변경'; renderBlocks(); }")
    reply(pending.pop())
    expect(page.locator("#documentAiStatus")).to_contain_text("생성 중 원문이 변경")
    expect(page.locator("#aiAnswer")).to_be_empty()
    with page.expect_request("**/api/notion/document-ai/ask"):
        page.locator("#aiAskBtn").click()
    reply(pending.pop())
    expect(page.locator("#aiAnswer")).to_contain_text("금요일")
    page.evaluate("() => { State.pageData.page.can_edit = false; }")
    page.get_by_role("button", name="페이지 끝에 삽입", exact=True).click()
    expect(page.locator("#documentAiStatus")).to_contain_text("편집 권한이 변경")
    expect(page.locator("#blocks .block-row")).to_have_count(2)
    page.evaluate("() => { State.pageData.page.can_edit = true; }")
    with page.expect_request("**/api/notion/document-ai/ask"):
        page.locator("#aiAskBtn").click()
    page.locator("#documentAiClear").click()
    reply(pending.pop(), "보여서는 안 되는 이전 답변")
    expect(page.locator("#aiAnswer")).to_be_empty()
    expect(page.locator("#aiResultActions")).to_be_hidden()


def test_navigation_discards_request_and_block_menu_opens_editor(document_page):
    page = document_page; setup(page)
    pending = []
    page.route("**/api/notion/document-ai/ask", lambda route: pending.append(route))
    page.locator('#blocks .block-row').nth(1).locator('[data-action="menu"]').click()
    page.get_by_role("option", name="AI로 다듬기").click()
    expect(page.locator("#documentAiSelection")).to_be_visible()
    expect(page.locator("#documentAiCapabilities")).to_contain_text("Gemma4")
    with page.expect_request("**/api/notion/document-ai/ask"):
        page.locator("#aiAskBtn").click()
    page.locator("#homeNavBtn").click()
    reply(pending.pop(), "이전 페이지의 답변")
    open_ai(page)
    expect(page.locator("#aiAnswer")).to_be_empty()
    expect(page.locator("#aiAskBtn")).to_be_disabled()
    expect(page.locator("#documentAiContext")).to_contain_text("먼저 페이지")


def test_api_error_preserves_text_and_allows_retry(document_page):
    page = document_page; setup(page)
    page.route("**/api/notion/document-ai/ask", lambda route: route.fulfill(status=503, json={"message": "모델 연결 실패"}))
    open_ai(page)
    page.locator("#aiQuestion").fill("요약해줘")
    page.locator("#aiAskBtn").click()
    expect(page.locator("#documentAiStatus")).to_have_text("모델 연결 실패")
    expect(page.locator("#aiAskBtn")).to_be_enabled()
    expect(page.locator("#aiQuestion")).to_have_value("요약해줘")
    expect(page.locator("#blocks .block-row")).to_have_count(2)
    page.route("**/api/notion/document-ai/ask", lambda route: reply(route))
    page.locator("#aiAskBtn").click()
    expect(page.locator("#aiAnswer")).to_contain_text("금요일")
