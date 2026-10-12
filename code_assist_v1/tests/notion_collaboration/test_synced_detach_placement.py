"""UI-SYNC-DETACH-PLACE: retain destination ancestry and all source scopes."""
from copy import deepcopy
from dataclasses import replace
import json
import pytest

from code_assist_v1.notion_collaboration import synced_detach_placement as placement
from code_assist_v1.notion_collaboration import synced_detach_plan as plan, synced_detach_remap as remap
from code_assist_v1.notion_collaboration import synced_detach_resources as resources, synced_blocks as synced
from code_assist_v1.notion_collaboration.canonical_authority import _canonical_json, _sha256
from code_assist_v1.notion_collaboration.synced_tree import content_hierarchy, content_tree
from code_assist_v1.tests.notion_collaboration.test_synced_detach_resources import (
    environment,member_environment,native_environment,archive_environment,guest_environment,image_environment,
    setup_resources,prepared,dump,text_update,
)


def block(identity,kind='paragraph',content='',**props):
    return {'id':identity,'type':kind,'content':content,'props':props}


def fixture(*,empty=False,same_page=False,source=None,destination=None):
    origin=block('original','synced_block',content='' if empty else '원본',synced_from=None,
        indent=4,visual_indent=2,text_color='red',background_color='yellow',
        children=[block('callout','callout','안쪽',indent=5,background_color='blue',
                        children=[block('text',content='한😀글',indent=6,visual_indent=1)]),
                  block('tail',content='마지막',indent=5)])
    if source:source(origin)
    target='source' if same_page else 'destination'
    reference=block('reference','synced_block',synced_from={'document_id':'source','block_id':'original'},
                    indent=2,visual_indent=1)
    body=[block('before'),reference,block('after')]
    if destination:body=destination(reference)
    source_body=[origin]
    if same_page:body=source_body+body;source_body=body
    tree=content_tree(source_body,'original')
    data=plan._plan(tree,reference,actor='owner',workspace='workspace',document=target,origin_document='source',
        operation='detach-placement-op',destination_ids=content_hierarchy(body).indexed,local_origin='https://app.example')
    def captured(document,blocks):
        descriptor=_canonical_json({'title':'','icon':'','cover':'','settings':{},'blocks':blocks,'databases':[],'files':[]})
        return synced._Source(document,(),descriptor,len(descriptor),())
    sources=(captured(target,body),) if same_page else (captured(target,body),captured('source',source_body))
    encoded=_canonical_json(data)
    detach=plan.PreparedSyncedDetach('owner','workspace',target,'reference','detach-placement-op',('source','original'),
        sources,'{}',encoded,_sha256(encoded))
    payload=_canonical_json({'databases':[],'files':[],'references':[]})
    return remap.remap_synced_resources(resources.PreparedSyncedResources(detach,payload,_sha256(payload)))


def test_ui_detach_placement_keeps_slot_neighbors_and_does_not_change_inputs():
    source=fixture();before=deepcopy(source)
    result=placement.bind_detached_placement(source);value=result.value()
    assert result==placement.bind_detached_placement(source) and source==before
    # reference indent2 belongs to before; after is a top-level neighbor.
    assert value['slot']=={'block_id':'reference','parent_id':'before','ancestor_ids':['before'],
        'sibling_index':0,'before_id':None,'after_id':None}
    assert value['replacement_blocks']==source.value()['blocks']
    assert value['layers']['replacement:reference']['props']['text_color']=='red'
    assert value['layers']['destination:reference']['props']['visual_indent']==1
    assert value['nodes'][0]['source_parent_placement'] is None
    assert value['commit_ready'] is False and value['required_integrations']


@pytest.mark.parametrize('representation',['inline','ids'])
def test_ui_detach_placement_respects_explicit_order_and_ancestor_colors(representation):
    def destination(reference):
        reference['props']['indent']=0
        parent=block('outer','callout',text_color='green',background_color='gray')
        before,after=block('before'),block('after')
        parent['props']['children']=[after,reference,before] if representation=='inline' else ['after','reference','before']
        return [parent] if representation=='inline' else [parent,before,reference,after]
    result=placement.bind_detached_placement(fixture(destination=destination)).value()
    assert result['slot']['before_id']=='after' and result['slot']['after_id']=='before'
    assert result['slot']['ancestor_ids']==['outer']
    layers=result['layers']
    assert layers['destination:outer']['props']=={'text_color':'green','background_color':'gray'}
    assert result['nodes'][0]['owner_layers']==['destination:outer','destination:reference']


@pytest.mark.parametrize('empty',[False,True])
def test_ui_detach_placement_preserves_nested_columns_tabs_and_closed_toggle(empty):
    def source(root):
        root['props'].update(layout_group='source-column',layout_column=1,layout_columns=2)
        root['props']['children']=[block('tabs','tabs',tabs=[{'id':'a','name':'A'},{'id':'b','name':'B'}],active_tab_id='b',children=[
            block('fold','toggle','닫힘',tab_group='tabs',tab_id='b',open=False,children=[
                block('left',content='왼쪽',layout_group='inner',layout_column=0,layout_columns=2),
                block('right',content='오른쪽',layout_group='inner',layout_column=1,layout_columns=2)])])]
    def destination(reference):
        reference['props'].update(indent=0,tab_group='outer-tabs',tab_id='outer-b',layout_group='outer-columns',layout_column=1,layout_columns=3)
        return [block('outer-tabs','tabs',tabs=[{'id':'outer-a','name':'A'},{'id':'outer-b','name':'B'}],active_tab_id='outer-a'),reference]
    source_content=fixture(empty=empty,source=source,destination=destination)
    value=placement.bind_detached_placement(source_content).value();ids=source_content.value()['block_ids']
    assert value['slot']['ancestor_ids']==['outer-tabs']
    right=next(n for n in value['nodes'] if n['block_id']==ids['right'])
    assert right['owner_layers'][:2]==['destination:outer-tabs','destination:reference']
    assert right['owner_layers'][-2:]==['replacement:'+ids['tabs'],'replacement:'+ids['fold']]
    assert value['layers']['destination:reference']['props']['layout_group']=='outer-columns'
    assert value['layers']['replacement:'+ids['right']]['props']['layout_group']!='inner'
    assert value['layers']['replacement:'+ids['fold']]['props']['open'] is False
    assert value['layers']['replacement:'+ids['tabs']]['props']['active_tab_id']!='b'
    assert value['source_placement']['layout_group']=='source-column'
    assert right['source_placement']['layout_group']=='inner'
    assert value['removed_wrapper_layer']==('source-wrapper' if empty else None)


