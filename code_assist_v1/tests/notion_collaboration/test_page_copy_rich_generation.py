"""UI-PAGE-COPY: independent attributed text survives copy and editor replay."""
import base64
import json
from pathlib import Path
import shutil
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from code_assist_v1.notion_collaboration import search_projection
from code_assist_v1.notion_collaboration.title_projection import _frames, _generation
from code_assist_v1.tests.notion_collaboration.test_page_copy_commit import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, copy, dump,
)
from code_assist_v1.tests.notion_collaboration.test_page_copy_source import current_update, request
from code_assist_v1.tests.notion_collaboration.test_parent_rich_restore_rebase import edit_yjs
from code_assist_v1.notion_collaboration import page_copy_rich, page_copy_source, page_copy_remap
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, InjectedFailure
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
from code_assist_v1.tests.notion_collaboration.test_title_projection import append


def frames(store, document):
    with store.iam._reader() as con:
        return _frames(con, document, _generation(con, document))


def editor_replay(store, document):
    """Use the real browser bridge in a new document, then independently decode."""
    descriptor = store.iam.read_document('owner', document)
    repo = Path(__file__).parents[2]
    script = r'''
const fs=require('node:fs');require(process.argv[1]+'/notion_static/vendor/yjs-13.6.32.bundle.js');
const Y=globalThis.NotionYjs,Core=require(process.argv[1]+'/notion_static/notion_editor_core.js');
const Editor=require(process.argv[1]+'/notion_static/notion_collaboration_editor.js');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
let state={page_id:input.id,title:input.page.title,blocks:input.page.blocks,can_edit:true,server_revision:1};
const host=Core.createEditorHostAdapter({readDocument(){return state;},applyProjection(next){state={...state,...next};return {applied:true};},
 captureSelection(){return null;},restoreSelection(){return true;},focusBlock(){return true;}});
const bridge=Editor.createEditorBridge({Y,host});bridge.configureDocument(state);
bridge.applyFrames(input.frames.map(payload=>({kind:'crdt.update',payload:new Uint8Array(Buffer.from(payload,'base64'))})));
bridge.finishReplay();const update=Buffer.from(bridge.encodeState()).toString('base64');bridge.dispose();
process.stdout.write(update);
'''
    result = subprocess.run([shutil.which('node'), '-e', script, str(repo)],
        input=json.dumps({'id': document, 'page': descriptor,
            'frames': [base64.b64encode(value).decode() for value in frames(store, document)]}).encode(),
        capture_output=True, check=True, timeout=15)
    return search_projection.materialize([[base64.b64decode(result.stdout)]], rich=True)[0]


def test_ui_page_copy_keeps_raw_text_attributes_in_new_generation_and_fresh_editor(environment):
    store, ws = environment
    current_update(store, ws)
    before = search_projection.materialize([frames(store, 'page-e1')], rich=True)[0]
    identity = before['blocks'][0]['id']
    edit_yjs(store, ws, identity, 'format2')
    before = search_projection.materialize([frames(store, 'page-e1')], rich=True)[0]
    source_dump = dump(store)
    fences = request(store)
    result = copy(store, ws, operation='rich-generation-copy', fences=fences)
    copied = editor_replay(store, result['page']['id'])
    copied_identity = copied['blocks'][0]['id']
    assert copied_identity != identity
    assert copied['blocks'][0]['content'] == before['blocks'][0]['content']
    assert copied['rich_text']['blocks'][copied_identity] == before['rich_text']['blocks'][identity]
    assert search_projection.materialize([frames(store, 'page-e1')], rich=True)[0] == before
    # Replaying the exact copy request must not append another seed or receipt.
    committed = dump(store)
    assert copy(store, ws, operation='rich-generation-copy', fences=fences) == {**result, 'replayed': True}
    assert dump(store) == committed
    assert committed != source_dump


def format_source(store, ws):
    current_update(store, ws)
    identity = search_projection.materialize([frames(store, 'page-e1')])[0]['blocks'][0]['id']
    edit_yjs(store, ws, identity, 'format2')
    return identity


