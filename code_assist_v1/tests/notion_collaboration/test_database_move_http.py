"""UI-R5: gated database move transport and durable receipt lookup."""

import sqlite3
from contextlib import closing

from flask import Flask

from code_assist_v1 import routes_notion_workspace as routes
from code_assist_v1.tests.notion_collaboration.test_database_activity_migration import activity_environment
from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import (
    environment, appearance_environment, body_environment, team_environment,
    file_environment, member_environment, native_environment,
    archive_environment, guest_environment, image_environment,
    owned_database_block,
)
from code_assist_v1.tests.notion_collaboration.test_database_move_commit import _destination, _request


def _client(store, monkeypatch, *, enabled):
    actor = {"id": "owner"}
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_collaborative_runtime_enabled", lambda: True)
    monkeypatch.setattr(routes, "current_user_id", lambda: actor["id"])
    if enabled:
        original = store.capabilities
        monkeypatch.setattr(store, "capabilities", lambda: {
            **original(), "collaborative_owned_database_move": True})
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-database-move-http")
    routes.register_notion_workspace_routes(app)
    return app.test_client(), actor


def test_ui_r5_database_move_http_gate_commit_and_result(activity_environment, monkeypatch):
    store, workspace_id = activity_environment
    owned_database_block(store)
    _destination(store, workspace_id)
    assert store.capabilities()["collaborative_owned_database_move"] is False
    url = f"/api/notion/workspaces/{workspace_id}/databases/database-e1/move"
    body = {**_request(store), "source_document_id": "page-e1",
        "destination_document_id": "page-db-destination",
        "operation_id": "db-move-http"}
    client, actor = _client(store, monkeypatch, enabled=False)
    for suffix in ("", "-result"):
        response = client.post(url + suffix, json=body)
        assert response.status_code == 503, response.json
    client, actor = _client(store, monkeypatch, enabled=True)
    for invalid in ({**body, "unexpected": True},
                    {key: value for key, value in body.items() if key != "operation_id"}):
        for suffix in ("", "-result"):
            assert client.post(url + suffix, json=invalid).status_code == 400
    unknown = client.post(url + "-result", json=body)
    assert unknown.status_code == 200 and unknown.json == {"state": "unknown"}
    saved = client.post(url, json=body)
    assert saved.status_code == 201, saved.json
    assert saved.json["operation_id"] == body["operation_id"]
    assert saved.json["database_id"] == "database-e1"
    assert saved.json["destination_document_id"] == "page-db-destination"
    replay = client.post(url, json=body)
    assert replay.status_code == 200 and replay.json == {**saved.json, "replayed": True}
    result = client.post(url + "-result", json=body)
    assert result.status_code == 200 and result.json["state"] == "committed"
    assert result.json["result"]["operation_id"] == body["operation_id"]
    assert result.json["current"]["destination_archived"] is False
    assert client.post(url, json={**body,
        "expected_source_sequence": body["expected_source_sequence"] + 1}).status_code == 409
    for response in (unknown, saved, replay, result):
        assert response.headers["Cache-Control"].startswith("no-store")
    actor["id"] = "content-editor"
    assert client.post(url + "-result", json=body).status_code == 404
    with closing(sqlite3.connect(store.db_path)) as con:
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("SELECT document_id FROM workspace_collaboration_databases "
            "WHERE id='database-e1'").fetchone() == ("page-db-destination",)
