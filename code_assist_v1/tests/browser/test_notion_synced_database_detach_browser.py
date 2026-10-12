"""UI-SYNC-DB-DETACH: rendered bodies, uncertain receipts and physical owners."""
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from code_assist_v1.tests.browser.synced_toolbar_helpers import open_synced_actions

from code_assist_v1.notion_collaboration import synced_blocks as synced
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.browser.test_notion_synced_linked_navigation import login, settled, source_database
from code_assist_v1.tests.browser.test_notion_synced_linked_template_mount import FLAGS, prepare as prepare_linked, open_template
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original, reference

DETACH_FLAGS={**FLAGS,'navigation_database_favorites':True,'synced_detach':True}


def proof(page,name,tmp_path,errors,details=None):
    output=Path(os.environ.get('NOTION_SYNC_DB_DETACH_ARTIFACT_DIR',str(tmp_path)));output.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(output/(name+'.png')))
    (output/(name+'.json')).write_text(json.dumps({'url':page.url,'page_errors':errors,'details':details},ensure_ascii=False,indent=2),encoding='utf-8')


def open_owned(page,database,target,kind):
    from playwright.sync_api import expect
    block=page.evaluate("id=>State.pageData.blocks.find(b=>b.props?.database_id===id&&!b.props?.linked_database)?.id",database)
    table=page.locator(f'[data-block-id="{block}"]')
    if kind=='row':
        table.locator('[data-table-row="0"].database-open-page').click();root=page.locator('[data-item-content]')
    else:
        table.locator('[data-db-template-picker]').click();table.locator(f'[data-db-template-more="{target}"]').click()
        table.locator(f'[data-db-template-edit="{target}"]').click();root=page.locator('#dbTemplateBodyEditor')
    expect(root).to_be_visible(timeout=30_000);return root


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('kind,mode,theme,width',[
    ('row','clean','light',1280),('template','clean','light',1280),
    ('row','clean','dark',390),('template','clean','dark',390),
    ('row','lost','dark',390),('template','lost','dark',390)])
def test_owned_database_detach_keeps_source_and_other_reference_then_saves_ordinary_body(tmp_path,kind,mode,theme,width):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect,sync_playwright
    with _isolated_collaborative_app(tmp_path,**DETACH_FLAGS) as (origin,password,authority,workspace,initial):
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='chrome',headless=True)
            try:
                page=browser.new_page(locale='ko-KR',viewport={'width':width,'height':844});errors=[];requests=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('request',lambda req:requests.append(req.post_data_json) if req.method=='POST' and req.url.endswith('/synced-blocks/body-detach-peer/detach') else None)
                login(page,origin,initial,'owner',password);page.evaluate('theme=>applyColorTheme(theme)',theme)
                source=source_database(page,initial);document,database=source['pageId'],source['databaseId'];store=SimpleNamespace(iam=authority)
                blocks=[original('body-detach-original','원본 A'),reference('body-detach-peer',document,'body-detach-original'),
                    reference('body-detach-keep',document,'body-detach-original'),
                    {'id':'body-detach-ordinary','type':'paragraph','content':'일반 본문 A','props':{},'position':3}]
                if kind=='row':
                    row=authority.read_database('owner',database)['rows'][0];target=row['id']
                    authority.execute_surface_command('owner',document,database_id=database,command_kind='database.row.content.set',target_id=target,
                        payload={'blocks':blocks},expected_target_version=row['version'],operation_id='body-detach-row-seed',**request(store,document))
                else:
                    target='body-detach-template'
                    authority.execute_schema_command('owner',document,database_id=database,command_kind='database.template.create',target_id=target,
                        payload={'name':'동기화 해제 검증','body':blocks,'default_values':{}},expected_target_version=0,
                        operation_id='body-detach-template-seed',**request(store,document))
                page.reload(wait_until='domcontentloaded');settled(page);root=open_owned(page,database,target,kind)
                peer=root.locator('[data-synced-reference="body-detach-peer"]');button=peer.locator('[data-synced-detach]')
                try:
                    open_synced_actions(peer);expect(button).to_be_visible(timeout=30_000);expect(button).to_be_enabled()
                    expect(peer.locator('.synced-reference-content')).to_contain_text('원본 A')
                    before=authority.read_database('owner',database)
                    with authority._reader() as con:
                        main=authority._editing_snapshot(con,authority._document(con,document))['blocks']
                        frames=synced._frames(con,document,synced._generation(con,document))
                    if mode=='lost':
                        def lose(route):
                            response=route.fetch();assert response.status==201,response.text();route.abort('failed')
                        page.route('**/synced-blocks/body-detach-peer/detach',lose)
                        button.click()
                        page.wait_for_function("() => getSyncedDetachController().list(syncedDetachScope()).some(e=>e.blockId==='body-detach-peer'&&!e.confirmed)",timeout=30_000)
                        # Recovery may occur inside the old slot or after the
                        # canonical observer has already replaced that slot.
                        if peer.count():
                            expect(button).to_have_text('해제 결과 확인 · 재시도',timeout=30_000)
                            expect(button).to_be_enabled(timeout=30_000);open_synced_actions(peer);button.click()
                        else:
                            page.locator('[data-linked-item-close]' if kind=='row' else '#modalClose').click()
                            page.locator('#syncedDetachStatus button').filter(has_text='결과 확인').click()
                            root=open_owned(page,database,target,kind)
                        page.unroute('**/synced-blocks/body-detach-peer/detach',lose)
                    else:
                        with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/synced-blocks/body-detach-peer/detach')) as detached:
                            button.click()
                        assert detached.value.status==201,detached.value.text()
                        assert detached.value.json()['storage']=='database_body'
                    expect(root.locator('[data-synced-reference="body-detach-peer"]')).to_have_count(0,timeout=30_000)
                    expect(root.locator('[data-body-text="body-detach-peer"]')).to_have_text('원본 A',timeout=30_000)
                    expect(root.locator('[data-synced-reference="body-detach-keep"] .synced-reference-content')).to_contain_text('원본 A',timeout=30_000)
                    expect(root.locator('[data-body-text="body-detach-ordinary"]')).to_have_attribute('contenteditable','true',timeout=30_000)
                    current=authority.read_database('owner',database)
                    value=next(item for item in current['rows' if kind=='row' else 'templates'] if item['id']==target)
                    old=next(item for item in before['rows' if kind=='row' else 'templates'] if item['id']==target)
                    assert value['version']==old['version']+1
                    raw=value['body']['blocks'] if kind=='row' else value['body']
                    old_raw=old['body']['blocks'] if kind=='row' else old['body']
                    assert synced.index_blocks(raw)['body-detach-original']==synced.index_blocks(old_raw)['body-detach-original']
                    with authority._reader() as con:
                        assert authority._editing_snapshot(con,authority._document(con,document))['blocks']==main
                        assert synced._frames(con,document,synced._generation(con,document))==frames
                    with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/databases/commands')) as saved:
                        root.locator('[data-body-text="body-detach-ordinary"]').fill('일반 본문 B')
                    assert saved.value.status==201,saved.value.text()
                    expect(root.locator('[data-synced-reference="body-detach-keep"] [data-synced-text="body-detach-original"]')).to_have_attribute('contenteditable','true',timeout=30_000)
                    with page.expect_response('**/synced-blocks/body-detach-keep/text') as edited:
                        root.locator('[data-synced-reference="body-detach-keep"] [data-synced-text="body-detach-original"]').fill('원본 C')
                    assert edited.value.status==201,edited.value.text()
                    expect(root.locator('[data-body-text="body-detach-original"]')).to_have_text('원본 C',timeout=30_000)
                    expect(root.locator('[data-body-text="body-detach-peer"]')).to_have_text('원본 A')
                    page.wait_for_function("() => !ItemBodyUi?.synced?.pending()&&!ActiveDatabaseTemplateEditor?.dirty?.()",timeout=30_000)
                    assert len(requests)==1
                    assert not get_pending(page,'body-detach-peer') and not errors
                    proof(page,f'detach-{kind}-{mode}-{theme}-{width}',tmp_path,errors,{'detached_requests':requests,'target':target})
                except Exception:
                    proof(page,f'detach-failure-{kind}-{mode}-{theme}-{width}',tmp_path,errors,
                          page.evaluate("() => ({enabled:State.navigation.collaborativeSyncedDetach,status:document.querySelector('#syncedDetachStatus')?.textContent,body:document.querySelector('[data-item-content],#dbTemplateBodyEditor')?.innerHTML,pending:getSyncedDetachController().list(syncedDetachScope())})"))
                    raise
            finally:browser.close()


