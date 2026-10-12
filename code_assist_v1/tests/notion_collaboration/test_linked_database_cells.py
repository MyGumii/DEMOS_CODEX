"""UI-LINKED-CELLS: shared writes bind durable instance and source authority."""
from copy import deepcopy
import pytest

from code_assist_v1.notion_collaboration import linked_databases as linked
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied, OperationConflict
from code_assist_v1.notion_collaboration.editing_authority import TargetVersionConflict
from code_assist_v1.tests.notion_collaboration.test_linked_database_copy import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, setup_pair, dump, update_views,
)
from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema


def command(store, ws, document, *, actor='owner', operation='linked-cell-write'):
    current = linked.read_linked_database(store.iam, actor, document, 'linked-block', workspace_id=ws)
    row = current['database']['rows'][0]
    title = next(p['id'] for p in current['database']['properties'] if p['type'] == 'title')
    return {'source': current['source'],
        'instance_witness': next(w for w in current['witnesses'] if w['document_id'] == document),
        'source_witness': next(w for w in current['witnesses'] if w['document_id'] == current['source']['document_id']),
        'cells': [{'row_id': row['id'], 'property_id': title, 'value': 'Changed through linked view',
            'expected_target_version': row['cell_versions'].get(title, 0)}], 'operation_id': operation}


def write(store, ws, document, body, actor='owner', **kwargs):
    return linked.execute_linked_cells(store.iam, actor, ws, document, 'linked-block', **body, **kwargs)


def grant(store, document, role, operation):
    page = store.iam.read_document('owner', document)
    return store.iam.set_page_grant('owner', document, 'subject', 'outsider', role=role,
        inherit_to_descendants=False, expected_workspace_epoch=page['workspace_epoch'],
        expected_document_epoch=page['document_epoch'], operation_id=operation)


def test_ui_linked_cells_commit_replay_and_scope_hash(environment):
    store, ws = environment
    child, title = setup_pair(store, ws)
    body = command(store, ws, child)
    instance_before = store.iam.read_document('owner', child)
    source_views = store.iam.read_database('owner', body['source']['database_id'])['views']
    first = write(store, ws, child, body)
    assert first['linked_instance'] == {'document_id': child, 'block_id': 'linked-block'}
    assert first['document_id'] == 'page-e1'
    after = dump(store)
    assert write(store, ws, child, body) == first
    assert dump(store) == after
    assert store.iam.read_document('owner', child) == instance_before
    current = linked.read_linked_database(store.iam, 'owner', child, 'linked-block')
    assert current['database']['rows'][0]['cells'][title] == 'Changed through linked view'
    assert current['database']['views'] == source_views
    changed = deepcopy(body); changed['cells'][0]['value'] = 'Reused ID'
    with pytest.raises(OperationConflict): write(store, ws, child, changed)
    assert dump(store) == after


def test_ui_linked_cells_date_range_round_trip_and_versioned_undo(environment):
    store, ws = environment
    _schema(store.iam, 'database.property.create', 'due',
        {'name': 'Due', 'type': 'date', 'config': {}}, 'linked-date-property', actor='owner')
    child, _ = setup_pair(store, ws)
    body = command(store, ws, child, operation='linked-date-range')
    row_id = body['cells'][0]['row_id']
    body['cells'] = [{'row_id': row_id, 'property_id': 'due',
        'value': {'start': '2026-09-16', 'end': '2026-09-18'},
        'expected_target_version': 0}]
    first = write(store, ws, child, body)
    assert first['cells'][0]['target_version'] == 1
    source_row = store.iam.read_database('owner', 'database-e1')['rows'][0]
    linked_row = linked.read_linked_database(store.iam, 'owner', child, 'linked-block')['database']['rows'][0]
    expected = {'start': '2026-09-16', 'end': '2026-09-18'}
    assert source_row['cells']['due'] == linked_row['cells']['due'] == expected

    undo = command(store, ws, child, operation='linked-date-range-undo')
    undo['cells'] = [{'row_id': row_id, 'property_id': 'due', 'value': None,
        'expected_target_version': 1}]
    second = write(store, ws, child, undo)
    assert second['cells'][0]['target_version'] == 2
    assert store.iam.read_database('owner', 'database-e1')['rows'][0]['cells'].get('due') is None


