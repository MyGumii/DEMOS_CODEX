"""UI-SYNC-DETACH-MAP: only owned resources and anchors become independent."""
from copy import deepcopy
from dataclasses import replace
import json
import pytest

from code_assist_v1.notion_collaboration import synced_detach_remap as remap, synced_detach_plan as plan
from code_assist_v1.notion_collaboration import synced_detach_resources as resources, synced_blocks as synced
from code_assist_v1.notion_collaboration.canonical_authority import _canonical_json,_sha256
from code_assist_v1.notion_collaboration.database_features import CollaborativeDatabaseAuthority
from code_assist_v1.tests.notion_collaboration.test_copy_row_resources import source_with_rows,block
from code_assist_v1.tests.notion_collaboration.test_synced_detach_resources import (
    environment,member_environment,native_environment,archive_environment,guest_environment,image_environment,
    setup_resources,prepared,dump,text_update,
)


def capture(*,empty=False,target='destination',change=None):
    source=source_with_rows();descriptor=source.documents[0].descriptor()
    descriptor['databases']+=source.documents[1].descriptor()['databases']
    for database in descriptor['databases']:
        for row in database['rows']:
            if 'appearance' in row:
                row['appearance']['cover']='https://app.example'+row['appearance']['cover']
    children=descriptor['blocks']+[block('second-db','table',database_id='db-b')]
    root=block('original','synced_block',content='' if empty else 'root',synced_from=None,children=children)
    descriptor['blocks']=[root]
    if change:change(descriptor,root,children)
    tree=remap.content_tree(descriptor['blocks'],'original')
    value=plan._plan(tree,{'id':'reference','props':{}},actor='owner',workspace='workspace',document=target,
        origin_document='page-a',operation='detach-map-operation',destination_ids={'reference'},local_origin='https://app.example')
    encoded=_canonical_json(value);original=_canonical_json(descriptor)
    detached=plan.PreparedSyncedDetach('owner','workspace',target,'reference','detach-map-operation',('page-a','original'),
        (synced._Source('page-a',(),original,len(original),()),),'{}',encoded,_sha256(encoded))
    databases,file_ids,refs=resources._select(tree,descriptor,'page-a','https://app.example')
    files=[v for v in json.loads(source.documents[0].files_json) if v['id'] in file_ids]
    payload=_canonical_json({'databases':databases,'files':files,'references':refs})
    return resources.PreparedSyncedResources(detached,payload,_sha256(payload))


def nodes(value):
    return [value]+[node for child in value['children'] for node in nodes(child)]


def test_ui_detach_map_canonical_rows_formula_relations_views_and_files_are_independent():
    source=capture();before=deepcopy(source)
    result=remap.remap_synced_resources(source);value=result.value()
    assert result==remap.remap_synced_resources(source) and source==before and value['commit_ready'] is False
    a,b=sorted(value['databases'],key=lambda d:d['title'])
    # The fixture titles sort "Second database" before "db-a".
    a,b=b,a
    props={p['name']:p for p in a['properties']};row=a['rows'][0]
    assert props['rel name']['config']['target_database_id']==b['id']!='db-b'
    assert row['cells'][props['rel name']['id']]==[b['rows'][0]['id']]
    assert props['external-rel name']['config']['target_database_id']=='external-db'
    assert row['cells'][props['words name']['id']]=='page-b'
    assert props['calc name']['config']['copy_property_ids']['amount']==props['amount name']['id']
    # Exercise the actual formula evaluator independently of relation access,
    # which needs a catalog connection and is asserted structurally above.
    computation=deepcopy(a);ids={props['amount name']['id'],props['calc name']['id']}
    computation['properties']=[p for p in computation['properties'] if p['id'] in ids]
    for item in computation['rows']:
        item['cells']={k:v for k,v in item['cells'].items() if k in ids}
    assert CollaborativeDatabaseAuthority._materialize_rows(None,'owner',computation)[0]['computed'][props['calc name']['id']]==4
    file=value['files'][0];assert file['source_file_id']=='file-source' and file['source_document_id']=='page-a'
    assert row['appearance']['icon']=='nf:'+file['id'] and row['body']['blocks'][1]['props']['file_id']==file['id']
    body=nodes(value['blocks'][0]);database=next(n for n in body if n['type']=='table' and n['props']['database_id']==a['id'])
    assert database['props']['active_view_id']==a['views'][0]['id'] and 'rows' not in database['props']
    assert file['id'] in row['appearance']['cover']
    assert row['body']['blocks'][0]['id']!=a['rows'][1]['body']['blocks'][0]['id']


