"""UI-HTML-DATABASE: current data, view projection, source races and no writes."""
from copy import deepcopy
import io
import zipfile

import pytest
from flask import Flask

from code_assist_v1 import notion_html_database as render
from code_assist_v1 import notion_database_export_assets
from code_assist_v1 import notion_database_export_bundle, routes_notion_search, routes_notion_workspace
from code_assist_v1.notion_collaboration import html_database_export
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, NotFoundOrDenied
from code_assist_v1.tests.test_notion_html_blocks import Tree
from code_assist_v1.tests.notion_collaboration.test_visible_search import _environment, frames
from code_assist_v1.tests.notion_collaboration.test_search_preview import _save, _dump
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema
from code_assist_v1.tests.notion_collaboration.test_file_authority import _begin, _stage_all
from code_assist_v1.tests.notion_collaboration.test_database_row_content import environment as body_environment
from code_assist_v1.tests.notion_collaboration.test_native_team_creation import environment as team_environment
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import environment as file_environment
from code_assist_v1.tests.notion_collaboration.test_title_projection import append
from code_assist_v1.tests.notion_collaboration.test_linked_database_copy import (
    environment, member_environment, native_environment, archive_environment, guest_environment, image_environment,
    setup_pair, set_title, update_views, dump, _grant, revoke,
)


def sample(kind='table', count=3):
    properties = [{'id': 'name', 'name': '이름', 'type': 'title'}, {'id': 'count', 'name': '수량', 'type': 'number'},
                  {'id': 'state', 'name': '상태', 'type': 'status'}, {'id': 'date', 'name': '날짜', 'type': 'date'},
                  {'id': 'secret', 'name': '숨긴 속성', 'type': 'text'}]
    rows = [{'id': f'row-{i}', 'cells': {'name': f'작업 {i}', 'count': i, 'state': '진행' if i % 2 else '완료',
             'date': {'start': f'2026-09-{(i % 28) + 1:02d}', 'end': f'2026-09-{min((i % 28) + 2, 28):02d}'}, 'secret': 'HIDDEN'},
             'computed': {}} for i in range(count)]
    return {'database': {'title': '할 일', 'properties': properties, 'rows': rows}, 'type': kind,
            'config': {'property_visibility': {'secret': False}, 'month': '2026-09'}, 'view_name': '현재 보기'}


@pytest.mark.parametrize('kind', ['table', 'board', 'list', 'gallery', 'calendar', 'timeline'])
def test_ui_html_database_six_views_keep_values_hidden_properties_and_escape(kind):
    model = sample(kind); before = deepcopy(model)
    model['database']['rows'][0]['cells']['name'] = '<img src=x onerror=evil>'
    source = render.render(model)
    assert f'data-view-type="{kind}"' in source
    assert '&lt;img src=x onerror=evil&gt;' in source and '<img' not in source
    assert 'HIDDEN' not in source and '숨긴 속성' not in source
    assert '작업 1' in source and '작업 2' in source
    assert len(model['database']['rows']) == len(before['database']['rows'])


def test_ui_html_database_more_than_100_rows_filters_sort_zero_and_property_order():
    model = sample(count=125)
    assert '작업 124' in render.render(model)
    model['config'].update(filters=[{'property': 'count', 'operator': 'less_than', 'value': '11'}],
                           sorts=[{'property': 'name', 'direction': 'desc'}], property_order=['count', 'name'])
    tree = Tree(render.render(model))
    assert [cell['text'] for cell in tree.find('th')][:2] == ['수량', '이름']
    assert [cell['text'] for cell in tree.find('td')][:2] == ['10', '작업 10']
    assert tree.find('td', text='0') and not tree.find('td', text='작업 11')


