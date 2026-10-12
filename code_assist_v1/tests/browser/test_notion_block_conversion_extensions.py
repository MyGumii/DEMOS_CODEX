"""Existing app-specific tab/DB conversions preserve body and permission boundaries."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _seed_blocks
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready


def _body(block):
    return {key:block.get(key) for key in ('id','type','content','props')}


def _conversion(page, block_id, query, kind):
    from playwright.sync_api import expect
    row=page.locator(f'#blocks .block-row[data-block-id="{block_id}"]')
    row.hover()
    row.locator('.block-handle').click()
    search=page.locator('#blockMenu .menu-search')
    expect(search).to_be_focused()
    search.fill(query)
    command=page.locator(f'#blockMenu [data-convert-type="{kind}"]')
    expect(command).to_be_visible()
    command.click()
    expect(page.locator('#blockMenu')).to_be_hidden()
    page.wait_for_function('() => !State.dirty && !State.saving',timeout=20000)


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('theme,width',[('light',1280),('dark',390)])
def test_legacy_database_conversion_titles_and_siblings_survive_reload(document_page,tmp_path,theme,width):
    from playwright.sync_api import expect
    page=document_page
    page.set_viewport_size({'width':width,'height':800})
    page.evaluate('theme => applyColorTheme(theme)',theme)
    origin='/'.join(page.url.split('/',3)[:3])
    page_id=page.evaluate('State.activePageId')
    url=origin+'/api/notion/pages/'+page_id
    records=[]
    for kind,label in [('table','표 보기'),('board','보드 보기'),('list','목록 보기'),
                       ('gallery','갤러리 보기'),('calendar','캘린더 보기'),('timeline','타임라인 보기')]:
        _seed_blocks(page,[{'type':'paragraph','content':'전체 문장을 DB 제목으로 유지','props':{'text_color':'red'}},
            {'type':'paragraph','content':'DB 밖의 다음 문장'}])
        before=page.request.get(url).json()['blocks']
        _conversion(page,before[0]['id'],label,kind)
        after=page.request.get(url).json()['blocks']
        assert after[0]['id']==before[0]['id'] and after[0]['type']==kind
        assert after[0]['content']==before[0]['content']
        assert _body(after[1])==_body(before[1])
        assert len(after)==2 and after[0]['props']['rows']==[]
        assert after[0]['props']['view']==kind and after[0]['props']['text_color']=='red'
        row=page.locator(f'#block-{after[0]["id"]}')
        row.hover()
        row.locator('.block-handle').click()
        expect(page.locator('#blockMenu [data-submenu="convert"]')).to_have_count(0)
        page.locator('#blockMenu .menu-search').fill('전환')
        expect(page.locator('#blockMenu [data-convert-type]')).to_have_count(0)
        page.locator('#blockMenu .menu-search').press('Escape')
        assert page.request.get(url).json()['blocks']==after
        page.reload()
        _wait_for_notion_ready(page)
        assert [_body(block) for block in page.request.get(url).json()['blocks']]==[_body(block) for block in after]
        assert page.evaluate('State.activePageId')==page_id
        records.append({'kind':kind,'blocks':after})
    output=Path(os.environ.get('NOTION_EXTENSION_CONVERSION_ARTIFACT_DIR',str(tmp_path)))
    output.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(output/f'legacy-database-conversion-{theme}-{width}.png'))
    (output/f'legacy-database-conversion-{theme}-{width}.json').write_text(
        json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('theme,width',[('light',1280),('dark',390)])
def test_tab_conversion_and_conversion_out_keep_all_panels_and_owned_children(document_page,tmp_path,theme,width):
    from playwright.sync_api import expect
    page=document_page
    page.set_viewport_size({'width':width,'height':800})
    page.evaluate('theme => applyColorTheme(theme)',theme)
    origin='/'.join(page.url.split('/',3)[:3])
    page_id=page.evaluate('State.activePageId')
    url=origin+'/api/notion/pages/'+page_id
    _seed_blocks(page,[{'id':'extension-owner','type':'callout','content':'앞 선택 뒤','props':{
        'children':['extension-child'],'rich_text_html':'앞 <b>선택</b> 뒤','text_color':'red'}},
        {'id':'extension-child','type':'paragraph','content':'소유한 하위 문장','props':{'rich_text_html':'소유한 <i>하위</i> 문장'}},
        {'id':'extension-tail','type':'paragraph','content':'탭 밖의 다음 문장'}])
    before=page.request.get(url).json()['blocks']
    _conversion(page,before[0]['id'],'탭','tabs')
    wrapped=page.request.get(url).json()['blocks']
    controller=next(block for block in wrapped if block['type']=='tabs')
    source=next(block for block in wrapped if block['id']==before[0]['id'])
    child=next(block for block in wrapped if block['id']==before[1]['id'])
    tail=next(block for block in wrapped if block['id']==before[2]['id'])
    assert source['content']==before[0]['content'] and source['type']=='callout'
    assert source['props']['rich_text_html']==before[0]['props']['rich_text_html']
    assert source['props']['text_color']=='red' and source['props']['children']==[child['id']]
    assert child['content']==before[1]['content'] and child['props']['rich_text_html']==before[1]['props']['rich_text_html']
    assert _body(tail)==_body(before[2])
    tabs=controller['props']['tabs']
    bar=page.locator(f'#block-{controller["id"]}')
    bar.locator(f'[data-tab-select="{tabs[1]["id"]}"]').click()
    expect(page.locator(f'#block-{source["id"]}')).to_be_hidden()
    bar.locator(f'[data-tab-select="{tabs[0]["id"]}"]').click()
    expect(page.locator(f'#block-{source["id"]}')).to_be_visible()
    _conversion(page,controller['id'],'제목 1','heading1')
    after=page.request.get(url).json()['blocks']
    assert next(block for block in after if block['id']==controller['id'])['type']=='heading1'
    for previous in (source,child,tail):
        current=next(block for block in after if block['id']==previous['id'])
        assert current['content']==previous['content'] and current['type']==previous['type']
        if previous['id']!=tail['id']:
            assert current['props'].get('rich_text_html')==previous['props'].get('rich_text_html')
            assert 'tab_group' not in current['props']
        else:assert _body(current)==_body(previous)
    expect(page.locator(f'#block-{child["id"]}')).to_be_visible()
    page.reload()
    _wait_for_notion_ready(page)
    expect(page.locator(f'#block-{source["id"]}')).to_be_visible()
    expect(page.locator(f'#block-{child["id"]}')).to_be_visible()
    assert [_body(block) for block in page.request.get(url).json()['blocks']]==[_body(block) for block in after]
    assert page.evaluate('State.activePageId')==page_id
    output=Path(os.environ.get('NOTION_EXTENSION_CONVERSION_ARTIFACT_DIR',str(tmp_path)))
    output.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(output/f'tabs-converted-out-{theme}-{width}.png'))
    (output/f'tabs-converted-out-{theme}-{width}.json').write_text(
        json.dumps({'before':before,'wrapped':wrapped,'after':after},ensure_ascii=False,indent=2),encoding='utf-8')


@pytest.mark.integration
@pytest.mark.regression
def test_collaborative_conversion_choices_and_writes_follow_real_page_grants(tmp_path):
    from playwright.sync_api import expect, sync_playwright
    from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
    from code_assist_v1.tests.browser.test_notion_synced_source_menu import _current_collaborative_body

    database_types=('table','board','list','gallery','calendar','timeline')
    output=Path(os.environ.get('NOTION_EXTENSION_CONVERSION_ARTIFACT_DIR',str(tmp_path)))
    output.mkdir(parents=True,exist_ok=True)
    errors=[]
    observations=[]
    with _isolated_collaborative_app(tmp_path,locked=False,navigation_database_favorites=True) as (
        origin,password,authority,workspace_id,page_id,
    ):
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='chrome',headless=True)
            try:
                def login(subject,width=1280):
                    context=browser.new_context(locale='ko-KR',viewport={'width':width,'height':800})
                    page=context.new_page()
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(origin+'/page/'+page_id)
                    page.locator('#userId').fill(subject)
                    page.locator('#password').fill(password)
                    page.locator('#submitBtn').click()
                    _wait_for_notion_ready(page)
                    page.wait_for_function('() => State.liveCollaboration.crdtReady',timeout=30000)
                    if subject!='owner':
                        page.evaluate('id => localStorage.setItem(navigationSelectionKey(),id)',workspace_id)
                        page.goto(origin+'/page/'+page_id)
                        _wait_for_notion_ready(page)
                        page.wait_for_function('() => State.liveCollaboration.crdtReady',timeout=30000)
                    return context,page

                def wait_saved(page):
                    page.wait_for_function('() => !State.dirty && !State.saving && '
                        '!State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive',timeout=30000)

                def inspect_choices(page,subject,allowed):
                    row=page.locator('#block-permission-database')
                    row.hover()
                    row.locator('.block-handle').click()
                    search=page.locator('#blockMenu .menu-search')
                    search.fill('전환')
                    visible=[]
                    for kind in database_types:
                        command=page.locator(f'#blockMenu [data-convert-type="{kind}"]:visible')
                        expect(command).to_have_count(1 if allowed else 0)
                        if allowed:visible.append(kind)
                    expect(page.locator('#blockMenu [data-convert-type="tabs"]:visible')).to_have_count(1)
                    page.screenshot(path=str(output/f'collaborative-conversion-{subject}.png'))
                    observations.append({'subject':subject,'role':page.evaluate('State.pageData.page.iam_role'),
                        'database_choices':visible,'tabs':True})
                    search.press('Escape')

                owner_context,owner=login('owner')
                _seed_blocks(owner,[{'id':'permission-database','type':'paragraph','content':'DB로 전환하는 전체 제목'},
                    {'id':'permission-tabs','type':'paragraph','content':'본문 편집자가 탭에 보존하는 문장',
                        'props':{'rich_text_html':'본문 편집자가 <b>탭에 보존</b>하는 문장'}},
                    {'id':'permission-tail','type':'paragraph','content':'항상 남아 있는 다음 문장'}])
                wait_saved(owner)
                before=_current_collaborative_body(authority,'owner',page_id,workspace_id)
                inspect_choices(owner,'owner',True)
                owner_context.close()

                content_context,content=login('content-editor')
                assert content.evaluate('State.pageData.page.iam_role')=='edit_content'
                inspect_choices(content,'content-editor',False)
                _conversion(content,'permission-tabs','탭','tabs')
                wait_saved(content)
                wrapped=_current_collaborative_body(authority,'owner',page_id,workspace_id)
                assert any(block['type']=='tabs' for block in wrapped)
                for old in before:
                    current=next(block for block in wrapped if block['id']==old['id'])
                    assert current['type']==old['type'] and current['content']==old['content']
                    if old['id']=='permission-tabs':
                        assert current['props']['rich_text_html']==old['props']['rich_text_html']
                    else:assert _body(current)==_body(old)
                content.reload()
                _wait_for_notion_ready(content)
                content.wait_for_function('() => State.liveCollaboration.crdtReady',timeout=30000)
                expect(content.locator('#block-permission-tabs')).to_be_visible()
                content_context.close()

                schema_context,schema=login('schema-editor')
                assert schema.evaluate('State.pageData.page.iam_role')=='edit'
                inspect_choices(schema,'schema-editor',True)
                _conversion(schema,'permission-database','표 보기','table')
                schema.wait_for_function("""() => {
                  const block=State.pageData.blocks.find(item=>item.id==='permission-database');
                  return block?.props?._canonical_version!=null && !State.dirty
                    && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive
                    && !State.databaseSync.timer && !State.databaseSync.inFlight
                    && DatabaseBridge.diffPage(State.pageData.blocks,State.pageData.databases).length===0;
                }""",timeout=30000)
                canonical=schema.evaluate("State.pageData.blocks.find(item=>item.id==='permission-database')")
                assert canonical['type']=='table' and canonical['content']==before[0]['content']
                database_id=canonical['props']['database_id']
                database=schema.evaluate('id => State.pageData.databases.find(item=>item.id===id)',database_id)
                assert database and database['id']==database_id
                schema.reload()
                _wait_for_notion_ready(schema)
                schema.wait_for_function('() => State.liveCollaboration.crdtReady',timeout=30000)
                persisted=schema.evaluate("State.pageData.blocks.find(item=>item.id==='permission-database')")
                assert persisted['type']=='table' and persisted['props']['database_id']==database_id
                assert persisted['content']==before[0]['content']
                schema.screenshot(path=str(output/'collaborative-conversion-canonical-table.png'))
                schema_context.close()
                after=_current_collaborative_body(authority,'owner',page_id,workspace_id)
                for old in wrapped:
                    if old['id']!='permission-database':
                        assert _body(next(block for block in after if block['id']==old['id']))==_body(old)

                for role in ('view','comment'):
                    witness=authority.read_document('owner',page_id)
                    authority.set_page_grant('owner',page_id,'subject','content-editor',role=role,
                        inherit_to_descendants=False,expected_workspace_epoch=witness['workspace_epoch'],
                        expected_document_epoch=witness['document_epoch'],operation_id='extension-'+role+'-grant')
                    readonly_context,readonly=login('content-editor',390)
                    expect(readonly.locator('#pageTitle')).to_be_disabled(timeout=30000)
                    assert readonly.evaluate('State.pageData.page.iam_role')==role
                    for handle in readonly.locator('#blocks .block-handle').all():
                        expect(handle).to_be_disabled()
                        expect(handle).to_be_hidden()
                    expect(readonly.locator('#blockMenu')).to_be_hidden()
                    assert not readonly.evaluate('activePageCanEdit()')
                    readonly.screenshot(path=str(output/f'collaborative-conversion-{role}-390.png'))
                    observations.append({'subject':'content-editor','role':role,'can_edit':False})
                    readonly_context.close()
                    assert _current_collaborative_body(authority,'owner',page_id,workspace_id)==after
                (output/'collaborative-conversion-result.json').write_text(json.dumps({
                    'observations':observations,'before':before,'wrapped':wrapped,'after':after,
                    'canonical_database':database},ensure_ascii=False,indent=2),encoding='utf-8')
                assert not errors,errors
            finally:
                browser.close()
