"""UI-SYNC-HIDDEN-ORIGINAL: a real canonical DB without a display block."""
import json
import os
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from code_assist_v1.tests.browser.synced_toolbar_helpers import open_synced_original

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.browser.test_notion_synced_linked_navigation import login, settled
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original
from code_assist_v1.notion_collaboration.page_creation import create_page


def prepare(authority,document,kind):
    store=SimpleNamespace(iam=authority);database='hidden-original-db';target='hidden-'+kind
    authority.execute_schema_command('owner',document,database_id=database,command_kind='database.create',target_id=database,
        payload={'title':'표 표시 없는 원본 DB'},expected_target_version=0,operation_id='hidden-database',**request(store,document))
    if kind=='row':
        authority.execute_surface_command('owner',document,database_id=database,command_kind='database.row.create',target_id=target,
            payload={},expected_target_version=0,operation_id='hidden-row',**request(store,document))
    def save(text,operation):
        rows=authority.read_database('owner',database)['rows' if kind=='row' else 'templates']
        current=next((item for item in rows if item['id']==target),None)
        body=[original('hidden-original',text)]
        if kind=='row':
            authority.execute_surface_command('owner',document,database_id=database,command_kind='database.row.content.set',target_id=target,
                payload={'blocks':body},expected_target_version=current['version'],operation_id=operation,**request(store,document))
        else:
            authority.execute_schema_command('owner',document,database_id=database,
                command_kind='database.template.update' if current else 'database.template.create',target_id=target,
                payload={'name':'표 없는 템플릿','body':body,'default_values':{}},expected_target_version=current['version'] if current else 0,
                operation_id=operation,**request(store,document))
    save('숨겨진 표의 원본 A','hidden-body-a')
    return database,target,save


def add_reference(page,document):
    # Service preparation has finished before the authenticated editor starts.
    # Only the reference is inserted through the established editor bridge.
    page.evaluate("""document=>{
      const ref=EditorCore.createBlock('synced_block',{id:'hidden-peer',props:{synced_from:{document_id:document,block_id:'hidden-original'}}});
      State.pageData.blocks=NotionBlockStructure.insert(State.pageData.blocks,State.pageData.blocks.length-1,[ref]);
      markDirty();captureEditorHistory(true);renderBlocks();
    }""",document)
    settled(page)


def flags(modern=False):
    value={'locked':False,'owned_database_copy':True,'navigation_ui':True}
    if modern:value.update(database_activity=True,owned_database_trash=True,relation_pair=True,custom_emoji=True,
        item_page_layout=True,template_default=True,staged_database_sharing=True)
    return value


def reference_page(authority,workspace):
    with authority._reader() as con:version=authority._workspace(con,workspace)['row_version']
    created=create_page(authority,'owner',workspace,title='원본으로 이동할 참조 페이지',parent_id='',base_version=version,operation_id='hidden-reference-page')
    visible=created['page']['id'];current=authority.read_document('owner',visible)
    authority.set_page_grant('owner',visible,'subject','content-editor',role='view',inherit_to_descendants=False,
        expected_workspace_epoch=current['workspace_epoch'],expected_document_epoch=current['document_epoch'],operation_id='hidden-reference-viewer')
    return visible


