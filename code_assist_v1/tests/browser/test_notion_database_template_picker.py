"""A saved database template is available when creating an owned item."""

import os
import json
import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app,
)
from code_assist_v1.tests.notion_collaboration.test_workspace_images import png


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
def test_template_editor_two_tabs_refuses_stale_autosave(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(
        tmp_path, locked=False, owned_database_copy=True, database_activity=True,
        owned_database_trash=True, relation_pair=True, custom_emoji=True,
        item_page_layout=True,
    ) as (origin, password, authority, _workspace_id, page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                context = browser.new_context(locale='ko-KR', viewport={'width': 1280, 'height': 800})
                first = context.new_page()
                second = context.new_page()
                errors = []
                for page in (first, second):
                    page.on('pageerror', lambda error: errors.append(str(error)))
                first.goto(origin + '/page/' + page_id, wait_until='domcontentloaded')
                first.locator('#userId').fill('owner')
                first.locator('#password').fill(password)
                first.locator('#submitBtn').click()
                expect(first.locator('#pageTitle')).to_be_enabled(timeout=30_000)
                first.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration?.crdtReady", timeout=30_000)
                database_id = first.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return State.pageData.blocks.find(block => block.type === 'table')?.id || '';
                }""")
                first.wait_for_function("""id => State.pageData.blocks.some(block => block.id === id)
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive
                  && !collaborativeDatabaseSyncPending()""", arg=database_id, timeout=30_000)
                document = authority.read_document('owner', page_id)
                authority.execute_schema_command(
                    'owner', page_id, database_id=database_id,
                    command_kind='database.template.create', target_id='two-tab-template',
                    payload={'name': '동시 수정', 'body': [], 'default_values': {}},
                    expected_document_sequence=document['document_sequence'],
                    expected_workspace_epoch=document['workspace_epoch'],
                    expected_document_epoch=document['document_epoch'],
                    expected_target_version=0, operation_id='two-tab-template-create',
                )
                first.reload(wait_until='domcontentloaded')
                second.goto(origin + '/page/' + page_id, wait_until='domcontentloaded')
                for page in (first, second):
                    expect(page.locator(f'[data-block-id="{database_id}"] [data-db-template-picker]')).to_be_visible(timeout=30_000)
                    root = page.locator(f'[data-block-id="{database_id}"]')
                    root.locator('[data-db-template-picker]').click()
                    root.locator('[data-db-template-more="two-tab-template"]').click()
                    root.locator('[data-db-template-edit="two-tab-template"]').click()
                    expect(page.locator('#dbTemplateName')).to_have_value('동시 수정')

                first.locator('#dbTemplateName').fill('첫 탭 저장')
                expect(first.locator('#dbTemplateSaveState')).to_have_text('모든 변경 사항 저장됨', timeout=30_000)
                assert authority.read_database('owner', database_id)['templates'][0]['name'] == '첫 탭 저장'
                second.locator('#dbTemplateName').fill('둘째 탭 입력')
                expect(second.locator('#dbTemplateError')).to_have_text(
                    '템플릿이 다른 곳에서 변경되었습니다. 창을 닫고 다시 열어 주세요.', timeout=30_000)
                assert authority.read_database('owner', database_id)['templates'][0]['name'] == '첫 탭 저장'
                second.locator('#modalClose').click()
                second.locator('[data-db-template-discard]').click()
                expect(second.locator('#modalBg')).to_be_hidden()
                root = second.locator(f'[data-block-id="{database_id}"]')
                root.locator('[data-db-template-picker]').click()
                root.locator('[data-db-template-more="two-tab-template"]').click()
                root.locator('[data-db-template-edit="two-tab-template"]').click()
                expect(second.locator('#dbTemplateName')).to_have_value('첫 탭 저장')
                assert not errors
            finally:
                browser.close()


@pytest.mark.browser
@pytest.mark.integration
def test_saved_template_creates_item_with_defaults(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(
        tmp_path, locked=False, owned_database_copy=True, database_activity=True,
        owned_database_trash=True, relation_pair=True, custom_emoji=True,
        item_page_layout=True,
    ) as (
        origin, password, authority, _workspace_id, page_id,
    ):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1440, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration?.crdtReady", timeout=30_000)
                block_id = page.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return State.pageData.blocks.find(block => block.type === 'table')?.id || '';
                }""")
                page.wait_for_function("""id => State.pageData.blocks.some(block => block.id === id)
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""", arg=block_id, timeout=30_000)
                page.reload(wait_until="domcontentloaded")
                add = page.locator(f'[data-block-id="{block_id}"] [data-database-add]')
                expect(add).to_be_visible(timeout=30_000)
                add.click()
                page.wait_for_function("""id => State.pageData?.databases?.find(database =>
                  database.id === id)?.rows?.length === 1 && !State.dirty
                  && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""", arg=block_id, timeout=30_000)
                empty_picker = page.locator(
                    f'[data-block-id="{block_id}"] [data-db-template-picker]')
                expect(empty_picker).to_be_visible()
                empty_picker.click()
                empty_menu = page.locator(
                    f'[data-block-id="{block_id}"] [data-db-template-popover]')
                expect(empty_menu.locator('[data-db-template-create]')).to_be_visible()
                assert empty_menu.locator('[data-db-template-option=""]').count() == 0
                assert "재사용 가능한 페이지 템플릿을 생성합니다" in empty_menu.inner_text()
                assert 350 <= empty_menu.bounding_box()['width'] <= 362
                assert empty_menu.locator('.database-template-help').get_attribute('href') \
                    == 'https://www.notion.com/ko/help/database-templates'
                assert page.evaluate("document.activeElement?.classList.contains('database-template-help')")
                if os.environ.get('NOTION_OWNED_EMPTY_TEMPLATE_MENU_SCREENSHOT'):
                    page.screenshot(path=os.environ['NOTION_OWNED_EMPTY_TEMPLATE_MENU_SCREENSHOT'])
                empty_picker.click()
                database = authority.read_database("owner", block_id)
                title_id = next(item["id"] for item in database["properties"] if item["type"] == "title")
                document = authority.read_document("owner", page_id)
                authority.execute_schema_command(
                    "owner", page_id, database_id=block_id,
                    command_kind="database.template.create", target_id="template-browser",
                    payload={"name": "시작 양식", "body": [
                        {"id": "template-paragraph", "type": "paragraph",
                         "content": "템플릿 본문", "props": {}},
                    ],
                             "default_values": {title_id: "템플릿으로 생성"}},
                    expected_document_sequence=document["document_sequence"],
                    expected_workspace_epoch=document["workspace_epoch"],
                    expected_document_epoch=document["document_epoch"],
                    expected_target_version=0, operation_id="template-browser-create",
                )
                page.reload(wait_until="domcontentloaded")
                picker = page.locator(f'[data-block-id="{block_id}"] [data-db-template-picker]')
                expect(picker).to_be_visible(timeout=30_000)
                picker.click()
                popover = page.locator(f'[data-block-id="{block_id}"] [data-db-template-popover]')
                expect(popover).to_be_visible()
                screenshot = os.environ.get("NOTION_TEMPLATE_PICKER_SCREENSHOT")
                if screenshot:
                    page.screenshot(path=screenshot)
                popover.get_by_role("menuitem", name="시작 양식").click()
                page.wait_for_function("""({id,titleId}) => State.pageData?.databases?.find(database =>
                  database.id === id)?.rows?.length === 2
                  && State.pageData.databases.find(database => database.id === id)
                    .rows.some(row => row.cells?.[titleId] === '템플릿으로 생성')
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""",
                  arg={"id": block_id, "titleId": title_id}, timeout=30_000)
                rows = authority.read_database("owner", block_id)["rows"]
                assert len(rows) == 2
                templated_row = next(row for row in rows
                                     if row["cells"].get(title_id) == "템플릿으로 생성")
                assert templated_row["body"]["blocks"][0]["content"] == "템플릿 본문"
                assert templated_row["body"]["blocks"][0]["id"] != "template-paragraph"
                assert not errors
            finally:
                browser.close()


