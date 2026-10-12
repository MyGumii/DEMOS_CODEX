"""UI-R5: isolated atomic move of an owned database and its scoped resources."""

import sqlite3
import json
import base64
from contextlib import closing
from concurrent.futures import ProcessPoolExecutor
from io import BytesIO
import multiprocessing
from pathlib import Path
import shutil
import subprocess

import pytest

from code_assist_v1.notion_collaboration import database_move, linked_databases, page_creation
from code_assist_v1.notion_collaboration.database_move import DatabaseMoveShapeUnavailable
from code_assist_v1.notion_collaboration.canonical_authority import AuthorityUnavailable, InjectedFailure, NotFoundOrDenied, _sha256
from code_assist_v1.notion_collaboration.iam_authority import InMemoryIdentityDirectory
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.notion_collaboration.editing_authority import CollaborativeEditingTransportAuthority
from code_assist_v1.notion_collaboration.title_projection import _frames, _generation
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import (
    activity_environment,
)
from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import (
    environment, appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment,
    archive_environment, guest_environment, image_environment,
    owned_database_block, request as command_request, upload,
)
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema
from code_assist_v1.tests.notion_collaboration.test_database_row_content import (
    request as body_request, execute as row_execute,
)
from code_assist_v1.tests.notion_collaboration.test_database_row_appearance import (
    request as appearance_request,
)
from code_assist_v1.tests.notion_collaboration.test_search_authority import _grant
from code_assist_v1.tests.notion_collaboration.test_title_projection import append


def _destination(store, workspace_id):
    source = store.iam.read_document("owner", "page-e1")
    store.iam.create_document("owner", workspace_id, "page-db-destination",
        title="Database destination", blocks=[{"id": "destination-body",
            "type": "paragraph", "content": "Keep destination", "props": {}}],
        expected_workspace_epoch=source["workspace_epoch"],
        operation_id="db-move-create-destination")


def _request(store):
    source = store.iam.read_document("owner", "page-e1")
    destination = store.iam.read_document("owner", "page-db-destination")
    return {"expected_source_sequence": source["document_sequence"],
        "expected_destination_sequence": destination["document_sequence"],
        "expected_workspace_epoch": source["workspace_epoch"],
        "expected_source_epoch": source["document_epoch"],
        "expected_destination_epoch": destination["document_epoch"]}


def _move(store, workspace_id, *, operation_id="db-move-commit", fault=None, fences=None):
    return database_move.move_owned_database(store.iam, "owner", workspace_id,
        "page-e1", "page-db-destination", "database-e1",
        operation_id=operation_id, fault=fault,
        **(fences if fences is not None else _request(store)))


def _move_worker(args):
    catalog, root, workspace_id, fences = args
    store = CollaborativeWorkspaceStore(catalog, user_data_root=root,
        identity_directory=InMemoryIdentityDirectory(("owner",)))
    return _move(store, workspace_id, fences=fences)


def _upload_png(store, operation):
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6xj8AAAAASUVORK5CYII=")
    page = store.iam.read_document("owner", "page-e1")
    session = store.iam.begin_upload("owner", "page-e1", original_name="icon.png",
        mime_type="image/png", total_size=len(png), expected_sha256=_sha256(png),
        chunk_size=65536, expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"], operation_id=operation + "-upload")
    store.iam.stage_chunk("owner", session["session_id"], chunk_index=0,
        stream=BytesIO(png), operation_id=operation + "-chunk")
    file_id = store.iam.finalize_upload("owner", session["session_id"],
        operation_id=operation + "-final")["file_id"]
    return file_id, png


def test_ui_r5_database_move_commits_both_documents_and_activity(activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    fences = _request(store)
    result = _move(store, workspace_id, fences=fences)
    assert result["database_id"] == "database-e1"
    assert result["block_id"] == "owned-db-source-block"
    assert result["refresh_page_ids"] == ["page-e1", "page-db-destination"]
    source = store.iam.read_document("owner", "page-e1")
    destination = store.iam.read_document("owner", "page-db-destination")
    assert not any(block["id"] == result["block_id"] for block in source["blocks"])
    assert [block["id"] for block in destination["blocks"]] == [
        "destination-body", result["block_id"]]
    assert [item["id"] for item in store.iam.read_databases(
        "owner", "page-db-destination")] == ["database-e1"]
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("SELECT document_id FROM workspace_collaboration_databases "
            "WHERE id='database-e1'").fetchone() == ("page-db-destination",)
        assert con.execute("SELECT document_id,last_editor_subject_id FROM "
            "workspace_collaboration_database_activity WHERE database_id='database-e1'").fetchone() == (
                "page-db-destination", "owner")
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations "
            "WHERE action=?", (database_move.ACTION,)).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_events "
            "WHERE event_type='database.moved'").fetchone()[0] == 1
    assert _move(store, workspace_id, fences=fences) == {**result, "replayed": True}


