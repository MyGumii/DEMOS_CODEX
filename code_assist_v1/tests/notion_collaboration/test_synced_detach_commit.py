"""UI-SYNC-DETACH-COMMIT: resources, body and durable updates share one TX."""
import json

import pytest

from code_assist_v1.notion_collaboration import synced_blocks as synced
from code_assist_v1.notion_collaboration import synced_detach_body as body
from code_assist_v1.notion_collaboration import synced_detach_commit as commit
from code_assist_v1.notion_collaboration import synced_detach_delta as delta
from code_assist_v1.notion_collaboration import synced_detach_placement as placement
from code_assist_v1.notion_collaboration import synced_detach_remap as remap
from code_assist_v1.notion_collaboration import synced_detach_resources as resources
from code_assist_v1.notion_collaboration import synced_detach_service as service
from code_assist_v1.notion_collaboration import search_projection
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure, _canonical_json, _sha256
from code_assist_v1.tests.notion_collaboration.test_synced_detach_resources import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, setup_resources, prepared, dump, text_update,
)
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import create, insert_synced, request, setup_pair


def prepare_all(store, ws, child):
    captured = resources.prepare_synced_resources(store.iam, prepared(store, ws, child))
    content = remap.prepare_detached_content(store.iam, captured)
    placed = placement.prepare_detached_placement(store.iam, content)
    prepared_body = body.prepare_detached_body(store.iam, placed)
    return delta.prepare_detached_delta(store.iam, prepared_body)


def service_request(store, ws, child, operation='detach-service-request'):
    current = synced.read_synced_block(store.iam, 'owner', child, 'synced-instance', workspace_id=ws)
    return {'origin':current['origin'], 'witnesses':current['witnesses'],
            'operation_id':operation, 'local_origin':'https://app.example'}


def inspect(store, original, destination):
    with store.iam._reader() as con:
        con.execute('BEGIN')
        source = store.iam._document(con, original)
        target = store.iam._document(con, destination)
        snapshots = [store.iam._editing_snapshot(con, row) for row in (source, target)]
        frames = [synced._frames(con, row['id'], synced._generation(con, row['id'])) for row in (source, target)]
        counts = {name:con.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in (
            'workspace_collaboration_crdt_updates','workspace_collaboration_revisions',
            'workspace_collaboration_operations','workspace_collaboration_events',
            'workspace_collaboration_audit_events','workspace_collaboration_outbox')}
        return {'source':dict(source),'target':dict(target), 'source_snapshot':snapshots[0],
                'target_snapshot':snapshots[1], 'source_frames':frames[0], 'target_frames':frames[1],
                'counts':counts}


def test_ui_detach_commit_preserves_source_and_atomically_clones_resources_and_body(environment):
    store, ws = environment; child, used, unused = setup_resources(store, ws)
    pending = prepare_all(store, ws, child)
    before = inspect(store, 'page-e1', child)
    result = commit.commit_prepared_detach(store.iam, pending)
    after = inspect(store, 'page-e1', child)
    assert result['contract'] == commit.CONTRACT and result['replayed'] is False
    assert after['source'] == before['source']
    assert after['source_snapshot'] == before['source_snapshot']
    assert after['source_frames'] == before['source_frames']
    assert after['target']['document_sequence'] == before['target']['document_sequence'] + len(pending.update_frames) + 1
    assert after['target']['revision'] == before['target']['revision'] + 1
    assert after['target']['content_sha256'] == result['content_sha256'] == _sha256(_canonical_json(after['target_snapshot']))
    assert after['target_snapshot']['blocks'] == pending.body.value()['blocks']
    assert {db['id'] for db in after['target_snapshot']['databases']} == set(result['copied_database_ids'])
    assert {file['id'] for file in after['target_snapshot']['files']} == set(result['copied_file_ids'])
    assert used not in result['copied_file_ids'] and unused not in result['copied_file_ids']
    assert len(after['target_frames']) == len(before['target_frames']) + len(pending.update_frames)
    projected = search_projection.materialize([after['target_frames']])[0]
    assert projected['blocks'] == [{k:v for k,v in block.items() if k != 'position'}
                                   for block in pending.body.value()['blocks']]
    assert after['counts']['workspace_collaboration_crdt_updates'] == before['counts']['workspace_collaboration_crdt_updates'] + len(pending.update_frames)
    assert after['counts']['workspace_collaboration_revisions'] == before['counts']['workspace_collaboration_revisions'] + 1
    for table in ('workspace_collaboration_operations','workspace_collaboration_events',
                  'workspace_collaboration_audit_events','workspace_collaboration_outbox'):
        assert after['counts'][table] == before['counts'][table] + len(pending.update_frames) + 1
    replay = commit.commit_prepared_detach(store.iam, pending)
    assert replay['replayed'] is True
    assert {k:v for k,v in replay.items() if k != 'replayed'} == {k:v for k,v in result.items() if k != 'replayed'}
    assert inspect(store, 'page-e1', child) == after
    text_update(store, ws, 'page-e1', 'synced-original', '저장 이후 원본 변경')
    target_after_source_edit = inspect(store, 'page-e1', child)['target_snapshot']
    assert target_after_source_edit == after['target_snapshot']
    assert commit.commit_prepared_detach(store.iam, pending)['replayed'] is True
    assert inspect(store, 'page-e1', child)['target_snapshot'] == target_after_source_edit


