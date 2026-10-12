"""Opt-in browser checks for the isolated 10221 preview; never a production port.

Creates one clearly labeled feedback post and removes it through the author UI.
Existing Wiki bodies, source links, graphs and organization data are read only.
"""
import argparse
import io
import json
from pathlib import Path
import time
import zipfile

from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=10221, choices=[10221])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{args.port}"
    report = {"base": base, "checks": [], "page_errors": [], "http_errors": []}
    def passed(name):
        report["checks"].append(name)
        print(name, flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        contexts = []
        def login(user):
            context = browser.new_context(base_url=base, accept_downloads=True, viewport={"width": 1440, "height": 1000})
            contexts.append(context)
            response = context.request.post("/api/code/auth/login", data={"user_id": user, "password": args.password}, headers={"Origin": base})
            assert response.ok, f"login {user}: {response.status} {response.text()}"
            page = context.new_page()
            page.on("pageerror", lambda error: report["page_errors"].append(error.stack or str(error)))
            page.on("response", lambda response: report["http_errors"].append({"url": response.url, "status": response.status}) if response.status >= 400 else None)
            page.goto(base)
            expect(page.locator("#feedbackBoardBtn")).to_be_visible()
            return context, page, response.json()["csrf_token"]

        author, page, csrf = login("sbv-p03-u02")
        expect(page.locator("#newSessionBtn")).to_have_count(0)
        page.locator('[data-tab="knowledge"]').click()
        expect(page.locator("[data-wiki-export]")).to_be_visible(timeout=30000)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "horizontal page overflow"
        box = page.locator("#feedbackBoardBtn").bounding_box()
        assert box and box["x"] >= 0 and box["x"]+box["width"] <= 1440
        expect(page.locator('[data-system-doc="graph"]')).to_have_count(0)
        rows = page.locator("[data-canonical-item-id]")
        expect(rows.first).to_be_visible()
        report["visible_wiki_items"] = rows.count()
        first = rows.first
        with page.expect_download(timeout=30000) as event:
            first.get_by_role("button", name="MD ↓", exact=True).click()
        downloaded = event.value
        downloaded.save_as(args.output / "individual.md")
        assert (args.output / "individual.md").stat().st_size > 0
        passed("individual_markdown_download")
        with page.expect_download(timeout=60000) as event:
            page.locator("[data-wiki-export]").click()
        event.value.save_as(args.output / "workspace.zip")
        with zipfile.ZipFile(args.output / "workspace.zip") as z:
            assert z.testzip() is None
            report["export_document_count"] = len(json.loads(z.read("export.json"))["documents"])
            assert "index.md" in z.namelist() and "log.md" in z.namelist()
        passed("whole_workspace_zip_and_management_markdown")
        # Higher-level selection remains disabled in the UI; the same backend
        # export must still include every server-authorized inherited document.
        report["scope_exports"] = []
        spaces = author.request.get("/api/knowledge/v1/spaces").json()["items"]
        for space in spaces:
            if space["kind"] not in {"part", "team", "responsibility"}:
                continue
            expected_ids, cursor = set(), None
            while True:
                params = {"limit": 100}
                if cursor:
                    params["cursor"] = cursor
                listed = author.request.get(f"/api/knowledge/v1/spaces/{space['id']}/items", params=params)
                assert listed.ok, listed.text()
                batch = listed.json()
                expected_ids.update(item["id"] for item in batch["items"])
                cursor = batch["next_cursor"]
                if not cursor:
                    break
            began = time.perf_counter()
            exported = author.request.get(f"/api/knowledge/v2/spaces/{space['id']}/markdown-export", timeout=120000)
            assert exported.ok, exported.text()
            with zipfile.ZipFile(io.BytesIO(exported.body())) as z:
                manifest = json.loads(z.read("export.json"))
                assert {doc["item_id"] for doc in manifest["documents"]} == expected_ids
                assert z.testzip() is None and "index.md" in z.namelist() and "log.md" in z.namelist()
                assert all(z.read(doc["path"]).strip() for doc in manifest["documents"])
            report["scope_exports"].append({"kind": space["kind"], "documents": len(expected_ids),
                "seconds": round(time.perf_counter()-began, 3)})
        assert {row["kind"] for row in report["scope_exports"]} == {"part", "team", "responsibility"}
        passed("part_team_responsibility_exports_match_complete_authorized_lists")
        for path in ("/api/code/feedback", f"/api/knowledge/v2/spaces/{spaces[0]['id']}/source-availability"):
            assert author.request.post(path, data={}, headers={"Origin": base}).status == 403
        passed("new_post_and_wiki_endpoints_keep_csrf_enforcement")
        original = first.locator("[data-original-download]")
        expect(original).to_be_enabled(timeout=15000)
        with page.expect_download(timeout=30000) as event:
            original.click()
        original_file = args.output / event.value.suggested_filename
        event.value.save_as(original_file)
        assert original_file.stat().st_size > 0
        passed("original_download_from_knowledge_list")
        page.screenshot(path=str(args.output / "knowledge-downloads.png"), full_page=True)

        # The original-link dialog opens without changing the item or attaching a fixture.
        first.get_by_role("button", name="원본 연결", exact=True).click()
        expect(page.locator("#modalTitle")).to_contain_text("원본 연결")
        expect(page.get_by_label("연결할 원본 파일")).to_be_visible()
        page.locator("#modalClose").click()
        passed("original_link_dialog")

        title = f"[자동 검증] 게시판 첨부·수정·삭제 {time.time_ns()}"
        page.locator("#feedbackBoardBtn").click()
        page.locator("#modalBody").get_by_role("button", name="작성", exact=True).click()
        page.get_by_label("요청 제목", exact=True).fill(title)
        page.get_by_label("요청 내용", exact=True).fill("이미지 붙여넣기와 파일 드롭 검증\n계정 간 공유 확인")
        author.grant_permissions(["clipboard-read", "clipboard-write"], origin=base)
        page.evaluate("""async () => {
          const canvas=document.createElement('canvas');canvas.width=120;canvas.height=60;
          const ctx=canvas.getContext('2d');ctx.fillStyle='#287e9e';ctx.fillRect(0,0,120,60);
          ctx.fillStyle='white';ctx.font='18px sans-serif';ctx.fillText('Paste test',12,35);
          const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
          await navigator.clipboard.write([new ClipboardItem({'image/png':blob})]);
        }""")
        page.get_by_label("요청 내용", exact=True).click()
        page.keyboard.press("Control+V")
        expect(page.locator(".feedback-asset")).to_have_count(1)
        pasted_name = page.locator(".feedback-asset img").get_attribute("alt")
        page.locator(".feedback-drop").evaluate("""el => {
          const data=new DataTransfer();data.items.add(new File(['Dropped file contents'],'dropped.txt',{type:'text/plain'}));
          el.dispatchEvent(new DragEvent('drop',{dataTransfer:data,bubbles:true,cancelable:true}));
        }""")
        expect(page.locator(".feedback-asset")).to_have_count(2)
        page.screenshot(path=str(args.output / "feedback-compose.png"), full_page=True)
        page.route("**/api/code/feedback", lambda route: route.fulfill(
            status=503, content_type="application/json", body='{"error":{"message":"Simulated save failure"}}'), times=1)
        page.locator("#modalFoot").get_by_role("button", name="작성 완료").click()
        expect(page.get_by_text("Simulated save failure", exact=True)).to_be_visible()
        expect(page.get_by_label("요청 제목", exact=True)).to_have_value(title)
        expect(page.locator(".feedback-asset")).to_have_count(2)
        passed("failed_submission_keeps_draft_and_attachments")
        page.locator("#modalFoot").get_by_role("button", name="작성 완료").click()
        expect(page.locator("#modalTitle")).to_have_text("버그·개선 요청", timeout=30000)
        page.locator(".feedback-post-row").filter(has_text=title).click()
        expect(page.locator(".feedback-content")).to_contain_text("계정 간 공유 확인")
        expect(page.locator(".feedback-asset")).to_have_count(2)
        passed("paste_and_drop_post_create_returns_to_board")
        post = next(x for x in author.request.get("/api/code/feedback").json()["items"] if x["title"] == title)
        report["test_post_id"] = post["id"]
        with page.expect_download(timeout=30000) as event:
            page.get_by_role("button", name="dropped.txt ↓", exact=True).click()
        event.value.save_as(args.output / "dropped.txt")
        assert (args.output / "dropped.txt").read_bytes() == b"Dropped file contents"
        passed("feedback_attachment_bytes_match")

        other, other_page, other_csrf = login("sbv-p03-u01")
        other_page.locator("#feedbackBoardBtn").click()
        other_page.locator(".feedback-post-row").filter(has_text=title).click()
        expect(other_page.locator("#modalBody").get_by_role("button", name="수정", exact=True)).to_have_count(0)
        expect(other_page.locator("#modalBody").get_by_role("button", name="삭제", exact=True)).to_have_count(0)
        unauthorized = other.request.delete(f"/api/code/feedback/{post['id']}", data={"operation_id": "12345678-1234-4234-8234-123456789abc", "expected_version": 1}, headers={"Origin": base, "X-CSRF-Token": other_csrf})
        assert unauthorized.status == 403, unauthorized.text()
        passed("other_account_can_read_but_cannot_edit_or_delete")
        other_page.screenshot(path=str(args.output / "feedback-other-user.png"), full_page=True)

        # Both roles can return without closing the modal. The footer stays
        # available even when a long post has scrolled its top button away.
        for view, footer, name in ((page, False, "author_top_back_returns_to_board"),
                                  (other_page, True, "reader_footer_back_returns_to_board")):
            parent = view.locator("#modalFoot" if footer else ".feedback-detail")
            expect(parent.get_by_role("button", name="← 게시판으로", exact=True)).to_be_visible()
            parent.get_by_role("button", name="← 게시판으로", exact=True).click()
            expect(view.locator("#modalTitle")).to_have_text("버그·개선 요청")
            expect(view.locator("#modalBg")).to_be_visible()
            view.locator(".feedback-post-row").filter(has_text=title).click()
            expect(view.locator(".feedback-content")).to_contain_text("계정 간 공유 확인")
            passed(name)

        page.locator("#modalFoot").get_by_role("button", name="← 게시판으로", exact=True).click()
        page.get_by_label("게시글 검색어").fill("SBV-P03-U02")
        page.get_by_label("검색 범위").select_option("requester")
        expect(page.locator(".feedback-post-row").filter(has_text=title)).to_be_visible()
        page.wait_for_function("document.querySelectorAll('.feedback-row-author').length > 0 && [...document.querySelectorAll('.feedback-row-author')].every(el=>el.textContent.includes('sbv-p03-u02'))")
        page.screenshot(path=str(args.output / "feedback-search.png"), full_page=True)
        page.locator(".feedback-post-row").filter(has_text=title).click()
        expect(page.locator(".feedback-content")).to_contain_text("계정 간 공유 확인")

        other_page.locator("#modalFoot").get_by_role("button", name="← 게시판으로", exact=True).click()
        other_page.get_by_label("게시글 검색어").fill(title.split()[-1])
        other_page.get_by_label("게시글 검색어").press("Enter")
        expect(other_page.locator(".feedback-post-row")).to_have_count(1)
        other_page.get_by_label("내가 쓴 글만").check()
        expect(other_page.locator(".feedback-page-info")).to_contain_text("1페이지 · 0개")
        other_page.get_by_label("내가 쓴 글만").uncheck()
        expect(other_page.locator(".feedback-post-row")).to_have_count(1)
        other_page.locator(".feedback-post-row").filter(has_text=title).click()
        expect(other_page.locator(".feedback-content")).to_contain_text("계정 간 공유 확인")
        passed("live_requester_search_and_authenticated_mine_filter")

        page.locator("#modalBody").get_by_role("button", name="수정", exact=True).click()
        page.get_by_label("요청 내용", exact=True).fill("작성자가 수정한 내용")
        page.get_by_role("button", name=f"{pasted_name} · 제거", exact=True).click()
        page.locator("#modalFoot").get_by_role("button", name="작성 완료").click()
        expect(page.locator(".feedback-content")).to_have_text("작성자가 수정한 내용", timeout=15000)
        expect(page.locator(".feedback-asset")).to_have_count(1)
        removed = next(a for a in post["attachments"] if a["filename"] == pasted_name)
        assert other.request.get(f"/api/code/feedback/{post['id']}/attachments/{removed['id']}").status == 404
        passed("author_edit_and_removed_attachment_access_revocation")
        page.reload()
        page.locator("#feedbackBoardBtn").click()
        page.locator(".feedback-post-row").filter(has_text=title).click()
        expect(page.locator(".feedback-content")).to_have_text("작성자가 수정한 내용")
        passed("feedback_survives_page_reload")
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator("#modalBody").get_by_role("button", name="삭제", exact=True).click()
        expect(page.locator("#modalTitle")).to_have_text("버그·개선 요청")
        expect(page.locator(".feedback-post-row").filter(has_text=title)).to_have_count(0)
        assert other.request.get(f"/api/code/feedback/{post['id']}").status == 404
        passed("author_delete_hides_post_for_everyone")
        (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        assert not report["page_errors"], report["page_errors"]
        for context in contexts:
            context.close()
        browser.close()
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
