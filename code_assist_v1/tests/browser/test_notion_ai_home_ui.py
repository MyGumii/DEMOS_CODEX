"""AI home UI journeys in an isolated server, with synthetic model/API replies."""
import copy
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4

import pytest
from playwright.sync_api import expect

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _seed_blocks, _screenshot
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready
from code_assist_v1.tests.test_notion_ai_home import ARTIFACTS

MODELS = [{"id": "gemma4", "name": "Gemma4", "provider": "hynix_api"},
          {"id": "qwen3.6", "name": "Qwen3.6", "provider": "hynix_api"}]


class SyntheticChatApi:
    def __init__(self, page):
        self.records = {}; self.sent = []; self.creates = []; self.downloads = []
        self.models = list(MODELS); self.hold = False; self.pending = []
        self.wikis = [{"space_id": "wiki1", "display_name": "개인 Wiki", "kind": "personal"}]
        self.abort_once = False; self.fail_once = False; self.abort_create_once = False
        page.route("**/api/notion/ai-home/**", self.handle)

    def answer(self, route):
        data = route.request.post_data_json
        cid = urlsplit(route.request.url).path.split("/")[5]
        record = self.records[cid]
        if not any(item["operation_id"] == data["operation_id"] for item in record["messages"]):
            record["revision"] += 1; record["title"] = data["message"][:80]
            record["messages"].extend([
                {"role": "user", "text": data["message"], "operation_id": data["operation_id"]},
                {"role": "assistant", "text": "## 핵심 요약\n금요일 보고서 제출 계획입니다. <script>no execution</script>\n- **금요일** 제출\n```text\n<script>not executable</script>\n```",
                 "operation_id": data["operation_id"], "model_name": "Gemma4", "mode": data["mode"],
                 "artifact": ARTIFACTS.get(data["mode"]), "citations": [{"source_id": "P1", "title": "합성 자료", "quote": "금요일 제출"}] if data["mode"] == "research" else [],
                 "context_info": {"truncated": False}}])
        record["pending"] = False
        route.fulfill(json=record)

    def handle(self, route):
        parsed = urlsplit(route.request.url); suffix = parsed.path.split("/ai-home/", 1)[1]
        if suffix == "capabilities":
            return route.fulfill(json={"models": self.models, "knowledge": {"enabled": True, "mode": "canonical-views", "items": self.wikis, "next_cursor": None}})
        if suffix == "conversations":
            if route.request.method == "POST":
                data = route.request.post_data_json; self.creates.append(data)
                cid = str(uuid4()); self.records[cid] = {"id": cid, "revision": 0, "title": "새 채팅", "archived": False, "pending": False, "messages": [], "scope": {key: data[key] for key in ("page_ids", "workspace_id", "knowledge_space_id")}}
                if self.abort_create_once:
                    self.abort_create_once = False; return route.abort("failed")
                return route.fulfill(json=self.records[cid])
            archived = parse_qs(parsed.query).get("archived") == ["1"]
            return route.fulfill(json={"items": [record for record in reversed(list(self.records.values())) if record["archived"] == archived], "next_cursor": None})
        parts = suffix.split("/"); cid = parts[1]; record = self.records[cid]
        if len(parts) == 2:
            return route.fulfill(json=record)
        if parts[2] == "archive":
            record["archived"] = route.request.post_data_json["archived"]
            return route.fulfill(json={"id": cid, "archived": record["archived"]})
        if "download" in parts:
            self.downloads.append(parts[-1]); return route.fulfill(body="synthetic export", content_type="application/octet-stream")
        if parts[2] == "messages":
            self.sent.append(route.request.post_data_json)
            if self.abort_once:
                self.abort_once = False; return route.abort("failed")
            if self.fail_once:
                self.fail_once = False; return route.fulfill(status=503, json={"message": "합성 연결 오류"})
            if self.hold:
                record["pending"] = True; self.pending.append(route); return
            return self.answer(route)
        raise AssertionError(suffix)


def open_home(page):
    page.locator("#aiHomeNewChatBtn").click()
    expect(page.locator("#aiHomeView")).to_be_visible()
    expect(page.locator("#aiHomeModel option")).to_have_count(3)
    expect(page.locator("#aiHomeSend")).to_be_enabled()


def send(page, text="계획을 정리해줘"):
    page.locator("#aiHomeInput").fill(text)
    page.locator("#aiHomeSend").click()
    expect(page.locator("#aiHomeStatus")).to_have_text("응답을 저장했습니다.")


