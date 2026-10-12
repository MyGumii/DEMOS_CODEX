"""UI-PARENT-STRUCTURE: DB lifecycle and retargeted hosts keep deletes/order."""
import pytest
from code_assist_v1.tests.notion_collaboration.test_database_link_insert_rebase import (
    link_environment, activity_environment, environment, appearance_environment, body_environment,
    team_environment, file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, setup, body, assert_note, dump, command_fences,
)
from code_assist_v1.tests.notion_collaboration.test_parent_structure_rebase import structural
from code_assist_v1.tests.notion_collaboration.test_page_link_parent_structure_rebase import (
    prepare_visible_notes, mixed_structure, expected_old,
)


@pytest.mark.parametrize('action', ['move', 'copy', 'archive', 'restore'])
def test_ui_database_link_and_external_host_keep_mixed_structural_edits(link_environment, action):
    store, ws = link_environment
    prepare_visible_notes(store)
    plan = setup(store, ws, action)
    mixed_structure(store)
    if action == 'move':
        structural(store, 'delete', 'linked-host-text', document=plan['host'])
        structural(store, 'move', 'retained-linked-view', document=plan['host'], after=None)
    plan['request'].update(command_fences(store, action))
    result = plan['call']()
    current = body(store)
    assert_note(current, plan['pending'])
    expected = expected_old(plan['original'])
    if action in {'move', 'archive'}:
        expected = [block for block in expected if block['id'] != 'owned-db-source-block']
    excluded = {plan['pending']['id']}
    if action == 'copy':
        excluded.add(result['block_id'])
    elif action == 'restore':
        excluded.add('owned-db-source-block')
    assert [block for block in current['blocks'] if block['id'] not in excluded] == expected
    assert current['title'] == plan['original']['title']
    if action == 'move':
        host = body(store, plan['host'])
        assert [block['id'] for block in host['blocks']] == ['retained-linked-view', plan['host_note']['id']]
        assert_note(host, plan['host_note'])
        view = host['blocks'][0]
        old_view = next(block for block in plan['host_before']['blocks'] if block['id'] == view['id'])
        assert view == {**old_view, 'props': {**old_view['props'], 'linked_database': {
            **old_view['props']['linked_database'], 'document_id': 'page-db-destination'}}}
    stable = dump(store)
    plan['call']()
    assert dump(store) == stable
