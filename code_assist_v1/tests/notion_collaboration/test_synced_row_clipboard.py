"""UI-SYNC-ROW-CLIPBOARD: exact original, scoped target and atomic recovery."""
from copy import deepcopy
from dataclasses import replace
import json
import multiprocessing

import pytest

from code_assist_v1.notion_collaboration import synced_blocks as synced, synced_row_clipboard as paste
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure, NotFoundOrDenied, OperationConflict, _canonical_json, _sha256
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_read import (
    environment, body_environment, team_environment, file_environment, member_environment,
    native_environment, archive_environment, guest_environment, image_environment,
    row_body, insert, dump, _schema, original, reference,
)
from code_assist_v1.tests.notion_collaboration.test_synced_database_text_edit import snapshot
from code_assist_v1.tests.notion_collaboration.test_page_copy_remap import block
from code_assist_v1.tests.notion_collaboration.test_synced_edit import grant_edit
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import insert_synced, text_update
from code_assist_v1.tests.notion_collaboration.test_native_page_creation import create
from code_assist_v1.tests.notion_collaboration.test_native_page_creation_http import client_for
from code_assist_v1.tests.notion_collaboration.test_synced_database_text_fences import sharing_environment, db_grant


def seed(store, kind='page', *, empty=False, nested=False):
    body = [] if empty else [block('paste-anchor', content='기존 입력', custom={'keep':[1,2]}), block('paste-keep', content='다른 글')]
    if nested:
        body = [block('paste-parent', 'toggle', '부모', children=body, open=True)]
    if kind == 'row':
        body.insert(0, original('paste-original'))
    elif kind == 'template':
        _schema(store.iam, 'database.template.create', 'paste-template',
                {'name':'원본', 'body':[original('paste-original')], 'default_values':{}}, 'paste-template-seed')
    else:
        insert(store, 'paste-original', original('paste-original'))
    row_body(store, body, 'paste-body-seed')


def payload(store, ws, *, source_document='page-e1', source_block='paste-original', anchor='paste-anchor', replace_empty=False, operation='paste-one'):
    page = store.iam.read_document('owner', 'page-e1')
    row = next(item for db in snapshot(store)['databases'] if db['id']=='database-e1' for item in db['rows'] if item['id']=='row-e1')
    from code_assist_v1.notion_collaboration import database_access
    with store.iam._reader() as con:
        epoch = database_access.resolve_on_connection(store.iam, con, 'owner', ws, 'database-e1').database_epoch if database_access.installed(con) else None
    return {'source':{'workspace_id':ws, 'document_id':source_document, 'block_id':source_block},
        'anchor_id':anchor, 'replace_empty':replace_empty, 'expected_target_version':row['version'],
        'expected_workspace_epoch':page['workspace_epoch'], 'expected_document_epoch':page['document_epoch'],
        'expected_database_epoch':epoch, 'operation_id':operation}


def run(store, ws, value, actor='owner', **options):
    return paste.insert_row_reference(store.iam, actor, ws, 'page-e1', 'database-e1', 'row-e1', **value, **options)


def receipt(store, ws, value, actor='owner'):
    return paste.read_row_reference_result(store.iam, actor, ws, 'page-e1', 'database-e1', 'row-e1', **value)


