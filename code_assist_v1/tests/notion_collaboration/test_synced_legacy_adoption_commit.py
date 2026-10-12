"""UI-SYNC-LEGACY-COMMIT: complete atomic selection, durable replay and retry."""
from contextlib import contextmanager
from dataclasses import replace
import base64
import json
import multiprocessing
from pathlib import Path
import shutil
import subprocess

import pytest

from code_assist_v1.notion_collaboration import search_projection, synced_blocks as synced
from code_assist_v1.notion_collaboration import synced_legacy_adoption as adoption
from code_assist_v1.notion_collaboration import synced_legacy_adoption_commit as commit
from code_assist_v1.notion_collaboration.canonical_authority import InjectedFailure, NotFoundOrDenied, OperationConflict, _canonical_json, _sha256
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
from code_assist_v1.notion_collaboration.page_settings import update_page_settings
from code_assist_v1.tests.notion_collaboration.test_synced_legacy_adoption import (
    environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, insert_legacy, setup_selection, endpoint,
    prepare, request, dump, text_update,
)
from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import copy, registrations
from code_assist_v1.tests.notion_collaboration.test_title_projection import append


def prepared(store, ws, child):
    return commit.prepare_adoption_delta(store.iam, prepare(store, ws, child))


def inspect(store, pending):
    result = {}
    with store.iam._reader() as con:
        con.execute("BEGIN")
        for item in pending.adoption.documents:
            document = item.source.document_id
            page = store.iam._document(con, document)
            snapshot = store.iam._editing_snapshot(con, page)
            frames = synced._frames(con, document, synced._generation(con, document))
            result[document] = {"page": dict(page), "snapshot": snapshot, "frames": frames,
                "rows": [dict(row) for row in con.execute("SELECT * FROM workspace_collaboration_blocks WHERE document_id=? ORDER BY position,id", (document,))]}
        counts = {name: con.execute("SELECT COUNT(*) FROM " + name).fetchone()[0] for name in (
            "workspace_collaboration_operations", "workspace_collaboration_events",
            "workspace_collaboration_revisions", "workspace_collaboration_audit_events", "workspace_collaboration_outbox")}
    return result, counts


def test_ui_legacy_commit_preserves_resources_and_unselected_rows_and_copy_uses_explicit_origin(environment, monkeypatch):
    store, ws = environment
    child = setup_selection(store, ws)
    pending = prepared(store, ws, child)
    before, counts_before = inspect(store, pending)
    def forbidden(*args, **kwargs):
        raise AssertionError("commit cannot replay Yjs or prepare Node updates")
    with monkeypatch.context() as frozen:
        frozen.setattr(commit, "_prepare_updates", forbidden)
        frozen.setattr(search_projection, "materialize", forbidden)
        result = commit.commit_prepared_adoption(store.iam, pending, operation_id="legacy-atomic-choice")
    after, counts_after = inspect(store, pending)
    assert result["replayed"] is False
    assert result["origin"] == endpoint("page-e1", "chosen-original")
    assert result["references"] == [endpoint(child, "chosen-instance")]
    assert result["preparation_sha256"] == pending.adoption.payload_sha256
    frame_count = sum(len(frames) for _doc, frames in pending.updates)
    assert counts_after["workspace_collaboration_revisions"] == counts_before["workspace_collaboration_revisions"] + 2
    for name in ("workspace_collaboration_operations", "workspace_collaboration_events", "workspace_collaboration_audit_events", "workspace_collaboration_outbox"):
        assert counts_after[name] == counts_before[name] + frame_count + 2
    for item in pending.adoption.documents:
        doc = item.source.document_id
        for field in ("title", "icon", "cover", "settings", "databases", "files"):
            assert after[doc]["snapshot"][field] == before[doc]["snapshot"][field]
        for old, row in zip(before[doc]["rows"], after[doc]["rows"]):
            assert row["id"] == old["id"] and row["position"] == old["position"] and row["created_at"] == old["created_at"]
            if row["id"] not in {"chosen-original", "chosen-instance"}:
                assert row == old
        expected = [{key: value for key, value in block.items() if key != "position"} for block in item.blocks()]
        assert search_projection.materialize([after[doc]["frames"]])[0]["blocks"] == expected
        saved = next(value for value in result["documents"] if value["document_id"] == doc)
        assert saved["content_sha256"] == _sha256(_canonical_json(after[doc]["snapshot"]))
        assert after[doc]["page"]["revision"] == before[doc]["page"]["revision"] + 1
        assert after[doc]["page"]["document_sequence"] == before[doc]["page"]["document_sequence"] + saved["update_frames"] + 1
    assert synced.read_synced_block(store.iam, "owner", child, "chosen-instance")["block"]["content"] == "조합된 한글 A"
    stable = dump(store)
    assert commit.commit_prepared_adoption(store.iam, pending, operation_id="legacy-atomic-choice")["replayed"] is True
    assert dump(store) == stable
    copied = copy(store, ws, operation="copy-explicitly-adopted-group")
    mapping = {row["source_document_id"]: row["document_id"] for row in registrations(store)}
    copied_child = mapping[child]
    instance = next(block for block in store.iam.read_document("owner", copied_child)["blocks"] if block["type"] == "synced_block")
    assert instance["props"]["synced_from"]["document_id"] == copied["page"]["id"]
    assert synced.read_synced_block(store.iam, "owner", copied_child, instance["id"])["block"]["content"] == "조합된 한글 A"
    text_update(store, ws, "page-e1", "chosen-original", "원본의 다음 문장")
    assert synced.read_synced_block(store.iam, "owner", child, "chosen-instance")["block"]["content"] == "원본의 다음 문장"
    assert synced.read_synced_block(store.iam, "owner", copied_child, instance["id"])["block"]["content"] == "조합된 한글 A"
    stable = dump(store)
    assert commit.commit_prepared_adoption(store.iam, pending, operation_id="legacy-atomic-choice")["replayed"] is True
    assert dump(store) == stable, "receipt recovery must precede stale legacy preparation"
    assert store.capabilities()["collaborative_page_copy"] is False


