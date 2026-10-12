"""UI-PARENT-STRUCTURE: immutable typed deletes/moves survive link writers."""
from contextlib import contextmanager
import multiprocessing

import pytest

from code_assist_v1.notion_collaboration import page_move, search_projection
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, InjectedFailure
from code_assist_v1.notion_collaboration.editing_authority import DocumentSequenceConflict
from code_assist_v1.tests.notion_collaboration.test_native_page_creation import (
    environment, archive_environment, guest_environment, image_environment, create, current_update, dump, registry,
)
from code_assist_v1.tests.notion_collaboration.test_page_copy_source import request as fences
from code_assist_v1.tests.notion_collaboration.test_page_link_parent_insert_rebase import note, body
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import text_update


def structural(store, kind, identity, *, document='page-e1', after=None, operation=None):
    return store.iam.execute_typed_command('owner', document, command_kind='block.' + kind, target_id=identity,
        payload={} if kind == 'delete' else {'after_block_id': after},
        operation_id=operation or ('structure-' + kind + '-' + identity), **fences(store, document))


def setup(store, ws, action):
    current_update(store, ws)
    first = body(store)['blocks'][0]['id']
    note(store, 'retained-structure-note', after=first)
    create(store, ws, operation='structure-body-bootstrap')
    original = body(store)
    expected = original['blocks'][:]
    if action == 'delete':
        structural(store, 'delete', first)
        expected = [block for block in expected if block['id'] != first]
    elif action == 'move':
        structural(store, 'move', first, after='retained-structure-note')
        moved = expected.pop(0)
        expected.insert(1, moved)
    else:
        raise AssertionError(action)
    assert body(store) == original, 'fixture must expose the actual pending structural divergence'
    return original, expected


@pytest.mark.parametrize('action', ['delete', 'move'])
def test_ui_parent_structure_native_child_keeps_delete_order_rich_body_and_retry(environment, action, monkeypatch):
    store, ws = environment
    original, expected = setup(store, ws, action)
    opened = 0
    reader, replay = store.iam._reader, search_projection.materialize
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
        assert opened == 0, 'structural replay must not retain a SQLite reader'
        return replay(*args, **kwargs)
    monkeypatch.setattr(store.iam, '_reader', observed)
    monkeypatch.setattr(search_projection, 'materialize', outside)
    version = store.get_workspace('owner', ws)['version']
    result = create(store, ws, operation='structure-final-child', base_version=version)
    current = body(store)
    assert current['blocks'][:-1] == expected
    assert current['blocks'][-1]['props']['page_id'] == result['page']['id']
    assert current['title'] == original['title']
    stable = dump(store)
    assert create(store, ws, operation='structure-final-child', base_version=version)['replayed'] is True
    assert dump(store) == stable


def test_ui_parent_structure_interleaved_insert_move_delete_preserves_rich_instances(environment):
    store, ws = environment
    original, expected = setup(store, ws, 'move')
    first = original['blocks'][0]['id']
    note(store, 'transient-structure-note', after=first)
    structural(store, 'move', 'transient-structure-note', after=None)
    structural(store, 'delete', 'transient-structure-note')
    pending = note(store, 'new-final-structure-note', after=first)
    create(store, ws, operation='interleaved-structure-child')
    expected.insert(2, {key: value for key, value in pending.items() if key != 'position'})
    current = body(store)
    assert current['blocks'][:-1] == expected and current['title'] == original['title']
    assert all(block['id'] != 'transient-structure-note' for block in current['blocks'])


@pytest.mark.parametrize('action', ['delete', 'move'])
def test_ui_parent_structure_older_divergence_without_certificate_refuses_without_writes(environment, action, monkeypatch):
    from code_assist_v1.notion_collaboration import parent_historical_structure
    monkeypatch.setattr(parent_historical_structure, 'capture', lambda *args: None)
    store, ws = environment
    setup(store, ws, action)
    text_update(store, ws, 'page-e1', 'retained-structure-note', '더 최근의 문단 입력')
    before = dump(store)
    with pytest.raises(AuthorityUnavailable):
        create(store, ws, operation='older-structure-child')
    assert dump(store) == before


