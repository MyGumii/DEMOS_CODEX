"""R5: sidebar child drafts survive concurrent access changes in an isolated app."""
from __future__ import annotations

from importlib import metadata
import os
import re
import time

import pytest

from code_assist_v1.notion_collaboration.page_settings import update_page_settings
from code_assist_v1.notion_collaboration.page_creation import native_page_items
from code_assist_v1.notion_collaboration.page_creation import create_page
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app, _settings_request,
)


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('starter_type', ['ai_notes', 'table'])
def test_ui_collaborative_row_plus_starter_creates_selected_page_content(tmp_path, starter_type):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False, navigation_database_favorites=True) as (
        origin, password, authority, workspace_id, parent_id,
    ):
        original_pages = {item['id'] for item in native_page_items(authority, 'owner', workspace_id)}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': 1280, 'height': 720})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin + '/page/' + parent_id, wait_until='domcontentloaded')
                page.locator('#userId').fill('owner')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                expect(page.locator('#pageTitle')).to_be_enabled(timeout=30_000)
                page.wait_for_function(
                    "() => typeof State !== 'undefined' && State.navigation.collaborativePageCreation && State.liveCollaboration.crdtReady",
                    timeout=30_000,
                )
                parent = page.locator(f'#ownPages .page-row[data-page-id="{parent_id}"]')
                parent.hover()
                parent.locator('[data-page-child]').click()
                starter = page.locator('#sidePeekCreateStarter')
                expect(starter).to_be_visible()
                page.wait_for_function('() => Boolean(State.sidePeek.pageId)', timeout=30_000)
                starter.locator(f'[data-peek-starter="{starter_type}"]').click()
                expect(starter).to_be_hidden()
                page.wait_for_function(
                    '(type) => Boolean(State.sidePeek.pageId) && State.sidePeek.data.blocks.some(block => block.type === type)',
                    arg=starter_type, timeout=30_000,
                )
                child_id = page.evaluate('() => State.sidePeek.pageId')
                page.wait_for_function('() => Boolean(State.sidePeek.embedded) && !State.sidePeek.dirty', timeout=30_000)
                assert child_id not in original_pages
                page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
                expect(page.locator('#sidePeek')).to_be_hidden(timeout=30_000)
                page.reload(wait_until='domcontentloaded')
                assert child_id in {item['id'] for item in native_page_items(authority, 'owner', workspace_id)}
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_collaborative_new_child_toolbar_targets_child_after_save(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False, navigation_database_favorites=True) as (
        origin, password, authority, workspace_id, parent_id,
    ):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': 1280, 'height': 720})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin + '/page/' + parent_id, wait_until='domcontentloaded')
                page.locator('#userId').fill('owner')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                expect(page.locator('#pageTitle')).to_be_enabled(timeout=30_000)
                page.wait_for_function(
                    "() => typeof State !== 'undefined' && State.navigation.collaborativePageCreation && State.liveCollaboration.crdtReady",
                    timeout=30_000,
                )
                parent = page.locator(f'#ownPages .page-row[data-page-id="{parent_id}"]')
                parent.hover()
                parent.locator('[data-page-child]').click()
                page.locator('#sidePeekTitle').fill('상단 버튼 대상 검증')
                page.wait_for_function('() => Boolean(State.sidePeek.pageId && State.sidePeek.embedded) && !State.sidePeek.dirty', timeout=30_000)
                child_id = page.evaluate('() => State.sidePeek.pageId')
                favorite = page.locator('#sidePeekFavoriteBtn')
                expect(favorite).to_have_attribute('aria-disabled', 'false')
                favorite.click()
                expect(favorite).to_have_attribute('aria-pressed', 'true', timeout=30_000)
                expect(page.locator(f'#favoritePages .page-row[data-page-id="{child_id}"]')).to_be_visible(timeout=30_000)
                assert not page.locator(f'#favoritePages .page-row[data-page-id="{parent_id}"]').count()
                page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
                page.locator('#sidePeekCopyLinkBtn').click()
                expect(page.locator('#status')).to_contain_text('페이지 링크 복사됨', timeout=30_000)
                copied_url = page.evaluate('() => navigator.clipboard.readText()')
                assert copied_url.endswith('/page/' + child_id), copied_url
                share_calls = []
                lose_first_response = {'POST': True, 'DELETE': True}

                def observe_share(route):
                    method = route.request.method
                    share_calls.append((method, route.request.url, route.request.post_data_json))
                    if lose_first_response.get(method):
                        lose_first_response[method] = False
                        body = route.request.post_data_json
                        if method == 'POST':
                            authority.set_page_grant(
                                'owner', child_id, body['principal_type'], body['principal_id'],
                                role=body['role'],
                                inherit_to_descendants=body['inherit_to_descendants'],
                                expected_workspace_epoch=body['expected_workspace_epoch'],
                                expected_document_epoch=body['expected_document_epoch'],
                                operation_id=body['operation_id'],
                            )
                        else:
                            authority.revoke_page_grant(
                                'owner', child_id, 'subject', 'content-editor',
                                expected_workspace_epoch=body['expected_workspace_epoch'],
                                expected_document_epoch=body['expected_document_epoch'],
                                operation_id=body['operation_id'],
                            )
                        route.fulfill(status=503, content_type='application/json',
                                      body='{"error":"simulated response loss"}')
                    else:
                        route.continue_()

                page.route(re.compile(r'/api/notion/workspaces/[^/]+/pages/[^/]+/shares(?:/|$)'), observe_share)
                page.locator('#sidePeekShareBtn').click()
                expect(page.locator('#sidePeekSharePopover')).to_be_visible(timeout=30_000)
                expect(page.locator('#sidePeekShareGeneral')).to_have_text('초대받은 사용자만')
                assert page.evaluate('() => State.activePageId') == parent_id
                expect(page.locator('#sidePeek')).to_be_visible()
                assert share_calls and all('/pages/' + child_id + '/shares' in url for _, url, _ in share_calls)
                search = page.locator('#sidePeekShareSearch')
                search.fill('schema-editor')
                expect(page.locator('#sidePeekShareSuggestions')).to_be_visible()
                search.press('Escape')
                expect(page.locator('#sidePeekShareSuggestions')).to_be_hidden()
                expect(page.locator('#sidePeekSharePopover')).to_be_visible()
                search.fill('content-editor')
                expect(page.get_by_role('option', name='content-editor')).to_be_visible()
                search.press('ArrowDown')
                search.press('Enter')
                expect(page.locator('#sidePeekShareOptions')).to_be_visible()
                page.locator('#sidePeekShareRole').select_option('view')
                page.locator('#sidePeekShareAddBtn').click()
                expect(page.locator('#sidePeekSharePeople')).to_contain_text('content-editor', timeout=30_000)
                response = page.request.get(f'{origin}/api/notion/workspaces/{workspace_id}/pages/{child_id}/shares')
                assert response.ok, response.text()
                assert any(item['principal_id'] == 'content-editor' and item['role'] == 'view'
                           for item in response.json()['items'])
                page.locator('#sidePeekSharePeople button[aria-label="content-editor의 공유 해제"]').click()
                expect(page.locator('#sidePeekSharePeople')).not_to_contain_text('content-editor', timeout=30_000)
                response = page.request.get(f'{origin}/api/notion/workspaces/{workspace_id}/pages/{child_id}/shares')
                assert response.ok, response.text()
                assert not any(item['principal_id'] == 'content-editor' for item in response.json()['items'])
                for method in ('POST', 'DELETE'):
                    calls = [body for call_method, _, body in share_calls if call_method == method]
                    assert len(calls) == 2, (method, calls)
                    assert calls[0] == calls[1], (method, calls)
                page.keyboard.press('Escape')
                expect(page.locator('#sidePeekSharePopover')).to_be_hidden()
                expect(page.locator('#sidePeek')).to_be_visible()
                assert page.evaluate('() => State.activePageId') == parent_id
                assert child_id in {item['id'] for item in native_page_items(authority, 'owner', workspace_id)}
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_collaborative_row_plus_creates_address_and_archives_empty_child_on_close(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False, navigation_database_favorites=True) as (
        origin, password, authority, workspace_id, parent_id,
    ):
        original_pages = {item['id'] for item in native_page_items(authority, 'owner', workspace_id)}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': 1280, 'height': 720})
                errors, operations, archives = [], [], []
                lose_next_create = {'enabled': False, 'lost': False}
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin + '/page/' + parent_id, wait_until='domcontentloaded')
                page.locator('#userId').fill('owner')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                expect(page.locator('#pageTitle')).to_be_enabled(timeout=30_000)
                page.wait_for_function(
                    "() => typeof State !== 'undefined' && State.navigation.collaborativePageCreation && State.liveCollaboration.crdtReady",
                    timeout=30_000,
                )

                def observe_create(route):
                    if route.request.method == 'POST':
                        operations.append(route.request.post_data_json['operation_id'])
                        if lose_next_create['enabled'] and not lose_next_create['lost']:
                            time.sleep(0.6)
                            response = route.fetch()
                            if response.ok:
                                lose_next_create['lost'] = True
                                route.fulfill(status=503, json={'error': 'simulated creation response loss'})
                                return
                    route.continue_()

                def observe_archive(route):
                    if route.request.method == 'POST':
                        archives.append(route.request.post_data_json)
                    route.continue_()

                page.route(re.compile(r'/api/notion/workspaces/[^/]+/pages$'), observe_create)
                page.route(re.compile(r'/api/notion/workspaces/[^/]+/pages/[^/]+/trash$'), observe_archive)
                parent = page.locator(f'#ownPages .page-row[data-page-id="{parent_id}"]')
                parent.hover()
                parent.locator('[data-page-child]').click()
                expect(page.locator('#sidePeek')).to_be_visible()
                expect(page.locator('#sidePeekBackdrop')).to_be_visible()
                expect(page.locator('#sidePeek')).to_have_attribute('role', 'dialog')
                assert page.evaluate('() => document.querySelector(".notion-root").inert') is True
                rect = page.locator('#sidePeek').evaluate(
                    '(element) => { const r = element.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; }'
                )
                assert all(abs(a - b) < 2 for a, b in zip(rect, [152, 72, 976, 576]))
                expect(page.locator('#sidePeekTitle')).to_have_value('')
                expect(page.locator('#sidePeekTitle')).to_have_attribute('placeholder', '새 페이지')
                page.wait_for_function('() => Boolean(State.sidePeek.pageId)', timeout=30_000)
                child_id = page.evaluate('() => State.sidePeek.pageId')
                assert page.evaluate('() => new URL(location.href).searchParams.get("p")') == child_id
                assert page.evaluate('() => new URL(location.href).searchParams.get("showMoveTo")') == 'true'
                for action in ['sidePeekShareBtn', 'sidePeekCopyLinkBtn', 'sidePeekFavoriteBtn', 'sidePeekMoreBtn']:
                    button = page.locator('#' + action)
                    expect(button).to_be_visible()
                    expect(button).to_have_attribute('aria-disabled', 'false')
                    expect(button).to_be_enabled()
                assert child_id not in original_pages
                assert child_id in {item['id'] for item in native_page_items(authority, 'owner', workspace_id)}
                assert len(operations) == 1
                page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
                page.locator('#sidePeekCopyLinkBtn').click()
                assert page.evaluate('() => navigator.clipboard.readText()').endswith('/page/' + child_id)
                page.locator('#sidePeekShareBtn').click()
                expect(page.locator('#sidePeekSharePopover')).to_be_visible(timeout=30_000)
                page.locator('#sidePeekShareCloseBtn').click()
                expect(page.locator('#sidePeekSharePopover')).to_be_hidden()
                with page.expect_popup() as popup:
                    page.evaluate('(url) => window.open(url, "_blank")', origin + '/page/' + child_id)
                direct = popup.value
                direct.on('pageerror', lambda error: errors.append(str(error)))
                direct.wait_for_load_state('domcontentloaded')
                expect(direct.locator('#pageTitle')).to_have_value('새 페이지', timeout=30_000)

                page.reload(wait_until='domcontentloaded')
                expect(page.locator('#sidePeek')).to_be_visible(timeout=30_000)
                expect(page.locator('#sidePeek')).to_have_attribute('role', 'dialog')
                expect(page.locator('#sidePeekTitle')).to_have_value('')
                assert page.evaluate('() => State.sidePeek.pageId') == child_id
                assert page.evaluate('() => new URL(location.href).searchParams.get("p")') == child_id

                page.go_back()
                expect(page.locator('#sidePeek')).to_be_hidden(timeout=30_000)
                expect(page.locator('#sidePeekBackdrop')).to_be_hidden()
                assert page.evaluate('() => new URL(location.href).searchParams.has("p")') is False
                assert page.evaluate('() => document.querySelector(".notion-root").inert') is False
                expect(page.locator(f'#ownPages .page-row[data-page-id="{child_id}"]')).to_have_count(0)
                assert 1 <= len(archives) <= 3
                first_archive_count = len(archives)
                assert child_id not in {item['id'] for item in native_page_items(authority, 'owner', workspace_id)}
                trash = page.request.get(f'{origin}/api/notion/workspaces/{workspace_id}/trash')
                assert trash.ok and child_id in {item['id'] for item in trash.json()['items']}
                assert next(item for item in trash.json()['items'] if item['id'] == child_id)['archived_by_subject_id'] == 'owner'
                direct.reload(wait_until='domcontentloaded')
                expect(direct.locator('#archivedPageNotice')).to_be_visible(timeout=30_000)
                expect(direct.locator('#archivedPageTitle')).to_have_text('새 페이지')
                expect(direct.locator('#archivedPageMessage')).to_contain_text('님이 이 페이지를')
                expect(direct.locator('#archivedPageRestoreBtn')).to_be_visible()
                direct.locator('#pageCopyLinkBtn').click()
                assert direct.evaluate('() => navigator.clipboard.readText()').endswith('/page/' + child_id)
                notice_layout = direct.evaluate('''() => {
                  const rect = selector => {const r = document.querySelector(selector).getBoundingClientRect();
                    return {x:r.x,y:r.y,width:r.width,height:r.height};};
                  return {bar:rect('.archived-page-notice-bar'), title:rect('#archivedPageTitle'),
                    background:getComputedStyle(document.querySelector('.archived-page-notice-bar')).backgroundColor};
                }''')
                assert abs(notice_layout['bar']['y'] - 44) < 6, notice_layout
                assert abs(notice_layout['bar']['height'] - 44) < 8, notice_layout
                assert abs(notice_layout['title']['x'] - 424) < 20, notice_layout
                assert notice_layout['background'] == 'rgb(235, 87, 87)', notice_layout

                lose_next_create['enabled'] = True
                parent.hover()
                parent.locator('[data-page-child]').click()
                expect(page.locator('#sidePeek')).to_be_visible()
                page.set_viewport_size({'width': 390, 'height': 720})
                mobile_rect = page.locator('#sidePeek').evaluate(
                    '(element) => { const r = element.getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; }'
                )
                assert all(abs(a - b) < 2 for a, b in zip(mobile_rect, [0, 0, 390, 720]))
                page.locator('#sidePeekCloseBtn').click()
                expect(page.locator('#sidePeek')).to_be_hidden(timeout=30_000)
                page.reload(wait_until='domcontentloaded')
                assert {item['id'] for item in native_page_items(authority, 'owner', workspace_id)} == original_pages
                assert lose_next_create['lost']
                assert len(operations) == 3 and len(set(operations)) == 2
                assert operations[1] == operations[2]
                assert len(archives) > first_archive_count
                direct.locator('#archivedPageRestoreBtn').click()
                expect(direct.locator('#pageTitle')).to_have_value('새 페이지', timeout=30_000)
                assert {item['id'] for item in native_page_items(authority, 'owner', workspace_id)} == original_pages | {child_id}
                direct.close()

                page.set_viewport_size({'width': 1280, 'height': 720})
                parent.hover()
                parent.locator('[data-page-child]').click()
                page.wait_for_function('() => Boolean(State.sidePeek.pageId)', timeout=30_000)
                edited_child_id = page.evaluate('() => State.sidePeek.pageId')
                with page.expect_popup() as popup:
                    page.evaluate('(url) => window.open(url, "_blank")', origin + '/page/' + edited_child_id)
                editor = popup.value
                editor.wait_for_load_state('domcontentloaded')
                expect(editor.locator('#pageTitle')).to_have_value('새 페이지', timeout=30_000)
                editor.locator('#pageTitle').fill('다른 창에서 수정한 제목')
                editor.locator('#pageTitle').press('Tab')
                editor.wait_for_function('''async ({workspaceId, pageId}) => {
                  const response = await fetch(`/api/notion/workspaces/${workspaceId}/pages/${pageId}/collaboration`);
                  return response.ok && (await response.json()).page.title === '다른 창에서 수정한 제목';
                }''', arg={'workspaceId': workspace_id, 'pageId': edited_child_id}, timeout=30_000)
                page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
                expect(page.locator('#sidePeek')).to_be_visible()
                page.wait_for_function('() => String(State.sidePeek.saveError || "").includes("다른 창")', timeout=30_000)
                assert edited_child_id in {item['id'] for item in native_page_items(authority, 'owner', workspace_id)}
                editor.close()
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_collaborative_reserved_child_query_in_another_tab_archives_blank_page(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False, navigation_database_favorites=True) as (
        origin, password, authority, workspace_id, parent_id,
    ):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': 1280, 'height': 720})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin + '/page/' + parent_id, wait_until='domcontentloaded')
                page.locator('#userId').fill('owner')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                expect(page.locator('#pageTitle')).to_be_enabled(timeout=30_000)
                page.wait_for_function('() => typeof State !== "undefined" && State.navigation.collaborativePageCreation && State.liveCollaboration.crdtReady',
                                       timeout=30_000)

                parent = page.locator(f'#ownPages .page-row[data-page-id="{parent_id}"]')
                parent.hover()
                parent.locator('[data-page-child]').click()
                page.wait_for_function('() => Boolean(State.sidePeek.pageId)', timeout=30_000)
                child_id = page.evaluate('() => State.sidePeek.pageId')
                assert child_id in {item['id'] for item in native_page_items(authority, 'owner', workspace_id)}

                with page.expect_popup() as popup:
                    page.evaluate('() => window.open("about:blank", "_blank")')
                viewer = popup.value
                viewer.on('pageerror', lambda error: errors.append(str(error)))
                viewer.goto(origin + '/page/' + parent_id + '?p=' + child_id + '&showMoveTo=true',
                            wait_until='domcontentloaded')
                expect(viewer.locator('#sidePeek')).to_be_visible(timeout=30_000)
                expect(viewer.locator('#sidePeek')).to_have_attribute('role', 'dialog')
                expect(viewer.locator('#sidePeekTitle')).to_have_value('')
                assert viewer.evaluate('() => State.sidePeek.pageId') == child_id
                viewer.locator('#sidePeekTitle').press('Escape')
                expect(viewer.locator('#sidePeek')).to_be_hidden(timeout=30_000)
                assert viewer.evaluate('() => new URL(location.href).searchParams.has("p")') is False
                assert child_id not in {item['id'] for item in native_page_items(authority, 'owner', workspace_id)}
                trash = viewer.request.get(f'{origin}/api/notion/workspaces/{workspace_id}/trash')
                assert trash.ok and child_id in {item['id'] for item in trash.json()['items']}
                expect(page.locator('#sidePeekArchivedNotice')).to_be_visible(timeout=30_000)
                expect(page.locator('#sidePeekArchivedTitle')).to_have_text('새 페이지')
                expect(page.locator('#sidePeekArchivedMessage')).to_contain_text('휴지통으로 이동했습니다')
                layout = page.evaluate('''() => {
                  const rect = selector => {const r = document.querySelector(selector).getBoundingClientRect();
                    return {x:r.x,y:r.y,width:r.width,height:r.height};};
                  return {bar:rect('.side-peek-archived-bar'), title:rect('#sidePeekArchivedTitle'),
                    background:getComputedStyle(document.querySelector('.side-peek-archived-bar')).backgroundColor};
                }''')
                assert abs(layout['bar']['y'] - 116) < 8, layout
                assert abs(layout['bar']['height'] - 44) < 8, layout
                assert abs(layout['title']['x'] - 287) < 20, layout
                assert layout['background'] == 'rgb(235, 87, 87)', layout
                page.set_viewport_size({'width': 390, 'height': 720})
                assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
                expect(page.locator('#sidePeekArchivedRestoreBtn')).to_be_visible()
                page.set_viewport_size({'width': 1280, 'height': 720})
                page.reload(wait_until='domcontentloaded')
                expect(page.locator('#sidePeekArchivedNotice')).to_be_visible(timeout=30_000)
                assert page.evaluate('() => State.sidePeek.pageId') == child_id
                page.locator('#sidePeekArchivedRestoreBtn').click()
                expect(page.locator('#pageTitle')).to_have_value('새 페이지', timeout=30_000)
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_collaborative_row_plus_title_input_before_and_after_idle_survives_editor_handoff(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False, navigation_database_favorites=True) as (
        origin, password, authority, workspace_id, parent_id,
    ):
        original_pages = {item['id'] for item in native_page_items(authority, 'owner', workspace_id)}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': 1280, 'height': 800})
                errors, operations = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin + '/page/' + parent_id, wait_until='domcontentloaded')
                page.locator('#userId').fill('owner')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                expect(page.locator('#pageTitle')).to_be_enabled(timeout=30_000)
                page.wait_for_function(
                    "() => typeof State !== 'undefined' && State.navigation.collaborativePageCreation && State.liveCollaboration.crdtReady",
                    timeout=30_000,
                )

                def delay_create(route):
                    if route.request.method == 'POST':
                        operations.append(route.request.post_data_json['operation_id'])
                        time.sleep(0.8)
                    route.continue_()

                page.route(re.compile(r'/api/notion/workspaces/[^/]+/pages$'), delay_create)
                parent = page.locator(f'#ownPages .page-row[data-page-id="{parent_id}"]')
                parent.hover()
                parent.locator('[data-page-child]').click()
                title = page.locator('#sidePeekTitle')
                expect(title).to_be_visible(timeout=30_000)
                expect(title).to_be_focused()
                expect(title).to_have_value('')
                expect(title).to_have_attribute('placeholder', '새 페이지')
                draft_title_rect = title.evaluate(
                    '(element) => { const r = element.getBoundingClientRect(); return [r.x, r.y]; }'
                )
                page.keyboard.insert_text('바로 입력한 제목')
                page.wait_for_function('() => Boolean(State.sidePeek.pageId)', timeout=30_000)
                child_id = page.evaluate('() => State.sidePeek.pageId')
                frame_title = page.frame_locator('iframe.side-peek-editor-frame').locator('#pageTitle')
                expect(frame_title).to_have_value('바로 입력한 제목', timeout=30_000)
                expect(frame_title).to_be_focused()
                embedded_title_rect = page.evaluate('''() => {
                  const frame = document.querySelector('iframe.side-peek-editor-frame');
                  const outer = frame.getBoundingClientRect();
                  const inner = frame.contentDocument.querySelector('#pageTitle').getBoundingClientRect();
                  return [outer.x + inner.x, outer.y + inner.y];
                }''')
                embedded_layout = page.evaluate('''() => {
                  const frame = document.querySelector('iframe.side-peek-editor-frame');
                  const doc = frame.contentDocument;
                  const info = selector => { const element = doc.querySelector(selector);
                    if (!element) return null; const r = element.getBoundingClientRect();
                    const css = frame.contentWindow.getComputedStyle(element);
                    return {y:r.y,height:r.height,marginTop:css.marginTop,paddingTop:css.paddingTop,hidden:element.hidden}; };
                  return {frame:frame.getBoundingClientRect().y,view:info('#pageView'),meta:info('.page-meta'),
                    head:info('.page-head'),title:info('#pageTitle'),icon:info('.page-icon-button')};
                }''')
                assert all(abs(a - b) < 4 for a, b in zip(draft_title_rect, embedded_title_rect)), (
                    draft_title_rect, embedded_title_rect, embedded_layout
                )
                page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
                expect(page.locator('#sidePeek')).to_be_hidden(timeout=30_000)
                assert operations and len(set(operations)) == 1
                assert {item['id'] for item in native_page_items(authority, 'owner', workspace_id)} == original_pages | {child_id}
                page.reload(wait_until='domcontentloaded')
                page.locator(f'#ownPages .page-row[data-page-id="{child_id}"]').click()
                expect(page.locator('#pageTitle')).to_have_value('바로 입력한 제목', timeout=30_000)

                parent = page.locator(f'#ownPages .page-row[data-page-id="{parent_id}"]')
                parent.hover()
                parent.locator('[data-page-child]').click()
                title = page.locator('#sidePeekTitle')
                expect(title).to_be_visible(timeout=30_000)
                expect(title).to_be_focused()
                page.wait_for_timeout(1100)
                assert page.evaluate('() => Boolean(State.sidePeek.pageId)')
                assert len(set(operations)) == 2
                page.keyboard.insert_text('연결 후 입력한 제목')
                page.wait_for_function(
                    '(firstId) => Boolean(State.sidePeek.pageId && State.sidePeek.pageId !== firstId && State.sidePeek.embedded)',
                    arg=child_id,
                    timeout=30_000,
                )
                second_child_id = page.evaluate('() => State.sidePeek.pageId')
                frame_title = page.frame_locator('iframe.side-peek-editor-frame').locator('#pageTitle')
                expect(frame_title).to_have_value('연결 후 입력한 제목', timeout=30_000)
                page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
                expect(page.locator('#sidePeek')).to_be_hidden(timeout=30_000)
                page.reload(wait_until='domcontentloaded')
                page.locator(f'#ownPages .page-row[data-page-id="{second_child_id}"]').click()
                expect(page.locator('#pageTitle')).to_have_value('연결 후 입력한 제목', timeout=30_000)
                assert len(set(operations)) == 2
                assert {item['id'] for item in native_page_items(authority, 'owner', workspace_id)} == original_pages | {child_id, second_child_id}
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_collaborative_child_draft_survives_remote_parent_lock_and_retry(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect, sync_playwright

    if not os.environ.get('CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES'):
        try:
            available = metadata.version('uvicorn') == '0.52.4' and metadata.version('websockets') == '17.0.1'
        except metadata.PackageNotFoundError:
            available = False
        if not available:
            pytest.skip('exact selected gateway runtime is unavailable')

    with _isolated_collaborative_app(tmp_path, locked=False, navigation_database_favorites=True) as (
        origin, password, authority, workspace_id, parent_id,
    ):
        original_pages = {item['id'] for item in native_page_items(authority, 'owner', workspace_id)}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': 1280, 'height': 800})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin + '/page/' + parent_id, wait_until='domcontentloaded')
                page.locator('#userId').fill('owner')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                expect(page.locator('#pageTitle')).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' && State.navigation.collaborativePageCreation && State.liveCollaboration.crdtReady", timeout=30_000)

                locked_on_create = {'done': False}
                operations = []

                def create_route(route):
                    if route.request.method == 'POST':
                        operations.append(route.request.post_data_json['operation_id'])
                        if not locked_on_create['done']:
                            locked_on_create['done'] = True
                            update_page_settings(authority, 'owner', workspace_id, parent_id,
                                **_settings_request(authority, parent_id, {'locked': True}, 'browser-draft-lock-parent'))
                    route.continue_()

                page.route(re.compile(r'/api/notion/workspaces/[^/]+/pages$'), create_route)
                parent = page.locator(f'#ownPages .page-row[data-page-id="{parent_id}"]')
                expect(parent).to_be_visible(timeout=30_000)
                parent.hover()
                parent.locator('[data-page-child]').click()
                peek = page.locator('#sidePeek')
                expect(peek).to_be_visible()
                title = page.locator('#sidePeekTitle')
                body = page.locator('#sidePeekBlocks [data-side-text="0"]')
                title.fill('잠금 경합 하위 페이지')
                body.fill('잠금 중에도 보존할 본문')
                expect(page.locator('#sidePeekDraftStatus')).to_contain_text('저장하지 못했습니다.', timeout=30_000)
                assert locked_on_create['done']
                assert authority.read_document('owner', parent_id)['settings']['locked'] is True
                assert {item['id'] for item in native_page_items(authority, 'owner', workspace_id)} == original_pages
                assert page.evaluate('() => State.sidePeek.draft?.phase') == 'failed'
                expect(title).to_have_value('잠금 경합 하위 페이지')
                expect(body).to_have_value('잠금 중에도 보존할 본문')
                page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
                expect(peek).to_be_visible()
                expect(title).to_have_value('잠금 경합 하위 페이지')
                expect(body).to_have_value('잠금 중에도 보존할 본문')

                update_page_settings(authority, 'owner', workspace_id, parent_id,
                    **_settings_request(authority, parent_id, {'locked': False}, 'browser-draft-unlock-parent'))
                page.locator('#sidePeekDraftRetry').click()
                page.wait_for_function('() => Boolean(State.sidePeek.pageId)', timeout=30_000)
                child_id = page.evaluate('() => State.sidePeek.pageId')
                assert child_id != parent_id
                assert {item['id'] for item in native_page_items(authority, 'owner', workspace_id)} == original_pages | {child_id}
                assert len(operations) >= 2 and len(set(operations)) == 1
                page.wait_for_function('() => !State.sidePeek.dirty && !State.sidePeek.creationFlight', timeout=30_000)
                page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
                expect(peek).to_be_hidden(timeout=30_000)
                expect(page.locator(f'#ownPages .page-row[data-page-id="{child_id}"]')).to_be_visible(timeout=30_000)
                page.locator(f'#ownPages .page-row[data-page-id="{child_id}"]').click()
                expect(page.locator('#pageTitle')).to_have_value('잠금 경합 하위 페이지', timeout=30_000)
                expect(page.locator('#blocks .block-editor')).to_have_text('잠금 중에도 보존할 본문', timeout=30_000)
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('access_change', ['revoke', 'view'])
def test_ui_collaborative_child_draft_survives_team_membership_revoke_and_retry(tmp_path, access_change):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect, sync_playwright

    if not os.environ.get('CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES'):
        try:
            available = metadata.version('uvicorn') == '0.52.4' and metadata.version('websockets') == '17.0.1'
        except metadata.PackageNotFoundError:
            available = False
        if not available:
            pytest.skip('exact selected gateway runtime is unavailable')

    with _isolated_collaborative_app(tmp_path, locked=False, navigation_database_favorites=True) as (
        origin, password, authority, workspace_id, _private_page_id,
    ):
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root, identity_directory=authority.identity_directory)
        version = lambda: store.get_workspace('owner', workspace_id)['version']
        teamspace_id = store.create_teamspace('owner', workspace_id, name='Draft race team',
            visibility='closed', base_version=version(), operation_id='browser-draft-team-create')['teamspace']['id']
        store.set_teamspace_member('owner', workspace_id, teamspace_id, 'content-editor', role='member',
            base_version=version(), operation_id='browser-draft-team-join')
        parent_id = create_page(authority, 'owner', workspace_id, title='Team draft parent',
            teamspace_id=teamspace_id, base_version=version(), operation_id='browser-draft-team-parent')['page']['id']
        original_pages = {item['id'] for item in native_page_items(authority, 'owner', workspace_id)}

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': 1280, 'height': 800})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin + '/page/' + parent_id, wait_until='domcontentloaded')
                page.locator('#userId').fill('content-editor')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                page.wait_for_function("() => typeof State !== 'undefined' && State.user?.id === 'content-editor' && State.navigation.enabled", timeout=30_000)
                destination = page.evaluate("""async ({workspaceId, pageId}) => {
                  const switched = await switchCanonicalWorkspace(workspaceId);
                  if (switched) await openPage(pageId, false, PageFocusPolicy.PRESERVE);
                  return {switched, selected: State.navigation.workspaceId};
                }""", {'workspaceId': workspace_id, 'pageId': parent_id})
                assert destination['switched'] and destination['selected'] == workspace_id, destination
                page.wait_for_function("() => State.navigation.collaborativePageCreation && State.liveCollaboration.crdtReady", timeout=30_000)
                parent = page.locator(f'.page-row[data-page-id="{parent_id}"]').first
                expect(parent).to_be_visible(timeout=30_000)

                revoked_on_create = {'done': False}
                operations = []

                def create_route(route):
                    if route.request.method == 'POST':
                        operations.append(route.request.post_data_json['operation_id'])
                        if not revoked_on_create['done']:
                            revoked_on_create['done'] = True
                            store.remove_teamspace_member('owner', workspace_id, teamspace_id, 'content-editor',
                                base_version=version(), operation_id='browser-draft-team-revoke')
                            if access_change == 'view':
                                witness = authority.read_document('owner', parent_id)
                                authority.set_page_grant('owner', parent_id, 'subject', 'content-editor', role='view',
                                    inherit_to_descendants=False,
                                    expected_workspace_epoch=witness['workspace_epoch'],
                                    expected_document_epoch=witness['document_epoch'],
                                    operation_id='browser-draft-team-view')
                    route.continue_()

                page.route(re.compile(r'/api/notion/workspaces/[^/]+/pages$'), create_route)
                parent.hover()
                parent.locator('[data-page-child]').click()
                peek = page.locator('#sidePeek')
                expect(peek).to_be_visible()
                title = page.locator('#sidePeekTitle')
                body = page.locator('#sidePeekBlocks [data-side-text="0"]')
                title.fill('권한 회수 중인 하위 페이지')
                body.fill('권한이 돌아오면 저장할 본문')
                expect(page.locator('#sidePeekDraftStatus')).to_contain_text('저장하지 못했습니다.', timeout=30_000)
                assert revoked_on_create['done']
                if access_change == 'revoke':
                    with pytest.raises(NotFoundOrDenied):
                        authority.read_document('content-editor', parent_id)
                else:
                    assert authority.resolve_access('content-editor', parent_id).role == 'view'
                    assert authority.read_document('content-editor', parent_id)['document_id'] == parent_id
                assert {item['id'] for item in native_page_items(authority, 'owner', workspace_id)} == original_pages
                assert page.evaluate('() => State.sidePeek.draft?.phase') == 'failed'
                expect(title).to_have_value('권한 회수 중인 하위 페이지')
                expect(body).to_have_value('권한이 돌아오면 저장할 본문')
                page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
                expect(peek).to_be_visible()

                store.set_teamspace_member('owner', workspace_id, teamspace_id, 'content-editor', role='member',
                    base_version=version(), operation_id='browser-draft-team-rejoin')
                page.locator('#sidePeekDraftRetry').click()
                page.wait_for_function('() => Boolean(State.sidePeek.pageId)', timeout=30_000)
                child_id = page.evaluate('() => State.sidePeek.pageId')
                assert child_id != parent_id
                assert authority.read_document('content-editor', child_id)['parent_document_id'] == parent_id
                assert {item['id'] for item in native_page_items(authority, 'owner', workspace_id)} == original_pages | {child_id}
                assert len(operations) >= 2 and len(set(operations)) == 1
                page.wait_for_function('() => !State.sidePeek.dirty && !State.sidePeek.creationFlight', timeout=30_000)
                page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
                expect(peek).to_be_hidden(timeout=30_000)
                page.goto(origin + '/page/' + child_id, wait_until='domcontentloaded')
                expect(page.locator('#pageTitle')).to_have_value('권한 회수 중인 하위 페이지', timeout=30_000)
                expect(page.locator('#blocks .block-editor')).to_have_text('권한이 돌아오면 저장할 본문', timeout=30_000)
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r5_open_sidebar_menu_rechecks_access_after_membership_revoke(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False, navigation_database_favorites=True,
                                    gateway_enabled=False) as (
        origin, password, authority, workspace_id, _private_page_id,
    ):
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root, identity_directory=authority.identity_directory)
        version = lambda: store.get_workspace('owner', workspace_id)['version']
        teamspace_id = store.create_teamspace('owner', workspace_id, name='Sidebar access team',
            visibility='closed', base_version=version(), operation_id='sidebar-menu-team-create')['teamspace']['id']
        store.set_teamspace_member('owner', workspace_id, teamspace_id, 'content-editor', role='member',
            base_version=version(), operation_id='sidebar-menu-team-join')
        parent_id = create_page(authority, 'owner', workspace_id, title='Sidebar access parent',
            teamspace_id=teamspace_id, base_version=version(), operation_id='sidebar-menu-parent')['page']['id']

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': 1280, 'height': 800})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin + '/page/' + parent_id, wait_until='domcontentloaded')
                page.locator('#userId').fill('content-editor')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                page.wait_for_function("() => typeof State !== 'undefined' && State.user?.id === 'content-editor' && State.navigation.enabled",
                    timeout=30_000)
                switched = page.evaluate("""async ({workspaceId, pageId}) => {
                  const ok = await switchCanonicalWorkspace(workspaceId);
                  if (ok) await openPage(pageId, false, PageFocusPolicy.PRESERVE);
                  return ok;
                }""", {'workspaceId': workspace_id, 'pageId': parent_id})
                assert switched
                row = page.locator(f'.page-row[data-page-id="{parent_id}"]').first
                expect(row).to_be_visible(timeout=30_000)
                row.hover()
                expect(row.locator('[data-page-child]')).to_be_visible()
                row.locator('[data-page-menu]').click()
                menu = page.locator('#pageMenu')
                expect(menu).to_be_visible()
                expect(menu.locator('[data-page-action="rename"]')).to_be_visible()

                store.remove_teamspace_member('owner', workspace_id, teamspace_id, 'content-editor',
                    base_version=version(), operation_id='sidebar-menu-team-revoke')
                page.evaluate("refreshPageNavigation(JSON.stringify([State.user.id, State.navigation.workspaceId]))")
                expect(menu).to_be_hidden(timeout=30_000)
                expect(page.locator(f'.page-row[data-page-id="{parent_id}"]')).to_have_count(0)

                witness = authority.read_document('owner', parent_id)
                authority.set_page_grant('owner', parent_id, 'subject', 'content-editor', role='view',
                    inherit_to_descendants=False,
                    expected_workspace_epoch=witness['workspace_epoch'],
                    expected_document_epoch=witness['document_epoch'],
                    operation_id='sidebar-menu-view-grant')
                page.evaluate("refreshPageNavigation(JSON.stringify([State.user.id, State.navigation.workspaceId]))")
                read_only = page.locator(f'.page-row[data-page-id="{parent_id}"]').first
                expect(read_only).to_be_visible(timeout=30_000)
                expect(read_only.locator('[data-page-child]')).to_have_count(0)
                read_only.locator('[data-page-menu]').click()
                expect(menu).to_be_visible()
                expect(menu.locator('[data-page-action="rename"]')).to_have_count(0)
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_ui_r11_sidebar_focus_moves_to_same_page_when_add_permission_is_removed(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False,
            navigation_database_favorites=True, gateway_enabled=False) as (
        origin, password, authority, workspace_id, _private_page_id,
    ):
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root, identity_directory=authority.identity_directory)
        version = lambda: store.get_workspace('owner', workspace_id)['version']
        teamspace_id = store.create_teamspace('owner', workspace_id, name='Focus transition team',
            visibility='closed', base_version=version(), operation_id='sidebar-focus-team-create')['teamspace']['id']
        store.set_teamspace_member('owner', workspace_id, teamspace_id, 'content-editor', role='member',
            base_version=version(), operation_id='sidebar-focus-team-join')
        parent_id = create_page(authority, 'owner', workspace_id, title='Focus transition page',
            teamspace_id=teamspace_id, base_version=version(), operation_id='sidebar-focus-page-create')['page']['id']
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': 1280, 'height': 800})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin + '/page/' + parent_id, wait_until='domcontentloaded')
                page.locator('#userId').fill('content-editor')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                page.wait_for_function("() => typeof State !== 'undefined' && State.navigation.enabled", timeout=30_000)
                page.evaluate("""async ({workspaceId, pageId}) => {
                  await switchCanonicalWorkspace(workspaceId);
                  await openPage(pageId, false, PageFocusPolicy.PRESERVE);
                }""", {'workspaceId': workspace_id, 'pageId': parent_id})
                row = page.locator(f'.page-row[data-page-id="{parent_id}"]:not(.compact)').first
                expect(row).to_be_visible(timeout=30_000)
                add = row.locator('[data-page-child]')
                expect(add).to_be_visible()
                add.focus()
                expect(add).to_be_focused()
                store.remove_teamspace_member('owner', workspace_id, teamspace_id, 'content-editor',
                    base_version=version(), operation_id='sidebar-focus-team-leave')
                witness = authority.read_document('owner', parent_id)
                authority.set_page_grant('owner', parent_id, 'subject', 'content-editor', role='view',
                    inherit_to_descendants=False, expected_workspace_epoch=witness['workspace_epoch'],
                    expected_document_epoch=witness['document_epoch'], operation_id='sidebar-focus-read-only')
                page.evaluate("refreshPageNavigation(JSON.stringify([State.user.id, State.navigation.workspaceId]))")
                read_only = page.locator(f'.page-row[data-page-id="{parent_id}"]:not(.compact)').first
                expect(read_only).to_be_visible()
                expect(read_only.locator('[data-page-child]')).to_have_count(0)
                expect(read_only).to_be_focused()
                read_only.press('Shift+F10')
                expect(page.locator('#pageMenu')).to_be_visible()
                expect(page.locator('#pageMenu [data-page-action="rename"]')).to_have_count(0)
                assert not errors, errors
            finally:
                browser.close()