def test_ui_detach_commit_late_outbox_failure_rolls_back_all_surfaces(environment):
    store, ws = environment; child, _, _ = setup_resources(store, ws)
    pending = prepare_all(store, ws, child); before = inspect(store, 'page-e1', child)
    with pytest.raises(InjectedFailure):
        commit.commit_prepared_detach(store.iam, pending, fault='after_outbox')
    assert inspect(store, 'page-e1', child) == before
    result = commit.commit_prepared_detach(store.iam, pending)
    assert result['replayed'] is False


def test_ui_detach_commit_rejects_source_change_before_any_target_write(environment):
    store, ws = environment; child, _, _ = setup_resources(store, ws)
    pending = prepare_all(store, ws, child)
    text_update(store, ws, 'page-e1', 'synced-original', '해제보다 먼저 저장')
    before = inspect(store, 'page-e1', child)
    with pytest.raises(synced.SyncedSourceChanged):
        commit.commit_prepared_detach(store.iam, pending)
    assert inspect(store, 'page-e1', child) == before


def test_ui_detach_commit_keeps_existing_child_page_and_writes_only_a_page_link(environment):
    store, ws = environment
    insert_synced(store, 'page-e1', 'synced-original', content='canonical A')
    linked = create(store, ws, parent='', operation='detach-existing-child-page')['page']['id']
    store.iam.execute_typed_command('owner', 'page-e1', command_kind='block.insert',
        target_id='synced-owned-child-link',
        payload={'type':'child_page','content':'검증용 하위 페이지',
                 'props':{'page_id':linked,'indent':1},'after_block_id':'synced-original'},
        operation_id='detach-source-child-block', **request(store))
    destination = create(store, ws, operation='detach-child-instance-page')['page']['id']
    insert_synced(store, destination, 'synced-instance',
                  origin={'document_id':'page-e1','block_id':'synced-original'})
    visible = synced.read_synced_block(store.iam, 'owner', 'page-e1', 'synced-original')['tree']
    assert any(item['id']=='synced-owned-child-link' for item in visible['children'])
    pending = prepare_all(store, ws, destination)
    plan = pending.body.placement.content.value()
    assert {'source':{'kind':'child_page','document_id':linked},
            'destination':{'kind':'page_link','document_id':linked},
            'status':'linked_existing_page'} in plan['reference_plan']
    assert pending.body.value()['required_integrations'] == ['atomic_destination_writer']
    before = inspect(store, 'page-e1', destination)
    with store.iam._reader() as con:
        count_before = con.execute('SELECT COUNT(*) FROM workspace_collaboration_documents').fetchone()[0]
        linked_before = dict(store.iam._document(con, linked))
    result = commit.commit_prepared_detach(store.iam, pending)
    after = inspect(store, 'page-e1', destination)
    with store.iam._reader() as con:
        assert con.execute('SELECT COUNT(*) FROM workspace_collaboration_documents').fetchone()[0] == count_before
        assert dict(store.iam._document(con, linked)) == linked_before
    assert result['copied_database_ids'] == result['copied_file_ids'] == []
    assert after['source_snapshot'] == before['source_snapshot']
    assert after['source_frames'] == before['source_frames']
    source_link = next(block for block in after['source_snapshot']['blocks'] if block['id']=='synced-owned-child-link')
    assert source_link['type']=='child_page' and source_link['props']['page_id']==linked
    linked_blocks = [block for block in after['target_snapshot']['blocks'] if block['props'].get('page_id')==linked]
    assert len(linked_blocks)==1 and linked_blocks[0]['type']=='link_to_page'
    projected = search_projection.materialize([after['target_frames']])[0]
    assert next(block for block in projected['blocks'] if block['id']==linked_blocks[0]['id'])['type']=='link_to_page'
    assert commit.commit_prepared_detach(store.iam, pending)['replayed'] is True


