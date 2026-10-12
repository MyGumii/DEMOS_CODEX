"""UI-SYNC-ROW-CLIPBOARD: real clipboard in owned/linked physical row bodies."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.browser.test_notion_synced_database_detach_browser import DETACH_FLAGS
from code_assist_v1.tests.browser.test_notion_synced_linked_template_mount import prepare as linked_prepare
from code_assist_v1.tests.browser.test_notion_synced_linked_navigation import login, settled
from code_assist_v1.tests.browser.test_notion_synced_clipboard import prepare, copy
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original
from code_assist_v1.tests.browser.test_notion_document_extras import _seed_blocks


def proof(page,tmp_path,name,errors,details=None):
    output=Path(os.environ.get('NOTION_SYNC_ROW_CLIPBOARD_ARTIFACT_DIR',str(tmp_path)));output.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(output/(name+'.png')))
    state=page.evaluate("()=>({status:document.querySelector('#status').textContent,paste:ItemBodyUi?.paste?.snapshot(),body:ItemBodyUi?.editor?.snapshot(),model:LinkedDatabaseItems?.snapshot()?.data,feedback:window.clipCopyFeedback})")
    (output/(name+'.json')).write_text(json.dumps({'state':state,'page_errors':errors,'details':details},ensure_ascii=False,indent=2),encoding='utf-8')


def setup(page,origin,password,authority,workspace,initial,mode):
    from playwright.sync_api import expect
    login(page,origin,initial,'owner',password);prepare(page)
    source,visible=linked_prepare(page,authority,initial);document=source['pageId'];database=source['databaseId']
    row=authority.read_database('owner',database)['rows'][0]
    body=[original('row-clip-original','현재 원본 A'),
          {'id':'row-clip-anchor','type':'paragraph','content':'기존 본문','props':{}},
          {'id':'row-clip-keep','type':'paragraph','content':'유지할 글','props':{}}]
    authority.execute_surface_command('owner',document,database_id=database,command_kind='database.row.content.set',target_id=row['id'],
        payload={'blocks':body},expected_target_version=row['version'],operation_id='row-clipboard-seed',**request(SimpleNamespace(iam=authority),document))
    if mode=='owned': page.goto(origin+'/page/'+document,wait_until='domcontentloaded')
    else: page.reload(wait_until='domcontentloaded')
    prepare(page)
    if mode=='owned':
        block=page.evaluate("id=>State.pageData.blocks.find(b=>String(b.props?.database_id||b.id)===id)?.id",database)
        page.locator(f'[data-block-id="{block}"] [data-table-row="0"].database-open-page').click()
    else: page.locator(f'[data-linked-row-id="{row["id"]}"]').first.click()
    root=page.locator('[data-item-content]')
    expect(root.locator('[data-body-text="row-clip-anchor"]')).to_have_text('기존 본문',timeout=30_000)
    page.wait_for_function('()=>Boolean(ItemBodyUi?.paste)&&!ItemBodyUi?.paste.pending()',timeout=30_000)
    if mode=='owned': expect(root.locator('[data-body-text="row-clip-anchor"]')).to_have_attribute('contenteditable','true',timeout=30_000)
    return document,database,row['id'],visible,root


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('mode,theme,width',[('owned','light',1280),('owned','dark',390),('linked','light',1280),('linked','dark',390)])
def test_native_clipboard_paste_row_keeps_real_owner_original_siblings_and_reload(tmp_path,mode,theme,width):
    from playwright.sync_api import expect,sync_playwright
    with _isolated_collaborative_app(tmp_path,**DETACH_FLAGS) as (origin,password,authority,workspace,initial):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            try:
                context=browser.new_context(locale='ko-KR',viewport={'width':width,'height':844});context.grant_permissions(['clipboard-read','clipboard-write'],origin=origin)
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                document,database,row,visible,root=setup(page,origin,password,authority,workspace,initial,mode)
                page.evaluate('theme=>applyColorTheme(theme)',theme)
                before=authority.read_database('owner',database)['rows'][0];visible_before=authority.read_document('owner',visible)
                copy(page,'row-clip-original');prepare(page)
                target=root.locator('[data-body-text="row-clip-anchor"]') if mode=='owned' else root.locator('.item-body-blocks')
                with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/synced-paste')) as pasted:
                    target.press('Control+v')
                response=pasted.value;assert response.status==201,response.text();result=response.json()
                assert result['document_id']==document and result['row_id']==row
                peer=root.locator(f'[data-synced-reference="{result["reference_id"]}"]')
                expect(peer).to_contain_text('현재 원본 A',timeout=30_000)
                page.wait_for_function('()=>ItemBodyUi?.paste&&!ItemBodyUi.paste.pending()',timeout=30_000)
                latest=authority.read_database('owner',database)['rows'][0];blocks=latest['body']['blocks']
                assert len(blocks)==4 and latest['version']==before['version']+1
                byid={item['id']:item for item in blocks};ref=byid[result['reference_id']]
                assert ref['content']=='' and not ref['props'].get('children') and ref['props']['synced_from']=={'document_id':document,'block_id':'row-clip-original'}
                for item in before['body']['blocks']:
                    new=dict(byid[item['id']]);old=dict(item);new.pop('position',None);old.pop('position',None);assert new==old
                if mode=='linked':assert authority.read_document('owner',visible)==visible_before
                proof(page,tmp_path,f'row-paste-{mode}-{theme}-{width}',errors,result)
                page.reload(wait_until='domcontentloaded');prepare(page)
                if mode=='owned':
                    block=page.evaluate("id=>State.pageData.blocks.find(b=>String(b.props?.database_id||b.id)===id)?.id",database)
                    page.locator(f'[data-block-id="{block}"] [data-table-row="0"].database-open-page').click()
                else:page.locator(f'[data-linked-row-id="{row}"]').first.click()
                expect(page.locator(f'[data-synced-reference="{result["reference_id"]}"]')).to_contain_text('현재 원본 A',timeout=30_000)
                assert authority.read_database('owner',database)['rows'][0]==latest
                assert not errors,errors
            except Exception:
                if 'page' in locals():proof(page,tmp_path,f'row-paste-failure-{mode}-{theme}-{width}',errors)
                raise
            finally:browser.close()


def reopen_owned(page,origin,document,database):
    page.goto(origin+'/page/'+document,wait_until='domcontentloaded');prepare(page)
    block=page.evaluate("id=>State.pageData.blocks.find(b=>String(b.props?.database_id||b.id)===id)?.id",database)
    page.locator(f'[data-block-id="{block}"] [data-table-row="0"].database-open-page').click()
    page.wait_for_function('()=>Boolean(ItemBodyUi?.paste)',timeout=30_000)
    return page.locator('[data-item-content]')


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
def test_main_original_copied_before_edit_pastes_latest_cross_page_row_without_source_body_clone(tmp_path):
    from playwright.sync_api import expect,sync_playwright
    with _isolated_collaborative_app(tmp_path,**DETACH_FLAGS) as (origin,password,authority,workspace,initial):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            try:
                context=browser.new_context(locale='ko-KR');context.grant_permissions(['clipboard-read','clipboard-write'],origin=origin)
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                document,database,row,_visible,_root=setup(page,origin,password,authority,workspace,initial,'owned')
                source=original('cross-row-original','복사 시점 A')
                page.goto(origin+'/page/'+initial,wait_until='domcontentloaded');prepare(page)
                _seed_blocks(page,page.evaluate('State.pageData.blocks')+[source]);prepare(page)
                copy(page,'cross-row-original')
                wire=page.evaluate("async()=>{const data=await navigator.clipboard.read();return await data[0].getType('text/html').then(value=>value.text());}")
                assert '복사 시점 A' not in wire
                page.locator('[data-synced-source="cross-row-original"] .block-editor').fill('붙여넣기 시점 B');prepare(page)
                source_sequence=authority.read_document('owner',initial)['document_sequence']
                root=reopen_owned(page,origin,document,database)
                with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/synced-paste')) as pasted:
                    root.locator('[data-body-text="row-clip-anchor"]').press('Control+v')
                assert pasted.value.status==201,pasted.value.text();result=pasted.value.json()
                expect(root.locator(f'[data-synced-reference="{result["reference_id"]}"]')).to_contain_text('붙여넣기 시점 B',timeout=30_000)
                page.wait_for_function('()=>!ItemBodyUi.paste.pending()',timeout=30_000)
                assert authority.read_document('owner',initial)['document_sequence']==source_sequence
                body=authority.read_database('owner',database)['rows'][0]['body']['blocks']
                ref=next(item for item in body if item['id']==result['reference_id'])
                assert ref['content']=='' and 'children' not in ref['props'] and ref['props']['synced_from']=={'document_id':initial,'block_id':'cross-row-original'}
                assert not errors,errors;proof(page,tmp_path,'row-paste-cross-page-latest-original',errors,result)
            except Exception:
                if 'page' in locals():proof(page,tmp_path,'row-paste-cross-page-failure',errors)
                raise
            finally:browser.close()


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
def test_lost_paste_response_survives_reload_and_exact_receipt_retry_without_duplicate(tmp_path):
    from playwright.sync_api import expect,sync_playwright
    with _isolated_collaborative_app(tmp_path,**DETACH_FLAGS) as (origin,password,authority,workspace,initial):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            try:
                context=browser.new_context(locale='ko-KR');context.grant_permissions(['clipboard-read','clipboard-write'],origin=origin)
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                document,database,row,_visible,root=setup(page,origin,password,authority,workspace,initial,'owned')
                copy(page,'row-clip-original');prepare(page);committed=[]
                def lose_response(route):
                    response=route.fetch();assert response.status==201,response.text();committed.append(response.json());route.abort('failed')
                page.route('**/synced-paste',lose_response)
                root.locator('[data-body-text="row-clip-anchor"]').press('Control+v')
                page.wait_for_function('()=>ItemBodyUi?.paste.snapshot().pending&&!ItemBodyUi.paste.snapshot().busy',timeout=30_000)
                assert len(committed)==1
                before=authority.read_database('owner',database)['rows'][0]
                root=reopen_owned(page,origin,document,database)
                retry=root.get_by_role('button',name='붙여넣기 결과 확인 · 재시도')
                expect(retry).to_be_visible(timeout=30_000)
                with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/synced-paste-result')) as receipt:
                    retry.press('Enter')
                assert receipt.value.status==200 and receipt.value.json()['state']=='committed'
                page.wait_for_function('()=>ItemBodyUi?.paste&&!ItemBodyUi.paste.pending()',timeout=30_000)
                assert len(committed)==1 and authority.read_database('owner',database)['rows'][0]==before
                expect(root.locator(f'[data-synced-reference="{committed[0]["reference_id"]}"]')).to_contain_text('현재 원본 A')
                assert not errors,errors;proof(page,tmp_path,'row-paste-lost-response-recovered',errors,committed[0])
            except Exception:
                if 'page' in locals():proof(page,tmp_path,'row-paste-lost-response-failure',errors)
                raise
            finally:browser.close()


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('race',['input','replacement','navigation','composition'])
def test_row_paste_preparation_rejects_changed_input_dom_navigation_and_ime(tmp_path,race):
    from playwright.sync_api import expect,sync_playwright
    with _isolated_collaborative_app(tmp_path,**DETACH_FLAGS) as (origin,password,authority,workspace,initial):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            try:
                context=browser.new_context(locale='ko-KR');context.grant_permissions(['clipboard-read','clipboard-write'],origin=origin)
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                document,database,row,_visible,root=setup(page,origin,password,authority,workspace,initial,'owned')
                copy(page,'row-clip-original');prepare(page);posts=[];intercepted=[]
                page.on('request',lambda value:posts.append(value.url) if value.method=='POST' and value.url.endswith('/synced-paste') else None)
                def change_target(route):
                    response=route.fetch();intercepted.append(True)
                    if race=='navigation':page.evaluate('id=>openPage(id)',initial)
                    else:page.evaluate("race=>{const field=document.querySelector('[data-item-content] [data-body-text=\"row-clip-anchor\"]');if(race==='replacement')field.replaceWith(field.cloneNode(true));else if(race==='input'){field.textContent='붙여넣기 대기 중 새 입력';field.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText'}));}else field.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true,data:'ㅎ'}));}",race)
                    route.fulfill(response=response)
                page.route('**/synced-paste-target',change_target)
                root.locator('[data-body-text="row-clip-anchor"]').press('Control+v')
                page.wait_for_timeout(1200)
                assert intercepted and not posts
                latest=authority.read_database('owner',database)['rows'][0]
                assert not any(item['props'].get('synced_from') for item in latest['body']['blocks'])
                if race=='input':
                    expect(root.locator('[data-body-text="row-clip-anchor"]')).to_have_text('붙여넣기 대기 중 새 입력')
                    page.wait_for_function('()=>!OwnedItemBody.pending(ItemBodyUi.scope,ItemBodyUi.database,ItemBodyUi.row)',timeout=30_000)
                    assert next(item for item in authority.read_database('owner',database)['rows'][0]['body']['blocks'] if item['id']=='row-clip-anchor')['content']=='붙여넣기 대기 중 새 입력'
                if race=='composition':
                    root.locator('[data-body-text="row-clip-anchor"]').dispatch_event('compositionend',{'data':''})
                assert not errors,errors;proof(page,tmp_path,'row-paste-guard-'+race,errors,latest)
            except Exception:
                if 'page' in locals():proof(page,tmp_path,'row-paste-guard-failure-'+race,errors)
                raise
            finally:browser.close()
