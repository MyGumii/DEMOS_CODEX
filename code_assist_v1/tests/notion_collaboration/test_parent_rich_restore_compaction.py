"""UI-PARENT-RESTORE: deletion recovery uses its exact older compaction range."""
import base64
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest

from code_assist_v1.notion_collaboration import parent_restore_recovery, search_projection, page_move
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
from code_assist_v1.notion_collaboration.selected_runtime_contract import (
    COMPACTION_RESPONSE_CONTRACT, runtime_binding_sha256, sha256_bytes,
)
from code_assist_v1.tests.notion_collaboration.test_parent_rich_restore_rebase import (
    environment, member_environment, native_environment, archive_environment, guest_environment,
    image_environment, current_update, body, structural, create, dump, request, edit_yjs,
)


def compact(store, grant, operation):
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    work = transport.prepare_compaction(grant)
    script = r'''
const fs=require('node:fs');require(process.argv[1]);const Y=globalThis.NotionYjs;
const doc=new Y.Doc();for(const frame of JSON.parse(fs.readFileSync(0,'utf8')))Y.applyUpdate(doc,Buffer.from(frame,'base64'));
process.stdout.write(JSON.stringify({snapshot:Buffer.from(Y.encodeStateAsUpdate(doc)).toString('base64'),
 vector:Buffer.from(Y.encodeStateVector(doc)).toString('base64')}));
'''
    frames = ([work.base_snapshot] if work.base_snapshot is not None else []) + list(work.ordered_updates)
    decoded = json.loads(subprocess.run([shutil.which('node'), '-e', script,
        str(Path(__file__).parents[2] / 'notion_static/vendor/yjs-13.6.32.bundle.js')],
        input=json.dumps([base64.b64encode(frame).decode() for frame in frames]).encode(),
        capture_output=True, check=True, timeout=15).stdout)
    snapshot, vector = base64.b64decode(decoded['snapshot']), base64.b64decode(decoded['vector'])
    response = {'contract': COMPACTION_RESPONSE_CONTRACT,
        'request_id': work.request['request_id'], 'request_sha256': work.request['request_sha256'],
        'base_high_water_sequence': work.request['base_high_water_sequence'],
        'target_high_water_sequence': work.request['target_high_water_sequence'],
        'applied_update_count': work.request['ordered_update_count'],
        'snapshot_bytes': len(snapshot), 'snapshot_sha256': sha256_bytes(snapshot),
        'state_vector_bytes': len(vector), 'state_vector_sha256': sha256_bytes(vector),
        'runtime_binding_sha256': runtime_binding_sha256()}
    result = transport.commit_compaction(grant, operation_id=operation, work=work,
        response=response, snapshot=snapshot, state_vector=vector)
    return result, snapshot


def compacted_restoration(store, ws):
    current_update(store, ws)
    create(store, ws, operation='compacted-restore-bootstrap')
    identity = body(store)['blocks'][0]['id']
    grant = edit_yjs(store, ws, identity, 'format2')
    original = body(store)
    before, snapshot = compact(store, grant, 'compacted-before-delete')
    deletion = structural(store, 'delete', identity, operation='compacted-delete')
    assert deletion['recovery_through_update_sequence'] == before['through_update_sequence']
    grant = edit_yjs(store, ws, identity, 'delete')
    after, _ = compact(store, grant, 'compacted-after-delete')
    assert after['through_update_sequence'] > before['through_update_sequence']
    block = original['blocks'][0]
    store.iam.execute_typed_command('owner', 'page-e1', command_kind='block.insert', target_id=identity,
        payload={**{key: block[key] for key in ('type', 'content', 'props')},
            'after_block_id': None, 'restore_deleted': True}, operation_id='compacted-restore', **request(store))
    return original, identity, before, snapshot


def test_ui_parent_rich_restore_chooses_deletion_snapshot_before_newer_removed_body(environment, monkeypatch):
    store, ws = environment
    original, identity, before, snapshot = compacted_restoration(store, ws)
    prepare = page_move._prepare_deltas
    witnessed = []
    def exact(sources, *args):
        proof = sources['page-e1']['post_frame_structure']['restores'][0]
        assert proof['through_update_sequence'] == before['through_update_sequence']
        assert [base64.b64decode(frame['payload']) for frame in proof['frames']] == [snapshot]
        witnessed.append(True)
        return prepare(sources, *args)
    monkeypatch.setattr(page_move, '_prepare_deltas', exact)
    create(store, ws, operation='compacted-restored-child')
    assert witnessed == [True] and body(store)['blocks'][:-1] == original['blocks']
    from code_assist_v1.notion_collaboration.title_projection import _frames, _generation
    with store.iam._reader() as con:
        frames = _frames(con, 'page-e1', _generation(con, 'page-e1'))
    delta = search_projection.materialize([frames], rich=True)[0]['rich_text']['blocks'][identity]
    assert delta[0]['attributes'] == {'bold': True, 'italic': True}


@pytest.mark.parametrize('invalid', ['checksum', 'wrong-generation', 'missing-high-water'])
def test_ui_parent_rich_restore_invalid_compacted_range_is_refused_without_writes(environment, invalid):
    store, ws = environment
    _original, _identity, before, _snapshot = compacted_restoration(store, ws)
    if invalid == 'checksum':
        # Deliberate corruption is confined to the synthetic temporary catalog.
        with sqlite3.connect(store.db_path) as con:
            con.execute('DROP TRIGGER trg_collab_crdt_snapshots_immutable_update')
            con.execute('UPDATE workspace_collaboration_crdt_snapshots SET snapshot_sha256=? WHERE id=?',
                ('sha256:' + '0' * 64, before['snapshot_id']))
    stable = dump(store)
    generation = 2 if invalid == 'wrong-generation' else 1
    through = before['through_update_sequence'] + 100 if invalid == 'missing-high-water' else before['through_update_sequence']
    with pytest.raises(AuthorityUnavailable):
        if invalid == 'checksum':
            create(store, ws, operation='corrupt-compacted-restored-child')
        else:
            with store.iam._reader() as con:
                con.execute('BEGIN')
                parent_restore_recovery.frames_at(con, 'page-e1', generation, through)
    assert dump(store) == stable
