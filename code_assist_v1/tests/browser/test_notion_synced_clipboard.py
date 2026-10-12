"""UI-SYNC-CLIP: actual clipboard, existing editor bridge, current roles and races."""
import json
import os
from pathlib import Path
import pytest
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.browser.test_notion_synced_database_detach_browser import DETACH_FLAGS
from code_assist_v1.tests.browser.test_notion_synced_linked_navigation import login,settled
from code_assist_v1.tests.browser.test_notion_document_extras import _seed_blocks
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original,reference
from code_assist_v1.notion_collaboration.page_creation import create_page
from code_assist_v1.tests.browser.test_notion_synced_source_menu import _current_collaborative_body

MIME='application/x-code-assist-synced-reference+json'
def output(tmp_path):
    path=Path(os.environ.get('NOTION_SYNC_CLIPBOARD_ARTIFACT_DIR',str(tmp_path)));path.mkdir(parents=True,exist_ok=True);return path
def current(authority,workspace,document):return _current_collaborative_body(authority,'owner',document,workspace)
def sequence(authority,document):return authority.read_document('owner',document)['document_sequence']
def prepare(page):
    page.wait_for_function('()=>State.liveCollaboration.connected&&!State.liveCollaboration.initialReplay&&State.liveCollaboration.writeAuthorized&&activePageCanEdit()',timeout=30_000)
    page.evaluate('async()=>{await State.liveCollaboration.persistenceChain}');settled(page)
    page.wait_for_function('()=>activePageCanEdit()',timeout=30_000)
def target_page(authority,workspace):
    with authority._reader() as con:version=authority._workspace(con,workspace)['row_version']
    return create_page(authority,'owner',workspace,title='붙여넣기 대상',parent_id='',base_version=version,operation_id='clipboard-target-create')['page']['id']
def copy(page,block):
    from playwright.sync_api import expect
    page.wait_for_function('()=>State.liveCollaboration.connected&&!State.liveCollaboration.initialReplay',timeout=30_000)
    button=page.locator(f'[data-synced-source="{block}"], [data-synced-reference="{block}"]').locator('[data-synced-source-copy]')
    expect(button).to_be_enabled(timeout=30_000)
    page.evaluate("()=>{window.clipCopyFeedback=[];if(!window.clipCopyObserver){window.clipPasteTrace=[];const paste=pasteSyncedReference;pasteSyncedReference=function(event,index){clipPasteTrace.push({index,nodeConnected:event.target?.isConnected,replay:State.liveCollaboration.initialReplay,authorized:State.liveCollaboration.writeAuthorized,connected:State.liveCollaboration.connected,canEdit:activePageCanEdit(),composing:EditorHost.isComposing(),clipboardTypes:[...event.clipboardData.types]});return paste(event,index);};const notify=setStatus;setStatus=function(text,kind){clipCopyFeedback.push(String(text));return notify(text,kind);};window.clipCopyObserver=true;}}")
    with page.expect_response(lambda r:r.request.method=='GET' and r.url.endswith('/synced-blocks/'+block)):
        button.press('Enter')
    page.wait_for_function("()=>clipCopyFeedback.some(text=>text.includes('복사 완료!'))",timeout=30_000)
    expect(button).to_be_enabled(timeout=30_000)
def grant(authority,document,role,op):
    witness=authority.read_document('owner',document)
    authority.set_page_grant('owner',document,'subject','content-editor',role=role,inherit_to_descendants=False,
        expected_workspace_epoch=witness['workspace_epoch'],expected_document_epoch=witness['document_epoch'],operation_id=op)
def dispatch(page,encoded,target='#blocks .block-editor'):
    page.locator(target).first.evaluate("""(node,encoded)=>{const data=new DataTransfer();data.setData('application/x-code-assist-synced-reference+json',encoded);data.setData('text/plain','절대 삽입되지 않을 글');node.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));}""",encoded)
def feedback(page,text):
    # Ordinary save/reconnect feedback can replace this same global status node.
    # Record the real notification without suppressing or changing its UI effect.
    page.wait_for_function('text=>window.clipFeedback.some(value=>value.includes(text))',arg=text,timeout=30_000)
    page.evaluate('window.clipFeedback=[]')

