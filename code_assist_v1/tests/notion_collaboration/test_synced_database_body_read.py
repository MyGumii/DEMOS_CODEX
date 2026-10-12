"""UI-SYNC-DB-READ: canonical bodies, exact authority and mutation boundaries."""
from contextlib import contextmanager
from copy import deepcopy
import multiprocessing

import pytest

from code_assist_v1.notion_collaboration import synced_blocks as synced, synced_edit, page_copy
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied, InjectedFailure
from code_assist_v1.tests.notion_collaboration.test_synced_database_body_identity import original, reference
from code_assist_v1.tests.notion_collaboration.test_database_row_appearance import (
    environment, body_environment, team_environment, file_environment, member_environment,
    native_environment, archive_environment, guest_environment, image_environment,
    execute, body_request, dump,
)
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema, _surface
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import request
from code_assist_v1.tests.notion_collaboration.test_native_page_creation_http import client_for


def row_body(store, body, operation="db-sync-row-seed"):
    return execute(store.iam, body_request(store.iam, operation, payload={"blocks":body}))


def insert(store, identity, body):
    store.iam.execute_typed_command("owner", "page-e1", command_kind="block.insert", target_id=identity,
        payload={"type":body["type"], "content":body["content"], "props":body["props"], "after_block_id":None},
        operation_id="db-sync-insert-" + identity, **request(store))


@pytest.mark.parametrize("source_kind", ["row", "template"])
def test_main_reference_reads_exact_database_owned_original_tree_and_http_location(environment, monkeypatch, source_kind):
    store, ws = environment
    if source_kind == "row": row_body(store, [original("db-original"), original("unrelated")])
    else: _schema(store.iam, "database.template.create", "db-template", {
        "name":"Synced", "body":[original("db-original"), original("unrelated")], "default_values":{}}, "db-template-create")
    insert(store, "db-peer", reference("db-peer", "page-e1", "db-original"))
    before = dump(store)
    current = synced.read_synced_block(store.iam, "owner", "page-e1", "db-peer", workspace_id=ws)
    assert current["block"]["content"] == "공유 내용 😀"
    assert [child["id"] for child in current["tree"]["children"]] == ["db-original-child"]
    assert current["locations"]["instance"] == {"kind":"page"}
    expected = {"kind":source_kind, "database_id":"database-e1",
                "row_id" if source_kind == "row" else "template_id":"row-e1" if source_kind == "row" else "db-template"}
    assert current["locations"]["origin"] == expected and current["capabilities"]["edit_text"] is True
    client = client_for(store, monkeypatch)
    response = client.get(f"/api/notion/workspaces/{ws}/pages/page-e1/synced-blocks/db-peer")
    assert response.status_code == 200 and response.json == current
    assert "no-store" in response.headers["Cache-Control"] and dump(store) == before


def test_row_reference_reads_main_original_without_copying_content_to_item_storage(environment):
    store, ws = environment
    insert(store, "main-original", original("main-original", "일반 페이지 원본"))
    body = [reference("row-peer", "page-e1", "main-original")]
    row_body(store, body); before = dump(store)
    current = synced.read_synced_block(store.iam, "owner", "page-e1", "row-peer")
    assert current["block"]["content"] == "일반 페이지 원본"
    assert current["locations"]["instance"] == {"kind":"row", "database_id":"database-e1", "row_id":"row-e1"}
    assert current["locations"]["origin"] == {"kind":"page"}
    assert current["capabilities"]["edit_text"]
    row = next(row for row in store.iam.read_database("owner", "database-e1")["rows"] if row["id"] == "row-e1")
    assert row["body"]["blocks"][0]["content"] == "" and dump(store) == before


@pytest.mark.parametrize("endpoint", ["origin", "instance"])
def test_database_body_text_write_routes_to_its_exact_owner_without_ghost_blocks(environment, endpoint):
    store, ws = environment
    if endpoint == "origin":
        row_body(store, [original("db-original")]); insert(store, "db-peer", reference("db-peer", "page-e1", "db-original"))
        target = "db-original"
    else:
        insert(store, "db-original", original("db-original")); row_body(store, [reference("db-peer", "page-e1", "db-original")])
        target = "db-original"
    current = synced.read_synced_block(store.iam, "owner", "page-e1", "db-peer")
    before = store.iam.read_document("owner","page-e1")["blocks"]
    synced_edit.edit_synced_text(store.iam, "owner", ws, "page-e1", "db-peer", origin=current["origin"],
        witnesses=current["witnesses"], updates=[{"id":target,"content":"정확한 원본 수정","rich_text_html":""}],
        operation_id="db-sync-exact-owner-writer")
    assert synced.read_synced_block(store.iam,"owner","page-e1","db-peer")["block"]["content"] == "정확한 원본 수정"
    assert store.iam.read_document("owner","page-e1")["blocks"] == before
    with store.iam._reader() as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_crdt_updates").fetchone()[0] == (0 if endpoint == "origin" else 1)


