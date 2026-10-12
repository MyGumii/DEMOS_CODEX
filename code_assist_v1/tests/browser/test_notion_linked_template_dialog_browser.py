"""The linked template dialog works inside the collaborative app shell."""
import base64
import json
import os
import re

import pytest

from code_assist_v1.notion_collaboration import linked_databases
from code_assist_v1.notion_collaboration import database_access
from code_assist_v1.notion_collaboration.database_relation_pair import enable_relation_pair
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app,
)

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
    "AAAAC0lEQVR42mP8/x8AAwMCAO5WZwoAAAAASUVORK5CYII=")


@pytest.mark.integration
def test_ui_linked_template_dialog_full_app_with_scoped_http_fixture(tmp_path):
    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        pytest.skip("selected collaboration gateway package directory required")
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(
        tmp_path, locked=False, gateway_enabled=True, navigation_ui=True,
        owned_database_copy=True, database_activity=True,
        owned_database_trash=True, relation_pair=True, custom_emoji=True,
        item_page_layout=True, template_default=True,
    ) as (origin, password, authority, workspace_id, page_id):
        _schema(authority, "database.property.create", "linked-template-title",
                {"name": "Name", "type": "title", "config": {}},
                "browser-linked-template-title", actor="owner")
        block_id = "linked-template-browser"
        page_snapshot = authority.read_document("owner", page_id)
        authority.execute_typed_command(
            "owner", page_id, command_kind="block.insert", target_id=block_id,
            payload={"type": "table", "content": "", "props": {
                "linked_database": {"document_id": "page-e1", "database_id": "database-e1"},
                "views": [{"id": "linked-template-view", "name": "표", "type": "table",
                           "config": {}}],
                "active_view_id": "linked-template-view", "indent": 0},
                "after_block_id": None},
            expected_document_sequence=page_snapshot["document_sequence"],
            expected_workspace_epoch=page_snapshot["workspace_epoch"],
            expected_document_epoch=page_snapshot["document_epoch"],
            operation_id="browser-linked-template-insert",
        )
        linked = linked_databases.read_linked_database(
            authority, "owner", page_id, block_id)
        source = authority.read_document("owner", "page-e1")
        linked["source"] = {"database_id": "database-e1"}
        linked["instance_witness"] = {
            "workspace_epoch": page_snapshot["workspace_epoch"],
            "document_sequence": page_snapshot["document_sequence"] + 1,
            "document_epoch": page_snapshot["document_epoch"],
            "generation": 1,
        }
        linked["access"] = {
            "workspace_epoch": source["workspace_epoch"],
            "document_sequence": source["document_sequence"],
            "document_epoch": source["document_epoch"],
            "database_epoch": 1,
        }
        linked["database"]["effective_role"] = "edit"
        linked["database"]["templates"] = []
        linked["database"]["default_template"] = {"template_id": ""}
        posted, patched = [], []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                endpoint = (f"**/api/notion/workspaces/{workspace_id}/pages/{page_id}"
                            f"/linked-databases/{block_id}")

                def scoped_http(route):
                    if route.request.method == "GET":
                        route.fulfill(status=200, content_type="application/json",
                                      body=json.dumps(linked, ensure_ascii=False))
                        return
                    if route.request.method == "POST" and route.request.url.endswith("/templates"):
                        request = route.request.post_data_json
                        posted.append(request)
                        linked["database"]["templates"].append({
                            "id": request["target_id"], "name": request["name"],
                            "state": "active", "version": 1, "body": [],
                            "default_values": {},
                        })
                        result = {
                            "contract": "code_assist.notion.database_sharing.v1",
                            "workspace_id": workspace_id,
                            "database_id": "database-e1",
                            "operation_id": request["operation_id"],
                            "command_kind": "database.template.create",
                            "target_id": request["target_id"], "target_version": 1,
                            "document_sequence": request["source_access"]["document_sequence"] + 1,
                            "linked_instance": {"document_id": page_id,
                                                "block_id": block_id},
                        }
                        linked["access"]["document_sequence"] += 1
                        route.fulfill(status=201, content_type="application/json",
                                      body=json.dumps(result, ensure_ascii=False))
                        return
                    if route.request.method == "PATCH" and "/templates/" in route.request.url:
                        request = route.request.post_data_json
                        template_id = route.request.url.rsplit("/", 1)[-1]
                        patched.append(request)
                        if len(patched) == 1:
                            route.abort("failed")
                            return
                        template = next(item for item in linked["database"]["templates"]
                                        if item["id"] == template_id)
                        template.update(name=request["name"], body=request["body"],
                                        default_values=request["default_values"],
                                        version=template["version"] + 1)
                        result = {
                            "contract": "code_assist.notion.database_sharing.v1",
                            "workspace_id": workspace_id,
                            "database_id": "database-e1",
                            "operation_id": request["operation_id"],
                            "command_kind": "database.template.update",
                            "target_id": template_id,
                            "target_version": template["version"],
                            "document_sequence": request["source_access"]["document_sequence"] + 1,
                            "linked_instance": {"document_id": page_id,
                                                "block_id": block_id},
                        }
                        linked["access"]["document_sequence"] += 1
                        route.fulfill(status=200, content_type="application/json",
                                      body=json.dumps(result, ensure_ascii=False))
                        return
                    route.continue_()

                page.route(endpoint + "**", scoped_http)
                page.goto(origin + f"/page/{page_id}", wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                root = page.locator(f'[data-linked-database="{block_id}"]')
                picker = root.locator("[data-linked-template-picker]")
                expect(picker).to_be_enabled(timeout=30_000)
                picker.click()
                menu = root.locator("[data-linked-template-menu]")
                expect(menu.locator("[data-linked-template-create]")).to_be_visible()
                assert menu.locator('[data-linked-template-id=""]').count() == 0
                assert "재사용 가능한 페이지 템플릿을 생성합니다" in menu.inner_text()
                assert menu.locator(".database-template-help").get_attribute("href") \
                    == "https://www.notion.com/ko/help/database-templates"
                if os.environ.get("NOTION_LINKED_TEMPLATE_MENU_SCREENSHOT"):
                    page.screenshot(path=os.environ["NOTION_LINKED_TEMPLATE_MENU_SCREENSHOT"])
                page.evaluate("renderBlocks()")
                expect(root.locator("[data-linked-template-menu]")).to_be_visible()
                root.locator("[data-linked-template-create]").click()
                name = page.locator("#linkedTemplateName")
                expect(name).to_be_visible()
                name.fill("새 회의록")
                name.press("Enter")
                expect(name).to_be_hidden(timeout=30_000)
                assert len(posted) == 1
                assert posted[0]["name"] == "새 회의록"
                assert posted[0]["source"] == {"database_id": "database-e1"}
                picker.click()
                expect(root.locator("[data-linked-template-id]:has-text('새 회의록')")) \
                    .to_be_visible()
                linked["database"]["title"] = "Tasks refreshed"
                page.evaluate("LinkedDatabaseViews.refresh()")
                expect(root.locator("[data-linked-template-edit]")).to_be_visible()
                root.locator("[data-linked-template-edit]").click()
                edit_name = page.locator("#linkedTemplateEditName")
                expect(edit_name).to_be_visible()
                if os.environ.get("NOTION_LINKED_TEMPLATE_SCREENSHOT"):
                    page.screenshot(path=os.environ["NOTION_LINKED_TEMPLATE_SCREENSHOT"])
                edit_name.fill("회의록 수정")
                page.locator('[data-linked-template-default="linked-template-title"]').fill(
                    "회의 제목")
                page.locator("#linkedTemplateBodyEditor [data-body-text]").first.fill(
                    "안건")
                page.locator("#modalActions button").click()
                if edit_name.is_visible():
                    expect(page.locator("#linkedTemplateEditError")).not_to_be_empty()
                    expect(edit_name).to_be_disabled()
                    page.evaluate("renderBlocks()")
                expect(edit_name).to_be_hidden(timeout=30_000)
                assert len(patched) == 2 and patched[0] == patched[1]
                assert patched[0]["name"] == "회의록 수정"
                assert patched[0]["default_values"] == {
                    "linked-template-title": "회의 제목"}
                assert patched[0]["body"][0]["content"] == "안건"
                picker.click()
                expect(root.locator("[data-linked-template-id]:has-text('회의록 수정')")) \
                    .to_be_visible()
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
def test_ui_linked_template_dialog_exact_2429_scoped_server(tmp_path):
    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        pytest.skip("selected collaboration gateway package directory required")
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(
        tmp_path, locked=False, gateway_enabled=True, navigation_ui=True,
        owned_database_copy=True, database_activity=True,
        owned_database_trash=True, relation_pair=True, custom_emoji=True,
        item_page_layout=True, template_default=True,
        staged_database_sharing=True,
    ) as (origin, password, authority, workspace_id, page_id):
        _schema(authority, "database.property.create", "linked-template-live-title",
                {"name": "Name", "type": "title", "config": {}},
                "browser-linked-template-live-title", actor="owner")
        _schema(authority, "database.property.create", "linked-template-live-related",
                {"name": "Related", "type": "relation",
                 "config": {"target_database_id": "database-e1"}},
                "browser-linked-template-live-related", actor="owner")
        _schema(authority, "database.property.create", "linked-template-live-files",
                {"name": "Files", "type": "files", "config": {}},
                "browser-linked-template-live-files", actor="owner")
        target_database_id = "linked-template-cross-target"
        target_row_id = "linked-template-cross-row"

        def target_command(kind, target_id, payload, operation_id, *, surface=False):
            source_page = authority.read_document("owner", "page-e1")
            execute = (authority.execute_surface_command if surface else
                       authority.execute_schema_command)
            return execute(
                "owner", "page-e1", database_id=target_database_id,
                command_kind=kind, target_id=target_id, payload=payload,
                expected_document_sequence=source_page["document_sequence"],
                expected_workspace_epoch=source_page["workspace_epoch"],
                expected_document_epoch=source_page["document_epoch"],
                expected_target_version=0, operation_id=operation_id,
            )

        target_command("database.create", target_database_id,
                       {"title": "Private target"}, "browser-cross-target-create")
        target_command("database.property.create", "linked-template-cross-title",
                       {"name": "Name", "type": "title", "config": {}},
                       "browser-cross-title-create")
        target_command("database.property.create", "linked-template-cross-date",
                       {"name": "Due", "type": "date", "config": {}},
                       "browser-cross-date-create")
        target_command("database.row.create", target_row_id, {},
                       "browser-cross-row-create", surface=True)
        target_page = authority.read_document("owner", "page-e1")
        authority.execute_typed_command(
            "owner", "page-e1", command_kind="database.cell.set",
            target_id=f"{target_row_id}:linked-template-cross-title",
            payload={"row_id": target_row_id,
                     "property_id": "linked-template-cross-title",
                     "value": "연결 대상"},
            expected_document_sequence=target_page["document_sequence"],
            expected_workspace_epoch=target_page["workspace_epoch"],
            expected_document_epoch=target_page["document_epoch"],
            expected_target_version=0,
            operation_id="browser-cross-row-title",
        )
        target_page = authority.read_document("owner", "page-e1")
        authority.execute_typed_command(
            "owner", "page-e1", command_kind="database.cell.set",
            target_id=f"{target_row_id}:linked-template-cross-date",
            payload={"row_id": target_row_id,
                     "property_id": "linked-template-cross-date",
                     "value": {"start": "2026-10-01"}},
            expected_document_sequence=target_page["document_sequence"],
            expected_workspace_epoch=target_page["workspace_epoch"],
            expected_document_epoch=target_page["document_epoch"],
            expected_target_version=0,
            operation_id="browser-cross-row-date",
        )
        target_page = authority.read_document("owner", "page-e1")
        target_row = next(row for row in authority.read_database(
            "owner", target_database_id)["rows"] if row["id"] == target_row_id)
        authority.execute_surface_command(
            "owner", "page-e1", database_id=target_database_id,
            command_kind="database.row.appearance.set",
            target_id=target_row_id, payload={"icon": "🚀"},
            expected_document_sequence=target_page["document_sequence"],
            expected_workspace_epoch=target_page["workspace_epoch"],
            expected_document_epoch=target_page["document_epoch"],
            expected_target_version=target_row["version"],
            operation_id="browser-cross-row-icon",
        )
        _schema(authority, "database.property.create", "linked-template-cross-related",
                {"name": "Cross related", "type": "relation",
                 "config": {"target_database_id": target_database_id}},
                "browser-cross-relation-create", actor="owner")
        page_access = authority.read_document("owner", page_id)
        authority.set_page_grant(
            "owner", page_id, "subject", "db-viewer", role="edit_content",
            inherit_to_descendants=False,
            expected_workspace_epoch=page_access["workspace_epoch"],
            expected_document_epoch=page_access["document_epoch"],
            operation_id="browser-linked-template-instance-grant",
        )
        source_access = database_access.resolve(authority, "owner", workspace_id,
                                                "database-e1")
        database_access.change_grant(
            authority, "owner", workspace_id, "database-e1", "subject",
            "db-viewer", role="edit",
            expected_workspace_epoch=source_access.workspace_epoch,
            expected_document_epoch=source_access.document_epoch,
            expected_database_epoch=source_access.database_epoch,
            operation_id="browser-linked-template-schema-grant",
        )
        target_access = database_access.resolve(
            authority, "owner", workspace_id, target_database_id)
        database_access.change_grant(
            authority, "owner", workspace_id, target_database_id, "subject",
            "db-viewer", role="view",
            expected_workspace_epoch=target_access.workspace_epoch,
            expected_document_epoch=target_access.document_epoch,
            expected_database_epoch=target_access.database_epoch,
            operation_id="browser-linked-template-cross-view-grant",
        )
        with pytest.raises(NotFoundOrDenied):
            authority.read_document("db-viewer", "page-e1")
        block_id = "linked-template-live-2429"
        page_snapshot = authority.read_document("owner", page_id)
        authority.execute_typed_command(
            "owner", page_id, command_kind="block.insert", target_id=block_id,
            payload={"type": "table", "content": "", "props": {
                "linked_database": {"document_id": "page-e1", "database_id": "database-e1"},
                "views": [{"id": "linked-template-live-view", "name": "표",
                           "type": "table", "config": {}}],
                "active_view_id": "linked-template-live-view", "indent": 0},
                "after_block_id": None},
            expected_document_sequence=page_snapshot["document_sequence"],
            expected_workspace_epoch=page_snapshot["workspace_epoch"],
            expected_document_epoch=page_snapshot["document_epoch"],
            operation_id="browser-linked-template-live-insert",
        )
        live_projection = linked_databases.read_linked_database_scoped(
            authority, "db-viewer", workspace_id, page_id, block_id)
        assert live_projection["database"]["effective_role"] == "edit"
        assert any(item["type"] == "title" for item in
                   live_projection["database"]["properties"])
        related_row_id = live_projection["database"]["rows"][0]["id"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + f"/page/{page_id}", wait_until="domcontentloaded")
                page.locator("#userId").fill("db-viewer")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                root = page.locator(f'[data-linked-database="{block_id}"]')
                picker = root.locator("[data-linked-template-picker]")
                expect(picker).to_be_enabled(timeout=30_000)
                scoped_response = page.request.get(
                    origin + f"/api/notion/workspaces/{workspace_id}/pages/{page_id}"
                             f"/linked-databases/{block_id}")
                assert scoped_response.status == 200
                assert "page-e1" not in scoped_response.text()
                full_page = root.locator("[data-linked-full-page]")
                expect(full_page).to_have_attribute(
                    "href", f"/page/{page_id}?linked_db={block_id}")
                full_page.click()
                expect(full_page).to_have_attribute("data-linked-close-full-page", "")
                expect(page.locator("#breadcrumb .database-breadcrumb-current")) \
                    .to_have_text(live_projection["database"]["title"])
                assert page.locator("#pageView").evaluate(
                    "node => node.classList.contains('database-full-page')")
                assert f"linked_db={block_id}" in page.url
                page.reload(wait_until="domcontentloaded")
                expect(full_page).to_have_attribute(
                    "data-linked-close-full-page", "", timeout=30_000)
                page.go_back()
                expect(full_page).to_have_attribute(
                    "data-linked-open-full-page", "", timeout=30_000)
                assert "linked_db=" not in page.url
                full_page.click()
                expect(full_page).to_have_attribute("data-linked-close-full-page", "")
                full_page.click()
                expect(full_page).to_have_attribute("data-linked-open-full-page", "")
                expect(full_page).to_be_focused()
                cross_cell = root.locator(
                    '[data-linked-relation-trigger="linked-template-cross-related"]').first
                expect(cross_cell).to_be_enabled(timeout=30_000)
                cross_cell.click()
                cross_option = page.locator(
                    f'[data-linked-relation-option="{target_row_id}"]')
                expect(cross_option).to_be_visible()
                expect(cross_option.locator(
                    'img.notion-emoji-image[src*="1f680.svg"]')).to_be_visible()
                page.get_by_role("button", name="표시할 속성을 선택하세요.").click()
                display_menu = page.get_by_role("dialog", name="관계형에서 표시할 속성")
                expect(display_menu).to_contain_text("관계형에서 숨기기")
                display_menu.get_by_role("button", name="Due 표시하기").click()
                expect(cross_option).to_contain_text("Due: 2026-10-01")
                page.keyboard.press("Escape")
                expect(display_menu).to_have_count(0)
                expect(cross_option).to_be_visible()
                page.locator(".personal-linked-relation-popover "
                             "[aria-label='관계형 대상 검색']").press("Escape")
                expect(cross_cell).to_be_focused()
                assert next(
                    row for row in authority.read_database("owner", "database-e1")["rows"]
                    if row["id"] == related_row_id)["cells"].get(
                        "linked-template-cross-related") in (None, [])
                cross_cell.click()
                expect(cross_option).to_be_visible()
                expect(page.locator(".personal-linked-relation-section")) \
                    .to_contain_text("0개 선택함")
                assert not page.locator(
                    ".personal-linked-relation-apply").count()
                cross_option.click()
                expect(cross_cell).to_contain_text("연결 대상", timeout=30_000)
                source_row = next(
                    row for row in authority.read_database("owner", "database-e1")["rows"]
                    if row["id"] == related_row_id)
                assert source_row["cells"]["linked-template-cross-related"] == [target_row_id]
                file_cell = root.locator(
                    '.database-files-property:has('
                    '[data-db-file-link-toggle="linked-template-live-files"])').first
                expect(file_cell.locator(".personal-linked-file-menu-toggle")).to_be_enabled()
                file_cell.locator(".personal-linked-file-menu-toggle").click()
                file_cell.locator("[data-db-file-link-toggle]").click()
                file_cell.locator("[data-db-file-link-url]").fill(
                    "https://example.com/linked-cell.pdf")
                file_cell.locator("[data-db-file-link-name]").fill("연결 셀 자료")
                file_cell.locator("[data-db-file-link-apply]").click()
                expect(file_cell.locator(".database-file-chip")).to_have_count(
                    1, timeout=30_000)
                file_row = next(
                    row for row in authority.read_database("owner", "database-e1")["rows"]
                    if row["id"] == related_row_id)
                assert file_row["cells"]["linked-template-live-files"] == [{
                    "url": "https://example.com/linked-cell.pdf",
                    "name": "연결 셀 자료"}]
                file_cell.locator(".personal-linked-file-menu-toggle").click()
                file_cell.locator("[data-db-file-input]").set_input_files({
                    "name": "linked-cell.png", "mimeType": "image/png",
                    "buffer": _PNG_BYTES,
                })
                expect(file_cell.locator(".database-file-chip")).to_have_count(
                    2, timeout=30_000)
                file_row = next(
                    row for row in authority.read_database("owner", "database-e1")["rows"]
                    if row["id"] == related_row_id)
                file_values = file_row["cells"]["linked-template-live-files"]
                assert file_values[0]["url"] == "https://example.com/linked-cell.pdf"
                assert file_values[1]["file_id"]
                uploaded_cell_file_id = file_values[1]["file_id"]
                file_download = page.request.get(
                    origin + f"/api/notion/workspaces/{workspace_id}"
                             f"/databases/database-e1/files/{file_values[1]['file_id']}")
                assert file_download.status == 200
                assert file_download.body() == _PNG_BYTES
                file_cell.locator('[data-db-file-drag][data-file-index="1"]').focus()
                file_cell.locator('[data-db-file-drag][data-file-index="1"]').press(
                    "Alt+ArrowUp")
                expect(file_cell.locator(".database-file-chip").first).to_contain_text(
                    "linked-cell.png", timeout=30_000)
                file_row = next(
                    row for row in authority.read_database("owner", "database-e1")["rows"]
                    if row["id"] == related_row_id)
                assert file_row["cells"]["linked-template-live-files"][0]["file_id"] == \
                    file_values[1]["file_id"]
                file_cell.locator('[data-db-file-drag][data-file-index="0"]').drag_to(
                    file_cell.locator('.database-file-chip[data-file-index="1"]'))
                expect(file_cell.locator(".database-file-chip").first).to_contain_text(
                    "연결 셀 자료", timeout=30_000)
                file_row = next(
                    row for row in authority.read_database("owner", "database-e1")["rows"]
                    if row["id"] == related_row_id)
                assert file_row["cells"]["linked-template-live-files"][1]["file_id"] == \
                    file_values[1]["file_id"]
                link_chip = file_cell.locator(
                    '.database-file-chip[data-file-index="0"]')
                link_chip.locator("[data-db-file-actions-toggle]").click()
                link_chip.locator("[data-db-file-remove]").click()
                expect(file_cell.locator(".database-file-chip")).to_have_count(
                    1, timeout=30_000)
                file_row = next(
                    row for row in authority.read_database("owner", "database-e1")["rows"]
                    if row["id"] == related_row_id)
                assert file_row["cells"]["linked-template-live-files"][0]["file_id"] == \
                    file_values[1]["file_id"]
                picker.click()
                root.locator("[data-linked-template-create]").click()
                name = page.locator("#linkedTemplateName")
                expect(name).to_be_visible()
                name.fill("실제 범위 템플릿")
                name.press("Enter")
                expect(name).to_be_hidden(timeout=30_000)
                picker.click()
                expect(root.locator("[data-linked-template-id]:has-text('실제 범위 템플릿')")) \
                    .to_be_visible(timeout=20_000)
                root.locator("[data-linked-template-edit]").click()
                edit_name = page.locator("#linkedTemplateEditName")
                expect(edit_name).to_be_visible()
                if os.environ.get("NOTION_LINKED_TEMPLATE_2429_SCREENSHOT"):
                    page.screenshot(path=os.environ["NOTION_LINKED_TEMPLATE_2429_SCREENSHOT"])
                edit_name.fill("실제 범위 템플릿 수정")
                page.locator('[data-linked-template-default="linked-template-live-title"]').fill(
                    "기본 제목")
                same_relation = page.locator(
                    '[data-linked-template-relation="linked-template-live-related"]')
                same_relation.locator("[data-linked-template-relation-toggle]").click()
                relation_choice = same_relation.locator(
                    f'[data-linked-template-relation-option] input[value="{related_row_id}"]')
                expect(relation_choice).to_be_visible()
                relation_choice.check()
                same_relation.locator("[data-linked-template-relation-search]").press("Escape")
                cross_relation = page.locator(
                    '[data-linked-template-relation="linked-template-cross-related"]')
                cross_relation.locator("[data-linked-template-relation-toggle]").click()
                cross_choice = cross_relation.locator(
                    f'[data-linked-template-relation-option] input[value="{target_row_id}"]')
                expect(cross_choice).to_be_visible()
                cross_choice.check()
                cross_relation.locator("[data-linked-template-relation-search]").press("Escape")
                file_default = page.locator(
                    '[data-linked-template-files="linked-template-live-files"]')
                file_default.locator("[data-linked-template-file-link-toggle]").click()
                file_default.locator("[data-linked-template-file-link-url]").fill(
                    "https://example.com/template.pdf")
                file_default.locator("[data-linked-template-file-link-name]").fill(
                    "참고 자료")
                file_default.locator("[data-linked-template-file-link-apply]").click()
                file_default.locator("[data-linked-template-file-input]").set_input_files({
                    "name": "template-default.png", "mimeType": "image/png",
                    "buffer": _PNG_BYTES,
                })
                expect(file_default.locator(".database-template-file")).to_have_count(
                    2, timeout=30_000)
                file_drag = file_default.locator(
                    '[data-linked-template-file-drag="0"]')
                file_drag.focus()
                file_drag.press("Alt+ArrowDown")
                assert file_default.locator(
                    '[data-linked-template-default="linked-template-live-files"]') \
                    .evaluate("el => JSON.parse(el.value)[0].name") == "template-default.png"
                page.locator("#linkedTemplateBodyEditor [data-body-text]").first.fill(
                    "검증 본문")
                page.locator("#modalActions button").click()
                expect(edit_name).to_be_hidden(timeout=30_000)
                read = linked_databases.read_linked_database_scoped(
                    authority, "db-viewer", workspace_id, page_id, block_id)
                item = next(item for item in read["database"]["templates"]
                            if item["name"] == "실제 범위 템플릿 수정")
                assert item["version"] == 2
                assert item["body"][0]["content"] == "검증 본문"
                assert item["default_values"]["linked-template-live-title"] == "기본 제목"
                assert item["default_values"]["linked-template-live-related"] == [related_row_id]
                assert item["default_values"]["linked-template-cross-related"] == [target_row_id]
                file_values = item["default_values"]["linked-template-live-files"]
                assert file_values[0]["file_id"]
                assert file_values[1] == {"url": "https://example.com/template.pdf",
                                          "name": "참고 자료"}
                download = page.request.get(
                    origin + f"/api/notion/workspaces/{workspace_id}"
                             f"/databases/database-e1/files/{file_values[0]['file_id']}")
                assert download.status == 200
                assert download.body() == _PNG_BYTES
                picker.click()
                root.locator("[data-linked-template-edit]").click()
                expect(page.locator("#linkedTemplateBodyEditor [data-body-add]")) \
                    .not_to_have_count(0)
                page.locator("#linkedTemplateBodyEditor [data-body-add]").first.click()
                image_option = page.locator(
                    "#linkedTemplateBodyEditor [data-body-type='image']")
                expect(image_option).to_be_visible()
                image_option.click()
                resource = page.locator("#linkedTemplateBodyEditor .item-resource-picker")
                expect(resource).to_be_visible()
                resource.locator("[data-resource-tab='link']").click()
                resource.locator("[data-resource-link] input").fill(
                    "https://example.com/template-image.png")
                resource.locator("[data-resource-insert]").click()
                expect(page.locator("#linkedTemplateBodyEditor [data-resource-kind='image']")) \
                    .to_be_visible()
                page.locator("#modalActions button").click()
                expect(page.locator("#linkedTemplateEditName")).to_be_hidden(timeout=30_000)
                media_read = linked_databases.read_linked_database_scoped(
                    authority, "db-viewer", workspace_id, page_id, block_id)
                media_template = next(entry for entry in media_read["database"]["templates"]
                                      if entry["id"] == item["id"])
                assert media_template["version"] == 3
                assert any(block["type"] == "image" and
                           block["props"]["url"] == "https://example.com/template-image.png"
                           for block in media_template["body"])
                picker.click()
                root.locator("[data-linked-template-edit]").click()
                page.locator("#linkedTemplateBodyEditor [data-item-resource]").first.click()
                upload_picker = page.locator(
                    "#linkedTemplateBodyEditor .item-resource-picker")
                expect(upload_picker).to_be_visible()
                upload_picker.locator("input[type='file']").set_input_files({
                    "name": "template-image.png", "mimeType": "image/png",
                    "buffer": _PNG_BYTES,
                })
                expect(page.locator(
                    "#linkedTemplateBodyEditor [data-resource-kind='image'] img"
                )).to_have_attribute("src", re.compile("/databases/database-e1/files/"),
                                      timeout=30_000)
                page.locator("#modalActions button").click()
                expect(page.locator("#linkedTemplateEditName")).to_be_hidden(timeout=30_000)
                uploaded_read = linked_databases.read_linked_database_scoped(
                    authority, "db-viewer", workspace_id, page_id, block_id)
                uploaded_template = next(entry for entry in uploaded_read["database"]["templates"]
                                         if entry["id"] == item["id"])
                assert uploaded_template["version"] == 4
                assert any(block["type"] == "image" and
                           block["props"].get("file_id")
                           for block in uploaded_template["body"])
                source_page = authority.read_document("owner", "page-e1")
                source_property = next(
                    property for property in authority.read_database(
                        "owner", "database-e1")["properties"]
                    if property["id"] == "linked-template-cross-related")
                enable_relation_pair(
                    authority, actor_subject_id="owner", workspace_id=workspace_id,
                    source_document_id="page-e1", source_database_id="database-e1",
                    source_property_id="linked-template-cross-related",
                    target_document_id="page-e1", target_database_id=target_database_id,
                    inverse_property_id="linked-template-cross-inverse",
                    inverse_name="Backlinks",
                    expected_source_sequence=source_page["document_sequence"],
                    expected_target_sequence=source_page["document_sequence"],
                    expected_workspace_epoch=source_page["workspace_epoch"],
                    expected_source_epoch=source_page["document_epoch"],
                    expected_target_epoch=source_page["document_epoch"],
                    expected_source_property_version=source_property["version"],
                    operation_id="browser-linked-template-cross-pair-enable",
                )
                paired_target = next(
                    row for row in authority.read_database(
                        "owner", target_database_id)["rows"]
                    if row["id"] == target_row_id)
                assert related_row_id in paired_target["cells"]["linked-template-cross-inverse"]
                page.reload(wait_until="domcontentloaded")
                expect(cross_cell).to_be_disabled(timeout=30_000)
                target_access = database_access.resolve(
                    authority, "owner", workspace_id, target_database_id)
                database_access.change_grant(
                    authority, "owner", workspace_id, target_database_id,
                    "subject", "db-viewer", role="edit_content",
                    expected_workspace_epoch=target_access.workspace_epoch,
                    expected_document_epoch=target_access.document_epoch,
                    expected_database_epoch=target_access.database_epoch,
                    operation_id="browser-linked-template-cross-content-grant",
                )
                page.reload(wait_until="domcontentloaded")
                expect(cross_cell).to_be_enabled(timeout=30_000)
                cross_cell.click()
                expect(page.locator(".personal-linked-relation-section")) \
                    .to_contain_text("1개 선택함")
                expect(page.locator(f'[data-linked-relation-option="{target_row_id}"]')) \
                    .to_contain_text("Due: 2026-10-01")
                page.locator(f'[data-linked-relation-option="{target_row_id}"] span:nth-child(2)').click()
                expect(page.locator(".personal-linked-relation-popover")).to_be_visible()
                page.locator(f'[data-linked-relation-remove="{target_row_id}"]').click()
                expect(cross_cell).to_contain_text("비어 있음", timeout=30_000)
                cleared_target = next(
                    row for row in authority.read_database(
                        "owner", target_database_id)["rows"]
                    if row["id"] == target_row_id)
                assert related_row_id not in cleared_target["cells"].get(
                    "linked-template-cross-inverse", [])
                cross_cell.click()
                page.locator(f'[data-linked-relation-option="{target_row_id}"]').click()
                expect(cross_cell).to_contain_text("연결 대상", timeout=30_000)
                restored_target = next(
                    row for row in authority.read_database(
                        "owner", target_database_id)["rows"]
                    if row["id"] == target_row_id)
                assert related_row_id in restored_target["cells"]["linked-template-cross-inverse"]
                cross_cell.click()
                expect(page.locator(".personal-linked-relation-popover")).to_be_visible()
                target_access = database_access.resolve(
                    authority, "owner", workspace_id, target_database_id)
                database_access.change_grant(
                    authority, "owner", workspace_id, target_database_id,
                    "subject", "db-viewer", role=None,
                    expected_workspace_epoch=target_access.workspace_epoch,
                    expected_document_epoch=target_access.document_epoch,
                    expected_database_epoch=target_access.database_epoch,
                    operation_id="browser-linked-template-cross-view-revoke",
                )
                page.locator(f'[data-linked-relation-remove="{target_row_id}"]').click()
                expect(root.locator("[data-linked-save-status]")).to_contain_text(
                    "권한", timeout=30_000)
                expect(cross_cell).to_be_disabled(timeout=30_000)
                expect(cross_cell).to_have_text("대상에 접근할 수 없습니다.")
                unchanged_row = next(
                    row for row in authority.read_database("owner", "database-e1")["rows"]
                    if row["id"] == related_row_id)
                assert unchanged_row["cells"]["linked-template-cross-related"] == [
                    target_row_id]
                root.locator("[data-linked-save-status] button", has_text="보관한 입력 지우기").click()
                expect(root.locator("[data-linked-save-status]")).to_be_hidden()
                page.reload(wait_until="domcontentloaded")
                expect(picker).to_be_enabled(timeout=30_000)
                expect(cross_cell).to_be_disabled(timeout=30_000)
                expect(cross_cell).to_have_text("대상에 접근할 수 없습니다.")
                picker.click()
                expect(root.locator(
                    f'[data-linked-template-id="{item["id"]}"]')).to_be_disabled()
                expect(root.locator("[data-linked-template-edit]")).to_have_count(0)
                revoked_read = linked_databases.read_linked_database_scoped(
                    authority, "db-viewer", workspace_id, page_id, block_id)
                revoked_template = next(
                    entry for entry in revoked_read["database"]["templates"]
                    if entry["id"] == item["id"])
                assert revoked_template["default_unavailable"] is True
                assert "linked-template-cross-related" not in revoked_template["default_values"]
                assert target_database_id not in json.dumps(revoked_read, ensure_ascii=False)
                source_access = database_access.resolve(
                    authority, "owner", workspace_id, "database-e1")
                database_access.change_grant(
                    authority, "owner", workspace_id, "database-e1",
                    "subject", "db-viewer", role="view",
                    expected_workspace_epoch=source_access.workspace_epoch,
                    expected_document_epoch=source_access.document_epoch,
                    expected_database_epoch=source_access.database_epoch,
                    operation_id="browser-linked-file-cell-view-only",
                )
                page.reload(wait_until="domcontentloaded")
                expect(root.locator(".database-file-chip")).to_have_count(
                    1, timeout=30_000)
                expect(root.locator(".personal-linked-file-menu-toggle")) \
                    .to_have_count(0)
                view_only_row = next(
                    row for row in authority.read_database("owner", "database-e1")["rows"]
                    if row["id"] == related_row_id)
                assert view_only_row["cells"]["linked-template-live-files"][0]["file_id"] == \
                    uploaded_cell_file_id
                assert not errors, errors
            finally:
                browser.close()
