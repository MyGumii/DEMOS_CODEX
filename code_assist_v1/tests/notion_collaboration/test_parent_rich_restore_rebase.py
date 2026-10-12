"""UI-PARENT-RESTORE: restore exact deleted Yjs body during parent link writes."""
import base64
import json
from pathlib import Path
import shutil
import subprocess
from contextlib import contextmanager
import multiprocessing

import pytest

from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
from code_assist_v1.tests.notion_collaboration.test_parent_structure_rebase import (
    archive_environment, guest_environment, image_environment, body, structural, create, dump,
)
from code_assist_v1.tests.notion_collaboration.test_page_copy_commit import environment, member_environment, native_environment
from code_assist_v1.tests.notion_collaboration.test_parent_historical_structure_rebase import note
from code_assist_v1.tests.notion_collaboration.test_page_copy_source import current_update, request
from code_assist_v1.tests.notion_collaboration.test_title_projection import append
from code_assist_v1.notion_collaboration.title_projection import _frames, _generation


def edit_yjs(store, ws, identity, action, *, document='page-e1'):
    with store.iam._reader() as con:
        frames = _frames(con, document, _generation(con, document))
    script = r'''
const fs=require('node:fs');require(process.argv[1]);const Y=globalThis.NotionYjs;
const input=JSON.parse(fs.readFileSync(0,'utf8')),doc=new Y.Doc();
for(const frame of input.frames)Y.applyUpdate(doc,Buffer.from(frame,'base64'));
const vector=Y.encodeStateVector(doc),order=doc.getArray('block-order'),blocks=doc.getMap('blocks');
const item=blocks.get(input.id);if(!item)throw Error('Missing fixture body');
if(input.action==='delete'){const at=order.toArray().indexOf(input.id);if(at<0)throw Error('Missing fixture order');order.delete(at,1);blocks.delete(input.id);}
else{const text=item.get('content');text.format(0,text.length,{bold:true,italic:input.action==='format2'});}
process.stdout.write(Buffer.from(Y.encodeStateAsUpdate(doc,vector)).toString('base64'));
'''
    result = subprocess.run([shutil.which('node'), '-e', script,
        str(Path(__file__).parents[2] / 'notion_static/vendor/yjs-13.6.32.bundle.js')],
        input=json.dumps({'frames': [base64.b64encode(frame).decode() for frame in frames], 'id': identity, 'action': action}).encode(),
        capture_output=True, check=True, timeout=15)
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    return append(store.iam, transport, ws, [base64.b64decode(result.stdout)], document=document)


def restored(store, ws, *, replacement=False, before_delete=None):
    current_update(store, ws)
    first = body(store)['blocks'][0]['id']
    note(store, 'restore-retained-note', after=first)
    child = create(store, ws, operation='restore-body-bootstrap')['page']['id']
    if before_delete:
        before_delete(child)
    edit_yjs(store, ws, first, 'format')
    original = body(store)
    block = original['blocks'][0]
    deleted = structural(store, 'delete', first, operation='rich-restore-delete')
    edit_yjs(store, ws, first, 'delete')
    assert all(item['id'] != first for item in body(store)['blocks'])
    values = {key: block[key] for key in ('type', 'content', 'props')}
    if replacement:
        values = {'type': 'heading2', 'content': '명시한 복원 내용', 'props': {'color': 'red'}}
    result = store.iam.execute_typed_command('owner', 'page-e1', command_kind='block.insert', target_id=first,
        payload={**values, 'after_block_id': None, 'restore_deleted': True}, operation_id='rich-restore-command',
        **request(store))
    assert result['restored_deletion_command_id'] == deleted['command_id']
    expected = [{**block, **values}, *original['blocks'][1:]]
    return original, expected, first


@pytest.mark.parametrize('replacement', [False, True])
def test_ui_parent_rich_restore_native_keeps_deleted_body_or_explicit_replacement(environment, replacement):
    store, ws = environment
    original, expected, _first = restored(store, ws, replacement=replacement)
    version = store.get_workspace('owner', ws)['version']
    create(store, ws, operation='rich-restored-child', base_version=version)
    assert body(store)['blocks'][:-1] == expected
    assert body(store)['title'] == original['title']
    stable = dump(store)
    assert create(store, ws, operation='rich-restored-child', base_version=version)['replayed'] is True
    assert dump(store) == stable


