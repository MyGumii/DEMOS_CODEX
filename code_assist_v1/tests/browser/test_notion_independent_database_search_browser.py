"""UI-R10: staged database search opens a database without its owner page."""
from __future__ import annotations

import base64
import re
import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app,
)


@pytest.mark.integration
def test_ui_r10_independent_database_search_preview_and_direct_view(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False, navigation_ui=True,
                                    gateway_enabled=False) as (
            origin, password, _authority, workspace_id, page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                context = browser.new_context(locale="ko-KR", viewport={"width": 1280, "height": 800})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                projection = {
                    "contract": "code_assist.notion.database_sharing.v1",
                    "workspace_id": workspace_id, "database_id": "database-e1",
                    "role": "view", "workspace_epoch": 1, "document_epoch": 1,
                    "database_epoch": 1, "document_sequence": 1,
                    "database": {
                        "id": "database-e1", "title": "Private Table", "version": 1,
                        "properties": [{"id": "title", "name": "Name", "type": "title",
                                        "version": 1, "config": {}},
                                       {"id": "amount", "name": "Amount", "type": "number",
                                        "version": 1, "config": {}},
                                       {"id": "checked", "name": "Checked", "type": "checkbox",
                                        "version": 1, "config": {}},
                                       {"id": "date", "name": "Date", "type": "date",
                                        "version": 1, "config": {}},
                                       {"id": "related", "name": "Related", "type": "relation",
                                        "version": 1, "config": {}}],
                        "rows": [{"id": "row-e1", "version": 1,
                                  "cells": {"title": "Visible row", "amount": 12,
                                            "checked": False,
                                            "date": {"start": "2026-09-15"},
                                            "related": []}, "computed": {},
                                  "cell_versions": {"title": 1, "amount": 1,
                                                    "checked": 1,
                                                    "date": 1, "related": 1},
                                      "body": {"version": 1, "blocks": [
                                          {"id": "body-e1", "type": "paragraph",
                                           "content": "Private body in shared database",
                                           "props": {}},
                                          {"id": "body-image-e1", "type": "image",
                                           "content": "Private image", "props": {
                                               "file_id": "file-e1", "name": "image.png"}}]}}],
                        "views": [
                            {"id": "view-table", "name": "Table", "type": "table",
                             "version": 1, "config": {}},
                            {"id": "view-list", "name": "List", "type": "list",
                             "version": 1, "config": {}},
                            {"id": "view-calendar", "name": "Calendar", "type": "calendar",
                             "version": 1, "config": {}},
                            {"id": "view-timeline", "name": "Timeline", "type": "timeline",
                             "version": 1, "config": {}},
                        ],
                    },
                }
                page.route("**/database-search?*", lambda route: route.fulfill(
                    status=200, json={"status": "database_search_loaded",
                                      "contract": "code_assist.notion.database_search.v1",
                                      "workspace_id": workspace_id, "query": "Private Table",
                                      "items": [{"id": "database-e1", "database_id": "database-e1",
                                                 "kind": "database", "title": "Private Table",
                                                 "role": "view", "updated_at": 1}],
                                      "count": 1, "has_more": False},
                ))
                database_access = {"allowed": True}
                def database_response(route):
                    if database_access["allowed"]:
                        route.fulfill(status=200, json=projection)
                    else:
                        route.fulfill(status=404, json={"error": "not found"})
                page.route("**/databases/database-e1", database_response)
                image=base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
                page.route("**/databases/database-e1/files/file-e1", lambda route: route.fulfill(
                    status=200, body=image, content_type="image/png"))
                comment_anchor={"kind":"database_row","database_id":"database-e1","row_id":"row-e1"}
                comment_items=[{"id":"comment-existing", "author_subject_id":"owner",
                                "content":"Visible item comment","anchor":comment_anchor,
                                "parent_comment_id":None,"row_version":1,
                                "reactions":{},"resolved":False,"created_at":1}]
                comment_posts=[]
                def comment_response(route):
                    method=route.request.method
                    path=route.request.url.split("?",1)[0]
                    if method!="GET":
                        posted=route.request.post_data_json
                        comment_posts.append(posted)
                        projection["document_sequence"]+=1
                        comment_id=posted.get("comment_id") or path.split("/comments/",1)[1].split("/",1)[0]
                        result={"contract":projection["contract"],"workspace_id":workspace_id,
                            "database_id":"database-e1","row_id":"row-e1",
                            "comment_id":comment_id,"operation_id":posted["operation_id"],
                            "document_sequence":projection["document_sequence"],
                            "workspace_epoch":projection["workspace_epoch"],
                            "document_epoch":projection["document_epoch"],
                            "database_epoch":projection["database_epoch"],
                            "replayed":False}
                        if method=="POST" and path.endswith("/comments"):
                            comment_items.append({"id":comment_id,"author_subject_id":"owner",
                                "content":posted["content"],"anchor":comment_anchor,
                                "parent_comment_id":posted.get("parent_comment_id") or None,
                                "row_version":1,"reactions":{},"resolved":False,"created_at":2})
                            route.fulfill(status=201,json=result)
                        else:
                            item=next(item for item in comment_items if item["id"]==comment_id)
                            item["row_version"]+=1
                            result["row_version"]=item["row_version"]
                            if method=="DELETE":
                                comment_items.remove(item);result["deleted"]=True
                            elif path.endswith("/reactions"):
                                emoji=posted["emoji"]
                                members=item["reactions"].get(emoji,[])
                                added="owner" not in members
                                item["reactions"]={emoji:["owner"]} if added else {}
                                result.update(emoji=emoji,added=added,reactions=item["reactions"])
                            elif "content" in posted:item["content"]=posted["content"]
                            else:item["resolved"]=posted["resolved"];result["resolved"]=posted["resolved"]
                            route.fulfill(status=200,json=result)
                    else:
                        route.fulfill(status=200,json={
                            "contract":projection["contract"],"workspace_id":workspace_id,
                            "database_id":"database-e1","row_id":"row-e1",
                            "item_target":comment_anchor,
                            "role":projection["role"],"workspace_epoch":projection["workspace_epoch"],
                            "document_epoch":projection["document_epoch"],
                            "database_epoch":projection["database_epoch"],
                            "document_sequence":projection["document_sequence"],
                            "items":comment_items,"count":len(comment_items)})
                page.route("**/databases/database-e1/rows/row-e1/comments",comment_response)
                page.route("**/databases/database-e1/rows/row-e1/comments/**",comment_response)
                page.goto(origin + "/workspace", wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#homeView")).to_be_visible(timeout=30_000)
                expect(page.locator("body")).to_have_attribute("data-notion-ready", "true", timeout=30_000)
                page.evaluate("State.navigation.independentDatabaseSearch=true;openSearchOverlay('Private Table')")
                expect(page.locator("#searchResults .search-result-row").filter(
                    has_text="Private Table")).to_be_visible(timeout=20_000)
                expect(page.locator("#searchPreview")).to_contain_text("1개 항목")
                page.locator("#previewOpenBtn").click()
                expect(page.locator("#independentDatabaseView")).to_be_visible(timeout=20_000)
                expect(page.locator("#independentDatabaseView")).to_contain_text("Visible row")
                header = page.locator("#independentDatabaseView .dynamic-table-grid .table-header")
                expect(header.locator(".database-column-icon")).to_have_count(5)
                assert header.locator(".database-column-icon").first.inner_text() == "Aa"
                assert header.locator(".database-column-icon").nth(1).inner_text() == "#"
                expect(header.locator(".database-column-icon").nth(3).locator("svg"))\
                    .to_have_count(1)
                assert "18.5 18 6" in (header.locator(".database-column-icon")
                    .nth(4).locator("path").get_attribute("d") or "")
                assert header.evaluate("element => getComputedStyle(element).backgroundColor")\
                    == "rgb(255, 255, 255)"
                assert page.url.endswith(f"/database/{workspace_id}/database-e1")
                assert "private-owner-page" not in page.locator("#independentDatabaseView").inner_text()
                # The last page's role must not grant this database's schema controls.
                page.evaluate("State.pageData={page:{iam_role:'full_access'},blocks:[],databases:[]};renderIndependentDatabase()")
                expect(page.locator("#independentDatabaseView [data-db-property]")).to_have_count(0)
                expect(page.locator("#independentDatabaseView [data-db-add-property]")).to_have_count(0)
                expect(page.locator("#independentDatabaseView [data-database-add]")).to_have_count(0)
                page.locator("#independentDatabaseView [data-db-view-id='view-calendar']").click()
                month_before=page.locator("#independentDatabaseView .calendar-nav strong").inner_text()
                page.locator("#independentDatabaseView [data-calendar-nav='1']").click()
                assert page.locator("#independentDatabaseView .calendar-nav strong").inner_text()!=month_before
                expect(page.locator("#independentDatabaseView [data-calendar-add]")).to_have_count(0)
                page.locator("#independentDatabaseView [data-db-view-id='view-timeline']").click()
                timeline_before=page.locator("#independentDatabaseView .timeline-controls strong").inner_text()
                page.locator("#independentDatabaseView [data-timeline-nav='1']").click()
                assert page.locator("#independentDatabaseView .timeline-controls strong").inner_text()!=timeline_before
                page.locator("#independentDatabaseView [data-db-view-id='view-list']").click()
                expect(page.locator("#independentDatabaseView .list-database")).to_be_visible()
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                expect(page.locator("#modalBody")).to_contain_text("Visible row")
                expect(page.locator("#modalBody [data-independent-item-body]")).to_contain_text(
                    "Private body in shared database")
                expect(page.locator("#modalBody [data-independent-item-comments]")).to_contain_text(
                    "Visible item comment")
                expect(page.locator("#modalBody [data-independent-item-comments] textarea")).to_be_disabled()
                expect(page.locator("#modalBody [data-body-text='body-e1']")).to_have_attribute(
                    "contenteditable", "false")
                assert page.locator("#modalBody [data-independent-item-body] img").get_attribute(
                    "src") == (f"/api/notion/workspaces/{workspace_id}/databases/"
                               "database-e1/files/file-e1")
                page.locator("#modalActions button").last.click()
                assert page.evaluate("IndependentDatabaseItemBodyEditor === null")
                page.evaluate("""() => {
                  State.independentDatabase.data.role='edit_content';
                  const row=State.independentDatabase.data.database.rows[0];
                  row.body_unavailable=true;
                  row.body={blocks:[{id:'body-safe',type:'paragraph',
                    content:'Visible shared note',props:{}}]};
                  renderIndependentDatabase();
                }""")
                page.locator("#independentDatabaseView [data-table-row='0']").first.click()
                expect(page.locator("#modalBody [data-independent-body-unavailable]"))\
                    .to_contain_text("표시하거나 편집할 수 없습니다")
                expect(page.locator("#modalBody [data-independent-item-body]"))\
                    .to_contain_text("Visible shared note")
                expect(page.locator("#modalBody [data-body-text='body-safe']"))\
                    .to_have_attribute("contenteditable", "false")
                page.locator("#modalActions button").last.click()
                projection["role"] = "edit_content"
                posted = []
                def cell_response(route):
                    request = route.request.post_data_json
                    posted.append(request)
                    if len(posted) == 1:
                        projection["document_sequence"] = request["expected_document_sequence"] + 1
                        projection["database"]["rows"][0]["cells"]["title"] = request["cells"][0]["value"]
                        projection["database"]["rows"][0]["cell_versions"]["title"] = 2
                        route.abort("failed")  # The write committed but its response was lost.
                    else:
                        cell=request["cells"][0]
                        if len(posted)>2:
                            projection["document_sequence"] = request["expected_document_sequence"] + 1
                            projection["database"]["rows"][0]["cells"][cell["property_id"]] = cell["value"]
                            projection["database"]["rows"][0]["cell_versions"][cell["property_id"]] = cell["expected_target_version"] + 1
                        route.fulfill(status=201, json={
                            "contract": projection["contract"], "workspace_id": workspace_id,
                            "database_id": "database-e1", "operation_id": request["operation_id"],
                            "document_sequence": request["expected_document_sequence"] + 1,
                            "cells": [{"row_id": cell["row_id"],
                                       "property_id": cell["property_id"],
                                       "target_version": cell["expected_target_version"]+1}],
                            "replayed": len(posted)==2,
                        })
                page.route("**/databases/database-e1/cells", cell_response)
                body_posted=[]
                def body_response(route):
                    request=route.request.post_data_json
                    body_posted.append(request)
                    if len(body_posted)<=2:
                        if len(body_posted)==1:
                            projection["document_sequence"] = request["expected_document_sequence"]+1
                            projection["database"]["rows"][0]["version"] = request["expected_target_version"]+1
                            projection["database"]["rows"][0]["body"] = {"blocks": request["blocks"]}
                        route.abort("failed")
                    else:
                        replayed=request["operation_id"]==body_posted[0]["operation_id"]
                        if not replayed:
                            projection["document_sequence"]=request["expected_document_sequence"]+1
                            projection["database"]["rows"][0]["version"]=request["expected_target_version"]+1
                            projection["database"]["rows"][0]["body"]={"blocks":request["blocks"]}
                        route.fulfill(status=201,json={
                            "contract":projection["contract"],
                            "workspace_id":workspace_id,"database_id":"database-e1",
                            "row_id":"row-e1","operation_id":request["operation_id"],
                            "target_version":request["expected_target_version"]+1,
                            "document_sequence":request["expected_document_sequence"]+1,
                            "body":{"blocks":request["blocks"]},"replayed":replayed,
                        })
                page.route("**/databases/database-e1/rows/row-e1/body",body_response)
                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_contain_text(
                    "Visible row", timeout=30_000)
                page.locator("#independentDatabaseView [data-table-row='0']").first.click()
                expect(page.locator("#modalBody [data-independent-item-comments] textarea")).to_be_enabled()
                page.evaluate("State.users.push({id:'db-mention-person',display_name:'Mention Person'},{id:'db-no-access',display_name:'No Access'})")
                mention_checks=[]
                def mention_candidates(route):
                    mention_checks.append(route.request.post_data_json)
                    route.fulfill(status=200,json={
                        "contract":projection["contract"],
                        "workspace_id":workspace_id,"database_id":"database-e1",
                        "subject_ids":["db-mention-person"],
                        "workspace_epoch":1,"document_epoch":1,"database_epoch":1,
                    })
                page.route("**/databases/database-e1/mention-candidates",mention_candidates)
                mention_button=page.locator(
                    "#modalBody [data-independent-item-comments] [data-comment-action='mention']")
                expect(mention_button).to_be_visible()
                mention_button.click()
                expect(page.locator("#modalBody .item-comment-mentions [data-person='db-no-access']")).to_have_count(0)
                page.locator("#modalBody .item-comment-mentions [data-person='db-mention-person']").click()
                assert mention_checks and "db-no-access" in mention_checks[0]["subject_ids"]
                comment_uploads=[]
                def comment_upload_response(route):
                    url=route.request.url
                    if url.endswith("/uploads") and route.request.method=="POST":
                        comment_uploads.append(route.request.post_data_json)
                        route.fulfill(status=201,json={"database_id":"database-e1",
                            "session_id":"comment-upload-session","chunk_size":1048576,
                            "state":"prepared"})
                    elif url.endswith("/uploads/comment-upload-session"):
                        route.fulfill(status=200,json={"database_id":"database-e1",
                            "state":"prepared","missing_chunks":[0]})
                    elif url.endswith("/chunks/0"):
                        route.fulfill(status=200,json={"received":True})
                    elif url.endswith("/finalize"):
                        projection["document_sequence"]+=1
                        route.fulfill(status=200,json={"file":{
                            "contract":"code_assist.notion.collaborative_file_authority.v1",
                            "database_id":"database-e1","id":"file-upload-comment",
                            "file_id":"file-upload-comment","state":"active",
                            "document_sequence":projection["document_sequence"],
                            "original_name":"comment.png","mime_type":"image/png",
                            "size_bytes":len(image)}})
                    else:
                        route.fulfill(status=404,json={"error":"unexpected comment upload"})
                page.route("**/databases/database-e1/files/uploads**",comment_upload_response)
                page.route("**/databases/database-e1/files/file-upload-comment",
                           lambda route:route.fulfill(status=200,body=image,
                                                      content_type="image/png"))
                attach=page.locator("#modalBody [data-independent-item-comments] [data-comment-action='attach']")
                expect(attach).to_be_visible()
                page.locator("#modalBody [data-independent-item-comments] [data-comment-file-input]")\
                    .set_input_files({"name":"comment.png","mimeType":"image/png","buffer":image})
                comment_input=page.locator("#modalBody [data-independent-item-comments] textarea")
                expect(comment_input).to_have_value(re.compile("file-upload-comment"),timeout=20_000)
                attachment_text=comment_input.input_value()
                comment_input.fill("New direct comment @db-mention-person\n"+attachment_text)
                page.locator("#modalBody [data-independent-item-comments] [data-comment-action='send']").click()
                expect(page.locator("#modalBody [data-independent-item-comments]")).to_contain_text(
                    "New direct comment")
                assert len(comment_posts)==1
                assert len(comment_uploads)==1
                assert comment_posts[0]["mention_subject_ids"]==["db-mention-person"]
                assert "file-upload-comment" in comment_posts[0]["content"]
                assert "document_id" not in str(comment_posts[0])
                new_id=comment_posts[0]["comment_id"]
                direct=page.locator(f"#modalBody [data-item-comment='{new_id}']")
                expect(direct.locator("a[href$='file-upload-comment']")).to_be_visible()
                direct.locator("[data-comment-action='menu']").click()
                direct.locator("[data-comment-action='reply']").click()
                page.locator("#modalBody [data-independent-item-comments] textarea").fill("Direct reply")
                page.locator("#modalBody [data-independent-item-comments] [data-comment-action='send']").click()
                expect(page.locator("#modalBody [data-independent-item-comments]")).to_contain_text(
                    "Direct reply")
                assert comment_posts[1]["parent_comment_id"]==new_id
                reply_id=comment_posts[1]["comment_id"]
                direct.locator("[data-comment-action='menu']").click()
                direct.locator("[data-comment-action='edit']").click()
                page.locator("#modalBody [data-independent-item-comments] textarea").fill("Edited direct comment")
                page.locator("#modalBody [data-independent-item-comments] [data-comment-action='send']").click()
                expect(direct).to_contain_text("Edited direct comment")
                assert comment_posts[2]["mention_subject_ids"]==[]
                direct.locator("[data-comment-action='menu']").click()
                direct.locator("[data-comment-action='react']").click()
                expect(direct.locator("[data-comment-action='reaction']")).to_have_attribute(
                    "aria-pressed","true")
                direct.locator("[data-comment-action='menu']").click()
                direct.locator("[data-comment-action='resolve']").click()
                expect(direct).to_have_count(0)
                page.locator("#modalBody [data-independent-item-comments] [data-comment-action='resolved']").click()
                expect(direct).to_contain_text("Edited direct comment")
                reply_item=page.locator(f"#modalBody [data-item-comment='{reply_id}']")
                reply_item.locator("[data-comment-action='menu']").click()
                reply_item.locator("[data-comment-action='delete']").click()
                reply_item.locator("[data-comment-action='delete']").click()
                expect(reply_item).to_have_count(0)
                assert all("document_id" not in str(item) for item in comment_posts)
                page.locator("#modalActions button").last.click()
                cell=page.locator("#independentDatabaseView [data-independent-edit-cell][data-grid-column='title']")
                cell.click()
                editor=cell.locator("textarea[data-db-field='title']")
                expect(editor).to_be_enabled()
                editor.fill("Cancelled draft")
                editor.press("Escape")
                expect(editor).to_have_value("Visible row")
                assert posted == []
                editor.fill("Directly edited row")
                editor.press("Enter")
                expect(page.locator("#independentDatabaseCellStatus")).to_contain_text(
                    "보관", timeout=20_000)
                assert len(posted) == 1
                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_contain_text(
                    "Directly edited row", timeout=30_000)
                expect(page.locator("#independentDatabaseCellStatus")).to_be_hidden(timeout=30_000)
                assert len(posted) == 2
                assert posted[0] == posted[1]
                assert "document_id" not in str(posted[0])
                amount=page.locator("#independentDatabaseView [data-independent-edit-cell][data-grid-column='amount']")
                amount.click()
                amount.locator("input[data-db-field='amount']").fill("27")
                with page.expect_request("**/databases/database-e1/cells"):
                    amount.locator("input[data-db-field='amount']").press("Enter")
                expect(page.locator("#independentDatabaseView [data-grid-column='amount'] input")).to_have_value("27")
                expect(page.locator("#independentDatabaseCellStatus")).to_be_hidden(timeout=20_000)
                assert posted[2]["cells"][0]["value"] == 27
                checked=page.locator("#independentDatabaseView [data-independent-edit-cell][data-grid-column='checked']")
                with page.expect_request("**/databases/database-e1/cells"):
                    checked.click()
                expect(page.locator("#independentDatabaseView [data-grid-column='checked'] input")).to_be_checked()
                assert posted[3]["cells"][0]["value"] is True
                page.locator("#independentDatabaseView [data-table-row='0']").first.click()
                body_editor=page.locator("#modalBody [data-body-text='body-e1']")
                expect(body_editor).to_have_attribute("contenteditable","true")
                with page.expect_request("**/databases/database-e1/rows/row-e1/body"):
                    body_editor.fill("Edited direct body")
                expect(page.locator("#modalBody .item-body-notice")).to_contain_text(
                    "Failed to fetch",timeout=20_000)
                assert len(body_posted)==1
                assert body_posted[0]["expected_database_epoch"]==projection["database_epoch"]
                assert "document_id" not in str(body_posted[0])
                assert page.evaluate("goHome(true)") is False
                expect(page.locator("#independentDatabaseView")).to_be_visible()
                expect(page.locator("#modalBg")).to_be_visible()
                assert len(body_posted)==2 and body_posted[0]==body_posted[1]
                page.reload(wait_until="domcontentloaded")
                page.locator("#independentDatabaseView [data-table-row='0']").first.click()
                expect(page.locator("#modalBody .item-body-notice")).to_contain_text(
                    "보관한 본문 저장",timeout=20_000)
                page.locator("#modalBody .item-body-notice button").filter(
                    has_text="다시 저장").click()
                expect(page.locator("#modalBody [data-body-text='body-e1']")).to_contain_text(
                    "Edited direct body",timeout=20_000)
                expect(page.locator("#modalBody .item-body-notice")).to_be_hidden(timeout=20_000)
                assert len(body_posted)==3 and body_posted[0]==body_posted[1]==body_posted[2]
                uploads=[]
                def upload_response(route):
                    url=route.request.url
                    method=route.request.method
                    if url.endswith("/uploads") and method=="POST":
                        uploads.append(route.request.post_data_json)
                        route.fulfill(status=201,json={"database_id":"database-e1",
                            "session_id":"body-upload-session","chunk_size":1048576,
                            "state":"prepared"})
                    elif url.endswith("/uploads/body-upload-session") and method=="GET":
                        route.fulfill(status=200,json={"database_id":"database-e1",
                            "state":"prepared","missing_chunks":[0]})
                    elif url.endswith("/chunks/0") and method=="PUT":
                        assert route.request.post_data_buffer==image
                        route.fulfill(status=200,json={"received":True})
                    elif url.endswith("/finalize") and method=="POST":
                        projection["document_sequence"]+=1
                        route.fulfill(status=200,json={"file":{
                            "contract":"code_assist.notion.collaborative_file_authority.v1",
                            "database_id":"database-e1","id":"file-upload-body",
                            "file_id":"file-upload-body","state":"active",
                            "document_sequence":projection["document_sequence"],
                            "original_name":"body.png","mime_type":"image/png",
                            "size_bytes":len(image),
                        }})
                    else:
                        route.fulfill(status=404,json={"error":"unexpected upload request"})
                page.route("**/databases/database-e1/files/uploads**",upload_response)
                page.route("**/databases/database-e1/files/file-upload-body",
                           lambda route:route.fulfill(status=200,body=image,
                                                      content_type="image/png"))
                page.locator("#modalBody [data-body-block='body-e1']").hover()
                page.locator("#modalBody [data-body-add='body-e1']").click()
                page.locator("#modalBody [data-body-type='image']").click()
                with page.expect_response(lambda response:
                        response.request.url.endswith("/rows/row-e1/body") and
                        any(block.get("props",{}).get("file_id")=="file-upload-body"
                            for block in response.request.post_data_json["blocks"]),
                        timeout=30_000):
                    page.locator("#modalBody .item-resource-picker input[type=file]").set_input_files(
                        {"name":"body.png","mimeType":"image/png","buffer":image})
                assert len(uploads)==1
                assert uploads[0]["expected_database_epoch"]==projection["database_epoch"]
                assert uploads[0]["expected_document_sequence"]<projection["document_sequence"]
                assert all("document_id" not in str(item) for item in body_posted)
                expect(page.locator("#modalBody [data-independent-item-body] img[src$='file-upload-body']"))\
                    .to_have_count(1,timeout=20_000)
                page.locator("#modalActions button").last.click()
                projection["role"] = "view"
                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView [data-independent-edit-cell]")).to_have_count(0)
                page.locator("#independentDatabaseView [data-table-row='0']").first.click()
                expect(page.locator("#modalBody [data-independent-item-body]")).to_contain_text(
                    "Edited direct body")
                expect(page.locator("#modalBody [data-independent-item-comments] "
                                    "[data-comment-action='attach']")).to_be_hidden()
                expect(page.locator("#modalBody [data-independent-item-body] img[src$='file-upload-body']"))\
                    .to_have_count(1)
                expect(page.locator("#modalBody [data-independent-item-body] [data-item-resource]"))\
                    .to_have_count(0)
                page.go_back(wait_until="domcontentloaded")
                expect(page.locator("#homeView")).to_be_visible(timeout=20_000)
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                expect(page.locator("#modalBg")).to_be_hidden()
                assert page.evaluate("IndependentDatabaseItemBodyEditor === null")
                inbox_reads=[]
                direct_note={
                    "id":"direct-alert","kind":"comment_replied",
                    "workspace_id":workspace_id,"database_id":"database-e1",
                    "database_title":"Private Table","row_id":"row-e1",
                    "comment_id":"comment-existing","actor_subject_id":"other",
                    "item_target":comment_anchor,"workspace_epoch":1,
                    "document_epoch":1,"database_epoch":1,"notification_epoch":1,
                    "read":False,"created_at":1790000000,
                }
                def inbox_response(route):
                    if route.request.method=="GET":
                        route.fulfill(status=200,json={"items":[direct_note],
                            "count":1,"unread_count":1})
                    else:
                        inbox_reads.append(route.request.post_data_json)
                        route.fulfill(status=200,json={"notification_id":"direct-alert",
                            "database_id":"database-e1","row_id":"row-e1","read":True})
                page.route(f"**/api/notion/workspaces/{workspace_id}/inbox*",inbox_response)
                page.route(f"**/api/notion/workspaces/{workspace_id}/inbox/direct-alert",
                           inbox_response)
                page.evaluate("State.navigation.collaborativeSharing=true;openInboxOverlay()")
                notice=page.locator("#inboxList [data-inbox-item='direct-alert']")
                expect(notice).to_contain_text("Private Table",timeout=20_000)
                with page.expect_response(lambda response: response.request.url.endswith(
                        f"/api/notion/workspaces/{workspace_id}/inbox/direct-alert")) as read_response:
                    notice.click()
                expect(page.locator("#independentDatabaseView")).to_be_visible(timeout=20_000)
                expect(page.locator("#modalBody [data-independent-item-comments]")).to_contain_text(
                    "Visible item comment",timeout=20_000)
                assert read_response.value.request.method=="PATCH"
                assert read_response.value.status==200, read_response.value.text()
                assert len(inbox_reads)==1, read_response.value.text()
                assert inbox_reads[0]["expected_database_epoch"]==1
                assert "document_id" not in str(inbox_reads[0])
                assert page.evaluate("State.sidebarMode") == "inbox"
                database_access["allowed"] = False
                page.goto(origin + f"/database/{workspace_id}/database-e1",
                          wait_until="domcontentloaded")
                expect(page).to_have_url(origin + "/workspace", timeout=30_000)
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                assert errors == []
            finally:
                browser.close()


