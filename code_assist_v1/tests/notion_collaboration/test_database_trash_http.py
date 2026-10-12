"""UI-R5: gated owned-database Trash transport and exact receipt recovery."""

import io
import zipfile

from flask import Flask
import pytest

from code_assist_v1 import routes_notion_workspace as routes
from code_assist_v1 import notion_workspace_database_trash_migration as migration
from code_assist_v1.notion_collaboration.database_trash import DatabaseTrashShapeUnavailable
from code_assist_v1.notion_collaboration.canonical_authority import NotFoundOrDenied
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import activity_environment
from code_assist_v1.tests.notion_collaboration.test_database_row_content import _apply
from code_assist_v1.tests.notion_collaboration.test_database_features import _schema
from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import (
    environment, appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, owned_database_block,
)


def _client(store, monkeypatch, *, enabled):
    actor = {"id": "owner"}
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_collaborative_runtime_enabled", lambda: True)
    monkeypatch.setattr(routes, "current_user_id", lambda: actor["id"])
    if enabled:
        original = store.capabilities
        monkeypatch.setattr(store, "capabilities", lambda: {
            **original(), "collaborative_owned_database_trash": True})
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-database-trash-http")
    routes.register_notion_workspace_routes(app)
    return app.test_client(), actor


def test_ui_r5_database_trash_live_attachment_conflict_is_actionable():
    app = Flask(__name__)
    with app.app_context():
        response, status = routes._workspace_error_response(
            DatabaseTrashShapeUnavailable("live owned database attachments differ"))
    assert status == 422
    assert response.get_json()["code"] == "database_trash_shape_unavailable"
    assert "동기화 후 다시 시도" in response.get_json()["message"]