@pytest.mark.parametrize('kind', ['page','row','template'])
@pytest.mark.parametrize('nested', [False,True])
def test_row_paste_real_original_keeps_siblings_main_body_original_and_atomic_history(environment, kind, nested):
    store, ws = environment; seed(store, kind, nested=nested)
    before = snapshot(store); value = payload(store, ws)
    with store.iam._reader() as con:
        old_page = dict(store.iam._document(con, 'page-e1'))
        frames = synced._frames(con, 'page-e1', synced._generation(con, 'page-e1'))
    result = run(store, ws, value)
    after = snapshot(store)
    location, body, ref = synced.locate_block(after, result['reference_id'])
    assert location.payload()=={'kind':'row','database_id':'database-e1','row_id':'row-e1'}
    assert ref['content']=='' and not ref['props'].get('children') and not ref['props'].get('rich_text_html')
    assert ref['props']['synced_from']=={'document_id':'page-e1','block_id':'paste-original'}
    assert synced.locate_block(after,'paste-original')[2]==synced.locate_block(before,'paste-original')[2]
    for identity in ('paste-anchor','paste-keep'):
        old = deepcopy(synced.locate_block(before,identity)[2]); new = deepcopy(synced.locate_block(after,identity)[2])
        old.pop('position',None); new.pop('position',None); assert new==old
    if nested:
        assert [item['id'] for item in synced.index_blocks(body)['paste-parent']['props']['children']]==['paste-anchor',result['reference_id'],'paste-keep']
    assert after['blocks']==before['blocks']
    assert synced.read_synced_block(store.iam,'owner','page-e1',result['reference_id'])['tree']['children'][0]['content']=='자식'
    with store.iam._reader() as con:
        page = store.iam._document(con,'page-e1')
        assert page['revision']==old_page['revision']+1 and page['document_sequence']==old_page['document_sequence']+1
        assert page['content_sha256']==_sha256(_canonical_json(after))==result['content_sha256']
        assert synced._frames(con,'page-e1',synced._generation(con,'page-e1'))==frames
        for table, field in [('workspace_collaboration_operations','action'),('workspace_collaboration_audit_events','action')]:
            assert con.execute(f'SELECT COUNT(*) FROM {table} WHERE {field}=? AND operation_id=?',(paste.ACTION,value['operation_id'])).fetchone()[0]==1
    stable=dump(store); assert run(store,ws,value)=={**result,'replayed':True}
    assert receipt(store,ws,value)=={'state':'committed','result':{**result,'replayed':True}}
    assert dump(store)==stable
    with pytest.raises(OperationConflict): run(store,ws,{**value,'anchor_id':'paste-keep'})
    assert dump(store)==stable


@pytest.mark.parametrize('empty_row', [False,True])
def test_empty_row_or_exact_empty_paragraph_is_replaced_without_copying_original(environment, empty_row):
    store, ws=environment; seed(store,empty=True)
    if not empty_row: row_body(store,[block('empty-slot')],'paste-empty-slot')
    result=run(store,ws,payload(store,ws,anchor=None if empty_row else 'empty-slot',replace_empty=not empty_row))
    blocks=next(db for db in snapshot(store)['databases'] if db['id']=='database-e1')['rows'][0]['body']['blocks']
    assert [item['id'] for item in blocks]==[result['reference_id']]
    assert blocks[0]['content']==''


@pytest.mark.parametrize('fault',['after_domain','after_event','after_audit','after_outbox','after_operation'])
def test_finish_failures_roll_back_body_version_history_operation_audit_and_outbox(environment,fault):
    store,ws=environment; seed(store); value=payload(store,ws); before=dump(store)
    with pytest.raises(InjectedFailure): run(store,ws,value,fault=fault)
    assert dump(store)==before and receipt(store,ws,value)=={'state':'unknown'}
    assert run(store,ws,value)['replayed'] is False


def test_read_only_source_can_be_pasted_to_editable_target_and_replay_survives_source_revocation(environment):
    store,ws=environment; seed(store)
    source=create(store,ws,parent='',operation='paste-source')['page']['id']
    insert_synced(store,source,'readonly-original',content='읽기만 가능한 최신 원본')
    grant_edit(store,source,'view','paste-source-view'); grant_edit(store,'page-e1','edit_content','paste-target-edit')
    value=payload(store,ws,source_document=source,source_block='readonly-original')
    result=run(store,ws,value,actor='outsider')
    assert synced.read_synced_block(store.iam,'outsider','page-e1',result['reference_id'])['block']['content']=='읽기만 가능한 최신 원본'
    page=store.iam.read_document('owner',source)
    store.iam.revoke_page_grant('owner',source,'subject','outsider',expected_workspace_epoch=page['workspace_epoch'],expected_document_epoch=page['document_epoch'],operation_id='paste-revoke-source')
    stable=dump(store)
    assert run(store,ws,value,actor='outsider')['replayed'] and receipt(store,ws,value,actor='outsider')['state']=='committed'
    with pytest.raises(NotFoundOrDenied): run(store,ws,payload(store,ws,source_document=source,source_block='readonly-original',operation='paste-after-revoke'),actor='outsider')
    assert dump(store)==stable


