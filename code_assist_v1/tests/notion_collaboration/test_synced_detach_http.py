"""A staged HTTP boundary for exact, recoverable synced-block detach requests."""

from code_assist_v1.notion_collaboration import page_archive
from code_assist_v1.notion_collaboration import synced_blocks
from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for
from code_assist_v1.tests.notion_collaboration.test_page_archive import request as archive_request
from code_assist_v1.tests.notion_collaboration.test_synced_detach_commit import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, inspect, service_request, setup_resources,
    text_update,
)
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import insert_synced, setup_pair
from code_assist_v1.tests.notion_collaboration.test_synced_edit import grant_edit


def endpoint(workspace, page, suffix):
    return (f'/api/notion/workspaces/{workspace}/pages/{page}'
            f'/synced-blocks/synced-instance/{suffix}')


def request_body(store, workspace, page):
    data = service_request(store, workspace, page, operation='http-detach-request')
    return {key: value for key, value in data.items() if key != 'local_origin'}


def enable_in_test(monkeypatch, store):
    capabilities = store.capabilities
    monkeypatch.setattr(store, 'capabilities',
        lambda: {**capabilities(), 'collaborative_synced_detach': True})


def test_ui_synced_detach_http_is_unavailable_until_browser_recovery_is_ready(environment, monkeypatch):
    store, workspace = environment
    page, _, _ = setup_resources(store, workspace)
    body = request_body(store, workspace, page)
    client, _ = client_for(monkeypatch, store)
    client.application.config['NOTION_CANONICAL_ORIGIN'] = 'https://app.example'
    before = inspect(store, 'page-e1', page)
    assert store.capabilities()['collaborative_synced_detach'] is False
    for suffix in ('detach', 'detach-result'):
        response = client.post(endpoint(workspace, page, suffix), json=body)
        assert response.status_code == 503
        assert response.json['code'] == 'collaboration_authority_unavailable'
    assert inspect(store, 'page-e1', page) == before


def test_ui_synced_detach_http_exact_retry_status_and_access_fence(environment, monkeypatch):
    store, workspace = environment
    page, _, _ = setup_resources(store, workspace)
    body = request_body(store, workspace, page)
    client, actor = client_for(monkeypatch, store)
    client.application.config['NOTION_CANONICAL_ORIGIN'] = 'https://app.example'
    enable_in_test(monkeypatch, store)
    save = endpoint(workspace, page, 'detach')
    status = endpoint(workspace, page, 'detach-result')
    before = inspect(store, 'page-e1', page)

    unknown = client.post(status, json=body)
    assert unknown.status_code == 200 and unknown.json == {'state': 'unknown'}
    assert 'no-store' in unknown.headers['Cache-Control']
    assert inspect(store, 'page-e1', page) == before
    assert client.post(save, json={**body, 'extra': 1}).status_code == 400
    assert client.post(save, json=body, headers={'Origin': 'https://elsewhere.example'}).status_code == 400
    assert inspect(store, 'page-e1', page) == before

    first = client.post(save, json=body)
    assert first.status_code == 201 and first.json['replayed'] is False
    assert first.json['status'] == 'synced_detached'
    after = inspect(store, 'page-e1', page)
    assert after['source_snapshot'] == before['source_snapshot']
    text_update(store, workspace, 'page-e1', 'synced-original', 'source changed after detach')
    stable = inspect(store, 'page-e1', page)

    receipt = client.post(status, json=body)
    assert receipt.status_code == 200 and receipt.json['state'] == 'committed'
    assert receipt.json['result']['operation_id'] == body['operation_id']
    assert 'no-store' in receipt.headers['Cache-Control']
    retry = client.post(save, json=body)
    assert retry.status_code == 200 and retry.json['replayed'] is True
    assert inspect(store, 'page-e1', page) == stable

    changed = {**body, 'witnesses': [dict(item) for item in body['witnesses']]}
    changed['witnesses'][0]['document_sequence'] += 1
    assert client.post(status, json=changed).status_code == 409
    assert client.post(save, json=changed).status_code == 409
    actor['id'] = 'outsider'
    assert client.post(status, json=body).status_code == 404
    assert client.post(save, json=body).status_code == 404
    assert inspect(store, 'page-e1', page) == stable

    actor['id'] = 'owner'
    page_archive.set_page_archived(store.iam, 'owner', workspace, page,
        **archive_request(store, workspace, page, operation='detached-target-archive'))
    archived = client.post(status, json=body)
    assert archived.status_code == 200 and archived.json['state'] == 'committed'
    assert archived.json['current']['archived'] is True
    assert client.post(save, json=body).status_code == 200
    actor['id'] = 'outsider'
    assert client.post(status, json=body).status_code == 404


