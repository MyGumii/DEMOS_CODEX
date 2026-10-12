"""UI-PARENT-RESTORE: restored rich parents during DB/linked/row operations."""
import pytest
from code_assist_v1.notion_collaboration import database_trash, database_row_page_move, search_projection
from code_assist_v1.notion_collaboration.title_projection import _frames, _generation
from code_assist_v1.tests.notion_collaboration.test_database_link_insert_rebase import (
    link_environment, activity_environment, environment, appearance_environment, body_environment,
    team_environment, file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, owned_database_block, copy, command_fences,
    _destination, _move,
)
from code_assist_v1.tests.notion_collaboration.test_database_row_page_parent_insert_rebase import (
    placement_environment, icon_batch_environment, _request, _return_request,
)
from code_assist_v1.tests.notion_collaboration.test_page_copy_source import current_update, request
from code_assist_v1.tests.notion_collaboration.test_page_link_parent_insert_rebase import body, dump
from code_assist_v1.tests.notion_collaboration.test_parent_structure_rebase import structural
from code_assist_v1.tests.notion_collaboration.test_parent_rich_restore_rebase import edit_yjs
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import text_update


def pending_restore(store, ws, document='page-e1'):
    identity = next(block['id'] for block in body(store, document)['blocks'] if block['type'] == 'paragraph')
    edit_yjs(store, ws, identity, 'format', document=document)
    original = body(store, document)
    block = next(block for block in original['blocks'] if block['id'] == identity)
    ids = [item['id'] for item in original['blocks']]
    at = ids.index(identity)
    structural(store, 'delete', identity, document=document, operation='scope-rich-delete-' + document)
    edit_yjs(store, ws, identity, 'delete', document=document)
    store.iam.execute_typed_command('owner', document, command_kind='block.insert', target_id=identity,
        payload={**{key: block[key] for key in ('type', 'content', 'props')}, 'restore_deleted': True,
                 'after_block_id': ids[at - 1] if at else None},
        operation_id='scope-rich-restore-' + document, **request(store, document))
    return original, identity


def assert_rich(store, document, identity):
    with store.iam._reader() as con:
        frames = _frames(con, document, _generation(con, document))
    rich = search_projection.materialize([frames], rich=True)[0]
    assert rich['rich_text']['blocks'][identity][0]['attributes']['bold'] is True


@pytest.mark.parametrize('action', ['move', 'copy', 'archive', 'restore'])
def test_ui_rich_restored_database_parent_and_linked_host_preserve_attributes(link_environment, action):
    store, ws = link_environment
    owned_database_block(store); current_update(store, ws)
    if action == 'restore':
        database_trash.set_owned_database_trashed(store.iam, 'owner', ws, 'page-e1', 'database-e1',
            archived=True, operation_id='scope-rich-db-prepare-restore', **command_fences(store, 'archive'))
    original, identity = pending_restore(store, ws)
    if action == 'move':
        _destination(store, ws)
        page = store.iam.read_document('owner', 'page-e1')
        host = 'rich-restored-linked-host'
        props = {'linked_database': {'document_id': 'page-e1', 'database_id': 'database-e1'},
            'views': [{'id': 'local-view', 'name': 'Local view', 'type': 'table', 'config': {'filters': [], 'sorts': []}}],
            'active_view_id': 'local-view', 'indent': 1}
        store.iam.create_document('owner', ws, host, title='복원한 연결 보기',
            blocks=[{'id': 'rich-host-text', 'type': 'paragraph', 'content': '원문', 'props': {}},
                    {'id': 'rich-linked-view', 'type': 'table', 'content': '', 'props': props}],
            expected_workspace_epoch=page['workspace_epoch'], operation_id='scope-rich-host-create')
        text_update(store, ws, host, 'rich-host-text', '연결 보기 최신 문단')
        host_before, host_identity = pending_restore(store, ws, host)
    fences = command_fences(store, action)
    if action == 'move':
        call = lambda: _move(store, ws, operation_id='scope-rich-db-move', fences=fences)
    elif action == 'copy':
        call = lambda: copy(store, ws, 'scope-rich-db-copy', fences=fences)
    else:
        call = lambda: database_trash.set_owned_database_trashed(store.iam, 'owner', ws, 'page-e1', 'database-e1',
            archived=action == 'archive', operation_id='scope-rich-db-' + action, **fences)
    call()
    current = body(store)
    assert [block for block in current['blocks'] if block['type'] == 'paragraph'] == [block for block in original['blocks'] if block['type'] == 'paragraph']
    assert current['title'] == original['title']; assert_rich(store, 'page-e1', identity)
    if action == 'move':
        after = body(store, host)
        assert after['blocks'][0] == host_before['blocks'][0] and after['title'] == host_before['title']
        assert after['blocks'][1]['props'] == {**props, 'linked_database': {'document_id': 'page-db-destination', 'database_id': 'database-e1'}}
        assert_rich(store, host, host_identity)
    stable = dump(store); call(); assert dump(store) == stable


@pytest.mark.parametrize('direction', ['out', 'back'])
def test_ui_rich_restored_row_page_parent_preserves_attributes_and_selected_order(placement_environment, direction):
    store, ws = placement_environment
    current_update(store, ws)
    if direction == 'back':
        database_row_page_move.move_owned_rows_to_page(store.iam, **_request(store, ws, 'scope-rich-row-prepare'))
    original, identity = pending_restore(store, ws)
    fences = (_request(store, ws, 'scope-rich-row-out') if direction == 'out' else _return_request(store, ws, 'scope-rich-row-back'))
    method = database_row_page_move.move_owned_rows_to_page if direction == 'out' else database_row_page_move.return_page_to_owned_database
    method(store.iam, **fences)
    current = body(store)
    if direction == 'out':
        assert current['blocks'][:-2] == original['blocks']
        assert [block['props']['page_id'] for block in current['blocks'][-2:]] == ['row-e1', 'row-e2']
    else:
        assert current['blocks'] == [block for block in original['blocks'] if block['props'].get('page_id') != 'row-e1']
    assert current['title'] == original['title']; assert_rich(store, 'page-e1', identity)
    stable = dump(store); method(store.iam, **fences); assert dump(store) == stable