def test_ui_detach_service_replays_exact_receipt_before_stale_source_preparation(environment):
    store, ws = environment; child, _, _ = setup_resources(store, ws)
    request = service_request(store, ws, child)
    before = inspect(store, 'page-e1', child)
    assert service.read_synced_detach_result(store.iam, 'owner', ws, child, 'synced-instance', **request) == {'state':'unknown'}
    assert inspect(store, 'page-e1', child) == before
    saved = service.detach_synced_block(store.iam, 'owner', ws, child, 'synced-instance', **request)
    after = inspect(store, 'page-e1', child)
    assert saved['replayed'] is False and after['source_snapshot'] == before['source_snapshot']
    status = service.read_synced_detach_result(store.iam, 'owner', ws, child, 'synced-instance', **request)
    assert status['state'] == 'committed' and status['result']['operation_id'] == request['operation_id']
    assert status['current']['document_sequence'] == saved['document_sequence']
    text_update(store, ws, 'page-e1', 'synced-original', 'later changed source')
    stable_target = inspect(store, 'page-e1', child)['target_snapshot']
    assert service.detach_synced_block(store.iam, 'owner', ws, child, 'synced-instance', **request)['replayed'] is True
    assert inspect(store, 'page-e1', child)['target_snapshot'] == stable_target


def test_ui_detach_service_late_failure_has_unknown_receipt_and_safe_retry(environment):
    store, ws = environment; child, _, _ = setup_resources(store, ws)
    request = service_request(store, ws, child, operation='detach-service-late-fault')
    before = inspect(store, 'page-e1', child)
    with pytest.raises(InjectedFailure):
        service.detach_synced_block(store.iam, 'owner', ws, child, 'synced-instance', fault='after_outbox', **request)
    assert inspect(store, 'page-e1', child) == before
    assert service.read_synced_detach_result(store.iam, 'owner', ws, child, 'synced-instance', **request) == {'state':'unknown'}
    assert service.detach_synced_block(store.iam, 'owner', ws, child, 'synced-instance', **request)['replayed'] is False


def test_ui_detach_service_preserves_new_target_sibling_added_before_fresh_request(environment):
    store, ws = environment
    child = setup_pair(store, ws)
    ordinary = next(block for block in store.iam.read_document('owner', child)['blocks']
                    if block['type'] == 'paragraph')
    # An active collaborative frame predates the typed command. Previously its
    # replay omitted the later SQL-only insertion and detach deleted that block.
    text_update(store, ws, child, ordinary['id'], 'collaborative paragraph')
    store.iam.execute_typed_command('owner', child, command_kind='block.insert',
        target_id='remote-target-note',
        payload={'type': 'paragraph', 'content': 'another editor note', 'props': {},
                 'after_block_id': 'synced-instance'},
        operation_id='detach-target-note', **request(store, child))
    before = store.iam.read_document('owner', child)
    assert next(block for block in before['blocks'] if block['id'] == 'remote-target-note')
    frames_before = inspect(store, 'page-e1', child)['target_frames']
    assert 'remote-target-note' not in {
        block['id'] for block in search_projection.materialize([frames_before])[0]['blocks']}
    payload = service_request(store, ws, child, operation='detach-with-target-note')
    assert service.detach_synced_block(store.iam, 'owner', ws, child, 'synced-instance', **payload)['replayed'] is False
    after = store.iam.read_document('owner', child)
    assert next(block for block in after['blocks'] if block['id'] == 'remote-target-note')['content'] == 'another editor note'
    frames_after = inspect(store, 'page-e1', child)['target_frames']
    assert next(block for block in search_projection.materialize([frames_after])[0]['blocks']
                if block['id'] == 'remote-target-note')['content'] == 'another editor note'


def test_ui_detach_rejects_unreconciled_canonical_yjs_structure_without_writing(environment):
    store, ws = environment
    child = setup_pair(store, ws)
    ordinary = next(block for block in store.iam.read_document('owner', child)['blocks']
                    if block['type'] == 'paragraph')
    text_update(store, ws, child, ordinary['id'], 'first collaborative frame')
    payload = service_request(store, ws, child, operation='detach-conflicting-structure')
    store.iam.execute_typed_command('owner', child, command_kind='block.insert',
        target_id='unframed-target-note',
        payload={'type':'paragraph', 'content':'keep this note', 'props':{},
                 'after_block_id':'synced-instance'},
        operation_id='detach-conflicting-note', **request(store, child))
    # A later frame was created from the previous Yjs structure, so the typed
    # insertion is no longer provably rebased after the last frame.
    text_update(store, ws, child, ordinary['id'], 'later collaborative frame')
    before = inspect(store, 'page-e1', child)
    with pytest.raises(synced.SyncedShapeUnavailable):
        service.detach_synced_block(store.iam, 'owner', ws, child, 'synced-instance', **payload)
    assert inspect(store, 'page-e1', child) == before
