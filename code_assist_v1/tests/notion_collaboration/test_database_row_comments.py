"""UI-DB-ITEM-COMMENTS: item identity, current authority, atomicity and replay."""
import multiprocessing
import sqlite3
import pytest

from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, InjectedFailure, NotFoundOrDenied, OperationConflict
from code_assist_v1.notion_collaboration.live_authority import CommentRevisionConflict
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_database_row_appearance import (
    environment, body_environment, team_environment, file_environment, member_environment, native_environment,
    archive_environment, guest_environment, image_environment, dump,
)
from code_assist_v1.tests.notion_collaboration.test_native_page_creation import create
from code_assist_v1.tests.notion_collaboration.test_native_page_creation_http import client_for

TARGET={'kind':'database_row','database_id':'database-e1','row_id':'row-e1'}


def epochs(a, actor='content-editor', document='page-e1'):
    page=a.read_document(actor,document)
    return {'expected_workspace_epoch':page['workspace_epoch'],'expected_document_epoch':page['document_epoch']}


def request(a, operation='item-comment-add', **changes):
    return {'content':'항목 댓글', 'anchor':dict(TARGET), 'operation_id':operation, **epochs(a), **changes}


def add(a, value, comment='comment-item', actor='content-editor'):
    return a.add_comment(actor,'page-e1',comment,**value)


def surface(a,kind,row,operation,**changes):
    page=a.read_document('content-editor','page-e1')
    values=a.read_database('content-editor','database-e1')['rows']
    version=next((value['version'] for value in values if value['id']==row),0)
    return a.execute_surface_command('content-editor','page-e1',database_id='database-e1',command_kind=kind,
        target_id=row,payload={},expected_document_sequence=page['document_sequence'],expected_target_version=version,
        operation_id=operation,**epochs(a),**changes)


def test_ui_db_item_comments_scope_reply_restart_and_source_preservation(environment):
    store,ws=environment;a=store.iam
    surface(a,'database.row.create','row-other','comment-second-row')
    before=a.read_database('owner','database-e1')
    add(a,request(a,'page-comment-add',anchor={}),comment='comment-page')
    first=add(a,request(a,mention_subject_ids=['owner']))
    add(a,request(a,'item-reply',parent_comment_id='comment-item'),comment='comment-reply')
    other={**TARGET,'row_id':'row-other'}
    add(a,request(a,'other-row',anchor=other),comment='comment-other')
    assert [item['id'] for item in a.list_comments('owner','page-e1')['items']]==['comment-page']
    assert {item['id'] for item in a.list_comments('owner','page-e1',item_target=TARGET)['items']}=={'comment-item','comment-reply'}
    assert [item['id'] for item in a.list_comments('owner','page-e1',item_target=other)['items']]==['comment-other']
    assert a.read_database('owner','database-e1')==before
    assert store.capabilities()['database_row_comments'] and before['capabilities']['row_comments']
    notification=a.list_inbox('owner',workspace_id=ws)['items'][0]
    assert notification['item_target']==TARGET and notification['comment_id']=='comment-item'
    restarted=CollaborativeWorkspaceStore(store.db_path,user_data_root=store.user_data_root,identity_directory=store.identity_directory)
    assert restarted.iam.list_comments('owner','page-e1',item_target=TARGET)['count']==2
    assert first['document_sequence']<a.read_document('owner','page-e1')['document_sequence']