def test_ui_legacy_commit_late_failure_rolls_back_complete_selection_and_retry_is_exact(environment):
    store, ws = environment
    child = setup_selection(store, ws)
    pending = prepared(store, ws, child)
    alternate = adoption.prepare_legacy_adoption(store.iam, "owner", ws,
        origin=endpoint(child, "chosen-instance"), references=[endpoint("page-e1", "chosen-original")])
    alternate_delta = commit.prepare_adoption_delta(store.iam, alternate)
    before = dump(store)
    with pytest.raises(InjectedFailure):
        commit.commit_prepared_adoption(store.iam, pending, operation_id="legacy-retry", fault="after_outbox")
    assert dump(store) == before
    corrupted = replace(pending, updates=((pending.updates[0][0], (b"different-update",)), *pending.updates[1:]))
    with pytest.raises(ValueError, match="immutable"):
        commit.commit_prepared_adoption(store.iam, corrupted, operation_id="legacy-tamper")
    assert dump(store) == before
    saved = commit.commit_prepared_adoption(store.iam, pending, operation_id="legacy-retry")
    after = dump(store)
    repeated = commit.commit_prepared_adoption(store.iam, pending, operation_id="legacy-retry")
    assert {**repeated, "replayed": False} == saved
    assert dump(store) == after
    with pytest.raises(OperationConflict):
        commit.commit_prepared_adoption(store.iam, alternate_delta, operation_id="legacy-retry")
    assert dump(store) == after


@pytest.mark.parametrize("failure", ["original_changed", "instance_changed", "identity_revoked", "instance_locked"])
def test_ui_legacy_commit_revalidates_every_endpoint_before_writes(environment, monkeypatch, failure):
    store, ws = environment
    child = setup_selection(store, ws)
    pending = prepared(store, ws, child)
    expected_error = synced.SyncedSourceChanged
    if failure.endswith("changed"):
        document = "page-e1" if failure == "original_changed" else child
        store.iam.execute_typed_command("owner", document, command_kind="block.insert", target_id="changed-legacy-note",
            payload={"type": "paragraph", "content": "must survive", "after_block_id": None},
            operation_id="legacy-changed-before-commit", **request(store, document))
    elif failure == "identity_revoked":
        active = store.iam.identity_directory.is_active_human
        monkeypatch.setattr(store.iam.identity_directory, "is_active_human", lambda subject: subject != "owner" and active(subject))
        expected_error = NotFoundOrDenied
    else:
        update_page_settings(store.iam, "owner", ws, child, updates={"locked": True},
            operation_id="legacy-lock-before-commit", **request(store, child))
        expected_error = PermissionError
    before = dump(store)
    with pytest.raises(expected_error):
        commit.commit_prepared_adoption(store.iam, pending, operation_id="legacy-changed-commit")
    assert dump(store) == before


