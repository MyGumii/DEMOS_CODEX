"""Selected-row icon picker commits one canonical batch through the browser."""

import json
from urllib.parse import urlsplit

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page
from code_assist_v1.tests.notion_collaboration.test_database_row_icon_batch import (
    icon_batch_environment, activity_environment, environment,
    appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment,
    archive_environment, guest_environment, image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_trash_http import _client
from code_assist_v1.tests.notion_collaboration.test_database_features import _apply
from code_assist_v1 import notion_workspace_database_row_page_placement_migration as placement_migration
from code_assist_v1 import notion_workspace_custom_emoji_migration as custom_emoji_migration
from code_assist_v1 import notion_workspace_custom_emoji
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_workspace_images import png


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('lose_response', [False, True])
def test_selected_row_icon_picker_escape_and_atomic_http_save(
        document_page, icon_batch_environment, monkeypatch, lose_response):
    from playwright.sync_api import expect

    store, workspace_id = icon_batch_environment
    client, _actor = _client(store, monkeypatch, enabled=False)
    capabilities = store.capabilities
    monkeypatch.setattr(store, 'capabilities', lambda: {
        **capabilities(), 'collaborative_database_row_icon_batch': True})
    base = f'/api/notion/workspaces/{workspace_id}'
    database = store.iam.read_database('owner', 'database-e1')
    page_data = store.iam.read_document('owner', 'page-e1')
    requests = []
    fault = {'sent': False}

    def route_to_store(route):
        request = route.request
        address = urlsplit(request.url)
        if not address.path.startswith(base + '/'):
            route.continue_()
            return
        target = address.path + ('?' + address.query if address.query else '')
        response = client.open(target, method=request.method,
            data=request.post_data, content_type='application/json')
        requests.append((request.method, address.path, response.status_code,
            request.post_data))
        if lose_response and not fault['sent'] and request.method == 'POST' \
                and address.path.endswith('/databases/commands'):
            fault['sent'] = True
            route.fulfill(status=503, content_type='application/json',
                body='{"error":"synthetic response loss"}')
            return
        route.fulfill(status=response.status_code, content_type='application/json',
            body=response.get_data())

    page = document_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('**/api/notion/workspaces/**', route_to_store)
    page.evaluate("""({workspaceId,database,pageData}) => {
      State.activePageId='page-e1'; State.pageData.page=pageData;
      State.navigation.workspaceId=workspaceId;
      State.navigation.collaborativeSharing=true;
      State.navigation.collaborativeDatabaseRowIconBatch=true;
      collaborativeDatabaseActive=()=>true; activePageCanEdit=()=>true;
      State.pageData.databases=[database];
      const block=EditorCore.createBlock('table',{content:'아이콘 일괄 검증',
        props:{database_id:'database-e1',properties:database.properties,
          rows:database.rows.map(item=>({page_id:item.id,_canonical_row_id:item.id,
            'property-e1':item.cells['property-e1']}))}});
      State.pageData.blocks=[block]; renderBlocks=()=>{};
      const root=document.createElement('div');root.id='selectedRowIconProbe';
      root.style.cssText='position:relative;margin:60px 40px;';
      root.innerHTML='<div class="dynamic-table-grid"><div class="table-row table-header">'
        +'<span>이름</span></div>'
        +database.rows.map((item,index)=>'<div class="table-row"><span>'
          +'<input data-db-row="'+index+'" data-db-field="property-e1" value="행">'
          +'</span></div>').join('')+'</div>';
      document.body.append(root);bindBlockGrid(root,block);
      NotionGridSelection.syncGutterRows([{grid:root.querySelector('.dynamic-table-grid'),
        rows:database.rows.map(item=>item.id)}]);
    }""", {'workspaceId': workspace_id, 'database': database,
            'pageData': page_data})
    toolbar = page.locator('#selectedRowIconProbe .database-row-selection-toolbar')
    expect(toolbar).to_contain_text('2개 선택됨')
    toolbar.locator('[data-selected-row-more]').click()
    toolbar.locator('[data-edit-selected-icons]').click()
    picker = page.locator('.database-row-icon-picker')
    expect(picker).to_be_visible()
    toolbar_box, picker_box = toolbar.bounding_box(), picker.bounding_box()
    assert toolbar_box and picker_box
    assert 0 <= picker_box['x'] and picker_box['x'] + picker_box['width'] <= page.viewport_size['width']
    assert (abs(picker_box['y'] - (toolbar_box['y'] + toolbar_box['height'])) < 16
        or abs(picker_box['y'] + picker_box['height'] - toolbar_box['y']) < 16)
    picker.locator('input[aria-label="아이콘 검색"]').press('Escape')
    expect(picker).to_have_count(0)
    expect(toolbar).to_contain_text('2개 선택됨')
    toolbar.locator('[data-selected-row-more]').click()
    toolbar.locator('[data-edit-selected-icons]').click()
    expect(picker).to_be_visible()
    picker.locator('[data-icon-value="😀"]').click()
    if lose_response:
        expect(picker.locator('.icon-picker-status.error')).to_be_visible()
        assert '😀' not in page.evaluate("() => NotionIcons.readRecent(State.user?.id)")
        picker.locator('[data-icon-value="😀"]').click()
    page.wait_for_function("""() => document.querySelector('#status')?.textContent
      ?.includes('2개 항목 · 아이콘 변경됨')""", timeout=10_000)
    assert [row['appearance']['icon'] for row in
        store.iam.read_database('owner', 'database-e1')['rows']] == ['😀', '😀']
    sent = [json.loads(body) for method, path, status, body in requests
        if method == 'POST' and path.endswith('/databases/commands') and status == 201]
    assert len(sent) == (2 if lose_response else 1)
    assert len({item['operation_id'] for item in sent}) == 1
    assert page.evaluate("() => NotionIcons.readRecent(State.user?.id)[0]") == '😀'
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
def test_selected_row_icon_picker_reuses_workspace_custom_emoji(
        document_page, icon_batch_environment, monkeypatch):
    from playwright.sync_api import expect

    source, workspace_id = icon_batch_environment
    _apply(placement_migration, source.db_path, source.user_data_root,
           'before-browser-row-emoji-placement.db')
    _apply(custom_emoji_migration, source.db_path, source.user_data_root,
           'before-browser-row-emoji.db')
    store = CollaborativeWorkspaceStore(source.db_path,
        user_data_root=source.user_data_root,
        identity_directory=source.identity_directory)
    created = notion_workspace_custom_emoji.create_custom_emoji(
        store, 'owner', workspace_id, name='Team', raw=png(),
        mime_type='image/png',
        base_version=store.get_workspace('owner', workspace_id)['version'],
        operation_id='browser-row-custom-emoji-create',
    )['emoji']
    client, _actor = _client(store, monkeypatch, enabled=False)
    capabilities = store.capabilities
    monkeypatch.setattr(store, 'capabilities', lambda: {
        **capabilities(), 'collaborative_database_row_icon_batch': True})
    base = f'/api/notion/workspaces/{workspace_id}'

    def route_to_store(route):
        request = route.request
        path = urlsplit(request.url).path
        if path != base and not path.startswith(base + '/') \
                and not path.startswith('/api/notion/custom-emoji/'):
            route.continue_()
            return
        response = client.open(path, method=request.method,
            data=request.post_data, content_type='application/json')
        route.fulfill(status=response.status_code,
            content_type=response.mimetype, body=response.get_data())

    page = document_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('**/api/notion/workspaces/**', route_to_store)
    page.route('**/api/notion/custom-emoji/**', route_to_store)
    database = store.iam.read_database('owner', 'database-e1')
    page_data = store.iam.read_document('owner', 'page-e1')
    page.evaluate("""({workspaceId,database,pageData}) => {
      State.activePageId='page-e1'; State.pageData.page=pageData;
      State.navigation.workspaceId=workspaceId;
      State.navigation.collaborativeSharing=true;
      State.navigation.collaborativeDatabaseRowIconBatch=true;
      collaborativeDatabaseActive=()=>true; activePageCanEdit=()=>true;
      State.pageData.databases=[database];
      const block=EditorCore.createBlock('table',{content:'공용 이모지 행 검증',
        props:{database_id:'database-e1',properties:database.properties,
          rows:database.rows.map(item=>({page_id:item.id,_canonical_row_id:item.id,
            'property-e1':item.cells['property-e1']}))}});
      State.pageData.blocks=[block]; renderBlocks=()=>{};
      const root=document.createElement('div');root.id='selectedRowEmojiProbe';
      root.style.cssText='position:relative;margin:60px 40px;';
      root.innerHTML='<div class="dynamic-table-grid"><div class="table-row table-header">'
        +'<span>이름</span></div>'
        +database.rows.map((item,index)=>'<div class="table-row"><span>'
          +'<input data-db-row="'+index+'" data-db-field="property-e1" value="행">'
          +'</span></div>').join('')+'</div>';
      document.body.append(root);bindBlockGrid(root,block);
      NotionGridSelection.syncGutterRows([{grid:root.querySelector('.dynamic-table-grid'),
        rows:database.rows.map(item=>item.id)}]);
    }""", {'workspaceId': workspace_id, 'database': database,
            'pageData': page_data})
    toolbar = page.locator('#selectedRowEmojiProbe .database-row-selection-toolbar')
    expect(toolbar).to_contain_text('2개 선택됨')
    toolbar.locator('[data-selected-row-more]').click()
    toolbar.locator('[data-edit-selected-icons]').click()
    picker = page.locator('.database-row-icon-picker')
    expect(picker).to_be_visible()
    picker.locator('[data-icon-tab="emoji"]').click()
    picker.locator('input[type="search"]').fill('Team')
    option = picker.locator(f'[data-icon-value="{created["icon"]}"]')
    expect(option).to_be_visible(timeout=10_000)
    option.click()
    page.wait_for_function("() => document.querySelector('#status')?.textContent?.includes('2개 항목 · 아이콘 변경됨')",
                           timeout=10_000)
    assert [row['appearance']['icon'] for row in
        store.iam.read_database('owner', 'database-e1')['rows']] == [created['icon']] * 2
    assert not errors, errors