def test_ui_r5_database_trash_http_gate_archive_restore_and_receipt(
        activity_environment, monkeypatch):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _apply(migration, store.db_path, store.user_data_root,
        "before-database-trash-http.db")
    store = CollaborativeWorkspaceStore(store.db_path,
        user_data_root=store.user_data_root,
        identity_directory=store.identity_directory)
    assert store.capabilities()["collaborative_owned_database_trash"] is False
    favorite_version = store.get_navigation_state("outsider", workspace_id)["favorite_version"]
    store.add_database_favorite("outsider", workspace_id,
        source_owner_subject_id="owner", source_page_id="page-e1",
        database_block_id="owned-db-source-block",
        expected_favorite_version=favorite_version,
        operation_id="db-trash-http-favorite")
    _schema(store.iam, "database.property.create", "archive-export-title",
        {"name": "Name", "type": "title", "config": {}},
        "db-trash-http-export-title-property", actor="owner")
    draft_page = store.iam.read_document("owner", "page-e1")
    draft_row = store.iam.read_database("owner", "database-e1")["rows"][0]
    store.iam.execute_surface_command("owner", "page-e1",
        database_id="database-e1", command_kind="database.row.content.set",
        target_id=draft_row["id"], payload={"blocks": [
            {"id": "archive-export-text", "type": "paragraph", "content": "ARCHIVED KEEP", "props": {}},
            {"id": "archive-export-image", "type": "image", "content": "ARCHIVED HIDE", "props": {}},
        ]}, expected_target_version=draft_row["version"],
        expected_document_sequence=draft_page["document_sequence"],
        expected_workspace_epoch=draft_page["workspace_epoch"],
        expected_document_epoch=draft_page["document_epoch"],
        operation_id="db-trash-http-export-media-body")
    page = store.iam.read_document("owner", "page-e1")
    database = store.iam.read_database("owner", "database-e1")
    body = {"document_id": "page-e1",
        "expected_document_sequence": page["document_sequence"],
        "expected_workspace_epoch": page["workspace_epoch"],
        "expected_document_epoch": page["document_epoch"],
        "expected_database_version": database["version"],
        "operation_id": "db-trash-http-archive"}
    base = f"/api/notion/workspaces/{workspace_id}/databases/database-e1"
    listing = f"/api/notion/workspaces/{workspace_id}/database-trash"
    preview = (f"/api/notion/workspaces/{workspace_id}/pages/page-e1/"
        "archived-databases/owned-db-source-block")
    export = preview + "/export"
    client, actor = _client(store, monkeypatch, enabled=False)
    assert client.post(base + "/trash", json=body).status_code == 503
    assert client.get(listing).status_code == 503
    assert client.get(preview).status_code == 503
    assert client.get(export).status_code == 503
    assert client.post(base + "/lifecycle-result", json={**body,
        "archived": True}).status_code == 503

    client, actor = _client(store, monkeypatch, enabled=True)
    updates = base + "/updates"
    assert client.get(updates).status_code == 200
    assert client.get(preview).status_code == 404
    assert client.get(export).status_code == 404
    for bad in ({**body, "unexpected": True},
                {key: value for key, value in body.items() if key != "operation_id"}):
        assert client.post(base + "/trash", json=bad).status_code == 400
    unknown = client.post(base + "/lifecycle-result", json={**body,
        "archived": True})
    assert unknown.status_code == 200 and unknown.json == {"state": "unknown"}
    archived = client.post(base + "/trash", json=body)
    assert archived.status_code == 201, archived.json
    assert archived.json["database_id"] == "database-e1"
    assert archived.json["archived"] is True
    assert client.get(updates).status_code == 404
    shown = client.get(preview)
    assert shown.status_code == 200, shown.json
    assert shown.json["database_id"] == "database-e1"
    assert shown.json["block"]["id"] == "owned-db-source-block"
    assert shown.json["database"]["properties"] == database["properties"]
    assert shown.json["database"]["rows"] == database["rows"]
    assert shown.json["last_edit"]["actor_subject_id"] == "owner"
    assert shown.headers["Cache-Control"].startswith("no-store")
    exported = client.get(export)
    assert exported.status_code == 200
    assert exported.mimetype == "application/zip"
    assert exported.headers["Cache-Control"].startswith("no-store")
    with zipfile.ZipFile(io.BytesIO(exported.data)) as archive_file:
        names = archive_file.namelist()
        assert any(name.endswith(".csv") for name in names)
        assert any(name.endswith("_all.csv") for name in names)
        assert any(archive_file.read(name) for name in names)
        assert all("database-e1" in name for name in names)
        assert all(archive_file.read(name).startswith(b"\xef\xbb\xbf") for name in names)
    html_export = client.get(export, query_string={"bundle": "html"})
    assert html_export.status_code == 200
    with zipfile.ZipFile(io.BytesIO(html_export.data)) as archive_file:
        assert any(name.endswith(".html") for name in archive_file.namelist())
        assert any(name.endswith(".csv") for name in archive_file.namelist())
        html_name = next(name for name in archive_file.namelist() if name.endswith(".html"))
        assert b"ARCHIVED KEEP" in archive_file.read(html_name)
        assert b"ARCHIVED HIDE" in archive_file.read(html_name)
        csv_name = next(name for name in archive_file.namelist() if name.endswith(".csv"))
        assert archive_file.read(csv_name).startswith(b"\xef\xbb\xbf")
    excluded_export = client.get(export, query_string={
        "bundle": "html", "page_content": "exclude_files_images"})
    assert excluded_export.status_code == 200
    with zipfile.ZipFile(io.BytesIO(excluded_export.data)) as archive_file:
        html_name = next(name for name in archive_file.namelist() if name.endswith(".html"))
        assert b"ARCHIVED KEEP" in archive_file.read(html_name)
        assert b"ARCHIVED HIDE" not in archive_file.read(html_name)
    assert client.get(export, query_string={"bundle": "invalid"}).status_code == 400
    assert client.get(export, query_string={"view_id": "missing"}).status_code == 404
    saved_favorite = store.get_navigation_state("outsider", workspace_id)["favorites"]
    assert saved_favorite == [{"source_owner_subject_id": "owner",
        "source_page_id": "page-e1", "database_block_id": "owned-db-source-block",
        "archived_database": {"database_id": "database-e1",
            "title": "Tasks", "type": "table"}, "position": 0}]
    favorite_request = {"source_owner_subject_id": "owner",
        "source_page_id": "page-e1", "database_block_id": "owned-db-source-block",
        "expected_favorite_version": favorite_version + 1}
    with pytest.raises(NotFoundOrDenied):
        store.add_database_favorite("outsider", workspace_id, **favorite_request,
            operation_id="db-trash-http-new-archived-favorite")
    removed_favorite = store.remove_database_favorite("outsider", workspace_id,
        **favorite_request, operation_id="db-trash-http-remove-favorite")
    assert removed_favorite["changed"] is True
    assert store.get_navigation_state("outsider", workspace_id)["favorites"] == []
    assert client.post(base + "/trash", json=body).json["replayed"] is True
    committed = client.post(base + "/lifecycle-result", json={**body,
        "archived": True})
    assert committed.status_code == 200, committed.json
    assert committed.json["state"] == "committed"
    assert committed.json["current"]["archived"] is True
    assert committed.json["result"]["operation_id"] == body["operation_id"]
    visible_list = client.get(listing)
    assert visible_list.status_code == 200, visible_list.json
    assert visible_list.json["count"] == 1
    assert visible_list.json["items"][0]["database_id"] == "database-e1"
    assert visible_list.json["items"][0]["database_version"] == archived.json["database_version"]
    assert client.get(listing, query_string={"q": "no-matching-database"}).json["count"] == 0
    assert client.get(listing, query_string={"unexpected": "1"}).status_code == 400
    actor["id"] = "content-editor"
    assert client.post(base + "/lifecycle-result", json={**body,
        "archived": True}).status_code == 404
    assert client.get(listing).status_code == 404
    assert client.get(preview).status_code == 404
    assert client.get(export).status_code == 404
    actor["id"] = "owner"

    hidden = store.iam.read_document("owner", "page-e1")
    restore = {"document_id": "page-e1",
        "expected_document_sequence": hidden["document_sequence"],
        "expected_workspace_epoch": hidden["workspace_epoch"],
        "expected_document_epoch": hidden["document_epoch"],
        "expected_database_version": archived.json["database_version"],
        "operation_id": "db-trash-http-restore"}
    restored = client.post(base + "/restore", json=restore)
    assert restored.status_code == 201, restored.json
    assert restored.json["archived"] is False
    assert client.get(updates).status_code == 200
    assert client.post(base + "/restore", json=restore).json["replayed"] is True
    result = client.post(base + "/lifecycle-result", json={**restore,
        "archived": False})
    assert result.status_code == 200 and result.json["current"]["archived"] is False
    assert store.iam.read_database("owner", "database-e1")["id"] == "database-e1"
    assert client.get(listing).json["items"] == []
    assert client.get(preview).status_code == 404
    assert client.get(export).status_code == 404
    for response in (unknown, archived, committed, restored, result, visible_list):
        assert response.headers["Cache-Control"].startswith("no-store")


