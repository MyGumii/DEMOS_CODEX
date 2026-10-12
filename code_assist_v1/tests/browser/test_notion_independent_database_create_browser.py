"""A direct database row creation survives a lost response without duplicates."""
import os
import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app,
)


@pytest.mark.integration
def test_ui_r10_direct_database_blank_create_replays_after_reload(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False,
                                    navigation_ui=True, gateway_enabled=False) as (
            origin, password, _authority, workspace_id, _page_id):
        projection = {
            "contract": "code_assist.notion.database_sharing.v1",
            "workspace_id": workspace_id, "database_id": "database-e1",
            "role": "edit_content", "workspace_epoch": 1,
            "document_epoch": 1, "database_epoch": 1,
            "document_sequence": 1,
            "database": {"id": "database-e1", "title": "Shared tasks",
                         "version": 1,
                         "properties": [{"id": "title", "name": "Name",
                                         "type": "title", "version": 1,
                                         "config": {}}],
                         "rows": [],
                         "views": [{"id": "view-table", "name": "Table",
                                    "type": "table", "version": 1,
                                    "config": {}}]},
        }
        posted = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                context = browser.new_context(locale="ko-KR",
                                              viewport={"width": 1280,
                                                        "height": 800})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/databases/database-e1", lambda route: route.fulfill(
                    status=200, json=projection))

                def create_response(route):
                    request = route.request.post_data_json
                    posted.append(request)
                    if len(posted) == 1:
                        projection["document_sequence"] += 1
                        projection["database"]["rows"].append({
                            "id": "new-row", "version": 1,
                            "cells": {"title": ""}, "computed": {},
                            "cell_versions": {"title": 1},
                        })
                        route.abort("failed")
                        return
                    if len(posted) == 3:
                        assert request["use_default_template"] is False
                        projection["document_sequence"] += 1
                        projection["database"]["rows"].append({
                            "id": "blank-row", "version": 1,
                            "cells": {"title": ""}, "computed": {},
                            "cell_versions": {"title": 1},
                        })
                    if len(posted) == 4:
                        assert request["template_id"] == "named-template"
                        projection["document_sequence"] += 1
                        projection["database"]["rows"].append({
                            "id": "named-row", "version": 1,
                            "cells": {"title": "Named item"}, "computed": {},
                            "cell_versions": {"title": 1},
                        })
                    route.fulfill(status=201, json={
                        "contract": projection["contract"],
                        "workspace_id": workspace_id,
                        "database_id": "database-e1",
                        "row_id": "blank-row" if len(posted) == 3 else
                                  "named-row" if len(posted) == 4 else "new-row",
                        "command_kind": "database.row.create",
                        **({"template_id": request["template_id"]}
                           if "template_id" in request else {}),
                        "document_sequence": request["expected_document_sequence"] + 1,
                        "operation_id": request["operation_id"],
                        "replayed": len(posted) not in (3, 4),
                    })

                page.route("**/databases/database-e1/rows", create_response)
                page.goto(origin + "/workspace", wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("body")).to_have_attribute(
                    "data-notion-ready", "true", timeout=30_000)
                page.evaluate("([workspace]) => openIndependentDatabase(workspace, 'database-e1', true)",
                              [workspace_id])
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=20_000)
                expect(page.locator('#pageMoreBtn')).to_be_visible()
                page.locator('#pageMoreBtn').click()
                more_menu=page.locator('#pageMenu')
                expect(more_menu).to_be_visible()
                more_geometry = page.evaluate("""() => {
                  const menu=document.querySelector('#pageMenu').getBoundingClientRect();
                  const button=document.querySelector('#pageMoreBtn').getBoundingClientRect();
                  return {width:menu.width,right:menu.right,top:menu.top,
                    buttonBottom:button.bottom,viewport:innerWidth};
                }""")
                assert 250 <= more_geometry['width'] <= 265, more_geometry
                assert more_geometry['viewport']-more_geometry['right'] <= 16, more_geometry
                assert more_geometry['top'] >= more_geometry['buttonBottom'], more_geometry
                if os.environ.get('NOTION_DIRECT_DATABASE_MORE_SCREENSHOT'):
                    page.screenshot(path=os.environ['NOTION_DIRECT_DATABASE_MORE_SCREENSHOT'])
                expect(more_menu).to_contain_text('링크 복사')
                expect(more_menu).to_contain_text('업데이트 보기')
                more_menu.locator('.menu-search').fill('업데이트')
                expect(more_menu.locator('[data-page-action="copy_link"]')).to_be_hidden()
                more_menu.locator('.menu-search').press('Escape')
                expect(more_menu).to_be_hidden()
                assert page.locator('#pageMoreBtn').evaluate(
                    'element => document.activeElement === element')
                create = page.locator(
                    "#independentDatabaseView [data-independent-row-create]")
                expect(create).to_be_enabled(timeout=15_000)
                create.click()
                expect(page.locator("#independentDatabaseRowStatus")).to_contain_text(
                    "보관", timeout=20_000)
                assert len(posted) == 1
                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=20_000)
                expect(page.locator("#independentDatabaseRowStatus")).to_be_hidden(
                    timeout=30_000)
                assert len(posted) == 2 and posted[0] == posted[1]
                assert len(projection["database"]["rows"]) == 1
                expect(page.locator(
                    "#independentDatabaseView [data-table-row='0']")).to_be_visible()
                assert "document_id" not in str(posted[0])
                projection["role"] = "view"
                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=20_000)
                expect(page.locator("#independentDatabaseView "
                                    "[data-independent-row-create]")).to_have_count(0)
                projection["role"] = "edit_content"
                projection["database"]["default_template"] = {
                    "template_id": "restricted-template", "version": 1}
                projection["database"]["templates"] = [{
                    "id": "restricted-template", "name": "Private template",
                    "state": "active", "body": [], "body_unavailable": True,
                    "default_values": {},
                }]
                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=20_000)
                expect(page.locator("[data-independent-row-create]")).to_be_disabled()
                expect(page.locator("[data-independent-row-create-blank]")).to_be_enabled()
                page.locator("[data-independent-row-create-blank]").click()
                expect(page.locator("[data-table-row='1']")).to_be_visible(timeout=20_000)
                assert len(posted) == 3
                projection["database"]["templates"][0]["body_unavailable"] = False
                projection["database"]["templates"].append({
                    "id": "named-template", "name": "Named template",
                    "state": "active", "body": [], "default_values": {},
                })
                page.reload(wait_until="domcontentloaded")
                expect(page.locator("#independentDatabaseView")).to_be_visible(
                    timeout=20_000)
                expect(page.locator("[data-independent-row-create]")).to_be_enabled()
                aligned = page.evaluate("""() => {
                  const tab=document.querySelector('#independentDatabaseView [data-db-view-id]');
                  const create=document.querySelector('#independentDatabaseView [data-independent-row-create]');
                  return tab&&create ? Math.abs(tab.getBoundingClientRect().top-create.getBoundingClientRect().top) : null;
                }""")
                assert aligned is not None and aligned < 8
                page.locator("[data-independent-template-picker]").click()
                expect(page.locator("[data-independent-template-menu]")).to_be_visible()
                menu = page.locator("[data-independent-template-menu]")
                expect(menu).to_contain_text("템플릿 Shared tasks")
                assert 350 <= menu.evaluate("element => element.getBoundingClientRect().width") <= 365
                if capture := os.environ.get("NOTION_TEMPLATE_MENU_CAPTURE"):
                    page.screenshot(path=capture)
                page.locator('[data-independent-template-id="named-template"]').click()
                expect(page.locator("[data-table-row='2']")).to_be_visible(timeout=20_000)
                assert len(posted) == 4
                assert not errors, errors
            finally:
                browser.close()