@pytest.mark.parametrize('race',['row','source','target_role'])
def test_change_after_preparation_never_writes_stale_body_or_reference(environment,monkeypatch,race):
    store,ws=environment; seed(store); value=payload(store,ws); prepare=paste.prepare; observed=[]
    def changed(*args):
        pending=prepare(*args)
        if race=='row': row_body(store,[block('new-text',content='다른 창의 입력')],'paste-new-body')
        elif race=='source': text_update(store,ws,'page-e1','paste-original','달라진 최신 원본')
        else: grant_edit(store,'page-e1','view','paste-change-grant')
        observed.append(dump(store)); return pending
    monkeypatch.setattr(paste,'prepare',changed)
    with pytest.raises(synced.SyncedSourceChanged): run(store,ws,value)
    assert dump(store)==observed[0] and receipt(store,ws,value)=={'state':'unknown'}


@pytest.mark.parametrize('invalid',['reference','ambiguous','missing','replace_nonempty','anchor_missing','row_version'])
def test_invalid_source_or_stale_placement_has_no_effect(environment,invalid):
    store,ws=environment; seed(store); value=payload(store,ws)
    if invalid=='reference':
        insert(store,'chain',reference('chain','page-e1','paste-original')); value=payload(store,ws,source_block='chain')
    elif invalid=='ambiguous': row_body(store,[original('paste-original'),block('paste-anchor')],'paste-ambiguous'); value=payload(store,ws)
    elif invalid=='missing': value['source']['block_id']='absent'
    elif invalid=='replace_nonempty': value['replace_empty']=True
    elif invalid=='anchor_missing': value['anchor_id']='absent'
    else: value['expected_target_version']+=1
    before=dump(store)
    with pytest.raises((synced.SyncedShapeUnavailable,synced.SyncedSourceChanged,NotFoundOrDenied)): run(store,ws,value)
    assert dump(store)==before


def test_rehashed_prepared_body_cannot_change_unrelated_content(environment,monkeypatch):
    store,ws=environment; seed(store); value=payload(store,ws); prepare=paste.prepare; before=dump(store)
    def tamper(*args):
        pending=prepare(*args); blocks=json.loads(pending.edit.body_json); blocks[-1]['content']='불법 변경'
        encoded=_canonical_json(blocks)
        return replace(pending,edit=replace(pending.edit,body_json=encoded,body_sha256=_sha256(encoded)))
    monkeypatch.setattr(paste,'prepare',tamper)
    with pytest.raises(synced.SyncedShapeUnavailable): run(store,ws,value)
    assert dump(store)==before


def test_http_exact_request_no_store_result_replay_and_payload_smuggling_denial(environment,monkeypatch):
    store,ws=environment; seed(store); client=client_for(store,monkeypatch); value=payload(store,ws)
    url=f'/api/notion/workspaces/{ws}/pages/page-e1/databases/database-e1/rows/row-e1/synced-paste'
    unknown=client.post(url+'-result',json=value); assert unknown.status_code==200 and unknown.json=={'state':'unknown'}
    response=client.post(url,json=value); assert response.status_code==201
    assert response.headers['Cache-Control']=='no-store, max-age=0'
    assert client.post(url,json=value).status_code==200 and client.post(url+'-result',json=value).json['state']=='committed'
    before=dump(store)
    denied=client.post(url,json={**value,'blocks':[block('injected')]}); assert denied.status_code==400
    assert dump(store)==before