def test_ui_detach_placement_empty_wrapper_keeps_color_scope_once_for_all_roots():
    content=fixture(empty=True);value=placement.bind_detached_placement(content).value()
    assert len(value['replacement_blocks'])==2
    assert value['layers']['source-wrapper']['props']['background_color']=='yellow'
    assert value['layers']['source-wrapper']['props']['text_color']=='red'
    for node in value['nodes']:
        assert node['owner_layers'].count('source-wrapper')==1
    # Promoted callout overrides its own background; the second root still has
    # the removed wrapper scope. No background is painted onto each child.
    assert value['layers']['replacement:reference']['props']['background_color']=='blue'
    assert 'background_color' not in value['replacement_blocks'][1]['props']
    assert len({n['block_id'] for n in value['nodes']})==len(value['nodes'])


def test_ui_detach_placement_local_reference_descendants_stay_outside_source_colors():
    def destination(reference):
        return [block('before'),reference,block('local','callout','로컬',indent=3,children=[block('local-inline',content='보존')]),block('after')]
    value=placement.bind_detached_placement(fixture(empty=True,destination=destination)).value()
    local=value['retained_destination_children'][0]
    assert local['id']=='local' and local['children'][0]['content']=='보존'
    assert value['retained_owner_layers']==['destination:before','destination:reference']
    assert not {n['block_id'] for n in value['nodes']} & {'local','local-inline'}
    assert 'source-wrapper' not in value['retained_owner_layers']


def test_ui_detach_placement_same_page_source_and_instance_have_distinct_contexts():
    content=fixture(same_page=True,empty=True)
    value=placement.bind_detached_placement(content).value()
    assert value['destination_document_id']=='source'
    assert 'source-wrapper' in value['layers'] and 'destination:reference' in value['layers']
    assert len(value['nodes'])==3


@pytest.mark.parametrize('part',['content','resources','plan'])
def test_ui_detach_placement_rejects_modified_preparation_at_every_layer(part):
    content=fixture()
    if part=='content': content=replace(content,payload_json='{}')
    elif part=='resources':content=replace(content,resources=replace(content.resources,payload_json='{}'))
    else:content=replace(content,resources=replace(content.resources,detach=replace(content.resources.detach,plan_json='{}')))
    with pytest.raises(ValueError,match='modified'):placement.bind_detached_placement(content)


def changed_destination(content,change):
    detach=content.resources.detach;source=detach.sources[0];descriptor=json.loads(source.descriptor_json)
    change(descriptor['blocks']);raw=_canonical_json(descriptor)
    source=replace(source,descriptor_json=raw)
    return replace(content,resources=replace(content.resources,detach=replace(detach,sources=(source,*detach.sources[1:]))))


def test_ui_detach_placement_rejects_changed_endpoint_and_position():
    content=fixture()
    for field,value in [('indent',9),('synced_from',{'document_id':'source','block_id':'different'})]:
        altered=changed_destination(content,lambda blocks:blocks[1]['props'].update({field:value}))
        with pytest.raises(synced.SyncedSourceChanged):placement.bind_detached_placement(altered)


def test_ui_detach_placement_rejects_colliding_body_identity_and_cyclic_owners():
    content=fixture();identity=content.value()['block_ids']['tail']
    changed=changed_destination(content,lambda blocks:blocks.append(block(identity)))
    with pytest.raises(synced.SyncedShapeUnavailable,match='collides'):placement.bind_detached_placement(changed)
    def cycle(blocks):
        blocks[0]['props']['children']=['reference']
        # reference cannot own explicit children, so close the ancestor cycle
        # through an unrelated paragraph instead.
        blocks[0]['props']['children'].append('after');blocks[2]['props']['children']=['before']
    with pytest.raises(ValueError,match='cyclic'):placement.bind_detached_placement(changed_destination(content,cycle))


def test_ui_detach_placement_real_capture_is_read_only_and_current_authority_is_rechecked(environment):
    store,ws=environment;child,_,_=setup_resources(store,ws)
    captured=resources.prepare_synced_resources(store.iam,prepared(store,ws,child))
    content=remap.prepare_detached_content(store.iam,captured);before=dump(store)
    result=placement.prepare_detached_placement(store.iam,content)
    assert result.value()['slot']['block_id']=='synced-instance' and dump(store)==before
    with store.iam._reader() as con:
        con.execute('BEGIN');placement.revalidate_detached_placement(store.iam,con,result)
        with pytest.raises(ValueError,match='modified'):placement.revalidate_detached_placement(store.iam,con,replace(result,payload_json='{}'))
    text_update(store,ws,'page-e1','synced-original','changed after placement');before=dump(store)
    with store.iam._reader() as con:
        con.execute('BEGIN')
        with pytest.raises(synced.SyncedSourceChanged):placement.revalidate_detached_placement(store.iam,con,result)
    assert dump(store)==before
