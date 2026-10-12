"""UI-COPY-ROW-RESOURCES: independent item bodies, appearance and insertion."""
from copy import deepcopy
from dataclasses import replace
import json
import pytest

from code_assist_v1.notion_collaboration import page_copy_remap as remap
from code_assist_v1.tests.notion_collaboration.test_page_copy_remap import fixture, block
from code_assist_v1.tests.notion_collaboration.test_database_row_appearance import (
    environment,body_environment,team_environment,file_environment,member_environment,native_environment,
    archive_environment,guest_environment,image_environment,execute,body_request,request as appearance_request,dump,
)
from code_assist_v1.tests.notion_collaboration.test_page_copy_source import prepare


def source_with_rows():
    source = fixture()
    page = source.documents[0].descriptor()
    database = page['databases'][0]
    body = [
        block('paragraph', 'toggle', content='접힌 내용 😀', open=False,
              children=[block('inner', content='본문', rich_text_html='<b>본문</b>')]),
        block('image', 'image', file_id='file-source', url='/api/notion/collaboration/files/file-source'),
        block('tabs1', 'tabs', tabs=[{'id':'a','name':'one'}], active_tab_id='a'),
        block('panel1', content='first', tab_group='tabs1', tab_id='a'),
        block('tabs2', 'tabs', tabs=[{'id':'a','name':'two'}], active_tab_id='a'),
        block('panel2', content='second', tab_group='tabs2', tab_id='a'),
    ]
    row = database['rows'][0]
    row['body'] = {'blocks':body}
    row['appearance'] = {'icon':'nf:file-source', 'cover':'/api/notion/collaboration/files/file-source', 'cover_position':0.2}
    other = deepcopy(row)
    other['id'] = 'row-a2';other['position'] = 1
    database['rows'].append(other)
    source = replace(source, documents=(replace(source.documents[0], descriptor_json=json.dumps(page)), source.documents[1]))
    return source


def test_ui_copy_row_resources_bodies_are_independent_from_page_template_and_other_rows():
    source = source_with_rows();before = deepcopy(source)
    result = remap.remap_page_copy(source, 'row-resource-copy')
    page = result.documents[0].descriptor();database = page['databases'][0]
    a,b = database['rows']
    ids = [page['blocks'][0]['id'], database['templates'][0]['body'][0]['id'],
           a['body']['blocks'][0]['id'], b['body']['blocks'][0]['id']]
    assert len(set(ids)) == 4 and 'paragraph' not in ids
    assert a['body']['blocks'][0]['content'] == '접힌 내용 😀'
    assert a['body']['blocks'][0]['props']['open'] is False
    assert a['body']['blocks'][0]['props']['children'][0]['props']['rich_text_html'] == '<b>본문</b>'
    assert a['body']['blocks'][0]['props']['children'][0]['id'] != b['body']['blocks'][0]['props']['children'][0]['id']
    assert result == remap.remap_page_copy(source, 'row-resource-copy') and source == before


def test_ui_copy_row_resources_controller_local_tabs_do_not_merge():
    result = remap.remap_page_copy(source_with_rows(), 'row-resource-tabs').documents[0].descriptor()
    for row in result['databases'][0]['rows']:
        left,one,right,two = row['body']['blocks'][2:]
        assert left['props']['active_tab_id'] != right['props']['active_tab_id']
        assert one['props']['tab_group'] == left['id'] and two['props']['tab_group'] == right['id']
        assert one['props']['tab_id'] == left['props']['active_tab_id'] == left['props']['tabs'][0]['id']
        assert two['props']['tab_id'] == right['props']['active_tab_id'] == right['props']['tabs'][0]['id']


