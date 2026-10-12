"""UI-SYNC-EDIT: text writes through a reference keep the original authoritative."""
import pytest
from code_assist_v1.notion_collaboration import synced_blocks as synced,synced_edit as edit
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied,OperationConflict,InjectedFailure
from code_assist_v1.notion_collaboration.page_settings import update_page_settings
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import (
    environment,member_environment,native_environment,archive_environment,guest_environment,image_environment,
    setup_pair,dump,text_update,insert_synced,create,_grant,revoke,
)
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request
from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for

def body(store,child,*,target='synced-original',text='새 동기화 내용',operation='synced-edit-text-one'):
    current=synced.read_synced_block(store.iam,'owner',child,'synced-instance')
    return {'origin':current['origin'],'witnesses':current['witnesses'],
        'updates':[{'id':target,'content':text,'rich_text_html':'<strong>'+text+'</strong>'}],'operation_id':operation}

def run(store,ws,child,payload,**kw):
    return edit.edit_synced_text(store.iam,'owner',ws,child,'synced-instance',**payload,**kw)

def test_ui_synced_edit_http_original_only_and_idempotent_receipt(environment,monkeypatch):
    store,ws=environment;child=setup_pair(store,ws);payload=body(store,child)
    before=store.iam.read_document('owner',child);client,_=client_for(monkeypatch,store)
    url=f'/api/notion/workspaces/{ws}/pages/{child}/synced-blocks/synced-instance/text'
    response=client.post(url,json=payload)
    assert response.status_code==201 and 'no-store' in response.headers['Cache-Control']
    current=synced.read_synced_block(store.iam,'owner',child,'synced-instance')
    assert current['block']['content']=='새 동기화 내용' and current['block']['props']['rich_text_html']=='<strong>새 동기화 내용</strong>'
    assert store.iam.read_document('owner',child)==before
    after=dump(store);assert client.post(url,json=payload).json==response.json and dump(store)==after
    changed={**payload,'updates':[dict(payload['updates'][0],content='other')]}
    with pytest.raises(OperationConflict):run(store,ws,child,changed)
    assert dump(store)==after

def test_ui_synced_edit_hierarchy_child_not_unrelated_sibling(environment):
    store,ws=environment;insert_synced(store,'page-e1','synced-original',content='root')
    for identity,indent in [('inside',1),('unrelated',0)]:
        store.iam.execute_typed_command('owner','page-e1',command_kind='block.insert',target_id=identity,
            payload={'type':'heading2','content':identity,'props':{'indent':indent},'after_block_id':'synced-original' if identity=='inside' else 'inside'},operation_id='synced-edit-seed-'+identity,**request(store,'page-e1'))
    child=create(store,ws,operation='synced-edit-child-page')['page']['id'];insert_synced(store,child,'synced-instance',origin={'document_id':'page-e1','block_id':'synced-original'})
    run(store,ws,child,body(store,child,target='inside'))
    current=synced.read_synced_block(store.iam,'owner',child,'synced-instance')
    assert current['block']['content']=='root' and current['tree']['children'][0]['content']=='새 동기화 내용'
    before=dump(store)
    with pytest.raises(synced.SyncedShapeUnavailable):run(store,ws,child,body(store,child,target='unrelated',operation='synced-edit-outside'))
    assert dump(store)==before

def test_ui_synced_edit_both_pages_require_edit_access_and_unlock(environment):
    store,ws=environment;child=setup_pair(store,ws);_grant(store.iam,child,'outsider','synced-edit-view-child')
    assert synced.read_synced_block(store.iam,'outsider',child,'synced-instance')['capabilities']['edit_text'] is False
    payload=body(store,child);before=dump(store)
    with pytest.raises(NotFoundOrDenied):edit.edit_synced_text(store.iam,'outsider',ws,child,'synced-instance',**payload)
    assert dump(store)==before
    update_page_settings(store.iam,'owner',ws,'page-e1',updates={'locked':True},operation_id='synced-edit-lock-original',**request(store,'page-e1'))
    assert synced.read_synced_block(store.iam,'owner',child,'synced-instance')['capabilities']['edit_text'] is False
    before=dump(store)
    with pytest.raises(PermissionError):run(store,ws,child,body(store,child))
    assert dump(store)==before

def test_ui_synced_edit_stale_witness_and_prepare_race_do_not_overwrite(environment,monkeypatch):
    store,ws=environment;child=setup_pair(store,ws);payload=body(store,child)
    text_update(store,ws,'page-e1','synced-original','newer content');before=dump(store)
    with pytest.raises(synced.SyncedSourceChanged):run(store,ws,child,payload)
    assert dump(store)==before
    payload=body(store,child);prepare=edit._prepare;after_race=[]
    def raced(*args):
        result=prepare(*args);text_update(store,ws,'page-e1','synced-original','concurrent winner');after_race.append(dump(store));return result
    monkeypatch.setattr(edit,'_prepare',raced)
    with pytest.raises(synced.SyncedSourceChanged):run(store,ws,child,payload)
    assert dump(store)==after_race[0]

