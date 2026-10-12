"""UI-PARENT-STRUCTURE: real child creation retains pending deletions and moves."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.page_creation import create_page
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.notion_collaboration.test_page_link_parent_insert_rebase import note, body
from code_assist_v1.tests.notion_collaboration.test_parent_structure_rebase import structural
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import text_update


def _run_parent_structure_scenario(tmp_path, theme, width, *, historical=False):
    from playwright.sync_api import expect, sync_playwright
    with _isolated_collaborative_app(tmp_path, locked=False,
            navigation_database_favorites=True, navigation_ui=True) as (origin, password, authority, ws, source):
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root, identity_directory=authority.identity_directory)
        victim = authority.read_document('owner', source)['blocks'][0]['id']
        note(store, 'browser-retained-rich', source, after=victim)
        note(store, 'browser-moved-rich', source, after='browser-retained-rich')
        if historical:
            pending = note(store, 'browser-new-rich', source, after='browser-retained-rich')
        create_page(authority, 'owner', ws, title='기존 하위 페이지', parent_id=source,
            base_version=store.get_workspace('owner', ws)['version'], operation_id='browser-structure-bootstrap')
        original = body(store, source)
        structural(store, 'delete', victim, document=source)
        structural(store, 'move', 'browser-moved-rich', document=source, after=None)
        if historical:
            retained = next(block for block in original['blocks'] if block['id'] == 'browser-retained-rich')
            text_update(store, ws, source, retained['id'], retained['content'])
        else:
            pending = note(store, 'browser-new-rich', source, after='browser-retained-rich')
        expected = [block for block in original['blocks'] if block['id'] not in {victim, 'browser-moved-rich'}]
        expected.insert(0, next(block for block in original['blocks'] if block['id'] == 'browser-moved-rich'))
        if not historical:
            expected.insert(2, {key: value for key, value in pending.items() if key != 'position'})
        assert body(store, source) == original
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
                assert body(store, source) == original, 'browser admission must not silently repair the fixture'
                if width <= 720:
                    page.locator('#sidebarRestoreBtn').click()
                row = page.locator(f'#ownPages .page-row[data-page-id="{source}"]')
                row.hover()
                with page.expect_response(f'**/api/notion/workspaces/{ws}/pages') as response:
                    row.locator('[data-page-child]').click()
                    expect(page.locator('#sidePeekTitle')).to_be_visible()
                    page.locator('#sidePeekTitle').fill('삭제와 순서 보존 확인용 하위 페이지')
                assert response.value.status == 201, response.value.text()
                child = response.value.json()['page']['id']
                page.wait_for_function("id => State.sidePeek.pageId === id && !State.sidePeek.dirty && !State.sidePeek.composing", arg=child, timeout=30_000)
                if width <= 720:
                    page.locator('#sidePeekCloseBtn').click()
                else:
                    page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
                expect(page.locator('#sidePeek')).to_be_hidden(timeout=30_000)
                current = body(store, source)
                assert current['blocks'][:-1] == expected and current['title'] == original['title']
                assert current['blocks'][-1]['props']['page_id'] == child
                page.reload(wait_until='domcontentloaded')
                page.wait_for_function("() => State.liveCollaboration.crdtReady", timeout=30_000)
                expect(page.locator(f'#block-{victim}')).to_have_count(0, timeout=30_000)
                for identity in ['browser-retained-rich', 'browser-moved-rich', 'browser-new-rich']:
                    expect(page.locator(f'#block-{identity} em')).to_have_text(
                        next(block for block in expected if block['id'] == identity)['content'])
                assert page.locator('#blocks .block-row').evaluate_all("rows => rows.map(row => row.dataset.blockId)") == [block['id'] for block in current['blocks']]
                assert body(store, source) == current and errors == []
                packet = os.environ.get('CODE_ASSIST_PARENT_STRUCTURE_EVIDENCE_DIR')
                if packet:
                    output = Path(packet); output.mkdir(parents=True, exist_ok=True)
                    prefix = 'historical' if historical else 'structure'
                    page.screenshot(path=os.fspath(output / f'{prefix}-{theme}-{width}.png'), full_page=False)
                    (output / f'browser-{prefix}-{theme}-{width}.json').write_text(json.dumps({
                        'theme': theme, 'width': width, 'http_status': response.value.status,
                        'request': response.value.request.post_data_json, 'deleted_identity': victim,
                        'deleted_block_absent': True, 'exact_current_order': True, 'rich_text_preserved': True,
                        'title_preserved': True, 'reload_preserved': True, 'errors': errors,
                        'normal_capability_defaults_changed': False,
                        'older_than_latest_frame': historical,
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


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('theme,width', [('light', 1280), ('dark', 390)])
def test_real_sidebar_child_creation_retains_deleted_block_order_and_rich_text(tmp_path, theme, width):
    _run_parent_structure_scenario(tmp_path, theme, width)
