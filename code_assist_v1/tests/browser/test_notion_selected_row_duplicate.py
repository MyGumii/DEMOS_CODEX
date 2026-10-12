"""Selected-row duplicate menu commits one canonical batch in the browser."""

import json
from urllib.parse import urlsplit

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page
from code_assist_v1.tests.notion_collaboration.test_database_row_duplicate_batch import (
    icon_batch_environment, appearance_environment, activity_environment,
    environment, body_environment, team_environment, file_environment,
    member_environment, native_environment, archive_environment,
    guest_environment, image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_trash_http import _client


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("lose_response", [False, True])
def test_selected_row_duplicate_menu_uses_one_replayable_command(
        document_page, icon_batch_environment, monkeypatch, lose_response):
    from playwright.sync_api import expect

    store, workspace_id = icon_batch_environment
    client, _ = _client(store, monkeypatch, enabled=False)
    capabilities = store.capabilities
    monkeypatch.setattr(store, "capabilities", lambda: {
        **capabilities(), "collaborative_database_row_duplicate_batch": True})
    base = f"/api/notion/workspaces/{workspace_id}"
    database = store.iam.read_database("owner", "database-e1")
    page_data = store.iam.read_document("owner", "page-e1")
    requests = []
    fault = {"sent": False}

    def route_to_store(route):
        request = route.request
        address = urlsplit(request.url)
        if not address.path.startswith(base + "/"):
            route.continue_()
            return
        target = address.path + ("?" + address.query if address.query else "")
        response = client.open(target, method=request.method,
            data=request.post_data, content_type="application/json")
        requests.append((request.method, address.path, response.status_code,
            request.post_data))
        if lose_response and not fault["sent"] and request.method == "POST" \
                and address.path.endswith("/databases/commands"):
            fault["sent"] = True
            route.fulfill(status=503, content_type="application/json",
                body='{"error":"synthetic response loss"}')
            return
        route.fulfill(status=response.status_code, content_type="application/json",
            body=response.get_data())

    page = document_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/notion/workspaces/**", route_to_store)
    page.evaluate("""({workspaceId,database,pageData}) => {
      State.activePageId='page-e1'; State.pageData.page=pageData;
      State.navigation.workspaceId=workspaceId;
      State.navigation.collaborativeSharing=true;
      State.navigation.collaborativeDatabaseRowDuplicateBatch=true;
      collaborativeDatabaseActive=()=>true; activePageCanEdit=()=>true;
      State.pageData.databases=[database];
      const block=EditorCore.createBlock('table',{content:'행 복제 검증',
        props:{database_id:'database-e1',properties:database.properties,
          rows:database.rows.map(item=>({page_id:item.id,_canonical_row_id:item.id,
            'property-e1':item.cells['property-e1']}))}});
      State.pageData.blocks=[block]; renderBlocks=()=>{};
      const root=document.createElement('div');root.id='selectedRowDuplicateProbe';
      root.style.cssText='position:relative;margin:60px 40px;';
      root.innerHTML='<div class="dynamic-table-grid"><div class="table-row table-header">'
        +'<span>이름</span></div>'
        +database.rows.map((item,index)=>'<div class="table-row"><span>'
          +'<input data-db-row="'+index+'" data-db-field="property-e1" value="행">'
          +'</span></div>').join('')+'</div>';
      document.body.append(root);bindBlockGrid(root,block);
      NotionGridSelection.syncGutterRows([{grid:root.querySelector('.dynamic-table-grid'),
        rows:database.rows.map(item=>item.id)}]);
    }""", {"workspaceId": workspace_id, "database": database,
            "pageData": page_data})
    toolbar = page.locator("#selectedRowDuplicateProbe .database-row-selection-toolbar")
    expect(toolbar).to_contain_text("2개 선택됨")
    toolbar.locator("[data-selected-row-more]").click()
    duplicate = toolbar.locator("[data-duplicate-selected-rows]")
    expect(duplicate).to_be_visible()
    duplicate.click()
    if lose_response:
        expect(page.locator("#status")).to_contain_text("synthetic response loss")
        expect(toolbar.locator("[data-selected-row-more]")).to_be_enabled()
        toolbar.locator("[data-selected-row-more]").click()
        toolbar.locator("[data-duplicate-selected-rows]").click()
    expect(page.locator("#status")).to_contain_text("2개 항목을 복제했습니다.")
    rows = store.iam.read_database("owner", "database-e1")["rows"]
    assert len(rows) == 4
    assert [row["id"] for row in rows[::2]] == ["row-e1", "row-e2"]
    assert [row["cells"] for row in rows[1::2]] == [row["cells"] for row in rows[::2]]
    sent = [json.loads(body) for method, path, status, body in requests
        if method == "POST" and path.endswith("/databases/commands") and status == 201]
    assert len(sent) == (2 if lose_response else 1)
    assert len({item["operation_id"] for item in sent}) == 1
    assert not errors, errors
