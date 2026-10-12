"""UI-R5: Trash hides exclusive DB files and preserves shared file access."""

import base64
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

from code_assist_v1 import notion_workspace_database_trash_migration as migration
from code_assist_v1.notion_collaboration import database_trash, page_copy
from code_assist_v1.notion_collaboration.database_trash import DatabaseTrashShapeUnavailable
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied, _canonical_json
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.page_copy_source import (
    CopySourceDocument, _exclude_archived_row_only_files,
)
from code_assist_v1.notion_collaboration.title_projection import _frames, _generation
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import activity_environment
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema
from code_assist_v1.tests.notion_collaboration.test_database_move_commit import _upload_png
from code_assist_v1.tests.notion_collaboration.test_database_row_content import _apply
from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import (
    environment, appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment,
    request as command_request, upload,
)
from code_assist_v1.tests.notion_collaboration.test_title_projection import append


def _append_live_block_props(store, workspace_id, block_id, props):
    with store.iam._reader() as con:
        document = store.iam._document(con, "page-e1")
        seed = store.iam._editing_snapshot(con, document)
        frames = _frames(con, document["id"], _generation(con, document["id"]))
    script = r"""
const fs=require('node:fs');require(process.argv[1]);const Y=globalThis.NotionYjs;
const input=JSON.parse(fs.readFileSync(0,'utf8')),d=new Y.Doc();
for(const frame of input.frames)Y.applyUpdate(d,new Uint8Array(Buffer.from(frame,'base64')));
const vector=Y.encodeStateVector(d);
if(!d.share.has('block-order')){
  d.getArray('block-order').insert(0,input.seed.blocks.map(b=>b.id));
  for(const b of input.seed.blocks){const m=new Y.Map();d.getMap('blocks').set(b.id,m);
    m.set('type',b.type);m.set('props',JSON.stringify(b.props));
    const t=new Y.Text();m.set('content',t);if(b.content)t.insert(0,b.content);}
}
d.getMap('blocks').get(input.block_id).set('props',JSON.stringify(input.props));
process.stdout.write(Buffer.from(Y.encodeStateAsUpdate(d,vector)).toString('base64'));
"""
    bundle = Path(__file__).parents[2] / "notion_static/vendor/yjs-13.6.32.bundle.js"
    prepared = subprocess.run([shutil.which("node"), "-e", script, str(bundle)],
        input=json.dumps({"seed": seed, "frames": [base64.b64encode(frame).decode()
            for frame in frames], "block_id": block_id, "props": props}).encode(),
        capture_output=True, check=True, timeout=15)
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    append(store.iam, transport, workspace_id, [base64.b64decode(prepared.stdout)],
        document="page-e1")