def test_ai_home_navigation_history_reload_archive_restore(document_page, tmp_path):
    page = document_page; api = SyntheticChatApi(page); errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    _seed_blocks(page, [{"type": "heading2", "content": "합성 테스트 문서"}])
    open_home(page)
    expect(page.locator("#aiHomeHero")).to_contain_text("무엇을 도와드릴까요?")
    expect(page.locator("#pageView")).to_be_hidden()
    expect(page.locator("#sidebarPagePanel")).to_be_hidden()
    expect(page.locator(".sidebar-footer").get_by_text("Second Brain 템플릿 만들기")).to_have_count(0)
    _screenshot(page, tmp_path, "ai-home-empty.png")
    send(page)
    cid = next(iter(api.records))
    assert page.url.endswith("/ai/" + cid)
    expect(page.locator("#aiHomeMessages article")).to_have_count(2)
    expect(page.locator("#aiHomeMessages script")).to_have_count(0)
    expect(page.locator("#aiHomeMessages h3")).to_have_text("핵심 요약")
    expect(page.locator("#aiHomeMessages strong")).to_have_text("금요일")
    expect(page.locator("#aiHomeMessages pre code")).to_contain_text("<script>not executable</script>")
    page.reload()
    expect(page.locator("#aiHomeMessages")).to_contain_text("금요일 보고서")
    expect(page.locator("#aiHomeHero")).to_be_hidden()
    _screenshot(page, tmp_path, "ai-home-conversation.png")
    page.locator("#aiHomeNewInline").click()
    expect(page.locator("#aiHomeMessages")).to_be_empty()
    page.locator("#aiHomeHistory").get_by_role("button", name="계획을 정리해줘", exact=True).click()
    expect(page.locator("#aiHomeMessages article")).to_have_count(2)
    page.get_by_role("button", name="계획을 정리해줘 보관", exact=True).click()
    expect(page.locator("#aiHomeMessages")).to_be_empty()
    page.locator("#aiHomeArchived").check()
    page.get_by_role("button", name="계획을 정리해줘 복원", exact=True).click()
    expect(page.locator("#aiHomeHistory .ai-history-item")).to_have_count(0)
    page.locator("#aiHomeArchived").uncheck()
    expect(page.locator("#aiHomeHistory .ai-history-item")).to_have_count(1)
    page.locator("#homeNavBtn").click()
    expect(page.locator("#sidebarPagePanel")).to_be_visible()
    expect(page.locator("#aiHomeView")).to_be_visible()
    page.locator("#workspaceHomeBtn").click()
    expect(page.locator("#aiHomeView")).to_be_hidden()
    page.keyboard.press("Control+o")
    expect(page.locator("#aiHomeView")).to_be_visible()
    expect(page.locator("#aiHomeMessages")).to_be_empty()
    assert not errors