def evidence(page,name,tmp_path,errors,mutations):
    output=Path(os.environ.get('NOTION_SYNC_HIDDEN_ARTIFACT_DIR',str(tmp_path)));output.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(output/(name+'.png')))
    details=page.evaluate("""() => ({url:location.href,focus:document.activeElement?.outerHTML,
      body:document.querySelector('#syncedOriginalReadBody')?.innerText,
      status:document.querySelector('#syncedOriginalReadStatus')?.textContent,
      blocks:State.pageData?.blocks.map(b=>({id:b.id,type:b.type,database:b.props?.database_id}))})""")
    details.update(page_errors=errors,mutations=mutations)
    (output/(name+'.json')).write_text(json.dumps(details,ensure_ascii=False,indent=2),encoding='utf-8')


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('kind,modern,theme,width',[('row',False,'light',1280),('template',False,'light',1280),('row',True,'dark',390),('template',True,'dark',390)])
def test_hidden_database_original_opens_exact_readonly_body_refreshes_and_redacts_after_page_revoke(tmp_path,kind,modern,theme,width):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect,sync_playwright
    with _isolated_collaborative_app(tmp_path,**flags(modern)) as (origin,password,authority,workspace,document):
        database,target,save=prepare(authority,document,kind)
        visible=reference_page(authority,workspace)
        current=authority.read_document('owner',document)
        authority.set_page_grant('owner',document,'subject','content-editor',role='view',inherit_to_descendants=False,
            expected_workspace_epoch=current['workspace_epoch'],expected_document_epoch=current['document_epoch'],operation_id='hidden-source-viewer')
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='chrome',headless=True)
            try:
                owner=browser.new_page(locale='ko-KR');login(owner,origin,document,'owner',password);settled(owner)
                # Establish the normal first-open collaborative checkpoint
                # before the read-only navigation's no-mutation baseline.
                owner.goto(origin+'/page/'+visible,wait_until='domcontentloaded');settled(owner);add_reference(owner,document)
                before=authority.read_database('owner',database)
                page=browser.new_page(locale='ko-KR',viewport={'width':width,'height':844});errors=[];mutations=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('request',lambda r:mutations.append(r.url) if r.method in {'POST','PATCH','DELETE'} and ('/commands' in r.url or '/synced-blocks/' in r.url) else None)
                login(page,origin,visible,'content-editor',password);page.evaluate('theme=>applyColorTheme(theme)',theme)
                assert page.evaluate("id=>State.pageData.blocks.some(b=>b.props?.database_id===id||b.id===id)",database) is False
                peer=page.locator('[data-synced-reference="hidden-peer"]');expect(peer.locator('[data-synced-locations]')).to_be_enabled(timeout=30_000)
                sequence=authority.read_document('owner',document)['document_sequence']
                try:
                    open_synced_original(peer)
                    root=page.locator('#syncedOriginalReadBody');source=root.locator('[data-synced-node="hidden-original"]')
                    expect(source).to_be_focused(timeout=30_000);expect(root).to_contain_text('숨겨진 표의 원본 A')
                    assert page.evaluate('State.activePageId')==document
                    assert page.evaluate("id=>State.pageData.blocks.some(b=>b.props?.database_id===id||b.id===id)",database) is False
                    expect(root.locator('b')).to_have_text('자식');expect(root.locator('[contenteditable="true"]')).to_have_count(0)
                    expect(page.locator('#modalBody')).to_contain_text(('항목' if kind=='row' else '템플릿')+' 본문 · 보기 전용')
                    assert authority.read_database('owner',database)==before
                    assert authority.read_document('owner',document)['document_sequence']==sequence
                    save('숨겨진 표의 원본 B','hidden-body-b')
                    expect(root).to_contain_text('숨겨진 표의 원본 B',timeout=30_000)
                    page.locator('#syncedOriginalReadRefresh').click();expect(root).to_contain_text('숨겨진 표의 원본 B',timeout=30_000)
                    evidence(page,f'hidden-{kind}-{theme}-{width}',tmp_path,errors,mutations)
                    current=authority.read_document('owner',document)
                    authority.revoke_page_grant('owner',document,'subject','content-editor',expected_workspace_epoch=current['workspace_epoch'],
                        expected_document_epoch=current['document_epoch'],operation_id='hidden-page-revoke')
                    expect(page.locator('#modalBody')).not_to_contain_text('숨겨진 표의 원본 B',timeout=30_000)
                    assert mutations==[] and errors==[],(mutations,errors)
                    latest=authority.read_database('owner',database)['rows' if kind=='row' else 'templates']
                    item=next(item for item in latest if item['id']==target)
                    assert (item['body']['blocks'] if kind=='row' else item['body'])[0]['content']=='숨겨진 표의 원본 B'
                    evidence(page,f'hidden-revoked-{kind}-{theme}-{width}',tmp_path,errors,mutations)
                except Exception:
                    evidence(page,f'hidden-failure-{kind}-{theme}-{width}',tmp_path,errors,mutations);raise
            finally:browser.close()


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('kind',['row','template'])
def test_hidden_original_rejects_location_change_and_late_closed_read_without_reopening_modal(tmp_path,kind):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect,sync_playwright
    with _isolated_collaborative_app(tmp_path,**flags()) as (origin,password,authority,workspace,document):
        prepare(authority,document,kind)
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='chrome',headless=True)
            try:
                page=browser.new_page(locale='ko-KR');errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                login(page,origin,document,'owner',password);add_reference(page,document)
                peer=page.locator('[data-synced-reference="hidden-peer"]');expect(peer.locator('[data-synced-locations]')).to_be_enabled(timeout=30_000)
                endpoint='**/pages/'+document+'/synced-blocks/hidden-original'
                captured=[]
                def wrong_location(route):
                    response=route.fetch();value=response.json();captured.append(deepcopy(value));value['locations']['origin']['database_id']='different-db'
                    route.fulfill(response=response,json=value)
                page.route(endpoint,wrong_location);open_synced_original(peer)
                expect(page.locator('#syncedOriginalReadStatus')).to_contain_text('표시할 수 없습니다',timeout=30_000)
                expect(page.locator('#syncedOriginalReadBody')).to_be_empty();page.locator('#modalClose').click();page.unroute(endpoint,wrong_location)
                held=[]
                def delay(route):held.append(route)
                page.route(endpoint,delay);open_synced_original(peer)
                expect(page.locator('#syncedOriginalReadStatus')).to_contain_text('불러오는 중',timeout=30_000)
                page.wait_for_function('() => document.querySelector("#syncedOriginalReadBody").childElementCount===0')
                deadline=time.monotonic()+5
                while not held and time.monotonic()<deadline:page.wait_for_timeout(25)
                assert len(held)==1
                page.locator('#modalClose').click();expect(page.locator('#modalBg')).to_be_hidden()
                route=held.pop()
                try:route.fulfill(status=200,json=captured[-1],headers={'Cache-Control':'no-store'})
                except Exception as error:
                    # Disposing the surface aborts the fetch; Playwright may
                    # report that its already-cancelled route no longer exists.
                    assert 'Target closed' in str(error) or 'Invalid' in str(error) or 'cancel' in str(error).lower(),str(error)
                page.unroute(endpoint,delay)
                page.locator('#pageTitle').click();expect(page.locator('#modalBg')).to_be_hidden()
                expect(page.locator('#modalBody')).not_to_contain_text('숨겨진 표의 원본 A')
                assert errors==[],errors
                evidence(page,'hidden-late-'+kind,tmp_path,errors,[])
            finally:browser.close()
