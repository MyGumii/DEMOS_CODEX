"""UI-SYNC-LOCATIONS: real roles, current peer navigation and cancelled menus."""
import json
import os
import traceback
from pathlib import Path
from types import SimpleNamespace
import pytest
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.browser.test_notion_synced_database_detach_browser import DETACH_FLAGS
from code_assist_v1.tests.browser.test_notion_synced_linked_navigation import login,settled,source_database
from code_assist_v1.tests.browser.test_notion_document_extras import _seed_blocks
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original,reference
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request
from code_assist_v1.notion_collaboration.page_creation import create_page

def output(tmp_path):
    value=Path(os.environ.get('NOTION_SYNC_LOCATIONS_ARTIFACT_DIR',str(tmp_path)));value.mkdir(parents=True,exist_ok=True);return value

def grant(authority,document,actor,op):
    page=authority.read_document('owner',document)
    authority.set_page_grant('owner',document,'subject',actor,role='view',inherit_to_descendants=False,
        expected_workspace_epoch=page['workspace_epoch'],expected_document_epoch=page['document_epoch'],operation_id=op)

def create_peer(authority,workspace,initial,document,title):
    with authority._reader() as con:version=authority._workspace(con,workspace)['row_version']
    created=create_page(authority,'owner',workspace,title=title,parent_id='',base_version=version,operation_id=document+'-create')['page']['id']
    block=reference(document+'-peer',initial,'list-original')
    authority.execute_typed_command('owner',created,command_kind='block.insert',target_id=block['id'],
        payload={key:block[key] for key in ('type','content','props')}|{'after_block_id':None},operation_id=document+'-insert',**request(SimpleNamespace(iam=authority),created))
    return created