def test_ui_copy_row_resources_image_icon_and_cover_use_new_file_record():
    result = remap.remap_page_copy(source_with_rows(), 'row-resource-images')
    file = json.loads(result.documents[0].files_json)[0]
    assert file['source_file_id'] == 'file-source' and file['id'] != 'file-source'
    for row in result.documents[0].descriptor()['databases'][0]['rows']:
        image = row['body']['blocks'][1]['props']
        assert image['file_id'] == file['id'] and image['url'].endswith('/'+file['id'])
        assert row['appearance'] == {'icon':'nf:'+file['id'], 'cover':image['url'], 'cover_position':0.2}


def test_ui_copy_row_resources_row_links_and_local_anchors_follow_exact_body_scope():
    source = source_with_rows();doc = source.documents[0].descriptor()
    markup = ('<a href="#block-inner">local</a> '
        '<a href="/page/page-a?item_db=db-a&amp;item_row=row-a2&amp;item_view=full&amp;label=keep%20me#block-inner">other row</a> '
        '<a href="https://app.example/page/page-a?item_db=db-a&amp;item_row=row-a#block-inner">absolute</a> '
        '<a href="/page/page-a?item_db=outside&amp;item_row=outside-row#block-inner">outside</a> '
        '<a href="https://remote.example/page/page-a?item_db=db-a&amp;item_row=row-a#block-inner">remote</a>')
    doc['databases'][0]['rows'][0]['body']['blocks'][0]['props']['rich_text_html'] = markup
    source = replace(source,documents=(replace(source.documents[0],descriptor_json=json.dumps(doc)),source.documents[1]))
    result = remap.remap_page_copy(source,'row-resource-links',local_origin='https://app.example')
    database = result.documents[0].descriptor()['databases'][0];a,b = database['rows']
    html = a['body']['blocks'][0]['props']['rich_text_html']
    aid = a['body']['blocks'][0]['props']['children'][0]['id'];bid = b['body']['blocks'][0]['props']['children'][0]['id']
    assert f'href="#block-{aid}"' in html
    assert f'/page/{result.documents[0].document_id}?item_db={database["id"]}&amp;item_row={b["id"]}&amp;item_view=full&amp;label=keep%20me#block-{bid}' in html
    assert f'https://app.example/page/{result.documents[0].document_id}?item_db={database["id"]}&amp;item_row={a["id"]}#block-{aid}' in html
    assert '/page/page-a?item_db=outside&amp;item_row=outside-row#block-inner' in html
    assert 'https://remote.example/page/page-a?item_db=db-a&amp;item_row=row-a#block-inner' in html


@pytest.mark.parametrize('body', [None, {'blocks':'invalid'}, {'blocks':[], 'unrecognized':True}])
def test_ui_copy_row_resources_malformed_body_cannot_pass_through(body):
    source = source_with_rows();doc = source.documents[0].descriptor()
    doc['databases'][0]['rows'][0]['body'] = body
    source = replace(source,documents=(replace(source.documents[0],descriptor_json=json.dumps(doc)),source.documents[1]))
    with pytest.raises(remap.CopyShapeUnavailable):remap.remap_page_copy(source,'row-resource-malformed')


def test_ui_copy_row_resources_real_canonical_insert_matches_prepared_body_and_appearance(environment,monkeypatch):
    from code_assist_v1.notion_collaboration import page_copy
    store,ws = environment;a = store.iam
    execute(a,body_request(a));execute(a,appearance_request(a))
    prepared = prepare(store,ws)
    planned = remap.remap_page_copy(prepared,'row-resource-real-insert')
    descriptor = page_copy._contents(planned)[planned.documents[0].document_id]
    database = descriptor['databases'][0]
    assert database['rows'][0]['body']['blocks'] and database['rows'][0]['appearance']['icon']
    before = dump(store)
    class RollbackProbe(Exception):pass
    with pytest.raises(RollbackProbe):
        with a._transaction() as con:
            # Test the target-page helper inside a disposable transaction; the
            # full copy/unsync writer is not enabled on this schema by this test.
            page_copy._insert_database(con,'page-e1',database,0)
            actual = a._editing_snapshot(con,a._document(con,'page-e1'))
            copied = next(value for value in actual['databases'] if value['id'] == database['id'])
            assert copied == database
            assert list(con.execute('PRAGMA foreign_key_check')) == []
            raise RollbackProbe()
    assert dump(store) == before
    with a._reader() as con:
        with pytest.raises(ValueError,match='transaction'):
            page_copy._insert_database(con,'page-e1',database,0)
    for module in (page_copy.database_row_content,page_copy.database_row_appearance):
        with monkeypatch.context() as patch:
            patch.setattr(module,'installed',lambda con:False)
            with a._transaction() as con:
                before_changes = con.total_changes
                with pytest.raises(page_copy.AuthorityUnavailable):page_copy._insert_database(con,'page-e1',database,0)
                assert con.total_changes == before_changes
        assert dump(store) == before