def test_ui_parent_rich_restore_pure_copy_descriptor_does_not_rewrite_original(environment):
    from code_assist_v1.notion_collaboration import page_copy_source
    store, ws = environment
    original, expected, _first = restored(store, ws)
    before = dump(store)
    prepared = page_copy_source.prepare_page_copy(store.iam, 'owner', ws, 'page-e1', **request(store))
    assert prepared.documents[0].descriptor()['blocks'] == expected
    assert prepared.documents[0].descriptor()['title'] == original['title']
    assert dump(store) == before


@pytest.mark.parametrize('action', ['move', 'copy', 'archive', 'restore'])
def test_ui_parent_rich_restore_page_lifecycle_keeps_exact_body_and_retry(environment, action):
    from code_assist_v1.notion_collaboration import page_move, page_archive, page_copy
    from code_assist_v1.tests.notion_collaboration.test_page_archive import request as archive_request
    store, ws = environment
    held = {}
    def prepare_restore(child):
        held['child'] = child
        page_archive.set_page_archived(store.iam, 'owner', ws, child,
            **archive_request(store, ws, child, operation='rich-restore-archive-before'))
    original, expected, first = restored(store, ws, before_delete=prepare_restore if action == 'restore' else None)
    child = held.get('child') or original['blocks'][-1]['props']['page_id']
    if action == 'move':
        destination = create(store, ws, parent='', operation='rich-restore-destination')['page']['id']
        fences = request(store, child)
        call = lambda: page_move.move_page(store.iam, 'owner', ws, child, parent_id=destination,
            operation_id='rich-restore-lifecycle-move', **fences)
    elif action == 'copy':
        fences = request(store, child)
        call = lambda: page_copy.copy_page(store.iam, 'owner', ws, child, operation_id='rich-restore-lifecycle-copy', **fences)
    else:
        fences = archive_request(store, ws, child, archived=action == 'archive', operation='rich-restore-lifecycle-' + action)
        call = lambda: page_archive.set_page_archived(store.iam, 'owner', ws, child, **fences)
    result = call()
    current = body(store)
    if action in {'move', 'archive'}:
        expected = [block for block in expected if block['props'].get('page_id') != child]
        assert current['blocks'] == expected
    else:
        assert current['blocks'][:-1] == expected
    assert current['title'] == original['title']
    stable = dump(store)
    call()
    assert dump(store) == stable


def test_ui_parent_rich_restore_second_cycle_uses_latest_deletion_and_keeps_first_tombstone(environment):
    store, ws = environment
    _original, _expected, first = restored(store, ws)
    create(store, ws, operation='first-rich-restore-child')
    with store.iam._reader() as con:
        tombstone = tuple(con.execute('SELECT * FROM workspace_collaboration_block_tombstones WHERE block_id=?', (first,)).fetchone())
    edit_yjs(store, ws, first, 'format2')
    expected = body(store)
    block = expected['blocks'][0]
    structural(store, 'delete', first, operation='rich-restore-delete-second')
    edit_yjs(store, ws, first, 'delete')
    store.iam.execute_typed_command('owner', 'page-e1', command_kind='block.insert', target_id=first,
        payload={**{key: block[key] for key in ('type', 'content', 'props')}, 'after_block_id': None, 'restore_deleted': True},
        operation_id='rich-restore-command-second', **request(store))
    create(store, ws, operation='second-rich-restore-child')
    assert body(store)['blocks'][:-1] == expected['blocks']
    with store.iam._reader() as con:
        assert tuple(con.execute('SELECT * FROM workspace_collaboration_block_tombstones WHERE block_id=?', (first,)).fetchone()) == tombstone
        frames = _frames(con, 'page-e1', _generation(con, 'page-e1'))
    from code_assist_v1.notion_collaboration import search_projection
    delta = search_projection.materialize([frames], rich=True)[0]['rich_text']['blocks'][first]
    assert delta[0]['attributes'] == {'bold': True, 'italic': True}


