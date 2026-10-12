"""UI-SYNC-LINKED-OWNER: physical owner routing and current read-only originals."""
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from code_assist_v1.tests.browser.synced_toolbar_helpers import open_synced_original

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original, reference
from code_assist_v1.notion_collaboration import database_access
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied


def login(page, origin, document, actor, password):
    page.goto(origin+'/page/'+document,wait_until='domcontentloaded')
    page.locator('#userId').fill(actor);page.locator('#password').fill(password);page.locator('#submitBtn').click()
    page.wait_for_function("() => typeof State!=='undefined'&&State.liveCollaboration.crdtReady",timeout=30_000)


def settled(page):
    page.wait_for_function("() => typeof State!=='undefined'&&State.liveCollaboration.crdtReady&&!State.dirty&&!State.saving&&!State.liveCollaboration.unsyncedCount&&!State.liveCollaboration.sendQueueActive",timeout=30_000)


def chooser(page):
    from playwright.sync_api import expect
    if not page.locator('#sidebarNewBtn').is_visible():
        page.locator('#sidebarRestoreBtn').click()
        expect(page.locator('#sidebarNewBtn')).to_be_visible()
    page.locator('#sidebarNewBtn').click()
    page.locator('#newPageMenu [data-new-page-type="database"]').click()
    expect(page.locator('#pageCreationChooser')).to_be_visible()


def source_database(page, initial):
    chooser(page);page.locator('[data-create-template="database"]').click()
    page.wait_for_function("initial=>State.activePageId!==initial&&State.pageData?.blocks.some(b=>b.props?.database_id)&&!State.saving",arg=initial,timeout=30_000)
    settled(page)
    source=page.evaluate("() => ({pageId:State.activePageId,databaseId:State.pageData.blocks.find(b=>b.props?.database_id).props.database_id})")
    page.locator('[data-database-add]').click()
    page.wait_for_function("id=>State.pageData.databases.find(d=>d.id===id)?.rows.length===1",arg=source['databaseId'],timeout=30_000)
    settled(page)
    return source