def test_ui_r5_empty_archived_database_download_matches_notion_empty_zip(
        activity_environment, monkeypatch):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _apply(migration, store.db_path, store.user_data_root,
        "before-empty-database-trash-export.db")
    store = CollaborativeWorkspaceStore(store.db_path,
        user_data_root=store.user_data_root,
        identity_directory=store.identity_directory)
    database = store.iam.read_database("owner", "database-e1")
    row = database["rows"][0]
    page = store.iam.read_document("owner", "page-e1")
    store.iam.execute_surface_command("owner", "page-e1",
        database_id="database-e1", command_kind="database.row.delete",
        target_id=row["id"], payload={},
        expected_document_sequence=page["document_sequence"],
        expected_workspace_epoch=page["workspace_epoch"],
        expected_document_epoch=page["document_epoch"],
        expected_target_version=row["version"],
        operation_id="empty-db-export-delete-row")
    assert store.iam.read_database("owner", "database-e1")["rows"] == []
    page = store.iam.read_document("owner", "page-e1")
    database = store.iam.read_database("owner", "database-e1")
    client, _actor = _client(store, monkeypatch, enabled=True)
    base = f"/api/notion/workspaces/{workspace_id}/databases/database-e1"
    archive = client.post(base + "/trash", json={
        "document_id": "page-e1",
        "expected_document_sequence": page["document_sequence"],
        "expected_workspace_epoch": page["workspace_epoch"],
        "expected_document_epoch": page["document_epoch"],
        "expected_database_version": database["version"],
        "operation_id": "empty-db-export-archive",
    })
    assert archive.status_code == 201, archive.json
    exported = client.get(
        f"/api/notion/workspaces/{workspace_id}/pages/page-e1/"
        "archived-databases/owned-db-source-block/export")
    assert exported.status_code == 200
    assert client.get(
        f"/api/notion/workspaces/{workspace_id}/pages/page-e1/"
        "archived-databases/owned-db-source-block/export"
        "?bundle=html&page_content=exclude_files_images").status_code == 200
    assert client.get(
        f"/api/notion/workspaces/{workspace_id}/pages/page-e1/"
        "archived-databases/owned-db-source-block/export"
        "?page_content=invalid").status_code == 400
    with zipfile.ZipFile(io.BytesIO(exported.data)) as archive_file:
        assert archive_file.namelist() == []
