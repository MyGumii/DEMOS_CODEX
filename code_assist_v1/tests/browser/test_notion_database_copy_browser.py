"""Owned database copy menu and commit in an isolated collaborative app."""
from __future__ import annotations

from hashlib import sha256
from importlib import metadata
from io import BytesIO
import os
import re
import json

import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app


def _attach_synthetic_file(authority, page_id, database_id, row_id):
    payload = b'isolated owned database copy attachment'
    page = authority.read_document('owner', page_id)
    fences = {
        'expected_document_sequence': page['document_sequence'],
        'expected_workspace_epoch': page['workspace_epoch'],
        'expected_document_epoch': page['document_epoch'],
    }
    session = authority.begin_upload('owner', page_id, original_name='copy-evidence.txt',
        mime_type='text/plain', total_size=len(payload),
        expected_sha256='sha256:' + sha256(payload).hexdigest(),
        operation_id='browser-db-copy-file-begin', **fences)
    authority.stage_chunk('owner', session['session_id'], chunk_index=0,
        stream=BytesIO(payload), operation_id='browser-db-copy-file-chunk')
    uploaded = authority.finalize_upload('owner', session['session_id'],
        operation_id='browser-db-copy-file-final')
    property_id = 'browser-db-copy-file-property'
    page = authority.read_document('owner', page_id)
    fences = {
        'expected_document_sequence': page['document_sequence'],
        'expected_workspace_epoch': page['workspace_epoch'],
        'expected_document_epoch': page['document_epoch'],
    }
    authority.execute_schema_command('owner', page_id, database_id=database_id,
        command_kind='database.property.create', target_id=property_id,
        payload={'name': 'Files', 'type': 'files', 'config': {}},
        operation_id='browser-db-copy-file-property', **fences)
    page = authority.read_document('owner', page_id)
    fences = {
        'expected_document_sequence': page['document_sequence'],
        'expected_workspace_epoch': page['workspace_epoch'],
        'expected_document_epoch': page['document_epoch'],
    }
    authority.execute_typed_command('owner', page_id, command_kind='database.cell.set',
        target_id=f'{row_id}:{property_id}',
        payload={'row_id': row_id, 'property_id': property_id,
                 'value': [{'file_id': uploaded['file_id'], 'name': 'copy-evidence.txt'}]},
        expected_target_version=0, operation_id='browser-db-copy-file-cell', **fences)
    return uploaded['file_id'], payload


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize("include_content", [False, True], ids=["schema-only", "with-rows"])
def test_ui_owned_database_copy_menu_and_commit(tmp_path, include_content):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        try:
            available = metadata.version("uvicorn") == "0.52.4" and metadata.version("websockets") == "17.0.1"
        except metadata.PackageNotFoundError:
            available = False
        if not available:
            pytest.skip("exact selected gateway runtime is unavailable")

    with _isolated_collaborative_app(tmp_path, locked=False, owned_database_copy=True) as (
            origin, password, authority, workspace_id, page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1280, "height": 800})
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' "
                    "&& State.navigation.collaborativeOwnedDatabaseCopy "
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
                expect(page.locator(f'[data-block-id="{block_id}"] [data-db-title]')).to_be_visible(timeout=30_000)
                page.locator(f'[data-block-id="{block_id}"] [data-database-add]').click()
                page.wait_for_function("""id => {
                  const database = State.pageData?.databases?.find(item => item.id === id);
                  return database?.rows?.length === 1 && !State.dirty
                    && !State.liveCollaboration.unsyncedCount && !State.liveCollaboration.sendQueueActive;
                }""", arg=block_id, timeout=30_000)
                source_rows = authority.read_database('owner', block_id)['rows']
                assert len(source_rows) == 1
                original_file = payload = None
                if include_content:
                    original_file, payload = _attach_synthetic_file(
                        authority, page_id, block_id, source_rows[0]['id'])
                    page.reload(wait_until='domcontentloaded')
                    expect(page.locator(f'[data-block-id="{block_id}"] .database-file-chip a')).to_be_visible(timeout=30_000)
                row = page.locator(f'.sidebar-database-row[data-sidebar-db-block="{block_id}"]').first
                expect(row).to_be_visible(timeout=30_000)
                row.hover()
                row.locator('[data-sidebar-db-menu]').click()
                expect(page.locator('#pageMenu [data-page-action="move"]')).to_have_count(0)
                trigger = page.locator('#pageMenu [data-page-action="duplicate_menu"]')
                expect(trigger).to_be_visible()
                trigger.focus()
                trigger.press('ArrowRight')
                submenu = page.locator('#databaseCopyMenu')
                expect(submenu).to_be_visible()
                expect(submenu.locator('[data-database-copy-content]')).to_have_count(2)
                expect(submenu.locator('[data-database-copy-content="true"]')).to_contain_text('콘텐츠 포함 복제')
                expect(submenu.locator('[data-database-copy-content="false"]')).to_contain_text('콘텐츠 제외 복제')
                expect(submenu.locator('[data-database-copy-content="true"]')).to_have_class(re.compile(r'\bmenu-active\b'))
                submenu.press('ArrowDown')
                expect(submenu.locator('[data-database-copy-content="false"]')).to_have_class(re.compile(r'\bmenu-active\b'))
                expect(submenu.locator('[data-database-copy-content="false"]')).to_be_focused()
                submenu.press('ArrowUp')
                expect(submenu.locator('[data-database-copy-content="true"]')).to_have_class(re.compile(r'\bmenu-active\b'))
                expect(submenu.locator('[data-database-copy-content="true"]')).to_be_focused()
                submenu.press('Escape')
                expect(submenu).to_be_hidden()
                expect(trigger).to_be_focused()
                trigger.press('ArrowRight')
                choice = str(include_content).lower()
                with page.expect_response(lambda response: response.request.method == 'POST'
                        and response.url.endswith(f'/databases/{block_id}/copy'), timeout=30_000) as reply:
                    submenu.locator(f'[data-database-copy-content="{choice}"]').click()
                response = reply.value
                assert response.status == 201, response.text()
                assert response.request.post_data_json['include_content'] is include_content
                result = response.json()
                assert result['workspace_id'] == workspace_id
                assert result['document_id'] == page_id
                assert result['source_database_id'] == block_id
                assert result['include_content'] is include_content
                copied_rows = authority.read_database('owner', result['database_id'])['rows']
                assert len(copied_rows) == (1 if include_content else 0)
                if include_content:
                    assert copied_rows[0]['id'] != source_rows[0]['id']
                    copied_files = [file['file_id'] for value in copied_rows[0]['cells'].values()
                        if isinstance(value, list) for file in value if isinstance(file, dict)
                        and 'file_id' in file]
                    assert len(copied_files) == 1 and copied_files[0] != original_file
                expect(page.locator('#databaseCopyStatus')).to_contain_text('데이터베이스 복제가 완료되었습니다.', timeout=30_000)
                try:
                    page.wait_for_function("id => new URL(location.href).searchParams.get('sidebar_db') === id",
                        arg=result['block_id'], timeout=15_000)
                except Exception as error:
                    detail = page.evaluate("""id => ({href:location.href,activePageId:State.activePageId,
                      pageId:State.pageData?.page?.id,blocks:State.pageData?.blocks?.map(block => block.id),
                      databases:State.pageData?.databases?.map(database => database.id),
                      status:document.querySelector('#status')?.textContent,
                      copiedRow:!!document.querySelector('.sidebar-database-row[data-sidebar-db-block="' +
                        CSS.escape(id) + '"]')})""", result['block_id'])
                    raise AssertionError(json.dumps(detail, ensure_ascii=False)) from error
                try:
                    expect(page.locator('#pageView')).to_have_class(re.compile(r'\bdatabase-full-page\b'), timeout=15_000)
                except Exception as error:
                    detail = page.evaluate("""() => ({href:location.href,activePageId:State.activePageId,
                      pageId:State.pageData?.page?.id,blocks:State.pageData?.blocks?.map(block => block.id),
                      databases:State.pageData?.databases?.map(database => database.id),
                      pageViewClass:document.querySelector('#pageView')?.className,
                      pageTitle:document.querySelector('#pageTitle')?.value,
                      status:document.querySelector('#status')?.textContent})""")
                    detail['gateway_log'] = (tmp_path / 'gateway.log').read_text(encoding='utf-8', errors='replace')[-3000:]
                    detail['app_log'] = (tmp_path / 'app.log').read_text(encoding='utf-8', errors='replace')[-3000:]
                    raise AssertionError(json.dumps(detail, ensure_ascii=False)) from error
                page.reload(wait_until='domcontentloaded')
                expect(page.locator(f'.sidebar-database-row[data-sidebar-db-block="{result["block_id"]}"]').first).to_be_visible(timeout=30_000)
                expect(page.locator('#databaseCopyStatus')).to_contain_text('데이터베이스 복제가 완료되었습니다.', timeout=30_000)
                expect(page.locator('#pageView')).to_have_class(re.compile(r'\bdatabase-full-page\b'), timeout=30_000)
                if include_content:
                    link = page.locator('.database-full-page .database-file-chip a').first
                    expect(link).to_be_visible()
                    assert copied_files[0] in link.get_attribute('href')
                    page_state = authority.read_document('owner', page_id)
                    authority.delete_file('owner', original_file,
                        operation_id='browser-db-copy-delete-original-file',
                        expected_document_sequence=page_state['document_sequence'],
                        expected_workspace_epoch=page_state['workspace_epoch'],
                        expected_document_epoch=page_state['document_epoch'])
                    downloaded = page.request.get(origin + link.get_attribute('href'))
                    assert downloaded.status == 200
                    assert downloaded.body() == payload
                page.locator('#pageMoreBtn').click()
                expect(page.locator('#pageMenu')).to_have_attribute('data-location', 'topbar')
                topbar_copy = page.locator('#pageMenu [data-page-action="duplicate_menu"]')
                expect(topbar_copy).to_be_visible()
                topbar_copy.click()
                expect(page.locator('#databaseCopyMenu')).to_be_visible()
                page.locator('#databaseCopyMenu').press('Escape')
                expect(topbar_copy).to_be_focused()
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_owned_database_copy_recovers_lost_response_after_reload(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        pytest.skip("exact selected gateway runtime is unavailable")

    with _isolated_collaborative_app(tmp_path, locked=False, owned_database_copy=True) as (
            origin, password, authority, _workspace_id, page_id):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1280, "height": 800})
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                page.wait_for_function("() => typeof State !== 'undefined' "
                    "&& State.navigation.collaborativeOwnedDatabaseCopy "
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
                posted: list[dict] = []

                def lose_reply(route):
                    response = route.fetch()
                    assert response.status == 201, response.text()
                    posted.append(response.json())
                    route.abort('failed')

                page.route(f'**/databases/{block_id}/copy', lose_reply)
                row.hover()
                row.locator('[data-sidebar-db-menu]').click()
                page.locator('#pageMenu [data-page-action="duplicate_menu"]').click()
                page.locator('#databaseCopyMenu [data-database-copy-content="true"]').click()
                expect(page.locator('#databaseCopyStatus')).to_contain_text('결과를 확인해야 합니다', timeout=30_000)
                assert len(posted) == 1
                copied_id = posted[0]['database_id']
                assert authority.read_database('owner', copied_id)['id'] == copied_id
                page.reload(wait_until='domcontentloaded')
                expect(page.locator('#databaseCopyStatus')).to_contain_text('결과를 확인해야 합니다', timeout=30_000)
                page.locator('#databaseCopyStatus button').filter(has_text='결과 확인').click()
                expect(page.locator('#databaseCopyStatus')).to_contain_text('데이터베이스 복제가 완료되었습니다.', timeout=30_000)
                assert len(posted) == 1
                assert not errors, errors
            finally:
                browser.close()