@pytest.mark.parametrize('tamper', ['payload', 'flag'])
def test_ui_parent_structure_independent_replay_refuses_tampered_delta(environment, monkeypatch, tamper):
    store, ws = environment
    setup(store, ws, 'move')
    prepare = page_move._prepare_deltas
    def bad(*args):
        value = prepare(*args)
        if tamper == 'payload':
            value['page-e1']['payload'] = b'\0\0'
        else:
            value['page-e1']['reconciled_structure_changed'] = False
        return value
    monkeypatch.setattr(page_move, '_prepare_deltas', bad)
    before = dump(store)
    with pytest.raises(AuthorityUnavailable):
        create(store, ws, operation='tampered-structure-child')
    assert dump(store) == before


@pytest.mark.parametrize('late_kind', ['delete', 'move'])
def test_ui_parent_structure_late_command_fences_new_child(environment, monkeypatch, late_kind):
    store, ws = environment
    original, _expected = setup(store, ws, 'move')
    prepare = page_move._prepare_deltas
    stable = None
    def late(*args):
        nonlocal stable
        value = prepare(*args)
        structural(store, late_kind, original['blocks'][0]['id'], operation='late-structural-command')
        stable = dump(store)
        return value
    monkeypatch.setattr(page_move, '_prepare_deltas', late)
    with pytest.raises(DocumentSequenceConflict):
        create(store, ws, operation='stale-structure-child')
    assert stable is not None and dump(store) == stable


@pytest.mark.parametrize('action', ['delete', 'move'])
def test_ui_parent_structure_and_new_link_rollback_together(environment, action):
    store, ws = environment
    _original, expected = setup(store, ws, action)
    before = dump(store)
    with pytest.raises(InjectedFailure):
        create(store, ws, operation='rollback-structure-child', fault='after_outbox')
    assert dump(store) == before
    create(store, ws, operation='rollback-structure-child')
    assert body(store)['blocks'][:-1] == expected


def test_ui_parent_structure_restored_visible_identity_keeps_existing_rich_body(environment):
    store, ws = environment
    original, _expected = setup(store, ws, 'delete')
    first = original['blocks'][0]
    store.iam.execute_typed_command('owner', 'page-e1', command_kind='block.insert', target_id=first['id'],
        payload={'restore_deleted': True, 'type': first['type'], 'content': first['content'],
                 'props': first['props'], 'after_block_id': None},
        operation_id='restore-structural-identity', **fences(store))
    create(store, ws, operation='restored-structure-child')
    assert body(store)['blocks'][:-1] == original['blocks']


def test_ui_parent_structure_two_processes_keep_order_and_commit_one_child(environment):
    from code_assist_v1.tests.notion_collaboration.test_native_page_parent_insert_rebase import _create_rebased_process
    store, ws = environment
    _original, expected = setup(store, ws, 'move')
    version = store.get_workspace('owner', ws)['version']
    count = len(registry(store))
    context = multiprocessing.get_context('spawn')
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    workers = [context.Process(target=_create_rebased_process,
        args=(str(store.db_path), str(store.user_data_root), ws, version, ready, start, results)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        assert [ready.get(timeout=120) for _ in workers] == [True, True]
        start.set()
        values = [results.get(timeout=120) for _ in workers]
        assert all('error' not in value for value in values), values
        assert sorted(value['replayed'] for value in values) == [False, True]
        assert values[0]['page'] == values[1]['page']
        assert len(registry(store)) == count + 1
        assert body(store)['blocks'][:-1] == expected
    finally:
        start.set()
        for worker in workers:
            if worker.pid:
                worker.join(timeout=10)
                if worker.is_alive():
                    worker.terminate(); worker.join(timeout=10)
