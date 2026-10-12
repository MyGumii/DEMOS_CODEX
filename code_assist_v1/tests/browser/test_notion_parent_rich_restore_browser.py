"""UI-PARENT-RESTORE: actual sidebar child action retains a restored paragraph."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration import search_projection
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.page_creation import create_page
from code_assist_v1.notion_collaboration.title_projection import _frames, _generation
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.notion_collaboration.test_page_link_parent_insert_rebase import note, body
from code_assist_v1.tests.notion_collaboration.test_parent_structure_rebase import structural
from code_assist_v1.tests.notion_collaboration.test_parent_rich_restore_rebase import edit_yjs
from code_assist_v1.tests.notion_collaboration.test_page_copy_source import request
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import text_update


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('theme,width', [('light', 1280), ('dark', 390)])
@pytest.mark.parametrize('historical', [False, True])
def test_real_sidebar_child_creation_keeps_restored_paragraph_and_exact_rich_attributes(tmp_path, theme, width, historical):
    from playwright.sync_api import expect, sync_playwright
    with _isolated_collaborative_app(tmp_path, locked=False,
            navigation_database_favorites=True, navigation_ui=True) as (origin, password, authority, ws, source):
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root, identity_directory=authority.identity_directory)
        root = authority.read_document('owner', source)['blocks'][0]['id']
        victim = 'browser-restored-rich'
        note(store, victim, source, after=root)
        create_page(authority, 'owner', ws, title='기존 하위 페이지', parent_id=source,
            base_version=store.get_workspace('owner', ws)['version'], operation_id='browser-restore-bootstrap')
        edit_yjs(store, ws, victim, 'format', document=source)
        original = body(store, source)
        block = next(block for block in original['blocks'] if block['id'] == victim)
        structural(store, 'delete', victim, document=source)
        edit_yjs(store, ws, victim, 'delete', document=source)
        authority.execute_typed_command('owner', source, command_kind='block.insert', target_id=victim,
            payload={**{key: block[key] for key in ('type', 'content', 'props')},
                'after_block_id': root, 'restore_deleted': True},
            operation_id='browser-restore-command', **request(store, source))
        if historical:
            text_update(store, ws, source, root, '복원 뒤에도 남아 있는 최신 문단')
            live = {block['id']: block for block in body(store, source)['blocks']}
            original = {**original, 'blocks': [live.get(block['id'], block) for block in original['blocks']]}
        pending = body(store, source)
        assert all(block['id'] != victim for block in pending['blocks'])
        errors = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            failure = None
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': width, 'height': 844})
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin + '/page/' + source, wait_until='domcontentloaded')
                page.locator('#userId').fill('owner'); page.locator('#password').fill(password); page.locator('#submitBtn').click()
                page.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration.crdtReady && State.navigation.collaborativePageCreation", timeout=30_000)
                page.wait_for_function("() => !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive", timeout=30_000)
                page.evaluate('theme => applyColorTheme(theme)', theme)
                assert body(store, source) == pending
                if width <= 720:
                    page.locator('#sidebarRestoreBtn').click()
                row = page.locator(f'#ownPages .page-row[data-page-id="{source}"]'); row.hover()
                with page.expect_response(f'**/api/notion/workspaces/{ws}/pages') as response:
                    row.locator('[data-page-child]').click()
                    expect(page.locator('#sidePeekTitle')).to_be_visible()
                    page.locator('#sidePeekTitle').fill('서식 복원 검증용 하위 페이지')
                assert response.value.status == 201, response.value.text()
                child = response.value.json()['page']['id']
                page.wait_for_function("id => State.sidePeek.pageId === id && !State.sidePeek.dirty && !State.sidePeek.composing", arg=child, timeout=30_000)
                if width <= 720:
                    page.locator('#sidePeekCloseBtn').click()
                else:
                    page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
                expect(page.locator('#sidePeek')).to_be_hidden(timeout=30_000)
                current = body(store, source)
                assert current['blocks'][:-1] == original['blocks'] and current['title'] == original['title']
                page.reload(wait_until='domcontentloaded')
                page.wait_for_function("() => State.liveCollaboration.crdtReady", timeout=30_000)
                expect(page.locator(f'#block-{victim} em')).to_have_text(block['content'])
                assert page.locator('#blocks .block-row').evaluate_all("rows => rows.map(row => row.dataset.blockId)") == [block['id'] for block in current['blocks']]
                with authority._reader() as con:
                    frames = _frames(con, source, _generation(con, source))
                rich = search_projection.materialize([frames], rich=True)[0]
                assert rich['rich_text']['blocks'][victim][0]['attributes']['bold'] is True
                assert body(store, source) == current and errors == []
                evidence = os.environ.get('CODE_ASSIST_RICH_RESTORE_EVIDENCE_DIR')
                if evidence:
                    output = Path(evidence); output.mkdir(parents=True, exist_ok=True)
                    prefix = 'historical' if historical else 'restore'
                    page.screenshot(path=os.fspath(output / f'{prefix}-{theme}-{width}.png'))
                    (output / f'browser-{prefix}-{theme}-{width}.json').write_text(json.dumps({
                        'theme': theme, 'width': width, 'historical': historical, 'http_status': response.value.status,
                        'exact_body_title_order': True, 'restored_dom_markup': True,
                        'restored_ytext_attributes': True, 'reload_preserved': True, 'errors': errors,
                        'normal_capability_defaults_changed': False,
                    }, ensure_ascii=False, indent=2), encoding='utf-8')
            except BaseException as exc:
                failure = exc
                raise
            finally:
                try:
                    browser.close()
                except Exception:
                    if failure is None:
                        raise
