"""UI-ROW-PAGE-INSERT: real row/page menus retain a pending parent paragraph."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration.database_row_page_move import move_owned_rows_to_page
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.page_creation import create_page
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.notion_collaboration.test_page_link_parent_insert_rebase import note, body, assert_note


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('direction,theme,width', [('out', 'light', 1280), ('back', 'dark', 390)])
def test_row_page_menu_retains_pending_parent_note(tmp_path, direction, theme, width):
    from playwright.sync_api import expect, sync_playwright
    with _isolated_collaborative_app(tmp_path, locked=False, navigation_ui=True,
            navigation_database_favorites=True, owned_database_copy=True, database_activity=True,
            owned_database_trash=True, relation_pair=True, row_page_placement=True) as (origin, password, authority, ws, source):
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root, identity_directory=authority.identity_directory)
        parent = create_page(authority, 'owner', ws, title='새 문단 보존 부모', parent_id='',
            base_version=store.get_workspace('owner', ws)['version'], operation_id='browser-row-note-parent')['page']['id']
        create_page(authority, 'owner', ws, title='기존 하위 페이지', parent_id=parent,
            base_version=store.get_workspace('owner', ws)['version'], operation_id='browser-row-note-anchor')
        errors = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': width, 'height': 844})
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin + '/page/' + source, wait_until='domcontentloaded')
                page.locator('#userId').fill('owner'); page.locator('#password').fill(password); page.locator('#submitBtn').click()
                page.wait_for_function('() => typeof State !== "undefined" && State.navigation.collaborativeDatabaseRowToPageBatch && State.liveCollaboration.crdtReady', timeout=30_000)
                # Existing editor helper creates the fixture DB; tested placement
                # and return below use real visible controls, without route stubs.
                database = page.evaluate("() => {insertBlockAfter(State.pageData.blocks.length-1,'table'); return State.pageData.blocks.find(b=>b.type==='table').id;}")
                page.wait_for_function('() => !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive', timeout=30_000)
                page.reload(wait_until='domcontentloaded')
                add = page.locator(f'[data-block-id="{database}"] [data-database-add]')
                for count in [1, 2]:
                    expect(add).to_be_visible(timeout=30_000)
                    add.click()
                    page.wait_for_function('({id,count}) => State.pageData.databases.find(db=>db.id===id)?.rows.length===count && !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive',
                        arg={'id': database, 'count': count}, timeout=30_000)
                rows = authority.read_database('owner', database)['rows']
                row_ids = [row['id'] for row in rows]
                if direction == 'back':
                    source_page = authority.read_document('owner', source)
                    parent_page = authority.read_document('owner', parent)
                    move_owned_rows_to_page(authority, actor_subject_id='owner', workspace_id=ws,
                        source_document_id=source, source_database_id=database, parent_document_id=parent,
                        rows=[{'row_id': row['id'], 'expected_target_version': row['version']} for row in rows],
                        expected_source_sequence=source_page['document_sequence'], expected_parent_sequence=parent_page['document_sequence'],
                        expected_workspace_epoch=source_page['workspace_epoch'], expected_source_epoch=source_page['document_epoch'],
                        expected_parent_epoch=parent_page['document_epoch'], operation_id='browser-row-note-prepare-return')
                original = body(store, parent)
                pending = note(store, 'pending-browser-row-page-parent-note', parent)
                assert all(block['id'] != pending['id'] for block in body(store, parent)['blocks'])
                if direction == 'out':
                    page.reload(wait_until='domcontentloaded')
                    page.wait_for_function('() => State.liveCollaboration.crdtReady', timeout=30_000)
                    page.evaluate('theme=>applyColorTheme(theme)', theme)
                    grid = page.locator(f'[data-block-id="{database}"] .dynamic-table-grid')
                    grid.scroll_into_view_if_needed()
                    points = grid.evaluate('''grid => {
                      const rect=grid.getBoundingClientRect();
                      const rows=[...grid.querySelectorAll('.table-row:not(.table-header)')].map(row=>row.getBoundingClientRect());
                      return {x:rect.left-20,y:rows[0].top+4,endX:rect.left+30,endY:rows.at(-1).bottom-4};
                    }''')
                    page.mouse.move(points['x'], points['y']); page.mouse.down()
                    page.mouse.move(points['endX'], points['endY'], steps=10); page.mouse.up()
                    toolbar = page.locator(f'[data-block-id="{database}"] .database-row-selection-toolbar')
                    expect(toolbar).to_contain_text('2개 선택됨')
                    toolbar.locator('[data-selected-row-more]').click()
                    toolbar.locator('[data-move-selected-rows]').click()
                    picker = page.locator('.database-row-move-picker')
                    expect(picker).to_be_visible()
                    picker.locator('input').fill('새 문단 보존 부모')
                    picker.locator(f'[data-destination-page="{parent}"]').click()
                    with page.expect_response('**/databases/commands') as response:
                        picker.locator(f'[data-destination-ordinary-page="{parent}"]').click()
                    expect(picker).to_have_count(0, timeout=30_000)
                else:
                    page.goto(origin + '/page/' + row_ids[0], wait_until='domcontentloaded')
                    page.wait_for_function('id => State.activePageId===id && State.liveCollaboration.crdtReady', arg=row_ids[0], timeout=30_000)
                    page.evaluate('theme=>applyColorTheme(theme)', theme)
                    page.locator('#pageMoreBtn').click()
                    page.locator('#pageMenu [data-page-action="move"]').click()
                    picker = page.locator('#movePageMenu')
                    target = picker.locator(f'[data-return-to-database="{database}"]')
                    expect(target).to_be_visible(timeout=30_000)
                    with page.expect_response('**/return-to-database') as response:
                        target.click()
                    expect(picker).to_be_hidden(timeout=30_000)
                assert response.value.status == (201 if direction == 'out' else 200)
                request = response.value.request.post_data_json
                current = body(store, parent)
                assert_note(current, pending)
                assert current['title'] == original['title']
                kept = [block for block in current['blocks'] if block['id'] != pending['id']]
                if direction == 'out':
                    assert kept[:len(original['blocks'])] == original['blocks']
                    assert [block['props']['page_id'] for block in kept[len(original['blocks']):]] == row_ids
                    assert authority.read_database('owner', database)['rows'] == []
                else:
                    assert kept == [block for block in original['blocks'] if block['props'].get('page_id') != row_ids[0]]
                    assert [row['id'] for row in authority.read_database('owner', database)['rows']] == [row_ids[0]]
                page.goto(origin + '/page/' + parent, wait_until='domcontentloaded')
                page.wait_for_function('() => State.liveCollaboration.crdtReady', timeout=30_000)
                page.reload(wait_until='domcontentloaded')
                page.wait_for_function('() => State.liveCollaboration.crdtReady', timeout=30_000)
                expect(page.locator('#block-pending-browser-row-page-parent-note em')).to_have_text(pending['content'], timeout=30_000)
                assert body(store, parent) == current and errors == []
                packet = os.environ.get('CODE_ASSIST_ROW_PAGE_REBASE_EVIDENCE_DIR')
                if packet:
                    output = Path(packet); output.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=os.fspath(output / f'{direction}-{theme}-{width}.png'))
                    (output / f'browser-{direction}.json').write_text(json.dumps({
                        'direction': direction, 'theme': theme, 'width': width, 'request': request,
                        'http_status': response.value.status, 'pending_note_and_rich_markup_retained': True,
                        'original_body_and_title_retained': True, 'exact_selected_links': True,
                        'after_reload_identical': True, 'errors': errors,
                        'operating_capability_enabled': False}, ensure_ascii=False, indent=2), encoding='utf-8')
            finally:
                browser.close()
