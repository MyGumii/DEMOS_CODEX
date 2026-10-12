"""Staged detach: complete source/target access loss retains the exact intent."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore, collaborative_page_payload
from code_assist_v1.notion_collaboration.page_creation import create_page
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import insert_synced


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('revoked_side', ['source', 'target'])
def test_staged_synced_detach_full_access_revoke_preserves_exact_pending_intent(tmp_path, revoked_side):
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False,
            navigation_database_favorites=True, synced_detach=True) as (
                origin, password, authority, workspace_id, target_id):
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root, identity_directory=authority.identity_directory)
        assert store.capabilities()['collaborative_synced_detach'] is False
        source_id = create_page(authority, 'owner', workspace_id, title='격리된 동기화 원본',
            base_version=store.get_workspace('owner', workspace_id)['version'],
            operation_id='detach-full-access-source-create')['page']['id']
        source = authority.read_document('owner', source_id)
        authority.set_page_grant('owner', source_id, 'subject', 'content-editor', role='view',
            inherit_to_descendants=False, expected_workspace_epoch=source['workspace_epoch'],
            expected_document_epoch=source['document_epoch'], operation_id='detach-full-source-view')
        secret = '접근 회수 검증용 동기화 원문'
        insert_synced(store, source_id, 'full-access-original', content=secret)
        insert_synced(store, target_id, 'full-access-reference',
            origin={'document_id':source_id, 'block_id':'full-access-original'})
        source_before = collaborative_page_payload(authority, 'owner', source_id)
        target_before = collaborative_page_payload(authority, 'owner', target_id)
        base = f'/api/notion/workspaces/{workspace_id}/pages/{target_id}/synced-blocks/full-access-reference'
        captured, saves = [], []

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width':1280,'height':844})
                errors = []
                page.on('pageerror', lambda error:errors.append(str(error)))
                page.goto(origin + '/page/' + target_id, wait_until='domcontentloaded')
                page.locator('#userId').fill('content-editor')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                page.wait_for_function('id => typeof State !== "undefined" && State.activePageId === id'
                    ' && State.liveCollaboration.crdtReady && State.navigation.collaborativeSyncedDetach',
                    arg=target_id, timeout=30_000)
                page.locator('#block-full-access-reference [data-action="menu"]').click()
                detach = page.locator('#blockMenu [data-action="sync_detach"]')
                expect(detach).to_be_visible()
                page.evaluate('''() => {
                    const original = api;
                    window.api = (path, options={}) => original(path,
                      path.endsWith('/synced-blocks/full-access-reference') && options.cache==='no-store' && !options.signal
                        ? {...options,headers:{...options.headers,'X-Detach-Access-Check':'prepare'}} : options);
                }''')

                def revoke_after_prepare(route):
                    if route.request.headers.get('x-detach-access-check') != 'prepare':
                        route.continue_(); return
                    response = route.fetch()
                    assert response.status == 200
                    captured.append(response.json())
                    revoked_id = source_id if revoked_side == 'source' else target_id
                    document = authority.read_document('owner', revoked_id)
                    authority.revoke_page_grant('owner', revoked_id, 'subject', 'content-editor',
                        expected_workspace_epoch=document['workspace_epoch'],
                        expected_document_epoch=document['document_epoch'],
                        operation_id='detach-full-access-revoke')
                    route.fulfill(response=response)

                def observe_save(route):
                    response = route.fetch()
                    saves.append({'request':json.loads(route.request.post_data),
                                  'status':response.status,'response':response.json()})
                    route.fulfill(response=response)

                page.route('**' + base, revoke_after_prepare)
                page.route('**' + base + '/detach', observe_save)
                with page.expect_response('**' + base + '/detach'):
                    detach.click()
                assert len(captured) == len(saves) == 1
                assert saves[0]['status'] == 404
                assert secret not in json.dumps(saves[0]['response'],ensure_ascii=False)
                page.wait_for_function('() => SyncedDetachFlights.size === 0', timeout=30_000)
                pending = page.evaluate('''() => Object.entries(localStorage)
                    .filter(([key])=>key.startsWith('notion.synced-detach.v1:'))''')
                assert len(pending) == 1
                first_record = json.loads(pending[0][1])
                assert first_record['request'] == saves[0]['request']
                assert first_record['confirmed'] is False
                assert collaborative_page_payload(authority,'owner',source_id)['blocks'] == source_before['blocks']
                assert collaborative_page_payload(authority,'owner',target_id)['blocks'] == target_before['blocks']
                if revoked_side == 'target':
                    # Access loss preserves the authorized local snapshot for
                    # recovery; it cannot edit or submit the inaccessible page.
                    expect(page.locator('#pageTitle')).to_be_disabled(timeout=30_000)
                    expect(page.locator('#pageView .collaboration-recovery-banner')).to_be_visible()
                    expect(page.locator('#blocks .block-editor[contenteditable="true"]')).to_have_count(0)
                    assert page.evaluate('() => State.liveCollaboration.recoveryOnly') is True

                # A failed receipt lookup cannot discard an operation whose
                # outcome is unknown. Regrant, then reject the same stale intent
                # before preparing a fresh operation.
                page.unroute('**' + base, revoke_after_prepare)
                revoked_id = source_id if revoked_side == 'source' else target_id
                document = authority.read_document('owner',revoked_id)
                authority.set_page_grant('owner',revoked_id,'subject','content-editor',
                    role='view' if revoked_side=='source' else 'edit_content', inherit_to_descendants=False,
                    expected_workspace_epoch=document['workspace_epoch'],
                    expected_document_epoch=document['document_epoch'],operation_id='detach-full-access-regrant')
                page.goto(origin + '/page/' + target_id, wait_until='domcontentloaded')
                page.wait_for_function('id => typeof State !== "undefined" && State.activePageId === id'
                    ' && State.liveCollaboration.crdtReady && State.navigation.collaborativeSyncedDetach',
                    arg=target_id,timeout=30_000)
                assert json.loads(page.evaluate('key => localStorage.getItem(key)',pending[0][0])) == first_record
                page.locator('#block-full-access-reference [data-action="menu"]').click()
                page.locator('#blockMenu [data-action="sync_detach"]').click()
                page.wait_for_function('() => !Object.keys(localStorage).some(key=>key.startsWith("notion.synced-detach.v1:"))',timeout=30_000)
                assert len(saves)==2 and saves[1]['status']==409
                assert saves[1]['request']==saves[0]['request']
                assert collaborative_page_payload(authority,'owner',target_id)['blocks'] == target_before['blocks']
                page.wait_for_function('() => State.liveCollaboration.crdtReady && !State.liveCollaboration.recoveryOnly',timeout=30_000)
                page.locator('#block-full-access-reference [data-action="menu"]').click()
                with page.expect_response('**' + base + '/detach') as fresh:
                    page.locator('#blockMenu [data-action="sync_detach"]').click()
                assert fresh.value.status==201
                page.wait_for_function('() => State.pageData.blocks.find(block=>block.id==="full-access-reference")?.type==="paragraph"',timeout=30_000)
                assert len(saves)==3 and saves[2]['request']['operation_id']!=saves[0]['request']['operation_id']
                committed=collaborative_page_payload(authority,'owner',target_id)
                block=next(item for item in committed['blocks'] if item['id']=='full-access-reference')
                assert block['type']=='paragraph' and block['content']==secret
                assert collaborative_page_payload(authority,'owner',source_id)['blocks']==source_before['blocks']
                assert not errors,errors
                if directory := os.environ.get('NOTION_DETACH_ACCESS_ARTIFACT_DIR'):
                    target=Path(directory);target.mkdir(parents=True,exist_ok=True)
                    page.screenshot(path=str(target / f'detach-full-access-{revoked_side}.png'))
            finally:
                browser.close()