@pytest.mark.parametrize("reference_count", [0, 2])
def test_ui_legacy_commit_single_document_and_explicit_singleton(environment, reference_count):
    store, ws = environment
    insert_legacy(store, "page-e1", "chosen-original")
    refs = []
    for index in range(reference_count):
        identity = "same-page-ref-" + str(index)
        insert_legacy(store, "page-e1", identity)
        refs.append(endpoint("page-e1", identity))
    selection = adoption.prepare_legacy_adoption(store.iam, "owner", ws,
        origin=endpoint("page-e1", "chosen-original"), references=list(reversed(refs)))
    pending = commit.prepare_adoption_delta(store.iam, selection)
    saved = commit.commit_prepared_adoption(store.iam, pending, operation_id="legacy-single-document")
    assert len(saved["documents"]) == 1
    for ref in refs:
        assert synced.read_synced_block(store.iam, "owner", ref["document_id"], ref["block_id"])["block"]["content"] == "조합된 한글 A"
    assert synced.read_synced_block(store.iam, "owner", "page-e1", "chosen-original")["origin"] == saved["origin"]
    stable = dump(store)
    assert commit.commit_prepared_adoption(store.iam, pending, operation_id="legacy-single-document")["replayed"] is True
    assert dump(store) == stable


def test_ui_legacy_commit_preserves_current_yjs_and_later_canonical_insert(environment):
    store, ws = environment
    child = setup_selection(store, ws)
    for document, block in (("page-e1", "chosen-original"), (child, "chosen-instance")):
        text_update(store, ws, document, block, "Yjs에서만 최신인 문장")
    store.iam.execute_typed_command("owner", "page-e1", command_kind="block.insert", target_id="post-frame-legacy-note",
        payload={"type": "paragraph", "content": "later canonical insertion", "after_block_id": "chosen-original"},
        operation_id="legacy-post-frame-insert", **request(store))
    pending = prepared(store, ws, child)
    saved = commit.commit_prepared_adoption(store.iam, pending, operation_id="legacy-current-body")
    after, _counts = inspect(store, pending)
    projected = search_projection.materialize([after["page-e1"]["frames"]])[0]
    assert next(block for block in projected["blocks"] if block["id"] == "post-frame-legacy-note")["content"] == "later canonical insertion"
    assert next(block for block in after["page-e1"]["snapshot"]["blocks"] if block["id"] == "post-frame-legacy-note")["content"] == "later canonical insertion"
    assert synced.read_synced_block(store.iam, "owner", child, "chosen-instance")["block"]["content"] == "Yjs에서만 최신인 문장"
    assert saved["replayed"] is False