def test_sidebar_tabs_available_before_boot_and_explicit_home_navigation(document_page, tmp_path):
    page = document_page
    # The tab shell is available before boot; only its inactive panel is hidden.
    page.route("**/notion_static/notion.js?*", lambda route: route.abort())
    page.reload()
    expect(page.locator("#homeNavBtn")).to_be_visible()
    expect(page.locator("#sidebarChatNavBtn")).to_be_visible()
    expect(page.locator("#aiHomeSidebar")).to_be_hidden()
    expect(page.locator("#notionAiNavBtn")).to_have_count(0)
    page.unroute("**/notion_static/notion.js?*")
    page.reload()
    _wait_for_notion_ready(page)
    expect(page.locator("#sidebarChatNavBtn")).to_be_visible()
    page.locator("#homeNavBtn").click()
    expect(page.locator("#sidebarTabMenu")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#sidebarTabMenu")).to_be_hidden()
    expect(page.locator("#pageView")).to_be_visible()
    page.locator("#workspaceHomeBtn").click()
    expect(page.locator("#homeView")).to_be_visible()
    expect(page.locator("#homeView")).not_to_contain_text("Second Brain 연결")
    expect(page.locator("#homeTemplateBtn")).to_have_count(0)
    expect(page.locator("#aiHomeNewChatBtn")).to_be_visible()
    _screenshot(page, tmp_path, "home-cleaned-sidebar.png")


def test_wiki_kind_path_choices_keep_one_scope_and_start_new_chat(document_page, tmp_path):
    page = document_page; api = SyntheticChatApi(page)
    api.wikis.extend([
        {"space_id": "part1", "kind": "part", "display_name": "설계 파트", "org_path": ["개발 담당", "제품 팀", "설계 파트"]},
        {"space_id": "team1", "kind": "team", "display_name": "제품 팀", "org_path": ["개발 담당", "제품 팀"]},
        {"space_id": "responsibility1", "kind": "responsibility", "display_name": "개발 담당", "org_path": ["개발 담당"]},
    ])
    open_home(page)
    page.locator("#aiHomeContextPicker summary").click()
    expect(page.locator("#aiHomeWiki optgroup")).to_have_count(4)
    expect(page.locator('#aiHomeWiki option[value="part1"]')).to_have_text("설계 파트 — 개발 담당 / 제품 팀 / 설계 파트")
    for sid in ["wiki1", "part1", "team1", "responsibility1"]:
        page.locator("#aiHomeWiki").select_option(sid)
        expect(page.locator("#aiHomeMessages")).to_be_empty()
        if sid == "part1":
            _screenshot(page, tmp_path, "ai-chat-organization-wiki.png")
        page.locator("#aiHomeContextPicker summary").click()
        send(page, "선택 Wiki에서 정리 " + sid)
        assert api.creates[-1]["knowledge_space_id"] == sid
        assert api.creates[-1]["page_ids"] == []
        page.locator("#aiHomeContextPicker summary").click()
    assert len(api.records) == 4 and all(len(record["messages"]) == 2 for record in api.records.values())


def test_context_changes_start_new_conversation_and_provider_gating(document_page, tmp_path):
    page = document_page; api = SyntheticChatApi(page)
    open_home(page); send(page)
    original = copy.deepcopy(next(iter(api.records.values())))
    assert "codex" not in page.locator("#aiHomeModel").inner_text().lower()
    page.locator("#aiHomeContextPicker summary").click()
    page.locator("#aiHomePageChoices input").first.check()
    expect(page.locator("#aiHomeMessages")).to_be_empty()
    expect(page.locator("#aiHomeStatus")).to_contain_text("참고 범위가 바뀌어")
    page.locator("#aiHomeWiki").select_option("wiki1")
    _screenshot(page, tmp_path, "ai-home-context.png")
    page.locator("#aiHomeContextPicker summary").click()
    page.locator("#aiHomeModel").select_option("qwen3.6")
    page.locator('[data-ai-home-mode="research"]').click()
    send(page, "자료에서 일정을 조사해줘")
    assert api.creates[-1]["page_ids"] and api.creates[-1]["knowledge_space_id"] == "wiki1"
    assert api.sent[-1]["model_id"] == "qwen3.6" and api.sent[-1]["mode"] == "research"
    assert api.records[original["id"]] == original
    expect(page.locator(".ai-home-citations")).to_contain_text("출처 1개")
    api.models.append({"id": "codex", "name": "Codex bridge", "provider": "codex"})
    page.locator("#aiHomeNewInline").click()
    expect(page.locator("#aiHomeModel option")).to_have_count(4)
    page.locator("#aiHomeModel").select_option("codex")
    send(page, "코덱스 명시 선택")
    assert api.sent[-1]["model_id"] == "codex"
    api.models = []
    page.locator("#aiHomeNewInline").click()
    expect(page.locator("#aiHomeSend")).to_be_disabled()
    expect(page.locator("#aiHomeModel option")).to_have_count(1)
    assert "codex" not in page.locator("#aiHomeModel").inner_text().lower()


@pytest.mark.parametrize("mode,kind,selector", [("slides", "html", ".ai-home-slide"), ("spreadsheet", "csv", "table"), ("visualization", "svg", ".ai-home-bar")])
def test_artifact_cards_preview_and_download(document_page, tmp_path, mode, kind, selector):
    page = document_page; api = SyntheticChatApi(page)
    open_home(page)
    page.locator(f'[data-ai-home-mode="{mode}"]').click()
    send(page, "합성 자료를 만들어줘")
    assert api.sent[-1]["mode"] == mode
    expect(page.locator(".ai-home-artifact " + selector).first).to_be_visible()
    expect(page.locator(".ai-home-artifact script")).to_have_count(0)
    with page.expect_download() as output:
        page.get_by_role("button", name=kind.upper() + " 다운로드", exact=True).click()
    assert output.value.suggested_filename.endswith("." + kind)
    assert output.value.failure() is None and api.downloads == [kind]
    _screenshot(page, tmp_path, f"ai-home-{mode}.png")


def test_unknown_delivery_retries_same_operation_and_known_failure_keeps_input(document_page):
    page = document_page; api = SyntheticChatApi(page)
    open_home(page); api.abort_once = True
    page.locator("#aiHomeInput").fill("재시도할 질문")
    page.locator("#aiHomeSend").click()
    expect(page.locator("#aiHomeStatus")).to_have_attribute("data-error", "true")
    expect(page.locator("#aiHomeInput")).to_have_value("재시도할 질문")
    op = api.sent[-1]["operation_id"]
    page.locator("#aiHomeSend").click()
    expect(page.locator("#aiHomeMessages article")).to_have_count(2)
    assert api.sent[-1]["operation_id"] == op
    api.fail_once = True
    page.locator("#aiHomeInput").fill("다음 질문")
    page.locator("#aiHomeSend").click()
    expect(page.locator("#aiHomeStatus")).to_have_text("합성 연결 오류")
    expect(page.locator("#aiHomeInput")).to_have_value("다음 질문")
    previous = api.sent[-1]["operation_id"]
    page.locator("#aiHomeSend").click()
    expect(page.locator("#aiHomeMessages article")).to_have_count(4)
    assert api.sent[-1]["operation_id"] != previous


def test_pending_generation_navigation_and_ime_guard(document_page):
    page = document_page; api = SyntheticChatApi(page)
    open_home(page); api.hold = True
    page.locator("#aiHomeInput").fill("오래 걸리는 질문")
    page.locator("#aiHomeInput").dispatch_event("keydown", {"key": "Enter", "isComposing": True})
    assert not api.sent
    with page.expect_request("**/messages"):
        page.locator("#aiHomeSend").click()
    expect(page.locator("#aiHomeInput")).to_be_disabled()
    expect(page.locator("#aiHomeModel")).to_be_disabled()
    cid = next(iter(api.records))
    page.locator("#aiHomeNewInline").click()
    expect(page.locator("#aiHomeInput")).to_have_value("")
    api.answer(api.pending.pop())
    expect(page.locator("#aiHomeMessages")).to_be_empty()
    page.goto(page.url.rstrip("/") + "/" + cid)
    expect(page.locator("#aiHomeMessages article")).to_have_count(2)
    expect(page.locator("#aiHomeInput")).to_be_enabled()


def test_changed_scope_after_lost_create_uses_new_operation(document_page):
    page = document_page; api = SyntheticChatApi(page); open_home(page)
    api.abort_create_once = True
    page.locator("#aiHomeInput").fill("첫 대화")
    page.locator("#aiHomeSend").click()
    expect(page.locator("#aiHomeStatus")).to_have_attribute("data-error", "true")
    first = api.creates[0]
    page.locator("#aiHomeContextPicker summary").click()
    page.locator("#aiHomeWiki").select_option("wiki1")
    page.locator("#aiHomeContextPicker summary").click()
    send(page, "참고 범위 변경 후 전송")
    assert api.creates[-1]["operation_id"] != first["operation_id"]
    assert api.creates[-1]["knowledge_space_id"] == "wiki1"


def test_pending_conversation_reload_polls_without_model_resubmission(document_page):
    page = document_page; api = SyntheticChatApi(page); open_home(page); send(page)
    cid = next(iter(api.records)); api.records[cid]["pending"] = True
    page.reload()
    expect(page.locator("#aiHomeStatus")).to_contain_text("이전 요청을 처리 중")
    expect(page.locator("#aiHomeSend")).to_be_disabled()
    api.records[cid]["pending"] = False
    expect(page.locator("#aiHomeStatus")).to_have_text("응답을 불러왔습니다.", timeout=10000)
    expect(page.locator("#aiHomeSend")).to_be_enabled()
    assert len(api.sent) == 1


def test_compact_layout_and_page_return_preserve_document_ai(document_page, tmp_path):
    page = document_page; api = SyntheticChatApi(page)
    original_url = page.url
    open_home(page)
    page.set_viewport_size({"width": 800, "height": 740})
    expect(page.locator("#aiHomeComposer")).to_be_visible()
    assert page.locator("#aiHomeView").evaluate("node => node.scrollWidth <= node.clientWidth + 1")
    _screenshot(page, tmp_path, "ai-home-compact.png")
    page.goto(original_url); _wait_for_notion_ready(page)
    page.route("**/api/notion/document-ai/capabilities", lambda route: route.fulfill(json={"models": MODELS}))
    page.get_by_role("button", name="문서 AI", exact=True).click()
    expect(page.locator("#aiPanel")).to_be_visible()
    expect(page.locator("#aiHomeView")).to_be_hidden()


def test_full_host_authenticated_routes_without_ai_home_api_stubs(document_page):
    """Verify real app registration/auth/HTML against the isolated host, not just mocks."""
    page = document_page
    parsed = urlsplit(page.url); origin = f"{parsed.scheme}://{parsed.netloc}"
    response = page.request.get(origin + "/api/notion/ai-home/capabilities")
    assert response.status == 200
    caps = response.json()
    assert caps["max_pages"] == 4 and caps["max_excerpt_bytes"] == 98304
    assert caps["web_search"] is False
    assert caps["exports"] == {"slides": "html", "spreadsheet": "csv", "visualization": "svg", "research": "md"}
    assert all(model["id"] != "codex" for model in caps["models"])
    listing = page.request.get(origin + "/api/notion/ai-home/conversations")
    assert listing.status == 200 and listing.json()["items"] == []
    page.goto(origin + "/ai"); _wait_for_notion_ready(page)
    expect(page.locator("#aiHomeHero")).to_be_visible()
    expect(page.locator("#aiHomeModel option")).to_have_count(len(caps["models"]) + 1)
    expect(page.locator("#aiHomeHistoryStatus")).to_have_text("아직 대화가 없습니다.")
