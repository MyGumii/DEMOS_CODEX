"""UI-TEAM-CREATE-HTTP: destination propagation and exact version gating."""
from code_assist_v1.tests.notion_collaboration.test_native_team_creation import (
    environment, file_environment, member_environment, native_environment, archive_environment,
    guest_environment, image_environment, teams, dump,
)
from code_assist_v1.tests.notion_collaboration.test_native_page_creation_http import client_for, body


def test_ui_team_create_http_member_destination_list_and_idempotent_retry(environment, monkeypatch):
    store, ws = environment; a, b = teams(store, ws)
    client = client_for(store, monkeypatch, "content-editor")
    path = f"/api/notion/workspaces/{ws}/pages"
    assert store.capabilities()["collaborative_team_page_creation"] is True
    request = {**body(store, ws), "teamspace_id":a}
    result = client.post(path, json=request)
    assert result.status_code == 201, result.json
    root = result.json["page"]["id"]
    retry = client.post(path, json=request)
    assert retry.status_code == 200 and retry.json["replayed"] is True
    assert retry.json["page"]["id"] == root
    listing = client.get(path)
    assert listing.status_code == 200, listing.json
    assert next(item for item in listing.json["items"] if item["id"] == root)["placement"]["teamspace_id"] == a
    child = client.post(path, json=body(store, ws, parent=root, operation="http-team-child"))
    assert child.status_code == 201, child.json
    before = dump(store)
    assert client.post(path, json={**request,"teamspace_id":b}).status_code == 409
    assert client.post(path, json={**body(store, ws),"teamspace_id":a,"parent_id":root}).status_code == 400
    assert client.post(path, json={**body(store, ws,operation="bad-team"),"teamspace_id":b}).status_code == 404
    for invalid in ("", None, 7):
        assert client.post(path, json={**body(store, ws),"teamspace_id":invalid}).status_code == 400
    assert dump(store) == before
    outsider = client_for(store, monkeypatch, "outsider")
    assert outsider.post(path, json={**body(store, ws),"teamspace_id":a}).status_code == 404
    assert dump(store) == before


def test_ui_team_create_http_old_exact_schema_never_creates_private_page(file_environment, monkeypatch):
    store, ws = file_environment; client = client_for(store, monkeypatch)
    before = dump(store)
    assert store.capabilities()["collaborative_team_page_creation"] is False
    response = client.post(f"/api/notion/workspaces/{ws}/pages",json={**body(store, ws),"teamspace_id":"ts-unavailable"})
    assert response.status_code == 503, response.json
    assert "no-store" in response.headers["Cache-Control"]
    assert dump(store) == before
