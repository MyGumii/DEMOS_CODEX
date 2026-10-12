"""Collaborative page icon upload must survive a failed appearance commit."""

from io import BytesIO

import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app


@pytest.mark.integration
@pytest.mark.regression
def test_collaborative_icon_apply_failure_reuses_uploaded_file(tmp_path):
    from PIL import Image
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False, navigation_database_favorites=True) as (
        origin, password, _authority, workspace_id, page_id,
    ):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': 1280, 'height': 800})
                page.goto(origin + '/page/' + page_id, wait_until='domcontentloaded')
                page.locator('#userId').fill('owner')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                expect(page.locator('#pageTitle')).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' && State.navigation.collaborativeFiles && State.liveCollaboration.crdtReady", timeout=30_000)
                page.locator('#addIconBtn').click()
                picker = page.locator('#pageIconPicker')
                expect(picker).to_be_visible()
                page.wait_for_function("() => !!State.pageData?.page?.icon && !State.saving", timeout=30_000)
                uploads = []
                failed = []
                page.on('request', lambda request: uploads.append(request.url)
                        if request.method == 'POST' and request.url.endswith(f'/pages/{page_id}/files/uploads') else None)

                def fail_first_apply(route):
                    if route.request.method == 'PATCH' and not failed:
                        failed.append(route.request.url)
                        route.fulfill(status=503, content_type='application/json', body='{"error":"temporary collaborative icon failure"}')
                    else:
                        route.continue_()

                page.route(f'**/api/notion/workspaces/{workspace_id}/pages/{page_id}/appearance', fail_first_apply)
                image = BytesIO()
                Image.new('RGB', (8, 8), '#58a6cc').save(image, 'PNG')
                picker.locator('[data-icon-tab="upload"]').click()
                picker.locator('input[aria-label="아이콘 이미지 파일"]').set_input_files({
                    'name': 'retry-collab-icon.png', 'mimeType': 'image/png', 'buffer': image.getvalue(),
                })
                expect(picker.locator('.icon-upload-preview')).to_be_visible()
                picker.locator('[data-icon-apply]').click()
                expect(picker.locator('.icon-picker-status')).to_contain_text('temporary collaborative icon failure', timeout=30_000)
                assert len(uploads) == len(failed) == 1
                picker.locator('[data-icon-apply]').click()
                expect(picker).to_be_hidden(timeout=30_000)
                page.wait_for_function("() => State.pageData?.page?.icon?.startsWith('nf:')", timeout=30_000)
                assert len(uploads) == 1
                expect(page.locator('#pageIconBtn img.notion-icon-image')).to_be_visible()
                page.reload(wait_until='domcontentloaded')
                expect(page.locator('#pageIconBtn img.notion-icon-image')).to_be_visible(timeout=30_000)
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_collaborative_child_icon_apply_failure_keeps_one_upload(tmp_path):
    from PIL import Image
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False, navigation_database_favorites=True) as (
        origin, password, _authority, workspace_id, parent_id,
    ):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': 1280, 'height': 800})
                page.goto(origin + '/page/' + parent_id, wait_until='domcontentloaded')
                page.locator('#userId').fill('owner')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                expect(page.locator('#pageTitle')).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' && State.navigation.collaborativePageCreation && State.liveCollaboration.crdtReady", timeout=30_000)
                parent = page.locator(f'#ownPages .page-row[data-page-id="{parent_id}"]')
                parent.hover()
                parent.locator('[data-page-child]').click()
                expect(page.locator('#sidePeek')).to_be_visible()
                page.wait_for_function("() => !!State.sidePeek.pageId && !State.sidePeek.creationFlight && !State.sidePeek.dirty", timeout=30_000)
                assert page.evaluate('!State.sidePeek.embedded')
                child_id = page.evaluate('State.sidePeek.pageId')
                assert child_id != parent_id
                uploads = []
                failed = []
                page.on('request', lambda request: uploads.append(request.url)
                        if request.method == 'POST' and request.url.endswith(f'/pages/{child_id}/files/uploads') else None)

                def fail_first_apply(route):
                    if route.request.method == 'PATCH' and not failed:
                        failed.append(route.request.url)
                        route.fulfill(status=503, content_type='application/json', body='{"error":"temporary child icon failure"}')
                    else:
                        route.continue_()

                page.route(f'**/api/notion/workspaces/{workspace_id}/pages/{child_id}/appearance', fail_first_apply)
                page.locator('#sidePeekIconBtn').click()
                picker = page.locator('#pageIconPicker')
                expect(picker).to_be_visible()
                picker.locator('[data-icon-tab="upload"]').click()
                image = BytesIO()
                Image.new('RGB', (8, 8), '#bd679f').save(image, 'PNG')
                picker.locator('input[aria-label="아이콘 이미지 파일"]').set_input_files({
                    'name': 'retry-child-icon.png', 'mimeType': 'image/png', 'buffer': image.getvalue(),
                })
                expect(picker.locator('.icon-upload-preview')).to_be_visible()
                picker.locator('[data-icon-apply]').click()
                expect(picker.locator('.icon-picker-status')).to_contain_text('temporary child icon failure', timeout=30_000)
                assert len(uploads) == len(failed) == 1
                picker.locator('[data-icon-apply]').click()
                expect(picker).to_be_hidden(timeout=30_000)
                page.wait_for_function("() => State.sidePeek.data?.page?.icon?.startsWith('nf:')", timeout=30_000)
                assert len(uploads) == 1
                expect(page.frame_locator('iframe.side-peek-editor-frame').locator('#pageIconBtn img.notion-icon-image')).to_be_visible(timeout=30_000)
                expect(page.locator(f'#ownPages .page-row[data-page-id="{child_id}"] img.notion-icon-image')).to_be_visible(timeout=30_000)
                page.locator('#sidePeekBackdrop').click(position={'x': 20, 'y': 20})
                expect(page.locator('#sidePeek')).to_be_hidden(timeout=30_000)
                page.reload(wait_until='domcontentloaded')
                expect(page.locator(f'#ownPages .page-row[data-page-id="{child_id}"] img.notion-icon-image')).to_be_visible(timeout=30_000)
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_collaborative_icon_apply_respects_role_downgrade_after_upload(tmp_path):
    from PIL import Image
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False, navigation_database_favorites=True) as (
        origin, password, authority, workspace_id, page_id,
    ):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': 1280, 'height': 800})
                page.goto(origin + '/page/' + page_id, wait_until='domcontentloaded')
                page.locator('#userId').fill('schema-editor')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                expect(page.locator('#pageTitle')).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' && State.navigation.collaborativeFiles && State.liveCollaboration.crdtReady", timeout=30_000)
                page.locator('#addIconBtn').click()
                expect(page.locator('#pageIconPicker')).to_be_visible()
                page.wait_for_function("() => !!State.pageData?.page?.icon && !State.saving", timeout=30_000)
                initial_icon = authority.read_document('owner', page_id)['icon']
                uploads = []
                downgraded = []
                page.on('request', lambda request: uploads.append(request.url)
                        if request.method == 'POST' and request.url.endswith(f'/pages/{page_id}/files/uploads') else None)

                def downgrade_before_apply(route):
                    if route.request.method == 'PATCH' and not downgraded:
                        witness = authority.read_document('owner', page_id)
                        authority.set_page_grant(
                            'owner', page_id, 'subject', 'schema-editor', role='view',
                            inherit_to_descendants=False,
                            expected_workspace_epoch=witness['workspace_epoch'],
                            expected_document_epoch=witness['document_epoch'],
                            operation_id='browser-icon-role-downgrade',
                        )
                        downgraded.append(route.request.url)
                    route.continue_()

                page.route(f'**/api/notion/workspaces/{workspace_id}/pages/{page_id}/appearance', downgrade_before_apply)
                picker = page.locator('#pageIconPicker')
                picker.locator('[data-icon-tab="upload"]').click()
                image = BytesIO()
                Image.new('RGB', (8, 8), '#5f8fba').save(image, 'PNG')
                picker.locator('input[aria-label="아이콘 이미지 파일"]').set_input_files({
                    'name': 'downgraded-icon.png', 'mimeType': 'image/png', 'buffer': image.getvalue(),
                })
                expect(picker.locator('.icon-upload-preview')).to_be_visible()
                picker.locator('[data-icon-apply]').click()
                page.wait_for_function("() => !document.querySelector('#pageTitle')?.matches(':enabled')", timeout=30_000)
                expect(picker).to_be_hidden(timeout=10_000)
                assert len(uploads) == len(downgraded) == 1
                assert authority.read_document('owner', page_id)['icon'] == initial_icon
                page.reload(wait_until='domcontentloaded')
                expect(page.locator('#pageTitle')).to_be_disabled(timeout=30_000)
                assert authority.read_document('owner', page_id)['icon'] == initial_icon
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_collaborative_side_peek_icon_picker_closes_after_role_downgrade(tmp_path):
    from PIL import Image
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False, navigation_database_favorites=True) as (
        origin, password, authority, _workspace_id, page_id,
    ):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': 1280, 'height': 800})
                page.goto(origin + '/page/' + page_id, wait_until='domcontentloaded')
                page.locator('#userId').fill('schema-editor')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                expect(page.locator('#pageTitle')).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' && State.liveCollaboration.crdtReady", timeout=30_000)
                row = page.locator(f'.page-row[data-page-id="{page_id}"]').first
                expect(row).to_be_visible(timeout=10_000)
                row.hover()
                row.locator('[data-page-menu]').click()
                page.locator('#pageMenu [data-page-action="side_peek"]').click()
                expect(page.locator('#sidePeek')).to_be_visible(timeout=30_000)
                frame = page.frame_locator('iframe.side-peek-editor-frame')
                expect(frame.locator('#pageTitle')).to_be_enabled(timeout=30_000)
                frame.locator('#addIconBtn').click()
                picker = frame.locator('#pageIconPicker')
                expect(picker).to_be_visible(timeout=30_000)
                image = BytesIO()
                Image.new('RGB', (8, 8), '#4e87b5').save(image, 'PNG')
                picker.locator('[data-icon-tab="upload"]').click()
                picker.locator('input[aria-label="아이콘 이미지 파일"]').set_input_files({
                    'name': 'side-peek-unsaved-icon.png', 'mimeType': 'image/png', 'buffer': image.getvalue(),
                })
                expect(picker.locator('.icon-upload-preview')).to_be_visible()
                initial_icon = authority.read_document('owner', page_id)['icon']
                witness = authority.read_document('owner', page_id)
                authority.set_page_grant(
                    'owner', page_id, 'subject', 'schema-editor', role='view',
                    inherit_to_descendants=False,
                    expected_workspace_epoch=witness['workspace_epoch'],
                    expected_document_epoch=witness['document_epoch'],
                    operation_id='browser-side-peek-icon-role-downgrade',
                )
                expect(frame.locator('#pageTitle')).to_be_disabled(timeout=30_000)
                expect(picker).to_be_hidden(timeout=15_000)
                assert authority.read_document('owner', page_id)['icon'] == initial_icon
            finally:
                browser.close()