def test_ui_html_database_nested_filter_group_matches_editor_logic():
    model = sample(count=4)
    model['config']['filter_group'] = {'logic': 'and', 'children': [
        {'property': 'state', 'operator': 'equals', 'value': '진행'},
        {'logic': 'or', 'children': [
            {'property': 'name', 'operator': 'equals', 'value': '작업 1'},
            {'property': 'date', 'operator': 'on_or_after', 'value': '2026-09-04'},
        ]},
    ]}
    tree = Tree(render.render(model))
    assert tree.find('td', text='작업 1') and tree.find('td', text='작업 3')
    assert not tree.find('td', text='작업 0') and not tree.find('td', text='작업 2')


@pytest.mark.parametrize('kind', ['table', 'board'])
def test_ui_html_database_group_order_follows_saved_view(kind):
    model = sample(kind, count=4)
    for row in model['database']['rows'][1:]:
        row['cells']['state'] = '진행'
    model['config'].update(group_property='state', board_group_property='state',
                           group_sort='count_desc', group_order=['완료', '진행'])
    before = deepcopy(model)
    assert [item['text'] for item in Tree(render.render(model)).find('h4')][:2] == ['진행 3', '완료 1']
    model['config']['group_sort'] = 'manual'
    assert [item['text'] for item in Tree(render.render(model)).find('h4')][:2] == ['완료 1', '진행 3']
    assert before['database'] == model['database']


def test_ui_html_database_item_body_keeps_nested_callout_and_closed_toggle():
    model = sample(count=1)
    model['database']['rows'][0]['body'] = {'blocks': [
        {'id': 'callout', 'type': 'callout', 'content': 'Row body', 'props': {'background_color': 'yellow', 'children': [
            {'id': 'child', 'type': 'paragraph', 'content': 'Nested child', 'props': {}}]}},
        {'id': 'toggle', 'type': 'toggle', 'content': 'Closed', 'props': {'open': False, 'children': [
            {'id': 'inside', 'type': 'paragraph', 'content': 'Hidden child', 'props': {}}]}}]}
    before = deepcopy(model)
    tree = Tree(render.render(model))
    assert tree.find('aside')[0]['text'] == '💡Row bodyNested child'
    assert tree.find('details', **{'class': 'html-db-item'})
    assert model == before


def test_ui_html_database_page_content_excludes_media_without_losing_nested_text(body_environment):
    store, _workspace_id = body_environment
    authority = store.iam
    _schema(authority, 'database.property.create', 'body-title',
        {'name': 'Name', 'type': 'title', 'config': {}},
        'html-db-media-title-property-0001', actor='owner')
    page = authority.read_document('owner', 'page-e1')
    row = next(item for item in authority.read_database('owner', 'database-e1')['rows']
        if item['id'] == 'row-e1')
    authority.execute_surface_command('owner', 'page-e1',
        database_id='database-e1', target_id='row-e1',
        command_kind='database.row.content.set',
        payload={'blocks': [
            {'id': 'body-text', 'type': 'paragraph', 'content': 'KEEP BODY', 'props': {}},
            {'id': 'body-image', 'type': 'image', 'content': 'HIDE IMAGE', 'props': {}},
            {'id': 'body-group', 'type': 'callout', 'content': 'KEEP GROUP', 'props': {
                'children': [
                    {'id': 'body-video', 'type': 'video', 'content': 'HIDE VIDEO', 'props': {}},
                    {'id': 'body-child', 'type': 'paragraph', 'content': 'KEEP CHILD', 'props': {}},
                ]}},
        ]}, expected_target_version=row['version'],
        expected_document_sequence=page['document_sequence'],
        expected_workspace_epoch=page['workspace_epoch'],
        expected_document_epoch=page['document_epoch'],
        operation_id='html-db-exclude-media-body-0001')
    _save(authority, 'page-e1', [{'id': 'db-export-media', 'type': 'table',
        'content': 'Media export', 'props': {'database_id': 'database-e1'}}])
    full = authority.export_database_html('owner', 'page-e1', block_id='db-export-media')
    excluded = authority.export_database_html('owner', 'page-e1',
        block_id='db-export-media', include_media=False)
    assert 'HIDE IMAGE' in full['html'] and 'HIDE VIDEO' in full['html']
    assert 'HIDE IMAGE' not in excluded['html'] and 'HIDE VIDEO' not in excluded['html']
    assert all(text in excluded['html'] for text in ('KEEP BODY', 'KEEP GROUP', 'KEEP CHILD'))
    assert excluded['csv'] == full['csv']


