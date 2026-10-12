"""UI-R5: database move picker and lost-response recovery in an isolated app."""
from __future__ import annotations

import os

import pytest

from code_assist_v1.notion_collaboration.page_creation import create_page
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.browser.test_notion_database_copy_browser import _attach_synthetic_file


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_owned_database_move_picker_recovers_lost_response(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        pytest.skip("exact selected gateway runtime is unavailable")

    with _isolated_collaborative_app(tmp_path, locked=False,
            owned_database_copy=True, owned_database_move=True,
            database_activity=True) as (origin, password, authority, workspace_id, source_id):
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root,
            identity_directory=authority.identity_directory)
        created = create_page(authority, "owner", workspace_id,
            title="MOVE-BROWSER-DESTINATION", parent_id="",
            base_version=store.get_workspace("owner", workspace_id)["version"],
            operation_id="browser-database-move-destination")
        destination_id = created["page"]["id"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1280, "height": 800})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + source_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' "
                    "&& State.navigation.collaborativeOwnedDatabaseMove "
                    "&& State.liveCollaboration.crdtReady", timeout=30_000)
                block_id = page.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return State.pageData.blocks.find(block => block.type === 'table')?.id || '';
                }""")
                assert block_id
                page.wait_for_function("""id => State.pageData.blocks.some(block => block.id === id)
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""", arg=block_id, timeout=30_000)
                page.reload(wait_until="domcontentloaded")
                row = page.locator(f'.sidebar-database-row[data-sidebar-db-block="{block_id}"]').first
                expect(row).to_be_visible(timeout=30_000)
                posted = []

                def lose_reply(route):
                    response = route.fetch()
                    assert response.status == 201, response.text()
                    posted.append(response.json())
                    route.abort("failed")

                page.route(f'**/databases/{block_id}/move', lose_reply)
                row.hover()
                row.locator('[data-sidebar-db-menu]').click()
                page.locator('#pageMenu [data-page-action="move"]').click()
                picker = page.locator('#moveDatabaseMenu')
                expect(picker).to_be_visible()
                picker.locator('#moveDatabaseSearch').press('Escape')
                expect(picker).to_be_hidden()
                expect(row.locator('[data-sidebar-db-menu]')).to_be_focused()
                row.locator('[data-sidebar-db-menu]').click()
                page.locator('#pageMenu [data-page-action="move"]').click()
                expect(picker).to_be_visible()
                expect(picker.locator('.menu-label')).to_contain_text('개인 페이지')
                picker.locator('#moveDatabaseSearch').fill('MOVE-BROWSER-DESTINATION')
                option = picker.locator(f'[data-move-database-destination="{destination_id}"]')
                expect(option).to_be_visible()
                option.click()
                expect(page.locator('#databaseMoveStatus')).to_contain_text(
                    '결과를 확인해야 합니다', timeout=30_000)
                assert len(posted) == 1
                assert posted[0]['source_document_id'] == source_id
                assert posted[0]['destination_document_id'] == destination_id
                assert posted[0]['database_id'] == block_id
                assert posted[0]['operation_id']
                assert all(block['id'] != block_id
                    for block in authority.read_document('owner', source_id)['blocks'])
                assert any(block['id'] == block_id
                    for block in authority.read_document('owner', destination_id)['blocks'])
                page.reload(wait_until='domcontentloaded')
                expect(page.locator('#databaseMoveStatus')).to_contain_text(
                    '결과를 확인해야 합니다', timeout=30_000)
                page.locator('#databaseMoveStatus button').filter(has_text='결과 확인').click()
                expect(page.locator('#databaseMoveStatus')).to_contain_text(
                    '옮겼습니다', timeout=30_000)
                expect(page.locator(f'.sidebar-database-row[data-sidebar-db-parent="{destination_id}"]'
                    f'[data-sidebar-db-block="{block_id}"]').first).to_be_visible(timeout=30_000)
                assert len(posted) == 1
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_owned_database_move_keeps_file_cell_readable_at_destination(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        pytest.skip("exact selected gateway runtime is unavailable")

    with _isolated_collaborative_app(tmp_path, locked=False,
            owned_database_copy=True, owned_database_move=True,
            database_activity=True) as (origin, password, authority, workspace_id, source_id):
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root,
            identity_directory=authority.identity_directory)
        created = create_page(authority, "owner", workspace_id,
            title="MOVE-FILE-DESTINATION", parent_id="",
            base_version=store.get_workspace("owner", workspace_id)["version"],
            operation_id="browser-database-move-file-destination")
        destination_id = created["page"]["id"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1280, "height": 800})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + source_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                page.wait_for_function("() => typeof State !== 'undefined' "
                    "&& State.navigation.collaborativeOwnedDatabaseMove "
                    "&& State.liveCollaboration.crdtReady", timeout=30_000)
                block_id = page.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return State.pageData.blocks.find(block => block.type === 'table')?.id || '';
                }""")
                assert block_id
                page.wait_for_function("""id => State.pageData.blocks.some(block => block.id === id)
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""", arg=block_id, timeout=30_000)
                page.reload(wait_until='domcontentloaded')
                page.locator(f'[data-block-id="{block_id}"] [data-database-add]').click()
                page.wait_for_function("""id => State.pageData?.databases?.find(database =>
                  database.id === id)?.rows?.length === 1 && !State.dirty
                  && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive""",
                  arg=block_id, timeout=30_000)
                row_id = authority.read_database('owner', block_id)['rows'][0]['id']
                file_id, payload = _attach_synthetic_file(authority, source_id, block_id, row_id)
                current = authority.read_document('owner', source_id)
                row_version = authority.read_database('owner', block_id)['rows'][0]['version']
                authority.execute_surface_command('owner', source_id,
                    database_id=block_id, target_id=row_id,
                    command_kind='database.row.content.set',
                    payload={'blocks': [{'id': 'browser-db-move-item-file', 'type': 'file',
                        'content': 'Item attachment', 'props': {'file_id': file_id}}]},
                    expected_target_version=row_version,
                    expected_document_sequence=current['document_sequence'],
                    expected_workspace_epoch=current['workspace_epoch'],
                    expected_document_epoch=current['document_epoch'],
                    operation_id='browser-db-move-item-file')
                current = authority.read_document('owner', source_id)
                target = {'kind': 'database_row', 'database_id': block_id, 'row_id': row_id}
                authority.add_comment('owner', source_id, 'browser-db-move-item-comment',
                    content='Keep item discussion', anchor=target,
                    mention_subject_ids=['content-editor'],
                    expected_workspace_epoch=current['workspace_epoch'],
                    expected_document_epoch=current['document_epoch'],
                    operation_id='browser-db-move-item-comment')
                destination = authority.read_document('owner', destination_id)
                authority.set_page_grant('owner', destination_id, 'subject', 'content-editor',
                    role='view', inherit_to_descendants=False,
                    expected_workspace_epoch=destination['workspace_epoch'],
                    expected_document_epoch=destination['document_epoch'],
                    operation_id='browser-db-move-destination-grant')
                page.reload(wait_until='domcontentloaded')
                row = page.locator(f'.sidebar-database-row[data-sidebar-db-block="{block_id}"]').first
                expect(row).to_be_visible(timeout=30_000)
                row.hover()
                row.locator('[data-sidebar-db-menu]').click()
                page.locator('#pageMenu [data-page-action="move"]').click()
                with page.expect_response(lambda response: response.request.method == 'POST'
                        and response.url.endswith(f'/databases/{block_id}/move'), timeout=30_000) as reply:
                    page.locator(f'#moveDatabaseMenu [data-move-database-destination="{destination_id}"]').click()
                assert reply.value.status == 201, reply.value.text()
                expect(page.locator('#databaseMoveStatus')).to_contain_text('옮겼습니다', timeout=30_000)
                assert all(block['id'] != block_id
                    for block in authority.read_document('owner', source_id)['blocks'])
                assert any(block['id'] == block_id
                    for block in authority.read_document('owner', destination_id)['blocks'])
                copied_id = authority.read_database('owner', block_id)['rows'][0]['cells'][
                    'browser-db-copy-file-property'][0]['file_id']
                assert authority.read_database('owner', block_id)['rows'][0]['body']['blocks'][0][
                    'props']['file_id'] == copied_id
                assert [item['id'] for item in authority.list_comments(
                    'owner', destination_id, item_target=target)['items']] == [
                        'browser-db-move-item-comment']
                assert authority.list_inbox('content-editor', workspace_id=workspace_id)[
                    'items'][0]['document_id'] == destination_id
                assert copied_id != file_id
                file = authority.get_file('owner', copied_id)
                assert file['document_id'] == destination_id and file['path'].read_bytes() == payload
                with pytest.raises(NotFoundOrDenied):
                    authority.get_file('owner', file_id)
                recipient = browser.new_page(locale='ko-KR', viewport={'width': 1280, 'height': 800})
                recipient_errors = []
                recipient.on('pageerror', lambda error: recipient_errors.append(str(error)))
                recipient.goto(origin + '/page/' + destination_id, wait_until='domcontentloaded')
                recipient.locator('#userId').fill('content-editor')
                recipient.locator('#password').fill(password)
                recipient.locator('#submitBtn').click()
                recipient.wait_for_function("() => typeof State !== 'undefined' "
                    "&& State.user?.id === 'content-editor' "
                    "&& State.navigation.collaborativeSharing "
                    "&& document.body.dataset.notionReady === 'true'", timeout=30_000)
                switched = recipient.evaluate("async workspace => "
                    "await switchCanonicalWorkspace(workspace)", workspace_id)
                assert switched
                assert recipient.evaluate("() => State.navigation.workspaceId") == workspace_id
                recipient.locator('#inboxNavBtn').click()
                item = recipient.locator('#inboxList [data-inbox-item]')
                expect(item).to_have_count(1, timeout=10_000)
                item.click()
                recipient.wait_for_function("""({destination,row}) =>
                  State.activePageId === destination
                  && LinkedDatabaseItems?.snapshot()?.rowId === row""",
                  arg={'destination': destination_id, 'row': row_id}, timeout=30_000)
                assert not recipient_errors, recipient_errors
                assert not errors, errors
            finally:
                browser.close()
