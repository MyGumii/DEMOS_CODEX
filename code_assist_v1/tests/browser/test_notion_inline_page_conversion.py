"""Inline conversion must retain the text that the user is converting."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import (
    document_page, _seed_blocks, _select_first_quote,
)


@pytest.mark.integration
@pytest.mark.regression
def test_inline_page_conversion_preserves_source_and_following_block(document_page,tmp_path):
    from playwright.sync_api import expect

    page=document_page
    source="전환해도 남아야 하는 문구"
    _seed_blocks(page,[{"type":"paragraph","content":source},
        {"type":"paragraph","content":"이어지는 문장은 그대로"}])
    parent_id=page.evaluate("State.activePageId")
    origin="/".join(page.url.split("/",3)[:3])
    parent_url=f"{origin}/api/notion/pages/{parent_id}"
    original=page.request.get(parent_url).json()
    _select_first_quote(page,"전환")
    page.locator('#inlineToolbar [data-inline-format="type"]').click()
    output=Path(os.environ.get("NOTION_INLINE_PAGE_ARTIFACT_DIR",str(tmp_path)))
    output.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(output/"inline-page-conversion-menu.png"))
    page.locator('#inlineFormatPopover').get_by_role('menuitem',name='페이지',exact=True).click()
    page.wait_for_function("""() => State.pageData.blocks[0].type==='child_page'
      && State.pageData.blocks[0].props.page_id && !State.dirty && !State.saving""",timeout=15000)
    parent=page.request.get(parent_url).json()
    child_id=parent["blocks"][0]["props"]["page_id"]
    child=page.request.get(f"{origin}/api/notion/pages/{child_id}").json()
    (output/"inline-page-conversion-result.json").write_text(
        json.dumps({"parent":parent,"child":child},ensure_ascii=False,indent=2),encoding="utf-8")
    page.screenshot(path=str(output/"inline-page-conversion-parent.png"))
    expect(page.locator('#blocks [data-child-page]')).to_be_visible()
    assert page.evaluate("State.activePageId")==parent_id
    assert {key:parent['blocks'][1].get(key) for key in ('id','type','content','props','position')}=={
        key:original['blocks'][1].get(key) for key in ('id','type','content','props','position')}
    assert child["page"]["title"]==source
    assert child["page"]["parent_id"]==parent_id
    assert parent["blocks"][0]["content"]==source
    assert not any(block.get("content","").strip() for block in child["blocks"])
    page.reload()
    expect(page.locator('#blocks [data-child-page]')).to_contain_text(source)
    assert page.request.get(parent_url).json()["blocks"]==parent["blocks"]


@pytest.mark.integration
@pytest.mark.regression
def test_inline_page_conversion_keeps_unrepresentable_titles_without_creating_pages(document_page):
    from playwright.sync_api import expect

    page=document_page
    origin="/".join(page.url.split("/",3)[:3])
    for source in ["전환 "+"가"*160,"전환  공백을 유지","전환\n줄바꿈을 유지"]:
        _seed_blocks(page,[{"type":"paragraph","content":source}])
        parent_id=page.evaluate('State.activePageId')
        parent_url=f"{origin}/api/notion/pages/{parent_id}"
        original=page.request.get(parent_url).json()
        pages_before=page.request.get(f"{origin}/api/notion/pages").json()
        _select_first_quote(page,'전환')
        page.locator('#inlineToolbar [data-inline-format="type"]').click()
        page.locator('#inlineFormatPopover').get_by_role('menuitem',name='페이지',exact=True).click()
        expect(page.locator('#status')).to_contain_text('원문은 유지됩니다')
        assert page.request.get(parent_url).json()['blocks']==original['blocks']
        assert page.request.get(f"{origin}/api/notion/pages").json()==pages_before
        assert page.evaluate('State.pageData.blocks[0].content')==source


@pytest.mark.integration
@pytest.mark.regression
def test_inline_page_conversion_collaborative_title_and_single_parent_link(tmp_path):
    from playwright.sync_api import expect, sync_playwright
    from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app

    with _isolated_collaborative_app(tmp_path,locked=False,navigation_database_favorites=True) as (
        origin,password,authority,workspace_id,parent_id,
    ):
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='chrome',headless=True)
            try:
                page=browser.new_page(locale='ko-KR',viewport={'width':1280,'height':800})
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(origin+'/page/'+parent_id,wait_until='domcontentloaded')
                page.locator('#userId').fill('owner')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                expect(page.locator('#pageTitle')).to_be_enabled(timeout=30000)
                try:
                    page.wait_for_function("() => typeof State!=='undefined' && State.navigation.collaborativePageCreation && State.liveCollaboration.crdtReady",timeout=30000)
                except Exception as exc:
                    state=page.evaluate("""() => ({url:location.href,state:typeof State,
                      create:typeof State==='undefined'?null:State.navigation?.collaborativePageCreation,
                      live:typeof State==='undefined'?null:{ready:State.liveCollaboration?.crdtReady,status:State.liveCollaboration?.status},
                      status:document.querySelector('#status')?.textContent,
                      login:document.querySelector('#loginError')?.textContent})""")
                    raise AssertionError(f'collaborative setup unavailable: {state}; errors={errors}') from exc
                source='전환해도 남아야 하는 협업 문구'
                _seed_blocks(page,[{'type':'paragraph','content':source},
                    {'type':'paragraph','content':'협업의 다음 문장'}])
                page.wait_for_function('() => !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive',timeout=30000)
                original=authority.read_document('owner',parent_id)
                _select_first_quote(page,'전환')
                page.locator('#inlineToolbar [data-inline-format="type"]').click()
                page.locator('#inlineFormatPopover').get_by_role('menuitem',name='페이지',exact=True).click()
                page.wait_for_function("""() => State.pageData.blocks[0].type==='child_page'
                  && !State.dirty && !State.saving && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""",timeout=30000)
                parent=authority.read_document('owner',parent_id)
                links=[b for b in parent['blocks'] if b['type']=='child_page']
                assert len(links)==1
                child_id=links[0]['props']['page_id']
                child=authority.read_document('owner',child_id)
                assert child['title']==source
                assert child['parent_document_id']==parent_id
                assert links[0]['content']==source
                assert parent['blocks'][1]==original['blocks'][1]
                assert not any(b.get('content','').strip() for b in child['blocks'])
                assert page.evaluate('State.activePageId')==parent_id
                output=Path(os.environ.get('NOTION_INLINE_PAGE_ARTIFACT_DIR',str(tmp_path)))
                output.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(output/'inline-page-conversion-collaborative.png'))
                (output/'inline-page-conversion-collaborative-result.json').write_text(
                    json.dumps({'parent':parent,'child':child},ensure_ascii=False,indent=2),encoding='utf-8')
                page.reload(wait_until='domcontentloaded')
                expect(page.locator('#blocks [data-child-page]')).to_contain_text(source,timeout=30000)
                assert len([b for b in authority.read_document('owner',parent_id)['blocks'] if b['type']=='child_page'])==1
                assert not errors,errors
            finally:
                browser.close()
