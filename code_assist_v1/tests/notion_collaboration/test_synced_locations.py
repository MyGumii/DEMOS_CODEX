"""UI-SYNC-LOCATIONS: complete current inventory and private metadata fences."""
from contextlib import contextmanager
import base64
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from code_assist_v1.notion_collaboration import synced_blocks as synced, synced_locations as locations
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, setup_pair, insert_synced, create,
    text_update, dump, _grant, revoke, request,
)
from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for
from code_assist_v1.tests.notion_collaboration.test_page_archive import change
from code_assist_v1.tests.notion_collaboration.test_title_projection import append
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority


def read(store, ws, document, block='synced-instance', actor='owner'):
    return locations.read_synced_locations(store.iam, actor, document, block, workspace_id=ws)


def test_inventory_includes_all_explicit_endpoints_excludes_unrelated_and_has_no_writes(environment, monkeypatch):
    store, ws = environment; child = setup_pair(store, ws)
    insert_synced(store, 'page-e1', 'same-page-peer', origin={'document_id':'page-e1','block_id':'synced-original'})
    insert_synced(store, child, 'unrelated-original', content='private unrelated text')
    opened = 0; reader = store.iam._reader; materialize = synced.search_projection.materialize
    @contextmanager
    def tracked():
        nonlocal opened
        with reader() as con:
            opened += 1
            try: yield con
            finally: opened -= 1
    def outside(*args, **kwargs):
        assert opened == 0
        return materialize(*args, **kwargs)
    monkeypatch.setattr(store.iam, '_reader', tracked)
    monkeypatch.setattr(synced.search_projection, 'materialize', outside)
    before = dump(store); value = read(store, ws, child)
    assert value['complete'] and value['other_count'] == 2
    assert value['origin'] == {'document_id':'page-e1','block_id':'synced-original'}
    assert {e['endpoint']['block_id'] for e in value['entries']} == {'synced-original','synced-instance','same-page-peer'}
    assert value['entries'][0]['original'] and all(e['location']=={'kind':'page'} for e in value['entries'])
    assert 'canonical A' not in json.dumps(value) and 'private unrelated text' not in json.dumps(value)
    assert dump(store) == before
    source = read(store, ws, 'page-e1', 'synced-original')
    assert source['entries'] == value['entries'] and source['other_count'] == 2


def test_inventory_covers_cross_workspace_references(environment):
    store, ws = environment; child = setup_pair(store, ws)
    new = store.create_workspace('owner', name='Other', operation_id='locations-other-workspace')
    other_ws = new['workspace']['id']; epoch = new['workspace']['security_epoch']
    store.iam.create_document('owner', other_ws, 'other-page', title='다른 공간', blocks=[],
        expected_workspace_epoch=epoch, operation_id='locations-other-page')
    insert_synced(store, 'other-page', 'other-peer', origin={'document_id':'page-e1','block_id':'synced-original'})
    value = read(store, ws, child)
    entry = next(e for e in value['entries'] if e['endpoint']['document_id']=='other-page')
    assert entry['workspace_id'] == other_ws and entry['page_title'] == '다른 공간'
    assert value['other_count'] == 2


def test_inventory_uses_current_collaborative_title_instead_of_sql_title(environment):
    store,ws=environment;child=setup_pair(store,ws)
    with store.iam._reader() as con:
        frames=synced._frames(con,'page-e1',synced._generation(con,'page-e1'))
    bundle=Path(__file__).parents[2]/'notion_static/vendor/yjs-13.6.32.bundle.js'
    script="""require(process.argv[1]);const fs=require('node:fs'),Y=globalThis.NotionYjs,d=new Y.Doc();
for(const frame of JSON.parse(fs.readFileSync(0,'utf8')))Y.applyUpdate(d,new Uint8Array(Buffer.from(frame,'base64')));
const vector=Y.encodeStateVector(d),title=d.getText('page-title');title.delete(0,title.length);title.insert(0,'새 협업 제목');
process.stdout.write(Buffer.from(Y.encodeStateAsUpdate(d,vector)).toString('base64'));"""
    result=subprocess.run([shutil.which('node'),'-e',script,str(bundle)],input=json.dumps([base64.b64encode(frame).decode() for frame in frames]).encode(),capture_output=True,check=True,timeout=15)
    transport=CollaborativeEditingTransportAuthority.open(store.db_path,store.user_data_root)
    append(store.iam,transport,ws,[base64.b64decode(result.stdout)])
    before=dump(store);value=read(store,ws,child)
    assert value['entries'][0]['page_title']=='새 협업 제목' and dump(store)==before


def test_inventory_does_not_disclose_denied_peer_identity_title_or_count(environment):
    store, ws = environment; child = setup_pair(store, ws)
    private = create(store, ws, operation='private-peer-page')['page']['id']
    insert_synced(store, private, 'secret-peer', origin={'document_id':'page-e1','block_id':'synced-original'})
    _grant(store.iam, child, 'outsider', 'locations-visible-instance')
    visible = read(store, ws, child, actor='outsider')
    assert visible['other_count'] == 1 and len(visible['entries']) == 2
    assert private not in json.dumps(visible) and 'secret-peer' not in json.dumps(visible)
    _grant(store.iam, private, 'outsider', 'locations-visible-peer')
    assert read(store, ws, child, actor='outsider')['other_count'] == 2
    revoke(store, ws)
    with pytest.raises(NotFoundOrDenied): read(store, ws, child, actor='outsider')


