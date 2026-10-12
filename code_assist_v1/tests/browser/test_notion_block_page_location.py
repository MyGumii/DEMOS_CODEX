"""A block converted in another page leaves a link and preserves both documents."""
import json
import os
from pathlib import Path

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page, _seed_blocks
from code_assist_v1.tests.browser.test_notion_ui1_golden_journey import _wait_for_notion_ready


def _body(block):
    return {key:block.get(key) for key in ('id','type','content','props','position')}


def _open_location(page, source_id):
    from playwright.sync_api import expect
    row = page.locator(f'#blocks .block-row[data-block-id="{source_id}"]')
    row.hover()
    row.locator('.block-handle').click()
    search = page.locator('#blockMenu .menu-search')
    search.fill('페이지 위치')
    command = page.locator('#blockMenu [data-page-location]')
    expect(command).to_be_visible()
    search.press('Enter')
    picker = page.locator('#blockPageLocationMenu')
    expect(picker).to_be_visible()
    expect(picker.get_by_role('combobox')).to_be_focused()
    return picker


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('theme,width', [('light', 1280), ('dark', 390)])
def test_block_page_location_preserves_source_destination_and_lost_reply(document_page, tmp_path, theme, width):
    from playwright.sync_api import expect
    page = document_page
    page.set_viewport_size({'width': width, 'height': 800})
    page.evaluate('theme => applyColorTheme(theme)', theme)
    origin = '/'.join(page.url.split('/', 3)[:3])
    destination = page.evaluate("""async () => api('/api/notion/pages', {method:'POST',
      body:{title:'위치 비교 대상', markdown:'대상의 기존 문장'}})""")
    destination_id = destination['page']['id']
    destination_before = destination['blocks']
    page.evaluate('async () => { await loadPages(); }')
    source = '페이지로 남아야 하는 전체 문장'
    _seed_blocks(page, [{'type': 'paragraph', 'content': source,
        'props': {'rich_text_html': '페이지로 <b>남아야</b> 하는 전체 문장'}},
        {'type': 'paragraph', 'content': '다음 문장은 그대로'}])
    source_page = page.evaluate('State.activePageId')
    source_url = f'{origin}/api/notion/pages/{source_page}'
    before = page.request.get(source_url).json()['blocks']
    source_id = before[0]['id']
    pages_before = page.request.get(origin+'/api/notion/pages').json()['items']
    output = Path(os.environ.get('NOTION_PAGE_LOCATION_ARTIFACT_DIR', str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)

    picker = _open_location(page, source_id)
    search = picker.get_by_role('combobox')
    search.fill('검색 결과 없음 928451')
    expect(picker.get_by_role('status')).to_contain_text('일치하는 페이지가 없습니다')
    search.press('Escape')
    expect(picker).to_be_hidden()
    expect(page.locator(f'#block-{source_id} .block-handle')).to_be_focused()
    assert page.request.get(source_url).json()['blocks'] == before
    assert page.request.get(origin+'/api/notion/pages').json()['items'] == pages_before

    picker = _open_location(page, source_id)
    search = picker.get_by_role('combobox')
    search.fill('위치 비교 대상')
    expect(picker.locator('[data-parent-id]:visible')).to_have_count(1)
    page.screenshot(path=str(output/f'page-location-picker-{theme}-{width}.png'))
    geometry = picker.bounding_box()
    assert geometry['x'] >= 0 and geometry['x']+geometry['width'] <= width+1

    sent = []
    lost = {'done': False}
    def lose_first_reply(route):
        body = route.request.post_data_json
        if route.request.method == 'POST' and body.get('title') == source:
            sent.append(body)
            if not lost['done']:
                lost['done'] = True
                route.fetch()
                route.abort()
                return
        route.continue_()
    page.route('**/api/notion/pages', lose_first_reply)
    search.press('Enter')
    expect(picker.get_by_role('alert')).to_be_visible(timeout=15000)
    assert page.request.get(source_url).json()['blocks'] == before
    expect(picker.locator(f'[data-parent-id="{destination_id}"]')).to_be_enabled()
    picker.locator(f'[data-parent-id="{destination_id}"]').click()
    expect(picker).to_be_hidden(timeout=20000)
    page.wait_for_function('() => !State.dirty && !State.saving')
    page.unroute('**/api/notion/pages', lose_first_reply)
    after = page.request.get(source_url).json()['blocks']
    assert after[0]['id'] == source_id and after[0]['type'] == 'link_to_page'
    child_id = after[0]['props']['page_id']
    child = page.request.get(f'{origin}/api/notion/pages/{child_id}').json()
    target = page.request.get(f'{origin}/api/notion/pages/{destination_id}').json()
    assert child['page']['title'] == source and child['page']['parent_id'] == destination_id
    assert not any(b.get('content', '').strip() for b in child['blocks'])
    assert _body(after[1]) == _body(before[1])
    assert [_body(b) for b in target['blocks'][:len(destination_before)]] == [_body(b) for b in destination_before]
    assert len([b for b in target['blocks'] if b['props'].get('page_id') == child_id]) == 1
    assert len(sent) == 2 and sent[0] == sent[1] and sent[0]['operation_id']
    assert len(page.request.get(origin+'/api/notion/pages').json()['items']) == len(pages_before)+1
    assert page.evaluate('State.activePageId') == source_page
    page.screenshot(path=str(output/f'page-location-result-{theme}-{width}.png'))
    page.reload()
    _wait_for_notion_ready(page)
    expect(page.locator('#blocks [data-linked-page-open]')).to_contain_text(source)
    assert page.request.get(source_url).json()['blocks'] == after
    (output/f'page-location-result-{theme}-{width}.json').write_text(
        json.dumps({'source': after, 'destination': target, 'child': child},
            ensure_ascii=False, indent=2), encoding='utf-8')


@pytest.mark.integration
@pytest.mark.regression
def test_block_page_location_root_and_same_page_through_conversion_flyout(document_page):
    from playwright.sync_api import expect
    page=document_page
    _seed_blocks(page,[{'type':'paragraph','content':'최상위로 전환하는 문장'},
        {'type':'paragraph','content':'현재 페이지 아래 문장'}])
    origin='/'.join(page.url.split('/',3)[:3])
    page_id=page.evaluate('State.activePageId')
    url=f'{origin}/api/notion/pages/{page_id}'
    before=page.request.get(url).json()['blocks']
    picker=_open_location(page,before[0]['id'])
    expect(picker.locator('[data-parent-id=""]')).to_be_visible()
    picker.get_by_role('combobox').press('Home')
    picker.get_by_role('combobox').press('Enter')
    expect(picker).to_be_hidden(timeout=15000)
    first=page.request.get(url).json()['blocks']
    root=page.request.get(f"{origin}/api/notion/pages/{first[0]['props']['page_id']}").json()
    assert root['page']['title']==before[0]['content'] and not root['page']['parent_id']
    assert first[0]['type']=='link_to_page' and _body(first[1])==_body(before[1])

    row=page.locator(f'#block-{before[1]["id"]}')
    row.hover()
    row.locator('.block-handle').click()
    page.locator('#blockMenu [data-submenu="convert"]').click()
    page.locator('#blockFlyout [data-page-location]').click()
    expect(picker).to_be_visible()
    picker.get_by_role('combobox').fill(page.evaluate('State.pageData.page.title'))
    picker.locator(f'[data-parent-id="{page_id}"]').click()
    expect(picker).to_be_hidden(timeout=15000)
    second=page.request.get(url).json()['blocks']
    local_child=page.request.get(f"{origin}/api/notion/pages/{second[1]['props']['page_id']}").json()
    assert local_child['page']['title']==before[1]['content'] and local_child['page']['parent_id']==page_id
    assert second[1]['type']=='child_page' and _body(second[0])==_body(first[0])


@pytest.mark.integration
@pytest.mark.regression
def test_block_page_location_rejects_stale_source_and_scope(document_page):
    from playwright.sync_api import expect
    page = document_page
    _seed_blocks(page, [{'type':'paragraph', 'content':'원문을 유지하는 문장'}])
    source_id = page.evaluate('State.pageData.blocks[0].id')
    origin = '/'.join(page.url.split('/', 3)[:3])
    source_page = page.evaluate('State.activePageId')
    url = f'{origin}/api/notion/pages/{source_page}'
    for kind in ('content', 'actor', 'workspace', 'readonly', 'composition'):
        before = page.request.get(url).json()['blocks']
        pages_before = page.request.get(origin+'/api/notion/pages').json()['items']
        picker = _open_location(page, source_id)
        picker.get_by_role('combobox').fill(page.evaluate('State.pageData.page.title'))
        own = picker.locator(f'[data-parent-id="{source_page}"]')
        expect(own).to_be_visible()
        page.evaluate("""kind => {
          if(kind==='content')State.pageData.blocks[0].content='다른 변경';
          if(kind==='actor')State.user.id='다른 사용자';
          if(kind==='workspace')State.navigation.workspaceId='다른 워크스페이스';
          if(kind==='readonly')State.pageData.page.can_edit=false;
          if(kind==='composition')EditorHost.beginComposition(State.pageData.blocks[0].id);
        }""", kind)
        own.click()
        assert page.request.get(url).json()['blocks'] == before
        assert page.request.get(origin+'/api/notion/pages').json()['items'] == pages_before
        page.reload()
        _wait_for_notion_ready(page)


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('theme,width', [('light',1280), ('dark',390)])
def test_block_page_location_tree_disclosure_search_and_cancel(document_page, tmp_path, theme, width):
    from playwright.sync_api import expect
    page=document_page
    page.set_viewport_size({'width':width,'height':800})
    page.evaluate('theme => applyColorTheme(theme)',theme)
    created=page.evaluate("""async () => {
      const parent=await api('/api/notion/pages',{method:'POST',body:{title:'위치 트리 부모'}});
      const child=await api('/api/notion/pages',{method:'POST',body:{title:'위치 트리 자식',parent_id:parent.page.id}});
      const grandchild=await api('/api/notion/pages',{method:'POST',body:{title:'검색으로 찾는 손자',parent_id:child.page.id}});
      for(let i=0;i<8;i++)await api('/api/notion/pages',{method:'POST',body:{title:'스크롤 확인 위치 '+i}});
      await loadPages(); saveRecentPageIds([]); return [parent.page,child.page,grandchild.page];
    }""")
    _seed_blocks(page,[{'type':'paragraph','content':'탐색 중에는 원문을 유지'}])
    source_id=page.evaluate('State.pageData.blocks[0].id')
    origin='/'.join(page.url.split('/',3)[:3])
    pages_before=page.request.get(origin+'/api/notion/pages').json()['items']
    body_before=page.request.get(origin+'/api/notion/pages/'+page.evaluate('State.activePageId')).json()['blocks']
    picker=_open_location(page,source_id)
    root=picker.locator('[data-location-branch="private"] [data-parent-id=""]')
    expect(root).to_be_visible()
    expect(picker.locator(f'[data-parent-id="{created[0]["id"]}"]')).to_have_count(0)
    search=picker.get_by_role('combobox')
    search.press('ArrowRight')
    expect(search).to_be_focused()
    assert picker.locator('[role="listbox"]').evaluate('el => el.scrollHeight>el.clientHeight')
    parent_row=picker.locator('[data-location-branch="private"]').filter(
        has=page.locator(f'[data-parent-id="{created[0]["id"]}"]'))
    expect(parent_row).to_be_visible()
    expect(picker.locator(f'[data-parent-id="{created[1]["id"]}"]')).to_have_count(0)
    parent_row.get_by_role('button',name='위치 트리 부모 펼치기',exact=True).click()
    child_row=picker.locator('[data-location-branch="private"]').filter(
        has=page.locator(f'[data-parent-id="{created[1]["id"]}"]'))
    expect(child_row).to_be_visible()
    child_row.get_by_role('button',name='위치 트리 자식 펼치기',exact=True).click()
    grandchild_row=picker.locator('[data-location-branch="private"]').filter(
        has=page.locator(f'[data-parent-id="{created[2]["id"]}"]'))
    expect(grandchild_row).to_be_visible()
    geometry=page.evaluate("""() => {
      const popup=document.querySelector('#blockPageLocationMenu');
      return {popup:popup.getBoundingClientRect().toJSON(),scrollHeight:document.documentElement.scrollHeight,
        rows:[...popup.querySelectorAll('[data-location-branch="private"]')].map(row=>({
          text:row.textContent,depth:row.style.getPropertyValue('--location-depth'),height:row.getBoundingClientRect().height})),
        horizontalOverflow:document.documentElement.scrollWidth>innerWidth};
    }""")
    assert not geometry['horizontalOverflow']
    assert geometry['popup']['x']>=0 and geometry['popup']['right']<=width
    output=Path(os.environ.get('NOTION_PAGE_LOCATION_ARTIFACT_DIR',str(tmp_path)))
    output.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(output/f'page-location-tree-{theme}-{width}.png'))
    (output/f'page-location-tree-{theme}-{width}.json').write_text(
        json.dumps(geometry,ensure_ascii=False,indent=2),encoding='utf-8')
    parent_row.get_by_role('button',name='위치 트리 부모 접기',exact=True).click()
    expect(picker.locator(f'[data-parent-id="{created[2]["id"]}"]')).to_have_count(0)
    search.fill('검색으로 찾는 손자')
    expect(picker.locator(f'[data-parent-id="{created[2]["id"]}"]')).to_have_count(1)
    expect(picker.locator(f'[data-parent-id="{created[2]["id"]}"]')).to_be_visible()
    search.fill('')
    expect(picker.locator(f'[data-parent-id="{created[2]["id"]}"]')).to_have_count(0)
    search.press('Home')
    search.press('ArrowLeft')
    expect(picker.locator(f'[data-parent-id="{created[0]["id"]}"]')).to_have_count(0)
    search.press('Escape')
    expect(picker).to_be_hidden()
    expect(page.locator(f'#block-{source_id} .block-handle')).to_be_focused()
    assert page.request.get(origin+'/api/notion/pages').json()['items']==pages_before
    assert page.request.get(origin+'/api/notion/pages/'+page.evaluate('State.activePageId')).json()['blocks']==body_before