@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('theme,width',[('light',1280),('dark',390)])
def test_actual_copy_paste_same_cross_page_reference_and_latest_source(tmp_path,theme,width):
    from playwright.sync_api import expect,sync_playwright
    proof=output(tmp_path)
    with _isolated_collaborative_app(tmp_path,**DETACH_FLAGS) as (origin,password,authority,workspace,document):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            try:
                context=browser.new_context(locale='ko-KR',viewport={'width':width,'height':844});page=context.new_page();context.grant_permissions(['clipboard-read','clipboard-write'],origin=origin)
                login(page,origin,document,'owner',password);prepare(page);page.evaluate('theme=>applyColorTheme(theme)',theme)
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                _seed_blocks(page,[original('clip-original','원본 A'),{'id':'clip-empty','type':'paragraph','content':'','props':{}},{'id':'clip-sibling','type':'paragraph','content':'다른 글 보존','props':{}}]);prepare(page)
                before=current(authority,workspace,document);baseline=sequence(authority,document)
                copy(page,'clip-original');assert current(authority,workspace,document)==before and sequence(authority,document)==baseline
                wire=page.evaluate("async()=>{const items=await navigator.clipboard.read();return await items[0].getType('text/html').then(b=>b.text())}")
                assert 'data-notion-synced-reference' in wire and '원본 A' not in wire
                prepare(page)
                page.locator('[data-block-id="clip-empty"] .block-editor').press('Control+v')
                expect(page.locator('.synced-reference-content')).to_contain_text('원본 A',timeout=30_000);prepare(page)
                same=current(authority,workspace,document);refs=[b for b in same if b.get('props',{}).get('synced_from')]
                assert len(refs)==1 and len(same)==3 and refs[0]['content']=='' and 'children' not in refs[0]['props']
                assert refs[0]['props']['synced_from']=={'document_id':document,'block_id':'clip-original'}
                assert same[0]==before[0] and same[-1]==before[-1]
                page.screenshot(path=str(proof/f'clipboard-same-{theme}-{width}.png'))
                copy(page,refs[0]['id']);same_sequence=sequence(authority,document)
                dest=target_page(authority,workspace);page.goto(origin+'/page/'+dest)
                page.wait_for_function('id=>State.activePageId===id&&State.liveCollaboration.crdtReady',arg=dest,timeout=30_000)
                prepare(page)
                _seed_blocks(page,[{'id':'dest-empty','type':'paragraph','content':'','props':{}},{'id':'dest-sibling','type':'paragraph','content':'대상 글 보존','props':{}}]);prepare(page)
                dest_before=current(authority,workspace,dest)
                # Change the source after copying. Clipboard must not cache its body.
                other=page.context.new_page();other.goto(origin+'/page/'+document);other.wait_for_function('id=>State.activePageId===id&&State.liveCollaboration.crdtReady',arg=document,timeout=30_000)
                prepare(other)
                other.locator('[data-synced-source="clip-original"] .block-editor').fill('최신 원본 B');prepare(other);other.close()
                prepare(page)
                page.locator('[data-block-id="dest-empty"] .block-editor').press('Control+v')
                expect(page.locator('.synced-reference-content')).to_contain_text('최신 원본 B',timeout=30_000);prepare(page)
                saved=current(authority,workspace,dest);assert len(saved)==2 and saved[-1]==dest_before[-1]
                assert saved[0]['props']['synced_from']=={'document_id':document,'block_id':'clip-original'} and saved[0]['content']==''
                assert sequence(authority,document)==same_sequence+1
                page.reload();page.wait_for_function('()=>State.liveCollaboration.crdtReady',timeout=30_000)
                expect(page.locator('.synced-reference-content')).to_contain_text('최신 원본 B',timeout=30_000);prepare(page)
                assert current(authority,workspace,dest)==saved and not errors,errors
                page.screenshot(path=str(proof/f'clipboard-cross-{theme}-{width}.png'))
                (proof/f'clipboard-{theme}-{width}.json').write_text(json.dumps({'copy_sequence':baseline,'source':document,'destination':dest,'destination_blocks':saved,'page_errors':errors,'wire_contains_body':False},ensure_ascii=False,indent=2),encoding='utf-8')
            except Exception:
                if 'page' in locals():
                    page.screenshot(path=str(proof/f'clipboard-failure-{theme}-{width}.png'))
                    (proof/f'clipboard-failure-{theme}-{width}.json').write_text(json.dumps(page.evaluate("()=>({status:document.querySelector('#status').textContent,blocks:State.pageData.blocks,selected:[...State.selectedBlockIds],focus:document.activeElement?.id,canEdit:activePageCanEdit(),composing:EditorHost.isComposing(),scopeCurrent:syncedScopeCurrent(syncedTextScope()),navigationSequence:State.navigation.sequence,feedback:window.clipCopyFeedback,trace:window.clipPasteTrace})"),ensure_ascii=False,indent=2),encoding='utf-8')
                raise
            finally:browser.close()