def test_database_body_change_during_replay_is_fenced_without_read_side_writes(environment, monkeypatch):
    store, ws = environment
    row_body(store, [original("db-original")]); insert(store, "db-peer", reference("db-peer", "page-e1", "db-original"))
    replay = synced.search_projection.materialize; after = []
    def change(*args, **kwargs):
        result = replay(*args, **kwargs)
        row_body(store, [original("db-original", "동시 변경")], "db-sync-late-row-change")
        after.extend(dump(store)); return result
    with monkeypatch.context() as patch:
        patch.setattr(synced.search_projection, "materialize", change)
        with pytest.raises(synced.SyncedSourceChanged): synced.read_synced_block(store.iam, "owner", "page-e1", "db-peer")
    assert dump(store) == tuple(after)
    assert synced.read_synced_block(store.iam, "owner", "page-e1", "db-peer")["block"]["content"] == "동시 변경"


def test_archived_template_original_is_unavailable_and_exact_read_rechecks_late_archive(environment, monkeypatch):
    store, ws = environment
    _schema(store.iam, "database.template.create", "db-template", {
        "name":"Synced", "body":[original("db-original")], "default_values":{}}, "db-template-create")
    row_body(store, [reference("db-peer", "page-e1", "db-original")])
    replay = synced.search_projection.materialize; after = []
    def archive(*args, **kwargs):
        result = replay(*args, **kwargs)
        _schema(store.iam, "database.template.archive", "db-template", {}, "db-sync-late-template-archive", target_version=1)
        after.extend(dump(store)); return result
    with monkeypatch.context() as patch:
        patch.setattr(synced.search_projection, "materialize", archive)
        with pytest.raises(synced.SyncedSourceChanged): synced.read_synced_block(store.iam, "owner", "page-e1", "db-peer")
    assert dump(store) == tuple(after)
    with pytest.raises(NotFoundOrDenied): synced.read_synced_block(store.iam, "owner", "page-e1", "db-peer")


def test_database_read_replay_is_outside_sqlite_readers_and_wrong_workspace_never_replays(environment, monkeypatch):
    store, ws = environment; row_body(store, [original("db-original")])
    reader = store.iam._reader; opened = 0
    @contextmanager
    def tracked():
        nonlocal opened
        with reader() as con:
            opened += 1
            try: yield con
            finally: opened -= 1
    monkeypatch.setattr(store.iam, "_reader", tracked)
    replay = synced.search_projection.materialize; calls = []
    def outside(*args, **kwargs):
        assert opened == 0; calls.append(True); return replay(*args, **kwargs)
    monkeypatch.setattr(synced.search_projection, "materialize", outside)
    before = dump(store)
    assert synced.read_synced_block(store.iam, "owner", "page-e1", "db-original")["block"]["content"] == "공유 내용 😀"
    assert len(calls) == 1
    with pytest.raises(NotFoundOrDenied): synced.read_synced_block(store.iam, "owner", "page-e1", "db-original", workspace_id="wrong-workspace")
    with pytest.raises(NotFoundOrDenied): synced.read_synced_block(store.iam, "inactive-user", "page-e1", "db-original")
    assert len(calls) == 1 and dump(store) == before


def test_two_template_instances_share_the_template_original_without_stored_content(environment):
    store, ws = environment
    body = [original("template-original"), reference("template-peer", "page-e1", "template-original")]
    _schema(store.iam, "database.template.create", "db-template", {"name":"Synced", "body":body, "default_values":{}}, "db-template-create")
    for identity in ("db-sync-one", "db-sync-two"):
        _surface(store.iam, "database.row.create", identity, {"template_id":"db-template"},
                 "create-" + identity, target_version=0)
    rows = {row["id"]:row for row in store.iam.read_database("owner", "database-e1")["rows"]}
    ids = []
    for identity in ("db-sync-one", "db-sync-two"):
        blocks = rows[identity]["body"]["blocks"]; ids.append(blocks[0]["id"])
        for instance in blocks:
            result = synced.read_synced_block(store.iam, "owner", "page-e1", instance["id"])
            assert result["origin"]["block_id"] == "template-original"
            assert result["locations"]["origin"] == {"kind":"template", "database_id":"database-e1", "template_id":"db-template"}
            assert result["locations"]["instance"]["row_id"] == identity
            assert result["tree"]["children"][0]["content"] == "자식"
            assert instance["content"] == "" and not instance["props"].get("children")
    assert len(set(ids)) == 2 and "template-original" not in ids