def test_ui_html_database_bundle_contains_authorized_body_and_property_files(body_environment, monkeypatch):
    store, workspace_id = body_environment
    authority = store.iam
    payload = b'\x89PNG\r\n\x1a\n' + b'isolated-export-attachment'
    session = _begin(authority, payload, 'html-asset-upload-0001', name='receipt.png')
    _stage_all(authority, session, payload)
    file_id = authority.finalize_upload('content-editor', session['session_id'],
        operation_id='html-asset-finalize-0001')['file_id']
    _schema(authority, 'database.property.create', 'html-asset-title',
        {'name': 'Name', 'type': 'title', 'config': {}},
        'html-asset-title-property-0001', actor='owner')
    _schema(authority, 'database.property.create', 'html-asset-files',
        {'name': 'Files', 'type': 'files', 'config': {}},
        'html-asset-files-property-0001', actor='owner')
    page = authority.read_document('owner', 'page-e1')
    authority.execute_typed_command('owner', 'page-e1',
        command_kind='database.cell.set', target_id='row-e1:html-asset-files',
        payload={'row_id': 'row-e1', 'property_id': 'html-asset-files',
            'value': [{'file_id': file_id, 'original_name': 'receipt.png'}]},
        expected_target_version=0,
        expected_document_sequence=page['document_sequence'],
        expected_workspace_epoch=page['workspace_epoch'],
        expected_document_epoch=page['document_epoch'],
        operation_id='html-asset-property-value-0001')
    page = authority.read_document('owner', 'page-e1')
    row = next(item for item in authority.read_database('owner', 'database-e1')['rows']
        if item['id'] == 'row-e1')
    authority.execute_surface_command('owner', 'page-e1',
        database_id='database-e1', target_id='row-e1',
        command_kind='database.row.content.set',
        payload={'blocks': [
            {'id': 'asset-image', 'type': 'image', 'content': 'Saved image',
                'props': {'file_id': file_id, 'name': 'receipt.png'}},
            {'id': 'asset-callout', 'type': 'callout', 'content': 'Parent text',
                'props': {'children': [{'id': 'asset-child', 'type': 'file',
                    'content': 'Nested attachment', 'props': {'file_id': file_id}}]}},
        ]}, expected_target_version=row['version'],
        expected_document_sequence=page['document_sequence'],
        expected_workspace_epoch=page['workspace_epoch'],
        expected_document_epoch=page['document_epoch'],
        operation_id='html-asset-body-value-0001')
    _save(authority, 'page-e1', [{'id': 'db-export-assets', 'type': 'table',
        'content': 'Asset export', 'props': {'database_id': 'database-e1'}}])
    result = authority.export_database_html('owner', 'page-e1',
        block_id='db-export-assets', with_asset_bundle=True)
    try:
        with zipfile.ZipFile(result['archive']) as archive:
            names = archive.namelist()
            assets = [name for name in names if name.startswith('assets/')]
            assert len(assets) == 1
            assert archive.read(assets[0]) == payload
            html = archive.read(next(name for name in names if name.endswith('.html'))).decode('utf-8')
            assert f'src="{assets[0]}"' in html
            assert html.count(f'href="{assets[0]}"') >= 2
            assert 'Parent text' in html
    finally:
        result['archive'].close()
    excluded = authority.export_database_html('owner', 'page-e1',
        block_id='db-export-assets', include_media=False, with_asset_bundle=True)
    try:
        with zipfile.ZipFile(excluded['archive']) as archive:
            assert not any(name.startswith('assets/') for name in archive.namelist())
            html = archive.read(next(name for name in archive.namelist() if name.endswith('.html'))).decode('utf-8')
            assert 'Saved image' not in html and 'Nested attachment' not in html
            assert 'Parent text' in html
    finally:
        excluded['archive'].close()
    monkeypatch.setattr(routes_notion_workspace, '_store', lambda: store)
    monkeypatch.setattr(routes_notion_workspace, 'current_user_id', lambda: 'owner')
    monkeypatch.setattr(routes_notion_workspace, '_collaborative_runtime_enabled', lambda: True)
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='isolated-html-export-assets')
    routes_notion_search.register_notion_search_routes(app)
    response = app.test_client().get(
        f'/api/notion/workspaces/{workspace_id}/pages/page-e1/export_html',
        query_string={'block_id': 'db-export-assets', 'bundle': '1'})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert any(name.startswith('assets/') for name in archive.namelist())
    original_archive = notion_database_export_bundle.html_archive_with_assets
    def deleted_during_packaging(*args, **kwargs):
        built = original_archive(*args, **kwargs)
        current = authority.read_document('owner', 'page-e1')
        authority.delete_file('owner', file_id,
            expected_document_sequence=current['document_sequence'],
            expected_workspace_epoch=current['workspace_epoch'],
            expected_document_epoch=current['document_epoch'],
            operation_id='html-asset-delete-during-export-0001')
        return built
    monkeypatch.setattr(notion_database_export_bundle, 'html_archive_with_assets',
        deleted_during_packaging)
    with pytest.raises((AuthorityUnavailable, NotFoundOrDenied)):
        authority.export_database_html('owner', 'page-e1',
            block_id='db-export-assets', with_asset_bundle=True)


