"""UI-SYNC-DB-DETACH: physical bodies, exact recovery and atomic isolation."""
from copy import deepcopy
from dataclasses import replace
import json
import multiprocessing

import pytest

from code_assist_v1.notion_collaboration import synced_blocks as synced, synced_detach_service as service
from code_assist_v1.notion_collaboration import synced_detach_database as database, synced_detach_commit as commit
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure, NotFoundOrDenied, OperationConflict, _canonical_json, _sha256
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_read import (
    environment, body_environment, team_environment, file_environment, member_environment,
    native_environment, archive_environment, guest_environment, image_environment,
    row_body, insert, dump, _schema, _surface, request, original, reference,
)
from code_assist_v1.tests.notion_collaboration.test_synced_database_text_edit import snapshot
from code_assist_v1.tests.notion_collaboration.test_page_copy_remap import block


def seed(store, source_kind='row', destination_kind='row', *, empty=False, nested=False):
    source = original('detach-original', '' if empty else '원본 내용 😀')
    source['props']['custom'] = {'source':[1,2]}
    source['props']['children'][0]['props'].update(text_color='red', visual_indent=1)
    peer = reference('detach-peer', 'page-e1', 'detach-original')
    keep = reference('detach-keep', 'page-e1', 'detach-original')
    bodies = {kind:[] for kind in ('page','row','template')}
    bodies[source_kind].append(source)
    bodies[destination_kind].extend([peer, keep, block('detach-unrelated', content='이 본문은 유지', custom={'untouched':True})])
    for kind, items in bodies.items():
        if not items: continue
        if nested and kind == destination_kind:
            items = [block('detach-parent', content='대상 부모', children=items)]
        if kind == 'page':
            for item in items: insert(store, item['id'], item)
        elif kind == 'row': row_body(store, items, 'detach-row-seed')
        else: _schema(store.iam, 'database.template.create', 'detach-template', {
            'name':'유지할 템플릿', 'body':items, 'default_values':{}}, 'detach-template-seed')


def payload(store, actor='owner', operation='database-body-detach'):
    current = synced.read_synced_block(store.iam, actor, 'page-e1', 'detach-peer')
    return {'origin':current['origin'], 'witnesses':current['witnesses'], 'operation_id':operation}


def run(store, ws, value, actor='owner', **kwargs):
    return service.detach_synced_block(store.iam, actor, ws, 'page-e1', 'detach-peer', **value, **kwargs)


def state(store):
    with store.iam._reader() as con:
        page = store.iam._document(con, 'page-e1')
        return dict(page), synced._frames(con, 'page-e1', synced._generation(con, 'page-e1'))


def without_positions(value):
    if isinstance(value,list):return [without_positions(item) for item in value]
    if isinstance(value,dict):return {key:without_positions(item) for key,item in value.items() if key!='position'}
    return value


@pytest.mark.parametrize('source_kind', ['page','row','template'])
@pytest.mark.parametrize('destination_kind', ['page','row','template'])
def test_detach_routes_all_nine_body_pairs_and_preserves_original_other_reference_and_exact_receipt(environment, source_kind, destination_kind):
    store, ws = environment; seed(store, source_kind, destination_kind)
    before = snapshot(store); page, frames = state(store); value = payload(store)
    source_before = deepcopy(synced.locate_block(before, 'detach-original')[2])
    result = run(store, ws, value)
    after = snapshot(store); current_page, current_frames = state(store)
    location, body, anchor = synced.locate_block(after, 'detach-peer')
    assert location.kind == destination_kind and anchor['type'] == 'paragraph'
    assert 'synced_from' not in anchor['props'] and 'sync_id' not in anchor['props']
    if source_kind==destination_kind=='page':
        from code_assist_v1.notion_collaboration.synced_tree import content_tree
        assert without_positions(content_tree(synced.locate_block(after,'detach-original')[1],'detach-original')) == without_positions(content_tree(synced.locate_block(before,'detach-original')[1],'detach-original'))
    else:assert synced.locate_block(after, 'detach-original')[2] == source_before
    assert without_positions(synced.locate_block(after, 'detach-keep')[2]) == without_positions(synced.locate_block(before, 'detach-keep')[2])
    assert synced.read_synced_block(store.iam, 'owner', 'page-e1', 'detach-keep')['tree']['children'][0]['content'] == '자식'
    indexed = synced.index_blocks(body)
    copies = [item for item in indexed.values() if item['content'] == '자식' and item['id'] != 'detach-original-child']
    assert len(copies) == 1 and copies[0]['props']['rich_text_html'] == '<b>자식</b>'
    assert copies[0]['props']['text_color'] == 'red'
    assert current_page['revision'] == page['revision'] + 1
    assert current_page['content_sha256'] == result['content_sha256'] == _sha256(_canonical_json(after))
    if destination_kind != 'page':
        assert result['storage'] == 'database_body' and result['location'] == location.payload()
        assert current_frames == frames and result['update_frames'] == 0
        assert after['blocks'] == before['blocks'] and current_page['document_sequence'] == page['document_sequence']+1
        db = next(item for item in after['databases'] if item['id'] == location.database_id)
        old_db = next(item for item in before['databases'] if item['id'] == location.database_id)
        field, identity = ('rows',location.row_id) if destination_kind == 'row' else ('templates',location.template_id)
        new = next(item for item in db[field] if item['id'] == identity)
        old = next(item for item in old_db[field] if item['id'] == identity)
        assert new['version'] == old['version']+1 == result['target_version']
        new, old = deepcopy(new), deepcopy(old)
        for item in (new, old): item.pop('body'); item.pop('version')
        assert new == old
    else:
        assert len(current_frames) == len(frames)+result['update_frames']
    stable = dump(store)
    replay = run(store, ws, value)
    assert replay == {**result, 'replayed':True} and dump(store) == stable
    receipt = service.read_synced_detach_result(store.iam, 'owner', ws, 'page-e1', 'detach-peer', **value)
    assert receipt['state'] == 'committed' and receipt['result'] == replay
    with pytest.raises(OperationConflict): run(store, ws, {**value, 'origin':{**value['origin'],'block_id':'other-original'}})
    assert dump(store) == stable


