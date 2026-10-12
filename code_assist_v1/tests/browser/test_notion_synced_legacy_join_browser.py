"""Staged later-member UI joins a recorded original without editing its page."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.page_creation import create_page
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.notion_collaboration.test_synced_legacy_adoption import insert_legacy


def source_state(authority, document):
    with authority._reader() as con:
        return {"page": dict(authority._document(con, document)),
            "blocks": [dict(row) for row in con.execute("SELECT * FROM workspace_collaboration_blocks WHERE document_id=? ORDER BY position,id", (document,))],
            "frames": [dict(row) for row in con.execute("SELECT * FROM workspace_collaboration_crdt_updates WHERE document_id=? ORDER BY document_sequence,id", (document,))],
            "revisions": [dict(row) for row in con.execute("SELECT * FROM workspace_collaboration_revisions WHERE document_id=? ORDER BY revision", (document,))]}


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("theme,width", [("light", 1280), ("dark", 390)])
def test_staged_later_member_uses_existing_original_and_recovers_lost_reply_without_source_changes(tmp_path, theme, width):
    from playwright.sync_api import expect, sync_playwright
    with _isolated_collaborative_app(tmp_path, locked=False,
            navigation_database_favorites=True, legacy_adoption=True) as (origin, password, authority, ws, source):
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root, identity_directory=authority.identity_directory)
        assert store.capabilities()["collaborative_synced_legacy_adoption"] is False
        initial = create_page(authority, "owner", ws, title="먼저 연결한 페이지",
            base_version=store.get_workspace("owner", ws)["version"], operation_id="legacy-join-browser-first")["page"]["id"]
        later = create_page(authority, "owner", ws, title="나중에 연결할 페이지",
            base_version=store.get_workspace("owner", ws)["version"], operation_id="legacy-join-browser-later")["page"]["id"]
        insert_legacy(store, initial, "first-reference")
        insert_legacy(store, later, "later-member")
        base = f"/api/notion/workspaces/{ws}/synced-legacy/"
        captures, errors = [], []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": width, "height": 844})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + source, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner"); page.locator("#password").fill(password); page.locator("#submitBtn").click()
                page.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration.crdtReady && State.navigation.collaborativeSyncedLegacyAdoption", timeout=30_000)
                page.evaluate("theme => applyColorTheme(theme)", theme)
                page.evaluate("""() => {
                    const block=EditorCore.createBlock('synced_block',{id:'chosen-original',content:'조합된 한글 A',props:{sync_id:'legacy-group-A',rich_text_html:'<strong>조합된 한글 A</strong>'}});
                    State.pageData.blocks=NotionBlockStructure.insert(State.pageData.blocks,State.pageData.blocks.length-1,[block]);
                    markDirty();captureEditorHistory(true);renderBlocks();
                }""")
                page.wait_for_function("() => !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive", timeout=30_000)
                # Establish the recorded group through the real initial-choice
                # UI, including the app's normal Origin and CSRF protections.
                page.locator('#block-chosen-original [data-action="menu"]').click()
                page.locator('#blockMenu [data-action="sync_adopt"]').click()
                first_chooser = page.locator('#syncedLegacyChooser')
                expect(first_chooser.locator('[data-legacy-block="chosen-original"]')).to_be_visible(timeout=30_000)
                first_chooser.locator('[data-legacy-block="chosen-original"] [data-legacy-origin]').check()
                first_chooser.get_by_label('동기화 블록을 확인할 페이지').select_option(initial)
                expect(first_chooser.locator('[data-legacy-block="first-reference"]')).to_be_visible(timeout=30_000)
                first_chooser.locator('[data-legacy-block="first-reference"] [data-legacy-reference]').check()
                first_chooser.get_by_role('button', name='선택 검토', exact=True).click()
                expect(first_chooser.get_by_role('button', name='원본 지정 및 전환', exact=True)).to_be_enabled(timeout=30_000)
                with page.expect_response('**' + base + 'adopt') as first_response:
                    first_chooser.get_by_role('button', name='원본 지정 및 전환', exact=True).click()
                first = first_response.value
                assert first.status == 201, first.text()
                first_result = first.json()
                page.wait_for_function("() => State.liveCollaboration.crdtReady && State.pageData.blocks.find(block=>block.id==='chosen-original')?.props.synced_from===null", timeout=30_000)
                if page.locator('#modalClose').is_visible(): page.locator('#modalClose').click()
                page.goto(origin + "/page/" + later, wait_until="domcontentloaded")
                page.wait_for_function("id => State.activePageId === id && State.liveCollaboration.crdtReady", arg=later, timeout=30_000)
                page.wait_for_function("() => !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive", timeout=30_000)
                original_before = source_state(authority, source)
                page.locator('#block-later-member [data-action="menu"]').click()
                page.locator('#blockMenu [data-action="sync_adopt"]').click()
                chooser = page.locator("#syncedLegacyChooser")
                expect(chooser.locator('[data-legacy-block="later-member"]')).to_be_visible(timeout=30_000)
                chooser.get_by_label("동기화 블록을 확인할 페이지").select_option(source)
                original_choice = chooser.locator('[data-legacy-block="chosen-original"]')
                expect(original_choice).to_contain_text("기존 원본 사용", timeout=30_000)
                expect(original_choice.locator('[data-legacy-reference]')).to_be_disabled()
                original_choice.locator('[data-legacy-origin]').check()
                expect(chooser.get_by_role("button", name="선택 검토", exact=True)).to_be_disabled()
                packet = Path(os.environ["CODE_ASSIST_LEGACY_JOIN_EVIDENCE_DIR"]) if os.environ.get("CODE_ASSIST_LEGACY_JOIN_EVIDENCE_DIR") else None
                if packet:
                    packet.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=os.fspath(packet / f"existing-original-{theme}-{width}.png"), full_page=False)
                chooser.get_by_label("동기화 블록을 확인할 페이지").select_option(later)
                chooser.locator('[data-legacy-block="later-member"] [data-legacy-reference]').check()
                chooser.get_by_role("button", name="선택 검토", exact=True).click()
                confirm = chooser.get_by_role("button", name="기존 원본에 연결", exact=True)
                expect(confirm).to_be_enabled(timeout=30_000)
                expect(chooser).to_contain_text("원본 내용은 그대로 유지됩니다")
                assert source_state(authority, source) == original_before
                def lost_reply(route):
                    response = route.fetch()
                    assert response.status == 201, response.text()
                    captures.append({"request": route.request.post_data_json, "response": response.json()})
                    route.abort("failed")
                page.route("**" + base + "adopt", lost_reply)
                confirm.click()
                banner = page.locator("#syncedLegacyStatus")
                expect(banner.get_by_role("button", name="결과 확인", exact=True)).to_be_enabled(timeout=30_000)
                assert len(captures) == 1
                original_receipt = next(item for item in captures[0]["response"]["result"]["documents"] if item["document_id"] == source)
                assert original_receipt["unchanged"] is True and original_receipt["update_frames"] == 0
                assert source_state(authority, source) == original_before
                page.reload(wait_until="domcontentloaded")
                page.wait_for_function("() => State.liveCollaboration.crdtReady", timeout=30_000)
                with page.expect_response("**" + base + "result") as receipt:
                    banner.get_by_role("button", name="결과 확인", exact=True).click()
                assert receipt.value.status == 200 and receipt.value.request.post_data_json == captures[0]["request"]
                expect(banner).to_be_hidden()
                expect(page.locator('#block-later-member')).to_contain_text("조합된 한글 A", timeout=30_000)
                assert source_state(authority, source) == original_before
                assert receipt.value.json()["result"]["sync_id"] == first_result["result"]["sync_id"]
                with authority._reader() as con:
                    assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations WHERE action='collaboration.synced_legacy_adoption'").fetchone()[0] == 2
                if packet:
                    page.screenshot(path=os.fspath(packet / f"joined-reference-{theme}-{width}.png"), full_page=False)
                    (packet / f"browser-{theme}-{width}.json").write_text(json.dumps({"theme": theme, "width": width,
                        "captures": captures, "receipt": receipt.value.json(), "source_rows_frames_revision_unchanged": True,
                        "same_original_group": True, "errors": errors, "parent_capability": store.capabilities()["collaborative_synced_legacy_adoption"]}, ensure_ascii=False, indent=2), encoding="utf-8")
                assert errors == []
            finally:
                browser.close()
