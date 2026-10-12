"""UI-SYNC-DETACH: complete preparation, no content writes or implicit grants."""
from copy import deepcopy
from dataclasses import replace
import pytest

from code_assist_v1.notion_collaboration import synced_blocks as synced, synced_detach_plan as detach
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.page_settings import update_page_settings
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import (
    environment,member_environment,native_environment,archive_environment,guest_environment,image_environment,
    setup_pair,dump,text_update,insert_synced,revoke,
)
from code_assist_v1.tests.notion_collaboration.test_synced_edit import grant_edit
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request


def node(identity,kind='paragraph',content='',children=None,**props):
    return {'id':identity,'type':kind,'content':content,'props':props,'children':children or []}


def tree():
    return node('original','synced_block','본문 한😀글',[
        node('toggle','toggle','접기',[node('hidden',content='접힌 내용',rich_text_html='<b>접힌 내용</b>')],open=False),
        node('table','simple_table',rows=[['이름','값'],['A','1']],header_row=True),
        node('tabs','tabs',children=[node('panel',content='패널',tab_group='tabs',tab_id='a')],
             tabs=[{'id':'a','name':'첫 탭'}],active_tab_id='a'),
    ],synced_from=None,indent=2,text_color='red',custom={'value':'original'})


def plan(value=None,**kw):
    args=dict(actor='owner',workspace='workspace',document='destination',origin_document='source',
              operation='detach-one',destination_ids={'reference','existing'},local_origin='https://app.example')
    args.update(kw)
    instance={'id':'reference','type':'synced_block','content':'','props':{'synced_from':{'document_id':'source','block_id':'original'},'indent':1}}
    return detach._plan(value or tree(),instance,**args)


def flatten(value):
    return [value]+[n for child in value['children'] for n in flatten(child)]


def test_ui_detach_plan_preserves_all_owned_content_and_is_deterministic():
    original=tree();before=deepcopy(original);first=plan(original)
    assert plan(original)==first and original==before
    assert len(flatten(first['blocks'][0]))==6
    root=first['blocks'][0];assert root['id']=='reference' and root['type']=='paragraph'
    assert root['content']=='본문 한😀글' and root['props']['text_color']=='red'
    assert 'synced_from' not in root['props'] and 'sync_id' not in root['props']
    assert root['children'][0]['props']['open'] is False
    assert root['children'][0]['children'][0]['props']['rich_text_html']=='<b>접힌 내용</b>'
    assert root['children'][1]['props']['rows']==[['이름','값'],['A','1']]
    assert root['props']['custom']=={'value':'original'}
    assert first['source_placement']=={'indent':2} and first['destination_placement']=={'indent':1}
    assert not first['unwrap_empty_root'] and not first['requires_resource_resolution']
    others=plan(operation='detach-two')['block_ids']
    assert all(first['block_ids'][k]!=others[k] for k in others if k!='original')


def test_ui_detach_plan_empty_wrapper_promotes_first_child_and_keeps_all_descendants():
    original=tree();original['content']='';result=plan(original)
    blocks=[n for block in result['blocks'] for n in flatten(block)]
    assert result['unwrap_empty_root'] and len(blocks)==5
    assert len({n['id'] for n in blocks})==len(blocks)
    assert len(result['block_ids'])==6
    assert result['block_ids']['original']==result['block_ids']['toggle']=='reference'
    assert result['blocks'][0]['type']=='toggle' and result['blocks'][0]['id']=='reference'
    assert result['wrapper_props']['text_color']=='red'
    assert result['wrapper_props']['custom']=={'value':'original'}
    assert {n['id'] for n in blocks}==set(result['block_ids'].values())


def test_ui_detach_plan_removed_wrapper_and_promoted_child_links_have_surviving_anchors():
    original=tree();original['content']=''
    original['children'][0]['props']['rich_text_html']=(
        '<a href="/page/source#block-original">wrapper</a> '
        '<a href="#block-toggle">child</a> <a href="#block-hidden">nested</a>')
    result=plan(original);html=result['blocks'][0]['props']['rich_text_html']
    assert 'href="/page/destination#block-reference"' in html
    assert 'href="#block-reference"' in html
    assert 'href="#block-'+result['block_ids']['hidden']+'"' in html
    assert '#block-original' not in html and '#block-toggle' not in html


@pytest.mark.parametrize('rich', ['', '<br>'])
def test_ui_detach_plan_empty_leaf_or_rich_wrapper_retains_existing_anchor(rich):
    original=node('original','synced_block',synced_from=None,rich_text_html=rich)
    if rich:original['children']=[node('child',content='preserved')]
    result=plan(original)
    assert result['unwrap_empty_root'] is False and result['wrapper_props'] is None
    assert len(result['blocks'])==1 and result['blocks'][0]['id']=='reference'
    assert len(flatten(result['blocks'][0]))==1+bool(rich)



