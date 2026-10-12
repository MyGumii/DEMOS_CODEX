"""UI-SYNC-DB-EDIT: exact canonical body writes through authorized references."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json
import multiprocessing

import pytest

from code_assist_v1.notion_collaboration import synced_blocks as synced, synced_edit as edit, synced_database_edit as body_edit
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure, NotFoundOrDenied, OperationConflict, _canonical_json, _sha256
from code_assist_v1.notion_collaboration.page_settings import update_page_settings
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_read import (
    environment, body_environment, team_environment, file_environment, member_environment,
    native_environment, archive_environment, guest_environment, image_environment,
    row_body, insert, dump, _schema, _surface, request, original, reference,
)
from code_assist_v1.tests.notion_collaboration.test_page_copy_remap import block


def seed(store, kind="row", instance="page"):
    body = [original("edit-original"), block("unrelated", content="그대로", custom={"keep":[1,2]})]
    body[0]["props"]["children"][0]["props"].update({"text_color":"red","layout_group":"columns","layout_column":1})
    if kind == "row": row_body(store, body, "edit-row-seed")
    else: _schema(store.iam,"database.template.create","edit-template",{
        "name":"Template stays","body":body,"default_values":{}},"edit-template-seed")
    peer = reference("edit-peer","page-e1","edit-original")
    if instance == "page": insert(store,"edit-peer",peer)
    elif instance == "row":
        row_body(store,body+[peer] if kind == "row" else [peer],"edit-row-peer-seed")
    else: _schema(store.iam,"database.template.create","edit-peer-template",{
        "name":"Peer stays","body":[peer],"default_values":{}},"edit-peer-template-seed")
    return "edit-peer"


def payload(store, peer="edit-peer", operation="db-synced-text", actor="owner", updates=None):
    current = synced.read_synced_block(store.iam,actor,"page-e1",peer)
    return {"origin":current["origin"],"witnesses":current["witnesses"],"operation_id":operation,
        "updates":updates or [{"id":"edit-original-child","content":"수정한 자식 😀","rich_text_html":"<strong>수정한 자식 😀</strong>"}]}


def run(store, ws, value, actor="owner", **kw):
    return edit.edit_synced_text(store.iam,actor,ws,"page-e1","edit-peer",**value,**kw)


def snapshot(store):
    with store.iam._reader() as con:
        return store.iam._editing_snapshot(con,store.iam._document(con,"page-e1"))


def source_value(store,kind="row"):
    database = snapshot(store)["databases"][0]
    if kind == "row":
        value = next(row for row in database["rows"] if row["id"] == "row-e1")
        return value,value["body"]["blocks"]
    value = next(item for item in database["templates"] if item["id"] == "edit-template")
    return value,value["body"]


@pytest.mark.parametrize("kind",["row","template"])
@pytest.mark.parametrize("instance",["page","row","template"])
def test_exact_owner_body_only_changes_and_receipt_retry_has_no_crdt_ghost(environment,kind,instance):
    store,ws = environment; seed(store,kind,instance)
    before = snapshot(store); old_value,old_body = source_value(store,kind); value = payload(store)
    with store.iam._reader() as con:
        frames = con.execute("SELECT COUNT(*) FROM workspace_collaboration_crdt_updates").fetchone()[0]
        generation = synced._generation(con,"page-e1")
    result = run(store,ws,value)
    assert result["storage"] == "database_body" and result["location"]["kind"] == kind
    assert result["document_sequence"] == value["witnesses"][0]["document_sequence"]+1
    assert result["generation"] == generation
    after = dump(store); assert run(store,ws,value) == result and dump(store) == after
    new_value,new_body = source_value(store,kind)
    expected = deepcopy(old_body)
    expected[0]["props"]["children"][0]["content"] = "수정한 자식 😀"
    expected[0]["props"]["children"][0]["props"]["rich_text_html"] = "<strong>수정한 자식 😀</strong>"
    assert new_body == expected and new_value["version"] == old_value["version"]+1
    old_meta,new_meta = deepcopy(old_value),deepcopy(new_value)
    for item in (old_meta,new_meta): item.pop("version"); item.pop("body")
    assert new_meta == old_meta
    current = synced.read_synced_block(store.iam,"owner","page-e1","edit-peer")
    assert current["tree"]["children"][0]["content"] == "수정한 자식 😀"
    assert snapshot(store)["blocks"] == before["blocks"]
    location,blocks,peer = synced.locate_block(snapshot(store),"edit-peer")
    assert peer["content"] == "" and not peer["props"].get("children")
    with store.iam._reader() as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_crdt_updates").fetchone()[0] == frames
        assert synced._generation(con,"page-e1") == generation
        assert not con.execute("PRAGMA foreign_key_check").fetchall()
        doc = store.iam._document(con,"page-e1")
        assert doc["content_sha256"] == _sha256(_canonical_json(store.iam._editing_snapshot(con,doc)))
    changed = {**value,"updates":[dict(value["updates"][0],content="different request")]}
    with pytest.raises(OperationConflict): run(store,ws,changed)
    assert dump(store) == after


@pytest.mark.parametrize("fault",["after_domain","after_event","after_audit","after_outbox","after_operation"])
def test_database_body_writer_rolls_back_at_each_atomic_finish_stage(environment,fault):
    store,ws = environment; seed(store); before = dump(store)
    with pytest.raises(InjectedFailure): run(store,ws,payload(store),fault=fault)
    assert dump(store) == before


@pytest.mark.parametrize("kind,instance,allowed",[("row","page",True),("template","page",False),("row","template",False)])
def test_content_only_actor_cannot_edit_template_origin_or_instance(environment,kind,instance,allowed):
    store,ws = environment; seed(store,kind,instance)
    current = synced.read_synced_block(store.iam,"content-editor","page-e1","edit-peer")
    assert current["capabilities"]["edit_text"] is allowed
    value = payload(store); before = dump(store)
    if allowed: assert run(store,ws,value,actor="content-editor")["storage"] == "database_body"
    else:
        with pytest.raises(NotFoundOrDenied): run(store,ws,value,actor="content-editor")
        assert dump(store) == before


def test_locked_page_refuses_body_write_and_stale_witness_never_overwrites_new_body(environment):
    store,ws = environment; seed(store); value = payload(store)
    row_body(store,[original("edit-original","다른 입력")],"edit-newer-body")
    before = dump(store)
    with pytest.raises(synced.SyncedSourceChanged): run(store,ws,value)
    assert dump(store) == before
    value = payload(store)
    update_page_settings(store.iam,"owner",ws,"page-e1",updates={"locked":True},operation_id="db-edit-lock",**request(store))
    assert not synced.read_synced_block(store.iam,"owner","page-e1","edit-peer")["capabilities"]["edit_text"]
    before = dump(store)
    with pytest.raises(PermissionError): run(store,ws,value)
    assert dump(store) == before


def test_concurrent_body_or_template_change_after_preparation_refuses_commit(environment,monkeypatch):
    store,ws = environment; seed(store,"template"); value = payload(store); prepare = body_edit.prepare; after=[]
    def changed(*args):
        plan = prepare(*args)
        _schema(store.iam,"database.template.update","edit-template",{"name":"New name"},"edit-template-late-change",target_version=1)
        after.extend(dump(store)); return plan
    monkeypatch.setattr(body_edit,"prepare",changed)
    with pytest.raises(synced.SyncedSourceChanged): run(store,ws,value)
    assert dump(store) == tuple(after)


def test_independent_plan_validation_rejects_well_hashed_unrelated_body_change(environment,monkeypatch):
    store,ws = environment; seed(store); value = payload(store); prepare = body_edit.prepare
    def tampered(*args):
        plan = prepare(*args); body = json.loads(plan.body_json); body[1]["content"] = "unauthorized sibling rewrite"
        encoded = _canonical_json(body)
        return replace(plan,body_json=encoded,body_sha256=_sha256(encoded))
    monkeypatch.setattr(body_edit,"prepare",tampered); before = dump(store)
    with pytest.raises(synced.SyncedShapeUnavailable,match="prepared database body edit changed"): run(store,ws,value)
    assert dump(store) == before


@pytest.mark.parametrize("target",["unrelated","nested-peer","nested-hidden"])
def test_unrelated_and_nested_reference_leaves_are_not_owned_by_the_original(environment,target):
    store,ws = environment; seed(store)
    body = source_value(store)[1]
    nested = reference("nested-peer","outside-page","outside-original")
    nested["props"]["children"] = [block("nested-hidden",content="hidden cache")]
    body[0]["props"]["children"].append(nested)
    row_body(store,body,"edit-nested-boundary")
    value = payload(store,updates=[{"id":target,"content":"unsafe","rich_text_html":None}]); before = dump(store)
    with pytest.raises(synced.SyncedShapeUnavailable): run(store,ws,value)
    assert dump(store) == before


def test_body_size_limit_is_checked_before_writes_and_plain_code_rejects_rich_html(environment):
    store,ws = environment; seed(store)
    body = source_value(store)[1]; body[1]["content"] = "x"*1_040_000
    row_body(store,body,"edit-big-body")
    value = payload(store,updates=[{"id":"edit-original-child","content":"y"*50_000,"rich_text_html":None}]); before = dump(store)
    with pytest.raises(ValueError,match="too large"): run(store,ws,value)
    assert dump(store) == before
    body = [original("edit-original")]; body[0]["props"]["children"][0]["type"] = "code"
    row_body(store,body,"edit-code-body"); before = dump(store)
    with pytest.raises(ValueError,match="plain text block"): run(store,ws,payload(store))
    assert dump(store) == before


def test_prepare_and_replay_run_outside_readers_and_body_edit_never_calls_main_delta(environment,monkeypatch):
    store,ws = environment; seed(store); value=payload(store); opened=0
    reader,writer = store.iam._reader,store.iam._transaction
    @contextmanager
    def tracked(original):
        nonlocal opened
        with original() as con:
            opened+=1
            try: yield con
            finally: opened-=1
    monkeypatch.setattr(store.iam,"_reader",lambda:tracked(reader))
    monkeypatch.setattr(store.iam,"_transaction",lambda:tracked(writer))
    replay,prepare,validate = synced.search_projection.materialize,body_edit.prepare,body_edit.validate
    def outside(function):
        def call(*args,**kw): assert opened==0; return function(*args,**kw)
        return call
    monkeypatch.setattr(synced.search_projection,"materialize",outside(replay))
    monkeypatch.setattr(body_edit,"prepare",outside(prepare)); monkeypatch.setattr(body_edit,"validate",outside(validate))
    def forbidden(*args): raise AssertionError("database original cannot enter main-page Yjs delta")
    monkeypatch.setattr(edit,"_prepare",forbidden)
    assert run(store,ws,value)["storage"] == "database_body"


def test_body_text_event_namespace_does_not_collide_with_existing_surface_operation(environment):
    store,ws = environment; seed(store)
    row_body(store,source_value(store)[1],"shared-operation-name")
    result = run(store,ws,payload(store,operation="shared-operation-name"))
    assert result["operation_id"] != result["update_operation_id"]
    with store.iam._reader() as con:
        row = con.execute("SELECT operation_id,payload_json FROM workspace_collaboration_events WHERE id=?",(result["event_id"],)).fetchone()
        assert row["operation_id"] == result["update_operation_id"] == json.loads(row["payload_json"])["operation_id"]
        audit = con.execute("SELECT operation_id FROM workspace_collaboration_audit_events WHERE id=?",(result["audit_id"],)).fetchone()
        assert audit["operation_id"] == "shared-operation-name"


def _worker(catalog,root,ws,value,ready,start,barrier,results):
    store = CollaborativeWorkspaceStore(catalog,user_data_root=root,identity_directory=InMemoryIdentityDirectory(["owner"]))
    prepare = body_edit.prepare
    def together(*args):
        plan = prepare(*args); barrier.wait(timeout=30); return plan
    body_edit.prepare = together; ready.put(True)
    if not start.wait(30): results.put({"error":"start timeout"}); return
    try: results.put(run(store,ws,value))
    except Exception as exc: results.put({"error":type(exc).__name__,"message":str(exc)})


def test_two_process_body_text_edit_returns_one_receipt_and_advances_body_version_once(environment):
    store,ws = environment; seed(store); value = payload(store); old_version = source_value(store)[0]["version"]
    context=multiprocessing.get_context("spawn"); ready,results,start,barrier=context.Queue(),context.Queue(),context.Event(),context.Barrier(2)
    workers=[context.Process(target=_worker,args=(store.db_path,store.user_data_root,ws,value,ready,start,barrier,results)) for _ in range(2)]
    try:
        for worker in workers: worker.start()
        assert ready.get(timeout=30) and ready.get(timeout=30); start.set()
        receipts=[results.get(timeout=40),results.get(timeout=40)]
        for worker in workers: worker.join(30); assert worker.exitcode==0
        assert all("error" not in result for result in receipts),receipts
        assert receipts[0]==receipts[1] and source_value(store)[0]["version"]==old_version+1
        with store.iam._reader() as con:
            assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations WHERE action=?",(edit.ACTION,)).fetchone()[0]==1
            assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_crdt_updates").fetchone()[0]==0
    finally:
        start.set()
        for worker in workers:
            if worker.is_alive(): worker.terminate()
            worker.join(5)
        ready.close(); results.close()
