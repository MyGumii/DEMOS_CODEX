"""UI-DB-LINK-INSERT: owned DB menus retain post-frame notes after reload."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration import database_trash
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.page_creation import create_page
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.notion_collaboration.test_page_link_parent_insert_rebase import note, body, assert_note
from code_assist_v1.tests.notion_collaboration.test_page_copy_source import request as fences
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import text_update


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('action,theme,width', [
    ('move', 'light', 1280), ('copy', 'dark', 390),
    ('archive', 'light', 1280), ('restore', 'dark', 390),
])
def test_owned_database_menu_keeps_parent_and_linked_host_notes(tmp_path, action, theme, width):
    from playwright.sync_api import expect, sync_playwright
    with _isolated_collaborative_app(tmp_path, locked=False,
            navigation_database_favorites=True, navigation_ui=True,
            owned_database_copy=True, owned_database_move=True,
            database_activity=True, owned_database_trash=True) as (origin, password, authority, ws, source):
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root, identity_directory=authority.identity_directory)
        def create(title, operation):
            return create_page(authority, 'owner', ws, title=title, parent_id='',
                base_version=store.get_workspace('owner', ws)['version'], operation_id=operation)['page']['id']
        errors = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': width, 'height': 844})
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin + '/page/' + source, wait_until='domcontentloaded')
                page.locator('#userId').fill('owner'); page.locator('#password').fill(password); page.locator('#submitBtn').click()
                page.wait_for_function("() => typeof State !== 'undefined' && State.navigation.collaborativeOwnedDatabaseMove && State.liveCollaboration.crdtReady", timeout=30_000)
                # Existing editor helper is fixture setup, not the UI action
                # under proof. Lifecycle writes below use real menu controls.
                database = page.evaluate("""() => {insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return State.pageData.blocks.find(block => block.type === 'table').id;}""")
                page.wait_for_function("() => !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive", timeout=30_000)
                page.reload(wait_until='domcontentloaded')
                expect(page.locator(f'[data-block-id="{database}"] [data-database-add]')).to_be_visible(timeout=30_000)
                page.locator(f'[data-block-id="{database}"] [data-database-add]').click()
                page.wait_for_function("id => State.pageData.databases.find(db => db.id === id)?.rows.length === 1 && !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive", arg=database, timeout=30_000)
                if action == 'restore':
                    database_trash.set_owned_database_trashed(authority, 'owner', ws, source, database,
                        archived=True, operation_id='browser-db-note-prepare-restore',
                        expected_database_version=authority.read_database('owner', database)['version'], **fences(store, source))
                if action == 'move':
                    destination = create('문단 보존 DB 이동 대상', 'browser-db-note-destination')
                    create_page(authority, 'owner', ws, title='대상의 기존 페이지', parent_id=destination,
                        base_version=store.get_workspace('owner', ws)['version'], operation_id='browser-db-destination-frame')
                    destination_note = note(store, 'pending-browser-db-destination-note', destination)
                    host = create('연결된 보기 문단 보존', 'browser-db-note-host')
                    host_block = authority.read_document('owner', host)['blocks'][0]['id']
                    props = {'linked_database': {'document_id': source, 'database_id': database},
                        'views': [{'id': 'host-view', 'name': 'Local view', 'type': 'table', 'config': {'filters': []}}],
                        'active_view_id': 'host-view', 'indent': 1}
                    authority.execute_typed_command('owner', host, command_kind='block.insert', target_id='browser-retarget-view',
                        payload={'type': 'table', 'content': '', 'props': props}, operation_id='browser-db-note-linked-view', **fences(store, host))
                    text_update(store, ws, host, host_block, '연결된 보기의 원래 문단')
                    host_note = note(store, 'pending-browser-db-host-note', host)
                original = body(store, source)
                pending = note(store, 'pending-browser-owned-db-note', source)
                page.reload(wait_until='domcontentloaded')
                page.wait_for_function("() => State.liveCollaboration.crdtReady && !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive", timeout=30_000)
                page.evaluate('theme => applyColorTheme(theme)', theme)
                assert all(block['id'] != pending['id'] for block in body(store, source)['blocks'])
                if width <= 720:
                    page.locator('#sidebarRestoreBtn').click()
                if action == 'restore':
                    page.locator('#trashBtn').click()
                    control = page.locator(f'#trashResults [data-restore-database="{database}"]')
                    expect(control).to_be_visible(timeout=30_000)
                    with page.expect_response(f'**/databases/{database}/restore') as response:
                        control.click()
                else:
                    row = page.locator(f'.sidebar-database-row[data-sidebar-db-block="{database}"]:not([data-sidebar-db-archived="true"])').first
                    row.hover(); row.locator('[data-sidebar-db-menu]').click()
                    endpoint = {'move': 'move', 'copy': 'copy', 'archive': 'trash'}[action]
                    with page.expect_response(f'**/databases/{database}/{endpoint}') as response:
                        if action == 'move':
                            page.locator('#pageMenu [data-page-action="move"]').click()
                            page.locator('#moveDatabaseSearch').fill('문단 보존 DB 이동 대상')
                            page.locator(f'#moveDatabaseMenu [data-move-database-destination="{destination}"]').click()
                        elif action == 'copy':
                            trigger = page.locator('#pageMenu [data-page-action="duplicate_menu"]')
                            trigger.focus(); trigger.press('ArrowRight')
                            page.locator('#databaseCopyMenu [data-database-copy-content="true"]').click()
                        else:
                            page.locator('#pageMenu [data-page-action="trash"]').click()
                assert 200 <= response.value.status < 300, response.value.text()
                result = response.value.json()
                request = response.value.request.post_data_json
                assert request['operation_id']
                if action == 'move':
                    expect(page.locator('#databaseMoveStatus')).to_contain_text('옮겼습니다', timeout=30_000)
                elif action == 'copy':
                    expect(page.locator('#databaseCopyStatus')).to_contain_text('복제가 완료되었습니다', timeout=30_000)
                else:
                    expect(page.locator('#databaseTrashStatus')).to_contain_text('복원' if action == 'restore' else '휴지통으로 이동했습니다', timeout=30_000)
                current = body(store, source)
                assert_note(current, pending)
                assert current['title'] == original['title']
                before = [block for block in original['blocks'] if block['id'] != database or action not in {'move', 'archive'}]
                excluded = {pending['id']}
                if action == 'copy':
                    excluded.add(result['block_id'])
                elif action == 'restore':
                    excluded.add(database)
                assert [block for block in current['blocks'] if block['id'] not in excluded] == before
                if action == 'move':
                    assert_note(body(store, destination), destination_note)
                    linked = body(store, host)
                    assert_note(linked, host_note)
                    view = next(block for block in linked['blocks'] if block['id'] == 'browser-retarget-view')
                    assert view['props'] == {**props, 'linked_database': {'document_id': destination, 'database_id': database}}
                page.goto(origin + '/page/' + source, wait_until='domcontentloaded')
                page.wait_for_function("() => State.liveCollaboration.crdtReady", timeout=30_000)
                page.reload(wait_until='domcontentloaded')
                page.wait_for_function("() => State.liveCollaboration.crdtReady", timeout=30_000)
                expect(page.locator('#block-pending-browser-owned-db-note em')).to_have_text(pending['content'], timeout=30_000)
                assert body(store, source) == current and errors == []
                packet = os.environ.get('CODE_ASSIST_DATABASE_LINK_REBASE_EVIDENCE_DIR')
                if packet:
                    output = Path(packet); output.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=os.fspath(output / f'{action}-{theme}-{width}.png'), full_page=False)
                    (output / f'browser-{action}.json').write_text(json.dumps({'action': action, 'theme': theme,
                        'width': width, 'request': request, 'http_status': response.value.status,
                        'pending_note_retained': True, 'rich_markup_retained': True,
                        'original_body_and_title_retained': True, 'after_reload_identical': True,
                        'linked_host_note_and_settings_retained': action == 'move', 'errors': errors,
                        'default_adoption_capability': store.capabilities()['collaborative_synced_legacy_adoption']},
                        ensure_ascii=False, indent=2), encoding='utf-8')
            finally:
                browser.close()