def test_ui_detach_plan_tabs_are_controller_scoped_and_layouts_are_independent():
    original=tree()
    original['children'].append(node('tabs2','tabs',children=[node('panel2',tab_group='tabs2',tab_id='a',layout_group='columns')],
                                    tabs=[{'id':'a','name':'same local id'}],active_tab_id='a'))
    result=plan(original);a,b=result['blocks'][0]['children'][2:]
    assert a['props']['tabs'][0]['id']!=b['props']['tabs'][0]['id']
    for tab in (a,b):
        assert tab['children'][0]['props']['tab_id']==tab['props']['active_tab_id']==tab['props']['tabs'][0]['id']
        assert tab['children'][0]['props']['tab_group']==tab['id']
    assert b['children'][0]['props']['layout_group']!='columns'


def test_ui_detach_plan_page_links_remain_and_only_owned_block_anchors_move():
    original=tree();markup=('<b>original &amp; hidden</b> '
        '<a href="/page/source">page</a> <a href="/page/source#block-hidden">inside</a> '
        '<a href="/page/source#block-unrelated">outside</a> <a href="#block-toggle">local</a> '
        '<a href="https://other.example/page/source#block-hidden">remote</a>')
    original['props']['rich_text_html']=markup
    result=plan(original);html=result['blocks'][0]['props']['rich_text_html']
    assert '<b>original &amp; hidden</b>' in html and 'href="/page/source">page' in html
    assert '/page/destination#block-'+result['block_ids']['hidden'] in html
    assert '/page/source#block-unrelated' in html
    assert 'href="#block-'+result['block_ids']['toggle'] in html
    assert 'https://other.example/page/source#block-hidden' in html


def test_ui_detach_plan_owned_resources_are_explicit_dependencies_not_silently_reused():
    original=tree();original['children'] += [
        node('image','image',file_id='file-source',url='/api/notion/collaboration/files/file-source'),
        node('database','table',database_id='db-source',rows=[{'stale':'cache'}]),
        node('child-page','child_page',page_id='source-child'),
    ]
    original['props']['rich_text_html']='<img src="https://app.example/api/notion/collaboration/files/file-source">'
    result=plan(original);by_kind={d['kind']:d for d in result['dependencies']}
    assert set(by_kind)=={'file','database','child_page'} and result['requires_resource_resolution']
    image,db,page=result['blocks'][0]['children'][-3:]
    assert image['props']['file_id']==by_kind['file']['destination_id']!='file-source'
    assert image['props']['url'].endswith(image['props']['file_id'])
    assert image['props']['file_id'] in result['blocks'][0]['props']['rich_text_html']
    assert db['props']['database_id']==by_kind['database']['destination_id']!='db-source'
    assert db['props']['rows']==[{'stale':'cache'}]  # A planner cannot mistake this for canonical data.
    assert page['props']['page_id']=='source-child'
    assert result['commit_ready'] is False


def test_ui_detach_plan_linked_database_preserves_source_but_clones_local_views():
    original=tree();original['children'].append(node('linked','table',
        linked_database={'document_id':'external','database_id':'external-db'},
        active_view_id='local-view',views=[{'id':'local-view','name':'필터','type':'table',
            'config':{'filters':[{'property':'source-prop','operator':'equals','value':'source-prop'}]}}]))
    result=plan(original);linked=result['blocks'][0]['children'][-1]['props']
    assert linked['linked_database']=={'document_id':'external','database_id':'external-db'}
    assert linked['active_view_id']==linked['views'][0]['id']!='local-view'
    assert linked['views'][0]['config']==original['children'][-1]['props']['views'][0]['config']
    assert result['dependencies']==[{'kind':'linked_database','source_id':'linked','source_document_id':'external','database_id':'external-db'}]


def test_ui_detach_plan_nested_originals_remap_internal_refs_but_keep_outer_origin_refs():
    original=tree();original['children'] += [
        node('nested-original','synced_block','nested',synced_from=None),
        node('nested-ref','synced_block',synced_from={'document_id':'source','block_id':'nested-original'}),
        node('root-ref','synced_block',synced_from={'document_id':'source','block_id':'original'}),
    ]
    result=plan(original);nested,ref,root_ref=result['blocks'][0]['children'][-3:]
    assert ref['props']['synced_from']=={'document_id':'destination','block_id':nested['id']}
    assert nested['props']['sync_id']==ref['props']['sync_id']
    assert root_ref['props']['synced_from']=={'document_id':'source','block_id':'original'}
    assert all(v['content']=='' for v in (ref,root_ref))


@pytest.mark.parametrize('change',[
    lambda t:t['children'].append(deepcopy(t['children'][0])),
    lambda t:t['children'][2]['children'][0]['props'].update(tab_id='missing'),
    lambda t:t['children'].append(node('bad-ref','synced_block',children=[node('secret')],synced_from={'document_id':'external','block_id':'x'})),
])
def test_ui_detach_plan_malformed_ownership_is_rejected(change):
    original=tree();change(original);before=deepcopy(original)
    with pytest.raises(synced.SyncedShapeUnavailable):plan(original)
    assert original==before


def test_ui_detach_plan_destination_collision_and_untrusted_origin_are_rejected():
    mapped=plan()['block_ids']['hidden']
    with pytest.raises(synced.SyncedShapeUnavailable):plan(destination_ids={'reference',mapped})
    with pytest.raises(ValueError):plan(local_origin='https://app.example/path')


