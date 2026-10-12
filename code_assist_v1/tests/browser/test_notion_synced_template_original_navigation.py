"""UI-SYNC-TEMPLATE-ORIGINAL: native source navigation after item instantiation."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from code_assist_v1.tests.browser.synced_toolbar_helpers import open_synced_original
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.browser.test_notion_synced_database_detach_browser import DETACH_FLAGS
from code_assist_v1.tests.browser.test_notion_synced_linked_navigation import login, settled, source_database
from code_assist_v1.tests.browser.test_notion_synced_template_instantiation import open_row, proof
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('actor,theme,width', [
    ('owner', 'light', 1280), ('owner', 'dark', 390),
    ('content-editor', 'light', 1280), ('content-editor', 'dark', 390),
])
def test_instantiated_item_original_opens_exact_template_with_current_role_and_preserves_reference_body(tmp_path, actor, theme, width):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect, sync_playwright

    flags = {**DETACH_FLAGS, 'staged_database_sharing': False}
    with _isolated_collaborative_app(tmp_path, **flags) as (origin, password, authority, _workspace, initial):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(locale='ko-KR', viewport={'width': width, 'height': 844})
            errors, mutations, responses = [], [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('response', lambda r: responses.append({'path': r.url.split(origin)[-1], 'method': r.request.method, 'status': r.status})
                if '/synced-blocks/' in r.url or r.url.endswith('/databases/commands') else None)
            page.on('request', lambda r: mutations.append({'url': r.url, 'method': r.method})
                if r.method in ('POST', 'PATCH', 'PUT', 'DELETE') and
                   (r.url.endswith('/databases/commands') or '/synced-blocks/' in r.url) else None)
            name = f'template-original-{actor}-{theme}-{width}'
            try:
                login(page, origin, initial, 'owner', password)
                page.evaluate('theme => applyColorTheme(theme)', theme)
                source = source_database(page, initial)
                document, database = source['pageId'], source['databaseId']
                template_id = 'navigation-shared-template'
                body = [original('navigation-shared-original', '원본 A'),
                    {'id': 'navigation-ordinary', 'type': 'paragraph', 'content': '독립 본문 유지', 'props': {}}]
                authority.execute_schema_command('owner', document, database_id=database,
                    command_kind='database.template.create', target_id=template_id,
                    payload={'name': '원본 이동 양식', 'body': body, 'default_values': {}},
                    expected_target_version=0, operation_id='navigation-shared-template-seed',
                    **request(SimpleNamespace(iam=authority), document))
                page.reload(wait_until='domcontentloaded')
                settled(page)
                first = authority.read_database('owner', database)['rows'][0]['id']
                page.locator('[data-db-template-picker]').click()
                with page.expect_response(lambda r: r.request.method == 'POST' and
                        r.url.endswith('/databases/commands') and r.status == 201 and
                        r.request.post_data_json.get('command_kind') == 'database.row.create') as created:
                    page.locator(f'[data-db-template-option="{template_id}"]').click()
                assert created.value.status == 201
                page.wait_for_function("id => State.pageData.databases.find(d => d.id === id)?.rows.length === 2", arg=database, timeout=30_000)
                settled(page)
                before = authority.read_database('owner', database)
                row = next(r for r in before['rows'] if r['id'] != first)
                row_id, reference_id = row['id'], row['body']['blocks'][0]['id']
                rows_before = {r['id']: (('body' in r), deepcopy(r.get('body'))) for r in before['rows']}
                template_before = deepcopy(next(t for t in before['templates'] if t['id'] == template_id))
                if actor == 'content-editor':
                    current = authority.read_document('owner', document)
                    authority.set_page_grant('owner', document, 'subject', actor,
                        role='edit_content', inherit_to_descendants=False,
                        expected_workspace_epoch=current['workspace_epoch'],
                        expected_document_epoch=current['document_epoch'],
                        operation_id='navigation-shared-content-role')
                    context = browser.new_context(locale='ko-KR', viewport={'width': width, 'height': 844})
                    page = context.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda r: mutations.append({'url': r.url, 'method': r.method})
                        if r.method in ('POST', 'PATCH', 'PUT', 'DELETE') and
                           (r.url.endswith('/databases/commands') or '/synced-blocks/' in r.url) else None)
                    login(page, origin, document, actor, password)
                    page.evaluate('theme => applyColorTheme(theme)', theme)
                mutations.clear()
                root = open_row(page, database, row_id)
                peer = root.locator(f'[data-synced-reference="{reference_id}"]')
                text = peer.locator('[data-synced-text="navigation-shared-original"]')
                expect(text).to_have_text('원본 A', timeout=30_000)
                if actor == 'owner':
                    expect(text).to_be_editable()
                    # Navigate immediately after native input. The source button
                    # must flush the item's shared draft before opening its source.
                    with page.expect_response(lambda r: r.request.method == 'POST' and
                            r.url.endswith(f'/synced-blocks/{reference_id}/text') and r.status == 201) as saved:
                        text.fill('항목에서 입력한 B')
                        open_synced_original(peer)
                    assert saved.value.status == 201
                    source_root = page.locator('#dbTemplateBodyEditor')
                    source_text = source_root.locator('[data-body-text="navigation-shared-original"]')
                    expect(page.locator('#dbTemplateName')).to_have_value('원본 이동 양식', timeout=30_000)
                    expect(source_text).to_be_focused(timeout=30_000)
                    expect(source_text).to_have_text('항목에서 입력한 B')
                    with page.expect_response(lambda r: r.request.method == 'POST' and
                            r.url.endswith('/databases/commands') and r.status == 201) as updated:
                        source_text.fill('템플릿에서 입력한 C')
                    assert updated.value.status == 201
                    expect(page.locator('#dbTemplateSaveState')).to_have_text('모든 변경 사항 저장됨', timeout=30_000)
                    page.wait_for_function('() => !ActiveDatabaseTemplateEditor.dirty()', timeout=30_000)
                    proof(page, name + '-source', tmp_path, errors, {'actor': actor, 'exact_template_focused': True, 'source_text': '템플릿에서 입력한 C'})
                    page.locator('#modalClose').click()
                    expect(page.locator('#modalBg')).to_be_hidden(timeout=30_000)
                    page.reload(wait_until='domcontentloaded')
                    settled(page)
                    root = open_row(page, database, row_id)
                    expect(root.locator(f'[data-synced-reference="{reference_id}"] [data-synced-text="navigation-shared-original"]')).to_have_text('템플릿에서 입력한 C', timeout=30_000)
                    expected_text, version_delta = '템플릿에서 입력한 C', 2
                else:
                    expect(text).not_to_be_editable()
                    open_synced_original(peer)
                    source_root = page.locator('#dbTemplateReadBody')
                    source_text = source_root.locator('[data-body-text="navigation-shared-original"]')
                    expect(page.locator('#dbTemplateReadName')).to_have_text('원본 이동 양식', timeout=30_000)
                    expect(source_text).to_be_focused(timeout=30_000)
                    expect(source_text).not_to_be_editable()
                    expect(source_text).to_have_text('원본 A')
                    expect(source_root.locator('[contenteditable="true"]')).to_have_count(0)
                    assert not mutations
                    expected_text, version_delta = '원본 A', 0
                latest = authority.read_database('owner', database)
                template = next(t for t in latest['templates'] if t['id'] == template_id)
                assert template['body'][0]['content'] == expected_text
                assert template['body'][0]['props']['synced_from'] is None
                assert template['version'] == template_before['version'] + version_delta
                assert {r['id']: (('body' in r), r.get('body')) for r in latest['rows']} == rows_before
                assert not errors
                proof(page, name + '-final', tmp_path, errors, {'actor': actor, 'source_text': expected_text,
                    'source_version_delta': version_delta, 'row_bodies_unchanged': True,
                    'business_mutations': len(mutations), 'original_endpoint_retained': True})
            except Exception:
                diagnostic = page.evaluate('''() => {
                    const item = LinkedDatabaseItems?.snapshot(), body = ItemBodyUi && OwnedItemBody?.snapshot(ItemBodyUi.scope, ItemBodyUi.database, ItemBodyUi.row);
                    const flags = model => model && Object.fromEntries(['ready','editable','dirty','pending','busy','composing','error'].map(k => [k, model[k]]));
                    return {item:flags(item),body:flags(body),syncedPending:ItemBodyUi?.synced?.pending(),
                        active:document.activeElement?.outerHTML?.slice(0,400), modalHidden:document.querySelector('#modalBg')?.hidden,
                        statuses:[...document.querySelectorAll('[role="status"]')].map(n=>n.textContent).filter(Boolean)};
                }''')
                proof(page, name + '-failure', tmp_path, errors, {'diagnostic': diagnostic, 'responses': responses})
                raise
            finally:
                browser.close()