def test_target_reader_exposes_only_exact_current_fence_and_denies_database_only_actor(sharing_environment):
    store,ws=sharing_environment; seed(store)
    target=paste.read_row_paste_target(store.iam,'owner',ws,'page-e1','database-e1','row-e1')
    assert target['expected_database_epoch']==payload(store,ws)['expected_database_epoch']
    assert set(target)=={'contract','workspace_id','document_id','database_id','row_id','expected_target_version','expected_workspace_epoch','expected_document_epoch','expected_database_epoch'}
    db_grant(store,ws,'full_access','paste-db-only-grant')
    page=store.iam.read_document('owner','page-e1')
    store.iam.revoke_page_grant('owner','page-e1','subject','outsider',expected_workspace_epoch=page['workspace_epoch'],expected_document_epoch=page['document_epoch'],operation_id='paste-no-parent-authority')
    before=dump(store)
    with pytest.raises(NotFoundOrDenied):paste.read_row_paste_target(store.iam,'outsider',ws,'page-e1','database-e1','row-e1')
    with pytest.raises(NotFoundOrDenied):run(store,ws,payload(store,ws),actor='outsider')
    assert dump(store)==before


def test_database_authority_epoch_changed_during_prepare_cannot_save(sharing_environment,monkeypatch):
    store,ws=sharing_environment; seed(store,'row'); value=payload(store,ws); prepare=paste.prepare; observed=[]
    def changed(*args):
        pending=prepare(*args); db_grant(store,ws,'view','paste-late-db-grant'); observed.append(dump(store)); return pending
    monkeypatch.setattr(paste,'prepare',changed)
    with pytest.raises(synced.SyncedSourceChanged):run(store,ws,value)
    assert dump(store)==observed[0]


def test_archived_original_after_prepare_never_creates_reference(environment,monkeypatch):
    store,ws=environment; seed(store,'template'); value=payload(store,ws); prepare=paste.prepare; observed=[]
    def changed(*args):
        pending=prepare(*args); _schema(store.iam,'database.template.archive','paste-template',{},'paste-late-archive',target_version=1); observed.append(dump(store)); return pending
    monkeypatch.setattr(paste,'prepare',changed)
    with pytest.raises(synced.SyncedSourceChanged):run(store,ws,value)
    assert dump(store)==observed[0]


def test_clipboard_source_ids_resolve_latest_text_without_cached_body(environment):
    store,ws=environment; seed(store); value=payload(store,ws)
    text_update(store,ws,'page-e1','paste-original','복사 이후 수정된 원본')
    result=run(store,ws,value)
    assert synced.read_synced_block(store.iam,'owner','page-e1',result['reference_id'])['block']['content']=='복사 이후 수정된 원본'


def test_history_restores_exact_row_under_existing_checkpoint_boundary(environment):
    from code_assist_v1.tests.notion_collaboration.test_database_row_content import checkpoint,restore
    store,ws=environment; seed(store); value=payload(store,ws)
    before=checkpoint(store.iam,'paste-history-before'); before_blocks=deepcopy(snapshot(store)['databases'][0]['rows'][0]['body']['blocks'])
    result=run(store,ws,value)
    saved=checkpoint(store.iam,'paste-history-after'); saved_blocks=deepcopy(snapshot(store)['databases'][0]['rows'][0]['body']['blocks'])
    restore(store.iam,before['revision'],'paste-history-restore-before')
    assert snapshot(store)['databases'][0]['rows'][0]['body']['blocks']==before_blocks
    restore(store.iam,saved['revision'],'paste-history-restore-after')
    assert snapshot(store)['databases'][0]['rows'][0]['body']['blocks']==saved_blocks
    assert receipt(store,ws,value)['state']=='committed'


