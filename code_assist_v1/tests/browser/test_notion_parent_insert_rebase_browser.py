"""UI-CREATE-INSERT: sidebar child creation retains pending canonical notes."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration import search_projection
from code_assist_v1.notion_collaboration.page_creation import create_page
from code_assist_v1.notion_collaboration.title_projection import _frames, _generation
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app


def current_body(authority, document):
    with authority._reader() as con:
        frames = _frames(con, document, _generation(con, document))
    return search_projection.materialize([frames])[0]


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("theme,width", [("light", 1280), ("dark", 390)])
def test_sidebar_child_creation_and_reload_preserve_prior_typed_note_and_original_body(tmp_path, theme, width):
    from playwright.sync_api import expect, sync_playwright
    with _isolated_collaborative_app(tmp_path, locked=False,
            navigation_database_favorites=True, navigation_ui=True) as (origin, password, authority, ws, source):
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root, identity_directory=authority.identity_directory)
        # The fresh root has a checkpoint but no durable body frames. Create a
        # prior child so the pending note genuinely follows an existing frame.
        create_page(authority, 'owner', ws, title='기존 하위 페이지', parent_id=source,
            base_version=store.get_workspace('owner', ws)['version'], operation_id='parent-rebase-browser-bootstrap')
        original = current_body(authority, source)
        before = authority.read_document("owner", source)
        authority.execute_typed_command("owner", source, command_kind="block.insert", target_id="pending-browser-note",
            payload={"type": "paragraph", "content": "추가한 문단은 하위 페이지 생성 뒤에도 유지",
                     "props": {"rich_text_html": "<em>추가한 문단은 하위 페이지 생성 뒤에도 유지</em>"}, "after_block_id": None},
            operation_id="browser-pending-parent-note", expected_document_sequence=before["document_sequence"],
            expected_workspace_epoch=before["workspace_epoch"], expected_document_epoch=before["document_epoch"])
        note = next(block for block in authority.read_document("owner", source)["blocks"] if block["id"] == "pending-browser-note")
        assert all(block["id"] != note["id"] for block in current_body(authority, source)["blocks"])
        errors = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": width, "height": 844})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + source, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner"); page.locator("#password").fill(password); page.locator("#submitBtn").click()
                page.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration.crdtReady && State.navigation.collaborativePageCreation", timeout=30_000)
                page.wait_for_function("() => !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive", timeout=30_000)
                page.evaluate("theme => applyColorTheme(theme)", theme)
                assert all(block['id'] != note['id'] for block in current_body(authority, source)['blocks'])
                if width <= 720:
                    page.locator("#sidebarRestoreBtn").click()
                row = page.locator(f'#ownPages .page-row[data-page-id="{source}"]')
                row.hover()
                with page.expect_response(f"**/api/notion/workspaces/{ws}/pages") as response:
                    row.locator('[data-page-child]').click()
                    expect(page.locator('#sidePeekTitle')).to_be_visible()
                    page.locator('#sidePeekTitle').fill('문단 보존 확인용 하위 페이지')
                assert response.value.status == 201, response.value.text()
                result = response.value.json()
                child = result['page']['id']
                page.wait_for_function("id => State.sidePeek.pageId === id && !State.sidePeek.dirty && !State.sidePeek.composing", arg=child, timeout=30_000)
                if width <= 720:
                    page.locator('#sidePeekCloseBtn').click()
                else:
                    page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
                expect(page.locator('#sidePeek')).to_be_hidden(timeout=30_000)
                expect(page.locator('#block-pending-browser-note')).to_contain_text(note['content'], timeout=30_000)
                after = current_body(authority, source)
                assert next(block for block in after['blocks'] if block['id'] == note['id']) == {key: value for key, value in note.items() if key != 'position'}
                links = [block for block in after['blocks'] if block['props'].get('page_id') == child]
                assert len(links) == 1
                assert [block for block in after['blocks'] if block['id'] not in {note['id'], links[0]['id']}] == original['blocks']
                assert after['title'] == original['title']
                page.reload(wait_until='domcontentloaded')
                page.wait_for_function("() => State.liveCollaboration.crdtReady", timeout=30_000)
                expect(page.locator('#block-pending-browser-note')).to_contain_text(note['content'], timeout=30_000)
                assert page.locator('#block-pending-browser-note em').inner_text() == note['content']
                assert current_body(authority, source) == after
                assert errors == []
                packet = Path(os.environ['CODE_ASSIST_PARENT_REBASE_EVIDENCE_DIR']) if os.environ.get('CODE_ASSIST_PARENT_REBASE_EVIDENCE_DIR') else None
                if packet:
                    packet.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=os.fspath(packet / f'parent-note-{theme}-{width}.png'), full_page=False)
                    (packet / f'browser-{theme}-{width}.json').write_text(json.dumps({
                        'theme': theme, 'width': width, 'native_create_request': response.value.request.post_data_json,
                        'child_id': child, 'pending_note_retained': True, 'rich_markup_retained': True,
                        'original_body_and_title_retained': True, 'one_parent_link': True,
                        'after_reload_identical': True, 'errors': errors,
                        'parent_adoption_capability': store.capabilities()['collaborative_synced_legacy_adoption'],
                    }, ensure_ascii=False, indent=2), encoding='utf-8')
            finally:
                browser.close()
