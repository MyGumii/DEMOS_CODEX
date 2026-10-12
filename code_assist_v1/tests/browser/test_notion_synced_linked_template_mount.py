"""UI-SYNC-LINKED-TEMPLATE: physical writes and whole-template draft fences."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from code_assist_v1.tests.browser.synced_toolbar_helpers import open_synced_original

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.browser.test_notion_synced_linked_navigation import login, settled, chooser, source_database
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original, reference
from code_assist_v1.notion_collaboration import database_access


FLAGS=dict(locked=False,owned_database_copy=True,navigation_ui=True,database_activity=True,
    owned_database_trash=True,relation_pair=True,custom_emoji=True,item_page_layout=True,
    template_default=True,staged_database_sharing=True)


def prepare(page,authority,initial):
    from playwright.sync_api import expect
    source=source_database(page,initial);database=source['databaseId'];document=source['pageId']
    authority.execute_schema_command('owner',document,database_id=database,command_kind='database.template.create',target_id='linked-sync-template',
        payload={'name':'연결 동기화 템플릿','body':[original('linked-template-original','템플릿 원본 A'),
            reference('linked-template-peer',document,'linked-template-original'),
            {'id':'linked-template-ordinary','type':'paragraph','content':'일반 본문 A','props':{},'position':2}],
            'default_values':{}},expected_target_version=0,operation_id='linked-sync-prepare',
        **request(SimpleNamespace(iam=authority),document))
    page.reload(wait_until='domcontentloaded');settled(page)
    chooser(page);page.locator('[data-create-template="linked_database"]').click()
    option=page.locator('#newPageSourceResults .linked-source-option').filter(has_text='새 데이터베이스').first
    expect(option).to_be_visible(timeout=30_000);option.click()
    page.wait_for_function("source=>State.activePageId!==source.pageId&&State.pageData?.blocks.some(b=>b.props?.linked_database?.database_id===source.databaseId)&&!State.saving",arg=source,timeout=30_000)
    settled(page);return source,page.evaluate('State.activePageId')


def open_template(page):
    from playwright.sync_api import expect
    root=page.locator('[data-linked-database]').first
    expect(root.locator('[data-linked-template-picker]')).to_be_enabled(timeout=30_000)
    root.locator('[data-linked-template-picker]').click()
    edit=root.locator('[data-linked-template-edit="linked-sync-template"]')
    expect(edit).to_be_enabled(timeout=30_000);edit.click()
    expect(page.locator('#linkedTemplateEditName')).to_be_visible(timeout=30_000)
    return page.locator('#linkedTemplateBodyEditor')


def proof(page,name,tmp_path,errors,details=None):
    output=Path(os.environ.get('NOTION_SYNC_LINKED_TEMPLATE_ARTIFACT_DIR',str(tmp_path)));output.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(output/(name+'.png')))
    (output/(name+'.json')).write_text(json.dumps({'url':page.url,'page_errors':errors,'details':details},ensure_ascii=False,indent=2),encoding='utf-8')


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('mode,theme,width',[('clean','light',1280),('clean','dark',390),('stale','light',1280),('stale','dark',390)])
def test_linked_template_reference_save_adopts_clean_body_but_refuses_stale_whole_form(tmp_path,mode,theme,width):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect,sync_playwright
    with _isolated_collaborative_app(tmp_path,**FLAGS) as (origin,password,authority,workspace,initial):
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='chrome',headless=True)
            try:
                page=browser.new_page(locale='ko-KR',viewport={'width':width,'height':844});errors=[];patches=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('request',lambda r:patches.append(r.post_data_json) if r.method=='PATCH' and '/templates/' in r.url else None)
                login(page,origin,initial,'owner',password);page.evaluate('theme=>applyColorTheme(theme)',theme)
                source,visible=prepare(page,authority,initial);database=source['databaseId'];document=source['pageId']
                root=open_template(page);text=root.locator('[data-synced-reference="linked-template-peer"] [data-synced-text="linked-template-original"]')
                try:
                    expect(text).to_have_attribute('contenteditable','true',timeout=30_000)
                    expect(text).to_have_text('템플릿 원본 A')
                    expect(root.locator('.synced-reference-content b')).to_have_text('자식')
                    expect(root.locator('[data-body-text="linked-template-original"]')).to_have_attribute('contenteditable','false')
                    expect(root.locator('[data-body-text="linked-template-original-child"]')).to_have_attribute('contenteditable','false')
                    before=authority.read_database('owner',database)['templates'][0]
                    visible_sequence=authority.read_document('owner',visible)['document_sequence']
                    writer=page
                    if mode=='stale':
                        root.locator('[data-body-text="linked-template-ordinary"]').fill('이 창의 미저장 본문')
                        expect(text).to_have_attribute('contenteditable','false')
                        other=browser.new_context(locale='ko-KR',viewport={'width':width,'height':844});writer=other.new_page()
                        writer.on('pageerror',lambda error:errors.append(str(error)))
                        login(writer,origin,visible,'owner',password);open_template(writer)
                    write_text=writer.locator('#linkedTemplateBodyEditor [data-synced-text="linked-template-original"]')
                    expect(write_text).to_have_attribute('contenteditable','true',timeout=30_000)
                    with writer.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/synced-blocks/linked-template-peer/text')) as saved:
                        write_text.fill('참조에서 확정한 D')
                    assert saved.value.status==201,saved.value.text()
                    assert '/pages/'+document+'/synced-blocks/' in saved.value.url
                    assert saved.value.json()['storage']=='database_body'
                    page.wait_for_function("() => LinkedDatabaseViews.slot(document.querySelector('[data-linked-database]').dataset.linkedDatabase)?.data?.database?.templates?.some(t=>t.id==='linked-sync-template'&&t.version>=2)",timeout=30_000)
                    after_reference=authority.read_database('owner',database)['templates'][0]
                    assert after_reference['version']==before['version']+1
                    assert after_reference['body'][0]['content']=='참조에서 확정한 D'
                    assert after_reference['body'][1]['content']=='' and not after_reference['body'][1]['props'].get('children')
                    if mode=='clean':
                        expect(root.locator('[data-body-text="linked-template-original"]')).to_have_text('참조에서 확정한 D',timeout=30_000)
                        expect(page.locator('#linkedTemplateEditName')).to_be_enabled(timeout=30_000)
                        root.locator('[data-body-text="linked-template-ordinary"]').fill('일반 본문 B')
                        with page.expect_response(lambda r:r.request.method=='PATCH' and '/templates/' in r.url) as full_save:
                            page.locator('#modalActions button').click()
                        assert full_save.value.status==200,full_save.value.text()
                        expect(page.locator('#linkedTemplateEditName')).to_be_hidden(timeout=30_000)
                        latest=authority.read_database('owner',database)['templates'][0]
                        assert latest['version']==before['version']+2
                        assert latest['body'][0]['content']=='참조에서 확정한 D'
                        assert latest['body'][1]['content']=='' and not latest['body'][1]['props'].get('children')
                        assert latest['body'][2]['content']=='일반 본문 B'
                        assert patches[0]['expected_target_version']==after_reference['version']
                        assert 'document_id' not in patches[0]['source']
                        root=open_template(page)
                        expect(root.locator('[data-synced-locations]')).to_be_enabled(timeout=30_000)
                        open_synced_original(root)
                        page.wait_for_function('id=>State.activePageId===id',arg=document,timeout=30_000)
                        target=page.locator('#dbTemplateBodyEditor [data-body-text="linked-template-original"]')
                        expect(target).to_be_focused(timeout=30_000);expect(target).to_have_text('참조에서 확정한 D')
                    else:
                        page.locator('#modalActions button').click()
                        expect(page.locator('#linkedTemplateEditError')).to_contain_text('다른 곳에서 변경',timeout=30_000)
                        expect(root.locator('[data-body-text="linked-template-ordinary"]')).to_have_text('이 창의 미저장 본문')
                        assert patches==[],patches
                        assert authority.read_database('owner',database)['templates'][0]==after_reference
                        other.close()
                    assert authority.read_document('owner',visible)['document_sequence']==visible_sequence
                    proof(page,f'linked-template-{mode}-{theme}-{width}',tmp_path,errors,{'source':source,'reference_version':after_reference['version'],'patches':patches})
                    assert not errors,errors
                except Exception:
                    proof(page,f'linked-template-failure-{mode}-{theme}-{width}',tmp_path,errors,
                        page.evaluate("() => ({status:document.querySelector('#status')?.textContent,error:document.querySelector('#linkedTemplateEditError')?.textContent,body:document.querySelector('#linkedTemplateBodyEditor')?.innerHTML})"));raise
            finally:browser.close()


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
def test_linked_template_parent_access_revocation_redacts_open_body_name_and_defaults_without_write(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect,sync_playwright
    with _isolated_collaborative_app(tmp_path,**FLAGS) as (origin,password,authority,workspace,initial):
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='chrome',headless=True)
            try:
                owner=browser.new_page(locale='ko-KR');login(owner,origin,initial,'owner',password)
                source,visible=prepare(owner,authority,initial);database=source['databaseId'];document=source['pageId']
                access=database_access.resolve(authority,'owner',workspace,database)
                database_access.change_grant(authority,'owner',workspace,database,'subject','db-viewer',role='edit',
                    expected_workspace_epoch=access.workspace_epoch,expected_document_epoch=access.document_epoch,
                    expected_database_epoch=access.database_epoch,operation_id='linked-template-db-grant')
                for target in [document,visible]:
                    current=authority.read_document('owner',target)
                    authority.set_page_grant('owner',target,'subject','db-viewer',role='edit',inherit_to_descendants=False,
                        expected_workspace_epoch=current['workspace_epoch'],expected_document_epoch=current['document_epoch'],operation_id='linked-template-page-grant-'+target)
                context=browser.new_context(locale='ko-KR');page=context.new_page();errors=[];writes=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('request',lambda r:writes.append(r.url) if r.method in ['POST','PATCH'] and ('/synced-blocks/' in r.url or '/templates/' in r.url) else None)
                login(page,origin,visible,'db-viewer',password);root=open_template(page)
                expect(root.locator('[data-synced-text="linked-template-original"]')).to_have_text('템플릿 원본 A',timeout=30_000)
                canonical=authority.read_database('owner',database)['templates'][0]
                current=authority.read_document('owner',document)
                authority.revoke_page_grant('owner',document,'subject','db-viewer',expected_workspace_epoch=current['workspace_epoch'],
                    expected_document_epoch=current['document_epoch'],operation_id='linked-template-parent-revoke')
                expect(root).not_to_contain_text('템플릿 원본 A',timeout=30_000)
                expect(page.locator('#linkedTemplateEditName')).to_have_value('')
                expect(page.locator('#modalBg .database-template-defaults')).to_be_empty()
                expect(page.locator('#modalActions button')).to_be_disabled()
                assert writes==[],writes
                assert authority.read_database('owner',database)['templates'][0]==canonical
                proof(page,'linked-template-parent-revoked',tmp_path,errors);assert not errors,errors
                page.locator('#modalClose').click()
                expect(page.locator('#linkedTemplateEditName')).to_be_hidden()
                context.close()
            finally:browser.close()
