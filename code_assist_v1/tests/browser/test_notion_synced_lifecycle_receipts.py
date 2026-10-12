"""UI-SYNC-LIFECYCLE: native uncertain intent survives Trash/restore and reload."""
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from code_assist_v1.tests.browser.test_notion_collaborative_lock_browser import _isolated_collaborative_app
from code_assist_v1.tests.browser.test_notion_synced_database_detach_browser import DETACH_FLAGS, open_owned
from code_assist_v1.tests.browser.test_notion_synced_row_clipboard import setup, reopen_owned
from code_assist_v1.tests.browser.test_notion_synced_clipboard import copy, prepare
from code_assist_v1.tests.browser.synced_toolbar_helpers import open_synced_actions
from code_assist_v1.tests.notion_collaboration.test_synced_lifecycle_receipts import lifecycle
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original, reference
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied


def proof(page, tmp_path, name, errors, details):
    root = Path(os.environ.get('NOTION_SYNC_LIFECYCLE_ARTIFACT_DIR', str(tmp_path)))
    root.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(root / (name + '.png')))
    (root / (name + '.json')).write_text(json.dumps({
        'url': page.url, 'page_errors': errors, 'details': details,
        'state': page.evaluate("""() => ({active:State.activePageId,
          status:document.querySelector('#status').textContent,
          paste:ItemBodyUi?.paste?.snapshot(),
          detach:getSyncedDetachController().list(syncedDetachScope()),
          ready:State.liveCollaboration.crdtReady,
          replay:State.liveCollaboration.initialReplay,pending:State.liveCollaboration.unsyncedCount})"""),
    }, ensure_ascii=False, indent=2), encoding='utf-8')


def write_row(authority, document, database, row, blocks, operation):
    before = next(r for r in authority.read_database('owner', database)['rows'] if r['id'] == row)
    return authority.execute_surface_command('owner', document, database_id=database,
        command_kind='database.row.content.set', target_id=row, payload={'blocks': blocks},
        expected_target_version=before['version'], operation_id=operation,
        **request(SimpleNamespace(iam=authority), document))