def attributed_update(store, ws, document, identity, link):
    script = r'''
const fs=require('node:fs');require(process.argv[1]);const Y=globalThis.NotionYjs;
const input=JSON.parse(fs.readFileSync(0,'utf8')),doc=new Y.Doc();
for(const frame of input.frames)Y.applyUpdate(doc,Buffer.from(frame,'base64'));
const vector=Y.encodeStateVector(doc),title=doc.getText('page-title');
title.format(0,title.length,{italic:true});const text=doc.getMap('blocks').get(input.id).get('content');
text.delete(0,text.length);text.insert(0,'한😀글 ');text.insert(text.length,'링크',{bold:true,link:input.link});
text.insert(text.length,' 끝',{italic:true,bold:false});
process.stdout.write(Buffer.from(Y.encodeStateAsUpdate(doc,vector)).toString('base64'));
'''
    result = subprocess.run([shutil.which('node'), '-e', script,
        str(Path(__file__).parents[2] / 'notion_static/vendor/yjs-13.6.32.bundle.js')],
        input=json.dumps({'frames': [base64.b64encode(value).decode() for value in frames(store, document)],
                         'id': identity, 'link': link}).encode(), capture_output=True, check=True, timeout=15)
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    append(store.iam, transport, ws, [base64.b64decode(result.stdout)], document=document)


def test_ui_page_copy_remaps_rich_link_and_preserves_title_unicode_runs_and_copy_of_copy(environment):
    from code_assist_v1.tests.notion_collaboration.test_page_copy_commit import create, registrations
    store, ws = environment
    first = format_source(store, ws)
    child = create(store, ws, operation='rich-generation-child')['page']['id']
    attributed_update(store, ws, 'page-e1', first, '/page/' + child)
    old = search_projection.materialize([frames(store, 'page-e1')], rich=True)[0]
    result = copy(store, ws, operation='rich-generation-links')
    mapped = {row['source_document_id']: row['document_id'] for row in registrations(store)}
    actual = editor_replay(store, result['page']['id'])
    expected = old['rich_text']['blocks'][first]
    expected[1]['attributes']['link'] = '/page/' + mapped[child]
    assert actual['rich_text']['blocks'][actual['blocks'][0]['id']] == expected
    assert actual['rich_text']['title'] == old['rich_text']['title']
    twice = copy(store, ws, result['page']['id'], operation='rich-generation-copy-twice')
    second = editor_replay(store, twice['page']['id'])
    twice_map = {row['source_document_id']: row['document_id'] for row in registrations(store)}
    expected[1]['attributes']['link'] = '/page/' + twice_map[mapped[child]]
    assert second['rich_text']['blocks'][second['blocks'][0]['id']] == expected


@pytest.mark.parametrize('historical', [False, True])
@pytest.mark.parametrize('replacement', [False, True])
def test_ui_page_copy_keeps_certified_restored_rich_text_or_plain_replacement(environment, historical, replacement):
    from code_assist_v1.tests.notion_collaboration.test_parent_rich_restore_rebase import restored
    from code_assist_v1.tests.notion_collaboration.test_parent_historical_rich_restore import historical as older
    store, ws = environment
    _original, expected, identity = (older if historical else restored)(store, ws, replacement=replacement)
    before = dump(store)
    source = page_copy_source.prepare_page_copy(store.iam, 'owner', ws, 'page-e1', **request(store))
    assert source.documents[0].descriptor()['blocks'] == expected and dump(store) == before
    result = copy(store, ws, operation='rich-generation-restored')
    actual = editor_replay(store, result['page']['id'])
    delta = actual['rich_text']['blocks'][actual['blocks'][0]['id']]
    assert delta == ([{'insert': expected[0]['content']}] if replacement else
        [{'insert': expected[0]['content'], 'attributes': {'bold': True, 'italic': False}}])
    assert search_projection.materialize([frames(store, 'page-e1')])[0]['blocks'][0]['id'] != identity


def test_ui_page_copy_rich_seed_rollback_then_exact_retry(environment):
    store, ws = environment
    identity = format_source(store, ws)
    expected = search_projection.materialize([frames(store, 'page-e1')], rich=True)[0]
    before, fences = dump(store), request(store)
    with pytest.raises(InjectedFailure):
        copy(store, ws, operation='rich-generation-fault', fences=fences, fault='after_outbox')
    assert dump(store) == before
    result = copy(store, ws, operation='rich-generation-fault', fences=fences)
    actual = editor_replay(store, result['page']['id'])
    assert actual['rich_text']['blocks'][actual['blocks'][0]['id']] == expected['rich_text']['blocks'][identity]


