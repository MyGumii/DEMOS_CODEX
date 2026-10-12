"""UI-SYNC-TREE: the reference contains the original's complete owned subtree."""
from copy import deepcopy
import pytest
from code_assist_v1.notion_collaboration.synced_tree import content_tree
from code_assist_v1.notion_collaboration import synced_blocks as synced
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, setup_pair, dump, text_update, insert_synced, create, _grant, revoke,
)
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request
from code_assist_v1.tests.notion_collaboration.test_page_archive_http import client_for

def block(identity,kind='paragraph',indent=0,**props):
    return {'id':identity,'type':kind,'content':identity,'props':{'indent':indent,**props}}

def ids(tree):
    return [tree['id']]+[identity for child in tree['children'] for identity in ids(child)]

def test_ui_synced_tree_flat_children_are_bounded_by_real_ancestry():
    source=[block('before'),block('origin','synced_block',2,synced_from=None),block('heading','heading1',3),
        block('callout','callout',3),block('nested',indent=4),block('toggle','toggle',3,open=False),
        block('hidden',indent=4),block('other-column',indent=4,layout_group='other'),block('after',indent=2)]
    before=deepcopy(source);tree=content_tree(source,'origin')
    assert ids(tree)==['origin','heading','callout','nested','toggle','hidden']
    assert tree['children'][2]['props']['open'] is False
    assert source==before

def test_ui_synced_tree_inline_and_identifier_children_preserve_explicit_order():
    source=[block('origin','synced_block',children=[block('inline','callout',children=['far','near'])]),block('near'),block('unrelated'),block('far')]
    before=deepcopy(source);tree=content_tree(source,'origin')
    assert ids(tree)==['origin','inline','far','near']
    assert 'children' not in tree['props'] and source==before

def test_ui_synced_tree_tabs_include_every_panel_and_nested_controller():
    source=[block('origin','synced_block'),block('tabs','tabs',1),block('first',tab_group='tabs',tab_id='a'),
        block('nested-tabs','tabs',tab_group='tabs',tab_id='b'),block('deep',tab_group='nested-tabs',tab_id='c'),block('unrelated')]
    assert ids(content_tree(source,'origin'))==['origin','tabs','first','nested-tabs','deep']

@pytest.mark.parametrize('source',[
    [block('origin',children=['missing'])],
    [block('origin',children=['child']),block('child',children=['origin'])],
    [block('origin',children=['child','child']),block('child')],
    [block('origin',children=['child',block('inline')])],
    [block('origin'),block('origin')],
])
def test_ui_synced_tree_invalid_ownership_fails_without_partial_tree(source):
    with pytest.raises(ValueError):content_tree(source,'origin')

def test_ui_synced_tree_http_current_children_and_read_only(environment,monkeypatch):
    store,ws=environment
    insert_synced(store,'page-e1','synced-original',content='canonical A')
    for identity,kind,indent,after,props in [('heading','heading2',1,'synced-original',{}),('callout','callout',1,'heading',{'background_color':'yellow'}),('nested','paragraph',2,'callout',{}),('unrelated','paragraph',0,'nested',{})]:
        store.iam.execute_typed_command('owner','page-e1',command_kind='block.insert',target_id=identity,
            payload={'type':kind,'content':identity,'props':{'indent':indent,**props},'after_block_id':after},operation_id='synced-tree-'+identity,**request(store,'page-e1'))
    # Native child creation materializes the parent's existing typed blocks into
    # CRDT frames; seed the complete hierarchy before that transition.
    child=create(store,ws,operation='synced-tree-instance-page')['page']['id']
    insert_synced(store,child,'synced-instance',origin={'document_id':'page-e1','block_id':'synced-original'})
    text_update(store,ws,'page-e1','nested','latest nested body')
    client,_=client_for(monkeypatch,store);before=dump(store)
    response=client.get(f'/api/notion/workspaces/{ws}/pages/{child}/synced-blocks/synced-instance')
    assert response.status_code==200 and 'no-store' in response.headers['Cache-Control']
    tree=response.json['tree'];assert ids(tree)==['synced-original','heading','callout','nested']
    assert tree['children'][1]['children'][0]['content']=='latest nested body'
    assert tree['children'][1]['props']['background_color']=='yellow'
    assert dump(store)==before

@pytest.mark.parametrize('action',['edit','revoke'])
def test_ui_synced_tree_change_during_tree_preparation_is_rejected(environment,monkeypatch,action):
    store,ws=environment;child=setup_pair(store,ws);build=synced.content_tree
    actor='owner' if action=='edit' else 'outsider'
    if action=='revoke':_grant(store.iam,child,'outsider','synced-tree-instance-view')
    def changed(*args):
        tree=build(*args)
        if action=='edit':text_update(store,ws,'page-e1','synced-original','changed during hierarchy')
        else:revoke(store,ws)
        return tree
    monkeypatch.setattr(synced,'content_tree',changed)
    with pytest.raises(synced.SyncedSourceChanged if action=='edit' else NotFoundOrDenied):synced.read_synced_block(store.iam,actor,child,'synced-instance')