def test_ui_synced_edit_same_request_commits_once_when_preparations_overlap(environment,monkeypatch):
    store,ws=environment;child=setup_pair(store,ws);payload=body(store,child);prepare=edit._prepare;winner=[]
    def raced(*args):
        result=prepare(*args)
        monkeypatch.setattr(edit,'_prepare',prepare)
        winner.append(run(store,ws,child,payload));return result
    monkeypatch.setattr(edit,'_prepare',raced)
    assert run(store,ws,child,payload)==winner[0]
    with store.iam._reader() as con:
        assert con.execute('SELECT COUNT(*) FROM workspace_collaboration_operations WHERE action=?',(edit.ACTION,)).fetchone()[0]==1

@pytest.mark.parametrize('fault',['after_domain','after_event','after_audit','after_outbox','after_operation'])
def test_ui_synced_edit_transaction_rolls_back_at_every_commit_stage(environment,fault):
    store,ws=environment;child=setup_pair(store,ws);payload=body(store,child);before=dump(store)
    with pytest.raises(InjectedFailure):run(store,ws,child,payload,fault=fault)
    assert dump(store)==before

def test_ui_synced_edit_route_has_no_legacy_fallback(monkeypatch):
    client,_=client_for(monkeypatch,object(),collaborative=False)
    response=client.post('/api/notion/workspaces/ws/pages/page/synced-blocks/block/text',json={})
    assert response.status_code==404 and 'no-store' in response.headers['Cache-Control']

def grant_edit(store,document,role,operation):
    current=store.iam.read_document('owner',document)
    store.iam.set_page_grant('owner',document,'subject','outsider',role=role,inherit_to_descendants=False,
        expected_workspace_epoch=current['workspace_epoch'],expected_document_epoch=current['document_epoch'],operation_id=operation)

@pytest.mark.parametrize('source_role,instance_role',[('view','edit_content'),('edit_content','view')])
def test_ui_synced_edit_one_sided_write_access_is_insufficient(environment,source_role,instance_role):
    store,ws=environment;child=setup_pair(store,ws)
    grant_edit(store,'page-e1',source_role,'synced-edit-one-sided-source');grant_edit(store,child,instance_role,'synced-edit-one-sided-instance')
    current=synced.read_synced_block(store.iam,'outsider',child,'synced-instance');assert current['capabilities']['edit_text'] is False
    payload=body(store,child);before=dump(store)
    with pytest.raises(NotFoundOrDenied):edit.edit_synced_text(store.iam,'outsider',ws,child,'synced-instance',**payload)
    assert dump(store)==before

def test_ui_synced_edit_revocation_during_preparation_blocks_the_commit(environment,monkeypatch):
    store,ws=environment;child=setup_pair(store,ws)
    grant_edit(store,'page-e1','edit_content','synced-edit-race-source');grant_edit(store,child,'edit_content','synced-edit-race-instance')
    payload=body(store,child);prepare=edit._prepare;after_revoke=[]
    def revoked(*args):
        delta=prepare(*args);revoke(store,ws);after_revoke.append(dump(store));return delta
    monkeypatch.setattr(edit,'_prepare',revoked)
    with pytest.raises(NotFoundOrDenied):edit.edit_synced_text(store.iam,'outsider',ws,child,'synced-instance',**payload)
    assert dump(store)==after_revoke[0]

def test_ui_synced_edit_same_page_reference_uses_one_witness(environment):
    store,ws=environment;insert_synced(store,'page-e1','synced-original',content='same page')
    insert_synced(store,'page-e1','synced-instance',origin={'document_id':'page-e1','block_id':'synced-original'})
    payload=body(store,'page-e1');assert len(payload['witnesses'])==1
    run(store,ws,'page-e1',payload)
    current=synced.read_synced_block(store.iam,'owner','page-e1','synced-instance')
    assert current['block']['content']=='새 동기화 내용'
    blocks=synced.index_blocks(__import__('json').loads(synced._current(store.iam,'owner','page-e1',64*1024*1024).descriptor_json)['blocks'])
    assert blocks['synced-instance']['content']=='' and 'rich_text_html' not in blocks['synced-instance']['props']

def test_ui_synced_edit_operation_namespace_does_not_collide_with_transport(environment):
    store,ws=environment;child=setup_pair(store,ws)
    text_update(store,ws,'page-e1','synced-original','transport update')
    with store.iam._reader() as con:
        existing=con.execute('SELECT operation_id FROM workspace_collaboration_crdt_updates WHERE document_id=? ORDER BY update_sequence DESC',('page-e1',)).fetchone()[0]
    payload=body(store,child,operation=existing);result=run(store,ws,child,payload)
    assert result['operation_id']==existing and result['update_operation_id']!=existing
    with store.iam._reader() as con:
        for table,key in [('workspace_collaboration_events','event_id'),('workspace_collaboration_outbox','outbox_id')]:
            row=con.execute(f'SELECT operation_id,payload_json FROM {table} WHERE id=?',(result[key],)).fetchone()
            assert row['operation_id']==result['update_operation_id']
            assert __import__('json').loads(row['payload_json'])['operation_id']==row['operation_id']
        audit=con.execute('SELECT operation_id FROM workspace_collaboration_audit_events WHERE id=?',(result['audit_id'],)).fetchone()
        assert audit['operation_id']==existing
    after=dump(store);assert run(store,ws,child,payload)==result and dump(store)==after
