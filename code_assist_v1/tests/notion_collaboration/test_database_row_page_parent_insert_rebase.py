"""UI-ROW-PAGE-INSERT: pending parent notes survive batch placement and return."""
from contextlib import contextmanager
import multiprocessing

import pytest

from code_assist_v1.notion_collaboration import database_row_page_move as placement, page_move, search_projection
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, InjectedFailure
from code_assist_v1.notion_collaboration.editing_authority import DocumentSequenceConflict
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_database_row_page_move import (
    placement_environment, icon_batch_environment, activity_environment, environment,
    appearance_environment, body_environment, team_environment, file_environment,
    member_environment, native_environment, archive_environment, guest_environment,
    image_environment, _request, _return_request,
)
from code_assist_v1.tests.notion_collaboration.test_page_archive import dump
from code_assist_v1.tests.notion_collaboration.test_page_copy_source import current_update
from code_assist_v1.tests.notion_collaboration.test_page_link_parent_insert_rebase import note, body, assert_note
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import text_update


def setup(store, ws, direction, cross=False):
    parent = 'page-e1'
    if cross:
        source = store.iam.read_document('owner', 'page-e1')
        parent = 'pending-row-page-parent'
        store.iam.create_document('owner', ws, parent, title='새 문단 부모',
            blocks=[{'id': 'cross-parent-text', 'type': 'paragraph', 'content': '기존 문단', 'props': {}}],
            expected_workspace_epoch=source['workspace_epoch'], operation_id='pending-row-parent-create')
        text_update(store, ws, parent, 'cross-parent-text', '현재 부모 문단')
    else:
        current_update(store, ws)
    if direction == 'back':
        placement.move_owned_rows_to_page(store.iam, **_request(store, ws, 'prepare-row-return', parent_id=parent))
    original = body(store, parent)
    pending = note(store, 'pending-row-page-parent-note', parent)
    assert all(block['id'] != pending['id'] for block in body(store, parent)['blocks'])
    request = (_request(store, ws, 'pending-row-page-out', parent_id=parent)
               if direction == 'out' else _return_request(store, ws, 'pending-row-page-back'))
    service = placement.move_owned_rows_to_page if direction == 'out' else placement.return_page_to_owned_database
    call = lambda **options: service(store.iam, **request, **options)
    return locals()


@pytest.mark.parametrize('direction', ['out', 'back'])
@pytest.mark.parametrize('cross', [False, True])
def test_ui_row_page_parent_notes_exact_body_order_and_retry(placement_environment, direction, cross, monkeypatch):
    store, ws = placement_environment
    plan = setup(store, ws, direction, cross)
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
        assert opened == 0, 'parent replay cannot retain a SQLite reader'
        return replay(*args, **kwargs)
    monkeypatch.setattr(store.iam, '_reader', observed)
    monkeypatch.setattr(search_projection, 'materialize', outside)
    result = plan['call']()
    current = body(store, plan['parent'])
    assert_note(current, plan['pending'])
    assert current['title'] == plan['original']['title']
    kept = [block for block in current['blocks'] if block['id'] != plan['pending']['id']]
    if direction == 'out':
        assert kept[:len(plan['original']['blocks'])] == plan['original']['blocks']
        assert [block['props']['page_id'] for block in kept[len(plan['original']['blocks']):]] == ['row-e1', 'row-e2']
        assert current['blocks'][-2:] == kept[-2:]
        assert store.iam.read_database('owner', 'database-e1')['rows'] == []
    else:
        assert kept == [block for block in plan['original']['blocks'] if block['props'].get('page_id') != 'row-e1']
        returned = store.iam.read_database('owner', 'database-e1')['rows'][0]
        assert returned['id'] == 'row-e1' and returned['cells']['property-date'] == '2026-09-15'
    stable = dump(store)
    assert plan['call']() == result
    assert dump(store) == stable
    reopened = CollaborativeWorkspaceStore(store.db_path, user_data_root=store.user_data_root,
        identity_directory=store.identity_directory)
    assert body(reopened, plan['parent']) == current


@pytest.mark.parametrize('direction', ['out', 'back'])
def test_ui_row_page_parent_uncertified_older_gap_refuses(placement_environment, direction, monkeypatch):
    from code_assist_v1.notion_collaboration import parent_historical_structure
    monkeypatch.setattr(parent_historical_structure, 'capture', lambda *args: None)
    store, ws = placement_environment
    plan = setup(store, ws, direction)
    old = plan['original']['blocks'][0]
    text_update(store, ws, plan['parent'], old['id'], old['content'])
    plan['request'].update(_request(store, ws, 'pending-row-page-out') if direction == 'out'
                           else _return_request(store, ws, 'pending-row-page-back'))
    before = dump(store)
    with pytest.raises(AuthorityUnavailable):
        plan['call']()
    assert dump(store) == before


