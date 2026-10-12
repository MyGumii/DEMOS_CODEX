"""UI-SYNC-DB-MOUNT: real owned body mounts, saves, and exact source navigation."""
import os
import json
from pathlib import Path
import pytest
from code_assist_v1.tests.browser.synced_toolbar_helpers import open_synced_original

from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original, reference


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('kind,theme,width', [('row','light',1280),('template','light',1280),('row','dark',390),('template','dark',390)])
def test_owned_body_reference_edit_preserves_fresh_original_on_whole_body_save_and_opens_exact_location(tmp_path,kind,theme,width):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect,sync_playwright
    with _isolated_collaborative_app(tmp_path,locked=False,navigation_database_favorites=True,page_copy=True) as (origin,password,authority,workspace,document):
        store=CollaborativeWorkspaceStore(authority.catalog_path,user_data_root=authority.user_data_root,identity_directory=authority.identity_directory)
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='chrome',headless=True)
            try:
                page=browser.new_page(locale='ko-KR',viewport={'width':width,'height':844})
                errors=[];reads=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('response',lambda response:reads.append({'url':response.url,'status':response.status})
                    if response.request.method=='GET' and '/synced-blocks/' in response.url else None)
                page.goto(origin+'/page/'+document,wait_until='domcontentloaded')
                page.locator('#userId').fill('owner');page.locator('#password').fill(password);page.locator('#submitBtn').click()
                page.wait_for_function("() => typeof State!=='undefined'&&State.liveCollaboration.crdtReady",timeout=30_000)
                page.evaluate('theme=>applyColorTheme(theme)',theme)
                database=page.evaluate("() => {insertBlockAfter(State.pageData.blocks.length-1,'table');return State.pageData.blocks.find(b=>b.type==='table').id;}")
                page.wait_for_function("() => !State.dirty&&!State.liveCollaboration.unsyncedCount&&!State.liveCollaboration.sendQueueActive",timeout=30_000)
                page.reload(wait_until='domcontentloaded')
                table=page.locator(f'[data-block-id="{database}"]')
                expect(table.locator('[data-database-add]')).to_be_visible(timeout=30_000)
                # The first owned interaction materializes this inserted table's
                # canonical DB before preparing either kind of synthetic body.
                table.locator('[data-database-add]').click()
                page.wait_for_function("id=>State.pageData.databases.find(db=>db.id===id)?.rows.length===1&&!State.dirty&&!State.liveCollaboration.unsyncedCount&&!State.liveCollaboration.sendQueueActive",arg=database,timeout=30_000)
                body=[original('mount-original','원본 A'),reference('mount-peer',document,'mount-original'),
                      {'id':'mount-ordinary','type':'paragraph','content':'다른 글 A','props':{},'position':2}]
                if kind=='row':
                    row=authority.read_database('owner',database)['rows'][0]
                    target=row['id']
                    authority.execute_surface_command('owner',document,database_id=database,command_kind='database.row.content.set',target_id=target,
                        payload={'blocks':body},expected_target_version=row['version'],operation_id='mount-prepare-row',**request(store,document))
                else:
                    target='mount-template'
                    authority.execute_schema_command('owner',document,database_id=database,command_kind='database.template.create',target_id=target,
                        payload={'name':'동기화 검증 템플릿','body':body,'default_values':{}},expected_target_version=0,operation_id='mount-prepare-template',**request(store,document))
                page.reload(wait_until='domcontentloaded')
                page.wait_for_function("() => typeof State!=='undefined'&&State.liveCollaboration.crdtReady",timeout=30_000)
                # Rehydration may replace a client temporary block ID while
                # retaining the exact canonical database ID. Bind the visible
                # owner afresh after service preparation, before any UI action.
                block_id=page.evaluate("id=>State.pageData.blocks.find(b=>b.type==='table'&&String(b.props?.database_id||b.id)===id)?.id",database)
                assert block_id, page.evaluate("State.pageData.blocks")
                table=page.locator(f'[data-block-id="{block_id}"]')
                if kind=='row':
                    table.locator('[data-table-row="0"].database-open-page').click()
                    root=page.locator('[data-item-content]')
                else:
                    table.locator('[data-db-template-picker]').click()
                    table.locator(f'[data-db-template-more="{target}"]').click()
                    table.locator(f'[data-db-template-edit="{target}"]').click()
                    root=page.locator('#dbTemplateBodyEditor')
                peer=root.locator('[data-synced-reference="mount-peer"]')
                try:
                    expect(peer.locator('[data-synced-text="mount-original"]')).to_be_editable(timeout=30_000)
                    trigger=peer.locator('[data-synced-locations]');trigger.press('ArrowDown')
                    expect(peer.locator('[data-synced-open]')).to_be_focused()
                    bounds=peer.locator('[data-synced-locations-panel]').bounding_box()
                    assert bounds and bounds['x']>=7 and bounds['x']+bounds['width']<=width-7,bounds
                    peer.locator('[data-synced-open]').press('Escape');expect(trigger).to_be_focused()
                    more=peer.locator('[data-synced-actions-menu]');more.press('ArrowDown')
                    expect(peer.locator('[data-synced-refresh]')).to_be_focused()
                    peer.locator('[data-synced-refresh]').press('Escape');expect(more).to_be_focused()
                    expect(peer.locator('.synced-reference-content b')).to_have_text('자식')
                    expect(root.locator('[data-body-text="mount-original"]')).to_have_text('원본 A')
                    # Both mounted toolbars copy only the same real original.
                    # Row paste has its own semantic service/browser gate;
                    # templates retain their existing structural paste guard.
                    page.context.grant_permissions(['clipboard-read','clipboard-write'],origin=origin)
                    before_copy=authority.read_document('owner',document)['document_sequence']
                    before_body=authority.read_database('owner',database)
                    page.evaluate("()=>{window.clipCopyFeedback=[];const notify=setStatus;setStatus=function(text,kind){clipCopyFeedback.push(String(text));return notify(text,kind);};}")
                    for block in ['mount-original','mount-peer']:
                        copy=root.locator(f'[data-synced-source="{block}"], [data-synced-reference="{block}"]').locator('[data-synced-source-copy]')
                        expect(copy).to_be_enabled(timeout=30_000)
                        with page.expect_response(lambda r:r.request.method=='GET' and r.url.endswith('/synced-blocks/'+block)):
                            copy.press('Enter')
                        page.wait_for_function("()=>clipCopyFeedback.some(text=>text.includes('복사 완료!'))",timeout=30_000)
                        page.evaluate('window.clipCopyFeedback=[]')
                        expect(copy).to_be_enabled(timeout=30_000)
                        wire=page.evaluate("async()=>{const items=await navigator.clipboard.read();const html=await items[0].getType('text/html').then(b=>b.text());const node=document.createElement('template');node.innerHTML=html;return JSON.parse(node.content.querySelector('[data-notion-synced-reference]').getAttribute('data-notion-synced-reference'));}")
                        assert wire['source']=={'workspace_id':workspace,'document_id':document,'block_id':'mount-original'}
                    if kind=='template':
                        root.locator('[data-body-text="mount-ordinary"]').press('Control+v')
                        expect(root).to_contain_text('이 본문에서는 동기화 블록 붙여넣기를 아직 지원하지 않습니다.')
                    assert authority.read_database('owner',database)==before_body
                    assert authority.read_document('owner',document)['document_sequence']==before_copy
                    with page.expect_response('**/synced-blocks/mount-peer/text') as response:
                        peer.locator('[data-synced-text="mount-original"]').fill('참조에서 저장한 D')
                    assert response.value.status==201,response.value.text()
                    assert response.value.json()['storage']=='database_body'
                    expect(root.locator('[data-body-text="mount-original"]')).to_have_text('참조에서 저장한 D',timeout=30_000)
                    page.wait_for_function("() => !ItemBodyUi?.synced?.pending()&&!ActiveDatabaseTemplateEditor?.dirty?.()",timeout=30_000)
                    expect(root.locator('[data-body-text="mount-ordinary"]')).to_have_attribute('contenteditable','true',timeout=30_000)
                    with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/databases/commands')) as saved:
                        root.locator('[data-body-text="mount-ordinary"]').fill('다른 글 B')
                    assert saved.value.status==201,saved.value.text()
                    page.wait_for_function("() => !ItemBodyUi?.synced?.pending()&&!ActiveDatabaseTemplateEditor?.dirty?.()",timeout=30_000)
                    value=authority.read_database('owner',database)
                    latest=next(item for item in value['rows' if kind=='row' else 'templates'] if item['id']==target)
                    blocks=latest['body']['blocks'] if kind=='row' else latest['body']
                    assert blocks[0]['content']=='참조에서 저장한 D'
                    assert blocks[1]['content']=='' and not blocks[1]['props'].get('children')
                    assert blocks[2]['content']=='다른 글 B'
                    expect(peer.locator('[data-synced-locations]')).to_be_enabled(timeout=30_000)
                    open_synced_original(peer)
                    expect(root.locator('[data-body-text="mount-original"]')).to_be_focused(timeout=30_000)
                    expect(peer.locator('.synced-reference-content')).to_contain_text('참조에서 저장한 D',timeout=30_000)
                    output=Path(os.environ.get('NOTION_SYNC_MOUNT_ARTIFACT_DIR',str(tmp_path)));output.mkdir(parents=True,exist_ok=True)
                    page.screenshot(path=str(output/f'body-mount-{kind}-{theme}-{width}.png'))
                    assert not errors
                    if kind=='row':
                        # A second real authenticated session observes the same
                        # owned body; current authority changes its editability.
                        reader_context=browser.new_context(locale='ko-KR',viewport={'width':width,'height':844})
                        reader=reader_context.new_page();reader.on('pageerror',lambda error:errors.append(str(error)))
                        reader.goto(origin+'/page/'+document,wait_until='domcontentloaded')
                        reader.locator('#userId').fill('content-editor');reader.locator('#password').fill(password);reader.locator('#submitBtn').click()
                        reader.wait_for_function("() => typeof State!=='undefined'&&State.liveCollaboration.crdtReady",timeout=30_000)
                        reader.locator(f'[data-block-id="{database}"] [data-table-row="0"].database-open-page').click()
                        read_content=reader.locator('[data-item-content] [data-synced-text="mount-original"]')
                        expect(read_content).to_have_attribute('contenteditable','true',timeout=30_000)
                        current=authority.read_document('owner',document)
                        authority.set_page_grant('owner',document,'subject','content-editor',role='view',inherit_to_descendants=False,
                            expected_workspace_epoch=current['workspace_epoch'],expected_document_epoch=current['document_epoch'],operation_id='mount-content-editor-view')
                        reader.wait_for_function("() => State.pageData?.page?.iam_role==='view'",timeout=30_000)
                        expect(read_content).to_have_attribute('contenteditable','false',timeout=30_000)
                        expect(read_content).to_have_text('참조에서 저장한 D')
                        expect(reader.locator('[data-item-content] [data-body-text="mount-ordinary"]')).to_have_attribute('contenteditable','false')
                        assert authority.read_database('owner',database)['rows'][0]['body']['blocks']==blocks
                        reader_context.close();assert not errors
                except Exception:
                    output=Path(os.environ.get('NOTION_SYNC_MOUNT_ARTIFACT_DIR',str(tmp_path)));output.mkdir(parents=True,exist_ok=True)
                    page.screenshot(path=str(output/f'body-mount-failure-{kind}-{theme}-{width}.png'))
                    (output/f'body-mount-failure-{kind}-{theme}-{width}.txt').write_text(str(errors),encoding='utf-8')
                    diagnostic=page.evaluate("""() => ({errors:[],page:State.pageData?.page,databases:State.pageData?.databases,
                      item:LinkedDatabaseItems?.snapshot(),body:ItemBodyUi&&OwnedItemBody?.snapshot(ItemBodyUi.scope,ItemBodyUi.database,ItemBodyUi.row),
                      pending:ItemBodyUi?.synced?.pending(),bodySnapshot:ItemBodyUi?.editor.snapshot(),templateDirty:ActiveDatabaseTemplateEditor?.dirty?.(),html:document.querySelector('[data-item-content],#dbTemplateBodyEditor')?.innerHTML})""")
                    diagnostic['errors']=errors
                    diagnostic['synced_reads']=reads
                    (output/f'body-mount-failure-{kind}-{theme}-{width}.json').write_text(json.dumps(diagnostic,ensure_ascii=False,indent=2),encoding='utf-8')
                    raise
            finally:browser.close()