def _paste_worker(catalog,root,ws,value,ready,start,barrier,results):
    from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
    from code_assist_v1.notion_collaboration.workspace_image_authority import WorkspaceImageAuthority
    authority=WorkspaceImageAuthority.open(catalog,root,InMemoryIdentityDirectory(['owner']))
    prepare=paste.prepare
    def together(*args):
        pending=prepare(*args);barrier.wait(timeout=45);return pending
    paste.prepare=together;ready.put(True)
    if not start.wait(45):results.put({'error':'start timeout'});return
    try:results.put(paste.insert_row_reference(authority,'owner',ws,'page-e1','database-e1','row-e1',**value))
    except Exception as problem:results.put({'error':type(problem).__name__,'message':str(problem)})


def test_two_process_paste_has_one_reference_version_revision_audit_and_receipt(environment):
    store,ws=environment;seed(store);value=payload(store,ws)
    ctx=multiprocessing.get_context('spawn');ready,results,start,barrier=ctx.Queue(),ctx.Queue(),ctx.Event(),ctx.Barrier(2)
    workers=[ctx.Process(target=_paste_worker,args=(store.db_path,store.user_data_root,ws,value,ready,start,barrier,results)) for _ in range(2)]
    try:
        for worker in workers:worker.start()
        assert ready.get(timeout=45) and ready.get(timeout=45);start.set()
        receipts=[results.get(timeout=60),results.get(timeout=60)]
        for worker in workers:worker.join(30);assert worker.exitcode==0
        assert all('error' not in item for item in receipts),receipts
        assert {item['replayed'] for item in receipts}=={True,False}
        assert {**receipts[0],'replayed':False}=={**receipts[1],'replayed':False}
        blocks=snapshot(store)['databases'][0]['rows'][0]['body']['blocks']
        assert sum(item['type']=='synced_block' and item['props'].get('synced_from') is not None for item in blocks)==1
        with store.iam._reader() as con:
            for table in ('workspace_collaboration_operations','workspace_collaboration_audit_events'):
                assert con.execute(f'SELECT COUNT(*) FROM {table} WHERE action=?',(paste.ACTION,)).fetchone()[0]==1
    finally:
        start.set()
        for worker in workers:
            if worker.is_alive():worker.terminate()
            worker.join(5)
        ready.close();results.close()


@pytest.mark.parametrize('kind',['row','database'])
def test_archived_target_during_prepare_never_resurrects_body(sharing_environment,monkeypatch,kind):
    from code_assist_v1.notion_collaboration import database_row_trash,database_trash
    store,ws=sharing_environment;seed(store)
    if kind=='database':insert(store,'paste-db-owner',block('paste-db-owner','table',database_id='database-e1'))
    value=payload(store,ws);prepare=paste.prepare;observed=[]
    def changed(*args):
        pending=prepare(*args)
        with store.iam._reader() as con:
            page=store.iam._document(con,'page-e1');workspace=store.iam._workspace(con,ws)
            db=con.execute("SELECT row_version FROM workspace_collaboration_databases WHERE id='database-e1'").fetchone()
            row=con.execute("SELECT row_version FROM workspace_collaboration_database_rows WHERE id='row-e1'").fetchone()
        fields={'archived':True,'operation_id':'paste-late-target-archive','expected_document_sequence':page['document_sequence'],
            'expected_workspace_epoch':workspace['security_epoch'],'expected_document_epoch':page['access_epoch'],'expected_database_version':db['row_version']}
        if kind=='row':database_row_trash.set_database_row_trashed(store.iam,'owner',ws,'page-e1','database-e1','row-e1',expected_row_version=row['row_version'],**fields)
        else:database_trash.set_owned_database_trashed(store.iam,'owner',ws,'page-e1','database-e1',**fields)
        observed.append(dump(store));return pending
    monkeypatch.setattr(paste,'prepare',changed)
    with pytest.raises((synced.SyncedSourceChanged,NotFoundOrDenied)):run(store,ws,value)
    assert dump(store)==observed[0]