def get_pending(page,identity):
    return page.evaluate("id=>getSyncedDetachController().list(syncedDetachScope()).some(e=>e.blockId===id&&!e.confirmed)",identity)


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
def test_linked_template_detach_uses_physical_owner_and_keeps_visible_page_unchanged(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect,sync_playwright
    with _isolated_collaborative_app(tmp_path,**DETACH_FLAGS) as (origin,password,authority,workspace,initial):
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='chrome',headless=True)
            try:
                page=browser.new_page(locale='ko-KR',viewport={'width':390,'height':844});errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                login(page,origin,initial,'owner',password);page.evaluate("applyColorTheme('dark')")
                source,visible=prepare_linked(page,authority,initial);document=source['pageId'];database=source['databaseId']
                root=open_template(page);button=root.locator('[data-synced-reference="linked-template-peer"] [data-synced-detach]')
                open_synced_actions(root.locator('[data-synced-reference="linked-template-peer"]'));expect(button).to_be_visible(timeout=30_000)
                before=deepcopy(authority.read_document('owner',visible))
                with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/synced-blocks/linked-template-peer/detach')) as detached:button.click()
                result=detached.value.json();assert detached.value.status==201,result
                assert result['document_id']==document and result['location']['template_id']=='linked-sync-template'
                expect(root.locator('[data-body-text="linked-template-peer"]')).to_have_text('템플릿 원본 A',timeout=30_000)
                expect(root.locator('[data-body-text="linked-template-ordinary"]')).to_have_attribute('contenteditable','true',timeout=30_000)
                root.locator('[data-body-text="linked-template-ordinary"]').fill('해제한 뒤 일반 본문 B')
                with page.expect_response(lambda response:response.request.method=='PATCH' and '/templates/' in response.url) as saved:page.locator('#modalActions button').click()
                assert saved.value.status==200,saved.value.text()
                assert authority.read_document('owner',visible)==before and page.evaluate('State.activePageId')==visible
                latest=authority.read_database('owner',database)['templates'][0]
                index=synced.index_blocks(latest['body'])
                assert index['linked-template-original']['content']==index['linked-template-peer']['content']=='템플릿 원본 A'
                assert index['linked-template-ordinary']['content']=='해제한 뒤 일반 본문 B'
                assert index['linked-template-peer']['type']=='paragraph' and not errors
                proof(page,'detach-linked-template-dark-390',tmp_path,errors,result)
            except Exception:
                proof(page,'detach-linked-template-failure',tmp_path,errors);raise
            finally:browser.close()
