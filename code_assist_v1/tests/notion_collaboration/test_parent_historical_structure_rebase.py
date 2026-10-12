"""UI-PARENT-HISTORY: certified older deletes/moves keep current rich text."""
from contextlib import contextmanager
import multiprocessing

import pytest

from code_assist_v1.notion_collaboration import page_copy_source, page_move, search_projection
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, InjectedFailure
from code_assist_v1.notion_collaboration.editing_authority import DocumentSequenceConflict
from code_assist_v1.tests.notion_collaboration.test_parent_structure_rebase import (
    environment, archive_environment, guest_environment, image_environment, setup, structural, body, dump, create,
)
from code_assist_v1.tests.notion_collaboration.test_page_copy_source import request as fences
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import text_update
from code_assist_v1.tests.notion_collaboration.test_page_link_parent_insert_rebase import note


def older(store, ws, action):
    original, expected = setup(store, ws, action)
    text_update(store, ws, 'page-e1', 'retained-structure-note', '더 최근의 서식 문단')
    projected = body(store)
    by_id = {block['id']: block for block in projected['blocks']}
    expected = [by_id[block['id']] for block in expected]
    return projected, expected


@pytest.mark.parametrize('action', ['delete', 'move'])
def test_ui_historical_child_recovers_structure_and_keeps_later_text_outside_reader(environment, action, monkeypatch):
    from code_assist_v1.notion_collaboration import parent_historical_structure
    store, ws = environment
    original, expected = older(store, ws, action)
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
        assert opened == 0
        return replay(*args, **kwargs)
    monkeypatch.setattr(store.iam, '_reader', observed)
    monkeypatch.setattr(search_projection, 'materialize', outside)
    certify = parent_historical_structure.certified_order
    def certify_outside(*args, **kwargs):
        assert opened == 0, 'anchor history replay must not retain a SQLite reader'
        return certify(*args, **kwargs)
    monkeypatch.setattr(parent_historical_structure, 'certified_order', certify_outside)
    version = store.get_workspace('owner', ws)['version']
    result = create(store, ws, operation='historical-child', base_version=version)
    assert body(store)['blocks'][:-1] == expected
    assert body(store)['title'] == original['title']
    assert body(store)['blocks'][-1]['props']['page_id'] == result['page']['id']
    stable = dump(store)
    assert create(store, ws, operation='historical-child', base_version=version)['replayed'] is True
    assert dump(store) == stable


@pytest.mark.parametrize('action', ['delete', 'move'])
def test_ui_historical_copy_descriptor_is_current_without_rewriting_source(environment, action):
    store, ws = environment
    original, expected = older(store, ws, action)
    before = dump(store)
    prepared = page_copy_source.prepare_page_copy(store.iam, 'owner', ws, 'page-e1', **fences(store))
    assert prepared.documents[0].descriptor()['blocks'] == expected
    assert prepared.documents[0].descriptor()['title'] == original['title']
    assert dump(store) == before and body(store) == original


def test_ui_historical_multiple_moves_are_not_reapplied_to_current_order(environment):
    store, ws = environment
    original, _expected = older(store, ws, 'move')
    child = original['blocks'][-1]['id']
    structural(store, 'move', 'retained-structure-note', after=child, operation='second-old-move')
    text_update(store, ws, 'page-e1', 'retained-structure-note', '두번째 이동 뒤의 입력')
    projected = body(store)
    current = store.iam.read_document('owner', 'page-e1')
    by_id = {block['id']: block for block in projected['blocks']}
    expected = [by_id[block['id']] for block in current['blocks']]
    create(store, ws, operation='multiple-history-child')
    assert body(store)['blocks'][:-1] == expected
    # A second link operation sees the already-correct order and must not replay
    # old moves onto it (move composition is not idempotent).
    create(store, ws, operation='multiple-history-second-child')
    assert body(store)['blocks'][:-2] == expected


@pytest.mark.parametrize('action', ['delete', 'move'])
def test_ui_historical_repair_and_child_rollback_together(environment, action):
    store, ws = environment
    _old, expected = older(store, ws, action)
    before = dump(store)
    with pytest.raises(InjectedFailure):
        create(store, ws, operation='historical-rollback', fault='after_outbox')
    assert dump(store) == before
    create(store, ws, operation='historical-rollback')
    assert body(store)['blocks'][:-1] == expected


def test_ui_historical_late_command_fences_repair(environment, monkeypatch):
    store, ws = environment
    original, _expected = older(store, ws, 'move')
    prepare = page_move._prepare_deltas
    stable = None
    def late(*args):
        nonlocal stable
        result = prepare(*args)
        structural(store, 'delete', original['blocks'][0]['id'], operation='late-history-delete')
        stable = dump(store)
        return result
    monkeypatch.setattr(page_move, '_prepare_deltas', late)
    with pytest.raises(DocumentSequenceConflict):
        create(store, ws, operation='historical-stale')
    assert stable is not None and dump(store) == stable


