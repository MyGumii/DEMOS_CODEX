"""UI-PARENT-STRUCTURE: page lifecycle retains mixed pending structural edits."""
import pytest
from code_assist_v1.tests.notion_collaboration.test_page_link_parent_insert_rebase import (
    environment, member_environment, native_environment, archive_environment, guest_environment, image_environment,
    setup, note, body, assert_note, dump,
)
from code_assist_v1.tests.notion_collaboration.test_parent_structure_rebase import structural


def prepare_visible_notes(store):
    note(store, 'visible-delete-victim')
    note(store, 'visible-retained-rich', after='block-a')


def mixed_structure(store):
    structural(store, 'delete', 'visible-delete-victim')
    structural(store, 'move', 'block-a', after='visible-retained-rich')


def expected_old(original):
    blocks = [block for block in original['blocks'] if block['id'] != 'visible-delete-victim']
    block = next(item for item in blocks if item['id'] == 'block-a')
    blocks.remove(block)
    index = next(index for index, item in enumerate(blocks) if item['id'] == 'visible-retained-rich')
    blocks.insert(index + 1, block)
    return blocks


@pytest.mark.parametrize('action', ['move', 'copy', 'archive', 'restore'])
def test_ui_page_link_mixed_structural_edits_and_pending_note_retained(environment, action):
    store, ws = environment
    prepare_visible_notes(store)
    plan = setup(store, ws, action)
    mixed_structure(store)
    result = plan['call']()
    current = body(store)
    assert_note(current, plan['pending'])
    expected = expected_old(plan['original'])
    if action in {'move', 'archive'}:
        expected = [block for block in expected if block['props'].get('page_id') != plan['child']]
    excluded = {plan['pending']['id']}
    if action in {'copy', 'restore'}:
        identity = result['page']['id'] if action == 'copy' else plan['child']
        excluded.update(block['id'] for block in current['blocks'] if block['props'].get('page_id') == identity)
    assert [block for block in current['blocks'] if block['id'] not in excluded] == expected
    assert current['title'] == plan['original']['title']
    stable = dump(store)
    plan['call']()
    assert dump(store) == stable
