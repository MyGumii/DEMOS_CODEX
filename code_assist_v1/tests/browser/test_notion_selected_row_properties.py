"""Selected owned-row property edits reach one canonical cell batch."""

from urllib.parse import urlsplit

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import activity_environment
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema, _surface
from code_assist_v1.tests.notion_collaboration.test_database_trash_http import _client
from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import (
    environment, appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, owned_database_block,
)


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('property_id,property_name,property_value', [
    ('property-date', '날짜', '2026-09-20'),
    ('property-score', '점수', 42),
])
def test_selected_rows_edit_property_once_and_escape_keeps_selection(
        document_page, activity_environment, monkeypatch,
        property_id, property_name, property_value):
    from playwright.sync_api import expect

    store, workspace_id = activity_environment
    owned_database_block(store)
    _schema(store.iam, "database.property.create", "property-date",
        {"name": "날짜", "type": "date", "config": {}},
        "selected-rows-property-date")
    _schema(store.iam, "database.property.create", "property-score",
        {"name": "점수", "type": "number", "config": {}},
        "selected-rows-property-score")
    _surface(store.iam, "database.row.create", "row-e2", {},
        "selected-rows-property-second")
    client, _actor = _client(store, monkeypatch, enabled=False)
    base = f"/api/notion/workspaces/{workspace_id}"
    database = store.iam.read_database("owner", "database-e1")
    page_data = store.iam.read_document("owner", "page-e1")
    requests = []

    def route_to_store(route):
        request = route.request
        address = urlsplit(request.url)
        if not address.path.startswith(base + "/"):
            route.continue_()
            return
        target = address.path + ("?" + address.query if address.query else "")
        response = client.open(target, method=request.method,
            data=request.post_data, content_type="application/json")
        requests.append((request.method, address.path, response.status_code))
        route.fulfill(status=response.status_code, content_type="application/json",
            body=response.get_data())

    page = document_page
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route("**/api/notion/workspaces/**", route_to_store)
    page.evaluate("""({workspaceId, database, pageData}) => {
      State.activePageId = 'page-e1';
      State.pageData.page = pageData;
      State.navigation.workspaceId = workspaceId;
      State.navigation.collaborativeSharing = true;
      collaborativeDatabaseActive = () => true;
      activePageCanEdit = () => true;
      State.pageData.databases = [database];
      const block = EditorCore.createBlock('table', {content:'행 속성 검증',
        props:{database_id:'database-e1',properties:database.properties,
          rows:database.rows.map(item=>({page_id:item.id,_canonical_row_id:item.id,
            'property-e1':item.cells['property-e1'],
            'property-date':item.cells['property-date'],
            'property-score':item.cells['property-score']}))}});
      State.pageData.blocks = [block];
      renderBlocks = () => {};
      const root = document.createElement('div');
      root.id = 'selectedRowPropertyProbe';
      root.style.cssText = 'position:relative;margin:60px 40px;';
      root.innerHTML = '<div class="dynamic-table-grid">'
        + '<div class="table-row table-header"><span>이름</span><span>날짜</span></div>'
        + database.rows.map((item,index)=>'<div class="table-row"><span>'
          + '<input data-db-row="'+index+'" data-db-field="property-e1" value="행">'
          + '</span><span><button data-db-row="'+index+'" data-db-field="property-date">날짜</button>'
          + '</span></div>').join('') + '</div>';
      document.body.append(root);
      bindBlockGrid(root, block);
      NotionGridSelection.syncGutterRows([{grid:root.querySelector('.dynamic-table-grid'),
        rows:database.rows.map(item=>item.id)}]);
    }""", {"workspaceId": workspace_id, "database": database,
            "pageData": page_data})
    toolbar = page.locator('#selectedRowPropertyProbe .database-row-selection-toolbar')
    expect(toolbar).to_contain_text('2개 선택됨')
    toolbar.locator('[data-selected-row-more]').click()
    toolbar.locator('[data-edit-selected-properties]').click()
    expect(toolbar.locator('.database-row-property-menu')).to_be_visible()
    toolbar.locator('.database-row-property-menu').press('Escape')
    expect(toolbar).to_contain_text('2개 선택됨')
    toolbar.locator('[data-edit-selected-properties]').click()
    toolbar.get_by_role('menuitem', name=property_name).click()
    if property_id == 'property-date':
        expect(page.locator('.database-date-picker')).to_be_visible()
        page.keyboard.press('Escape')
        expect(page.locator('.database-date-picker')).to_have_count(0)
        expect(toolbar).to_contain_text('2개 선택됨')
        toolbar.locator('[data-selected-row-more]').click()
        toolbar.locator('[data-edit-selected-properties]').click()
        toolbar.get_by_role('menuitem', name=property_name).click()
        page.locator('.database-date-picker [data-date-day="2026-09-20"]').click()
    else:
        editor = page.locator('.database-row-property-editor')
        expect(editor).to_be_visible()
        editor.get_by_role('spinbutton', name=property_name).fill('42')
        editor.get_by_role('button', name='적용').click()
    page.wait_for_function("""name => document.querySelector('#status')?.textContent
      ?.includes('2개 항목 · ' + name + ' 변경됨')""",
      arg=property_name, timeout=10_000)
    status = page.locator('#status').inner_text()
    assert f'2개 항목 · {property_name} 변경됨' in status, {
        'status': status, 'requests': requests, 'errors': errors,
        'database': store.iam.read_database('owner', 'database-e1')}
    updated = store.iam.read_database("owner", "database-e1")
    assert all(item['cells'][property_id] == property_value
        for item in updated['rows'])
    assert sum(method == 'POST' and path.endswith('/databases/commands')
        and status == 201 for method, path, status in requests) == 1