@pytest.mark.browser
@pytest.mark.integration
def test_default_template_is_used_by_new_button_and_empty_can_override_it(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(
        tmp_path, locked=False, owned_database_copy=True, database_activity=True,
        owned_database_trash=True, relation_pair=True, custom_emoji=True,
        item_page_layout=True, template_default=True,
    ) as (origin, password, authority, _workspace_id, page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1440, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration?.crdtReady", timeout=30_000)
                block_id = page.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return State.pageData.blocks.find(block => block.type === 'table')?.id || '';
                }""")
                page.wait_for_function("""id => State.pageData.blocks.some(block => block.id === id)
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""", arg=block_id, timeout=30_000)
                page.reload(wait_until="domcontentloaded")
                root = page.locator(f'[data-block-id="{block_id}"]')
                expect(root.locator('[data-database-add]')).to_be_visible(timeout=30_000)
                root.locator('[data-database-add]').click()
                page.wait_for_function("""id => State.pageData?.databases?.find(db => db.id === id)?.rows?.length === 1
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive
                  && PendingDatabaseItemCreates.size === 0""", arg=block_id, timeout=30_000)
                document = authority.read_document("owner", page_id)
                title_id = next(item["id"] for item in authority.read_database("owner", block_id)["properties"]
                                if item["type"] == "title")
                authority.execute_schema_command(
                    "owner", page_id, database_id=block_id,
                    command_kind="database.template.create", target_id="template-browser-default",
                    payload={"name": "기본 회의 양식", "body": [
                        {"id": "template-default-body", "type": "heading1",
                         "content": "기본 안건", "props": {}},
                    ], "default_values": {title_id: "기본 제목"}},
                    expected_document_sequence=document["document_sequence"],
                    expected_workspace_epoch=document["workspace_epoch"],
                    expected_document_epoch=document["document_epoch"],
                    expected_target_version=0, operation_id="template-browser-default-create",
                )
                page.reload(wait_until="domcontentloaded")
                root = page.locator(f'[data-block-id="{block_id}"]')
                picker = root.locator('[data-db-template-picker]')
                expect(picker).to_be_visible(timeout=30_000)
                picker.click()
                root.locator('[data-db-template-more="template-browser-default"]').click()
                root.locator('[data-db-template-set-default="template-browser-default"]').focus()
                page.evaluate('renderBlocks()')
                expect(root.locator('[data-db-template-popover]')).to_be_visible()
                assert page.evaluate("() => document.activeElement?.dataset.dbTemplateSetDefault") == "template-browser-default"
                root.locator('[data-db-template-set-default="template-browser-default"]').click()
                page.wait_for_function("""id => State.pageData?.databases?.find(db => db.id === id)
                  ?.default_template?.template_id === 'template-browser-default'""",
                  arg=block_id, timeout=30_000)
                assert authority.read_database("owner", block_id)["default_template"]["template_id"] == "template-browser-default"
                page.set_viewport_size({"width": 390, "height": 844})
                picker.click()
                root.locator('[data-db-template-more="template-browser-default"]').click()
                expect(root.locator('[data-db-template-actions]')).to_be_visible()
                assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1")
                root.locator('[data-db-template-back]').click()
                expect(root.locator('[data-db-template-option=""]')).to_be_visible()
                root.locator('[data-db-template-popover]').evaluate('(node) => node.hidePopover()')
                page.set_viewport_size({"width": 1440, "height": 900})
                screenshot = os.environ.get("NOTION_DEFAULT_TEMPLATE_SCREENSHOT")
                if screenshot:
                    picker.click()
                    page.screenshot(path=screenshot)
                    root.locator('[data-db-template-popover]').evaluate('(node) => node.hidePopover()')
                actions_screenshot = os.environ.get("NOTION_TEMPLATE_ACTIONS_SCREENSHOT")
                if actions_screenshot:
                    picker.click()
                    root.locator('[data-db-template-more="template-browser-default"]').click()
                    page.screenshot(path=actions_screenshot)
                    root.locator('[data-db-template-popover]').evaluate('(node) => node.hidePopover()')
                root.locator('[data-database-add]').click()
                page.wait_for_function("""id => State.pageData?.databases?.find(db => db.id === id)?.rows?.length === 2
                  && PendingDatabaseItemCreates.size === 0 && !State.dirty""",
                  arg=block_id, timeout=30_000)
                expect(page.locator('#status')).to_have_text('항목이 생성되었습니다.', timeout=30_000)
                rows = authority.read_database("owner", block_id)["rows"]
                templated = next(item for item in rows if item["cells"].get(title_id) == "기본 제목")
                assert templated["body"]["blocks"][0]["content"] == "기본 안건"
                picker.click()
                root.locator('[data-db-template-option=""]').click()
                page.wait_for_function("""id => State.pageData?.databases?.find(db => db.id === id)?.rows?.length === 3
                  && PendingDatabaseItemCreates.size === 0 && !State.dirty""",
                  arg=block_id, timeout=30_000)
                expect(page.locator('#status')).to_have_text('항목이 생성되었습니다.', timeout=30_000)
                rows = authority.read_database("owner", block_id)["rows"]
                assert len([item for item in rows if item.get("body", {}).get("blocks")]) == 1
                picker.click()
                root.locator('[data-db-template-more=""]').click()
                root.locator('[data-db-template-set-default=""]').click()
                page.wait_for_function("""id => State.pageData?.databases?.find(db => db.id === id)
                  ?.default_template?.template_id === null
                  && State.pageData.databases.find(db => db.id === id).default_template.version === 2""",
                  arg=block_id, timeout=30_000)
                assert not errors
            finally:
                browser.close()


@pytest.mark.browser
@pytest.mark.integration
def test_template_ui_creates_template_and_applies_it_to_empty_item(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(
        tmp_path, locked=False, owned_database_copy=True, database_activity=True,
        owned_database_trash=True, relation_pair=True, custom_emoji=True,
        item_page_layout=True,
    ) as (origin, password, authority, _workspace_id, page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1440, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration?.crdtReady", timeout=30_000)
                block_id = page.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return State.pageData.blocks.find(block => block.type === 'table')?.id || '';
                }""")
                page.wait_for_function("""id => State.pageData.blocks.some(block => block.id === id)
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""", arg=block_id, timeout=30_000)
                page.reload(wait_until="domcontentloaded")
                root = page.locator(f'[data-block-id="{block_id}"]')
                expect(root.locator('[data-database-add]')).to_be_visible(timeout=30_000)
                root.locator('[data-database-add]').click()
                page.wait_for_function("""id => {
                  const database=State.pageData?.databases?.find(item=>item.id===id);
                  return database?.rows?.length===1
                    && database?.properties?.some(property=>property.type==='title') && !State.dirty
                  && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive
                  && PendingDatabaseItemCreates.size === 0;
                }""",
                  arg=block_id, timeout=30_000)
                first_row = authority.read_database("owner", block_id)["rows"][0]
                title_id = next(item["id"] for item in authority.read_database("owner", block_id)["properties"]
                                if item["type"] == "title")
                source_page = authority.read_document('owner', page_id)
                authority.create_document(
                    'owner', _workspace_id, 'template-target-page', title='관계 대상', blocks=[],
                    expected_workspace_epoch=source_page['workspace_epoch'],
                    operation_id='ui-template-target-page-create')
                with authority._transaction() as con:
                    con.execute(
                        "INSERT INTO workspace_collaboration_databases("
                        "id,document_id,title,row_version,created_at,updated_at) "
                        "VALUES('template-target-db','template-target-page','관계 대상',1,1,1)")
                    con.execute(
                        "INSERT INTO workspace_collaboration_database_properties("
                        "id,database_id,name,property_type,position,config_json,"
                        "row_version,created_at,updated_at) VALUES("
                        "'template-target-title','template-target-db','이름','title',0,'{}',1,1,1)")
                    con.execute(
                        "INSERT INTO workspace_collaboration_database_rows("
                        "id,database_id,position,row_version,created_at,updated_at) "
                        "VALUES('template-target-row','template-target-db',0,1,1,1)")
                    con.execute(
                        "INSERT INTO workspace_collaboration_database_cells("
                        "row_id,property_id,value_json,row_version,updated_at) "
                        "VALUES('template-target-row','template-target-title','\"연결 대상\"',1,1)")
                document = authority.read_document("owner", page_id)
                authority.execute_schema_command(
                    "owner", page_id, database_id=block_id,
                    command_kind="database.property.create", target_id="property-ui-template-note",
                    payload={"name": "메모", "type": "text", "config": {}},
                    expected_document_sequence=document["document_sequence"],
                    expected_workspace_epoch=document["workspace_epoch"],
                    expected_document_epoch=document["document_epoch"],
                    expected_target_version=0, operation_id="ui-template-note-property",
                )
                document = authority.read_document("owner", page_id)
                authority.execute_schema_command(
                    "owner", page_id, database_id=block_id,
                    command_kind="database.property.create", target_id="property-ui-template-rel",
                    payload={"name": "관련 항목", "type": "relation",
                             "config": {"target_database_id": "template-target-db"}},
                    expected_document_sequence=document["document_sequence"],
                    expected_workspace_epoch=document["workspace_epoch"],
                    expected_document_epoch=document["document_epoch"],
                    expected_target_version=0, operation_id="ui-template-relation-property",
                )
                document = authority.read_document("owner", page_id)
                authority.execute_schema_command(
                    "owner", page_id, database_id=block_id,
                    command_kind="database.property.create", target_id="property-ui-template-files",
                    payload={"name": "첨부", "type": "files", "config": {}},
                    expected_document_sequence=document["document_sequence"],
                    expected_workspace_epoch=document["workspace_epoch"],
                    expected_document_epoch=document["document_epoch"],
                    expected_target_version=0, operation_id="ui-template-files-property",
                )
                page.reload(wait_until="domcontentloaded")
                root = page.locator(f'[data-block-id="{block_id}"]')
                root.locator('[data-db-template-picker]').click()
                root.locator('[data-db-template-create]').click()
                expect(page.locator('#dbTemplateName')).to_be_visible()
                created_id = next(item['id'] for item in authority.read_database('owner', block_id)['templates']
                                  if item['name'] == '새 페이지' and item['state'] == 'active')
                page.locator('#modalClose').click()
                expect(page.locator('#modalBg')).to_be_hidden()
                assert any(item['id'] == created_id for item in authority.read_database('owner', block_id)['templates'])
                root.locator('[data-db-template-picker]').click()
                root.locator(f'[data-db-template-more="{created_id}"]').click()
                root.locator(f'[data-db-template-edit="{created_id}"]').click()
                page.locator('#dbTemplateName').fill('회의 양식')
                page.locator('[data-db-template-default="property-ui-template-note"]').fill('검토 필요')
                relation = page.locator('[data-db-template-relation="property-ui-template-rel"]')
                relation.locator('[data-db-template-relation-toggle]').click()
                expect(relation.locator('[data-db-template-relation-menu]')).to_be_visible()
                relation.locator('input[type="checkbox"][value="template-target-row"]').check()
                assert relation.locator('[data-db-template-default]').input_value() == '["template-target-row"]'
                relation.locator('[data-db-template-relation-search]').press('Escape')
                expect(relation.locator('[data-db-template-relation-menu]')).to_be_hidden()
                body_editor = page.locator('#dbTemplateBodyEditor')
                body_editor.locator('[data-body-text]').first.fill('회의 안건')
                body_editor.locator('[data-body-text]').first.press('Enter')
                body_editor.locator('[data-body-text]').nth(1).fill('결정 사항')
                if os.environ.get('NOTION_TEMPLATE_EDITOR_SCREENSHOT'):
                    page.locator('#modalBg').screenshot(path=os.environ['NOTION_TEMPLATE_EDITOR_SCREENSHOT'])
                page.set_viewport_size({"width": 390, "height": 844})
                expect(page.locator('#modalClose')).to_be_visible()
                expect(page.locator('#modalActions [data-db-template-retry]')).to_be_hidden()
                relation.locator('[data-db-template-relation-toggle]').click()
                relation.locator('[data-db-template-relation-search]').fill('연결 대상')
                expect(relation.locator('[data-db-template-relation-option]')).to_be_visible()
                relation.locator('[data-db-template-relation-search]').fill('없는 항목')
                expect(relation.locator('[data-db-template-relation-option]')).to_be_hidden()
                relation.locator('[data-db-template-relation-search]').press('Escape')
                assert page.evaluate("""() => {
                  const body=document.querySelector('#modalBody');
                  return body.scrollWidth <= body.clientWidth + 1;
                }""")
                page.set_viewport_size({"width": 1440, "height": 900})
                page.locator('#modalClose').click()
                expect(page.locator('#modalBg')).to_be_hidden(timeout=30_000)
                templates = authority.read_database("owner", block_id)["templates"]
                created = next(item for item in templates if item["id"] == created_id)
                assert created['name'] == '회의 양식'
                assert created["default_values"]["property-ui-template-note"] == "검토 필요"
                assert created["default_values"]["property-ui-template-rel"] == ['template-target-row']
                assert [item["content"] for item in created["body"]] == ['회의 안건', '결정 사항']

                root.locator('[data-table-row="0"]').first.click()
                chooser = page.locator('#linkedItemPeek [data-db-empty-template]')
                expect(chooser).to_be_visible(timeout=30_000)
                if os.environ.get('NOTION_EMPTY_TEMPLATE_SCREENSHOT'):
                    page.locator('#linkedItemPeek').screenshot(path=os.environ['NOTION_EMPTY_TEMPLATE_SCREENSHOT'])
                chooser.get_by_role('button', name='회의 양식').click()
                page.wait_for_function("""({id,rowId}) => State.pageData?.databases?.find(db =>
                  db.id === id)?.rows?.find(row => row.id === rowId)?.body?.blocks?.length === 2""",
                  arg={"id": block_id, "rowId": first_row["id"]}, timeout=30_000)
                updated = next(item for item in authority.read_database("owner", block_id)["rows"]
                               if item["id"] == first_row["id"])
                assert updated["cells"][title_id] == first_row["cells"][title_id]
                assert updated["cells"]["property-ui-template-note"] == "검토 필요"
                assert updated["cells"]["property-ui-template-rel"] == ['template-target-row']
                assert [item["content"] for item in updated["body"]["blocks"]] == ['회의 안건', '결정 사항']
                expect(chooser).to_be_hidden()
                page.locator('#linkedItemPeek [data-linked-item-close]').click()
                root.locator('[data-db-template-picker]').click()
                root.locator(f'[data-db-template-more="{created["id"]}"]').click()
                page.evaluate('renderBlocks()')
                expect(root.locator('[data-db-template-actions]')).to_be_visible()
                root.locator(f'[data-db-template-edit="{created["id"]}"]').click()
                expect(page.locator('#dbTemplateName')).to_be_visible()
                page.locator('#dbTemplateName').fill('수정된 회의 양식')
                page.locator('#dbTemplateBodyEditor [data-body-text]').first.fill('/제목 1')
                page.locator('#dbTemplateBodyEditor [data-body-type="heading1"]').click()
                page.locator('#dbTemplateBodyEditor [data-body-text]').first.fill('새 회의 안건')
                assert page.locator('#modalActions button:visible').all_text_contents() == []
                page.locator('#modalClose').click()
                expect(page.locator('#modalBg')).to_be_hidden(timeout=30_000)
                edited = next(item for item in authority.read_database("owner", block_id)["templates"]
                              if item["id"] == created["id"])
                assert edited["name"] == '수정된 회의 양식'
                assert [item["content"] for item in edited["body"]] == ['새 회의 안건', '결정 사항']
                assert edited["body"][0]["type"] == 'heading1'
                root.locator('[data-db-template-picker]').click()
                root.locator(f'[data-db-template-option="{created["id"]}"]').click()
                page.wait_for_function("""id => State.pageData?.databases?.find(db =>
                  db.id === id)?.rows?.length === 2 && !State.dirty
                  && PendingDatabaseItemCreates.size === 0""", arg=block_id, timeout=30_000)
                second_row = next(item for item in authority.read_database("owner", block_id)["rows"]
                                  if item["id"] != first_row["id"])
                assert second_row["body"]["blocks"][0]["type"] == 'heading1'
                assert second_row["body"]["blocks"][0]["content"] == '새 회의 안건'
                assert second_row["cells"]["property-ui-template-rel"] == ['template-target-row']
                page.reload(wait_until='domcontentloaded')
                expect(root.locator('[data-db-template-picker]')).to_be_visible(timeout=30_000)
                root.locator('[data-db-template-picker]').click()
                page.evaluate('renderBlocks()')
                expect(root.locator('[data-db-template-popover]')).to_be_visible()
                root.locator(f'[data-db-template-more="{created["id"]}"]').click()
                root.locator(f'[data-db-template-duplicate="{created["id"]}"]').click()
                page.wait_for_function("""id => State.pageData?.databases?.find(db => db.id === id)
                  ?.templates?.some(item => item.name === '수정된 회의 양식 사본' && item.state === 'active')""",
                  arg=block_id, timeout=30_000)
                copied = next(item for item in authority.read_database("owner", block_id)["templates"]
                              if item["name"] == '수정된 회의 양식 사본')
                assert copied["body"] == edited["body"]
                root.locator('[data-db-template-picker]').click()
                root.locator(f'[data-db-template-more="{copied["id"]}"]').click()
                root.locator(f'[data-db-template-delete="{copied["id"]}"]').click()
                expect(page.locator('#modalTitle')).to_have_text('템플릿 삭제')
                page.locator('#modalActions').get_by_role('button', name='삭제').click()
                expect(page.locator('#modalBg')).to_be_hidden(timeout=30_000)
                archived = next(item for item in authority.read_database("owner", block_id)["templates"]
                                if item["id"] == copied["id"])
                assert archived["state"] == 'archived'
                root.locator('[data-db-template-picker]').click()
                root.locator(f'[data-db-template-more="{created["id"]}"]').click()
                expect(root.locator('[data-db-template-actions]')).to_be_visible()
                root.locator(f'[data-db-template-edit="{created["id"]}"]').click()
                expect(page.locator('#dbTemplateName')).to_be_visible(timeout=30_000)
                document = authority.read_document('owner', page_id)
                latest = next(item for item in authority.read_database('owner', block_id)['templates']
                              if item['id'] == created['id'])
                authority.execute_schema_command(
                    'owner', page_id, database_id=block_id,
                    command_kind='database.template.update', target_id=created['id'],
                    payload={'name': '다른 곳에서 변경', 'body': latest['body'],
                             'default_values': latest['default_values']},
                    expected_document_sequence=document['document_sequence'],
                    expected_workspace_epoch=document['workspace_epoch'],
                    expected_document_epoch=document['document_epoch'],
                    expected_target_version=latest['version'],
                    operation_id='ui-template-remote-change',
                )
                page.locator('#dbTemplateName').fill('오래된 화면의 입력')
                expect(page.locator('#modalActions [data-db-template-retry]')).to_be_hidden()
                expect(page.locator('#dbTemplateError')).to_have_text(
                    '템플릿이 다른 곳에서 변경되었습니다. 창을 닫고 다시 열어 주세요.', timeout=30_000)
                assert next(item for item in authority.read_database('owner', block_id)['templates']
                            if item['id'] == created['id'])['name'] == '다른 곳에서 변경'
                page.locator('#modalClose').click()
                page.locator('[data-db-template-discard]').click()
                expect(page.locator('#modalBg')).to_be_hidden()

                root.locator('[data-db-template-picker]').click()
                root.locator(f'[data-db-template-more="{created["id"]}"]').click()
                root.locator(f'[data-db-template-edit="{created["id"]}"]').click()
                editor = page.locator('#dbTemplateBodyEditor')
                editor.locator('[data-body-add]').first.click()
                editor.locator('[data-body-type="bookmark"]').click()
                resource = editor.locator('.item-resource-picker')
                expect(resource).to_be_visible()
                resource.locator('input[type="url"]').fill('https://example.com/template-reference')
                resource.locator('[data-resource-insert]').click()
                expect(editor.locator('[data-resource-kind="bookmark"] .item-resource-link')).to_be_visible()
                editor.locator('[data-type="bookmark"] [data-body-add]').click()
                editor.locator('[data-body-type="image"]').click()
                expect(resource).to_be_visible()
                resource.locator('input[type="file"]').set_input_files({
                    'name': 'template.png', 'mimeType': 'image/png', 'buffer': png(),
                })
                expect(editor.locator('[data-resource-kind="image"] img')).to_be_visible(timeout=30_000)
                file_default = page.locator('[data-db-template-files="property-ui-template-files"]')
                file_default.locator('[data-db-template-file-input]').set_input_files({
                    'name': 'template-attachment.png', 'mimeType': 'image/png', 'buffer': png(),
                })
                expect(file_default.locator('.database-template-file')).to_have_count(1, timeout=30_000)
                file_default.locator('[data-db-template-file-link-toggle]').click()
                file_default.locator('[data-db-template-file-link-url]').fill('https://example.com/brief.pdf')
                file_default.locator('[data-db-template-file-link-name]').fill('Brief')
                file_default.locator('[data-db-template-file-link-apply]').click()
                expect(file_default.locator('.database-template-file')).to_have_count(2)
                assert len(json.loads(file_default.locator('[data-db-template-default]').input_value())) == 2
                file_default.locator('[data-db-template-file-drag="1"]').press('Alt+ArrowLeft')
                assert [item['name'] for item in json.loads(
                    file_default.locator('[data-db-template-default]').input_value())] == [
                        'Brief', 'template-attachment.png']
                page.set_viewport_size({"width": 390, "height": 844})
                expect(file_default.locator('.database-template-file')).to_have_count(2)
                assert page.evaluate("""() => {
                  const body = document.querySelector('#modalBody');
                  return body.scrollWidth <= body.clientWidth + 1;
                }""")
                if os.environ.get('NOTION_TEMPLATE_FILES_MOBILE_SCREENSHOT'):
                    page.screenshot(path=os.environ['NOTION_TEMPLATE_FILES_MOBILE_SCREENSHOT'])
                page.set_viewport_size({"width": 1440, "height": 900})
                if os.environ.get('NOTION_TEMPLATE_RESOURCE_SCREENSHOT'):
                    page.screenshot(path=os.environ['NOTION_TEMPLATE_RESOURCE_SCREENSHOT'])
                page.locator('#modalClose').click()
                expect(page.locator('#modalBg')).to_be_hidden(timeout=30_000)
                media_template = next(item for item in authority.read_database('owner', block_id)['templates']
                                      if item['id'] == created['id'])
                bookmark = next(item for item in media_template['body'] if item['type'] == 'bookmark')
                image = next(item for item in media_template['body'] if item['type'] == 'image')
                assert bookmark['props']['url'] == 'https://example.com/template-reference'
                assert image['props']['file_id']
                attached = media_template['default_values']['property-ui-template-files']
                assert len(attached) == 2 and attached[0] == {
                    'url': 'https://example.com/brief.pdf', 'name': 'Brief'}
                assert attached[1]['name'] == 'template-attachment.png'
                root.locator('[data-db-template-picker]').click()
                root.locator(f'[data-db-template-option="{created["id"]}"]').click()
                page.wait_for_function("""id => State.pageData?.databases?.find(db => db.id === id)?.rows?.length === 3
                  && PendingDatabaseItemCreates.size === 0""", arg=block_id, timeout=30_000)
                newest_row = authority.read_database('owner', block_id)['rows'][-1]
                assert next(item for item in newest_row['body']['blocks'] if item['type'] == 'bookmark')['props']['url'] == bookmark['props']['url']
                assert next(item for item in newest_row['body']['blocks'] if item['type'] == 'image')['props']['file_id'] == image['props']['file_id']
                assert newest_row['cells']['property-ui-template-files'] == attached
                expect(root.get_by_role('link', name='Brief')).to_have_attribute(
                    'href', 'https://example.com/brief.pdf')
                expect(root.get_by_role('link', name='template-attachment.png')).to_have_attribute(
                    'href', f'/api/notion/collaboration/files/{attached[1]["file_id"]}')
                link_toggle = root.locator('[data-db-row="2"][data-db-file-link-toggle="property-ui-template-files"]')
                file_cell = link_toggle.locator('..').locator('..')
                file_menu_toggle = file_cell.get_by_role('button', name='파일 추가')
                file_menu = file_cell.get_by_role('dialog', name='파일 추가')
                file_menu_toggle.click()
                expect(file_menu).to_be_visible()
                expect(file_menu.get_by_role('button', name='업로드')).to_be_focused()
                expect(file_menu.get_by_role('button', name='링크 임베드')).to_be_visible()
                page.evaluate('renderBlocks()')
                expect(file_menu).to_be_visible()
                expect(file_menu.get_by_role('button', name='업로드')).to_be_focused()
                if os.environ.get('NOTION_GRID_FILE_MENU_SCREENSHOT'):
                    page.screenshot(path=os.environ['NOTION_GRID_FILE_MENU_SCREENSHOT'])
                link_toggle.click()
                link_form = link_toggle.locator('..').locator('[data-db-file-link-form]')
                expect(link_form).to_be_visible()
                link_form.locator('[data-db-file-link-url]').fill('javascript:alert(1)')
                link_form.locator('[data-db-file-link-apply]').click()
                assert len(authority.read_database('owner', block_id)['rows'][-1]['cells']['property-ui-template-files']) == 2
                link_form.locator('[data-db-file-link-url]').focus()
                page.evaluate('renderBlocks()')
                expect(link_form).to_be_visible()
                expect(link_form.locator('[data-db-file-link-url]')).to_have_value('javascript:alert(1)')
                expect(link_form.locator('[data-db-file-link-url]')).to_be_focused()
                expect(file_menu).to_be_visible()
                link_form.locator('[data-db-file-link-url]').fill('https://example.com/agenda.pdf')
                link_form.locator('[data-db-file-link-name]').fill('Agenda')
                page.evaluate('renderBlocks()')
                expect(link_form).to_be_visible()
                expect(link_form.locator('[data-db-file-link-name]')).to_have_value('Agenda')
                link_form.locator('[data-db-file-link-apply]').click()
                expect(root.get_by_role('link', name='Agenda')).to_have_attribute(
                    'href', 'https://example.com/agenda.pdf', timeout=30_000)
                expect(file_menu).to_be_hidden()
                assert authority.read_database('owner', block_id)['rows'][-1]['cells'][
                    'property-ui-template-files'][-1] == {
                        'url': 'https://example.com/agenda.pdf', 'name': 'Agenda'}
                page.set_viewport_size({'width':390,'height':700})
                file_menu_toggle.click()
                bounds = file_menu.bounding_box()
                assert bounds and bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 390
                page.keyboard.press('Escape')
                expect(file_menu).to_be_hidden()
                expect(file_menu_toggle).to_be_focused()
                page.set_viewport_size({'width':1440,'height':900})
                page.evaluate("document.documentElement.dataset.theme='dark'")
                file_menu_toggle.click()
                expect(file_menu).to_be_visible()
                assert file_menu.evaluate('(menu) => getComputedStyle(menu).backgroundColor') == 'rgb(25, 25, 25)'
                page.keyboard.press('Escape')
                page.evaluate("document.documentElement.dataset.theme='light'")
                uploaded_chip = root.locator('[data-db-file-target="property-ui-template-files"]').filter(
                    has_text='template-attachment.png').first
                uploaded_chip.get_by_role('button', name='template-attachment.png 메뉴').click()
                uploaded_actions = uploaded_chip.get_by_role('menu', name='template-attachment.png 파일 작업')
                expect(uploaded_actions).to_be_visible()
                expect(uploaded_actions.get_by_role('menuitem', name='원본 보기')).to_have_attribute(
                    'href', f'/api/notion/collaboration/files/{attached[1]["file_id"]}')
                expect(uploaded_actions.get_by_role('menuitem', name='다운로드')).to_have_attribute(
                    'download', 'template-attachment.png')
                uploaded_actions.get_by_role('menuitem', name='전체 화면').click()
                expect(page.locator('#modalBody .database-file-fullscreen img')).to_have_attribute(
                    'src', f'/api/notion/collaboration/files/{attached[1]["file_id"]}')
                page.locator('#modalClose').click()
                external_chip = root.locator('[data-db-file-target="property-ui-template-files"]').filter(
                    has_text='Brief').first
                external_chip.get_by_role('button', name='Brief 메뉴').click()
                external_actions = external_chip.get_by_role('menu', name='Brief 파일 작업')
                expect(external_actions.get_by_role('menuitem', name='원본 보기')).to_have_attribute(
                    'href', 'https://example.com/brief.pdf')
                expect(external_actions.get_by_role('menuitem', name='다운로드')).to_have_count(0)
                page.keyboard.press('Escape')
                expect(external_chip.get_by_role('button', name='Brief 메뉴')).to_be_focused()
                row_files = root.locator('[data-db-row="2"][data-db-file-target="property-ui-template-files"]')
                root.locator('[data-db-row="2"][data-db-file-drag="property-ui-template-files"][data-file-index="2"]').drag_to(
                    row_files.nth(0))
                page.wait_for_function("""id => State.pageData?.databases?.find(db => db.id === id)
                  ?.rows?.at(-1)?.cells?.['property-ui-template-files']?.[0]?.name === 'Agenda'""",
                  arg=block_id, timeout=30_000)
                assert [item['name'] for item in authority.read_database('owner', block_id)['rows'][-1]
                        ['cells']['property-ui-template-files']] == [
                            'Agenda', 'Brief', 'template-attachment.png']

                root.locator('[data-table-row="2"]').first.click()
                peek = page.locator('#linkedItemPeek')
                expect(peek).to_be_visible(timeout=30_000)
                peek_files = peek.locator('[data-linked-item-files="property-ui-template-files"]')
                expect(peek_files.get_by_role('link', name='Brief')).to_have_attribute(
                    'href', 'https://example.com/brief.pdf')
                expect(peek_files.get_by_role('link', name='Agenda')).to_have_attribute(
                    'href', 'https://example.com/agenda.pdf')
                expect(peek_files.get_by_role('link', name='template-attachment.png')).to_have_attribute(
                    'href', f'/api/notion/collaboration/files/{attached[1]["file_id"]}')
                peek_file = peek_files.locator('[data-linked-item-file-target]').filter(
                    has_text='template-attachment.png')
                peek_file.get_by_role('button', name='template-attachment.png 메뉴').click()
                peek_actions = peek_file.get_by_role('menu', name='template-attachment.png 파일 작업')
                expect(peek_actions.get_by_role('menuitem', name='다운로드')).to_have_attribute(
                    'download', 'template-attachment.png')
                peek_actions.get_by_role('menuitem', name='전체 화면').click()
                expect(page.locator('#modalBody .database-file-fullscreen img')).to_be_visible()
                page.locator('#modalClose').click()
                peek_files.get_by_role('button', name='파일 추가').click()
                peek_add_menu = peek_files.get_by_role('dialog', name='파일 추가')
                expect(peek_add_menu).to_be_visible()
                expect(peek_add_menu.get_by_role('button', name='업로드')).to_be_focused()
                expect(peek_add_menu.get_by_role('button', name='링크 임베드')).to_be_visible()
                peek_files.locator('[data-linked-item-file-toggle]').click()
                peek_form = peek_files.locator('[data-linked-item-file-form]')
                expect(peek_form).to_be_visible()
                peek_form.locator('[data-linked-item-file-url]').fill('https://example.com/minutes.pdf')
                peek_form.locator('[data-linked-item-file-name]').fill('Minutes')
                page.evaluate('renderLinkedItemPeek(LinkedDatabaseItems.snapshot())')
                expect(peek_form).to_be_visible()
                expect(peek_add_menu).to_be_visible()
                expect(peek_form.locator('[data-linked-item-file-name]')).to_have_value('Minutes')
                expect(peek_form.locator('[data-linked-item-file-name]')).to_be_focused()
                peek_form.locator('[data-linked-item-file-apply]').click()
                expect(peek_files.get_by_role('link', name='Minutes')).to_have_attribute(
                    'href', 'https://example.com/minutes.pdf', timeout=30_000)
                expect(peek_add_menu).to_be_hidden()
                page.wait_for_function("""id => State.pageData?.databases?.find(db => db.id === id)
                  ?.rows?.at(-1)?.cells?.['property-ui-template-files']?.at(-1)?.name === 'Minutes'
                  && !LinkedDatabaseItems.pending()""", arg=block_id, timeout=30_000)
                assert authority.read_database('owner', block_id)['rows'][-1]['cells'][
                    'property-ui-template-files'][-1] == {
                        'url': 'https://example.com/minutes.pdf', 'name': 'Minutes'}
                peek_files.locator('[data-linked-item-file-drag="3"]').press('Alt+ArrowLeft')
                page.wait_for_function("""id => State.pageData?.databases?.find(db => db.id === id)
                  ?.rows?.at(-1)?.cells?.['property-ui-template-files']?.[2]?.name === 'Minutes'
                  && !LinkedDatabaseItems.pending()""", arg=block_id, timeout=30_000)
                assert [item['name'] for item in authority.read_database('owner', block_id)['rows'][-1]
                        ['cells']['property-ui-template-files']] == [
                            'Agenda', 'Brief', 'Minutes', 'template-attachment.png']
                peek_files.get_by_role('button', name='파일 추가').click()
                peek_files.locator('[data-linked-item-file-input]').set_input_files({
                    'name': 'meeting-notes.png', 'mimeType': 'image/png', 'buffer': png(),
                })
                expect(peek_files.get_by_role('link', name='meeting-notes.png')).to_be_visible(timeout=30_000)
                expect(peek_add_menu).to_be_hidden()
                page.wait_for_function("""id => State.pageData?.databases?.find(db => db.id === id)
                  ?.rows?.at(-1)?.cells?.['property-ui-template-files']?.at(-1)?.name === 'meeting-notes.png'
                  && !LinkedDatabaseItems.pending()""", arg=block_id, timeout=30_000)
                added_file = authority.read_database('owner', block_id)['rows'][-1]['cells'][
                    'property-ui-template-files'][-1]
                assert added_file['file_id'] and added_file['file_id'] != attached[1]['file_id']
                peek_files.get_by_role('button', name='파일 추가').click()
                expect(peek_add_menu).to_be_visible()
                if os.environ.get('NOTION_PEEK_FILE_MENU_SCREENSHOT'):
                    page.screenshot(path=os.environ['NOTION_PEEK_FILE_MENU_SCREENSHOT'])
                page.set_viewport_size({'width':390,'height':700})
                peek_bounds = peek_add_menu.bounding_box()
                assert peek_bounds and peek_bounds['x'] >= 0 and peek_bounds['x'] + peek_bounds['width'] <= 390
                page.keyboard.press('Escape')
                expect(peek_add_menu).to_be_hidden()
                expect(peek_files.get_by_role('button', name='파일 추가')).to_be_focused()
                page.set_viewport_size({'width':1440,'height':900})
                peek.locator('[data-linked-item-close]').click()
                page.reload(wait_until='domcontentloaded')
                expect(root.get_by_role('link', name='Minutes')).to_have_attribute(
                    'href', 'https://example.com/minutes.pdf', timeout=30_000)
                expect(root.get_by_role('link', name='meeting-notes.png')).to_have_attribute(
                    'href', f'/api/notion/collaboration/files/{added_file["file_id"]}')
                root.locator('[data-table-row="2"]').first.click()
                expect(peek_files.get_by_role('link', name='Minutes')).to_have_attribute(
                    'href', 'https://example.com/minutes.pdf', timeout=30_000)
                expect(peek_files.get_by_role('link', name='meeting-notes.png')).to_be_visible()
                expect(peek_files.locator('.database-file-chip > a')).to_have_text([
                    'Agenda', 'Brief', 'Minutes', 'template-attachment.png', 'meeting-notes.png'])
                peek.locator('[data-linked-item-close]').click()
                root.locator('[data-db-row="2"][data-db-file-input="property-ui-template-files"]').set_input_files({
                    'name': 'table-only.png', 'mimeType': 'image/png', 'buffer': png(),
                })
                expect(root.get_by_role('link', name='table-only.png')).to_be_visible(timeout=30_000)
                assert authority.read_database('owner', block_id)['rows'][-1]['cells'][
                    'property-ui-template-files'][-1]['name'] == 'table-only.png'
                table_file = root.locator('[data-db-row="2"][data-db-file-target="property-ui-template-files"]').filter(
                    has_text='table-only.png')
                table_file.get_by_role('button', name='table-only.png 메뉴').click()
                table_file.get_by_role('menuitem', name='삭제').click()
                page.wait_for_function("""id => !State.pageData?.databases?.find(db => db.id === id)
                  ?.rows?.at(-1)?.cells?.['property-ui-template-files']?.some(file => file.name === 'table-only.png')""",
                  arg=block_id, timeout=30_000)
                assert all(item['name'] != 'table-only.png' for item in authority.read_database(
                    'owner', block_id)['rows'][-1]['cells']['property-ui-template-files'])
                page.reload(wait_until='domcontentloaded')
                expect(root.get_by_role('link', name='meeting-notes.png')).to_be_visible(timeout=30_000)
                expect(root.get_by_role('link', name='table-only.png')).to_have_count(0)

                root.locator('[data-db-template-picker]').click()
                root.locator(f'[data-db-template-more="{created["id"]}"]').click()
                root.locator(f'[data-db-template-edit="{created["id"]}"]').click()
                page.locator('#dbTemplateName').fill('자동 저장 확인')
                page.wait_for_function("""({databaseId,templateId}) => State.pageData?.databases
                  ?.find(db => db.id === databaseId)?.templates?.find(item => item.id === templateId)
                  ?.name === '자동 저장 확인'""",
                  arg={'databaseId': block_id, 'templateId': created['id']}, timeout=30_000)
                expect(page.locator('#modalBg')).to_be_visible()
                expect(page.locator('#dbTemplateSaveState')).to_have_text('모든 변경 사항 저장됨')
                if os.environ.get('NOTION_TEMPLATE_AUTOSAVE_SCREENSHOT'):
                    page.screenshot(path=os.environ['NOTION_TEMPLATE_AUTOSAVE_SCREENSHOT'])
                page.locator('#dbTemplateName').evaluate("""node => {
                  node.dispatchEvent(new CompositionEvent('compositionstart', {bubbles:true, data:'조'}));
                  node.value='조합 완료';
                  node.dispatchEvent(new InputEvent('input', {bubbles:true, isComposing:true, data:'조'}));
                }""")
                page.wait_for_timeout(1200)
                assert next(item for item in authority.read_database('owner', block_id)['templates']
                            if item['id'] == created['id'])['name'] == '자동 저장 확인'
                page.locator('#dbTemplateName').evaluate("""node =>
                  node.dispatchEvent(new CompositionEvent('compositionend', {bubbles:true, data:'조합 완료'}))""")
                page.wait_for_function("""({databaseId,templateId}) => State.pageData?.databases
                  ?.find(db => db.id === databaseId)?.templates?.find(item => item.id === templateId)
                  ?.name === '조합 완료'""",
                  arg={'databaseId': block_id, 'templateId': created['id']}, timeout=30_000)
                page.locator('[data-db-template-default="property-ui-template-note"]').fill('자동 메모')
                page.locator('#modalClose').click()
                expect(page.locator('#modalBg')).to_be_hidden(timeout=30_000)
                auto_saved = next(item for item in authority.read_database('owner', block_id)['templates']
                                  if item['id'] == created['id'])
                assert auto_saved['name'] == '조합 완료'
                assert auto_saved['default_values']['property-ui-template-note'] == '자동 메모'
                root.locator('[data-db-template-picker]').click()
                root.locator(f'[data-db-template-more="{created["id"]}"]').click()
                root.locator(f'[data-db-template-edit="{created["id"]}"]').click()
                failed = {'once': True}

                def fail_template_update_once(route):
                    if (failed['once'] and route.request.post_data_json
                            .get('command_kind') == 'database.template.update'):
                        failed['once'] = False
                        return route.abort('failed')
                    return route.continue_()

                page.route('**/databases/commands', fail_template_update_once)
                page.locator('#dbTemplateName').fill('재시도 확인')
                retry = page.locator('#modalActions [data-db-template-retry]')
                expect(retry).to_be_visible(timeout=30_000)
                assert next(item for item in authority.read_database('owner', block_id)['templates']
                            if item['id'] == created['id'])['name'] == '조합 완료'
                page.unroute('**/databases/commands', fail_template_update_once)
                retry.click()
                expect(retry).to_be_hidden(timeout=30_000)
                expect(page.locator('#dbTemplateSaveState')).to_have_text('모든 변경 사항 저장됨', timeout=30_000)
                assert next(item for item in authority.read_database('owner', block_id)['templates']
                            if item['id'] == created['id'])['name'] == '재시도 확인'
                page.locator('#modalClose').click()
                expect(page.locator('#modalBg')).to_be_hidden()
                assert not errors
            finally:
                browser.close()
