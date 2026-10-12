"""UI-PARENT-RESTORE-HISTORY: retain exact restores across later text frames."""
import pytest

from code_assist_v1.notion_collaboration import page_copy_source, search_projection
from code_assist_v1.notion_collaboration.title_projection import _frames, _generation
from code_assist_v1.tests.notion_collaboration.test_parent_rich_restore_rebase import (
    environment, member_environment, native_environment, archive_environment, guest_environment,
    image_environment, restored, create, body, dump, request, edit_yjs, current_update, structural,
)
from code_assist_v1.tests.notion_collaboration.test_page_link_parent_insert_rebase import note
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import text_update


def historical(store, ws, *, replacement=False):
    original, expected, identity = restored(store, ws, replacement=replacement)
    text_update(store, ws, 'page-e1', 'restore-retained-note', '복원 뒤에 입력한 최신 문단')
    current = body(store)
    by_id = {block['id']: block for block in current['blocks']}
    expected = [by_id.get(block['id'], block) for block in expected]
    return original, expected, identity


@pytest.mark.parametrize('replacement', [False, True])
@pytest.mark.parametrize('mode', ['native', 'source'])
def test_ui_historical_rich_restore_keeps_archive_or_replacement_and_later_unrelated_text(environment, replacement, mode):
    store, ws = environment
    original, expected, identity = historical(store, ws, replacement=replacement)
    before = dump(store)
    if mode == 'native':
        create(store, ws, operation='historical-rich-restored-child')
        assert body(store)['blocks'][:-1] == expected and body(store)['title'] == original['title']
        with store.iam._reader() as con:
            frames = _frames(con, 'page-e1', _generation(con, 'page-e1'))
        delta = search_projection.materialize([frames], rich=True)[0]['rich_text']['blocks'][identity]
        assert delta == ([{'insert': expected[0]['content']}] if replacement else
            [{'insert': expected[0]['content'], 'attributes': {'bold': True, 'italic': False}}])
    else:
        result = page_copy_source.prepare_page_copy(store.iam, 'owner', ws, 'page-e1', **request(store))
        assert result.documents[0].descriptor()['blocks'] == expected
        assert result.documents[0].descriptor()['title'] == original['title'] and dump(store) == before


@pytest.mark.parametrize('replacement', [False, True])
def test_ui_historical_rich_restore_already_applied_new_map_keeps_later_target_text_and_formatting(environment, replacement):
    store, ws = environment
    _original, _expected, identity = restored(store, ws, replacement=replacement)
    create(store, ws, operation='historical-rich-first-applied')
    text_update(store, ws, 'page-e1', identity, '복원한 문단에 새로 입력한 글')
    edit_yjs(store, ws, identity, 'format2')
    current = body(store)
    with store.iam._reader() as con:
        frames = _frames(con, 'page-e1', _generation(con, 'page-e1'))
    rich_before = search_projection.materialize([frames], rich=True)[0]['rich_text']['blocks'][identity]
    create(store, ws, operation='historical-rich-later-applied')
    assert body(store)['blocks'][:-1] == current['blocks'] and body(store)['title'] == current['title']
    with store.iam._reader() as con:
        frames = _frames(con, 'page-e1', _generation(con, 'page-e1'))
    assert search_projection.materialize([frames], rich=True)[0]['rich_text']['blocks'][identity] == rich_before


def older_visible(store, ws, replacement=False):
    current_update(store, ws)
    identity = body(store)['blocks'][0]['id']
    note(store, 'old-visible-retained-note', after=identity)
    create(store, ws, operation='old-visible-bootstrap')
    edit_yjs(store, ws, identity, 'format2')
    original = body(store)
    block = original['blocks'][0]
    structural(store, 'delete', identity, operation='old-visible-delete')
    values = {key: block[key] for key in ('type', 'content', 'props')}
    if replacement:
        values = {'type': 'heading2', 'content': '명시적 교체', 'props': {'color': 'red'}}
    store.iam.execute_typed_command('owner', 'page-e1', command_kind='block.insert', target_id=identity,
        payload={**values, 'after_block_id': None, 'restore_deleted': True},
        operation_id='old-visible-restore', **request(store))
    text_update(store, ws, 'page-e1', 'old-visible-retained-note', '나중의 다른 문단')
    if not replacement:
        text_update(store, ws, 'page-e1', identity, '복원 후 최신 입력')
        edit_yjs(store, ws, identity, 'format2')
    return body(store), identity


@pytest.mark.parametrize('mode', ['native', 'source'])
def test_ui_historical_original_restore_keeps_newer_text_on_an_older_visible_map(environment, mode):
    store, ws = environment
    original, _identity = older_visible(store, ws)
    stable = dump(store)
    if mode == 'native':
        create(store, ws, operation='old-visible-latest-child')
        assert body(store)['blocks'][:-1] == original['blocks']
    else:
        prepared = page_copy_source.prepare_page_copy(store.iam, 'owner', ws, 'page-e1', **request(store))
        assert prepared.documents[0].descriptor()['blocks'] == original['blocks'] and dump(store) == stable


@pytest.mark.parametrize('mode', ['native', 'source'])
def test_ui_historical_replacement_on_an_unexplained_old_map_refuses_without_writes(environment, mode):
    from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable
    store, ws = environment
    older_visible(store, ws, replacement=True)
    stable = dump(store)
    with pytest.raises(AuthorityUnavailable if mode == 'native' else page_copy_source.CopySourceChanged):
        if mode == 'native':
            create(store, ws, operation='unexplained-old-replacement')
        else:
            page_copy_source.prepare_page_copy(store.iam, 'owner', ws, 'page-e1', **request(store))
    assert dump(store) == stable


@pytest.mark.parametrize('check', [
    'second_cycle_uses_latest_deletion_and_keeps_first_tombstone',
    'rollback_preserves_pending_restoration',
    'replays_and_validates_attributes_outside_reader',
    'late_change_fences_prepared_recovery',
    'budget_exhaustion_leaves_pending_state_unchanged',
    'two_processes_commit_one_child_and_replay_exactly',
])
def test_ui_historical_rich_restore_retains_existing_safety_contracts(environment, check, monkeypatch):
    from code_assist_v1.tests.notion_collaboration import test_parent_rich_restore_rebase as current
    monkeypatch.setattr(current, 'restored', historical)
    call = getattr(current, 'test_ui_parent_rich_restore_' + check)
    if check in {'second_cycle_uses_latest_deletion_and_keeps_first_tombstone',
            'rollback_preserves_pending_restoration', 'two_processes_commit_one_child_and_replay_exactly'}:
        call(environment)
    else:
        call(environment, monkeypatch)


@pytest.mark.parametrize('target', ['restored', 'other'])
def test_ui_historical_rich_restore_attribute_only_tamper_is_rejected(environment, target, monkeypatch):
    from code_assist_v1.tests.notion_collaboration import test_parent_rich_restore_rebase as current
    monkeypatch.setattr(current, 'restored', historical)
    current.test_ui_parent_rich_restore_attribute_only_tamper_is_independently_rejected(environment, target, monkeypatch)