def test_ui_db_item_comments_reject_cross_item_parent_foreign_source_and_malformed_targets(environment):
    store,ws=environment;a=store.iam
    surface(a,'database.row.create','row-other','comment-second-row')
    add(a,request(a))
    for anchor,block in [({**TARGET,'extra':True},None),({**TARGET,'row_id':7},None),({**TARGET,'row_id':' row-e1'},None),
        ({**TARGET,'row_id':''},None),({**TARGET,'row_id':'missing'},None),({**TARGET,'database_id':'wrong'},None),(TARGET,'block-e1')]:
        before=dump(store)
        with pytest.raises((ValueError,NotFoundOrDenied)):
            add(a,request(a,'invalid',anchor=anchor,block_id=block),comment='bad')
        assert dump(store)==before
    for anchor in ({},{**TARGET,'row_id':'row-other'}):
        before=dump(store)
        with pytest.raises(NotFoundOrDenied):add(a,request(a,'cross-reply',anchor=anchor,parent_comment_id='comment-item'),comment='cross')
        assert dump(store)==before
    other_page=create(store,ws,parent='',title='다른 문서',operation='comment-foreign-page')['page']['id']
    before=dump(store)
    with pytest.raises(NotFoundOrDenied):
        a.add_comment('owner',other_page,'wrong-page',content='invalid',anchor=TARGET,operation_id='cross-source',**epochs(a,'owner',other_page))
    assert dump(store)==before
    with pytest.raises(NotFoundOrDenied):a.list_comments('owner',other_page,item_target=TARGET)


def test_ui_db_item_comments_transactions_versions_delete_replay_and_no_duplicate_events(environment):
    store,_=environment;a=store.iam;value=request(a)
    for stage in ('after_domain','after_event','after_audit','after_outbox'):
        before=dump(store)
        with pytest.raises(InjectedFailure):add(a,{**value,'fault':stage})
        assert dump(store)==before
    saved=add(a,value);before=dump(store)
    assert add(a,value)==saved and dump(store)==before
    with pytest.raises(OperationConflict):add(a,{**value,'content':'different'})
    common={**epochs(a),'expected_row_version':1,'operation_id':'item-comment-edit'}
    edited=a.update_comment('content-editor','page-e1','comment-item',content='수정한 댓글',mention_subject_ids=[],**common)
    with pytest.raises(CommentRevisionConflict):
        a.update_comment('content-editor','page-e1','comment-item',content='stale',mention_subject_ids=[],**{**common,'operation_id':'stale-edit'})
    reaction=a.toggle_reaction('owner','page-e1','comment-item',emoji='👍',**{**epochs(a,'owner'),'expected_row_version':edited['row_version'],'operation_id':'item-reaction'})
    resolved=a.set_comment_resolved('owner','page-e1','comment-item',resolved=True,**{**epochs(a,'owner'),'expected_row_version':reaction['row_version'],'operation_id':'item-resolve'})
    reopened=a.set_comment_resolved('owner','page-e1','comment-item',resolved=False,**{**epochs(a,'owner'),'expected_row_version':resolved['row_version'],'operation_id':'item-reopen'})
    delete={**epochs(a),'expected_row_version':reopened['row_version'],'operation_id':'item-comment-delete'}
    deleted=a.delete_comment('content-editor','page-e1','comment-item',**delete);before=dump(store)
    assert a.delete_comment('content-editor','page-e1','comment-item',**delete)==deleted and dump(store)==before
    assert a.list_comments('owner','page-e1',item_target=TARGET)['count']==0
    with a._reader() as con:
        for table in ('operations','events','audit_events','outbox'):
            assert con.execute('SELECT count(*) FROM workspace_collaboration_'+table+' WHERE operation_id=?',('item-comment-add',)).fetchone()[0]==1


def test_ui_db_item_comments_current_comment_role_and_revoked_read(environment):
    store,_=environment;a=store.iam;value=request(a);add(a,value)
    page=a.read_document('owner','page-e1')
    a.set_page_grant('owner','page-e1','subject','content-editor',role='view',inherit_to_descendants=False,
        expected_workspace_epoch=page['workspace_epoch'],expected_document_epoch=page['document_epoch'],operation_id='comment-downgrade')
    before=dump(store)
    with pytest.raises(NotFoundOrDenied):add(a,value)
    with pytest.raises(NotFoundOrDenied):add(a,request(a,'view-comment'),comment='view-comment')
    assert dump(store)==before and a.list_comments('content-editor','page-e1',item_target=TARGET)['count']==1
    page=a.read_document('owner','page-e1')
    a.set_page_grant('owner','page-e1','subject','content-editor',role='comment',inherit_to_descendants=False,
        expected_workspace_epoch=page['workspace_epoch'],expected_document_epoch=page['document_epoch'],operation_id='comment-only-role')
    add(a,request(a,'comment-only-create'),comment='comment-only')
    with pytest.raises(NotFoundOrDenied):surface(a,'database.row.create','forbidden-row','comment-cannot-edit-database')
    page=a.read_document('owner','page-e1')
    a.revoke_page_grant('owner','page-e1','subject','content-editor',expected_workspace_epoch=page['workspace_epoch'],
        expected_document_epoch=page['document_epoch'],operation_id='comment-revoke')
    with pytest.raises(NotFoundOrDenied):a.list_comments('content-editor','page-e1',item_target=TARGET)