@pytest.mark.parametrize('target', ['restored', 'other'])
def test_ui_parent_rich_restore_attribute_only_tamper_is_independently_rejected(environment, target, monkeypatch):
    from code_assist_v1.notion_collaboration import page_move
    from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable
    store, ws = environment
    _original, _expected, first = restored(store, ws)
    prepare = page_move._prepare_deltas
    def bad(sources, *args):
        deltas = prepare(sources, *args)
        script = r'''
const fs=require('node:fs');require(process.argv[1]);const Y=globalThis.NotionYjs,input=JSON.parse(fs.readFileSync(0,'utf8')),doc=new Y.Doc();
for(const frame of input.frames)Y.applyUpdate(doc,Buffer.from(frame,'base64'));
const vector=Y.encodeStateVector(doc);Y.applyUpdate(doc,Buffer.from(input.delta,'base64'));
const text=doc.getMap('blocks').get(input.id).get('content');text.format(0,text.length,{bold:input.bold});
process.stdout.write(Buffer.from(Y.encodeStateAsUpdate(doc,vector)).toString('base64'));
'''
        result = subprocess.run([shutil.which('node'), '-e', script,
            str(Path(__file__).parents[2] / 'notion_static/vendor/yjs-13.6.32.bundle.js')],
            input=json.dumps({'frames': [base64.b64encode(frame).decode() for frame in sources['page-e1']['frames']],
                'delta': base64.b64encode(deltas['page-e1']['payload']).decode(),
                'id': first if target == 'restored' else 'restore-retained-note', 'bold': target != 'restored'}).encode(),
            capture_output=True, check=True, timeout=15)
        deltas['page-e1']['payload'] = base64.b64decode(result.stdout)
        return deltas
    monkeypatch.setattr(page_move, '_prepare_deltas', bad)
    before = dump(store)
    with pytest.raises(AuthorityUnavailable, match='text formatting'):
        create(store, ws, operation='tampered-rich-restored-child')
    assert dump(store) == before


def test_ui_parent_rich_restore_rollback_preserves_pending_restoration(environment):
    from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure
    store, ws = environment
    _original, expected, _first = restored(store, ws)
    before = dump(store)
    with pytest.raises(InjectedFailure):
        create(store, ws, operation='rich-restore-rollback', fault='after_outbox')
    assert dump(store) == before
    create(store, ws, operation='rich-restore-rollback')
    assert body(store)['blocks'][:-1] == expected


def test_ui_parent_rich_restore_replays_and_validates_attributes_outside_reader(environment, monkeypatch):
    from code_assist_v1.notion_collaboration import search_projection
    store, ws = environment
    _original, expected, _first = restored(store, ws)
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
    create(store, ws, operation='rich-restore-outside-reader')
    assert body(store)['blocks'][:-1] == expected


def test_ui_parent_rich_restore_late_change_fences_prepared_recovery(environment, monkeypatch):
    from code_assist_v1.notion_collaboration import page_move
    from code_assist_v1.notion_collaboration.editing_authority import DocumentSequenceConflict
    store, ws = environment
    restored(store, ws)
    prepare = page_move._prepare_deltas
    stable = None
    def late(*args):
        nonlocal stable
        value = prepare(*args)
        note(store, 'late-rich-restore-note')
        stable = dump(store)
        return value
    monkeypatch.setattr(page_move, '_prepare_deltas', late)
    with pytest.raises(DocumentSequenceConflict):
        create(store, ws, operation='late-rich-restore-child')
    assert stable is not None and dump(store) == stable


def test_ui_parent_rich_restore_budget_exhaustion_leaves_pending_state_unchanged(environment, monkeypatch):
    from code_assist_v1.notion_collaboration import parent_restore_recovery
    from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable
    store, ws = environment
    restored(store, ws)
    before = dump(store)
    monkeypatch.setattr(parent_restore_recovery, 'MAX_RECOVERY_BYTES', 1)
    with pytest.raises(AuthorityUnavailable):
        create(store, ws, operation='over-budget-rich-restore')
    assert dump(store) == before


def test_ui_parent_rich_restore_two_processes_commit_one_child_and_replay_exactly(environment):
    from code_assist_v1.tests.notion_collaboration.test_native_page_parent_insert_rebase import _create_rebased_process
    from code_assist_v1.tests.notion_collaboration.test_native_page_creation import registry
    store, ws = environment
    _original, expected, _first = restored(store, ws)
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