@pytest.mark.parametrize('kind', ['row','template'])
@pytest.mark.parametrize('empty,nested', [(True,False),(False,True)])
def test_empty_original_promotes_first_child_and_nested_slot_preserves_parent(environment, kind, empty, nested):
    store, ws = environment; seed(store, 'page', kind, empty=empty, nested=nested)
    before = snapshot(store); result = run(store, ws, payload(store)); after = snapshot(store)
    _location, blocks, anchor = synced.locate_block(after, 'detach-peer')
    assert anchor['content'] == ('자식' if empty else '원본 내용 😀')
    if nested:
        assert blocks[0]['id'] == 'detach-parent' and blocks[0]['content'] == '대상 부모'
    assert synced.locate_block(after, 'detach-unrelated')[2] == synced.locate_block(before, 'detach-unrelated')[2]
    assert result['storage'] == 'database_body'


@pytest.mark.parametrize('kind', ['row','template'])
@pytest.mark.parametrize('fault', ['after_domain','after_event','after_audit','after_outbox','after_operation'])
def test_database_detach_rolls_back_body_revision_receipt_and_resources_at_each_finish_stage(environment, kind, fault):
    store, ws = environment; seed(store, 'page', kind); value = payload(store); before = dump(store)
    with pytest.raises(InjectedFailure): run(store, ws, value, fault=fault)
    assert dump(store) == before
    assert service.read_synced_detach_result(store.iam, 'owner', ws, 'page-e1', 'detach-peer', **value) == {'state':'unknown'}
    assert run(store, ws, value)['replayed'] is False


@pytest.mark.parametrize('kind,allowed', [('row',True),('template',False)])
def test_content_only_actor_can_detach_row_but_cannot_change_template_structure(environment, kind, allowed):
    store, ws = environment; seed(store, 'page', kind); value = payload(store); before = dump(store)
    if allowed: assert run(store, ws, value, actor='content-editor')['storage'] == 'database_body'
    else:
        with pytest.raises(NotFoundOrDenied): run(store, ws, value, actor='content-editor')
        assert dump(store) == before


def test_changed_row_body_after_preparation_never_overwrites_newer_content(environment, monkeypatch):
    store, ws = environment; seed(store, 'page', 'row'); value = payload(store)
    prepare = database.prepare_detached_database_body; after = []
    def raced(*args):
        pending = prepare(*args)
        row_body(store, [block('newer-body',content='다른 창에서 새 입력')], 'detach-newer-body')
        after.append(dump(store)); return pending
    monkeypatch.setattr(database, 'prepare_detached_database_body', raced)
    with pytest.raises(synced.SyncedSourceChanged): run(store, ws, value)
    assert dump(store) == after[0]


def test_rehashed_unrelated_body_tampering_is_rejected_before_write(environment, monkeypatch):
    store, ws = environment; seed(store, 'page', 'row'); value = payload(store); before = dump(store)
    prepare = database.prepare_detached_database_body
    def tampered(*args):
        pending = prepare(*args); blocks = json.loads(pending.body_edit.body_json)
        blocks[-1]['content'] = '의도하지 않은 변경'
        encoded = _canonical_json(blocks)
        return replace(pending,body_edit=replace(pending.body_edit,body_json=encoded,body_sha256=_sha256(encoded)))
    monkeypatch.setattr(database, 'prepare_detached_database_body', tampered)
    with pytest.raises(synced.SyncedShapeUnavailable, match='prepared database detach write changed'): run(store, ws, value)
    assert dump(store) == before