def test_ui_page_copy_rich_seed_late_format_only_change_fences_all_writes(environment, monkeypatch):
    from code_assist_v1.notion_collaboration import page_copy as copying
    store, ws = environment
    identity = format_source(store, ws)
    capacity, after = copying._check_capacity, []
    def changed(*args):
        capacity(*args)
        edit_yjs(store, ws, identity, 'format')
        after.extend(dump(store))
    monkeypatch.setattr(copying, '_check_capacity', changed)
    with pytest.raises(page_copy_source.CopySourceChanged, match='content'):
        copy(store, ws, operation='rich-generation-late-format')
    assert dump(store) == tuple(after)


def test_ui_page_copy_rich_seed_independent_decoder_rejects_format_only_tamper(environment, monkeypatch):
    store, ws = environment
    format_source(store, ws)
    before = dump(store)
    run = subprocess.run
    def corrupt(command, *args, **options):
        result = run(command, *args, **options)
        if str(command[-1]) == str(Path(page_copy_rich.__file__).with_suffix('.cjs')):
            response = json.loads(result.stdout)
            script = r'''
require(process.argv[1]);const Y=globalThis.NotionYjs,doc=new Y.Doc();
Y.applyUpdate(doc,Buffer.from(process.argv[2],'base64'));
const text=doc.getMap('blocks').values().next().value.get('content');text.format(0,text.length,{bold:false});
process.stdout.write(Buffer.from(Y.encodeStateAsUpdate(doc)).toString('base64'));
'''
            bad = run([shutil.which('node'), '-e', script,
                str(Path(__file__).parents[2] / 'notion_static/vendor/yjs-13.6.32.bundle.js'), response['update']],
                capture_output=True, check=True, timeout=15)
            return SimpleNamespace(returncode=0, stdout=json.dumps({**response, 'update': bad.stdout.decode()}).encode())
        return result
    monkeypatch.setattr(subprocess, 'run', corrupt)
    with pytest.raises(AuthorityUnavailable, match='content or formatting'):
        copy(store, ws, operation='rich-generation-tamper')
    assert dump(store) == before


def test_ui_page_copy_rich_seed_budget_refuses_before_any_write(environment, monkeypatch):
    store, ws = environment
    format_source(store, ws)
    before = dump(store)
    monkeypatch.setattr(page_copy_rich, 'MAX_SEED_BYTES', 1)
    with pytest.raises(AuthorityUnavailable, match='could not be prepared'):
        copy(store, ws, operation='rich-generation-budget')
    assert dump(store) == before


def test_ui_page_copy_rich_work_is_outside_sql_and_checkpoint_includes_initial_frame(environment, monkeypatch):
    store, ws = environment
    format_source(store, ws)
    opened = 0
    @contextmanager
    def tracked(method):
        nonlocal opened
        with method() as con:
            opened += 1
            try:
                yield con
            finally:
                opened -= 1
    reader, writer = store.iam._reader, store.iam._transaction
    monkeypatch.setattr(store.iam, '_reader', lambda: tracked(reader))
    monkeypatch.setattr(store.iam, '_transaction', lambda: tracked(writer))
    for target, name in ((page_copy_rich, 'capture'), (page_copy_rich, 'prepare'), (search_projection, 'materialize')):
        original = getattr(target, name)
        def outside(*args, original=original, **options):
            assert opened == 0
            return original(*args, **options)
        monkeypatch.setattr(target, name, outside)
    result = copy(store, ws, operation='rich-generation-sql-boundary')
    with store.iam._reader() as con:
        checkpoint = con.execute('SELECT * FROM workspace_collaboration_revision_checkpoints WHERE document_id=?',
            (result['page']['id'],)).fetchone()
        assert checkpoint['through_update_sequence'] == 1 and checkpoint['document_sequence'] == 1
        assert con.execute('SELECT COUNT(*) FROM workspace_collaboration_crdt_updates WHERE document_id=?',
            (result['page']['id'],)).fetchone()[0] == 1
        assert con.execute('PRAGMA foreign_key_check').fetchall() == []


def test_ui_page_copy_rich_two_real_processes_commit_one_seed_per_document(environment):
    from code_assist_v1.tests.notion_collaboration.test_page_copy_commit import (
        test_ui_copy_commit_two_process_same_operation_has_one_subtree as two_processes,
        registrations,
    )
    store, ws = environment
    format_source(store, ws)
    two_processes(environment)
    root = next(row['document_id'] for row in registrations(store) if row['source_document_id'] == 'page-e1')
    assert len(frames(store, root)) == 1
    actual = editor_replay(store, root)
    assert actual['rich_text']['blocks'][actual['blocks'][0]['id']][0]['attributes']['bold'] is True
