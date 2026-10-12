"""Staged database-owned original display, inline edit and whole-page copy."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("theme,width", [("light",1280), ("dark",390)])
def test_staged_database_owned_synced_original_displays_refreshes_and_copies_independently(tmp_path, theme, width):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright
    with _isolated_collaborative_app(tmp_path, locked=False, navigation_database_favorites=True, page_copy=True) as (
            origin, password, authority, workspace, source):
        store = CollaborativeWorkspaceStore(authority.catalog_path, user_data_root=authority.user_data_root,
                                            identity_directory=authority.identity_directory)
        assert store.capabilities()["collaborative_page_copy"] is False
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width":width,"height":844})
                errors = []; page.on("pageerror", lambda error:errors.append(str(error)))
                page.goto(origin + "/page/" + source, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner"); page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                page.wait_for_function("() => typeof State!=='undefined' && State.liveCollaboration.crdtReady"
                    " && State.navigation.collaborativePageCopy", timeout=30_000)
                page.evaluate("theme => applyColorTheme(theme)", theme)
                database = page.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length-1,'table');
                  return State.pageData.blocks.find(block=>block.type==='table').id;
                }""")
                page.wait_for_function("() => !State.dirty && !State.liveCollaboration.unsyncedCount"
                    " && !State.liveCollaboration.sendQueueActive", timeout=30_000)
                page.reload(wait_until="domcontentloaded")
                table = page.locator(f'[data-block-id="{database}"]')
                expect(table.locator("[data-database-add]")).to_be_visible(timeout=30_000)
                table.locator("[data-database-add]").click()
                page.wait_for_function("id => State.pageData.databases.find(db=>db.id===id)?.rows.length===1"
                    " && !State.dirty && !State.liveCollaboration.unsyncedCount"
                    " && !State.liveCollaboration.sendQueueActive", arg=database, timeout=30_000)
                row = authority.read_database("owner", database)["rows"][0]["id"]
                def save_body(text, operation):
                    current = next(value for value in authority.read_database("owner", database)["rows"] if value["id"] == row)
                    authority.execute_surface_command("owner", source, database_id=database,
                        command_kind="database.row.content.set", target_id=row, payload={"blocks":[original("db-body-original",text)]},
                        expected_target_version=current["version"], operation_id=operation, **request(store, source))
                save_body("항목 원본 A", "browser-db-sync-a")
                # Complete the synthetic service-side preparation before
                # creating the reference through the live editor bridge.
                page.reload(wait_until="domcontentloaded")
                page.wait_for_function("() => State.liveCollaboration.crdtReady", timeout=30_000)
                page.evaluate("""document => {
                  const ref=EditorCore.createBlock('synced_block',{id:'db-body-peer',props:{synced_from:{document_id:document,block_id:'db-body-original'}}});
                  State.pageData.blocks=NotionBlockStructure.insert(State.pageData.blocks,State.pageData.blocks.length-1,[ref]);
                  markDirty();captureEditorHistory(true);renderBlocks();
                }""", source)
                page.wait_for_function("() => !State.dirty && !State.liveCollaboration.unsyncedCount"
                    " && !State.liveCollaboration.sendQueueActive", timeout=30_000)
                content = page.locator('#block-db-body-peer .synced-reference-content')
                expect(content).to_contain_text("항목 원본 A", timeout=30_000)
                expect(content.locator("b")).to_have_text("자식")
                expect(content.locator('[contenteditable="true"]')).to_have_count(2)
                save_body("항목 원본 B", "browser-db-sync-b")
                expect(content).to_contain_text("항목 원본 B", timeout=20_000)
                endpoint_edit = f"/api/notion/workspaces/{workspace}/pages/{source}/synced-blocks/db-body-peer/text"
                with page.expect_response("**" + endpoint_edit) as edited:
                    content.locator('[data-synced-text="db-body-original"]').fill("참조에서 수정한 D")
                assert edited.value.status == 201, edited.value.text()
                assert edited.value.json()["storage"] == "database_body"
                expect(content).to_contain_text("참조에서 수정한 D", timeout=20_000)
                body = authority.read_database("owner",database)["rows"][0]["body"]["blocks"]
                assert body[0]["content"] == "참조에서 수정한 D"
                assert page.evaluate("State.pageData.blocks.find(block=>block.id==='db-body-peer').content") == ""
                source_body = authority.read_database("owner", database)
                endpoint = f"/api/notion/workspaces/{workspace}/pages/{source}/duplicate"
                # A metadata redraw can finish while a pointer is held. Keep
                # the native target intact through that real press/release.
                icon = page.locator("#pageMoreBtn svg").bounding_box()
                assert icon
                page.mouse.move(icon["x"]+icon["width"]/2,icon["y"]+icon["height"]/2)
                page.mouse.down()
                page.evaluate("() => renderTopbarPageActions(State.pageData.page)")
                page.mouse.up()
                try:
                    expect(page.locator('#pageMenu [data-page-action="duplicate"]')).to_be_visible(timeout=5_000)
                except AssertionError:
                    output = Path(os.environ.get("NOTION_SYNC_DB_ARTIFACT_DIR", str(tmp_path)))
                    output.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(output/f"database-synced-menu-failure-{theme}-{width}.png"))
                    diagnostic = page.evaluate("""() => ({
                      active:State.activePageId, page:State.pageData?.page, blocks:State.pageData?.blocks,
                      fullPageDatabase:activeFullPageDatabase(), menu:document.querySelector('#pageMenu')?.outerHTML,
                      dirty:State.dirty, deferred:SyncedTextRenderDeferred,
                      synced:SyncedText?.snapshot(syncedTextScope(),'db-body-peer')
                    })""")
                    diagnostic["page_errors"] = errors
                    (output/f"database-synced-menu-failure-{theme}-{width}.json").write_text(
                        json.dumps(diagnostic,ensure_ascii=False,indent=2),encoding="utf-8")
                    raise
                page.locator('#pageMenu [data-page-action="duplicate"]').press("Escape")
                expect(page.locator('#pageMenu')).to_be_hidden()
                page.locator('#pageMoreBtn').click()
                expect(page.locator('#pageMenu [data-page-action="duplicate"]')).to_be_visible()
                with page.expect_response("**" + endpoint) as committed:
                    page.locator('#pageMenu [data-page-action="duplicate"]').click()
                assert committed.value.status == 201, committed.value.text()
                copied = committed.value.json()["page"]["id"]
                status = page.locator("#pageCopyStatus")
                status.get_by_role("button",name="복제본 열기",exact=True).click()
                page.wait_for_function("id => State.activePageId===id && State.liveCollaboration.crdtReady", arg=copied, timeout=30_000)
                with authority._reader() as con:
                    copied_body = authority._editing_snapshot(con, authority._document(con,copied))
                copied_database = copied_body["databases"][0]
                copied_original = copied_database["rows"][0]["body"]["blocks"][0]
                peer = page.evaluate("State.pageData.blocks.find(block=>block.type==='synced_block'&&block.props.synced_from!==null)")
                assert peer["props"]["synced_from"] == {"document_id":copied,"block_id":copied_original["id"]}
                assert peer["content"] == "" and copied_original["id"] != "db-body-original"
                copied_content = page.locator(f'#block-{peer["id"]} .synced-reference-content')
                expect(copied_content).to_contain_text("참조에서 수정한 D", timeout=30_000)
                assert authority.read_database("owner", database) == source_body
                save_body("항목 원본 C", "browser-db-sync-source-only-c")
                page.reload(wait_until="domcontentloaded")
                page.wait_for_function("() => State.liveCollaboration.crdtReady", timeout=30_000)
                expect(copied_content).to_contain_text("참조에서 수정한 D", timeout=30_000)
                expect(copied_content).not_to_contain_text("항목 원본 C")
                expect(copied_content.locator('[contenteditable="true"]')).to_have_count(2)
                output = Path(os.environ.get("NOTION_SYNC_DB_ARTIFACT_DIR", str(tmp_path)))
                output.mkdir(parents=True, exist_ok=True)
                copied_content.scroll_into_view_if_needed()
                page.screenshot(path=str(output/f"database-synced-copy-{theme}-{width}.png"))
                assert not errors
            finally: browser.close()
