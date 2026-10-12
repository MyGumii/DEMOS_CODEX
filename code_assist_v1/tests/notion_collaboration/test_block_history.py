"""UI-BLOCK-HISTORY: structural undo must agree with the canonical block ledger."""
import base64
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest
from flask import Flask

from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure, NotFoundOrDenied
from code_assist_v1.notion_collaboration.editing_authority import DocumentSequenceConflict, TargetVersionConflict
from code_assist_v1.notion_collaboration import synced_blocks, search_projection
from code_assist_v1.tests.notion_collaboration.test_collaborative_editing_authority import _environment
from code_assist_v1.tests.notion_collaboration.test_title_projection import append
from code_assist_v1 import routes_notion_workspace
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore, collaborative_page_payload
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory


def command(authority, kind, target, payload, operation, **options):
    page = authority.read_document('owner', 'page-e1')
    args = dict(command_kind=kind, target_id=target, payload=payload,
        expected_document_sequence=page['document_sequence'], expected_workspace_epoch=page['workspace_epoch'],
        expected_document_epoch=page['document_epoch'], operation_id=operation)
    args.update(options)
    return authority.execute_typed_command('owner', 'page-e1', **args)


def test_ui_block_history_real_yjs_undo_redo_preserves_canonical_order_and_deletion_history(tmp_path):
    authority, transport, _, catalog, workspace = _environment(tmp_path)
    script = Path(__file__).parents[1] / 'notion_block_history_fixture.cjs'
    events = json.loads(subprocess.run([shutil.which('node'), str(script)], check=True,
        capture_output=True, timeout=20).stdout)
    original_tombstone = None
    for step, event in enumerate(events):
        for index, item in enumerate(event['commands']):
            command(authority, item['command_kind'], item['target_id'], item['payload'], f'op-ui-history-{step}-{index}')
        append(authority, transport, workspace, [base64.b64decode(event['update'])])
        current = authority.read_document('owner', 'page-e1')
        assert [b['id'] for b in current['blocks']] == [b['id'] for b in event['snapshot']['blocks']], step
        with sqlite3.connect(catalog) as con:
            row = con.execute('SELECT * FROM workspace_collaboration_block_tombstones WHERE block_id=?', ('block-a',)).fetchone()
            if row and original_tombstone is None:
                original_tombstone = row
            if original_tombstone is not None:
                assert row == original_tombstone, 'first deletion record must remain immutable through every restore'
    assert events[2]['commands'][0]['payload']['restore_deleted'] is True
    assert events[7]['commands'][0]['payload']['restore_deleted'] is True
    with authority._reader() as con:
        con.execute('BEGIN')
        frames = synced_blocks._frames(con, 'page-e1', synced_blocks._generation(con, 'page-e1'))
        records = con.execute("SELECT result_json FROM workspace_collaboration_typed_commands WHERE target_id='block-a' AND command_kind='block.delete' ORDER BY document_sequence").fetchall()
        assert len(records) == 2
        assert [json.loads(r[0])['recovery']['block']['version'] for r in records] == [1, 2]
    restored = search_projection.materialize([frames])[0]
    assert restored['blocks'] == events[-1]['snapshot']['blocks']