def test_ui_synced_detach_http_same_page_reference_uses_one_witness(environment, monkeypatch):
    store, workspace = environment
    insert_synced(store, 'page-e1', 'synced-original', content='shared on one page')
    insert_synced(store, 'page-e1', 'same-page-instance',
        origin={'document_id': 'page-e1', 'block_id': 'synced-original'})
    current = synced_blocks.read_synced_block(store.iam, 'owner', 'page-e1',
        'same-page-instance', workspace_id=workspace)
    assert len(current['witnesses']) == 1
    body = {'origin': current['origin'], 'witnesses': current['witnesses'],
            'operation_id': 'http-detach-same-page'}
    client, _ = client_for(monkeypatch, store)
    enable_in_test(monkeypatch, store)
    url = (f'/api/notion/workspaces/{workspace}/pages/page-e1/'
           'synced-blocks/same-page-instance/detach')
    response = client.post(url, json=body)
    assert response.status_code == 201
    blocks = store.iam.read_document('owner', 'page-e1')['blocks']
    original = next(block for block in blocks if block['id'] == 'synced-original')
    detached = next(block for block in blocks if block['id'] == 'same-page-instance')
    assert original['type'] == 'synced_block'
    assert detached['type'] == 'paragraph' and detached['content'] == 'shared on one page'
    assert client.post(url, json=body).status_code == 200


def test_ui_synced_detach_http_copies_owned_file_and_database_without_changing_source(environment, monkeypatch):
    store, workspace = environment
    page, used, unused = setup_resources(store, workspace)
    body = request_body(store, workspace, page)
    client, _ = client_for(monkeypatch, store)
    client.application.config['NOTION_CANONICAL_ORIGIN'] = 'https://app.example'
    enable_in_test(monkeypatch, store)
    before = inspect(store, 'page-e1', page)

    committed = client.post(endpoint(workspace, page, 'detach'), json=body)
    assert committed.status_code == 201
    result = committed.json
    assert result['replayed'] is False
    assert len(result['copied_file_ids']) == 1
    assert used not in result['copied_file_ids'] and unused not in result['copied_file_ids']
    assert len(result['copied_database_ids']) == 1
    after = inspect(store, 'page-e1', page)
    assert after['source_snapshot'] == before['source_snapshot']
    assert {item['id'] for item in after['target_snapshot']['files']} == set(result['copied_file_ids'])
    assert {item['id'] for item in after['target_snapshot']['databases']} == set(result['copied_database_ids'])
    assert client.post(endpoint(workspace, page, 'detach-result'), json=body).json['state'] == 'committed'


def test_ui_synced_detach_http_editor_needs_source_view_and_target_edit_then_only_target_view_for_receipt(environment, monkeypatch):
    store, workspace = environment
    page = setup_pair(store, workspace)
    grant_edit(store, 'page-e1', 'view', 'detach-http-source-view')
    grant_edit(store, page, 'view', 'detach-http-target-view')
    client, actor = client_for(monkeypatch, store)
    actor['id'] = 'outsider'
    enable_in_test(monkeypatch, store)
    save = endpoint(workspace, page, 'detach')
    status = endpoint(workspace, page, 'detach-result')
    current = synced_blocks.read_synced_block(store.iam, 'outsider', page,
        'synced-instance', workspace_id=workspace)
    body = {'origin': current['origin'], 'witnesses': current['witnesses'],
            'operation_id': 'detach-http-view-only'}
    assert client.post(save, json=body).status_code == 404
    grant_edit(store, page, 'edit_content', 'detach-http-target-edit')
    current = synced_blocks.read_synced_block(store.iam, 'outsider', page,
        'synced-instance', workspace_id=workspace)
    body = {'origin': current['origin'], 'witnesses': current['witnesses'],
            'operation_id': 'detach-http-editor'}
    assert client.post(status, json=body).json == {'state': 'unknown'}
    committed = client.post(save, json=body)
    assert committed.status_code == 201 and committed.json['replayed'] is False
    target_snapshot = inspect(store, 'page-e1', page)['target_snapshot']

    source = store.iam.read_document('owner', 'page-e1')
    store.iam.revoke_page_grant('owner', 'page-e1', 'subject', 'outsider',
        expected_workspace_epoch=source['workspace_epoch'],
        expected_document_epoch=source['document_epoch'],
        operation_id='detach-http-source-revoke')
    assert client.post(status, json=body).json['state'] == 'committed'
    assert client.post(save, json=body).status_code == 200
    assert inspect(store, 'page-e1', page)['target_snapshot'] == target_snapshot

    target = store.iam.read_document('owner', page)
    store.iam.revoke_page_grant('owner', page, 'subject', 'outsider',
        expected_workspace_epoch=target['workspace_epoch'],
        expected_document_epoch=target['document_epoch'],
        operation_id='detach-http-target-revoke')
    assert client.post(status, json=body).status_code == 404
    assert client.post(save, json=body).status_code == 404