@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('actor,theme,width',[('owner','light',1280),('owner','dark',390),('content-editor','light',1280),('content-editor','dark',390)])
def test_location_list_real_roles_source_peer_navigation_and_late_close(tmp_path,actor,theme,width):
    from playwright.sync_api import expect,sync_playwright
    proof=output(tmp_path)
    with _isolated_collaborative_app(tmp_path,**DETACH_FLAGS) as (origin,password,authority,workspace,initial):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            try:
                owner=browser.new_page(locale='ko-KR');login(owner,origin,initial,'owner',password)
                _seed_blocks(owner,[original('list-original','공유 A'),reference('list-peer',initial,'list-original'),{'id':'sibling','type':'paragraph','content':'보존','props':{}}]);settled(owner)
                second=create_peer(authority,workspace,initial,'locations-second','대상 둘')
                private=create_peer(authority,workspace,initial,'locations-private','비공개 대상')
                # Fresh native pages publish their missing deterministic title
                # CRDT root once at first editable startup. Prepare that state
                # before measuring the read-only list/navigation operations.
                for prepared in (second,private):
                    owner.goto(origin+'/page/'+prepared)
                    owner.wait_for_function("target=>State.activePageId===target&&State.liveCollaboration.crdtReady",arg=prepared,timeout=30_000)
                    owner.evaluate('async()=>{await State.liveCollaboration.persistenceChain}')
                    settled(owner)
                    expect(owner.locator('[data-synced-reference]')).to_have_count(1)
                    assert authority.read_document('owner',prepared)['document_sequence']==3
                owner.goto(origin+'/page/'+initial)
                owner.wait_for_function("target=>State.activePageId===target&&State.liveCollaboration.crdtReady",arg=initial,timeout=30_000)
                settled(owner)
                if actor!='owner':
                    grant(authority,initial,actor,'locations-view-main');grant(authority,second,actor,'locations-view-second')
                page=owner if actor=='owner' else browser.new_page(locale='ko-KR')
                if actor!='owner':login(page,origin,initial,actor,password)
                page.set_viewport_size({'width':width,'height':844});page.evaluate('theme=>applyColorTheme(theme)',theme);settled(page)
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                before={doc:authority.read_document('owner',doc)['document_sequence'] for doc in [initial,second,private]}
                root=page.locator('[data-synced-reference="list-peer"]');trigger=root.locator('[data-synced-locations]');menu=root.locator('[data-synced-locations-panel]')
                expect(trigger).to_be_enabled(timeout=30_000);trigger.press('ArrowDown')
                expect(root.locator('[data-synced-location-status]')).to_be_hidden(timeout=30_000)
                expected=3 if actor=='owner' else 2
                expect(root.locator('[data-synced-location-count]')).to_have_text(f'{expected}곳에 동기화')
                expect(root.locator('[data-synced-peer]')).to_have_count(expected-1)
                expect(root.locator('[data-synced-open]')).to_be_focused()
                if actor!='owner':expect(menu).not_to_contain_text('비공개 대상')
                bounds=menu.bounding_box();assert bounds and bounds['x']>=7 and bounds['x']+bounds['width']<=width-7
                page.screenshot(path=str(proof/f'locations-{actor}-{theme}-{width}.png'))
                root.locator('[data-synced-open]').press('ArrowDown');expect(root.locator('[data-synced-peer]').first).to_be_focused()
                root.locator('[data-synced-peer]').first.press('Escape');expect(trigger).to_be_focused();expect(menu).to_be_hidden()
                source=page.locator('[data-synced-source="list-original"]');source.locator('[data-synced-locations]').press('Enter')
                expect(source.locator('[data-synced-peer]')).to_have_count(expected-1,timeout=30_000)
                expect(source.locator('[data-synced-open]')).to_be_visible()
                source.locator('[data-synced-peer]').filter(has_text='대상 둘').press('Enter')
                page.wait_for_function("target=>State.activePageId===target&&State.liveCollaboration.crdtReady",arg=second,timeout=30_000)
                expect(page.locator('[data-synced-reference="locations-second-peer"] [data-synced-actions-menu]')).to_be_focused(timeout=30_000)
                page.goto(origin+'/page/'+initial);settled(page);expect(trigger).to_be_enabled(timeout=30_000)
                # Hold an actual response, close the popup, then deliver it. It
                # cannot recreate a dismissed list or steal focus.
                url=f'{origin}/api/notion/workspaces/{workspace}/pages/{initial}/synced-blocks/list-peer/locations'
                response=page.request.get(url);assert response.ok,response.text()
                held=[];page.route(url,lambda route:held.append(route))
                trigger.press('Enter');expect(root.locator('[data-synced-location-status]')).to_be_visible()
                page.wait_for_timeout(100)
                assert held
                root.locator('[data-synced-open]').press('Escape');expect(menu).to_be_hidden();expect(trigger).to_be_focused()
                try:held[0].fulfill(status=200,content_type='application/json',body=response.text())
                except Exception:pass # Aborted browser request may already be gone.
                page.unroute(url);expect(menu).to_be_hidden();expect(root.locator('[data-synced-peer]')).to_have_count(0)
                if actor!='owner':
                    trigger.press('Enter')
                    target_button=root.locator('[data-synced-peer]').filter(has_text='대상 둘')
                    expect(target_button).to_be_visible(timeout=30_000)
                    target=authority.read_document('owner',second)
                    authority.revoke_page_grant('owner',second,'subject',actor,
                        expected_workspace_epoch=target['workspace_epoch'],expected_document_epoch=target['document_epoch'],operation_id='locations-revoke-second')
                    # A label already displayed by a previous read cannot grant
                    # navigation authority after the target grant is revoked.
                    with page.expect_response(lambda r:r.request.method=='GET' and r.url==url) as rechecked:
                        target_button.press('Enter')
                    assert rechecked.value.ok and rechecked.value.json()['other_count']==1
                    expect(page.locator('#status')).to_contain_text('접근권한이 변경',timeout=30_000)
                    assert page.evaluate('State.activePageId')==initial
                    trigger.press('Enter');expect(root.locator('[data-synced-location-count]')).to_have_text('1곳에 동기화',timeout=30_000)
                    expect(menu).not_to_contain_text('대상 둘');expect(root.locator('[data-synced-peer]')).to_have_count(0)
                after={doc:authority.read_document('owner',doc)['document_sequence'] for doc in before}
                assert after==before and not errors,errors
                (proof/f'locations-{actor}-{theme}-{width}.json').write_text(json.dumps({'before':before,'after':after,'bounds':bounds,'role':actor,'page_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
            except Exception:
                (proof/f'failure-{actor}-{theme}-{width}.txt').write_text(traceback.format_exc(),encoding='utf-8')
                if 'page' in locals():page.screenshot(path=str(proof/f'failure-{actor}-{theme}-{width}.png'))
                raise
            finally:browser.close()

@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('source_kind',['row','template'])
def test_hidden_peer_list_opens_exact_current_reference_tree_readonly_without_writes(tmp_path,source_kind):
    from playwright.sync_api import expect,sync_playwright
    from code_assist_v1.tests.browser.test_notion_synced_hidden_original import prepare,add_reference
    proof=output(tmp_path)
    with _isolated_collaborative_app(tmp_path,**DETACH_FLAGS) as (origin,password,authority,workspace,document):
        database,_,_=prepare(authority,document,source_kind)
        peer_kind='template' if source_kind=='row' else 'row'
        peer_id='hidden-locations-peer';target='hidden-locations-body'
        body=[reference(peer_id,document,'hidden-original')]
        store=SimpleNamespace(iam=authority)
        if peer_kind=='row':
            authority.execute_surface_command('owner',document,database_id=database,command_kind='database.row.create',target_id=target,
                payload={},expected_target_version=0,operation_id='hidden-locations-row',**request(store,document))
            row=next(item for item in authority.read_database('owner',database)['rows'] if item['id']==target)
            authority.execute_surface_command('owner',document,database_id=database,command_kind='database.row.content.set',target_id=target,
                payload={'blocks':body},expected_target_version=row['version'],operation_id='hidden-locations-body',**request(store,document))
        else:
            authority.execute_schema_command('owner',document,database_id=database,command_kind='database.template.create',target_id=target,
                payload={'name':'표 없는 참조 템플릿','body':body,'default_values':{}},expected_target_version=0,
                operation_id='hidden-locations-body',**request(store,document))
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            try:
                owner=browser.new_page(locale='ko-KR');login(owner,origin,document,'owner',password);settled(owner)
                add_reference(owner,document);grant(authority,document,'content-editor','hidden-locations-view')
                page=browser.new_page(locale='ko-KR',viewport={'width':390,'height':844})
                login(page,origin,document,'content-editor',password);settled(page)
                page.evaluate("applyColorTheme('dark')")
                errors=[];mutations=[];reads=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('request',lambda r:mutations.append(r.url)
                    if r.method in {'POST','PATCH','DELETE'} and ('/commands' in r.url or '/synced-blocks/' in r.url) else None)
                page.on('response',lambda r:reads.append(r.url) if r.request.method=='GET' and '/synced-blocks/' in r.url else None)
                before=authority.read_database('owner',database);sequence=authority.read_document('owner',document)['document_sequence']
                peer=page.locator('[data-synced-reference="hidden-peer"]')
                expect(peer.locator('[data-synced-locations]')).to_be_enabled(timeout=30_000)
                peer.locator('[data-synced-locations]').press('Enter')
                expect(peer.locator('[data-synced-peer]')).to_have_count(2,timeout=30_000)
                endpoint={'document_id':document,'block_id':peer_id}
                chosen=next(button for button in peer.locator('[data-synced-peer]').all()
                    if json.loads(button.get_attribute('data-synced-peer'))==endpoint)
                chosen.press('Enter')
                root=page.locator('#syncedOriginalReadBody')
                expect(root.locator('[data-synced-node="hidden-original"]')).to_be_focused(timeout=30_000)
                expect(root).to_contain_text('숨겨진 표의 원본 A')
                expect(root.locator('[contenteditable="true"]')).to_have_count(0)
                expect(page.locator('#modalBody')).to_contain_text(('항목' if peer_kind=='row' else '템플릿')+' 본문 · 보기 전용')
                assert page.evaluate("id=>State.pageData.blocks.some(b=>b.props?.database_id===id||b.id===id)",database) is False
                endpoint_url='**/synced-blocks/'+peer_id
                def wrong_original_location(route):
                    response=route.fetch();value=response.json();value['locations']['origin']['database_id']='wrong-source-database'
                    route.fulfill(response=response,json=value)
                page.route(endpoint_url,wrong_original_location)
                page.locator('#syncedOriginalReadRefresh').click()
                expect(page.locator('#syncedOriginalReadStatus')).to_contain_text('표시할 수 없습니다',timeout=30_000)
                expect(root).to_be_empty()
                page.unroute(endpoint_url,wrong_original_location)
                page.locator('#syncedOriginalReadRefresh').click()
                expect(root).to_contain_text('숨겨진 표의 원본 A',timeout=30_000)
                assert authority.read_database('owner',database)==before
                assert authority.read_document('owner',document)['document_sequence']==sequence
                assert any('/synced-blocks/'+peer_id in url for url in reads) and not errors and not mutations
                page.screenshot(path=str(proof/f'hidden-location-peer-{source_kind}.png'))
                (proof/f'hidden-location-peer-{source_kind}.json').write_text(json.dumps({'source_kind':source_kind,
                    'peer_kind':peer_kind,'sequence':sequence,'mutations':mutations,'page_errors':errors,'reads':reads},ensure_ascii=False,indent=2),encoding='utf-8')
            except Exception:
                if 'page' in locals():page.screenshot(path=str(proof/f'hidden-location-peer-failure-{source_kind}.png'))
                (proof/f'hidden-location-peer-failure-{source_kind}.txt').write_text(traceback.format_exc(),encoding='utf-8')
                raise
            finally:browser.close()

@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('theme,width',[('light',1280),('dark',390)])
def test_db_source_and_reference_list_navigates_exact_template_and_main_peer_without_writes(tmp_path,theme,width):
    from playwright.sync_api import expect,sync_playwright
    proof=output(tmp_path)
    with _isolated_collaborative_app(tmp_path,**DETACH_FLAGS) as (origin,password,authority,workspace,initial):
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            try:
                page=browser.new_page(locale='ko-KR',viewport={'width':width,'height':844});login(page,origin,initial,'owner',password)
                page.evaluate('theme=>applyColorTheme(theme)',theme);source=source_database(page,initial);document=source['pageId'];db=source['databaseId']
                store=SimpleNamespace(iam=authority);row=authority.read_database('owner',db)['rows'][0]
                authority.execute_surface_command('owner',document,database_id=db,command_kind='database.row.content.set',target_id=row['id'],
                    payload={'blocks':[original('db-list-original','항목 원본'),reference('row-list-peer',document,'db-list-original')]},expected_target_version=row['version'],operation_id='db-locations-row',**request(store,document))
                authority.execute_schema_command('owner',document,database_id=db,command_kind='database.template.create',target_id='db-location-template',
                    payload={'name':'대상 템플릿','body':[reference('template-list-peer',document,'db-list-original')],'default_values':{}},expected_target_version=0,operation_id='db-locations-template',**request(store,document))
                page.reload(wait_until='domcontentloaded');settled(page)
                # Main structure belongs to the active Yjs editor, unlike DB
                # row/template bodies. Seed the peer through that editor rather
                # than adding a canonical-only block behind its existing frames.
                blocks=page.evaluate('State.pageData.blocks')
                _seed_blocks(page,blocks+[reference('main-list-peer',document,'db-list-original')]);settled(page)
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                before=authority.read_document('owner',document)['document_sequence']
                peer=page.locator('[data-synced-reference="main-list-peer"]');trigger=peer.locator('[data-synced-locations]')
                expect(trigger).to_be_enabled(timeout=30_000);trigger.press('Enter')
                expect(peer.locator('[data-synced-location-count]')).to_have_text('3곳에 동기화',timeout=30_000)
                expect(peer.locator('[data-synced-open]')).to_contain_text('항목 본문')
                peer.locator('[data-synced-peer]').filter(has_text='대상 템플릿').press('Enter')
                template=page.locator('#dbTemplateBodyEditor [data-synced-reference="template-list-peer"]')
                expect(template.locator('[data-synced-actions-menu]')).to_be_focused(timeout=30_000)
                expect(template.locator('[data-synced-locations]')).to_be_enabled(timeout=30_000)
                template.locator('[data-synced-locations]').press('Enter')
                expect(template.locator('[data-synced-location-count]')).to_have_text('3곳에 동기화',timeout=30_000)
                template.locator('[data-synced-open]').press('Enter')
                source=page.locator('[data-item-content] [data-synced-source="db-list-original"]')
                expect(source.locator('[data-body-text="db-list-original"]')).to_be_focused(timeout=30_000)
                source.locator('[data-synced-locations]').press('Enter')
                expect(source.locator('[data-synced-peer]')).to_have_count(2,timeout=30_000)
                page.screenshot(path=str(proof/f'db-locations-{theme}-{width}.png'))
                # The exact endpoint identifies the main peer despite identical
                # page names on all three independently owned body locations.
                endpoint={'document_id':document,'block_id':'main-list-peer'}
                target=next(button for button in source.locator('[data-synced-peer]').all()
                    if json.loads(button.get_attribute('data-synced-peer'))==endpoint)
                target.press('Enter')
                expect(page.locator('[data-synced-reference="main-list-peer"] [data-synced-actions-menu]')).to_be_focused(timeout=30_000)
                assert authority.read_document('owner',document)['document_sequence']==before and not errors,errors
                (proof/f'db-locations-{theme}-{width}.json').write_text(json.dumps({'sequence':before,'page_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
            except Exception:
                if 'page' in locals():page.screenshot(path=str(proof/f'db-failure-{theme}-{width}.png'))
                raise
            finally:browser.close()