def test_ui_copy_row_resources_copy_of_copy_keeps_files_tabs_and_rich_body():
    from code_assist_v1.notion_collaboration.page_copy_source import CopySourceDocument
    source = source_with_rows();first = remap.remap_page_copy(source,'row-resource-first')
    source2 = replace(source,source_page_id=first.documents[0].document_id,
        documents=tuple(CopySourceDocument(p.document_id,p.parent_id,p.position,(1,),p.descriptor_json,p.files_json) for p in first.documents))
    second = remap.remap_page_copy(source2,'row-resource-second')
    old = first.documents[0].descriptor()['databases'][0]['rows'][0]
    new = second.documents[0].descriptor()['databases'][0]['rows'][0]
    assert old['id'] != new['id'] and old['body']['blocks'][0]['id'] != new['body']['blocks'][0]['id']
    assert old['body']['blocks'][0]['content'] == new['body']['blocks'][0]['content']
    file = json.loads(second.documents[0].files_json)[0]
    assert file['source_file_id'] == old['appearance']['icon'][3:]
    assert new['appearance']['icon'] == 'nf:'+file['id']
    assert new['body']['blocks'][2]['props']['active_tab_id'] == new['body']['blocks'][3]['props']['tab_id']


@pytest.mark.parametrize('query', ['item_db=db-a', 'item_row=row-a', 'item_db=db-a&item_row=row-a&item_row=row-a2',
    'item_db=db-b&item_row=row-b'])
def test_ui_copy_row_resources_ambiguous_or_wrong_page_item_link_stays_exact(query):
    source = source_with_rows();mapper = remap._Remapper(source,'row-resource-url-scope','https://app.example')
    value = '/page/page-a?'+query+'#block-paragraph'
    assert mapper.url(value,'page-a') == value


def test_ui_copy_row_resources_missing_icon_or_duplicate_local_tab_is_rejected():
    source = source_with_rows()
    for change in ('icon','tab'):
        doc = source.documents[0].descriptor();row = doc['databases'][0]['rows'][0]
        if change == 'icon':row['appearance']['icon'] = 'nf:missing'
        else:row['body']['blocks'][2]['props']['tabs'].append({'id':'a','name':'duplicate'})
        bad = replace(source,documents=(replace(source.documents[0],descriptor_json=json.dumps(doc)),source.documents[1]))
        with pytest.raises(remap.CopyShapeUnavailable):remap.remap_page_copy(bad,'row-resource-invalid')


def test_ui_copy_row_resources_file_query_is_not_interpreted_as_item_navigation():
    mapper = remap._Remapper(source_with_rows(),'row-resource-file-query','https://app.example')
    file = mapper.ref('file','','file-source')
    for prefix in ('','https://app.example'):
        old = prefix+'/api/notion/collaboration/files/file-source?item_db=literal&label=keep%20me#block-paragraph'
        assert mapper.url(old,'page-a') == prefix+'/api/notion/collaboration/files/'+file+'?item_db=literal&label=keep%20me#block-paragraph'