def test_ui_r5_database_trash_files_exclusive_shared_and_restore(activity_environment):
    store, workspace_id = activity_environment
    icon, _png = _upload_png(store, "db-trash-owned-icon")
    content_file = upload(store, operation="db-trash-owned-content-upload")["file_id"]
    store.iam.execute_typed_command("owner", "page-e1",
        command_kind="block.insert", target_id="owned-db-source-block",
        payload={"type": "table", "content":
            "/api/notion/collaboration/files/" + content_file,
            "props": {"database_id": "database-e1", "icon": "nf:" + icon},
            "after_block_id": None}, operation_id="db-trash-icon-block",
        **command_request(store))
    exclusive = upload(store, operation="db-trash-exclusive-upload")["file_id"]
    shared = upload(store, operation="db-trash-shared-upload")["file_id"]
    exclusive_bytes = store.iam.get_file("owner", exclusive)["path"].read_bytes()
    shared_bytes = store.iam.get_file("owner", shared)["path"].read_bytes()
    _schema(store.iam, "database.property.create", "db-trash-files",
        {"name": "Attachments", "type": "files", "config": {}},
        "db-trash-files-property")
    store.iam.execute_typed_command("owner", "page-e1",
        command_kind="database.cell.set", target_id="row-e1:db-trash-files",
        payload={"row_id": "row-e1", "property_id": "db-trash-files",
            "value": [{"file_id": exclusive}, {"file_id": shared}]},
        expected_target_version=0, operation_id="db-trash-files-cell",
        **command_request(store))
    store.iam.execute_typed_command("owner", "page-e1",
        command_kind="block.insert", target_id="db-trash-shared-active-block",
        payload={"type": "file", "content": "Shared attachment",
            "props": {"file_id": shared}, "after_block_id": "owned-db-source-block"},
        operation_id="db-trash-shared-active-block", **command_request(store))
    _apply(migration, store.db_path, store.user_data_root,
        "before-database-trash-files.db")
    store = CollaborativeWorkspaceStore(store.db_path,
        user_data_root=store.user_data_root,
        identity_directory=store.identity_directory)
    before = store.iam.read_document("owner", "page-e1")
    database = store.iam.read_database("owner", "database-e1")
    archived = database_trash.set_owned_database_trashed(store.iam,
        "owner", workspace_id, "page-e1", "database-e1", archived=True,
        operation_id="db-trash-files-archive",
        expected_document_sequence=before["document_sequence"],
        expected_workspace_epoch=before["workspace_epoch"],
        expected_document_epoch=before["document_epoch"],
        expected_database_version=database["version"])
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("owner", exclusive)
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("owner", icon)
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("owner", content_file)
    exported = database_trash.export_trashed_owned_database(store.iam,
        "owner", workspace_id, "page-e1", "owned-db-source-block",
        with_asset_bundle=True)
    try:
        with zipfile.ZipFile(exported["archive"]) as archive_file:
            paths = {name for name in archive_file.namelist() if name.startswith("assets/")}
            assert len(paths) == 2
            assert archive_file.read(next(name for name in paths if exclusive in name)) == exclusive_bytes
            assert archive_file.read(next(name for name in paths if shared in name)) == shared_bytes
            html_name = next(name for name in archive_file.namelist() if name.endswith(".html"))
            html = archive_file.read(html_name).decode("utf-8")
            assert all(f'href="{name}"' in html for name in paths)
    finally:
        exported["archive"].close()
    assert {exclusive, icon, content_file}.isdisjoint({item["id"] for item in
        store.iam.list_files("owner", "page-e1")["files"]})
    assert store.iam.get_file("owner", shared)["file_id"] == shared
    assert shared in {item["id"] for item in
        store.iam.list_files("owner", "page-e1")["files"]}
    with store.iam._reader() as con:
        page = store.iam._document(con, "page-e1")
        source = CopySourceDocument("page-e1", None, 0, (),
            _canonical_json(store.iam._editing_snapshot(con, page)),
            _canonical_json([dict(item) for item in con.execute(
                "SELECT * FROM workspace_collaboration_files "
                "WHERE document_id='page-e1' AND state='active' ORDER BY id")]))
        filtered = _exclude_archived_row_only_files(con, source)
        copied_file_ids = {item["id"] for item in json.loads(filtered.files_json)}
        assert copied_file_ids & {exclusive, icon, content_file} == set()
        assert shared in copied_file_ids
    copy_fences = store.iam.read_document("owner", "page-e1")
    copied_page = page_copy.copy_page(store.iam, "owner", workspace_id,
        "page-e1", operation_id="db-trash-files-page-copy",
        expected_document_sequence=copy_fences["document_sequence"],
        expected_workspace_epoch=copy_fences["workspace_epoch"],
        expected_document_epoch=copy_fences["document_epoch"])["page"]["id"]
    assert store.iam.read_databases("owner", copied_page) == []
    with store.iam._reader() as con:
        copied_sources = {str(item[0]) for item in con.execute(
            "SELECT source_file_id FROM workspace_collaboration_files "
            "WHERE document_id=? AND origin_kind='copy'", (copied_page,)).fetchall()}
        assert copied_sources == {shared}
    hidden = store.iam.read_document("owner", "page-e1")
    with pytest.raises(NotFoundOrDenied):
        store.iam.delete_file("owner", shared,
            expected_document_sequence=hidden["document_sequence"],
            expected_workspace_epoch=hidden["workspace_epoch"],
            expected_document_epoch=hidden["document_epoch"],
            operation_id="db-trash-shared-file-delete-denied")
    restored = database_trash.set_owned_database_trashed(store.iam,
        "owner", workspace_id, "page-e1", "database-e1", archived=False,
        operation_id="db-trash-files-restore",
        expected_document_sequence=hidden["document_sequence"],
        expected_workspace_epoch=hidden["workspace_epoch"],
        expected_document_epoch=hidden["document_epoch"],
        expected_database_version=archived["database_version"])
    assert not restored["archived"]
    assert store.iam.get_file("owner", exclusive)["file_id"] == exclusive
    assert store.iam.get_file("owner", icon)["file_id"] == icon
    assert store.iam.get_file("owner", content_file)["file_id"] == content_file
    assert {exclusive, shared, icon, content_file}.issubset({item["id"] for item in
        store.iam.list_files("owner", "page-e1")["files"]})


