"""UI-SYNC-DB-DETACH-LINKED-ROW: resolve and mutate the physical row owner."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from code_assist_v1.tests.browser.synced_toolbar_helpers import open_synced_actions

from code_assist_v1.notion_collaboration import synced_blocks as synced
from code_assist_v1.tests.browser.test_notion_synced_database_detach_browser import DETACH_FLAGS, proof
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.browser.test_notion_synced_linked_navigation import login, settled
from code_assist_v1.tests.browser.test_notion_synced_linked_template_mount import prepare
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original, reference


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
def test_linked_row_detach_updates_only_physical_owner_and_leaves_other_reference_live(tmp_path):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect, sync_playwright
    with _isolated_collaborative_app(tmp_path, **DETACH_FLAGS) as (origin, password, authority, workspace, initial):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width':390, 'height':844})
                errors = []; page.on('pageerror', lambda error:errors.append(str(error)))
                login(page, origin, initial, 'owner', password); page.evaluate("applyColorTheme('dark')")
                source, visible = prepare(page, authority, initial)
                document, database = source['pageId'], source['databaseId']
                row = authority.read_database('owner', database)['rows'][0]
                row_id = row['id']
                authority.execute_surface_command('owner', document, database_id=database,
                    command_kind='database.row.content.set', target_id=row_id,
                    payload={'blocks':[original('linked-detach-row-original','연결 항목 원본 A'),
                        reference('linked-detach-row-peer',document,'linked-detach-row-original'),
                        reference('linked-detach-row-keep',document,'linked-detach-row-original')]},
                    expected_target_version=row['version'], operation_id='linked-detach-row-seed',
                    **request(SimpleNamespace(iam=authority), document))
                page.reload(wait_until='domcontentloaded'); settled(page)
                page.locator(f'[data-linked-row-id="{row_id}"]').first.click()
                root = page.locator('[data-item-content]')
                button = root.locator('[data-synced-reference="linked-detach-row-peer"] [data-synced-detach]')
                try:
                    open_synced_actions(root.locator('[data-synced-reference="linked-detach-row-peer"]'));expect(button).to_be_visible(timeout=30_000); expect(button).to_be_enabled()
                    before = deepcopy(authority.read_document('owner', visible))
                    with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/synced-blocks/linked-detach-row-peer/detach')) as detached:
                        button.click()
                    result = detached.value.json(); assert detached.value.status == 201, result
                    assert result['document_id'] == document
                    assert result['location'] == {'kind':'row','database_id':database,'row_id':row_id}
                    expect(root.locator('[data-synced-reference="linked-detach-row-peer"]')).to_have_count(0,timeout=30_000)
                    expect(root.locator('[data-body-text="linked-detach-row-peer"]')).to_have_text('연결 항목 원본 A',timeout=30_000)
                    text = root.locator('[data-synced-reference="linked-detach-row-keep"] [data-synced-text="linked-detach-row-original"]')
                    expect(text).to_have_attribute('contenteditable','true',timeout=30_000)
                    with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/synced-blocks/linked-detach-row-keep/text')) as edited:
                        text.fill('연결 항목 원본 C')
                    assert edited.value.status == 201, edited.value.text()
                    expect(root.locator('[data-body-text="linked-detach-row-original"]')).to_have_text('연결 항목 원본 C',timeout=30_000)
                    expect(root.locator('[data-body-text="linked-detach-row-peer"]')).to_have_text('연결 항목 원본 A')
                    page.wait_for_function("() => !ItemBodyUi?.synced?.pending()",timeout=30_000)
                    assert authority.read_document('owner', visible) == before
                    assert page.evaluate('State.activePageId') == visible
                    latest = authority.read_database('owner', database)['rows'][0]
                    index = synced.index_blocks(latest['body']['blocks'])
                    assert index['linked-detach-row-original']['content'] == '연결 항목 원본 C'
                    assert index['linked-detach-row-peer']['type'] == 'paragraph'
                    assert not errors
                    proof(page,'detach-linked-row-dark-390',tmp_path,errors,result)
                except Exception:
                    proof(page,'detach-linked-row-failure',tmp_path,errors); raise
            finally:
                browser.close()