@pytest.mark.integration
@pytest.mark.regression
def test_block_page_location_team_root_uses_existing_native_creation(tmp_path):
    from playwright.sync_api import expect, sync_playwright
    from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
    from code_assist_v1.tests.browser.test_notion_synced_source_menu import _current_collaborative_body
    from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
    from code_assist_v1.notion_collaboration.page_creation import create_page, native_page_items
    with _isolated_collaborative_app(tmp_path,locked=False,navigation_database_favorites=True) as (
        origin,password,authority,workspace_id,source_page,
    ):
        store=CollaborativeWorkspaceStore(authority.catalog_path,user_data_root=authority.user_data_root,
            identity_directory=authority.identity_directory)
        version=lambda:store.get_workspace('owner',workspace_id)['version']
        team=store.create_teamspace('owner',workspace_id,name='위치 선택 팀',visibility='closed',
            base_version=version(),operation_id='location-team-fixture')['teamspace']['id']
        parent=create_page(authority,'owner',workspace_id,title='팀 안의 기존 페이지',teamspace_id=team,
            base_version=version(),operation_id='location-team-page-fixture')['page']['id']
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='chrome',headless=True)
            try:
                page=browser.new_page(locale='ko-KR',viewport={'width':1280,'height':800})
                page.goto(origin+'/page/'+source_page)
                page.locator('#userId').fill('owner')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                _wait_for_notion_ready(page)
                page.wait_for_function('team => State.liveCollaboration.crdtReady && teamspaceCanCreatePage(team)',arg=team)
                _seed_blocks(page,[{'type':'paragraph','content':'팀 최상위에 생성하는 전체 문장'},
                    {'type':'paragraph','content':'남아 있는 다음 문장'}])
                page.wait_for_function('() => !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive')
                before=_current_collaborative_body(authority,'owner',source_page,workspace_id)
                picker=_open_location(page,before[0]['id'])
                expect(picker.get_by_role('group',name='팀스페이스',exact=True)).to_be_visible()
                team_root=picker.locator(f'[data-teamspace-id="{team}"]')
                expect(team_root).to_be_visible()
                picker.get_by_role('button',name='위치 선택 팀 펼치기',exact=True).click()
                expect(picker.locator(f'[data-location-branch="teams"] [data-parent-id="{parent}"]')).to_be_visible()
                assert _current_collaborative_body(authority,'owner',source_page,workspace_id)==before
                output=Path(os.environ.get('NOTION_PAGE_LOCATION_ARTIFACT_DIR',str(tmp_path)))
                output.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(output/'page-location-team-tree.png'))
                team_root.click()
                expect(picker).to_be_hidden(timeout=30000)
                page.wait_for_function('() => !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive')
                after=_current_collaborative_body(authority,'owner',source_page,workspace_id)
                assert after[0]['type']=='link_to_page' and after[0]['id']==before[0]['id']
                assert after[1]==before[1]
                child_id=after[0]['props']['page_id']
                child=authority.read_document('owner',child_id)
                placement=next(item['placement'] for item in native_page_items(authority,'owner',workspace_id) if item['id']==child_id)
                assert not child['parent_document_id'] and child['title']==before[0]['content']
                assert placement['scope']=='teamspace' and placement['teamspace_id']==team
                assert all(not block.get('content') for block in _current_collaborative_body(authority,'owner',child_id,workspace_id))
                page.reload()
                _wait_for_notion_ready(page)
                expect(page.locator('#blocks [data-linked-page-open]')).to_contain_text(before[0]['content'])
                (output/'page-location-team-result.json').write_text(
                    json.dumps({'source':after,'child':child,'placement':placement},ensure_ascii=False,indent=2),encoding='utf-8')
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_block_page_location_collaborative_parent_link_and_view_role(tmp_path):
    from playwright.sync_api import expect, sync_playwright
    from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
    from code_assist_v1.tests.browser.test_notion_synced_source_menu import _current_collaborative_body

    with _isolated_collaborative_app(tmp_path, locked=False, navigation_database_favorites=True) as (
        origin, password, authority, workspace_id, source_page,
    ):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width':1280, 'height':800})
                page.goto(origin+'/page/'+source_page)
                page.locator('#userId').fill('owner')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                _wait_for_notion_ready(page)
                page.wait_for_function('() => State.liveCollaboration.crdtReady && State.navigation.collaborativePageCreation', timeout=30000)
                destination = page.evaluate("""async () => {
                  const result=await api(`/api/notion/workspaces/${State.navigation.workspaceId}/pages`,
                    {method:'POST',body:{title:'협업 위치 대상',parent_id:'',
                      base_version:selectedNavigationWorkspace().version,operation_id:navigationOperationId('location-fixture')}});
                  applyNavigationWorkspaceResult(result.workspace); await loadPages(); return result;
                }""")
                destination_id = destination['page']['id']
                target_before = _current_collaborative_body(authority, 'owner', destination_id, workspace_id)
                source = '협업에서 전환하는 전체 문장'
                _seed_blocks(page, [{'type':'paragraph','content':source},
                    {'type':'paragraph','content':'협업 다음 문장'}])
                page.wait_for_function('() => !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive')
                before = _current_collaborative_body(authority, 'owner', source_page, workspace_id)
                picker = _open_location(page, before[0]['id'])
                picker.get_by_role('combobox').fill('협업 위치 대상')
                try:
                    expect(picker.locator(f'[data-parent-id="{destination_id}"]')).to_be_visible(timeout=15000)
                except Exception as exc:
                    diagnostic = page.evaluate("""async () => ({menu:document.querySelector('#blockPageLocationMenu').textContent,
                      pages:await api(`/api/notion/workspaces/${State.navigation.workspaceId}/pages`)})""")
                    raise AssertionError(f'location fixture target unavailable: {diagnostic}') from exc
                picker.get_by_role('combobox').press('Enter')
                expect(picker).to_be_hidden(timeout=30000)
                page.wait_for_function('() => !State.dirty && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive')
                after = _current_collaborative_body(authority, 'owner', source_page, workspace_id)
                assert after[0]['type'] == 'link_to_page' and after[0]['id'] == before[0]['id']
                child_id = after[0]['props']['page_id']
                child = authority.read_document('owner', child_id)
                target = _current_collaborative_body(authority, 'owner', destination_id, workspace_id)
                assert child['title'] == source and child['parent_document_id'] == destination_id
                assert after[1] == before[1]
                assert [{key:b.get(key) for key in ('id','type','content','props')} for b in target[:len(target_before)]] == [
                    {key:b.get(key) for key in ('id','type','content','props')} for b in target_before]
                assert len([b for b in target if b['type']=='child_page' and b['props']['page_id']==child_id]) == 1
                assert page.evaluate('State.activePageId') == source_page
                page.reload()
                _wait_for_notion_ready(page)
                expect(page.locator('#blocks [data-linked-page-open]')).to_contain_text(source)
                witness = authority.read_document('owner', source_page)
                authority.set_page_grant('owner', source_page, 'subject', 'content-editor', role='view',
                    inherit_to_descendants=False, expected_workspace_epoch=witness['workspace_epoch'],
                    expected_document_epoch=witness['document_epoch'], operation_id='location-view-grant')
                viewer = browser.new_page(locale='ko-KR', viewport={'width':390,'height':800})
                viewer.goto(origin+'/page/'+source_page)
                viewer.locator('#userId').fill('content-editor')
                viewer.locator('#password').fill(password)
                viewer.locator('#submitBtn').click()
                _wait_for_notion_ready(viewer)
                expect(viewer.locator('#pageTitle')).to_be_disabled(timeout=30000)
                for handle in viewer.locator('#blocks .block-handle').all():
                    expect(handle).to_be_hidden()
                    expect(handle).to_be_disabled()
                expect(viewer.locator('#blockPageLocationMenu')).to_be_hidden()
                assert not viewer.evaluate('blockPageLocationAvailable()')
                output=Path(os.environ.get('NOTION_PAGE_LOCATION_ARTIFACT_DIR',str(tmp_path)))
                output.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(output/'page-location-collaborative-owner.png'))
                viewer.screenshot(path=str(output/'page-location-collaborative-viewer.png'))
                (output/'page-location-collaborative-result.json').write_text(
                    json.dumps({'source':after,'destination':target,'child':child},ensure_ascii=False,indent=2),encoding='utf-8')
                assert _current_collaborative_body(authority,'owner',source_page,workspace_id) == after
            finally:
                browser.close()
