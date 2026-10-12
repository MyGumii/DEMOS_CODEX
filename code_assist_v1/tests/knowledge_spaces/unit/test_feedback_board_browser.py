"""Real board JS/Modal + real HTTP/storage using isolated temporary accounts.

No posts are seeded in the user's running preview. The minimal shell uses the
production Modal implementation; full-application authentication is covered by
the separate opt-in 10221 browser smoke runner.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from uuid import uuid4

from flask import Flask, request
from playwright.sync_api import sync_playwright, expect
import pytest
from werkzeug.serving import make_server

from code_assist_v1.knowledge_core.workspace_feature_routes import register_workspace_feature_routes
from code_assist_v1.tests.knowledge_spaces.unit.test_workspace_features import wiki
from code_assist_v1.tests.knowledge_spaces.unit.test_feedback_board_search import AUTHOR, OTHER, ADMIN, add


pytestmark = pytest.mark.unit
STATIC = Path(__file__).resolve().parents[3] / "static"


@pytest.fixture
def board_server(wiki):
    _, _, source, management, _ = wiki
    app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")
    register_workspace_feature_routes(app, source, management,
        lambda: {"id": request.cookies.get("fixture_user", "member01"), "scope_epoch": 1})
    store = app.extensions["workspace_features"]["feedback"]
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    modal = app_js[app_js.index("const Modal = {"):app_js.index('$("#modalBg").addEventListener')]

    @app.get("/")
    def shell():
        user = request.cookies.get("fixture_user", "member01")
        return """<!doctype html><html lang="ko" data-theme="light"><head><meta charset="UTF-8">
        <link rel="stylesheet" href="/static/app.css"><link rel="stylesheet" href="/static/workspace_features.css">
        </head><body><button id="feedbackBoardBtn">버그·개선 요청</button>
        <div id="modalBg" class="modal-bg"><div id="modal" class="modal">
        <div class="head"><span id="modalTitle" class="title"></span><button id="modalClose">닫기</button></div>
        <div id="modalBody" class="body"></div><div id="modalFoot" class="foot"></div></div></div>
        <script>const $=selector=>document.querySelector(selector);
        const State={currentUser:{id:""" + json.dumps(user) + """}};
        window.feedbackErrors=[];const toast=message=>window.feedbackErrors.push(message);
        const authenticatedFetch=(url,options)=>fetch(url,options);
        """ + modal + """
        document.getElementById('modalClose').onclick=()=>Modal.close();
        </script><script src="/static/workspace_features.js"></script></body></html>"""

    for number in range(65):
        actor = AUTHOR if number % 2 == 0 else OTHER
        pid = add(store, actor, title=f"동작 검증 {number:03d}",
            body="지연신호 보고" if number == 12 else f"본문 상세 {number}",
            kind="bug" if number < 62 else "improvement")
        if number < 5:
            store.mutate(ADMIN, action="status", operation_id=str(uuid4()), post_id=pid,
                expected_version=1, status="completed")
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", store
    finally:
        server.shutdown()
        thread.join(10)
        server.server_close()


def test_wf007_board_search_pages_scroll_restore_and_empty_page_fallback(board_server, tmp_path):
    base, store = board_server
    evidence = Path(os.environ.get("SB_FEEDBACK_EVIDENCE_DIR", str(tmp_path)))
    evidence.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base)
        page.locator("#feedbackBoardBtn").click()
        rows = page.locator(".feedback-post-row")
        info = page.locator(".feedback-page-info")
        expect(rows).to_have_count(30)
        expect(page.get_by_role("button", name="← 이전", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="더 보기", exact=True)).to_have_count(0)
        page.get_by_label("요청 유형 필터").select_option("bug")
        page.get_by_label("게시글 검색어").fill("동작")
        page.get_by_label("게시글 검색어").press("Enter")
        expect(info).to_contain_text("1페이지 · 30개")
        page.get_by_role("button", name="다음 →", exact=True).click()
        expect(info).to_contain_text("2페이지 · 30개")
        expect(rows).to_have_count(30)
        second_ids = rows.evaluate_all("rows=>rows.map(row=>row.dataset.feedbackPostId)")
        page.locator("#modalBody").evaluate("el=>el.scrollTop=600")
        target = rows.nth(18)
        expect(target).to_be_in_viewport(ratio=1)
        scroll = page.locator("#modalBody").evaluate("el=>el.scrollTop")
        target_id = target.get_attribute("data-feedback-post-id")
        target.click()
        page.locator("#modalFoot").get_by_role("button", name="← 게시판으로", exact=True).click()
        expect(info).to_contain_text("2페이지 · 30개")
        expect(page.get_by_label("게시글 검색어")).to_have_value("동작")
        expect(page.get_by_label("요청 유형 필터")).to_have_value("bug")
        assert rows.evaluate_all("rows=>rows.map(row=>row.dataset.feedbackPostId)") == second_ids
        page.wait_for_function("expected=>Math.abs(document.getElementById('modalBody').scrollTop-expected)<2", arg=scroll)
        assert page.evaluate("document.activeElement.dataset.feedbackPostId") == target_id
        page.screenshot(path=str(evidence / "board-second-page.png"), full_page=True)

        page.get_by_role("button", name="← 이전", exact=True).click()
        expect(info).to_contain_text("1페이지")
        page.get_by_role("button", name="다음 →", exact=True).click()
        expect(info).to_contain_text("2페이지")
        assert rows.evaluate_all("rows=>rows.map(row=>row.dataset.feedbackPostId)") == second_ids

        page.get_by_label("게시글 검색어").fill("민수")
        page.get_by_label("검색 범위").select_option("requester")
        expect(info).to_contain_text("1페이지 · 30개")
        expect(rows).to_have_count(30)
        assert all("김민수 (member01)" in text for text in page.locator(".feedback-row-author").all_text_contents())
        page.screenshot(path=str(evidence / "board-requester-search.png"), full_page=True)
        page.get_by_label("게시글 검색어").fill("OTHER01")
        page.get_by_label("게시글 검색어").press("Enter")
        page.wait_for_function("[...document.querySelectorAll('.feedback-row-author')].every(el=>el.textContent.includes('other01')) && document.querySelectorAll('.feedback-post-row').length===30")
        page.get_by_label("내가 쓴 글만").check()
        expect(info).to_contain_text("1페이지 · 0개")
        expect(rows).to_have_count(0)
        expect(page.get_by_role("button", name="다음 →", exact=True)).to_be_disabled()
        page.get_by_role("button", name="초기화", exact=True).click()
        expect(rows).to_have_count(30)
        page.get_by_label("게시글 검색어").fill("지연신호")
        page.get_by_label("검색 범위").select_option("body")
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text("012")
        page.get_by_role("button", name="초기화", exact=True).click()
        page.get_by_label("처리 상태 필터").select_option("completed")
        expect(rows).to_have_count(5)
        page.get_by_role("button", name="초기화", exact=True).click()
        expect(rows).to_have_count(30)
        page.get_by_role("button", name="다음 →", exact=True).click()
        expect(info).to_contain_text("2페이지")
        page.get_by_role("button", name="다음 →", exact=True).click()
        expect(info).to_contain_text("3페이지 · 5개")
        final_ids = rows.evaluate_all("rows=>rows.map(row=>row.dataset.feedbackPostId)")
        rows.first.click()
        expect(page.locator(".feedback-detail")).to_be_visible()
        for pid in final_ids:
            post = store.get(AUTHOR, pid)
            actor = AUTHOR if post["author_id"] == AUTHOR["id"] else OTHER
            store.mutate(actor, action="delete", operation_id=str(uuid4()), post_id=pid, expected_version=post["row_version"])
        page.locator("#modalFoot").get_by_role("button", name="← 게시판으로", exact=True).click()
        expect(info).to_contain_text("2페이지 · 30개")
        expect(page.get_by_role("button", name="다음 →", exact=True)).to_be_disabled()
        assert not errors and page.evaluate("window.feedbackErrors") == []

        other = browser.new_context(viewport={"width": 1440, "height": 1000})
        other.add_cookies([{"name": "fixture_user", "value": "other01", "url": base}])
        reader = other.new_page()
        reader.goto(base)
        reader.locator("#feedbackBoardBtn").click()
        reader.get_by_label("내가 쓴 글만").check()
        expect(reader.locator(".feedback-post-row")).to_have_count(30)
        assert all("other01" in text for text in reader.locator(".feedback-row-author").all_text_contents())
        (evidence / "search-pagination-report.json").write_text(json.dumps({
            "status": "pass", "fixture_posts": 65, "page_size": 30,
            "requester_name_and_account_id": True, "body_search": True,
            "kind_status_mine_filters": True, "previous_next_replace_rows": True,
            "return_restores_page_filters_scroll_and_focus": True,
            "empty_last_page_returns_to_previous": True, "page_errors": errors,
            "fixture_scope": "isolated temporary database and HTTP server",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        browser.close()