@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
def test_clipboard_failures_composition_changed_target_and_late_navigation_do_not_insert(tmp_path):
    from playwright.sync_api import expect,sync_playwright
    with _isolated_collaborative_app(tmp_path,**DETACH_FLAGS) as (origin,password,authority,workspace,document):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            try:
                page=browser.new_page(locale='ko-KR');login(page,origin,document,'owner',password);prepare(page)
                _seed_blocks(page,[original('clip-original','원본'),{'id':'clip-target','type':'paragraph','content':'보존','props':{}}]);prepare(page)
                page.evaluate("()=>{window.clipFeedback=[];const notify=setStatus;setStatus=function(text,kind){clipFeedback.push(String(text));return notify(text,kind);};}")
                base=current(authority,workspace,document);seq=sequence(authority,document)
                descriptor={'version':1,'contract':'code_assist.notion.synced_clipboard.v1','application_origin':origin,'source':{'workspace_id':workspace,'document_id':document,'block_id':'clip-original'}}
                for encoded in ['{',json.dumps(descriptor|{'application_origin':'http://foreign.test'}),json.dumps(descriptor|{'grant':'invented'})]:
                    dispatch(page,encoded,'[data-block-id="clip-target"] .block-editor');feedback(page,'동기화')
                    assert current(authority,workspace,document)==base and sequence(authority,document)==seq
                page.evaluate("""()=>{window.clipExec=document.execCommand.bind(document);window.clipNav=navigator.clipboard;document.execCommand=()=>false;Object.defineProperty(navigator,'clipboard',{configurable:true,value:{write:async()=>{throw Error('clipboard denied')}}});}""")
                page.locator('[data-synced-source-copy]').press('Enter');feedback(page,'복사하지 못했습니다')
                assert sequence(authority,document)==seq
                page.evaluate("()=>{document.execCommand=window.clipExec;Object.defineProperty(navigator,'clipboard',{configurable:true,value:window.clipNav});}")
                editor=page.locator('[data-block-id="clip-target"] .block-editor');editor.evaluate("node=>node.dataset.imeComposing='true'")
                dispatch(page,json.dumps(descriptor),'[data-block-id="clip-target"] .block-editor');feedback(page,'입력 상태')
                editor.evaluate("node=>delete node.dataset.imeComposing");assert sequence(authority,document)==seq
                url=f'{origin}/api/notion/workspaces/{workspace}/pages/{document}/synced-blocks/clip-original';body=page.request.get(url).text();held=[]
                page.route(url,lambda route:held.append(route));dispatch(page,json.dumps(descriptor),'[data-block-id="clip-target"] .block-editor')
                page.wait_for_timeout(150);assert held
                editor.fill('추가 입력 유지');prepare(page);held[0].fulfill(status=200,content_type='application/json',body=body);page.unroute(url)
                feedback(page,'위치가 변경');assert len(current(authority,workspace,document))==2
                expect(editor).to_have_text('추가 입력 유지')
                # A replaced editor DOM also invalidates the same pending paste.
                held=[];page.route(url,lambda route:held.append(route));dispatch(page,json.dumps(descriptor),'[data-block-id="clip-target"] .block-editor')
                page.wait_for_timeout(150);assert held
                editor.evaluate('node=>node.replaceWith(node.cloneNode(true))')
                held[0].fulfill(status=200,content_type='application/json',body=body);page.unroute(url)
                feedback(page,'위치가 변경');assert len(current(authority,workspace,document))==2
                # Navigate while an original read is held. The destination and
                # the old editor must both remain unchanged after the late read.
                dest=target_page(authority,workspace);page.goto(origin+'/page/'+dest)
                page.wait_for_function('id=>State.activePageId===id&&State.liveCollaboration.crdtReady',arg=dest,timeout=30_000);prepare(page)
                _seed_blocks(page,[{'id':'dest-safe','type':'paragraph','content':'목적지 보존','props':{}}]);prepare(page)
                dest_base=current(authority,workspace,dest);dest_seq=sequence(authority,dest)
                page.goto(origin+'/page/'+document);page.wait_for_function('id=>State.activePageId===id&&State.liveCollaboration.crdtReady',arg=document,timeout=30_000);prepare(page)
                held=[];page.route(url,lambda route:held.append(route));dispatch(page,json.dumps(descriptor),'[data-block-id="clip-target"] .block-editor')
                page.wait_for_timeout(150);assert held;page.goto(origin+'/page/'+dest)
                page.wait_for_function('id=>State.activePageId===id&&State.liveCollaboration.crdtReady',arg=dest,timeout=30_000);prepare(page)
                try:held[0].fulfill(status=200,content_type='application/json',body=body)
                except Exception:pass # Navigation may already have cancelled it.
                page.unroute(url);assert current(authority,workspace,dest)==dest_base and sequence(authority,dest)==dest_seq
                assert len(current(authority,workspace,document))==2
                page.screenshot(path=str(output(tmp_path)/'clipboard-guards.png'))
            finally:browser.close()