@pytest.mark.parametrize('direction', ['out', 'back'])
def test_ui_row_page_parent_tampered_body_refuses_before_writes(placement_environment, direction, monkeypatch):
    store, ws = placement_environment
    plan = setup(store, ws, direction)
    prepare = page_move._prepare_deltas
    def bad(*args):
        deltas = prepare(*args)
        deltas[plan['parent']]['payload'] = b'\0\0'
        return deltas
    monkeypatch.setattr(page_move, '_prepare_deltas', bad)
    before = dump(store)
    with pytest.raises(AuthorityUnavailable, match='unrelated parent content'):
        plan['call']()
    assert dump(store) == before


@pytest.mark.parametrize('direction', ['out', 'back'])
def test_ui_row_page_parent_late_note_fences_commit(placement_environment, direction, monkeypatch):
    store, ws = placement_environment
    plan = setup(store, ws, direction)
    prepare = page_move._prepare_deltas
    stable = None
    def late(*args):
        nonlocal stable
        value = prepare(*args)
        note(store, 'late-row-page-parent-note', plan['parent'])
        stable = dump(store)
        return value
    monkeypatch.setattr(page_move, '_prepare_deltas', late)
    with pytest.raises(DocumentSequenceConflict):
        plan['call']()
    assert stable is not None and dump(store) == stable


@pytest.mark.parametrize('direction', ['out', 'back'])
def test_ui_row_page_parent_note_and_domain_rollback_together(placement_environment, direction):
    store, ws = placement_environment
    plan = setup(store, ws, direction)
    before = dump(store)
    with pytest.raises(InjectedFailure):
        plan['call'](fault='after_outbox')
    assert dump(store) == before
    plan['call']()
    assert_note(body(store), plan['pending'])


def test_ui_row_page_parent_batch_structural_anchor_is_verified(placement_environment, monkeypatch):
    store, ws = placement_environment
    plan = setup(store, ws, 'out')
    prepare = page_move._prepare_deltas
    def bad(*args):
        deltas = prepare(*args)
        deltas[plan['parent']]['placed_many'][0]['after_block_id'] = 'unrelated-anchor'
        return deltas
    monkeypatch.setattr(page_move, '_prepare_deltas', bad)
    before = dump(store)
    with pytest.raises(AuthorityUnavailable, match='batch link placement'):
        plan['call']()
    assert dump(store) == before


def test_ui_row_page_batch_keeps_unrelated_unresolved_child_locator():
    # A projected unrelated locator need not resolve to a native page. It must
    # neither collide with the chosen string identity nor break body checking.
    original = {'id': 'unresolved-child', 'type': 'child_page', 'content': '기존 링크',
                'props': {'page_id': ['unresolved'], 'color': 'blue'}}
    link = {'id': 'selected-row-link', 'type': 'child_page', 'content': '선택한 행',
            'props': {'page_id': 'selected-row'}}
    source = {'id': 'parent', 'generation': 1, 'frames': [],
              'snapshot': {'title': '부모 원문', 'blocks': [original]},
              'insert': None, 'insert_many': [link], 'post_frame_insert_ids': []}
    deltas = page_move._prepare_deltas({'parent': source}, 'selected-row', 'unresolved-parent-batch')
    page_move.parent_rebase.validate_deltas({'parent': source}, deltas, 'selected-row')
    projected = search_projection.materialize([[deltas['parent']['payload']]])[0]
    assert projected == {'title': '부모 원문', 'blocks': [original, link]}


def _out_worker(catalog, root, request, ready, start, results):
    store = CollaborativeWorkspaceStore(catalog, user_data_root=root,
        identity_directory=InMemoryIdentityDirectory(['owner']))
    prepare = page_move._prepare_deltas
    def coordinated(*args):
        value = prepare(*args)
        ready.put(True)
        if not start.wait(60):
            raise RuntimeError('outside-reader batch barrier timed out')
        return value
    page_move._prepare_deltas = coordinated
    try:
        results.put(placement.move_owned_rows_to_page(store.iam, **request))
    except Exception as exc:
        results.put({'error': type(exc).__name__, 'message': str(exc)})


def test_ui_row_page_parent_two_processes_prepare_note_and_link_each_row_once(placement_environment):
    store, ws = placement_environment
    plan = setup(store, ws, 'out')
    context = multiprocessing.get_context('spawn')
    ready, start, results = context.Queue(), context.Event(), context.Queue()
    workers = [context.Process(target=_out_worker, args=(str(store.db_path), str(store.user_data_root),
        plan['request'], ready, start, results)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        assert [ready.get(timeout=120) for _ in workers] == [True, True]
        start.set()
        values = [results.get(timeout=120) for _ in workers]
        assert all('error' not in value for value in values), values
        assert values[0] == values[1]
        current = body(store)
        assert_note(current, plan['pending'])
        assert [block['props']['page_id'] for block in current['blocks'] if block['type'] == 'child_page'] == ['row-e1', 'row-e2']
    finally:
        start.set()
        for worker in workers:
            if worker.pid:
                worker.join(timeout=10)
                if worker.is_alive():
                    worker.terminate()
                    worker.join(timeout=10)
