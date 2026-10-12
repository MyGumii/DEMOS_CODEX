"""UI-DB-COPY-HTTP: staged owned-database transport and receipt lookup."""
from flask import Flask
import pytest

from code_assist_v1 import routes_notion_workspace as routes
from code_assist_v1.tests.notion_collaboration.test_file_copy_provenance import (
    member_environment, native_environment, archive_environment,
    guest_environment, image_environment, request, dump,
)
from code_assist_v1.tests.notion_collaboration.test_owned_database_copy_commit import (
    environment, appearance_environment, body_environment, team_environment,
    file_environment, owned_database_block,
)


def client_for(store, monkeypatch, *, enabled):
    actor = {"id": "owner"}
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_collaborative_runtime_enabled", lambda: True)
    monkeypatch.setattr(routes, "current_user_id", lambda: actor["id"])
    if enabled:
        original = store.capabilities
        monkeypatch.setattr(store, "capabilities", lambda: {
            **original(), "collaborative_owned_database_copy": True})
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-database-copy-http",
        NOTION_CANONICAL_ORIGIN="https://notes.example.test")
    routes.register_notion_workspace_routes(app)
    return app.test_client(), actor


def test_ui_owned_database_copy_http_gate_and_exact_request_shape(environment, monkeypatch):
    store, workspace_id = environment
    owned_database_block(store)
    assert store.capabilities()["collaborative_owned_database_copy"] is False
    client, actor = client_for(store, monkeypatch, enabled=False)
    url = f"/api/notion/workspaces/{workspace_id}/pages/page-e1/databases/database-e1/copy"
    body = {**request(store), "operation_id": "owned-db-http-gated", "include_content": True}
    before = dump(store)
    for suffix in ("", "-result"):
        assert client.post(url + suffix, json=body).status_code == 503
    assert dump(store) == before
    client, actor = client_for(store, monkeypatch, enabled=True)
    for invalid in ({**body, "include_content": 1},
                    {**body, "base_version": 1},
                    {key: value for key, value in body.items() if key != "include_content"}):
        for suffix in ("", "-result"):
            assert client.post(url + suffix, json=invalid).status_code == 400
    actor["id"] = "content-editor"
    assert client.post(url, json=body).status_code == 404
    actor["id"] = "owner"
    assert client.post(url.replace(workspace_id, "wrong-workspace"), json=body).status_code == 404
    assert dump(store) == before


@pytest.mark.parametrize("include_content", [True, False])
def test_ui_owned_database_copy_http_commit_replay_and_result(environment, monkeypatch, include_content):
    store, workspace_id = environment
    owned_database_block(store)
    client, actor = client_for(store, monkeypatch, enabled=True)
    url = f"/api/notion/workspaces/{workspace_id}/pages/page-e1/databases/database-e1/copy"
    body = {**request(store), "operation_id": "owned-db-http-" + str(include_content),
        "include_content": include_content}
    unknown = client.post(url + "-result", json=body)
    assert unknown.status_code == 200 and unknown.json == {"state": "unknown"}
    saved = client.post(url, json=body)
    assert saved.status_code == 201, saved.json
    assert saved.json["include_content"] is include_content
    assert bool(store.iam.read_database("owner", saved.json["database_id"])["rows"]) is include_content
    committed = dump(store)
    replay = client.post(url, json=body)
    assert replay.status_code == 200 and replay.json["database_id"] == saved.json["database_id"]
    assert replay.json["replayed"] is True and dump(store) == committed
    result = client.post(url + "-result", json=body)
    assert result.status_code == 200 and result.json["state"] == "committed"
    assert result.json["result"]["database_id"] == saved.json["database_id"]
    changed = client.post(url, json={**body, "include_content": not include_content})
    assert changed.status_code == 409 and changed.json["code"] == "collaboration_operation_id_conflict"
    for response in (unknown, saved, replay, result):
        assert response.headers["Cache-Control"].startswith("no-store")
    actor["id"] = "content-editor"
    assert client.post(url + "-result", json=body).status_code == 404
