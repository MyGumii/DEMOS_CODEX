"""UI-SYNC-TEMPLATE: native apply/create share a template; one detach freezes it."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_assist_v1.tests.browser.synced_toolbar_helpers import open_synced_actions
from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.browser.test_notion_synced_database_detach_browser import DETACH_FLAGS
from code_assist_v1.tests.browser.test_notion_synced_linked_navigation import login, settled, source_database
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original


def proof(page, name, tmp_path, errors, details):
    output = Path(os.environ.get('NOTION_SYNC_TEMPLATE_ARTIFACT_DIR', str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output / (name + '.png')))
    (output / (name + '.json')).write_text(json.dumps(
        {'page_errors': errors, 'details': details}, ensure_ascii=False, indent=2), encoding='utf-8')


def open_row(page, database, identity):
    from playwright.sync_api import expect
    target = page.evaluate("""({database, identity}) => {
        const block = State.pageData.blocks.find(b => b.props?.database_id === database && !b.props?.linked_database);
        return {block: block.id, index: databaseRows(block).findIndex(r => r._canonical_row_id === identity)};
    }""", {'database': database, 'identity': identity})
    assert target['index'] >= 0
    page.locator(f'[data-block-id="{target["block"]}"] [data-table-row="{target["index"]}"].database-open-page').click()
    root = page.locator('[data-item-content]')
    expect(root).to_be_visible(timeout=30_000)
    return root


def edit_source(page, root, identity, text):
    from playwright.sync_api import expect
    editable = root.locator(f'[data-synced-reference="{identity}"] [data-synced-text="instantiation-original"]')
    expect(editable).to_have_attribute('contenteditable', 'true', timeout=30_000)
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith(f'/synced-blocks/{identity}/text')) as saved:
        editable.fill(text)
    assert saved.value.status == 201, saved.value.text()
    assert saved.value.json()['storage'] == 'database_body'
    page.wait_for_function('() => !ItemBodyUi?.synced?.pending()', timeout=30_000)
    expect(editable).to_have_text(text, timeout=30_000)


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('theme,width', [('light', 1280), ('dark', 390)])
def test_native_apply_and_create_share_template_then_single_unsync_preserves_detached_text(tmp_path, theme, width):
    pytest.importorskip('playwright.sync_api')
    from playwright.sync_api import expect, sync_playwright

    flags = {**DETACH_FLAGS, 'staged_database_sharing': False}
    with _isolated_collaborative_app(tmp_path, **flags) as (origin, password, authority, _workspace, initial):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(locale='ko-KR', viewport={'width': width, 'height': 844})
            errors, commands = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda r: commands.append(r.post_data_json)
                    if r.method == 'POST' and r.url.endswith('/databases/commands') else None)
            try:
                login(page, origin, initial, 'owner', password)
                page.evaluate('theme => applyColorTheme(theme)', theme)
                source = source_database(page, initial)
                document, database = source['pageId'], source['databaseId']
                first = authority.read_database('owner', database)['rows'][0]['id']
                authority.execute_schema_command('owner', document, database_id=database,
                    command_kind='database.template.create', target_id='instantiation-template',
                    payload={'name': '공유 원본 양식', 'body': [original('instantiation-original', '템플릿 원본 A'),
                        {'id': 'instantiation-ordinary', 'type': 'paragraph', 'content': '항목별 독립 문장', 'props': {}}],
                        'default_values': {}}, expected_target_version=0, operation_id='instantiation-template-seed',
                    **request(SimpleNamespace(iam=authority), document))
                page.reload(wait_until='domcontentloaded')
                settled(page)
                root = open_row(page, database, first)
                apply = page.locator('[data-db-apply-template="instantiation-template"]')
                expect(apply).to_be_visible(timeout=30_000)
                with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/databases/commands')) as applied:
                    apply.click()
                assert applied.value.status == 201, applied.value.text()
                expect(root.locator('[data-synced-reference] [data-synced-text="instantiation-original"]')).to_have_text('템플릿 원본 A', timeout=30_000)
                page.locator('#linkedItemPeek [data-linked-item-close]').click()

                block = page.evaluate('id => State.pageData.blocks.find(b => b.props?.database_id === id && !b.props?.linked_database).id', database)
                table = page.locator(f'[data-block-id="{block}"]')
                commands.clear()
                table.locator('[data-db-template-picker]').click()
                with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/databases/commands')
                        and r.status == 201 and r.request.post_data_json.get('command_kind') == 'database.row.create') as created:
                    table.locator('[data-db-template-option="instantiation-template"]').click()
                assert created.value.status == 201, created.value.text()
                page.wait_for_function("id => State.pageData.databases.find(d => d.id === id)?.rows.length === 2", arg=database, timeout=30_000)
                settled(page)
                attempts = [c for c in commands if c.get('command_kind') == 'database.row.create']
                assert attempts and len({c['operation_id'] for c in attempts}) == 1
                assert len({c['target_id'] for c in attempts}) == 1
                assert all(c['payload'] == attempts[0]['payload'] for c in attempts)
                rows = authority.read_database('owner', database)['rows']
                second = next(r['id'] for r in rows if r['id'] != first)
                refs = {r['id']: r['body']['blocks'][0]['id'] for r in rows}
                assert refs[first] != refs[second]
                for row in rows:
                    reference = row['body']['blocks'][0]
                    assert reference['content'] == '' and not reference['props'].get('children')
                    assert reference['props']['synced_from'] == {'document_id': document, 'block_id': 'instantiation-original'}

                root = open_row(page, database, first)
                edit_source(page, root, refs[first], '공유 원본 B')
                page.locator('#linkedItemPeek [data-linked-item-close]').click()
                root = open_row(page, database, second)
                peer = root.locator(f'[data-synced-reference="{refs[second]}"]')
                expect(peer.locator('[data-synced-text="instantiation-original"]')).to_have_text('공유 원본 B', timeout=30_000)
                expect(peer.locator('.synced-reference-content b')).to_have_text('자식')
                proof(page, f'shared-template-{theme}-{width}', tmp_path, errors, {'shared_text': '공유 원본 B', 'stored_reference_count': 2, 'native_create_attempts': len(attempts), 'same_operation_and_payload': True})
                open_synced_actions(peer)
                with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith(f'/synced-blocks/{refs[second]}/detach')) as detached:
                    peer.locator('[data-synced-detach]').click()
                assert detached.value.status == 201, detached.value.text()
                expect(root.locator(f'[data-body-text="{refs[second]}"]')).to_have_text('공유 원본 B', timeout=30_000)
                page.locator('#linkedItemPeek [data-linked-item-close]').click()
                root = open_row(page, database, first)
                edit_source(page, root, refs[first], '공유 원본 C')
                page.locator('#linkedItemPeek [data-linked-item-close]').click()
                page.reload(wait_until='domcontentloaded')
                settled(page)
                root = open_row(page, database, first)
                expect(root.locator(f'[data-synced-reference="{refs[first]}"] [data-synced-text="instantiation-original"]')).to_have_text('공유 원본 C', timeout=30_000)
                page.locator('#linkedItemPeek [data-linked-item-close]').click()
                root = open_row(page, database, second)
                expect(root.locator('[data-synced-reference]')).to_have_count(0)
                expect(root.locator(f'[data-body-text="{refs[second]}"]')).to_have_text('공유 원본 B', timeout=30_000)
                latest = authority.read_database('owner', database)
                template = next(t for t in latest['templates'] if t['id'] == 'instantiation-template')
                assert template['body'][0]['content'] == '공유 원본 C'
                assert template['body'][0]['props']['synced_from'] is None
                assert not errors
                proof(page, f'detached-template-{theme}-{width}', tmp_path, errors, {'source_text': '공유 원본 C', 'detached_text': '공유 원본 B', 'reload_verified': True})
            except Exception:
                proof(page, f'template-failure-{theme}-{width}', tmp_path, errors, None)
                raise
            finally:
                browser.close()