@pytest.mark.parametrize('fault', ['after_domain', 'after_event', 'after_audit', 'after_outbox'])
def test_ui_linked_cells_atomic_rollback(environment, fault):
    store, ws = environment
    child, _ = setup_pair(store, ws)
    body = command(store, ws, child)
    before = dump(store)
    with pytest.raises(RuntimeError): write(store, ws, child, body, fault=fault)
    assert dump(store) == before


def test_ui_linked_cells_stale_versions_and_source_binding(environment):
    store, ws = environment
    child, _ = setup_pair(store, ws)
    body = command(store, ws, child)
    before = dump(store)
    stale = deepcopy(body); stale['cells'][0]['expected_target_version'] += 10
    with pytest.raises(TargetVersionConflict): write(store, ws, child, stale)
    mismatched = deepcopy(body); mismatched['source']['database_id'] = 'other-database'
    with pytest.raises(linked.LinkedDatabaseSourceChanged): write(store, ws, child, mismatched)
    missing = deepcopy(body); missing['cells'].append({**missing['cells'][0], 'row_id': 'missing-row'})
    with pytest.raises(NotFoundOrDenied): write(store, ws, child, missing)
    assert dump(store) == before


def test_ui_linked_cells_revalidates_instance_after_replay(environment, monkeypatch):
    store, ws = environment
    child, _ = setup_pair(store, ws)
    body = command(store, ws, child)
    prepare = linked.synced_blocks._current
    expected = []
    def racing(*args, **kwargs):
        prepared = prepare(*args, **kwargs)
        update_views(store, ws, child, 'linked-block', 'concurrent filter')
        expected.append(dump(store))
        return prepared
    monkeypatch.setattr(linked.synced_blocks, '_current', racing)
    with pytest.raises(linked.LinkedDatabaseSourceChanged): write(store, ws, child, body)
    assert dump(store) == expected[0]


@pytest.mark.parametrize('denied_document', ['source', 'instance'])
def test_ui_linked_cells_requires_both_current_edit_roles(environment, monkeypatch, denied_document):
    store, ws = environment
    child, _ = setup_pair(store, ws)
    grant(store, child, 'edit_content', 'linked-instance-edit')
    grant(store, 'page-e1', 'edit_content', 'linked-source-edit')
    body = command(store, ws, child, actor='outsider')
    prepare = linked.synced_blocks._current
    expected = []
    def revoked(*args, **kwargs):
        prepared = prepare(*args, **kwargs)
        grant(store, 'page-e1' if denied_document == 'source' else child, 'view', 'linked-revoke-'+denied_document)
        expected.append(dump(store))
        return prepared
    monkeypatch.setattr(linked.synced_blocks, '_current', revoked)
    with pytest.raises(NotFoundOrDenied): write(store, ws, child, body, actor='outsider')
    assert dump(store) == expected[0]


def test_ui_linked_cells_http_exact_scope_and_retry(environment, monkeypatch):
    store, ws = environment
    child, _ = setup_pair(store, ws)
    body = command(store, ws, child)
    client, actor = client_for(monkeypatch, store)
    url = f'/api/notion/workspaces/{ws}/pages/{child}/linked-databases/linked-block/cells'
    read_response = client.get(url.removesuffix('/cells'))
    assert read_response.status_code == 200
    assert read_response.json['source'] == {'document_id': 'page-e1', 'database_id': 'database-e1'}
    before = dump(store)
    for target, payload in [(url.replace(ws, 'wrong-workspace'), body), (url, {**body, 'extra': True})]:
        response = client.post(target, json=payload)
        assert response.status_code in (400, 404)
        assert 'no-store' in response.headers['Cache-Control']
    assert dump(store) == before
    first = client.post(url, json=body)
    assert first.status_code == 201, first.json
    after = dump(store)
    assert client.post(url, json=body).json == first.json
    assert dump(store) == after
    actor['id'] = 'outsider'
    assert client.post(url, json=body).status_code == 404


def test_ui_linked_cells_ordinary_runtime_cannot_write(monkeypatch):
    client, _ = client_for(monkeypatch, object(), collaborative=False)
    response = client.post('/api/notion/workspaces/ws/pages/page/linked-databases/block/cells', json={})
    assert response.status_code == 404 and 'no-store' in response.headers['Cache-Control']
