"""Sidebar filtering preserves the open collaborative document and its input."""

import os

import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import (
    _isolated_collaborative_app,
)
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.page_creation import create_page
from code_assist_v1.notion_collaboration.page_appearance import update_page_appearance


@pytest.mark.integration
@pytest.mark.regression
def test_collaborative_sidebar_filter_keeps_open_document_and_editor(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        pytest.skip("exact selected gateway runtime is unavailable")

    with _isolated_collaborative_app(tmp_path, locked=False,
                                    navigation_database_favorites=True, navigation_ui=True) as (
        origin, password, _authority, _workspace_id, page_id,
    ):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(locale="ko-KR", viewport={"width": 1280, "height": 800})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(origin + "/page/" + page_id, wait_until="domcontentloaded")
                page.locator("#userId").fill("owner")
                page.locator("#password").fill(password)
                page.locator("#submitBtn").click()
                expect(page.locator("#pageTitle")).to_be_enabled(timeout=30_000)
                page.wait_for_function("() => typeof State !== 'undefined' && State.navigation.enabled && State.liveCollaboration.crdtReady", timeout=30_000)
                editor = page.locator("#blocks .block-editor").first
                editor.fill("필터 중에도 보존할 작성 내용")
                # Complete our own CRDT echo before measuring filter-only DOM changes.
                page.evaluate("async () => { await State.liveCollaboration.persistenceChain; }")
                page.wait_for_function("""() => {
                  const live = State.liveCollaboration;
                  return live.unsyncedCount === 0 && !live.syncPending
                    && !live.durableReplayPending && live.replayCursor >= live.durableCursor;
                }""", timeout=30_000)
                editor.evaluate("element => { element.dataset.filterWitness = 'kept'; }")
                blocks_before = page.evaluate("() => JSON.stringify(State.pageData.blocks)")
                page.evaluate("""async () => {
                  document.querySelector('#pageSearch').value = '일치하지 않는 페이지 검색';
                  await loadPages();
                }""")
                assert page.evaluate("() => State.activePageId") == page_id
                expect(page.locator("#pageTitle")).to_be_visible()
                expect(editor).to_have_text("필터 중에도 보존할 작성 내용")
                expect(editor).to_have_attribute("data-filter-witness", "kept")
                assert page.evaluate("() => JSON.stringify(State.pageData.blocks)") == blocks_before
                assert page.evaluate("() => State.pages.length") == 0
                page.evaluate("""async () => {
                  document.querySelector('#pageSearch').value = '';
                  await loadPages();
                }""")
                assert page.evaluate("() => State.activePageId") == page_id
                expect(editor).to_have_attribute("data-filter-witness", "kept")
                assert not errors, errors
            finally:
                browser.close()


@pytest.mark.integration
@pytest.mark.regression
def test_collaborative_filtered_page_reference_survives_body_redraw(tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import expect, sync_playwright

    if not os.environ.get("CODE_ASSIST_TEST_GATEWAY_SITEPACKAGES"):
        pytest.skip("exact selected gateway runtime is unavailable")

    with _isolated_collaborative_app(tmp_path, locked=False,
                                    navigation_database_favorites=True, navigation_ui=True) as (
        origin, password, authority, workspace_id, parent_id,
    ):
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root, identity_directory=authority.identity_directory)
        child = create_page(authority, 'owner', workspace_id, title='협업 필터 밖 링크',
            parent_id=parent_id, base_version=store.get_workspace('owner', workspace_id)['version'],
            operation_id='filter-reference-child-create')['page']

        def set_icon(icon, operation_id):
            fresh = authority.read_document('owner', child['id'])
            update_page_appearance(authority, 'owner', workspace_id, child['id'],
                updates={'icon': icon}, operation_id=operation_id,
                expected_document_sequence=fresh['document_sequence'],
                expected_workspace_epoch=fresh['workspace_epoch'],
                expected_document_epoch=fresh['document_epoch'])

        set_icon('📌', 'filter-reference-child-pin')
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
                page.wait_for_function("() => typeof State !== 'undefined' && State.navigation.enabled && State.liveCollaboration.crdtReady", timeout=30_000)
                page.evaluate("""async child => {
                  insertChildPageLinkInEditor(child, State.activePageId);
                  await State.liveCollaboration.typedCommandChain;
                  await State.liveCollaboration.persistenceChain;
                }""", child)
                page.wait_for_function("""() => {
                  const live = State.liveCollaboration;
                  return live.unsyncedCount === 0 && !live.syncPending
                    && !live.durableReplayPending && live.replayCursor >= live.durableCursor;
                }""", timeout=30_000)
                reference = page.locator(f'#blocks [data-page-reference="{child["id"]}"]')
                expect(reference).to_contain_text('협업 필터 밖 링크')
                expect(reference.locator('img.notion-emoji-image')).to_be_visible()
                original_blocks = page.evaluate('() => JSON.stringify(State.pageData.blocks)')
                page.evaluate("""async () => {
                  document.querySelector('#pageSearch').value = '일치하지 않는 페이지 검색';
                  await loadPages();
                  renderBlocks();
                }""")
                expect(reference).to_contain_text('협업 필터 밖 링크')
                expect(reference.locator('img.notion-emoji-image')).to_be_visible()
                assert page.evaluate('() => State.pages.length') == 0
                set_icon('', 'filter-reference-child-icon-remove')
                page.evaluate('async () => { await loadPages(); renderBlocks(); }')
                expect(reference.locator('img.notion-emoji-image')).to_have_count(0)
                expect(reference.locator('svg[data-icon-id="doc"]')).to_be_visible()
                assert page.evaluate('() => State.activePageId') == parent_id
                assert page.evaluate('() => JSON.stringify(State.pageData.blocks)') == original_blocks
                assert not errors, errors
            finally:
                browser.close()
