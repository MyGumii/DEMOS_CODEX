"""UI-DB-ITEM-APPEARANCE: durable item icons/covers and exact source authority."""
import base64
from copy import deepcopy
from io import BytesIO
import multiprocessing
import time
import pytest
from code_assist_v1 import notion_workspace_row_appearance_migration as migration
from code_assist_v1.notion_collaboration import database_row_appearance
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable,InjectedFailure,NotFoundOrDenied,OperationConflict,_canonical_json,_sha256
from code_assist_v1.notion_collaboration.editing_authority import DocumentSequenceConflict,TargetVersionConflict
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_database_row_content import (
    environment as body_environment,team_environment,file_environment,member_environment,native_environment,
    archive_environment,guest_environment,image_environment,_apply,_backup,dump,request as body_request,
    execute,checkpoint,restore,_writer,blocks,
    test_ui_db_item_body_successor_keeps_navigation_team_and_transport_gates as verify_successor,
    test_ui_db_item_body_row_delete_cascades_but_history_cannot_partially_restore as verify_delete_history,
)
from code_assist_v1.tests.notion_collaboration.test_native_page_creation_http import client_for

@pytest.fixture()
def environment(body_environment):
    old,ws=body_environment
    _apply(migration,old.db_path,old.user_data_root,'before-row-appearance-2419.db')
    return CollaborativeWorkspaceStore(old.db_path,user_data_root=old.user_data_root,identity_directory=old.identity_directory),ws

def request(authority,operation='item-appearance-save',**changes):
    return body_request(authority,operation,command_kind='database.row.appearance.set',payload={'icon':'ni:flask:blue','cover':'linear-gradient(135deg, #ecb3be, #677fbb)','cover_position':0.3},**changes)

def test_ui_db_item_appearance_migration_preserves_bodies_hashes_and_refuses_inexact_plan(body_environment):
    store,ws=body_environment; execute(store.iam,body_request(store.iam)); before=dump(store)
    with store.iam._reader() as con: original=store.iam._editing_snapshot(con,store.iam._document(con,'page-e1'))
    assert not store.capabilities()['database_row_appearance']
    with pytest.raises(AuthorityUnavailable):execute(store.iam,request(store.iam))
    plan=migration.build_plan(store.db_path,store.user_data_root)
    backup=_backup(store.db_path,store.user_data_root/'backups/appearance-plan.db')
    options=dict(exact_plan_checksum=plan['plan_checksum'],backup_confirmed=True,catalog_quiesced_confirmed=True,backup_path=backup)
    for wrong in [{'backup_confirmed':False},{'catalog_quiesced_confirmed':False},{'exact_plan_checksum':'sha256:'+'0'*64}]:
        with pytest.raises(migration.MigrationRefused):migration.apply_plan(store.db_path,store.user_data_root,**{**options,**wrong})
        assert dump(store)==before
    def fail(stage):
        if stage=='after_schema':raise RuntimeError('synthetic appearance migration failure')
    with pytest.raises(RuntimeError):migration.apply_plan(store.db_path,store.user_data_root,**options,fault_hook=fail)
    assert dump(store)==before
    migration.apply_plan(store.db_path,store.user_data_root,**options)
    with store.iam._reader() as con:
        assert store.iam._editing_snapshot(con,store.iam._document(con,'page-e1'))==original
        assert not con.execute('PRAGMA foreign_key_check').fetchall()
    assert migration.build_plan(store.db_path,store.user_data_root)['state']=='already_current'

def test_ui_db_item_appearance_partial_changes_replay_restart_preserve_body_and_cells(environment):
    store,ws=environment;a=store.iam
    body=execute(a,body_request(a)); before=a.read_database('owner','database-e1')['rows'][0]
    value=request(a); saved=execute(a,value); committed=dump(store)
    assert saved['target_version']==before['version']+1 and execute(a,value)==saved and dump(store)==committed
    with pytest.raises(OperationConflict):execute(a,{**value,'payload':{'icon':'⭐'}})
    row=a.read_database('owner','database-e1')['rows'][0]
    assert row['appearance']==saved['appearance'] and row['body']==body['body'] and row['cells']==before['cells'] and row['cell_versions']==before['cell_versions']
    value=request(a,'icon-only'); value['payload']={'icon':'🚀'}; second=execute(a,value)
    assert second['appearance']=={**saved['appearance'],'icon':'🚀'}
    value=request(a,'clear-cover');value['payload']={'cover':''};cleared=execute(a,value)
    assert cleared['appearance']=={'icon':'🚀','cover':'','cover_position':0.5}
    restarted=CollaborativeWorkspaceStore(store.db_path,user_data_root=store.user_data_root,identity_directory=store.identity_directory)
    assert restarted.iam.read_database('owner','database-e1')['rows'][0]['appearance']==cleared['appearance']
    with a._reader() as con:
        doc=a._document(con,'page-e1');assert doc['content_sha256']==_sha256(_canonical_json(a._editing_snapshot(con,doc)))
        for table in ['events','audit_events','outbox','operations']:
            assert con.execute('SELECT count(*) FROM workspace_collaboration_'+table+' WHERE operation_id=?',('item-appearance-save',)).fetchone()[0]==1

