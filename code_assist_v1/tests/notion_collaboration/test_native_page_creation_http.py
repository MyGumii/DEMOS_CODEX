"""UI-NATIVE-CREATE-HTTP: canonical-only routing, recoverable receipts and scope."""
import pytest
from flask import Flask

from code_assist_v1 import notion_store as legacy, notion_workspace_pages as pages, routes_notion_workspace as routes
from code_assist_v1.notion_collaboration import page_creation
from code_assist_v1.tests.notion_collaboration.test_member_navigation import (
    environment, native_environment, archive_environment, guest_environment, image_environment, dump, database,
)
from code_assist_v1.tests.notion_collaboration.test_page_move import current_body


def client_for(store, monkeypatch, actor="owner"):
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "current_user_id", lambda: actor)
    monkeypatch.setattr(routes, "_navigation_ui_enabled", lambda: True)
    monkeypatch.setattr(routes, "_collaborative_runtime_enabled", lambda: True)
    def forbidden(*args, **kwargs):
        raise AssertionError("native creation cannot touch the legacy page path")
    monkeypatch.setattr(pages, "create_workspace_page", forbidden)
    monkeypatch.setattr(legacy, "get_page", forbidden)
    app = Flask(__name__); app.config["TESTING"] = True
    routes.register_notion_workspace_routes(app)
    return app.test_client()


def body(store, ws, *, operation="native-http-create", parent="", title="HTTP 새 페이지"):
    return {"base_version": store.get_workspace("owner", ws)["version"], "operation_id": operation,
        "parent_id": parent, "title": title}


def test_ui_native_create_http_root_child_list_and_retry(environment, monkeypatch):
    store, ws = environment
    client = client_for(store, monkeypatch)
    base = f"/api/notion/workspaces/{ws}"
    assert store.capabilities()["collaborative_page_creation"] is True
    request = body(store, ws)
    root = client.post(base + "/pages", json=request)
    assert root.status_code == 201, root.json
    assert "no-store" in root.headers["Cache-Control"]
    assert root.json["parent_link_saved"] is False
    root_id = root.json["page"]["id"]
    # A fresh page has a canonical checkpoint but no durable update frames yet.
    # The frame-only replay helper becomes applicable after the parent delta.
    before = store.iam.read_document("owner", root_id)
    assert len(before["blocks"]) == 1 and before["blocks"][0]["content"] == ""
    child_request = body(store, ws, operation="native-http-child", parent=root_id)
    child = client.post(base + "/pages", json=child_request)
    assert child.status_code == 201, child.json
    assert child.json["parent_link_saved"] is True
    child_id = child.json["page"]["id"]
    after = current_body(store.iam, root_id)
    links = [block for block in after["blocks"] if block["props"].get("page_id") == child_id]
    assert len(links) == 1
    # Canonical snapshots carry an explicit position; Yjs exposes the same
    # ordering as the array order. Compare every body field in that order.
    expected_blocks = [{key: value for key, value in block.items() if key != "position"} for block in before["blocks"]]
    assert [block for block in after["blocks"] if block["id"] != links[0]["id"]] == expected_blocks
    committed = dump(store)
    replay = client.post(base + "/pages", json=child_request)
    assert replay.status_code == 200 and replay.json["replayed"] is True
    assert {**replay.json, "replayed": False} == child.json
    assert dump(store) == committed
    listing = client.get(base + "/pages")
    assert {item["id"] for item in listing.json["items"]} == {root_id, child_id}
    assert next(item for item in listing.json["items"] if item["id"] == child_id)["parent_id"] == root_id
    favorite = client.put(base + "/navigation/favorites", json={"source_owner_subject_id": "owner",
        "source_page_id": child_id, "expected_favorite_version": 0, "operation_id": "native-http-favorite"})
    assert favorite.status_code == 200 and favorite.json["favorite_version"] == 1
    detail = client.get(base + f"/pages/{child_id}/collaboration")
    assert detail.status_code == 200 and detail.json["page"]["id"] == child_id
    with database(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_page_sources").fetchone()[0] == 0


def test_ui_native_create_http_cas_changed_input_and_actor_denial(environment, monkeypatch):
    store, ws = environment
    client = client_for(store, monkeypatch)
    path = f"/api/notion/workspaces/{ws}/pages"
    request = body(store, ws)
    accepted = client.post(path, json=request)
    assert accepted.status_code == 201
    committed = dump(store)
    changed = client.post(path, json={**request, "title": "다른 요청"})
    assert changed.status_code == 409
    stale = client.post(path, json={**request, "operation_id": "new-stale-create"})
    assert stale.status_code == 409 and stale.json["current_version"] == accepted.json["workspace"]["version"]
    spoofed = client.post(path, json={**request, "actor_subject_id": "owner"})
    assert spoofed.status_code == 400
    assert dump(store) == committed
    guest_client = client_for(store, monkeypatch, "outsider")
    denied = guest_client.post(path, json=body(store, ws, operation="guest-denied-create"))
    assert denied.status_code == 404 and "no-store" in denied.headers["Cache-Control"]
    assert dump(store) == committed


def test_ui_native_create_http_failure_has_no_partial_page(environment, monkeypatch):
    store, ws = environment
    client = client_for(store, monkeypatch)
    path = f"/api/notion/workspaces/{ws}/pages"
    request = body(store, ws)
    original = routes.create_collaborative_page
    def failed(*args, **kwargs):
        return page_creation.create_page(*args, **kwargs, fault="after_outbox")
    monkeypatch.setattr(routes, "create_collaborative_page", failed)
    before = dump(store)
    failed_response = client.post(path, json=request)
    assert failed_response.status_code >= 400
    assert dump(store) == before
    monkeypatch.setattr(routes, "create_collaborative_page", original)
    result = client.post(path, json=request)
    assert result.status_code == 201
    with database(store.db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM workspace_collaboration_native_pages").fetchone()[0] == 1


def test_ui_native_create_http_old_schema_has_no_legacy_fallback(native_environment, monkeypatch):
    store, ws = native_environment
    client = client_for(store, monkeypatch)
    assert store.capabilities()["collaborative_page_creation"] is False
    before = dump(store)
    result = client.post(f"/api/notion/workspaces/{ws}/pages", json=body(store, ws))
    assert result.status_code == 503 and "no-store" in result.headers["Cache-Control"]
    assert dump(store) == before
