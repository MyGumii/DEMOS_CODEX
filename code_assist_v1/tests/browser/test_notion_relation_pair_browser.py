"""UI-R5: cross-page relation pairing in an isolated 2424 browser app."""
from __future__ import annotations

import os

import pytest

from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.page_creation import create_page
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app,
)


def _command(authority, document_id, database_id, kind, target_id, payload, operation_id):
    page = authority.read_document("owner", document_id)
    common = dict(command_kind=kind, target_id=target_id, payload=payload,
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        expected_target_version=0, operation_id=operation_id)
    if kind == "database.cell.set":
        return authority.execute_typed_command("owner", document_id, **common)
    if kind == "database.row.create":
        return authority.execute_surface_command(
            "owner", document_id, database_id=database_id, **common)
    return authority.execute_schema_command(
        "owner", document_id, database_id=database_id, **common)


def _seed_database(authority, document_id, database_id, title, row_id):
    title_id = database_id + "-title"
    _command(authority, document_id, database_id, "database.create",
        database_id, {"title": title}, database_id + "-create")
    _command(authority, document_id, database_id, "database.property.create",
        title_id, {"name": "이름", "type": "title", "config": {}},
        database_id + "-title-create")
    _command(authority, document_id, database_id, "database.view.create",
        database_id + "-view", {"name": "표", "type": "table", "config": {}},
        database_id + "-view-create")
    _command(authority, document_id, database_id, "database.row.create",
        row_id, {}, database_id + "-row-create")
    _command(authority, document_id, database_id, "database.cell.set",
        row_id + ":" + title_id,
        {"row_id": row_id, "property_id": title_id, "value": title},
        database_id + "-row-title")
    page = authority.read_document("owner", document_id)
    authority.execute_typed_command(
        "owner", document_id, command_kind="block.insert",
        target_id=database_id,
        payload={"type": "table", "content": title,
                 "props": {"database_id": database_id}, "after_block_id": None},
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        operation_id=database_id + "-block-insert")


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_cross_page_relation_pair_restart_retry_disable(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        pytest.skip("exact selected gateway runtime is unavailable")

    with _isolated_collaborative_app(
            tmp_path, locked=False, owned_database_copy=True,
            database_activity=True, owned_database_trash=True,
            relation_pair=True) as (origin, password, authority, workspace_id, source_id):
        store = CollaborativeWorkspaceStore(
            authority.catalog_path, user_data_root=authority.user_data_root,
            identity_directory=authority.identity_directory)
        target = create_page(authority, "owner", workspace_id,
            title="PAIR-TARGET-PAGE", parent_id="",
            base_version=store.get_workspace("owner", workspace_id)["version"],
            operation_id="pair-browser-target-page")
        target_id = target["page"]["id"]
        source_database, target_database = "pair-browser-source-db", "pair-browser-target-db"
        source_row, target_row = "pair-browser-source-row", "pair-browser-target-row"
        _seed_database(authority, source_id, source_database, "PAIR-SOURCE", source_row)
        _seed_database(authority, target_id, target_database, "PAIR-TARGET", target_row)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1280, "height": 800})
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + source_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' "
                    "&& State.navigation.collaborativeDatabaseRelationPair "
                    "&& State.liveCollaboration.crdtReady", timeout=30_000)
                source_block = page.locator(f'[data-block-id="{source_database}"]')
                expect(source_block.locator("[data-db-add-property]")).to_be_visible(timeout=30_000)
                source_block.locator("[data-db-add-property]").click()
                page.locator("#dbPropertyName").fill("Related")
                page.locator("#dbPropertyType").select_option("relation")
                page.locator("#dbRelationTargetSearch").fill("PAIR-TARGET")
                page.locator("#dbRelationTargetResults button").filter(
                    has_text="PAIR-TARGET").click()
                expect(page.locator("#dbPropertyRelationDatabase")).to_have_value(target_database)
                page.locator("#modalActions button.primary").click()
                page.wait_for_function("""({database, target}) => {
                  const item = State.pageData?.databases?.find(db => db.id === database);
                  return item?.properties?.some(property => property.type === 'relation'
                    && property.config?.target_database_id === target)
                    && !State.databaseSync.inFlight && !State.databaseSync.timer;
                }""", arg={"database": source_database, "target": target_database}, timeout=30_000)
                relation_id = page.evaluate("""database => State.pageData.databases.find(
                  item => item.id === database).properties.find(item => item.type === 'relation').id""",
                  source_database)
                relation_cell = source_block.locator(f'[data-db-field="{relation_id}"]')
                expect(relation_cell.locator(f'option[value="{target_row}"]')).to_have_count(
                    1, timeout=30_000)
                relation_cell.select_option(target_row)
                page.wait_for_function("""({database, row, property, target}) =>
                  State.pageData?.databases?.find(item => item.id === database)?.rows
                    ?.find(item => item.id === row)?.cells?.[property]?.includes(target)
                """, arg={"database": source_database, "row": source_row,
                       "property": relation_id, "target": target_row}, timeout=30_000)

                source_block.locator(f'[data-db-property="{relation_id}"]').click()
                page.locator("#modalActions button").filter(has_text="양방향 관계 설정").click()
                page.locator("#dbRelationPairName").fill("Inverse")
                submitted = []

                def lose_first_reply(route):
                    body = route.request.post_data_json
                    if body.get("command_kind") != "database.relation_pair.enable":
                        route.continue_()
                        return
                    submitted.append(body)
                    if len(submitted) == 1:
                        response = route.fetch()
                        assert response.status == 201, response.text()
                        route.abort("failed")
                    else:
                        route.continue_()

                page.route("**/databases/commands", lose_first_reply)
                page.locator("#modalActions button.primary").click()
                expect(page.locator("#dbRelationPairStatus")).to_contain_text(
                    "다시 확인", timeout=30_000)
                saved_session = page.context.storage_state()
                browser.close()
                browser = playwright.chromium.launch(channel="chrome", headless=True)
                context = browser.new_context(storage_state=saved_session,
                    locale="ko-KR", viewport={"width": 1280, "height": 800})
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/databases/commands", lose_first_reply)
                page.goto(origin + "/page/" + source_id, wait_until="domcontentloaded")
                page.wait_for_function("() => typeof State !== 'undefined' "
                    "&& State.navigation.collaborativeDatabaseRelationPair "
                    "&& State.liveCollaboration.crdtReady", timeout=30_000)
                expect(page.locator("#databaseRelationPairStatus button")).to_be_visible(
                    timeout=30_000)
                page.locator("#databaseRelationPairStatus button").click()
                expect(page.locator("#databaseRelationPairStatus")).to_be_hidden(
                    timeout=30_000)
                assert len(submitted) == 2 and submitted[0] == submitted[1]
                source_block = page.locator(f'[data-block-id="{source_database}"]')
                inverse = next(item for item in authority.read_database(
                    "owner", target_database)["properties"] if item["name"] == "Inverse")
                assert inverse["config"]["reciprocal_property_id"] == relation_id
                target_cells = authority.read_database("owner", target_database)["rows"][0]["cells"]
                assert target_cells[inverse["id"]] == [source_row]

                source_block.locator(f'[data-db-property="{relation_id}"]').click()
                page.locator("#modalActions button").filter(has_text="양방향 관계 해제").click()
                page.locator("#modalActions button.primary").click()
                expect(page.locator("#modalBg")).to_be_hidden(timeout=30_000)
                assert all(item["id"] != inverse["id"] for item in authority.read_database(
                    "owner", target_database)["properties"])
                source_cells = authority.read_database("owner", source_database)["rows"][0]["cells"]
                assert source_cells[relation_id] == [target_row]
                assert not errors, errors
            finally:
                browser.close()