def test_ui_db_item_comments_row_delete_retires_threads_and_notifications_atomically(environment):
    store,ws=environment;a=store.iam;value=request(a,mention_subject_ids=['owner']);add(a,value)
    notification=a.list_inbox('owner',workspace_id=ws)['items'][0]
    resolve={**epochs(a),'expected_row_version':1,'operation_id':'resolve-before-row-delete','resolved':True}
    a.set_comment_resolved('content-editor','page-e1','comment-item',**resolve)
    for stage in ('after_domain','after_event','after_audit','after_outbox'):
        before=dump(store)
        with pytest.raises(InjectedFailure):surface(a,'database.row.delete','row-e1','delete-comment-row',fault=stage)
        assert dump(store)==before
    surface(a,'database.row.delete','row-e1','delete-comment-row')
    assert a.list_comments('owner','page-e1')['count']==0
    assert a.list_inbox('owner',workspace_id=ws)['count']==0
    with pytest.raises(NotFoundOrDenied):a.list_comments('owner','page-e1',item_target=TARGET)
    with pytest.raises(NotFoundOrDenied):add(a,value)
    with pytest.raises(NotFoundOrDenied):a.set_comment_resolved('content-editor','page-e1','comment-item',**resolve)
    with pytest.raises(NotFoundOrDenied):a.mark_notification_read('owner',notification['id'],operation_id='read-deleted-row')
    surface(a,'database.row.create','row-e1','reuse-row-id')
    assert a.list_comments('owner','page-e1',item_target=TARGET)['count']==0
    with pytest.raises(NotFoundOrDenied):add(a,value)
    add(a,request(a,'new-incarnation-comment'),comment='new-comment')
    with a._reader() as con:
        assert con.execute('SELECT deleted_at FROM workspace_collaboration_comments WHERE id=?',('comment-item',)).fetchone()[0] is not None
        assert not list(con.execute('PRAGMA foreign_key_check'))


def test_ui_db_item_comments_unsupported_older_schema(team_environment):
    old,_=team_environment
    assert not old.capabilities()['database_row_comments']
    with pytest.raises(AuthorityUnavailable):add(old.iam,request(old.iam))


def test_ui_db_item_comments_http_scope(environment,monkeypatch):
    store,ws=environment;a=store.iam;client=client_for(store,monkeypatch,actor='content-editor')
    base=f'/api/notion/workspaces/{ws}/pages/page-e1/comments'
    value={**request(a),'comment_id':'comment-http'}
    created=client.post(base,json=value);assert created.status_code==201,created.json
    assert client.post(base,json=value).json==created.json
    assert client.get(base).json['items']==[]
    scoped=client.get(base,query_string={'database_id':'database-e1','row_id':'row-e1'})
    assert scoped.status_code==200 and scoped.json['items'][0]['id']=='comment-http'
    assert scoped.json['item_target']==TARGET
    assert 'no-store' in scoped.headers['Cache-Control']
    assert client.get(base,query_string={'row_id':'row-e1'}).status_code==400
    assert client.get(base,query_string={'database_id':'database-e1','row_id':''}).status_code==400
    assert client.get(base,query_string={'database_id':'database-e1','row_id':'missing'}).status_code==404
    assert client.get(base.replace(ws,'wrong-workspace'),query_string={'database_id':'database-e1','row_id':'row-e1'}).status_code==404