def prepared(store,ws,child,actor='owner',**kw):
    current=synced.read_synced_block(store.iam,actor,child,'synced-instance')
    args=dict(origin=current['origin'],witnesses=current['witnesses'],operation_id='detach-preparation')
    args.update(kw)
    return detach.prepare_synced_detach(store.iam,actor,ws,child,'synced-instance',**args)


def test_ui_detach_authority_preparation_is_read_only_and_revalidatable(environment):
    store,ws=environment;child=setup_pair(store,ws);before=dump(store)
    p=prepared(store,ws,child);assert p.value()['blocks'][0]['content']=='canonical A'
    assert dump(store)==before and prepared(store,ws,child)==p
    with store.iam._reader() as con:
        with pytest.raises(ValueError):detach.revalidate_synced_detach(store.iam,con,p)
        con.execute('BEGIN');detach.revalidate_synced_detach(store.iam,con,p)
        with pytest.raises(ValueError):detach.revalidate_synced_detach(store.iam,con,replace(p,plan_json='{}'))
    assert dump(store)==before


def test_ui_detach_authority_needs_source_read_and_destination_edit_only(environment):
    store,ws=environment;child=setup_pair(store,ws)
    grant_edit(store,'page-e1','view','detach-source-view');grant_edit(store,child,'edit_content','detach-target-edit')
    before=dump(store);assert prepared(store,ws,child,'outsider').value()['blocks'][0]['content']=='canonical A'
    assert dump(store)==before
    grant_edit(store,child,'view','detach-target-view');before=dump(store)
    with pytest.raises(NotFoundOrDenied):prepared(store,ws,child,'outsider')
    assert dump(store)==before


def test_ui_detach_authority_source_lock_allows_copy_but_destination_lock_blocks(environment):
    store,ws=environment;child=setup_pair(store,ws)
    update_page_settings(store.iam,'owner',ws,'page-e1',updates={'locked':True},operation_id='detach-lock-source',**request(store,'page-e1'))
    p=prepared(store,ws,child);assert p.value()['blocks'][0]['type']=='paragraph'
    update_page_settings(store.iam,'owner',ws,child,updates={'locked':True},operation_id='detach-lock-target',**request(store,child))
    before=dump(store)
    with pytest.raises(PermissionError):prepared(store,ws,child)
    assert dump(store)==before


@pytest.mark.parametrize('race',['source','revoke'])
def test_ui_detach_authority_rechecks_after_preparation(environment,monkeypatch,race):
    store,ws=environment;child=setup_pair(store,ws);actor='owner'
    if race=='revoke':
        actor='outsider';grant_edit(store,child,'edit_content','detach-race-target')
    planner=detach._plan;baseline=[]
    def raced(*args,**kw):
        result=planner(*args,**kw)
        if race=='source':text_update(store,ws,'page-e1','synced-original','winner')
        else:revoke(store,ws)
        baseline.append(dump(store));return result
    monkeypatch.setattr(detach,'_plan',raced)
    with pytest.raises(synced.SyncedSourceChanged if race=='source' else NotFoundOrDenied):prepared(store,ws,child,actor)
    assert dump(store)==baseline[0]


def test_ui_detach_authority_original_and_stale_witness_do_not_prepare(environment):
    store,ws=environment;child=setup_pair(store,ws)
    current=synced.read_synced_block(store.iam,'owner',child,'synced-instance')
    text_update(store,ws,'page-e1','synced-original','newer');before=dump(store)
    with pytest.raises(synced.SyncedSourceChanged):prepared(store,ws,child,witnesses=current['witnesses'])
    with pytest.raises(synced.SyncedShapeUnavailable):
        detach.prepare_synced_detach(store.iam,'owner',ws,'page-e1','synced-original',
            origin={'document_id':'page-e1','block_id':'synced-original'},witnesses=current['witnesses'],operation_id='not-unsync-all')
    assert dump(store)==before


def test_ui_detach_authority_same_page_uses_one_witness_and_keeps_original(environment):
    store,ws=environment;insert_synced(store,'page-e1','synced-original',content='same page')
    insert_synced(store,'page-e1','synced-instance',origin={'document_id':'page-e1','block_id':'synced-original'})
    before=dump(store);p=prepared(store,ws,'page-e1')
    assert len(p.sources)==1 and p.value()['blocks'][0]['content']=='same page'
    assert p.value()['blocks'][0]['id']=='synced-instance' and dump(store)==before


def test_ui_detach_authority_prepared_plan_expires_after_destination_edit(environment):
    store,ws=environment;child=setup_pair(store,ws);p=prepared(store,ws,child)
    update_page_settings(store.iam,'owner',ws,child,updates={'full_width':True},operation_id='detach-change-target',**request(store,child))
    before=dump(store)
    with store.iam._reader() as con:
        con.execute('BEGIN')
        with pytest.raises(synced.SyncedSourceChanged):detach.revalidate_synced_detach(store.iam,con,p)
    assert dump(store)==before