@pytest.mark.parametrize('kind', ['row','template'])
def test_cross_page_resource_detach_with_view_only_source_copies_files_and_database_independently(environment, kind):
    from code_assist_v1.tests.notion_collaboration.test_synced_detach_resources import setup_resources, text_update
    from code_assist_v1.tests.notion_collaboration.test_synced_edit import grant_edit
    from code_assist_v1.tests.notion_collaboration.test_synced_detach_commit import inspect
    store, ws = environment; document,used,unused = setup_resources(store,ws)
    authority = store.iam; db = 'detach-destination-db'; target = 'detach-destination-body'
    authority.execute_schema_command('owner',document,database_id=db,command_kind='database.create',target_id=db,
        payload={'title':'Destination'},expected_target_version=0,operation_id='detach-destination-db-create',**request(store,document))
    peer = reference('detach-resource-peer','page-e1','synced-original')
    if kind == 'row':
        authority.execute_surface_command('owner',document,database_id=db,command_kind='database.row.create',target_id=target,
            payload={},expected_target_version=0,operation_id='detach-resource-row-create',**request(store,document))
        row = next(item for item in authority.read_database('owner',db)['rows'] if item['id']==target)
        authority.execute_surface_command('owner',document,database_id=db,command_kind='database.row.content.set',target_id=target,
            payload={'blocks':[peer]},expected_target_version=row['version'],operation_id='detach-resource-row-body',**request(store,document))
    else:
        authority.execute_schema_command('owner',document,database_id=db,command_kind='database.template.create',target_id=target,
            payload={'name':'Destination template','body':[peer],'default_values':{}},expected_target_version=0,
            operation_id='detach-resource-template-create',**request(store,document))
    grant_edit(store,'page-e1','view','detach-source-view-only')
    grant_edit(store,document,'edit' if kind=='template' else 'edit_content','detach-target-authorized')
    current = synced.read_synced_block(authority,'outsider',document,'detach-resource-peer')
    value = {'origin':current['origin'],'witnesses':current['witnesses'],'operation_id':'detach-resources-independent'}
    before = inspect(store,'page-e1',document); stable = dump(store)
    with pytest.raises(InjectedFailure):
        service.detach_synced_block(authority,'outsider',ws,document,'detach-resource-peer',**value,fault='after_outbox')
    assert dump(store)==stable
    result = service.detach_synced_block(authority,'outsider',ws,document,'detach-resource-peer',**value)
    after = inspect(store,'page-e1',document)
    assert result['storage']=='database_body' and len(result['copied_file_ids'])==len(result['copied_database_ids'])==1
    assert used not in result['copied_file_ids'] and unused not in result['copied_file_ids']
    assert after['source']==before['source'] and after['source_snapshot']==before['source_snapshot']
    assert after['source_frames']==before['source_frames'] and after['target_frames']==before['target_frames']
    assert after['target_snapshot']['blocks']==before['target_snapshot']['blocks']
    copied_db=authority.read_database('owner',result['copied_database_ids'][0])
    assert copied_db['rows'] and copied_db['rows'][0]['id']!='row-e1'
    _location,blocks,_anchor=synced.locate_block(after['target_snapshot'],'detach-resource-peer')
    indexed=synced.index_blocks(blocks)
    assert {item['props']['file_id'] for item in indexed.values() if item['type']=='image'}==set(result['copied_file_ids'])
    assert {item['props']['database_id'] for item in indexed.values() if item['type']=='table'}==set(result['copied_database_ids'])
    with authority._reader() as con:
        copied=con.execute('SELECT source_file_id,source_document_id,copied_by_subject_id,copy_operation_id FROM workspace_collaboration_files WHERE id=?',(result['copied_file_ids'][0],)).fetchone()
        assert tuple(copied)==(used,'page-e1','outsider',value['operation_id'])
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
    text_update(store,ws,'page-e1','synced-original','원본은 다시 바뀜')
    assert inspect(store,'page-e1',document)['target_snapshot']==after['target_snapshot']
    assert service.detach_synced_block(authority,'outsider',ws,document,'detach-resource-peer',**value)['replayed'] is True