def proof(page, name, tmp_path, errors):
    output=Path(os.environ.get('NOTION_SYNC_LINKED_ARTIFACT_DIR',str(tmp_path)));output.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(output/(name+'.png')))
    (output/(name+'.json')).write_text(json.dumps({'page_errors':errors,'url':page.url},ensure_ascii=False,indent=2),encoding='utf-8')


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('modern,theme,width',[(False,'light',1280),(True,'light',1280),(False,'dark',390),(True,'dark',390)])
def test_linked_reference_writes_physical_owner_and_opens_exact_row_without_copying_body(tmp_path,modern,theme,width):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect,sync_playwright
    flags={'locked':False,'owned_database_copy':True,'navigation_ui':True}
    if modern:
        flags.update(database_activity=True,owned_database_trash=True,relation_pair=True,custom_emoji=True,item_page_layout=True,template_default=True,staged_database_sharing=True)
    with _isolated_collaborative_app(tmp_path,**flags) as (origin,password,authority,workspace,initial):
        store=SimpleNamespace(iam=authority)
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='chrome',headless=True)
            try:
                page=browser.new_page(locale='ko-KR',viewport={'width':width,'height':844})
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                login(page,origin,initial,'owner',password);page.evaluate('theme=>applyColorTheme(theme)',theme)
                source=source_database(page,initial);database=source['databaseId'];document=source['pageId']
                row=authority.read_database('owner',database)['rows'][0];row_id=row['id']
                authority.execute_surface_command('owner',document,database_id=database,command_kind='database.row.content.set',target_id=row_id,
                    payload={'blocks':[original('linked-original','연결 원본 A'),reference('linked-peer',document,'linked-original')]},
                    expected_target_version=row['version'],operation_id='linked-prepare-row',**request(store,document))
                page.reload(wait_until='domcontentloaded');settled(page)
                chooser(page);page.locator('[data-create-template="linked_database"]').click()
                option=page.locator('#newPageSourceResults .linked-source-option').filter(has_text='새 데이터베이스').first
                expect(option).to_be_visible(timeout=30_000);option.click()
                page.wait_for_function("source=>State.activePageId!==source.pageId&&State.pageData?.blocks.some(b=>b.props?.linked_database?.database_id===source.databaseId)&&!State.saving",arg=source,timeout=30_000)
                settled(page);visible=page.evaluate('State.activePageId')
                expect(page.locator(f'[data-linked-row-id="{row_id}"]').first).to_be_visible(timeout=30_000)
                page.locator(f'[data-linked-row-id="{row_id}"]').first.click()
                root=page.locator('[data-item-content]');peer=root.locator('[data-synced-reference="linked-peer"]')
                try:
                    text=peer.locator('[data-synced-text="linked-original"]')
                    expect(text).to_have_attribute('contenteditable','true',timeout=30_000)
                    expect(text).to_have_text('연결 원본 A')
                    expect(peer.locator('.synced-reference-content b')).to_have_text('자식')
                    expect(root.locator('[data-body-text="linked-original"]')).to_have_attribute('contenteditable','false')
                    assert page.evaluate('LinkedDatabaseItems.snapshot().data.source.document_id===undefined') is modern
                    before=authority.read_database('owner',database)['rows'][0]
                    visible_before=authority.read_document('owner',visible)['document_sequence']
                    with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/synced-blocks/linked-peer/text')) as saved:
                        text.fill('연결 참조에서 저장한 B')
                    assert saved.value.status==201,saved.value.text()
                    assert '/pages/'+document+'/synced-blocks/' in saved.value.url
                    assert saved.value.json()['storage']=='database_body'
                    expect(root.locator('[data-body-text="linked-original"]')).to_have_text('연결 참조에서 저장한 B',timeout=30_000)
                    page.wait_for_function("() => !ItemBodyUi?.synced?.pending()",timeout=30_000)
                    after=authority.read_database('owner',database)['rows'][0]
                    assert after['version']==before['version']+1
                    assert after['cells']==before['cells']
                    assert after['body']['blocks'][0]['content']=='연결 참조에서 저장한 B'
                    assert after['body']['blocks'][1]['content']=='' and not after['body']['blocks'][1]['props'].get('children')
                    assert authority.read_document('owner',visible)['document_sequence']==visible_before
                    # Delay the next source-page startup ticket so the initial
                    # replay race is exercised rather than hidden by a fast PC.
                    def delayed_ticket(route):
                        time.sleep(0.5);route.continue_()
                    page.route('**/pages/'+document+'/realtime-ticket',delayed_ticket)
                    open_synced_original(peer)
                    page.wait_for_function('id=>State.activePageId===id',arg=document,timeout=30_000)
                    target=page.locator('[data-item-content] [data-body-text="linked-original"]')
                    expect(target).to_be_focused(timeout=30_000)
                    expect(target).to_have_text('연결 참조에서 저장한 B')
                    assert page.evaluate('State.liveCollaboration.crdtReady')
                    expect(page.locator('[data-item-content] [data-synced-text="linked-original"]')).to_have_text('연결 참조에서 저장한 B',timeout=30_000)
                    expect(target).to_be_focused()
                    proof(page,f'linked-owner-{modern}-{theme}-{width}',tmp_path,errors)
                    assert not errors,errors
                    if modern:
                        # Direct DB authority must never manufacture authority
                        # for the page that physically owns an embedded source.
                        access=database_access.resolve(authority,'owner',workspace,database)
                        database_access.change_grant(authority,'owner',workspace,database,'subject','db-viewer',role='edit',
                            expected_workspace_epoch=access.workspace_epoch,expected_document_epoch=access.document_epoch,
                            expected_database_epoch=access.database_epoch,operation_id='linked-direct-db-grant')
                        current=authority.read_document('owner',visible)
                        authority.set_page_grant('owner',visible,'subject','db-viewer',role='view',inherit_to_descendants=False,
                            expected_workspace_epoch=current['workspace_epoch'],expected_document_epoch=current['document_epoch'],operation_id='linked-visible-view-grant')
                        with pytest.raises(NotFoundOrDenied):authority.read_document('db-viewer',document)
                        context=browser.new_context(locale='ko-KR',viewport={'width':width,'height':844})
                        reader=context.new_page();reads=[];reader.on('pageerror',lambda error:errors.append(str(error)))
                        reader.on('request',lambda r:reads.append(r.url) if '/synced-blocks/' in r.url else None)
                        login(reader,origin,visible,'db-viewer',password)
                        reader.locator(f'[data-linked-row-id="{row_id}"]').first.click()
                        expect(reader.locator('[data-item-content]')).to_be_visible(timeout=30_000)
                        assert reader.evaluate("id=>LinkedDatabaseItems.snapshot().data.database.rows.find(r=>r.id===id).body_unavailable",row_id) is True
                        expect(reader.locator('[data-item-content] [data-synced-reference]')).to_have_count(0)
                        expect(reader.locator('[data-item-content]')).not_to_contain_text('연결 참조에서 저장한 B')
                        assert reads==[],reads
                        context.close();assert not errors,errors
                except Exception:
                    proof(page,f'linked-failure-{modern}-{theme}-{width}',tmp_path,errors)
                    output=Path(os.environ.get('NOTION_SYNC_LINKED_ARTIFACT_DIR',str(tmp_path)))
                    (output/f'linked-trace-{modern}-{theme}-{width}.json').write_text(json.dumps(page.evaluate("() => ({focus:document.activeElement?.outerHTML,status:document.querySelector('#status')?.textContent,ready:State.liveCollaboration.crdtReady,itemScope:ItemBodyUi?.scope&&{page:ItemBodyUi.scope.pageId,current:linkedDatabaseScopeCurrent(ItemBodyUi.scope)},body:ItemBodyUi?.editor.snapshot()})"),ensure_ascii=False,indent=2),encoding='utf-8')
                    raise
            finally:browser.close()


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('theme,width',[('light',1280),('dark',390)])
def test_viewer_opens_readonly_template_original_refreshes_then_loses_access_without_writes(tmp_path,theme,width):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect,sync_playwright
    with _isolated_collaborative_app(tmp_path,locked=False,owned_database_copy=True,navigation_ui=True) as (origin,password,authority,workspace,document):
        store=SimpleNamespace(iam=authority)
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='chrome',headless=True)
            try:
                owner=browser.new_page(locale='ko-KR');login(owner,origin,document,'owner',password)
                source=source_database(owner,document);document=source['pageId'];database=source['databaseId']
                row=authority.read_database('owner',database)['rows'][0]
                authority.execute_schema_command('owner',document,database_id=database,command_kind='database.template.create',target_id='readonly-template',
                    payload={'name':'보기 전용 템플릿 A','body':[original('template-original','템플릿 원본 A')],'default_values':{}},
                    expected_target_version=0,operation_id='read-template-create',**request(store,document))
                authority.execute_surface_command('owner',document,database_id=database,command_kind='database.row.content.set',target_id=row['id'],
                    payload={'blocks':[reference('row-template-peer',document,'template-original')]},expected_target_version=row['version'],operation_id='read-template-reference',**request(store,document))
                current=authority.read_document('owner',document)
                authority.set_page_grant('owner',document,'subject','content-editor',role='view',inherit_to_descendants=False,
                    expected_workspace_epoch=current['workspace_epoch'],expected_document_epoch=current['document_epoch'],operation_id='read-template-view-grant')
                page=browser.new_page(locale='ko-KR',viewport={'width':width,'height':844});errors=[];mutations=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('request',lambda r:mutations.append(r.url) if r.method=='POST' and ('/commands' in r.url or '/synced-blocks/' in r.url) else None)
                login(page,origin,document,'content-editor',password);page.evaluate('theme=>applyColorTheme(theme)',theme)
                block=page.evaluate("id=>State.pageData.blocks.find(b=>b.type==='table'&&String(b.props?.database_id||b.id)===id).id",database)
                page.locator(f'[data-block-id="{block}"] [data-table-row="0"].database-open-page').click()
                peer=page.locator('[data-item-content] [data-synced-reference="row-template-peer"]')
                expect(peer.locator('[data-synced-text="template-original"]')).to_have_attribute('contenteditable','false',timeout=30_000)
                open_synced_original(peer)
                target=page.locator('#dbTemplateReadBody [data-body-text="template-original"]')
                try:
                    expect(target).to_be_focused(timeout=30_000)
                    expect(target).to_have_attribute('contenteditable','false')
                    expect(page.locator('#dbTemplateReadName')).to_have_text('보기 전용 템플릿 A')
                    template=authority.read_database('owner',database)['templates'][0]
                    authority.execute_schema_command('owner',document,database_id=database,command_kind='database.template.update',target_id=template['id'],
                        payload={'name':'보기 전용 템플릿 B','body':[original('template-original','템플릿 원본 B')],'default_values':{}},
                        expected_target_version=template['version'],operation_id='read-template-refresh',**request(store,document))
                    expect(page.locator('#dbTemplateReadName')).to_have_text('보기 전용 템플릿 B',timeout=30_000)
                    expect(target).to_have_text('템플릿 원본 B',timeout=30_000)
                    proof(page,f'read-template-{theme}-{width}',tmp_path,errors)
                    current=authority.read_document('owner',document)
                    authority.revoke_page_grant('owner',document,'subject','content-editor',expected_workspace_epoch=current['workspace_epoch'],
                        expected_document_epoch=current['document_epoch'],operation_id='read-template-revoke')
                    expect(page.locator('#dbTemplateReadBody')).not_to_contain_text('템플릿 원본 B',timeout=30_000)
                    expect(page.locator('#dbTemplateReadName')).not_to_contain_text('보기 전용 템플릿 B',timeout=30_000)
                    assert authority.read_database('owner',database)['templates'][0]['body'][0]['content']=='템플릿 원본 B'
                    assert mutations==[],mutations
                    assert not errors,errors
                except Exception:
                    proof(page,f'read-template-failure-{theme}-{width}',tmp_path,errors);raise
            finally:browser.close()
