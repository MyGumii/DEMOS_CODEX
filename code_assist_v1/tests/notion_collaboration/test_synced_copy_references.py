"""UI-SYNC-COPY: current authorized originals, internal remap and external refs."""
import base64
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from code_assist_v1.notion_collaboration import synced_blocks as synced
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
from code_assist_v1.notion_collaboration.page_copy_remap import CopyShapeUnavailable
from code_assist_v1.tests.notion_collaboration.test_page_copy_commit import (
    environment, member_environment, native_environment, archive_environment, guest_environment, image_environment,
    copy, create, request, dump, registrations, insert_block,
)
from code_assist_v1.tests.notion_collaboration.test_title_projection import append
from code_assist_v1.tests.notion_collaboration.test_search_authority import _grant
from code_assist_v1.tests.notion_collaboration.test_guest_navigation import revoke


def insert_synced(store, document, identity, *, origin=None, content=""):
    props = {"synced_from": origin}
    store.iam.execute_typed_command("owner", document, command_kind="block.insert", target_id=identity,
        payload={"type": "synced_block", "content": content, "props": props, "after_block_id": None},
        operation_id="synced-insert-" + identity, **request(store, document))


def setup_pair(store, ws):
    insert_synced(store, "page-e1", "synced-original", content="canonical A")
    child = create(store, ws, operation="synced-instance-page")["page"]["id"]
    insert_synced(store, child, "synced-instance", origin={"document_id": "page-e1", "block_id": "synced-original"})
    return child


def text_update(store, ws, document, block_id, value):
    with store.iam._reader() as con:
        seed = store.iam._editing_snapshot(con, store.iam._document(con, document))
        frames = synced._frames(con, document, synced._generation(con, document))
    bundle = Path(__file__).parents[2] / "notion_static/vendor/yjs-13.6.32.bundle.js"
    script = r"""
const fs=require('node:fs');require(process.argv[1]);const Y=globalThis.NotionYjs;
const input=JSON.parse(fs.readFileSync(0,'utf8')),d=new Y.Doc();
for(const frame of input.frames)Y.applyUpdate(d,new Uint8Array(Buffer.from(frame,'base64')));
const vector=Y.encodeStateVector(d);
if(!d.share.has('block-order')){
  d.getArray('block-order').insert(0,input.seed.blocks.map(b=>b.id));
  for(const b of input.seed.blocks){const m=new Y.Map();d.getMap('blocks').set(b.id,m);
    m.set('type',b.type);m.set('props',JSON.stringify(b.props));
    const t=new Y.Text();m.set('content',t);t.insert(0,b.content);}
}
if(!d.getArray('block-order').toArray().includes(input.block))throw Error('Missing active source');
const t=d.getMap('blocks').get(input.block).get('content');t.delete(0,t.length);t.insert(0,input.value);
process.stdout.write(Buffer.from(Y.encodeStateAsUpdate(d,vector)).toString('base64'));
"""
    result = subprocess.run([shutil.which("node"), "-e", script, str(bundle)],
        input=json.dumps({"seed": seed, "frames": [base64.b64encode(frame).decode() for frame in frames],
            "block": block_id, "value": value}).encode(), capture_output=True, check=True, timeout=15)
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    append(store.iam, transport, ws, [base64.b64decode(result.stdout)], document=document)


def instance_block(store, document):
    return next(block for block in store.iam.read_document("owner", document)["blocks"]
        if block["type"] == "synced_block" and block["props"]["synced_from"] is not None)


def read_text(store, document, block="synced-instance", actor="owner"):
    return synced.read_synced_block(store.iam, actor, document, block)["block"]["content"]


def test_ui_synced_copy_internal_external_current_yjs_and_history(environment, monkeypatch):
    store, ws = environment
    child = setup_pair(store, ws)
    text_update(store, ws, "page-e1", "synced-original", "current A")
    opened = 0
    reader = store.iam._reader
    @contextmanager
    def tracked_reader():
        nonlocal opened
        with reader() as con:
            opened += 1
            try:
                yield con
            finally:
                opened -= 1
    monkeypatch.setattr(store.iam, "_reader", tracked_reader)
    materialize = synced.search_projection.materialize
    def outside(*args, **kwargs):
        assert opened == 0, "synced Yjs replay must run outside SQLite readers"
        return materialize(*args, **kwargs)
    monkeypatch.setattr(synced.search_projection, "materialize", outside)
    before = dump(store)
    assert read_text(store, child) == "current A"
    assert dump(store) == before

    full = copy(store, ws, operation="synced-full-copy")
    mapped = {row["source_document_id"]: row["document_id"] for row in registrations(store)}
    copied_child = mapped[child]
    copied_instance = instance_block(store, copied_child)
    endpoint = copied_instance["props"]["synced_from"]
    assert endpoint["document_id"] == full["page"]["id"]
    assert endpoint["block_id"] != "synced-original"
    assert copied_instance["content"] == ""
    assert copied_instance["props"]["sync_id"] == synced.group_id(**endpoint)
    assert read_text(store, copied_child, copied_instance["id"]) == "current A"

    external = copy(store, ws, child, operation="synced-external-copy")["page"]["id"]
    external_instance = instance_block(store, external)
    assert external_instance["props"]["synced_from"] == {"document_id": "page-e1", "block_id": "synced-original"}
    assert external_instance["content"] == ""
    snapshot = store.iam.get_revision("owner", external, 1)["snapshot"]
    assert "current A" not in json.dumps(snapshot), "an external reference copy must not store the original's text"

    text_update(store, ws, endpoint["document_id"], endpoint["block_id"], "copy B")
    assert read_text(store, copied_child, copied_instance["id"]) == "copy B"
    assert read_text(store, child) == read_text(store, external, external_instance["id"]) == "current A"
    text_update(store, ws, "page-e1", "synced-original", "external D")
    assert read_text(store, child) == read_text(store, external, external_instance["id"]) == "external D"
    assert read_text(store, copied_child, copied_instance["id"]) == "copy B"

    # History restores the original's current generation. Its instances keep
    # the identity and read restored content instead of a copied stale cache.
    target = endpoint["document_id"]
    page = store.iam.read_document("owner", target)
    store.iam.restore_revision("owner", target, 1, expected_revision=page["revision"],
        operation_id="synced-restore-original", **request(store, target))
    assert read_text(store, copied_child, copied_instance["id"]) == "current A"
    assert read_text(store, external, external_instance["id"]) == "external D"


