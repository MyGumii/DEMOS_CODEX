"""An exact 2429 synthetic catalog reaches the independent database UI."""
import os
import time

import pytest

from code_assist_v1.notion_collaboration import database_access, database_comments
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app,
)


@pytest.mark.integration
def test_ui_r10_exact_2429_database_grant_full_app_browser(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(
        tmp_path, locked=False, gateway_enabled=False, navigation_ui=True,
        owned_database_copy=True, database_activity=True,
        owned_database_trash=True, relation_pair=True, custom_emoji=True,
        item_page_layout=True, template_default=True,
        staged_database_sharing=True,
    ) as (origin, password, authority, workspace_id, page_id):
        with pytest.raises(NotFoundOrDenied):
            authority.read_document("db-viewer", "page-e1")
        owner_projection = database_access.read_projection(
            authority, "owner", workspace_id, "database-e1")
        updated_row = owner_projection["database"]["rows"][0]
        status_property = next(item["id"] for item in
                               owner_projection["database"]["properties"]
                               if item["name"] == "Status")
        database_access.change_cells(
            authority, "owner", workspace_id, "database-e1",
            cells=[{"row_id": updated_row["id"], "property_id": status_property,
                    "value": "updated status",
                    "expected_target_version": updated_row["cell_versions"].get(
                        status_property, 0)}],
            expected_document_sequence=owner_projection["document_sequence"],
            expected_workspace_epoch=owner_projection["workspace_epoch"],
            expected_document_epoch=owner_projection["document_epoch"],
            expected_database_epoch=owner_projection["database_epoch"],
            operation_id="browser-staged-database-update-activity",
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                context = browser.new_context(
                    locale="ko-KR", viewport={"width": 1280, "height": 800})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + f"/database/{workspace_id}/database-e1",
                          wait_until="domcontentloaded")
                page.locator("#userId").fill("db-viewer")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("body")).to_have_attribute(
                    "data-notion-ready", "true", timeout=30_000)
                api_result = page.request.get(
                    origin + f"/api/notion/workspaces/{workspace_id}/databases/database-e1")
                assert api_result.status == 200, (api_result.status, api_result.text())
                assert "page-e1" not in api_result.text()
                parent = page.request.get(
                    origin + f"/api/notion/workspaces/{workspace_id}/pages/page-e1/collaboration")
                assert parent.status == 404
                assert page.url.endswith(f"/database/{workspace_id}/database-e1"), page.url
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=10_000)
                expect(page.locator("#independentDatabaseView input").first).to_have_value("Tasks")
                assert "page-e1" not in page.locator("#independentDatabaseView").inner_text()
                assert page.evaluate("State.independentDatabase.data.role") == "comment"
                access = database_access.resolve(authority, "owner", workspace_id,
                                                 "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="edit_content",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-staged-database-promote",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(page.locator(
                    "#independentDatabaseView [data-independent-edit-cell]"
                )).not_to_have_count(0, timeout=15_000)
                assert page.evaluate("State.independentDatabase.data.role") == "edit_content"
                downgrade_field = page.locator(
                    "#independentDatabaseView [data-independent-edit-cell] "
                    "[data-db-field]:not([type='checkbox'])").first
                downgrade_field.evaluate(
                    "element => element.closest('[data-independent-edit-cell]').click()")
                expect(downgrade_field).to_be_enabled()
                before_downgrade = downgrade_field.input_value()
                downgrade_field.fill(before_downgrade + " local role downgrade")
                access = database_access.resolve(authority, "owner", workspace_id,
                                                 "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="comment",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-staged-database-downgrade",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(downgrade_field).to_have_value(
                    before_downgrade + " local role downgrade")
                expect(page.locator("#independentDatabaseAccessStatus")).to_be_visible()
                expect(page.locator("#shareDrawerBtn")).to_be_hidden()
                assert page.evaluate(
                    "State.independentDatabase.accessRefreshPending") is True
                assert page.evaluate(
                    "IndependentDatabaseCells.status(State.independentDatabase.scope).pending"
                ) == 0
                downgrade_field.press("Escape")
                expect(page.locator(
                    "#independentDatabaseView [data-independent-edit-cell]"
                )).to_have_count(0, timeout=15_000)
                assert page.evaluate("State.independentDatabase.data.role") == "comment"
                expect(page.locator("#shareDrawerBtn")).to_be_visible()
                expect(page.locator("#pageCopyLinkBtn")).to_be_visible()
                expect(page.locator("#pageMoreBtn")).to_be_visible()
                page.locator("#pageMoreBtn").click()
                expect(page.locator("#pageMenu[data-surface='independent_database']")).to_be_visible()
                assert "page-e1" not in page.locator("#pageMenu").inner_text()
                page.locator("#pageMenu [data-page-action='updates']").click()
                expect(page.locator("#databaseUpdatesPanel")).to_be_visible()
                page.locator("#drawerCloseBtn").click()
                page.locator("#shareDrawerBtn").click()
                expect(page.locator("#archivedDatabaseSharePopover")).to_be_visible()
                expect(page.locator("#archivedDatabaseShareAccess")).to_contain_text("댓글")
                expect(page.locator("#databaseParentShareBtn")).to_be_hidden()
                assert "page-e1" not in page.locator("#archivedDatabaseSharePopover").inner_text()
                context.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin)
                page.locator("#archivedDatabaseShareCopyBtn").click()
                copied = page.evaluate("navigator.clipboard.readText()")
                assert copied.endswith(f"/database/{workspace_id}/database-e1"), copied
                page.set_viewport_size({"width": 390, "height": 700})
                share_bounds = page.locator("#archivedDatabaseSharePopover").bounding_box()
                assert share_bounds and share_bounds["x"] >= 0
                assert share_bounds["x"] + share_bounds["width"] <= 390
                if os.environ.get("NOTION_DIRECT_DATABASE_SHARE_SCREENSHOT"):
                    page.screenshot(path=os.environ["NOTION_DIRECT_DATABASE_SHARE_SCREENSHOT"])
                page.locator("#archivedDatabaseShareCloseBtn").click()
                if os.environ.get("NOTION_DIRECT_DATABASE_MOBILE_SCREENSHOT"):
                    page.screenshot(path=os.environ["NOTION_DIRECT_DATABASE_MOBILE_SCREENSHOT"])
                page.set_viewport_size({"width": 1280, "height": 800})
                updates = page.request.get(origin +
                    f"/api/notion/workspaces/{workspace_id}/databases/database-e1/updates")
                assert updates.status == 200, (updates.status, updates.text())
                assert "page-e1" not in updates.text()
                assert "document_id" not in updates.json()
                assert any(item.get("row_id") == updated_row["id"]
                           for item in updates.json()["items"])
                assert any(item["command_kind"] == "database.cells.set"
                           for item in updates.json()["items"])
                expect(page.locator("#pageEditTime")).to_be_hidden()
                page.locator("#pageMoreBtn").click()
                page.locator('#pageMenu [data-page-action="updates"]').click()
                expect(page.locator("#databaseUpdatesPanel")).to_be_visible()
                page.wait_for_function("""() => {
                  const text=document.querySelector('#databaseUpdatesList')?.textContent || '';
                  return text.trim().length > 0 && !text.includes('불러오는 중');
                }""")
                assert "page-e1" not in page.locator("#databaseUpdatesPanel").inner_text()
                expect(page.locator("#databaseUpdatesPanel")).to_contain_text(
                    "항목의 속성을 수정했습니다.")
                page.locator(
                    f"#databaseUpdatesList [data-database-update-row='{updated_row['id']}']"
                ).first.click()
                expect(page.locator("#modalBody [data-independent-item-comments]")).to_be_visible()
                page.locator("#modalClose").click()
                direct_search = page.request.get(origin +
                    f"/api/notion/workspaces/{workspace_id}/database-search?direct_only=1")
                assert direct_search.status == 200, (direct_search.status, direct_search.text())
                assert direct_search.json()["direct_only"] is True
                assert [item["database_id"] for item in direct_search.json()["items"]] == ["database-e1"]
                direct_row = page.locator("#sharedPages [data-direct-db-id='database-e1']")
                expect(direct_row).to_be_visible(timeout=15_000)
                expect(direct_row).to_have_attribute("aria-label", "Tasks 데이터베이스 열기")
                assert "page-e1" not in page.locator("#sharedPages").inner_text()
                direct_row.focus()
                page.evaluate("renderPageLists()")
                assert page.evaluate("document.activeElement?.dataset.directDbId") == "database-e1"
                search = page.request.get(origin +
                    f"/api/notion/workspaces/{workspace_id}/database-search?q=Tasks")
                assert search.status == 200, (search.status, search.text())
                assert [item["database_id"] for item in search.json()["items"]] == ["database-e1"]
                assert "page-e1" not in search.text()
                page.locator("#sidebarSearchBtn").click()
                page.locator("#globalSearchInput").fill("Tasks")
                expect(page.locator("#searchResults .search-result-row").filter(
                    has_text="Tasks")).to_be_visible(timeout=15_000)
                assert "page-e1" not in page.locator("#searchOverlay").inner_text()
                page.locator("#searchResults .search-result-row").filter(
                    has_text="Tasks").click()
                page.locator("#previewOpenBtn").click()
                expect(page.locator("#independentDatabaseView")).to_be_visible()
                direct_row.click()
                expect(page.locator("#independentDatabaseView")).to_be_visible()
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                expect(page.locator(
                    "#modalBody [data-independent-item-comments] textarea"
                )).to_be_enabled(timeout=20_000)
                expect(page.locator(
                    "#modalBody [data-independent-item-comments] [data-comment-action='attach']"
                )).to_be_hidden()
                comment_panel = page.locator("#modalBody [data-independent-item-comments]")
                comment_panel.locator("[data-comment-action='mention']").click()
                expect(comment_panel.locator(
                    ".item-comment-mentions [data-person='owner']"
                )).to_be_visible(timeout=15_000)
                comment_panel.locator(
                    ".item-comment-mentions [data-person='owner']").click()
                comment_panel.locator("textarea").fill("Isolated 2429 comment @owner")
                with page.expect_request(lambda request:
                        request.url.endswith("/databases/database-e1/rows/row-e1/comments")
                        and request.method == "POST") as sent:
                    comment_panel.locator("[data-comment-action='send']").click()
                posted = sent.value.post_data_json
                assert posted["mention_subject_ids"] == ["owner"]
                assert "document_id" not in posted
                expect(comment_panel).to_contain_text("Isolated 2429 comment @owner")
                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_visible(timeout=20_000)
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                expect(page.locator("#modalBody [data-independent-item-comments]")).to_contain_text(
                    "Isolated 2429 comment @owner")
                page.locator("#modalClose").click()
                access = database_access.resolve(authority, "owner", workspace_id,
                                                 "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="edit_content",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-staged-database-edit-before-revoke",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                field = page.locator(
                    "#independentDatabaseView [data-independent-edit-cell] "
                    "[data-db-field]:not([type='checkbox'])").first
                expect(field).to_be_visible(timeout=15_000)
                field.evaluate("element => element.closest('[data-independent-edit-cell]').click()")
                expect(field).to_be_enabled()
                original = field.input_value()
                field.fill(original + " local unsaved")
                access = database_access.resolve(authority, "owner", workspace_id,
                                                 "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role=None,
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-staged-database-viewer-revoke",
                )
                denied = page.request.get(
                    origin + f"/api/notion/workspaces/{workspace_id}/databases/database-e1")
                assert denied.status == 404, denied.text()
                denied_search = page.request.get(origin +
                    f"/api/notion/workspaces/{workspace_id}/database-search?q=Tasks")
                assert denied_search.status == 404, denied_search.text()
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(page.locator("#independentDatabaseView")).to_be_visible()
                expect(field).to_have_value(original + " local unsaved")
                expect(page.locator("#independentDatabaseAccessStatus")).to_be_visible()
                expect(page.locator("#sharedPages [data-direct-db-id='database-e1']")).to_have_count(0)
                expect(page.locator("#shareDrawerBtn")).to_be_hidden()
                page.locator("#independentDatabaseView [data-db-close-full-page]").click()
                expect(page.locator("#independentDatabaseView")).to_be_visible()
                expect(field).to_have_value(original + " local unsaved")
                field.press("Escape")
                expect(field).to_have_value(original)
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                expect(page.locator("#sharedPages [data-direct-db-id='database-e1']")).to_have_count(0)
                page.reload(wait_until="domcontentloaded")
                expect(page.locator("body")).to_have_attribute(
                    "data-notion-ready", "true", timeout=20_000)
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                expect(page.locator("#sharedPages [data-direct-db-id='database-e1']")).to_have_count(0)
                assert not errors, errors
                owner_context = browser.new_context(
                    locale="ko-KR", viewport={"width": 1280, "height": 800})
                owner_page = owner_context.new_page()
                owner_page.goto(origin + f"/page/{page_id}", wait_until="domcontentloaded")
                owner_page.locator("#userId").fill("owner")
                owner_page.locator("#password").fill(password)
                owner_page.locator("#submitBtn").click()
                expect(owner_page.locator("#pageView")).to_be_visible(timeout=30_000)
                assert owner_page.evaluate("State.pageData?.page?.id") == page_id
                owner_page.evaluate("""() => {
                  State.pageData.blocks.push({id:'synthetic-parent-share-db',type:'table',props:{}});
                  State.databaseFullPageBlockId='synthetic-parent-share-db';
                  renderTopbarPageActions(State.pageData.page);
                }""")
                owner_page.locator("#shareDrawerBtn").click()
                expect(owner_page.locator("#archivedDatabaseSharePopover")).to_be_visible()
                expect(owner_page.locator("#databaseShareScopeNote")).to_contain_text(
                    "부모 페이지의 접근 권한을 따릅니다")
                expect(owner_page.locator("#databaseParentShareBtn")).to_be_visible()
                expect(owner_page.locator("#archivedDatabaseShareAccess")).to_contain_text("내 권한")
                owner_page.locator("#archivedDatabaseShareCloseBtn").click()
                owner_page.locator("#pageMoreBtn").click()
                expect(owner_page.locator("#pageMenu[data-location='topbar']")).to_be_visible()
                owned_menu = owner_page.evaluate("""() => {
                  const menu=document.querySelector('#pageMenu').getBoundingClientRect();
                  const button=document.querySelector('#pageMoreBtn').getBoundingClientRect();
                  return {width:menu.width,right:menu.right,top:menu.top,
                    buttonBottom:button.bottom,viewport:innerWidth};
                }""")
                assert 250 <= owned_menu["width"] <= 265, owned_menu
                assert owned_menu["viewport"] - owned_menu["right"] <= 16, owned_menu
                assert owned_menu["top"] >= owned_menu["buttonBottom"], owned_menu
                owner_page.locator("#pageMenu .menu-search").press("Escape")
                _schema(authority, "database.property.create", "direct-create-title",
                        {"name": "Name", "type": "title", "config": {}},
                        "exact-browser-direct-row-title", actor="owner")
                before_count = len(database_access.read_projection(
                    authority, "owner", workspace_id,
                    "database-e1")["database"]["rows"])
                owner_page.goto(origin + f"/database/{workspace_id}/database-e1",
                                wait_until="domcontentloaded")
                expect(owner_page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=20_000)
                create_button = owner_page.locator(
                    "#independentDatabaseView [data-independent-row-create]")
                expect(create_button).to_be_enabled(timeout=20_000)
                with owner_page.expect_response(lambda response:
                        response.url.endswith("/databases/database-e1/rows")
                        and response.request.method == "POST") as created_response:
                    create_button.click()
                assert created_response.value.status == 201
                assert "page-e1" not in created_response.value.text()
                expect(owner_page.locator(
                    "#independentDatabaseView .table-row:not(.table-header)"
                )).to_have_count(before_count + 1, timeout=20_000)
                assert len(database_access.read_projection(
                    authority, "owner", workspace_id,
                    "database-e1")["database"]["rows"]) == before_count + 1
                owner_context.close()
            finally:
                browser.close()


@pytest.mark.integration
def test_ui_r10_revoked_direct_database_comment_draft_recovery(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(
        tmp_path, locked=False, gateway_enabled=False, navigation_ui=True,
        owned_database_copy=True, database_activity=True,
        owned_database_trash=True, relation_pair=True, custom_emoji=True,
        item_page_layout=True, template_default=True,
        staged_database_sharing=True,
    ) as (origin, password, authority, workspace_id, _page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                url = origin + f"/database/{workspace_id}/database-e1"
                page.goto(url, wait_until="domcontentloaded")
                page.locator("#userId").fill("db-viewer")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=30_000)
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                draft = "Revoked access must keep this local comment draft"
                comment = page.locator(
                    "#modalBody [data-independent-item-comments] textarea")
                expect(comment).to_be_enabled(timeout=20_000)
                comment.fill(draft)
                page.wait_for_function("""text => {
                  const context=IndependentItemCommentsUi?.context;
                  const snapshot=context&&IndependentItemComments?.snapshot(context);
                  return snapshot?.draft.content===text && snapshot.draftSaved;
                }""", arg=draft)
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role=None,
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-revoke-with-comment-draft",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                recovery = page.locator(
                    "#modalBody [data-independent-recovery-copy='comment']")
                expect(recovery).to_be_visible(timeout=20_000)
                expect(recovery).to_have_value(draft)
                expect(recovery).to_have_attribute("readonly", "")
                expect(page.locator("#shareDrawerBtn")).to_be_hidden()
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                assert page.evaluate("State.independentDatabase.accessDenied") is True
                if os.environ.get("NOTION_DIRECT_DATABASE_RECOVERY_SCREENSHOT"):
                    page.screenshot(path=os.environ[
                        "NOTION_DIRECT_DATABASE_RECOVERY_SCREENSHOT"])
                assert "database-only-property" not in page.locator(
                    "#modalBody").inner_html()
                page.locator("#modalActions button").filter(
                    has_text="홈으로 이동").click()
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="comment",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-regrant-after-comment-draft",
                )
                page.goto(url, wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=20_000)
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                expect(page.locator(
                    "#modalBody [data-independent-item-comments] textarea"
                )).to_have_value(draft, timeout=20_000)
                page.locator("#modalClose").click()
                expect(page.locator("#modalBg")).to_be_hidden()
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="edit_content",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-promote-before-modal-cell-revoke",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(page.locator(
                    "#independentDatabaseView [data-independent-edit-cell]"
                )).not_to_have_count(0, timeout=15_000)
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                page.locator(
                    "#modalBody [data-independent-property]").first.click()
                field = page.locator(
                    "#modalBody [data-independent-editor-input]")
                expect(field).to_be_visible()
                original = field.input_value()
                field.fill(original + " unconfirmed cell")
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role=None,
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-revoke-with-raw-modal-cell",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                raw = page.locator(
                    "#modalBody [data-independent-recovery-copy='cell']")
                expect(raw).to_be_visible(timeout=20_000)
                expect(raw).to_have_value(original + " unconfirmed cell")
                expect(page.locator("#modalClose")).to_be_hidden()
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                expect(page.locator("#modalActions button")).to_contain_text(
                    "입력 버리고 홈으로 이동")
                page.locator("#modalActions button").click()
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="edit_content",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-regrant-before-body-draft",
                )
                page.goto(url, wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=20_000)
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                page.wait_for_function("""() => {
                  const editor=IndependentDatabaseItemBodyEditor;
                  return editor && IndependentDatabaseItemBody?.snapshot(
                    editor.scope,editor.scope.databaseId,editor.rowId)?.ready;
                }""")
                body_draft = "Body draft retained after direct access loss"
                assert page.evaluate("""text => {
                  const editor=IndependentDatabaseItemBodyEditor;
                  const blocks=IndependentDatabaseItemBody.snapshot(
                    editor.scope,editor.scope.databaseId,editor.rowId).blocks;
                  blocks.push({id:crypto.randomUUID(),type:'paragraph',
                    content:text,props:{rich_text_html:text}});
                  return IndependentDatabaseItemBody.draft(
                    editor.scope,editor.scope.databaseId,editor.rowId,blocks);
                }""", body_draft) is True
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role=None,
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-revoke-with-body-draft",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                body_recovery = page.locator(
                    "#modalBody [data-independent-recovery-copy='body']")
                expect(body_recovery).to_be_visible(timeout=20_000)
                assert body_draft in body_recovery.input_value()
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                page.locator("#modalActions button").filter(
                    has_text="홈으로 이동").click()
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="edit_content",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-regrant-after-body-draft",
                )
                page.goto(url, wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=20_000)
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                expect(page.locator(
                    "#modalBody [data-independent-item-body]"
                )).to_contain_text(body_draft, timeout=20_000)
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="comment",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-downgrade-with-body-draft",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                body_recovery = page.locator(
                    "#modalBody [data-independent-recovery-copy='body']")
                expect(body_recovery).to_be_visible(timeout=20_000)
                assert body_draft in body_recovery.input_value()
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                page.locator("#modalActions button").filter(
                    has_text="변경된 권한으로 계속").click()
                expect(page.locator("#independentDatabaseView")).to_be_visible()
                expect(page.locator(
                    "#independentDatabaseView [data-independent-edit-cell]"
                )).to_have_count(0)
                assert page.evaluate("State.independentDatabase.data.role") == "comment"
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                expect(page.locator(
                    "#modalBody [data-independent-item-comments] textarea"
                )).to_have_value(draft, timeout=20_000)
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="view",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-downgrade-with-comment-draft",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                comment_recovery = page.locator(
                    "#modalBody [data-independent-recovery-copy='comment']")
                expect(comment_recovery).to_have_value(draft, timeout=20_000)
                page.locator("#modalActions button").filter(
                    has_text="변경된 권한으로 계속").click()
                expect(page.locator("#independentDatabaseView")).to_be_visible()
                assert page.evaluate("State.independentDatabase.data.role") == "view"
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                expect(page.locator(
                    "#modalBody [data-independent-item-comments] textarea"
                )).to_be_disabled(timeout=20_000)
                page.locator("#modalClose").click()
                expect(page.locator("#modalBg")).to_be_hidden()
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="edit_content",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-promote-before-raw-modal-downgrade",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(page.locator(
                    "#independentDatabaseView [data-independent-edit-cell]"
                )).not_to_have_count(0, timeout=15_000)
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                page.locator(
                    "#modalBody [data-independent-property]").first.click()
                role_field = page.locator(
                    "#modalBody [data-independent-editor-input]")
                expect(role_field).to_be_visible()
                role_value = role_field.input_value() + " unsaved role change"
                role_field.fill(role_value)
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="comment",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-downgrade-with-raw-modal-cell",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                raw_recovery = page.locator(
                    "#modalBody [data-independent-recovery-copy='cell']")
                expect(raw_recovery).to_have_value(role_value, timeout=20_000)
                expect(page.locator("#modalClose")).to_be_hidden()
                page.locator("#modalActions button").filter(
                    has_text="입력 버리고 계속").click()
                expect(page.locator("#independentDatabaseView")).to_be_visible()
                assert page.evaluate("State.independentDatabase.data.role") == "comment"
                expect(page.locator(
                    "#independentDatabaseView [data-independent-edit-cell]"
                )).to_have_count(0)
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                composing_comment = page.locator(
                    "#modalBody [data-independent-item-comments] textarea")
                expect(composing_comment).to_have_value(draft, timeout=20_000)
                composing_comment.evaluate("""(element, value) => {
                  element.dispatchEvent(new CompositionEvent('compositionstart',
                    {bubbles:true}));
                  element.value=value;
                  element.dispatchEvent(new Event('input',{bubbles:true}));
                }""", draft + " 조")
                page.wait_for_function("""text => {
                  const context=IndependentItemCommentsUi?.context;
                  const snapshot=context&&IndependentItemComments?.snapshot(context);
                  return snapshot?.draft.content===text && snapshot.composing;
                }""", arg=draft + " 조")
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="view",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-downgrade-during-comment-composition",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(composing_comment).to_have_value(draft + " 조")
                expect(page.locator(
                    "#modalBody .independent-database-recovery"
                )).to_have_count(0)
                page.wait_for_function(
                    "State.independentDatabase?.accessRefreshPending===true",
                    timeout=20_000)
                final_composition = draft + " 조합 완료"
                composing_comment.evaluate("""(element, value) => {
                  element.value=value;
                  element.dispatchEvent(new CompositionEvent('compositionend',
                    {bubbles:true}));
                }""", final_composition)
                raw_composition = page.locator(
                    "#modalBody [data-independent-recovery-copy='cell']")
                expect(raw_composition).to_have_value(
                    final_composition, timeout=20_000)
                page.locator("#modalActions button").filter(
                    has_text="입력 버리고 계속").click()
                expect(page.locator("#independentDatabaseView")).to_be_visible()
                assert page.evaluate("State.independentDatabase.data.role") == "view"
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="edit_content",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-promote-before-body-composition",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(page.locator(
                    "#independentDatabaseView [data-independent-edit-cell]"
                )).not_to_have_count(0, timeout=15_000)
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                body_input = page.locator(
                    "#modalBody [data-independent-item-body] [data-body-text]"
                ).first
                expect(body_input).to_be_visible(timeout=20_000)
                body_prefix = body_input.inner_text() + " 조"
                body_input.evaluate("""(element, value) => {
                  element.dispatchEvent(new CompositionEvent('compositionstart',
                    {bubbles:true}));
                  element.textContent=value;
                  element.dispatchEvent(new Event('input',{bubbles:true}));
                }""", body_prefix)
                page.wait_for_function("""() => {
                  const editor=IndependentDatabaseItemBodyEditor;
                  return editor&&IndependentDatabaseItemBody?.snapshot(
                    editor.scope,editor.scope.databaseId,editor.rowId)?.composing;
                }""")
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="comment",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-downgrade-during-body-composition",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                page.wait_for_function(
                    "State.independentDatabase?.accessRefreshPending===true",
                    timeout=20_000)
                final_body = body_input.inner_text() + "합 완료"
                body_input.evaluate("""(element, value) => {
                  element.textContent=value;
                  element.dispatchEvent(new CompositionEvent('compositionend',
                    {bubbles:true}));
                }""", final_body)
                raw_body = page.locator(
                    "#modalBody [data-independent-recovery-copy='cell']")
                expect(raw_body).to_be_visible(timeout=20_000)
                assert final_body in raw_body.input_value()
                page.locator("#modalActions button").filter(
                    has_text="입력 버리고 계속").click()
                expect(page.locator("#independentDatabaseView")).to_be_visible()
                assert page.evaluate("State.independentDatabase.data.role") == "comment"
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                revoke_comment = page.locator(
                    "#modalBody [data-independent-item-comments] textarea")
                expect(revoke_comment).to_be_enabled(timeout=20_000)
                revoke_prefix = revoke_comment.input_value() + " revoke"
                revoke_comment.evaluate("""(element, value) => {
                  element.dispatchEvent(new CompositionEvent('compositionstart',
                    {bubbles:true}));
                  element.value=value;
                  element.dispatchEvent(new Event('input',{bubbles:true}));
                }""", revoke_prefix)
                page.wait_for_function("""() => {
                  const context=IndependentItemCommentsUi?.context;
                  return context&&IndependentItemComments.snapshot(context)?.composing;
                }""")
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role=None,
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-revoke-during-comment-composition",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                page.wait_for_function(
                    "State.independentDatabase?.accessDenied===true",
                    timeout=20_000)
                expect(revoke_comment).to_have_value(revoke_prefix)
                revoke_final = revoke_prefix + " complete"
                revoke_comment.evaluate("""(element, value) => {
                  element.value=value;
                  element.dispatchEvent(new CompositionEvent('compositionend',
                    {bubbles:true}));
                }""", revoke_final)
                revoke_recovery = page.locator(
                    "#modalBody [data-independent-recovery-copy='cell']")
                expect(revoke_recovery).to_have_value(
                    revoke_final, timeout=20_000)
                page.locator("#modalActions button").filter(
                    has_text="입력 버리고 홈으로 이동").click()
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
def test_ui_r10_revoked_direct_database_pending_journals_can_leave(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(
        tmp_path, locked=False, gateway_enabled=False, navigation_ui=True,
        owned_database_copy=True, database_activity=True,
        owned_database_trash=True, relation_pair=True, custom_emoji=True,
        item_page_layout=True, template_default=True,
        staged_database_sharing=True,
    ) as (origin, password, authority, workspace_id, _page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                url = origin + f"/database/{workspace_id}/database-e1"
                page.goto(url, wait_until="domcontentloaded")
                page.locator("#userId").fill("db-viewer")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=30_000)
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="edit_content",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-promote-before-pending-cell",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                field = page.locator(
                    "#independentDatabaseView [data-independent-edit-cell] "
                    "[data-db-field]:not([type='checkbox'])").first
                expect(field).to_be_visible(timeout=15_000)
                field.evaluate(
                    "element => element.closest('[data-independent-edit-cell]').click()")
                expect(field).to_be_enabled()
                page.route("**/databases/database-e1/cells",
                           lambda route: route.abort("failed"))
                field.fill(field.input_value() + " pending on revoke")
                field.press("Enter")
                page.wait_for_function("""() => {
                  const state=State.independentDatabase;
                  const status=state&&IndependentDatabaseCells?.status(state.scope);
                  return status?.pending===1 && !status.busy;
                }""", timeout=20_000)
                assert page.evaluate(
                    "independentDatabaseRawGridCellWork(State.independentDatabase)"
                ) is False
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role=None,
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-revoke-with-pending-cell",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                recovery = page.locator(
                    "#modalBody .independent-database-recovery")
                expect(recovery).to_be_visible(timeout=20_000)
                expect(recovery).to_contain_text("보관된 셀 저장 요청 1건")
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                assert page.evaluate("State.independentDatabase.parkedPending") is True
                page.locator("#modalActions button").filter(
                    has_text="홈으로 이동").click()
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="edit_content",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-regrant-after-pending-cell",
                )
                page.unroute("**/databases/database-e1/cells")
                page.goto(url, wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=20_000)
                page.wait_for_function("""() => {
                  const state=State.independentDatabase;
                  const status=state&&IndependentDatabaseCells?.status(state.scope);
                  return status?.pending===1 && !status.busy;
                }""", timeout=20_000)
                page.locator("#independentDatabaseCellStatus button").filter(
                    has_text="보관한 입력 지우기").click()
                page.wait_for_function("""() => {
                  const state=State.independentDatabase;
                  return state&&IndependentDatabaseCells?.status(state.scope)?.pending===0;
                }""", timeout=20_000)
                _schema(authority, "database.property.create",
                        "pending-recovery-title",
                        {"name": "Name", "type": "title", "config": {}},
                        "browser-pending-row-title", actor="owner")
                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=20_000)
                before_rows = len(database_access.read_projection(
                    authority, "owner", workspace_id,
                    "database-e1")["database"]["rows"])
                page.route("**/databases/database-e1/rows",
                           lambda route: route.abort("failed"))
                create = page.locator(
                    "#independentDatabaseView [data-independent-row-create]")
                expect(create).to_be_visible(timeout=15_000)
                if not create.is_enabled():
                    create = page.locator(
                        "#independentDatabaseView [data-independent-row-create-blank]")
                expect(create).to_be_enabled()
                create.click()
                page.wait_for_function("""() => {
                  const state=State.independentDatabase;
                  const status=state&&IndependentDatabaseRows?.status(state.scope);
                  return status?.pending && !status.busy;
                }""", timeout=20_000)
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role=None,
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-revoke-with-pending-row",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                recovery = page.locator(
                    "#modalBody .independent-database-recovery")
                expect(recovery).to_be_visible(timeout=20_000)
                expect(recovery).to_contain_text("새 항목 생성 요청 1건")
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                page.locator("#modalActions button").filter(
                    has_text="홈으로 이동").click()
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="edit_content",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-regrant-after-pending-row",
                )
                page.unroute("**/databases/database-e1/rows")
                page.goto(url, wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=20_000)
                page.wait_for_function("""() => {
                  const state=State.independentDatabase;
                  const status=state&&IndependentDatabaseRows?.status(state.scope);
                  return status?.pending && !status.busy;
                }""", timeout=20_000)
                assert len(database_access.read_projection(
                    authority, "owner", workspace_id,
                    "database-e1")["database"]["rows"]) == before_rows
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="comment",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-downgrade-with-pending-row",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(page.locator(
                    "#modalBody .independent-database-recovery"
                )).to_contain_text("새 항목 생성 요청 1건", timeout=20_000)
                page.locator("#modalActions button").filter(
                    has_text="변경된 권한으로 계속").click()
                expect(page.locator("#independentDatabaseView")).to_be_visible()
                assert page.evaluate("State.independentDatabase.data.role") == "comment"
                assert page.evaluate("""() => {
                  const state=State.independentDatabase;
                  return IndependentDatabaseRows.status(state.scope).pending;
                }""") is True
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
def test_ui_r10_inflight_direct_database_writes_recover_after_access_loss(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(
        tmp_path, locked=False, gateway_enabled=False, navigation_ui=True,
        owned_database_copy=True, database_activity=True,
        owned_database_trash=True, relation_pair=True, custom_emoji=True,
        item_page_layout=True, template_default=True,
        staged_database_sharing=True,
    ) as (origin, password, authority, workspace_id, _page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                url = origin + f"/database/{workspace_id}/database-e1"
                page.goto(url, wait_until="domcontentloaded")
                page.locator("#userId").fill("db-viewer")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=30_000)
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="edit_content",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-promote-before-inflight-cell",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                field = page.locator(
                    "#independentDatabaseView [data-independent-edit-cell] "
                    "[data-db-field]:not([type='checkbox'])").first
                expect(field).to_be_visible(timeout=15_000)
                field.evaluate(
                    "element => element.closest('[data-independent-edit-cell]').click()")
                committed = []

                def hold_committed_cell(route):
                    response = route.fetch()
                    committed.append((route, response))

                page.route("**/databases/database-e1/cells",
                           hold_committed_cell)
                committed_value = field.input_value() + " committed before revoke"
                field.fill(committed_value)
                field.press("Enter")
                page.wait_for_function("""() => {
                  const state=State.independentDatabase;
                  const status=state&&IndependentDatabaseCells?.status(state.scope);
                  return status?.busy && status.pending===1;
                }""", timeout=20_000)
                deadline = time.monotonic() + 20
                while not committed and time.monotonic() < deadline:
                    page.wait_for_timeout(100)
                assert len(committed) == 1
                assert committed[0][1].status == 201
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role=None,
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-revoke-after-cell-commit-before-ack",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                page.wait_for_function(
                    "State.independentDatabase?.accessDenied===true",
                    timeout=20_000)
                committed_route, committed_response = committed.pop()
                committed_route.fulfill(response=committed_response)
                expect(page.locator("#independentDatabaseView")).to_be_hidden(
                    timeout=20_000)
                expect(page.locator(
                    "#modalBody .independent-database-recovery"
                )).to_have_count(0)
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="edit_content",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-regrant-after-committed-cell",
                )
                page.unroute("**/databases/database-e1/cells")
                page.goto(url, wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=20_000)
                expect(field).to_have_value(committed_value, timeout=20_000)
                field.evaluate(
                    "element => element.closest('[data-independent-edit-cell]').click()")
                held_cells = []
                page.route("**/databases/database-e1/cells",
                           lambda route: held_cells.append(route))
                input_value = field.input_value() + " in flight revoke"
                field.fill(input_value)
                field.press("Enter")
                page.wait_for_function("""() => {
                  const state=State.independentDatabase;
                  const status=state&&IndependentDatabaseCells?.status(state.scope);
                  return status?.busy && status.pending===1;
                }""", timeout=20_000)
                assert len(held_cells) == 1
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role=None,
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-revoke-during-inflight-cell",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                page.wait_for_function(
                    "State.independentDatabase?.accessDenied===true",
                    timeout=20_000)
                expect(page.locator(
                    "#modalBody .independent-database-recovery"
                )).to_have_count(0)
                held_cells.pop().fulfill(status=403, json={"error": "access revoked"})
                recovery = page.locator(
                    "#modalBody .independent-database-recovery")
                expect(recovery).to_be_visible(timeout=20_000)
                expect(recovery).to_contain_text("보관된 셀 저장 요청 1건")
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                assert page.evaluate("""() => {
                  const state=State.independentDatabase;
                  const status=IndependentDatabaseCells.status(state.scope);
                  return status.pending===1 && !status.busy;
                }""") is True
                page.locator("#modalActions button").filter(
                    has_text="홈으로 이동").click()
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="edit_content",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-regrant-after-inflight-cell",
                )
                page.unroute("**/databases/database-e1/cells")
                page.goto(url, wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=20_000)
                page.wait_for_function("""() => {
                  const state=State.independentDatabase;
                  const status=state&&IndependentDatabaseCells?.status(state.scope);
                  return status?.pending===1 && !status.busy;
                }""", timeout=20_000)
                page.locator("#independentDatabaseCellStatus button").filter(
                    has_text="보관한 입력 지우기").click()
                page.wait_for_function("""() => {
                  const state=State.independentDatabase;
                  return state&&IndependentDatabaseCells?.status(state.scope)?.pending===0;
                }""", timeout=20_000)
                _schema(authority, "database.property.create",
                        "inflight-title",
                        {"name": "Name", "type": "title", "config": {}},
                        "browser-inflight-row-title", actor="owner")
                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=20_000)
                before_rows = len(database_access.read_projection(
                    authority, "owner", workspace_id,
                    "database-e1")["database"]["rows"])
                held_rows = []
                page.route("**/databases/database-e1/rows",
                           lambda route: held_rows.append(route))
                create = page.locator(
                    "#independentDatabaseView [data-independent-row-create]")
                expect(create).to_be_visible(timeout=15_000)
                if not create.is_enabled():
                    create = page.locator(
                        "#independentDatabaseView [data-independent-row-create-blank]")
                create.click()
                page.wait_for_function("""() => {
                  const state=State.independentDatabase;
                  const status=state&&IndependentDatabaseRows?.status(state.scope);
                  return status?.busy && status.pending;
                }""", timeout=20_000)
                assert len(held_rows) == 1
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="comment",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-downgrade-during-inflight-row",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                page.wait_for_function(
                    "State.independentDatabase?.accessRefreshPending===true",
                    timeout=20_000)
                held_rows.pop().fulfill(status=403, json={"error": "role reduced"})
                expect(page.locator(
                    "#modalBody .independent-database-recovery"
                )).to_contain_text("새 항목 생성 요청 1건", timeout=20_000)
                page.locator("#modalActions button").filter(
                    has_text="변경된 권한으로 계속").click()
                expect(page.locator("#independentDatabaseView")).to_be_visible()
                assert page.evaluate("State.independentDatabase.data.role") == "comment"
                assert len(database_access.read_projection(
                    authority, "owner", workspace_id,
                    "database-e1")["database"]["rows"]) == before_rows
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
def test_ui_r10_inflight_direct_item_body_and_comment_recover(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(
        tmp_path, locked=False, gateway_enabled=False, navigation_ui=True,
        owned_database_copy=True, database_activity=True,
        owned_database_trash=True, relation_pair=True, custom_emoji=True,
        item_page_layout=True, template_default=True,
        staged_database_sharing=True,
    ) as (origin, password, authority, workspace_id, _page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                url = origin + f"/database/{workspace_id}/database-e1"
                page.goto(url, wait_until="domcontentloaded")
                page.locator("#userId").fill("db-viewer")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=30_000)
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="edit_content",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-promote-before-inflight-body",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(page.locator(
                    "#independentDatabaseView [data-independent-edit-cell]"
                )).not_to_have_count(0, timeout=15_000)
                projection = database_access.read_projection(
                    authority, "owner", workspace_id, "database-e1")
                row = projection["database"]["rows"][0]
                row_id = row["id"]
                original_body = row.get("body", {}).get("blocks", [])
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                body_input = page.locator(
                    "#modalBody [data-independent-item-body] [data-body-text]"
                ).first
                expect(body_input).to_have_attribute(
                    "contenteditable", "true", timeout=20_000)
                held_body = []
                page.route("**/databases/database-e1/rows/*/body",
                           lambda route: held_body.append(route))
                body_text = "Body held across role downgrade"
                body_input.fill(body_text)
                page.wait_for_function("""() => {
                  const editor=IndependentDatabaseItemBodyEditor;
                  const snapshot=editor&&IndependentDatabaseItemBody?.snapshot(
                    editor.scope,editor.scope.databaseId,editor.rowId);
                  return snapshot?.busy && snapshot.pending;
                }""", timeout=20_000)
                deadline = time.monotonic() + 20
                while not held_body and time.monotonic() < deadline:
                    page.wait_for_timeout(100)
                assert len(held_body) == 1
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="comment",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-downgrade-during-inflight-body",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                page.wait_for_function(
                    "State.independentDatabase?.accessRefreshPending===true",
                    timeout=20_000)
                expect(page.locator(
                    "#modalBody .independent-database-recovery"
                )).to_have_count(0)
                held_body.pop().fulfill(status=403, json={"error": "role reduced"})
                body_recovery = page.locator(
                    "#modalBody [data-independent-recovery-copy='body']")
                expect(body_recovery).to_be_visible(timeout=20_000)
                assert body_text in body_recovery.input_value()
                page.locator("#modalActions button").filter(
                    has_text="변경된 권한으로 계속").click()
                expect(page.locator("#independentDatabaseView")).to_be_visible()
                assert page.evaluate("State.independentDatabase.data.role") == "comment"
                assert database_access.read_projection(
                    authority, "owner", workspace_id,
                    "database-e1")["database"]["rows"][0].get(
                        "body", {}).get("blocks", []) == original_body
                page.unroute("**/databases/database-e1/rows/*/body")
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                comment_input = page.locator(
                    "#modalBody [data-independent-item-comments] textarea")
                expect(comment_input).to_be_enabled(timeout=20_000)
                held_comments = []

                def hold_comment(route):
                    if route.request.method == "POST":
                        held_comments.append(route)
                    else:
                        route.continue_()

                page.route("**/databases/database-e1/rows/*/comments",
                           hold_comment)
                comment_text = "Comment held across access revocation"
                comment_input.fill(comment_text)
                page.locator(
                    "#modalBody [data-independent-item-comments] "
                    "[data-comment-action='send']").click()
                page.wait_for_function("""() => {
                  const context=IndependentItemCommentsUi?.context;
                  const snapshot=context&&IndependentItemComments?.snapshot(context);
                  return snapshot?.busy && snapshot.pending;
                }""", timeout=20_000)
                deadline = time.monotonic() + 20
                while not held_comments and time.monotonic() < deadline:
                    page.wait_for_timeout(100)
                assert len(held_comments) == 1
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role=None,
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-revoke-during-inflight-comment",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                page.wait_for_function(
                    "State.independentDatabase?.accessDenied===true",
                    timeout=20_000)
                expect(page.locator(
                    "#modalBody .independent-database-recovery"
                )).to_have_count(0)
                held_comments.pop().fulfill(
                    status=403, json={"error": "access revoked"})
                comment_recovery = page.locator(
                    "#modalBody [data-independent-recovery-copy='comment']")
                expect(comment_recovery).to_have_value(
                    comment_text, timeout=20_000)
                expect(page.locator("#independentDatabaseView")).to_be_hidden()
                assert all(comment_text not in item.get("content", "")
                           for item in database_comments.list_row_comments(
                               authority, "owner", workspace_id,
                               "database-e1", row_id)["items"])
                page.locator("#modalActions button").filter(
                    has_text="홈으로 이동").click()
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="comment",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-regrant-after-inflight-comment",
                )
                page.unroute("**/databases/database-e1/rows/*/comments")
                page.goto(url, wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=20_000)
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                expect(page.locator(
                    "#modalBody [data-independent-item-comments] textarea"
                )).to_have_value(comment_text, timeout=20_000)
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
def test_ui_r10_committed_direct_item_responses_after_access_change(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(
        tmp_path, locked=False, gateway_enabled=False, navigation_ui=True,
        owned_database_copy=True, database_activity=True,
        owned_database_trash=True, relation_pair=True, custom_emoji=True,
        item_page_layout=True, template_default=True,
        staged_database_sharing=True,
    ) as (origin, password, authority, workspace_id, _page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                url = origin + f"/database/{workspace_id}/database-e1"
                page.goto(url, wait_until="domcontentloaded")
                page.locator("#userId").fill("db-viewer")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=30_000)
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="edit_content",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-promote-before-late-body-ack",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(page.locator(
                    "#independentDatabaseView [data-independent-edit-cell]"
                )).not_to_have_count(0, timeout=15_000)
                row_id = database_access.read_projection(
                    authority, "owner", workspace_id,
                    "database-e1")["database"]["rows"][0]["id"]
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                body_input = page.locator(
                    "#modalBody [data-independent-item-body] [data-body-text]"
                ).first
                expect(body_input).to_have_attribute(
                    "contenteditable", "true", timeout=20_000)
                committed_body = []

                def hold_committed_body(route):
                    committed_body.append((route, route.fetch()))

                page.route("**/databases/database-e1/rows/*/body",
                           hold_committed_body)
                body_text = "Body committed before response and role reduction"
                body_input.fill(body_text)
                page.wait_for_function("""() => {
                  const editor=IndependentDatabaseItemBodyEditor;
                  const snapshot=editor&&IndependentDatabaseItemBody?.snapshot(
                    editor.scope,editor.scope.databaseId,editor.rowId);
                  return snapshot?.busy && snapshot.pending;
                }""", timeout=20_000)
                deadline = time.monotonic() + 20
                while not committed_body and time.monotonic() < deadline:
                    page.wait_for_timeout(100)
                assert len(committed_body) == 1
                assert committed_body[0][1].status == 201
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="comment",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-downgrade-after-body-commit",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                page.wait_for_function(
                    "State.independentDatabase?.accessRefreshPending===true",
                    timeout=20_000)
                expect(page.locator("#status")).to_contain_text(
                    "작성 중인 입력과 저장 결과", timeout=20_000)
                body_route, body_response = committed_body.pop()
                body_route.fulfill(response=body_response)
                expect(page.locator("#modalBg")).to_be_hidden(timeout=20_000)
                expect(page.locator("#independentDatabaseView")).to_be_visible()
                assert page.evaluate("State.independentDatabase.data.role") == "comment"
                assert body_text in str(database_access.read_projection(
                    authority, "owner", workspace_id,
                    "database-e1")["database"]["rows"][0]["body"]["blocks"])
                page.unroute("**/databases/database-e1/rows/*/body")
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                expect(page.locator(
                    "#modalBody [data-independent-item-body]"
                )).to_contain_text(body_text, timeout=20_000)
                comment_input = page.locator(
                    "#modalBody [data-independent-item-comments] textarea")
                expect(comment_input).to_be_enabled(timeout=20_000)
                committed_comment = []

                def hold_committed_comment(route):
                    if route.request.method == "POST":
                        committed_comment.append((route, route.fetch()))
                    else:
                        route.continue_()

                page.route("**/databases/database-e1/rows/*/comments",
                           hold_committed_comment)
                comment_text = "Comment committed before response and revoke"
                comment_input.fill(comment_text)
                page.locator(
                    "#modalBody [data-independent-item-comments] "
                    "[data-comment-action='send']").click()
                page.wait_for_function("""() => {
                  const context=IndependentItemCommentsUi?.context;
                  const snapshot=context&&IndependentItemComments?.snapshot(context);
                  return snapshot?.busy && snapshot.pending;
                }""", timeout=20_000)
                deadline = time.monotonic() + 20
                while not committed_comment and time.monotonic() < deadline:
                    page.wait_for_timeout(100)
                assert len(committed_comment) == 1
                assert committed_comment[0][1].status == 201
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role=None,
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-revoke-after-comment-commit",
                )
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                page.wait_for_function(
                    "State.independentDatabase?.accessDenied===true",
                    timeout=20_000)
                comment_route, comment_response = committed_comment.pop()
                comment_route.fulfill(response=comment_response)
                expect(page.locator("#independentDatabaseView")).to_be_hidden(
                    timeout=20_000)
                expect(page.locator(
                    "#modalBody .independent-database-recovery"
                )).to_have_count(0)
                assert any(comment_text in item.get("content", "")
                           for item in database_comments.list_row_comments(
                               authority, "owner", workspace_id,
                               "database-e1", row_id)["items"])
                access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1", "subject",
                    "db-viewer", role="comment",
                    expected_workspace_epoch=access.workspace_epoch,
                    expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,
                    operation_id="browser-regrant-after-committed-comment",
                )
                page.unroute("**/databases/database-e1/rows/*/comments")
                page.goto(url, wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=20_000)
                page.locator("#independentDatabaseView [data-table-row='0']").click()
                expect(page.locator(
                    "#modalBody [data-independent-item-comments]"
                )).to_contain_text(comment_text, timeout=20_000)
                expect(page.locator(
                    "#modalBody [data-independent-item-comments] textarea"
                )).to_have_value("", timeout=20_000)
                assert not errors, errors
            finally:
                browser.close()