def test_ui_db_item_appearance_validation_cas_and_faults_are_atomic(environment):
    store,ws=environment;a=store.iam;value=request(a);before=dump(store)
    for payload in [{},{'title':'wrong'},{'icon':7},{'icon':'ni:missing:blue'},{'icon':'nf:bad'},
                    {'cover':'javascript:alert(1)'},{'cover':'/unbound-file'},{'cover_position':True},
                    {'cover_position':float('nan')},{'cover_position':-0.1},{'cover_position':1.1}]:
        with pytest.raises(ValueError):execute(a,{**value,'payload':payload})
        assert dump(store)==before
    for version in [None,0,True,'1',1.5]:
        with pytest.raises(ValueError):execute(a,{**value,'expected_target_version':version})
    with pytest.raises(TargetVersionConflict):execute(a,{**value,'expected_target_version':99})
    with pytest.raises(DocumentSequenceConflict):execute(a,{**value,'expected_document_sequence':999})
    for key in ['database_id','target_id']:
        with pytest.raises(NotFoundOrDenied):execute(a,{**value,key:'wrong-binding'})
    for fault in ['after_domain','after_event','after_audit','after_outbox']:
        with pytest.raises(InjectedFailure):execute(a,{**value,'fault':fault})
        assert dump(store)==before

def test_ui_db_item_appearance_history_restores_body_and_appearance_with_one_row_version(environment):
    store,ws=environment;a=store.iam;empty=checkpoint(a,'appearance-empty')
    first=execute(a,request(a));body=execute(a,body_request(a));saved=checkpoint(a,'appearance-checkpoint')
    change=request(a,'appearance-change');change['payload']={'icon':'⭐'};execute(a,change)
    execute(a,body_request(a,'body-change',payload={'blocks':blocks('changed')}))
    before=a.read_database('owner','database-e1')['rows'][0]['version'];restore(a,saved['revision'],'appearance-restore')
    row=a.read_database('owner','database-e1')['rows'][0]
    assert row['appearance']==first['appearance'] and row['body']==body['body'] and row['version']==before+1
    page=a.read_document('owner','page-e1')
    ordinary=a.save_document('owner','page-e1',title='Title change',blocks=page['blocks'],expected_revision=page['revision'],
        expected_workspace_epoch=page['workspace_epoch'],expected_document_epoch=page['document_epoch'],operation_id='normal-save-keeps-appearance')
    assert a.get_revision('owner','page-e1',ordinary['revision'])['snapshot']['databases'][0]['rows'][0]['appearance']==first['appearance']
    restore(a,empty['revision'],'appearance-restore-empty')
    row=a.read_database('owner','database-e1')['rows'][0];assert 'appearance' not in row and 'body' not in row

def test_ui_db_item_appearance_replay_rechecks_current_edit_and_read_grants(environment):
    store,ws=environment;a=store.iam;value=request(a);execute(a,value)
    page=a.read_document('owner','page-e1')
    a.set_page_grant('owner','page-e1','subject','content-editor',role='view',inherit_to_descendants=False,
        expected_workspace_epoch=page['workspace_epoch'],expected_document_epoch=page['document_epoch'],operation_id='appearance-remove-edit')
    before=dump(store)
    with pytest.raises(NotFoundOrDenied):execute(a,value)
    assert dump(store)==before
    assert a.read_database('content-editor','database-e1')['rows'][0]['appearance']
    page=a.read_document('owner','page-e1')
    a.revoke_page_grant('owner','page-e1','subject','content-editor',expected_workspace_epoch=page['workspace_epoch'],expected_document_epoch=page['document_epoch'],operation_id='appearance-remove-read')
    with pytest.raises(NotFoundOrDenied):a.read_database('content-editor','database-e1')

