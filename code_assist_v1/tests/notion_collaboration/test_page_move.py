"""UI-MOVE-001: atomic parent, child-link, permission and durable replay effects."""
import base64
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
from types import SimpleNamespace

import pytest
from flask import Flask

from code_assist_v1.notion_collaboration import page_move
from code_assist_v1.notion_collaboration.canonical_authority import (
    EpochConflict, InjectedFailure, NotFoundOrDenied, OperationConflict,
)
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingAuthority, DocumentSequenceConflict
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.page_appearance import project_page_metadata
from code_assist_v1.notion_collaboration.protocol_contract import session_binding_sha256
from code_assist_v1.notion_collaboration.title_projection import _frames, _generation
from code_assist_v1.tests.notion_collaboration.test_collaborative_editing_authority import _environment, SUBJECTS, ORIGIN
from code_assist_v1.tests.notion_collaboration.test_page_appearance import snapshot
from code_assist_v1.tests.notion_collaboration.test_title_projection import append

DELTA = Path(page_move.__file__).with_name("page_move_delta.cjs")


def request(authority, parent="page-destination", operation="op-ui-move-0001", doc="page-child"):
    page = authority.read_document("owner", doc)
    return dict(parent_id=parent, operation_id=operation,
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"])


def environment(tmp_path):
    authority, transport, root, catalog, workspace = _environment(tmp_path)
    for doc, parent in (("page-destination", None), ("page-child", "page-e1"), ("page-grandchild", "page-child")):
        current = authority.read_document("owner", "page-e1")
        authority.create_document("owner", workspace, doc, title=doc,
            parent_document_id=parent, blocks=[{"id": f"body-{doc}", "type": "paragraph", "content": f"preserve {doc}"}],
            expected_workspace_epoch=current["workspace_epoch"], operation_id=f"op-create-{doc}")
    current = authority.read_document("owner", "page-e1")
    authority.execute_typed_command("owner", "page-e1", command_kind="block.insert", target_id="child-link-original",
        payload={"type":"child_page", "content":"page-child", "props":{"page_id":"page-child"}, "after_block_id":"block-b"},
        expected_document_sequence=current["document_sequence"], expected_workspace_epoch=current["workspace_epoch"],
        expected_document_epoch=current["document_epoch"], operation_id="op-create-original-link")
    return authority, transport, root, catalog, workspace


def current_body(authority, doc):
    with authority._reader() as con:
        frames = _frames(con, doc, _generation(con, doc))
    script = """
require(process.argv[1]); const Y=globalThis.NotionYjs;
const fs=require('node:fs'),d=new Y.Doc();
for(const f of JSON.parse(fs.readFileSync(0,'utf8')))Y.applyUpdate(d,Buffer.from(f,'base64'));
if(d.store.pendingStructs||d.store.pendingDs)throw Error('incomplete');
const blocks=d.getMap('blocks');process.stdout.write(JSON.stringify({title:d.getText('page-title').toString(),
blocks:d.getArray('block-order').toArray().map(id=>{const b=blocks.get(id);return {id,type:b.get('type'),content:b.get('content').toString(),props:JSON.parse(b.get('props'))};})}));
"""
    process = subprocess.run([shutil.which("node"), "-e", script, str(DELTA.parent.parent / "notion_static/vendor/yjs-13.6.32.bundle.js")],
        input=json.dumps([base64.b64encode(f).decode() for f in frames]).encode(), capture_output=True, timeout=15, check=True)
    return json.loads(process.stdout)


def test_ui_move_001_moves_links_and_replays_exact_receipt(tmp_path):
    authority, _, _, catalog, workspace = environment(tmp_path)
    before = snapshot(catalog)
    child = authority.read_document("owner", "page-child")
    grandchild = authority.read_document("owner", "page-grandchild")
    req = request(authority)
    result = page_move.move_page(authority, "owner", workspace, "page-child", **req)
    moved = authority.read_document("owner", "page-child")
    assert moved["parent_document_id"] == "page-destination"
    assert moved["blocks"] == child["blocks"]
    assert moved["document_epoch"] == child["document_epoch"] + 1
    assert authority.read_document("owner", "page-grandchild")["document_epoch"] == grandchild["document_epoch"] + 1
    assert [b["content"] for b in current_body(authority,"page-e1")["blocks"]] == ["A", "B"]
    destination = current_body(authority,"page-destination")
    assert destination["blocks"][0]["content"] == "preserve page-destination"
    assert destination["blocks"][1]["props"]["page_id"] == "page-child"
    stable = snapshot(catalog)
    assert page_move.move_page(authority,"owner",workspace,"page-child",**req) == result
    assert snapshot(catalog) == stable
    with pytest.raises(OperationConflict):
        page_move.move_page(authority,"owner",workspace,"page-child",**{**req,"parent_id":""})
    for table in ("workspace_collaboration_revisions", "workspace_collaboration_revision_checkpoints", "workspace_collaboration_client_ack_state"):
        assert before[table] == stable[table]
    returned = page_move.move_page(authority,"owner",workspace,"page-child",**request(authority,"page-e1","op-ui-move-back"))
    assert returned["parent_id"] == "page-e1"
    assert len([b for b in current_body(authority,"page-e1")["blocks"] if b["type"] == "child_page"]) == 1
    assert len(current_body(authority,"page-destination")["blocks"]) == 1
    # A receipt from before a later move is immutable; it cannot execute again.
    assert page_move.move_page(authority,"owner",workspace,"page-child",**req) == result


def test_ui_move_001_faults_roll_back_entire_multi_page_write(tmp_path):
    authority, _, _, catalog, workspace = environment(tmp_path)
    req = request(authority)
    before = snapshot(catalog)
    with sqlite3.connect(catalog) as con:
        workspace_before = con.execute("SELECT * FROM workspaces").fetchall()
    for fault in ("after_domain", "after_event", "after_audit", "after_outbox", "after_operation"):
        with pytest.raises(InjectedFailure):
            page_move.move_page(authority,"owner",workspace,"page-child",**req,fault=fault)
        assert snapshot(catalog) == before
        with sqlite3.connect(catalog) as con:
            assert con.execute("SELECT * FROM workspaces").fetchall() == workspace_before


def test_ui_move_001_denial_lock_cycle_and_stale_input(tmp_path):
    authority, _, _, catalog, workspace = environment(tmp_path)
    req = request(authority)
    before = snapshot(catalog)
    for actor in ("content-editor", "schema-editor"):
        with pytest.raises(NotFoundOrDenied):
            page_move.move_page(authority,actor,workspace,"page-child",**req)
    for parent in ("page-child", "page-grandchild"):
        with pytest.raises(ValueError):
            page_move.move_page(authority,"owner",workspace,"page-child",**{**req,"parent_id":parent})
    with pytest.raises(NotFoundOrDenied):
        page_move.move_page(authority,"owner","ws-wrong","page-child",**req)
    with pytest.raises(DocumentSequenceConflict):
        page_move.move_page(authority,"owner",workspace,"page-child",**{**req,"expected_document_sequence":999})
    with pytest.raises(EpochConflict):
        page_move.move_page(authority,"owner",workspace,"page-child",**{**req,"expected_workspace_epoch":999})
    assert snapshot(catalog) == before
    for locked in ("page-e1", "page-child", "page-destination"):
        with sqlite3.connect(catalog) as con:
            con.execute("UPDATE workspace_collaboration_documents SET settings_json=? WHERE id=?", ('{"locked":true}', locked))
        with pytest.raises(PermissionError):
            page_move.move_page(authority,"owner",workspace,"page-child",**req)
        with sqlite3.connect(catalog) as con:
            con.execute("UPDATE workspace_collaboration_documents SET settings_json='{}' WHERE id=?", (locked,))


def test_ui_move_001_revalidates_parent_after_node_preparation(tmp_path, monkeypatch):
    authority, _, _, catalog, workspace = environment(tmp_path)
    req = request(authority)
    original = page_move._prepare_deltas
    def concurrently_edit(*args):
        deltas = original(*args)
        with sqlite3.connect(catalog) as con:
            con.execute("UPDATE workspace_collaboration_documents SET row_version=row_version+1,document_sequence=document_sequence+1 WHERE id='page-destination'")
        return deltas
    monkeypatch.setattr(page_move,"_prepare_deltas",concurrently_edit)
    with pytest.raises(DocumentSequenceConflict):
        page_move.move_page(authority,"owner",workspace,"page-child",**req)
    assert authority.read_document("owner","page-child")["parent_document_id"] == "page-e1"
    with sqlite3.connect(catalog) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_crdt_updates").fetchone()[0] == 0


def test_ui_move_001_uses_live_yjs_body_and_preserves_other_types(tmp_path):
    authority, transport, _, _, workspace = environment(tmp_path)
    # Seed an actual editor document, then change rich-text leaves without changing the old structural snapshot.
    page = authority.read_document("owner","page-e1")
    script = """
const {transform}=require(process.argv[1]);const Y=globalThis.NotionYjs;
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const base=transform(input).update,d=new Y.Doc();Y.applyUpdate(d,Buffer.from(base,'base64'));
const b=d.getMap('blocks').get('block-a');b.set('type','heading1');b.get('content').insert(1,' 새 본문');
process.stdout.write(Buffer.from(Y.encodeStateAsUpdate(d)).toString('base64'));
"""
    process = subprocess.run([shutil.which("node"),"-e",script,str(DELTA)],
        input=json.dumps({"document_id":"page-e1","generation":1,"seed":page,"frames":[],"child_id":"unrelated","insert":None}).encode(),
        capture_output=True,timeout=15,check=True)
    append(authority,transport,workspace,[base64.b64decode(process.stdout)])
    page_move.move_page(authority,"owner",workspace,"page-child",**request(authority))
    blocks = current_body(authority,"page-e1")["blocks"]
    assert blocks[0]["type"] == "heading1"
    assert blocks[0]["content"] == "A 새 본문"
    assert len(blocks) == 2


def _move_worker(args):
    catalog, root, workspace, req = args
    authority = CollaborativeEditingAuthority.open(catalog, root, InMemoryIdentityDirectory(SUBJECTS))
    return page_move.move_page(authority,"owner",workspace,"page-child",**req)


def test_ui_move_001_cross_process_same_request_has_one_effect(tmp_path):
    authority, _, root, catalog, workspace = environment(tmp_path)
    args = (str(catalog), str(root), workspace, request(authority))
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(_move_worker, [args, args]))
    assert results[0] == results[1]
    assert len(current_body(authority,"page-destination")["blocks"]) == 2
    with sqlite3.connect(catalog) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations WHERE action=?", (page_move.ACTION,)).fetchone()[0] == 1


