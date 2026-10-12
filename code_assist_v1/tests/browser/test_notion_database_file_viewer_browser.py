"""File cells remain readable without offering edits to a real view-only member."""

from hashlib import sha256
from io import BytesIO
import os

import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app,
)
from code_assist_v1.tests.notion_collaboration.test_workspace_images import png


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
def test_viewer_file_menu_is_read_only_and_preserves_database(tmp_path):
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
                owner = browser.new_page(locale='ko-KR')
                owner.goto(origin + '/page/' + page_id, wait_until='domcontentloaded')
                owner.locator('#userId').fill('owner')
                owner.locator('#password').fill(password)
                owner.locator('#submitBtn').click()
                expect(owner.locator('#pageTitle')).to_be_enabled(timeout=30_000)
                owner.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration?.crdtReady", timeout=30_000)
                database_id = owner.evaluate("""() => {
                  insertBlockAfter(State.pageData.blocks.length - 1, 'table');
                  return State.pageData.blocks.find(block => block.type === 'table')?.id || '';
                }""")
                owner.wait_for_function("""id => State.pageData.blocks.some(block => block.id === id)
                  && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive""", arg=database_id, timeout=30_000)
                owner.reload(wait_until='domcontentloaded')
                root = owner.locator(f'[data-block-id="{database_id}"]')
                root.locator('[data-database-add]').click()
                owner.wait_for_function("""id => State.pageData?.databases?.find(db => db.id === id)
                  ?.rows?.length === 1 && !State.dirty && !State.liveCollaboration.unsyncedCount
                  && !State.liveCollaboration.sendQueueActive && PendingDatabaseItemCreates.size === 0""",
                  arg=database_id, timeout=30_000)
                document = authority.read_document('owner', page_id)
                authority.execute_schema_command(
                    'owner', page_id, database_id=database_id,
                    command_kind='database.property.create', target_id='viewer-files',
                    payload={'name': '첨부', 'type': 'files', 'config': {}},
                    expected_document_sequence=document['document_sequence'],
                    expected_workspace_epoch=document['workspace_epoch'],
                    expected_document_epoch=document['document_epoch'],
                    expected_target_version=0, operation_id='viewer-file-property',
                )
                document = authority.read_document('owner', page_id)
                authority.execute_schema_command(
                    'owner', page_id, database_id=database_id,
                    command_kind='database.property.create', target_id='viewer-empty-files',
                    payload={'name': '빈 첨부', 'type': 'files', 'config': {}},
                    expected_document_sequence=document['document_sequence'],
                    expected_workspace_epoch=document['workspace_epoch'],
                    expected_document_epoch=document['document_epoch'],
                    expected_target_version=0, operation_id='viewer-empty-file-property',
                )
                row_id = authority.read_database('owner', database_id)['rows'][0]['id']
                file_bytes = png()
                document = authority.read_document('owner', page_id)
                upload = authority.begin_upload(
                    'owner', page_id, original_name='viewer-tiny.png',
                    mime_type='image/png', total_size=len(file_bytes),
                    expected_sha256='sha256:' + sha256(file_bytes).hexdigest(),
                    expected_document_sequence=document['document_sequence'],
                    expected_workspace_epoch=document['workspace_epoch'],
                    expected_document_epoch=document['document_epoch'],
                    operation_id='viewer-file-upload-begin',
                )
                authority.stage_chunk(
                    'owner', upload['session_id'], chunk_index=0,
                    stream=BytesIO(file_bytes), operation_id='viewer-file-upload-chunk',
                )
                uploaded = authority.finalize_upload(
                    'owner', upload['session_id'], operation_id='viewer-file-upload-finish',
                )
                document = authority.read_document('owner', page_id)
                authority.execute_typed_command(
                    'owner', page_id, command_kind='database.cell.set',
                    target_id=f'{row_id}:viewer-files',
                    payload={'row_id': row_id, 'property_id': 'viewer-files', 'value': [
                        {'url': 'https://example.com/viewer.pdf', 'name': 'Viewer brief'},
                        {'file_id': uploaded['file_id'], 'name': 'viewer-tiny.png'},
                    ]},
                    expected_document_sequence=document['document_sequence'],
                    expected_workspace_epoch=document['workspace_epoch'],
                    expected_document_epoch=document['document_epoch'],
                    expected_target_version=0, operation_id='viewer-file-link-cell',
                )
                owner.reload(wait_until='domcontentloaded')
                root = owner.locator(f'[data-block-id="{database_id}"]')
                expect(root.get_by_role('link', name='Viewer brief')).to_be_visible(timeout=30_000)
                owner_viewport = owner.viewport_size
                owner.set_viewport_size({'width':390,'height':700})
                topbar_geometry = owner.evaluate("""() => {
                  const top=document.querySelector('.topbar');
                  const context=document.querySelector('.topbar-page-context').getBoundingClientRect();
                  const actions=document.querySelector('.top-actions').getBoundingClientRect();
                  const restore=document.querySelector('#sidebarRestoreBtn').getBoundingClientRect();
                  const editTime=document.querySelector('#pageEditTime');
                  return {scrollWidth:top.scrollWidth,clientWidth:top.clientWidth,
                    contextLeft:context.left,contextRight:context.right,
                    restoreRight:restore.right,actionsLeft:actions.left,actionsRight:actions.right,
                    editTimeVisible:!editTime.hidden && editTime.getBoundingClientRect().width>0,
                    locationDisplay:getComputedStyle(document.querySelector('#pageLocationBtn')).display,
                    lockDisplay:getComputedStyle(document.querySelector('#pageLockBtn')).display};
                }""")
                assert topbar_geometry['restoreRight'] <= topbar_geometry['contextLeft'], topbar_geometry
                assert topbar_geometry['contextRight'] <= topbar_geometry['actionsLeft'] + 1, topbar_geometry
                assert topbar_geometry['actionsRight'] <= 390, topbar_geometry
                assert topbar_geometry['scrollWidth'] <= topbar_geometry['clientWidth'] + 1, topbar_geometry
                assert not topbar_geometry['editTimeVisible'], topbar_geometry
                assert topbar_geometry['locationDisplay'] == 'none', topbar_geometry
                assert topbar_geometry['lockDisplay'] == 'none', topbar_geometry
                empty_cell = root.locator('[data-db-row="0"][data-db-file-link-toggle="viewer-empty-files"]').locator('..').locator('..')
                empty_cell.get_by_role('button', name='파일 추가').click()
                empty_cell.get_by_role('button', name='링크 임베드').click()
                empty_cell.locator('[data-db-file-link-url]').fill('javascript:alert(1)')
                empty_cell.locator('[data-db-file-link-apply]').click()
                expect(empty_cell.locator('[data-db-file-link-error]')).to_have_text(
                    'http 또는 https 파일 링크를 입력해 주세요.')
                owner.evaluate('renderBlocks()')
                expect(empty_cell.locator('[data-db-file-link-error]')).to_be_visible()
                anchor_bounds = empty_cell.locator('.database-file-menu-toggle').bounding_box()
                assert anchor_bounds and anchor_bounds['x'] >= 0 and anchor_bounds['x'] < 390
                assert anchor_bounds['y'] >= 0 and anchor_bounds['y'] < 700, anchor_bounds
                menu_bounds = empty_cell.get_by_role('dialog', name='파일 추가').bounding_box()
                assert menu_bounds and menu_bounds['x'] >= 0 and menu_bounds['x'] + menu_bounds['width'] <= 390
                assert menu_bounds['y'] >= 0 and menu_bounds['y'] + menu_bounds['height'] <= 700
                if os.environ.get('NOTION_GRID_FILE_ERROR_SCREENSHOT'):
                    owner.screenshot(path=os.environ['NOTION_GRID_FILE_ERROR_SCREENSHOT'])
                empty_cell.locator('[data-db-file-link-url]').fill('https://example.com/first.pdf')
                expect(empty_cell.locator('[data-db-file-link-error]')).to_be_hidden()
                empty_cell.locator('[data-db-file-link-name]').fill('First file')
                empty_cell.locator('[data-db-file-link-apply]').click()
                expect(root.get_by_role('link', name='First file')).to_be_visible(timeout=15_000)
                owner.set_viewport_size(owner_viewport)
                file_cell = root.locator('[data-db-row="0"][data-db-file-link-toggle="viewer-files"]').locator('..').locator('..')
                file_cell.get_by_role('button', name='파일 추가').click()
                file_cell.get_by_role('button', name='링크 임베드').click()
                file_cell.locator('[data-db-file-link-url]').fill('https://example.com/owner.pdf')
                file_cell.locator('[data-db-file-link-name]').fill('Owner note')
                file_cell.locator('[data-db-file-link-apply]').click()
                expect(root.get_by_role('link', name='Owner note')).to_be_visible(timeout=15_000)
                owner.set_viewport_size({'width':390,'height':700})
                root.locator('[data-table-row="0"]').first.click()
                owner_peek = owner.locator('#linkedItemPeek')
                expect(owner_peek).to_be_visible(timeout=30_000)
                owner_peek_files = owner_peek.locator('[data-linked-item-files="viewer-files"]')
                owner_peek_files.get_by_role('button', name='파일 추가').click()
                owner_peek_files.get_by_role('button', name='링크 임베드').click()
                owner_peek_form = owner_peek_files.locator('[data-linked-item-file-form]')
                owner_peek_form.locator('[data-linked-item-file-url]').fill('javascript:alert(1)')
                owner_peek_form.locator('[data-linked-item-file-apply]').click()
                expect(owner_peek_form.locator('[data-linked-item-file-error]')).to_have_text(
                    'http 또는 https 파일 링크를 입력해 주세요.')
                owner.evaluate('renderLinkedItemPeek(LinkedDatabaseItems.snapshot())')
                expect(owner_peek_form.locator('[data-linked-item-file-error]')).to_be_visible()
                peek_menu_bounds = owner_peek_files.get_by_role('dialog', name='파일 추가').bounding_box()
                assert peek_menu_bounds and peek_menu_bounds['x'] >= 0
                assert peek_menu_bounds['x'] + peek_menu_bounds['width'] <= 390
                assert peek_menu_bounds['y'] >= 0
                assert peek_menu_bounds['y'] + peek_menu_bounds['height'] <= 700
                if os.environ.get('NOTION_PEEK_FILE_ERROR_SCREENSHOT'):
                    owner.screenshot(path=os.environ['NOTION_PEEK_FILE_ERROR_SCREENSHOT'])
                owner_peek.locator('[data-linked-item-close]').click()
                owner.set_viewport_size(owner_viewport)
                before = authority.read_database('owner', database_id)
                witness = authority.read_document('owner', page_id)
                authority.set_page_grant(
                    'owner', page_id, 'subject', 'content-editor', role='view',
                    inherit_to_descendants=False,
                    expected_workspace_epoch=witness['workspace_epoch'],
                    expected_document_epoch=witness['document_epoch'],
                    operation_id='viewer-file-role',
                )

                viewer_context = browser.new_context(locale='ko-KR', accept_downloads=True)
                viewer = viewer_context.new_page()
                errors = []
                viewer.on('pageerror', lambda error: errors.append(str(error)))
                viewer.goto(origin + '/page/' + page_id, wait_until='domcontentloaded')
                viewer.locator('#userId').fill('content-editor')
                viewer.locator('#password').fill(password)
                viewer.locator('#submitBtn').click()
                expect(viewer.locator('#workspaceMain')).to_be_visible(timeout=30_000)
                viewer.goto(origin + '/page/' + page_id, wait_until='domcontentloaded')
                expect(viewer.locator('#pageTitle')).to_be_disabled(timeout=30_000)
                viewer_root = viewer.locator(f'[data-block-id="{database_id}"]')
                viewer_chip = viewer_root.locator('[data-db-file-target="viewer-files"]')
                expect(viewer_chip.get_by_role('link', name='Viewer brief')).to_have_attribute(
                    'href', 'https://example.com/viewer.pdf', timeout=30_000)
                expect(viewer_root.get_by_role('link', name='Owner note')).to_have_attribute(
                    'href', 'https://example.com/owner.pdf')
                expect(viewer_root.get_by_role('link', name='First file')).to_have_attribute(
                    'href', 'https://example.com/first.pdf')
                expect(viewer_root.get_by_role('button', name='파일 추가')).to_have_count(0)
                expect(viewer_root.locator('[data-db-file-drag], [data-db-file-remove]')).to_have_count(0)
                viewer_chip.get_by_role('button', name='Viewer brief 메뉴').click()
                actions = viewer_chip.get_by_role('menu', name='Viewer brief 파일 작업')
                expect(actions).to_be_visible()
                expect(actions.get_by_role('menuitem', name='원본 보기')).to_have_attribute(
                    'href', 'https://example.com/viewer.pdf')
                expect(actions.get_by_role('menuitem', name='삭제')).to_have_count(0)
                expect(actions.get_by_role('menuitem', name='다운로드')).to_have_count(0)
                viewer.keyboard.press('Escape')
                expect(actions).to_be_hidden()
                uploaded_chip = viewer_root.locator('.database-file-chip').filter(has_text='viewer-tiny.png')
                expect(uploaded_chip.get_by_role('link', name='viewer-tiny.png')).to_have_attribute(
                    'href', f'/api/notion/collaboration/files/{uploaded["file_id"]}')
                uploaded_chip.get_by_role('button', name='viewer-tiny.png 메뉴').click()
                uploaded_actions = uploaded_chip.get_by_role('menu', name='viewer-tiny.png 파일 작업')
                expect(uploaded_actions.get_by_role('menuitem', name='삭제')).to_have_count(0)
                expect(uploaded_actions.get_by_role('menuitem', name='다운로드')).to_have_attribute(
                    'download', 'viewer-tiny.png')
                uploaded_actions.get_by_role('menuitem', name='전체 화면').click()
                expect(viewer.locator('#modalBody .database-file-fullscreen img')).to_have_attribute(
                    'src', f'/api/notion/collaboration/files/{uploaded["file_id"]}')
                viewer.locator('#modalClose').click()
                uploaded_chip.get_by_role('button', name='viewer-tiny.png 메뉴').click()
                with viewer.expect_download() as download_info:
                    uploaded_actions.get_by_role('menuitem', name='다운로드').click()
                download = download_info.value
                assert download.suggested_filename == 'viewer-tiny.png'
                assert open(download.path(), 'rb').read() == file_bytes
                viewer_root.locator('[data-table-row="0"]').first.click()
                peek = viewer.locator('#linkedItemPeek')
                expect(peek).to_be_visible(timeout=30_000)
                peek_files = peek.locator('[data-linked-item-files="viewer-files"]')
                expect(peek_files.get_by_role('link', name='Viewer brief')).to_have_attribute(
                    'href', 'https://example.com/viewer.pdf')
                expect(peek_files.get_by_role('button', name='파일 추가')).to_have_count(0)
                expect(peek_files.locator('[data-linked-item-file-drag], [data-linked-item-file-remove]')).to_have_count(0)
                peek_files.get_by_role('button', name='Viewer brief 메뉴').click()
                peek_actions = peek_files.get_by_role('menu', name='Viewer brief 파일 작업')
                expect(peek_actions.get_by_role('menuitem', name='원본 보기')).to_be_visible()
                expect(peek_actions.get_by_role('menuitem', name='삭제')).to_have_count(0)
                viewer.keyboard.press('Escape')
                expect(peek_actions).to_be_hidden()
                peek_file = peek_files.locator('.database-file-chip').filter(has_text='viewer-tiny.png')
                peek_file.get_by_role('button', name='viewer-tiny.png 메뉴').click()
                expect(peek_file.get_by_role('menuitem', name='다운로드')).to_be_visible()
                expect(peek_file.get_by_role('menuitem', name='삭제')).to_have_count(0)
                viewer.keyboard.press('Escape')
                assert authority.read_database('owner', database_id)['rows'] == before['rows']
                witness = authority.read_document('owner', page_id)
                authority.revoke_page_grant(
                    'owner', page_id, 'subject', 'content-editor',
                    expected_workspace_epoch=witness['workspace_epoch'],
                    expected_document_epoch=witness['document_epoch'],
                    operation_id='viewer-file-role-revoke',
                )
                viewer.wait_for_function("""() => State.liveCollaboration.recoveryOnly
                  || document.querySelector('#pageTitle')?.offsetParent === null""", timeout=15_000)
                response = viewer.request.get(
                    origin + f'/api/notion/collaboration/files/{uploaded["file_id"]}',
                )
                assert response.status in (403, 404)
                assert response.body() != file_bytes
                assert not errors
            finally:
                browser.close()