@pytest.mark.browser
@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('effect', ['paste-row', 'detach-row', 'detach-template'])
@pytest.mark.parametrize('boundary,theme,width', [('database', 'light', 1280), ('page', 'dark', 390)])
def test_uncertain_operation_reload_archive_restore_fresh_body_and_exact_result(
        tmp_path, effect, boundary, theme, width):
    from playwright.sync_api import expect, sync_playwright
    flags = {**DETACH_FLAGS, 'staged_database_sharing': boundary == 'database'}
    with _isolated_collaborative_app(tmp_path, **flags) as (origin, password, authority, workspace, initial):
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            context = browser.new_context(locale='ko-KR', viewport={'width': width, 'height': 844})
            context.grant_permissions(['clipboard-read', 'clipboard-write'], origin=origin)
            page = context.new_page()
            errors, posts, committed = [], [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('dialog', lambda dialog: dialog.accept())
            details = {'effect': effect, 'boundary': boundary, 'theme': theme, 'width': width}
            name = f'lifecycle-{effect}-{boundary}-{theme}-{width}'
            try:
                document, database, row, visible, root = setup(page, origin, password, authority, workspace, initial, 'owned')
                page.evaluate('theme=>applyColorTheme(theme)', theme)
                if effect == 'paste-row':
                    copy(page, 'row-clip-original')
                    prepare(page)
                    suffix = '/synced-paste'
                else:
                    blocks = [original('lifecycle-original', '해제 전 원본 A'),
                              reference('lifecycle-peer', document, 'lifecycle-original'),
                              reference('lifecycle-keep', document, 'lifecycle-original'),
                              {'id': 'lifecycle-unrelated', 'type': 'paragraph', 'content': '기존 다른 글', 'props': {}}]
                    kind = effect.removeprefix('detach-')
                    if kind == 'row':
                        write_row(authority, document, database, row, blocks, 'lifecycle-row-seed')
                        target = row
                    else:
                        target = 'lifecycle-template'
                        authority.execute_schema_command('owner', document, database_id=database,
                            command_kind='database.template.create', target_id=target,
                            payload={'name': '휴지통 복구 확인', 'body': blocks, 'default_values': {}},
                            expected_target_version=0, operation_id='lifecycle-template-seed',
                            **request(SimpleNamespace(iam=authority), document))
                    page.reload(wait_until='domcontentloaded')
                    prepare(page)
                    root = open_owned(page, database, target, kind)
                    peer = root.locator('[data-synced-reference="lifecycle-peer"]')
                    expect(peer.locator('.synced-reference-content')).to_contain_text('해제 전 원본 A', timeout=30_000)
                    open_synced_actions(peer)
                    suffix = '/synced-blocks/lifecycle-peer/detach'
                page.on('request', lambda req: posts.append(req.post_data_json)
                        if req.method == 'POST' and req.url.endswith(suffix) else None)
                def lose(route):
                    response = route.fetch()
                    assert response.status == 201, response.text()
                    committed.append(response.json())
                    route.abort('failed')
                page.route('**' + suffix, lose)
                if effect == 'paste-row':
                    root.locator('[data-body-text="row-clip-anchor"]').press('Control+v')
                    page.wait_for_function('()=>ItemBodyUi?.paste.snapshot().pending&&!ItemBodyUi.paste.snapshot().busy', timeout=30_000)
                else:
                    peer.locator('[data-synced-detach]').click()
                    page.wait_for_function("()=>getSyncedDetachController().list(syncedDetachScope()).some(e=>e.blockId==='lifecycle-peer'&&!e.confirmed&&!e.busy)", timeout=30_000)
                assert len(committed) == len(posts) == 1
                details['committed'] = committed[0]
                details['request'] = posts[0]
                saved = deepcopy(authority.read_database('owner', database))
                page.goto(origin + '/page/' + initial, wait_until='domcontentloaded')
                prepare(page)
                lifecycle(authority, workspace, document, database, boundary, True, 'lifecycle-native-archive')
                with pytest.raises(NotFoundOrDenied):
                    authority.read_database('owner', database)
                page.reload(wait_until='domcontentloaded')
                prepare(page)
                if effect != 'paste-row':
                    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/detach-result')) as recovered:
                        page.locator('#syncedDetachStatus').get_by_role('button', name='결과 확인', exact=True).click()
                    response = recovered.value
                    assert response.status == 200, response.text()
                    details['archived_receipt'] = response.json()
                    assert details['archived_receipt']['state'] == 'committed'
                    assert details['archived_receipt']['current']['body_archived']
                    assert details['archived_receipt']['current']['archived'] is (boundary == 'page')
                    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/detach-result')):
                        page.locator('#syncedDetachStatus').get_by_role('button', name='본문 열기', exact=True).click()
                    assert page.evaluate('State.activePageId') == initial
                assert len(posts) == 1
                lifecycle(authority, workspace, document, database, boundary, False, 'lifecycle-native-restore')
                restored = authority.read_database('owner', database)
                assert restored['rows'] == saved['rows'] and restored['templates'] == saved['templates']
                if effect == 'detach-template':
                    template = next(t for t in restored['templates'] if t['id'] == target)
                    fresh = deepcopy(template['body'])
                    next(b for b in fresh if b['id'] == 'lifecycle-unrelated')['content'] = '복원 후 최신 입력 B'
                    authority.execute_schema_command('owner', document, database_id=database,
                        command_kind='database.template.update', target_id=target, payload={'body': fresh},
                        expected_target_version=template['version'], operation_id='lifecycle-native-new-template-input',
                        **request(SimpleNamespace(iam=authority), document))
                else:
                    fresh = deepcopy(next(r for r in restored['rows'] if r['id'] == row)['body']['blocks'])
                    ordinary = 'row-clip-keep' if effect == 'paste-row' else 'lifecycle-unrelated'
                    next(b for b in fresh if b['id'] == ordinary)['content'] = '복원 후 최신 입력 B'
                    write_row(authority, document, database, row, fresh, 'lifecycle-native-new-row-input')
                expected_database = deepcopy(authority.read_database('owner', database))
                details['expected_database'] = expected_database
                if effect == 'paste-row':
                    root = reopen_owned(page, origin, document, database)
                    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/synced-paste-result')) as recovered:
                        root.get_by_role('button', name='붙여넣기 결과 확인 · 재시도').click()
                    assert recovered.value.status == 200 and recovered.value.json()['state'] == 'committed'
                    details['restored_receipt'] = recovered.value.json()
                    page.wait_for_function('()=>ItemBodyUi?.paste&&!ItemBodyUi.paste.pending()', timeout=30_000)
                    expect(root.locator('[data-body-text="row-clip-keep"]')).to_have_text('복원 후 최신 입력 B')
                    expect(root.locator(f'[data-synced-reference="{committed[0]["reference_id"]}"]')).to_contain_text('현재 원본 A')
                else:
                    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/detach-result')) as recovered:
                        page.locator('#syncedDetachStatus').get_by_role('button', name='본문 열기', exact=True).click()
                    assert recovered.value.status == 200 and not recovered.value.json()['current']['body_archived']
                    details['restored_receipt'] = recovered.value.json()
                    root = page.locator('[data-item-content]' if effect == 'detach-row' else '#dbTemplateBodyEditor')
                    expect(root.locator('[data-body-text="lifecycle-unrelated"]')).to_have_text('복원 후 최신 입력 B', timeout=30_000)
                    expect(root.locator('[data-body-text="lifecycle-peer"]')).to_have_text('해제 전 원본 A')
                    expect(root.locator('[data-synced-reference="lifecycle-keep"] .synced-reference-content')).to_contain_text(
                        '해제 전 원본 A', timeout=30_000)
                    details['retained_reference_rendered'] = True
                    # Template modals and the narrow-screen item peek own
                    # native pointer input. Capture the current body, then
                    # close through the actual control before acknowledging
                    # the separate global recovery panel underneath.
                    proof(page, tmp_path, name + '-body', errors, details)
                    page.locator('#modalClose' if effect == 'detach-template'
                                 else '#linkedItemPeek [data-linked-item-close]').click()
                    expect(root).not_to_be_visible()
                    page.locator('#syncedDetachStatus').get_by_role('button', name='확인', exact=True).click()
                    page.wait_for_function("()=>!getSyncedDetachController().list(syncedDetachScope()).some(e=>e.blockId==='lifecycle-peer')", timeout=30_000)
                assert authority.read_database('owner', database) == expected_database
                assert len(posts) == 1 and not errors, errors
                details['posted_requests'] = posts
                details['preserved_database'] = authority.read_database('owner', database)
                proof(page, tmp_path, name, errors, details)
            except Exception:
                proof(page, tmp_path, name + '-failure', errors, details)
                raise
            finally:
                browser.close()
