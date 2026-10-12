"""Staged full-page copy: mixed canonical content and a lost committed reply."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration import linked_databases, page_copy_source, synced_blocks
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.page_creation import create_page
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.browser.test_notion_database_copy_browser import _attach_synthetic_file
from code_assist_v1.tests.notion_collaboration.test_page_copy_source import request
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import insert_synced
from code_assist_v1.tests.notion_collaboration.test_parent_rich_restore_rebase import edit_yjs
from code_assist_v1.notion_collaboration import search_projection
from code_assist_v1.notion_collaboration.title_projection import _frames, _generation


def _insert(store, document, identity, kind, content="", props=None):
    store.iam.execute_typed_command("owner", document, command_kind="block.insert", target_id=identity,
        payload={"type": kind, "content": content, "props": props or {}, "after_block_id": None},
        operation_id="mixed-copy-insert-" + identity, **request(store, document))


def _prepared(store, workspace, document):
    return page_copy_source.prepare_page_copy(store.iam, "owner", workspace, document,
                                             **request(store, document))


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("theme,width", [("light", 1280), ("dark", 390)])
@pytest.mark.parametrize('raw_rich', [False, True])
def test_staged_page_copy_mixed_subtree_recovers_lost_reply_without_a_second_copy(tmp_path, theme, width, raw_rich):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False,
            navigation_database_favorites=True, page_copy=True) as (
                origin, password, authority, workspace_id, source_id):
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root, identity_directory=authority.identity_directory)
        assert store.capabilities()["collaborative_page_copy"] is False

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": width, "height": 844})
                errors, dispatched = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + source_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                page.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration.crdtReady"
                    " && State.navigation.collaborativePageCopy", timeout=30_000)
                page.evaluate("theme => applyColorTheme(theme)", theme)
                # Populate a fresh synthetic native page; the operation under
                # test below is the product's actual menu and canonical HTTP path.
                database_id = page.evaluate("""() => {
                    insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                    return State.pageData.blocks.find(block => block.type === 'table').id;
                }""")
                page.wait_for_function("() => !State.dirty && !State.liveCollaboration.unsyncedCount"
                    " && !State.liveCollaboration.sendQueueActive", timeout=30_000)
                page.reload(wait_until="domcontentloaded")
                table = page.locator(f'[data-block-id="{database_id}"]')
                expect(table.locator("[data-database-add]")).to_be_visible(timeout=30_000)
                table.locator("[data-database-add]").click()
                page.wait_for_function("id => State.pageData.databases.find(db => db.id === id)?.rows.length === 1"
                    " && !State.dirty && !State.liveCollaboration.unsyncedCount"
                    " && !State.liveCollaboration.sendQueueActive", arg=database_id, timeout=30_000)
                original_database = authority.read_database("owner", database_id)
                row_id = original_database["rows"][0]["id"]
                file_id, file_bytes = _attach_synthetic_file(authority, source_id, database_id, row_id)
                title_property = next(prop["id"] for prop in original_database["properties"]
                                      if prop["type"] == "title")
                authority.execute_typed_command("owner", source_id, command_kind="database.cell.set",
                    target_id=f"{row_id}:{title_property}", payload={"row_id": row_id,
                        "property_id": title_property, "value": "복제할 항목 A"},
                    expected_target_version=original_database["rows"][0]["cell_versions"].get(title_property, 0),
                    operation_id="mixed-copy-row-title", **request(store, source_id))

                child_id = create_page(authority, "owner", workspace_id, title="함께 복제할 하위 페이지",
                    parent_id=source_id, base_version=store.get_workspace("owner", workspace_id)["version"],
                    operation_id="mixed-copy-child-create")["page"]["id"]
                external_id = create_page(authority, "owner", workspace_id, title="복제 범위 밖 동기화 원본",
                    base_version=store.get_workspace("owner", workspace_id)["version"],
                    operation_id="mixed-copy-external-create")["page"]["id"]
                insert_synced(store, child_id, "mixed-internal-reference",
                    origin={"document_id": source_id, "block_id": "mixed-internal-original"})
                insert_synced(store, external_id, "mixed-external-original", content="외부 동기화 D")
                _insert(store, child_id, "mixed-linked-database", "table", props={
                    "linked_database": {"document_id": source_id, "database_id": database_id},
                    "views": [{"id": "mixed-local-view", "name": "Local A", "type": "table",
                        "config": {"filters": [{"property": title_property, "operator": "contains", "value": "A"}]}}],
                    "active_view_id": "mixed-local-view"})
                page.reload(wait_until="domcontentloaded")
                page.wait_for_function("() => State.liveCollaboration.crdtReady", timeout=30_000)
                # The already-open source has CRDT frames. Populate its body
                # through the real editor bridge instead of leaving SQL-only
                # test inserts absent from that collaborative frame stream.
                page.evaluate("""external => {
                    const additions = [
                      EditorCore.createBlock('synced_block', {id:'mixed-internal-original',
                        content:'내부 동기화 A',props:{synced_from:null}}),
                      EditorCore.createBlock('synced_block', {id:'mixed-external-reference',
                        props:{synced_from:{document_id:external,block_id:'mixed-external-original'}}}),
                      EditorCore.createBlock('paragraph', {id:'mixed-rich-note',content:'서식 보존 문단',
                        props:{rich_text_html:'<strong>서식 보존</strong> 문단'}}),
                    ];
                    State.pageData.blocks = NotionBlockStructure.insert(
                      State.pageData.blocks,State.pageData.blocks.length-1,additions);
                    markDirty(); captureEditorHistory(true); renderBlocks();
                }""", external_id)
                page.wait_for_function("() => !State.dirty && !State.liveCollaboration.unsyncedCount"
                    " && !State.liveCollaboration.sendQueueActive", timeout=30_000)
                expect(page.locator("#block-mixed-rich-note strong")).to_have_text("서식 보존")
                expect(page.locator(f'[data-child-page="{child_id}"]')).to_be_visible()
                expect(page.locator(f'[data-block-id="{database_id}"] .database-file-chip > a')).to_be_visible()
                page.locator("#pageTitle").fill("혼합 문서 복제 최신 제목")
                page.locator("#pageTitle").press("Tab")
                page.wait_for_function("() => !State.dirty && !State.liveCollaboration.unsyncedCount"
                    " && !State.liveCollaboration.sendQueueActive", timeout=30_000)
                if raw_rich:
                    edit_yjs(store, workspace_id, 'mixed-rich-note', 'format2', document=source_id)
                    page.reload(wait_until='domcontentloaded')
                    page.wait_for_function("() => State.liveCollaboration.crdtReady && !State.dirty"
                        " && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive", timeout=30_000)
                before = _prepared(store, workspace_id, source_id)
                external_before = _prepared(store, workspace_id, external_id).documents[0].descriptor()
                assert len(before.documents) == 2
                endpoint = f"/api/notion/workspaces/{workspace_id}/pages/{source_id}/duplicate"

                def lose_committed_reply(route):
                    response = route.fetch()
                    dispatched.append({"request": route.request.post_data_json,
                        "status": response.status, "result": response.json()})
                    assert response.status == 201, response.text()
                    route.abort("failed")

                page.route("**" + endpoint, lose_committed_reply)
                page.locator("#pageMoreBtn").click()
                expect(page.locator('#pageMenu [data-page-action="duplicate"]')).to_be_visible()
                page.locator('#pageMenu [data-page-action="duplicate"]').click()
                status = page.locator("#pageCopyStatus")
                expect(status.get_by_role("button", name="결과 확인", exact=True)).to_be_enabled(timeout=30_000)
                assert len(dispatched) == 1
                committed = dispatched[0]["result"]
                copied_id = committed["page"]["id"]
                page.reload(wait_until="domcontentloaded")
                page.wait_for_function("() => State.liveCollaboration.crdtReady", timeout=30_000)
                with page.expect_response("**" + endpoint + "-result") as receipt:
                    status.get_by_role("button", name="결과 확인", exact=True).click()
                assert receipt.value.status == 200
                assert receipt.value.request.post_data_json == dispatched[0]["request"]
                assert receipt.value.json()["state"] == "committed"
                assert receipt.value.json()["result"]["page"]["id"] == copied_id
                copied = _prepared(store, workspace_id, copied_id)
                status.get_by_role("button", name="복제본 열기", exact=True).click()
                page.wait_for_function("id => State.activePageId === id && State.liveCollaboration.crdtReady",
                                       arg=copied_id, timeout=30_000)
                page.wait_for_function("() => !State.dirty && !State.liveCollaboration.unsyncedCount"
                    " && !State.liveCollaboration.sendQueueActive", timeout=30_000)
                expect(page.locator("#pageTitle")).to_have_value("혼합 문서 복제 최신 제목")
                assert len(copied.documents) == 2
                root = copied.documents[0].descriptor()
                child = copied.documents[1].descriptor()
                copied_child_id = copied.documents[1].document_id
                rich = next(block for block in root["blocks"] if block["content"] == "서식 보존 문단")
                assert rich["props"]["rich_text_html"] == "<strong>서식 보존</strong> 문단"
                expect(page.locator(f'#block-{rich["id"]} strong')).to_have_text("서식 보존")
                if raw_rich:
                    def exact_raw_runs():
                        with authority._reader() as con:
                            saved = _frames(con, copied_id, _generation(con, copied_id))
                        return search_projection.materialize([saved], rich=True)[0]['rich_text']['blocks'][rich['id']]
                    expected_runs = [{'insert': '서식 보존 문단', 'attributes': {'bold': True, 'italic': True}}]
                    assert exact_raw_runs() == expected_runs
                    page.reload(wait_until='domcontentloaded')
                    page.wait_for_function("() => State.liveCollaboration.crdtReady && !State.dirty"
                        " && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive", timeout=30_000)
                    expect(page.locator(f'#block-{rich["id"]} strong')).to_have_text('서식 보존')
                    assert exact_raw_runs() == expected_runs
                assert next(block for block in root["blocks"] if block["type"] == "child_page")["props"]["page_id"] == copied_child_id
                original = next(block for block in root["blocks"] if block["type"] == "synced_block"
                                and block["props"]["synced_from"] is None)
                external = next(block for block in root["blocks"] if block["type"] == "synced_block"
                                and block["props"]["synced_from"] is not None)
                assert external["props"]["synced_from"] == {"document_id": external_id, "block_id": "mixed-external-original"}
                assert external["content"] == ""
                reference = next(block for block in child["blocks"] if block["type"] == "synced_block")
                assert reference["props"]["synced_from"] == {"document_id": copied_id, "block_id": original["id"]}
                assert synced_blocks.read_synced_block(authority, "owner", copied_child_id, reference["id"])["block"]["content"] == "내부 동기화 A"
                assert synced_blocks.read_synced_block(authority, "owner", copied_id, external["id"])["block"]["content"] == "외부 동기화 D"
                copied_database = root["databases"][0]
                assert copied_database["id"] != database_id
                copied_table = next(block for block in root["blocks"]
                                    if block["props"].get("database_id") == copied_database["id"])
                linked = next(block for block in child["blocks"] if "linked_database" in block["props"])
                assert linked["props"]["linked_database"] == {"document_id": copied_id, "database_id": copied_database["id"]}
                assert linked["props"]["active_view_id"] != "mixed-local-view"
                copied_file_ids = [item["file_id"] for value in copied_database["rows"][0]["cells"].values()
                    if isinstance(value, list) for item in value if isinstance(item, dict) and "file_id" in item]
                assert len(copied_file_ids) == 1 and copied_file_ids[0] != file_id
                assert authority.get_file("owner", copied_file_ids[0])["path"].read_bytes() == file_bytes
                link = page.locator(f'[data-block-id="{copied_table["id"]}"] .database-file-chip > a')
                expect(link).to_be_visible()
                assert copied_file_ids[0] in link.get_attribute("href")
                if raw_rich and (directory := os.environ.get('NOTION_MIXED_COPY_ARTIFACT_DIR')):
                    target = Path(directory); target.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(target / f'rich-generation-root-{theme}-{width}.png'))
                page.locator(f'[data-child-page="{copied_child_id}"]').click()
                page.wait_for_function("id => State.activePageId === id && State.liveCollaboration.crdtReady",
                                       arg=copied_child_id, timeout=30_000)
                expect(page.locator(f'#block-{reference["id"]}')).to_contain_text("내부 동기화 A", timeout=30_000)
                expect(page.locator(f'#block-{linked["id"]}')).to_contain_text("복제할 항목 A", timeout=30_000)
                page.reload(wait_until="domcontentloaded")
                expect(page.locator(f'#block-{linked["id"]}')).to_contain_text("복제할 항목 A", timeout=30_000)
                page.wait_for_function("() => State.liveCollaboration.crdtReady && !State.dirty"
                    " && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive", timeout=30_000)
                assert linked_databases.read_linked_database(authority, "owner", copied_child_id, linked["id"])["source"]["database_id"] == copied_database["id"]
                assert [doc.descriptor() for doc in _prepared(store, workspace_id, source_id).documents] == [doc.descriptor() for doc in before.documents]
                assert _prepared(store, workspace_id, external_id).documents[0].descriptor() == external_before
                with authority._reader() as con:
                    assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_native_pages WHERE creation_kind='copy'").fetchone()[0] == 2
                    assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations WHERE action='collaboration.page_copy'").fetchone()[0] == 1
                assert len(dispatched) == 1 and not errors, errors
                assert store.capabilities()["collaborative_page_copy"] is False
                if directory := os.environ.get("NOTION_MIXED_COPY_ARTIFACT_DIR"):
                    target = Path(directory); target.mkdir(parents=True, exist_ok=True)
                    prefix = 'rich-generation' if raw_rich else 'mixed-copy'
                    page.screenshot(path=str(target / f"{prefix}-{theme}-{width}.png"))
                    (target / f"{prefix}-{theme}-{width}.json").write_text(json.dumps({
                        "copy_count": 1, "copied_document_count": 2, "exact_request_recovery": True,
                        "source_preserved": True, "external_source_preserved": True,
                        "rich_text_preserved": True, "file_independent": True,
                        "internal_synced_remapped": True, "external_synced_preserved": True,
                        "linked_database_remapped": True, "production_capability_enabled": False,
                        'raw_attributes_and_reload_verified': raw_rich,
                    }, ensure_ascii=False, indent=2), encoding="utf-8")
            finally:
                browser.close()