def test_ui_move_001_http_and_parent_metadata_use_canonical_store(tmp_path, monkeypatch):
    from code_assist_v1 import routes_notion_workspace as routes
    authority, _, _, _, workspace = environment(tmp_path)
    actor = {"id":"owner"}
    monkeypatch.setattr(routes,"_store",lambda:SimpleNamespace(iam=authority))
    monkeypatch.setattr(routes,"_collaborative_runtime_enabled",lambda:True)
    monkeypatch.setattr(routes,"current_user_id",lambda:actor["id"])
    app = Flask(__name__); app.config.update(TESTING=True,SECRET_KEY="synthetic-move")
    routes.register_notion_workspace_routes(app)
    client = app.test_client()
    url = f"/api/notion/workspaces/{workspace}/pages/page-child/move"
    req = request(authority)
    saved = client.post(url,json=req)
    assert saved.status_code == 200, saved.json
    assert saved.headers["Cache-Control"].startswith("no-store")
    assert client.post(url,json=req).json == saved.json
    projected = project_page_metadata(authority,"owner",workspace,[{"id":"page-child","parent_id":"wrong-legacy-parent"}])
    assert projected[0]["parent_id"] == "page-destination"
    actor["id"] = "content-editor"
    assert client.post(url,json=request(authority)).status_code in (403,404)
    actor["id"] = "owner"
    for extra in ({"title":"unexpected"},{"parent_id":False},{"expected_document_sequence":True}):
        assert client.post(url,json={**request(authority),**extra}).status_code == 400