def test_ui_block_history_restore_requires_explicit_intent_and_current_authority(tmp_path):
    authority, _, _, catalog, _ = _environment(tmp_path)
    deleted = command(authority, 'block.delete', 'block-a', {}, 'op-ui-delete-guards')
    payload = {'type':'paragraph', 'content':'A', 'props':{}, 'after_block_id':None}
    with pytest.raises(ValueError, match='identity already exists'):
        command(authority, 'block.insert', 'block-a', payload, 'op-ui-no-reuse')
    payload['restore_deleted'] = True
    with pytest.raises(NotFoundOrDenied):
        command(authority, 'block.insert', 'never-existed', payload, 'op-ui-no-fabricated-restore')
    with pytest.raises(TargetVersionConflict):
        command(authority, 'block.insert', 'block-a', payload, 'op-ui-stale-target', expected_target_version=99)
    with pytest.raises(DocumentSequenceConflict):
        command(authority, 'block.insert', 'block-a', payload, 'op-ui-stale-document', expected_document_sequence=deleted['document_sequence']-1)
    page = authority.read_document('owner', 'page-e1')
    with pytest.raises(NotFoundOrDenied):
        authority.execute_typed_command('unknown', 'page-e1', command_kind='block.insert', target_id='block-a', payload=payload,
            expected_document_sequence=page['document_sequence'], expected_workspace_epoch=page['workspace_epoch'],
            expected_document_epoch=page['document_epoch'], operation_id='op-ui-denied-restore')
    with pytest.raises(InjectedFailure, match='after_outbox'):
        command(authority, 'block.insert', 'block-a', payload, 'op-ui-restore-fault', fault='after_outbox')
    assert authority.read_document('owner', 'page-e1')['document_sequence'] == deleted['document_sequence']
    restored = command(authority, 'block.insert', 'block-a', payload, 'op-ui-restore-once', expected_target_version=1)
    assert restored['restored_deletion_command_id'] == deleted['command_id']
    assert restored['target_version'] == 2
    assert command(authority, 'block.insert', 'block-a', payload, 'op-ui-restore-once',
        expected_target_version=1, expected_document_sequence=deleted['document_sequence']) == restored
    with sqlite3.connect(catalog) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_typed_commands WHERE operation_id='op-ui-restore-fault'").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_block_tombstones WHERE block_id='block-a'").fetchone()[0] == 1


def test_ui_block_history_receipt_route_replays_exact_request_and_rechecks_actor(tmp_path, monkeypatch):
    authority, _, root, catalog, workspace = _environment(tmp_path)
    saved = command(authority, 'block.delete', 'block-a', {}, 'op-ui-receipt-delete')
    store = CollaborativeWorkspaceStore(catalog, user_data_root=root,
        identity_directory=InMemoryIdentityDirectory(['owner','content-editor','schema-editor']))
    actor = {'id':'owner'}
    monkeypatch.setattr(routes_notion_workspace, '_store', lambda:store)
    monkeypatch.setattr(routes_notion_workspace, '_collaborative_runtime_enabled', lambda:True)
    monkeypatch.setattr(routes_notion_workspace, 'current_user_id', lambda:actor['id'])
    app = Flask('ui-block-history-receipts'); app.config.update(TESTING=True, SECRET_KEY='ui-history-test')
    routes_notion_workspace.register_notion_workspace_routes(app)
    client = app.test_client()
    endpoint = f'/api/notion/workspaces/{workspace}/pages/page-e1/editing/commands'
    response = client.get(endpoint, query_string={'operation_id':'op-ui-receipt-delete'})
    assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    receipt = response.get_json()['receipt']
    assert receipt['result'] == saved
    replay = client.post(endpoint, json=receipt['command'])
    assert replay.status_code == 201 and replay.get_json()['document_sequence'] == saved['document_sequence']
    altered = {**receipt['command'], 'expected_document_sequence':saved['document_sequence']}
    assert client.post(endpoint, json=altered).status_code == 409, 'different request under same operation id remains forbidden'
    assert client.get(endpoint, query_string={'operation_id':'op-ui-unknown-receipt'}).get_json()['receipt'] is None
    assert collaborative_page_payload(authority, 'owner', 'page-e1')['capabilities']['collaborative_block_history'] is True
    actor['id'] = 'content-editor'
    assert client.get(endpoint, query_string={'operation_id':'op-ui-receipt-delete'}).get_json()['receipt'] is None
    actor['id'] = 'unknown'
    assert client.get(endpoint, query_string={'operation_id':'op-ui-receipt-delete'}).status_code == 404