def test_ui_r5_database_trash_rejects_live_only_owned_block_attachment(activity_environment):
    store, workspace_id = activity_environment
    file_id = upload(store, operation="db-trash-live-only-upload")["file_id"]
    store.iam.execute_typed_command("owner", "page-e1",
        command_kind="block.insert", target_id="owned-db-source-block",
        payload={"type": "table", "content": "", "props": {"database_id": "database-e1"},
            "after_block_id": None}, operation_id="db-trash-live-only-block",
        **command_request(store))
    _append_live_block_props(store, workspace_id, "owned-db-source-block",
        {"database_id": "database-e1", "icon": "nf:" + file_id})
    _apply(migration, store.db_path, store.user_data_root,
        "before-database-trash-live-file.db")
    store = CollaborativeWorkspaceStore(store.db_path,
        user_data_root=store.user_data_root,
        identity_directory=store.identity_directory)
    before = store.iam.read_document("owner", "page-e1")
    database = store.iam.read_database("owner", "database-e1")
    with pytest.raises(DatabaseTrashShapeUnavailable,
            match="live owned database attachments differ"):
        database_trash.set_owned_database_trashed(store.iam,
            "owner", workspace_id, "page-e1", "database-e1", archived=True,
            operation_id="db-trash-live-file-archive",
            expected_document_sequence=before["document_sequence"],
            expected_workspace_epoch=before["workspace_epoch"],
            expected_document_epoch=before["document_epoch"],
            expected_database_version=database["version"])
    assert store.iam.get_file("owner", file_id)["file_id"] == file_id
    assert store.iam.read_database("owner", "database-e1")["id"] == "database-e1"


def test_ui_r5_database_trash_preserves_live_only_active_file_sharing(activity_environment):
    store, workspace_id = activity_environment
    file_id = upload(store, operation="db-trash-live-shared-upload")["file_id"]
    store.iam.execute_typed_command("owner", "page-e1",
        command_kind="block.insert", target_id="owned-db-source-block",
        payload={"type": "table", "content": "", "props": {
            "database_id": "database-e1", "icon": "nf:" + file_id},
            "after_block_id": None}, operation_id="db-trash-live-shared-db-block",
        **command_request(store))
    store.iam.execute_typed_command("owner", "page-e1",
        command_kind="block.insert", target_id="db-trash-live-shared-active",
        payload={"type": "file", "content": "Shared", "props": {},
            "after_block_id": "owned-db-source-block"},
        operation_id="db-trash-live-shared-active-block", **command_request(store))
    _append_live_block_props(store, workspace_id, "db-trash-live-shared-active",
        {"file_id": file_id})
    _apply(migration, store.db_path, store.user_data_root,
        "before-database-trash-live-shared.db")
    store = CollaborativeWorkspaceStore(store.db_path,
        user_data_root=store.user_data_root,
        identity_directory=store.identity_directory)
    before = store.iam.read_document("owner", "page-e1")
    database = store.iam.read_database("owner", "database-e1")
    archived = database_trash.set_owned_database_trashed(store.iam,
        "owner", workspace_id, "page-e1", "database-e1", archived=True,
        operation_id="db-trash-live-shared-archive",
        expected_document_sequence=before["document_sequence"],
        expected_workspace_epoch=before["workspace_epoch"],
        expected_document_epoch=before["document_epoch"],
        expected_database_version=database["version"])
    assert archived["archived"]
    assert store.iam.get_file("owner", file_id)["file_id"] == file_id
    assert file_id in {item["id"] for item in
        store.iam.list_files("owner", "page-e1")["files"]}
    _append_live_block_props(store, workspace_id, "db-trash-live-shared-active", {})
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("owner", file_id)
    assert file_id not in {item["id"] for item in
        store.iam.list_files("owner", "page-e1")["files"]}
