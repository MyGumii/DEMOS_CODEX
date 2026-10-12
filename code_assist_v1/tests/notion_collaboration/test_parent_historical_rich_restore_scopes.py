"""UI-PARENT-RESTORE-HISTORY: older restores remain valid in DB/row writers."""
import pytest
from contextlib import contextmanager
from code_assist_v1.tests.notion_collaboration import test_parent_rich_restore_scopes as current
from code_assist_v1.tests.notion_collaboration.test_parent_rich_restore_scopes import (
    link_environment, activity_environment, environment, appearance_environment, body_environment,
    team_environment, file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, placement_environment, icon_batch_environment,
)
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import text_update
from code_assist_v1.tests.notion_collaboration.test_page_link_parent_insert_rebase import note
from code_assist_v1.tests.notion_collaboration.test_parent_structure_rebase import create


def historical_pending(store, ws, document='page-e1'):
    # Write later input to a real paragraph, never to a linked DB cache field.
    other = 'history-other-' + document
    initial = current.body(store, document)['blocks']
    note(store, other, document, after=initial[-1]['id'])
    create(store, ws, parent=document, operation='history-scope-bootstrap-' + document)
    original, identity = pending_current(store, ws, document)
    text_update(store, ws, document, other, '복원 뒤에 입력한 별도 본문')
    live = {block['id']: block for block in current.body(store, document)['blocks']}
    expected = {**original, 'blocks': [live.get(block['id'], block) for block in original['blocks']]}
    return expected, identity


pending_current = current.pending_restore


def observe_outside_reader(store, monkeypatch):
    from code_assist_v1.notion_collaboration import search_projection, parent_historical_structure
    reader, replay, certify = store.iam._reader, search_projection.materialize, parent_historical_structure.certified_order
    opened = 0
    @contextmanager
    def observed():
        nonlocal opened
        with reader() as con:
            opened += 1
            try:
                yield con
            finally:
                opened -= 1
    def outside(*args, **kwargs):
        assert opened == 0, 'linked history/rich replay retained a SQLite reader'
        return replay(*args, **kwargs)
    def certified(*args, **kwargs):
        assert opened == 0, 'linked certificate replay retained a SQLite reader'
        return certify(*args, **kwargs)
    monkeypatch.setattr(store.iam, '_reader', observed)
    monkeypatch.setattr(search_projection, 'materialize', outside)
    monkeypatch.setattr(parent_historical_structure, 'certified_order', certified)


@pytest.mark.parametrize('action', ['move', 'copy', 'archive', 'restore'])
def test_ui_historical_rich_restored_database_and_linked_host_keep_latest_body(link_environment, action, monkeypatch):
    if action == 'move':
        observe_outside_reader(link_environment[0], monkeypatch)
    monkeypatch.setattr(current, 'pending_restore', historical_pending)
    current.test_ui_rich_restored_database_parent_and_linked_host_preserve_attributes(link_environment, action)


@pytest.mark.parametrize('direction', ['out', 'back'])
def test_ui_historical_rich_restored_row_parent_keeps_order_and_attributes(placement_environment, direction, monkeypatch):
    monkeypatch.setattr(current, 'pending_restore', historical_pending)
    current.test_ui_rich_restored_row_page_parent_preserves_attributes_and_selected_order(placement_environment, direction)


def test_ui_historical_linked_host_edit_after_preparation_fences_all_domain_writes(link_environment, monkeypatch):
    from code_assist_v1.notion_collaboration import database_move
    from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable
    store, ws = link_environment
    observe_outside_reader(store, monkeypatch)
    monkeypatch.setattr(current, 'pending_restore', historical_pending)
    prepare = database_move._prepare_linked_deltas
    stable = None
    def late(*args, **kwargs):
        nonlocal stable
        result = prepare(*args, **kwargs)
        document = 'rich-restored-linked-host'
        text_update(store, ws, document, 'history-other-' + document, '준비가 끝난 뒤의 입력')
        stable = current.dump(store)
        return result
    monkeypatch.setattr(database_move, '_prepare_linked_deltas', late)
    with pytest.raises(AuthorityUnavailable):
        current.test_ui_rich_restored_database_parent_and_linked_host_preserve_attributes(link_environment, 'move')
    assert stable is not None and current.dump(store) == stable
