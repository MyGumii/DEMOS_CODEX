"""R3/E: real isolated notifications, filters, exact navigation and read receipts."""
import os
from pathlib import Path

import pytest

from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.page_creation import create_page
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize(('theme', 'width'), [('light', 1280), ('dark', 390)])
def test_ui_inbox_filters_open_exact_notification_and_persist_server_read(tmp_path, theme, width):
    from playwright.sync_api import expect, sync_playwright

    with _isolated_collaborative_app(tmp_path, locked=False, navigation_database_favorites=True) as (
        origin, password, authority, workspace_id, target_id,
    ):
        target = authority.read_document('owner', target_id)
        original_blocks = target['blocks']
        epochs = dict(expected_workspace_epoch=target['workspace_epoch'], expected_document_epoch=target['document_epoch'])
        authority.add_comment('content-editor', target_id, 'inbox-mention',
            content='@owner 확인할 알림', mention_subject_ids=['owner'],
            operation_id='browser-inbox-mention-create', **epochs)
        authority.add_comment('owner', target_id, 'inbox-reaction-target',
            content='반응할 댓글', mention_subject_ids=[],
            operation_id='browser-inbox-own-comment-create', **epochs)
        own_comment = next(item for item in authority.list_comments('owner', target_id)['items']
            if item['id'] == 'inbox-reaction-target')
        authority.toggle_reaction('content-editor', target_id, 'inbox-reaction-target',
            emoji='👍', expected_row_version=own_comment['row_version'],
            operation_id='browser-inbox-reaction-create', **epochs)
        notifications = authority.list_inbox('owner')['items']
        assert {item['kind'] for item in notifications} == {'comment_mentioned', 'comment_reacted'}
        mention_id = next(item['id'] for item in notifications if item['kind'] == 'comment_mentioned')
        store = CollaborativeWorkspaceStore(authority.catalog_path,
            user_data_root=authority.user_data_root, identity_directory=authority.identity_directory)
        current_id = create_page(authority, 'owner', workspace_id, title='알림과 다른 열린 페이지',
            base_version=store.get_workspace('owner', workspace_id)['version'],
            operation_id='browser-inbox-other-page-create')['page']['id']

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page(locale='ko-KR', viewport={'width': width, 'height': 844})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin + '/page/' + current_id, wait_until='domcontentloaded')
                page.locator('#userId').fill('owner')
                page.locator('#password').fill(password)
                page.locator('#submitBtn').click()
                page.wait_for_function('expected => typeof State !== "undefined" && State.activePageId === expected && State.liveCollaboration.crdtReady', arg=current_id, timeout=30_000)
                page.evaluate('theme => applyColorTheme(theme)', theme)
                if width <= 720:
                    page.locator('#sidebarRestoreBtn').click()
                page.locator('#inboxNavBtn').click()
                rows = page.locator('#inboxList [data-inbox-item]')
                expect(rows).to_have_count(2, timeout=30_000)
                assert page.evaluate('() => State.activePageId') == current_id
                for value, count in [('due', 2), ('mention', 1), ('upcoming', 0), ('all', 2), ('mention', 1)]:
                    page.locator('#inboxFilterBtn').click()
                    page.locator(f'#inboxFilterMenu [data-inbox-filter="{value}"]').click()
                    expect(rows).to_have_count(count)
                    expect(page.locator('#inboxFilterBtn')).to_be_focused()
                page.locator(f'#inboxList [data-inbox-item="{mention_id}"]').click()
                page.wait_for_function('expected => State.activePageId === expected && State.liveCollaboration.crdtReady', arg=target_id, timeout=30_000)
                page.wait_for_function('id => State.inboxItems.find(item => item.id === id)?.read === true', arg=mention_id, timeout=15_000)
                assert next(item for item in authority.list_inbox('owner')['items'] if item['id'] == mention_id)['read'] is True
                assert authority.read_document('owner', target_id)['blocks'] == original_blocks
                if width <= 720 and not page.evaluate('() => document.body.classList.contains("mobile-sidebar-open")'):
                    page.locator('#sidebarRestoreBtn').click()
                page.locator('#inboxFilterBtn').click()
                page.locator('#inboxFilterMenu [data-inbox-filter="due"]').click()
                expect(rows).to_have_count(1)
                page.locator('#inboxReadAllBtn').click()
                expect(rows).to_have_count(0, timeout=15_000)
                assert all(item['read'] for item in authority.list_inbox('owner')['items'])
                output = Path(os.environ.get('NOTION_INBOX_ARTIFACT_DIR', str(tmp_path)))
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / f'inbox-read-{theme}-{width}.png'))
                page.reload(wait_until='domcontentloaded')
                page.wait_for_function('expected => typeof State !== "undefined" && State.navigation.collaborativeSharing && State.activePageId === expected && State.liveCollaboration.crdtReady', arg=target_id, timeout=30_000)
                if width <= 720 and not page.evaluate('() => document.body.classList.contains("mobile-sidebar-open")'):
                    page.locator('#sidebarRestoreBtn').click()
                if page.evaluate('() => State.sidebarMode') != 'inbox':
                    page.locator('#inboxNavBtn').click()
                page.locator('#inboxRefreshBtn').click()
                page.wait_for_function('() => State.inboxItems.length === 2 && State.inboxItems.every(item => item.read)', timeout=30_000)
                assert not errors, errors
            finally:
                browser.close()