def test_ui_db_item_appearance_image_bytes_and_source_binding_checked_before_commit(environment):
    store,ws=environment;a=store.iam
    png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6xj8AAAAASUVORK5CYII=')
    def upload(data,suffix,document='page-e1'):
        page=a.read_document('owner',document)
        session=a.begin_upload('owner',document,original_name='icon.png',mime_type='image/png',total_size=len(data),expected_sha256=_sha256(data),chunk_size=65536,
            expected_document_sequence=page['document_sequence'],expected_workspace_epoch=page['workspace_epoch'],expected_document_epoch=page['document_epoch'],operation_id='appearance-upload-'+suffix)
        a.stage_chunk('owner',session['session_id'],chunk_index=0,stream=BytesIO(data),operation_id='appearance-chunk-'+suffix)
        return a.finalize_upload('owner',session['session_id'],operation_id='appearance-file-'+suffix)['file_id']
    file=upload(png,'valid');value=request(a);value['payload']={'icon':'nf:'+file}
    assert execute(a,value)['appearance']['icon']=='nf:'+file
    bad=upload(b'not an image','invalid');value=request(a,'invalid-bytes');value['payload']={'icon':'nf:'+bad}
    before=dump(store)
    with pytest.raises(ValueError,match='content'):execute(a,value)
    assert dump(store)==before
    page=a.read_document('owner','page-e1');a.create_document('owner',ws,'appearance-other',title='Other',blocks=[],expected_workspace_epoch=page['workspace_epoch'],operation_id='appearance-other-create')
    foreign=upload(png,'foreign','appearance-other');value=request(a,'foreign-image');value['payload']={'icon':'nf:'+foreign}
    before=dump(store)
    with pytest.raises(NotFoundOrDenied):execute(a,value)
    assert dump(store)==before

def test_ui_db_item_appearance_http_exact_receipt_and_successor_gates(environment,monkeypatch):
    store,ws=environment;client=client_for(store,monkeypatch,actor='content-editor');value=request(store.iam)
    path=f'/api/notion/workspaces/{ws}/pages/page-e1/databases';saved=client.post(path+'/commands',json=value)
    assert saved.status_code==201,saved.json
    assert client.post(path+'/commands',json=value).json==saved.json
    loaded=client.get(path+'/database-e1');assert loaded.json['database']['rows'][0]['appearance']==saved.json['appearance']
    assert loaded.json['database']['capabilities']['row_appearance']
    assert client.post(path+'/commands',json={**value,'expected_target_version':None}).status_code==400
    assert client.post(path.replace(ws,'wrong-workspace')+'/commands',json=value).status_code==404
    assert store.capabilities()['database_row_appearance'];verify_successor(environment)

def test_ui_db_item_appearance_delete_cascades_and_history_never_partially_restores(environment):
    store,ws=environment;execute(store.iam,request(store.iam));verify_delete_history(environment)
    with store.iam._reader() as con:assert not con.execute('SELECT * FROM workspace_collaboration_database_row_appearance').fetchall()

def test_ui_db_item_appearance_competing_processes_keep_one_atomic_winner(environment):
    store,ws=environment;value=request(store.iam);other={**deepcopy(value),'operation_id':'other-appearance','payload':{'icon':'⭐'}}
    context=multiprocessing.get_context('spawn');barrier=context.Barrier(2);queue=context.Queue()
    processes=[context.Process(target=_writer,args=(store.db_path,store.user_data_root,body,barrier,queue)) for body in [value,other]]
    try:
        for process in processes:process.start()
        results=sorted(queue.get(timeout=60) for _ in processes)
        for process in processes:process.join(10);assert process.exitcode==0
        assert results==['committed','conflict']
        row=store.iam.read_database('owner','database-e1')['rows'][0]
        assert row['version']==value['expected_target_version']+1 and row['appearance']['icon'] in ['⭐','ni:flask:blue']
    finally:
        for process in processes:
            if process.is_alive():process.terminate();process.join(10)
        queue.close()

def test_ui_db_item_appearance_lock_blocks_new_writes_and_replay(environment):
    from code_assist_v1.notion_collaboration.page_settings import current_page_lock_generation, update_page_settings
    from code_assist_v1.notion_collaboration.canonical_authority import bind_temporary_unlock
    store,ws=environment;a=store.iam;value=request(a);execute(a,value)
    page=a.read_document('owner','page-e1')
    update_page_settings(a,'owner',ws,'page-e1',updates={'locked':True},expected_document_sequence=page['document_sequence'],
        expected_workspace_epoch=page['workspace_epoch'],expected_document_epoch=page['document_epoch'],operation_id='lock-item-source')
    before=dump(store)
    with pytest.raises(PermissionError,match='unlock'):execute(a,value)
    with pytest.raises(PermissionError,match='unlock'):execute(a,request(a,'locked-new-appearance'))
    assert dump(store)==before
    with bind_temporary_unlock({
        'user_id':'owner','workspace_id':ws,'page_id':'page-e1',
        'lock_generation':current_page_lock_generation(a,'owner',ws,'page-e1'),
        'expires_at_ms':int(time.time()*1000)+60_000,
    }):
        result=execute(a,request(a,'temporary-locked-appearance'),actor='owner')
    assert result['document_sequence']>page['document_sequence']
    with pytest.raises(PermissionError,match='unlock'):
        execute(a,request(a,'after-temporary-appearance'))
