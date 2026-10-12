"""UI-LINK-INSERT: real sidebar lifecycle menus retain post-frame notes."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration import page_archive
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.page_creation import create_page
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.notion_collaboration.test_page_archive import request as archive_request
from code_assist_v1.tests.notion_collaboration.test_page_link_parent_insert_rebase import note, body, assert_note


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("action,theme,width", [
    ("move", "light", 1280), ("copy", "dark", 390),
    ("archive", "light", 1280), ("restore", "dark", 390),
])
def test_sidebar_lifecycle_keeps_pending_parent_note_after_reload(tmp_path, action, theme, width):
    from playwright.sync_api import expect, sync_playwright
    with _isolated_collaborative_app(tmp_path, locked=False,
            navigation_database_favorites=True, navigation_ui=True, page_copy=True) as (origin, password, authority, ws, source):
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root, identity_directory=authority.identity_directory)
        def create(title, parent, operation):
            return create_page(authority, 'owner', ws, title=title, parent_id=parent,
                base_version=store.get_workspace('owner', ws)['version'], operation_id=operation)['page']['id']
        child = create('문단 보존 확인용 페이지', source, 'browser-link-child')
        create('기존 위치 확인용 페이지', source, 'browser-link-anchor')
        if action == 'restore':
            page_archive.set_page_archived(authority, 'owner', ws, child,
                **archive_request(store, ws, child, operation='browser-prepare-restore'))
        original = body(store, source)
        pending = note(store, 'pending-browser-lifecycle-note', source)
        if action == 'move':
            destination = create('문단 보존 이동 대상', '', 'browser-link-destination')
            create('이동 대상의 기존 페이지', destination, 'browser-destination-bootstrap')
            destination_before = body(store, destination)
            destination_note = note(store, 'pending-browser-destination-note', destination)
        assert all(block['id'] != pending['id'] for block in body(store, source)['blocks'])
        errors = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': width, 'height': 844})
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('dialog', lambda dialog: dialog.accept())
                page.goto(origin + '/page/' + source, wait_until='domcontentloaded')
                page.locator('#userId').fill('owner')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                page.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration.crdtReady && State.navigation.collaborativePageCreation", timeout=30_000)
                page.wait_for_function("() => !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive", timeout=30_000)
                page.evaluate('theme => applyColorTheme(theme)', theme)
                assert all(block['id'] != pending['id'] for block in body(store, source)['blocks'])
                if width <= 720:
                    page.locator('#sidebarRestoreBtn').click()
                if action == 'restore':
                    page.locator('#trashBtn').click()
                    restore = page.locator(f'#trashResults [data-restore="{child}"]')
                    expect(restore).to_be_visible(timeout=30_000)
                    dialog = page.locator('#trashOverlay .trash-dialog')
                    box = dialog.bounding_box()
                    button_box = restore.bounding_box()
                    assert box and 0 <= box['x'] and box['x'] + box['width'] <= width
                    assert button_box and 0 <= button_box['x'] and button_box['x'] + button_box['width'] <= width
                    packet = os.environ.get('CODE_ASSIST_PAGE_LINK_REBASE_EVIDENCE_DIR')
                    if packet:
                        output = Path(packet)
                        output.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=os.fspath(output / 'restore-dialog-dark-390.png'), full_page=False)
                    endpoint = f'/api/notion/workspaces/{ws}/trash/{child}/restore'
                    with page.expect_response('**' + endpoint) as response:
                        restore.click()
                else:
                    row = page.locator(f'#ownPages .page-row[data-page-id="{child}"]')
                    row.hover()
                    row.locator('[data-page-menu]').click()
                    menu_action = {'move': 'move', 'copy': 'duplicate', 'archive': 'trash'}[action]
                    endpoint = f'/api/notion/workspaces/{ws}/pages/{child}/{menu_action}'
                    with page.expect_response('**' + endpoint) as response:
                        page.locator(f'#pageMenu [data-page-action="{menu_action}"]').click()
                        if action == 'move':
                            expect(page.locator('#movePageMenu')).to_be_visible()
                            page.locator('#movePageSearch').fill('문단 보존 이동 대상')
                            page.locator(f'#movePageResults [data-move-parent="{destination}"]').click()
                assert 200 <= response.value.status < 300, response.value.text()
                result = response.value.json()
                request = response.value.request.post_data_json
                assert request['operation_id']
                expect(page.locator('#pageMenu')).to_be_hidden(timeout=30_000)
                if action == 'move':
                    expect(page.locator('#movePageMenu')).to_be_hidden(timeout=30_000)
                    expect(page.locator(f'#ownPages .page-row[data-page-id="{child}"][data-parent-id="{destination}"]')).to_be_visible(timeout=30_000)
                elif action == 'copy':
                    expect(page.locator('#pageCopyStatus')).to_contain_text('페이지 복제가 완료되었습니다', timeout=30_000)
                elif action == 'archive':
                    expect(page.locator(f'#ownPages .page-row[data-page-id="{child}"]')).to_have_count(0, timeout=30_000)
                else:
                    expect(page.locator('#trashOverlay')).to_be_hidden(timeout=30_000)
                    expect(page.locator(f'#ownPages .page-row[data-page-id="{child}"]')).to_have_count(1, timeout=30_000)
                current = body(store, source)
                assert_note(current, pending)
                assert current['title'] == original['title']
                visible_old = {block['id']: block for block in original['blocks']}
                if action in {'move', 'archive'}:
                    visible_old = {key: block for key, block in visible_old.items() if block['props'].get('page_id') != child}
                assert {block['id']: block for block in current['blocks'] if block['id'] in visible_old} == visible_old
                linked_child = result['page']['id'] if action == 'copy' else child
                count = sum(block['type'] == 'child_page' and block['props'].get('page_id') == linked_child for block in current['blocks'])
                assert count == (0 if action in {'move', 'archive'} else 1)
                if action == 'move':
                    moved = body(store, destination)
                    assert_note(moved, destination_note)
                    assert moved['title'] == destination_before['title']
                    moved_ids = {block['id'] for block in destination_before['blocks']}
                    assert [block for block in moved['blocks'] if block['id'] in moved_ids] == destination_before['blocks']
                    assert sum(block['props'].get('page_id') == child for block in moved['blocks']) == 1
                # Copy/restore may open the selected page. Return through the
                # ordinary URL and then reload the parent to verify durable UI.
                page.goto(origin + '/page/' + source, wait_until='domcontentloaded')
                page.wait_for_function("() => State.liveCollaboration.crdtReady", timeout=30_000)
                page.reload(wait_until='domcontentloaded')
                page.wait_for_function("() => State.liveCollaboration.crdtReady", timeout=30_000)
                expect(page.locator('#block-pending-browser-lifecycle-note em')).to_have_text(pending['content'], timeout=30_000)
                assert body(store, source) == current
                assert errors == []
                packet = os.environ.get('CODE_ASSIST_PAGE_LINK_REBASE_EVIDENCE_DIR')
                if packet:
                    output = Path(packet)
                    output.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=os.fspath(output / f'{action}-{theme}-{width}.png'), full_page=False)
                    (output / f'browser-{action}.json').write_text(json.dumps({
                        'action': action, 'theme': theme, 'width': width, 'request': request,
                        'http_status': response.value.status, 'pending_note_retained': True,
                        'rich_markup_retained': True, 'original_body_and_title_retained': True,
                        'exact_link_count': count, 'after_reload_identical': True, 'errors': errors,
                        'parent_adoption_capability': store.capabilities()['collaborative_synced_legacy_adoption'],
                    }, ensure_ascii=False, indent=2), encoding='utf-8')
            finally:
                browser.close()
