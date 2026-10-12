"""UI-PARENT-HISTORY: older structure during page/DB/row link operations."""
import pytest
import json
import base64
from pathlib import Path
import shutil
import subprocess
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable
from code_assist_v1.tests.notion_collaboration import test_page_link_parent_structure_rebase as pages
from code_assist_v1.tests.notion_collaboration import test_database_link_parent_structure_rebase as databases
from code_assist_v1.tests.notion_collaboration import test_row_page_parent_structure_rebase as rows
from code_assist_v1.tests.notion_collaboration.test_database_link_insert_rebase import (
    link_environment, activity_environment, environment, appearance_environment, body_environment,
    team_environment, file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment,
)
from code_assist_v1.tests.notion_collaboration.test_database_row_page_parent_insert_rebase import placement_environment, icon_batch_environment
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import text_update


def move_structure_behind_later_frame(store, ws, original):
    old = original['blocks'][0]
    text_update(store, ws, 'page-e1', old['id'], old['content'])


@pytest.mark.parametrize('action', ['move', 'copy', 'archive', 'restore'])
def test_ui_historical_page_lifecycle_preserves_body_order_and_exact_retry(environment, action, monkeypatch):
    store, ws = environment
    previous = pages.mixed_structure
    def historical(current):
        original = pages.body(current)
        previous(current)
        move_structure_behind_later_frame(current, ws, original)
    monkeypatch.setattr(pages, 'mixed_structure', historical)
    pages.test_ui_page_link_mixed_structural_edits_and_pending_note_retained(environment, action)


@pytest.mark.parametrize('action', ['move', 'copy', 'archive', 'restore'])
def test_ui_historical_database_lifecycle_and_linked_host_keep_current_settings(link_environment, action, monkeypatch):
    store, ws = link_environment
    previous = databases.mixed_structure
    def historical(current):
        original = databases.body(current)
        previous(current)
        move_structure_behind_later_frame(current, ws, original)
    monkeypatch.setattr(databases, 'mixed_structure', historical)
    previous_structural = databases.structural
    def host_historical(current, kind, identity, **options):
        result = previous_structural(current, kind, identity, **options)
        if options.get('document') and kind == 'move':
            # A later full replay materializes neither the missing insert nor the
            # older deletion/order. Retarget must use the host's own certificate.
            text_update(current, ws, options['document'], identity, '')
        return result
    monkeypatch.setattr(databases, 'structural', host_historical)
    databases.test_ui_database_link_and_external_host_keep_mixed_structural_edits(link_environment, action)


@pytest.mark.parametrize('direction', ['out', 'back'])
def test_ui_historical_row_page_parent_preserves_selected_order_and_retry(placement_environment, direction, monkeypatch):
    store, ws = placement_environment
    previous = rows.mixed_structure
    def historical(current):
        original = rows.body(current)
        previous(current)
        move_structure_behind_later_frame(current, ws, original)
    monkeypatch.setattr(rows, 'mixed_structure', historical)
    rows.test_ui_row_page_links_keep_mixed_pending_structural_edits(placement_environment, direction)


def test_ui_historical_database_archive_receipt_corruption_refuses_before_writer(link_environment):
    from code_assist_v1.tests.notion_collaboration.test_page_archive import database
    store, ws = link_environment
    databases.prepare_visible_notes(store)
    plan = databases.setup(store, ws, 'restore')
    databases.mixed_structure(store)
    move_structure_behind_later_frame(store, ws, plan['original'])
    plan['request'].update(databases.command_fences(store, 'restore'))
    # The archive event and its independent immutable operation receipt must
    # agree. Simulate corruption only in this temporary sanitized catalog.
    with database(store.db_path) as con:
        row = con.execute("SELECT result_json FROM workspace_collaboration_operations WHERE action='collaboration.database_trash'").fetchone()
        result = json.loads(row[0]); result['block_id'] = 'unrelated-block'
        con.execute('DROP TRIGGER trg_collab_operations_immutable_update')
        con.execute("UPDATE workspace_collaboration_operations SET result_json=? WHERE action='collaboration.database_trash'",
            (json.dumps(result),))
        con.commit()
    before = databases.dump(store)
    with pytest.raises(AuthorityUnavailable, match='database receipt differs'):
        plan['call']()
    assert databases.dump(store) == before


@pytest.mark.parametrize('mode', ['native', 'copy'])
def test_ui_historical_archived_database_cannot_reappear_without_a_typed_tombstone(link_environment, mode):
    from code_assist_v1.notion_collaboration import database_trash, page_copy_source
    from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
    from code_assist_v1.notion_collaboration.title_projection import _frames, _generation
    from code_assist_v1.tests.notion_collaboration.test_title_projection import append
    from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import owned_database_block
    from code_assist_v1.tests.notion_collaboration.test_page_copy_source import current_update, request
    from code_assist_v1.tests.notion_collaboration.test_native_page_creation import create
    store, ws = link_environment
    owned_database_block(store); current_update(store, ws)
    database_trash.set_owned_database_trashed(store.iam, 'owner', ws, 'page-e1', 'database-e1',
        archived=True, operation_id='history-ghost-archive', **databases.command_fences(store, 'archive'))
    original = databases.body(store)
    with store.iam._reader() as con:
        frames = _frames(con, 'page-e1', _generation(con, 'page-e1'))
        block = json.loads(con.execute("SELECT block_snapshot_json FROM workspace_collaboration_database_trash_state WHERE database_id='database-e1'").fetchone()[0])
        assert con.execute("SELECT 1 FROM workspace_collaboration_block_tombstones WHERE block_id=?", (block['id'],)).fetchone() is None
    script = r'''
const fs=require('node:fs');require(process.argv[1]);const Y=globalThis.NotionYjs;
const input=JSON.parse(fs.readFileSync(0,'utf8')),doc=new Y.Doc();
for(const frame of input.frames)Y.applyUpdate(doc,Buffer.from(frame,'base64'));
const vector=Y.encodeStateVector(doc),b=input.block,m=new Y.Map(),t=new Y.Text();
doc.getMap('blocks').set(b.id,m);m.set('type',b.type);m.set('props',JSON.stringify(b.props));m.set('content',t);
if(b.content)t.insert(0,b.content);doc.getArray('block-order').insert(0,[b.id]);
process.stdout.write(Buffer.from(Y.encodeStateAsUpdate(doc,vector)).toString('base64'));
'''
    result = subprocess.run([shutil.which('node'), '-e', script,
        str(Path(__file__).parents[2] / 'notion_static/vendor/yjs-13.6.32.bundle.js')],
        input=json.dumps({'frames': [base64.b64encode(frame).decode() for frame in frames], 'block': block}).encode(),
        capture_output=True, check=True, timeout=15)
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    append(store.iam, transport, ws, [base64.b64decode(result.stdout)], document='page-e1')
    assert any(item['id'] == block['id'] for item in databases.body(store)['blocks'])
    before = databases.dump(store)
    if mode == 'native':
        create(store, ws, operation='history-ghost-child')
        assert databases.body(store)['blocks'][:-1] == original['blocks']
    else:
        prepared = page_copy_source.prepare_page_copy(store.iam, 'owner', ws, 'page-e1', **request(store))
        assert prepared.documents[0].descriptor()['blocks'] == original['blocks']
        assert databases.dump(store) == before
