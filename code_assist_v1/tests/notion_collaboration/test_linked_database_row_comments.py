"""UI-ITEM-COMMENTS-ACCESS: linked sources expose only installed comment support."""
from code_assist_v1.notion_collaboration import linked_databases
from code_assist_v1.tests.notion_collaboration.test_database_row_comments import (
    environment,body_environment,team_environment,file_environment,member_environment,native_environment,
    archive_environment,guest_environment,image_environment,create,client_for,TARGET,
)

def grant(a,document,role,op):
    current=a.read_document('owner',document)
    return a.set_page_grant('owner',document,'subject','outsider',role=role,inherit_to_descendants=False,
        expected_workspace_epoch=current['workspace_epoch'],expected_document_epoch=current['document_epoch'],operation_id=op)

def linked_host(store,ws):
    a=store.iam;host=create(store,ws,parent='',operation='item-comment-host')['page']['id']
    current=a.read_document('owner',host)
    a.execute_typed_command('owner',host,command_kind='block.insert',target_id='item-comment-link',
        payload={'type':'table','content':'','props':{'linked_database':{'document_id':'page-e1','database_id':'database-e1'},
        'views':[{'id':'local','name':'Local','type':'table','config':{}}],'active_view_id':'local'},'after_block_id':None},
        operation_id='item-comment-link',**{'expected_'+key:current[key] for key in ('document_sequence','workspace_epoch','document_epoch')})
    grant(a,host,'view','item-comment-host-view');grant(a,'page-e1','comment','item-comment-source-comment')
    return host

def test_ui_linked_item_comments_source_roles_http_and_preservation(environment,monkeypatch):
    store,ws=environment;a=store.iam;host=linked_host(store,ws)
    client=client_for(store,monkeypatch,actor='outsider')
    linked=f'/api/notion/workspaces/{ws}/pages/{host}/linked-databases/item-comment-link'
    response=client.get(linked);assert response.status_code==200
    data=response.json;assert data['database']['capabilities']['row_comments'] is True
    assert data['database']['effective_role']=='comment'
    assert data['source']=={'document_id':'page-e1','database_id':'database-e1'}
    before_rows=a.read_database('owner','database-e1')['rows'];before_host=a.read_document('owner',host)
    source=a.read_document('outsider','page-e1')
    payload={'comment_id':'linked-comment','content':'원본 항목 댓글','anchor':TARGET,'operation_id':'linked-comment-save',
        'expected_workspace_epoch':source['workspace_epoch'],'expected_document_epoch':source['document_epoch']}
    url=f'/api/notion/workspaces/{ws}/pages/page-e1/comments'
    assert client.post(url,json=payload).status_code==201
    assert client.post(url,json=payload).status_code==201
    assert len(client.get(url+'?database_id=database-e1&row_id=row-e1').json['items'])==1
    assert client.get(f'/api/notion/workspaces/{ws}/pages/{host}/comments').json['items']==[]
    assert a.read_database('owner','database-e1')['rows']==before_rows
    assert a.read_document('owner',host)==before_host
    grant(a,'page-e1','view','item-comment-source-view')
    assert client.get(linked).json['database']['effective_role']=='view'
    current=a.read_document('outsider','page-e1');payload.update(operation_id='linked-comment-denied',comment_id='denied',
        expected_workspace_epoch=current['workspace_epoch'],expected_document_epoch=current['document_epoch'])
    assert client.post(url,json=payload).status_code==404
    assert len(a.list_comments('owner','page-e1',item_target=TARGET)['items'])==1

def test_ui_linked_item_comments_predecessor_does_not_advertise_uninstalled_support(member_environment):
    store,ws=member_environment;host=linked_host(store,ws)
    result=linked_databases.read_linked_database(store.iam,'outsider',host,'item-comment-link')
    assert result['database']['capabilities']['row_comments'] is False
    assert result['database']['capabilities']['atomic_cell_batch'] is True