@pytest.mark.parametrize('target',['destination','page-a'])
def test_ui_detach_map_keeps_page_links_and_outside_anchors_but_moves_owned_and_item_links(target):
    def change(doc,root,children):
        children[0]['props']['rich_text_html']=(
            '<a href="/page/page-a">page</a> <a href="/page/page-a#block-original">root</a> '
            '<a href="/page/page-a#block-unrelated">outside</a> '
            '<a href="/page/page-a?item_db=db-a&amp;item_row=row-a#block-inner">row</a>')
    source=capture(target=target,change=change);value=remap.remap_synced_resources(source).value()
    a=next(d for d in value['databases'] if d['title']=='db-a');row=a['rows'][0]
    html=value['blocks'][0]['children'][0]['props']['rich_text_html']
    assert 'href="/page/page-a">page' in html and '/page/page-a#block-unrelated' in html
    assert f'/page/{target}#block-reference' in html
    inner=row['body']['blocks'][0]['props']['children'][0]['id']
    assert f'/page/{target}?item_db={a["id"]}&amp;item_row={row["id"]}#block-{inner}' in html


def test_ui_detach_map_unwrapped_root_links_and_nested_sync_endpoints_stay_valid():
    def change(doc,root,children):
        children[0]['props']['rich_text_html']='<a href="/page/page-a#block-original">root</a>'
        children.extend([block('nested-original','synced_block',content='nested',synced_from=None),
            block('nested-ref','synced_block',synced_from={'document_id':'page-a','block_id':'nested-original'}),
            block('outer-ref','synced_block',synced_from={'document_id':'page-a','block_id':'original'}),
            block('outside-ref','synced_block',synced_from={'document_id':'page-a','block_id':'outside-original'})])
    value=remap.remap_synced_resources(capture(empty=True,change=change)).value()
    body=[n for block in value['blocks'] for n in nodes(block)]
    assert value['blocks'][0]['id']=='reference' and len({n['id'] for n in body})==len(body)
    assert '/page/destination#block-reference' in value['blocks'][0]['props']['rich_text_html']
    nested,ref,outer,outside=value['blocks'][-4:]
    assert ref['props']['synced_from']=={'document_id':'destination','block_id':nested['id']}
    assert ref['props']['sync_id']==nested['props']['sync_id']
    assert outer['props']['synced_from']=={'document_id':'page-a','block_id':'original'}
    assert outside['props']['synced_from']=={'document_id':'page-a','block_id':'outside-original'}


def test_ui_detach_map_linked_owned_database_reconnects_but_unowned_same_page_source_stays():
    def change(doc,root,children):
        for name,db,prop in [('inside','db-a','words'),('outside','outside-db','foreign-property')]:
            filter_value='amount' if name=='inside' else [{'file_id':'file-source','url':'/api/notion/collaboration/files/file-source'}]
            children.append(block('linked-'+name,'table',linked_database={'document_id':'page-a','database_id':db},
                views=[{'id':'local','name':'view','type':'table','config':{'filters':[{'property':prop,'operator':'equals','value':filter_value}]}}],active_view_id='local'))
    value=remap.remap_synced_resources(capture(change=change)).value();inside,outside=value['blocks'][0]['children'][-2:]
    a=next(d for d in value['databases'] if d['title']=='db-a');words=next(p['id'] for p in a['properties'] if p['name']=='words name')
    assert inside['props']['linked_database']=={'document_id':'destination','database_id':a['id']}
    assert inside['props']['views'][0]['config']['filters'][0]['property']==words
    assert outside['props']['linked_database']=={'document_id':'page-a','database_id':'outside-db'}
    assert outside['props']['views'][0]['config']['filters'][0]=={'property':'foreign-property','operator':'equals',
        'value':[{'file_id':'file-source','url':'/api/notion/collaboration/files/file-source'}]}
    assert inside['props']['active_view_id']==inside['props']['views'][0]['id']!='local'
    refs={item['source'].get('database_id'):item for item in value['reference_plan'] if item['source']['kind']=='linked_database'}
    assert refs['db-a']['status']=='internal' and refs['db-a']['destination']['database_id']==a['id']
    assert refs['outside-db']['status']=='preserved'
    child=next(n for n in nodes(value['blocks'][0]) if n['id']==value['block_ids']['child'])
    assert child['type']=='link_to_page' and child['props']['page_id']=='page-b'
    assert {'source':{'kind':'child_page','document_id':'page-b'},
            'destination':{'kind':'page_link','document_id':'page-b'},
            'status':'linked_existing_page'} in value['reference_plan']


