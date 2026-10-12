"""UI-SYNC-TEMPLATE: created items share source authority and detach independently."""
from copy import deepcopy

import pytest

from code_assist_v1.notion_collaboration import synced_blocks as synced, synced_edit
from code_assist_v1.notion_collaboration import synced_detach_service as detach
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_database_template_default_migration import (
    icon_batch_environment, activity_environment, environment, appearance_environment,
    body_environment, team_environment, file_environment, member_environment,
    native_environment, archive_environment, guest_environment, image_environment,
    placement, custom_emoji, item_layout, migration, _apply, _schema, _surface,
)
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import dump


@pytest.fixture()
def template_environment(icon_batch_environment):
    old, workspace = icon_batch_environment
    for index, target in enumerate((placement, custom_emoji, item_layout, migration)):
        _apply(target, old.db_path, old.user_data_root, f'before-shared-template-{index}.db')
    store = CollaborativeWorkspaceStore(old.db_path, user_data_root=old.user_data_root,
                                        identity_directory=old.identity_directory)
    _schema(store.iam, 'database.template.create', 'shared-template', {
        'name': '공유 템플릿', 'body': [original('shared-original', 'A'),
            {'id': 'ordinary', 'type': 'paragraph', 'content': '항목별 독립 문장', 'props': {}}],
        'default_values': {}}, 'shared-template-seed', actor='owner', target_version=0)
    return store, workspace


def command(store, kind, target, payload, operation, version, actor='owner'):
    document = store.iam.read_document(actor, 'page-e1')
    arguments = dict(database_id='database-e1', command_kind=kind, target_id=target,
        payload=payload, operation_id=operation, expected_target_version=version,
        expected_document_sequence=document['document_sequence'],
        expected_workspace_epoch=document['workspace_epoch'],
        expected_document_epoch=document['document_epoch'])
    return store.iam.execute_surface_command(actor, 'page-e1', **arguments), arguments


def change(store, workspace, instance, text, operation, actor='owner'):
    current = synced.read_synced_block(store.iam, actor, 'page-e1', instance)
    return synced_edit.edit_synced_text(store.iam, actor, workspace, 'page-e1', instance,
        origin=current['origin'], witnesses=current['witnesses'],
        updates=[{'id': 'shared-original', 'content': text, 'rich_text_html': ''}],
        operation_id=operation)


@pytest.mark.integration
@pytest.mark.regression
@pytest.mark.parametrize('mode', ['explicit', 'default', 'apply'])
def test_created_items_share_template_and_single_detach_survives_later_source_edit(template_environment, mode):
    store, workspace = template_environment
    template_before = deepcopy(store.iam.read_database('owner', 'database-e1')['templates'])
    if mode == 'default':
        _surface(store.iam, 'database.template_default.set', 'database-e1',
            {'template_id': 'shared-template'}, 'shared-template-default', actor='owner', target_version=0)
    saved_request = saved_result = None
    for row in ('shared-one', 'shared-two'):
        payload = {'template_id': None} if mode == 'apply' else {} if mode == 'default' else {'template_id': 'shared-template'}
        result, arguments = command(store, 'database.row.create', row, payload, 'create-' + row, 0)
        if mode == 'apply':
            result, arguments = command(store, 'database.row.template.apply', row,
                {'template_id': 'shared-template'}, 'apply-' + row, result['target_version'])
        if row == 'shared-one': saved_request, saved_result = arguments, result
    database = store.iam.read_database('owner', 'database-e1')
    assert database['templates'] == template_before
    rows = {r['id']: r for r in database['rows'] if r['id'].startswith('shared-')}
    refs = {key: value['body']['blocks'][0]['id'] for key, value in rows.items()}
    for value in rows.values():
        blocks = value['body']['blocks']
        assert len(blocks) == 2 and blocks[0]['content'] == ''
        assert not blocks[0]['props'].get('children')
        assert blocks[0]['props']['synced_from'] == {'document_id': 'page-e1', 'block_id': 'shared-original'}
        assert blocks[1]['content'] == '항목별 독립 문장'
    assert refs['shared-one'] != refs['shared-two']
    change(store, workspace, refs['shared-one'], 'B', 'shared-template-edit-b')
    for identity in refs.values():
        current = synced.read_synced_block(store.iam, 'owner', 'page-e1', identity)
        assert current['block']['content'] == 'B'
        assert current['locations']['origin'] == {'kind': 'template', 'database_id': 'database-e1', 'template_id': 'shared-template'}
        assert current['tree']['children'][0]['props']['rich_text_html'] == '<b>자식</b>'
    current = synced.read_synced_block(store.iam, 'owner', 'page-e1', refs['shared-two'])
    value = dict(origin=current['origin'], witnesses=current['witnesses'], operation_id='shared-template-detach')
    detached = detach.detach_synced_block(store.iam, 'owner', workspace, 'page-e1', refs['shared-two'], **value)
    change(store, workspace, refs['shared-one'], 'C', 'shared-template-edit-c')
    current = synced.read_synced_block(store.iam, 'owner', 'page-e1', refs['shared-one'])
    assert current['block']['content'] == 'C'
    database = store.iam.read_database('owner', 'database-e1')
    second = next(r for r in database['rows'] if r['id'] == 'shared-two')
    anchor = synced.index_blocks(second['body']['blocks'])[refs['shared-two']]
    assert anchor['type'] == 'paragraph' and anchor['content'] == 'B'
    assert 'synced_from' not in anchor['props']
    stable = dump(store)
    assert store.iam.execute_surface_command('owner', 'page-e1', **saved_request) == saved_result
    assert detach.detach_synced_block(store.iam, 'owner', workspace, 'page-e1', refs['shared-two'], **value) == {**detached, 'replayed': True}
    assert dump(store) == stable


@pytest.mark.integration
@pytest.mark.regression
def test_creating_shared_item_does_not_grant_template_edit_authority(template_environment):
    store, workspace = template_environment
    command(store, 'database.row.create', 'content-owned-row', {'template_id': 'shared-template'},
            'content-shared-template-create', 0, actor='content-editor')
    row = next(r for r in store.iam.read_database('content-editor', 'database-e1')['rows'] if r['id'] == 'content-owned-row')
    identity = row['body']['blocks'][0]['id']
    current = synced.read_synced_block(store.iam, 'content-editor', 'page-e1', identity)
    assert current['block']['content'] == 'A' and current['capabilities']['edit_text'] is False
    before = dump(store)
    with pytest.raises((PermissionError, NotFoundOrDenied)):
        change(store, workspace, identity, 'forbidden', 'content-template-edit-denied', actor='content-editor')
    assert dump(store) == before