def test_inventory_excludes_archived_page_then_restores_current_endpoint(environment):
    store, ws = environment; child = setup_pair(store, ws)
    other = create(store, ws, operation='locations-archive-peer')['page']['id']
    insert_synced(store, other, 'archived-peer', origin={'document_id':'page-e1','block_id':'synced-original'})
    change(store, ws, other, operation='locations-archive')
    before = dump(store); value = read(store, ws, child)
    assert value['other_count']==1 and other not in json.dumps(value) and dump(store)==before
    change(store, ws, other, archived=False, operation='locations-restore')
    assert read(store, ws, child)['other_count']==2


@pytest.mark.parametrize('event', ['peer-edit','new-document','peer-grant','actor-inactive'])
def test_inventory_rejects_changes_during_preparation_instead_of_returning_stale_count(environment, monkeypatch, event):
    store, ws = environment; child = setup_pair(store, ws)
    _grant(store.iam, child, 'outsider', 'locations-race-grant')
    actor = 'outsider' if event=='peer-grant' else 'owner'
    current = synced._current; calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        value = current(*args, **kwargs)
        if kwargs.get('include_title'):
            calls += 1
            if calls==2:
                if event=='peer-edit': insert_synced(store, child, 'late-peer', origin={'document_id':'page-e1','block_id':'synced-original'})
                elif event=='new-document': create(store, ws, operation='late-document')
                elif event=='peer-grant':
                    page = store.iam.read_document('owner', child)
                    store.iam.revoke_page_grant('owner', child, 'subject', 'outsider',
                        expected_workspace_epoch=page['workspace_epoch'],expected_document_epoch=page['document_epoch'],operation_id='locations-race-revoke')
                else:
                    active = store.iam.identity_directory.is_active_human
                    monkeypatch.setattr(store.iam.identity_directory,'is_active_human',lambda subject:subject!='owner' and active(subject))
        return value
    monkeypatch.setattr(synced,'_current',changed)
    with pytest.raises((synced.SyncedSourceChanged,NotFoundOrDenied)): read(store,ws,child,actor=actor)
    assert calls >= 2


def test_inventory_budget_error_never_returns_partial_counts(environment, monkeypatch):
    store, ws = environment; child = setup_pair(store, ws)
    original = synced._current
    def over_budget(*args, **kwargs):
        if kwargs.get('include_title'):
            raise synced.SyncedShapeUnavailable('synced source exceeds the preparation budget')
        return original(*args,**kwargs)
    monkeypatch.setattr(synced,'_current',over_budget)
    before=dump(store)
    with pytest.raises(synced.SyncedShapeUnavailable,match='budget'):read(store,ws,child)
    assert dump(store)==before


@pytest.mark.parametrize('wrong', ['workspace','actor','missing','legacy','cached-peer'])
def test_inventory_request_scope_and_shape_fail_closed(environment, monkeypatch, wrong):
    store,ws=environment;child=setup_pair(store,ws)
    if wrong=='cached-peer':insert_synced(store,child,'cached-peer',content='must not cache',origin={'document_id':'page-e1','block_id':'synced-original'})
    if wrong=='legacy':
        store.iam.execute_typed_command('owner',child,command_kind='block.insert',target_id='legacy',
            payload={'type':'synced_block','content':'legacy','props':{'sync_id':'legacy'},'after_block_id':None},
            operation_id='locations-legacy',**request(store,child))
    if wrong=='workspace':
        def forbidden(*args,**kwargs):raise AssertionError('wrong scope replay')
        monkeypatch.setattr(synced.search_projection,'materialize',forbidden)
    before=dump(store)
    with pytest.raises((NotFoundOrDenied,synced.SyncedShapeUnavailable)):
        read(store,'wrong-workspace' if wrong=='workspace' else ws,child,
            block='legacy' if wrong=='legacy' else 'missing' if wrong=='missing' else 'synced-instance',
            actor='inactive-user' if wrong=='actor' else 'owner')
    assert dump(store)==before


def test_inventory_http_and_ordinary_runtime_no_store(environment,monkeypatch):
    store,ws=environment;child=setup_pair(store,ws);client,actor=client_for(monkeypatch,store)
    url=f'/api/notion/workspaces/{ws}/pages/{child}/synced-blocks/synced-instance/locations'
    before=dump(store);response=client.get(url)
    assert response.status_code==200 and response.json==read(store,ws,child)
    assert 'no-store' in response.headers['Cache-Control'] and dump(store)==before
    actor['id']='inactive-user';response=client.get(url)
    assert response.status_code==404 and 'entries' not in response.json and 'no-store' in response.headers['Cache-Control']
    client,_=client_for(monkeypatch,object(),collaborative=False)
    response=client.get(url);assert response.status_code==404 and 'no-store' in response.headers['Cache-Control']