@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
def test_real_viewer_copy_target_edit_and_revoked_original_cannot_grant_paste(tmp_path):
    from playwright.sync_api import expect,sync_playwright
    with _isolated_collaborative_app(tmp_path,**DETACH_FLAGS) as (origin,password,authority,workspace,document):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            try:
                owner=browser.new_page();login(owner,origin,document,'owner',password);prepare(owner)
                _seed_blocks(owner,[original('clip-original','읽기만 가능한 원본')]);prepare(owner)
                dest=target_page(authority,workspace);owner.goto(origin+'/page/'+dest);owner.wait_for_function('id=>State.activePageId===id&&State.liveCollaboration.crdtReady',arg=dest,timeout=30_000)
                prepare(owner)
                _seed_blocks(owner,[{'id':'dest-target','type':'paragraph','content':'보존','props':{}}]);prepare(owner)
                grant(authority,document,'view','clipboard-read-source');grant(authority,dest,'edit','clipboard-edit-target')
                page=browser.new_page(locale='ko-KR');page.context.grant_permissions(['clipboard-read','clipboard-write'],origin=origin)
                login(page,origin,document,'content-editor',password);expect(page.locator('#pageTitle')).to_be_disabled()
                seq=sequence(authority,document);copy(page,'clip-original');assert sequence(authority,document)==seq
                page.goto(origin+'/page/'+dest);page.wait_for_function('id=>State.activePageId===id&&State.liveCollaboration.crdtReady',arg=dest,timeout=30_000)
                expect(page.locator('#pageTitle')).to_be_enabled();prepare(page);base=current(authority,workspace,dest);dest_seq=sequence(authority,dest)
                witness=authority.read_document('owner',document)
                authority.revoke_page_grant('owner',document,'subject','content-editor',expected_workspace_epoch=witness['workspace_epoch'],expected_document_epoch=witness['document_epoch'],operation_id='clipboard-revoke-source')
                source_url=f'{origin}/api/notion/workspaces/{workspace}/pages/{document}/synced-blocks/clip-original'
                with page.expect_response(lambda r:r.request.method=='GET' and r.url==source_url) as denied:page.locator('.block-editor').press('Control+v')
                assert denied.value.status in [403,404]
                expect(page.locator('.block-editor')).to_have_text('보존');assert current(authority,workspace,dest)==base and sequence(authority,dest)==dest_seq
                page.screenshot(path=str(output(tmp_path)/'clipboard-revoked-source.png'))
            finally:browser.close()