@pytest.mark.parametrize("revoked", ["instance", "original", "identity"])
def test_ui_synced_reader_requires_both_current_grants_and_rechecks_revocation(environment, monkeypatch, revoked):
    store, ws = environment
    child = setup_pair(store, ws)
    with pytest.raises(NotFoundOrDenied):
        read_text(store, child, actor="outsider")  # Original access alone is insufficient.
    revoke(store, ws)  # The inherited test environment starts with a root view grant.
    _grant(store.iam, child, "outsider", "synced-instance-view")
    with pytest.raises(NotFoundOrDenied):
        read_text(store, child, actor="outsider")
    _grant(store.iam, "page-e1", "outsider", "synced-original-view")
    assert read_text(store, child, actor="outsider") == "canonical A"
    materialize, calls, after = synced.search_projection.materialize, 0, []
    def revoke_during_replay(*args, **kwargs):
        nonlocal calls
        result = materialize(*args, **kwargs)
        calls += 1
        if calls == 2:
            if revoked == "identity":
                original_active = store.iam.identity_directory.is_active_human
                monkeypatch.setattr(store.iam.identity_directory, "is_active_human",
                    lambda subject: subject != "outsider" and original_active(subject))
            else:
                document = child if revoked == "instance" else "page-e1"
                page = store.iam.read_document("owner", document)
                store.iam.revoke_page_grant("owner", document, "subject", "outsider",
                    expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"],
                    operation_id="synced-revoke-during-replay")
            after.extend(dump(store))
        return result
    monkeypatch.setattr(synced.search_projection, "materialize", revoke_during_replay)
    with pytest.raises(NotFoundOrDenied):
        read_text(store, child, actor="outsider")
    assert calls == 2 and dump(store) == tuple(after)


@pytest.mark.parametrize("changed", ["instance", "original"])
def test_ui_synced_reader_rejects_either_document_changing_during_replay(environment, monkeypatch, changed):
    store, ws = environment
    child = setup_pair(store, ws)
    materialize, calls, after = synced.search_projection.materialize, 0, []
    def change_during_replay(*args, **kwargs):
        nonlocal calls
        result = materialize(*args, **kwargs)
        calls += 1
        if calls == 2:
            insert_block(store, child if changed == "instance" else "page-e1", "synced-concurrent-" + changed)
            after.extend(dump(store))
        return result
    monkeypatch.setattr(synced.search_projection, "materialize", change_during_replay)
    with pytest.raises(synced.SyncedSourceChanged):
        read_text(store, child)
    assert calls == 2 and dump(store) == tuple(after)


def test_ui_synced_copy_and_reader_reject_cached_instances_and_reference_chains(environment):
    store, ws = environment
    child = setup_pair(store, ws)
    insert_synced(store, child, "synced-chain", origin={"document_id": child, "block_id": "synced-instance"})
    before = dump(store)
    with pytest.raises(synced.SyncedShapeUnavailable, match="original"):
        read_text(store, child, "synced-chain")
    with pytest.raises(CopyShapeUnavailable, match="original"):
        copy(store, ws)
    assert dump(store) == before
    insert_synced(store, child, "synced-stale-cache", content="stale private source text",
        origin={"document_id": "page-e1", "block_id": "synced-original"})
    before = dump(store)
    with pytest.raises(synced.SyncedShapeUnavailable, match="store original content"):
        read_text(store, child, "synced-stale-cache")
    with pytest.raises(CopyShapeUnavailable):
        copy(store, ws)
    assert dump(store) == before


def test_ui_synced_reader_budget_failure_does_not_start_replay(environment, monkeypatch):
    store, ws = environment
    child = setup_pair(store, ws)
    monkeypatch.setattr(synced, "MAX_SOURCE_BYTES", 1)
    def forbidden(*args):
        raise AssertionError("budget must be checked before Node")
    monkeypatch.setattr(synced.search_projection, "materialize", forbidden)
    before = dump(store)
    with pytest.raises(synced.SyncedShapeUnavailable, match="budget"):
        read_text(store, child)
    assert dump(store) == before