@pytest.mark.parametrize('kind', ['row','template'])
def test_http_exact_receipt_survives_missing_source_without_repreparation(environment, monkeypatch, kind):
    from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for
    from code_assist_v1.tests.notion_collaboration.test_synced_detach_http import enable_in_test
    store,ws=environment;seed(store,'page',kind);value=payload(store)
    client,_actor=client_for(monkeypatch,store);enable_in_test(monkeypatch,store)
    url=f'/api/notion/workspaces/{ws}/pages/page-e1/synced-blocks/detach-peer/'
    assert client.post(url+'detach-result',json=value).json=={'state':'unknown'}
    first=client.post(url+'detach',json=value)
    assert first.status_code==201,first.json
    assert first.json['storage']=='database_body' and first.json['location']['kind']==kind
    assert 'no-store' in first.headers['Cache-Control']
    def forbidden(*args,**kw): raise AssertionError('receipt recovery must not capture source again')
    monkeypatch.setattr(service.plan,'prepare_synced_detach',forbidden)
    # Change the source's endpoint through a canonical command, then recover
    # the saved exact result with the old witnesses and caller operation.
    store.iam.execute_typed_command('owner','page-e1',command_kind='block.delete',target_id='detach-original',
        payload={},operation_id='detach-delete-old-source',**request(store))
    stable=dump(store)
    receipt=client.post(url+'detach-result',json=value);retry=client.post(url+'detach',json=value)
    assert receipt.status_code==retry.status_code==200
    assert receipt.json['state']=='committed' and retry.json['replayed'] is True
    assert receipt.json['current']['body_archived'] is False and dump(store)==stable


@pytest.mark.parametrize('boundary', ['lock','revoke','template-update'])
def test_late_lock_authority_or_template_metadata_change_fences_prepared_detach(environment, monkeypatch, boundary):
    from code_assist_v1.notion_collaboration.page_settings import update_page_settings
    store,ws=environment;seed(store,'page','template');value=payload(store)
    prepare=database.prepare_detached_database_body;after=[];actor='schema-editor'
    def raced(*args):
        pending=prepare(*args)
        if boundary=='lock':update_page_settings(store.iam,'owner',ws,'page-e1',updates={'locked':True},operation_id='detach-late-lock',**request(store))
        elif boundary=='template-update':_schema(store.iam,'database.template.update','detach-template',{'name':'다른 창에서 바꾼 이름'},'detach-late-name',target_version=1)
        else:
            current=store.iam.read_document('owner','page-e1')
            store.iam.revoke_page_grant('owner','page-e1','subject',actor,expected_workspace_epoch=current['workspace_epoch'],
                expected_document_epoch=current['document_epoch'],operation_id='detach-late-revoke')
        after.append(dump(store));return pending
    monkeypatch.setattr(database,'prepare_detached_database_body',raced)
    with pytest.raises((synced.SyncedSourceChanged,NotFoundOrDenied,PermissionError)):run(store,ws,value,actor=actor)
    assert dump(store)==after[0]


def _worker(catalog, root, ws, value, ready, start, barrier, results):
    from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
    from code_assist_v1.notion_collaboration.workspace_image_authority import WorkspaceImageAuthority
    authority = WorkspaceImageAuthority.open(catalog,root,InMemoryIdentityDirectory(['owner']))
    prepare = database.prepare_detached_database_body
    def together(*args):
        pending = prepare(*args); barrier.wait(timeout=45); return pending
    database.prepare_detached_database_body = together; ready.put(True)
    if not start.wait(45): results.put({'error':'start timeout'}); return
    try: results.put(service.detach_synced_block(authority,'owner',ws,'page-e1','detach-peer',**value))
    except Exception as exc: results.put({'error':type(exc).__name__,'message':str(exc)})


@pytest.mark.parametrize('kind', ['row','template'])
def test_two_process_detach_returns_one_receipt_and_one_physical_body_version(environment, kind):
    store, ws = environment; seed(store, 'page', kind); value = payload(store)
    ctx = multiprocessing.get_context('spawn'); ready,results,start,barrier = ctx.Queue(),ctx.Queue(),ctx.Event(),ctx.Barrier(2)
    workers = [ctx.Process(target=_worker,args=(store.db_path,store.user_data_root,ws,value,ready,start,barrier,results)) for _ in range(2)]
    try:
        for worker in workers: worker.start()
        assert ready.get(timeout=45) and ready.get(timeout=45); start.set()
        receipts = [results.get(timeout=60), results.get(timeout=60)]
        for worker in workers: worker.join(30); assert worker.exitcode == 0
        assert all('error' not in item for item in receipts),receipts
        assert {item['replayed'] for item in receipts} == {True,False}
        assert {**receipts[0],'replayed':False} == {**receipts[1],'replayed':False}
        with store.iam._reader() as con:
            assert con.execute('SELECT COUNT(*) FROM workspace_collaboration_operations WHERE action=?',(commit.ACTION,)).fetchone()[0] == 1
            assert con.execute('SELECT COUNT(*) FROM workspace_collaboration_crdt_updates').fetchone()[0] == 0
    finally:
        start.set()
        for worker in workers:
            if worker.is_alive(): worker.terminate()
            worker.join(5)
        ready.close(); results.close()
