"""Selected canonical rows can target an ordinary page in the browser."""

from contextlib import closing
import json
import multiprocessing
import sqlite3
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest

from code_assist_v1.tests.browser.test_notion_document_extras import document_page
from code_assist_v1.tests.notion_collaboration.test_database_row_page_move import (
    _available_local_port, _page_return_http_worker, _request,
    move_owned_rows_to_page,
    placement_environment, icon_batch_environment, activity_environment,
    environment, appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment,
    archive_environment, guest_environment, image_environment,
)
from code_assist_v1.notion_collaboration.database_row_page_move import RETURN_ACTION
from code_assist_v1.tests.notion_collaboration.test_database_trash_http import _client
from code_assist_v1.notion_collaboration.iam_runtime import collaborative_page_payload


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("lose_response,lose_return_response,root_before_return,repeat,other_database,same_document_database", [
    (False, False, False, False, False, False),
    (True, False, False, False, False, False),
    (False, True, False, False, False, False),
    (False, False, True, False, False, False),
    (False, True, True, False, False, False),
    (False, False, False, True, False, False),
    (False, False, False, False, True, False),
    (False, True, False, False, True, False),
    (False, False, False, False, True, True),
    (False, True, False, False, True, True),
])
def test_selected_rows_move_to_ordinary_page_and_recover(
        document_page, placement_environment, monkeypatch, lose_response,
        lose_return_response, root_before_return, repeat, other_database,
        same_document_database):
    from playwright.sync_api import expect

    store, workspace_id = placement_environment
    source_page = store.iam.read_document("owner", "page-e1")
    store.iam.create_document(
        "owner", workspace_id, "page-e2", title="Ordinary destination",
        blocks=[], expected_workspace_epoch=source_page["workspace_epoch"],
        operation_id="browser-create-row-page-parent",
    )
    if other_database:
        database_document = "page-e1" if same_document_database else "page-e2"
        with store.iam._transaction() as con:
            con.execute(
                "INSERT INTO workspace_collaboration_databases("
                "id,document_id,title,row_version,created_at,updated_at) "
                "VALUES('database-e2',?,'Other destination',1,1,1)",
                (database_document,))
            for property_id, name, kind, position in (
                    ("property-title-e2", "Name", "title", 0),
                    ("property-date-text-e2", "날짜", "text", 1)):
                con.execute(
                    "INSERT INTO workspace_collaboration_database_properties("
                    "id,database_id,name,property_type,position,config_json,"
                    "row_version,created_at,updated_at) "
                    "VALUES(?, 'database-e2', ?, ?, ?, '{}', 1, 1, 1)",
                    (property_id, name, kind, position),
                )
        destination_page = store.iam.read_document("owner", database_document)
        store.iam.execute_typed_command(
            "owner", database_document, command_kind="block.insert",
            target_id="browser-other-database-block",
            payload={"type": "table", "content": "Other destination",
                     "props": {"database_id": "database-e2"}},
            expected_document_sequence=destination_page["document_sequence"],
            expected_workspace_epoch=destination_page["workspace_epoch"],
            expected_document_epoch=destination_page["document_epoch"],
            operation_id="browser-create-other-database-block")
    with_reminder = other_database and not same_document_database
    if with_reminder:
        reminder = {"at": "2027-01-14T00:00:00.000Z", "lead": 0,
                    "user_id": "owner", "preset": "day_1",
                    "zone": "Asia/Seoul"}
        source_page = store.iam.read_document("owner", "page-e1")
        store.iam.execute_cell_batch(
            "owner", "page-e1", database_id="database-e1",
            target_id="database-e1",
            payload={"cells": [{"row_id": "row-e1", "property_id": "property-date",
                                "value": {"start": "2027-01-15",
                                          "reminder": reminder},
                                "expected_target_version": 1}]},
            expected_document_sequence=source_page["document_sequence"],
            expected_workspace_epoch=source_page["workspace_epoch"],
            expected_document_epoch=source_page["document_epoch"],
            operation_id="browser-reminder-before-page-move")
        stored_date = next(item for item in store.iam.read_database(
            "owner", "database-e1")["rows"] if item["id"] == "row-e1")[
                "cells"]["property-date"]
    client, _ = _client(store, monkeypatch, enabled=False)
    capabilities = store.capabilities
    monkeypatch.setattr(store, "capabilities", lambda: {
        **capabilities(), "collaborative_database_row_to_page_batch": True,
        "collaborative_page_to_database_row": True})
    source = store.iam.read_database("owner", "database-e1")
    page_data = store.iam.read_document("owner", "page-e1")
    pages = [collaborative_page_payload(store.iam, "owner", key)["page"]
             for key in ("page-e1", "page-e2")]
    base = f"/api/notion/workspaces/{workspace_id}"
    requests = []
    fault = {"sent": False, "return_sent": False}

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
        if lose_return_response and not fault["return_sent"] \
                and request.method == "POST" \
                and address.path.endswith("/return-to-database"):
            fault["return_sent"] = True
            route.fulfill(status=503, content_type="application/json",
                body='{"error":"synthetic return response loss"}')
            return
        route.fulfill(status=response.status_code, content_type="application/json",
            body=response.get_data())

    page = document_page
    fixture_url = page.url
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/notion/workspaces/**", route_to_store)
    state = {"workspaceId": workspace_id, "source": source,
             "pageData": page_data, "pages": pages}
    page.evaluate("""({workspaceId,source,pageData,pages}) => {
      State.activePageId='page-e1';State.pageData.page=pageData;
      State.navigation.workspaceId=workspaceId;
      State.navigation.collaborativeSharing=true;
      State.navigation.collaborativeDatabaseRowMoveBatch=false;
      State.navigation.collaborativeDatabaseRowToPageBatch=true;
      State.navigation.collaborativePageToDatabaseRow=true;
      collaborativeDatabaseActive=()=>true;activePageCanEdit=()=>true;
      State.pages=pages;State.pageData.databases=[source];
      const block=EditorCore.createBlock('table',{content:'원본 DB',
        props:{database_id:source.id,properties:source.properties,
          rows:source.rows.map(item=>({page_id:item.id,_canonical_row_id:item.id,
            'property-title':item.cells['property-title']}))}});
      State.pageData.blocks=[block];renderBlocks=()=>{};
      const root=document.createElement('div');root.id='selectedRowPageProbe';
      root.style.cssText='position:relative;margin:60px 40px;';
      root.innerHTML='<div class="dynamic-table-grid"><div class="table-row table-header">'
        +'<span>이름</span></div>'
        +source.rows.map((item,index)=>'<div class="table-row"><span>'
          +'<input data-db-row="'+index+'" data-db-field="property-title" value="행">'
          +'</span></div>').join('')+'</div>';
      document.body.append(root);bindBlockGrid(root,block);
      NotionGridSelection.syncGutterRows([{grid:root.querySelector('.dynamic-table-grid'),
        rows:source.rows.map(item=>item.id)}]);
    }""", state)
    toolbar = page.locator("#selectedRowPageProbe .database-row-selection-toolbar")
    expect(toolbar).to_contain_text("2개 선택됨")
    toolbar.locator("[data-selected-row-more]").click()
    toolbar.locator("[data-move-selected-rows]").click()
    picker = page.locator(".database-row-move-picker")
    expect(picker).to_be_visible()
    picker.locator("input").fill("Ordinary destination")
    picker.locator('.database-row-move-page[data-destination-page="page-e2"]').click()
    picker.locator('[data-destination-ordinary-page="page-e2"]').click()
    if lose_response:
        expect(page.locator("#status")).to_contain_text("synthetic response loss")
        recovery = page.locator("#databaseRowMoveStatus")
        expect(recovery).to_contain_text("2개 항목의 이동 결과를 확인해야 합니다.")
        page.reload()
        page.wait_for_function('typeof State !== "undefined" && State.user && State.pageData')
        page.evaluate("""({workspaceId,source,pageData,pages}) => {
          State.activePageId='page-e1';State.pageData.page=pageData;
          State.pageData.databases=[source];State.pageData.blocks=[];
          State.pages=pages;State.navigation.workspaceId=workspaceId;
          State.navigation.collaborativeSharing=true;
          State.navigation.collaborativeDatabaseRowToPageBatch=true;
          State.navigation.collaborativePageToDatabaseRow=true;
          collaborativeDatabaseActive=()=>true;activePageCanEdit=()=>true;
          renderBlocks=()=>{};renderPageLists();
        }""", state)
        recovery = page.locator("#databaseRowMoveStatus")
        expect(recovery).to_contain_text("2개 항목의 이동 결과를 확인해야 합니다.")
        recovery.get_by_role("button", name="결과 확인").click()
        expect(page.locator("#status")).to_contain_text("1건의 이동 결과를 확인했습니다.")
        expect(recovery).to_be_hidden()
    else:
        expect(page.locator("#status")).to_contain_text("2개 항목을 페이지로 옮겼습니다.")
    assert store.iam.read_database("owner", "database-e1")["rows"] == []
    assert [store.iam.read_document("owner", key)["parent_document_id"]
            for key in ("row-e1", "row-e2")] == ["page-e2", "page-e2"]
    if with_reminder:
        during = store.iam.list_reminders("owner", workspace_id)["items"]
        assert len(during) == 1 and during[0]["page_id"] == "row-e1"
    sent = [json.loads(body) for method, path, status, body in requests
            if method == "POST" and path.endswith("/databases/commands")
            and status == 201]
    assert len(sent) == (2 if lose_response else 1)
    assert len({item["operation_id"] for item in sent}) == 1
    assert all(item["command_kind"] == "database.rows.move_to_page" for item in sent)
    if not lose_response:
        page.evaluate("""async () => {
          history.replaceState({pageId:'page-e1'},'',
            '/page/page-e1?item_db=database-e1&item_row=row-e1&item_view=full');
          await openPage('page-e1',false);
        }""")
        page.wait_for_function("State.activePageId==='row-e1'")
        url = urlsplit(page.url)
        assert url.path == "/page/row-e1"
        assert not any(key in parse_qs(url.query)
                       for key in ("item_db", "item_row", "item_view"))
        if other_database:
            page.locator("#pageMoreBtn").click()
            page.locator('#pageMenu [data-page-action="move"]').click()
            other_picker = page.locator("#movePageMenu")
            expect(other_picker).to_be_visible()
            other_target = other_picker.locator(
                '[data-move-database="database-e2"]')
            expect(other_target).to_be_visible(timeout=20_000)
            other_target.click()
            if lose_return_response:
                expect(other_picker.locator('#movePageError')).to_contain_text(
                    'synthetic return response loss')
                recovery = page.locator("#databaseRowMoveStatus")
                expect(recovery).to_contain_text(
                    "1개 항목의 이동 결과를 확인해야 합니다.")
                page.goto(fixture_url)
                page.wait_for_function(
                    'typeof State !== "undefined" && State.user && State.pageData')
                page.evaluate("""({workspaceId,source,pageData,pages}) => {
                  State.activePageId='page-e1';State.pageData.page=pageData;
                  State.pageData.databases=[source];State.pageData.blocks=[];
                  State.pages=pages;State.navigation.workspaceId=workspaceId;
                  State.navigation.collaborativeSharing=true;
                  State.navigation.collaborativePageToDatabaseRow=true;
                  collaborativeDatabaseActive=()=>true;
                  renderBlocks=()=>{};renderPageLists();
                }""", state)
                recovery = page.locator("#databaseRowMoveStatus")
                expect(recovery).to_contain_text(
                    "1개 항목의 이동 결과를 확인해야 합니다.")
                recovery.get_by_role("button", name="결과 확인").click()
            if same_document_database:
                page.wait_for_function("State.activePageId==='page-e1'")
            else:
                page.wait_for_function("State.activePageId==='page-e2' && "
                    "LinkedDatabaseItems?.snapshot()?.rowId==='row-e1'")
            return_posts = [json.loads(body) for method, path, status, body
                in requests if method == "POST" and
                path.endswith("/return-to-database") and status == 200]
            assert len(return_posts) == (2 if lose_return_response else 1)
            assert len({item["operation_id"] for item in return_posts}) == 1
            moved = store.iam.read_database("owner", "database-e2")
            row = moved["rows"][0]
            assert row["id"] == "row-e1"
            assert row["cells"]["property-title-e2"] == "페이지로 이동 A"
            date = next(item for item in moved["properties"]
                        if item["name"] == "날짜 1" and item["type"] == "date")
            assert row["cells"][date["id"]] == (
                stored_date if with_reminder else "2026-09-15")
            if with_reminder:
                after = store.iam.list_reminders("owner", workspace_id)["items"]
                assert len(after) == 1
                assert after[0]["page_id"] == database_document
                assert after[0]["remind_at"] == reminder["at"]
            assert "property-date-text-e2" not in row["cells"]
            assert not errors
            return
        if root_before_return:
            page.evaluate("""workspaceId => {
              State.navigation.enabled=true;
              State.navigation.authoritative=true;
              State.navigation.pageWorkspaceResolution=true;
              State.navigation.workspaces=[{id:workspaceId,name:'Test workspace',
                version:1,access:{role:'owner'}}];
              collaborativeEditingActive=()=>false;
            }""", workspace_id)
            page.locator("#pageMoreBtn").click()
            page.locator('#pageMenu [data-page-action="move"]').click()
            root_picker = page.locator("#movePageMenu")
            expect(root_picker).to_be_visible()
            root_target = root_picker.locator('[data-move-parent=""]')
            expect(root_target).to_be_visible()
            root_target.click()
            expect(root_picker).to_be_hidden(timeout=20_000)
            assert store.iam.read_document("owner", "row-e1")[
                "parent_document_id"] is None
            root_posts = [json.loads(body) for method, path, status, body in requests
                if method == "POST" and path.endswith("/pages/row-e1/move")
                and status == 200]
            assert len(root_posts) == 1
            assert root_posts[0]["parent_id"] == ""
            # This fixture's /pages projection contains the newly placed rows,
            # but not the source document seeded directly in canonical storage.
            page.evaluate("""pages => {
              const seen=new Set(State.pages.map(page=>page.id));
              State.pages.push(...pages.filter(page=>!seen.has(page.id)));
            }""", pages)
        page.locator("#pageMoreBtn").click()
        page.locator('#pageMenu [data-page-action="move"]').click()
        picker = page.locator("#movePageMenu")
        expect(picker).to_be_visible()
        target = picker.locator('[data-return-to-database="database-e1"]')
        expect(target).to_be_visible(timeout=20_000)
        target.click()
        if lose_return_response:
            expect(picker.locator('#movePageError')).to_contain_text(
                'synthetic return response loss')
            recovery = page.locator("#databaseRowMoveStatus")
            expect(recovery).to_contain_text(
                "1개 항목의 이동 결과를 확인해야 합니다.")
            page.goto(fixture_url)
            page.wait_for_function('typeof State !== "undefined" && State.user && State.pageData')
            page.evaluate("""({workspaceId,source,pageData,pages}) => {
              State.activePageId='page-e1';State.pageData.page=pageData;
              State.pageData.databases=[source];State.pageData.blocks=[];
              State.pages=pages;State.navigation.workspaceId=workspaceId;
              State.navigation.collaborativeSharing=true;
              State.navigation.collaborativePageToDatabaseRow=true;
              collaborativeDatabaseActive=()=>true;
              renderBlocks=()=>{};renderPageLists();
            }""", state)
            recovery = page.locator("#databaseRowMoveStatus")
            expect(recovery).to_contain_text(
                "1개 항목의 이동 결과를 확인해야 합니다.")
            recovery.get_by_role("button", name="결과 확인").click()
        page.wait_for_function("State.activePageId==='page-e1' && "
            "LinkedDatabaseItems?.snapshot()?.rowId==='row-e1'")
        returned = store.iam.read_database("owner", "database-e1")["rows"]
        assert [item["id"] for item in returned] == ["row-e1"]
        assert returned[0]["cells"]["property-date"] == "2026-09-15"
        return_posts = [json.loads(body) for method, path, status, body in requests
            if method == "POST" and path.endswith("/return-to-database")
            and status == 200]
        assert len(return_posts) == (2 if lose_return_response else 1)
        assert len({item["operation_id"] for item in return_posts}) == 1
        if repeat:
            current_source = store.iam.read_database("owner", "database-e1")
            current_page = store.iam.read_document("owner", "page-e1")
            page.goto(fixture_url)
            page.wait_for_function('typeof State !== "undefined" && State.user && State.pageData')
            page.evaluate("""({workspaceId,source,pageData,pages}) => {
              State.activePageId='page-e1';State.pageData.page=pageData;
              State.navigation.workspaceId=workspaceId;
              State.navigation.collaborativeSharing=true;
              State.navigation.collaborativeDatabaseRowToPageBatch=true;
              State.navigation.collaborativePageToDatabaseRow=true;
              collaborativeDatabaseActive=()=>true;activePageCanEdit=()=>true;
              State.pages=pages;State.pageData.databases=[source];
              const block=EditorCore.createBlock('table',{content:'원본 DB',
                props:{database_id:source.id,properties:source.properties,
                  rows:source.rows.map(item=>({page_id:item.id,
                    _canonical_row_id:item.id,
                    'property-title':item.cells['property-title']}))}});
              State.pageData.blocks=[block];renderBlocks=()=>{};
              const root=document.createElement('div');root.id='secondRoundtripProbe';
              root.style.cssText='position:relative;margin:60px 40px;';
              root.innerHTML='<div class="dynamic-table-grid">'
                +'<div class="table-row table-header"><span>이름</span></div>'
                +'<div class="table-row"><span><input data-db-row="0" '
                +'data-db-field="property-title" value="행"></span></div></div>';
              document.body.append(root);bindBlockGrid(root,block);
              NotionGridSelection.syncGutterRows([{
                grid:root.querySelector('.dynamic-table-grid'),rows:[source.rows[0].id]}]);
            }""", {"workspaceId": workspace_id, "source": current_source,
                    "pageData": current_page, "pages": pages})
            second_toolbar = page.locator(
                "#secondRoundtripProbe .database-row-selection-toolbar")
            expect(second_toolbar).to_contain_text("1개 선택됨")
            second_toolbar.locator("[data-selected-row-more]").click()
            second_toolbar.locator("[data-move-selected-rows]").click()
            second_picker = page.locator(".database-row-move-picker")
            expect(second_picker).to_be_visible()
            second_picker.locator("input").fill("Ordinary destination")
            second_picker.locator(
                '.database-row-move-page[data-destination-page="page-e2"]').click()
            second_picker.locator(
                '[data-destination-ordinary-page="page-e2"]').click()
            expect(page.locator("#status")).to_contain_text(
                "1개 항목을 페이지로 옮겼습니다.")
            assert store.iam.read_database("owner", "database-e1")["rows"] == []
            assert store.iam.read_document("owner", "row-e1")[
                "parent_document_id"] == "page-e2"
            page.evaluate("() => openPage('row-e1', false)")
            page.wait_for_function("State.activePageId==='row-e1'")
            page.locator("#pageMoreBtn").click()
            page.locator('#pageMenu [data-page-action="move"]').click()
            second_return = page.locator("#movePageMenu")
            expect(second_return).to_be_visible()
            second_target = second_return.locator(
                '[data-return-to-database="database-e1"]')
            expect(second_target).to_be_visible(timeout=20_000)
            second_target.click()
            page.wait_for_function("State.activePageId==='page-e1' && "
                "LinkedDatabaseItems?.snapshot()?.rowId==='row-e1'")
            twice_returned = store.iam.read_database("owner", "database-e1")[
                "rows"]
            assert [item["id"] for item in twice_returned] == ["row-e1"]
            assert twice_returned[0]["cells"]["property-date"] == "2026-09-15"
            assert len([1 for method, path, status, _ in requests
                        if method == "POST" and status == 200
                        and path.endswith("/return-to-database")]) == 2
    assert not errors, errors


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("other_database", [False, True])
def test_ui_r5_page_return_browser_recovers_across_two_http_processes(
        document_page, placement_environment, other_database):
    from playwright.sync_api import expect

    store, workspace_id = placement_environment
    source = store.iam.read_document("owner", "page-e1")
    store.iam.create_document(
        "owner", workspace_id, "page-e2", title="HTTP browser parent", blocks=[],
        expected_workspace_epoch=source["workspace_epoch"],
        operation_id="browser-http-return-parent")
    if other_database:
        with store.iam._transaction() as con:
            con.execute(
                "INSERT INTO workspace_collaboration_databases("
                "id,document_id,title,row_version,created_at,updated_at) "
                "VALUES('database-e2','page-e2','HTTP other DB',1,1,1)")
            for property_id, name, kind, position in (
                    ("property-title-e2", "Name", "title", 0),
                    ("property-date-text-e2", "날짜", "text", 1)):
                con.execute(
                    "INSERT INTO workspace_collaboration_database_properties("
                    "id,database_id,name,property_type,position,config_json,"
                    "row_version,created_at,updated_at) "
                    "VALUES(?,'database-e2',?,?,?,'{}',1,1,1)",
                    (property_id, name, kind, position))
            con.execute(
                "INSERT INTO workspace_collaboration_database_properties("
                "id,database_id,name,property_type,position,config_json,"
                "row_version,created_at,updated_at) VALUES("
                "'property-relation','database-e1','Related','relation',3,?,1,1,1)",
                (json.dumps({"target_database_id": "database-e2"}),))
            con.execute(
                "INSERT INTO workspace_collaboration_database_properties("
                "id,database_id,name,property_type,position,config_json,"
                "row_version,created_at,updated_at) VALUES("
                "'property-back','database-e2','Back','relation',2,?,1,1,1)",
                (json.dumps({"target_database_id": "database-e1"}),))
            con.execute(
                "INSERT INTO workspace_collaboration_database_rows("
                "id,database_id,position,row_version,created_at,updated_at) "
                "VALUES('row-target','database-e2',0,1,1,1)")
            con.execute(
                "INSERT INTO workspace_collaboration_database_cells("
                "row_id,property_id,value_json,row_version,updated_at) "
                "VALUES('row-e1','property-relation',?,1,1)",
                (json.dumps(['row-target']),))
            con.execute(
                "INSERT INTO workspace_collaboration_database_cells("
                "row_id,property_id,value_json,row_version,updated_at) "
                "VALUES('row-target','property-back',?,1,1)",
                (json.dumps(['row-e1']),))
        destination = store.iam.read_document("owner", "page-e2")
        store.iam.execute_typed_command(
            "owner", "page-e2", command_kind="block.insert",
            target_id="browser-http-other-db-block",
            payload={"type": "table", "content": "HTTP other DB",
                     "props": {"database_id": "database-e2"}},
            expected_document_sequence=destination["document_sequence"],
            expected_workspace_epoch=destination["workspace_epoch"],
            expected_document_epoch=destination["document_epoch"],
            operation_id="browser-http-other-db-block")
    move_owned_rows_to_page(store.iam, **_request(
        store, workspace_id, "browser-http-row-to-page", parent_id="page-e2",
        row_ids={"row-e1"}))
    if other_database:
        assert store.iam.read_database("owner", "database-e2")["rows"][0][
            "cells"]["property-back"] == []
    source_page = collaborative_page_payload(
        store.iam, "owner", "page-e1")["page"]
    pages = [collaborative_page_payload(store.iam, "owner", key)["page"]
             for key in ("page-e1", "page-e2", "row-e1")]
    source_database = store.iam.read_database("owner", "database-e1")

    first_port = _available_local_port()
    second_port = _available_local_port()
    while second_port == first_port:
        second_port = _available_local_port()
    ports = (first_port, second_port)
    context = multiprocessing.get_context("spawn")
    workers = [context.Process(
        target=_page_return_http_worker,
        args=(str(store.db_path), str(store.user_data_root), port))
        for port in ports]
    started = []
    try:
        for worker in workers:
            worker.start()
            started.append(worker)
        return_path = (f"/api/notion/workspaces/{workspace_id}/pages/row-e1/"
                       "return-to-database")
        deadline = time.monotonic() + 40
        for port in ports:
            target_url = f"http://127.0.0.1:{port}{return_path}-target"
            while True:
                if any(worker.exitcode is not None for worker in started):
                    raise RuntimeError("isolated page-return HTTP server stopped")
                try:
                    with urllib.request.urlopen(target_url, timeout=3) as response:
                        assert response.status == 200
                        assert json.load(response)["row_id"] == "row-e1"
                    break
                except (urllib.error.URLError, TimeoutError):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("isolated page-return HTTP server timed out")
                    time.sleep(0.2)

        page = document_page
        fixture_url = page.url
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        requests = []
        return_attempts = 0

        def route_to_process(route):
            nonlocal return_attempts
            request = route.request
            address = urlsplit(request.url)
            if not address.path.startswith(
                    f"/api/notion/workspaces/{workspace_id}/"):
                route.continue_()
                return
            is_return = (request.method == "POST"
                         and address.path == return_path)
            server_index = 1 if is_return and return_attempts else 0
            if is_return:
                return_attempts += 1
            target = (f"http://127.0.0.1:{ports[server_index]}"
                      + address.path + ("?" + address.query if address.query else ""))
            body = request.post_data
            outbound = urllib.request.Request(
                target, data=body.encode("utf-8") if body is not None else None,
                headers={"Content-Type": "application/json"},
                method=request.method)
            try:
                with urllib.request.urlopen(outbound, timeout=15) as response:
                    status, payload = response.status, response.read()
            except urllib.error.HTTPError as error:
                status, payload = error.code, error.read()
            requests.append((server_index, request.method, address.path,
                             status, body))
            if is_return and return_attempts == 1 and status == 200:
                route.fulfill(status=503, content_type="application/json",
                    body='{"error":"synthetic return response loss"}')
            else:
                route.fulfill(status=status, content_type="application/json",
                              body=payload)

        page.route("**/api/notion/workspaces/**", route_to_process)
        page.evaluate("""async ({workspaceId,pages}) => {
          State.navigation.workspaceId=workspaceId;
          State.navigation.collaborativeSharing=true;
          State.navigation.collaborativePageToDatabaseRow=true;
          State.pages=pages;collaborativeDatabaseActive=()=>true;
          activePageCanEdit=()=>true;
          await openPage('row-e1',false);
        }""", {"workspaceId": workspace_id, "pages": pages})
        page.wait_for_function("State.activePageId==='row-e1'")
        page.locator("#pageMoreBtn").click()
        page.locator('#pageMenu [data-page-action="move"]').click()
        picker = page.locator("#movePageMenu")
        expect(picker).to_be_visible()
        target = picker.locator(
            '[data-move-database="database-e2"]' if other_database
            else '[data-return-to-database="database-e1"]')
        expect(target).to_be_visible(timeout=20_000)
        target.click()
        expect(picker.locator("#movePageError")).to_contain_text(
            "synthetic return response loss")
        recovery = page.locator("#databaseRowMoveStatus")
        expect(recovery).to_contain_text(
            "1개 항목의 이동 결과를 확인해야 합니다.")
        page.goto(fixture_url)
        page.wait_for_function(
            'typeof State !== "undefined" && State.user && State.pageData')
        page.evaluate("""({workspaceId,source,pageData,pages}) => {
          State.activePageId='page-e1';State.pageData.page=pageData;
          State.pageData.databases=[source];State.pageData.blocks=[];
          State.pages=pages;State.navigation.workspaceId=workspaceId;
          State.navigation.collaborativeSharing=true;
          State.navigation.collaborativePageToDatabaseRow=true;
          collaborativeDatabaseActive=()=>true;
          renderBlocks=()=>{};renderPageLists();
        }""", {"workspaceId": workspace_id, "source": source_database,
                 "pageData": source_page, "pages": pages})
        recovery = page.locator("#databaseRowMoveStatus")
        expect(recovery).to_contain_text(
            "1개 항목의 이동 결과를 확인해야 합니다.")
        recovery.get_by_role("button", name="결과 확인").click()
        destination_page_id = "page-e2" if other_database else "page-e1"
        page.wait_for_function("destination => State.activePageId===destination && "
            "LinkedDatabaseItems?.snapshot()?.rowId==='row-e1'",
            arg=destination_page_id)
        assert [(server, method) for server, method, path, status, _ in requests
                if path == return_path and status == 200] == [(0, "POST"),
                                                               (1, "POST")]
        database_id = "database-e2" if other_database else "database-e1"
        returned_database = store.iam.read_database("owner", database_id)
        returned = returned_database["rows"]
        row = next(item for item in returned if item["id"] == "row-e1")
        if other_database:
            date = next(item for item in returned_database["properties"]
                        if item["name"] == "날짜 1" and item["type"] == "date")
            assert row["cells"][date["id"]] == "2026-09-15"
            assert "property-date-text-e2" not in row["cells"]
            assert "property-relation" not in row["cells"]
            assert next(item for item in returned if item["id"] == "row-target")[
                "cells"]["property-back"] == []
            with store.iam._reader() as con:
                retained = con.execute(
                    "SELECT value_json FROM workspace_collaboration_database_cells "
                    "WHERE row_id='row-e1' AND property_id='property-relation'"
                ).fetchone()
                assert retained is not None
                assert json.loads(retained["value_json"]) == ['row-target']
                assert json.loads(con.execute(
                    "SELECT value_json FROM workspace_collaboration_database_cells "
                    "WHERE row_id='row-target' AND property_id='property-back'"
                ).fetchone()[0]) == ['row-e1']
        else:
            assert row["cells"]["property-date"] == "2026-09-15"
        with closing(sqlite3.connect(store.db_path)) as con:
            assert con.execute(
                "SELECT COUNT(*) FROM workspace_collaboration_operations "
                "WHERE action=?",
                (RETURN_ACTION,)).fetchone()[0] == 1
        assert not errors, errors
    finally:
        for worker in started:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=10)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=10)
