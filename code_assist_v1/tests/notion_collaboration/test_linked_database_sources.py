"""UI-LINKED-SOURCES: current discovery without denied database metadata."""
import pytest

from code_assist_v1.notion_collaboration.linked_databases import (
    list_linkable_databases, read_linkable_database_source,
)
from code_assist_v1.tests.notion_collaboration.test_linked_database_copy import (
    environment,member_environment,native_environment,archive_environment,guest_environment,image_environment,
    create,request,dump,_grant,revoke,
)
from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for


def add_database(store,document,identity,title):
    store.iam.execute_schema_command('owner',document,database_id=identity,command_kind='database.create',
        target_id=identity,payload={'title':title},expected_target_version=0,operation_id='create-'+identity,
        **request(store,document))


def test_ui_linked_sources_current_visibility_pagination_and_literal_query(environment):
    store,ws=environment
    private=create(store,ws,parent='',operation='private-source',title='Private source')['page']['id']
    visible=create(store,ws,parent='',operation='visible-source',title='Visible source')['page']['id']
    add_database(store,private,'a-private-db','Private source secret')
    add_database(store,visible,'b-visible-db','Visible source 100%')
    _grant(store.iam,visible,'outsider','linked-discovery-grant')
    before=dump(store)
    first=list_linkable_databases(store.iam,'outsider',ws,limit=1)
    assert [item['id'] for item in first['items']]==['b-visible-db']
    assert first['next_after']=='b-visible-db'
    second=list_linkable_databases(store.iam,'outsider',ws,after=first['next_after'],limit=1)
    assert [item['id'] for item in second['items']]==['database-e1'] and second['next_after'] is None
    assert [item['id'] for item in list_linkable_databases(store.iam,'outsider',ws,query='%')['items']]==['b-visible-db']
    assert list_linkable_databases(store.iam,'outsider',ws,query='Private')['items']==[]
    assert dump(store)==before
    revoke(store,ws)
    assert [item['id'] for item in list_linkable_databases(store.iam,'outsider',ws)['items']]==['b-visible-db']
    assert list_linkable_databases(store.iam,'outsider',ws,query='no-match')['next_after'] is None


def test_ui_linked_sources_http_scope_no_store_and_limits(environment,monkeypatch):
    store,ws=environment
    client,actor=client_for(monkeypatch,store)
    url=f'/api/notion/workspaces/{ws}/database-sources'
    before=dump(store)
    response=client.get(url)
    assert response.status_code==200 and 'no-store' in response.headers['Cache-Control']
    assert response.json['items'][0]['id']=='database-e1'
    assert client.get(f'/api/notion/workspaces/{ws}/databases/database-e1').status_code==404
    assert client.get(url.replace(ws,'missing-workspace')).status_code==404
    assert client.get(url+'?q='+'x'*201).status_code==400
    actor['id']='inactive-user'
    assert client.get(url).status_code==404
    assert dump(store)==before
    for limit in (0,51,True):
        with pytest.raises(ValueError):list_linkable_databases(store.iam,'owner',ws,limit=limit)


def test_ui_r5_relation_target_direct_lookup_is_scoped_and_current(environment, monkeypatch):
    store, ws = environment
    private = create(store, ws, parent='', operation='pair-private-source', title='Private pair source')['page']['id']
    visible = create(store, ws, parent='', operation='pair-visible-source', title='Visible pair source')['page']['id']
    add_database(store, private, 'pair-private-db', 'Private pair database')
    add_database(store, visible, 'pair-visible-db', 'Visible pair database')
    _grant(store.iam, visible, 'outsider', 'pair-visible-grant')
    before = dump(store)
    item = read_linkable_database_source(store.iam, 'outsider', ws, 'pair-visible-db')['source']
    assert item['document_id'] == visible
    assert item['effective_role']
    client, actor = client_for(monkeypatch, store)
    actor['id'] = 'outsider'
    base = f'/api/notion/workspaces/{ws}/database-sources/'
    response = client.get(base + 'pair-visible-db')
    assert response.status_code == 200
    assert response.json['source'] == item
    assert 'no-store' in response.headers['Cache-Control']
    for denied in ('pair-private-db', 'missing-db'):
        assert client.get(base + denied).status_code == 404
    assert client.get(base.replace(ws, 'missing-workspace') + 'pair-visible-db').status_code == 404
    assert dump(store) == before