def test_ui_historical_delta_tamper_is_independently_rejected(environment, monkeypatch):
    store, ws = environment
    older(store, ws, 'delete')
    prepare = page_move._prepare_deltas
    def bad(*args):
        result = prepare(*args)
        result['page-e1']['payload'] = b'\0\0'
        return result
    monkeypatch.setattr(page_move, '_prepare_deltas', bad)
    before = dump(store)
    with pytest.raises(AuthorityUnavailable):
        create(store, ws, operation='historical-tampered')
    assert dump(store) == before


@pytest.mark.parametrize('mode', ['native', 'copy'])
def test_ui_historical_unique_insert_uses_current_values_and_preserves_live_blocks(environment, mode):
    store, ws = environment
    older(store, ws, 'move')
    note(store, 'older-missing-note', after='retained-structure-note')
    text_update(store, ws, 'page-e1', 'retained-structure-note', '추가 뒤의 최신 문단')
    original = body(store)
    canonical = store.iam.read_document('owner', 'page-e1')['blocks']
    live = {block['id']: block for block in original['blocks']}
    expected = [live.get(block['id'], {key: value for key, value in block.items() if key != 'position'}) for block in canonical]
    assert 'older-missing-note' not in live
    before = dump(store)
    if mode == 'native':
        create(store, ws, operation='historical-insert-child')
        assert body(store)['blocks'][:-1] == expected
    else:
        prepared = page_copy_source.prepare_page_copy(store.iam, 'owner', ws, 'page-e1', **fences(store))
        assert prepared.documents[0].descriptor()['blocks'] == expected
        assert dump(store) == before and body(store) == original


@pytest.mark.parametrize('budget', ['MAX_COMMANDS', 'MAX_ANCHOR_BYTES', 'MAX_HISTORY_BYTES'])
def test_ui_historical_budget_exhaustion_refuses_old_repair_without_writes(environment, budget, monkeypatch):
    from code_assist_v1.notion_collaboration import parent_historical_structure
    store, ws = environment
    older(store, ws, 'delete')
    monkeypatch.setattr(parent_historical_structure, budget, 0)
    before = dump(store)
    with pytest.raises(AuthorityUnavailable):
        create(store, ws, operation='historical-over-budget')
    assert dump(store) == before


@pytest.mark.parametrize('budget', ['MAX_COMMANDS', 'MAX_ANCHOR_BYTES', 'MAX_HISTORY_BYTES'])
def test_ui_current_tail_remains_available_when_historical_certificate_is_over_budget(environment, budget, monkeypatch):
    from code_assist_v1.notion_collaboration import parent_historical_structure
    store, ws = environment
    _original, expected = setup(store, ws, 'move')
    monkeypatch.setattr(parent_historical_structure, budget, 0)
    create(store, ws, operation='current-tail-without-old-certificate')
    assert body(store)['blocks'][:-1] == expected


def test_ui_historical_corrupt_immutable_anchor_never_authorizes_a_writer(environment):
    from code_assist_v1.tests.notion_collaboration.test_page_archive import database
    store, ws = environment
    older(store, ws, 'delete')
    # Simulate storage corruption only inside this sanitized temporary catalog.
    with database(store.db_path) as con:
        con.execute('DROP TRIGGER trg_collab_revisions_immutable_update')
        con.execute("UPDATE workspace_collaboration_revisions SET content_sha256=? WHERE document_id='page-e1' AND revision=1",
            ('sha256:' + '0' * 64,))
        con.commit()
    before = dump(store)
    with pytest.raises(AuthorityUnavailable, match='anchor hash'):
        create(store, ws, operation='corrupt-history-child')
    assert dump(store) == before


def test_ui_historical_two_processes_commit_one_repair_and_one_child(environment):
    from code_assist_v1.tests.notion_collaboration.test_native_page_parent_insert_rebase import _create_rebased_process
    from code_assist_v1.tests.notion_collaboration.test_native_page_creation import registry
    store, ws = environment
    _original, expected = older(store, ws, 'move')
    version, count = store.get_workspace('owner', ws)['version'], len(registry(store))
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
        assert len(registry(store)) == count + 1 and body(store)['blocks'][:-1] == expected
    finally:
        start.set()
        for worker in workers:
            if worker.pid:
                worker.join(timeout=10)
                if worker.is_alive():
                    worker.terminate(); worker.join(timeout=10)
