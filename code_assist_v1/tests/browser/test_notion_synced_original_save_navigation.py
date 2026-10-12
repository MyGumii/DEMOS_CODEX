"""UI-SYNC-ORIGINAL-SAVE: opening the source while after-save binding is held."""
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from code_assist_v1.tests.browser.synced_toolbar_helpers import open_synced_original

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.browser.test_notion_synced_linked_navigation import login, settled, source_database
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original, reference


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
def test_original_navigation_waits_for_ordinary_template_save_without_deferred_close_of_new_modal(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect,sync_playwright
    with _isolated_collaborative_app(tmp_path,locked=False,owned_database_copy=True,navigation_ui=True) as (origin,password,authority,workspace,initial):
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='chrome',headless=True)
            try:
                page=browser.new_page(locale='ko-KR',viewport={'width':1280,'height':844});errors=[];commands=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                login(page,origin,initial,'owner',password)
                source=source_database(page,initial);document=source['pageId'];database=source['databaseId'];target='save-navigation-template'
                authority.execute_schema_command('owner',document,database_id=database,command_kind='database.template.create',target_id=target,
                    payload={'name':'저장 중 원본 이동','body':[original('save-original','공유 원본 A'),reference('save-peer',document,'save-original'),
                        {'id':'save-ordinary','type':'paragraph','content':'일반 본문 A','props':{}}],'default_values':{}},
                    expected_target_version=0,operation_id='save-navigation-prepare',**request(SimpleNamespace(iam=authority),document))
                page.reload(wait_until='domcontentloaded');settled(page)
                table=page.locator('[data-db-template-picker]').first
                table.click();page.locator(f'[data-db-template-more="{target}"]').click();page.locator(f'[data-db-template-edit="{target}"]').click()
                root=page.locator('#dbTemplateBodyEditor');peer=root.locator('[data-synced-reference="save-peer"]')
                expect(peer.locator('[data-synced-text="save-original"]')).to_be_editable(timeout=30_000)
                expect(peer.locator('[data-synced-locations]')).to_be_enabled()
                page.on('request',lambda r:commands.append(r.url) if r.method=='POST' and r.url.endswith('/databases/commands') else None)
                held=[];released=False;pattern='**/pages/'+document+'/synced-blocks/save-peer'
                def hold(route):
                    if released:route.continue_()
                    else:held.append(route)
                page.route(pattern,hold)
                output=Path(os.environ.get('NOTION_SYNC_HIDDEN_ARTIFACT_DIR',str(tmp_path)));output.mkdir(parents=True,exist_ok=True)
                try:
                    with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/databases/commands')) as saved:
                        root.locator('[data-body-text="save-ordinary"]').fill('일반 본문 B')
                    assert saved.value.status==201,saved.value.text()
                    expect(page.locator('#dbTemplateSaveState')).to_have_text('모든 변경 사항 저장됨',timeout=30_000)
                    page.wait_for_function('() => !ActiveDatabaseTemplateEditor.dirty()',timeout=30_000)
                    deadline=time.monotonic()+5
                    while not held and time.monotonic()<deadline:page.wait_for_timeout(25)
                    assert len(held)==1
                    # The after-save GET is deliberately unfinished. This is
                    # the real window in which beforeClose used to schedule a
                    # deferred close and discard the requested navigation.
                    open_synced_original(peer)
                    deadline=time.monotonic()+5
                    while len(held)<2 and time.monotonic()<deadline:page.wait_for_timeout(25)
                    assert len(held)==2
                    response=page.request.get(origin+f'/api/notion/workspaces/{workspace}/pages/{document}/synced-blocks/save-peer')
                    assert response.status==200;fresh=response.json()
                    released=True
                    for route in list(held):route.fulfill(status=200,json=fresh,headers={'Cache-Control':'no-store'})
                    expect(root.locator('[data-body-text="save-original"]')).to_be_focused(timeout=30_000)
                    expect(root.locator('[data-body-text="save-ordinary"]')).to_have_text('일반 본문 B')
                    expect(peer.locator('[data-synced-text="save-original"]')).to_have_text('공유 원본 A',timeout=30_000)
                    expect(page.locator('#modalBg')).to_be_visible()
                    page.unroute(pattern,hold)
                    assert len(commands)==1 and not errors,(commands,errors)
                    template=next(item for item in authority.read_database('owner',database)['templates'] if item['id']==target)
                    assert template['body'][0]['content']=='공유 원본 A' and template['body'][2]['content']=='일반 본문 B'
                    assert template['body'][1]['props']['synced_from']=={'document_id':document,'block_id':'save-original'}
                    page.screenshot(path=str(output/'original-save-navigation.png'))
                    (output/'original-save-navigation.json').write_text(json.dumps({'held_reads':len(held),'ordinary_commands':len(commands),
                        'page_errors':errors,'focus':page.evaluate('document.activeElement?.getAttribute("data-body-text")'),
                        'template_version':template['version'],'source_preserved':True},ensure_ascii=False,indent=2),encoding='utf-8')
                except Exception:
                    page.screenshot(path=str(output/'original-save-navigation-failure.png'))
                    (output/'original-save-navigation-failure.json').write_text(json.dumps(page.evaluate("""() => ({status:document.querySelector('#status')?.textContent,
                        modal:document.querySelector('#modalBg')?.outerHTML,focus:document.activeElement?.outerHTML,dirty:ActiveDatabaseTemplateEditor?.dirty?.()})"""),ensure_ascii=False,indent=2),encoding='utf-8')
                    page.unroute_all(behavior='ignoreErrors');raise
            finally:browser.close()
