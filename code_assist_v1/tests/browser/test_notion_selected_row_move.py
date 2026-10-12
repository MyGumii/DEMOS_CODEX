"""Selected-row move picker commits one canonical batch in the browser."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page
from code_assist_v1.tests.notion_collaboration.test_database_row_move_batch import (
    _destination, _cross_destination, icon_batch_environment, appearance_environment,
    activity_environment, environment, body_environment, team_environment,
    file_environment, member_environment, native_environment,
    archive_environment, guest_environment, image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_trash_http import _client
from code_assist_v1.notion_collaboration.iam_runtime import collaborative_page_payload


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('lose_response,cross_document,missing_property', [
    (False, False, False), (True, False, False),
    (False, True, False), (True, True, False),
    (False, False, True), (True, False, True),
])
def test_selected_row_move_picker_preserves_ids_and_replays(
        document_page, icon_batch_environment, monkeypatch, lose_response,
        cross_document, missing_property):
    from playwright.sync_api import expect

    store, workspace_id = icon_batch_environment
    if cross_document:
        _cross_destination(store, workspace_id)
    else:
        _destination(store, matching=not missing_property)
    if cross_document and not lose_response:
        with store.iam._transaction() as con:
            con.execute(
                "INSERT INTO workspace_collaboration_database_properties("
                "id,database_id,name,property_type,position,config_json,"
                "row_version,created_at,updated_at) VALUES("
                "'property-date','database-e1','Date','date',1,'{}',1,1,1)")
        page = store.iam.read_document('owner', 'page-e1')
        store.iam.execute_cell_batch(
            'owner', 'page-e1', database_id='database-e1',
            target_id='database-e1',
            payload={'cells': [{'row_id': 'row-e1', 'property_id': 'property-date',
                                'value': {'start': '2027-01-15', 'reminder': {
                                    'at': '2027-01-14T00:00:00.000Z',
                                    'lead': 0, 'user_id': 'owner',
                                    'preset': 'day_1', 'zone': 'Asia/Seoul'}},
                                'expected_target_version': 0}]},
            expected_document_sequence=page['document_sequence'],
            expected_workspace_epoch=page['workspace_epoch'],
            expected_document_epoch=page['document_epoch'],
            operation_id='browser-row-move-reminder-seed')
    client, _ = _client(store, monkeypatch, enabled=False)
    capabilities = store.capabilities
    monkeypatch.setattr(store, 'capabilities', lambda: {
        **capabilities(), 'collaborative_database_row_move_batch': True})
    source = store.iam.read_database('owner', 'database-e1')
    destination = store.iam.read_database('owner', 'database-e2')
    page_data = store.iam.read_document('owner', 'page-e1')
    pages = [collaborative_page_payload(store.iam, 'owner', 'page-e1')['page']]
    if cross_document:
        pages.append(collaborative_page_payload(store.iam, 'owner', 'page-e2')['page'])
    base = f'/api/notion/workspaces/{workspace_id}'
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
    page.evaluate("""({workspaceId,source,destination,pageData,pages,crossDocument}) => {
      State.activePageId='page-e1';State.pageData.page=pageData;
      State.navigation.workspaceId=workspaceId;
      State.navigation.collaborativeSharing=true;
      State.navigation.collaborativeDatabaseRowMoveBatch=true;
      collaborativeDatabaseActive=()=>true;activePageCanEdit=()=>true;
      State.pages=pages;
      State.pageData.databases=crossDocument?[source]:[source,destination];
      const block=EditorCore.createBlock('table',{content:'원본 DB',
        props:{database_id:source.id,properties:source.properties,
          rows:source.rows.map(item=>({page_id:item.id,_canonical_row_id:item.id,
            'property-e1':item.cells['property-e1']}))}});
      const target=EditorCore.createBlock('table',{content:'대상 DB',
        props:{database_id:destination.id,properties:destination.properties,rows:[]}});
      State.pageData.blocks=crossDocument?[block]:[block,target];renderBlocks=()=>{};
      const root=document.createElement('div');root.id='selectedRowMoveProbe';
      root.style.cssText='position:relative;margin:60px 40px;';
      root.innerHTML='<div class="dynamic-table-grid"><div class="table-row table-header">'
        +'<span>이름</span></div>'
        +source.rows.map((item,index)=>'<div class="table-row"><span>'
          +'<input data-db-row="'+index+'" data-db-field="property-e1" value="행">'
          +'</span></div>').join('')+'</div>';
      document.body.append(root);bindBlockGrid(root,block);
      NotionGridSelection.syncGutterRows([{grid:root.querySelector('.dynamic-table-grid'),
        rows:source.rows.map(item=>item.id)}]);
    }""", {'workspaceId': workspace_id, 'source': source,
            'destination': destination, 'pageData': page_data,
            'pages': pages, 'crossDocument': cross_document})
    toolbar = page.locator('#selectedRowMoveProbe .database-row-selection-toolbar')
    expect(toolbar).to_contain_text('2개 선택됨')

    def choose_destination():
        toolbar.locator('[data-selected-row-more]').click()
        move = toolbar.locator('[data-move-selected-rows]')
        expect(move).to_be_visible()
        move.click()
        picker = page.locator('.database-row-move-picker')
        expect(picker).to_be_visible()
        if cross_document:
            picker.locator('input').fill('Another page')
            picker.locator('.database-row-move-page[data-destination-page="page-e2"]').click()
        else:
            picker.locator('input').fill('Dest')
        picker.locator('[data-destination-database="database-e2"]').click()

    if not lose_response:
        toolbar.locator('[data-selected-row-more]').click()
        toolbar.locator('[data-move-selected-rows]').click()
        picker = page.locator('.database-row-move-picker')
        expect(picker).to_be_visible()
        picker.locator('input').press('Escape')
        expect(picker).to_have_count(0)
        expect(toolbar).to_contain_text('2개 선택됨')
    choose_destination()
    if lose_response:
        expect(page.locator('#status')).to_contain_text('synthetic response loss')
        recovery = page.locator('#databaseRowMoveStatus')
        expect(recovery).to_be_visible()
        expect(recovery).to_contain_text('2개 항목의 이동 결과를 확인해야 합니다.')
        page.reload()
        page.wait_for_function('typeof State !== "undefined" && State.user && State.pageData')
        page.evaluate("""({workspaceId,source,destination,pageData,pages,crossDocument}) => {
          State.activePageId='page-e1';State.pageData.page=pageData;
          State.pageData.databases=crossDocument?[source]:[source,destination];State.pageData.blocks=[];
          State.pages=pages;
          State.navigation.workspaceId=workspaceId;
          State.navigation.collaborativeSharing=true;
          State.navigation.collaborativeDatabaseRowMoveBatch=true;
          collaborativeDatabaseActive=()=>true;activePageCanEdit=()=>true;
          renderBlocks=()=>{};renderPageLists();
        }""", {'workspaceId': workspace_id, 'source': source,
                'destination': destination, 'pageData': page_data,
                'pages': pages, 'crossDocument': cross_document})
        recovery = page.locator('#databaseRowMoveStatus')
        expect(recovery).to_be_visible()
        expect(recovery).to_contain_text('2개 항목의 이동 결과를 확인해야 합니다.')
        recovery.get_by_role('button', name='결과 확인').click()
        expect(page.locator('#status')).to_contain_text('1건의 이동 결과를 확인했습니다.')
        expect(recovery).to_be_hidden()
    else:
        expect(page.locator('#status')).to_contain_text('2개 항목을 옮겼습니다.')
    expect(page.locator('.database-row-move-picker')).to_have_count(0)
    assert store.iam.read_database('owner', 'database-e1')['rows'] == []
    moved = store.iam.read_database('owner', 'database-e2')['rows']
    assert [row['id'] for row in moved] == ['row-e1', 'row-e2']
    assert any(row['cells'].get('property-e1') is not None for row in source['rows'])
    if missing_property:
        new_property = next(item for item in store.iam.read_database(
            'owner', 'database-e2')['properties'] if item['name'] == 'Status')
        assert [row['cells'].get(new_property['id']) for row in moved] == [
            row['cells'].get('property-e1') for row in source['rows']]
        assert all('property-e2' not in row['cells'] for row in moved)
    else:
        assert [row['cells'].get('property-e2') for row in moved] == [
            row['cells'].get('property-e1') for row in source['rows']]
    if cross_document and not lose_response:
        date_property = next(item for item in store.iam.read_database(
            'owner', 'database-e2')['properties'] if item['name'] == 'Date')
        assert moved[0]['cells'][date_property['id']] == (
            source['rows'][0]['cells']['property-date'])
        inbox = store.iam.list_reminders('owner', workspace_id)['items']
        assert len(inbox) == 1
        assert inbox[0]['page_id'] == 'page-e2'
        assert inbox[0]['block_id'] == 'database-e2'
    sent = [json.loads(body) for method, path, status, body in requests
        if method == 'POST' and path.endswith('/databases/commands')
            and status == 201]
    assert len(sent) == (2 if lose_response else 1)
    assert len({item['operation_id'] for item in sent}) == 1
    assert all(item['payload']['destination_document_id'] ==
        ('page-e2' if cross_document else 'page-e1') for item in sent)
    if cross_document and not lose_response:
        page.evaluate("""async () => {
          history.replaceState({pageId:'page-e1'},'',
            '/page/page-e1?item_db=database-e1&item_row=row-e1&item_view=full');
          await openPage('page-e1',false);
        }""")
        page.wait_for_function("State.activePageId==='page-e2' && "
            "LinkedDatabaseItems?.snapshot()?.rowId==='row-e1' && "
            "LinkedDatabaseItems?.snapshot()?.mode==='full'")
        url = urlsplit(page.url)
        assert url.path == '/page/page-e2'
        assert parse_qs(url.query)['item_db'] == ['database-e2']
        assert parse_qs(url.query)['item_row'] == ['row-e1']
        assert any(path.endswith('/database-rows/row-e1/location') and status == 200
            for method, path, status, _ in requests if method == 'GET')
    assert not errors, errors