def _writer(catalog,root,value,start,queue):
    try:
        store=CollaborativeWorkspaceStore(catalog,user_data_root=root,identity_directory=InMemoryIdentityDirectory(('owner','content-editor','schema-editor')))
        start.wait(20)
        queue.put(('ok',add(store.iam,value)))
    except Exception as exc:
        queue.put(('error',type(exc).__name__,str(exc)))


def test_ui_db_item_comments_two_windows_processes_replay_one_commit(environment):
    store,_=environment;value=request(store.iam,'item-comment-process-replay')
    ctx=multiprocessing.get_context('spawn');start=ctx.Event();queue=ctx.Queue()
    processes=[ctx.Process(target=_writer,args=(str(store.db_path),str(store.user_data_root),value,start,queue)) for _ in range(2)]
    try:
        for process in processes:process.start()
        start.set();results=[queue.get(timeout=40) for _ in processes]
        for process in processes:process.join(10);assert process.exitcode==0
        assert all(result[0]=='ok' for result in results),results
        assert results[0][1]==results[1][1]
        assert store.iam.list_comments('owner','page-e1',item_target=TARGET)['count']==1
        with store.iam._reader() as con:
            for table in ('operations','events','audit_events','outbox'):
                assert con.execute('SELECT count(*) FROM workspace_collaboration_'+table+' WHERE operation_id=?',(value['operation_id'],)).fetchone()[0]==1
    finally:
        for process in processes:
            if process.is_alive():process.terminate();process.join(5)
        queue.close()


def _row_delete_writer(catalog,root,start,queue):
    try:
        store=CollaborativeWorkspaceStore(catalog,user_data_root=root,identity_directory=InMemoryIdentityDirectory(('owner','content-editor','schema-editor')))
        queue.put(('ready',))
        start.wait(30)
        queue.put(('ok',surface(store.iam,'database.row.delete','row-e1','concurrent-row-delete')))
    except Exception as exc:
        queue.put(('error',type(exc).__name__,str(exc)))


@pytest.mark.parametrize('surface_name',['comments','inbox'])
def test_ui_db_item_comments_reader_never_mixes_before_and_after_row_deletion(environment,monkeypatch,surface_name):
    from code_assist_v1.notion_collaboration import database_row_comments
    store,ws=environment;a=store.iam
    add(a,request(a,mention_subject_ids=['owner']))
    add(a,request(a,'second-item-comment',mention_subject_ids=['owner']),comment='comment-second')
    # Only this disposable fixture uses WAL, allowing the writer to commit while
    # a reader is paused at its item-binding boundary.
    with sqlite3.connect(store.db_path) as con:
        assert con.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
    ctx=multiprocessing.get_context('spawn');start=ctx.Event();queue=ctx.Queue()
    process=ctx.Process(target=_row_delete_writer,args=(str(store.db_path),str(store.user_data_root),start,queue))
    original=database_row_comments.require_binding;triggered=False
    def interleave(con,document,anchor,**kwargs):
        nonlocal triggered
        result=original(con,document,anchor,**kwargs)
        if result and not triggered:
            triggered=True;start.set()
            reply=queue.get(timeout=30);assert reply[0]=='ok',reply
        return result
    try:
        process.start();assert queue.get(timeout=30)==('ready',)
        monkeypatch.setattr(database_row_comments,'require_binding',interleave)
        result=a.list_comments('owner','page-e1',item_target=TARGET) if surface_name=='comments' else a.list_inbox('owner',workspace_id=ws)
        assert triggered and result['count']==2
        process.join(10);assert process.exitcode==0
        with pytest.raises(NotFoundOrDenied):a.list_comments('owner','page-e1',item_target=TARGET)
        assert a.list_inbox('owner',workspace_id=ws)['count']==0
    finally:
        if process.is_alive():process.terminate();process.join(5)
        queue.close()