def test_ui_html_database_asset_path_cannot_escape_archive():
    path = notion_database_export_assets.zip_path('file_safe', '../../receipt.png')
    assert path.startswith('assets/file_safe-')
    assert '..' not in path.split('/')


def test_ui_html_database_date_property_is_selected_for_its_view():
    model = sample('timeline', count=1)
    model['database']['properties'].append({'id': 'deadline', 'name': '마감', 'type': 'date'})
    model['database']['rows'][0]['cells']['deadline'] = '2026-10-20'
    model['config'].update(calendar_date_property='date', timeline_date_property='deadline')
    assert '2026-10-20 — 2026-10-21' in render.render(model)
    model['type'] = 'calendar'
    tree = Tree(render.render(model))
    assert tree.find('time', datetime='2026-09-01')
    assert '다른 날짜 또는 날짜 없는 항목' not in render.render(model)


def test_ui_html_database_current_canonical_cells_formulas_relations_bundle_and_no_writes(tmp_path):
    a, _, _, catalog, ws = _environment(tmp_path)
    _save(a, 'page-e1', [{'id': 'db-html', 'type': 'table', 'content': 'STALE', 'props': {
        'database_id': 'database-e1', 'rows': [{'title': 'STALE ROW'}]}}])
    for identity, name, kind, config in [('html-title', 'Current title', 'title', {}),
            ('html-formula', 'Computed', 'formula', {'expression': '42 * 2'}),
            ('html-relation', 'Related', 'relation', {'target_database_id': 'database-e1'})]:
        _schema(a, 'database.property.create', identity, {'name': name, 'type': kind, 'config': config},
                'op-' + identity + '-property-0001', actor='owner')
    for identity, value in [('html-title', 'CURRENT VALUE'), ('html-relation', ['row-e1'])]:
        page = a.read_document('owner', 'page-e1')
        a.execute_typed_command('owner', 'page-e1', command_kind='database.cell.set', target_id='row-e1:' + identity,
            payload={'row_id': 'row-e1', 'property_id': identity, 'value': value}, expected_target_version=0,
            expected_document_sequence=page['document_sequence'], expected_workspace_epoch=page['workspace_epoch'],
            expected_document_epoch=page['document_epoch'], operation_id='op-' + identity + '-cell-0001')
    before = _dump(catalog)
    result = a.export_html('owner', 'page-e1')
    assert 'CURRENT VALUE' in result['html'] and '>84<' in result['html']
    assert 'STALE ROW' not in result['html'] and 'STALE' not in result['html']
    assert result['html'].count('CURRENT VALUE') == 2
    assert result['bundle'] == a.export_bundle('owner', 'page-e1')
    assert _dump(catalog) == before