def test_page_copy_commits_database_originals_and_recovers_exact_result_without_touching_source(environment):
    store, ws = environment
    row_body(store, [original("db-original"), reference("row-peer", "page-e1", "db-original")])
    _schema(store.iam, "database.template.create", "db-template", {"name":"Synced", "body":[original("template-original")],
                                                               "default_values":{}}, "db-template-create")
    source = deepcopy(store.iam.read_document("owner", "page-e1"))
    source_database = deepcopy(store.iam.read_database("owner", "database-e1"))
    args = dict(operation_id="db-sync-page-copy", **request(store))
    result = page_copy.copy_page(store.iam, "owner", ws, "page-e1", **args)
    after = dump(store)
    replayed = page_copy.copy_page(store.iam, "owner", ws, "page-e1", **args)
    assert replayed["replayed"] is True and result["replayed"] is False
    assert {key:value for key,value in replayed.items() if key != "replayed"} == {
        key:value for key,value in result.items() if key != "replayed"}
    assert dump(store) == after
    copied = result["page"]["id"]
    with store.iam._reader() as con:
        database = store.iam._editing_snapshot(con, store.iam._document(con, copied))["databases"][0]
    original_block, peer = database["rows"][0]["body"]["blocks"]
    current = synced.read_synced_block(store.iam, "owner", copied, peer["id"])
    assert current["origin"] == {"document_id":copied, "block_id":original_block["id"]}
    assert current["block"]["content"] == "공유 내용 😀"
    assert current["locations"]["origin"]["database_id"] == database["id"]
    assert store.iam.read_document("owner", "page-e1") == source
    assert store.iam.read_database("owner", "database-e1") == source_database
    with store.iam._reader() as con: assert not con.execute("PRAGMA foreign_key_check").fetchall()


def test_invalid_template_instantiation_rolls_back_row_receipt_and_document_version(environment):
    store, ws = environment
    malformed = original("legacy-original"); malformed["props"].pop("synced_from")
    _schema(store.iam, "database.template.create", "legacy-template", {"name":"Legacy", "body":[malformed],
                                                                   "default_values":{}}, "db-legacy-template-create")
    before = dump(store)
    with pytest.raises(synced.SyncedShapeUnavailable, match="explicit original"):
        _surface(store.iam, "database.row.create", "should-not-exist", {"template_id":"legacy-template"},
                 "legacy-row-create", target_version=0)
    assert dump(store) == before


def test_database_body_copy_rolls_back_all_canonical_effects_after_domain_failure(environment):
    store, ws = environment
    row_body(store, [original("db-original"), reference("row-peer", "page-e1", "db-original")])
    before = dump(store)
    with pytest.raises(InjectedFailure):
        page_copy.copy_page(store.iam, "owner", ws, "page-e1", operation_id="db-body-copy-fault",
                            fault="after_domain", **request(store))
    assert dump(store) == before


def test_two_process_database_body_copy_creates_one_original_and_one_reference(environment):
    from code_assist_v1.tests.notion_collaboration.test_page_copy_commit import _copy_process, registrations
    store, ws = environment
    row_body(store, [original("db-original"), reference("row-peer", "page-e1", "db-original")])
    context = multiprocessing.get_context("spawn")
    ready, results, start, barrier = context.Queue(), context.Queue(), context.Event(), context.Barrier(2)
    workers = [context.Process(target=_copy_process, args=(store.db_path, store.user_data_root, ws,
        request(store), ready, start, barrier, results)) for _ in range(2)]
    try:
        for worker in workers: worker.start()
        assert ready.get(timeout=30) and ready.get(timeout=30); start.set()
        receipts = [results.get(timeout=40), results.get(timeout=40)]
        for worker in workers:
            worker.join(30); assert worker.exitcode == 0
        assert all("error" not in value for value in receipts), receipts
        assert sorted(value["replayed"] for value in receipts) == [False, True]
        assert {**receipts[0],"replayed":False} == {**receipts[1],"replayed":False}
        assert len(registrations(store)) == 1
        copied = receipts[0]["page"]["id"]
        with store.iam._reader() as con:
            blocks = store.iam._editing_snapshot(con,store.iam._document(con,copied))["databases"][0]["rows"][0]["body"]["blocks"]
        assert len(blocks) == 2
        assert synced.read_synced_block(store.iam,"owner",copied,blocks[1]["id"])["origin"]["block_id"] == blocks[0]["id"]
    finally:
        start.set()
        for worker in workers:
            if worker.is_alive(): worker.terminate()
            worker.join(5)
        ready.close(); results.close()
