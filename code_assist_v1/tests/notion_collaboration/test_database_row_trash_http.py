"""UI-R5: gated database-row Trash transport on a synthetic 2423 catalog."""

import sqlite3
from pathlib import Path

from flask import Flask

from code_assist_v1 import routes_notion_workspace as routes
from code_assist_v1 import notion_workspace_database_trash_migration as database_trash_migration
from code_assist_v1 import notion_workspace_database_row_trash_migration as row_trash_migration
from code_assist_v1.notion_collaboration.iam_runtime import CollaborativeWorkspaceStore
from code_assist_v1.tests.notion_collaboration.test_database_features import _surface
from code_assist_v1.tests.notion_collaboration.test_database_trash_http import _client
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import activity_environment
from code_assist_v1.tests.notion_collaboration.test_database_row_content import _apply
from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import (
    environment, appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, owned_database_block,
)


def test_ui_r5_database_row_trash_http_gate_lifecycle_and_receipt(
        activity_environment, monkeypatch):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _apply(database_trash_migration, store.db_path, store.user_data_root,
        "before-row-trash-http-source.db")
    _apply(row_trash_migration, store.db_path, store.user_data_root,
        "before-row-trash-http.db")

    def staged_connect(*, query_only):
        con = sqlite3.connect(
            f"{Path(store.db_path).as_uri()}?mode=ro" if query_only else store.db_path,
            uri=query_only, timeout=5.0, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        if query_only:
            con.execute("PRAGMA query_only=ON")
        return con

    monkeypatch.setattr(store.iam, "_connect", staged_connect)
    assert store.capabilities()["collaborative_database_row_trash"] is False
    actor = {"id": "owner"}
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_collaborative_runtime_enabled", lambda: True)
    monkeypatch.setattr(routes, "current_user_id", lambda: actor["id"])
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-row-trash-http")
    routes.register_notion_workspace_routes(app)
    client = app.test_client()
    base = (f"/api/notion/workspaces/{workspace_id}/databases/database-e1"
        "/rows/row-e1")
    listing = (f"/api/notion/workspaces/{workspace_id}/databases/database-e1"
        "/row-trash")
    with store.iam._reader() as con:
        page = store.iam._document(con, "page-e1")
        workspace = store.iam._workspace(con, workspace_id)
        database = con.execute("SELECT row_version FROM "
            "workspace_collaboration_databases WHERE id='database-e1'").fetchone()
        row = con.execute("SELECT row_version FROM "
            "workspace_collaboration_database_rows WHERE id='row-e1'").fetchone()
        fences = {"expected_document_sequence": int(page["document_sequence"]),
            "expected_workspace_epoch": int(workspace["security_epoch"]),
            "expected_document_epoch": int(page["access_epoch"]),
            "expected_database_version": int(database[0]),
            "expected_row_version": int(row[0])}
    body = {"document_id": "page-e1", **fences,
        "operation_id": "row-trash-http-archive"}
    assert client.post(base + "/trash", json=body).status_code == 503
    assert client.post(base + "/lifecycle-result", json={**body,
        "archived": True}).status_code == 503
    assert client.get(listing, query_string={"document_id": "page-e1"}).status_code == 503

    original = store.capabilities
    monkeypatch.setattr(store, "capabilities", lambda: {
        **original(), "collaborative_database_row_trash": True})
    assert client.post(base + "/trash", json={**body,
        "unexpected": True}).status_code == 400
    assert client.get(listing).status_code == 400
    assert client.get(listing, query_string={"document_id": "page-e1",
        "limit": "201"}).status_code == 400
    assert client.post(base + "/lifecycle-result", json={**body,
        "archived": True}).json == {"state": "unknown"}

    archived = client.post(base + "/trash", json=body)
    assert archived.status_code == 201, archived.json
    assert archived.json["archived"] is True
    assert archived.json["row_id"] == "row-e1"
    assert client.post(base + "/trash", json=body).json["replayed"] is True
    receipt = client.post(base + "/lifecycle-result", json={**body,
        "archived": True})
    assert receipt.status_code == 200, receipt.json
    assert receipt.json["state"] == "committed"
    assert receipt.json["result"]["operation_id"] == body["operation_id"]
    trashed = client.get(listing, query_string={"document_id": "page-e1"})
    assert trashed.status_code == 200, trashed.json
    assert trashed.json["count"] == 1
    assert trashed.json["items"][0]["row_id"] == "row-e1"
    assert client.get(listing, query_string={"document_id": "page-e1",
        "row_id": "row-e1"}).json["count"] == 1
    assert client.get(listing, query_string={"document_id": "page-e1",
        "row_id": "unrelated-row"}).json["count"] == 0
    actor["id"] = "outsider"
    assert client.post(base + "/lifecycle-result", json={**body,
        "archived": True}).status_code == 404
    assert client.get(listing, query_string={"document_id": "page-e1"}).status_code == 404
    actor["id"] = "owner"

    restored_body = {"document_id": "page-e1",
        "expected_document_sequence": archived.json["document_sequence"],
        "expected_workspace_epoch": archived.json["workspace_epoch"],
        "expected_document_epoch": archived.json["document_epoch"],
        "expected_database_version": archived.json["database_version"],
        "expected_row_version": archived.json["row_version"],
        "operation_id": "row-trash-http-restore"}
    restored = client.post(base + "/restore", json=restored_body)
    assert restored.status_code == 201, restored.json
    assert restored.json["archived"] is False
    assert client.post(base + "/restore", json=restored_body).json["replayed"] is True
    assert client.get(listing, query_string={"document_id": "page-e1"}).json["count"] == 0
    for response in (archived, receipt, trashed, restored):
        assert response.headers["Cache-Control"].startswith("no-store")


def test_ui_r5_selected_rows_trash_http_one_receipt_for_two_rows(
        activity_environment, monkeypatch):
    source, workspace_id = activity_environment
    owned_database_block(source)
    _surface(source.iam, "database.row.create", "row-e2", {},
        "row-batch-http-create")
    _apply(database_trash_migration, source.db_path, source.user_data_root,
        "before-row-batch-http-source.db")
    _apply(row_trash_migration, source.db_path, source.user_data_root,
        "before-row-batch-http.db")
    store = CollaborativeWorkspaceStore(source.db_path,
        user_data_root=source.user_data_root,
        identity_directory=source.identity_directory)
    client, actor = _client(store, monkeypatch, enabled=False)
    page = store.iam.read_document("owner", "page-e1")
    database = store.iam.read_database("owner", "database-e1")
    rows = [{"row_id": row["id"], "expected_row_version": row["version"]}
        for row in database["rows"]]
    body = {"document_id": "page-e1", "rows": rows,
        "operation_id": "row-batch-http-archive",
        "expected_document_sequence": page["document_sequence"],
        "expected_workspace_epoch": page["workspace_epoch"],
        "expected_document_epoch": page["document_epoch"],
        "expected_database_version": database["version"]}
    base = (f"/api/notion/workspaces/{workspace_id}/databases/database-e1"
        "/rows/trash-batch")
    assert client.post(base, json=body).status_code == 503
    original = store.capabilities
    monkeypatch.setattr(store, "capabilities", lambda: {
        **original(), "collaborative_database_row_trash": True})
    assert client.post(base, json={**body, "unexpected": True}).status_code == 400
    assert client.post(base + "/lifecycle-result", json={**body,
        "archived": True}).json == {
        "state": "unknown"}
    archived = client.post(base, json=body)
    assert archived.status_code == 201, archived.json
    assert archived.json["archived"] is True
    assert [item["row_id"] for item in archived.json["rows"]] == [
        "row-e1", "row-e2"]
    assert archived.json["document_sequence"] == page["document_sequence"] + 1
    assert client.post(base, json=body).json["replayed"] is True
    assert client.post(base + "/lifecycle-result", json=body).status_code == 400
    receipt = client.post(base + "/lifecycle-result", json={**body,
        "archived": True})
    assert receipt.status_code == 200, receipt.json
    assert receipt.json["result"]["operation_id"] == body["operation_id"]
    assert store.iam.read_database("owner", "database-e1")["rows"] == []
    restore_base = base.replace("/trash-batch", "/restore-batch")
    restored_body = {**body,
        "rows": [{"row_id": item["row_id"],
                  "expected_row_version": item["row_version"]}
                 for item in archived.json["rows"]],
        "operation_id": "row-batch-http-restore",
        "expected_document_sequence": archived.json["document_sequence"]}
    assert client.post(restore_base + "/lifecycle-result", json={**restored_body,
        "archived": False}).json == {"state": "unknown"}
    restored = client.post(restore_base, json=restored_body)
    assert restored.status_code == 201, restored.json
    assert restored.json["archived"] is False
    assert [item["id"] for item in store.iam.read_database(
        "owner", "database-e1")["rows"]] == ["row-e1", "row-e2"]
    assert client.post(restore_base, json=restored_body).json["replayed"] is True
    restore_receipt = client.post(restore_base + "/lifecycle-result",
        json={**restored_body, "archived": False})
    assert restore_receipt.status_code == 200, restore_receipt.json
    assert restore_receipt.json["result"]["operation_id"] == restored_body["operation_id"]
    assert client.post(restore_base + "/lifecycle-result",
        json={**restored_body, "archived": True}).status_code == 400
    actor["id"] = "outsider"
    assert client.post(base + "/lifecycle-result", json={**body,
        "archived": True}).status_code == 404
    assert client.post(restore_base + "/lifecycle-result", json={**restored_body,
        "archived": False}).status_code == 404
    for response in (archived, receipt, restored, restore_receipt):
        assert response.headers["Cache-Control"].startswith("no-store")
