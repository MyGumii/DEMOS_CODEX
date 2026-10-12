"""UI-SYNC-BRIDGE: canonical item writes overlap an already-ready main editor.

The earlier DB-body fixture required a reload before a raw bridge insertion.
Keep that exact order without reloading and also exercise the supported native
clipboard UI, recording bridge readiness and local/remote mutation ownership.
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.browser.test_notion_synced_database_detach_browser import DETACH_FLAGS
from code_assist_v1.tests.browser.test_notion_synced_clipboard import prepare, copy, current
from code_assist_v1.tests.browser.test_notion_synced_linked_navigation import login
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original


def observe(page):
    return page.evaluate("""() => ({
      active:State.activePageId, blocks:State.pageData.blocks,
      databases:State.pageData.databases, bridge:NotionCollaborativeEditor.descriptor(),
      ready:State.liveCollaboration.crdtReady, replay:State.liveCollaboration.initialReplay,
      connected:State.liveCollaboration.connected, authorized:State.liveCollaboration.writeAuthorized,
      pending:State.liveCollaboration.unsyncedCount, status:document.querySelector('#status').textContent,
      rendered:Array.from(document.querySelectorAll('#blocks .block-row')).map(n=>n.dataset.blockId),
      database_focus:databaseInputHasFocus(document.querySelector('#blocks')),
      render_deferred:DatabaseInputTransition.deferred,
      focus:{tag:document.activeElement?.tagName,id:document.activeElement?.id,
        label:document.activeElement?.getAttribute('aria-label')},
      events:window.interleaveEvents
    })""")


def proof(page, tmp_path, name, errors, details):
    root = Path(os.environ.get('NOTION_EDITOR_BRIDGE_INTERLEAVING_ARTIFACT_DIR', str(tmp_path)))
    root.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(root / (name + '.png')))
    (root / (name + '.json')).write_text(json.dumps({
        'state': observe(page), 'errors': errors, 'details': details,
    }, ensure_ascii=False, indent=2), encoding='utf-8')


def setup(page, origin, password, authority, document):
    from playwright.sync_api import expect
    login(page, origin, document, 'owner', password)
    prepare(page)
    database = page.evaluate("""() => {
      insertBlockAfter(State.pageData.blocks.length-1,'table');
      return State.pageData.blocks.find(block=>block.type==='table').id;
    }""")
    prepare(page)
    # This baseline reload predates the body write under test. No reload or
    # navigation is allowed between that write and either tested insertion.
    page.reload(wait_until='domcontentloaded')
    prepare(page)
    table = page.locator(f'[data-block-id="{database}"]')
    expect(table.locator('[data-database-add]')).to_be_visible(timeout=30_000)
    table.locator('[data-database-add]').click()
    page.wait_for_function("id => State.pageData.databases.find(d=>d.id===id)?.rows.length===1", arg=database, timeout=30_000)
    prepare(page)
    page.evaluate("""() => {
      const anchor=EditorCore.createBlock('paragraph',{id:'interleave-anchor',content:''});
      State.pageData.blocks=NotionBlockStructure.insert(State.pageData.blocks,State.pageData.blocks.length-1,[anchor]);
      markDirty();captureEditorHistory(true);renderBlocks();
      window.interleaveEvents=[];
      NotionEditorHost.subscribe(event=>interleaveEvents.push({type:event.type,
        blocks:State.pageData?.blocks.map(b=>b.id),ready:NotionCollaborativeEditor.descriptor().ready,
        replay:State.liveCollaboration.initialReplay,pending:State.liveCollaboration.unsyncedCount}));
    }""")
    prepare(page)
    return database, authority.read_database('owner', database)['rows'][0]['id']


def write_body(authority, document, database, row, text, operation):
    before = next(r for r in authority.read_database('owner', database)['rows'] if r['id'] == row)
    return authority.execute_surface_command('owner', document, database_id=database,
        command_kind='database.row.content.set', target_id=row,
        payload={'blocks': [original('interleave-original', text)]},
        expected_target_version=before['version'], operation_id=operation,
        **request(SimpleNamespace(iam=authority), document))


def same_main_block(before, after):
    if before['type'] != 'table':
        return before == after
    # Opening the item renderer materializes optional empty view defaults in
    # the client CRDT projection. Require every prior value and all canonical
    # database state; do not confuse those empty display defaults with a loss.
    a, b = json.loads(json.dumps(before)), json.loads(json.dumps(after))
    for value in (a, b):
        # The two explicit body writes advance the row version. This shadow
        # field is a derived view cache; all actual database versions/values
        # are compared against the exact post-write canonical database below.
        for row in value['props'].get('rows', []):
            row.pop('_canonical_row_version', None)
        for view in value['props'].get('views', []):
            for key in list(view):
                if view[key] in ('', None, []):
                    view.pop(key)
    return a == b


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('mode,theme,width', [
    ('raw-bridge', 'light', 1280), ('raw-bridge', 'dark', 390),
    ('clipboard', 'light', 1280), ('clipboard', 'dark', 390),
])
def test_ready_editor_insertion_after_canonical_row_write_preserves_reference_without_reload(tmp_path, mode, theme, width):
    from playwright.sync_api import expect, sync_playwright
    with _isolated_collaborative_app(tmp_path, **DETACH_FLAGS) as (origin, password, authority, workspace, document):
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            context = browser.new_context(locale='ko-KR', viewport={'width': width, 'height': 844})
            context.grant_permissions(['clipboard-read', 'clipboard-write'], origin=origin)
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            details = {}
            name = f'interleaving-{mode}-{theme}-{width}'
            try:
                database, row = setup(page, origin, password, authority, document)
                page.evaluate('theme=>applyColorTheme(theme)', theme)
                if mode == 'clipboard':
                    write_body(authority, document, database, row, '복사 시 원본 A', 'interleave-copy-source')
                    page.locator(f'[data-block-id="{database}"] [data-table-row="0"].database-open-page').click()
                    expect(page.locator('[data-synced-source="interleave-original"]')).to_be_visible(timeout=30_000)
                    copy(page, 'interleave-original')
                    # Escape first releases the copied source toolbar's focus.
                    # Use the explicit existing drawer control for this test's
                    # transition, rather than assuming one Escape closes both.
                    page.get_by_role('button', name='항목 상세 닫기', exact=True).click()
                    expect(page.locator('[data-item-content]')).not_to_be_visible()
                    prepare(page)
                baseline = current(authority, workspace, document)
                details['baseline_main'] = baseline
                details['before'] = observe(page)
                assert details['before']['bridge']['ready'] and details['before']['authorized'] and not details['before']['replay']
                write_body(authority, document, database, row, '새 원본 B', 'interleave-overlap-write')
                expected_database = authority.read_database('owner', database)
                details['expected_database'] = expected_database
                if mode == 'raw-bridge':
                    page.evaluate("""document => {
                      const ref=EditorCore.createBlock('synced_block',{id:'interleave-peer',props:{synced_from:{document_id:document,block_id:'interleave-original'}}});
                      State.pageData.blocks=NotionBlockStructure.insert(State.pageData.blocks,State.pageData.blocks.length-1,[ref]);
                      markDirty();captureEditorHistory(true);renderBlocks();
                    }""", document)
                    peer = 'interleave-peer'
                else:
                    page.locator('#block-interleave-anchor .block-editor').press('Control+v')
                    page.wait_for_function("() => State.pageData.blocks.some(b=>b.type==='synced_block'&&b.props.synced_from?.block_id==='interleave-original')", timeout=30_000)
                    peer = page.evaluate("State.pageData.blocks.find(b=>b.type==='synced_block'&&b.props.synced_from?.block_id==='interleave-original').id")
                details['immediately_after'] = observe(page)
                prepare(page)
                if mode == 'raw-bridge':
                    # A raw fixture mutates State while the row title still
                    # owns focus. This intentionally defers the body redraw;
                    # it is not evidence that the newly persisted block died.
                    details['while_database_focused'] = observe(page)
                    assert details['while_database_focused']['database_focus']
                    assert details['while_database_focused']['render_deferred']
                    assert peer not in details['while_database_focused']['rendered']
                    assert any(b['id'] == peer for b in current(authority, workspace, document))
                    page.locator('#pageTitle').click()
                content = page.locator(f'#block-{peer} .synced-reference-content')
                expect(content).to_contain_text('새 원본 B', timeout=30_000)
                fresh = current(authority, workspace, document)
                details['persisted_main'] = fresh
                details['persisted_database'] = authority.read_database('owner', database)
                mapped = {b['id']: b for b in fresh}
                assert mapped[peer]['content'] == '' and not mapped[peer]['props'].get('children')
                assert mapped[peer]['props']['synced_from'] == {'document_id': document, 'block_id': 'interleave-original'}
                for old in baseline:
                    if mode == 'clipboard' and old['id'] == 'interleave-anchor':
                        continue  # Supported paste replaces this exact empty paragraph.
                    assert same_main_block(old, mapped[old['id']])
                saved_body = authority.read_database('owner', database)['rows'][0]['body']
                assert saved_body['blocks'][0]['content'] == '새 원본 B'
                assert authority.read_database('owner', database) == expected_database
                details['persisted_main'] = fresh
                details['persisted_body'] = saved_body
                page.reload(wait_until='domcontentloaded')
                prepare(page)
                expect(content).to_contain_text('새 원본 B', timeout=30_000)
                assert current(authority, workspace, document) == fresh
                assert authority.read_database('owner', database) == expected_database
                assert not errors, errors
                proof(page, tmp_path, name, errors, details)
            except Exception:
                proof(page, tmp_path, name + '-failure', errors, details)
                raise
            finally:
                browser.close()