def test_ui_r5_database_move_fault_rolls_back_ownership(activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    store.iam.execute_typed_command("owner", "page-db-destination",
        command_kind="block.insert", target_id="fault-linked-view",
        payload={"type": "table", "content": "", "props": {
            "linked_database": {"document_id": "page-e1", "database_id": "database-e1"},
            "views": [{"id": "fault-view", "name": "Keep view", "type": "table",
                "config": {}}], "active_view_id": "fault-view"},
            "after_block_id": "destination-body"},
        operation_id="db-move-fault-linked-view", **command_request(store, "page-db-destination"))
    store.add_database_favorite("outsider", workspace_id,
        source_owner_subject_id="owner", source_page_id="page-e1",
        database_block_id="owned-db-source-block", expected_favorite_version=0,
        operation_id="db-move-fault-favorite")
    original_favorite = store.get_navigation_state("outsider", workspace_id)["favorites"]
    fences = _request(store)
    for fault in ("after_domain", "after_event", "after_audit", "after_outbox", "after_operation"):
        with pytest.raises(InjectedFailure):
            _move(store, workspace_id, operation_id="db-move-fault-" + fault,
                fault=fault, fences=fences)
        assert any(block["id"] == "owned-db-source-block" for block in
            store.iam.read_document("owner", "page-e1")["blocks"])
        assert store.get_navigation_state("outsider", workspace_id)["favorites"] == original_favorite
        with closing(sqlite3.connect(store.db_path)) as con:
            assert con.execute("SELECT document_id FROM workspace_collaboration_databases "
                "WHERE id='database-e1'").fetchone() == ("page-e1",)
            linked = json.loads(con.execute("SELECT props_json FROM workspace_collaboration_blocks "
                "WHERE id='fault-linked-view'").fetchone()[0])
            assert linked["linked_database"]["document_id"] == "page-e1"
            assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert linked_databases.read_linked_database(store.iam, "owner", "page-db-destination",
        "fault-linked-view")["source"]["document_id"] == "page-e1"


def test_ui_r5_database_move_refuses_malformed_linked_reference(activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    store.iam.execute_typed_command("owner", "page-db-destination",
        command_kind="block.insert", target_id="linked-db-ref",
        payload={"type": "table", "content": "", "props": {"linked_database": {
            "document_id": "page-e1", "database_id": "database-e1"}},
            "after_block_id": "destination-body"},
        operation_id="db-move-linked-view", **command_request(store, "page-db-destination"))
    fences = _request(store)
    with pytest.raises(DatabaseMoveShapeUnavailable):
        _move(store, workspace_id, fences=fences)
    assert any(block["id"] == "owned-db-source-block" for block in
        store.iam.read_document("owner", "page-e1")["blocks"])
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute("SELECT document_id FROM workspace_collaboration_databases "
            "WHERE id='database-e1'").fetchone() == ("page-e1",)
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_ui_r5_database_move_retargets_item_comment_thread_and_inbox(activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    _grant(store.iam, "page-db-destination", "content-editor", "db-move-comment-dest-grant")
    target = {"kind": "database_row", "database_id": "database-e1", "row_id": "row-e1"}
    source = store.iam.read_document("content-editor", "page-e1")
    store.iam.add_comment("content-editor", "page-e1", "db-move-item-comment",
        content="Keep this anchor", anchor=target, mention_subject_ids=["owner"],
        operation_id="db-move-item-comment",
        expected_workspace_epoch=source["workspace_epoch"],
        expected_document_epoch=source["document_epoch"])
    source = store.iam.read_document("owner", "page-e1")
    store.iam.add_comment("owner", "page-e1", "db-move-item-reply",
        content="Keep this reply", anchor=target, parent_comment_id="db-move-item-comment",
        operation_id="db-move-item-reply",
        expected_workspace_epoch=source["workspace_epoch"],
        expected_document_epoch=source["document_epoch"])
    source = store.iam.read_document("owner", "page-e1")
    store.iam.add_comment("owner", "page-e1", "db-move-unrelated-page-comment",
        content="Stay on the source", anchor={}, operation_id="db-move-page-comment",
        expected_workspace_epoch=source["workspace_epoch"],
        expected_document_epoch=source["document_epoch"])
    source = store.iam.read_document("owner", "page-e1")
    store.iam.add_comment("owner", "page-e1", "db-move-owned-block-comment",
        content="Move with database block", block_id="owned-db-source-block",
        operation_id="db-move-block-comment",
        expected_workspace_epoch=source["workspace_epoch"],
        expected_document_epoch=source["document_epoch"])
    owner_before = store.iam.list_inbox("owner", workspace_id=workspace_id)["items"]
    editor_before = store.iam.list_inbox("content-editor", workspace_id=workspace_id)["items"]
    assert owner_before[0]["document_id"] == editor_before[0]["document_id"] == "page-e1"
    with pytest.raises(InjectedFailure):
        _move(store, workspace_id, operation_id="db-move-comment-fault", fault="after_domain")
    assert {item["id"] for item in store.iam.list_comments(
        "owner", "page-e1", item_target=target)["items"]} == {
            "db-move-item-comment", "db-move-item-reply"}
    assert store.iam.list_inbox("owner", workspace_id=workspace_id)["items"][0][
        "document_id"] == "page-e1"
    result = _move(store, workspace_id)
    assert result["retargeted_comment_count"] == 3
    comments = store.iam.list_comments("owner", "page-db-destination", item_target=target)["items"]
    assert {item["id"] for item in comments} == {"db-move-item-comment", "db-move-item-reply"}
    assert [item["id"] for item in store.iam.list_comments(
        "owner", "page-db-destination")["items"]] == ["db-move-owned-block-comment"]
    assert [item["id"] for item in store.iam.list_comments("owner", "page-e1")["items"]] == [
        "db-move-unrelated-page-comment"]
    for actor in ("owner", "content-editor"):
        items = store.iam.list_inbox(actor, workspace_id=workspace_id)["items"]
        assert len(items) == 1 and items[0]["document_id"] == "page-db-destination"
        assert items[0]["payload"]["document_id"] == "page-db-destination"
    editor_notification = store.iam.list_inbox(
        "content-editor", workspace_id=workspace_id)["items"][0]
    destination = store.iam.read_document("owner", "page-db-destination")
    store.iam.revoke_page_grant("owner", "page-db-destination", "subject",
        "content-editor", expected_workspace_epoch=destination["workspace_epoch"],
        expected_document_epoch=destination["document_epoch"],
        operation_id="db-move-comment-dest-revoke")
    assert not store.iam.list_inbox("content-editor", workspace_id=workspace_id)["items"]
    with pytest.raises(NotFoundOrDenied):
        store.iam.mark_notification_read("content-editor", editor_notification["id"],
            expected_workspace_id=workspace_id, operation_id="db-move-comment-denied-read")
    _grant(store.iam, "page-db-destination", "content-editor", "db-move-comment-dest-regrant")
    assert store.iam.list_inbox("content-editor", workspace_id=workspace_id)["items"][0][
        "document_id"] == "page-db-destination"
    with closing(sqlite3.connect(store.db_path)) as con:
        assert {row[0] for row in con.execute("SELECT document_id FROM "
            "workspace_collaboration_comments WHERE id IN (?,?,?)",
            ("db-move-item-comment", "db-move-item-reply",
                "db-move-owned-block-comment"))} == {"page-db-destination"}
        assert {row[0] for row in con.execute("SELECT document_id FROM "
            "workspace_collaboration_notifications WHERE comment_id IN (?,?)",
            ("db-move-item-comment", "db-move-item-reply"))} == {"page-e1"}
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    notification = store.iam.list_inbox("owner", workspace_id=workspace_id)["items"][0]
    read = store.iam.mark_notification_read("owner", notification["id"],
        expected_workspace_id=workspace_id, operation_id="db-move-comment-read")
    assert read["document_id"] == "page-db-destination"


def test_ui_r5_database_move_retargets_favorite_without_changing_order(activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    store.add_database_favorite("outsider", workspace_id,
        source_owner_subject_id="owner", source_page_id="page-e1",
        database_block_id="owned-db-source-block", expected_favorite_version=0,
        operation_id="db-move-favorite")
    before = store.get_navigation_state("outsider", workspace_id)
    assert before["favorites"][0]["source_page_id"] == "page-e1"
    fences = _request(store)
    result = _move(store, workspace_id, fences=fences)
    assert result["retargeted_favorite_count"] == 1
    with closing(sqlite3.connect(store.db_path)) as con:
        row = con.execute("SELECT favorites_json,favorite_version FROM "
            "workspace_guest_navigation_states WHERE workspace_id=? AND subject_id='outsider'",
            (workspace_id,)).fetchone()
        assert row[1] == before["favorite_version"] + 1
        assert json.loads(row[0]) == [{"source_owner_subject_id": "owner",
            "source_page_id": "page-db-destination",
            "database_block_id": "owned-db-source-block"}]
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert _move(store, workspace_id, fences=fences) == {**result, "replayed": True}


def test_ui_r5_database_move_favorite_remains_visible_at_native_destination(activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    created = page_creation.create_page(store.iam, "owner", workspace_id,
        title="Favorite destination", parent_id="",
        base_version=store.get_workspace("owner", workspace_id)["version"],
        operation_id="db-move-native-destination")
    destination_id = created["page"]["id"]
    _grant(store.iam, destination_id, "outsider", "db-move-destination-guest-view")
    store.add_database_favorite("outsider", workspace_id,
        source_owner_subject_id="owner", source_page_id="page-e1",
        database_block_id="owned-db-source-block", expected_favorite_version=0,
        operation_id="db-move-native-favorite")
    source = store.iam.read_document("owner", "page-e1")
    destination = store.iam.read_document("owner", destination_id)
    result = database_move.move_owned_database(store.iam, "owner", workspace_id,
        "page-e1", destination_id, "database-e1", operation_id="db-move-native-commit",
        expected_source_sequence=source["document_sequence"],
        expected_destination_sequence=destination["document_sequence"],
        expected_workspace_epoch=source["workspace_epoch"],
        expected_source_epoch=source["document_epoch"],
        expected_destination_epoch=destination["document_epoch"])
    assert result["retargeted_favorite_count"] == 1
    assert store.get_navigation_state("outsider", workspace_id)["favorites"] == [{
        "source_owner_subject_id": "owner", "source_page_id": destination_id,
        "database_block_id": "owned-db-source-block", "position": 0}]


def test_ui_r5_database_move_copies_file_cell_authority_atomically(activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    file_id = upload(store)["file_id"]
    _schema(store.iam, "database.property.create", "move-file-property",
        {"name": "Attachment", "type": "files", "config": {}}, "move-file-property-create")
    store.iam.execute_typed_command("owner", "page-e1", command_kind="database.cell.set",
        target_id="row-e1:move-file-property", payload={"row_id": "row-e1",
            "property_id": "move-file-property", "value": [{"file_id": file_id}]},
        expected_target_version=0, operation_id="db-move-file-cell", **command_request(store))
    original = store.iam.get_file("owner", file_id)["path"].read_bytes()
    fences = _request(store)
    result = _move(store, workspace_id, fences=fences)
    assert result["copied_file_count"] == 1
    rows = store.iam.read_database("owner", "database-e1")["rows"]
    copied_id = rows[0]["cells"]["move-file-property"][0]["file_id"]
    assert copied_id != file_id
    copied = store.iam.get_file("owner", copied_id)
    assert copied["document_id"] == "page-db-destination"
    assert copied["path"].read_bytes() == original
    assert result["retired_source_file_count"] == 1
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("owner", file_id)
    assert store.iam.get_file("owner", copied_id)["path"].read_bytes() == original
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute("SELECT source_file_id,source_document_id,origin_kind FROM "
            "workspace_collaboration_files WHERE id=?", (copied_id,)).fetchone() == (
                file_id, "page-e1", "copy")
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("SELECT document_id FROM workspace_collaboration_databases "
            "WHERE id='database-e1'").fetchone() == ("page-db-destination",)
    assert _move(store, workspace_id, fences=fences) == {**result, "replayed": True}


def test_ui_r5_database_move_file_copy_rolls_back_with_domain(activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    file_id = upload(store)["file_id"]
    _schema(store.iam, "database.property.create", "move-file-property",
        {"name": "Attachment", "type": "files", "config": {}}, "move-file-fault-property")
    store.iam.execute_typed_command("owner", "page-e1", command_kind="database.cell.set",
        target_id="row-e1:move-file-property", payload={"row_id": "row-e1",
            "property_id": "move-file-property", "value": [{"file_id": file_id}]},
        expected_target_version=0, operation_id="db-move-file-fault-cell", **command_request(store))
    with pytest.raises(InjectedFailure):
        _move(store, workspace_id, operation_id="db-move-file-fault",
            fault="after_domain")
    assert store.iam.read_database("owner", "database-e1")["rows"][0]["cells"][
        "move-file-property"][0]["file_id"] == file_id
    assert store.iam.get_file("owner", file_id)["document_id"] == "page-e1"
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_files "
            "WHERE origin_kind='copy' AND copy_operation_id='db-move-file-fault'").fetchone()[0] == 0
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_ui_r5_database_move_remaps_item_body_file_and_embedded_url(activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    file_id = upload(store)["file_id"]
    original_url = "/api/notion/collaboration/files/" + file_id
    row_execute(store.iam, body_request(store.iam, "db-move-body-file",
        payload={"blocks": [{"id": "item-file", "type": "file", "content": "Attachment",
            "props": {"file_id": file_id, "url": original_url}}]}))
    result = _move(store, workspace_id)
    assert result["copied_file_count"] == result["retired_source_file_count"] == 1
    body = store.iam.read_database("owner", "database-e1")["rows"][0]["body"]["blocks"][0]
    copied_id = body["props"]["file_id"]
    assert copied_id != file_id and body["props"]["url"].endswith("/" + copied_id)
    assert store.iam.get_file("owner", copied_id)["document_id"] == "page-db-destination"
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("owner", file_id)
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_ui_r5_database_move_remaps_item_uploaded_icon(activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    file_id, png = _upload_png(store, "db-move-icon")
    row_execute(store.iam, body_request(store.iam, "db-move-icon-body",
        payload={"blocks": [{"id": "item-icon-file", "type": "file", "content": "Icon file",
            "props": {"file_id": file_id}}]}))
    request = appearance_request(store.iam, "db-move-item-icon")
    request["payload"] = {"icon": "nf:" + file_id}
    row_execute(store.iam, request)
    with pytest.raises(InjectedFailure):
        _move(store, workspace_id, operation_id="db-move-item-icon-fault",
            fault="after_domain")
    source_row = store.iam.read_database("owner", "database-e1")["rows"][0]
    assert source_row["appearance"]["icon"] == "nf:" + file_id
    assert source_row["body"]["blocks"][0]["props"]["file_id"] == file_id
    assert store.iam.get_file("owner", file_id)["document_id"] == "page-e1"
    result = _move(store, workspace_id)
    assert result["copied_file_count"] == result["retired_source_file_count"] == 1
    row = store.iam.read_database("owner", "database-e1")["rows"][0]
    icon = row["appearance"]["icon"]
    assert icon.startswith("nf:") and icon != "nf:" + file_id
    assert row["body"]["blocks"][0]["props"]["file_id"] == icon[3:]
    assert store.iam.get_file("owner", icon[3:])["path"].read_bytes() == png
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("owner", file_id)


def test_ui_r5_database_move_remaps_owned_block_uploaded_icon(activity_environment):
    store, workspace_id = activity_environment
    _destination(store, workspace_id)
    file_id, png = _upload_png(store, "db-move-owned-block-icon")
    store.iam.execute_typed_command("owner", "page-e1", command_kind="block.insert",
        target_id="owned-db-source-block", payload={"type": "table", "content": "",
            "props": {"database_id": "database-e1", "icon": "nf:" + file_id},
            "after_block_id": None}, operation_id="db-move-owned-icon-block",
        **command_request(store))
    result = _move(store, workspace_id)
    assert result["copied_file_count"] == result["retired_source_file_count"] == 1
    destination = store.iam.read_document("owner", "page-db-destination")
    block = next(item for item in destination["blocks"]
        if item["id"] == "owned-db-source-block")
    icon = block["props"]["icon"]
    assert icon.startswith("nf:") and icon != "nf:" + file_id
    assert store.iam.get_file("owner", icon[3:])["path"].read_bytes() == png
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("owner", file_id)
    with store.iam._reader() as con:
        generation = _generation(con, "page-db-destination")
        frames = _frames(con, "page-db-destination", generation)
    from code_assist_v1.notion_collaboration import search_projection
    live = search_projection.materialize([frames])[0]
    moved = next(item for item in live["blocks"] if item["id"] == block["id"])
    assert moved["props"]["icon"] == icon


def test_ui_r5_database_move_remaps_owned_block_content_file_link(activity_environment):
    store, workspace_id = activity_environment
    _destination(store, workspace_id)
    file_id, png = _upload_png(store, "db-move-owned-block-content")
    original_url = "/api/notion/collaboration/files/" + file_id
    store.iam.execute_typed_command("owner", "page-e1", command_kind="block.insert",
        target_id="owned-db-source-block", payload={"type": "table",
            "content": "Attachment " + original_url,
            "props": {"database_id": "database-e1"}, "after_block_id": None},
        operation_id="db-move-owned-content-block", **command_request(store))
    fences = _request(store)
    with pytest.raises(InjectedFailure):
        _move(store, workspace_id, operation_id="db-move-owned-content-fault",
            fault="after_domain", fences=fences)
    original = next(item for item in store.iam.read_document("owner", "page-e1")["blocks"]
        if item["id"] == "owned-db-source-block")
    assert original["content"] == "Attachment " + original_url
    assert store.iam.get_file("owner", file_id)["path"].read_bytes() == png

    result = _move(store, workspace_id, fences=fences)
    assert result["copied_file_count"] == result["retired_source_file_count"] == 1
    destination = store.iam.read_document("owner", "page-db-destination")
    block = next(item for item in destination["blocks"]
        if item["id"] == "owned-db-source-block")
    assert block["content"].startswith("Attachment /api/notion/collaboration/files/")
    assert original_url not in block["content"]
    copied_id = block["content"].split("/files/", 1)[1]
    assert store.iam.get_file("owner", copied_id)["path"].read_bytes() == png
    with pytest.raises(NotFoundOrDenied):
        store.iam.get_file("owner", file_id)
    with store.iam._reader() as con:
        generation = _generation(con, "page-db-destination")
        frames = _frames(con, "page-db-destination", generation)
    from code_assist_v1.notion_collaboration import search_projection
    live = search_projection.materialize([frames])[0]
    moved = next(item for item in live["blocks"] if item["id"] == block["id"])
    assert moved["content"] == block["content"]


def test_ui_r5_database_move_retains_shared_source_file_reference(activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    file_id = upload(store)["file_id"]
    _schema(store.iam, "database.property.create", "move-file-property",
        {"name": "Attachment", "type": "files", "config": {}}, "move-file-shared-property")
    store.iam.execute_typed_command("owner", "page-e1", command_kind="database.cell.set",
        target_id="row-e1:move-file-property", payload={"row_id": "row-e1",
            "property_id": "move-file-property", "value": [{"file_id": file_id}]},
        expected_target_version=0, operation_id="db-move-file-shared-cell", **command_request(store))
    store.iam.execute_typed_command("owner", "page-e1", command_kind="block.insert",
        target_id="shared-source-file-block", payload={"type": "file",
            "content": "Keep source attachment", "props": {"file_id": file_id},
            "after_block_id": "owned-db-source-block"},
        operation_id="db-move-file-shared-block", **command_request(store))
    result = _move(store, workspace_id)
    assert result["copied_file_count"] == 1
    assert result["retired_source_file_count"] == 0
    assert store.iam.get_file("owner", file_id)["document_id"] == "page-e1"
    copied_id = store.iam.read_database("owner", "database-e1")["rows"][0]["cells"][
        "move-file-property"][0]["file_id"]
    assert copied_id != file_id
    assert store.iam.get_file("owner", copied_id)["document_id"] == "page-db-destination"
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_ui_r5_database_move_refuses_live_only_owned_block_file(activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    file_id = upload(store)["file_id"]
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
const block=d.getMap('blocks').get('owned-db-source-block');
block.set('props',JSON.stringify({database_id:'database-e1',icon:'nf:'+input.file_id}));
process.stdout.write(Buffer.from(Y.encodeStateAsUpdate(d,vector)).toString('base64'));
"""
    bundle = Path(__file__).parents[2] / "notion_static/vendor/yjs-13.6.32.bundle.js"
    prepared = subprocess.run([shutil.which("node"), "-e", script, str(bundle)],
        input=json.dumps({"seed": seed, "frames": [base64.b64encode(frame).decode()
            for frame in frames], "file_id": file_id}).encode(), capture_output=True,
        check=True, timeout=15)
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    append(store.iam, transport, workspace_id, [base64.b64decode(prepared.stdout)],
        document="page-e1")
    assert next(block for block in store.iam.read_document("owner", "page-e1")["blocks"]
        if block["id"] == "owned-db-source-block")["props"] == {"database_id": "database-e1"}
    with pytest.raises(DatabaseMoveShapeUnavailable, match="live database attachments"):
        _move(store, workspace_id)
    assert store.iam.get_file("owner", file_id)["document_id"] == "page-e1"
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute("SELECT document_id FROM workspace_collaboration_databases "
            "WHERE id='database-e1'").fetchone() == ("page-e1",)
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_ui_r5_database_move_retargets_live_only_linked_reference(activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    store.iam.execute_typed_command("owner", "page-db-destination",
        command_kind="block.insert", target_id="live-only-linked",
        payload={"type": "table", "content": "", "props": {},
            "after_block_id": "destination-body"},
        operation_id="db-move-live-link-host", **command_request(store, "page-db-destination"))
    with store.iam._reader() as con:
        document = store.iam._document(con, "page-db-destination")
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
const block=d.getMap('blocks').get('live-only-linked');
block.set('props',JSON.stringify({linked_database:{document_id:'page-e1',database_id:'database-e1'},
  views:[{id:'live-view',name:'Live',type:'table',config:{}}],active_view_id:'live-view'}));
process.stdout.write(Buffer.from(Y.encodeStateAsUpdate(d,vector)).toString('base64'));
"""
    bundle = Path(__file__).parents[2] / "notion_static/vendor/yjs-13.6.32.bundle.js"
    result = subprocess.run([shutil.which("node"), "-e", script, str(bundle)],
        input=json.dumps({"seed": seed, "frames": [base64.b64encode(frame).decode()
            for frame in frames]}).encode(), capture_output=True, check=True, timeout=15)
    transport = CollaborativeEditingTransportAuthority.open(store.db_path, store.user_data_root)
    append(store.iam, transport, workspace_id, [base64.b64decode(result.stdout)],
        document="page-db-destination")
    assert next(block for block in store.iam.read_document("owner", "page-db-destination")["blocks"]
        if block["id"] == "live-only-linked")["props"] == {}
    result = _move(store, workspace_id)
    assert result["retargeted_linked_view_count"] == 1
    linked = linked_databases.read_linked_database(store.iam, "owner",
        "page-db-destination", "live-only-linked")
    assert linked["source"] == {"document_id": "page-db-destination",
        "database_id": "database-e1"}
    with closing(sqlite3.connect(store.db_path)) as con:
        props = json.loads(con.execute("SELECT props_json FROM workspace_collaboration_blocks "
            "WHERE id='live-only-linked'").fetchone()[0])
        assert props["linked_database"]["document_id"] == "page-db-destination"
        assert con.execute("SELECT document_id FROM workspace_collaboration_databases "
            "WHERE id='database-e1'").fetchone() == ("page-db-destination",)


def test_ui_r5_database_move_retargets_external_linked_view(activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    source = store.iam.read_document("owner", "page-e1")
    store.iam.create_document("owner", workspace_id, "page-linked-host",
        title="Linked host", blocks=[{"id": "host-body", "type": "paragraph",
            "content": "Keep host", "props": {}}],
        expected_workspace_epoch=source["workspace_epoch"],
        operation_id="db-move-linked-host")
    props = {"linked_database": {"document_id": "page-e1", "database_id": "database-e1"},
        "views": [{"id": "host-view", "name": "Local view", "type": "table",
            "config": {"filters": []}}], "active_view_id": "host-view", "indent": 1}
    store.iam.execute_typed_command("owner", "page-linked-host",
        command_kind="block.insert", target_id="host-linked-view",
        payload={"type": "table", "content": "", "props": props,
            "after_block_id": "host-body"}, operation_id="db-move-linked-host-insert",
        **command_request(store, "page-linked-host"))
    before = linked_databases.read_linked_database(store.iam, "owner",
        "page-linked-host", "host-linked-view")
    result = _move(store, workspace_id)
    assert result["retargeted_linked_view_count"] == 1
    assert result["refresh_page_ids"] == ["page-e1", "page-db-destination", "page-linked-host"]
    after = linked_databases.read_linked_database(store.iam, "owner",
        "page-linked-host", "host-linked-view")
    assert after["source"] == {"document_id": "page-db-destination",
        "database_id": "database-e1"}
    assert after["views"] == before["views"]
    assert after["instance"] == before["instance"]
    with closing(sqlite3.connect(store.db_path)) as con:
        stored = json.loads(con.execute("SELECT props_json FROM workspace_collaboration_blocks "
            "WHERE id='host-linked-view'").fetchone()[0])
        assert stored == {**props, "linked_database": {"document_id": "page-db-destination",
            "database_id": "database-e1"}}
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_ui_r5_database_move_rechecks_other_document_after_link_scan(activity_environment, monkeypatch):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    source = store.iam.read_document("owner", "page-e1")
    store.iam.create_document("owner", workspace_id, "page-other-link-host",
        title="Other host", blocks=[], expected_workspace_epoch=source["workspace_epoch"],
        operation_id="db-move-other-host")
    original = database_move.page_move._prepare_deltas

    def concurrent_edit(*args, **kwargs):
        deltas = original(*args, **kwargs)
        store.iam.execute_typed_command("owner", "page-other-link-host",
            command_kind="block.insert", target_id="racing-block",
            payload={"type": "paragraph", "content": "Concurrent edit", "props": {},
                "after_block_id": None}, operation_id="db-move-racing-edit",
            **command_request(store, "page-other-link-host"))
        return deltas

    monkeypatch.setattr(database_move.page_move, "_prepare_deltas", concurrent_edit)
    with pytest.raises(AuthorityUnavailable, match="linked-reference scan changed"):
        _move(store, workspace_id)
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute("SELECT document_id FROM workspace_collaboration_databases "
            "WHERE id='database-e1'").fetchone() == ("page-e1",)
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_ui_r5_database_move_two_process_replay_has_one_effect(activity_environment):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    store.iam.execute_typed_command("owner", "page-db-destination",
        command_kind="block.insert", target_id="concurrent-linked-view",
        payload={"type": "table", "content": "", "props": {
            "linked_database": {"document_id": "page-e1", "database_id": "database-e1"},
            "views": [{"id": "concurrent-view", "name": "Linked", "type": "table",
                "config": {}}], "active_view_id": "concurrent-view"},
            "after_block_id": "destination-body"},
        operation_id="db-move-concurrent-linked-view",
        **command_request(store, "page-db-destination"))
    store.add_database_favorite("outsider", workspace_id,
        source_owner_subject_id="owner", source_page_id="page-e1",
        database_block_id="owned-db-source-block", expected_favorite_version=0,
        operation_id="db-move-concurrent-favorite")
    args = (str(store.db_path), str(store.user_data_root), workspace_id, _request(store))
    with ProcessPoolExecutor(max_workers=2,
            mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(_move_worker, (args, args)))
    assert results[0]["operation_id"] == results[1]["operation_id"] == "db-move-commit"
    assert sorted(result["replayed"] for result in results) == [False, True]
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_operations "
            "WHERE action=?", (database_move.ACTION,)).fetchone()[0] == 1
        favorite = con.execute("SELECT favorites_json,favorite_version FROM "
            "workspace_guest_navigation_states WHERE workspace_id=? AND subject_id='outsider'",
            (workspace_id,)).fetchone()
        assert favorite[1] == 2
        assert json.loads(favorite[0])[0]["source_page_id"] == "page-db-destination"
        linked = json.loads(con.execute("SELECT props_json FROM workspace_collaboration_blocks "
            "WHERE id='concurrent-linked-view'").fetchone()[0])
        assert linked["linked_database"]["document_id"] == "page-db-destination"
        assert con.execute("SELECT row_version FROM workspace_collaboration_blocks "
            "WHERE id='concurrent-linked-view'").fetchone()[0] == 2
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert linked_databases.read_linked_database(store.iam, "owner",
        "page-db-destination", "concurrent-linked-view")["source"]["document_id"] == "page-db-destination"