@pytest.mark.parametrize("empty_title", [False, True])
def test_ui_legacy_commit_history_keeps_current_durable_title_including_empty(environment, empty_title):
    store, ws = environment
    child = setup_selection(store, ws)
    # Rename the existing title lane, rather than append a competing independent
    # title stream whose deletion cannot remove another client's old characters.
    text_update(store, ws, "page-e1", "chosen-original", "조합된 한글 A")
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    expected = "" if empty_title else "새 협업 제목"
    with store.iam._reader() as con:
        assert store.iam._document(con, "page-e1")["title"] != expected
        existing = synced._frames(con, "page-e1", synced._generation(con, "page-e1"))
    bundle = Path(__file__).parents[2] / "notion_static/vendor/yjs-13.6.32.bundle.js"
    script = r"""
const fs=require('node:fs');require(process.argv[1]);const Y=globalThis.NotionYjs;
const input=JSON.parse(fs.readFileSync(0,'utf8')),d=new Y.Doc();
for(const frame of input.frames)Y.applyUpdate(d,new Uint8Array(Buffer.from(frame,'base64')));
const vector=Y.encodeStateVector(d),title=d.getText('page-title');
title.delete(0,title.length);if(input.title)title.insert(0,input.title);
process.stdout.write(Buffer.from(Y.encodeStateAsUpdate(d,vector)).toString('base64'));
"""
    renamed = subprocess.run([shutil.which("node"), "-e", script, str(bundle)],
        input=json.dumps({"frames": [base64.b64encode(frame).decode("ascii") for frame in existing],
                          "title": expected}).encode("utf-8"), capture_output=True, check=True, timeout=15)
    append(store.iam, transport, ws, [base64.b64decode(renamed.stdout)])
    with store.iam._reader() as con:
        current_frames = synced._frames(con, "page-e1", synced._generation(con, "page-e1"))
        assert store.iam._document(con, "page-e1")["title"] != expected
    assert search_projection.materialize([current_frames])[0]["title"] == expected
    pending = prepared(store, ws, child)
    result = commit.commit_prepared_adoption(store.iam, pending, operation_id="legacy-current-title-history")
    origin_receipt = next(item for item in result["documents"] if item["document_id"] == "page-e1")
    current = store.iam.read_document("owner", "page-e1")
    history = store.iam.get_revision("owner", "page-e1", origin_receipt["revision"])["snapshot"]
    assert current["title"] == history["title"] == expected
    with store.iam._reader() as con:
        durable = synced._frames(con, "page-e1", synced._generation(con, "page-e1"))
    assert search_projection.materialize([durable])[0]["title"] == expected
    assert synced.read_synced_block(store.iam, "owner", child, "chosen-instance")["block"]["content"] == "조합된 한글 A"


def test_ui_legacy_delta_budget_refusal_precedes_independent_replay(environment, monkeypatch):
    store, ws = environment
    child = setup_selection(store, ws)
    selection = prepare(store, ws, child)
    before = dump(store)
    monkeypatch.setattr(commit, "MAX_SOURCE_BYTES", 1)
    def forbidden(*args, **kwargs):
        raise AssertionError("oversized prepared updates must fail before replay")
    monkeypatch.setattr(search_projection, "materialize", forbidden)
    with pytest.raises(synced.SyncedShapeUnavailable, match="budget"):
        commit.prepare_adoption_delta(store.iam, selection)
    assert dump(store) == before


def _adopt_process(catalog, data_root, pending, ready, start, barrier, results):
    from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
    from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
    store = CollaborativeWorkspaceStore(catalog, user_data_root=data_root, identity_directory=InMemoryIdentityDirectory(["owner"]))
    transaction = store.iam._transaction
    @contextmanager
    def both_saw_no_receipt():
        barrier.wait(timeout=40)
        with transaction() as con:
            yield con
    store.iam._transaction = both_saw_no_receipt
    ready.put(True)
    if not start.wait(40):
        results.put({"error": "start timeout"})
        return
    try:
        results.put(commit.commit_prepared_adoption(store.iam, pending, operation_id="legacy-two-process"))
    except Exception as exc:
        results.put({"error": type(exc).__name__, "message": str(exc)})


def test_ui_legacy_commit_two_processes_adopt_all_endpoints_once(environment):
    store, ws = environment
    child = setup_selection(store, ws)
    pending = prepared(store, ws, child)
    before, counts_before = inspect(store, pending)
    context = multiprocessing.get_context("spawn")
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    barrier = context.Barrier(2)
    workers = [context.Process(target=_adopt_process, args=(store.db_path, store.user_data_root, pending, ready, start, barrier, results)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        assert ready.get(timeout=40) and ready.get(timeout=40)
        start.set()
        receipts = [results.get(timeout=50), results.get(timeout=50)]
        for worker in workers:
            worker.join(30)
            assert worker.exitcode == 0
        assert all("error" not in receipt for receipt in receipts), receipts
        assert sorted(receipt["replayed"] for receipt in receipts) == [False, True]
        assert {**receipts[0], "replayed": False} == {**receipts[1], "replayed": False}
        after, counts_after = inspect(store, pending)
        assert counts_after["workspace_collaboration_revisions"] == counts_before["workspace_collaboration_revisions"] + 2
        assert synced.read_synced_block(store.iam, "owner", child, "chosen-instance")["block"]["content"] == "조합된 한글 A"
        for document in after:
            assert after[document]["page"]["revision"] == before[document]["page"]["revision"] + 1
    finally:
        start.set()
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(5)
        ready.close()
        results.close()