def test_ui_move_001_inherited_access_changes_and_old_tickets_are_fenced(tmp_path):
    authority, _, _, _, workspace = environment(tmp_path)
    for doc, subject in (("page-e1","content-editor"),("page-destination","schema-editor")):
        page = authority.read_document("owner",doc)
        authority.set_page_grant("owner",doc,"subject",subject,role="view",inherit_to_descendants=True,
            expected_workspace_epoch=page["workspace_epoch"],expected_document_epoch=page["document_epoch"],
            operation_id=f"op-move-inherit-{subject}")
    assert authority.resolve_access("content-editor","page-grandchild").role == "view"
    with pytest.raises(NotFoundOrDenied):
        authority.resolve_access("schema-editor","page-grandchild")
    before = authority.resolve_access("owner","page-grandchild")
    page_move.move_page(authority,"owner",workspace,"page-child",**request(authority))
    with pytest.raises(NotFoundOrDenied):
        authority.resolve_access("content-editor","page-grandchild")
    assert authority.resolve_access("schema-editor","page-grandchild").role == "view"
    with authority._reader() as con:
        with pytest.raises(EpochConflict):
            authority._exact_access(con,"owner","page-grandchild",minimum_role="view",
                expected_workspace_epoch=before.workspace_epoch,expected_document_epoch=before.document_epoch)


