"""UI-PARENT-STRUCTURE: row/page placement preserves the exact edited parent."""
import pytest
from code_assist_v1.tests.notion_collaboration.test_database_row_page_parent_insert_rebase import (
    placement_environment, icon_batch_environment, activity_environment, environment,
    appearance_environment, body_environment, team_environment, file_environment, member_environment,
    native_environment, archive_environment, guest_environment, image_environment, setup, body, assert_note, dump,
    _request, _return_request,
)
from code_assist_v1.tests.notion_collaboration.test_page_link_parent_structure_rebase import (
    prepare_visible_notes, mixed_structure, expected_old,
)


@pytest.mark.parametrize('direction', ['out', 'back'])
def test_ui_row_page_links_keep_mixed_pending_structural_edits(placement_environment, direction):
    store, ws = placement_environment
    prepare_visible_notes(store)
    plan = setup(store, ws, direction)
    mixed_structure(store)
    plan['request'].update(_request(store, ws, 'pending-row-page-out') if direction == 'out'
                           else _return_request(store, ws, 'pending-row-page-back'))
    plan['call']()
    current = body(store)
    assert_note(current, plan['pending'])
    expected = expected_old(plan['original'])
    if direction == 'out':
        assert [block for block in current['blocks'][:-2] if block['id'] != plan['pending']['id']] == expected
        assert [block['props']['page_id'] for block in current['blocks'][-2:]] == ['row-e1', 'row-e2']
    else:
        expected = [block for block in expected if block['props'].get('page_id') != 'row-e1']
        assert [block for block in current['blocks'] if block['id'] != plan['pending']['id']] == expected
    assert current['title'] == plan['original']['title']
    stable = dump(store)
    plan['call']()
    assert dump(store) == stable
