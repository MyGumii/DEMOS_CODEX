"""UI-COLLAB-001 regressions on isolated page discussions; no live comments are modified."""
from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _screenshot
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready


def _open(page):
    from playwright.sync_api import expect
    page.locator("#addCommentBtn").click()
    expect(page.locator("#pageInlineDiscussion")).to_be_visible()
    expect(page.locator("#commentInput")).to_be_enabled()


def _post(page, text):
    page.locator("#commentInput").fill(text)
    with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/comments")) as response:
        page.locator("#commentSubmitBtn").click()
    return response.value.json()["comment"]


def test_ui_collab_001_discussion_position_overflow_and_close(document_page, tmp_path):
    from playwright.sync_api import expect
    page = document_page
    before = page.locator("#pageEditorGroup").bounding_box()
    _open(page)
    discussion = page.locator("#pageInlineDiscussion")
    box = discussion.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 1440
    assert page.locator("#pageEditorGroup").bounding_box()["y"] >= before["y"]
    expect(page.locator("#inlineCommentsClose")).to_be_visible()
    root = _post(page, "긴 댓글 합성검증\n" * 60 + "X" * 500)
    thread = page.locator(f'[data-comment-thread="{root["id"]}"]')
    expect(thread).to_contain_text("긴 댓글 합성검증")
    thread.locator("[data-comment-reply]").click()
    reply = _post(page, "긴 댓글에도 연결되는 합성 답글")
    assert reply["parent_id"] == root["id"]
    expect(thread.locator(".comment-reply")).to_contain_text("합성 답글")
    reply_box = thread.locator(".comment-reply").bounding_box()
    list_box = page.locator("#commentList").bounding_box()
    assert reply_box["y"] >= list_box["y"] - 1
    assert reply_box["y"] + reply_box["height"] <= list_box["y"] + list_box["height"] + 1
    assert discussion.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
    _screenshot(page, tmp_path, "comments-inline-long-reply.png")
    page.locator("#commentInput").fill("닫아도 유지하는 초안")
    page.locator("#inlineCommentsClose").click()
    expect(discussion).to_be_hidden()
    preview = page.locator("#pageCommentPreview")
    expect(preview).to_be_visible()
    assert preview.bounding_box()["height"] <= 230
    expect(page.locator("#pageCommentAffordanceBtn")).to_have_text("댓글 보기")
    page.locator("#pageCommentAffordanceBtn").click()
    expect(discussion).to_be_visible()
    expect(page.locator("#commentInput")).to_have_value("닫아도 유지하는 초안")
    page.locator("#inlineCommentsClose").click()
    expect(discussion).to_be_hidden()
    _open(page)
    page.keyboard.press("Escape")
    expect(discussion).to_be_hidden()
    page.set_viewport_size({"width": 600, "height": 640})
    _open(page)
    assert discussion.bounding_box()["x"] >= 0
    assert discussion.bounding_box()["x"] + discussion.bounding_box()["width"] <= 600
    expect(page.locator("#inlineCommentsClose")).to_be_visible()
    _screenshot(page, tmp_path, "comments-inline-600.png")


def test_ui_collab_001_reply_reload_resolve_and_delete_do_not_promote(document_page):
    from playwright.sync_api import expect
    page = document_page
    _open(page)
    root = _post(page, "합성 원 댓글")
    thread = page.locator(f'[data-comment-thread="{root["id"]}"]')
    thread.locator("[data-comment-reply]").click()
    reply = _post(page, "합성 답글")
    assert reply["parent_id"] == root["id"]
    expect(page.locator(".comment-thread")).to_have_count(1)
    expect(thread.locator(".comment-reply")).to_have_count(1)
    page.reload(); _wait_for_notion_ready(page); _open(page)
    expect(thread.locator(".comment-reply")).to_contain_text("합성 답글")
    thread.locator("[data-comment-resolve]").click()
    expect(page.locator(".comment-thread")).to_have_count(0)
    page.locator("#commentFilterSelect").select_option("resolved")
    expect(thread.locator(".comment-reply")).to_have_count(1)
    thread.locator("[data-comment-resolve]").click()
    page.locator("#commentFilterSelect").select_option("open")
    expect(thread.locator(".comment-reply")).to_have_count(1)
    page.once("dialog", lambda dialog: dialog.accept())
    thread.locator("[data-comment-delete]").first.click()
    expect(page.locator(".comment-thread")).to_have_count(0)
    page.locator("#commentFilterSelect").select_option("all")
    expect(page.locator(".comment-thread")).to_have_count(0)


def test_ui_collab_001_deleted_reply_target_keeps_draft_without_root_fallback(document_page):
    from playwright.sync_api import expect
    page = document_page
    _open(page)
    root = _post(page, "삭제될 합성 댓글")
    page.locator(f'[data-comment-reply="{root["id"]}"]').click()
    page.locator("#commentInput").fill("답글 초안을 새 댓글로 바꾸면 안 됨")
    # Simulate another authorized owner action while this composer remains open.
    page.evaluate("""async id => {
      await api(`/api/notion/pages/${State.activePageId}/comments/${id}`, {method:'DELETE'});
      await loadComments();
    }""", root["id"])
    expect(page.locator("#commentSubmitBtn")).to_be_disabled()
    expect(page.locator("#commentInput")).to_have_value("답글 초안을 새 댓글로 바꾸면 안 됨")
    expect(page.locator("#commentReplyContext")).to_be_visible()
    expect(page.locator("#commentComposerNotice")).to_contain_text("답글 대상")
    assert page.evaluate("State.commentReplyParentId") == root["id"]


def test_ui_collab_001_resolved_reply_target_must_reopen(document_page):
    from playwright.sync_api import expect
    page = document_page
    _open(page)
    root = _post(page, "해결될 합성 댓글")
    page.locator(f'[data-comment-reply="{root["id"]}"]').click()
    page.locator("#commentInput").fill("해결 중인 답글 초안")
    page.locator(f'[data-comment-resolve="{root["id"]}"]').click()
    expect(page.locator("#commentSubmitBtn")).to_be_disabled()
    expect(page.locator("#commentInput")).to_have_value("해결 중인 답글 초안")
    page.locator("#commentFilterSelect").select_option("resolved")
    page.locator(f'[data-comment-resolve="{root["id"]}"]').click()
    expect(page.locator("#commentSubmitBtn")).to_be_enabled()
    page.locator("#commentFilterSelect").select_option("open")
    reply = _post(page, "다시 열린 원 댓글에 답글")
    assert reply["parent_id"] == root["id"]
