"""Staged explicit origin chooser and server history after lost browser records."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration import synced_blocks as synced
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.notion_collaboration.test_synced_legacy_adoption import insert_legacy
from code_assist_v1.notion_collaboration.page_creation import create_page


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("theme,width,lost_action", [("light", 1280, "adopt"), ("dark", 390, "intent")])
def test_staged_legacy_chooser_explicit_selection_and_server_recovery_without_local_record(tmp_path, theme, width, lost_action):
    from playwright.sync_api import expect, sync_playwright
    with _isolated_collaborative_app(tmp_path, locked=False,
            navigation_database_favorites=True, legacy_adoption=True) as (
                origin, password, authority, ws, page_id):
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root, identity_directory=authority.identity_directory)
        assert store.capabilities()["collaborative_synced_legacy_adoption"] is False
        child = create_page(authority, "owner", ws, title="함께 전환할 페이지",
            base_version=store.get_workspace("owner", ws)["version"], operation_id="legacy-browser-child")["page"]["id"]
        insert_legacy(store, child, "chosen-instance", indent=1)
        prefix = f"/api/notion/workspaces/{ws}/synced-legacy/"
        captures, errors = [], []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": width, "height": 844})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                page.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration.crdtReady"
                    " && State.navigation.collaborativeSyncedLegacyAdoption", timeout=30_000)
                page.evaluate("theme => applyColorTheme(theme)", theme)
                # This fixture's root already has durable CRDT frames. Add its
                # selected wrappers through the real editor bridge so SQL and
                # the durable stream agree before exercising the product UI.
                page.evaluate("""() => {
                    const props={sync_id:'legacy-group-A',rich_text_html:'<strong>조합된 한글 A</strong>'};
                    const source=EditorCore.createBlock('synced_block',{id:'chosen-original',content:'조합된 한글 A',props});
                    const other=EditorCore.createBlock('synced_block',{id:'unselected-divergent',content:'선택하지 않은 내용 유지',props:{...props,indent:0}});
                    State.pageData.blocks=NotionBlockStructure.insert(State.pageData.blocks,State.pageData.blocks.length-1,[source,other]);
                    markDirty();captureEditorHistory(true);renderBlocks();
                }""")
                page.wait_for_function("() => !State.dirty && !State.liveCollaboration.unsyncedCount"
                    " && !State.liveCollaboration.sendQueueActive", timeout=30_000)
                source_before = authority.read_document("owner", page_id)
                child_before = authority.read_document("owner", child)
                page.locator('#block-chosen-original [data-action="menu"]').click()
                page.locator('#blockMenu [data-action="sync_adopt"]').click()
                chooser = page.locator("#syncedLegacyChooser")
                root_choice = chooser.locator('[data-legacy-block="chosen-original"]')
                expect(root_choice).to_be_visible(timeout=30_000)
                expect(chooser.get_by_role("button", name="선택 검토", exact=True)).to_be_disabled()
                assert chooser.locator('input[type="radio"]:checked').count() == 0
                assert chooser.locator('input[type="checkbox"]:checked').count() == 0
                divergent = chooser.locator('[data-legacy-block="unselected-divergent"]')
                expect(divergent.locator('[data-legacy-origin]')).to_be_disabled()
                expect(divergent.locator('small')).to_contain_text("내용 또는 서식이 다릅니다")
                packet = Path(os.environ["CODE_ASSIST_LEGACY_ADOPTION_EVIDENCE_DIR"]) if os.environ.get("CODE_ASSIST_LEGACY_ADOPTION_EVIDENCE_DIR") else None
                if packet:
                    packet.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=os.fspath(packet / f"selection-{theme}-{width}.png"), full_page=False)
                root_choice.locator('[data-legacy-origin]').check()
                chooser.get_by_label("동기화 블록을 확인할 페이지").select_option(child)
                child_choice = chooser.locator('[data-legacy-block="chosen-instance"]')
                expect(child_choice).to_be_visible(timeout=30_000)
                child_choice.locator('[data-legacy-reference]').check()
                chooser.get_by_role("button", name="선택 검토", exact=True).click()
                expect(chooser.get_by_role("button", name="원본 지정 및 전환", exact=True)).to_be_enabled(timeout=30_000)
                expect(chooser).to_contain_text("2개 페이지에서 원본 1개와 참조 1개")
                contrast = chooser.locator('.synced-legacy-preview').evaluate("""element => {
                    const style=getComputedStyle(element);
                    const luminance=color=>{
                        const rgb=color.match(/[\\d.]+/g).slice(0,3).map(Number).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;});
                        return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;
                    };
                    const a=luminance(style.color),b=luminance(style.backgroundColor);
                    return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
                }""")
                assert contrast >= 4.5, "readable preview in either theme"
                # Review and candidate discovery never alter domain content.
                assert authority.read_document("owner", page_id)["blocks"] == source_before["blocks"]
                assert authority.read_document("owner", child)["blocks"] == child_before["blocks"]
                with authority._reader() as con:
                    assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations WHERE action='collaboration.synced_legacy_adoption_intent'").fetchone()[0] == 0
                if packet:
                    packet.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=os.fspath(packet / f"chooser-{theme}-{width}.png"), full_page=False)
                def lose_reply(route):
                    response = route.fetch()
                    captures.append({"request": route.request.post_data_json, "status": response.status, "response": response.json()})
                    assert response.status == 201, response.text()
                    route.abort("failed")
                page.route("**" + prefix + lost_action, lose_reply)
                chooser.get_by_role("button", name="원본 지정 및 전환", exact=True).click()
                banner = page.locator("#syncedLegacyStatus")
                expect(banner.get_by_role("button", name="결과 확인", exact=True)).to_be_enabled(timeout=30_000)
                assert len(captures) == 1
                request = captures[0]["request"]
                assert request["origin"] == {"document_id": page_id, "block_id": "chosen-original"}
                assert request["references"] == [{"document_id": child, "block_id": "chosen-instance"}]
                # Simulate a cleared/different browser partition. Recovery now
                # comes from the server's durable intent and terminal ledger.
                page.evaluate("() => { for(const key of Object.keys(localStorage)) if(key.startsWith('notion.synced-legacy-adoption.v1:')) localStorage.removeItem(key); }")
                page.reload(wait_until="domcontentloaded")
                page.wait_for_function("() => State.liveCollaboration.crdtReady", timeout=30_000)
                expect(banner).to_be_hidden()
                if width < 720:
                    page.locator("#sidebarRestoreBtn").click()
                page.locator("#workspaceName").click()
                page.locator('[data-workspace-action="synced-legacy-results"]').click()
                history = page.locator("#syncedLegacyHistory")
                expect(history.locator(".synced-legacy-choice")).to_have_count(1, timeout=30_000)
                action = "result" if lost_action == "adopt" else "cancel"
                label = "결과 확인" if action == "result" else "요청 취소"
                with page.expect_response("**" + prefix + action) as receipt:
                    history.get_by_role("button", name=label, exact=True).click()
                assert receipt.value.status in (200, 201)
                assert receipt.value.request.post_data_json == request
                expected = "committed" if action == "result" else "cancelled"
                assert receipt.value.json()["state"] == expected
                expect(history).to_contain_text("원본 지정이 저장되었습니다." if expected == "committed" else "전환 요청이 취소되었습니다.")
                after_source, after_child = authority.read_document("owner", page_id), authority.read_document("owner", child)
                unchanged = next(block for block in source_before["blocks"] if block["id"] == "unselected-divergent")
                assert next(block for block in after_source["blocks"] if block["id"] == unchanged["id"]) == unchanged
                assert [block["id"] for block in after_source["blocks"]] == [block["id"] for block in source_before["blocks"]]
                if expected == "committed":
                    assert synced.read_synced_block(authority, "owner", child, "chosen-instance")["block"]["content"] == "조합된 한글 A"
                    original = next(block for block in after_source["blocks"] if block["id"] == "chosen-original")
                    assert original["props"]["synced_from"] is None and original["props"]["rich_text_html"] == "<strong>조합된 한글 A</strong>"
                else:
                    assert after_source["blocks"] == source_before["blocks"] and after_child["blocks"] == child_before["blocks"]
                if packet:
                    page.screenshot(path=os.fspath(packet / f"history-{theme}-{width}.png"), full_page=False)
                    (packet / f"browser-{theme}-{width}.json").write_text(json.dumps({"theme": theme, "width": width,
                        "lost_action": lost_action, "captures": captures, "recovered": receipt.value.json(),
                        "preview_contrast": contrast, "console_errors": errors, "parent_capability": store.capabilities()["collaborative_synced_legacy_adoption"]}, ensure_ascii=False, indent=2), encoding="utf-8")
                if expected == "committed":
                    page.goto(origin + "/page/" + child, wait_until="domcontentloaded")
                    page.wait_for_function("() => State.liveCollaboration.crdtReady", timeout=30_000)
                    expect(page.locator('#block-chosen-instance')).to_contain_text("조합된 한글 A", timeout=30_000)
                    page.reload(wait_until="domcontentloaded")
                    expect(page.locator('#block-chosen-instance')).to_contain_text("조합된 한글 A", timeout=30_000)
                assert errors == []
            finally:
                browser.close()
