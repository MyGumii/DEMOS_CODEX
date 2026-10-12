"""UI-R5: browser selection through the real isolated 2423 row lifecycle."""

from urllib.parse import urlsplit

import pytest

from code_assist_v1 import notion_workspace_database_trash_migration as database_trash_migration
from code_assist_v1 import notion_workspace_database_row_trash_migration as row_trash_migration
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.browser.test_notion_document_extras import document_page
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import activity_environment
from code_assist_v1.tests.notion_collaboration.test_database_features import _surface
from code_assist_v1.tests.notion_collaboration.test_database_row_content import _apply
from code_assist_v1.tests.notion_collaboration.test_database_trash_http import _client
from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import (
    environment, appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, owned_database_block,
)


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("mode", ["batch", "single_lost", "batch_lost",
    "batch_restore_lost"])
def test_ui_r5_selected_row_trash_browser_http_store_restore(
        document_page, activity_environment, monkeypatch, mode):
    from playwright.sync_api import expect

    source, workspace_id = activity_environment
    owned_database_block(source)
    include_second = mode != "single_lost"
    lose_archive_response = mode in {"single_lost", "batch_lost"}
    lose_restore_response = mode == "batch_restore_lost"
    if include_second:
        _surface(source.iam, "database.row.create", "row-e2", {},
            "row-browser-create-second")
    _apply(database_trash_migration, source.db_path, source.user_data_root,
        "before-row-browser-source.db")
    _apply(row_trash_migration, source.db_path, source.user_data_root,
        "before-row-browser.db")
    store = CollaborativeWorkspaceStore(source.db_path,
        user_data_root=source.user_data_root,
        identity_directory=source.identity_directory)
    client, _actor = _client(store, monkeypatch, enabled=False)
    original_capabilities = store.capabilities
    monkeypatch.setattr(store, "capabilities", lambda: {
        **original_capabilities(), "collaborative_database_row_trash": True})
    base = f"/api/notion/workspaces/{workspace_id}"
    assert client.get(base + "/pages/page-e1/collaboration").status_code == 200
    assert client.get(base + "/pages/page-e1/databases/database-e1").status_code == 200

    page = document_page
    requests = []
    fault = {"sent": False}

    def route_to_2423(route):
        request = route.request
        address = urlsplit(request.url)
        target = address.path + ("?" + address.query if address.query else "")
        if not address.path.startswith(base + "/"):
            route.continue_()
            return
        response = client.open(target, method=request.method,
            data=request.post_data, content_type="application/json")
        requests.append((request.method, address.path, response.status_code))
        if ((lose_archive_response or lose_restore_response) and not fault["sent"]
                and request.method == "POST"
                and address.path.endswith("/rows/row-e1/trash"
                    if mode == "single_lost" else "/rows/trash-batch"
                    if mode == "batch_lost" else "/rows/restore-batch")):
            fault["sent"] = True
            route.fulfill(status=503, content_type="application/json",
                body='{"error":"synthetic response loss"}')
            return
        route.fulfill(status=response.status_code,
            content_type="application/json", body=response.get_data())

    page.route("**/api/notion/workspaces/**", route_to_2423)
    page.evaluate("""({workspaceId, includeSecond}) => {
      const rows = [{page_id:'row-e1',_canonical_row_id:'row-e1',
        'property-e1':'행 휴지통 검증'}];
      if (includeSecond) rows.push({page_id:'row-e2',_canonical_row_id:'row-e2',
        'property-e1':'둘째 행'});
      const block = EditorCore.createBlock('table', {content:'보관할 표',
        props:{database_id:'database-e1',properties:[{id:'property-e1',name:'이름',type:'title'}],
          rows}});
      State.activePageId = 'page-e1';
      State.pageData.page.id = 'page-e1';
      State.pageData.blocks = [block];
      State.pageData.databases = [{id:'database-e1',version:1,rows:[]}];
      State.navigation.workspaceId = workspaceId;
      State.navigation.collaborativeSharing = true;
      State.navigation.collaborativeDatabaseRowTrash = true;
      collaborativeDatabaseActive = () => true;
      activePageCanEdit = () => true;
      holdPageMoveInputs = () => () => {};
      flushPageMoveEditors = async () => true;
      refreshAfterCollaborativeMove = async () => {};
      reloadCanonicalDatabases = async () => [];
      renderBlocks = () => {};
      PageMetadataSync = {invalidate:() => {}};
      const root = document.createElement('div');
      root.id = 'realRowTrashProbe';
      root.style.cssText = 'position:relative;margin:60px 40px;';
      root.innerHTML = '<div class="dynamic-table-grid">'
        + '<div class="table-row table-header"><span>이름</span></div>'
        + '<div class="table-row"><span><input data-db-row="0" data-db-field="property-e1" value="행 휴지통 검증"></span></div>'
        + (includeSecond
          ? '<div class="table-row"><span><input data-db-row="1" data-db-field="property-e1" value="둘째 행"></span></div>'
          : '')
        + '</div>';
      document.body.append(root);
      bindBlockGrid(root, block);
      const grid = root.querySelector('.dynamic-table-grid');
      NotionGridSelection.syncGutterRows([{grid,rows:includeSecond
        ? ['row-e1','row-e2'] : ['row-e1']}]);
    }""", {"workspaceId": workspace_id,
        "includeSecond": include_second})
    expect(page.locator('#realRowTrashProbe [data-trash-selected-rows]')).to_be_visible()
    page.locator('#realRowTrashProbe [data-trash-selected-rows]').click()
    if lose_archive_response:
        pending = page.locator('#databaseRowTrashStatus > div').filter(
            has_text='행 휴지통 검증' if mode == "single_lost"
                else '2개 데이터베이스 항목')
        expect(pending.get_by_role('button', name='결과 확인')).to_be_visible()
        pending.get_by_role('button', name='결과 확인').click()
    page.wait_for_function("() => document.querySelector('#databaseRowTrashStatus')"
        ".textContent.includes('휴지통으로 이동했습니다')")
    if include_second:
        page.wait_for_function("() => document.querySelector('#databaseRowTrashStatus')"
            ".textContent.includes('2개 데이터베이스 항목')")
    assert store.iam.read_database("owner", "database-e1")["rows"] == []
    if mode == "single_lost":
        assert any(method == "POST" and path.endswith("/rows/row-e1/trash")
            and status == 201 for method, path, status in requests)
    else:
        assert sum(method == "POST" and path.endswith("/rows/trash-batch")
            and status == 201 for method, path, status in requests) == 1
        assert not any(method == "POST" and "/rows/row-" in path
            and path.endswith("/trash") for method, path, _status in requests)
        batch = page.locator('#databaseRowTrashStatus > div').filter(
            has_text='2개 데이터베이스 항목')
        if mode == "batch_lost":
            assert any(path.endswith("/rows/trash-batch/lifecycle-result")
                and status == 200 for _method, path, status in requests)
        expect(batch.get_by_role('button', name='복원')).to_be_visible()
        batch.get_by_role('button', name='복원').click()
        if lose_restore_response:
            expect(batch.get_by_role('button', name='결과 확인')).to_be_visible()
            batch.get_by_role('button', name='결과 확인').click()
        page.wait_for_function("() => document.querySelector('#databaseRowTrashStatus')"
            ".textContent.includes('2개 데이터베이스 항목 · 복원했습니다')")
        visible_rows = store.iam.read_database("owner", "database-e1")["rows"]
        assert [item["id"] for item in visible_rows] == ["row-e1", "row-e2"]
        assert sum(method == "POST" and path.endswith("/rows/restore-batch")
            and status == 201 for method, path, status in requests) == 1
        if lose_restore_response:
            assert any(path.endswith("/rows/restore-batch/lifecycle-result")
                and status == 200 for _method, path, status in requests)
        expect(batch.get_by_role('button', name='확인')).to_be_visible()
        batch.get_by_role('button', name='확인').click()
        expect(batch).to_have_count(0)
        return
    assert any(path.endswith("/rows/row-e1/lifecycle-result") and status == 200
        for _method, path, status in requests)
    banner = page.locator('#databaseRowTrashStatus > div').filter(has_text='행 휴지통 검증')
    expect(banner.get_by_role('button', name='복원')).to_be_visible()
    banner.get_by_role('button', name='복원').click()
    page.wait_for_function("() => document.querySelector('#databaseRowTrashStatus')"
        ".textContent.includes('복원했습니다')")
    visible_rows = store.iam.read_database("owner", "database-e1")["rows"]
    assert [item["id"] for item in visible_rows] == ["row-e1"]
    assert any(method == "POST" and path.endswith("/rows/row-e1/restore") and status == 201
        for method, path, status in requests)
