"""UI-PAGE-COPY source fidelity and the preparation/commit transaction boundary."""
import base64
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from code_assist_v1 import notion_store
from code_assist_v1.notion_collaboration import page_copy_source as copy_source
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, NotFoundOrDenied
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority, DocumentSequenceConflict
from code_assist_v1.tests.notion_collaboration.test_page_archive import (
    environment, guest_environment, image_environment, create, change, dump,
)
from code_assist_v1.tests.notion_collaboration.test_title_projection import append, yjs
from code_assist_v1.tests.notion_collaboration.test_file_authority import _begin, _stage_all


def request(store, doc="page-e1"):
    page = store.iam.read_document("owner", doc)
    return dict(expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"], expected_document_epoch=page["document_epoch"])


def prepare(store, ws, doc="page-e1", **kwargs):
    return copy_source.prepare_page_copy(store.iam, "owner", ws, doc, **(kwargs or request(store, doc)))


def insert_block(store, doc, block_id):
    current = request(store, doc)
    store.iam.execute_typed_command("owner", doc, command_kind="block.insert", target_id=block_id,
        payload={"type": "paragraph", "content": "concurrent content", "after_block_id": None},
        operation_id="copy-source-insert-" + block_id, **current)


def current_update(store, ws, *, clear=False, title="최신 협업 복제 제목"):
    with store.iam._reader() as con:
        original = store.iam._editing_snapshot(con, store.iam._document(con, "page-e1"))
    bundle = Path(__file__).parents[2] / "notion_static/vendor/yjs-13.6.32.bundle.js"
    script = r"""
const fs=require('node:fs');require(process.argv[1]);const Y=globalThis.NotionYjs;
const input=JSON.parse(fs.readFileSync(0,'utf8')),d=new Y.Doc();d.clientID=812341;
d.getText('page-title').insert(0,input.title);
const order=d.getArray('block-order'),blocks=d.getMap('blocks');
order.insert(0,input.snapshot.blocks.map(b=>b.id));
for(const [index,b] of input.snapshot.blocks.entries()){
  const item=new Y.Map();blocks.set(b.id,item);item.set('type',b.type);item.set('props',JSON.stringify(b.props));
  const text=new Y.Text();item.set('content',text);text.insert(0,index===0?'<strong>최신 본문</strong> '+b.id:b.content);
}
const frames=[Buffer.from(Y.encodeStateAsUpdate(d)).toString('base64')],vector=Y.encodeStateVector(d);
if(input.clear){d.getText('page-title').delete(0,d.getText('page-title').length);order.delete(0,order.length);
  frames.push(Buffer.from(Y.encodeStateAsUpdate(d,vector)).toString('base64'));}
process.stdout.write(JSON.stringify(frames));
"""
    result = subprocess.run([shutil.which("node"), "-e", script, str(bundle)],
        input=json.dumps({"snapshot": original, "clear": clear, "title": title}).encode(), capture_output=True, check=True, timeout=15)
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    append(store.iam, transport, ws, [base64.b64decode(item) for item in json.loads(result.stdout)])
    return original


def test_ui_copy_source_current_yjs_database_and_no_legacy_or_transaction_work(environment, monkeypatch):
    store, ws = environment
    create(store, ws, "copy-child")
    page = store.iam.read_document("owner", "page-e1")
    with store.iam._reader() as con:
        descriptor = store.iam._editing_snapshot(con, store.iam._document(con, "page-e1"))
    database = descriptor["databases"][0]
    row, prop = database["rows"][0], database["properties"][0]
    store.iam.execute_typed_command("owner", "page-e1", command_kind="database.cell.set",
        target_id=f'{row["id"]}:{prop["id"]}', payload={"row_id": row["id"], "property_id": prop["id"], "value": "최신 DB 값"},
        expected_target_version=row["cell_versions"].get(prop["id"], 0), operation_id="copy-current-database-value", **request(store))
    original = current_update(store, ws)
    before = dump(store)
    def forbidden(*args, **kwargs):
        raise AssertionError("copy preparation cannot read a legacy page")
    monkeypatch.setattr(notion_store, "get_page", forbidden)
    reader, decode, check_blobs = store.iam._reader, copy_source.search_projection.materialize, copy_source._verify_blobs
    opened = 0
    @contextmanager
    def tracked_reader():
        nonlocal opened
        with reader() as con:
            opened += 1
            try:
                yield con
            finally:
                opened -= 1
    def decode_outside_transaction(frames, **options):
        assert opened == 0
        return decode(frames, **options)
    def files_outside_transaction(authority, documents):
        assert opened == 0
        return check_blobs(authority, documents)
    monkeypatch.setattr(store.iam, "_reader", tracked_reader)
    monkeypatch.setattr(copy_source.search_projection, "materialize", decode_outside_transaction)
    monkeypatch.setattr(copy_source, "_verify_blobs", files_outside_transaction)
    prepared = prepare(store, ws)
    assert [item.document_id for item in prepared.documents] == ["page-e1", "copy-child"]
    current = prepared.documents[0].descriptor()
    assert current["title"] == "최신 협업 복제 제목" and current["title"] != page["title"]
    assert current["blocks"][0]["content"].startswith("<strong>최신 본문</strong>")
    assert current["databases"] == original["databases"]
    assert current["databases"][0]["rows"][0]["cells"][prop["id"]] == "최신 DB 값"
    current["title"] = "a remapping scratch value"
    assert prepared.documents[0].descriptor()["title"] == "최신 협업 복제 제목"
    assert dump(store) == before


def test_ui_copy_source_rejects_uncertified_typed_insert_missing_from_later_yjs_frame(environment, monkeypatch):
    from code_assist_v1.notion_collaboration import parent_historical_structure
    monkeypatch.setattr(parent_historical_structure, 'capture', lambda *args: None)
    from code_assist_v1.tests.notion_collaboration.test_synced_copy_references import text_update
    store, ws = environment
    current_update(store, ws)
    insert_block(store, 'page-e1', 'copy-stale-frame-note')
    first = next(block['id'] for block in store.iam.read_document('owner', 'page-e1')['blocks']
                 if block['id'] != 'copy-stale-frame-note')
    text_update(store, ws, 'page-e1', first, 'later collaborative text')
    before = dump(store)
    with pytest.raises(copy_source.CopySourceChanged, match='structures differ'):
        prepare(store, ws)
    assert dump(store) == before


def test_ui_copy_source_deleted_branch_roles_and_expected_sequence(environment, monkeypatch):
    store, ws = environment
    create(store, ws, "copy-active")
    create(store, ws, "copy-old-trash")
    create(store, ws, "copy-hidden-child", parent="copy-old-trash")
    stale = request(store)
    change(store, ws, "copy-old-trash", operation="copy-exclude-old-trash")
    prepared = prepare(store, ws)
    assert [item.document_id for item in prepared.documents] == ["page-e1", "copy-active"]
    with pytest.raises((DocumentSequenceConflict, copy_source.EpochConflict)):
        prepare(store, ws, **stale)
    with pytest.raises(NotFoundOrDenied):
        copy_source.prepare_page_copy(store.iam, "outsider", ws, "page-e1", **request(store))
    with pytest.raises(NotFoundOrDenied):
        prepare(store, ws, "copy-old-trash", **request(store))
    before = dump(store)
    monkeypatch.setattr(copy_source, "MAX_SOURCE_DOCUMENTS", 1)
    with pytest.raises(ValueError, match="subtree limit"):
        prepare(store, ws)
    assert dump(store) == before


def test_ui_copy_source_revalidates_after_replay_and_before_commit(environment, monkeypatch):
    store, ws = environment
    create(store, ws, "copy-child")
    prepared = prepare(store, ws, "copy-child")
    insert_block(store, "page-e1", "parent-concurrent-block")
    before = dump(store)
    with store.iam._reader() as con:
        with pytest.raises(ValueError, match="transaction"):
            copy_source.revalidate_copy_source(store.iam, con, prepared)
        con.execute("BEGIN")
        with pytest.raises(copy_source.CopySourceChanged, match="parent"):
            copy_source.revalidate_copy_source(store.iam, con, prepared)
    assert dump(store) == before
    prepared_root = prepare(store, ws)
    insert_block(store, "copy-child", "child-concurrent-block")
    before = dump(store)
    with store.iam._reader() as con:
        con.execute("BEGIN")
        with pytest.raises(copy_source.CopySourceChanged, match="content"):
            copy_source.revalidate_copy_source(store.iam, con, prepared_root)
    assert dump(store) == before
    decode = copy_source.search_projection.materialize
    created_during_replay = False
    def concurrent_child(frames, **options):
        nonlocal created_during_replay
        if not created_during_replay:
            created_during_replay = True
            create(store, ws, "copy-created-during-replay")
        return decode(frames, **options)
    monkeypatch.setattr(copy_source.search_projection, "materialize", concurrent_child)
    with pytest.raises(copy_source.CopySourceChanged, match="scope"):
        prepare(store, ws)
    assert store.iam.read_document("owner", "copy-created-during-replay")["parent_document_id"] == "page-e1"


def test_ui_copy_source_preserves_explicit_empty_yjs_body_and_title(environment):
    store, ws = environment
    current_update(store, ws, clear=True)
    before = dump(store)
    prepared = prepare(store, ws)
    assert prepared.documents[0].descriptor()["title"] == ""
    assert prepared.documents[0].descriptor()["blocks"] == []
    assert dump(store) == before


def test_ui_copy_source_title_only_frames_keep_the_existing_body(environment, yjs):
    store, ws = environment
    original = store.iam.read_document("owner", "page-e1")
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    append(store.iam, transport, ws, [yjs["base"], yjs["rename"]])
    before = dump(store)
    current = prepare(store, ws).documents[0].descriptor()
    assert current["title"] == "새 협업 제목"
    assert [block["content"] for block in current["blocks"]] == [block["content"] for block in original["blocks"]]
    assert dump(store) == before


def test_ui_copy_source_attachment_bytes_and_budget_fail_before_any_copy(environment, monkeypatch):
    store, ws = environment
    payload = b"independent-copy-attachment" * 16
    begun = _begin(store.iam, payload, "copy-source-file-begin")
    _stage_all(store.iam, begun, payload)
    uploaded = store.iam.finalize_upload("content-editor", begun["session_id"], operation_id="copy-source-file-finalize")
    before = dump(store)
    prepared = prepare(store, ws)
    files = json.loads(prepared.documents[0].files_json)
    assert [item["id"] for item in files] == [uploaded["file_id"]]
    assert prepared.documents[0].descriptor()["files"][0]["sha256"] == files[0]["content_sha256"]
    assert dump(store) == before
    path, _ = store.iam._blob_path(files[0]["content_sha256"])
    path.write_bytes(b"x" * len(payload))
    try:
        with pytest.raises(AuthorityUnavailable, match="attachment"):
            prepare(store, ws)
        assert dump(store) == before
    finally:
        path.write_bytes(payload)
    monkeypatch.setattr(copy_source, "MAX_SOURCE_BYTES", 1)
    with pytest.raises(ValueError, match="budget"):
        prepare(store, ws)
    assert dump(store) == before