def test_ui_detach_map_nested_database_child_pages_become_existing_page_links():
    def change(doc,root,children):
        db=doc['databases'][0]
        db['rows'][0]['body']['blocks'].append(block('row-child','child_page',page_id='existing-child'))
        db['templates'][0]['body'].append(block('template-child','child_page',page_id='existing-child'))
    value=remap.remap_synced_resources(capture(change=change)).value()
    db=next(item for item in value['databases'] if item['title']=='db-a')
    row_child=next(item for item in db['rows'][0]['body']['blocks'] if item['props'].get('page_id')=='existing-child')
    template_child=next(item for item in db['templates'][0]['body'] if item['props'].get('page_id')=='existing-child')
    assert row_child['type']==template_child['type']=='link_to_page'
    assert row_child['props']['page_id']==template_child['props']['page_id']=='existing-child'
    assert any(item['source']=={'kind':'child_page','document_id':'existing-child'}
               and item['status']=='linked_existing_page' for item in value['reference_plan'])


def test_ui_detach_map_partial_scope_does_not_accept_mismatched_owned_database_identity():
    def change(doc,root,children):
        children.append(block('wrong','table',linked_database={'document_id':'wrong-page','database_id':'db-a'},
            views=[{'id':'v','name':'v','type':'table','config':{}}],active_view_id='v'))
    with pytest.raises(remap.copying.CopyShapeUnavailable):remap.remap_synced_resources(capture(change=change))


def test_ui_detach_map_partial_scope_is_not_the_default_page_copy_policy():
    def change(doc,root,children):
        children.append(block('outside-ref','synced_block',synced_from={'document_id':'page-a','block_id':'outside-original'}))
    partial=remap._DetachRemapper(capture(change=change))
    with pytest.raises(remap.copying.CopyShapeUnavailable):
        remap.copying._Remapper(partial.source,partial.operation,'https://app.example')


def test_ui_detach_map_same_page_removed_wrapper_anchor_is_rewritten_exactly_once():
    def change(doc,root,children):
        root['props']['custom']={'url':'/page/page-a#block-original'}
        children.append(block('reference','synced_block',synced_from={'document_id':'page-a','block_id':'original'}))
    value=remap.remap_synced_resources(capture(empty=True,target='page-a',change=change)).value()
    assert value['blocks'][0]['id']=='reference'
    assert value['wrapper_props']['custom']['url']=='/page/page-a#block-reference'
    assert value['blocks'][-1]['id']!='reference'


def test_ui_detach_map_content_or_capture_tampering_is_rejected():
    source=capture()
    with pytest.raises(ValueError):remap.remap_synced_resources(replace(source,payload_json='{}'))
    with pytest.raises(ValueError):remap.remap_synced_resources(replace(source,detach=replace(source.detach,plan_json='{}')))


def test_ui_detach_map_real_capture_is_read_only_and_expires_on_source_change(environment):
    store,ws=environment;child,used,_=setup_resources(store,ws)
    source=resources.prepare_synced_resources(store.iam,prepared(store,ws,child));before=dump(store)
    result=remap.prepare_detached_content(store.iam,source);value=result.value()
    assert value['databases'] and value['files'][0]['source_file_id']==used and dump(store)==before
    with store.iam._reader() as con:
        con.execute('BEGIN');remap.revalidate_detached_content(store.iam,con,result)
        with pytest.raises(ValueError):remap.revalidate_detached_content(store.iam,con,replace(result,payload_json='{}'))
    text_update(store,ws,'page-e1','synced-original','newer source');before=dump(store)
    with store.iam._reader() as con:
        con.execute('BEGIN')
        with pytest.raises(synced.SyncedSourceChanged):remap.revalidate_detached_content(store.iam,con,result)
    assert dump(store)==before