def test_ui_move_001_archived_targets_are_not_movable(tmp_path):
    authority, _, _, catalog, workspace = environment(tmp_path)
    req = request(authority)
    for archived in ("page-child","page-e1","page-destination"):
        with sqlite3.connect(catalog) as con:
            con.execute("UPDATE workspace_collaboration_documents SET archived=1 WHERE id=?",(archived,))
        before = snapshot(catalog)
        with pytest.raises(NotFoundOrDenied):
            page_move.move_page(authority,"owner",workspace,"page-child",**req)
        assert snapshot(catalog) == before
        with sqlite3.connect(catalog) as con:
            con.execute("UPDATE workspace_collaboration_documents SET archived=0 WHERE id=?",(archived,))


def test_ui_move_001_root_noop_and_transport_replay(tmp_path):
    authority, transport, _, catalog, workspace = environment(tmp_path)
    moved = page_move.move_page(authority,"owner",workspace,"page-child",**request(authority,""))
    assert moved["parent_id"] == ""
    assert [b["content"] for b in current_body(authority,"page-e1")["blocks"]] == ["A","B"]
    before = snapshot(catalog)
    page_before = authority.read_document("owner","page-child")
    noop = page_move.move_page(authority,"owner",workspace,"page-child",**request(authority,"","op-move-root-noop"))
    assert noop["changed"] is False
    after = snapshot(catalog)
    page_after = authority.read_document("owner","page-child")
    assert page_after["document_sequence"] == page_before["document_sequence"] + 1
    for key in ("parent_document_id","blocks","updated_at","workspace_epoch","document_epoch","content_sha256"):
        assert page_after[key] == page_before[key]
    assert before["workspace_collaboration_crdt_updates"] == after["workspace_collaboration_crdt_updates"]
    args = dict(origin=ORIGIN,subject_id="owner",workspace_id=workspace,document_id="page-e1",
                client_id="client-move-read",session_binding_sha256=session_binding_sha256("session-move-read"))
    ticket = transport.issue_ticket(csrf_valid=True,capabilities=("read",),now_ms=1000,**args)
    grant = transport.consume_ticket(ticket.token,now_ms=1001,**args)
    replay = transport.replay(grant,after_document_sequence=0,limit=100)
    assert len(replay.events) == 1
    assert replay.events[0].client_id == "page-move-service"
    assert replay.events[0].payload
    assert replay.events[0].workspace_epoch == moved["workspace_epoch"]