def test_ui_html_database_deleted_yjs_block_does_not_resurrect_database(tmp_path, frames):
    a, transport, _, catalog, ws = _environment(tmp_path)
    append(a, transport, ws, [frames['base'], frames['current']])
    before = _dump(catalog)
    result = a.export_html('owner', 'page-e1')
    assert '<section class="html-database"' not in result['html']
    assert _dump(catalog) == before


def test_ui_html_database_linked_local_current_filter_and_source_revoke(environment):
    store, ws = environment
    child, title = setup_pair(store, ws)
    set_title(store, title, 'Current A value', 'html-linked-title-current')
    update_views(store, ws, child, 'linked-block', 'B missing')
    before = dump(store)
    assert 'Current A value' not in store.iam.export_html('owner', child)['html']
    assert dump(store) == before
    # A new authorized source value is reflected without an instance-body cache.
    set_title(store, title, 'B missing now present', 'html-linked-title-next')
    assert 'B missing now present' in store.iam.export_html('owner', child)['html']
    _grant(store.iam, child, 'outsider', 'html-linked-grant-instance')
    revoke(store, ws)
    before = dump(store)
    denied = store.iam.export_html('outsider', child)['html']
    assert '접근할 수 없는 데이터베이스' in denied and 'B missing now present' not in denied
    assert dump(store) == before


def test_ui_html_database_source_edit_during_render_rebuilds_entire_result(environment, monkeypatch):
    store, ws = environment
    child, title = setup_pair(store, ws)
    set_title(store, title, 'A before', 'html-race-title-before')
    original, calls = render.render, []
    def changed(model):
        result = original(model)
        if not calls:
            calls.append(1); set_title(store, title, 'A after', 'html-race-title-after')
        return result
    monkeypatch.setattr(render, 'render', changed)
    result = store.iam.export_html('owner', child)['html']
    assert 'A after' in result and 'A before' not in result


def test_ui_html_database_source_revocation_during_render_never_returns_prepared_cells(environment, monkeypatch):
    store, ws = environment
    child, title = setup_pair(store, ws)
    set_title(store, title, 'A SECRET', 'html-revoke-title')
    _grant(store.iam, child, 'outsider', 'html-revoke-instance-grant')
    original, calls = render.render, []
    def revoked(model):
        result = original(model)
        if not calls:
            calls.append(1); revoke(store, ws)
        return result
    monkeypatch.setattr(render, 'render', revoked)
    result = store.iam.export_html('outsider', child)['html']
    assert 'A SECRET' not in result and '접근할 수 없는 데이터베이스' in result


def test_ui_html_database_source_revocation_after_read_rebuilds_unavailable_marker(environment, monkeypatch):
    store, ws = environment
    child, title = setup_pair(store, ws)
    set_title(store, title, 'A SECRET', 'html-read-revoke-title')
    _grant(store.iam, child, 'outsider', 'html-read-revoke-instance')
    original = html_database_export.linked_databases.read_linked_database
    calls = []
    def revoked(*args, **kwargs):
        result = original(*args, **kwargs)
        if not calls:
            calls.append(1)
            revoke(store, ws)
        return result
    monkeypatch.setattr(html_database_export.linked_databases, 'read_linked_database', revoked)
    result = store.iam.export_html('outsider', child)['html']
    assert calls == [1]
    assert 'A SECRET' not in result and '접근할 수 없는 데이터베이스' in result
