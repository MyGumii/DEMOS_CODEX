"""UI-SYNC-DETACH-RESOURCES: exact owned scope, verified files, no writes."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import pytest

from code_assist_v1.notion_collaboration import synced_detach_resources as resources, synced_blocks as synced
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable,NotFoundOrDenied
from code_assist_v1.tests.notion_collaboration.test_synced_detach_plan import (
    environment,member_environment,native_environment,archive_environment,guest_environment,image_environment,
    prepared,setup_pair,dump,text_update,grant_edit,revoke,node,
)
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import upload,request
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import insert_synced,create
from code_assist_v1.notion_collaboration.linked_databases import LinkedDatabaseShapeUnavailable


def selection():
    tree=node('root','synced_block',children=[node('table','table',database_id='db-one',rows=[{'file_id':'stale-cache'}])],synced_from=None)
    second={'id':'db-two','rows':[{'id':'row-two','body':{'blocks':[node('image','image',file_id='row-file')]}}]}
    first={'id':'db-one','rows':[{'id':'row-one','cells':{'files':[{'file_id':'cell-file'}]},
        'appearance':{'icon':'nf:icon-file','cover':'/api/notion/collaboration/files/cover-file'},
        'body':{'blocks':[node('nested','table',database_id='db-two'),
            node('rich',rich_text_html='<a href="/api/notion/collaboration/files/rich-file">file</a>')]} }],
        'templates':[{'id':'template','state':'active','body':[node('template-image','image',url='https://app.example/api/notion/collaboration/files/template-file')]},
                     {'id':'archived','state':'archived','body':[node('old-image','image',file_id='old-file')]}]}
    descriptor={'databases':[first,second,{'id':'unrelated','rows':[{'file_id':'unrelated-file'}]}]}
    return tree,descriptor


def test_ui_detach_resources_selects_canonical_rows_bodies_templates_appearance_only():
    tree,descriptor=selection();before=deepcopy((tree,descriptor))
    databases,files,references=resources._select(tree,descriptor,'source','https://app.example')
    assert [d['id'] for d in databases]==['db-one','db-two']
    assert files==['cell-file','cover-file','icon-file','rich-file','row-file','template-file']
    assert not references and (tree,descriptor)==before


def test_ui_detach_resources_external_sources_are_references_and_relations_are_not_ownership():
    tree,descriptor=selection()
    tree['children'] += [node('linked','table',linked_database={'document_id':'external','database_id':'outside'},
        views=[{'id':'view','type':'table','name':'view','config':{'filters':[{'file_id':'external-filter-file'}]}}],active_view_id='view'),
        node('child','child_page',page_id='child-page'),
        node('ref','synced_block',synced_from={'document_id':'external','block_id':'original'})]
    descriptor['databases'][0]['properties']=[{'id':'rel','type':'relation','config':{'target_database_id':'unrelated'}}]
    databases,files,references=resources._select(tree,descriptor,'source','https://app.example')
    assert [d['id'] for d in databases]==['db-one','db-two']
    assert not any('external' in file or 'unrelated' in file for file in files)
    assert {item['kind'] for item in references}=={'child_page','linked_database','synced_reference'}


def test_ui_detach_resources_shared_or_cyclic_database_reference_is_collected_once():
    tree,descriptor=selection()
    descriptor['databases'][1]['rows'][0]['body']['blocks'].append(node('back','table',database_id='db-one'))
    tree['children'].append(node('another','table',database_id='db-one'))
    assert len(resources._select(tree,descriptor,'source','https://app.example')[0])==2


def test_ui_detach_resources_foreign_urls_and_literal_text_do_not_import_files():
    tree=node('root','synced_block','/api/notion/collaboration/files/literal',synced_from=None,
        rich_text_html='<img src="https://other.example/api/notion/collaboration/files/foreign">',
        url='https://app.example/api/notion/collaboration/files/not-trusted-without-origin')
    assert resources._select(tree,{},'source','')==([],[],[])


def test_ui_detach_resources_missing_database_and_untrusted_origin_fail_without_partial_result():
    tree,descriptor=selection()
    with pytest.raises(synced.SyncedShapeUnavailable):resources._select(tree,{},'source','')
    with pytest.raises(ValueError):resources._select(tree,descriptor,'source','https://app.example/path')


def test_ui_detach_resources_invalid_linked_cache_is_rejected_not_used_as_owned_data():
    block=node('linked','table',linked_database={'document_id':'external','database_id':'outside'},
        views=[{'id':'view','type':'table','name':'view','config':{}}],active_view_id='view',rows=[{'file_id':'external-cache'}])
    with pytest.raises(LinkedDatabaseShapeUnavailable):resources._select(block,{},'source','')


def setup_resources(store,ws):
    used=upload(store,operation='detach-resource-used')['file_id']
    unused=upload(store,b'unrelated bytes','detach-resource-unused')['file_id']
    insert_synced(store,'page-e1','synced-original',content='canonical A')
    for identity,kind,props in [('resource-image','image',{'file_id':used,'url':'/api/notion/collaboration/files/'+used}),
                                ('resource-database','table',{'database_id':'database-e1','rows':[{'file_id':'stale'}]})]:
        store.iam.execute_typed_command('owner','page-e1',command_kind='block.insert',target_id=identity,
            payload={'type':kind,'content':'','props':{'indent':1,**props},'after_block_id':'synced-original'},
            operation_id='detach-resource-block-'+identity,**request(store))
    # Native child creation initializes the parent's CRDT body. Seed the full
    # hierarchy first, as a real editor would include these blocks in its state.
    child=create(store,ws,operation='detach-resource-instance-page')['page']['id']
    insert_synced(store,child,'synced-instance',origin={'document_id':'page-e1','block_id':'synced-original'})
    visible=synced.read_synced_block(store.iam,'owner',child,'synced-instance')['tree']
    assert {item['id'] for item in visible['children']}=={'resource-image','resource-database'}
    return child,used,unused


def test_ui_detach_resources_real_read_only_capture_and_outside_transaction_blob_check(environment,monkeypatch):
    store,ws=environment;child,used,unused=setup_resources(store,ws)
    grant_edit(store,'page-e1','view','detach-resource-source-view')
    grant_edit(store,child,'edit_content','detach-resource-target-edit')
    plan=prepared(store,ws,child,'outsider');before=dump(store)
    count=0;reader=store.iam._reader;verify=resources.page_copy_source._verify_blobs
    @contextmanager
    def tracked():
        nonlocal count
        with reader() as con:
            count+=1
            try:yield con
            finally:count-=1
    monkeypatch.setattr(store.iam,'_reader',tracked)
    def checked(*args):
        assert count==0
        return verify(*args)
    monkeypatch.setattr(resources.page_copy_source,'_verify_blobs',checked)
    result=resources.prepare_synced_resources(store.iam,plan);value=result.value()
    assert [f['id'] for f in value['files']]==[used] and unused not in result.payload_json
    assert [d['id'] for d in value['databases']]==['database-e1']
    assert value['commit_ready'] is False and dump(store)==before
    with reader() as con:
        con.execute('BEGIN');resources.revalidate_synced_resources(store.iam,con,result)
        with pytest.raises(ValueError):resources.revalidate_synced_resources(store.iam,con,replace(result,payload_json='{}'))


@pytest.mark.parametrize('race',['source','revoke','file'])
def test_ui_detach_resources_revalidates_after_blob_preparation(environment,monkeypatch,race):
    store,ws=environment;child,used,_=setup_resources(store,ws)
    actor='owner'
    if race=='revoke':
        actor='outsider';grant_edit(store,child,'edit_content','detach-resource-race-target')
    plan=prepared(store,ws,child,actor);verify=resources.page_copy_source._verify_blobs;after=[]
    def raced(*args):
        verify(*args)
        if race=='source':text_update(store,ws,'page-e1','synced-original','changed during resource preparation')
        elif race=='revoke':revoke(store,ws)
        else:store.iam.delete_file('owner',used,operation_id='detach-resource-delete',**request(store))
        after.append(dump(store))
    monkeypatch.setattr(resources.page_copy_source,'_verify_blobs',raced)
    with pytest.raises((synced.SyncedSourceChanged,NotFoundOrDenied)):
        resources.prepare_synced_resources(store.iam,plan)
    assert dump(store)==after[0]


def test_ui_detach_resources_corrupt_temporary_blob_prevents_capture(environment):
    store,ws=environment;child,used,_=setup_resources(store,ws);plan=prepared(store,ws,child)
    path=store.iam.get_file('owner',used)['path'];original=path.read_bytes();before=dump(store)
    try:
        path.write_bytes(b'corrupted synthetic test bytes')
        with pytest.raises(AuthorityUnavailable):resources.prepare_synced_resources(store.iam,plan)
        assert dump(store)==before
    finally:path.write_bytes(original)
